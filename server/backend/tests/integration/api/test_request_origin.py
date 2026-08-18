"""Tests: state-changing requests declaring a foreign origin are refused.

The ``origin_guard`` middleware is the REST surface's server-side cross-site
check (pentest ``CSRF-01``). The auth cookie is ``SameSite=lax``, so a browser
already withholds it on cross-site writes - but that is one cookie attribute
deep and vanishes silently if the cookie ever needs ``SameSite=None``. The
guard answers 403 before routing, whatever the visitor's browser does.

The policy predicates are covered by :mod:`tests.unit.test_origins`; what
these pin is the wiring - that the app actually runs the guard, on the right
methods, with the deployment's own origin reconstructed from the proxy
headers. They drive the real application over HTTP against a path that routes
nowhere: 404 proves the request passed the guard and reached the router,
without needing auth or the database.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from mascope_backend.app.fast import fast


#: A path no router claims: reaching the router's 404 is the "allowed" signal.
PROBE = "/api/origin-guard-probe"

#: What proxy_pass leaves as the Host: the upstream block's name, not the
#: browser's. Driving the production-shaped cases through this makes them
#: depend on X-Forwarded-Host rather than passing by accident.
UPSTREAM_URL = "http://backend"

#: Headers nginx attaches, from which the guard rebuilds the browser-visible
#: origin (nginx overwrites both, so a client cannot choose them).
PROXY_HEADERS = {
    "X-Forwarded-Proto": "https",
    "X-Forwarded-Host": "mascope.example.com",
}

FOREIGN = "https://attacker.example.invalid"


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=fast), base_url=UPSTREAM_URL)


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
async def test_a_write_claiming_a_foreign_origin_is_refused(method):
    """Every state-changing method is covered, on any path, before routing."""
    async with _client() as client:
        resp = await client.request(
            method,
            PROBE,
            headers={**PROXY_HEADERS, "Origin": FOREIGN, "Referer": f"{FOREIGN}/"},
        )

    assert resp.status_code == 403
    assert "not an accepted origin" in resp.json()["error"]


@pytest.mark.asyncio
async def test_a_write_from_the_deployments_own_origin_passes():
    """The app's own pages keep writing, on whatever hostname it is served."""
    async with _client() as client:
        resp = await client.post(
            PROBE, headers={**PROXY_HEADERS, "Origin": "https://mascope.example.com"}
        )

    assert resp.status_code == 404  # reached the router: the guard let it pass


@pytest.mark.asyncio
async def test_a_write_declaring_no_origin_passes():
    """Non-browser clients send neither header and must keep working.

    This is the instrument agents' upload path (bearer token, no browser) and
    the file converter's service calls. An absent declaration is not a
    mismatch; auth still applies as always.
    """
    async with _client() as client:
        resp = await client.post(PROBE, headers=PROXY_HEADERS)

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_the_referer_stands_in_when_origin_is_absent():
    """A browser that omits Origin still names its page in Referer."""
    async with _client() as client:
        refused = await client.post(
            PROBE, headers={**PROXY_HEADERS, "Referer": f"{FOREIGN}/page"}
        )
        passed = await client.post(
            PROBE,
            headers={**PROXY_HEADERS, "Referer": "https://mascope.example.com/page"},
        )

    assert refused.status_code == 403
    assert passed.status_code == 404


@pytest.mark.asyncio
async def test_reads_are_not_origin_checked():
    """Safe methods stay unchecked: they change no state, and the CORS layer
    (no cross-origin allowance in prod) already keeps their responses from
    being readable cross-site. Pinned so the guard does not silently widen."""
    async with _client() as client:
        resp = await client.get(PROBE, headers={**PROXY_HEADERS, "Origin": FOREIGN})

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_a_direct_connection_compares_against_what_it_dialled():
    """No proxy headers (dev, tests, service calls): Host is the origin."""
    async with _client() as client:
        passed = await client.post(PROBE, headers={"Origin": UPSTREAM_URL})
        refused = await client.post(PROBE, headers={"Origin": FOREIGN})

    assert passed.status_code == 404
    assert refused.status_code == 403
