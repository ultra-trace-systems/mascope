"""P3 measurement (scratch): how much does adduct co-occurrence corroborate a correct formula?

The proposal (see docs/dev/assignment_confidence.md P3): don't hand-pick a corroboration
weight -- measure the empirical lift on the golden/decoy set, then fold it into p_correct as a
Bayesian odds update.

This script reuses the score_eval v2 fit scorer + arbitration evidence (fit x plausibility) to
pick a winner per anchor, then -- exactly as the runtime `adduct_corroboration` would -- counts,
per winner's NEUTRAL formula, how many distinct adducts of that formula also *won* an anchor in the
same file. It then measures, among winners:

    P(correct | n_adducts)          the correct-rate by co-occurrence bucket
    odds ratio / likelihood ratio   the multiplier to fold into the p_correct odds

and checks whether a 2-feature logistic (evidence + corroboration) beats the evidence-only Platt
curve on held-out discrimination/calibration.

    python corroboration_eval.py <bundle_dir> [--limit N] [--accept-fdr 0.05]

Scored evidence is cached to <bundle_dir>/expected/_scored_evidence.parquet so the corroboration
analysis can be re-run instantly.
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd
import score_eval as se

from mascope_tools.composition.arbitration import threshold_at_fdr
from mascope_tools.composition.corroboration import _corroboration_score
from mascope_tools.composition.heuristic_filter import formula_plausibility
from mascope_tools.composition.utils import (
    parse_composition,
    parse_ionization,
    to_hill_order,
)


def neutral_of(ion: str, ionization: str) -> str:
    """Reconstruct the NEUTRAL formula from an ion + its ionization notation (the inverse of
    combine_formula_and_ionization). Falls back to the charge-stripped ion when the notation
    can't be parsed."""
    body = ion[:-1] if ion and ion[-1] in "+-" else ion
    try:
        ion_comp = parse_composition(body)
        im = parse_ionization(ionization)
        add = parse_composition(im.formula) if im.formula else parse_composition("")
        neutral = ion_comp - add if im.addition else ion_comp + add
        return to_hill_order(neutral)
    except Exception:
        return body


def score_candidates(bundle_dir: str, limit: int) -> pd.DataFrame:
    """Score every candidate's fit x plausibility (cached)."""
    cache = f"{bundle_dir}/expected/_scored_evidence.parquet"
    cands = pd.read_parquet(f"{bundle_dir}/expected/candidates.parquet")
    if limit:
        keep = cands.drop_duplicates(["filename", "anchor_mz"]).head(limit)
        cands = cands.merge(
            keep[["filename", "anchor_mz"]], on=["filename", "anchor_mz"]
        )
    if not limit and os.path.exists(cache):
        cached = pd.read_parquet(cache)
        if len(cached) == len(cands):
            print(f"using cached scores ({len(cached)} rows)", file=sys.stderr)
            return cached

    filenames = cands["filename"].unique()
    obs = {}
    for f in filenames:
        df = se.load_filestore_peaks(bundle_dir, f)
        if df is not None:
            df = df[~df["is_satellite"]]
            obs[f] = (
                df["mz"].to_numpy(float),
                df["height"].to_numpy(float),
                df["snr"].to_numpy(float),
            )
        else:
            obs[f] = None
    cal = {f: se.fit_mass_accuracy(bundle_dir, f) for f in filenames}

    def params(f):
        mu, sigma, _ = cal.get(f, (0.0, None, 0))
        if sigma is not None:
            sigma = float(np.hypot(sigma, se.PRED_SIGMA_PPM))
            return max(6.0 * sigma, 1.0), sigma, mu
        return se.MZ_PPM, None, 0.0

    fit = np.full(len(cands), np.nan)
    plaus = np.ones(len(cands))
    neutral = [""] * len(cands)
    for idx, r in enumerate(cands.itertuples()):
        rec = obs.get(r.filename)
        neutral[idx] = neutral_of(r.candidate_ion, r.ionization)
        if rec is None:
            continue
        mzs, ints, snrs = rec
        window, sigma, mu = params(r.filename)
        s = se.score_ion_v2(
            mzs, ints, snrs, r.candidate_ion, ppm=window, sigma_ppm=sigma, mu=mu
        )
        if s is not None:
            fit[idx] = s
        plaus[idx] = formula_plausibility(neutral[idx])
        if idx % 10000 == 0:
            print(f"  scored {idx}/{len(cands)}", file=sys.stderr)

    cands = cands.assign(
        fit=fit, plaus=plaus, neutral=neutral, evidence=np.nan_to_num(fit) * plaus
    )
    if not limit:
        cands.to_parquet(cache)
    return cands


def pick_winners(cands: pd.DataFrame) -> pd.DataFrame:
    """Top-1 by evidence per anchor, over anchors that have a scored true candidate."""
    rows = []
    for (fn, mz), g in cands.groupby(["filename", "anchor_mz"]):
        gg = g.dropna(subset=["fit"])
        if gg.empty or not gg["is_true"].any():
            continue
        wi = gg["evidence"].idxmax()
        tot = gg["evidence"].sum()
        w = gg.loc[wi]
        rows.append(
            {
                "filename": fn,
                "anchor_mz": mz,
                "neutral": w["neutral"],
                "ionization": w["ionization"],
                "evidence": float(w["evidence"]),
                "conf": float(w["evidence"] / tot) if tot > 0 else 0.0,
                "is_true": bool(w["is_true"]),
            }
        )
    return pd.DataFrame(rows)


def add_corroboration(winners: pd.DataFrame, accept_mask=None) -> pd.DataFrame:
    """n_adducts = distinct adducts under which a winner's neutral formula won in its file.

    Mirrors runtime adduct_corroboration: competitor-blind, counts distinct adduct channels
    of the same compound among accepted winners. `accept_mask` (bool Series) restricts which
    winners count toward the co-occurrence tally (e.g. FDR-accepted only)."""
    pool = winners[accept_mask] if accept_mask is not None else winners
    counts = (
        pool.groupby(["filename", "neutral"])["ionization"]
        .nunique()
        .rename("n_adducts")
    )
    out = winners.merge(counts, on=["filename", "neutral"], how="left")
    out["n_adducts"] = out["n_adducts"].fillna(0).astype(int).clip(lower=1)
    out["corroboration"] = out["n_adducts"].map(_corroboration_score)
    return out


def bucket_report(winners: pd.DataFrame, label: str):
    print(
        f"\n=== correct-rate by adduct co-occurrence [{label}] (n winners={len(winners)}) ==="
    )
    base = winners["is_true"].mean()
    print(f"  overall P(correct) = {base:.4f}")

    def odds(p):
        return p / (1 - p) if 0 < p < 1 else float("inf") if p >= 1 else 0.0

    o1 = None
    for n, g in winners.groupby("n_adducts"):
        p = g["is_true"].mean()
        if n == 1:
            o1 = odds(p)
        print(
            f"  n_adducts={n:>2}  P(correct)={p:.4f}  n={len(g):>5}  "
            f"odds={odds(p):.3f}"
            + (f"  odds-ratio vs n=1: {odds(p) / o1:.2f}x" if o1 else "")
        )
    # collapsed 1 vs >=2
    single = winners[winners["n_adducts"] == 1]["is_true"]
    multi = winners[winners["n_adducts"] >= 2]["is_true"]
    if len(single) and len(multi):
        os_, om = odds(single.mean()), odds(multi.mean())
        print(
            f"  --> single-adduct P={single.mean():.4f} (n={len(single)}) | "
            f"multi-adduct P={multi.mean():.4f} (n={len(multi)})"
        )
        if os_ > 0 and om > 0:
            print(
                f"  --> odds ratio (multi / single) = {om / os_:.2f}x  "
                f"(log-odds shift {np.log(om / os_):+.3f})"
            )
        else:
            print(
                "  --> odds ratio undefined (a bucket has 0% or 100% correct -- "
                "too few multi-adduct cases to estimate; see note below)"
            )
        if len(multi) < 30:
            print(
                f"  !! only {len(multi)} multi-adduct winners -- underpowered: this "
                "labelled set lacks multi-adduct chemistry, so no reliable LR."
            )


def logistic_compare(winners: pd.DataFrame):
    """Does adding corroboration as a feature beat evidence-only? Held-out ECE + AUC."""
    try:
        from sklearn.linear_model import LogisticRegression
        from sklearn.metrics import roc_auc_score
        from sklearn.model_selection import train_test_split
    except ImportError:
        print("\n(sklearn unavailable -- skipping logistic comparison)")
        return
    w = winners.dropna(subset=["evidence"]).copy()
    y = w["is_true"].to_numpy(int)
    ev = w["evidence"].to_numpy(float)
    cor = w["corroboration"].to_numpy(float)
    Xe = ev.reshape(-1, 1)
    Xec = np.column_stack([ev, cor])
    Xtr_e, Xte_e, Xtr_ec, Xte_ec, ytr, yte = train_test_split(
        Xe, Xec, y, test_size=0.3, random_state=0, stratify=y
    )

    def ece(p, yt, bins=10):
        e = 0.0
        for b in range(bins):
            lo, hi = b / bins, (b + 1) / bins
            m = (p >= lo) & (p < hi if b < bins - 1 else p <= hi)
            if m.sum():
                e += m.mean() * abs(yt[m].mean() - p[m].mean())
        return e

    print("\n=== evidence-only vs evidence+corroboration (30% held out) ===")
    for name, Xtr, Xte in [
        ("evidence", Xtr_e, Xte_e),
        ("evidence+corrob", Xtr_ec, Xte_ec),
    ]:
        m = LogisticRegression(max_iter=1000).fit(Xtr, ytr)
        p = m.predict_proba(Xte)[:, 1]
        coefs = ", ".join(f"{c:+.3f}" for c in m.coef_[0])
        print(
            f"  [{name:16s}] AUC={roc_auc_score(yte, p):.4f}  ECE={ece(p, yte):.4f}  "
            f"coef=({coefs})  intercept={m.intercept_[0]:+.3f}"
        )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("bundle_dir")
    ap.add_argument("--limit", type=int, default=0, help="cap anchors (smoke)")
    ap.add_argument(
        "--accept-fdr",
        type=float,
        default=0.05,
        help="FDR for the accepted-winner co-occurrence pool",
    )
    a = ap.parse_args()

    cands = score_candidates(a.bundle_dir, a.limit)
    winners = pick_winners(cands)
    print(
        f"\nwinners={len(winners)}  files={winners['filename'].nunique()}  "
        f"base P(correct)={winners['is_true'].mean():.4f}"
    )

    # (1) all winners count toward co-occurrence
    all_w = add_corroboration(winners)
    bucket_report(all_w, "all winners")

    # (2) only FDR-accepted winners count (matches runtime accept predicate)
    thr = threshold_at_fdr(
        winners["conf"].tolist(), winners["is_true"].tolist(), a.accept_fdr
    )
    if thr is not None:
        accepted = winners["conf"] >= thr
        acc_w = add_corroboration(winners, accept_mask=accepted)
        bucket_report(acc_w[accepted], f"accepted winners (FDR<={a.accept_fdr})")
        logistic_compare(acc_w[accepted])
    else:
        logistic_compare(all_w)

    return 0


if __name__ == "__main__":
    sys.exit(main())
