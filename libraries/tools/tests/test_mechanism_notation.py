"""The two notations a mechanism is written in, and the map between them.

What is pinned:

- the standard notation's trailing sign is the ion's charge, so ``[M-H]-`` is
  deprotonation and ``[M-H]+`` hydride abstraction, and each reads as the
  legacy spelling of the same mechanism does (``-H+`` and ``-H-``);
- the map is exact in the direction a stored row travels: every legacy
  spelling a deployment stores converts to a standard one and back to itself,
  and a standard spelling is stored as that round trip writes it, so a
  downgrade followed by an upgrade leaves every row as it was;
- what neither notation can say is refused with the reason, not approximated.
"""

import pytest

from mascope_tools.composition.mechanism_notation import (
    MechanismNotationError,
    MechanismParts,
    legacy_notation,
    mechanism_key,
    parse_mechanism,
    standard_notation,
)


#: Every legacy spelling the fleet's servers store, with its standard one.
STORED = [
    ("+", "[M]+."),
    ("-", "[M]-."),
    ("+H+", "[M+H]+"),
    ("-H+", "[M-H]-"),
    ("+Br-", "[M+Br]-"),
    ("+Br2-", "[M+Br2]-"),
    ("+Br3-", "[M+Br3]-"),
    ("+I-", "[M+I]-"),
    ("+I2-", "[M+I2]-"),
    ("+I3-", "[M+I3]-"),
    ("+NO3-", "[M+NO3]-"),
    ("+^NO3-", "[M+^NO3]-"),
    ("+CO3-", "[M+CO3]-"),
    ("+HSO4-", "[M+HSO4]-"),
    ("+(HNO3)NO3-", "[M+HNO3+NO3]-"),
    ("+NH4+", "[M+NH4]+"),
    ("+^NH4+", "[M+^NH4]+"),
    ("+Na+", "[M+Na]+"),
    ("+C4H11N+", "[M+C4H11N]+"),
    ("+(CH4N2O)H+", "[M+CH4N2O+H]+"),
    ("+(CH4N2O)2H+", "[M+(CH4N2O)2H]+"),
    ("+(C3H6O)H+", "[M+C3H6O+H]+"),
    ("+(C6H10O2)H+", "[M+C6H10O2+H]+"),
    ("+(C6H15N)H+", "[M+C6H15N+H]+"),
]

#: Spellings no deployment stores yet that the map must still carry both ways.
UNUSUAL = [
    ("-H-", "[M-H]+"),
    ("-CH3-", "[M-CH3]+"),
    ("+[15N]O3-", "[M+[15N]O3]-"),
    ("+((CH3CH2)2NH)H+", "[M+(CH3CH2)2NH+H]+"),
    ("+(H2O)(H2O)H+", "[M+H2O+H2O+H]+"),
    ("+(H2O)2H+", "[M+(H2O)2H]+"),
    ("+(CH3)3C+", "[M+(CH3)3C]+"),
    ("+(A)(B)+", "[M+A+(B)]+"),
    ("+(CH4N2O)+", "[M+(CH4N2O)]+"),
]


@pytest.mark.parametrize(("legacy", "standard"), STORED + UNUSUAL)
def test_a_legacy_spelling_converts_and_comes_back_as_it_was(legacy, standard):
    assert standard_notation(legacy) == standard
    assert legacy_notation(standard) == legacy
    assert standard_notation(standard) == standard
    assert legacy_notation(legacy) == legacy


@pytest.mark.parametrize(("legacy", "standard"), STORED + UNUSUAL)
def test_both_spellings_are_one_mechanism(legacy, standard):
    assert parse_mechanism(legacy) == parse_mechanism(standard)
    assert mechanism_key(legacy) == mechanism_key(standard) == standard


@pytest.mark.parametrize(
    ("notation", "addition", "moiety", "charge", "moiety_charge"),
    [
        ("[M+H]+", True, "H", 1, 1),
        ("[M-H]-", False, "H", -1, 1),
        ("[M-H]+", False, "H", 1, -1),
        ("[M+Br]-", True, "Br", -1, -1),
        ("[M-CH3]+", False, "CH3", 1, -1),
        ("[M+CH4N2O+H]+", True, "(CH4N2O)H", 1, 1),
        # Electron transfer: an electron, charge -1, removed or attached.
        ("[M]+.", False, "", 1, -1),
        ("[M]-.", True, "", -1, -1),
    ],
)
def test_the_trailing_sign_is_the_ions_charge(
    notation, addition, moiety, charge, moiety_charge
):
    parts = parse_mechanism(notation)
    assert parts == MechanismParts(addition=addition, moiety=moiety, charge=charge)
    assert parts.moiety_charge == moiety_charge
    assert parts.polarity == ("+" if charge > 0 else "-")
    assert parts.electron_transfer is (not moiety)


@pytest.mark.parametrize(
    ("typed", "stored"),
    [
        # A term that opens with a group reads the same as the group standing
        # alone, and is stored so that a downgrade does not split it.
        ("[M+(CH4N2O)H]+", "[M+CH4N2O+H]+"),
        ("[M+(CH4N2O)2+H]+", "[M+(CH4N2O)2+H]+"),
        ("[M+(A)+B]+", "[M+(A)+B]+"),
        ("  [M+H]+  ", "[M+H]+"),
    ],
)
def test_a_standard_spelling_is_stored_as_its_round_trip_writes_it(typed, stored):
    assert standard_notation(typed) == stored
    assert standard_notation(legacy_notation(stored)) == stored


@pytest.mark.parametrize(
    ("notation", "reason"),
    [
        ("[M]+", "radical"),
        ("[M+H]+.", "only electron transfer"),
        ("[2M+H]+", "one"),
        ("[M+2H]2+", "singly charged"),
        ("[M+H]", "singly charged"),
        ("[M+Na-2H]-", "both adds and removes"),
        ("[M+2H2O+H]+", "as a formula"),
        ("[M++H]+", "added with"),
        ("[M+ H]+", "not a formula"),
        ("[M+(CH4N2O+H]+", "unbalanced"),
        ("H+", "standard adduct notation"),
        ("+H", "standard adduct notation"),
        ("++", "standard adduct notation"),
        ("", "standard adduct notation"),
        ("+H!-", "not a formula"),
    ],
)
def test_what_neither_notation_says_is_refused_with_the_reason(notation, reason):
    with pytest.raises(MechanismNotationError, match=reason):
        parse_mechanism(notation)


def test_a_key_leaves_unreadable_text_as_it_is():
    assert mechanism_key("  not a mechanism ") == "not a mechanism"
    assert mechanism_key("+Br-") == mechanism_key("[M+Br]-")
    assert mechanism_key("+Br-") != mechanism_key("+I-")
