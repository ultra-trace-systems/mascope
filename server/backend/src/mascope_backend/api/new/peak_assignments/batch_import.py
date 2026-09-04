"""Import an external engine's batch-level result into the batch ledger, as a
batch run.

An engine that works on a batch as a whole - a pipeline that assigns a
representative subset of files and merges their ledgers on m/z, say - ends with
one identity per m/z: a neutral formula, its ion and the adduct it rode in on.
This module lands that table on the batch ledger the way the untargeted search
lands its own findings (``batch_untargeted``): each row is matched to the batch
peak nearest its m/z within a tolerance, and the identity is then MEASURED
against every member of that anchor with the seeded scorer, so a member carries
this server's fit of the engine's formula rather than the engine's score. The
engine's own numbers are provenance the client keeps; the ledger column means
what it means for every other source, and the registry entry names the engine.

What the import does, and does not do, deliberately:

- It re-points members the in-app engine had assigned. The import is the
  engine's view of the batch, and the run that carries it snapshotted the
  ledger as it was (``batch_runs``), so nothing is lost: the run selector shows
  both, and a rebuild puts the in-app view back.
- It leaves a curated anchor alone. A human pin outranks every engine.
- It leaves an isotopologue anchor alone. The engine's rows are M0s, and a
  row landing on a peak this ledger reads as another peak's isotopologue is a
  disagreement about the peak's role, which is not an identity to write.
- A row whose adduct resolved to no mechanism id cannot be measured and is
  skipped: the mechanism is what the seeded scorer builds the ion from.
- Two rows nearest the same anchor: the closer one takes it.

Every skipped row is counted by reason in the run's summary, so an import that
landed fewer identities than it sent says why. The matching is pure and
tested offline; ``run_batch_import`` does the network and the writes.
"""

from __future__ import annotations

import bisect
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from sqlalchemy import select

from mascope_backend.api.lib.api_features import api_controller_background_task
from mascope_backend.api.new.peak_assignments.batch_peaks import ROLE_M0, manual_pin_of
from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
    recompute_batch_consensus,
)
from mascope_backend.api.new.peak_assignments.batch_runs import complete_run, fail_run
from mascope_backend.api.new.peak_assignments.batch_untargeted import (
    Annotation,
    _propagate_to_sample,
)
from mascope_backend.api.new.peak_assignments.config import PeakAssignmentConfig
from mascope_backend.api.new.peak_assignments.import_service import (
    UnprocessableImportException,
)
from mascope_backend.api.new.peak_assignments.import_validation import (
    json_size_error,
    normalize_engine,
)
from mascope_backend.db import (
    BatchPeak,
    BatchPeakOccurrence,
    IonizationMechanism,
    IonizationMode,
    async_session,
)
from mascope_backend.socket.notifications import (
    UserNotification,
    send_progress_user_notification,
)


NOTIFICATION_TYPE = "import_batch_run"

#: Rows one import may carry. A batch-level ledger is one row per species, so
#: this is generous for a batch and small enough that the body stays well under
#: the proxy's cap without the chunking the per-sample import needs.
MAX_BATCH_IMPORT_ROWS = 5000

#: How far, in ppm, a row's m/z may sit from the batch peak it lands on. The
#: engine's m/z is its own reading of the same peaks, offset-corrected by the
#: engine; the anchor's is this ledger's frozen m/z. A few ppm covers both.
DEFAULT_MZ_TOLERANCE_PPM = 5.0

# Why a row landed nothing, as the summary counts them.
REASON_NO_MECHANISM = "no_mechanism"
REASON_NO_ANCHOR = "no_anchor_within_tolerance"
REASON_COLLISION = "closer_row_took_the_anchor"
REASON_CURATED = "anchor_curated"
REASON_ISOTOPOLOGUE = "anchor_is_isotopologue"


@dataclass(frozen=True)
class AnchorRef:
    """What the matcher needs of an anchor: where it is and which polarity its
    samples were measured in (None when the anchor carries no ionization mode,
    in which case it accepts a row of either polarity)."""

    batch_peak_id: str
    mz: float
    polarity: Optional[str]


def match_rows_to_anchors(
    rows: Sequence[Any],
    anchors: Sequence[AnchorRef],
    tolerance_ppm: float,
    mechanism_polarity: dict[str, str],
) -> tuple[dict[str, int], dict[int, str]]:
    """Match each row to the batch peak nearest its m/z, within the tolerance
    and of the polarity its mechanism implies.

    Pure. ``rows`` need ``mz`` and ``ionization_mechanism_id``; a row without
    a mechanism is not matched at all, since it could not be measured. When
    two rows are nearest the same anchor the closer one takes it.

    :param rows: The import's rows, in request order.
    :param anchors: The batch's anchors.
    :param tolerance_ppm: The widest m/z distance that still lands.
    :param mechanism_polarity: Mechanism id -> polarity (``+``/``-``).
    :return: ``(anchor id -> row index, row index -> reason)`` for the rows
        that landed and the ones that did not.
    """
    grouped: dict[Optional[str], list[tuple[float, str]]] = defaultdict(list)
    for anchor in anchors:
        grouped[anchor.polarity].append((anchor.mz, anchor.batch_peak_id))
    lanes: dict[Optional[str], tuple[list[float], list[str]]] = {}
    for polarity, pairs in grouped.items():
        pairs.sort()
        lanes[polarity] = ([mz for mz, _ in pairs], [aid for _, aid in pairs])

    best: dict[str, tuple[float, int]] = {}
    unmatched: dict[int, str] = {}
    for index, row in enumerate(rows):
        mechanism = row.ionization_mechanism_id
        if not mechanism:
            unmatched[index] = REASON_NO_MECHANISM
            continue
        polarity = mechanism_polarity.get(mechanism)
        nearest: Optional[tuple[float, str]] = None
        # The row's own polarity lane, and the anchors that have no mode at all.
        for lane_polarity in dict.fromkeys((polarity, None)):
            lane = lanes.get(lane_polarity)
            if lane is None:
                continue
            mzs, ids = lane
            position = bisect.bisect_left(mzs, row.mz)
            for candidate in (position - 1, position):
                if not 0 <= candidate < len(mzs):
                    continue
                ppm = abs(mzs[candidate] - row.mz) / mzs[candidate] * 1e6
                if ppm <= tolerance_ppm and (nearest is None or ppm < nearest[0]):
                    nearest = (ppm, ids[candidate])
        if nearest is None:
            unmatched[index] = REASON_NO_ANCHOR
            continue
        ppm, anchor_id = nearest
        held = best.get(anchor_id)
        if held is None or ppm < held[0]:
            if held is not None:
                unmatched[held[1]] = REASON_COLLISION
            best[anchor_id] = (ppm, index)
        else:
            unmatched[index] = REASON_COLLISION
    return {anchor_id: index for anchor_id, (_, index) in best.items()}, unmatched


def _names(values, limit: int = 5) -> str:
    shown = ", ".join(f"'{value}'" for value in list(values)[:limit])
    rest = len(values) - limit
    return f"{shown} and {rest} more" if rest > 0 else shown


async def validate_batch_import(body: Any) -> tuple[str, dict[str, str]]:
    """Refuse what the import cannot honour, before a run is opened.

    :param body: The request body (``ImportBatchRunBody``).
    :return: The normalized engine name and mechanism id -> polarity for
        every mechanism the rows name.
    :raises UnprocessableImportException: On a reserved engine name, an
        oversized config, or a mechanism this deployment does not have.
    """
    engine, error = normalize_engine(body.engine)
    if error:
        raise UnprocessableImportException(error)
    if body.config is not None and (
        size_error := json_size_error("config", body.config)
    ):
        raise UnprocessableImportException(size_error)
    supplied = {
        row.ionization_mechanism_id for row in body.rows if row.ionization_mechanism_id
    }
    polarity: dict[str, str] = {}
    if supplied:
        async with async_session() as session:
            found = (
                await session.execute(
                    select(
                        IonizationMechanism.ionization_mechanism_id,
                        IonizationMechanism.ionization_mechanism_polarity,
                    ).where(IonizationMechanism.ionization_mechanism_id.in_(supplied))
                )
            ).all()
        polarity = {mechanism_id: pol for mechanism_id, pol in found}
        if unknown := sorted(supplied - set(polarity)):
            raise UnprocessableImportException(
                f"ionization_mechanism_id {_names(unknown)} does not exist on this "
                "deployment; leave it null when the adduct cannot be resolved "
                "(the row is then counted as unmeasurable rather than refused)"
            )
    return engine, polarity


async def _batch_anchors(
    sample_batch_id: str,
) -> tuple[list[BatchPeak], dict[str, Optional[str]]]:
    """The batch's anchors and, for each ionization mode they name, its polarity."""
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
        mode_ids = {a.ionization_mode_id for a in anchors if a.ionization_mode_id}
        polarity_by_mode: dict[str, Optional[str]] = {}
        if mode_ids:
            polarity_by_mode = dict(
                (
                    await session.execute(
                        select(
                            IonizationMode.ionization_mode_id,
                            IonizationMode.ionization_mode_polarity,
                        ).where(IonizationMode.ionization_mode_id.in_(mode_ids))
                    )
                ).all()
            )
    return list(anchors), polarity_by_mode


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


async def run_batch_import(
    sample_batch_id: str,
    engine: str,
    tolerance_ppm: float,
    rows: Sequence[Any],
    mechanism_polarity: dict[str, str],
    user_id: Optional[int] = None,
    process_id: Optional[str] = None,
    parent_id: Optional[str] = None,
) -> dict:
    """Land the engine's rows on the batch ledger: match, measure, recompute.

    :param sample_batch_id: The batch.
    :param engine: The engine's normalized name; recorded as the source of the
        registry entries this import creates.
    :param tolerance_ppm: The m/z match tolerance.
    :param rows: The validated rows (``BatchImportRow``).
    :param mechanism_polarity: Mechanism id -> polarity, from validation.
    :return: The counts the run's summary records.
    """
    anchors, polarity_by_mode = await _batch_anchors(sample_batch_id)
    refs = [
        AnchorRef(a.batch_peak_id, a.mz, polarity_by_mode.get(a.ionization_mode_id))
        for a in anchors
    ]
    matched, unmatched = match_rows_to_anchors(
        rows, refs, tolerance_ppm, mechanism_polarity
    )
    skipped: Counter = Counter(unmatched.values())
    by_id = {anchor.batch_peak_id: anchor for anchor in anchors}
    annotations: dict[str, Annotation] = {}
    for anchor_id, index in matched.items():
        anchor = by_id[anchor_id]
        if manual_pin_of(anchor) is not None:
            skipped[REASON_CURATED] += 1
            continue
        if anchor.isotopologue_of:
            skipped[REASON_ISOTOPOLOGUE] += 1
            continue
        row = rows[index]
        annotations[anchor_id] = Annotation(
            formula=row.formula,
            ion_formula=row.ion_formula,
            ionization_mechanism_id=row.ionization_mechanism_id,
            role=ROLE_M0,
            owner_batch_peak_id=None,
        )
    counts: dict[str, Any] = {
        "rows": len(rows),
        "anchors": len(anchors),
        "anchors_matched": len(annotations),
        "members_measured": 0,
        "samples_rescored": 0,
        "rows_skipped": sum(skipped.values()),
        "rows_skipped_by_reason": dict(sorted(skipped.items())),
        "mz_tolerance_ppm": tolerance_ppm,
    }
    if not annotations:
        return counts

    async with async_session() as session:
        sample_ids = sorted(
            (
                await session.execute(
                    select(BatchPeakOccurrence.sample_item_id)
                    .where(BatchPeakOccurrence.batch_peak_id.in_(list(annotations)))
                    .distinct()
                )
            )
            .scalars()
            .all()
        )
    # The ledger's own bands tier what the measurement finds, as the search's do.
    config = PeakAssignmentConfig()
    reports = process_id is not None
    total = len(sample_ids)
    for step, sample_item_id in enumerate(sample_ids):
        if reports:
            await send_progress_user_notification(
                _progress(
                    sample_batch_id,
                    step,
                    total,
                    f"Measuring the imported compositions, sample {step + 1}/{total}.",
                    user_id,
                    process_id,
                    parent_id,
                )
            )
        counts["members_measured"] += await _propagate_to_sample(
            sample_batch_id,
            sample_item_id,
            annotations,
            config,
            only_unassigned=False,
            source=engine,
        )
        counts["samples_rescored"] += 1
    await recompute_batch_consensus(sample_batch_id, set(annotations))
    return counts


def import_outcome(counts: dict, sample_batch_id: str, engine: str) -> dict:
    """The controller result the background-task decorator reports from."""
    notification_data = {"sample_batch_id": sample_batch_id}
    skipped = counts.get("rows_skipped_by_reason") or {}
    skipped_text = (
        " Skipped: "
        + ", ".join(f"{n} {reason.replace('_', ' ')}" for reason, n in skipped.items())
        + "."
        if skipped
        else ""
    )
    if not counts["anchors_matched"]:
        return {
            "status": "partial",
            "message": (
                f"No row of the {engine} import landed on a batch peak within "
                f"{counts['mz_tolerance_ppm']} ppm.{skipped_text}"
            ),
            "data": counts,
            "_notification_data": notification_data,
        }
    return {
        "status": "success",
        "message": (
            f"Imported {engine}: {counts['anchors_matched']} of {counts['rows']} row"
            f"{'s' if counts['rows'] != 1 else ''} landed on a batch peak, and "
            f"{counts['members_measured']} member peak"
            f"{'s' if counts['members_measured'] != 1 else ''} across "
            f"{counts['samples_rescored']} sample"
            f"{'s' if counts['samples_rescored'] != 1 else ''} measured against "
            f"them.{skipped_text}"
        ),
        "data": counts,
        "_notification_data": notification_data,
    }


async def perform_batch_import(
    sample_batch_id: str,
    batch_peak_run_id: str,
    engine: str,
    tolerance_ppm: float,
    rows: Sequence[Any],
    mechanism_polarity: dict[str, str],
    user_id: Optional[int] = None,
    process_id: Optional[str] = None,
    parent_id: Optional[str] = None,
) -> dict:
    """The import as the batch run the route opened: closed as completed with
    the counts, or as failed with the error, the live ledger then holding
    whatever the import wrote before it stopped."""
    try:
        counts = await run_batch_import(
            sample_batch_id,
            engine,
            tolerance_ppm,
            rows,
            mechanism_polarity,
            user_id=user_id,
            process_id=process_id,
            parent_id=parent_id,
        )
    except Exception as exc:
        await fail_run(
            sample_batch_id, batch_peak_run_id, f"{type(exc).__name__}: {exc}"
        )
        raise
    await complete_run(sample_batch_id, batch_peak_run_id, summary=counts)
    return import_outcome(counts, sample_batch_id, engine)


@api_controller_background_task(
    success_notification_rooms=["sample_batch_id"],
    success_reload=[("peak_assignment", "sample_batch_id")],
    error_notification_rooms=["sample_batch_id"],
)
async def import_batch_run(
    sample_batch_id: str,
    batch_peak_run_id: str,
    engine: str,
    tolerance_ppm: float,
    rows: Sequence[Any],
    mechanism_polarity: dict[str, str],
    independent_transaction: bool = False,
    user_id: Optional[int] = None,
    process_id: Optional[str] = None,
    parent_id: Optional[str] = None,
) -> dict:
    """Land an external engine's batch-level rows on the batch ledger as the
    run the route opened. Emits ``peak_assignment_reload`` on completion so the
    ledgers and the run selector refresh."""
    return await perform_batch_import(
        sample_batch_id,
        batch_peak_run_id,
        engine,
        tolerance_ppm,
        rows,
        mechanism_polarity,
        user_id=user_id,
        process_id=process_id,
        parent_id=parent_id,
    )
