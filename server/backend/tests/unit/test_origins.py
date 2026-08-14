"""Tests: the shared cross-origin policy (``mascope_backend.origins``).

The REST CORS middleware and the Socket.IO handshake check both read this
module. Socket.IO's use is a security control rather than a convenience header -
it refuses a handshake from a disallowed origin, and the auth cookie is
SameSite=lax, so a policy that is accidentally permissive removes the only
server-side cross-site check the realtime channel has.
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
