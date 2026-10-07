"""Unit tests: routing a sample file by the chemistry its acquisition record names.

Rung 0 of the binding ladder. The record gives the token of an ionization
mode, and ``resolve_ionization_modes_by_declaration`` reads it against the
modes a file name is read against - with one difference that is the point of
a declaration: the token has to *be* the mode's, not contain it.

The mode list is scripted, so no database rows are involved.
"""

from types import SimpleNamespace

import pytest

from mascope_backend.api.new.ionization.modes.util import (
    resolve_ionization_modes_by_declaration,
)


def _mode(name, token, polarity, instrument=None):
    return SimpleNamespace(
        ionization_mode_id=f"id-{name}",
        ionization_mode_name=name,
        ionization_mode_token=token,
        ionization_mode_polarity=polarity,
        instrument=instrument,
    )


def _file(polarity, instrument="ORBI-1", filename="run_0042"):
    return SimpleNamespace(filename=filename, polarity=polarity, instrument=instrument)


NITRATE = _mode("Nitrate", "NO3", "-")
NITRATE_15N = _mode("Nitrate 15N", "N15", "-")
AMMONIUM = _mode("Ammonium", "NH4", "+")
UNTOKENED = _mode("Manual", None, "-")
MODES = [NITRATE, NITRATE_15N, AMMONIUM, UNTOKENED]


@pytest.mark.asyncio
async def test_a_declared_token_binds_the_mode_that_has_it():
    bound, unanswered = await resolve_ionization_modes_by_declaration(
        _file("-"), "NO3", MODES
    )

    assert bound == [NITRATE]
    assert unanswered is None


@pytest.mark.asyncio
async def test_the_file_name_plays_no_part():
    """A name that carries another mode's token does not outvote the record."""
    bound, _ = await resolve_ionization_modes_by_declaration(
        _file("-", filename="inst_2026.10.07_N15_ambient"), "NO3", MODES
    )

    assert bound == [NITRATE]


@pytest.mark.parametrize("declared", ["NO3_15N", "NO", "no3", " NO3"])
@pytest.mark.asyncio
async def test_a_declaration_is_the_token_and_not_text_that_holds_it(declared):
    """A name is searched for tokens; a declaration is one."""
    bound, unanswered = await resolve_ionization_modes_by_declaration(
        _file("-"), declared, MODES
    )

    assert bound == []
    assert unanswered == (
        "no ionization mode of this instrument has that token in a polarity "
        "the file holds"
    )


@pytest.mark.asyncio
async def test_a_mode_of_a_polarity_the_file_does_not_hold_is_not_named():
    bound, unanswered = await resolve_ionization_modes_by_declaration(
        _file("-"), "NH4", MODES
    )

    assert bound == []
    assert unanswered is not None


@pytest.mark.asyncio
async def test_a_mode_of_another_instrument_is_not_named():
    theirs = _mode("Their nitrate", "NO3", "-", instrument="ORBI-2")

    bound, unanswered = await resolve_ionization_modes_by_declaration(
        _file("-", instrument="ORBI-1"), "NO3", [theirs, AMMONIUM]
    )

    assert bound == []
    assert unanswered is not None


@pytest.mark.asyncio
async def test_the_instruments_own_mode_wins_over_a_shared_one_of_the_same_token():
    ours = _mode("Our nitrate", "NO3", "-", instrument="orbi-1")

    bound, unanswered = await resolve_ionization_modes_by_declaration(
        _file("-", instrument="ORBI-1"), "NO3", [NITRATE, ours]
    )

    assert bound == [ours]
    assert unanswered is None


@pytest.mark.asyncio
async def test_a_file_of_two_polarities_is_not_half_bound():
    """One token names one mode. The file falls to the next rung whole."""
    bound, unanswered = await resolve_ionization_modes_by_declaration(
        _file("+-"), "NO3", MODES
    )

    assert bound == []
    assert unanswered == "no mode matches polarity +"


@pytest.mark.asyncio
async def test_a_file_that_records_no_polarity_is_not_bound():
    bound, unanswered = await resolve_ionization_modes_by_declaration(
        _file(None), "NO3", MODES
    )

    assert bound == []
    assert unanswered is not None
