"""
Utility functions for cheminfo composition search.
"""

import re

from pyteomics.mass import calculate_mass

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


def explicit_isotope_line(formula: str) -> tuple[str, int] | None:
    """The isotopologue line a formula's bracketed isotopes name.

    A search whose formula range holds an explicit isotope (``[13C]0-1``) finds
    formulas carrying it, and the search reports each as its unlabelled
    compound read at the labelled mass - that compound's 13C line. This names
    the line the way an isotope label does, with its whole-unit offset from the
    compound's monoisotopic line.

    Only bracketed tokens count: a labelled reagent's isotope is a custom
    element (``^N``) by the time a formula gets here, a compound of its own
    rather than a line of another.

    :param formula: A formula, e.g. ``"[13C]2C4H12O6"``.
    :return: ``(label, offset)``, e.g. ``("13C2", 2)``; None when the formula
        holds no bracketed isotope.

    Examples
    --------
    >>> explicit_isotope_line("[13C]C5H12O6")
    ('13C', 1)
    >>> explicit_isotope_line("[13C]2[18O]C4H12O5")
    ('13C2+18O', 4)
    >>> explicit_isotope_line("C6H12O6") is None
    True
    """
    parts, offset = [], 0
    for mass_number, element, count in re.findall(
        r"\[(\d+)([A-Z][a-z]?)\](\d*)", formula
    ):
        n = int(count) if count else 1
        parts.append(f"{mass_number}{element}{n if n > 1 else ''}")
        offset += n * (int(mass_number) - round(calculate_mass(formula=element)))
    if not parts:
        return None
    return "+".join(parts), offset
