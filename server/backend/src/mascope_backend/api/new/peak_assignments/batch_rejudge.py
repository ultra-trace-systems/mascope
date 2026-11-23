"""A sample's judging, run again on what the batch stages wrote in it.

A batch stage that rewrites a sample's members - a water-ladder rung or a known
species claimed, the search's or a series' reading measured, a row stripped to
a parent's isotope line - writes what it decided. Measured by the stage alone,
a reading is judged only against the readings the stage proposes in that
sample: the sample's other readings, which a run of the sample judges it
against, stay in the batch ledger out of sight. A reading another channel of
the same neutral corroborates in the run then reads ``candidate`` on the batch
where the run reads it ``assigned`` (MAS-976's measurement on the demo
dataset; MAS-983, task 2).

So after a stage rewrites members, each sample it wrote in is judged again
(:func:`rejudge_sample`), through the per-sample stage list as a run judges
(``pipeline.PROPAGATION``): every reading the sample's members carry is
proposed together, one given formula per member, measured at the sample's
recorded measurement, elected, and judged and capped as a run judges its rows,
with the batch's series records read as a run reads them. A peak the batch
claimed for the source is no reading's (``RunContext.held_peak_ids``). What
comes back is written onto the members the batch wrote (the member's batch
mark, ``batch_peaks.batch_mark``) and onto those a new decision corroborates
(a series proposal the member already reads, MAS-765): each takes the tier
the judging gives the reading it carries, and nothing else of it moves. A
listed compound the batch decided present is proposed at candidate
(``RunContext.floors``), so its tier is the caps' to lower, never the
evidence's alone to keep below (MAS-983's Verify).

The members a sample's own run or fold wrote keep the tiers the run gave them:
they are the context the batch's readings are judged in, not what is judged.

A pass judges a sample again after each of its rewriting stages, and keeps
what it measured of the sample's readings between them
(``kept_measurement.KeptMeasurement``): a judging scores only the readings it
has not measured at the same inputs, the ones the stage wrote or changed, and
elects, judges and caps all of them as a run does (MAS-1073).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Awaitable, Callable, Iterable
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select

from mascope_backend.api.controllers.samples.lib.samples_fetch import fetch_sample
from mascope_backend.api.new.peak_assignments.batch_lock import _acquire_batch_fold_lock
from mascope_backend.api.new.peak_assignments.batch_peaks import (
    KNOWN_SPECIES_KEY,
    ROLE_ISO_CHILD,
    ROLE_M0,
    SERIES_KEY,
    SOURCE_ROLES,
    batch_mark_of,
    resolve_candidate,
    role_name,
    standing_series_record,
    tier_code,
)
from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
    _recompute_consensus,
)
from mascope_backend.api.new.peak_assignments.batch_runs import touch_ledger
from mascope_backend.api.new.peak_assignments.batch_untargeted import Annotation
from mascope_backend.api.new.peak_assignments.config import PeakAssignmentConfig
from mascope_backend.api.new.peak_assignments.fold_view import fold_run_id
from mascope_backend.api.new.peak_assignments.kept_measurement import KeptMeasurement
from mascope_backend.api.new.peak_assignments.pipeline import (
    PROPAGATION,
    RunContext,
    run_stages,
)
from mascope_backend.api.new.peak_assignments.tiers import TIER_CANDIDATE
from mascope_backend.db import BatchPeak, BatchPeakOccurrence, async_session
from mascope_backend.runtime import runtime


#: The stage whose listed-compound claims are proposed at candidate.
KNOWN_SPECIES_STAGE = "known_species"


def given_readings(members: Iterable[Any], registries: dict[str, list]) -> dict:
    """Every monoisotopic reading a sample's members carry, one per anchor:
    what a run of the sample would hold together, proposed together.

    :param members: The sample's members, as loaded.
    :param registries: Anchor id -> its candidate registry.
    :return: Anchor id -> ``batch_untargeted.Annotation``.
    """

    given = {}
    for member in members:
        if role_name(member.role) != ROLE_M0 or member.candidate is None:
            continue
        identity = resolve_candidate(
            registries.get(member.batch_peak_id), member.candidate
        )
        if not identity.get("formula") or not identity.get("ionization_mechanism_id"):
            continue
        given[member.batch_peak_id] = Annotation(
            formula=identity["formula"],
            ion_formula=identity.get("ion_formula"),
            ionization_mechanism_id=identity["ionization_mechanism_id"],
            role=ROLE_M0,
            owner_batch_peak_id=None,
        )
    return given


def judged_targets(members: Iterable[Any], corroborated: frozenset[str]) -> set[str]:
    """The anchors whose members in this sample take the judging's tier: the
    ones a batch stage wrote, and the ones a new decision corroborates."""
    return {
        member.batch_peak_id
        for member in members
        if batch_mark_of(member) is not None or member.batch_peak_id in corroborated
    }


def floors_of(members: Iterable[Any]) -> dict[str, str]:
    """Peak -> the tier a batch decision proposes its reading at: a listed
    compound the batch decided present, at candidate."""
    floors = {}
    for member in members:
        mark = batch_mark_of(member)
        if (
            mark is not None
            and mark.get("stage") == KNOWN_SPECIES_STAGE
            and role_name(member.role) == ROLE_M0
        ):
            floors[str(member.sample_peak_id)] = TIER_CANDIDATE
    return floors


def judged_reading(member: Any, targets: set[str], given: dict) -> Any:
    """The reading a member takes the judging's tier for, or None where it
    keeps its own: a monoisotopic member of a target anchor, for its own
    reading; an isotope line a stage wrote, or one of a target's, for its
    owner's (a line is judged through its owner).

    :param targets: The anchors whose members take the tier
        (:func:`judged_targets`).
    :param given: Anchor id -> the reading proposed for it
        (:func:`given_readings`).
    """
    role = role_name(member.role)
    if role == ROLE_M0 and member.batch_peak_id in targets:
        return given.get(member.batch_peak_id)
    if role == ROLE_ISO_CHILD and (
        member.batch_peak_id in targets or member.owner_batch_peak_id in targets
    ):
        return given.get(member.owner_batch_peak_id)
    return None


def new_tier(member: Any, row: Optional[dict], annotation: Any) -> Optional[str]:
    """The tier a judged row gives a member, where the row reads what the
    member carries; None where it reads something else or nothing.

    :param member: The member, as loaded.
    :param row: The judged row on the member's peak, if any.
    :param annotation: The reading the member carries (a monoisotopic one),
        or its owner's (an isotope line).
    """
    if row is None or annotation is None:
        return None
    if row.get("assigned_formula") != annotation.formula:
        return None
    if str(row.get("ionization_mechanism_id")) != str(
        annotation.ionization_mechanism_id
    ):
        return None
    if row.get("role") != role_name(member.role):
        return None
    return row.get("tier")


async def _sample_members(
    sample_batch_id: str, sample_item_id: str
) -> tuple[list, dict[str, list]]:
    async with async_session() as session:
        members = (
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
        registries = dict(
            (
                await session.execute(
                    select(BatchPeak.batch_peak_id, BatchPeak.candidates).where(
                        BatchPeak.batch_peak_id.in_(
                            sorted({m.batch_peak_id for m in members})
                        )
                    )
                )
            ).all()
        )
    return list(members), registries


async def rejudge_sample(
    sample_batch_id: str,
    sample_item_id: str,
    config: PeakAssignmentConfig,
    *,
    corroborated: frozenset[str] = frozenset(),
    kept: Optional[KeptMeasurement] = None,
) -> Counter:
    """Judge one sample's readings again as a run judges them, and write the
    tiers onto the members the batch wrote or corroborates.

    :param corroborated: Anchors a new decision corroborates in this sample:
        their members take the judging's tier though no stage rewrote them.
    :param kept: What the pass measured of its samples' readings: the
        readings it measured at the same inputs are not scored again. None
        scores every one.
    :return: ``samples_judged`` (1 where the sample was judged, nothing
        where nothing the batch wrote in it carries a reading to judge),
        ``members_judged`` and ``members_moved``; with ``kept``, the
        readings scored and kept (``readings_scored``, ``readings_kept``),
        or ``samples_unrecorded`` for a sample whose run recorded no model
        to keep a measurement at, every reading of it scored.
    """

    members, registries = await _sample_members(sample_batch_id, sample_item_id)
    targets = judged_targets(members, corroborated)
    given = given_readings(members, registries)
    if not any(judged_reading(m, targets, given) is not None for m in members):
        # Nothing the batch wrote here carries a reading to judge: a claim for
        # the source only, which no run's judging tiers.
        return Counter()
    held = frozenset(
        str(m.sample_peak_id) for m in members if role_name(m.role) in SOURCE_ROLES
    )

    async def keep(ctx: RunContext) -> list[dict]:
        return ctx.judged.rows

    ctx = await run_stages(
        RunContext(
            path=PROPAGATION,
            sample=await fetch_sample(sample_item_id),
            config=config,
            run_id=fold_run_id(sample_item_id),
            keep=keep,
            given=given,
            held_peak_ids=held,
            floors=floors_of(members),
            kept=kept,
        )
    )
    by_peak = {str(row["sample_peak_id"]): row for row in ctx.result}
    counts: Counter = Counter(samples_judged=1)
    if kept is not None:
        counts.update(kept.counts.pop(sample_item_id, Counter()))
    now = datetime.now(timezone.utc)
    async with async_session() as session:
        await _acquire_batch_fold_lock(session, sample_batch_id)
        # Read again under the lock: a fold since replaced them.
        fresh = (
            (
                await session.execute(
                    select(BatchPeakOccurrence).where(
                        BatchPeakOccurrence.sample_item_id == sample_item_id,
                        BatchPeakOccurrence.batch_peak_id.in_(sorted(registries)),
                    )
                )
            )
            .scalars()
            .all()
        )
        touched: set[str] = set()
        for member in fresh:
            annotation = judged_reading(member, targets, given)
            if annotation is None:
                continue
            tier = new_tier(member, by_peak.get(str(member.sample_peak_id)), annotation)
            if tier is None:
                continue
            counts["members_judged"] += 1
            if member.tier != tier_code(tier):
                member.tier = tier_code(tier)
                counts["members_moved"] += 1
                touched.add(member.batch_peak_id)
        if touched:
            await session.flush()
            await _recompute_consensus(session, touched, now)
            await touch_ledger(session, sample_batch_id)
        await session.commit()
    return counts


async def rejudge_samples(
    sample_batch_id: str,
    samples: dict[str, frozenset[str]],
    config: PeakAssignmentConfig,
    *,
    kept: Optional[KeptMeasurement] = None,
    progress: Optional[Callable[[float], Awaitable[None]]] = None,
) -> dict:
    """Judge each sample again (:func:`rejudge_sample`); a sample that cannot
    be read is counted and skipped, as the stages skip one.

    :param samples: Sample id -> the anchors a new decision corroborates in it.
    :param kept: What the pass measured of its samples' readings.
    :param progress: Told how far through the samples the judging is, a
        share, before each sample and after the last.
    :return: The counts, with ``samples_judged`` and ``samples_failed``.
    """
    counts: Counter = Counter()
    order = sorted(samples)
    for done, sample_item_id in enumerate(order):
        if progress is not None:
            await progress(done / len(order))
        try:
            counts.update(
                await rejudge_sample(
                    sample_batch_id,
                    sample_item_id,
                    config,
                    corroborated=samples[sample_item_id],
                    kept=kept,
                )
            )
        except Exception as exc:  # noqa: BLE001 - one sample never stops the rest
            runtime.logger.warning(
                f"Judging sample '{sample_item_id}' again after a batch stage "
                f"failed: {exc}"
            )
            counts["samples_failed"] += 1
    if progress is not None and order:
        await progress(1.0)
    return dict(counts)


def judged_message(counts: dict) -> str:
    """What a pass's judging of a stage's samples says it did
    (:func:`rejudge_samples`' counts): the samples judged, the members that
    took another tier, the readings measured and the ones kept from earlier
    in the pass, and the samples with no recorded measurement to keep."""
    judged = counts.get("samples_judged", 0)
    moved = counts.get("members_moved", 0)
    message = (
        f"Judged {judged} sample{'s' if judged != 1 else ''} again as a run "
        f"judges them: {moved} member{'s' if moved != 1 else ''} took another "
        f"tier; {counts.get('readings_scored', 0)} readings measured and "
        f"{counts.get('readings_kept', 0)} kept from earlier in the pass."
    )
    unrecorded = counts.get("samples_unrecorded", 0)
    if unrecorded:
        message += (
            f" {unrecorded} of them had no recorded measurement to keep, and "
            "every reading of them was measured again."
        )
    return message


async def samples_written(
    sample_batch_id: str, stage: str, version: Optional[int]
) -> set[str]:
    """The samples whose members a stage's run marked as written."""
    async with async_session() as session:
        rows = (
            await session.execute(
                select(
                    BatchPeakOccurrence.sample_item_id,
                    BatchPeakOccurrence.batch_written,
                )
                .join(
                    BatchPeak,
                    BatchPeak.batch_peak_id == BatchPeakOccurrence.batch_peak_id,
                )
                .where(
                    BatchPeak.sample_batch_id == sample_batch_id,
                    BatchPeakOccurrence.batch_written.is_not(None),
                )
            )
        ).all()
    return {
        sample
        for sample, mark in rows
        if isinstance(mark, dict)
        and mark.get("stage") == stage
        and (version is None or mark.get("version") == version)
    }


async def species_restored(
    sample_batch_id: str, version: Optional[int]
) -> dict[str, set[str]]:
    """Sample -> the batch peaks where a known-species decision of this
    version withdrew an earlier one's claims (``batch_known_species``), whose
    members read again what they read before it: the samples a withdrawn
    decision is judged again in."""
    async with async_session() as session:
        rows = (
            await session.execute(
                select(BatchPeak.batch_peak_id, BatchPeak.provenance).where(
                    BatchPeak.sample_batch_id == sample_batch_id,
                    BatchPeak.provenance[KNOWN_SPECIES_KEY].is_not(None),
                )
            )
        ).all()
    restored: dict[str, set[str]] = {}
    for batch_peak_id, provenance in rows:
        record = (provenance or {}).get(KNOWN_SPECIES_KEY)
        if not isinstance(record, dict) or not isinstance(record.get("restored"), dict):
            continue
        if version is not None and record.get("version") != version:
            continue
        for sample in record["restored"].get("samples") or ():
            restored.setdefault(sample, set()).add(batch_peak_id)
    return restored


async def series_corroborated(
    sample_batch_id: str, version: Optional[int]
) -> dict[str, set[str]]:
    """Sample -> the anchors a series record of this version corroborates in
    it: a member that already reads the formula the record names, through its
    mechanism. The series counts as a second observation of the reading
    (``tiering.series_anchor``), which the member's tier has to say without a
    new run of its sample (MAS-765)."""
    async with async_session() as session:
        anchors = (
            (
                await session.execute(
                    select(BatchPeak).where(
                        BatchPeak.sample_batch_id == sample_batch_id,
                        BatchPeak.provenance[SERIES_KEY].is_not(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        standing = {
            anchor.batch_peak_id: (anchor, record)
            for anchor in anchors
            if (record := standing_series_record(anchor)) is not None
            and (version is None or record.get("version") == version)
        }
        if not standing:
            return {}
        members = (
            (
                await session.execute(
                    select(BatchPeakOccurrence).where(
                        BatchPeakOccurrence.batch_peak_id.in_(sorted(standing))
                    )
                )
            )
            .scalars()
            .all()
        )
    corroborated: dict[str, set[str]] = {}
    for member in members:
        anchor, record = standing[member.batch_peak_id]
        if role_name(member.role) != ROLE_M0:
            continue
        identity = resolve_candidate(anchor.candidates, member.candidate)
        if identity.get("formula") == record.get("formula") and identity.get(
            "ionization_mechanism_id"
        ) == record.get("ionization_mechanism_id"):
            corroborated.setdefault(member.sample_item_id, set()).add(
                member.batch_peak_id
            )
    return corroborated


__all__ = [
    "series_corroborated",
    "species_restored",
    "floors_of",
    "given_readings",
    "judged_message",
    "judged_reading",
    "judged_targets",
    "new_tier",
    "rejudge_sample",
    "rejudge_samples",
    "samples_written",
]
