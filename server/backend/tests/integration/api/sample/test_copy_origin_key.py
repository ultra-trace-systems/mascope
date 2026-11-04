"""
Tests: a copy records which part of the acquisition record it came from.

Through the copy path as a person's copy runs it, against the database: the
key each copy is stored with, what the sample view shows of it, and that
editing a copy afterwards leaves it as it was
(``docs/dev/sample_filters_and_metadata.md``, section 4.3).
"""

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, text

from mascope_backend.api.controllers.sample.items import sample_items_controller
from mascope_backend.api.controllers.sample.lib.origin_key import origin_key
from mascope_backend.api.models.sample.items.sample_item_pydantic_model import (
    SampleItemUpdate,
)
from mascope_backend.db import (
    Dataset,
    IonizationMechanism,
    IonizationMode,
    Sample,
    SampleBatch,
    SampleFile,
    SampleItem,
    Workspace,
)
from mascope_backend.db.id import gen_id


_NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)


@pytest_asyncio.fixture(scope="module", autouse=True)
async def sample_view(async_engine):
    """The copy reads its sources through ``sample_view``, which ``create_all``
    does not make (``tests/integration/api/match/conftest.py`` says why)."""
    async with async_engine.begin() as conn:
        await conn.execute(text(Sample.drop_view()))
        await conn.execute(text(Sample.create_view()))


@pytest_asyncio.fixture
async def world(async_session_factory):
    """A file's acquisition sample, and three batches of a person's own."""
    ids = {
        "workspace": gen_id(),
        "dataset": gen_id(),
        "file": gen_id(),
        "mechanism": gen_id(),
        "mode": gen_id(),
        "acquired": gen_id(),
        "acquisition_batch": gen_id(),
        "batches": [gen_id() for _ in range(3)],
    }
    async with async_session_factory() as session:
        session.add(
            Workspace(
                workspace_id=ids["workspace"],
                workspace_name=f"Origin key {ids['workspace']}",
            )
        )
        session.add(
            Dataset(
                dataset_id=ids["dataset"],
                workspace_id=ids["workspace"],
                dataset_name=f"Origin key {ids['dataset']}",
            )
        )
        session.add(
            SampleFile(
                sample_file_id=ids["file"],
                filename=f"instrument-a_{ids['file']}.raw",
                instrument="instrument-a",
                instrument_type="orbi",
                datetime=datetime(2026, 11, 4, 12, 0),
                datetime_utc=_NOW,
                length=60.0,
                range=[40.0, 600.0],
                polarity="-",
            )
        )
        session.add(
            IonizationMechanism(
                ionization_mechanism_id=ids["mechanism"],
                ionization_mechanism_polarity="-",
                ionization_mechanism=f"[M+NO3]- {ids['mechanism']}",
            )
        )
        session.add(
            IonizationMode(
                ionization_mode_id=ids["mode"],
                ionization_mode_name=f"Origin key mode {ids['mode']}",
                ionization_mode_polarity="-",
                ionization_mechanism_ids=[ids["mechanism"]],
            )
        )
        session.add(
            SampleBatch(
                sample_batch_id=ids["acquisition_batch"],
                dataset_id=ids["dataset"],
                sample_batch_name=f"Acquisitions {ids['acquisition_batch']}",
                sample_batch_type="ACQUISITION",
                polarity="-",
            )
        )
        for batch_id in ids["batches"]:
            session.add(
                SampleBatch(
                    sample_batch_id=batch_id,
                    dataset_id=ids["dataset"],
                    sample_batch_name=f"Analysis {batch_id}",
                )
            )
        await session.flush()
        session.add(
            SampleItem(
                sample_item_id=ids["acquired"],
                sample_batch_id=ids["acquisition_batch"],
                sample_file_id=ids["file"],
                ionization_mode_id=ids["mode"],
                sample_item_name="2026-11-04 12:00:00",
                sample_item_type="ACQUISITION",
                sample_item_attributes={},
                polarity="-",
                tic=5.0,
                t0=0.0,
                t1=60.0,
                sample_item_utc_created=_NOW,
            )
        )
        await session.commit()
    yield ids
    async with async_session_factory() as session:
        await session.execute(
            delete(Workspace).where(Workspace.workspace_id == ids["workspace"])
        )
        await session.execute(
            delete(SampleFile).where(SampleFile.sample_file_id == ids["file"])
        )
        await session.execute(
            delete(IonizationMode).where(
                IonizationMode.ionization_mode_id == ids["mode"]
            )
        )
        await session.execute(
            delete(IonizationMechanism).where(
                IonizationMechanism.ionization_mechanism_id == ids["mechanism"]
            )
        )
        await session.commit()


async def _copy(item_id: str, batch_id: str) -> str:
    """Copy one sample as a person's copy does; the copy's id."""
    copied = await sample_items_controller.copy_sample_items.__wrapped__(
        sample_item_ids=[item_id], sample_batch_id=batch_id, process_id=gen_id(8)
    )
    return copied["data"][0]["sample_item_id"]


async def _key(async_session_factory, item_id: str) -> str | None:
    async with async_session_factory() as session:
        return (
            await session.execute(
                select(SampleItem.origin_key).where(
                    SampleItem.sample_item_id == item_id
                )
            )
        ).scalar_one()


@pytest.mark.asyncio
async def test_copies_of_one_part_in_two_batches_carry_its_key(
    world, async_session_factory
):
    first = await _copy(world["acquired"], world["batches"][0])
    second = await _copy(world["acquired"], world["batches"][1])

    part = origin_key(world["file"], "-", 0.0, 60.0)
    assert await _key(async_session_factory, first) == part
    assert await _key(async_session_factory, second) == part
    # The acquisition sample itself was made by no copy
    assert await _key(async_session_factory, world["acquired"]) is None


@pytest.mark.asyncio
async def test_the_sample_view_shows_the_key(world, async_session_factory):
    copy_id = await _copy(world["acquired"], world["batches"][0])

    async with async_session_factory() as session:
        shown = (
            await session.execute(
                select(Sample.origin_key).where(Sample.sample_item_id == copy_id)
            )
        ).scalar_one()

    assert shown == origin_key(world["file"], "-", 0.0, 60.0)


@pytest.mark.asyncio
async def test_a_copys_window_edited_keeps_its_key_and_passes_it_on(
    world, async_session_factory
):
    """The window is editable on a copy and the key is not: a copy of the
    edited copy still says which part the first came from."""
    copy_id = await _copy(world["acquired"], world["batches"][0])
    async with async_session_factory() as session:
        copy = await session.get(SampleItem, copy_id)
        edit = SampleItemUpdate.model_validate(copy).model_copy(
            update={"t0": 10.0, "t1": 20.0}
        )
    await sample_items_controller.update_sample_item.__wrapped__(copy_id, edit)

    part = origin_key(world["file"], "-", 0.0, 60.0)
    async with async_session_factory() as session:
        edited = await session.get(SampleItem, copy_id)
    assert (edited.t0, edited.t1, edited.origin_key) == (10.0, 20.0, part)

    assert (
        await _key(async_session_factory, await _copy(copy_id, world["batches"][2]))
        == part
    )


@pytest.mark.asyncio
async def test_the_keys_a_batch_holds_are_indexed_for_the_copies_alone(
    async_session_factory,
):
    """The index a refresh reads, as the models build it. Partial: only a
    copy carries a key, and most items are the acquisition record's own."""
    async with async_session_factory() as session:
        definition = (
            await session.execute(
                text(
                    "SELECT indexdef FROM pg_indexes WHERE indexname = "
                    "'ix_sample_item_sample_batch_id_origin_key'"
                )
            )
        ).scalar_one()

    assert definition.endswith(
        "(sample_batch_id, origin_key) WHERE (origin_key IS NOT NULL)"
    )
