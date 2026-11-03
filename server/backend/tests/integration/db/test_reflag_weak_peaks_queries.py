"""
Tests: the database side of ``reflag_orbitrap_weak_peaks``.

The rest of the script is decided in memory and on stores in a temporary
filestore, and tested hermetically. Three things only a database can show:
that it offers Orbitrap files and no others, newest first; which batches
hold a sample of a file; and that those batches are set to ``rematch``.
"""

from datetime import datetime, timedelta, timezone

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
from mascope_backend.db.scripts import reflag_orbitrap_weak_peaks as script


@pytest_asyncio.fixture
async def world(async_session_factory):
    """Adds sample files and batches holding their samples, and cleans up."""
    made: dict[str, list] = {"files": [], "workspaces": []}
    workspace = Workspace(
        workspace_id=gen_id(), workspace_name=f"Reflag weak peaks {gen_id(6)}"
    )
    dataset = Dataset(
        dataset_id=gen_id(),
        workspace_id=workspace.workspace_id,
        dataset_name=f"Dataset {gen_id(6)}",
        dataset_type="ANALYSIS",
        instrument=f"instrument-{gen_id(8)}",
    )
    async with async_session_factory() as session:
        session.add_all([workspace, dataset])
        await session.commit()
    made["workspaces"].append(workspace.workspace_id)

    async def batch() -> str:
        sample_batch = SampleBatch(
            sample_batch_id=gen_id(),
            dataset_id=dataset.dataset_id,
            sample_batch_name=f"Batch {gen_id(6)}",
            sample_batch_type="ANALYSIS",
            status="ready",
        )
        async with async_session_factory() as session:
            session.add(sample_batch)
            await session.commit()
        return sample_batch.sample_batch_id

    async def file(instrument_type="orbi", minutes=0, batches=()) -> SampleFile:
        when = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=minutes)
        sample_file = SampleFile(
            sample_file_id=gen_id(),
            filename=f"{gen_id(10)}.raw",
            instrument=f"instrument-{gen_id(8)}",
            instrument_type=instrument_type,
            datetime=when.replace(tzinfo=None),
            datetime_utc=when,
            length=60.0,
            range=[40.0, 600.0],
            polarity="-",
        )
        async with async_session_factory() as session:
            session.add(sample_file)
            await session.commit()
        made["files"].append(sample_file.sample_file_id)
        for sample_batch_id in batches:
            # Two samples of the file in each batch: the batch is named once
            for _ in range(2):
                async with async_session_factory() as session:
                    session.add(
                        SampleItem(
                            sample_item_id=gen_id(),
                            sample_batch_id=sample_batch_id,
                            sample_file_id=sample_file.sample_file_id,
                            sample_item_name=f"Item {gen_id(6)}",
                            sample_item_type="ANALYSIS",
                        )
                    )
                    await session.commit()
        return sample_file

    async def status(sample_batch_id: str) -> str:
        async with async_session_factory() as session:
            return (
                await session.execute(
                    select(SampleBatch.status).where(
                        SampleBatch.sample_batch_id == sample_batch_id
                    )
                )
            ).scalar_one()

    yield file, batch, status

    async with async_session_factory() as session:
        await session.execute(
            delete(SampleFile).where(SampleFile.sample_file_id.in_(made["files"]))
        )
        await session.execute(
            delete(Workspace).where(Workspace.workspace_id.in_(made["workspaces"]))
        )
        await session.commit()


@pytest.mark.asyncio
async def test_orbitrap_files_are_offered_newest_first(world):
    file, _batch, _status = world
    oldest = await file(minutes=0)
    newest = await file(minutes=20)
    tof = await file("tof", minutes=10)

    offered = [c for c in await script._candidates()]
    names = [c["filename"] for c in offered]

    assert tof.filename not in names
    assert [n for n in names if n in {oldest.filename, newest.filename}] == [
        newest.filename,
        oldest.filename,
    ]
    (row,) = [c for c in offered if c["filename"] == newest.filename]
    assert row["sample_file_id"] == newest.sample_file_id
    assert row["instrument"] == newest.instrument


@pytest.mark.asyncio
async def test_the_batches_of_a_files_samples_are_set_to_rematch(world):
    file, batch, status = world
    first, second, elsewhere = await batch(), await batch(), await batch()
    touched = await file(batches=(first, second))
    await file(batches=(elsewhere,))

    batches = await script._batches_of(touched.sample_file_id)
    await script._queue_rematch(batches)

    assert sorted(batches) == sorted([first, second])
    assert await status(first) == "rematch"
    assert await status(second) == "rematch"
    assert await status(elsewhere) == "ready"
