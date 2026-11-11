"""Unit tests for the confidence-calibration layer (assignment-confidence P2).

Contract: fit a score -> P(correct) Platt curve from labelled data, apply it, and be
honest -- a calibration carries provenance, an uncalibrated instrument returns None
(never a borrowed probability), and too little data refuses to fit. The machinery is
validated on synthetic labels (independent of the still-provisional real datasets).
See docs/dev/assignment_confidence.md (P2).
"""

import numpy as np
import pytest

from mascope_tools.composition.calibration import (
    DEFAULT_CORROBORATION_CAP,
    EVIDENCE_LEVEL_WEIGHTS,
    INSTRUMENT_CALIBRATIONS,
    MIN_CALIBRATION_CLASS_LABELS,
    MIN_CALIBRATION_LABELS,
    PROVISIONAL_ORBITRAP_CORROBORATION,
    Calibration,
    CalibrationRefused,
    DegenerateCalibration,
    InsufficientCalibrationData,
    _platt_fit,
    apply_calibration,
    apply_corroboration,
    calibration_error,
    calibration_for,
    corroboration_by_mechanism,
    discrimination_auc,
    fit_calibration,
    holdout_split,
    label_weights,
    recalibrate,
)
from mascope_tools.composition.mechanism_notation import (
    from_row,
    mechanism_key,
    to_row,
)


def _separated_labels(n=400, seed=0):
    """Scores where correctness rises with score (a calibratable signal)."""
    rng = np.random.default_rng(seed)
    scores = rng.uniform(0, 1, n)
    # P(correct) truly ~ score -> a good curve should recover calibration
    correct = rng.uniform(0, 1, n) < scores
    return scores, correct.astype(int)


def _anticorrelated_labels(n=400, seed=7):
    """Scores where correctness FALLS with the score.

    The pathological case for a Platt fit: the optimizer happily converges on a
    monotone *decreasing* curve, which is not a worse calibration but an inverted one.
    """
    rng = np.random.default_rng(seed)
    scores = rng.uniform(0, 1, n)
    correct = rng.uniform(0, 1, n) < (1 - scores)
    return scores, correct.astype(int)


def _noise_labels(n=400, seed=13):
    """Correctness independent of the score -- nothing for a curve to learn."""
    rng = np.random.default_rng(seed)
    scores = rng.uniform(0, 1, n)
    correct = rng.uniform(0, 1, n) < 0.5
    return scores, correct.astype(int)


def _curve_labels(cal, n=400, seed=0):
    """Labels drawn from a KNOWN curve, so "the incumbent is already right" and
    "the incumbent is wrong" can be set up as separate, controlled situations."""
    rng = np.random.default_rng(seed)
    scores = rng.uniform(0, 1, n)
    correct = rng.uniform(0, 1, n) < apply_calibration(scores, cal)
    return scores, correct.astype(int)


def test_apply_calibration_is_a_probability():
    c = Calibration(a=6.0, b=-3.0)
    p = apply_calibration(np.array([0.0, 0.5, 1.0]), c)
    assert np.all((p >= 0) & (p <= 1))
    assert p[2] > p[0]  # monotone increasing in score
    # accepts a raw (a, b) tuple too
    assert apply_calibration(0.5, (6.0, -3.0)) == pytest.approx(p[1])


def test_calibration_error_zero_when_perfect():
    # probabilities that exactly match empirical correctness -> ECE 0
    probs = np.array([0.0] * 50 + [1.0] * 50)
    correct = np.array([0] * 50 + [1] * 50)
    assert calibration_error(probs, correct) == pytest.approx(0.0)


def test_calibration_error_detects_miscalibration():
    # claim 0.9 everywhere but only half are correct -> large ECE
    probs = np.full(100, 0.9)
    correct = np.array([1, 0] * 50)
    assert calibration_error(probs, correct) > 0.3


def test_fit_calibration_recovers_a_calibrated_curve():
    scores, correct = _separated_labels(n=600)
    cal = fit_calibration(scores, correct, instrument="orbi", source="synthetic")
    assert cal.instrument == "orbi"
    assert cal.n_pos > 0 and cal.n_neg > 0
    assert cal.fit_utc is not None
    # the fitted curve should be reasonably calibrated on held-out data
    assert cal.ece is not None and cal.ece < 0.15
    # applying it yields probabilities that increase with score
    p = apply_calibration(np.array([0.2, 0.8]), cal)
    assert p[1] > p[0]


def test_fit_calibration_refuses_too_few_labels():
    with pytest.raises(InsufficientCalibrationData):
        fit_calibration([0.9, 0.1], [1, 0])


def test_fit_calibration_refuses_single_class():
    scores = list(np.linspace(0, 1, MIN_CALIBRATION_LABELS + 10))
    with pytest.raises(InsufficientCalibrationData):
        fit_calibration(scores, [1] * len(scores))  # all correct, nothing to separate


def test_fit_calibration_requires_a_minimum_of_each_class():
    """A total-only floor is not the gate the docstring promises.

    Enough labels overall but a single negative is exactly the shape that used to slip
    through: two parameters fit against one point of one class, certified by an ECE
    measured on a holdout that may not contain that class at all.
    """
    n = MIN_CALIBRATION_LABELS + 10
    scores = list(np.linspace(0, 1, n))
    labels = [0] + [1] * (n - 1)
    with pytest.raises(InsufficientCalibrationData) as exc:
        fit_calibration(scores, labels)
    assert exc.value.reason == "insufficient_labels"
    assert str(MIN_CALIBRATION_CLASS_LABELS) in str(exc.value)


def test_calibration_error_rejects_values_outside_zero_one():
    """The bins tile [0, 1]; anything outside falls in none of them.

    Dropping those silently would report an ECE averaged over a subset while reading as
    an average over everything -- an understatement of the very miscalibration the
    number exists to expose. Raw evidence fed in by mistake must be loud.
    """
    with pytest.raises(ValueError):
        calibration_error([0.5, 1.7], [1, 0])
    with pytest.raises(ValueError):
        calibration_error([0.5, float("nan")], [1, 0])


def test_calibration_for_returns_provisional_orbitrap():
    cal = calibration_for("orbi")
    assert cal is not None
    assert cal.instrument == "orbi"
    assert cal.provisional is True


def test_calibration_for_uncalibrated_instrument_is_none():
    # TOF has no curated dataset yet -> None, so callers stay honest (uncalibrated)
    assert calibration_for("tof") is None
    assert calibration_for(None) is None
    assert calibration_for("unknown-instrument") is None


def test_registry_only_ships_orbitrap():
    assert set(INSTRUMENT_CALIBRATIONS) == {"orbi"}


# --- adduct corroboration odds-update -------------------------------------------------

WEIGHTS = {
    "[M+Br]-": 2.28,
    "[M+NH4]+": 0.83,
    "[M+CH4N2O+H]+": 0.70,
    "[M+H]+": 0.0,
    "[M-H]-": 0.0,
}


def test_corroboration_raises_probability_with_a_strong_adduct():
    # a weak-ish 0.6 assignment corroborated by a bromide adduct should rise
    p = apply_corroboration(0.6, ["[M+Br]-"], WEIGHTS)
    assert p > 0.6
    # matches the closed-form odds update: logit(0.6) + 2.28
    z = np.log(0.6 / 0.4) + 2.28
    assert p == pytest.approx(1 / (1 + np.exp(-z)))


def test_corroboration_generic_adduct_barely_moves_it():
    # deprotonation carries ~0 log-odds -> essentially unchanged
    assert apply_corroboration(0.6, ["[M-H]-"], WEIGHTS) == pytest.approx(0.6, abs=1e-9)


def test_corroboration_matches_an_adduct_to_its_weight_by_mechanism():
    # Weights keyed in the legacy notation, as a stored calibration may be,
    # apply to the same adducts written in the standard one, and back.
    legacy = {"+Br-": 2.28, "-H+": 0.0}
    assert apply_corroboration(0.6, ["[M+Br]-"], legacy) == pytest.approx(
        apply_corroboration(0.6, ["[M+Br]-"], WEIGHTS)
    )
    assert apply_corroboration(0.6, ["+Br-"], WEIGHTS) > 0.6
    # A mechanism of several terms, whichever order they are written in.
    assert apply_corroboration(0.6, ["[M+H+CH4N2O]+"], WEIGHTS) == pytest.approx(
        apply_corroboration(0.6, ["[M+CH4N2O+H]+"], WEIGHTS)
    )


def test_the_adduct_row_leaves_the_corroboration_weights_as_they_were():
    # The weights are keyed by the mechanism's standard spelling, and the row
    # beside the string changes no spelling a weight is stored under: every
    # shipped key, and its legacy spelling, keys as it did before.
    keys = {
        "[M+Br]-": "+Br-",
        "[M+NH4]+": "+NH4+",
        "[M+CH4N2O+H]+": "+(CH4N2O)H+",
        "[M+H]+": "+H+",
        "[M-H]-": "-H+",
    }
    assert set(keys) == set(PROVISIONAL_ORBITRAP_CORROBORATION)
    for standard, legacy in keys.items():
        assert mechanism_key(standard) == mechanism_key(legacy) == standard
        assert from_row(to_row(standard)) == standard
    weights = dict(PROVISIONAL_ORBITRAP_CORROBORATION)
    assert corroboration_by_mechanism(weights) == weights
    assert apply_corroboration(0.6, ["[M+Br]-"], weights) == pytest.approx(
        1 / (1 + np.exp(-(np.log(0.6 / 0.4) + 2.28)))
    )
    # A multimer, a charge above one or a neutral loss of a weighted adduct is
    # another mechanism, and takes none of its weight.
    for other in ("[2M+Br]-", "[M+2NH4]2+", "[M+NH4-H2O]+"):
        assert mechanism_key(other) not in weights
        assert apply_corroboration(0.6, [other], weights) == 0.6


@pytest.mark.parametrize(
    "weights",
    [
        {"[M+Br]-": 2.0, "+Br-": 1.0},
        {"+Br-": 1.0, "[M+Br]-": 2.0},
        {"+Br-": 1.0, "[M+Br]-": 2.0, "[M+Br]- ": 3.0},
    ],
)
def test_a_weight_in_the_standard_notation_wins_over_a_legacy_one(weights):
    # Both spellings of one adduct in one calibration: the standard one decides,
    # whichever comes first, as it does once the migration has kept it alone.
    assert corroboration_by_mechanism(weights) == {"[M+Br]-": 2.0}
    assert apply_corroboration(0.5, ["[M+Br]-"], weights) == pytest.approx(
        apply_corroboration(0.5, ["[M+Br]-"], {"[M+Br]-": 2.0})
    )


def test_weights_already_keyed_come_back_as_they_were():
    keyed = corroboration_by_mechanism({"+Br-": 2.28, "-H+": 0.0, "unknown": 1.0})
    assert keyed == {"[M+Br]-": 2.28, "[M-H]-": 0.0, "unknown": 1.0}
    assert corroboration_by_mechanism(keyed) == keyed


def test_corroboration_sums_multiple_adducts():
    p1 = apply_corroboration(0.5, ["[M+NH4]+"], WEIGHTS)
    p2 = apply_corroboration(0.5, ["[M+NH4]+", "[M+CH4N2O+H]+"], WEIGHTS)
    assert p2 > p1  # two corroborating adducts lift more than one


def test_corroboration_is_capped():
    # a pile of strong adducts can't exceed the cap on the log-odds shift
    big = {f"a{i}": 5.0 for i in range(10)}
    p = apply_corroboration(0.5, list(big), big, cap=DEFAULT_CORROBORATION_CAP)
    z = np.log(0.5 / 0.5) + DEFAULT_CORROBORATION_CAP
    assert p == pytest.approx(1 / (1 + np.exp(-z)))


def test_corroboration_noops_when_uncalibrated_or_empty():
    assert (
        apply_corroboration(None, ["[M+Br]-"], WEIGHTS) is None
    )  # uncalibrated stays None
    assert apply_corroboration(0.7, [], WEIGHTS) == 0.7  # nothing corroborating
    assert apply_corroboration(0.7, ["[M+Br]-"], None) == 0.7  # no weights configured
    assert apply_corroboration(0.7, ["unknown"], WEIGHTS) == 0.7  # unweighted adduct


def test_provisional_orbitrap_carries_corroboration_weights():
    cal = calibration_for("orbi")
    assert cal.corroboration_weights is not None
    assert (
        cal.corroboration_weights["[M+Br]-"] > cal.corroboration_weights["[M+NH4]+"] > 0
    )


def test_provisional_orbitrap_records_when_it_was_fit():
    # The one curve that actually ships must carry the provenance the class exists for;
    # fit_utc is what tells an operator whether the default Orbitrap curve is stale.
    cal = calibration_for("orbi")
    assert cal.fit_utc is not None
    assert cal.source is not None


# --- recalibrate (V2 loop: labels -> new calibration) ---------------------------------


def test_recalibrate_fits_and_reports_change():
    scores, labels = _separated_labels(n=400, seed=1)
    current = Calibration(
        a=1.0, b=0.0, instrument="orbi", corroboration_weights={"[M+Br]-": 2.28}
    )
    out = recalibrate(
        scores, labels, instrument="orbi", source="user verifications", current=current
    )
    assert out["calibration"].instrument == "orbi"
    assert 0.0 <= out["after_ece"] <= 1.0
    assert out["before_ece"] is not None  # current curve scored on these labels
    # a curve fit on the labels should calibrate them at least as well as an arbitrary prior
    assert out["after_ece"] <= out["before_ece"] + 1e-6
    # corroboration weights are carried forward (refit separately, not from verdicts)
    assert out["calibration"].corroboration_weights == {"[M+Br]-": 2.28}


def test_recalibrate_stays_provisional_without_strong_evidence():
    scores, labels = _separated_labels(n=400, seed=2)
    levels = ["visual"] * len(labels)  # eyeball-only -> cannot graduate the curve
    out = recalibrate(scores, labels, levels, instrument="orbi", source="s")
    assert out["provisional"] is True
    assert out["n_strong_positives"] == 0
    assert out["calibration"].provisional is True


def test_recalibrate_graduates_with_enough_strong_positives():
    scores, labels = _separated_labels(n=400, seed=3)
    # give the positives reference-standard evidence; negatives carry none
    levels = ["reference_standard" if y == 1 else None for y in labels]
    out = recalibrate(
        scores, labels, levels, instrument="orbi", source="s", provisional_min_strong=5
    )
    assert out["n_strong_positives"] >= 5
    assert out["provisional"] is False
    assert out["calibration"].provisional is False


def test_recalibrate_refuses_too_few_labels():
    with pytest.raises(InsufficientCalibrationData):
        recalibrate([0.9, 0.1, 0.8], [1, 0, 1], instrument="orbi", source="s")


def test_recalibrate_no_current_curve_has_no_before_ece():
    scores, labels = _separated_labels(n=200, seed=4)
    out = recalibrate(scores, labels, instrument="tof", source="s", current=None)
    assert out["before_ece"] is None
    assert out["calibration"].corroboration_weights is None


# --- the activation guards --------------------------------------------------------
#
# A recalibration rewrites P(correct) for every assignment on an instrument, from one
# batch of user verdicts, with nothing downstream able to tell that it happened. These
# tests pin the three ways a fit can be unusable and the one way it can be merely worse.


def test_calibration_refusals_share_a_base():
    """One `except` at the call site has to cover every refusal, or the caller that
    forgets a branch activates the curve the guard was written to stop."""
    assert issubclass(InsufficientCalibrationData, CalibrationRefused)
    assert issubclass(DegenerateCalibration, CalibrationRefused)
    assert InsufficientCalibrationData("x").reason == "insufficient_labels"
    assert DegenerateCalibration("x", reason="non_monotonic_fit").reason == (
        "non_monotonic_fit"
    )


def test_fit_calibration_refuses_an_inverted_curve():
    """Anti-correlated labels fit a MONOTONE DECREASING curve (measured: a=-6.39,
    b=+3.17, i.e. P(0.1)=0.93 and P(0.9)=0.07 -- highest confidence for the weakest
    evidence, instrument-wide).

    The second half of this test is the point: the optimizer reports success on exactly
    these labels, so a convergence check would wave the inverted curve through. Only the
    sign of the slope catches it.
    """
    scores, labels = _anticorrelated_labels()
    with pytest.raises(DegenerateCalibration) as exc:
        fit_calibration(scores, labels, instrument="orbi", source="synthetic")
    assert exc.value.reason == "non_monotonic_fit"

    a, _b, converged = _platt_fit(scores, labels)
    assert converged is True
    assert a < 0


def test_inverted_curve_would_pass_the_ece_gate():
    """The regression guard against "simplifying" the guards down to the ECE gate.

    On its own labels the inverted curve is BETTER calibrated than a healthy incumbent
    (measured: 0.087 vs 0.540), so an ECE-only gate would activate it enthusiastically.
    The sign check has to run first.
    """
    scores, labels = _anticorrelated_labels()
    a, b, _ = _platt_fit(scores, labels)
    inverted = Calibration(a=a, b=b, instrument="orbi")
    healthy = Calibration(a=6.0, b=-3.0, instrument="orbi")
    inverted_ece = calibration_error(apply_calibration(scores, inverted), labels)
    healthy_ece = calibration_error(apply_calibration(scores, healthy), labels)
    assert inverted_ece < healthy_ece


def test_recalibrate_refuses_an_inverted_curve_and_reports_the_reason():
    scores, labels = _anticorrelated_labels()
    current = Calibration(a=6.0, b=-3.0, instrument="orbi")
    with pytest.raises(CalibrationRefused) as exc:
        recalibrate(scores, labels, instrument="orbi", source="s", current=current)
    assert exc.value.reason == "non_monotonic_fit"
    # the incumbent is a frozen dataclass; nothing about it was touched
    assert current.params() == (6.0, -3.0)


def test_fit_calibration_refuses_uninformative_labels():
    """Noise labels fit a ~ 0: positive (so the sign check passes) with an excellent
    ECE (a constant predictor is perfectly calibrated and perfectly useless), while
    flattening every p_correct on the instrument to the base rate.

    Held-out AUC is the only guard that sees it. If this guard is ever dropped for
    scope, this test must be dropped WITH IT and the gap recorded in
    docs/dev/verification_calibration_loop.md S5 -- not quietly omitted.
    """
    scores, labels = _noise_labels()
    with pytest.raises(DegenerateCalibration) as exc:
        fit_calibration(scores, labels, instrument="orbi", source="synthetic")
    assert exc.value.reason == "no_discrimination"
    assert discrimination_auc(scores, labels) < 0.6


def test_discrimination_auc_is_the_rank_statistic():
    # perfect separation, no separation, and the undefined single-class case
    assert discrimination_auc([0.1, 0.2, 0.9, 1.0], [0, 0, 1, 1]) == pytest.approx(1.0)
    assert discrimination_auc([0.5] * 4, [0, 1, 0, 1]) == pytest.approx(0.5)
    assert np.isnan(discrimination_auc([0.1, 0.9], [1, 1]))


def test_recalibrate_refuses_an_ece_regression():
    """The incumbent is already the curve the labels came from, so a refit on 80 points
    is a noisier estimate of it, not an improvement (measured: 0.111 -> 0.137).

    It is a perfectly usable curve -- it passes the sign and discrimination guards --
    so it is reported rather than raised, and the caller gets the params and both errors
    to look at before dropping it.
    """
    current = Calibration(a=6.0, b=-3.0, instrument="orbi")
    scores, labels = _curve_labels(current, n=80, seed=0)
    out = recalibrate(scores, labels, instrument="orbi", source="s", current=current)
    assert out["activate"] is False
    assert out["refusal"] == "ece_regression"
    assert out["after_ece"] > out["before_ece"]
    assert out["calibration"] is not None
    assert out["calibration"].a > 0


def test_recalibrate_activates_when_it_beats_the_active_curve():
    """The situation recalibration exists for: the active curve is wrong for this
    instrument and enough labels are in to say so."""
    truth = Calibration(a=6.0, b=-3.0, instrument="orbi")
    scores, labels = _curve_labels(truth, n=400, seed=0)
    current = Calibration(a=1.2, b=-0.4, instrument="orbi")
    out = recalibrate(scores, labels, instrument="orbi", source="s", current=current)
    assert out["activate"] is True
    assert out["refusal"] is None
    assert out["after_ece"] < out["before_ece"]


def test_recalibrate_measures_both_eces_on_the_same_holdout():
    """The gate only means something if the two numbers are measured on the same
    points: the ECE estimator is biased upward on fewer points, so scoring the new
    curve on a held-out half against an incumbent scored on ALL the labels would fail
    refits that are genuinely better."""
    truth = Calibration(a=6.0, b=-3.0, instrument="orbi")
    scores, labels = _curve_labels(truth, n=400, seed=0)
    current = Calibration(a=1.2, b=-0.4, instrument="orbi")
    out = recalibrate(scores, labels, instrument="orbi", source="s", current=current)
    test, _train = holdout_split(len(scores))
    expected = calibration_error(
        apply_calibration(np.asarray(scores)[test], current),
        np.asarray(labels)[test],
    )
    assert out["before_ece"] == pytest.approx(round(float(expected), 4))


def test_holdout_split_is_deterministic_and_partitions():
    test, train = holdout_split(100, holdout=0.5, seed=0)
    assert len(test) == 50 and len(train) == 50
    assert sorted(np.concatenate([test, train]).tolist()) == list(range(100))
    assert np.array_equal(test, holdout_split(100, holdout=0.5, seed=0)[0])


# --- evidence-level weighting (MAS-329) ---------------------------------------------
#
# Guardrail 1 of the verification loop: a reference standard or an MS/MS spectrum is truth
# from outside the fit, a visual look reads the score's own picture. The refit weighs each
# label by the evidence its verdict states, and records the weights on the curve.


def test_label_weights_reads_the_evidence_level_of_each_label():
    weights = label_weights(["reference_standard", "msms", "visual", None, "unknown"])
    assert list(weights) == [1.0, 1.0, 0.25, 0.25, 0.25]
    # stronger evidence weighs more, all the way down the ladder
    ladder = ["reference_standard", "orthogonal", "pattern", "visual"]
    assert list(label_weights(ladder)) == sorted(label_weights(ladder), reverse=True)
    assert EVIDENCE_LEVEL_WEIGHTS["visual"] < EVIDENCE_LEVEL_WEIGHTS["msms"]


def test_calibration_error_weighs_each_label():
    """Unit weights are the unweighted error; a zero weight takes a label out of it."""
    probs = np.array([0.1, 0.1, 0.9, 0.9])
    correct = np.array([0, 0, 1, 0])
    assert calibration_error(probs, correct, weights=np.ones(4)) == pytest.approx(
        calibration_error(probs, correct)
    )
    # the one miscalibrated label weighed out: the rest is perfectly calibrated
    sure = [0.0, 0.0, 1.0, 1.0]
    assert calibration_error(sure, correct) == pytest.approx(0.25)
    assert calibration_error(sure, correct, weights=[1, 1, 1, 0]) == pytest.approx(0.0)


def test_platt_fit_weight_two_is_a_label_counted_twice():
    scores, labels = _separated_labels(n=120, seed=5)
    weights = np.ones(len(scores))
    weights[:30] = 2.0
    doubled_s = np.concatenate([scores, scores[:30]])
    doubled_y = np.concatenate([labels, labels[:30]])
    a_w, b_w, _ = _platt_fit(scores, labels, weights)
    a_d, b_d, _ = _platt_fit(doubled_s, doubled_y)
    assert a_w == pytest.approx(a_d, abs=1e-3)
    assert b_w == pytest.approx(b_d, abs=1e-3)


def _echo_pool(seed=11):
    """Strong labels drawn from a known curve, plus visual labels that echo a wrong one.

    The visual verdicts are what the confirmation-bias loop produces: the user confirms
    whatever scores high, so their labels follow a much steeper curve than the truth.
    """
    truth = Calibration(a=4.0, b=-2.0)
    echo = Calibration(a=20.0, b=-10.0)
    s_true, y_true = _curve_labels(truth, n=300, seed=seed)
    s_echo, y_echo = _curve_labels(echo, n=900, seed=seed + 1)
    scores = np.concatenate([s_true, s_echo])
    labels = np.concatenate([y_true, y_echo])
    levels = ["reference_standard"] * len(s_true) + ["visual"] * len(s_echo)
    return truth, scores, labels, levels


def test_fit_calibration_weights_pull_the_curve_toward_the_strong_labels():
    truth, scores, labels, levels = _echo_pool()
    flat = fit_calibration(scores, labels)
    weighted = fit_calibration(scores, labels, weights=label_weights(levels))
    # the weighted curve's slope sits nearer the truth than the unweighted one's
    assert abs(weighted.a - truth.a) < abs(flat.a - truth.a)


def test_fit_calibration_certifies_itself_by_the_weighted_held_out_error():
    """The ECE a curve carries is the weighted one, on the held-out labels, and a
    label dropped for a non-finite score takes its weight with it."""
    scores, labels = _separated_labels(n=400, seed=21)
    # Weights alternating by place, so a weight that slid one place would land
    # on every other label wrong.
    weights = np.tile([1.0, 0.1], 200)
    scores = np.concatenate([[np.nan], scores])
    labels = np.concatenate([[1], labels])
    weights = np.concatenate([[1.0], weights])
    cal = fit_calibration(scores, labels, weights=weights)
    finite = np.isfinite(scores)
    s, y, w = scores[finite], labels[finite], weights[finite]
    test, _ = holdout_split(len(s))
    weighted = calibration_error(
        apply_calibration(s[test], cal), y[test], weights=w[test]
    )
    unweighted = calibration_error(apply_calibration(s[test], cal), y[test])
    assert cal.ece == round(weighted, 4)
    assert round(weighted, 4) != round(unweighted, 4)


def test_recalibrate_weighs_the_labels_and_records_the_weights():
    truth, scores, labels, levels = _echo_pool()
    current = Calibration(a=1.0, b=0.0, instrument="orbi")
    out = recalibrate(
        scores, labels, levels, instrument="orbi", source="s", current=current
    )
    assert out["label_weights"] == dict(EVIDENCE_LEVEL_WEIGHTS)
    assert out["calibration"].label_weights == dict(EVIDENCE_LEVEL_WEIGHTS)
    # the fit is the weighted one
    weighted = fit_calibration(scores, labels, weights=label_weights(levels))
    assert out["calibration"].a == pytest.approx(weighted.a)
    assert out["after_ece"] == weighted.ece
    # and the incumbent is scored under the same weights, on the same held-out points
    test, _ = holdout_split(len(scores))
    expected = calibration_error(
        apply_calibration(scores[test], current),
        labels[test],
        weights=label_weights(levels)[test],
    )
    assert out["before_ece"] == round(expected, 4)


def test_recalibrate_without_levels_or_weights_is_unweighted():
    _, scores, labels, levels = _echo_pool()
    flat = fit_calibration(scores, labels)
    for out in (
        recalibrate(scores, labels, instrument="orbi", source="s"),
        recalibrate(
            scores, labels, levels, instrument="orbi", source="s", evidence_weights=None
        ),
    ):
        assert out["label_weights"] is None
        assert out["calibration"].label_weights is None
        assert out["calibration"].a == pytest.approx(flat.a)
