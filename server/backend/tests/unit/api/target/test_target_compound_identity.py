"""Target compound structure identity: InChIKey, InChI and SMILES (FAIR I3).

The request models check each field's shape; the controllers check that the
InChI describes the compound's formula, through ``check_inchi_formula``. Both
are pinned here without a database; the controllers' use of the check, against
the formula a row will have, is in the integration tests.
"""

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from mascope_backend.api.controllers.target.compounds.target_compound_identity import (
    check_inchi_formula,
    formula_composition,
    inchi_composition,
)
from mascope_backend.api.models.target.compounds.target_compound_pydantic_model import (
    TargetCompoundBase,
    TargetCompoundUpdate,
)


GLUCOSE_INCHIKEY = "WQZGKKKJIJFFOK-GASJEMHNSA-N"
GLUCOSE_INCHI = (
    "InChI=1S/C6H12O6/c7-1-2-3(8)4(9)5(10)6(11)12-2/h2-11H,1H2/t2-,3-,4+,5-,6+/m1/s1"
)
SODIUM_ACETATE_INCHI = "InChI=1S/C2H4O2.Na/c1-2(3)4;/h1H3,(H,3,4);/q;+1/p-1"


@pytest.mark.parametrize("model", [TargetCompoundBase, TargetCompoundUpdate])
def test_an_identity_is_stripped_and_kept(model):
    compound = model(
        target_compound_id="c1",
        target_compound_formula="C6H12O6",
        inchikey=f" {GLUCOSE_INCHIKEY} ",
        inchi=f"{GLUCOSE_INCHI}\n",
        smiles=" OC[C@H]1OC(O)[C@H](O)[C@@H](O)[C@@H]1O ",
    )
    assert compound.inchikey == GLUCOSE_INCHIKEY
    assert compound.inchi == GLUCOSE_INCHI
    assert compound.smiles == "OC[C@H]1OC(O)[C@H](O)[C@@H](O)[C@@H]1O"


@pytest.mark.parametrize("model", [TargetCompoundBase, TargetCompoundUpdate])
def test_every_identity_field_is_optional(model):
    compound = model(target_compound_id="c1", target_compound_formula="C6H12O6")
    assert (compound.inchikey, compound.inchi, compound.smiles) == (None, None, None)


@pytest.mark.parametrize(
    "inchikey",
    [
        "wqzgkkkjijffok-gasjemhnsa-n",
        "WQZGKKKJIJFFOK-GASJEMHNSA",
        "WQZGKKKJIJFFOKGASJEMHNSAN",
        "WQZGKKKJIJFFO1-GASJEMHNSA-N",
        "WQZGKKKJIJFFOK-GASJEMHNSA-N-X",
        "",
    ],
)
@pytest.mark.parametrize("model", [TargetCompoundBase, TargetCompoundUpdate])
def test_a_malformed_inchikey_is_refused(model, inchikey):
    with pytest.raises(ValidationError, match="InChIKey"):
        model(
            target_compound_id="c1",
            target_compound_formula="C6H12O6",
            inchikey=inchikey,
        )


@pytest.mark.parametrize("inchi", ["1S/C6H12O6", "inchi=1S/CH4/h1H4", " ", ""])
@pytest.mark.parametrize("model", [TargetCompoundBase, TargetCompoundUpdate])
def test_an_inchi_must_start_with_its_prefix(model, inchi):
    with pytest.raises(ValidationError, match="InChI="):
        model(target_compound_id="c1", target_compound_formula="CH4", inchi=inchi)


@pytest.mark.parametrize("smiles", ["", "   ", "\t"])
@pytest.mark.parametrize("model", [TargetCompoundBase, TargetCompoundUpdate])
def test_an_empty_smiles_is_refused(model, smiles):
    with pytest.raises(ValidationError, match="SMILES"):
        model(target_compound_id="c1", target_compound_formula="CH4", smiles=smiles)


@pytest.mark.parametrize(
    ("inchi", "composition"),
    [
        (GLUCOSE_INCHI, {"C": 6, "H": 12, "O": 6}),
        ("InChI=1S/H2O/h1H2", {"H": 2, "O": 1}),
        # A salt: the neutral parent and the proton balance.
        (SODIUM_ACETATE_INCHI, {"C": 2, "H": 3, "O": 2, "Na": 1}),
        # A component count, and protons taken from two of them.
        ("InChI=1S/Ca.2ClH/h;2*1H/q+2;;/p-2", {"Ca": 1, "Cl": 2}),
        # A protonated species gains its proton.
        ("InChI=1S/H3N/h1H3/p+1", {"H": 4, "N": 1}),
        # Non-standard: the fixed-H section restates the formula, read once.
        ("InChI=1/CH2O2/c2-1-3/h1H,(H,2,3)/f/h2H", {"C": 1, "H": 2, "O": 2}),
        # A reconnected section restates the structure, its proton layer
        # included; only the main layer's balance counts.
        ("InChI=1/H3N/h1H3/p+1/rH3N/h1H3/p+1", {"H": 4, "N": 1}),
    ],
)
def test_the_inchi_formula_layer_is_read_as_a_composition(inchi, composition):
    assert inchi_composition(inchi) == composition


@pytest.mark.parametrize(
    "inchi", ["InChI=", "InChI=1S", "InChI=1S//", "InChI=1S/C6H12O6!/c", "InChI=1S/Zz"]
)
def test_an_inchi_without_a_readable_formula_layer_has_no_composition(inchi):
    assert inchi_composition(inchi) is None


@pytest.mark.parametrize(
    ("inchi", "formula"),
    [
        (GLUCOSE_INCHI, "C6H12O6"),
        # Order does not matter, nor parenthesised groups.
        (GLUCOSE_INCHI, "H12C6O6"),
        ("InChI=1S/C4H11N/c1-3-5-4-2/h5H,3-4H2,1-2H3", "(CH3CH2)2NH"),
        (SODIUM_ACETATE_INCHI, "C2H3NaO2"),
    ],
)
def test_an_inchi_of_the_formula_passes(inchi, formula):
    check_inchi_formula(inchi, formula)


def test_an_inchi_of_another_formula_is_refused_naming_both():
    with pytest.raises(HTTPException) as refused:
        check_inchi_formula(GLUCOSE_INCHI, "C6H14O6")
    assert refused.value.status_code == 422
    assert "C6H12O6" in refused.value.detail
    assert "C6H14O6" in refused.value.detail


def test_an_unreadable_inchi_is_refused():
    with pytest.raises(HTTPException) as refused:
        check_inchi_formula("InChI=1S//", "CH4")
    assert refused.value.status_code == 422
    assert "CH4" in refused.value.detail


@pytest.mark.parametrize(
    ("inchi", "formula"),
    [
        (None, "C6H12O6"),
        (GLUCOSE_INCHI, None),
        # A labelled reagent: no InChI formula layer can say ^N.
        ("InChI=1S/HNO3/c2-1(3)4/h(H,2,3,4)", "H^NO3"),
    ],
)
def test_nothing_is_checked_without_both_or_for_a_caret_isotope(inchi, formula):
    check_inchi_formula(inchi, formula)


def test_a_formula_composition_counts_a_bracket_isotope_as_its_element():
    assert formula_composition("[13C]C5H12O6") == {"C": 6, "H": 12, "O": 6}
    assert formula_composition("[13C]6H12O6") == {"C": 6, "H": 12, "O": 6}
    assert formula_composition("C6[2H]2H10O6") == {"C": 6, "H": 12, "O": 6}
    assert formula_composition("H^NO3") == {"H": 1, "^N": 1, "O": 3}
