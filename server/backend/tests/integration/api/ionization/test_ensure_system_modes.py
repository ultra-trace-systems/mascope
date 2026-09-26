"""
Tests: seeding the ionization modes Mascope ships, the mode pass on its own.

The seeder runs at every start, so what it does on a server that already has
data matters more than what it does on an empty one.

A start creates the shipped mechanisms first (``test_ensure_system_mechanisms``),
so this pass finds them. What these pin is the pass itself: a chemistry is
seeded only where its mechanisms are present, under the right polarity, and
the pass creates none. Creating a mechanism is the other pass's work, with the
target ions it has to build for every compound in the library.
"""

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from mascope_backend.db import IonizationMechanism, IonizationMode
from mascope_backend.db.admin.ionization.ensure_system_modes import (
    ensure_system_ionization_modes,
)
from mascope_backend.db.id import gen_id
from mascope_backend.ionization_catalogue import SYSTEM_MODES
from mascope_tools.composition.mechanism_notation import mechanism_spellings


# "Ambient, negative" is the catalogue's simplest entry: one mechanism,
# electron capture - "[M]-.", or "-" on a row still holding the legacy spelling.
_AMBIENT = next(entry for entry in SYSTEM_MODES if entry[0] == "ambient-negative")
_KEY, _MODE_ID, _NAME, _POLARITY, _MECHANISMS = _AMBIENT
_SPELLINGS = mechanism_spellings(_MECHANISMS)


@pytest_asyncio.fixture
async def clean_slate(async_session_factory):
    """No seeded modes and no electron-capture mechanism, restored afterwards."""

    async def _clear():
        async with async_session_factory() as session:
            await session.execute(
                delete(IonizationMode).where(IonizationMode.system_key.is_not(None))
            )
            await session.execute(
                delete(IonizationMechanism).where(
                    IonizationMechanism.ionization_mechanism.in_(_SPELLINGS)
                )
            )
            await session.commit()

    await _clear()
    yield
    await _clear()


async def _mode(async_session_factory, key):
    async with async_session_factory() as session:
        return (
            await session.execute(
                select(IonizationMode).where(IonizationMode.system_key == key)
            )
        ).scalar_one_or_none()


async def _add_mechanism(async_session_factory, notation, polarity):
    mechanism_id = gen_id(11)
    async with async_session_factory() as session:
        session.add(
            IonizationMechanism(
                ionization_mechanism_id=mechanism_id,
                ionization_mechanism_polarity=polarity,
                ionization_mechanism=notation,
            )
        )
        await session.commit()
    return mechanism_id


@pytest.mark.asyncio
async def test_a_chemistry_without_its_mechanism_is_not_seeded(
    async_session_factory, clean_slate
):
    counts = await ensure_system_ionization_modes()
    assert counts["no_mechanism"] > 0
    assert await _mode(async_session_factory, _KEY) is None


@pytest.mark.asyncio
async def test_the_mode_pass_creates_no_mechanisms(async_session_factory, clean_slate):
    """A mechanism is created with its target ions, by the mechanism pass."""
    async with async_session_factory() as session:
        before = (
            (await session.execute(select(IonizationMechanism.ionization_mechanism_id)))
            .scalars()
            .all()
        )

    await ensure_system_ionization_modes()

    async with async_session_factory() as session:
        after = (
            (await session.execute(select(IonizationMechanism.ionization_mechanism_id)))
            .scalars()
            .all()
        )
    assert sorted(after) == sorted(before)


@pytest.mark.asyncio
@pytest.mark.parametrize("notation", _SPELLINGS)
async def test_the_chemistry_is_seeded_once_its_mechanism_exists(
    async_session_factory, clean_slate, notation
):
    """In either spelling: a row the migration has not rewritten yet is the
    same mechanism the catalogue names."""
    mechanism_id = await _add_mechanism(async_session_factory, notation, "-")

    await ensure_system_ionization_modes()

    mode = await _mode(async_session_factory, _KEY)
    assert mode is not None
    assert mode.ionization_mode_name == _NAME
    assert mode.ionization_mode_token is None
    assert mode.calibration_collection_id is None
    assert mode.ionization_mechanism_ids == [mechanism_id]


@pytest.mark.asyncio
async def test_a_mechanism_stored_under_the_wrong_polarity_is_not_used(
    async_session_factory, clean_slate
):
    """A mismatch would make the mode uneditable: every PATCH revalidates it."""
    await _add_mechanism(async_session_factory, "[M]-.", "+")

    await ensure_system_ionization_modes()

    assert await _mode(async_session_factory, _KEY) is None


@pytest.mark.asyncio
async def test_running_twice_changes_nothing(async_session_factory, clean_slate):
    await _add_mechanism(async_session_factory, "[M]-.", "-")
    await ensure_system_ionization_modes()
    first = await _mode(async_session_factory, _KEY)

    counts = await ensure_system_ionization_modes()

    again = await _mode(async_session_factory, _KEY)
    assert counts["seeded"] == 0
    assert again.ionization_mode_id == first.ionization_mode_id


@pytest.mark.asyncio
async def test_a_row_left_by_a_downgrade_is_claimed_not_duplicated(
    async_session_factory, clean_slate
):
    """A downgrade drops the key but keeps the row, under its own id."""
    mechanism_id = await _add_mechanism(async_session_factory, "[M]-.", "-")
    async with async_session_factory() as session:
        session.add(
            IonizationMode(
                ionization_mode_id=_MODE_ID,
                ionization_mode_name=_NAME,
                ionization_mode_token=None,
                ionization_mode_polarity=_POLARITY,
                ionization_mechanism_ids=[mechanism_id],
                system_key=None,
            )
        )
        await session.commit()

    counts = await ensure_system_ionization_modes()

    async with async_session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(IonizationMode).where(
                        IonizationMode.ionization_mode_name == _NAME
                    )
                )
            )
            .scalars()
            .all()
        )
    assert counts["claimed"] == 1
    assert [row.ionization_mode_id for row in rows] == [_MODE_ID]


@pytest.mark.asyncio
async def test_a_name_the_deployment_already_uses_is_left_alone(
    async_session_factory, clean_slate
):
    """Two rows under one name turn a later create into a 500."""
    mechanism_id = await _add_mechanism(async_session_factory, "[M]-.", "-")
    theirs = gen_id()
    async with async_session_factory() as session:
        session.add(
            IonizationMode(
                ionization_mode_id=theirs,
                ionization_mode_name=_NAME,
                ionization_mode_token=None,
                ionization_mode_polarity=_POLARITY,
                ionization_mechanism_ids=[mechanism_id],
                system_key=None,
            )
        )
        await session.commit()

    counts = await ensure_system_ionization_modes()

    async with async_session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(IonizationMode).where(
                        IonizationMode.ionization_mode_name == _NAME
                    )
                )
            )
            .scalars()
            .all()
        )
        await session.execute(
            delete(IonizationMode).where(IonizationMode.ionization_mode_id == theirs)
        )
        await session.commit()
    assert counts["name_taken"] == 1
    assert [row.ionization_mode_id for row in rows] == [theirs]


@pytest.mark.asyncio
async def test_a_leftover_row_that_was_edited_is_not_claimed(
    async_session_factory, clean_slate
):
    """Claiming it would lock somebody's edit under the catalogue's key.

    The guard then refuses to undo that edit, token included, so the row
    would read as Mascope's while saying something else.
    """
    mechanism_id = await _add_mechanism(async_session_factory, "[M]-.", "-")
    async with async_session_factory() as session:
        session.add(
            IonizationMode(
                ionization_mode_id=_MODE_ID,
                ionization_mode_name=f"{_NAME} (ours now)",
                ionization_mode_token="OURS",
                ionization_mode_polarity=_POLARITY,
                ionization_mechanism_ids=[mechanism_id],
                system_key=None,
            )
        )
        await session.commit()

    counts = await ensure_system_ionization_modes()

    async with async_session_factory() as session:
        row = await session.get(IonizationMode, _MODE_ID)
        await session.execute(
            delete(IonizationMode).where(IonizationMode.ionization_mode_id == _MODE_ID)
        )
        await session.commit()
    assert counts["edited"] == 1
    assert counts["claimed"] == 0
    assert row.system_key is None
    assert row.ionization_mode_token == "OURS"
