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

With ``--decoy`` it reads the decoy runs of each sample instead: the in-app
engine re-run with the m/z axis shifted or the adducts swapped, scored at the
calibration of the run it is a decoy of (its control). Per arm it reports the
share of the control's assigned rows, and of their intensity, that the decoy
still holds at assigned, below and above m/z 350, and on the adduct arm how
many of those rows are new ions rather than another split of an ion the
control reads. Writes ``decoy.json`` and ``decoy.md``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

import numpy as np
import pandas as pd

from mascope_sdk import MascopeClient
from mascope_sdk.exceptions import MascopeError


# The card's metrics live beside this script, which is run by its path rather
# than installed, so its own folder is where they are found.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import metrics  # noqa: E402


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


def latest_completed_run(
    runs: pd.DataFrame | None, engine: str, before: pd.Timestamp | None = None
) -> str | None:
    """The newest completed run id of ``engine``, or None. Runs list newest first.

    With ``before``, the newest one created before that instant: a reference as it
    stood before it was re-published, which the store keeps beside the new one.
    """
    if runs is None or runs.empty:
        return None
    mine = runs[(runs["engine"] == engine) & (runs["status"] == "completed")]
    if before is not None:
        created = pd.to_datetime(
            mine["peak_assignment_run_utc_created"], utc=True, format="ISO8601"
        )
        mine = mine[created < before]
    if mine.empty:
        return None
    return str(mine.iloc[0]["peak_assignment_run_id"])


def utc_instant(value: str) -> pd.Timestamp:
    """An ISO timestamp as a UTC instant; one without an offset is read as UTC."""
    instant = pd.Timestamp(value)
    return (
        instant.tz_localize("UTC")
        if instant.tzinfo is None
        else instant.tz_convert("UTC")
    )


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
    ranking that remainder by intensity reproduces which peaks it saw. Where the
    run records that the stage was offered every eligible peak (its search
    scope is not ``limited``), none is past a cap, and a peak the stage
    committed a row on is one it saw.

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
    if scope.get("limited") is False:
        # The run records that the stage was offered every eligible peak. A
        # later pass can still move a peak out of the database stage's rows or
        # into them, so the ledger's remainder is not the set it was offered,
        # and ranking it would put the dimmest few past a cap there was none of.
        past_cap = set()
    # A peak the stage committed a row on is one it saw, whatever the ranking.
    committed = {
        str(peak)
        for peak in ledger.loc[source.fillna("") == "untargeted", "sample_peak_id"]
    }
    return {str(peak) for peak in below | past_cap} - committed


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
            # What the card's own-ledger metrics read (G7, G12): the row and
            # its owner, its mass z, the channels committing its neutral and
            # what a list calls it.
            f"{prefix}_assignment_id": ledger.get("peak_assignment_id"),
            f"{prefix}_owner_id": owner,
            f"{prefix}_mass_z": ledger.get("mass_z"),
            f"{prefix}_channels": ledger.get("corroboration_channels"),
            f"{prefix}_listing": ledger.get("reference_listing"),
        }
    )
    return out


#: The columns of a ledger :func:`prepare` cannot do without.
LEDGER_COLUMNS = (
    "sample_peak_id",
    "sample_peak_mz",
    "sample_peak_intensity",
    "role",
    "tier",
    "assigned_formula",
    "ion_formula",
    "ionization_mechanism_id",
)


def absent_side(prefix: str) -> pd.DataFrame:
    """A side with no rows, joined in for a sample the engine has no run of, so
    that sample's peaks all read ``missing`` on that side."""
    return prepare(pd.DataFrame(columns=list(LEDGER_COLUMNS)), prefix, {})


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
        # stage that claims isotopologues must not leave a child behind. A row
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


#: Below this m/z a 0.35 Da shift lands in the mass-defect gap no CHNOS formula
#: sits in, and above it the gap closes, so a decoy arm is read on either side.
#: The reference engine's own split (its ``scorecard.py``).
DECOY_MZ_SPLIT = 350.0
#: The width of the m/z bins a decoy arm is also reported in.
DECOY_BIN_DA = 50
#: The run config key under which a decoy run names the run it is a decoy of.
DECOY_ARM_KEY = "decoy_arm"
#: The arm the engine refuses with, in a refused decoy run's error.
DECOY_REFUSAL = "no decoy:"


def assigned_rows(ledger: pd.DataFrame) -> pd.DataFrame:
    """A ledger's monoisotopic rows at the ``assigned`` tier, with what a decoy
    count reads of them: the peak, its m/z and intensity, and the ion."""
    role = ledger["role"].fillna("unassigned")
    tier = ledger["tier"].fillna("unassigned")
    rows = ledger[(role == ROLE_MAIN) & (tier == "assigned")]
    return pd.DataFrame(
        {
            "sample_peak_id": rows["sample_peak_id"].astype(str),
            "mz": pd.to_numeric(rows["sample_peak_mz"], errors="coerce"),
            "intensity": pd.to_numeric(
                rows["sample_peak_intensity"], errors="coerce"
            ).fillna(0.0),
            "ion": rows["ion_formula"].map(canonical),
        }
    )


def assigned_counts(rows: pd.DataFrame) -> dict:
    """How many assigned rows, and how much intensity, either side of the split
    and per m/z bin (keyed by the bin's lower edge)."""
    below = rows["mz"] < DECOY_MZ_SPLIT
    above = rows["mz"] >= DECOY_MZ_SPLIT
    bins = (np.floor(rows["mz"].dropna() / DECOY_BIN_DA) * DECOY_BIN_DA).astype(int)
    return {
        "rows": int(len(rows)),
        "rows_lt_350": int(below.sum()),
        "rows_ge_350": int(above.sum()),
        "intensity": float(rows["intensity"].sum()),
        "intensity_lt_350": float(rows.loc[below, "intensity"].sum()),
        "intensity_ge_350": float(rows.loc[above, "intensity"].sum()),
        "by_bin": {str(edge): int(n) for edge, n in bins.value_counts().items()},
    }


def ion_split(decoy: pd.DataFrame, control_ledger: pd.DataFrame) -> dict:
    """The adduct arm's assigned rows at the ion level.

    A swapped adduct can read an ion the control already reads as another
    neutral: X through [M+Na]+ where the control had (X + Na - H) through
    [M+H]+. The ion is pinned by its mass and the split is not, so such a row
    is another split of an ion the run reads, not a new one. A row is a new ion
    where the control read no monoisotopic row of that ion on that peak, at any
    tier, or left the peak unexplained.

    :param decoy: The arm's assigned rows (:func:`assigned_rows`).
    :param control_ledger: The control's whole ledger.
    :return: The counts, the new ions below the split among them.
    """
    role = control_ledger["role"].fillna("unassigned")
    main = control_ledger[role == ROLE_MAIN]
    control_ions = set(
        zip(main["sample_peak_id"].astype(str), main["ion_formula"].map(canonical))
    )
    same = [
        ion is not None and (peak, ion) in control_ions
        for peak, ion in zip(decoy["sample_peak_id"], decoy["ion"])
    ]
    new = decoy[~np.asarray(same, dtype=bool)] if len(decoy) else decoy
    return {
        "same_ion": int(sum(same)),
        "new_ion": int(len(new)),
        "new_ion_lt_350": int((new["mz"] < DECOY_MZ_SPLIT).sum()),
    }


def on_control_reagent(decoy: pd.DataFrame, control_ledger: pd.DataFrame) -> dict:
    """The arm's assigned rows on peaks its control claimed as the source's own.

    A decoy recorded before its arm claimed the source's lines where its
    control did (no ``source_ions`` in its ``decoy_arm``) looked for them on
    the moved axis, and on a dalton shift claimed none, so the brightest ions
    of the spectrum were offered to the stages as any peak is. A row there is
    a false assignment like any other, and the brightest one there is: counted
    apart, so the intensity share is read knowing it.
    """
    role = control_ledger["role"].fillna("unassigned")
    reagent = set(control_ledger.loc[role == "reagent", "sample_peak_id"].astype(str))
    rows = decoy[decoy["sample_peak_id"].isin(reagent)]
    return {
        "on_control_reagent": int(len(rows)),
        "intensity_on_control_reagent": float(rows["intensity"].sum()),
    }


def decoy_rates(control: dict, decoy: dict) -> dict:
    """What share of its control's assigned rows and intensity an arm holds."""
    rates = {
        "held_pct": pct(decoy["rows"], control["rows"]),
        "held_lt_350_pct": pct(decoy["rows_lt_350"], control["rows_lt_350"]),
        "held_ge_350_pct": pct(decoy["rows_ge_350"], control["rows_ge_350"]),
        "held_intensity_pct": pct(decoy["intensity"], control["intensity"]),
        "held_intensity_lt_350_pct": pct(
            decoy["intensity_lt_350"], control["intensity_lt_350"]
        ),
        "held_intensity_ge_350_pct": pct(
            decoy["intensity_ge_350"], control["intensity_ge_350"]
        ),
    }
    if "new_ion" in decoy:
        rates["new_ion_pct"] = pct(decoy["new_ion"], control["rows"])
    return rates


def _added(total: dict, one: dict) -> dict:
    """Two arms' counts summed, bins and all."""
    out = dict(total)
    for key, value in one.items():
        if key == "by_bin":
            bins = dict(out.get("by_bin", {}))
            for edge, n in value.items():
                bins[edge] = bins.get(edge, 0) + n
            out["by_bin"] = bins
        elif isinstance(value, (int, float)):
            out[key] = out.get(key, 0) + value
    return out


def newest_decoys(runs: pd.DataFrame | None) -> dict[str, pd.Series]:
    """A sample's newest decoy run per arm: the newest completed one, else the
    newest of any status, so a refused or unfinished arm is still reported."""
    if runs is None or runs.empty or "decoy" not in runs.columns:
        return {}
    decoys = runs[runs["decoy"].notna()]
    newest: dict[str, pd.Series] = {}
    for arm, group in decoys.groupby("decoy", sort=True):
        completed = group[group["status"] == "completed"]
        newest[str(arm)] = (completed if not completed.empty else group).iloc[0]
    return newest


def decoy_report(client: MascopeClient, sample_ids: list[str]) -> dict:
    """Every sample's decoy arms against their controls, and the arms pooled.

    :param client: The SDK client.
    :param sample_ids: The samples of the set.
    :return: ``{"pooled": {arm: ...}, "per_sample": {sample: {arm: ...}},
        "skipped": {sample: {arm: why}}}``.
    """
    per_sample: dict[str, dict] = {}
    skipped: dict[str, dict] = {}
    pooled: dict[str, dict] = {}
    for sample_id in sample_ids:
        runs = client.peak_assignments.list_runs(sample_id, include_decoys=True)
        arms = newest_decoys(runs)
        if not arms:
            print(f"{sample_id}: skipped (no decoy run)")
            continue
        ledgers: dict[str, pd.DataFrame | None] = {}
        for arm, run in arms.items():
            if run["status"] != "completed":
                error = run.get("error")
                why = (
                    str(error)
                    if isinstance(error, str) and error.startswith(DECOY_REFUSAL)
                    else f"run {run['status']}"
                )
                skipped.setdefault(sample_id, {})[arm] = why
                continue
            config = run.get("config") if isinstance(run.get("config"), dict) else {}
            control_id = (config.get(DECOY_ARM_KEY) or {}).get("control_run_id")
            if control_id is None:
                skipped.setdefault(sample_id, {})[arm] = "names no control run"
                continue
            if control_id not in set(runs["peak_assignment_run_id"]):
                # Each arm has a retention quota of its own, so a decoy can
                # outlive the control it was scored against
                skipped.setdefault(sample_id, {})[arm] = "its control run is gone"
                continue
            for run_id in (control_id, run["peak_assignment_run_id"]):
                if run_id not in ledgers:
                    ledgers[run_id] = client.peak_assignments.get(
                        sample_id, run_id=run_id
                    )
            control_ledger = ledgers[control_id]
            decoy_ledger = ledgers[run["peak_assignment_run_id"]]
            if control_ledger is None or decoy_ledger is None:
                skipped.setdefault(sample_id, {})[arm] = "empty ledger"
                continue
            control = assigned_counts(assigned_rows(control_ledger))
            decoy_rows = assigned_rows(decoy_ledger)
            decoy = assigned_counts(decoy_rows)
            decoy.update(on_control_reagent(decoy_rows, control_ledger))
            if arm == "swap_adducts":
                decoy.update(ion_split(decoy_rows, control_ledger))
            per_sample.setdefault(sample_id, {})[arm] = {
                "run": run["peak_assignment_run_id"],
                "control_run": control_id,
                "control": control,
                "decoy": decoy,
                **decoy_rates(control, decoy),
            }
            arm_total = pooled.setdefault(
                arm, {"samples": 0, "control": {}, "decoy": {}}
            )
            arm_total["samples"] += 1
            arm_total["control"] = _added(arm_total["control"], control)
            arm_total["decoy"] = _added(arm_total["decoy"], decoy)
        print(
            f"{sample_id}: "
            + ", ".join(
                f"{arm} {one['held_pct']}%"
                for arm, one in per_sample.get(sample_id, {}).items()
            )
        )
    for total in pooled.values():
        total.update(decoy_rates(total["control"], total["decoy"]))
    return {
        "pooled": dict(sorted(pooled.items())),
        "per_sample": per_sample,
        "skipped": skipped,
    }


def decoy_markdown(result: dict) -> str:
    """The decoy report as the gate quotes it: one row per arm, pooled."""
    lines = [
        f"# Decoy arms over {len(result['per_sample'])} sample(s)",
        "",
        "Share of the control's assigned rows (and of their intensity) the arm "
        "still holds at assigned. No target yet (decision 31).",
        "",
        "| arm | samples | control assigned | arm assigned | held % | "
        f"held % < {DECOY_MZ_SPLIT:g} | held % >= {DECOY_MZ_SPLIT:g} | "
        "held intensity % | on the control's reagent | new ions (< 350) |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for arm, total in result["pooled"].items():
        decoy = total["decoy"]
        new = (
            f"{decoy['new_ion']} ({decoy['new_ion_lt_350']}), {total['new_ion_pct']}%"
            if "new_ion" in decoy
            else "-"
        )
        lines.append(
            f"| {arm} | {total['samples']} | {total['control']['rows']} | "
            f"{decoy['rows']} | {total['held_pct']} | {total['held_lt_350_pct']} | "
            f"{total['held_ge_350_pct']} | {total['held_intensity_pct']} | "
            f"{decoy['on_control_reagent']} | {new} |"
        )
    if result["skipped"]:
        lines += ["", "Arms not read:", ""]
        lines += [
            f"- `{sample}` {arm}: {why}"
            for sample, arms in result["skipped"].items()
            for arm, why in arms.items()
        ]
    return "\n".join(lines) + "\n"


# --- The gate card (assignment quality plan, step 6.4) ----------------------------

#: The card's JSON format; a baseline of another one is refused, not misread.
CARD_FORMAT = 1
#: The thresholds file's format.
THRESHOLDS_FORMAT = 1
#: What a set may be called on a card: an upper-case letter, then at most one
#: digit or lower-case letter, then at most a sign (C2, F1, Hr, I+). The card
#: names sets by their letter and nothing else, so a manifest that names one
#: otherwise is refused before anything is read.
SET_NAME = re.compile(r"^[A-Z][0-9a-z]?[+-]?$")
INSTRUMENTS = ("orbitrap", "tof")
#: The card's column over every set at once. Lower case, so no set is called so.
POOLED = "pooled"
#: How many detail reads run at once. Each committed row's provenance is one
#: read, and a dense set holds thousands of them.
DETAIL_WORKERS = 8
#: Shipped beside this script: the log's stage targets.
DEFAULT_THRESHOLDS = Path(__file__).resolve().parent / "thresholds.json"


class CardRefused(Exception):
    """The card's text holds a token the deny list names; nothing is written."""

    def __init__(self, token: str, where: str):
        super().__init__(f"{where} holds {token!r}, which the deny list names")
        self.token, self.where = token, where


def load_sets(path: str | Path) -> dict:
    """The sets a card reads, from a manifest.

    ``{"sets": {"A": {"workspace": ..., "samples": [...]}, "E": {"workspace":
    ..., "batch": ..., "dataset": ..., "instrument": "tof", "sulfur_free":
    true}}, "deny": [...]}``: per set its workspace, its samples or a batch (and
    dataset) whose samples it reads, its instrument class (``orbitrap``, the
    default, or ``tof``) and whether its chemistry holds no sulfur (G9); and
    any further names the card must not hold.

    :raises SystemExit: On a set named other than by a letter, or one the
        manifest does not say how to read.
    """
    manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    sets = manifest.get("sets")
    if not isinstance(sets, dict) or not sets:
        sys.exit(f"{path}: no sets")
    for name, spec in sets.items():
        if not SET_NAME.match(name):
            sys.exit(
                f"{path}: a set is named by a letter, then at most one digit or "
                f"lower-case letter, then at most a sign (A, C2, Hr, I+); "
                f"{name!r} is not one"
            )
        if not isinstance(spec, dict) or not spec.get("workspace"):
            sys.exit(f"{path}: set {name} names no workspace")
        if not spec.get("samples") and not spec.get("batch"):
            sys.exit(f"{path}: set {name} names no samples and no batch")
        spec.setdefault("instrument", "orbitrap")
        if spec["instrument"] not in INSTRUMENTS:
            sys.exit(f"{path}: set {name} has instrument {spec['instrument']!r}")
        spec.setdefault("sulfur_free", False)
    return manifest


#: Every word the card prints of its own. A word of a name that is one of these
#: tells a reader nothing when the card prints it, so the scrub does not refuse
#: the card for it alone; the name itself, whole, still refuses it. The JSON's
#: keys and the thresholds file it carries are the card's own too
#: (:func:`card_vocabulary`).
CARD_WORDS = frozenset(
    word.lower()
    for text in [label for _, label, _ in metrics.METRICS]
    + list(metrics.DECOY_LABELS.values())
    + [
        "gate card against sets set pooled metric value target a reading of",
        "stage pass fail missed met not read gated with reference run engine",
        "since the baseline moved was now tiering rule decoy samples orbitrap",
        "tof instrument mixed readings miss ok no sample on only",
    ]
    for word in re.split(r"[^A-Za-z0-9]+", text)
    if word
)


def _keys(value) -> set[str]:
    """Every key of a card's JSON, at any depth."""
    if isinstance(value, dict):
        return set(map(str, value)).union(*map(_keys, value.values()))
    if isinstance(value, list):
        return set().union(*map(_keys, value))
    return set()


def card_vocabulary(card: dict) -> set[str]:
    """The words a card's JSON prints of its own beyond :data:`CARD_WORDS`: its
    keys at any depth, and the thresholds file it carries, prose included."""
    texts = sorted(_keys(card))
    if card.get("thresholds"):
        texts.append(json.dumps(card["thresholds"]))
    return {
        word.lower()
        for text in texts
        for word in re.split(r"[^A-Za-z0-9]+", text)
        if word
    }


def _words(name: str, vocabulary: frozenset[str] = CARD_WORDS) -> list[str]:
    """The words of a name a card could carry alone: three characters or more,
    not a number, and not a word the card prints of its own."""
    return [
        word
        for word in re.split(r"[^A-Za-z0-9]+", name)
        if len(word) >= 3 and not word.isdigit() and word.lower() not in vocabulary
    ]


def deny_tokens(
    names, ids, set_names=(), *, strict=(), hosts=(), own_words=()
) -> list[str]:
    """What the card's text must not hold.

    :param names: Workspace, batch and dataset names: each whole, unless it is
        a number or made of the card's own words alone, and each word of it
        (:func:`_words`). A set's letter is the card's to print, so neither a
        name nor a word of one equal to it is a token.
    :param ids: Sample ids, whole.
    :param set_names: The sets' letters, which the card is meant to hold.
    :param strict: Names someone listed as ones the card must not hold, such as
        an instrument's: each whole whatever it is made of, and each word of it.
        A set named so is refused before the card is read
        (:func:`refuse_denied_sets`), never let through.
    :param hosts: The hosts (:func:`host_names`): each whole unless it is made
        of the card's own words alone, a number never being one, so an
        address is denied; and each word of it.
    :param own_words: Further words this card prints of its own: the engines'
        names, the JSON's keys, the thresholds file (:func:`card_vocabulary`).
    :return: The tokens, longest first, so a refusal names the most of a name.
    """
    vocabulary = CARD_WORDS | {word.lower() for word in own_words}
    tokens = set()
    for name in names:
        name = str(name or "").strip()
        parts = [part for part in re.split(r"[^A-Za-z0-9]+", name) if part]
        # A number, or a name made of the card's own words alone, names
        # nothing the card could leak.
        if len(name) >= 3 and any(
            not part.isdigit() and part.lower() not in vocabulary for part in parts
        ):
            tokens.add(name)
        tokens.update(_words(name, vocabulary))
    letters = {name.lower() for name in set_names}
    tokens = {token for token in tokens if token.lower() not in letters}
    for name in hosts:
        name = str(name or "").strip()
        parts = [part for part in re.split(r"[^A-Za-z0-9]+", name) if part]
        if any(part.isdigit() or part.lower() not in vocabulary for part in parts):
            tokens.add(name)
        tokens.update(_words(name, vocabulary))
    for name in strict:
        name = str(name or "").strip()
        if name:
            tokens.add(name)
        tokens.update(_words(name, vocabulary))
    tokens.update(str(one).strip() for one in ids if str(one or "").strip())
    return sorted(tokens, key=lambda token: (-len(token), token))


def load_deny(path: str | Path) -> list[str]:
    """The names a ``--deny`` file lists: one per line, ``#`` starting a comment.

    :raises SystemExit: On a file that cannot be read.
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as error:
        sys.exit(f"--deny {path}: {error.strerror or error}")
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def refuse_denied_sets(set_names, denied) -> None:
    """Refuse a set the deny list names, before anything is read: the card
    prints every set's name, and a name the list denies is never let through.

    :raises SystemExit: Naming the set.
    """
    named = {str(one).strip().lower() for one in denied}
    for name in set_names:
        if name.lower() in named:
            sys.exit(
                f"set {name}: the deny list names it, and the card prints it on "
                "every row; give the set another letter"
            )


def host_names(url: str | None) -> list[str]:
    """The hosts a card could name: the deployment's, and this machine's."""
    names = [socket.gethostname(), socket.getfqdn()]
    if url:
        host = urlparse(url if "//" in url else f"//{url}").hostname
        if host:
            names.append(host)
    return [name for name in names if name]


def scrub(texts: dict[str, str], tokens: list[str]) -> None:
    """Refuse a card whose text holds a denied token, before any of it is written.

    A token matches whole, in any case, where no letter or digit adjoins it.

    :param texts: Every file of the card, by name.
    :param tokens: The deny list (:func:`deny_tokens`).
    :raises CardRefused: Naming the first token found and the file holding it.
    """
    for where, text in texts.items():
        for token in tokens:
            pattern = r"(?<![A-Za-z0-9])" + re.escape(token) + r"(?![A-Za-z0-9])"
            if re.search(pattern, text, flags=re.IGNORECASE):
                raise CardRefused(token, where)


def load_thresholds(path: str | Path) -> dict:
    """The stage targets a gate marks the card against (``thresholds.json``).

    :raises SystemExit: On a file of another format, or one naming a stage it
        does not list.
    """
    thresholds = json.loads(Path(path).read_text(encoding="utf-8"))
    if thresholds.get("format") != THRESHOLDS_FORMAT:
        sys.exit(f"{path}: not a thresholds file of format {THRESHOLDS_FORMAT}")
    if thresholds.get("stage") not in thresholds.get("stages", []):
        sys.exit(f"{path}: stage {thresholds.get('stage')!r} is not one it lists")
    return thresholds


def target_for(
    thresholds: dict, key: str, set_name: str | None, instrument: str | None
) -> dict | None:
    """The target a set's reading is marked against at the file's stage, or None.

    A stage the metric's entry does not name holds the stage before it, as the
    log's "hold" does; one it names as null has no target. A set may carry an
    entry of its own per stage, which replaces the metric's for that stage.
    There is none for a set the file lists as not gated, nor where the entry
    names the sets it holds for (``only_sets``) or the instrument classes and
    the set is not among them. With no set, the metric's own target.

    :return: ``{"max" or "min": number, "reading": bool, "stage": name}``.
    """
    entry = (thresholds.get("targets") or {}).get(key)
    if not entry:
        return None
    if set_name is not None:
        if set_name in thresholds.get("not_gated_sets", []):
            return None
        only = entry.get("only_sets")
        if only and set_name not in only:
            return None
        instruments = entry.get("instruments")
        if instruments and instrument not in instruments:
            return None
    stages = thresholds["stages"]
    own = (entry.get("sets") or {}).get(set_name) or {}
    found, stage_of = None, None
    for stage in stages[: stages.index(thresholds["stage"]) + 1]:
        for source in (own, entry):
            if stage in source:
                found, stage_of = source[stage], stage
                break
    if not found:
        return None
    return {**found, "reading": bool(found.get("reading")), "stage": stage_of}


def meets(value, target: dict) -> bool:
    """Whether a value meets its target."""
    if "max" in target and value > target["max"]:
        return False
    return not ("min" in target and value < target["min"])


def judge(card: dict, thresholds: dict) -> dict:
    """Mark every set's readings against the stage the thresholds name.

    The pooled column is not marked: every target is stated per set, and a
    set with no reference run is not marked on the readings that read one. A
    target the file marks as a reading is marked and left out of the verdict
    (decision 27 makes G1 and G2 readings from stage 3 on). A gated reading
    with no value is not read, and is named rather than passed or failed; so
    is one the detail read feeds that meets its target on a set where some of
    those reads failed (:data:`metrics.DETAIL_METRICS`), since the rows left
    out could hold a miss. A set on which no sample was read at all fails the
    gate, unless nothing gates it: its readings are not read, and a set the
    bench could not read is not one that passed.

    :return: ``{"stage", "verdict", "met", "missed": [...], "not_read": [...],
        "unread_sets": [...], "marks": {set: {metric: "ok" | "miss" | "not
        read"}}, "met_by_set": {set: n}}``.
    """
    met, missed, not_read, marks, met_by_set = 0, [], [], {}, {}
    unread_sets = []
    for name, column in card["columns"].items():
        if name == POOLED:
            continue
        readings = column["readings"]
        unreferenced = (readings.get("reference_samples") or {}).get("value") == 0
        unread = (readings.get("samples") or {}).get("value") == 0
        failed = (readings.get("detail_failed") or {}).get("value")
        for key, one in readings.items():
            target = target_for(thresholds, key, name, column.get("instrument"))
            if target is None or (unreferenced and key in metrics.REFERENCE_METRICS):
                continue
            if unread:
                marks.setdefault(name, {})[key] = "not read"
                if not target["reading"] and name not in unread_sets:
                    unread_sets.append(name)
                continue
            value = one.get("value")
            if value is None:
                mark = "not read"
            else:
                mark = "ok" if meets(value, target) else "miss"
                if mark == "ok" and failed and key in metrics.DETAIL_METRICS:
                    mark = "not read"
            marks.setdefault(name, {})[key] = mark
            if target["reading"]:
                continue
            if mark == "ok":
                met += 1
                met_by_set[name] = met_by_set.get(name, 0) + 1
            elif mark == "miss":
                missed.append(
                    {"set": name, "metric": key, "value": value, "target": target}
                )
            else:
                not_read.append({"set": name, "metric": key})
    return {
        "stage": thresholds["stage"],
        "verdict": "fail" if missed or unread_sets else "pass",
        "met": met,
        "missed": missed,
        "not_read": not_read,
        "unread_sets": unread_sets,
        "marks": marks,
        "met_by_set": met_by_set,
    }


def for_set(gate: dict, name: str) -> dict:
    """The gate's verdict on one set alone."""
    missed = [one for one in gate["missed"] if one["set"] == name]
    unread = [one for one in gate["unread_sets"] if one == name]
    marks = gate["marks"].get(name)
    return {
        **gate,
        "verdict": "fail" if missed or unread else "pass",
        "met": gate["met_by_set"].get(name, 0),
        "missed": missed,
        "not_read": [one for one in gate["not_read"] if one["set"] == name],
        "unread_sets": unread,
        "marks": {name: marks} if marks else {},
        "met_by_set": {name: gate["met_by_set"][name]}
        if name in gate["met_by_set"]
        else {},
    }


def describe_target(target: dict | None) -> str:
    """A target as the card prints it."""
    if not target:
        return "-"
    bound = f"<= {target['max']:g}" if "max" in target else f">= {target['min']:g}"
    return f"{bound}, a reading" if target.get("reading") else bound


def unit_of(key: str) -> str:
    """A reading's unit: ``%``, ``count`` or ``ppm``."""
    if key.startswith("decoy "):
        return "count" if key.endswith(" on_control_reagent") else "%"
    return {key: unit for key, _, unit in metrics.METRICS}.get(key, "count")


def label_of(key: str) -> str:
    """A reading's label, as the card's row names it."""
    labels = {key: label for key, label, _ in metrics.METRICS}
    if key in labels:
        return labels[key]
    if key.startswith("decoy "):
        _, arm, name = key.split(" ", 2)
        return f"decoy {arm}: {metrics.DECOY_LABELS.get(name, name)}"
    return key


def format_value(value, unit: str) -> str:
    """A value as the card prints it: a count whole, a share as the log rounds
    it, a mass error to three decimals."""
    if value is None:
        return "-"
    if unit == "ppm":
        return f"{float(value):.3f}"
    if unit == "count":
        return f"{int(value)}"
    return f"{float(value):g}"


def format_reading(one: dict | None, unit: str) -> str:
    """A reading with the count it is over: ``6.2 of 999``."""
    if one is None:
        return "-"
    text = format_value(one.get("value"), unit)
    return f"{text} of {one['n']}" if one.get("n") is not None else text


def gate_line(gate: dict) -> str:
    """The pass-or-fail line: the verdict, the sets on which no sample was read,
    every reading that missed, and the gated readings that could not be read."""
    gated = gate["met"] + len(gate["missed"])
    line = f"GATE {gate['stage']}: {gate['verdict'].upper()} -"
    if gate["unread_sets"]:
        line += f" no sample read on {', '.join(gate['unread_sets'])};"
    if gate["missed"]:
        line += (
            f" {len(gate['missed'])} of {gated} gated readings missed: "
            + "; ".join(
                f"{one['set']} {one['metric']} "
                f"{format_value(one['value'], unit_of(one['metric']))} "
                f"({describe_target(one['target'])})"
                for one in gate["missed"]
            )
        )
    else:
        line += f" {gated} gated readings met"
    if gate["not_read"]:
        line += (
            f"; {len(gate['not_read'])} not read ("
            + ", ".join(f"{one['set']} {one['metric']}" for one in gate["not_read"])
            + ")"
        )
    return line


def load_baseline(path: str | Path) -> dict:
    """A previous card, read before anything else is.

    :raises SystemExit: On a file that is not a card of this format.
    """
    try:
        baseline = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        sys.exit(f"--baseline {path}: {error}")
    if not isinstance(baseline, dict) or baseline.get("format") != CARD_FORMAT:
        sys.exit(f"--baseline {path}: not a card of format {CARD_FORMAT}")
    return baseline


def diff_cards(baseline: dict, card: dict) -> dict:
    """Every reading that moved since a previous card, in the columns both hold.

    A reading moved where its value or its count differs, or where one of the
    two cards' column does not hold it. A column only one card holds is named
    once, not reading by reading: a set's own card, given as the baseline of a
    whole one, holds none of the other sets and no pooled column.

    :return: ``{"compared": n, "by_column": {column: n}, "moved": [{"set",
        "metric", "was", "now"}], "only_baseline": [...], "only_card":
        [...]}``.
    :raises SystemExit: On a baseline that is not a card of this format.
    """
    if baseline.get("format") != CARD_FORMAT:
        sys.exit(f"the baseline is not a card of format {CARD_FORMAT}")
    moved, by_column = [], {}
    old_columns, new_columns = baseline.get("columns", {}), card["columns"]
    for name in new_columns:
        if name not in old_columns:
            continue
        old = old_columns[name].get("readings", {})
        new = new_columns[name].get("readings", {})
        keys = list(new) + [k for k in old if k not in new]
        by_column[name] = len(keys)
        for key in keys:
            was, now = old.get(key), new.get(key)
            if was != now:
                moved.append({"set": name, "metric": key, "was": was, "now": now})
    return {
        "compared": sum(by_column.values()),
        "by_column": by_column,
        "moved": moved,
        "only_baseline": [name for name in old_columns if name not in new_columns],
        "only_card": [name for name in new_columns if name not in old_columns],
    }


def baseline_line(diff: dict) -> str:
    """What moved since the baseline, in one line."""
    line = f"{len(diff['moved'])} of {diff['compared']} readings moved"
    if diff["only_card"]:
        line += f"; not on the baseline: {', '.join(diff['only_card'])}"
    if diff["only_baseline"]:
        line += f"; on the baseline only: {', '.join(diff['only_baseline'])}"
    return line


def _context(name: str, column: dict) -> str:
    parts = [column.get("instrument") or "-"]
    if (column["readings"].get("samples") or {}).get("value") == 0:
        parts.append("no sample read")
    for side, values in (column.get("engine_versions") or {}).items():
        if values:
            parts.append(f"engine {side} {', '.join(values)}")
    if column.get("tiering_rules"):
        rules = ", ".join(str(one) for one in column["tiering_rules"])
        parts.append(f"tiering rule set {rules}")
    for arm, count in sorted((column.get("decoys_not_read") or {}).items()):
        parts.append(f"decoy {arm} not read on {count}")
    return f"- {name}: " + "; ".join(parts)


def card_markdown(card: dict, names: list[str] | None = None) -> str:
    """The card as markdown: the log's table, a metric per row and a set per
    column (one set's card adds its counts, targets and marks), the gate line,
    and the readings that moved since the baseline."""
    names = names or list(card["columns"])
    one_set = len(names) == 1 and names[0] != POOLED
    engines = card["engines"]
    gate = card.get("gate")
    if gate and one_set:
        gate = for_set(gate, names[0])
    thresholds = card.get("thresholds")
    sets = [name for name in names if name != POOLED]
    title = (
        f"# Card {names[0]}: {engines['a']} against {engines['b']}"
        if one_set
        else f"# Gate card: {engines['a']} against {engines['b']}, {len(sets)} sets"
    )
    lines = [title, ""]
    lines += [_context(name, card["columns"][name]) for name in names]
    lines.append("")
    if gate:
        lines += [gate_line(gate), ""]
    keys: list[str] = []
    for name in names:
        keys += [k for k in card["columns"][name]["readings"] if k not in keys]
    target_head = f" target ({gate['stage']}) |" if gate else ""
    if one_set:
        lines += [
            "| metric | value |" + (target_head + " |" if gate else ""),
            "|---|---|" + ("---|---|" if gate else ""),
        ]
    else:
        lines += [
            "| metric | " + " | ".join(names) + " |" + target_head,
            "|---|" + "---|" * len(names) + ("---|" if gate else ""),
        ]
    for key in keys:
        unit = unit_of(key)
        if one_set:
            column = card["columns"][names[0]]
            row = f"| {label_of(key)} | {format_reading(column['readings'].get(key), unit)} |"
            if gate:
                target = target_for(thresholds, key, names[0], column.get("instrument"))
                mark = gate["marks"].get(names[0], {}).get(key, "")
                row += f" {describe_target(target)} | {mark} |"
        else:
            cells = []
            for name in names:
                one = card["columns"][name]["readings"].get(key)
                cell = format_value(one.get("value"), unit) if one else "-"
                if gate and gate["marks"].get(name, {}).get(key) == "miss":
                    cell += " MISS"
                cells.append(cell)
            row = f"| {label_of(key)} | " + " | ".join(cells) + " |"
            if gate:
                # The column states the metric's own target; the marks say which
                # sets it holds for, and where a set is held to one of its own.
                row += f" {describe_target(target_for(thresholds, key, None, None))} |"
        lines.append(row)
    baseline = card.get("baseline")
    if baseline is not None:
        moved = [one for one in baseline["moved"] if one["set"] in names]
        lines += ["", "## Since the baseline", "", baseline_line(baseline) + "."]
        if moved:
            lines += ["", "| set | metric | was | now |", "|---|---|---|---|"]
            for one in moved:
                unit = unit_of(one["metric"])
                lines.append(
                    f"| {one['set']} | {label_of(one['metric'])} | "
                    f"{format_reading(one['was'], unit)} | "
                    f"{format_reading(one['now'], unit)} |"
                )
    return "\n".join(lines) + "\n"


def card_files(card: dict) -> dict[str, str]:
    """Every file of the card, by name: the whole card and one per set, each as
    markdown and JSON. A set's JSON is a card of its own, so it can be the
    baseline of another."""
    files = {
        "card.md": card_markdown(card),
        "card.json": json.dumps(card, indent=2, default=_json_default) + "\n",
    }
    for name in card["columns"]:
        if name == POOLED:
            continue
        diff = card.get("baseline")
        own = {
            **card,
            "columns": {name: card["columns"][name]},
            "gate": for_set(card["gate"], name) if card.get("gate") else None,
            "baseline": (
                {
                    "compared": diff["by_column"].get(name, 0),
                    "by_column": {
                        k: v for k, v in diff["by_column"].items() if k == name
                    },
                    "moved": [m for m in diff["moved"] if m["set"] == name],
                    "only_baseline": [],
                    "only_card": [k for k in diff["only_card"] if k == name],
                }
                if diff is not None
                else None
            ),
        }
        files[f"card_{name}.md"] = card_markdown(own, [name])
        files[f"card_{name}.json"] = (
            json.dumps(own, indent=2, default=_json_default) + "\n"
        )
    return files


def read_provenance(client: MascopeClient, sample_id: str, ids: list[str]) -> dict:
    """Each committed row's provenance, read through the detail route.

    The ledger serves a slim row; the tier reasons, the mass gate's record, the
    predicted envelope and the peak's separation are in the provenance, which
    only the detail read serves. A read that fails, on an answer or on no
    answer at all once the client's retries are spent, leaves its row unread
    and is counted (``detail_failed``): the reason coverage, G13 and G14 leave
    it out, and where they then meet their target the gate marks them not
    read; G7 counts it as uncorroborated where its mass sits beyond three
    widths. So a failed read never passes the gate. A read that answers with
    no provenance is a row with no record: no tier reason, no corroboration.

    :return: Assignment id -> its provenance, or None where the read failed.
    """

    def one(row_id: str):
        try:
            detail = client.peak_assignments.detail(sample_id, row_id)
        except MascopeError as error:
            print(
                f"{sample_id}: detail of {row_id} not read ({error})", file=sys.stderr
            )
            return row_id, None
        return row_id, (detail or {}).get("provenance") or {}

    with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as pool:
        return dict(pool.map(one, ids))


def committed_ids(ledger: pd.DataFrame) -> list[str]:
    """The rows whose provenance the card reads: every committed one, a
    monoisotopic or isotopologue row with a formula."""
    committed = ledger[
        ledger["role"].isin([ROLE_MAIN, "iso_child"])
        & ledger["assigned_formula"].notna()
    ]
    return committed["peak_assignment_id"].astype(str).tolist()


def card_samples(client: MascopeClient, spec: dict) -> list[str]:
    """A set's samples: the ones the manifest names, else its batch's."""
    if spec.get("samples"):
        return [str(one) for one in spec["samples"]]
    listing = client.samples.list(batch=spec["batch"], dataset=spec.get("dataset"))
    if listing is None or listing.empty:
        return []
    return listing["sample_item_id"].astype(str).tolist()


def collect_set(client: MascopeClient, name: str, spec: dict, args) -> dict:
    """Everything one set's card column is computed from.

    Every sample with a completed run of engine A is read; one with a
    reference run too is joined to it, one without is joined to an absent side
    and read by the intrinsic metrics alone. A sample with no completed run of
    engine A, or an empty ledger, is skipped and counted.

    :return: ``{"samples", "skipped", "frame", "source_ions",
        "engine_versions", "tiering_rules", "decoy"}``.
    """
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
    samples = card_samples(client, spec)
    frames, source_ions, skipped = [], {}, 0
    versions = {"a": set(), "b": set()}
    rules = set()
    for sample_id in samples:
        runs = client.peak_assignments.list_runs(sample_id)
        run_a = latest_completed_run(runs, args.engine_a)
        if run_a is None:
            print(f"set {name}: {sample_id} skipped (no completed {args.engine_a} run)")
            skipped += 1
            continue
        ledger_a = client.peak_assignments.get(sample_id, run_id=run_a)
        if ledger_a is None:
            print(f"set {name}: {sample_id} skipped (empty ledger)")
            skipped += 1
            continue
        run_b = latest_completed_run(runs, args.engine_b, before=args.engine_b_before)
        ledger_b = (
            client.peak_assignments.get(sample_id, run_id=run_b) if run_b else None
        )
        joined = join_ledgers(
            prepare(ledger_a, "a", notation_by_id),
            prepare(ledger_b, "b", notation_by_id)
            if ledger_b is not None
            else absent_side("b"),
        )
        provenance = read_provenance(client, sample_id, committed_ids(ledger_a))
        read = pd.DataFrame(
            [
                {
                    "a_assignment_id": row_id,
                    **metrics.provenance_columns(record),
                    "a_detail_failed": record is None,
                }
                for row_id, record in provenance.items()
            ],
            columns=[
                "a_assignment_id",
                "a_reasons",
                "a_mass_gate_corroborated",
                "a_absent_lines",
                "a_unseparated",
                "a_detail_failed",
            ],
        )
        joined["a_assignment_id"] = joined["a_assignment_id"].astype(object)
        joined = joined.merge(
            read.astype({"a_assignment_id": object}),
            on="a_assignment_id",
            how="left",
        )
        config = run_config(runs, run_a)
        joined["a_unsearched"] = joined["sample_peak_id"].isin(
            unsearched_peaks(ledger_a, config)
        )
        joined["reference"] = ledger_b is not None
        joined["sulfur_free"] = bool(spec.get("sulfur_free"))
        joined.insert(0, "sample_item_id", sample_id)
        frames.append(joined)
        source_ions[sample_id] = metrics.profile_source_ions(config)
        for side, run_id in (("a", run_a), ("b", run_b)):
            row = runs[runs["peak_assignment_run_id"] == run_id] if run_id else None
            if row is not None and not row.empty:
                versions[side].add(str(row.iloc[0]["engine_version"]))
        tiering = (config.get("tiering") or {}).get("version")
        if tiering is not None:
            rules.add(int(tiering))
        print(
            f"set {name}: {sample_id} {len(joined)} peaks, "
            f"{'with' if ledger_b is not None else 'without'} a reference run, "
            f"{sum(one is not None for one in provenance.values())} of "
            f"{len(provenance)} rows' provenance read"
        )
    decoy = decoy_report(client, samples) if args.decoy else None
    return {
        "samples": samples,
        "skipped": skipped,
        "frame": pd.concat(frames, ignore_index=True) if frames else None,
        "source_ions": source_ions,
        "engine_versions": {side: sorted(v) for side, v in versions.items()},
        "tiering_rules": sorted(rules),
        "decoy": decoy,
    }


def pooled_arms(decoys: list[dict]) -> dict:
    """The decoy arms' totals over several sets, each arm's rates re-read."""
    pooled: dict = {}
    for decoy in decoys:
        for arm, total in decoy.get("pooled", {}).items():
            into = pooled.setdefault(arm, {"samples": 0, "control": {}, "decoy": {}})
            into["samples"] += total["samples"]
            into["control"] = _added(into["control"], total["control"])
            into["decoy"] = _added(into["decoy"], total["decoy"])
    for total in pooled.values():
        total.update(decoy_rates(total["control"], total["decoy"]))
    return dict(sorted(pooled.items()))


def _not_read_arms(decoy: dict | None) -> dict[str, int]:
    """How many samples of a set each decoy arm could not be read on."""
    counts: dict[str, int] = {}
    for arms in (decoy or {}).get("skipped", {}).values():
        for arm in arms:
            counts[arm] = counts.get(arm, 0) + 1
    return dict(sorted(counts.items()))


def build_card(collected: dict[str, dict], manifest: dict, args) -> dict:
    """The card: one column per set and one pooled over them all.

    A set on which no sample was read keeps its column, every reading of it
    not read but the samples it skipped, so the card and the gate name it.
    """
    columns = {}
    for name, one in collected.items():
        frame = one["frame"]
        columns[name] = {
            "instrument": manifest["sets"][name]["instrument"],
            "engine_versions": one["engine_versions"],
            "tiering_rules": one["tiering_rules"],
            "decoys_not_read": _not_read_arms(one["decoy"]),
            "readings": (
                metrics.card_readings(
                    frame,
                    skipped=one["skipped"],
                    source_ions=one["source_ions"],
                    decoy_arms=(one["decoy"] or {}).get("pooled"),
                )
                if frame is not None
                else metrics.unread_readings(one["skipped"])
            ),
        }
    read = [one for one in collected.values() if one["frame"] is not None]
    pooled_frame = pd.concat([one["frame"] for one in read], ignore_index=True)
    instruments = sorted({columns[name]["instrument"] for name in columns})
    columns[POOLED] = {
        "instrument": instruments[0] if len(instruments) == 1 else "mixed",
        "engine_versions": {
            side: sorted({v for one in read for v in one["engine_versions"][side]})
            for side in ("a", "b")
        },
        "tiering_rules": sorted({v for one in read for v in one["tiering_rules"]}),
        "decoys_not_read": {},
        "readings": metrics.card_readings(
            pooled_frame,
            skipped=sum(one["skipped"] for one in collected.values()),
            source_ions={k: v for one in read for k, v in one["source_ions"].items()},
            decoy_arms=pooled_arms([one["decoy"] for one in read if one["decoy"]])
            if args.decoy
            else None,
        ),
    }
    return {
        "format": CARD_FORMAT,
        "engines": {"a": args.engine_a, "b": args.engine_b},
        "columns": columns,
        "gate": None,
        "thresholds": None,
        "baseline": None,
    }


def run_card(args) -> int:
    """``--card``: read every set of the manifest, mark, diff, scrub, write.

    :return: 0 when the card is written and the gate, if asked, passes; 1 when
        it fails; 2 when the scrub refuses the card, which is then not written.
    """
    manifest = load_sets(args.sets)
    thresholds = load_thresholds(args.gate) if args.gate else None
    baseline = load_baseline(args.baseline) if args.baseline else None
    denied = list(manifest.get("deny") or [])
    if args.deny:
        denied += load_deny(args.deny)
    hosts = host_names(os.environ.get("MASCOPE_URL"))
    refuse_denied_sets(manifest["sets"], denied + hosts)
    collected, names, ids = {}, [], []
    for name, spec in manifest["sets"].items():
        client = MascopeClient(workspace=spec["workspace"])
        collected[name] = one = collect_set(client, name, spec, args)
        names += [spec["workspace"], spec.get("batch"), spec.get("dataset")]
        ids += one["samples"]
    if not any(one["frame"] is not None for one in collected.values()):
        sys.exit(f"nothing to card: no sample carries a completed {args.engine_a} run")
    card = build_card(collected, manifest, args)
    if thresholds is not None:
        card["thresholds"] = thresholds
        card["gate"] = judge(card, thresholds)
    if baseline is not None:
        card["baseline"] = diff_cards(baseline, card)
    tokens = deny_tokens(
        [n for n in names if n],
        ids,
        list(manifest["sets"]),
        strict=denied,
        hosts=hosts,
        own_words=re.split(r"[^A-Za-z0-9]+", f"{args.engine_a} {args.engine_b}")
        + sorted(card_vocabulary(card)),
    )
    files = card_files(card)
    try:
        scrub(files, tokens)
    except CardRefused as refused:
        print(f"card refused, nothing written: {refused}", file=sys.stderr)
        return 2
    out = Path(args.card)
    out.mkdir(parents=True, exist_ok=True)
    for file_name, text in files.items():
        (out / file_name).write_text(text, encoding="utf-8")
    print(files["card.md"])
    if card["gate"]:
        print(gate_line(card["gate"]))
    if card["baseline"] is not None:
        print(f"BASELINE: {baseline_line(card['baseline'])}")
    return 1 if card["gate"] and card["gate"]["verdict"] == "fail" else 0


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
    parser.add_argument("--workspace", help="workspace name the samples live in")
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
        "--engine-b-before",
        type=utc_instant,
        metavar="TIMESTAMP",
        help="read the second engine's newest completed run created before this "
        "ISO timestamp (UTC unless it says otherwise) instead of its latest - "
        "a re-published reference read as it stood before",
    )
    parser.add_argument(
        "--out", default="assignment_compare_out", help="output directory"
    )
    parser.add_argument(
        "--decoy",
        action="store_true",
        help="report each sample's decoy runs against the run each is a decoy "
        "of, instead of comparing two engines; with --card, the arms join the card",
    )
    parser.add_argument(
        "--card",
        metavar="DIR",
        help="write the gate card for every set of --sets into DIR: one card per "
        "set and one pooled, as markdown and JSON",
    )
    parser.add_argument(
        "--sets",
        metavar="MANIFEST",
        help="the sets a card reads, by letter (README, 'The gate card')",
    )
    parser.add_argument(
        "--gate",
        nargs="?",
        const=str(DEFAULT_THRESHOLDS),
        metavar="THRESHOLDS",
        help="mark the card against the stage targets of THRESHOLDS (the shipped "
        "thresholds.json without one) and print the pass-or-fail line",
    )
    parser.add_argument(
        "--baseline",
        metavar="CARD_JSON",
        help="diff the card against a previous card.json, reading by reading",
    )
    parser.add_argument(
        "--deny",
        metavar="FILE",
        help="further names the card must not hold, one per line, beside the "
        "manifest's names and the hosts",
    )
    args = parser.parse_args(argv)
    if args.card:
        if not args.sets:
            parser.error("--card reads the sets of a --sets manifest")
        return run_card(args)
    if args.gate or args.baseline or args.sets or args.deny:
        parser.error("--sets, --gate, --baseline and --deny belong to --card")
    if not args.workspace:
        parser.error("give a --workspace")
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

    if args.decoy:
        result = decoy_report(client, resolve_samples(client, args))
        if not result["per_sample"]:
            sys.exit("nothing to report: no sample carries a completed decoy run")
        (out_dir / "decoy.json").write_text(
            json.dumps(result, indent=2, default=_json_default), encoding="utf-8"
        )
        (out_dir / "decoy.md").write_text(decoy_markdown(result), encoding="utf-8")
        print(decoy_markdown(result))
        return 0

    frames = []
    per_sample = {}
    for sample_id in resolve_samples(client, args):
        runs = client.peak_assignments.list_runs(sample_id)
        run_a = latest_completed_run(runs, args.engine_a)
        run_b = latest_completed_run(runs, args.engine_b, before=args.engine_b_before)
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
    if args.engine_b_before is not None:
        result["engine_b_before"] = args.engine_b_before.isoformat()
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
