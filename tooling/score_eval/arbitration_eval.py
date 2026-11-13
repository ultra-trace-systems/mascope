"""Decoy/arbitration harness: what a chemistry context does to the competition.

`docs/dev/chemistry_profiles.md` section 7 asks for this run: the decoy harness
with the matched context, with `none`, and with a deliberately mismatched one,
the first pair to quantify the context layer and the third to bound the harm of
a wrong choice. Assignment quality plan step 4.18 (MAS-328) runs it.

For every monoisotopic anchor of the demo bundle (`expected/peaks.parquet`,
the seeded targets each file matched) it builds the pool the untargeted stage
would compete at that m/z, and asks who wins:

1. Enumerate every neutral whose ion lands within the profile's window, through
   the mechanisms the demo assigns, on the reagent profile's own grid (`BR` on
   the negative files, `UR` on the positive ones, the context `none`).
2. Per arm, keep what the engine's Stage B would keep under that arm's context:
   the grid the context's element caps narrow (`resolve_element_ranges`), the
   finder's per-mechanism row cap on what is left, then the heuristic rules with
   the context's ratio windows riding along as the boolean gate that shipped
   (`rule_context_ratios`), exactly as `ResolvedProfile.heuristics_config`
   configures them.
3. The true reading joins the pool unless the arm's context gates it: by a cap
   or by a ratio window. The structural rules do not judge it, in any arm: the
   seeded target is the truth by definition, and the arms differ only in their
   context.
4. Merge the readings of one ion into one hypothesis, as Stage B does before
   it scores anything (`elect_same_ion_families`): the closed-shell neutral
   with the heaviest mechanism is elected, whatever the context. A truth whose
   own family elects a twin (X through `[M+NH4]+` against X+NH3 through
   `[M+H]+`) has lost to the engine's election rule, not to the competition:
   Stage B keeps it on the elected row as a same-ion alternative.
5. Score every candidate's ion against the file's full spectrum
   (`score_pattern_v2`, the per-file fitted mass accuracy, as `score_eval.py
   --scorer v2` does) and compete the pool by fit x plausibility
   (`arbitrate_candidates`).

Per arm it reports, on one fixed set of anchors (the anchors whose truth the
reagent grid holds and the spectrum scores, and whose truth wins its own
family's election under `none`; a context only removes readings, so such a
truth wins it in every arm that keeps it):

  top1_all            the winner is the truth; a gated truth is a miss
  top1_contested      the same over the anchors contested under `none` (a pool of
                      two or more), so every arm is read on the same anchors
  false_winner_rate   winners that are a decoy, of all winners
  wrong_winners_confident
                      decoy winners at confidence 0.9 or more
  kept_at_5pct_fdr    winners accepted at the most permissive confidence cut
                      whose q-value is at most 5 % (`threshold_at_fdr`)
  ece                 calibration error of the shipped Orbitrap curve over every
                      candidate (evidence -> P(correct), true vs decoy; the
                      population the curve was fit on)
  ece_winners         the same over the winners alone, beside the curve's mean
                      P(correct) on them (winner_mean_p)
  truth_gated         anchors whose true reading the arm's context removed, by
                      cap and by ratio window, and by the truth's carbon count
  truth_lost_election over every measured anchor, the truths a same-ion twin
                      displaced in the election; under `none` these are the
                      anchors left out of the fixed set, and an arm that
                      removes the twin wins some of them back

The arms: `matched` is the profile's default context (`BR` ambient air, `UR`
uronium), `mismatched` the other demo reagent's (`BR` uronium, `UR` ambient air:
a user who picked the wrong one of the two), `none` the identity, and `all`
in the list adds every shipped context besides. An arm suffixed `:caps` keeps only
its context's element caps and `:ratios` only its ratio windows, so the two
halves of a context are read apart (`matched:caps`); `:graded` keeps the caps
as the grid and reads the ratio windows as a factor instead of a gate, at the
plausibility floor (0.1) outside a window with no taper: the harshest the
graded, floored factor of the note's section 4.2 could be, short of building it.

What it does not model: Stage A (the targets and reference lists, whose ceiling
the context also sets through `known_window`), the corroboration update across
a compound's adducts, and the competition between anchors for one peak. The
harness reads one anchor's competition at a time, as the measurements in
`assignment_confidence.md` did.

The truth's neutral and mechanism are the seed's: the bundle records only the
ion. Export them once from a database restored from the bundle's seed (the
demo env's `mascope_demo`):

    psql -d mascope_demo -At -F, -c "select ti.target_ion_id, tc.target_compound_formula, \\
      im.ionization_mechanism from target_ion ti join target_compound tc \\
      using (target_compound_id) join ionization_mechanism im \\
      using (ionization_mechanism_id)" > demo_ions.csv

    python arbitration_eval.py <bundle_dir> --ions demo_ions.csv \\
      [--arms all,matched:graded,matched:caps,...] [--workers 8] [--out m.json]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field, replace

import numpy as np
import pandas as pd
import polars as pl


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from score_eval import (  # noqa: E402
    PRED_SIGMA_PPM,
    fit_mass_accuracy,
    load_filestore_peaks,
    score_ion_v2,
)

from mascope_tools.composition import profiles as presets  # noqa: E402
from mascope_tools.composition.arbitration import (  # noqa: E402
    arbitrate_candidates,
    fdr_curve,
)
from mascope_tools.composition.calibration import (  # noqa: E402
    PROVISIONAL_ORBITRAP,
    apply_calibration,
    calibration_error,
)
from mascope_tools.composition.config import DEFAULT_MAXIMUM_ROWS  # noqa: E402
from mascope_tools.composition.finder import find_compositions  # noqa: E402
from mascope_tools.composition.grid import admits  # noqa: E402
from mascope_tools.composition.heuristic_filter import (  # noqa: E402
    PLAUSIBILITY_FLOOR,
    apply_heuristic_rules,
    elect_same_ion_families,
    element_counts,
    rule_context_ratios,
)
from mascope_tools.composition.models import (  # noqa: E402
    CompositionSearchConfig,
    HeuristicFilterConfig,
)
from mascope_tools.composition.utils import (  # noqa: E402
    combine_formula_and_ionization,
    parse_ionization,
)


# The mechanisms the demo assigns, per polarity: the operative panel of
# `corroboration_benchmark.py`. The `^NO3` readings of the negative files sit
# outside the bromide profile and are left out of every arm.
PANEL = {
    "neg": ("[M-H]-", "[M+Br]-"),
    "pos": ("[M+H]+", "[M+NH4]+", "[M+CH4N2O+H]+"),
}
PROFILE = {"neg": presets.BR, "pos": presets.UR}
# The deliberate mismatch: the other demo reagent's default context.
MISMATCHED = {"BR": presets.URONIUM.name, "UR": presets.AMBIENT_AIR.name}
FIXED_ARMS = ("matched", "none", "mismatched")
# Enumerated wide once, then cut per arm: a cap this far above the finder's own
# is never reached at the profile windows, so each arm's cut is the one the
# finder would make on that arm's grid.
ENUMERATION_ROWS = 5000
MATCH_K = 6.0  # score_eval's v2 match window, in units of the fitted sigma
TARGET_FDR = 0.05
CONFIDENT = 0.9  # a winner's arbitration confidence read as confident


def _polarity(filename: str) -> str | None:
    if "_neg_" in filename:
        return "neg"
    if "_pos_" in filename:
        return "pos"
    return None


def _hill(neutral: str) -> str:
    """A neutral formula in Hill order, so the seed's spelling and the finder's
    compare equal."""
    counts = element_counts(neutral) or {}
    order = [el for el in ("C", "H") if el in counts] + sorted(
        el for el in counts if el not in ("C", "H")
    )
    return "".join(f"{el}{counts[el] if counts[el] > 1 else ''}" for el in order)


def _ion_key(ion: str) -> str:
    """An ion formula in Hill order with its sign, for matching spellings."""
    sign = ion[-1] if ion and ion[-1] in "+-" else ""
    return _hill(ion[:-1] if sign else ion) + sign


def arm_context(arm: str, profile: presets.ReagentProfile) -> presets.ChemistryContext:
    """The context an arm names for a profile's files.

    A ``:caps`` suffix keeps only the context's element caps, a ``:ratios``
    suffix only its ratio windows: the two halves of what a context does, the
    grid it narrows and the gate it adds, read apart.
    """
    name, _, half = arm.partition(":")
    if name == "matched":
        context = presets.get_chemistry_context(profile.default_context)
    elif name == "mismatched":
        context = presets.get_chemistry_context(MISMATCHED[profile.name])
    else:
        context = presets.get_chemistry_context(name)
    if half in ("caps", "graded"):
        return replace(context, h_to_c=None, o_to_c=None, n_to_c=None, dbe_to_c=None)
    if half == "ratios":
        return replace(context, element_caps={})
    if half:
        raise ValueError(f"unknown arm half {half!r}: caps, ratios or graded")
    return context


def graded_windows(
    arm: str, profile: presets.ReagentProfile
) -> dict[str, tuple[float, float]]:
    """The ratio windows a ``:graded`` arm reads as a factor rather than a gate."""
    name, _, half = arm.partition(":")
    if half != "graded":
        return {}
    return arm_context(name, profile).ratio_windows()


def outside_windows(formulas: list[str], windows: dict) -> list[bool]:
    """Which formulas a context's ratio windows would gate."""
    if not windows or not formulas:
        return [False] * len(formulas)
    mask, _ = rule_context_ratios(
        pl.DataFrame({"formula": formulas}),
        heuristics_config=HeuristicFilterConfig(context_ratio_windows=windows),
    )
    return [not keep for keep in mask.to_list()]


def _arm_label(arm: str, profile: presets.ReagentProfile) -> str:
    """The context an arm resolved to, with its half where it names one."""
    _, sep, half = arm.partition(":")
    return arm_context(arm, profile).name + sep + half


def search_config(
    profile: presets.ReagentProfile,
    context: presets.ChemistryContext,
    rows: int = DEFAULT_MAXIMUM_ROWS,
) -> CompositionSearchConfig:
    """The finder configuration Stage B builds for this profile and context
    (`ResolvedProfile.search_config`), over the demo panel."""
    polarity = "neg" if profile.polarity == "-" else "pos"
    return CompositionSearchConfig(
        ionizations=",".join(PANEL[polarity]),
        mass_range_ppm=presets.resolve_mz_precision_ppm(profile, "orbi"),
        element_count_ranges=presets.resolve_element_ranges(profile, context),
        use_unsaturation=True,
        min_unsaturation=-1000.0,
        max_unsaturation=10000.0,
        max_result_rows=rows,
    )


def truth_gate(
    neutral: str, profile: presets.ReagentProfile, context: presets.ChemistryContext
) -> str | None:
    """Why the arm's context removes the true neutral, or None when it keeps it.

    ``"cap"`` when the context's element caps take it off the grid, ``"ratio"``
    when a ratio window rejects it. The reagent grid's own bounds are not the
    context's doing; anchors outside them are left out of every arm.
    """
    counts = element_counts(neutral) or {}
    if not admits(search_config(profile, context), counts):
        return "cap"
    mask, _ = rule_context_ratios(
        pl.DataFrame({"formula": [neutral]}),
        heuristics_config=HeuristicFilterConfig(
            context_ratio_windows=context.ratio_windows()
        ),
    )
    return None if bool(mask[0]) else "ratio"


@dataclass
class ArmAnchor:
    """One anchor's outcome under one arm."""

    n_candidates: int = 0
    gated: str | None = None
    twin: bool = False  # a kept truth shares its ion with another reading
    lost_election: bool = False  # ... and the election chose that reading
    winner_correct: bool | None = None  # None: no winner (empty pool)
    winner_confidence: float = 0.0
    winner_evidence: float = 0.0
    evidence: list[float] = field(default_factory=list)
    is_true: list[bool] = field(default_factory=list)


def _arm_pool(
    readings: list[dict],
    profile: presets.ReagentProfile,
    context: presets.ChemistryContext,
) -> list[dict]:
    """Stage B's surviving readings at one anchor under one context."""
    config = search_config(profile, context)
    per_mechanism: dict[str, list[dict]] = defaultdict(list)
    for reading in readings:
        counts = element_counts(reading["formula"]) or {}
        if admits(config, counts):
            per_mechanism[reading["ionization_mechanism"]].append(reading)
    kept: list[dict] = []
    for rows in per_mechanism.values():
        rows.sort(key=lambda r: abs(r["composition_error_ppm"]))
        kept.extend(rows[: config.max_result_rows])
    if not kept:
        return []
    survivors, _ = apply_heuristic_rules(
        kept,
        HeuristicFilterConfig(
            use_senior=True, context_ratio_windows=context.ratio_windows()
        ),
    )
    return survivors


def evaluate_file(job: tuple) -> dict:
    """Every anchor of one file under every arm. Runs in a worker process."""
    bundle_dir, filename, anchors, arms = job
    polarity = _polarity(filename)
    profile = PROFILE[polarity]
    widest = search_config(profile, presets.NO_CONTEXT, rows=ENUMERATION_ROWS)
    peaks = load_filestore_peaks(bundle_dir, filename)
    if peaks is None:
        return {"filename": filename, "missing_spectrum": True, "anchors": []}
    peaks = peaks[~peaks["is_satellite"]]
    mzs = peaks["mz"].to_numpy(float)
    ints = peaks["height"].to_numpy(float)
    snrs = peaks["snr"].to_numpy(float)
    counts = (
        (peaks["n_scans"].to_numpy(float), peaks["n_scans_total"].to_numpy(float))
        if "n_scans" in peaks
        else None
    )
    mu, sigma, _ = fit_mass_accuracy(bundle_dir, filename)
    if sigma is not None:
        sigma = float(np.hypot(sigma, PRED_SIGMA_PPM))
        window = max(MATCH_K * sigma, 1.0)
    else:
        window, mu = widest.mass_range_ppm, 0.0
    fits: dict[str, float | None] = {}

    def fit(ion: str) -> float | None:
        if ion not in fits:
            fits[ion] = score_ion_v2(
                mzs,
                ints,
                snrs,
                ion,
                ppm=window,
                sigma_ppm=sigma,
                mu=mu,
                counts=counts,
            )
        return fits[ion]

    contexts = {arm: arm_context(arm, profile) for arm in arms}
    graded = {arm: graded_windows(arm, profile) for arm in arms}
    out = []
    capped = 0
    for anchor in anchors:
        mz = anchor["mz"]
        # One ion can be two seeded readings (a neutral through [M+H]+ and a
        # lighter one through [M+CH4N2O+H]+): the bundle cannot say which the
        # peak is, so either winning is right.
        truths = sorted(
            (_hill(neutral), mechanism)
            for neutral, mechanism in anchor["readings"]
            if mechanism in PANEL[polarity]
            and admits(widest, element_counts(neutral) or {})
        )
        record = {
            "neutral": " | ".join(neutral for neutral, _ in truths),
            "polarity": polarity,
            "arms": {},
        }
        if not truths:
            record["outside_grid"] = True
            out.append(record)
            continue
        truth_fit = fit(anchor["ion"])
        if truth_fit is None:
            record["unscored"] = True
            out.append(record)
            continue
        readings = [
            r
            for r in find_compositions(mz, widest)
            if r.get("formula") and r["formula"] != "---"
        ]
        per_mechanism = Counter(r["ionization_mechanism"] for r in readings)
        if per_mechanism and max(per_mechanism.values()) >= ENUMERATION_ROWS:
            capped += 1
        record["carbon"] = min(
            int((element_counts(neutral) or {}).get("C", 0)) for neutral, _ in truths
        )
        for arm, context in contexts.items():
            result = ArmAnchor()
            gates = {key: truth_gate(key[0], profile, context) for key in truths}
            kept_truths = {key for key, gate in gates.items() if gate is None}
            if not kept_truths:
                result.gated = sorted(gates.values())[0]
            pool: dict[tuple[str, str], float] = {}
            ion_of: dict[tuple[str, str], str] = {}
            for reading in _arm_pool(readings, profile, context):
                key = (_hill(reading["formula"]), reading["ionization_mechanism"])
                if key in gates:
                    continue
                score = fit(reading["ion"])
                if score is not None:
                    pool[key] = score
                    ion_of[key] = _ion_key(reading["ion"])
            for key in kept_truths:
                pool[key] = truth_fit
                ion_of[key] = _ion_key(anchor["ion"])
            # One hypothesis per ion, before any ranking: Stage B's election.
            elected = {
                c["key"]
                for c in elect_same_ion_families(
                    [
                        {
                            "ion": ion_of[k],
                            "formula": k[0],
                            "ionization_mechanism": k[1],
                            "key": k,
                        }
                        for k in pool
                    ]
                )
            }
            if kept_truths:
                result.twin = any(
                    ion_of[k] == ion_of[t] and k not in kept_truths
                    for k in pool
                    for t in kept_truths
                )
                result.lost_election = not (elected & kept_truths)
            pool = {k: v for k, v in pool.items() if k in elected}
            if graded[arm]:
                # The floored factor at its harshest: outside a window, the
                # floor at once, with no taper.
                keys = list(pool)
                for key, outside in zip(
                    keys, outside_windows([k[0] for k in keys], graded[arm])
                ):
                    if outside:
                        pool[key] *= PLAUSIBILITY_FLOOR
            result.n_candidates = len(pool)
            if pool:
                # Plausibility judges the neutral; one neutral reaches one m/z
                # through one mechanism at most, so the formula is the key.
                by_formula = {key[0]: key for key in pool}
                ranked = arbitrate_candidates(
                    [{"formula": k[0], "fit_score": s} for k, s in pool.items()]
                )
                for cand in ranked:
                    result.evidence.append(cand.evidence)
                    result.is_true.append(by_formula[cand.formula] in kept_truths)
                best = ranked[0]
                result.winner_correct = by_formula[best.formula] in kept_truths
                result.winner_confidence = best.confidence
                result.winner_evidence = best.evidence
            record["arms"][arm] = result.__dict__
        out.append(record)
    return {"filename": filename, "anchors": out, "capped": capped}


def _round(value: float | None, digits: int = 4) -> float | None:
    return None if value is None else round(float(value), digits)


def summarize(records: list[dict], arms: list[str]) -> dict:
    """The per-arm metrics over the fixed anchor set."""
    measured = [r for r in records if r.get("arms")]
    fixed = [r for r in measured if not r["arms"]["none"]["lost_election"]]
    contested = [r for r in fixed if r["arms"]["none"]["n_candidates"] >= 2]
    summary: dict = {
        "anchors": len(records),
        "anchors_measured": len(fixed),
        # The election rule against the seed, not the context: out of every arm.
        "anchors_truth_lost_election_under_none": len(measured) - len(fixed),
        "anchors_outside_reagent_grid": sum(
            1 for r in records if r.get("outside_grid")
        ),
        "anchors_truth_unscored": sum(1 for r in records if r.get("unscored")),
        "anchors_contested_under_none": len(contested),
        "arms": {},
    }
    for arm in arms:
        rows = [r["arms"][arm] for r in fixed]
        # A truth its own family displaced is a same-ion alternative on the
        # elected row, not a decoy winning: a miss in top-1, out of the FDR cut.
        winners = [
            a
            for a in rows
            if a["winner_correct"] is not None and not a["lost_election"]
        ]
        correct = [bool(a["winner_correct"]) for a in winners]
        conf = [a["winner_confidence"] for a in winners]
        points = fdr_curve(conf, correct) if winners else []
        kept = max((p.n_accepted for p in points if p.q_value <= TARGET_FDR), default=0)
        evidence = np.concatenate(
            [np.asarray(a["evidence"], float) for a in rows if a["evidence"]]
        )
        truth = np.concatenate(
            [np.asarray(a["is_true"], bool) for a in rows if a["is_true"]]
        )
        win_ev = np.asarray([a["winner_evidence"] for a in winners], float)
        gated = [r for r in fixed if r["arms"][arm]["gated"]]
        own_contested = [a for a in rows if a["n_candidates"] >= 2]
        summary["arms"][arm] = {
            "top1_all": _round(np.mean([a["winner_correct"] is True for a in rows])),
            "top1_contested": _round(
                np.mean([r["arms"][arm]["winner_correct"] is True for r in contested])
            )
            if contested
            else None,
            "top1_own_contested": _round(
                np.mean([a["winner_correct"] is True for a in own_contested])
            )
            if own_contested
            else None,
            "n_own_contested": len(own_contested),
            "winners": len(winners),
            "false_winner_rate": _round(1 - np.mean(correct)) if correct else None,
            # A decoy left alone in its pool wins at full confidence: how a gated
            # truth turns into a confident wrong assignment.
            "wrong_winners_confident": sum(
                1 for c, x in zip(correct, conf) if not c and x >= CONFIDENT
            ),
            # Which way the curve misses on the winners: below the accuracy is
            # underconfident.
            "winner_mean_p": _round(
                np.mean(apply_calibration(win_ev, PROVISIONAL_ORBITRAP))
            )
            if len(win_ev)
            else None,
            "kept_at_5pct_fdr": int(kept),
            # The accepted prefix in fdr_curve's own order: wrong first in a tie.
            "kept_correct_at_5pct_fdr": int(
                sum(
                    correct[i]
                    for i in sorted(
                        range(len(conf)), key=lambda i: (-conf[i], correct[i])
                    )[:kept]
                )
            ),
            "candidates": int(len(evidence)),
            "mean_pool": _round(np.mean([a["n_candidates"] for a in rows]), 2),
            "ece": _round(
                calibration_error(
                    apply_calibration(evidence, PROVISIONAL_ORBITRAP), truth
                )
            )
            if len(evidence)
            else None,
            "ece_winners": _round(
                calibration_error(
                    apply_calibration(win_ev, PROVISIONAL_ORBITRAP), correct
                )
            )
            if len(win_ev)
            else None,
            "truth_with_twin": sum(1 for a in rows if a["twin"]),
            "truth_lost_election": sum(
                1 for r in measured if r["arms"][arm]["lost_election"]
            ),
            "truth_gated": len(gated),
            "truth_gated_by": dict(Counter(r["arms"][arm]["gated"] for r in gated)),
            "truth_gated_by_carbon": dict(
                sorted(
                    Counter("C<5" if r["carbon"] < 5 else "C>=5" for r in gated).items()
                )
            ),
            "truth_gated_neutrals": dict(
                Counter(r["neutral"] for r in gated).most_common()
            ),
        }
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("bundle_dir")
    ap.add_argument("--ions", required=True, help="target_ion_id,neutral,mechanism CSV")
    ap.add_argument(
        "--arms",
        default=",".join(FIXED_ARMS),
        help="comma-separated arms: matched, none, mismatched, a context name, "
        "each with an optional :caps, :ratios or :graded, and all for every "
        "shipped context",
    )
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    ap.add_argument("--limit-files", type=int, default=0)
    ap.add_argument("--out", help="write the metrics JSON here")
    a = ap.parse_args()

    arms: list[str] = []
    for arm in (arm.strip() for arm in a.arms.split(",")):
        if arm == "all":
            arms.extend(FIXED_ARMS)
            arms.extend(name for name in presets.CHEMISTRY_CONTEXTS if name != "none")
        elif arm:
            arms.append(arm)
    # The fixed and contested sets are read under `none`.
    arms = list(dict.fromkeys(arms + ["none"]))

    peaks = pd.read_parquet(f"{a.bundle_dir}/expected/peaks.parquet")
    seed = pd.read_csv(
        a.ions, header=None, names=["target_ion_id", "neutral", "mechanism"]
    )
    readings_of: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for row in seed.itertuples():
        try:
            ion = combine_formula_and_ionization(
                row.neutral, parse_ionization(row.mechanism)
            )
        except Exception:
            continue
        readings_of[_ion_key(ion)].add((row.neutral, row.mechanism))
    m0 = peaks[
        ~peaks["target_isotope_formula"].astype(str).str.contains(r"\[", regex=True)
    ].drop_duplicates(["filename", "target_isotope_formula"])
    m0 = m0[m0["filename"].map(_polarity).notna()]

    jobs = []
    unmapped = 0
    for filename, group in m0.groupby("filename", sort=True):
        anchors = []
        for r in group.itertuples():
            ion = str(r.target_isotope_formula)
            readings = readings_of.get(_ion_key(ion))
            if not readings:
                unmapped += 1
                continue
            anchors.append(
                {"ion": ion, "readings": sorted(readings), "mz": float(r.mz)}
            )
        jobs.append((a.bundle_dir, filename, anchors, arms))
    if a.limit_files:
        jobs = jobs[: a.limit_files]
    print(
        f"{len(jobs)} files, {sum(len(j[2]) for j in jobs)} anchors "
        f"({unmapped} without a seed reading), arms {arms}",
        file=sys.stderr,
    )

    records: list[dict] = []
    by_polarity: dict[str, list[dict]] = defaultdict(list)
    capped = 0
    with ProcessPoolExecutor(max_workers=a.workers) as pool:
        for n, result in enumerate(pool.map(evaluate_file, jobs), 1):
            capped += result.get("capped", 0)
            for record in result["anchors"]:
                records.append(record)
                by_polarity[record["polarity"]].append(record)
            if n % 10 == 0:
                print(f"  {n}/{len(jobs)} files", file=sys.stderr)

    metrics = {
        "bundle": os.path.basename(os.path.normpath(a.bundle_dir)),
        "calibration": PROVISIONAL_ORBITRAP.source,
        "enumeration_capped_anchors": capped,
        "contexts": {
            profile.name: {arm: _arm_label(arm, profile) for arm in arms}
            for profile in PROFILE.values()
        },
        "all": summarize(records, arms),
        "by_set": {
            PROFILE[pol].name: summarize(rows, arms)
            for pol, rows in sorted(by_polarity.items())
        },
    }
    text = json.dumps(metrics, indent=2)
    print(text)
    if a.out:
        with open(a.out, "w") as handle:
            handle.write(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
