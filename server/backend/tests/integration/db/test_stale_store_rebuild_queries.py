"""
Tests: the queries of the rebuild of peak stores their files no longer read
back.

The rest of ``rebuild_stale_peak_stores`` is tested hermetically
(``tests/unit/db/test_rebuild_stale_peak_stores.py`` and
``tests/unit/libraries/signal/test_stale_store_rebuild.py``). Two things
only a database can show: that it offers Orbitrap files and no others, and
that a file's samples are found in every batch that holds one - the
pipeline's and every copy a person made, which a re-processing refuses to
touch and the rebuild keeps.
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
from mascope_backend.db.scripts import rebuild_stale_peak_stores as script


@pytest_asyncio.fixture
async def files(async_session_factory):
    """Adds sample files, each with the samples asked for, and cleans up.

    A pipeline sample is the shape ``_pipeline_item`` reads: an ACQUISITION
    item, in an ACQUISITION batch, of an ACQUISITION dataset, in a system
    workspace. A person's is an analysis sample in a batch of their own
    workspace's dataset.
    """
    made: dict[str, list] = {"files": [], "items": [], "workspaces": []}
    batches: dict[str, SampleBatch] = {}

    async def batch_of(kind: str) -> SampleBatch:
        if kind not in batches:
            pipeline = kind == "pipeline"
            workspace = Workspace(
                workspace_id=gen_id(),
                workspace_name=f"Stale store rebuild {kind} {gen_id(6)}",
                is_system=pipeline,
            )
            dataset = Dataset(
                dataset_id=gen_id(),
                workspace_id=workspace.workspace_id,
                dataset_name=f"Dataset {gen_id(6)}",
                dataset_type="ACQUISITION" if pipeline else "ANALYSIS",
                instrument=f"instrument-{gen_id(8)}",
            )
            batch = SampleBatch(
                sample_batch_id=gen_id(),
                dataset_id=dataset.dataset_id,
                sample_batch_name=f"Batch {gen_id(6)}",
                sample_batch_type="ACQUISITION" if pipeline else "ANALYSIS",
            )
            async with async_session_factory() as session:
                session.add_all([workspace, dataset, batch])
                await session.commit()
            made["workspaces"].append(workspace.workspace_id)
            batches[kind] = batch
        return batches[kind]

    async def add(instrument_type: str = "orbi", samples: tuple[str, ...] = ()):
        when = datetime(2026, 1, 1, tzinfo=timezone.utc)
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

        for kind in samples:
            batch = await batch_of(kind)
            item = SampleItem(
                sample_item_id=gen_id(),
                sample_batch_id=batch.sample_batch_id,
                sample_file_id=sample_file.sample_file_id,
                sample_item_name=f"Item {gen_id(6)}",
                sample_item_type="ACQUISITION" if kind == "pipeline" else "ANALYSIS",
            )
            async with async_session_factory() as session:
                session.add(item)
                await session.commit()
            made["items"].append(item.sample_item_id)

        return sample_file

    yield add

    async with async_session_factory() as session:
        if made["items"]:
            await session.execute(
                delete(SampleItem).where(SampleItem.sample_item_id.in_(made["items"]))
            )
        await session.execute(
            delete(SampleFile).where(SampleFile.sample_file_id.in_(made["files"]))
        )
        if made["workspaces"]:
            await session.execute(
                delete(Workspace).where(Workspace.workspace_id.in_(made["workspaces"]))
            )
        await session.commit()


@pytest.mark.asyncio
async def test_only_orbitrap_files_are_offered(files):
    """A TOF file's scans are not selected by the reader whose rule changed."""
    orbitrap = await files("orbi")
    tof = await files("tof")

    offered = {row["sample_file_id"]: row for row in await script._candidates()}

    assert offered[orbitrap.sample_file_id] == {
        "sample_file_id": orbitrap.sample_file_id,
        "filename": orbitrap.filename,
        "instrument": orbitrap.instrument,
    }
    assert tof.sample_file_id not in offered


@pytest.mark.asyncio
async def test_every_sample_of_a_file_is_found_in_every_batch(
    files, async_session_factory
):
    """The pipeline's sample and a person's copy of it, in a workspace of
    their own: both had their matches made on the store being replaced."""
    copied = await files(samples=("pipeline", "person"))
    other = await files(samples=("pipeline",))

    samples, batches = await script._samples_of(copied.sample_file_id)

    async with async_session_factory() as session:
        items = (
            await session.execute(
                select(SampleItem.sample_item_id, SampleItem.sample_batch_id).where(
                    SampleItem.sample_file_id == copied.sample_file_id
                )
            )
        ).all()
    assert sorted(samples) == sorted(item for item, _ in items)
    assert sorted(batches) == sorted({batch for _, batch in items})
    assert len(batches) == 2

    others, _ = await script._samples_of(other.sample_file_id)
    assert not set(others) & set(samples)


@pytest.mark.asyncio
async def test_a_file_with_no_samples_has_none_to_rematch(files):
    alone = await files()

    assert await script._samples_of(alone.sample_file_id) == ([], [])
