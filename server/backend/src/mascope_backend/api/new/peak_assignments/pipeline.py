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
(``service.Judging``, :data:`service.MAX_CLAIM_ROUNDS`).

A path is a configuration of the list (:class:`AssignmentPath`), set by the
stage it starts at, what its propose stage proposes and where its measurement
model comes from, and it runs every stage from its start to the end
(:func:`run_stages`). Where it keeps what it judged is the caller's
(:attr:`RunContext.keep`).

Design: ``docs/dev/assignment_quality_plan.md``, milestone 7 (MAS-974,
finding 1).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from types import SimpleNamespace
from typing import Any

import pandas as pd
from sqlalchemy import insert, update

from mascope_backend.api.new.cheminfo.utils import to_custom_element_format
from mascope_backend.api.new.ionization.modes.versions import (
    sample_label_purity,
    sample_setup,
)
from mascope_backend.api.new.match.params import default_match_params
from mascope_backend.api.new.peak_assignments import service
from mascope_backend.api.new.peak_assignments.batch_known_species import (
    batch_species_for,
)
from mascope_backend.api.new.peak_assignments.config import (
    MAX_UNTARGETED_PEAKS_CEILING,
    PeakAssignmentConfig,
)
from mascope_backend.api.new.peak_assignments.decoy import DecoyPlan
from mascope_backend.api.new.peak_assignments.engine import (
    LIST_PRIOR_WEIGHT,
    SOURCE_DATABASE,
    build_unassigned_assignments,
    list_readings,
    pattern_scoring_for,
    pattern_scoring_snapshot,
    record_displaced_list_readings,
    settle_list_election,
    untargeted_matches_to_peak_assignments,
    untargeted_seeds,
    untargeted_targets,
)
from mascope_backend.api.new.peak_assignments.halogen_twins import record_twins
from mascope_backend.api.new.peak_assignments.identification_level import (
    LEVEL_SUMMARY,
    apply_levels,
)
from mascope_backend.api.new.peak_assignments.mass_gate import (
    BELOW_ASSIGNABILITY_Z,
    SpectrumLines,
)
from mascope_backend.api.new.peak_assignments.profile_preview import (
    named_context_for,
)
from mascope_backend.api.new.peak_assignments.profiles import (
    resolve_profile,
    with_fitted_window,
)
from mascope_backend.db import PeakAssignment, PeakAssignmentRun, async_session
from mascope_backend.runtime import runtime
from mascope_backend.socket.notifications import (
    UserNotification,
    send_progress_user_notification,
)
from mascope_file.name import get_instrument_type
from mascope_tools.composition.custom_elements import labelled_purity
from mascope_tools.composition.finder import assign_compositions


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


class Measurement(StrEnum):
    """Where a path's measurement model comes from: the centre and width a
    mass error is read against, and the tolerance a line's share is."""

    #: Fitted on the path's own readings: Stage A's lines, its commits'
    #: envelopes, the search's winners and the mass gate's anchors.
    FITTED = "fitted"
    #: A decoy's control's, recorded on the control and never fitted again on
    #: the decoy's wrong readings (``decoy.DecoyPlan``).
    CONTROL = "control"


@dataclass(frozen=True)
class AssignmentPath:
    """A configuration of the stage list.

    :param name: The path, as a log line or a test names it.
    :param start: The stage it starts at; it runs every stage after it.
    :param proposal: What its propose stage proposes.
    :param measurement: Where its measurement model comes from.
    """

    name: str
    start: Stage
    proposal: Proposal
    measurement: Measurement

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
#: batch ledger with no run written (``service.fold_sample_peaks_without_run``).
INGEST_FOLD = AssignmentPath(
    "ingest fold", Stage.RESOLVE, Proposal.KNOWN, Measurement.FITTED
)

#: Every path, in the order this module names them.
PATHS: tuple[AssignmentPath, ...] = (RUN, DECOY, INGEST_FOLD)


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
    #: The stages run so far, in order, a round's once per round.
    ran: list[Stage] = field(default_factory=list)
    # -- resolve
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
    confidence_calibration: dict | None = None
    mass_accuracy: Any = None
    envelope: Any = None
    listed_formulas: frozenset[str] = frozenset()
    scoring: Any = None
    search: SearchProposal | None = None
    # -- score
    fit_by_seed: dict = field(default_factory=dict)
    winner_accuracy: Any = None
    # -- elect
    stage_b: list[dict] = field(default_factory=list)
    mirror_families: int = 0
    fragments: Any = None
    series_anchored: int = 0
    # -- refit, judge, cap
    lines: SpectrumLines | None = None
    judging: service.Judging | None = None
    judged: Any = None
    tiering: dict = field(default_factory=dict)
    # -- persist
    result: Any = None

    @property
    def claimed_peak_ids(self) -> set[str]:
        """The peaks the claim stage took out of every stage after it."""
        return self.reagent_peak_ids | self.artifact_peak_ids

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
    one, so a run that fails still says what it would have searched.
    """
    sample = ctx.sample
    if ctx.match_params is None:
        ctx.match_params = await default_match_params(sample.sample_item_id)
    if ctx.peaks_df is None:
        # The read opens the sample's peak list: it runs on a worker thread
        # so the event loop keeps answering every other request.
        ctx.peaks_df = await asyncio.to_thread(service.load_sample_peaks, sample)
        await ctx.progress(0.1)
    ctx.ionization_setup = await sample_setup(sample.sample_item_id)
    ctx.mechanism_ids, ctx.mechanisms = await service.fetch_sample_mechanisms(sample)
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
    # -- Opportunistic channels (`service._resolve_secondary_channels`).
    (
        ctx.resolved_profile,
        ctx.secondary_mechanisms,
    ) = await service._resolve_secondary_channels(
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
    # the sample's whatever the arm (`service._reagent_assignments`).
    ctx.own_mechanisms = service._searched_mechanisms(
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
    if ctx.resolved_profile.unavailable_channels:
        # Once per pass, and only for a channel the sample actually shows: an
        # operator can act on this by adding the mechanism.
        runtime.logger.info(
            f"Sample '{sample.sample_item_name}' shows the fingerprint of "
            f"{', '.join(ctx.resolved_profile.unavailable_channels)}, which this "
            "deployment has no ionization mechanism for; not searched."
        )
    if ctx.run is not None:
        await service._record_resolved_profile(
            ctx.run_id,
            ctx.config,
            ctx.resolved_profile,
            ionization_setup=ctx.ionization_setup,
            decoy_arm=None if plan is None else plan.record(),
        )
    # The mechanisms a sample's channels are searched and read through: Stage A
    # reads the reference lists through them, the search enumerates through
    # them, and the passes after both read them.
    ctx.searched_mechanisms = service._searched_mechanisms(
        ctx.mechanisms, ctx.secondary_mechanisms, ctx.resolved_profile
    )
    # The file's resolving power, read once: what one peak of the file holds
    # for every pass that predicts an isotope line - the reagent pre-pass, the
    # envelope match, the mass gate and the tiering pass.
    ctx.resolution = await service._resolution_of(sample)


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

    The reagent pre-pass (``service._reagent_assignments``): the source's own
    cluster ions are the brightest peaks of the spectrum and none of them is
    sample chemistry, so they are claimed here and taken out of what any later
    stage may propose. Ordering is the whole mechanism: nothing downstream is
    offered the peak, so nothing can overwrite it. The library ions the
    sample's batch decided present (step 4.2h) are claimed on the batch's
    word where the file's own claim does not take them.

    Then the artifact pre-pass on the same footing: ringing around a very
    intense centroid is the detector's answer to a neighbour, not a species.
    """
    sample = ctx.sample
    (
        ctx.reagent_rows,
        ctx.reagent_peak_ids,
        ctx.reagent_offset,
    ) = service._reagent_assignments(
        ctx.peaks_df,
        ctx.resolved_profile,
        sample.sample_item_id,
        ctx.run_id,
        resolution=ctx.resolution,
        own_mechanisms=ctx.own_mechanisms,
        sample_name=sample.sample_item_name,
        batch_species=await batch_species_for(sample),
    )
    if ctx.reagent_rows:
        runtime.logger.info(
            f"Reagent pre-pass claimed {len(ctx.reagent_rows)} of "
            f"{len(ctx.peaks_df)} peaks of sample '{sample.sample_item_name}' "
            f"for profile '{ctx.resolved_profile.profile.name}'"
        )
    ctx.artifact_rows, ctx.artifact_peak_ids = service._artifact_assignments(
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
    at is the one the known set's lines fitted.
    """
    proposal = ctx.path.proposal
    if proposal in (Proposal.KNOWN_AND_SEARCH, Proposal.KNOWN):
        await _propose_known_set(ctx)
    if proposal is Proposal.KNOWN_AND_SEARCH and ctx.config.run_untargeted:
        await _propose_by_search(ctx)


async def _propose_known_set(ctx: RunContext) -> None:
    """Stage A: the known composition set, matched, scored and arbitrated.

    The curated target library plus (when loaded) the reference mirror
    (``service._stage_a_assignments``), whose lines fit the measurement every
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
    ) = await service._stage_a_assignments(
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
        inherited_accuracy=None if plan is None else plan.accuracy,
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
    # How this sample's envelopes are predicted and matched, resolved before
    # the search because the judging reads it whether or not the search ran: a
    # pass of the known set alone still has committed rows whose peaks a
    # neighbour's envelope may already predict.
    ctx.scoring = pattern_scoring_for(
        ctx.match_params,
        ctx.mass_accuracy,
        ctx.instrument_type,
        ctx.resolution,
        envelope_tolerance=envelope.tolerance,
    )
    # On a TOF the search's window is bounded by the width Stage A fitted (step
    # 3.6): a candidate further from the sample's centre than the mass gate's
    # floor is one it would hold below assignability whatever its fit, so it
    # is no candidate to search for.
    ctx.resolved_profile = with_fitted_window(
        ctx.resolved_profile,
        sigma_ppm=ctx.scoring.sigma_ppm
        if ctx.mass_accuracy.sigma_ppm is not None
        else None,
        mu_ppm=ctx.scoring.mu_ppm,
        sigmas=BELOW_ASSIGNABILITY_Z,
        instrument_type=ctx.instrument_type,
    )
    # The measurement the search's winners' fits were scored at: the seeded
    # re-score's where it scored any (the score stage), else Stage A's, which
    # the finder's own numbers were scored at.
    ctx.winner_accuracy = ctx.mass_accuracy


async def _propose_by_search(ctx: RunContext) -> None:
    """The untargeted search over the peaks the known set left.

    The claim stage's peaks and the known set's are out of it from the start,
    so it never searches them: a reagent cluster has an ordinary elemental
    composition and the search would fit a neutral to it happily. Every
    monoisotopic peak a list read is put to the search too; there the list's
    reading is one candidate beside the grid's (``engine.settle_list_election``).
    A chlorine or bromine reading is then proposed from its twin line
    (``service._reread_halogen_twins``).
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
    notations, mechanism_id_by_notation = service._untargeted_ionization_notations(
        ctx.searched_mechanisms
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
            for column in service.SEARCH_PEAK_COLUMNS
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
            service._notation_by_id(ctx.searched_mechanisms),
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
    mass_accuracy = ctx.mass_accuracy
    search.snapshot = pattern_scoring_snapshot(scoring, mass_accuracy, frame)
    runtime.logger.info(
        f"Untargeted stage for sample '{sample.sample_item_name}' scores at "
        f"{scoring.sigma_ppm:.3f} ppm ({search.snapshot['sigma_source']}, "
        f"{mass_accuracy.anchors} anchors of {mass_accuracy.anchor_ions} ions), "
        f"offset {scoring.mu_ppm:+.3f} ppm ({search.snapshot['mu_source']})"
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
    search.matches, search.twin_records = await service._reread_halogen_twins(
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


# --- score ----------------------------------------------------------------------


async def score(ctx: RunContext) -> None:
    """Measure every candidate the way Stage A measures one.

    The known set's readings were measured as ions in Stage A's own match
    pass. Every reading the search committed to is measured again here, as an
    ion, through one match pass over the sample, gated by the run's match
    params (``service._seeded_rescore``): the second measurement is what the
    row is tiered on, so a search's "assigned" and a list's mean one thing.
    """
    search = ctx.search
    if search is None or not search.ran:
        return
    plan = ctx.decoy_plan if ctx.inherits else None
    ctx.fit_by_seed, rescored_accuracy = await service._seeded_rescore(
        ctx.sample,
        ctx.match_params,
        untargeted_seeds(
            search.matches,
            search.mechanism_id_by_notation,
            to_custom_element_format,
        ),
        reagent_offset=service._run_scoring_centre(ctx.mass_accuracy),
        envelope_tolerance=ctx.envelope.tolerance,
        inherited_accuracy=None if plan is None else plan.accuracy,
        resolution=ctx.resolution,
    )
    if rescored_accuracy is not None:
        ctx.winner_accuracy = rescored_accuracy


# --- elect ----------------------------------------------------------------------


async def elect(ctx: RunContext) -> None:
    """One reading per peak: the ledger's committed rows.

    The search's matches become rows on the peaks it was offered and no
    earlier stage took (``engine.untargeted_matches_to_peak_assignments``),
    carrying the list readings it displaced and the twins it read. Then, on
    the rows of both:

    - a list's readings through the channels the run opened are held as the
      search's readings through them are, before any pass reads them as
      partners or rivals (``service._hold_opened_channel_readings``);
    - a reference list's matches carry the other readings of their ion
      (``service._record_mirror_readings``), which reads no tier and so is
      written once however many rounds judge them;
    - the fragments the source made of a committed analyte are read as the
      source's before anything weighs them as molecules
      (``service._claim_fragments``);
    - and a reading the batch's series detection proposed for its peak
      carries the series' record, which the tiering pass counts as a second
      observation (step 4.1). Not on a decoy arm: the batch ledger is the
      control's chemistry, not the arm's.
    """
    sample = ctx.sample
    config = ctx.config
    search = ctx.search
    if search is not None and search.ran:
        election = search.election
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
            excluded_peak_ids=search.excluded - election.released,
            fit_by_seed=ctx.fit_by_seed,
            listed_formulas=ctx.listed_formulas,
        )
        held = record_displaced_list_readings(
            ctx.stage_b, election.displaced, config.max_alternatives
        )
        record_twins(ctx.stage_b, search.twin_records, config.max_alternatives)
        runtime.logger.info(
            f"Stage B assigned {len(ctx.stage_b)} of {len(search.remainder)} "
            "remaining peaks via untargeted search; of "
            f"{election.summary['measured']} list hits it measured, "
            f"{election.summary['with_rivals']} kept their peak against a "
            f"closed-shell rival and {election.summary['taken_by_rivals']} lost it "
            f"to one ({held} held by the search) "
            f"({time.perf_counter() - search.started:.1f} s)"
        )
    service._hold_opened_channel_readings(
        ctx.stage_a,
        searched_mechanisms=ctx.searched_mechanisms,
        resolved_profile=ctx.resolved_profile,
        sample_name=sample.sample_item_name,
    )
    ctx.mirror_families = service._record_mirror_readings(
        ctx.stage_a,
        searched_mechanisms=ctx.searched_mechanisms,
        resolved_profile=ctx.resolved_profile,
        max_alternatives=config.max_alternatives,
    )
    ctx.fragments = service._claim_fragments(
        ctx.stage_a + ctx.stage_b,
        resolved_profile=ctx.resolved_profile,
        searched_mechanisms=ctx.searched_mechanisms,
        sample_name=sample.sample_item_name,
    )
    ctx.series_anchored = (
        0
        if ctx.decoy_plan is not None
        else service.stamp_series(
            ctx.fragments.rows,
            await service.batch_series_by_peak(sample.sample_item_id),
        )
    )


# --- refit, judge, cap ----------------------------------------------------------


async def refit(ctx: RunContext) -> None:
    """Refit the measurement model on the ledger as the reading changes so far
    leave it: the run's own mass calibration (``mass_gate.measure_mass_gate``)
    over its corroborated commits and the source's own lines, or a decoy's
    control's. It records every row's ceiling and lowers none.

    The first round sets up the judging (``service.Judging``) over the elected
    rows, with what every round reads: the sample's peaks, read for which
    lines each envelope's peaks hold, and the run's channels, bands, context
    and measurement.
    """
    if ctx.judging is None:
        ctx.lines = SpectrumLines.from_peaks(ctx.peaks_df, ctx.resolution)
        # The cap on how bright a source line may be to anchor the mass gate,
        # which the m/z calibration node puts on its calibrants.
        ctx.source_line_cap = await service._source_line_cap(ctx.sample)
        ctx.judging = _judging_for(ctx)
    ctx.judging.refit()


def _judging_for(ctx: RunContext) -> service.Judging:
    """The judging rounds over the elected rows, under the run's rules."""
    resolved_profile = ctx.resolved_profile
    plan = ctx.decoy_plan if ctx.inherits else None
    return service.Judging(
        ctx.fragments.rows,
        stage_a_accuracy=ctx.mass_accuracy,
        fallback_sigma_ppm=resolved_profile.fallback_sigma_ppm,
        notation_by_id=service._notation_by_id(ctx.searched_mechanisms),
        mz_tolerance_ppm=ctx.scoring.mz_tolerance_ppm,
        abundance_floor=ctx.scoring.abundance_floor,
        max_alternatives=ctx.config.max_alternatives,
        lines=ctx.lines,
        tier_bands=ctx.config.tier_bands(),
        minor_channels=resolved_profile.minor_channels,
        partner_gated_channels=resolved_profile.partner_gated_channels,
        reagent_lines=ctx.reagent_rows,
        resolution=ctx.resolution,
        inherited_calibration=None if plan is None else plan.calibration,
        envelope_tolerance=ctx.scoring.envelope_tolerance,
        artifact_peak_ids=frozenset(ctx.artifact_peak_ids),
        instrument_type=ctx.instrument_type,
        listed_formulas=ctx.listed_formulas,
        source_line_cap=ctx.source_line_cap,
        context=resolved_profile.context,
        nox_skeleton=resolved_profile.nox_skeleton,
    )


async def judge(ctx: RunContext) -> None:
    """The reading changes: the partner gate settles which molecule each ion
    read through a gated channel is, against the molecules the sample shows
    through the mode's own, and the cross-channel pass records what the
    settled readings say of each other (``service.Judging.judge``)."""
    ctx.judging.judge()


async def cap(ctx: RunContext) -> None:
    """The caps, which only lower a tier: the mass gate's, the cross-channel
    pass's and the tiering pass's rules (``service.Judging.cap``). Their
    order decides which cap a row names, never which tier it ends on.

    Where the caps leave a line in doubt on an assigned neighbour's envelope,
    another round reads it as that neighbour's (:func:`run_stages`); once
    none does, the round's rows are the judged ledger.
    """
    judging = ctx.judging
    judging.cap()
    if judging.another_round():
        return
    ctx.judged = judging.result()
    ctx.tiering = {**ctx.judged.tiering, "series_anchored": ctx.series_anchored}
    _report_judging(ctx)


def _report_judging(ctx: RunContext) -> None:
    """The judging's log lines, once its rounds have settled."""
    name = ctx.sample.sample_item_name
    judged = ctx.judged
    mass_calibration = judged.mass_calibration
    cross_channel = judged.cross_channel
    tiering = ctx.tiering
    partner_gate = judged.partner_gate
    if mass_calibration["applied"]:
        trend = mass_calibration["trend"]
        centre = (
            f"a centre following {trend['offset_mda']:+.3f} mDa over m/z "
            f"{trend['mz_lo']:.0f}-{trend['mz_hi']:.0f}"
            if trend
            else f"a constant centre ({mass_calibration['trend_refused']})"
        )
        runtime.logger.info(
            f"Sample '{name}' calibrates at {mass_calibration['mu_ppm']:+.3f} ppm, "
            f"width {mass_calibration['sigma_ppm']:.3f} ppm over "
            f"{mass_calibration['anchors']} anchors "
            f"({mass_calibration['reagent_anchors']} of them the source's own "
            f"lines), judged at {centre}; {mass_calibration['capped']} of "
            f"{mass_calibration['committed'] - mass_calibration['corroborated']} "
            "uncorroborated commits capped off calibration"
        )
    else:
        runtime.logger.info(
            f"Sample '{name}' corroborated {mass_calibration['corroborated']} of "
            f"{mass_calibration['committed']} commits and had "
            f"{mass_calibration['reagent_anchors']} of the source's own lines, "
            "too few to measure a mass calibration; no row is gated on one"
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
        f"{cross_channel['partner_margin']:g}-fold margin; {ctx.mirror_families} "
        "reference-list matches carry other readings of their ion"
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
    every channel the search went through and at the width the winners were
    re-scored at (``alternatives_scoring.measure_winner_confidence``). Read off
    the judged ledger, after every pass that may swap a reading; it writes
    provenance alone, which the level then reads.
    """
    sample = ctx.sample
    rows = ctx.judged.rows
    search = ctx.search
    if ctx.config.stage_b_confidence and search is not None:
        # Imported here: the inspector's module imports the service.
        from mascope_backend.api.new.peak_assignments.alternatives_scoring import (
            WINNER_CONFIDENCE_KEY,
            measure_winner_confidence,
        )

        winner_confidence = await measure_winner_confidence(
            sample,
            rows,
            ctx.searched_mechanisms,
            ctx.match_params,
            peak_budget=ctx.config.stage_b_confidence_peaks,
            reagent_offset=service._run_scoring_centre(ctx.mass_accuracy),
            envelope_tolerance=ctx.envelope.tolerance,
            accuracy=ctx.winner_accuracy,
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
    ctx.tiering[LEVEL_SUMMARY] = apply_levels(
        rows,
        notation_by_id=service._notation_by_id(ctx.searched_mechanisms),
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


async def keep_run(ctx: RunContext) -> dict:
    """A run keeps its ledger: what it recorded of each stage on its run, the
    rows, and then its run completed and folded into the batch ledger.

    :return: The run's outcome, as the assign task reports it.
    """
    sample = ctx.sample
    judged = ctx.judged
    plan = ctx.decoy_plan
    search = ctx.search
    fragments = ctx.fragments
    await service._record_resolved_profile(
        ctx.run_id,
        ctx.config,
        ctx.resolved_profile,
        None if search is None else search.scope,
        None if search is None else search.snapshot,
        judged.mass_calibration,
        judged.cross_channel,
        ctx.tiering,
        judged.partner_gate,
        fragments.summary if fragments.summary["ladders"] else None,
        ctx.ionization_setup,
        ctx.envelope.record(),
        None if plan is None else plan.record(),
    )
    await ctx.progress(0.8)
    all_assignments, committed_rows, unassigned_assignments = ledger_rows(ctx)
    # Insert owners before children: owner_peak_assignment_id is a
    # self-referential FK validated per row during the bulk insert.
    all_assignments.sort(key=lambda row: row["owner_peak_assignment_id"] is not None)
    if all_assignments:
        async with async_session() as session:
            await session.execute(insert(PeakAssignment), all_assignments)
            # The curve the rows were scored against, committed with them. It is
            # the run's to record - one curve serves a whole run - but it has to
            # land in the same transaction as the ledger, because a run
            # interrupted after this point keeps its rows without ever being
            # finalized: the startup reaper marks it failed and leaves the
            # ledger standing, and a read that names that run by id serves rows
            # whose P(correct) would then name no curve at all.
            await session.execute(
                update(PeakAssignmentRun)
                .where(PeakAssignmentRun.peak_assignment_run_id == ctx.run_id)
                .values(confidence_calibration=ctx.confidence_calibration)
            )
            await session.commit()

    await service._finalize_run(ctx.run_id, "completed")
    await ctx.progress(1.0)

    # Fold this completed run into the batch peaks so the batch overview
    # reflects every assignment path: sample arrival, an explicit single-sample
    # re-assign, and a batch assign (which loops the run). Isolated -- a fold-in
    # failure must never fail or un-complete the assignment itself. A decoy is
    # never folded: its rows are the sample read wrong on purpose, and the
    # batch ledger is the sample's result.
    try:
        from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
            fold_sample_into_batch_peaks,
        )

        if plan is None:
            await fold_sample_into_batch_peaks(sample.sample_item_id)
    except Exception as fold_error:  # noqa: BLE001 - fold-in is best-effort
        runtime.logger.warning(
            f"Batch-peak fold-in failed for sample '{sample.sample_item_id}' "
            f"(run '{ctx.run_id}'): {fold_error}"
        )

    database_assigned = sum(
        1 for row in committed_rows if row.get("source") == SOURCE_DATABASE
    )
    untargeted_assigned = len(committed_rows) - database_assigned
    message = (
        f"Assigned peaks for sample '{sample.sample_item_name}': "
        f"{database_assigned} from the target library, "
        f"{untargeted_assigned} untargeted, "
        f"{len(unassigned_assignments)} unassigned "
        f"({len(all_assignments)} peaks total)."
    )
    runtime.logger.info(message)
    return {
        "status": "success",
        "message": message,
        "data": {
            "peak_assignment_run_id": ctx.run_id,
            "total_peaks": len(all_assignments),
            "database_assigned": database_assigned,
            "untargeted_assigned": untargeted_assigned,
            "unassigned": len(unassigned_assignments),
        },
        "_notification_data": {
            "sample_batch_id": sample.sample_batch_id,
            "sample_item_id": sample.sample_item_id,
            "peak_assignment_run_id": ctx.run_id,
        },
    }


async def keep_fold(
    ctx: RunContext, *, defer_consensus_to: set[str] | None = None
) -> str | None:
    """The ingest fold keeps its ledger as the sample's members in its batch's
    batch peaks, and writes no run (``fold_view`` serves the Sample view from
    them). Its rows are shaped as ledger rows, stamped with the derived run's
    id: the run they will never be.

    :param defer_consensus_to: As for ``fold_sample_into_batch_peaks``: a
        whole-batch walk collects the anchors touched and recomputes once.
    :return: The batch folded into.
    """
    # Imported here: the batch fold imports the service, which this module
    # composes.
    from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
        fold_sample_into_batch_peaks,
    )

    every, committed, _ = ledger_rows(ctx)
    runtime.logger.info(
        f"Stage A assigned {len(committed)} of {len(ctx.peaks_df)} peaks of sample "
        f"'{ctx.sample.sample_item_name}' ({len(ctx.reagent_rows)} claimed by the "
        f"reagent pre-pass, {len(ctx.artifact_rows)} by the artifact one, "
        f"{len(ctx.fragments.fragments)} read as fragments, "
        f"{ctx.tiering['capped']} capped by the tiering rules); folding into the "
        "batch ledger without a run"
    )
    return await fold_sample_into_batch_peaks(
        ctx.sample.sample_item_id,
        rows=[SimpleNamespace(**row) for row in every],
        persisted=False,
        defer_consensus_to=defer_consensus_to,
    )


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
    with labelled_purity(await sample_label_purity(ctx.sample.sample_item_id)):
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
