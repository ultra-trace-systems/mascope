"""Manual curation of a batch peak: pin the species claim for the whole batch.

The anchor-scoped counterpart of ``curation.py``. A per-sample override edits one
row of one run; with the batch ledger primary, the species is the anchor and the
sample rows are its members, so "use this" on a derived row acts on the anchor
(``docs/dev/peak_assignment_batch_primary.md`` section 6.3). Three parts, in
order:

- **Pin.** The chosen identity - one of the anchor's registry entries, so a
  formula some member of the batch actually carried - is written under
  ``provenance.manual`` on the anchor, with the displaced consensus archived as
  ``previous``. Every consensus recompute honours the pin
  (:func:`batch_peaks.compute_consensus` with ``manual=``): the anchor claims the
  pinned formula whatever the vote says, its tier rolled up over the members that
  carry it, and the vote's own winner is kept beside it so a disagreement stays
  visible.
- **Propagate.** The pinned identity is measured on every sample holding a
  member that does not already carry it, through the seeded chain the untargeted
  search propagates with: a member the ion's envelope reaches takes the identity
  with a fit of its own; one it does not reach keeps what it had. Nothing is
  inferred from another sample's spectrum. What each re-pointed member read
  before is archived on the pin (``displaced_members``), so release is an undo
  and not a re-vote over rewritten evidence.
- **Recompute**, once, so the batch ledger and every derived Sample view agree.

**Release** puts the archived member states back where nobody has changed them
since, drops the pin and recomputes the plain vote.

The claim is guarded as a verdict's is: the client names the consensus formula it
saw (``expected_formula``), and a mismatch is refused rather than a pin recorded
over a consensus the user never read.
"""

from __future__ import annotations

from datetime import datetime as dt
from datetime import timezone
from typing import Any, Optional

from fastapi import status
from sqlalchemy import select

from mascope_backend.api.lib.api_features import (
    api_controller,
    api_controller_background_task,
)
from mascope_backend.api.lib.exceptions.api_exceptions import (
    CodedHTTPException,
    NotFoundException,
)
from mascope_backend.api.new.peak_assignments.batch_peak_verification import (
    ClaimChangedException,
)
from mascope_backend.api.new.peak_assignments.batch_peaks import (
    ROLE_M0,
    manual_pin_of,
    member_state,
    resolve_candidate,
)
from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
    _acquire_batch_fold_lock,
    recompute_batch_consensus,
)
from mascope_backend.api.new.peak_assignments.batch_untargeted import (
    Annotation,
    _propagate_to_sample,
)
from mascope_backend.api.new.peak_assignments.config import PeakAssignmentConfig
from mascope_backend.db import BatchPeak, BatchPeakOccurrence, async_session
from mascope_backend.socket.notifications import (
    UserNotification,
    send_progress_user_notification,
)


#: The notification channel the curation reports on, start to finish.
NOTIFICATION_TYPE = "curate_batch_peak"

#: The one action an anchor knows: promote a registry entry to the claim.
ACTION_PROMOTE_IDENTITY = "promote_identity"

#: Machine-readable codes on the refusals a client has to react to.
UNKNOWN_CANDIDATE_CODE = "batch_peak_candidate_unknown"
NOT_CURATED_CODE = "batch_peak_not_curated"


class UnknownCandidateException(CodedHTTPException):
    """The registry index names no identity on this anchor (422)."""

    error_code = UNKNOWN_CANDIDATE_CODE

    def __init__(self, batch_peak_id: str, candidate: int):
        super().__init__(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"Batch peak '{batch_peak_id}' has no candidate {candidate}; the "
                "identities it can be assigned are the ones its members have carried"
            ),
        )


class NotCuratedException(CodedHTTPException):
    """A release on an anchor nobody pinned (409)."""

    error_code = NOT_CURATED_CODE

    def __init__(self, batch_peak_id: str):
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Batch peak '{batch_peak_id}' carries no manual curation to release",
        )


# --- pure parts -------------------------------------------------------------------


def registry_entry(anchor: Any, candidate: int) -> Optional[dict]:
    """The registry entry ``candidate`` names, or None when the index names
    nothing an anchor can be assigned (out of range, malformed, formula-less)."""
    registry = anchor.candidates if isinstance(anchor.candidates, list) else []
    if not isinstance(candidate, int) or not 0 <= candidate < len(registry):
        return None
    entry = registry[candidate]
    if not isinstance(entry, dict) or not entry.get("formula"):
        return None
    return entry


def manual_pin(anchor: Any, candidate: int, user_id: Optional[int], at: str) -> dict:
    """The pin a curated anchor carries under ``provenance.manual``.

    The identity is read off the registry, never from the client, and the
    consensus being displaced is archived whole - the row path's ``previous`` -
    so the note can say what the species read before and a release can be
    checked against it.
    """
    identity = resolve_candidate(anchor.candidates, candidate)
    return {
        "action": ACTION_PROMOTE_IDENTITY,
        "candidate": candidate,
        "formula": identity.get("formula"),
        "ion_formula": identity.get("ion_formula"),
        "ionization_mechanism_id": identity.get("ionization_mechanism_id"),
        "user_id": user_id,
        "at": at,
        "previous": {
            "consensus_formula": anchor.consensus_formula,
            "consensus_ion_formula": anchor.consensus_ion_formula,
            "ionization_mechanism_id": anchor.ionization_mechanism_id,
            "consensus_tier": anchor.consensus_tier,
            "best_fit_score": anchor.best_fit_score,
            "support_fraction": anchor.support_fraction,
            "is_ambiguous": bool(anchor.is_ambiguous),
        },
        "displaced_members": [],
    }


def restore_member(member: Any, state: dict) -> None:
    """Put an archived state back on a member."""
    member.candidate = state.get("candidate")
    member.tier = state.get("tier")
    member.fit_score = state.get("fit_score")
    member.role = state.get("role")
    member.owner_batch_peak_id = state.get("owner_batch_peak_id")
    member.p_correct = state.get("p_correct")


def curation_outcome(counts: dict, sample_batch_id: str) -> dict:
    """The task's closing message."""
    measured = counts["samples_measured"]
    repointed = counts["members_repointed"]
    message = (
        f"Pinned {counts['formula']} on the batch peak; measured it in {measured} "
        f"sample{'s' if measured != 1 else ''}, {repointed} now read"
        f"{'s' if repointed == 1 else ''} it."
    )
    return {
        "status": "success",
        "message": message,
        "results": repointed,
        "data": counts,
        "_notification_data": {"sample_batch_id": sample_batch_id},
    }


def _progress(
    sample_batch_id: str,
    item_index: int,
    total: int,
    message: str,
    user_id: Optional[int],
    process_id: Optional[str],
    parent_id: Optional[str],
) -> UserNotification:
    return UserNotification(
        process_id=process_id,
        parent_id=parent_id,
        type=NOTIFICATION_TYPE,
        status="pending",
        message=message,
        data={
            "sample_batch_id": sample_batch_id,
            "_room_ids": [sample_batch_id],
            "_user_id": user_id,
            "_total_samples": total,
            "_item_index": item_index,
        },
    )


# --- the writes -------------------------------------------------------------------


def _check(anchor: Any, sample_batch_id: str, batch_peak_id: str) -> None:
    if anchor is None or anchor.sample_batch_id != sample_batch_id:
        raise NotFoundException(f"Batch peak '{batch_peak_id}' not found in this batch")


def _check_claim(
    anchor: Any, batch_peak_id: str, candidate: int, expected_formula: Optional[str]
) -> dict:
    entry = registry_entry(anchor, candidate)
    if entry is None:
        raise UnknownCandidateException(batch_peak_id, candidate)
    if expected_formula is not None and expected_formula != anchor.consensus_formula:
        raise ClaimChangedException(
            batch_peak_id, expected_formula, anchor.consensus_formula or "unassigned"
        )
    return entry


@api_controller()
async def validate_curation(
    sample_batch_id: str,
    batch_peak_id: str,
    candidate: int,
    expected_formula: Optional[str] = None,
) -> dict:
    """The route's synchronous check before the task is launched, so a stale
    claim, an unknown candidate or a foreign anchor is refused on the request
    rather than reported through a notification.

    :return: The identity about to be pinned.
    """
    async with async_session() as session:
        anchor = await session.get(BatchPeak, batch_peak_id)
        _check(anchor, sample_batch_id, batch_peak_id)
        entry = _check_claim(anchor, batch_peak_id, candidate, expected_formula)
    return {"formula": entry.get("formula"), "mz": anchor.mz}


async def _pin(
    sample_batch_id: str,
    batch_peak_id: str,
    candidate: int,
    expected_formula: Optional[str],
    user_id: Optional[int],
) -> tuple[dict, list[str]]:
    """Write the pin under the fold lock, re-checking the claim: it may have moved
    between the route's check and this task starting.

    :return: The pin, and the samples holding a member not yet carrying it.
    """
    async with async_session() as session:
        await _acquire_batch_fold_lock(session, sample_batch_id)
        anchor = await session.get(BatchPeak, batch_peak_id)
        _check(anchor, sample_batch_id, batch_peak_id)
        _check_claim(anchor, batch_peak_id, candidate, expected_formula)
        now = dt.now(timezone.utc)
        pin = manual_pin(anchor, candidate, user_id, now.isoformat())
        provenance = (
            dict(anchor.provenance) if isinstance(anchor.provenance, dict) else {}
        )
        provenance["manual"] = pin
        anchor.provenance = provenance
        anchor.batch_peak_utc_modified = now
        samples = (
            (
                await session.execute(
                    select(BatchPeakOccurrence.sample_item_id)
                    .where(
                        BatchPeakOccurrence.batch_peak_id == batch_peak_id,
                        BatchPeakOccurrence.candidate.is_distinct_from(candidate),
                    )
                    .order_by(BatchPeakOccurrence.sample_item_id)
                )
            )
            .scalars()
            .all()
        )
        await session.commit()
    return pin, list(samples)


async def _archive_displaced(
    sample_batch_id: str, batch_peak_id: str, displaced: list[dict]
) -> None:
    """Record what the re-pointed members read before, on the pin."""
    if not displaced:
        return
    async with async_session() as session:
        await _acquire_batch_fold_lock(session, sample_batch_id)
        anchor = await session.get(BatchPeak, batch_peak_id)
        pin = manual_pin_of(anchor) if anchor is not None else None
        if pin is None:
            return
        provenance = dict(anchor.provenance)
        provenance["manual"] = {**pin, "displaced_members": displaced}
        anchor.provenance = provenance
        await session.commit()


async def run_batch_peak_curation(
    sample_batch_id: str,
    batch_peak_id: str,
    candidate: int,
    expected_formula: Optional[str],
    config: PeakAssignmentConfig,
    user_id: Optional[int] = None,
    process_id: Optional[str] = None,
    parent_id: Optional[str] = None,
) -> dict:
    """Pin, propagate, recompute - see the module docstring.

    :return: Counts: the formula pinned, samples measured, members re-pointed.
    """
    pin, samples = await _pin(
        sample_batch_id, batch_peak_id, candidate, expected_formula, user_id
    )
    counts = {
        "formula": pin["formula"],
        "samples_measured": 0,
        "members_repointed": 0,
    }
    annotation = Annotation(
        formula=pin["formula"],
        ion_formula=pin.get("ion_formula"),
        ionization_mechanism_id=pin.get("ionization_mechanism_id"),
        role=ROLE_M0,
        owner_batch_peak_id=None,
    )
    displaced: list[dict] = []
    total = len(samples)
    for step, sample_item_id in enumerate(samples):
        if process_id is not None:
            await send_progress_user_notification(
                _progress(
                    sample_batch_id,
                    step,
                    total,
                    f"Measuring {pin['formula']}, sample {step + 1}/{total}.",
                    user_id,
                    process_id,
                    parent_id,
                )
            )
        counts["members_repointed"] += await _propagate_to_sample(
            sample_batch_id,
            sample_item_id,
            {batch_peak_id: annotation},
            config,
            only_unassigned=False,
            skip_candidate={batch_peak_id: candidate},
            displaced=displaced,
        )
        counts["samples_measured"] += 1
    await _archive_displaced(sample_batch_id, batch_peak_id, displaced)
    await recompute_batch_consensus(sample_batch_id, {batch_peak_id})
    return counts


@api_controller_background_task(
    success_notification_rooms=["sample_batch_id"],
    success_reload=[("peak_assignment", "sample_batch_id")],
    error_notification_rooms=["sample_batch_id"],
)
async def curate_batch_peak(
    sample_batch_id: str,
    batch_peak_id: str,
    candidate: int,
    expected_formula: Optional[str] = None,
    config: PeakAssignmentConfig | None = None,
    independent_transaction: bool = False,
    user_id: int | None = None,
    process_id: str | None = None,
    parent_id: str | None = None,
) -> dict:
    """Pin one of a batch peak's identities as its species for the whole batch,
    measure it in every sample holding the peak, and recompute the anchor.
    Emits ``peak_assignment_reload`` so the ledgers refresh."""
    counts = await run_batch_peak_curation(
        sample_batch_id,
        batch_peak_id,
        candidate,
        expected_formula,
        config or PeakAssignmentConfig(),
        user_id=user_id,
        process_id=process_id,
        parent_id=parent_id,
    )
    return curation_outcome(counts, sample_batch_id)


@api_controller()
async def release_batch_peak_curation(sample_batch_id: str, batch_peak_id: str) -> dict:
    """Undo a manual curation: put the re-pointed members back where nobody has
    changed them since, drop the pin, recompute the plain vote.

    A member is restored only while it still carries the pinned identity - one a
    later fold or curation has moved on is somebody else's now and is left
    alone. Nothing is deleted: the identities stay in the registry, and the
    vote runs over what the members read.

    :return: Status envelope with the members restored and left alone.
    """
    async with async_session() as session:
        await _acquire_batch_fold_lock(session, sample_batch_id)
        anchor = await session.get(BatchPeak, batch_peak_id)
        _check(anchor, sample_batch_id, batch_peak_id)
        pin = manual_pin_of(anchor)
        if pin is None:
            raise NotCuratedException(batch_peak_id)
        archived = {
            state["sample_item_id"]: state
            for state in pin.get("displaced_members") or []
            if isinstance(state, dict) and state.get("sample_item_id")
        }
        restored = skipped = 0
        if archived:
            members = (
                (
                    await session.execute(
                        select(BatchPeakOccurrence).where(
                            BatchPeakOccurrence.batch_peak_id == batch_peak_id,
                            BatchPeakOccurrence.sample_item_id.in_(list(archived)),
                        )
                    )
                )
                .scalars()
                .all()
            )
            for member in members:
                if member.candidate == pin.get("candidate"):
                    restore_member(member, archived[member.sample_item_id])
                    restored += 1
                else:
                    skipped += 1
        provenance = dict(anchor.provenance)
        provenance.pop("manual", None)
        anchor.provenance = provenance
        anchor.batch_peak_utc_modified = dt.now(timezone.utc)
        formula = pin.get("formula")
        await session.commit()
    await recompute_batch_consensus(sample_batch_id, {batch_peak_id})
    return {
        "status": "success",
        "message": (
            f"Released the manual curation ({formula}) on the batch peak; "
            f"{restored} sample{'s' if restored != 1 else ''} put back, "
            f"{skipped} left as since changed."
        ),
        "results": restored,
        "data": [{"restored": restored, "skipped": skipped, "formula": formula}],
    }


__all__ = [
    "ACTION_PROMOTE_IDENTITY",
    "NOTIFICATION_TYPE",
    "NOT_CURATED_CODE",
    "UNKNOWN_CANDIDATE_CODE",
    "curate_batch_peak",
    "curation_outcome",
    "manual_pin",
    "member_state",
    "registry_entry",
    "release_batch_peak_curation",
    "restore_member",
    "run_batch_peak_curation",
    "validate_curation",
]
