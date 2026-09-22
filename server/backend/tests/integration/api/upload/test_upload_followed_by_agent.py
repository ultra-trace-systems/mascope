"""
Integration tests: what a paired File Agent asks about the files it uploaded.

The agent finds each upload by the name the file had on its machine - the
server keeps it as ``source_filename``, while the stored name carries the
acquisition timestamp - among the files the server registered since the
upload, counted on the server's clock. It first reads whether the server can
be asked at all, from ``GET /api/version`` with its device token. And it sees
the files of its instrument however their instrument is spelled, as its
workspace does.
"""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, update

from mascope_backend.accounts import ACCOUNT_TYPE_MACHINE
from mascope_backend.app.fast import fast
from mascope_backend.db import (
    AccessToken,
    AgentDevice,
    SampleFile,
    User,
    Workspace,
    WorkspaceMember,
)
from mascope_backend.db.id import gen_id


INSTRUMENT = "Follow-Orbi"
WORKSPACE = f"Acquisitions {INSTRUMENT}"


@pytest_asyncio.fixture
async def agent(async_session_factory, test_users, provision_device):
    """A paired agent whose machine account belongs to the instrument's workspace."""
    _device_id, machine, token = await provision_device(
        test_users["editor"].id, machine_name="FOLLOW-PC"
    )
    workspace_id = gen_id(16)
    async with async_session_factory() as session:
        session.add(
            Workspace(
                workspace_id=workspace_id,
                workspace_name=WORKSPACE,
                workspace_status="active",
                is_system=True,
            )
        )
        await session.flush()
        session.add(
            WorkspaceMember(
                workspace_member_id=gen_id(16),
                workspace_id=workspace_id,
                user_id=machine.id,
                workspace_role="editor",
            )
        )
        await session.commit()
    client = AsyncClient(
        transport=ASGITransport(app=fast),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}", "X-Service-Name": "file-agent"},
    )
    async with client:
        yield client
    async with async_session_factory() as session:
        await session.execute(
            delete(SampleFile).where(
                SampleFile.instrument.in_([INSTRUMENT, INSTRUMENT.lower()])
            )
        )
        await session.execute(
            delete(Workspace).where(Workspace.workspace_id == workspace_id)
        )
        await session.execute(
            delete(AccessToken).where(AccessToken.service_name == "file-agent")
        )
        await session.execute(
            delete(User).where(User.account_type == ACCOUNT_TYPE_MACHINE)
        )
        await session.execute(
            delete(AgentDevice).where(AgentDevice.service_name == "file-agent")
        )
        await session.commit()


async def _file(
    async_session_factory,
    source_filename: str,
    registered_ago: timedelta = timedelta(0),
    instrument: str = INSTRUMENT,
) -> str:
    sample_file_id = gen_id()
    async with async_session_factory() as session:
        session.add(
            SampleFile(
                sample_file_id=sample_file_id,
                filename=f"{instrument}_{sample_file_id}",
                instrument=instrument,
                instrument_type="orbi",
                datetime=datetime(2026, 9, 1, 12, 0, 0),
                datetime_utc=datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc),
                length=60.0,
                range=[50.0, 500.0],
                polarity="-",
                source_filename=source_filename,
                processing_status="done",
            )
        )
        await session.commit()
        if registered_ago:
            await session.execute(
                update(SampleFile)
                .where(SampleFile.sample_file_id == sample_file_id)
                .values(
                    sample_file_utc_created=datetime.now(timezone.utc) - registered_ago
                )
            )
            await session.commit()
    return sample_file_id


@pytest.mark.asyncio
async def test_a_paired_agent_reads_what_the_server_can_do(agent):
    resp = await agent.get("/api/version")

    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["capabilities"]["files_listed_by_source_filename"]


@pytest.mark.asyncio
async def test_an_agent_finds_its_upload_by_its_name_among_the_latest(
    agent, async_session_factory
):
    """Not an earlier upload of the same name, and not another file."""
    await _file(async_session_factory, "x.raw", registered_ago=timedelta(days=1))
    latest = await _file(async_session_factory, "x.raw")
    await _file(async_session_factory, "y.raw")

    resp = await agent.get(
        "/api/sample/files",
        params={
            "source_filename": "x.raw",
            "registered_within": 300,
            "sort": "sample_file_utc_created",
            "order": "desc",
            "page": 0,
            "limit": 1,
        },
    )

    assert resp.status_code == 200, resp.text
    (row,) = resp.json()["data"]
    assert row["sample_file_id"] == latest
    assert row["processing_status"] == "done"


@pytest.mark.asyncio
async def test_an_agent_sees_its_instruments_files_however_they_are_spelled(
    agent, async_session_factory
):
    """Recorded as "follow-orbi", the file is still the workspace's."""
    lower = await _file(async_session_factory, "z.raw", instrument=INSTRUMENT.lower())

    resp = await agent.get("/api/sample/files", params={"source_filename": "z.raw"})

    assert resp.status_code == 200, resp.text
    assert [row["sample_file_id"] for row in resp.json()["data"]] == [lower]


@pytest.mark.asyncio
async def test_a_registration_window_must_be_positive(agent):
    resp = await agent.get("/api/sample/files", params={"registered_within": 0})

    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_an_earlier_upload_of_the_name_is_not_taken_for_this_one(
    agent, async_session_factory
):
    """Until this upload is registered, the answer is that there is none yet."""
    await _file(async_session_factory, "w.raw", registered_ago=timedelta(days=1))

    resp = await agent.get(
        "/api/sample/files",
        params={"source_filename": "w.raw", "registered_within": 300},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["data"] == []
