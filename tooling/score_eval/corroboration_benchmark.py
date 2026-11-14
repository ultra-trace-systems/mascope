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

Two more layers are measured the same way (step 4.16 of the assignment plan), each against an
anchor-swap null whose anchor is drawn from the spectrum's peaks of about the primary's own height
(``--match-anchors``): a ratio read off a random anchor would otherwise differ from the real one by
the anchor's brightness alone.

    ratio consistency   given the adduct's peak IS present, how far does ln(I_b / I_a) of the pair
                        sit from the same pair's ratio over the file's other detections, in their
                        robust spread (mascope_tools.composition.corroboration.ratio_consistency)?
                        LR per band = P(band | real, present) / P(band | swap, present)
    in-source fragment  is there a peak at the primary ion less a small neutral
                        (corroboration.IN_SOURCE_LOSSES), for a neutral that holds the loss, and
                        is it the shorter (the pair the engine names)?
                        LR per loss = P(present | real) / P(present | swap), and the same of
                        "present and shorter"

Both are true likelihood ratios, a ratio of two probabilities, with a 95% interval from a bootstrap
over files (the slots of one file are not independent). Each is read beside a control that says
what it reads: the ratio bands inside quartiles of the partner's brightness, a loss beside the same
neutral GAINED (a source sheds a neutral and never adds one) and beside the series units CH2 and O.
A factor is marked ``eligible`` for the fold-in only where its LR is above one with the whole
interval above one and its control does not read the same (:func:`measure_step_416`); whether an
eligible factor is folded is the plan owner's call.

    python corroboration_benchmark.py <bundle_dir> [--samples N] [--snr 3] [--per-neutral M]
        [--json out.json] [--bootstrap 1000]
"""

from __future__ import annotations

import argparse
import json
import sys

import numpy as np
import pandas as pd
import score_eval as se
from pyteomics.mass import calculate_mass

from mascope_tools.composition import utils
from mascope_tools.composition.corroboration import (
    IN_SOURCE_LOSSES,
    MIN_RATIO_REFERENCE,
    RATIO_BANDS,
    RATIO_CONSISTENT,
    RATIO_SPREAD_FLOOR,
    channel_log_ratio,
    ratio_consistency,
    ratio_reference,
)
from mascope_tools.composition.heuristic_filter import element_counts


# Adduct channels ACTUALLY operative in the demo -- the mechanisms that appear in the
# peak_assignment table (verified against the ionization_mechanism library), not the guessed
# panel in make_candidates. Only 5 mechanisms are ever assigned: [M+H]+, [M+NH4]+,
# [M+CH4N2O+H]+ (pos) and [M-H]-, [M+Br]- (neg). Defined-but-unused here: [M+Br2]-, [M+Br3]-,
# [M+NO3]-, [M+CO3]-, [M]+. and [M]-.. The deprotonation is "[M-H]-", the notation the
# mechanism library stores it under.
CHANNELS = {
    "neg": {"primary": "[M-H]-", "adducts": ["[M-H]-", "[M+Br]-"]},
    "pos": {"primary": "[M+H]+", "adducts": ["[M+H]+", "[M+NH4]+", "[M+CH4N2O+H]+"]},
}
# Random decoy offsets are drawn in this |Da| range (comparable to real adduct differences,
# ~17-80 Da) and rejected if within TOL_DA of any real adduct offset or a 13C/12C multiple.
OFFSET_RANGE = (10.0, 90.0)
ISOTOPE_STEP = 1.003355
# Steps of the lattice of related formulas that no source sheds: step 4.1's series units. The
# in-source fragment measurement reads them beside the losses as its control.
LATTICE_STEPS = {"CH2": {"C": 1, "H": 2}, "O": {"O": 1}}


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


def peak_height_at(mzs, ints, snrs, target, *, tol_da, snr_min) -> float | None:
    """The height of the peak :func:`peak_present` decides on: the highest-SNR peak within tol of
    target, if its SNR is at least snr_min; else None."""
    lo = np.searchsorted(mzs, target - tol_da, "left")
    hi = np.searchsorted(mzs, target + tol_da, "right")
    if hi <= lo:
        return None
    best = lo + int(np.argmax(snrs[lo:hi]))
    if snrs[best] < snr_min:
        return None
    return float(ints[best])


class MatchedAnchors:
    """Anchor-swap anchors of about a given height: the spectrum's peaks at or above the SNR floor,
    sorted by log height, from which a draw takes one of the ``k`` nearest in height."""

    def __init__(self, mzs, ints, snrs, *, snr_min, k):
        keep = (snrs >= snr_min) & (ints > 0)
        order = np.argsort(np.log(ints[keep]))
        self.mz = mzs[keep][order]
        self.logh = np.log(ints[keep])[order]
        self.k = k

    def draw(self, rng, height, *, away_from):
        """One anchor (m/z, height) of about ``height``, not within 1 Da of ``away_from``."""
        if len(self.mz) < 2 * self.k:
            return None
        at = int(np.searchsorted(self.logh, np.log(height)))
        lo = max(0, min(at - self.k // 2, len(self.mz) - self.k))
        for _ in range(10):
            j = lo + int(rng.integers(self.k))
            if abs(self.mz[j] - away_from) >= 1.0:
                return float(self.mz[j]), float(np.exp(self.logh[j]))
        return None


def loss_mass(formula: str) -> float:
    return float(calculate_mass(formula=formula))


def file_bootstrap(
    per_file: pd.DataFrame, num: str, den: str, num0: str, den0: str, *, n, rng
):
    """LR = (sum num / sum den) / (sum num0 / sum den0) over files, and its 95% interval from a
    bootstrap over files. ``per_file`` holds one row per file with the four counts."""

    def lr(table):
        a, b = table[num].sum(), table[den].sum()
        c, d = table[num0].sum(), table[den0].sum()
        if not (a > 0 and b > 0 and c > 0 and d > 0):
            return float("nan")
        return (a / b) / (c / d)

    point = lr(per_file)
    counts = per_file[[num, den, num0, den0]].to_numpy(float)
    m = len(counts)
    if m == 0:
        return point, (float("nan"), float("nan"))
    draws = []
    for _ in range(n):
        w = np.bincount(rng.integers(m, size=m), minlength=m).astype(float)
        a, b, c, d = (w[:, None] * counts).sum(axis=0)
        if a > 0 and b > 0 and c > 0 and d > 0:
            draws.append((a / b) / (c / d))
    if len(draws) < n * 0.9:
        return point, (float("nan"), float("nan"))
    return point, (float(np.percentile(draws, 2.5)), float(np.percentile(draws, 97.5)))


def _clears_one(lr, ci) -> bool:
    return bool(np.isfinite(lr) and lr > 1.0 and np.isfinite(ci[0]) and ci[0] > 1.0)


def _rounded(x, digits=3):
    return None if x is None or not np.isfinite(x) else round(float(x), digits)


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
    ap.add_argument(
        "--match-anchors",
        type=int,
        default=40,
        help="an intensity-matched swap draws its anchor from the k peaks nearest the "
        "primary's height",
    )
    ap.add_argument(
        "--bootstrap", type=int, default=1000, help="bootstrap draws over files"
    )
    ap.add_argument("--json", default=None, help="write the step 4.16 measurement here")
    a = ap.parse_args()
    rng = np.random.default_rng(0)
    # The step 4.16 draws take a stream of their own, so the adduct presence numbers above stay
    # what they were.
    rng_matched = np.random.default_rng(1)
    step_masses = {
        step: loss_mass(step) for step in [*IN_SOURCE_LOSSES, *LATTICE_STEPS]
    }

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
    ratio_slots = []  # step 4.16: a present adduct slot's log ratio, real or matched swap
    fragment_slots = []  # step 4.16: a loss slot's presence, real or matched swap
    detection_id = 0
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
        matched = MatchedAnchors(mzs, ints, snrs, snr_min=a.snr, k=a.match_anchors)
        sorted_heights = np.sort(ints)

        def height_percentile(height, _sorted=sorted_heights):
            return float(np.searchsorted(_sorted, height, "right")) / len(_sorted)

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
            # step 4.16, on the same detection: the ratio of each present adduct to the primary,
            # and a peak one in-source loss below the primary ion, each against swaps re-anchored
            # on peaks of about the primary's own height.
            detection_id += 1
            h_p = peak_height_at(mzs, ints, snrs, p, tol_da=tol, snr_min=0.0)
            if h_p is None or h_p <= 0:
                continue
            for m, off in real_offsets:
                pair = tuple(sorted((primary, m)))
                q = p + off
                h_q = peak_height_at(
                    mzs,
                    ints,
                    snrs,
                    q,
                    tol_da=max(tol, q * window * 1e-6),
                    snr_min=a.snr,
                )
                heights = {primary: h_p, m: h_q}
                if h_q is not None:
                    ratio_slots.append(
                        {
                            "filename": f,
                            "detection": detection_id,
                            "pair": pair,
                            "null_type": "real",
                            "log_ratio": channel_log_ratio(
                                heights[pair[0]], heights[pair[1]]
                            ),
                            "partner_pct": height_percentile(h_q),
                        }
                    )
                for _ in range(a.decoys_per_adduct):
                    anchor = matched.draw(rng_matched, h_p, away_from=p)
                    if anchor is None:
                        continue
                    pp, h_pp = anchor
                    qq = pp + off
                    if qq <= mzs[0] or qq >= mzs[-1]:
                        continue
                    h_qq = peak_height_at(
                        mzs, ints, snrs, qq, tol_da=qq * window * 1e-6, snr_min=a.snr
                    )
                    if h_qq is None:
                        continue
                    swapped = {primary: h_pp, m: h_qq}
                    ratio_slots.append(
                        {
                            "filename": f,
                            "detection": detection_id,
                            "pair": pair,
                            "null_type": "swap",
                            "log_ratio": channel_log_ratio(
                                swapped[pair[0]], swapped[pair[1]]
                            ),
                            "partner_pct": height_percentile(h_qq),
                        }
                    )
            # A source only takes a neutral away from an ion, so the same step as a GAIN (a peak at
            # the primary ion plus the neutral) is the control: where a gain is as likely as a
            # loss, the loss's likelihood ratio reads the spectrum's lattice of related formulas,
            # not fragmentation. CH2 and O, the series units of step 4.1 that no source sheds,
            # are the lattice's own reading.
            counts = element_counts(neutral) or {}
            steps = [(loss, -1, "loss") for loss in IN_SOURCE_LOSSES]
            steps += [(loss, +1, "gain") for loss in IN_SOURCE_LOSSES]
            steps += [(step, -1, "lattice") for step in LATTICE_STEPS]
            for loss, sign, direction in steps:
                loss_counts = IN_SOURCE_LOSSES.get(loss) or LATTICE_STEPS[loss]
                # The gain is asked of the same neutrals as the loss, those that hold it: a
                # neutral that does not differs from one that does in more than the step.
                if any(counts.get(el, 0) < n for el, n in loss_counts.items()) or sum(
                    counts.values()
                ) <= sum(loss_counts.values()):
                    continue
                q = p + sign * step_masses[loss]
                if q <= mzs[0] or q >= mzs[-1]:
                    continue
                h_q = peak_height_at(mzs, ints, snrs, q, tol_da=tol, snr_min=a.snr)
                fragment_slots.append(
                    {
                        "filename": f,
                        "loss": loss,
                        "direction": direction,
                        "polarity": pol,
                        "null_type": "real",
                        "present": h_q is not None,
                        "dimmer": h_q is not None and h_q < h_p,
                    }
                )
                for _ in range(a.decoys_per_adduct):
                    anchor = matched.draw(rng_matched, h_p, away_from=p)
                    if anchor is None:
                        continue
                    pp, h_pp = anchor
                    qq = pp + sign * step_masses[loss]
                    if qq <= mzs[0] or qq >= mzs[-1]:
                        continue
                    h_qq = peak_height_at(
                        mzs, ints, snrs, qq, tol_da=qq * window * 1e-6, snr_min=a.snr
                    )
                    fragment_slots.append(
                        {
                            "filename": f,
                            "loss": loss,
                            "direction": direction,
                            "polarity": pol,
                            "null_type": "swap",
                            "present": h_qq is not None,
                            "dimmer": h_qq is not None and h_qq < h_pp,
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

    measured = measure_step_416(
        pd.DataFrame(ratio_slots),
        pd.DataFrame(fragment_slots),
        bootstrap=a.bootstrap,
        rng=np.random.default_rng(2),
    )
    measured["source"] = (
        f"corroboration_benchmark.py on {a.bundle_dir.rstrip('/').split('/')[-1]} "
        f"({C['filename'].nunique()} files, {len(C)} detections)"
    )
    if a.json:
        with open(a.json, "w") as out:
            json.dump(measured, out, indent=2)
            out.write("\n")
        print(f"\nwrote {a.json}")
    return 0


def _band_lrs(g: pd.DataFrame, bands, *, bootstrap, rng) -> dict:
    """Per band, P(band | real), P(band | swap), their LR and its file-bootstrap interval."""
    per_file = (
        g.assign(real=g["null_type"] == "real", swap=g["null_type"] == "swap")
        .groupby("filename")
        .agg(n_real=("real", "sum"), n_swap=("swap", "sum"))
    )
    for b in bands:
        for kind in ("real", "swap"):
            per_file[f"{kind}_{b}"] = (
                g[(g["null_type"] == kind) & (g["band"] == b)]
                .groupby("filename")
                .size()
            )
    per_file = per_file.fillna(0)
    record = {}
    for b in bands:
        lr, ci = file_bootstrap(
            per_file, f"real_{b}", "n_real", f"swap_{b}", "n_swap", n=bootstrap, rng=rng
        )
        record[b] = {
            "p_real": _rounded(
                per_file[f"real_{b}"].sum() / max(per_file["n_real"].sum(), 1)
            ),
            "p_swap": _rounded(
                per_file[f"swap_{b}"].sum() / max(per_file["n_swap"].sum(), 1)
            ),
            "lr": _rounded(lr),
            "lr_ci": [_rounded(ci[0]), _rounded(ci[1])],
            "clears_one": _clears_one(lr, ci),
        }
    return record


def _print_bands(record: dict, indent: str = "    ") -> None:
    for b, r in record.items():
        print(
            f"{indent}{b:10s} P(real)={r['p_real']} P(swap)={r['p_swap']} LR={r['lr']} "
            f"{r['lr_ci']}{'  <- clears one' if r['clears_one'] else ''}"
        )


def _presence_lr(g: pd.DataFrame, *, bootstrap, rng) -> dict:
    """P(present | real), P(present | swap), their LR with its interval, and how often a present
    partner is dimmer than the anchor, real and swap."""
    per_file = (
        g.assign(
            real=g["null_type"] == "real",
            swap=g["null_type"] == "swap",
            real_present=(g["null_type"] == "real") & g["present"],
            swap_present=(g["null_type"] == "swap") & g["present"],
            real_dimmer=(g["null_type"] == "real") & g["dimmer"],
            swap_dimmer=(g["null_type"] == "swap") & g["dimmer"],
        )
        .groupby("filename")[
            [
                "real",
                "swap",
                "real_present",
                "swap_present",
                "real_dimmer",
                "swap_dimmer",
            ]
        ]
        .sum()
    )
    n_real, n_swap = int(per_file["real"].sum()), int(per_file["swap"].sum())
    record = {"n_real": n_real, "n_swap": n_swap}
    if n_real < 20 or n_swap < 20:
        return record
    lr, ci = file_bootstrap(
        per_file, "real_present", "real", "swap_present", "swap", n=bootstrap, rng=rng
    )
    shorter_lr, shorter_ci = file_bootstrap(
        per_file, "real_dimmer", "real", "swap_dimmer", "swap", n=bootstrap, rng=rng
    )
    record.update(
        {
            # Present, and on the shorter peak of the two. For a loss that is the pair the
            # engine names (``cross_channel.in_source_fragments``: the lighter the shorter); for
            # the same neutral gained it is the mirror, the same relation one step up in mass.
            "shorter": {
                "p_real": _rounded(per_file["real_dimmer"].sum() / n_real),
                "p_swap": _rounded(per_file["swap_dimmer"].sum() / n_swap),
                "lr": _rounded(shorter_lr),
                "lr_ci": [_rounded(shorter_ci[0]), _rounded(shorter_ci[1])],
                "clears_one": _clears_one(shorter_lr, shorter_ci),
            },
            "p_real": _rounded(per_file["real_present"].sum() / n_real),
            "p_swap": _rounded(per_file["swap_present"].sum() / n_swap),
            "lr": _rounded(lr),
            "lr_ci": [_rounded(ci[0]), _rounded(ci[1])],
            "clears_one": _clears_one(lr, ci),
            "dimmer_real": _rounded(
                per_file["real_dimmer"].sum() / max(per_file["real_present"].sum(), 1)
            ),
            "dimmer_swap": _rounded(
                per_file["swap_dimmer"].sum() / max(per_file["swap_present"].sum(), 1)
            ),
        }
    )
    return record


def measure_step_416(R: pd.DataFrame, F: pd.DataFrame, *, bootstrap, rng) -> dict:
    """The step 4.16 likelihood ratios: ratio consistency per adduct pair and band, and the
    in-source fragment presence per loss, each with its file-bootstrap interval, beside the
    controls that say what each reads.

    A factor is marked ``eligible`` only where its LR clears one AND its control does not read the
    same. The ratio is a consistency factor, so it can fold only through its consistent band, and
    only where that band clears one inside every partner-brightness stratum: a band further out
    reads a ratio the sample's other neutrals do not share, which corroborates no consistency
    whatever its LR. A loss is eligible only where the pair the engine names, a shorter peak one
    loss down, has an LR whose interval lies above its mirror's, a shorter peak the same neutral
    up: a source sheds a neutral and does not add one, so a mirror as likely as the loss says the
    loss reads the spectrum's lattice of related formulas."""
    bands = [band for _, band in RATIO_BANDS]
    out = {
        "method": (
            "anchor-swap null re-anchored on peaks of about the primary's height; "
            "LR = P(outcome | real) / P(outcome | swap), 95% interval by a bootstrap over files"
        ),
        "ratio_consistency": {
            "bands": {band: edge for edge, band in RATIO_BANDS if np.isfinite(edge)},
            "min_reference": MIN_RATIO_REFERENCE,
            "spread_floor": RATIO_SPREAD_FLOOR,
            "pairs": {},
        },
        "in_source_fragments": {"losses": {}, "lattice_controls": {}},
    }

    print("\n=== step 4.16: ratio consistency, given the adduct is present ===")
    if not R.empty:
        # Each slot is read against the same pair's REAL ratios over the file's other detections,
        # as the engine reads a neutral against the sample's other neutrals.
        z, band = [], []
        real = R[R["null_type"] == "real"]
        by_file_pair = {
            key: g[["detection", "log_ratio"]].to_numpy()
            for key, g in real.groupby(["filename", "pair"])
        }
        for row in R.itertuples():
            pool = by_file_pair.get((row.filename, row.pair))
            others = [] if pool is None else [r for d, r in pool if d != row.detection]
            reference = ratio_reference(others)
            if reference is None or row.log_ratio is None:
                z.append(np.nan)
                band.append(None)
                continue
            reading = ratio_consistency(row.log_ratio, reference)
            z.append(reading.z)
            band.append(reading.band)
        R = R.assign(z=z, band=band)
        R = R[R["band"].notna()]
        for pair, g in R.groupby("pair"):
            key = "|".join(pair)
            n_real = int((g["null_type"] == "real").sum())
            n_swap = int((g["null_type"] == "swap").sum())
            record = {"n_real": n_real, "n_swap": n_swap}
            print(f"  {key}: n_real={n_real} n_swap={n_swap}")
            if n_real < 20 or n_swap < 20:
                out["ratio_consistency"]["pairs"][key] = record
                continue
            record["bands"] = _band_lrs(g, bands, bootstrap=bootstrap, rng=rng)
            _print_bands(record["bands"])
            # The control: the same bands among partners of one brightness. Where a band's LR
            # moves to one inside every stratum, it read the partner's brightness, not the ratio.
            real_pct = g[g["null_type"] == "real"]["partner_pct"]
            swap_pct = g[g["null_type"] == "swap"]["partner_pct"]
            record["partner_brightness"] = {
                "median_pct_real": _rounded(real_pct.median()),
                "median_pct_swap": _rounded(swap_pct.median()),
                "top_decile_real": _rounded((real_pct >= 0.9).mean()),
                "top_decile_swap": _rounded((swap_pct >= 0.9).mean()),
            }
            print(f"    partner brightness: {record['partner_brightness']}")
            edges = np.quantile(g["partner_pct"], [0.0, 0.25, 0.5, 0.75, 1.0])
            strata = {}
            for i in range(4):
                lo, hi = edges[i], edges[i + 1]
                inside = (g["partner_pct"] >= lo) & (
                    (g["partner_pct"] <= hi) if i == 3 else (g["partner_pct"] < hi)
                )
                gs = g[inside]
                name = f"q{i + 1} [{lo:.2f}, {hi:.2f}]"
                if (gs["null_type"] == "real").sum() < 20 or (
                    gs["null_type"] == "swap"
                ).sum() < 20:
                    continue
                strata[name] = _band_lrs(gs, bands, bootstrap=bootstrap, rng=rng)
                print(f"    within partner-brightness quartile {name}:")
                _print_bands(strata[name], indent="      ")
            record["within_partner_brightness"] = strata
            for b in bands:
                record["bands"][b]["eligible"] = bool(
                    b == RATIO_CONSISTENT
                    and record["bands"][b]["clears_one"]
                    and strata
                    and all(s[b]["clears_one"] for s in strata.values())
                )
            out["ratio_consistency"]["pairs"][key] = record

    print(
        "\n=== step 4.16: a peak one step from the primary ion: loss, gain, lattice ==="
    )
    if not F.empty:
        for (direction, loss), g in F.groupby(["direction", "loss"]):
            record = _presence_lr(g, bootstrap=bootstrap, rng=rng)
            if direction == "lattice":
                out["in_source_fragments"]["lattice_controls"][loss] = record
            else:
                out["in_source_fragments"]["losses"].setdefault(loss, {})[direction] = (
                    record
                )
            print(f"  {direction:7s} {loss:6s} {record}")
        for loss, record in out["in_source_fragments"]["losses"].items():
            # The pair the engine names, a shorter peak one loss down, against its mirror one
            # step up. A loss the source makes is likelier than the gain; where the gain's
            # interval reaches the loss's, the loss reads the lattice.
            lost = record.get("loss", {}).get("shorter", {})
            gained = record.get("gain", {}).get("shorter", {})
            record["eligible"] = bool(
                lost.get("clears_one")
                and gained.get("lr_ci")
                and gained["lr_ci"][1] is not None
                and lost["lr_ci"][0] is not None
                and lost["lr_ci"][0] > gained["lr_ci"][1]
            )
    return out


if __name__ == "__main__":
    sys.exit(main())
