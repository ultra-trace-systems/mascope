"""
Tests: the ionization modes listing and one mode answer a real SDK token.

A sample names its mode by ``ionization_mode_id`` alone, so a script that
knows a mode by its name - ``mascope_viz``'s ``Scope(mode=...)`` - reads the
modes to find its ID. Both reads are the guest's, write nothing, and answer a
token as they answer a session. The flags are pinned in
``unit/api/routes/test_token_access_coverage.py``; here a guest's token, sent
the way the SDK sends it, goes through the auth selector, the role dependency
and the handler. Only the reads are opened: changing a mode still needs the
web app's session, and a token sent under another service's name is refused
before the handler runs.
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from mascope_backend.app.fast import fast
from mascope_backend.db import (
    AccessToken,
    IonizationMechanism,
    IonizationMode,
    hash_access_token,
)
from mascope_backend.db.id import gen_id


MODES = "/api/ionization/modes"


@pytest_asyncio.fixture
async def mode(async_session_factory):
    """A deployment's own mode, so the default listing holds it."""
    mechanism_id, mode_id = gen_id(), gen_id()
    async with async_session_factory() as session:
        session.add(
            IonizationMechanism(
                ionization_mechanism_id=mechanism_id,
                ionization_mechanism_polarity="-",
                ionization_mechanism=f"[M+Br]- {mechanism_id}",
            )
        )
        await session.flush()
        session.add(
            IonizationMode(
                ionization_mode_id=mode_id,
                ionization_mode_name=f"Bromide {mode_id}",
                ionization_mode_token=f"tok{mode_id}",
                ionization_mode_polarity="-",
                ionization_mechanism_ids=[mechanism_id],
            )
        )
        await session.commit()
    yield mode_id
    async with async_session_factory() as session:
        await session.execute(
            delete(IonizationMode).where(IonizationMode.ionization_mode_id == mode_id)
        )
        await session.execute(
            delete(IonizationMechanism).where(
                IonizationMechanism.ionization_mechanism_id == mechanism_id
            )
        )
        await session.commit()


@pytest_asyncio.fixture
async def sdk_token(async_session_factory, test_users):
    """A real ``mascope_sdk`` API token of the guest user."""
    token = f"modes-{gen_id()}".ljust(43, "x")
    async with async_session_factory() as session:
        session.add(
            AccessToken(
                token_hash=hash_access_token(token),
                user_id=test_users["guest"].id,
                service_name="mascope_sdk",
            )
        )
        await session.commit()
    yield token
    async with async_session_factory() as session:
        await session.execute(
            delete(AccessToken).where(
                AccessToken.token_hash == hash_access_token(token)
            )
        )
        await session.commit()


def _token_client(token: str, service: str = "mascope_sdk") -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=fast),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}", "X-Service-Name": service},
    )


@pytest.mark.asyncio
async def test_an_sdk_token_lists_the_modes(sdk_token, mode):
    async with _token_client(sdk_token) as client:
        resp = await client.get(MODES)

    assert resp.status_code == 200, resp.text
    [listed] = [m for m in resp.json()["data"] if m["ionization_mode_id"] == mode]
    assert listed["ionization_mode_name"] == f"Bromide {mode}"
    assert listed["ionization_mode_polarity"] == "-"


@pytest.mark.asyncio
async def test_an_sdk_token_reads_one_mode(sdk_token, mode):
    async with _token_client(sdk_token) as client:
        resp = await client.get(f"{MODES}/{mode}")

    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["ionization_mode_name"] == f"Bromide {mode}"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["", "/{mode}"])
@pytest.mark.parametrize("service", ["file-agent", "file-converter", "sdk"])
async def test_a_token_sent_as_another_service_is_refused(
    sdk_token, mode, path, service
):
    async with _token_client(sdk_token, service) as client:
        resp = await client.get(MODES + path.format(mode=mode))

    assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
async def test_a_token_cannot_change_a_mode(sdk_token, mode):
    """Only the reads are opened: an edit still answers 401 to a token."""
    async with _token_client(sdk_token) as client:
        resp = await client.patch(
            f"{MODES}/{mode}",
            json={
                "ionization_mode_name": "renamed",
                "ionization_mode_polarity": "-",
                "ionization_mechanism_ids": ["x"],
            },
        )

    assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
async def test_a_guest_session_reads_as_before(guest_client, mode):
    listing = await guest_client.get(MODES)
    one = await guest_client.get(f"{MODES}/{mode}")

    assert listing.status_code == 200, listing.text
    assert mode in {m["ionization_mode_id"] for m in listing.json()["data"]}
    assert one.status_code == 200, one.text
