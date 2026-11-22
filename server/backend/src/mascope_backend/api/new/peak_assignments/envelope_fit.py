"""The envelope tolerance a run fits on its own committed envelopes (step 3.5b).

The fit score reads a matched isotope line's share at 5% of itself and charges
an absent line from an expected signal-to-noise of 3, and on an Orbitrap a dim
line is read low or not at all for reasons that have nothing to do with the
formula: the file's peak list averages scans that each kept the line only where
it cleared that scan's floor. How much, and from which S/N, is the file's own
(``mascope_tools.composition.envelope_tolerance`` has the measurement and the
form, decided by decision 32). This module reads it off the run.

**Where.** After Stage A and before the search is scored, as Stage A's width
and offset are: Stage A scores its frame at today's numbers, builds its rows,
and its committed envelopes are what the fit reads. The run then gates and
scores Stage A's frame again, at the first pass's width and offset, and the
search's ranking and the seeded re-score its search rows are tiered on, all at
the one fitted tolerance, so no row of the run is read at another. A fit that
waited for the judging passes could read the search's second-channel rows too,
but each committed row would then need its fit, its evidence, its tier and its
arbitration derived a second time.

**What is read.** A committed Stage A envelope is a list's reading: the
workspace's target library or a reference list proposed the formula. Of those,
the ones whose monoisotopic line sits within the mass gate's cap
(``mass_gate.OFF_CALIBRATION_Z`` of the width Stage A scored at, about the
centre it scored at) are read, and of their lines only the ones the gate would
call tracking (``line_model.tracks`` at the class's precision): a line
that does not track is another peak the window reached, no reading of this
ion's share. A predicted line the scorer saw absent - no peak, or one the gate
turned away, the line model's height test at today's tolerance
(``seeded_scoring.gate_lines``) - counts toward its bin's presence. On an
Orbitrap only the 13C lines are read (the curve is the 13C lines' response,
decision 32), a line read as its brightest member where the run merged lines
at the file's R(m/z); on a TOF every line, since its centre and width are of the
committed lines' relative error. A line is read as the score reads it: its
intensity over the anchor's, the most abundant predicted line's, against its
share of that line, at an expected S/N of its share times the anchor's S/N.

**The pooled level.** A run is one file. A file too thin for a curve of its own
takes the curve pooled with its :data:`POOL_NEIGHBOURS` nearest files in
acquisition time (:func:`read_pool`): files of its batch that share its
acquisition settings (:func:`acquisition_settings`: the polarity, the microscan
count and the resolution, so a 100-microscan file stays apart from its
10-microscan siblings), have a peak list and that the engine would run. Each is
read fresh, by its own Stage A first pass at today's numbers under the reading
run's config and match parameters, never from a run of its own: the pool is
then a function of this run's inputs and the neighbours' data, and two runs of
a batch pool alike whatever order they ran in, at once or one after the other
(decision 32, as the plan owner amended it on MAS-842). Files near it in time
share its instrument state better than a batch spanning days does, and a file
that joins a growing batch later in time moves the pool only of the files near
the batch's end. The record names the files pooled, the lines each gave and the
time span the pool covers.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd
from sqlalchemy import select

import mascope_file.io as m_io
import mascope_thermo.streams as m_streams
from mascope_backend.api.controllers.target.lib.compute.target_ions_compute import (
    LEAD_ISOTOPE_FORMULA,
)
from mascope_backend.api.new.peak_assignments.config import IN_APP_ENGINE
from mascope_backend.api.new.peak_assignments.engine import (
    ENVELOPE_TOLERANCE_KEY,
    ROLE_M0,
    SOURCE_DATABASE,
    labelled_isotopes,
    monoisotopic_row,
)
from mascope_backend.api.new.peak_assignments.mass_gate import (
    OFF_CALIBRATION_Z,
)
from mascope_backend.db import (
    AcquisitionStream,
    PeakAssignmentRun,
    Sample,
    SampleItem,
    SampleMeasurementModel,
    async_session,
)
from mascope_backend.runtime import runtime
from mascope_file.name import get_instrument_type
from mascope_thermo.backend import trailer_float
from mascope_tools.composition.envelope_tolerance import (
    DEFAULT_TOLERANCE,
    FORM_CENTRE_WIDTH,
    FORM_CURVE,
    FORM_DEFAULT,
    LEVEL_DEFAULT,
    LEVEL_OWN,
    RESPONSE_SNR_EDGES,
    EnvelopeFit,
    EnvelopeLines,
    EnvelopeTolerance,
    fit_envelope_tolerance,
)
from mascope_tools.composition.line_model import tracks
from mascope_tools.composition.measurement_model import MeasurementModel
from mascope_tools.composition.utils import parse_formula_tokens


#: The form each instrument class's tolerance takes (decision 32): a response
#: curve on an Orbitrap, a centre and a width on a TOF until its S/N column is
#: re-read, today's numbers on anything else.
FORM_BY_INSTRUMENT = {"orbi": FORM_CURVE, "tof": FORM_CENTRE_WIDTH}

#: The isotope a curve's lines substitute, as an isotopologue formula names it.
CARBON_13 = "[13C]"


def form_for(instrument_type: str | None) -> str:
    """The form a run's tolerance takes on its instrument class."""
    return FORM_BY_INSTRUMENT.get(instrument_type or "", FORM_DEFAULT)


def is_carbon13_line(formula, labels: dict[str, int] | None = None) -> bool:
    """Whether an isotopologue formula names a line that substitutes 13C and
    nothing else.

    A labelled reagent's atom is in brackets on every line of its ion
    (``engine.is_monoisotopic_formula``), so it is taken off before the line is
    read. A line at a low resolution names several isotopologues joined by
    "/"; it is a 13C line when every one of them is.

    :param formula: An isotopologue formula (``C5[13C]H13O6+``).
    :param labels: The ion's labelled isotopes (``engine.labelled_isotopes``).
    """
    if not isinstance(formula, str) or not formula:
        return False
    for name in formula.split("/"):
        substituted = {
            symbol: count - (labels or {}).get(symbol, 0)
            for symbol, count in parse_formula_tokens(name).items()
            if symbol.startswith("[")
        }
        substituted = {symbol: n for symbol, n in substituted.items() if n}
        if set(substituted) != {CARBON_13}:
            return False
    return True


def _line_name(ion: pd.DataFrame, index) -> str | None:
    """The isotopologue a line of the frame is read as: its leading member
    where the run resolved its lines at the file's R(m/z)
    (``target_ions_compute.LEAD_ISOTOPE_FORMULA``), else its own formula."""
    if LEAD_ISOTOPE_FORMULA in ion.columns:
        lead = ion.at[index, LEAD_ISOTOPE_FORMULA]
        if isinstance(lead, str) and lead:
            return lead
    return ion.at[index, "target_isotope_formula"]


def _finite(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) else None


def envelope_lines(
    match_isotope_df: pd.DataFrame,
    stage_a_rows: Sequence[dict],
    *,
    model: MeasurementModel,
    form: str,
) -> EnvelopeLines:
    """The lines of a run's committed Stage A envelopes its tolerance is fitted on.

    :param match_isotope_df: Stage A's gated, fit-scored frame: every predicted
        isotopologue of every ion, a gated-out one with its intensity zeroed.
    :param stage_a_rows: The rows Stage A built from it; their monoisotopic
        rows name the envelopes it committed.
    :param model: The run's measurement model as Stage A fitted it (v1, before
        the tolerance it is reading lines for): its centre and width are what
        the monoisotopic line must sit within the gate's cap of, and the
        instrument class's width it carries is the precision a line must track
        its parent within.
    :param form: The form the tolerance takes (:func:`form_for`), which says
        which lines are read.
    :return: The lines, with each one's expected S/N and its observed /
        predicted share (NaN where the scorer saw it absent).
    """
    required = {
        "target_ion_id",
        "relative_abundance",
        "sample_peak_intensity",
        "match_mz_error",
        "sample_peak_id",
        "target_ion_formula",
        "ionization_mechanism_id",
        "signal_to_noise",
    }
    if (
        form == FORM_DEFAULT
        or match_isotope_df.empty
        or not required.issubset(match_isotope_df.columns)
        or (form == FORM_CURVE and "target_isotope_formula" not in match_isotope_df)
    ):
        return EnvelopeLines()
    committed = {
        (
            str(row.get("sample_peak_id")),
            row.get("ion_formula"),
            row.get("ionization_mechanism_id"),
        )
        for row in stage_a_rows
        if row.get("role") == ROLE_M0
        and row.get("source") == SOURCE_DATABASE
        and row.get("assigned_formula")
    }
    if not committed:
        return EnvelopeLines()

    # The committed ions' rows alone: with a reference list loaded the frame
    # holds every ion the lists predict, most of them committed nowhere.
    keys = pd.Series(
        list(
            zip(
                match_isotope_df["sample_peak_id"].astype(str),
                match_isotope_df["target_ion_formula"],
                match_isotope_df["ionization_mechanism_id"],
            )
        ),
        index=match_isotope_df.index,
        dtype=object,
    )
    ions = match_isotope_df.loc[keys.isin(committed), "target_ion_id"].unique()
    frame = match_isotope_df[match_isotope_df["target_ion_id"].isin(ions)]

    width = model.width_ppm
    precision = model.fallback_sigma_ppm
    snr_column, ratio_column = [], []
    read: set[tuple] = set()
    for _, ion in frame.groupby("target_ion_id", sort=False):
        # A frame through a gate and a scorer can carry a duplicated index.
        ion = ion.reset_index(drop=True)
        mono = monoisotopic_row(ion)
        key = (
            str(mono["sample_peak_id"]),
            mono["target_ion_formula"],
            mono["ionization_mechanism_id"],
        )
        # One envelope once, however many lists named its ion.
        if key not in committed or key in read:
            continue
        read.add(key)
        mono_error = _finite(mono["match_mz_error"])
        if (
            mono_error is None
            or abs(mono_error - model.centre_ppm(_finite(mono.get("mz"))))
            > OFF_CALIBRATION_Z * width
        ):
            continue
        abundance = pd.to_numeric(ion["relative_abundance"], errors="coerce")
        intensity = pd.to_numeric(ion["sample_peak_intensity"], errors="coerce")
        if "is_satellite" in ion.columns:
            intensity = intensity.where(
                ~ion["is_satellite"].fillna(False).astype(bool), 0.0
            )
        anchor = abundance.idxmax() if abundance.notna().any() else None
        if (
            anchor is None
            or not np.isfinite(abundance[anchor])
            or abundance[anchor] <= 0
        ):
            continue
        anchor_intensity = _finite(intensity[anchor])
        anchor_snr = _finite(ion.at[anchor, "signal_to_noise"])
        anchor_error = _finite(ion.at[anchor, "match_mz_error"])
        if (
            not anchor_intensity
            or anchor_intensity <= 0
            or not anchor_snr
            or anchor_snr <= 0
        ):
            continue
        labels = labelled_isotopes(mono["target_ion_formula"])
        for index in ion.index:
            if index == anchor:
                continue
            if form == FORM_CURVE and not is_carbon13_line(
                _line_name(ion, index), labels
            ):
                continue
            share = float(abundance[index]) / float(abundance[anchor])
            if not np.isfinite(share) or share <= 0:
                continue
            observed = _finite(intensity[index]) or 0.0
            if observed > 0:
                # The line is read against the monoisotopic line, as the gate
                # reads a child against its owner, and that line against the
                # anchor where it is not the anchor itself.
                reference = anchor_error if index == mono.name else mono_error
                if not tracks(
                    ion.at[index, "match_mz_error"],
                    reference,
                    float(precision),
                ):
                    continue
                ratio = (observed / anchor_intensity) / share
            elif share * anchor_snr < RESPONSE_SNR_EDGES[0]:
                # An absent line below the first bin is in no bin's presence,
                # and the score never charges it: not worth recording, nor
                # reading for a pool. Most predicted 13C lines of a file are such.
                continue
            else:
                ratio = np.nan
            snr_column.append(share * anchor_snr)
            ratio_column.append(ratio)
    return EnvelopeLines(np.array(snr_column), np.array(ratio_column))


async def acquisition_settings(sample) -> dict | None:
    """What a sample's file was acquired at, as far as its pooled curve cares.

    The polarity, the microscan count and the resolution: how far a dim line
    is censored depends on how the file was acquired, so a curve pools only
    files acquired alike. Read off the sample's stream where it points at one,
    else off the file's own acquisition parameters (its ``.props``).

    :param sample: The sample view row.
    :return: The settings, or None where the file records neither count.
    """
    async with async_session() as session:
        params = (
            await session.execute(
                select(AcquisitionStream.acquisition_params)
                .join(SampleItem, SampleItem.stream_id == AcquisitionStream.stream_id)
                .where(SampleItem.sample_item_id == sample.sample_item_id)
            )
        ).scalar_one_or_none()
    if not params:
        try:
            props = await asyncio.to_thread(m_io.read_props, sample.filename)
        except (OSError, ValueError) as error:
            runtime.logger.debug(
                f"No acquisition parameters for sample '{sample.sample_item_name}': "
                f"{error}"
            )
            return None
        params = props.get("acquisition_params")
    stream = {"acquisition_params": params if isinstance(params, dict) else None}
    microscans = m_streams.microscans(stream)
    resolution = trailer_float(
        ((stream["acquisition_params"] or {}).get("constant") or {}).get(
            m_streams.FT_RESOLUTION
        )
    )
    if microscans is None and resolution is None:
        return None
    return {
        "polarity": sample.polarity,
        "microscans": microscans,
        "resolution": resolution,
    }


async def _latest_records(sample_item_ids: Sequence[str] | None, batch_id=None):
    """The ``envelope_tolerance`` record of each sample's latest completed
    in-app run that is no decoy: of the samples named, or of every sample in a
    batch."""
    query = select(
        PeakAssignmentRun.sample_item_id,
        PeakAssignmentRun.config[ENVELOPE_TOLERANCE_KEY],
    ).where(
        PeakAssignmentRun.status == "completed",
        # An import's config is the client's own: it says nothing of how the
        # in-app scorer read a line.
        PeakAssignmentRun.engine == IN_APP_ENGINE,
        # A decoy scored at its control's tolerance, and the lines it would
        # record are wrong formulas on a moved axis: neither is the sample's.
        PeakAssignmentRun.decoy.is_(None),
    )
    if batch_id is not None:
        query = query.join(
            Sample, Sample.sample_item_id == PeakAssignmentRun.sample_item_id
        ).where(Sample.sample_batch_id == batch_id)
    if sample_item_ids is not None:
        query = query.where(PeakAssignmentRun.sample_item_id.in_(list(sample_item_ids)))
    query = query.order_by(
        PeakAssignmentRun.sample_item_id,
        PeakAssignmentRun.peak_assignment_run_utc_created.desc(),
    ).distinct(PeakAssignmentRun.sample_item_id)
    async with async_session() as session:
        return (await session.execute(query)).all()


#: The files a thin file's pooled curve reads beside its own, at the most: the
#: nearest in acquisition time that share its settings (the plan owner's K on
#: MAS-842). A pool of seven files is the size the curve's minimum was set on
#: (K3's 34 lines over 7 files, I positive's 30 over 6, C2's 52 over 5). Fixed:
#: the pool does not widen until the curve's minimum is met.
POOL_NEIGHBOURS = 6

#: Reads one neighbour's own lines for a pool: its Stage A first pass at today's
#: numbers, under the reading run's config and match parameters. None where the
#: file is not one a pool reads - the engine would not run it, or it has no
#: peak list - so the next nearest file takes its place.
NeighbourReader = Callable[[Sample], Awaitable[EnvelopeLines | None]]


@dataclass(frozen=True)
class PooledFile:
    """One neighbour a pool read.

    :param sample_item_id: The sample.
    :param acquired: When its file was acquired (``datetime_utc``); None where
        the file does not say.
    :param lines: Its own lines, as its Stage A first pass read them.
    """

    sample_item_id: str
    acquired: datetime | None
    lines: EnvelopeLines

    def record(self) -> dict:
        """What the run records of it."""
        return {
            "sample_item_id": self.sample_item_id,
            "lines": self.lines.lines,
            "absent": self.lines.absent,
        }


@dataclass(frozen=True)
class EnvelopePool:
    """The neighbours a thin file's curve was pooled over (:func:`read_pool`).

    :param files: The neighbours read, nearest first; one that gave no line
        still took its place.
    :param span: The earliest and the latest acquisition time among them and
        the file itself; None where no file of the pool says when it was
        acquired.
    :param nearest: The files the pool reads at the most (its K).
    """

    files: tuple[PooledFile, ...] = ()
    span: tuple[datetime, datetime] | None = None
    nearest: int = POOL_NEIGHBOURS

    @property
    def lines(self) -> list[EnvelopeLines]:
        """The neighbours' lines, one entry per file, for the fit to pool."""
        return [pooled.lines for pooled in self.files]

    def record(self) -> dict:
        """``config.envelope_tolerance.pool``: the files pooled, the lines each
        gave and the time span the pool covers, so a ledger comparison sees
        what moved."""
        span = None
        if self.span is not None:
            first, last = self.span
            span = {
                "from": first.isoformat(),
                "to": last.isoformat(),
                "hours": round((last - first).total_seconds() / 3600.0, 2),
            }
        return {
            "nearest": self.nearest,
            "files": [pooled.record() for pooled in self.files],
            "span": span,
        }


def _by_distance_in_time(sample, candidates: Sequence) -> list:
    """The candidates nearest the sample in acquisition time first, ties - and a
    file that does not say when it was acquired, after every one that does - by
    sample id, so the order is the data's alone."""
    acquired = getattr(sample, "datetime_utc", None)

    def key(candidate):
        other = getattr(candidate, "datetime_utc", None)
        distance = (
            abs((other - acquired).total_seconds())
            if acquired is not None and other is not None
            else float("inf")
        )
        return (distance, str(candidate.sample_item_id))

    return sorted(candidates, key=key)


async def _batch_candidates(sample) -> list:
    """Every other sample of the sample's batch, nearest it in acquisition time
    first (:func:`_by_distance_in_time`)."""
    async with async_session() as session:
        candidates = (
            (
                await session.execute(
                    select(Sample).where(
                        Sample.sample_batch_id == sample.sample_batch_id,
                        Sample.sample_item_id != sample.sample_item_id,
                    )
                )
            )
            .scalars()
            .all()
        )
    return _by_distance_in_time(sample, candidates)


def _is_curve_file(candidate) -> bool:
    try:
        return form_for(get_instrument_type(candidate.filename)) == FORM_CURVE
    except ValueError:
        return False


async def read_pool(
    sample,
    acquisition: dict,
    read_neighbour: NeighbourReader,
    *,
    nearest: int = POOL_NEIGHBOURS,
) -> EnvelopePool:
    """The neighbours a thin file's curve is pooled over, each read fresh.

    The ``nearest`` files of the sample's batch nearest it in acquisition
    time (``datetime_utc``, ties by sample id) that share its acquisition
    settings, have a peak list and that the engine would run, each read by
    ``read_neighbour``: its own Stage A first pass, never a run of its own nor
    the curve it would pool itself, so the pool cannot feed on itself and does
    not depend on which of its files ran before it. A blank, which an Orbitrap
    sequence interleaves, takes no place: the reader refuses it and the next
    nearest file is read.

    :param sample: The sample whose curve is pooled.
    :param acquisition: Its settings (:func:`acquisition_settings`).
    :param read_neighbour: Reads a neighbour's own lines, or None where the
        file is not one a pool reads.
    :param nearest: The files to read at the most.
    :return: The pool; empty where the sample is in no batch.
    """
    if not getattr(sample, "sample_batch_id", None):
        return EnvelopePool(nearest=nearest)
    pooled: list[PooledFile] = []
    for candidate in await _batch_candidates(sample):
        if len(pooled) >= nearest:
            break
        if not _is_curve_file(candidate):
            continue
        if await acquisition_settings(candidate) != acquisition:
            continue
        lines = await read_neighbour(candidate)
        if lines is None:
            continue
        pooled.append(
            PooledFile(
                sample_item_id=str(candidate.sample_item_id),
                acquired=getattr(candidate, "datetime_utc", None),
                lines=lines,
            )
        )
    times = [
        moment
        for moment in [
            getattr(sample, "datetime_utc", None),
            *(pooled_file.acquired for pooled_file in pooled),
        ]
        if moment is not None
    ]
    pool = EnvelopePool(
        files=tuple(pooled),
        span=(min(times), max(times)) if times else None,
        nearest=nearest,
    )
    runtime.logger.info(
        f"Envelope curve of sample '{sample.sample_item_name}' pooled over "
        f"{len(pooled)} of its nearest files at its settings "
        f"({sum(pooled_file.lines.lines for pooled_file in pooled)} lines)"
        + (
            f", spanning {pool.record()['span']['hours']} h"
            if pool.span is not None
            else ""
        )
    )
    return pool


async def recorded_envelope_tolerances(
    sample_batch_id: str,
) -> dict[str, EnvelopeTolerance]:
    """The tolerance each sample of a batch scored at, as its latest
    completed in-app run recorded it, or for a sample with none, as the
    run-less fold that assigned it kept it (``model_readers.record_sample_model``):
    what the batch's isotopologue stamping (``batch_isotopologues``) judges a
    line's share at in each sample. One sample's reader outside a run reads
    the same tolerance with the rest of its model
    (``model_readers.recorded_measurement``). A sample neither recorded is not
    named; its reader takes today's.

    :param sample_batch_id: The batch.
    :return: Sample -> its tolerance.
    """
    tolerances = {
        sample_item_id: EnvelopeTolerance.from_record(record)
        for sample_item_id, record in await _latest_records(
            None, batch_id=sample_batch_id
        )
    }
    async with async_session() as session:
        folded = (
            await session.execute(
                select(
                    SampleMeasurementModel.sample_item_id,
                    SampleMeasurementModel.config,
                )
                .join(
                    Sample,
                    Sample.sample_item_id == SampleMeasurementModel.sample_item_id,
                )
                .where(Sample.sample_batch_id == sample_batch_id)
            )
        ).all()
    for sample_item_id, config in folded:
        if sample_item_id not in tolerances and isinstance(config, dict):
            tolerances[sample_item_id] = EnvelopeTolerance.from_record(
                config.get(ENVELOPE_TOLERANCE_KEY)
            )
    return tolerances


@dataclass(frozen=True)
class RunEnvelopeFit:
    """The tolerance a run scores at, with what it records of it.

    :param fit: The fit (``envelope_tolerance.EnvelopeFit``).
    :param acquisition: The settings a pooled curve was matched on; None where
        the file records none, or the form pools nothing.
    :param inherited: On a decoy run, the record of its control's tolerance,
        which the decoy scores at instead of fitting its own
        (:func:`inherited_envelope_fit`); None on every other run.
    :param pool: The neighbours a thin file read for its pooled curve
        (:func:`read_pool`), whether or not they held enough lines; None where
        no pool was read.
    """

    fit: EnvelopeFit
    acquisition: dict | None = None
    inherited: dict | None = None
    pool: EnvelopePool | None = None

    @property
    def tolerance(self) -> EnvelopeTolerance:
        """What every fit score of the run reads."""
        return self.fit.tolerance

    def record(self) -> dict:
        """``config.envelope_tolerance``."""
        if self.inherited is not None:
            # The control's own record, marked only where it holds, as the
            # mass gate marks an inherited calibration.
            return {**self.inherited, "inherited": True}
        record = self.fit.record()
        if self.fit.form == FORM_CURVE:
            record["acquisition"] = self.acquisition
            if self.pool is not None:
                record["pool"] = self.pool.record()
        return record


async def fit_run_envelope_tolerance(
    sample,
    match_isotope_df: pd.DataFrame,
    stage_a_rows: Sequence[dict],
    *,
    model: MeasurementModel,
    read_neighbour: NeighbourReader,
) -> RunEnvelopeFit:
    """Fit the tolerance a run scores a line's share at, on Stage A's commits.

    The file's own lines first; where they are too few for a curve, those of
    its nearest files that share its acquisition settings as well, each read
    fresh (:func:`read_pool`); then today's numbers.

    :param sample: The sample the run assigns.
    :param match_isotope_df: Stage A's gated, fit-scored frame.
    :param stage_a_rows: The rows Stage A built from it.
    :param model: The run's v1 model, before its tolerance
        (:func:`envelope_lines`).
    :param read_neighbour: Reads a neighbour's own lines for the pool, under
        this run's config and match parameters.
    :return: The fit and what the run records of it.
    """
    try:
        instrument_type = get_instrument_type(sample.filename)
    except ValueError:
        # A sample that keeps no data file and whose name does not say: the
        # run-less fold stands down on it rather than fail, and so does this.
        instrument_type = None
    form = form_for(instrument_type)
    lines = envelope_lines(match_isotope_df, stage_a_rows, model=model, form=form)
    fit = fit_envelope_tolerance(form, lines)
    if form != FORM_CURVE:
        return RunEnvelopeFit(fit)
    acquisition = await acquisition_settings(sample)
    if fit.level == LEVEL_OWN or acquisition is None:
        return RunEnvelopeFit(fit, acquisition)
    pool = await read_pool(sample, acquisition, read_neighbour)
    return RunEnvelopeFit(
        fit_envelope_tolerance(form, lines, pool.lines), acquisition, pool=pool
    )


def inherited_envelope_fit(record: dict | None) -> RunEnvelopeFit:
    """The tolerance a decoy run scores a line's share at: its control's.

    A decoy's Stage A commits are wrong formulas on a moved axis, and few: on
    the demo's uronium sample the +9 ppm arm kept 8 of its control's 702
    assigned rows, short of the lines a fit of its own needs. Fitted on them,
    the arm would fall back to the pooled curve or to today's numbers, and
    measure a different tolerance as well as the arm. So it scores at the
    control's, as it does at the control's mass accuracy
    (:mod:`~mascope_backend.api.new.peak_assignments.decoy`).

    :param record: The control's ``config.envelope_tolerance``; None for a
        control from before the fit, which scored at today's numbers.
    :return: The fit the decoy scores at, which records the control's record.
    """
    if not isinstance(record, dict):
        record = EnvelopeFit(
            DEFAULT_TOLERANCE, FORM_DEFAULT, LEVEL_DEFAULT, EnvelopeLines()
        ).record()
    fit = EnvelopeFit(
        EnvelopeTolerance.from_record(record),
        form=str(record.get("form") or FORM_DEFAULT),
        level=str(record.get("level") or LEVEL_DEFAULT),
        lines=EnvelopeLines(),
    )
    return RunEnvelopeFit(fit, inherited=record)
