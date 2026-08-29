"""Based on https://github.com/cheminfo/chemcalc"""

import re
import warnings
from math import ceil, floor
from typing import Iterator

import pandas as pd
import polars as pl
from pyteomics.mass import Composition

from mascope_tools.composition import utils
from mascope_tools.composition.config import UNSATURATION_COEFFICIENTS
from mascope_tools.composition.exceptions import (
    CompositionFinderWarning,
)
from mascope_tools.composition.heuristic_filter import (
    apply_heuristic_rules,
    match_isotopic_pattern,
)
from mascope_tools.composition.models import (
    Atom,
    CompositionSearchConfig,
    CompositionSearchState,
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
) -> tuple[pd.DataFrame, dict[float, list[str]]]:
    """Assign molecular compositions to a set of peaks.

    :param peaks: DataFrame with 'mz' and 'intensity' columns.
    :type peaks: pd.DataFrame
    :param config: Configuration parameters for the composition search.
    :type config: CompositionSearchConfig
    :param heuristics: Optional heuristic filter configuration.
    :type heuristics: HeuristicFilterConfig, optional
    :return: A DataFrame with assigned compositions and related information.
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

    mzs = peaks_to_match["mz"].to_numpy()
    results_per_peak, assigned_mzs, mass_log_messages = [], set(), {}

    for mz in mzs:
        if mz in assigned_mzs:
            continue

        comp_results = find_compositions(mz, config)

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

    unmatched_peaks = set(mzs) - assigned_mzs
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
    # mz_error_ppm is signed, so rank on its magnitude: the duplicate kept below has
    # to be the closest match, not the one furthest BELOW its prediction.
    sort_by = [c for c in ["mz"] if c in matches.columns]
    if "mz_error_ppm" in matches.columns:
        matches = matches.assign(_mz_error_abs=matches["mz_error_ppm"].abs())
        sort_by.append("_mz_error_abs")
    matches = matches.sort_values(by=sort_by)
    # Drop duplicate m/z entries, keeping the closest match
    matches = matches.drop_duplicates(subset=["mz"], keep="first")
    matches = matches.drop(columns="_mz_error_abs", errors="ignore")
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


def find_compositions(target_mz: float, config: CompositionSearchConfig) -> list[dict]:
    """Find molecular compositions based on the provided parameters.

    :param target_mz: The target m/z value for which to find compositions.
    :type target_mz: float
    :param config: Configuration parameters for the composition search.
    :type config: CompositionSearchConfig
    :return: A list of dictionaries containing composition results.
    :rtype: list[dict]
    """
    atoms = utils.parse_atom_count_ranges(config.element_count_ranges)
    atoms.sort(key=lambda a: a.mass, reverse=True)

    ionization_mech_string_list = get_ionization_mech_string_list(config.ionizations)
    mz_tolerance_da = target_mz * config.mass_range_ppm * 1e-6

    # Initialise list of results across all ionization mechanisms
    all_results: list[Result] = []

    # Precompute minimal and maximal remaining masses for pruning the search space
    min_inner_mass, max_inner_mass = calc_min_max_inner_mass(atoms)

    for ionization_mech_string in ionization_mech_string_list:
        # Reset number of found compositions for this ionization mechanism
        ionization_mechanism = utils.parse_ionization(ionization_mech_string)

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
            # Signed, relative to the prediction (see recursive_search); for an
            # ionization peak the prediction is the adduct's own m/z, ion_shift.
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

        # --- Regular case: search for matching compositions --- #
        # Initialize search runtime state per ionization mechanism
        state = CompositionSearchState(
            ion_shift=ion_shift,
            mz_tolerance_da=mz_tolerance_da,
            atoms=atoms,
            min_inner_mass=min_inner_mass,
            max_inner_mass=max_inner_mass,
            ionization_mechanism=ionization_mechanism,
        )

        for res in recursive_search(0, [], 0.0, target_mz, state, config):
            all_results.append(res)

    all_results.sort(key=lambda r: abs(r.composition_error_ppm))

    return [r.to_dict() for r in all_results]


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
        # Extract and process base peak (M0)
        m0_mass = isotope_mzs[0]
        main_candidate["mz"] = m0_mass
        main_candidate["observed_mass"] = m0_mass
        main_candidate["predicted_mz"] = isotope_pred_mzs[0]
        main_candidate["predicted_intensity"] = isotope_pred_ints[0]
        main_candidate["isotope_label"] = "M0"
        main_candidate["mz_error_ppm"] = isotope_mz_errors[0]
        main_candidate["intensity_error"] = isotope_intensity_errors[0]
        results_per_peak.append(main_candidate)
        assigned_mzs.add(m0_mass)

        # Extract and process higher isotopes
        for idx in range(1, len(isotope_mzs)):
            iso_mz = isotope_mzs[idx]
            if iso_mz == 0:
                continue
            if iso_mz in assigned_mzs:
                continue
            iso_result = main_candidate.copy()
            iso_result["mz"] = iso_mz
            iso_result["observed_mass"] = iso_mz
            iso_result["isotope_label"] = isotope_labels[idx]
            iso_result["predicted_mz"] = isotope_pred_mzs[idx]
            iso_result["predicted_intensity"] = isotope_pred_ints[idx]
            iso_result["mz_error_ppm"] = isotope_mz_errors[idx]
            iso_result["intensity_error"] = isotope_intensity_errors[idx]
            iso_result["neutral_mass"] = iso_result["neutral_mass"] + (iso_mz - m0_mass)
            results_per_peak.append(iso_result)
            assigned_mzs.add(iso_mz)

    return results_per_peak, assigned_mzs


def recursive_search(
    idx: int,
    counts: list,
    current_mass: float,
    target_mz: float,
    state: CompositionSearchState,
    config: CompositionSearchConfig,
) -> Iterator[Result]:
    """A recursive function to explore all possible combinations of atom counts.

    :param idx: Current index in the list of atoms.
    :type idx: int
    :param counts: Current counts of each atom type.
    :type counts: list
    :param current_mass: Current total mass of the composition based on counts.
    :type current_mass: float
    :param target_mz: The target m/z value for which to find compositions.
    :type target_mz: float
    :param state: Current state of the composition search.
    :type state: CompositionSearchState
    :param config: Configuration parameters for the composition search.
    :type config: CompositionSearchConfig
    :yield: Result objects for valid compositions.
    :rtype: Iterator[Result]
    """
    if state.results_found >= config.max_result_rows:
        return

    # Evaluate full composition
    if idx == len(state.atoms):
        ion_mz = current_mass + state.ion_shift
        if abs(ion_mz - target_mz) <= state.mz_tolerance_da:
            if config.use_unsaturation:
                unsat = get_unsaturation(state.atoms, counts)
                if not (config.min_unsaturation <= unsat <= config.max_unsaturation):
                    return
                if config.only_integer_unsaturation and not unsat.is_integer():
                    return
            else:
                unsat = None

            atomic_counts = {
                state.atoms[i].symbol: counts[i] for i in range(len(state.atoms))
            }
            formula = utils.to_hill_order(atomic_counts)
            state.results_found += 1
            ion_formula = utils.combine_formula_and_ionization(
                formula, state.ionization_mechanism
            )
            # (observed - predicted)/predicted, signed: the targeted matcher's
            # match_mz_error convention. Dividing by the PREDICTION (not by the
            # observation) is what makes the consumers' recovery of the predicted
            # m/z, observed / (1 + error/1e6), exact.
            error_ppm = (target_mz - ion_mz) / ion_mz * 1e6
            yield Result(
                formula=formula,
                neutral_mass=current_mass,
                composition_error_ppm=error_ppm,
                unsaturation=unsat,
                ion=ion_formula,
                ionization_mechanism=state.ionization_mechanism.mascope_notation,
                observed_mass=target_mz,
            )
        return

    atom = state.atoms[idx]
    min_inner = state.min_inner_mass[idx]
    max_inner = state.max_inner_mass[idx]
    tol = state.mz_tolerance_da
    shift = state.ion_shift

    # Feasible count bounds for this atom (neutral mass domain)
    feasible_min = max(
        atom.min_count,
        int(ceil(((target_mz - shift) - tol - current_mass - max_inner) / atom.mass))
        - 1,
    )
    feasible_max = min(
        atom.max_count,
        int(floor(((target_mz - shift) + tol - current_mass - min_inner) / atom.mass))
        + 1,
    )
    if feasible_min > feasible_max:
        return

    # Reuse a single counts buffer through recursion to avoid repeated list allocations.
    counts.append(0)
    try:
        for atom_count in range(feasible_min, feasible_max + 1):
            if state.results_found >= config.max_result_rows:
                return
            counts[-1] = atom_count
            new_mass = current_mass + atom_count * atom.mass

            if idx < len(state.atoms) - 1:
                min_mass = new_mass + min_inner
                max_mass = new_mass + max_inner
                min_ion = min_mass + shift
                max_ion = max_mass + shift

                # Too heavy already (even minimal remaining mass overshoots)
                if (min_ion - target_mz) > tol:
                    break
                # Still too light (even maximal remaining mass below window)
                if (target_mz - max_ion) > tol:
                    continue

            yield from recursive_search(
                idx + 1, counts, new_mass, target_mz, state, config
            )
    finally:
        counts.pop()


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


def calc_min_max_inner_mass(atoms) -> tuple[list[float], list[float]]:
    """Prepare suffix arrays of minimal and maximal remaining masses AFTER each index.

    Returns:
        min_suffix[i]: minimal mass contribution of atoms with index > i
        max_suffix[i]: maximal mass contribution of atoms with index > i
        For convenience lengths match len(atoms); min_suffix[-1] == max_suffix[-1] == 0.
    """
    n = len(atoms)
    min_suffix = [0.0] * n
    max_suffix = [0.0] * n
    running_min = 0.0
    running_max = 0.0
    # Build from the end toward the front; suffix after i
    for i in range(n - 1, -1, -1):
        min_suffix[i] = running_min
        max_suffix[i] = running_max
        running_min += atoms[i].min_count * atoms[i].mass
        running_max += atoms[i].max_count * atoms[i].mass
    return min_suffix, max_suffix


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

    # Rebuild the formula string
    for element in element_counts.keys():
        count = element_counts[element]
        if count == 0:
            continue  # Skip elements with a count of zero
        elif count == 1:
            new_formula_parts.append(element)
        else:
            new_formula_parts.append(f"{element}{count}")

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
