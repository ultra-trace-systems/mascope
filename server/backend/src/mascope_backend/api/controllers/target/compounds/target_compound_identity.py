"""
The structure identity of a target compound: does its InChI describe its formula?

A compound may carry an InChIKey, an InChI and a SMILES string (FAIR I3). The
pydantic models check their shapes; this module checks the one thing a shape
cannot: that the InChI is the identity of a compound with the formula the row
has. It runs in the controllers, against the formula the row will have after
the write - the incoming formula when it changes, the stored one otherwise - so
an update that sends an InChI alone is checked against the formula it has to
agree with. A wrong identity is worse than none.
"""

import re
from collections import Counter

from fastapi import HTTPException, status

from mascope_tools.composition.utils import (
    assert_valid_formula,
    normalize_formula_with_isotopes,
    parse_composition,
    to_hill_notation,
)


# One component of an InChI's formula layer: a count of identical components,
# then their formula ("2ClH" in the layer "Ca.2ClH").
_COMPONENT = re.compile(r"(\d*)([A-Z][A-Za-z0-9]*)")


def inchi_composition(inchi: str) -> dict[str, int] | None:
    """The elemental composition an InChI describes, or None when it has none
    that can be read.

    The formula layer is the one after the version (``InChI=1S/<formula>/...``);
    its dot-separated components are summed, each times its count. The mobile
    protons of the ``/p`` layer are added to the hydrogen: standard InChI writes
    a salt as its neutral parent and a proton balance (sodium acetate is
    ``C2H4O2.Na`` with ``/p-1``), and the compound's formula is the salt's.
    Isotope labels (the ``/i`` layer) relabel atoms the formula already counts.
    A non-standard InChI's reconnected section (``/r``) restates the structure,
    proton layer included, so reading stops there; its fixed-hydrogen section
    (``/f``) has no proton layer to count twice.

    :param inchi: An InChI, ``InChI=`` included.
    :return: ``{element: count}`` with every count positive, or None.
    """
    layers = inchi.removeprefix("InChI=").split("/")
    if len(layers) < 2 or not layers[1]:
        return None
    counts: Counter[str] = Counter()
    for component in layers[1].split("."):
        match = _COMPONENT.fullmatch(component)
        if match is None:
            return None
        multiplier = int(match.group(1) or 1)
        try:
            assert_valid_formula(match.group(2))
        except ValueError:
            return None
        for element, count in parse_composition(match.group(2)).items():
            counts[element] += count * multiplier
    for layer in layers[2:]:
        if layer.startswith("r"):
            break
        if layer.startswith("p"):
            try:
                counts["H"] += int(layer[1:])
            except ValueError:
                return None
    return {element: count for element, count in counts.items() if count}


def formula_composition(formula: str) -> dict[str, int]:
    """The elemental composition of a compound formula, as an InChI counts it.

    A bracket isotope counts as its element, count included (``[13C]6H12O6``
    is C6H12O6), as the InChI's formula layer counts a labelled atom: the
    brackets are dropped before parsing, since ``parse_composition`` alone
    reads the count after a bracket as nothing. A caret isotope (``^N``) stays
    a symbol of its own, which no InChI or reference formula has.

    :param formula: A compound formula, parenthesised groups allowed.
    :return: ``{element: count}`` with every count positive.
    """
    return {
        element: count
        for element, count in parse_composition(
            normalize_formula_with_isotopes(formula)
        ).items()
        if count > 0
    }


def check_inchi_formula(inchi: str | None, formula: str | None) -> None:
    """Refuse an InChI whose formula is not the compound's.

    Compared as compositions, so the order the two are written in does not
    matter: the InChI's formula layer (with its proton balance) must contain
    exactly the atoms of ``formula``. Nothing to check without either one, and
    nothing for a formula with a caret isotope (``^N``): that names a labelled
    reagent, which an InChI's formula layer cannot express.

    :param inchi: The InChI the row will have.
    :param formula: The formula the row will have.
    :raises HTTPException: 422 naming both formulas, on a mismatch or an InChI
        whose formula layer cannot be read.
    """
    if not inchi or formula is None or "^" in formula:
        return
    described = inchi_composition(inchi)
    if described is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"The InChI '{inchi}' has no formula layer that can be read, so "
                f"it cannot be checked against the compound formula '{formula}'."
            ),
        )
    compound = formula_composition(formula)
    if described != compound:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"The InChI describes {to_hill_notation(described)} "
                f"(formula layer '{inchi.removeprefix('InChI=').split('/')[1]}'), "
                f"but the compound formula is '{formula}' "
                f"({to_hill_notation(compound) or 'no atoms'}). An identity "
                "must be the compound's own: send the InChI of this formula, or "
                "none."
            ),
        )
