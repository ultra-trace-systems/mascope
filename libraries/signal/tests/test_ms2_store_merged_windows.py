"""A window's spectra merged over the activations, read from an MS2 store.

A precursor stepped by hand is fragmented in consecutive blocks, one per
collision energy, so a window can hold every scan of one step and none of the
next. With the activations merged (``by_activation=False``, the route's
default), the raw read averages only the window's scans of the precursor: one
step's alone is that step's spectrum, not the precursor's over every step.

The hermetic MS2 fixture's stepped shape interleaves its steps, so no window
of it holds one alone; the store here is written from contents made by hand.
Each step's spectrum, and the precursor's merged one, is a single centroid at
an m/z of its own, so an answer says which stored spectrum it is. The
stepped precursor is fragmented in the negative polarity too, at one
activation: a group of another polarity is not one of its steps.
"""

import numpy as np
import pytest
import zarr

from mascope_signal.ms2_store import Ms2Store, _write
from mascope_thermo.backend import MS2_SCAN_RECORD_SOURCES


STEPPED_MZ, OTHER_MZ = 137.096, 200.5
#: The stepped precursor's steps in turn, two scans each, after the survey
#: scan; then another precursor at one activation.
SCANS = {
    (STEPPED_MZ, "hcd10.00"): [2, 3],
    (STEPPED_MZ, "hcd20.00"): [4, 5],
    (STEPPED_MZ, "hcd40.00"): [6, 7],
    (OTHER_MZ, "hcd25.00"): [8, 9],
}
#: The centroid each group's averaged spectrum holds.
OWN_MZ = {
    (STEPPED_MZ, "hcd10.00"): 50.0,
    (STEPPED_MZ, "hcd20.00"): 60.0,
    (STEPPED_MZ, "hcd40.00"): 70.0,
    (OTHER_MZ, "hcd25.00"): 100.0,
}
#: The centroid the stepped precursor's spectrum over every step holds.
MERGED_MZ = 90.0
#: The negative polarity's survey scan, and its group of the stepped
#: precursor, with the centroid that group's spectrum holds.
NEGATIVE_SURVEY = 10
NEGATIVE_SCANS = {(STEPPED_MZ, "hcd20.00"): [11, 12]}
NEGATIVE_MZ = 110.0


def _spectrum(mz: float) -> tuple[np.ndarray, ...]:
    return (
        np.array([mz]),
        np.array([1000.0]),
        np.array([60000.0]),
        np.array([50.0]),
    )


def _record(
    scan_number: int, parent_peak_mz: float, activation: str, polarity: str = "+"
) -> dict:
    return {
        "scan_number": scan_number,
        "time_s": float(scan_number),
        "polarity": polarity,
        "precursor_mz": parent_peak_mz,
        "activation": activation,
        "activation_kind": "hcd",
        "nce": float(activation[3:]),
        "hcd_energy_v": None,
        "isolation_width": 1.0,
        "low_mass": 40.0,
        "high_mass": 210.0,
        "injection_time_ms": 10.0,
        "tic": 100.0 * scan_number,
        "sources": dict(MS2_SCAN_RECORD_SOURCES),
    }


def _contents() -> dict:
    fragments = sorted(n for scans in SCANS.values() for n in scans)
    (negative,) = NEGATIVE_SCANS.values()
    return {
        "polarities": {
            "+": {
                "scan_numbers": [1, *fragments],
                "times": [float(n) for n in (1, *fragments)],
                "ms2_scan_numbers": fragments,
                "records": [
                    _record(n, *key) for key, scans in SCANS.items() for n in scans
                ],
                "groups": dict(SCANS),
                "averaged": {key: _spectrum(mz) for key, mz in OWN_MZ.items()},
                # Only the precursor taken at several activations has one
                "merged": {(STEPPED_MZ, ""): _spectrum(MERGED_MZ)},
            },
            "-": {
                "scan_numbers": [NEGATIVE_SURVEY, *negative],
                "times": [float(n) for n in (NEGATIVE_SURVEY, *negative)],
                "ms2_scan_numbers": negative,
                "records": [
                    _record(n, *key, polarity="-")
                    for key, scans in NEGATIVE_SCANS.items()
                    for n in scans
                ],
                "groups": dict(NEGATIVE_SCANS),
                "averaged": {key: _spectrum(NEGATIVE_MZ) for key in NEGATIVE_SCANS},
                "merged": {},
            },
        },
        "centroids": {
            n: {
                "masses": np.array([float(n)]),
                "intensities": np.array([1.0]),
                "resolutions": np.array([1.0]),
                "signal_to_noise": np.array([1.0]),
                "timestamp": float(n),
            }
            for n in (*fragments, *negative)
        },
    }


@pytest.fixture(scope="module")
def store(tmp_path_factory) -> Ms2Store:
    path = str(tmp_path_factory.mktemp("store") / "ms2.zarr")
    _write(path, _contents(), parent_peak_tolerance=0.001, ppm=1)
    root = zarr.open_group(path, mode="r")
    return Ms2Store(base_filename="ORBI-1_steps", root=root, attrs=root.attrs.asdict())


def _merged(store, first_scan, last_scan, polarity="+") -> dict | None:
    """The window's spectra merged over the activations, each as its one m/z.

    A scan's time is its number; no scan number is the whole file."""
    t_min, t_max = (None if n is None else float(n) for n in (first_scan, last_scan))
    spectra = store.averaged_centroids(t_min, t_max, polarity, by_activation=False)
    if spectra is None:
        return None
    return {key: mz.tolist() for key, (mz, *_) in spectra.items()}


class TestOneStepAlone:
    @pytest.mark.parametrize(
        ("first_scan", "last_scan", "activation"),
        [(2, 3, "hcd10.00"), (4, 5, "hcd20.00"), (6, 7, "hcd40.00")],
    )
    def test_is_that_steps_own_spectrum(self, store, first_scan, last_scan, activation):
        """Its scans are the step's every scan, so the average over them is
        the step's own, keyed as the precursor."""
        assert _merged(store, first_scan, last_scan) == {
            (STEPPED_MZ, ""): [OWN_MZ[(STEPPED_MZ, activation)]]
        }

    def test_beside_another_precursor_whole(self, store):
        assert _merged(store, 6, 9) == {
            (STEPPED_MZ, ""): [OWN_MZ[(STEPPED_MZ, "hcd40.00")]],
            (OTHER_MZ, ""): [OWN_MZ[(OTHER_MZ, "hcd25.00")]],
        }

    def test_split_by_activation_as_well(self, store):
        spectra = store.averaged_centroids(2.0, 3.0, "+", by_activation=True)

        assert {key: mz.tolist() for key, (mz, *_) in spectra.items()} == {
            (STEPPED_MZ, "hcd10.00"): [OWN_MZ[(STEPPED_MZ, "hcd10.00")]]
        }


def test_every_step_is_the_precursors_merged_spectrum(store):
    """And a precursor taken at one activation is its one group's."""
    assert _merged(store, 1, 9) == {
        (STEPPED_MZ, ""): [MERGED_MZ],
        (OTHER_MZ, ""): [OWN_MZ[(OTHER_MZ, "hcd25.00")]],
    }


def test_each_polarity_merges_its_own_groups(store):
    """The negative polarity's one group of the precursor is every scan of it
    there, and is not a step of the positive's."""
    assert _merged(store, None, None, "-") == {(STEPPED_MZ, ""): [NEGATIVE_MZ]}
    assert _merged(store, None, None, "+")[(STEPPED_MZ, "")] == [MERGED_MZ]


@pytest.mark.parametrize(
    ("first_scan", "last_scan"),
    [
        # Two whole steps of three: no stored average is over their scans
        (2, 5),
        (4, 9),
        # A step cut, alone and beside a whole one
        (3, 3),
        (3, 5),
        # Every step whole, the other precursor cut
        (1, 8),
    ],
)
def test_any_other_window_is_left_to_the_raw_read(store, first_scan, last_scan):
    assert _merged(store, first_scan, last_scan) is None
