"""How many things a peak's mass could be, counted across element families.

A run enumerates ONE element box - the reagent profile's, narrowed by the
sample's chemistry context - and a peak's candidate density is relative to that
box (see :func:`arbitration.candidate_density`). "Unique in the window" is
therefore a claim about the box as much as about the spectrum: at heavy m/z a
fluorinated, a sulfur-rich and a silicon formula can all sit inside the
instrument's mass accuracy, and a search that enumerated none of them will
report the one it did enumerate as unopposed.

This module re-asks the question over a WIDER box and reports the answer as a
number: how many distinct, chemically plausible IONS fall inside a calibrated
mass window around the peak. It is a measurement and nothing else - it does not
rank, does not pick a winner, and does not know what the run committed. What a
high count means for a row's tier is the tiering's judgement; a corroborated
reading is not made doubtful by having neighbours, and an uncorroborated one is.

Readings of the same ION are one candidate, not several. A covalent and a
cluster reading of one m/z (``+NH4+`` on M, ``+H+`` on M+NH3) are the same
physical ion arriving under two names, so they are collapsed on the ion formula
before anything is counted - the same collapse
:func:`heuristic_filter.elect_same_ion_families` makes inside the run, for the
same reason.

**This is not a per-run measurement, and the numbers say so.** Over the relaxed
box it costs about 30 s to build the band grids for a 100-500 Da spectrum and
about 40 ms per peak after that - so a sample with two thousand committed peaks
pays around two minutes, against the ten or so seconds the whole assignment of
that sample currently takes. Batching the chemistry filter over many peaks was
tried and bought 2%, so the cost is the enumeration itself and not the frame.
Call it the way ``alternatives_scoring`` is called: one peak, or a handful, when
somebody is actually looking at them. What a run can afford per row is
:func:`arbitration.candidate_density`, which counts inside the box the finder
already enumerated and costs nothing extra.

Ported from peaky's ``assignment/degeneracy.py`` (github.com/ultra-trace-systems/peaky),
where the measurement was fitted on real Br-/nitrate campaigns. What is not
ported is peaky's hard-coded relaxed box: the box is this caller's to state, and
:data:`RELAXED_ELEMENT_RANGES` is offered as the default it used rather than as
a constant of the measurement.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

from mascope_tools.composition import utils
from mascope_tools.composition.finder import (
    find_compositions,
    get_ionization_mech_string_list,
    grids_for_targets,
)
from mascope_tools.composition.heuristic_filter import apply_heuristic_rules
from mascope_tools.composition.models import (
    CompositionSearchConfig,
    HeuristicFilterConfig,
)


#: The element box peaky measured cross-family degeneracy over: wide enough to
#: admit the real competitors an atmospheric run can open - the fluorinated,
#: sulfur, silicon and halogen families beside CHON - and no wider, because the
#: count is meant to be of things a source could plausibly present. Oxygen is
#: capped at HOM levels: an open oxygen range grids an O-monster mass fit onto
#: every heavy peak and the count stops meaning anything.
RELAXED_ELEMENT_RANGES = "C0-20 H0-36 N0-3 O0-12 S0-1 F0-17 Cl0-2 Br0-2 Si0-3"

#: Competitors named on a reading, nearest in mass first.
DEFAULT_MAX_COMPETITORS = 6

#: A density above which the mass is not identifiable from accurate mass alone,
#: whatever else is true of the peak. peaky's own figure, kept so the two
#: engines' readings mean the same thing.
SATURATION_DENSITY = 8


@dataclass(frozen=True)
class DegeneracyReading:
    """What one peak's mass could be, over the box that was measured.

    :param density: Distinct plausible ions inside the window, the committed
        one included. 1 means the mass really is unique over this box.
    :param competitors: Up to ``max_competitors`` of them, nearest the centre
        of the calibrated window first, as ``"<neutral> <mechanism>"``.
    :param censored: True when the search hit its own per-peak result cap, so
        the density is a lower bound rather than the count.
    :param saturated: True when the density passes :data:`SATURATION_DENSITY`.
    """

    density: int
    competitors: tuple[str, ...]
    censored: bool
    saturated: bool


def measure_degeneracy(
    mzs: Sequence[float],
    *,
    config: CompositionSearchConfig,
    heuristics: HeuristicFilterConfig | None = None,
    mu_ppm: float = 0.0,
    max_competitors: int = DEFAULT_MAX_COMPETITORS,
) -> dict[float, DegeneracyReading]:
    """Count the plausible ions sharing each peak's mass, over one element box.

    The window is ``config.mass_range_ppm`` either side of the peak, which the
    caller sets from the sample's own fitted mass width rather than from a
    constant - the whole point of the measurement is how many things fit inside
    the accuracy this instrument actually has on this sample.

    :param mzs: The peaks to measure, ASCENDING. Out of order they still get an
        answer, but each one pays for its own grid instead of sharing a band's.
    :param config: The search: the element box to count over, the ionization
        channels a reading may come through, the window, and the per-peak result
        cap that decides when a density is reported censored.
    :param heuristics: The chemistry filter applied to each competitor, so what
        is counted is plausible formulas rather than arithmetic. Defaults to the
        filter's own defaults.
    :param mu_ppm: The sample's fitted mass-error offset. The window is centred
        on the calibration rather than on the raw reading, so a systematically
        shifted axis does not count the competitors on one side only.
    :param max_competitors: How many competitors to name per peak.
    :return: One reading per DISTINCT m/z given; a repeated m/z is measured once.
    """
    targets = np.asarray([float(mz) for mz in mzs], dtype=float)
    if targets.size == 0:
        return {}
    mechanisms = [
        utils.parse_ionization(name)
        for name in get_ionization_mech_string_list(config.ionizations)
    ]
    # Centre the window on what the instrument reads as zero error. A candidate
    # at +2.4 ppm on an axis running +2.5 ppm high is on the calibration, and a
    # window around the raw m/z would have counted it as an outlier while
    # counting a true 0.0 ppm competitor twice as far inside.
    shifted = targets / (1.0 + mu_ppm * 1e-6)

    out: dict[float, DegeneracyReading] = {}
    for original, (centre, grid) in zip(
        targets, grids_for_targets(shifted, config, mechanisms)
    ):
        mz = float(original)
        if mz in out:
            continue
        results = find_compositions(centre, config, grid=grid)
        censored = _hit_the_cap(results, config.max_result_rows)
        if results:
            results, _log = apply_heuristic_rules(results, heuristics_config=heuristics)
        out[mz] = _reading(results, censored=censored, max_competitors=max_competitors)
    return out


def _hit_the_cap(results: list[dict], max_result_rows: int) -> bool:
    """Whether the search returned everything it found, or stopped counting.

    ``find_compositions`` applies its row cap PER MECHANISM, keeping the closest
    rows of each, so a peak with two channels can return twice the cap without
    having lost anything. Testing the total against the cap therefore calls a
    complete answer censored; what says the count is a lower bound is one
    channel filling its own cap.
    """
    if not max_result_rows:
        return False
    per_mechanism: dict[str, int] = {}
    for result in results:
        key = str(result.get("ionization_mechanism") or "")
        per_mechanism[key] = per_mechanism.get(key, 0) + 1
    return any(count >= max_result_rows for count in per_mechanism.values())


def _reading(
    results: list[dict], *, censored: bool, max_competitors: int
) -> DegeneracyReading:
    """Collapse a peak's plausible compositions into one reading."""
    by_ion: dict[str, tuple[float, str]] = {}
    for result in results:
        ion = str(result.get("ion") or "")
        if not ion:
            continue
        error = abs(float(result.get("composition_error_ppm") or 0.0))
        label = f"{result.get('formula')} {result.get('ionization_mechanism')}".strip()
        previous = by_ion.get(ion)
        if previous is None or error < previous[0]:
            by_ion[ion] = (error, label)
    density = len(by_ion)
    nearest = sorted(by_ion.values(), key=lambda pair: (pair[0], pair[1]))
    return DegeneracyReading(
        density=density,
        competitors=tuple(label for _error, label in nearest[:max_competitors]),
        censored=censored,
        saturated=density > SATURATION_DENSITY,
    )
