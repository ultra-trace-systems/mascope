"""Compare two peak-assignment engines on the same samples, peak by peak.

Both engines' runs live in the same store - the in-app engine writes its own,
an external engine publishes into it through ``runs/import`` (peaky does with
``peaky publish``) - so a comparison needs no engine-specific files: for each
sample the latest completed run of each engine is read through the SDK and the
two ledgers are joined on ``sample_peak_id``.

Per peak the join yields one verdict:

- ``same_formula``            both commit the same neutral formula and adduct
- ``same_neutral_other_adduct``  same neutral, scored under different adducts
- ``same_ion_other_split``    the same ion read as different neutral/adduct
                              pairs (X.[M+NH4]+ against (X+NH3).[M+H]+) - no
                              spectrum can separate these, only chemistry can
- ``different_formula``       both commit, and the ions differ
- ``a_only_<b role>``         only engine A commits a main peak (M0); the
                              other engine's role for that peak follows
- ``b_only_<a role>``         the mirror image
- ``<a role>/<b role>``       neither commits an M0

Usage::

    export MASCOPE_URL=... MASCOPE_ACCESS_TOKEN=...
    python compare_runs.py --workspace "My workspace" --sample <id> [<id> ...]
    python compare_runs.py --workspace "My workspace" --batch "<batch name>" \\
        --dataset "<dataset>" --engine-a mascope --engine-b peaky --out compare/

Writes ``joined_<sample>.csv`` per sample, ``joined_all.csv``, ``summary.json``
(pooled and per-sample metrics) and ``summary.md`` (the same, as tables).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from mascope_sdk import MascopeClient


ROLE_MAIN = "M0"
RANK_BINS = [0, 10, 25, 50, 100, 200, 300, 1_000_000]
RANK_LABELS = ["1-10", "11-25", "26-50", "51-100", "101-200", "201-300", "301+"]

_ELEMENT = re.compile(r"([A-Z][a-z]?)(\d*)")


def element_counts(formula) -> dict[str, int]:
    """Element counts of a formula string; isotope markers fold into the element."""
    if not isinstance(formula, str) or not formula or formula in ("---", "()"):
        return {}
    counts: dict[str, int] = {}
    for element, n in _ELEMENT.findall(re.sub(r"[\[\]^]", "", formula)):
        counts[element] = counts.get(element, 0) + (int(n) if n else 1)
    return counts


def canonical(formula) -> str | None:
    """Order-independent key for a formula, or None for no formula."""
    counts = element_counts(formula)
    if not counts:
        return None
    return "".join(f"{element}{counts[element]}" for element in sorted(counts))


def ring_double_bond_equivalents(counts: dict[str, int]) -> float | None:
    if not counts or counts.get("C", 0) == 0:
        return None
    tetravalent = counts.get("C", 0) + counts.get("Si", 0)
    monovalent = sum(counts.get(e, 0) for e in ("H", "F", "Cl", "Br", "I"))
    trivalent = counts.get("N", 0) + counts.get("P", 0)
    return tetravalent - monovalent / 2 + trivalent / 2 + 1


def latest_completed_run(runs: pd.DataFrame | None, engine: str) -> str | None:
    """The newest completed run id of ``engine``, or None. Runs list newest first."""
    if runs is None or runs.empty:
        return None
    mine = runs[(runs["engine"] == engine) & (runs["status"] == "completed")]
    if mine.empty:
        return None
    return str(mine.iloc[0]["peak_assignment_run_id"])


def previous_completed_run(runs: pd.DataFrame | None, engine: str) -> str | None:
    """The completed run of ``engine`` before its newest one, or None.

    What one engine's own last two runs did differently, which no
    engine-against-engine number can show. A step that moves peaks between roles
    can leave the totals almost unchanged - some peaks gaining an M0 while
    others lose one - and then a regression is invisible in the role counts.
    """
    if runs is None or runs.empty:
        return None
    mine = runs[(runs["engine"] == engine) & (runs["status"] == "completed")]
    if len(mine) < 2:
        return None
    return str(mine.iloc[1]["peak_assignment_run_id"])


#: What a run's config calls the record of how much of its spectrum the
#: untargeted stage was offered. Absent on a run written before step 1.6, and on
#: an imported run, whose config is the other engine's own.
SEARCH_SCOPE_KEY = "search_scope"

#: The peak cap that applied before it became "every peak". Used to reconstruct
#: the searched set of a run predating the scope record, so a before/after
#: comparison of G5 is possible at all.
LEGACY_MAX_UNTARGETED_PEAKS = 300


def run_config(runs: pd.DataFrame | None, run_id: str | None) -> dict:
    """The stored config of one run, or an empty dict."""
    if runs is None or runs.empty or run_id is None:
        return {}
    row = runs[runs["peak_assignment_run_id"] == run_id]
    if row.empty:
        return {}
    config = row.iloc[0].get("config")
    return config if isinstance(config, dict) else {}


def unsearched_peaks(ledger: pd.DataFrame, config: dict) -> set[str]:
    """The peaks this run's untargeted stage was never offered (gate metric G5).

    A blank ledger row is two different results - searched and unexplained, or
    never looked at - and only the run's config separates them. The searched set
    is reconstructed rather than recorded: the stage takes the most intense of
    the peaks left after the pre-passes and the database stage, up to its cap, so
    ranking that remainder by intensity reproduces exactly which peaks it saw.

    Peaks a pre-pass or the database stage explained are not counted: something
    looked at them and answered. Peaks below the run's intensity threshold are,
    because the threshold is a choice about what to search.

    :param ledger: The run's ledger.
    :param config: The run's stored config.
    :return: The ``sample_peak_id`` of every peak the stage never saw.
    """
    if not config.get("run_untargeted", True):
        return set(ledger["sample_peak_id"].astype(str))
    role = ledger["role"].fillna("unassigned")
    source = (
        ledger["source"]
        if "source" in ledger.columns
        else pd.Series(pd.NA, index=ledger.index)
    )
    # What Stage B was offered to choose from, in the state it was offered in.
    remainder = ledger[
        ~role.isin(["reagent", "artifact"]) & (source.fillna("") != "database")
    ]
    threshold = float(config.get("peak_intensity_threshold") or 0.0)
    intensity = remainder["sample_peak_intensity"].astype(float)
    eligible = remainder[(intensity >= threshold) & (intensity > 0)]
    below = set(remainder.loc[~remainder.index.isin(eligible.index), "sample_peak_id"])

    scope = config.get(SEARCH_SCOPE_KEY) or {}
    limit = scope.get("searched_peaks")
    if limit is None:
        limit = config.get("max_untargeted_peaks") or LEGACY_MAX_UNTARGETED_PEAKS
    searched = eligible.nlargest(int(limit), "sample_peak_intensity")
    past_cap = set(eligible.loc[~eligible.index.isin(searched.index), "sample_peak_id"])
    return {str(peak) for peak in below | past_cap}


def role_transitions(before: pd.DataFrame, after: pd.DataFrame) -> dict:
    """How one engine's peaks changed role between two of its own runs.

    :param before: The earlier run's ledger.
    :param after: The later run's ledger.
    :return: Every role pair that changed, counted, plus the two the gate reads:
        peaks that carried a committed analyte and now carry nothing, and the
        reverse.
    """
    pair = (
        before[["sample_peak_id", "role", "tier"]]
        .astype({"sample_peak_id": str})
        .rename(columns={"role": "was", "tier": "was_tier"})
        .merge(
            after[["sample_peak_id", "role"]]
            .astype({"sample_peak_id": str})
            .rename(columns={"role": "now"}),
            on="sample_peak_id",
            how="inner",
        )
    )
    moved = pair[pair.was != pair.now]
    lost = moved[moved.was.eq(ROLE_MAIN) & moved.now.eq("unassigned")]
    return {
        "peaks": int(len(pair)),
        "changed": int(len(moved)),
        "m0_to_unassigned": int(len(lost)),
        "m0_to_unassigned_assigned_tier": int(lost.was_tier.eq("assigned").sum()),
        "unassigned_to_m0": int(
            (moved.was.eq("unassigned") & moved.now.eq(ROLE_MAIN)).sum()
        ),
        "by_pair": {
            f"{was} -> {now}": int(count)
            for (was, now), count in moved.groupby(["was", "now"]).size().items()
        },
    }


#: Nominal masses for the nitrogen rule. Anything outside the table makes a
#: formula unjudged rather than judged wrong.
NOMINAL_MASS = {
    "C": 12, "H": 1, "N": 14, "O": 16, "S": 32, "P": 31, "F": 19, "Cl": 35,
    "Br": 79, "I": 127, "Si": 28, "B": 11, "Na": 23, "K": 39, "Se": 80, "As": 75,
}  # fmt: skip


def odd_electron_neutral(formula) -> bool | None:
    """Whether a committed neutral formula breaks the nitrogen rule.

    A closed-shell neutral has an odd nominal mass exactly when it carries an odd
    number of nitrogen-like (trivalent) atoms; a formula that breaks that parity
    is an odd-electron species. Under an even-electron ionization - a proton, an
    ammonium, a bromide or a nitrate on a molecule - such a neutral is a radical
    the source did not make, and decision 9 keeps it as a tie-break rather than a
    filter, so the share of them is the number step 2.1 has to move. None when
    there is no formula or an element the table does not know.
    """
    if not isinstance(formula, str) or not formula:
        return None
    counts: dict[str, int] = {}
    for element, n in re.findall(
        r"([A-Z][a-z]?)(\d*)", re.sub(r"\[\d+|\]|\^", "", formula)
    ):
        if element not in NOMINAL_MASS:
            return None
        counts[element] = counts.get(element, 0) + (int(n) if n else 1)
    if not counts:
        return None
    mass = sum(NOMINAL_MASS[element] * n for element, n in counts.items())
    trivalent = (
        counts.get("N", 0)
        + counts.get("P", 0)
        + counts.get("As", 0)
        + counts.get("B", 0)
    )
    return (mass % 2) != (trivalent % 2)


def prepare(ledger: pd.DataFrame, prefix: str, notation_by_id: dict) -> pd.DataFrame:
    """Reduce one ledger to the prefixed columns the join needs."""
    # An isotopologue row names the M0 it belongs to, so a child with no owner
    # is a ledger inconsistency rather than a verdict: counted per engine as
    # step 1.5's coherence check, which the stage 1 gate wants at zero.
    owner = (
        ledger["owner_peak_assignment_id"]
        if "owner_peak_assignment_id" in ledger.columns
        else pd.Series(pd.NA, index=ledger.index)
    )
    out = pd.DataFrame(
        {
            "sample_peak_id": ledger["sample_peak_id"].astype(str),
            "mz": ledger["sample_peak_mz"].astype(float),
            "intensity": ledger["sample_peak_intensity"].astype(float),
            f"{prefix}_role": ledger["role"].fillna("unassigned"),
            f"{prefix}_tier": ledger["tier"].fillna("unassigned"),
            f"{prefix}_engine_tier": ledger.get("engine_tier"),
            f"{prefix}_source": ledger.get("source"),
            f"{prefix}_formula": ledger["assigned_formula"].map(canonical),
            f"{prefix}_ion": ledger["ion_formula"].map(canonical),
            f"{prefix}_adduct": ledger["ionization_mechanism_id"].map(notation_by_id),
            f"{prefix}_isotope": ledger.get("isotope_label"),
            f"{prefix}_fit": ledger.get("fit_score"),
            f"{prefix}_ppm": ledger.get("mz_error_ppm"),
            f"{prefix}_evidence": ledger.get("evidence"),
            f"{prefix}_ownerless": (ledger["role"] == "iso_child") & owner.isna(),
            f"{prefix}_odd_electron": ledger["assigned_formula"].map(
                odd_electron_neutral
            ),
        }
    )
    return out


def verdict(row) -> str:
    if row.a_role == ROLE_MAIN and row.b_role == ROLE_MAIN:
        if row.a_formula == row.b_formula:
            if (
                row.a_adduct == row.b_adduct
                or row.a_adduct is None
                or row.b_adduct is None
            ):
                return "same_formula"
            return "same_neutral_other_adduct"
        if row.a_ion is not None and row.a_ion == row.b_ion:
            return "same_ion_other_split"
        return "different_formula"
    if row.a_role == ROLE_MAIN:
        return f"a_only_{row.b_role}"
    if row.b_role == ROLE_MAIN:
        return f"b_only_{row.a_role}"
    return f"{row.a_role}/{row.b_role}"


def join_ledgers(a: pd.DataFrame, b: pd.DataFrame) -> pd.DataFrame:
    joined = a.merge(
        b.drop(columns=["mz", "intensity"]), on="sample_peak_id", how="outer"
    )
    missing = joined["mz"].isna()
    if missing.any():
        joined.loc[missing, "mz"] = (
            b.set_index("sample_peak_id")
            .loc[joined.loc[missing, "sample_peak_id"], "mz"]
            .to_numpy()
        )
        joined.loc[missing, "intensity"] = (
            b.set_index("sample_peak_id")
            .loc[joined.loc[missing, "sample_peak_id"], "intensity"]
            .to_numpy()
        )
    for prefix in ("a", "b"):
        joined[f"{prefix}_role"] = joined[f"{prefix}_role"].fillna("missing")
        joined[f"{prefix}_tier"] = joined[f"{prefix}_tier"].fillna("missing")
    joined = joined.sort_values("intensity", ascending=False).reset_index(drop=True)
    joined["rank"] = np.arange(1, len(joined) + 1)
    joined["verdict"] = joined.apply(verdict, axis=1)
    for prefix in ("a", "b"):
        counts = joined[f"{prefix}_formula"].map(element_counts)
        for element in ("C", "H", "N", "O"):
            joined[f"{prefix}_{element}"] = counts.map(lambda c, e=element: c.get(e, 0))
        joined[f"{prefix}_rdbe"] = counts.map(ring_double_bond_equivalents)
    return joined


def pct(numerator, denominator) -> float | None:
    return (
        round(100.0 * float(numerator) / float(denominator), 1) if denominator else None
    )


def chemistry(frame: pd.DataFrame, prefix: str) -> dict:
    """How many committed formulas fall outside what ambient organic chemistry produces."""
    d = frame[frame[f"{prefix}_C"] > 0]
    n = len(d)
    if n == 0:
        return {"n": 0}
    h_c = d[f"{prefix}_H"] / d[f"{prefix}_C"]
    o_c = d[f"{prefix}_O"] / d[f"{prefix}_C"]
    n_c = d[f"{prefix}_N"] / d[f"{prefix}_C"]
    return {
        "n": n,
        "carbon_free_formulas": int(
            (frame[f"{prefix}_C"] == 0).sum() - frame[f"{prefix}_formula"].isna().sum()
        ),
        "N_ge_5_pct": pct((d[f"{prefix}_N"] >= 5).sum(), n),
        "N_over_C_gt_0.6_pct": pct((n_c > 0.6).sum(), n),
        "H_over_C_lt_0.5_pct": pct((h_c < 0.5).sum(), n),
        "O_over_C_gt_1.5_pct": pct((o_c > 1.5).sum(), n),
        "rdbe_gt_15_pct": pct((d[f"{prefix}_rdbe"] > 15).sum(), n),
    }


def mass_error(series: pd.Series) -> dict | None:
    s = pd.to_numeric(series, errors="coerce").dropna()
    if s.empty:
        return None
    return {
        "n": int(len(s)),
        "median_ppm": round(float(s.median()), 3),
        "mad_ppm": round(float((s - s.median()).abs().median()), 3),
        "abs_gt_1_pct": pct((s.abs() > 1).sum(), len(s)),
        "abs_gt_2_pct": pct((s.abs() > 2).sum(), len(s)),
    }


def summarize(j: pd.DataFrame, engine_a: str, engine_b: str) -> dict:
    total_intensity = float(j["intensity"].sum())
    a_main = j[j.a_role == ROLE_MAIN]
    b_main = j[j.b_role == ROLE_MAIN]
    both = j[(j.a_role == ROLE_MAIN) & (j.b_role == ROLE_MAIN)]
    out: dict = {
        "engines": {"a": engine_a, "b": engine_b},
        "peaks": int(len(j)),
        "roles": {
            "a": j["a_role"].value_counts().to_dict(),
            "b": j["b_role"].value_counts().to_dict(),
        },
        "tiers_of_main_peaks": {
            "a": a_main["a_tier"].value_counts().to_dict(),
            "b": b_main["b_tier"].value_counts().to_dict(),
            "b_engine_tier": b_main["b_engine_tier"]
            .value_counts(dropna=False)
            .to_dict(),
        },
        # Two shares per engine, and reading them together is the point. The
        # analyte share counts only what the engine calls sample chemistry, so
        # it FALLS when reagent ions stop being read as analytes - which is an
        # improvement, not a regression. The second share is what the engine
        # accounts for at all, analyte or source background, and that is the one
        # to compare across engines.
        "signal_explained_pct": {
            "a_analyte": pct(
                j[j.a_role.isin([ROLE_MAIN, "iso_child"])]["intensity"].sum(),
                total_intensity,
            ),
            "a_analyte_reagent_artifact": pct(
                j[j.a_role.isin([ROLE_MAIN, "iso_child", "reagent", "artifact"])][
                    "intensity"
                ].sum(),
                total_intensity,
            ),
            "b_analyte": pct(
                j[j.b_role.isin([ROLE_MAIN, "iso_child"])]["intensity"].sum(),
                total_intensity,
            ),
            "b_analyte_reagent_artifact": pct(
                j[j.b_role.isin([ROLE_MAIN, "iso_child", "reagent", "artifact"])][
                    "intensity"
                ].sum(),
                total_intensity,
            ),
        },
        # G4: the reagent agreement. Of the peaks engine B calls reagent - the
        # source's own cluster ions, which are the brightest in the spectrum -
        # how many does engine A call reagent too, and how many does it still
        # commit an analyte M0 on? The second number is the one that matters:
        # an analyte fitted to a reagent cluster is a phantom, and a confident
        # one is a phantom presented as a result.
        "reagent_agreement": {
            "b_reagent": int((j.b_role == "reagent").sum()),
            "a_reagent_too": int(
                ((j.b_role == "reagent") & (j.a_role == "reagent")).sum()
            ),
            "a_reagent_too_pct": pct(
                ((j.b_role == "reagent") & (j.a_role == "reagent")).sum(),
                (j.b_role == "reagent").sum(),
            ),
            "a_claims_analyte": int(
                ((j.b_role == "reagent") & (j.a_role == ROLE_MAIN)).sum()
            ),
            "a_claims_analyte_assigned": int(
                (
                    (j.b_role == "reagent")
                    & (j.a_role == ROLE_MAIN)
                    & (j.a_tier == "assigned")
                ).sum()
            ),
            "a_reagent": int((j.a_role == "reagent").sum()),
            # What engine B makes of the peaks A calls reagent, so a claim A
            # makes on its own is visible rather than only its agreement.
            "b_role_where_a_reagent": j[j.a_role == "reagent"]["b_role"]
            .value_counts()
            .to_dict(),
        },
        # Step 1.5's coherence count. The untargeted figure is the gate's: the
        # stage that claims satellites must not leave a child behind. A row
        # of the same shape from Stage A - two curated targets sharing a peak,
        # the loser's children staying - is reported inside the total.
        "ownerless_iso_child": {
            "a": int(j["a_ownerless"].eq(True).sum()),
            "a_untargeted": int(
                (j["a_ownerless"].eq(True) & (j["a_source"] == "untargeted")).sum()
            ),
            "b": int(j["b_ownerless"].eq(True).sum()),
        },
        # G5. A peak the reference commits an analyte on that engine A's
        # untargeted stage was never offered - past its peak cap, or under its
        # intensity threshold. Not a disagreement: engine A did not look. The
        # stage-1 target is zero, because a cap is a bound on the answer and the
        # ledger cannot tell a reader which blank rows are one.
        "reference_main_unsearched": int(
            (j.b_role.eq(ROLE_MAIN) & j.get("a_unsearched", False)).sum()
        ),
        "reference_main_assigned_unsearched": int(
            (
                j.b_role.eq(ROLE_MAIN)
                & j.b_tier.eq("assigned")
                & j.get("a_unsearched", False)
            ).sum()
        ),
        "unsearched": int(j.get("a_unsearched", pd.Series(dtype=bool)).sum()),
        # G6. A peak one engine commits an analyte M0 on and the other reads as
        # part of another ion's envelope. Only one of the two can be right, and
        # the M0 is the expensive way to be wrong: it puts a formula, a tier and
        # a vote on a peak that carries no new species. Counted both ways, so a
        # move is visible as a move rather than as one engine's number falling.
        "m0_on_the_others_isotopologue": {
            "a": int((j.a_role.eq(ROLE_MAIN) & j.b_role.eq("iso_child")).sum()),
            "a_assigned": int(
                (
                    j.a_role.eq(ROLE_MAIN)
                    & j.b_role.eq("iso_child")
                    & j.a_tier.eq("assigned")
                ).sum()
            ),
            "b": int((j.b_role.eq(ROLE_MAIN) & j.a_role.eq("iso_child")).sum()),
        },
        # The share of committed formulas that are odd-electron neutrals. Decision
        # 9 keeps the radical reading as a tie-break, so this is not a gate; it is
        # the number step 2.1's fit has to move, recorded per engine so the move
        # is visible. The untargeted figure is the one the plan quotes.
        "odd_electron_m0": {
            "a": int((j.a_role.eq(ROLE_MAIN) & j.a_odd_electron.eq(True)).sum()),
            "a_pct": pct(
                (j.a_role.eq(ROLE_MAIN) & j.a_odd_electron.eq(True)).sum(),
                (j.a_role.eq(ROLE_MAIN) & j.a_odd_electron.notna()).sum(),
            ),
            "a_untargeted_pct": pct(
                (
                    j.a_role.eq(ROLE_MAIN)
                    & j.a_odd_electron.eq(True)
                    & j.a_source.eq("untargeted")
                ).sum(),
                (
                    j.a_role.eq(ROLE_MAIN)
                    & j.a_odd_electron.notna()
                    & j.a_source.eq("untargeted")
                ).sum(),
            ),
            "b": int((j.b_role.eq(ROLE_MAIN) & j.b_odd_electron.eq(True)).sum()),
            "b_pct": pct(
                (j.b_role.eq(ROLE_MAIN) & j.b_odd_electron.eq(True)).sum(),
                (j.b_role.eq(ROLE_MAIN) & j.b_odd_electron.notna()).sum(),
            ),
        },
        "both_main": int(len(both)),
        "verdicts_where_both_main": both["verdict"].value_counts().to_dict(),
        "a_main_by_verdict": a_main["verdict"].value_counts().to_dict(),
        "b_main_by_verdict": b_main["verdict"].value_counts().to_dict(),
        "a_assigned_tier_not_confirmed_by_b_pct": pct(
            ((a_main.a_tier == "assigned") & (a_main.verdict != "same_formula")).sum(),
            (a_main.a_tier == "assigned").sum(),
        ),
        "b_assigned_tier_not_confirmed_by_a_pct": pct(
            ((b_main.b_tier == "assigned") & (b_main.verdict != "same_formula")).sum(),
            (b_main.b_tier == "assigned").sum(),
        ),
        "agreement_by_b_engine_tier": {
            str(tier): {
                "n": int(len(group)),
                "same_formula_pct": pct(
                    (group.verdict == "same_formula").sum(), len(group)
                ),
                "same_ion_pct": pct(
                    group.verdict.isin(["same_formula", "same_ion_other_split"]).sum(),
                    len(group),
                ),
                "a_unassigned_pct": pct(
                    (group.a_role == "unassigned").sum(), len(group)
                ),
            }
            for tier, group in b_main.groupby(b_main["b_engine_tier"].fillna("none"))
        },
        "mass_error": {
            "a": mass_error(a_main["a_ppm"]),
            "b": mass_error(b_main["b_ppm"]),
        },
        "chemistry_of_main_peaks": {
            "a": chemistry(a_main, "a"),
            "b": chemistry(b_main, "b"),
        },
        "adducts_of_main_peaks": {
            "a": a_main["a_adduct"].value_counts(dropna=False).to_dict(),
            "b": b_main["b_adduct"].value_counts(dropna=False).to_dict(),
        },
    }
    bins = pd.cut(j["rank"], bins=RANK_BINS, labels=RANK_LABELS)
    rows = []
    for label, group in j.groupby(bins, observed=True):
        pair = group[(group.a_role == ROLE_MAIN) & (group.b_role == ROLE_MAIN)]
        rows.append(
            {
                "rank": str(label),
                "peaks": int(len(group)),
                "a_main_pct": pct((group.a_role == ROLE_MAIN).sum(), len(group)),
                "a_assigned_tier_pct": pct(
                    ((group.a_role == ROLE_MAIN) & (group.a_tier == "assigned")).sum(),
                    len(group),
                ),
                "b_main_pct": pct((group.b_role == ROLE_MAIN).sum(), len(group)),
                "b_assigned_tier_pct": pct(
                    ((group.b_role == ROLE_MAIN) & (group.b_tier == "assigned")).sum(),
                    len(group),
                ),
                "b_unassigned_pct": pct(
                    (group.b_role == "unassigned").sum(), len(group)
                ),
                "both_main": int(len(pair)),
                "same_formula_pct_of_both": pct(
                    (pair.verdict == "same_formula").sum(), len(pair)
                ),
                "same_ion_pct_of_both": pct(
                    pair.verdict.isin(["same_formula", "same_ion_other_split"]).sum(),
                    len(pair),
                ),
            }
        )
    out["by_intensity_rank"] = rows
    return out


def markdown_summary(result: dict) -> str:
    pooled = result["pooled"]
    a, b = pooled["engines"]["a"], pooled["engines"]["b"]
    lines = [
        f"# {a} vs {b}: {pooled['peaks']} peaks over {len(result['per_sample'])} sample(s)",
        "",
        "| metric | " + a + " | " + b + " |",
        "|---|---|---|",
        f"| main peaks (M0) | {pooled['roles']['a'].get('M0', 0)} | {pooled['roles']['b'].get('M0', 0)} |",
        f"| tiered assigned | {pooled['tiers_of_main_peaks']['a'].get('assigned', 0)} | {pooled['tiers_of_main_peaks']['b'].get('assigned', 0)} |",
        f"| unassigned peaks | {pooled['roles']['a'].get('unassigned', 0)} | {pooled['roles']['b'].get('unassigned', 0)} |",
        f"| analyte signal explained | {pooled['signal_explained_pct']['a_analyte']}% | {pooled['signal_explained_pct']['b_analyte']}% |",
        f"| signal accounted for (incl. reagent/artifact) | {pooled['signal_explained_pct']['a_analyte_reagent_artifact']}% | {pooled['signal_explained_pct']['b_analyte_reagent_artifact']}% |",
        f"| assigned-tier rows the other engine does not confirm | {pooled['a_assigned_tier_not_confirmed_by_b_pct']}% | {pooled['b_assigned_tier_not_confirmed_by_a_pct']}% |",
        f"| reagent peaks | {pooled['roles']['a'].get('reagent', 0)} | {pooled['roles']['b'].get('reagent', 0)} |",
        f"| artifact peaks | {pooled['roles']['a'].get('artifact', 0)} | {pooled['roles']['b'].get('artifact', 0)} |",
        f"| isotopologue rows without an owner (of them untargeted) | {pooled['ownerless_iso_child']['a']} ({pooled['ownerless_iso_child']['a_untargeted']}) | {pooled['ownerless_iso_child']['b']} |",
        f"| M0 on a peak the other engine calls an isotopologue (of them assigned-tier) | {pooled['m0_on_the_others_isotopologue']['a']} ({pooled['m0_on_the_others_isotopologue']['a_assigned']}) | {pooled['m0_on_the_others_isotopologue']['b']} |",
        f"| peaks the untargeted stage never searched | {pooled['unsearched']} | - |",
        f"| of them, ones the other engine commits an analyte on (assigned-tier) | {pooled['reference_main_unsearched']} ({pooled['reference_main_assigned_unsearched']}) | - |",
        f"| committed formulas that are odd-electron neutrals (untargeted share) | {pooled['odd_electron_m0']['a']} ({pooled['odd_electron_m0']['a_pct']}%, untargeted {pooled['odd_electron_m0']['a_untargeted_pct']}%) | {pooled['odd_electron_m0']['b']} ({pooled['odd_electron_m0']['b_pct']}%) |",
        "",
        f"Peaks both call M0: {pooled['both_main']} - "
        + ", ".join(f"{k} {v}" for k, v in pooled["verdicts_where_both_main"].items()),
        "",
        f"G4 reagent agreement: of {pooled['reagent_agreement']['b_reagent']} peaks "
        f"{b} calls reagent, {a} calls "
        f"{pooled['reagent_agreement']['a_reagent_too']} reagent too "
        f"({pooled['reagent_agreement']['a_reagent_too_pct']}%) and still commits "
        f"an analyte M0 on {pooled['reagent_agreement']['a_claims_analyte']} "
        f"({pooled['reagent_agreement']['a_claims_analyte_assigned']} of them at "
        f"assigned tier). {a} calls "
        f"{pooled['reagent_agreement']['a_reagent']} peaks reagent in total.",
        "",
        "| intensity rank | peaks | a M0 % | a assigned % | b M0 % | b assigned % | b unassigned % | both M0 | same formula % | same ion % |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in pooled["by_intensity_rank"]:
        lines.append(
            f"| {row['rank']} | {row['peaks']} | {row['a_main_pct']} | {row['a_assigned_tier_pct']} | "
            f"{row['b_main_pct']} | {row['b_assigned_tier_pct']} | {row['b_unassigned_pct']} | "
            f"{row['both_main']} | {row['same_formula_pct_of_both']} | {row['same_ion_pct_of_both']} |"
        )
    moved = result.get("since_previous_run")
    if moved:
        # What THIS engine's previous run made of the same peaks. Role totals
        # can hold still while peaks trade places underneath them, so a change
        # that loses committed analytes is invisible without this.
        lines += [
            "",
            f"Since {a}'s previous run on the same {moved['samples']} sample(s): "
            f"{moved['changed']} peaks changed role, of which "
            f"**{moved['m0_to_unassigned']} lost a committed analyte outright** "
            f"({moved['m0_to_unassigned_assigned_tier']} at assigned tier) and "
            f"{moved['unassigned_to_m0']} gained one.",
            "",
            f"Peaks the untargeted stage never searched: "
            f"{moved['unsearched_before']} then, {result['pooled']['unsearched']} now "
            f"({moved['reference_main_unsearched_before']} -> "
            f"{result['pooled']['reference_main_unsearched']} of them peaks the "
            f"other engine commits an analyte on).",
            "",
            "| role change | peaks |",
            "|---|---|",
        ] + [f"| {pair} | {count} |" for pair, count in moved["by_pair"].items()]
    return "\n".join(lines) + "\n"


def resolve_samples(client: MascopeClient, args) -> list[str]:
    if args.sample:
        return list(args.sample)
    listing = client.samples.list(batch=args.batch, dataset=args.dataset)
    if listing is None or listing.empty:
        sys.exit(f"no samples found for batch {args.batch!r}")
    return listing["sample_item_id"].astype(str).tolist()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--workspace", required=True, help="workspace name the samples live in"
    )
    parser.add_argument(
        "--sample", nargs="*", help="sample item ids (default: every sample of --batch)"
    )
    parser.add_argument("--batch", help="batch name, used when no --sample is given")
    parser.add_argument("--dataset", help="dataset name, to disambiguate --batch")
    parser.add_argument(
        "--engine-a",
        default="mascope",
        help="engine name of the first run (default: mascope)",
    )
    parser.add_argument(
        "--engine-b",
        default="peaky",
        help="engine name of the second run (default: peaky)",
    )
    parser.add_argument(
        "--out", default="assignment_compare_out", help="output directory"
    )
    args = parser.parse_args(argv)
    if not args.sample and not args.batch:
        parser.error("give --sample ids or a --batch name")

    client = MascopeClient(workspace=args.workspace)
    mechanisms = client.ionization.list()
    notation_by_id = (
        dict(
            zip(
                mechanisms["ionization_mechanism_id"],
                mechanisms["ionization_mechanism"],
            )
        )
        if mechanisms is not None
        else {}
    )
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    frames = []
    per_sample = {}
    for sample_id in resolve_samples(client, args):
        runs = client.peak_assignments.list_runs(sample_id)
        run_a = latest_completed_run(runs, args.engine_a)
        run_b = latest_completed_run(runs, args.engine_b)
        if run_a is None or run_b is None:
            print(
                f"{sample_id}: skipped (runs: {args.engine_a}={run_a}, {args.engine_b}={run_b})"
            )
            continue
        ledger_a = client.peak_assignments.get(sample_id, run_id=run_a)
        ledger_b = client.peak_assignments.get(sample_id, run_id=run_b)
        if ledger_a is None or ledger_b is None:
            print(f"{sample_id}: skipped (empty ledger)")
            continue
        joined = join_ledgers(
            prepare(ledger_a, "a", notation_by_id),
            prepare(ledger_b, "b", notation_by_id),
        )
        # Which peaks engine A's untargeted stage never saw, reconstructed from
        # its run config; the column travels with the join so the pooled frame
        # can count it too.
        joined["a_unsearched"] = joined["sample_peak_id"].isin(
            unsearched_peaks(ledger_a, run_config(runs, run_a))
        )
        joined.insert(0, "sample_item_id", sample_id)
        joined.insert(1, "run_a", run_a)
        joined.insert(2, "run_b", run_b)
        joined.to_csv(out_dir / f"joined_{sample_id}.csv", index=False)
        per_sample[sample_id] = summarize(joined, args.engine_a, args.engine_b)
        # What engine A's own previous run made of the same peaks. Reported
        # per sample rather than pooled: a step is deployed once, so the run
        # before it is the comparison, and "M0 -> unassigned" is the number a
        # change can hide behind unchanged role totals.
        previous_a = previous_completed_run(runs, args.engine_a)
        if previous_a is not None:
            ledger_previous = client.peak_assignments.get(sample_id, run_id=previous_a)
            if ledger_previous is not None:
                # G5 on the previous run as well, so the metric has a before to
                # be read against: the searched set is a property of the run's
                # config, and the run before a deployment is the one that had
                # the old one.
                unsearched_before = unsearched_peaks(
                    ledger_previous, run_config(runs, previous_a)
                )
                reference_main = set(
                    ledger_b.loc[
                        ledger_b["role"].fillna("unassigned") == ROLE_MAIN,
                        "sample_peak_id",
                    ].astype(str)
                )
                per_sample[sample_id]["since_previous_run"] = dict(
                    role_transitions(ledger_previous, ledger_a),
                    run=previous_a,
                    unsearched_before=len(unsearched_before),
                    reference_main_unsearched_before=len(
                        unsearched_before & reference_main
                    ),
                )
        frames.append(joined)
        print(
            f"{sample_id}: {len(joined)} peaks, both M0 {per_sample[sample_id]['both_main']}"
        )
    if not frames:
        sys.exit(
            "nothing to compare: no sample carries a completed run of both engines"
        )

    pooled = pd.concat(frames, ignore_index=True)
    pooled.to_csv(out_dir / "joined_all.csv", index=False)
    result = {
        "pooled": summarize(pooled, args.engine_a, args.engine_b),
        "per_sample": per_sample,
    }
    transitions = [
        one["since_previous_run"]
        for one in per_sample.values()
        if "since_previous_run" in one
    ]
    if transitions:
        pairs: dict[str, int] = {}
        for one in transitions:
            for key, count in one["by_pair"].items():
                pairs[key] = pairs.get(key, 0) + count
        result["since_previous_run"] = {
            "samples": len(transitions),
            "changed": sum(one["changed"] for one in transitions),
            "m0_to_unassigned": sum(one["m0_to_unassigned"] for one in transitions),
            "m0_to_unassigned_assigned_tier": sum(
                one["m0_to_unassigned_assigned_tier"] for one in transitions
            ),
            "unassigned_to_m0": sum(one["unassigned_to_m0"] for one in transitions),
            "unsearched_before": sum(
                one.get("unsearched_before", 0) for one in transitions
            ),
            "reference_main_unsearched_before": sum(
                one.get("reference_main_unsearched_before", 0) for one in transitions
            ),
            "by_pair": dict(sorted(pairs.items(), key=lambda kv: -kv[1])),
        }
    (out_dir / "summary.json").write_text(
        json.dumps(result, indent=2, default=_json_default), encoding="utf-8"
    )
    (out_dir / "summary.md").write_text(markdown_summary(result), encoding="utf-8")
    print(markdown_summary(result))
    return 0


def _json_default(value):
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    return str(value)


if __name__ == "__main__":
    sys.exit(main())
