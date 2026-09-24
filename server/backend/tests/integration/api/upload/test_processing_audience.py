"""
Integration tests: both routes that start auto-processing name the instrument.

A failed auto-processing run has no result, so its error notification cannot
read the instrument room from one the way a finished run's does. The routes
that start the pipeline hand it the file's instrument up front, and the
decorator's error path finds the room there (#1910). The pipeline is stubbed:
what is pinned is what the routes pass it.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import delete

from mascope_backend.api.controllers.sample.files import sample_files_controller
from mascope_backend.api.controllers.sample.files.process import (
    service as process_service,
)
from mascope_backend.api.routes.sample.files import sample_files_routes
from mascope_backend.db import SampleFile
from mascope_backend.db.id import gen_id


INSTRUMENT = "audience-orbi"


@pytest_asyncio.fixture(autouse=True)
async def clean_state(async_session_factory):
    yield
    async with async_session_factory() as session:
        await session.execute(
            delete(SampleFile).where(SampleFile.instrument == INSTRUMENT)
        )
        await session.commit()


@pytest.fixture
def spawn(monkeypatch) -> AsyncMock:
    """Record how the pipeline is started instead of starting it.

    Patched on both modules that start it: the registration controller imports
    it at call time from the process service, the process route at module
    load. ``create_acquisition_datasets`` is stubbed for the reason given in
    ``test_upload_attribution``.
    """
    stub = AsyncMock(return_value=None)
    monkeypatch.setattr(process_service, "spawn_auto_process_sample_file", stub)
    monkeypatch.setattr(sample_files_routes, "spawn_auto_process_sample_file", stub)
    monkeypatch.setattr(
        sample_files_controller,
        "create_acquisition_datasets",
        AsyncMock(return_value={"results": 0, "data": []}),
    )
    return stub


@pytest.mark.asyncio
async def test_registration_hands_the_pipeline_its_instrument(
    editor_client, test_users, spawn
):
    body = {
        "filename": f"{INSTRUMENT}_20260101_0000_.raw",
        "instrument": INSTRUMENT,
        "datetime": "2026-01-01T00:00:00",
        "datetime_utc": "2026-01-01T00:00:00Z",
        "length": 60.0,
        "range": [50, 500],
        "polarity": "-",
    }
    resp = await editor_client.post("/api/sample/files", json=body)
    assert resp.status_code == 201, resp.text

    spawn.assert_awaited_once()
    assert spawn.await_args.kwargs["instrument"] == INSTRUMENT
    assert spawn.await_args.kwargs["user_id"] == test_users["editor"].id


@pytest.mark.asyncio
async def test_processing_by_hand_hands_the_pipeline_its_instrument(
    admin_client, async_session_factory, spawn
):
    sample_file_id = gen_id()
    async with async_session_factory() as session:
        session.add(
            SampleFile(
                sample_file_id=sample_file_id,
                filename=f"{INSTRUMENT}_20260101_0100_.raw",
                instrument=INSTRUMENT,
                instrument_type="orbi",
                datetime=datetime(2026, 1, 1, 1, 0, 0),
                datetime_utc=datetime(2026, 1, 1, 1, 0, 0, tzinfo=timezone.utc),
                length=60.0,
                range=[50.0, 500.0],
                polarity="-",
            )
        )
        await session.commit()

    resp = await admin_client.post(f"/api/sample/files/{sample_file_id}/process")
    assert resp.status_code == 202, resp.text

    spawn.assert_awaited_once()
    assert spawn.await_args.kwargs["sample_file_id"] == sample_file_id
    assert spawn.await_args.kwargs["instrument"] == INSTRUMENT
