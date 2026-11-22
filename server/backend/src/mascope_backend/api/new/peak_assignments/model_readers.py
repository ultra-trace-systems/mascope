"""The measurement model a run recorded, read back where no run is in progress.

The model a sample's latest run recorded (or the one a fold kept for a sample
with no run), the reagent lines' offset read again where none was recorded,
and the run's confidence calibration folded back into a row's provenance.
"""

import asyncio
from collections.abc import Callable
from datetime import datetime as dt
from datetime import timezone

import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from mascope_backend.api.new.peak_assignments.config import (
    IN_APP_ENGINE,
    PEAK_ASSIGNMENT_ENGINE_VERSION,
    PeakAssignmentConfig,
)
from mascope_backend.api.new.peak_assignments.profiles import (
    ResolvedProfile,
    resolve_profile,
)
from mascope_backend.api.new.peak_assignments.reagent_pass import (
    claim_reagent_peaks,
    reagent_library_for,
)
from mascope_backend.api.new.peak_assignments.row_terms import ENVELOPE_TOLERANCE_KEY
from mascope_backend.api.new.peak_assignments.sample_inputs import (
    _resolution_of,
    load_sample_peaks,
)
from mascope_backend.api.new.peak_assignments.sample_mechanisms import (
    fetch_sample_mechanisms,
)
from mascope_backend.db import PeakAssignmentRun, SampleMeasurementModel, async_session
from mascope_file.name import get_instrument_type
from mascope_tools.composition.custom_elements import label_purity
from mascope_tools.composition.envelope_tolerance import EnvelopeTolerance
from mascope_tools.composition.mass_accuracy import (
    ReagentOffset,
    SampleMassAccuracy,
    reagent_line_offset,
)
from mascope_tools.composition.measurement_model import MeasurementModel
from mascope_tools.composition.profiles import resolve_fallback_sigma_ppm
from mascope_tools.composition.reagents import REAGENT_LINE_FAMILIES


def _carries_own_calibration(provenance: dict) -> bool:
    """Whether a row states its own confidence calibration.

    True for a row written before the curve moved to the run, or seeded that
    way. The engine wrote ``calibrated`` and ``calibration`` together, so either
    key alone is the whole answer: such a row speaks for itself and the run's
    record does not apply to it.

    One predicate rather than one per reader, so the detail read and the ledger
    read cannot resolve the same row differently - a response saying
    ``calibrated: false`` beside a provisional flag read off the run would be
    contradicting itself.
    """
    return "calibration" in provenance or "calibrated" in provenance


def _row_calibration(provenance: dict, run_calibration: dict | None) -> dict | None:
    """The confidence calibration a row's ``p_correct`` was read off.

    The row's own block when it carries one, else the run's - but only for a row
    that carries a ``p_correct`` at all. The run's curve says nothing about a
    row it did not calibrate: Stage B rows, unassigned placeholders and imported
    rows (whose ``p_correct`` is stripped at ingest) have none.

    :param provenance: The row's stored provenance.
    :param run_calibration: The run's ``confidence_calibration``, or None.
    :return: The curve that applies to this row, or None.
    """
    if _carries_own_calibration(provenance):
        return provenance.get("calibration")
    return run_calibration if "p_correct" in provenance else None


def provenance_with_calibration(
    provenance: dict | None, run_calibration: dict | None
) -> dict | None:
    """Fold the run's confidence calibration back into a row's provenance.

    The engine records ``p_correct`` per row and the curve it came from once per
    run (``PeakAssignmentRun.confidence_calibration``); the detail row is served
    with ``calibrated`` and ``calibration`` beside ``p_correct`` all the same,
    because that is the shape the inspector and the SDK read. Only rows that
    carry a ``p_correct`` key ever had the pair - the database stage's - and a
    row that already states its own is left as it is, on the same test
    :func:`_provenance_scalars` resolves the ledger's flag by.

    :param provenance: The row's stored provenance.
    :param run_calibration: The run's ``confidence_calibration``, or None.
    :return: The provenance to serve; a new dict when the pair was folded in.
    """
    if not isinstance(provenance, dict) or "p_correct" not in provenance:
        return provenance
    if _carries_own_calibration(provenance):
        return provenance
    return {
        **provenance,
        "calibrated": run_calibration is not None,
        "calibration": run_calibration,
    }


def _claim_reagent_lines(
    peaks_df: pd.DataFrame,
    resolved_profile: ResolvedProfile,
    *,
    resolution: Callable[[float], float] | None,
):
    """The reagent pre-pass's claim, as every reader of it makes it.

    One call for the run's pre-pass (:func:`_reagent_assignments`) and the
    offset a path with no run reads (:func:`reagent_offset_for`): the claim is
    made at the profile's own m/z precision and the file's own resolving power,
    and a second copy of it is where either would drift apart.

    :param peaks_df: Every observed peak of the sample.
    :param resolved_profile: The chemistry the claim is made under.
    :param resolution: The file's resolving power as a function of m/z
        (:func:`_resolution_of`), at which an ion's isotope line is one peak of
        the file; None where it has none. Required, so no reader leaves it out.
    :return: What :func:`claim_reagent_peaks` claimed, and its calibration.
    """
    return claim_reagent_peaks(
        peaks_df,
        reagent_library_for(resolved_profile.profile.name),
        claim_ppm=resolved_profile.mz_precision_ppm,
        purity=_reagent_purity(resolved_profile),
        resolution=resolution,
    )


def _reagent_purity(resolved_profile: ResolvedProfile) -> float | None:
    """The purity a labelled chemistry's reagent is read at, None unlabelled.

    The sample's setup's, around a run. The profile's own figure is the
    library default a setup starts from; a site's bottle may differ.
    """
    if resolved_profile.profile.label_purity is None:
        return None
    return label_purity("^N")


def _claimed_lines_offset(
    hits, calibration, resolved_profile: ResolvedProfile
) -> ReagentOffset | None:
    """Where the reagent pre-pass's claimed lines put the mass axis.

    :param hits: What :func:`claim_reagent_peaks` claimed.
    :param calibration: What its anchors said; None when there was nothing to
        match.
    :param resolved_profile: The chemistry the pass claimed under, whose class
        width decides whether the offset is beyond it.
    :return: The lines' offset (:func:`reagent_line_offset`), or None for a
        profile with no reagent library.
    """
    # A profile with no reagent library has no pre-pass to ask, which the run
    # records differently from a pass that claimed nothing. The offset is read
    # off the reagent's own ladder and the calibrant beam; the air's ions sit
    # where the axis bends (``REAGENT_LINE_FAMILIES``).
    if calibration is None:
        return None
    return reagent_line_offset(
        (
            hit.mz_error_ppm
            for hit in hits
            if hit.cluster.family in REAGENT_LINE_FAMILIES
        ),
        resolved_profile.fallback_sigma_ppm,
    )


def reagent_offset_for(
    peaks_df: pd.DataFrame,
    resolved_profile: ResolvedProfile,
    *,
    resolution: Callable[[float], float] | None,
) -> ReagentOffset | None:
    """The reagent pre-pass's offset alone, for a path that writes no reagent rows.

    The same claim :func:`_reagent_assignments` makes, at the same precision
    and resolution, so a seeded re-score is centred where a run of the sample
    would be.

    :param peaks_df: Every observed peak of the sample.
    :param resolved_profile: The sample's resolved chemistry.
    :param resolution: The file's resolving power as a function of m/z, or None.
    :return: Where the claimed lines put the axis; None for a profile with no
        reagent library.
    """
    return _claimed_lines_offset(
        *_claim_reagent_lines(peaks_df, resolved_profile, resolution=resolution),
        resolved_profile,
    )


async def _latest_in_app_run(sample_item_id: str) -> tuple[str | None, dict | None]:
    """The sample's latest completed in-app run that is no decoy.

    The run the sample's own result is, as far as this engine is concerned:
    what a seeded re-score outside a run centres at, and what a decoy run is a
    decoy of.

    :param sample_item_id: The sample.
    :return: The run's id and stored config; ``(None, None)`` where the sample
        has none.
    """
    async with async_session() as session:
        row = (
            await session.execute(
                select(
                    PeakAssignmentRun.peak_assignment_run_id, PeakAssignmentRun.config
                )
                .where(
                    PeakAssignmentRun.sample_item_id == sample_item_id,
                    PeakAssignmentRun.status == "completed",
                    # An import's config is the client's own, stored as sent:
                    # it says nothing of where the in-app scorer centred.
                    PeakAssignmentRun.engine == IN_APP_ENGINE,
                    # A decoy scored at its control's centre, on an axis moved
                    # off it: neither is the sample's.
                    PeakAssignmentRun.decoy.is_(None),
                )
                .order_by(PeakAssignmentRun.peak_assignment_run_utc_created.desc())
                .limit(1)
            )
        ).first()
    if row is None:
        return None, None
    run_id, config = row
    return run_id, config if isinstance(config, dict) else {}


async def record_sample_model(sample_item_id: str, record: dict) -> None:
    """Keep the model a run-less pass fitted on a sample: the ingest fold's and
    the batch ledger rebuild's, which have no run to record it on (MAS-725).

    One row per sample, the last such pass's, replacing any before it; a
    reader outside a run takes it where the sample has no completed in-app run
    (:func:`recorded_measurement`).

    :param sample_item_id: The sample.
    :param record: What a run keeps of its model in its config: the model's
        record under ``measurement_model`` and the line-share tolerance's under
        ``envelope_tolerance``.
    """
    now = dt.now(timezone.utc)
    statement = pg_insert(SampleMeasurementModel).values(
        sample_item_id=sample_item_id,
        engine_version=PEAK_ASSIGNMENT_ENGINE_VERSION,
        config=record,
        sample_measurement_model_utc_recorded=now,
    )
    statement = statement.on_conflict_do_update(
        index_elements=[SampleMeasurementModel.sample_item_id],
        set_={
            "engine_version": statement.excluded.engine_version,
            "config": statement.excluded.config,
            "sample_measurement_model_utc_recorded": now,
        },
    )
    async with async_session() as session:
        await session.execute(statement)
        await session.commit()


async def _recorded_sample_model(sample_item_id: str) -> dict | None:
    """What :func:`record_sample_model` kept for a sample, or None."""
    async with async_session() as session:
        return (
            await session.execute(
                select(SampleMeasurementModel.config).where(
                    SampleMeasurementModel.sample_item_id == sample_item_id
                )
            )
        ).scalar_one_or_none()


#: :func:`recorded_measurement` was handed no reagent lines' centre, and
#: claims the lines where it needs them. Distinct from None, a pass's answer
#: for a profile with no reagent library.
CLAIM_REAGENT_LINES = object()


async def recorded_measurement(
    sample,
    config: PeakAssignmentConfig | None = None,
    *,
    recorded: dict | None = None,
    peaks_df: pd.DataFrame | None = None,
    resolved_profile: ResolvedProfile | None = None,
    reagent_offset: ReagentOffset | None | object = CLAIM_REAGENT_LINES,
) -> tuple[dict | None, MeasurementModel, bool]:
    """The measurement model a sample is read at outside a run, and the run
    config it was read off: the one way every reader outside a run reads it.

    The model the sample's latest completed in-app run recorded, read with
    the rest of that run's config in one query, so the width, the centre and
    the line-share tolerance a reader scores at come from one run even when
    another completes meanwhile (``MeasurementModel.from_run_config``; a run
    from before the one record is read at what it scored at). A pin, the
    inspector's evidence and alternatives, the batch search and its
    propagation, the seeded chain's every caller outside a run read it here.

    Where the sample has no completed in-app run, the model the last run-less
    pass fitted on it - the ingest fold, the batch ledger's rebuild - is read
    instead, which it kept per sample (:func:`record_sample_model`). Where
    nothing recorded one, the sample is read as a run whose target library
    fitted nothing reads it: at the instrument class's width, and at the
    reagent lines' centre where they put the axis beyond that width, claimed
    as the run-less ingest fold claims them - the configuration's profile (the
    default's where the caller has none), from the sample's own mechanisms,
    instrument and polarity, over the same peak read.

    :param sample: The sample.
    :param config: The configuration of the pass the caller is part of, whose
        ``profile`` or ``mz_precision_ppm`` may override the default's where
        the lines are claimed; None for a caller with none of its own.
    :param recorded: The run config to read the model off, where the caller
        has read one (a person's choice reads its own run's); None reads the
        sample's latest in-app run's.
    :param peaks_df: The sample's peaks, where the caller has read them.
    :param resolved_profile: The sample's resolved chemistry, where the caller
        has resolved it; ``config`` is then not read.
    :param reagent_offset: The reagent lines' centre, where the caller has
        claimed them (None for a profile with no reagent library); left out,
        they are claimed here where they are needed.
    :return: The run config read (None where the sample has no completed in-app
        run), the model, and whether a record held it (False where it stands
        in for one).
    """
    if recorded is None:
        run_id, recorded = await _latest_in_app_run(sample.sample_item_id)
        if run_id is None:
            recorded = await _recorded_sample_model(sample.sample_item_id)
    try:
        instrument_type = get_instrument_type(sample.filename)
    except ValueError:
        instrument_type = None
    fallback = resolve_fallback_sigma_ppm(instrument_type)
    model = MeasurementModel.from_run_config(recorded, fallback)
    if model is not None:
        return recorded, model, True
    if reagent_offset is CLAIM_REAGENT_LINES:
        if resolved_profile is None:
            _, mechanisms = await fetch_sample_mechanisms(sample)
            resolved_profile = resolve_profile(
                config if config is not None else PeakAssignmentConfig(),
                mechanism_notations=[m.ionization_mechanism for m in mechanisms],
                instrument_type=instrument_type,
                polarity=sample.polarity,
            )
        if peaks_df is None:
            peaks_df = await asyncio.to_thread(load_sample_peaks, sample)
        reagent_offset = await asyncio.to_thread(
            reagent_offset_for,
            peaks_df,
            resolved_profile,
            resolution=await _resolution_of(sample),
        )
    return (
        recorded,
        MeasurementModel.v1(
            SampleMassAccuracy(reagent=reagent_offset),
            fallback,
            EnvelopeTolerance.from_record((recorded or {}).get(ENVELOPE_TOLERANCE_KEY)),
        ),
        False,
    )


async def recorded_model(
    sample, config: PeakAssignmentConfig | None = None, **kwargs
) -> MeasurementModel:
    """:func:`recorded_measurement`'s model alone."""
    _, model, _ = await recorded_measurement(sample, config, **kwargs)
    return model
