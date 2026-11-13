"""
Maintenance script to give the stored match aggregates the intensity of the
isotope lines that missed the ratio tolerance.

Each matched isotope line is gated per file on its m/z error and its
abundance error (``api/new/match/params/lib.py``, ``apply_match_params``).
A line outside the isotope ratio tolerance used to lose its intensity as well
as its score, so an ion whose line missed the ratio by a little was stored at
part of its intensity: a bromide ion, whose two lines are near-equal, at
half. Its batch time series toggled between 1x and 2x from file to file
(#2943). A line on its mass now keeps its intensity when its ratio misses
by less than 100 %; only its score, and so the category, takes the miss. A
stored line always misses by less: one that misses by more scores 0 and is
not stored (#3000).

``sample_peak_intensity_sum`` on ``match_ion``, ``match_compound``,
``match_collection`` and ``match_sample`` is written when a sample's matches
are aggregated, so every sample aggregated before the change keeps the old
sum until it is matched again. This re-aggregates them from the stored
``match_isotope`` rows, as a batch match does, and writes the new sum where
it differs. It reads no raw file and matches nothing again.

**Only the sums are written.** The aggregation also recomputes each row's
score and category, which the change does not move: they are compared with
the stored ones and reported, never written. A row whose category would
differ was stored by other code than this build's (an older release, other
match parameters); a rematch of its batch is what changes that, and this
script leaves it to one.

**Which samples are read.** Only a sample that holds a matched line outside
the smallest isotope ratio tolerance any of its rows can have - the
instrument defaults and every per-ion override - can have a sum that
changes; the others are not read. Each candidate is read with the match
parameters a batch match uses (per ion, else the instrument's defaults).

**Safe beside a running server.** A sum is written under the match-write
lock of its batch, which every match writer takes, and only where the
stored sum is still the one read before the aggregation: a sample matched
again in the meantime already has a sum of this build and is left alone.
A run that stops partway is finished by the next one; running it again
writes nothing more.

``DRY_RUN=1`` reads the same samples and writes nothing. It reports, per
batch and in total, how many samples were read, how many rows of each level
would change their sum, how many ion rows would change their category (and
are left as they are), and the median factor by which the changed ion sums
grow.

``BATCH_SIZE`` sets how many samples are aggregated at once. Unset, each
batch's chunks are sized from its target set as a batch match sizes them
(``_resolve_aggregation_chunk_size``): the script runs inside the backend
container, so its memory is the live server's.
``REAGGREGATE_BATCHES`` (sample batch ids, comma-separated) limits a run to
those batches, to try one first.

**Undoing it.** The pre-script ``pg_dump`` holds the old sums. A rematch of
a batch writes the same sums this does.

Usage:
    DRY_RUN=1 mascope prod db script run reaggregate_ratio_gated_intensity
    mascope prod db script run reaggregate_ratio_gated_intensity

    REAGGREGATE_BATCHES=<id> mascope prod db script run reaggregate_ratio_gated_intensity

Date: 2026-11-05
Issue: #2943
"""

import asyncio
import math
import os
from collections import Counter, defaultdict
from dataclasses import dataclass, field

import pandas as pd
from sqlalchemy import and_, bindparam, func, select, text, update

from mascope_backend.api.controllers.match.aggregate.match_aggregate_controller import (
    _resolve_aggregation_chunk_size,
    aggregate_matches,
)
from mascope_backend.api.controllers.match.lib.match_write_lock import (
    acquire_match_write_locks,
)
from mascope_backend.db import (
    MatchCollection,
    MatchCompound,
    MatchIon,
    MatchSample,
    async_session,
    configure_database_engine,
)
from mascope_backend.runtime import runtime
from mascope_match.params import OrbiMatchParams, TofMatchParams


@dataclass(frozen=True)
class Level:
    """One stored aggregate level: its table, its natural key, and the name
    of its frame in what ``aggregate_matches`` returns."""

    name: str
    model: type
    key: tuple[str, ...]
    modified: str


LEVELS = (
    Level(
        "match_ions",
        MatchIon,
        ("sample_item_id", "target_ion_id"),
        "match_ion_utc_modified",
    ),
    Level(
        "match_compounds",
        MatchCompound,
        ("sample_item_id", "target_compound_id"),
        "match_compound_utc_modified",
    ),
    Level(
        "match_collections",
        MatchCollection,
        ("sample_item_id", "target_collection_id"),
        "match_collection_utc_modified",
    ),
    Level(
        "match_samples", MatchSample, ("sample_item_id",), "match_sample_utc_modified"
    ),
)

#: The samples holding a matched line beyond the smallest tolerance, per
#: batch. A superset: the line's own tolerance and its m/z gate are the
#: aggregation's to apply, and a sample with no stored aggregate gets none.
#: ``{batches}`` is empty, or the clause that keeps the named batches.
_CANDIDATES_SQL = """
    SELECT si.sample_batch_id AS sample_batch_id,
           mi.sample_item_id AS sample_item_id
    FROM match_isotope mi
    JOIN sample_item si ON si.sample_item_id = mi.sample_item_id
    WHERE mi.sample_peak_intensity > 0
      AND abs(mi.match_abundance_error) > :tolerance
      {batches}
    GROUP BY si.sample_batch_id, mi.sample_item_id
    ORDER BY si.sample_batch_id, mi.sample_item_id
"""


class BadSetting(ValueError):
    """An environment variable was set to something the script cannot use."""


def smallest_ratio_tolerance(filter_params: list) -> float:
    """The smallest isotope ratio tolerance any matched line is gated at.

    :param filter_params: Every ``target_ion.filter_params`` that is set: per
        instrument name, the ion's own match parameters.
    :return: The least of the instrument defaults and every override.
    """
    tolerances = [
        OrbiMatchParams().isotope_ratio_tolerance,
        TofMatchParams().isotope_ratio_tolerance,
    ]
    for params in filter_params:
        if not isinstance(params, dict):
            continue
        for own in params.values():
            value = (
                own.get("isotope_ratio_tolerance") if isinstance(own, dict) else None
            )
            if isinstance(value, (int, float)) and math.isfinite(value):
                tolerances.append(float(value))
    return min(tolerances)


def changed_sums(stored: pd.DataFrame, computed: pd.DataFrame, key) -> pd.DataFrame:
    """The stored rows whose sum the aggregation moves.

    :param stored: The level's stored rows: ``key``, ``sample_peak_intensity_sum``.
    :param computed: The level's aggregated rows, the same columns.
    :param key: The level's natural key.
    :return: One row per stored row to write: ``key``, ``old`` (the stored
        sum) and ``new``. A computed row with no stored row is not one: this
        writes sums, it creates no match.
    """
    if stored.empty or computed.empty:
        return pd.DataFrame(columns=[*key, "old", "new"])
    merged = stored[[*key, "sample_peak_intensity_sum"]].merge(
        computed[[*key, "sample_peak_intensity_sum"]],
        on=list(key),
        suffixes=("_old", "_new"),
    )
    old = merged["sample_peak_intensity_sum_old"]
    new = merged["sample_peak_intensity_sum_new"]
    moved = new.notna() & ~(
        (old.isna() & new.isna())
        | (old.notna() & ((old - new).abs() <= 1e-9 * new.abs().clip(lower=1.0)))
    )
    return merged.loc[moved, list(key)].assign(old=old[moved], new=new[moved])


def changed_categories(stored: pd.DataFrame, computed: pd.DataFrame, key) -> int:
    """How many stored rows the aggregation gives another category: rows this
    script leaves as they are."""
    if stored.empty or computed.empty:
        return 0
    merged = stored[[*key, "match_category"]].merge(
        computed[[*key, "match_category"]], on=list(key), suffixes=("_old", "_new")
    )
    return int((merged["match_category_old"] != merged["match_category_new"]).sum())


def growth_factors(changes: pd.DataFrame) -> list[float]:
    """New sum over old, for the changed rows that had a sum to grow from."""
    grown = changes[changes["old"] > 0]
    return (grown["new"] / grown["old"]).tolist()


@dataclass
class Tally:
    """What a run read and wrote, for one batch or for all."""

    samples: int = 0
    changed: Counter = field(default_factory=Counter)
    written: Counter = field(default_factory=Counter)
    moved: Counter = field(default_factory=Counter)
    categories: int = 0
    factors: list = field(default_factory=list)

    def add(self, other: "Tally") -> None:
        self.samples += other.samples
        self.changed.update(other.changed)
        self.written.update(other.written)
        self.moved.update(other.moved)
        self.categories += other.categories
        self.factors.extend(other.factors)

    def line(self, dry_run: bool) -> str:
        levels = ", ".join(
            f"{level.name} {self.changed[level.name]}"
            + ("" if dry_run else f" (written {self.written[level.name]})")
            for level in LEVELS
        )
        moved = sum(self.moved.values())
        factor = (
            f", ion sums x{pd.Series(self.factors).median():.3g} median"
            if self.factors
            else ""
        )
        return (
            f"{self.samples} samples read; sums that change: {levels}{factor}; "
            f"ion categories that would differ, left as stored: {self.categories}"
            + (f"; moved since read, left alone: {moved}" if moved else "")
        )


def _batch_size() -> int | None:
    """``BATCH_SIZE``, or None to size each batch's chunks as a batch match does."""
    raw = os.environ.get("BATCH_SIZE", "").strip()
    if not raw:
        return None
    try:
        size = int(raw)
    except ValueError as e:
        raise BadSetting(f"BATCH_SIZE must be a whole number, not {raw!r}") from e
    if size < 1:
        raise BadSetting(f"BATCH_SIZE must be at least 1, not {size}")
    return size


def _batches() -> set[str] | None:
    raw = os.environ.get("REAGGREGATE_BATCHES", "")
    batches = {part.strip() for part in raw.split(",") if part.strip()}
    return batches or None


async def candidates(batches: set[str] | None = None) -> dict[str, list[str]]:
    """The samples whose sums can change, per batch.

    :param batches: Batch ids to keep, or None for every batch.
    """
    async with async_session() as session:
        filter_params = (
            (
                await session.execute(
                    text(
                        "SELECT filter_params FROM target_ion "
                        "WHERE filter_params IS NOT NULL"
                    )
                )
            )
            .scalars()
            .all()
        )
        params = {"tolerance": smallest_ratio_tolerance(filter_params)}
        statement = text(
            _CANDIDATES_SQL.format(
                batches="" if batches is None else "AND si.sample_batch_id IN :batches"
            )
        )
        if batches is not None:
            statement = statement.bindparams(bindparam("batches", expanding=True))
            params["batches"] = sorted(batches)
        rows = (await session.execute(statement, params)).all()
    found = defaultdict(list)
    for batch_id, sample_item_id in rows:
        found[batch_id].append(sample_item_id)
    return dict(found)


async def _chunk_size(sample_batch_id: str, batch_size: int | None) -> int:
    """Samples aggregated at once in a batch: ``batch_size`` when set, else
    what a batch match of it would take."""
    if batch_size is not None:
        return batch_size
    async with async_session() as session:
        return await _resolve_aggregation_chunk_size(session, sample_batch_id)


async def _stored(level: Level, sample_item_ids: list[str]) -> pd.DataFrame:
    model = level.model
    columns = [getattr(model, name) for name in level.key] + [
        model.sample_peak_intensity_sum,
        model.match_category,
    ]
    async with async_session() as session:
        rows = (
            await session.execute(
                select(*columns).where(model.sample_item_id.in_(sample_item_ids))
            )
        ).all()
    return pd.DataFrame(
        rows, columns=[*level.key, "sample_peak_intensity_sum", "match_category"]
    )


async def _write(level: Level, changes: pd.DataFrame) -> int:
    """Write the new sums, where the stored sum is still the one read.

    :return: How many rows were written.
    """
    model = level.model
    statement = (
        update(model)
        .where(
            and_(
                *(getattr(model, name) == bindparam(f"k_{name}") for name in level.key),
                model.sample_peak_intensity_sum.is_not_distinct_from(bindparam("old")),
            )
        )
        .values(
            sample_peak_intensity_sum=bindparam("new"),
            **{level.modified: func.now()},
        )
        .execution_options(synchronize_session=False)
    )
    written = 0
    async with async_session() as session:
        await acquire_match_write_locks(session, set(changes["sample_item_id"]))
        for row in changes.to_dict("records"):
            params = {f"k_{name}": row[name] for name in level.key}
            params["old"] = None if pd.isna(row["old"]) else float(row["old"])
            params["new"] = float(row["new"])
            result = await session.execute(statement, params)
            written += result.rowcount
        await session.commit()
    return written


async def reaggregate_chunk(
    sample_batch_id: str, sample_item_ids: list[str], dry_run: bool
) -> Tally:
    """Re-aggregate one chunk of a batch's samples and write the sums that move."""
    tally = Tally(samples=len(sample_item_ids))
    # Read before the aggregation, so a sample matched again while it runs
    # shows as moved at the write
    stored = {level.name: await _stored(level, sample_item_ids) for level in LEVELS}
    aggregated = await aggregate_matches(
        sample_batch_id=sample_batch_id,
        sample_item_ids=sample_item_ids,
        as_frames=True,
    )
    frames = aggregated.get("data") or {}
    for level in LEVELS:
        computed = frames.get(level.name)
        if computed is None or computed.empty:
            continue
        changes = changed_sums(stored[level.name], computed, level.key)
        tally.changed[level.name] += len(changes)
        if level.name == "match_ions":
            tally.categories += changed_categories(
                stored[level.name], computed, level.key
            )
            tally.factors.extend(growth_factors(changes))
        if dry_run or changes.empty:
            continue
        written = await _write(level, changes)
        tally.written[level.name] += written
        tally.moved[level.name] += len(changes) - written
    return tally


async def reaggregate(
    dry_run: bool, batch_size: int | None = None, batches: set[str] | None = None
) -> Tally:
    """Re-aggregate every candidate sample, batch by batch.

    :param dry_run: Read and report only.
    :param batch_size: Samples aggregated at once, or None to size each
        batch's chunks from its target set, as a batch match does.
    :param batches: Batch ids to keep, or None for every batch.
    :return: The totals.
    """
    found = await candidates(batches)
    total = Tally()
    if not found:
        runtime.logger.info("No sample holds a line beyond the ratio tolerance.")
        return total
    runtime.logger.info(
        f"{sum(len(ids) for ids in found.values())} samples in {len(found)} "
        "batches hold a line beyond the ratio tolerance"
        + (" (DRY_RUN=1: nothing is written)" if dry_run else "")
    )
    for sample_batch_id, sample_item_ids in found.items():
        tally = Tally()
        chunk = await _chunk_size(sample_batch_id, batch_size)
        for start in range(0, len(sample_item_ids), chunk):
            tally.add(
                await reaggregate_chunk(
                    sample_batch_id,
                    sample_item_ids[start : start + chunk],
                    dry_run,
                )
            )
        runtime.logger.info(f"Batch {sample_batch_id}: {tally.line(dry_run)}")
        total.add(tally)
    runtime.logger.info(f"Total: {total.line(dry_run)}")
    return total


async def run() -> None:
    await configure_database_engine()
    await reaggregate(
        dry_run=os.environ.get("DRY_RUN") == "1",
        batch_size=_batch_size(),
        batches=_batches(),
    )


def main() -> None:
    """Entry point for the script."""
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        runtime.logger.info("Re-aggregation was cancelled by user (Ctrl+C)")
    except Exception as e:
        runtime.logger.exception(f"Script failed: {e}")
        raise


if __name__ == "__main__":
    main()
