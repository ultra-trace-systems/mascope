"""v2 match score (mascope_tools) — backend adapter.

Computes the consolidated, detectability-gated, SNR-aware per-ion match score
(`mascope_tools.composition.heuristic_filter.score_pattern_v2`) from one ion's
isotopologue rows as produced by `compute_match_isotopes`. Wired alongside the
legacy `Σ score_i·rel_ab` aggregation behind `MATCH_SCORE_VERSION` so the two can
be switched and compared; v1 stays byte-identical.

Background in tooling/score_eval/DESIGN.md (untracked scratch, not in the repo).
The score returned is the raw fit quality; the optional Platt calibration must
be supplied by the caller (see `ion_score_v2`).

NOTE: this is a pure function (unit-tested via mascope_tools). End-to-end behaviour
in the live match pipeline must be validated with the backend test suite.

SNR: real `signal_to_noise` is carried on the isotope rows of the compute path
(compute_match_isotopes -> load_peaks coord -> _parse_and_filter_peaks -> _match_assign).
The DB-read paths have none — `match_isotope` does not persist the column — and rows
missing it are passed through as NaN, i.e. an honest "no SNR for this row", which
`score_pattern_v2` scores in its no-SNR mode (fixed instrument mass width, abundance-floor
intensity tolerance, abundance-based detectability gate). There is deliberately no
intensity-derived proxy SNR: intensity over a per-sample intensity percentile measures
DYNAMIC RANGE, not signal-to-noise, and the ~4 it typically produced for a base peak
silently disabled the detectability gate and widened every mass tolerance. It also made
one ion's score depend on which unrelated targets shared the request, breaking the
determinism the fit score is documented to have.
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd

from mascope_tools.composition.heuristic_filter import calibrate_score, score_pattern_v2


# Correct matches spread wider than the calibration-anchor precision (centroiding +
# prediction error + analyte tail); added in quadrature to the fitted instrument sigma.
PRED_SIGMA_PPM = 0.5


def match_score_version() -> int:
    """Backend match-score switch: 1 = legacy Sum(score*rel_ab), 2 = the consolidated
    fit score (mascope_tools v2). Env `MASCOPE_MATCH_SCORE_VERSION`.

    Default is **1** on the legacy targeted path: on the peak-centric integration the
    fit score is adopted *deliberately* (it is the scoring engine for the peak-centric
    Stage A/B engine and the `fit_score` column), not by silently changing the legacy
    targeted behaviour. Set =2 to score the legacy path with the fit score for
    comparison. Both paths stay wired."""
    try:
        return int(os.environ.get("MASCOPE_MATCH_SCORE_VERSION", "1"))
    except (TypeError, ValueError):
        return 1  # malformed value -> the default


def fit_sample_mass_accuracy(
    match_isotope_df: pd.DataFrame,
) -> tuple[float, float | None]:
    """Robust (mu, sigma) ppm of the matched isotopologues' mass error — the
    instrument's measured mass accuracy (resolution-correct, Orbitrap vs TOF).
    Returns sigma=None when there are too few matched anchors (caller falls back)."""
    me = pd.to_numeric(match_isotope_df.get("match_mz_error"), errors="coerce")
    inten = pd.to_numeric(
        match_isotope_df.get("sample_peak_intensity"), errors="coerce"
    )
    me = me[(inten.fillna(0) > 0) & me.notna()]
    if len(me) < 8:
        return 0.0, None
    mu = float(me.median())
    sigma = max(float(1.4826 * (me - mu).abs().median()), 0.05)
    return mu, sigma


def sample_noise_floor(match_isotope_df: pd.DataFrame) -> float:
    """2nd-percentile matched intensity — a per-sample intensity floor.

    It does NOT feed the fit score any more (see the module docstring: it is a
    dynamic-range statistic, not a noise estimate). Retained because four call sites still
    compute and pass it as `ion_score_v2(noise=...)`, where it is now ignored; dropping it
    from them is follow-up cleanup."""
    inten = pd.to_numeric(
        match_isotope_df.get("sample_peak_intensity"), errors="coerce"
    )
    inten = inten[inten > 0]
    return float(np.percentile(inten, 2)) if len(inten) else 1.0


def ion_score_v2(
    group: pd.DataFrame,
    *,
    sigma_ppm: float | None = None,
    mu: float = 0.0,
    noise: float = 1.0,
    calibrate: bool = False,
    calibration: tuple[float, float] | None = None,
) -> float:
    """v2 match score for ONE ion: the FIT QUALITY of its isotopologue rows against the
    predicted pattern (matched + unmatched).

    This is a pure measurement of *how well the data fits this assignment* (mass,
    intensity, SNR-detectability, isotopic pattern) on [0, 1], 1.0 = perfect. It is
    deliberately competitor-blind: mass alone cannot prove a composition, so deciding
    *which* of several well-fitting formulas is correct is a separate identification /
    arbitration layer (peaky), NOT this score.

    `group` must contain `relative_abundance`, `match_mz_error`, `sample_peak_intensity`
    (and optionally `signal_to_noise`). Sorts by predicted abundance (base first), builds
    the matched arrays, and calls `score_pattern_v2`. Rows without a usable
    `signal_to_noise` are scored in its no-SNR mode; there is no proxy SNR (module
    docstring).

    `noise` is accepted and IGNORED. It fed the deleted intensity-derived proxy SNR, which
    conflated dynamic range with signal-to-noise; the kwarg stays so the four call sites
    that still pass a `sample_noise_floor` keep working until they are cleaned up.

    `calibrate=True` recasts the fit as a single-candidate P(correct) — a confidence-layer
    concern, not the headline match score — and REQUIRES `calibration`, the Platt `(a, b)`
    fitted for this instrument/dataset. The library default was fitted on the demo
    Orbitrap goldens (and on the real-SNR path), so applying it to an arbitrary instrument
    is exactly the mismatch the per-instrument calibration exists to prevent."""
    if calibrate and calibration is None:
        raise ValueError(
            "ion_score_v2(calibrate=True) requires calibration=(a, b), the Platt curve "
            "fitted for this instrument/dataset; the library default "
            "(DEFAULT_CALIBRATION_V2) was fitted on the demo Orbitrap golden set and is "
            "not transferable."
        )
    g = group.sort_values("relative_abundance", ascending=False)
    pr = pd.to_numeric(g["relative_abundance"], errors="coerce").to_numpy(float)
    if pr.size == 0 or not np.isfinite(pr).any() or np.nanmax(pr) <= 0:
        return 0.0
    pr = np.nan_to_num(pr) / np.nanmax(pr)  # base-relative
    oi = (
        pd.to_numeric(g["sample_peak_intensity"], errors="coerce")
        .fillna(0.0)
        .to_numpy(float)
    )
    # No .fillna(0.0) here: an unusable mass error is not a perfect one. An absent row is
    # already absent by intensity, and a *matched* row whose error is NaN must not be
    # handed a free perfect mass likelihood — score_pattern_v2 routes it to the gate.
    me = pd.to_numeric(g["match_mz_error"], errors="coerce").to_numpy(float) - mu
    # A predicted isotopologue that "matched" a satellite (artifact near an intense
    # peak) is not a real match — treat it as absent so the detectability gate applies.
    if "is_satellite" in g.columns:
        sat = g["is_satellite"].fillna(False).astype(bool).to_numpy()
        oi = np.where(sat, 0.0, oi)
    # Per-row NaN is a first-class value: "no SNR for this row". None says the same for
    # the whole ion (the DB-read paths, which carry no signal_to_noise column at all).
    snr = (
        pd.to_numeric(g["signal_to_noise"], errors="coerce").to_numpy(float)
        if "signal_to_noise" in g
        else None
    )
    sig = float(np.hypot(sigma_ppm, PRED_SIGMA_PPM)) if sigma_ppm is not None else None
    raw = score_pattern_v2(me, oi, snr, pr, sigma_ppm=sig)
    return float(calibrate_score(raw, calibration)) if calibrate else float(raw)
