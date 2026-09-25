"""
Tests: which files the scan-stream census backfill offers to read.

The rest of that script is decided in memory and tested hermetically. This
part is a query, and two things about it only a database can show: that it
asks for the instrument types the method binding backfill requires a census
from and no others, and that it answers newest first - which is the whole
policy of a bounded run, since CENSUS_LIMIT reads the head of this list.
"""

from datetime import datetime, timedelta, timezone

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
from mascope_backend.db.scripts import backfill_scan_stream_census as script
from mascope_backend.method_keys import CENSUS_BEARING_INSTRUMENT_TYPES


@pytest_asyncio.fixture
async def files(async_session_factory):
    """Adds sample files, optionally routed by the pipeline, and cleans up.

    "Routed" is the shape `backfill_method_bindings` reads its history
    through, and `_candidates` mirrors: an ACQUISITION item, in an ACQUISITION
    batch, of an ACQUISITION dataset, in a system workspace.
    """
    made: dict[str, list] = {"files": [], "items": [], "workspaces": []}
    home: dict[str, object] = {}

    async def system_home():
        if not home:
            workspace = Workspace(
                workspace_id=gen_id(),
                workspace_name=f"Census test {gen_id(6)}",
                is_system=True,
            )
            dataset = Dataset(
                dataset_id=gen_id(),
                workspace_id=workspace.workspace_id,
                dataset_name=f"Acquisitions {gen_id(6)}",
                dataset_type="ACQUISITION",
                instrument=f"instrument-{gen_id(8)}",
            )
            batch = SampleBatch(
                sample_batch_id=gen_id(),
                dataset_id=dataset.dataset_id,
                sample_batch_name=f"Batch {gen_id(6)}",
                sample_batch_type="ACQUISITION",
            )
            async with async_session_factory() as session:
                session.add_all([workspace, dataset, batch])
                await session.commit()
            made["workspaces"].append(workspace.workspace_id)
            home["batch"] = batch
        return home["batch"]

    async def add(instrument_type: str, *, minutes: int = 0, routed: bool = False):
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
            polarity="+",
        )
        async with async_session_factory() as session:
            session.add(sample_file)
            await session.commit()
        made["files"].append(sample_file.sample_file_id)

        if routed:
            batch = await system_home()
            item = SampleItem(
                sample_item_id=gen_id(),
                sample_batch_id=batch.sample_batch_id,
                sample_file_id=sample_file.sample_file_id,
                sample_item_name=f"Item {gen_id(6)}",
                sample_item_type="ACQUISITION",
            )
            async with async_session_factory() as session:
                session.add(item)
                await session.commit()
            made["items"].append(item.sample_item_id)

        return sample_file.filename

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
async def test_only_census_bearing_instruments_are_offered(files):
    """A TofDaq acquisition has no census to record, so it is not a candidate.

    Offering one would reopen every TOF file a server holds to be told what
    ``mascope_tofwerk.processor.scan_streams`` says without opening anything:
    that there is one stream and no per-scan filter to tell scans apart.
    """
    census_bearing = await files(sorted(CENSUS_BEARING_INSTRUMENT_TYPES)[0])
    tof = await files("tof")

    offered = {c["filename"] for c in await script._candidates()}

    assert census_bearing in offered
    assert tof not in offered


@pytest.mark.asyncio
async def test_a_file_the_pipeline_routed_comes_before_a_newer_one_it_did_not(files):
    """A capped run should spend its reads where the bindings are.

    `backfill_method_bindings` only reads files with a pipeline ACQUISITION
    item in a system workspace. Every Orbitrap file is worth a census - the
    pooled streams note reads one - but a newest-first run would otherwise
    spend its whole budget on recent manual uploads that the binding backfill
    never looks at.
    """
    instrument_type = sorted(CENSUS_BEARING_INSTRUMENT_TYPES)[0]
    routed = await files(instrument_type, minutes=0, routed=True)
    newer_unrouted = await files(instrument_type, minutes=30)

    ordered = [
        c["filename"]
        for c in await script._candidates()
        if c["filename"] in {routed, newer_unrouted}
    ]

    assert ordered == [routed, newer_unrouted]


@pytest.mark.asyncio
async def test_the_newest_file_comes_first(files):
    """What a bounded run reads: the methods still in use, not the oldest.

    A full run reaches the same state either way, so this is only about which
    files a run capped by CENSUS_LIMIT gets to.
    """
    instrument_type = sorted(CENSUS_BEARING_INSTRUMENT_TYPES)[0]
    oldest = await files(instrument_type, minutes=0)
    middle = await files(instrument_type, minutes=10)
    newest = await files(instrument_type, minutes=20)

    ordered = [
        c["filename"]
        for c in await script._candidates()
        if c["filename"] in {oldest, middle, newest}
    ]

    assert ordered == [newest, middle, oldest]
