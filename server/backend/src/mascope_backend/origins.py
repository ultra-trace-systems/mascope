"""
Browser origins this deployment accepts cross-origin requests from.

In production the frontend and the API share one origin, so nothing needs a
cross-origin allowance: the REST API sends no CORS header, and Socket.IO accepts
only the deployment's own origin. Development splits them - Vite serves the app
on ``5173 + slot`` while the API listens on ``8090 + slot`` - so the dev-server
origins have to be named explicitly.

Three surfaces read this module: the REST CORS middleware, the Socket.IO
handshake predicate (:mod:`mascope_backend.socket.server`), and the REST
write-origin guard (the ``origin_guard`` middleware in
:mod:`mascope_backend.app.fast`, which refuses a state-changing request that
declares a foreign origin). They are separate mechanisms (Starlette's
CORSMiddleware answers preflights; Socket.IO refuses a handshake outright; the
guard answers 403), and holding one policy in one place is what keeps them
from drifting apart into a gap that only shows up as a cross-site request
nobody rejected.

Kept import-light on purpose, like :mod:`mascope_backend.roles`: the socket
server imports it during construction, before the API package is importable.
"""

import os
import re
from urllib.parse import urlsplit


#: Vite's default dev-server port, used when no instance port is exported.
_DEFAULT_FRONTEND_PORT = "5173"

#: Ports a browser elides when serializing an origin. Folded away during
#: normalization so an authority spelling the default port out (a Referer with
#: ``:443``, a proxy synthesizing ``host:port``) still compares equal to the
#: browser's ``Origin``.
_DEFAULT_PORTS = {"http": ":80", "https": ":443"}

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


def _normalize_origin(origin: str) -> str:
    """Lowercase an origin and fold away an explicitly-spelled default port."""
    normalized = origin.lower()
    default = _DEFAULT_PORTS.get(normalized.partition("://")[0])
    if default and normalized.endswith(default):
        normalized = normalized[: -len(default)]
    return normalized


def _first_value(header: str | None) -> str | None:
    """
    The first element of a possibly comma-joined header value.

    Proxy chains that append rather than overwrite deliver values like
    ``outer.example.com, inner``; the first element is the outermost hop's,
    the one describing what the browser saw.

    :param header: The raw header value, or ``None`` when absent.
    :return: The first element stripped, or ``None`` when absent or empty.
    """
    if not header:
        return None
    return header.split(",")[0].strip() or None


def origin_of(url: str | None) -> str | None:
    """
    The normalized ``scheme://host[:port]`` origin of an absolute URL.

    Used to fall back to ``Referer`` when a request carries no ``Origin``:
    the two headers name the same page, they just differ in when browsers
    send them.

    :param url: A URL, e.g. a ``Referer`` header value.
    :return: The origin, lowercased with a default port folded, or ``None``
        when the value has no absolute origin (missing, relative, or
        unparseable).
    """
    if not url:
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if not parts.scheme or not parts.netloc:
        return None
    return _normalize_origin(f"{parts.scheme}://{parts.netloc}")


def own_request_origins(
    scheme: str,
    host: str | None,
    forwarded_proto: str | None = None,
    forwarded_host: str | None = None,
) -> set[str]:
    """
    The origins that are "this deployment" for one request.

    Reconstructed per request from the proxy headers, so a deployment on any
    hostname works with no configuration. Behind a proxy the browser-visible
    origin is carried by ``X-Forwarded-Proto`` + ``X-Forwarded-Host``; either
    alone is honoured, the missing half falling back to the request's own
    scheme or ``Host`` (the same reconstruction Engine.IO applies, consumed
    from here by the socket handshake predicate). Comma-joined values from an
    appending proxy chain are read from their first, outermost element, and
    origins are normalized (lowercased, default port folded).

    When a forwarded header is present the request came through a proxy, and
    only the reconstructed browser-visible origin is trusted. The unforwarded
    ``Host`` is then the internal upstream name - *dialled* only from inside
    the Docker network, but a hostile intranet document on a host of the same
    name could *claim* it in an ``Origin``, so it stays out of the set. A
    request with no forwarded headers is a direct dial (dev, tests, service
    calls) and compares against what it dialled.

    The shipped nginx overwrites ``X-Forwarded-Host`` outright; it normalizes
    ``X-Forwarded-Proto`` to the two real schemes but honours a client-sent
    literal (for TLS terminated in front of it), so the proto half is
    client-influenceable between ``http`` and ``https`` - the host half is
    not, and inventing an origin still requires controlling the host.

    :param scheme: The transport scheme the request arrived on.
    :param host: The request's ``Host`` (authority), or ``None`` if absent.
    :param forwarded_proto: ``X-Forwarded-Proto``, if the proxy set it.
    :param forwarded_host: ``X-Forwarded-Host``, if the proxy set it.
    :return: Normalized origins; membership is the "is this us?" test.
    """
    forwarded_proto = _first_value(forwarded_proto)
    forwarded_host = _first_value(forwarded_host)
    if forwarded_proto or forwarded_host:
        authority = forwarded_host or host
        if not authority:
            return set()
        return {_normalize_origin(f"{forwarded_proto or scheme}://{authority}")}
    if not host:
        return set()
    return {_normalize_origin(f"{scheme}://{host}")}


def is_trusted_write_origin(origin: str, own_origins: set[str], *, dev: bool) -> bool:
    """
    Whether a request declaring ``origin`` may cross the site boundary.

    Decides both the REST write guard and the production socket handshake.
    The deployment's own origin is always trusted; the named dev-server
    origins only in development, where the frontend is served from another
    port. Everything else is foreign - including ``null``, which browsers
    send from sandboxed documents, and the empty string, a declaration like
    any other.

    :param origin: The origin the request declares (``Origin`` header, or the
        origin of ``Referer`` when absent). Not ``None``: a request declaring
        no origin at all is the caller's case to decide, not a mismatch.
    :param own_origins: This request's own origins, from
        :func:`own_request_origins`.
    :param dev: Whether the deployment runs in development mode.
    :return: True if the request may proceed.
    """
    normalized = _normalize_origin(origin)
    if normalized in own_origins:
        return True
    return dev and is_allowed_dev_origin(normalized)
