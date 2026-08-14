"""
Browser origins this deployment accepts cross-origin requests from.

In production the frontend and the API share one origin, so nothing needs a
cross-origin allowance: the REST API sends no CORS header, and Socket.IO accepts
only the deployment's own origin. Development splits them - Vite serves the app
on ``5173 + slot`` while the API listens on ``8090 + slot`` - so the dev-server
origins have to be named explicitly.

Both surfaces read this module. They are separate mechanisms (Starlette's
CORSMiddleware answers preflights; Socket.IO refuses a handshake outright), and
holding one policy in one place is what keeps them from drifting apart into a
gap that only shows up as a cross-site connection nobody rejected.

Kept import-light on purpose, like :mod:`mascope_backend.roles`: the socket
server imports it during construction, before the API package is importable.
"""

import os
import re


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
