"""
Tests: the sample rematch route answers the file converter's token and no other.

``POST /api/match/rematch/sample/{id}`` deletes a sample's matches (all of them
with ``full_remove``) and recomputes them. The file converter's peak-recompute
worker posts to it after every peak recompute, with a ``file-converter`` token
of the user who asked; an SDK or agent token holder must not reach it
(GitHub #2354). The web app reaches it with its session. A token-driven
rematch is logged with the service and the user.

The narrowing itself is pinned in ``unit/api/routes/test_token_access_coverage.py``;
here real tokens of the editor test user go through the auth selector, the
role dependency and the handler. The rematch is replaced by a recorder, so
nothing is recomputed.

Fixtures used:
- ``alpha_item`` - a sample in ``ws_alpha``, where the editor is an editor
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, text
from test_utils import captured_logs

from mascope_backend.api.routes.match import match_routes
from mascope_backend.app.fast import fast
from mascope_backend.db import AccessToken, Sample, hash_access_token
from mascope_backend.db.id import gen_id


@pytest_asyncio.fixture(scope="session")
async def sample_view(async_engine):
    """The ``sample_view`` VIEW the route finds the sample through.

    ``Base.metadata.create_all`` does not create it; see
    ``integration/api/match/conftest.py``, which does the same.
    """
    async with async_engine.begin() as conn:
        await conn.execute(text(Sample.drop_view()))
        await conn.execute(text(Sample.create_view()))


@pytest.fixture
def rematches(monkeypatch, sample_view):
    """The rematches the route scheduled, instead of running them."""
    scheduled = []

    async def record(**kwargs):
        scheduled.append(kwargs)

    monkeypatch.setattr(match_routes, "rematch_sample", record)
    return scheduled


@pytest_asyncio.fixture
async def token_client(async_session_factory, test_users):
    """A client sending a real token of the editor, minted for ``service``."""
    tokens = []

    def _client(service: str) -> AsyncClient:
        token = f"rematch-{gen_id()}".ljust(43, "x")
        tokens.append((token, service))
        return AsyncClient(
            transport=ASGITransport(app=fast),
            base_url="http://test",
            headers={"Authorization": f"Bearer {token}", "X-Service-Name": service},
        )

    async def _mint():
        async with async_session_factory() as session:
            for token, service in tokens:
                session.add(
                    AccessToken(
                        token_hash=hash_access_token(token),
                        user_id=test_users["editor"].id,
                        service_name=service,
                    )
                )
            await session.commit()

    _client.mint = _mint
    yield _client
    async with async_session_factory() as session:
        await session.execute(
            delete(AccessToken).where(
                AccessToken.token_hash.in_(
                    [hash_access_token(token) for token, _ in tokens]
                )
            )
        )
        await session.commit()


def _url(sample_item_id: str) -> str:
    return f"/api/match/rematch/sample/{sample_item_id}"


@pytest.mark.asyncio
async def test_the_file_converter_token_rematches(
    token_client, rematches, alpha_item, test_users
):
    client = token_client("file-converter")
    await token_client.mint()

    async with client:
        resp = await client.post(_url(alpha_item), params={"full_remove": "true"})

    assert resp.status_code == 202, resp.text
    assert [
        (r["sample_item_id"], r["full_remove"], r["user_id"]) for r in rematches
    ] == [(alpha_item, True, test_users["editor"].id)]


@pytest.mark.asyncio
@pytest.mark.parametrize("service", ["mascope_sdk", "ai-agent", "file-agent"])
async def test_another_services_token_is_refused(
    token_client, rematches, alpha_item, service
):
    client = token_client(service)
    await token_client.mint()

    async with client:
        resp = await client.post(_url(alpha_item), params={"full_remove": "true"})

    assert resp.status_code == 401, resp.text
    assert rematches == []


@pytest.mark.asyncio
async def test_the_web_app_session_still_rematches(
    editor_client, rematches, alpha_item
):
    resp = await editor_client.post(_url(alpha_item))

    assert resp.status_code == 202, resp.text
    assert [r["sample_item_id"] for r in rematches] == [alpha_item]


@pytest.mark.asyncio
async def test_a_token_driven_rematch_is_logged(
    token_client, rematches, alpha_item, test_users
):
    client = token_client("file-converter")
    await token_client.mint()

    with captured_logs("INFO") as records:
        async with client:
            resp = await client.post(_url(alpha_item), params={"full_remove": "true"})

    assert resp.status_code == 202, resp.text
    process_id = resp.headers["Process-ID"]
    assert [
        r["message"] for r in records if r["message"].startswith("Rematch of sample")
    ] == [
        f"Rematch of sample {alpha_item} requested by the file-converter token of "
        f"user {test_users['editor'].id} (full_remove=True, process {process_id})."
    ]


@pytest.mark.asyncio
async def test_a_session_rematch_is_not_logged_as_a_token_one(
    editor_client, rematches, alpha_item
):
    with captured_logs("INFO") as records:
        resp = await editor_client.post(_url(alpha_item))

    assert resp.status_code == 202, resp.text
    assert not [r for r in records if r["message"].startswith("Rematch of sample")]
