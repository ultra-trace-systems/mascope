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

Its sibling, :func:`get_batch_peak_member_readings`, serves the same members whatever
their row reads (MAS-1315): every member, on the consensus or another formula
or none, with its run row's reading and level, and what the batch did to that
row. Both read one query (:func:`_members`), so the two agree on who the
members are.

What the batch did to a row is read off the marks the row carries, never
inferred:

- **the batch rewrote it in place**: the known-species claim and the water
  ladder keep what the row read before under ``provenance.batch_written_before``
  (``batch_known_species.with_row_before``), the claim's reason beside it
  under ``provenance.reagent.reason`` (a library ion, a rung) or its decision
  under ``provenance.batch_known_species`` (a listed compound); the merged
  isotopologues' strip writes ``provenance.batch_isotopologue`` with no
  ``version`` (``batch_lines.read_as_batch_isotopologue``);
- **the sample's run read the batch's decision** (``batch_decisions``): the
  same blocks, written by the run itself and citing the stage's ``version``,
  and the series stamp, ``provenance.series`` (``batch_lines.stamp_series``).

A batch pin, the batch search and the series stage rewrite members, never
rows. The member's own mark (``batch_written``, ``batch_peaks.batch_mark``)
says which stage rewrote it; a pin takes that mark off and leaves none.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field
from sqlalchemy import select

from mascope_backend.api.lib.api_features import api_controller
from mascope_backend.api.lib.exceptions.api_exceptions import NotFoundException
from mascope_backend.api.new.peak_assignments.batch_known_species import (
    ROW_BEFORE_KEY,
)
from mascope_backend.api.new.peak_assignments.batch_lines import BATCH_ISOTOPOLOGUE
from mascope_backend.api.new.peak_assignments.batch_peaks import SERIES_KEY
from mascope_backend.api.new.peak_assignments.batch_staleness import BatchStage
from mascope_backend.api.new.peak_assignments.identification_level import (
    IDENTIFICATION_LEVEL,
    LEVELS,
    is_level,
    level_lines,
    members_level,
)
from mascope_backend.api.new.peak_assignments.reagent_pass import (
    REASON_BATCH_KNOWN_SPECIES,
    REASON_BATCH_WATER_LADDER,
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


def _mark(path: tuple[str, ...]):
    """A path into a run row's provenance, as stored JSON: SQL NULL where the
    row carries nothing there."""
    return PeakAssignment.provenance[path]


async def _members(session, batch_peak_id: str) -> list:
    """Every member of the batch peak with its sample, its acquisition time
    and the run row it was folded from, where that row still exists: the
    row's reading, its level, and the marks the batch's decisions leave on it
    (:func:`_batch_marks`), each path read alone so no row's whole provenance
    is carried."""
    reagent = (
        _mark(("reagent", REASON_BATCH_KNOWN_SPECIES, "version")).label(
            "reagent_known_species_version"
        ),
        _mark(("reagent", REASON_BATCH_WATER_LADDER, "version")).label(
            "reagent_water_ladder_version"
        ),
    )
    query = (
        select(
            BatchPeakOccurrence.sample_item_id,
            BatchPeakOccurrence.sample_peak_id,
            BatchPeakOccurrence.peak_assignment_id,
            BatchPeakOccurrence.intensity,
            BatchPeakOccurrence.batch_written[("stage",)]
            .as_string()
            .label("member_stage"),
            SampleItem.sample_item_name,
            SampleFile.datetime_utc,
            PeakAssignment.assigned_formula,
            PeakAssignment.ion_formula,
            PeakAssignment.tier,
            PeakAssignment.role,
            PeakAssignment.level,
            PeakAssignment.provenance[IDENTIFICATION_LEVEL].label("block"),
            _mark((ROW_BEFORE_KEY,)).isnot(None).label("rewritten"),
            _mark(("reagent", "reason")).as_string().label("reagent_reason"),
            *reagent,
            _mark((REASON_BATCH_KNOWN_SPECIES,)).isnot(None).label("listed"),
            _mark((REASON_BATCH_KNOWN_SPECIES, "version")).label("listed_version"),
            _mark((BATCH_ISOTOPOLOGUE,)).isnot(None).label("strip"),
            _mark((BATCH_ISOTOPOLOGUE, "version")).label("strip_version"),
            _mark((SERIES_KEY,)).isnot(None).label("series"),
            _mark((SERIES_KEY, "version")).label("series_version"),
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


async def _anchor(session, sample_batch_id: str, batch_peak_id: str):
    """The batch peak and its consensus formula, where it is in the batch.

    :raises NotFoundException: No such batch peak in that batch.
    """
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
    return anchor


def _brightest_first(members: list[dict]) -> list[dict]:
    """Brightest first, a member without an intensity last; the sample id
    keeps equal intensities in one order from read to read."""
    return sorted(
        members,
        key=lambda m: (
            m["intensity"] is None,
            -(m["intensity"] or 0),
            m["sample_item_id"],
        ),
    )


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
        anchor = await _anchor(session, sample_batch_id, batch_peak_id)
        rows = await _members(session, batch_peak_id)

    unstated = dict.fromkeys(UNSTATED, 0)
    data: list[dict] = []
    for row in rows:
        why = _why_unstated(row, anchor.consensus_formula)
        if why is None:
            data.append(_stating(row))
        else:
            unstated[why] += 1
    data = _brightest_first(data)
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


# --- every member, any formula (MAS-1315) ----------------------------------------


class BatchPeakMemberReadingsResponse(BaseModel):
    """Every member of one batch peak, what its run row reads, and what the
    batch's decisions did to that row."""

    status: str = Field(description="Response status")
    message: str = Field(description="Response message")
    results: int = Field(description="Members listed: all of them")
    data: list[dict] = Field(
        description=(
            "Every member, brightest first: ``sample_item_id``, "
            "``sample_item_name``, ``datetime_utc`` (the acquisition time), "
            "``sample_peak_id``, ``peak_assignment_id``, ``intensity``; from "
            "its run row ``assigned_formula``, ``ion_formula``, ``tier``, "
            "``role``, ``level``, ``level_evidence`` and ``level_lift``, all "
            "None for a member with no run row; ``unstated``, why it states no "
            "level of the consensus (the levels read's keys) or None where it "
            "states one; ``batch_stage``, the batch stage that rewrote its run "
            "row in place (``known_species``, ``water_ladders``, "
            "``isotope_lines``) or None; ``batch_read``, the batch decisions "
            "its run read onto the row, each ``{stage, version}``; and "
            "``member_stage``, the batch stage whose mark the member itself "
            "carries, or None."
        )
    )
    batch_peak_id: str = Field(description="The batch peak")
    consensus_formula: Optional[str] = Field(description="Its consensus formula")
    n_members: int = Field(description="Its members")
    unstated: dict[str, int] = Field(
        description="Members stating no level of the consensus, by why, as "
        "the levels read counts them"
    )
    batch_stages: dict[str, int] = Field(
        description="Members whose run row a batch stage rewrote, per stage"
    )


def _batch_marks(row) -> tuple[Optional[str], list[dict]]:
    """What the batch's decisions did to a member's run row, off its marks.

    :param row: One member as :func:`_members` reads it.
    :return: The stage that rewrote the row in place, or None; and the
        decisions the sample's run read onto the row, each with the stage
        and the version the row cites, in the stages' order.
    """
    reagent = {
        REASON_BATCH_KNOWN_SPECIES: (
            BatchStage.KNOWN_SPECIES,
            row.reagent_known_species_version,
        ),
        REASON_BATCH_WATER_LADDER: (
            BatchStage.WATER_LADDERS,
            row.reagent_water_ladder_version,
        ),
    }.get(row.reagent_reason)
    # Each mark, the block it lives in, and its version where it has one.
    marks: list[tuple[str, BatchStage, object]] = []
    if reagent is not None:
        marks.append(("reagent", *reagent))
    if row.listed:
        marks.append(("listed", BatchStage.KNOWN_SPECIES, row.listed_version))
    if row.strip:
        marks.append(("strip", BatchStage.ISOTOPE_LINES, row.strip_version))
    if row.series:
        marks.append(("series", BatchStage.SERIES, row.series_version))

    rewrote: Optional[str] = None
    if row.rewritten:
        # The known-species claim's or the water ladder's rewrite: the row
        # carries what it read before, and the claim's block beside it.
        rewrote = next(
            (block for block, _, _ in marks if block in ("reagent", "listed")),
            None,
        )
    elif row.strip and row.strip_version is None:
        # The strip's own rewrite cites no version; a run's reading does.
        rewrote = "strip"
    stage = next((str(stage) for block, stage, _ in marks if block == rewrote), None)
    # The blocks are gathered in the stages' order, so the reads are too.
    read = [
        {"stage": str(stage), "version": version}
        for block, stage, version in marks
        if block != rewrote
    ]
    return stage, read


def _member(row, consensus: Optional[str]) -> dict:
    """One member as the members read serves it."""
    stage, read = _batch_marks(row)
    return {
        "sample_item_id": row.sample_item_id,
        "sample_item_name": row.sample_item_name,
        "datetime_utc": row.datetime_utc.isoformat() if row.datetime_utc else None,
        "sample_peak_id": row.sample_peak_id,
        "peak_assignment_id": row.peak_assignment_id,
        "intensity": row.intensity,
        "assigned_formula": row.assigned_formula,
        "ion_formula": row.ion_formula,
        "tier": row.tier,
        "role": row.role,
        "level": row.level,
        **level_lines({IDENTIFICATION_LEVEL: row.block}),
        "unstated": _why_unstated(row, consensus),
        "batch_stage": stage,
        "batch_read": read,
        "member_stage": row.member_stage,
    }


@api_controller()
async def get_batch_peak_member_readings(
    sample_batch_id: str, batch_peak_id: str
) -> dict:
    """Every member of one batch peak, whatever its run row reads.

    The members the levels read lists, and the ones it only counts: a member
    on another formula, on none, with no level, or with no run row, each with
    its reading and why it states no level of the consensus. Per member, the
    batch stage that rewrote its run row, the batch decisions its run read
    onto the row, and the stage whose mark the member carries.

    :param sample_batch_id: The batch the batch peak is in.
    :param batch_peak_id: The batch peak.
    :raises NotFoundException: No such batch peak in that batch.
    :return: Every member, brightest first, and the counts.
    """
    async with async_session() as session:
        anchor = await _anchor(session, sample_batch_id, batch_peak_id)
        rows = await _members(session, batch_peak_id)

    data = _brightest_first([_member(row, anchor.consensus_formula) for row in rows])
    unstated = dict.fromkeys(UNSTATED, 0)
    stages: dict[str, int] = {}
    for member in data:
        if member["unstated"] is not None:
            unstated[member["unstated"]] += 1
        if member["batch_stage"] is not None:
            stages[member["batch_stage"]] = stages.get(member["batch_stage"], 0) + 1
    order = list(BatchStage)
    return {
        "status": "success",
        "message": (
            f"{len(data)} member{'s' if len(data) != 1 else ''} "
            f"of batch peak '{batch_peak_id}'"
        ),
        "results": len(data),
        "data": data,
        "batch_peak_id": anchor.batch_peak_id,
        "consensus_formula": anchor.consensus_formula,
        "n_members": len(data),
        "unstated": unstated,
        "batch_stages": {
            str(stage): stages[stage] for stage in order if stage in stages
        },
    }
