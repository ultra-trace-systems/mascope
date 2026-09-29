"""Reading a peak as any line of a candidate ion's isotope envelope.

The composition search asks which neutral formulas make an ion whose
monoisotopic line lands on a peak. For most peaks that is the right question,
and for three kinds it is the wrong one:

- a heavier line of an ion whose monoisotopic line sits elsewhere in the
  spectrum: the 13C line one mass unit up, the 34S or 37Cl line two up;
- the brightest line of an ion whose monoisotopic line is not its brightest: a
  dibromide's 79Br81Br line is twice its 79Br2 one;
- the unlabelled remainder of a labelled reagent's ion, one mass unit BELOW the
  15N line the search reads a 15N-nitrate adduct at.

Asked of those peaks, the search proposes whatever fits their mass as a
monoisotopic line and misses the compound whose line each one is. This module
asks the other question: which formulas put ANY line of their ion's envelope,
at least a floor of its brightest line, within the window.

It does not predict an envelope for every formula in a mass range. A line sits
a whole number of mass units from its ion's monoisotopic line plus a mass
defect bounded by the isotopes involved (:func:`line_offsets`), so the formulas
one offset could explain are a narrow window of the neutral grid, found by the
binary search the monoisotopic reading uses. Only those formulas are predicted,
and one is kept where its envelope puts a line on the peak
(:func:`isotopologue_readings`).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from functools import lru_cache
from math import comb
from math import floor as round_down

import numpy as np
from IsoSpecPy import IsoThreshold
from pyteomics.mass import calculate_mass

from mascope_tools.composition import utils
from mascope_tools.composition.config import ELECTRON_MASS
from mascope_tools.composition.custom_elements import CUSTOM_ELEMENTS
from mascope_tools.composition.grid import NeutralGrid
from mascope_tools.composition.heuristic_filter import predict_isotopes
from mascope_tools.composition.models import (
    CompositionSearchConfig,
    IonizationMechanism,
    Result,
)


__all__ = [
    "MAX_NOMINAL_OFFSET",
    "isotopologue_mass_bounds",
    "isotopologue_readings",
    "line_offsets",
]


#: The furthest, in whole mass units, a line may sit from its ion's
#: monoisotopic line and still be searched for. A pentabromide's brightest line
#: is four units up and its envelope reaches ten; nothing a composition search
#: is given to search comes close to twelve, and the bound only keeps a box of
#: absurd width from turning into a scan of the whole grid.
MAX_NOMINAL_OFFSET = 12

#: An element's configurations worth a line: each one's nominal offset, its
#: mass offset in daltons, and its abundance relative to the element's most
#: abundant configuration.
_ElementLines = tuple[tuple[int, float, float], ...]

#: What :func:`line_offsets` answers: per nominal offset, the lowest and the
#: highest mass offset (daltons) a line at it can have.
LineOffsets = dict[int, tuple[float, float]]


@lru_cache(maxsize=8192)
def _element_lines(symbol: str, count: int, floor: float) -> _ElementLines:
    """The configurations of ``count`` atoms of one element worth a line.

    Offsets are measured from all ``count`` atoms at the element's monoisotopic
    mass, which is the mass the neutral grid gives it. A configuration is
    worth a line when it is at least ``floor`` of the element's most abundant
    one: the envelope of an ion is the product of its elements' own, so no line
    of the ion can clear the floor on an element configuration that does not.

    :param symbol: A plain element symbol.
    :param count: How many of its atoms the ion holds.
    :param floor: The abundance, relative to the most abundant configuration,
        below which a configuration is not a line.
    :return: ``(nominal offset, mass offset, relative abundance)`` per
        configuration; the monoisotopic one alone for an element the isotope
        tables do not know.
    """
    if count <= 0:
        return ((0, 0.0, 1.0),)
    try:
        reference = calculate_mass(formula=symbol) * count
        lines = IsoThreshold(formula=f"{symbol}{count}", threshold=floor)
    except Exception:  # noqa: BLE001 - an element with no isotope data has one line
        return ((0, 0.0, 1.0),)
    masses = np.fromiter(lines.masses, dtype=float)
    probs = np.fromiter(lines.probs, dtype=float)
    if not probs.size:
        return ((0, 0.0, 1.0),)
    relative = probs / probs.max()
    return tuple(
        (int(round(mass - reference)), float(mass - reference), float(share))
        for mass, share in zip(masses, relative)
    )


def _custom_lines(symbol: str, count: int, floor: float) -> _ElementLines:
    """The configurations of a labelled reagent's atoms worth a line.

    Measured from every atom at its LABELLED mass, which is where the search
    reads a labelled adduct, so the unlabelled remainder sits below it: a
    single 98% 15N atom answers its 15N line and a 14N line one unit down at
    2% of it.
    """
    element = CUSTOM_ELEMENTS[symbol]
    (light_mass, light_number), (heavy_mass, heavy_number) = (
        element.isotopes[0],
        element.isotopes[-1],
    )
    purity = element.default_purity
    probs = [
        comb(count, light) * purity ** (count - light) * (1.0 - purity) ** light
        for light in range(count + 1)
    ]
    most = max(probs)
    return tuple(
        (
            -light * (heavy_number - light_number),
            light * (light_mass - heavy_mass),
            prob / most,
        )
        for light, prob in enumerate(probs)
        if prob >= floor * most
    )


def _ion_count_ranges(
    config: CompositionSearchConfig,
    mechanism: IonizationMechanism,
    target_mz: float,
) -> dict[str, tuple[int, int]]:
    """How many atoms of each element an ion this search can build may hold.

    The grid's range for the neutral, moved by what the mechanism adds or
    removes, and capped by what fits in the ion's mass at all - a box's
    ``H0-160`` means nothing at m/z 300. Explicitly labelled grid atoms
    (``[13C]``) are left out: their mass is fixed, so they carry no envelope of
    their own.
    """
    box: dict[str, tuple[int, int]] = {}
    for atom in utils.parse_atom_count_ranges(config.element_count_ranges):
        if atom.symbol.startswith("["):
            continue
        box[atom.symbol] = (atom.min_count, atom.max_count)

    sign = 1 if mechanism.addition else -1
    moiety = utils.ionization_composition(mechanism.formula)
    ranges: dict[str, tuple[int, int]] = {}
    for symbol in set(box) | set(moiety):
        low, high = box.get(symbol, (0, 0))
        shift = sign * int(moiety.get(symbol, 0))
        low, high = max(0, low + shift), max(0, high + shift)
        if symbol in CUSTOM_ELEMENTS:
            atom_mass = CUSTOM_ELEMENTS[symbol].isotopes[-1][0]
        else:
            try:
                atom_mass = calculate_mass(formula=symbol)
            except Exception:  # noqa: BLE001 - an unknown symbol holds no envelope
                continue
        # Generous by a few mass units, for the lines that sit below the
        # monoisotopic one and so put the ion's own mass above the target's.
        high = min(high, int(round_down((abs(target_mz) + 5.0) / atom_mass)))
        if high >= low:
            ranges[symbol] = (low, high)
    return ranges


def line_offsets(
    config: CompositionSearchConfig,
    mechanism: IonizationMechanism,
    target_mz: float,
    floor: float,
) -> LineOffsets:
    """Where a line of an ion this search can build may sit, relative to its
    monoisotopic line.

    An ion's configuration is a product of one configuration per element, and
    its abundance relative to the ion's most abundant line is the product of
    each element's relative to that element's most abundant one - every factor
    at most one. So a line at least ``floor`` of its ion's brightest is built
    only of element configurations each at least ``floor`` of their own, and
    the product of the best of them bounds it from above. Combining the
    elements' configurations under that bound, element by element, gives every
    nominal offset a line could have and the span of mass offsets at each, for
    every formula the grid holds, without enumerating a formula.

    :param config: The search, whose element box bounds the atom counts.
    :param mechanism: The ionization mechanism the ion is built with.
    :param target_mz: The peak being searched, which bounds the atom counts by
        mass.
    :param floor: The abundance, relative to the ion's most abundant line,
        below which a line is not searched for; 1.0 searches the most abundant
        line alone.
    :return: ``{nominal offset: (lowest, highest) mass offset}``, the
        monoisotopic offset 0 left out, each offset within
        :data:`MAX_NOMINAL_OFFSET` either way.
    """
    # nominal offset -> (best achievable relative abundance, mass span)
    reachable: dict[int, tuple[float, float, float]] = {0: (1.0, 0.0, 0.0)}
    for symbol, (low, high) in sorted(
        _ion_count_ranges(config, mechanism, target_mz).items()
    ):
        per_offset: dict[int, tuple[float, float, float]] = {}
        for count in range(low, high + 1):
            lines = (
                _custom_lines(symbol, count, floor)
                if symbol in CUSTOM_ELEMENTS
                else _element_lines(symbol, count, floor)
            )
            for nominal, delta, share in lines:
                best, lowest, highest = per_offset.get(
                    nominal, (0.0, float("inf"), float("-inf"))
                )
                per_offset[nominal] = (
                    max(best, share),
                    min(lowest, delta),
                    max(highest, delta),
                )
        combined: dict[int, tuple[float, float, float]] = {}
        for nominal, (best, lowest, highest) in reachable.items():
            for step, (share, step_low, step_high) in per_offset.items():
                bound = best * share
                offset = nominal + step
                if bound < floor or abs(offset) > MAX_NOMINAL_OFFSET:
                    continue
                known = combined.get(offset, (0.0, float("inf"), float("-inf")))
                combined[offset] = (
                    max(known[0], bound),
                    min(known[1], lowest + step_low),
                    max(known[2], highest + step_high),
                )
        reachable = combined
    return {
        nominal: (lowest, highest)
        for nominal, (_, lowest, highest) in sorted(reachable.items())
        if nominal != 0
    }


def isotopologue_mass_bounds(
    target_mz: float,
    mechanisms: Sequence[IonizationMechanism],
    offsets: Sequence[LineOffsets],
    tolerance_da: float,
) -> tuple[float, float]:
    """The neutral masses a grid must span to answer every line reading.

    :param target_mz: The peak being searched.
    :param mechanisms: The ionization mechanisms, in the order of ``offsets``.
    :param offsets: Each mechanism's :func:`line_offsets`.
    :param tolerance_da: The search window's half-width.
    :return: ``(mass_min, mass_max)``; ``(0.0, -1.0)`` when nothing is searched.
    """
    lows, highs = [], []
    for mechanism, spans in zip(mechanisms, offsets):
        shift = mechanism.mass if mechanism.addition else -mechanism.mass
        for lowest, highest in spans.values():
            lows.append(target_mz - shift - highest - tolerance_da)
            highs.append(target_mz - shift - lowest + tolerance_da)
    if not lows:
        return (0.0, -1.0)
    return (max(0.0, min(lows)), max(highs))


def _envelope(ion_formula: str, charge: int, floor: float):
    """An ion's lines at least ``floor`` of its brightest: m/z and share.

    IsoSpec alone for an ordinary ion. A labelled reagent's atoms are not in
    IsoSpec's tables, so for an ion holding one the rest of the ion is predicted
    and convolved with the reagent's own distribution; the envelope of the whole
    is the product of the two, which is also why thresholding each part at the
    floor loses no line of the product.

    :return: ``(m/z array, relative abundance array)``, empty where the formula
        cannot be predicted: a grid atom carrying a fixed isotope label
        (``[13C]``) has no envelope of its own, and reading it as its base
        element would predict one it does not have.
    """
    if "[" in ion_formula:
        return np.empty(0), np.empty(0)
    counts = utils.parse_composition(ion_formula)
    labelled = {
        symbol: int(n) for symbol, n in counts.items() if symbol in CUSTOM_ELEMENTS
    }
    base = {
        symbol: int(n)
        for symbol, n in counts.items()
        if symbol not in CUSTOM_ELEMENTS and n > 0
    }
    try:
        if base:
            lines = IsoThreshold(formula=utils.to_hill_order(base), threshold=floor)
            masses = np.fromiter(lines.masses, dtype=float)
            probs = np.fromiter(lines.probs, dtype=float)
        else:
            masses, probs = np.zeros(1), np.ones(1)
    except Exception:  # noqa: BLE001 - IsoSpec refuses a bracketed isotope
        return np.empty(0), np.empty(0)
    for symbol, n in labelled.items():
        heavy_mass = CUSTOM_ELEMENTS[symbol].isotopes[-1][0]
        custom = _custom_lines(symbol, n, floor)
        masses = (
            masses[:, None] + np.array([n * heavy_mass + d for _, d, _ in custom])
        ).ravel()
        probs = (probs[:, None] * np.array([s for _, _, s in custom])).ravel()
    if not probs.size:
        return np.empty(0), np.empty(0)
    share = probs / probs.max()
    keep = share >= floor
    mzs = (masses[keep] - ELECTRON_MASS * charge) / abs(charge)
    return mzs, share[keep]


def _label_of(ion_formula: str, charge: int, line_mz: float, floor: float) -> str:
    """The isotope label the rest of the finder gives a line: ``13C``, ``81Br2``,
    ``13C+34S``, and ``14N`` for a labelled reagent's unlabelled remainder."""
    mzs, _, labels = predict_isotopes(ion_formula, charge, threshold=floor / 10.0)
    if not len(mzs):
        return ""
    return labels[int(np.argmin(np.abs(np.asarray(mzs) - line_mz)))]


def isotopologue_readings(
    target_mz: float,
    tolerance_da: float,
    mechanism: IonizationMechanism,
    grid: NeutralGrid,
    offsets: LineOffsets,
    floor: float,
    max_rows: int,
    skip_rows: Sequence[int] = (),
) -> list[Result]:
    """Compositions one of whose ion's lines, other than the monoisotopic one,
    lands on the target.

    Each nominal offset's span is one window of the grid, and every formula in
    one is predicted: the window only says a line COULD sit on the target, the
    envelope says whether one does and how bright it is. Where several lines of
    one ion are inside the window - a wide window around fine structure - the
    brightest is the reading, being the line a matcher would give the peak to.

    :param target_mz: The peak being searched.
    :param tolerance_da: The search window's half-width.
    :param mechanism: The ionization mechanism the ions are built with.
    :param grid: A neutral grid spanning :func:`isotopologue_mass_bounds`.
    :param offsets: The mechanism's :func:`line_offsets`.
    :param floor: The least share of its ion's brightest line a line may have.
    :param max_rows: How many readings to keep, the closest in mass first.
    :param skip_rows: Grid rows already read as the monoisotopic line of this
        mechanism's ion; no other line of theirs can be on the target.
    :return: One :class:`Result` per formula, its mass error taken against the
        line it is read as, with the line's label, nominal offset, m/z and
        share of the brightest line.
    """
    shift = mechanism.mass if mechanism.addition else -mechanism.mass
    moiety = utils.ionization_composition(mechanism.formula)
    sign = 1 if mechanism.addition else -1
    skipped = set(skip_rows)

    candidates: dict[int, None] = {}
    for lowest, highest in offsets.values():
        centre = target_mz - shift - (lowest + highest) / 2.0
        half_width = (highest - lowest) / 2.0 + tolerance_da
        for row in grid.window(centre, half_width):
            if row not in skipped:
                candidates.setdefault(row, None)

    readings: list[tuple[float, Result]] = []
    for row in candidates:
        if grid.mass[row] <= 0.0:
            continue  # the empty neutral: the adduct's own lines, read below
        counts = grid.pyteomics_composition(row)
        if not mechanism.addition and any(
            counts.get(symbol, 0) < n for symbol, n in moiety.items()
        ):
            continue  # a removal the neutral has nothing to give for
        ion = utils.combine_counts_and_ionization(counts, mechanism)
        reading = _line_on_target(ion, mechanism, target_mz, tolerance_da, floor)
        if reading is None:
            continue
        line_mz, share = reading
        neutral_mass = float(grid.mass[row])
        monoisotopic_mz = neutral_mass + shift
        error_ppm = (target_mz - line_mz) / line_mz * 1e6
        readings.append(
            (
                abs(error_ppm),
                Result(
                    formula=utils.to_hill_order(grid.composition(row)),
                    neutral_mass=neutral_mass,
                    composition_error_ppm=error_ppm,
                    unsaturation=(
                        None
                        if grid.unsaturation is None
                        else float(grid.unsaturation[row])
                    ),
                    ion=ion,
                    ionization_mechanism=mechanism.mascope_notation,
                    observed_mass=target_mz,
                    isotope_label=_label_of(ion[:-1], mechanism.charge, line_mz, floor),
                    isotope_offset=int(round(line_mz - monoisotopic_mz)),
                    isotope_mz=line_mz,
                    isotope_abundance=share,
                ),
            )
        )

    readings.extend(
        _adduct_line_readings(target_mz, tolerance_da, mechanism, moiety, sign, floor)
    )
    readings.sort(key=lambda pair: pair[0])
    return [result for _, result in readings[:max_rows]]


def _line_on_target(
    ion: str,
    mechanism: IonizationMechanism,
    target_mz: float,
    tolerance_da: float,
    floor: float,
) -> tuple[float, float] | None:
    """The brightest line of an ion within the window, when there is one.

    :return: ``(line m/z, share of the brightest line)``, or None.
    """
    mzs, shares = _envelope(ion[:-1], mechanism.charge, floor)
    inside = np.abs(mzs - target_mz) <= tolerance_da
    if not inside.any():
        return None
    brightest = int(np.argmax(np.where(inside, shares, -1.0)))
    return float(mzs[brightest]), float(shares[brightest])


def _adduct_line_readings(
    target_mz: float,
    tolerance_da: float,
    mechanism: IonizationMechanism,
    moiety: Mapping[str, int],
    sign: int,
    floor: float,
) -> list[tuple[float, Result]]:
    """A line of the adduct's own ion on the target: the reagent's isotopologue.

    The search reads the bare adduct as the empty formula ``()`` where its
    monoisotopic line is the peak; its 81Br or 37Cl line, or a labelled
    reagent's unlabelled remainder, is the same ion read at another line.
    """
    if sign < 0 or not moiety:
        return []
    ion = utils.combine_counts_and_ionization({}, mechanism)
    reading = _line_on_target(ion, mechanism, target_mz, tolerance_da, floor)
    if reading is None:
        return []
    line_mz, share = reading
    offset = int(round(line_mz - mechanism.mass))
    if offset == 0:
        return []  # the monoisotopic line, which the search reads already
    error_ppm = (target_mz - line_mz) / line_mz * 1e6
    return [
        (
            abs(error_ppm),
            Result(
                formula="()",
                neutral_mass=0.0,
                composition_error_ppm=error_ppm,
                ion=ion,
                ionization_mechanism=mechanism.mascope_notation,
                observed_mass=target_mz,
                isotope_label=_label_of(ion[:-1], mechanism.charge, line_mz, floor),
                isotope_offset=offset,
                isotope_mz=line_mz,
                isotope_abundance=share,
            ),
        )
    ]
