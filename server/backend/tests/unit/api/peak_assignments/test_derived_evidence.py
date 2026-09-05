"""The on-demand measurement's isotopologue rows, offline: labels by offset,
pairing, and a contested peak going to the more abundant isotopologue."""

import numpy as np
import pandas as pd
import pytest

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


def test_abundances_are_fractions_of_the_most_abundant_isotopologue():
    # The seed pattern arrives normalised to its sum, so the M0 is only its
    # share of the envelope here (89 %). The inspector's column is a fraction of
    # the most abundant isotopologue, as an isotope table's is: it reads 1 and
    # the rest scale with it.
    rows = isotopologue_rows(
        frame(
            [
                ("ion", 181.0707, "C6H13O6+", 0.89, "p1", 0.4, 0.0),
                ("ion", 182.0741, "C5[13C]H13O6+", 0.099, "p2", 1.1, -0.05),
                ("ion", 183.0775, "C4[13C]2H13O6+", 0.011, np.nan, np.nan, np.nan),
            ]
        )
    )
    assert [r["relative_abundance"] for r in rows] == pytest.approx(
        [1.0, 0.099 / 0.89, 0.011 / 0.89]
    )
    # The pairing and its errors are untouched by the scaling.
    assert rows[1]["sample_peak_id"] == "p2"
    assert rows[1]["abundance_error"] == -0.05


def test_a_bromine_rich_ion_counts_from_its_monoisotopic_peak_and_tops_out_at_one():
    # Br3-: the monoisotopic peak (79Br3) is the lightest but not the most
    # intense - the 79Br2 81Br combination is three times as likely. Labels
    # count from the monoisotopic peak the way an isotope table does (M0, M+2,
    # M+4, M+6; nothing below M0), and the abundances top out at the most
    # abundant isotopologue rather than exceeding 1.
    rows = isotopologue_rows(
        frame(
            [
                ("ion", 236.7558, "Br3-", 0.128, "p1", 0.8, 0.02),
                ("ion", 238.7537, "[81Br]Br2-", 0.373, np.nan, np.nan, np.nan),
                ("ion", 240.7515, "[81Br]2Br-", 0.363, "p3", 0.3, 0.01),
                ("ion", 242.7494, "[81Br]3-", 0.118, "p4", -0.1, -0.03),
            ]
        )
    )
    assert [r["isotope_label"] for r in rows] == ["M0", "M+2", "M+4", "M+6"]
    assert [r["relative_abundance"] for r in rows] == pytest.approx(
        [0.128 / 0.373, 1.0, 0.363 / 0.373, 0.118 / 0.373]
    )
    assert max(r["relative_abundance"] for r in rows) == 1.0


def test_the_monoisotopic_peak_is_the_unsubstituted_formula_not_the_lightest_row():
    # An element whose most abundant isotope is not its lightest (iron: 56Fe
    # over 54Fe): the monoisotopic isotopologue is the unmarked formula, and
    # the lighter substitution reads as M-2 - the one case a negative offset
    # is the right name.
    rows = isotopologue_rows(
        frame(
            [
                ("ion", 110.0, "[54Fe]C4H2+", 0.058, np.nan, np.nan, np.nan),
                ("ion", 112.0, "FeC4H2+", 0.917, "p1", 0.2, 0.0),
                ("ion", 113.0, "[57Fe]C4H2+", 0.021, np.nan, np.nan, np.nan),
            ]
        )
    )
    assert [r["isotope_label"] for r in rows] == ["M-2", "M0", "M+1"]
    assert rows[1]["relative_abundance"] == 1.0


def test_abundances_scale_by_the_most_abundant_that_carries_one():
    rows = isotopologue_rows(
        frame(
            [
                ("ion", 181.0707, "C6H13O6+", np.nan, "p1", 0.4, 0.0),
                ("ion", 182.0741, "C5[13C]H13O6+", 0.066, "p2", 1.1, -0.05),
            ]
        )
    )
    assert rows[0]["relative_abundance"] is None
    assert rows[1]["relative_abundance"] == 1.0


def test_the_main_peak_note_names_the_nearest_prediction_and_where_it_went():
    from mascope_backend.api.new.peak_assignments.derived_evidence import (
        main_peak_note,
    )

    isotopologues = [
        {"isotope_label": "M0", "mz": 236.7558, "sample_peak_id": "p1"},
        {"isotope_label": "M+2", "mz": 238.7537, "sample_peak_id": None},
        {"isotope_label": "M+4", "mz": 240.7515, "sample_peak_id": "p3"},
    ]
    # The main peak sits 1.3 ppm from the M+2 prediction, which paired nothing.
    note = main_peak_note(238.7537 * (1 + 1.3e-6), isotopologues)
    assert note.startswith("the family's main peak at m/z 238.7540 matched none")
    assert (
        "M+2 predicted at m/z 238.7537, lies 1.3 ppm away and paired with no peak"
        in note
    )
    # A prediction that found a different peak says so instead.
    note = main_peak_note(240.7520, isotopologues)
    assert "M+4 predicted at m/z 240.7515" in note
    assert note.endswith("paired with another peak")
    # Nothing predicted: the head alone.
    assert main_peak_note(200.0, []) == (
        "the family's main peak at m/z 200.0000 matched none of the ion's predicted "
        "isotopologues within the sample's m/z window"
    )
