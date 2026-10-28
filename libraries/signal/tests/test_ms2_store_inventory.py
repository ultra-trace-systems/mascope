"""A file's groups as the group inventory lists them, read from its MS2 store.

The inventory holds one row per group of a file, each over all of its scans
(``Ms2Store.inventory``), and a summary whose groups are the inventory's takes
the rest from the store's scan index (``Ms2Store.scan_counts``). So a group's
record must be the one the store's own summary gives a selection that holds
every scan of it, and the counts the ones that summary gives.

The store here is written from contents made by hand, so each group's scans
can differ in what the summary and the inventory read of them: a precursor
stepped at two energies whose scans' trailers differ - one isolation width
and two, a scan with no volts and one stepped within it - and whose scan
ranges differ from scan to scan; a chained activation, which has a kind and
no one energy, one of whose scans records no scan range; a precursor whose
filter names no activation, so neither kind nor energy; a group of the negative polarity; and a scan whose filter
names no precursor, in no group, which is counted beside the groups.
"""

import numpy as np
import pytest
import zarr

from mascope_signal.ms2_store import Ms2Store, _write
from mascope_thermo.backend import MS2_SCAN_RECORD_SOURCES


A_MZ, CHAINED_MZ, BARE_MZ = 137.096, 445.12, 250.5
#: Scan number -> (parent peak, activation, polarity); scan 1 and 10 are the
#: survey scans, scan 9 an MS2 scan whose filter names no precursor.
GROUPED = {
    2: (A_MZ, "hcd10.00", "+"),
    3: (A_MZ, "hcd20.00", "+"),
    4: (A_MZ, "hcd10.00", "+"),
    5: (A_MZ, "hcd20.00", "+"),
    6: (CHAINED_MZ, "cid30.00@hcd20.00", "+"),
    7: (CHAINED_MZ, "cid30.00@hcd20.00", "+"),
    # A filter that names no activation
    8: (BARE_MZ, "", "+"),
    11: (A_MZ, "hcd10.00", "-"),
    12: (A_MZ, "hcd10.00", "-"),
}
UNGROUPED = 9
#: What each scan's trailer and filter record that differs between scans.
WIDTH = {
    **{2: 1.0, 3: 1.0, 4: 2.0, 5: 1.0, 6: 1.5, 7: 1.5, 8: 1.0, 9: 3.0},
    **{11: 1.0, 12: 1.0},
}
VOLTS = {2: [3.9], 3: [7.8], 4: None, 5: [7.8, 15.6], 6: None, 7: None}
#: Scan 6's filter records no scan range.
SCAN_RANGE = {
    2: (40.0, 150.0),
    3: (50.0, 140.0),
    4: (35.0, 150.0),
    5: (50.0, 160.0),
    6: (None, None),
    7: (60.0, 455.0),
}


def _record(scan_number: int) -> dict:
    parent_peak_mz, activation, polarity = GROUPED.get(scan_number, (None, None, "+"))
    stage = activation if activation and "@" not in activation else None
    low, high = SCAN_RANGE.get(scan_number, (40.0, 460.0))
    return {
        "scan_number": scan_number,
        "time_s": float(scan_number),
        "polarity": polarity,
        "precursor_mz": parent_peak_mz,
        "activation": activation,
        "activation_kind": (
            None if not activation else "cid@hcd" if stage is None else "hcd"
        ),
        "nce": None if stage is None else float(stage[3:]),
        "hcd_energy_v": VOLTS.get(scan_number),
        "isolation_width": WIDTH[scan_number],
        "low_mass": low,
        "high_mass": high,
        "injection_time_ms": 10.0,
        "tic": 100.0 * scan_number,
        "sources": dict(MS2_SCAN_RECORD_SOURCES),
    }


def _spectrum() -> tuple[np.ndarray, ...]:
    return tuple(np.array([value]) for value in (50.0, 1000.0, 60000.0, 50.0))


def _polarity(polarity: str, survey: int) -> dict:
    groups: dict[tuple[float, str], list[int]] = {}
    for number, (mz, activation, held) in GROUPED.items():
        if held == polarity:
            groups.setdefault((mz, activation), []).append(number)
    fragments = sorted(
        [n for scans in groups.values() for n in scans]
        + ([UNGROUPED] if polarity == "+" else [])
    )
    parents = {mz for mz, _ in groups}
    return {
        "scan_numbers": [survey, *fragments],
        "times": [float(n) for n in (survey, *fragments)],
        "ms2_scan_numbers": fragments,
        "records": [_record(n) for n in fragments],
        "groups": groups,
        "averaged": {key: _spectrum() for key in groups},
        "merged": {
            (mz, ""): _spectrum()
            for mz in parents
            if sum(1 for p, _ in groups if p == mz) > 1
        },
    }


@pytest.fixture(scope="module")
def store(tmp_path_factory) -> Ms2Store:
    contents = {
        "polarities": {"+": _polarity("+", 1), "-": _polarity("-", 10)},
        "centroids": {
            n: {
                "masses": np.array([float(n)]),
                "intensities": np.array([1.0]),
                "resolutions": np.array([1.0]),
                "signal_to_noise": np.array([1.0]),
                "timestamp": float(n),
            }
            for n in (*GROUPED, UNGROUPED)
        },
    }
    path = str(tmp_path_factory.mktemp("store") / "ms2.zarr")
    _write(path, contents, parent_peak_tolerance=0.001, ppm=1)
    root = zarr.open_group(path, mode="r")
    return Ms2Store(base_filename="ORBI-1_groups", root=root, attrs=root.attrs.asdict())


def _listed(store) -> dict[tuple[str, str], dict]:
    return {(g["polarity"], g["key"]): g for g in store.inventory()}


def test_lists_every_group_of_the_file_in_both_polarities(store):
    assert list(_listed(store)) == [
        ("+", "137.0960@hcd10.00"),
        ("+", "137.0960@hcd20.00"),
        ("+", "445.1200@cid30.00@hcd20.00"),
        ("+", "250.5000"),
        ("-", "137.0960@hcd10.00"),
    ]


@pytest.mark.parametrize("polarity", ["+", "-"])
def test_each_group_is_its_record_in_the_summary_of_the_whole_file(store, polarity):
    """Bit for bit: the summary whose groups are the inventory's is the
    store's own."""
    summary = store.summary(None, None, polarity)
    listed = sorted(
        (g for g in store.inventory() if g["polarity"] == polarity),
        # The summary's order, which the inventory's readers sort in
        key=lambda g: (g["parent_peak_mz"], g["t_min"]),
    )

    assert [{k: g[k] for k in summary["groups"][0]} for g in listed] == summary[
        "groups"
    ]


def test_a_group_holds_what_its_scans_differ_in(store):
    """Two widths, the volts of the scans that store them, the outer bounds
    of the scans' ranges, the span of their times."""
    a20 = _listed(store)[("+", "137.0960@hcd20.00")]

    assert a20["isolation_widths"] == [{"isolation_width": 1.0, "scan_count": 2}]
    assert a20["hcd_energy"] == [7.8, 15.6]
    assert (a20["low_mass"], a20["high_mass"]) == (50.0, 160.0)
    assert (a20["t_min"], a20["t_max"], a20["scan_count"]) == (3.0, 5.0, 2)

    a10 = _listed(store)[("+", "137.0960@hcd10.00")]
    assert a10["isolation_width"] is None
    assert a10["isolation_widths"] == [
        {"isolation_width": 1.0, "scan_count": 1},
        {"isolation_width": 2.0, "scan_count": 1},
    ]
    assert (a10["low_mass"], a10["high_mass"]) == (35.0, 150.0)


def test_kind_and_energy_are_the_activations(store):
    listed = _listed(store)

    assert (
        listed[("+", "137.0960@hcd10.00")]["activation_kind"],
        listed[("+", "137.0960@hcd10.00")]["nce"],
    ) == ("hcd", 10.0)
    chained = listed[("+", "445.1200@cid30.00@hcd20.00")]
    assert (chained["activation_kind"], chained["nce"]) == ("cid@hcd", None)
    assert chained["hcd_energy"] == []
    # The one scan that records a range
    assert (chained["low_mass"], chained["high_mass"]) == (60.0, 455.0)
    bare = listed[("+", "250.5000")]
    assert (bare["activation"], bare["activation_kind"], bare["nce"]) == (
        "",
        None,
        None,
    )


@pytest.mark.parametrize(
    ("t_min", "t_max", "polarity", "counted"),
    [
        (None, None, "+", (1, 8)),
        (None, None, "-", (1, 2)),
        (2.0, 5.0, "+", (0, 4)),
        (1.0, 1.0, "+", (1, 0)),
    ],
)
def test_the_counts_beside_the_groups_are_the_summarys(
    store, t_min, t_max, polarity, counted
):
    """The scan that names no precursor is an MS2 scan and its width counts."""
    summary = store.summary(t_min, t_max, polarity)

    ms1, ms2, widths = store.scan_counts(t_min, t_max, polarity)

    assert (ms1, ms2) == counted
    assert (ms1, ms2) == (summary["ms1_scan_count"], summary["ms2_scan_count"])
    assert len(widths) == ms2
    if polarity == "+" and t_min is None:
        assert 3.0 in widths and summary["isolation_width"] is None


def test_counts_need_a_polarity(store):
    assert store.scan_counts(None, None, None) is None
