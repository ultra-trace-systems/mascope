"""
Functions for target ions and target isotopes generation.
"""

import re
from itertools import combinations_with_replacement
from itertools import product as cartesian_product
from math import comb

import numpy as np
from IsoSpecPy import IsoThreshold

from mascope_backend.api.models.target.compounds.target_compound_pydantic_model import (
    TargetCompoundBase,
)
from mascope_backend.db import (
    IonizationMechanism,
    TargetIon,
    TargetIsotope,
)
from mascope_backend.db.id import gen_id
from mascope_backend.runtime import runtime
from mascope_tools.composition.config import ELECTRON_MASS
from mascope_tools.composition.custom_elements import CUSTOM_ELEMENTS
from mascope_tools.composition.finder import replace_atom_with_isotope
from mascope_tools.composition.heuristic_filter import extract_isotope_labels
from mascope_tools.composition.mechanism_notation import (
    MechanismNotationError,
    MechanismParts,
    parse_mechanism,
)
from mascope_tools.composition.utils import (
    assert_valid_formula,
    parse_composition,
    parse_formula_tokens,
    to_hill_notation,
)


# Threshold for high resolution isotope peaks prediction
# We store "all" isotopes in the database, filter by abundance later if needed
ISOTOPE_ABUNDANCE_THRESHOLD = 0.00001  # 0.001 %
# Low/TOF resolution constant
RESOLUTION_LOW = 1e4


class SkipIonizationMechanism(Exception):
    """Exception to skip an ionization mechanism when generating target ions."""

    pass


class UnknownIonizationMechanism(Exception):
    """Exception for unknown ionization mechanisms."""

    pass


class UnknownCustomElement(Exception):
    """Exception for unknown custom elements in formulas."""

    pass


def charge_string(charge: int) -> str:
    """Get charge string (+/-) for an ion charge.

    :param charge: Ion charge (e.g. +1, -1, 0)
    :type charge: int
    :return: Charge string, either "+", "-", or "" for a neutral
    :rtype: str

    Examples
    --------
    >>> charge_string(1)
    '+'
    >>> charge_string(-1)
    '-'
    >>> charge_string(0)
    ''
    """
    if charge > 0:
        return "+"
    if charge < 0:
        return "-"
    return ""


def generate_target_ions_from_composition(
    target_compound: TargetCompoundBase,
    ionization_mechanisms: list[IonizationMechanism],
) -> tuple[list[TargetIon], list[TargetIsotope]]:
    """Generate target ions and isotopes based on target compound composition and given ionization mechanisms

    Combines the neutral formula with each ionization mechanism (formula addition/
    subtraction), potentially including custom elements (e.g. ^N). Predicts isotopic
    patterns using IsoSpecPy.

    :param target_compound: Target compound to use as a base for the ions
    :type target_compound: TargetCompoundBase
    :param ionization_mechanisms: List of ionization mechanisms to apply to the target compound
    :type ionization_mechanisms: list[IonizationMechanism]
    :return: 2-tuple of (list of ions (instances of TargetIon), list of isotopes (instances of TargetIsotope))
    :rtype: tuple
    """
    target_ions = []
    target_isotopes = []

    # generate and create ion records
    target_compound_formula = target_compound.target_compound_formula.rstrip()

    # parse_composition silently drops characters it does not recognise, so an
    # invalid formula (garbage like 'xyz', a leftover numeric mass, or an unknown
    # custom element) would otherwise yield an empty/partial composition and
    # produce bogus adduct-only ions - or make IsoSpecPy raise further down. The
    # retired molmass fork raised FormulaError here, which the caller treated as
    # "skip". Preserve that: reject the whole compound (no ions) up front. This
    # also guards existing (pre-validation) rows reached via create_target_ions.
    try:
        assert_valid_formula(target_compound_formula)
    except ValueError as e:
        # INFO: fires per row of a user-imported collection
        runtime.logger.info(
            f"Skipping target compound with invalid formula "
            f"'{target_compound_formula}': {e}"
        )
        return [], []

    for ionization_mechanism in ionization_mechanisms:
        mechanism = ionization_mechanism.ionization_mechanism

        try:
            parts = _read_mechanism(mechanism)
            compound_composition = _get_compound_composition(
                target_compound_formula, parts
            )
            ion_composition, ion_charge = _get_raw_ion(parts, compound_composition)
        except (SkipIonizationMechanism, ValueError) as e:
            runtime.logger.debug(
                f"Skipping ionization mechanism {mechanism} for compound {target_compound_formula}: {e}"
            )
            continue
        except UnknownIonizationMechanism as e:
            runtime.logger.info(
                f"Failed to parse ion formula for compound {target_compound_formula} "
                f"and mechanism {mechanism}: {e}"
            )
            # Try to create ions with other mechanisms
            continue

        # Ion formula (no charge) in classic Hill notation. Explicit isotopes are
        # folded into their base element (labelled '^X' elements are preserved).
        ion_formula = to_hill_notation(ion_composition)
        charge_str = charge_string(ion_charge)

        # construct and save ion row
        runtime.logger.debug(
            f"Generated ion formula {ion_formula}{charge_str} for compound "
            f"{target_compound_formula} with mechanism {mechanism}"
        )
        ion = TargetIon(
            target_ion_id=gen_id(16),
            target_compound_id=target_compound.target_compound_id,
            ionization_mechanism_id=ionization_mechanism.ionization_mechanism_id,
            target_ion_formula=ion_formula + charge_str,
            filter_params={},
        )
        target_ions.append(ion)

        predicted_isotopes = dict()
        # Predict high resolution isotopes
        predicted_isotopes["HIGH"] = predict_isotopes(ion_composition, ion_charge)
        # Group for low resolution
        predicted_isotopes["LOW"] = group_target_isotopes(
            *predicted_isotopes["HIGH"], RESOLUTION_LOW
        )
        # Store target isotopes
        for resolution, (masses, probs, formulae) in predicted_isotopes.items():
            target_isotopes.extend(
                [
                    TargetIsotope(
                        target_isotope_id=gen_id(16),
                        target_ion_id=ion.target_ion_id,
                        mz=mz,
                        relative_abundance=rel_abu,
                        resolution=resolution,
                        target_isotope_formula=form,
                    )
                    for mz, rel_abu, form in zip(masses, probs, formulae)
                ]
            )

    return target_ions, target_isotopes


def _read_mechanism(ionization_mechanism: str) -> MechanismParts:
    """Read a stored mechanism in either notation: ``[M+H]+`` or ``+H+``,
    ``[M-H]-`` or ``-H+`` (deprotonation), ``[M+Br]-`` or ``+Br-``, and
    electron transfer, ``[M]+.`` / ``[M]-.`` or ``+`` / ``-``.

    :raises UnknownIonizationMechanism: The text is neither notation - a row
        older validation let through, such as ``"++"``.
    """
    try:
        return parse_mechanism(ionization_mechanism)
    except MechanismNotationError as e:
        raise UnknownIonizationMechanism(str(e)) from e


def _composition_counts(formula: str) -> dict[str, int]:
    """Parse ``formula`` into a plain ``{symbol: count}`` dict, preserving labelled
    '^X' custom elements. Uses ``parse_composition`` (which handles parentheses and
    folds explicit isotopes into their base element) but returns a plain dict so
    that count arithmetic does not go through pyteomics' element re-parsing, which
    rejects the bare '^N' symbol."""
    return dict(parse_composition(formula))


def _combine_counts(
    compound_counts: dict[str, int], mechanism_counts: dict[str, int], *, add: bool
) -> dict[str, int]:
    """Add or subtract element counts, dropping symbols that reach zero."""
    combined = dict(compound_counts)
    for symbol, count in mechanism_counts.items():
        combined[symbol] = combined.get(symbol, 0) + (count if add else -count)
    return {symbol: count for symbol, count in combined.items() if count > 0}


def _get_compound_composition(
    target_compound_formula: str, parts: MechanismParts
) -> dict[str, int] | None:
    """Get the neutral compound composition, handling special cases.

    :param target_compound_formula: Target compound formula string
    :type target_compound_formula: str
    :param parts: The ionization mechanism, read (:func:`_read_mechanism`)
    :type parts: MechanismParts
    :raises SkipIonizationMechanism: If the ionization mechanism cannot be applied:
        - Electron transfer on empty formula
        - Abstraction from empty formula
        - Not enough atoms of a subtracted element
    :return: Compound composition as ``{symbol: count}``, or None for empty "()"
    :rtype: dict[str, int] | None
    """
    runtime.logger.debug(
        f"Processing compound formula '{target_compound_formula}' with ionization mechanism '{parts.standard}'"
    )
    subtraction = not parts.addition and not parts.electron_transfer

    # Handle the special case when generating ions for empty formula "()"
    if target_compound_formula == "()":
        if parts.electron_transfer:
            # Electron transfer does not apply
            raise SkipIonizationMechanism(
                "Electron transfer does not apply to empty formula"
            )
        if subtraction:
            # Cannot subtract from empty formula
            raise SkipIonizationMechanism(
                "Subtraction mechanisms do not apply to empty formula"
            )
        return None

    compound_composition = _composition_counts(target_compound_formula)

    if subtraction:
        # For subtraction mechanisms, ensure the compound composition can support it
        mechanism_composition = _composition_counts(parts.moiety)
        for element, mech_count in mechanism_composition.items():
            # Check if element to be subtracted exists in compound composition
            if element not in compound_composition:
                raise SkipIonizationMechanism(
                    f"Element {element} from mechanism formula {to_hill_notation(mechanism_composition)} "
                    f"not in compound formula {to_hill_notation(compound_composition)}"
                )
            # Check if there are enough atoms of the element to be subtracted
            if mech_count > compound_composition[element]:
                raise SkipIonizationMechanism(
                    f"Cannot subtract {mech_count} of element {element} from compound formula "
                    f"{to_hill_notation(compound_composition)}"
                )

    return compound_composition


def _get_raw_ion(
    parts: MechanismParts, compound_composition: dict[str, int] | None
) -> tuple[dict[str, int], int]:
    """Get the ion composition and charge for a mechanism and compound composition.

    :param parts: The ionization mechanism, read (:func:`_read_mechanism`)
    :type parts: MechanismParts
    :param compound_composition: Neutral compound composition (None for empty "()")
    :type compound_composition: dict[str, int] | None
    :raises SkipIonizationMechanism: If the resulting ion has no atoms.
    :return: 2-tuple of (ion composition as ``{symbol: count}``, the ion's
        charge, which the mechanism states)
    :rtype: tuple[dict[str, int], int]
    """
    mechanism_composition = _composition_counts(parts.moiety)

    if parts.addition or parts.electron_transfer:
        # The moiety is added; electron transfer moves nothing but an electron,
        # so the ion keeps the compound's atoms whichever way it went.
        if compound_composition is None:
            # Special case: empty formula "()"
            ion_composition = mechanism_composition
        else:
            ion_composition = _combine_counts(
                compound_composition, mechanism_composition, add=True
            )
    else:
        ion_composition = _combine_counts(
            compound_composition, mechanism_composition, add=False
        )

    if not ion_composition:
        # E.g. an empty-modification mechanism on the empty compound "()", or a
        # subtraction that removes every atom. An atomless ion has no meaningful
        # formula or isotope pattern (its mass would be the electron mass alone).
        raise SkipIonizationMechanism(
            f"Mechanism {parts.standard} yields an ion with no atoms"
        )

    return ion_composition, parts.charge


def predict_isotopes(
    ion_composition: dict[str, int], ion_charge: int
) -> tuple[list[float], list[float], list[str]]:
    """Predicts isotope masses and abundances for a given ion using IsoSpecPy.

    Handles custom elements (e.g., ^N for isotopically labelled nitrogen) by:
    1. Computing isotope pattern for the non-custom part using IsoSpecPy
    2. Multiplying with custom element isotope distributions

    :param ion_composition: Composition of the ion as ``{symbol: count}`` (may
        contain '^X' custom elements)
    :type ion_composition: dict[str, int]
    :param ion_charge: Ion charge (e.g. +1, -1)
    :type ion_charge: int
    :raises UnknownCustomElement: If a custom element is unknown.
    :return: 3-tuple lists of (m/z values, relative abundances, isotope formulae)
    :rtype: tuple[list[float], list[float], list[str]]
    """
    custom_elements = _extract_custom_elements(ion_composition)
    base_composition = {
        symbol: count
        for symbol, count in ion_composition.items()
        if not symbol.startswith("^") and count > 0
    }
    base_formula = to_hill_notation(base_composition)

    # Compute isotope pattern for the base (non-custom) part
    base_masses, base_probs, base_labels = _compute_base_isotope_pattern(base_formula)

    # Combine with custom element isotope patterns if present
    if custom_elements:
        masses, probs, formulae = _combine_with_custom_elements(
            base_formula, base_masses, base_probs, base_labels, custom_elements
        )
    else:
        masses = base_masses
        probs = base_probs
        formulae = [
            replace_atom_with_isotope(base_formula, label) for label in base_labels
        ]

    # Correct masses for electron charge and add charge string to formulae
    masses = [(m - ELECTRON_MASS * ion_charge) / abs(ion_charge) for m in masses]
    charge_str = charge_string(ion_charge)
    formulae = [f + charge_str for f in formulae]

    return masses, probs, formulae


def _extract_custom_elements(ion_composition: dict[str, int]) -> dict[str, dict]:
    """Extract custom element data from an ion composition.

    Isotope masses and abundances come from the shared custom-element registry
    (mascope_tools.composition.custom_elements); the labelled-reagent purity sets
    the heavy/light isotope split.

    :param ion_composition: Ion composition to check for custom elements
    :return: Dict mapping custom element symbols to their properties
    :raises UnknownCustomElement: If a custom element is not in the registry
    """
    custom_elements = {}

    for symbol, count in ion_composition.items():
        if not symbol.startswith("^") or count <= 0:
            continue

        element = CUSTOM_ELEMENTS.get(symbol)
        if element is None:
            raise UnknownCustomElement(symbol)

        # Labelled distribution: heaviest isotope at `purity`, remainder split to
        # the lighter isotope(s). For the two-isotope elements defined today this
        # is simply (1 - purity) on the light isotope, `purity` on the heavy one.
        purity = element.default_purity
        abundances = [1.0 - purity] + [purity] * (len(element.isotopes) - 1)

        custom_elements[symbol] = {
            "count": count,
            "regular_symbol": element.base_element,
            "lightest_mass_number": element.isotopes[0][1],
            "isotopes": [
                {
                    "mass": mass,
                    "abundance": abundance,
                    "mass_number": mass_number,
                }
                for (mass, mass_number), abundance in zip(element.isotopes, abundances)
            ],
        }

    return custom_elements


def _compute_base_isotope_pattern(
    formula: str,
) -> tuple[list[float], list[float], list[str]]:
    """Compute isotope pattern for a formula using IsoSpecPy.

    :param formula: Chemical formula string (without custom elements)
    :return: 3-tuple of (masses, probabilities, isotope labels)
    """
    if not formula:
        # Empty formula (e.g., just custom elements with nothing else)
        return [0.0], [1.0], [""]

    predicted_peaks = IsoThreshold(
        formula=formula,
        threshold=ISOTOPE_ABUNDANCE_THRESHOLD,
        get_confs=True,
    )

    masses = [float(m) for m in predicted_peaks.masses]
    probs = [float(p) for p in predicted_peaks.probs]
    labels = extract_isotope_labels(formula, predicted_peaks)

    return masses, probs, labels


def _combine_with_custom_elements(
    base_formula: str,
    base_masses: list[float],
    base_probs: list[float],
    base_labels: list[str],
    custom_elements: dict[str, dict],
) -> tuple[list[float], list[float], list[str]]:
    """Multiply base isotope pattern with custom element isotope distributions.

    For custom elements with multiple atoms (e.g., ^N2), enumerates all isotope
    combinations following multinomial distribution. For multiple custom elements
    (e.g., ^H and ^N), computes the Cartesian product of their combinations.

    Duplicate formulas (from different isotope paths yielding same composition)
    are merged by summing probabilities.

    :param base_formula: Formula string for the base (non-custom) part
    :param base_masses: Base isotope masses
    :param base_probs: Base isotope probabilities
    :param base_labels: Base isotope labels (e.g., "M0", "17O")
    :param custom_elements: Dict of custom element data
    :return: 3-tuple of combined (masses, probabilities, formulae)
    """
    # Generate isotope combinations for each custom element
    custom_combinations = [
        _generate_isotope_combinations(custom_data)
        for custom_data in custom_elements.values()
    ]

    # Compute Cartesian product of all custom element combinations
    all_custom_combos = list(cartesian_product(*custom_combinations))

    # Use dict to merge duplicate formulas
    # Key: normalized formula, Value: (mass, accumulated_prob)
    formula_data: dict[str, tuple[float, float]] = {}

    for base_mass, base_prob, base_label in zip(base_masses, base_probs, base_labels):
        # Convert label to proper formula (e.g., "M0" -> "O3", "17O" -> "[17O]O2")
        base_isotope_formula = (
            replace_atom_with_isotope(base_formula, base_label) if base_formula else ""
        )

        for custom_combo in all_custom_combos:
            # Multiply all custom element contributions
            total_mass = base_mass + sum(c[0] for c in custom_combo)
            total_prob = base_prob
            for c in custom_combo:
                total_prob *= c[1]

            if total_prob < ISOTOPE_ABUNDANCE_THRESHOLD:
                continue

            # Concatenate custom element formulae
            custom_formula = "".join(c[2] for c in custom_combo)

            # Combine and normalize to merge elements (e.g. NN -> N2), then reorder
            # to put isotope labels at the front.
            raw_combined = custom_formula + base_isotope_formula
            normalized = _reorder_isotopes_first(
                to_hill_notation(parse_formula_tokens(raw_combined))
                if raw_combined
                else ""
            )

            # Merge duplicates by summing probabilities
            if normalized in formula_data:
                existing_mass, existing_prob = formula_data[normalized]
                # Use probability-weighted average mass (masses should be nearly identical)
                new_prob = existing_prob + total_prob
                new_mass = (
                    existing_mass * existing_prob + total_mass * total_prob
                ) / new_prob
                formula_data[normalized] = (new_mass, new_prob)
            else:
                formula_data[normalized] = (total_mass, total_prob)

    # Convert back to lists
    combined_formulae = list(formula_data.keys())
    combined_masses = [formula_data[f][0] for f in combined_formulae]
    combined_probs = [formula_data[f][1] for f in combined_formulae]

    return combined_masses, combined_probs, combined_formulae


def _reorder_isotopes_first(formula: str) -> str:
    """Reorder formula to put isotope labels (e.g., [15N], [18O]) at the beginning.

    :param formula: Chemical formula string
    :return: Formula with isotope labels moved to the front

    Examples
    --------
    >>> _reorder_isotopes_first("HN[15N]O6")
    '[15N]HNO6'
    >>> _reorder_isotopes_first("[18O]C2H4[15N]")
    '[18O][15N]C2H4'
    >>> _reorder_isotopes_first("C6H12O6")
    'C6H12O6'
    >>> _reorder_isotopes_first("")
    ''
    """
    if not formula or "[" not in formula:
        return formula

    # Extract all isotope labels like [15N], [18O], [2H], [15N]2, etc.
    isotope_pattern = r"\[\d+[A-Za-z]+\]\d*"
    isotopes = re.findall(isotope_pattern, formula)
    remaining = re.sub(isotope_pattern, "", formula)

    return "".join(isotopes) + remaining


def _generate_isotope_combinations(
    custom_data: dict,
) -> list[tuple[float, float, str]]:
    """Generate all isotope combinations for a custom element.

    For n atoms with k isotopes, generates all distributions following
    multinomial probability. E.g., ^N2 with 2% 14N / 98% 15N yields:
    - N2: 0.02**2 = 0.04%
    - [15N]N: 2 * 0.02 * 0.98 = 3.92%
    - [15N]2: 0.98**2 = 96.04%

    :param custom_data: Dict with 'count', 'regular_symbol', 'lightest_mass_number', 'isotopes'
    :return: List of (mass, probability, formula) tuples

    Examples
    --------
    Single atom with two isotopes (like ^N with 2% 14N / 98% 15N):

    >>> data = {
    ...     "count": 1,
    ...     "regular_symbol": "N",
    ...     "lightest_mass_number": 14,
    ...     "isotopes": [
    ...         {"mass": 14.003, "abundance": 0.02, "mass_number": 14},
    ...         {"mass": 15.000, "abundance": 0.98, "mass_number": 15},
    ...     ],
    ... }
    >>> result = _generate_isotope_combinations(data)
    >>> len(result)
    2
    >>> result[0]  # Light isotope (14N)
    (14.003, 0.02, 'N')
    >>> result[1]  # Heavy isotope (15N)
    (15.0, 0.98, '[15N]')

    Two atoms - multinomial distribution (^N2):

    >>> data2 = {
    ...     "count": 2,
    ...     "regular_symbol": "N",
    ...     "lightest_mass_number": 14,
    ...     "isotopes": [
    ...         {"mass": 14.003, "abundance": 0.02, "mass_number": 14},
    ...         {"mass": 15.000, "abundance": 0.98, "mass_number": 15},
    ...     ],
    ... }
    >>> result2 = _generate_isotope_combinations(data2)
    >>> len(result2)  # N2, [15N]N, [15N]2
    3
    >>> result2[0]  # N2: 0.02^2 = 0.0004
    (28.006, 0.0004, 'N2')
    >>> result2[1][1]  # [15N]N: 2 * 0.02 * 0.98 = 0.0392
    0.0392
    >>> result2[1][2]  # Formula shows heavy isotope first
    '[15N]N'
    >>> result2[2][0], round(result2[2][1], 4), result2[2][2]  # [15N]2: 0.98^2 = 0.9604
    (30.0, 0.9604, '[15N]2')
    """
    count = custom_data["count"]
    symbol = custom_data["regular_symbol"]
    isotopes = custom_data["isotopes"]
    lightest = custom_data["lightest_mass_number"]

    results = []
    for combo in combinations_with_replacement(range(len(isotopes)), count):
        # Count occurrences of each isotope index
        counts_per_isotope = {i: combo.count(i) for i in set(combo)}

        # Mass = sum of (isotope_mass × count)
        mass = sum(isotopes[i]["mass"] * c for i, c in counts_per_isotope.items())

        # Probability = multinomial_coeff × product(abundance^count)
        prob = _multinomial_coeff(count, list(counts_per_isotope.values()))
        for i, c in counts_per_isotope.items():
            prob *= isotopes[i]["abundance"] ** c

        # Build formula: heavy isotopes first (descending mass), then light
        formula_parts = []
        for i, c in sorted(
            counts_per_isotope.items(),
            key=lambda x: isotopes[x[0]]["mass_number"],
            reverse=True,
        ):
            if c == 0:
                continue
            mass_num = isotopes[i]["mass_number"]
            count_str = str(c) if c > 1 else ""
            if mass_num == lightest:
                formula_parts.append(f"{symbol}{count_str}")
            else:
                formula_parts.append(f"[{mass_num}{symbol}]{count_str}")

        results.append((mass, prob, "".join(formula_parts)))

    return results


def _multinomial_coeff(n: int, counts: list[int]) -> int:
    """Calculate multinomial coefficient n! / (k1! * k2! * ...).

    Examples
    --------
    >>> _multinomial_coeff(4, [2, 2])  # 4! / (2! * 2!) = 6
    6
    >>> _multinomial_coeff(3, [1, 1, 1])  # 3! / (1! * 1! * 1!) = 6
    6
    >>> _multinomial_coeff(5, [3, 2])  # 5! / (3! * 2!) = 10
    10
    """
    result = 1
    remaining = n
    for k in counts:
        result *= comb(remaining, k)
        remaining -= k
    return result


def group_target_isotopes(
    masses: list, probs: list, formulae: list, resolution: float
) -> tuple[list, list, list]:
    """
    Group target isotope m/z, relative abundance (probs), and isotope formulae
    to produce lower resolution isotopes.

    The width of the group/bin is defined as dmz = FWHM / 2 = m/z / resolution / 2.

    The isotope formulae are concatenated with "/" separator for all isotopes
    that fall within the same bin.

    :param masses: High resolution target isotope m/z
    :type masses: list
    :param probs: High resolution target isotope relative abundance
    :type probs: list
    :param formulae: High resolution target isotope formulae
    :type formulae: list
    :param resolution: Resolution value
    :type resolution: float
    :return: Tuple with lists of grouped "mz", "relative_abundance",
            "target_isotope_formula" values
    :rtype: tuple[list, list, list]
    """
    # Convert to numpy arrays to simplify computations
    mz = np.array(masses)
    intensity = np.array(probs)
    formula = np.array(formulae)

    # Sort by m/z to ensure proper binning
    sorted_indices = np.argsort(mz)
    mz = mz[sorted_indices]
    intensity = intensity[sorted_indices]
    formula = formula[sorted_indices]

    mz_grouped = []
    intensity_grouped = []
    formula_grouped = []
    i = 0
    while i < mz.size:
        # Calculate bin width for the current m/z
        dmz = mz[i] / resolution / 2

        # Determine bin size
        bin_mask = (mz >= mz[i]) & (mz < mz[i] + dmz)

        # A non-positive bin width (m/z <= 0, e.g. a degenerate atomless ion)
        # leaves the mask empty, which would never advance the loop; fall back
        # to a single-point bin so every isotope is emitted and i always moves.
        if not bin_mask.any():
            bin_mask = np.zeros(mz.size, dtype=bool)
            bin_mask[i] = True

        # Extract values within the current bin
        mz_bin = mz[bin_mask]
        intensity_bin = intensity[bin_mask]
        formula_bin = formula[bin_mask]

        # Final isotope intensity
        intensity_total = np.sum(intensity_bin)

        # Calculate center of mass for mz in the bin
        if intensity_total > 0:
            mz_bin_center = np.sum(mz_bin * intensity_bin) / intensity_total
        else:
            mz_bin_center = np.mean(mz_bin)

        # Store grouped values
        mz_grouped.append(mz_bin_center)
        intensity_grouped.append(intensity_total)
        formula_grouped.append("/".join(formula_bin.tolist()))

        # Move to the next bin, skipping all processed values
        i += np.sum(bin_mask)

    return mz_grouped, intensity_grouped, formula_grouped
