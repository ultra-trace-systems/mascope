"""Target compound formula validation.

The pydantic models reject invalid formulas at the API boundary: mass-based
target compounds (a bare numeric mass instead of a chemical formula) are no
longer supported, and unparseable formulas (unknown elements, stray characters)
are rejected up front rather than silently producing a compound with no ions.

Explicit bracket isotope notation is rejected too, because ions and isotopes are
always generated from the formula - pinning an isotope on the compound asks for
a monoisotopic species where a full pattern is computed regardless. Caret
isotopes stay allowed: those name a labelled reagent, a different substance.
"""

import pytest
from pydantic import ValidationError

from mascope_backend.api.models.target.compounds.target_compound_pydantic_model import (
    TargetCompoundBase,
    TargetCompoundMatches,
    TargetCompoundUpdate,
)


@pytest.mark.parametrize(
    "formula",
    ["C6H12O6", "H2O", "CH4N2O", "H^NO3", "(HNO3)2", "()"],
)
def test_chemical_formulas_are_accepted(formula):
    assert TargetCompoundBase(target_compound_formula=formula)
    assert TargetCompoundMatches(target_compound_formula=formula)


@pytest.mark.parametrize(
    "mass_formula",
    ["136.1252", "60", "0", "  42.0 ", "1e3", "18.01056"],
)
def test_mass_only_formulas_are_rejected(mass_formula):
    # Base creation model
    with pytest.raises(ValidationError):
        TargetCompoundBase(target_compound_formula=mass_formula)
    # Match request model (inherits the validator)
    with pytest.raises(ValidationError):
        TargetCompoundMatches(target_compound_formula=mass_formula)


@pytest.mark.parametrize(
    "formula",
    ["NaN", "InN", "CoInS"],
)
def test_formulas_that_float_would_misparse_are_accepted(formula):
    # float("NaN"/"InN"...) parses "NaN" as not-a-number but the old float()
    # guard rejected "NaN" (a valid Na+N formula); the numeric-pattern guard
    # only rejects actual numeric masses.
    assert TargetCompoundBase(target_compound_formula=formula)


@pytest.mark.parametrize(
    "formula",
    ["Zz", "xyz", "^C", "H2O!", "C6H12O6;"],
)
def test_invalid_formulas_are_rejected(formula):
    # Ion generation skips unparseable formulas, so accepting one here would
    # create a compound that can never produce ions or matches.
    with pytest.raises(ValidationError):
        TargetCompoundBase(target_compound_formula=formula)
    with pytest.raises(ValidationError):
        TargetCompoundMatches(target_compound_formula=formula)
    with pytest.raises(ValidationError):
        TargetCompoundUpdate(target_compound_id="x", target_compound_formula=formula)


def test_update_model_rejects_mass_but_allows_none():
    # Formula is optional on update; None is allowed (formula left unchanged)
    assert TargetCompoundUpdate(target_compound_id="x").target_compound_formula is None
    with pytest.raises(ValidationError):
        TargetCompoundUpdate(target_compound_id="x", target_compound_formula="12.3")


@pytest.mark.parametrize(
    "formula",
    [
        "[13C]C5H12O6",
        "C[13]C5H12O6",
        "[15N]O3",
        "N[15]O3",
        "(H[15N]O3)2",
        "[18O]C2H4",
        "[2H]2O",
    ],
)
def test_bracket_isotope_formulas_are_rejected(formula):
    """Both accepted spellings of bracket notation, on every model."""
    with pytest.raises(ValidationError):
        TargetCompoundBase(target_compound_formula=formula)
    with pytest.raises(ValidationError):
        TargetCompoundMatches(target_compound_formula=formula)
    with pytest.raises(ValidationError):
        TargetCompoundUpdate(target_compound_id="x", target_compound_formula=formula)


@pytest.mark.parametrize(
    "formula",
    ["H^NO3", "(H^NO3)2", "^NHO3", "HO3^N", "C10H17NO11^N", "C10H15NO7H^NO3"],
)
def test_caret_isotope_reagents_are_still_accepted(formula):
    """Caret isotopes name a labelled reagent, not one isotopologue of a compound.

    These six are the shapes actually in production use across three servers
    (the 15N nitrate reagent behind the ``+^NO3-`` mechanism and its clusters);
    rejecting them alongside bracket notation would break live configuration.
    """
    assert TargetCompoundBase(target_compound_formula=formula)
    assert TargetCompoundMatches(target_compound_formula=formula)


def test_isotope_rejection_names_the_alternative():
    """The error has to tell the user what to do instead, not just say no."""
    with pytest.raises(ValidationError) as excinfo:
        TargetCompoundBase(target_compound_formula="[13C]C5H12O6")
    message = str(excinfo.value)
    assert "isotopes are generated from the formula" in message
    assert "^N" in message
