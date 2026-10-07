"""Unit tests for the File Agent's token-renewal loop.

Hermetic: the renewal HTTP call and the shutdown wait are stubbed, so the loop
runs synchronously and deterministically with no network or threads.
"""

import builtins
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from mascope_file_agent import agent, config, credentials
from mascope_file_agent.credentials import Credentials
from mascope_sdk.exceptions import AuthenticationError, TusNotSupportedError


def _silent_logger():
    return SimpleNamespace(
        info=lambda *a, **k: None,
        warning=lambda *a, **k: None,
        exception=lambda *a, **k: None,
    )


class FakeStop:
    """A stop event whose wait() returns False `false_count` times, then True."""

    def __init__(self, false_count):
        self.delays = []
        self._false_count = false_count

    def wait(self, delay):
        self.delays.append(delay)
        return len(self.delays) > self._false_count


@pytest.fixture
def held():
    """A credential with no token yet, and nowhere to save one."""
    return Credentials("http://testserver", None, _silent_logger())


def test_renewal_loop_rotates_token_and_reschedules(monkeypatch, held):
    held.set_access_token("t1")
    used = []

    def fake_renew(url, token):
        used.append(token)
        return "t2", 2592000  # 30 days

    monkeypatch.setattr(credentials, "api_renew_agent_token", fake_renew)
    stop = FakeStop(false_count=1)  # one renewal, then stop

    held.renewal_loop(stop)

    # Renewed using the live token, and the fresh token is now in use.
    assert used == ["t1"]
    assert held.current_access_token() == "t2"
    # Renewed after the initial delay, then rescheduled at half the lifetime.
    assert stop.delays == [credentials.RENEW_INITIAL_DELAY, 2592000 // 2]


def test_renewal_loop_backs_off_when_endpoint_absent(monkeypatch, held):
    held.set_access_token("t1")

    def fake_renew(url, token):
        raise TusNotSupportedError("no endpoint", status_code=404, url="x")

    monkeypatch.setattr(credentials, "api_renew_agent_token", fake_renew)
    stop = FakeStop(false_count=2)

    held.renewal_loop(stop)

    # Token unchanged, and the loop keeps waiting on the long interval rather
    # than ending: a 404 from a proxy mid-restart must not retire renewal for
    # the life of the process, which would let the token lapse silently.
    assert held.current_access_token() == "t1"
    assert stop.delays == [
        credentials.RENEW_INITIAL_DELAY,
        credentials.RENEW_FALLBACK_INTERVAL,
        credentials.RENEW_FALLBACK_INTERVAL,
    ]


def test_renewal_loop_backs_off_when_token_not_renewable(monkeypatch, held):
    held.set_access_token("t1")

    def fake_renew(url, token):
        raise AuthenticationError("expired", status_code=401, url="x")

    monkeypatch.setattr(credentials, "api_renew_agent_token", fake_renew)
    stop = FakeStop(false_count=2)

    held.renewal_loop(stop)

    assert held.current_access_token() == "t1"
    assert stop.delays == [
        credentials.RENEW_INITIAL_DELAY,
        credentials.RENEW_FALLBACK_INTERVAL,
        credentials.RENEW_FALLBACK_INTERVAL,
    ]


def test_renewal_loop_survives_a_persist_failure(monkeypatch, held):
    """A failure after the rotation must not kill the daemon thread."""
    held.set_access_token("t1")
    monkeypatch.setattr(
        credentials, "api_renew_agent_token", lambda url, token: ("t2", 100)
    )

    def boom(token):
        raise KeyError("source")

    monkeypatch.setattr(held, "persist", boom)
    stop = FakeStop(false_count=1)

    held.renewal_loop(stop)

    # The rotation still took effect in memory, and the loop kept running
    # instead of dying with an unread traceback.
    assert held.current_access_token() == "t2"
    assert stop.delays == [
        credentials.RENEW_INITIAL_DELAY,
        credentials.RENEW_RETRY_DELAY,
    ]


def test_renewal_loop_retries_after_a_transient_failure(monkeypatch, held):
    held.set_access_token("t1")
    outcomes = iter([RuntimeError("network blip"), ("t2", 100)])

    def fake_renew(url, token):
        result = next(outcomes)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(credentials, "api_renew_agent_token", fake_renew)
    stop = FakeStop(false_count=2)  # transient failure, then a success

    held.renewal_loop(stop)

    assert held.current_access_token() == "t2"
    # Initial delay, then the retry delay after the blip, then the reschedule.
    assert stop.delays == [
        credentials.RENEW_INITIAL_DELAY,
        credentials.RENEW_RETRY_DELAY,
        credentials.RENEW_MIN_INTERVAL,  # max(min, 100//2) == min
    ]


def test_persist_token_writes_it_back_to_config(tmp_path, make_settings):
    cfg = tmp_path / config.CONFIG_FILENAME
    settings = make_settings(access_token="old-token")
    held = Credentials(
        "http://testserver",
        "old-token",
        _silent_logger(),
        persist=settings.token_writer(str(cfg)),
    )

    held.persist("renewed-token")

    reloaded = config.load_user_config(str(cfg))
    assert reloaded["access_token"] == "renewed-token"
    # verify_tls survives the rewrite untouched.
    assert reloaded["verify_tls"] is True


def test_resolve_timezone_prefers_the_configured_zone():
    """An explicit setting wins: OS detection names a zone group on Windows."""
    assert agent.resolve_timezone("Europe/Helsinki") == "Europe/Helsinki"
    assert agent.resolve_timezone("  Europe/Helsinki  ") == "Europe/Helsinki"


def test_resolve_timezone_detects_the_local_zone():
    """With nothing configured, the machine's own zone is reported."""
    detected = agent.resolve_timezone("")

    # Whatever this machine reports must be a real IANA zone the server can
    # load - sending a name the converter cannot resolve is worse than silence.
    assert detected
    assert ZoneInfo(detected).key == detected


def test_resolve_timezone_degrades_to_none_when_undetectable(monkeypatch):
    """Detection failure must not stop uploads; the server falls back."""

    def no_tzlocal(name, *args, **kwargs):
        if name == "tzlocal":
            raise ImportError("no tzlocal in this build")
        return _real_import(name, *args, **kwargs)

    _real_import = builtins.__import__
    monkeypatch.setattr(builtins, "__import__", no_tzlocal)

    assert agent.resolve_timezone("") is None


def test_resolve_timezone_ignores_an_unloadable_configured_zone():
    """A typo must not be reported as authoritative.

    The converter would reject it and silently fall back, leaving the operator
    looking at a setting the agent claimed to accept.
    """
    detected = agent.resolve_timezone("")

    assert agent.resolve_timezone("Europe/Helsinky") == detected
    assert agent.resolve_timezone("UTC+2") == detected
