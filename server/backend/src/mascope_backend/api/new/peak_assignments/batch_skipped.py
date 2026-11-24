"""The samples a batch stage could not read, and whether they read again.

A stage that cannot read one of the batch's samples (a moved peak file, a
spectrum that does not read) skips it and goes on with the rest. It used to
be stale for that alone (``partial``) until a run read every sample, so every
later step ran it again first, the search above all, with the same sample
failing each time (mascope#3356). Decided in MAS-1060: a stage is current
while the samples it cannot read are the ones it skipped at its last run.

- A stage's steps name the samples they skip (``samples_unread`` beside the
  count ``samples_failed``, ``batch_staleness.count_unread``), and the
  stage's row records them as its run ends (:func:`skipped_record`), each
  with whether its peak list read then: the engine's own read of the sample
  (``sample_inputs.load_sample_peaks``).
- The stage ends current with them. The batch's assignment status names them
  per stage, and Details lists them.
- A skipped sample whose peak list did not read then and reads now is a
  change in that set: its file is back. The stage is stale, ``read_again``
  (:func:`stage_rows`), and the next pass runs it, from there on.
- A sample the stage skipped though its peak list read (a failure further
  in) never marks it so: the peak list reads every time, and a mark on that
  would run the stage again on every press, as before.
- A new sample that does not read reaches the batch through a fold, which
  marks every stage stale: the run after it records the new set.
- A step that counts skipped samples without naming them leaves the stage
  stale, ``partial``, as before: nothing says which samples to wait for.

Only the samples a stage recorded as unread are read again, and only for a
current stage, so a batch with every sample read reads no file here.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Iterable
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select

from mascope_backend.api.new.peak_assignments.batch_staleness import (
    REASON_READ_AGAIN,
    SAMPLES_UNREAD,
    STATE_CURRENT,
    STATE_STALE,
    _now,
    stage_states,
)
from mascope_backend.api.new.peak_assignments.sample_inputs import load_sample_peaks
from mascope_backend.db import Sample, async_session
from mascope_backend.runtime import runtime


#: The keys of one skipped sample in a stage's record.
SKIPPED_ID = "sample_item_id"
SKIPPED_NAME = "sample_item_name"
PEAKS_READ = "peaks_read"


def skipped_in(outcomes: Iterable[Any]) -> tuple[list[str], bool]:
    """The samples a stage's steps say they could not read, sorted, and
    whether a step counted skipped samples without naming them.

    :param outcomes: The steps' outcomes, each with its counts in ``data``.
    """
    named: set[str] = set()
    unnamed = False
    for outcome in outcomes:
        data = outcome.get("data") if isinstance(outcome, dict) else None
        if not isinstance(data, dict):
            continue
        ids = data.get(SAMPLES_UNREAD)
        if isinstance(ids, list) and ids:
            named.update(str(i) for i in ids)
        elif data.get("samples_failed"):
            unnamed = True
    return sorted(named), unnamed


def _peaks_read(sample: Sample) -> bool:
    try:
        load_sample_peaks(sample)
    except Exception:  # noqa: BLE001 - any failure is a sample that does not read
        return False
    return True


async def _samples(sample_item_ids: Iterable[str]) -> dict[str, Sample]:
    ids = sorted(set(sample_item_ids))
    if not ids:
        return {}
    async with async_session() as session:
        rows = (
            (
                await session.execute(
                    select(Sample).where(Sample.sample_item_id.in_(ids))
                )
            )
            .scalars()
            .all()
        )
    return {row.sample_item_id: row for row in rows}


async def peaks_read(sample_item_ids: Iterable[str]) -> dict[str, bool]:
    """Whether each sample's peak list reads now, off the event loop: the
    engine's own read of it. A sample gone from the batch reads as not read.
    """
    ids = sorted(set(sample_item_ids))
    samples = await _samples(ids)
    reads = {sample_item_id: False for sample_item_id in ids}
    for sample_item_id, sample in samples.items():
        reads[sample_item_id] = await asyncio.to_thread(_peaks_read, sample)
    return reads


async def skipped_record(sample_item_ids: list[str]) -> Optional[list[dict]]:
    """What a stage's row records of the samples its run skipped: each with
    whether its peak list read as the run ended; None where it skipped none.
    """
    if not sample_item_ids:
        return None
    reads = await peaks_read(sample_item_ids)
    return [
        {SKIPPED_ID: sample_item_id, PEAKS_READ: reads[sample_item_id]}
        for sample_item_id in sorted(set(sample_item_ids))
    ]


def skipped_ids(record: Any, *, unread_only: bool = False) -> list[str]:
    """The sample ids of a stage's record of what it skipped; with
    ``unread_only``, those whose peak list did not read as its run ended."""
    if not isinstance(record, list):
        return []
    return [
        str(entry[SKIPPED_ID])
        for entry in record
        if isinstance(entry, dict)
        and entry.get(SKIPPED_ID)
        and not (unread_only and entry.get(PEAKS_READ))
    ]


async def read_again(rows: dict[str, Any]) -> dict[str, list[str]]:
    """Stage -> the samples it skipped whose peak list did not read as its
    run ended and reads now. Only a current stage's: a stale one runs again
    anyway, and a running one records its own set as it ends.

    :param rows: Stage name -> its row.
    """
    waiting = {
        stage: skipped_ids(row.skipped_samples, unread_only=True)
        for stage, row in rows.items()
        if row.state == STATE_CURRENT
    }
    ids = {sample_item_id for named in waiting.values() for sample_item_id in named}
    if not ids:
        return {}
    reads = await peaks_read(ids)
    found = {
        stage: [sample_item_id for sample_item_id in named if reads[sample_item_id]]
        for stage, named in waiting.items()
    }
    return {stage: named for stage, named in found.items() if named}


@dataclasses.dataclass(frozen=True)
class StageView:
    """A stage's row as a pass plans from it and the status reads it: the
    row's own fields, its skipped samples named, and its state as the
    skipped samples' files leave it (:func:`stage_rows`)."""

    stage: str
    version: int
    state: str
    stale_reason: Optional[str]
    utc_started: Optional[datetime]
    utc_ran: Optional[datetime]
    utc_stale: Optional[datetime]
    batch_peak_run_id: Optional[str]
    skipped_samples: Optional[list]


def stage_view(
    row: Any, names: dict[str, str], again: Optional[list[str]] = None
) -> StageView:
    """A stage's row as a view: its skipped samples with their names, and
    stale ``read_again`` where ``again`` names samples of it that read now.
    """
    fields = {f.name: getattr(row, f.name) for f in dataclasses.fields(StageView)}
    if isinstance(row.skipped_samples, list):
        fields["skipped_samples"] = [
            {**entry, SKIPPED_NAME: names.get(str(entry.get(SKIPPED_ID)))}
            for entry in row.skipped_samples
            if isinstance(entry, dict)
        ]
    if again:
        fields.update(state=STATE_STALE, stale_reason=REASON_READ_AGAIN)
        fields["utc_stale"] = _now()
    return StageView(**fields)


async def stage_rows(session, sample_batch_id: str) -> dict[str, Any]:
    """The rows of a batch's stages as a pass plans from them and the
    batch's status reads them: a current stage one of whose skipped samples
    reads again is stale (``read_again``), and each skipped sample carries
    its name. Nothing is written: the stage's next run writes its standing.

    :param session: The caller's session, which reads the rows.
    """
    rows = await stage_states(session, sample_batch_id)
    skipped = {
        name: skipped_ids(row.skipped_samples)
        for name, row in rows.items()
        if row.skipped_samples
    }
    if not skipped:
        return rows
    again = await read_again(rows)
    samples = await _samples(i for ids in skipped.values() for i in ids)
    names = {i: sample.sample_item_name for i, sample in samples.items()}
    for stage, ids in again.items():
        runtime.logger.info(
            f"Batch stage '{stage}' of batch '{sample_batch_id}' is stale: "
            f"{len(ids)} sample(s) it skipped read again."
        )
    return {
        name: stage_view(row, names, again.get(name)) if name in skipped else row
        for name, row in rows.items()
    }


__all__ = [
    "PEAKS_READ",
    "SKIPPED_ID",
    "SKIPPED_NAME",
    "StageView",
    "peaks_read",
    "read_again",
    "skipped_ids",
    "skipped_in",
    "skipped_record",
    "stage_rows",
    "stage_view",
]
