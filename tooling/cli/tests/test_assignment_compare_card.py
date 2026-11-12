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
        "tiering": {"version": 13},
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
    manifest["sets"]["D"] = {"workspace": "Chamber study", "samples": ["s3"]}
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
