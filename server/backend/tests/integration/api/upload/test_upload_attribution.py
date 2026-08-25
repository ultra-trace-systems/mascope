"""
Integration tests: who, and which machine, an upload is attributed to.

``POST /api/sample/files`` stamps two attributions on the row it creates, and
they are decided in opposite ways. The user comes from the authenticated
request and never from the body. The device *is* carried in the body - the
converter writes this record back on its own token, long after the agent's
request ended, so it cannot be derived from the caller's binding - and is
therefore honoured only when the caller is the machine account that device
authenticates as.

That last condition is the whole forgery guard, and a guard that quietly stops
guarding looks exactly like one that works: without it any editor could stamp a
file with another site's instrument, and every existing test would still pass.
So both halves are pinned over real HTTP here, together with the rule that
makes the guard safe to have at all - a device id the caller does not own, or
one that names no device, degrades to unattributed and still creates the file.
Attribution must never fail an ingest.

Each test uses its own instrument name so that the acquisition workspace one
test's upload creates cannot decide the next test's ACL.
"""

from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select

from mascope_backend.accounts import ACCOUNT_TYPE_MACHINE
from mascope_backend.api.controllers.sample.files import sample_files_controller
from mascope_backend.api.controllers.sample.files.process import (
    service as process_service,
)
from mascope_backend.api.new.auth.access_token.service import create_access_token
from mascope_backend.app.fast import fast
from mascope_backend.db import AgentDevice, SampleFile, User


#: One instrument per test. They must satisfy ``validate_instrument_name``
#: (letters, digits and hyphens only) and resolve to a known instrument type,
#: which is what the "orbi" fragment is for.
INSTRUMENT_AGENT = "attrib-orbi-agent"
INSTRUMENT_FORGED = "attrib-orbi-forged"
INSTRUMENT_GHOST = "attrib-orbi-ghost"

ALL_INSTRUMENTS = (INSTRUMENT_AGENT, INSTRUMENT_FORGED, INSTRUMENT_GHOST)

#: The machines these tests pair, one per test for the same reason the
#: instruments are one per test. Bound to names here so the teardown deletes
#: exactly them - a literal at the call site could drift out of that set and
#: leave a device behind in the shared database.
MACHINE_AGENT = "ORBI-ATTRIB"
MACHINE_FORGED = "ORBI-FORGED"
MACHINE_GHOST = "ORBI-GHOST"

ALL_MACHINES = (MACHINE_AGENT, MACHINE_FORGED, MACHINE_GHOST)


@pytest_asyncio.fixture(autouse=True)
async def clean_state(async_session_factory):
    """Remove what these tests create, so the suite's shared database is unchanged.

    Deleting the machine accounts cascades to their access tokens
    (``access_token.user_id`` is ``ON DELETE CASCADE``), and the device rows go
    last because everything pointing at them is ``ON DELETE SET NULL`` - which
    is also why the accounts are selected through the devices while those rows
    still carry the link.

    Scoped to this module's own devices rather than to every machine account
    and every file-agent device in the database. The integration database is
    shared for the whole session, so a blanket sweep running after each of
    these tests would reach into whatever another module had paired - the same
    reason each test here uses an instrument of its own.
    """
    yield
    async with async_session_factory() as session:
        await session.execute(
            delete(SampleFile).where(SampleFile.instrument.in_(ALL_INSTRUMENTS))
        )
        await session.execute(
            delete(User).where(
                User.account_type == ACCOUNT_TYPE_MACHINE,
                User.id.in_(
                    select(AgentDevice.machine_user_id).where(
                        AgentDevice.name.in_(ALL_MACHINES)
                    )
                ),
            )
        )
        await session.execute(
            delete(AgentDevice).where(AgentDevice.name.in_(ALL_MACHINES))
        )
        await session.commit()


@pytest.fixture(autouse=True)
def _no_post_create_work(monkeypatch):
    """Stub the two things creation kicks off after the row is written.

    Neither is under test here, and both would otherwise decide whether these
    tests pass for reasons that have nothing to do with attribution:

    * ``spawn_auto_process_sample_file`` is queued as a BackgroundTask, which
      ASGITransport runs to completion before the response is handed back - so
      the real one would try to convert a file that was never uploaded.
    * ``create_acquisition_datasets`` runs whenever the instrument is new, and
      it iterates over *every* instrument that has sample files, validating
      each name. One session-scoped fixture elsewhere in this suite commits a
      file whose instrument is ``" Test-Orbion "`` (with the spaces), which
      fails that validation - so leaving this unstubbed makes these tests pass
      or fail on collection order.

    Both are patched on the module attribute the controller reads: the
    auto-process import is function-local and resolves at call time, and
    ``create_acquisition_datasets`` is bound on ``sample_files_controller``.
    """
    monkeypatch.setattr(
        process_service, "spawn_auto_process_sample_file", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        sample_files_controller,
        "create_acquisition_datasets",
        AsyncMock(return_value={"results": 0, "data": []}),
    )


def _bearer_client(token: str, service: str) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=fast),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}", "X-Service-Name": service},
    )


def _payload(
    instrument: str, device_id: int | None, forged_user_id: int | None = None
) -> dict:
    """The body the converter posts back once a file has been converted.

    A subset of what ``file_converter/api.py`` sends - the omitted fields
    (method_file, mz_calibration, instrument_function_id, acquisition_timezone,
    utc_offset_source) are all optional and irrelevant to attribution.
    """
    body = {
        "filename": f"{instrument}_20260101_0000_.raw",
        "instrument": instrument,
        "datetime": "2026-01-01T00:00:00",
        "datetime_utc": "2026-01-01T00:00:00Z",
        "length": 60.0,
        "range": [0, 500],
        "polarity": "+",
    }
    if device_id is not None:
        body["uploaded_by_device_id"] = device_id
    if forged_user_id is not None:
        # Not a field of SampleFileCreate today, so pydantic drops it. Sent
        # anyway: the day someone adds it to the schema the way
        # uploaded_by_device_id was added, this is what notices.
        body["uploaded_by_user_id"] = forged_user_id
    return body


async def _stored(async_session_factory, filename: str) -> SampleFile:
    async with async_session_factory() as session:
        return (
            await session.execute(
                select(SampleFile).where(SampleFile.filename == filename)
            )
        ).scalar_one()


@pytest.mark.asyncio
async def test_agent_upload_records_its_device_and_machine_account(
    async_session_factory, test_users, provision_device
):
    """The round trip: a paired machine's upload is attributed to both.

    This is the only path that ends with a non-NULL ``uploaded_by_device_id``,
    so if the column stops being written nothing else in the suite notices.
    """
    device_id, machine, _agent_token = await provision_device(
        test_users["editor"].id, machine_name=MACHINE_AGENT
    )
    # The converter authenticates as the machine account on its own unbound
    # file-converter token - this is the credential that writes the record.
    converter_token = await create_access_token(
        user=machine, service_name="file-converter"
    )

    body = _payload(INSTRUMENT_AGENT, device_id)
    async with _bearer_client(converter_token, "file-converter") as client:
        resp = await client.post("/api/sample/files", json=body)
    assert resp.status_code == 201, resp.text

    stored = await _stored(async_session_factory, body["filename"])
    assert stored.uploaded_by_device_id == device_id
    assert stored.uploaded_by_user_id == machine.id

    # The response reports the same attribution the row holds, so a client
    # cannot be told one thing while the database records another.
    data = resp.json()["data"]
    assert data["uploaded_by_device_id"] == device_id
    assert data["uploaded_by_user_id"] == machine.id


@pytest.mark.asyncio
async def test_a_user_cannot_claim_a_device_they_do_not_authenticate_as(
    async_session_factory, test_users, provision_device, editor_client
):
    """The forgery guard: a real device id, claimed by someone else, is dropped.

    The editor here is even the device's *sponsor* - the person who approved
    the pairing - and still may not stamp a file with it. Attribution names the
    machine that produced the file; anything weaker would let any editor
    attribute an upload to another site's instrument.

    The body also names the machine account as the uploading user, which is the
    other half of the rule: the user is taken from the authenticated request,
    never from the body.
    """
    device_id, machine, _agent_token = await provision_device(
        test_users["editor"].id, machine_name=MACHINE_FORGED
    )
    assert machine.id != test_users["editor"].id  # the claim really is someone else's

    body = _payload(INSTRUMENT_FORGED, device_id, forged_user_id=machine.id)
    resp = await editor_client.post("/api/sample/files", json=body)

    # Refused attribution, not a refused upload - and the user is the
    # authenticated caller, never the one the body named.
    assert resp.status_code == 201, resp.text
    stored = await _stored(async_session_factory, body["filename"])
    assert stored.uploaded_by_device_id is None
    assert stored.uploaded_by_user_id == test_users["editor"].id


@pytest.mark.asyncio
async def test_an_unknown_device_id_degrades_to_unattributed(
    async_session_factory, test_users, provision_device
):
    """A device id naming no row stores NULL and still creates the file.

    The real case is a pairing revoked and its device deleted between the
    upload and the converter writing the record back. Without the lookup the
    insert would hit the foreign key and the ingest would be lost to a 500 -
    the file is on disk by then and nothing re-posts it.
    """
    _device_id, machine, _agent_token = await provision_device(
        test_users["editor"].id, machine_name=MACHINE_GHOST
    )
    converter_token = await create_access_token(
        user=machine, service_name="file-converter"
    )

    async with async_session_factory() as session:
        highest = (
            await session.execute(select(func.max(AgentDevice.device_id)))
        ).scalar()
    missing_device_id = (highest or 0) + 1000

    body = _payload(INSTRUMENT_GHOST, missing_device_id)
    async with _bearer_client(converter_token, "file-converter") as client:
        resp = await client.post("/api/sample/files", json=body)
    assert resp.status_code == 201, resp.text

    stored = await _stored(async_session_factory, body["filename"])
    assert stored.uploaded_by_device_id is None
    assert stored.uploaded_by_user_id == machine.id
