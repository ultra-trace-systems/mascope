"""The reagent ions' water ladders, measured on the batch's own series: step
4.2's ninth rider (4.2j).

A soft-interface source carries its reagent ions hydrated - Br-.(H2O)n,
NO3-.(H2O)n, HNO3.NO3-.(H2O)n - and how far a ladder reaches moves with the
source and the humidity. A file's reagent pre-pass claims a hydrate only as far
as a work names it (3.3, 3.3d), so on a humid stretch of a TOF batch the high
rungs read as organics, or as nothing at all. "Measure water ladders" reads
each ladder on the batch's own series instead (``mascope_tools.composition
.water_ladder``), and claims what passes:

1. **The ladders.** Per partition (one ionization mode, one instrument type),
   the profile a sample of it resolves to declares its cores
   (``reagents.water_ladders``: bromide, its dimer, trimer and HBr cluster;
   nitrate, its acid clusters, nitrite; the labelled cores; none on a positive
   profile). The partition's spectra are cut into acquisition segments, and
   each core's every track (its monoisotopic line, and each bright heavy line,
   a bromide's 81Br) is climbed per segment on the batch peaks: a rung passes
   where it and every rung below it, to the core, are present in half the
   segment's spectra, it is present three times as often as at its decoys, and,
   on a TOF, its M+1 line shows no carbon.
2. **The claim.** A batch peak at a passing rung's line is the reagent's ion in
   the samples of the segments it passed in. In each of them its member is
   rewritten as the rung - role ``reagent``, no formula, the rung's ion - and so
   is the sample's own in-app row (``reagent_pass.water_rung_row``), the
   reading it displaced first among its alternatives. The member's isotope
   lines go with it, as a library ion's do; a heavier track's peak read on its
   own becomes the rung's line, owned by the rung's monoisotopic peak where
   that is the rung in the sample. A sample is kept as it is where a person
   assigned the peak, where the target library or a reference list named its
   reading (a cited analyte), where the peak is another ion's isotope line or
   another source ion's claim, and where its reading carries an isotope line
   the rung cannot have (a 13C line under a carbon-free cluster). An imported
   run's rows are never rewritten, and a curated batch peak is never claimed.
   Samples of segments the rung did not pass in keep their readings: the
   batch peak then reads what its members' vote reads.
3. **The record and the report.** Every batch peak at a passing rung carries
   ``provenance.water_ladder`` (``batch_peaks.WATER_LADDER_KEY``): the rung,
   its reading in every segment it was read in, what the claim wrote and kept,
   each rewritten member's earlier state, and the reading the batch peak holds
   since. The run's summary reports per partition the segments, each ladder's
   reach per segment, the rungs tested and passed, the batch peaks and rows
   moved, and the share of the partition's signal no committed reading, source
   role or isotope line explains, before and after.

A sample's next run reads its peaks afresh; the next measurement reads them
again. The reading runs outside the fold lock (and off the event loop) and the
writes under it, a chunk of rungs per transaction, each rung's members read
again as the ledger stands then.

Design: ``docs/dev/assignment_quality_plan.md``, step 4.2, the ninth rider.
"""

from __future__ import annotations

import asyncio
import functools
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from sqlalchemy import select

from mascope_backend.api.lib.api_features import api_controller_background_task
from mascope_backend.api.new.peak_assignments.batch_known_species import (
    ALREADY,
    KEPT_CARRIES_LINES,
    KEPT_CLAIMED_OTHERWISE,
    KEPT_HAND_ASSIGNED,
    KEPT_LINE_OF_ANOTHER,
    KEPT_TARGET_LIBRARY,
    ROW_KEPT_IMPORTED,
    ROW_KEPT_NO_ROW,
    Member,
    Partition,
    Row,
    Species,
    _as_dict,
    _load_batch,
    _member_from,
    _read_rows,
    _row_marks,
    partition_of,
    trace_centre,
)
from mascope_backend.api.new.peak_assignments.batch_peaks import (
    ROLE_ISO_CHILD,
    ROLE_REAGENT,
    SOURCE_ROLES,
    WATER_LADDER_KEY,
    candidate_index,
    manual_pin_of,
    member_state,
    mz_from_delta,
    reading_of,
    resolve_candidate,
    role_code,
    tier_code,
    water_ladder_record_of,
)
from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
    _acquire_batch_fold_lock,
    _recompute_consensus,
)
from mascope_backend.api.new.peak_assignments.batch_runs import (
    ACTION_WATER_LADDERS,
    complete_run,
    fail_run,
    start_run,
    touch_ledger,
)
from mascope_backend.api.new.peak_assignments.batch_series import _mode_chemistry
from mascope_backend.api.new.peak_assignments.config import (
    IN_APP_ENGINE,
    PeakAssignmentConfig,
)
from mascope_backend.api.new.peak_assignments.engine import SOURCE_REAGENT
from mascope_backend.api.new.peak_assignments.reagent_pass import water_rung_row
from mascope_backend.api.new.peak_assignments.tiers import (
    TIER_ASSIGNED,
    TIER_CANDIDATE,
    TIER_UNASSIGNED,
)
from mascope_backend.db import (
    BatchPeak,
    BatchPeakOccurrence,
    PeakAssignmentRun,
    Sample,
    async_session,
)
from mascope_backend.runtime import runtime
from mascope_tools.composition.heuristic_filter import predict_isotopes
from mascope_tools.composition.known_species import KIND_LIBRARY
from mascope_tools.composition.reagents import (
    WATER_RUNG_REFERENCES,
    WaterLadder,
    WaterTrack,
    water_ladders,
)
from mascope_tools.composition.water_ladder import (
    LadderReading,
    RungReading,
    Segment,
    Trace,
    acquisition_segments,
    read_ladders,
    thresholds,
)
from mascope_tools.composition.water_ladder import Partition as LadderPartition


#: Kept: the sample's reading names a reference list's identity, a cited
#: analyte the ladder does not displace.
KEPT_LISTED = "listed"
#: A heavier line's peak the sample reads as the line of the rung's
#: monoisotopic peak: the claim on that peak moves it, as its line.
WITH_MONOISOTOPIC = "with_monoisotopic"

#: Why a batch peak at a passing rung was not claimed: it is pinned by hand.
HELD_CURATED = "curated"

#: The rungs a run's summary lists at most; the counts are whole, and every
#: claimed batch peak carries its record on the ledger.
MAX_LISTED = 500

#: The samples a record lists with their readings at most; its counts are whole.
MAX_RECORDED_FILES = 50

#: Batch peaks written per transaction under the fold lock, at least; a rung's
#: tracks are never split over two.
WRITE_CHUNK = 50

#: The tiers at which a batch peak's reading is committed, for the report's
#: unexplained signal.
_COMMITTED = (TIER_ASSIGNED, TIER_CANDIDATE)


# --- the plan (pure) ---------------------------------------------------------------


def _signed(formula: str, charge: int) -> str:
    return formula + ("+" * charge if charge > 0 else "-" * -charge)


@dataclass
class Line:
    """A rung's track that passed in at least one segment.

    :ivar readings: Segment index -> the rung's reading there, for every
        segment its ladder reached it in.
    """

    ladder: WaterLadder
    track: WaterTrack
    waters: int
    readings: dict[int, RungReading] = field(default_factory=dict)

    @property
    def rung(self):
        return self.ladder.rung(self.waters)

    @property
    def ion_formula(self) -> str:
        return _signed(self.rung.formula, self.rung.charge)

    @property
    def exact_mz(self) -> float:
        return self.ladder.line_mz(self.waters, self.track)

    @property
    def passed_in(self) -> list[int]:
        return sorted(i for i, r in self.readings.items() if r.passed)

    @property
    def presence(self) -> float:
        """Its presence where it passed, the most of any segment's."""
        return max(
            (r.presence for r in self.readings.values() if r.passed), default=0.0
        )

    @property
    def rung_key(self) -> tuple[str, int]:
        return (self.ladder.core.label, self.waters)

    @property
    def label(self) -> str:
        """The rung's label, its track's beside it for a heavier line."""
        if self.track.monoisotopic:
            return self.rung.label
        return f"{self.rung.label} ({self.track.label})"

    def to_json(self) -> dict:
        rung = self.rung
        entry = {
            "core": self.ladder.core.label,
            "ion": rung.label,
            "ion_formula": self.ion_formula,
            "waters": self.waters,
            "track": self.track.label,
            "mz": round(self.exact_mz, 5),
            "family": rung.family,
            "references": list(rung.references),
            "rule_references": list(WATER_RUNG_REFERENCES),
        }
        if rung.observed:
            entry["observed"] = rung.observed
        return entry


@dataclass
class Claim:
    """One batch peak at a passing rung's line, and what its claim does.

    :ivar samples: Sample -> None where the claim rewrites it, :data:`ALREADY`
        where it already reads the rung, :data:`WITH_MONOISOTOPIC` where the
        claim on the rung's monoisotopic peak moves it, else why it is kept;
        for the samples of the segments the rung passed in that hold a member.
    :ivar segment_of: Sample -> its segment's index, for those samples.
    :ivar monoisotopic: For a heavier track, the batch peaks at the rung's
        monoisotopic line, whose claim in a sample makes this one its line.
    :ivar also_reads_as: Other passing rungs at this batch peak, by label.
    """

    row: Row
    line: Line
    samples: dict[str, Optional[str]] = field(default_factory=dict)
    segment_of: dict[str, int] = field(default_factory=dict)
    monoisotopic: tuple[str, ...] = ()
    held: Optional[str] = None
    also_reads_as: list[str] = field(default_factory=list)

    @property
    def batch_peak_id(self) -> str:
        return self.row.batch_peak_id

    @property
    def claims(self) -> bool:
        return self.held is None and any(d is None for d in self.samples.values())

    def to_json(self) -> dict:
        entry = {
            "batch_peak_id": self.batch_peak_id,
            "mz": round(trace_centre(self.row.anchor.mz, self.row.members.values()), 6),
            **self.line.to_json(),
            "passed_in": self.line.passed_in,
            "presence": round(self.line.presence, 4),
            "claimed": sum(1 for d in self.samples.values() if d is None),
            "kept": sum(
                1
                for d in self.samples.values()
                if d not in (None, ALREADY, WITH_MONOISOTOPIC)
            ),
        }
        if self.held is not None:
            entry["held"] = self.held
        return entry


@dataclass
class PartitionPlan:
    """One partition's ladders, read, and the claims they make."""

    key: Partition
    profile: Optional[str]
    ladders: tuple[WaterLadder, ...] = ()
    spectra: int = 0
    segments: list[Segment] = field(default_factory=list)
    readings: list[LadderReading] = field(default_factory=list)
    lines: list[Line] = field(default_factory=list)
    claims: list[Claim] = field(default_factory=list)
    unexplained_before: Optional[float] = None

    @property
    def rungs_tested(self) -> int:
        return len(
            {
                (r.ladder.core.label, r.track.label, rung.waters)
                for r in self.readings
                for rung in r.rungs
            }
        )

    @property
    def rungs_passed(self) -> int:
        return len(self.lines)


@dataclass
class Plan:
    """What the reading found, before anything is written."""

    partitions: list[PartitionPlan] = field(default_factory=list)

    @property
    def claims(self) -> list[Claim]:
        return [claim for part in self.partitions for claim in part.claims]


def _committed(anchor: Any) -> bool:
    return bool(anchor.consensus_formula) and anchor.consensus_tier in _COMMITTED


def explained(anchor: Any) -> bool:
    """Whether a batch peak's signal is explained: its reading commits a
    formula, a source's role claims it, or it is another's isotope line."""
    return (
        _committed(anchor)
        or anchor.consensus_role in SOURCE_ROLES
        or bool(anchor.isotopologue_of)
    )


def unexplained_share(
    anchors: Iterable[Any], intensity: dict[str, float]
) -> Optional[float]:
    """The share of a partition's summed member intensity on batch peaks
    nothing explains (:func:`explained`); None for a partition with none.

    :param intensity: Batch peak -> its members' summed intensity.
    """
    total = 0.0
    unexplained = 0.0
    for anchor in anchors:
        value = intensity.get(anchor.batch_peak_id, 0.0)
        total += value
        if not explained(anchor):
            unexplained += value
    return None if total <= 0 else unexplained / total


def _summed(row: Row) -> float:
    return float(sum(m.intensity or 0.0 for m in row.members.values()))


def reads_rung(member: Member, line: Line) -> bool:
    """Whether a member already reads the peak as the rung: a source's claim
    of the rung's ion, its monoisotopic peak or one of its lines."""
    return (
        member.role == ROLE_REAGENT
        and not member.formula
        and member.ion_formula == line.ion_formula
    )


def _listed(member: Member, anchor: Any) -> bool:
    """Whether the member's reading names a reference list's identity."""
    if not member.formula:
        return False
    for entry in anchor.candidates or []:
        if (
            isinstance(entry, dict)
            and entry.get("formula") == member.formula
            and entry.get("ion_formula") == member.ion_formula
            and entry.get("ionization_mechanism_id") == member.ionization_mechanism_id
            and isinstance(entry.get("listing"), dict)
        ):
            return True
    return False


#: (sample, batch peak) -> each line the sample's members name the batch peak
#: the owner of: the label its row gives it (None where no row is linked), its
#: m/z and its batch peak's window.
Lines = dict[tuple[str, str], tuple[tuple[Optional[str], float, float], ...]]


def lines_of(
    entries: Iterable[tuple[str, Optional[str], Optional[str], float, float]],
) -> Lines:
    """:data:`Lines` from (sample, owner batch peak, label, m/z, window) per
    member; a member naming no owner is no line."""
    found: dict[tuple[str, str], list] = defaultdict(list)
    for sample, owner, label, mz, tol in entries:
        if owner:
            found[(sample, owner)].append((label, mz, tol))
    return {key: tuple(lines) for key, lines in found.items()}


#: The rung's envelope down to this share of its brightest line: what an
#: unlabelled line it carries must sit at to be its own.
_ENVELOPE_FLOOR = 1e-3


@functools.lru_cache(maxsize=4096)
def _envelope(formula: str, charge: int, purity: Optional[float]) -> tuple:
    mzs, _, _ = predict_isotopes(formula, charge, purity, _ENVELOPE_FLOOR)
    return tuple(float(mz) for mz in mzs)


def rungs_own_line(line: Line, label: Optional[str], mz: float, tol_ppm: float) -> bool:
    """Whether an isotope line a member carries can be the rung's own: one
    whose label names isotopes of the rung's elements only, in no more atoms
    than it holds (a 13C line under a carbon-free cluster cannot), or, with no
    label to read (a sample folded without a run), one whose batch peak sits
    at a line of the rung's envelope."""
    if label:
        return Species(KIND_LIBRARY, line.ion_formula).can_have(label)
    rung = line.rung
    return any(
        abs(mz - exact) <= exact * tol_ppm * 1e-6
        for exact in _envelope(rung.formula, rung.charge, line.ladder.purity)
    )


def sample_decision(
    member: Member,
    claim: Claim,
    lines: Lines,
    monoisotopic: Optional[set[str]] = None,
) -> Optional[str]:
    """What becomes of one sample's member of a batch peak at a passing rung:
    None where the claim rewrites it, :data:`ALREADY` where it reads the rung
    already, :data:`WITH_MONOISOTOPIC` where the claim on the rung's
    monoisotopic peak moves it, else why it is kept.

    :param lines: The lines each sample's members name a batch peak the
        owner of. The claim takes the lines with the member, so a line the
        rung cannot have keeps it (:func:`rungs_own_line`).
    :param monoisotopic: For a heavier track, the samples the claim on the
        rung's monoisotopic peak rewrites.
    """
    line = claim.line
    if reads_rung(member, line):
        return ALREADY
    if member.hand:
        return KEPT_HAND_ASSIGNED
    if member.target:
        return KEPT_TARGET_LIBRARY
    if _listed(member, claim.row.anchor):
        return KEPT_LISTED
    if member.role == ROLE_ISO_CHILD or member.owner_batch_peak_id:
        # A monoisotopic track's claim names no monoisotopic peak of its own.
        if (
            member.owner_batch_peak_id in claim.monoisotopic
            and member.sample_item_id in (monoisotopic or ())
        ):
            return WITH_MONOISOTOPIC
        return KEPT_LINE_OF_ANOTHER
    if member.role in SOURCE_ROLES:
        return KEPT_CLAIMED_OTHERWISE
    carried = lines.get((member.sample_item_id, claim.batch_peak_id), ())
    if not all(rungs_own_line(line, *carried_line) for carried_line in carried):
        return KEPT_CARRIES_LINES
    return None


def decide_claim(
    claim: Claim,
    lines: Lines,
    monoisotopic: Optional[set[str]] = None,
) -> Claim:
    """Fill a claim's samples in, and hold a curated batch peak."""
    claim.samples = {
        sample: sample_decision(member, claim, lines, monoisotopic)
        for sample, member in sorted(claim.row.members.items())
        if sample in claim.segment_of
    }
    if claim.held is None and manual_pin_of(claim.row.anchor) is not None:
        claim.held = HELD_CURATED
    return claim


def _trace(row: Row) -> Trace:
    return Trace(
        row.batch_peak_id,
        trace_centre(row.anchor.mz, row.members.values()),
        float(row.anchor.mz_tol_ppm or 0.0),
        {sample: float(m.intensity or 0.0) for sample, m in row.members.items()},
    )


def strength(line: Line, order: dict[str, int]) -> tuple:
    """Which of two passing lines at one batch peak claims it, the least
    first: the one present more often where it passed, then the core the
    profile declares first, the fewer waters, the track.

    :param order: Core label -> its place among the profile's cores.
    """
    return (
        -line.presence,
        order[line.ladder.core.label],
        line.waters,
        line.track.label,
    )


def plan_partition(
    key: Partition,
    rows: list[Row],
    profile: Optional[str],
    times: dict[str, Optional[float]],
) -> PartitionPlan:
    """One partition's ladders read and its claims decided.

    :param profile: The profile a sample of the partition resolves to; None
        where none could be resolved.
    :param times: Sample -> its acquisition time in seconds, or None.
    """
    spectra = sorted({sample for row in rows for sample in row.members})
    ladders = water_ladders(profile) if profile else ()
    part = PartitionPlan(key, profile, ladders, spectra=len(spectra))
    part.unexplained_before = unexplained_share(
        [row.anchor for row in rows], {row.batch_peak_id: _summed(row) for row in rows}
    )
    if not ladders or not spectra:
        return part
    part.segments = acquisition_segments({s: times.get(s) for s in spectra})
    traces = [_trace(row) for row in rows]
    reading_partition = LadderPartition.of(traces, key[1], ladders)
    part.readings = read_ladders(ladders, reading_partition, part.segments)
    lines: dict[tuple[str, str, int], Line] = {}
    for reading in part.readings:
        for rung in reading.rungs:
            line_key = (reading.ladder.core.label, reading.track.label, rung.waters)
            line = lines.get(line_key)
            if line is None:
                line = Line(reading.ladder, reading.track, rung.waters)
                lines[line_key] = line
            line.readings[reading.segment.index] = rung
    part.lines = [line for line in lines.values() if line.passed_in]
    by_id = {row.batch_peak_id: row for row in rows}
    segment_of = {s: seg.index for seg in part.segments for s in seg.spectra}
    # Every batch peak at a passing line, and the lines that pass at it.
    at: dict[str, list[Line]] = defaultdict(list)
    keys_of: dict[tuple[str, int], dict[str, tuple[str, ...]]] = defaultdict(dict)
    for line in part.lines:
        keys = sorted(
            {key for r in line.readings.values() for key in r.keys if key in by_id}
        )
        keys_of[line.rung_key][line.track.label] = tuple(keys)
        for batch_peak_id in keys:
            at[batch_peak_id].append(line)
    order = {ladder.core.label: i for i, ladder in enumerate(ladders)}

    lines = lines_of(
        (
            member.sample_item_id,
            member.owner_batch_peak_id,
            member.isotope_label,
            member.mz,
            float(row.anchor.mz_tol_ppm or 0.0),
        )
        for row in rows
        for member in row.members.values()
    )
    claims: list[Claim] = []
    for batch_peak_id in sorted(at):
        candidates = sorted(at[batch_peak_id], key=lambda line: strength(line, order))
        line = candidates[0]
        passed = set(line.passed_in)
        claim = Claim(
            by_id[batch_peak_id],
            line,
            segment_of={
                sample: segment_of[sample]
                for sample in by_id[batch_peak_id].members
                if segment_of.get(sample) in passed
            },
            monoisotopic=(
                () if line.track.monoisotopic else keys_of[line.rung_key].get("M0", ())
            ),
            also_reads_as=[other.label for other in candidates[1:]],
        )
        claims.append(claim)
    # The monoisotopic tracks first: a heavier track's line moves with them.
    claims.sort(
        key=lambda c: (
            order[c.line.ladder.core.label],
            c.line.waters,
            not c.line.track.monoisotopic,
            c.line.track.label,
            c.batch_peak_id,
        )
    )
    decided: dict[str, Claim] = {}
    for claim in claims:
        monoisotopic = _monoisotopic_samples(claim, decided)
        decided[claim.batch_peak_id] = decide_claim(claim, lines, monoisotopic)
    part.claims = claims
    return part


def _monoisotopic_samples(claim: Claim, decided: dict[str, Claim]) -> Optional[set]:
    """The samples the claim on a heavier track's rung's monoisotopic peak
    rewrites, over every batch peak at that line that claims the rung; None
    for a monoisotopic track."""
    if claim.line.track.monoisotopic:
        return None
    samples: set[str] = set()
    for key in claim.monoisotopic:
        mono = decided.get(key)
        if (
            mono is None
            or mono.held is not None
            or mono.line.rung_key != claim.line.rung_key
            or not mono.line.track.monoisotopic
        ):
            continue
        samples.update(s for s, decision in mono.samples.items() if decision is None)
    return samples


def plan_batch(
    anchors: Iterable[Any],
    members: dict[str, dict[str, Member]],
    profiles: dict[Partition, Optional[str]],
    times: dict[str, Optional[float]],
) -> Plan:
    """Every partition's water ladders read, and their claims decided.

    :param anchors: The batch's anchors.
    :param members: Anchor id -> sample -> its member.
    :param profiles: Partition -> the profile a sample of it resolves to.
    :param times: Sample -> its acquisition time in seconds, or None.
    """
    by_partition: dict[Partition, list[Row]] = defaultdict(list)
    for anchor in sorted(anchors, key=lambda a: a.batch_peak_id):
        by_partition[partition_of(anchor)].append(
            Row(anchor, members.get(anchor.batch_peak_id, {}))
        )
    plan = Plan()
    for key in sorted(by_partition, key=lambda p: (str(p[0]), str(p[1]))):
        plan.partitions.append(
            plan_partition(key, by_partition[key], profiles.get(key), times)
        )
    return plan


# --- the records -----------------------------------------------------------------


def _member_reading(member: Member, reason: str) -> dict:
    return {
        "sample_item_id": member.sample_item_id,
        "reason": reason,
        "role": member.role,
        "formula": member.formula,
        "ion_formula": member.ion_formula,
        "tier": member.tier,
    }


def files_recorded(claim: Claim) -> tuple[list[dict], dict[str, int]]:
    """The samples a record lists with their readings, those the claim kept,
    and their counts by why."""
    entries = [
        _member_reading(claim.row.members[sample], reason)
        for sample, reason in sorted(claim.samples.items())
        if reason not in (None, ALREADY, WITH_MONOISOTOPIC)
    ]
    counts = dict(sorted(Counter(entry["reason"] for entry in entries).items()))
    return entries[:MAX_RECORDED_FILES], counts


def segment_readings(claim: Claim, segments: list[Segment]) -> list[dict]:
    """The rung's reading in each segment its ladder reached it in."""
    sizes = {segment.index: len(segment.spectra) for segment in segments}
    return [
        {"segment": index, "spectra": sizes.get(index), **reading.to_json()}
        for index, reading in sorted(claim.line.readings.items())
    ]


def _anchor_reading(anchor: Any) -> dict:
    return {
        "role": anchor.consensus_role,
        "formula": anchor.consensus_formula,
        "ion_formula": anchor.consensus_ion_formula,
        "tier": anchor.consensus_tier,
    }


def ladder_record(
    claim: Claim,
    segments: list[Segment],
    *,
    before: dict,
    reads: dict,
    batch_peak_run_id: Optional[str],
    displaced_members: Optional[dict[str, dict]] = None,
    rows_rewritten: int = 0,
    lines_rewritten: int = 0,
    rows_kept: Optional[dict[str, int]] = None,
) -> dict:
    """The record a batch peak at a passing rung carries."""
    files, by_reason = files_recorded(claim)
    displaced_members = displaced_members or {}
    turned = reads.get("role") == ROLE_REAGENT and before.get("role") != ROLE_REAGENT
    moved = turned and bool(before.get("formula"))
    return {
        "rung": claim.line.to_json(),
        "segments": segment_readings(claim, segments),
        "passed_in": claim.line.passed_in,
        "samples": len(claim.samples),
        "held": claim.held,
        "also_reads_as": list(claim.also_reads_as),
        "claimed": bool(displaced_members),
        "members_rewritten": len(displaced_members),
        "rows_rewritten": rows_rewritten,
        "lines_rewritten": lines_rewritten,
        "rows_kept": dict(sorted((rows_kept or {}).items())),
        "files_by_reason": by_reason,
        "files_recorded": files,
        "read_before": before,
        "turned": turned,
        "moved_analyte": moved,
        "displaced_members": displaced_members,
        "reads": reads,
        "batch_peak_run_id": batch_peak_run_id,
    }


def row_record(claim: Claim, sample: str, batch_peak_run_id: Optional[str]) -> dict:
    """What a sample's row the claim rewrote records of the batch's reading:
    the rung, the segment and its numbers there."""
    segment = claim.segment_of.get(sample)
    reading = claim.line.readings.get(segment) if segment is not None else None
    return {
        "batch_peak_id": claim.batch_peak_id,
        "core": claim.line.ladder.core.label,
        "waters": claim.line.waters,
        "track": claim.line.track.label,
        "segment": segment,
        "presence": None if reading is None else round(reading.presence, 4),
        "decoy_presence": (
            None
            if reading is None or reading.decoy_presence is None
            else round(reading.decoy_presence, 4)
        ),
        "references": list(WATER_RUNG_REFERENCES),
        "batch_peak_run_id": batch_peak_run_id,
    }


# --- the run ----------------------------------------------------------------------


async def _load_extras(
    sample_batch_id: str, anchors: list[Any], members: dict[str, dict[str, Member]]
) -> tuple[dict[Partition, Optional[str]], dict[str, Optional[float]]]:
    """Each partition's profile, resolved on its first sample as a run of it
    resolves its chemistry, and each sample's acquisition time."""
    async with async_session() as session:
        rows = (
            await session.execute(
                select(Sample.sample_item_id, Sample.datetime_utc).where(
                    Sample.sample_batch_id == sample_batch_id
                )
            )
        ).all()
    times = {
        row.sample_item_id: (
            row.datetime_utc.timestamp() if row.datetime_utc is not None else None
        )
        for row in rows
    }
    first: dict[Partition, str] = {}
    for anchor in sorted(anchors, key=lambda a: a.batch_peak_id):
        for sample in sorted(members.get(anchor.batch_peak_id, {})):
            key = partition_of(anchor)
            if key not in first or sample < first[key]:
                first[key] = sample
    profiles: dict[Partition, Optional[str]] = {}
    config = PeakAssignmentConfig()
    for key, sample in sorted(first.items(), key=lambda kv: (str(kv[0]), kv[1])):
        try:
            profiles[key] = (await _mode_chemistry(sample, config)).profile
        except Exception as exc:  # noqa: BLE001 - a mode no profile resolves reads no ladder
            runtime.logger.info(
                f"Water ladders: no profile for partition {key}: "
                f"{type(exc).__name__}: {exc}"
            )
            profiles[key] = None
    return profiles, times


@dataclass
class Written:
    """What one claim's write did."""

    claim: Claim
    claimed: bool
    members_rewritten: int = 0
    rows_rewritten: int = 0
    lines_rewritten: int = 0
    rows_kept: dict[str, int] = field(default_factory=dict)
    turned: bool = False
    moved: bool = False


def _write_claim(
    claim: Claim,
    anchors: dict[str, Any],
    members: dict[str, dict[str, Any]],
    lines: dict[str, list[tuple[Any, Any]]],
    rows: dict[str, Any],
    in_app_runs: set[str],
    batch_peak_run_id: Optional[str],
) -> tuple[Written, dict[str, dict]]:
    """Claim one batch peak as its rung in the samples the claim takes.

    :param members: Anchor id -> sample -> its member row, for the claim's
        batch peak and the rung's monoisotopic ones.
    :param lines: Sample -> the (member row, anchor) of each isotope line the
        sample's member owns.
    :return: What was written, and each rewritten member's earlier state.
    """
    line = claim.line
    rung = line.rung
    anchor = anchors[claim.batch_peak_id]
    registry = list(anchor.candidates or [])
    displaced: dict[str, dict] = {}
    rows_kept: Counter = Counter()
    rows_rewritten = lines_rewritten = 0
    own_members = members.get(claim.batch_peak_id, {})
    for sample in sorted(claim.samples):
        if claim.samples[sample] is not None:
            continue
        member = own_members[sample]
        # A heavier line is the rung's monoisotopic peak's line where that peak
        # is the rung in the sample.
        owner_anchor = owner_row_id = None
        if not line.track.monoisotopic:
            for key in claim.monoisotopic:
                mono = members.get(key, {}).get(sample)
                if (
                    mono is not None
                    and key in anchors
                    and mono.role == role_code(ROLE_REAGENT)
                    and mono.owner_batch_peak_id is None
                    and resolve_candidate(anchors[key].candidates, mono.candidate).get(
                        "ion_formula"
                    )
                    == line.ion_formula
                ):
                    owner_anchor = key
                    mono_row = rows.get(mono.peak_assignment_id or "")
                    if (
                        mono_row is not None
                        and mono_row.role == ROLE_REAGENT
                        and mono_row.ion_formula == line.ion_formula
                    ):
                        owner_row_id = mono_row.peak_assignment_id
                    break
        record = row_record(claim, sample, batch_peak_run_id)
        own_row = rows.get(member.peak_assignment_id or "")
        if own_row is None:
            rows_kept[ROW_KEPT_NO_ROW] += 1
        elif own_row.peak_assignment_run_id not in in_app_runs:
            rows_kept[ROW_KEPT_IMPORTED] += 1
        else:
            values = water_rung_row(
                _as_dict(own_row),
                rung,
                record,
                exact_mz=line.exact_mz,
                isotope_label=None if line.track.monoisotopic else line.track.label,
                owner_peak_assignment_id=owner_row_id,
            )
            for key, value in values.items():
                setattr(own_row, key, value)
            rows_rewritten += 1
        displaced[sample] = member_state(member)
        member.candidate = candidate_index(
            registry, None, line.ion_formula, None, source=SOURCE_REAGENT
        )
        member.role = role_code(ROLE_REAGENT)
        member.tier = tier_code(TIER_UNASSIGNED)
        member.fit_score = None
        member.p_correct = None
        member.owner_batch_peak_id = owner_anchor
        # The member's isotope lines go with it, as a library ion's do.
        for line_member, line_anchor in lines.get(sample, ()):
            line_registry = list(line_anchor.candidates or [])
            line_member.candidate = candidate_index(
                line_registry, None, line.ion_formula, None, source=SOURCE_REAGENT
            )
            if line_registry != (line_anchor.candidates or []):
                line_anchor.candidates = line_registry
            line_member.role = role_code(ROLE_REAGENT)
            line_member.tier = tier_code(TIER_UNASSIGNED)
            line_member.fit_score = None
            line_member.p_correct = None
            line_row = rows.get(line_member.peak_assignment_id or "")
            if (
                own_row is not None
                and line_row is not None
                and line_row.peak_assignment_run_id in in_app_runs
                and line_row.owner_peak_assignment_id == own_row.peak_assignment_id
            ):
                for key, value in water_rung_row(
                    _as_dict(line_row),
                    rung,
                    record,
                    exact_mz=line.exact_mz,
                    keep_line=True,
                ).items():
                    setattr(line_row, key, value)
            lines_rewritten += 1
    if registry != (anchor.candidates or []):
        anchor.candidates = registry
    return (
        Written(
            claim,
            True,
            members_rewritten=len(displaced),
            rows_rewritten=rows_rewritten,
            lines_rewritten=lines_rewritten,
            rows_kept=dict(rows_kept),
        ),
        displaced,
    )


async def _write_chunk(
    sample_batch_id: str,
    claims: list[Claim],
    segments: dict[Partition, list[Segment]],
    batch_peak_run_id: Optional[str],
) -> list[Written]:
    """Write a chunk of claims in one transaction under the fold lock: decide
    each again on the ledger as it stands, claim the samples it takes, record
    every one, and recompute the consensus of each batch peak the claims
    touched. A rung's monoisotopic tracks are written before its heavier ones,
    so a heavier line sees what its rung's peak reads by then."""
    ids = sorted({claim.batch_peak_id for claim in claims})
    read_ids = sorted(set(ids) | {key for c in claims for key in c.monoisotopic})
    async with async_session() as session:
        await _acquire_batch_fold_lock(session, sample_batch_id)
        anchors = {
            anchor.batch_peak_id: anchor
            for anchor in (
                await session.execute(
                    select(BatchPeak).where(BatchPeak.batch_peak_id.in_(read_ids))
                )
            )
            .scalars()
            .all()
        }
        members: dict[str, dict[str, Any]] = defaultdict(dict)
        for member in (
            (
                await session.execute(
                    select(BatchPeakOccurrence).where(
                        BatchPeakOccurrence.batch_peak_id.in_(read_ids)
                    )
                )
            )
            .scalars()
            .all()
        ):
            members[member.batch_peak_id][member.sample_item_id] = member
        line_members = (
            (
                await session.execute(
                    select(BatchPeakOccurrence).where(
                        BatchPeakOccurrence.owner_batch_peak_id.in_(ids),
                        BatchPeakOccurrence.role == role_code(ROLE_ISO_CHILD),
                    )
                )
            )
            .scalars()
            .all()
        )
        line_anchor_ids = sorted(
            {m.batch_peak_id for m in line_members} - set(read_ids)
        )
        line_anchors = dict(anchors)
        if line_anchor_ids:
            for anchor in (
                (
                    await session.execute(
                        select(BatchPeak).where(
                            BatchPeak.batch_peak_id.in_(line_anchor_ids)
                        )
                    )
                )
                .scalars()
                .all()
            ):
                line_anchors[anchor.batch_peak_id] = anchor
        marks = await _row_marks(session, sample_batch_id, read_ids)
        links = [
            m.peak_assignment_id
            for by_sample in members.values()
            for m in by_sample.values()
            if m.peak_assignment_id
        ] + [m.peak_assignment_id for m in line_members if m.peak_assignment_id]
        rows = await _read_rows(session, sorted(set(links)))
        # Every line a claimed batch peak's members own, labelled as its row
        # names it, where it sits.
        carried = lines_of(
            (
                m.sample_item_id,
                m.owner_batch_peak_id,
                getattr(rows.get(m.peak_assignment_id or ""), "isotope_label", None),
                mz_from_delta(line_anchors[m.batch_peak_id].mz, m.mz_delta_ppm),
                float(line_anchors[m.batch_peak_id].mz_tol_ppm or 0.0),
            )
            for m in line_members
        )
        run_ids = sorted({row.peak_assignment_run_id for row in rows.values()})
        in_app_runs = set(
            (
                await session.execute(
                    select(PeakAssignmentRun.peak_assignment_run_id).where(
                        PeakAssignmentRun.peak_assignment_run_id.in_(run_ids),
                        PeakAssignmentRun.engine == IN_APP_ENGINE,
                    )
                )
            )
            .scalars()
            .all()
        )

        def fresh_row(anchor: Any) -> Row:
            return Row(
                anchor,
                {
                    sample: _member_from(member, anchor.mz, anchor.candidates, marks)
                    for sample, member in members.get(anchor.batch_peak_id, {}).items()
                },
            )

        before = {key: _anchor_reading(anchor) for key, anchor in anchors.items()}
        written: list[Written] = []
        displaced_of: dict[str, dict[str, dict]] = {}
        touched: set[str] = set()
        decided: dict[str, Claim] = {}
        for planned in claims:
            anchor = anchors.get(planned.batch_peak_id)
            if anchor is None:
                continue
            fresh = Claim(
                fresh_row(anchor),
                planned.line,
                segment_of=planned.segment_of,
                monoisotopic=planned.monoisotopic,
                also_reads_as=planned.also_reads_as,
            )
            decide_claim(fresh, carried, _monoisotopic_samples(fresh, decided))
            decided[fresh.batch_peak_id] = fresh
            if not fresh.claims:
                written.append(Written(fresh, False))
                continue
            by_sample: dict[str, list[tuple[Any, Any]]] = defaultdict(list)
            for line in line_members:
                if line.owner_batch_peak_id == anchor.batch_peak_id:
                    by_sample[line.sample_item_id].append(
                        (line, line_anchors[line.batch_peak_id])
                    )
            done, displaced = _write_claim(
                fresh,
                anchors,
                members,
                by_sample,
                rows,
                in_app_runs,
                batch_peak_run_id,
            )
            touched.add(anchor.batch_peak_id)
            touched |= {
                line_anchor.batch_peak_id
                for pairs in by_sample.values()
                for _, line_anchor in pairs
            }
            displaced_of[anchor.batch_peak_id] = displaced
            written.append(done)
        now = datetime.now(timezone.utc)
        if touched:
            await session.flush()
            await _recompute_consensus(session, touched, now)
        for done in written:
            anchor = anchors.get(done.claim.batch_peak_id)
            if anchor is None:
                continue
            provenance = dict(anchor.provenance or {})
            record = ladder_record(
                done.claim,
                segments.get(partition_of(anchor), []),
                before=before[anchor.batch_peak_id],
                reads=reading_of(anchor),
                batch_peak_run_id=batch_peak_run_id,
                displaced_members=displaced_of.get(anchor.batch_peak_id),
                rows_rewritten=done.rows_rewritten,
                lines_rewritten=done.lines_rewritten,
                rows_kept=done.rows_kept,
            )
            done.turned = done.claimed and record["turned"]
            done.moved = done.claimed and record["moved_analyte"]
            provenance[WATER_LADDER_KEY] = record
            anchor.provenance = provenance
            anchor.batch_peak_utc_modified = now
        await touch_ledger(session, sample_batch_id)
        await session.commit()
    return written


def _chunks(claims: list[Claim]) -> list[list[Claim]]:
    """The claims in write order, cut into chunks of at least
    :data:`WRITE_CHUNK` batch peaks between two rungs, never through one."""
    chunks: list[list[Claim]] = []
    current: list[Claim] = []
    previous = None
    for claim in claims:
        rung = (partition_of(claim.row.anchor), claim.line.rung_key)
        if current and rung != previous and len(current) >= WRITE_CHUNK:
            chunks.append(current)
            current = []
        current.append(claim)
        previous = rung
    if current:
        chunks.append(current)
    return chunks


async def _clear_stale_records(sample_batch_id: str, kept: set[str]) -> int:
    """Take the record off every batch peak this measurement did not claim at:
    a new measurement replaces every record."""
    async with async_session() as session:
        await _acquire_batch_fold_lock(session, sample_batch_id)
        anchors = (
            (
                await session.execute(
                    select(BatchPeak).where(
                        BatchPeak.sample_batch_id == sample_batch_id
                    )
                )
            )
            .scalars()
            .all()
        )
        cleared = 0
        for anchor in anchors:
            if anchor.batch_peak_id in kept or water_ladder_record_of(anchor) is None:
                continue
            provenance = dict(anchor.provenance or {})
            provenance.pop(WATER_LADDER_KEY, None)
            anchor.provenance = provenance
            cleared += 1
        if cleared:
            await touch_ledger(session, sample_batch_id)
        await session.commit()
    return cleared


async def _unexplained_after(sample_batch_id: str) -> dict[Partition, Optional[float]]:
    """Each partition's unexplained share as the ledger stands after the
    writes."""
    async with async_session() as session:
        anchors = (
            (
                await session.execute(
                    select(BatchPeak).where(
                        BatchPeak.sample_batch_id == sample_batch_id
                    )
                )
            )
            .scalars()
            .all()
        )
        intensity: dict[str, float] = defaultdict(float)
        for batch_peak_id, value in (
            await session.execute(
                select(BatchPeakOccurrence.batch_peak_id, BatchPeakOccurrence.intensity)
                .join(
                    BatchPeak,
                    BatchPeak.batch_peak_id == BatchPeakOccurrence.batch_peak_id,
                )
                .where(BatchPeak.sample_batch_id == sample_batch_id)
            )
        ).all():
            intensity[batch_peak_id] += float(value or 0.0)
    by_partition: dict[Partition, list[Any]] = defaultdict(list)
    for anchor in anchors:
        by_partition[partition_of(anchor)].append(anchor)
    return {
        key: unexplained_share(group, intensity) for key, group in by_partition.items()
    }


#: The counts a partition reports, and the run's summary sums.
_COUNTS = (
    "spectra",
    "rungs_tested",
    "rungs_passed",
    "batch_peaks",
    "claimed",
    "held",
    "members_rewritten",
    "rows_rewritten",
    "lines_rewritten",
    "peaks_turned",
    "rows_moved",
)


def summarise(
    plan: Plan,
    written: list[Written],
    after: dict[Partition, Optional[float]],
    cleared: int = 0,
) -> dict:
    """The run's summary: per partition its segments, each ladder's reach per
    segment, the counts and the unexplained share before and after; over the
    batch the sums, and the rungs claimed at, each as the write decided it."""
    final = {done.claim.batch_peak_id: done for done in written}
    partitions = []
    listed = []
    rows_kept: Counter = Counter()
    samples_kept: Counter = Counter()
    held: Counter = Counter()
    for part in plan.partitions:
        counts = {key: 0 for key in _COUNTS}
        counts["spectra"] = part.spectra
        counts["rungs_tested"] = part.rungs_tested
        counts["rungs_passed"] = part.rungs_passed
        for planned in part.claims:
            done = final.get(planned.batch_peak_id)
            claim = planned if done is None else done.claim
            counts["batch_peaks"] += 1
            if claim.held is not None:
                counts["held"] += 1
                held[claim.held] += 1
            if done is not None and done.claimed:
                counts["claimed"] += 1
                counts["members_rewritten"] += done.members_rewritten
                counts["rows_rewritten"] += done.rows_rewritten
                counts["lines_rewritten"] += done.lines_rewritten
                rows_kept.update(done.rows_kept)
            samples_kept.update(
                reason
                for reason in claim.samples.values()
                if reason not in (None, ALREADY, WITH_MONOISOTOPIC)
            )
            listed.append(claim)
        partitions.append(
            {
                "ionization_mode_id": part.key[0],
                "instrument_type": part.key[1],
                "profile": part.profile,
                "cores": [ladder.core.label for ladder in part.ladders],
                "segments": [segment.to_json() for segment in part.segments],
                "ladders": [
                    reading.to_json()
                    for reading in part.readings
                    if reading.core_presence > 0 or reading.rungs
                ],
                **counts,
                "unexplained_before": _share(part.unexplained_before),
                "unexplained_after": _share(after.get(part.key)),
            }
        )
    turned, moved = _turned(written)
    for entry in partitions:
        key = (entry["ionization_mode_id"], entry["instrument_type"])
        entry["peaks_turned"] = turned.get(key, 0)
        entry["rows_moved"] = moved.get(key, 0)
    return {
        "thresholds": thresholds(),
        "partitions": partitions,
        **{key: sum(p[key] for p in partitions) for key in _COUNTS},
        "held_by_reason": dict(sorted(held.items())),
        "rows_kept": dict(sorted(rows_kept.items())),
        "samples_kept": dict(sorted(samples_kept.items())),
        "records_cleared": cleared,
        "rungs_listed": [claim.to_json() for claim in listed[:MAX_LISTED]],
        "rungs_listed_at_most": MAX_LISTED,
    }


def _share(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(value, 4)


def _turned(written: list[Written]) -> tuple[dict, dict]:
    """Per partition, the batch peaks whose reading the claims turned to the
    reagent's ion, and of those the ones that read an analyte before: the rows
    that left the ledger as analytes."""
    turned: Counter = Counter()
    moved: Counter = Counter()
    for done in written:
        key = partition_of(done.claim.row.anchor)
        turned[key] += done.turned
        moved[key] += done.moved
    return dict(turned), dict(moved)


async def run_batch_water_ladders(
    sample_batch_id: str, batch_peak_run_id: Optional[str] = None
) -> dict:
    """Measure the batch's water ladders, claim the passing rungs, and record
    every one.

    :return: The run's summary (:func:`summarise`).
    """
    anchors, members, _ = await _load_batch(sample_batch_id)
    profiles, times = await _load_extras(sample_batch_id, anchors, members)
    plan = await asyncio.to_thread(plan_batch, anchors, members, profiles, times)
    segments = {part.key: part.segments for part in plan.partitions}
    written: list[Written] = []
    for chunk in _chunks(plan.claims):
        written.extend(
            await _write_chunk(sample_batch_id, chunk, segments, batch_peak_run_id)
        )
    cleared = await _clear_stale_records(
        sample_batch_id, {claim.batch_peak_id for claim in plan.claims}
    )
    after = await _unexplained_after(sample_batch_id)
    return summarise(plan, written, after, cleared)


def measuring_outcome(summary: dict, sample_batch_id: str) -> dict:
    """The controller result the background-task decorator reports from."""
    notification_data = {"sample_batch_id": sample_batch_id}
    if not any(part["cores"] for part in summary["partitions"]):
        return {
            "status": "partial",
            "message": (
                "No water ladder was read: no ionization mode of the batch "
                "resolves to a profile that declares a reagent core."
            ),
            "data": summary,
            "_notification_data": notification_data,
        }
    return {
        "status": "success",
        "message": (
            f"Read {summary['rungs_tested']} water-ladder rungs: "
            f"{summary['rungs_passed']} passed, {summary['claimed']} batch peak"
            f"{'s' if summary['claimed'] != 1 else ''} claimed in "
            f"{summary['members_rewritten']} sample"
            f"{'s' if summary['members_rewritten'] != 1 else ''}; "
            f"{summary['peaks_turned']} now read as the reagent, "
            f"{summary['rows_moved']} of them analytes before."
        ),
        "data": summary,
        "_notification_data": notification_data,
    }


@api_controller_background_task(
    success_notification_rooms=["sample_batch_id"],
    success_reload=[("peak_assignment", "sample_batch_id")],
    error_notification_rooms=["sample_batch_id"],
)
async def measure_batch_water_ladders(
    sample_batch_id: str,
    independent_transaction: bool = False,
    user_id: int | None = None,
    process_id: str | None = None,
    parent_id: str | None = None,
) -> dict:
    """Measure the batch's water ladders, as a batch run. Rewrites the members
    and the samples' in-app rows of each passing rung's batch peaks in the
    segments it passed in; emits ``peak_assignment_reload`` so the ledgers
    refresh."""
    run_id = await start_run(
        sample_batch_id, ACTION_WATER_LADDERS, config=thresholds(), user_id=user_id
    )
    try:
        summary = await run_batch_water_ladders(
            sample_batch_id, batch_peak_run_id=run_id
        )
    except Exception as exc:
        await fail_run(sample_batch_id, run_id, f"{type(exc).__name__}: {exc}")
        raise
    await complete_run(sample_batch_id, run_id, summary=summary)
    return measuring_outcome(summary, sample_batch_id)


__all__ = [
    "HELD_CURATED",
    "KEPT_LISTED",
    "WITH_MONOISOTOPIC",
    "Claim",
    "Line",
    "Plan",
    "PartitionPlan",
    "decide_claim",
    "explained",
    "ladder_record",
    "measure_batch_water_ladders",
    "measuring_outcome",
    "plan_batch",
    "plan_partition",
    "reads_rung",
    "rungs_own_line",
    "strength",
    "run_batch_water_ladders",
    "sample_decision",
    "summarise",
    "unexplained_share",
]
