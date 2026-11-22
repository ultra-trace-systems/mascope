"""Stage A: every peak matched against the known isotopologue library.

The database-first pass: the targeted matcher's lines inverted from
target-anchored to peak-anchored rows, scored at the run's model, with the
confidence curve its ``p_correct`` is read off and the neighbouring files'
envelope lines a thin file's tolerance is pooled from.
"""

import asyncio
import weakref
from collections.abc import Callable
from dataclasses import dataclass
from types import SimpleNamespace

import pandas as pd

from mascope_backend.api.lib.exceptions.api_exceptions import NotFoundException
from mascope_backend.api.new.cheminfo.utils import to_custom_element_format
from mascope_backend.api.new.ionization.modes.versions import sample_label_purity
from mascope_backend.api.new.peak_assignments.calibration_store import load_calibration
from mascope_backend.api.new.peak_assignments.config import PeakAssignmentConfig
from mascope_backend.api.new.peak_assignments.engine import (
    STAGE_A_FIT_MIN_IONS,
    calibration_meta,
    drop_ions_claimed_elsewhere,
    invert_matches_to_peak_assignments,
    listed_formula_identities,
    record_stage_a_same_ion_readings,
    sample_mass_accuracy,
    score_ions_by_fit,
)
from mascope_backend.api.new.peak_assignments.envelope_fit import (
    NeighbourReader,
    RunEnvelopeFit,
    envelope_lines,
    fit_run_envelope_tolerance,
)
from mascope_backend.api.new.peak_assignments.profiles import ResolvedProfile
from mascope_backend.api.new.peak_assignments.sample_inputs import (
    _fetch_known_isotopes,
    ineligible_reason,
    load_sample_peaks,
)
from mascope_backend.api.new.peak_assignments.sample_mechanisms import (
    _untargeted_ionization_notations,
)
from mascope_backend.api.new.peak_assignments.seeded_scoring import (
    RESOLVE_RESOLUTION,
    gate_lines,
)
from mascope_file.name import get_instrument_type
from mascope_match import compute_match_isotopes
from mascope_tools.composition.calibration import Calibration
from mascope_tools.composition.custom_elements import labelled_purity
from mascope_tools.composition.envelope_tolerance import (
    FORM_CURVE,
    LEVEL_DEFAULT,
    EnvelopeLines,
)
from mascope_tools.composition.heuristic_filter import SCORE_VERSION
from mascope_tools.composition.known_window import KnownWindow
from mascope_tools.composition.mass_accuracy import ReagentOffset, SampleMassAccuracy
from mascope_tools.composition.measurement_model import MeasurementModel


def _record_same_ion_readings(
    stage_a_assignments: list[dict],
    *,
    searched_mechanisms: list[SimpleNamespace],
    resolved_profile: ResolvedProfile,
    max_alternatives: int,
) -> int:
    """Give each Stage A row its ion's family, which reads no tier and so is
    written once however many times the passes after it run.

    It is built under the untargeted search's own box and filter, whether or
    not that stage ran, so it is the family the search would have held. Every
    path of the stage list writes it in its elect stage, before the fragment
    claim and the judging rounds that read the families (:class:`Judging`).

    :return: How many Stage A rows carry same-ion readings.
    """
    notations, mechanism_id_by_notation = _untargeted_ionization_notations(
        searched_mechanisms
    )
    return record_stage_a_same_ion_readings(
        stage_a_assignments,
        mechanism_id_by_notation=mechanism_id_by_notation,
        search_config=resolved_profile.search_config(notations),
        heuristics_config=resolved_profile.heuristics_config(),
        formula_formatter=to_custom_element_format,
        max_alternatives=max_alternatives,
    )


@dataclass(frozen=True)
class ConfidenceCurve:
    """The curve Stage A read its rows' ``p_correct`` off, and the notation
    each mechanism's adducts were folded under, so a row the run scores again
    reads its ``p_correct`` again as Stage A would have
    (``engine.read_p_correct_again``).

    :param calibration: The instrument's confidence curve.
    :param notation_by_mechanism: Each mechanism's notation as Stage A's frame
        names it.
    """

    calibration: Calibration
    notation_by_mechanism: dict[str, str]

    @classmethod
    def of(
        cls, calibration: Calibration | None, frame: pd.DataFrame
    ) -> "ConfidenceCurve | None":
        """The curve and the notations of the frame Stage A read through it;
        None where the instrument has no curve, so no row has a number."""
        if calibration is None:
            return None
        notations: dict[str, str] = {}
        if {"ionization_mechanism_id", "ionization_mechanism"}.issubset(frame.columns):
            for mechanism_id, notation in zip(
                frame["ionization_mechanism_id"],
                frame["ionization_mechanism"],
                strict=True,
            ):
                if mechanism_id is None or notation is None or pd.isna(notation):
                    continue
                notations.setdefault(str(mechanism_id), str(notation))
        return cls(calibration, notations)


@dataclass(frozen=True)
class _StageAFirstPass:
    """Stage A's first pass: the frame scored at the tolerance it was handed and
    the rows arbitrated on it (:func:`_stage_a_first_pass`).

    :param rows: The assignment rows.
    :param scored: The gated, fit-scored frame, which the envelope fit reads.
    :param matched: The frame as matched, before any gate, which a second pass
        gates and scores again at the fitted tolerance (:func:`gate_lines`).
    :param mass_accuracy: What the frame says of the sample's mass error.
    :param confidence_calibration: The record of the confidence curve the rows'
        P(correct) came from; None where Stage A never ran or the instrument
        has no curve.
    :param listed_formulas: The neutrals the lists read name, matched or not.
    :param assignments_of: Arbitrates a scored frame into rows as this pass
        did; None where the lists predicted nothing.
    :param curve: The curve the rows' P(correct) was read off, which a re-score
        reads again (:func:`rescore_rows`); None where there is none.
    """

    rows: list[dict]
    scored: pd.DataFrame
    matched: pd.DataFrame
    mass_accuracy: SampleMassAccuracy
    confidence_calibration: dict | None
    listed_formulas: frozenset[str]
    assignments_of: Callable[[pd.DataFrame], list[dict]] | None = None
    curve: ConfidenceCurve | None = None


async def _stage_a_first_pass(
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
) -> _StageAFirstPass:
    """Stage A's first pass, at today's numbers - or a decoy's control's
    tolerance - before any envelope tolerance is fitted.

    What :func:`_stage_a_assignments` scores first and fits the run's tolerance
    on, and what a thin file's pool reads of each neighbour
    (:func:`_neighbour_envelope_lines`): the neighbour's own lines, never its
    pooled curve, so the pool cannot feed on itself. The parameters are
    :func:`_stage_a_assignments`'.
    """
    mass_accuracy = SampleMassAccuracy(reagent=reagent_offset)
    matched_df = match_isotope_df = pd.DataFrame()
    known_isotopes_df = await _fetch_known_isotopes(
        sample,
        match_params.isotope_abundance_threshold,
        mechanism_ids,
        mechanisms,
        known_window=known_window,
        resolution=resolution,
    )
    listed_formulas = listed_formula_identities(known_isotopes_df)
    if not known_isotopes_df.empty:
        match_isotope_df = await compute_match_isotopes(
            filename=sample.filename,
            target_isotopes_df=known_isotopes_df,
            polarity=sample.polarity,
        )
        if excluded_peak_ids and not match_isotope_df.empty:
            # Off the event loop, as the arbitration below: seconds on a large
            # frame, and a thin file's pool runs it once per neighbour too.
            match_isotope_df = await asyncio.to_thread(
                drop_ions_claimed_elsewhere, match_isotope_df, excluded_peak_ids
            )
        # Gate raw matches by the sample's match parameters' mass window and
        # intensity floor, as the targeted Match pipeline does: without it a
        # peak tens of ppm off a target would be tiered "assigned" here while
        # the Match tab reports no match. A line's height is held to the line
        # model's test at the run's tolerance, the one the search's lines are
        # admitted by, not to the parameters' fixed ratio tolerance
        # (:func:`gate_lines`): a line that test turns away is absent to the
        # fit and its peak goes to the search.
        if not match_isotope_df.empty:
            # A decoy reads its control's model, tolerance included, from the
            # start; any other run first today's numbers, since the fit reads
            # the commits this pass makes.
            tolerance = (
                None if inherited_model is None else inherited_model.envelope_tolerance
            )
            matched_df = match_isotope_df
            gated_df = gate_lines(match_isotope_df, match_params, tolerance)
            # Deliberately score Stage A with the fit score (score_pattern_v2):
            # the peak-centric engine's scoring engine is the ion-level fit
            # quality, not the targeted matcher's per-isotopologue term. Runs
            # after gating so tolerance/intensity cuts carry into the fit.
            # The fit counts the ions its lines come from as well as the lines:
            # a few bright ions with many lines each would otherwise calibrate
            # the sample on their own mass error (STAGE_A_FIT_MIN_IONS).
            match_isotope_df = score_ions_by_fit(
                gated_df,
                fallback_sigma_ppm=fallback_sigma_ppm,
                reagent_offset=reagent_offset,
                min_anchor_ions=STAGE_A_FIT_MIN_IONS,
                envelope_tolerance=tolerance,
                model=inherited_model,
            )
            # Read off the frame the fit was computed on: the run's v1 model,
            # at which Stage A was judged and the search ranks.
            if inherited_model is None:
                mass_accuracy = sample_mass_accuracy(
                    match_isotope_df,
                    reagent=reagent_offset,
                    min_anchor_ions=STAGE_A_FIT_MIN_IONS,
                )
        instrument = get_instrument_type(sample.filename)
        # Load this instrument's confidence calibration from the D6 store (active DB row,
        # else the in-code provisional curve, else None -> uncalibrated). Passing it in keeps
        # the engine DB-free; its corroboration_weights drive the P3 adduct fold-in.
        calibration = await load_calibration(
            instrument,
            SCORE_VERSION,
            ionization_setup=ionization_setup,
            reagent_profile=reagent_profile,
        )
        confidence_calibration = calibration_meta(
            calibration, ionization_setup, reagent_profile
        )

        def assignments_of(scored_df: pd.DataFrame) -> list[dict]:
            return invert_matches_to_peak_assignments(
                scored_df,
                sample_item_id=sample.sample_item_id,
                peak_assignment_run_id=peak_assignment_run_id,
                candidate_threshold=config.candidate_threshold,
                assigned_threshold=config.assigned_threshold,
                max_alternatives=config.max_alternatives,
                instrument=instrument,
                calibration=calibration,
            )

        return _StageAFirstPass(
            # Off the event loop: the arbitration takes seconds on a large
            # frame, and a thin file's pool runs it once per neighbour as well,
            # which held every other request of the server behind it.
            rows=await asyncio.to_thread(assignments_of, match_isotope_df),
            scored=match_isotope_df,
            matched=matched_df,
            mass_accuracy=mass_accuracy,
            confidence_calibration=confidence_calibration,
            listed_formulas=listed_formulas,
            assignments_of=assignments_of,
            curve=ConfidenceCurve.of(calibration, match_isotope_df),
        )
    return _StageAFirstPass(
        rows=[],
        scored=match_isotope_df,
        matched=matched_df,
        mass_accuracy=mass_accuracy,
        confidence_calibration=None,
        listed_formulas=listed_formulas,
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
        scored = score_ions_by_fit(
            gate_lines(first.matched, match_params, envelope.tolerance),
            model=MeasurementModel.v1(
                first.mass_accuracy, fallback_sigma_ppm, envelope.tolerance
            ),
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
    # Imported here: the stage list composes this module's helpers.
    from mascope_backend.api.new.peak_assignments.pipeline import (
        INGEST_FOLD,
        RunContext,
        claim,
        resolve,
    )

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
