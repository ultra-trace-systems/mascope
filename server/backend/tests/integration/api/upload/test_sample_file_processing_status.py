"""
Integration tests: a sample file's processing status, stored and served.

Registration records ``converted`` and the time of registration, each later
stage writes its own status, and the file list serves the columns to the Raw
files view and to an agent reading back what became of its upload. A restart
marks the runs it cut short as failed. The pipeline itself is covered by the
unit tests; here the columns, the writer, the list filter and the startup
reset meet a real database.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, update

from mascope_backend.api.controllers.sample.files import sample_files_controller
from mascope_backend.api.controllers.sample.files.process import (
    service as process_service,
)
from mascope_backend.api.controllers.sample.files.process import status
from mascope_backend.api.models.sample.files.config import ProcessingStatus
from mascope_backend.api.routes.sample.files import sample_files_routes
from mascope_backend.db import SampleFile
from mascope_backend.db.admin.sample_file.reset_interrupted_processing import (
    INTERRUPTED_DETAIL,
    reset_interrupted_processing,
)
from mascope_backend.db.id import gen_id


INSTRUMENT = "status-orbi"


@pytest_asyncio.fixture(autouse=True)
async def clean_state(async_session_factory):
    yield
    async with async_session_factory() as session:
        await session.execute(
            delete(SampleFile).where(SampleFile.instrument == INSTRUMENT)
        )
        await session.commit()


@pytest.fixture
def no_post_create_work(monkeypatch):
    """Stub the work registration kicks off; see ``test_upload_attribution``."""
    monkeypatch.setattr(
        process_service, "spawn_auto_process_sample_file", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        sample_files_controller,
        "create_acquisition_datasets",
        AsyncMock(return_value={"results": 0, "data": []}),
    )


async def _add_file(
    async_session_factory,
    name: str,
    processing_status: str | None = None,
    acquired: datetime = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc),
) -> str:
    sample_file_id = gen_id()
    async with async_session_factory() as session:
        session.add(
            SampleFile(
                sample_file_id=sample_file_id,
                filename=f"{INSTRUMENT}_{name}.raw",
                instrument=INSTRUMENT,
                instrument_type="orbi",
                datetime=acquired.replace(tzinfo=None),
                datetime_utc=acquired,
                length=60.0,
                range=[50.0, 500.0],
                polarity="-",
                processing_status=processing_status,
            )
        )
        await session.commit()
    return sample_file_id


async def _set(async_session_factory, sample_file_id: str, **values) -> None:
    async with async_session_factory() as session:
        await session.execute(
            update(SampleFile)
            .where(SampleFile.sample_file_id == sample_file_id)
            .values(**values)
        )
        await session.commit()


async def _row(async_session_factory, sample_file_id: str) -> SampleFile:
    async with async_session_factory() as session:
        return (
            await session.execute(
                select(SampleFile).where(SampleFile.sample_file_id == sample_file_id)
            )
        ).scalar_one()


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_registered_file_is_converted(
    editor_client, async_session_factory, no_post_create_work, monkeypatch
):
    note = "Polarity - pools 2 MS1 scan streams into one peak list: A; B."
    monkeypatch.setattr(
        sample_files_controller,
        "read_pooled_streams_note",
        AsyncMock(return_value=note),
    )
    body = {
        "filename": f"{INSTRUMENT}_registered.raw",
        "instrument": INSTRUMENT,
        "datetime": "2026-09-01T12:00:00",
        "datetime_utc": "2026-09-01T12:00:00Z",
        "length": 60.0,
        "range": [50, 500],
        "polarity": "-",
    }

    before = datetime.now(timezone.utc)
    resp = await editor_client.post("/api/sample/files", json=body)

    assert resp.status_code == 201, resp.text
    data = resp.json()["data"]
    assert data["processing_status"] == "converted"
    assert data["processing_detail"] == note
    assert data["processing_updated_utc"] is not None
    row = await _row(async_session_factory, data["sample_file_id"])
    assert row.processing_status == "converted"
    # Set by the database, and served with the rest of the row.
    registered = datetime.fromisoformat(data["sample_file_utc_created"])
    assert registered == row.sample_file_utc_created
    assert abs((registered - before).total_seconds()) < 60


@pytest.mark.asyncio
async def test_a_status_cannot_be_posted_with_the_file(
    editor_client, no_post_create_work
):
    """The body is the converter's reading of the file; the status is ours."""
    body = {
        "filename": f"{INSTRUMENT}_claims_done.raw",
        "instrument": INSTRUMENT,
        "datetime": "2026-09-01T12:00:00",
        "datetime_utc": "2026-09-01T12:00:00Z",
        "length": 60.0,
        "range": [50, 500],
        "polarity": "-",
        "processing_status": "done",
        "processing_detail": "Nothing to see here.",
    }

    resp = await editor_client.post("/api/sample/files", json=body)

    assert resp.status_code == 201, resp.text
    data = resp.json()["data"]
    assert data["processing_status"] == "converted"
    assert data["processing_detail"] is None


# ---------------------------------------------------------------------------
# The writer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_status_is_stored_and_sent_to_the_instrument_room(
    async_session_factory, monkeypatch
):
    sample_file_id = await _add_file(async_session_factory, "written", "bound")
    emit = AsyncMock()
    monkeypatch.setattr(status, "emit_record_updated", emit)

    await status.record_processing_status(
        sample_file_id, ProcessingStatus.DONE, "Matched 1 sample."
    )

    row = await _row(async_session_factory, sample_file_id)
    assert row.processing_status == "done"
    assert row.processing_detail == "Matched 1 sample."
    assert row.processing_updated_utc is not None

    emit.assert_awaited_once()
    kwargs = emit.await_args.kwargs
    assert kwargs["record_type"] == "acquisition"
    assert kwargs["record_id"] == sample_file_id
    assert kwargs["room"] == INSTRUMENT
    # The whole row, so a view that replaces rows keeps every column.
    assert kwargs["record"]["filename"] == row.filename
    assert kwargs["record"]["processing_status"] == "done"


@pytest.mark.asyncio
async def test_a_status_for_a_deleted_file_is_dropped(monkeypatch):
    emit = AsyncMock()
    monkeypatch.setattr(status, "emit_record_updated", emit)

    await status.record_processing_status(gen_id(), ProcessingStatus.FAILED, "Gone.")

    emit.assert_not_called()


# ---------------------------------------------------------------------------
# The file list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_file_list_filters_by_processing_status(
    admin_client, async_session_factory
):
    wanted = {
        await _add_file(async_session_factory, "parked", "needs_chemistry"),
        await _add_file(async_session_factory, "broken", "failed"),
    }
    await _add_file(async_session_factory, "fine", "done")
    await _add_file(async_session_factory, "legacy", None)

    resp = await admin_client.get(
        "/api/sample/files",
        params=[
            ("instrument", INSTRUMENT),
            ("processing_status", "needs_chemistry"),
            ("processing_status", "failed"),
        ],
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["results"] == 2
    assert {row["sample_file_id"] for row in body["data"]} == wanted


@pytest.mark.asyncio
async def test_the_file_list_serves_the_status_unfiltered(
    admin_client, async_session_factory
):
    await _add_file(async_session_factory, "fine", "done")
    await _add_file(async_session_factory, "legacy", None)

    resp = await admin_client.get(
        "/api/sample/files",
        params={"instrument": INSTRUMENT, "sort": "processing_status"},
    )

    assert resp.status_code == 200, resp.text
    statuses = {row["processing_status"] for row in resp.json()["data"]}
    assert statuses == {"done", None}


@pytest.mark.asyncio
async def test_the_file_list_sorts_by_registration_time(
    admin_client, async_session_factory
):
    first = await _add_file(async_session_factory, "first")
    second = await _add_file(async_session_factory, "second")

    resp = await admin_client.get(
        "/api/sample/files",
        params={
            "instrument": INSTRUMENT,
            "sort": "sample_file_utc_created",
            "order": "desc",
        },
    )

    assert resp.status_code == 200, resp.text
    assert [row["sample_file_id"] for row in resp.json()["data"]] == [second, first]


@pytest.mark.asyncio
async def test_a_file_without_a_registration_time_sorts_last(
    admin_client, async_session_factory
):
    """Newest first means the files registered before the column come last."""
    legacy = await _add_file(async_session_factory, "legacy")
    await _set(async_session_factory, legacy, sample_file_utc_created=None)
    first = await _add_file(async_session_factory, "first")
    second = await _add_file(async_session_factory, "second")

    resp = await admin_client.get(
        "/api/sample/files",
        params={
            "instrument": INSTRUMENT,
            "sort": "sample_file_utc_created",
            "order": "desc",
        },
    )

    assert resp.status_code == 200, resp.text
    ids = [row["sample_file_id"] for row in resp.json()["data"]]
    assert ids == [second, first, legacy]


@pytest.mark.asyncio
async def test_files_that_tie_on_the_sort_column_keep_one_order(
    admin_client, async_session_factory
):
    """Paging with OFFSET needs a total order, or pages repeat and skip rows."""
    added = [
        await _add_file(async_session_factory, f"tie{i}", "done") for i in range(4)
    ]

    pages = []
    for page in range(2):
        resp = await admin_client.get(
            "/api/sample/files",
            params={
                "instrument": INSTRUMENT,
                "sort": "processing_status",
                "page": page,
                "limit": 2,
            },
        )
        assert resp.status_code == 200, resp.text
        pages += [row["sample_file_id"] for row in resp.json()["data"]]

    assert pages == sorted(added)


@pytest.mark.asyncio
async def test_recent_files_can_be_recent_by_their_processing(
    admin_client, async_session_factory
):
    """A file acquired long ago that failed today is a recent failure."""
    long_ago = datetime.now(timezone.utc) - timedelta(days=90)
    backlog = await _add_file(async_session_factory, "backlog", "failed", long_ago)
    await _set(
        async_session_factory,
        backlog,
        processing_updated_utc=datetime.now(timezone.utc),
    )
    params = {"instrument": INSTRUMENT, "days": 1, "processing_status": "failed"}

    by_acquisition = await admin_client.get("/api/sample/files/recent", params=params)
    by_processing = await admin_client.get(
        "/api/sample/files/recent", params={**params, "recent_by": "processing"}
    )

    assert by_acquisition.status_code == 200, by_acquisition.text
    assert by_acquisition.json()["data"] == []
    assert by_processing.status_code == 200, by_processing.text
    assert [row["sample_file_id"] for row in by_processing.json()["data"]] == [backlog]


@pytest.mark.asyncio
async def test_an_unknown_status_is_refused(admin_client):
    resp = await admin_client.get(
        "/api/sample/files", params={"processing_status": "halfway"}
    )

    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# Processing asked for again
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_file_is_queued_before_re_processing_replaces_it(
    async_session_factory, monkeypatch
):
    """Until the rebuild records its own stages, the old `done` would stand."""
    sample_file_id = await _add_file(async_session_factory, "redo", "done")
    seen = {}

    async def clear(sample_file_id, independent_transaction):
        row = await _row(async_session_factory, sample_file_id)
        seen["status"] = row.processing_status
        return set()

    monkeypatch.setattr(status, "emit_record_updated", AsyncMock())
    monkeypatch.setattr(process_service, "reset_mz_calibration", AsyncMock())
    monkeypatch.setattr(
        process_service, "resolve_ionization_modes_by_tokens", AsyncMock()
    )
    monkeypatch.setattr(process_service, "_clear_sample_items_for_reprocessing", clear)
    monkeypatch.setattr(
        process_service, "auto_process_sample_file", AsyncMock(return_value={})
    )

    await process_service.re_process_sample_files(sample_file_ids=[sample_file_id])

    assert seen["status"] == "queued"
    row = await _row(async_session_factory, sample_file_id)
    assert row.processing_detail == "Queued for re-processing."


@pytest.mark.asyncio
async def test_processing_a_file_on_request_queues_it(
    admin_client, async_session_factory, monkeypatch
):
    sample_file_id = await _add_file(async_session_factory, "again", "done")
    spawn = AsyncMock()
    monkeypatch.setattr(status, "emit_record_updated", AsyncMock())
    monkeypatch.setattr(sample_files_routes, "spawn_auto_process_sample_file", spawn)

    resp = await admin_client.post(f"/api/sample/files/{sample_file_id}/process")

    assert resp.status_code == 202, resp.text
    row = await _row(async_session_factory, sample_file_id)
    assert row.processing_status == "queued"
    spawn.assert_awaited_once()


# ---------------------------------------------------------------------------
# The startup reset
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_restart_fails_the_runs_it_cut_short(async_session_factory):
    interrupted = [
        await _add_file(async_session_factory, "converted", "converted"),
        await _add_file(async_session_factory, "queued", "queued"),
        await _add_file(async_session_factory, "bound", "bound"),
        await _add_file(async_session_factory, "calibrated", "calibrated"),
    ]
    settled = {
        await _add_file(async_session_factory, "done", "done"): "done",
        await _add_file(async_session_factory, "parked", "needs_chemistry"): (
            "needs_chemistry"
        ),
        await _add_file(async_session_factory, "legacy", None): None,
    }

    result = await reset_interrupted_processing()

    assert result["status"] == "success"
    assert result["data"]["reset_count"] >= len(interrupted)
    for sample_file_id in interrupted:
        row = await _row(async_session_factory, sample_file_id)
        assert row.processing_status == "failed"
        assert row.processing_detail == INTERRUPTED_DETAIL
    for sample_file_id, expected in settled.items():
        row = await _row(async_session_factory, sample_file_id)
        assert row.processing_status == expected
