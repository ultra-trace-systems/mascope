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

**Every count depends on the window, the channels and the offset it was taken
at**, so quote those with it: a density is a count over one box inside one
instrument's accuracy on one sample, and a bare number is not reproducible. On
forty committed peaks of a bromide-mode sample spanning 83 to 489 Da, through
that run's own three channels, at three times its fitted width (1.25 ppm) and
its fitted offset (-0.151 ppm): all forty measured, median 3, maximum 11, four
saturated, six reported censored.

**This is not a per-run measurement, and the numbers say so.** Searching one
channel at a time over the relaxed box, a 100-500 Da spectrum costs about 27 s
of band grids and about 6 ms per peak after that, so a sample with two thousand
committed peaks pays around 38 s against the ten or so seconds the whole
assignment of that sample takes. (An earlier arrangement banded every channel
together and quoted 40 ms a peak; that figure was measured partly on peaks the
overflow below returned nothing for, and is not this module's.) Batching the
chemistry filter over many peaks was tried and bought 2%, so what remains is the
enumeration itself. Call it the way ``alternatives_scoring`` is called: one
peak, or a handful, when somebody is actually looking at them. What a run can
afford per row is :func:`arbitration.candidate_density`, which counts inside the
box the finder already enumerated and costs nothing extra.

Ported from peaky's ``assignment/degeneracy.py`` (github.com/ultra-trace-systems/peaky),
where the measurement was fitted on real Br-/nitrate campaigns, and the port is
partial in a way worth stating because :data:`SATURATION_DENSITY` is peaky's
number and only means the same thing over the same population.

What IS ported: the box (as a default the caller may replace, not as a constant
of the measurement), the heteroatom-type gate
(:data:`MAX_HETEROATOM_TYPES`), the same-ion collapse, and the saturation
figure.

What is NOT: peaky filters every competitor through a relaxed CONTEXT PROFILE -
Van Krevelen windows, a DBE cap, an oxygen cap - which admits or refuses. Here
the competitors go through Mascope's own heuristic rules instead, which GRADE
rather than gate, so a formula those windows would have refused is counted with
a low plausibility rather than dropped. The two counts are therefore close
rather than equal, and the direction is known: this one is the more generous.
The default box floors carbon at one for the same reason every resolved Mascope
grid does - a carbon-free composition is not something this engine could commit,
so counting one as a rival to a committed reading would inflate the count with
answers that were never available.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Sequence

import numpy as np

from mascope_tools.composition import utils
from mascope_tools.composition.finder import (
    find_compositions,
    get_ionization_mech_string_list,
    grids_for_targets,
    neutral_mass_bounds,
)
from mascope_tools.composition.heuristic_filter import (
    apply_heuristic_rules,
    element_counts,
)
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
RELAXED_ELEMENT_RANGES = "C1-20 H0-36 N0-3 O0-12 S0-1 F0-17 Cl0-2 Br0-2 Si0-3"

#: Distinct heteroatom TYPES a competitor may carry. peaky's gate, ported with
#: it, because without it the count is of a different population: real
#: atmospheric and contaminant species carry at most a few heteroatom types -
#: CHO, CHON, CHOS, a single-halogen organic, a siloxane - and a formula mixing
#: bromine, chlorine, fluorine and silicon at once is arithmetic rather than a
#: rival. Mascope's own heuristic rules cannot stand in for it: they GRADE a
#: formula rather than gate it, so a five-heteroatom composition scores 1.0.
MAX_HETEROATOM_TYPES = 3

#: The heteroatoms it counts.
_HETEROATOMS = ("N", "S", "P", "F", "Cl", "Br", "Si", "I")

#: Competitors named on a reading, nearest in mass first.
DEFAULT_MAX_COMPETITORS = 6

#: A density above which the mass is not identifiable from accurate mass alone,
#: whatever else is true of the peak. peaky's own figure, kept so the two
#: engines' readings mean the same thing.
SATURATION_DENSITY = 8


@dataclass(frozen=True)
class DegeneracyReading:
    """What one peak's mass could be, over the box that was measured.

    Three states, not two, and the difference between them is the whole point of
    a measurement that is allowed to fail: ``measured`` false means the box
    could not be enumerated over this peak's window at all, ``density`` 0 means
    it was and nothing in it explains the peak, and any other density is a
    count. A reading that reported the first as the second would say "nothing
    could be here" about a peak it never looked at.

    :param density: Distinct plausible ions inside the window, the committed one
        included; 1 means the mass really is unique over this box. Meaningless
        when ``measured`` is false, where it is 0.
    :param competitors: Up to ``max_competitors`` of them, nearest the centre
        of the calibrated window first, as ``"<neutral> <mechanism>"``.
    :param measured: False when no grid could be built over this peak's window -
        the element box is too wide for the range the mechanisms open around it.
    :param censored: True when the search hit its own per-peak result cap, so
        the density is a lower bound rather than the count.
    :param saturated: True when the density passes :data:`SATURATION_DENSITY`.
    """

    density: int
    competitors: tuple[str, ...]
    measured: bool
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
    notations = get_ionization_mech_string_list(config.ionizations)
    # Centre the window on what the instrument reads as zero error. A candidate
    # at +2.4 ppm on an axis running +2.5 ppm high is on the calibration, and a
    # window around the raw m/z would have counted it as an outlier while
    # counting a true 0.0 ppm competitor twice as far inside.
    shifted = targets / (1.0 + mu_ppm * 1e-6)

    # ONE CHANNEL AT A TIME. The banded walk sizes its bands from the range ALL
    # the mechanisms open around a target, and a bromide channel beside a
    # deprotonation opens eighty daltons of neutral mass around one peak - wide
    # enough over a relaxed box to overflow the row bound, after which the walk
    # stops banding and every heavier peak is searched with no grid at all. Per
    # channel each band is the search window wide, so it fits, and a peak's
    # answer stops depending on which other peaks were asked with it.
    found: dict[float, list[dict]] = {mz: [] for mz in map(float, targets)}
    censored_at: dict[float, bool] = dict.fromkeys(found, False)
    measured_at: dict[float, bool] = dict.fromkeys(found, True)
    for notation in notations:
        one = replace(config, ionizations=notation)
        mechanism = utils.parse_ionization(notation)
        for original, (centre, grid) in zip(
            targets, grids_for_targets(shifted, one, [mechanism])
        ):
            mz = float(original)
            if grid is None:
                low, high = neutral_mass_bounds(
                    [centre], [mechanism], one.mass_range_ppm
                )
                if high < low or high <= 0:
                    # Not a gap in the measurement: this channel cannot explain
                    # a peak this light at all, because the adduct weighs more
                    # than the peak does. `find_compositions` skips it for the
                    # same reason, and marking the peak unmeasured for it would
                    # report every light peak of a bromide run as unanswered.
                    continue
                # The box could not be enumerated over this peak's window, so
                # whatever the other channels found is a lower bound and not a
                # count. Said outright rather than folded into the number.
                measured_at[mz] = False
                continue
            results = find_compositions(centre, one, grid=grid)
            if _hit_the_cap(results, one.max_result_rows):
                censored_at[mz] = True
            found[mz].extend(r for r in results if _plausible_rival(r))

    out: dict[float, DegeneracyReading] = {}
    for mz, results in found.items():
        if not measured_at[mz]:
            out[mz] = DegeneracyReading(
                density=0,
                competitors=(),
                measured=False,
                censored=False,
                saturated=False,
            )
            continue
        if results:
            results, _log = apply_heuristic_rules(results, heuristics_config=heuristics)
        out[mz] = _reading(
            results, censored=censored_at[mz], max_competitors=max_competitors
        )
    return out


def _plausible_rival(result: dict) -> bool:
    """Whether a composition is a species a source could present at all.

    peaky's heteroatom-type gate. It runs before the graded chemistry filter
    because it answers a different question: not how plausible this formula is,
    but whether counting it as a competitor describes the chemistry. A relaxed
    box admits millions of formulas mixing four halogens with silicon, and a
    density that counted them would say every heavy peak is unidentifiable.
    """
    counts = element_counts(str(result.get("formula") or ""))
    if not counts:
        return True
    return (
        sum(1 for element in _HETEROATOMS if counts.get(element, 0) > 0)
        <= MAX_HETEROATOM_TYPES
    )


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
        measured=True,
        censored=censored,
        saturated=density > SATURATION_DENSITY,
    )
