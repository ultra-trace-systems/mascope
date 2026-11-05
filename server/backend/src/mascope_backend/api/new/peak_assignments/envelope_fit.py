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
and its committed envelopes are what the fit reads. The run then scores Stage
A's frame again, the search's ranking, and the seeded re-score its search rows
are tiered on, all at the one fitted tolerance, so no row of the run is read at
another. A fit that waited for the judging passes could read the search's
second-channel rows too, but each committed row would then need its fit, its
evidence, its tier and its arbitration derived a second time.

**What is read.** A committed Stage A envelope is a list's reading: the
workspace's target library or a reference list proposed the formula. Of those,
the ones whose monoisotopic line sits within the mass gate's cap
(``mass_gate.OFF_CALIBRATION_Z`` of the width Stage A scored at, about the
centre it scored at) are read, and of their lines only the ones the gate would
call tracking (``mass_gate.tracks_its_parent`` at the class's precision): a line
that does not track is another peak the window reached, no reading of this
ion's share. A predicted line the scorer saw absent - no peak, or one the match
parameters' ratio window turned away - counts toward its bin's presence. On an
Orbitrap only the 13C lines are read (the curve is the 13C lines' response,
decision 32); on a TOF every line, since its centre and width are of the
committed lines' relative error. A line is read as the score reads it: its
intensity over the anchor's, the most abundant predicted line's, against its
share of that line, at an expected S/N of its share times the anchor's S/N.

**The pooled level.** A run is one file. A file too thin for a curve of its own
takes the curve pooled with the files of its batch that share its acquisition
settings (:func:`acquisition_settings`: the polarity, the microscan count and the
resolution, so a 100-microscan file stays apart from its 10-microscan
siblings), read from the lines each sibling's latest completed run recorded. A
sibling with no run yet adds nothing, so the first file of a batch to be run
reads only its own lines; the record names how many files the pool held.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sqlalchemy import select

import mascope_file.io as m_io
import mascope_thermo.streams as m_streams
from mascope_backend.api.new.peak_assignments.config import IN_APP_ENGINE
from mascope_backend.api.new.peak_assignments.engine import (
    ENVELOPE_TOLERANCE_KEY,
    ROLE_M0,
    SOURCE_DATABASE,
    SampleMassAccuracy,
    labelled_isotopes,
    monoisotopic_row,
)
from mascope_backend.api.new.peak_assignments.mass_gate import (
    OFF_CALIBRATION_Z,
    tracking_tolerance_ppm,
    tracks_its_parent,
)
from mascope_backend.db import (
    AcquisitionStream,
    PeakAssignmentRun,
    Sample,
    SampleItem,
    async_session,
)
from mascope_backend.runtime import runtime
from mascope_file.name import get_instrument_type
from mascope_thermo.backend import trailer_float
from mascope_tools.composition.envelope_tolerance import (
    FORM_CENTRE_WIDTH,
    FORM_CURVE,
    FORM_DEFAULT,
    LEVEL_OWN,
    RESPONSE_SNR_EDGES,
    EnvelopeFit,
    EnvelopeLines,
    EnvelopeTolerance,
    fit_envelope_tolerance,
)
from mascope_tools.composition.mass_accuracy import scoring_sigma_ppm
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
    mass_accuracy: SampleMassAccuracy,
    fallback_sigma_ppm: float,
    form: str,
) -> EnvelopeLines:
    """The lines of a run's committed Stage A envelopes its tolerance is fitted on.

    :param match_isotope_df: Stage A's gated, fit-scored frame: every predicted
        isotopologue of every ion, a gated-out one with its intensity zeroed.
    :param stage_a_rows: The rows Stage A built from it; their monoisotopic
        rows name the envelopes it committed.
    :param mass_accuracy: What Stage A measured, whose centre and width the
        monoisotopic line must sit within the gate's cap of.
    :param fallback_sigma_ppm: The instrument class's width, which is also the
        precision a line must track its parent within.
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

    width = scoring_sigma_ppm(mass_accuracy.sigma_ppm, float(fallback_sigma_ppm))
    centre = float(mass_accuracy.scoring_mu_ppm or 0.0)
    tracking = tracking_tolerance_ppm(float(fallback_sigma_ppm))
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
        if mono_error is None or abs(mono_error - centre) > OFF_CALIBRATION_Z * width:
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
                ion.at[index, "target_isotope_formula"], labels
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
                if not tracks_its_parent(
                    ion.at[index, "match_mz_error"], reference, tracking
                ):
                    continue
                ratio = (observed / anchor_intensity) / share
            elif share * anchor_snr < RESPONSE_SNR_EDGES[0]:
                # An absent line below the first bin is in no bin's presence,
                # and the score never charges it: not worth recording for a
                # sibling to pool. Most predicted 13C lines of a file are such.
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
    in-app run: of the samples named, or of every sample in a batch."""
    query = select(
        PeakAssignmentRun.sample_item_id,
        PeakAssignmentRun.config[ENVELOPE_TOLERANCE_KEY],
    ).where(
        PeakAssignmentRun.status == "completed",
        # An import's config is the client's own: it says nothing of how the
        # in-app scorer read a line.
        PeakAssignmentRun.engine == IN_APP_ENGINE,
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


async def sibling_lines(sample, acquisition: dict) -> list[EnvelopeLines]:
    """The lines the files beside a sample recorded, for its pooled curve.

    Every other sample of its batch whose latest completed in-app run fitted
    the curve's form under the same acquisition settings, one entry per file,
    as each recorded its own lines: what a sibling pooled itself is not
    counted twice.

    :param sample: The sample whose curve is pooled.
    :param acquisition: Its settings (:func:`acquisition_settings`).
    :return: The siblings' lines; none where the sample is in no batch.
    """
    if not getattr(sample, "sample_batch_id", None):
        return []
    pooled = []
    for sample_item_id, record in await _latest_records(
        None, batch_id=sample.sample_batch_id
    ):
        if sample_item_id == sample.sample_item_id or not isinstance(record, dict):
            continue
        if record.get("form") != FORM_CURVE or record.get("acquisition") != acquisition:
            continue
        lines = EnvelopeLines.from_record(record.get("observations"))
        if lines.expected_snr.size:
            pooled.append(lines)
    return pooled


async def recorded_envelope_tolerance(sample_item_id: str) -> EnvelopeTolerance:
    """The tolerance a sample's latest completed in-app run scored at.

    What a formula measured outside any run - a pin, the inspector's evidence
    and alternatives, the batch search and its propagation - is read at, so
    one ion in one sample reads alike alone and in its run
    (``seeded_scoring.score_seeds``).

    :param sample_item_id: The sample.
    :return: That tolerance; today's where the sample has no such run, or its
        latest run fitted none.
    """
    rows = await _latest_records([sample_item_id])
    return EnvelopeTolerance.from_record(rows[0][1] if rows else None)


@dataclass(frozen=True)
class RunEnvelopeFit:
    """The tolerance a run scores at, with what it records of it.

    :param fit: The fit (``envelope_tolerance.EnvelopeFit``).
    :param acquisition: The settings a pooled curve was matched on, recorded
        so a sibling run can match this one's lines; None where the file
        records none, or the form pools nothing.
    """

    fit: EnvelopeFit
    acquisition: dict | None = None

    @property
    def tolerance(self) -> EnvelopeTolerance:
        """What every fit score of the run reads."""
        return self.fit.tolerance

    def record(self) -> dict:
        """``config.envelope_tolerance``."""
        record = self.fit.record()
        if self.fit.form == FORM_CURVE:
            record["acquisition"] = self.acquisition
        return record


async def fit_run_envelope_tolerance(
    sample,
    match_isotope_df: pd.DataFrame,
    stage_a_rows: Sequence[dict],
    *,
    mass_accuracy: SampleMassAccuracy,
    fallback_sigma_ppm: float,
) -> RunEnvelopeFit:
    """Fit the tolerance a run scores a line's share at, on Stage A's commits.

    The file's own lines first; where they are too few for a curve, those of
    the files beside it that share its acquisition settings as well
    (:func:`sibling_lines`); then today's numbers.

    :param sample: The sample the run assigns.
    :param match_isotope_df: Stage A's gated, fit-scored frame.
    :param stage_a_rows: The rows Stage A built from it.
    :param mass_accuracy: What Stage A measured.
    :param fallback_sigma_ppm: The instrument class's width and precision.
    :return: The fit and what the run records of it.
    """
    try:
        instrument_type = get_instrument_type(sample.filename)
    except ValueError:
        # A sample that keeps no data file and whose name does not say: the
        # run-less fold stands down on it rather than fail, and so does this.
        instrument_type = None
    form = form_for(instrument_type)
    lines = envelope_lines(
        match_isotope_df,
        stage_a_rows,
        mass_accuracy=mass_accuracy,
        fallback_sigma_ppm=fallback_sigma_ppm,
        form=form,
    )
    fit = fit_envelope_tolerance(form, lines)
    if form != FORM_CURVE:
        return RunEnvelopeFit(fit)
    acquisition = await acquisition_settings(sample)
    if fit.level != LEVEL_OWN and acquisition is not None:
        fit = fit_envelope_tolerance(
            form, lines, await sibling_lines(sample, acquisition)
        )
    return RunEnvelopeFit(fit, acquisition)
