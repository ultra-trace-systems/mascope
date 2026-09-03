"""Stage B once per anchor: the untargeted search over a batch's unassigned peaks.

The untargeted composition search is the expensive stage of assignment, and a
per-sample run pays for it once per sample - which is why it is off for batches.
In the batch ledger a species is one anchor whatever the sample count, so the
search can run once per anchor instead: on the anchor's **brightest member's
real spectrum** (never a synthetic consensus one), enumerating compositions for
that one peak while the isotope pattern is scored against the sample's whole
peak list. On a 32-sample batch that is about nineteen times fewer enumerations
than a per-sample pass, and the factor grows with the batch.

What the search decides for an anchor is then **propagated** to the anchor's
other members: each sample that holds one is measured against the winning
formula through the seeded chain the copy service uses (one peak read, one
match pass per sample), so every member carries a fit of its own and the
sample's derived view agrees with the batch. A member whose peak the seeded
envelope does not reach stays unassigned rather than inheriting a number.

Nothing here writes a per-sample run: results live on the members and, through
the consensus, on the anchors. An explicit run on a sample still supersedes
them for that sample. Design: ``docs/dev/peak_assignment_batch_primary.md``,
section 5.3.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from sqlalchemy import select

from mascope_backend.api.controllers.samples.lib.samples_fetch import fetch_sample
from mascope_backend.api.lib.api_features import api_controller_background_task
from mascope_backend.api.new.cheminfo.utils import (
    to_custom_element_format,
    to_explicit_isotope_format,
)
from mascope_backend.api.new.match.params import default_match_params
from mascope_backend.api.new.peak_assignments.batch_peaks import (
    ROLE_ISO_CHILD,
    candidate_index,
    member_state,
    role_code,
    tier_code,
)
from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
    _acquire_batch_fold_lock,
    recompute_batch_consensus,
)
from mascope_backend.api.new.peak_assignments.config import PeakAssignmentConfig
from mascope_backend.api.new.peak_assignments.engine import (
    SOURCE_UNTARGETED,
    evidence_for,
    tier_for_evidence,
    untargeted_matches_to_peak_assignments,
)
from mascope_backend.api.new.peak_assignments.fold_view import fold_run_id
from mascope_backend.api.new.peak_assignments.seeded_scoring import score_seeds
from mascope_backend.api.new.peak_assignments.service import (
    _untargeted_ionization_notations,
    fetch_sample_mechanisms,
    load_sample_peaks,
)
from mascope_backend.api.new.peak_assignments.tiers import TIER_UNASSIGNED
from mascope_backend.db import BatchPeak, BatchPeakOccurrence, async_session
from mascope_backend.runtime import runtime
from mascope_backend.socket.notifications import (
    UserNotification,
    send_progress_user_notification,
)
from mascope_tools.composition import CompositionSearchConfig, HeuristicFilterConfig
from mascope_tools.composition.finder import assign_compositions


#: The notification channel the search reports on, start to finish.
NOTIFICATION_TYPE = "search_batch_untargeted"


# --- pure helpers ---------------------------------------------------------------


def choose_representatives(members: Iterable[Any]) -> dict[str, Any]:
    """The brightest member of each anchor: the real spectrum its search runs on.

    A member with no intensity counts as the dimmest, not as a candidate.

    :param members: Occurrence rows (or anything carrying ``batch_peak_id``,
        ``sample_item_id``, ``sample_peak_id`` and ``intensity``).
    :return: anchor id -> the member chosen for it.
    """
    best: dict[str, Any] = {}
    for member in members:
        current = best.get(member.batch_peak_id)
        if current is None or (member.intensity or 0.0) > (current.intensity or 0.0):
            best[member.batch_peak_id] = member
    return best


def group_by_sample(representatives: dict[str, Any]) -> dict[str, list[Any]]:
    """The representatives by the sample they are in: one search per sample,
    over every anchor that sample represents."""
    by_sample: dict[str, list[Any]] = defaultdict(list)
    for member in representatives.values():
        by_sample[member.sample_item_id].append(member)
    return dict(by_sample)


def owner_anchor_of(
    row: dict, rows_by_id: dict[str, dict], members_by_peak: dict[str, Any]
) -> Optional[str]:
    """The anchor an isotopologue row's owner peak sits in, in the same sample.

    The search names an owner by the assignment id it minted for the owner's
    row; that row names the owner's peak, and the peak's member names its
    anchor. Any missing link means no owner: the isotopologue then stands on
    its own rather than pointing at a peak the batch does not hold.
    """
    if row.get("role") != ROLE_ISO_CHILD or not row.get("owner_peak_assignment_id"):
        return None
    owner_row = rows_by_id.get(row["owner_peak_assignment_id"])
    if owner_row is None:
        return None
    owner_member = members_by_peak.get(owner_row["sample_peak_id"])
    return owner_member.batch_peak_id if owner_member is not None else None


def search_config(config: PeakAssignmentConfig, notations: list[str]):
    """The finder's configuration for this search - the orchestrator's, verbatim."""
    formula_ranges, _ = to_explicit_isotope_format(config.formula_ranges)
    return CompositionSearchConfig(
        ionizations=",".join(notations),
        mass_range_ppm=config.mz_precision_ppm,
        element_count_ranges=formula_ranges,
        use_unsaturation=True,
        min_unsaturation=-1000.0,
        max_unsaturation=10000.0,
    )


@dataclass
class Annotation:
    """What the search decided for one anchor: the identity its representative
    won, the role it plays and, for an isotopologue, the anchor of its owner."""

    formula: str
    ion_formula: Optional[str]
    ionization_mechanism_id: Optional[str]
    role: str
    owner_batch_peak_id: Optional[str]


# --- the search ---------------------------------------------------------------------


async def _unassigned_anchors_and_members(
    session, sample_batch_id: str
) -> tuple[dict[str, BatchPeak], list[BatchPeakOccurrence]]:
    """The batch's anchors that carry no assignment, with every member of theirs."""
    anchors = (
        (
            await session.execute(
                select(BatchPeak).where(
                    BatchPeak.sample_batch_id == sample_batch_id,
                    BatchPeak.consensus_tier == TIER_UNASSIGNED,
                )
            )
        )
        .scalars()
        .all()
    )
    members = (
        (
            await session.execute(
                select(BatchPeakOccurrence)
                .join(
                    BatchPeak,
                    BatchPeak.batch_peak_id == BatchPeakOccurrence.batch_peak_id,
                )
                .where(
                    BatchPeak.sample_batch_id == sample_batch_id,
                    BatchPeak.consensus_tier == TIER_UNASSIGNED,
                )
            )
        )
        .scalars()
        .all()
    )
    return {anchor.batch_peak_id: anchor for anchor in anchors}, list(members)


async def _search_sample(
    sample_item_id: str, target_peak_ids: set[str], config: PeakAssignmentConfig
) -> list[dict]:
    """Stage B over one sample's representative peaks, in the context of its
    whole spectrum. Returns the engine's assignment rows for the peaks it
    explained - the representatives and any isotopologue peaks it paired to
    them - shaped as ledger rows that will never be written."""
    sample = await fetch_sample(sample_item_id)
    peaks_df = load_sample_peaks(sample)
    frame = (
        peaks_df[
            (peaks_df["intensity"] > 0)
            & (peaks_df["intensity"] >= config.peak_intensity_threshold)
        ]
        .sort_values("mz")
        .reset_index(drop=True)
    )
    targets = frame[frame["sample_peak_id"].isin(target_peak_ids)].nlargest(
        config.max_untargeted_peaks, "intensity"
    )
    if targets.empty:
        return []
    _, mechanisms = await fetch_sample_mechanisms(sample)
    notations, mechanism_id_by_notation = _untargeted_ionization_notations(mechanisms)
    if not notations:
        runtime.logger.info(
            f"Untargeted batch search skips sample '{sample.sample_item_name}': "
            "no polarity-compatible ionization mechanisms."
        )
        return []
    # The whole spectrum is the frame - isotope patterns are scored against it -
    # while only the representatives are enumerated.
    matches_df, _ = await asyncio.to_thread(
        assign_compositions,
        frame[["mz", "intensity"]],
        search_config(config, notations),
        HeuristicFilterConfig(use_senior=True),
        targets=targets["mz"].tolist(),
    )
    return untargeted_matches_to_peak_assignments(
        matches_df,
        peaks_df=frame,
        sample_item_id=sample_item_id,
        peak_assignment_run_id=fold_run_id(sample_item_id),
        candidate_threshold=config.candidate_threshold,
        assigned_threshold=config.assigned_threshold,
        mechanism_id_by_notation=mechanism_id_by_notation,
        formula_formatter=to_custom_element_format,
        max_alternatives=config.max_alternatives,
    )


async def _apply_search_rows(
    sample_batch_id: str,
    sample_item_id: str,
    rows: list[dict],
    anchor_ids: set[str],
) -> dict[str, Annotation]:
    """Write what the search decided onto the sample's members and their
    anchors' registries, under the batch's fold lock.

    Only members of the anchors being searched are touched: a row the search
    produced for a peak that already had an assignment (an isotopologue it
    paired to a Stage-A peak, say) is left alone.

    :return: anchor id -> what it was annotated with.
    """
    annotations: dict[str, Annotation] = {}
    if not rows:
        return annotations
    rows_by_id = {row["peak_assignment_id"]: row for row in rows}
    async with async_session() as session:
        await _acquire_batch_fold_lock(session, sample_batch_id)
        members = (
            (
                await session.execute(
                    select(BatchPeakOccurrence).where(
                        BatchPeakOccurrence.sample_item_id == sample_item_id,
                        BatchPeakOccurrence.batch_peak_id.in_(list(anchor_ids)),
                    )
                )
            )
            .scalars()
            .all()
        )
        members_by_peak = {member.sample_peak_id: member for member in members}
        anchors = {
            anchor.batch_peak_id: anchor
            for anchor in (
                await session.execute(
                    select(BatchPeak).where(
                        BatchPeak.batch_peak_id.in_(
                            [member.batch_peak_id for member in members]
                        )
                    )
                )
            )
            .scalars()
            .all()
        }
        for row in rows:
            member = members_by_peak.get(row["sample_peak_id"])
            if member is None or not row.get("assigned_formula"):
                continue
            anchor = anchors[member.batch_peak_id]
            registry = list(anchor.candidates or [])
            index = candidate_index(
                registry,
                row["assigned_formula"],
                row.get("ion_formula"),
                row.get("ionization_mechanism_id"),
                source=SOURCE_UNTARGETED,
            )
            if len(registry) != len(anchor.candidates or []):
                anchor.candidates = registry
            owner = owner_anchor_of(row, rows_by_id, members_by_peak)
            member.candidate = index
            member.tier = tier_code(row["tier"])
            member.fit_score = row.get("fit_score")
            member.role = role_code(row["role"])
            member.owner_batch_peak_id = owner
            member.p_correct = None  # the untargeted stage is uncalibrated
            annotations[member.batch_peak_id] = Annotation(
                formula=row["assigned_formula"],
                ion_formula=row.get("ion_formula"),
                ionization_mechanism_id=row.get("ionization_mechanism_id"),
                role=row["role"],
                owner_batch_peak_id=owner,
            )
        await session.commit()
    return annotations


async def _propagate_to_sample(
    sample_batch_id: str,
    sample_item_id: str,
    annotations: dict[str, Annotation],
    config: PeakAssignmentConfig,
    *,
    only_unassigned: bool = True,
    source: str = SOURCE_UNTARGETED,
    skip_candidate: Optional[dict[str, int]] = None,
    displaced: Optional[list] = None,
) -> int:
    """Measure the annotated anchors' formulas against one other sample and
    write the result onto its members of those anchors.

    The seeded chain scores each (formula, mechanism) as an ion against the
    sample's own peaks. A member whose peak the ion's envelope paired to takes
    the ion's fit, tiered under the search's own bands; a member the envelope
    did not reach stays unassigned. Runs outside the fold lock, writes inside.

    The batch curation measures a pinned identity the same way, over every
    member rather than the unassigned ones (``only_unassigned=False``),
    skipping members that already carry it (``skip_candidate``, anchor id ->
    registry index) and archiving what each re-pointed member read before
    (``displaced``, appended to). ``source`` is recorded on a registry entry
    this write creates.

    :return: How many members were assigned.
    """
    seeds = {
        (a.formula, a.ionization_mechanism_id)
        for a in annotations.values()
        if a.formula and a.ionization_mechanism_id
    }
    if not seeds:
        return 0
    sample = await fetch_sample(sample_item_id)
    match_params = await default_match_params(sample_item_id)
    ion_by_seed, fit_by_ion, errors_by_pairing, _ = await score_seeds(
        sample, seeds, match_params
    )
    if not ion_by_seed:
        return 0

    conditions = [
        BatchPeakOccurrence.sample_item_id == sample_item_id,
        BatchPeakOccurrence.batch_peak_id.in_(list(annotations)),
    ]
    if only_unassigned:
        conditions.append(BatchPeakOccurrence.candidate.is_(None))
    assigned = 0
    async with async_session() as session:
        await _acquire_batch_fold_lock(session, sample_batch_id)
        members = (
            (await session.execute(select(BatchPeakOccurrence).where(*conditions)))
            .scalars()
            .all()
        )
        if not members:
            return 0
        anchors = {
            anchor.batch_peak_id: anchor
            for anchor in (
                await session.execute(
                    select(BatchPeak).where(
                        BatchPeak.batch_peak_id.in_([m.batch_peak_id for m in members])
                    )
                )
            )
            .scalars()
            .all()
        }
        for member in members:
            annotation = annotations[member.batch_peak_id]
            if (
                skip_candidate
                and skip_candidate.get(member.batch_peak_id) == member.candidate
            ):
                continue
            ion_id = ion_by_seed.get(
                (annotation.formula, annotation.ionization_mechanism_id)
            )
            if (
                ion_id is None
                or (ion_id, member.sample_peak_id) not in errors_by_pairing
            ):
                continue
            fit = fit_by_ion.get(ion_id)
            evidence = evidence_for(fit, annotation.formula)
            if fit is None or evidence is None:
                continue
            if displaced is not None:
                displaced.append(member_state(member))
            anchor = anchors[member.batch_peak_id]
            registry = list(anchor.candidates or [])
            index = candidate_index(
                registry,
                annotation.formula,
                annotation.ion_formula,
                annotation.ionization_mechanism_id,
                source=source,
            )
            if len(registry) != len(anchor.candidates or []):
                anchor.candidates = registry
            member.candidate = index
            member.tier = tier_code(
                tier_for_evidence(
                    evidence,
                    candidate_threshold=config.candidate_threshold,
                    assigned_threshold=config.assigned_threshold,
                )
            )
            member.fit_score = fit
            member.role = role_code(annotation.role)
            member.owner_batch_peak_id = annotation.owner_batch_peak_id
            member.p_correct = None
            assigned += 1
        await session.commit()
    return assigned


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


async def run_batch_untargeted_search(
    sample_batch_id: str,
    config: PeakAssignmentConfig,
    user_id: Optional[int] = None,
    process_id: Optional[str] = None,
    parent_id: Optional[str] = None,
) -> dict:
    """Search every unassigned anchor of a batch once, then propagate.

    Two passes, one progress bar: the search over each sample that holds a
    representative, then the seeded re-score over each sample that holds a
    member of an annotated anchor. Every anchor touched is recomputed once at
    the end, so the consensus - and with it the batch ledger and every derived
    Sample view - reflects the new members.

    :return: Counts: anchors searched, anchors annotated, members assigned by
        propagation, samples searched, samples re-scored.
    """
    async with async_session() as session:
        anchors, members = await _unassigned_anchors_and_members(
            session, sample_batch_id
        )
    counts = {
        "anchors_searched": len(anchors),
        "anchors_annotated": 0,
        "members_propagated": 0,
        "samples_searched": 0,
        "samples_rescored": 0,
    }
    if not anchors:
        return counts

    representatives = choose_representatives(members)
    by_sample = group_by_sample(representatives)
    members_by_sample: dict[str, list] = defaultdict(list)
    for member in members:
        members_by_sample[member.sample_item_id].append(member)

    reports = process_id is not None
    total = len(by_sample) + len(members_by_sample)
    step = 0

    annotations: dict[str, Annotation] = {}
    for sample_item_id, sample_representatives in by_sample.items():
        if reports:
            await send_progress_user_notification(
                _progress(
                    sample_batch_id,
                    step,
                    total,
                    f"Searching untargeted compositions, sample {step + 1}/{total}.",
                    user_id,
                    process_id,
                    parent_id,
                )
            )
        rows = await _search_sample(
            sample_item_id,
            {member.sample_peak_id for member in sample_representatives},
            config,
        )
        annotations.update(
            await _apply_search_rows(
                sample_batch_id, sample_item_id, rows, set(anchors)
            )
        )
        counts["samples_searched"] += 1
        step += 1
    counts["anchors_annotated"] = len(annotations)

    if annotations:
        for sample_item_id in members_by_sample:
            if reports:
                await send_progress_user_notification(
                    _progress(
                        sample_batch_id,
                        step,
                        total,
                        f"Measuring the found compositions, sample {step + 1}/{total}.",
                        user_id,
                        process_id,
                        parent_id,
                    )
                )
            counts["members_propagated"] += await _propagate_to_sample(
                sample_batch_id, sample_item_id, annotations, config
            )
            counts["samples_rescored"] += 1
            step += 1

    await recompute_batch_consensus(sample_batch_id, set(anchors))
    return counts


def search_outcome(counts: dict, sample_batch_id: str) -> dict:
    """The controller result the background-task decorator reports from."""
    notification_data = {"sample_batch_id": sample_batch_id}
    if not counts["anchors_searched"]:
        return {
            "status": "partial",
            "message": (
                "No untargeted search was run: every batch peak of this batch "
                "already carries an assignment, or the batch has no batch peaks yet."
            ),
            "data": counts,
            "_notification_data": notification_data,
        }
    return {
        "status": "success",
        "message": (
            f"Searched {counts['anchors_searched']} unassigned batch peak"
            f"{'s' if counts['anchors_searched'] != 1 else ''} across "
            f"{counts['samples_searched']} sample"
            f"{'s' if counts['samples_searched'] != 1 else ''}: "
            f"{counts['anchors_annotated']} assigned a composition, and "
            f"{counts['members_propagated']} member peak"
            f"{'s' if counts['members_propagated'] != 1 else ''} in other samples "
            "measured against it."
        ),
        "data": counts,
        "_notification_data": notification_data,
    }


@api_controller_background_task(
    success_notification_rooms=["sample_batch_id"],
    success_reload=[("peak_assignment", "sample_batch_id")],
    error_notification_rooms=["sample_batch_id"],
)
async def search_batch_untargeted(
    sample_batch_id: str,
    config: PeakAssignmentConfig | None = None,
    independent_transaction: bool = False,
    user_id: int | None = None,
    process_id: str | None = None,
    parent_id: str | None = None,
) -> dict:
    """Run the untargeted composition search once per unassigned anchor of a
    batch, on each anchor's brightest member, and measure the result against
    the anchor's other members. Writes no per-sample run; emits
    ``peak_assignment_reload`` so the ledgers refresh."""
    counts = await run_batch_untargeted_search(
        sample_batch_id,
        config or PeakAssignmentConfig(),
        user_id=user_id,
        process_id=process_id,
        parent_id=parent_id,
    )
    return search_outcome(counts, sample_batch_id)
