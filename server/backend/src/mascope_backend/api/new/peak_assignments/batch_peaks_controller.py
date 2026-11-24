"""DB controller: fold a sample's per-sample assignments into the batch peaks.

Wraps the pure :mod:`batch_peaks` engine with the database I/O behind the batch
overview. :func:`fold_sample_into_batch_peaks` runs on the arrival path right
after Stage-A assignment (the assignments are already committed by then), snapping
the sample's peaks into the batch's frozen, append-only anchors and recomputing the
consensus of every touched batch peak. :func:`backfill_sample_batch_peaks` folds a
whole batch's existing runs in time order, reporting progress per sample, and
recomputes the anchors those folds touched once each at the end
(:func:`recompute_batch_consensus`) rather than once per sample.

A member row carries everything the consensus reads - formula, tier, fit,
intensity, P(correct), role, the owner anchor of an isotopologue, and an index
into its anchor's candidate registry for the ion formula and mechanism - so the
batch ledger stands without the per-sample ledger rows it was folded from: a
run pruned or deleted afterwards costs the anchors nothing.

Design: ``docs/dev/peak_assignment_batch_primary.md`` (section 12 for the fold, section 4
for the member fields).
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from sqlalchemy import select, update

from mascope_backend.api.new.instrument_configs.lib import read_instrument_functions
from mascope_backend.api.new.ionization.modes.versions import root_ids
from mascope_backend.api.new.peak_assignments import batch_peaks
from mascope_backend.api.new.peak_assignments.batch_blend import BLEND_TIERS
from mascope_backend.api.new.peak_assignments.batch_lock import _acquire_batch_fold_lock
from mascope_backend.api.new.peak_assignments.batch_peaks import (
    DIAGNOSTIC_ISOTOPOLOGUES_KEY,
    HELD_MEMBERS_KEY,
    MERGED_ISOTOPOLOGUE_OF_KEY,
    MERGED_ISOTOPOLOGUES_KEY,
    PERSISTENCE_KEY,
    ROLE_ISO_CHILD,
    ROLE_M0,
    ROLE_REAGENT,
    SOURCE_ROLES,
    STAMPED_ISOTOPOLOGUE_OF_KEY,
    Anchor,
    AnchorSet,
    Consensus,
    PersistenceFacts,
    _intensity_variable,
    batch_checks_record_of,
    candidate_index,
    co_variation_record_of,
    compute_consensus,
    diagnostic_isotopologues_record_of,
    flat_series_record_of,
    fold_in_sample,
    held_member_tiers,
    hold_persistence_only,
    is_stale,
    kept_record,
    known_species_record_of,
    manual_pin_of,
    measured_members,
    merged_isotopologue_of_record,
    merged_isotopologues_record,
    mz_delta_ppm,
    on_the_reading,
    persistence_asks,
    persistence_record_of,
    resolution_adaptive_tol_ppm,
    resolve_candidate,
    role_code,
    role_name,
    series_record_of,
    stamped_isotopologue_of_record,
    stamped_lines,
    tier_code,
    tier_name,
    water_ladder_record_of,
    went_stale,
    without_merged_lines,
)
from mascope_backend.api.new.peak_assignments.batch_runs import (
    ensure_current_run,
    touch_ledger,
)
from mascope_backend.api.new.peak_assignments.batch_search_records import (
    SEARCHED_EMPTY_KEY,
    searched_empty_record_of,
)
from mascope_backend.api.new.peak_assignments.batch_staleness import (
    REASON_CONSENSUS,
    REASON_FOLD,
    STAGE_OF_KEY,
    mark_every_stage_stale,
    mark_stale,
)
from mascope_backend.api.new.peak_assignments.continuity import run_batch_continuity
from mascope_backend.api.new.peak_assignments.listing import reference_listing
from mascope_backend.api.new.peak_assignments.monoisotopic import (
    is_monoisotopic_formula,
    labelled_isotopes,
)
from mascope_backend.api.new.peak_assignments.tiers import TIER_UNASSIGNED
from mascope_backend.db import (
    BatchPeak,
    BatchPeakOccurrence,
    PeakAssignment,
    PeakAssignmentRun,
    Sample,
    SampleBatch,
    TargetCollectionInSampleBatch,
    TargetCompoundInTargetCollection,
    TargetIon,
    TargetIsotope,
    async_session,
)
from mascope_backend.db.id import gen_id
from mascope_backend.runtime import runtime
from mascope_tools.composition.utils import formula_identity


async def _latest_completed_run_id(session, sample_item_id: str) -> str | None:
    """The run a sample folds from: its latest completed one that is no decoy.

    A decoy run is the sample read with its chemistry made wrong on purpose
    (``PeakAssignmentConfig.decoy``); folded, it would put that reading on the
    batch ledger and into every consensus the sample's members vote in.
    """
    return (
        await session.execute(
            select(PeakAssignmentRun.peak_assignment_run_id)
            .where(
                PeakAssignmentRun.sample_item_id == sample_item_id,
                PeakAssignmentRun.status == "completed",
                PeakAssignmentRun.decoy.is_(None),
            )
            .order_by(PeakAssignmentRun.peak_assignment_run_utc_created.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


def _tolerance_fn(resolution_func):
    """Build a resolution-adaptive ``mz -> tol_ppm`` from a per-file resolution
    function (half-FWHM + drift margin); falls back to the margin alone."""

    def tol_fn(mz: float) -> float:
        resolution = None
        if resolution_func is not None:
            try:
                resolution = float(resolution_func(mz))
            except Exception:
                resolution = None
        return resolution_adaptive_tol_ppm(mz, resolution)

    return tol_fn


def _monoisotopic_mz(isotopes: list) -> float:
    """The m/z of an ion's monoisotopic line among its isotope rows: the row
    the engine counts every role and label from (``engine.monoisotopic_row``),
    the lightest where no formula tells the lines apart."""
    ordered = sorted(isotopes, key=lambda row: row.mz)
    labels = labelled_isotopes(ordered[0].target_ion_formula)
    for row in ordered:
        if is_monoisotopic_formula(row.target_isotope_formula, labels):
            return float(row.mz)
    return float(ordered[0].mz)


async def target_ion_seed_mzs(
    session,
    *,
    sample_batch_id: str,
    target_ion_ids: Iterable[str],
    resolution: str,
) -> dict[str, float]:
    """The m/z an anchor is seeded at for each of the given target ions: its
    monoisotopic line at the isotope resolution the run reads (``HIGH`` for an
    Orbitrap, ``LOW`` for a TOF, whose merged lines are the ones it matches).
    Only the ions of the batch's own collections: a target ion of another
    collection seeds nothing.

    Selected column by column on the fold's own session: it runs inside the
    fold's critical section, which may open no second session.

    :param session: The fold's session.
    :param sample_batch_id: The batch whose collections are read.
    :param target_ion_ids: The ions the sample's run assigned a line to.
    :param resolution: ``HIGH`` or ``LOW``.
    :return: The seed m/z by ``target_ion_id``; an ion of no collection of the
        batch, or with no line at ``resolution``, is absent.
    """
    ion_ids = {str(i) for i in target_ion_ids if i}
    if not ion_ids:
        return {}
    rows = (
        await session.execute(
            select(
                TargetIsotope.target_ion_id,
                TargetIsotope.target_isotope_formula,
                TargetIsotope.mz,
                TargetIon.target_ion_formula,
            )
            .join(TargetIon, TargetIon.target_ion_id == TargetIsotope.target_ion_id)
            .where(
                TargetIsotope.target_ion_id.in_(ion_ids),
                TargetIsotope.resolution == resolution,
                TargetIon.target_compound_id.in_(
                    select(TargetCompoundInTargetCollection.target_compound_id)
                    .join(
                        TargetCollectionInSampleBatch,
                        TargetCollectionInSampleBatch.target_collection_id
                        == TargetCompoundInTargetCollection.target_collection_id,
                    )
                    .where(
                        TargetCollectionInSampleBatch.sample_batch_id == sample_batch_id
                    )
                ),
            )
        )
    ).all()
    by_ion: dict[str, list] = defaultdict(list)
    for row in rows:
        by_ion[row.target_ion_id].append(row)
    return {ion_id: _monoisotopic_mz(isotopes) for ion_id, isotopes in by_ion.items()}


def _assigned_target_ion(row) -> str | None:
    """The target ion whose monoisotopic line a run's row assigned its peak
    to, or None: an isotopologue's row, a reagent's or an unassigned peak's
    names no seed, nor does a reference list's row, which carries no ion.
    Only a row at candidate or better is evidence (the tiers the blend and
    the consensus read as an identification): a row the run refused, such as
    one the mass gate capped to below_assignability for sitting off its
    calibration, mints at its own m/z."""
    if getattr(row, "role", None) != ROLE_M0:
        return None
    if getattr(row, "tier", None) not in BLEND_TIERS:
        return None
    return getattr(row, "target_ion_id", None) or None


async def _seeds_batch_peaks(session, sample_batch_id: str) -> bool:
    """Whether the batch mints a target's anchor at the ion's m/z."""
    return bool(
        (
            await session.execute(
                select(SampleBatch.seed_batch_peaks).where(
                    SampleBatch.sample_batch_id == sample_batch_id
                )
            )
        ).scalar_one_or_none()
    )


def _member_candidate(registry: list, row, provenance: dict) -> tuple[int, str | None]:
    """A folded row's index into its anchor's registry (``registry`` extended
    in place, :func:`~mascope_backend.api.new.peak_assignments.batch_peaks.
    candidate_index`), and the target compound the member keeps as its own.

    The member keeps its own beside the registry's: an isomer of the compound
    an earlier member brought through the same ion shares that member's entry,
    which keeps the first one (``fold_view.member_target_id``).
    """
    target_compound_id = getattr(row, "target_compound_id", None) or None
    candidate = candidate_index(
        registry,
        row.assigned_formula,
        row.ion_formula,
        row.ionization_mechanism_id,
        source=getattr(row, "source", None),
        listing=reference_listing(provenance.get("reference_identities")),
        target_compound_id=target_compound_id,
    )
    return candidate, target_compound_id


async def fold_sample_into_batch_peaks(
    sample_item_id: str,
    *,
    defer_consensus_to: set[str] | None = None,
    rows: list | None = None,
    persisted: bool = True,
) -> str | None:
    """Fold a sample's latest completed assignment into its batch's batch peaks.

    Append-only: each observed peak joins the nearest frozen anchor within its
    resolution-adaptive tolerance (after a per-sample offset correction) or mints a
    new anchor - at the target ion's m/z, where the batch seeds and the row
    assigns the peak to a target ion's monoisotopic line; the consensus of every
    touched batch peak is then recomputed from its members' per-sample
    assignments. Idempotent -- re-folding a sample replaces
    its prior occurrences and re-derives the affected consensus.

    :param sample_item_id: The sample to fold.
    :param defer_consensus_to: When given, the fold writes its occurrences and
        adds the anchors it touched to this set instead of recomputing their
        consensus, leaving that to a later :func:`recompute_batch_consensus` over
        exactly those anchors. That is the backfill's mode: recomputing per
        sample there costs O(samples x anchors), and every intermediate result is
        overwritten by the next sample's. The ids are added only once the fold
        has COMMITTED, so a fold that raised contributes none and the deferred
        pass never reaches an anchor this fold did not write. Until that pass
        runs, a touched anchor's consensus columns describe the members it had
        before this fold.
    :param rows: The sample's assignment rows to fold, in place of the latest
        completed run's. The run-less ingest path hands the fold what the
        stage list's ingest fold judged in memory (``pipeline.INGEST_FOLD``),
        shaped as ledger rows (attribute access), and no run is consulted.
    :param persisted: Whether ``rows`` exist as ``peak_assignment`` rows. False
        for rows that were never written: the members then link to no ledger
        row, while the ids the rows carry still resolve the family link among
        themselves.
    :returns: the ``sample_batch_id`` (for the caller's reload event) or ``None``
        when there is nothing to fold (unknown sample / no completed run).
    """
    async with async_session() as session:
        sample = (
            await session.execute(
                select(Sample).where(Sample.sample_item_id == sample_item_id)
            )
        ).scalar_one_or_none()
        if sample is None:
            return None
        sample_batch_id = sample.sample_batch_id
        # The ledger is partitioned per setup, keyed by its first version: an
        # anchor made under one version is the same species' anchor under the
        # next, so a sample bound under a later version folds into it.
        ionization_mode_id = (await root_ids(session, [sample.ionization_mode_id])).get(
            sample.ionization_mode_id
        )
        filename = sample.filename

        if rows is None:
            run_id = await _latest_completed_run_id(session, sample_item_id)
            if run_id is None:
                return None
            # Every observed peak of the run (assigned or not) folds into a
            # batch peak, so no m/z is dropped from the batch view.
            rows = (
                (
                    await session.execute(
                        select(PeakAssignment).where(
                            PeakAssignment.peak_assignment_run_id == run_id
                        )
                    )
                )
                .scalars()
                .all()
            )
        if not rows:
            return None

        # Per-sample residual mass offset (mu): the median m/z error of assigned
        # peaks. Shifts this sample's peaks onto the shared batch axis before
        # snapping so calibration drift does not split one species across anchors.
        errs = [r.mz_error_ppm for r in rows if r.mz_error_ppm is not None]
        mu_ppm = statistics.median(errs) if errs else 0.0
        mu_factor = 1.0 - mu_ppm / 1e6

        try:
            _, resolution_func = await read_instrument_functions(
                filename, polarity=sample.polarity
            )
        except Exception as exc:  # noqa: BLE001 - resolution is best-effort
            runtime.logger.debug(
                f"No resolution function for '{filename}' ({exc}); "
                "batch-peak tolerance falls back to the drift margin."
            )
            resolution_func = None
        tol_fn = _tolerance_fn(resolution_func)
        # The instrument type, read before the lock below: it may read the
        # sample's props off disk, which the critical section should not wait on.
        intensity_variable = _intensity_variable(filename)

        # Everything from here to the commit is one batch's critical section: the
        # anchor read, the mint, and the insert have to be one indivisible step or
        # concurrent folds duplicate anchors. Waiting here cannot invalidate what
        # was read above: no fold of ANOTHER sample can touch this run, its rows
        # or this sample's offset. A fold of this same sample can leave `run_id`
        # stale, but that is older than this lock and self-corrects -- the next
        # fold of that sample replaces its occurrences wholesale.
        #
        # Taken here rather than at the top for a reason that outlives the
        # ordering: read_instrument_functions above opens sessions of its own
        # while this one is already holding a connection, and blocking on a lock
        # and only then checking out more connections is the hold-and-wait shape
        # that db/__init__ documents as deadlocking a worker. So the invariant
        # this placement buys is that nothing between here and the commit may
        # open a second session -- everything below runs on `session`.
        await _acquire_batch_fold_lock(session, sample_batch_id)
        # A fold edits the current run's state; the first fold of a batch
        # mints that run, so the ledger's history has a first entry.
        await ensure_current_run(session, sample_batch_id)

        # Existing frozen anchors for this (batch, ionization mode, instrument
        # type). The instrument type is read off the unit its anchors are in:
        # an Orbitrap member's intensity is a height and a TOF member's an
        # area, so the two cannot share a trace, and a sample of the other
        # instrument folds into anchors of its own beside this one's. That is
        # the alignment path's rule (one instrument type per alignment), kept
        # here by partitioning rather than by refusing, so no sample's peaks
        # are left out of the batch view. A single-instrument batch has one
        # unit on every anchor, which this filter leaves whole.
        existing = (
            (
                await session.execute(
                    select(BatchPeak).where(
                        BatchPeak.sample_batch_id == sample_batch_id,
                        BatchPeak.ionization_mode_id == ionization_mode_id,
                        BatchPeak.intensity_variable == intensity_variable,
                    )
                )
            )
            .scalars()
            .all()
        )
        anchor_set = AnchorSet(
            [Anchor(bp.batch_peak_id, bp.mz, bp.mz_tol_ppm) for bp in existing]
        )

        # Seeds, where the batch asks for them: a peak that mints an anchor and
        # that the rows assign to a target ion's monoisotopic line mints it at
        # the ion's m/z (batch_peaks.fold_in_sample). Read off the rows, so a
        # target no sample's peak was assigned to mints nothing.
        seed_of = None
        if await _seeds_batch_peaks(session, sample_batch_id):
            seed_mz_of_ion = await target_ion_seed_mzs(
                session,
                sample_batch_id=sample_batch_id,
                target_ion_ids={_assigned_target_ion(r) for r in rows},
                resolution=(
                    "LOW" if intensity_variable == "sum_peak_areas" else "HIGH"
                ),
            )
            if seed_mz_of_ion:

                def seed_of(peak):
                    return seed_mz_of_ion.get(_assigned_target_ion(peak["row"]))

        # Idempotency: drop this sample's prior occurrences (a re-fold), noting the
        # anchors it touched so their consensus is recomputed even if it now leaves
        # them.
        prior = (
            (
                await session.execute(
                    select(BatchPeakOccurrence)
                    .join(
                        BatchPeak,
                        BatchPeak.batch_peak_id == BatchPeakOccurrence.batch_peak_id,
                    )
                    .where(
                        BatchPeakOccurrence.sample_item_id == sample_item_id,
                        BatchPeak.sample_batch_id == sample_batch_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        touched: set[str] = {occ.batch_peak_id for occ in prior}
        for occ in prior:
            await session.delete(occ)
        # Flush the deletes before re-inserting so a re-fold does not collide with
        # this sample's prior rows on the (batch_peak_id, sample_item_id) unique key.
        if prior:
            await session.flush()

        peaks = [
            {
                "mz": float(r.sample_peak_mz) * mu_factor,
                "raw_mz": float(r.sample_peak_mz),
                "row": r,
            }
            for r in rows
        ]
        now = datetime.now(timezone.utc)

        folded = fold_in_sample(
            anchor_set,
            peaks,
            new_id=lambda: gen_id(16),
            tol_fn=tol_fn,
            seed_of=seed_of,
        )

        new_ids = {f.batch_peak_id for f in folded if f.is_new_anchor}
        anchors_by_id = {a.batch_peak_id: a for a in anchor_set.anchors()}
        rows_by_id = {bp.batch_peak_id: bp for bp in existing}
        for bp_id in new_ids:
            a = anchors_by_id[bp_id]
            bp = BatchPeak(
                batch_peak_id=a.batch_peak_id,
                sample_batch_id=sample_batch_id,
                ionization_mode_id=ionization_mode_id,
                mz=a.mz,
                mz_tol_ppm=a.tol_ppm,
                intensity_variable=intensity_variable,
                seeded=a.seeded,
                candidates=[],
                batch_peak_utc_created=now,
                batch_peak_utc_modified=now,
            )
            session.add(bp)
            rows_by_id[bp_id] = bp

        # The family link, resolved while this sample's fold is in hand: an
        # iso_child names the assignment that owns it, and so does a reagent
        # row that is a line of its ion's envelope (step 3.3c); the owner's own
        # peak folded into some anchor in this same pass - or lost a same-anchor
        # contest and folded nowhere, in which case the line names no owner and
        # abstains from the vote (see resolve_isotopologue_of).
        anchor_of_assignment = {
            f.peak["row"].peak_assignment_id: f.batch_peak_id for f in folded
        }
        # Each touched anchor's candidate registry, extended as members bring
        # identities it has not seen. Copied out and reassigned rather than
        # mutated in place, which a JSON column would not notice. A row a
        # pre-pass claimed brings its ion with no formula - the source's ion is
        # known exactly and is no compound of the sample - so the batch can say
        # which ion the anchor is where most of its members were claimed
        # (compute_consensus).
        registries: dict[str, list] = {}
        for f in folded:
            r = f.peak["row"]
            provenance = r.provenance if isinstance(r.provenance, dict) else {}
            candidate, target_compound_id = None, None
            if r.assigned_formula or (r.role in SOURCE_ROLES and r.ion_formula):
                if f.batch_peak_id not in registries:
                    registries[f.batch_peak_id] = list(
                        rows_by_id[f.batch_peak_id].candidates or []
                    )
                candidate, target_compound_id = _member_candidate(
                    registries[f.batch_peak_id], r, provenance
                )
            session.add(
                BatchPeakOccurrence(
                    batch_peak_id=f.batch_peak_id,
                    sample_item_id=sample_item_id,
                    sample_peak_id=r.sample_peak_id,
                    peak_assignment_id=r.peak_assignment_id if persisted else None,
                    mz_delta_ppm=mz_delta_ppm(
                        f.peak["raw_mz"], anchors_by_id[f.batch_peak_id].mz
                    ),
                    intensity=r.sample_peak_intensity,
                    tier=tier_code(r.tier),
                    fit_score=r.fit_score,
                    candidate=candidate,
                    target_compound_id=target_compound_id,
                    role=role_code(r.role),
                    owner_batch_peak_id=(
                        anchor_of_assignment.get(r.owner_peak_assignment_id)
                        if r.role in (ROLE_ISO_CHILD, ROLE_REAGENT)
                        and r.owner_peak_assignment_id
                        else None
                    ),
                    p_correct=provenance.get("p_correct"),
                )
            )
            touched.add(f.batch_peak_id)
        # Compared whole, not by length: a member that matched from a list an
        # identity the registry already held adds its listing to that entry.
        for bp_id, registry in registries.items():
            bp = rows_by_id[bp_id]
            if registry != (bp.candidates or []):
                bp.candidates = registry

        await session.flush()  # make occurrences visible to the consensus recompute
        if defer_consensus_to is None:
            await _recompute_consensus(session, touched, now)
        # The members every batch stage reads were just rewritten: the
        # sample's earlier members, with whatever the stages wrote on them,
        # are gone, so every stage is stale until it runs again on these.
        await mark_every_stage_stale(session, sample_batch_id, REASON_FOLD)
        await touch_ledger(session, sample_batch_id)
        await session.commit()
        committed_at = datetime.now(timezone.utc)
        if defer_consensus_to is not None:
            # After the commit, not before it: a fold that raised rolls its
            # occurrences back, and handing its anchors to the deferred pass
            # would invite that pass to recompute anchors nothing had just
            # written live members into -- which is exactly the case that
            # loses data (see recompute_batch_consensus). And INSIDE the
            # session block, so a teardown that raises between the commit and
            # the caller cannot drop a committed fold's anchors from the set:
            # an anchor this fold MINTED would then keep its server defaults
            # for good, which is the state the deferral must never leave.
            defer_consensus_to |= touched

    # After the session is closed: the pass opens one of its own, and a fold
    # deferring its consensus is a backfill's, which grades once at its end.
    if defer_consensus_to is None and batch_peaks.CONTINUITY_ON_FOLD:
        await _grade_continuity_after_fold(sample_batch_id, committed_at)
    return sample_batch_id


async def _grade_continuity_after_fold(
    sample_batch_id: str, committed_at: datetime
) -> None:
    """The fold's trigger of the batch's continuity pass (``continuity.py``):
    after the fold committed, outside its lock, and never failing it - a fold
    that folded is done whatever the grading does.

    The pass skips where another is grading the batch, and where a grade was
    read after this fold committed; a grade one fold behind is served marked
    (``batch_peaks.continuity_stale``) until the next pass.
    """

    try:
        await run_batch_continuity(sample_batch_id, triggered_at=committed_at)
    except Exception as exc:  # noqa: BLE001 - grading is best-effort
        runtime.logger.warning(
            f"Continuity pass after a fold failed for batch '{sample_batch_id}': {exc}"
        )


#: Anchors per statement in a consensus recompute, and per transaction in a
#: deferred whole-set one. The member read behind each is one IN over the chunk,
#: so this bounds both its bind parameters (asyncpg caps a query at 32767) and
#: the rows one statement materializes, whatever the caller hands in. It stays
#: an ``IN`` rather than one array parameter because the row bound is the one
#: that matters: the member read returns one row per (anchor, sample), so an
#: unbounded statement would trade a clean driver error for an out-of-memory
#: one - and a parameterized array is estimated at ten elements, which freezes
#: a bad generic plan into the prepared statement.
_CONSENSUS_CHUNK = 500


async def _recompute_consensus(
    session, batch_peak_ids: set[str], now: datetime
) -> None:
    """Recompute (and persist) the consensus of the given batch peaks from their
    members' per-sample assignments. A batch peak left with no members is deleted.

    Walked in statements of :data:`_CONSENSUS_CHUNK` anchors. The caller's set is
    not bounded: a fold's touched set is one anchor per detected peak, and
    ``peak_assignment_ingest_max_peaks`` admits a hundred thousand of them, while
    asyncpg refuses a query carrying more than 32767 bind parameters -- so an
    unchunked ``IN`` raises before the statement is even sent, after the ledger
    it belongs to has already committed. The chunk bounds the rows one statement
    materializes (members x chunk) as well.

    Runs on the caller's session and inside the caller's transaction; the chunks
    are statements, not transactions.

    The persistence rule reads its second channel off the other batch peaks'
    readings (:func:`_persistence_facts`), and a chunk reads those of a later
    chunk as they stood before this pass. So where the set took more than one
    chunk, the anchors the rule asked are recomputed once more at the end,
    when every reading in the set is the new one.
    """
    ids = sorted(batch_peak_ids)
    asked: set[str] = set()
    for start in range(0, len(ids), _CONSENSUS_CHUNK):
        asked |= await _recompute_consensus_chunk(
            session, ids[start : start + _CONSENSUS_CHUNK], now
        )
    if len(ids) > _CONSENSUS_CHUNK and asked:
        settle = sorted(asked)
        for start in range(0, len(settle), _CONSENSUS_CHUNK):
            await _recompute_consensus_chunk(
                session, settle[start : start + _CONSENSUS_CHUNK], now
            )


def _consensus_of(
    bp: BatchPeak,
    members: list,
    persistence: Optional[dict],
    diagnostic_isotopologues: Optional[dict],
    merged_isotopologue_of: Optional[dict],
) -> Consensus:
    """The anchor's consensus, every record it carries read with its stale
    mark and kept (``batch_peaks.compute_consensus``), and the record of a
    search that found nothing for it while it still reads nothing: true of
    it then, and of no reading it takes later
    (``batch_search_records.SEARCHED_EMPTY_KEY``)."""
    consensus = compute_consensus(
        members,
        batch_peak_id=bp.batch_peak_id,
        manual=manual_pin_of(bp),
        series=series_record_of(bp, stale=True),
        co_variation=co_variation_record_of(bp, stale=True),
        diagnostic_isotopologues=diagnostic_isotopologues,
        stamped_isotopologue_of=stamped_isotopologue_of_record(bp, stale=True),
        merged_isotopologue_of=merged_isotopologue_of,
        merged_isotopologues=merged_isotopologues_record(bp, stale=True),
        batch_checks=batch_checks_record_of(bp, stale=True),
        known_species=known_species_record_of(bp, stale=True),
        persistence=persistence,
        flat_series=flat_series_record_of(bp, stale=True),
        water_ladder=water_ladder_record_of(bp, stale=True),
    )
    searched_empty = searched_empty_record_of(bp)
    if searched_empty is not None and consensus.consensus_tier == TIER_UNASSIGNED:
        consensus.provenance[SEARCHED_EMPTY_KEY] = searched_empty
    return consensus


async def _recompute_consensus_chunk(
    session, id_list: list[str], now: datetime
) -> set[str]:
    """One statement's worth of :func:`_recompute_consensus`.

    Reads the anchors, when their batches' samples were acquired, and the
    members, and nothing else: a member's formula, ion formula and mechanism
    are the registry entry its ``candidate`` index names on its own anchor, its
    tier and role are codes (``tier_name``, ``role_name``), and its family link
    and P(correct) are columns of its own. The per-sample ledger is not
    consulted, so a member whose ledger row has since been pruned or deleted
    votes exactly as it did the day it was folded. The acquisition times are
    for the probable-blend test's time axis (``batch_blend.py``), read once per
    batch rather than joined to every member.

    Every anchor's vote is taken before any is written. The persistence rule
    (``batch_peaks.hold_persistence_only``) then reads, for the readings it
    asks, what other batch peaks hold beside them (:func:`_persistence_facts`)
    - this chunk's at the readings just voted - and only then is each anchor
    written, and the members of a reading the rule holds stored at candidate
    (:func:`_store_held_members`). The vote reads those members at the tiers
    they were measured at (``batch_peaks.measured_members``).

    :return: The anchors the persistence rule asked.
    """
    if not id_list:
        return set()
    # One read for the anchors rather than one `get` per anchor: a whole-batch
    # recompute walks thousands of them.
    anchors = (
        (
            await session.execute(
                select(BatchPeak).where(BatchPeak.batch_peak_id.in_(id_list))
            )
        )
        .scalars()
        .all()
    )
    registries = {bp.batch_peak_id: bp.candidates for bp in anchors}
    acquired = {
        row.sample_item_id: (row.datetime_utc, row.t0)
        for row in (
            await session.execute(
                select(Sample.sample_item_id, Sample.datetime_utc, Sample.t0).where(
                    Sample.sample_batch_id.in_(
                        sorted({bp.sample_batch_id for bp in anchors})
                    )
                )
            )
        ).all()
    }

    rows = (
        await session.execute(
            select(
                BatchPeakOccurrence.batch_peak_id,
                BatchPeakOccurrence.sample_item_id,
                BatchPeakOccurrence.tier,
                BatchPeakOccurrence.fit_score,
                BatchPeakOccurrence.intensity,
                BatchPeakOccurrence.candidate,
                BatchPeakOccurrence.p_correct,
                BatchPeakOccurrence.role,
                BatchPeakOccurrence.owner_batch_peak_id,
            ).where(BatchPeakOccurrence.batch_peak_id.in_(id_list))
        )
    ).all()

    members_by_peak: dict[str, list] = defaultdict(list)
    for r in rows:
        identity = resolve_candidate(registries.get(r.batch_peak_id), r.candidate)
        when, t0 = acquired.get(r.sample_item_id, (None, None))
        members_by_peak[r.batch_peak_id].append(
            {
                "sample_item_id": r.sample_item_id,
                "acquired": when,
                "t0": t0,
                "assigned_formula": identity.get("formula"),
                "ion_formula": identity.get("ion_formula"),
                "ionization_mechanism_id": identity.get("ionization_mechanism_id"),
                "tier": tier_name(r.tier),
                "fit_score": r.fit_score,
                "intensity": r.intensity,
                "p_correct": r.p_correct,
                "role": role_name(r.role),
                "owner_batch_peak_id": r.owner_batch_peak_id,
            }
        )

    # The lines each parent stamped, where the parent's diagnostic
    # isotopologues record comes off here: its reading moved, or the anchor
    # went.
    unstamped: dict[str, set[str]] = {}
    # The records this recompute marked stale, by key, and their batches: the
    # stage that wrote each is stale until it runs again.
    stale_keys: set[str] = set()
    stale_batches: set[str] = set()
    # The rows a strip read as a parent's line whose record comes off here:
    # their members read them otherwise now, or the anchor went. Parent id ->
    # those rows.
    released: dict[str, set[str]] = defaultdict(set)
    voted: dict[str, tuple[BatchPeak, Consensus]] = {}
    for bp in anchors:
        persistence = persistence_record_of(bp)
        # The vote reads a member the persistence rule holds at the tier it
        # was measured at; the rule then decides what each is stored at.
        members = measured_members(
            members_by_peak.get(bp.batch_peak_id, []), persistence
        )
        members_by_peak[bp.batch_peak_id] = members
        # Every record read with its stale mark: the recompute keeps them
        # all, and marks them by what the new reading names.
        diagnostic_isotopologues = diagnostic_isotopologues_record_of(bp, stale=True)
        merged_isotopologue_of = merged_isotopologue_of_record(bp, stale=True)
        if not members:
            if diagnostic_isotopologues is not None:
                unstamped[bp.batch_peak_id] = stamped_lines(diagnostic_isotopologues)
            if merged_isotopologue_of is not None:
                released[merged_isotopologue_of["batch_peak_id"]].add(bp.batch_peak_id)
            await session.delete(bp)
            continue
        consensus = _consensus_of(
            bp, members, persistence, diagnostic_isotopologues, merged_isotopologue_of
        )
        newly = went_stale(bp.provenance, consensus.provenance)
        if newly:
            stale_keys |= newly
            stale_batches.add(bp.sample_batch_id)
        if DIAGNOSTIC_ISOTOPOLOGUES_KEY in newly:
            unstamped[bp.batch_peak_id] = stamped_lines(diagnostic_isotopologues)
        if MERGED_ISOTOPOLOGUE_OF_KEY in newly:
            released[merged_isotopologue_of["batch_peak_id"]].add(bp.batch_peak_id)
        voted[bp.batch_peak_id] = (bp, consensus)
    asked = {
        bp_id: consensus
        for bp_id, (_, consensus) in voted.items()
        if persistence_asks(consensus) is not None
    }
    holds: dict[str, bool] = {}
    if asked:
        facts = await _persistence_facts(session, asked, voted)
        for bp_id, consensus in asked.items():
            holds[bp_id] = hold_persistence_only(
                consensus, facts.get(bp_id, PersistenceFacts())
            )
    renamed = renamed_parents(voted)
    for bp_id, (bp, consensus) in voted.items():
        await _store_held_members(
            session, bp_id, consensus, members_by_peak[bp_id], holds.get(bp_id, False)
        )
        _apply_consensus(bp, consensus, now)
    await _follow_parents(session, unstamped, released, renamed, now)
    stages = {STAGE_OF_KEY[key] for key in stale_keys if key in STAGE_OF_KEY}
    for sample_batch_id in sorted(stale_batches):
        # A stage running now is left to its own end: a record its own run
        # leaves behind is its run's business, not news to it.
        await mark_stale(
            session, sample_batch_id, stages, REASON_CONSENSUS, skip_running=True
        )
    return set(asked)


async def _store_held_members(
    session, bp_id: str, consensus: Consensus, members: list[dict], holds: bool
) -> None:
    """Store the anchor's members at the tiers the persistence rule gives them
    (``batch_peaks.held_member_tiers``), and keep on its record the ones it
    holds, so a sample's own view of the reading stands where the batch's
    does and a lift can put them back. Nothing for an anchor with no record.
    """
    record = consensus.provenance.get(PERSISTENCE_KEY)
    if not isinstance(record, dict):
        return
    moves, held = held_member_tiers(members, record, holds)
    record = {key: value for key, value in record.items() if key != HELD_MEMBERS_KEY}
    if held:
        record[HELD_MEMBERS_KEY] = held
    consensus.provenance[PERSISTENCE_KEY] = record
    by_tier: dict[str, list[str]] = defaultdict(list)
    for sample_item_id, tier in moves.items():
        by_tier[tier].append(sample_item_id)
    for tier, samples in sorted(by_tier.items()):
        await session.execute(
            update(BatchPeakOccurrence)
            .where(
                BatchPeakOccurrence.batch_peak_id == bp_id,
                BatchPeakOccurrence.sample_item_id.in_(sorted(samples)),
            )
            .values(tier=tier_code(tier))
        )


def line_samples(rows: Iterable[tuple], asked: dict[str, Consensus]) -> dict[str, int]:
    """How many samples hold a committed line of each asked reading's isotope
    pattern.

    A member names an owner batch peak only where its sample read it as a line
    of the owner's row: an analyte's isotopologue, under the owner's formula,
    or a source ion's line, under none. So a member naming the batch peak
    under the reading's formula and mechanism is a line of that reading; one
    under another formula is a line of another reading of the peak, a sample
    that dissents from the batch.

    :param rows: (owner batch peak id, sample id, formula, mechanism) of every
        member that names an asked batch peak as its owner.
    :param asked: Batch peak id -> the fresh consensus the rule asks.
    :return: Batch peak id -> the samples holding such a line; absent for none.
    """
    samples: dict[str, set] = defaultdict(set)
    for owner, sample_item_id, formula, mechanism in rows:
        consensus = asked.get(owner)
        if (
            consensus is not None
            and formula == consensus.consensus_formula
            and mechanism == consensus.ionization_mechanism_id
        ):
            samples[owner].add(sample_item_id)
    return {owner: len(held) for owner, held in samples.items()}


def partition_channels(
    stored: Iterable[tuple], fresh: dict[str, tuple[tuple, Consensus]]
) -> dict[tuple, set[str]]:
    """The mechanisms each neutral is committed through, per partition.

    As a run counts a second channel (``cross_channel.channels_by_neutral``):
    a reading that commits a formula on a peak of its own - no line of
    another batch peak's - through a mechanism, keyed on the neutral's
    composition (``utils.formula_identity``), so two spellings of one formula
    are one neutral. A batch peak's fresh consensus replaces what the
    ledger stored for it.

    :param stored: (batch peak id, partition, formula, mechanism,
        ``isotopologue_of``) as the ledger holds them.
    :param fresh: Batch peak id -> (its partition, its fresh consensus).
    :return: (partition, neutral key) -> the mechanisms committing it.
    """
    readings = {
        bp_id: (partition, formula, mechanism, isotopologue_of)
        for bp_id, partition, formula, mechanism, isotopologue_of in stored
    }
    for bp_id, (partition, consensus) in fresh.items():
        readings[bp_id] = (
            partition,
            consensus.consensus_formula,
            consensus.ionization_mechanism_id,
            consensus.isotopologue_of,
        )
    keys: dict[str, str] = {}
    channels: dict[tuple, set[str]] = defaultdict(set)
    for partition, formula, mechanism, isotopologue_of in readings.values():
        if not formula or not mechanism or isotopologue_of is not None:
            continue
        if formula not in keys:
            keys[formula] = formula_identity(formula)
        channels[(partition, keys[formula])].add(mechanism)
    return channels


def _partition_of(bp: BatchPeak) -> tuple:
    return (bp.sample_batch_id, bp.ionization_mode_id, bp.intensity_variable)


async def _persistence_facts(
    session,
    asked: dict[str, Consensus],
    voted: dict[str, tuple[BatchPeak, Consensus]],
) -> dict[str, PersistenceFacts]:
    """What the batch holds beside each reading the persistence rule asks.

    - **A line of its isotope pattern**: the samples in which a member of
      another batch peak is that reading's isotopologue (:func:`line_samples`)
      - a line a sample's run committed, a line the search paired, a row the
      strip rewrote: each is a member whose sample read its peak as the
      reading's line.
    - **Another channel**: the mechanisms other than the reading's through
      which a batch peak of the same partition - the batch, the ionization
      mode, the instrument type - commits the same neutral, at any tier that
      commits it, as a run counts a second channel
      (:func:`partition_channels`). This chunk's batch peaks are read at the
      readings just voted, every other at the one the ledger holds.

    :param asked: Anchor id -> the fresh consensus the rule asks.
    :param voted: Every anchor of the chunk, with its fresh consensus.
    :return: Anchor id -> its facts; an anchor with none is absent.
    """
    ids = sorted(asked)
    # The readings earlier chunks of this pass wrote are still pending on the
    # session; read without a flush, a session that does not autoflush would
    # answer with what they read before the pass.
    await session.flush()
    entry = BatchPeak.candidates[BatchPeakOccurrence.candidate]
    lines = line_samples(
        (
            await session.execute(
                select(
                    BatchPeakOccurrence.owner_batch_peak_id,
                    BatchPeakOccurrence.sample_item_id,
                    entry["formula"].as_string(),
                    entry["ionization_mechanism_id"].as_string(),
                )
                .join(
                    BatchPeak,
                    BatchPeak.batch_peak_id == BatchPeakOccurrence.batch_peak_id,
                )
                .where(BatchPeakOccurrence.owner_batch_peak_id.in_(ids))
            )
        ).all(),
        asked,
    )
    # Every reading of the batch, each in its own partition: the batch's
    # committed batch peaks are a few thousand rows of four short columns.
    sample_batch_ids = sorted({voted[bp_id][0].sample_batch_id for bp_id in ids})
    stored = [
        (bp_id, (sample_batch_id, mode, unit), formula, mechanism, isotopologue_of)
        for bp_id, sample_batch_id, mode, unit, formula, mechanism, isotopologue_of in (
            await session.execute(
                select(
                    BatchPeak.batch_peak_id,
                    BatchPeak.sample_batch_id,
                    BatchPeak.ionization_mode_id,
                    BatchPeak.intensity_variable,
                    BatchPeak.consensus_formula,
                    BatchPeak.ionization_mechanism_id,
                    BatchPeak.isotopologue_of,
                ).where(
                    BatchPeak.sample_batch_id.in_(sample_batch_ids),
                    BatchPeak.consensus_formula.is_not(None),
                )
            )
        ).all()
    ]
    channels = partition_channels(
        stored,
        {
            bp_id: (_partition_of(bp), consensus)
            for bp_id, (bp, consensus) in voted.items()
        },
    )
    facts: dict[str, PersistenceFacts] = {}
    for bp_id in ids:
        bp, consensus = voted[bp_id]
        others = channels.get(
            (_partition_of(bp), formula_identity(consensus.consensus_formula)), set()
        ) - {consensus.ionization_mechanism_id}
        if lines.get(bp_id) or others:
            facts[bp_id] = PersistenceFacts(
                line_samples=lines.get(bp_id, 0), channels=tuple(sorted(others))
            )
    return facts


def _line_ids(key: str, record: dict) -> set[str]:
    """The batch peaks a parent's isotope-line record names as its lines."""
    if key == DIAGNOSTIC_ISOTOPOLOGUES_KEY:
        return stamped_lines(record)
    return {
        entry["batch_peak_id"]
        for entry in record.get("lines") or []
        if isinstance(entry, dict) and entry.get("batch_peak_id")
    }


def renamed_parents(
    voted: dict[str, tuple[Any, Consensus]],
) -> dict[str, tuple[Consensus, set[str]]]:
    """The parents whose isotope-line records a recompute renamed: their vote
    moved to another reading of the ion the records stand on
    (``batch_peaks.on_the_reading``, MAS-1144). Read before the votes are
    written, as it compares each anchor's records with its vote's.

    :param voted: Anchor id -> the anchor and its fresh vote.
    :return: Parent id -> its vote and the lines its renamed records name.
    """
    renamed: dict[str, tuple[Consensus, set[str]]] = {}
    for bp_id, (bp, consensus) in voted.items():
        lines: set[str] = set()
        for key in (DIAGNOSTIC_ISOTOPOLOGUES_KEY, MERGED_ISOTOPOLOGUES_KEY):
            before = (bp.provenance or {}).get(key)
            after = consensus.provenance.get(key)
            if (
                isinstance(before, dict)
                and isinstance(after, dict)
                and not is_stale(after)
                and any(before.get(k) != after.get(k) for k in _READING)
            ):
                lines |= _line_ids(key, after)
        if lines:
            renamed[bp_id] = (consensus, lines)
    return renamed


#: The parts of a parent's reading its lines' records name beside its ion.
_READING = ("formula", "ionization_mechanism_id")


async def _follow_parents(
    session,
    unstamped: dict[str, set[str]],
    released: dict[str, set[str]],
    renamed: dict[str, tuple[Consensus, set[str]]],
    now: datetime,
) -> None:
    """Carry a recompute's moves of its parents onto their isotope lines, which
    need not be in it: the stamps taken off where a parent moved to another
    ion (:func:`_take_off_stamps`), the stripped rows its members released
    taken off their parent's list (:func:`_take_off_merged_lines`), and the
    lines of a parent that moved to another reading of its ion renamed with it
    (:func:`_rename_lines`)."""
    await _take_off_stamps(session, unstamped, now)
    await _take_off_merged_lines(session, released, now)
    await _rename_lines(session, renamed, now)


async def _rename_lines(
    session, renamed: dict[str, tuple[Consensus, set[str]]], now: datetime
) -> None:
    """Rename a stamped line's record, and a stripped row's, to the reading
    their parent moved to within its ion (:func:`renamed_parents`): the line
    is still that parent's, and its record names what the parent reads now,
    as the parent's own does. A line that is another parent's since keeps
    that one's record, renamed only if that parent was.
    """
    lines = sorted({line for _, ids in renamed.values() for line in ids})
    if not lines:
        return
    anchors = (
        (
            await session.execute(
                select(BatchPeak).where(BatchPeak.batch_peak_id.in_(lines))
            )
        )
        .scalars()
        .all()
    )
    for line in anchors:
        provenance = dict(line.provenance or {})
        for key in (STAMPED_ISOTOPOLOGUE_OF_KEY, MERGED_ISOTOPOLOGUE_OF_KEY):
            record = provenance.get(key)
            parent = (
                renamed.get(record.get("batch_peak_id"))
                if isinstance(record, dict)
                else None
            )
            if parent is not None:
                provenance[key] = on_the_reading(record, parent[0])
        if provenance != (line.provenance or {}):
            line.provenance = provenance
            line.batch_peak_utc_modified = now


async def _take_off_stamps(
    session, unstamped: dict[str, set[str]], now: datetime
) -> None:
    """Mark a parent's stamps stale on the lines it stamped, once its
    diagnostic isotopologues record has gone stale.

    A stamp says a line holds its predicted height against the parent's
    reading, so it stands with the reading, and is kept marked stale once the
    reading moves (``batch_peaks.kept_record``), as the parent's record is.
    The line's own consensus cannot see that happen - the line need not be in
    the recompute that moved the parent - so the parent's recompute does it. A
    line stamped since by another parent keeps that one's stamp.

    :param unstamped: Parent id -> the lines its record stamped.
    """
    lines = sorted({line for ids in unstamped.values() for line in ids})
    if not lines:
        return
    anchors = (
        (
            await session.execute(
                select(BatchPeak).where(BatchPeak.batch_peak_id.in_(lines))
            )
        )
        .scalars()
        .all()
    )
    for line in anchors:
        record = stamped_isotopologue_of_record(line)
        if record is None or line.batch_peak_id not in unstamped.get(
            record["batch_peak_id"], ()
        ):
            continue
        provenance = dict(line.provenance or {})
        provenance[STAMPED_ISOTOPOLOGUE_OF_KEY] = kept_record(record, False)
        line.provenance = provenance
        line.batch_peak_utc_modified = now


async def _take_off_merged_lines(
    session, released: dict[str, set[str]], now: datetime
) -> None:
    """Take a stripped row off its parent's list of lines, once the row's own
    record has come off.

    The parent's record says which rows fold under it as its lines, and its
    mark and the ledger's *Stripped rows* filter read it. A row released from
    the parent - its samples assigned again, a re-fold, a curation, or the
    anchor gone - is no longer one of them; the parent's own consensus cannot
    see that happen, as the parent need not be in the recompute that released
    the row, so the row's recompute does it. A parent with no line left
    carries no record.

    :param released: Parent id -> the rows whose records came off.
    """
    if not released:
        return
    parents = (
        (
            await session.execute(
                select(BatchPeak).where(BatchPeak.batch_peak_id.in_(sorted(released)))
            )
        )
        .scalars()
        .all()
    )
    for parent in parents:
        record = merged_isotopologues_record(parent)
        if record is None:
            continue
        kept = without_merged_lines(record, released[parent.batch_peak_id])
        if kept == record:
            continue
        provenance = dict(parent.provenance or {})
        if kept is None:
            provenance.pop(MERGED_ISOTOPOLOGUES_KEY, None)
        else:
            provenance[MERGED_ISOTOPOLOGUES_KEY] = kept
        parent.provenance = provenance
        parent.batch_peak_utc_modified = now


def _json_shape(value):
    """``value`` as a JSON column hands it back, so a freshly computed list or
    dict compares equal to the stored one (tuples become lists, keys strings)."""
    return json.loads(json.dumps(value))


def _apply_consensus(bp: BatchPeak, consensus: Consensus, now: datetime) -> bool:
    """Write ``consensus`` onto the anchor row, if it differs from what is there.

    Every fold recomputes every anchor its sample touched, and most of those
    recomputes reach the answer the last one did - a species already seen in
    thirty samples does not change formula because a thirty-first agrees.
    Writing the row regardless, which stamping ``batch_peak_utc_modified``
    unconditionally did, turned every fold into a rewrite of most of the batch:
    on a 32-sample batch that was 44k updates for 1,700 anchors and a heap
    eighteen times its compacted size. So the row is compared first and left
    alone when nothing moved; the modified stamp then means what it says.

    An importing engine's verdict (``engine_tier``, ``engine_claim``) is about
    the identity the consensus named when the import wrote it, so it comes off
    when that identity moves - a new sample's vote, a curation, a re-fold -
    rather than standing beside a formula the engine never judged.

    :param bp: The anchor row, as loaded in the caller's session.
    :param consensus: The freshly computed consensus for it.
    :param now: The stamp to record if something changed.
    :return: Whether anything was written.
    """
    values = {
        "consensus_formula": consensus.consensus_formula,
        "consensus_ion_formula": consensus.consensus_ion_formula,
        "ionization_mechanism_id": consensus.ionization_mechanism_id,
        "consensus_tier": consensus.consensus_tier,
        "consensus_role": consensus.consensus_role,
        "best_fit_score": consensus.best_fit_score,
        "support_fraction": consensus.support_fraction,
        "n_present": consensus.n_present,
        "is_ambiguous": int(consensus.is_ambiguous),
        "max_intensity": consensus.max_intensity,
        "isotopologue_of": consensus.isotopologue_of,
        "alternatives": _json_shape(consensus.alternatives),
        "provenance": _json_shape(consensus.provenance),
    }
    changed = {
        column: value
        for column, value in values.items()
        if getattr(bp, column) != value
    }
    if not changed:
        return False
    if {"consensus_formula", "ionization_mechanism_id"} & changed.keys():
        changed.update(
            engine_tier=None,
            engine_claim=None,
            engine_level=None,
            engine_evidence=None,
            engine_lift=None,
        )
    for column, value in changed.items():
        setattr(bp, column, value)
    bp.batch_peak_utc_modified = now
    return True


async def recompute_batch_consensus(
    sample_batch_id: str, batch_peak_ids: set[str]
) -> int:
    """Recompute the consensus of the given anchors of one batch, once each.

    The per-arrival fold recomputes the anchors its sample touched, which is
    right for one sample and wrong for a backfill: folding N samples that way
    recomputes each anchor up to N times, rewrites it as often, and the member
    read behind each recompute grows with the samples already folded - O(N x
    anchors) work for a result the last pass alone determines. The backfill
    folds every sample with the recompute deferred, collecting the anchors those
    folds touched, and then calls this once.

    **Scoped to those anchors, never to the whole batch.** An anchor no fold
    just touched already holds the consensus of its unchanged members, so
    walking it is work for no result - and on a large batch most of the work.
    It used to be load-bearing for correctness as well: the ion formula, the
    mechanism and the family link lived on the ledger rows alone, so
    recomputing an anchor whose members had lost theirs (a run deleted
    outright, or pruned) blanked those columns for good. A member now carries
    everything the vote reads (see :func:`_recompute_consensus_chunk`), so a
    recompute over dead-linked members reaches the answer their fold did; the
    scoping is a matter of cost only.

    Chunked, and each chunk is its own transaction under the fold lock, so a
    fold arriving mid-way waits for a chunk rather than for the whole set.
    The two cannot disagree: an arriving fold recomputes its own anchors, and a
    chunk that runs after it recomputes them again from the same members.

    :param sample_batch_id: The batch the anchors belong to, for the fold lock.
    :param batch_peak_ids: The anchors to recompute - what the deferred folds
        reported touching. Empty means there is nothing to do.
    :return: How many anchors were walked.
    """
    anchor_ids = sorted(batch_peak_ids)
    now = datetime.now(timezone.utc)
    asked: set[str] = set()
    for start in range(0, len(anchor_ids), _CONSENSUS_CHUNK):
        chunk = anchor_ids[start : start + _CONSENSUS_CHUNK]
        async with async_session() as session:
            await _acquire_batch_fold_lock(session, sample_batch_id)
            asked |= await _recompute_consensus_chunk(session, chunk, now)
            await touch_ledger(session, sample_batch_id)
            await session.commit()
    # The persistence rule's second pass, as `_recompute_consensus` takes one:
    # a chunk read the later chunks' readings as they stood before this pass.
    if len(anchor_ids) > _CONSENSUS_CHUNK and asked:
        settle = sorted(asked)
        for start in range(0, len(settle), _CONSENSUS_CHUNK):
            async with async_session() as session:
                await _acquire_batch_fold_lock(session, sample_batch_id)
                await _recompute_consensus_chunk(
                    session, settle[start : start + _CONSENSUS_CHUNK], now
                )
                await touch_ledger(session, sample_batch_id)
                await session.commit()
    return len(anchor_ids)


def clear_engine_verdicts_statement(sample_batch_id: str):
    """The statement that takes every importing engine's verdict off a batch's
    anchors, for the caller to run in its own transaction under the fold lock."""
    return (
        update(BatchPeak)
        .where(
            BatchPeak.sample_batch_id == sample_batch_id,
            (BatchPeak.engine_tier.is_not(None))
            | (BatchPeak.engine_claim.is_not(None))
            | (BatchPeak.engine_level.is_not(None))
            | (BatchPeak.engine_evidence.is_not(None))
            | (BatchPeak.engine_lift.is_not(None)),
        )
        .values(
            engine_tier=None,
            engine_claim=None,
            engine_level=None,
            engine_evidence=None,
            engine_lift=None,
        )
    )


async def clear_engine_verdicts(sample_batch_id: str) -> None:
    """Take every importing engine's verdict off a batch's anchors.

    A rebuild puts the ledger back to what the samples' own runs say, so an
    import's verdict has nothing left to be about, even on an anchor whose
    re-folded consensus happens to name the same identity; the import's run
    snapshot keeps it.
    """
    async with async_session() as session:
        await _acquire_batch_fold_lock(session, sample_batch_id)
        await session.execute(clear_engine_verdicts_statement(sample_batch_id))
        await touch_ledger(session, sample_batch_id)
        await session.commit()
