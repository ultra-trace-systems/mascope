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
    arbitrate_candidates,
    candidate_density,
    density_of,
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


class TestTheTwoRoutesAgree:
    """`candidate_density` counts in one pass; `density_of` reads the count off
    an arbitration a caller already has. They are two implementations of one
    number, so the only thing worth testing about them is that they agree."""

    @pytest.mark.parametrize(
        "fits",
        [
            [0.9],
            [0.9, 0.88],
            [0.9, 0.2],
            [0.9, 0.89, 0.88, 0.1],
            [0.0, 0.0],
            [0.0],
            [0.9, 0.9, 0.9],
            [0.005, 0.004, 0.001],
            [1.0, 0.9, 0.8, 0.7, 0.6, 0.5],
        ],
    )
    def test_on_a_range_of_shapes(self, fits):
        formulas = [
            "C6H12O6",
            "C5H8N2O4",
            "C7H16O4",
            "C3H4O4",
            "C9H12N2O2",
            "C4H8O2",
        ]
        candidates = [
            {"formula": formulas[i % len(formulas)], "fit_score": fit}
            for i, fit in enumerate(fits)
        ]
        assert candidate_density(candidates) == density_of(
            arbitrate_candidates(candidates)
        )

    def test_including_a_formula_arriving_twice(self):
        candidates = [
            {"formula": "C6H12O6", "fit_score": 0.9},
            {"formula": "C6H12O6", "fit_score": 0.4},
            {"formula": "C5H8N2O4", "fit_score": 0.88},
        ]
        assert candidate_density(candidates) == density_of(
            arbitrate_candidates(candidates)
        )

    def test_and_an_impossible_formula(self):
        candidates = [
            {"formula": "C6H12O6", "fit_score": 0.9},
            {"formula": "C6H17NO4", "fit_score": 0.99},
        ]
        assert candidate_density(candidates) == density_of(
            arbitrate_candidates(candidates)
        )


class TestAnchoringOnTheCommittedFormula:
    """The finder ranks by fit score and commits the best reading whose envelope
    holds its required lines; the arbitration ranks by fit x plausibility. When
    those disagree, a count taken at the top of the arbitration is a statement
    about a formula the row does not carry."""

    #: C6H17NO4 is over-saturated - plausibility 0 - so the arbitration ranks it
    #: last however well it fits, while a fit-first ranking would put it first.
    CONTESTED = [
        {"formula": "C6H12O6", "fit_score": 0.90},
        {"formula": "C5H8N2O4", "fit_score": 0.88},
        {"formula": "C3H4O4", "fit_score": 0.10},
    ]

    def test_the_anchor_moves_the_count(self):
        # C3H4O4 is far below the top pair, so around IT nothing ties.
        assert candidate_density(self.CONTESTED) == 2
        assert candidate_density(self.CONTESTED, around="C3H4O4") == 1

    def test_a_committed_formula_in_the_tie_counts_the_tie(self):
        assert candidate_density(self.CONTESTED, around="C6H12O6") == 2
        assert candidate_density(self.CONTESTED, around="C5H8N2O4") == 2

    def test_a_rival_the_evidence_ranks_ABOVE_the_commit_is_counted(self):
        # The whole point: a formula the evidence prefers to the committed one
        # is exactly what the count exists to surface, so the neighbourhood is
        # symmetric rather than "candidates below the commit".
        candidates = [
            {"formula": "C6H12O6", "fit_score": 0.95},
            {"formula": "C5H8N2O4", "fit_score": 0.92},
        ]
        assert candidate_density(candidates, around="C5H8N2O4") == 2

    def test_a_formula_that_is_not_a_candidate_stands_alone(self):
        assert candidate_density(self.CONTESTED, around="C9H12N2O2") == 1

    def test_the_gap_is_still_the_peak_s_own(self):
        # Anchoring moves what is counted, never how far apart two candidates
        # have to be to count as separated - that stays a fraction of the peak's
        # BEST evidence, so two rows of one peak use one scale.
        assert candidate_density(self.CONTESTED, around="C3H4O4", tie_tol=1.0) == 3

    def test_both_routes_agree_when_anchored(self):
        for formula in ("C6H12O6", "C5H8N2O4", "C3H4O4", "C9H12N2O2"):
            assert candidate_density(self.CONTESTED, around=formula) == density_of(
                arbitrate_candidates(self.CONTESTED), around=formula
            )
