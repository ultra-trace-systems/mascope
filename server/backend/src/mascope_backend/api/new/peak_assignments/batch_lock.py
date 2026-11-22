"""The batch's fold lock: the one lock every writer of a batch's ledger takes first.

A fold, a consensus recompute, a stage's records and a batch run's bookkeeping
all take it before they write, so they write in one order and Postgres never
has to break a deadlock between two of them.
"""

from __future__ import annotations

from sqlalchemy import func, select


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
