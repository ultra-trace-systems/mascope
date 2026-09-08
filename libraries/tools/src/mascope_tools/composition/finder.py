"""Based on https://github.com/cheminfo/chemcalc"""

import re
import warnings
from typing import Iterator, Sequence

import numpy as np
import pandas as pd
import polars as pl
from pyteomics.mass import Composition

from mascope_tools.composition import utils
from mascope_tools.composition.config import UNSATURATION_COEFFICIENTS
from mascope_tools.composition.exceptions import (
    CompositionFinderWarning,
)
from mascope_tools.composition.grid import (
    DEFAULT_MAX_GRID_ROWS,
    NeutralGrid,
    build_neutral_grid,
)
from mascope_tools.composition.heuristic_filter import (
    SAME_ION_ALTERNATIVES,
    apply_heuristic_rules,
    match_isotopic_pattern,
)
from mascope_tools.composition.models import (
    Atom,
    CompositionSearchConfig,
    HeuristicFilterConfig,
    IonizationMechanism,
    Result,
)


def _is_notebook():
    try:
        from IPython import get_ipython

        shell = get_ipython().__class__.__name__
        return shell == "ZMQInteractiveShell"
    except Exception:
        return False


def _other_candidate_formulas(
    comp_results: list[dict], chosen_formula: str | None = None
) -> str:
    """Format a peak's runner-up compositions, never including the chosen one.

    `other_candidates` is the shortlist an inspector shows beside a committed
    assignment, so the composition that WON the peak must not appear in it. It
    cannot be taken positionally: `comp_results` arrives in mass-error order,
    while the winner is whatever survives `apply_heuristic_rules` and then ranks
    first on `match_isotopic_pattern`'s isotope-pattern score - routinely not
    `comp_results[0]`. Dropping index 0 therefore listed the winner as its own
    alternative and hid the mass-closest composition, which is exactly the
    runner-up worth seeing when the isotope pattern demoted it.

    :param comp_results: Every composition found for the peak's mass.
    :param chosen_formula: Formula that won the peak, excluded from the result;
        ``None`` when no candidate survived, where all of them stay open.
    :return: Comma-separated formulas, empty when there are no others.
    """
    return ", ".join(
        result["formula"]
        for result in comp_results
        if result["formula"] != chosen_formula
    )


def assign_compositions(
    peaks: pd.DataFrame,
    config: CompositionSearchConfig,
    heuristics: HeuristicFilterConfig | None = None,
    targets: Sequence[float] | None = None,
) -> tuple[pd.DataFrame, dict[float, list[str]]]:
    """Assign molecular compositions to a set of peaks.

    :param peaks: DataFrame with 'mz' and 'intensity' columns.
    :type peaks: pd.DataFrame
    :param config: Configuration parameters for the composition search.
    :type config: CompositionSearchConfig
    :param heuristics: Optional heuristic filter configuration.
    :type heuristics: HeuristicFilterConfig, optional
    :param targets: When given, only peaks whose m/z is in this list have
        compositions enumerated for them; every peak in ``peaks`` still
        serves as isotope-pattern context, and every peak still gets a row
        in the result. Values must be the frame's own m/z values. This is
        what lets a caller search a few peaks of a spectrum at the cost of
        those few, with the pattern scored against the whole spectrum.
    :type targets: Sequence[float], optional
    :return: A DataFrame with assigned compositions and related information.
        An M0 row whose ion could also be read as a different neutral/adduct
        pair carries those readings under ``same_ion_alternatives``: the
        composition, ion and mechanism of each, the same peak explained the
        same well by a different split. The column is absent when no peak had
        such a family, and null on the rows that did not.
    :rtype: tuple[pd.DataFrame, dict[float, list[str]]]
    """
    # Convert peaks to Polars DataFrame
    peaks_df = pl.from_pandas(peaks).sort("mz")
    peaks_to_match = peaks_df.filter(
        pl.col("intensity") >= config.peak_height_threshold
    )
    if peaks_to_match.is_empty():
        warnings.warn(
            "No peaks above the intensity threshold. Returning empty results.",
            CompositionFinderWarning,
        )
        peaks_df = peaks_df.with_columns(
            formula=pl.lit("---"),
            ion=pl.lit("---"),
            isotope_label=pl.lit("---"),
            other_candidates=pl.lit(""),
        )
        return peaks_df.to_pandas(), {}

    peaks_to_match = peaks_to_match.sort("mz")

    # Every peak above the threshold gets a row; the targets, when given, are
    # what is enumerated, not what is reported.
    reported_mzs = peaks_to_match["mz"].to_numpy()
    if targets is not None:
        peaks_to_match = peaks_to_match.filter(pl.col("mz").is_in(list(targets)))
    mzs = peaks_to_match["mz"].to_numpy()
    results_per_peak, assigned_mzs, mass_log_messages = [], set(), {}

    # One grid serves many targets, because the compositions the element box
    # allows are the same set for all of them and only the window into it moves.
    # Not one grid for the whole spectrum, though: a wide box over a TOF's
    # thousand-dalton range holds millions of compositions, so the targets are
    # walked in ascending mass bands and each band's grid is dropped when the
    # next begins. See :func:`_grids_for_targets`.
    mechanisms = [
        utils.parse_ionization(name)
        for name in get_ionization_mech_string_list(config.ionizations)
    ]

    for mz, grid in _grids_for_targets(mzs, config, mechanisms):
        if mz in assigned_mzs:
            continue

        comp_results = find_compositions(mz, config, grid=grid)

        if comp_results:
            candidates, log_messages = apply_heuristic_rules(
                comp_results, heuristics_config=heuristics
            )
            mass_log_messages[mz] = log_messages
            if candidates:
                candidates, all_matched_isotopes = match_isotopic_pattern(
                    candidates, peaks_df
                )
            else:
                all_matched_isotopes = []
            if not candidates:
                results_per_peak.append(
                    {
                        "formula": "---",
                        "ion": "---",
                        "mz": mz,
                        "other_candidates": _other_candidate_formulas(comp_results),
                        "isotope_label": "---",
                    }
                )
                continue
            main_candidate = candidates[0].copy()
            main_candidate["mz"] = mz
            main_candidate["formula"] = main_candidate.get("formula", "---")
            main_candidate["other_candidates"] = _other_candidate_formulas(
                comp_results, main_candidate["formula"]
            )

            if all_matched_isotopes:
                all_matched_isotopes = [
                    m for m in all_matched_isotopes if len(m.get("masses", [])) > 0
                ]
            if all_matched_isotopes and not _pattern_is_evidence(candidates[0]):
                # The best candidate's envelope was predicted, matched against
                # the spectrum and came out worth nothing - a line the
                # prediction requires is not there (see
                # `heuristic_filter.score_pattern`). Candidates are ranked by
                # that score, so no other reading of this peak does better
                # either. Committing the top one anyway is how a `+Br2-` phantom
                # takes a peak with no envelope at all: its monoisotopic line is
                # the target, so the row can always be written, and only the
                # score says it should not be.
                results_per_peak.append(
                    {
                        "formula": "---",
                        "ion": "---",
                        "mz": mz,
                        "other_candidates": _other_candidate_formulas(comp_results),
                        "isotope_label": "---",
                    }
                )
                continue
            if all_matched_isotopes:
                isotopic_results, assigned_mzs = process_isotopes(
                    main_candidate, all_matched_isotopes, assigned_mzs
                )
                results_per_peak.extend(isotopic_results)
            else:
                # No isotopic pattern matched, just add the main result
                main_candidate["isotope_label"] = "M0"
                results_per_peak.append(main_candidate)
                assigned_mzs.add(main_candidate["mz"])

    unmatched_peaks = set(reported_mzs) - assigned_mzs
    for mz in unmatched_peaks:
        results_per_peak.append(
            {
                "mz": mz,
                "formula": "---",
                "ion": "---",
                "isotope_label": "---",
                "other_candidates": "",
            }
        )

    matches = pd.DataFrame(results_per_peak)
    # --- Format results --- #
    # One row per peak, so two candidates that both explain it are cut down to
    # one here. Which one survives decides more than which formula is reported:
    # a candidate is a whole envelope, and dropping its monoisotopic row leaves
    # the satellites it named belonging to nothing. So a row that IS somebody's
    # monoisotopic line outranks another candidate's isotopologue for the same
    # peak, and only then does mass error decide.
    #
    # A guard rather than a live rule. The loop above claims each row's m/z as
    # it emits it and skips an m/z already claimed, so on real input the
    # collision this settles does not arise; it is here because nothing in the
    # loop's structure PROMISES that, and the cost of being wrong is an
    # envelope's satellites outliving it. mz_error_ppm is signed, so rank on
    # its magnitude: the row kept has to be the closest match, not the one
    # furthest BELOW its prediction.
    sort_by = [c for c in ["mz"] if c in matches.columns]
    if "isotope_label" in matches.columns:
        matches = matches.assign(_not_m0=matches["isotope_label"] != "M0")
        sort_by.append("_not_m0")
    if "mz_error_ppm" in matches.columns:
        matches = matches.assign(_mz_error_abs=matches["mz_error_ppm"].abs())
        sort_by.append("_mz_error_abs")
    matches = matches.sort_values(by=sort_by)
    # Drop duplicate m/z entries, keeping the closest match
    matches = matches.drop_duplicates(subset=["mz"], keep="first")
    matches = matches.drop(columns=["_not_m0", "_mz_error_abs"], errors="ignore")
    matches = sort_matches_by_formula(matches)
    # Add isotope label to ion string
    matches = update_ion_with_isotope_label(matches)
    # Show mz, formula, ion, isotope_label and then all other columns
    first_columns = [
        c
        for c in ["mz", "formula", "ion", "isotope_label", "ionization_mechanism"]
        if c in matches.columns
    ]
    cols = first_columns + [col for col in matches.columns if col not in first_columns]
    matches = matches[cols].reset_index(drop=True)

    return matches, mass_log_messages


def find_compositions(
    target_mz: float,
    config: CompositionSearchConfig,
    grid: NeutralGrid | None = None,
) -> list[dict]:
    """Find molecular compositions whose ion lands on a target m/z.

    :param target_mz: The target m/z value for which to find compositions.
    :type target_mz: float
    :param config: Configuration parameters for the composition search.
    :type config: CompositionSearchConfig
    :param grid: A neutral grid already enumerated over a range covering this
        target, from :func:`grid.build_neutral_grid`. A caller searching many
        peaks of one spectrum builds it once and passes it here; without one a
        grid is built over this target's own window, which is the same walk the
        search used to make per peak. The grid must have been built from an
        equivalent config - it carries the element box and the unsaturation cut.
    :type grid: NeutralGrid, optional
    :return: A list of dictionaries containing composition results.
    :rtype: list[dict]
    """
    ionization_mech_string_list = get_ionization_mech_string_list(config.ionizations)
    mechanisms = [utils.parse_ionization(name) for name in ionization_mech_string_list]
    mz_tolerance_da = target_mz * config.mass_range_ppm * 1e-6

    if grid is None:
        grid = build_neutral_grid(
            config,
            *neutral_mass_bounds([target_mz], mechanisms, config.mass_range_ppm),
        )
    if grid is None:
        # Only reachable when a single target's own window overflows the row
        # bound, which takes an element box orders of magnitude wider than the
        # API's species cap allows. Nothing to search rather than a wrong answer.
        return []

    all_results: list[Result] = []

    for ionization_mechanism in mechanisms:
        # Ion shift: ion m/z = neutral_mass + ion_shift
        ion_shift = (
            ionization_mechanism.mass
            if ionization_mechanism.addition
            else -ionization_mechanism.mass
        )
        # Neutral mass that would give the target m/z with this ionization mechanism
        required_neutral_mass = target_mz - ion_shift

        # --- Ionization peak case: no analyte mass (neutral mass ~ 0) ---
        if abs(required_neutral_mass) <= mz_tolerance_da:
            ion_charge = "+" if ionization_mechanism.charge > 0 else "-"
            ion_formula = ionization_mechanism.formula + ion_charge
            # Signed, relative to the prediction; for an ionization peak the
            # prediction is the adduct's own m/z, ion_shift.
            compositions_error_ppm = (target_mz - ion_shift) / ion_shift * 1e6
            all_results.append(
                Result(
                    formula="()",
                    neutral_mass=0.0,
                    composition_error_ppm=compositions_error_ppm,
                    unsaturation=None,
                    ion=ion_formula,
                    ionization_mechanism=ionization_mechanism.mascope_notation,
                    observed_mass=target_mz,
                )
            )
            continue

        # --- Negative neutral mass case (ionization mechanism inapplicable) ---
        if required_neutral_mass <= 0:
            continue

        # --- Regular case: the grid rows whose mass is close enough --- #
        # Ranked before the row cap applies, so a target with more readings than
        # the cap allows keeps the closest ones rather than whichever the walk
        # reached first.
        rows = grid.window(required_neutral_mass, mz_tolerance_da)
        errors = [
            (
                abs(grid.mass[row] + ion_shift - target_mz),
                row,
            )
            for row in rows
        ]
        errors.sort()
        for _, row in errors[: config.max_result_rows]:
            neutral_mass = float(grid.mass[row])
            ion_mz = neutral_mass + ion_shift
            formula = utils.to_hill_order(grid.composition(row))
            ion_formula = utils.combine_counts_and_ionization(
                grid.pyteomics_composition(row), ionization_mechanism
            )
            # (observed - predicted)/predicted, signed: the targeted matcher's
            # match_mz_error convention. Dividing by the PREDICTION (not by the
            # observation) is what makes the consumers' recovery of the predicted
            # m/z, observed / (1 + error/1e6), exact.
            error_ppm = (target_mz - ion_mz) / ion_mz * 1e6
            all_results.append(
                Result(
                    formula=formula,
                    neutral_mass=neutral_mass,
                    composition_error_ppm=error_ppm,
                    unsaturation=(
                        None
                        if grid.unsaturation is None
                        else float(grid.unsaturation[row])
                    ),
                    ion=ion_formula,
                    ionization_mechanism=ionization_mechanism.mascope_notation,
                    observed_mass=target_mz,
                )
            )

    all_results.sort(key=lambda r: abs(r.composition_error_ppm))

    return [r.to_dict() for r in all_results]


#: The narrowest band worth building a shared grid for. A band this small that
#: still overflows the row bound means an element box no spectrum-wide grid can
#: hold, and the search falls back to a window per peak.
_MIN_BAND_DA = 1.0


def _grids_for_targets(
    target_mzs: np.ndarray,
    config: CompositionSearchConfig,
    mechanisms: Sequence[IonizationMechanism],
    max_rows: int = DEFAULT_MAX_GRID_ROWS,
) -> Iterator[tuple[float, NeutralGrid | None]]:
    """Pair each target with a grid covering it, rebuilding as the band advances.

    The targets arrive in ascending m/z, so a band is a contiguous run of them
    and one grid answers every target until the band ends. Bands rather than one
    grid because the element box is the same everywhere but the compositions it
    allows are not: a box wide enough for a TOF's whole mass range holds millions
    of them, and only the band being searched has to be resident.

    The band width is found by halving until it fits, and the width that worked
    is the first guess for the next band - so a spectrum pays the search for its
    density once rather than at every band. A grid of None means even one band
    would not fit, and the caller searches each peak's own window instead.

    :param target_mzs: The peaks to be searched, ascending.
    :param config: The search, whose element box the grid enumerates.
    :param mechanisms: The ionization mechanisms, which decide how far a target's
        m/z is from the neutral masses that could explain it.
    :param max_rows: The row bound a single band's grid must fit inside.
    :yield: Each target with the grid that covers it.
    """
    if target_mzs.size == 0:
        return
    spectrum_end = float(target_mzs[-1])
    grid: NeutralGrid | None = None
    band_end = float("-inf")
    band_width = spectrum_end - float(target_mzs[0])
    banded = True

    for value in target_mzs:
        mz = float(value)
        if banded and mz > band_end:
            wanted_end = min(spectrum_end, mz + band_width) if band_width else mz
            grid, band_end, band_width = _grid_for_band(
                config, mechanisms, mz, max(wanted_end, mz), max_rows
            )
            if grid is None:
                banded = False
                warnings.warn(
                    "The element ranges are too wide to enumerate over this "
                    "spectrum's mass range; searching each peak separately, "
                    "which is slower.",
                    CompositionFinderWarning,
                )
        yield mz, (grid if banded else None)


def _grid_for_band(
    config: CompositionSearchConfig,
    mechanisms: Sequence[IonizationMechanism],
    band_start: float,
    band_end: float,
    max_rows: int,
) -> tuple[NeutralGrid | None, float, float]:
    """Build the widest band starting here that fits inside the row bound.

    :return: The grid, the m/z its band reaches, and the band's width - which the
        caller carries forward as the first guess for the band after it.
    """
    while True:
        grid = build_neutral_grid(
            config,
            *neutral_mass_bounds(
                [band_start, band_end], mechanisms, config.mass_range_ppm
            ),
            max_rows=max_rows,
        )
        if grid is not None:
            return grid, band_end, band_end - band_start
        span = band_end - band_start
        if span <= _MIN_BAND_DA:
            return None, band_end, span
        band_end = band_start + span / 2


def neutral_mass_bounds(
    target_mzs: Sequence[float],
    mechanisms: Sequence[IonizationMechanism],
    mass_range_ppm: float,
) -> tuple[float, float]:
    """The neutral masses any of these targets could be, under any of these ions.

    The range a grid has to span to answer every one of the targets: the lightest
    neutral the heaviest-adding adduct leaves of the lightest peak, up to the
    heaviest neutral the heaviest-subtracting adduct leaves of the heaviest peak.

    :param target_mzs: The m/z values to be searched.
    :param mechanisms: The ionization mechanisms they are searched under.
    :param mass_range_ppm: The search window, which widens the bounds by its own
        tolerance at each end.
    :return: ``(mass_min, mass_max)``; the minimum is never below zero.
    """
    if not target_mzs or not mechanisms:
        return (0.0, -1.0)
    shifts = [
        mechanism.mass if mechanism.addition else -mechanism.mass
        for mechanism in mechanisms
    ]
    lowest_mz, highest_mz = min(target_mzs), max(target_mzs)
    tolerance = highest_mz * mass_range_ppm * 1e-6
    return (
        max(0.0, lowest_mz - max(shifts) - tolerance),
        highest_mz - min(shifts) + tolerance,
    )


def _pattern_is_evidence(candidate: dict) -> bool:
    """Whether a scored candidate's isotope pattern supports committing it.

    A zero here is not a weak match: `score_pattern` returns zero only when a
    line it requires is absent - the ion's own, or the one the prediction leads
    with - and every other outcome is a positive combination of mass, intensity
    and pattern terms.
    """
    return float(candidate.get("isotopic_pattern_score") or 0.0) > 0.0


def process_isotopes(
    main_candidate: dict, all_matched_isotopes: list, assigned_mzs: set
) -> tuple:
    """Process and add isotopic pattern results

    :param main_candidate: Most likely composition for the monoisotopic m/z and related data.
    :type main_candidate: dict
    :param all_matched_isotopes: List of matched isotopic patterns.
    :type all_matched_isotopes: list
    :param assigned_mzs: The m/z values that have already been assigned to a composition.
    :type assigned_mzs: set
    :return: A tuple containing:
        - List of results per peak including isotopic variants.
        - Updated set of assigned m/z values.
    :rtype: tuple
    """
    results_per_peak = []
    # Take the first matched isotopic pattern (best scoring)
    matched_isotopes = all_matched_isotopes[0]
    isotope_mzs = matched_isotopes["masses"]
    isotope_labels = matched_isotopes["labels"]
    isotope_pred_mzs = matched_isotopes["predicted_masses"]
    isotope_pred_ints = matched_isotopes["predicted_intensities"]
    isotope_mz_errors = matched_isotopes["mass_errors_ppm"]
    isotope_intensity_errors = matched_isotopes["intensity_errors"]
    if isotope_mzs[0] != 0:
        # Index 0 is the ion's own line, which is what the pattern's intensities
        # are relative to and, for a candidate the composition search proposed,
        # the peak it was enumerated for
        # (`heuristic_filter.anchor_on_monoisotopic`). Its label still comes
        # from its own configuration like every other row's, because a caller
        # may hand this function a pattern in any order; what index 0 means to
        # THIS function is the line the rest of the envelope hangs off, and a
        # pattern whose index 0 went unmatched has no envelope to write.
        base_mass = isotope_mzs[0]
        main_candidate["mz"] = base_mass
        main_candidate["observed_mass"] = base_mass
        main_candidate["predicted_mz"] = isotope_pred_mzs[0]
        main_candidate["predicted_intensity"] = isotope_pred_ints[0]
        main_candidate["isotope_label"] = isotope_labels[0]
        main_candidate["mz_error_ppm"] = isotope_mz_errors[0]
        main_candidate["intensity_error"] = isotope_intensity_errors[0]
        results_per_peak.append(main_candidate)
        assigned_mzs.add(base_mass)

        # Extract and process higher isotopes
        for idx in range(1, len(isotope_mzs)):
            iso_mz = isotope_mzs[idx]
            if iso_mz == 0:
                continue
            if iso_mz in assigned_mzs:
                continue
            iso_result = main_candidate.copy()
            # The same-ion family is a statement about how the ION was read, and
            # the M0 row is where that reading is committed; a satellite is
            # owned by it. Restating the family on every child would store the
            # same ambiguity once per isotopologue and invite an inspector to
            # resolve it in a place that cannot act on it.
            iso_result.pop(SAME_ION_ALTERNATIVES, None)
            iso_result["mz"] = iso_mz
            iso_result["observed_mass"] = iso_mz
            iso_result["isotope_label"] = isotope_labels[idx]
            iso_result["predicted_mz"] = isotope_pred_mzs[idx]
            iso_result["predicted_intensity"] = isotope_pred_ints[idx]
            iso_result["mz_error_ppm"] = isotope_mz_errors[idx]
            iso_result["intensity_error"] = isotope_intensity_errors[idx]
            iso_result["neutral_mass"] = iso_result["neutral_mass"] + (
                iso_mz - base_mass
            )
            results_per_peak.append(iso_result)
            assigned_mzs.add(iso_mz)

    return results_per_peak, assigned_mzs


def get_ionization_mech_string_list(ionizations: str) -> list[str]:
    """Get a list of ionizations from the params dictionary."""
    if ionizations:
        return [ionization for ionization in ionizations.split(",")]
    else:
        raise ValueError("No ionization mechanisms provided.")


def get_neutral_mass_and_ionization_mech(
    target_mass: float, ion: str
) -> tuple[float, IonizationMechanism | None]:
    if ion:
        ionization_mech = utils.parse_ionization(ion)
        if ionization_mech.addition:
            # If it's an addition, we subtract mass
            neutral_mass = target_mass - ionization_mech.mass
        else:
            # If it's a subtraction, we add mass
            neutral_mass = target_mass + ionization_mech.mass
        return neutral_mass, ionization_mech
    return target_mass, None


def get_unsaturation(atoms: list[Atom], counts: list[int]) -> float:
    """Calculate the unsaturation (double bond equivalents) of a molecular formula.

    Warns if an atom's unsaturation coefficient is not supported.

    :param atoms: Iterable of Atom objects representing the elements in the formula.
    :type atoms: list[Atom]
    :param counts: List of counts for each atom in the formula.
    :type counts: list[int]
    :return: Unsaturation value (double bond equivalents).
    :rtype: float
    """
    unsaturation_value = 0
    for i, atom in enumerate(atoms):
        coefficient = UNSATURATION_COEFFICIENTS.get(atom.symbol, 0)
        if atom.symbol not in UNSATURATION_COEFFICIENTS:
            warnings.warn(
                f"Unsaturation coefficient for '{atom.symbol}' not supported, using {coefficient}.",
                CompositionFinderWarning,
            )
        unsaturation_value += coefficient * counts[i]
    return (unsaturation_value + 2) / 2.0


def _formula_sort_key(formula: str) -> tuple[int, int, str]:
    """
    Generate a sorting key for a chemical formula based on atomic composition.
    Priority:
        0: Only C and H
        1: Only C, H, and O
        2: Only C, H, O, and N
        3: All other C-containing
        4: Non-carbon containing
    """
    try:
        atoms = set(Composition(formula=utils.to_pyteomics(formula)))
        if "C" not in atoms:
            return (4, len(atoms), formula)
        if atoms <= {"C", "H"}:
            return (0, len(atoms), formula)
        if atoms <= {"C", "H", "O"}:
            return (1, len(atoms), formula)
        if atoms <= {"C", "H", "O", "N"}:
            return (2, len(atoms), formula)
        return (3, len(atoms), formula)
    except Exception:
        return (5, 0, formula)  # Place invalid formulas at the end


def sort_matches_by_formula(matches: pd.DataFrame) -> pd.DataFrame:
    """Sort a DataFrame of chemical formulae by atomic composition:
    1. C,H only
    2. C,H,O only
    3. C,H,O,N only
    4. Other C-containing
    5. Non-carbon containing
    Within each group, sort by number of atoms, then lexicographically.

    :param matches: Dataframe with matched peaks
    :type matches: pd.DataFrame
    :return: Sorted matches
    :rtype: pd.DataFrame
    """
    sort_keys = matches["formula"].apply(_formula_sort_key)
    return (
        matches.assign(_sort_key=sort_keys)
        .sort_values("_sort_key")
        .drop("_sort_key", axis=1)
        .reset_index(drop=True)
    )


def replace_atom_with_isotope(ion_formula: str, isotope_label: str) -> str:
    """Replace atoms in ion formula with their corresponding isotopic labels.

    Examples:
        >>> replace_atom_with_isotope("C6H12O6+", "13C2")
        '[13C]2C4H12O6+'
        >>> replace_atom_with_isotope("C10H15N-", "15N")
        '[15N]C10H15-'
        >>> replace_atom_with_isotope("C5H5+", "13C+2H")
        '[13C][2H]C4H4+'
        >>> replace_atom_with_isotope("C3H7O2-", "M0")
        'C3H7O2-'

    :param ion_formula: Formula of the ion, Hill order, with a charge at the end.
    :type ion_formula: str
    :param isotope_label: Label of the isotope to replace, e.g. "13C", "13C+2H", "13C3".
    :type isotope_label: str
    :return: Modified ion formula with isotopes.
    :rtype: str
    """
    if not isinstance(isotope_label, str) or isotope_label in {"M0", "---", ""}:
        return ion_formula

    # Split multiple isotopes if present, wrap each in square brackets
    # e.g. "13C+2H" -> ["[13C]", "[2H]"]
    isotope_labels = [f"[{iso_label}]" for iso_label in isotope_label.split("+")]

    # Separate the charge at the end of the formula, if any
    ion_charge = ion_formula[-1] if ion_formula[-1] in "+-" else ""
    ion_formula = ion_formula[:-1] if ion_charge else ion_formula
    element_counts = Composition(formula=utils.to_pyteomics(ion_formula))

    new_formula_parts = []
    for iso in isotope_labels:
        # Match isotope label with optional count, e.g. [13C2]
        element_match = re.match(r"\[(\d+)([A-Z][a-z]*)(\d*)\]", iso)
        if not element_match:
            raise ValueError(f"Invalid isotope label: {iso}.")

        isotope_mass = element_match.group(1)
        isotope_element = element_match.group(2)
        isotope_count_str = element_match.group(3)
        isotope_count = int(isotope_count_str) if isotope_count_str else 1

        # Check if the isotope's element exists in the formula
        if (
            isotope_element not in element_counts
            or element_counts[isotope_element] < isotope_count
        ):
            raise ValueError(
                f"Isotope element not found in the formula: {isotope_element}"
            )

        # Decrement the count of the target element
        element_counts[isotope_element] -= isotope_count

        # Add the isotope label to the formula parts
        new_formula_parts.append(
            f"[{isotope_mass}{isotope_element}]{isotope_count_str}"
        )

    # Rebuild the formula string, in the notation it arrived in: the counting
    # above went through pyteomics, which spells a labelled reagent's atom
    # 'N[15]' where every other layer writes '^N'.
    for element in element_counts.keys():
        count = element_counts[element]
        symbol = utils.from_pyteomics_symbol(element)
        if count == 0:
            continue  # Skip elements with a count of zero
        elif count == 1:
            new_formula_parts.append(symbol)
        else:
            new_formula_parts.append(f"{symbol}{count}")

    # Append the charge and join everything into the final string
    return "".join(new_formula_parts) + ion_charge


def update_ion_with_isotope_label(matches: pd.DataFrame) -> pd.DataFrame:
    """Update ion formulas in the matches DataFrame by replacing atoms with isotopic labels.

    :param matches: Matches DataFrame with 'ion' and 'isotope_label' columns.
    :type matches: pd.DataFrame
    :return: Updated matches DataFrame with modified 'ion' formulas.
    :rtype: DataFrame
    """
    matches = matches.copy()
    modified_ion_labels = []
    for ion, isotope_label in zip(matches["ion"], matches["isotope_label"]):
        try:
            updated_ion = replace_atom_with_isotope(ion, isotope_label)
        except ValueError:
            # If replacement fails, prepend empty brackets to indicate an issue
            updated_ion = f"[]{ion}"
        modified_ion_labels.append(updated_ion)

    matches["ion"] = modified_ion_labels
    return matches
