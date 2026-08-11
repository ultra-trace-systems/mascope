"""#2 — Match-score evaluation harness (baseline + regression gate).

Scores the demo golden dataset with `mascope_tools` and reports the four numbers
that define whether a scorer is good (DESIGN.md §5.3 — untracked scratch, kept
out of the repo):

  reproducibility  scoring is deterministic (re-run → identical scores)
  ranking          per peak, does the TRUE formula outrank its near-mass decoys?
                   (top-1 rate + ROC-AUC over candidate pools from make_candidates.py)
  calibration      does score ≈ P(correct)?  (reliability curve + ECE)
  score dist.      distribution of the true assignments' scores (the baseline)

It is both the DECISION MECHANISM (which scorer/params win) and a CI REGRESSION
GATE: pass `--baseline <json>` and it exits non-zero if ranking/AUC regress.

Scoring is done at the ION level — `predict_isotopes(ion[:-1], charge)` — against
each file's observed peaks. NOTE (v1 limitation): the "observed peaks" are the
bundle's matched-target peaks (`expected/peaks.parquet`), a proxy for the full
spectrum; a decoy isotopologue landing on an un-matched real peak is missed. A
full-spectrum peak list (Phase A.5) tightens ranking/calibration; the baseline and
reproducibility numbers are exact regardless.

    python score_eval.py <bundle_dir> [--ppm 5] [--baseline base.json] [--out base.json]
"""

from __future__ import annotations

import argparse
import json
import sys

import numpy as np
import pandas as pd


MZ_PPM = 5.0
INT_TOL = 0.4  # mascope_tools ISOTOPE_MATCHING_INTENSITY_TOLERANCE
# Correct matches spread WIDER than the calibration-anchor precision (centroiding +
# prediction error + the analyte tail). Added in quadrature to the fitted instrument
# sigma so a tiny fitted sigma doesn't crush legitimate true ions. Instrument-agnostic.
PRED_SIGMA_PPM = 0.5


def load_filestore_peaks(
    bundle_dir: str, filename: str, _cache: dict = {}
) -> "pd.DataFrame | None":
    """Full detected-peak list for a sample from its `peak_timeseries.zarr` in the
    filestore: m/z, summed height, real `signal_to_noise`, and `is_satellite`. This
    is the COMPLETE spectrum (not the matched-target subset) WITH SNR — so it
    replaces both the matched-peak proxy and the proxy noise floor. None if absent."""
    if filename in _cache:
        return _cache[filename]
    import glob

    import xarray as xr

    hits = glob.glob(
        f"{bundle_dir}/snapshot/filestore/**/{filename}/peak_timeseries.zarr",
        recursive=True,
    )
    if not hits:
        _cache[filename] = None
        return None
    ds = xr.open_zarr(hits[0])
    df = (
        pd.DataFrame(
            {
                "mz": ds["mz"].values.astype(float),
                "height": ds["sum_peak_heights"].values.astype(float),
                "snr": ds["signal_to_noise"].values.astype(float),
                "is_satellite": ds["is_satellite"].values.astype(bool),
            }
        )
        .sort_values("mz")
        .reset_index(drop=True)
    )
    _cache[filename] = df
    return df


def fit_mass_accuracy(
    bundle_dir: str, filename: str, _cache: dict = {}, _exp: list = [None]
) -> tuple:
    """Robust (mu, sigma, n) of the ppm mass error for a sample, fit from its TRUE
    M0 assignments (the seeded targets, observed m/z vs theoretical) — i.e. the
    instrument's MEASURED mass accuracy. mu = median ppm error, sigma = 1.4826*MAD.
    Returns sigma=None when there are too few anchors (caller falls back). This is
    the value that makes the mass term resolution-correct (Orbitrap vs TOF)."""
    if filename in _cache:
        return _cache[filename]
    from mascope_tools.composition.heuristic_filter import predict_isotopes

    if _exp[0] is None:
        _exp[0] = pd.read_parquet(f"{bundle_dir}/expected/peaks.parquet")
    exp = _exp[0]
    m0 = exp[
        (exp["filename"] == filename)
        & ~exp["target_isotope_formula"].astype(str).str.contains(r"\[", regex=True)
    ]
    errs = []
    for r in m0.itertuples():
        ion = str(r.target_isotope_formula)
        if not ion or ion[-1] not in "+-":
            continue
        try:
            theo = float(
                predict_isotopes(ion[:-1], 1 if ion[-1] == "+" else -1, None)[0][0]
            )
        except Exception:
            continue
        errs.append((float(r.mz) - theo) / theo * 1e6)
    if len(errs) < 8:
        res = (0.0, None, len(errs))
    else:
        e = np.asarray(errs)
        mu = float(np.median(e))
        sigma = max(float(1.4826 * np.median(np.abs(e - mu))), 0.05)  # honest fit
        res = (mu, sigma, len(errs))
    _cache[filename] = res
    return res


def score_ion(
    mzs: np.ndarray, ints: np.ndarray, ion: str, *, ppm: float
) -> float | None:
    """Score one ion formula (e.g. 'C10H13O+') against a sorted observed peak list,
    mirroring peaky's validated local_scoring matching loop. Returns the
    mascope_tools score, or None if the monoisotopic peak isn't present."""
    from mascope_tools.composition.heuristic_filter import (
        predict_isotopes,
        score_pattern,
    )

    sign = ion[-1] if ion and ion[-1] in "+-" else ""
    if not sign:
        return None
    charge = 1 if sign == "+" else -1
    try:
        pred_mz, pred_int, _ = predict_isotopes(ion[:-1], charge, None)
    except Exception:
        return None
    if len(pred_mz) == 0:
        return None
    pred_rel = pred_int / pred_int[0]
    obs_mz = np.zeros_like(pred_mz)
    obs_int = np.zeros_like(pred_mz)
    obs_mz_err = np.zeros_like(pred_mz)
    obs_int_err = np.zeros_like(pred_mz)
    base_int = None
    for i, pmz in enumerate(pred_mz):
        d = pmz * ppm * 1e-6
        lo = np.searchsorted(mzs, pmz - d, "left")
        hi = np.searchsorted(mzs, pmz + d, "right")
        if lo >= hi:
            continue
        k = lo + int(np.argmin(np.abs(mzs[lo:hi] - pmz)))
        if i == 0:
            base_int = ints[k]
            obs_int[0] = ints[k]
            obs_mz[0] = mzs[k]
            obs_mz_err[0] = abs(mzs[k] - pmz) / pmz * 1e6
            continue
        if not base_int:
            continue
        rel_obs = ints[k] / base_int
        ierr = abs(pred_rel[i] - rel_obs) / pred_rel[i]
        if ierr <= INT_TOL:
            obs_int[i] = ints[k]
            obs_mz[i] = mzs[k]
            obs_mz_err[i] = abs(mzs[k] - pmz) / pmz * 1e6
            obs_int_err[i] = ierr
    if base_int is None:
        return None
    return float(score_pattern(obs_mz, obs_mz_err, obs_int, obs_int_err, pred_rel))


def _match_arrays(mzs, ints, snrs, ion, *, ppm, mu=0.0, noise=0.0):
    """Build v2's per-isotopologue matched arrays (obs_me, obs_int, obs_snr, pred_rel)
    for one ion by matching its predicted envelope to the observed peaks. None if the
    ion can't be predicted or its monoisotopic peak is absent. Mass errors are
    offset-centred by mu; SNR falls back to height/noise when not provided."""
    from mascope_tools.composition.heuristic_filter import predict_isotopes

    sign = ion[-1] if ion and ion[-1] in "+-" else ""
    if not sign:
        return None
    charge = 1 if sign == "+" else -1
    try:
        pred_mz, pred_int, _ = predict_isotopes(ion[:-1], charge, None)
    except Exception:
        return None
    if len(pred_mz) == 0:
        return None
    pred_rel = pred_int / pred_int[0]
    n = len(pred_mz)
    obs_int = np.zeros(n)
    obs_me = np.zeros(n)
    obs_snr = np.zeros(n)
    for i, pmz in enumerate(pred_mz):
        d = pmz * ppm * 1e-6
        lo = np.searchsorted(mzs, pmz - d, "left")
        hi = np.searchsorted(mzs, pmz + d, "right")
        if hi <= lo:
            continue
        k = lo + int(np.argmin(np.abs(mzs[lo:hi] - pmz)))
        obs_int[i] = ints[k]
        obs_me[i] = (mzs[k] - pmz) / pmz * 1e6 - mu  # offset-centred; library abs()es
        obs_snr[i] = float(snrs[k]) if snrs is not None else 0.0
    if obs_int[0] <= 0:
        return None  # monoisotopic must be present
    if snrs is None:  # proxy SNR from the noise floor
        obs_snr = np.where(obs_int > 0, obs_int / max(noise, 1e-9), 0.0)
    return obs_me, obs_int, obs_snr, pred_rel


def score_ion_v2(
    mzs: np.ndarray,
    ints: np.ndarray,
    snrs: "np.ndarray | None",
    ion: str,
    *,
    ppm: float,
    noise: float = 0.0,
    k_detect: float = 3.0,
    miss_penalty: float = 0.3,
    sigma_ppm: float | None = None,
    mu: float = 0.0,
    snr_intensity: bool = True,
) -> float | None:
    """Match the candidate ion's envelope and delegate to the PROMOTED library scorer
    `mascope_tools...score_pattern_v2` (single source of truth). `snr_intensity` is
    retained for signature compat; v2 always uses SNR-set intensity tolerance."""
    from mascope_tools.composition.heuristic_filter import score_pattern_v2

    arrs = _match_arrays(mzs, ints, snrs, ion, ppm=ppm, mu=mu, noise=noise)
    if arrs is None:
        return None
    obs_me, obs_int, obs_snr, pred_rel = arrs
    return score_pattern_v2(
        obs_me,
        obs_int,
        obs_snr,
        pred_rel,
        k_detect=k_detect,
        miss_penalty=miss_penalty,
        sigma_ppm=sigma_ppm,
    )


def score_v2_resample_std(
    mzs,
    ints,
    snrs,
    ion,
    *,
    ppm,
    mu=0.0,
    noise=0.0,
    sigma_ppm=None,
    k_detect=3.0,
    miss_penalty=0.3,
    n=16,
    rng=None,
):
    """ROBUSTNESS metric: std of the v2 score when each matched peak's intensity is
    resampled within its own measurement noise (Gaussian, sigma = intensity/SNR). A
    robust score barely moves under noise. None if the ion can't be scored."""
    from mascope_tools.composition.heuristic_filter import score_pattern_v2

    arrs = _match_arrays(mzs, ints, snrs, ion, ppm=ppm, mu=mu, noise=noise)
    if arrs is None:
        return None
    obs_me, obs_int, obs_snr, pred_rel = arrs
    rng = rng or np.random.default_rng(0)
    matched = obs_int > 0
    sigma_int = np.where(
        matched & (obs_snr > 0), obs_int / np.maximum(obs_snr, 1e-9), 0.0
    )
    scores = []
    for _ in range(n):
        pert = np.where(
            matched, np.maximum(obs_int + rng.normal(0.0, sigma_int), 0.0), 0.0
        )
        if pert[0] <= 0:  # base perturbed below zero -> skip this draw
            continue
        scores.append(
            score_pattern_v2(
                obs_me,
                pert,
                obs_snr,
                pred_rel,
                k_detect=k_detect,
                miss_penalty=miss_penalty,
                sigma_ppm=sigma_ppm,
            )
        )
    return float(np.std(scores)) if len(scores) >= 2 else None


def _roc_auc(y_true: np.ndarray, y_score: np.ndarray) -> float:
    """Rank-based ROC-AUC (P[true scored above a random decoy]); no sklearn dep."""
    pos, neg = y_score[y_true == 1], y_score[y_true == 0]
    if len(pos) == 0 or len(neg) == 0:
        return float("nan")
    order = np.argsort(y_score, kind="mergesort")
    ranks = np.empty(len(y_score), float)
    ranks[order] = np.arange(1, len(y_score) + 1)
    # average ranks for ties
    s = y_score[order]
    i = 0
    while i < len(s):
        j = i
        while j + 1 < len(s) and s[j + 1] == s[i]:
            j += 1
        if j > i:
            ranks[order[i : j + 1]] = (i + 1 + j + 1) / 2
        i = j + 1
    return float(
        (ranks[y_true == 1].sum() - len(pos) * (len(pos) + 1) / 2)
        / (len(pos) * len(neg))
    )


def _platt_fit(s: np.ndarray, y: np.ndarray) -> tuple:
    """Platt scaling: fit P(correct) = sigmoid(a*score + b) by log-loss. Returns
    (a, b). This is the calibration LAYER — it turns the raw fit score into a
    probability; it would be fit once on the golden set and shipped with the
    SCORE_VERSION."""
    from scipy.optimize import minimize

    def nll(p):
        z = p[0] * s + p[1]
        return float(np.where(y == 1, np.logaddexp(0, -z), np.logaddexp(0, z)).mean())

    return tuple(minimize(nll, [3.0, -1.5], method="Nelder-Mead").x)


def _sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-z))


def _ece(scores: np.ndarray, correct: np.ndarray, bins: int = 10) -> float:
    edges = np.linspace(0, 1, bins + 1)
    ece = 0.0
    for b in range(bins):
        m = (scores >= edges[b]) & (
            scores < edges[b + 1] if b < bins - 1 else scores <= 1.0
        )
        if m.sum():
            ece += m.mean() * abs(correct[m].mean() - scores[m].mean())
    return float(ece)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("bundle_dir")
    ap.add_argument("--ppm", type=float, default=MZ_PPM)
    ap.add_argument(
        "--scorer",
        choices=["v1", "v2"],
        default="v1",
        help="v1 = current mascope_tools; v2 = detectability-gated",
    )
    ap.add_argument(
        "--k-detect",
        type=float,
        default=3.0,
        help="v2: min expected SNR to call an absent isotopologue 'missing'",
    )
    ap.add_argument(
        "--miss-penalty",
        type=float,
        default=0.3,
        help="v2: likelihood for a detectable-but-absent isotopologue",
    )
    ap.add_argument(
        "--fit-sigma",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="v2: fit per-sample mass accuracy (mu,sigma) from true assignments "
        "and use it (resolution-correct); --no-fit-sigma uses --sigma-ppm",
    )
    ap.add_argument(
        "--sigma-ppm",
        type=float,
        default=None,
        help="v2: fixed mass-error width when --no-fit-sigma (else fitted)",
    )
    ap.add_argument(
        "--match-k",
        type=float,
        default=6.0,
        help="v2: match window in units of sigma (scales with resolution)",
    )
    ap.add_argument(
        "--intensity",
        choices=["snr", "linear"],
        default="snr",
        help="v2: intensity term — snr-normalised (default) or linear",
    )
    ap.add_argument(
        "--dump-calibration",
        action="store_true",
        help="print the full-set Platt (a,b) for DEFAULT_CALIBRATION_V2",
    )
    ap.add_argument("--baseline", help="JSON to compare against (regression gate)")
    ap.add_argument("--out", help="write metrics JSON here")
    a = ap.parse_args()

    cand_path = f"{a.bundle_dir}/expected/candidates.parquet"
    filenames = (
        pd.read_parquet(cand_path, columns=["filename"])["filename"].unique()
        if __import__("os").path.exists(cand_path)
        else []
    )

    # Observed peaks per file: prefer the FULL spectrum + real SNR from the
    # filestore's peak_timeseries.zarr (satellites dropped); fall back to the
    # matched-target peaks in expected/peaks.parquet (proxy, noise from heights).
    matched = pd.read_parquet(f"{a.bundle_dir}/expected/peaks.parquet")
    matched = matched.assign(mz=matched["mz"].astype(float))
    obs = {}
    n_fs = 0
    for f in filenames:
        df = load_filestore_peaks(a.bundle_dir, f)
        if df is not None:
            df = df[~df["is_satellite"]]
            obs[f] = (
                df["mz"].to_numpy(float),
                df["height"].to_numpy(float),
                df["snr"].to_numpy(float),
                0.0,
            )
            n_fs += 1
        else:
            g = matched[matched["filename"] == f].sort_values("mz")
            h = g["height"].to_numpy(float)
            noise = float(np.percentile(h, 2)) if len(h) else 1.0
            obs[f] = (g["mz"].to_numpy(float), h, None, noise)
    src = (
        "filestore peak_timeseries.zarr (real SNR, full spectrum)"
        if n_fs
        else "matched-target proxy (no SNR)"
    )
    print(f"scorer={a.scorer}  peaks: {n_fs}/{len(filenames)} files from {src}")

    # Per-sample fitted mass accuracy (mu, sigma) for the resolution-correct mass term.
    cal = {}
    if a.scorer == "v2" and a.fit_sigma:
        for f in filenames:
            cal[f] = fit_mass_accuracy(a.bundle_dir, f)
        sig = [v[1] for v in cal.values() if v[1] is not None]
        if sig:
            print(
                f"fitted mass accuracy sigma: median {np.median(sig):.2f} ppm "
                f"(range {min(sig):.2f}-{max(sig):.2f}) over {len(sig)} files; "
                f"match window = {a.match_k}*sigma"
            )
    elif a.scorer == "v2":
        fb = a.sigma_ppm if a.sigma_ppm is not None else "FALLBACK"
        print(
            f"v2 mass term: FIXED sigma_ppm={fb}, match window={a.ppm} ppm (--no-fit-sigma)"
        )

    try:
        cands = pd.read_parquet(cand_path)
    except Exception:
        print(f"!! {cand_path} not found — run make_candidates.py first.")
        return 2

    # per-file v2 mass-term params: (match window, scoring sigma, mu). Fitted +
    # prediction-spread quadrature when available, else the fixed/fallback sigma.
    def _v2_params(filename):
        mu, sigma, _ = cal.get(filename, (0.0, None, 0))
        if a.fit_sigma and sigma is not None:
            sigma = float(np.hypot(sigma, PRED_SIGMA_PPM))
            return max(a.match_k * sigma, 1.0), sigma, mu
        return a.ppm, a.sigma_ppm, 0.0

    # score every candidate
    def score_all() -> np.ndarray:
        out = np.full(len(cands), np.nan)
        for idx, r in enumerate(cands.itertuples()):
            rec = obs.get(r.filename)
            if rec is None:
                continue
            mzs, ints, snrs, noise = rec
            if a.scorer == "v2":
                window, sigma, mu = _v2_params(r.filename)
                s = score_ion_v2(
                    mzs,
                    ints,
                    snrs,
                    r.candidate_ion,
                    ppm=window,
                    noise=noise,
                    k_detect=a.k_detect,
                    miss_penalty=a.miss_penalty,
                    sigma_ppm=sigma,
                    mu=mu,
                    snr_intensity=(a.intensity == "snr"),
                )
            else:
                s = score_ion(mzs, ints, r.candidate_ion, ppm=a.ppm)
            if s is not None:
                out[idx] = s
        return out

    s1 = score_all()
    s2 = score_all()
    reproducible = bool(
        np.array_equal(np.nan_to_num(s1, nan=-1), np.nan_to_num(s2, nan=-1))
    )
    cands = cands.assign(score=s1)

    # ---- ranking: per anchor, is the true candidate the argmax? ----
    # `_plausible` variants restrict decoys to the chemically plausible ones (the
    # score-only test: rejecting implausible mass-degenerate formulas is chemistry's
    # job, so the score should beat the PLAUSIBLE alternatives).
    has_plaus = "is_plausible" in cands.columns
    top1, top1_contested = [], []
    top1_contested_plausible, n_contested, n_contested_plaus = [], 0, 0
    for (_, _), g in cands.groupby(["filename", "anchor_mz"]):
        g = g.dropna(subset=["score"])
        if g.empty or not g["is_true"].any():
            continue
        win_true = bool(g.loc[g["score"].idxmax(), "is_true"])
        top1.append(win_true)
        if len(g) > 1:  # contested = has at least one decoy
            n_contested += 1
            top1_contested.append(win_true)
        if has_plaus:
            gp = g[g["is_plausible"].fillna(False) | g["is_true"]]
            if gp["is_true"].any() and len(gp) > 1:
                n_contested_plaus += 1
                top1_contested_plausible.append(
                    bool(gp.loc[gp["score"].idxmax(), "is_true"])
                )

    auc = _roc_auc(
        cands["is_true"].to_numpy(int),
        np.nan_to_num(cands["score"].to_numpy(), nan=0.0),
    )

    # ---- calibration over the candidate pool (raw, in-sample) ----
    valid = cands.dropna(subset=["score"]).copy()
    sc = valid["score"].to_numpy()
    yy = valid["is_true"].to_numpy(int)
    ece = _ece(sc, yy)
    if a.dump_calibration:
        a_pl, b_pl = _platt_fit(sc, yy)
        print(f"DEFAULT_CALIBRATION (full-set Platt fit): ({a_pl:.4f}, {b_pl:.4f})")

    # ---- calibration LAYER: fit P(correct)=sigmoid(a*score+b), honest train/test
    # split by file so a calibrated score is a probability. ECE_cal is out-of-sample.
    rng = np.random.default_rng(0)
    files = valid["filename"].to_numpy()
    uniq = np.unique(files)
    test_files = set(rng.choice(uniq, size=max(1, len(uniq) // 2), replace=False))
    te = np.array([f in test_files for f in files])
    ece_cal = None
    if te.any() and (~te).any() and yy[~te].any() and (yy[~te] == 0).any():
        a_pl, b_pl = _platt_fit(sc[~te], yy[~te])
        ece_cal = _ece(_sigmoid(a_pl * sc[te] + b_pl), yy[te])

    # ---- baseline score distribution of the TRUE assignments ----
    true_scores = valid.loc[valid["is_true"], "score"].to_numpy()

    # ---- robustness: how much does a TRUE assignment's score wobble when its peak
    # intensities are resampled within their noise? (v2 only; small = robust) ----
    robustness = None
    if a.scorer == "v2":
        tr = valid[valid["is_true"]]
        tr = tr.sample(min(len(tr), 800), random_state=0) if len(tr) else tr
        rrng = np.random.default_rng(0)
        stds = []
        for r in tr.itertuples():
            rec = obs.get(r.filename)
            if rec is None:
                continue
            mzs, ints, snrs, noise = rec
            window, sigma, mu = _v2_params(r.filename)
            sd = score_v2_resample_std(
                mzs,
                ints,
                snrs,
                r.candidate_ion,
                ppm=window,
                mu=mu,
                noise=noise,
                sigma_ppm=sigma,
                k_detect=a.k_detect,
                miss_penalty=a.miss_penalty,
                rng=rrng,
            )
            if sd is not None:
                stds.append(sd)
        if stds:
            robustness = round(float(np.median(stds)), 4)

    metrics = {
        "n_anchors": int(len(top1)),
        "n_contested": int(n_contested),
        "reproducible": reproducible,
        "rank_top1_all": round(float(np.mean(top1)), 4) if top1 else None,
        "rank_top1_contested": round(float(np.mean(top1_contested)), 4)
        if top1_contested
        else None,
        "rank_top1_contested_plausible": (
            round(float(np.mean(top1_contested_plausible)), 4)
            if top1_contested_plausible
            else None
        ),
        "n_contested_plausible": int(n_contested_plaus),
        "roc_auc": round(auc, 4),
        "ece": round(ece, 4),
        "ece_calibrated": round(float(ece_cal), 4) if ece_cal is not None else None,
        "robustness_score_std": robustness,
        "true_score_median": round(float(np.median(true_scores)), 4)
        if len(true_scores)
        else None,
        "true_score_p10": round(float(np.percentile(true_scores, 10)), 4)
        if len(true_scores)
        else None,
    }

    print("=== match-score evaluation (mascope_tools baseline) ===")
    for k, v in metrics.items():
        print(f"  {k:<22} {v}")

    if a.out:
        json.dump(metrics, open(a.out, "w"), indent=2)
        print(f"\nwrote {a.out}")

    if a.baseline:
        base = json.load(open(a.baseline))
        regressed = []
        for key in ("rank_top1_contested", "roc_auc"):
            b, n = base.get(key), metrics.get(key)
            if b is not None and n is not None and n < b - 1e-9:
                regressed.append(f"{key}: {b} -> {n}")
        if not metrics["reproducible"]:
            regressed.append("reproducibility: scoring is non-deterministic")
        if regressed:
            print("\nREGRESSION vs baseline:\n  " + "\n  ".join(regressed))
            return 1
        print("\nno regression vs baseline.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
