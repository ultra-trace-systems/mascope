"""An MS2 store keeps a file's polarities apart.

A polarity-switching acquisition can fragment one precursor in both
polarities under one key. The hermetic MS2 fixture is positive only, on
purpose, so the store here is written from contents made by hand: one group
of each polarity under the same key, their scans interleaved, each with a
spectrum of its own. A sample reads its own polarity's.
"""

import numpy as np
import pytest
import zarr

from mascope_signal.ms2_store import Ms2Store, _write
from mascope_thermo.backend import MS2_SCAN_RECORD_SOURCES


PARENT_PEAK_MZ, ACTIVATION = 137.096, "hcd20.00"
KEY = (PARENT_PEAK_MZ, ACTIVATION)
#: Each polarity's survey scan and its two fragment scans, interleaved.
SCANS = {"+": [1, 3, 5], "-": [2, 4, 6]}
#: The one centroid each polarity's spectra hold.
FRAGMENT_MZ = {"+": 50.0, "-": 60.0}


def _record(scan_number: int, polarity: str) -> dict:
    record = {
        "scan_number": scan_number,
        "time_s": float(scan_number),
        "polarity": polarity,
        "precursor_mz": PARENT_PEAK_MZ,
        "activation": ACTIVATION,
        "activation_kind": "hcd",
        "nce": 20.0,
        "hcd_energy_v": [5.5 if polarity == "+" else 6.5],
        "isolation_width": 1.0 if polarity == "+" else 2.0,
        "low_mass": 40.0,
        "high_mass": 160.0,
        "injection_time_ms": 10.0,
        "tic": 100.0 * scan_number,
    }
    record["sources"] = dict(MS2_SCAN_RECORD_SOURCES)
    return record


def _spectrum(mz: float) -> tuple[np.ndarray, ...]:
    return (
        np.array([mz]),
        np.array([1000.0]),
        np.array([60000.0]),
        np.array([50.0]),
    )


def _contents() -> dict:
    polarities, centroids = {}, {}
    for polarity, (survey, *fragments) in SCANS.items():
        polarities[polarity] = {
            "scan_numbers": [survey, *fragments],
            "times": [float(n) for n in (survey, *fragments)],
            "ms2_scan_numbers": fragments,
            "records": [_record(n, polarity) for n in fragments],
            "groups": {KEY: fragments},
            "averaged": {KEY: _spectrum(FRAGMENT_MZ[polarity])},
            "merged": {},
        }
        for n in fragments:
            masses, intensities, resolutions, signal_to_noise = _spectrum(
                FRAGMENT_MZ[polarity] + n / 1000
            )
            centroids[n] = {
                "masses": masses,
                "intensities": intensities,
                "resolutions": resolutions,
                "signal_to_noise": signal_to_noise,
                "timestamp": float(n),
            }
    return {"polarities": polarities, "centroids": centroids}


@pytest.fixture(scope="module")
def store(tmp_path_factory) -> Ms2Store:
    path = str(tmp_path_factory.mktemp("store") / "ms2.zarr")
    _write(path, _contents(), parent_peak_tolerance=0.001, ppm=1)
    root = zarr.open_group(path, mode="r")
    return Ms2Store(
        base_filename="ORBI-1_switching", root=root, attrs=root.attrs.asdict()
    )


@pytest.mark.parametrize("polarity", ["+", "-"])
class TestASampleReadsItsOwnPolarity:
    def test_its_summary(self, store, polarity):
        summary = store.summary(None, None, polarity)

        assert (summary["ms1_scan_count"], summary["ms2_scan_count"]) == (1, 2)
        (group,) = summary["groups"]
        assert group["isolation_width"] == (1.0 if polarity == "+" else 2.0)
        assert group["t_min"] == float(SCANS[polarity][1])

    def test_its_spectra(self, store, polarity):
        for by_activation in (True, False):
            spectra = store.averaged_centroids(
                None, None, polarity, by_activation=by_activation
            )

            ((mz, *_),) = spectra.values()
            assert mz.tolist() == [FRAGMENT_MZ[polarity]]

    def test_its_fragment_scans(self, store, polarity):
        centroids, tics = store.fragment_scans(PARENT_PEAK_MZ, None, None, polarity)

        assert [c["timestamp"] for c in centroids] == [
            float(n) for n in SCANS[polarity][1:]
        ]
        assert tics == [100.0 * n for n in SCANS[polarity][1:]]

    def test_its_groups_scans(self, store, polarity):
        group, records, centroids = store.group_scans(
            "137.0960@hcd20.00", None, None, polarity
        )

        assert group["polarity"] == polarity
        assert records == [_record(n, polarity) for n in SCANS[polarity][1:]]
        assert [c["masses"].tolist() for c in centroids] == [
            [FRAGMENT_MZ[polarity] + n / 1000] for n in SCANS[polarity][1:]
        ]


def test_a_sample_with_no_polarity_is_not_answered(store):
    """The raw read pools both polarities' scans; the store's groups are per
    polarity, so the caller reads the raw file."""
    assert store.summary(None, None, None) is None
    assert store.averaged_centroids(None, None, None) is None
    assert store.fragment_scans(PARENT_PEAK_MZ, None, None, None) is None
    assert store.group_scans("137.0960@hcd20.00", None, None, None) is None


def test_a_windows_groups_are_ordered_by_their_first_scan_in_it(tmp_path):
    """As the raw read orders the groups of the scans it selects: by parent
    peak, then by the first of them. Two steps of one precursor taken in
    turn, the second first in a window that starts after the first's first
    scan."""
    steps = {"hcd10.00": [2, 5], "hcd20.00": [3, 4]}
    contents = {
        "polarities": {
            "+": {
                "scan_numbers": [1, 2, 3, 4, 5],
                "times": [1.0, 2.0, 3.0, 4.0, 5.0],
                "ms2_scan_numbers": [2, 3, 4, 5],
                "records": [
                    {
                        **_record(n, "+"),
                        "activation": step,
                    }
                    for n in (2, 3, 4, 5)
                    for step, scans in steps.items()
                    if n in scans
                ],
                "groups": {
                    (PARENT_PEAK_MZ, step): scans for step, scans in steps.items()
                },
                "averaged": {(PARENT_PEAK_MZ, step): _spectrum(50.0) for step in steps},
                "merged": {(PARENT_PEAK_MZ, ""): _spectrum(50.0)},
            }
        },
        "centroids": {
            n: {
                "masses": np.array([50.0]),
                "intensities": np.array([1.0]),
                "resolutions": np.array([1.0]),
                "signal_to_noise": np.array([1.0]),
                "timestamp": float(n),
            }
            for n in (2, 3, 4, 5)
        },
    }
    path = str(tmp_path / "ms2.zarr")
    _write(path, contents, parent_peak_tolerance=0.001, ppm=1)
    root = zarr.open_group(path, mode="r")
    store = Ms2Store(base_filename="ORBI-1_steps", root=root, attrs=root.attrs.asdict())

    whole = store.summary(None, None, "+")
    window = store.summary(3.0, 5.0, "+")

    assert [g["activation"] for g in whole["groups"]] == ["hcd10.00", "hcd20.00"]
    assert [g["activation"] for g in window["groups"]] == ["hcd20.00", "hcd10.00"]
