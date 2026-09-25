"""
Utility functions for cheminfo composition search.
"""

import re

from mascope_tools.composition.custom_elements import CUSTOM_ELEMENTS
from mascope_tools.composition.mechanism_notation import (
    mechanism_key,
    parse_mechanism,
)


def to_cheminfo_ionization_format(ionization: str) -> str:
    """
    Convert a Mascope ionization mechanism to composition finder ionization format.

    The mechanism is read in either notation: the standard adduct one
    (``[M-H]-``, the ion's charge last) or the legacy
    ``<operation><moiety><moiety charge>`` one (``-H+``); see
    :mod:`mascope_tools.composition.mechanism_notation`.

    The composition finder accepts ionizations in the format:
        <polarity>(<modification formula>)<modification operation>
    where:
    - "polarity" is the charge polarity of the resulting ion ("+" or "-")
    - "modification formula" is the chemical formula subtracted from or added to the parent molecule in the parentheses
    - "modification operation" is either "-1" for subtraction or "" (empty string) for addition.

    Examples how Mascope ionization mechanisms get converted:
    - "[M+H]+" (legacy "+H+") becomes "+(H)" (composition finder)
    - "[M+Cl]-" (legacy "+Cl-") becomes "-(Cl)" (composition finder)
    - "[M]+." (legacy "+") becomes "+()" (composition finder)
    - "[M-H]-" (legacy "-H+") becomes "-(H)-1" (composition finder)

    :param ionization: Ionization mechanism string in Mascope format
    :type ionization: str
    :raises MechanismNotationError: The mechanism is in neither notation.
    :return: Ionization string formatted for composition finder
    :rtype: str
    """
    parts = parse_mechanism(ionization)
    if parts.electron_transfer:
        # Special case of electron abstraction/addition
        return f"{parts.polarity}()"
    # Strip custom element notation from body for composition finder
    body, _ = to_explicit_isotope_format(parts.moiety)
    operation = "" if parts.addition else "-1"
    return f"{parts.polarity}({body}){operation}"


def to_custom_element_format(formula: str) -> str:
    """
    Convert explicit isotope notation in a formula to custom element notation.

    Explicit isotopes are denoted with square brackets, e.g., "[15N]" for Nitrogen-15.
    This function replaces such notations with custom element notation, e.g., "^N", if
    the custom element exists, and its main isotope matches the specified isotope.

    :param formula: String containing a chemical formula with explicit isotopes.
        e.g. "C6H12[15N]O6"
    :type formula: str
    :return: String with custom element notation.
        e.g. "C6H12^NO6"
    :rtype: str
    """
    pattern = r"\[(\d+)([A-Z][a-z]?)\]"

    def replace_isotope(match):
        element = match.group(2)
        custom_element = f"^{element}"
        labelled = CUSTOM_ELEMENTS.get(custom_element)
        # Replace with the custom element only if it exists and its labelled
        # (main) isotope mass number matches the explicit isotope in the formula.
        if labelled is not None and labelled.labelled_massnumber == int(match.group(1)):
            return custom_element
        # If no custom element found or mass number doesn't match, return original
        return match.group(0)

    result = re.sub(pattern, replace_isotope, formula)
    return result


def to_explicit_isotope_format(formula_ranges: str) -> str:
    """
    Convert custom element notation in formula ranges to explicit isotope notation.

    Custom elements are denoted with a caret (^) followed by the element symbol,
    e.g., "^N" for Nitrogen-15. This function replaces such notations with
    explicit isotope notation, e.g., "[15N]".

    :param formula_ranges: String containing element count ranges with custom elements.
        e.g. "C0-30 H0-40 O0-20 [13C]0-1 ^N0-1"
    :type formula_ranges: str
    :return: String with explicit isotope notation.
        e.g. "C0-30 H0-40 O0-20 [13C]0-1 [15N]0-1"
    :rtype: str
    """
    pattern = r"\^([A-Z][a-z]?)"
    replacements = {}

    def replace_custom_element(match):
        element = match.group(1)
        key = f"^{element}"
        # KeyError for an unknown custom element, matching the previous behaviour.
        labelled = CUSTOM_ELEMENTS[key]
        replaced_with = f"[{labelled.labelled_massnumber}{element}]"
        replacements[key] = replaced_with
        return replaced_with

    result = re.sub(pattern, replace_custom_element, formula_ranges)
    return result, replacements


def to_mascope_ion_mech(ionization: str, all_ionization_mechanisms: list) -> dict:
    """
    Convert composition finder ionization format back to Mascope format and find the matching mechanism.

    The composition finder returns ionizations in formats like:
    - "+(H)+" for protonation
    - "(-1)(H)-1" for deprotonation

    This function parses this format and finds the matching ionization mechanism in provided
    Mascope database ionization mechanisms.

    :param ionization: Ionization string in composition finder format
    :type ionization: str
    :param all_ionization_mechanisms: List of ionization mechanisms from the database
    :type all_ionization_mechanisms: List[IonizationMechanism]
    :return: Dictionary with ionization mechanism details
    :rtype: dict
    :raises ValueError: If the ionization format is invalid
    :raises IndexError: If no matching ionization mechanism is found
    """
    pattern = r"^(\(-1\)|\+)\((.*?)\)(-1)?$"
    match = re.search(pattern, ionization)

    if not match:
        raise ValueError(f"Invalid ionization format: {ionization}")

    polarity = "-" if match.group(1) == "(-1)" else "+"
    body = match.group(2) or ""
    operation = "-" if match.group(3) == "-1" else "+"

    # Remove explicit isotope notation from body
    body = to_custom_element_format(body)

    # For subtraction operations, the modification polarity is reversed relative to the resulting ion polarity
    mod_polarity = polarity if operation == "+" else ("-" if polarity == "+" else "+")

    # Reconstruct the Mascope ionization format
    ionization_str = f"{operation}{body}{mod_polarity}" if body else mod_polarity

    # Find matching mechanism in our database results all_ionization_mechanisms,
    # by mechanism rather than by spelling; will raise IndexError if not found
    wanted = mechanism_key(ionization_str)
    return [
        mech
        for mech in all_ionization_mechanisms
        if mechanism_key(mech.ionization_mechanism) == wanted
    ][0].to_dict()
