"""Where a batch ledger row's level comes from: its members and their levels.

The batch states no level of its own yet (step 5.8c of the assignment quality
plan). The ledger's Level column shows the level most of a batch peak's
members' runs stated for the consensus formula
(``batch_peaks_records._with_member_levels``). That level is a set of
samples, and the set can hide better ones: 4a in three samples and 5a in
forty reads 5a. This read names the set, for one batch peak, on the reader's
click: each member whose run row commits the consensus formula and states a
level, with that level's evidence and lift, and how many members state none
and why. It is asked per batch peak, never put on the ledger's list read,
whose member levels are already a cost (MAS-1160).

It states no level and changes none. A member counts as the ledger counts
it, so the split it serves is the one the ledger's hover shows.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field
from sqlalchemy import select

from mascope_backend.api.lib.api_features import api_controller
from mascope_backend.api.lib.exceptions.api_exceptions import NotFoundException
from mascope_backend.api.new.peak_assignments.identification_level import (
    IDENTIFICATION_LEVEL,
    LEVELS,
    is_level,
    level_lines,
    members_level,
)
from mascope_backend.db import (
    BatchPeak,
    BatchPeakOccurrence,
    PeakAssignment,
    SampleFile,
    SampleItem,
    async_session,
)


#: Why a member states no level of the consensus, keyed as the read serves it.
UNSTATED = ("no_run", "no_formula", "other_formula", "no_level")


class BatchPeakLevelSourcesResponse(BaseModel):
    """The members of one batch peak and the levels their runs stated."""

    status: str = Field(description="Response status")
    message: str = Field(description="Response message")
    results: int = Field(description="Members listed: those stating a level")
    data: list[dict] = Field(
        description=(
            "The members whose run row commits the consensus formula and "
            "states a level, brightest first: ``sample_item_id``, "
            "``sample_item_name``, ``datetime_utc`` (the acquisition time), "
            "``sample_peak_id``, ``peak_assignment_id``, ``intensity``, "
            "``level``, ``level_evidence`` and ``level_lift``."
        )
    )
    batch_peak_id: str = Field(description="The batch peak")
    consensus_formula: Optional[str] = Field(description="Its consensus formula")
    n_members: int = Field(description="Its members, with a level or without")
    member_levels: Optional[dict[str, int]] = Field(
        description="Level -> members stating it, in the scale's order; None "
        "where none does (the ledger's ``member_levels``)"
    )
    member_level: Optional[str] = Field(
        description="The level most of them state, a tie reading as the "
        "weaker level (the ledger's ``member_level``)"
    )
    unstated: dict[str, int] = Field(
        description=(
            "Members stating no level of the consensus, by why: ``no_run`` "
            "(no run row of their own), ``no_formula`` (the row commits no "
            "formula), ``other_formula`` (it commits another) and "
            "``no_level`` (it commits the consensus but states no level of "
            "the scale)."
        )
    )


def _why_unstated(row, consensus: Optional[str]) -> Optional[str]:
    """Why a member states no level of the consensus, or None where it does.

    :param row: One member as :func:`get_batch_peak_level_sources` reads it.
    :param consensus: The batch peak's consensus formula.
    """
    if row.peak_assignment_id is None:
        return "no_run"
    if row.assigned_formula is None:
        return "no_formula"
    if consensus is None or row.assigned_formula != consensus:
        return "other_formula"
    if not is_level(row.level):
        return "no_level"
    return None


async def _members(session, batch_peak_id: str) -> list:
    """Every member of the batch peak with its sample, its acquisition time
    and the run row it was folded from, where that row still exists."""
    query = (
        select(
            BatchPeakOccurrence.sample_item_id,
            BatchPeakOccurrence.sample_peak_id,
            BatchPeakOccurrence.peak_assignment_id,
            BatchPeakOccurrence.intensity,
            SampleItem.sample_item_name,
            SampleFile.datetime_utc,
            PeakAssignment.assigned_formula,
            PeakAssignment.level,
            PeakAssignment.provenance[IDENTIFICATION_LEVEL].label("block"),
        )
        .join(
            SampleItem, SampleItem.sample_item_id == BatchPeakOccurrence.sample_item_id
        )
        .outerjoin(SampleFile, SampleFile.sample_file_id == SampleItem.sample_file_id)
        .outerjoin(
            PeakAssignment,
            PeakAssignment.peak_assignment_id == BatchPeakOccurrence.peak_assignment_id,
        )
        .where(BatchPeakOccurrence.batch_peak_id == batch_peak_id)
    )
    return (await session.execute(query)).all()


def _stating(row) -> dict:
    """A member stating a level, as the read serves it."""
    return {
        "sample_item_id": row.sample_item_id,
        "sample_item_name": row.sample_item_name,
        "datetime_utc": row.datetime_utc.isoformat() if row.datetime_utc else None,
        "sample_peak_id": row.sample_peak_id,
        "peak_assignment_id": row.peak_assignment_id,
        "intensity": row.intensity,
        "level": row.level,
        **level_lines({IDENTIFICATION_LEVEL: row.block}),
    }


@api_controller()
async def get_batch_peak_level_sources(
    sample_batch_id: str, batch_peak_id: str
) -> dict:
    """One batch peak's members with the levels their runs stated.

    A member states a level where its run row commits the consensus formula
    and carries a level of the scale: the ledger's member levels count
    exactly these. The rest are counted by why they state none.

    :param sample_batch_id: The batch the batch peak is in.
    :param batch_peak_id: The batch peak.
    :raises NotFoundException: No such batch peak in that batch.
    :return: The members stating a level, brightest first, and the counts.
    """
    async with async_session() as session:
        anchor = (
            await session.execute(
                select(BatchPeak.batch_peak_id, BatchPeak.consensus_formula).where(
                    BatchPeak.batch_peak_id == batch_peak_id,
                    BatchPeak.sample_batch_id == sample_batch_id,
                )
            )
        ).first()
        if anchor is None:
            raise NotFoundException(
                f"No batch peak '{batch_peak_id}' in batch '{sample_batch_id}'"
            )
        rows = await _members(session, batch_peak_id)

    unstated = dict.fromkeys(UNSTATED, 0)
    data: list[dict] = []
    for row in rows:
        why = _why_unstated(row, anchor.consensus_formula)
        if why is None:
            data.append(_stating(row))
        else:
            unstated[why] += 1
    # Brightest first, a member without an intensity last; the sample id
    # keeps equal intensities in one order from read to read.
    data.sort(
        key=lambda m: (
            m["intensity"] is None,
            -(m["intensity"] or 0),
            m["sample_item_id"],
        )
    )
    counts: dict[str, int] = {}
    for member in data:
        counts[member["level"]] = counts.get(member["level"], 0) + 1
    return {
        "status": "success",
        "message": (
            f"{len(data)} of {len(rows)} member{'s' if len(rows) != 1 else ''} "
            f"of batch peak '{batch_peak_id}' state a level"
        ),
        "results": len(data),
        "data": data,
        "batch_peak_id": anchor.batch_peak_id,
        "consensus_formula": anchor.consensus_formula,
        "n_members": len(rows),
        "member_levels": (
            {level: counts[level] for level in LEVELS if level in counts}
            if counts
            else None
        ),
        "member_level": members_level(counts),
        "unstated": unstated,
    }
