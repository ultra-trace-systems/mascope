"""One stage list for every assignment path.

A sample's peaks are assigned by one ordered list of stages, whichever path
asks (:data:`STAGES`):

1. **resolve** the chemistry: the sample's peaks, its mechanisms, the reagent
   profile and context, the secondary channels its spectrum opens;
2. **claim** the source's peaks: the reagent's lines and the detector's
   ringing, which no later stage is offered;
3. **propose** candidates: what the path proposes for each peak;
4. **score** every candidate the way a library line is scored;
5. **elect** one reading per peak, the ledger's rows;
6. **refit** the measurement model on the ledger as the reading changes leave
   it;
7. **judge** the readings: the reading changes and what the caps read of them;
8. **cap** the tiers, which only lowers one;
9. state the **level** (``identification_level.apply_levels``);
10. **persist** what the path keeps.

The refit, judge and cap stages are one round (:data:`ROUND`): a cap can leave
a line in doubt on an assigned neighbour's envelope, which the next round reads
as that neighbour's, and the round runs again until nothing new is read
(``judging_loop.Judging``, :data:`judging_loop.MAX_CLAIM_ROUNDS`).

A path is a configuration of the list (:class:`AssignmentPath`), set by the
stage it starts at, what its propose stage proposes and where its measurement
model comes from, and it runs every stage from its start to the end
(:func:`run_stages`). Where it keeps what it judged is the caller's
(:attr:`RunContext.keep`).

What the sample's batch decided about it is a declared input of the list:
read once, in the resolve stage (``batch_decisions.read_batch_decisions``),
each kind entering at the stage that decides what it decides, and read by a
decoy arm only where the arm claims what its control claims
(:data:`BATCH_INPUTS`). A run records the versions it read (MAS-985).

Design: ``docs/dev/assignment_quality_plan.md``, milestone 7 (MAS-974,
finding 1).
"""

from __future__ import annotations

import asyncio
import time
import weakref
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any, Optional

import pandas as pd

from mascope_backend.api.lib.exceptions.api_exceptions import NotFoundException
from mascope_backend.api.new.cheminfo.utils import to_custom_element_format
from mascope_backend.api.new.ionization.modes.versions import (
    sample_label_purity,
    sample_setup,
)
from mascope_backend.api.new.match.params import default_match_params
from mascope_backend.api.new.peak_assignments import (
    batch_lines,
    judging_loop,
    kept_measurement,
    model_readers,
    prepasses,
    run_config,
    sample_inputs,
    sample_mechanisms,
    stage_a_pass,
)
from mascope_backend.api.new.peak_assignments.alternatives_scoring import (
    WINNER_CONFIDENCE_KEY,
    measure_winner_confidence,
)
from mascope_backend.api.new.peak_assignments.batch_decisions import (
    KIND_ISOTOPOLOGUE_STRIP,
    KIND_KNOWN_SPECIES,
    KIND_LISTED_COMPOUND,
    KIND_SERIES,
    KIND_WATER_RUNG,
    LISTED_ELECTED,
    LISTED_LIFTED,
    BatchDecisions,
    ListedDecision,
    cite_listed,
    elect_listed,
    read_batch_decisions,
    read_strips,
)
from mascope_backend.api.new.peak_assignments.config import (
    MAX_UNTARGETED_PEAKS_CEILING,
    PeakAssignmentConfig,
)
from mascope_backend.api.new.peak_assignments.decoy import DecoyPlan
from mascope_backend.api.new.peak_assignments.engine import (
    LIST_PRIOR_WEIGHT,
    build_unassigned_assignments,
    drop_ions_claimed_elsewhere,
    evidence_for,
    formula_identity,
    invert_matches_to_peak_assignments,
    list_readings,
    listed_formula_identities,
    listed_ions,
    pattern_scoring_for,
    record_displaced_list_readings,
    record_same_ion_readings,
    scoring_snapshot,
    settle_list_election,
    tier_for_evidence,
    untargeted_targets,
)
from mascope_backend.api.new.peak_assignments.envelope_fit import (
    NeighbourReader,
    RunEnvelopeFit,
    envelope_lines,
    fit_run_envelope_tolerance,
)
from mascope_backend.api.new.peak_assignments.envelope_memo import open_memo
from mascope_backend.api.new.peak_assignments.halogen_twins import record_twins
from mascope_backend.api.new.peak_assignments.identification_level import (
    IDENTIFICATION_LEVEL,
    LEVEL_SUMMARY,
    apply_levels,
    hand_level,
    inherited_level,
)
from mascope_backend.api.new.peak_assignments.mass_gate import (
    BELOW_ASSIGNABILITY_Z,
    SpectrumLines,
)
from mascope_backend.api.new.peak_assignments.profile_preview import named_context_for
from mascope_backend.api.new.peak_assignments.profiles import (
    resolve_profile,
    with_fitted_window,
)
from mascope_backend.api.new.peak_assignments.reagent_pass import (
    REASON_BATCH_KNOWN_SPECIES,
    REASON_BATCH_WATER_LADDER,
    FragmentClaims,
    fragment_ladders_for,
)
from mascope_backend.api.new.peak_assignments.row_terms import (
    ROLE_ISO_CHILD,
    ROLE_M0,
    SOURCE_UNTARGETED,
    TIERING_KEY,
)
from mascope_backend.api.new.peak_assignments.sample_inputs import (
    RESOLVE_RESOLUTION,
    ineligible_reason,
    load_sample_peaks,
)
from mascope_backend.api.new.peak_assignments.seeded_scoring import (
    score_seeds,
)
from mascope_backend.api.new.peak_assignments.stage_a_pass import (
    ConfidenceCurve,
    _stage_a_first_pass,
)
from mascope_backend.api.new.peak_assignments.tiering import LineGate, ask_absent_line
from mascope_backend.api.new.peak_assignments.tiers import TIER_ASSIGNED, TIER_CANDIDATE
from mascope_backend.api.new.peak_assignments.untargeted_rows import (
    untargeted_matches_to_peak_assignments,
    untargeted_seeds,
)
from mascope_backend.runtime import runtime
from mascope_backend.socket.notifications import (
    UserNotification,
    send_progress_user_notification,
)
from mascope_file.name import get_instrument_type
from mascope_tools.composition.custom_elements import labelled_purity
from mascope_tools.composition.envelope_tolerance import (
    FORM_CURVE,
    LEVEL_DEFAULT,
    EnvelopeLines,
)
from mascope_tools.composition.finder import assign_compositions
from mascope_tools.composition.known_window import KnownWindow
from mascope_tools.composition.mass_accuracy import ReagentOffset, SampleMassAccuracy
from mascope_tools.composition.measurement_model import MeasurementModel


class Stage(StrEnum):
    """A stage of the list, in the order every path runs them."""

    RESOLVE = "resolve"
    CLAIM = "claim"
    PROPOSE = "propose"
    SCORE = "score"
    ELECT = "elect"
    REFIT = "refit"
    JUDGE = "judge"
    CAP = "cap"
    LEVEL = "level"
    PERSIST = "persist"


#: The stages, in order.
STAGES: tuple[Stage, ...] = tuple(Stage)

#: The judging round: run in this order, and again from its first stage until
#: the reading changes reach a fixed point.
ROUND: tuple[Stage, ...] = (Stage.REFIT, Stage.JUDGE, Stage.CAP)


class Proposal(StrEnum):
    """What a path's propose stage proposes."""

    #: The known set (Stage A), then the formula search over the peaks it
    #: leaves and the ones its lists read, where the config asks for it.
    KNOWN_AND_SEARCH = "known set and search"
    #: The known set alone.
    KNOWN = "known set"
    #: The formula search over the representatives of the batch's unassigned
    #: anchors that the sample holds (``batch_untargeted``).
    SEARCH = "search on the representatives"
    #: One given formula per member: the reading the batch decided for each
    #: anchor the sample holds a member of, proposed to the member's peak as a
    #: list proposes its formula to a peak.
    GIVEN = "one given formula per member"
    #: The person's formula: a hand assignment's on a sample's row, a pin's on
    #: a batch peak's members.
    PERSONS_FORMULA = "the person's formula"


class Measurement(StrEnum):
    """Where a path's measurement model comes from: the centre and width a
    mass error is read against, and the tolerance a line's share is."""

    #: Fitted on the path's own readings at the model's two named points:
    #: Stage A's lines and its commits' envelopes (v1), and the mass gate's
    #: corroborated commits and source lines (v2).
    FITTED = "fitted"
    #: A decoy's control's, recorded on the control and never fitted again on
    #: the decoy's wrong readings (``decoy.DecoyPlan``).
    CONTROL = "control"
    #: The sample's own, as its latest run recorded it: the measurement model
    #: it scored and capped at, its centre, width and line-share tolerance
    #: (``model_readers.recorded_measurement``). A sample no run recorded one for
    #: scores at the instrument class's width and its reagent lines' centre,
    #: and its refit stage refits that on what the path committed and the
    #: source's own lines, as a run's does.
    RECORDED = "recorded"


class Rules(StrEnum):
    """What a path's judge and cap stages ask of its readings."""

    #: Every rule a run asks: the reading changes and every cap.
    RUN = "run"
    #: The rules about the spectrum alone, which a person's choice is asked:
    #: whether the lines its formula predicts are there (``tiering.ask_absent_line``,
    #: step 3.4e). The others weigh rivals, channels and lists, which a
    #: person's choice is not; a missing line is missing whoever named the
    #: formula. Whether the unresolved-peak rule is one of them is MAS-766's
    #: open call; until it is made, it is not.
    SPECTRUM = "spectrum"


@dataclass(frozen=True)
class AssignmentPath:
    """A configuration of the stage list.

    :param name: The path, as a log line or a test names it.
    :param start: The stage it starts at; it runs every stage after it.
    :param proposal: What its propose stage proposes.
    :param measurement: Where its measurement model comes from.
    :param rules: What its judge and cap stages ask.
    """

    name: str
    start: Stage
    proposal: Proposal
    measurement: Measurement
    rules: Rules = Rules.RUN

    def stages(self) -> tuple[Stage, ...]:
        """The stages this path runs, in order, a round counted once."""
        return STAGES[STAGES.index(self.start) :]


#: A sample's run: the known set and the search, at the measurement it fits.
RUN = AssignmentPath(
    "run", Stage.RESOLVE, Proposal.KNOWN_AND_SEARCH, Measurement.FITTED
)

#: A decoy arm of a run (``decoy.py``): its control's measurement.
DECOY = AssignmentPath(
    "decoy", Stage.RESOLVE, Proposal.KNOWN_AND_SEARCH, Measurement.CONTROL
)

#: The run-less ingest fold: the known set alone, folded straight into the
#: batch ledger with no run written (``batch_fold.fold_sample_peaks_without_run``).
INGEST_FOLD = AssignmentPath(
    "ingest fold", Stage.RESOLVE, Proposal.KNOWN, Measurement.FITTED
)

#: The batch's untargeted search, one sample at a time: the search on the
#: representatives that sample holds, at its recorded measurement. Its rows are
#: merged across the batch's samples before any is written
#: (``batch_untargeted.run_batch_untargeted_search``).
BATCH_SEARCH = AssignmentPath(
    "batch search", Stage.RESOLVE, Proposal.SEARCH, Measurement.RECORDED
)

#: The batch's decisions measured on one sample's members: the batch search's
#: propagation of what it found, the series detection's proposals and an
#: import's readings (``batch_untargeted._propagate_to_sample``).
PROPAGATION = AssignmentPath(
    "propagation", Stage.RESOLVE, Proposal.GIVEN, Measurement.RECORDED
)

#: A person's choice: the hand assignment on a sample's row
#: (``curation.curate_assignment``) and the batch's *Use this*, a pin measured
#: on each member (``batch_curation``). Both read one rule set, the spectrum's,
#: at the gate the sample's run recorded; a pin in a sample no run asked lines
#: in, at the gate a run of it would ask at.
PERSONS_CHOICE = AssignmentPath(
    "person's choice",
    Stage.RESOLVE,
    Proposal.PERSONS_FORMULA,
    Measurement.RECORDED,
    Rules.SPECTRUM,
)

#: Every path, in the order this module names them.
PATHS: tuple[AssignmentPath, ...] = (
    RUN,
    DECOY,
    INGEST_FOLD,
    BATCH_SEARCH,
    PROPAGATION,
    PERSONS_CHOICE,
)


@dataclass(frozen=True)
class BatchInput:
    """A kind of batch decision a pass reads (``batch_decisions``).

    :param kind: The kind (``batch_decisions.KINDS``).
    :param stage: The stage it enters at: the one that decides what the
        decision decides.
    :param decoy: Whether a decoy arm reads it. A source ion and a listed
        compound are claimed by an arm as its control claims them; a strip and
        a series are the control's chemistry, and an arm's own lines decide.
    """

    kind: str
    stage: Stage
    decoy: bool


#: What a pass reads of its batch's decisions, the stage each enters at, and
#: what a decoy arm does with it.
BATCH_INPUTS: tuple[BatchInput, ...] = (
    # A library ion the batch decided present: claimed as the source's.
    BatchInput(KIND_KNOWN_SPECIES, Stage.CLAIM, decoy=True),
    # A rung the water ladder passed in the sample's segment: claimed so too.
    BatchInput(KIND_WATER_RUNG, Stage.CLAIM, decoy=True),
    # A listed compound decided present: proposed at candidate, citing it.
    BatchInput(KIND_LISTED_COMPOUND, Stage.PROPOSE, decoy=True),
    # A stripped row: read as the parent's line, which the judging reads.
    BatchInput(KIND_ISOTOPOLOGUE_STRIP, Stage.JUDGE, decoy=False),
    # A series: stamped on its reading, a second observation of it
    # (``tiering.series_anchor``).
    BatchInput(KIND_SERIES, Stage.JUDGE, decoy=False),
)


def batch_kinds(path: AssignmentPath, *, decoy: bool) -> frozenset[str]:
    """The kinds of batch decision a pass reads: each one whose stage the path
    runs as a run runs it, and on a decoy arm only those an arm reads.

    Every path claims the source's peaks. A listed compound is proposed only
    where the propose stage proposes the known set, which it stands beside;
    the inputs the judging reads, only where the judging asks a run's rules.
    """
    kinds = set()
    for one in BATCH_INPUTS:
        if decoy and not one.decoy:
            continue
        if one.stage is Stage.PROPOSE and path.proposal not in (
            Proposal.KNOWN_AND_SEARCH,
            Proposal.KNOWN,
        ):
            continue
        if one.stage is Stage.JUDGE and path.rules is not Rules.RUN:
            continue
        kinds.add(one.kind)
    return frozenset(kinds)


@dataclass
class SearchProposal:
    """What the formula search proposed, for the stages after it.

    :param remainder: The peaks it enumerated, by m/z.
    :param frame: The frame it scored over, positionally indexed.
    :param columns: The frame's columns it read.
    :param mechanism_id_by_notation: The searched channels.
    :param scope: What it was offered, for the run's record.
    :param excluded: The peaks earlier stages own: the claims' and the known
        set's, as the known set stood before the list election.
    :param ran: Whether it searched at all.
    :param matches: Its rows, the list's readings elected and the twins read.
    :param election: The list election's outcome, where it searched.
    :param twin_records: The twins it read, by peak.
    :param snapshot: What it scored a pattern at, for the run's record.
    :param started: When it started, for its log line.
    """

    remainder: pd.DataFrame
    frame: pd.DataFrame
    columns: list[str]
    mechanism_id_by_notation: dict[str, str]
    scope: dict
    excluded: set[str] = field(default_factory=set)
    ran: bool = False
    matches: pd.DataFrame | None = None
    election: Any = None
    twin_records: dict = field(default_factory=dict)
    snapshot: dict | None = None
    started: float = 0.0


@dataclass
class RunContext:
    """One sample's pass through the stages: what each stage reads and leaves.

    The inputs a caller sets; every other field is a stage's to fill, in the
    order the stages run, and each says which stage fills it.

    :param path: The path this pass is (:class:`AssignmentPath`).
    :param sample: The sample view row.
    :param config: The configuration the stages run under.
    :param run_id: The id every row is stamped with.
    :param keep: Where the persist stage keeps what was judged; its answer is
        :attr:`result`.
    :param run: The run row a run records on as it goes; None for a path that
        writes no run.
    :param decoy_plan: A decoy arm's plan (:class:`~decoy.DecoyPlan`).
    :param notification: The progress a run reports on, if any.
    :param strict_instrument: Whether a file name that names no instrument
        class fails the pass; a path with nobody to report to reads none.
    :param strict_chemistry: Whether a sample bound under no ionization mode
        fails the pass; a person's choice, whose rules read the spectrum
        alone, reads it as declaring no mechanism.
    :param targets: The batch search's representatives in this sample, the
        peaks it may enumerate.
    :param persistent_peak_ids: Those of them whose anchors are persistent,
        which the batch search takes however faint.
    :param explained_peak_ids: The peaks the batch ledger already explains in
        this sample: a member that reads a formula, or one a pre-pass claimed.
        The batch search judges "unexplained" against them as a run does
        against its Stage A and its pre-passes.
    :param given: The readings a path proposes one per member, by the anchor
        whose members they are (``batch_untargeted.Annotation``).
    :param series_by_peak: The batch's series records of given readings, by
        the peak each is proposed on, which the batch serves no reader while
        the series detection measures them
        (``batch_peaks.standing_series_record``): the elect stage stamps them
        as it stamps the ones the batch serves.
    :param chosen: A hand assignment's row, the person's reading already
        measured (``curation``).
    :param recorded: The run config a recorded measurement is read off; None
        reads the sample's latest in-app run's.
    :param recorded_artifact_peak_ids: The peaks the run a person's choice is
        made on claimed as the detector's ringing, which its spectrum's rules
        read as neither a line nor an absence; None reads the claim stage's.
    :param held_peak_ids: The peaks the batch claimed for the source in this
        sample (a water-ladder rung, a known species): no given reading is
        elected on them, as on the claim stage's own
        (``batch_rejudge.rejudge_sample``).
    :param floors: The tier a batch decision proposes its given reading at,
        by the peak it is proposed on: a listed compound the batch decided
        present, at candidate. The elect stage raises the reading to it, and
        so does a re-score at the refit's model (``judging_loop.rescore_rows``):
        the caps may lower it again, but the evidence alone does not keep it
        below.
    :param kept: What the batch's pass measured of the sample's given
        readings, scored again only at other inputs (``kept_measurement``).
    """

    path: AssignmentPath
    sample: Any
    config: PeakAssignmentConfig
    run_id: str
    keep: Callable[[RunContext], Awaitable[Any]] | None = None
    run: Any = None
    decoy_plan: DecoyPlan | None = None
    notification: UserNotification | None = None
    strict_instrument: bool = True
    strict_chemistry: bool = True
    targets: frozenset[str] = frozenset()
    persistent_peak_ids: frozenset[str] = frozenset()
    explained_peak_ids: frozenset[str] = frozenset()
    given: dict[str, Any] = field(default_factory=dict)
    series_by_peak: dict[str, dict] = field(default_factory=dict)
    chosen: list[dict] = field(default_factory=list)
    recorded: dict | None = None
    recorded_artifact_peak_ids: frozenset[str] | None = None
    held_peak_ids: frozenset[str] = frozenset()
    floors: dict[str, str] = field(default_factory=dict)
    kept: kept_measurement.KeptMeasurement | None = None
    #: The stages run so far, in order, a round's once per round.
    ran: list[Stage] = field(default_factory=list)
    # -- resolve
    #: What the sample's batch decided about it, read once
    #: (:data:`BATCH_INPUTS`).
    batch: BatchDecisions = field(default_factory=BatchDecisions)
    match_params: Any = None
    peaks_df: pd.DataFrame | None = None
    ionization_setup: dict | None = None
    mechanism_ids: list[str] = field(default_factory=list)
    mechanisms: list = field(default_factory=list)
    secondary_mechanisms: list = field(default_factory=list)
    own_mechanisms: list = field(default_factory=list)
    searched_mechanisms: list = field(default_factory=list)
    instrument_type: str | None = None
    resolved_profile: Any = None
    resolution: Callable[[float], float] | None = None
    source_line_cap: Any = None
    # -- claim
    reagent_rows: list[dict] = field(default_factory=list)
    reagent_peak_ids: set[str] = field(default_factory=set)
    reagent_offset: Any = None
    artifact_rows: list[dict] = field(default_factory=list)
    artifact_peak_ids: set[str] = field(default_factory=set)
    # -- propose
    stage_a: list[dict] = field(default_factory=list)
    stage_a_scored: pd.DataFrame | None = None
    confidence_calibration: dict | None = None
    confidence_curve: Any = None
    mass_accuracy: Any = None
    envelope: Any = None
    listed_formulas: frozenset[str] = frozenset()
    listed_ions: dict = field(default_factory=dict)
    model: MeasurementModel | None = None
    model_recorded: bool = False
    scoring: Any = None
    #: What the spectrum's rules ask lines at; set by :func:`_recorded_measurement`.
    line_gate: LineGate | None = None
    search: SearchProposal | None = None
    seeds: set[tuple[str, str]] = field(default_factory=set)
    #: The listed compounds the batch decided present, proposed for their
    #: peaks beside the known set's readings, by the sample's peak.
    decided: dict[str, ListedDecision] = field(default_factory=dict)
    # -- score
    seeded: Any = None
    unsearched: int = 0
    by_persistence: set[str] = field(default_factory=set)
    # -- score
    fit_by_seed: dict = field(default_factory=dict)
    seeded_frame: judging_loop.SeededFrame | None = None
    # -- elect
    stage_b: list[dict] = field(default_factory=list)
    same_ion_families: int = 0
    #: What became of each listed compound the batch decided present, by its
    #: peak (``batch_decisions.elect_listed``).
    listed_outcomes: dict[str, str] = field(default_factory=dict)
    #: What the batch's decisions did to the rows, by kind and outcome.
    batch_applied: dict = field(default_factory=dict)
    # -- refit, judge, cap
    lines: SpectrumLines | None = None
    judging: judging_loop.Judging | None = None
    judged: Any = None
    fragments: Any = None
    tiering: dict = field(default_factory=dict)
    # -- persist
    result: Any = None

    @property
    def claimed_peak_ids(self) -> set[str]:
        """The peaks the claim stage took out of every stage after it, and the
        ones the batch claimed for the source (:attr:`held_peak_ids`)."""
        return self.reagent_peak_ids | self.artifact_peak_ids | self.held_peak_ids

    @property
    def judged_mechanisms(self) -> list:
        """The mechanisms the lists are read and the judging names its rows'
        channels through (``sample_mechanisms.judged_mechanisms``)."""
        return sample_mechanisms.judged_mechanisms(
            self.searched_mechanisms, self.secondary_mechanisms, self.resolved_profile
        )

    @property
    def inherits(self) -> bool:
        """Whether the measurement model is a decoy's control's."""
        return self.path.measurement is Measurement.CONTROL and (
            self.decoy_plan is not None
        )

    async def progress(self, share: float) -> None:
        """Report how far the pass is, where it reports at all."""
        if self.notification is not None:
            await send_progress_user_notification(self.notification, share)


# --- resolve --------------------------------------------------------------------


async def resolve(ctx: RunContext) -> None:
    """Resolve the chemistry the pass searches and judges under.

    The sample's peaks and match parameters where the caller did not hand
    them over, its mechanisms, the reagent profile and context
    (``profiles.resolve_profile``) with the context its batch or dataset
    names, and the secondary channels its spectrum opens. A decoy that swaps
    adducts swaps them here, after the chemistry is resolved through the mode's
    own. A run records the resolution on its run before any stage after this
    one, so a run that fails still says what it would have searched. What the
    sample's batch decided about it is read here, once, with the versions of
    the stages that wrote it (:attr:`RunContext.batch`).
    """
    sample = ctx.sample
    if ctx.match_params is None:
        ctx.match_params = await default_match_params(sample.sample_item_id)
    if ctx.peaks_df is None:
        # The read opens the sample's peak list: it runs on a worker thread
        # so the event loop keeps answering every other request.
        ctx.peaks_df = await asyncio.to_thread(sample_inputs.load_sample_peaks, sample)
        await ctx.progress(0.1)
    ctx.ionization_setup = await sample_setup(sample.sample_item_id)
    ctx.mechanism_ids, ctx.mechanisms = await _mechanisms_of(
        sample, strict=ctx.strict_chemistry
    )
    ctx.instrument_type = _instrument_type(sample, strict=ctx.strict_instrument)
    plan = ctx.decoy_plan
    ctx.resolved_profile = resolve_profile(
        ctx.config,
        mechanism_notations=[m.ionization_mechanism for m in ctx.mechanisms],
        instrument_type=ctx.instrument_type,
        polarity=sample.polarity,
        # The context the sample's batch or dataset names, which a run on
        # `auto` takes before the profile's default (step 3.6). A decoy takes
        # what they named when its control ran, so it searches its control's
        # chemistry whatever they name now (`decoy.recorded_named_context`).
        named_context=(
            plan.named_context
            if ctx.inherits
            else await named_context_for(sample.sample_batch_id)
        ),
    )
    # -- Opportunistic channels (`sample_mechanisms._resolve_secondary_channels`).
    (
        ctx.resolved_profile,
        ctx.secondary_mechanisms,
    ) = await sample_mechanisms._resolve_secondary_channels(
        sample, ctx.resolved_profile, ctx.peaks_df
    )
    if plan is not None:
        runtime.logger.info(
            f"Run '{ctx.run_id}' is a decoy of run '{plan.control_run_id}' for "
            f"sample '{sample.sample_item_name}': {plan.decoy.arm}, scored at its "
            "control's calibration"
        )
    # The mode's own channels, read before a decoy swaps them: the reagent
    # pre-pass's ladder claim reads the source's lines through them, which are
    # the sample's whatever the arm (`prepasses._reagent_assignments`).
    ctx.own_mechanisms = sample_mechanisms._searched_mechanisms(
        ctx.mechanisms, ctx.secondary_mechanisms, ctx.resolved_profile
    )
    if plan is not None and plan.decoy.swap_adducts:
        # Swapped after the chemistry is resolved, which reads the mode's own
        # channels: the profile, the grid and the reagent are the sample's, and
        # only the adducts read through them are wrong. No secondary channel is
        # opened beside them.
        ctx.mechanisms = list(plan.mechanisms)
        ctx.mechanism_ids = [m.ionization_mechanism_id for m in ctx.mechanisms]
        ctx.secondary_mechanisms = []
        runtime.logger.info(
            f"Decoy run '{ctx.run_id}' searches "
            + ", ".join(
                f"{own} as {wrong or 'nothing'}" for own, wrong in plan.swapped.items()
            )
        )
    # What the batch decided about the sample, read once for every stage after
    # this one: the kinds this pass's stages read (:data:`BATCH_INPUTS`), the
    # rungs against the ladders of the profile the sample resolved to.
    ctx.batch = await read_batch_decisions(
        sample,
        profile=ctx.resolved_profile.profile.name,
        kinds=batch_kinds(ctx.path, decoy=plan is not None),
    )
    if ctx.resolved_profile.unavailable_channels:
        # Once per pass, and only for a channel the sample actually shows: an
        # operator can act on this by adding the mechanism.
        runtime.logger.info(
            f"Sample '{sample.sample_item_name}' shows the fingerprint of "
            f"{', '.join(ctx.resolved_profile.unavailable_channels)}, which this "
            "deployment has no ionization mechanism for; not searched."
        )
    if ctx.run is not None:
        await run_config._record_resolved_profile(
            ctx.run_id,
            ctx.config,
            ctx.resolved_profile,
            ionization_setup=ctx.ionization_setup,
            decoy_arm=None if plan is None else plan.record(),
            batch_decisions=ctx.batch.record(),
        )
    # The mechanisms a sample's channels are searched and read through: Stage A
    # reads the reference lists through them, the search enumerates through
    # them, and the passes after both read them.
    ctx.searched_mechanisms = sample_mechanisms._searched_mechanisms(
        ctx.mechanisms, ctx.secondary_mechanisms, ctx.resolved_profile
    )
    # The file's resolving power, read once: what one peak of the file holds
    # for every pass that predicts an isotope line - the reagent pre-pass, the
    # envelope match, the mass gate and the tiering pass.
    ctx.resolution = await kept_measurement.kept_resolution(ctx)


async def _mechanisms_of(sample, *, strict: bool) -> tuple[list[str], list]:
    """The sample's mode's mechanisms (``sample_mechanisms.fetch_sample_mechanisms``).

    A sample bound under no ionization mode has none to read, and the read
    fails on it. A path whose rules read the spectrum alone reads such a
    sample as declaring no mechanism instead of failing.
    """
    try:
        return await sample_mechanisms.fetch_sample_mechanisms(sample)
    except AttributeError:
        if strict:
            raise
        return [], []


def _instrument_type(sample, *, strict: bool) -> str | None:
    """The sample's instrument class, read off its file name.

    The parse raises for a sample that keeps no data file and whose name does
    not say. A path with nobody to report a failure to reads None instead and
    stands down where it needs one, as the ingest fold always has.
    """
    if strict:
        return get_instrument_type(sample.filename)
    try:
        return get_instrument_type(sample.filename)
    except ValueError:
        return None


# --- claim ----------------------------------------------------------------------


async def claim(ctx: RunContext) -> None:
    """Claim the source's peaks before any stage that proposes.

    The reagent pre-pass (``prepasses._reagent_assignments``): the source's own
    cluster ions are the brightest peaks of the spectrum and none of them is
    sample chemistry, so they are claimed here and taken out of what any later
    stage may propose. Ordering is the whole mechanism: nothing downstream is
    offered the peak, so nothing can overwrite it. The library ions the
    sample's batch decided present (step 4.2h), and the rungs its water
    ladder passed in the sample's acquisition segment (step 4.2j), are
    claimed on the batch's word where the file's own claim does not take
    them, each row naming the decision.

    Then the artifact pre-pass on the same footing: ringing around a very
    intense centroid is the detector's answer to a neighbour, not a species.
    """
    sample = ctx.sample
    (
        ctx.reagent_rows,
        ctx.reagent_peak_ids,
        ctx.reagent_offset,
    ) = prepasses._reagent_assignments(
        ctx.peaks_df,
        ctx.resolved_profile,
        sample.sample_item_id,
        ctx.run_id,
        resolution=ctx.resolution,
        own_mechanisms=ctx.own_mechanisms,
        sample_name=sample.sample_item_name,
        batch_species=ctx.batch.source_ions(),
    )
    batch_claims = Counter(
        row["provenance"]["reagent"]["reason"]
        for row in ctx.reagent_rows
        if not row.get("owner_peak_assignment_id")
        and (row.get("provenance") or {}).get("reagent", {}).get("reason")
        in (REASON_BATCH_KNOWN_SPECIES, REASON_BATCH_WATER_LADDER)
    )
    if batch_claims:
        ctx.batch_applied["claimed"] = dict(sorted(batch_claims.items()))
    if ctx.reagent_rows:
        runtime.logger.info(
            f"Reagent pre-pass claimed {len(ctx.reagent_rows)} of "
            f"{len(ctx.peaks_df)} peaks of sample '{sample.sample_item_name}' "
            f"for profile '{ctx.resolved_profile.profile.name}'"
        )
    ctx.artifact_rows, ctx.artifact_peak_ids = prepasses._artifact_assignments(
        ctx.peaks_df,
        ctx.instrument_type,
        sample.sample_item_id,
        ctx.run_id,
        claimed_peak_ids=ctx.reagent_peak_ids,
    )
    if ctx.artifact_rows:
        runtime.logger.info(
            f"Artifact pre-pass claimed {len(ctx.artifact_rows)} of "
            f"{len(ctx.peaks_df)} peaks of sample '{sample.sample_item_name}' "
            "as instrument ringing"
        )


# --- propose --------------------------------------------------------------------


async def propose(ctx: RunContext) -> None:
    """Propose the readings the path proposes (:class:`Proposal`).

    The known set, then the search where the config asks for it: what the
    search enumerates is what the known set left, and the measurement it ranks
    at is the one the known set's lines fitted. Beside the known set, each
    listed compound the sample's batch decided present is proposed for its
    peak (:attr:`RunContext.decided`).
    """
    proposal = ctx.path.proposal
    if proposal in (Proposal.KNOWN_AND_SEARCH, Proposal.KNOWN):
        await _propose_known_set(ctx)
        # Beside the known set: each listed compound the batch decided present,
        # for its peak, which the elect stage elects as the batch's own claim
        # takes a member (``batch_decisions.elect_listed``).
        ctx.decided = dict(ctx.batch.listed)
    if proposal is Proposal.KNOWN_AND_SEARCH and ctx.config.run_untargeted:
        await _propose_by_search(ctx)
    if proposal is Proposal.SEARCH:
        await _propose_on_representatives(ctx)
    if proposal in (Proposal.GIVEN, Proposal.PERSONS_FORMULA):
        await _propose_given(ctx)


async def _propose_known_set(ctx: RunContext) -> None:
    """Stage A: the known composition set, matched, scored and arbitrated.

    The curated target library plus (when loaded) the reference mirror
    (``pipeline._stage_a_assignments``), whose lines fit the measurement every
    later stage scores at: the width, the offset and the line-share
    tolerance. A decoy scores at its control's instead.
    """
    sample = ctx.sample
    plan = ctx.decoy_plan if ctx.inherits else None
    (
        ctx.stage_a,
        ctx.confidence_calibration,
        ctx.mass_accuracy,
        ctx.envelope,
        ctx.listed_formulas,
        ctx.stage_a_scored,
        ctx.confidence_curve,
    ) = await _stage_a_assignments(
        sample,
        ctx.config,
        ctx.match_params,
        ctx.mechanism_ids,
        ctx.searched_mechanisms,
        ctx.run_id,
        excluded_peak_ids=ctx.claimed_peak_ids,
        fallback_sigma_ppm=ctx.resolved_profile.fallback_sigma_ppm,
        known_window=ctx.resolved_profile.context.known_window,
        reagent_offset=ctx.reagent_offset,
        ionization_setup=ctx.ionization_setup,
        reagent_profile=ctx.resolved_profile.profile.name,
        inherited_model=None if plan is None else plan.model,
        inherited_envelope=None if plan is None else plan.envelope,
        resolution=ctx.resolution,
    )
    runtime.logger.info(
        f"Stage A assigned {len(ctx.stage_a)} of {len(ctx.peaks_df)} peaks from "
        "the known target library"
    )
    envelope = ctx.envelope
    if envelope.inherited is not None:
        runtime.logger.info(
            f"Sample '{sample.sample_item_name}' reads an isotope line's share at "
            f"its control run's {envelope.fit.form} tolerance, level "
            f"{envelope.fit.level}"
        )
    else:
        runtime.logger.info(
            f"Sample '{sample.sample_item_name}' reads an isotope line's share at "
            f"the {envelope.fit.form} tolerance, level {envelope.fit.level}, over "
            f"{envelope.fit.lines.lines} of its committed lines "
            f"({envelope.fit.lines.absent} predicted lines absent)"
            + (
                f", pooled over {envelope.fit.pooled_files} files"
                if envelope.fit.pooled_files
                else ""
            )
        )
    await ctx.progress(0.4)
    # The run's measurement model at its first named point (v1): Stage A's
    # fit on the target library's lines, with the class's width and the reagent
    # lines' centre where they stand in, and the line-share tolerance fitted on
    # Stage A's envelopes. A decoy's is its control's, unchanged.
    ctx.model = (
        plan.model
        if plan is not None
        else MeasurementModel.v1(
            ctx.mass_accuracy,
            ctx.resolved_profile.fallback_sigma_ppm,
            envelope.tolerance,
            envelope.record(),
        )
    )
    _read_at_model(ctx)


def _read_at_model(ctx: RunContext) -> None:
    """Resolve what the search ranks at from the model: how this sample's
    envelopes are predicted and matched (``engine.pattern_scoring_for``), and
    on a TOF the window it searches. Resolved before the search because the
    judging reads it whether or not the search ran: a pass of the known set
    alone still has committed rows whose peaks a neighbour's envelope may
    already predict. On a TOF the window is bounded by the model (step 3.6):
    a candidate further from the sample's centre than the mass gate's floor
    is one it would hold below assignability whatever its fit, so it is no
    candidate to search for."""
    ctx.scoring = pattern_scoring_for(ctx.match_params, ctx.model, ctx.resolution)
    ctx.resolved_profile = with_fitted_window(
        ctx.resolved_profile,
        model=ctx.model,
        sigmas=BELOW_ASSIGNABILITY_Z,
        instrument_type=ctx.instrument_type,
    )


async def _propose_by_search(ctx: RunContext) -> None:
    """The untargeted search over the peaks the known set left.

    The claim stage's peaks and the known set's are out of it from the start,
    so it never searches them: a reagent cluster has an ordinary elemental
    composition and the search would fit a neutral to it happily. Every
    monoisotopic peak a list read is put to the search too; there the list's
    reading is one candidate beside the grid's (``engine.settle_list_election``).
    A chlorine or bromine reading is then proposed from its twin line
    (``judging_loop._reread_halogen_twins``).
    """
    sample = ctx.sample
    config = ctx.config
    peaks_df = ctx.peaks_df
    assigned_peak_ids = set(ctx.claimed_peak_ids)
    assigned_peak_ids.update(row["sample_peak_id"] for row in ctx.stage_a)
    eligible_df = peaks_df[
        ~peaks_df["sample_peak_id"].isin(assigned_peak_ids)
        & (peaks_df["intensity"] >= config.peak_intensity_threshold)
        & (peaks_df["intensity"] > 0)
    ]
    remainder_df, search_scope = untargeted_targets(
        eligible_df, config.max_untargeted_peaks, MAX_UNTARGETED_PEAKS_CEILING
    )
    remainder_df = remainder_df.sort_values("mz")
    if search_scope["limited"]:
        # A peak nobody searched is not a peak nobody could explain, and only
        # the run can say which of the two a blank row is.
        runtime.logger.info(
            f"Untargeted stage for sample '{sample.sample_item_name}' searches "
            f"{search_scope['searched_peaks']} of {search_scope['eligible_peaks']} "
            "unexplained peaks"
            + (
                f"; the rest are past the {search_scope['ceiling']}-peak ceiling"
                if search_scope["at_ceiling"]
                else ""
            )
        )
    notations, mechanism_id_by_notation = (
        sample_mechanisms._untargeted_ionization_notations(ctx.searched_mechanisms)
    )
    # One positionally-indexed frame feeds both the search and the join back.
    # The finder returns rows in the order it was given them, so position - not
    # float m/z equality - is what maps a result to the peak it came from.
    search_peaks_df = peaks_df.reset_index(drop=True)
    search = ctx.search = SearchProposal(
        remainder=remainder_df,
        frame=search_peaks_df,
        columns=[
            column
            for column in sample_inputs.SEARCH_PEAK_COLUMNS
            if column in search_peaks_df
        ],
        mechanism_id_by_notation=mechanism_id_by_notation,
        scope=search_scope,
        excluded=assigned_peak_ids,
    )
    # Every monoisotopic peak a list read is put to the search too. There the
    # list's reading is one candidate beside the grid's, and a rival takes the
    # peak only where its evidence beats the reading's by the list's prior
    # (engine.LIST_PRIOR_WEIGHT); where none does, the grid's rivals are
    # counted into the reading's density.
    readings = (
        list_readings(
            ctx.stage_a,
            search_peaks_df,
            sample_mechanisms._notation_by_id(ctx.searched_mechanisms),
        )
        if notations
        else {}
    )
    if not notations or (remainder_df.empty and not readings):
        skip_reason = (
            "no polarity-compatible ionization mechanisms"
            if not notations
            else "no eligible unassigned peaks and no list hits"
        )
        runtime.logger.info(f"Skipping untargeted stage: {skip_reason}")
        return
    resolved_profile = ctx.resolved_profile
    search_config = resolved_profile.search_config(notations)
    secondary = sorted(resolved_profile.minor_channels)
    runtime.logger.info(
        f"Untargeted stage for sample '{sample.sample_item_name}' searches profile "
        f"'{resolved_profile.profile.name}' / context "
        f"'{resolved_profile.context.name}': {search_config.element_count_ranges} "
        f"at {search_config.mass_range_ppm} ppm"
        + (f"; secondary channels {', '.join(secondary)}" if secondary else "")
    )
    # Stage B opts into the Senior/RDBE feasibility cut: the peak-centric
    # engine wants chemically impossible formulas gone before arbitration. The
    # resolved context's ratio windows ride along with it.
    heuristics_config = resolved_profile.heuristics_config()
    # The whole spectrum is the context, the remainder and the list peaks are
    # what is enumerated. An isotope envelope is scored against every peak the
    # frame holds, so an isotopologue is found wherever it sits - below the
    # stage's intensity threshold, past its cap, or on a peak another pass
    # already owns - instead of only inside the searched set. The noise
    # estimate rides along when the file has one, and so do the scan counts.
    frame = search_peaks_df[search.columns]
    scoring = ctx.scoring
    model = ctx.model
    search.snapshot = scoring_snapshot(scoring, frame)
    runtime.logger.info(
        f"Untargeted stage for sample '{sample.sample_item_name}' ranks at the "
        f"v{model.version} model, {scoring.sigma_ppm:.3f} ppm "
        f"({model.sigma_source}, anchors {dict(model.anchors)}), "
        f"offset {scoring.mu_ppm:+.3f} ppm ({model.mu_source})"
    )
    search.started = time.perf_counter()
    # assign_compositions is synchronous and CPU-bound: it runs on a worker
    # thread so the event loop keeps answering every other request.
    matches_df, _ = await asyncio.to_thread(
        assign_compositions,
        frame,
        search_config,
        heuristics_config,
        targets=remainder_df["mz"].tolist()
        + [reading.mz for reading in readings.values()],
        scoring=scoring,
        known={reading.mz: reading for reading in readings.values()},
        known_prior=LIST_PRIOR_WEIGHT,
        # A radical is never held at assigned, so it is no plausible rival to a
        # list's reading (the plan owner's answer).
        closed_shell_rivals=True,
    )
    election = search.election = settle_list_election(ctx.stage_a, matches_df, readings)
    ctx.stage_a = election.stage_a
    search_scope["list_hits"] = election.summary
    # -- A chlorine or bromine reading, proposed from its twin line rather than
    # from an opened grid (step 3.6): a searched peak whose 37Cl or 81Br twin
    # sits unexplained at the exact offset is searched again over carbon,
    # hydrogen, oxygen and that halogen, and the reading replaces the peak's
    # where it passes every gate.
    search.matches, search.twin_records = await judging_loop._reread_halogen_twins(
        election.matches,
        frame,
        resolved_profile=resolved_profile,
        listed_formulas=ctx.listed_formulas,
        searched_mzs=remainder_df["mz"],
        claimed_mzs=search_peaks_df.loc[
            search_peaks_df["sample_peak_id"].isin(
                assigned_peak_ids - election.released
            ),
            "mz",
        ],
        search_config=search_config,
        heuristics=heuristics_config,
        scoring=scoring,
        candidate_threshold=config.candidate_threshold,
        search_scope=search_scope,
        sample_name=sample.sample_item_name,
    )
    search.ran = True


async def _propose_on_representatives(ctx: RunContext) -> None:
    """The batch's untargeted search over one sample's representative peaks,
    in the context of its whole spectrum (``batch_untargeted``).

    The frame is the sample's peaks at or above the config's intensity floor,
    and the persistent anchors' representatives under it: a peak the search
    enumerates is one of the peaks it reads (``admitted_targets``). A peak the
    claim stage took is no target, nor is one the batch ledger explains
    (``explained_peak_ids``), as a run's search never searches a peak its
    claims or its Stage A took.

    It ranks at the measurement the sample's latest run recorded
    (:func:`_recorded_measurement`), since no Stage A runs here. The lists a
    run of the sample reads are read for their formulas alone, never matched:
    they corroborate a reading through a secondary channel, tell the partner
    gate what they name, and can open a halogen to the twin re-read, as in a
    run. That re-read follows the search, with a peak unexplained where neither
    the claim stage nor the batch ledger explains it (``explained_peak_ids``):
    the batch's frame, as a run's is its Stage A and its pre-passes.
    """

    sample = ctx.sample
    config = ctx.config
    peaks_df = ctx.peaks_df
    frame = (
        peaks_df[
            (peaks_df["intensity"] > 0)
            & (
                (peaks_df["intensity"] >= config.peak_intensity_threshold)
                | peaks_df["sample_peak_id"].isin(ctx.persistent_peak_ids)
            )
        ]
        .sort_values("mz")
        .reset_index(drop=True)
    )
    targets, scope, by_persistence = admitted_targets(
        frame,
        set(ctx.targets) - ctx.claimed_peak_ids - ctx.explained_peak_ids,
        set(ctx.persistent_peak_ids),
        peak_intensity_threshold=config.peak_intensity_threshold,
        max_untargeted_peaks=config.max_untargeted_peaks,
        ceiling=MAX_UNTARGETED_PEAKS_CEILING,
    )
    ctx.unsearched = scope["eligible_peaks"] - scope["searched_peaks"]
    notations, mechanism_id_by_notation = (
        sample_mechanisms._untargeted_ionization_notations(ctx.searched_mechanisms)
    )
    search = ctx.search = SearchProposal(
        remainder=targets,
        frame=frame,
        columns=[
            column for column in sample_inputs.SEARCH_PEAK_COLUMNS if column in frame
        ],
        mechanism_id_by_notation=mechanism_id_by_notation,
        scope=scope,
        excluded=ctx.claimed_peak_ids | set(ctx.explained_peak_ids),
    )
    await _recorded_measurement(ctx)
    if targets.empty:
        ctx.by_persistence = by_persistence
        return
    if scope["limited"]:
        runtime.logger.info(
            f"Batch untargeted search on sample '{sample.sample_item_name}' "
            f"searches {scope['searched_peaks']} of {scope['eligible_peaks']} "
            "representative peaks"
        )
    if not notations:
        # The mode's own mechanisms decide whether there is anything to search
        # at all (`sample_mechanisms._searched_mechanisms`).
        runtime.logger.info(
            f"Untargeted batch search skips sample '{sample.sample_item_name}': "
            "no polarity-compatible ionization mechanisms."
        )
        return
    ctx.by_persistence = by_persistence
    resolved_profile = ctx.resolved_profile
    ctx.listed_formulas = listed_formula_identities(
        await sample_inputs._fetch_known_isotopes(
            sample,
            ctx.match_params.isotope_abundance_threshold,
            ctx.mechanism_ids,
            ctx.searched_mechanisms,
            known_window=resolved_profile.context.known_window,
            resolution=ctx.resolution,
        )
    )
    search_config = resolved_profile.search_config(notations)
    heuristics_config = resolved_profile.heuristics_config()
    runtime.logger.info(
        f"Untargeted batch search of sample '{sample.sample_item_name}' searches "
        f"profile '{resolved_profile.profile.name}' / context "
        f"'{resolved_profile.context.name}': {resolved_profile.element_ranges} "
        f"at {resolved_profile.mz_precision_ppm} ppm"
    )
    scored_frame = frame[search.columns]
    search.started = time.perf_counter()
    matches_df, _ = await asyncio.to_thread(
        assign_compositions,
        scored_frame,
        search_config,
        heuristics_config,
        targets=targets["mz"].tolist(),
        scoring=ctx.scoring,
    )
    search.matches, search.twin_records = await judging_loop._reread_halogen_twins(
        matches_df,
        scored_frame,
        resolved_profile=resolved_profile,
        listed_formulas=ctx.listed_formulas,
        searched_mzs=targets["mz"],
        claimed_mzs=frame.loc[frame["sample_peak_id"].isin(search.excluded), "mz"],
        search_config=search_config,
        heuristics=heuristics_config,
        scoring=ctx.scoring,
        candidate_threshold=config.candidate_threshold,
        search_scope=scope,
        sample_name=sample.sample_item_name,
    )
    search.ran = True


async def _propose_given(ctx: RunContext) -> None:
    """One given formula per member: the reading the batch decided for each of
    its anchors this sample holds a member of (:attr:`RunContext.given`), each
    one an ion to measure through its mechanism.

    A batch decision is proposed to a sample as a list proposes its formula to
    a peak: the score stage measures it on the sample's own peaks, and the
    elect stage elects one reading per peak as Stage A elects a list's. It is
    measured at the sample's recorded measurement
    (:func:`_recorded_measurement`).

    Under a run's rules each reading is then judged as the row a run of the
    sample makes of it (:func:`_read_as_a_run_reads`), so the lists a run of
    the sample reads are read here, as the batch search reads them, never
    matched: for the ions they hold, whose readings a run commits as a list's
    rows, and for the neutrals they name, which corroborate a reading through
    a secondary channel. A person's choice reads none: its rules are the
    spectrum's.

    A given reading through a secondary channel the sample did not open is
    judged as a run of the sample that opened it judges it (MAS-1039,
    :attr:`RunContext.judged_mechanisms`).
    """
    ctx.seeds = {
        (annotation.formula, annotation.ionization_mechanism_id)
        for annotation in ctx.given.values()
        if annotation.formula and annotation.ionization_mechanism_id
    }
    await _recorded_measurement(ctx)
    if ctx.path.rules is Rules.RUN:
        _open_given_channels(ctx)
        ctx.listed_formulas, ctx.listed_ions = await kept_measurement.kept_lists(
            ctx, lambda: _read_lists(ctx)
        )


async def _read_lists(ctx: RunContext) -> tuple[frozenset[str], dict]:
    """The neutrals the sample's lists name and the ions they hold, read as
    a run reads its known set (:func:`_propose_given`)."""
    known = await sample_inputs._fetch_known_isotopes(
        ctx.sample,
        ctx.match_params.isotope_abundance_threshold,
        ctx.mechanism_ids,
        ctx.judged_mechanisms,
        known_window=ctx.resolved_profile.context.known_window,
        resolution=ctx.resolution,
    )
    return listed_formula_identities(known), listed_ions(known)


def _open_given_channels(ctx: RunContext) -> None:
    """Read the given readings' unopened secondary channels as opened ones
    (``sample_mechanisms.given_secondary_channels``): the hold, the partner
    gate and the lists then read a reading through one as a run that opened
    it reads it (:attr:`RunContext.judged_mechanisms`)."""
    channels = sample_mechanisms.given_secondary_channels(
        [mechanism_id for _, mechanism_id in ctx.seeds],
        ctx.searched_mechanisms,
        ctx.secondary_mechanisms,
        ctx.resolved_profile,
    )
    if not channels:
        return
    ctx.resolved_profile = replace(ctx.resolved_profile, given_channels=channels)
    runtime.logger.info(
        f"Sample '{ctx.sample.sample_item_name}' reads batch decisions through "
        f"{', '.join(channels)}, which its spectrum did not open: judged as "
        "an opened channel's readings"
    )


async def _recorded_measurement(ctx: RunContext) -> None:
    """The measurement a path with no Stage A of its own scores and judges at:
    the model the sample's latest run recorded (:attr:`Measurement.RECORDED`),
    read with the rest of that run's config in one query
    (``model_readers.recorded_measurement``), so the width, the centre and the
    line-share tolerance come from one run even where another completes
    mid-search. The caps judge at the same model, as a decoy's judge at its
    control's. A sample no run recorded one for is read at the class's width
    and the reagent lines the claim stage read, taken only beyond it, as a run
    whose library fitted nothing is; its refit stage then refits that model on
    what the path committed and the source's own lines, as a run's does. A
    TOF's window is bounded by the model, as a run's own search is by its v1
    (step 3.6).
    """
    recorded, ctx.model, ctx.model_recorded = await model_readers.recorded_measurement(
        ctx.sample,
        ctx.config,
        recorded=ctx.recorded,
        peaks_df=ctx.peaks_df,
        resolved_profile=ctx.resolved_profile,
        reagent_offset=ctx.reagent_offset,
    )
    _read_at_model(ctx)
    ctx.line_gate = _persons_line_gate(ctx, recorded)


def _persons_line_gate(ctx: RunContext, recorded: dict | None) -> LineGate:
    """The gate a person's choice is asked its lines at (:func:`_cap_by_the_spectrum`).

    The one the sample's run asked its own rows' lines at, where it recorded
    one. In a sample no run asked lines in - one the ingest folded straight
    into the batch ledger, or whose run was tiered before rule set 11 - the
    gate a run of the sample would ask at: the measurement it is scored at,
    that is its match window, envelope floor and line-share tolerance, and its
    instrument class (``tiering.LineGate.at``), as a propagated reading of
    that sample is asked. So the batch's *Use this* asks its lines in every
    sample. So every path that reads its recorded measurement has a gate. A
    hand assignment on a run that asked no line is still asked none, not for
    want of a gate: it reads no peaks there (``curation._persons_choice``).
    """
    gate = LineGate.from_record((recorded or {}).get(TIERING_KEY))
    if gate is not None:
        return gate
    return LineGate.at(
        mz_tolerance_ppm=ctx.scoring.mz_tolerance_ppm,
        abundance_floor=ctx.scoring.abundance_floor,
        envelope_tolerance=ctx.model.envelope_tolerance,
        instrument_type=ctx.instrument_type,
    )


# --- score ----------------------------------------------------------------------


async def score(ctx: RunContext) -> None:
    """Measure every candidate the way Stage A measures one.

    The known set's readings were measured as ions in Stage A's own match
    pass. Every reading the search committed to is measured again here, as an
    ion, through one match pass over the sample, gated by the run's match
    params (``judging_loop._seeded_rescore``): the second measurement is what the
    row is tiered on, so a search's "assigned" and a list's mean one thing.
    Both are read at the run's model, the one the search ranked at; the
    winners fit none of their own.
    """
    if ctx.path.proposal in (Proposal.GIVEN, Proposal.PERSONS_FORMULA):
        if not ctx.seeds:
            # A hand assignment's reading comes measured.
            return
        # The given readings, each an ion measured as a list's is: in one match
        # pass over the sample's peaks, gated by its match parameters and fit
        # at its recorded model (``seeded_scoring.score_seeds``). Under a
        # run's rules a reading the sample's lists hold is the list's row a
        # run makes, and Stage A measures that one without its lines on the
        # peaks the claim stage took, before it gates and fits: so is it here.
        # Every other reading is the search's, whose re-score reads a claimed
        # peak as part of the pattern; the elect stage drops it there.
        # A person's choice reads no lists (:func:`_propose_given`), so it
        # holds none and keeps every line.
        listed_seeds = {
            seed
            for seed in ctx.seeds
            if (formula_identity(seed[0]), str(seed[1])) in ctx.listed_ions
        }
        # What the pass measured of the sample is kept (``kept_measurement``).
        ctx.seeded = await kept_measurement.score_kept(ctx, score_seeds, listed_seeds)
        ion_by_seed, _fits, _errors, scored_df = ctx.seeded
        if not scored_df.empty:
            ctx.seeded_frame = judging_loop.SeededFrame(scored_df, ion_by_seed)
        return
    search = ctx.search
    if search is None or not search.ran:
        return
    ctx.fit_by_seed, ctx.seeded_frame = await judging_loop._seeded_rescore(
        ctx.sample,
        ctx.match_params,
        untargeted_seeds(
            search.matches,
            search.mechanism_id_by_notation,
            to_custom_element_format,
        ),
        model=ctx.model,
        resolution=ctx.resolution,
    )


# --- elect ----------------------------------------------------------------------


async def elect(ctx: RunContext) -> None:
    """One reading per peak: the ledger's committed rows.

    The search's matches become rows on the peaks it was offered and no
    earlier stage took (``untargeted_rows.untargeted_matches_to_peak_assignments``),
    carrying the list readings it displaced and the twins it read. Given
    readings are elected as Stage A elects a list's. Under a run's rules none
    is elected on a peak the claim stage took, and each is then the row a run
    of the sample makes of it, a list's or the search's
    (:func:`_read_as_a_run_reads`). Then, on the rows of both:

    - a list's matches carry the other readings of their ion
      (``stage_a_pass._record_same_ion_readings``), which reads no tier and so is
      written once however many rounds judge them;
    - and a reading the batch's series detection proposed for its peak
      carries the series' record, which the tiering pass counts as a second
      observation (step 4.1), the detection's own measurement of it included
      (:attr:`RunContext.series_by_peak`). Not on a decoy arm: the batch
      ledger is the control's chemistry, not the arm's.

    The batch's decisions enter here as :data:`BATCH_INPUTS` says. Before
    any of the above, each listed compound the batch decided present is
    elected on its peak as the batch's own claim takes a member
    (``batch_decisions.elect_listed``). After the same-ion readings, and
    before the series' records, each row the batch stripped as a parent's
    isotope line is read as that line (``batch_decisions.read_strips``): the
    judging then reads the line, and the series, as the batch decided them,
    and its fragment claim reads the ledger the strips left.
    """
    # Off the event loop, each: rows from a large frame are seconds of pandas.
    if ctx.path.proposal in (Proposal.GIVEN, Proposal.PERSONS_FORMULA) and (
        ctx.seeded is not None
    ):
        await asyncio.to_thread(_elect_given, ctx)
    if ctx.path.rules is Rules.SPECTRUM:
        _elect_persons_choice(ctx)
        return
    await asyncio.to_thread(_elect_rows, ctx)


def _elect_given(ctx: RunContext) -> None:
    """The given readings elected (:func:`elect`)."""
    sample = ctx.sample
    config = ctx.config
    frame = ctx.seeded[3]
    if ctx.path.rules is Rules.RUN:
        # No reading is elected on a peak the claim stage took, as a run
        # elects none there: Stage A drops an ion whose monoisotopic line a
        # pre-pass claimed and any line that landed on a claimed peak, and
        # the search drops its rows on them
        # (``engine.drop_ions_claimed_elsewhere``). A list's reading was
        # measured without them (:func:`score`); the search's is dropped
        # once measured, its fit reading them as the pattern. A person's
        # formula takes the peak it was chosen for.
        frame = drop_ions_claimed_elsewhere(frame, ctx.claimed_peak_ids)
    # One reading per peak among the given ones, elected as Stage A elects
    # a list's: the measured ion owns the peak its gated line paired to,
    # an isotopologue beside its ion's monoisotopic row, and the rest are
    # its alternatives (``engine.invert_matches_to_peak_assignments``).
    # Uncalibrated, as a batch decision's measurement always was.
    rows = invert_matches_to_peak_assignments(
        frame,
        sample_item_id=sample.sample_item_id,
        peak_assignment_run_id=ctx.run_id,
        candidate_threshold=config.candidate_threshold,
        assigned_threshold=config.assigned_threshold,
        max_alternatives=config.max_alternatives,
        instrument=ctx.instrument_type,
        calibration=None,
    )
    judging_loop.raise_to_floors(rows, ctx.floors)
    if ctx.path.rules is Rules.RUN:
        ctx.stage_a, ctx.stage_b = _read_as_a_run_reads(ctx, rows)
    else:
        ctx.stage_a = rows


def _elect_rows(ctx: RunContext) -> None:
    """The search's rows, and what every path's rows carry (:func:`elect`)."""
    sample = ctx.sample
    config = ctx.config
    search = ctx.search
    if search is not None and search.ran:
        election = search.election
        released = set() if election is None else election.released
        ctx.stage_b = untargeted_matches_to_peak_assignments(
            search.matches,
            peaks_df=search.frame,
            sample_item_id=sample.sample_item_id,
            peak_assignment_run_id=ctx.run_id,
            candidate_threshold=config.candidate_threshold,
            assigned_threshold=config.assigned_threshold,
            mechanism_id_by_notation=search.mechanism_id_by_notation,
            formula_formatter=to_custom_element_format,
            max_alternatives=config.max_alternatives,
            minor_channels=ctx.resolved_profile.minor_channels,
            excluded_peak_ids=search.excluded - released,
            fit_by_seed=ctx.fit_by_seed,
        )
        # Each names a reading at the head of the row's alternatives, under
        # the cap: the reading the twin replaced leads, then the list reading
        # the row displaced.
        held = (
            0
            if election is None
            else record_displaced_list_readings(
                ctx.stage_b, election.displaced, config.max_alternatives
            )
        )
        record_twins(ctx.stage_b, search.twin_records, config.max_alternatives)
        if election is None:
            runtime.logger.info(
                f"Untargeted search of sample '{sample.sample_item_name}' read "
                f"{len(ctx.stage_b)} rows on its {len(search.remainder)} "
                "representative peaks and their lines "
                f"({time.perf_counter() - search.started:.1f} s)"
            )
        else:
            runtime.logger.info(
                f"Stage B assigned {len(ctx.stage_b)} of {len(search.remainder)} "
                "remaining peaks via untargeted search; of "
                f"{election.summary['measured']} list hits it measured, "
                f"{election.summary['with_rivals']} kept their peak against a "
                f"closed-shell rival and {election.summary['taken_by_rivals']} "
                f"lost it to one ({held} held by the search) "
                f"({time.perf_counter() - search.started:.1f} s)"
            )
    if ctx.decided:
        _elect_batch_listed(ctx)
    ctx.same_ion_families = _record_stage_a_families(ctx)
    if ctx.batch.strips:
        _read_batch_strips(ctx)
    # A decoy arm reads no series of the batch's (:data:`BATCH_INPUTS`), and
    # stamps none of its own.
    if ctx.decoy_plan is None:
        batch_lines.stamp_series(
            ctx.stage_a + ctx.stage_b, {**ctx.batch.series, **ctx.series_by_peak}
        )


def _record_stage_a_families(ctx: RunContext) -> int:
    """Stage A's same-ion families, each row's read through the mechanisms
    ``sample_mechanisms.family_mechanisms`` gives it."""
    return sum(
        stage_a_pass._record_same_ion_readings(
            rows,
            searched_mechanisms=mechanisms,
            resolved_profile=ctx.resolved_profile,
            max_alternatives=ctx.config.max_alternatives,
        )
        for rows, mechanisms in sample_mechanisms.family_mechanisms(
            ctx.stage_a, ctx.searched_mechanisms, ctx.judged_mechanisms
        )
    )


def _elect_batch_listed(ctx: RunContext) -> None:
    """The listed compounds the batch decided present, elected on their peaks
    (``batch_decisions.elect_listed``), measured on the axis as stored: a
    decoy arm claims what its control claims. Each elected or lifted row
    stands at candidate on the decision (:attr:`RunContext.floors`), which
    the refit's re-score lowers no row below and only a cap does, as the
    batch's own judging holds its members (``batch_rejudge.floors_of``)."""
    source = sample_mechanisms._source_axis(ctx.peaks_df)
    ctx.stage_a, ctx.stage_b, ctx.listed_outcomes = elect_listed(
        ctx.stage_a,
        ctx.stage_b,
        ctx.decided,
        claimed_peak_ids=ctx.claimed_peak_ids,
        peaks=ctx.peaks_df,
        source_mz=dict(
            zip(
                source["sample_peak_id"].astype(str),
                source["mz"].astype(float),
                strict=True,
            )
        ),
        sample_item_id=ctx.sample.sample_item_id,
        run_id=ctx.run_id,
    )
    for peak_id, outcome in ctx.listed_outcomes.items():
        if outcome in (LISTED_ELECTED, LISTED_LIFTED):
            ctx.floors[peak_id] = TIER_CANDIDATE
    outcomes = Counter(ctx.listed_outcomes.values())
    ctx.batch_applied["listed"] = dict(sorted(outcomes.items()))
    runtime.logger.info(
        f"Sample '{ctx.sample.sample_item_name}' read {len(ctx.decided)} listed "
        "compounds its batch decided present: "
        + ", ".join(f"{count} {why}" for why, count in sorted(outcomes.items()))
    )


def _read_batch_strips(ctx: RunContext) -> None:
    """The rows the batch stripped as a parent's isotope line, read as that
    line on the elected rows (``batch_decisions.read_strips``), before the
    judging reads the ledger: no peak the claim stage took is read so. A line
    read off a peak no stage read joins Stage A's rows."""
    elected = len(ctx.stage_a) + len(ctx.stage_b)
    rows, outcomes = read_strips(
        ctx.stage_a + ctx.stage_b,
        ctx.batch.strips,
        claimed_peak_ids=ctx.claimed_peak_ids,
        peaks=ctx.peaks_df,
        sample_item_id=ctx.sample.sample_item_id,
        run_id=ctx.run_id,
    )
    ctx.stage_a = [*ctx.stage_a, *rows[elected:]]
    ctx.batch_applied["strips"] = dict(sorted(outcomes.items()))
    runtime.logger.info(
        f"Sample '{ctx.sample.sample_item_name}' read {len(ctx.batch.strips)} rows "
        "its batch stripped as a parent's isotope line: "
        + ", ".join(f"{count} {why}" for why, count in sorted(outcomes.items()))
    )


def _read_as_a_run_reads(
    ctx: RunContext, rows: list[dict]
) -> tuple[list[dict], list[dict]]:
    """Each given reading as the row a run of the sample makes of it, so the
    judging asks it what a run asks of that row.

    A run commits an ion its lists hold through Stage A, as that list's row
    (``engine.listed_ions``): the target library's names its compound, and
    either carries the reference lists' names for its neutral. Every other
    reading a run commits is the formula search's, and so is such a given
    one: the batch searched for it, or a series or an import named it, and a
    run of the sample would have had to search for it too. The search's rows
    (``source`` untargeted) then get what the search gives its own before any
    judging: the other readings of their ion it would have held, which the
    partner gate and the cross-channel pass weigh
    (``engine.record_same_ion_readings``). The judging then caps a reading
    through a secondary channel as it caps the search's, a list naming its
    neutral lifting it (``engine.cap_second_channel``), and the tiering pass
    asks them the rules it asks only of the search's rows: the lone peak, the
    radical, the context's priors, the nitrate skeleton and the shapes of a
    formula found by mass.

    :param ctx: The pass, its lists read (:func:`_propose_given`).
    :param rows: The elected rows, each isotopologue beside its owner.
    :return: The lists' rows and the search's, as Stage A's and the search's
        rows of a run.
    """
    listed: list[dict] = []
    searched: list[dict] = []
    for row in rows:
        ion = ctx.listed_ions.get(
            (
                formula_identity(row.get("assigned_formula")),
                str(row.get("ionization_mechanism_id")),
            )
        )
        if ion is None:
            row["source"] = SOURCE_UNTARGETED
            searched.append(row)
            continue
        row["target_compound_id"] = ion.target_compound_id
        if ion.reference_identities:
            row["provenance"]["reference_identities"] = ion.reference_identities
        listed.append(row)
    for family_rows, mechanisms in sample_mechanisms.family_mechanisms(
        searched, ctx.searched_mechanisms, ctx.judged_mechanisms
    ):
        notations, mechanism_id_by_notation = (
            sample_mechanisms._untargeted_ionization_notations(mechanisms)
        )
        record_same_ion_readings(
            family_rows,
            mechanism_id_by_notation=mechanism_id_by_notation,
            search_config=ctx.resolved_profile.search_config(notations),
            heuristics_config=ctx.resolved_profile.heuristics_config(),
            formula_formatter=to_custom_element_format,
            max_alternatives=ctx.config.max_alternatives,
        )
    return listed, searched


def _elect_persons_choice(ctx: RunContext) -> None:
    """A person's formula takes the peak it was chosen for, tiered on its
    evidence under the path's bands as both engine stages tier theirs: the hand
    chooses the formula, but what tier the choice earns is still measured. A
    pin's reading is the elected one on each member's peak; a hand
    assignment's is the row given. Nothing else of a run's election is asked
    of it: no list holds it, no family is written and no fragment claimed."""
    config = ctx.config
    for row in ctx.chosen:
        row["tier"] = tier_for_evidence(
            evidence_for(row.get("fit_score"), row.get("assigned_formula")),
            candidate_threshold=config.candidate_threshold,
            assigned_threshold=config.assigned_threshold,
        )
    ctx.stage_a = [*ctx.stage_a, *ctx.chosen]
    ctx.fragments = FragmentClaims(
        rows=ctx.stage_a, fragments=[], peak_ids=set(), summary={"ladders": []}
    )


# --- refit, judge, cap ----------------------------------------------------------


async def refit(ctx: RunContext) -> None:
    """Refit the measurement model on the ledger as the reading changes so far
    leave it: its second named point (v2), over the run's corroborated commits
    and the source's own lines (``mass_gate.measure_mass_gate``). Where the
    refit moves the model beyond the bound, every row is scored again at it
    before any cap (``judging_loop.rescore_rows``), so the caps read the model the
    rows were scored at; a decoy and a path reading the sample's recorded
    model refit nothing. It records every row's ceiling and lowers none.

    The first round sets up the judging (``judging_loop.Judging``) over the elected
    rows, with what every round reads: the sample's peaks, read for which
    lines each envelope's peaks hold, and the run's channels, bands, context
    and measurement.
    """
    if ctx.path.rules is Rules.SPECTRUM:
        # A person's choice refits nothing: it is asked at what was recorded.
        return
    if ctx.judging is None:
        ctx.lines = SpectrumLines.from_peaks(ctx.peaks_df, ctx.resolution)
        # The cap on how bright a source line may be to anchor the mass gate,
        # which the m/z calibration node puts on its calibrants.
        ctx.source_line_cap = await judging_loop._source_line_cap(ctx.sample)
        ctx.judging = _judging_for(ctx)
    # The judging's rounds are seconds on a large ledger: off the event loop.
    await asyncio.to_thread(ctx.judging.refit)


def _judging_for(ctx: RunContext) -> judging_loop.Judging:
    """The judging rounds over the elected rows, under the run's rules, at
    its model."""
    resolved_profile = ctx.resolved_profile
    refits = ctx.path.measurement is Measurement.FITTED or (
        ctx.path.measurement is Measurement.RECORDED and not ctx.model_recorded
    )
    stage_a_scored, seeded_frame = ctx.stage_a_scored, ctx.seeded_frame
    return judging_loop.Judging(
        ctx.stage_a + ctx.stage_b,
        model=ctx.model,
        fallback_sigma_ppm=resolved_profile.fallback_sigma_ppm,
        notation_by_id=sample_mechanisms._notation_by_id(ctx.judged_mechanisms),
        mz_tolerance_ppm=ctx.scoring.mz_tolerance_ppm,
        abundance_floor=ctx.scoring.abundance_floor,
        max_alternatives=ctx.config.max_alternatives,
        lines=ctx.lines,
        tier_bands=ctx.config.tier_bands(),
        minor_channels=resolved_profile.minor_channels,
        partner_gated_channels=resolved_profile.partner_gated_channels,
        reagent_lines=ctx.reagent_rows,
        resolution=ctx.resolution,
        refits=refits,
        rescore=(
            (lambda model: judging_loop.fits_at(model, stage_a_scored, seeded_frame))
            if refits
            else None
        ),
        curve=ctx.confidence_curve,
        floors=ctx.floors,
        artifact_peak_ids=frozenset(ctx.artifact_peak_ids),
        instrument_type=ctx.instrument_type,
        listed_formulas=ctx.listed_formulas,
        opened_channels=resolved_profile.added_channels,
        given_channels=frozenset(resolved_profile.given_channels),
        source_line_cap=ctx.source_line_cap,
        context=resolved_profile.context,
        nox_skeleton=resolved_profile.nox_skeleton,
        fragment_ladders=fragment_ladders_for(resolved_profile.profile.name),
    )


async def judge(ctx: RunContext) -> None:
    """The reading changes, to their fixed point: the partner gate settles
    which molecule each ion read through a gated channel is, against the
    molecules the sample shows through the mode's own; the fragments the
    source made of a committed analyte are read as the source's on the settled
    ledger, before anything weighs them as molecules; and the cross-channel
    pass records what the settled readings say of each other
    (``judging_loop.Judging.judge``). A person's choice changes no reading: its
    formula is the person's."""
    if ctx.path.rules is Rules.SPECTRUM:
        return
    await asyncio.to_thread(ctx.judging.judge)


async def cap(ctx: RunContext) -> None:
    """The caps, which only lower a tier: the mass gate's, the cross-channel
    pass's and the tiering pass's rules (``judging_loop.Judging.cap``). Their
    order decides which cap a row names, never which tier it ends on.

    Where the caps leave a line in doubt on an assigned neighbour's envelope,
    another round reads it as that neighbour's (:func:`run_stages`); once
    none does, the round's rows are the judged ledger.
    """
    if ctx.path.rules is Rules.SPECTRUM:
        await asyncio.to_thread(_cap_by_the_spectrum, ctx)
        return
    judging = ctx.judging
    # The tiering pass asks every committed row for the lines its formula
    # predicts: seconds on a large ledger, the longest stall a run held the
    # event loop for (MAS-947).
    await asyncio.to_thread(judging.cap)
    if judging.another_round():
        return
    ctx.judged = judging.result()
    ctx.fragments = ctx.judged.fragments
    prepasses._log_fragments(ctx.fragments, ctx.sample.sample_item_name)
    # The judging rewrote every row's tier reasons; a row elected on the
    # batch's decision names it again among them.
    cite_listed(ctx.judged.rows, ctx.decided, ctx.listed_outcomes)
    # Counted on the judged ledger, not at the stamp (``elect``): a row the
    # fragment claim took leaves the ledger with its record.
    anchored = batch_lines.series_anchored(ctx.judged.rows)
    if anchored:
        ctx.batch_applied["series"] = anchored
    ctx.tiering = {**ctx.judged.tiering, "series_anchored": anchored}
    _report_judging(ctx)


def _cap_by_the_spectrum(ctx: RunContext) -> None:
    """The spectrum's rules (:attr:`Rules.SPECTRUM`): each monoisotopic row is
    asked whether the spectrum holds the lines its formula predicts, at the
    gate the sample's run asked its own rows at, or in a sample no run asked
    lines in, at the gate a run of it would ask at (:func:`_persons_line_gate`;
    ``tiering.ask_absent_line``). An absent line holds the row at candidate at
    most, never lifts one, and is the reason it lists; its isotopologue rows
    follow it. What decides whether a row is asked is the peaks the pass
    holds, never the gate, which :func:`_recorded_measurement` always sets: a
    hand assignment on a run that asked no line reads no peaks
    (``curation._persons_choice``), so it is asked none, and neither is a
    sample whose peaks the pass has none of."""
    rows = ctx.fragments.rows
    gate = ctx.line_gate
    artifacts = (
        frozenset(ctx.artifact_peak_ids)
        if ctx.recorded_artifact_peak_ids is None
        else ctx.recorded_artifact_peak_ids
    )
    lines = (
        None
        if ctx.peaks_df is None or ctx.peaks_df.empty
        else SpectrumLines.from_peaks(ctx.peaks_df, ctx.resolution)
    )
    ctx.lines = lines
    capped: set[str] = set()
    asked = 0
    for row in rows:
        if row.get("role") != ROLE_M0 or lines is None:
            continue
        _, reason = ask_absent_line(
            row,
            lines,
            gate,
            resolution=ctx.resolution,
            artifact_peak_ids=artifacts,
        )
        asked += 1
        if reason is None:
            continue
        row.setdefault("provenance", {})["tier_reasons"] = [reason]
        if row.get("tier") == TIER_ASSIGNED:
            row["tier"] = TIER_CANDIDATE
            capped.add(str(row.get("peak_assignment_id")))
    for row in rows:
        owner = str(row.get("owner_peak_assignment_id") or "")
        if row.get("role") == ROLE_ISO_CHILD and owner in capped:
            if row.get("tier") == TIER_ASSIGNED:
                row["tier"] = TIER_CANDIDATE
    ctx.tiering = {
        "rules": Rules.SPECTRUM.value,
        "asked": asked,
        "capped": len(capped),
    }
    ctx.judged = judging_loop.JudgedCommits(
        rows, {}, {}, ctx.tiering, {}, ctx.fragments
    )


def _report_judging(ctx: RunContext) -> None:
    """The judging's log lines, once its rounds have settled."""
    name = ctx.sample.sample_item_name
    judged = ctx.judged
    mass_calibration = judged.mass_calibration
    cross_channel = judged.cross_channel
    tiering = ctx.tiering
    partner_gate = judged.partner_gate
    model = judged.model
    refit = mass_calibration.get("refit") or {}
    if mass_calibration["applied"]:
        trend = model.trend
        centre = (
            f"a centre following {trend.offset_mda:+.3f} mDa over m/z "
            f"{trend.mz_lo:.0f}-{trend.mz_hi:.0f}"
            if trend is not None
            else f"a constant centre ({model.trend_refused or 'no trend asked'})"
        )
        runtime.logger.info(
            f"Sample '{name}' scores and caps at its v{model.version} model, "
            f"{model.scoring_mu_ppm:+.3f} ppm, width {model.width_ppm:.3f} ppm "
            f"over anchors {dict(model.anchors)}, at {centre}"
            + (
                f"; its refit moved {refit.get('centre_moved_widths')} widths "
                f"and rescored {refit.get('rescored', 0)} rows"
                if refit.get("adopted")
                else ""
            )
            + f"; {mass_calibration['capped']} of "
            f"{mass_calibration['committed'] - mass_calibration['corroborated']} "
            "uncorroborated commits capped off calibration"
        )
    else:
        runtime.logger.info(
            f"Sample '{name}' corroborated {mass_calibration['corroborated']} of "
            f"{mass_calibration['committed']} commits, its refit anchors "
            f"{refit.get('anchors', {})}, too few to measure a mass "
            "calibration; no row is gated on one"
        )
    followed = mass_calibration["isotopologues"]
    runtime.logger.info(
        f"Sample '{name}' isotopologues: {followed['tracks']} track their "
        f"parents, {followed['in_doubt']} only within what their lines deliver "
        f"({mass_calibration['capped_in_doubt']} held at candidate for it), "
        f"{followed['untracked']} not at all "
        f"({mass_calibration['capped_untracked']} held at candidate for it)"
    )
    if partner_gate["rounds"]:
        runtime.logger.info(
            f"Sample '{name}' partner gate: {partner_gate['partnered']} "
            f"opportunistic readings stand on a partner, {partner_gate['swapped']} "
            f"turned to another reading and {partner_gate['kept']} left to the "
            f"cap, over {partner_gate['rounds']} rounds"
            + ("" if partner_gate["settled"] else " (not settled)")
            + f"; {partner_gate['contested']} weighed against another borne-out "
            f"reading, {partner_gate['contest_swapped']} taken by it, "
            f"{partner_gate['outweighed']} readings outweighed at "
            f"{partner_gate['margin']:g} times and "
            f"{partner_gate['within_margin']} within it"
        )
    runtime.logger.info(
        f"Sample '{name}' corroborates {cross_channel['corroborated']} of "
        f"{cross_channel['committed_m0']} committed readings across "
        f"{len(cross_channel['channels'])} channels; "
        f"{cross_channel['ambiguous_nitrogen'] + cross_channel['ambiguous_adduct']} "
        "read an ion another molecule explains as well "
        f"({cross_channel['ambiguous_nitrogen']} with another nitrogen count), "
        f"{cross_channel['capped']} of them capped, "
        f"{cross_channel['capped_mirror']} of those reference-list matches "
        f"({cross_channel['capped_isotopologues']} isotopologues with them); "
        f"{sum(cross_channel['settled'].values())} others settled "
        f"({cross_channel['settled']}), "
        f"{cross_channel['shown_rival_weighed']} of them the mode's own reading "
        "against a rival the sample shows short of the "
        f"{cross_channel['partner_margin']:g}-fold margin; "
        f"{ctx.same_ion_families} list matches carry other readings of their ion"
    )
    runtime.logger.info(
        f"Sample '{name}' tiers {tiering['committed_m0']} committed readings on "
        f"rule set {tiering['version']}: {tiering['capped']} capped "
        f"({tiering['capped_isotopologues']} isotopologue rows with them)"
        + (
            ", "
            + ", ".join(
                f"{count} {rule}"
                for rule, count in sorted(
                    tiering["capped_by_rule"].items(), key=lambda kv: -kv[1]
                )
            )
            if tiering["capped_by_rule"]
            else ""
        )
        + f"; {tiering['claimed']} lines read as an assigned neighbour's "
        f"isotopologue ({tiering['claimed_with_their_lines']} of their own lines "
        f"with them, {tiering['released']} released) over "
        f"{tiering['claim_rounds']} rounds"
        + (
            ", held back: "
            + ", ".join(
                f"{count} {why}" for why, count in sorted(tiering["held"].items())
            )
            if tiering["held"]
            else ""
        )
    )


# --- level ----------------------------------------------------------------------


async def level(ctx: RunContext) -> None:
    """What a reader may state of each committed row: its identification level
    (step 5.8), beside the tier and moving none.

    Where the run asks for it, the search's winners' confidence first: the
    shortlist each winner's row carries, measured on the peak it names through
    every channel the search went through and at the model the winners were
    scored and capped at (``alternatives_scoring.measure_winner_confidence``). Read off
    the judged ledger, after every pass that may swap a reading; it writes
    provenance alone, which the level then reads.
    """
    sample = ctx.sample
    rows = ctx.judged.rows
    if ctx.path.rules is Rules.SPECTRUM:
        _level_persons_choice(rows)
        return
    search = ctx.search
    if ctx.config.stage_b_confidence and search is not None:
        winner_confidence = await measure_winner_confidence(
            sample,
            rows,
            ctx.searched_mechanisms,
            ctx.match_params,
            peak_budget=ctx.config.stage_b_confidence_peaks,
            model=ctx.judged.model or ctx.model,
        )
        search.scope[WINNER_CONFIDENCE_KEY] = winner_confidence
        runtime.logger.info(
            f"Sample '{sample.sample_item_name}' measured the shortlists of "
            f"{winner_confidence['measured']} of {winner_confidence['winners']} "
            f"untargeted winners ({winner_confidence['unreached']} past the "
            f"budget of {winner_confidence['peak_budget']}): "
            f"{winner_confidence['placed']} of {winner_confidence['formulas']} "
            f"formulas placed on their peak, {winner_confidence['ties']} winners "
            f"tied ({winner_confidence['seconds']:.1f} s)"
        )
    # Recorded with the tiering record, whose absent-line gate the rivals'
    # lines are asked at.
    # Off the event loop, as the tiering pass that asks a row's own lines.
    ctx.tiering[LEVEL_SUMMARY] = await asyncio.to_thread(
        apply_levels,
        rows,
        notation_by_id=sample_mechanisms._notation_by_id(ctx.searched_mechanisms),
        instrument_type=ctx.instrument_type,
        context=ctx.resolved_profile.context,
        lines=ctx.lines,
        tiering=ctx.tiering,
        resolution=ctx.resolution,
    )
    levels = ctx.tiering[LEVEL_SUMMARY]
    runtime.logger.info(
        f"Sample '{sample.sample_item_name}' identification levels on rule set "
        f"{levels['level_rules_version']}: "
        + (
            ", ".join(f"{count} at {lvl}" for lvl, count in levels["levels"].items())
            or "no committed reading"
        )
        + f" ({levels['rivals_not_measured']} with no measured shortlist)"
    )


def _level_persons_choice(rows: list[dict]) -> None:
    """What the mapping can state of a person's formula
    (``identification_level.hand_level``): refuted where a line it predicts is
    absent, else its rivals not measured, since no shortlist of a run was
    competed against it. Its isotopologue rows show its level."""
    by_id: dict[str, dict] = {}
    for row in rows:
        if row.get("role") != ROLE_M0 or not row.get("assigned_formula"):
            continue
        level = hand_level(row)
        row.setdefault("provenance", {})[IDENTIFICATION_LEVEL] = level
        row["level"] = level["level"]
        by_id[str(row.get("peak_assignment_id"))] = level
    for row in rows:
        if row.get("role") != ROLE_ISO_CHILD or not row.get("assigned_formula"):
            continue
        owner = str(row.get("owner_peak_assignment_id") or "")
        level = inherited_level(by_id.get(owner), owner)
        row.setdefault("provenance", {})[IDENTIFICATION_LEVEL] = level
        row["level"] = level["level"]


# --- persist --------------------------------------------------------------------


async def persist(ctx: RunContext) -> None:
    """Keep what the stages judged where the path keeps it
    (:attr:`RunContext.keep`)."""
    ctx.result = await ctx.keep(ctx)


def ledger_rows(ctx: RunContext) -> tuple[list[dict], list[dict], list[dict]]:
    """The complete ledger the stages leave: one row per observed peak.

    What the judging left committed, the claims, the fragments, and an
    unassigned row for every other peak: an isotopologue released with the
    reading it belonged to is a peak nothing explains.

    :return: Every row, the committed ones, and the unassigned ones.
    """
    committed = ctx.judged.rows
    assigned_peak_ids = (
        ctx.claimed_peak_ids
        | ctx.fragments.peak_ids
        | {row["sample_peak_id"] for row in committed}
    )
    peaks_df = ctx.peaks_df
    unassigned = build_unassigned_assignments(
        peaks_df[~peaks_df["sample_peak_id"].isin(assigned_peak_ids)],
        sample_item_id=ctx.sample.sample_item_id,
        peak_assignment_run_id=ctx.run_id,
    )
    every = (
        ctx.reagent_rows
        + ctx.fragments.fragments
        + ctx.artifact_rows
        + committed
        + unassigned
    )
    return every, committed, unassigned


async def keep_search_rows(ctx: RunContext) -> tuple[list[dict], int, set[str]]:
    """The batch search keeps a sample's judged rows for the batch: every
    sample's are merged into one reading per anchor before any is written
    (``batch_untargeted.merge_search_rows``).

    :return: The judged rows, shaped as ledger rows that will never be
        written; how many of the sample's representatives the cap left
        unsearched; and the representatives persistence alone admitted.
    """
    return ctx.judged.rows, ctx.unsearched, ctx.by_persistence


# --- the runner -----------------------------------------------------------------


#: Each stage's function.
STAGE_FUNCTIONS: dict[Stage, Callable[[RunContext], Awaitable[None]]] = {
    Stage.RESOLVE: resolve,
    Stage.CLAIM: claim,
    Stage.PROPOSE: propose,
    Stage.SCORE: score,
    Stage.ELECT: elect,
    Stage.REFIT: refit,
    Stage.JUDGE: judge,
    Stage.CAP: cap,
    Stage.LEVEL: level,
    Stage.PERSIST: persist,
}


async def run_stages(ctx: RunContext) -> RunContext:
    """Run the path's stages in order, from its start to the end.

    Every stage reads labelled ions at the purity the sample's ionization setup
    carries (``custom_elements.labelled_purity``), whichever path runs it. A
    round (:data:`ROUND`) runs again from its first stage while the cap stage
    leaves the judging unsettled.

    :param ctx: The pass, its inputs set; the stages fill the rest.
    :return: The same context, :attr:`RunContext.ran` naming every stage run.
    """
    stages = ctx.path.stages()
    purity = await sample_label_purity(ctx.sample.sample_item_id)
    with labelled_purity(purity), open_memo(kept_measurement.envelopes_of(ctx)):
        index = 0
        while index < len(stages):
            stage = stages[index]
            await STAGE_FUNCTIONS[stage](ctx)
            ctx.ran.append(stage)
            if stage is Stage.CAP and ctx.judged is None:
                index = stages.index(ROUND[0])
                continue
            index += 1
    return ctx


def admitted_targets(
    frame: pd.DataFrame,
    target_peak_ids: set[str],
    persistent_peak_ids: set[str],
    *,
    peak_intensity_threshold: float,
    max_untargeted_peaks: Optional[int],
    ceiling: int,
) -> tuple[pd.DataFrame, dict, set[str]]:
    """The representatives a sample's search enumerates, and which of them
    persistence alone admitted.

    Two admissions, side by side. By height, as a run's search admits: a
    representative at or above the intensity floor, brightest first up to the
    cap (``engine.untargeted_targets``). By persistence: a representative of a
    persistent anchor, however faint and wherever the cap stopped. The ceiling
    still bounds the two together, the faint ones brightest first: it is
    there so no search is unbounded work, whatever admitted the peaks.

    :param frame: The sample's peaks the search reads, persistent sub-floor
        ones included, with ``sample_peak_id`` and ``intensity``.
    :param target_peak_ids: The sample's representatives.
    :param persistent_peak_ids: Those whose anchors are persistent.
    :param peak_intensity_threshold: The run's intensity floor.
    :param max_untargeted_peaks: The run's cap, or None.
    :param ceiling: The hard bound on the peaks enumerated.
    :return: The peaks to enumerate; the search's scope, with
        ``admitted_by_persistence``; and the ids of the peaks persistence
        alone admitted.
    """
    representatives = frame[frame["sample_peak_id"].isin(target_peak_ids)]
    by_height = representatives[
        representatives["intensity"] >= peak_intensity_threshold
    ]
    targets, scope = untargeted_targets(by_height, max_untargeted_peaks, ceiling)
    persistent = representatives[
        representatives["sample_peak_id"].isin(persistent_peak_ids)
        & ~representatives["sample_peak_id"].isin(targets["sample_peak_id"])
    ]
    unfloored = int(
        (~persistent["sample_peak_id"].isin(by_height["sample_peak_id"])).sum()
    )
    extra = persistent.nlargest(max(ceiling - len(targets), 0), "intensity")
    scope = {
        **scope,
        "eligible_peaks": scope["eligible_peaks"] + unfloored,
        "searched_peaks": scope["searched_peaks"] + int(len(extra)),
        "admitted_by_persistence": int(len(extra)),
    }
    scope["limited"] = scope["searched_peaks"] < scope["eligible_peaks"]
    return (
        pd.concat([targets, extra]) if len(extra) else targets,
        scope,
        {str(peak) for peak in extra["sample_peak_id"]},
    )


async def _stage_a_assignments(
    sample,
    config: PeakAssignmentConfig,
    match_params,
    mechanism_ids,
    mechanisms,
    peak_assignment_run_id: str,
    excluded_peak_ids: set[str] | None = None,
    *,
    fallback_sigma_ppm: float,
    known_window: KnownWindow | None,
    reagent_offset: ReagentOffset | None = None,
    ionization_setup: dict | None = None,
    reagent_profile: str | None = None,
    inherited_model: MeasurementModel | None = None,
    inherited_envelope: RunEnvelopeFit | None = None,
    resolution: Callable[[float], float] | None | object = RESOLVE_RESOLUTION,
) -> tuple[
    list[dict],
    dict | None,
    SampleMassAccuracy,
    RunEnvelopeFit,
    frozenset[str],
    pd.DataFrame,
    ConfidenceCurve | None,
]:
    """Stage A: database-first assignment from the known composition set.

    The curated target library plus (when loaded) the reference mirror, matched
    against the sample's peaks, gated by the sample's match parameters, scored
    with the fit score and arbitrated into one row per won peak. Shared by the
    run-backed orchestrator and the run-less ingest fold
    (:func:`fold_sample_peaks_without_run`), so the two cannot drift apart in
    what they call Stage A.

    :param sample: The sample view row.
    :param config: The run configuration (thresholds, alternatives cap).
    :param match_params: The sample's match parameters (gating, abundance floor).
    :param mechanism_ids: The sample's ionization mechanism ids, which the
        target library is read through: the workspace named its compounds for
        the modes its collection is attached to.
    :param mechanisms: The mechanisms the reference lists are read through:
        the mode's own and the secondary channels the run opened
        (:func:`_searched_mechanisms`), since a list names a compound and not
        the channel it is seen through. A reading through an opened channel is
        held as the search's are (``engine.cap_second_channel``).
    :param peak_assignment_run_id: The id stamped on every row.
    :param excluded_peak_ids: Peaks the reagent pre-pass has already claimed.
        Dropped before arbitration rather than after it: a reagent peak left in
        the frame would still fold corroboration into a compound's other
        adducts, so removing its row afterwards would leave a boost behind that
        no surviving row accounts for. Whole target ions go, not single rows -
        see :func:`drop_ions_claimed_elsewhere` for why the difference matters.
    :param fallback_sigma_ppm: The resolved profile's instrument class width.
        Stage A scores at it when the target library matched too few lines to
        fit a width, or lines of fewer than :data:`STAGE_A_FIT_MIN_IONS` ions,
        as the untargeted stage does, so neither stage falls back to a width
        the other does not use (:func:`score_ions_by_fit`).
    :param known_window: The resolved chemistry context's ceiling on every
        reference source's window, or None where the context sets none.
    :param reagent_offset: Where the reagent pre-pass's lines put the mass
        axis. Stage A scores at it where the target library matched too few
        lines, or too few ions, to fit an offset and the lines show one beyond
        the width (:attr:`SampleMassAccuracy.scoring_mu_ppm`), and so does the
        untargeted stage. The width stays the class's: a handful of the source's own
        bright lines says where the axis sits, not how an analyte scatters
        about it.
    :param ionization_setup: The setup the sample is bound under, whose
        confidence curve the rows are read off.
    :param reagent_profile: The reagent profile the run resolved to, which the
        run's record of that curve is compared with.
    :param inherited_model: A decoy run's control's measurement model, which
        the frame is scored at instead of one fitted off a frame whose axis was
        moved on purpose (``decoy.DecoyPlan``). None for every other run, whose
        frame fits the v1 model the stage returns the measurement of.
    :param inherited_envelope: A decoy run's control's envelope tolerance,
        which the frame is scored at from the first pass and which the stage
        returns, instead of one fitted on the decoy's own commits. None for
        every other run.
    :param resolution: The file's resolving power as the run read it, the
        lines of its known set are resolved at (:func:`_fetch_known_isotopes`).
    :return: The assignment rows; what a run records about the confidence curve
        their P(correct) came from - None when Stage A never ran or the
        instrument has no curve; and what the target library's own matched
        isotopologues say about this sample's mass error. That last one is the
        instrument's accuracy ON THIS SAMPLE, and Stage B is scored at it: the
        untargeted stage has no corroborated set of its own to fit a width
        from, and the target library is exactly such a set. The reference
        mirror is not, so its lines are left out of the fit even though they
        compete for peaks in the same frame (:func:`target_library_rows`). Its
        ``sigma_ppm`` is None when too few of the library's lines, or too few
        of its ions, matched to fit one, its ``anchors`` and ``anchor_ions``
        say how few, and its ``reagent`` is the pre-pass's reading, carried
        even where the library matched nothing. And last, the envelope
        tolerance the run fitted on the envelopes Stage A committed
        (:func:`envelope_fit.fit_run_envelope_tolerance`), which Stage A's own
        rows are scored at, and the search and its seeded re-score after them.
        Stage A first scores at today's numbers, since it is its commits that
        the fit reads, and gates and scores its frame again at the fitted
        tolerance, at the width and offset the first pass measured.
        And the neutrals the lists it read name, matched or not
        (:func:`listed_formula_identities`), which corroborate a search row
        through a secondary channel (``engine.cap_second_channel``).
        And the gated frame its rows were scored on, which the run
        scores again at its v2 model where the mass gate's refit takes one
        (``mass_gate.measure_mass_gate``). And last the curve its rows'
        P(correct) was read off, which that re-score reads again
        (:func:`rescore_rows`); None where the instrument has none.
    """
    first = await _stage_a_first_pass(
        sample,
        config,
        match_params,
        mechanism_ids,
        mechanisms,
        peak_assignment_run_id,
        excluded_peak_ids,
        fallback_sigma_ppm=fallback_sigma_ppm,
        known_window=known_window,
        reagent_offset=reagent_offset,
        ionization_setup=ionization_setup,
        reagent_profile=reagent_profile,
        inherited_model=inherited_model,
        inherited_envelope=inherited_envelope,
        resolution=resolution,
    )
    if inherited_envelope is not None:
        # A decoy's commits are wrong formulas on a moved axis, and few: it
        # fits no tolerance on them, and was scored at its control's above.
        return (
            first.rows,
            first.confidence_calibration,
            first.mass_accuracy,
            inherited_envelope,
            first.listed_formulas,
            first.scored,
            first.curve,
        )
    # The tolerance is fitted on what Stage A committed, which a sample with no
    # library match has nothing of: it can still take its neighbours' curve,
    # each read under this run's config and match parameters.
    envelope = await fit_run_envelope_tolerance(
        sample,
        first.scored,
        first.rows,
        model=MeasurementModel.v1(first.mass_accuracy, fallback_sigma_ppm),
        read_neighbour=_neighbour_reader(config, match_params),
    )
    stage_a_assignments, scored = first.rows, first.scored
    if envelope.fit.level != LEVEL_DEFAULT and not first.matched.empty:
        # Gated and scored again at the fitted tolerance, and the peaks
        # arbitrated on that, at the first pass's width and offset: the ones
        # the run records and Stage B and the mass gate are scored at. The
        # fitted window can admit a line the first pass turned away, a TOF
        # line read below 0.6 of its share where the curve opens, and refitted
        # here that line would be an anchor and move both.
        # Off the event loop, as the first pass's own fit and arbitration.
        scored = await asyncio.to_thread(
            first.rescored_at, match_params, envelope, fallback_sigma_ppm
        )
        stage_a_assignments = await asyncio.to_thread(first.assignments_of, scored)
    return (
        stage_a_assignments,
        first.confidence_calibration,
        first.mass_accuracy,
        envelope,
        first.listed_formulas,
        scored,
        first.curve,
    )


#: The id a neighbour's first-pass rows are stamped with: they are read for
#: their lines and never kept.
_NEIGHBOUR_READ_RUN_ID = "envelope-pool-read"

#: How many neighbour reads one process runs at a time. A read is seconds of
#: pandas work that holds the interpreter lock, in a worker thread or on the
#: event loop. Five thin runs at once on the demo bundle's bromide batch read
#: 30 neighbours side by side, and the server answered no request for eight
#: minutes; one at a time, the other runs' reads wait without working and the
#: server keeps answering.
NEIGHBOUR_READS_AT_ONCE = 1

_neighbour_read_slots: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, asyncio.Semaphore
] = weakref.WeakKeyDictionary()


def _neighbour_read_slot() -> asyncio.Semaphore:
    """The running loop's semaphore of :data:`NEIGHBOUR_READS_AT_ONCE`, made on
    first use: an asyncio semaphore belongs to the loop it first waits on."""
    loop = asyncio.get_running_loop()
    slot = _neighbour_read_slots.get(loop)
    if slot is None:
        slot = _neighbour_read_slots[loop] = asyncio.Semaphore(NEIGHBOUR_READS_AT_ONCE)
    return slot


async def _neighbour_envelope_lines(
    neighbour, config: PeakAssignmentConfig, match_params
) -> EnvelopeLines | None:
    """A neighbour's own lines, for a thin file's pooled curve.

    Its Stage A first pass at today's numbers (:func:`_stage_a_first_pass`),
    on its own peaks, mechanisms, channels and pre-passes, composed by the stage
    list's resolve and claim stages as the run-less fold composes them
    (``pipeline.resolve``, ``pipeline.claim``), and read by
    ``envelope_fit.envelope_lines``. The config and the match parameters are
    the reading run's, so every line of one pool is measured alike. Labelled
    lines are predicted at the purity of the neighbour's own setup, as a run of
    it predicts them. It is never its pooled curve, nor anything a run of it
    recorded: the pool is a function of the reading run's inputs and its
    neighbours' data, whichever of them ran before. Runs that read the same
    neighbour under equal configs and match parameters while its read is in
    flight share that read (:func:`_join_neighbour_read`).

    :param neighbour: The neighbour's sample view row.
    :param config: The reading run's configuration.
    :param match_params: The reading run's match parameters.
    :return: Its lines; None where the engine would not run it (a blank, an
        unverified calibration) or it has no peak list, so the next nearest
        file takes its place.
    """
    if ineligible_reason(neighbour) is not None:
        return None
    read = _join_neighbour_read(neighbour, config, match_params)
    read.waiters += 1
    try:
        # Shielded: a run cancelled while it waits leaves the read to the
        # runs still waiting on it.
        return await asyncio.shield(read.task)
    finally:
        read.waiters -= 1
        if read.waiters == 0 and not read.task.done():
            # Every run that asked for it has gone. Out of the table first, so
            # no run joins a read that is being cancelled.
            _forget_neighbour_read(read)
            read.task.cancel()


@dataclass(eq=False)
class _NeighbourRead:
    """One neighbour read in flight in a process (:func:`_join_neighbour_read`).

    :param sample_item_id: The neighbour read.
    :param config: The configuration it is read under.
    :param match_params: The match parameters it is read under.
    :param task: The read, in its process's slot.
    :param waiters: The runs waiting on it.
    """

    sample_item_id: str
    config: PeakAssignmentConfig
    match_params: object
    task: asyncio.Task
    waiters: int = 0


_neighbour_reads_in_flight: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, dict[str, list[_NeighbourRead]]
] = weakref.WeakKeyDictionary()


def _join_neighbour_read(
    neighbour, config: PeakAssignmentConfig, match_params
) -> _NeighbourRead:
    """The read of ``neighbour`` in flight under an equal config and equal match
    parameters, else a new one.

    Runs reading the same neighbour at the same time share one read: they would
    compute the same lines from the same data at the same moment. The thin
    files of a batch run at once read largely the same neighbours, and the
    reads, one at a time (:data:`NEIGHBOUR_READS_AT_ONCE`), would otherwise
    queue the same work several times over. A read that has ended is never
    joined: the next run reads afresh, so no run reads lines older than its
    own start.
    """
    in_flight = _neighbour_reads_in_flight.setdefault(asyncio.get_running_loop(), {})
    sample_item_id = str(neighbour.sample_item_id)
    reads = in_flight.setdefault(sample_item_id, [])
    for read in reads:
        if read.config == config and read.match_params == match_params:
            return read
    read = _NeighbourRead(
        sample_item_id=sample_item_id,
        config=config,
        match_params=match_params,
        task=asyncio.create_task(
            _slotted_neighbour_read(neighbour, config, match_params)
        ),
    )
    reads.append(read)
    read.task.add_done_callback(lambda _task: _forget_neighbour_read(read))
    return read


def _forget_neighbour_read(read: _NeighbourRead) -> None:
    """Take ``read`` out of its process's reads in flight, if it is still in."""
    in_flight = _neighbour_reads_in_flight.get(read.task.get_loop(), {})
    reads = in_flight.get(read.sample_item_id, [])
    if any(other is read for other in reads):
        reads[:] = [other for other in reads if other is not read]
        if not reads:
            del in_flight[read.sample_item_id]


async def _slotted_neighbour_read(
    neighbour, config: PeakAssignmentConfig, match_params
) -> EnvelopeLines | None:
    """:func:`_read_neighbour_lines`, in its process's slot."""
    async with _neighbour_read_slot():
        return await _read_neighbour_lines(neighbour, config, match_params)


async def _read_neighbour_lines(
    neighbour, config: PeakAssignmentConfig, match_params
) -> EnvelopeLines | None:
    """:func:`_neighbour_envelope_lines`' read itself."""
    # The file reads and the line read go off the event loop, as the first
    # pass's arbitration does: a thin run reads six files, and on the loop
    # their work would hold every other request of the server for a minute.
    try:
        peaks_df = await asyncio.to_thread(load_sample_peaks, neighbour)
    except NotFoundException:
        # Its file is missing or not processed yet: no peak list.
        return None
    if peaks_df.empty:
        return None

    with labelled_purity(await sample_label_purity(neighbour.sample_item_id)):
        # The stage list's first two stages, composed as the run-less fold
        # composes them: its peaks, mechanisms, channels and pre-passes.
        inputs = RunContext(
            path=INGEST_FOLD,
            sample=neighbour,
            config=config,
            run_id=_NEIGHBOUR_READ_RUN_ID,
            match_params=match_params,
            peaks_df=peaks_df,
            strict_instrument=False,
        )
        await resolve(inputs)
        await claim(inputs)
        fallback_sigma_ppm = inputs.resolved_profile.fallback_sigma_ppm
        first = await _stage_a_first_pass(
            neighbour,
            config,
            match_params,
            inputs.mechanism_ids,
            inputs.searched_mechanisms,
            _NEIGHBOUR_READ_RUN_ID,
            excluded_peak_ids=inputs.claimed_peak_ids,
            fallback_sigma_ppm=fallback_sigma_ppm,
            known_window=inputs.resolved_profile.context.known_window,
            reagent_offset=inputs.reagent_offset,
        )
    return await asyncio.to_thread(
        envelope_lines,
        first.scored,
        first.rows,
        model=MeasurementModel.v1(first.mass_accuracy, fallback_sigma_ppm),
        form=FORM_CURVE,
    )


def _neighbour_reader(config: PeakAssignmentConfig, match_params) -> NeighbourReader:
    """Reads a thin file's neighbours under the reading run's config and match
    parameters (:func:`_neighbour_envelope_lines`)."""

    async def read(neighbour) -> EnvelopeLines | None:
        return await _neighbour_envelope_lines(neighbour, config, match_params)

    return read
