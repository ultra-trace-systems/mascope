"""Ionization mechanism validation.

A mechanism is accepted in the standard adduct notation (``[M-H]-``) or the
legacy one (``-H+``), and stored in the standard one. Each term's formula is
validated (strictly) via mascope_tools.composition.utils.assert_valid_formula,
which raises on invalid characters and unknown elements rather than silently
ignoring them.
"""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from mascope_backend.api.controllers.ionization_mechanisms.ionization_mechanisms_controller import (
    read_ionization_mechanism,
    report_if_unwritable,
)
from mascope_backend.api.models.ionization_mechanisms.ionization_mechanism_pydantic_model import (
    IonizationMechanismCreate,
    IonizationMechanismRead,
)


@pytest.mark.parametrize(
    ("mechanism", "stored", "polarity"),
    [
        ("[M+H]+", "[M+H]+", "+"),  # protonation
        ("+H+", "[M+H]+", "+"),
        ("[M-H]-", "[M-H]-", "-"),  # deprotonation
        ("-H+", "[M-H]-", "-"),
        ("[M-H]+", "[M-H]+", "+"),  # hydride abstraction
        ("-H-", "[M-H]+", "+"),
        ("[M+Br]-", "[M+Br]-", "-"),  # bromide adduct
        ("+Br-", "[M+Br]-", "-"),
        ("+NO3-", "[M+NO3]-", "-"),  # nitrate adduct
        ("[M+^NO3]-", "[M+^NO3]-", "-"),  # 15N-labelled nitrate (custom element)
        ("+^NO3-", "[M+^NO3]-", "-"),
        ("[M+CH4N2O+H]+", "[M+CH4N2O+H]+", "+"),  # urea cluster
        ("+(CH4N2O)H+", "[M+CH4N2O+H]+", "+"),
        ("[M+H+CH4N2O]+", "[M+CH4N2O+H]+", "+"),  # its terms in another order
        ("+(H)CH4N2O+", "[M+CH4N2O+H]+", "+"),
        ("[M]+.", "[M]+.", "+"),  # electron abstraction
        ("+", "[M]+.", "+"),
        ("[M]-.", "[M]-.", "-"),  # electron capture
        ("-", "[M]-.", "-"),
    ],
)
def test_either_notation_is_accepted_and_stored_in_the_standard_one(
    mechanism, stored, polarity
):
    created = IonizationMechanismCreate(ionization_mechanism=mechanism)
    assert created.ionization_mechanism == stored
    assert created.ionization_mechanism_polarity == polarity


@pytest.mark.parametrize(
    "mechanism",
    [
        "+Zz+",  # unknown element
        "+H!+",  # invalid character
        "+(H+",  # unbalanced parenthesis
        "[M+H)(H]+",  # parentheses closed before they open
        "H+",  # missing leading operation
        "+H",  # missing trailing polarity
        "+-",  # invalid sign combination
        "++",  # empty modification formula (used to hang ion generation)
        "--",  # empty modification formula
        "+()+",  # a group with no atoms
        "[M+Zz]+",  # unknown element
        "[M]+",  # electron transfer without its radical dot
        "[M+H]+.",  # a radical dot on a closed-shell ion
        "[2M+H]+",  # a dimer
        "[M+2H]2+",  # a doubly charged ion
        "[M+Na-2H]-",  # terms added and removed at once
        "[M+()]+",  # a term with no atoms
    ],
)
def test_invalid_mechanisms_rejected(mechanism):
    with pytest.raises(ValidationError):
        IonizationMechanismCreate(ionization_mechanism=mechanism)


@pytest.mark.parametrize(
    ("polarity", "mechanism"),
    [("-", "[M-H]+"), ("+", "[M-H]-"), ("-", "-H-"), ("+", "[M]-.")],
)
def test_a_polarity_the_ion_does_not_carry_is_rejected(polarity, mechanism):
    with pytest.raises(ValidationError, match="inconsistent"):
        IonizationMechanismCreate(
            ionization_mechanism_polarity=polarity, ionization_mechanism=mechanism
        )


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
    # None of them is a mechanism Mascope ships: "-H-" is hydride abstraction,
    # which makes a cation, stored here under the anion's polarity.
    assert read.model_dump() == {**row, "shipped": False}

    with pytest.raises(ValidationError):
        IonizationMechanismCreate(
            ionization_mechanism_polarity=polarity, ionization_mechanism=mechanism
        )


@pytest.mark.parametrize(
    "body",
    [
        {},  # no mechanism at all
        {"ionization_mechanism": None},
        {"ionization_mechanism": 5},  # not a string
        {"ionization_mechanism": ""},
        [],  # not an object
    ],
)
def test_malformed_create_body_is_a_validation_error(body):
    """``auto_derive_fields`` runs before pydantic has checked anything, so it
    must hand a malformed body on rather than index into it: a TypeError there
    is a 500, where the request deserves a 422."""
    with pytest.raises(ValidationError):
        IonizationMechanismCreate.model_validate(body)


def _stored(mechanism, polarity="+", mechanism_id="0123456789abcdef"):
    return SimpleNamespace(
        ionization_mechanism_id=mechanism_id,
        ionization_mechanism_polarity=polarity,
        ionization_mechanism=mechanism,
    )


def test_unwritable_stored_row_is_reported_once(monkeypatch):
    """Reads no longer refuse such a row, so the log is what says it is there -
    once per id, not on every read of the listing it appears in."""
    from mascope_backend.api.controllers.ionization_mechanisms import (
        ionization_mechanisms_controller as controller,
    )

    warnings = []
    monkeypatch.setattr(
        controller.runtime.logger, "warning", lambda message: warnings.append(message)
    )
    monkeypatch.setattr(controller, "_reported_unwritable", set())

    row = _stored("+H+ (legacy)", mechanism_id="fedcba9876543210")
    report_if_unwritable(row)
    report_if_unwritable(row)

    assert len(warnings) == 1
    assert "+H+ (legacy)" in warnings[0]
    assert "fedcba9876543210" in warnings[0]


def test_writable_stored_row_is_not_reported(monkeypatch):
    from mascope_backend.api.controllers.ionization_mechanisms import (
        ionization_mechanisms_controller as controller,
    )

    warnings = []
    monkeypatch.setattr(
        controller.runtime.logger, "warning", lambda message: warnings.append(message)
    )
    monkeypatch.setattr(controller, "_reported_unwritable", set())

    report_if_unwritable(_stored("+H+"))

    assert warnings == []


def test_reading_a_row_reports_it_and_returns_it(monkeypatch):
    """The reporting hangs off the read itself, which is what both the listing
    and the single-record route build their response from."""
    from mascope_backend.api.controllers.ionization_mechanisms import (
        ionization_mechanisms_controller as controller,
    )

    warnings = []
    monkeypatch.setattr(
        controller.runtime.logger, "warning", lambda message: warnings.append(message)
    )
    monkeypatch.setattr(controller, "_reported_unwritable", set())

    row = _stored("++", mechanism_id="00112233445566aa")
    assert read_ionization_mechanism(row) == {
        "ionization_mechanism_id": "00112233445566aa",
        "ionization_mechanism_polarity": "+",
        "ionization_mechanism": "++",
        "shipped": False,
    }
    assert len(warnings) == 1
