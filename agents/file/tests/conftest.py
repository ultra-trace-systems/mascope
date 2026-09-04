"""Shared fixtures for the File Agent tests.

The agent sets the SDK's package-level service name and version when
``mascope_file_agent.main`` is imported, and every request it makes reports
them. A test that exercises a module without importing main would otherwise
see the SDK's own defaults - a configuration the agent never runs in - and
whether it did would depend on which test imported main first. The same
assignment is made here so each test starts from the agent's real identity.
"""

import pytest

import mascope_sdk
from mascope_file_agent import __version__


@pytest.fixture(autouse=True)
def agent_sdk_identity(monkeypatch):
    monkeypatch.setattr(mascope_sdk, "SERVICE_NAME", "file-agent")
    monkeypatch.setattr(mascope_sdk, "AGENT_VERSION", __version__)
