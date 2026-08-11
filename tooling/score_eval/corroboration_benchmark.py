"""P3 measurement: how much does adduct co-occurrence discriminate a true detection?

The clean-labelled decoy set (candidates.parquet) predates the adduct-rich library and has
almost no true multi-adduct chemistry, so it can't weigh adduct corroboration
(corroboration_eval.py). The live data has rich co-occurrence but no independent truth. This
script measures the corroboration signal directly with an *offset decoy*, the honest null for a
co-occurrence claim:

    Take a real detection -- a true neutral whose PRIMARY adduct ([M+H]+ / [M-H]-) envelope fits a
    real peak at m/z p. The corroboration claim is that the compound's OTHER adducts appear at
    p + (adduct mass difference). Null: at p + a RANDOM offset of similar magnitude there should be
    no systematic peak.

      real slot  = is there a peak at the compound's true adduct m/z?      (co-occurrence)
      decoy slot = is there a peak at a random offset of similar size?      (chance, spectral density)

Both slots use the identical "peak present within tolerance & SNR>=k" test, so the comparison is
apples-to-apples; the only difference is whether the offset is a real adduct relationship. Then:

    P(present | real adduct) vs P(present | random)      the per-adduct likelihood ratio
    P(present) as a function of #real adducts already seen (is co-occurrence self-consistent?)
    logit(is_real_slot) ~ intercept                      the odds a matched adduct is genuine

The per-adduct LR is exactly the Bayesian odds-update weight to fold into p_correct: each
independently corroborating adduct multiplies the odds of a correct formula by ~LR. Runs against
the v1 bundle's real spectra; the v1 seed's poor adduct library is irrelevant -- we apply the full
adduct panel and offsets ourselves.

    python corroboration_benchmark.py <bundle_dir> [--samples N] [--snr 3] [--per-neutral M]
"""

from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd
import score_eval as se

from mascope_tools.composition import utils


# Adduct channels ACTUALLY operative in the demo -- the mechanisms that appear in the
# peak_assignment table (verified against the ionization_mechanism library), not the guessed
# panel in make_candidates. Only 5 mechanisms are ever assigned: +H+, +NH4+, +(CH4N2O)H+ (pos)
# and -H+, +Br- (neg). Defined-but-unused here: +Br2-, +Br3-, +NO3-, +CO3-, bare +/-. The
# deprotonation mass is taken via "-H-" (correct [M-H]- mass; the library id is "-H+").
CHANNELS = {
    "neg": {"primary": "-H-", "adducts": ["-H-", "+Br-"]},
    "pos": {"primary": "+H+", "adducts": ["+H+", "+NH4+", "+(CH4N2O)H+"]},
}
# Random decoy offsets are drawn in this |Da| range (comparable to real adduct differences,
# ~17-80 Da) and rejected if within TOL_DA of any real adduct offset or a 13C/12C multiple.
OFFSET_RANGE = (10.0, 90.0)
ISOTOPE_STEP = 1.003355


def _polarity(filename: str) -> str | None:
    if "_neg_" in filename:
        return "neg"
    if "_pos_" in filename:
        return "pos"
    return None


def neutral_of(ion: str, ionization: str) -> str:
    body = ion[:-1] if ion and ion[-1] in "+-" else ion
    try:
        ic = utils.parse_composition(body)
        im = utils.parse_ionization(ionization)
        add = (
            utils.parse_composition(im.formula)
            if im.formula
            else utils.parse_composition("")
        )
        return utils.to_hill_order(ic - add if im.addition else ic + add)
    except Exception:
        return body


def ion_mono_mz(neutral: str, mech: str) -> float | None:
    """Monoisotopic ion m/z for neutral + adduct, or None if it can't be built."""
    from mascope_tools.composition.heuristic_filter import predict_isotopes

    try:
        ion = utils.combine_formula_and_ionization(
            neutral, utils.parse_ionization(mech)
        )
    except Exception:
        return None
    if not ion or ion[-1] not in "+-":
        return None
    charge = 1 if ion[-1] == "+" else -1
    try:
        pred_mz, _, _ = predict_isotopes(ion[:-1], charge, None)
    except Exception:
        return None
    return float(pred_mz[0]) if len(pred_mz) else None


def peak_present(mzs, snrs, target, *, tol_da, snr_min) -> bool:
    """Is there a non-satellite peak within tol of target with SNR >= snr_min?"""
    lo = np.searchsorted(mzs, target - tol_da, "left")
    hi = np.searchsorted(mzs, target + tol_da, "right")
    if hi <= lo:
        return False
    return bool(np.max(snrs[lo:hi]) >= snr_min)


def score_primary(
    mzs, ints, snrs, neutral, mech, *, ppm, sigma_ppm, mu
) -> float | None:
    """Envelope fit of the primary adduct (defines a real detection)."""
    ion = None
    try:
        ion = utils.combine_formula_and_ionization(
            neutral, utils.parse_ionization(mech)
        )
    except Exception:
        return None
    if not ion:
        return None
    return se.score_ion_v2(mzs, ints, snrs, ion, ppm=ppm, sigma_ppm=sigma_ppm, mu=mu)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("bundle_dir")
    ap.add_argument("--samples", type=int, default=0, help="cap files (0 = all)")
    ap.add_argument(
        "--tau-primary",
        type=float,
        default=0.5,
        help="primary-adduct fit >= this = a real detection (the analysis anchor)",
    )
    ap.add_argument(
        "--snr", type=float, default=3.0, help="min SNR for a slot to count as present"
    )
    ap.add_argument(
        "--per-neutral", type=int, default=0, help="cap true neutrals per file (smoke)"
    )
    ap.add_argument(
        "--decoys-per-adduct",
        type=int,
        default=3,
        help="random offset draws per real adduct slot",
    )
    a = ap.parse_args()
    rng = np.random.default_rng(0)

    cands = pd.read_parquet(f"{a.bundle_dir}/expected/candidates.parquet")
    true = cands[cands["is_true"]].copy()
    true["neutral"] = [
        neutral_of(i, z) for i, z in zip(true["candidate_ion"], true["ionization"])
    ]
    by_file = {f: sorted(set(g["neutral"])) for f, g in true.groupby("filename")}

    files = list(by_file)
    if a.samples:
        pos = [f for f in files if _polarity(f) == "pos"][: a.samples // 2]
        neg = [f for f in files if _polarity(f) == "neg"][: a.samples - len(pos)]
        files = pos + neg

    slots = []  # per adduct-slot: null_type (real|random|swap), present (bool)
    per_cmpd = []  # per detection: n_real_present, n_decoy_present
    for fi, f in enumerate(files, 1):
        pol = _polarity(f)
        if pol is None:
            continue
        df = se.load_filestore_peaks(a.bundle_dir, f)
        if df is None:
            continue
        df = df[~df["is_satellite"]]
        mzs = df["mz"].to_numpy(float)
        ints = df["height"].to_numpy(float)
        snrs = df["snr"].to_numpy(float)
        order = np.argsort(mzs)
        mzs, ints, snrs = mzs[order], ints[order], snrs[order]
        mu, sigma, _ = se.fit_mass_accuracy(a.bundle_dir, f)
        if sigma is not None:
            sigma = float(np.hypot(sigma, se.PRED_SIGMA_PPM))
            window = max(6.0 * sigma, 1.0)
        else:
            window, sigma, mu = se.MZ_PPM, None, 0.0
        spec = CHANNELS[pol]
        primary = spec["primary"]
        others = [m for m in spec["adducts"] if m != primary]
        neutrals = by_file[f][: a.per_neutral] if a.per_neutral else by_file[f]
        # pool of real peaks to re-anchor the SAME adduct offsets on (the harder null)
        anchor_pool = mzs[snrs >= a.snr]
        for neutral in neutrals:
            p = ion_mono_mz(neutral, primary)
            if p is None:
                continue
            # require a real primary detection (envelope fit) as the anchor
            fit = score_primary(
                mzs, ints, snrs, neutral, primary, ppm=window, sigma_ppm=sigma, mu=mu
            )
            if fit is None or fit < a.tau_primary:
                continue
            tol = p * window * 1e-6
            real_offsets = []  # (mech, offset)
            n_real = 0
            for m in others:
                q = ion_mono_mz(neutral, m)
                if q is None:
                    continue
                off = q - p
                real_offsets.append((m, off))
                present = peak_present(
                    mzs, snrs, q, tol_da=max(tol, q * window * 1e-6), snr_min=a.snr
                )
                slots.append(
                    {
                        "null_type": "real",
                        "adduct": m,
                        "present": present,
                        "polarity": pol,
                    }
                )
                n_real += int(present)
            # null 1 -- RANDOM offset: same count, similar magnitude, not a real offset/isotope
            n_dec = 0
            n_dec_slots = 0
            for _ in real_offsets:
                for _ in range(a.decoys_per_adduct):
                    for _try in range(20):
                        off = rng.uniform(*OFFSET_RANGE)
                        if any(abs(off - ro) < 0.2 for _, ro in real_offsets):
                            continue
                        if (
                            abs((off % ISOTOPE_STEP)) < 0.1
                            or abs((off % ISOTOPE_STEP) - ISOTOPE_STEP) < 0.1
                        ):
                            continue
                        break
                    q = p + off
                    if q <= mzs[0] or q >= mzs[-1]:
                        continue
                    present = peak_present(
                        mzs, snrs, q, tol_da=q * window * 1e-6, snr_min=a.snr
                    )
                    slots.append(
                        {
                            "null_type": "random",
                            "adduct": None,
                            "present": present,
                            "polarity": pol,
                        }
                    )
                    n_dec += int(present)
                    n_dec_slots += 1
            # null 2 -- ANCHOR SWAP (harder): the SAME real adduct offset, re-anchored on a
            # random real peak. Controls for adduct-like offsets landing on structured spacings.
            if len(anchor_pool) > 5:
                for m, off in real_offsets:
                    for _ in range(a.decoys_per_adduct):
                        pp = float(rng.choice(anchor_pool))
                        if abs(pp - p) < 1.0:
                            continue
                        q = pp + off
                        if q <= mzs[0] or q >= mzs[-1]:
                            continue
                        present = peak_present(
                            mzs, snrs, q, tol_da=q * window * 1e-6, snr_min=a.snr
                        )
                        slots.append(
                            {
                                "null_type": "swap",
                                "adduct": m,
                                "present": present,
                                "polarity": pol,
                            }
                        )
            per_cmpd.append(
                {
                    "filename": f,
                    "neutral": neutral,
                    "n_others": len(real_offsets),
                    "n_real_present": n_real,
                    "decoy_rate": (n_dec / n_dec_slots) if n_dec_slots else np.nan,
                }
            )
        if fi % 5 == 0:
            print(f"  {fi}/{len(files)} files  ({len(slots)} slots)", file=sys.stderr)

    S = pd.DataFrame(slots)
    C = pd.DataFrame(per_cmpd)

    def odds(p):
        return p / (1 - p) if 0 < p < 1 else float("inf")

    def rate(mask):
        sub = S[mask]["present"]
        return sub.mean(), len(sub)

    real_p, n_real = rate(S["null_type"] == "real")
    rand_p, n_rand = rate(S["null_type"] == "random")
    swap_p, n_swap = rate(S["null_type"] == "swap")
    print(f"\ndetections (anchors) = {len(C)}  files = {C['filename'].nunique()}")

    print("\n=== per-adduct-slot presence (P a peak sits at the adduct's m/z) ===")
    print(f"  REAL adduct offset      P={real_p:.4f}  (n={n_real})")
    print(f"  RANDOM offset (null 1)  P={rand_p:.4f}  (n={n_rand})")
    print(f"  ANCHOR-SWAP (null 2)    P={swap_p:.4f}  (n={n_swap})")
    if 0 < rand_p < 1:
        print(
            f"  --> LR vs random  = {odds(real_p) / odds(rand_p):.1f}x per corroborating adduct"
        )
    if 0 < swap_p < 1:
        print(
            f"  --> LR vs swap    = {odds(real_p) / odds(swap_p):.1f}x  (the defensible, harder null)"
        )

    print("\n=== by polarity (LR vs anchor-swap null) ===")
    for pol, g in S.groupby("polarity"):
        rp = g[g["null_type"] == "real"]["present"].mean()
        sp = g[g["null_type"] == "swap"]["present"].mean()
        lr = odds(rp) / odds(sp) if 0 < sp < 1 and 0 < rp < 1 else float("nan")
        print(
            f"  {pol}: real={rp:.3f}  swap={sp:.3f}  LR={lr:.1f}x  "
            f"(n_real={int((g['null_type'] == 'real').sum())})"
        )

    print(
        "\n=== per-adduct LR (real vs anchor-swap null) -- the weight is adduct-specific ==="
    )
    for m in [
        x for pol in CHANNELS.values() for x in pol["adducts"] if x != pol["primary"]
    ]:
        gm = S[S["adduct"] == m]
        rp = gm[gm["null_type"] == "real"]["present"]
        sp = gm[gm["null_type"] == "swap"]["present"]
        if len(rp) < 20:
            continue
        lr = (
            odds(rp.mean()) / odds(sp.mean())
            if 0 < sp.mean() < 1 and 0 < rp.mean() < 1
            else float("nan")
        )
        print(
            f"  {m:14s} real={rp.mean():.3f}  swap={sp.mean():.3f}  "
            f"LR={lr:.2f}x  log-odds={np.log(lr):+.2f}  (n={len(rp)})"
        )

    # co-occurrence distribution among real detections
    print("\n=== #real adducts present per detection ===")
    print(C["n_real_present"].value_counts().sort_index().to_string())
    print(
        f"  mean real adducts co-present = {C['n_real_present'].mean():.3f} "
        f"of up to {C['n_others'].max()}"
    )
    print(f"  mean random-offset presence rate = {C['decoy_rate'].mean():.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
