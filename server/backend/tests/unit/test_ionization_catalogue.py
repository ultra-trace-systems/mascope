"""
Tests: the ionization mechanisms Mascope ships.

A run searches a channel only where the server holds its mechanism, so the
list of shipped mechanisms is what makes the chemistry a run searches under
the same on every server. It has to name every mechanism a shipped mode
declares and every channel a profile can open, and nothing else - so it is
read off those tables, and pinned here against them read independently.
"""

import re

import pytest

from mascope_backend.db import IonizationMechanism
from mascope_backend.ionization_catalogue import (
    DECLARED_BEFORE,
    SYSTEM_MODES,
    is_shipped_mechanism,
    shipped_mechanisms,
)
from mascope_tools.composition.mechanism_notation import legacy_notation
from mascope_tools.composition.profiles import REAGENT_PROFILES
from mascope_tools.composition.reagents import SECONDARY_CHANNELS


#: The ids the seed creates rows under. Fixed, so a row reads the same on every
#: server that the seed created it on; a mechanism a later channel adds
#: extends this rather than changing it.
FIXED_IDS = {
    "[M]+.": "sysLossEPos",
    "[M]-.": "sysAddENeg",
    "[M+H]+": "sysAddHPos",
    "[M-H]-": "sysLossHNeg",
    "[M-H]+": "sysLossHPos",
    "[M+NO3]-": "sysAddNO3Neg",
    "[M+^NO3]-": "sysAddLabNO3Neg",
    "[M+Br]-": "sysAddBrNeg",
    "[M+I]-": "sysAddINeg",
    "[M+NH4]+": "sysAddNH4Pos",
    "[M+^NH4]+": "sysAddLabNH4Pos",
    "[M+CH4N2O+H]+": "sysAddCH4N2OHPos",
    "[M+CO3]-": "sysAddCO3Neg",
    "[M+HCOO]-": "sysAddHCOONeg",
    "[M+Br2]-": "sysAddBr2Neg",
    "[M+I2]-": "sysAddI2Neg",
    "[M+Na]+": "sysAddNaPos",
    "[M+K]+": "sysAddKPos",
}


#: What each shipped mode declares. Written out, so that a change to a
#: chemistry's mechanisms reads as a change to what every server searches
#: under it, and needs its earlier set in DECLARED_BEFORE.
DECLARED = {
    "nitrate": ("[M+NO3]-", "[M-H]-"),
    "nitrate-15n": ("[M+^NO3]-", "[M-H]-"),
    "bromide": ("[M+Br]-", "[M-H]-"),
    "iodide": ("[M+I]-", "[M-H]-"),
    "deprotonation": ("[M-H]-",),
    "ambient-negative": ("[M]-.",),
    # The ammonium adduct since decision MAS-811: a regular channel of the
    # source, which without it reads its ions as nitrogen-bearing neutrals.
    "uronium": ("[M+CH4N2O+H]+", "[M+H]+", "[M+NH4]+"),
    "ammonium": ("[M+NH4]+", "[M+H]+"),
    "ammonium-15n": ("[M+^NH4]+", "[M+H]+"),
    "protonation": ("[M+H]+",),
    "ambient-positive": ("[M]+.",),
}


def test_what_each_shipped_mode_declares():
    """The reagent's own adduct first: a seeded collection writes each of its
    ions through the first mechanism that makes it."""
    assert {key: mechanisms for key, *_, mechanisms in SYSTEM_MODES} == DECLARED


def test_a_definition_declared_before_is_an_earlier_one_of_its_chemistry():
    """A current definition among the earlier ones would read as the one to
    replace; one of another chemistry's key would redefine nothing."""
    polarity = {key: polarity for key, _, _, polarity, _ in SYSTEM_MODES}
    shipped = {m.notation: m.polarity for m in shipped_mechanisms()}

    assert set(DECLARED_BEFORE) <= set(DECLARED)
    for key, earlier in DECLARED_BEFORE.items():
        for mechanisms in earlier:
            assert mechanisms != frozenset(DECLARED[key])
            assert {shipped[m] for m in mechanisms} == {polarity[key]}
    assert DECLARED_BEFORE["uronium"] == (frozenset({"[M+CH4N2O+H]+", "[M+H]+"}),)


def _modes_and_channels() -> set[str]:
    """The union the list stands for, read from the tables as they are."""
    return (
        {mechanism for *_, mechanisms in SYSTEM_MODES for mechanism in mechanisms}
        | {
            notation
            for profile in REAGENT_PROFILES.values()
            for notation in profile.secondary_adducts
        }
        | {
            channel.notation
            for channels in SECONDARY_CHANNELS.values()
            for channel in channels
        }
    )


def test_the_list_is_the_modes_mechanisms_and_the_profiles_channels():
    shipped = [mechanism.notation for mechanism in shipped_mechanisms()]

    assert len(shipped) == len(set(shipped))
    assert set(shipped) == _modes_and_channels()


def test_the_list_holds_every_mechanism_a_shipped_mode_or_a_channel_needs():
    """Named, so that dropping one from a profile or a mode reads as a change
    to what every server searches, not as a count moving."""
    shipped = {mechanism.notation for mechanism in shipped_mechanisms()}

    assert set(FIXED_IDS) <= shipped


@pytest.mark.parametrize(
    "mechanism", shipped_mechanisms(), ids=lambda mechanism: mechanism.notation
)
def test_the_polarity_is_the_charge_the_notation_ends_in(mechanism):
    """``[M-H]-`` is an anion and ``[M-H]+`` a cation: the sign after the
    brackets, not the one inside them."""
    charge = mechanism.notation.rstrip(".")[-1]

    assert mechanism.polarity == charge


@pytest.mark.parametrize(
    "mechanism", shipped_mechanisms(), ids=lambda mechanism: mechanism.notation
)
def test_the_fixed_id_fits_the_column_and_a_url(mechanism):
    column = IonizationMechanism.__table__.c.ionization_mechanism_id.type.length

    assert len(mechanism.mechanism_id) <= column
    assert re.fullmatch(r"sys[A-Za-z0-9]+", mechanism.mechanism_id)


def test_every_fixed_id_is_its_own():
    ids = [mechanism.mechanism_id for mechanism in shipped_mechanisms()]

    assert len(ids) == len(set(ids))


@pytest.mark.parametrize(("notation", "mechanism_id"), sorted(FIXED_IDS.items()))
def test_the_fixed_ids(notation, mechanism_id):
    by_notation = {m.notation: m.mechanism_id for m in shipped_mechanisms()}

    assert by_notation[notation] == mechanism_id


@pytest.mark.parametrize("notation", sorted(FIXED_IDS))
def test_a_row_in_either_spelling_under_its_polarity_is_shipped(notation):
    polarity = notation.rstrip(".")[-1]

    assert is_shipped_mechanism(notation, polarity)
    assert is_shipped_mechanism(legacy_notation(notation), polarity)


@pytest.mark.parametrize(
    ("notation", "polarity"),
    [
        # A shipped notation stored under the other polarity: a broken copy.
        ("[M+Br]-", "+"),
        ("-H-", "-"),  # legacy for [M-H]+, a cation
        # Not shipped at all.
        ("[M+Cl]-", "-"),
        ("[M+H]+ (a free-text label)", "+"),
    ],
)
def test_anything_else_is_not(notation, polarity):
    assert not is_shipped_mechanism(notation, polarity)


@pytest.mark.parametrize(
    ("notation", "fixed_id"),
    [("[M-H-H2O]-", "sysLossHH2ONeg"), ("[M-CO2-H]-", "sysLossCO2HNeg")],
)
def test_a_removing_mechanism_s_id_names_its_loss_too(notation, fixed_id):
    # It reads its proton apart from its loss now, and its id is still made
    # of every term it removes, as before.
    from mascope_backend.ionization_catalogue import _fixed_mechanism_id
    from mascope_tools.composition.mechanism_notation import parse_mechanism

    assert _fixed_mechanism_id(parse_mechanism(notation)) == fixed_id


#: Mechanisms of the shapes no shipped mode or channel holds yet, and the ids
#: the first one shipped would be seeded under: each apart from the simple
#: mechanism it extends, and from every id :data:`FIXED_IDS` pins.
SHAPED_IDS = {
    "[2M+H]+": "sys2MAddHPos",
    "[3M+H]+": "sys3MAddHPos",
    "[2M-H]-": "sys2MLossHNeg",
    "[2M]+.": "sys2MLossEPos",
    "[M+H-H2O]+": "sysAddHLsH2OPos",
    "[M+Na-H2O]+": "sysAddNaLsH2OPos",
    "[M+2H]2+": "sysAdd2HPos2",
    "[M-2H]2-": "sysLoss2HNeg2",
    "[M-H-H2O]-": "sysLossHH2ONeg",
    # Two waters are not hydrogen peroxide, lost, added or removed.
    "[M+H-(H2O)2]+": "sysAddHLs2H2OPos",
    "[M+H-H2O2]+": "sysAddHLsH2O2Pos",
    "[M+(H2O)2H]+": "sysAddH2Ox2HPos",
    "[M+H2O2H]+": "sysAddH2O2HPos",
    "[M+(H2OH)2]+": "sysAdd2H2OHPos",
    "[M-H-(H2O)2]-": "sysLossH2Ox2HNeg",
    "[M-(H2OH)2]-": "sysLoss2H2OHNeg",
    "[M-H-H2O2]-": "sysLossHH2O2Neg",
    # A formula before a group does not run into it.
    "[M+H+(CCH3)4]+": "sysAddCCH3x4HPos",
}


@pytest.mark.parametrize(("notation", "fixed_id"), sorted(SHAPED_IDS.items()))
def test_the_id_says_the_molecule_count_the_loss_and_the_charge(notation, fixed_id):
    from mascope_backend.ionization_catalogue import _fixed_mechanism_id
    from mascope_tools.composition.mechanism_notation import parse_mechanism

    assert _fixed_mechanism_id(parse_mechanism(notation)) == fixed_id


def test_no_shape_takes_another_mechanism_s_id():
    """``[M+H-H2O]+`` and ``[2M+H]+`` used to be seeded as ``sysAddHPos``,
    the id of ``[M+H]+``, and ``[M+H-(H2O)2]+`` as ``sysAddHLsH2O2Pos``, the
    id of ``[M+H-H2O2]+``."""
    from mascope_backend.ionization_catalogue import _fixed_mechanism_id
    from mascope_tools.composition.mechanism_notation import parse_mechanism

    notations = sorted(set(FIXED_IDS) | set(SHAPED_IDS))
    ids = [_fixed_mechanism_id(parse_mechanism(notation)) for notation in notations]

    assert len(ids) == len(set(ids))


@pytest.mark.parametrize(
    ("one", "other"),
    [
        # A count in front would run into the term after the group.
        ("[M+(H2O)2+H]+", "[M+(H2OH)2]+"),
        ("[M-H-(H2O)2]-", "[M-(H2OH)2]-"),
        ("[M+H-(H2O)2-NH3]+", "[M+H-(H2ONH3)2]+"),
        # An x alone would let the formula before a group run into it.
        ("[M+H+(CCH3)4]+", "[M+C(CH3)4H]+"),
        ("[M+((CH3)2CO)2H]+", "[M+(CH3)2(CO)2H]+"),
    ],
)
def test_mechanisms_of_other_masses_take_other_ids(one, other):
    """Pairs too long for :data:`SHAPED_IDS`, or that share an id with one
    there by adding the same atoms in other terms (``[M+(H2O)2+H]+`` and
    ``[M+(H2O)2H]+``, as ``[M+C+H2O]+`` and ``[M+CH2O]+`` do)."""
    from mascope_backend.ionization_catalogue import _fixed_mechanism_id
    from mascope_tools.composition.mechanism_notation import parse_mechanism

    assert _fixed_mechanism_id(parse_mechanism(one)) != _fixed_mechanism_id(
        parse_mechanism(other)
    )


@pytest.mark.parametrize(
    ("terms", "spelled"),
    [
        (("H2O",), "H2O"),
        (("H", "H2O"), "HH2O"),
        (("(CH4N2O)H",), "CH4N2OH"),
        # One group alone: its count in front.
        (("(H2O)2",), "2H2O"),
        (("(H2O)12",), "12H2O"),
        (("((CH3)2CO)2",), "2CH3x2CO"),
        # Any other group: q, its formula, x and its count; no q at the start.
        (("(H2O)2", "H"), "H2Ox2H"),
        (("H", "(H2O)2"), "HqH2Ox2"),
        (("(H2O)2H",), "H2Ox2H"),
        (("C(CH3)4",), "CqCH3x4"),
        (("(CH3)2(CO)2",), "CH3x2qCOx2"),
        (("((CH3)2CO)2", "H"), "CH3x2COx2H"),
        (("(C(CH3)3)2H",), "CqCH3x3x2H"),
        (("H", "((CH3)2CO)2"), "HqqCH3x2COx2"),
    ],
)
def test_a_group_s_multiplier_stands_apart_from_its_formula(terms, spelled):
    from mascope_backend.ionization_catalogue import _id_terms

    assert _id_terms(terms) == spelled


@pytest.mark.parametrize("notation", sorted(SHAPED_IDS))
def test_a_shaped_id_fits_the_column_and_a_url(notation):
    """Each shape fits the column as the shipped ids do. A mechanism with two
    of them on top of a species can pass it (``[2M+H-H2O]+`` would be 17
    characters): the shipped mechanisms' own test refuses such a one the day
    it is shipped, before a seed could write it."""
    from mascope_backend.ionization_catalogue import _fixed_mechanism_id
    from mascope_tools.composition.mechanism_notation import parse_mechanism

    column = IonizationMechanism.__table__.c.ionization_mechanism_id.type.length
    fixed_id = _fixed_mechanism_id(parse_mechanism(notation))

    assert len(fixed_id) <= column
    assert re.fullmatch(r"sys[A-Za-z0-9]+", fixed_id)


@pytest.mark.parametrize(
    ("ion", "charge", "mechanisms", "neutral"),
    [
        # The loss is given back with the proton.
        ("C6H9O5", -1, ("[M-H-H2O]-",), "C6H12O6"),
        ("C6H11O5", 1, ("[M+H-H2O]+",), "C6H12O6"),
        # A dimer's ion is two of one neutral; an ion that is not passes on.
        ("C12H25O12", 1, ("[2M+H]+",), "C6H12O6"),
        ("C13H25O12", 1, ("[2M+H]+", "[M+H]+"), "C13H24O12"),
        # A dication is read only at its own charge.
        ("C6H14O6", 2, ("[M+H]+", "[M+2H]2+"), "C6H12O6"),
    ],
)
def test_the_neutral_of_a_reagent_ion_through_any_shape(
    ion, charge, mechanisms, neutral
):
    from mascope_backend.ionization_catalogue import _neutral

    assert _neutral(ion, charge, mechanisms) == neutral
