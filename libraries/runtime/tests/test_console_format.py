"""
Tests for the console format (`RuntimeLogging.formatter`) on a colourised sink.

Each line has to end with its colour reset and then the newline, so the next
line on the stream starts clean: a reader that matches a whole line (a marker
a test waits for, a JSON object a command prints) would miss it otherwise.
"""

import io

import pytest

import mascope_runtime.logging as rl


RESET = "\x1b[0m"


class _FakeEnv:
    def path(self):
        return "/env"


class _FakeConfig:
    color = "green"


class _FakeRuntime:
    """Just enough Runtime for RuntimeLogging.formatter()."""

    env = _FakeEnv()
    config = _FakeConfig()

    def path(self):
        return "/mascope"


@pytest.fixture
def console():
    """A colourised sink with the console format, and what it was written."""
    stream = io.StringIO()
    handler = rl.logger.add(
        stream,
        format=rl.RuntimeLogging(_FakeRuntime()).formatter(),
        colorize=True,
        level="DEBUG",
    )
    logger = rl.logger.bind(mod="test", key="", status_code="", method="")
    yield logger, stream
    rl.logger.remove(handler)


def test_dimmed_line_ends_with_reset_then_newline(console, monkeypatch):
    """An info line is dimmed whole, so its reset is the line's last code."""
    monkeypatch.delenv("MASCOPE_LOGGREP", raising=False)
    logger, stream = console
    logger.info("first")
    logger.info("second")
    lines = stream.getvalue().splitlines(keepends=True)
    assert len(lines) == 2
    for line in lines:
        assert not line.startswith(RESET)
        assert line.endswith(RESET + "\n")


def test_traceback_after_a_dimmed_line_starts_clean(console, monkeypatch):
    """The traceback, and the line after it, start with no reset code."""
    monkeypatch.delenv("MASCOPE_LOGGREP", raising=False)
    logger, stream = console
    try:
        raise ValueError("boom")
    except ValueError:
        logger.opt(exception=True).info("failed")
    logger.info("after")
    lines = stream.getvalue().splitlines(keepends=True)
    assert lines[0].endswith(RESET + "\n")
    assert any("ValueError" in line for line in lines)
    assert "after" in lines[-1]
    assert not any(line.startswith(RESET) for line in lines)
