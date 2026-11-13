"""Ionization mechanism validation.

A mechanism is accepted in the standard adduct notation (``[M-H]-``) or the
legacy one (``-H+``), and stored in the standard one. Each term's formula is
validated (strictly) via mascope_tools.composition.utils.assert_valid_formula,
which raises on invalid characters and unknown elements rather than silently
ignoring them. A mechanism longer than its column is refused before anything
parses it.
"""

import contextlib
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from mascope_backend.api.controllers.ionization_mechanisms.ionization_mechanisms_controller import (
    read_ionization_mechanism,
    report_if_unwritable,
)
from mascope_backend.api.models.ionization_mechanisms.config import (
    ionization_mechanism_config,
)
from mascope_backend.api.models.ionization_mechanisms.ionization_mechanism_pydantic_model import (
    IonizationMechanismCreate,
    IonizationMechanismRead,
)
from mascope_backend.db import IonizationMechanism
from mascope_tools.composition.mechanism_notation import (
    MechanismNotationError,
    parse_mechanism,
)
from mascope_tools.composition.utils import mass_shift


#: The longest mechanism a create accepts.
LIMIT = ionization_mechanism_config.IONIZATION_MECHANISM_MAX_LENGTH


def _mechanism(length: int) -> str:
    """A valid mechanism this many characters long, in the standard notation."""
    return "[M+" + "H" * (length - 5) + "]+"


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
        # What only the standard notation says.
        ("[2M+H]+", "[2M+H]+", "+"),  # a dimer
        ("[M+2H]2+", "[M+2H]2+", "+"),  # a doubly charged ion
        ("[M-2H]2-", "[M-2H]2-", "-"),
        ("[M+Ca]2+", "[M+Ca]2+", "+"),
        ("[M+H-H2O]+", "[M+H-H2O]+", "+"),  # a neutral loss
        ("[M-H2O+H]+", "[M+H-H2O]+", "+"),
        ("[2M+2H-H2O]2+", "[2M+2H-H2O]2+", "+"),
        ("[M+H+H]2+", "[M+2H]2+", "+"),  # one species as often as the charge
        ("[M-H-H]2-", "[M-2H]2-", "-"),
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
        "[1M+H]+",  # one molecule written with a count
        "[M+H]1+",  # a single charge written with a count
        "[M+3H]2+",  # a coefficient that is not the ion's charge
        "[M]2+.",  # electron transfer of two electrons
        "[M+Na-2H]-",  # a neutral loss written with a coefficient
        "[M+H-Zz]+",  # a neutral loss of an unknown element
        "[M+H-()]+",  # a neutral loss with no atoms
        "[M+2Zz]2+",  # a coefficient of an unknown element
        "[M+()]+",  # a term with no atoms
        "[M+H]2+",  # one proton carrying two charges: [M+2H]2+ mistyped
        "[M-H]2-",
        "[M+H2]2+",  # two protons written as a formula
        "[M+H2O-H]-",  # a removed proton read as a neutral loss
        "[M+Na-H-H]-",
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


def test_the_bound_is_the_column_length():
    """A create accepts what the column can store, and nothing longer."""
    assert LIMIT == IonizationMechanism.__table__.c.ionization_mechanism.type.length


def test_a_mechanism_as_long_as_the_column_is_accepted():
    mechanism = _mechanism(LIMIT)
    created = IonizationMechanismCreate(ionization_mechanism=mechanism)
    assert created.ionization_mechanism == mechanism


@pytest.mark.parametrize("polarity", [None, "+"])
def test_an_overlong_mechanism_is_refused_before_it_is_parsed(monkeypatch, polarity):
    """Parsing takes time in proportion to the text, and the parser's cache
    keeps each value it accepts, so the bound has to come first - including
    where the polarity is derived, before pydantic has checked any field."""
    parsed = []

    def recording_parse(text):
        parsed.append(len(text))
        return parse_mechanism(text)

    monkeypatch.setattr(
        "mascope_backend.api.models.ionization_mechanisms."
        "ionization_mechanism_pydantic_model.parse_mechanism",
        recording_parse,
    )
    body = {"ionization_mechanism": _mechanism(LIMIT + 1)}
    if polarity is not None:
        body["ionization_mechanism_polarity"] = polarity

    with pytest.raises(ValidationError) as refused:
        IonizationMechanismCreate.model_validate(body)

    errors = {error["loc"]: error["type"] for error in refused.value.errors()}
    assert errors[("ionization_mechanism",)] == "string_too_long"
    assert parsed == []


def test_a_legacy_spelling_has_to_fit_the_column_as_it_is_stored():
    """The standard spelling is what is stored, and for one term it is three
    characters longer than the legacy one: the bound on what is sent is not
    enough on its own."""
    fits = "+" + "H" * (LIMIT - 5) + "+"
    created = IonizationMechanismCreate(ionization_mechanism=fits)
    assert created.ionization_mechanism == _mechanism(LIMIT)

    overflows = "+" + "H" * (LIMIT - 2) + "+"
    assert len(overflows) == LIMIT
    with pytest.raises(ValidationError, match=f"is {LIMIT + 3} characters"):
        IonizationMechanismCreate(ionization_mechanism=overflows)


#: Rows a create would refuse today, as older rules or direct inserts left them.
STORED_ROWS_CREATE_REFUSES = [
    ("+", "++"),  # empty modification, accepted before that rule
    ("+", "[M+H]+ 0123456789abcdef"),  # free-text label
    ("-", "-H-"),  # polarity the mechanism does not imply
    ("+", "+Zz+"),  # element the formula check does not know
    # A legacy row as long as the column, read in the standard notation.
    pytest.param("+", _mechanism(LIMIT + 3), id="legacy-row-at-the-column-length"),
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
    # which makes a cation, stored here under the anion's polarity. What is
    # derived is derived from the mechanism, where it reads as one.
    parts = None
    with contextlib.suppress(MechanismNotationError):
        parts = parse_mechanism(mechanism)
    dumped = read.model_dump()
    assert dumped.pop("ion_charge") == (parts.charge if parts else None)
    assert (dumped.pop("mass_shift") is None) is (parts is None or "Zz" in mechanism)
    assert dumped == {
        **row,
        "shipped": False,
        "name": None,
        "operation": None,
        "species": None,
        "species_charge": None,
        "multimer_n": 1,
        "neutral_loss": None,
    }

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
        "name": None,
        "operation": None,
        "species": None,
        "species_charge": None,
        "multimer_n": 1,
        "neutral_loss": None,
        "ion_charge": None,
        "mass_shift": None,
    }
    assert len(warnings) == 1


@pytest.mark.parametrize(
    ("mechanism", "columns", "ion_charge"),
    [
        ("[M+H]+", ("add", "H", 1, 1, None), 1),
        ("-H+", ("remove", "H", 1, 1, None), -1),
        ("[M+Br]-", ("add", "Br", -1, 1, None), -1),
        ("[M]+.", ("remove", "", -1, 1, None), 1),
        ("[2M+H]+", ("add", "H", 1, 2, None), 1),
        ("[M+2H]2+", ("add", "(H)2", 2, 1, None), 2),
        ("[M+H-H2O]+", ("add", "H", 1, 1, "H2O"), 1),
    ],
)
def test_a_stored_row_writes_and_reports_its_structured_row(
    mechanism, columns, ion_charge
):
    """Setting the mechanism writes the row's columns; the read model reports
    them and what is derived from them."""
    created = IonizationMechanismCreate(
        ionization_mechanism=mechanism, name="  a name  "
    )
    row = IonizationMechanism(
        ionization_mechanism_id="unit-row", **created.model_dump()
    )
    assert (
        row.operation,
        row.species,
        row.species_charge,
        row.multimer_n,
        row.neutral_loss,
    ) == columns
    assert row.name == "a name"
    assert row.adduct_row.display == created.ionization_mechanism
    assert row.ion_charge == ion_charge
    assert row.mass_shift == mass_shift(row.adduct_row)
    assert row.legacy_notation == parse_mechanism(mechanism).legacy
    read = IonizationMechanismRead.model_validate(row).model_dump()
    assert (
        read["operation"],
        read["species"],
        read["species_charge"],
        read["multimer_n"],
        read["neutral_loss"],
    ) == columns
    assert read["name"] == "a name"
    assert read["ion_charge"] == ion_charge
    assert read["mass_shift"] == row.mass_shift


def test_setting_the_mechanism_again_rewrites_the_row():
    row = IonizationMechanism(
        ionization_mechanism_id="unit-row",
        ionization_mechanism_polarity="+",
        ionization_mechanism="[2M+H-H2O]+",
    )
    assert row.legacy_notation is None
    row.ionization_mechanism = "[M+Na]+"
    assert (row.species, row.multimer_n, row.neutral_loss) == ("Na", 1, None)
    assert row.legacy_notation == "+Na+"
    row.ionization_mechanism = "++"
    assert row.adduct_row is None
    assert (row.operation, row.species, row.species_charge) == (None, None, None)
    assert row.ion_charge is None
    assert row.mass_shift is None


@pytest.mark.parametrize("name", [None, "", "   "])
def test_a_blank_name_is_no_name(name):
    created = IonizationMechanismCreate(ionization_mechanism="[M+H]+", name=name)
    assert created.name is None


def test_a_name_longer_than_the_column_is_refused():
    with pytest.raises(ValidationError):
        IonizationMechanismCreate(ionization_mechanism="[M+H]+", name="x" * 257)
