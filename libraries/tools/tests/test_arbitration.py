"""Unit tests for candidate arbitration (assignment-confidence P2).

Contract: for one peak, rank candidates by ``fit x plausibility``, report a
normalised per-candidate confidence, and flag ties honestly. Plausibility is the
graded Seven Golden Rules score, so a spectrally-good but chemically-impossible
formula must lose to a plausible one. See docs/dev/assignment_confidence.md (P2).
"""

import pytest

from mascope_tools.composition.arbitration import (
    DEFAULT_TIE_TOL,
    arbitrate_candidates,
    fdr_curve,
    threshold_at_fdr,
)


def test_empty_returns_empty():
    assert arbitrate_candidates([]) == []


def test_single_candidate_full_confidence():
    [c] = arbitrate_candidates([{"formula": "C6H12O6", "fit_score": 0.9}])
    assert c.rank == 1
    assert c.confidence == pytest.approx(1.0)
    assert c.is_tie is False
    assert c.plausibility == pytest.approx(1.0)
    assert c.evidence == pytest.approx(0.9)


def test_plausibility_beats_a_better_fitting_impossible_formula():
    # C6H17NO4 is over-saturated (plausibility 0): even with a higher fit it must
    # lose to the plausible glucose formula. This is the whole point of the layer.
    ranked = arbitrate_candidates(
        [
            {"formula": "C6H17NO4", "fit_score": 0.99},
            {"formula": "C6H12O6", "fit_score": 0.90},
        ]
    )
    assert ranked[0].formula == "C6H12O6"
    assert ranked[0].confidence == pytest.approx(1.0)  # sole positive evidence
    assert ranked[1].formula == "C6H17NO4"
    assert ranked[1].evidence == 0.0
    assert ranked[1].confidence == 0.0


def test_confidence_is_normalised_across_candidates():
    ranked = arbitrate_candidates(
        [
            {"formula": "C6H12O6", "fit_score": 0.8},  # plaus 1.0 -> ev 0.8
            {"formula": "C9H14", "fit_score": 0.6},  # plaus 1.0 -> ev 0.6
        ]
    )
    assert [c.formula for c in ranked] == ["C6H12O6", "C9H14"]
    assert sum(c.confidence for c in ranked) == pytest.approx(1.0)
    assert ranked[0].confidence == pytest.approx(0.8 / 1.4)
    assert ranked[1].confidence == pytest.approx(0.6 / 1.4)
    assert all(c.is_tie is False for c in ranked)


def test_close_evidence_is_flagged_as_a_tie():
    ranked = arbitrate_candidates(
        [
            {"formula": "C6H12O6", "fit_score": 0.80},
            {"formula": "C9H14", "fit_score": 0.78},  # gap 0.02 <= tie_tol
        ],
        tie_tol=0.05,
    )
    assert ranked[0].is_tie is True
    assert ranked[1].is_tie is True


def test_clear_winner_is_not_a_tie():
    ranked = arbitrate_candidates(
        [
            {"formula": "C6H12O6", "fit_score": 0.90},
            {"formula": "C9H14", "fit_score": 0.50},  # gap 0.40 > tie_tol
        ]
    )
    assert ranked[0].is_tie is False
    assert ranked[1].is_tie is False


def test_no_evidence_everything_ties_zero_confidence():
    ranked = arbitrate_candidates(
        [
            {"formula": "C6H12O6", "fit_score": 0.0},
            {"formula": "C9H14", "fit_score": 0.0},
        ]
    )
    assert all(c.confidence == 0.0 for c in ranked)
    assert all(c.is_tie is True for c in ranked)


def test_accepts_tuples_and_match_score_alias():
    a = arbitrate_candidates([("C6H12O6", 0.9)])
    assert a[0].evidence == pytest.approx(0.9)
    b = arbitrate_candidates([{"formula": "C6H12O6", "match_score": 0.7}])
    assert b[0].fit_score == pytest.approx(0.7)


def test_nan_and_negative_fit_treated_as_no_evidence():
    ranked = arbitrate_candidates(
        [
            {"formula": "C6H12O6", "fit_score": float("nan")},
            {"formula": "C9H14", "fit_score": -1.0},
        ]
    )
    assert all(c.evidence == 0.0 for c in ranked)


def test_deterministic_order_on_equal_evidence():
    # identical evidence -> stable tie-break by fit desc then formula
    r1 = arbitrate_candidates(
        [
            {"formula": "C9H14", "fit_score": 0.8},
            {"formula": "C6H12O6", "fit_score": 0.8},
        ]
    )
    r2 = arbitrate_candidates(
        [
            {"formula": "C6H12O6", "fit_score": 0.8},
            {"formula": "C9H14", "fit_score": 0.8},
        ]
    )
    assert [c.formula for c in r1] == [c.formula for c in r2]


def test_default_tie_tol_exported():
    assert 0.0 < DEFAULT_TIE_TOL < 1.0


# --- FDR helpers -----------------------------------------------------------


def test_fdr_curve_perfect_ranking():
    # all correct above all wrong -> FDR stays 0 while only corrects are accepted
    conf = [0.9, 0.8, 0.7, 0.4, 0.3]
    correct = [True, True, True, False, False]
    pts = fdr_curve(conf, correct)
    assert [p.n_accepted for p in pts] == [1, 2, 3, 4, 5]
    assert pts[2].fdr == pytest.approx(0.0)  # top-3 all correct
    assert pts[4].fdr == pytest.approx(2 / 5)  # all accepted -> 2 wrong of 5
    # q-values are monotone non-decreasing as acceptance grows
    qs = [p.q_value for p in pts]
    assert qs == sorted(qs)


def test_fdr_curve_length_mismatch_raises():
    with pytest.raises(ValueError):
        fdr_curve([0.9, 0.8], [True])


def test_threshold_at_fdr_picks_permissive_cut():
    conf = [0.9, 0.8, 0.7, 0.4, 0.3]
    correct = [True, True, True, False, False]
    # 0% FDR -> accept the three corrects, cut at 0.7
    assert threshold_at_fdr(conf, correct, 0.0) == pytest.approx(0.7)
    # allow 25% -> can also take the first wrong one (1/4 = 0.25), cut at 0.4
    assert threshold_at_fdr(conf, correct, 0.25) == pytest.approx(0.4)


def test_threshold_at_fdr_unreachable_returns_none():
    # a wrong winner at the very top makes every cut's FDR >= 0.5
    assert threshold_at_fdr([0.9, 0.8], [False, True], 0.1) is None


def test_fdr_curve_tie_is_conservative():
    # a correct and a wrong winner tie at 0.8; the tie's FDR must count the wrong one
    pts = fdr_curve([0.8, 0.8], [True, False])
    # wrong ordered first within the tie -> after 1 accepted, FDR 1.0
    assert pts[0].fdr == pytest.approx(1.0)
    assert pts[1].fdr == pytest.approx(0.5)
