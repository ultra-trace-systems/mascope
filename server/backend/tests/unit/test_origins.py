"""Tests: the shared cross-origin policy (``mascope_backend.origins``).

The REST CORS middleware, the Socket.IO handshake check and the REST
write-origin guard all read this module. The latter two are security controls
rather than convenience headers - they refuse a cross-site request outright,
and the auth cookie is SameSite=lax, so a policy that is accidentally
permissive removes the only server-side cross-site check either surface has.
"""

import pytest

from mascope_backend import origins


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Start from a bare environment so a developer's own exports cannot leak in."""
    monkeypatch.delenv("MASCOPE_FRONTEND_PORT", raising=False)
    monkeypatch.delenv("MASCOPE_DEVHOST", raising=False)


def test_default_dev_origin_is_the_vite_port():
    assert origins.dev_origins() == ["http://localhost:5173"]


def test_instance_port_is_honoured_alongside_the_default(monkeypatch):
    """A worktree instance on slot 3 serves 5176 but slot 0 may still be running."""
    monkeypatch.setenv("MASCOPE_FRONTEND_PORT", "5176")

    assert origins.dev_origins() == [
        "http://localhost:5173",
        "http://localhost:5176",
    ]


def test_tailnet_pattern_is_off_unless_devhost_is_set(monkeypatch):
    assert origins.dev_origin_regex() is None

    monkeypatch.setenv("MASCOPE_DEVHOST", "1")

    assert origins.dev_origin_regex() is not None


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost:5173",
    ],
)
def test_dev_server_origins_are_allowed(origin):
    assert origins.is_allowed_dev_origin(origin) is True


@pytest.mark.parametrize(
    "origin",
    [
        "https://evil.example.com",
        "http://localhost:5174",  # a port we do not serve
        "http://localhost.evil.com:5173",  # suffix trick on the host
        "",
        None,
    ],
)
def test_foreign_origins_are_refused(origin):
    assert origins.is_allowed_dev_origin(origin) is False


def test_tailnet_origins_allowed_only_with_devhost(monkeypatch):
    tailnet = "http://devbox.tail6adf84.ts.net:5173"

    assert origins.is_allowed_dev_origin(tailnet) is False

    monkeypatch.setenv("MASCOPE_DEVHOST", "1")

    assert origins.is_allowed_dev_origin(tailnet) is True


def test_tailnet_pattern_is_anchored(monkeypatch):
    """A lookalike host must not pass by embedding the pattern.

    The regex is matched in full rather than searched, so an attacker-controlled
    host that merely contains a tailnet-looking substring is refused.
    """
    monkeypatch.setenv("MASCOPE_DEVHOST", "1")

    assert origins.is_allowed_dev_origin("http://evil.com/x.ts.net") is False
    assert origins.is_allowed_dev_origin("http://a.ts.net.evil.com") is False


# --- The REST write-origin guard's helpers ---


@pytest.mark.parametrize(
    ("url", "origin"),
    [
        ("https://mascope.example.com/app/page?x=1", "https://mascope.example.com"),
        ("http://localhost:8080/", "http://localhost:8080"),
        ("HTTPS://Mascope.Example.COM/Page", "https://mascope.example.com"),
        # An explicitly-spelled default port folds to the browser serialization
        ("https://mascope.example.com:443/page", "https://mascope.example.com"),
    ],
)
def test_origin_of_extracts_the_origin(url, origin):
    assert origins.origin_of(url) == origin


@pytest.mark.parametrize(
    "url",
    [
        None,
        "",
        "/relative/path",  # a Referer stripped to a path carries no origin
        "mascope.example.com/page",  # no scheme
        "http://[bad",  # unparseable authority
    ],
)
def test_origin_of_yields_none_without_an_absolute_origin(url):
    assert origins.origin_of(url) is None


def test_own_origins_come_from_the_forwarded_headers_behind_the_proxy():
    """Behind nginx the browser-visible origin is the forwarded pair - alone.

    The unforwarded Host is what proxy_pass leaves: the upstream block's name.
    Only the upstream is *dialled* by that name, but any intranet document on
    a host of the same name could *claim* it in an Origin, so it must not be
    trusted; the forwarded origin is the one a real browser's Origin matches.
    """
    assert origins.own_request_origins(
        "http", "backend", "https", "mascope.example.com"
    ) == {"https://mascope.example.com"}


def test_own_origins_fall_back_to_the_request_itself():
    """A direct connection (dev, tests, service calls) has no proxy headers."""
    assert origins.own_request_origins("http", "localhost:8090") == {
        "http://localhost:8090"
    }
    assert origins.own_request_origins("http", "demo.example.com:8080", None, None) == {
        "http://demo.example.com:8080"
    }


def test_own_origins_keep_the_scheme_when_only_the_host_is_forwarded():
    assert origins.own_request_origins(
        "https", "backend", None, "mascope.example.com"
    ) == {"https://mascope.example.com"}


def test_own_origins_pair_a_lone_forwarded_proto_with_the_host():
    """A proxy that preserves Host and forwards only the proto still works.

    The common minimal TLS-termination shape: without the pairing, every
    browser write from such a deployment would be refused while the socket
    handshake accepted it.
    """
    assert origins.own_request_origins(
        "http", "mascope.example.com", "https", None
    ) == {"https://mascope.example.com"}


def test_own_origins_read_comma_joined_headers_from_the_outermost_hop():
    """Chained proxies that append rather than overwrite stay matchable."""
    assert origins.own_request_origins(
        "http", "backend", "https, http", "mascope.example.com, internal.example"
    ) == {"https://mascope.example.com"}


def test_own_origins_fold_an_explicit_default_port():
    """A proxy synthesizing host:port must still match the browser's Origin,
    which never spells a default port out."""
    assert origins.own_request_origins(
        "http", "backend", "https", "mascope.example.com:443"
    ) == {"https://mascope.example.com"}
    assert origins.own_request_origins("http", "localhost:80") == {"http://localhost"}


OWN = {"https://mascope.example.com"}


@pytest.mark.parametrize(
    "origin",
    [
        "https://mascope.example.com",
        "HTTPS://MASCOPE.EXAMPLE.COM",  # scheme and host compare case-insensitively
        "https://mascope.example.com:443",  # explicit default port folds away
    ],
)
def test_a_write_from_the_deployments_own_origin_is_trusted(origin):
    assert origins.is_trusted_write_origin(origin, OWN, dev=False) is True


@pytest.mark.parametrize(
    "origin",
    [
        "https://attacker.example.invalid",
        "https://mascope.example.com.evil.com",  # suffix trick on the host
        "https://mascope.example.com:8443",  # same host, another port
        "http://backend",  # the internal upstream name is not "us" to a browser
        "null",  # sandboxed documents declare the opaque origin
        "",  # declared-but-empty is a declaration, not an absence
    ],
)
def test_a_write_from_a_foreign_origin_is_refused(origin):
    assert origins.is_trusted_write_origin(origin, OWN, dev=False) is False


def test_the_vite_origin_is_trusted_only_in_development():
    """Production must not inherit the dev-server allowance."""
    vite = "http://localhost:5173"

    assert origins.is_trusted_write_origin(vite, OWN, dev=True) is True
    assert origins.is_trusted_write_origin(vite, OWN, dev=False) is False
