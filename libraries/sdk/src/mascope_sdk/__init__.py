"""Mascope SDK - Python client for the Mascope API.

This library provides a Pythonic interface to the Mascope mass spectrometry
data analysis platform. It is designed for researchers working in Jupyter
notebooks who want to load and analyze data from a Mascope server.

For detailed documentation, see the README and docstrings.
"""

from importlib.metadata import version


# Version of the SDK (read from pyproject.toml via installed package metadata)
__version__ = version("mascope_sdk")

# Agent-internal helpers (used by file-agent)
from ._agents import (  # noqa: F401
    AGENT_VERSION,
    AGENT_VERSION_HEADER,
    SERVICE_NAME,
    VERIFY_TLS,
    agent_headers,
    api_post_file,
    api_post_file_tus,
    api_renew_agent_token,
)

# Public API
from .client import MascopeClient
from .exceptions import (
    AuthenticationError,
    ConfigurationError,
    MascopeAPIError,
    MascopeConnectionError,
    MascopeError,
    MascopeTimeoutError,
    NotFoundError,
    ServerError,
    ValidationError,
)


def copy_examples(dest: str = "./mascope_examples") -> None:
    """Copy the bundled example notebooks to a local directory.

    :param dest: Target directory. Created if it doesn't exist.
                 Existing files are **not** overwritten.
    """
    from importlib.resources import files
    from pathlib import Path

    src = files("mascope_sdk").joinpath("examples")
    dest_path = Path(dest)
    dest_path.mkdir(parents=True, exist_ok=True)

    copied = 0
    for item in src.iterdir():
        if item.name.endswith(".ipynb"):
            target = dest_path / item.name
            if target.exists():
                print(f"  skip (exists): {target}")
                continue
            with open(target, "wb") as f:
                f.write(item.read_bytes())
            copied += 1
            print(f"  copied: {target}")

    print(f"\n{copied} notebook(s) copied to {dest_path.resolve()}")


__all__ = [
    "MascopeClient",
    "copy_examples",
    # Exceptions
    "MascopeError",
    "ConfigurationError",
    "MascopeAPIError",
    "AuthenticationError",
    "NotFoundError",
    "ValidationError",
    "ServerError",
    "MascopeConnectionError",
    "MascopeTimeoutError",
    # Agent helpers
    "SERVICE_NAME",
    "VERIFY_TLS",
    "api_post_file",
    "api_post_file_tus",
    "api_renew_agent_token",
]
