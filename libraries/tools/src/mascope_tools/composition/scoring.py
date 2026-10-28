"""What a sample is scored at.

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
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

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
