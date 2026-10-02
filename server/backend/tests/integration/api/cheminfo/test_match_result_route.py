"""
Tests: ``GET /api/cheminfo/mz/match/result/{process_id}``, a match search's result.

A composition match search keeps its result for the user who ran it, and the
search pane fetches it from here when the search's completion notification
names it - the rows no longer ride in the notification, whose every copy went
through Redis pub/sub. These pin who may read a result, and for how long.

Redis is replaced with an in-memory fake through the store's ``_redis`` hook,
with a clock the tests move: what matters is the key each result is kept under
and the expiry it is given, not Redis itself.
"""

import math

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from mascope_backend.api.new.cheminfo import match_results
from mascope_backend.api.new.cheminfo.config import cheminfo_config
from mascope_backend.app.fast import fast


ROUTE = "/api/cheminfo/mz/match/result"

RESULT = {
    "mz": 401.12345,
    "sample_item_id": "sample-1",
    "results": 1,
    "total": 3,
    "data": [
        {
            "target_compound_formula": "C10H16O4",
            "ionization_mechanism_id": "mech-1",
            "cheminfo": {"target_isotope_mz": 401.12345},
            "children": [{"mz": 401.12345, "match_mz_error": None}],
        }
    ],
}


class FakeRedis:
    """The store's Redis, with a clock that the tests move and keys that expire."""

    def __init__(self):
        self.now = 0.0
        self.store: dict[str, tuple[str, float]] = {}

    async def set(self, key, value, ex=None):
        self.store[key] = (value, self.now + ex if ex else math.inf)

    async def get(self, key):
        value, expires = self.store.get(key, (None, math.inf))
        return value if self.now < expires else None


@pytest.fixture
def fake_redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(match_results, "_redis", lambda: fake)
    return fake


@pytest_asyncio.fixture
async def anonymous_client():
    async with AsyncClient(
        transport=ASGITransport(app=fast), base_url="http://test"
    ) as client:
        yield client


@pytest.mark.asyncio
async def test_the_user_who_searched_reads_the_result(
    fake_redis, editor_client, test_users
):
    await match_results.save_match_result(test_users["editor"].id, "p-1", RESULT)

    resp = await editor_client.get(f"{ROUTE}/p-1")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["data"] == RESULT["data"]
    assert (body["results"], body["total"]) == (1, 3)
    assert (body["mz"], body["sample_item_id"]) == (401.12345, "sample-1")


@pytest.mark.asyncio
async def test_another_user_cannot_read_it(fake_redis, guest_client, test_users):
    await match_results.save_match_result(test_users["editor"].id, "p-1", RESULT)

    resp = await guest_client.get(f"{ROUTE}/p-1")

    # The answer an unknown or expired result gets, so the route does not
    # confirm that someone else's search exists.
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_a_result_expires(fake_redis, editor_client, test_users):
    await match_results.save_match_result(test_users["editor"].id, "p-1", RESULT)

    fake_redis.now += cheminfo_config.MATCH_RESULT_TTL_SECONDS - 1
    assert (await editor_client.get(f"{ROUTE}/p-1")).status_code == 200

    fake_redis.now += 1
    resp = await editor_client.get(f"{ROUTE}/p-1")
    assert resp.status_code == 404
    assert "expired" in resp.json()["error"]


@pytest.mark.asyncio
async def test_no_search_no_result(fake_redis, editor_client):
    resp = await editor_client.get(f"{ROUTE}/p-unknown")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_an_anonymous_caller_is_refused(fake_redis, anonymous_client):
    resp = await anonymous_client.get(f"{ROUTE}/p-1")

    assert resp.status_code == 401
