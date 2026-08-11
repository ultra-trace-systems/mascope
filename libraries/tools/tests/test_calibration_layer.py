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
    INSTRUMENT_CALIBRATIONS,
    MIN_CALIBRATION_CLASS_LABELS,
    MIN_CALIBRATION_LABELS,
    Calibration,
    CalibrationRefused,
    DegenerateCalibration,
    InsufficientCalibrationData,
    _platt_fit,
    apply_calibration,
    apply_corroboration,
    calibration_error,
    calibration_for,
    discrimination_auc,
    fit_calibration,
    holdout_split,
    recalibrate,
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

WEIGHTS = {"+Br-": 2.28, "+NH4+": 0.83, "+(CH4N2O)H+": 0.70, "+H+": 0.0, "-H+": 0.0}


def test_corroboration_raises_probability_with_a_strong_adduct():
    # a weak-ish 0.6 assignment corroborated by a bromide adduct should rise
    p = apply_corroboration(0.6, ["+Br-"], WEIGHTS)
    assert p > 0.6
    # matches the closed-form odds update: logit(0.6) + 2.28
    z = np.log(0.6 / 0.4) + 2.28
    assert p == pytest.approx(1 / (1 + np.exp(-z)))


def test_corroboration_generic_adduct_barely_moves_it():
    # deprotonation carries ~0 log-odds -> essentially unchanged
    assert apply_corroboration(0.6, ["-H+"], WEIGHTS) == pytest.approx(0.6, abs=1e-9)


def test_corroboration_sums_multiple_adducts():
    p1 = apply_corroboration(0.5, ["+NH4+"], WEIGHTS)
    p2 = apply_corroboration(0.5, ["+NH4+", "+(CH4N2O)H+"], WEIGHTS)
    assert p2 > p1  # two corroborating adducts lift more than one


def test_corroboration_is_capped():
    # a pile of strong adducts can't exceed the cap on the log-odds shift
    big = {f"a{i}": 5.0 for i in range(10)}
    p = apply_corroboration(0.5, list(big), big, cap=DEFAULT_CORROBORATION_CAP)
    z = np.log(0.5 / 0.5) + DEFAULT_CORROBORATION_CAP
    assert p == pytest.approx(1 / (1 + np.exp(-z)))


def test_corroboration_noops_when_uncalibrated_or_empty():
    assert (
        apply_corroboration(None, ["+Br-"], WEIGHTS) is None
    )  # uncalibrated stays None
    assert apply_corroboration(0.7, [], WEIGHTS) == 0.7  # nothing corroborating
    assert apply_corroboration(0.7, ["+Br-"], None) == 0.7  # no weights configured
    assert apply_corroboration(0.7, ["unknown"], WEIGHTS) == 0.7  # unweighted adduct


def test_provisional_orbitrap_carries_corroboration_weights():
    cal = calibration_for("orbi")
    assert cal.corroboration_weights is not None
    assert cal.corroboration_weights["+Br-"] > cal.corroboration_weights["+NH4+"] > 0


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
        a=1.0, b=0.0, instrument="orbi", corroboration_weights={"+Br-": 2.28}
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
    assert out["calibration"].corroboration_weights == {"+Br-": 2.28}


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
