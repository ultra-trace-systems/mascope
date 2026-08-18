"""Tests: state-changing requests declaring a foreign origin are refused.

The ``origin_guard`` middleware is the REST surface's server-side cross-site
check (pentest ``CSRF-01``). The auth cookie is ``SameSite=lax``, so a browser
already withholds it on cross-site writes - but that is one cookie attribute
deep and vanishes silently if the cookie ever needs ``SameSite=None``. The
guard answers 403 before routing, whatever the visitor's browser does.

The policy predicates are covered by :mod:`tests.unit.test_origins`; what
these pin is the wiring - that the app actually runs the guard, on the right
methods, in the right runtime mode, with the deployment's own origin
reconstructed from the proxy headers. They drive the real application over
HTTP against a path that routes nowhere: 404 proves the request passed the
guard and reached the router, without needing auth or the database.
"""

import sys

import pytest
from httpx import ASGITransport, AsyncClient
from proxy_contract import PROD_HOST, PROXY_HEADERS, UPSTREAM_URL

from mascope_backend.app.fast import fast


#: The guard's module. The app package re-exports ``fast`` (the application)
#: under the same name as its module, so a string setattr target would resolve
#: to the FastAPI instance; patch the module object itself.
_GUARD_MODULE = sys.modules["mascope_backend.app.fast"]


#: A path no router claims: reaching the router's 404 is the "allowed" signal.
PROBE = "/api/origin-guard-probe"

FOREIGN = "https://attacker.example.invalid"


def _client(base_url: str = UPSTREAM_URL) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=fast), base_url=base_url)


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
            PROBE, headers={**PROXY_HEADERS, "Origin": f"https://{PROD_HOST}"}
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
async def test_an_empty_origin_is_a_declaration_and_is_refused():
    """Absent fails open for non-browser clients; present-but-empty does not.

    No browser sends an empty ``Origin`` (opaque contexts serialize as the
    literal ``null``), so nothing legitimate is lost by refusing it - and a
    value that was declared should never widen into "no declaration".
    """
    async with _client() as client:
        resp = await client.post(PROBE, headers={**PROXY_HEADERS, "Origin": ""})

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_the_upstream_name_is_not_an_accepted_origin_behind_the_proxy():
    """Behind nginx only the browser-visible origin is "us".

    The unforwarded Host is the internal upstream name; an Origin claiming it
    could come from a hostile intranet document on a host of the same name,
    never from this deployment's own pages, whose origin is the forwarded one.
    """
    async with _client() as client:
        resp = await client.post(
            PROBE, headers={**PROXY_HEADERS, "Origin": UPSTREAM_URL}
        )

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_a_lone_forwarded_proto_pairs_with_the_preserved_host():
    """A front proxy that keeps Host and sends only X-Forwarded-Proto works.

    That is the common minimal TLS-termination shape (Caddy and friends).
    The browser's Origin is https on the preserved host; without the pairing
    every write from such a deployment would be refused.
    """
    async with _client(base_url=f"http://{PROD_HOST}") as client:
        resp = await client.post(
            PROBE,
            headers={
                "X-Forwarded-Proto": "https",
                "Origin": f"https://{PROD_HOST}",
            },
        )

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
            headers={**PROXY_HEADERS, "Referer": f"https://{PROD_HOST}/page"},
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


@pytest.mark.asyncio
async def test_the_vite_origin_is_trusted_only_in_development(monkeypatch):
    """The dev-server allowance must not leak into production.

    The unit tests pin this on the predicate; this pins the wiring - the
    guard's ``dev=`` argument - which would otherwise have no coverage: the
    suite itself runs in dev mode, and a foreign origin is refused in both
    modes, so a wiring mutation would survive every other test here.
    """
    vite = {"Origin": "http://localhost:5173"}

    monkeypatch.setattr(_GUARD_MODULE, "_DEV_MODE", True)
    async with _client() as client:
        allowed = await client.post(PROBE, headers=vite)

    monkeypatch.setattr(_GUARD_MODULE, "_DEV_MODE", False)
    async with _client() as client:
        refused = await client.post(PROBE, headers=vite)

    assert allowed.status_code == 404
    assert refused.status_code == 403
