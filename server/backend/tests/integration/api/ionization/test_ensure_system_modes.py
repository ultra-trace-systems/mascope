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

from mascope_backend.db import (
    Dataset,
    IonizationMechanism,
    IonizationMode,
    SampleBatch,
    Workspace,
)
from mascope_backend.db.admin.ionization.ensure_system_modes import (
    ensure_system_ionization_modes,
)
from mascope_backend.db.id import gen_id
from mascope_backend.ionization_catalogue import NAMED_BEFORE, SYSTEM_MODES
from mascope_tools.composition.mechanism_notation import mechanism_spellings


# Negative charge transfer is the catalogue's simplest entry: one mechanism,
# electron capture - "[M]-.", or "-" on a row still holding the legacy spelling.
# It is also one an earlier release seeded under another name.
_AMBIENT = next(entry for entry in SYSTEM_MODES if entry[0] == "ambient-negative")
_KEY, _MODE_ID, _NAME, _POLARITY, _MECHANISMS = _AMBIENT
_SPELLINGS = mechanism_spellings(_MECHANISMS)
(_EARLIER_NAME,) = NAMED_BEFORE[_KEY]


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


# --- versions --------------------------------------------------------------


async def _add_mode(async_session_factory, mode_id, **fields):
    async with async_session_factory() as session:
        session.add(
            IonizationMode(
                ionization_mode_id=mode_id,
                ionization_mode_polarity=_POLARITY,
                **fields,
            )
        )
        await session.commit()


async def _drop_modes(async_session_factory, *mode_ids):
    async with async_session_factory() as session:
        await session.execute(
            delete(IonizationMode).where(
                IonizationMode.ionization_mode_id.in_(mode_ids)
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_a_superseded_row_under_the_catalogue_id_is_left_alone(
    async_session_factory, clean_slate
):
    """Neither claimed - a key on a version nothing reads - nor seeded over,
    which its id would refuse."""
    mechanism_id = await _add_mechanism(async_session_factory, _MECHANISMS[0], "-")
    successor = gen_id()
    await _add_mode(
        async_session_factory,
        _MODE_ID,
        ionization_mode_name=_NAME,
        ionization_mechanism_ids=[mechanism_id],
        is_active=False,
    )
    await _add_mode(
        async_session_factory,
        successor,
        ionization_mode_name=f"{_NAME} edited",
        ionization_mechanism_ids=[mechanism_id],
        version=2,
        supersedes_id=_MODE_ID,
    )
    try:
        counts = await ensure_system_ionization_modes()
        async with async_session_factory() as session:
            superseded = await session.get(IonizationMode, _MODE_ID)
    finally:
        await _drop_modes(async_session_factory, successor, _MODE_ID)

    assert counts["edited"] == 1
    assert superseded.system_key is None
    assert await _mode(async_session_factory, _KEY) is None


@pytest.mark.asyncio
async def test_a_name_only_a_superseded_version_holds_is_free_to_seed(
    async_session_factory, clean_slate
):
    await _add_mechanism(async_session_factory, _MECHANISMS[0], "-")
    old = gen_id()
    await _add_mode(
        async_session_factory,
        old,
        ionization_mode_name=_NAME,
        ionization_mechanism_ids=[],
        is_active=False,
    )
    try:
        counts = await ensure_system_ionization_modes()
        seeded = await _mode(async_session_factory, _KEY)
    finally:
        await _drop_modes(async_session_factory, old)

    assert counts["name_taken"] == 0
    assert seeded is not None
    assert seeded.ionization_mode_name == _NAME


# --- a chemistry renamed under its key -------------------------------------


async def _seeded_as_an_earlier_release(async_session_factory, **fields):
    """The chemistry's mode as an earlier release seeded it, under its key."""
    mechanism_id = await _add_mechanism(async_session_factory, _MECHANISMS[0], "-")
    await _add_mode(
        async_session_factory,
        _MODE_ID,
        **{
            "ionization_mode_name": _EARLIER_NAME,
            "ionization_mechanism_ids": [mechanism_id],
            "system_key": _KEY,
            **fields,
        },
    )


@pytest.mark.asyncio
async def test_a_mode_under_the_name_an_earlier_release_gave_it_is_renamed(
    async_session_factory, clean_slate
):
    """In place, as an edit of a name is made: the same row, the same version."""
    await _seeded_as_an_earlier_release(async_session_factory)

    counts = await ensure_system_ionization_modes()
    again = await ensure_system_ionization_modes()

    mode = await _mode(async_session_factory, _KEY)
    assert counts["renamed"] == 1
    assert again["renamed"] == again["not_renamed"] == 0
    assert mode.ionization_mode_id == _MODE_ID
    assert mode.ionization_mode_name == _NAME
    assert (mode.version, mode.is_active) == (1, True)


@pytest.mark.asyncio
async def test_a_keyed_mode_under_a_name_no_release_gave_it_is_left_as_it_is(
    async_session_factory, clean_slate
):
    """Only a name the catalogue lists as its own earlier one is replaced."""
    await _seeded_as_an_earlier_release(
        async_session_factory, ionization_mode_name="Named some other way"
    )

    counts = await ensure_system_ionization_modes()

    mode = await _mode(async_session_factory, _KEY)
    assert counts["renamed"] == counts["not_renamed"] == 0
    assert mode.ionization_mode_name == "Named some other way"


@pytest.mark.asyncio
async def test_the_rename_waits_while_another_mode_holds_the_name(
    async_session_factory, clean_slate
):
    """Two rows under one name turn a later create into a 500."""
    await _seeded_as_an_earlier_release(async_session_factory)
    theirs = gen_id()
    await _add_mode(
        async_session_factory,
        theirs,
        ionization_mode_name=_NAME,
        ionization_mechanism_ids=[],
    )
    try:
        counts = await ensure_system_ionization_modes()
        mode = await _mode(async_session_factory, _KEY)
    finally:
        await _drop_modes(async_session_factory, theirs)

    assert counts["renamed"] == 0
    assert counts["not_renamed"] == 1
    assert mode.ionization_mode_name == _EARLIER_NAME


async def _batch_named(async_session_factory, name, batch_type):
    """A daily batch named as one of a mode's is, and the dataset it is in."""
    workspace_id, dataset_id, batch_id = gen_id(), gen_id(), gen_id()
    async with async_session_factory() as session:
        session.add(
            Workspace(
                workspace_id=workspace_id,
                workspace_name=f"Rename test {workspace_id}",
                is_system=True,
            )
        )
        session.add(
            Dataset(
                dataset_id=dataset_id,
                workspace_id=workspace_id,
                dataset_name="2026",
                dataset_type="ACQUISITION",
            )
        )
        session.add(
            SampleBatch(
                sample_batch_id=batch_id,
                dataset_id=dataset_id,
                sample_batch_name=f"2026-10-27 {name} acquisition",
                sample_batch_type=batch_type,
                polarity=_POLARITY,
            )
        )
        await session.commit()
    return workspace_id, dataset_id, batch_id


async def _drop_batch(async_session_factory, workspace_id, dataset_id, batch_id):
    async with async_session_factory() as session:
        await session.execute(
            delete(SampleBatch).where(SampleBatch.sample_batch_id == batch_id)
        )
        await session.execute(delete(Dataset).where(Dataset.dataset_id == dataset_id))
        await session.execute(
            delete(Workspace).where(Workspace.workspace_id == workspace_id)
        )
        await session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("named_for", "batch_type", "renamed"),
    [
        ("earlier", "ACQUISITION", False),
        ("current", "ACQUISITION", False),
        ("earlier", "ANALYSIS", True),
    ],
    ids=["old-name", "new-name", "not-an-acquisition-batch"],
)
async def test_the_rename_waits_while_acquisition_batches_are_named_for_it(
    async_session_factory, clean_slate, named_for, batch_type, renamed
):
    """A file's daily batch is found by its mode's name, so the day's next
    file would start a second batch, or join another mode's. An edit of the
    name is refused there too; a batch a person named is not a daily one."""
    await _seeded_as_an_earlier_release(async_session_factory)
    batch = await _batch_named(
        async_session_factory,
        _EARLIER_NAME if named_for == "earlier" else _NAME,
        batch_type,
    )
    try:
        counts = await ensure_system_ionization_modes()
        mode = await _mode(async_session_factory, _KEY)
    finally:
        await _drop_batch(async_session_factory, *batch)

    assert counts["renamed"] == int(renamed)
    assert counts["not_renamed"] == int(not renamed)
    assert mode.ionization_mode_name == (_NAME if renamed else _EARLIER_NAME)


@pytest.mark.asyncio
async def test_a_row_a_downgrade_left_under_an_earlier_name_is_claimed_and_renamed(
    async_session_factory, clean_slate
):
    """It is still what was seeded, by the release that seeded it."""
    await _seeded_as_an_earlier_release(async_session_factory, system_key=None)
    try:
        counts = await ensure_system_ionization_modes()
        mode = await _mode(async_session_factory, _KEY)
    finally:
        await _drop_modes(async_session_factory, _MODE_ID)

    assert (counts["claimed"], counts["renamed"]) == (1, 1)
    assert mode.ionization_mode_id == _MODE_ID
    assert mode.ionization_mode_name == _NAME
