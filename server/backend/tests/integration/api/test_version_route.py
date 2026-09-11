"""Tests: the deployment version route ``GET /api/version``.

The route exists so an operator or an audit can attribute a running deployment
to an artifact, and so the web app's About dialog can show the server's build
next to its own. Two properties are load-bearing: it must report the version the
process was actually configured with - which means reading ``MASCOPE_VERSION``,
not some other source - and it must answer every signed-in user while still
refusing anonymous callers.

Authentication is there to keep the API from growing an anonymous,
machine-readable surface rather than as a secrecy boundary (the frontend renders
the same version on the login screen), so these tests assert the status codes
and that an anonymous body carries no version, not that the version is
unobtainable system-wide.

``GET /api/version/third-party-notices`` serves the attributions the backend
image build generates for the Python packages it ships.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from mascope_backend.api.new.version import routes as version_routes
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
async def test_guest_reads_the_version(guest_client, monkeypatch):
    """The lowest role gets it too: the About dialog is open to every user, and
    a support request has to be able to name the build whoever files it."""
    monkeypatch.setattr(runtime, "_version", VERSION_SENTINEL, raising=False)

    resp = await guest_client.get("/api/version")

    assert resp.status_code == 200
    assert resp.json()["data"]["version"] == VERSION_SENTINEL


@pytest.mark.asyncio
async def test_editor_reads_the_version(editor_client, monkeypatch):
    """No role between guest and admin is left out."""
    monkeypatch.setattr(runtime, "_version", VERSION_SENTINEL, raising=False)

    resp = await editor_client.get("/api/version")

    assert resp.status_code == 200
    assert resp.json()["data"]["version"] == VERSION_SENTINEL


@pytest.mark.asyncio
async def test_signed_in_user_reads_the_third_party_notices(
    guest_client, monkeypatch, tmp_path
):
    """The attributions come back verbatim, declared as UTF-8.

    Licence files are full of names that are not ASCII, so the charset is part
    of what is served, not a detail: without it a browser may guess and mangle
    the very names the file exists to credit.
    """
    notices = tmp_path / "THIRD_PARTY_NOTICES.txt"
    notices.write_text(
        "numpy 2.0.0\nLicense: BSD-3-Clause\n\nCopyright (c) Zoë Example\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(version_routes, "third_party_notices_path", lambda: notices)

    resp = await guest_client.get("/api/version/third-party-notices")

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "text/plain; charset=utf-8"
    assert resp.text == notices.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_missing_notices_are_a_404_not_an_empty_document(
    guest_client, monkeypatch, tmp_path
):
    """A source checkout has none unless someone generated them. An empty 200
    would read as "this server ships no third-party code", which is never true."""
    monkeypatch.setattr(
        version_routes, "third_party_notices_path", lambda: tmp_path / "absent.txt"
    )

    resp = await guest_client.get("/api/version/third-party-notices")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_anonymous_callers_get_no_notices(monkeypatch, tmp_path):
    """Behind the same authentication as the version itself."""
    notices = tmp_path / "THIRD_PARTY_NOTICES.txt"
    notices.write_text("numpy 2.0.0\n", encoding="utf-8")
    monkeypatch.setattr(version_routes, "third_party_notices_path", lambda: notices)

    async with AsyncClient(
        transport=ASGITransport(app=fast), base_url="http://test"
    ) as client:
        resp = await client.get("/api/version/third-party-notices")

    assert resp.status_code == 401
    assert "numpy" not in resp.text
