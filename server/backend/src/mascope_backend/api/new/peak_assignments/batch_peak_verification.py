"""Verdicts on batch peaks: one judgment per species at an anchor, batch-wide.

The anchor-scoped counterpart of the per-sample verification in ``service.py``
(``docs/dev/peak_assignment_continuity.md`` section 4,
``docs/dev/peak_assignment_batch_primary.md`` section 6.3). A verdict here judges the
**claim** a batch peak makes - its consensus formula and ionization mechanism - and
covers every sample in the batch whose peak folded into that anchor and that carries
no verdict of its own. Per-sample verdicts always win; that is what makes them the
exceptions.

Three properties the writes keep:

- **Claim-pinned.** The claim is snapshotted from the anchor at write time, never
  taken from the client. If the consensus later flips from F to G the verdict stays
  live about F and reads as *stale* (``stale`` on every record served), because a
  machine recompute never supersedes a human label. The client sends the formula it
  judged (``expected_formula``) and a mismatch is refused: unlike a per-sample
  identity, an anchor's claim can change under *another sample's* fold between the
  user reading the row and the request landing.
- **Append-only, current row marked.** Recording a verdict stamps the live row on the
  same claim ``superseded_utc`` in the same transaction and inserts the new one, under
  an advisory lock per anchor. A *retract* is that stamp with no successor - the exit
  a verdict that annotates up to ``n_present`` rows needs.
- **Outside the calibration pool.** These rows live in their own table and the pool
  selects from ``assignment_verification`` alone; the model docstring says why.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime as dt
from datetime import timezone
from typing import Any, Optional

from fastapi import status
from sqlalchemy import func, select, update

from mascope_backend.api.lib.api_features import api_controller
from mascope_backend.api.lib.exceptions.api_exceptions import (
    CodedHTTPException,
    NotFoundException,
)
from mascope_backend.db import (
    BatchPeak,
    BatchPeakOccurrence,
    BatchPeakVerification,
    async_session,
)
from mascope_backend.db.id import gen_id


#: Advisory-lock namespace for the supersede-then-insert; one lock per anchor.
_WRITE_LOCK_NAMESPACE = "mascope_batch_peak_verification_write"

#: Machine-readable codes on the two refusals a client has to react to.
UNASSIGNED_ANCHOR_CODE = "batch_peak_unassigned"
CLAIM_CHANGED_CODE = "batch_peak_claim_changed"


class UnassignedAnchorException(CodedHTTPException):
    """The anchor makes no species claim, so there is nothing to judge (422)."""

    error_code = UNASSIGNED_ANCHOR_CODE

    def __init__(self, batch_peak_id: str):
        super().__init__(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"Batch peak '{batch_peak_id}' has no consensus formula, so there is "
                "no species claim to verify"
            ),
        )


class ClaimChangedException(CodedHTTPException):
    """The client judged a formula the anchor no longer claims (409)."""

    error_code = CLAIM_CHANGED_CODE

    def __init__(self, batch_peak_id: str, expected: str, current: str):
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Batch peak '{batch_peak_id}' now reads {current}, not {expected}; "
                "the consensus changed since the row was read - judge it again"
            ),
        )


@dataclass(frozen=True)
class AnchorClaim:
    """What a batch peak claims right now - or nothing, when the anchor is gone."""

    present: bool
    formula: Optional[str] = None
    ionization_mechanism_id: Optional[str] = None
    mz: Optional[float] = None


def claim_of(anchor: Any) -> AnchorClaim:
    """The anchor's present claim; an absent anchor claims nothing.

    :param anchor: A ``BatchPeak``, or None when the anchor has been deleted.
    """
    if anchor is None:
        return AnchorClaim(present=False)
    return AnchorClaim(
        present=True,
        formula=anchor.consensus_formula,
        ionization_mechanism_id=anchor.ionization_mechanism_id,
        mz=anchor.mz,
    )


def context_snapshot(anchor: Any) -> dict:
    """What the human saw when judging: the anchor's consensus at that moment.

    Pinned on the record because the consensus keeps moving under later folds,
    and a verdict is only interpretable against what it was a verdict *on*.
    """
    return {
        "mz": anchor.mz,
        "consensus_formula": anchor.consensus_formula,
        "consensus_ion_formula": anchor.consensus_ion_formula,
        "ionization_mechanism_id": anchor.ionization_mechanism_id,
        "consensus_tier": anchor.consensus_tier,
        "best_fit_score": anchor.best_fit_score,
        "support_fraction": anchor.support_fraction,
        "n_present": anchor.n_present,
        "is_ambiguous": bool(anchor.is_ambiguous),
    }


def is_stale(record: dict, claim: AnchorClaim) -> bool:
    """A live verdict about a claim the anchor no longer makes, or whose anchor is
    gone. Superseded rows are history and never stale."""
    if record.get("superseded_utc") is not None:
        return False
    if not claim.present:
        return True
    return (record["assigned_formula"], record["ionization_mechanism_id"]) != (
        claim.formula,
        claim.ionization_mechanism_id,
    )


def with_current_claim(record: dict, claim: AnchorClaim) -> dict:
    """The record plus the anchor's present claim, so a reader sees staleness
    without a second fetch."""
    return {
        **record,
        "current_formula": claim.formula,
        "current_ionization_mechanism_id": claim.ionization_mechanism_id,
        "anchor_present": claim.present,
        "stale": is_stale(record, claim),
    }


async def _lock_anchor(session, batch_peak_id: str) -> None:
    """Serialize the writes on one anchor for the rest of the transaction."""
    await session.execute(
        select(
            func.pg_advisory_xact_lock(
                func.hashtext(_WRITE_LOCK_NAMESPACE), func.hashtext(batch_peak_id)
            )
        )
    )


@api_controller()
async def verify_batch_peak(
    sample_batch_id: str,
    batch_peak_id: str,
    verdict: str,
    expected_formula: Optional[str] = None,
    evidence_level: Optional[str] = None,
    note: Optional[str] = None,
    user_id: Optional[int] = None,
) -> dict:
    """Record a verdict on a batch peak's species claim.

    The anchor must belong to the batch (404 otherwise) and make a claim - an
    unassigned anchor has nothing to judge (422). The claim is read off the anchor
    here, and ``expected_formula`` - the formula the client judged - has to be that
    claim (409 otherwise): the guard against a consensus that moved between the row
    being read and this request landing. The live verdict on the same claim, if
    any, is stamped superseded in the same transaction.

    :param sample_batch_id: The batch the anchor belongs to.
    :param batch_peak_id: The anchor being judged.
    :param verdict: confirmed | rejected | unsure.
    :param expected_formula: The consensus formula the user judged; the schema
        requires it for 'confirmed' and 'rejected'.
    :param evidence_level: Basis for the verdict; the schema requires it for
        'confirmed'.
    :param note: Optional free-text note.
    :param user_id: The judging user (attribution).
    :return: Status envelope with the created record, current claim attached.
    """
    async with async_session() as session:
        anchor = await session.get(BatchPeak, batch_peak_id)
        if anchor is None or anchor.sample_batch_id != sample_batch_id:
            raise NotFoundException(
                f"Batch peak '{batch_peak_id}' not found in this batch"
            )
        if anchor.consensus_formula is None:
            raise UnassignedAnchorException(batch_peak_id)
        if (
            expected_formula is not None
            and expected_formula != anchor.consensus_formula
        ):
            raise ClaimChangedException(
                batch_peak_id, expected_formula, anchor.consensus_formula
            )
        claim = claim_of(anchor)
        context = context_snapshot(anchor)
        await _lock_anchor(session, batch_peak_id)
        now = dt.now(timezone.utc)
        await session.execute(
            update(BatchPeakVerification)
            .where(
                BatchPeakVerification.batch_peak_id == batch_peak_id,
                BatchPeakVerification.assigned_formula == claim.formula,
                BatchPeakVerification.ionization_mechanism_id.is_not_distinct_from(
                    claim.ionization_mechanism_id
                ),
                BatchPeakVerification.superseded_utc.is_(None),
            )
            .values(superseded_utc=now)
        )
        verification = BatchPeakVerification(
            batch_peak_verification_id=gen_id(32),
            sample_batch_id=sample_batch_id,
            batch_peak_id=batch_peak_id,
            assigned_formula=claim.formula,
            ionization_mechanism_id=claim.ionization_mechanism_id,
            verdict=verdict,
            evidence_level=evidence_level,
            note=note,
            context=context,
            verified_by=user_id,
            verified_utc=now,
        )
        session.add(verification)
        await session.commit()
        await session.refresh(verification)
        record = with_current_claim(verification.to_dict(), claim)
    return {
        "status": "success",
        "message": (
            f"Recorded '{verdict}' for {claim.formula} at m/z {claim.mz:.4f} "
            "across the batch"
        ),
        "results": 1,
        "data": [record],
    }


@api_controller()
async def retract_batch_peak_verdict(
    sample_batch_id: str,
    batch_peak_id: str,
    assigned_formula: Optional[str] = None,
    ionization_mechanism_id: Optional[str] = None,
) -> dict:
    """Withdraw the live verdict(s) on a batch peak: the supersede stamp with no
    successor, returning the species to unverified in every sample it covered.

    Scoped by the batch on the verdict rows themselves rather than through the
    anchor, so a verdict whose anchor a re-fold has since deleted can still be
    withdrawn. With a claim given, only that claim's live verdict goes; without
    one, every live verdict on the anchor does, stale ones included. Nothing live
    is not an error - the answer is zero.

    :param sample_batch_id: The batch the anchor belongs to.
    :param batch_peak_id: The anchor whose verdict(s) to retract.
    :param assigned_formula: Retract only the live verdict on this claim.
    :param ionization_mechanism_id: The claim's mechanism, with the formula.
    :return: Status envelope with the ids of the verdicts retracted.
    """
    conditions = [
        BatchPeakVerification.sample_batch_id == sample_batch_id,
        BatchPeakVerification.batch_peak_id == batch_peak_id,
        BatchPeakVerification.superseded_utc.is_(None),
    ]
    if assigned_formula is not None:
        conditions.append(BatchPeakVerification.assigned_formula == assigned_formula)
        conditions.append(
            BatchPeakVerification.ionization_mechanism_id.is_not_distinct_from(
                ionization_mechanism_id
            )
        )
    async with async_session() as session:
        await _lock_anchor(session, batch_peak_id)
        result = await session.execute(
            update(BatchPeakVerification)
            .where(*conditions)
            .values(superseded_utc=dt.now(timezone.utc))
            .returning(BatchPeakVerification.batch_peak_verification_id)
        )
        retracted = list(result.scalars().all())
        await session.commit()
    n = len(retracted)
    return {
        "status": "success",
        "message": (
            f"Retracted {n} verdict{'s' if n != 1 else ''} on batch peak "
            f"'{batch_peak_id}'"
            if n
            else f"Batch peak '{batch_peak_id}' has no live verdict to retract"
        ),
        "results": n,
        "data": [{"batch_peak_verification_id": i} for i in retracted],
    }


@api_controller()
async def get_batch_peak_verdicts(sample_batch_id: str) -> dict:
    """Every verdict recorded on the batch's anchors, newest first, superseded ones
    included so the history stays inspectable.

    Each row carries the anchor's present claim and a ``stale`` flag: a live
    verdict about a formula the consensus has since left, or whose anchor a
    re-fold deleted, is served rather than hidden - a machine recompute never
    supersedes a human label - and the UI says so.

    :param sample_batch_id: The batch whose verdicts to list.
    :return: Status envelope with the records.
    """
    async with async_session() as session:
        rows = (
            await session.execute(
                select(BatchPeakVerification, BatchPeak)
                .outerjoin(
                    BatchPeak,
                    BatchPeak.batch_peak_id == BatchPeakVerification.batch_peak_id,
                )
                .where(BatchPeakVerification.sample_batch_id == sample_batch_id)
                .order_by(
                    BatchPeakVerification.verified_utc.desc(),
                    BatchPeakVerification.batch_peak_verification_id,
                )
            )
        ).all()
        data = [with_current_claim(v.to_dict(), claim_of(bp)) for v, bp in rows]
    return {
        "status": "success",
        "message": (
            f"Retrieved {len(data)} batch-level verdict{'s' if len(data) != 1 else ''}"
        ),
        "results": len(data),
        "data": data,
    }


@api_controller()
async def get_anchor_context(sample_item_id: str) -> dict:
    """The batch-level verdicts that reach a sample.

    For each of the sample's member peaks whose anchor carries a live verdict, that
    verdict with the peak's id on it. Sparse - a peak whose anchor nobody judged
    has no row - and one indexed query, so the per-sample ledger and the inspector
    share it. Whether a verdict *applies* to a row is the reader's call: it does
    iff the row's own claim (formula, mechanism) is the one judged, which keeps a
    dissenting sample out from under a verdict about another formula.

    :param sample_item_id: The sample whose peaks to look up.
    :return: Status envelope with the records, ``sample_peak_id`` on each.
    """
    async with async_session() as session:
        rows = (
            await session.execute(
                select(
                    BatchPeakOccurrence.sample_peak_id, BatchPeakVerification, BatchPeak
                )
                .join(
                    BatchPeakVerification,
                    BatchPeakVerification.batch_peak_id
                    == BatchPeakOccurrence.batch_peak_id,
                )
                .join(
                    BatchPeak,
                    BatchPeak.batch_peak_id == BatchPeakOccurrence.batch_peak_id,
                )
                .where(
                    BatchPeakOccurrence.sample_item_id == sample_item_id,
                    BatchPeakVerification.superseded_utc.is_(None),
                )
                .order_by(
                    BatchPeakOccurrence.sample_peak_id,
                    BatchPeakVerification.verified_utc.desc(),
                )
            )
        ).all()
        data = [
            {"sample_peak_id": peak_id, **with_current_claim(v.to_dict(), claim_of(bp))}
            for peak_id, v, bp in rows
        ]
    return {
        "status": "success",
        "message": (
            f"Retrieved {len(data)} batch-level verdict"
            f"{'s' if len(data) != 1 else ''} reaching the sample"
        ),
        "results": len(data),
        "data": data,
    }
