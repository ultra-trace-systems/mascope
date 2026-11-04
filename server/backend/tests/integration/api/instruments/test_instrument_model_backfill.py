"""
Integration tests: the instrument model backfill against the database.

It picks the instruments with no model, lists each one's Orbitrap files
newest first, and writes the model with its PSI-MS term only on a row that
still has none. The rules of the search are in
``tests/unit/db/test_backfill_instrument_model.py``.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from mascope_backend.db import Instrument, SampleFile
from mascope_backend.db.id import gen_id
from mascope_backend.db.scripts import backfill_instrument_model as script


EXPLORIS = "Orbitrap Exploris 120"
EXPLORIS_TERM = "MS:1003095"


def _file(row_id, instrument_type, day, sample_file_id):
    return SampleFile(
        sample_file_id=sample_file_id,
        filename=f"bim_{sample_file_id}.raw",
        instrument="instrument-x",
        instrument_id=row_id,
        instrument_type=instrument_type,
        datetime=datetime(2026, 1, day, 12, 0, 0),
        datetime_utc=datetime(2026, 1, day, 12, 0, 0, tzinfo=timezone.utc),
        length=60.0,
        range=[50.0, 750.0],
        polarity="+",
    )


@pytest_asyncio.fixture
async def rows(async_session_factory):
    """Four instruments: an Orbitrap with no model and three files (two of
    them Orbitrap, one TOF), an Orbitrap with a model, a TOF with no model,
    and a name with no files. Removed afterwards."""
    ids = SimpleNamespace(
        bare=gen_id(16),
        modelled=gen_id(16),
        tof=gen_id(16),
        named=gen_id(16),
        old=gen_id(),
        new=gen_id(),
        stray=gen_id(),
        tof_file=gen_id(),
        modelled_file=gen_id(),
    )
    async with async_session_factory() as session:
        for row_id, instrument_type, model in (
            (ids.bare, "orbi", None),
            (ids.modelled, "orbi", "Q Exactive"),
            (ids.tof, "tof", None),
            (ids.named, None, None),
        ):
            session.add(
                Instrument(
                    instrument_id=row_id,
                    name=f"bim-{row_id}",
                    instrument_type=instrument_type,
                    model=model,
                )
            )
        await session.flush()
        for row_id, instrument_type, day, file_id in (
            (ids.bare, "orbi", 1, ids.old),
            (ids.bare, "orbi", 3, ids.new),
            (ids.bare, "tof", 5, ids.stray),
            (ids.tof, "tof", 2, ids.tof_file),
            (ids.modelled, "orbi", 2, ids.modelled_file),
        ):
            session.add(_file(row_id, instrument_type, day, file_id))
        await session.commit()
    yield ids
    row_ids = [ids.bare, ids.modelled, ids.tof, ids.named]
    async with async_session_factory() as session:
        await session.execute(
            delete(SampleFile).where(SampleFile.instrument_id.in_(row_ids))
        )
        await session.execute(
            delete(Instrument).where(Instrument.instrument_id.in_(row_ids))
        )
        await session.commit()


async def _row(async_session_factory, instrument_id) -> Instrument:
    async with async_session_factory() as session:
        return (
            await session.execute(
                select(Instrument).where(Instrument.instrument_id == instrument_id)
            )
        ).scalar_one()


@pytest.mark.asyncio
async def test_only_the_instruments_with_no_model_are_picked(rows):
    picked = {i["instrument_id"]: i for i in await script._instruments()}
    assert rows.bare in picked
    assert rows.tof in picked
    assert rows.named in picked
    assert rows.modelled not in picked
    assert picked[rows.bare]["orbitrap_files"] == 2
    assert picked[rows.tof]["orbitrap_files"] == 0
    assert picked[rows.tof]["instrument_type"] == "tof"
    assert picked[rows.named]["orbitrap_files"] == 0


@pytest.mark.asyncio
async def test_an_instruments_orbitrap_files_come_newest_first(rows):
    assert await script._files(rows.bare) == [
        f"bim_{rows.new}.raw",
        f"bim_{rows.old}.raw",
    ]


@pytest.mark.asyncio
async def test_the_model_and_its_term_are_written(async_session_factory, rows):
    assert await script._write(rows.bare, EXPLORIS, EXPLORIS_TERM)
    row = await _row(async_session_factory, rows.bare)
    assert row.model == EXPLORIS
    assert row.cv_term == EXPLORIS_TERM


@pytest.mark.asyncio
async def test_a_row_that_has_a_model_is_not_overwritten(async_session_factory, rows):
    """A file registered during the run knows better than an old one."""
    assert not await script._write(rows.modelled, EXPLORIS, EXPLORIS_TERM)
    row = await _row(async_session_factory, rows.modelled)
    assert row.model == "Q Exactive"


@pytest.fixture
def readers(monkeypatch, rows):
    """The newest file's props name nothing and the older one's fingerprint
    names the model; no raw file is kept."""
    props = {
        f"bim_{rows.new}.raw": {"instrument_model": None},
        f"bim_{rows.old}.raw": {
            "axis_fingerprint": {"source": {"instrument_model": EXPLORIS}}
        },
    }
    monkeypatch.setattr(script, "_read_props", props.get)
    monkeypatch.setattr(script, "_has_raw", lambda f: False)
    monkeypatch.setattr(script, "_model_in_raw", lambda f: None)


@pytest.mark.asyncio
async def test_a_run_fills_the_row_and_leaves_the_rest(
    async_session_factory, rows, readers, monkeypatch
):
    monkeypatch.delenv("DRY_RUN", raising=False)
    monkeypatch.setattr(script, "configure_database_engine", _nothing)
    await script.run()

    bare = await _row(async_session_factory, rows.bare)
    assert (bare.model, bare.cv_term) == (EXPLORIS, EXPLORIS_TERM)
    tof = await _row(async_session_factory, rows.tof)
    assert (tof.model, tof.cv_term) == (None, None)
    modelled = await _row(async_session_factory, rows.modelled)
    assert modelled.model == "Q Exactive"

    # A second run finds the row filled and changes nothing.
    await script.run()
    again = await _row(async_session_factory, rows.bare)
    assert (again.model, again.cv_term) == (EXPLORIS, EXPLORIS_TERM)


@pytest.mark.asyncio
async def test_a_dry_run_writes_nothing(
    async_session_factory, rows, readers, monkeypatch
):
    monkeypatch.setenv("DRY_RUN", "1")
    monkeypatch.setattr(script, "configure_database_engine", _nothing)
    await script.run()

    bare = await _row(async_session_factory, rows.bare)
    assert (bare.model, bare.cv_term) == (None, None)


async def _nothing() -> None:
    """The test database is configured by the suite."""
