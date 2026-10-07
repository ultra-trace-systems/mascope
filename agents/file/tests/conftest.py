"""Shared fixtures for the File Agent tests.

Every request the agent makes reports the service name and version its
process has set on the SDK, and a process that runs an agent sets them with
``identity()`` before it does anything else. A test that exercised a module
without that call would see the SDK's own defaults - a configuration the
agent never runs in. The same call is made here so each test starts from the
agent's real identity.
"""

import pytest

import mascope_sdk
from mascope_file_agent import AgentSettings, identity


@pytest.fixture(autouse=True)
def agent_sdk_identity(monkeypatch):
    # Through monkeypatch first, so that what the call sets is undone after
    # the test: the identity is the SDK's, and outlives any one agent.
    for name in ("SERVICE_NAME", "AGENT_VERSION", "VERIFY_TLS"):
        monkeypatch.setattr(mascope_sdk, name, getattr(mascope_sdk, name))
    identity()


@pytest.fixture
def make_settings(tmp_path):
    """Settings an agent can run on, watching the test's own folder."""

    def make(**overrides):
        return AgentSettings(
            **{
                "host": "mascope.example.com",
                "access_token": "tok",
                "source": str(tmp_path),
                "instrument": "Orbi-Lab2",
                **overrides,
            }
        )

    return make
