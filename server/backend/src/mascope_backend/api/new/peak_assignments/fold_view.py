"""The Sample view of a sample the batch ledger knows but no run describes.

A sample's peaks can be in the batch ledger without a per-sample run behind
them: its runs deleted outright or pruned after the fold, or - once ingest
folds a sample without writing a run (``docs/dev/peak_assignment_batch_primary.md``)
- never written at all. Each member row carries formula, tier, fit, intensity,
P(correct), role and family link, and names its ion formula and mechanism in
its anchor's candidate registry. That is enough to serve the Sample view, and
this module does so in the shape the run-backed reads use, so the ledger table,
the spectrum colouring, the inspector and the SDK need no second code path:

- the runs listing carries one synthetic, always-completed run per folded
  sample, engine :data:`FOLD_ENGINE`, listed after the real runs so a client
  that takes the first completed run still gets a real one where one exists;
- the ledger and detail reads answer for that run's id, and for its rows' ids,
  which name the anchor the member sits in (``fold-<batch_peak_id>``);
- a verdict can be recorded against a derived row - the verification is keyed
  on the peak, and the member carries the identity and score to snapshot;
- curation cannot: there is no row to edit, and the caller is told so with a
  409 rather than a 404 that would read as a stale id.

Derived rows are a lossy view of a run: no mass or abundance error, no isotope
label, no source, no target ids, no evidence. Those are per-sample numbers a
run computes and a member does not carry; the design note leaves them to an
on-demand re-score.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from fastapi import status
from sqlalchemy import func, select

from mascope_backend.api.lib.exceptions.api_exceptions import CodedHTTPException
from mascope_backend.api.new.peak_assignments.batch_peaks import resolve_candidate
from mascope_backend.api.new.peak_assignments.config import (
    FOLD_ENGINE,
    PEAK_ASSIGNMENT_ENGINE_VERSION,
)
from mascope_backend.api.new.peak_assignments.engine import ROLE_UNASSIGNED
from mascope_backend.api.new.peak_assignments.tiers import TIER_UNASSIGNED
from mascope_backend.db import BatchPeak, BatchPeakOccurrence


#: Prefix of every identifier this module mints. A stored id never carries it:
#: ``gen_id`` draws from letters and digits only, so the hyphen alone tells a
#: derived id from a row's or a run's.
FOLD_ID_PREFIX = "fold-"

#: Error code of the 409 a write against a derived row answers with.
DERIVED_READ_ONLY_CODE = "derived_assignment_read_only"


# --- identifiers --------------------------------------------------------------


def fold_run_id(sample_item_id: str) -> str:
    """The id of the derived run of ``sample_item_id``: one per sample, stable."""
    return f"{FOLD_ID_PREFIX}{sample_item_id}"


def fold_assignment_id(batch_peak_id: str) -> str:
    """The id of a derived row: the anchor the member sits in. Unique within a
    sample because an anchor has at most one member per sample, and the routes
    that take it also take the sample."""
    return f"{FOLD_ID_PREFIX}{batch_peak_id}"


def is_fold_id(value: Optional[str]) -> bool:
    """Whether ``value`` is an id this module minted (a run's or a row's)."""
    return bool(value) and value.startswith(FOLD_ID_PREFIX)


def fold_id_target(value: str) -> str:
    """The sample or anchor id a derived id names."""
    return value[len(FOLD_ID_PREFIX) :]


class DerivedAssignmentReadOnlyException(CodedHTTPException):
    """A write named a derived row (409).

    There is no ``peak_assignment`` row behind a derived id, so the edit has
    nothing to land on. A 409 rather than a 404: the id is not stale, the sample
    simply has no run, and the remedy is to assign it.

    :param peak_assignment_id: The derived id that was addressed.
    :param sample_item_name: The sample, for the message.
    :param action: What was attempted, as a past participle ("curated").
    """

    error_code = DERIVED_READ_ONLY_CODE

    def __init__(self, peak_assignment_id: str, sample_item_name: str, action: str):
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Assignment '{peak_assignment_id}' of sample '{sample_item_name}' "
                "is derived from the batch ledger rather than a row of an "
                f"assignment run, so it cannot be {action}. Assign the sample to "
                "get a ledger of its own."
            ),
        )


# --- the row shape ------------------------------------------------------------


def member_row(member: Any, anchor: Any) -> dict:
    """One derived ledger row, in the shape of ``schemas.PeakAssignmentRecord``.

    Everything a member carries is passed through; everything only a run
    computes is ``None``. A member written before it carried a role or a tier
    reads as unassigned, which is what a member with no assignment is.

    :param member: The sample's ``BatchPeakOccurrence`` (or anything carrying
        its attributes).
    :param anchor: The ``BatchPeak`` the member sits in.
    :return: The row, ready to validate as a ledger record.
    """
    identity = resolve_candidate(anchor.candidates, member.candidate)
    owner = member.owner_batch_peak_id
    return {
        "peak_assignment_id": fold_assignment_id(member.batch_peak_id),
        "peak_assignment_run_id": fold_run_id(member.sample_item_id),
        "sample_item_id": member.sample_item_id,
        "sample_peak_id": member.sample_peak_id,
        "sample_peak_mz": member.sample_peak_mz,
        "sample_peak_intensity": (
            member.intensity if member.intensity is not None else 0.0
        ),
        "sample_peak_tof": None,
        "role": member.role or ROLE_UNASSIGNED,
        "assigned_formula": member.assigned_formula,
        "ion_formula": identity.get("ion_formula"),
        "ionization_mechanism_id": identity.get("ionization_mechanism_id"),
        "isotope_label": None,
        "isotope_formula": None,
        "source": None,
        "fit_score": member.fit_score,
        "mz_error_ppm": None,
        "abundance_error": None,
        "tier": member.tier or TIER_UNASSIGNED,
        "engine_tier": None,
        "target_compound_id": None,
        "target_ion_id": None,
        "owner_peak_assignment_id": fold_assignment_id(owner) if owner else None,
        "evidence": None,
        "p_correct": member.p_correct,
        "p_correct_provisional": None,
        "corroboration_adducts": None,
        "batch_peak_id": member.batch_peak_id,
    }


def member_detail(member: Any, anchor: Any) -> dict:
    """The derived row plus the inspector detail, from the anchor.

    The alternatives are the other identities the anchor's members have carried
    - what the batch saw this m/z as elsewhere - each with the evidence share
    the consensus gave that formula when it has one. The provenance is the
    anchor's consensus record with the anchor's own summary under
    ``batch_peak``, and this member's P(correct) in place of the anchor's.

    :param member: The sample's ``BatchPeakOccurrence``.
    :param anchor: The ``BatchPeak`` the member sits in.
    :return: The full row, ready to validate as a detail record.
    """
    row = member_row(member, anchor)
    shares = {
        entry.get("formula"): entry
        for entry in anchor.alternatives or []
        if isinstance(entry, dict)
    }
    alternatives = []
    for index, entry in enumerate(anchor.candidates or []):
        if index == member.candidate or not isinstance(entry, dict):
            continue
        share = shares.get(entry.get("formula"), {})
        alternatives.append(
            {
                "assigned_formula": entry.get("formula"),
                "ion_formula": entry.get("ion_formula"),
                "ionization_mechanism_id": entry.get("ionization_mechanism_id"),
                "source": FOLD_ENGINE,
                "evidence_share": share.get("evidence_share"),
                "n_members": share.get("n"),
            }
        )
    provenance = anchor.provenance if isinstance(anchor.provenance, dict) else {}
    row["alternatives"] = alternatives
    row["provenance"] = {
        **provenance,
        "batch_peak": {
            "batch_peak_id": anchor.batch_peak_id,
            "mz": anchor.mz,
            "consensus_formula": anchor.consensus_formula,
            "consensus_ion_formula": anchor.consensus_ion_formula,
            "consensus_tier": anchor.consensus_tier,
            "support_fraction": anchor.support_fraction,
            "n_present": anchor.n_present,
            "is_ambiguous": bool(anchor.is_ambiguous),
            "best_fit_score": anchor.best_fit_score,
        },
        "p_correct": member.p_correct,
    }
    return row


@dataclass
class VerificationTarget:
    """What the verification write reads off the judged assignment, for a
    derived row: the peak identity and the score to snapshot, and no row-level
    links - as for a verdict whose run was later pruned."""

    sample_item_id: str
    sample_peak_id: str
    assigned_formula: Optional[str]
    ionization_mechanism_id: Optional[str]
    fit_score: Optional[float]
    provenance: dict = field(default_factory=dict)
    peak_assignment_id: Optional[str] = None
    peak_assignment_run_id: Optional[str] = None


def verification_target(member: Any, anchor: Any) -> VerificationTarget:
    """The verification snapshot of a derived row.

    :param member: The sample's ``BatchPeakOccurrence``.
    :param anchor: The ``BatchPeak`` the member sits in.
    """
    identity = resolve_candidate(anchor.candidates, member.candidate)
    return VerificationTarget(
        sample_item_id=member.sample_item_id,
        sample_peak_id=member.sample_peak_id,
        assigned_formula=member.assigned_formula,
        ionization_mechanism_id=identity.get("ionization_mechanism_id"),
        fit_score=member.fit_score,
        provenance={"p_correct": member.p_correct, "evidence": None},
    )


# --- reads --------------------------------------------------------------------


def _members_of(sample_item_id: str):
    return (
        select(BatchPeakOccurrence, BatchPeak)
        .join(BatchPeak, BatchPeak.batch_peak_id == BatchPeakOccurrence.batch_peak_id)
        .where(BatchPeakOccurrence.sample_item_id == sample_item_id)
    )


async def member_count(session, sample_item_id: str) -> int:
    """How many anchors of its batch the sample is a member of."""
    return (
        await session.execute(
            select(func.count())
            .select_from(BatchPeakOccurrence)
            .where(BatchPeakOccurrence.sample_item_id == sample_item_id)
        )
    ).scalar_one()


async def fold_run_record(session, sample: Any) -> Optional[dict]:
    """The derived run of ``sample``, or ``None`` when the batch ledger does not
    know it.

    Shaped as ``schemas.PeakAssignmentRunRecord``. It carries no creation time,
    so a client ordering completed runs by it sorts this one last - the same
    place the runs listing puts it - and a completion time that is the last
    write to any anchor the sample sits in, the nearest thing to when its
    ledger was last derived.

    :param session: An open session.
    :param sample: The sample view row.
    """
    members = await member_count(session, sample.sample_item_id)
    if not members:
        return None
    last_written = (
        await session.execute(
            select(func.max(BatchPeak.batch_peak_utc_modified))
            .join(
                BatchPeakOccurrence,
                BatchPeakOccurrence.batch_peak_id == BatchPeak.batch_peak_id,
            )
            .where(BatchPeakOccurrence.sample_item_id == sample.sample_item_id)
        )
    ).scalar_one()
    return {
        "peak_assignment_run_id": fold_run_id(sample.sample_item_id),
        "sample_item_id": sample.sample_item_id,
        "engine": FOLD_ENGINE,
        "engine_version": PEAK_ASSIGNMENT_ENGINE_VERSION,
        "status": "completed",
        "config": {
            "derived_from": "batch_peaks",
            "sample_batch_id": sample.sample_batch_id,
            "ionization_mode_id": sample.ionization_mode_id,
            "n_members": members,
        },
        "tier_bands": None,
        "calibration": None,
        "confidence_calibration": None,
        "error": None,
        "peak_assignment_run_utc_created": None,
        "peak_assignment_run_utc_completed": last_written,
    }


async def fold_member(session, sample_item_id: str, batch_peak_id: str):
    """The sample's member of ``batch_peak_id`` with its anchor, or ``None``."""
    row = (
        await session.execute(
            _members_of(sample_item_id).where(
                BatchPeakOccurrence.batch_peak_id == batch_peak_id
            )
        )
    ).first()
    return (row[0], row[1]) if row is not None else None


def _plain(value: Any) -> Any:
    """A filter value as the column stores it: an enum member's value, else itself."""
    return getattr(value, "value", value)


async def derived_ledger(
    session,
    sample: Any,
    *,
    tier: Any = None,
    engine_tier: Any = None,
    tier_disagrees: Optional[bool] = None,
    role: Any = None,
    source: Any = None,
    limit: int,
    offset: int,
) -> Optional[dict]:
    """The sample's ledger read off the batch peaks, as the list route answers it.

    ``None`` when the batch ledger does not know the sample at all - the caller
    then answers as it always has for a sample with no run. Filters on what a
    member does not carry (an engine's own tier, a source) match nothing rather
    than everything: absence is not a match.

    :param session: An open session.
    :param sample: The sample view row.
    :param limit: Page size.
    :param offset: Rows to skip.
    :return: The response envelope, with ``total`` for paging, or ``None``.
    """
    if not await member_count(session, sample.sample_item_id):
        return None

    if engine_tier is not None or tier_disagrees is not None or source is not None:
        total, rows = 0, []
    else:
        conditions = []
        if tier:
            conditions.append(
                func.coalesce(BatchPeakOccurrence.tier, TIER_UNASSIGNED) == _plain(tier)
            )
        if role:
            conditions.append(
                func.coalesce(BatchPeakOccurrence.role, ROLE_UNASSIGNED) == _plain(role)
            )
        total = (
            await session.execute(
                select(func.count())
                .select_from(BatchPeakOccurrence)
                .where(
                    BatchPeakOccurrence.sample_item_id == sample.sample_item_id,
                    *conditions,
                )
            )
        ).scalar_one()
        # Ordered by m/z with the anchor id as a tiebreak, as the run-backed
        # read orders by m/z and row id: two peaks can share an m/z.
        members = (
            await session.execute(
                _members_of(sample.sample_item_id)
                .where(*conditions)
                .order_by(
                    BatchPeakOccurrence.sample_peak_mz,
                    BatchPeakOccurrence.batch_peak_id,
                )
                .offset(offset)
                .limit(limit)
            )
        ).all()
        rows = [member_row(member, anchor) for member, anchor in members]

    return {
        "status": "success",
        "message": (
            f"Retrieved {len(rows)} of {total} batch-derived assignment"
            f"{'s' if total != 1 else ''} for sample '{sample.sample_item_name}'"
        ),
        "results": len(rows),
        "total": total,
        "data": rows,
    }
