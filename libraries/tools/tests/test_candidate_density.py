"""Unit tests for candidate density (step 2.4's first measurement).

Contract: report how many of a peak's candidates its own evidence cannot
separate, in the currency the arbitration already competes them in. 1 means the
winner stands alone; more is the size of the tie at the top. The count is over
distinct formulas, and it never claims uniqueness the measurement did not earn.
"""

import pytest

from mascope_tools.composition.arbitration import (
    DEFAULT_TIE_TOL,
    TIE_ABS_FLOOR,
    candidate_density,
)


class TestWhatTheCountMeans:
    def test_no_candidates_is_no_measurement(self):
        assert candidate_density([]) == 0

    def test_one_candidate_stands_alone(self):
        assert candidate_density([{"formula": "C6H12O6", "fit_score": 0.9}]) == 1

    def test_a_clear_winner_stands_alone(self):
        # 0.90 against 0.20: far outside the 10% gap, so nothing ties it.
        assert (
            candidate_density(
                [
                    {"formula": "C6H12O6", "fit_score": 0.90},
                    {"formula": "C5H8N2O4", "fit_score": 0.20},
                ]
            )
            == 1
        )

    def test_two_the_evidence_cannot_separate_are_counted_as_two(self):
        assert (
            candidate_density(
                [
                    {"formula": "C6H12O6", "fit_score": 0.90},
                    {"formula": "C5H8N2O4", "fit_score": 0.88},
                ]
            )
            == 2
        )

    def test_the_whole_tie_is_counted_not_just_the_runner_up(self):
        assert (
            candidate_density(
                [
                    {"formula": "C6H12O6", "fit_score": 0.90},
                    {"formula": "C5H8N2O4", "fit_score": 0.89},
                    {"formula": "C7H16O4", "fit_score": 0.88},
                    {"formula": "C3H4O4", "fit_score": 0.10},
                ]
            )
            == 3
        )


class TestWhatItRefusesToClaim:
    def test_no_evidence_at_all_is_not_uniqueness(self):
        # Nothing was measured, so nothing was separated: saying "unique" here
        # would be a claim the measurement did not make.
        assert (
            candidate_density(
                [
                    {"formula": "C6H12O6", "fit_score": 0.0},
                    {"formula": "C5H8N2O4", "fit_score": 0.0},
                ]
            )
            == 2
        )

    def test_a_chemically_impossible_rival_does_not_count(self):
        # C6H17NO4 is over-saturated, so its plausibility is 0 and its evidence
        # with it. Density counts hypotheses, and that is not one.
        assert (
            candidate_density(
                [
                    {"formula": "C6H12O6", "fit_score": 0.90},
                    {"formula": "C6H17NO4", "fit_score": 0.99},
                ]
            )
            == 1
        )


class TestOneHypothesisArrivingTwice:
    def test_the_same_formula_twice_is_one_candidate(self):
        # Two adducts of one neutral are not competitors; arbitration collapses
        # them, and a density that counted both would report a peak whose
        # assignment is not in doubt as an unresolved tie with itself.
        assert (
            candidate_density(
                [
                    {"formula": "C6H12O6", "fit_score": 0.90},
                    {"formula": "C6H12O6", "fit_score": 0.89},
                ]
            )
            == 1
        )


class TestTheGapItself:
    def test_the_gap_is_relative_to_the_best_evidence(self):
        # 0.90 vs 0.80 is 11% below the best - outside the default tolerance.
        candidates = [
            {"formula": "C6H12O6", "fit_score": 0.90},
            {"formula": "C5H8N2O4", "fit_score": 0.80},
        ]
        assert candidate_density(candidates) == 1
        assert candidate_density(candidates, tie_tol=0.2) == 2

    def test_the_default_tolerance_is_the_arbitration_s_own(self):
        assert 0.0 < DEFAULT_TIE_TOL < 1.0
        # Two candidates whose evidence is noise are not a resolved winner and a
        # loser, however large the relative gap between them; the absolute floor
        # is what says so.
        assert (
            candidate_density(
                [
                    {"formula": "C6H12O6", "fit_score": TIE_ABS_FLOOR / 2},
                    {"formula": "C5H8N2O4", "fit_score": TIE_ABS_FLOOR / 4},
                ]
            )
            == 2
        )


class TestTheInputShapes:
    @pytest.mark.parametrize(
        "candidates",
        [
            [("C6H12O6", 0.90), ("C5H8N2O4", 0.88)],
            [
                {"formula": "C6H12O6", "match_score": 0.90},
                {"formula": "C5H8N2O4", "match_score": 0.88},
            ],
        ],
    )
    def test_it_takes_what_the_arbitration_takes(self, candidates):
        assert candidate_density(candidates) == 2
