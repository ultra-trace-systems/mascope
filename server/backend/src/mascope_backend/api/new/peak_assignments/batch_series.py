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
so each member carries a fit of its own and the tier a run of its sample gives
the reading, the series' record counted as its second observation as such a
run counts it, and a member whose peak the formula's envelope does not reach
stays as it was. Where
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

A contaminant ladder no assigned anchor carries the element of (a siloxane, a
perfluorinated acid) is never walked: a contaminant unit steps only from a
formula holding its element. So each contaminant unit is also linked by mass
alone among the anchors nothing explains - the ones the walk may reach and no
committed proposal took, and the ones held below assignability - less the
isotope lines of brighter anchors
(``mascope_tools.composition.series.ladder_evidence``), against the same
decoys and floors, in a mode with no assigned anchor too. A committed ladder
names its unit and its anchors, not a formula, so it is measured on no member
and recorded on no anchor: the run's summary lists it, per mode, beside the
walk's units.

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
    MARK_SERIES,
    ROLE_M0,
    SERIES_KEY,
    is_isotope_line,
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
    TIER_BELOW_ASSIGNABILITY,
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
    CONTAMINANT_UNITS,
    MIN_ENRICHMENT,
    MIN_LINKS,
    SERIES_UNITS,
    LadderEvidence,
    MeasuredPeak,
    Proposal,
    SeriesEvidence,
    SeriesSource,
    SeriesTarget,
    committed_proposals,
    isotope_lines,
    ladder_evidence,
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


@dataclass
class ModeAnchors:
    """One ionization mode's anchors, as the walk reads them.

    :ivar sources: The assigned anchors the walk starts from.
    :ivar targets: The anchors it may reach.
    :ivar formula_of: Anchor id -> its consensus formula, for the sources and
        for every target that reads one.
    :ivar series_members: The targets the batch assigns: members of a series,
        which the ladders leave out, as their readings explain them.
    :ivar below: The anchors held below assignability: a formula was tried
        and none reached candidate. The walk does not reach them; the ladders
        link them, as nothing explains them.
    :ivar peaks: Every anchor of the mode, claimed ones included, with its
        brightest member: what the ladders' isotope lines are read against.
    """

    sources: list[SeriesSource] = field(default_factory=list)
    targets: list[SeriesTarget] = field(default_factory=list)
    formula_of: dict[str, str] = field(default_factory=dict)
    series_members: set[str] = field(default_factory=set)
    below: list[SeriesTarget] = field(default_factory=list)
    peaks: list[MeasuredPeak] = field(default_factory=list)


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

    An anchor held below assignability is neither, and is kept apart for the
    ladders (:attr:`ModeAnchors.below`). Each mode also keeps every anchor of
    its own, claimed ones included, with its brightest member
    (:attr:`ModeAnchors.peaks`).

    :param anchors: The batch's anchors (``BatchPeak`` rows or anything with
        their attributes).
    :param notation_by_id: Mechanism id -> its notation.
    :return: Mode id -> its anchors.
    """
    modes: dict[Optional[str], ModeAnchors] = defaultdict(ModeAnchors)
    peaks: dict[Optional[str], list[MeasuredPeak]] = defaultdict(list)
    for anchor in anchors:
        peaks[anchor.ionization_mode_id].append(
            MeasuredPeak(anchor.batch_peak_id, anchor.mz, anchor.max_intensity)
        )
        # A series is a statement about molecules, not their isotope lines.
        if anchor.consensus_role or is_isotope_line(anchor):
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
            if tier == TIER_ASSIGNED:
                mode.series_members.add(anchor.batch_peak_id)
        elif tier == TIER_BELOW_ASSIGNABILITY:
            mode.below.append(SeriesTarget(anchor.batch_peak_id, anchor.mz, None))
    for mode_id, mode in modes.items():
        mode.peaks = peaks[mode_id]
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
        every committed target. A member that already reads the proposed
        formula is left as it is.
    :ivar corroborated: The committed targets already reading the proposed
        formula - held at candidate on it, or a member an earlier detection
        measured: the record corroborates their reading, and only their
        members that do not read it yet are measured (MAS-769).
    :ivar ladders: Mode id -> every contaminant unit's links by mass alone
        there (:func:`plan_series`), which nothing measures or records on an
        anchor.
    """

    evidence: dict[Optional[str], list[SeriesEvidence]] = field(default_factory=dict)
    records: dict[str, dict] = field(default_factory=dict)
    annotations: dict[str, Annotation] = field(default_factory=dict)
    corroborated: set[str] = field(default_factory=set)
    ladders: dict[Optional[str], list[LadderEvidence]] = field(default_factory=dict)

    @property
    def modes_walked(self) -> list[Optional[str]]:
        """Every mode walked from an assigned anchor or linked by mass alone,
        in the order they were walked: :func:`plan_series` links every mode it
        walks, so :attr:`ladders` holds them all in that order."""
        return list(dict.fromkeys([*self.ladders, *self.evidence]))


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
    """Walk every unit in every mode, keep the committed families' proposals,
    and link the contaminant units by mass alone among what is left.

    The ladders link the anchors nothing explains: the targets no committed
    proposal took and the batch does not assign
    (:attr:`ModeAnchors.series_members`), and the anchors held below
    assignability (:attr:`ModeAnchors.below`). That is the unassigned anchors
    and those held at candidate or below assignability, which is where a
    contaminant read on a grid without its element ends up. An anchor one
    isotope spacing above a brighter one of the mode is left out
    (``mascope_tools.composition.series.isotope_lines``): a rung nothing reads
    claims none of its lines, and they would link into ladders of their own. A
    mode with no assigned anchor to walk from is still linked.

    :param modes: The anchors, per mode (:func:`series_anchors`).
    :param chemistry: Each mode's walk settings; a mode with none is not
        walked.
    :param mechanism_id_by_notation: The notation -> mechanism id the
        sources' notations came from, for the proposals' mechanism ids.
    :param batch_peak_run_id: The run doing it, for the records.
    :param min_links: The proposals, or links, a family needs
        (:data:`MIN_LINKS`).
    :param min_enrichment: Its floor over the decoys (:data:`MIN_ENRICHMENT`).
    :return: The plan.
    """
    plan = SeriesPlan()
    for mode_id, anchors in modes.items():
        settings = chemistry.get(mode_id)
        if settings is None or not (anchors.targets or anchors.below):
            continue
        if anchors.sources and anchors.targets:
            _walk_mode(
                plan,
                mode_id,
                anchors,
                settings,
                mechanism_id_by_notation=mechanism_id_by_notation,
                batch_peak_run_id=batch_peak_run_id,
                min_links=min_links,
                min_enrichment=min_enrichment,
            )
        lines = isotope_lines(anchors.peaks, tolerance_ppm=settings.tolerance_ppm)
        free = [
            target
            for target in [*anchors.targets, *anchors.below]
            if target.key not in plan.records
            and target.key not in anchors.series_members
            and target.key not in lines
        ]
        plan.ladders[mode_id] = [
            ladder_evidence(
                free,
                unit,
                tolerance_ppm=settings.tolerance_ppm,
                min_links=min_links,
                min_enrichment=min_enrichment,
            )
            for unit in CONTAMINANT_UNITS
        ]
    return plan


def _walk_mode(
    plan: SeriesPlan,
    mode_id: Optional[str],
    anchors: ModeAnchors,
    settings: ModeChemistry,
    *,
    mechanism_id_by_notation: dict[str, str],
    batch_peak_run_id: Optional[str],
    min_links: int,
    min_enrichment: float,
) -> None:
    """Walk every unit from one mode's assigned anchors into ``plan``."""
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
        # A proposal its anchor already reads corroborates the reading, and is
        # measured too: on the members that do not read it yet, which an
        # earlier detection that stopped partway never reached, or a sample
        # folded in since (MAS-769). A member that reads it already is left
        # as it is (``batch_untargeted._takes_the_member``).
        if targets[key].formula == proposal.formula:
            plan.corroborated.add(key)
        plan.annotations[key] = Annotation(
            formula=proposal.formula,
            ion_formula=ion_formula_of(proposal.formula, proposal.mechanism),
            ionization_mechanism_id=mechanism_id,
            role=ROLE_M0,
            owner_batch_peak_id=None,
        )


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


def ladder_summary(evidence: LadderEvidence) -> dict:
    """The run's record of one contaminant unit's links by mass alone: its
    counts against the decoys and, where it beat them, every ladder, its batch
    peaks and their m/z lightest first."""
    record = evidence.to_json()
    record["ladders"] = (
        [
            {
                "batch_peak_ids": list(ladder.keys),
                "mz": [round(mz, 6) for mz in ladder.mzs],
            }
            for ladder in evidence.ladders
        ]
        if evidence.significant
        else []
    )
    return record


def _evidence_summary(
    plan: SeriesPlan, chemistry: dict[Optional[str], ModeChemistry]
) -> list[dict]:
    """The run's record of every mode's walk: its settings, every unit's
    evidence and every contaminant unit's links by mass alone, the decoy counts
    included, so a reader can recompute the FDR. ``units`` is empty in a mode
    with no assigned anchor to walk from."""
    return [
        {
            "ionization_mode_id": mode_id,
            "profile": chemistry[mode_id].profile,
            "tolerance_ppm": chemistry[mode_id].tolerance_ppm,
            "labelled": chemistry[mode_id].labelled,
            "units": [unit.to_json() for unit in plan.evidence.get(mode_id, [])],
            "ladders": [ladder_summary(unit) for unit in plan.ladders.get(mode_id, [])],
        }
        for mode_id in plan.modes_walked
    ]


def ladder_counts(ladders: dict[Optional[str], list[LadderEvidence]]) -> dict:
    """What the run's counts say of the ladders: how many units beat their
    decoys, their families, the batch peaks their ladders hold and the FDR
    over them, pooled as :func:`batch_fdr` pools the walk's."""
    committed = [e for units in ladders.values() for e in units if e.significant]
    families = {e.family for e in committed}
    return {
        "ladders_committed": len(committed),
        "ladder_families": [
            unit.family for unit in CONTAMINANT_UNITS if unit.family in families
        ],
        "ladder_members": sum(e.n_members for e in committed),
        "ladder_fdr": batch_fdr(ladders),
    }


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
        "units": _evidence_summary(plan, chemistry),
        "fdr": batch_fdr(plan.evidence),
        "modes_walked": len(plan.modes_walked),
        "modes_anchored": len(plan.evidence),
        "modes_skipped": len(skipped_modes),
        **ladder_counts(plan.ladders),
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
                    series=plan.records,
                    mark=MARK_SERIES,
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


def _rate(fdr: Optional[float]) -> str:
    """The false discovery rate as the outcome words it, or nothing."""
    return (
        f" (false discovery rate on the decoys {100 * fdr:.1f}%)"
        if fdr is not None
        else ""
    )


def _ladders_found(counts: dict, anchored: bool) -> str:
    """What the outcome says of the ladders linked by mass alone."""
    if not counts.get("ladders_committed"):
        return (
            ""
            if anchored
            else (
                " Nor did a siloxane or CF2 ladder beat the decoys by mass alone "
                "among its batch peaks."
            )
        )
    families = counts.get("ladder_families") or ["contaminant"]
    many = len(families) > 1
    members = counts.get("ladder_members", 0)
    return (
        f" By mass alone, the {' and '.join(families)} ladder{'s' if many else ''} "
        f"beat the decoys among the batch peaks nothing assigns"
        f"{_rate(counts.get('ladder_fdr'))}: {members} batch peak"
        f"{'s' if members != 1 else ''} one unit apart, listed in the run's "
        "summary with no formula proposed."
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
                    "No series was walked: the batch has no batch peak left "
                    "unassigned, or held only at candidate or below "
                    "assignability, to reach."
                )
            ),
            "data": counts,
            "_notification_data": notification_data,
        }
    anchored = bool(counts.get("modes_anchored", counts["modes_walked"]))
    families = counts["families_committed"]
    failed = counts.get("samples_failed", 0)
    skipped = (
        f" {failed} sample{'s' if failed != 1 else ''} could not be read and "
        f"{'were' if failed != 1 else 'was'} skipped."
        if failed
        else ""
    )
    if unresolved:
        skipped += f" {_unresolved_modes(unresolved)}"
    found = (
        (
            f"{families} series famil{'ies' if families != 1 else 'y'} beat the "
            f"decoys{_rate(counts.get('fdr'))}: {counts['anchors_proposed']} "
            f"batch peak{'s' if counts['anchors_proposed'] != 1 else ''} "
            f"proposed, {counts['anchors_in_series']} now read as a member of a "
            f"series, and {counts['members_assigned']} member peak"
            f"{'s' if counts['members_assigned'] != 1 else ''} measured against "
            "its formula."
        )
        if anchored
        else (
            "No series was walked from an assigned batch peak: the batch has "
            "none to walk from, or no batch peak unassigned or at candidate to "
            "step onto."
        )
    )
    return {
        "status": "partial" if failed or unresolved else "success",
        "message": f"{found}{_ladders_found(counts, anchored)}{skipped}",
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
