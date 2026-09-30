"""Unit tests: scoping an ionization mode to one instrument.

Issue #1463. The scope is a filter on automatic routing: a mode belonging to
one instrument is matched against that instrument's file names alone, so a site
can spell its chemistry the same way in two instruments' names and mean a
different mode by it. Where a scoped and an unscoped mode match one polarity
and the instrument's own token covers the shared one, the instrument's own
wins; anything else about that name stays ambiguous.

Measured before building it: on the largest production server 13 of 16
instruments have run more than one chemistry (one has run 55), and 78 of its
116 modes carry a token - so a token that cannot be scoped collides.

The mode list is scripted, so no database rows are involved.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from mascope_backend.api.new.ionization.modes.util import (
    NoTokenMatchError,
    applies_to_instrument,
    resolve_ionization_modes_by_tokens,
    token_is_unique,
    tokens_conflict,
)


_UTIL = "mascope_backend.api.new.ionization.modes.util"


def _mode(name, token, polarity, instrument=None):
    return SimpleNamespace(
        ionization_mode_id=f"id-{name}",
        ionization_mode_name=name,
        ionization_mode_token=token,
        ionization_mode_polarity=polarity,
        instrument=instrument,
    )


def _file(filename, polarity, instrument="ORBI-1"):
    return SimpleNamespace(filename=filename, polarity=polarity, instrument=instrument)


# The same token, meaning a different chemistry on each instrument - the case
# the scope exists for.
NITRATE_ANY = _mode("Nitrate", "NO3", "-")
NITRATE_ON_1 = _mode("Nitrate 15N", "NO3", "-", instrument="ORBI-1")
NITRATE_ON_2 = _mode("Nitrate natural", "NO3", "-", instrument="ORBI-2")
BROMIDE_ANY = _mode("Bromide", "BR", "-")
AMMONIUM_ON_1 = _mode("Ammonium", "NH4", "+", instrument="ORBI-1")


async def _resolve(sample_file, modes):
    with patch(
        f"{_UTIL}.fetch_all_ionization_modes", AsyncMock(return_value=list(modes))
    ):
        return await resolve_ionization_modes_by_tokens(sample_file)


# --- the filter -------------------------------------------------------------


def test_an_unscoped_mode_applies_to_every_instrument():
    assert applies_to_instrument(NITRATE_ANY, "ORBI-1")
    assert applies_to_instrument(NITRATE_ANY, "TOF-9")
    assert applies_to_instrument(NITRATE_ANY, None)


def test_a_scoped_mode_applies_to_its_own_instrument_only():
    assert applies_to_instrument(NITRATE_ON_1, "ORBI-1")
    assert not applies_to_instrument(NITRATE_ON_1, "ORBI-2")


@pytest.mark.asyncio
async def test_one_token_can_mean_a_different_chemistry_on_each_instrument():
    """The whole point of the scope."""
    modes = [NITRATE_ON_1, NITRATE_ON_2]

    on_one = await _resolve(_file("ORBI-1_NO3_ambient", "-", "ORBI-1"), modes)
    on_two = await _resolve(_file("ORBI-2_NO3_ambient", "-", "ORBI-2"), modes)

    assert on_one == [NITRATE_ON_1]
    assert on_two == [NITRATE_ON_2]


@pytest.mark.asyncio
async def test_another_instruments_mode_is_not_matched_however_well_it_reads():
    with pytest.raises(NoTokenMatchError):
        await _resolve(_file("TOF-9_NO3_ambient", "-", "TOF-9"), [NITRATE_ON_1])


@pytest.mark.asyncio
async def test_an_unscoped_mode_still_routes_every_instrument():
    """Nothing changes for a site that has scoped nothing, which is all of them."""
    for instrument in ("ORBI-1", "ORBI-2", "TOF-9"):
        resolved = await _resolve(
            _file(f"{instrument}_NO3_ambient", "-", instrument),
            [NITRATE_ANY, BROMIDE_ANY],
        )
        assert resolved == [NITRATE_ANY]


# --- the instrument's own mode wins ----------------------------------------


@pytest.mark.asyncio
async def test_the_instruments_own_mode_beats_the_shared_one():
    """So a site can add one scoped mode without scoping everything at once.

    Both tokens match the name; without this rule the file would be refused
    for matching two modes in one polarity, and adding a scoped mode would
    break the instrument it was added for.
    """
    resolved = await _resolve(
        _file("ORBI-1_NO3_ambient", "-", "ORBI-1"), [NITRATE_ANY, NITRATE_ON_1]
    )
    assert resolved == [NITRATE_ON_1]


@pytest.mark.asyncio
async def test_the_shared_mode_still_wins_where_there_is_no_scoped_one():
    resolved = await _resolve(
        _file("ORBI-2_NO3_ambient", "-", "ORBI-2"), [NITRATE_ANY, NITRATE_ON_1]
    )
    assert resolved == [NITRATE_ANY]


@pytest.mark.asyncio
async def test_preference_is_per_polarity():
    """An instrument may own one polarity and share the other."""
    positive_shared = _mode("Proton", "H3O", "+")
    resolved = await _resolve(
        _file("ORBI-1_NO3_H3O_switching", "-+", "ORBI-1"),
        [NITRATE_ANY, NITRATE_ON_1, positive_shared],
    )
    assert resolved == [NITRATE_ON_1, positive_shared]


@pytest.mark.asyncio
async def test_two_scoped_modes_of_one_polarity_are_still_ambiguous():
    """The scope resolves shared-versus-own, not a genuine misconfiguration."""
    other = _mode("Nitrate again", "NO3x", "-", instrument="ORBI-1")
    with pytest.raises(ValueError) as raised:
        await _resolve(
            _file("ORBI-1_NO3x_ambient", "-", "ORBI-1"), [NITRATE_ON_1, other]
        )
    assert "2 modes match polarity -" in str(raised.value)


# --- token uniqueness within the scope --------------------------------------


def test_a_scope_is_compared_folded():
    """SampleFile.instrument is recorded with inconsistent case.

    So two modes scoped to two spellings of one instrument share a scope, and an
    overlap between their tokens conflicts.
    """
    assert tokens_conflict("NO3", "ORBI-1", "NO3", "orbi-1")
    assert tokens_conflict("NO3", "ORBI-1", "NO3", " orbi-1 ")


def test_unrelated_tokens_never_conflict():
    assert not tokens_conflict("BR", None, "NO3", "ORBI-1")


def test_an_overlap_within_one_scope_conflicts():
    assert tokens_conflict("NO3", None, "NO3", None)
    assert tokens_conflict("NO3_15N", "ORBI-1", "NO3", "ORBI-1")


def test_an_overlap_between_two_named_instruments_does_not():
    """Neither is ever matched against the other's files.

    The second pair is the one that pins this: two instruments whose tokens
    overlap *unequally*, where the scoped-versus-shared rule below would call it
    a conflict if it were reached. With equal tokens that rule happens to answer
    the same way, so a test using those alone leaves this case uncovered - which
    it was.
    """
    assert not tokens_conflict("NO3", "ORBI-1", "NO3", "ORBI-2")
    assert not tokens_conflict("NO3", "ORBI-1", "NO3_15N", "ORBI-2")
    assert not tokens_conflict("NO3_15N", "ORBI-1", "NO3", "ORBI-2")


def test_an_instrument_may_take_over_a_shared_token():
    """The override: the instrument's own token covers the shared one."""
    assert not tokens_conflict("NO3", "ORBI-1", "NO3", None)
    assert not tokens_conflict("NO3_15N", "ORBI-1", "NO3", None)


def test_a_shared_token_more_specific_than_a_scoped_one_is_refused():
    """The pair routing could never resolve, so it is refused up front.

    A shared `NO3_15N` beside an instrument's `NO3`: `_prefer_scoped` does not
    override, because the scoped token does not cover the shared one, so every
    file of that instrument naming 15N would match both and park - for good,
    since the instrument cannot add its own `NO3_15N` either (that overlaps its
    `NO3` within one scope). Accepting it would mean a configuration Mascope
    takes and cannot route.
    """
    assert tokens_conflict("NO3_15N", None, "NO3", "ORBI-1")
    assert tokens_conflict("NO3", "ORBI-1", "NO3_15N", None)


async def _unique(token, modes, instrument=None, ignore_id=None):
    with patch(
        f"{_UTIL}.fetch_all_ionization_modes", AsyncMock(return_value=list(modes))
    ):
        return await token_is_unique(token, ignore_id=ignore_id, instrument=instrument)


@pytest.mark.asyncio
async def test_a_token_may_be_reused_on_another_instrument():
    """Without this the feature is unreachable: validation would refuse it."""
    assert await _unique("NO3", [NITRATE_ON_1], instrument="ORBI-2")


@pytest.mark.asyncio
async def test_a_token_may_not_be_reused_on_the_same_instrument():
    assert not await _unique("NO3", [NITRATE_ON_1], instrument="ORBI-1")


@pytest.mark.asyncio
async def test_a_longer_shared_token_is_refused_for_a_scoped_mode():
    """The dead end, through the validation a caller actually hits."""
    shared_longer = _mode("Nitrate 15N shared", "NO3_15N", "-")
    assert not await _unique("NO3", [shared_longer], instrument="ORBI-1")


@pytest.mark.asyncio
async def test_a_scoped_token_may_repeat_an_unscoped_one():
    """The override the scope exists for, so not a collision.

    Both match that instrument's files, and `_prefer_scoped` resolves it in the
    instrument's favour. Refusing it here would forbid the case the CHANGELOG,
    the design note and the database's own indexes all describe - and it did,
    until this test was written the other way round.
    """
    assert await _unique("NO3", [NITRATE_ANY], instrument="ORBI-1")


@pytest.mark.asyncio
async def test_an_unscoped_token_may_repeat_a_scoped_one():
    """The same case from the other side: adding the shared mode afterwards."""
    assert await _unique("NO3", [NITRATE_ON_1], instrument=None)


@pytest.mark.asyncio
async def test_two_unscoped_modes_still_collide():
    """Nothing breaks the tie between them."""
    assert not await _unique("NO3", [NITRATE_ANY], instrument=None)


@pytest.mark.asyncio
async def test_widening_a_scope_is_compared_with_the_other_unscoped_modes():
    """What the update path checks when only the scope changes.

    The widened mode is unscoped from then on, so it is compared with the
    unscoped ones - and collides with them, which is the refusal.
    """
    assert not await _unique(
        "NO3", [NITRATE_ANY, NITRATE_ON_1], instrument=None, ignore_id="id-Nitrate 15N"
    )


@pytest.mark.asyncio
async def test_overlap_is_still_refused_within_a_scope():
    """A token containing another would make one name match both."""
    assert not await _unique("NO3_15N", [NITRATE_ON_1], instrument="ORBI-1")
    assert not await _unique("NO", [NITRATE_ON_1], instrument="ORBI-1")


@pytest.mark.asyncio
async def test_a_scope_spelled_differently_is_the_same_scope():
    assert not await _unique("NO3", [NITRATE_ON_1], instrument="orbi-1")


@pytest.mark.asyncio
async def test_overlap_across_named_instruments_is_allowed():
    assert await _unique("NO3_15N", [NITRATE_ON_1], instrument="ORBI-2")


@pytest.mark.asyncio
async def test_a_mode_does_not_collide_with_itself_when_updated():
    assert await _unique(
        "NO3", [NITRATE_ON_1], instrument="ORBI-1", ignore_id="id-Nitrate 15N"
    )


# --- the case an instrument name is recorded in does not matter -------------


@pytest.mark.asyncio
async def test_a_scoped_mode_matches_its_instrument_whatever_the_case():
    """SampleFile.instrument is recorded with inconsistent case.

    A mode scoped to one spelling has to match the files recorded under the
    other, or they park while an operator looks at a setting that reads right.
    """
    for recorded in ("ORBI-1", "orbi-1", "Orbi-1"):
        resolved = await _resolve(_file("x_NO3_ambient", "-", recorded), [NITRATE_ON_1])
        assert resolved == [NITRATE_ON_1], recorded


def test_applies_to_instrument_folds_the_case():
    assert applies_to_instrument(NITRATE_ON_1, "orbi-1")
    assert applies_to_instrument(NITRATE_ON_1, " ORBI-1 ")
    assert not applies_to_instrument(NITRATE_ON_1, "orbi-2")


# --- the override covers one token, not the whole polarity ------------------


@pytest.mark.asyncio
async def test_two_unrelated_tokens_are_ambiguous_even_with_a_scoped_match():
    """A name that says two chemistries is a fault, not an override.

    An unscoped BR and a scoped NO3 both matching one name was refused and
    parked for a person before the scope existed. Dropping every unscoped match
    of the polarity would bind it to the scoped mode with no review.
    """
    with pytest.raises(ValueError) as raised:
        await _resolve(
            _file("ORBI-1_BR_NO3_ambient", "-", "ORBI-1"),
            [BROMIDE_ANY, NITRATE_ON_1],
        )
    assert "2 modes match polarity -" in str(raised.value)


@pytest.mark.asyncio
async def test_the_override_still_applies_where_the_tokens_overlap():
    """The case it is for: the same token, and a longer one containing it."""
    longer = _mode("Nitrate 15N long", "NO3_15N", "-", instrument="ORBI-1")
    resolved = await _resolve(
        _file("ORBI-1_NO3_15N_ambient", "-", "ORBI-1"), [NITRATE_ANY, longer]
    )
    assert resolved == [longer]


@pytest.mark.asyncio
async def test_the_override_does_not_reach_across_polarities():
    """A scoped mode in one polarity leaves the other polarity's alone.

    The polarity and the token overlap are both conditions of the override, and
    only a case where the tokens *do* overlap can tell them apart: an instrument
    that means its own thing by "NO3" in negative mode, while positive "NO3" is
    still the shared one. Dropping the polarity from the test would take the
    positive mode away and leave that polarity with nothing to bind to, so a
    dual-polarity file would be refused outright.
    """
    positive_shared = _mode("Nitrate positive", "NO3", "+")

    # No shared negative NO3 beside them: two unscoped modes cannot both hold
    # the token (uq_ionization_mode_token_global), so a test naming that pair
    # would pin a configuration the database refuses.
    resolved = await _resolve(
        _file("ORBI-1_NO3_switching", "-+", "ORBI-1"),
        [NITRATE_ON_1, positive_shared],
    )

    assert resolved == [NITRATE_ON_1, positive_shared]


@pytest.mark.asyncio
async def test_a_shorter_scoped_token_does_not_beat_a_longer_shared_one():
    """The other way round is not an override, it is a worse reading.

    A shared NO3_15N beside an instrument's NO3: a file named ..._NO3_15N_...
    carries both tokens, and the shared mode is the one that reads the name
    precisely. Letting the scope win on the shorter token would bind a 15N file
    to the instrument's plain nitrate.

    Reached here by resolving directly, because that pair cannot be configured -
    `tokens_conflict` refuses it, so nobody meets this in the app. Kept because
    the resolver must not depend on validation having run: a mode edited around
    it, or a row seeded another way, would otherwise bind to the wrong
    chemistry rather than parking.
    """
    shared_longer = _mode("Nitrate 15N shared", "NO3_15N", "-")

    with pytest.raises(ValueError) as raised:
        await _resolve(
            _file("ORBI-1_NO3_15N_ambient", "-", "ORBI-1"),
            [shared_longer, NITRATE_ON_1],
        )

    assert "2 modes match polarity -" in str(raised.value)


def test_a_scoped_token_covers_the_shared_one_only_one_way():
    from mascope_backend.api.new.ionization.modes.util import _token_covers

    assert _token_covers("NO3", "NO3"), "equal tokens"
    assert _token_covers("NO3_15N", "NO3"), "a longer scoped token"
    assert not _token_covers("NO3", "NO3_15N"), "a longer shared token"
    assert not _token_covers("BR", "NO3"), "unrelated"
