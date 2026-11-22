"""
Maintenance script to rebuild the stored TOF isotope lines of the target ions
made before the grouping they are computed by last changed.

A target ion's TOF lines (``target_isotope`` rows with ``resolution =
'LOW'``) are computed once, when the ion is made
(``generate_target_ions_from_composition``), and nothing computes them again.
The grouping behind them has changed since (#2587, #2591, MAS-977), so an ion
made before a change keeps the lines it was given then, while an ion made
since has the current ones, and the callers that build an ion's lines on the
fly (peak assignment, the inspector, the TOF calibration audit) read the
current ones too. The classic matcher and a TOF calibration read the stored
rows (#3131).

**What it does.** For each target ion in scope it computes the ion's lines
again from its compound and its mechanism, as a new ion gets them, and
compares the LOW lines with the stored LOW rows by formula, m/z and relative
abundance, each to 6 decimals as ``calibration_isotopes_sha256`` counts them.
An ion whose lines agree is left as it is, its rows' ids and their matches
included. An ion whose lines differ has its LOW rows replaced. An ion the
generator no longer makes, or makes under another ion formula, is left as it
is and counted: rebuilding its lines would not make it a current ion.

The HIGH rows (the fine structure Orbitrap files are matched on) are compared
the same way and counted, and never written: a number for the plan owner, not
part of this rebuild.

**What a new line changes.** ``match_isotope`` rows reference the line they
were made on, ``ON DELETE CASCADE``, so a replaced line takes its matches
along. So, in one transaction per collection: every TOF batch that holds a
collection with a changed ion, or that holds a sample matched on one of its
old lines, is set to ``rematch``; every match of those batches' TOF samples
is removed, as ``reflag_orbitrap_weak_peaks`` and a calibration apply remove
them; and the changed ions' LOW rows are replaced. A batch in ``rematch``
shows that it is behind, and its status icon or **Refresh matches** computes
every match its samples lack: all of them, on the new lines. Orbitrap samples
read the HIGH rows and keep their matches; a batch that is not a TOF batch
keeps its status. Nothing is rematched and nothing is refitted here.

**Calibration.** A TOF fit reads its calibrants' stored LOW rows, and records
the hash of what it read (``calibration_isotopes_sha256``), which the fleet
runner (``recalibrate_tof``) compares to tell a file on target. A changed
calibrant line changes the hash, so every file fitted before the rebuild is
off target again. Run this on an instrument's calibration collections
before its ``recalibrate_tof`` pass, never after one: after it, it forces a
second pass. The report gives, for each calibration collection, the modes
that name it and the hash each mode's fit reads, before and after.

**Scope**, through the environment:

- every target ion (the default), those of no collection included;
- ``REBUILD_TOF_COLLECTIONS=<id>,<id>``: the ions of these target
  collections;
- ``REBUILD_TOF_SCOPE=calibration``: the ions of the calibration
  collections, those an ionization mode names as its
  ``calibration_collection_id``.

An ion in two collections is read with each, and rebuilt with the first; the
second then finds it current, and the batches of both were set to rematch.

``DRY_RUN=1`` reads the same and writes nothing: per collection, the ions
read, the ions whose LOW lines differ and their lines before and after, the
modes and hashes of a calibration collection, the batches that would be set
to rematch and the matches that would go. A second run finds nothing to do.

**Undoing it.** The pre-script ``pg_dump`` holds the old rows, the matches
and the batch statuses.

Usage:
    DRY_RUN=1 mascope prod db script run rebuild_tof_target_isotopes
    mascope prod db script run rebuild_tof_target_isotopes

    DRY_RUN=1 REBUILD_TOF_SCOPE=calibration \\
        mascope prod db script run rebuild_tof_target_isotopes
    DRY_RUN=1 REBUILD_TOF_COLLECTIONS=<id>,<id> \\
        mascope prod db script run rebuild_tof_target_isotopes

Date: 2026-11-22
Issue: #3131
"""

import asyncio
import os
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import delete, distinct, exists, func, select, update

from mascope_backend.api.controllers.calibration.lib.calibration_mz_fit import (
    calibration_isotopes_sha256,
)
from mascope_backend.api.controllers.match.lib.match_write_lock import (
    acquire_match_write_locks,
)
from mascope_backend.api.controllers.target.lib.compute.target_ions_compute import (
    generate_target_ions_from_composition,
)
from mascope_backend.db import (
    IonizationMechanism,
    IonizationMode,
    MatchCollection,
    MatchCompound,
    MatchIon,
    MatchIsotope,
    MatchSample,
    SampleBatch,
    SampleFile,
    SampleItem,
    TargetCollection,
    TargetCollectionInSampleBatch,
    TargetCompound,
    TargetCompoundInTargetCollection,
    TargetIon,
    TargetIsotope,
    async_session,
    configure_database_engine,
)
from mascope_backend.db.id import gen_id
from mascope_backend.runtime import runtime


#: The resolution of the stored TOF lines, the ones this rebuilds.
LOW = "LOW"
#: The fine structure, compared and counted, never written.
HIGH = "HIGH"

#: The instrument class whose samples are matched on the LOW lines.
TOF = "tof"

#: What a batch is set to, and the one pending status stronger than it, which
#: it does not lower (``status.service.PENDING_STATUS_RANK``).
REMATCH = "rematch"
RECALIBRATE = "recalibrate"

#: The scopes ``REBUILD_TOF_SCOPE`` takes.
ALL = "all"
CALIBRATION = "calibration"
COLLECTIONS = "collections"

SCOPE_ENV = "REBUILD_TOF_SCOPE"
COLLECTIONS_ENV = "REBUILD_TOF_COLLECTIONS"

#: The digits a line's m/z and abundance count to, as
#: ``calibration_isotopes_sha256`` counts them.
DIGITS = 6

#: Ids per ``IN`` list: Postgres takes at most 32767 parameters a statement.
_CHUNK = 5000

#: The id standing for the target ions of no collection, which the default
#: scope reads too.
NO_COLLECTION = None


class BadScope(ValueError):
    """The scope variables ask for something that is not a scope."""


@dataclass(frozen=True)
class Scope:
    """Which target ions a run reads."""

    kind: str
    #: The collections of a ``collections`` scope, as given.
    collection_ids: tuple[str, ...] = ()


def read_scope(environ: Mapping[str, str] = os.environ) -> Scope:
    """The scope from the environment.

    :raises BadScope: For a scope it does not know, for ``collections`` with
        no collection named, and for collections named in another scope.
    """
    kind = (environ.get(SCOPE_ENV) or "").strip().lower()
    names = tuple(
        part.strip()
        for part in (environ.get(COLLECTIONS_ENV) or "").split(",")
        if part.strip()
    )
    if names:
        if kind not in ("", COLLECTIONS):
            raise BadScope(
                f"{COLLECTIONS_ENV} names collections, but {SCOPE_ENV}={kind!r} "
                f"asks for another scope. Set one of them."
            )
        return Scope(kind=COLLECTIONS, collection_ids=tuple(dict.fromkeys(names)))
    if kind == COLLECTIONS:
        raise BadScope(f"{SCOPE_ENV}=collections needs {COLLECTIONS_ENV}=<id>,<id>.")
    if kind in ("", ALL):
        return Scope(kind=ALL)
    if kind == CALIBRATION:
        return Scope(kind=CALIBRATION)
    raise BadScope(
        f"{SCOPE_ENV}={kind!r} is not a scope: leave it unset for every ion, "
        f"or set it to 'calibration', or name collections in {COLLECTIONS_ENV}."
    )


#: A line as it is stored: formula, m/z, relative abundance.
Row = tuple[str, float, float]
#: A line as it is compared: the same, to :data:`DIGITS` decimals.
Line = tuple[str, float, float]


def line_of(row: Row) -> Line:
    """A line as it is compared, and as the calibration hash counts it."""
    formula, mz, abundance = row
    return (str(formula), round(float(mz), DIGITS), round(float(abundance), DIGITS))


def lines_of(rows: Iterable[Row]) -> list[Line]:
    """Lines as they are compared: rounded, in order."""
    return sorted(line_of(row) for row in rows)


def isotopes_sha256(rows: Iterable[Row]) -> str:
    """``calibration_isotopes_sha256`` of the rows, as a fit offered them would
    hash."""
    rows = list(rows)
    return calibration_isotopes_sha256(
        pd.DataFrame(
            {
                "target_isotope_formula": [row[0] for row in rows],
                "mz": [row[1] for row in rows],
                "relative_abundance": [row[2] for row in rows],
            }
        )
    )


@dataclass(frozen=True)
class Fresh:
    """What a new ion of a compound and a mechanism gets, or None for an
    ion the generator no longer makes."""

    ion_formula: str
    low: tuple[Row, ...]
    high: tuple[Row, ...]


def compute_fresh(
    compound: TargetCompound, mechanism: IonizationMechanism
) -> Fresh | None:
    """The lines a new ion of the compound under the mechanism is stored with.

    The generator the two writers of the rows call
    (``target_ions_controller.create_target_ions``,
    ``ionization_mechanisms_controller``), with no file: the stored library's
    two sets.
    """
    ions, isotopes = generate_target_ions_from_composition(compound, [mechanism])
    if len(ions) != 1:
        return None
    by_resolution = defaultdict(list)
    for isotope in isotopes:
        by_resolution[isotope.resolution].append(
            (
                isotope.target_isotope_formula,
                float(isotope.mz),
                float(isotope.relative_abundance),
            )
        )
    return Fresh(
        ion_formula=ions[0].target_ion_formula,
        low=tuple(by_resolution[LOW]),
        high=tuple(by_resolution[HIGH]),
    )


@dataclass
class IonRead:
    """One stored ion, its rows, and what a new ion of it gets."""

    target_ion_id: str
    target_ion_formula: str
    target_compound_id: str
    ionization_mechanism_id: str
    #: Its stored LOW rows: ids, and the lines.
    low_ids: list[str]
    low: list[Row]
    high: list[Row]
    fresh: Fresh | None

    @property
    def computable(self) -> bool:
        return (
            self.fresh is not None and self.fresh.ion_formula == self.target_ion_formula
        )

    @property
    def low_differs(self) -> bool:
        return self.computable and lines_of(self.low) != lines_of(self.fresh.low)

    @property
    def high_differs(self) -> bool:
        return self.computable and lines_of(self.high) != lines_of(self.fresh.high)

    def low_after(self) -> list[Row]:
        """The LOW rows the ion holds after a run."""
        return list(self.fresh.low) if self.low_differs else list(self.low)


@dataclass
class ModeHash:
    """A mode that names a calibration collection, and the hash of what its
    fit reads of it before and after."""

    ionization_mode_id: str
    name: str
    version: int
    is_active: bool
    before: str
    after: str


@dataclass
class CollectionPlan:
    """What a run does to one collection's ions."""

    collection_id: str | None
    name: str
    ions: list[IonRead] = field(default_factory=list)
    modes: list[ModeHash] = field(default_factory=list)
    #: The TOF batches to set to rematch, and their TOF samples, whose
    #: matches go.
    batches: list[str] = field(default_factory=list)
    samples: list[str] = field(default_factory=list)
    #: One TOF sample of each batch, which the batch's match-write lock is
    #: taken through.
    lock_samples: list[str] = field(default_factory=list)
    #: The match isotopes and match ions each sample holds, by sample: a
    #: sample that two collections reach counts once over a run (``Totals``).
    match_isotopes_by_sample: dict[str, int] = field(default_factory=dict)
    match_ions_by_sample: dict[str, int] = field(default_factory=dict)

    @property
    def match_isotopes(self) -> int:
        return sum(self.match_isotopes_by_sample.values())

    @property
    def match_ions(self) -> int:
        return sum(self.match_ions_by_sample.values())

    @property
    def changed(self) -> list[IonRead]:
        return [ion for ion in self.ions if ion.low_differs]

    @property
    def high_differ(self) -> int:
        return sum(1 for ion in self.ions if ion.high_differs)

    @property
    def not_made(self) -> list[IonRead]:
        """Ions the generator makes no more, or makes under another formula."""
        return [ion for ion in self.ions if not ion.computable]


def _chunks(ids: Iterable[str]) -> Iterable[list[str]]:
    ids = list(ids)
    for start in range(0, len(ids), _CHUNK):
        yield ids[start : start + _CHUNK]


async def _collections(session, scope: Scope) -> list[tuple[str | None, str]]:
    """The collections in scope, ``(id, name)``, by name; the default scope
    ends with the ions of no collection."""
    query = select(
        TargetCollection.target_collection_id, TargetCollection.target_collection_name
    )
    if scope.kind == COLLECTIONS:
        query = query.where(
            TargetCollection.target_collection_id.in_(scope.collection_ids)
        )
    elif scope.kind == CALIBRATION:
        query = query.where(
            TargetCollection.target_collection_id.in_(
                select(IonizationMode.calibration_collection_id).where(
                    IonizationMode.calibration_collection_id.is_not(None)
                )
            )
        )
    found = [(row[0], row[1] or "") for row in (await session.execute(query)).all()]
    if scope.kind == COLLECTIONS:
        missing = sorted(set(scope.collection_ids) - {cid for cid, _ in found})
        if missing:
            raise BadScope(
                f"{COLLECTIONS_ENV} names no collection with the id(s) "
                f"{', '.join(missing)}."
            )
    found.sort(key=lambda item: (item[1], item[0]))
    if scope.kind == ALL:
        found.append((NO_COLLECTION, "(target ions of no collection)"))
    return found


def _in_collection(collection_id: str | None):
    """The condition on ``TargetIon`` that it is of a compound of the
    collection, or of none."""
    if collection_id is NO_COLLECTION:
        return ~exists().where(
            TargetCompoundInTargetCollection.target_compound_id
            == TargetIon.target_compound_id
        )
    return TargetIon.target_compound_id.in_(
        select(TargetCompoundInTargetCollection.target_compound_id).where(
            TargetCompoundInTargetCollection.target_collection_id == collection_id
        )
    )


async def read_ions(
    session,
    collection_id: str | None,
    fresh_cache: dict | None = None,
) -> list[IonRead]:
    """The collection's target ions, their stored rows, and what a new ion
    of each gets."""
    fresh_cache = {} if fresh_cache is None else fresh_cache
    rows = (
        await session.execute(
            select(TargetIon, TargetCompound, IonizationMechanism)
            .join(
                TargetCompound,
                TargetCompound.target_compound_id == TargetIon.target_compound_id,
            )
            .join(
                IonizationMechanism,
                IonizationMechanism.ionization_mechanism_id
                == TargetIon.ionization_mechanism_id,
            )
            .where(_in_collection(collection_id))
            .order_by(TargetIon.target_ion_formula, TargetIon.target_ion_id)
        )
    ).all()
    stored = defaultdict(lambda: {LOW: [], HIGH: [], "low_ids": []})
    isotopes = (
        await session.execute(
            select(
                TargetIsotope.target_ion_id,
                TargetIsotope.target_isotope_id,
                TargetIsotope.resolution,
                TargetIsotope.target_isotope_formula,
                TargetIsotope.mz,
                TargetIsotope.relative_abundance,
            )
            .join(TargetIon, TargetIon.target_ion_id == TargetIsotope.target_ion_id)
            .where(_in_collection(collection_id))
        )
    ).all()
    for ion_id, isotope_id, resolution, formula, mz, abundance in isotopes:
        if resolution not in (LOW, HIGH):
            continue
        stored[ion_id][resolution].append((formula, float(mz), float(abundance)))
        if resolution == LOW:
            stored[ion_id]["low_ids"].append(isotope_id)

    ions = []
    for ion, compound, mechanism in rows:
        key = (compound.target_compound_formula, mechanism.ionization_mechanism_id)
        if key not in fresh_cache:
            try:
                fresh_cache[key] = compute_fresh(compound, mechanism)
            except Exception as exc:  # noqa: BLE001 - a row older rules let in
                runtime.logger.info(
                    f"  Cannot compute {ion.target_ion_formula} "
                    f"({compound.target_compound_formula}, "
                    f"{mechanism.ionization_mechanism}): {exc}"
                )
                fresh_cache[key] = None
        held = stored[ion.target_ion_id]
        ions.append(
            IonRead(
                target_ion_id=ion.target_ion_id,
                target_ion_formula=ion.target_ion_formula,
                target_compound_id=ion.target_compound_id,
                ionization_mechanism_id=ion.ionization_mechanism_id,
                low_ids=held["low_ids"],
                low=held[LOW],
                high=held[HIGH],
                fresh=fresh_cache[key],
            )
        )
    return ions


async def mode_hashes(
    session, collection_id: str | None, ions: list[IonRead]
) -> list[ModeHash]:
    """For each mode that names the collection as its calibration collection,
    the hash of the LOW lines its fit reads, before and after.

    A fit reads the LOW rows of the collection's ions under the mode's
    mechanisms (``calibration_mz_fit._resolve_calibration_isotopes``).
    """
    if collection_id is NO_COLLECTION:
        return []
    modes = (
        (
            await session.execute(
                select(IonizationMode)
                .where(IonizationMode.calibration_collection_id == collection_id)
                .order_by(IonizationMode.ionization_mode_name, IonizationMode.version)
            )
        )
        .scalars()
        .all()
    )
    hashes = []
    for mode in modes:
        mechanisms = set(mode.ionization_mechanism_ids or [])
        read = [ion for ion in ions if ion.ionization_mechanism_id in mechanisms]
        hashes.append(
            ModeHash(
                ionization_mode_id=mode.ionization_mode_id,
                name=mode.ionization_mode_name,
                version=mode.version,
                is_active=bool(mode.is_active),
                before=isotopes_sha256(row for ion in read for row in ion.low),
                after=isotopes_sha256(row for ion in read for row in ion.low_after()),
            )
        )
    return hashes


@dataclass
class Reach:
    """The TOF batches a collection's changed ions reach, and their TOF
    samples."""

    batches: list[str] = field(default_factory=list)
    samples: list[str] = field(default_factory=list)
    #: One sample of each batch.
    lock_samples: list[str] = field(default_factory=list)
    match_isotopes_by_sample: dict[str, int] = field(default_factory=dict)
    match_ions_by_sample: dict[str, int] = field(default_factory=dict)


async def _count_by_sample(session, table, sample_ids: list[str]) -> dict[str, int]:
    """The rows of a match table each sample holds; a sample with none is
    left out."""
    counts: dict[str, int] = {}
    for chunk in _chunks(sample_ids):
        counts.update(
            (
                await session.execute(
                    select(table.sample_item_id, func.count())
                    .where(table.sample_item_id.in_(chunk))
                    .group_by(table.sample_item_id)
                )
            )
            .tuples()
            .all()
        )
    return counts


async def affected(session, changed: list[IonRead]) -> Reach:
    """The TOF batches the changed ions reach, their TOF samples, and how
    many match isotopes and match ions each of those samples holds.

    A batch is reached when it holds a collection that holds a changed ion's
    compound, or a sample matched on one of a changed ion's LOW rows.
    """
    if not changed:
        return Reach()
    compound_ids = sorted({ion.target_compound_id for ion in changed})
    old_ids = sorted({isotope_id for ion in changed for isotope_id in ion.low_ids})
    batch_ids: set[str] = set()
    for chunk in _chunks(compound_ids):
        batch_ids.update(
            (
                await session.execute(
                    select(distinct(TargetCollectionInSampleBatch.sample_batch_id))
                    .join(
                        TargetCompoundInTargetCollection,
                        TargetCompoundInTargetCollection.target_collection_id
                        == TargetCollectionInSampleBatch.target_collection_id,
                    )
                    .where(
                        TargetCompoundInTargetCollection.target_compound_id.in_(chunk)
                    )
                )
            )
            .scalars()
            .all()
        )
    for chunk in _chunks(old_ids):
        batch_ids.update(
            (
                await session.execute(
                    select(distinct(SampleItem.sample_batch_id))
                    .join(
                        MatchIsotope,
                        MatchIsotope.sample_item_id == SampleItem.sample_item_id,
                    )
                    .where(MatchIsotope.target_isotope_id.in_(chunk))
                )
            )
            .scalars()
            .all()
        )

    samples: set[str] = set()
    #: Each batch, and one of its TOF samples.
    batches: dict[str, str] = {}
    for chunk in _chunks(sorted(batch_ids)):
        for sample_item_id, sample_batch_id in (
            await session.execute(
                select(SampleItem.sample_item_id, SampleItem.sample_batch_id)
                .order_by(SampleItem.sample_item_id)
                .join(
                    SampleFile, SampleFile.sample_file_id == SampleItem.sample_file_id
                )
                .where(
                    SampleItem.sample_batch_id.in_(chunk),
                    SampleFile.instrument_type == TOF,
                )
            )
        ).all():
            samples.add(sample_item_id)
            batches.setdefault(sample_batch_id, sample_item_id)

    samples_sorted = sorted(samples)
    return Reach(
        batches=sorted(batches),
        samples=samples_sorted,
        lock_samples=sorted(batches.values()),
        match_isotopes_by_sample=await _count_by_sample(
            session, MatchIsotope, samples_sorted
        ),
        match_ions_by_sample=await _count_by_sample(session, MatchIon, samples_sorted),
    )


async def plan_collection(
    session, collection_id: str | None, name: str, fresh_cache: dict | None = None
) -> CollectionPlan:
    """What a run does to one collection, read in the session."""
    plan = CollectionPlan(collection_id=collection_id, name=name)
    plan.ions = await read_ions(session, collection_id, fresh_cache)
    plan.modes = await mode_hashes(session, collection_id, plan.ions)
    reach = await affected(session, plan.changed)
    plan.batches = reach.batches
    plan.samples = reach.samples
    plan.lock_samples = reach.lock_samples
    plan.match_isotopes_by_sample = reach.match_isotopes_by_sample
    plan.match_ions_by_sample = reach.match_ions_by_sample
    return plan


async def write_plan(session, plan: CollectionPlan) -> None:
    """Set the batches to rematch, remove their TOF samples' matches, and
    replace the changed ions' LOW rows, in the session's transaction."""
    changed = plan.changed
    if not changed:
        return
    now = datetime.now(timezone.utc)
    # The batches' match-write locks first, as every match writer takes them,
    # so a match being written to these samples lands before or after. One
    # sample per batch: the helper reads the batches back in one ``IN``, which
    # every TOF sample of a server could overfill.
    await acquire_match_write_locks(session, plan.lock_samples)
    for chunk in _chunks(plan.batches):
        await session.execute(
            update(SampleBatch)
            .where(
                SampleBatch.sample_batch_id.in_(chunk),
                SampleBatch.status.not_in([REMATCH, RECALIBRATE]),
            )
            .values(status=REMATCH, sample_batch_utc_modified=now)
        )
    # Every level, as a full removal of a sample's matches does
    # (``match_remove.remove_matches``)
    for chunk in _chunks(plan.samples):
        for table in (
            MatchIsotope,
            MatchIon,
            MatchCompound,
            MatchCollection,
            MatchSample,
        ):
            await session.execute(delete(table).where(table.sample_item_id.in_(chunk)))
        await session.execute(
            update(SampleItem)
            .where(SampleItem.sample_item_id.in_(chunk))
            .values(sample_item_utc_modified=now)
        )
    old_ids = [isotope_id for ion in changed for isotope_id in ion.low_ids]
    for chunk in _chunks(old_ids):
        await session.execute(
            delete(TargetIsotope).where(TargetIsotope.target_isotope_id.in_(chunk))
        )
    session.add_all(
        TargetIsotope(
            target_isotope_id=gen_id(16),
            target_ion_id=ion.target_ion_id,
            mz=mz,
            relative_abundance=abundance,
            resolution=LOW,
            target_isotope_formula=formula,
        )
        for ion in changed
        for formula, mz, abundance in ion.fresh.low
    )
    await session.flush()


def _format_lines(rows: Iterable[Row]) -> str:
    return ", ".join(
        f"{mz:.{DIGITS}f} {abundance:.{DIGITS}f} {formula}"
        for formula, mz, abundance in lines_of(rows)
    )


def report(plan: CollectionPlan, dry_run: bool) -> None:
    """Log what was, or would be, done to one collection."""
    log = runtime.logger.info
    changed = plan.changed
    label = (
        plan.name
        if plan.collection_id is None
        else f"{plan.name} ({plan.collection_id})"
    )
    log(
        f"{label}: {len(plan.ions)} ion(s) read, LOW lines differ on "
        f"{len(changed)}, HIGH lines differ on {plan.high_differ} (not written)"
        + (
            f", {len(plan.not_made)} not made by the generator as stored (left as they are)"
            if plan.not_made
            else ""
        )
    )
    for ion in changed:
        log(
            f"  {ion.target_ion_formula} ({ion.target_ion_id}): "
            f"{len(ion.low)} line(s) -> {len(ion.fresh.low)}"
        )
        log(f"    before: {_format_lines(ion.low)}")
        log(f"    after:  {_format_lines(ion.fresh.low)}")
    for ion in plan.not_made:
        made = "makes no ion" if ion.fresh is None else f"makes {ion.fresh.ion_formula}"
        log(f"  {ion.target_ion_formula} ({ion.target_ion_id}): the generator {made}")
    for mode in plan.modes:
        state = "" if mode.is_active else ", inactive"
        moved = "unchanged" if mode.before == mode.after else "changes"
        log(
            f"  Calibration collection of mode {mode.name} (v{mode.version}{state}, "
            f"{mode.ionization_mode_id}): isotope hash {moved}, "
            f"before {mode.before}, after {mode.after}"
        )
    if changed:
        verb = "to set" if dry_run else "set"
        log(
            f"  Batches {verb} to rematch: {len(plan.batches)}"
            + (f" ({', '.join(plan.batches)})" if plan.batches else "")
        )
        log(
            f"  Matches {'to remove' if dry_run else 'removed'}: "
            f"{plan.match_isotopes} match isotope(s) and {plan.match_ions} match "
            f"ion(s) of {len(plan.samples)} TOF sample(s)"
        )


@dataclass
class Totals:
    """What a run read and did, over every collection."""

    plans: list = field(default_factory=list)
    collections: int = 0
    ions: int = 0
    changed: set = field(default_factory=set)
    high_differ: set = field(default_factory=set)
    not_made: set = field(default_factory=set)
    batches: set = field(default_factory=set)
    samples: set = field(default_factory=set)
    #: Each sample's matches as the first collection that reached it counted
    #: them. A run's later collections find them gone, and a dry run's find
    #: them again, so this is what a run removes in both.
    match_isotopes_by_sample: dict = field(default_factory=dict)
    match_ions_by_sample: dict = field(default_factory=dict)
    hashes_changed: int = 0

    @property
    def match_isotopes(self) -> int:
        return sum(self.match_isotopes_by_sample.values())

    @property
    def match_ions(self) -> int:
        return sum(self.match_ions_by_sample.values())

    def add(self, plan: CollectionPlan) -> None:
        self.plans.append(plan)
        self.collections += 1
        self.ions += len(plan.ions)
        self.changed.update(ion.target_ion_id for ion in plan.changed)
        self.high_differ.update(
            ion.target_ion_id for ion in plan.ions if ion.high_differs
        )
        self.not_made.update(ion.target_ion_id for ion in plan.not_made)
        self.batches.update(plan.batches)
        self.samples.update(plan.samples)
        for into, counts in (
            (self.match_isotopes_by_sample, plan.match_isotopes_by_sample),
            (self.match_ions_by_sample, plan.match_ions_by_sample),
        ):
            for sample_id, count in counts.items():
                into.setdefault(sample_id, count)
        self.hashes_changed += sum(
            1 for mode in plan.modes if mode.before != mode.after
        )


async def rebuild(scope: Scope, dry_run: bool) -> Totals:
    """Read the ions in scope, collection by collection, and rebuild the
    changed ones' LOW rows unless ``dry_run``.

    Each collection is planned and written in one transaction of its own:
    a run that stops leaves each collection either done or as it was.
    """
    totals = Totals()
    fresh_cache: dict = {}
    async with async_session() as session:
        collections = await _collections(session, scope)
    for collection_id, name in collections:
        async with async_session() as session:
            plan = await plan_collection(session, collection_id, name, fresh_cache)
            if not dry_run and plan.changed:
                await write_plan(session, plan)
                await session.commit()
            else:
                await session.rollback()
        report(plan, dry_run)
        totals.add(plan)
    return totals


async def run() -> Totals:
    """Entry point body: read the scope, rebuild, and log the totals."""
    dry_run = os.environ.get("DRY_RUN") == "1"
    scope = read_scope()
    await configure_database_engine()
    runtime.logger.info(
        f"Rebuilding the stored TOF isotope lines, scope {scope.kind}"
        + (f" ({', '.join(scope.collection_ids)})" if scope.collection_ids else "")
        + (" (DRY_RUN=1: nothing is written)" if dry_run else "")
    )
    totals = await rebuild(scope, dry_run)

    log = runtime.logger.info
    log("=" * 80)
    log("REBUILD TOF TARGET ISOTOPES COMPLETE" + (" (DRY RUN)" if dry_run else ""))
    verb = "would be rebuilt" if dry_run else "rebuilt"
    log(
        f"Collections read {totals.collections}, ions read {totals.ions}: "
        f"{len(totals.changed)} ion(s) {verb}, {len(totals.not_made)} not made by "
        f"the generator as stored (left as they are)"
    )
    log(
        f"Ions whose stored HIGH lines differ from a fresh computation: "
        f"{len(totals.high_differ)} (not written)"
    )
    log(f"Calibration mode hashes that change: {totals.hashes_changed}")
    log(
        f"Batches {'to set' if dry_run else 'set'} to rematch: {len(totals.batches)}; "
        f"matches {'to remove' if dry_run else 'removed'}: {totals.match_isotopes} "
        f"match isotope(s), {totals.match_ions} match ion(s) of "
        f"{len(totals.samples)} TOF sample(s)"
    )
    if totals.batches and not dry_run:
        log(
            "  Rematch them (their status icon, or Refresh matches) to match "
            "their samples on the new lines."
        )
    if totals.hashes_changed:
        log(
            "  A changed hash puts every file fitted from that collection off "
            "target: run recalibrate_tof for the instrument after this, not before."
        )
    if not totals.changed:
        log("Nothing to do.")
    log("=" * 80)
    return totals


def main() -> None:
    """Entry point for ``mascope dev|prod db script run``."""
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        runtime.logger.info("Cancelled by user (Ctrl+C)")
    except Exception as e:
        runtime.logger.exception(f"Script failed: {e}")
        raise


if __name__ == "__main__":
    main()
