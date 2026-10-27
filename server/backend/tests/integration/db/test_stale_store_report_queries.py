"""
Tests: the two queries of the report of outgrown peak stores.

The rest of ``report_stale_peak_stores`` is decided in memory and tested
hermetically (``tests/unit/db/test_report_stale_peak_stores.py``). Two
things only a database can show: that it offers Orbitrap files and no
others, and which of the files it lists are marked as holding a sample a
person made. That mark sends a file to *Detect peaks* instead of a
re-processing, which refuses such a file and nothing else.
"""

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete

from mascope_backend.db import (
    Dataset,
    SampleBatch,
    SampleFile,
    SampleItem,
    Workspace,
)
from mascope_backend.db.id import gen_id
from mascope_backend.db.scripts import report_stale_peak_stores as script


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
                workspace_name=f"Stale store report {kind} {gen_id(6)}",
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
async def test_a_file_with_a_sample_a_person_made_is_marked(files):
    """Re-processing refuses it, so it is sent to *Detect peaks*; a file
    holding only the pipeline's samples, or none, is re-processed, which
    also calibrates it on the peaks detected again."""
    pipeline_only = await files(samples=("pipeline",))
    person_too = await files(samples=("pipeline", "person"))
    person_only = await files(samples=("person",))
    none = await files()

    marked = await script._with_samples_a_person_made(
        [
            pipeline_only.sample_file_id,
            person_too.sample_file_id,
            person_only.sample_file_id,
            none.sample_file_id,
        ]
    )

    assert marked == {person_too.sample_file_id, person_only.sample_file_id}


@pytest.mark.asyncio
async def test_no_files_ask_nothing():
    assert await script._with_samples_a_person_made([]) == set()
