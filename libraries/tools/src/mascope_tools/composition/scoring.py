"""What a sample is scored at, and the candidates scored at it in bulk.

Two engines judge the same spectra: Mascope's assignment engine and an
outside one reading a sample through the SDK. Both need the same
:class:`models.PatternScoring` for a sample, which is several rules in one
order: the width Stage A fitted, or the instrument class's where it fitted
none; the offset Stage A fitted, or the reagent lines' where they put the axis
beyond that width; the match window and the abundance floor of the sample's
match parameters; and the file's resolving power. Each rule is in this
library, and the order they are combined in is too
(:func:`pattern_scoring_for_sample`): two copies of it drifted, the resolution
term, the stand-in offset and the TOF handling each landing on one side
first.

:func:`score_candidates` scores a candidate list against a peak list the way
the composition finder ranks a peak's readings, for every candidate at once:
each distinct envelope predicted once, every predicted line matched with one
``searchsorted`` over the sorted peaks, and every envelope scored in one pass
(:func:`heuristic_filter.score_ion_envelopes`). Scored one at a time, a file's
first pass of some 10^5 candidates costs a millisecond each.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol

import numpy as np
import pandas as pd
import polars as pl

from mascope_tools.composition.heuristic_filter import (
    PATTERN_BASE_SNR,
    PATTERN_REQUIRED_LINES,
    _extract_formulae_and_charges,
    _target_peak,
    envelope_floor_for_peak,
    score_ion_envelopes,
)
from mascope_tools.composition.mass_accuracy import (
    SampleMassAccuracy,
    scoring_sigma_ppm,
)
from mascope_tools.composition.models import PatternScoring
from mascope_tools.composition.profiles import resolve_fallback_sigma_ppm


class MatchParams(Protocol):
    """The two fields of a sample's match parameters a search is scored at.

    The backend's resolved match parameters carry them under these names, and
    so does anything else that describes the sample's targeted match.
    """

    #: The window a predicted line is matched in, in ppm.
    mz_tolerance: float
    #: The deepest a line is predicted, relative to the ion's own line.
    isotope_abundance_threshold: float


def pattern_scoring_for_sample(
    mass_accuracy: SampleMassAccuracy,
    instrument_type: str | None,
    match_params: MatchParams,
    resolution: Callable[[float], float] | None = None,
) -> PatternScoring:
    """How a sample's isotope envelopes are scored, from what was measured of it.

    The width is Stage A's fitted one where it has one, else the instrument
    class's (:func:`profiles.resolve_fallback_sigma_ppm`), widened the same way
    in both cases (:func:`mass_accuracy.scoring_sigma_ppm`). The offset is
    :attr:`SampleMassAccuracy.scoring_mu_ppm`: the fitted one, else the
    reagent lines' where they are beyond the width, else none, scored as zero.
    The window and the floor are the sample's match parameters, the ones Stage
    A matched and generated its isotopes at, so both stages read a line alike.

    :param mass_accuracy: What Stage A measured of the sample's mass error. An
        empty :class:`SampleMassAccuracy` is a sample nothing measured, scored
        at the class's width and no offset.
    :param instrument_type: ``"orbi"``, ``"tof"``, or None/unknown, which
        takes the default class.
    :param match_params: The sample's resolved match parameters
        (:class:`MatchParams`).
    :param resolution: The file's resolving power as a function of m/z, or
        None where it could not be read; with it, lines one peak cannot
        separate are scored as one.
    :return: The scoring parameters for the sample's search.
    """
    return PatternScoring(
        sigma_ppm=scoring_sigma_ppm(
            mass_accuracy.sigma_ppm, resolve_fallback_sigma_ppm(instrument_type)
        ),
        mu_ppm=float(mass_accuracy.scoring_mu_ppm or 0.0),
        mz_tolerance_ppm=float(match_params.mz_tolerance),
        abundance_floor=float(match_params.isotope_abundance_threshold),
        resolution=resolution,
    )


#: The columns :func:`score_candidates` returns, one row per candidate.
CANDIDATE_SCORE_COLUMNS = (
    "fit_score",
    PATTERN_REQUIRED_LINES,
    PATTERN_BASE_SNR,
    "envelope_floor",
    "matched_lines",
    "labels",
    "predicted_mz",
    "predicted_rel",
    "observed_mz",
    "mass_error_ppm",
    "intensity_error",
)


def score_candidates(
    candidates: pd.DataFrame | Sequence[Mapping[str, Any]],
    peaks: pd.DataFrame | pl.DataFrame,
    scoring: PatternScoring | None = None,
) -> pd.DataFrame:
    """Score every candidate's envelope against a peak list, in bulk.

    The composition finder's ranking measurement (``match_isotopic_pattern``)
    for any number of candidates over any number of peaks: each candidate's
    envelope predicted to the depth its own peak's noise allows
    (:func:`heuristic_filter.envelope_floor_for_peak`), its lines merged where
    the file cannot resolve them, each line matched to the nearest peak inside
    the window, an isotopologue kept only within the intensity tolerance of
    its prediction, and the whole scored by the v2 fit. A candidate's score
    equals the finder's for the same candidate on the same peak list to
    floating-point rounding; it is the same rules on arrays.

    What is not done here is the finder's own policy: no candidate is dropped,
    elected or ranked. The result is in the candidates' order.

    :param candidates: One row per candidate with ``ion``, the ion formula
        ending in its charge sign (``"C6H5O-"``), and optionally
        ``observed_mass``, the m/z of the peak it was enumerated for. The
        envelope's depth is read off that peak; a candidate without one is
        predicted to the floor.
    :param peaks: The sample's peak list with ``mz`` and ``intensity`` and,
        where the file has one, ``signal_to_noise``. Need not be sorted.
    :param scoring: What the sample is scored at
        (:func:`pattern_scoring_for_sample`); None takes the defaults.
    :return: One row per candidate, on the candidates' index: ``fit_score``;
        :data:`heuristic_filter.PATTERN_REQUIRED_LINES`, whether the ion's own
        line and the brightest predicted one were both found;
        :data:`heuristic_filter.PATTERN_BASE_SNR`, the signal-to-noise its
        absent lines were judged against (NaN without one);
        ``envelope_floor``; ``matched_lines``; and per predicted line, as
        arrays in the envelope's order with the ion's own line first,
        ``labels``, ``predicted_mz``, ``predicted_rel`` (relative to the ion's
        own line), ``observed_mz`` (zero where nothing matched),
        ``mass_error_ppm`` and ``intensity_error``, both signed as the
        targeted matcher signs them and zero where nothing matched.
    """
    scoring = scoring or PatternScoring()
    frame = (
        candidates
        if isinstance(candidates, pd.DataFrame)
        else pd.DataFrame(list(candidates))
    )
    if frame.empty:
        return pd.DataFrame(columns=list(CANDIDATE_SCORE_COLUMNS), index=frame.index)

    # Sorted the way the finder's frame is: stable, so a list already in order
    # matches lines exactly as it would there.
    mzs = np.asarray(peaks["mz"].to_numpy(), dtype=float)
    order = np.argsort(mzs, kind="stable")
    mzs = mzs[order]
    intensities = np.asarray(peaks["intensity"].to_numpy())[order]
    snrs = (
        np.asarray(peaks["signal_to_noise"].to_numpy(), dtype=float)[order]
        if "signal_to_noise" in peaks.columns
        else None
    )

    positive = intensities[intensities > 0]
    faintest = float(positive.min()) if positive.size else 0.0
    observed = (
        frame["observed_mass"]
        if "observed_mass" in frame.columns
        else pd.Series(None, index=frame.index, dtype=object)
    )
    # One depth per peak, as the finder reads it for that peak's readings.
    depth_by_peak: dict[Any, tuple[float, float | None]] = {}
    floors = np.empty(len(frame), dtype=float)
    base_snrs: list[float | None] = []
    for position, mass in enumerate(observed):
        key = None if mass is None or pd.isna(mass) else float(mass)
        if key not in depth_by_peak:
            base_intensity, base_snr = _target_peak(
                [{"observed_mass": key}], mzs, intensities, snrs
            )
            reported = (
                float(base_snr)
                if base_snr is not None and np.isfinite(base_snr) and base_snr > 0
                else None
            )
            depth_by_peak[key] = (
                envelope_floor_for_peak(base_intensity, base_snr, faintest, scoring),
                reported,
            )
        floors[position], reported = depth_by_peak[key]
        base_snrs.append(reported)

    ion_formulas, ion_charges = _extract_formulae_and_charges(
        pl.Series("ion", [str(ion or "") for ion in frame["ion"]])
    )
    scores, required, isotopes = score_ion_envelopes(
        ion_formulas, ion_charges, floors, mzs, intensities, snrs, scoring
    )
    return pd.DataFrame(
        {
            "fit_score": scores,
            PATTERN_REQUIRED_LINES: required,
            PATTERN_BASE_SNR: np.array(
                [np.nan if snr is None else snr for snr in base_snrs], dtype=float
            ),
            "envelope_floor": floors,
            "matched_lines": [
                int(np.count_nonzero(np.asarray(i["masses"]) > 0)) for i in isotopes
            ],
            "labels": [i["labels"] for i in isotopes],
            "predicted_mz": [i["predicted_masses"] for i in isotopes],
            "predicted_rel": [i["predicted_intensities"] for i in isotopes],
            "observed_mz": [i["masses"] for i in isotopes],
            "mass_error_ppm": [i["mass_errors_ppm"] for i in isotopes],
            "intensity_error": [i["intensity_errors"] for i in isotopes],
        },
        index=frame.index,
    )
