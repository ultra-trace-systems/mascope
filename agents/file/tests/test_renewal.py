"""Unit tests for the File Agent's token-renewal loop.

Hermetic: the renewal HTTP call and the shutdown wait are stubbed, so the loop
runs synchronously and deterministically with no network or threads.
"""

from types import SimpleNamespace

import pytest

from mascope_file_agent import config, main
from mascope_sdk.exceptions import AuthenticationError, TusNotSupportedError


def _silent_logger():
    return SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None)


class FakeStop:
    """A stop event whose wait() returns False `false_count` times, then True."""

    def __init__(self, false_count):
        self.delays = []
        self._false_count = false_count

    def wait(self, delay):
        self.delays.append(delay)
        return len(self.delays) > self._false_count


@pytest.fixture(autouse=True)
def _isolate_module_state(monkeypatch):
    """Reset the module globals the loop reads, restored after each test."""
    monkeypatch.setattr(main, "URL", "http://testserver")
    monkeypatch.setattr(main, "runtime", SimpleNamespace(logger=_silent_logger()))
    monkeypatch.setattr(main, "_config_path", None)
    monkeypatch.setattr(main, "_settings", None)
    monkeypatch.setattr(main, "_access_token", None)


def test_renewal_loop_rotates_token_and_reschedules(monkeypatch):
    main._set_access_token("t1")
    used = []

    def fake_renew(url, token):
        used.append(token)
        return "t2", 2592000  # 30 days

    monkeypatch.setattr(main, "api_renew_agent_token", fake_renew)
    stop = FakeStop(false_count=1)  # one renewal, then stop

    main._renewal_loop(stop)

    # Renewed using the live token, and the fresh token is now in use.
    assert used == ["t1"]
    assert main.current_access_token() == "t2"
    # Renewed after the initial delay, then rescheduled at half the lifetime.
    assert stop.delays == [main.RENEW_INITIAL_DELAY, 2592000 // 2]


def test_renewal_loop_stops_quietly_when_endpoint_absent(monkeypatch):
    main._set_access_token("t1")

    def fake_renew(url, token):
        raise TusNotSupportedError("no endpoint", status_code=404, url="x")

    monkeypatch.setattr(main, "api_renew_agent_token", fake_renew)
    stop = FakeStop(false_count=5)

    main._renewal_loop(stop)

    # Token unchanged, and the loop returned after a single attempt.
    assert main.current_access_token() == "t1"
    assert stop.delays == [main.RENEW_INITIAL_DELAY]


def test_renewal_loop_stops_when_token_not_renewable(monkeypatch):
    main._set_access_token("t1")

    def fake_renew(url, token):
        raise AuthenticationError("expired", status_code=401, url="x")

    monkeypatch.setattr(main, "api_renew_agent_token", fake_renew)
    stop = FakeStop(false_count=5)

    main._renewal_loop(stop)

    assert main.current_access_token() == "t1"
    assert stop.delays == [main.RENEW_INITIAL_DELAY]


def test_renewal_loop_retries_after_a_transient_failure(monkeypatch):
    main._set_access_token("t1")
    outcomes = iter([RuntimeError("network blip"), ("t2", 100)])

    def fake_renew(url, token):
        result = next(outcomes)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(main, "api_renew_agent_token", fake_renew)
    stop = FakeStop(false_count=2)  # transient failure, then a success

    main._renewal_loop(stop)

    assert main.current_access_token() == "t2"
    # Initial delay, then the retry delay after the blip, then the reschedule.
    assert stop.delays == [
        main.RENEW_INITIAL_DELAY,
        main.RENEW_RETRY_DELAY,
        main.RENEW_MIN_INTERVAL,  # max(min, 100//2) == min
    ]


def test_persist_token_writes_it_back_to_config(tmp_path, monkeypatch):
    cfg = tmp_path / config.CONFIG_FILENAME
    settings = config.merge_settings(
        {
            "host": "mascope.example.com",
            "access_token": "old-token",
            "source": str(tmp_path),
        }
    )
    monkeypatch.setattr(main, "_config_path", str(cfg))
    monkeypatch.setattr(main, "_settings", settings)

    main._persist_token("renewed-token")

    reloaded = config.load_user_config(str(cfg))
    assert reloaded["access_token"] == "renewed-token"
    # verify_tls survives the rewrite untouched.
    assert reloaded["verify_tls"] is True
