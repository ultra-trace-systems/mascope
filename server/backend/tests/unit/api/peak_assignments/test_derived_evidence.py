"""The on-demand measurement's isotopologue rows, offline: labels by offset,
pairing, and a contested peak going to the more abundant isotopologue."""

import numpy as np
import pandas as pd

from mascope_backend.api.new.peak_assignments.derived_evidence import (
    isotopologue_rows,
)


def frame(rows):
    return pd.DataFrame(
        rows,
        columns=[
            "target_ion_id",
            "mz",
            "target_isotope_formula",
            "relative_abundance",
            "sample_peak_id",
            "match_mz_error",
            "match_abundance_error",
        ],
    )


def test_labels_isotopologues_by_their_offset_from_the_lightest_row():
    rows = isotopologue_rows(
        frame(
            [
                # Deliberately out of m/z order: the M0 is the lightest, not the first.
                ("ion", 182.0741, "C5[13C]H13O6+", 0.066, "p2", 1.1, -0.05),
                ("ion", 181.0707, "C6H13O6+", 1.0, "p1", 0.4, 0.02),
                ("ion", 183.0775, "C4[13C]2H13O6+", 0.004, np.nan, np.nan, np.nan),
            ]
        )
    )
    assert [r["isotope_label"] for r in rows] == ["M0", "M+1", "M+2"]
    assert rows[0] == {
        "isotope_label": "M0",
        "isotope_formula": "C6H13O6+",
        "mz": 181.0707,
        "relative_abundance": 1.0,
        "sample_peak_id": "p1",
        "mz_error_ppm": 0.4,
        "abundance_error": 0.02,
    }
    assert rows[1]["sample_peak_id"] == "p2"
    assert rows[1]["mz_error_ppm"] == 1.1
    # Unpaired: predicted, carried for the record, without errors.
    assert rows[2]["sample_peak_id"] is None
    assert rows[2]["mz_error_ppm"] is None
    assert rows[2]["abundance_error"] is None
    assert rows[2]["relative_abundance"] == 0.004


def test_the_more_abundant_isotopologue_keeps_a_contested_peak():
    rows = isotopologue_rows(
        frame(
            [
                ("ion", 181.0707, "M0", 1.0, "p1", 0.4, 0.0),
                # Two isotopologues of one ion landed on the same crowded peak.
                ("ion", 182.0741, "A", 0.066, "p2", 1.1, -0.05),
                ("ion", 182.0770, "B", 0.010, "p2", 2.5, 0.3),
            ]
        )
    )
    assert rows[1]["sample_peak_id"] == "p2"
    assert rows[1]["mz_error_ppm"] == 1.1
    assert rows[2]["sample_peak_id"] is None
    assert rows[2]["mz_error_ppm"] is None
    # Whichever order they came in.
    swapped = isotopologue_rows(
        frame(
            [
                ("ion", 181.0707, "M0", 1.0, "p1", 0.4, 0.0),
                ("ion", 182.0770, "B", 0.010, "p2", 2.5, 0.3),
                ("ion", 182.0741, "A", 0.066, "p2", 1.1, -0.05),
            ]
        )
    )
    assert [(r["isotope_formula"], r["sample_peak_id"]) for r in swapped] == [
        ("M0", "p1"),
        ("A", "p2"),
        ("B", None),
    ]


def test_an_empty_frame_measures_nothing():
    assert isotopologue_rows(frame([])) == []
