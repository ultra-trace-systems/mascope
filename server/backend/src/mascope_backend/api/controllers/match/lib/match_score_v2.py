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
(compute_match_isotopes -> load_peaks coord -> _parse_and_filter_peaks -> _match_assign)
and persisted on `match_isotope`, so the DB-read paths carry it too. A row without a
value (files with no noise data, and rows stored before the column existed) is passed
through as NaN, i.e. an honest "no SNR for this row", which `score_pattern_v2` scores
in its no-SNR mode PER ROW (fixed instrument mass width, abundance-floor intensity
tolerance; the detectability gate keys on the BASE row's SNR, falling back to the
abundance gate only when that is unknown). There is deliberately no
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

from mascope_tools.composition.heuristic_filter import (
    calibrate_score,
    score_pattern_v2,
    score_patterns_v2,
)
from mascope_tools.composition.mass_accuracy import PRED_SIGMA_PPM


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
    mu: float | None = 0.0,
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

    `mu` is the sample's fitted mass offset, and ``None`` means none was fitted -
    `fit_mass_accuracy` answers that for a sample with too few anchors to measure one.
    An unmeasured offset is scored as no offset, which is what "uncorrected" means;
    it is stated here rather than at each call site so that the three of them cannot
    answer it differently, and so that a caller wanting to know whether an offset was
    measured has to ask the fit rather than read it off a zero.

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
    mu = 0.0 if mu is None else float(mu)
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
    # Per-row NaN/None is a first-class value: "no SNR for this row" (files without
    # noise data, and rows stored before match_isotope persisted the column). A frame
    # with no column at all says the same for the whole ion.
    snr = (
        pd.to_numeric(g["signal_to_noise"], errors="coerce").to_numpy(float)
        if "signal_to_noise" in g
        else None
    )
    sig = float(np.hypot(sigma_ppm, PRED_SIGMA_PPM)) if sigma_ppm is not None else None
    raw = score_pattern_v2(me, oi, snr, pr, sigma_ppm=sig)
    return float(calibrate_score(raw, calibration)) if calibrate else float(raw)


def ion_scores_v2(
    frame: pd.DataFrame,
    by: str,
    *,
    sigma_ppm: float | None = None,
    mu: float | None = 0.0,
) -> pd.Series:
    """:func:`ion_score_v2` for every ion of a frame, scored together.

    The same fit, ion by ion, as grouping the frame on ``by`` and applying
    :func:`ion_score_v2` to each group, equal to it to floating-point rounding:
    each ion's rows are put most abundant first and a missing abundance last,
    as that function sorts them, read the same way, and laid end to end for
    ``heuristic_filter.score_patterns_v2``, which scores every envelope in one
    pass instead of one call per ion.

    The first row is the anchor every other line is read against, so it is
    the one place the order reaches the score. Where an ion's top abundance is
    tied, ``sort_values`` breaks the tie with numpy's quicksort, which is not
    stable, and the tie is settled here by that same sort over that ion's rows
    alone; the other rows' order changes the sums' rounding and nothing else.

    :param frame: Isotopologue rows as :func:`ion_score_v2` takes them, any
        number of ions.
    :param by: The column naming each row's ion. A missing value is an ion of
        its own, as ``groupby(dropna=False)`` makes it.
    :param sigma_ppm: As :func:`ion_score_v2`.
    :param mu: As :func:`ion_score_v2`.
    :return: Each row's ion's fit, on the frame's index.
    """
    if frame.empty:
        return pd.Series(dtype=float, index=frame.index)
    mu = 0.0 if mu is None else float(mu)
    codes, _ = pd.factorize(frame[by], use_na_sentinel=False)
    abundance = pd.to_numeric(frame["relative_abundance"], errors="coerce").to_numpy(
        float
    )
    missing = np.isnan(abundance)
    # Each ion's rows together, most abundant first and a missing abundance
    # last, in the frame's order within a tie.
    order = np.lexsort(
        (np.arange(len(frame)), np.where(missing, 0.0, -abundance), missing, codes)
    )
    lengths = np.bincount(codes, minlength=codes.max() + 1)
    offsets = np.concatenate([[0], np.cumsum(lengths)])
    starts = offsets[:-1]

    pr = abundance[order]
    with np.errstate(invalid="ignore"):
        peak = np.fmax.reduceat(pr, starts)
    # An ion with no positive abundance scores 0, as alone. One whose only
    # values are infinite passes here and scores 0 all the same: divided by
    # its infinite top, every abundance is 0.
    scored = peak > 0
    tied = np.add.reduceat(pr == np.repeat(peak, lengths), starts) > 1
    for ion in np.flatnonzero(tied):
        lines = slice(offsets[ion], offsets[ion + 1])
        rows = np.sort(order[lines])
        values = abundance[rows]
        known = ~np.isnan(values)
        # ``nargsort`` descending: the reversed rows sorted ascending, reversed.
        reversed_rows = rows[known][::-1]
        anchor = reversed_rows[values[known][::-1].argsort(kind="quicksort")][-1]
        rest = order[lines][order[lines] != anchor]
        order[lines] = np.concatenate([[anchor], rest])
    pr = abundance[order]
    with np.errstate(divide="ignore", invalid="ignore"):
        pr = np.nan_to_num(pr) / np.repeat(peak, lengths)

    oi = (
        pd.to_numeric(frame["sample_peak_intensity"], errors="coerce")
        .fillna(0.0)
        .to_numpy(float)[order]
    )
    me = (
        pd.to_numeric(frame["match_mz_error"], errors="coerce").to_numpy(float)[order]
        - mu
    )
    if "is_satellite" in frame.columns:
        satellite = frame["is_satellite"].fillna(False).astype(bool).to_numpy()[order]
        oi = np.where(satellite, 0.0, oi)
    snr = (
        pd.to_numeric(frame["signal_to_noise"], errors="coerce").to_numpy(float)[order]
        if "signal_to_noise" in frame
        else None
    )
    sig = float(np.hypot(sigma_ppm, PRED_SIGMA_PPM)) if sigma_ppm is not None else None
    scores = score_patterns_v2(me, oi, snr, pr, offsets, sigma_ppm=sig)
    scores = np.where(scored, scores, 0.0)
    return pd.Series(scores[codes], index=frame.index, dtype=float)
