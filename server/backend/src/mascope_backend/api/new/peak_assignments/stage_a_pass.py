"""Stage A: every peak matched against the known isotopologue library.

The database-first pass: the targeted matcher's lines inverted from
target-anchored to peak-anchored rows, scored at the run's model, with the
confidence curve its ``p_correct`` is read off and the neighbouring files'
envelope lines a thin file's tolerance is pooled from.
"""

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import SimpleNamespace

import numpy as np
import pandas as pd

from mascope_backend.api.new.cheminfo.utils import to_custom_element_format
from mascope_backend.api.new.peak_assignments.calibration_store import load_calibration
from mascope_backend.api.new.peak_assignments.config import PeakAssignmentConfig
from mascope_backend.api.new.peak_assignments.engine import (
    STAGE_A_FIT_MIN_IONS,
    ListedIon,
    calibration_meta,
    drop_ions_claimed_elsewhere,
    invert_matches_to_peak_assignments,
    listed_formula_identities,
    record_stage_a_same_ion_readings,
    reference_mirror_mask,
    reference_names_by_formula,
    sample_mass_accuracy,
    score_ions_by_fit,
)
from mascope_backend.api.new.peak_assignments.envelope_fit import RunEnvelopeFit
from mascope_backend.api.new.peak_assignments.profiles import ResolvedProfile
from mascope_backend.api.new.peak_assignments.row_terms import REFERENCE_IDENTITIES_COL
from mascope_backend.api.new.peak_assignments.sample_inputs import (
    RESOLVE_RESOLUTION,
    StageAKnown,
    _fetch_stage_a_known,
    match_reach,
)
from mascope_backend.api.new.peak_assignments.sample_mechanisms import (
    _untargeted_ionization_notations,
)
from mascope_backend.api.new.peak_assignments.seeded_scoring import gate_lines
from mascope_file.name import get_instrument_type
from mascope_match import compute_match_isotopes
from mascope_tools.composition.calibration import Calibration
from mascope_tools.composition.formula_shape import spare_listed
from mascope_tools.composition.heuristic_filter import SCORE_VERSION
from mascope_tools.composition.known_window import KnownWindow
from mascope_tools.composition.mass_accuracy import ReagentOffset, SampleMassAccuracy
from mascope_tools.composition.measurement_model import MeasurementModel
from mascope_tools.composition.utils import formula_identity


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
        cls,
        calibration: Calibration | None,
        frame: pd.DataFrame,
        absent: pd.DataFrame | None = None,
    ) -> "ConfidenceCurve | None":
        """The curve and the notations of the frame Stage A read through it;
        None where the instrument has no curve, so no row has a number. The
        reference ions Stage A left out of the frame (``absent``,
        ``sample_inputs.StageAKnown``) are of it too."""
        if calibration is None:
            return None
        notations: dict[str, str] = {}
        for rows in (frame, pd.DataFrame() if absent is None else absent):
            if not {"ionization_mechanism_id", "ionization_mechanism"}.issubset(
                rows.columns
            ):
                continue
            for mechanism_id, notation in zip(
                rows["ionization_mechanism_id"],
                rows["ionization_mechanism"],
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
    :param absent: The reference ions left out of the frame, none of whose
        lines reaches a peak (``sample_inputs.StageAKnown``): each scores 0 at
        any model, which a re-score reads (``judging_loop.fits_at``).
    """

    rows: list[dict]
    scored: pd.DataFrame
    matched: pd.DataFrame
    mass_accuracy: SampleMassAccuracy
    confidence_calibration: dict | None
    listed_formulas: frozenset[str]
    assignments_of: Callable[[pd.DataFrame], list[dict]] | None = None
    curve: ConfidenceCurve | None = None
    absent: pd.DataFrame = field(default_factory=pd.DataFrame)

    def rescored_at(
        self, match_params, envelope: RunEnvelopeFit, fallback_sigma_ppm: float
    ) -> pd.DataFrame:
        """The matched frame gated and scored again at the run's fitted
        envelope tolerance, at this pass's width and offset."""
        return score_ions_by_fit(
            gate_lines(self.matched, match_params, envelope.tolerance),
            model=MeasurementModel.v1(
                self.mass_accuracy, fallback_sigma_ppm, envelope.tolerance
            ),
        )


#: What ``pipeline._stage_a_assignments`` returns: the rows, the record of
#: their confidence curve, the sample's mass accuracy, the run's envelope
#: tolerance, the neutrals the lists name, the scored frame, the curve, and
#: the reference ions left out of the frame.
StageAResult = tuple[
    list[dict],
    dict | None,
    SampleMassAccuracy,
    RunEnvelopeFit,
    frozenset[str],
    pd.DataFrame,
    ConfidenceCurve | None,
    pd.DataFrame,
]


async def _read_known_set(
    sample,
    match_params,
    mechanisms: tuple[list[str], list[SimpleNamespace]],
    excluded_peak_ids: set[str] | None,
    *,
    known_window: KnownWindow | None,
    resolution: Callable[[float], float] | None | object,
) -> StageAKnown:
    """The known set :func:`_stage_a_first_pass` matches, with the lines of
    only the reference ions that may reach one of the sample's peaks
    (``sample_inputs._fetch_stage_a_known``).

    :param mechanisms: The mode's mechanism ids, which the target library is
        read through, and the mechanisms the reference lists are read through.
    :param excluded_peak_ids: The peaks another pass claimed, or None.
    """
    mechanism_ids, reference_mechanisms = mechanisms
    return await _fetch_stage_a_known(
        sample,
        match_params.isotope_abundance_threshold,
        mechanism_ids,
        reference_mechanisms,
        known_window=known_window,
        reach=await match_reach(sample, match_params.mz_tolerance, excluded_peak_ids),
        resolution=resolution,
    )


def _listed_formulas(known: StageAKnown) -> frozenset[str]:
    """The neutrals the known set names, matched or not: the absent ions'
    too (``engine.listed_formula_identities``)."""
    return listed_formula_identities(known.frame) | listed_formula_identities(
        known.absent
    )


def _reference_names(frame: pd.DataFrame, known: StageAKnown) -> dict | None:
    """The lists' names by formula a scored frame's rows take
    (``engine.reference_names_by_formula``), the absent ions' among them; None,
    to read them off the frame, where none is absent."""
    if known.absent.empty:
        return None
    return reference_names_with_absent(frame, known.absent, known.reference_order)


def reference_names_with_absent(
    frame: pd.DataFrame, absent: pd.DataFrame, order: dict[str, int]
) -> dict[str, list]:
    """The lists' names by formula of a scored frame and the reference ions
    Stage A left out of it, each formula's from the first of its ions in the
    known set's order, as one frame of all of them would name it.

    :param frame: The scored frame.
    :param absent: The reference ions left out (``StageAKnown.absent``).
    :param order: Every reference ion's position in the known set.
    :return: The names by formula identity.
    """
    if REFERENCE_IDENTITIES_COL not in frame.columns:
        return {}
    columns = ["target_ion_id", "target_compound_formula", REFERENCE_IDENTITIES_COL]
    mirror = frame[reference_mirror_mask(frame)].drop_duplicates("target_ion_id")
    rows = pd.concat([mirror[columns], absent[columns]], ignore_index=True)
    rank = rows["target_ion_id"].astype(str).map(order).to_numpy()
    return reference_names_by_formula(rows.iloc[np.argsort(rank, kind="stable")])


def reference_named(
    known: pd.DataFrame | Mapping[tuple, ListedIon] | None,
) -> frozenset[str]:
    """The formula identities of the neutrals a reference list names in ``known``."""
    if isinstance(known, Mapping):
        return frozenset(k[0] for k, ion in known.items() if ion.reference_identities)
    if known is None or "target_compound_formula" not in known.columns:
        return frozenset()
    formulas = known.loc[reference_mirror_mask(known), "target_compound_formula"]
    return frozenset(formula_identity(f) for f in formulas.dropna().unique())


def spare_listed_neutrals(
    *known: pd.DataFrame | Mapping[tuple, ListedIon] | None,
) -> None:
    """Leave the neutrals :func:`reference_named` finds in any of ``known`` out of
    the run's windows: Stage A's frame and the reference ions it left out of it
    (``StageAKnown.absent``), the batch search's known set, or a person's choice's
    listed ions."""
    spare_listed(frozenset().union(*map(reference_named, known)))


def _gate_and_fit(
    frame: pd.DataFrame,
    match_params,
    tolerance,
    *,
    fallback_sigma_ppm: float,
    reagent_offset: ReagentOffset | None,
    inherited_model: MeasurementModel | None,
    mass_accuracy: SampleMassAccuracy,
) -> tuple[pd.DataFrame, SampleMassAccuracy]:
    """The first pass's matched frame gated and fit-scored, and the mass
    accuracy read off it (:func:`_stage_a_first_pass`).

    The raw matches are gated by the sample's match parameters' mass window
    and intensity floor, as the targeted Match pipeline does: without it a
    peak tens of ppm off a target would be tiered "assigned" here while the
    Match tab reports no match. A line's height is held to the line model's
    test at the run's tolerance, the one the search's lines are admitted by,
    not to the parameters' fixed ratio tolerance (:func:`gate_lines`): a line
    that test turns away is absent to the fit and its peak goes to the search.

    Deliberately scored with the fit score (score_pattern_v2): the
    peak-centric engine's scoring engine is the ion-level fit quality, not the
    targeted matcher's per-isotopologue term. Run after gating so
    tolerance/intensity cuts carry into the fit. The fit counts the ions its
    lines come from as well as the lines: a few bright ions with many lines
    each would otherwise calibrate the sample on their own mass error
    (STAGE_A_FIT_MIN_IONS). The accuracy is read off the frame the fit was
    computed on: the run's v1 model, at which Stage A was judged and the
    search ranks. A decoy, scored at its control's model, keeps the
    ``mass_accuracy`` it was handed.
    """
    scored_df = score_ions_by_fit(
        gate_lines(frame, match_params, tolerance),
        fallback_sigma_ppm=fallback_sigma_ppm,
        reagent_offset=reagent_offset,
        min_anchor_ions=STAGE_A_FIT_MIN_IONS,
        envelope_tolerance=tolerance,
        model=inherited_model,
    )
    if inherited_model is not None:
        return scored_df, mass_accuracy
    return scored_df, sample_mass_accuracy(
        scored_df,
        reagent=reagent_offset,
        min_anchor_ions=STAGE_A_FIT_MIN_IONS,
    )


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
    known = await _read_known_set(
        sample,
        match_params,
        (mechanism_ids, mechanisms),
        excluded_peak_ids,
        known_window=known_window,
        resolution=resolution,
    )
    known_isotopes_df, listed_formulas = known.frame, _listed_formulas(known)
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
        # Gated by the match parameters' mass window and floor (:func:`_gate_and_fit`).
        if not match_isotope_df.empty:
            # A decoy reads its control's model, tolerance included, from the
            # start; any other run first today's numbers, since the fit reads
            # the commits this pass makes.
            tolerance = (
                None if inherited_model is None else inherited_model.envelope_tolerance
            )
            matched_df = match_isotope_df

            # Off the event loop, as the drop above: a fit over the whole
            # gated frame.
            match_isotope_df, mass_accuracy = await asyncio.to_thread(
                _gate_and_fit,
                match_isotope_df,
                match_params,
                tolerance,
                fallback_sigma_ppm=fallback_sigma_ppm,
                reagent_offset=reagent_offset,
                inherited_model=inherited_model,
                mass_accuracy=mass_accuracy,
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
                reference_identities=_reference_names(scored_df, known),
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
            curve=await asyncio.to_thread(
                ConfidenceCurve.of, calibration, match_isotope_df, known.absent
            ),
            absent=known.absent,
        )
    return _StageAFirstPass(
        rows=[],
        scored=match_isotope_df,
        matched=matched_df,
        mass_accuracy=mass_accuracy,
        confidence_calibration=None,
        listed_formulas=listed_formulas,
    )
