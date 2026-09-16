"""
Integration tests: the calibration record a TOF file starts from.

The converter posts a TOF file's acquisition coefficients as its
``mz_calibration``. That record describes the axis the instrument wrote, not a
fit, so registration stamps it ``unfitted`` and unverified - a registration
cannot claim a verified axis - and a later failed fit replaces it with a
failure marker instead of leaving it looking untouched.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from mascope_backend.api.controllers.sample.files import sample_files_controller
from mascope_backend.api.controllers.sample.files.process import (
    service as process_service,
)
from mascope_backend.db import SampleFile


INSTRUMENT = "calib-record-tof"
CONVERTER_RECORD = {"mode": 0, "par": [2700.5, -1210.25]}


@pytest_asyncio.fixture(autouse=True)
async def clean_state(async_session_factory):
    yield
    async with async_session_factory() as session:
        await session.execute(
            delete(SampleFile).where(SampleFile.instrument == INSTRUMENT)
        )
        await session.commit()


@pytest.fixture(autouse=True)
def _no_post_create_work(monkeypatch):
    """Stub the work creation kicks off; see ``test_upload_attribution``."""
    monkeypatch.setattr(
        process_service, "spawn_auto_process_sample_file", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        sample_files_controller,
        "create_acquisition_datasets",
        AsyncMock(return_value={"results": 0, "data": []}),
    )


def _payload(name: str, mz_calibration: dict | None) -> dict:
    return {
        "filename": f"{INSTRUMENT}_{name}_20260101_0000_.h5",
        "instrument": INSTRUMENT,
        "datetime": "2026-01-01T00:00:00",
        "datetime_utc": "2026-01-01T00:00:00Z",
        "length": 60.0,
        "range": [10, 500],
        "polarity": "-",
        "mz_calibration": mz_calibration,
    }


async def _record(async_session_factory, filename: str) -> dict | None:
    async with async_session_factory() as session:
        return (
            await session.execute(
                select(SampleFile.mz_calibration).where(SampleFile.filename == filename)
            )
        ).scalar_one()


@pytest.mark.asyncio
async def test_a_converter_record_is_stored_unfitted(
    async_session_factory, editor_client
):
    body = _payload("plain", dict(CONVERTER_RECORD))
    resp = await editor_client.post("/api/sample/files", json=body)
    assert resp.status_code == 201, resp.text

    assert await _record(async_session_factory, body["filename"]) == {
        **CONVERTER_RECORD,
        "status": "unfitted",
        "verified": False,
    }


@pytest.mark.asyncio
async def test_a_registration_cannot_claim_a_verified_axis(
    async_session_factory, editor_client
):
    forged = {
        **CONVERTER_RECORD,
        "status": "ok",
        "verified": True,
        "quality": {"n_points": 9, "post_fit_mz_error_ppm": 0.1},
    }
    body = _payload("forged", forged)
    resp = await editor_client.post("/api/sample/files", json=body)
    assert resp.status_code == 201, resp.text

    stored = await _record(async_session_factory, body["filename"])
    assert stored["status"] == "unfitted"
    assert stored["verified"] is False
    assert "quality" not in stored


@pytest.mark.asyncio
async def test_an_orbitrap_registration_stays_without_a_record(
    async_session_factory, editor_client
):
    body = _payload("none", None)
    resp = await editor_client.post("/api/sample/files", json=body)
    assert resp.status_code == 201, resp.text

    assert await _record(async_session_factory, body["filename"]) is None


async def _insert(
    async_session_factory, name: str, record: dict | None
) -> tuple[str, str]:
    """Write a sample file row directly, returning its id and filename."""
    filename = _payload(name, None)["filename"]
    sample_file = SampleFile(
        sample_file_id=f"calibrec{name}"[:16],
        filename=filename,
        instrument=INSTRUMENT,
        datetime=datetime(2026, 1, 1),
        datetime_utc=datetime(2026, 1, 1, tzinfo=timezone.utc),
        length=60.0,
        range=[10, 500],
        polarity="-",
        mz_calibration=record,
    )
    async with async_session_factory() as session:
        session.add(sample_file)
        await session.commit()
    return sample_file.sample_file_id, filename


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "record",
    [
        {**CONVERTER_RECORD, "status": "unfitted", "verified": False},
        # Registered before the stamp.
        dict(CONVERTER_RECORD),
    ],
    ids=["stamped", "legacy"],
)
async def test_a_failed_fit_replaces_the_converter_record(
    async_session_factory, record
):
    sample_file_id, filename = await _insert(async_session_factory, "fail", record)

    await process_service._record_calibration_failure(
        sample_file_id,
        RuntimeError("Not enough calibration peaks"),
        attempts=7,
        mz_error_tolerance=960,
    )

    stored = await _record(async_session_factory, filename)
    assert stored["status"] == "failed"
    assert stored["verified"] is False
    assert stored["error"] == "Not enough calibration peaks"
    # The acquisition coefficients stay on the marker.
    assert stored["mode"] == CONVERTER_RECORD["mode"]
    assert stored["par"] == CONVERTER_RECORD["par"]


@pytest.mark.asyncio
async def test_a_failed_fit_keeps_an_applied_one(async_session_factory):
    applied = {"mode": 2, "par": [1.0], "status": "poor", "verified": False}
    sample_file_id, filename = await _insert(async_session_factory, "keep", applied)

    await process_service._record_calibration_failure(
        sample_file_id, RuntimeError("boom"), attempts=1, mz_error_tolerance=15
    )

    assert await _record(async_session_factory, filename) == applied
