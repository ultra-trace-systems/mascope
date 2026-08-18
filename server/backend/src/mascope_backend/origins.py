"""
Browser origins this deployment accepts cross-origin requests from.

In production the frontend and the API share one origin, so nothing needs a
cross-origin allowance: the REST API sends no CORS header, and Socket.IO accepts
only the deployment's own origin. Development splits them - Vite serves the app
on ``5173 + slot`` while the API listens on ``8090 + slot`` - so the dev-server
origins have to be named explicitly.

Three surfaces read this module: the REST CORS middleware, the Socket.IO
handshake check, and the REST write-origin guard (the ``origin_guard``
middleware in :mod:`mascope_backend.app.fast`, which refuses a state-changing
request that declares a foreign origin). They are separate mechanisms
(Starlette's CORSMiddleware answers preflights; Socket.IO refuses a handshake
outright; the guard answers 403), and holding one policy in one place is what
keeps them from drifting apart into a gap that only shows up as a cross-site
request nobody rejected.

Kept import-light on purpose, like :mod:`mascope_backend.roles`: the socket
server imports it during construction, before the API package is importable.
"""

import os
import re
from urllib.parse import urlsplit


#: Vite's default dev-server port, used when no instance port is exported.
_DEFAULT_FRONTEND_PORT = "5173"

#: Tailnet hosts, for `mascope dev run --host`: the dev servers bind 0.0.0.0 and
#: the app is browsed by machine name, so page origins arrive as *.ts.net rather
#: than localhost. Mirrors `allowedHosts` in vite.config.js. Only consulted when
#: MASCOPE_DEVHOST is set, so localhost-only development keeps the strict list.
_DEVHOST_ORIGIN_PATTERN = r"http://[^/]+\.ts\.net(:\d+)?"


def dev_origins() -> list[str]:
    """
    Dev-server origins the API accepts.

    Per-worktree instances run Vite on ``5173 + slot`` and export
    ``MASCOPE_FRONTEND_PORT``; honour it so instances past slot 0 are reachable
    from their own frontend, while keeping the default port for slot 0.

    :return: Origins, deduplicated, in a stable order.
    """
    port = os.environ.get("MASCOPE_FRONTEND_PORT", _DEFAULT_FRONTEND_PORT)
    return sorted(
        {f"http://localhost:{port}", f"http://localhost:{_DEFAULT_FRONTEND_PORT}"}
    )


def dev_origin_regex() -> str | None:
    """
    Extra origin pattern for a dev stack exposed beyond localhost.

    :return: The tailnet pattern when ``MASCOPE_DEVHOST`` is set, else ``None``.
    """
    return _DEVHOST_ORIGIN_PATTERN if os.environ.get("MASCOPE_DEVHOST") else None


def is_allowed_dev_origin(origin: str | None) -> bool:
    """
    Whether ``origin`` is one this dev deployment serves its frontend from.

    The exact-list and regex halves are the same two the REST CORS middleware is
    configured with, evaluated here rather than declared, because Socket.IO takes
    a predicate instead of a list plus a pattern.

    :param origin: Value of the request's ``Origin`` header. ``None`` when the
        request carries none, which Engine.IO passes through while assembling
        CORS response headers; a request without an origin is not one this
        function can vouch for, so it is refused rather than treated as absent.
    :return: True if the origin may open a cross-origin connection.
    """
    if not origin:
        return False
    if origin in dev_origins():
        return True
    pattern = dev_origin_regex()
    return bool(pattern and re.fullmatch(pattern, origin))


def origin_of(url: str | None) -> str | None:
    """
    The ``scheme://host[:port]`` origin of an absolute URL, lowercased.

    Used to fall back to ``Referer`` when a request carries no ``Origin``:
    the two headers name the same page, they just differ in when browsers
    send them.

    :param url: A URL, e.g. a ``Referer`` header value.
    :return: The origin, or ``None`` when the value has no absolute origin
        (missing, relative, or unparseable).
    """
    if not url:
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if not parts.scheme or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}".lower()


def own_request_origins(
    scheme: str,
    host: str | None,
    forwarded_proto: str | None = None,
    forwarded_host: str | None = None,
) -> set[str]:
    """
    The origins that are "this deployment" for one request.

    Reconstructed per request from the proxy headers, exactly as Engine.IO
    does for the socket handshake, so a deployment on any hostname works with
    no configuration. Behind nginx the browser-visible origin is
    ``X-Forwarded-Proto`` + ``X-Forwarded-Host`` (nginx overwrites both, so a
    client cannot choose them); the unforwarded ``Host`` is the internal
    upstream name, reachable only from inside the Docker network, and is
    included so direct dev/service connections compare against what they
    actually dialled.

    :param scheme: The transport scheme the request arrived on.
    :param host: The request's ``Host`` (authority), or ``None`` if absent.
    :param forwarded_proto: ``X-Forwarded-Proto``, if the proxy set it.
    :param forwarded_host: ``X-Forwarded-Host``, if the proxy set it.
    :return: Lowercased origins; membership is the "is this us?" test.
    """
    origins = set()
    if host:
        origins.add(f"{scheme}://{host}".lower())
    if forwarded_host:
        origins.add(f"{forwarded_proto or scheme}://{forwarded_host}".lower())
    return origins


def is_trusted_write_origin(origin: str, own_origins: set[str], *, dev: bool) -> bool:
    """
    Whether a state-changing request declaring ``origin`` may proceed.

    The deployment's own origin is always trusted; the named dev-server
    origins only in development, where the frontend is served from another
    port. Everything else - including ``null``, which browsers send from
    sandboxed documents - is foreign.

    :param origin: The origin the request declares (``Origin`` header, or the
        origin of ``Referer`` when absent). Not ``None``: a request declaring
        no origin at all is the caller's case to decide, not a mismatch.
    :param own_origins: This request's own origins, from
        :func:`own_request_origins`.
    :param dev: Whether the deployment runs in development mode.
    :return: True if the request may proceed.
    """
    normalized = origin.lower()
    if normalized in own_origins:
        return True
    return dev and is_allowed_dev_origin(normalized)
