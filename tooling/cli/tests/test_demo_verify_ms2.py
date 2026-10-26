"""
Unit tests for the demo reproducibility comparator's MS2 half (``compare_ms2``).

Pure logic - no database, stack, or secrets required. These run standalone:

    uv run pytest tooling/cli/tests/test_demo_verify_ms2.py

The comparator splits the MS2 goldens into (file, polarity, precursor,
activation) groups, pairs each group's centroids by m/z within the tolerance,
and reports what is left on either side, what drifted, and a group whose scan
count or isolation width changed.
"""

import pandas as pd

from mascope_cli.cmd.demo.verify import compare_ms2


_FILE = "Orbion_2026.10.15-03h49m40s_MS2_15NO3_manual-C10H18O2_20261015034940"


def _ms2(rows: list[tuple]) -> pd.DataFrame:
    """An MS2 golden frame from ``(parent, activation, scans, width, mz, intensity)``."""
    return pd.DataFrame(
        [
            {
                "filename": _FILE,
                "polarity": "-",
                "parent_peak_mz": parent,
                "activation": activation,
                "scan_count": scans,
                "isolation_width": width,
                "mz": mz,
                "intensity": intensity,
                "resolution": 60000.0,
                "signal_to_noise": 40.0,
            }
            for parent, activation, scans, width, mz, intensity in rows
        ]
    )


_NO3 = (233.116, "hcd15.00", 13, 1.0, 62.98620, 1512.0)
_LOSS = (233.116, "hcd15.00", 13, 1.0, 213.00070, 203.0)
_LOW = (233.116, "hcd1.00", 8, 1.0, 62.98620, 1112.0)


def test_identical_spectra_reproduce():
    golden = _ms2([_NO3, _LOSS, _LOW])
    assert compare_ms2(golden, golden.copy()) == []


def test_row_order_does_not_matter():
    golden = _ms2([_NO3, _LOSS, _LOW])
    shuffled = _ms2([_LOW, _LOSS, _NO3])
    assert compare_ms2(golden, shuffled) == []


def test_mz_within_tolerance_pairs_the_centroids():
    golden = _ms2([_NO3])
    p, a, n, w, mz, h = _NO3
    actual = _ms2([(p, a, n, w, mz * (1 + 0.05e-6), h)])
    assert compare_ms2(golden, actual) == []


def test_mz_drift_beyond_tolerance_is_missing_and_unexpected():
    """A centroid that moved past the tolerance pairs with nothing: it is
    reported as missing where it was and unexpected where it is."""
    golden = _ms2([_NO3])
    p, a, n, w, mz, h = _NO3
    actual = _ms2([(p, a, n, w, mz * (1 + 5e-6), h)])
    problems = compare_ms2(golden, actual, tolerances={"mz_ppm": 1.0})
    assert len(problems) == 2
    assert any("missing MS2 centroid" in m for m in problems)
    assert any("unexpected MS2 centroid" in m for m in problems)


def test_intensity_drift_flagged():
    golden = _ms2([_NO3])
    p, a, n, w, mz, h = _NO3
    actual = _ms2([(p, a, n, w, mz, h * 1.05)])
    problems = compare_ms2(golden, actual)
    assert len(problems) == 1
    assert "intensity differs by 5.00%" in problems[0]
    assert "233.1160@hcd15.00" in problems[0]


def test_missing_centroid_flagged():
    golden = _ms2([_NO3, _LOSS])
    actual = _ms2([_NO3])
    problems = compare_ms2(golden, actual)
    assert problems == [
        f"{_FILE} [-] 233.1160@hcd15.00: missing MS2 centroid m/z 213.00070 "
        "(intensity 203)"
    ]


def test_unexpected_centroid_flagged():
    golden = _ms2([_NO3])
    actual = _ms2([_NO3, _LOSS])
    problems = compare_ms2(golden, actual)
    assert len(problems) == 1
    assert problems[0].startswith(f"{_FILE} [-] 233.1160@hcd15.00: unexpected")


def test_missing_and_unexpected_groups_are_named():
    golden = _ms2([_NO3, _LOW])
    actual = _ms2([_NO3, (233.116, "hcd30.00", 5, 1.0, 62.98620, 900.0)])
    problems = compare_ms2(golden, actual)
    assert f"missing MS2 group: {_FILE} [-] 233.1160@hcd1.00" in problems
    assert f"unexpected MS2 group: {_FILE} [-] 233.1160@hcd30.00" in problems
    assert len(problems) == 2


def test_scan_count_and_isolation_width_are_compared():
    golden = _ms2([_NO3])
    p, a, n, w, mz, h = _NO3
    actual = _ms2([(p, a, n + 1, 0.7, mz, h)])
    problems = compare_ms2(golden, actual)
    assert any("scan_count differs (expected 13, got 14)" in m for m in problems)
    assert any("isolation_width differs" in m for m in problems)
    assert len(problems) == 2


def test_absent_group_values_on_both_sides_agree():
    """A group without a stored isolation width stays without one."""
    golden = _ms2([(233.116, "hcd15.00", 13, None, 62.98620, 1512.0)])
    assert compare_ms2(golden, golden.copy()) == []


def test_nearest_centroid_wins_a_contested_pairing():
    """Two expected centroids within the tolerance of both actual ones pair
    nearest first, each with its own. Paired in index order instead, the
    first expected would take the first actual and both intensities would
    read as drifted."""
    golden = _ms2(
        [
            (233.116, "hcd15.00", 13, 1.0, 100.00000, 500.0),
            (233.116, "hcd15.00", 13, 1.0, 100.00004, 400.0),
        ]
    )
    actual = _ms2(
        [
            (233.116, "hcd15.00", 13, 1.0, 100.00003, 400.0),
            (233.116, "hcd15.00", 13, 1.0, 100.00009, 500.0),
        ]
    )
    assert compare_ms2(golden, actual, {"mz_ppm": 1.0}) == []


def test_empty_actual_names_every_group_missing():
    golden = _ms2([_NO3, _LOW])
    problems = compare_ms2(golden, golden.iloc[0:0])
    assert len(problems) == 2
    assert all(m.startswith("missing MS2 group") for m in problems)


def test_same_group_in_another_polarity_or_file_is_distinct():
    golden = _ms2([_NO3])
    other = golden.copy()
    other["polarity"] = "+"
    problems = compare_ms2(golden, other)
    assert any("missing MS2 group" in m and "[-]" in m for m in problems)
    assert any("unexpected MS2 group" in m and "[+]" in m for m in problems)
