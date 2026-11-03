"""
Tests: what the sample item TIC fix reads and writes in the database.

The run is decided in memory and tested hermetically
(``tests/unit/db/test_fix_sample_item_tic.py``). Its two queries are pinned
here: one target per file and polarity whose items store a TIC, and a write
that reaches every item of that pair - a copy in another batch included -
and no other.
"""

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from mascope_backend.db import (
    Dataset,
    SampleBatch,
    SampleFile,
    SampleItem,
    Workspace,
)
from mascope_backend.db.id import gen_id
from mascope_backend.db.scripts import fix_sample_item_tic as script


@pytest_asyncio.fixture
async def home(async_session_factory):
    """A workspace with two batches to put items in, removed afterwards."""
    workspace = Workspace(
        workspace_id=gen_id(), workspace_name=f"TIC fix test {gen_id(6)}"
    )
    dataset = Dataset(
        dataset_id=gen_id(),
        workspace_id=workspace.workspace_id,
        dataset_name=f"Dataset {gen_id(6)}",
    )
    batches = [
        SampleBatch(
            sample_batch_id=gen_id(),
            dataset_id=dataset.dataset_id,
            sample_batch_name=f"Batch {gen_id(6)}",
        )
        for _ in range(2)
    ]
    made = {"files": []}
    async with async_session_factory() as session:
        session.add_all([workspace, dataset, *batches])
        await session.commit()

    async def add_file():
        sample_file = SampleFile(
            sample_file_id=gen_id(),
            filename=f"{gen_id(10)}.raw",
            instrument=f"instrument-{gen_id(8)}",
            instrument_type="orbi",
            datetime=datetime(2026, 1, 1),
            datetime_utc=datetime(2026, 1, 1, tzinfo=timezone.utc),
            length=60.0,
            range=[40.0, 600.0],
            polarity="+",
        )
        async with async_session_factory() as session:
            session.add(sample_file)
            await session.commit()
        made["files"].append(sample_file.sample_file_id)
        return sample_file

    async def add_item(sample_file, polarity, tic, batch=0):
        item = SampleItem(
            sample_item_id=gen_id(),
            sample_batch_id=batches[batch].sample_batch_id,
            sample_file_id=sample_file.sample_file_id,
            sample_item_name=f"Item {gen_id(6)}",
            sample_item_type="ACQUISITION",
            polarity=polarity,
            tic=tic,
        )
        async with async_session_factory() as session:
            session.add(item)
            await session.commit()
        return item.sample_item_id

    async def tic_of(item_id):
        async with async_session_factory() as session:
            return await session.scalar(
                select(SampleItem.tic).where(SampleItem.sample_item_id == item_id)
            )

    yield add_file, add_item, tic_of

    async with async_session_factory() as session:
        await session.execute(
            delete(SampleItem).where(SampleItem.sample_file_id.in_(made["files"]))
        )
        await session.execute(
            delete(SampleFile).where(SampleFile.sample_file_id.in_(made["files"]))
        )
        await session.execute(
            delete(Workspace).where(Workspace.workspace_id == workspace.workspace_id)
        )
        await session.commit()


@pytest.mark.asyncio
async def test_one_target_per_file_and_polarity_with_a_stored_tic(home):
    add_file, add_item, _ = home
    file_a, file_b = await add_file(), await add_file()
    await add_item(file_a, "+", 10.0)
    await add_item(file_a, "+", 12.0, batch=1)
    await add_item(file_a, "-", 20.0)
    await add_item(file_a, None, 30.0)
    await add_item(file_b, "+", None)

    targets = {
        (t["filename"], t["polarity"]): t
        for t in await script._targets()
        if t["filename"] in {file_a.filename, file_b.filename}
    }

    assert set(targets) == {
        (file_a.filename, "+"),
        (file_a.filename, "-"),
        (file_a.filename, None),
    }
    plus = targets[(file_a.filename, "+")]
    assert (plus["items"], plus["stored_min"], plus["stored_max"]) == (2, 10.0, 12.0)


@pytest.mark.asyncio
async def test_the_write_reaches_every_item_of_the_pair_and_no_other(home):
    add_file, add_item, tic_of = home
    file_a, file_b = await add_file(), await add_file()
    original = await add_item(file_a, "+", 10.0)
    copy = await add_item(file_a, "+", 10.0, batch=1)
    other_polarity = await add_item(file_a, "-", 20.0)
    no_tic = await add_item(file_a, "+", None)
    other_file = await add_item(file_b, "+", 10.0)

    updated = await script._apply({"filename": file_a.filename, "polarity": "+"}, 1.5)

    assert updated == 2
    assert [await tic_of(i) for i in (original, copy)] == [1.5, 1.5]
    assert await tic_of(other_polarity) == 20.0
    assert await tic_of(no_tic) is None
    assert await tic_of(other_file) == 10.0


@pytest.mark.asyncio
async def test_an_item_with_no_polarity_is_its_own_pair(home):
    add_file, add_item, tic_of = home
    sample_file = await add_file()
    unpolarised = await add_item(sample_file, None, 30.0)
    positive = await add_item(sample_file, "+", 10.0)

    await script._apply({"filename": sample_file.filename, "polarity": None}, 3.0)

    assert await tic_of(unpolarised) == 3.0
    assert await tic_of(positive) == 10.0
