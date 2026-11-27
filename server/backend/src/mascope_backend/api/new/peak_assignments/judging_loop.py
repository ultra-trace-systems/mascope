"""The judging loop: the commits a run's stages proposed, judged until they settle.

Rows re-scored at the refitted model, the tiering rules, the caps and the
claims, round after round (``judge_commits``, ``Judging``), and the seeded
re-score the loop and its callers measure a reading at.
"""

import asyncio
import copy
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

import pandas as pd

from mascope_backend.api.models.calibration.calibration_pydantic_model import (
    OrbiCalibrationParams,
    TofCalibrationParams,
)
from mascope_backend.api.new.peak_assignments.cross_channel import (
    cap_cross_channel,
    read_cross_channel,
)
from mascope_backend.api.new.peak_assignments.engine import (
    cap_second_channel,
    evidence_for,
    read_p_correct_again,
    score_frame_at,
    tier_for_evidence,
)
from mascope_backend.api.new.peak_assignments.envelope_claims import (
    EnvelopeClaim,
    apply_claims,
    is_claimed,
)
from mascope_backend.api.new.peak_assignments.halogen_twins import (
    find_twins,
    reread_twins,
    twin_halogens,
)
from mascope_backend.api.new.peak_assignments.mass_gate import (
    OFF_CALIBRATION_Z,
    SourceLineCap,
    SpectrumLines,
    cap_mass_gate,
    measure_mass_gate,
)
from mascope_backend.api.new.peak_assignments.partner_gate import apply_partner_gates
from mascope_backend.api.new.peak_assignments.profiles import ResolvedProfile
from mascope_backend.api.new.peak_assignments.reagent_pass import (
    FragmentClaims,
    claim_fragments,
)
from mascope_backend.api.new.peak_assignments.rival_set import (
    RivalFrames,
    SeededFrame,
    measure_rival_sets,
)
from mascope_backend.api.new.peak_assignments.row_terms import (
    ROLE_ISO_CHILD,
    ROLE_M0,
    SOURCE_DATABASE,
    SOURCE_UNTARGETED,
)
from mascope_backend.api.new.peak_assignments.sample_inputs import (
    RESOLVE_RESOLUTION,
    _source_ion_current,
)
from mascope_backend.api.new.peak_assignments.seeded_scoring import score_seeds
from mascope_backend.api.new.peak_assignments.stage_a_pass import ConfidenceCurve
from mascope_backend.api.new.peak_assignments.tiering import (
    apply_tiering,
    find_envelope_claims,
)
from mascope_backend.api.new.peak_assignments.tiers import (
    TIER_ASSIGNED,
    TIER_CANDIDATE,
    TIER_RANK,
)
from mascope_backend.runtime import runtime
from mascope_file.name import get_instrument_type
from mascope_tools.composition.measurement_model import MeasurementModel
from mascope_tools.composition.profiles import IDENTITY_PROFILE_NAME, ChemistryContext
from mascope_tools.composition.reagents import FragmentLadder


#: How many times a run reads its commits again after reading lines as a
#: neighbour's. A claim takes a monoisotopic row off the ledger, and a row that
#: row's own envelope had flagged can then sit on another neighbour's line - a
#: chain as long as an envelope has lines to walk, which in practice is one or
#: two. The last reading stands whether or not it found more.
MAX_CLAIM_ROUNDS = 4


@dataclass
class JudgedCommits:
    """A sample's committed rows after every pass that judges them.

    :param rows: The rows, claims applied and released isotopologues gone.
    :param mass_calibration: The mass gate's summary.
    :param cross_channel: The cross-channel pass's summary.
    :param tiering: The tiering pass's summary, with what the claims did.
    :param partner_gate: The partner gate's summary, with its margin.
    :param fragments: What the fragment claim read as the source's
        (:meth:`Judging.judge`).
    :param model: The measurement model the rows were scored and capped at;
        None for a pass that judged none (a person's choice).
    """

    rows: list[dict]
    mass_calibration: dict
    cross_channel: dict
    tiering: dict
    partner_gate: dict
    fragments: FragmentClaims
    model: MeasurementModel | None = None


async def _reread_halogen_twins(
    matches: pd.DataFrame,
    frame: pd.DataFrame,
    *,
    resolved_profile: ResolvedProfile,
    listed_formulas: frozenset[str],
    searched_mzs,
    claimed_mzs,
    search_config,
    heuristics,
    scoring,
    candidate_threshold: float,
    search_scope: dict,
    sample_name: str,
) -> tuple[pd.DataFrame, dict[float, dict]]:
    """Read the search's halogen twins (``halogen_twins``), and record it.

    Not on the identity profile, which searches as the engine did before
    profiles existed, nor where neither the context nor the run's lists open a
    halogen (``halogen_twins.twin_halogens``). The re-read is CPU-bound, as the
    search is, so it runs on a worker thread.

    :param matches: The search's rows, as the list election left them.
    :param frame: The frame the search scored over.
    :param resolved_profile: The run's chemistry.
    :param listed_formulas: The neutrals the run's lists name.
    :param searched_mzs: The m/z of the peaks the search enumerated.
    :param claimed_mzs: The m/z of the peaks earlier passes own.
    :param search_config: The run's search configuration.
    :param heuristics: The run's filter configuration.
    :param scoring: How the run's search scored the sample.
    :param candidate_threshold: The run's candidate bar.
    :param search_scope: The run's search record, which gains
        ``halogen_twins``.
    :param sample_name: For the log.
    :return: The rows with the re-read peaks' readings in place, and the
        records :func:`halogen_twins.record_twins` names on their rows.
    """
    halogens = (
        ()
        if resolved_profile.profile.name == IDENTITY_PROFILE_NAME
        else twin_halogens(
            resolved_profile.context,
            listed_formulas,
            reagent_formula=resolved_profile.profile.reagent_formula,
        )
    )
    if not halogens:
        return matches, {}
    twins = find_twins(
        matches,
        frame,
        halogens=halogens,
        searched_mzs=searched_mzs,
        claimed_mzs=claimed_mzs,
    )
    reread = await asyncio.to_thread(
        reread_twins,
        matches,
        frame,
        twins,
        search_config=search_config,
        heuristics=heuristics,
        scoring=scoring,
        candidate_threshold=candidate_threshold,
        sigmas=OFF_CALIBRATION_Z,
    )
    search_scope["halogen_twins"] = {"halogens": list(halogens), **reread.summary}
    if reread.summary["twins"]:
        runtime.logger.info(
            f"Sample '{sample_name}' shows {reread.summary['twins']} halogen twins; "
            f"{reread.summary['reread']} read as their halogen's formula "
            f"({reread.summary['replaced']} replacing a halogen-free reading, "
            f"{reread.summary['proposed']} on a peak the search left unexplained)"
        )
    return reread.matches, reread.records


def judge_commits(
    rows: list[dict],
    *,
    model: MeasurementModel,
    fallback_sigma_ppm: float,
    notation_by_id: dict[str, str],
    mz_tolerance_ppm: float,
    abundance_floor: float,
    max_alternatives: int,
    lines: SpectrumLines | None = None,
    tier_bands: dict[str, float] | None = None,
    minor_channels: frozenset[str] = frozenset(),
    partner_gated_channels: frozenset[str] = frozenset(),
    reagent_lines: Sequence[dict] = (),
    resolution: Callable[[float], float] | None = None,
    refits: bool = True,
    rescore: Callable[[MeasurementModel], dict] | None = None,
    curve: ConfidenceCurve | None = None,
    artifact_peak_ids: frozenset[str] = frozenset(),
    instrument_type: str | None = None,
    listed_formulas: frozenset[str] = frozenset(),
    opened_channels: frozenset[str] = frozenset(),
    source_line_cap: SourceLineCap | None = None,
    context: ChemistryContext | None = None,
    nox_skeleton: bool = False,
    fragment_ladders: tuple[FragmentLadder, ...] = (),
) -> JudgedCommits:
    """Judge a sample's commits: the reading changes first, then the caps,
    which only lower a tier, and the lines the caps leave in doubt read as
    their neighbours'.

    Each round works on a fresh copy of the rows as the stages built them:

    1. **Reading changes.** The lines earlier rounds claimed are read as their
       owners' isotopologues (``envelope_claims.apply_claims``), each at
       candidate, or lower where the round that found it held the row lower,
       by its evidence or by a cap (``envelope_claims.claimed_tier``). Then
       the reading changes, to their fixed point (:meth:`Judging.judge`): the
       mass gate measures the run (``mass_gate.measure_mass_gate``): it refits
       the measurement model on the round's corroborated commits, scores every
       row again at it where it takes the refit, and records the ceiling each
       line holds every reading of it under at the model the rows were scored
       at,
       lowering nothing. The partner gate (``partner_gate.apply_partner_gates``)
       reads its partners under those ceilings and settles each gated ion's
       reading, at the tier that reading's own evidence earns; a swap keeps
       the ion, its line and its mass error, so it moves nothing the mass gate
       measured. The fragment claim (``reagent_pass.claim_fragments``) reads
       the settled ledger, and a claim takes its rows off and runs these
       again. The cross-channel pass then records what the settled readings
       say of each other (``cross_channel.read_cross_channel``), which the
       tiering pass reads. Each of the three reads "the sample shows this
       molecule" (``engine.shows_molecule``) on that one settled ledger.
    2. **Caps**: the second-channel cap (``engine.cap_second_channel``), the
       mass gate's (``mass_gate.cap_mass_gate``), the cross-channel pass's
       (``cross_channel.cap_cross_channel``) and the tiering pass's rules
       (``tiering.apply_tiering``). Each only lowers a tier, and decides on the readings and records step 1 left: a bar on
       another row's tier is read under the mass gate's ceiling
       (``engine.tier_under_ceiling``), and that gate is the only cap that
       takes a row under candidate. So their order decides which of them a
       row names, never which tier it ends on.
    3. **Claims.** The rows on an assigned neighbour's line that the caps left
       in doubt are read as that line (``tiering.find_envelope_claims``). A
       claim changes the ledger: a monoisotopic row becomes an isotopologue,
       so it no longer anchors the run's calibration or draws the line its
       centre follows, no longer counts as a channel for its neutral, and no
       longer predicts lines of its own. So a round that finds a new claim is
       not kept: the claims join the earlier ones, and the round runs again
       from the stages' rows, until the reading changes reach a fixed point,
       a round that finds nothing new (:data:`MAX_CLAIM_ROUNDS`).

    The rows passed in are not modified.

    :param rows: Both stages' committed rows, as built.
    :param model: The measurement model the rows were scored at: the run's v1,
        a decoy's control's, or the sample's recorded one. Its line-share
        tolerance is the one whose detectability threshold the tiering pass
        asks a row's lines from (``tiering.detect_threshold``).
    :param fallback_sigma_ppm: The instrument class's width and precision.
    :param notation_by_id: The searched mechanisms (``read_cross_channel``).
    :param mz_tolerance_ppm: The run's match window.
    :param abundance_floor: The run's envelope floor.
    :param max_alternatives: Cap on stored alternatives per row.
    :param lines: The sample's peaks, read for how well each places its line.
    :param tier_bands: The run's evidence bands, which a row under the top one
        names first among its reasons.
    :param minor_channels: The run's opportunistic channels. A molecule the
        sample commits through any other channel is one it shows, which the
        cross-channel pass does not let a second channel settle against.
    :param partner_gated_channels: Those of them held to a partner
        (``partner_gate.apply_partner_gates``), read over both stages' rows after
        the mass gate's measurement, so a partner is a reading its ceiling
        leaves committed, and before every cap, so a reading set aside is no
        rival in the cross-channel pass.
    :param reagent_lines: The reagent pre-pass's rows, whose monoisotopic lines
        anchor the mass gate's calibration beside the commits
        (``mass_gate.fit_run_mass_accuracy``). Read only, and the same in every
        round: a claim moves a commit, never one of the source's lines.
    :param resolution: The file's resolving power as a function of m/z, at
        which the tiering pass and the claims read a neighbour's envelope, one
        line per peak the file resolves; None where the run could read none.
    :param refits: Whether each round refits the model on its commits (v2);
        False judges every round at ``model``: a decoy run's control's, whose
        commits are wrong formulas on a moved axis, and the sample's recorded
        one for a path that measured nothing of its own.
    :param rescore: Every reading's fit at a model the refit takes
        (:func:`fits_at` over the frames the rows were scored on), which the
        round sets on its rows (:func:`rescore_rows`); None scores none again.
    :param curve: The curve Stage A read its rows' ``p_correct`` off, which a
        re-scored row reads again at its moved evidence; None reads none.
    :param artifact_peak_ids: The peaks the artifact pre-pass claimed, which
        the tiering pass reads as neither a line nor an absence.
    :param instrument_type: The sample's instrument class, which on a TOF
        holds a bromine or chlorine reading to its own M+2 line.
    :param listed_formulas: The neutrals the sample's lists name
        (``engine.listed_formula_identities``), which corroborate a search's
        reading through a secondary channel (``engine.cap_second_channel``).
    :param opened_channels: The secondary channels the run opened
        (``ResolvedProfile.added_channels``), a list's readings through which
        the second-channel cap judges.
    :param source_line_cap: The calibrant cap the source's lines anchor the
        mass gate under (:func:`_source_line_cap`), or None for none.
    :param context: The run's chemistry context, whose priors the tiering
        pass asks (``tiering.context_prior_reason``); None asks none.
    :param nox_skeleton: Whether the run reads the carbon skeleton of an
        organonitrate, whose readings the tiering pass holds at candidate
        (``tiering.nox_skeleton_reason``).
    :param fragment_ladders: The profile's fragment ladders, whose fragments
        the reading changes read as the source's; none claims none.
    :return: The judged rows, each pass's summary, and the fragments.
    """
    judging = Judging(
        rows,
        model=model,
        fallback_sigma_ppm=fallback_sigma_ppm,
        notation_by_id=notation_by_id,
        mz_tolerance_ppm=mz_tolerance_ppm,
        abundance_floor=abundance_floor,
        max_alternatives=max_alternatives,
        lines=lines,
        tier_bands=tier_bands,
        minor_channels=minor_channels,
        partner_gated_channels=partner_gated_channels,
        reagent_lines=reagent_lines,
        resolution=resolution,
        refits=refits,
        rescore=rescore,
        curve=curve,
        artifact_peak_ids=artifact_peak_ids,
        instrument_type=instrument_type,
        listed_formulas=listed_formulas,
        opened_channels=opened_channels,
        source_line_cap=source_line_cap,
        context=context,
        nox_skeleton=nox_skeleton,
        fragment_ladders=fragment_ladders,
    )
    while True:
        judging.refit()
        judging.judge()
        judging.cap()
        if not judging.another_round():
            return judging.result()


@dataclass
class Judging:
    """A sample's commits through the judging rounds: the refit, judge and cap
    stages of the stage list (``pipeline.ROUND``), each a method here, run in
    that order and repeated until the reading changes reach a fixed point
    (:meth:`another_round`). :func:`judge_commits` says what a round does;
    the parameters are its own, and ``rows`` are never modified.

    :ivar floors: The tier a batch decision proposes its reading at, by the
        peak it is proposed on (``pipeline.RunContext.floors``), which a
        re-score lowers no such row below (:func:`rescore_rows`): only a cap
        does. Empty for a pass with no batch decision.
    :ivar given_channels: The secondary channels the sample's spectrum did
        not open that a batch decision measured on it reads through
        (``ResolvedProfile.given_channels``). The cross-channel pass and the
        tiering pass count a row through one as no second channel of the
        sample's own readings (``cross_channel.read_cross_channel``,
        MAS-1039). Empty for a run of the sample's own.
    :ivar claims: The lines earlier rounds read as their owners', by row.
    :ivar rounds: The rounds begun.
    :ivar applied: The round's rows as it began: a fresh copy of ``rows`` with
        the claims applied. With fragment ladders nothing modifies it, since a
        fragment claim measures the round again from it (:meth:`judge`).
        Without them nothing reads it again, and it is ``judged`` itself: the
        round's passes modify it.
    :ivar judged: The round's rows: a copy of ``applied`` without the
        fragments claimed so far, which the round's passes then modify (with
        no fragment ladders, ``applied`` itself).
    :ivar fragments: What the fragment claim read as the source's in the
        round, over every pass of it (:meth:`judge`).
    :ivar found: The claims the round's caps left, which another round
        applies; ``held`` says what held the rest back.
    """

    rows: list[dict]
    model: MeasurementModel
    fallback_sigma_ppm: float
    notation_by_id: dict[str, str]
    mz_tolerance_ppm: float
    abundance_floor: float
    max_alternatives: int
    lines: SpectrumLines | None = None
    tier_bands: dict[str, float] | None = None
    minor_channels: frozenset[str] = frozenset()
    partner_gated_channels: frozenset[str] = frozenset()
    reagent_lines: Sequence[dict] = ()
    resolution: Callable[[float], float] | None = None
    refits: bool = True
    rescore: Callable[[MeasurementModel], dict] | None = None
    curve: ConfidenceCurve | None = None
    floors: dict[str, str] = field(default_factory=dict)
    artifact_peak_ids: frozenset[str] = frozenset()
    instrument_type: str | None = None
    listed_formulas: frozenset[str] = frozenset()
    opened_channels: frozenset[str] = frozenset()
    given_channels: frozenset[str] = frozenset()
    source_line_cap: SourceLineCap | None = None
    context: ChemistryContext | None = None
    nox_skeleton: bool = False
    fragment_ladders: tuple[FragmentLadder, ...] = ()
    rival_frames: RivalFrames | None = None
    claims: dict[str, EnvelopeClaim] = field(default_factory=dict)
    judged_at: MeasurementModel | None = None
    rounds: int = 0
    applied: list[dict] = field(default_factory=list)
    judged: list[dict] = field(default_factory=list)
    fragments: FragmentClaims | None = None
    mass_calibration: dict = field(default_factory=dict)
    partner_gate: dict = field(default_factory=dict)
    cross_channel: dict = field(default_factory=dict)
    second_channel: dict = field(default_factory=dict)
    tiering: dict = field(default_factory=dict)
    found: list[EnvelopeClaim] = field(default_factory=list)
    held: dict = field(default_factory=dict)
    rival_sets: dict = field(default_factory=dict)

    def refit(self) -> None:
        """Begin a round: the rows as the claims so far leave them, the
        measurement model refitted on them where the path fits its own (v2),
        the rows scored again at it where the refit takes it, and the mass
        gate's measurement of them at the model they were scored at
        (``mass_gate.measure_mass_gate``), which records every row's ceiling
        and lowers none. A claim moves which rows anchor the refit, so it is
        measured each round, from the model the rows were first scored at."""
        self.rounds += 1
        self.applied = apply_claims(
            copy.deepcopy(self.rows),
            self.claims.values(),
            max_alternatives=self.max_alternatives,
        )
        self._measure(
            copy.deepcopy(self.applied) if self.fragment_ladders else self.applied
        )

    def _measure(self, ledger: list[dict]) -> None:
        """Take ``ledger`` as the round's rows: the model refitted on them
        where the path fits its own, the rows scored again at it where the
        refit takes it, and the mass gate's measurement of them at the model
        they were scored at."""
        self.judged = ledger

        def rescore(model: MeasurementModel) -> int:
            if self.rescore is None:
                return 0
            return rescore_rows(
                ledger,
                self.rescore(model),
                self.tier_bands,
                self.curve,
                floors=self.floors,
            )

        self.mass_calibration, self.judged_at = measure_mass_gate(
            ledger,
            model=self.model,
            fallback_sigma_ppm=self.fallback_sigma_ppm,
            lines=self.lines,
            reagent_lines=self.reagent_lines,
            source_line_cap=self.source_line_cap,
            refit=self.refits,
            rescore=rescore,
        )

    def judge(self) -> None:
        """The reading changes, to their fixed point, and what the caps read
        of them.

        1. The partner gate settles each gated ion's reading against the
           molecules the sample shows (``partner_gate.apply_partner_gates``), under
           the ceilings the refit recorded.
        2. The fragment claim reads the settled ledger
           (``reagent_pass.claim_fragments``): a reading of a fragment's ion is
           the source's where no reading of it names a molecule the sample
           shows elsewhere. A fragment read as a molecule is a partner, a
           second channel and a rival to every other reading of its mass, so
           a claim is not kept on this ledger: the claimed rows leave, the
           round's rows are measured again without them, and the changes run
           again, until a pass claims nothing new.
        3. The cross-channel pass records what the settled readings say of
           each other (``cross_channel.read_cross_channel``).

        So "the sample shows this molecule" (``engine.shows_molecule``) is
        read by the partner gate, the fragment claim and the cross-channel
        pass on one ledger: the one these changes settled, measured, and
        capped by nothing yet.
        """
        taken: set[str] = set()
        fragments: list[dict] = []
        peak_ids: set[str] = set()
        records: list[dict] = []
        claimed = claimed_isotopologues = 0
        while True:
            self.partner_gate = apply_partner_gates(
                self.judged,
                notation_by_id=self.notation_by_id,
                minor_channels=self.minor_channels,
                partner_gated_channels=self.partner_gated_channels,
                tier_bands=self.tier_bands,
            )
            found = claim_fragments(
                self.judged,
                self.fragment_ladders,
                notation_by_id=self.notation_by_id,
                minor_channels=self.minor_channels,
            )
            if not found.summary["claimed"]:
                self.fragments = FragmentClaims(
                    self.judged,
                    fragments,
                    peak_ids,
                    {
                        **found.summary,
                        "claimed": claimed,
                        "claimed_isotopologues": claimed_isotopologues,
                        "claims": records,
                    },
                )
                break
            left = {id(row) for row in found.rows}
            taken |= {
                str(row.get("peak_assignment_id"))
                for row in self.judged
                if id(row) not in left
            }
            fragments += found.fragments
            peak_ids |= found.peak_ids
            records += found.summary["claims"]
            claimed += found.summary["claimed"]
            claimed_isotopologues += found.summary["claimed_isotopologues"]
            self._measure(
                [
                    row
                    for row in copy.deepcopy(self.applied)
                    if str(row.get("peak_assignment_id")) not in taken
                ]
            )
        self.cross_channel = read_cross_channel(
            self.judged,
            notation_by_id=self.notation_by_id,
            minor_channels=self.minor_channels,
            given_channels=self.given_channels,
        )

    def cap(self) -> None:
        """The caps, which only lower a tier, in the order that decides which
        of them a row names; then the lines on an assigned neighbour's
        envelope that they left in doubt, which another round reads as that
        neighbour's (:meth:`another_round`).

        Before any of them, each committed row's rival set is measured at
        the model the round's rows were scored at, the one the caps read
        (``rival_set.measure_rival_sets``): the density the tiering pass caps
        on and the arbitration's confidence and tie are read off it.

        The second-channel cap comes first (``engine.cap_second_channel``): a
        reading through a secondary channel that nothing corroborates, read
        on the ledger the reading changes settled, with the lists."""
        if self.rival_frames is not None:
            self.rival_sets = measure_rival_sets(
                self.judged, self.rival_frames, self.judged_at or self.model
            )
        self.second_channel = cap_second_channel(
            self.judged,
            notation_by_id=self.notation_by_id,
            minor_channels=self.minor_channels,
            opened_channels=self.opened_channels,
            listed_formulas=self.listed_formulas,
        )
        cap_mass_gate(self.judged, self.mass_calibration)
        cap_cross_channel(self.judged, self.cross_channel)
        self.tiering = apply_tiering(
            self.judged,
            mz_tolerance_ppm=self.mz_tolerance_ppm,
            abundance_floor=self.abundance_floor,
            notation_by_id=self.notation_by_id,
            tier_bands=self.tier_bands,
            resolution=self.resolution,
            lines=self.lines,
            envelope_tolerance=self.model.envelope_tolerance,
            artifact_peak_ids=self.artifact_peak_ids,
            instrument_type=self.instrument_type,
            context=self.context,
            nox_skeleton=self.nox_skeleton,
            given_channels=self.given_channels,
        )
        self.found, self.held = find_envelope_claims(
            self.judged,
            mz_tolerance_ppm=self.mz_tolerance_ppm,
            abundance_floor=self.abundance_floor,
            precision_ppm=self.fallback_sigma_ppm,
            lines=self.lines,
            resolution=self.resolution,
        )

    def another_round(self) -> bool:
        """Whether the round just capped found a claim no round applied, and
        the rounds have not run out (:data:`MAX_CLAIM_ROUNDS`): the claims
        then join the earlier ones for the next. Otherwise the round's rows
        stand, and the tiering record says what the claims did.

        :return: True where another round is to run.
        """
        if not self.found or self.rounds >= MAX_CLAIM_ROUNDS:
            self.tiering.update(
                # The readings through a secondary channel the second-channel
                # cap judged, and held.
                second_channel=self.second_channel,
                # The rows whose rival set was measured, and at which model.
                **({"rival_sets": self.rival_sets} if self.rival_sets else {}),
                claim_rounds=self.rounds,
                # Rows on an assigned neighbour's line that stayed as they were,
                # by the reason that held each back.
                held=self.held,
                # Isotopologues that left the ledger with the reading they
                # belonged to.
                released=sum(len(claim.released) for claim in self.claims.values()),
                # Claims the last round found and no round applied: none unless
                # the rounds ran out.
                unapplied=len(self.found),
            )
            return False
        self.claims.update((claim.row_id, claim) for claim in self.found)
        return True

    def result(self) -> JudgedCommits:
        """The rows the last round left, each pass's summary, and the model
        they were scored and capped at."""
        return JudgedCommits(
            self.judged,
            self.mass_calibration,
            self.cross_channel,
            self.tiering,
            self.partner_gate,
            self.fragments,
            self.judged_at or self.model,
        )


async def _source_line_cap(sample) -> SourceLineCap | None:
    """The calibrant cap the mass gate reads the source's own lines by.

    The m/z calibration node's default for the file's instrument class
    (``OrbiCalibrationParams``, ``TofCalibrationParams``), held against the
    mean ion current per scan of the sample's polarity, which a line's
    averaged height (:func:`load_sample_peaks`) is a share of. Where the
    file's store stitches the polarity, each line is held against its own
    stream's instead (:func:`_source_ion_current`). None where the class has
    no cap, the TOF's, and where the ion current cannot be read: the
    source's lines then anchor the gate as they did before there was a cap,
    which is said.

    :param sample: The sample view row.
    :return: The cap, or None for none.
    """
    try:
        instrument_type = get_instrument_type(sample.filename)
    except ValueError:
        return None
    params = (
        OrbiCalibrationParams() if instrument_type == "orbi" else TofCalibrationParams()
    )
    if params.calibrant_tic_share_max is None and params.peak_intensity_max is None:
        return None
    try:
        tic, tic_by_peak = await asyncio.to_thread(
            _source_ion_current, sample.filename, sample.polarity
        )
    except Exception as exc:  # noqa: BLE001 - the lines anchor uncapped
        runtime.logger.info(
            f"No ion current for sample '{sample.sample_item_name}' ({exc}); "
            "its source's lines anchor the mass gate however bright"
        )
        return None
    return SourceLineCap(
        tic=tic,
        tic_share_max=params.calibrant_tic_share_max,
        peak_intensity_max=params.peak_intensity_max,
        tic_by_peak=tic_by_peak,
    )


async def _seeded_rescore(
    sample,
    match_params,
    seeds: set[tuple[str, str]],
    *,
    model: MeasurementModel,
    resolution: Callable[[float], float] | None | object = RESOLVE_RESOLUTION,
    named: Mapping[tuple[str, str], frozenset[str]] | None = None,
    fit_only: Mapping[tuple[str, str], frozenset[str]] | None = None,
) -> tuple[dict[tuple[str, str], float | None], SeededFrame | None]:
    """Measure the untargeted stage's readings the way Stage A measures one.

    One ``compute_match_isotopes`` pass over the sample for the whole seed list,
    the run's match-params gating, the ion-level v2 fit with the file's own
    per-peak signal-to-noise - the chain in :mod:`seeded_scoring`, which the
    batch ledger's propagation and the inspector's shortlist already measure
    through. The finder scored these readings too, against the peak list, and
    that score is what ranked them; this is what they are tiered on, and it is
    the same quantity a Stage A row carries, at the same model: the run's,
    which the finder ranked at too. The winners fit no model of their own.

    :param sample: The sample being assigned.
    :param match_params: The sample's resolved match parameters.
    :param seeds: ``(formula, mechanism id)`` pairs, from ``untargeted_seeds``.
    :param model: The measurement model the run scores at: a run's v1 or its
        decoy's control's, a batch search's the sample's recorded one.
    :param resolution: The file's resolving power as the run read it, the
        seeds' lines are resolved at; left out, the chain reads it.
    :param named: The readings the search named against a peak's reading
        (``rival_set.named_rival_seeds``), each with the peaks it was named
        for, measured in the same pass so a row's rivals are read in the
        frame its own reading is (``rival_set.SeededFrame.placed_for``). A
        seed the search also commits is measured as the reading it commits.
    :param fit_only: The readings the search named against a list reading by
        their fit alone (``rival_set.fit_tied_seeds``), placed as ``named``
        are, which the set records apart (``rival_set.SeededFrame.fit_only``).
    :return: Fit per committed seed, where a seed whose ion the pass could not
        score is absent and its rows fall back to the finder's own number; and
        the frame it scored, None where it scored nothing.
    """
    fit_only = {
        seed: peaks for seed, peaks in (fit_only or {}).items() if seed not in seeds
    }
    named = {seed: peaks for seed, peaks in (named or {}).items() if seed not in seeds}
    for seed, peaks in fit_only.items():
        named[seed] = named.get(seed, frozenset()) | peaks
    if not seeds and not named:
        return {}, None
    started = time.perf_counter()
    ion_by_seed, fit_by_ion, _errors, scored_df = await score_seeds(
        sample, seeds | set(named), match_params, model=model, resolution=resolution
    )
    fits = {
        seed: fit_by_ion.get(ion_id)
        for seed, ion_id in ion_by_seed.items()
        if seed in seeds and fit_by_ion.get(ion_id) is not None
    }
    if len(fits) < len(seeds):
        runtime.logger.info(
            f"Seeded re-score of sample '{sample.sample_item_name}': "
            f"{len(fits)} of {len(seeds)} untargeted readings measured as ions; "
            "the rest keep the finder's own fit"
        )
    return fits, (
        None
        if scored_df.empty
        else SeededFrame(
            scored_df,
            ion_by_seed,
            named,
            fit_only,
            seconds=round(time.perf_counter() - started, 3),
        )
    )


def fits_at(
    model: MeasurementModel,
    stage_a: pd.DataFrame | None,
    seeded: SeededFrame | None,
    absent: pd.DataFrame | None = None,
) -> dict[str, dict[tuple[str, str], float]]:
    """Every scored reading's fit at a model, from the frames the run scored
    its rows on: Stage A's and the seeded re-score's.

    The same lines, the same gate's verdict, the same tolerance; only the
    centre and the width are the model's (``engine.score_frame_at``). Keyed
    by the row's source, then by ``(formula, mechanism id)``, the key a ledger
    row carries in ``assigned_formula`` and ``ionization_mechanism_id``: a
    Stage A row's compound formula, a search row's seed.

    :param model: The model to score at.
    :param stage_a: Stage A's gated, scored frame, or None.
    :param seeded: The seeded re-score's frame, or None.
    :param absent: The reference ions Stage A left out of its frame
        (``sample_inputs.StageAKnown``), or None. No line of one reaches a
        peak, so its base line is absent and it scores 0 at any model
        (``match_score_v2.ion_scores_v2``), as it would in the frame.
    :return: Fit by reading, under :data:`SOURCE_DATABASE` for Stage A's
        frame and :data:`SOURCE_UNTARGETED` for the re-score's.
    """
    fits: dict[tuple[str, str], float] = {}
    searched: dict[tuple[str, str], float] = {}
    if (
        stage_a is not None
        and not stage_a.empty
        and {
            "target_compound_formula",
            "ionization_mechanism_id",
        }.issubset(stage_a.columns)
    ):
        scores = score_frame_at(stage_a, model)
        for formula, mechanism_id, fit in zip(
            stage_a["target_compound_formula"],
            stage_a["ionization_mechanism_id"],
            scores,
            strict=True,
        ):
            if formula is None or mechanism_id is None or pd.isna(fit):
                continue
            fits.setdefault((str(formula), str(mechanism_id)), float(fit))
    if absent is not None and not absent.empty:
        for formula, mechanism_id in zip(
            absent["target_compound_formula"],
            absent["ionization_mechanism_id"],
            strict=True,
        ):
            if formula is not None and mechanism_id is not None:
                fits.setdefault((str(formula), str(mechanism_id)), 0.0)
    if seeded is not None:
        scores = score_frame_at(seeded.scored, model)
        fit_by_ion: dict[str, float] = {}
        for ion_id, fit in zip(seeded.scored["target_ion_id"], scores, strict=True):
            if not pd.isna(fit):
                fit_by_ion.setdefault(str(ion_id), float(fit))
        for seed, ion_id in seeded.ion_by_seed.items():
            if str(ion_id) in fit_by_ion:
                searched[(str(seed[0]), str(seed[1]))] = fit_by_ion[str(ion_id)]
    return {SOURCE_DATABASE: fits, SOURCE_UNTARGETED: searched}


def rescore_rows(
    rows: list[dict],
    fits: dict[str, dict[tuple[str, str], float]],
    tier_bands: dict[str, float] | None,
    curve: ConfidenceCurve | None = None,
    *,
    floors: dict[str, str] | None = None,
) -> int:
    """Score a run's rows again at the fits :func:`fits_at` read, in place.

    Each committed row whose reading the fits name takes its new fit; its
    evidence moves with it, by the fit's ratio, so a list's prior and any
    other weight on it stand; and its tier is the band its new evidence earns,
    lowered no further than an earlier stage had already lowered it below the
    band its old evidence earned (a list election's). A reading a batch
    decision proposed at a tier falls no lower than that tier
    (:func:`raise_to_floors`): the decision, not the evidence, put it there,
    and only a cap takes it lower (MAS-983).
    A row the frames did not score keeps its fit. Before any cap: the caps
    judge the rows from here.

    What else a row records of its fit moves with it:

    * its ``p_correct``, read again off the curve at the new evidence, with the
      adduct corroboration folded again (``engine.read_p_correct_again``);
    * each stored alternative Stage A's frame scored, which takes that
      reading's fit at the model; and on a target library's row each other
      reading of its own ion, written at the row's fit, which follows it.

    Which formula won each peak stays as it was ranked: the search ranked at
    v1 too. The arbitration's confidence and tie and the candidate density
    are not read here: the round reads them off each row's rival set, at the
    model it caps at (``rival_set.measure_rival_sets``).

    :param rows: The run's rows.
    :param fits: Fit by source and ``(formula, mechanism id)``.
    :param tier_bands: The run's bands; None moves no tier.
    :param curve: The curve Stage A read ``p_correct`` off; None reads none
        again.
    :param floors: The tier a batch decision proposes its reading at, by the
        peak it is proposed on; None for none.
    :return: How many rows took a new fit.
    """
    bands = tier_bands or {}
    candidate, assigned = bands.get(TIER_CANDIDATE), bands.get(TIER_ASSIGNED)
    listed = fits.get(SOURCE_DATABASE, {})
    rescored = 0
    moved: set[str] = set()
    for row in rows:
        if not row.get("assigned_formula") or row.get("role") not in (
            ROLE_M0,
            ROLE_ISO_CHILD,
        ):
            continue
        key = (str(row["assigned_formula"]), str(row.get("ionization_mechanism_id")))
        # The frame that scored the row's own stage first: a reading a path
        # gave rather than searched was scored in the seeded frame whichever
        # stage it is read as.
        own = (
            SOURCE_UNTARGETED
            if row.get("source") == SOURCE_UNTARGETED
            else (SOURCE_DATABASE)
        )
        other = SOURCE_DATABASE if own == SOURCE_UNTARGETED else SOURCE_UNTARGETED
        new = fits.get(own, {}).get(key, fits.get(other, {}).get(key))
        old = row.get("fit_score")
        if new is not None and old is not None:
            new = round(float(new), 4)
            if new != old:
                rescored += 1
                row["fit_score"] = new
                if _move_evidence(row, float(old), new, candidate, assigned):
                    moved.add(str(row.get("peak_assignment_id")))
        _rescore_alternatives(row, listed, followed=(old, row.get("fit_score")))
    raise_to_floors(rows, floors or {})
    if curve is not None and moved and candidate is not None and assigned is not None:
        read_p_correct_again(
            rows,
            moved,
            curve.calibration,
            curve.notation_by_mechanism,
            candidate_threshold=candidate,
            assigned_threshold=assigned,
        )
    return rescored


def raise_to_floors(rows: list[dict], floors: dict[str, str]) -> int:
    """Raise each monoisotopic row on a peak a batch decision proposes its
    reading at a tier (``pipeline.RunContext.floors``) to that tier, where its
    evidence left it lower: where the elect stage proposes it, and again where
    a re-score moves its evidence (:func:`rescore_rows`). Only a cap may
    lower it again.

    :return: How many rows were raised.
    """
    raised = 0
    for row in rows:
        floor = floors.get(str(row.get("sample_peak_id")))
        if floor is None or row.get("role") != ROLE_M0:
            continue
        if TIER_RANK.get(row.get("tier"), 0) < TIER_RANK[floor]:
            row["tier"] = floor
            raised += 1
    return raised


def _move_evidence(
    row: dict,
    old: float,
    new: float,
    candidate: float | None,
    assigned: float | None,
) -> bool:
    """Move a re-scored row's evidence by its fit's ratio, and its tier to the
    band that evidence earns (:func:`rescore_rows`).

    A row an earlier stage held below the band its old evidence earned stays
    held: it takes the lower of the two. So does a line an envelope claim
    reads (``envelope_claims.apply_claims``): the claim reads it no higher
    than candidate whatever its owner's evidence earns
    (``envelope_claims.claimed_tier``), and it takes its owner's evidence, so
    the band that evidence earned before says nothing of that ceiling. The
    claim is applied each round to the rows as the stages built them, before
    the re-score, and no cap lowers the line on its own after it. A reading
    through a secondary channel needs no hold of its own: the second-channel
    cap judges it after the re-score, at its new evidence
    (``engine.cap_second_channel``).

    :return: Whether the row carries evidence that moved.
    """
    provenance = row.setdefault("provenance", {})
    evidence = provenance.get("evidence")
    if evidence is None:
        return False
    moved = (
        round(float(evidence) * new / old, 4)
        if old > 0
        else evidence_for(new, row["assigned_formula"])
    )
    provenance["evidence"] = moved
    if candidate is None or assigned is None or row.get("tier") not in TIER_RANK:
        return True
    earned_before = tier_for_evidence(
        float(evidence), candidate_threshold=candidate, assigned_threshold=assigned
    )
    earned = tier_for_evidence(
        moved, candidate_threshold=candidate, assigned_threshold=assigned
    )
    tier = row["tier"]
    held = TIER_RANK[tier] < TIER_RANK[earned_before] or is_claimed(row)
    row["tier"] = min(earned, tier, key=TIER_RANK.__getitem__) if held else earned
    return True


def _rescore_alternatives(
    row: dict,
    listed: dict[tuple[str, str], float],
    *,
    followed: tuple[float | None, float | None],
) -> None:
    """Give a row's stored alternatives their fits at the model, in place
    (:func:`rescore_rows`).

    An alternative of the target library's frame takes its reading's fit there,
    on whichever row stores it. On a target library's row, another reading of
    the row's own ion that no frame scored was written at the row's fit
    (``engine.record_stage_a_same_ion_readings``), and follows it.

    :param row: The row.
    :param listed: Stage A's fits by ``(formula, mechanism id)``.
    :param followed: The row's fit before the re-score and after it.
    """
    old, new = followed
    for alternative in row.get("alternatives") or ():
        if not isinstance(alternative, dict) or alternative.get("fit_score") is None:
            continue
        key = (
            str(alternative.get("assigned_formula")),
            str(alternative.get("ionization_mechanism_id")),
        )
        if alternative.get("source") == SOURCE_DATABASE and key in listed:
            alternative["fit_score"] = round(float(listed[key]), 4)
        elif (
            alternative.get("same_ion")
            and row.get("source") == SOURCE_DATABASE
            and old is not None
            and new is not None
            and alternative["fit_score"] == old
        ):
            alternative["fit_score"] = new
