"""Integration tests: the instrument rows, made where an instrument is named.

A registered file is evidence of its instrument: it fills the class, the
vendor, the model its reader reported and that model's PSI-MS term, and moves
``last_seen_utc``. Any
other writer only names the instrument, so it makes the row if there is none
and touches nothing on one that exists. Both answer the row's id, which the
writer stores beside its free-text ``instrument``.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import BackgroundTasks
from sqlalchemy import delete, select

from mascope_backend.api.controllers.sample.files import sample_files_controller
from mascope_backend.api.models.sample.files.sample_file_pydantic_model import (
    SampleFileCreate,
    SampleFileUpdate,
)
from mascope_backend.api.new.instrument_configs.schemas import (
    CreateInstrumentConfigBody,
)
from mascope_backend.api.new.instrument_configs.service import (
    create_instrument_config,
)
from mascope_backend.api.new.instruments.registry import (
    instrument_id_for,
    record_instrument_seen,
)
from mascope_backend.db import Instrument, InstrumentFunction, SampleFile
from mascope_backend.db.id import gen_id


@pytest_asyncio.fixture
async def name(async_session_factory):
    """An instrument name no other test uses, its rows removed afterwards."""
    instrument = f"Reg{gen_id(8)}"
    yield instrument
    async with async_session_factory() as session:
        await session.execute(
            delete(SampleFile).where(SampleFile.instrument == instrument)
        )
        await session.execute(delete(Instrument).where(Instrument.name == instrument))
        await session.commit()


async def _row(async_session_factory, name: str) -> Instrument:
    async with async_session_factory() as session:
        return (
            await session.execute(select(Instrument).where(Instrument.name == name))
        ).scalar_one()


@pytest.mark.asyncio
async def test_a_registered_file_fills_the_row(async_session_factory, name):
    async with async_session_factory() as session:
        instrument_id = await record_instrument_seen(
            session, name, instrument_type="orbi", model="Orbitrap Exploris 120"
        )
        await session.commit()

    row = await _row(async_session_factory, name)
    assert row.instrument_id == instrument_id
    assert row.instrument_type == "orbi"
    assert row.vendor == "Thermo Fisher Scientific"
    assert row.model == "Orbitrap Exploris 120"
    assert row.cv_term == "MS:1003095"
    assert row.last_seen_utc is not None


@pytest.mark.asyncio
async def test_a_later_file_keeps_the_id_and_what_it_does_not_report(
    async_session_factory, name
):
    """A file converted before the model was read cannot erase it."""
    async with async_session_factory() as session:
        first = await record_instrument_seen(
            session, name, instrument_type="orbi", model="Q Exactive"
        )
        await session.commit()
    seen_first = (await _row(async_session_factory, name)).last_seen_utc

    async with async_session_factory() as session:
        again = await record_instrument_seen(
            session, name, instrument_type="tof", model=None
        )
        await session.commit()

    row = await _row(async_session_factory, name)
    assert again == first
    assert row.model == "Q Exactive"
    assert row.cv_term == "MS:1001911"
    # The class is the instrument's: a second file does not overwrite it,
    # nor the vendor that follows from it.
    assert row.instrument_type == "orbi"
    assert row.vendor == "Thermo Fisher Scientific"
    assert row.last_seen_utc > seen_first


@pytest.mark.asyncio
async def test_a_newly_reported_model_replaces_the_old_one(async_session_factory, name):
    async with async_session_factory() as session:
        await record_instrument_seen(session, name, instrument_type="orbi", model="A")
        await record_instrument_seen(session, name, instrument_type="orbi", model="B")
        await session.commit()
    assert (await _row(async_session_factory, name)).model == "B"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("instrument_type", "model"),
    [
        # A family name names no model, so no nearby term either
        ("orbi", "Orbitrap Exploris"),
        # The ontology has no Tofwerk models
        ("tof", None),
    ],
)
async def test_a_model_without_a_term_leaves_the_term_empty(
    async_session_factory, name, instrument_type, model
):
    async with async_session_factory() as session:
        await record_instrument_seen(
            session, name, instrument_type=instrument_type, model=model
        )
        await session.commit()
    assert (await _row(async_session_factory, name)).cv_term is None


@pytest.mark.asyncio
async def test_the_term_follows_a_newly_reported_model(async_session_factory, name):
    """The term is the model's: a model without one clears the old term
    rather than leave it naming the model before."""
    async with async_session_factory() as session:
        await record_instrument_seen(
            session, name, instrument_type="orbi", model="Q Exactive"
        )
        await session.commit()
    assert (await _row(async_session_factory, name)).cv_term == "MS:1001911"

    async with async_session_factory() as session:
        await record_instrument_seen(
            session, name, instrument_type="orbi", model="Orbitrap Exploris 480"
        )
        await session.commit()
    assert (await _row(async_session_factory, name)).cv_term == "MS:1003028"

    async with async_session_factory() as session:
        await record_instrument_seen(
            session, name, instrument_type="orbi", model="Orbitrap Exploris"
        )
        await session.commit()
    row = await _row(async_session_factory, name)
    assert row.model == "Orbitrap Exploris"
    assert row.cv_term is None


@pytest.mark.asyncio
async def test_a_mere_mention_makes_a_bare_row(async_session_factory, name):
    async with async_session_factory() as session:
        instrument_id = await instrument_id_for(session, name)
        await session.commit()

    row = await _row(async_session_factory, name)
    assert row.instrument_id == instrument_id
    assert row.instrument_type is None
    assert row.vendor is None
    assert row.last_seen_utc is None


@pytest.mark.asyncio
async def test_a_mention_touches_nothing_on_a_row_that_exists(
    async_session_factory, name
):
    async with async_session_factory() as session:
        seen = await record_instrument_seen(session, name, instrument_type="tof")
        await session.commit()
    before = await _row(async_session_factory, name)

    async with async_session_factory() as session:
        mentioned = await instrument_id_for(session, name)
        await session.commit()

    after = await _row(async_session_factory, name)
    assert mentioned == seen
    assert after.last_seen_utc == before.last_seen_utc
    assert after.vendor == "TOFWERK"


@pytest.mark.asyncio
@pytest.mark.parametrize("blank", [None, "", "   "])
async def test_no_name_is_no_instrument(async_session_factory, blank):
    async with async_session_factory() as session:
        assert await instrument_id_for(session, blank) is None
        assert await record_instrument_seen(session, blank) is None


@pytest.mark.asyncio
async def test_registering_a_file_links_it_and_records_the_model(
    async_session_factory, name, monkeypatch
):
    """The converter's registration is what fills the row on ingest."""
    monkeypatch.setattr(sample_files_controller, "emit_record_created", AsyncMock())
    monkeypatch.setattr(
        sample_files_controller, "create_acquisition_datasets", AsyncMock()
    )
    created = await sample_files_controller.create_sample_file(
        SampleFileCreate(
            filename=f"{name}_2026.10.27-12h00m00s_registry",
            instrument=name,
            instrument_type="orbi",
            instrument_model="Orbitrap Exploris 240",
            datetime=datetime(2026, 10, 27, 12, 0),
            datetime_utc=datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc),
            length=60.0,
            range=[40.0, 500.0],
            polarity="+",
        ),
        BackgroundTasks(),
    )

    row = await _row(async_session_factory, name)
    assert created["data"]["instrument_id"] == row.instrument_id
    assert row.model == "Orbitrap Exploris 240"
    assert row.cv_term == "MS:1003094"
    assert row.instrument_type == "orbi"
    assert "instrument_model" not in created["data"]


@pytest.mark.asyncio
async def test_an_instrument_function_links_the_row(async_session_factory, name):
    created = await create_instrument_config(
        CreateInstrumentConfigBody(
            instrument=name,
            method_file="",
            resolution_function=[1.0],
            datetime_utc=datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc),
        )
    )
    async with async_session_factory() as session:
        stored = await session.get(
            InstrumentFunction, created["data"]["instrument_function_id"]
        )
        await session.delete(stored)
        await session.commit()
    assert (
        stored.instrument_id == (await _row(async_session_factory, name)).instrument_id
    )


@pytest.mark.asyncio
async def test_moving_a_file_to_another_instrument_relinks_it(
    async_session_factory, name, monkeypatch
):
    monkeypatch.setattr(sample_files_controller, "emit_record_updated", AsyncMock())
    monkeypatch.setattr(
        sample_files_controller, "create_acquisition_datasets", AsyncMock()
    )
    monkeypatch.setattr(
        sample_files_controller, "delete_acquisition_datasets", AsyncMock()
    )
    other = f"{name}-B"
    file_id = gen_id()
    fields = {
        "filename": f"{name}_2026.10.27-12h00m00s_moved",
        "datetime": datetime(2026, 10, 27, 12, 0),
        "datetime_utc": datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc),
        "length": 60.0,
        "range": [40.0, 500.0],
        "polarity": "+",
    }
    async with async_session_factory() as session:
        session.add(
            SampleFile(
                sample_file_id=file_id,
                instrument=name,
                instrument_id=await instrument_id_for(session, name),
                instrument_type="orbi",
                **fields,
            )
        )
        await session.commit()
    try:
        await sample_files_controller.update_sample_file(
            file_id, SampleFileUpdate(instrument=other, **fields)
        )
        async with async_session_factory() as session:
            moved = await session.get(SampleFile, file_id)
        assert (
            moved.instrument_id
            == (await _row(async_session_factory, other)).instrument_id
        )
    finally:
        async with async_session_factory() as session:
            await session.execute(
                delete(SampleFile).where(SampleFile.sample_file_id == file_id)
            )
            await session.execute(delete(Instrument).where(Instrument.name == other))
            await session.commit()
