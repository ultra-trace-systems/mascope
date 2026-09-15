"""Ionization mechanism formula validation.

The mechanism modification formula is validated (strictly) via
mascope_tools.composition.utils.assert_valid_formula, which raises on invalid
characters and unknown elements rather than silently ignoring them.
"""

import pytest
from pydantic import ValidationError

from mascope_backend.api.models.ionization_mechanisms.ionization_mechanism_pydantic_model import (
    IonizationMechanismCreate,
    IonizationMechanismRead,
    IonizationMechanismUpdate,
)


@pytest.mark.parametrize(
    "mechanism",
    [
        "+H+",  # protonation
        "-H+",  # deprotonation
        "+Br-",  # bromide adduct
        "+NO3-",  # nitrate adduct
        "+^NO3-",  # 15N-labelled nitrate adduct (custom element)
        "+(CH4N2O)H+",  # parenthesised adduct
        "+",  # electron abstraction
        "-",  # electron capture
    ],
)
def test_valid_mechanisms_accepted(mechanism):
    assert IonizationMechanismCreate(ionization_mechanism=mechanism)


@pytest.mark.parametrize(
    "mechanism",
    [
        "+Zz+",  # unknown element
        "+H!+",  # invalid character
        "+(H+",  # unbalanced parenthesis
        "H+",  # missing leading operation
        "+H",  # missing trailing polarity
        "+-",  # invalid sign combination
        "++",  # empty modification formula (used to hang ion generation)
        "--",  # empty modification formula
    ],
)
def test_invalid_mechanisms_rejected(mechanism):
    with pytest.raises(ValidationError):
        IonizationMechanismCreate(ionization_mechanism=mechanism)


#: Rows a create would refuse today, as older rules or direct inserts left them.
STORED_ROWS_CREATE_REFUSES = [
    ("+", "++"),  # empty modification, accepted before that rule
    ("+", "[M+H]+ 0123456789abcdef"),  # free-text label
    ("-", "-H-"),  # polarity the mechanism does not imply
    ("+", "+Zz+"),  # element the formula check does not know
]


@pytest.mark.parametrize(("polarity", "mechanism"), STORED_ROWS_CREATE_REFUSES)
def test_stored_row_is_read_as_it_is(polarity, mechanism):
    """One row the write validators refuse must not fail the whole listing."""
    row = {
        "ionization_mechanism_id": "0123456789abcdef",
        "ionization_mechanism_polarity": polarity,
        "ionization_mechanism": mechanism,
    }
    read = IonizationMechanismRead.model_validate(row)
    assert read.model_dump() == row

    with pytest.raises(ValidationError):
        IonizationMechanismCreate(
            ionization_mechanism_polarity=polarity, ionization_mechanism=mechanism
        )


@pytest.mark.parametrize("mechanism", ["++", "+Zz+", "H+"])
def test_update_still_validates_the_mechanism(mechanism):
    with pytest.raises(ValidationError):
        IonizationMechanismUpdate(
            ionization_mechanism_polarity="+", ionization_mechanism=mechanism
        )
