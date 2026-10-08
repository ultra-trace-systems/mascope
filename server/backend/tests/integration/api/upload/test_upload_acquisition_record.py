"""
Integration tests: an upload's acquisition record and hash, kept on its file.

The program that ran an acquisition can send a record of it with the file's
upload - which step of which run acquired the file, under which chemistry -
and the uploading agent sends the file's SHA-256 beside it
(``docs/dev/acquisition_sidecar.md``). These tests follow the two from the
door to the row: what is refused when an upload is created, what is checked
when it has arrived, and what the registration the converter posts back
leaves on the sample file and shows of it.

One rule runs through all of them: neither the record nor the hash costs a
file its place on the server.

Each test uses its own instrument name, as the attribution tests beside this
one do, so that no test's file decides another's.
"""

import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.orm import undefer
from test_utils import captured_logs

from mascope_backend.api.controllers.sample.files import sample_files_controller
from mascope_backend.api.controllers.sample.files.process import (
    service as process_service,
)
from mascope_backend.api.routes.sample.files import (
    sample_files_routes as files_routes,
)
from mascope_backend.capabilities import SERVER_CAPABILITIES
from mascope_backend.db import SampleFile
from mascope_sdk import acquisition


INSTRUMENTS = tuple(f"acqrec-orbi-{letter}" for letter in "abcdefg")

RECORD = {
    "schema": "mascope-acquisition/1",
    "source_filename": "run_0042.raw",
    "agent_id": "3f0e8f0c-5d0b-4c7e-9a43-0d8f6c1b2a10",
    "sequence_run_id": "0199b6a0-7c00-7000-8000-000000000001",
    "step_id": "0199b6a0-7c00-7000-8000-000000000002",
    "acquisition_id": "0199b6a0-7c00-7000-8000-000000000003",
    "ionization": "NO3",
    "later": {"kept": True},
}
SHA256 = "ab" * 32


@pytest_asyncio.fixture(autouse=True)
async def clean_state(async_session_factory):
    """Remove the files these tests register, so the shared database is unchanged."""
    yield
    async with async_session_factory() as session:
        await session.execute(
            delete(SampleFile).where(SampleFile.instrument.in_(INSTRUMENTS))
        )
        await session.commit()


@pytest.fixture(autouse=True)
def _no_post_create_work(monkeypatch):
    """Stub what a registration kicks off after the row is written: neither
    is under test, and the pipeline would look for a file never uploaded."""
    monkeypatch.setattr(
        process_service, "spawn_auto_process_sample_file", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        sample_files_controller,
        "create_acquisition_datasets",
        AsyncMock(return_value={"results": 0, "data": []}),
    )


def _registration(instrument: str, **carried) -> dict:
    """The body the converter posts back once a file has been converted."""
    return {
        "filename": f"{instrument}_20260101_0000_.raw",
        "instrument": instrument,
        "datetime": "2026-01-01T00:00:00",
        "datetime_utc": "2026-01-01T00:00:00Z",
        "length": 60.0,
        "range": [0, 500],
        "polarity": "-",
        **carried,
    }


def _record(number: int, **changes) -> dict:
    """A record of its own acquisition, so the tests' files do not collide."""
    return {
        **RECORD,
        "acquisition_id": f"0199b6a0-7c00-7000-8000-0000000001{number:02d}",
        **changes,
    }


def _said(records: list[dict]) -> list[str]:
    """The messages of the log records a block emitted."""
    return [record["message"] for record in records]


async def _without_a_record(async_session_factory, filename: str) -> bool:
    """Whether the database holds the file with no record: ``IS NULL``."""
    async with async_session_factory() as session:
        return bool(
            await session.scalar(
                select(func.count())
                .select_from(SampleFile)
                .where(
                    SampleFile.filename == filename,
                    SampleFile.acquisition.is_(None),
                )
            )
        )


async def _stored(async_session_factory, filename: str) -> SampleFile:
    async with async_session_factory() as session:
        return (
            await session.execute(
                select(SampleFile)
                .where(SampleFile.filename == filename)
                .options(undefer(SampleFile.acquisition))
            )
        ).scalar_one()


# ---------------------------------------------------------------------------
# Announced
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_server_announces_that_it_keeps_them(editor_client):
    """An agent sends neither to a server that does not say this."""
    assert SERVER_CAPABILITIES[acquisition.CAPABILITY] is True

    resp = await editor_client.get("/api/version")

    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["capabilities"][acquisition.CAPABILITY] is True


# ---------------------------------------------------------------------------
# On the sample file
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_registration_keeps_the_record_its_identifiers_and_the_hash(
    async_session_factory, editor_client
):
    record = _record(1)
    body = _registration(
        INSTRUMENTS[0],
        acquisition=record,
        sha256=SHA256,
        source_filename=record["source_filename"],
    )

    resp = await editor_client.post("/api/sample/files", json=body)
    assert resp.status_code == 201, resp.text

    stored = await _stored(async_session_factory, body["filename"])
    # As it was sent, the field this version does not know included.
    assert stored.acquisition == record
    assert stored.acquisition_id == record["acquisition_id"]
    assert stored.step_id == record["step_id"]
    assert stored.sequence_run_id == record["sequence_run_id"]
    assert stored.agent_id == record["agent_id"]
    assert stored.sha256 == SHA256

    # What the registration answers with is what a listing and every event
    # of the file carry: the identifiers and the hash, not the whole record.
    data = resp.json()["data"]
    assert data["acquisition_id"] == record["acquisition_id"]
    assert data["sha256"] == SHA256
    assert "acquisition" not in data


@pytest.mark.asyncio
async def test_the_file_itself_shows_its_whole_record_and_a_listing_does_not(
    async_session_factory, editor_client, admin_client
):
    record = _record(2)
    body = _registration(INSTRUMENTS[1], acquisition=record)
    created = await editor_client.post("/api/sample/files", json=body)
    assert created.status_code == 201, created.text
    sample_file_id = created.json()["data"]["sample_file_id"]

    # Read as an admin: the instrument's workspace, which is what lets an
    # editor read its files, is not created here.
    one = await admin_client.get(f"/api/sample/files/{sample_file_id}")
    listed = await admin_client.get(
        "/api/sample/files", params={"filename": body["filename"]}
    )

    assert one.status_code == 200, one.text
    assert one.json()["data"]["acquisition"] == record
    assert listed.status_code == 200, listed.text
    (row,) = listed.json()["data"]
    assert "acquisition" not in row
    assert row["step_id"] == record["step_id"]


@pytest.mark.asyncio
async def test_every_file_of_a_run_is_found_by_the_runs_identifier(
    async_session_factory, editor_client
):
    for number, instrument in ((3, INSTRUMENTS[2]), (4, INSTRUMENTS[3])):
        resp = await editor_client.post(
            "/api/sample/files",
            json=_registration(instrument, acquisition=_record(number)),
        )
        assert resp.status_code == 201, resp.text

    async with async_session_factory() as session:
        of_the_run = (
            await session.scalars(
                select(SampleFile.instrument)
                .where(SampleFile.sequence_run_id == RECORD["sequence_run_id"])
                .where(SampleFile.instrument.in_(INSTRUMENTS[2:4]))
                .order_by(SampleFile.instrument)
            )
        ).all()

    assert of_the_run == [INSTRUMENTS[2], INSTRUMENTS[3]]


@pytest.mark.asyncio
async def test_a_file_registered_with_neither_has_neither(
    async_session_factory, editor_client
):
    body = _registration(INSTRUMENTS[4])

    resp = await editor_client.post("/api/sample/files", json=body)
    assert resp.status_code == 201, resp.text

    stored = await _stored(async_session_factory, body["filename"])
    assert stored.acquisition is None
    assert stored.acquisition_id is None
    assert stored.sha256 is None
    # Asked of the database and not of the row: a column that holds the JSON
    # value null reads as None too, and is a record that says nothing.
    assert await _without_a_record(async_session_factory, body["filename"])


@pytest.mark.asyncio
async def test_an_acquisition_is_one_file(async_session_factory, editor_client):
    """A second file naming it is kept, and its record is not."""
    record = _record(5)
    first = _registration(INSTRUMENTS[5], acquisition=record)
    second = _registration(INSTRUMENTS[6], acquisition=record, sha256=SHA256)
    assert (
        await editor_client.post("/api/sample/files", json=first)
    ).status_code == 201

    with captured_logs("WARNING") as lines:
        resp = await editor_client.post("/api/sample/files", json=second)
    assert resp.status_code == 201, resp.text

    kept = await _stored(async_session_factory, first["filename"])
    newcomer = await _stored(async_session_factory, second["filename"])
    assert kept.acquisition_id == record["acquisition_id"]
    assert newcomer.acquisition is None
    assert newcomer.acquisition_id is None
    assert newcomer.step_id is None
    assert await _without_a_record(async_session_factory, second["filename"])
    assert not await _without_a_record(async_session_factory, first["filename"])
    # What was checked about the file itself is still recorded.
    assert newcomer.sha256 == SHA256
    assert any(
        first["filename"] in line and "an acquisition is one file" in line
        for line in _said(lines)
    )


@pytest.mark.asyncio
async def test_a_registration_whose_record_is_no_record_still_registers_the_file(
    async_session_factory, editor_client
):
    body = _registration(
        INSTRUMENTS[0], acquisition={**_record(6), "acquisition_id": "not-a-uuid"}
    )

    resp = await editor_client.post("/api/sample/files", json=body)
    assert resp.status_code == 201, resp.text

    stored = await _stored(async_session_factory, body["filename"])
    assert stored.acquisition is None
    assert stored.acquisition_id is None


# ---------------------------------------------------------------------------
# At the door
# ---------------------------------------------------------------------------


@pytest.fixture
def an_open_door(monkeypatch):
    """Every other admission check passing, so a refusal is the record's."""
    monkeypatch.setattr(files_routes, "ensure_converter_available", AsyncMock())
    monkeypatch.setattr(files_routes, "_sweep_abandoned_partials", lambda: None)
    monkeypatch.setattr(files_routes, "_reject_when_disk_is_low", lambda info: None)


def _upload(**metadata) -> dict:
    return {
        "filename": "Orbi-Lab2_run_0042.raw",
        "filetype": "application/octet-stream",
        "source_filename": "run_0042.raw",
        **metadata,
    }


@pytest.mark.asyncio
async def test_an_upload_with_a_record_is_let_in(an_open_door):
    await files_routes._tus_pre_create_hook(
        _upload(acquisition=json.dumps(RECORD)), {"size": 10}
    )


@pytest.mark.parametrize(
    "document, reason",
    [
        ("{not json", "it is not JSON"),
        (
            json.dumps({**RECORD, "schema": "mascope-acquisition/2"}),
            "its schema is 'mascope-acquisition/2'",
        ),
        (
            json.dumps({**RECORD, "source_filename": "run_0041.raw"}),
            "it is the record of 'run_0041.raw', and the upload is 'run_0042.raw'",
        ),
    ],
)
@pytest.mark.asyncio
async def test_an_upload_whose_record_cannot_be_kept_is_refused_when_it_is_created(
    an_open_door, document, reason
):
    """While the uploader can still act on it: the agent reads why, and sends
    the file again without the record."""
    with pytest.raises(HTTPException) as refused:
        await files_routes._tus_pre_create_hook(
            _upload(acquisition=document), {"size": 10}
        )

    assert refused.value.status_code == 422
    assert refused.value.detail.startswith(
        "The acquisition record sent with this upload is not kept, as "
    )
    assert reason in refused.value.detail
    assert refused.value.detail.endswith("Send the file without it.")


@pytest.mark.asyncio
async def test_an_upload_is_not_refused_over_a_hash_that_is_none(an_open_door):
    """There is no agent-side second try for a hash, so nothing to refuse to."""
    await files_routes._tus_pre_create_hook(_upload(sha256="not-a-hash"), {"size": 10})


# ---------------------------------------------------------------------------
# Once it has arrived
# ---------------------------------------------------------------------------


@pytest.fixture
def arrived(tmp_path):
    path = tmp_path / "Orbi-Lab2_run_0042.raw"
    path.write_bytes(b"raw-bytes")
    return str(path)


@pytest.mark.asyncio
async def test_a_hash_the_bytes_received_have_is_recorded(arrived):
    sent = hashlib.sha256(b"raw-bytes").hexdigest()

    record, sha256 = await files_routes._provenance_of_upload(
        arrived, _upload(acquisition=json.dumps(RECORD), sha256=sent), "x.raw"
    )

    assert record == RECORD
    assert sha256 == sent


@pytest.mark.asyncio
async def test_a_hash_the_bytes_received_do_not_have_is_not_recorded(arrived):
    """The file is kept all the same: nothing can un-accept it now."""
    sent = hashlib.sha256(b"what was sent").hexdigest()

    with captured_logs("WARNING") as lines:
        record, sha256 = await files_routes._provenance_of_upload(
            arrived, _upload(acquisition=json.dumps(RECORD), sha256=sent), "x.raw"
        )

    assert record == RECORD
    assert sha256 is None
    (line,) = [line for line in _said(lines) if "did not arrive as it was sent" in line]
    assert sent in line
    assert hashlib.sha256(b"raw-bytes").hexdigest() in line


@pytest.mark.asyncio
async def test_an_upload_that_reports_no_hash_is_not_hashed(arrived, monkeypatch):
    monkeypatch.setattr(
        files_routes, "file_sha256", lambda path: pytest.fail("hashed for nobody")
    )

    assert await files_routes._provenance_of_upload(arrived, _upload(), "x.raw") == (
        None,
        None,
    )


@pytest.mark.asyncio
async def test_a_file_that_cannot_be_hashed_is_kept_without_one(tmp_path):
    gone = str(tmp_path / "gone.raw")

    with captured_logs("WARNING") as lines:
        record, sha256 = await files_routes._provenance_of_upload(
            gone, _upload(sha256="a" * 64), "gone.raw"
        )

    assert (record, sha256) == (None, None)
    assert any("Could not hash 'gone.raw'" in line for line in _said(lines))


@pytest.mark.asyncio
async def test_a_record_that_went_bad_since_the_door_is_dropped_not_raised(arrived):
    with captured_logs("WARNING") as lines:
        record, sha256 = await files_routes._provenance_of_upload(
            arrived, _upload(acquisition="{not json"), "x.raw"
        )

    assert (record, sha256) == (None, None)
    assert any("the file is stored without it" in line for line in _said(lines))


# ---------------------------------------------------------------------------
# From the door to the converter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_finished_upload_hands_on_its_record_and_its_verified_hash(
    tmp_path, monkeypatch
):
    stored = AsyncMock()
    monkeypatch.setattr(files_routes, "upload_sample_file", stored)
    for check in (
        "check_upload_matches_the_instrument_class",
        "check_instrument_workspace_access",
        "record_reported_instrument",
    ):
        monkeypatch.setattr(files_routes, check, AsyncMock())
    monkeypatch.setattr(files_routes, "get_access_token", AsyncMock(return_value="t"))
    monkeypatch.setattr(files_routes, "_request_device_id", lambda request: None)
    spooled = tmp_path / "abc123"
    spooled.write_bytes(b"raw-bytes")
    sent = hashlib.sha256(b"raw-bytes").hexdigest()

    finished = files_routes.get_upload_handler(request=None, user=SimpleNamespace(id=1))
    await finished(
        str(spooled),
        _upload(acquisition=json.dumps(RECORD), sha256=sent, instrument="Orbi-Lab2"),
    )

    assert stored.await_args.kwargs["acquisition"] == RECORD
    assert stored.await_args.kwargs["sha256"] == sent


@pytest.mark.asyncio
async def test_storing_an_upload_tells_the_converter_what_it_carried(
    tmp_path, monkeypatch
):
    told = AsyncMock()
    monkeypatch.setattr(sample_files_controller, "_register_file_with_converter", told)
    # Nothing is moved: what is told is all this looks at.
    monkeypatch.setattr(sample_files_controller.shutil, "move", lambda src, dst: None)
    monkeypatch.setattr(sample_files_controller.os, "replace", lambda src, dst: None)

    await sample_files_controller.upload_sample_file(
        str(tmp_path / "Orbi-Lab2_run_0042.raw"),
        user=SimpleNamespace(id=1, username="machine", role_id=2),
        access_token="t",
        acquisition=RECORD,
        sha256=SHA256,
    )

    assert told.await_args.kwargs["acquisition"] == RECORD
    assert told.await_args.kwargs["sha256"] == SHA256


# ---------------------------------------------------------------------------
# The registration's own door
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "changes, reason",
    [
        (
            {"acquisition_id": "0199B6A0-7C00-7000-8000-000000000190"},
            "a UUID is written in lowercase",
        ),
        ({"triggered_at": "1791374400"}, "a time is written as"),
        ({"later": "p" * 20_000}, "16384 at most"),
    ],
)
@pytest.mark.asyncio
async def test_the_registration_reads_a_record_by_the_uploads_rule(
    async_session_factory, editor_client, changes, reason
):
    """What the upload's door refuses, this one leaves out: the file is
    registered, and its record is not kept in a spelling the other door
    would not have let in."""
    body = _registration(INSTRUMENTS[0], acquisition={**_record(7), **changes})

    with captured_logs("WARNING") as lines:
        resp = await editor_client.post("/api/sample/files", json=body)
    assert resp.status_code == 201, resp.text

    assert await _without_a_record(async_session_factory, body["filename"])
    stored = await _stored(async_session_factory, body["filename"])
    assert stored.acquisition_id is None
    assert any("is not kept, as" in line and reason in line for line in _said(lines))


@pytest.mark.asyncio
async def test_the_record_of_another_file_is_not_registered_with_this_one(
    async_session_factory, editor_client
):
    body = _registration(
        INSTRUMENTS[1], acquisition=_record(8), source_filename="run_0041.raw"
    )

    resp = await editor_client.post("/api/sample/files", json=body)
    assert resp.status_code == 201, resp.text

    assert await _without_a_record(async_session_factory, body["filename"])


@pytest.mark.parametrize("written", ["1e999", "NaN"])
@pytest.mark.asyncio
async def test_a_registration_holding_a_number_json_cannot_write_keeps_the_file(
    async_session_factory, editor_client, written
):
    """A request body can hold one. The database would refuse the row for
    it, and the file would be the one to pay."""
    body = _registration(INSTRUMENTS[2], acquisition=_record(9))
    raw = json.dumps(body).replace(
        '"ionization": "NO3"', '"ionization": "NO3", "setpoints": {"a.b": %s}' % written
    )
    assert written in raw

    resp = await editor_client.post(
        "/api/sample/files", content=raw, headers={"Content-Type": "application/json"}
    )
    assert resp.status_code == 201, resp.text

    assert await _without_a_record(async_session_factory, body["filename"])


@pytest.mark.asyncio
async def test_a_row_the_database_refuses_with_its_record_is_stored_without_it(
    async_session_factory, editor_client, monkeypatch
):
    """Two registrations of one acquisition, the second past the check before
    the first has committed: the unique column refuses the row. The file is
    what must not be lost."""
    record = _record(10)
    first = _registration(INSTRUMENTS[3], acquisition=record)
    second = _registration(INSTRUMENTS[4], acquisition=record, sha256=SHA256)
    assert (
        await editor_client.post("/api/sample/files", json=first)
    ).status_code == 201

    async def past_the_check(session, filename, document, source_filename):
        return document, sample_files_controller.record_ids(document)

    monkeypatch.setattr(sample_files_controller, "_record_to_keep", past_the_check)

    with captured_logs("WARNING") as lines:
        resp = await editor_client.post("/api/sample/files", json=second)
    assert resp.status_code == 201, resp.text

    newcomer = await _stored(async_session_factory, second["filename"])
    assert newcomer.acquisition_id is None
    assert newcomer.sha256 == SHA256
    assert await _without_a_record(async_session_factory, second["filename"])
    assert resp.json()["data"]["acquisition_id"] is None
    assert any("was refused by the database" in line for line in _said(lines))


@pytest.mark.asyncio
async def test_a_row_refused_for_another_reason_is_refused(editor_client, monkeypatch):
    """Only a row with a record gets a second try without it."""
    body = _registration(INSTRUMENTS[5])
    assert (await editor_client.post("/api/sample/files", json=body)).status_code == 201
    # The same file name again, past the check for one already registered.
    monkeypatch.setattr(
        sample_files_controller,
        "get_sample_files",
        AsyncMock(return_value={"results": 0}),
    )

    with captured_logs("WARNING") as lines:
        resp = await editor_client.post("/api/sample/files", json=body)

    assert resp.status_code >= 400
    assert not any("was refused by the database" in line for line in _said(lines))


@pytest.mark.parametrize("sha256", ["AB" * 32, "ab" * 31, "not-a-hash"])
@pytest.mark.asyncio
async def test_a_registration_with_a_hash_that_is_none_is_refused(
    editor_client, sha256
):
    """The upload route hands the converter a hash it has checked, or none.
    Anything else in the field is a registration made up by hand."""
    resp = await editor_client.post(
        "/api/sample/files", json=_registration(INSTRUMENTS[6], sha256=sha256)
    )

    assert resp.status_code == 422, resp.text
