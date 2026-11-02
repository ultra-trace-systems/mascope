"""
The Mascope release this CLI was cut with.

A wheel install deploys only this release: the compose file and config layers
it writes into the runtime home are the ones the release was built against, so
moving a wheel deployment to another release means installing that release's
CLI first (``uv tool install mascope-cli==<calver>``). The release manifest
names the CLI cut with each release (``cmd/prod/release_manifest.py``), which
is how ``prod update --auto`` learns which CLI to hand over to.

``RELEASE`` is set at release prep, in the same pull request that bumps the
CLI's calver in ``tooling/cli/pyproject.toml`` and the app's version in
``CITATION.cff``; a test fails while it and ``CITATION.cff`` disagree.
"""

from importlib import metadata
from typing import Optional


# The app release (``vX.Y.Z``, or a pre-release of the next one) this CLI
# deploys on a wheel install.
RELEASE = "v1.15.0"

# The CLI's distribution name, as `uv tool install` and PyPI know it.
DISTRIBUTION = "mascope-cli"


def cli_version() -> Optional[str]:
    """
    The installed CLI's own version (its calver), or None when it has none.

    The workspace's placeholder ``0.0.0`` counts as none: no CLI of that
    version was ever published to install.
    """
    try:
        version = metadata.version("mascope_cli")
    except metadata.PackageNotFoundError:
        return None
    return version if version and version != "0.0.0" else None


def install_command(version: str) -> str:
    """The command that installs the CLI ``version``, for messages."""
    return f"uv tool install {DISTRIBUTION}=={version}"
