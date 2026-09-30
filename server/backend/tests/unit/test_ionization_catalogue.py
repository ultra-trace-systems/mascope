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
