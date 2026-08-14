"""Tests: the Socket.IO handshake refuses cross-site origins.

Engine.IO's origin check is the realtime channel's only server-side cross-site
defence. The auth cookie is ``SameSite=lax``, so if the check is disabled a
hostile page can open a *credentialed* WebSocket to this deployment and the
browser's default is all that stops it.

These drive real handshakes through a server built with the production policy
(:func:`_allowed_origins`), because the risk is not in our predicate - which
:mod:`tests.unit.test_origins` covers - but in Engine.IO's semantics around it:
``None`` means "same origin only", the same-origin value is reconstructed per
request from the proxy headers, and an empty list would silently mean "do not
check at all".
"""

from types import SimpleNamespace

import pytest
import socketio
from httpx import ASGITransport, AsyncClient

from mascope_backend.socket.server import _allowed_origins


PROD_HOST = "mascope.example.com"
HANDSHAKE = "/socket.io/?EIO=4&transport=polling"

#: What proxy_pass leaves as the Host: the upstream block's name, not the
#: browser's. Driving the production cases through this is what makes them
#: depend on X-Forwarded-Host rather than passing by accident.
UPSTREAM_URL = "http://backend"
DEV_API_URL = "http://localhost:8090"

#: Headers nginx attaches, from which Engine.IO rebuilds the browser-visible
#: origin. Without X-Forwarded-Host it would compare against the upstream name.
PROXY_HEADERS = {
    "X-Forwarded-Proto": "https",
    "X-Forwarded-Host": PROD_HOST,
}


def _server(mode: str, monkeypatch) -> socketio.AsyncServer:
    """A Socket.IO server carrying the origin policy for ``mode``.

    ``runtime.mode`` is a read-only property, so stand in a stub for the module's
    reference to the runtime; the policy reads nothing else from it.
    """
    monkeypatch.setattr(
        "mascope_backend.socket.server.runtime", SimpleNamespace(mode=mode)
    )
    return socketio.AsyncServer(
        async_mode="asgi", cors_allowed_origins=_allowed_origins()
    )


async def _handshake(server: socketio.AsyncServer, headers: dict, base_url: str):
    """Attempt one Engine.IO handshake and return the response.

    ``base_url`` sets the Host the server sees. The production cases pass the
    upstream name, because that is what ``proxy_pass`` actually puts there - so
    those tests pass only when the forwarded headers are doing the work.
    """
    app = socketio.ASGIApp(socketio_server=server)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=base_url
    ) as client:
        return await client.get(HANDSHAKE, headers=headers)


@pytest.mark.asyncio
async def test_production_refuses_a_foreign_origin(monkeypatch):
    """A hostile page cannot open a connection to a production deployment."""
    server = _server("prod", monkeypatch)

    resp = await _handshake(
        server,
        {**PROXY_HEADERS, "Origin": "https://evil.example.com"},
        base_url=UPSTREAM_URL,
    )

    assert resp.status_code == 400
    assert "not an accepted origin" in resp.text


@pytest.mark.asyncio
async def test_production_accepts_its_own_origin(monkeypatch):
    """The app's own pages still connect, on whatever hostname it is served."""
    server = _server("prod", monkeypatch)

    resp = await _handshake(
        server,
        {**PROXY_HEADERS, "Origin": f"https://{PROD_HOST}"},
        base_url=UPSTREAM_URL,
    )

    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_a_client_sending_no_origin_still_connects(monkeypatch):
    """Engine.IO checks only when an ``Origin`` is present.

    This is what keeps the file converter working, and it is not automatic:
    websocket-client synthesises an ``Origin`` from the URL it dials unless
    ``suppress_origin`` is set, so the converter has to ask for it explicitly
    (see :class:`FileConverterSocketClient` and the test below).
    """
    server = _server("prod", monkeypatch)

    resp = await _handshake(
        server, {"X-Service-Name": "file-converter"}, base_url=UPSTREAM_URL
    )

    assert resp.status_code == 200


def test_the_app_server_refuses_a_foreign_origin():
    """Pin the object the app actually runs, not just the policy function.

    Every handshake test above builds its own server, so all of them would still
    pass if ``create_socket_server`` stopped passing the policy through - the one
    edit that would silently restore the wide-open behaviour. Exercise the policy
    the real server holds rather than comparing it to the two known-bad values,
    so a third way of being wrong is caught too.
    """
    from mascope_backend.socket.server import sio

    policy = sio.eio.cors_allowed_origins

    if callable(policy):  # development: the predicate must reject outright
        assert policy("https://evil.example.com", {}) is False
    else:  # production: None is same-origin-only; a list or "*" is not
        assert policy is None


def test_file_converter_suppresses_its_synthesised_origin():
    """The converter must not send a browser-style ``Origin``.

    Without ``suppress_origin`` websocket-client sends ``Origin: <scheme>://
    <host:port>`` built from the connect URL. In development that is the API's
    own origin, which the dev policy does not list, so the converter would be
    refused and file conversion would stop. Pinning the option here because the
    failure is silent and only reproduces on a websocket transport.
    """
    from mascope_backend.file_converter.socket.client import FileConverterSocketClient

    client = FileConverterSocketClient("http://localhost:8090")

    assert client.sio.eio.websocket_extra_options.get("suppress_origin") is True


@pytest.mark.asyncio
async def test_dev_accepts_the_vite_origin_and_refuses_others(monkeypatch):
    """Dev splits frontend and API across ports, so it names the dev origins."""
    monkeypatch.delenv("MASCOPE_FRONTEND_PORT", raising=False)
    monkeypatch.delenv("MASCOPE_DEVHOST", raising=False)
    server = _server("dev", monkeypatch)

    allowed = await _handshake(
        server, {"Origin": "http://localhost:5173"}, base_url=DEV_API_URL
    )
    refused = await _handshake(
        server, {"Origin": "https://evil.example.com"}, base_url=DEV_API_URL
    )

    assert allowed.status_code == 200
    assert refused.status_code == 400
