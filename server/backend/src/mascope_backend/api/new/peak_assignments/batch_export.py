"""The batch ledger as flat rows: one per member, with its anchor's consensus.

Two consumers, one shape. The SDK reads the rows page by page
(``GET /api/batch-peaks/batch/{id}/members``) and hands them over as a
DataFrame; the *Export ledger* button writes the same rows to a CSV in the
user's temp store and the browser downloads it. Both are the whole ledger of a
batch - every batch peak with every sample's member - so a species table, a
per-sample view or any other cut is one ``groupby`` away, and nothing about the
ledger has to be re-derived outside the app.

A row carries the anchor's consensus (``batch_*`` and ``consensus_*``) beside the
member's own reading (``assigned_formula``, ``fit_score``, ``tier``, ...), so a
dissenting sample reads as one: the batch says one thing, the member another,
side by side.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

import pandas as pd
from sqlalchemy import func, select

from mascope_backend.api.controllers.sample.lib.sample_batches_fetch import (
    fetch_sample_batch,
)
from mascope_backend.api.lib.api_features import (
    api_controller,
    api_controller_background_task,
)
from mascope_backend.api.new.peak_assignments.batch_peaks import (
    ROLE_UNASSIGNED,
    manual_pin_of,
    mz_from_delta,
    resolve_candidate,
    role_name,
    tier_name,
)
from mascope_backend.api.new.peak_assignments.tiers import TIER_UNASSIGNED
from mascope_backend.api.new.temp.storage import download_name, user_temp_path
from mascope_backend.db import BatchPeak, BatchPeakOccurrence, SampleItem, async_session
from mascope_backend.runtime import runtime


#: The most rows one members page may carry; the SDK asks for exactly this.
MEMBER_PAGE_CAP = 5000

#: Rows read per statement when the whole ledger is written out.
_EXPORT_CHUNK = 20000

#: The notification channel the export reports on.
EXPORT_NOTIFICATION_TYPE = "export_batch_ledger"

#: The column order of a row, which is the CSV's header.
COLUMNS = (
    "sample_batch_id",
    "batch_peak_id",
    "batch_mz",
    "consensus_formula",
    "consensus_ion_formula",
    "consensus_ionization_mechanism_id",
    "consensus_tier",
    "support_fraction",
    "n_present",
    "is_ambiguous",
    "max_intensity",
    "isotopologue_of",
    "curated",
    "sample_item_id",
    "sample_item_name",
    "sample_peak_id",
    "mz",
    "intensity",
    "assigned_formula",
    "ion_formula",
    "ionization_mechanism_id",
    "source",
    "tier",
    "role",
    "fit_score",
    "p_correct",
    "owner_batch_peak_id",
)


def member_export_row(
    member: Any, anchor: Any, sample_item_name: Optional[str]
) -> dict:
    """One flat row: the anchor's consensus beside the member's own reading.

    :param member: The ``BatchPeakOccurrence``.
    :param anchor: The ``BatchPeak`` it sits in.
    :param sample_item_name: The member's sample, by name.
    """
    identity = resolve_candidate(anchor.candidates, member.candidate)
    return {
        "sample_batch_id": anchor.sample_batch_id,
        "batch_peak_id": anchor.batch_peak_id,
        "batch_mz": anchor.mz,
        "consensus_formula": anchor.consensus_formula,
        "consensus_ion_formula": anchor.consensus_ion_formula,
        "consensus_ionization_mechanism_id": anchor.ionization_mechanism_id,
        "consensus_tier": anchor.consensus_tier,
        "support_fraction": anchor.support_fraction,
        "n_present": anchor.n_present,
        "is_ambiguous": bool(anchor.is_ambiguous),
        "max_intensity": anchor.max_intensity,
        "isotopologue_of": anchor.isotopologue_of,
        "curated": manual_pin_of(anchor) is not None,
        "sample_item_id": member.sample_item_id,
        "sample_item_name": sample_item_name,
        "sample_peak_id": member.sample_peak_id,
        "mz": mz_from_delta(anchor.mz, member.mz_delta_ppm),
        "intensity": member.intensity,
        "assigned_formula": identity.get("formula"),
        "ion_formula": identity.get("ion_formula"),
        "ionization_mechanism_id": identity.get("ionization_mechanism_id"),
        "source": identity.get("source"),
        "tier": tier_name(member.tier) or TIER_UNASSIGNED,
        "role": role_name(member.role) or ROLE_UNASSIGNED,
        "fit_score": member.fit_score,
        "p_correct": member.p_correct,
        "owner_batch_peak_id": member.owner_batch_peak_id,
    }


def _members_query(sample_batch_id: str, sample_item_id: Optional[str]):
    """Members of a batch with their anchor and sample name, in anchor m/z
    order and then by sample - the order a reader expects a ledger in."""
    query = (
        select(BatchPeakOccurrence, BatchPeak, SampleItem.sample_item_name)
        .join(BatchPeak, BatchPeak.batch_peak_id == BatchPeakOccurrence.batch_peak_id)
        .join(
            SampleItem, SampleItem.sample_item_id == BatchPeakOccurrence.sample_item_id
        )
        .where(BatchPeak.sample_batch_id == sample_batch_id)
    )
    if sample_item_id:
        query = query.where(BatchPeakOccurrence.sample_item_id == sample_item_id)
    return query.order_by(
        BatchPeak.mz, BatchPeak.batch_peak_id, BatchPeakOccurrence.sample_item_id
    )


async def _member_count(
    session, sample_batch_id: str, sample_item_id: Optional[str]
) -> int:
    query = (
        select(func.count())
        .select_from(BatchPeakOccurrence)
        .join(BatchPeak, BatchPeak.batch_peak_id == BatchPeakOccurrence.batch_peak_id)
        .where(BatchPeak.sample_batch_id == sample_batch_id)
    )
    if sample_item_id:
        query = query.where(BatchPeakOccurrence.sample_item_id == sample_item_id)
    return (await session.execute(query)).scalar_one()


@api_controller()
async def get_batch_peak_members(
    sample_batch_id: str,
    sample_item_id: Optional[str] = None,
    limit: int = 1000,
    offset: int = 0,
) -> dict:
    """One page of a batch's ledger as flat member rows (:data:`COLUMNS`).

    Paged like the per-sample ledger: ``total`` says how many rows match, so a
    client pages until it has them all. ``sample_item_id`` narrows to one
    sample's members.

    :param sample_batch_id: The batch.
    :param sample_item_id: Only this sample's members.
    :param limit: Page size, at most :data:`MEMBER_PAGE_CAP`.
    :param offset: Rows to skip.
    :return: Status envelope with ``total``, ``limit``, ``offset`` and the rows.
    """
    limit = max(1, min(limit, MEMBER_PAGE_CAP))
    async with async_session() as session:
        total = await _member_count(session, sample_batch_id, sample_item_id)
        rows = (
            await session.execute(
                _members_query(sample_batch_id, sample_item_id)
                .offset(offset)
                .limit(limit)
            )
        ).all()
        data = [member_export_row(m, bp, name) for m, bp, name in rows]
    return {
        "status": "success",
        "message": (
            f"Retrieved {len(data)} of {total} batch ledger member"
            f"{'s' if total != 1 else ''}"
        ),
        "results": len(data),
        "total": total,
        "limit": limit,
        "offset": offset,
        "data": data,
    }


async def write_batch_ledger_csv(sample_batch_id: str, user_id: int) -> tuple[str, int]:
    """Write the whole ledger of a batch to a CSV in the user's temp store.

    Read in chunks rather than in one statement: a large batch's ledger is a
    hundred thousand rows or more, and the frame is built as it goes.

    :return: The file name written, and how many rows it holds.
    """
    sample_batch = await fetch_sample_batch(sample_batch_id)
    frames = []
    offset = 0
    async with async_session() as session:
        while True:
            rows = (
                await session.execute(
                    _members_query(sample_batch_id, None)
                    .offset(offset)
                    .limit(_EXPORT_CHUNK)
                )
            ).all()
            if not rows:
                break
            frames.append(
                pd.DataFrame(
                    [member_export_row(m, bp, name) for m, bp, name in rows],
                    columns=list(COLUMNS),
                )
            )
            offset += len(rows)
            if len(rows) < _EXPORT_CHUNK:
                break
    ledger = (
        pd.concat(frames, ignore_index=True)
        if frames
        else pd.DataFrame(columns=list(COLUMNS))
    )
    stamp = datetime.now().isoformat().replace("-", "").replace(":", "").split(".")[0]
    name = download_name(
        stamp, "batch_ledger", sample_batch.sample_batch_name, extension="csv"
    )
    runtime.logger.info(f"Writing the batch ledger ({len(ledger)} rows) to file {name}")
    ledger.to_csv(user_temp_path(user_id, name), index=False, sep=";")
    return name, len(ledger)


@api_controller_background_task(
    success_notification_rooms=["user_id"],
    error_notification_rooms=["user_id"],
)
async def export_batch_ledger(
    sample_batch_id: str,
    independent_transaction: bool = False,
    user_id: int | None = None,
    process_id: str | None = None,
    parent_id: str | None = None,
) -> dict:
    """Export a batch's ledger - every batch peak with every sample's member,
    one row per member - to a CSV the browser downloads when it is ready."""
    name, rows = await write_batch_ledger_csv(sample_batch_id, user_id)
    sample_batch = await fetch_sample_batch(sample_batch_id)
    message = (
        f"The batch ledger of '{sample_batch.sample_batch_name}' ({rows} member "
        f"row{'s' if rows != 1 else ''}) was exported to file '{name}'."
    )
    runtime.logger.info(message)
    return {
        "message": message,
        "data": {"filename": name, "rows": rows},
        "_notification_data": {"sample_batch_id": sample_batch_id, "download": name},
    }
