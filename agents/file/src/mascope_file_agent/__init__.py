"""Mascope File Agent - watches a folder and uploads new data files.

Installed as a program it is ``mascope-file-agent``. As a library it is
:class:`Agent`, built from :class:`AgentSettings` in a process that has said
who it is with :func:`identity`; see :mod:`mascope_file_agent.agent`.
"""

try:
    # Written by build.ps1 so the frozen exe reports its release version;
    # absent (and gitignored) in a source checkout.
    from mascope_file_agent._version import __version__
except ImportError:
    __version__ = "dev"

# Below the version, which the modules they import read from this package.
from mascope_file_agent.agent import Agent, identity  # noqa: E402
from mascope_file_agent.config import ConfigError  # noqa: E402
from mascope_file_agent.credentials import ConsoleRepair, Repair  # noqa: E402
from mascope_file_agent.settings import AgentSettings  # noqa: E402


__all__ = [
    "Agent",
    "AgentSettings",
    "ConfigError",
    "ConsoleRepair",
    "Repair",
    "__version__",
    "identity",
]
