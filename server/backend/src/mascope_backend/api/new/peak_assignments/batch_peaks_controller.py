"""DB controller: fold a sample's per-sample assignments into the batch peaks.

Wraps the pure :mod:`batch_peaks` engine with the database I/O behind the batch
overview. :func:`fold_sample_into_batch_peaks` runs on the arrival path right
after Stage-A assignment (the assignments are already committed by then), snapping
the sample's peaks into the batch's frozen, append-only anchors and recomputing the
consensus of every touched batch peak. :func:`backfill_sample_batch_peaks` folds a
whole batch's existing runs in time order, reporting progress per sample, and
recomputes the anchors those folds touched once each at the end
(:func:`recompute_batch_consensus`) rather than once per sample.

Design: ``docs/dev/peak_assignment_batch.md``.
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import String, any_, bindparam, func, select
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.exc import IntegrityError

from mascope_backend.api.lib.api_features import api_controller_background_task
from mascope_backend.api.new.instrument_configs.lib import read_instrument_functions
from mascope_backend.api.new.peak_assignments.batch_peaks import (
    ROLE_ISO_CHILD,
    Anchor,
    AnchorSet,
    Consensus,
    compute_consensus,
    fold_in_sample,
    resolution_adaptive_tol_ppm,
)
from mascope_backend.db import (
    BatchPeak,
    BatchPeakOccurrence,
    PeakAssignment,
    PeakAssignmentRun,
    Sample,
    async_session,
)
from mascope_backend.db.id import gen_id
from mascope_backend.runtime import runtime
from mascope_backend.socket.notifications import (
    UserNotification,
    send_progress_user_notification,
)
from mascope_file.name import get_instrument_type


def _intensity_variable(filename: str) -> str:
    """The batch peak's intensity unit, from the instrument type (areas for TOF,
    heights for Orbitrap). Defaults to heights if the filename cannot be classified
    -- an unclassifiable file must never abort the fold-in."""
    try:
        instrument_type = get_instrument_type(filename)
    except Exception:  # noqa: BLE001 - classification is best-effort
        instrument_type = None
    return "sum_peak_areas" if instrument_type == "tof" else "sum_peak_heights"


async def _latest_completed_run_id(session, sample_item_id: str) -> str | None:
    return (
        await session.execute(
            select(PeakAssignmentRun.peak_assignment_run_id)
            .where(
                PeakAssignmentRun.sample_item_id == sample_item_id,
                PeakAssignmentRun.status == "completed",
            )
            .order_by(PeakAssignmentRun.peak_assignment_run_utc_created.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


#: Namespace discriminator for the fold-in lock, hashed into the first int of the
#: two-int advisory-lock key space so these locks cannot collide with the other
#: advisory-lock users (match writes, assignment claims).
_BATCH_PEAK_FOLD_LOCK_NAMESPACE = "mascope_batch_peak_fold"


async def _acquire_batch_fold_lock(session, sample_batch_id: str) -> None:
    """Serialize this batch's fold-ins for the rest of the caller's transaction.

    Anchors are frozen and append-only, and no unique constraint can catch a
    duplicate one -- anchor identity is tolerance-based, which an exact-value key
    cannot express. So the only thing keeping two folds from each minting an
    anchor for the same species is that they do not read the anchor set before
    the other has committed its own. Nothing enforced that. Run finalize and
    import publish are admitted per *sample*, so two samples of one batch can be
    assigned or published at the same time and their folds overlap; the backfill
    is admitted not at all, so it can overlap either of those or a second
    backfill of the same batch; and production runs parallel workers, which no
    in-process gate would reach anyway. A duplicate minted that way is permanent
    -- later folds snap to whichever anchor is nearest, so one species stays
    split across two traces with both support fractions wrong.

    Transaction-scoped, so it releases on the caller's commit or rollback with no
    unlock to forget. Keyed on the batch, so folds of different batches still run
    in parallel. ``hashtext`` is 32-bit and the key space is two ints, so a
    collision is possible; what it cannot do is let two folds of one batch
    through, because the same batch id always hashes to the same key. (Postgres
    keeps session- and transaction-scoped advisory locks in one space, so a
    collision with :mod:`admission`'s never-committed claim would be a hang
    rather than a slowdown. Two namespaces and two ids would have to collide at
    once; the point here is only that the failure is one-sided in our favour for
    the case that matters -- a missed lock -- not that every collision is cheap.)

    **Depends on READ COMMITTED**, which is what the engine runs (no
    ``isolation_level`` is set anywhere). The fold's transaction opens well
    before this lock -- at the ``Sample`` read -- so the anchor SELECT that
    follows is only guaranteed to see the previous holder's committed anchors
    because each statement takes its own snapshot. Under REPEATABLE READ the
    lock would still serialize perfectly and the duplicate anchors would come
    straight back, with nothing in the wait to hint at it.

    :param session: The session whose transaction takes and holds the lock; must
        be the one that goes on to read the anchors and write the fold.
    :type session: AsyncSession
    :param sample_batch_id: The batch whose fold-ins are serialized.
    :type sample_batch_id: str
    """
    await session.execute(
        select(
            func.pg_advisory_xact_lock(
                func.hashtext(_BATCH_PEAK_FOLD_LOCK_NAMESPACE),
                func.hashtext(sample_batch_id),
            )
        )
    )


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


async def fold_sample_into_batch_peaks(
    sample_item_id: str, *, defer_consensus_to: set[str] | None = None
) -> str | None:
    """Fold a sample's latest completed assignment into its batch's batch peaks.

    Append-only: each observed peak joins the nearest frozen anchor within its
    resolution-adaptive tolerance (after a per-sample offset correction) or mints a
    new anchor; the consensus of every touched batch peak is then recomputed from
    its members' per-sample assignments. Idempotent -- re-folding a sample replaces
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
        ionization_mode_id = sample.ionization_mode_id
        filename = sample.filename

        run_id = await _latest_completed_run_id(session, sample_item_id)
        if run_id is None:
            return None

        # Every observed peak of the run (assigned or not) folds into a batch peak,
        # so no m/z is dropped from the batch view.
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
            _, resolution_func = await read_instrument_functions(filename)
        except Exception as exc:  # noqa: BLE001 - resolution is best-effort
            runtime.logger.debug(
                f"No resolution function for '{filename}' ({exc}); "
                "batch-peak tolerance falls back to the drift margin."
            )
            resolution_func = None
        tol_fn = _tolerance_fn(resolution_func)

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

        # Existing frozen anchors for this (batch, ionization mode).
        existing = (
            (
                await session.execute(
                    select(BatchPeak).where(
                        BatchPeak.sample_batch_id == sample_batch_id,
                        BatchPeak.ionization_mode_id == ionization_mode_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        anchor_set = AnchorSet(
            [Anchor(bp.batch_peak_id, bp.mz, bp.mz_tol_ppm) for bp in existing]
        )

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
        intensity_variable = _intensity_variable(filename)

        folded = fold_in_sample(
            anchor_set, peaks, new_id=lambda: gen_id(16), tol_fn=tol_fn
        )

        new_ids = {f.batch_peak_id for f in folded if f.is_new_anchor}
        anchors_by_id = {a.batch_peak_id: a for a in anchor_set.anchors()}
        for bp_id in new_ids:
            a = anchors_by_id[bp_id]
            session.add(
                BatchPeak(
                    batch_peak_id=a.batch_peak_id,
                    sample_batch_id=sample_batch_id,
                    ionization_mode_id=ionization_mode_id,
                    mz=a.mz,
                    mz_tol_ppm=a.tol_ppm,
                    intensity_variable=intensity_variable,
                    batch_peak_utc_created=now,
                    batch_peak_utc_modified=now,
                )
            )

        for f in folded:
            r = f.peak["row"]
            session.add(
                BatchPeakOccurrence(
                    batch_peak_id=f.batch_peak_id,
                    sample_item_id=sample_item_id,
                    sample_peak_id=r.sample_peak_id,
                    peak_assignment_id=r.peak_assignment_id,
                    sample_peak_mz=f.peak["raw_mz"],
                    intensity=r.sample_peak_intensity,
                    tier=r.tier,
                    fit_score=r.fit_score,
                    assigned_formula=r.assigned_formula,
                )
            )
            touched.add(f.batch_peak_id)

        await session.flush()  # make occurrences visible to the consensus recompute
        if defer_consensus_to is None:
            await _recompute_consensus(session, touched, now)
        await session.commit()
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

    return sample_batch_id


async def _owner_anchor_by_assignment(session, owner_ids: set[str]) -> dict[str, str]:
    """Map each owning assignment to the batch peak its own peak folded into.

    The family link an isotopologue needs is per-sample: an ``iso_child`` assignment
    names the assignment that owns it, and what the batch level wants is the
    ANCHOR that owning peak landed on. ``BatchPeakOccurrence`` already records
    the assignment each member was folded from, so the hop is one indexed lookup
    rather than a walk back through the owner's sample peak.

    An owner with no occurrence resolves to nothing, which is the honest answer:
    a peak dropped from the fold (two peaks inside one anchor's tolerance in one
    sample, nearest wins) has no anchor to point at, and the isotopologue abstains
    from the vote rather than naming a peak that is not in the ledger.

    One lookup per owner, not one per (owner, sample), because an assignment
    belongs to a single sample and a sample's peak folds into a single anchor -
    so an assignment has at most one occurrence. No constraint says so
    (``batch_peak_occurrence`` is unique on batch peak + sample), which is why
    it is written down here rather than relied on silently.

    Runs on the caller's session, as everything between the fold lock and the
    commit must.

    :param session: The fold's session, already holding the batch lock.
    :param owner_ids: ``peak_assignment_id`` of each owning assignment.
    :return: owning ``peak_assignment_id`` -> ``batch_peak_id``.
    :rtype: dict[str, str]
    """
    if not owner_ids:
        return {}
    # `= ANY(array)` rather than `IN`, which would expand to one bind parameter
    # per id: this set is one entry per iso_child member of the whole recompute,
    # so a chunk of anchors in a large batch reaches tens of thousands of them
    # (anchors x samples) and asyncpg refuses a query with more than 32767
    # arguments. Chunking is the wrong tool here -- the ids are already bounded
    # by the caller's chunk and the read returns at most one row each, so only
    # the parameter count is the problem, and an array is one parameter whatever
    # its length. The index on `peak_assignment_id` serves it either way.
    #
    # The element type stays UNQUALIFIED `String`, rendering `$1::VARCHAR[]`.
    # `String(32)` would render `$1::VARCHAR(32)[]`, and a length-qualified cast
    # in Postgres TRUNCATES rather than errors - so an over-long id would
    # silently match the wrong row instead of matching nothing. Do not "tighten"
    # this to the column's own length.
    rows = (
        await session.execute(
            select(
                BatchPeakOccurrence.peak_assignment_id,
                BatchPeakOccurrence.batch_peak_id,
            ).where(
                BatchPeakOccurrence.peak_assignment_id
                == any_(
                    bindparam("owner_ids", value=list(owner_ids), type_=ARRAY(String))
                )
            )
        )
    ).all()
    return {peak_assignment_id: bp_id for peak_assignment_id, bp_id in rows}


#: Anchors per statement in a consensus recompute, and per transaction in a
#: deferred whole-set one. The member read behind each is one IN over the chunk,
#: so this bounds both its bind parameters (asyncpg caps a query at 32767) and
#: the rows one statement materializes, whatever the caller hands in. The row
#: bound is the binding one here and it is why these two stay an ``IN`` rather
#: than becoming the array `_owner_anchor_by_assignment` uses: the member read
#: returns one row per (anchor, sample) carrying a whole ``provenance``
#: document, so a single unbounded statement would trade a clean driver error
#: for an out-of-memory one - and a parameterized array is estimated at ten
#: elements, which freezes a bad generic plan into the prepared statement.
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
    """
    ids = sorted(batch_peak_ids)
    for start in range(0, len(ids), _CONSENSUS_CHUNK):
        await _recompute_consensus_chunk(
            session, ids[start : start + _CONSENSUS_CHUNK], now
        )


async def _recompute_consensus_chunk(
    session, id_list: list[str], now: datetime
) -> None:
    """One statement's worth of :func:`_recompute_consensus`."""
    if not id_list:
        return
    rows = (
        await session.execute(
            select(
                BatchPeakOccurrence.batch_peak_id,
                BatchPeakOccurrence.assigned_formula,
                BatchPeakOccurrence.tier,
                BatchPeakOccurrence.fit_score,
                BatchPeakOccurrence.intensity,
                PeakAssignment.ion_formula,
                PeakAssignment.ionization_mechanism_id,
                PeakAssignment.provenance,
                PeakAssignment.role,
                PeakAssignment.owner_peak_assignment_id,
            )
            .outerjoin(
                PeakAssignment,
                PeakAssignment.peak_assignment_id
                == BatchPeakOccurrence.peak_assignment_id,
            )
            .where(BatchPeakOccurrence.batch_peak_id.in_(id_list))
        )
    ).all()

    # Second hop of the isotopologue link, resolved once for every member of this
    # recompute rather than once per batch peak.
    anchor_of_owner = await _owner_anchor_by_assignment(
        session,
        {
            r.owner_peak_assignment_id
            for r in rows
            if r.role == ROLE_ISO_CHILD and r.owner_peak_assignment_id
        },
    )

    members_by_peak: dict[str, list] = defaultdict(list)
    for r in rows:
        prov = r.provenance if isinstance(r.provenance, dict) else {}
        members_by_peak[r.batch_peak_id].append(
            {
                "assigned_formula": r.assigned_formula,
                "ion_formula": r.ion_formula,
                "ionization_mechanism_id": r.ionization_mechanism_id,
                "tier": r.tier,
                "fit_score": r.fit_score,
                "intensity": r.intensity,
                "p_correct": prov.get("p_correct"),
                "role": r.role,
                "owner_batch_peak_id": anchor_of_owner.get(r.owner_peak_assignment_id),
            }
        )

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
    for bp in anchors:
        members = members_by_peak.get(bp.batch_peak_id, [])
        if not members:
            await session.delete(bp)
            continue
        _apply_consensus(
            bp, compute_consensus(members, batch_peak_id=bp.batch_peak_id), now
        )


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

    **Scoped to those anchors, never to the whole batch.** Recomputing an anchor
    no fold just wrote to is not the harmless no-op it looks like. Deleting a run
    cascades to ``peak_assignment`` but leaves the occurrences standing with
    ``peak_assignment_id`` NULL (``ON DELETE SET NULL``), and the ion formula,
    the ionization mechanism and the family link live on the assignment rows
    alone. Recompute such an anchor and the outer join hands back nothing, so
    those columns are written to NULL for good - the occurrence cannot supply
    them again. An anchor a fold has just touched carries that fold's own live
    members, so ``_mode`` has something to skip the dead-linked ones in favour
    of, and the anchor keeps its columns.

    That is a reduction of the hazard, not its elimination, and the difference
    is worth stating exactly. ``_mode`` reads only the members backing the
    WINNING formula, and the winner is decided over every member carrying an
    ``assigned_formula`` - which the occurrence denormalizes, so a dead-linked
    member still votes. An anchor whose winner is backed only by dead-linked
    members therefore still loses its ion formula and mechanism, and
    ``resolve_isotopologue_of`` still counts dead members in the denominator of
    a majority only live ones can vote in. Both predate this scoping (the
    per-arrival fold recomputes touched anchors the same way, and so did the
    per-sample backfill before the deferral); scoping removes the exposure on
    anchors NO fold refreshed, which is the part this pass added.

    Note what does NOT produce a dead-linked member: the nightly retention pass
    keeps the newest completed run per sample and engine and refuses a quota
    below one, so it cannot leave a sample runless. It takes a run deleted
    outright - an import run abandoned, an external engine's whole quota evicted
    by the across-engine bound - and then a sample the backfill cannot re-fold,
    since re-folding replaces its occurrences with live links.

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
    for start in range(0, len(anchor_ids), _CONSENSUS_CHUNK):
        chunk = set(anchor_ids[start : start + _CONSENSUS_CHUNK])
        async with async_session() as session:
            await _acquire_batch_fold_lock(session, sample_batch_id)
            await _recompute_consensus(session, chunk, now)
            await session.commit()
    return len(anchor_ids)


#: Roughly how many points along a batch report progress, whatever its size.
#: Every sample reports up to this many; past it every stride-th sample does,
#: and the last one always, so the bar still fills. Integer division puts the
#: true worst case just under twice this (a 199-sample batch reports all 199),
#: which matters only in that the count stops growing with the batch.
#:
#: Bounded at all because the backfill's per-sample cost is not the assignment
#: loop's: a sample with no completed run returns from the fold after two
#: selects, so on the population this button exists for - a batch assigned
#: before batch peaks, most of it unassigned - a packet per sample is a burst of
#: socket traffic with no work between the packets. Each one is a Redis publish
#: plus a room-membership check, and re-runs every registered notification
#: watcher in every subscribed browser. A hundred steps is already finer than a
#: progress bar can draw.
_BACKFILL_PROGRESS_STEPS = 100


def _backfill_progress_notification(
    sample_batch_id: str,
    item_index: int,
    total_samples: int,
    user_id: int | None,
    process_id: str,
    parent_id: str | None,
) -> UserNotification:
    """The pending packet announcing that sample ``item_index`` is being folded.

    Typed as ``compute_batch_peaks`` -- the controller that owns the process,
    not the function that emits it -- so the whole run reports on one channel:
    the browser tracks a process by its id and ends its bar on the terminal
    packet the decorator sends under that same type.
    """
    return UserNotification(
        process_id=process_id,
        parent_id=parent_id,
        type="compute_batch_peaks",
        status="pending",
        message=f"Computing batch peaks, folding sample {item_index + 1}/{total_samples}.",
        data={
            "sample_batch_id": sample_batch_id,
            "_room_ids": [sample_batch_id],
            "_user_id": user_id,
            "_total_samples": total_samples,
            "_item_index": item_index,
        },
    )


async def backfill_sample_batch_peaks(
    sample_batch_id: str,
    user_id: int | None = None,
    process_id: str | None = None,
    parent_id: str | None = None,
) -> tuple[int, int]:
    """Fold every sample of a batch (that has a completed run) into the batch
    peaks, in acquisition-time order. Used to seed batch peaks for batches assigned
    before this feature.

    Returns the number of samples folded and the number whose fold raised. The
    two are counted apart because they mean opposite things to whoever asked:
    nothing folded because nothing was assigned is the batch's state, while
    nothing folded because every fold raised is a fault - and the count alone
    cannot tell them apart.

    Reports progress while it runs, on the same terms as a batch assignment: a
    reporting sample sends two pending packets, before and after its fold, so
    the bar steps from ``i/N`` to ``(i + 1)/N``. Every sample reports up to
    :data:`_BACKFILL_PROGRESS_STEPS`, past which the batch is sampled. The
    after-packet is sent for a sample whose fold raised as well -- the bar
    tracks how much of the batch has been dealt with, not how much of it
    succeeded, and stalling it on the one sample the run isolated is exactly the
    reading it must not invite. For the same reason the bar counts samples
    walked, not samples folded: it can fill while nothing was folded at all,
    which is the outcome the terminal notification exists to name.

    :param sample_batch_id: The batch to fold.
    :param user_id: Who to route the progress stream to alongside the batch's
        room; ``None`` sends to the room alone.
    :param process_id: The process the progress belongs to. Without one there is
        no bar to drive -- the browser tracks progress per process id, and a
        generated one would open a new bar per packet -- so the stream is
        skipped entirely rather than emitted anonymously.
    :param parent_id: The owning process, when this backfill is nested in one.
    :return: ``(folded, failed)`` sample counts.
    :rtype: tuple[int, int]
    """
    async with async_session() as session:
        sample_ids = (
            (
                await session.execute(
                    select(Sample.sample_item_id)
                    .where(Sample.sample_batch_id == sample_batch_id)
                    .order_by(Sample.datetime)
                )
            )
            .scalars()
            .all()
        )

    total_samples = len(sample_ids)
    stride = max(1, total_samples // _BACKFILL_PROGRESS_STEPS)

    folded = 0
    failed = 0
    # The anchors the deferred folds below report touching, recomputed once
    # each at the end instead of once per sample (see recompute_batch_consensus).
    touched: set[str] = set()
    # True only once the loop has walked the whole batch of its own accord. Still
    # False in the finally means we are unwinding, which is the one case where
    # the consensus pass must not replace the exception that got us there.
    walked_the_batch = False
    try:
        for item_index, sample_item_id in enumerate(sample_ids):
            # The last sample always reports, whatever the stride, so the bar ends
            # full rather than a stride short of it.
            reports = process_id is not None and (
                item_index % stride == 0 or item_index == total_samples - 1
            )
            notification = (
                _backfill_progress_notification(
                    sample_batch_id=sample_batch_id,
                    item_index=item_index,
                    total_samples=total_samples,
                    user_id=user_id,
                    process_id=process_id,
                    parent_id=parent_id,
                )
                if reports
                else None
            )
            if notification is not None:
                await send_progress_user_notification(notification)

            try:
                if (
                    await fold_sample_into_batch_peaks(
                        sample_item_id, defer_consensus_to=touched
                    )
                    is not None
                ):
                    folded += 1
            except IntegrityError:
                # Its own branch as a tripwire, not because the cause is known: the
                # fold-in lock plus the delete-then-flush of a re-fold should have
                # made the (batch peak, sample) unique key unreachable, so a
                # violation of THAT key means the serialization did not hold and is
                # worth chasing. The same branch also catches the foreign keys a
                # long backfill can lose under it -- a run pruned, a sample removed --
                # which are ordinary and unrelated. Hence a message that reports
                # which sample and leaves the diagnosis to the logged constraint.
                failed += 1
                runtime.logger.exception(
                    "Batch-peak backfill hit a database constraint on sample "
                    f"'{sample_item_id}'. A violated (batch peak, sample) unique key "
                    "means two folds of it overlapped, which the fold-in lock should "
                    "prevent; a foreign key means a run or sample it referenced went "
                    "away while the backfill ran."
                )
            except Exception:  # noqa: BLE001 - one bad sample must not abort backfill
                # Logged with the traceback: the exception's message alone is often
                # just a key or an index, and the caller only ever sees a count.
                failed += 1
                runtime.logger.exception(
                    f"Batch-peak backfill failed for sample '{sample_item_id}'."
                )

            # Outside the try, so the bar advances past a sample whose fold raised
            # as well as one that folded. `send_progress_user_notification` deep
            # copies, so this is the same object the pre-fold tick was built from.
            if notification is not None:
                await send_progress_user_notification(notification, 1.0)
        walked_the_batch = True
    finally:
        # In a finally because deferring the recompute is what makes this pass
        # load-bearing rather than a tidy-up: every fold above has COMMITTED its
        # occurrences while their anchors still describe the members they had
        # before, and nothing else will fix them - a later arriving fold
        # recomputes only the anchors ITS own sample touched. An anchor a
        # deferred fold MINTED and left unwritten is worse than stale: it keeps
        # its server defaults (tier 'unassigned', n_present 0, no formula), which
        # the ledger's default occupancy floor hides outright while the series
        # endpoint still draws it from the committed occurrences.
        #
        # The vector is cancellation, not the notification calls: both socket
        # layers swallow a Redis failure, but a CancelledError walks past both
        # `except` arms above (it is a BaseException) and out of the loop.
        #
        # In-process only, and deliberately NOT asyncio.shield-ed the way the run
        # finalizer shields its terminal write. Under a level-triggered
        # cancellation (anyio re-cancels a suspended child until its scope is
        # left) the first await here raises again and the pass does nothing;
        # shielding would fix that at the price of making a cancelled backfill
        # uninterruptible for up to one lock-taking transaction per chunk, on a
        # fold lock with no timeout. Neither is free, and the recovery is the
        # same either way and cheap: re-run the backfill. It is idempotent and
        # derives everything from committed occurrences. A SIGKILL, an OOM kill
        # or a worker killed past its stop grace reaches no finally at all.
        #
        # Scoping also retires an incidental sweep the whole-batch pass did: an
        # anchor left memberless by a SAMPLE deletion (its occurrences cascade,
        # the anchor does not) is no longer collected, because no fold's touched
        # set can name it. That is develop's behaviour too - the per-sample
        # recompute was touched-only - so nothing regresses here, but the sample
        # -delete path owns that cleanup and does not yet do it.
        try:
            await recompute_batch_consensus(sample_batch_id, touched)
        except Exception:
            if walked_the_batch:
                raise
            # Already unwinding. Raising here would REPLACE the exception that
            # interrupted the loop - a CancelledError above all, which has to
            # reach the caller as a cancellation and not as a database error.
            runtime.logger.exception(
                "Batch-peak backfill was interrupted and its deferred consensus "
                f"pass then failed for batch '{sample_batch_id}'. The anchors its "
                "committed folds touched still describe their previous members; "
                "re-running the backfill repairs them."
            )

    return folded, failed


def backfill_outcome(folded: int, failed: int, sample_batch_id: str) -> dict:
    """Report a backfill outcome, distinguishing "folded nothing" from success.

    Folding zero samples is what a batch with no completed assignment runs looks
    like, and it is the outcome the person who clicked most needs to hear: the
    request was accepted and did nothing. Announced green as "Computed batch
    peaks from 0 assigned sample(s)" it reads as done, which is how a user
    arrives back at an empty ledger with no idea why.

    Which of the four things happened is not a count's to say, so the failures
    are carried separately. Nothing folded and nothing raised is the batch's
    state and the message names it; nothing folded because every fold raised is
    a fault, and telling that user to "assign the batch first" would be advice
    that cannot help. A run that folded some and dropped others is neither, and
    saying only how many succeeded hides a sample missing from the ledger.

    ``partial`` is the outcome the notification layer maps to a warning
    severity. The word is a stretch for "none of it" -- but the run did finish,
    which rules out ``failed``, and the point of the status is the severity a
    reader sees.

    :param folded: How many of the batch's samples were folded in.
    :param failed: How many raised while folding and were skipped.
    :param sample_batch_id: The batch the backfill ran for.
    :return: The controller result the background-task decorator reports from.
    :rtype: dict
    """
    counts = {"samples_folded": folded, "samples_failed": failed}
    notification_data = {"sample_batch_id": sample_batch_id}

    if folded == 0 and failed == 0:
        return {
            "status": "partial",
            "message": (
                "No batch peaks were computed: none of this batch's samples "
                "has a completed assignment run yet. Assign the batch first."
            ),
            "data": counts,
            "_notification_data": notification_data,
        }
    if folded == 0:
        return {
            "status": "failed",
            "message": (
                f"No batch peaks were computed: all {failed} sample(s) failed "
                "to fold. See the server log for the reason."
            ),
            "data": counts,
            "_notification_data": notification_data,
        }
    if failed:
        return {
            "status": "partial",
            "message": (
                f"Computed batch peaks from {folded} assigned sample(s); "
                f"{failed} sample(s) failed to fold and are missing from the "
                "result. See the server log for the reason."
            ),
            "data": counts,
            "_notification_data": notification_data,
        }
    return {
        "status": "success",
        "message": f"Computed batch peaks from {folded} assigned sample(s).",
        "data": counts,
        "_notification_data": notification_data,
    }


@api_controller_background_task(
    success_notification_rooms=["sample_batch_id"],
    success_reload=[("peak_assignment", "sample_batch_id")],
    error_notification_rooms=["sample_batch_id"],
)
async def compute_batch_peaks(
    sample_batch_id: str,
    independent_transaction: bool = False,
    user_id: int | None = None,
    process_id: str | None = None,
    parent_id: str | None = None,
) -> dict:
    """Backfill a batch's batch peaks from its samples' existing completed
    assignment runs, without re-running assignment.

    This is how a batch assigned before batch peaks existed (or after a bulk
    import) gets populated into the batch overview. Idempotent -- re-running
    re-folds each sample. Emits ``peak_assignment_reload`` so the Assignments
    chart refreshes, including when nothing was folded: the ledger is then
    correct in being empty, and the notification says why.

    Folds sample by sample and reports as it goes, so the wait is a filling bar
    rather than a spinner: the per-sample packets are pending ones on this same
    ``compute_batch_peaks`` channel, and the decorator's terminal packet ends
    the bar.
    """
    folded, failed = await backfill_sample_batch_peaks(
        sample_batch_id,
        user_id=user_id,
        process_id=process_id,
        parent_id=parent_id,
    )
    return backfill_outcome(folded, failed, sample_batch_id)
