"""Unit tests: routing a sample file to ionization modes by file-name token.

``resolve_ionization_modes_by_tokens`` must find exactly one mode for every
polarity the file contains. Counting matches against the number of polarities
is not enough: a dual-polarity file whose name matches two positive tokens and
no negative one has as many matches as polarities, yet would be routed twice as
positive and never as negative.

The mode list is scripted, so no database rows are involved.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from mascope_backend.api.new.ionization.modes.util import (
    NoTokenMatchError,
    resolve_ionization_modes_by_tokens,
)


_UTIL = "mascope_backend.api.new.ionization.modes.util"


def _mode(name, token, polarity):
    return SimpleNamespace(
        ionization_mode_id=f"id-{name}",
        ionization_mode_name=name,
        ionization_mode_token=token,
        ionization_mode_polarity=polarity,
    )


def _file(filename, polarity):
    return SimpleNamespace(filename=filename, polarity=polarity)


BROMIDE = _mode("Bromide", "BR", "-")
NITRATE = _mode("Nitrate", "NO3", "-")
AMMONIUM = _mode("Ammonium", "NH4", "+")
PROTON = _mode("Proton", "H3O", "+")
UNTOKENED = _mode("Manual", None, "-")


async def _resolve(sample_file, modes):
    with patch(
        f"{_UTIL}.fetch_all_ionization_modes", AsyncMock(return_value=list(modes))
    ):
        return await resolve_ionization_modes_by_tokens(sample_file)


@pytest.mark.asyncio
async def test_single_polarity_file_routes_to_its_one_mode():
    modes = [BROMIDE, NITRATE, AMMONIUM, UNTOKENED]
    resolved = await _resolve(_file("inst_2026.09.18_BR_ambient", "-"), modes)
    assert resolved == [BROMIDE]


@pytest.mark.asyncio
async def test_dual_polarity_file_routes_one_mode_per_polarity_in_its_order():
    """The file's polarity order, as for modes chosen by hand."""
    for modes in ([AMMONIUM, BROMIDE, NITRATE], [BROMIDE, NITRATE, AMMONIUM]):
        resolved = await _resolve(_file("inst_2026.09.18_BR_NH4", "+-"), modes)
        assert resolved == [AMMONIUM, BROMIDE]


@pytest.mark.asyncio
async def test_a_token_of_a_polarity_the_file_lacks_is_ignored():
    modes = [BROMIDE, AMMONIUM]
    resolved = await _resolve(_file("inst_2026.09.18_BR_NH4", "-"), modes)
    assert resolved == [BROMIDE]


@pytest.mark.asyncio
async def test_two_tokens_of_one_polarity_do_not_stand_in_for_the_other():
    modes = [AMMONIUM, PROTON, BROMIDE]
    with pytest.raises(ValueError) as excinfo:
        await _resolve(_file("inst_2026.09.18_NH4_H3O", "+-"), modes)
    message = str(excinfo.value)
    assert "exactly one mode per polarity" in message
    assert "2 modes match polarity +" in message
    assert "'Ammonium' (token 'NH4')" in message
    assert "'Proton' (token 'H3O')" in message
    assert "no mode matches polarity -" in message


@pytest.mark.asyncio
async def test_two_tokens_in_a_single_polarity_file_are_ambiguous():
    modes = [BROMIDE, NITRATE]
    with pytest.raises(ValueError) as excinfo:
        await _resolve(_file("inst_2026.09.18_BR_NO3", "-"), modes)
    message = str(excinfo.value)
    assert "2 modes match polarity -" in message
    assert "'Bromide' (token 'BR')" in message
    assert "'Nitrate' (token 'NO3')" in message


@pytest.mark.asyncio
async def test_dual_polarity_file_with_one_polarity_unmatched_is_refused():
    modes = [BROMIDE, AMMONIUM]
    with pytest.raises(ValueError) as excinfo:
        await _resolve(_file("inst_2026.09.18_BR", "+-"), modes)
    message = str(excinfo.value)
    assert "no mode matches polarity +" in message
    assert "polarity -" not in message


@pytest.mark.asyncio
async def test_no_token_at_all_keeps_the_plain_message():
    modes = [BROMIDE, AMMONIUM, UNTOKENED]
    with pytest.raises(NoTokenMatchError, match="No ionization mode tokens found"):
        await _resolve(_file("inst_2026.09.18_ambient", "+-"), modes)


@pytest.mark.asyncio
async def test_an_ambiguous_name_is_not_taken_for_one_with_no_token():
    """Re-processing stands a file's earlier binding in only for the latter."""
    with pytest.raises(ValueError) as excinfo:
        await _resolve(_file("inst_2026.09.18_BR_NO3", "-"), [BROMIDE, NITRATE])
    assert not isinstance(excinfo.value, NoTokenMatchError)
