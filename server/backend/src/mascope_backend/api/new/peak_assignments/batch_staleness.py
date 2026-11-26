"""The batch stages' names, their states, and the marks that make them stale.

What the stages and their orchestrator (``batch_stages``) share, below both: a
stage's name and the order every pass runs the stages in, what a stage's row
says of it, why it is stale, and the marks a writer of the ledger leaves on
the stages that read what it wrote - a fold, a rebuild, an import, a pin, a
consensus recompute. A stage marks the batch stale through this module and
never imports the orchestrator.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from datetime import datetime, timezone
from enum import StrEnum
from typing import Optional

from sqlalchemy import case, select, update

from mascope_backend.api.new.peak_assignments.batch_lock import _acquire_batch_fold_lock
from mascope_backend.db import BatchPeakStage, async_session
from mascope_backend.runtime import runtime


class BatchStage(StrEnum):
    """A batch stage, in the order every pass runs them."""

    WATER_LADDERS = "water_ladders"
    KNOWN_SPECIES = "known_species"
    SEARCH = "search"
    ISOTOPE_LINES = "isotope_lines"
    SERIES = "series"
    CO_VARIATION = "co_variation"
    CONTINUITY = "continuity"
    BATCH_CHECKS = "batch_checks"
    BLENDS = "blends"
    LEVEL = "level"


#: What a stage's row says of it.
STATE_CURRENT = "current"
STATE_RUNNING = "running"
STATE_STALE = "stale"
#: What the ledger says of a stage with no row: it never ran on the batch.
STATE_NOT_RUN = "not_run"

#: Why a stage is stale.
REASON_FOLD = "fold"
REASON_REBUILD = "rebuild"
#: An engine's batch result imported (``batch_import.py``).
REASON_IMPORT = "import"
#: A person's pin on a batch peak, or its release (``batch_curation.py``).
REASON_CURATION = "curation"
REASON_INTERRUPTED = "interrupted"
#: A step that skipped samples without saying which (``count_unread``).
REASON_PARTIAL = "partial"
#: A sample the stage skipped, whose peak list did not read then, reads now
#: (``batch_skipped``).
REASON_READ_AGAIN = "read_again"
REASON_CONSENSUS = "consensus"
#: An earlier stage ran again: ``upstream:<stage>``.
REASON_UPSTREAM = "upstream"


#: The batch peak provenance keys of the records each stage writes, each
#: stamped with the run's version (``batch_stages.StageSpec.record_keys``).
STAGE_RECORD_KEYS: dict[BatchStage, tuple[str, ...]] = {
    BatchStage.WATER_LADDERS: ("water_ladder",),
    BatchStage.KNOWN_SPECIES: ("known_species",),
    BatchStage.SEARCH: ("persistence",),
    BatchStage.ISOTOPE_LINES: (
        "merged_isotopologue_of",
        "merged_isotopologues",
        "diagnostic_isotopologues",
        "stamped_isotopologue_of",
    ),
    BatchStage.SERIES: ("series",),
    BatchStage.CO_VARIATION: ("co_variation", "flat_series"),
    BatchStage.CONTINUITY: (),
    BatchStage.BATCH_CHECKS: ("batch_checks",),
    BatchStage.BLENDS: (),
    BatchStage.LEVEL: ("identification_level",),
}

#: The stage that wrote each provenance key's records.
STAGE_OF_KEY: dict[str, BatchStage] = {
    key: stage for stage, keys in STAGE_RECORD_KEYS.items() for key in keys
}


#: The key a step's outcome data names the samples it could not read under,
#: beside their count ``samples_failed`` (:func:`count_unread`).
SAMPLES_UNREAD = "samples_unread"


# --- pure parts -------------------------------------------------------------------


def count_unread(counts: dict, sample_item_id: str) -> None:
    """Count a sample a step could not read and skipped, and name it: the
    stage records the samples it skipped and is current with them
    (``batch_skipped``). A sample skipped twice in one step is named once."""
    counts["samples_failed"] = counts.get("samples_failed", 0) + 1
    named = counts.setdefault(SAMPLES_UNREAD, [])
    if sample_item_id not in named:
        named.append(sample_item_id)


def stages_from(first: BatchStage | str) -> tuple[BatchStage, ...]:
    """The stages from ``first`` to the end, in order."""
    names = list(BatchStage)
    return tuple(names[names.index(BatchStage(first)) :])


# --- the stages' standing ---------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _fold_lock(session, sample_batch_id: str) -> None:
    """The batch's fold lock, taken before any stage row is written.

    A fold marks the stage rows inside its own transaction, under this lock,
    and so does a consensus recompute. A writer of more than one stage row
    that took the rows' own locks first could lock them in another order than
    the fold, and Postgres would abort one of the two to break the deadlock.
    Taken first by every writer, the lock orders them all.
    """
    await _acquire_batch_fold_lock(session, sample_batch_id)


async def mark_stale(
    session,
    sample_batch_id: str,
    stages: Iterable[BatchStage | str],
    reason: str,
    *,
    at: Optional[datetime] = None,
    skip_running: bool = False,
) -> None:
    """Mark stages of a batch stale, on the caller's session and inside its
    transaction: the fold's, which writes what they read, and which holds
    the batch's fold lock (:func:`_fold_lock`).

    Only a stage that has run has a row to mark; one that never ran is not
    stale, it is not run. A running stage keeps running, and the mark is what
    its end reads (:func:`finish_stage`): it ends stale where something moved
    after it started. With ``skip_running`` a running stage is not marked at
    all: a consensus recompute's mark, which a running stage's own writes
    set off.
    """
    names = [BatchStage(stage).value for stage in stages]
    if not names:
        return
    conditions = [
        BatchPeakStage.sample_batch_id == sample_batch_id,
        BatchPeakStage.stage.in_(names),
    ]
    if skip_running:
        conditions.append(BatchPeakStage.state != STATE_RUNNING)
    await session.execute(
        update(BatchPeakStage)
        .where(*conditions)
        .values(
            state=case(
                (BatchPeakStage.state == STATE_RUNNING, STATE_RUNNING),
                else_=STATE_STALE,
            ),
            stale_reason=reason,
            utc_stale=at or _now(),
        )
        .execution_options(synchronize_session=False)
    )


async def mark_every_stage_stale(session, sample_batch_id: str, reason: str) -> None:
    """A fold's mark: every stage reads the members it rewrote."""
    await mark_stale(session, sample_batch_id, tuple(BatchStage), reason)


async def mark_batch_stale(sample_batch_id: str, reason: str) -> None:
    """Mark every stage of a batch stale, in a transaction of its own under
    the fold lock: the mark of a writer whose rewrites commit sample by
    sample, a rebuild, an import, a pin. Shielded, and logged rather than
    raised where it fails: it is written while an interruption unwinds too,
    and must not replace what interrupted it."""

    async def write() -> None:
        async with async_session() as session:
            await _fold_lock(session, sample_batch_id)
            await mark_every_stage_stale(session, sample_batch_id, reason)
            await session.commit()

    try:
        await asyncio.shield(write())
    except Exception as exc:  # noqa: BLE001 - never replace what interrupted it
        runtime.logger.warning(
            f"Could not mark the batch stages of batch '{sample_batch_id}' stale "
            f"({reason}): {exc}"
        )


async def stage_states(session, sample_batch_id: str) -> dict[str, BatchPeakStage]:
    """The rows of a batch's stages, by stage name."""
    rows = (
        (
            await session.execute(
                select(BatchPeakStage).where(
                    BatchPeakStage.sample_batch_id == sample_batch_id
                )
            )
        )
        .scalars()
        .all()
    )
    return {row.stage: row for row in rows}


__all__ = [
    "BatchStage",
    "SAMPLES_UNREAD",
    "STAGE_OF_KEY",
    "count_unread",
    "mark_batch_stale",
    "mark_every_stage_stale",
    "mark_stale",
    "stage_states",
    "stages_from",
]
