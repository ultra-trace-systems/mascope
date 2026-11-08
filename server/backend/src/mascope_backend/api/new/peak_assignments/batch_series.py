"""Series detection on the batch ledger: step 4.1 of the assignment plan.

A batch that holds a compound at a confident reading often holds its
homologues as well - one CH2, one O, one CO2 further - as anchors nobody
assigned, or held at candidate because one peak is all that saw each of them.
"Detect series" walks the batch's assigned anchors along the repeat units of
``mascope_tools.composition.series`` and proposes, wherever an anchor the
batch did not assign sits at the exact mass a step predicts, the formula the
step makes. Each unit is walked again at decoy offsets, and only the units
whose proposals beat the decoys are committed (:class:`SeriesEvidence`).

A committed proposal is a formula to measure, not an assignment. It is scored
against every sample that holds a member of its anchor through the batch
untargeted search's propagation chain (``batch_untargeted._propagate_to_sample``),
so each member carries a fit of its own, tiered under the batch's bands, and a
member whose peak the formula's envelope does not reach stays as it was. Where
an anchor's members already read the peak another way, the proposal takes a
member only where it measures at least as well: a homologue is a reason to
measure a formula, not to overrule a better measured one. Where the anchor
already reads the proposed formula at candidate, nothing is measured and the
series is recorded beside it.

The anchor carries the series under ``provenance.series``
(``batch_peaks.SERIES_KEY``) with ``has_anchor``: the unit, the assigned anchor
its chain starts at, how far, and the run that found it. The consensus keeps
the record for as long as it names the proposed formula, and an anchor that
reads it stays one the walk reaches rather than one it starts from, however
assigned it has become, so a second detection on an unchanged ledger finds the
same families and leaves the ledger as it stands. The records go on before the
measurement and again after it, so a detection that stops partway leaves its
record on every member it measured, and the next one finds the family again.
A per-sample run of a sample of the batch copies the record onto its row of the
same reading (``service.stamp_series``), and the tiering pass counts it as a
second observation (``tiering.series_anchor``). Nothing here writes a
per-sample run.

Design: ``docs/dev/assignment_quality_plan.md``, step 4.1.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from sqlalchemy import select

from mascope_backend.api.controllers.samples.lib.samples_fetch import fetch_sample
from mascope_backend.api.lib.api_features import api_controller_background_task
from mascope_backend.api.new.cheminfo.utils import to_custom_element_format
from mascope_backend.api.new.peak_assignments.batch_peaks import (
    ROLE_M0,
    SERIES_KEY,
    manual_pin_of,
    series_record_of,
    standing_series_record,
)
from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
    _acquire_batch_fold_lock,
    recompute_batch_consensus,
)
from mascope_backend.api.new.peak_assignments.batch_runs import (
    ACTION_SERIES,
    complete_run,
    fail_run,
    start_run,
    touch_ledger,
)
from mascope_backend.api.new.peak_assignments.batch_untargeted import (
    Annotation,
    _propagate_to_sample,
)
from mascope_backend.api.new.peak_assignments.config import PeakAssignmentConfig
from mascope_backend.api.new.peak_assignments.profiles import resolve_profile
from mascope_backend.api.new.peak_assignments.service import fetch_sample_mechanisms
from mascope_backend.api.new.peak_assignments.tiers import (
    TIER_ASSIGNED,
    TIER_CANDIDATE,
    TIER_UNASSIGNED,
)
from mascope_backend.db import (
    BatchPeak,
    BatchPeakOccurrence,
    IonizationMechanism,
    async_session,
)
from mascope_backend.runtime import runtime
from mascope_backend.socket.notifications import (
    UserNotification,
    send_progress_user_notification,
)
from mascope_file.name import get_instrument_type
from mascope_tools.composition.series import (
    MIN_ENRICHMENT,
    MIN_LINKS,
    SERIES_UNITS,
    Proposal,
    SeriesEvidence,
    SeriesSource,
    SeriesTarget,
    committed_proposals,
    series_evidence,
)
from mascope_tools.composition.utils import (
    combine_formula_and_ionization,
    parse_ionization,
)


#: The notification channel the detection reports on, start to finish.
NOTIFICATION_TYPE = "detect_batch_series"

#: The ``source`` a registry entry records for an identity this detection
#: brought to an anchor, as ``untargeted`` records the search's.
SOURCE_SERIES = "series"


# --- pure helpers -------------------------------------------------------------


def _monoisotopic(ion_formula: Optional[str]) -> bool:
    """Whether an anchor's ion is its monoisotopic line: an isotope line's ion
    names its isotope in brackets (``[13C]C9H15+``), and a series is a
    statement about molecules, not about their isotope lines."""
    return not ion_formula or "[" not in ion_formula


@dataclass
class ModeAnchors:
    """One ionization mode's anchors, as the walk reads them.

    :ivar sources: The assigned anchors the walk starts from.
    :ivar targets: The anchors it may reach.
    :ivar formula_of: Anchor id -> its consensus formula, for the sources and
        for every target that reads one.
    """

    sources: list[SeriesSource] = field(default_factory=list)
    targets: list[SeriesTarget] = field(default_factory=list)
    formula_of: dict[str, str] = field(default_factory=dict)


def _series_member(anchor: Any) -> bool:
    """Whether an anchor reads the formula the series record it carries
    proposed: a member of that series, however its tier got where it is."""
    return standing_series_record(anchor) is not None


def series_anchors(
    anchors: Iterable[Any], notation_by_id: dict[str, str]
) -> dict[Optional[str], ModeAnchors]:
    """The batch's anchors split into the walk's sources and targets, per
    ionization mode.

    A batch's anchors are partitioned per mode, and so is the walk: two modes'
    ions are on two axes. An anchor is neither when a pre-pass claimed it (the
    source's ion, the detector's ringing), when it is an isotope line of
    another anchor, or when its ion is one. A source is an assigned reading
    whose mechanism the walk can mass. A target is an anchor with no reading,
    or one held at candidate, that nobody pinned: a pin outranks every engine.

    An assigned anchor that reads the formula a series proposed for it is that
    series' member, not a root of its own, and stays a target: the detection
    measured it to assigned, or a run of a sample counted the series as its
    second observation. Walked as a source, it would leave its family the
    proposals the family was committed on less the members it lifted, and a
    second detection on an unchanged ledger would take every record off. As a
    target, the walk reaches it again and its record is renewed beside the
    reading it already holds. A pinned one is a source like any assigned pin.

    :param anchors: The batch's anchors (``BatchPeak`` rows or anything with
        their attributes).
    :param notation_by_id: Mechanism id -> its notation.
    :return: Mode id -> its anchors.
    """
    modes: dict[Optional[str], ModeAnchors] = defaultdict(ModeAnchors)
    for anchor in anchors:
        if anchor.consensus_role or anchor.isotopologue_of:
            continue
        if not _monoisotopic(anchor.consensus_ion_formula):
            continue
        mode = modes[anchor.ionization_mode_id]
        tier = anchor.consensus_tier
        formula = anchor.consensus_formula
        pinned = manual_pin_of(anchor) is not None
        if tier == TIER_ASSIGNED and (pinned or not _series_member(anchor)):
            notation = notation_by_id.get(anchor.ionization_mechanism_id or "")
            if notation:
                mode.sources.append(
                    SeriesSource(anchor.batch_peak_id, formula, notation)
                )
                mode.formula_of[anchor.batch_peak_id] = formula
            continue
        if pinned:
            continue
        if tier == TIER_UNASSIGNED:
            mode.targets.append(SeriesTarget(anchor.batch_peak_id, anchor.mz, None))
        elif tier in (TIER_CANDIDATE, TIER_ASSIGNED) and formula:
            mode.targets.append(SeriesTarget(anchor.batch_peak_id, anchor.mz, formula))
            mode.formula_of[anchor.batch_peak_id] = formula
    return dict(modes)


def ion_formula_of(formula: str, notation: str) -> str:
    """The ion a neutral makes through a mechanism, spelled as the engine
    spells a row's ion."""
    return to_custom_element_format(
        combine_formula_and_ionization(formula, parse_ionization(notation))
    )


def series_record(
    proposal: Proposal,
    units: Iterable[str],
    *,
    ionization_mechanism_id: str,
    root_formula: str,
    batch_peak_run_id: Optional[str],
) -> dict:
    """What an anchor carries of the series that proposed its formula.

    :param proposal: The committed proposal.
    :param units: Every committed unit that named the same formula.
    :param ionization_mechanism_id: The mechanism the formula is read through.
    :param root_formula: The formula of the assigned anchor its chain starts at.
    :param batch_peak_run_id: The run that found it.
    :return: The record, JSON-ready.
    """
    return {
        "formula": proposal.formula,
        "ionization_mechanism_id": ionization_mechanism_id,
        "has_anchor": True,
        "unit": proposal.unit,
        "units": sorted(units),
        "step": proposal.step,
        "depth": proposal.depth,
        "anchor_batch_peak_id": proposal.anchor,
        "root_batch_peak_id": proposal.root,
        "root_formula": root_formula,
        "ppm": round(proposal.ppm, 3),
        "batch_peak_run_id": batch_peak_run_id,
    }


@dataclass
class SeriesPlan:
    """What the detection found and what it will do with it.

    :ivar evidence: Mode id -> every unit's evidence there.
    :ivar records: Anchor id -> the series record it is to carry.
    :ivar annotations: Anchor id -> the reading to measure on its members:
        every committed target that does not already read the proposed formula.
    :ivar corroborated: The committed targets already reading the proposed
        formula - held at candidate on it, or a member an earlier detection
        measured - which carry the record and are not measured.
    """

    evidence: dict[Optional[str], list[SeriesEvidence]] = field(default_factory=dict)
    records: dict[str, dict] = field(default_factory=dict)
    annotations: dict[str, Annotation] = field(default_factory=dict)
    corroborated: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class ModeChemistry:
    """What a mode's chemistry decides about its walk.

    :ivar tolerance_ppm: The window: the untargeted search's own for the
        mode's samples (``ResolvedProfile.mz_precision_ppm``).
    :ivar labelled: Whether the mode's source is a labelled reagent.
    :ivar profile: The reagent profile the mode resolved to, for the record.
    """

    tolerance_ppm: float
    labelled: bool
    profile: str


def plan_series(
    modes: dict[Optional[str], ModeAnchors],
    chemistry: dict[Optional[str], ModeChemistry],
    *,
    mechanism_id_by_notation: dict[str, str],
    batch_peak_run_id: Optional[str] = None,
    min_links: int = MIN_LINKS,
    min_enrichment: float = MIN_ENRICHMENT,
) -> SeriesPlan:
    """Walk every unit in every mode, keep the committed families' proposals.

    :param modes: The anchors, per mode (:func:`series_anchors`).
    :param chemistry: Each mode's walk settings; a mode with none is not
        walked.
    :param mechanism_id_by_notation: The notation -> mechanism id the
        sources' notations came from, for the proposals' mechanism ids.
    :param batch_peak_run_id: The run doing it, for the records.
    :param min_links: The proposals a family needs (:data:`MIN_LINKS`).
    :param min_enrichment: Its floor over the decoys (:data:`MIN_ENRICHMENT`).
    :return: The plan.
    """
    plan = SeriesPlan()
    for mode_id, anchors in modes.items():
        settings = chemistry.get(mode_id)
        if settings is None or not anchors.sources or not anchors.targets:
            continue
        evidence = [
            series_evidence(
                anchors.sources,
                anchors.targets,
                unit,
                tolerance_ppm=settings.tolerance_ppm,
                labelled=settings.labelled,
                min_links=min_links,
                min_enrichment=min_enrichment,
            )
            for unit in SERIES_UNITS
        ]
        plan.evidence[mode_id] = evidence
        targets = {target.key: target for target in anchors.targets}
        for key, (proposal, units) in sorted(committed_proposals(evidence).items()):
            mechanism_id = mechanism_id_by_notation.get(proposal.mechanism)
            if mechanism_id is None:
                continue
            plan.records[key] = series_record(
                proposal,
                units,
                ionization_mechanism_id=mechanism_id,
                root_formula=anchors.formula_of[proposal.root],
                batch_peak_run_id=batch_peak_run_id,
            )
            if targets[key].formula == proposal.formula:
                plan.corroborated.add(key)
                continue
            plan.annotations[key] = Annotation(
                formula=proposal.formula,
                ion_formula=ion_formula_of(proposal.formula, proposal.mechanism),
                ionization_mechanism_id=mechanism_id,
                role=ROLE_M0,
                owner_batch_peak_id=None,
            )
    return plan


def batch_fdr(evidence: dict[Optional[str], list[SeriesEvidence]]) -> Optional[float]:
    """The batch's false discovery rate over its committed families: the
    decoys' mean over the proposals, summed over every committed unit of every
    mode. None where nothing was committed."""
    committed = [e for unit in evidence.values() for e in unit if e.significant]
    links = sum(e.n_links for e in committed)
    if not links:
        return None
    return min(1.0, sum(e.decoy_mean for e in committed) / links)


# --- the detection ------------------------------------------------------------


async def _mode_chemistry(
    sample_item_id: str, config: PeakAssignmentConfig
) -> ModeChemistry:
    """A mode's walk settings, resolved as a run of one of its samples
    resolves its chemistry."""
    sample = await fetch_sample(sample_item_id)
    _, mechanisms = await fetch_sample_mechanisms(sample)
    resolved = resolve_profile(
        config,
        mechanism_notations=[m.ionization_mechanism for m in mechanisms],
        instrument_type=get_instrument_type(sample.filename),
        polarity=sample.polarity,
    )
    return ModeChemistry(
        tolerance_ppm=resolved.mz_precision_ppm,
        labelled=resolved.profile.label_isotope is not None,
        profile=resolved.profile.name,
    )


async def _load_batch(
    sample_batch_id: str,
) -> tuple[list[BatchPeak], dict[str, str], dict[Optional[str], str]]:
    """The batch's anchors, its mechanisms' notations, and one sample per
    ionization mode to resolve the mode's chemistry on."""
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
        mechanism_ids = sorted(
            {a.ionization_mechanism_id for a in anchors if a.ionization_mechanism_id}
        )
        notation_by_id = {
            mechanism_id: notation
            for mechanism_id, notation in (
                await session.execute(
                    select(
                        IonizationMechanism.ionization_mechanism_id,
                        IonizationMechanism.ionization_mechanism,
                    ).where(
                        IonizationMechanism.ionization_mechanism_id.in_(mechanism_ids)
                    )
                )
            ).all()
            if notation
        }
        rows = (
            await session.execute(
                select(BatchPeak.ionization_mode_id, BatchPeakOccurrence.sample_item_id)
                .join(
                    BatchPeakOccurrence,
                    BatchPeakOccurrence.batch_peak_id == BatchPeak.batch_peak_id,
                )
                .where(BatchPeak.sample_batch_id == sample_batch_id)
                .distinct()
            )
        ).all()
    # Any sample of a mode resolves its chemistry: the mode's samples share its
    # mechanisms. The first by id, so it is the same one every time.
    sample_by_mode: dict[Optional[str], str] = {}
    for mode_id, sample_item_id in sorted(rows, key=lambda r: (str(r[0]), r[1])):
        sample_by_mode.setdefault(mode_id, sample_item_id)
    return list(anchors), notation_by_id, sample_by_mode


async def _write_records(
    sample_batch_id: str,
    records: dict[str, dict],
    skipped_modes: Iterable[Optional[str]] = (),
) -> set[str]:
    """Replace the batch's series records with this run's, under the fold lock.

    Every record an earlier run left comes off first: a detection reads the
    ledger as it stands, and a homologue it no longer finds is no longer a
    reason. The consensus recompute that follows keeps a record only on an
    anchor whose consensus names its formula. A mode the run could not walk,
    its chemistry unresolved, found nothing either way, so its anchors keep
    what they carry.

    :param sample_batch_id: The batch.
    :param records: Anchor id -> the record this run found for it.
    :param skipped_modes: The ionization modes the run did not walk.
    :return: Every anchor whose provenance was written, cleared or set.
    """
    skipped = set(skipped_modes)
    touched: set[str] = set()
    async with async_session() as session:
        await _acquire_batch_fold_lock(session, sample_batch_id)
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
        for anchor in anchors:
            if anchor.ionization_mode_id in skipped:
                continue
            record = records.get(anchor.batch_peak_id)
            provenance = dict(anchor.provenance or {})
            if record is None and SERIES_KEY not in provenance:
                continue
            if record is None:
                provenance.pop(SERIES_KEY)
            else:
                provenance[SERIES_KEY] = record
            anchor.provenance = provenance
            touched.add(anchor.batch_peak_id)
        await touch_ledger(session, sample_batch_id)
        await session.commit()
    return touched


async def _samples_holding(annotated: Iterable[str]) -> list[str]:
    """The samples with a member in any of the annotated anchors."""
    ids = sorted(set(annotated))
    if not ids:
        return []
    async with async_session() as session:
        return sorted(
            (
                await session.execute(
                    select(BatchPeakOccurrence.sample_item_id)
                    .where(BatchPeakOccurrence.batch_peak_id.in_(ids))
                    .distinct()
                )
            )
            .scalars()
            .all()
        )


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


def _evidence_summary(
    evidence: dict[Optional[str], list[SeriesEvidence]],
    chemistry: dict[Optional[str], ModeChemistry],
) -> list[dict]:
    """The run's record of every mode's walk: its settings and every unit's
    evidence, the decoy counts included, so a reader can recompute the FDR."""
    return [
        {
            "ionization_mode_id": mode_id,
            "profile": chemistry[mode_id].profile,
            "tolerance_ppm": chemistry[mode_id].tolerance_ppm,
            "labelled": chemistry[mode_id].labelled,
            "units": [unit.to_json() for unit in units],
        }
        for mode_id, units in evidence.items()
    ]


async def run_batch_series_detection(
    sample_batch_id: str,
    config: PeakAssignmentConfig,
    batch_peak_run_id: Optional[str] = None,
    user_id: Optional[int] = None,
    process_id: Optional[str] = None,
    parent_id: Optional[str] = None,
) -> dict:
    """Walk the batch's series, measure the committed proposals, record them.

    :return: The run's summary: what each mode's walk found against its
        decoys, the batch's FDR over the committed families, and counts of
        what was proposed, measured and kept.
    """
    anchors, notation_by_id, sample_by_mode = await _load_batch(sample_batch_id)
    modes = series_anchors(anchors, notation_by_id)
    chemistry: dict[Optional[str], ModeChemistry] = {}
    skipped_modes: set[Optional[str]] = set()
    for mode_id in modes:
        try:
            chemistry[mode_id] = await _mode_chemistry(sample_by_mode[mode_id], config)
        except Exception:  # noqa: BLE001 - one unreadable mode must not stop the rest
            skipped_modes.add(mode_id)
            runtime.logger.exception(
                f"Series detection skips ionization mode '{mode_id}' of batch "
                f"'{sample_batch_id}': its chemistry could not be resolved."
            )
    mechanism_id_by_notation = {
        notation: mechanism_id for mechanism_id, notation in notation_by_id.items()
    }
    plan = await asyncio.to_thread(
        plan_series,
        modes,
        chemistry,
        mechanism_id_by_notation=mechanism_id_by_notation,
        batch_peak_run_id=batch_peak_run_id,
    )
    counts = {
        "units": _evidence_summary(plan.evidence, chemistry),
        "fdr": batch_fdr(plan.evidence),
        "modes_walked": len(plan.evidence),
        "modes_skipped": len(skipped_modes),
        "families_committed": sum(
            e.significant for units in plan.evidence.values() for e in units
        ),
        "proposals": sum(e.n_links for units in plan.evidence.values() for e in units),
        "anchors_proposed": len(plan.records),
        "anchors_corroborated": len(plan.corroborated),
        "anchors_measured": len(plan.annotations),
        "members_assigned": 0,
        "anchors_in_series": 0,
        "samples_measured": 0,
        "samples_failed": 0,
    }

    samples = await _samples_holding(plan.annotations)
    reports = process_id is not None
    # The records go on before the members are measured, and again after. A
    # run that stops in between - cancelled, or a notification that raises -
    # then leaves this run's record on every member it measured, and the next
    # detection walks to them as members rather than from them as roots; the
    # consensus pass below takes it off every anchor it did not reach. Until
    # then an anchor still to be measured carries a record beside the reading
    # it held before, which no reader serves (``standing_series_record``).
    await _write_records(sample_batch_id, plan.records, skipped_modes)
    touched: set[str] = set()
    walked = False
    try:
        for step, sample_item_id in enumerate(samples):
            if reports:
                await send_progress_user_notification(
                    _progress(
                        sample_batch_id,
                        step,
                        len(samples),
                        "Measuring the series' proposals, sample "
                        f"{step + 1}/{len(samples)}.",
                        user_id,
                        process_id,
                        parent_id,
                    )
                )
            try:
                counts["members_assigned"] += await _propagate_to_sample(
                    sample_batch_id,
                    sample_item_id,
                    plan.annotations,
                    config,
                    only_unassigned=False,
                    source=SOURCE_SERIES,
                    prefer_better=True,
                )
                counts["samples_measured"] += 1
            except Exception:  # noqa: BLE001 - one bad sample must not abort the pass
                counts["samples_failed"] += 1
                runtime.logger.exception(
                    "Measuring the series' proposals failed for sample "
                    f"'{sample_item_id}'."
                )
        # Again once the members read the proposals. A fold of an anchor keeps
        # its record only while its consensus names the record's formula, and
        # before the measurement a measured anchor's does not: a sample folding
        # in meanwhile took the record off, and this puts it back.
        touched = await _write_records(sample_batch_id, plan.records, skipped_modes)
        walked = True
    finally:
        # As the untargeted search's: the members are committed sample by
        # sample, and the anchors describe them only once this has run.
        try:
            await recompute_batch_consensus(
                sample_batch_id, touched | set(plan.annotations)
            )
        except Exception:
            if walked:
                raise
            runtime.logger.exception(
                "The series detection was interrupted and its deferred consensus "
                f"pass then failed for batch '{sample_batch_id}'; rebuilding the "
                "ledger recomputes the anchors it touched."
            )
    async with async_session() as session:
        kept = (
            (
                await session.execute(
                    select(BatchPeak).where(
                        BatchPeak.batch_peak_id.in_(sorted(plan.records))
                    )
                )
            )
            .scalars()
            .all()
        )
    counts["anchors_in_series"] = sum(
        series_record_of(anchor) is not None for anchor in kept
    )
    return counts


def _unresolved_modes(n_modes: int) -> str:
    """What the outcome says of the modes whose chemistry was not resolved."""
    one = n_modes == 1
    return (
        f"The chemistry of {n_modes} ionization mode{'' if one else 's'} could "
        f"not be resolved, so {'it was' if one else 'they were'} not walked and "
        f"{'its' if one else 'their'} batch peaks keep the series an earlier "
        "detection found."
    )


def series_outcome(counts: dict, sample_batch_id: str) -> dict:
    """The controller result the background-task decorator reports from."""
    notification_data = {"sample_batch_id": sample_batch_id}
    unresolved = counts.get("modes_skipped", 0)
    if not counts["modes_walked"]:
        return {
            "status": "partial",
            "message": (
                f"No series was walked. {_unresolved_modes(unresolved)}"
                if unresolved
                else (
                    "No series was walked: the batch has no batch peak assigned "
                    "to walk from, or none left unassigned to reach."
                )
            ),
            "data": counts,
            "_notification_data": notification_data,
        }
    families = counts["families_committed"]
    fdr = counts.get("fdr")
    rate = (
        f" (false discovery rate on the decoys {100 * fdr:.1f}%)"
        if fdr is not None
        else ""
    )
    failed = counts.get("samples_failed", 0)
    skipped = (
        f" {failed} sample{'s' if failed != 1 else ''} could not be read and "
        f"{'were' if failed != 1 else 'was'} skipped."
        if failed
        else ""
    )
    if unresolved:
        skipped += f" {_unresolved_modes(unresolved)}"
    return {
        "status": "partial" if failed or unresolved else "success",
        "message": (
            f"{families} series famil{'ies' if families != 1 else 'y'} beat the "
            f"decoys{rate}: {counts['anchors_proposed']} batch peak"
            f"{'s' if counts['anchors_proposed'] != 1 else ''} proposed, "
            f"{counts['anchors_in_series']} now read as a member of a series, "
            f"and {counts['members_assigned']} member peak"
            f"{'s' if counts['members_assigned'] != 1 else ''} measured against "
            f"its formula.{skipped}"
        ),
        "data": counts,
        "_notification_data": notification_data,
    }


@api_controller_background_task(
    success_notification_rooms=["sample_batch_id"],
    success_reload=[("peak_assignment", "sample_batch_id")],
    error_notification_rooms=["sample_batch_id"],
)
async def detect_batch_series(
    sample_batch_id: str,
    config: PeakAssignmentConfig | None = None,
    independent_transaction: bool = False,
    user_id: int | None = None,
    process_id: str | None = None,
    parent_id: str | None = None,
) -> dict:
    """Detect the batch's homologous series and measure what they propose, as
    a batch run. Writes no per-sample run; emits ``peak_assignment_reload`` so
    the ledgers refresh."""
    detection_config = config or PeakAssignmentConfig()
    run_id = await start_run(
        sample_batch_id,
        ACTION_SERIES,
        config=detection_config.model_dump(mode="json"),
        user_id=user_id,
    )
    try:
        counts = await run_batch_series_detection(
            sample_batch_id,
            detection_config,
            batch_peak_run_id=run_id,
            user_id=user_id,
            process_id=process_id,
            parent_id=parent_id,
        )
    except Exception as exc:
        await fail_run(sample_batch_id, run_id, f"{type(exc).__name__}: {exc}")
        raise
    await complete_run(sample_batch_id, run_id, summary=counts)
    return series_outcome(counts, sample_batch_id)
