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

from mascope_backend.db import SampleFile
from mascope_backend.db.id import gen_id
from mascope_backend.db.scripts import backfill_scan_stream_census as script
from mascope_backend.method_keys import CENSUS_BEARING_INSTRUMENT_TYPES


@pytest_asyncio.fixture
async def files(async_session_factory):
    """Adds sample files and takes them away again."""
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
