"""Chlorine and bromine read from their twin line, not from an opened grid.

Step 3.6 of the assignment quality plan, item 4 and the rider from the
reference engine's 0.10 line (read, not copied). A chlorine atom puts a second
line 1.99705 Da above its first at a third of its height (37Cl against 35Cl), and
a bromine atom one 1.99795 Da up at nearly its full height (81Br against 79Br).
No other element pair makes that line: it is the evidence a chlorinated or
brominated reading stands on, and the evidence a halogen-free one cannot
account for.

The formula search cannot read it on its own. Its grid holds chlorine only
where a profile opens it, the raw ratio rule then refuses a chlorine formula
under twenty carbons (``config.DEFAULT_ELEMENTAL_RATIO_RANGE``, Cl/C 0.05),
and on a labelled-nitrate run a chlorinated deprotonated ion is mass-identical
to a nitrate cluster with two more carbons (Cl against C2H-4 15N, 0.044 mDa
apart): the search elects the reading its grid holds and leaves the twin
unexplained. Opening chlorine in every grid would fit it to every peak a
coincidence lets it reach.

So the reading is proposed from the twin (:func:`find_twins`). A peak the
search searched - left unexplained, or committed to a reading whose ion carries
no chlorine or bromine - whose twin sits unexplained within :data:`TWIN_PPM` of
the exact offset, at one atom's ratio, is searched again over a grid of carbon,
hydrogen, oxygen and that one halogen, with the halogen's ratio band opened
(:func:`reread_twins`). The reading that search elects replaces the peak's only
where it passes every gate the run puts a reading through: the context's
windows and the structural rules in the search, its own twin line claimed, its
evidence at the run's candidate bar, and its mass error on the sample's
calibration. The reference's own re-read took its labelled-nitrate batch's
merged Assigned rows from 498 to 519, and fired nowhere else.

A halogen is read only where the run's context opens it, or a list the run read
names a compound carrying it and the context does not close it, and never the
reagent's own (:func:`twin_halogens`): the certified cylinder of the gate holds
chlorobenzene, which no grid of the charge-transfer source reaches, and a
bromide source's one-bromine patterns are its own clusters.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field, replace

import numpy as np
import pandas as pd
from IsoSpecPy import PeriodicTbl

from mascope_backend.api.new.peak_assignments.engine import (
    UNTARGETED_IONIZATION,
    UNTARGETED_NO_MATCH,
    cap_alternatives,
)
from mascope_tools.composition.finder import assign_compositions
from mascope_tools.composition.heuristic_filter import (
    element_counts,
    formula_plausibility,
)
from mascope_tools.composition.models import (
    CompositionSearchConfig,
    HeuristicFilterConfig,
    PatternScoring,
)
from mascope_tools.composition.profiles import (
    ChemistryContext,
    format_element_ranges,
    parse_element_ranges,
)


def _isotope_step(element: str, heavy: int) -> float:
    """The mass between an element's lightest isotope and the given one."""
    masses = PeriodicTbl.symbol_to_masses[element]
    numbers = [round(mass) for mass in masses]
    return float(masses[numbers.index(heavy)] - masses[0])


#: The halogens read from their twin, in the order a peak with both twins
#: within reach is asked them (the nearer offset wins).
TWIN_HALOGENS: tuple[str, ...] = ("Cl", "Br")

#: Each halogen's heavy isotope, which names its twin line.
HEAVY_ISOTOPE: dict[str, int] = {"Cl": 37, "Br": 81}

#: The twin's distance above its line, in Da: 37Cl less 35Cl, 81Br less 79Br.
TWIN_OFFSET: dict[str, float] = {
    element: _isotope_step(element, HEAVY_ISOTOPE[element]) for element in TWIN_HALOGENS
}

#: The twin's height over its line for one atom of the halogen: the heavy
#: isotope's natural abundance over the light one's.
ONE_ATOM_RATIO: dict[str, float] = {
    element: float(
        PeriodicTbl.symbol_to_probs[element][1]
        / PeriodicTbl.symbol_to_probs[element][0]
    )
    for element in TWIN_HALOGENS
}

#: How far from the exact offset a twin may sit, in ppm of its m/z. Two: the
#: offset is a property of the halogen alone, so the twin and its line share
#: their calibration and their distance carries none of it.
TWIN_PPM = 2.0

#: How far from one atom's ratio a twin's height over its line may be and still
#: be read as one: a factor either way. The ratio only proposes the reading -
#: the reading's own envelope is then fitted against the spectrum - so the band
#: is wide enough for an Orbitrap's height noise on a dim twin and narrow
#: enough to keep a chlorine reading off a bromine's twin and the other way
#: round (0.32 against 0.97, a factor of three apart).
TWIN_RATIO_FACTOR = 1.5

#: The elements a committed reading's own heavy-isotope lines are evidence for:
#: a reading whose 15N, 34S or 30Si line is committed with it stands on that
#: line, and a twin does not take it.
OWN_EVIDENCE_ELEMENTS: tuple[str, ...] = ("N", "S", "Si")

#: The offsets at which a twin may be another committed ion's line instead -
#: its 13C, 13C2, 18O, 34S, 30Si, 37Cl or 81Br line - and so be no twin.
OTHER_ION_OFFSETS: tuple[float, ...] = (
    _isotope_step("C", 13),
    2 * _isotope_step("C", 13),
    _isotope_step("O", 18),
    _isotope_step("S", 34),
    _isotope_step("Si", 30),
    TWIN_OFFSET["Cl"],
    TWIN_OFFSET["Br"],
)

#: The halogen's raw ratio band in the re-read: the common range of Kind &
#: Fiehn 2007 (Table 2), which the graded plausibility reads too
#: (``heuristic_filter._RATIO_BANDS``), in place of the search's one atom per
#: twenty carbons.
TWIN_HALOGEN_RATIO_BAND: tuple[float, float] = (0.0, 0.8)

#: The elements a twin's reading is built of besides its halogen: carbon,
#: hydrogen and oxygen. The twin confirms the halogen and nothing else, so a
#: nitrogen or a sulfur in its reading would be a heteroatom fit no line saw.
TWIN_GRID_ELEMENTS: tuple[str, ...] = ("C", "H", "O")


def twin_halogens(
    context: ChemistryContext,
    listed_formulas: Iterable[str] = (),
    reagent_formula: str | None = None,
) -> tuple[str, ...]:
    """The halogens a run reads from their twin.

    A halogen the context opens (a positive element cap), or one a list the
    run read names a compound carrying while the context does not close it (a
    cap of zero closes it). A context that says nothing of a halogen leaves it
    to the lists.

    Never the reagent's own halogen. On a bromide source a peak with one
    bromine's twin is an analyte clustered with the reagent, ``[M+Br]-``,
    which the search reads through the mode's own channel; the re-read's grid
    puts the halogen in the neutral, and would read that peak as a covalent
    bromine the sample never held.

    :param context: The run's chemistry context.
    :param listed_formulas: The neutral formulas the run's lists name.
    :param reagent_formula: The reagent profile's own composition
        (``ReagentProfile.reagent_formula``), whose halogens are left out.
    :return: The halogens, in :data:`TWIN_HALOGENS` order.
    """
    listed = [element_counts(formula) or {} for formula in listed_formulas]
    reagent = element_counts(reagent_formula or "") or {}
    halogens = []
    for halogen in TWIN_HALOGENS:
        cap = context.element_caps.get(halogen)
        if cap == 0 or reagent.get(halogen):
            continue
        if (cap is not None and cap > 0) or any(
            counts.get(halogen) for counts in listed
        ):
            halogens.append(halogen)
    return tuple(halogens)


@dataclass(frozen=True)
class Twin:
    """A peak whose halogen twin sits unexplained above it.

    :param mz: The peak's m/z.
    :param twin_mz: The twin's.
    :param ratio: The twin's height over the peak's.
    :param halogen: The halogen whose offset and ratio the twin sits at.
    :param incumbent: The search's halogen-free reading of the peak, as its
        row stood (``formula``, ``ion``, ``ionization_mechanism``); None for a
        peak the search left unexplained.
    """

    mz: float
    twin_mz: float
    ratio: float
    halogen: str
    incumbent: dict | None = None


def _is_reading(formula) -> bool:
    return isinstance(formula, str) and formula not in (
        UNTARGETED_NO_MATCH,
        UNTARGETED_IONIZATION,
    )


def _nearest(sorted_mzs: np.ndarray, target: float) -> int | None:
    """The index of the m/z nearest ``target`` within :data:`TWIN_PPM`."""
    index = int(np.searchsorted(sorted_mzs, target))
    best = None
    for candidate in (index - 1, index):
        if 0 <= candidate < len(sorted_mzs):
            off = abs(sorted_mzs[candidate] - target)
            if off <= target * TWIN_PPM * 1e-6 and (
                best is None or off < abs(sorted_mzs[best] - target)
            ):
                best = candidate
    return best


def _carries(label, elements: Iterable[str]) -> bool:
    """Whether an isotope label names a heavy isotope of one of ``elements``."""
    text = str(label or "")
    return any(element in text for element in elements) and text != "M0"


def find_twins(
    matches: pd.DataFrame,
    peaks: pd.DataFrame,
    *,
    halogens: Iterable[str],
    searched_mzs: Iterable[float],
    claimed_mzs: Iterable[float] = (),
) -> list[Twin]:
    """The searched peaks whose halogen twin sits unexplained above them.

    A peak is asked where the search searched it (``searched_mzs``) and either
    left it unexplained or committed a reading whose ion carries no chlorine or
    bromine; a reading with a nitrogen, sulfur or silicon line of its own
    committed (:data:`OWN_EVIDENCE_ELEMENTS`) is not asked, and nor is a
    silicon reading for chlorine, whose 30Si line sits 0.2 mDa from the 37Cl
    offset. Its twin is the peak nearest the exact offset within
    :data:`TWIN_PPM`, the nearer of the two halogens' where both are within
    reach, at one atom's ratio within :data:`TWIN_RATIO_FACTOR`, holding no
    reading of the search and no other pass's row, and not where another
    committed ion's line would sit (:data:`OTHER_ION_OFFSETS`).

    :param matches: The search's rows, one or more per peak of ``peaks``.
    :param peaks: The frame the search scored over: ``mz`` and ``intensity``.
    :param halogens: The halogens to read (:func:`twin_halogens`).
    :param searched_mzs: The m/z of the peaks the search enumerated.
    :param claimed_mzs: The m/z of the peaks another pass owns (Stage A, the
        pre-passes), which are neither asked nor a twin.
    :return: The twins, by ascending m/z.
    """
    halogens = tuple(halogens)
    if not halogens or matches.empty or peaks.empty:
        return []
    frame = peaks[["mz", "intensity"]].sort_values("mz")
    mzs = frame["mz"].to_numpy(dtype=float)
    heights = frame["intensity"].to_numpy(dtype=float)
    searched = {float(mz) for mz in searched_mzs}
    claimed = {float(mz) for mz in claimed_mzs}

    rows_at: dict[float, list[dict]] = {}
    for row in matches.to_dict("records"):
        rows_at.setdefault(float(row["mz"]), []).append(row)
    explained = {
        mz
        for mz, rows in rows_at.items()
        if any(_is_reading(r.get("formula")) for r in rows)
    }
    m0_mzs = np.array(
        sorted(
            mz
            for mz, rows in rows_at.items()
            if any(
                _is_reading(r.get("formula")) and r.get("isotope_label") == "M0"
                for r in rows
            )
        ),
        dtype=float,
    )
    own_lines: dict[tuple, list[str]] = {}
    for rows in rows_at.values():
        for row in rows:
            if _is_reading(row.get("formula")) and row.get("isotope_label") != "M0":
                key = (row.get("formula"), row.get("ionization_mechanism"))
                own_lines.setdefault(key, []).append(str(row.get("isotope_label")))

    def line_of_another(twin_mz: float, own_mz: float) -> bool:
        for offset in OTHER_ION_OFFSETS:
            index = _nearest(m0_mzs, twin_mz - offset)
            if index is not None and abs(m0_mzs[index] - own_mz) > 1e-6:
                return True
        return False

    twins = []
    for position, mz in enumerate(mzs):
        if mz not in searched or mz in claimed or not heights[position] > 0:
            continue
        readings = [
            r
            for r in rows_at.get(float(mz), [])
            if _is_reading(r.get("formula")) and r.get("isotope_label") == "M0"
        ]
        incumbent = readings[0] if readings else None
        if incumbent is None and float(mz) in explained:
            # The peak is some other reading's isotope line.
            continue
        if incumbent is not None:
            ion = element_counts(str(incumbent.get("ion") or "").rstrip("+-")) or {}
            if any(ion.get(halogen) for halogen in TWIN_HALOGENS):
                continue
            key = (incumbent.get("formula"), incumbent.get("ionization_mechanism"))
            if any(
                _carries(label, OWN_EVIDENCE_ELEMENTS)
                for label in own_lines.get(key, [])
            ):
                continue
        hits = []
        for halogen in halogens:
            if halogen == "Cl" and incumbent is not None and ion.get("Si"):
                continue
            target = float(mz) + TWIN_OFFSET[halogen]
            index = _nearest(mzs, target)
            if index is not None:
                hits.append((abs(mzs[index] - target), index, halogen))
        if not hits:
            continue
        _, index, halogen = min(hits)
        twin_mz = float(mzs[index])
        ratio = float(heights[index] / heights[position])
        expected = ONE_ATOM_RATIO[halogen]
        if not expected / TWIN_RATIO_FACTOR <= ratio <= expected * TWIN_RATIO_FACTOR:
            continue
        if twin_mz in explained or twin_mz in claimed:
            continue
        if line_of_another(twin_mz, float(mz)):
            continue
        twins.append(
            Twin(
                mz=float(mz),
                twin_mz=twin_mz,
                ratio=round(ratio, 4),
                halogen=halogen,
                incumbent=(
                    None
                    if incumbent is None
                    else {
                        "formula": incumbent.get("formula"),
                        "ion": incumbent.get("ion"),
                        "ionization_mechanism": incumbent.get("ionization_mechanism"),
                    }
                ),
            )
        )
    return twins


def twin_search_config(
    search_config: CompositionSearchConfig, halogen: str
) -> CompositionSearchConfig:
    """The run's search, over carbon, hydrogen, oxygen and one atom of the halogen.

    The run's own bounds on carbon, hydrogen and oxygen (:data:`TWIN_GRID_ELEMENTS`),
    and exactly one halogen: the twin's ratio is one atom's.

    :param search_config: The run's search configuration.
    :param halogen: The twin's halogen.
    :return: The re-read's configuration.
    """
    ranges = parse_element_ranges(search_config.element_count_ranges)
    grid = {
        element: bounds
        for element, bounds in ranges.items()
        if element in TWIN_GRID_ELEMENTS
    }
    grid[halogen] = (1, 1)
    return replace(search_config, element_count_ranges=format_element_ranges(grid))


def twin_heuristics(
    heuristics: HeuristicFilterConfig, halogen: str
) -> HeuristicFilterConfig:
    """The run's filter, with the halogen's raw ratio band opened.

    The default band holds a chlorine or bromine formula to one atom per
    twenty carbons (``config.DEFAULT_ELEMENTAL_RATIO_RANGE``), which no
    chlorobenzene meets. A twin is evidence that band was written without:
    the re-read opens the halogen to the common range of Kind & Fiehn 2007
    (:data:`TWIN_HALOGEN_RATIO_BAND`), and every other rule stands.

    :param heuristics: The run's filter configuration.
    :param halogen: The twin's halogen.
    :return: The re-read's filter configuration.
    """
    bands = dict(heuristics.carbon_element_ratio_range)
    bands[f"{halogen}/C"] = TWIN_HALOGEN_RATIO_BAND
    return replace(heuristics, carbon_element_ratio_range=bands)


@dataclass
class TwinReread:
    """What reading the twins changed.

    :param matches: The search's rows with each twin's reading in place of the
        peak's, its lines with it.
    :param records: The M0 m/z of each re-read peak -> what the run records on
        its row (``provenance.halogen_twin``).
    :param summary: What the run records under its search scope.
    """

    matches: pd.DataFrame
    records: dict[float, dict] = field(default_factory=dict)
    summary: dict = field(default_factory=dict)


def _reading_of(rows: pd.DataFrame, mz: float, halogen: str) -> dict | None:
    """The re-read's M0 reading of the peak, where it carries the halogen."""
    at = rows[(rows["mz"] == mz) & (rows["isotope_label"] == "M0")]
    for row in at.to_dict("records"):
        counts = element_counts(str(row.get("formula") or "")) or {}
        if _is_reading(row.get("formula")) and counts.get(halogen):
            return row
    return None


def reread_twins(
    matches: pd.DataFrame,
    peaks: pd.DataFrame,
    twins: list[Twin],
    *,
    search_config: CompositionSearchConfig,
    heuristics: HeuristicFilterConfig,
    scoring: PatternScoring | None,
    candidate_threshold: float,
    sigmas: float,
) -> TwinReread:
    """Read each twin's peak as its halogen's formula where that passes every gate.

    Each halogen's peaks are searched once (:func:`twin_search_config`,
    :func:`twin_heuristics`), over the whole frame as the run's search scored
    them, and the reading the search elects is the peak's only where:

    - it carries the halogen, and the run's context windows and structural
      rules admitted it (the search's filter);
    - its envelope claimed the twin as its own heavy-halogen line;
    - its evidence, fit times plausibility, reaches ``candidate_threshold``;
    - its mass error sits within ``sigmas`` of the sample's width from the
      sample's centre.

    It then replaces the peak's rows - the incumbent's, and the incumbent's
    lines with it - and the twin's, and its own lines join the frame's. A peak
    whose reading fails a gate keeps what it had.

    :param matches: The run's search rows.
    :param peaks: The frame the search scored over.
    :param twins: The twins :func:`find_twins` found.
    :param search_config: The run's search configuration.
    :param heuristics: The run's filter configuration.
    :param scoring: How the run's search scored the sample; None for the
        finder's defaults.
    :param candidate_threshold: The run's candidate bar on evidence.
    :param sigmas: How many of the sample's widths from its centre a reading
        may sit.
    :return: The rows, what was re-read and why, and a summary.
    """
    summary = {
        "twins": len(twins),
        "reread": 0,
        "replaced": 0,
        "proposed": 0,
        "by_halogen": {},
    }
    if not twins:
        return TwinReread(matches=matches, summary=summary)
    sigma = None if scoring is None else float(scoring.sigma_ppm)
    mu = 0.0 if scoring is None else float(scoring.mu_ppm)
    records: dict[float, dict] = {}
    for halogen in TWIN_HALOGENS:
        mine = [twin for twin in twins if twin.halogen == halogen]
        if not mine:
            continue
        found, _ = assign_compositions(
            peaks,
            twin_search_config(search_config, halogen),
            twin_heuristics(heuristics, halogen),
            targets=[twin.mz for twin in mine],
            scoring=scoring,
        )
        if found.empty or "isotope_label" not in found.columns:
            continue
        heavy = f"{HEAVY_ISOTOPE[halogen]}{halogen}"
        for twin in mine:
            reading = _reading_of(found, twin.mz, halogen)
            if reading is None:
                continue
            group = found[
                (found["formula"] == reading["formula"])
                & (found["ionization_mechanism"] == reading["ionization_mechanism"])
            ]
            twin_line = group[group["mz"] == twin.twin_mz]
            if twin_line.empty or not any(
                heavy in str(label) or str(label) == "M+2"
                for label in twin_line["isotope_label"]
            ):
                continue
            fit = float(reading.get("isotopic_pattern_score") or 0.0)
            plausibility = float(formula_plausibility(str(reading["formula"])))
            if fit * plausibility < candidate_threshold:
                continue
            error = reading.get("mz_error_ppm")
            if sigma is not None and (
                error is None or abs(float(error) - mu) > sigmas * sigma
            ):
                continue
            # The incumbent's rows, and everything at the peaks the reading
            # takes that explains nothing, leave; the reading's rows come in.
            drop = pd.Series(False, index=matches.index)
            if twin.incumbent is not None:
                drop |= (matches["formula"] == twin.incumbent["formula"]) & (
                    matches["ionization_mechanism"]
                    == twin.incumbent["ionization_mechanism"]
                )
            taken = set(group["mz"].astype(float))
            drop |= matches["mz"].astype(float).isin(taken) & ~matches["formula"].map(
                _is_reading
            )
            matches = pd.concat([matches[~drop], group], ignore_index=True)
            records[twin.mz] = {
                "halogen": halogen,
                "twin_mz": twin.twin_mz,
                "ratio": twin.ratio,
                "one_atom_ratio": round(ONE_ATOM_RATIO[halogen], 4),
                "replaced": twin.incumbent,
            }
            summary["reread"] += 1
            summary["replaced" if twin.incumbent else "proposed"] += 1
            summary["by_halogen"][halogen] = summary["by_halogen"].get(halogen, 0) + 1
    return TwinReread(matches=matches, records=records, summary=summary)


def record_twins(
    assignments: list[dict], records: dict[float, dict], max_alternatives: int
) -> int:
    """Name on each re-read row the twin it was read from and what it replaced.

    The row carries ``provenance.halogen_twin`` (the halogen, the twin's m/z,
    its ratio against one atom's, and the reading it replaced, if any), and the
    replaced reading leads its alternatives, as a reading a rival took from a
    list does.

    :param assignments: The stage's rows, modified in place.
    :param records: :attr:`TwinReread.records`.
    :param max_alternatives: The cap on a row's alternatives.
    :return: How many rows were named.
    """
    named = 0
    for row in assignments:
        if row.get("role") != "M0":
            continue
        record = records.get(float(row.get("sample_peak_mz") or 0.0))
        if record is None:
            continue
        row.setdefault("provenance", {})["halogen_twin"] = record
        replaced = record.get("replaced")
        if replaced:
            alternative = {
                "assigned_formula": replaced["formula"],
                "ion_formula": replaced["ion"],
                "plausibility": round(
                    float(formula_plausibility(str(replaced["formula"]))), 4
                ),
                "source": row.get("source"),
                "halogen_twin_replaced": True,
            }
            row["alternatives"] = cap_alternatives(
                row,
                [alternative, *(row.get("alternatives") or [])],
                max(max_alternatives, 1),
            )
        named += 1
    return named
