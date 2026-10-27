"""
Tests: the MS2 store backfill over the fixture database and the MS2 fixture.

A file of the hermetic MS2 fixture (``tests/ms2_fixture.py``) converted, its
store then taken away as if it had been converted before the store existed,
and a second file beside it whose census was never recorded. Both are
sample-file rows of the integration database, as the script finds them, under
an instrument of their own so the counts are theirs alone.
"""

import os
import shutil
from collections import Counter
from datetime import datetime, timezone
from unittest.mock import patch

import numpy as np
import pytest
import pytest_asyncio
import zarr
from ms2_fixture import converted
from sqlalchemy import delete

import mascope_file.io as m_io
from mascope_backend.db import SampleFile
from mascope_backend.db.id import gen_id
from mascope_backend.db.scripts import backfill_ms2_store as script
from mascope_signal import ms2_store


#: Fixture files share their instrument and day; a second name, a minute on.
def _beside(filename: str) -> str:
    return filename.replace("-17h43m57s_", "-17h44m57s_")


def _arrays(filename: str) -> dict[str, np.ndarray]:
    root = zarr.open_group(ms2_store.store_path(filename), mode="r")
    return {
        f"{group}/{name}": np.asarray(array[:])
        for group, members in root.groups()
        for name, array in members.arrays()
    }


async def _no_sleep(_seconds):
    pass


@pytest.fixture
def converted_before_the_store(tmp_path):
    """Two Orbitrap files converted before the store: one whose census holds
    a fragmentation stream, one with no census at all.

    Not async: the converter runs an event loop of its own.
    """
    with converted("stepped", tmp_path) as done:
        fragmented = done.props.filename
        written = _arrays(fragmented)
        recorded = m_io.read_props(fragmented)["ms2_store"]
        ms2_store.remove_ms2_store(fragmented)

        no_census = _beside(fragmented)
        shutil.copytree(done.sample_dir, done.sample_dir.parent / no_census)
        props = m_io.read_props(no_census)
        del props["scan_streams"]
        m_io.write_props(no_census, props)
        yield {
            "fragmented": fragmented,
            "no_census": no_census,
            "written": written,
            "recorded": recorded,
        }


@pytest_asyncio.fixture
async def history(converted_before_the_store, async_session_factory):
    """Both files as sample-file rows, under an instrument of their own."""
    files = converted_before_the_store
    instrument = f"instrument-{gen_id(8)}"
    rows = [
        SampleFile(
            sample_file_id=gen_id(),
            filename=files[name],
            instrument=instrument,
            instrument_type="orbi",
            datetime=datetime(2026, 1, 9, 17, 43),
            datetime_utc=datetime(2026, 1, 9, 17, 43, tzinfo=timezone.utc),
            length=60.0,
            range=[40.0, 600.0],
            polarity="+",
        )
        for name in ("fragmented", "no_census")
    ]
    async with async_session_factory() as session:
        session.add_all(rows)
        await session.commit()
    yield {**files, "instrument": instrument}
    async with async_session_factory() as session:
        await session.execute(
            delete(SampleFile).where(
                SampleFile.sample_file_id.in_([row.sample_file_id for row in rows])
            )
        )
        await session.commit()


def _held(filename: str) -> list[str]:
    return sorted(os.listdir(os.path.dirname(ms2_store.store_path(filename))))


@pytest.mark.asyncio
async def test_a_count_reports_both_and_writes_nothing(history):
    before = {f: _held(history[f]) for f in ("fragmented", "no_census")}

    counts, written = await script.backfill("count")

    assert counts[history["instrument"]] == Counter(
        {script.PENDING: 1, script.NO_CENSUS: 1}
    )
    assert written == Counter()
    assert {f: _held(history[f]) for f in before} == before


@pytest.mark.asyncio
async def test_a_run_writes_the_store_and_a_second_writes_nothing(history):
    fragmented = history["fragmented"]
    no_census = history["no_census"]
    untouched = _held(no_census)

    counts, written = await script.backfill("run", pause=0, sleep=_no_sleep)

    assert counts[history["instrument"]] == Counter(
        {script.PENDING: 1, script.NO_CENSUS: 1}
    )
    assert written == Counter({"written": 1})
    # What the converter wrote, array for array, and recorded the same.
    assert m_io.read_props(fragmented)["ms2_store"] == history["recorded"]
    assert ms2_store.Ms2Store.open(fragmented) is not None
    stored = _arrays(fragmented)
    assert stored.keys() == history["written"].keys()
    for name, values in history["written"].items():
        np.testing.assert_array_equal(stored[name], values)
    # The file with no census is reported and left alone.
    assert _held(no_census) == untouched
    assert "ms2_store_backfill" not in m_io.read_props(fragmented)

    counts, written = await script.backfill("run", pause=0, sleep=_no_sleep)

    assert counts[history["instrument"]] == Counter(
        {script.HAS_STORE: 1, script.NO_CENSUS: 1}
    )
    assert written == Counter()
    assert _held(no_census) == untouched


@pytest.mark.asyncio
async def test_a_file_whose_store_cannot_be_written_is_passed_over_next_time(
    history,
):
    with patch.object(ms2_store, "write_ms2_store", side_effect=OSError("full")):
        _, written = await script.backfill("run", pause=0, sleep=_no_sleep)
    assert written == Counter({"failed": 1})

    counts, written = await script.backfill("run", pause=0, sleep=_no_sleep)
    assert counts[history["instrument"]][script.TRIED] == 1
    assert written == Counter()

    _, written = await script.backfill("run", pause=0, retry=True, sleep=_no_sleep)
    assert written == Counter({"written": 1})
    assert m_io.read_props(history["fragmented"])[script.ATTEMPT_FIELD] is None
