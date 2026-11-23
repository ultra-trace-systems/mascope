"""The co-variation null model, measured on a database's batch ledgers (step 4.21).

Step 4.2's co-variation reading calls two channels of one neutral co-varying
at r >= 0.6. This script measures what that r is worth: how often chance
pairs read it, how often pairs of one species do, and the likelihood ratio
between the two, per stratum of shared spectra and per reagent chemistry
(``mascope_tools.composition.co_variation_lr`` says the model). It reads a
database read-only and writes nothing to it.

For every batch ledger, in each partition (an ionization mode on an
instrument type), it reads the partition's traces as the reading does
(``batch_coherence.partition_anchors``, ``coherence.batch_residuals``) and
then three kinds of pair:

- **channel pairs**: every two batch peaks of one neutral through two
  different mechanisms, each pair once, as ``batch_coherence.read_partition``
  reads them;
- **decoy pairs**, the null: for each channel pair, its first trace against
  ``--decoys`` traces of other neutrals of the partition, each present in a
  number of spectra within a quarter of the real partner's (the rule of
  step 4.2's measurement of 2026-11-08), drawn without replacement from a
  generator seeded by ``--seed`` and the batch, so a second run gives the
  same tables;
- **isotope pairs**, the upper bound: an assigned M0 against its own assigned
  isotope line, the line read through the partition's normaliser and common
  mode (``BatchResiduals.residual_of``).

Each pair is read with ``coherence.read_co_variation`` at its thresholds. A
batch's reagent chemistry is read off the mechanisms its channels name
(:func:`chemistry_of`), never off a mode's name. The script reports, per
chemistry and stratum, the pairs of each kind, the share at r >= 0.6, the
mixture's true share, both likelihood-ratio rows, the bar (the lowest r at
which the mixture's ratio reaches 10) with a cluster bootstrap over the
channel pairs and their decoys, and whether the residual spread needs to be
a stratum too. It counts the channel and isotope pairs under the null's
median and lower quartile, the null share's assumption read, and says
whether the bar clears on two reagents: two chemistries that share no
reagent (:data:`REAGENTS`). It prints the table the library keeps, for a new
version.

Run it against an env's database with the dev stack up::

    MASCOPE_ENV=testbed uv run python tooling/assignment_bench/covariation_null.py \\
        --json out.json --markdown out.md

The output names batches by an index and chemistries by their reagent only:
it is the shape the plan's log takes, with nothing that names a sample.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import sys
import zlib
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterable, Optional

import numpy as np

from mascope_tools.composition.co_variation_lr import (
    ANTI_QUANTILE,
    LR_BAR,
    R_EDGES,
    STRATA,
    anti_excess_shown,
    bar_of,
    excess_shown,
    mixture_lr,
    null_share_error,
    ratio_lr,
    stratum_of,
)
from mascope_tools.composition.coherence import (
    CO_VARYING_R,
    VERDICT_UNTESTABLE,
    CoVariation,
    batch_residuals,
    read_co_variation,
)


#: The seed the decoys and the bootstrap are drawn from.
DEFAULT_SEED = 421

#: Decoy partners drawn per channel pair.
DEFAULT_DECOYS = 20

#: Bootstrap resamples behind each bar's interval.
DEFAULT_BOOTSTRAP = 200

#: The prevalence a decoy partner must be within, as a share of the real
#: partner's (step 4.2's rule).
PREVALENCE_TOLERANCE = 0.25

#: The fewest testable channel pairs a stratum of a chemistry must hold for
#: its bar to count towards "clears on two reagents".
MIN_CHANNEL_PAIRS = 30

#: The share of bootstrap resamples that must find a bar for it to clear.
BOOTSTRAP_SUPPORT = 0.95

#: The bands of the flatter trace's residual spread the null's tail is read
#: in, to see whether the spread needs to be a stratum of its own.
SPREAD_BANDS = (0.02, 0.04, 0.08, 0.16)

KIND_CHANNEL = "channel"
KIND_DECOY = "decoy"
KIND_ISOTOPE = "isotope"
KINDS = (KIND_CHANNEL, KIND_ISOTOPE, KIND_DECOY)

TIER_ASSIGNED = "assigned"


# --- reading a batch -----------------------------------------------------------


#: The chemistry of a partition that commits no channel: none to read it off.
CHEMISTRY_NONE = "none"


def chemistry_of(notations: Iterable[str]) -> str:
    """A batch's reagent chemistry, read off the mechanisms its channels name:
    the reagent an adduct channel carries, else charge transfer by polarity.

    :param notations: The notation of every committed channel of the batch.
    """
    notations = list(notations)
    if not notations:
        return CHEMISTRY_NONE
    joined = " ".join(notations)
    if "CH4N2O" in joined:
        return "uronium"
    if "^NO3" in joined:
        return "15N-nitrate"
    has_nitrate = "NO3" in joined.replace("^NO3", "")
    has_bromide = "Br" in joined
    if has_nitrate and has_bromide:
        return "nitrate+bromide"
    if has_bromide:
        return "bromide"
    if has_nitrate:
        return "nitrate"
    if "I]" in joined or "+I" in joined:
        return "iodide"
    positive = sum(n.endswith("+") or n.endswith("+.") for n in notations)
    return (
        "charge transfer (+)"
        if positive * 2 >= len(notations)
        else "charge transfer (-)"
    )


#: The reagents each chemistry label reads with. Decision 35's "two reagents"
#: are two reagent chemistries whose batches are independent evidence, so two
#: labels count as two reagents only where they share none: the two
#: polarities of charge transfer are one chemistry, and 15N-nitrate, nitrate
#: and nitrate+bromide all read with the nitrate ion. A label not here (none,
#: or one a later :func:`chemistry_of` adds before this is told) counts as no
#: reagent at all.
REAGENTS: dict[str, frozenset[str]] = {
    "uronium": frozenset({"uronium"}),
    "15N-nitrate": frozenset({"nitrate"}),
    "nitrate": frozenset({"nitrate"}),
    "nitrate+bromide": frozenset({"nitrate", "bromide"}),
    "bromide": frozenset({"bromide"}),
    "iodide": frozenset({"iodide"}),
    "charge transfer (+)": frozenset({"charge transfer"}),
    "charge transfer (-)": frozenset({"charge transfer"}),
}


def two_reagents(chemistries: Iterable[str]) -> Optional[tuple[str, str]]:
    """The first two of ``chemistries`` that read with no reagent in common
    (:data:`REAGENTS`); None where no two do."""
    names = sorted(set(chemistries))
    for i, first in enumerate(names):
        a = REAGENTS.get(first, frozenset())
        for second in names[i + 1 :]:
            b = REAGENTS.get(second, frozenset())
            if a and b and not a & b:
                return first, second
    return None


@dataclass(frozen=True)
class PairReading:
    """One pair's reading, as the measurement keeps it.

    :ivar kind: :data:`KIND_CHANNEL`, :data:`KIND_DECOY` or
        :data:`KIND_ISOTOPE`.
    :ivar cluster: The channel pair a decoy was drawn for (its own index for a
        channel pair; -1 for an isotope pair), counted within its batch: with
        :attr:`batch`, the unit the bootstrap draws.
    :ivar n_spectra: The spectra both traces hold.
    :ivar r: Pearson's r; None where untestable.
    :ivar spread: The flatter trace's residual spread; None under two spectra.
    :ivar reason: Why it is untestable; None otherwise.
    :ivar batch: The batch's index in the run.
    """

    kind: str
    cluster: int
    n_spectra: int
    r: Optional[float]
    spread: Optional[float]
    reason: Optional[str]
    batch: int = 0

    @property
    def testable(self) -> bool:
        return self.r is not None


def _pair(kind: str, cluster: int, reading: CoVariation, batch: int) -> PairReading:
    return PairReading(
        batch=batch,
        kind=kind,
        cluster=cluster,
        n_spectra=reading.n_spectra,
        r=reading.r,
        spread=None if reading.spread is None else min(reading.spread),
        reason=reading.reason if reading.verdict == VERDICT_UNTESTABLE else None,
    )


@dataclass
class BatchReading:
    """What one batch's partition gave: its chemistry and every pair."""

    index: int
    chemistry: str
    instrument_type: Optional[str]
    spectra: int
    pairs: list[PairReading] = field(default_factory=list)


def read_partition_pairs(
    part: Any,
    anchors_by_id: dict[str, Any],
    traces: dict[str, dict[str, float]],
    rng: np.random.Generator,
    *,
    decoys: int = DEFAULT_DECOYS,
    cluster_start: int = 0,
    batch: int = 0,
) -> list[PairReading]:
    """Every channel, decoy and isotope pair of one partition.

    :param part: The partition's anchors (``batch_coherence.PartitionAnchors``).
    :param anchors_by_id: The batch's anchors, for the isotope lines.
    :param traces: Anchor id -> sample -> intensity.
    :param rng: The generator the decoys are drawn from.
    :param decoys: Decoy partners per channel pair.
    :param cluster_start: The first channel pair's cluster index.
    :param batch: The batch's index, which every pair carries.
    """
    residuals = batch_residuals(
        {key: traces.get(key, {}) for key in part.traces},
        reagent_traces={key: traces.get(key, {}) for key in part.reagents},
    )
    present = {
        key: int(np.isfinite(residuals.residuals[i]).sum())
        for i, key in enumerate(residuals.keys)
    }
    neutral_of = {}
    for neutral, channels in part.neutrals.items():
        for channel in channels:
            neutral_of[channel.batch_peak_id] = neutral
    pool = sorted(neutral_of)

    out: list[PairReading] = []
    cluster = cluster_start
    for neutral, channels in sorted(part.neutrals.items()):
        if len({channel.channel for channel in channels}) < 2:
            continue
        ordered = sorted(channels, key=lambda c: c.batch_peak_id)
        for i, own in enumerate(ordered):
            for other in ordered[i + 1 :]:
                if other.channel == own.channel:
                    continue
                a, b = own.batch_peak_id, other.batch_peak_id
                out.append(
                    _pair(KIND_CHANNEL, cluster, residuals.co_variation(a, b), batch)
                )
                target = present[b]
                eligible = [
                    key
                    for key in pool
                    if neutral_of[key] != neutral
                    and abs(present[key] - target) <= PREVALENCE_TOLERANCE * target
                ]
                if eligible:
                    take = min(decoys, len(eligible))
                    for index in sorted(rng.choice(len(eligible), take, replace=False)):
                        decoy = eligible[int(index)]
                        out.append(
                            _pair(
                                KIND_DECOY,
                                cluster,
                                residuals.co_variation(a, decoy),
                                batch,
                            )
                        )
                cluster += 1

    in_partition = set(residuals.keys)
    for anchor_id in sorted(anchors_by_id):
        line = anchors_by_id[anchor_id]
        parent_id = line.isotopologue_of
        if not parent_id or parent_id not in in_partition:
            continue
        parent = anchors_by_id[parent_id]
        if (
            line.consensus_tier != TIER_ASSIGNED
            or parent.consensus_tier != TIER_ASSIGNED
        ):
            continue
        reading = read_co_variation(
            residuals.trace(parent_id), residuals.residual_of(traces.get(anchor_id, {}))
        )
        out.append(_pair(KIND_ISOTOPE, -1, reading, batch))
    return out


def batch_rng(seed: int, batch_id: str) -> np.random.Generator:
    """The generator a batch's decoys are drawn from: the run's seed and the
    batch's own id, so each batch draws the same decoys whatever else the
    run reads, and two batches draw different ones."""
    return np.random.default_rng([seed, zlib.crc32(batch_id.encode())])


def read_batch_pairs(
    index: int,
    batch_id: str,
    anchors: list[Any],
    notation_by_id: dict[str, str],
    traces: dict[str, dict[str, float]],
    *,
    seed: int = DEFAULT_SEED,
    decoys: int = DEFAULT_DECOYS,
    partition_anchors: Optional[Callable[..., dict]] = None,
) -> list[BatchReading]:
    """Every partition of one batch.

    :param partition_anchors: What sorts the anchors into partitions and
        roles; ``batch_coherence.partition_anchors``, the reading's own, when
        omitted.
    """
    if partition_anchors is None:
        from mascope_backend.api.new.peak_assignments.batch_coherence import (
            partition_anchors,
        )

    rng = batch_rng(seed, batch_id)
    anchors_by_id = {a.batch_peak_id: a for a in anchors}
    out = []
    cluster = 0
    partitions = partition_anchors(anchors, notation_by_id)
    for (mode_id, instrument_type), part in sorted(
        partitions.items(), key=lambda item: (str(item[0][0]), str(item[0][1]))
    ):
        notations = [c.channel for cs in part.neutrals.values() for c in cs]
        pairs = read_partition_pairs(
            part,
            anchors_by_id,
            traces,
            rng,
            decoys=decoys,
            cluster_start=cluster,
            batch=index,
        )
        cluster += sum(1 for p in pairs if p.kind == KIND_CHANNEL)
        held = {
            s for key in (*part.traces, *part.reagents) for s in traces.get(key, {})
        }
        out.append(
            BatchReading(
                index=index,
                chemistry=chemistry_of(notations),
                instrument_type=instrument_type,
                spectra=len(held),
                pairs=pairs,
            )
        )
    return out


# --- the statistics ------------------------------------------------------------


def wilson(
    successes: int, n: int, z: float = 1.96
) -> tuple[Optional[float], Optional[float]]:
    """Wilson's score interval of a share (J. Am. Stat. Assoc. 1927, 22, 209)."""
    if n == 0:
        return None, None
    p = successes / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, centre - half), min(1.0, centre + half)


def _tail(rs: list[float], at: float = CO_VARYING_R) -> dict:
    hits = sum(r >= at for r in rs)
    low, high = wilson(hits, len(rs))
    return {
        "n": len(rs),
        "share": None if not rs else round(hits / len(rs), 4),
        "low": None if low is None else round(low, 4),
        "high": None if high is None else round(high, 4),
    }


def _rounded(values: Iterable[float], digits: int = 3) -> list[float]:
    return [round(float(v), digits) for v in values]


def _bootstrap_bars(
    clusters: dict[tuple[int, int], tuple[list[float], list[float]]],
    rng: np.random.Generator,
    resamples: int,
) -> list[Optional[float]]:
    """The mixture's bar on each resample of the channel pairs, each drawn
    with the decoys drawn for it."""
    keys = sorted(clusters)
    if not keys:
        return []
    bars = []
    for _ in range(resamples):
        picks = rng.integers(0, len(keys), len(keys))
        channel: list[float] = []
        null: list[float] = []
        for pick in picks:
            c, d = clusters[keys[int(pick)]]
            channel.extend(c)
            null.extend(d)
        if not channel or not null:
            bars.append(None)
            continue
        ratios, _ = mixture_lr(channel, null)
        bars.append(bar_of(ratios))
    return bars


def stratum_summary(
    pairs: list[PairReading], rng: np.random.Generator, resamples: int
) -> list[dict]:
    """Per stratum of shared spectra: the counts, the tails at 0.6, the
    mixture's share, both ratio rows, and the bar with its interval."""
    out = []
    for stratum, edge in enumerate(STRATA):
        upper = STRATA[stratum + 1] - 1 if stratum + 1 < len(STRATA) else None
        inside = [p for p in pairs if p.testable and stratum_of(p.n_spectra) == stratum]
        by_kind = {kind: [p.r for p in inside if p.kind == kind] for kind in KINDS}
        channel, null, iso = (
            by_kind[KIND_CHANNEL],
            by_kind[KIND_DECOY],
            by_kind[KIND_ISOTOPE],
        )
        row: dict = {
            "stratum": f"{edge}-{upper}" if upper else f"{edge}+",
            "pairs": {kind: len(by_kind[kind]) for kind in KINDS},
            "tail_at_0_6": {kind: _tail(by_kind[kind]) for kind in KINDS},
        }
        row["below_null"] = _below_null(channel, iso, null)
        row["anti_excess_shown"] = bool(
            channel and null and anti_excess_shown(channel, null)
        )
        if channel and null:
            mix, pi = mixture_lr(channel, null)
            row["true_share"] = round(pi, 4)
            row["true_share_error"] = round(null_share_error(channel, null), 4)
            row["excess_shown"] = excess_shown(channel, null)
            row["lr_mixture"] = _rounded(mix)
            row["bar_mixture"] = bar_of(mix)
            # A channel pair is its batch's and its index there: two
            # batches' first pairs are two clusters.
            clusters: dict[tuple[int, int], tuple[list[float], list[float]]] = (
                defaultdict(lambda: ([], []))
            )
            for p in inside:
                if p.kind == KIND_CHANNEL:
                    clusters[(p.batch, p.cluster)][0].append(p.r)
                elif p.kind == KIND_DECOY:
                    clusters[(p.batch, p.cluster)][1].append(p.r)
            bars = _bootstrap_bars(dict(clusters), rng, resamples)
            found = sorted(b for b in bars if b is not None)
            row["bar_bootstrap"] = {
                "resamples": len(bars),
                "found": len(found),
                "with_bar": round(len(found) / len(bars), 4) if bars else 0.0,
                "p05": found[int(0.05 * (len(found) - 1))] if found else None,
                "p50": found[int(0.50 * (len(found) - 1))] if found else None,
                "p95": found[int(0.95 * (len(found) - 1))] if found else None,
            }
            row["clears"] = bool(
                row["bar_mixture"] is not None
                and len(channel) >= MIN_CHANNEL_PAIRS
                and row["bar_bootstrap"]["with_bar"] >= BOOTSTRAP_SUPPORT
            )
        else:
            row.update(
                true_share=None,
                true_share_error=None,
                excess_shown=False,
                lr_mixture=None,
                bar_mixture=None,
                bar_bootstrap=None,
                clears=False,
            )
        if iso and null:
            upper_bound = ratio_lr(iso, null)
            row["lr_isotope"] = _rounded(upper_bound)
            row["bar_isotope"] = bar_of(upper_bound)
        else:
            row["lr_isotope"] = None
            row["bar_isotope"] = None
        out.append(row)
    return out


def _below_null(
    channel: list[float], iso: list[float], null: list[float]
) -> Optional[dict]:
    """How many channel and isotope pairs lie under the null's median and
    its lower quartile: the null share's assumption read. An isotope pair is
    one species, so each under the median is a true pair the share counts as
    chance; an excess of channel pairs under the quartile is anti-correlated
    true pairs."""
    if not null:
        return None
    median, quartile = np.quantile(null, [0.5, ANTI_QUANTILE])
    out = {"null_median": round(float(median), 3)}
    out["null_quartile"] = round(float(quartile), 3)
    for kind, rs in ((KIND_CHANNEL, channel), (KIND_ISOTOPE, iso)):
        values = np.asarray(rs, dtype=float)
        out[kind] = {
            "n": int(values.size),
            "under_median": int((values < median).sum()),
            "under_quartile": int((values < quartile).sum()),
        }
    return out


def spread_summary(pairs: list[PairReading]) -> list[dict]:
    """The null's tail at 0.6 per stratum and band of the flatter trace's
    residual spread: whether the spread needs a stratum of its own."""
    out = []
    for stratum, edge in enumerate(STRATA):
        row = {"stratum": edge, "bands": []}
        decoys = [
            p
            for p in pairs
            if p.kind == KIND_DECOY
            and p.testable
            and stratum_of(p.n_spectra) == stratum
        ]
        for i, low in enumerate(SPREAD_BANDS):
            high = SPREAD_BANDS[i + 1] if i + 1 < len(SPREAD_BANDS) else None
            rs = [
                p.r
                for p in decoys
                if p.spread >= low and (high is None or p.spread < high)
            ]
            row["bands"].append({"from": low, "to": high, **_tail(rs)})
        out.append(row)
    return out


def untestable_counts(pairs: list[PairReading]) -> dict:
    counts: dict = {kind: defaultdict(int) for kind in KINDS}
    for p in pairs:
        counts[p.kind]["testable" if p.testable else str(p.reason)] += 1
    return {kind: dict(sorted(c.items())) for kind, c in counts.items()}


def summarise(batches: list[BatchReading], *, seed: int, resamples: int) -> dict:
    """The whole measurement: per chemistry, and over every batch."""
    groups: dict[str, list[BatchReading]] = defaultdict(list)
    for batch in batches:
        groups[batch.chemistry].append(batch)
    out_groups = {}
    for name in [*sorted(groups), "all"]:
        members = batches if name == "all" else groups[name]
        pairs = [p for b in members for p in b.pairs]
        rng = np.random.default_rng([seed, zlib.crc32(name.encode())])
        out_groups[name] = {
            "batches": len(members),
            "instrument_types": sorted({str(b.instrument_type) for b in members}),
            "spectra": sorted(b.spectra for b in members),
            "counts": untestable_counts(pairs),
            "strata": stratum_summary(pairs, rng, resamples),
            "spread": spread_summary(pairs),
        }
    chemistries = [name for name in sorted(groups)]
    clears = []
    for stratum, edge in enumerate(STRATA):
        cleared_by = [
            name
            for name in chemistries
            if out_groups[name]["strata"][stratum]["clears"]
        ]
        reagents = two_reagents(cleared_by)
        clears.append(
            {
                "stratum": out_groups["all"]["strata"][stratum]["stratum"],
                "cleared_by": cleared_by,
                "two_reagents": None if reagents is None else list(reagents),
                "clears_on_two_reagents": reagents is not None,
            }
        )
    return {
        "parameters": {
            "seed": seed,
            "bootstrap": resamples,
            "lr_bar": LR_BAR,
            "strata": list(STRATA),
            "r_edges": list(R_EDGES),
            "prevalence_tolerance": PREVALENCE_TOLERANCE,
            "min_channel_pairs": MIN_CHANNEL_PAIRS,
            "anti_quantile": ANTI_QUANTILE,
            "bootstrap_support": BOOTSTRAP_SUPPORT,
        },
        "groups": out_groups,
        "clears": clears,
    }


# --- the output ----------------------------------------------------------------


def _fmt_tail(tail: dict) -> str:
    if not tail["n"]:
        return "-"
    return f"{tail['share'] * 100:.1f} % ({tail['low'] * 100:.1f}-{tail['high'] * 100:.1f})"


def _fmt_bar(value: Optional[float]) -> str:
    return "none" if value is None else f"{value:.1f}"


def markdown(result: dict) -> str:
    """The measurement as the plan's log keeps it."""
    lines = []
    lines.append(
        "| chemistry | stratum | channel / isotope / decoy pairs | decoy r >= 0.6 | "
        "channel r >= 0.6 | isotope r >= 0.6 | true share | bar (mixture) | "
        "bootstrap: resamples with a bar, its 5-95 % | bar (isotope) | clears |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for name, group in result["groups"].items():
        for row in group["strata"]:
            pairs = row["pairs"]
            boot = row["bar_bootstrap"]
            boot_text = (
                "-"
                if not boot
                else f"{boot['found']} of {boot['resamples']}, "
                f"{_fmt_bar(boot['p05'])}-{_fmt_bar(boot['p95'])}"
            )
            lines.append(
                f"| {name} | {row['stratum']} | {pairs['channel']} / {pairs['isotope']} / "
                f"{pairs['decoy']} | {_fmt_tail(row['tail_at_0_6']['decoy'])} | "
                f"{_fmt_tail(row['tail_at_0_6']['channel'])} | "
                f"{_fmt_tail(row['tail_at_0_6']['isotope'])} | "
                f"{'-' if row['true_share'] is None else round(row['true_share'], 2)} | "
                f"{_fmt_bar(row['bar_mixture'])} | {boot_text} | "
                f"{_fmt_bar(row['bar_isotope'])} | {'yes' if row['clears'] else 'no'} |"
            )
    lines.append("")
    lines.append("Likelihood ratio over every batch, per bin of r (lower edge):")
    lines.append("")
    edges = result["parameters"]["r_edges"]
    shown = [i for i, e in enumerate(edges[:-1]) if e >= -0.2]
    lines.append(
        "| stratum | estimate | " + " | ".join(f"{edges[i]:.1f}" for i in shown) + " |"
    )
    lines.append("|---|---|" + "---|" * len(shown))
    for row in result["groups"]["all"]["strata"]:
        for label, key in (("mixture", "lr_mixture"), ("isotope", "lr_isotope")):
            values = row[key]
            cells = (
                ["-"] * len(shown)
                if values is None
                else [_fmt_lr(values[i]) for i in shown]
            )
            lines.append(f"| {row['stratum']} | {label} | " + " | ".join(cells) + " |")
    lines.append("")
    lines.append("Decoy r >= 0.6 by the flatter trace's residual spread, every batch:")
    lines.append("")
    bands = result["groups"]["all"]["spread"][0]["bands"]
    lines.append(
        "| stratum | "
        + " | ".join(
            f"{b['from']}-{b['to']}" if b["to"] else f"{b['from']}+" for b in bands
        )
        + " |"
    )
    lines.append("|---|" + "---|" * len(bands))
    for row, strat in zip(
        result["groups"]["all"]["spread"], result["groups"]["all"]["strata"]
    ):
        lines.append(
            f"| {strat['stratum']} | "
            + " | ".join(
                f"{_fmt_tail(b)} of {b['n']}" if b["n"] else "-" for b in row["bands"]
            )
            + " |"
        )
    lines.append("")
    lines.append(
        "The null share's assumption, pairs under the null's median and its "
        "lower quartile (a chance pair: a half and a quarter):"
    )
    lines.append("")
    lines.append(
        "| chemistry | stratum | null median / quartile | channel under median | "
        "channel under quartile | isotope under median | isotope under quartile | "
        "anti-correlated excess |"
    )
    lines.append("|---|---|---|---|---|---|---|---|")
    for name, group in result["groups"].items():
        for row in group["strata"]:
            below = row["below_null"]
            if below is None:
                continue
            cells = [f"{below['null_median']:.2f} / {below['null_quartile']:.2f}"]
            for kind in (KIND_CHANNEL, KIND_ISOTOPE):
                counts = below[kind]
                for key in ("under_median", "under_quartile"):
                    cells.append(
                        f"{counts[key]} of {counts['n']}" if counts["n"] else "-"
                    )
            cells.append("shown" if row["anti_excess_shown"] else "no")
            lines.append(f"| {name} | {row['stratum']} | " + " | ".join(cells) + " |")
    lines.append("")
    for row in result["clears"]:
        lines.append(
            f"- {row['stratum']}: cleared by {', '.join(row['cleared_by']) or 'none'}"
            f" - {'clears' if row['clears_on_two_reagents'] else 'does not clear'} on two reagents"
        )
    return "\n".join(lines) + "\n"


def _fmt_lr(value: float) -> str:
    if value >= 100:
        return f"{value:.0f}"
    if value >= 10:
        return f"{value:.1f}"
    return f"{value:.2f}"


# --- the database --------------------------------------------------------------


def _json_value(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


async def load_batches(batch_ids: Optional[list[str]] = None) -> list[tuple]:
    """Every batch ledger of the env's database, read-only: each batch's id,
    anchors, mechanism notations and traces. Raw SQL, so a copy whose schema
    predates the branch's columns reads too."""
    from sqlalchemy import text

    from mascope_backend.db import async_session, configure_database_engine

    await configure_database_engine()
    out = []
    async with async_session() as session:
        await session.execute(text("SET TRANSACTION READ ONLY"))
        ids = batch_ids or [
            row[0]
            for row in (
                await session.execute(
                    text("SELECT DISTINCT sample_batch_id FROM batch_peak ORDER BY 1")
                )
            ).all()
        ]
        for batch_id in ids:
            rows = (
                (
                    await session.execute(
                        text(
                            "SELECT batch_peak_id, ionization_mode_id, intensity_variable, "
                            "consensus_formula, consensus_ion_formula, ionization_mechanism_id, "
                            "consensus_tier, consensus_role, isotopologue_of "
                            "FROM batch_peak WHERE sample_batch_id = :b"
                        ),
                        {"b": batch_id},
                    )
                )
                .mappings()
                .all()
            )
            anchors = [SimpleNamespace(**dict(row)) for row in rows]
            mechanisms = sorted(
                {
                    a.ionization_mechanism_id
                    for a in anchors
                    if a.ionization_mechanism_id
                }
            )
            notation_by_id = {
                key: value
                for key, value in (
                    await session.execute(
                        text(
                            "SELECT ionization_mechanism_id, ionization_mechanism "
                            "FROM ionization_mechanism WHERE ionization_mechanism_id = ANY(:m)"
                        ),
                        {"m": mechanisms},
                    )
                ).all()
                if value
            }
            traces: dict[str, dict[str, float]] = defaultdict(dict)
            for batch_peak_id, sample_item_id, intensity in (
                await session.execute(
                    text(
                        "SELECT o.batch_peak_id, o.sample_item_id, o.intensity "
                        "FROM batch_peak_occurrence o JOIN batch_peak b USING (batch_peak_id) "
                        "WHERE b.sample_batch_id = :b AND o.intensity IS NOT NULL"
                    ),
                    {"b": batch_id},
                )
            ).all():
                traces[batch_peak_id][sample_item_id] = float(intensity)
            out.append((batch_id, anchors, notation_by_id, dict(traces)))
            print(f"read batch {len(out)} of {len(ids)}", file=sys.stderr)
    return out


def measure(loaded: list[tuple], *, seed: int, decoys: int, resamples: int) -> dict:
    batches: list[BatchReading] = []
    for index, (batch_id, anchors, notation_by_id, traces) in enumerate(loaded, 1):
        batches.extend(
            read_batch_pairs(
                index,
                batch_id,
                anchors,
                notation_by_id,
                traces,
                seed=seed,
                decoys=decoys,
            )
        )
    result = summarise(batches, seed=seed, resamples=resamples)
    result["parameters"]["decoys_per_pair"] = decoys
    result["batches"] = [
        {
            "index": b.index,
            "chemistry": b.chemistry,
            "instrument_type": b.instrument_type,
            "spectra": b.spectra,
            **{
                f"{kind}_testable": sum(
                    1 for p in b.pairs if p.kind == kind and p.testable
                )
                for kind in KINDS
            },
        }
        for b in batches
    ]
    return result


def table_literal(result: dict, version: int, measured: str) -> str:
    """The library's table (``co_variation_lr.TABLE``) from every batch's
    mixture estimate, as Python source."""
    strata = result["groups"]["all"]["strata"]
    clears = result["clears"]
    rows = []
    for row in strata:
        ratios = [
            repr(float(v)) for v in row["lr_mixture"] or [1.0] * (len(R_EDGES) - 1)
        ]
        half = len(ratios) // 2
        rows.append("        (" + ", ".join(ratios[:half]) + ")")
        rows.append("        + (" + ", ".join(ratios[half:]) + "),")
    pairs = [
        f'        {{"channel": {row["pairs"]["channel"]}, "decoy": {row["pairs"]["decoy"]}, '
        f'"isotope": {row["pairs"]["isotope"]}}},'
        for row in strata
    ]
    return "\n".join(
        [
            "TABLE = LikelihoodRatioTable(",
            f"    version={version},",
            "    strata=STRATA,",
            "    r_edges=R_EDGES,",
            "    ratios=(",
            *rows,
            "    ),",
            "    bars=(" + ", ".join(repr(row["bar_mixture"]) for row in strata) + "),",
            f"    measured={measured!r},",
            "    pairs=(",
            *pairs,
            "    ),",
            f"    provisional={not any(c['clears_on_two_reagents'] for c in clears)},",
            ")",
        ]
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--decoys", type=int, default=DEFAULT_DECOYS)
    parser.add_argument("--bootstrap", type=int, default=DEFAULT_BOOTSTRAP)
    parser.add_argument(
        "--batch", action="append", help="read only this batch (repeatable)"
    )
    parser.add_argument("--json", type=Path, help="write the full result here")
    parser.add_argument("--markdown", type=Path, help="write the log's tables here")
    parser.add_argument(
        "--table", type=Path, help="write the library's table literal here"
    )
    parser.add_argument("--version", type=int, default=1, help="the table's version")
    parser.add_argument("--measured", default="", help="the table's provenance line")
    args = parser.parse_args(argv)

    loaded = asyncio.run(load_batches(args.batch))
    result = measure(
        loaded, seed=args.seed, decoys=args.decoys, resamples=args.bootstrap
    )
    text_json = json.dumps(result, indent=1, sort_keys=True)
    text_md = markdown(result)
    if args.json:
        args.json.write_text(text_json)
    if args.markdown:
        args.markdown.write_text(text_md)
    if args.table:
        args.table.write_text(table_literal(result, args.version, args.measured) + "\n")
    print(text_md)
    print("sha256 " + hashlib.sha256(text_json.encode()).hexdigest())
    return 0


if __name__ == "__main__":
    sys.exit(main())
