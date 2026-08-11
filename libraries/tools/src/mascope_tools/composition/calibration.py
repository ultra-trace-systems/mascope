"""Confidence calibration (assignment-confidence Phase 2).

A fit score, and the arbitration *evidence* (fit x plausibility), rank assignments
well but are not probabilities: a raw 0.85 is not "85% likely correct". Calibration
maps a raw score to a **calibrated probability of being correct** via Platt scaling
([Platt 1999][platt]) -- a logistic curve ``P = sigmoid(a*score + b)`` fit on assignments
whose truth is known (true identifications vs near-mass decoys).

Two design commitments make this safe to ship before the datasets are final:

1. **A calibration is a provenance-carrying object, not a bare constant.** A
   :class:`Calibration` records the instrument it was fit for, how many positive/negative
   labels went into it, its held-out calibration error, when and from what dataset it was
   fit, and whether it is still ``provisional``. "We don't have good data yet" becomes
   metadata, not a hidden risk.
2. **Never fabricate a probability we cannot back up.** When no calibration exists for an
   instrument (e.g. TOF today), :func:`calibration_for` returns ``None`` and callers must
   report the assignment as *uncalibrated* (surfacing the raw evidence + a flag) rather
   than emit an instrument-mismatched probability.

**Where the labels come from (the reference-dataset link).** The positive labels are
*confident identifications* -- most strongly, compounds confirmed by a reference standard
(Schymanski Level 1). That is exactly what the reference dataset encodes, so a per-instrument
calibration is "how well the score predicts reference-confirmed identity on this instrument".
The negatives are near-mass decoys (`tooling/score_eval`). This is also the basis of the
*user self-calibration* flow: a user runs known standards on their instrument, Mascope scores
them plus decoys, and fits that instrument's curve -- persisted per instrument (a later step;
today the registry ships one provisional Orbitrap curve).

[platt]: https://en.wikipedia.org/wiki/Platt_scaling
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Mapping, Sequence

import numpy as np


@dataclass(frozen=True)
class Calibration:
    """A fitted score -> P(correct) curve with its provenance.

    ``a``/``b`` are the Platt parameters (``P = sigmoid(a*score + b)``). The rest is
    provenance so a calibrated number is auditable and its trust level is explicit.

    ``corroboration_weights`` are the per-adduct log-odds by which co-occurring adducts of
    the same compound update ``p_correct`` (see :func:`apply_corroboration`), measured on the
    golden set (``tooling/score_eval/corroboration_benchmark.py``). They ride on the same
    per-instrument object because, like the Platt curve, they encode this instrument's reagent
    chemistry and are refit alongside it. ``None``/empty means no corroboration adjustment.
    """

    a: float
    b: float
    instrument: str | None = None  # instrument class this was fit for (e.g. "orbi")
    n_pos: int = 0  # confident (correct) labels used
    n_neg: int = 0  # decoy (incorrect) labels used
    ece: float | None = None  # held-out expected calibration error (lower = better)
    fit_utc: str | None = None  # when it was fit (ISO-8601)
    source: str | None = None  # dataset / reference provenance it was fit from
    provisional: bool = True  # True until fit on a curated, sufficient dataset
    # {adduct notation -> log-odds boost}; e.g. {"+Br-": 2.28, "+NH4+": 0.83}
    corroboration_weights: Mapping[str, float] | None = field(default=None)

    def params(self) -> tuple[float, float]:
        return (self.a, self.b)


# A corroboration boost is capped so a compound seen via many adducts can't drive p_correct
# arbitrarily to 1 (the per-adduct LRs assume rough independence, which weakens as they stack).
DEFAULT_CORROBORATION_CAP = 3.0


def apply_corroboration(
    p_correct: float | None,
    observed_adducts: Sequence[str],
    weights: Mapping[str, float] | None,
    *,
    cap: float = DEFAULT_CORROBORATION_CAP,
) -> float | None:
    """Update a calibrated probability with adduct co-occurrence, as a bounded Bayesian odds
    update: ``logit(p') = logit(p) + clamp(sum(weights[a] for a in observed_adducts), -cap, cap)``.

    ``observed_adducts`` are the OTHER adducts (beyond the winner's own) via which the same
    compound was independently assigned; each contributes its measured log-odds. Generic adducts
    (protonation/deprotonation) carry ~0, distinctive ones (e.g. bromide) carry more, so a strong
    corroborator lifts a weak assignment while a generic one barely moves a strong one. Returns
    ``p_correct`` unchanged when it is ``None`` (uncalibrated), or when there are no weights or no
    observed corroborating adducts."""
    if p_correct is None or not weights or not observed_adducts:
        return p_correct
    delta = float(sum(weights.get(a, 0.0) for a in observed_adducts))
    if delta == 0.0:
        return p_correct
    delta = max(-cap, min(cap, delta))
    p = min(max(float(p_correct), 1e-9), 1 - 1e-9)
    z = np.log(p / (1.0 - p)) + delta
    return float(1.0 / (1.0 + np.exp(-z)))


def apply_calibration(score, calibration: "Calibration | tuple[float, float]"):
    """Map a raw score (or array) to a calibrated probability via
    ``sigmoid(a*score + b)``. Accepts a :class:`Calibration` or a raw ``(a, b)`` pair."""
    a, b = calibration.params() if isinstance(calibration, Calibration) else calibration
    return 1.0 / (1.0 + np.exp(-(a * np.asarray(score, float) + b)))


def calibration_error(
    probs: Sequence[float], is_correct: Sequence[bool], *, bins: int = 10
) -> float:
    """Expected calibration error: the average gap between predicted probability and
    observed correctness, over equal-width probability bins (weighted by bin count).
    0 = perfectly calibrated.

    ``probs`` must be PROBABILITIES, not raw scores: the bins tile [0, 1] exactly, so a
    value outside that range falls in no bin. Silently dropping such points would make
    the ECE an average over a subset while still reading as an average over everything --
    an understatement of exactly the miscalibration the number exists to expose -- so it
    raises instead. Calibrated output (a sigmoid) can never trip this; feeding raw
    evidence in by mistake always will.

    :raises ValueError: any probability is non-finite or outside [0, 1].
    """
    p = np.asarray(probs, float)
    y = np.asarray(is_correct, float)
    if len(p) == 0:
        return float("nan")
    if not np.all(np.isfinite(p)) or p.min() < 0.0 or p.max() > 1.0:
        raise ValueError(
            "calibration_error expects probabilities in [0, 1] "
            f"(got min={np.nanmin(p)}, max={np.nanmax(p)}); "
            "apply the calibration before measuring its error"
        )
    edges = np.linspace(0.0, 1.0, bins + 1)
    ece = 0.0
    for k in range(bins):
        hi = p <= edges[k + 1] if k == bins - 1 else p < edges[k + 1]
        m = (p >= edges[k]) & hi
        if m.any():
            ece += m.mean() * abs(y[m].mean() - p[m].mean())
    return float(ece)


def _platt_fit(scores: np.ndarray, labels: np.ndarray) -> tuple[float, float, bool]:
    """Fit ``P = sigmoid(a*score + b)`` by minimising log-loss (Platt scaling).

    Returns ``(a, b, converged)``. Convergence is reported rather than assumed: when the
    optimize fails, ``res.x`` is simply wherever the simplex happened to stop, which is a
    pair of numbers and not a curve. Callers must not turn that into a
    :class:`Calibration`.
    """
    from scipy.optimize import minimize

    s = np.asarray(scores, float)
    y = np.asarray(labels, float)

    def nll(p: np.ndarray) -> float:
        z = p[0] * s + p[1]
        # numerically stable log-loss
        return float(
            np.where(y == 1, np.logaddexp(0.0, -z), np.logaddexp(0.0, z)).mean()
        )

    res = minimize(nll, np.array([3.0, -1.5]), method="Nelder-Mead")
    return float(res.x[0]), float(res.x[1]), bool(res.success)


# Fewer than this many labelled points is too little to fit a meaningful curve;
# callers should keep the assignment uncalibrated instead.
MIN_CALIBRATION_LABELS = 30

# ...and this many of EACH class. A total-only floor is not the same requirement: 31
# labels with a single positive clears it, yet that curve's intercept -- and the held-out
# ECE that is supposed to certify it -- rest on one point. The floor is per class because
# Platt scaling fits two parameters against the balance between the classes, and the ECE
# is then estimated on roughly half the labels, so each class has to survive the split
# with several points left. Deliberately well below MIN_CALIBRATION_LABELS: demanding 30
# rejections before a user's confirmations can be used would put recalibration out of
# reach in normal use, which is a different way of getting the wrong curve (the stale one).
MIN_CALIBRATION_CLASS_LABELS = 10

# A calibration must be MONOTONE INCREASING: more evidence, more probability. A fit with
# a <= 0 is inverted and would report high confidence for weak evidence across the whole
# instrument. This is the ONLY guard that catches it -- on anti-correlated labels
# Nelder-Mead converges cleanly (res.success is True) and the inverted curve's held-out
# ECE beats the incumbent's, so neither a convergence check nor an ECE gate refuses it.
MIN_CALIBRATION_SLOPE = 0.0

# The curve must also DISCRIMINATE. Noise labels fit a ~ 0 with an excellent ECE (a
# constant predictor is perfectly calibrated and perfectly useless), passing both the sign
# and ECE gates while flattening every p_correct to the base rate. Held-out AUC is the
# honest test: because sigmoid(a*s+b) is monotone in s for a > 0, this measures whether the
# LABELS carry any signal about the score, which is the precondition for Platt-scaling them.
MIN_CALIBRATION_AUC = 0.6

# A refit must not be WORSE than the curve it replaces, measured on the same held-out
# points. Zero tolerance (float slop only) is not arbitrary strictness: on synthetic labels
# it accepts 94% of refits at n=200 and 100% at n>=400 when the incumbent is genuinely
# wrong, while refusing most refits that would replace an already-correct curve with a
# noisier estimate of itself. Refusing is recoverable (gather more labels, retry);
# activating a bad curve is instrument-wide and silent.
ECE_REGRESSION_TOL = 1e-9


class CalibrationRefused(ValueError):
    """A calibration could not be fit, or the fit is not usable as a calibration.

    Carries a stable ``reason`` code so an API can report *why* without parsing the
    message.
    """

    reason = "refused"

    def __init__(self, message: str, reason: str | None = None):
        super().__init__(message)
        if reason is not None:
            self.reason = reason


class InsufficientCalibrationData(CalibrationRefused):
    """Raised when there are too few / single-class labels to fit a calibration."""

    reason = "insufficient_labels"


class DegenerateCalibration(CalibrationRefused):
    """Raised when the fit converged on something that is not a usable curve.

    Three ways this happens, all of which would otherwise be activated silently: the
    optimizer failed; the slope is non-positive (the curve is INVERTED -- it reports high
    confidence for weak evidence); or the labels do not separate on the score at all, so
    the fitted curve is flat and every probability collapses to the base rate.
    """


def holdout_split(
    n: int, *, holdout: float = 0.5, seed: int = 0
) -> tuple[np.ndarray, np.ndarray]:
    """Deterministic ``(test_idx, train_idx)`` split of ``n`` points.

    Shared by :func:`fit_calibration` and :func:`recalibrate` so a refit's held-out ECE
    and the incumbent curve's ECE are measured on the SAME points. Comparing a held-out
    half against an all-label baseline is not a comparison: the ECE estimator is biased
    upward on fewer points, so the new curve loses a gate it should win (measured on
    labels drawn from the incumbent itself: 92% refused with an all-label baseline vs
    55-71% with this one).
    """
    rng = np.random.default_rng(seed)
    idx = rng.permutation(n)
    n_test = max(1, int(n * holdout))
    return idx[:n_test], idx[n_test:]


def discrimination_auc(scores: Sequence[float], is_correct: Sequence[bool]) -> float:
    """Rank-based AUC (Mann-Whitney U) of ``scores`` against binary labels.

    0.5 = the score carries no information about correctness on these labels; 1.0 = every
    correct assignment outscores every wrong one. ``NaN`` when only one class is present
    (an AUC is undefined without both). Ties get averaged ranks, so a score that is
    constant across the labels lands on exactly 0.5 rather than on the order the rows
    happened to arrive in. numpy only -- no new dependency.
    """
    s = np.asarray(scores, float)
    y = np.asarray(is_correct, int)
    pos = y == 1
    n_pos, n_neg = int(pos.sum()), int((~pos).sum())
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    order = np.argsort(s, kind="mergesort")
    ranks_sorted = np.arange(1, len(s) + 1, dtype=float)
    s_sorted = s[order]
    start = 0
    for i in range(1, len(s) + 1):
        if i == len(s) or s_sorted[i] != s_sorted[start]:
            ranks_sorted[start:i] = ranks_sorted[start:i].mean()
            start = i
    ranks = np.empty(len(s), float)
    ranks[order] = ranks_sorted
    return float((ranks[pos].sum() - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg))


def fit_calibration(
    scores: Sequence[float],
    is_correct: Sequence[bool],
    *,
    instrument: str | None = None,
    source: str | None = None,
    provisional: bool = True,
    holdout: float = 0.5,
    seed: int = 0,
) -> Calibration:
    """Fit a :class:`Calibration` from labelled ``(score, is_correct)`` pairs.

    Platt-fits ``(a, b)`` on a random train split and reports the **held-out** ECE as the
    curve's quality, so a calibration carries an honest, out-of-sample calibration error.
    ``instrument`` / ``source`` / ``provisional`` are recorded as provenance.

    Refusal is by exception, never by returning a degraded object: a curve that is not
    monotone increasing, or that carries no information, is not a *worse* calibration --
    it is not one. Returning it would mean every caller has to remember to check, and the
    one that forgets rewrites P(correct) for a whole instrument.

    :raises InsufficientCalibrationData: fewer than ``MIN_CALIBRATION_LABELS`` labels in
        total or fewer than ``MIN_CALIBRATION_CLASS_LABELS`` of either class.
    :raises DegenerateCalibration: the optimize did not converge, the fitted slope is not
        positive (an inverted curve), or the labels do not separate on the score
        (held-out AUC below ``MIN_CALIBRATION_AUC``).
    """
    from datetime import datetime, timezone

    s = np.asarray(scores, float)
    y = np.asarray(is_correct, int)
    mask = np.isfinite(s)
    s, y = s[mask], y[mask]
    n_pos, n_neg = int(y.sum()), int((y == 0).sum())
    if (
        len(s) < MIN_CALIBRATION_LABELS
        or n_pos < MIN_CALIBRATION_CLASS_LABELS
        or n_neg < MIN_CALIBRATION_CLASS_LABELS
    ):
        raise InsufficientCalibrationData(
            f"need >= {MIN_CALIBRATION_LABELS} labels with >= "
            f"{MIN_CALIBRATION_CLASS_LABELS} of each class "
            f"(got n={len(s)}, pos={n_pos}, neg={n_neg})"
        )
    test, train = holdout_split(len(s), holdout=holdout, seed=seed)
    # Guard: if the split leaves a single class in train, fit on all data instead.
    if y[train].sum() == 0 or (y[train] == 0).sum() == 0:
        train = np.arange(len(s))
    a, b, converged = _platt_fit(s[train], y[train])
    if not converged or not (np.isfinite(a) and np.isfinite(b)):
        raise DegenerateCalibration(
            f"Platt fit did not converge (a={a}, b={b})", reason="fit_not_converged"
        )
    if a <= MIN_CALIBRATION_SLOPE:
        raise DegenerateCalibration(
            f"fitted curve is not monotone increasing (a={a:.4f}, b={b:.4f}): it would "
            f"report higher confidence for weaker evidence",
            reason="non_monotonic_fit",
        )
    auc = discrimination_auc(s[test], y[test])
    if not np.isfinite(auc):
        # A single-class holdout says nothing; fall back to all labels (both classes are
        # guaranteed present by the InsufficientCalibrationData check above).
        auc = discrimination_auc(s, y)
    if auc < MIN_CALIBRATION_AUC:
        raise DegenerateCalibration(
            f"labels do not separate on the score (held-out AUC {auc:.3f} < "
            f"{MIN_CALIBRATION_AUC}); the fitted curve carries no information",
            reason="no_discrimination",
        )
    ece = calibration_error(apply_calibration(s[test], (a, b)), y[test])
    return Calibration(
        a=a,
        b=b,
        instrument=instrument,
        n_pos=int(y.sum()),
        n_neg=int((y == 0).sum()),
        ece=round(float(ece), 4) if np.isfinite(ece) else None,
        fit_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        source=source,
        provisional=provisional,
    )


# Evidence levels trusted enough to declare a calibration non-provisional (the confirmation-bias
# guardrail: a visual eyeball shouldn't graduate a curve from "provisional" to "real").
STRONG_EVIDENCE = ("reference_standard", "msms")


def recalibrate(
    scores: Sequence[float],
    labels: Sequence[int],
    evidence_levels: Sequence[str | None] | None = None,
    *,
    instrument: str,
    source: str,
    current: "Calibration | None" = None,
    strong_evidence: Sequence[str] = STRONG_EVIDENCE,
    provisional_min_strong: int = MIN_CALIBRATION_LABELS,
    holdout: float = 0.5,
    seed: int = 0,
) -> dict:
    """Fit a fresh calibration from labelled ``(score, is_correct)`` pairs and report the change.

    The V2 refit of the verification-calibration loop: turns a batch of user verdicts (positives =
    confirmed, negatives = rejected; ``score`` is the arbitration evidence snapshotted at
    verification) into a new :class:`Calibration`, carrying the corroboration weights forward from
    ``current`` (those are refit separately, from the offset-decoy benchmark).

    The ``provisional`` gate is the honest part: the curve stays provisional unless at least
    ``provisional_min_strong`` positives carry *strong* evidence (``strong_evidence`` — reference
    standard / MS-MS by default), so a pile of visual-only confirmations can't graduate it to "real".

    ``activate`` is the second half of that honesty and the division of labour with the caller:
    this function decides whether the curve is *usable* (by raising) and whether it *beats the
    incumbent* (by comparing the two held-out ECEs); the caller decides whether to persist it.
    A refit that is worse than the curve it would replace is reported, not raised -- the caller
    legitimately wants to see the params and the two errors before dropping it on the floor.

    :returns: ``{calibration, before_ece, after_ece, n_pos, n_neg, n_strong_positives, provisional,
        activate, refusal}`` where ``before_ece`` is ``current`` scored on the same held-out points
        as ``after_ece`` (None if no current curve), and ``refusal`` names the failed gate when
        ``activate`` is False.
    :raises CalibrationRefused: the labels cannot be fit (:class:`InsufficientCalibrationData`) or
        the fit is not a usable curve (:class:`DegenerateCalibration`). Not swallowed: a caller
        that gets a dict back must be able to trust it.
    """
    s = np.asarray(scores, float)
    y = np.asarray(labels, int)
    before_ece = None
    if current is not None:
        # Score the incumbent on exactly the points the new curve is held out on. The
        # finite mask has to be applied FIRST because fit_calibration masks before it
        # splits, so anything else indexes into a different array. Comparing a held-out
        # half against an all-label baseline would not be a comparison at all -- see
        # holdout_split.
        finite = np.isfinite(s)
        s_f, y_f = s[finite], y[finite]
        if len(s_f):
            test, _ = holdout_split(len(s_f), holdout=holdout, seed=seed)
            before_ece = calibration_error(
                apply_calibration(s_f[test], current), y_f[test]
            )
    cal = fit_calibration(
        s,
        y,
        instrument=instrument,
        source=source,
        provisional=True,
        holdout=holdout,
        seed=seed,
    )
    n_strong = 0
    if evidence_levels is not None:
        strong = set(strong_evidence)
        n_strong = sum(
            1 for lvl, lab in zip(evidence_levels, y) if lab == 1 and lvl in strong
        )
    provisional = n_strong < provisional_min_strong
    cal = replace(
        cal,
        provisional=provisional,
        corroboration_weights=(
            current.corroboration_weights if current is not None else None
        ),
    )
    before_ece = round(float(before_ece), 4) if before_ece is not None else None
    refusal = None
    if (
        before_ece is not None
        and cal.ece is not None
        and cal.ece > before_ece + ECE_REGRESSION_TOL
    ):
        refusal = "ece_regression"
    return {
        "calibration": cal,
        "before_ece": before_ece,
        "after_ece": cal.ece,
        "n_pos": cal.n_pos,
        "n_neg": cal.n_neg,
        "n_strong_positives": n_strong,
        "provisional": provisional,
        "activate": refusal is None,
        "refusal": refusal,
    }


# ---------------------------------------------------------------------------
# Per-instrument registry.
#
# Ships one PROVISIONAL Orbitrap curve fit from the demo golden set (evidence =
# fit x plausibility of the arbitrated winners vs near-mass decoys); fit via
# arbitration_eval.py --fit-calibration (untracked scratch alongside the
# tooling/score_eval harness, not in the repo). There is deliberately
# NO TOF curve: no curated TOF golden set exists yet, so TOF stays uncalibrated and
# callers must report its assignments as such rather than borrow the Orbitrap curve.
# The long-term store is per-instrument and user-refittable (a DB-backed follow-up).
# ---------------------------------------------------------------------------

# Provisional Orbitrap calibration of the arbitration evidence, fit on the demo bundle
# (Br neg + Ur pos) via `arbitration_eval.py --fit-calibration` over all candidates
# (true vs decoy). PRELIMINARY -- a placeholder until a curated reference dataset replaces
# it; refit with `fit_calibration` and update here (or, later, the per-instrument store).
# Held-out ECE 0.029; e.g. evidence 0.3 -> P 0.16, 0.6 -> 0.52, 0.9 -> 0.86.
# Per-adduct corroboration log-odds, measured on the same demo goldens via the offset-decoy
# benchmark (real adduct offset vs anchor-swap null; tooling/score_eval/corroboration_benchmark.py
# and _corroboration_metrics.json). Distinctive reagent adducts corroborate strongly, generic
# protonation/deprotonation ~0. PROVISIONAL, instrument+library specific -- refit per deployment.
PROVISIONAL_ORBITRAP_CORROBORATION = {
    "+Br-": 2.28,
    "+NH4+": 0.83,
    "+(CH4N2O)H+": 0.70,
    "+H+": 0.0,
    "-H+": 0.0,
    "-H-": 0.0,
}

PROVISIONAL_ORBITRAP = Calibration(
    a=5.7380,
    b=-3.3625,
    instrument="orbi",
    n_pos=17329,
    n_neg=81754,
    ece=0.0289,
    # The one curve that actually ships must carry the provenance the class exists for;
    # a null here says "nobody knows when this was fit", which for the default Orbitrap
    # calibration is the field that decides whether it is stale. Date-only precision is
    # deliberate: the eval run's wall clock was not recorded, so this is the date the
    # fitted parameters landed in the tree, and ISO-8601 lets us say exactly that rather
    # than invent a time of day.
    fit_utc="2026-07-08",
    source="demo goldens (Br/Ur, preliminary)",
    provisional=True,
    corroboration_weights=PROVISIONAL_ORBITRAP_CORROBORATION,
)

INSTRUMENT_CALIBRATIONS: dict[str, Calibration] = {"orbi": PROVISIONAL_ORBITRAP}


def calibration_for(instrument: str | None) -> Calibration | None:
    """The calibration for an instrument class, or ``None`` when none exists.

    ``None`` is a first-class answer: the caller MUST then treat the assignment as
    uncalibrated (report the raw evidence + an ``uncalibrated`` flag) rather than apply
    another instrument's curve. Unknown / missing instruments return ``None``."""
    if not instrument:
        return None
    return INSTRUMENT_CALIBRATIONS.get(str(instrument).lower())
