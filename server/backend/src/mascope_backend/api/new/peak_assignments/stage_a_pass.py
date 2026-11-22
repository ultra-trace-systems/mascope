"""Stage A: every peak matched against the known isotopologue library.

The database-first pass: the targeted matcher's lines inverted from
target-anchored to peak-anchored rows, scored at the run's model, with the
confidence curve its ``p_correct`` is read off and the neighbouring files'
envelope lines a thin file's tolerance is pooled from.
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from types import SimpleNamespace

import pandas as pd

from mascope_backend.api.new.cheminfo.utils import to_custom_element_format
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
from mascope_backend.api.new.peak_assignments.envelope_fit import RunEnvelopeFit
from mascope_backend.api.new.peak_assignments.profiles import ResolvedProfile
from mascope_backend.api.new.peak_assignments.sample_inputs import (
    RESOLVE_RESOLUTION,
    _fetch_known_isotopes,
)
from mascope_backend.api.new.peak_assignments.sample_mechanisms import (
    _untargeted_ionization_notations,
)
from mascope_backend.api.new.peak_assignments.seeded_scoring import gate_lines
from mascope_file.name import get_instrument_type
from mascope_match import compute_match_isotopes
from mascope_tools.composition.calibration import Calibration
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
