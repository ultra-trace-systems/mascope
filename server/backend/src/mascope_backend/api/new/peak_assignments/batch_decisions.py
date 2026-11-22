"""What a sample's batch decided about it, read by the sample's run (MAS-985).

The batch stages decide things about a sample that its own run cannot see on
one file (``batch_stages.py``): a source ion present across the partition, a
rung of a reagent's water ladder in an acquisition segment, a listed compound
present, a merged row that is another row's isotope line, a homologous
series. Each stage writes its decision as a versioned record on the batch
peak it is about, and rewrites the members it takes. A sample assigned again
afterwards reads its peaks afresh, so a run that read none of the records
dropped what the batch decided, and its fold brought the dropped reading back
into the batch peak as a dissenting member until the stage ran again.

So a sample's run reads them, once, as an input the stage list declares
(:func:`read_batch_decisions`, called in its resolve stage), and each kind of
record enters the stage list at the stage that decides what it decides
(``pipeline.BATCH_INPUTS``):

- **a known species**, a library ion the batch decided present
  (``batch_known_species``), and **a water-ladder rung** the batch passed in
  the segment the sample was acquired in (``batch_water_ladder``), are the
  source's: the claim stage claims them on the sample's corrected mass where
  its own claim did not (``reagent_pass.claim_batch_species``). A sample
  acquired outside every segment the rung passed in reads no rung: a rung is
  evidence about the segment it was measured in (decided 2026-11-17).
- **a listed compound** the batch decided present is proposed for its peak,
  and elected as the batch's own claim takes a member
  (:func:`elect_listed`): the list's reading at candidate, citing the
  decision, where the stages read the peak another way or not at all.
- **an isotopologue strip** reads the row on the stripped peak as the parent's
  line before the judging (:func:`read_strips`), and **a series** stamps its
  record on the row that reads its formula (``batch_lines.stamp_series``): the
  judging then reads both.

A decoy arm claims what its control claims, so it reads the source ions and
the listed compounds, and reads neither a strip nor a series: those are the
control's chemistry, and the arm's own lines decide. The run records the
version of every batch stage whose records it read (:meth:`BatchDecisions
.record`), so the batch's staleness rule can tell when a run read an older
decision (MAS-983, MAS-1013).

The listed compound and the strip are read through the sample's own members,
so they reach a sample the batch already holds: a sample folded in for the
first time is brought up to them by the next pass of the stages, as every
new member is (``batch_assignment_one_action.md``, 4.3). So is a sample whose
peaks were detected again: its members name the old peaks until its next
fold, and a decision on a peak its run does not hold is left
(:data:`PEAK_ABSENT`). A rung is claimed by mass, and reaches a sample folded
in for the first time too.

Design: ``docs/dev/batch_assignment_one_action.md``, section 5.1, and
``docs/dev/assignment_quality_plan.md``, milestone 7 (MAS-974, finding 1).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

import pandas as pd
from sqlalchemy import or_, select

from mascope_backend.api.new.peak_assignments import batch_lines, known_species
from mascope_backend.api.new.peak_assignments.batch_lines import (
    BatchLine,
    read_as_batch_isotopologue,
    with_batch_isotopologue_line,
)
from mascope_backend.api.new.peak_assignments.batch_peaks import (
    KNOWN_SPECIES_KEY,
    MERGED_ISOTOPOLOGUE_OF_KEY,
    WATER_LADDER_KEY,
    standing_known_species_record,
    standing_merged_isotopologue_of_record,
    standing_water_ladder_record,
)
from mascope_backend.api.new.peak_assignments.batch_staleness import (
    STATE_NOT_RUN,
    BatchStage,
    stage_states,
)
from mascope_backend.api.new.peak_assignments.engine import (
    build_unassigned_assignments,
    is_target_library_row,
)
from mascope_backend.api.new.peak_assignments.known_species import (
    ALREADY,
    KEPT_CARRIES_LINES,
    KEPT_CLAIMED_OTHERWISE,
    KEPT_LINE_OF_ANOTHER,
    KEPT_ON_TWIN,
    KEPT_TARGET_LIBRARY,
    Species,
    _registry_exemplar,
    listed_species_row,
    partition_of,
    sample_partition,
)
from mascope_backend.api.new.peak_assignments.reagent_pass import (
    REASON_BATCH_KNOWN_SPECIES,
    REASON_BATCH_WATER_LADDER,
    BatchSpecies,
)
from mascope_backend.api.new.peak_assignments.row_terms import ROLE_ISO_CHILD, ROLE_M0
from mascope_backend.api.new.peak_assignments.tiers import TIER_ASSIGNED, TIER_CANDIDATE
from mascope_backend.db import BatchPeak, BatchPeakOccurrence, async_session
from mascope_tools.composition.known_species import KIND_LISTED
from mascope_tools.composition.reagents import (
    WATER_RUNG_REFERENCES,
    WaterLadder,
    water_ladders,
)


#: The kinds of batch decision a sample's run reads.
KIND_KNOWN_SPECIES = "known_species"
KIND_WATER_RUNG = "water_rung"
KIND_LISTED_COMPOUND = "listed_compound"
KIND_ISOTOPOLOGUE_STRIP = "isotopologue_strip"
KIND_SERIES = "series"

#: Every kind, in the order the stage list reads them.
KINDS: tuple[str, ...] = (
    KIND_KNOWN_SPECIES,
    KIND_WATER_RUNG,
    KIND_LISTED_COMPOUND,
    KIND_ISOTOPOLOGUE_STRIP,
    KIND_SERIES,
)

#: The batch stage whose records each kind is, whose version the run records.
STAGE_OF_KIND: dict[str, BatchStage] = {
    KIND_KNOWN_SPECIES: BatchStage.KNOWN_SPECIES,
    KIND_WATER_RUNG: BatchStage.WATER_LADDERS,
    KIND_LISTED_COMPOUND: BatchStage.KNOWN_SPECIES,
    KIND_ISOTOPOLOGUE_STRIP: BatchStage.ISOTOPE_LINES,
    KIND_SERIES: BatchStage.SERIES,
}

#: The water-ladder track a rung is claimed on: its monoisotopic line. The
#: claim takes the rung's isotope lines with it, as a library ion's.
MONOISOTOPIC_TRACK = "M0"

#: What became of a listed compound the batch decided present on its peak:
#: elected as the list's reading, or the run's own reading of it lifted to
#: candidate. :data:`known_species.ALREADY` where the run committed it
#: on its own, and a ``KEPT_*`` reason where the batch's claim keeps a file's
#: own reading too.
LISTED_ELECTED = "elected"
LISTED_LIFTED = "lifted"

#: What became of a listed compound or a strip whose peak (or, for a strip,
#: its parent's) the run's peaks do not hold: the decision named a peak of an
#: earlier detection of the sample, or one a time window left out, until the
#: sample's next fold brings its members up to its peaks.
PEAK_ABSENT = "peak_absent"

#: What became of an isotopologue strip on the sample's rows: read as the
#: parent's line, or why the row was left as it was.
STRIP_READ = "read"
STRIP_KEPT_PARENT = "parent_reads_otherwise"
STRIP_KEPT_CLAIMED = "claimed"
STRIP_KEPT_CARRIES_LINES = "carries_lines"

#: The tiers at which a run's row commits its formula, as a file's own test
#: of a listed compound reads it (``known_species.Species.reads``).
_COMMITTED = (TIER_ASSIGNED, TIER_CANDIDATE)


@dataclass(frozen=True)
class ListedDecision:
    """A listed compound the batch decided present, on the sample's peak.

    :param sample_peak_id: The sample's peak in the decision's batch peak.
    :param species: The compound.
    :param exemplar: The reading a row of it takes: the list's source and
        target off the batch peak's registry, at the ion's own mass
        (``known_species._registry_exemplar``).
    :param record: The decision, as a row cites it.
    :param twin_peak_ids: The sample's peaks in the decision's twins: the
        other batch peaks of the compound's trace in its partition
        (:func:`twin_peaks`), where a sample that reads the compound is kept
        (``known_species.KEPT_ON_TWIN``).
    """

    sample_peak_id: str
    species: Species
    exemplar: dict
    record: dict
    twin_peak_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class StripDecision:
    """A merged row the batch stripped as its parent's isotope line, on the
    sample's peaks.

    :param sample_peak_id: The sample's peak in the stripped batch peak.
    :param parent_peak_id: The sample's peak in the parent's batch peak.
    :param formula: The parent's formula, which the sample's row on the
        parent's peak must read for the line to be its.
    :param ionization_mechanism_id: The parent's mechanism.
    :param line: The line, as the batch read it.
    """

    sample_peak_id: str
    parent_peak_id: str
    formula: Optional[str]
    ionization_mechanism_id: Optional[str]
    line: BatchLine


@dataclass(frozen=True)
class BatchDecisions:
    """The batch's current decisions about one sample, as its run reads them.

    :param sample_batch_id: The sample's batch; None for a sample in none,
        which reads no decision.
    :param kinds: The kinds read (:data:`KINDS`): every one for a run, fewer
        for a decoy arm.
    :param stages: Batch stage -> its version and state when the run read its
        records.
    :param species: The library ions the batch decided present.
    :param rungs: The water-ladder rungs it passed in the sample's segment.
    :param listed: Sample peak -> the listed compound decided present there.
    :param strips: Sample peak -> the strip that reads it as a parent's line.
    :param series: Sample peak -> its batch peak's series record
        (``batch_lines.batch_series_by_peak``).
    """

    sample_batch_id: Optional[str] = None
    kinds: frozenset[str] = frozenset()
    stages: Mapping[str, dict] = field(default_factory=dict)
    species: tuple[BatchSpecies, ...] = ()
    rungs: tuple[BatchSpecies, ...] = ()
    listed: Mapping[str, ListedDecision] = field(default_factory=dict)
    strips: Mapping[str, StripDecision] = field(default_factory=dict)
    series: Mapping[str, dict] = field(default_factory=dict)

    def source_ions(self) -> list[BatchSpecies]:
        """The source ions the claim stage claims on the batch's word, one
        entry per ion: the known species first, the batch's own decision over
        its whole partition, then each rung no species names."""
        named = {(one.cluster.formula, one.cluster.charge) for one in self.species}
        return [*self.species] + [
            one
            for one in self.rungs
            if (one.cluster.formula, one.cluster.charge) not in named
        ]

    def _versions(self, kind: str) -> list[Optional[int]]:
        if kind == KIND_KNOWN_SPECIES:
            return [one.record.get("version") for one in self.species]
        if kind == KIND_WATER_RUNG:
            return [one.record.get("version") for one in self.rungs]
        if kind == KIND_LISTED_COMPOUND:
            return [one.record.get("version") for one in self.listed.values()]
        if kind == KIND_ISOTOPOLOGUE_STRIP:
            return [one.line.version for one in self.strips.values()]
        return [record.get("version") for record in self.series.values()]

    def record(self, applied: Optional[dict] = None) -> Optional[dict]:
        """What the run records of what it read (``stages``): per batch stage
        whose records it read, the stage's version and state then, and how
        many records it read and at which versions. A stage none of whose
        records concern the sample is recorded too: the run read that it
        decided nothing about it. None for a sample in no batch.

        :param applied: What the decisions did to the run's rows, by kind and
            outcome, recorded beside it (``applied``) once the stages ran.
        """
        if self.sample_batch_id is None:
            return None
        read: dict[str, dict] = {}
        for kind in KINDS:
            if kind not in self.kinds:
                continue
            stage = STAGE_OF_KIND[kind].value
            standing = self.stages.get(stage) or {
                "version": 0,
                "state": STATE_NOT_RUN,
            }
            entry = read.setdefault(
                stage,
                {
                    "version": standing.get("version", 0),
                    "state": standing.get("state", STATE_NOT_RUN),
                    "records": 0,
                    "record_versions": set(),
                },
            )
            versions = self._versions(kind)
            entry["records"] += len(versions)
            entry["record_versions"].update(versions)
        for entry in read.values():
            entry["record_versions"] = sorted(
                entry["record_versions"], key=lambda v: (v is None, v or 0)
            )
        return {"stages": read} | ({"applied": applied} if applied else {})


# --- the water ladder's rungs (pure) -----------------------------------------------


def acquired_at(sample: Any) -> Optional[float]:
    """When a sample was acquired, in seconds, as the water ladder cuts its
    segments on (``batch_water_ladder._load_extras``); None where it says
    not."""
    value = getattr(sample, "datetime_utc", None)
    return value.timestamp() if isinstance(value, datetime) else None


def segment_of(
    record: dict, sample_item_id: str, acquired: Optional[float]
) -> tuple[bool, Optional[dict]]:
    """Whether a rung's record places a sample in a segment the rung passed
    in, and that segment's reading.

    A sample acquired inside a passing segment, its first spectrum's time to
    its last's, is in it. Any other sample with a time - acquired after the
    last segment, before the first, in a gap between two, or in a segment the
    rung did not pass in - is in none, even where the ladder claimed the rung
    in it: a rung is evidence about the segment it was measured in, and
    ``claimed_samples`` carries an earlier measurement's samples over
    whatever segment this one puts them in. Only where the time cannot
    decide - a sample with no time, of the segment of spectra with none, or
    a record from before the records kept a segment's times - is a sample
    the ladder claimed the rung in (``claimed_samples``) in one.

    :return: Whether it is placed, and the segment's entry where the time
        placed it (None where only the claim did).
    """
    passed = set(record.get("passed_in") or ())
    segments = [one for one in record.get("segments") or () if isinstance(one, dict)]
    if acquired is not None and any("start" in one for one in segments):
        for entry in segments:
            if entry.get("segment") not in passed:
                continue
            start, end = entry.get("start"), entry.get("end")
            if start is None or end is None:
                continue
            if float(start) <= acquired <= float(end):
                return True, entry
        return False, None
    if sample_item_id in (record.get("claimed_samples") or ()):
        return True, None
    return False, None


def rung_from(
    record: dict,
    batch_peak_id: str,
    sample_item_id: str,
    acquired: Optional[float],
    ladders: Mapping[str, WaterLadder],
) -> Optional[BatchSpecies]:
    """The rung a batch peak's water-ladder record decides for a sample, or
    None.

    None where the claim was held, where the record is of a heavier track (a
    rung is claimed on its monoisotopic line, its lines with it), where the
    sample's profile declares no ladder of the rung's core, and where the
    sample is in no segment the rung passed in (:func:`segment_of`).

    :param record: The batch peak's standing record
        (``batch_peaks.standing_water_ladder_record``).
    :param ladders: The sample's profile's ladders, by their core's label.
    """
    rung = record.get("rung") if isinstance(record, dict) else None
    if not isinstance(rung, dict) or record.get("held"):
        return None
    if rung.get("track") != MONOISOTOPIC_TRACK:
        return None
    ladder = ladders.get(rung.get("core"))
    waters = rung.get("waters")
    if ladder is None or not isinstance(waters, int):
        return None
    if not 0 <= waters <= len(ladder.rungs):
        return None
    cluster = ladder.rung(waters)
    placed, entry = segment_of(record, sample_item_id, acquired)
    if not placed:
        return None
    entry = entry or {}
    return BatchSpecies(
        cluster,
        {
            "batch_peak_id": batch_peak_id,
            "core": rung.get("core"),
            "waters": waters,
            "track": MONOISOTOPIC_TRACK,
            "segment": entry.get("segment"),
            "presence": entry.get("presence"),
            "decoy_presence": entry.get("decoy_presence"),
            "references": list(WATER_RUNG_REFERENCES),
            "batch_peak_run_id": record.get("batch_peak_run_id"),
            "version": record.get("version"),
        },
        reason=REASON_BATCH_WATER_LADDER,
    )


async def rungs_for(sample: Any, profile: Optional[str]) -> tuple[BatchSpecies, ...]:
    """The rungs a sample's batch passed in the segment the sample falls in,
    one per ion: where the fold split a rung's trace over two batch peaks,
    the one read in the most of its segment's spectra speaks for it.

    :param profile: The name of the profile the sample's run resolved to,
        whose ladders the rungs are of.
    """
    sample_batch_id = getattr(sample, "sample_batch_id", None)
    ladders = {
        ladder.core.label: ladder for ladder in (water_ladders(profile or "") or ())
    }
    if not sample_batch_id or not ladders:
        return ()
    async with async_session() as session:
        mode, variable = await sample_partition(session, sample)
        anchors = (
            (
                await session.execute(
                    select(BatchPeak).where(
                        BatchPeak.sample_batch_id == sample_batch_id,
                        BatchPeak.ionization_mode_id == mode,
                        BatchPeak.intensity_variable == variable,
                        BatchPeak.provenance[WATER_LADDER_KEY].is_not(None),
                    )
                )
            )
            .scalars()
            .all()
        )
    acquired = acquired_at(sample)
    by_ion: dict[tuple[str, int], BatchSpecies] = {}
    for anchor in sorted(anchors, key=lambda a: a.batch_peak_id):
        record = standing_water_ladder_record(anchor)
        one = (
            rung_from(
                record, anchor.batch_peak_id, sample.sample_item_id, acquired, ladders
            )
            if record is not None
            else None
        )
        if one is None:
            continue
        ion = (one.cluster.formula, one.cluster.charge)
        if ion not in by_ion or (one.record.get("presence") or 0.0) > (
            by_ion[ion].record.get("presence") or 0.0
        ):
            by_ion[ion] = one
    return tuple(by_ion.values())


# --- the decisions read through the sample's members -------------------------------


def listed_from(
    anchor: Any,
    sample_peak_id: str,
    record: dict,
    twin_peak_ids: Iterable[str] = (),
) -> Optional[ListedDecision]:
    """The listed compound a batch peak's known-species record decided present
    on the sample's peak, or None where the record is another kind's, did not
    decide it present, or held its claim.

    :param record: The batch peak's standing record
        (``batch_peaks.standing_known_species_record``).
    :param twin_peak_ids: The sample's peaks in its twins (:func:`twin_peaks`).
    """
    species = record.get("species") if isinstance(record, dict) else None
    if (
        not isinstance(species, dict)
        or species.get("kind") != KIND_LISTED
        or record.get("verdict") != "present"
        or record.get("held")
        or not species.get("formula")
    ):
        return None
    compound = Species(
        kind=KIND_LISTED,
        ion_formula=species.get("ion_formula"),
        formula=species["formula"],
        ionization_mechanism_id=species.get("ionization_mechanism_id"),
        listing=species.get("listing"),
    )
    return ListedDecision(
        sample_peak_id=sample_peak_id,
        species=compound,
        exemplar=_registry_exemplar(anchor, compound),
        record={
            "batch_peak_id": anchor.batch_peak_id,
            "verdict": record.get("verdict"),
            "files": record.get("files"),
            "present": record.get("present"),
            "test_held": record.get("test_held"),
            "batch_peak_run_id": record.get("batch_peak_run_id"),
            "version": record.get("version"),
        },
        twin_peak_ids=tuple(sorted(twin_peak_ids)),
    )


def _species_key(record: dict) -> Optional[tuple]:
    """What makes two records' species one, as the batch groups them
    (``batch_known_species.species_key``): its kind, ion and formula."""
    species = record.get("species")
    if not isinstance(species, dict):
        return None
    return (species.get("kind"), species.get("ion_formula"), species.get("formula"))


def twin_peaks(
    anchor: Any, record: dict, known: Iterable[tuple[str, Any, dict]]
) -> list[str]:
    """The sample's peaks in a decision's twins: the other batch peaks whose
    standing record is of the same species, in the same partition. The batch
    decides each partition's species together, and where the fold split a
    species' trace over two batch peaks, the one that claims it keeps a sample
    that reads it on any other (``batch_known_species.settle_twins``): one
    held for it (``twin_of``), and one decided otherwise too.

    :param known: (sample peak, batch peak, its standing known-species
        record) for each of the sample's members that carries one.
    """
    key = _species_key(record)
    partition = partition_of(anchor)
    return [
        peak_id
        for peak_id, other, other_record in known
        if other.batch_peak_id != anchor.batch_peak_id
        and _species_key(other_record) == key
        and partition_of(other) == partition
    ]


def strip_from(
    anchor: Any,
    sample_peak_id: str,
    record: dict,
    parent_peak_id: Optional[str],
    parent_mz: Optional[float],
) -> Optional[StripDecision]:
    """The strip a batch peak's record reads on the sample's peaks, or None
    where the sample holds no member of the parent's batch peak.

    :param record: The stripped batch peak's standing record
        (``batch_peaks.standing_merged_isotopologue_of_record``).
    :param parent_peak_id: The sample's peak in the parent's batch peak.
    :param parent_mz: The parent's batch peak's m/z, for a record from before
        the records kept the line's offset: its line sits at the record's m/z,
        read at the parent's trace centre.
    """
    if parent_peak_id is None:
        return None
    offset = record.get("mz_offset")
    if offset is None:
        if record.get("mz") is None or parent_mz is None:
            return None
        offset = float(record["mz"]) - float(parent_mz)
    return StripDecision(
        sample_peak_id=sample_peak_id,
        parent_peak_id=parent_peak_id,
        formula=record.get("formula"),
        ionization_mechanism_id=record.get("ionization_mechanism_id"),
        line=BatchLine(
            label=record.get("label"),
            mz_offset=float(offset),
            share=float(record.get("ratio") or 0.0),
            batch_peak_id=anchor.batch_peak_id,
            parent_batch_peak_id=record["batch_peak_id"],
            reading={
                key: record.get(key)
                for key in ("spectra", "median_factor", "above", "verdict")
            },
            batch_peak_run_id=record.get("batch_peak_run_id"),
            version=record.get("version"),
        ),
    )


async def member_decisions(
    sample: Any, kinds: Iterable[str]
) -> tuple[dict[str, ListedDecision], dict[str, StripDecision]]:
    """The listed compounds and the strips a sample's batch decided on the
    sample's own members, by the sample's peak.

    :param kinds: The kinds read; neither is read where neither is asked for.
    """
    kinds = set(kinds)
    keys = [
        key
        for kind, key in (
            (KIND_LISTED_COMPOUND, KNOWN_SPECIES_KEY),
            (KIND_ISOTOPOLOGUE_STRIP, MERGED_ISOTOPOLOGUE_OF_KEY),
        )
        if kind in kinds
    ]
    sample_batch_id = getattr(sample, "sample_batch_id", None)
    if not keys or not sample_batch_id:
        return {}, {}
    async with async_session() as session:
        rows = (
            await session.execute(
                select(BatchPeakOccurrence.sample_peak_id, BatchPeak)
                .join(
                    BatchPeak,
                    BatchPeak.batch_peak_id == BatchPeakOccurrence.batch_peak_id,
                )
                .where(
                    BatchPeakOccurrence.sample_item_id == sample.sample_item_id,
                    BatchPeak.sample_batch_id == sample_batch_id,
                    or_(*(BatchPeak.provenance[key].is_not(None) for key in keys)),
                )
            )
        ).all()
        stripped = []
        for peak_id, anchor in rows:
            if KIND_ISOTOPOLOGUE_STRIP not in kinds:
                break
            record = standing_merged_isotopologue_of_record(anchor)
            if record is not None:
                stripped.append((str(peak_id), anchor, record))
        parents: dict[str, tuple[str, Optional[float]]] = {}
        parent_ids = sorted({record["batch_peak_id"] for _, _, record in stripped})
        if parent_ids:
            parents = {
                batch_peak_id: (str(peak_id), mz)
                for batch_peak_id, peak_id, mz in (
                    await session.execute(
                        select(
                            BatchPeakOccurrence.batch_peak_id,
                            BatchPeakOccurrence.sample_peak_id,
                            BatchPeak.mz,
                        )
                        .join(
                            BatchPeak,
                            BatchPeak.batch_peak_id
                            == BatchPeakOccurrence.batch_peak_id,
                        )
                        .where(
                            BatchPeakOccurrence.sample_item_id == sample.sample_item_id,
                            BatchPeakOccurrence.batch_peak_id.in_(parent_ids),
                        )
                    )
                ).all()
            }
    listed: dict[str, ListedDecision] = {}
    if KIND_LISTED_COMPOUND in kinds:
        known = [
            (str(peak_id), anchor, record)
            for peak_id, anchor in rows
            if (record := standing_known_species_record(anchor)) is not None
        ]
        for peak_id, anchor, record in known:
            one = listed_from(
                anchor, peak_id, record, twin_peaks(anchor, record, known)
            )
            if one is not None:
                listed[one.sample_peak_id] = one
    strips: dict[str, StripDecision] = {}
    for peak_id, anchor, record in stripped:
        parent_peak, parent_mz = parents.get(record["batch_peak_id"], (None, None))
        one = strip_from(anchor, peak_id, record, parent_peak, parent_mz)
        if one is not None:
            strips[one.sample_peak_id] = one
    return listed, strips


async def stage_versions(sample_batch_id: str) -> dict[str, dict]:
    """Each batch stage's version and state as the run reads its records."""
    async with async_session() as session:
        rows = await stage_states(session, sample_batch_id)
    return {
        name: {"version": row.version, "state": row.state} for name, row in rows.items()
    }


async def read_batch_decisions(
    sample: Any, *, profile: Optional[str], kinds: Iterable[str] = KINDS
) -> BatchDecisions:
    """The one reader of what a sample's batch decided about it: every kind of
    record asked for, read once, with the version of each stage it read, read
    before its records.

    :param sample: The sample view row.
    :param profile: The name of the profile the sample's run resolved to,
        whose water ladders a rung is read against.
    :param kinds: The kinds to read (:data:`KINDS`); a decoy arm reads fewer
        (``pipeline.BATCH_INPUTS``).
    """
    kinds = frozenset(kinds)
    sample_batch_id = getattr(sample, "sample_batch_id", None)
    if not sample_batch_id:
        return BatchDecisions(kinds=kinds)
    # The versions before the records: a stage that hands over while they are
    # read is then recorded as running, or older than what was read, and the
    # staleness rule reads the sample again rather than calling it current.
    stages = await stage_versions(sample_batch_id)
    listed, strips = await member_decisions(sample, kinds)
    return BatchDecisions(
        sample_batch_id=sample_batch_id,
        kinds=kinds,
        stages=stages,
        species=(
            tuple(await known_species.batch_species_for(sample))
            if KIND_KNOWN_SPECIES in kinds
            else ()
        ),
        rungs=await rungs_for(sample, profile) if KIND_WATER_RUNG in kinds else (),
        listed=listed,
        strips=strips,
        series=(
            await batch_lines.batch_series_by_peak(sample.sample_item_id)
            if KIND_SERIES in kinds
            else {}
        ),
    )


# --- the decisions on a run's rows (pure) -------------------------------------------


def listed_decision(
    decision: ListedDecision,
    rows_on_peak: list[dict],
    *,
    claimed: bool,
    owners: set[str],
    on_twins: Iterable[dict] = (),
) -> Optional[str]:
    """What becomes of a listed compound decided present on the run's rows of
    its peak, as the batch's own claim decides a member
    (``batch_known_species.sample_decision``, then ``decide_row``'s twins):
    None where its reading is elected, :data:`LISTED_LIFTED` where the run's
    own reading of the compound is lifted to candidate,
    :data:`known_species.ALREADY` where the run committed it on its
    own, else why the run's reading is kept: the target library named the
    peak, the peak is another row's isotope line, a claim took it for the
    source, its reading carries isotope lines of another formula, or the run
    reads the compound on a twin (``known_species.KEPT_ON_TWIN``), so
    the election would write a second copy of it.

    :param rows_on_peak: The run's elected rows on the peak.
    :param claimed: Whether the claim stage took the peak.
    :param owners: The ids of the rows some row names as the owner of its line.
    :param on_twins: The run's elected rows on the sample's peaks in the
        decision's twins (:attr:`ListedDecision.twin_peak_ids`). One reads
        the compound where it reads its formula at any tier, as a twin's
        member does (``batch_known_species.reads_on_twin``).
    """
    formula = decision.species.formula
    m0 = next((r for r in rows_on_peak if r.get("role") == ROLE_M0), None)
    if (
        m0 is not None
        and m0.get("assigned_formula") == formula
        and m0.get("tier") in _COMMITTED
    ):
        return ALREADY
    if m0 is not None and is_target_library_row(m0):
        return KEPT_TARGET_LIBRARY
    if any(
        r.get("role") == ROLE_ISO_CHILD or r.get("owner_peak_assignment_id")
        for r in rows_on_peak
    ):
        return KEPT_LINE_OF_ANOTHER
    if claimed:
        return KEPT_CLAIMED_OTHERWISE
    lifts = m0 is not None and m0.get("assigned_formula") == formula
    if m0 is not None and str(m0.get("peak_assignment_id")) in owners and not lifts:
        return KEPT_CARRIES_LINES
    if any(
        row.get("role") == ROLE_M0 and row.get("assigned_formula") == formula
        for row in on_twins
    ):
        return KEPT_ON_TWIN
    return LISTED_LIFTED if lifts else None


def elect_listed(
    stage_a: list[dict],
    stage_b: list[dict],
    decided: Mapping[str, ListedDecision],
    *,
    claimed_peak_ids: set[str],
    peaks: pd.DataFrame,
    source_mz: Mapping[str, float],
    sample_item_id: str,
    run_id: str,
) -> tuple[list[dict], list[dict], dict[str, str]]:
    """Elect each listed compound the batch decided present on its peak, as
    the batch's own claim writes a member's row
    (``known_species.listed_species_row``): the list's reading at
    candidate, no fit of the run's own, the reading it displaced first among
    its alternatives and the decision as its tier reason; or, where the run
    read the compound below candidate, that reading lifted. A row the claim
    would keep is kept (:func:`listed_decision`). The elected row is a list's
    row, among Stage A's; a cap after it may lower its tier, as it lowers any
    row's, but the refit's re-score does not (``pipeline.RunContext.floors``).

    :param claimed_peak_ids: The peaks the claim stage took.
    :param peaks: The sample's peaks, for a peak no stage read. A decision
        on a peak they do not hold is left (:data:`PEAK_ABSENT`).
    :param source_mz: Sample peak -> its m/z on the axis as stored, which the
        list's reading is measured against: a decoy arm's shifted axis moves
        no batch decision, so it claims what its control claims.
    :return: Stage A's rows and the search's, the elected rows among Stage
        A's, and what became of each decision, by its peak.
    """
    outcomes: dict[str, str] = {}
    if not decided:
        return stage_a, stage_b, outcomes
    rows = [*stage_a, *stage_b]
    on_peak: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        on_peak[str(row.get("sample_peak_id"))].append(row)
    owners = {
        str(row["owner_peak_assignment_id"])
        for row in rows
        if row.get("owner_peak_assignment_id")
    }
    held = set(peaks["sample_peak_id"].astype(str))
    moved: set[int] = set()
    added: list[dict] = []
    for peak_id in sorted(decided):
        if peak_id not in held:
            outcomes[peak_id] = PEAK_ABSENT
            continue
        decision = decided[peak_id]
        here = on_peak.get(peak_id, [])
        outcome = listed_decision(
            decision,
            here,
            claimed=peak_id in claimed_peak_ids,
            owners=owners,
            on_twins=[
                row for twin in decision.twin_peak_ids for row in on_peak.get(twin, [])
            ],
        )
        outcomes[peak_id] = outcome or LISTED_ELECTED
        if outcome not in (None, LISTED_LIFTED):
            continue
        m0 = next((r for r in here if r.get("role") == ROLE_M0), None)
        if m0 is None:
            [m0] = build_unassigned_assignments(
                peaks[peaks["sample_peak_id"].astype(str) == peak_id],
                sample_item_id=sample_item_id,
                peak_assignment_run_id=run_id,
            )
            added.append(m0)
            # So a twin's decision, read after this one, sees the compound here.
            on_peak[peak_id].append(m0)
        else:
            moved.add(id(m0))
        measured = {
            **m0,
            "sample_peak_mz": source_mz.get(peak_id, m0["sample_peak_mz"]),
        }
        m0.update(
            listed_species_row(
                measured, decision.exemplar, decision.species, decision.record
            )
        )
    stage_b_left = [row for row in stage_b if id(row) not in moved]
    from_b = [row for row in stage_b if id(row) in moved]
    return [*stage_a, *from_b, *added], stage_b_left, outcomes


def cite_listed(
    rows: Iterable[dict],
    decided: Mapping[str, ListedDecision],
    outcomes: Mapping[str, str],
) -> int:
    """Name the batch's decision among the tier reasons of each row the run
    elected or lifted on it, once the judging has written its own: the
    tiering pass rewrites a row's reasons, and the decision is what put the
    row at candidate, whatever a cap made of it since. A row whose reading
    the judging turned to another formula cites nothing.

    :param rows: The judged rows, written into.
    :param outcomes: Sample peak -> what became of its decision
        (:func:`elect_listed`).
    :return: How many rows cite a decision.
    """
    cited = 0
    for row in rows:
        peak_id = str(row.get("sample_peak_id"))
        outcome = outcomes.get(peak_id)
        if (
            outcome not in (LISTED_ELECTED, LISTED_LIFTED)
            or row.get("role") != ROLE_M0
            or row.get("assigned_formula") != decided[peak_id].species.formula
        ):
            continue
        provenance = row.setdefault("provenance", {})
        if REASON_BATCH_KNOWN_SPECIES not in provenance:
            continue
        reasons = [
            reason
            for reason in provenance.get("tier_reasons") or []
            if not (
                isinstance(reason, dict)
                and reason.get("rule") == REASON_BATCH_KNOWN_SPECIES
            )
        ]
        reasons.append(_listed_reason(lifted=outcome == LISTED_LIFTED))
        provenance["tier_reasons"] = reasons
        cited += 1
    return cited


def _listed_reason(*, lifted: bool) -> dict:
    """The tier reason a run's row on a listed compound its batch decided
    present names: what the decision did, which the judging after it may have
    lowered."""
    detail = (
        "the batch decided this listed compound present over its files, and "
        "this run lifted its own reading of it, below candidate, to candidate "
        "on that decision"
        if lifted
        else "the batch decided this listed compound present over its files, and "
        "this run proposed it at candidate on that decision: this file's own "
        "lines did not commit it"
    )
    return {"rule": REASON_BATCH_KNOWN_SPECIES, "detail": detail, "caps": False}


def read_strips(
    rows: list[dict],
    strips: Mapping[str, StripDecision],
    *,
    claimed_peak_ids: set[str],
    peaks: pd.DataFrame,
    sample_item_id: str,
    run_id: str,
) -> tuple[list[dict], Counter]:
    """Read each row the batch stripped as its parent's isotope line, as the
    batch's own strip rewrites a sample's row
    (``batch_lines.read_as_batch_isotopologue``): an isotopologue child of the
    run's row on the parent's peak, the reading it displaced first among its
    alternatives, recorded on both rows. Left as it is where the run's row on
    the parent's peak reads another reading, where a claim took the peak,
    and where the row carries isotope lines of its own, as the batch leaves a
    member (``batch_merged_isotopologues.sample_decision``). A peak no stage
    read is read as the line too. The judging then judges it as the parent's
    line.

    :param rows: The run's elected rows, before the judging, written into.
    :param claimed_peak_ids: The peaks the claim stage took.
    :param peaks: The sample's peaks, for a peak no stage read. A strip on a
        peak they do not hold, or whose parent's peak they do not hold, is
        left (:data:`PEAK_ABSENT`).
    :return: The rows, a line read off an unread peak among them, and how
        many strips ended which way.
    """
    counts: Counter = Counter()
    if not strips:
        return rows, counts
    on_peak: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        on_peak[str(row.get("sample_peak_id"))].append(row)
    owners = {
        str(row["owner_peak_assignment_id"])
        for row in rows
        if row.get("owner_peak_assignment_id")
    }
    held = set(peaks["sample_peak_id"].astype(str))
    added: list[dict] = []
    for peak_id in sorted(strips):
        strip = strips[peak_id]
        if peak_id not in held or strip.parent_peak_id not in held:
            counts[PEAK_ABSENT] += 1
            continue
        parent = next(
            (
                r
                for r in on_peak.get(strip.parent_peak_id, [])
                if r.get("role") == ROLE_M0
                and r.get("assigned_formula") == strip.formula
                and str(r.get("ionization_mechanism_id"))
                == str(strip.ionization_mechanism_id)
            ),
            None,
        )
        here = on_peak.get(peak_id, [])
        if parent is None:
            counts[STRIP_KEPT_PARENT] += 1
            continue
        parent_id = str(parent.get("peak_assignment_id"))
        if any(str(r.get("owner_peak_assignment_id") or "") == parent_id for r in here):
            counts[ALREADY] += 1
            continue
        if peak_id in claimed_peak_ids:
            counts[STRIP_KEPT_CLAIMED] += 1
            continue
        row = next(iter(here), None)
        if row is not None and str(row.get("peak_assignment_id")) in owners:
            counts[STRIP_KEPT_CARRIES_LINES] += 1
            continue
        if row is None:
            [row] = build_unassigned_assignments(
                peaks[peaks["sample_peak_id"].astype(str) == peak_id],
                sample_item_id=sample_item_id,
                peak_assignment_run_id=run_id,
            )
            added.append(row)
        values = read_as_batch_isotopologue(row, parent, strip.line)
        parent["provenance"] = with_batch_isotopologue_line(parent, row, strip.line)
        row.update(values)
        counts[STRIP_READ] += 1
    return [*rows, *added], counts


__all__ = [
    "KINDS",
    "KIND_ISOTOPOLOGUE_STRIP",
    "KIND_KNOWN_SPECIES",
    "KIND_LISTED_COMPOUND",
    "KIND_SERIES",
    "KIND_WATER_RUNG",
    "LISTED_ELECTED",
    "LISTED_LIFTED",
    "MONOISOTOPIC_TRACK",
    "PEAK_ABSENT",
    "STAGE_OF_KIND",
    "STRIP_KEPT_CARRIES_LINES",
    "STRIP_KEPT_CLAIMED",
    "STRIP_KEPT_PARENT",
    "STRIP_READ",
    "BatchDecisions",
    "ListedDecision",
    "StripDecision",
    "acquired_at",
    "cite_listed",
    "elect_listed",
    "listed_decision",
    "listed_from",
    "member_decisions",
    "read_batch_decisions",
    "read_strips",
    "rung_from",
    "rungs_for",
    "segment_of",
    "stage_versions",
    "strip_from",
]
