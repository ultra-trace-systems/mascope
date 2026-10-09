"""Unit tests for the identity a process gives itself before it runs an agent.

The SDK keeps one service name, one agent version and one TLS setting for the
whole process, and every request reports them. ``identity()`` is the one place
they are set; importing the agent sets none of them.
"""

import subprocess
import sys

import pytest

import mascope_sdk
from mascope_file_agent import Agent, __version__, agent, identity
from mascope_sdk import _agents, agent_headers


class StubLogger:
    def __init__(self):
        self.warnings = []

    def warning(self, message):
        self.warnings.append(message)

    def info(self, message):
        pass

    def debug(self, message):
        pass


def test_identity_is_what_every_request_reports():
    identity("instrument-agent", version="6.0.0", verify_tls=False)

    headers = agent_headers("tok")
    assert headers["X-Service-Name"] == "instrument-agent"
    assert headers["X-Agent-Version"] == "6.0.0"
    assert mascope_sdk.VERIFY_TLS is False


def test_identity_defaults_to_the_file_agent_at_this_version():
    identity("instrument-agent", version="6.0.0", verify_tls=False)

    identity()

    headers = agent_headers("tok")
    assert headers["X-Service-Name"] == "file-agent"
    assert headers["X-Agent-Version"] == __version__
    assert mascope_sdk.VERIFY_TLS is True


def test_importing_the_agent_sets_no_identity():
    """A program that embeds the agent decides who it is, not an import."""
    reported = subprocess.run(
        [
            sys.executable,
            "-c",
            "import mascope_sdk, mascope_file_agent, mascope_file_agent.main;"
            "print(mascope_sdk.SERVICE_NAME, mascope_sdk.AGENT_VERSION,"
            " mascope_sdk.VERIFY_TLS)",
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    assert reported.stdout.split() == ["mascope_sdk", "None", "True"]


def test_the_agent_knows_the_name_the_sdk_starts_with():
    """The check below compares against it, so it has to be the SDK's own."""
    assert agent.SDK_SERVICE_NAME == _agents.SERVICE_NAME


def test_an_agent_does_not_start_before_the_process_says_who_it_is(
    monkeypatch, make_settings
):
    """A device token is refused under any service name but its own.

    Started without the call, the agent would send the SDK's default name and
    every upload would come back as a refused credential - which reads as a
    machine that needs pairing, not as a program that forgot a call.
    """
    monkeypatch.setattr(mascope_sdk, "SERVICE_NAME", "mascope_sdk")
    unidentified = Agent(make_settings(), logger=StubLogger())

    with pytest.raises(RuntimeError, match=r"identity\(\)"):
        unidentified.start()
    with pytest.raises(RuntimeError, match=r"identity\(\)"):
        unidentified.run_until_complete()

    assert not unidentified.running
    assert not unidentified.watcher.observer.is_alive()


def test_an_agent_says_when_its_tls_setting_is_not_the_process_s(
    monkeypatch, make_settings
):
    """Two settings for one thing: the log names the one uploads follow."""
    identity(verify_tls=True)
    mismatched = Agent(make_settings(verify_tls=False), logger=StubLogger())
    monkeypatch.setattr(mismatched.watcher, "start", lambda: None)
    monkeypatch.setattr(mismatched.uploader, "start", lambda: None)

    mismatched._begin()

    assert any(
        "uploads follow identity()" in line for line in mismatched.logger.warnings
    )
