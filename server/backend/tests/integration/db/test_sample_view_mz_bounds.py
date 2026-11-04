"""
Tests: the m/z a sample reads, as ``sample_view`` shows it.

A sample reads its file's m/z range, or, where it reads a stream, that
stream's scan ranges: the polarity's one stream, or the composite its streams
are stitched into, which has no scans of its own and reads its segments'.
The view a listing of samples is answered from must not fail on a census of
any shape, so a stream whose ranges cannot be read gives the file's.

Run against the view the models create (``Sample.create_view``); the
migration's own is pinned in ``tests/migrations``.
"""

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, text

from mascope_backend.db import (
    AcquisitionStream,
    Dataset,
    Sample,
    SampleBatch,
    SampleFile,
    SampleItem,
    Workspace,
)
from mascope_backend.db.id import gen_id


@pytest_asyncio.fixture(scope="module", autouse=True)
async def sample_view(async_engine):
    async with async_engine.begin() as conn:
        await conn.execute(text(Sample.drop_view()))
        await conn.execute(text(Sample.create_view()))


def _census(stream_id: str, polarity: str, scan_ranges, **fields) -> dict:
    """A stream's row as a census writes it, with these scan ranges."""
    return {
        "stream_id": stream_id,
        "stream_key": f"stream {stream_id}",
        "signature_key": f"stream {stream_id}",
        "signature": {"ms_order": 1, "polarity": polarity, "scan_ranges": scan_ranges},
        "scan_count": 4,
        "blocks": 1,
        "t_first": 0.0,
        "t_last": 3.0,
        **fields,
    }


#: The stream each item reads, by what it shows; None for no stream.
_READS = {
    "no stream": None,
    "one stream": "single",
    "composite": "composite",
    "no range stated": "empty",
    "ranges of another shape": "odd",
    "ranges not numbers": "text",
}


@pytest_asyncio.fixture(scope="module")
async def items(async_session_factory):
    """One file read every way an item reads it."""
    ids = {"workspace": gen_id(), "dataset": gen_id(), "batch": gen_id()}
    ids["file"] = sample_file_id = gen_id()
    streams = {
        name: gen_id() for name in ("single", "composite", "empty", "odd", "text")
    }
    segments = [gen_id(), gen_id()]
    items = {shown: gen_id() for shown in _READS}
    async with async_session_factory() as session:
        session.add(Workspace(workspace_id=ids["workspace"], workspace_name=gen_id()))
        session.add(
            Dataset(
                dataset_id=ids["dataset"],
                workspace_id=ids["workspace"],
                dataset_name=gen_id(),
            )
        )
        session.add(
            SampleBatch(
                sample_batch_id=ids["batch"],
                dataset_id=ids["dataset"],
                sample_batch_name=gen_id(),
            )
        )
        session.add(
            SampleFile(
                sample_file_id=sample_file_id,
                filename=f"instrument-a_{sample_file_id}.raw",
                instrument="instrument-a",
                instrument_type="orbi",
                datetime=datetime(2026, 11, 4, 12, 0),
                datetime_utc=datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc),
                length=60.0,
                range=[40.01, 899.99],
                polarity="+-",
            )
        )
        await session.flush()
        session.add_all(
            [
                AcquisitionStream(
                    sample_file_id=sample_file_id,
                    **_census(streams["single"], "-", [[50.0, 750.0]]),
                ),
                AcquisitionStream(
                    sample_file_id=sample_file_id,
                    stream_id=streams["composite"],
                    stream_key="composite +",
                    signature={"ms_order": 1, "polarity": "+", "composite": True},
                    stitch={"rule": 1, "runs": [], "source": "default", "notes": []},
                ),
                AcquisitionStream(
                    sample_file_id=sample_file_id,
                    **_census(streams["empty"], "-", []),
                ),
                AcquisitionStream(
                    sample_file_id=sample_file_id,
                    **_census(streams["odd"], "-", "50-750"),
                ),
                AcquisitionStream(
                    sample_file_id=sample_file_id,
                    **_census(
                        streams["text"],
                        "-",
                        [["50", "750"], ["low", 750.0], [60.0], "x"],
                    ),
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                AcquisitionStream(
                    sample_file_id=sample_file_id,
                    composite_stream_id=streams["composite"],
                    **_census(segments[0], "+", [[50.0, 300.0]]),
                ),
                AcquisitionStream(
                    sample_file_id=sample_file_id,
                    composite_stream_id=streams["composite"],
                    **_census(segments[1], "+", [[290.0, 750.0], [800.0, 900.0]]),
                ),
            ]
        )
        await session.flush()
        for shown, reads in _READS.items():
            session.add(
                SampleItem(
                    sample_item_id=items[shown],
                    sample_batch_id=ids["batch"],
                    sample_file_id=sample_file_id,
                    sample_item_name=shown,
                    sample_item_type="ACQUISITION",
                    polarity="+" if reads == "composite" else "-",
                    stream_id=streams.get(reads),
                    t0=0.0,
                    t1=60.0,
                )
            )
        await session.commit()
    yield ids | {"items": items}
    async with async_session_factory() as session:
        await session.execute(
            delete(Workspace).where(Workspace.workspace_id == ids["workspace"])
        )
        await session.execute(
            delete(SampleFile).where(SampleFile.sample_file_id == sample_file_id)
        )
        await session.commit()


async def _shown(async_session_factory, batch_id: str) -> dict[str, tuple]:
    async with async_session_factory() as session:
        rows = (
            await session.execute(
                select(
                    Sample.sample_item_name,
                    Sample.mz_min,
                    Sample.mz_max,
                    Sample.origin_key,
                ).where(Sample.sample_batch_id == batch_id)
            )
        ).all()
    return {name: (low, high, key) for name, low, high, key in rows}


@pytest.mark.asyncio
async def test_each_sample_shows_the_m_z_it_reads(items, async_session_factory):
    shown = await _shown(async_session_factory, items["batch"])

    assert {name: (low, high) for name, (low, high, _) in shown.items()} == {
        "no stream": (40.01, 899.99),
        "one stream": (50.0, 750.0),
        # Its segments', which the composite row itself does not carry
        "composite": (50.0, 900.0),
        "no range stated": (40.01, 899.99),
        "ranges of another shape": (40.01, 899.99),
        "ranges not numbers": (40.01, 899.99),
    }


@pytest.mark.asyncio
async def test_an_item_no_copy_made_shows_no_origin_key(items, async_session_factory):
    shown = await _shown(async_session_factory, items["batch"])

    assert {key for _, _, key in shown.values()} == {None}
