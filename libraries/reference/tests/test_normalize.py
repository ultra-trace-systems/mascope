"""Formula/mass canonicalization tests."""

import pytest

from mascope_reference.normalize import (
    canonical_formula,
    finalize,
    monoisotopic_mass,
)
from mascope_reference.record import ReferenceRecord


def _record(formula: str, **kw) -> ReferenceRecord:
    return ReferenceRecord(
        formula=formula,
        source="test",
        source_native_id="1",
        license="test",
        **kw,
    )


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("C6H12O6", "C6H12O6"),
        ("H2O", "H2O"),
        # Element order is normalized to Hill (C, H, then alphabetical).
        ("O2C1H4", "CH4O2"),
        # Isotope labels collapse to the base element, matching the de novo path.
        ("[13C]C5H12O6", "C6H12O6"),
    ],
)
def test_canonical_formula_normalizes(raw, expected):
    assert canonical_formula(raw) == expected


@pytest.mark.parametrize("raw", ["", "()", "123", "-", "+"])
def test_canonical_formula_rejects_unusable(raw):
    assert canonical_formula(raw) is None


@pytest.mark.parametrize(
    "raw",
    [
        # Placeholder cells routine in NORMAN suspect lists and hand-authored
        # CSVs. A partial parse is worse than no parse: 'Not available' and
        # 'None' both yield No (nobelium) at 259 Da, 'N/A' yields AN, and a
        # mass-window search then reports them as known compounds.
        "Not available",
        "None",
        "N/A",
        "n/a",
        "unknown",
        # Free text trailing a real formula must not be silently truncated to
        # the formula - the cell was not a formula.
        "C6H12O6 (anhydrous)",
        # Unbalanced brackets, and a symbol that tokenizes but is not an element.
        "C6H12O6)",
        "Xx2",
    ],
)
def test_canonical_formula_rejects_placeholders_and_free_text(raw):
    assert canonical_formula(raw) is None


@pytest.mark.parametrize("raw", ["C2H3O2-", "Ca+2", "NH4+", "SO4-2"])
def test_canonical_formula_rejects_charged_formulas(raw):
    # PubChem publishes charge-suffixed formulas for millions of ionic CIDs.
    # Stripping the charge would store the ion under a neutral formula at a
    # neutral mass, so Stage A would report an anion as a known neutral
    # compound. Mascope models an ion as a neutral formula plus an explicit
    # ionization mechanism, so the mirror only stores neutrals.
    assert canonical_formula(raw) is None


def test_monoisotopic_mass_matches_known_values():
    assert monoisotopic_mass("H2O") == pytest.approx(18.0106, abs=1e-3)
    assert monoisotopic_mass("C6H12O6") == pytest.approx(180.0634, abs=1e-3)


def test_monoisotopic_mass_unknown_element_is_none():
    # 'Xx' is not a real element; mass computation fails softly.
    assert monoisotopic_mass("Xx2") is None


def test_finalize_canonicalizes_and_computes_mass():
    result = finalize(_record("[13C]C5H12O6"))
    assert result is not None
    assert result.formula == "C6H12O6"
    assert result.monoisotopic_mass == pytest.approx(180.0634, abs=1e-3)


def test_finalize_drops_unusable_formula():
    assert finalize(_record("")) is None


def test_finalize_drops_unknown_element_despite_source_mass():
    # 'Xx' tokenizes like a symbol but is not an element. Such a row is dropped
    # rather than stored against the source's own mass: keeping it would put a
    # compound that does not exist into the known set Stage A matches every peak
    # against, at a mass that looks entirely plausible.
    record = _record("Xx2", monoisotopic_mass=123.45)
    assert finalize(record) is None
