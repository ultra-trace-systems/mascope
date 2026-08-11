"""Canonicalization of reference formulas and masses.

Reference formulas must land in the exact same canonical Hill order as
de novo assigned formulas, or annotation lookups silently miss. Both paths
therefore share ``mascope_tools.composition``: the de novo cheminfo service
canonicalizes with ``to_hill_order(parse_composition(...))`` over an
isotope-normalized formula, and this module does the same, so a reference row
and an assigned result key on identical strings.

Source formula columns are far dirtier than assigned formulas, so this module is
also the gate that decides what counts as a formula at all. It is strict on
purpose: a rejected row costs one missing annotation, while a wrongly accepted
row puts a compound that does not exist into the known set Stage A matches every
peak against.
"""

import re

from mascope_reference.record import ReferenceRecord
from mascope_tools.composition.utils import (
    assert_valid_formula,
    composition_mass,
    normalize_formula_with_isotopes,
    parse_composition,
    to_hill_order,
)


# ``to_hill_order`` returns this sentinel for an empty/unparseable composition.
_EMPTY_FORMULA = "()"

# Every character of a source formula must belong to an element symbol, a
# bracketed isotope, a parenthesized group, or a count. ``parse_composition``
# silently skips characters it does not recognise, so without a full-coverage
# check a placeholder cell is read as whatever elements happen to fall out of
# it: 'None' and 'Not available' both yield No (nobelium, 259 Da), 'N/A' yields
# AN, 'C6H12O6 (anhydrous)' yields C6H12O6. Such placeholders are routine in
# NORMAN suspect-list exports and in hand-authored CSVs.
_FORMULA_TOKENS = re.compile(r"(?:(?:\[\d+[A-Z][a-z]?\]|\^?[A-Z][a-z]?|[()])\d*)+")


def canonical_formula(formula: str) -> str | None:
    """Canonicalize a neutral source formula to Hill order.

    Returns ``None`` for anything that is not unambiguously a neutral molecular
    formula, so the caller drops the record rather than mirroring a fabricated
    annotation key. Rejected, in rough order of how often they occur:

    * **Free-text and placeholder cells** - ``Not available``, ``None``,
      ``N/A``, ``C6H12O6 (anhydrous)``. These are dangerous rather than merely
      useless: a partial parse yields a real-looking formula at a real mass, and
      a mass-window search then reports it as a known compound.
    * **Charge-suffixed ionic formulas** - ``C2H3O2-``, ``Ca+2``, ``NH4+``,
      which PubChem publishes for millions of ionic CIDs. They are rejected
      rather than neutralized by adjusting the hydrogen count: that would store
      the conjugate acid's formula under the ion's name and InChIKey, and
      Mascope already models a detected ion as a neutral formula plus an
      explicit ionization mechanism, so an ion has no meaning in this mirror.
      Sources are expected to supply the neutral formula.
    * **Formulas with no element at all** (an R-group placeholder, a lone
      charge) or carrying a symbol that is not a real element.

    Isotope labels collapse to their base element, matching the de novo path
    which never emits isotope labels in an assigned neutral formula.

    :param formula: Raw formula string from a source dump.
    :return: Canonical Hill-order formula, or ``None`` if it is not usable.
    """
    if not formula:
        return None
    candidate = normalize_formula_with_isotopes(formula.strip())
    if not _FORMULA_TOKENS.fullmatch(candidate):
        return None
    try:
        # The shared validator adds what token coverage cannot see: unbalanced
        # brackets, and symbols that tokenize cleanly but are not elements ('Xx').
        assert_valid_formula(candidate)
        hill = to_hill_order(parse_composition(candidate))
    except Exception:
        return None
    if hill == _EMPTY_FORMULA or not hill:
        return None
    return hill


def monoisotopic_mass(formula: str) -> float | None:
    """Monoisotopic mass of a neutral formula, or ``None`` if it cannot be massed.

    Computed from the (already canonical) formula rather than trusting a
    source-provided mass, so the value is always consistent with the formula
    Mascope indexes and matches against. Elements unknown to the underlying
    mass table (or malformed formulas) yield ``None`` instead of raising.

    :param formula: Neutral formula string.
    :return: Monoisotopic mass, or ``None`` on failure.
    """
    if not formula:
        return None
    try:
        mass = composition_mass(parse_composition(formula))
    except Exception:
        return None
    return mass if mass > 0 else None


def finalize(record: ReferenceRecord) -> ReferenceRecord | None:
    """Canonicalize a record's formula and (re)compute its monoisotopic mass.

    Returns a new record with ``formula`` in canonical Hill order and
    ``monoisotopic_mass`` computed from it. Returns ``None`` when the source
    formula is not a usable neutral formula (see :func:`canonical_formula`) or
    cannot be massed - such rows carry no annotation key Stage A could match and
    are dropped at the ingestion boundary.

    :param record: Adapter-emitted record carrying a raw source formula.
    :return: Finalized record, or ``None`` if the formula is unusable.
    """
    canonical = canonical_formula(record.formula)
    if canonical is None:
        return None
    computed = monoisotopic_mass(canonical)
    if computed is None:
        # A validated formula always masses, so this is a guard rather than a
        # path. A source-provided mass is deliberately not used as a fallback:
        # every stored mass is the one computed from the stored formula, and a
        # row that cannot be massed is invisible to mass-window search and to
        # isotopologue expansion anyway.
        return None
    return record.model_copy(
        update={"formula": canonical, "monoisotopic_mass": computed}
    )
