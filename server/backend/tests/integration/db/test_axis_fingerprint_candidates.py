"""
Tests: which files the axis fingerprint backfill offers to read.

The rest of that script is decided in memory and tested hermetically. This
part is a query: that it asks for Orbitrap files and no others, and that it
answers newest first, which is what a run capped by AXF_LIMIT reads.
"""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete

from mascope_backend.db import SampleFile
from mascope_backend.db.id import gen_id
from mascope_backend.db.scripts import backfill_axis_fingerprint as script


@pytest_asyncio.fixture
async def files(async_session_factory):
    """Adds sample files and cleans up."""
    made: list[str] = []

    async def add(instrument_type: str, *, minutes: int = 0) -> str:
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
        made.append(sample_file.sample_file_id)
        return sample_file.filename

    yield add

    async with async_session_factory() as session:
        await session.execute(
            delete(SampleFile).where(SampleFile.sample_file_id.in_(made))
        )
        await session.commit()


@pytest.mark.asyncio
async def test_only_orbitrap_files_are_offered(files):
    orbitrap = await files("orbi")
    tof = await files("tof")

    offered = {c["filename"] for c in await script._candidates()}

    assert orbitrap in offered
    assert tof not in offered


@pytest.mark.asyncio
async def test_the_newest_file_comes_first(files):
    oldest = await files("orbi", minutes=0)
    middle = await files("orbi", minutes=10)
    newest = await files("orbi", minutes=20)

    ordered = [
        c["filename"]
        for c in await script._candidates()
        if c["filename"] in {oldest, middle, newest}
    ]

    assert ordered == [newest, middle, oldest]
