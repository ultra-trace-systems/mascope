"""Merged isotopologue rows on the batch ledger: step 4.2's fifth rider (4.2f).

Each file's run reads its peaks on its own, and a run that did not read a dim
isotope line as its parent's commits it as an analyte of its own; the fold
merges that reading across the batch into a row of the batch ledger which is
no compound. "Strip isotopologue rows" reads every merged row against the lines
of the batch's other merged ions
(``mascope_tools.composition.merged_isotopologues``) and strips the rows that
are a brighter merged ion's isotope line - the batch-level form of step 2.4e's
per-file claims:

1. **Read at the lines.** A merged ion is a batch peak whose consensus commits
   a formula as its monoisotopic line, unclaimed and no batch peak's
   isotopologue (``batch_isotopologues.is_parent``). Each one predicts its 13C
   line and its diagnostic isotope lines at its trace centre (its members'
   median m/z) and at the resolving power its anchor was minted at. A merged
   row whose own trace centre sits at a line of a brighter merged ion below it
   - within four times the partition's mass scatter, and 1 ppm at least - is
   read against that parent: its intensity over the parent's in every sample
   holding both, as a factor of the line's predicted share.
2. **Stripped where the share holds**: a median factor of 0.4 to 1.4 over
   three or more samples, fewer than a tenth of them above 2. A row the lines
   of two parents reach goes to the one whose median factor is nearest one,
   then the one read over more samples, then the brighter, then the lower id.
   Rows are read from the lowest m/z up, so a row stripped is no parent of a
   row above it.
3. **Never stripped**: a curated row (a batch peak pinned by hand), a row a
   sample of which is a hand assignment, a row that carries its own 13C line
   (the batch peak at its 13C position is its isotopologue), and a row too
   few of whose samples hold the parent's reading for its members' vote to
   turn. Each is held and listed with its reason.
4. **The per-sample ledgers agree.** In each sample holding both, the row's
   member becomes an isotopologue child of the parent's member, and where the
   sample's in-app run is the one folded, its row on that peak an
   isotopologue child of its row on the parent's peak, recorded on both rows
   (``service.read_as_batch_isotopologue``). A sample is left as it is where
   the parent's member reads another reading, where a pre-pass claimed the
   row's member, or where the row's member carries lines of its own there;
   an imported run's rows are another engine's record and are never
   rewritten. The consensus then reads the row as the parent's line through
   its members, as it reads every isotopologue: it leaves the ledger as an M0
   and folds under its parent.

The stripped row carries ``provenance.merged_isotopologue_of``
(``batch_peaks.MERGED_ISOTOPOLOGUE_OF_KEY``): its parent, the line, the share
read, the reading it displaced, and per sample what each rewritten member read
before. The parent carries ``provenance.merged_isotopologues``
(``batch_peaks.MERGED_ISOTOPOLOGUES_KEY``): the reading its lines were read
against, and each line it took. The run's summary lists the stripped pairs and
the held ones.

The reading runs outside the fold lock and the writes under it, a chunk of
pairs per transaction. Each pair is checked again against the ledger as it
stands then: a row or a parent whose reading moved since is left out and
counted, and a row whose samples no longer turn its vote is held.

Design: ``docs/dev/assignment_quality_plan.md``, step 4.2, the fifth rider.
"""

from __future__ import annotations

import bisect
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from sqlalchemy import select

from mascope_backend.api.lib.api_features import api_controller_background_task
from mascope_backend.api.new.peak_assignments.batch_isotopologues import (
    Partition,
    is_parent,
    partition_of,
    trace_centre,
)
from mascope_backend.api.new.peak_assignments.batch_peaks import (
    MERGED_ISOTOPOLOGUE_OF_KEY,
    MERGED_ISOTOPOLOGUES_KEY,
    ROLE_ISO_CHILD,
    ROLE_M0,
    ROLE_REAGENT,
    SOURCE_ROLES,
    candidate_index,
    manual_pin_of,
    member_state,
    mz_from_delta,
    resolution_from_tol_ppm,
    resolve_candidate,
    role_code,
    role_name,
    standing_merged_isotopologues_record,
)
from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
    _acquire_batch_fold_lock,
    _recompute_consensus,
)
from mascope_backend.api.new.peak_assignments.batch_runs import (
    ACTION_STRIP_ISOTOPOLOGUES,
    complete_run,
    fail_run,
    start_run,
    touch_ledger,
)
from mascope_backend.api.new.peak_assignments.config import IN_APP_ENGINE
from mascope_backend.api.new.peak_assignments.engine import SOURCE_MANUAL
from mascope_backend.api.new.peak_assignments.envelope_claims import (
    isotopologue_names,
)
from mascope_backend.api.new.peak_assignments.service import (
    BatchLine,
    read_as_batch_isotopologue,
    with_batch_isotopologue_line,
)
from mascope_backend.db import (
    BatchPeak,
    BatchPeakOccurrence,
    PeakAssignment,
    PeakAssignmentRun,
    async_session,
)
from mascope_tools.composition.diagnostic_isotopologues import DiagnosticIsotopologue
from mascope_tools.composition.merged_isotopologues import (
    VERDICT_OFF_RATIO,
    ShareReading,
    at_position,
    brighter,
    mass_scatter_ppm,
    position_window_ppm,
    predict_isotopologue_lines,
    read_share,
    thresholds,
)


#: Why a row whose share held was not stripped.
HELD_CURATED = "curated"
HELD_HAND_ASSIGNED = "hand_assigned"
HELD_OWN_13C = "own_13c"
HELD_PARENT_ABSENT = "parent_absent"

#: Why a sample of a stripped row was left as it was.
KEPT_PARENT_ABSENT = "parent_absent"
KEPT_PARENT_READS_OTHERWISE = "parent_reads_otherwise"
KEPT_CLAIMED = "claimed"
KEPT_CARRIES_LINES = "carries_lines"

#: A sample already holding the row as the parent's line.
ALREADY = "already"

#: The stripped pairs a run's summary lists at most, and the held ones; the
#: counts are whole, and every stripped row carries its record on the ledger.
MAX_LISTED = 500

#: Pairs written per transaction under the fold lock.
WRITE_CHUNK = 50

#: Sample rows read per statement: a chunk's members link one row each, and
#: asyncpg refuses a statement of more than 32767 bind parameters.
ROW_READ_CHUNK = 5000

#: Why a rewritten member's sample row was left as it was: no row is linked
#: (a sample folded without a run, or its run pruned), the parent's member
#: links none in the same run, or the run is an import, another engine's
#: record.
ROW_KEPT_NO_ROW = "no_row"
ROW_KEPT_NO_PARENT_ROW = "no_parent_row"
ROW_KEPT_IMPORTED = "imported"


# --- what is read -------------------------------------------------------------


@dataclass(frozen=True)
class Member:
    """What the reading reads of one member.

    :ivar formula: The neutral formula its registry entry names, or None.
    :ivar ionization_mechanism_id: The mechanism its registry entry names.
    :ivar source: Where its registry entry's identity came from.
    """

    sample_item_id: str
    sample_peak_id: str
    intensity: Optional[float]
    mz: float
    role: Optional[str] = None
    owner_batch_peak_id: Optional[str] = None
    formula: Optional[str] = None
    ionization_mechanism_id: Optional[str] = None
    source: Optional[str] = None


@dataclass
class Row:
    """One batch peak and its members, sample -> member, at its trace centre."""

    anchor: Any
    members: dict[str, Member] = field(default_factory=dict)
    centre: float = 0.0

    @property
    def batch_peak_id(self) -> str:
        return self.anchor.batch_peak_id


@dataclass(frozen=True)
class Line:
    """One isotope line of a merged ion, where its m/z puts it."""

    mz: float
    parent: Row
    isotopologue: DiagnosticIsotopologue


@dataclass
class Pair:
    """A merged row read as one parent's line.

    :ivar reason: None for a pair stripped, else why it was held.
    """

    row: Row
    line: Line
    reading: ShareReading
    distance_ppm: float
    window_ppm: float
    scatter_ppm: Optional[float]
    reason: Optional[str] = None

    @property
    def parent(self) -> Row:
        return self.line.parent

    def to_json(self) -> dict:
        """The pair as a run's summary lists it."""
        row, parent = self.row.anchor, self.parent.anchor
        entry = {
            "batch_peak_id": row.batch_peak_id,
            "mz": round(self.row.centre, 6),
            "formula": row.consensus_formula,
            "ion_formula": row.consensus_ion_formula,
            "tier": row.consensus_tier,
            "parent_batch_peak_id": parent.batch_peak_id,
            "parent_mz": round(self.parent.centre, 6),
            "parent_formula": parent.consensus_formula,
            "parent_ion_formula": parent.consensus_ion_formula,
            "label": self.line.isotopologue.label,
            "ratio": round(self.line.isotopologue.ratio, 6),
            "distance_ppm": round(self.distance_ppm, 3),
            **self.reading.to_json(),
        }
        if self.reason is not None:
            entry["reason"] = self.reason
        return entry


@dataclass
class Plan:
    """What the reading found, before anything is written.

    :ivar stripped: The pairs to strip, in the order they were read.
    :ivar held: The pairs whose share held and that a rule kept.
    :ivar counts: Per partition, the counts the run's summary sums.
    """

    stripped: list[Pair] = field(default_factory=list)
    held: list[Pair] = field(default_factory=list)
    counts: dict[Partition, dict] = field(default_factory=dict)


#: The counts a partition keeps, and the run's summary sums.
_COUNTS = (
    "merged_rows",
    "at_a_line",
    "off_ratio",
    "untestable",
    "held",
    "stripped",
)


def _empty_counts() -> dict:
    return {key: 0 for key in _COUNTS}


# --- the reading (pure) ---------------------------------------------------------


def lines_of(parent: Row) -> list[Line]:
    """A merged ion's resolved 13C and diagnostic isotope lines, at its trace
    centre and the resolving power its anchor was minted at."""
    anchor = parent.anchor
    resolution = resolution_from_tol_ppm(anchor.mz_tol_ppm)
    return [
        Line(parent.centre + isotopologue.mz_offset, parent, isotopologue)
        for isotopologue in predict_isotopologue_lines(
            anchor.consensus_ion_formula, resolution
        )
        if isotopologue.resolved
    ]


def carries_own_13c(rows: Iterable[Row], window_ppm: float) -> set[str]:
    """The merged ions whose 13C line the batch already holds as theirs: a
    batch peak at the ion's 13C position whose family link names the ion.

    Such a row has an envelope of its own in the batch, and is never read as
    another ion's line.
    """
    rows = list(rows)
    by_id = {row.batch_peak_id: row for row in rows}
    c13: dict[str, float] = {}
    carrying = set()
    for row in rows:
        owner_id = row.anchor.isotopologue_of
        owner = by_id.get(owner_id) if owner_id else None
        if owner is None:
            continue
        if owner_id not in c13:
            found = [
                line.mz for line in lines_of(owner) if line.isotopologue.label == "13C"
            ]
            c13[owner_id] = found[0] if found else None
        line_mz = c13[owner_id]
        if line_mz is not None and at_position(row.centre, line_mz, window_ppm):
            carrying.add(owner_id)
    return carrying


def _reads_parent(member: Optional[Member], parent: Row) -> bool:
    """Whether the parent's member in a sample reads the parent's consensus:
    its monoisotopic line, of that formula through that mechanism."""
    anchor = parent.anchor
    return (
        member is not None
        and member.role == ROLE_M0
        and member.formula == anchor.consensus_formula
        and member.ionization_mechanism_id == anchor.ionization_mechanism_id
    )


def sample_decision(
    row: Row, parent: Row, sample_item_id: str, carrying: set[tuple[str, str]]
) -> Optional[str]:
    """What becomes of a stripped row's member in one sample: None where it is
    rewritten as the parent's line, :data:`ALREADY` where it is that already,
    else why it is left as it is.

    :param carrying: (sample, batch peak) pairs where some member of the
        sample names the batch peak as the owner of its line.
    """
    member = row.members[sample_item_id]
    if (
        member.role in (ROLE_ISO_CHILD, ROLE_REAGENT)
        and member.owner_batch_peak_id == parent.batch_peak_id
    ):
        return ALREADY
    if parent.members.get(sample_item_id) is None:
        return KEPT_PARENT_ABSENT
    if not _reads_parent(parent.members.get(sample_item_id), parent):
        return KEPT_PARENT_READS_OTHERWISE
    if member.role in SOURCE_ROLES:
        return KEPT_CLAIMED
    if (sample_item_id, row.batch_peak_id) in carrying:
        return KEPT_CARRIES_LINES
    return None


def vote_turns(row: Row, decisions: dict[str, Optional[str]]) -> bool:
    """Whether rewriting the members the decisions name makes the parent the
    row's family link (``batch_peaks.resolve_isotopologue_of``): a strict
    majority of the members the consensus reads the row from.

    A rewritten member carries the parent's formula, so it is read among the
    assigned members; the members left as they are keep what they carry.
    """
    follow = sum(1 for d in decisions.values() if d is None or d == ALREADY)
    assigned = follow
    claimed = 0
    for sample_item_id, member in row.members.items():
        if decisions.get(sample_item_id) in (None, ALREADY):
            continue
        if member.formula:
            assigned += 1
        elif member.role in SOURCE_ROLES:
            claimed += 1
    if claimed > assigned:
        return False
    return 2 * follow > assigned


def _rank(pair: Pair) -> tuple:
    """The order two parents' lines reaching one row are ranked in."""
    return (
        pair.reading.closeness,
        -pair.reading.spectra,
        -(pair.parent.anchor.max_intensity or 0.0),
        pair.parent.batch_peak_id,
    )


def _held_reason(
    pair: Pair,
    hand_assigned: set[str],
    carrying_13c: set[str],
    carrying: set[tuple[str, str]],
) -> Optional[str]:
    row = pair.row
    if manual_pin_of(row.anchor) is not None:
        return HELD_CURATED
    if row.batch_peak_id in hand_assigned or any(
        member.source == SOURCE_MANUAL for member in row.members.values()
    ):
        return HELD_HAND_ASSIGNED
    if row.batch_peak_id in carrying_13c:
        return HELD_OWN_13C
    decisions = {
        sample_item_id: sample_decision(row, pair.parent, sample_item_id, carrying)
        for sample_item_id in row.members
    }
    if not vote_turns(row, decisions):
        return HELD_PARENT_ABSENT
    return None


def _read_pair(row: Row, line: Line, window_ppm: float, scatter) -> Pair:
    parent = line.parent
    ratios = []
    for sample_item_id, member in row.members.items():
        own = parent.members.get(sample_item_id)
        if own is None or not own.intensity or member.intensity is None:
            continue
        ratios.append(member.intensity / own.intensity)
    return Pair(
        row=row,
        line=line,
        reading=read_share(ratios, line.isotopologue.ratio),
        distance_ppm=(row.centre - line.mz) / line.mz * 1e6,
        window_ppm=window_ppm,
        scatter_ppm=scatter,
    )


def plan_partition(
    rows: list[Row], hand_assigned: set[str], counts: dict
) -> tuple[list[Pair], list[Pair]]:
    """One partition's stripped and held pairs. ``counts`` is filled in."""
    scatter = mass_scatter_ppm(
        [member.mz for member in row.members.values()] for row in rows
    )
    window = position_window_ppm(scatter)
    # Recorded beside the counts, never summed: what the partition was read at.
    counts["scatter_ppm"] = None if scatter is None else round(scatter, 3)
    counts["window_ppm"] = round(window, 3)
    merged = sorted(
        (row for row in rows if is_parent(row.anchor)),
        key=lambda row: (row.centre, row.batch_peak_id),
    )
    lines = sorted(
        (line for parent in merged for line in lines_of(parent)),
        key=lambda line: line.mz,
    )
    line_mzs = [line.mz for line in lines]
    carrying_13c = carries_own_13c(rows, window)
    # Only a line names an owner (the fold, the batch search and this
    # reading write one beside an isotopologue's or a source line's role).
    carrying = {
        (member.sample_item_id, member.owner_batch_peak_id)
        for row in rows
        for member in row.members.values()
        if member.owner_batch_peak_id
    }
    stripped_ids: set[str] = set()
    stripped: list[Pair] = []
    held: list[Pair] = []
    for row in merged:
        counts["merged_rows"] += 1
        # The lines within the window of the row's centre. Every line sits
        # half a mass unit or more above its parent, so a row at one is above
        # the parent, and no row is at a line of its own.
        reach = row.centre * window / 1e6
        start = bisect.bisect_left(line_mzs, row.centre - reach)
        stop = bisect.bisect_right(line_mzs, row.centre + reach)
        pairs = []
        for line in lines[start:stop]:
            parent = line.parent
            if parent.batch_peak_id in stripped_ids or not brighter(
                parent.anchor.max_intensity,
                row.anchor.max_intensity,
                line.isotopologue.ratio,
            ):
                continue
            pairs.append(_read_pair(row, line, window, scatter))
        if not pairs:
            continue
        counts["at_a_line"] += 1
        passing = [pair for pair in pairs if pair.reading.is_isotopologue]
        if not passing:
            off = any(pair.reading.verdict == VERDICT_OFF_RATIO for pair in pairs)
            counts["off_ratio" if off else "untestable"] += 1
            continue
        best = min(passing, key=_rank)
        best.reason = _held_reason(best, hand_assigned, carrying_13c, carrying)
        if best.reason is not None:
            counts["held"] += 1
            held.append(best)
            continue
        counts["stripped"] += 1
        stripped.append(best)
        stripped_ids.add(row.batch_peak_id)
    return stripped, held


def plan_batch(
    anchors: Iterable[Any],
    members: dict[str, dict[str, Member]],
    hand_assigned: Iterable[str] = (),
) -> Plan:
    """Every merged row read against the lines of the merged ions below it.

    :param anchors: The batch's anchors.
    :param members: Anchor id -> sample -> its member.
    :param hand_assigned: The anchors a sample of which holds a hand
        assignment (:func:`hand_assigned_anchors`).
    :return: The pairs to strip and the ones held, and the counts.
    """
    hand = set(hand_assigned)
    by_partition: dict[Partition, list[Row]] = defaultdict(list)
    for anchor in sorted(anchors, key=lambda a: a.batch_peak_id):
        own = members.get(anchor.batch_peak_id, {})
        row = Row(anchor, own, trace_centre(anchor.mz, own.values()))
        by_partition[partition_of(anchor)].append(row)
    plan = Plan()
    for partition in sorted(by_partition, key=lambda p: (str(p[0]), str(p[1]))):
        counts = _empty_counts()
        stripped, held = plan_partition(by_partition[partition], hand, counts)
        plan.stripped.extend(stripped)
        plan.held.extend(held)
        plan.counts[partition] = counts
    return plan


# --- the records ----------------------------------------------------------------


def row_record(
    pair: Pair,
    *,
    line_ion_formula: Optional[str],
    displaced_members: dict[str, dict],
    kept: dict[str, int],
    rows_rewritten: int,
    rows_kept: dict[str, int],
    batch_peak_run_id: Optional[str],
) -> dict:
    """The stripped row's record: its parent, the line, the share read, what
    it read before, and what each rewritten member read."""
    row, parent = pair.row.anchor, pair.parent.anchor
    isotopologue = pair.line.isotopologue
    return {
        "batch_peak_id": parent.batch_peak_id,
        "formula": parent.consensus_formula,
        "ion_formula": parent.consensus_ion_formula,
        "ionization_mechanism_id": parent.ionization_mechanism_id,
        "label": isotopologue.label,
        "line_ion_formula": line_ion_formula,
        "ratio": round(isotopologue.ratio, 6),
        "mz": round(pair.line.mz, 6),
        "centre": round(pair.row.centre, 6),
        "distance_ppm": round(pair.distance_ppm, 3),
        "window_ppm": round(pair.window_ppm, 3),
        "scatter_ppm": (
            None if pair.scatter_ppm is None else round(pair.scatter_ppm, 3)
        ),
        **pair.reading.to_json(),
        "displaced": {
            "formula": row.consensus_formula,
            "ion_formula": row.consensus_ion_formula,
            "ionization_mechanism_id": row.ionization_mechanism_id,
            "tier": row.consensus_tier,
            "support_fraction": row.support_fraction,
        },
        "members_rewritten": len(displaced_members),
        "rows_rewritten": rows_rewritten,
        "rows_kept": dict(sorted(rows_kept.items())),
        "samples_kept": dict(sorted(kept.items())),
        "displaced_members": displaced_members,
        "batch_peak_run_id": batch_peak_run_id,
    }


def parent_record(parent: Any, entries: list[dict]) -> dict:
    """The parent's record: the reading its lines were read against, and each
    line it took."""
    return {
        "formula": parent.consensus_formula,
        "ion_formula": parent.consensus_ion_formula,
        "ionization_mechanism_id": parent.ionization_mechanism_id,
        "lines": entries,
        "n_lines": len(entries),
    }


def parent_entry(pair: Pair, record: dict) -> dict:
    """One line in the parent's record, from the row's."""
    return {
        "batch_peak_id": pair.row.batch_peak_id,
        "label": record["label"],
        "ratio": record["ratio"],
        "spectra": record["spectra"],
        "median_factor": record["median_factor"],
        "displaced_formula": record["displaced"]["formula"],
        "displaced_ion_formula": record["displaced"]["ion_formula"],
        "members_rewritten": record["members_rewritten"],
        "rows_rewritten": record["rows_rewritten"],
        "batch_peak_run_id": record["batch_peak_run_id"],
    }


# --- the run --------------------------------------------------------------------


def _member_from(row: Any, anchor_mz: float, registry: Optional[list]) -> Member:
    identity = resolve_candidate(registry, row.candidate)
    return Member(
        sample_item_id=row.sample_item_id,
        sample_peak_id=str(row.sample_peak_id),
        intensity=row.intensity,
        mz=mz_from_delta(anchor_mz, row.mz_delta_ppm),
        role=role_name(row.role),
        owner_batch_peak_id=row.owner_batch_peak_id,
        formula=identity.get("formula"),
        ionization_mechanism_id=identity.get("ionization_mechanism_id"),
        source=identity.get("source"),
    )


async def _load_batch(
    sample_batch_id: str,
) -> tuple[list[BatchPeak], dict[str, dict[str, Member]], set[str]]:
    """The batch's anchors, every anchor's members, and the anchors a sample
    of which holds a hand assignment."""
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
        rows = (
            await session.execute(
                select(
                    BatchPeakOccurrence.batch_peak_id,
                    BatchPeakOccurrence.sample_item_id,
                    BatchPeakOccurrence.sample_peak_id,
                    BatchPeakOccurrence.intensity,
                    BatchPeakOccurrence.mz_delta_ppm,
                    BatchPeakOccurrence.role,
                    BatchPeakOccurrence.owner_batch_peak_id,
                    BatchPeakOccurrence.candidate,
                )
                .join(
                    BatchPeak,
                    BatchPeak.batch_peak_id == BatchPeakOccurrence.batch_peak_id,
                )
                .where(BatchPeak.sample_batch_id == sample_batch_id)
            )
        ).all()
        by_id = {anchor.batch_peak_id: anchor for anchor in anchors}
        members: dict[str, dict[str, Member]] = defaultdict(dict)
        for row in rows:
            anchor = by_id[row.batch_peak_id]
            members[row.batch_peak_id][row.sample_item_id] = _member_from(
                row, anchor.mz, anchor.candidates
            )
        hand = await hand_assigned_anchors(session, sample_batch_id)
    return list(anchors), dict(members), hand


async def hand_assigned_anchors(
    session, sample_batch_id: str, batch_peak_ids: Optional[Iterable[str]] = None
) -> set[str]:
    """The batch's anchors a sample of which holds a hand assignment: a member
    folded from a row a person committed (``source`` manual)."""
    query = (
        select(BatchPeakOccurrence.batch_peak_id)
        .join(
            PeakAssignment,
            PeakAssignment.peak_assignment_id == BatchPeakOccurrence.peak_assignment_id,
        )
        .join(BatchPeak, BatchPeak.batch_peak_id == BatchPeakOccurrence.batch_peak_id)
        .where(
            BatchPeak.sample_batch_id == sample_batch_id,
            PeakAssignment.source == SOURCE_MANUAL,
        )
        .distinct()
    )
    if batch_peak_ids is not None:
        query = query.where(BatchPeakOccurrence.batch_peak_id.in_(list(batch_peak_ids)))
    return set((await session.execute(query)).scalars().all())


#: The columns of a sample's row the rewrite reads.
_ROW_FIELDS = (
    "peak_assignment_id",
    "sample_peak_id",
    "sample_peak_mz",
    "sample_peak_intensity",
    "role",
    "assigned_formula",
    "ion_formula",
    "ionization_mechanism_id",
    "isotope_label",
    "isotope_formula",
    "source",
    "fit_score",
    "mz_error_ppm",
    "tier",
    "target_compound_id",
    "target_ion_id",
    "alternatives",
    "provenance",
)


def _as_dict(row: Any) -> dict:
    return {key: getattr(row, key) for key in _ROW_FIELDS}


@dataclass
class Written:
    """What one pair's write did: stripped, or why not.

    :ivar outcome: ``stripped``, ``moved`` (the row or the parent read
        otherwise by the write), or a held reason.
    """

    pair: Pair
    outcome: str
    members_rewritten: int = 0
    rows_rewritten: int = 0
    kept: dict[str, int] = field(default_factory=dict)
    rows_kept: dict[str, int] = field(default_factory=dict)


def _still_planned(pair: Pair, anchors: dict[str, Any]) -> bool:
    """Whether the row and the parent still read what the pair was read for."""
    row = anchors.get(pair.row.batch_peak_id)
    parent = anchors.get(pair.parent.batch_peak_id)
    if row is None or parent is None or not is_parent(row) or not is_parent(parent):
        return False
    planned_row, planned_parent = pair.row.anchor, pair.parent.anchor
    return (
        row.consensus_formula == planned_row.consensus_formula
        and row.ionization_mechanism_id == planned_row.ionization_mechanism_id
        and parent.consensus_formula == planned_parent.consensus_formula
        and parent.consensus_ion_formula == planned_parent.consensus_ion_formula
        and parent.ionization_mechanism_id == planned_parent.ionization_mechanism_id
    )


def row_rewrite(own_row: Any, parent_row: Any, in_app_runs: set[str]) -> Optional[str]:
    """Whether a rewritten member's sample row is rewritten beside it: None
    where it is, else why it is left as it is.

    :param own_row: The sample's row the member links, or None.
    :param parent_row: The sample's row the parent's member links, or None.
    :param in_app_runs: The runs among them this server computed.
    """
    if own_row is None:
        return ROW_KEPT_NO_ROW
    if (
        parent_row is None
        or own_row.peak_assignment_run_id != parent_row.peak_assignment_run_id
    ):
        return ROW_KEPT_NO_PARENT_ROW
    if own_row.peak_assignment_run_id not in in_app_runs:
        return ROW_KEPT_IMPORTED
    return None


def _line_ion_formula(
    parent: Any, parent_member: Any, line: BatchLine, parent_mz: float
) -> Optional[str]:
    """The ion formula a rewritten member names where no row of the sample is
    rewritten beside it: the line's, in the convention of the parent member's
    identity, as the rewrite of a row writes it."""
    identity = resolve_candidate(parent.candidates, parent_member.candidate)
    _, ion_formula, _ = isotopologue_names(
        {
            "ion_formula": identity.get("ion_formula"),
            "source": identity.get("source"),
            "sample_peak_mz": parent_mz,
        },
        line.label,
        parent_mz + line.mz_offset,
    )
    return ion_formula


async def _write_pair(
    session,
    pair: Pair,
    anchors: dict[str, Any],
    members: dict[str, dict[str, Any]],
    rows: dict[str, Any],
    in_app_runs: set[str],
    hand: set[str],
    carrying: set[tuple[str, str]],
    batch_peak_run_id: Optional[str],
) -> Written:
    """Strip one row to its parent's line, on the ledger as it stands."""
    if not _still_planned(pair, anchors):
        return Written(pair, "moved")
    row_bp = anchors[pair.row.batch_peak_id]
    parent_bp = anchors[pair.parent.batch_peak_id]
    fresh_row = Row(
        row_bp,
        {
            sample: _member_from(member, row_bp.mz, row_bp.candidates)
            for sample, member in members.get(row_bp.batch_peak_id, {}).items()
        },
        pair.row.centre,
    )
    fresh_parent = Row(
        parent_bp,
        {
            sample: _member_from(member, parent_bp.mz, parent_bp.candidates)
            for sample, member in members.get(parent_bp.batch_peak_id, {}).items()
        },
        pair.parent.centre,
    )
    fresh = Pair(
        fresh_row,
        Line(pair.line.mz, fresh_parent, pair.line.isotopologue),
        pair.reading,
        pair.distance_ppm,
        pair.window_ppm,
        pair.scatter_ppm,
    )
    reason = _held_reason(fresh, hand, set(), carrying)
    if reason is not None:
        return Written(pair, reason)

    label = pair.line.isotopologue.label
    line = BatchLine(
        label=label,
        mz_offset=pair.line.isotopologue.mz_offset,
        share=pair.line.isotopologue.ratio,
        batch_peak_id=row_bp.batch_peak_id,
        parent_batch_peak_id=parent_bp.batch_peak_id,
        reading=pair.reading.to_json(),
        batch_peak_run_id=batch_peak_run_id,
    )
    registry = list(row_bp.candidates or [])
    displaced: dict[str, dict] = {}
    kept: Counter = Counter()
    rows_kept: Counter = Counter()
    rows_rewritten = 0
    line_ion_formula = None
    for sample_item_id in sorted(fresh_row.members):
        decision = sample_decision(fresh_row, fresh_parent, sample_item_id, carrying)
        if decision == ALREADY:
            continue
        if decision is not None:
            kept[decision] += 1
            continue
        member = members[row_bp.batch_peak_id][sample_item_id]
        parent_member = members[parent_bp.batch_peak_id][sample_item_id]
        parent_mz = fresh_parent.members[sample_item_id].mz
        own_row = rows.get(member.peak_assignment_id or "")
        parent_row = rows.get(parent_member.peak_assignment_id or "")
        kept_row = row_rewrite(own_row, parent_row, in_app_runs)
        if kept_row is None:
            values = read_as_batch_isotopologue(
                _as_dict(own_row), _as_dict(parent_row), line
            )
            parent_row.provenance = with_batch_isotopologue_line(
                _as_dict(parent_row), _as_dict(own_row), line
            )
            for key, value in values.items():
                setattr(own_row, key, value)
            ion_formula = values["ion_formula"]
            rows_rewritten += 1
        else:
            rows_kept[kept_row] += 1
            ion_formula = _line_ion_formula(parent_bp, parent_member, line, parent_mz)
        line_ion_formula = line_ion_formula or ion_formula
        parent_identity = resolve_candidate(
            parent_bp.candidates, parent_member.candidate
        )
        index = candidate_index(
            registry,
            parent_bp.consensus_formula,
            ion_formula,
            parent_bp.ionization_mechanism_id,
            source=parent_identity.get("source"),
        )
        displaced[sample_item_id] = member_state(member)
        member.role = role_code(ROLE_ISO_CHILD)
        member.owner_batch_peak_id = parent_bp.batch_peak_id
        member.candidate = index
        member.tier = parent_member.tier
        member.fit_score = parent_member.fit_score
        member.p_correct = None
    if registry != (row_bp.candidates or []):
        row_bp.candidates = registry

    record = row_record(
        pair,
        line_ion_formula=line_ion_formula,
        displaced_members=displaced,
        kept=dict(kept),
        rows_rewritten=rows_rewritten,
        rows_kept=dict(rows_kept),
        batch_peak_run_id=batch_peak_run_id,
    )
    row_provenance = dict(row_bp.provenance or {})
    row_provenance[MERGED_ISOTOPOLOGUE_OF_KEY] = record
    row_bp.provenance = row_provenance

    standing = standing_merged_isotopologues_record(parent_bp)
    entries = [
        entry
        for entry in (standing or {}).get("lines") or []
        if isinstance(entry, dict)
        and entry.get("batch_peak_id") != row_bp.batch_peak_id
    ]
    entries.append(parent_entry(pair, record))
    parent_provenance = dict(parent_bp.provenance or {})
    parent_provenance[MERGED_ISOTOPOLOGUES_KEY] = parent_record(parent_bp, entries)
    parent_bp.provenance = parent_provenance
    return Written(
        pair,
        "stripped",
        members_rewritten=len(displaced),
        rows_rewritten=rows_rewritten,
        kept=dict(kept),
        rows_kept=dict(rows_kept),
    )


async def _write_chunk(
    sample_batch_id: str, pairs: list[Pair], batch_peak_run_id: Optional[str]
) -> list[Written]:
    """Strip a chunk of pairs in one transaction under the fold lock, and
    recompute the consensus of every row and parent they touched."""
    ids = sorted(
        {pair.row.batch_peak_id for pair in pairs}
        | {pair.parent.batch_peak_id for pair in pairs}
    )
    row_ids = sorted({pair.row.batch_peak_id for pair in pairs})
    async with async_session() as session:
        await _acquire_batch_fold_lock(session, sample_batch_id)
        anchors = {
            anchor.batch_peak_id: anchor
            for anchor in (
                await session.execute(
                    select(BatchPeak).where(BatchPeak.batch_peak_id.in_(ids))
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
                        BatchPeakOccurrence.batch_peak_id.in_(ids)
                    )
                )
            )
            .scalars()
            .all()
        ):
            members[member.batch_peak_id][member.sample_item_id] = member
        # Where a row's member carries lines of its own in a sample: some
        # member of the sample names the row as the owner of its line.
        carrying = {
            (sample_item_id, owner)
            for sample_item_id, owner in (
                await session.execute(
                    select(
                        BatchPeakOccurrence.sample_item_id,
                        BatchPeakOccurrence.owner_batch_peak_id,
                    ).where(BatchPeakOccurrence.owner_batch_peak_id.in_(row_ids))
                )
            ).all()
        }
        hand = await hand_assigned_anchors(session, sample_batch_id, row_ids)
        row_links = [
            member.peak_assignment_id
            for by_sample in members.values()
            for member in by_sample.values()
            if member.peak_assignment_id
        ]
        rows = {}
        for start in range(0, len(row_links), ROW_READ_CHUNK):
            for row in (
                (
                    await session.execute(
                        select(PeakAssignment).where(
                            PeakAssignment.peak_assignment_id.in_(
                                row_links[start : start + ROW_READ_CHUNK]
                            )
                        )
                    )
                )
                .scalars()
                .all()
            ):
                rows[row.peak_assignment_id] = row
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
        written = [
            await _write_pair(
                session,
                pair,
                anchors,
                members,
                rows,
                in_app_runs,
                hand,
                carrying,
                batch_peak_run_id,
            )
            for pair in pairs
        ]
        touched = {
            batch_peak_id
            for done in written
            if done.outcome == "stripped"
            for batch_peak_id in (
                done.pair.row.batch_peak_id,
                done.pair.parent.batch_peak_id,
            )
        }
        if touched:
            await session.flush()
            await _recompute_consensus(session, touched, datetime.now(timezone.utc))
            await touch_ledger(session, sample_batch_id)
        await session.commit()
    return written


def _listed(pairs: Iterable[Pair]) -> list[dict]:
    return [pair.to_json() for pair in list(pairs)[:MAX_LISTED]]


def summarise(plan: Plan, written: list[Written]) -> dict:
    """The run's summary: the counts per partition and over the batch, the
    stripped pairs and the held ones."""
    by_partition: dict[Partition, dict] = {
        partition: dict(counts) for partition, counts in plan.counts.items()
    }
    stripped = [done for done in written if done.outcome == "stripped"]
    late = [done for done in written if done.outcome not in ("stripped", "moved")]
    for done in written:
        if done.outcome == "stripped":
            continue
        counts = by_partition[partition_of(done.pair.row.anchor)]
        counts["stripped"] -= 1
        if done.outcome == "moved":
            counts["moved"] = counts.get("moved", 0) + 1
        else:
            counts["held"] += 1
    for done in late:
        done.pair.reason = done.outcome
    held = plan.held + [done.pair for done in late]
    kept: Counter = Counter()
    rows_kept: Counter = Counter()
    for done in stripped:
        kept.update(done.kept)
        rows_kept.update(done.rows_kept)
    rows = [
        {"ionization_mode_id": mode_id, "instrument_type": instrument_type, **counts}
        for (mode_id, instrument_type), counts in sorted(
            by_partition.items(), key=lambda item: (str(item[0][0]), str(item[0][1]))
        )
    ]
    keys = (*_COUNTS, "moved")
    return {
        "thresholds": thresholds(),
        "partitions": rows,
        **{key: sum(row.get(key, 0) for row in rows) for key in keys},
        "held_by_reason": dict(sorted(Counter(p.reason for p in held).items())),
        "members_rewritten": sum(done.members_rewritten for done in stripped),
        "rows_rewritten": sum(done.rows_rewritten for done in stripped),
        "samples_kept": dict(sorted(kept.items())),
        "rows_kept": dict(sorted(rows_kept.items())),
        "stripped_pairs": _listed(done.pair for done in stripped),
        "held_pairs": _listed(held),
        "pairs_listed_at_most": MAX_LISTED,
    }


async def run_batch_merged_isotopologues(
    sample_batch_id: str, batch_peak_run_id: Optional[str] = None
) -> dict:
    """Strip the batch's merged isotopologue rows and record them.

    :return: The run's summary (:func:`summarise`).
    """
    anchors, members, hand = await _load_batch(sample_batch_id)
    plan = plan_batch(anchors, members, hand)
    written: list[Written] = []
    for start in range(0, len(plan.stripped), WRITE_CHUNK):
        written.extend(
            await _write_chunk(
                sample_batch_id,
                plan.stripped[start : start + WRITE_CHUNK],
                batch_peak_run_id,
            )
        )
    return summarise(plan, written)


def stripping_outcome(summary: dict, sample_batch_id: str) -> dict:
    """The controller result the background-task decorator reports from."""
    notification_data = {"sample_batch_id": sample_batch_id}
    if not summary["merged_rows"]:
        return {
            "status": "partial",
            "message": (
                "No row was read: no batch peak commits a formula as its "
                "monoisotopic line."
            ),
            "data": summary,
            "_notification_data": notification_data,
        }
    stripped = summary["stripped"]
    return {
        "status": "success",
        "message": (
            f"Read {summary['merged_rows']} merged row"
            f"{'s' if summary['merged_rows'] != 1 else ''}: "
            f"{summary['at_a_line']} at another row's isotope line, "
            f"{stripped} stripped to that row's isotopologue "
            f"({summary['members_rewritten']} sample"
            f"{'s' if summary['members_rewritten'] != 1 else ''} rewritten), "
            f"{summary['held']} held, {summary['off_ratio']} off the line's "
            f"share and {summary['untestable']} on too few samples."
        ),
        "data": summary,
        "_notification_data": notification_data,
    }


@api_controller_background_task(
    success_notification_rooms=["sample_batch_id"],
    success_reload=[("peak_assignment", "sample_batch_id")],
    error_notification_rooms=["sample_batch_id"],
)
async def strip_batch_isotopologue_rows(
    sample_batch_id: str,
    independent_transaction: bool = False,
    user_id: int | None = None,
    process_id: str | None = None,
    parent_id: str | None = None,
) -> dict:
    """Strip the batch's merged isotopologue rows, as a batch run. Rewrites the
    members and the samples' in-app rows of each row stripped; emits
    ``peak_assignment_reload`` so the ledgers refresh."""
    run_id = await start_run(
        sample_batch_id,
        ACTION_STRIP_ISOTOPOLOGUES,
        config=thresholds(),
        user_id=user_id,
    )
    try:
        summary = await run_batch_merged_isotopologues(
            sample_batch_id, batch_peak_run_id=run_id
        )
    except Exception as exc:
        await fail_run(sample_batch_id, run_id, f"{type(exc).__name__}: {exc}")
        raise
    await complete_run(sample_batch_id, run_id, summary=summary)
    return stripping_outcome(summary, sample_batch_id)


__all__ = [
    "HELD_CURATED",
    "HELD_HAND_ASSIGNED",
    "HELD_OWN_13C",
    "HELD_PARENT_ABSENT",
    "Member",
    "Pair",
    "Plan",
    "Row",
    "carries_own_13c",
    "hand_assigned_anchors",
    "lines_of",
    "plan_batch",
    "row_rewrite",
    "run_batch_merged_isotopologues",
    "sample_decision",
    "strip_batch_isotopologue_rows",
    "stripping_outcome",
    "summarise",
    "vote_turns",
]
