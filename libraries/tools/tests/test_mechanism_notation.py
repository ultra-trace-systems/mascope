"""The two notations a mechanism is written in, and the map between them.

What is pinned:

- the standard notation's trailing sign is the ion's charge, so ``[M-H]-`` is
  deprotonation and ``[M-H]+`` hydride abstraction, and each reads as the
  legacy spelling of the same mechanism does (``-H+`` and ``-H-``);
- the map is exact in the direction a stored row travels: every legacy
  spelling a deployment stores converts to a standard one and back to itself,
  and a standard spelling is stored as that round trip writes it, so a
  downgrade followed by an upgrade leaves every row as it was;
- one mechanism has one spelling: its terms are written in alphabetical order
  whichever order they were typed in, and every spelling the fleet stores is
  already written so;
- what neither notation can say is refused with the reason, not approximated;
- the three shapes only the standard notation says - a multimer, a charge
  above one and a neutral loss - are read and written back as they are, and
  have no legacy spelling;
- a mechanism and its structured row map onto each other exactly, for every
  spelling the fleet stores, the library's tables and the new shapes, and the
  row derives the ion's charge and polarity rather than storing them;
- one ion of a charge above one has one spelling: one species written as many
  times as the charge folds into its coefficient, and a species of a single
  charge is refused where it would carry more (alone, or repeated inside its
  formula), with the spelling meant;
- a removed proton is never a neutral loss, and only a mechanism that adds has
  one.
"""

import itertools

import pytest

from mascope_tools.composition.calibration import PROVISIONAL_ORBITRAP_CORROBORATION
from mascope_tools.composition.mechanism_notation import (
    AdductRow,
    MechanismNotationError,
    MechanismParts,
    from_row,
    legacy_notation,
    mechanism_key,
    mechanism_spellings,
    parse_mechanism,
    standard_notation,
    to_row,
)
from mascope_tools.composition.profiles import REAGENT_PROFILES
from mascope_tools.composition.reagents import SECONDARY_CHANNELS


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
    ("+(H)(H2O)H2O+", "[M+H+H2O+H2O]+"),
    ("+(H2O)2H+", "[M+(H2O)2H]+"),
    ("+(CH3)3C+", "[M+(CH3)3C]+"),
    ("+((A))(B)+", "[M+(A)+(B)]+"),
    ("+(CH4N2O)+", "[M+(CH4N2O)]+"),
]

#: A mechanism typed with its terms in another order, in either notation, and
#: the one spelling it is stored and shown in.
OUT_OF_ORDER = [
    ("[M+H+CH4N2O]+", "[M+CH4N2O+H]+"),
    ("+(H)CH4N2O+", "[M+CH4N2O+H]+"),
    ("[M+NO3+HNO3]-", "[M+HNO3+NO3]-"),
    ("[M+H2O+H]+", "[M+H+H2O]+"),
    ("+(H2O)(H2O)H+", "[M+H+H2O+H2O]+"),
    ("+(A)(B)+", "[M+(B)+A]+"),
    # A term that opens with a group reads as the terms it holds, wherever
    # it stands among the others.
    ("[M+(B)C+(A)]+", "[M+(A)+B+C]+"),
    ("[M+(H2O)Na+K]+", "[M+H2O+K+Na]+"),
    ("[M+K+(H2O)Na]+", "[M+H2O+K+Na]+"),
    ("+(K)(H2O)Na+", "[M+H2O+K+Na]+"),
    ("+((H2O)Na)K+", "[M+H2O+K+Na]+"),
    ("[M+(H2O)H+NH4]+", "[M+H+H2O+NH4]+"),
    ("[M+NH4+(H2O)H]+", "[M+H+H2O+NH4]+"),
]

#: Terms to write mechanisms of in every order, the grouped and the
#: multiplied among them.
TERMS = ["H", "K", "H2O", "(H2O)Na", "(H2O)2", "(CH3)3C", "((A)B)C", "(A)", "^NO3"]


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


@pytest.mark.parametrize(("typed", "stored"), OUT_OF_ORDER)
def test_terms_typed_in_another_order_are_the_same_mechanism(typed, stored):
    assert standard_notation(typed) == stored
    assert mechanism_key(typed) == stored
    assert parse_mechanism(typed) == parse_mechanism(stored)
    assert standard_notation(legacy_notation(stored)) == stored


@pytest.mark.parametrize(
    "terms",
    [
        combination
        for count in (2, 3)
        for combination in itertools.combinations(TERMS, count)
    ],
)
def test_every_order_of_the_terms_is_one_spelling(terms):
    written = {
        standard_notation("[M" + "".join("+" + term for term in order) + "]+")
        for order in itertools.permutations(terms)
    }
    assert len(written) == 1
    (stored,) = written
    assert standard_notation(stored) == stored
    assert standard_notation(legacy_notation(stored)) == stored


def _table_notations():
    for profile in REAGENT_PROFILES.values():
        yield from profile.detection
        yield from profile.secondary_adducts
    for channels in SECONDARY_CHANNELS.values():
        yield from (channel.notation for channel in channels)
    yield from PROVISIONAL_ORBITRAP_CORROBORATION


@pytest.mark.parametrize("notation", sorted(set(_table_notations())))
def test_the_library_spells_its_tables_as_mechanisms_are_compared(notation):
    # A mode's mechanisms are keyed before they meet these tables, which are
    # read as they are written.
    assert mechanism_key(notation) == notation


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
        ("[1M+H]+", "one molecule is written '\\[M'"),
        ("[02M+H]+", "one molecule"),
        ("[M+H]1+", "single charge is written"),
        ("[M+H]", "must end in the ion's charge"),
        ("[M+H]2", "must end in the ion's charge"),
        ("[M]2+.", "one electron"),
        ("[M-H2O]+.", "only electron transfer"),
        ("[M+2H]+", "as a formula"),
        ("[M+2H+Na]3+", "only term"),
        ("[M+3H]2+", "is the ion's charge"),
        ("[M+2]2+", "is the ion's charge"),
        ("[M+Na-2H]-", "neutral loss '2H' as a formula"),
        ("[M+H-2H2O]+", "neutral loss '2H2O' as a formula"),
        ("[M+H-H 2O]+", "not a formula"),
        ("[M+2H2O+H]+", "as a formula"),
        ("[M++H]+", "added with"),
        ("[M+ H]+", "not a formula"),
        ("[M+(CH4N2O+H]+", "unbalanced"),
        ("[M+A)(B]+", "unbalanced"),
        ("+A)(B+", "unbalanced"),
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


#: The shapes only the standard notation says, as typed, as stored, and as a
#: row: a multimer, a charge above one, a neutral loss, and their mixtures.
NEW_SHAPES = [
    ("[2M+H]+", "[2M+H]+", AdductRow("add", "H", 1, multimer_n=2)),
    ("[3M+Na]+", "[3M+Na]+", AdductRow("add", "Na", 1, multimer_n=3)),
    ("[2M-H]-", "[2M-H]-", AdductRow("remove", "H", 1, multimer_n=2)),
    ("[2M+Br]-", "[2M+Br]-", AdductRow("add", "Br", -1, multimer_n=2)),
    ("[2M]+.", "[2M]+.", AdductRow("remove", "", -1, multimer_n=2)),
    ("[M+2H]2+", "[M+2H]2+", AdductRow("add", "(H)2", 2)),
    ("[M+(H)2]2+", "[M+2H]2+", AdductRow("add", "(H)2", 2)),
    ("[M+3H]3+", "[M+3H]3+", AdductRow("add", "(H)3", 3)),
    ("[M-2H]2-", "[M-2H]2-", AdductRow("remove", "(H)2", 2)),
    ("[M+2NH4]2+", "[M+2NH4]2+", AdductRow("add", "(NH4)2", 2)),
    ("[M+Ca]2+", "[M+Ca]2+", AdductRow("add", "Ca", 2)),
    ("[M+H+Na]2+", "[M+H+Na]2+", AdductRow("add", "(H)Na", 2)),
    ("[M+Na+H]2+", "[M+H+Na]2+", AdductRow("add", "(H)Na", 2)),
    # One species as many times as the charge is the coefficient form.
    ("[M+H+H]2+", "[M+2H]2+", AdductRow("add", "(H)2", 2)),
    ("[M+(H)H]2+", "[M+2H]2+", AdductRow("add", "(H)2", 2)),
    ("[M-H-H]2-", "[M-2H]2-", AdductRow("remove", "(H)2", 2)),
    ("[M+H+H+H]3+", "[M+3H]3+", AdductRow("add", "(H)3", 3)),
    ("[M+K+K]2+", "[M+2K]2+", AdductRow("add", "(K)2", 2)),
    ("[2M+H+H]2+", "[2M+2H]2+", AdductRow("add", "(H)2", 2, multimer_n=2)),
    (
        "[M+H+H-H2O]2+",
        "[M+2H-H2O]2+",
        AdductRow("add", "(H)2", 2, neutral_loss="H2O"),
    ),
    # Several species stay one term each, as the coefficient's hint says.
    ("[M+H+H+Na]3+", "[M+H+H+Na]3+", AdductRow("add", "(H)(H)Na", 3)),
    # Multivalent ions carry the whole charge.
    ("[M+Mg]2+", "[M+Mg]2+", AdductRow("add", "Mg", 2)),
    ("[M+Fe]3+", "[M+Fe]3+", AdductRow("add", "Fe", 3)),
    ("[M+Hg2]2+", "[M+Hg2]2+", AdductRow("add", "Hg2", 2)),
    # Fewer repeats than the charge stay as typed: each Ca carries two.
    ("[M+Ca+Ca]4+", "[M+Ca+Ca]4+", AdductRow("add", "(Ca)Ca", 4)),
    # A count in the formula other than the charge is no coefficient written
    # into it.
    ("[M+H3]2+", "[M+H3]2+", AdductRow("add", "H3", 2)),
    # A group multiplied by anything but the ion's charge is no coefficient.
    ("[M+(H)3]2+", "[M+(H)3]2+", AdductRow("add", "(H)3", 2)),
    ("[M+H-H2O]+", "[M+H-H2O]+", AdductRow("add", "H", 1, neutral_loss="H2O")),
    ("[M-H2O+H]+", "[M+H-H2O]+", AdductRow("add", "H", 1, neutral_loss="H2O")),
    ("[M+Na-CO2]+", "[M+Na-CO2]+", AdductRow("add", "Na", 1, neutral_loss="CO2")),
    (
        "[M+H-NH3-H2O]+",
        "[M+H-H2O-NH3]+",
        AdductRow("add", "H", 1, neutral_loss="(H2O)NH3"),
    ),
    (
        "[M+H-(H2O)2]+",
        "[M+H-(H2O)2]+",
        AdductRow("add", "H", 1, neutral_loss="(H2O)2"),
    ),
    # Dihydrogen is a neutral; a removed proton is not.
    ("[M+H-H2]+", "[M+H-H2]+", AdductRow("add", "H", 1, neutral_loss="H2")),
    (
        "[2M+2H-H2O]2+",
        "[2M+2H-H2O]2+",
        AdductRow("add", "(H)2", 2, multimer_n=2, neutral_loss="H2O"),
    ),
]


@pytest.mark.parametrize(("typed", "stored", "row"), NEW_SHAPES)
def test_the_new_shapes_read_and_are_written_back_as_stored(typed, stored, row):
    assert standard_notation(typed) == stored
    assert standard_notation(stored) == stored
    assert mechanism_key(typed) == stored
    assert to_row(typed) == row
    assert from_row(row) == stored
    assert row.display == stored


@pytest.mark.parametrize(("typed", "stored", "row"), NEW_SHAPES)
def test_the_new_shapes_have_no_legacy_spelling(typed, stored, row):
    parts = parse_mechanism(typed)
    assert not parts.simple
    assert parts.legacy is None
    assert row.legacy is None
    with pytest.raises(MechanismNotationError, match="no legacy spelling"):
        legacy_notation(typed)
    assert mechanism_spellings([typed]) == list(dict.fromkeys([typed, stored]))


def _every_spelling():
    yield from (standard for _, standard in STORED + UNUSUAL)
    yield from (stored for _, stored in OUT_OF_ORDER)
    yield from _table_notations()
    yield from (stored for _, stored, _ in NEW_SHAPES)


@pytest.mark.parametrize("notation", sorted(set(_every_spelling())))
def test_a_mechanism_and_its_row_map_onto_each_other(notation):
    row = to_row(notation)
    assert from_row(row) == notation
    assert to_row(from_row(row)) == row
    assert to_row(notation, name="a name") == AdductRow(
        **{**row.__dict__, "name": "a name"}
    )


@pytest.mark.parametrize(("legacy", "standard"), STORED + UNUSUAL)
def test_a_legacy_spelling_has_the_row_of_its_standard_one(legacy, standard):
    row = to_row(legacy)
    assert row == to_row(standard)
    assert row.legacy == legacy
    assert row.parts.simple
    assert row.multimer_n == 1
    assert row.neutral_loss is None


@pytest.mark.parametrize(
    ("notation", "operation", "species", "species_charge", "ion_charge"),
    [
        ("[M+H]+", "add", "H", 1, 1),
        ("[M-H]-", "remove", "H", 1, -1),
        ("[M-H]+", "remove", "H", -1, 1),
        ("[M+Br]-", "add", "Br", -1, -1),
        ("[M+NH4]+", "add", "NH4", 1, 1),
        ("[M+CH4N2O+H]+", "add", "(CH4N2O)H", 1, 1),
        ("[M]+.", "remove", "", -1, 1),
        ("[M]-.", "add", "", -1, -1),
        ("[M+2H]2+", "add", "(H)2", 2, 2),
        ("[M-2H]2-", "remove", "(H)2", 2, -2),
        ("[M+Ca]2+", "add", "Ca", 2, 2),
    ],
)
def test_a_row_derives_the_ions_charge_and_polarity(
    notation, operation, species, species_charge, ion_charge
):
    row = to_row(notation)
    assert (row.operation, row.species, row.species_charge) == (
        operation,
        species,
        species_charge,
    )
    assert row.ion_charge == ion_charge
    assert row.ion_polarity == ("+" if ion_charge > 0 else "-")
    assert row.ion_polarity == parse_mechanism(notation).polarity


@pytest.mark.parametrize(
    ("row", "reason"),
    [
        (AdductRow("attach", "H", 1), "'add' or 'remove'"),
        (AdductRow("add", "H", 0), "carries a charge"),
        (AdductRow("add", "H", 1, multimer_n=0), "at least one molecule"),
        # Terms out of order, and a group that would split: another spelling
        # of a mechanism the row would then not round-trip to.
        (AdductRow("add", "(H)CH4N2O", 1), "not written as a mechanism reads"),
        (AdductRow("add", "Ca", 2, neutral_loss="(NH3)H2O"), "not written as"),
        # Another spelling of an ion that has one: two protons are (H)2.
        (AdductRow("add", "(H)H", 2), "not written as"),
        # One proton carries one charge.
        (AdductRow("add", "H", 2), "write '\\[M\\+2H\\]2\\+'"),
        # A mechanism that removes loses no neutral: written out, the loss
        # reads as part of what is removed.
        (AdductRow("remove", "H", 1, neutral_loss="H2O"), "not written as"),
        # The electron is one charge.
        (AdductRow("remove", "", -2), "one electron"),
        (AdductRow("add", "H!", 1), "not a formula"),
    ],
)
def test_a_row_no_mechanism_reads_as_is_refused(row, reason):
    with pytest.raises(MechanismNotationError, match=reason):
        from_row(row)


@pytest.mark.parametrize(
    ("notation", "formulas"),
    [
        ("[M+CH4N2O+H]+", ("CH4N2O", "H")),
        ("[M+2H]2+", ("(H)2",)),
        ("[M+H-H2O-NH3]+", ("H", "H2O", "NH3")),
        ("[2M+Na-CO2]+", ("Na", "CO2")),
        ("[M]+.", ()),
    ],
)
def test_every_formula_a_mechanism_moves_is_listed(notation, formulas):
    # What the write validator checks for real elements, the neutral loss's
    # included.
    assert parse_mechanism(notation).formulas == formulas


@pytest.mark.parametrize(
    ("notation", "meant"),
    [
        # The coefficient left out: one species of a single charge carrying
        # the ion's whole charge.
        ("[M+H]2+", "[M+2H]2+"),
        ("[M-H]2-", "[M-2H]2-"),
        ("[M+Na]3+", "[M+3Na]3+"),
        ("[M+NH4]2+", "[M+2NH4]2+"),
        ("[M+NO3]2-", "[M+2NO3]2-"),
        ("[M+Ag]2+", "[M+2Ag]2+"),
        ("[M+Cl]2-", "[M+2Cl]2-"),
        ("[2M+H-H2O]2+", "[2M+2H-H2O]2+"),
        # The coefficient written into the formula.
        ("[M+H2]2+", "[M+2H]2+"),
        ("[M-H2]2-", "[M-2H]2-"),
        ("[M+Na2]2+", "[M+2Na]2+"),
        ("[M+H3]3+", "[M+3H]3+"),
        ("[M+Br2]2-", "[M+2Br]2-"),
    ],
)
def test_a_species_of_one_charge_carrying_more_is_refused_with_the_ion_meant(
    notation, meant
):
    with pytest.raises(MechanismNotationError, match="one charge") as refused:
        parse_mechanism(notation)
    assert f"write {meant!r}" in str(refused.value)
    assert standard_notation(meant) == meant


#: The species that carry one charge, never more: the frontend's table too.
SINGLE_CHARGE_SPECIES = "H Li Na K Rb Cs F Cl Br I NH4 NO3 Ag".split()


@pytest.mark.parametrize("species", SINGLE_CHARGE_SPECIES)
def test_every_species_of_one_charge_is_refused_on_two(species):
    with pytest.raises(MechanismNotationError, match="one charge"):
        parse_mechanism(f"[M+{species}]2+")
    assert standard_notation(f"[M+2{species}]2+") == f"[M+2{species}]2+"


@pytest.mark.parametrize(
    "notation",
    [
        "[M+H2O-H]-",
        "[M+Na-H-H]-",
        "[M+Na+Na-H]+",
        "[M+Na-(H)H]-",
        "[M+NH4-H-H2O]+",
    ],
)
def test_a_removed_proton_is_never_a_neutral_loss(notation):
    with pytest.raises(MechanismNotationError, match="'H' is a proton"):
        parse_mechanism(notation)


@pytest.mark.parametrize(
    ("notation", "species"),
    [("[M-H-H2O]-", "(H)H2O"), ("[M-CO2-H]-", "(CO2)H")],
)
def test_only_a_mechanism_that_adds_has_a_neutral_loss(notation, species):
    # What a mechanism removes is all its species; these are read as they
    # were before neutral losses existed, and keep their legacy spelling.
    row = to_row(notation)
    assert (row.operation, row.species, row.neutral_loss) == ("remove", species, None)
    assert row.legacy is not None


def test_coefficients_still_go_on_the_only_term():
    # Repeated coefficient terms do not fold into a group of a group.
    with pytest.raises(MechanismNotationError, match="only term"):
        parse_mechanism("[M+2H+2H]2+")
