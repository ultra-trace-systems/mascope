"""
Utility functions for cheminfo composition search.
"""

import re

from mascope_tools.composition.custom_elements import CUSTOM_ELEMENTS


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
