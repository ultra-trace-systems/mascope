"""Tests: the deployment version route ``GET /api/version``.

The route exists so an operator or an audit can attribute a running deployment
to an artifact. Two properties are load-bearing: it must report the version the
process was actually configured with - which means reading ``MASCOPE_VERSION``,
not some other source - and it must stay behind the admin gate.

The gate is least privilege for an operational endpoint rather than a secrecy
boundary (the frontend renders the same version on the login screen), so these
tests assert the status codes and that the body carries no version, not that the
version is unobtainable system-wide.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from mascope_backend.app.fast import fast
from mascope_backend.runtime import runtime


VERSION_SENTINEL = "v9.9.9-test"


@pytest.mark.asyncio
async def test_admin_reads_the_running_version(admin_client, monkeypatch):
    """An admin gets the version the runtime resolved at startup."""
    monkeypatch.setattr(runtime, "_version", VERSION_SENTINEL, raising=False)

    resp = await admin_client.get("/api/version")

    assert resp.status_code == 200
    assert resp.json()["data"]["version"] == VERSION_SENTINEL


@pytest.mark.asyncio
async def test_version_comes_from_the_deployment_environment(admin_client, monkeypatch):
    """The reported version originates in ``MASCOPE_VERSION``.

    The other tests set ``runtime._version`` directly, which is convenient but
    skips the only mechanism that matters in production: compose passes
    ``MASCOPE_VERSION`` into the container and the runtime reads it at startup.
    Drive that path end to end - set the variable, re-resolve, and read the
    route - so a change to where the version comes from fails here rather than
    shipping a deployment that reports the wrong thing.
    """
    # Register the attribute with monkeypatch first: it snapshots the current
    # value and restores it at teardown, so re-resolving below cannot leak the
    # test's version into the rest of the session.
    monkeypatch.setattr(runtime, "_version", None, raising=False)
    monkeypatch.setenv("MASCOPE_VERSION", "v1.2.3-from-env")
    runtime._init_version()

    resp = await admin_client.get("/api/version")

    assert resp.status_code == 200
    assert resp.json()["data"]["version"] == "v1.2.3-from-env"


@pytest.mark.asyncio
async def test_unset_version_reports_unknown(admin_client, monkeypatch):
    """Outside compose the variable is unset; say so rather than inventing one.

    Reporting an empty string or omitting the field would let a caller record
    "no version" as if it were a version.
    """
    monkeypatch.setattr(runtime, "_version", None, raising=False)

    resp = await admin_client.get("/api/version")

    assert resp.status_code == 200
    assert resp.json()["data"]["version"] == "unknown"


@pytest.mark.asyncio
async def test_anonymous_callers_are_challenged(monkeypatch):
    """An unauthenticated caller is challenged and gets no version in the body."""
    # Install the sentinel so the body assertion can actually fail. Without it
    # the version is whatever the environment happens to hold, and asserting on
    # a value that is never present tests nothing.
    monkeypatch.setattr(runtime, "_version", VERSION_SENTINEL, raising=False)

    async with AsyncClient(
        transport=ASGITransport(app=fast), base_url="http://test"
    ) as client:
        resp = await client.get("/api/version")

    assert resp.status_code == 401
    assert VERSION_SENTINEL not in resp.text


@pytest.mark.asyncio
async def test_guest_is_refused(guest_client, monkeypatch):
    """A guest is authenticated but not entitled to the version."""
    monkeypatch.setattr(runtime, "_version", VERSION_SENTINEL, raising=False)

    resp = await guest_client.get("/api/version")

    assert resp.status_code == 403
    assert VERSION_SENTINEL not in resp.text


@pytest.mark.asyncio
async def test_editor_is_refused(editor_client, monkeypatch):
    """An editor may write data but is still below the admin gate."""
    monkeypatch.setattr(runtime, "_version", VERSION_SENTINEL, raising=False)

    resp = await editor_client.get("/api/version")

    assert resp.status_code == 403
    assert VERSION_SENTINEL not in resp.text
