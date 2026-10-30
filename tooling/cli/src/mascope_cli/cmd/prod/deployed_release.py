"""
The release a wheel deployment last brought up healthy.

A checkout install needs no record of its own: the checked-out tag says which
release runs, `prod update` moves it, and a boot redeploys it. A wheel
install has no checkout, so without this file the release an update deployed
was forgotten when the update's process exited, and the next `prod up` - a
restart, a reboot - deployed the rolling ``latest`` images instead.

The file lives in the runtime home's ``.runtime/``, beside the updater's own
state. It is not the backend's ``deployment.json`` in the filestore
(``runtime.DEPLOYMENT_FILE``), which holds the deployment's id and travels
with the data; this one says which images the CLI starts, and stays with the
home.
"""

import datetime
import json
import os
from pathlib import Path
from typing import Optional

from mascope_runtime import is_release_tag
from mascope_runtime.atomic import write_json


# Under MASCOPE_PATH.
DEPLOYED_RELEASE_FILE = Path(".runtime") / "deployed-release.json"


class DeployedReleaseError(ValueError):
    """The record exists but does not name a release."""


def path(mascope_path: str) -> Path:
    """Where the record of ``mascope_path``'s deployed release lives."""
    return Path(mascope_path) / DEPLOYED_RELEASE_FILE


def load(mascope_path: str) -> Optional[str]:
    """
    The recorded release, or None when nothing is recorded.

    :raises DeployedReleaseError: The file is there but unreadable, or names
        something other than a release tag. Not None: a record that cannot be
        read is a release the operator deployed and the CLI cannot follow,
        which the caller has to say rather than fall back past in silence.
    """
    p = path(mascope_path)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        raise DeployedReleaseError(f"{p} could not be read ({e})") from e
    version = data.get("version") if isinstance(data, dict) else None
    if not isinstance(version, str) or not is_release_tag(version):
        raise DeployedReleaseError(f"{p} names no release tag ({version!r})")
    return version


def save(mascope_path: str, version: str) -> None:
    """
    Record ``version`` as the deployed release, atomically.

    :raises ValueError: ``version`` is not a release tag.
    :raises OSError: The write failed; the previous record is unchanged.
    """
    if not is_release_tag(version):
        raise ValueError(f"'{version}' is not a release tag")
    p = path(mascope_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    write_json(
        str(p),
        {
            "version": version,
            "deployed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(
                timespec="seconds"
            ),
        },
        indent=2,
    )


def clear(mascope_path: str) -> None:
    """Remove the record, so the deployment follows ``latest`` again."""
    try:
        os.unlink(path(mascope_path))
    except FileNotFoundError:
        pass
