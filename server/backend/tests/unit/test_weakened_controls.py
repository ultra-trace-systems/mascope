"""Tests: the test-only switches for the weakened target take effect on a demo stack alone.

``mascope_backend.weakened_controls`` holds the two switches the security
suite's weakened target sets to turn the backend's rate limiter and its
write-origin guard off. What must hold:

- a switch takes effect only beside the demo stack's marker, read as the demo
  seed reads it, so a real deployment that copies a switch keeps its control;
- a switch is off unless set to a value the seed reads as set;
- at start, a switch that took effect is logged as a warning naming the
  control, and one that did not as an error, and with none set nothing is.

The wiring into the controls themselves is pinned beside them:
``tests/system/security/test_weakened_rate_limit.py`` (the limiter) and
``tests/integration/api/test_request_origin.py`` (the guard).
"""

import pytest
from test_utils import captured_logs

from mascope_backend import weakened_controls as wc
from mascope_backend.db.scripts.seed_demo import DEMO_SEED_ENV_FLAG, _demo_seed_allowed


@pytest.fixture(autouse=True)
def bare_env(monkeypatch):
    """No switch and no marker, whatever the shell running the suite exports."""
    for name in (wc.DEMO_STACK_MARKER, *wc.SWITCHES):
        monkeypatch.delenv(name, raising=False)


SWITCHES = sorted(wc.SWITCHES)


def test_the_marker_is_the_demo_seeds():
    assert wc.DEMO_STACK_MARKER == DEMO_SEED_ENV_FLAG


@pytest.mark.parametrize(
    "value", ["1", "true", "TRUE", " yes ", "on", "", "0", "false", "off", "2", "nope"]
)
def test_the_marker_is_read_as_the_demo_seed_reads_it(monkeypatch, value):
    """One marker, one reading: a value the seed refuses must not weaken."""
    monkeypatch.setenv(DEMO_SEED_ENV_FLAG, value)
    assert wc.on_a_demo_stack() is _demo_seed_allowed()


@pytest.mark.parametrize("switch", SWITCHES)
def test_a_switch_on_a_demo_stack_takes_effect(monkeypatch, switch):
    monkeypatch.setenv(wc.DEMO_STACK_MARKER, "1")
    monkeypatch.setenv(switch, "1")
    assert wc.switched_off(switch) is True


@pytest.mark.parametrize("switch", SWITCHES)
@pytest.mark.parametrize("marker", [None, "", "0", "false"])
def test_a_switch_without_the_marker_is_ignored(monkeypatch, switch, marker):
    """A real deployment never sets the marker: the control stays on."""
    if marker is not None:
        monkeypatch.setenv(wc.DEMO_STACK_MARKER, marker)
    monkeypatch.setenv(switch, "1")
    assert wc.switched_off(switch) is False


@pytest.mark.parametrize("switch", SWITCHES)
@pytest.mark.parametrize("value", [None, "", "0", "false", "off"])
def test_a_switch_not_set_leaves_the_control_on_a_demo_stack(
    monkeypatch, switch, value
):
    """The demo stack as it ships keeps every control."""
    monkeypatch.setenv(wc.DEMO_STACK_MARKER, "1")
    if value is not None:
        monkeypatch.setenv(switch, value)
    assert wc.switched_off(switch) is False


def test_the_two_switches_are_independent(monkeypatch):
    monkeypatch.setenv(wc.DEMO_STACK_MARKER, "1")
    monkeypatch.setenv(wc.RATE_LIMIT_OFF, "1")
    assert wc.switched_off(wc.RATE_LIMIT_OFF) is True
    assert wc.switched_off(wc.ORIGIN_GUARD_OFF) is False


def _announced() -> list[tuple[str, str]]:
    with captured_logs(level="DEBUG") as records:
        wc.announce()
    return [
        (r["level"].name, r["message"]) for r in records if r["function"] == "announce"
    ]


@pytest.mark.parametrize("switch", SWITCHES)
def test_a_switch_that_took_effect_is_announced_as_a_weakened_stack(
    monkeypatch, switch
):
    monkeypatch.setenv(wc.DEMO_STACK_MARKER, "1")
    monkeypatch.setenv(switch, "1")
    [(level, message)] = _announced()
    assert level == "WARNING"
    assert message.startswith("WEAKENED STACK: ")
    assert wc.SWITCHES[switch] in message and switch in message


@pytest.mark.parametrize("switch", SWITCHES)
def test_a_switch_set_off_a_demo_stack_is_logged_as_ignored(monkeypatch, switch):
    monkeypatch.setenv(switch, "1")
    [(level, message)] = _announced()
    assert level == "ERROR"
    assert "ignored" in message and f"{wc.SWITCHES[switch]} stays on" in message


def test_both_switches_are_announced(monkeypatch):
    monkeypatch.setenv(wc.DEMO_STACK_MARKER, "1")
    for switch in SWITCHES:
        monkeypatch.setenv(switch, "1")
    announced = _announced()
    assert [level for level, _ in announced] == ["WARNING", "WARNING"]
    for switch in SWITCHES:
        assert any(switch in message for _, message in announced)


def test_nothing_is_announced_without_a_switch(monkeypatch):
    monkeypatch.setenv(wc.DEMO_STACK_MARKER, "1")
    assert _announced() == []
