"""
The deployment's identity: one id per deployment, stable across restarts and
updates.

Records are keyed by nanoids that are unique within one database, so two
deployments can mint the same id for different samples. The deployment id
names the deployment that minted them, and it is what the provenance of
everything the deployment exports carries (``mascope_backend.provenance``).

Resolved in this order:

1. ``[backend] deployment_id``, when an operator configured one.
2. The id generated on the backend's first start, kept in ``deployment.json``
   at the root of the env's filestore. The filestore is persistent in every
   deployment shape - a bind mount in the prod containers, a directory of the
   env in dev - and it is what the off-site backup copies beside the database
   dumps, so the id is restored together with the records it names.
   ``.runtime/secrets/`` would not do: the containers see only the individual
   secret files compose hands them, so an id written there would go with the
   container.

The id is generated once, by the main process at startup
(:func:`ensure_deployment_id`), before any worker serves a request - so no two
processes race to create it - and it is never written over an existing file.
A file that cannot be read is reported and left for an operator, because a
quietly replaced id would split one deployment's exports across two names.
Reading (:func:`deployment_id`) never writes: a process that finds no id
reports none.

The tools that copy a filestore from one deployment to another - ``mascope
env sync`` and the demo bundle - leave the file behind, so the copy keeps the
id it had or generates its own. A restore from backup brings it back, which is
the point when the original is gone. A backup restored beside an original that
keeps running carries the original's id too, unless its file is moved aside
before the copy's first start.
"""

import json
import os
from datetime import datetime, timezone

from mascope_backend.db.id import gen_id
from mascope_backend.runtime import runtime
from mascope_runtime.atomic import write_json
from mascope_runtime.config import DEPLOYMENT_FILE, DEPLOYMENT_ID_PATTERN


class UnreadableDeploymentFile(ValueError):
    """The deployment file exists but holds no id this deployment can use."""


def deployment_file() -> str:
    """
    Where the generated deployment id is kept.

    :return: ``deployment.json`` at the root of the active env's filestore.
    :rtype: str
    """
    return runtime.filestore(DEPLOYMENT_FILE)


def _read_generated(path: str) -> str | None:
    """
    The id kept in ``path``.

    :param path: The deployment file.
    :return: The id, or None when there is no file.
    :raises UnreadableDeploymentFile: If the file exists but cannot be read,
        is not JSON, or holds no valid id.
    """
    try:
        with open(path, encoding="utf-8") as f:
            record = json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        raise UnreadableDeploymentFile(f"{path} cannot be read: {e}") from e
    value = record.get("deployment_id") if isinstance(record, dict) else None
    if not isinstance(value, str) or not DEPLOYMENT_ID_PATTERN.fullmatch(value):
        raise UnreadableDeploymentFile(f"{path} holds no valid deployment id")
    return value


def deployment_id() -> str | None:
    """
    The id of this deployment.

    :return: The configured id, else the generated one, else None - when the
        file is missing or unreadable. :func:`ensure_deployment_id` reports
        why at startup.
    :rtype: str | None
    """
    if runtime.config.deployment_id:
        return runtime.config.deployment_id
    try:
        return _read_generated(deployment_file())
    except UnreadableDeploymentFile:
        return None


def ensure_deployment_id() -> str | None:
    """
    Make sure this deployment has an id, generating it on the first start.

    Called once by the main process at startup, before any worker serves a
    request. A configured id needs no file; otherwise the id kept in the
    deployment file is used, and only when there is no file at all is one
    generated and written. An unreadable file is logged and left alone.

    :return: The deployment's id, or None when its file is unreadable.
    :rtype: str | None
    :raises OSError: If a generated id could not be written.
    """
    if runtime.config.deployment_id:
        return runtime.config.deployment_id
    path = deployment_file()
    try:
        existing = _read_generated(path)
    except UnreadableDeploymentFile as e:
        runtime.logger.warning(
            f"No deployment id: {e}. Exports record none until the file is "
            "repaired, moved aside to generate a new id, or [backend] "
            "deployment_id is set."
        )
        return None
    if existing:
        return existing

    record = {
        "deployment_id": gen_id(),
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    write_json(path, record, indent=2)
    runtime.logger.info(
        f"Generated the deployment id {record['deployment_id']}, kept in {path}"
    )
    return record["deployment_id"]
