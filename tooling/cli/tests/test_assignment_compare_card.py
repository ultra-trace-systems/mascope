"""The comparison tool's gate card (``tooling/assignment_compare``, ``--card``).

Every metric of the plan's table is read off synthetic ledgers joined the way
the tool joins them, so each test states the rows and the number they give. The
gate is read against a thresholds file, the diff between two cards, and the
scrub against a card that holds a name; the whole command against a client that
serves canned runs, ledgers and details.
"""

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from mascope_sdk import NotFoundError
from mascope_sdk.exceptions import MascopeTimeoutError


TOOL = Path(__file__).resolve().parents[2] / "assignment_compare"


def _load(name):
    sys.path.insert(0, str(TOOL))
    spec = importlib.util.spec_from_file_location(name, TOOL / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


metrics = _load("metrics")
compare_runs = _load("compare_runs")

#: Mechanism ids of the synthetic ledgers, and their notations.
NOTATION = {
    "dep": "[M-H]-",
    "no3": "[M+NO3]-",
    "hcoo": "[M+HCOO]-",
    "brm": "[M+Br]-",
}


def _row(peak, mz=100.0, intensity=100.0, **values):
    """One ledger row; an M0 row at assigned through deprotonation by default."""
    row = {
        "peak_assignment_id": f"pa-{peak}",
        "sample_peak_id": peak,
        "sample_peak_mz": mz,
        "sample_peak_intensity": intensity,
        "role": "M0",
        "tier": "assigned",
        "source": "untargeted",
        "assigned_formula": "C6H10O4",
        "ion_formula": "C6H9O4-",
        "ionization_mechanism_id": "dep",
        "mz_error_ppm": 0.1,
        "mass_z": 0.5,
        "corroboration_channels": 1,
        "reference_listing": None,
        "owner_peak_assignment_id": None,
        "engine_tier": None,
    }
    row.update(values)
    return row


def _unassigned(peak, mz=100.0, intensity=100.0):
    return _row(
        peak,
        mz,
        intensity,
        role="unassigned",
        tier="unassigned",
        source=None,
        assigned_formula=None,
        ion_formula=None,
        ionization_mechanism_id=None,
        mz_error_ppm=None,
        mass_z=None,
    )


def _frame(a_rows, b_rows=None, *, sample="s1", provenance=None, **columns):
    """The frame the card reads for one sample: engine A's ledger joined to
    engine B's (or to nothing), with the columns the card adds."""
    a = compare_runs.prepare(pd.DataFrame(a_rows), "a", NOTATION)
    b = (
        compare_runs.prepare(pd.DataFrame(b_rows), "b", NOTATION)
        if b_rows is not None
        else compare_runs.absent_side("b")
    )
    frame = compare_runs.join_ledgers(a, b)
    frame.insert(0, "sample_item_id", sample)
    frame["reference"] = b_rows is not None
    frame["sulfur_free"] = columns.pop("sulfur_free", False)
    frame["a_unsearched"] = frame["sample_peak_id"].isin(columns.pop("unsearched", ()))
    read = {
        f"pa-{peak}": metrics.provenance_columns(record)
        for peak, record in (provenance or {}).items()
    }
    for column in (
        "a_reasons",
        "a_mass_gate_corroborated",
        "a_absent_lines",
        "a_unseparated",
        "a_series_anchor",
    ):
        if provenance is not None:
            frame[column] = frame["a_assignment_id"].map(
                lambda row_id, c=column: read.get(row_id, {}).get(c)
            )
    if provenance is not None:
        failed = {f"pa-{peak}" for peak, record in provenance.items() if record is None}
        frame["a_detail_failed"] = frame["a_assignment_id"].isin(failed)
    return frame


def _value(one):
    return one["value"], one["n"]


# --- G1 and G2: against the reference -----------------------------------------


def _g1_frames():
    mine = [
        _row("p1", assigned_formula="C6H10O4", ion_formula="C6H9O4-"),
        _row("p2"),
        _row("p3", assigned_formula="C7H12O3", ion_formula="C7H11O3-"),
        _row("p4"),
        _row("p5"),
        _row("p6", tier="candidate", assigned_formula="C9H9O9"),
    ]
    theirs = [
        # The same formula through the same adduct: confirmed.
        _row("p1", engine_tier="assigned"),
        # The same ion read as another neutral and adduct: the split.
        _row(
            "p2",
            assigned_formula="C5H8O2",
            ion_formula="C6H9O4-",
            ionization_mechanism_id="hcoo",
            # The reference's own spelling of its tier is read in any case.
            engine_tier="Assigned",
        ),
        # Another formula: a contradiction.
        _row("p3", assigned_formula="C8H8O2", ion_formula="C8H7O2-"),
        # Nothing on the peak at all: silence.
        _unassigned("p4"),
        # Another compound's isotopologue: a contradiction too.
        _row("p5", role="iso_child", tier="assigned", engine_tier="assigned"),
        _row("p6"),
    ]
    return pd.concat(
        [
            _frame(mine, theirs),
            # A sample without a reference run reads in no G1 at all.
            _frame([_row("q1")], sample="s2"),
        ],
        ignore_index=True,
    )


def test_g1_is_the_share_of_assigned_rows_the_reference_does_not_confirm():
    frame = _g1_frames()
    assert _value(metrics.g1_unconfirmed(frame)) == (80.0, 5)
    parts = metrics.g1_parts(frame)
    assert _value(parts["contradicts"]) == (40.0, 5)
    assert _value(parts["split"]) == (20.0, 5)
    assert _value(parts["silent"]) == (20.0, 5)


def test_g1_conditioned_reads_only_the_rows_the_reference_commits_an_m0_on():
    # p1, p2 and p3: the split and the contradiction stay unconfirmed.
    assert _value(metrics.g1_conditioned(_g1_frames())) == (66.7, 3)


def test_g2_reads_the_reference_s_own_assigned_tier():
    recovered = metrics.g2_recovered(_g1_frames())
    # p1 and p2 are the reference's Assigned M0 rows; p5 is an isotopologue.
    assert _value(recovered["formula"]) == (50.0, 2)
    assert _value(recovered["ion"]) == (100.0, 2)


def test_a_set_without_a_reference_reads_no_reference_metric():
    frame = _frame([_row("q1")])
    assert metrics.g1_unconfirmed(frame)["value"] is None
    assert metrics.g1_conditioned(frame)["value"] is None
    assert metrics.g2_recovered(frame)["formula"]["value"] is None
    assert metrics.g4_reagent_agreement(frame)["value"] is None
    assert metrics.g5_unsearched(frame)["value"] is None
    assert metrics.g6_main_on_isotopologue(frame)["value"] is None


# --- G3 to G8, the mass error and the reasons --------------------------------------


def test_g3_counts_nitrogen_rich_and_carbon_free_formulas():
    frame = _frame(
        [
            _row("p1", assigned_formula="C5H10N5O"),
            _row("p2", assigned_formula="C6H12O6"),
            _row("p3", assigned_formula="C2H3N3"),
            _row("p4", assigned_formula="H2SO4", source="database"),
            _row("p5", assigned_formula="HNO3"),
        ]
    )
    assert _value(metrics.g3_nitrogen_rich(frame)) == (33.3, 3)
    assert _value(metrics.g3_carbon_free(frame)) == (2, 5)
    assert _value(metrics.g3_carbon_free(frame, untargeted_only=True)) == (1, 5)


def test_g4_reads_the_reference_s_reagent_peaks_and_g4a_those_naming_an_ion():
    mine = [
        _row("p1", role="reagent", tier="unassigned"),
        _row("p2"),
        _row("p3", role="artifact", tier="unassigned"),
    ]
    theirs = [
        _row("p1", role="reagent", tier="unassigned", ion_formula="NO3-"),
        _row("p2", role="reagent", tier="unassigned", ion_formula=None),
        _row("p3", role="reagent", tier="unassigned", ion_formula="Br-"),
    ]
    frame = _frame(mine, theirs)
    assert _value(metrics.g4_reagent_agreement(frame)) == (66.7, 3)
    assert _value(metrics.g4_reagent_agreement(frame, naming_an_ion=True)) == (100.0, 2)


def test_g5_counts_reference_assigned_peaks_the_search_never_saw():
    mine = [_unassigned("p1"), _unassigned("p2"), _unassigned("p3")]
    theirs = [
        _row("p1", engine_tier="assigned"),
        _row("p2", engine_tier="candidate"),
        _row("p3", engine_tier="assigned"),
    ]
    frame = _frame(mine, theirs, unsearched={"p1", "p2"})
    # p2 is unsearched but not the reference's Assigned; p3 was searched.
    assert _value(metrics.g5_unsearched(frame)) == (1, 2)


def _remainder():
    """Four peaks the formula search could be offered, brightest first."""
    return pd.DataFrame(
        [
            _unassigned("p1", intensity=400.0),
            _unassigned("p2", intensity=300.0),
            _row("p3", intensity=200.0),
            _unassigned("p4", intensity=100.0),
            _row("p5", intensity=900.0, source="database"),
            _row("p6", intensity=0.0, role="unassigned", source=None),
        ]
    )


def test_the_search_s_cap_leaves_the_dimmest_peaks_unsearched():
    config = {"search_scope": {"searched_peaks": 2, "limited": True}}
    # p3 is past the cap of two, but the search committed a row on it; p6 has
    # no intensity, and p5 the curated stage explained.
    assert compare_runs.unsearched_peaks(_remainder(), config) == {"p4", "p6"}
    # A run from before the scope record reads its cap off the config.
    assert compare_runs.unsearched_peaks(_remainder(), {"max_untargeted_peaks": 3}) == {
        "p4",
        "p6",
    }
    assert compare_runs.unsearched_peaks(_remainder(), {}) == {"p6"}


def test_a_search_the_run_records_as_unlimited_left_no_peak_past_a_cap():
    # The ledger holds four peaks the search could have been offered, the run
    # says it was offered three and not limited: a later pass moved one.
    config = {"search_scope": {"searched_peaks": 3, "limited": False}}
    assert compare_runs.unsearched_peaks(_remainder(), config) == {"p6"}


def test_g6_counts_main_peaks_the_reference_reads_as_isotopologues():
    mine = [_row("p1"), _row("p2", tier="candidate"), _row("p3")]
    theirs = [
        _row("p1", role="iso_child"),
        _row("p2", role="iso_child"),
        _row("p3"),
    ]
    frame = _frame(mine, theirs)
    assert _value(metrics.g6_main_on_isotopologue(frame)) == (2, 3)
    assert _value(metrics.g6_main_on_isotopologue(frame, assigned_only=True)) == (1, 2)


def test_g7_counts_assigned_rows_beyond_three_widths_with_no_corroboration():
    rows = [
        _row("p1", mass_z=3.5),
        _row("p2", mass_z=-3.2),
        _row("p3", mass_z=4.0),
        _row("p4", mass_z=1.0),
        _row("p5", mass_z=5.0, tier="candidate"),
    ]
    provenance = {
        "p1": {"mass_gate": {}},
        "p2": {"mass_gate": {"corroborated_by": []}},
        "p3": {"mass_gate": {"corroborated_by": ["isotope"]}},
        "p4": {},
        "p5": {},
    }
    frame = _frame(rows, provenance=provenance)
    assert _value(metrics.g7_uncorroborated_beyond_3(frame)) == (2, 4)
    # A row whose detail read failed reads as uncorroborated, not as NaN's truth.
    # (The card's merge leaves the column of object dtype, NaN where unread.)
    frame["a_mass_gate_corroborated"] = frame["a_mass_gate_corroborated"].astype(object)
    frame.loc[frame["sample_peak_id"] == "p1", "a_mass_gate_corroborated"] = np.nan
    assert _value(metrics.g7_uncorroborated_beyond_3(frame)) == (2, 4)


def test_g7_is_not_read_without_the_detail_read():
    assert metrics.g7_uncorroborated_beyond_3(_frame([_row("p1")]))["value"] is None


def test_g8_counts_untargeted_isotopologue_rows_without_an_owner():
    rows = [
        _row("p1"),
        _row("p2", role="iso_child", owner_peak_assignment_id="pa-p1"),
        _row("p3", role="iso_child", owner_peak_assignment_id=None),
        _row("p4", role="iso_child", owner_peak_assignment_id=None, source="database"),
    ]
    assert _value(metrics.g8_ownerless(_frame(rows))) == (1, 2)


def test_the_mad_is_over_every_committed_main_row():
    rows = [
        _row("p1", mz_error_ppm=0.1),
        _row("p2", mz_error_ppm=0.2, tier="candidate"),
        _row("p3", mz_error_ppm=0.5),
        _row("p4", mz_error_ppm=-0.3),
        _row("p5", mz_error_ppm=9.0, role="iso_child"),
    ]
    # Median 0.15; the deviations 0.05, 0.05, 0.35 and 0.45 have median 0.2.
    assert _value(metrics.mass_error_mad(_frame(rows))) == (0.2, 4)


def test_reason_coverage_is_the_share_of_committed_rows_with_a_reason():
    rows = [
        _row("p1"),
        _row("p2", role="iso_child", owner_peak_assignment_id="pa-p1"),
        _row("p3", tier="candidate"),
        _row("p4"),
    ]
    provenance = {
        "p1": {"tier_reasons": [{"rule": "corroborated"}]},
        "p2": {"tier_reasons": [{"rule": "isotopologue"}]},
        "p3": {"tier_reasons": []},
        "p4": None,
    }
    frame = _frame(rows, provenance=provenance)
    # p4's read failed: it is not counted either way, and the failure is.
    assert _value(metrics.reason_coverage(frame)) == (66.7, 3)
    assert _value(metrics.detail_failed(frame)) == (1, 4)
    # Without the detail read, nothing is read.
    assert _value(metrics.detail_failed(_frame(rows))) == (None, None)


# --- G9 to G14 -------------------------------------------------------------------


def test_an_isotope_s_mass_number_is_no_count():
    assert metrics.element_counts("C2H4[15N]O3") == {"C": 2, "H": 4, "N": 1, "O": 3}
    assert metrics.element_counts("C2H3^NO3") == {"C": 2, "H": 3, "N": 1, "O": 3}


def test_g9_counts_implausible_assigned_neutrals():
    rows = [
        # Two nitrogens with one oxygen.
        _row("p1", assigned_formula="C2H4N2O"),
        _row("p2", assigned_formula="C10H16O3"),
        # Sulfur, implausible only where the set holds none.
        _row("p3", assigned_formula="C4H8O3S"),
        # H/C of 3.
        _row("p4", assigned_formula="C3H9N"),
        # A negative double-bond equivalent: 2 - 7/2 + 1.
        _row("p5", assigned_formula="C2H4F3"),
        _row("p6", assigned_formula="C2H4N2O", tier="candidate"),
    ]
    assert _value(metrics.g9_implausible(_frame(rows))) == (60.0, 5)
    assert _value(metrics.g9_implausible(_frame(rows, sulfur_free=True))) == (80.0, 5)


def test_g9_holds_a_neutral_on_each_edge_plausible():
    rows = [
        # A saturated neutral: a double-bond equivalent of 0, 6 - 14/2 + 1.
        _row("p1", assigned_formula="C6H14O"),
        # Exactly two oxygens per nitrogen.
        _row("p2", assigned_formula="C6H4N2O4"),
        # H/C of exactly 2.4.
        _row("p3", assigned_formula="C5H12"),
    ]
    assert _value(metrics.g9_implausible(_frame(rows))) == (0.0, 3)


def test_g10_is_the_intensity_share_of_the_source_ions_a_run_names():
    rows = [
        _row("p1", mz=100.0001, intensity=900.0, role="reagent", tier="unassigned"),
        _unassigned("p2", mz=200.0003, intensity=100.0),
        # A dimmer peak in the same window: the brightest one is the ion's.
        _unassigned("p3", mz=199.9998, intensity=50.0),
        # Not on an ion.
        _unassigned("p4", mz=300.0, intensity=1000.0),
        # An ion read through a list tagged as background is named.
        _row(
            "p5",
            mz=400.0,
            intensity=300.0,
            source="database",
            reference_listing={"name": "a siloxane", "tags": ["background"]},
        ),
        # Under the pre-pass's floor of the base peak: no ion it failed to name.
        _unassigned("p6", mz=500.0, intensity=0.05),
        # 5 ppm off its ion, outside the run's window of 3.
        _unassigned("p7", mz=600.003, intensity=200.0),
        # A list that does not tag the ion as background does not name it.
        _row(
            "p8",
            mz=700.0,
            intensity=100.0,
            source="database",
            reference_listing={"name": "an acid", "tags": []},
        ),
    ]
    ions = {"s1": ([100.0, 200.0, 400.0, 500.0, 600.0, 700.0], 3.0)}
    reading = metrics.g10_source_ions_named(_frame(rows), ions)
    # p1 and p5 of p1, p2, p5 and p8.
    assert _value(reading) == (85.7, 4)
    assert metrics.g10_source_ions_named(_frame(rows), {})["value"] is None


def test_g10_reads_the_library_of_the_profile_the_run_resolved():
    ions, ppm = metrics.profile_source_ions(
        {"resolved_profile": {"profile": "NO3", "mz_precision_ppm": 10.0}}
    )
    assert ppm == 10.0
    assert any(abs(mz - 61.98837) < 1e-3 for mz in ions)
    assert metrics.profile_source_ions({}) == ([], None)


def test_g11_counts_deprotonated_acids_whose_formate_reading_has_a_partner():
    def acid(peak, formula, intensity=100.0, **values):
        return _row(peak, intensity=intensity, assigned_formula=formula, **values)

    def through_nitrate(peak, formula, **values):
        return acid(peak, formula, ionization_mechanism_id="no3", **values)

    rows = [
        # C11H18O5 less CH2O2 is C10H16O3, which is assigned through nitrate.
        acid("p1", "C11H18O5"),
        through_nitrate("p2", "C10H16O3", intensity=300.0),
        # No C10H14O2 in the sample.
        acid("p3", "C11H16O4"),
        # Not a CHO acid, though C10H17NO2 is assigned.
        acid("p4", "C11H19NO4"),
        through_nitrate("p5", "C10H17NO2"),
        # Its lighter neutral is read only through formate itself.
        acid("p6", "C11H18O4"),
        acid("p7", "C10H16O2", ionization_mechanism_id="hcoo"),
        # A candidate acid with a partner counts in the committed reading only.
        acid("p8", "C11H16O6", intensity=200.0, tier="candidate"),
        through_nitrate("p9", "C10H14O4"),
        # Its lighter neutral is only a candidate: no partner assigned.
        acid("p10", "C11H20O5"),
        through_nitrate("p11", "C10H18O3", tier="candidate"),
        # Not read through deprotonation: no acid.
        acid("p12", "C11H14O5", ionization_mechanism_id="brm"),
        through_nitrate("p13", "C10H12O3"),
        # A C8 acid with a partner is not the C11 the metric reads.
        acid("p14", "C8H12O4"),
        through_nitrate("p15", "C7H10O2"),
    ]
    frame = _frame(rows)
    # p1 of the 1,500 assigned; p1 and p8 of the 1,800 assigned and candidate.
    assert _value(metrics.g11_formate_pseudo_acids(frame)) == (6.7, 13)
    assert _value(metrics.g11_formate_pseudo_acids(frame, committed=True)) == (
        16.7,
        15,
    )


def test_g11_reads_a_partner_in_the_acid_s_own_sample_only():
    frame = pd.concat(
        [
            _frame(
                [_row("p1", assigned_formula="C10H16O3", ionization_mechanism_id="no3")]
            ),
            _frame([_row("q1", assigned_formula="C11H18O5")], sample="s2"),
        ],
        ignore_index=True,
    )
    assert _value(metrics.g11_formate_pseudo_acids(frame)) == (0.0, 2)


def test_g12_counts_assigned_search_rows_nothing_else_saw():
    rows = [
        _row("p1"),
        _row("p2"),
        _row("p2i", role="iso_child", owner_peak_assignment_id="pa-p2"),
        _row("p3", corroboration_channels=2),
        _row("p4", reference_listing={"name": "an acid", "tags": []}),
        _row("p5", source="database"),
        _row("p6", tier="candidate"),
    ]
    assert _value(metrics.g12_lone_peaks(_frame(rows))) == (1, 4)


#: A row's provenance where a series of its batch anchors it and nothing else
#: saw it twice: the reason the tiering rule keeps it at assigned on.
_SERIES = {"tier_reasons": [{"rule": "series_anchor"}, {"rule": "no_close_rival"}]}


def test_g12_exempts_a_series_anchored_row_and_the_line_beside_it_counts_it():
    rows = [
        # No escape at all: G12's, not the line's.
        _row("p1"),
        # A series is all that saw it: the line's, not G12's.
        _row("p2"),
        # A series and a second channel: another escape, so in neither.
        _row("p3", corroboration_channels=2),
        # A series and a line of its own isotope pattern: in neither.
        _row("p4"),
        _row("p4i", role="iso_child", owner_peak_assignment_id="pa-p4"),
        # A series and a list: in neither.
        _row("p5", reference_listing={"name": "an acid", "tags": []}),
        # A series on a row the search did not elect: in neither.
        _row("p6", source="database"),
    ]
    provenance = {peak: _SERIES for peak in ("p2", "p3", "p4", "p5", "p6")}
    frame = _frame(rows, provenance={"p1": {"tier_reasons": []}, **provenance})
    assert _value(metrics.g12_lone_peaks(frame)) == (1, 5)
    lifted = metrics.series_lifted(frame)
    assert _value(lifted["G12_series"]) == (1, 5)
    # No reference run: the shares are not read, not 0.
    for key in ("series_same", "series_other", "yardstick_silent"):
        assert lifted[f"G12_{key}"] == {"value": None, "n": None}
    readings = metrics.card_readings(frame)
    keys = list(readings)
    assert keys[keys.index("G12") + 1] == "G12_series"
    assert readings["G12"] == {"value": 1, "n": 5}


def test_without_a_series_anchored_row_the_card_holds_no_series_line():
    # The control's runs read no series: G12 as it was, and no line beside it.
    rows = [_row("p1"), _row("p2", corroboration_channels=2)]
    frame = _frame(rows, provenance={"p1": {"tier_reasons": []}})
    assert metrics.series_lifted(frame) == {}
    readings = metrics.card_readings(frame)
    assert not [key for key in readings if key.startswith("G12_")]
    assert readings["G12"] == {"value": 1, "n": 2}
    # A frame from before the record reads as one with no series.
    assert metrics.series_lifted(_frame(rows)) == {}


def test_the_series_line_reads_the_lifted_rows_and_the_rest_against_the_reference():
    mine = [
        # Lifted by the series: the reference agrees, reads otherwise, says
        # nothing, and agrees.
        _row("p1"),
        _row("p2"),
        _row("p3"),
        _row("p4"),
        # The yardstick: the other assigned search rows, lone or not.
        _row("q1", corroboration_channels=2),
        _row("q2", corroboration_channels=2),
        _row("q3"),
        # Not a search row at assigned: in neither.
        _row("r1", tier="candidate"),
        _row("r2", source="database"),
    ]
    theirs = [
        _row("p1"),
        _row("p2", assigned_formula="C7H12O3", ion_formula="C7H11O3-"),
        _row("p4"),
        _row("q1"),
        _row("q2", role="reagent", tier="unassigned", source=None),
        _row("r1"),
        _row("r2"),
    ]
    provenance = {peak: _SERIES for peak in ("p1", "p2", "p3", "p4", "r1", "r2")}
    lifted = metrics.series_lifted(_frame(mine, theirs, provenance=provenance))
    assert {key: _value(one) for key, one in lifted.items()} == {
        "G12_series": (4, 7),
        "G12_series_same": (50.0, 4),
        "G12_series_other": (25.0, 4),
        "G12_series_silent": (25.0, 4),
        "G12_yardstick_same": (33.3, 3),
        "G12_yardstick_other": (33.3, 3),
        "G12_yardstick_silent": (33.3, 3),
    }
    # The shares read the samples with a reference run alone; the count all.
    pooled = pd.concat(
        [
            _frame(mine, theirs, provenance=provenance),
            _frame([_row("s1")], sample="s2", provenance={"s1": _SERIES}),
        ],
        ignore_index=True,
    )
    lifted = metrics.series_lifted(pooled)
    assert _value(lifted["G12_series"]) == (5, 8)
    assert _value(lifted["G12_series_same"]) == (50.0, 4)


def test_the_level_shares_and_where_tier_and_level_cross():
    # Step 5.8: five committed readings state a level, a sixth does not.
    frame = _frame(
        [
            _row("p1", level="4a"),
            _row("p2", level="5a"),
            _row("p8", level="4a"),
            _row("p3", tier="candidate", level="4a"),
            _row("p4", tier="candidate", level="5b"),
            _row("p5"),
            _row("p6", role="iso_child", level="4a"),
            _unassigned("p7"),
        ]
    )

    readings = metrics.card_readings(frame)

    assert _value(readings["level_4a"]) == (60.0, 5)
    assert _value(readings["level_4b"]) == (0.0, 5)
    assert _value(readings["level_5a"]) == (20.0, 5)
    assert _value(readings["level_5b"]) == (20.0, 5)
    # Of the three assigned rows that state one, one is at 5; of the two under
    # assigned, one is at 4 or above.
    assert _value(readings["level_assigned_at_5"]) == (33.3, 3)
    assert _value(readings["level_under_assigned_at_4"]) == (50.0, 2)


def test_the_5b_rows_split_by_what_put_them_there():
    absent = {"predicted_envelope": {"lines": [{"state": "absent"}]}}
    unseparated = {"peak_separation": {"separated": False}}
    # Eight rows at 5b: three with an absent line alone, two with an
    # unseparated peak alone, one with both, and two with neither: one whose
    # lines were all found, and one whose peak is unseparated but a separated
    # partner commits the neutral. A 5a row with both records is left out.
    provenance = {
        "p1": absent,
        "p2": absent,
        "p3": absent,
        "p4": unseparated,
        "p5": unseparated,
        "p6": {**absent, **unseparated},
        "p7": {"predicted_envelope": {"lines": [{"state": "found"}]}},
        "p8": {**unseparated, "cross_channel": {"separated_through": ["[M+Br]-"]}},
        "p9": {**absent, **unseparated},
    }
    rows = [
        _row(peak, tier="candidate" if peak == "p4" else "assigned", level="5b")
        for peak in provenance
    ]
    rows[-1]["level"] = "5a"

    readings = metrics.card_readings(_frame(rows, provenance=provenance))

    assert _value(readings["level_5b_absent"]) == (50.0, 8)
    assert _value(readings["level_5b_unseparated"]) == (37.5, 8)
    assert _value(readings["level_5b_neither"]) == (25.0, 8)
    assert [key for key, _, _ in metrics.METRICS if key.startswith("level_5b_")] == [
        f"level_5b_{fact}" for fact in metrics.LEVEL_5B_FACTS
    ]


def test_a_5b_row_without_a_record_carries_none_of_its_facts():
    # A run whose 5b rows hold no envelope record leaves the column all None;
    # a frame of the ledger's flattened columns puts NaN beside a count. Both
    # read as not carrying the fact.
    frame = _frame([_row("p1", level="5b"), _row("p2", level="5b")])
    frame["a_absent_lines"] = [None, None]
    frame["a_unseparated"] = [None, True]

    assert _value(metrics.level_5b_fact(frame, "absent")) == (0.0, 2)
    assert _value(metrics.level_5b_fact(frame, "unseparated")) == (50.0, 2)
    assert _value(metrics.level_5b_fact(frame, "neither")) == (50.0, 2)
    frame["a_absent_lines"] = [2, None]
    assert _value(metrics.level_5b_fact(frame, "absent")) == (50.0, 2)
    assert _value(metrics.level_5b_fact(frame, "neither")) == (0.0, 2)
    assert _value(metrics.level_5b_fact(frame.drop(columns="a_level"), "absent")) == (
        None,
        None,
    )
    # A set with no row at 5b reads none of the split, every fact alike.
    frame["a_level"] = "5a"
    for fact in metrics.LEVEL_5B_FACTS:
        assert _value(metrics.level_5b_fact(frame, fact)) == (None, 0)


def test_a_run_from_before_the_level_reads_no_level():
    readings = metrics.card_readings(_frame([_row("p1"), _row("p2")]))

    for key in ("level_4a", "level_5a", "level_assigned_at_5"):
        assert _value(readings[key]) == (None, None)


def test_a_level_the_scale_does_not_name_crosses_nothing():
    frame = _frame([_row("p1", level="9z"), _row("p2", tier="candidate", level="?")])

    readings = metrics.card_readings(frame)

    assert _value(readings["level_assigned_at_5"]) == (0.0, 1)
    assert _value(readings["level_under_assigned_at_4"]) == (0.0, 1)


def test_an_arm_reads_its_committed_rows_at_level_4_or_above():
    total = {
        "samples": 1,
        "control": {"rows": 10},
        "decoy": {"rows": 2, "levelled": 8, "at_4_or_above": 1},
        "at_4_or_above_pct": compare_runs.pct(1, 8),
    }

    readings = metrics.card_readings(
        _frame([_row("p1")]), decoy_arms={"mz_shift_ppm=+9": total}
    )

    assert _value(readings["decoy mz_shift_ppm=+9 at_4_or_above"]) == (12.5, 8)
    assert metrics.DECOY_LABELS["at_4_or_above"]


def test_g13_and_g14_read_the_rows_records_and_none_without_them():
    rows = [_row("p1"), _row("p2"), _row("p3"), _row("p4", tier="candidate")]
    provenance = {
        "p1": {
            "predicted_envelope": {
                "lines": [{"state": "found"}, {"state": "absent"}, {"state": "absent"}]
            },
            "peak_separation": {"separated": False},
        },
        "p2": {
            "predicted_envelope": {"lines": [{"state": "untestable"}]},
            "peak_separation": {"separated": False},
            "cross_channel": {"separated_through": ["[M+NO3]-"]},
        },
        "p3": {},
        "p4": {
            "predicted_envelope": {"lines": [{"state": "absent"}]},
            "peak_separation": {"separated": False},
        },
    }
    frame = _frame(rows, provenance=provenance)
    assert _value(metrics.g13_absent_lines(frame)) == (1, 2)
    assert _value(metrics.g14_unseparated(frame)) == (1, 2)
    older = _frame(rows, provenance={"p1": {}, "p2": {}, "p3": {}})
    assert _value(metrics.g13_absent_lines(older)) == (None, 0)
    assert _value(metrics.g14_unseparated(older)) == (None, 0)


def test_the_provenance_columns_of_a_failed_read_are_all_none():
    assert set(metrics.provenance_columns(None).values()) == {None}


# --- The decoy arms -----------------------------------------------------------


def test_the_decoy_arms_join_the_card_as_readings_of_their_own():
    total = {
        "samples": 2,
        "control": {"rows": 40, "rows_lt_350": 30, "rows_ge_350": 10},
        "decoy": {"rows": 4, "on_control_reagent": 1, "new_ion": 2},
        **compare_runs.decoy_rates(
            {
                "rows": 40,
                "rows_lt_350": 30,
                "rows_ge_350": 10,
                "intensity": 1000.0,
                "intensity_lt_350": 800.0,
                "intensity_ge_350": 200.0,
            },
            {
                "rows": 4,
                "rows_lt_350": 3,
                "rows_ge_350": 1,
                "intensity": 50.0,
                "intensity_lt_350": 40.0,
                "intensity_ge_350": 10.0,
                "new_ion": 2,
            },
        ),
    }
    readings = metrics.card_readings(
        _frame([_row("p1")]), decoy_arms={"swap_adducts": total}
    )
    assert _value(readings["decoy swap_adducts held"]) == (10.0, 40)
    assert _value(readings["decoy swap_adducts held_lt_350"]) == (10.0, 30)
    assert _value(readings["decoy swap_adducts held_ge_350"]) == (10.0, 10)
    assert _value(readings["decoy swap_adducts held_intensity"]) == (5.0, 40)
    assert _value(readings["decoy swap_adducts new_ion"]) == (5.0, 40)
    assert _value(readings["decoy swap_adducts on_control_reagent"]) == (1, 4)


def test_the_card_reads_each_variant_of_a_metric_on_its_own_row():
    mine = [
        _row("p1", assigned_formula="HNO3", source="database"),
        _row("p2", role="reagent", tier="unassigned"),
        _row("p3", tier="candidate"),
        _row("p4", tier="candidate", assigned_formula="C11H16O6"),
        _row("p5", assigned_formula="C10H14O4", ionization_mechanism_id="no3"),
    ]
    theirs = [
        _row("p1"),
        _row("p2", role="reagent", tier="unassigned", ion_formula=None),
        _row("p3", role="iso_child"),
        _row("p4"),
        _row("p5"),
    ]
    frame = pd.concat(
        [_frame(mine, theirs), _frame([_row("q1")], sample="s2")], ignore_index=True
    )
    readings = metrics.card_readings(frame)
    assert readings["samples"]["value"] == 2
    assert readings["reference_samples"]["value"] == 1
    assert readings["G3_carbon_free"]["value"] == 1
    assert readings["G3_carbon_free_untargeted"]["value"] == 0
    assert readings["G4"]["n"] == 1 and readings["G4a"]["n"] == 0
    assert readings["G6"]["value"] == 1 and readings["G6_assigned"]["value"] == 0
    assert readings["G11"]["value"] == 0.0 and readings["G11_committed"]["value"] > 0


def test_every_metric_of_the_table_is_on_the_card_in_its_order():
    readings = metrics.card_readings(_frame([_row("p1")], [_row("p1")]), skipped=2)
    assert list(readings) == [key for key, _, _ in metrics.METRICS]
    assert readings["samples_skipped"] == {"value": 2, "n": None}


def test_a_column_no_sample_was_read_on_reads_nothing_but_its_skips():
    readings = metrics.unread_readings(3)
    assert list(readings) == [key for key, _, _ in metrics.METRICS]
    assert readings["samples"]["value"] == 0
    assert readings["samples_skipped"]["value"] == 3
    assert {
        one["value"] for k, one in readings.items() if not k.startswith("samples")
    } == {None}


# --- An authors' list -----------------------------------------------------------


def _keys(*formulas):
    return frozenset(compare_runs.truth_key(f) for f in formulas)


def test_a_list_s_formulas_are_keyed_by_element_counts_as_the_ledgers_are():
    # Hill order, another order, a count of one written out, a zero count and
    # spaces: one formula, keyed as the ledgers' formulas are.
    spellings = ("C10H15NO8", "NO8C10H15", "C10H15N1O8", "C10H15NO8S0", "C10 H15 N O8")
    assert {compare_runs.truth_key(f) for f in spellings} == {
        compare_runs.canonical("C10H15NO8")
    }
    for bad in ("C10H15NO8-", "[C10H15NO8+NO3]-", "[13C]C9H16O3", "c10h16o3", "S0"):
        assert compare_runs.truth_key(bad) is None
    for bad in ("", None, 42):
        assert compare_runs.truth_key(bad) is None


def test_a_list_is_its_distinct_formulas_and_refuses_anything_else():
    assert compare_runs.truth_formulas(["C10H16O3", "O3C10H16", "C7H9NO5"], "t") == (
        sorted({compare_runs.canonical("C10H16O3"), compare_runs.canonical("C7H9NO5")})
    )
    for bad in ([], "C10H16O3", {"C10H16O3": 1}):
        with pytest.raises(SystemExit, match="a list of neutral formulas"):
            compare_runs.truth_formulas(bad, "t")
    # An entry that is not one is named by its place, never by its text.
    with pytest.raises(SystemExit) as raised:
        compare_runs.truth_formulas(["C10H16O3", "HNO3NO3-"], "t")
    assert "t[1]" in str(raised.value) and "NO3-" not in str(raised.value)


def test_both_engines_are_read_against_the_list_by_distinct_formulas():
    mine = [
        _row("p1", assigned_formula="C10H16O3"),
        _row("p2", assigned_formula="C7H10O4"),
        _row("p3", assigned_formula="C5H8O4", tier="candidate"),
        # Off the list: an assigned formula the authors did not publish.
        _row("p4", assigned_formula="C9H14O4"),
        # An isotopologue row is no monoisotopic formula.
        _row("p5", role="iso_child", assigned_formula="C8H12O4"),
    ]
    theirs = [
        # The reference's own tier, in any case, not the tier the store gave it.
        _row("p1", assigned_formula="H16C10O3", engine_tier="Assigned"),
        _row("p2", assigned_formula="C7H10O4", engine_tier="candidate"),
        _row("p6", mz=200.0, assigned_formula="C6H8O5", engine_tier="assigned"),
    ]
    frame = pd.concat(
        [
            _frame(mine, theirs),
            # A second sample, where each engine assigns a formula again: one
            # formula, counted once. The reference's C10H16O3 is p1's alone.
            _frame(
                [_row("q1", assigned_formula="C7H10O4")],
                [_row("q1", assigned_formula="C6H8O5", engine_tier="assigned")],
                sample="s2",
            ),
        ],
        ignore_index=True,
    )
    truth = _keys("C10H16O3", "C7H10O4", "C5H8O4", "C8H12O4")

    readings = metrics.truth_readings([(frame, truth)])

    assert _value(readings["list_formulas"]) == (4, None)
    # Engine A assigns C10H16O3, C7H10O4 and C9H14O4; C5H8O4 at candidate.
    assert _value(readings["list_held"]) == (66.7, 3)
    assert _value(readings["list_found"]) == (50.0, 4)
    assert _value(readings["list_found_candidate"]) == (75.0, 4)
    # The reference assigns C10H16O3 and C6H8O5; C7H10O4 at its candidate.
    assert _value(readings["ref_list_held"]) == (50.0, 2)
    assert _value(readings["ref_list_found"]) == (25.0, 4)
    assert _value(readings["ref_list_found_candidate"]) == (50.0, 4)


def test_the_pooled_column_adds_each_set_s_counts_against_its_own_list():
    one = _frame(
        [_row("p1", assigned_formula="C10H16O3")],
        [_row("p1", assigned_formula="C10H16O3", engine_tier="assigned")],
    )
    two = _frame(
        [
            _row("q1", assigned_formula="C5H8O4"),
            _row("q2", assigned_formula="C10H16O3"),
        ],
        sample="s2",
    )

    readings = metrics.truth_readings(
        [(one, _keys("C10H16O3", "C7H10O4")), (two, _keys("C5H8O4"))]
    )

    assert _value(readings["list_formulas"]) == (3, None)
    # The second set's C10H16O3 is on the first set's list, not on its own.
    assert _value(readings["list_held"]) == (66.7, 3)
    assert _value(readings["list_found"]) == (66.7, 3)
    # Only the first set carries a reference run: the reference is read
    # against its list alone, and with no such set not at all.
    assert _value(readings["ref_list_found"]) == (50.0, 2)
    alone = metrics.truth_readings([(two, _keys("C5H8O4"))])
    assert _value(alone["ref_list_held"]) == (None, None)
    assert _value(alone["list_found"]) == (100.0, 1)


def test_a_list_s_rows_follow_the_table_and_an_unread_column_keeps_its_size():
    frame = _frame([_row("p1")], [_row("p1")])
    readings = metrics.card_readings(frame, truth=[(frame, _keys("C6H10O4"))])
    assert list(readings) == [key for key, _, _ in metrics.METRICS] + [
        key for key, _, _ in metrics.TRUTH_METRICS
    ]
    assert readings["list_found"] == {"value": 100.0, "n": 1}

    unread = metrics.unread_readings(2, _keys("C6H10O4", "C7H10O4"))
    assert unread["list_formulas"] == {"value": 2, "n": None}
    assert unread["list_found"] == {"value": None, "n": None}
    assert "list_formulas" not in metrics.unread_readings(2)


def test_a_list_s_words_are_the_card_s_own():
    for text in [compare_runs.TRUTH_NOTE] + [
        label for _, label, _ in metrics.TRUTH_METRICS
    ]:
        assert compare_runs._words(text) == []


# --- The gate ------------------------------------------------------------------


THRESHOLDS = {
    "format": 1,
    "stage": "stage2",
    "stages": ["stage1", "stage2", "stage3"],
    "not_gated_sets": ["J"],
    "targets": {
        "G1": {
            "instruments": ["orbitrap"],
            "stage1": {"max": 45},
            "stage2": {"max": 20},
            "stage3": {"max": 20, "reading": True},
        },
        "G2_formula": {
            "stage1": {"min": 80},
            "stage2": {"min": 85},
            "sets": {"B": {"stage1": {"min": 70}, "stage2": {"min": 75}}},
        },
        "G5": {"stage1": {"max": 0}},
        "G6": {"stage1": {"max": 10}, "stage2": None},
        "G9": {"stage3": {"max": 2}},
        "MAD": {"only_sets": ["A"], "stage1": {"max": 0.35}},
    },
}


def _at(stage):
    return {**THRESHOLDS, "stage": stage}


def test_a_thresholds_file_of_another_format_is_refused(tmp_path):
    path = tmp_path / "thresholds.json"
    path.write_text(json.dumps({**THRESHOLDS, "format": 2}))
    with pytest.raises(SystemExit, match="format"):
        compare_runs.load_thresholds(path)
    path.write_text(json.dumps({**THRESHOLDS, "stage": "stage9"}))
    with pytest.raises(SystemExit, match="stage9"):
        compare_runs.load_thresholds(path)


def test_a_value_meets_a_bound_up_to_it():
    assert compare_runs.meets(20, {"max": 20})
    assert not compare_runs.meets(20.1, {"max": 20})
    assert compare_runs.meets(85, {"min": 85})
    assert not compare_runs.meets(84.9, {"min": 85})


def test_a_stage_a_metric_does_not_name_holds_the_one_before():
    assert compare_runs.target_for(THRESHOLDS, "G5", "A", "orbitrap")["max"] == 0
    assert compare_runs.target_for(THRESHOLDS, "G1", "A", "orbitrap")["max"] == 20
    assert compare_runs.target_for(_at("stage1"), "G1", "A", "orbitrap")["max"] == 45


def test_a_null_stage_ends_a_target_and_a_later_stage_starts_one():
    assert compare_runs.target_for(_at("stage1"), "G6", "A", "orbitrap")["max"] == 10
    assert compare_runs.target_for(THRESHOLDS, "G6", "A", "orbitrap") is None
    assert compare_runs.target_for(THRESHOLDS, "G9", "A", "orbitrap") is None
    assert compare_runs.target_for(_at("stage3"), "G9", "A", "orbitrap")["max"] == 2


def test_a_set_s_own_target_replaces_the_metric_s_for_its_stage():
    assert compare_runs.target_for(_at("stage1"), "G2_formula", "B", None)["min"] == 70
    assert compare_runs.target_for(THRESHOLDS, "G2_formula", "B", None)["min"] == 75
    assert compare_runs.target_for(THRESHOLDS, "G2_formula", "A", None)["min"] == 85


def test_a_target_holds_for_the_sets_it_names_and_the_metric_states_it():
    assert compare_runs.target_for(THRESHOLDS, "MAD", "A", "orbitrap")["max"] == 0.35
    assert compare_runs.target_for(THRESHOLDS, "MAD", "B", "orbitrap") is None
    # With no set, the metric's own target: what the overview's column states.
    assert compare_runs.target_for(THRESHOLDS, "MAD", None, None)["max"] == 0.35
    assert compare_runs.target_for(THRESHOLDS, "G1", None, None)["max"] == 20


def test_no_target_on_another_instrument_or_a_set_not_gated():
    assert compare_runs.target_for(THRESHOLDS, "G1", "E", "tof") is None
    assert compare_runs.target_for(THRESHOLDS, "G5", "J", "orbitrap") is None


def test_a_target_marked_reading_is_shown_and_not_counted():
    target = compare_runs.target_for(_at("stage3"), "G1", "A", "orbitrap")
    assert target["reading"] is True
    card = _card({"A": {"G1": (32.0, 100), "G5": (0, 10)}})
    gate = compare_runs.judge(card, _at("stage3"))
    assert gate["verdict"] == "pass"
    assert gate["marks"]["A"]["G1"] == "miss"
    assert gate["met"] == 1


def _card(columns, instrument="orbitrap"):
    return {
        "format": compare_runs.CARD_FORMAT,
        "engines": {"a": "mascope", "b": "peaky"},
        "columns": {
            name: {
                "instrument": instrument,
                "readings": {
                    key: metrics.reading(value, n)
                    for key, (value, n) in readings.items()
                },
            }
            for name, readings in columns.items()
        },
        "gate": None,
        "thresholds": None,
        "baseline": None,
    }


def test_the_gate_line_names_every_miss_and_every_reading_not_read():
    card = _card(
        {
            "A": {"G1": (12.0, 100), "G5": (0, 10), "G2_formula": (90.0, 50)},
            "B": {"G1": (32.0, 100), "G5": (3, 10), "G2_formula": (None, 0)},
            "J": {"G1": (99.0, 100)},
            compare_runs.POOLED: {"G1": (50.0, 300)},
        }
    )
    gate = compare_runs.judge(card, THRESHOLDS)
    assert gate["verdict"] == "fail"
    assert compare_runs.gate_line(gate) == (
        "GATE stage2: FAIL - 2 of 5 gated readings missed: B G1 32 (<= 20); "
        "B G5 3 (<= 0); 1 not read (B G2_formula)"
    )
    passing = compare_runs.judge(_card({"A": {}}), THRESHOLDS)
    assert compare_runs.gate_line(passing) == "GATE stage2: PASS - 0 gated readings met"


def test_a_set_without_a_reference_run_is_not_marked_on_the_reference_s_metrics():
    card = _card(
        {
            "A": {"reference_samples": (1, None), "G5": (None, 0)},
            "G": {"reference_samples": (0, None), "G5": (None, 0), "G9": (None, 0)},
        }
    )
    gate = compare_runs.judge(card, _at("stage3"))
    assert [(m["set"], m["metric"]) for m in gate["not_read"]] == [
        ("A", "G5"),
        ("G", "G9"),
    ]
    assert "G5" not in gate["marks"]["G"]


def test_a_failed_detail_read_leaves_a_met_detail_reading_not_read():
    thresholds = {
        **THRESHOLDS,
        "stage": "stage3",
        "targets": {key: {"stage1": {"max": 0}} for key in ("G7", "G13", "G14", "G12")}
        | {"reasons": {"stage1": {"min": 100}}},
    }
    readings = {
        "detail_failed": (1, 40),
        "reasons": (100.0, 39),
        "G13": (0, 30),
        "G14": (0, 30),
        "G7": (0, 30),
        "G12": (0, 30),
    }
    marks = compare_runs.judge(_card({"A": readings}), thresholds)["marks"]["A"]
    assert marks == {
        "reasons": "not read",
        "G13": "not read",
        "G14": "not read",
        # G7 counts a row it could not read against itself; G12 reads none.
        "G7": "ok",
        "G12": "ok",
    }
    # A miss on the rows read is a miss, whatever the rows left out hold.
    readings |= {"reasons": (97.4, 39), "G13": (2, 30)}
    gate = compare_runs.judge(_card({"A": readings}), thresholds)
    assert gate["marks"]["A"]["reasons"] == "miss" == gate["marks"]["A"]["G13"]
    # With every read answered, a met reading is met.
    readings |= {"detail_failed": (0, 40), "reasons": (100.0, 40), "G13": (0, 30)}
    marks = compare_runs.judge(_card({"A": readings}), thresholds)["marks"]["A"]
    assert set(marks.values()) == {"ok"}


def test_a_set_no_sample_was_read_on_fails_only_where_a_target_gates_it():
    unread = {"samples": (0, None), "G1": (None, None), "G5": (None, None)}
    gate = compare_runs.judge(_card({"A": unread}), _at("stage3"))
    assert gate["unread_sets"] == ["A"] and gate["verdict"] == "fail"
    assert gate["marks"]["A"] == {"G1": "not read", "G5": "not read"}
    # G1 is a reading at stage 3: shown, and failing nothing on its own.
    only_g1 = {"samples": (0, None), "G1": (None, None)}
    gate = compare_runs.judge(_card({"A": only_g1}), _at("stage3"))
    assert gate["unread_sets"] == [] and gate["verdict"] == "pass"
    assert gate["marks"]["A"] == {"G1": "not read"}


def test_the_gate_on_one_set_alone():
    card = _card({"A": {"G5": (0, 10)}, "B": {"G5": (3, 10), "G1": (12.0, 100)}})
    gate = compare_runs.judge(card, THRESHOLDS)
    assert compare_runs.gate_line(compare_runs.for_set(gate, "A")) == (
        "GATE stage2: PASS - 1 gated readings met"
    )
    assert compare_runs.for_set(gate, "B")["verdict"] == "fail"


# ------------------------------------------- the gate against a baseline

#: Develop's card: B misses G1 and G5, A meets everything.
DEVELOP_CARD = {
    "A": {"G1": (12.0, 100), "G5": (0, 10), "G2_formula": (90.0, 50)},
    "B": {"G1": (32.0, 100), "G5": (3, 10), "G2_formula": (70.0, 50)},
}


def _against(columns, baseline=DEVELOP_CARD, thresholds=THRESHOLDS):
    return compare_runs.judge_against(_card(columns), _card(baseline), thresholds)


def test_develop_s_own_misses_pass_the_ref_and_are_listed():
    gate = _against(DEVELOP_CARD)
    assert gate["verdict"] == "pass" and gate["absolute"] == "fail"
    assert gate["against"]["new"] == [] and gate["against"]["fixed"] == []
    assert [(m["set"], m["metric"]) for m in gate["against"]["develops"]] == [
        ("B", "G1"),
        ("B", "G5"),
        ("B", "G2_formula"),
    ]
    assert compare_runs.gate_line(gate) == (
        "GATE stage2: PASS for this ref - 0 new, 3 develop's (B G1 32 (<= 20); "
        "B G5 3 (<= 0); B G2_formula 70 (>= 75)), 0 fixed"
    )


def test_a_miss_develop_holds_is_new_and_fails_the_ref():
    columns = {**DEVELOP_CARD, "A": {**DEVELOP_CARD["A"], "G5": (1, 10)}}
    gate = _against(columns)
    assert gate["verdict"] == "fail"
    assert [(m["set"], m["metric"], m["was"]) for m in gate["against"]["new"]] == [
        ("A", "G5", 0)
    ]
    assert len(gate["against"]["develops"]) == 3
    assert compare_runs.gate_line(gate).startswith(
        "GATE stage2: FAIL for this ref - 1 new (A G5 1 (<= 0), was 0), 3 develop's ("
    )


def test_a_miss_develop_has_that_the_ref_meets_is_fixed():
    columns = {**DEVELOP_CARD, "B": {**DEVELOP_CARD["B"], "G1": (14.0, 100)}}
    gate = _against(columns)
    assert gate["verdict"] == "pass"
    assert gate["against"]["fixed"] == [
        {
            "set": "B",
            "metric": "G1",
            "value": 14.0,
            "target": {"max": 20, "reading": False, "stage": "stage2"},
            "was": 32.0,
        }
    ]
    assert compare_runs.gate_line(gate).endswith(
        "2 develop's (B G5 3 (<= 0); B G2_formula 70 (>= 75)), "
        "1 fixed (B G1 14 (<= 20), was 32)"
    )


def test_a_miss_develop_has_that_the_ref_leaves_unread_is_not_fixed():
    columns = {**DEVELOP_CARD, "B": {**DEVELOP_CARD["B"], "G1": (None, 0)}}
    gate = _against(columns)
    assert gate["marks"]["B"]["G1"] == "not read"
    assert gate["against"]["fixed"] == []
    assert [m["metric"] for m in gate["against"]["develops"]] == ["G5", "G2_formula"]
    # The reading shows on the line's not-read tail, and nowhere else.
    assert compare_runs.gate_line(gate) == (
        "GATE stage2: PASS for this ref - 0 new, 2 develop's (B G5 3 (<= 0); "
        "B G2_formula 70 (>= 75)), 0 fixed; 1 not read (B G1)"
    )


def test_a_shared_miss_made_worse_beyond_the_noise_is_new():
    # G1 has a max: higher is worse. G2_formula a min: lower is worse.
    columns = {
        **DEVELOP_CARD,
        "B": {"G1": (40.0, 100), "G5": (3, 10), "G2_formula": (60.0, 50)},
    }
    gate = _against(columns)
    assert gate["verdict"] == "fail"
    assert [(m["metric"], m["worse"]) for m in gate["against"]["new"]] == [
        ("G1", True),
        ("G2_formula", True),
    ]
    assert "B G1 40 (<= 20), worse than 32" in compare_runs.gate_line(gate)
    # Within the metric's noise, the miss is develop's still.
    noisy = {**THRESHOLDS, "noise": {"G1": 10, "G2_formula": 10}}
    gate = _against(columns, thresholds=noisy)
    assert gate["verdict"] == "pass" and len(gate["against"]["develops"]) == 3
    # A shared miss the ref makes better is develop's, not new.
    better = {**DEVELOP_CARD, "B": {**DEVELOP_CARD["B"], "G1": (25.0, 100)}}
    gate = _against(better)
    assert gate["verdict"] == "pass"
    assert ("B", "G1", 32.0) in [
        (m["set"], m["metric"], m["was"]) for m in gate["against"]["develops"]
    ]


def test_a_miss_on_a_set_or_reading_the_baseline_lacks_is_new():
    columns = {**DEVELOP_CARD, "C": {"G5": (2, 10)}}
    gate = _against(columns)
    assert gate["verdict"] == "fail"
    assert "C G5 2 (<= 0), not read on the baseline" in compare_runs.gate_line(gate)


def test_a_set_no_sample_was_read_on_fails_the_ref_against_a_baseline():
    columns = {**DEVELOP_CARD, "A": {"samples": (0, None), "G5": (None, None)}}
    gate = _against(columns)
    assert gate["against"]["new"] == [] and gate["verdict"] == "fail"
    assert compare_runs.gate_line(gate).startswith(
        "GATE stage2: FAIL for this ref - no sample read on A; 0 new"
    )


def test_the_gate_against_a_baseline_on_one_set_alone():
    columns = {**DEVELOP_CARD, "A": {**DEVELOP_CARD["A"], "G5": (1, 10)}}
    gate = _against(columns)
    a, b = compare_runs.for_set(gate, "A"), compare_runs.for_set(gate, "B")
    assert a["verdict"] == "fail" and a["absolute"] == "fail"
    assert b["verdict"] == "pass" and b["absolute"] == "fail"
    assert [m["set"] for m in b["against"]["develops"]] == ["B", "B", "B"]
    assert b["against"]["new"] == [] and a["against"]["develops"] == []
    # Alone, a set that misses nothing passes either way.
    clean = compare_runs.for_set(_against(DEVELOP_CARD), "A")
    assert clean["absolute"] == "pass" == clean["verdict"]
    assert compare_runs.gate_line(b).startswith(
        "GATE stage2: PASS for this ref - 0 new, 3 develop's"
    )
    # A set no sample was read on fails alone too, with no new miss.
    unread = {**DEVELOP_CARD, "A": {"samples": (0, None), "G5": (None, None)}}
    alone = compare_runs.for_set(_against(unread), "A")
    assert alone["against"]["new"] == []
    assert alone["verdict"] == "fail" == alone["absolute"]


def test_the_shipped_noise_names_every_metric_it_gates():
    shipped = compare_runs.load_thresholds(compare_runs.DEFAULT_THRESHOLDS)
    assert set(shipped["noise"]) == set(shipped["targets"])
    # Two cards of develop at one commit read alike on every gated metric.
    assert set(shipped["noise"].values()) == {0}


def test_the_shipped_thresholds_hold_the_log_s_targets():
    shipped = compare_runs.load_thresholds(compare_runs.DEFAULT_THRESHOLDS)
    assert shipped["stage"] == "stage3"
    at = lambda stage: {**shipped, "stage": stage}  # noqa: E731
    target = compare_runs.target_for
    assert target(at("stage1"), "G1", "A", "orbitrap")["max"] == 45
    assert target(at("stage1"), "G2_formula", "B", "orbitrap")["min"] == 70
    assert target(at("stage2"), "G2_formula", "B", "orbitrap")["min"] == 85
    assert target(at("stage2"), "G6_assigned", "E", "tof")["max"] == 5
    assert target(at("stage1"), "MAD", "E", "tof") is None
    # The log states G1, G2 and the mass error for A to D and C2 alone.
    assert target(at("stage1"), "MAD", "C2", "orbitrap")["max"] == 0.35
    assert target(at("stage1"), "MAD", "K", "orbitrap") is None
    assert target(at("stage1"), "G1", "G", "orbitrap") is None
    # G4 is read where the reference's reagent rows are this engine's claim.
    for name in ("A", "B", "D", "E", "F1"):
        assert target(at("stage1"), "G4", name, "tof")["min"] == 90
        assert target(shipped, "G4a", name, "tof")["min"] == 100
    for name in ("C", "C2", "F2", "G"):
        assert target(shipped, "G4", name, "orbitrap") is None
        assert target(shipped, "G4a", name, "orbitrap") is None
    assert target(shipped, "G1", "A", "orbitrap")["reading"] is True
    for key in ("G9", "G10", "G11", "G12", "G13", "G14"):
        assert target(at("stage2"), key, "A", "orbitrap") is None
        assert target(shipped, key, "A", "orbitrap") is not None
    assert target(shipped, "G9", "J", "tof") is None
    assert target(at("stage4"), "G1", "A", "orbitrap") == {
        "max": 15,
        "reading": True,
        "stage": "stage4",
    }


def test_the_overview_marks_every_miss_and_states_the_target():
    card = _card({"A": {"G1": (12.0, 100)}, "B": {"G1": (32.0, 100)}})
    card["thresholds"] = THRESHOLDS
    card["gate"] = compare_runs.judge(card, THRESHOLDS)
    text = compare_runs.card_markdown(card)
    assert (
        "| G1 assigned rows the reference does not confirm | 12 | 32 MISS | <= 20 |"
        in text
    )
    one = compare_runs.card_markdown(card, ["B"])
    assert (
        "| G1 assigned rows the reference does not confirm | 32 of 100 | <= 20 | miss |"
        in one
    )


def test_a_value_is_printed_in_its_unit():
    assert compare_runs.format_value(3.0, "count") == "3"
    assert compare_runs.format_value(0.2, "ppm") == "0.200"
    assert compare_runs.format_value(6.25, "%") == "6.25"
    assert compare_runs.format_value(None, "%") == "-"


# --- The diff --------------------------------------------------------------------


def test_two_cards_differing_in_one_reading_diff_to_that_row():
    readings = {"G1": (6.2, 999), "G5": (0, 10), "MAD": (0.2, 900)}
    old = _card({"A": readings, "B": readings})
    new = _card({"A": readings, "B": {**readings, "MAD": (0.21, 900)}})
    diff = compare_runs.diff_cards(json.loads(json.dumps(old)), new)
    assert diff == {
        "compared": 6,
        "by_column": {"A": 3, "B": 3},
        "moved": [
            {
                "set": "B",
                "metric": "MAD",
                "was": {"value": 0.2, "n": 900},
                "now": {"value": 0.21, "n": 900},
            }
        ],
        "only_baseline": [],
        "only_card": [],
        "arms_not_run": [],
    }


def test_a_reading_one_column_holds_has_moved_and_a_column_one_card_holds_is_named():
    old = _card({"A": {"G1": (6.2, 999)}, "C": {"G1": (1.0, 10)}})
    new = _card({"A": {"G1": (6.2, 999), "G14": (0, 50)}, "D": {"G1": (3.0, 9)}})
    diff = compare_runs.diff_cards(old, new)
    assert [(m["set"], m["metric"], m["was"], m["now"]) for m in diff["moved"]] == [
        ("A", "G14", None, {"value": 0, "n": 50}),
    ]
    assert diff["compared"] == 2 and diff["by_column"] == {"A": 2}
    assert diff["only_baseline"] == ["C"] and diff["only_card"] == ["D"]
    assert compare_runs.baseline_line(diff) == (
        "1 of 2 readings moved; not on the baseline: D; on the baseline only: C"
    )


def test_a_baseline_of_another_format_is_refused():
    with pytest.raises(SystemExit):
        compare_runs.diff_cards({"format": 99}, _card({}))


def test_the_card_shows_the_rows_that_moved():
    card = _card({"A": {"G1": (6.2, 999)}})
    card["baseline"] = compare_runs.diff_cards(_card({"A": {"G1": (6.1, 999)}}), card)
    text = compare_runs.card_markdown(card)
    assert "1 of 1 readings moved." in text
    assert (
        "| A | G1 assigned rows the reference does not confirm | 6.1 of 999 | 6.2 of 999 |"
        in text
    )


# --- The scrub -------------------------------------------------------------------


def test_a_card_with_a_planted_sample_id_is_refused_naming_it():
    card = _card({"A": {"G1": (6.2, 999)}})
    card["engines"]["b"] = "peaky kSQveDMAGALUSBOY"
    tokens = compare_runs.deny_tokens(
        ["Acquisitions Instrument9"], ["kSQveDMAGALUSBOY"], ["A"]
    )
    files = compare_runs.card_files(card)
    with pytest.raises(compare_runs.CardRefused) as refused:
        compare_runs.scrub(files, tokens)
    assert refused.value.token == "kSQveDMAGALUSBOY"


def test_a_word_of_a_name_alone_refuses_the_card():
    tokens = compare_runs.deny_tokens(["Acquisitions Instrument9"], [], ["A"])
    with pytest.raises(compare_runs.CardRefused) as refused:
        compare_runs.scrub({"card.md": "read on INSTRUMENT9 today"}, tokens)
    assert refused.value.token == "Instrument9"
    assert refused.value.where == "card.md"


def test_the_card_s_own_words_the_set_letters_and_numbers_do_not_refuse_it():
    tokens = compare_runs.deny_tokens(
        ["Uronium No RI new", "B", "2026", "assigned peaks"], ["xYz"], ["A", "B"]
    )
    # The name whole stays denied; its words the card prints of its own do not.
    assert "Uronium No RI new" in tokens and "Uronium" in tokens
    assert "new" not in tokens and "B" not in tokens and "2026" not in tokens
    assert "RI" not in tokens and "No" not in tokens
    # A name made of the card's own words alone names nothing.
    assert "assigned peaks" not in tokens and "assigned" not in tokens
    # The card prints these too: a mark, the unread set's note, the diff's.
    for word in ("Miss", "Sample", "Only"):
        assert compare_runs.deny_tokens([f"{word} Lab"], []) == [f"{word} Lab", "Lab"]
    card = _card({"A": {"G1": (2026.0, 3)}, "B": {"G1": (6.2, 999)}})
    card["columns"]["A"]["readings"]["decoy swap_adducts new_ion"] = metrics.reading(
        1.0, 2
    )
    compare_runs.scrub(compare_runs.card_files(card), tokens)


def test_the_engines_names_are_the_card_s_own_and_a_listed_name_is_denied_whole():
    tokens = compare_runs.deny_tokens(
        ["Mascope demo", "peaky"],
        [],
        strict=["Orbitrap Exploris", "peaky"],
        own_words=["mascope", "peaky"],
    )
    # A workspace word the card prints as an engine's name refuses nothing ...
    assert "Mascope" not in tokens and "demo" in tokens and "Mascope demo" in tokens
    # ... but a name listed as denied is denied whole, whatever it is made of,
    # and its words the card prints of its own are not.
    assert "peaky" in tokens and "Orbitrap Exploris" in tokens
    assert "Orbitrap" not in tokens and "Exploris" in tokens
    # A set's letter is the card's to print where a name holds it; a set the
    # deny list names is refused before anything is read, never let through.
    assert "C2+" not in compare_runs.deny_tokens(["C2+"], [], ["C2+"])
    assert "C2" in compare_runs.deny_tokens([], [], ["C2"], strict=["C2"])


def test_a_three_character_word_of_a_name_alone_refuses_the_card():
    tokens = compare_runs.deny_tokens(["Lab XYZ 2"], [], ["A"])
    with pytest.raises(compare_runs.CardRefused) as refused:
        compare_runs.scrub({"card.md": "read on xyz"}, tokens)
    assert refused.value.token == "XYZ"


def test_a_host_is_denied_whole_even_as_an_address_of_numbers():
    assert compare_runs.deny_tokens([], [], hosts=["10.0.0.5"]) == ["10.0.0.5"]
    # Numbers the card prints of its own are no part of a host's name.
    assert compare_runs.deny_tokens(
        [], [], hosts=["10.0.0.5"], own_words=["10", "0", "5"]
    ) == ["10.0.0.5"]
    with pytest.raises(compare_runs.CardRefused):
        compare_runs.scrub({"card.md": "on 10.0.0.5:8090"}, ["10.0.0.5"])
    # A host the set letters spell is still a host.
    assert compare_runs.deny_tokens([], [], ["Hr"], hosts=["Hr"]) == ["Hr"]
    # A host made of the card's own words alone names nothing, its words aside.
    tokens = compare_runs.deny_tokens(
        [], [], hosts=["mascope", "box.lan"], own_words=["mascope"]
    )
    assert tokens == ["box.lan", "box", "lan"]


def test_the_card_s_json_keys_and_its_thresholds_are_its_own_words():
    card = _card({"A": {"G1": (6.2, 999)}})
    card["thresholds"] = compare_runs.load_thresholds(compare_runs.DEFAULT_THRESHOLDS)
    card["gate"] = compare_runs.judge(card, card["thresholds"])
    card["columns"]["A"]["engine_versions"] = {"a": ["0.6.0-zeta"], "b": []}
    card["baseline"] = compare_runs.diff_cards(_card({"A": {"G1": (6.1, 999)}}), card)
    vocabulary = compare_runs.card_vocabulary(card)
    # The thresholds' prose and the JSON's keys, at any depth, in a list too.
    assert {"quality", "dev", "docs", "versions", "met", "unread"} <= vocabulary
    assert {"was", "now"} <= vocabulary
    # A value is not a key: the engines' versions are the server's.
    assert "zeta" not in vocabulary and "mascope" not in vocabulary
    tokens = compare_runs.deny_tokens(
        ["Air quality 2025", "dev"], [], ["A"], own_words=sorted(vocabulary)
    )
    assert tokens == ["Air quality 2025", "Air"]
    compare_runs.scrub(compare_runs.card_files(card), tokens)


def test_a_token_matches_whole_and_in_any_case():
    tokens = compare_runs.deny_tokens([], ["abc123"])
    compare_runs.scrub({"x": "xabc123 abc1234"}, tokens)
    with pytest.raises(compare_runs.CardRefused):
        compare_runs.scrub({"x": "(ABC123)"}, tokens)


def test_a_set_named_other_than_by_a_letter_is_refused(tmp_path):
    manifest = tmp_path / "sets.json"
    manifest.write_text(
        json.dumps({"sets": {"Spring batch": {"workspace": "w", "samples": ["s"]}}})
    )
    with pytest.raises(SystemExit, match="named by a letter"):
        compare_runs.load_sets(manifest)
    for name in ("Acme", "Kyo", "Abc1", "AB", "C22", "c2", "I++"):
        manifest.write_text(
            json.dumps({"sets": {name: {"workspace": "w", "samples": ["s"]}}})
        )
        with pytest.raises(SystemExit, match="named by a letter"):
            compare_runs.load_sets(manifest)
    for name in ("A", "C2", "F1", "Hr", "I+", "I-", "Hr+"):
        manifest.write_text(
            json.dumps({"sets": {name: {"workspace": "w", "samples": ["s"]}}})
        )
        assert compare_runs.load_sets(manifest)["sets"][name]["instrument"] == (
            "orbitrap"
        )


def test_the_deny_file_holds_a_name_a_line_and_comments(tmp_path):
    path = tmp_path / "deny.txt"
    path.write_text("# instruments\n\n  site X  \n   # a note\nOrbitrap 7\n")
    assert compare_runs.load_deny(path) == ["site X", "Orbitrap 7"]


def test_a_set_the_deny_list_names_is_refused():
    with pytest.raises(SystemExit, match="set Hr: the deny list names it"):
        compare_runs.refuse_denied_sets(["A", "Hr"], ["site X", " hr "])
    compare_runs.refuse_denied_sets(["A", "Hr"], ["site X", "Hr2"])


def test_the_hosts_are_the_deployment_s_and_this_machine_s(monkeypatch):
    monkeypatch.setattr(compare_runs.socket, "gethostname", lambda: "box")
    monkeypatch.setattr(compare_runs.socket, "getfqdn", lambda: "box.lan")
    assert compare_runs.host_names("https://server-a.example:8443/api") == [
        "box",
        "box.lan",
        "server-a.example",
    ]


# --- The command ------------------------------------------------------------------


class _Listing:
    def __init__(self, frame):
        self.frame = frame
        self.asked = []

    def list(self, **query):
        self.asked.append(query)
        return self.frame


class _Client:
    """The SDK reads the card makes, over canned runs, ledgers and details."""

    runs: dict = {}
    ledgers: dict = {}
    details: dict = {}
    batches: dict = {}

    def __init__(self, workspace):
        self.workspace = workspace
        self.peak_assignments = self
        self.ionization = _Listing(
            pd.DataFrame(
                {
                    "ionization_mechanism_id": list(NOTATION),
                    "ionization_mechanism": list(NOTATION.values()),
                }
            )
        )
        self.samples = _Listing(
            pd.DataFrame({"sample_item_id": self.batches.get(workspace, [])})
        )

    def list_runs(self, sample_id, include_decoys=False):
        runs = [
            run
            for run in self.runs[sample_id]
            if include_decoys or run.get("decoy") is None
        ]
        return pd.DataFrame(runs)

    def get(self, sample_id, run_id=None):
        found = self.ledgers[run_id]
        return None if found is None else pd.DataFrame(found)

    def detail(self, sample_id, row_id):
        found = self.details.get(row_id)
        if isinstance(found, Exception):
            raise found
        return {"provenance": found}


def _run(run_id, engine, version, created, config=None, decoy=None, status="completed"):
    return {
        "peak_assignment_run_id": run_id,
        "engine": engine,
        "engine_version": version,
        "status": status,
        "peak_assignment_run_utc_created": created,
        "config": config or {},
        "decoy": decoy,
        "error": None,
    }


@pytest.fixture
def served(monkeypatch, tmp_path):
    config = {
        "resolved_profile": {"profile": "NO3", "mz_precision_ppm": 3.0},
        "tiering": {"version": 13, "identification_level": {"level_rules_version": 1}},
        "search_scope": {"searched_peaks": 10, "limited": True},
        "peak_intensity_threshold": 60.0,
    }
    _Client.batches = {}
    _Client.runs = {
        "s1": [
            _run("ra1", "mascope", "0.6.0", "2026-11-10T10:00:00Z", config),
            _run("rb1", "peaky", "0.8.0", "2026-11-01T10:00:00Z"),
        ],
        "s2": [_run("ra2", "mascope", "0.6.0", "2026-11-10T10:00:00Z", config)],
    }
    seen_twice = {"corroboration_channels": 2}
    _Client.ledgers = {
        "ra1": [
            _row("p1", **seen_twice),
            _row("p2", assigned_formula="C2H4N2O", **seen_twice),
            _row("p1i", mz=101.0, role="iso_child", owner_peak_assignment_id="pa-p1"),
            # Under the run's intensity threshold: never searched.
            _unassigned("p3", mz=150.0, intensity=50.0),
            # Nitrate, the profile's own ion, parked as reagent.
            _row(
                "p4",
                mz=61.98837,
                intensity=1000.0,
                role="reagent",
                tier="unassigned",
                source=None,
            ),
        ],
        "rb1": [
            _row("p1", engine_tier="assigned"),
            _unassigned("p2"),
            _row("p3", mz=150.0, intensity=50.0, engine_tier="assigned"),
        ],
        "ra2": [
            _row("q1", **seen_twice),
            _row("q2", assigned_formula="C4H8O3S", **seen_twice),
        ],
    }
    _Client.details = {
        "pa-p1": {"tier_reasons": [{"rule": "corroborated"}]},
        "pa-p2": NotFoundError("no assignment pa-p2", status_code=404),
        "pa-p1i": {"tier_reasons": [{"rule": "isotopologue"}]},
        "pa-q1": {"tier_reasons": [{"rule": "on_a_list"}]},
        "pa-q2": {"tier_reasons": [{"rule": "second_line"}]},
    }
    monkeypatch.setattr(compare_runs, "MascopeClient", _Client)
    monkeypatch.setenv("MASCOPE_URL", "https://server-a.example")
    manifest = tmp_path / "sets.json"
    manifest.write_text(
        json.dumps(
            {
                "sets": {
                    "A": {"workspace": "Acquisitions Instrument9", "samples": ["s1"]},
                    "G": {
                        "workspace": "Chamber study",
                        "samples": ["s2"],
                        "sulfur_free": True,
                    },
                }
            }
        )
    )
    return manifest


def test_the_command_writes_a_card_per_set_and_one_pooled(served, tmp_path, capsys):
    out = tmp_path / "card"
    code = compare_runs.main(["--sets", str(served), "--card", str(out), "--gate"])
    assert code == 1
    assert sorted(p.name for p in out.iterdir()) == [
        "card.json",
        "card.md",
        "card_A.json",
        "card_A.md",
        "card_G.json",
        "card_G.md",
    ]
    card = json.loads((out / "card.json").read_text())
    assert list(card["columns"]) == ["A", "G", "pooled"]
    a, g = card["columns"]["A"], card["columns"]["G"]
    assert a["readings"]["reference_samples"]["value"] == 1
    assert g["readings"]["reference_samples"]["value"] == 0
    # p1 confirmed, p2 unconfirmed against the reference; G has none.
    assert a["readings"]["G1"] == {"value": 50.0, "n": 2}
    assert g["readings"]["G1"]["value"] is None
    # Every committed row's provenance was read, on both sets; p2's read
    # failed, and the reading says it counts one row fewer.
    assert a["readings"]["reasons"] == {"value": 100.0, "n": 2}
    assert a["readings"]["detail_failed"] == {"value": 1, "n": 3}
    assert card["columns"]["pooled"]["readings"]["reasons"] == {"value": 100.0, "n": 4}
    assert card["columns"]["pooled"]["readings"]["detail_failed"]["value"] == 1
    # So A's reasons meet their target on the rows read, which is not a pass.
    assert card["gate"]["marks"]["A"]["reasons"] == "not read"
    assert card["gate"]["marks"]["G"]["reasons"] == "ok"
    assert {"set": "A", "metric": "reasons"} in card["gate"]["not_read"]
    # The reference assigns p3, which the run's threshold kept from the search.
    assert a["readings"]["G5"] == {"value": 1, "n": 2}
    # The nitrate ion of the run's profile is parked.
    assert a["readings"]["G10"] == {"value": 100.0, "n": 1}
    assert a["engine_versions"] == {"a": ["0.6.0"], "b": ["0.8.0"]}
    assert a["tiering_rules"] == [13]
    # The level's rule set is named beside the tier's (step 5.8).
    assert a["level_rules"] == [1]
    assert card["columns"]["pooled"]["level_rules"] == [1]
    assert "tiering rule set 13; level rules 1" in (out / "card.md").read_text()
    # C2H4N2O is implausible on A; C4H8O3S only on G, which holds no sulfur.
    assert [(m["set"], m["metric"], m["value"]) for m in card["gate"]["missed"]] == [
        ("A", "G5", 1),
        ("A", "G9", 50.0),
        ("G", "G9", 50.0),
    ]
    assert card["gate"]["missed"][1]["target"] == {
        "max": 2,
        "reading": False,
        "stage": "stage3",
    }
    captured = capsys.readouterr()
    assert "GATE stage3: FAIL - 3 of" in captured.out
    assert "detail of pa-p2 not read" in captured.err
    assert "Instrument9" not in (out / "card.md").read_text()


def test_the_command_diffs_against_a_baseline(served, tmp_path, capsys):
    first, second = tmp_path / "first", tmp_path / "second"
    compare_runs.main(["--sets", str(served), "--card", str(first)])
    baseline = json.loads((first / "card.json").read_text())
    baseline["columns"]["A"]["readings"]["G1"]["value"] = 40.0
    (first / "edited.json").write_text(json.dumps(baseline))
    code = compare_runs.main(
        [
            "--sets",
            str(served),
            "--card",
            str(second),
            "--baseline",
            str(first / "edited.json"),
        ]
    )
    assert code == 0
    card = json.loads((second / "card.json").read_text())
    assert [(m["set"], m["metric"]) for m in card["baseline"]["moved"]] == [("A", "G1")]
    assert "BASELINE: 1 of" in capsys.readouterr().out
    assert "## Since the baseline" in (second / "card_A.md").read_text()
    # A set's card counts what it compared of its own column.
    own = json.loads((second / "card_A.json").read_text())["baseline"]
    count = len(card["columns"]["A"]["readings"])
    assert own["by_column"] == {"A": count} and own["compared"] == count
    assert f"1 of {count} readings moved." in (second / "card_A.md").read_text()


def test_the_gate_against_a_baseline_sets_the_exit_code(served, tmp_path, capsys):
    first = tmp_path / "first"
    # Alone, the card misses three gated readings: A G5, A G9 and G G9.
    assert (
        compare_runs.main(["--sets", str(served), "--card", str(first), "--gate"]) == 1
    )
    capsys.readouterr()

    def card_against(baseline, *extra):
        out = tmp_path / f"against-{len(list(tmp_path.iterdir()))}"
        argv = ["--sets", str(served), "--card", str(out), "--gate"]
        code = compare_runs.main(argv + ["--baseline", str(baseline), *extra])
        return code, json.loads((out / "card.json").read_text()), out

    # Against itself, every miss is develop's: this ref passes.
    code, card, out = card_against(first / "card.json")
    assert code == 0 and card["gate"]["verdict"] == "pass"
    assert card["gate"]["absolute"] == "fail"
    line = "GATE stage3: PASS for this ref - 0 new, 3 develop's (A G5 1 (<= 0); "
    assert line in capsys.readouterr().out
    assert line in (out / "card.md").read_text()
    # Its set's card says the same of its own misses.
    assert json.loads((out / "card_G.json").read_text())["gate"]["verdict"] == "pass"

    # A baseline that meets G's G9 makes that miss the ref's own.
    held = json.loads((first / "card.json").read_text())
    held["columns"]["G"]["readings"]["G9"]["value"] = 0.0
    (tmp_path / "held.json").write_text(json.dumps(held))
    code, card, out = card_against(tmp_path / "held.json")
    assert code == 1 and card["gate"]["verdict"] == "fail"
    assert [(m["set"], m["metric"]) for m in card["gate"]["against"]["new"]] == [
        ("G", "G9")
    ]
    assert (
        "FAIL for this ref - 1 new (G G9 50 (<= 2), was 0)" in capsys.readouterr().out
    )
    assert json.loads((out / "card_A.json").read_text())["gate"]["verdict"] == "pass"

    # --absolute-gate: the diff still, the gate on the card alone.
    code, card, out = card_against(first / "card.json", "--absolute-gate")
    assert code == 1 and "against" not in card["gate"]
    assert card["baseline"]["moved"] == []
    assert "GATE stage3: FAIL - 3 of" in capsys.readouterr().out


def test_absolute_gate_belongs_to_card(capsys):
    with pytest.raises(SystemExit):
        compare_runs.main(["--workspace", "W", "--sample", "s1", "--absolute-gate"])
    assert "--absolute-gate and --deny belong to --card" in capsys.readouterr().err


def test_the_command_refuses_a_card_that_names_a_site_and_writes_nothing(
    served, tmp_path, capsys
):
    _Client.runs["s1"][0]["engine_version"] = "0.6.0 Instrument9"
    out = tmp_path / "card"
    code = compare_runs.main(["--sets", str(served), "--card", str(out)])
    assert code == 2
    assert not out.exists()
    assert "Instrument9" in capsys.readouterr().err


def test_the_command_refuses_a_name_from_the_deny_file(served, tmp_path):
    deny = tmp_path / "deny.txt"
    deny.write_text("# names the card must not hold\npeaky\n")
    out = tmp_path / "card"
    assert (
        compare_runs.main(
            ["--sets", str(served), "--card", str(out), "--deny", str(deny)]
        )
        == 2
    )
    assert not out.exists()


def test_a_failed_detail_read_never_turns_a_miss_into_a_pass(served, tmp_path):
    argv = ["--sets", str(served), "--card", str(tmp_path / "card"), "--gate"]
    # Read, p2 carries no reason: a miss.
    _Client.details["pa-p2"] = {"tier_reasons": []}
    compare_runs.main(argv)
    gate = json.loads((tmp_path / "card" / "card.json").read_text())["gate"]
    assert gate["marks"]["A"]["reasons"] == "miss"
    # Its read failing leaves the rest meeting the target: not read, not ok.
    _Client.details["pa-p2"] = compare_runs.MascopeError("refused")
    compare_runs.main(argv)
    gate = json.loads((tmp_path / "card" / "card.json").read_text())["gate"]
    assert gate["marks"]["A"]["reasons"] == "not read"
    # A read that answers with no provenance is a row with no reason.
    _Client.details["pa-p2"] = None
    compare_runs.main(argv)
    card = json.loads((tmp_path / "card" / "card.json").read_text())
    assert card["columns"]["A"]["readings"]["reasons"] == {"value": 66.7, "n": 3}
    assert card["columns"]["A"]["readings"]["detail_failed"]["value"] == 0


def test_a_read_that_gets_no_answer_is_a_failed_read_not_the_end(
    served, tmp_path, capsys
):
    _Client.details["pa-p2"] = MascopeTimeoutError("Request timed out")
    out = tmp_path / "card"
    assert compare_runs.main(["--sets", str(served), "--card", str(out)]) == 0
    card = json.loads((out / "card.json").read_text())
    assert card["columns"]["A"]["readings"]["detail_failed"]["value"] == 1
    assert "detail of pa-p2 not read (Request timed out)" in capsys.readouterr().err


def test_a_set_no_sample_was_read_on_stays_on_the_card_and_fails_the_gate(
    served, tmp_path, capsys
):
    manifest = json.loads(served.read_text())
    manifest["sets"]["D"] = {
        "workspace": "Chamber study",
        "samples": ["s3"],
        "truth": ["C6H10O4", "C7H10O4"],
    }
    manifest["sets"]["A"]["samples"].append("s4")
    served.write_text(json.dumps(manifest))
    _Client.runs["s3"] = [_run("rb3", "peaky", "0.8.0", "2026-11-01T10:00:00Z")]
    _Client.runs["s4"] = [_run("ra4", "mascope", "0.6.0", "2026-11-10T10:00:00Z")]
    _Client.ledgers["ra4"] = None
    _Client.details["pa-p2"] = {"tier_reasons": [{"rule": "corroborated"}]}
    _Client.ledgers["ra1"] = [_row("p1", corroboration_channels=2)]
    _Client.ledgers["ra2"] = [_row("q1", corroboration_channels=2)]
    out = tmp_path / "card"
    code = compare_runs.main(["--sets", str(served), "--card", str(out), "--gate"])
    assert code == 1
    card = json.loads((out / "card.json").read_text())
    d = card["columns"]["D"]
    assert d["readings"]["samples"]["value"] == 0
    assert d["readings"]["samples_skipped"]["value"] == 1
    assert d["readings"]["G9"]["value"] is None
    # Its list keeps its size; nothing is read against it, and the pooled
    # column, with no other set's list, reads none.
    assert d["readings"]["list_formulas"]["value"] == 2
    assert d["readings"]["list_found"] == {"value": None, "n": None}
    assert "list_formulas" not in card["columns"]["pooled"]["readings"]
    # A read one sample and skipped the one whose ledger is empty.
    assert card["columns"]["A"]["readings"]["samples"]["value"] == 1
    assert card["columns"]["A"]["readings"]["samples_skipped"]["value"] == 1
    assert card["columns"]["pooled"]["readings"]["samples_skipped"]["value"] == 2
    assert card["gate"]["unread_sets"] == ["D"]
    assert card["gate"]["missed"] == []
    assert card["gate"]["marks"]["D"]["G9"] == "not read"
    line = [one for one in capsys.readouterr().out.splitlines() if "GATE" in one][-1]
    assert line.startswith("GATE stage3: FAIL - no sample read on D; ")
    assert "D G9" not in line
    assert "- D: orbitrap; no sample read" in (out / "card_D.md").read_text()
    assert compare_runs.for_set(card["gate"], "A")["verdict"] == "pass"
    assert compare_runs.for_set(card["gate"], "D")["verdict"] == "fail"


def test_a_set_nothing_gates_fails_nothing_when_no_sample_was_read(served, tmp_path):
    manifest = json.loads(served.read_text())
    manifest["sets"] = {
        "A": manifest["sets"]["A"],
        "J": {"workspace": "Chamber study", "samples": ["s3"]},
    }
    served.write_text(json.dumps(manifest))
    _Client.runs["s3"] = []
    out = tmp_path / "card"
    compare_runs.main(["--sets", str(served), "--card", str(out), "--gate"])
    gate = json.loads((out / "card.json").read_text())["gate"]
    assert gate["unread_sets"] == []


def test_a_set_s_card_as_the_baseline_compares_that_set_alone(served, tmp_path, capsys):
    first, second = tmp_path / "first", tmp_path / "second"
    compare_runs.main(["--sets", str(served), "--card", str(first)])
    compare_runs.main(
        [
            "--sets",
            str(served),
            "--card",
            str(second),
            "--baseline",
            str(first / "card_A.json"),
        ]
    )
    card = json.loads((second / "card.json").read_text())
    count = len(card["columns"]["A"]["readings"])
    assert card["baseline"]["moved"] == []
    assert card["baseline"]["compared"] == count
    assert card["baseline"]["only_card"] == ["G", "pooled"]
    assert (
        f"BASELINE: 0 of {count} readings moved; not on the baseline: G, pooled"
        in capsys.readouterr().out
    )
    own = json.loads((second / "card_A.json").read_text())["baseline"]
    assert own["compared"] == count and own["only_card"] == []
    assert own["by_column"] == {"A": count}
    g = json.loads((second / "card_G.json").read_text())["baseline"]
    assert g["compared"] == 0 and g["only_card"] == ["G"]
    assert (
        "0 of 0 readings moved; not on the baseline: G."
        in (second / "card_G.md").read_text()
    )


def test_a_workspace_of_the_card_s_own_words_writes_a_gated_card(served, tmp_path):
    manifest = json.loads(served.read_text())
    manifest["sets"]["A"]["workspace"] = "dev"
    manifest["sets"]["G"]["workspace"] = "Air quality 2025"
    served.write_text(json.dumps(manifest))
    out = tmp_path / "card"
    code = compare_runs.main(["--sets", str(served), "--card", str(out), "--gate"])
    assert code == 1
    assert "quality" in (out / "card.json").read_text()


def test_a_workspace_of_the_gate_line_s_own_words_writes_a_card_against_a_baseline(
    served, tmp_path
):
    first = tmp_path / "first"
    compare_runs.main(["--sets", str(served), "--card", str(first), "--gate"])
    held = json.loads((first / "card.json").read_text())
    held["columns"]["A"]["readings"]["G9"]["value"] = 10.0
    (tmp_path / "held.json").write_text(json.dumps(held))
    manifest = json.loads(served.read_text())
    manifest["sets"]["A"]["workspace"] = "Develop"
    manifest["sets"]["G"]["workspace"] = "Worse than this ref"
    served.write_text(json.dumps(manifest))
    out = tmp_path / "card"
    argv = ["--sets", str(served), "--card", str(out), "--gate"]
    assert compare_runs.main(argv + ["--baseline", str(tmp_path / "held.json")]) == 1
    text = (out / "card.md").read_text()
    assert "FAIL for this ref - 1 new (A G9 50 (<= 2), worse than 10)" in text
    assert "develop's" in text


def test_the_command_denies_an_address_host(served, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MASCOPE_URL", "http://10.0.0.5:8090")
    out = tmp_path / "card"
    argv = ["--sets", str(served), "--card", str(out), "--engine-b", "peaky 10.0.0.5"]
    assert compare_runs.main(argv) == 2
    assert not out.exists()
    assert "'10.0.0.5'" in capsys.readouterr().err


def test_a_set_the_deny_list_names_is_refused_before_anything_is_read(
    served, tmp_path, monkeypatch
):
    manifest = json.loads(served.read_text())
    manifest["deny"] = ["g"]
    served.write_text(json.dumps(manifest))
    _Client.runs = {}
    with pytest.raises(SystemExit, match="set G: the deny list names it"):
        compare_runs.main(["--sets", str(served), "--card", str(tmp_path / "card")])
    del manifest["deny"]
    served.write_text(json.dumps(manifest))
    deny = tmp_path / "deny.txt"
    deny.write_text("A\n")
    with pytest.raises(SystemExit, match="set A: the deny list names it"):
        compare_runs.main(
            [
                "--sets",
                str(served),
                "--card",
                str(tmp_path / "card"),
                "--deny",
                str(deny),
            ]
        )
    # A host is on the deny list too.
    monkeypatch.setenv("MASCOPE_URL", "http://g:8090")
    with pytest.raises(SystemExit, match="set G: the deny list names it"):
        compare_runs.main(["--sets", str(served), "--card", str(tmp_path / "card")])


def test_the_deny_file_and_the_baseline_are_read_before_any_set(served, tmp_path):
    # Any read of a set would fail on these.
    _Client.runs = {}
    argv = ["--sets", str(served), "--card", str(tmp_path / "card")]
    with pytest.raises(SystemExit, match="--deny"):
        compare_runs.main(argv + ["--deny", str(tmp_path / "missing.txt")])
    old = tmp_path / "old.json"
    old.write_text(json.dumps({"format": 0, "columns": {}}))
    with pytest.raises(SystemExit, match="not a card of format"):
        compare_runs.main(argv + ["--baseline", str(old)])
    old.write_text("[]")
    with pytest.raises(SystemExit, match="not a card of format"):
        compare_runs.main(argv + ["--baseline", str(old)])
    with pytest.raises(SystemExit, match="--baseline"):
        compare_runs.main(argv + ["--baseline", str(tmp_path / "missing.json")])


@pytest.mark.parametrize(
    "option",
    [
        ["--sets", "sets.json"],
        ["--gate"],
        ["--baseline", "card.json"],
        ["--deny", "names.txt"],
    ],
)
def test_the_card_options_need_a_card(option, capsys):
    # Refused by the card check itself, not later for want of a --workspace:
    # a --deny the plain compare ignored would leave its output unchecked.
    argv = [*option, "--workspace", "W", "--batch", "B"]
    with pytest.raises(SystemExit):
        compare_runs.main(argv)
    assert "belong to --card" in capsys.readouterr().err


def test_a_card_needs_its_sets(capsys):
    with pytest.raises(SystemExit):
        compare_runs.main(["--card", "x"])
    assert "--card reads the sets of a --sets manifest" in capsys.readouterr().err


def test_a_set_is_read_by_its_batch(served, tmp_path):
    manifest = json.loads(served.read_text())
    manifest["sets"]["G"] = {
        "workspace": "Chamber study",
        "batch": "Spring oxidation",
        "dataset": "Chamber 2026",
    }
    served.write_text(json.dumps(manifest))
    _Client.batches = {"Chamber study": ["s2"]}
    out = tmp_path / "card"
    assert compare_runs.main(["--sets", str(served), "--card", str(out)]) == 0
    card = json.loads((out / "card.json").read_text())
    assert card["columns"]["G"]["readings"]["samples"]["value"] == 1


def test_a_batch_name_and_its_words_are_denied(served, tmp_path):
    manifest = json.loads(served.read_text())
    manifest["sets"]["G"] = {"workspace": "Chamber study", "batch": "Spring oxidation"}
    served.write_text(json.dumps(manifest))
    _Client.batches = {"Chamber study": ["s2"]}
    _Client.runs["s2"][0]["engine_version"] = "0.6.0-spring"
    out = tmp_path / "card"
    assert compare_runs.main(["--sets", str(served), "--card", str(out)]) == 2
    assert not out.exists()


def test_with_decoy_the_arms_join_each_set_s_card_and_the_pooled_one(served, tmp_path):
    _Client.runs["s1"] += [
        _run(
            "rd1",
            "mascope",
            "0.6.0",
            "2026-11-10T11:00:00Z",
            {"decoy_arm": {"control_run_id": "ra1"}},
            decoy="swap_adducts",
        ),
        _run(
            "rd2",
            "mascope",
            "0.6.0",
            "2026-11-10T11:00:00Z",
            decoy="mz_shift_ppm=+9",
            status="failed",
        ),
    ]
    _Client.ledgers["rd1"] = [_row("p1"), _row("p2", tier="candidate")]
    out = tmp_path / "card"
    assert (
        compare_runs.main(["--sets", str(served), "--card", str(out), "--decoy"]) == 0
    )
    card = json.loads((out / "card.json").read_text())
    a = card["columns"]["A"]
    # The arm holds one of its control's two assigned rows.
    assert a["readings"]["decoy swap_adducts held"] == {"value": 50.0, "n": 2}
    assert a["decoys_not_read"] == {"mz_shift_ppm=+9": 1}
    pooled = card["columns"]["pooled"]["readings"]
    assert pooled["decoy swap_adducts held"] == {"value": 50.0, "n": 2}
    # The decoy run is never read as the sample's own.
    assert a["readings"]["assigned"]["value"] == 2
    assert "decoy swap_adducts: held at assigned" in (out / "card_A.md").read_text()


def test_the_reference_is_read_as_it_stood_before_an_instant(served, tmp_path):
    _Client.runs["s1"].append(_run("rb0", "peaky", "0.7.0", "2026-10-01T10:00:00Z"))
    _Client.ledgers["rb0"] = [_row("p1", assigned_formula="C9H9O9"), _unassigned("p2")]
    out = tmp_path / "card"
    argv = ["--sets", str(served), "--card", str(out)]
    assert compare_runs.main(argv + ["--engine-b-before", "2026-10-15T00:00:00Z"]) == 0
    a = json.loads((out / "card.json").read_text())["columns"]["A"]
    assert a["engine_versions"]["b"] == ["0.7.0"]
    assert a["readings"]["G1"] == {"value": 100.0, "n": 2}


def test_a_card_reads_no_arm_made_against_another_run_than_the_one_it_reads(
    served, tmp_path
):
    # The +9 arm against ra1 was refused before it ran, so the newest +9 arm
    # the store keeps is one an earlier build made against its own control.
    _Client.runs["s1"] += [
        _run(
            "rd0",
            "mascope",
            "0.5.0",
            "2026-11-09T11:00:00Z",
            {"decoy_arm": {"control_run_id": "ra0"}},
            decoy="mz_shift_ppm=+9",
        ),
        _run("ra0", "mascope", "0.5.0", "2026-11-09T10:00:00Z"),
    ]
    _Client.ledgers["rd0"] = [_row("p1")]
    _Client.ledgers["ra0"] = [_row("p1"), _row("p2")]
    out = tmp_path / "card"
    assert (
        compare_runs.main(["--sets", str(served), "--card", str(out), "--decoy"]) == 0
    )
    a = json.loads((out / "card.json").read_text())["columns"]["A"]
    assert a["decoys_not_read"] == {"mz_shift_ppm=+9": 1}
    assert "decoy mz_shift_ppm=+9 held" not in a["readings"]


def _with_rule_records(rows, records):
    """Ledger rows as a server that flattens the rule records serves them."""
    for row in rows:
        row.update(
            dict.fromkeys(metrics.LEDGER_RULE_RECORDS),
            **records.get(row["peak_assignment_id"], {}),
        )
    return rows


def test_the_card_reads_the_rule_records_off_the_ledger(
    served, tmp_path, capsys, monkeypatch
):
    """A server that flattens the rows' rule records onto the ledger is read in
    one paged read: no detail read at all, and no row left unread (#3134)."""

    def no_detail(self, sample_id, row_id):
        raise AssertionError(f"detail read of {row_id}")

    monkeypatch.setattr(_Client, "detail", no_detail)
    _with_rule_records(
        _Client.ledgers["ra1"],
        {
            "pa-p1": {
                "tier_reason_rules": ["corroborated"],
                "mass_gate_corroborated": True,
                "envelope_absent_lines": 0,
                "peak_unseparated": False,
            },
            # Read, p2 carries no reason, misses a line and is unseparated.
            "pa-p2": {"envelope_absent_lines": 2, "peak_unseparated": True},
            "pa-p1i": {"tier_reason_rules": ["isotopologue"]},
        },
    )
    _with_rule_records(
        _Client.ledgers["ra2"],
        {
            "pa-q1": {"tier_reason_rules": ["on_a_list"]},
            "pa-q2": {"tier_reason_rules": ["second_line"]},
        },
    )
    out = tmp_path / "card"
    compare_runs.main(["--sets", str(served), "--card", str(out), "--gate"])
    card = json.loads((out / "card.json").read_text())
    a = card["columns"]["A"]["readings"]
    assert a["reasons"] == {"value": 66.7, "n": 3}
    assert a["detail_failed"] == {"value": 0, "n": 3}
    # p1 and p2 recorded an envelope and a separation; p2 is the hit of each.
    assert a["G13"] == {"value": 1, "n": 2}
    assert a["G14"] == {"value": 1, "n": 2}
    assert card["gate"]["marks"]["A"]["reasons"] == "miss"
    assert card["columns"]["G"]["readings"]["reasons"] == {"value": 100.0, "n": 2}
    # G's rows carry no envelope or separation: not read, not 0.
    assert card["columns"]["G"]["readings"]["G13"]["value"] is None
    assert "3 rows' records read off the ledger" in capsys.readouterr().out


def test_a_ledger_with_every_record_is_read_with_no_row_failed(served):
    ledger = pd.DataFrame(
        _with_rule_records(
            [_row("p1"), _row("p2"), _unassigned("p3")],
            {"pa-p1": {"tier_reason_rules": ["corroborated"]}},
        )
    )
    read, off_ledger = compare_runs.read_rule_records(_Client("w"), "s1", ledger)
    assert off_ledger
    assert read["a_assignment_id"].tolist() == ["pa-p1", "pa-p2"]
    assert read["a_reasons"].tolist() == [True, False]
    assert read["a_detail_failed"].tolist() == [False, False]


def test_a_ledger_with_only_some_records_falls_back_to_the_detail_read(served):
    ledger = pd.DataFrame(
        _with_rule_records(
            [_row("p1"), _row("p2")], {"pa-p1": {"tier_reason_rules": ["corroborated"]}}
        )
    ).drop(columns=["peak_unseparated"])
    _Client.details["pa-p2"] = {"tier_reasons": []}
    read, off_ledger = compare_runs.read_rule_records(_Client("w"), "s1", ledger)
    assert not off_ledger
    assert read["a_reasons"].tolist() == [True, False]


def test_the_ledger_columns_read_a_frames_missing_values_as_no_record():
    """A frame stores a null count as NaN and a null flag as None or NaN; both
    are a row with no record, as the detail read gives one."""
    frame = pd.DataFrame(
        {
            "tier_reason_rules": [["a", "series_anchor"], None, []],
            "mass_gate_corroborated": [True, None, False],
            "envelope_absent_lines": [1, None, 0],
            "peak_unseparated": [False, np.nan, True],
        }
    )
    assert [metrics.ledger_columns(row) for row in frame.to_dict("records")] == [
        {
            "a_reasons": True,
            "a_mass_gate_corroborated": True,
            "a_absent_lines": 1,
            "a_unseparated": False,
            "a_series_anchor": True,
        },
        {
            "a_reasons": False,
            "a_mass_gate_corroborated": False,
            "a_absent_lines": None,
            "a_unseparated": None,
            "a_series_anchor": False,
        },
        {
            "a_reasons": False,
            "a_mass_gate_corroborated": False,
            "a_absent_lines": 0,
            "a_unseparated": True,
            "a_series_anchor": False,
        },
    ]


def test_the_card_reads_both_engines_against_each_set_s_authors_list(
    served, tmp_path, capsys
):
    sets = json.loads(served.read_text())
    (tmp_path / "truth").mkdir()
    (tmp_path / "truth" / "A.json").write_text(json.dumps(["C6H10O4", "O4H8C5"]))
    sets["sets"]["A"]["truth"] = {"file": "truth/A.json"}
    sets["sets"]["G"]["truth"] = ["C4 H8 O3 S"]
    served.write_text(json.dumps(sets))
    out = tmp_path / "card"

    assert compare_runs.main(["--sets", str(served), "--card", str(out), "--gate"]) == 1

    card = json.loads((out / "card.json").read_text())
    a, g, pooled = (card["columns"][n]["readings"] for n in ("A", "G", "pooled"))
    # A assigns C6H10O4 and C2H4N2O; the reference C6H10O4, twice.
    assert a["list_formulas"]["value"] == 2
    assert a["list_held"] == {"value": 50.0, "n": 2}
    assert a["list_found"] == {"value": 50.0, "n": 2}
    assert a["ref_list_held"] == {"value": 100.0, "n": 1}
    assert a["ref_list_found"] == {"value": 50.0, "n": 2}
    # G has no reference run: its engine is read, the reference is not.
    assert g["list_found"] == {"value": 100.0, "n": 1}
    assert g["ref_list_found"] == {"value": None, "n": None}
    # Pooled, each set against its own list, the counts added.
    assert pooled["list_formulas"]["value"] == 3
    assert pooled["list_held"] == {"value": 50.0, "n": 4}
    assert pooled["list_found"] == {"value": 66.7, "n": 3}
    assert pooled["ref_list_found"] == {"value": 50.0, "n": 2}
    # Readings, never gated: no mark, and the gate's verdict as without them.
    marked = {key for marks in card["gate"]["marks"].values() for key in marks}
    assert not marked & {key for key, _, _ in metrics.TRUTH_METRICS}
    assert [(m["set"], m["metric"]) for m in card["gate"]["missed"]] == [
        ("A", "G5"),
        ("A", "G9"),
        ("G", "G9"),
    ]
    for name in ("card.md", "card_A.md"):
        text = (out / name).read_text()
        assert compare_runs.TRUTH_NOTE in text
        assert "authors' list: its formulas assigned |" in text
    # A share prints as one, to a tenth.
    assert (
        "| authors' list: its formulas assigned | 50 | 100 | 66.7 | - |"
        in (out / "card.md").read_text().splitlines()
    )
    # The formulas themselves are never on the card.
    assert "C6H10O4" not in (out / "card.json").read_text()


def test_a_card_without_a_list_says_nothing_of_one(served, tmp_path):
    out = tmp_path / "card"
    compare_runs.main(["--sets", str(served), "--card", str(out)])
    assert compare_runs.TRUTH_NOTE not in (out / "card.md").read_text()
    card = json.loads((out / "card.json").read_text())
    assert "list_formulas" not in card["columns"]["pooled"]["readings"]


@pytest.mark.parametrize(
    "truth, message",
    [
        ({"file": "truth/none.json"}, "its file was not read"),
        ({"path": "truth/A.json"}, "a list of formulas, or"),
        (["C6H10O4", "C6H9O4-"], r"truth\[1\]: not a neutral formula"),
        ([], "a list of neutral formulas"),
    ],
)
def test_a_list_that_is_not_one_stops_the_card_before_anything_is_read(
    served, tmp_path, truth, message
):
    sets = json.loads(served.read_text())
    sets["sets"]["A"]["truth"] = truth
    served.write_text(json.dumps(sets))
    _Client.runs = {}
    with pytest.raises(SystemExit, match=message):
        compare_runs.main(["--sets", str(served), "--card", str(tmp_path / "card")])
    assert not (tmp_path / "card").exists()


def test_a_card_without_arms_diffs_the_controls_of_a_baseline_with_them():
    old = _card({"A": {"G1": (6.2, 999), "decoy x9 held_pct": (3.0, 10)}})
    new = _card({"A": {"G1": (6.2, 999)}})
    diff = compare_runs.diff_cards(old, new)
    assert diff["moved"] == [] and diff["compared"] == 1
    assert diff["arms_not_run"] == ["x9"]
    assert compare_runs.baseline_line(diff) == (
        "0 of 1 readings moved; the baseline's decoy rows were not run on this ref (x9)"
    )
    new["baseline"] = diff
    own = json.loads(compare_runs.card_files(new)["card_A.json"])
    assert own["baseline"]["arms_not_run"] == ["x9"]


def test_a_card_with_arms_diffs_its_decoy_rows():
    old = _card({"A": {"G1": (6.2, 999), "decoy x9 held_pct": (3.0, 10)}})
    new = _card({"A": {"G1": (6.2, 999), "decoy x9 held_pct": (5.0, 10)}})
    diff = compare_runs.diff_cards(old, new)
    assert diff["arms_not_run"] == []
    assert [m["metric"] for m in diff["moved"]] == ["decoy x9 held_pct"]


def test_a_set_that_read_no_arm_on_a_card_with_arms_diffs_its_decoy_rows():
    # The card ran the arms: B read them, A read none. A's decoy rows are
    # gone on this ref, which is a move, not "not run".
    readings = {"G1": (6.2, 999), "decoy x9 held_pct": (3.0, 10)}
    old = _card({"A": readings, "B": readings})
    new = _card({"A": {"G1": (6.2, 999)}, "B": readings})
    diff = compare_runs.diff_cards(old, new)
    assert diff["arms_not_run"] == []
    assert [(m["set"], m["metric"]) for m in diff["moved"]] == [
        ("A", "decoy x9 held_pct")
    ]


def test_an_arm_not_read_marks_a_card_made_with_arms():
    # No decoy reading anywhere, but a column says an arm was not read:
    # the card was made with the arms.
    old = _card({"A": {"G1": (6.2, 999), "decoy x9 held_pct": (3.0, 10)}})
    new = _card({"A": {"G1": (6.2, 999)}})
    new["columns"]["A"]["decoys_not_read"] = {"x9": 1}
    diff = compare_runs.diff_cards(old, new)
    assert diff["arms_not_run"] == []
    assert [m["metric"] for m in diff["moved"]] == ["decoy x9 held_pct"]


# --- the batch arm (MAS-1140) --------------------------------------------------


def _arm_manifest(tmp_path, **a):
    """The served sets, set A carrying ``a`` beside its samples."""
    manifest = tmp_path / "arm_sets.json"
    manifest.write_text(
        json.dumps(
            {
                "sets": {
                    "A": {
                        "workspace": "Acquisitions Instrument9",
                        "samples": ["s1"],
                        **a,
                    },
                    "G": {
                        "workspace": "Chamber study",
                        "samples": ["s2"],
                        "sulfur_free": True,
                    },
                }
            }
        )
    )
    return manifest


def _card_of(manifest, out, *extra):
    code = compare_runs.main(
        ["--sets", str(manifest), "--card", str(out), "--gate", *extra]
    )
    return code, json.loads((out / "card.json").read_text())


def _add_after_pass():
    """A run of s1 after a pass of the batch stages, newer than its control:
    the series lifts nothing but cites itself on p1, the strip reads p2 as
    p1's line (at candidate, so its tier moved too), and the known species
    claims p4, the reagent peak, as another ion. p1i and p3 are as the
    control reads them."""
    decisions = {"batch_decisions": {"stages": {"series": {"records": 2}}}}
    config = {**_Client.runs["s1"][0]["config"], **decisions}
    _Client.runs["s1"].insert(
        0, _run("rx1", "mascope", "0.6.0", "2026-11-10T12:00:00Z", config)
    )
    _Client.ledgers["rx1"] = [
        _row(
            "p1",
            peak_assignment_id="px-p1",
            tier="candidate",
            tier_reason_rules=["corroborated", "series_anchor"],
            corroboration_channels=2,
        ),
        _row(
            "p2",
            peak_assignment_id="px-p2",
            role="iso_child",
            tier="candidate",
            isotope_label="13C",
            owner_peak_assignment_id="px-p1",
        ),
        _row(
            "p1i",
            peak_assignment_id="px-p1i",
            mz=101.0,
            role="iso_child",
            owner_peak_assignment_id="px-p1",
        ),
        _unassigned("p3", mz=150.0, intensity=50.0),
        _row(
            "p4",
            peak_assignment_id="px-p4",
            mz=61.98837,
            intensity=1000.0,
            role="reagent",
            tier="unassigned",
            source=None,
            ion_formula="NO3-",
        ),
    ]
    _Client.details.update(
        {
            "px-p2": {"batch_isotopologue": {"line": "13C"}},
            "px-p4": {"reagent": {"reason": "batch_known_species"}},
        }
    )
    return "rx1"


@pytest.fixture
def after_pass(served):
    return _add_after_pass()


def test_a_named_run_is_read_not_the_sample_s_newest(served, tmp_path):
    _, before = _card_of(served, tmp_path / "before_arm")
    _add_after_pass()
    # Without a name the newest run is read: the one after the pass.
    _, newest = _card_of(_arm_manifest(tmp_path), tmp_path / "newest")
    assert newest["columns"]["A"] != before["columns"]["A"]
    code, named = _card_of(
        _arm_manifest(tmp_path, runs={"s1": "ra1"}), tmp_path / "named"
    )
    assert named["columns"] == before["columns"]
    assert named["gate"] == before["gate"]
    assert "batch_arm" not in named


def test_a_named_run_that_is_no_completed_run_skips_its_sample(served, tmp_path):
    _Client.runs["s1"].insert(
        0, _run("rf1", "mascope", "0.6.0", "2026-11-10T12:00:00Z", status="failed")
    )
    for name in ("rf1", "nowhere"):
        _, card = _card_of(
            _arm_manifest(tmp_path, runs={"s1": name}), tmp_path / f"card_{name}"
        )
        readings = card["columns"]["A"]["readings"]
        assert readings["samples"]["value"] == 0
        assert readings["samples_skipped"]["value"] == 1


def test_the_batch_arm_reads_the_runs_after_the_pass_beside_the_control(
    served, tmp_path
):
    _, control = _card_of(served, tmp_path / "control")
    after_pass = _add_after_pass()
    # The pass refit the model; the control predates the model record.
    rx1 = next(r for r in _Client.runs["s1"] if r["peak_assignment_run_id"] == "rx1")
    rx1["config"] = {**rx1["config"], **_at_model(2)}
    # A run of a sample in a batch records every stage it read, at records: 0
    # too (MAS-985): the control read none of the batch's decisions.
    ra1 = next(r for r in _Client.runs["s1"] if r["peak_assignment_run_id"] == "ra1")
    ra1["config"] = {
        **ra1["config"],
        "batch_decisions": {"stages": {"series": {"records": 0}}},
    }
    code, card = _card_of(
        _arm_manifest(
            tmp_path, runs={"s1": "ra1"}, batch_arm={"runs": {"s1": after_pass}}
        ),
        tmp_path / "arm",
    )
    # The control is the card it always was: its columns, its gate, its exit.
    assert card["columns"] == control["columns"]
    assert card["gate"] == control["gate"]
    assert code == 1
    arm = card["batch_arm"]
    assert list(arm["columns"]) == ["A"] and arm["off"] == {}
    after = arm["columns"]["A"]
    # Every reading of the control, read on the runs after the pass: p2 is a
    # line now, and p1 a candidate.
    assert set(after["readings"]) == set(control["columns"]["A"]["readings"])
    # The arm's column counts its runs' model versions as the control's does:
    # the run after the pass took the refit, the control's predates the record.
    model_rows = ("model_v1", "model_v2", "model_before")
    assert [after["readings"][k]["value"] for k in model_rows] == [0, 1, 0]
    assert [control["columns"]["A"]["readings"][k]["value"] for k in model_rows] == [
        0,
        0,
        1,
    ]
    assert after["readings"]["assigned"]["value"] == 0
    assert control["columns"]["A"]["readings"]["assigned"]["value"] == 2
    moves = after["moves"]
    assert {k: moves[k] for k in ("samples", "peaks", "moved")} == {
        "samples": 1,
        "peaks": 5,
        "moved": 3,
    }
    # p2 changed role (its tier with it, counted under role alone); p1 its
    # tier alone; p4 its reading alone.
    assert (moves["role"], moves["tier"], moves["reading"]) == (1, 1, 2)
    assert moves["by_role"] == {"M0 -> iso_child": 1}
    assert moves["by_decision"] == {
        "known_species": 1,
        "water_rung": 0,
        "listed_compound": 0,
        "isotopologue_strip": 1,
        "series": 1,
        "none": 0,
        "not_read": 0,
    }
    assert (moves["control_read_decisions"], moves["after_read_decisions"]) == (0, 1)
    text = (tmp_path / "arm" / "card.md").read_text()
    assert "## After the batch pass" in text
    assert "| metric | A | A after the pass |" in text
    assert "| A | 1 | 5 | 3 | 1 | 1 | 2 | 1 | 0 | 0 | 1 | 1 | 0 | 0 |" in text
    assert compare_runs.BATCH_ARM_COVERS in text
    # A set's own card carries its arm; G asked for none and shows none.
    assert "## After the batch pass" in (tmp_path / "arm" / "card_A.md").read_text()
    assert "After the batch pass" not in (tmp_path / "arm" / "card_G.md").read_text()
    own = json.loads((tmp_path / "arm" / "card_A.json").read_text())
    assert list(own["batch_arm"]["columns"]) == ["A"]
    # With a second set on the arm, each set's file holds its own alone.
    both = json.loads(json.dumps(card))
    both["batch_arm"]["columns"]["G"] = both["batch_arm"]["columns"]["A"]
    both["batch_arm"]["off"] = {"A": "x", "G": "y"}
    files = compare_runs.card_files(both)
    for name in ("A", "G"):
        arm_of = json.loads(files[f"card_{name}.json"])["batch_arm"]
        assert list(arm_of["columns"]) == [name]
        assert list(arm_of["off"]) == [name]


def test_the_batch_arm_carries_the_series_line_and_the_control_none(
    served, after_pass, tmp_path
):
    """A run after the pass whose p1 a series alone keeps at assigned: G12
    exempts it on the arm, the line beside G12 counts it, and the control,
    whose runs read no series, carries no line."""
    _Client.ledgers[after_pass] = _with_rule_records(
        [
            _row("p1", peak_assignment_id="px-p1"),
            _unassigned("p3", mz=150.0, intensity=50.0),
        ],
        {"px-p1": {"tier_reason_rules": ["series_anchor", "no_close_rival"]}},
    )
    _, card = _card_of(
        _arm_manifest(
            tmp_path, runs={"s1": "ra1"}, batch_arm={"runs": {"s1": after_pass}}
        ),
        tmp_path / "arm",
    )
    control = card["columns"]["A"]["readings"]
    assert not [key for key in control if key.startswith("G12_")]
    after = card["batch_arm"]["columns"]["A"]["readings"]
    assert after["G12"] == {"value": 0, "n": 1}
    assert after["G12_series"] == {"value": 1, "n": 1}
    # s1 carries a reference run, which commits p1's formula.
    assert after["G12_series_same"] == {"value": 100.0, "n": 1}
    assert after["G12_yardstick_same"] == {"value": None, "n": 0}
    text = (tmp_path / "arm" / "card_A.md").read_text()
    arm_part = text[text.index("## After the batch pass") :]
    assert "| G12 exempt: lone peaks a batch series lifts | - | 1 |" in arm_part
    assert "G12 exempt" not in text[: text.index("## After the batch pass")]
    assert compare_runs.SERIES_NOTE in arm_part
    assert compare_runs.SERIES_NOTE not in text[: text.index("## After the batch pass")]
    # A table that holds the line says what it counts, wherever it is.
    on_control = json.loads(json.dumps(card))
    on_control["columns"]["A"]["readings"]["G12_series"] = after["G12_series"]
    head = compare_runs.card_markdown(on_control, ["A"])
    assert compare_runs.SERIES_NOTE in head[: head.index("## After the batch pass")]


def test_a_moved_row_that_cites_nothing_or_whose_read_failed_is_counted_so(
    served, after_pass, tmp_path, monkeypatch
):
    _Client.details["px-p2"] = {}
    _Client.details["px-p4"] = NotFoundError("gone", status_code=404)
    _Client.ledgers[after_pass][0]["tier_reason_rules"] = ["corroborated"]
    asked = []
    read = compare_runs.read_provenance

    def recorded(client, sample_id, ids):
        # The moves' own detail read, not the readings' read of every row.
        if sys._getframe(1).f_code.co_name == "batch_moves":
            asked.extend(ids)
        return read(client, sample_id, ids)

    monkeypatch.setattr(compare_runs, "read_provenance", recorded)
    _, card = _card_of(
        _arm_manifest(
            tmp_path, runs={"s1": "ra1"}, batch_arm={"runs": {"s1": after_pass}}
        ),
        tmp_path / "arm",
    )
    by = card["batch_arm"]["columns"]["A"]["moves"]["by_decision"]
    assert (by["none"], by["not_read"]) == (2, 1)
    # Only the moved reagent and iso_child rows are read for their detail:
    # p1, an M0 that cites nothing now, is not read and counts as none.
    assert sorted(asked) == ["px-p2", "px-p4"]
    assert sum(by[k] for k in compare_runs.BATCH_DECISIONS) == 0


def test_a_moved_row_cites_each_decision_its_provenance_names():
    assert compare_runs.cited_by_provenance(
        {
            "reagent": {"reason": "batch_water_ladder"},
            "tier_reasons": [{"rule": "batch_known_species"}, {"rule": "lone_peak"}],
        }
    ) == {"water_rung", "listed_compound"}
    assert compare_runs.cited_by_provenance({"reagent": {"reason": "own"}}) == set()
    assert compare_runs.cited_by_provenance(None) == set()


def test_a_set_left_off_the_arm_is_named_with_why_and_its_control_stands(
    served, tmp_path
):
    _, control = _card_of(served, tmp_path / "control")
    _add_after_pass()
    _, card = _card_of(
        _arm_manifest(
            tmp_path, runs={"s1": "ra1"}, batch_arm_off="the pass of its batch failed"
        ),
        tmp_path / "off",
    )
    assert card["columns"] == control["columns"]
    assert card["batch_arm"] == {
        "columns": {},
        "off": {"A": "the pass of its batch failed"},
    }
    text = (tmp_path / "off" / "card.md").read_text()
    assert "- A: no batch arm, the pass of its batch failed" in text
    assert "| metric | A | A after the pass |" not in text


def test_a_sample_with_no_run_after_the_pass_is_skipped_on_the_arm_alone(
    served, after_pass, tmp_path
):
    _, card = _card_of(
        _arm_manifest(tmp_path, runs={"s1": "ra1"}, batch_arm={"runs": {}}),
        tmp_path / "arm",
    )
    assert card["columns"]["A"]["readings"]["samples"]["value"] == 1
    after = card["batch_arm"]["columns"]["A"]
    assert after["readings"]["samples"]["value"] == 0
    assert after["readings"]["samples_skipped"]["value"] == 1
    assert after["moves"]["samples"] == 0


@pytest.mark.parametrize(
    "spec, why",
    [
        ({"runs": ["ra1"]}, "runs maps each sample to a run id"),
        ({"runs": {"s1": ""}}, "runs maps each sample to a run id"),
        ({"batch_arm": {"s1": "rx1"}}, "batch_arm names its runs"),
        ({"batch_arm": {"runs": {"s1": 3}}}, "batch_arm.runs maps each sample"),
        ({"batch_arm_off": True}, "batch_arm_off says why"),
        (
            {"batch_arm": {"runs": {}}, "batch_arm_off": "x"},
            "names both a batch arm and why it has none",
        ),
    ],
)
def test_a_set_s_runs_and_arm_of_another_shape_are_refused(tmp_path, spec, why):
    manifest = tmp_path / "sets.json"
    manifest.write_text(
        json.dumps({"sets": {"A": {"workspace": "W", "samples": ["s1"], **spec}}})
    )
    with pytest.raises(SystemExit, match=why):
        compare_runs.load_sets(manifest)


# --- The measurement model each control scored at -----------------------------------


def test_a_column_counts_its_samples_by_the_model_version_they_were_read_at():
    readings = metrics.card_readings(
        _frame([_row("p1")]), model_versions={"v2": 3, "before": 1}
    )
    assert readings["model_v1"] == {"value": 0, "n": None}
    assert readings["model_v2"] == {"value": 3, "n": None}
    assert readings["model_before"] == {"value": 1, "n": None}
    # Without the counts the rows are there, and read nothing.
    plain = metrics.card_readings(_frame([_row("p1")]))
    assert {plain[f"model_{v}"]["value"] for v in ("v1", "v2", "before")} == {None}
    # A version beyond the known ones adds a row of its own.
    later = metrics.card_readings(_frame([_row("p1")]), model_versions={"v3": 2})
    assert later["model_v3"] == {"value": 2, "n": None}
    assert compare_runs.unit_of("model_v1") == "count"
    assert compare_runs.label_of("model_before") == (
        "of them read off a run from before the model record"
    )


def _arm_part(control_rows, decoy_rows):
    control = {"rows": control_rows, "rows_lt_350": control_rows, "rows_ge_350": 0}
    control.update(intensity=1.0, intensity_lt_350=1.0, intensity_ge_350=0.0)
    decoy = {"rows": decoy_rows, "rows_lt_350": decoy_rows, "rows_ge_350": 0}
    decoy.update(intensity=0.5, intensity_lt_350=0.5, intensity_ge_350=0.0)
    return {
        "samples": 1,
        "control": control,
        "decoy": decoy,
        **compare_runs.decoy_rates(control, decoy),
    }


def test_an_arm_s_held_share_is_split_by_its_controls_model_version():
    v1, v2 = _arm_part(40, 2), _arm_part(10, 3)
    total = {
        **_arm_part(50, 5),
        "samples": 2,
        "by_model": {"v1": v1, "v2": v2},
        "model_differs": 1,
    }
    readings = metrics.card_readings(_frame([_row("p1")]), decoy_arms={"x9": total})
    assert _value(readings["decoy x9 held"]) == (10.0, 50)
    assert _value(readings["decoy x9 held_model_v1"]) == (5.0, 40)
    assert _value(readings["decoy x9 held_model_v2"]) == (30.0, 10)
    # No control from before the record: the row is there, over no rows.
    assert _value(readings["decoy x9 held_model_before"]) == (None, 0)
    assert _value(readings["decoy x9 model_differs"]) == (1, 2)
    assert compare_runs.unit_of("decoy x9 model_differs") == "count"
    assert compare_runs.unit_of("decoy x9 held_model_v2") == "%"
    assert compare_runs.label_of("decoy x9 held_model_v2") == (
        "decoy x9: held at assigned, controls at model v2"
    )
    assert compare_runs.label_of("decoy x9 held_model_before") == (
        "decoy x9: held at assigned, controls from before the model record"
    )
    # A total from before the split reads no split.
    old = {k: v for k, v in total.items() if k not in ("by_model", "model_differs")}
    plain = metrics.card_readings(_frame([_row("p1")]), decoy_arms={"x9": old})
    assert not any("model" in key for key in plain if key.startswith("decoy "))


def test_the_pooled_arms_add_each_set_s_parts_by_model_version():
    a = {
        **_arm_part(50, 5),
        "by_model": {"v1": _arm_part(40, 2), "v2": _arm_part(10, 3)},
        "model_differs": 0,
    }
    b = {**_arm_part(20, 4), "by_model": {"v2": _arm_part(20, 4)}, "model_differs": 1}
    pooled = compare_runs.pooled_arms([{"pooled": {"x9": a}}, {"pooled": {"x9": b}}])
    arm = pooled["x9"]
    assert arm["samples"] == 2 and arm["model_differs"] == 1
    assert arm["by_model"]["v1"]["held_pct"] == 5.0
    # 3 + 4 of 10 + 20 rows at v2.
    assert arm["by_model"]["v2"]["samples"] == 2
    assert arm["by_model"]["v2"]["held_pct"] == pytest.approx(23.3)
    assert arm["held_pct"] == pytest.approx(12.9)


def _at_model(version):
    if version is None:
        return {"pattern_scoring": {"mz_tolerance_ppm": 3.0}}
    return {"measurement_model": {"version": version, "width_ppm": 1.2}}


@pytest.mark.parametrize(
    "versions, expected",
    [
        # s1's control took the refit; s2's predates the model record.
        ((2, None), {"A": [0, 1, 0], "G": [0, 0, 1], "pooled": [0, 1, 1]}),
        # Both kept v1: the pooled column adds the two sets' counts.
        ((1, 1), {"A": [1, 0, 0], "G": [1, 0, 0], "pooled": [2, 0, 0]}),
    ],
)
def test_the_card_counts_each_set_s_controls_by_model_version(
    served, tmp_path, versions, expected
):
    for sample, version in zip(("s1", "s2"), versions):
        run = _Client.runs[sample][0]
        run["config"] = {**run["config"], **_at_model(version)}
    out = tmp_path / "card"
    compare_runs.main(["--sets", str(served), "--card", str(out)])
    card = json.loads((out / "card.json").read_text())
    values = {
        name: [
            column["readings"][f"model_{v}"]["value"] for v in ("v1", "v2", "before")
        ]
        for name, column in card["columns"].items()
    }
    assert values == expected
    v2 = " | ".join(str(values[name][1]) for name in ("A", "G", "pooled"))
    text = (out / "card.md").read_text()
    assert f"| of them read off a run at measurement model v2 | {v2} |" in text


def test_a_set_of_two_samples_at_one_version_counts_both(served, tmp_path):
    for sample in ("s1", "s2"):
        run = _Client.runs[sample][0]
        run["config"] = {**run["config"], **_at_model(2)}
    manifest = tmp_path / "one_set.json"
    manifest.write_text(
        json.dumps(
            {"sets": {"A": {"workspace": "Chamber study", "samples": ["s1", "s2"]}}}
        )
    )
    out = tmp_path / "card"
    compare_runs.main(["--sets", str(manifest), "--card", str(out)])
    readings = json.loads((out / "card.json").read_text())["columns"]["A"]["readings"]
    assert readings["samples"]["value"] == 2
    assert [readings[f"model_{v}"]["value"] for v in ("v1", "v2", "before")] == [
        0,
        2,
        0,
    ]


def test_with_decoy_each_arm_is_split_by_its_control_s_model_version(served, tmp_path):
    first = _Client.runs["s1"][0]
    first["config"] = {**first["config"], **_at_model(2)}
    _Client.runs["s1"].append(
        _run(
            "rd1",
            "mascope",
            "0.6.0",
            "2026-11-10T11:00:00Z",
            {"decoy_arm": {"control_run_id": "ra1"}, **_at_model(2)},
            decoy="swap_adducts",
        )
    )
    _Client.ledgers["rd1"] = [_row("p1"), _row("p2", tier="candidate")]
    out = tmp_path / "card"
    compare_runs.main(["--sets", str(served), "--card", str(out), "--decoy"])
    card = json.loads((out / "card.json").read_text())
    for name in ("A", "pooled"):
        readings = card["columns"][name]["readings"]
        assert readings["decoy swap_adducts held_model_v2"] == {"value": 50.0, "n": 2}
        assert readings["decoy swap_adducts held_model_v1"] == {"value": None, "n": 0}
        assert readings["decoy swap_adducts model_differs"] == {"value": 0, "n": 1}
    assert (
        "decoy swap_adducts: held at assigned, controls at model v2"
        in (out / "card_A.md").read_text()
    )


def test_a_baseline_without_the_model_rows_reads_them_as_new_and_fails_nothing(
    served, tmp_path, capsys
):
    first, second = tmp_path / "first", tmp_path / "second"
    compare_runs.main(["--sets", str(served), "--card", str(first)])
    baseline = json.loads((first / "card.json").read_text())
    for column in baseline["columns"].values():
        for key in ("model_v1", "model_v2", "model_before"):
            del column["readings"][key]
    (first / "older.json").write_text(json.dumps(baseline))
    code = compare_runs.main(
        [
            "--sets",
            str(served),
            "--card",
            str(second),
            "--gate",
            "--baseline",
            str(first / "older.json"),
        ]
    )
    card = json.loads((second / "card.json").read_text())
    moved = card["baseline"]["moved"]
    # Every row the baseline lacks is named with no value before it, and
    # nothing else moved.
    assert {(m["set"], m["metric"]) for m in moved} == {
        (name, f"model_{v}")
        for name in ("A", "G", "pooled")
        for v in ("v1", "v2", "before")
    }
    assert {str(m["was"]) for m in moved} == {"None"}
    # The gate against the baseline reads no new miss off them.
    assert card["gate"]["against"]["new"] == []
    assert card["gate"]["verdict"] == "pass" and code == 0


def _scored_rivals(refit, ties=1, reach=None):
    """A run's count of the candidates its search scored outside its rival
    sets, as ``rival_count`` records it in the search scope (MAS-1184)."""
    kind = {
        "readings": 4,
        "scored": 40,
        "held": 6,
        "outside": 30,
        "not_placed": 4,
        "ties": {
            "within_1": 0,
            "1_to_2": ties,
            "2_to_3": 0,
            "3_to_5": 0,
            "over_5": 0,
        },
    }
    return {
        "list": {**kind, "readings": 1, "ties": {**kind["ties"], "1_to_2": 0}},
        "search": kind,
        "density_rises": ties,
        "reach_limit": reach or {},
        "would_cap": sum((reach or {}).values()),
        "density_limit": 2,
        "refit_taken": refit,
        "named_at_version": 1,
        "measured_at_version": 2 if refit else 1,
        "seeds_measured": 30,
        "seconds": 1.5,
        "seeded_seconds": 0.5,
    }


def test_a_run_s_count_is_flattened_by_its_group():
    group, counts = compare_runs.scored_rivals_counts(
        {
            "search_scope": {
                "scored_rivals": _scored_rivals(
                    True, reach={"assigned": 1, "x": 2, "y": 1}
                )
            }
        }
    )
    assert group == "refit_taken"
    assert counts["runs"] == 1
    assert counts["search.ties.1_to_2"] == 1
    assert counts["list.readings"] == 1
    assert counts["reach_limit.assigned"] == 1
    assert counts["reach_limit.other"] == 3
    assert counts["would_cap"] == 4
    assert counts["seconds"] == 1.5
    assert compare_runs.scored_rivals_counts({"search_scope": {}}) is None
    assert compare_runs.scored_rivals_counts(None) is None


def test_runs_counts_are_summed_by_their_group():
    summed = compare_runs.summed_scored_rivals(
        [
            {"refit_taken": {"runs": 1, "seconds": 0.25}},
            {"refit_taken": {"runs": 1, "seconds": 0.5}, "first_model": {"runs": 1}},
            None,
        ]
    )
    assert summed == {
        "refit_taken": {"runs": 2, "seconds": 0.75},
        "first_model": {"runs": 1},
    }


def test_the_card_shows_the_scored_rivals_per_set_apart_by_the_refit(served, tmp_path):
    # s1, set A, took its refit and has a candidate tie at v2 that takes an
    # assigned row to the density limit; s2, set G, kept its first model.
    for sample, record in (
        ("s1", _scored_rivals(True, reach={"assigned": 1})),
        ("s2", _scored_rivals(False, ties=0)),
    ):
        run = _Client.runs[sample][0]
        run["config"] = {
            **run["config"],
            "search_scope": {**run["config"]["search_scope"], "scored_rivals": record},
        }
    out = tmp_path / "card"
    compare_runs.main(["--sets", str(served), "--card", str(out)])
    card = json.loads((out / "card.json").read_text())
    columns = card["columns"]
    assert set(columns["A"]["scored_rivals"]) == {"refit_taken"}
    assert set(columns["G"]["scored_rivals"]) == {"first_model"}
    assert columns["A"]["scored_rivals"]["refit_taken"]["search.ties.1_to_2"] == 1
    assert columns["A"]["scored_rivals"]["refit_taken"]["reach_limit.assigned"] == 1
    assert columns["G"]["scored_rivals"]["first_model"]["search.ties.1_to_2"] == 0
    # The pooled column adds each group over the sets.
    assert columns["pooled"]["scored_rivals"] == {
        "refit_taken": columns["A"]["scored_rivals"]["refit_taken"],
        "first_model": columns["G"]["scored_rivals"]["first_model"],
    }
    text = (out / "card.md").read_text()
    assert "## The candidates outside the rival sets" in text
    refit = text.index("### Runs that took their refitted model")
    first = text.index("### Runs that kept their first model")
    assert "| count | A | pooled |" in text[refit:first]
    assert "| count | G | pooled |" in text[first:]
    assert (
        "| search formulas: tie, 1 to 2 gaps off at the search | 1 | 1 |"
        in (text[refit:first])
    )
    assert (
        "| of them to the density limit, assigned today | 1 | 1 |"
        in (text[refit:first])
    )
    assert "| seconds of the extra pass | 1.5 | 1.5 |" in text[first:]
    # The count is no reading: it moves no metric and no gate reads it.
    assert not any("scored" in key for key in columns["A"]["readings"])


def test_a_card_of_runs_without_the_option_shows_no_count(served, tmp_path):
    out = tmp_path / "card"
    compare_runs.main(["--sets", str(served), "--card", str(out)])
    card = json.loads((out / "card.json").read_text())
    assert all("scored_rivals" not in column for column in card["columns"].values())
    assert "outside the rival sets" not in (out / "card.md").read_text()
