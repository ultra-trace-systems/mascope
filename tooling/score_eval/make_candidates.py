"""#1 — Scoring-quality extension for the demo golden dataset.

The demo bundle (`mascope-demo-dataset-v1`) records, per detected peak, the TRUE
assigned formula (the seeded target) and the current match score. That anchors
*reproducibility* but not scoring *quality*: to test whether a scorer RANKS the
true formula above plausible wrong ones, and whether its score is CALIBRATED, we
need, per peak, a candidate pool = {true formula} ∪ {near-mass decoys}.

This script generates that pool. For each M0 peak in `expected/peaks.parquet` it
enumerates, via `mascope_tools.find_compositions`, the formulas whose ion lands
within a small m/z window for the polarity's ionization channels, forms the ion
formula, dedupes, and flags `is_true` against the bundle's assigned ion. Output:
`expected/candidates.parquet` (filename, anchor_mz, true_ion, candidate_ion,
ionization, is_true) — the labels the ranking + calibration metrics consume.

Decoys are the assignability test: they are the mass-degenerate alternatives a good
score must rank below the truth.

    python make_candidates.py <bundle_dir> [--ppm 3] [--max-per-channel 25] [--limit N]
"""

from __future__ import annotations

import argparse
import sys

import pandas as pd

from mascope_tools.composition import CompositionSearchConfig, utils
from mascope_tools.composition.finder import find_compositions
from mascope_tools.composition.heuristic_filter import apply_heuristic_rules


# Ionization channels + grid element ranges per polarity. '-H-' is deprotonation as a
# charge-−1 anion (mascope_tools notation), '+Br-' bromide adduct, etc.
# NB (2026-07): these were NOT verified against the real ionization_mechanism library. The
# mechanisms actually assigned in the demo are +H+/+NH4+/+(CH4N2O)H+ (pos) and -H+/+Br- (neg);
# '+HBrBr-' is not a real mechanism (~1 Da off the real '+Br2-') and '+CO3-' is defined but never
# assigned. They only widen the DECOY pool here so are harmless to fit-ranking, but do not treat
# this list as the reagent profile -- see corroboration_benchmark.py for the operative panel.
CHANNELS = {
    "neg": {
        "ionizations": ["-H-", "+Br-", "+HBrBr-", "+CO3-"],
        "ranges": "C0-40 H0-80 N0-3 O0-18 S0-2 Cl0-2 Br0-2",
    },
    "pos": {
        "ionizations": ["+H+", "+NH4+", "+(CH4N2O)H+"],
        "ranges": "C0-40 H0-90 N0-8 O0-15 S0-2",
    },
}


def _polarity(filename: str) -> str | None:
    if "_neg_" in filename:
        return "neg"
    if "_pos_" in filename:
        return "pos"
    return None


def _norm_ion(formula: str) -> str:
    """Normalise an ion formula string for stable equality (Hill order, keep the
    trailing charge sign)."""
    f = str(formula).strip()
    sign = f[-1] if f and f[-1] in "+-" else ""
    body = f[:-1] if sign else f
    try:
        return utils.to_hill_order(body) + sign
    except Exception:
        return f


def candidates_for_peak(mz: float, polarity: str, *, ppm: float, cap: int) -> dict:
    """Enumerate candidate ion formulas near `mz` across the polarity's channels.
    Returns {normalised_ion_formula: (ionization, is_plausible)} where is_plausible
    is whether the neutral passes mascope_tools' structural gates (Senior/valence/
    element-ratio) — so a score-only ranking can compete the truth against the
    chemically plausible decoys, not the implausible-but-mass-degenerate ones."""
    spec = CHANNELS[polarity]
    out: dict[str, tuple] = {}
    for mech in spec["ionizations"]:
        try:
            im = utils.parse_ionization(mech)
            cfg = CompositionSearchConfig(
                ionizations=mech,
                element_count_ranges=spec["ranges"],
                mass_range_ppm=ppm,
                max_result_rows=cap,
            )
            results = find_compositions(mz, cfg)
            try:
                plaus, _ = apply_heuristic_rules(results)
                plausible_neutrals = {d.get("formula") for d in plaus}
            except Exception:
                plausible_neutrals = {r.get("formula") for r in results}  # fail open
            for r in results:
                neutral = r.get("formula")
                if not neutral or neutral == "---":
                    continue
                ion = utils.combine_formula_and_ionization(neutral, im)
                key = _norm_ion(ion)
                is_plaus = neutral in plausible_neutrals
                prev = out.get(key)
                out[key] = (
                    (mech, is_plaus) if prev is None else (prev[0], prev[1] or is_plaus)
                )
        except Exception:
            continue
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("bundle_dir")
    ap.add_argument("--ppm", type=float, default=3.0, help="enumeration m/z window")
    ap.add_argument("--max-per-channel", type=int, default=25)
    ap.add_argument(
        "--limit", type=int, default=0, help="cap peaks (0 = all; for smoke)"
    )
    a = ap.parse_args()

    peaks = pd.read_parquet(f"{a.bundle_dir}/expected/peaks.parquet")
    # M0 anchors only: the assigned ion's monoisotopic row (no [isotope] label).
    m0 = peaks[
        ~peaks["target_isotope_formula"].astype(str).str.contains(r"\[", regex=True)
    ]
    m0 = m0.drop_duplicates(["filename", "target_isotope_formula"]).reset_index(
        drop=True
    )
    if a.limit:
        m0 = m0.head(a.limit)
    print(f"M0 anchors: {len(m0)} (of {len(peaks)} isotopologue rows)")

    rows = []
    n_true_found = 0
    for n, r in enumerate(m0.itertuples(), 1):
        pol = _polarity(r.filename)
        if pol is None:
            continue
        true_ion = _norm_ion(r.target_isotope_formula)
        pool = candidates_for_peak(float(r.mz), pol, ppm=a.ppm, cap=a.max_per_channel)
        # Guarantee the truth is in the pool even if enumeration narrowly missed it
        # (the true assignment is plausible by definition).
        if true_ion not in pool:
            pool[true_ion] = ("true", True)
        else:
            n_true_found += 1
        for ion, (mech, plaus) in pool.items():
            is_true = ion == true_ion
            rows.append(
                {
                    "filename": r.filename,
                    "anchor_mz": float(r.mz),
                    "true_ion": true_ion,
                    "candidate_ion": ion,
                    "ionization": mech,
                    "is_true": is_true,
                    "is_plausible": bool(plaus or is_true),
                }
            )
        if n % 500 == 0:
            print(f"  {n}/{len(m0)} anchors  ({len(rows)} candidates)")

    out = pd.DataFrame(rows)
    dst = f"{a.bundle_dir}/expected/candidates.parquet"
    out.to_parquet(dst, index=False)
    per = (len(out) / len(m0)) if len(m0) else 0
    print(
        f"\nwrote {dst}\n"
        f"  {len(out)} candidate rows over {len(m0)} anchors ({per:.1f} cand/anchor)\n"
        f"  true ion recovered by enumeration: {n_true_found}/{len(m0)} "
        f"({n_true_found / max(1, len(m0)):.1%})  (rest force-added as is_true)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
