"""
Unit tests for the peak-centric assignment engine.

Covers the target-to-peak inversion (Stage A), the single-owner-per-peak
arbitration, isotope-child attribution, confidence tiers, and the mapping of
untargeted composition results onto assignment rows (Stage B). Pure
DataFrame/dict logic - no database access.
"""

import json

import pandas as pd
import pytest

from mascope_backend.api.new.peak_assignments.engine import (
    ROLE_ISO_CHILD,
    ROLE_M0,
    ROLE_UNASSIGNED,
    SOURCE_DATABASE,
    SOURCE_UNTARGETED,
    TIER_ASSIGNED,
    TIER_BELOW_ASSIGNABILITY,
    TIER_CANDIDATE,
    TIER_UNASSIGNED,
    build_unassigned_assignments,
    invert_matches_to_peak_assignments,
    score_ions_by_fit,
    tier_for_score,
    untargeted_matches_to_peak_assignments,
)


POSSIBLE = 0.7
PROBABLE = 0.8


def _isotope_row(
    *,
    target_isotope_id: str,
    target_ion_id: str,
    target_compound_id: str,
    compound_formula: str,
    ion_formula: str,
    mz: float,
    relative_abundance: float,
    sample_peak_id: str,
    sample_peak_mz: float | None = None,
    sample_peak_intensity: float = 1000.0,
    match_score: float = 0.9,
    match_mz_error: float = 1.0,
    match_abundance_error: float = 0.05,
    ionization: str = "+H+",
    ionization_mechanism_id: str | None = None,
) -> dict:
    """One row of the targeted matcher's output enriched with target metadata.

    The mechanism's database id and its notation are two separate columns on the
    real frame. Rows that do not care let the id default to the notation, which
    keeps the fixtures readable; pass ``ionization_mechanism_id`` explicitly when
    the point of the test is WHICH of the two columns a value was read from.
    """
    return {
        "target_isotope_id": target_isotope_id,
        "target_ion_id": target_ion_id,
        "target_isotope_formula": compound_formula,
        "mz": mz,
        "relative_abundance": relative_abundance,
        "resolution": "HIGH",
        "target_ion_formula": ion_formula,
        "ionization_mechanism_id": ionization_mechanism_id or ionization,
        "ionization_mechanism": ionization,
        "target_compound_id": target_compound_id,
        "target_compound_formula": compound_formula,
        "sample_peak_id": sample_peak_id,
        "sample_peak_mz": sample_peak_mz if sample_peak_mz is not None else mz,
        "sample_peak_intensity": sample_peak_intensity,
        "sample_peak_tof": 12.3,
        "match_score": match_score,
        "match_mz_error": match_mz_error,
        "match_abundance_error": match_abundance_error,
    }


class TestTierForScore:
    def test_probable_score_is_assigned(self):
        assert tier_for_score(0.85, POSSIBLE, PROBABLE) == TIER_ASSIGNED

    def test_threshold_boundaries_are_inclusive(self):
        assert tier_for_score(PROBABLE, POSSIBLE, PROBABLE) == TIER_ASSIGNED
        assert tier_for_score(POSSIBLE, POSSIBLE, PROBABLE) == TIER_CANDIDATE

    def test_possible_score_is_candidate(self):
        assert tier_for_score(0.75, POSSIBLE, PROBABLE) == TIER_CANDIDATE

    def test_weak_score_is_below_assignability(self):
        assert tier_for_score(0.3, POSSIBLE, PROBABLE) == TIER_BELOW_ASSIGNABILITY

    def test_zero_and_missing_scores_are_below_assignability(self):
        assert tier_for_score(0.0, POSSIBLE, PROBABLE) == TIER_BELOW_ASSIGNABILITY
        assert tier_for_score(None, POSSIBLE, PROBABLE) == TIER_BELOW_ASSIGNABILITY
        assert tier_for_score(float("nan"), POSSIBLE, PROBABLE) == (
            TIER_BELOW_ASSIGNABILITY
        )


class TestInvertMatches:
    def test_empty_input_yields_no_assignments(self):
        assert (
            invert_matches_to_peak_assignments(
                pd.DataFrame(), "sample1", "run1", POSSIBLE, PROBABLE
            )
            == []
        )

    def test_best_score_wins_contested_peak_and_loser_becomes_alternative(self):
        match_df = pd.DataFrame(
            [
                _isotope_row(
                    target_isotope_id="iso1",
                    target_ion_id="ion1",
                    target_compound_id="cmp1",
                    compound_formula="C6H12O6",
                    ion_formula="C6H13O6+",
                    mz=181.0707,
                    relative_abundance=1.0,
                    sample_peak_id="p1",
                    match_score=0.95,
                    ionization="+H+",
                    ionization_mechanism_id="mech-h",
                ),
                _isotope_row(
                    target_isotope_id="iso3",
                    target_ion_id="ion2",
                    target_compound_id="cmp2",
                    compound_formula="C7H16O5",
                    ion_formula="C7H16NaO5+",
                    mz=181.0705,
                    relative_abundance=1.0,
                    sample_peak_id="p1",
                    match_score=0.75,
                    ionization="+Na+",
                    ionization_mechanism_id="mech-na",
                ),
            ]
        )

        assignments = invert_matches_to_peak_assignments(
            match_df, "sample1", "run1", POSSIBLE, PROBABLE
        )

        assert len(assignments) == 1
        winner = assignments[0]
        assert winner["sample_peak_id"] == "p1"
        assert winner["assigned_formula"] == "C6H12O6"
        assert winner["target_compound_id"] == "cmp1"
        assert winner["target_ion_id"] == "ion1"
        assert winner["source"] == SOURCE_DATABASE
        assert winner["role"] == ROLE_M0
        assert winner["tier"] == TIER_ASSIGNED
        assert winner["peak_assignment_run_id"] == "run1"
        assert winner["sample_item_id"] == "sample1"

        # The losing candidate is preserved as an alternative
        assert len(winner["alternatives"]) == 1
        alternative = winner["alternatives"][0]
        assert alternative["target_ion_id"] == "ion2"
        assert alternative["assigned_formula"] == "C7H16O5"
        assert alternative["fit_score"] == pytest.approx(0.75)
        # The runner-up records the adduct IT was scored under, not the winner's.
        # A formula without its mechanism is half an assignment: promoting one by
        # hand would put an adductless claim on the ledger, and a verification's
        # identity (peak + formula + mechanism) could not be formed from it. The
        # id is a column of its own - the notation ("+Na+") must not stand in.
        assert winner["ionization_mechanism_id"] == "mech-h"
        assert alternative["ionization_mechanism_id"] == "mech-na"
        # Both candidates are their ion's most abundant isotope, so the runner-up
        # is labelled the main peak it is; the satellite case is the test below.
        assert alternative["isotope_label"] == "M0"

    def test_runner_up_satellite_keeps_its_own_isotope_label(self):
        """A runner-up is as free as a winner to be one of its ion's satellites.

        ion2's M+1 contests the peak ion1's M0 wins. The label is computed per
        candidate off ion2's own M0 m/z, not copied from the winner: without it
        there is nothing on the alternative to say it is a satellite, and
        promoting it by hand would enter C7H11NO3's M+1 into the ledger as
        C7H11NO3 itself - one compound owning a peak a mass unit off its own,
        and an isotopologue family headed by its own child.
        """
        match_df = pd.DataFrame(
            [
                _isotope_row(  # wins the contested peak
                    target_isotope_id="iso1",
                    target_ion_id="ion1",
                    target_compound_id="cmp1",
                    compound_formula="C6H12O6",
                    ion_formula="C6H13O6+",
                    mz=181.0707,
                    relative_abundance=1.0,
                    sample_peak_id="p1",
                    match_score=0.95,
                    ionization_mechanism_id="mech-h",
                ),
                _isotope_row(  # ion2's own M0, on a peak of its own
                    target_isotope_id="iso2",
                    target_ion_id="ion2",
                    target_compound_id="cmp2",
                    compound_formula="C7H11NO3",
                    ion_formula="C7H11NNaO3+",
                    mz=180.0631,
                    relative_abundance=1.0,
                    sample_peak_id="p0",
                    match_score=0.60,
                    ionization="+Na+",
                    ionization_mechanism_id="mech-na",
                ),
                _isotope_row(  # ion2's M+1, contesting p1 and losing it
                    target_isotope_id="iso3",
                    target_ion_id="ion2",
                    target_compound_id="cmp2",
                    compound_formula="C7H11NO3",
                    ion_formula="C7H11NNaO3+",
                    mz=181.0665,
                    relative_abundance=0.08,
                    sample_peak_id="p1",
                    sample_peak_mz=181.0707,
                    match_score=0.60,
                    match_mz_error=23.0,
                    ionization="+Na+",
                    ionization_mechanism_id="mech-na",
                ),
            ]
        )

        assignments = invert_matches_to_peak_assignments(
            match_df, "sample1", "run1", POSSIBLE, PROBABLE
        )
        by_peak = {a["sample_peak_id"]: a for a in assignments}

        contested = by_peak["p1"]
        assert contested["assigned_formula"] == "C6H12O6"
        assert contested["isotope_label"] == "M0"
        [alternative] = contested["alternatives"]
        assert alternative["assigned_formula"] == "C7H11NO3"
        assert alternative["target_ion_id"] == "ion2"
        assert alternative["ionization_mechanism_id"] == "mech-na"
        # 181.0665 sits one nominal mass above ion2's own M0 at 180.0631.
        assert alternative["isotope_label"] == "M+1"
        # ...and ion2's M0 is labelled as the main peak it is, on its own peak.
        assert by_peak["p0"]["isotope_label"] == "M0"

    def test_winner_is_not_listed_among_its_own_alternatives(self):
        # The reference mirror sits in the same frame as the curated library, so a
        # compound that is both a target and a known reference reaches one peak on
        # two rows. Excluding the winner by position alone kept the loser, which
        # carries the same formula and the same ion formula and so renders in the
        # inspector as the committed assignment offered as an alternative to itself.
        shared = dict(
            compound_formula="C10H16O3",
            ion_formula="C10H15O3",
            mz=183.1027,
            relative_abundance=1.0,
            sample_peak_id="p1",
            ionization="-H-",
        )
        df = pd.DataFrame(
            [
                _isotope_row(
                    target_isotope_id="iso1",
                    target_ion_id="ion1",
                    target_compound_id="cmp1",
                    match_score=0.95,
                    **shared,
                ),
                _reference_row(
                    target_isotope_id="refiso1",
                    target_ion_id="refion1",
                    match_score=0.80,
                    **shared,
                ),
                # A genuinely different formula on the same peak must survive.
                _isotope_row(
                    target_isotope_id="iso2",
                    target_ion_id="ion2",
                    target_compound_id="cmp2",
                    compound_formula="C9H12N2O2",
                    ion_formula="C9H11N2O2",
                    mz=183.1027,
                    relative_abundance=1.0,
                    sample_peak_id="p1",
                    match_score=0.60,
                    ionization="-H-",
                ),
            ]
        )

        [assignment] = invert_matches_to_peak_assignments(
            df,
            sample_item_id="s1",
            peak_assignment_run_id="run1",
            possible_threshold=POSSIBLE,
            probable_threshold=PROBABLE,
        )

        assert assignment["assigned_formula"] == "C10H16O3"
        assert [alt["assigned_formula"] for alt in assignment["alternatives"]] == [
            "C9H12N2O2"
        ]

    def test_only_alternative_being_the_winner_leaves_no_alternatives(self):
        # Nothing else contested the peak, so screening the twin out must leave the
        # field empty rather than an empty list.
        shared = dict(
            compound_formula="C10H16O3",
            ion_formula="C10H15O3",
            mz=183.1027,
            relative_abundance=1.0,
            sample_peak_id="p1",
            ionization="-H-",
        )
        df = pd.DataFrame(
            [
                _isotope_row(
                    target_isotope_id="iso1",
                    target_ion_id="ion1",
                    target_compound_id="cmp1",
                    match_score=0.95,
                    **shared,
                ),
                _reference_row(
                    target_isotope_id="refiso1",
                    target_ion_id="refion1",
                    match_score=0.80,
                    **shared,
                ),
            ]
        )

        [assignment] = invert_matches_to_peak_assignments(
            df,
            sample_item_id="s1",
            peak_assignment_run_id="run1",
            possible_threshold=POSSIBLE,
            probable_threshold=PROBABLE,
        )

        assert assignment["alternatives"] is None

    def test_same_formula_through_another_adduct_stays_an_alternative(self):
        # One formula seen through two ionization mechanisms is two arrivals of the
        # same compound, not the winner repeated: the screen keys on the mechanism
        # as well as the formula so this alternative survives.
        df = pd.DataFrame(
            [
                _isotope_row(
                    target_isotope_id="iso1",
                    target_ion_id="ion1",
                    target_compound_id="cmp1",
                    compound_formula="C10H16O3",
                    ion_formula="C10H15O3",
                    mz=183.1027,
                    relative_abundance=1.0,
                    sample_peak_id="p1",
                    match_score=0.95,
                    ionization="-H-",
                ),
                _isotope_row(
                    target_isotope_id="iso2",
                    target_ion_id="ion2",
                    target_compound_id="cmp1",
                    compound_formula="C10H16O3",
                    ion_formula="C10H16ClO3",
                    mz=183.1027,
                    relative_abundance=1.0,
                    sample_peak_id="p1",
                    match_score=0.70,
                    ionization="+Cl-",
                ),
            ]
        )

        [assignment] = invert_matches_to_peak_assignments(
            df,
            sample_item_id="s1",
            peak_assignment_run_id="run1",
            possible_threshold=POSSIBLE,
            probable_threshold=PROBABLE,
        )

        assert len(assignment["alternatives"]) == 1
        assert assignment["alternatives"][0]["ion_formula"] == "C10H16ClO3"

    def test_winner_twin_does_not_consume_an_alternatives_slot(self):
        # Screening happens before the cap, so a duplicate cannot displace a rival
        # the analyst would otherwise have seen.
        shared = dict(
            mz=183.1027,
            relative_abundance=1.0,
            sample_peak_id="p1",
            ionization="-H-",
        )
        df = pd.DataFrame(
            [
                _isotope_row(
                    target_isotope_id="iso1",
                    target_ion_id="ion1",
                    target_compound_id="cmp1",
                    compound_formula="C10H16O3",
                    ion_formula="C10H15O3",
                    match_score=0.95,
                    **shared,
                ),
                _reference_row(
                    target_isotope_id="refiso1",
                    target_ion_id="refion1",
                    compound_formula="C10H16O3",
                    ion_formula="C10H15O3",
                    match_score=0.90,
                    **shared,
                ),
                _isotope_row(
                    target_isotope_id="iso2",
                    target_ion_id="ion2",
                    target_compound_id="cmp2",
                    compound_formula="C9H12N2O2",
                    ion_formula="C9H11N2O2",
                    match_score=0.60,
                    **shared,
                ),
            ]
        )

        [assignment] = invert_matches_to_peak_assignments(
            df,
            sample_item_id="s1",
            peak_assignment_run_id="run1",
            possible_threshold=POSSIBLE,
            probable_threshold=PROBABLE,
            max_alternatives=1,
        )

        assert [alt["assigned_formula"] for alt in assignment["alternatives"]] == [
            "C9H12N2O2"
        ]

    def test_arbitration_demotes_implausible_higher_fit_winner(self):
        # C6H17NO4 is over-saturated (plausibility 0): despite the higher fit it must
        # lose the peak to the plausible glucose formula (evidence = fit x plausibility).
        match_df = pd.DataFrame(
            [
                _isotope_row(
                    target_isotope_id="isoX",
                    target_ion_id="ionX",
                    target_compound_id="cmpX",
                    compound_formula="C6H17NO4",
                    ion_formula="C6H18NO4+",
                    mz=180.1,
                    relative_abundance=1.0,
                    sample_peak_id="p1",
                    match_score=0.95,
                ),
                _isotope_row(
                    target_isotope_id="isoG",
                    target_ion_id="ionG",
                    target_compound_id="cmpG",
                    compound_formula="C6H12O6",
                    ion_formula="C6H13O6+",
                    mz=180.1,
                    relative_abundance=1.0,
                    sample_peak_id="p1",
                    match_score=0.85,
                ),
            ]
        )
        [winner] = invert_matches_to_peak_assignments(
            match_df, "sample1", "run1", POSSIBLE, PROBABLE
        )
        assert winner["assigned_formula"] == "C6H12O6"
        assert winner["fit_score"] == pytest.approx(0.85)  # its own pure fit
        assert winner["provenance"]["plausibility"] == pytest.approx(1.0)
        assert winner["provenance"]["confidence"] == pytest.approx(1.0)
        # the implausible formula survives as a runner-up, carrying its plausibility
        assert winner["alternatives"][0]["assigned_formula"] == "C6H17NO4"
        assert winner["alternatives"][0]["plausibility"] == pytest.approx(0.0)

    def test_provenance_carries_confidence_and_tie_flag(self):
        match_df = pd.DataFrame(
            [
                _isotope_row(
                    target_isotope_id="iso1",
                    target_ion_id="ion1",
                    target_compound_id="cmp1",
                    compound_formula="C6H12O6",
                    ion_formula="C6H13O6+",
                    mz=181.0707,
                    relative_abundance=1.0,
                    sample_peak_id="p1",
                    match_score=0.9,
                )
            ]
        )
        [a] = invert_matches_to_peak_assignments(
            match_df, "sample1", "run1", POSSIBLE, PROBABLE
        )
        assert a["provenance"]["confidence"] == pytest.approx(1.0)
        assert a["provenance"]["is_tie"] is False
        # provenance must be JSON-serializable (it lands in a JSON column) — guards
        # against numpy scalars (e.g. numpy.bool_) leaking in.
        assert json.loads(json.dumps(a["provenance"]))["is_tie"] is False
        assert isinstance(a["provenance"]["is_tie"], bool)

    def test_duplicate_formula_arrivals_do_not_split_confidence(self):
        """One formula via two adducts is one hypothesis, not two competitors.

        Uncollapsed, the two arrivals split the normalisation between
        themselves and tie against each other - a peak whose assignment is not
        in doubt reporting 50% confidence and an unresolved tie with itself.
        Delegating to arbitrate_candidates collapses them (issue #1731).
        """
        match_df = pd.DataFrame(
            [
                _isotope_row(
                    target_isotope_id="iso1",
                    target_ion_id="ion1",
                    target_compound_id="cmp1",
                    compound_formula="C6H12O6",
                    ion_formula="C6H13O6+",
                    mz=181.0707,
                    relative_abundance=1.0,
                    sample_peak_id="p1",
                    match_score=0.9,
                ),
                _isotope_row(
                    target_isotope_id="iso2",
                    target_ion_id="ion2",
                    target_compound_id="cmp1",
                    compound_formula="C6H12O6",
                    ion_formula="C6H16NO6+",
                    mz=181.0707,
                    relative_abundance=1.0,
                    sample_peak_id="p1",
                    match_score=0.85,
                ),
            ]
        )
        [a] = invert_matches_to_peak_assignments(
            match_df, "sample1", "run1", POSSIBLE, PROBABLE
        )
        assert a["provenance"]["confidence"] == pytest.approx(1.0)
        assert a["provenance"]["is_tie"] is False
        assert a["provenance"]["n_candidates"] == 1

    def test_low_evidence_candidates_resolve_when_clearly_separated(self):
        """The tie gap is relative to the best evidence, not one absolute width.

        Evidence 0.05 vs 0.02 is one candidate with 2.5x the support of the
        other - a resolved winner. The old inline copy compared against an
        absolute 0.1 gap, which called nearly everything at low evidence a tie.
        """
        match_df = pd.DataFrame(
            [
                _isotope_row(
                    target_isotope_id="iso1",
                    target_ion_id="ion1",
                    target_compound_id="cmp1",
                    compound_formula="C6H12O6",
                    ion_formula="C6H13O6+",
                    mz=181.0707,
                    relative_abundance=1.0,
                    sample_peak_id="p1",
                    match_score=0.05,
                ),
                _isotope_row(
                    target_isotope_id="iso3",
                    target_ion_id="ion2",
                    target_compound_id="cmp2",
                    compound_formula="C7H16O5",
                    ion_formula="C7H17O5+",
                    mz=181.0705,
                    relative_abundance=1.0,
                    sample_peak_id="p1",
                    match_score=0.02,
                ),
            ]
        )
        [a] = invert_matches_to_peak_assignments(
            match_df, "sample1", "run1", POSSIBLE, PROBABLE
        )
        assert a["assigned_formula"] == "C6H12O6"
        assert a["provenance"]["is_tie"] is False
        assert a["provenance"]["confidence"] == pytest.approx(0.05 / 0.07, abs=1e-3)

    def test_noise_level_candidates_still_tie_under_the_absolute_floor(self):
        """A big relative gap between two negligible evidences is still a tie.

        0.004 vs 0.001 is a 4x relative gap between candidates that both have
        essentially no support; the absolute floor keeps that an honest tie
        rather than a confidently resolved winner.
        """
        match_df = pd.DataFrame(
            [
                _isotope_row(
                    target_isotope_id="iso1",
                    target_ion_id="ion1",
                    target_compound_id="cmp1",
                    compound_formula="C6H12O6",
                    ion_formula="C6H13O6+",
                    mz=181.0707,
                    relative_abundance=1.0,
                    sample_peak_id="p1",
                    match_score=0.004,
                ),
                _isotope_row(
                    target_isotope_id="iso3",
                    target_ion_id="ion2",
                    target_compound_id="cmp2",
                    compound_formula="C7H16O5",
                    ion_formula="C7H17O5+",
                    mz=181.0705,
                    relative_abundance=1.0,
                    sample_peak_id="p1",
                    match_score=0.001,
                ),
            ]
        )
        [a] = invert_matches_to_peak_assignments(
            match_df, "sample1", "run1", POSSIBLE, PROBABLE
        )
        assert a["provenance"]["is_tie"] is True

    def test_lone_zero_evidence_candidate_reports_no_confidence(self):
        """Zero evidence means nothing to distinguish: confidence 0, tie True.

        Pins the library's zero-evidence semantics now that the engine
        delegates - a lone candidate with no evidence must not read as a
        confident (1.0) assignment.
        """
        match_df = pd.DataFrame(
            [
                _isotope_row(
                    target_isotope_id="iso1",
                    target_ion_id="ion1",
                    target_compound_id="cmp1",
                    compound_formula="C6H12O6",
                    ion_formula="C6H13O6+",
                    mz=181.0707,
                    relative_abundance=1.0,
                    sample_peak_id="p1",
                    match_score=0.0,
                )
            ]
        )
        [a] = invert_matches_to_peak_assignments(
            match_df, "sample1", "run1", POSSIBLE, PROBABLE
        )
        assert a["provenance"]["confidence"] == pytest.approx(0.0)
        assert a["provenance"]["is_tie"] is True
        assert isinstance(a["provenance"]["is_tie"], bool)

    def test_calibrated_p_correct_for_known_instrument(self):
        match_df = pd.DataFrame(
            [
                _isotope_row(
                    target_isotope_id="iso1",
                    target_ion_id="ion1",
                    target_compound_id="cmp1",
                    compound_formula="C6H12O6",
                    ion_formula="C6H13O6+",
                    mz=181.0707,
                    relative_abundance=1.0,
                    sample_peak_id="p1",
                    match_score=0.9,
                )
            ]
        )
        [a] = invert_matches_to_peak_assignments(
            match_df, "sample1", "run1", POSSIBLE, PROBABLE, instrument="orbi"
        )
        prov = a["provenance"]
        assert prov["calibrated"] is True
        assert 0.0 <= prov["p_correct"] <= 1.0
        assert prov["calibration"]["instrument"] == "orbi"
        assert prov["calibration"]["provisional"] is True
        # fully JSON-serializable
        json.dumps(prov)

    def test_adduct_corroboration_lifts_p_correct(self):
        # Same compound assigned via two adducts (+H+ and the distinctive +Br-): the +H+
        # winner is corroborated by the +Br- (weight ~2.28) and should get a p_correct boost
        # + corroboration provenance; the +Br- winner is corroborated only by the generic +H+
        # (weight 0), so it gets no lift. Both share evidence (same fit + formula).
        match_df = pd.DataFrame(
            [
                _isotope_row(
                    target_isotope_id="isoA",
                    target_ion_id="ionA",
                    target_compound_id="cmp1",
                    compound_formula="C6H12O6",
                    ion_formula="C6H13O6+",
                    mz=181.0707,
                    relative_abundance=1.0,
                    sample_peak_id="p1",
                    match_score=0.9,
                    ionization="+H+",
                ),
                _isotope_row(
                    target_isotope_id="isoB",
                    target_ion_id="ionB",
                    target_compound_id="cmp1",
                    compound_formula="C6H12O6",
                    ion_formula="C6H12BrO6-",
                    mz=258.9823,
                    relative_abundance=1.0,
                    sample_peak_id="p2",
                    match_score=0.9,
                    ionization="+Br-",
                ),
            ]
        )
        out = invert_matches_to_peak_assignments(
            match_df, "sample1", "run1", POSSIBLE, PROBABLE, instrument="orbi"
        )
        by_peak = {a["sample_peak_id"]: a for a in out}
        prot, brom = by_peak["p1"], by_peak["p2"]
        # the protonated winner is lifted by the co-occurring bromide
        assert prot["provenance"]["corroboration"]["n_adducts"] == 2
        assert set(prot["provenance"]["corroboration"]["adducts"]) == {"+H+", "+Br-"}
        assert prot["provenance"]["corroboration"]["boost"] > 0
        assert prot["provenance"]["p_correct"] > brom["provenance"]["p_correct"]
        json.dumps(prot["provenance"])  # still serializable

    def test_no_corroboration_for_single_adduct_compound(self):
        match_df = pd.DataFrame(
            [
                _isotope_row(
                    target_isotope_id="iso1",
                    target_ion_id="ion1",
                    target_compound_id="cmp1",
                    compound_formula="C6H12O6",
                    ion_formula="C6H13O6+",
                    mz=181.0707,
                    relative_abundance=1.0,
                    sample_peak_id="p1",
                    match_score=0.9,
                    ionization="+H+",
                )
            ]
        )
        [a] = invert_matches_to_peak_assignments(
            match_df, "sample1", "run1", POSSIBLE, PROBABLE, instrument="orbi"
        )
        assert "corroboration" not in a["provenance"]

    def test_explicit_calibration_overrides_the_registry(self):
        # the service passes a calibration from the D6 store; passing None forces uncalibrated
        # even for an instrument the in-code registry would calibrate.
        match_df = pd.DataFrame(
            [
                _isotope_row(
                    target_isotope_id="iso1",
                    target_ion_id="ion1",
                    target_compound_id="cmp1",
                    compound_formula="C6H12O6",
                    ion_formula="C6H13O6+",
                    mz=181.0707,
                    relative_abundance=1.0,
                    sample_peak_id="p1",
                    match_score=0.9,
                )
            ]
        )
        [a] = invert_matches_to_peak_assignments(
            match_df,
            "sample1",
            "run1",
            POSSIBLE,
            PROBABLE,
            instrument="orbi",
            calibration=None,
        )
        assert a["provenance"]["calibrated"] is False
        assert a["provenance"]["p_correct"] is None

    def test_uncalibrated_instrument_reports_no_probability(self):
        # TOF has no calibration yet -> p_correct null, calibrated False (never a
        # borrowed Orbitrap number).
        match_df = pd.DataFrame(
            [
                _isotope_row(
                    target_isotope_id="iso1",
                    target_ion_id="ion1",
                    target_compound_id="cmp1",
                    compound_formula="C6H12O6",
                    ion_formula="C6H13O6+",
                    mz=181.0707,
                    relative_abundance=1.0,
                    sample_peak_id="p1",
                    match_score=0.9,
                )
            ]
        )
        [a] = invert_matches_to_peak_assignments(
            match_df, "sample1", "run1", POSSIBLE, PROBABLE, instrument="tof"
        )
        assert a["provenance"]["calibrated"] is False
        assert a["provenance"]["p_correct"] is None
        assert a["provenance"]["calibration"] is None

    def test_isotope_child_points_at_its_ions_m0_assignment(self):
        match_df = pd.DataFrame(
            [
                _isotope_row(
                    target_isotope_id="iso1",
                    target_ion_id="ion1",
                    target_compound_id="cmp1",
                    compound_formula="C6H12O6",
                    ion_formula="C6H13O6+",
                    mz=181.0707,
                    relative_abundance=1.0,
                    sample_peak_id="p1",
                    match_score=0.95,
                ),
                _isotope_row(
                    target_isotope_id="iso2",
                    target_ion_id="ion1",
                    target_compound_id="cmp1",
                    compound_formula="C6H12O6",
                    ion_formula="C6H13O6+",
                    mz=182.0741,
                    relative_abundance=0.065,
                    sample_peak_id="p2",
                    match_score=0.85,
                ),
            ]
        )

        assignments = invert_matches_to_peak_assignments(
            match_df, "sample1", "run1", POSSIBLE, PROBABLE
        )
        by_peak = {a["sample_peak_id"]: a for a in assignments}

        m0 = by_peak["p1"]
        child = by_peak["p2"]
        assert m0["role"] == ROLE_M0
        assert m0["isotope_label"] == "M0"
        assert m0["owner_peak_assignment_id"] is None
        assert child["role"] == ROLE_ISO_CHILD
        assert child["isotope_label"] == "M+1"
        assert child["owner_peak_assignment_id"] == m0["peak_assignment_id"]

    def test_child_owner_stays_none_when_ions_m0_lost_its_peak(self):
        # ion1's M0 loses peak p1 to ion2, but ion1's M+1 still wins p2:
        # the child cannot be attributed to an M0 assignment of its own ion.
        match_df = pd.DataFrame(
            [
                _isotope_row(
                    target_isotope_id="iso1",
                    target_ion_id="ion1",
                    target_compound_id="cmp1",
                    compound_formula="C6H12O6",
                    ion_formula="C6H13O6+",
                    mz=181.0707,
                    relative_abundance=1.0,
                    sample_peak_id="p1",
                    match_score=0.6,
                ),
                _isotope_row(
                    target_isotope_id="iso2",
                    target_ion_id="ion1",
                    target_compound_id="cmp1",
                    compound_formula="C6H12O6",
                    ion_formula="C6H13O6+",
                    mz=182.0741,
                    relative_abundance=0.065,
                    sample_peak_id="p2",
                    match_score=0.75,
                ),
                _isotope_row(
                    target_isotope_id="iso3",
                    target_ion_id="ion2",
                    target_compound_id="cmp2",
                    compound_formula="C7H16O5",
                    ion_formula="C7H17O5+",
                    mz=181.0705,
                    relative_abundance=1.0,
                    sample_peak_id="p1",
                    match_score=0.9,
                ),
            ]
        )

        assignments = invert_matches_to_peak_assignments(
            match_df, "sample1", "run1", POSSIBLE, PROBABLE
        )
        by_peak = {a["sample_peak_id"]: a for a in assignments}

        assert by_peak["p1"]["target_ion_id"] == "ion2"
        child = by_peak["p2"]
        assert child["target_ion_id"] == "ion1"
        assert child["role"] == ROLE_ISO_CHILD
        assert child["owner_peak_assignment_id"] is None
        assert child["tier"] == TIER_CANDIDATE

    def test_unmatched_isotopes_are_ignored(self):
        match_df = pd.DataFrame(
            [
                _isotope_row(
                    target_isotope_id="iso1",
                    target_ion_id="ion1",
                    target_compound_id="cmp1",
                    compound_formula="C6H12O6",
                    ion_formula="C6H13O6+",
                    mz=181.0707,
                    relative_abundance=1.0,
                    sample_peak_id="",  # matcher placeholder for unmatched
                    match_score=0.0,
                ),
            ]
        )
        assert (
            invert_matches_to_peak_assignments(
                match_df, "sample1", "run1", POSSIBLE, PROBABLE
            )
            == []
        )

    def test_out_of_tolerance_pairing_does_not_claim_its_peak(self):
        # A trace isotopologue paired to a peak within the wide 0.5 Da search window but
        # out of tolerance (apply_match_params zeroed its intensity) must NOT own that
        # peak - it belongs to another compound and should fall through to Stage B.
        match_df = pd.DataFrame(
            [
                _isotope_row(  # in-tolerance M0 -> owns its peak
                    target_isotope_id="iso1",
                    target_ion_id="ion1",
                    target_compound_id="cmp1",
                    compound_formula="C6H12O6",
                    ion_formula="C6H13O6+",
                    mz=181.0707,
                    relative_abundance=1.0,
                    sample_peak_id="pGood",
                    sample_peak_intensity=1000.0,
                    match_score=0.95,
                ),
                _isotope_row(  # trace isotopologue, 40 ppm off -> gated to intensity 0
                    target_isotope_id="iso2",
                    target_ion_id="ion1",
                    target_compound_id="cmp1",
                    compound_formula="C6H12O6",
                    ion_formula="C6H13O6+",
                    mz=182.0741,
                    relative_abundance=0.06,
                    sample_peak_id="pStolen",
                    sample_peak_intensity=0.0,  # apply_match_params zeroed (out of tol)
                    match_score=0.95,  # ion fit carried onto the row
                    match_mz_error=40.0,
                ),
            ]
        )
        assignments = invert_matches_to_peak_assignments(
            match_df, "sample1", "run1", POSSIBLE, PROBABLE
        )
        peaks = {a["sample_peak_id"] for a in assignments}
        assert peaks == {"pGood"}  # the stolen peak is released, not owned

    def test_alternatives_are_capped(self):
        # One formula per contender: rows repeating the winner's formula through
        # the winner's mechanism are a single hypothesis and are screened out
        # before the cap is applied, so a field of six identical formulas would
        # leave nothing to cap in the first place.
        contenders = [
            ("C6H12O6", "C6H13O6+"),
            ("C7H14O6", "C7H15O6+"),
            ("C8H16O6", "C8H17O6+"),
            ("C9H18O6", "C9H19O6+"),
            ("C10H20O6", "C10H21O6+"),
            ("C11H22O6", "C11H23O6+"),
        ]
        rows = [
            _isotope_row(
                target_isotope_id=f"iso{i}",
                target_ion_id=f"ion{i}",
                target_compound_id=f"cmp{i}",
                compound_formula=compound_formula,
                ion_formula=ion_formula,
                mz=181.0707,
                relative_abundance=1.0,
                sample_peak_id="p1",
                match_score=0.9 - i * 0.05,
            )
            for i, (compound_formula, ion_formula) in enumerate(contenders)
        ]
        assignments = invert_matches_to_peak_assignments(
            pd.DataFrame(rows),
            "sample1",
            "run1",
            POSSIBLE,
            PROBABLE,
            max_alternatives=2,
        )
        assert len(assignments) == 1
        assert len(assignments[0]["alternatives"]) == 2


class TestScoreIonsByFit:
    """Stage A deliberately scores with the ion-level fit score (score_pattern_v2)
    instead of the targeted matcher's per-isotopologue term."""

    @staticmethod
    def _iso(
        ion: str,
        rel: float,
        mz_err: float | None,
        intensity: float,
        score: float,
        snr: float | None = None,
        peak_id: str = "p",
    ) -> dict:
        return {
            "target_ion_id": ion,
            "relative_abundance": rel,
            "match_mz_error": mz_err,
            "sample_peak_intensity": intensity,
            "match_score": score,
            "signal_to_noise": snr,
            "sample_peak_id": peak_id,
        }

    def test_noop_when_required_columns_missing(self):
        df = pd.DataFrame({"target_ion_id": ["a"], "match_score": [0.5]})
        out = score_ions_by_fit(df)
        assert out["match_score"].tolist() == [0.5]

    def test_empty_frame_returned_unchanged(self):
        assert score_ions_by_fit(pd.DataFrame()).empty

    def test_every_isotopologue_of_an_ion_shares_its_fit_score(self):
        df = pd.DataFrame(
            [
                self._iso("ion1", 1.0, 0.5, 1000.0, 0.9, snr=50.0, peak_id="a"),
                self._iso("ion1", 0.5, 0.5, 500.0, 0.85, snr=30.0, peak_id="b"),
            ]
        )
        out = score_ions_by_fit(df)
        scores = out["match_score"].tolist()
        assert scores[0] == pytest.approx(scores[1])
        assert 0.0 < scores[0] <= 1.0

    def test_gated_out_monoisotopic_scores_zero(self):
        # apply_match_params zeroes an out-of-tolerance isotopologue's match_score;
        # the fit score must then treat it as absent (M0 absent -> fit 0).
        df = pd.DataFrame(
            [self._iso("ion1", 1.0, 0.5, 1000.0, 0.0, snr=50.0, peak_id="a")]
        )
        out = score_ions_by_fit(df)
        assert out["match_score"].tolist() == [0.0]

    def test_corroborated_envelope_beats_missing_detectable_sibling(self):
        df = pd.DataFrame(
            [
                # full: M0 + a matched M+1
                self._iso("full", 1.0, 0.5, 1000.0, 0.9, snr=50.0, peak_id="a"),
                self._iso("full", 0.5, 0.5, 500.0, 0.85, snr=30.0, peak_id="b"),
                # miss: M0 matched, its predicted M+1 detectable but absent
                self._iso("miss", 1.0, 0.5, 1000.0, 0.9, snr=50.0, peak_id="c"),
                self._iso("miss", 0.5, None, float("nan"), 0.0, snr=None, peak_id=""),
            ]
        )
        out = score_ions_by_fit(df)
        fit = out.groupby("target_ion_id")["match_score"].first()
        assert fit["full"] > fit["miss"]


class TestUntargetedMatches:
    def _matches_df(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "mz": 100.1,
                    "formula": "C5H10O2",
                    "ion": "C5H11O2+",
                    "isotope_label": "M0",
                    "ionization_mechanism": "+H+",
                    "mz_error_ppm": 2.0,
                    "intensity_error": 0.1,
                    "other_candidates": "C4H8N2O, C6H14N",
                    "neutral_mass": 102.068,
                    "unsaturation": 1.0,
                },
                {
                    "mz": 101.1033,
                    "formula": "C5H10O2",
                    "ion": "[13C]C4H11O2+",
                    "isotope_label": "13C",
                    "ionization_mechanism": "+H+",
                    "mz_error_ppm": 3.0,
                    "intensity_error": 0.2,
                    "other_candidates": "",
                    "neutral_mass": 103.071,
                    "unsaturation": 1.0,
                },
                {
                    "mz": 102.2,
                    "formula": "---",
                    "ion": "---",
                    "isotope_label": "---",
                    "other_candidates": "",
                },
            ]
        )

    def _peaks_df(self) -> pd.DataFrame:
        # The peaks fed to the untargeted search, in the order they were fed.
        # Results join back to this frame by position, not by float m/z equality.
        return pd.DataFrame(
            {
                "sample_peak_id": ["pA", "pB", "pC"],
                "mz": [100.1, 101.1033, 102.2],
                "intensity": [5000.0, 300.0, 50.0],
            }
        )

    def _one_peak_df(self, peak_id: str, mz: float, intensity: float) -> pd.DataFrame:
        return pd.DataFrame(
            {"sample_peak_id": [peak_id], "mz": [mz], "intensity": [intensity]}
        )

    def _untargeted_row(self, **overrides) -> dict:
        """One composition-finder result row, all contending for the same peak."""
        return {
            "mz": 100.1,
            "formula": "C5H10O2",
            "ion": "C5H11O2+",
            "isotope_label": "M0",
            "ionization_mechanism": "+H+",
            "mz_error_ppm": 2.0,
            "intensity_error": 0.1,
            "other_candidates": "",
            "neutral_mass": 102.068,
            "unsaturation": 1.0,
        } | overrides

    def test_winner_formula_in_the_finder_shortlist_is_not_an_alternative(self):
        # The finder freezes `other_candidates` before the heuristic filter and the
        # isotope-pattern ranking choose the winner, so a stored result can name the
        # winning formula among the peak's "other" candidates. Extending that into
        # `alternatives` put the committed assignment in its own shortlist.
        assignments = untargeted_matches_to_peak_assignments(
            pd.DataFrame([self._untargeted_row(other_candidates="C5H10O2, C4H8N2O")]),
            self._one_peak_df("pA", 100.1, 5000.0),
            "sample1",
            "run1",
            POSSIBLE,
            PROBABLE,
        )

        [assignment] = assignments
        assert assignment["assigned_formula"] == "C5H10O2"
        assert [alt["assigned_formula"] for alt in assignment["alternatives"]] == [
            "C4H8N2O"
        ]

    def test_shortlist_naming_only_the_winner_leaves_no_alternatives(self):
        assignments = untargeted_matches_to_peak_assignments(
            pd.DataFrame([self._untargeted_row(other_candidates="C5H10O2")]),
            self._one_peak_df("pA", 100.1, 5000.0),
            "sample1",
            "run1",
            POSSIBLE,
            PROBABLE,
        )

        assert assignments[0]["alternatives"] is None

    def test_losing_contender_restating_the_winner_is_not_an_alternative(self):
        # Two finder rows can land on one observed peak. One reaching the winner's
        # formula through the winner's mechanism is the same explanation arriving
        # twice; a different formula is a real rival and stays.
        assignments = untargeted_matches_to_peak_assignments(
            pd.DataFrame(
                [
                    self._untargeted_row(),
                    self._untargeted_row(mz_error_ppm=5.0, intensity_error=0.3),
                    self._untargeted_row(
                        formula="C4H8N2O",
                        ion="C4H9N2O+",
                        mz_error_ppm=8.0,
                        intensity_error=0.4,
                    ),
                ]
            ),
            self._one_peak_df("pA", 100.1, 5000.0),
            "sample1",
            "run1",
            POSSIBLE,
            PROBABLE,
        )

        [assignment] = assignments
        assert assignment["assigned_formula"] == "C5H10O2"
        assert [alt["assigned_formula"] for alt in assignment["alternatives"]] == [
            "C4H8N2O"
        ]

    def test_same_formula_through_another_mechanism_stays_an_alternative(self):
        # The screen keys on the mechanism too, so one composition seen through two
        # ionizations keeps the loser as the distinct alternative it is.
        assignments = untargeted_matches_to_peak_assignments(
            pd.DataFrame(
                [
                    self._untargeted_row(),
                    self._untargeted_row(
                        ion="C5H10NaO2+",
                        ionization_mechanism="+Na+",
                        mz_error_ppm=5.0,
                        intensity_error=0.3,
                    ),
                ]
            ),
            self._one_peak_df("pA", 100.1, 5000.0),
            "sample1",
            "run1",
            POSSIBLE,
            PROBABLE,
        )

        [assignment] = assignments
        assert len(assignment["alternatives"]) == 1
        assert assignment["alternatives"][0]["ion_formula"] == "C5H10NaO2+"

    def test_assigned_rows_map_to_assignments_and_placeholder_is_skipped(self):
        assignments = untargeted_matches_to_peak_assignments(
            self._matches_df(),
            self._peaks_df(),
            "sample1",
            "run1",
            POSSIBLE,
            PROBABLE,
            mechanism_id_by_notation={"+H+": "mech1"},
        )

        assert len(assignments) == 2
        by_peak = {a["sample_peak_id"]: a for a in assignments}
        assert set(by_peak) == {"pA", "pB"}

        m0 = by_peak["pA"]
        assert m0["role"] == ROLE_M0
        assert m0["source"] == SOURCE_UNTARGETED
        assert m0["assigned_formula"] == "C5H10O2"
        assert m0["ionization_mechanism_id"] == "mech1"
        assert m0["target_compound_id"] is None
        # score = (1 - 0.1) * (1 - 2.0/100)
        assert m0["fit_score"] == pytest.approx(0.9 * 0.98)
        assert m0["tier"] == TIER_ASSIGNED
        assert [alt["assigned_formula"] for alt in m0["alternatives"]] == [
            "C4H8N2O",
            "C6H14N",
        ]
        # untargeted runner-ups are formula-only, but still carry plausibility
        assert all(alt["plausibility"] is not None for alt in m0["alternatives"])
        # ...and nothing else. The finder's other_candidates shortlist resolved no
        # adduct for them, so there is no mechanism id to record - which is why
        # curation refuses to commit one of these rather than write an assignment
        # whose verification identity (peak + formula + mechanism) is half missing.
        assert all(
            alt.get("ionization_mechanism_id") is None for alt in m0["alternatives"]
        )
        assert m0["provenance"]["neutral_mass"] == pytest.approx(102.068)
        # Chemical plausibility now rides on untargeted winners too (previously
        # database-stage only), so an assigned de-novo formula reports it and
        # the inspector shows it consistently across stages.
        assert 0.0 <= m0["provenance"]["plausibility"] <= 1.0

    def test_scored_runner_up_carries_its_own_adduct_and_isotope_label(self):
        """A losing CONTENDER is a whole candidate, unlike the shortlist above.

        Two compositions explain the same observed peak; the loser was scored on
        that peak under a resolved ionization, so it records the mechanism id
        that ionization maps to - the runner-up a person can actually promote.
        Its isotope label rides along for the same reason a Stage A runner-up's
        does: promoting a 13C child as though it were an M0 would enter a
        compound's satellite into the ledger as the compound.
        """
        matches_df = pd.DataFrame(
            [
                {
                    "mz": 100.1,
                    "formula": "C5H10O2",
                    "ion": "C5H11O2+",
                    "isotope_label": "M0",
                    "ionization_mechanism": "+H+",
                    "mz_error_ppm": 1.0,
                    "intensity_error": 0.05,
                    "other_candidates": "",
                },
                {
                    # A second composition's 13C child, sodiated, landing on the
                    # same peak and losing it on evidence.
                    "mz": 100.1,
                    "formula": "C4H8N2O",
                    "ion": "[13C]C3H8N2NaO+",
                    "isotope_label": "13C",
                    "ionization_mechanism": "+Na+",
                    "mz_error_ppm": 12.0,
                    "intensity_error": 0.4,
                    "other_candidates": "",
                },
            ]
        )
        [assignment] = untargeted_matches_to_peak_assignments(
            matches_df,
            self._one_peak_df("pA", 100.1, 5000.0),
            "sample1",
            "run1",
            POSSIBLE,
            PROBABLE,
            mechanism_id_by_notation={"+H+": "mech1", "+Na+": "mech2"},
        )

        assert assignment["assigned_formula"] == "C5H10O2"
        assert assignment["ionization_mechanism_id"] == "mech1"
        [alternative] = assignment["alternatives"]
        assert alternative["assigned_formula"] == "C4H8N2O"
        assert alternative["source"] == SOURCE_UNTARGETED
        assert alternative["ionization_mechanism_id"] == "mech2"
        assert alternative["isotope_label"] == "13C"
        # fit = (1 - 0.4) * (1 - 12.0/100), well under the winner's
        assert alternative["fit_score"] == pytest.approx(0.6 * 0.88)

    def test_isotopic_pattern_fit_score_is_used_when_present(self):
        # When assign_compositions scored the whole envelope, Stage B uses that
        # fit score, not the crude single-peak abundance*mz term.
        matches_df = pd.DataFrame(
            [
                {
                    "mz": 100.1,
                    "formula": "C5H10O2",
                    "ion": "C5H11O2+",
                    "isotope_label": "M0",
                    "ionization_mechanism": "+H+",
                    "mz_error_ppm": 2.0,
                    "intensity_error": 0.1,
                    "isotopic_pattern_score": 0.42,
                    "other_candidates": "",
                }
            ]
        )
        assignments = untargeted_matches_to_peak_assignments(
            matches_df,
            self._one_peak_df("pA", 100.1, 5000.0),
            "sample1",
            "run1",
            POSSIBLE,
            PROBABLE,
            mechanism_id_by_notation={"+H+": "mech1"},
        )
        assert len(assignments) == 1
        # uses the envelope fit (0.42), not the inline 0.9 * 0.98
        assert assignments[0]["fit_score"] == pytest.approx(0.42)
        assert assignments[0]["tier"] == TIER_BELOW_ASSIGNABILITY

    def test_signed_abundance_error_is_persisted_unchanged(self):
        # The finder's intensity_error is the signed relative error
        # (observed/predicted - 1), the same convention as the targeted
        # matcher's match_abundance_error. The inspector recovers the
        # predicted relative abundance as observed_rel / (1 + error), so the
        # sign must survive persistence; only the score works on |error|.
        matches_df = pd.DataFrame(
            [
                {
                    "mz": 100.1,
                    "formula": "C5H10O2",
                    "ion": "C5H11O2+",
                    "isotope_label": "M0",
                    "ionization_mechanism": "+H+",
                    "mz_error_ppm": 2.0,
                    "intensity_error": -0.3,
                    "other_candidates": "",
                }
            ]
        )
        assignments = untargeted_matches_to_peak_assignments(
            matches_df,
            self._one_peak_df("pA", 100.1, 5000.0),
            "sample1",
            "run1",
            POSSIBLE,
            PROBABLE,
            mechanism_id_by_notation={"+H+": "mech1"},
        )
        assert len(assignments) == 1
        assert assignments[0]["abundance_error"] == pytest.approx(-0.3)
        # fallback score = (1 - |-0.3|) * (1 - 2.0/100)
        assert assignments[0]["fit_score"] == pytest.approx(0.7 * 0.98)

    def test_signed_mz_error_is_persisted_unchanged(self):
        # mz_error_ppm is signed too ((observed - predicted)/predicted * 1e6), the
        # same convention as the targeted matcher's match_mz_error. A peak measured
        # BELOW its prediction persists a negative ppm error: the spectrum chart
        # recovers the theoretical m/z as observed / (1 + ppm/1e6) and the inspector
        # shows the sign, while only the score works on |ppm|.
        matches_df = pd.DataFrame(
            [
                {
                    "mz": 100.1,
                    "formula": "C5H10O2",
                    "ion": "C5H11O2+",
                    "isotope_label": "M0",
                    "ionization_mechanism": "+H+",
                    "mz_error_ppm": -2.0,
                    "intensity_error": 0.1,
                    "other_candidates": "",
                }
            ]
        )
        assignments = untargeted_matches_to_peak_assignments(
            matches_df,
            self._one_peak_df("pA", 100.1, 5000.0),
            "sample1",
            "run1",
            POSSIBLE,
            PROBABLE,
            mechanism_id_by_notation={"+H+": "mech1"},
        )
        assert len(assignments) == 1
        assert assignments[0]["mz_error_ppm"] == pytest.approx(-2.0)
        # fallback score = (1 - 0.1) * (1 - |-2.0|/100)
        assert assignments[0]["fit_score"] == pytest.approx(0.9 * 0.98)

    def test_negative_composition_error_fallback_is_persisted_signed(self):
        # The fallback source is signed as well, so a row with no isotope-envelope
        # error still persists which side of its prediction the peak sits on.
        matches_df = pd.DataFrame(
            [
                {
                    "mz": 100.1,
                    "formula": "C5H10O2",
                    "ion": "C5H11O2+",
                    "isotope_label": "M0",
                    "ionization_mechanism": "+H+",
                    "mz_error_ppm": float("nan"),
                    "composition_error_ppm": -2.0,
                    "intensity_error": 0.1,
                    "other_candidates": "",
                }
            ]
        )
        assignments = untargeted_matches_to_peak_assignments(
            matches_df,
            self._one_peak_df("pA", 100.1, 5000.0),
            "sample1",
            "run1",
            POSSIBLE,
            PROBABLE,
            mechanism_id_by_notation={"+H+": "mech1"},
        )
        assert len(assignments) == 1
        assert assignments[0]["mz_error_ppm"] == pytest.approx(-2.0)
        assert assignments[0]["fit_score"] == pytest.approx(0.9 * 0.98)

    def test_isotope_child_is_attributed_to_its_formula_group_m0(self):
        assignments = untargeted_matches_to_peak_assignments(
            self._matches_df(),
            self._peaks_df(),
            "sample1",
            "run1",
            POSSIBLE,
            PROBABLE,
        )
        by_peak = {a["sample_peak_id"]: a for a in assignments}
        child = by_peak["pB"]
        assert child["role"] == ROLE_ISO_CHILD
        assert child["isotope_label"] == "13C"
        assert child["owner_peak_assignment_id"] == by_peak["pA"]["peak_assignment_id"]

    def test_rows_without_observed_peak_are_skipped(self):
        assignments = untargeted_matches_to_peak_assignments(
            self._matches_df(),
            # Only pA was fed to the search, so pB's row has no peak to own.
            self._one_peak_df("pA", 100.1, 5000.0),
            "sample1",
            "run1",
            POSSIBLE,
            PROBABLE,
        )
        assert [a["sample_peak_id"] for a in assignments] == ["pA"]

    def test_formula_formatter_is_applied(self):
        assignments = untargeted_matches_to_peak_assignments(
            self._matches_df(),
            self._peaks_df(),
            "sample1",
            "run1",
            POSSIBLE,
            PROBABLE,
            formula_formatter=lambda formula: f"fmt({formula})",
        )
        by_peak = {a["sample_peak_id"]: a for a in assignments}
        assert by_peak["pA"]["assigned_formula"] == "fmt(C5H10O2)"
        assert by_peak["pA"]["alternatives"][0]["assigned_formula"] == ("fmt(C4H8N2O)")

    def test_nan_mz_error_falls_back_to_composition_error(self):
        # A NaN mz_error_ppm (present column, no isotope-envelope error for this
        # row) must fall back to composition_error_ppm, not collapse the score.
        matches_df = pd.DataFrame(
            [
                {
                    "mz": 100.1,
                    "formula": "C5H10O2",
                    "ion": "C5H11O2+",
                    "isotope_label": "M0",
                    "ionization_mechanism": "+H+",
                    "mz_error_ppm": float("nan"),
                    "composition_error_ppm": 2.0,
                    "intensity_error": 0.1,
                    "other_candidates": "",
                }
            ]
        )
        assignments = untargeted_matches_to_peak_assignments(
            matches_df,
            self._one_peak_df("pA", 100.1, 5000.0),
            "sample1",
            "run1",
            POSSIBLE,
            PROBABLE,
            mechanism_id_by_notation={"+H+": "mech1"},
        )
        assert len(assignments) == 1
        # score = (1 - 0.1) * (1 - 2.0/100), not 0 from a collapsed mz term.
        assert assignments[0]["fit_score"] == pytest.approx(0.9 * 0.98)
        assert assignments[0]["tier"] == TIER_ASSIGNED

    def test_ionization_placeholder_formula_is_skipped(self):
        # The finder emits "()" for reagent/ionization peaks; it is not a
        # molecular formula and must not be persisted as one.
        matches_df = pd.DataFrame(
            [
                {
                    "mz": 19.018,
                    "formula": "()",
                    "ion": "H3O+",
                    "isotope_label": "M0",
                    "ionization_mechanism": "+H+",
                    "mz_error_ppm": 1.0,
                    "intensity_error": 0.0,
                    "other_candidates": "",
                }
            ]
        )
        assignments = untargeted_matches_to_peak_assignments(
            matches_df,
            self._one_peak_df("pR", 19.018, 1000.0),
            "sample1",
            "run1",
            POSSIBLE,
            PROBABLE,
        )
        assert assignments == []


class TestBuildUnassigned:
    def test_every_leftover_peak_gets_a_placeholder_row(self):
        peaks_df = pd.DataFrame(
            {
                "sample_peak_id": ["p9", "p10"],
                "mz": [55.05, 56.06],
                "intensity": [12.0, 0.0],
            }
        )
        rows = build_unassigned_assignments(peaks_df, "sample1", "run1")

        assert len(rows) == 2
        for row in rows:
            assert row["role"] == ROLE_UNASSIGNED
            assert row["tier"] == TIER_UNASSIGNED
            assert row["source"] is None
            assert row["assigned_formula"] is None
            assert row["peak_assignment_run_id"] == "run1"
            assert row["sample_item_id"] == "sample1"
        assert rows[0]["sample_peak_id"] == "p9"
        assert rows[0]["sample_peak_mz"] == pytest.approx(55.05)


_REF_IDENTITIES = [
    {
        "name": "Pinonic acid",
        "source": "pinene-tracers",
        "license": "custom",
        "inchikey": None,
        "source_native_id": "473-72-3",
        "xrefs": {"cas": "473-72-3"},
    }
]


def _reference_row(**kwargs) -> dict:
    """A Stage A match row sourced from the reference mirror (no curated target)."""
    row = _isotope_row(target_compound_id=None, **kwargs)
    row["reference_identities"] = _REF_IDENTITIES
    return row


class TestReferenceStageAInversion:
    """Reference-sourced Stage A winners: identity in provenance, null target FKs."""

    def test_reference_winner_persists_identity_and_nulls_target_fks(self):
        df = pd.DataFrame(
            [
                _reference_row(
                    target_isotope_id="refiso1",
                    target_ion_id="refion1",  # synthetic - must not persist as FK
                    compound_formula="C10H16O3",
                    ion_formula="C10H15O3",
                    mz=183.1027,
                    relative_abundance=1.0,
                    sample_peak_id="p1",
                    match_score=0.95,
                    ionization="-H-",
                )
            ]
        )
        [assignment] = invert_matches_to_peak_assignments(
            df,
            sample_item_id="s1",
            peak_assignment_run_id="run1",
            possible_threshold=POSSIBLE,
            probable_threshold=PROBABLE,
        )
        assert assignment["source"] == SOURCE_DATABASE
        assert assignment["assigned_formula"] == "C10H16O3"
        # Reference winners carry no curated target linkage...
        assert assignment["target_compound_id"] is None
        assert assignment["target_ion_id"] is None
        # ...but their one-to-many identity rides in provenance.
        assert assignment["provenance"]["reference_identities"] == _REF_IDENTITIES
        # JSON-serializable (the provenance column is JSON).
        json.dumps(assignment["provenance"])

    def test_target_winner_has_no_reference_identities(self):
        df = pd.DataFrame(
            [
                _isotope_row(
                    target_isotope_id="iso1",
                    target_ion_id="ion1",
                    target_compound_id="cmp1",
                    compound_formula="C6H12O6",
                    ion_formula="C6H11O6",
                    mz=179.0561,
                    relative_abundance=1.0,
                    sample_peak_id="p1",
                    match_score=0.9,
                    ionization="-H-",
                )
            ]
        )
        [assignment] = invert_matches_to_peak_assignments(
            df,
            sample_item_id="s1",
            peak_assignment_run_id="run1",
            possible_threshold=POSSIBLE,
            probable_threshold=PROBABLE,
        )
        assert assignment["target_compound_id"] == "cmp1"
        assert assignment["target_ion_id"] == "ion1"
        assert "reference_identities" not in assignment["provenance"]

    def test_reference_beats_target_on_same_peak_and_target_is_alternative(self):
        df = pd.DataFrame(
            [
                _reference_row(
                    target_isotope_id="refiso1",
                    target_ion_id="refion1",
                    compound_formula="C10H16O3",
                    ion_formula="C10H15O3",
                    mz=183.1027,
                    relative_abundance=1.0,
                    sample_peak_id="p1",
                    match_score=0.95,
                    ionization="-H-",
                    ionization_mechanism_id="mech-deprot",
                ),
                _isotope_row(
                    target_isotope_id="iso2",
                    target_ion_id="ion2",
                    target_compound_id="cmp2",
                    compound_formula="C9H12N2O2",
                    ion_formula="C9H11N2O2",
                    mz=183.1027,
                    relative_abundance=1.0,
                    sample_peak_id="p1",
                    match_score=0.60,
                    ionization="-H-",
                    ionization_mechanism_id="mech-deprot",
                ),
            ]
        )
        [assignment] = invert_matches_to_peak_assignments(
            df,
            sample_item_id="s1",
            peak_assignment_run_id="run1",
            possible_threshold=POSSIBLE,
            probable_threshold=PROBABLE,
        )
        # Higher-scoring reference candidate owns the peak, with identity.
        assert assignment["assigned_formula"] == "C10H16O3"
        assert assignment["target_ion_id"] is None
        assert assignment["provenance"]["reference_identities"] == _REF_IDENTITIES
        # The curated target drops to alternatives, keeping its own ids.
        alt = assignment["alternatives"][0]
        assert alt["assigned_formula"] == "C9H12N2O2"
        assert alt["target_ion_id"] == "ion2"
        # Both candidates were scored deprotonated, and the alternative says so:
        # the id comes off the mechanism column, not the "-H-" notation beside it.
        assert alt["ionization_mechanism_id"] == "mech-deprot"
        # Each is its ion's only isotope, so the runner-up is a main peak too.
        assert alt["isotope_label"] == "M0"

    def test_target_winner_inherits_reference_identity_for_shared_formula(self):
        # Convergence precedence: when the curated target and a reference compound
        # share the winning peak's formula, the target owns the FK but the known
        # name still rides alongside in provenance.
        df = pd.DataFrame(
            [
                _isotope_row(
                    target_isotope_id="iso1",
                    target_ion_id="ion1",
                    target_compound_id="cmp1",
                    compound_formula="C10H16O3",
                    ion_formula="C10H15O3",
                    mz=183.1027,
                    relative_abundance=1.0,
                    sample_peak_id="p1",
                    match_score=0.95,
                    ionization="-H-",
                ),
                _reference_row(
                    target_isotope_id="refiso1",
                    target_ion_id="refion1",
                    compound_formula="C10H16O3",
                    ion_formula="C10H15O3",
                    mz=183.1027,
                    relative_abundance=1.0,
                    sample_peak_id="p1",
                    match_score=0.80,
                    ionization="-H-",
                ),
            ]
        )
        [assignment] = invert_matches_to_peak_assignments(
            df,
            sample_item_id="s1",
            peak_assignment_run_id="run1",
            possible_threshold=POSSIBLE,
            probable_threshold=PROBABLE,
        )
        # Target owns the curated FK...
        assert assignment["target_compound_id"] == "cmp1"
        assert assignment["target_ion_id"] == "ion1"
        # ...and the reference name rides alongside.
        assert assignment["provenance"]["reference_identities"] == _REF_IDENTITIES
