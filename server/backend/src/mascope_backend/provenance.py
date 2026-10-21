"""
The provenance block: which software, on which deployment, produced a result,
and from which records.

One implementation, so that everything Mascope hands out answers "which build
produced this number?" the same way: the batch spreadsheet's Provenance sheet,
and ``GET /api/provenance``, which the SDK stamps onto the frames it returns.

Shape, ``provenance_version`` 1::

    {
        "provenance_version": 1,
        "generated_utc": "2026-10-01T12:00:00Z",
        "deployment_id": "k3J9...",
        "produced_with": {
            "mascope_version": "v1.10.1",
            "match_score_version": 1,
            "peak_assignment_engine_version": "0.5.0"
        },
        "inputs": {
            "dataset_id": "...",
            "dataset_global_id": "k3J9...:dataset:...",
            "sample_batch_id": "...",
            "sample_batch_global_id": "k3J9...:sample_batch:..."
        }
    }

``produced_with`` uses the vocabulary of the demo bundle's ``manifest.json``,
which records ``mascope_version`` and ``match_score_version`` for the goldens
it ships. ``inputs`` names the records a result was built from, and is present
only when the caller passes them. ``deployment_id`` is null when the deployment
has none (``mascope_backend.deployment``).

Each ``<entity>_id`` in ``inputs`` whose entity has a global id
(``GLOBAL_ID_ENTITIES``) and whose value is set gains a sibling
``<entity>_global_id``, ``<deployment_id>:<entity>:<local_id>``, right after
it. A deployment without an id gets no such key rather than a made-up one.

The keys are a contract: a key that has shipped is never renamed or given a new
meaning. A key may be added without changing ``provenance_version``, which
moves only when the meaning of an existing key does.

What ``produced_with`` describes, precisely:

- ``mascope_version`` is ``MASCOPE_VERSION``, read the way ``GET /api/version``
  reads it: the image tag compose deployed, ``latest`` on a deployment that
  tracks it, ``unknown`` where it is unset.
- ``match_score_version`` is the process-wide switch
  (``MASCOPE_MATCH_SCORE_VERSION``), not the scale of any one stored score: an
  aggregate whose frame lacks the per-isotopologue columns v2 needs is scored
  on the v1 scale whatever the switch says (``effective_match_score_version``).
- ``peak_assignment_engine_version`` is the engine this build runs, whether or
  not peak assignment is switched on.

All three describe the server as it runs when the block is generated. A result
stored earlier was computed by whichever build ran then; where a record carries
provenance of its own - a peak assignment run's ``engine_version`` and
``config`` - that record is the authority for it.
"""

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from mascope_backend.api.controllers.match.lib.match_score_v2 import (
    match_score_version,
)
from mascope_backend.api.new.peak_assignments.config import (
    PEAK_ASSIGNMENT_ENGINE_VERSION,
)
from mascope_backend.deployment import (
    GLOBAL_ID_ENTITIES,
    compose_global_id,
    deployment_id,
)
from mascope_backend.runtime import runtime


#: Moves only when the meaning of an existing key changes; see the module
#: docstring.
PROVENANCE_VERSION = 1


def build_provenance(inputs: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """
    The provenance block of a result this server is producing now.

    :param inputs: The records the result is built from, by id - e.g.
        ``{"dataset_id": ..., "sample_batch_id": ...}``. Left out of the block
        when None.
    :return: The block, JSON-serializable as long as ``inputs`` is.
    :rtype: dict
    """
    block: dict[str, Any] = {
        "provenance_version": PROVENANCE_VERSION,
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "deployment_id": deployment_id(),
        "produced_with": {
            "mascope_version": runtime.version or "unknown",
            "match_score_version": match_score_version(),
            "peak_assignment_engine_version": PEAK_ASSIGNMENT_ENGINE_VERSION,
        },
    }
    if inputs is not None:
        block["inputs"] = _with_global_ids(inputs, block["deployment_id"])
    return block


def _with_global_ids(inputs: Mapping[str, Any], namespace: str | None) -> dict:
    """
    ``inputs``, with a ``<entity>_global_id`` after each recognised
    ``<entity>_id`` that is set.

    :param inputs: The records a result is built from, by id.
    :param namespace: The deployment id the block names; no global id is
        added when it is None.
    :return: A new dict, in the order of ``inputs``.
    :rtype: dict
    """
    derived: dict[str, Any] = {}
    for key, value in inputs.items():
        derived[key] = value
        entity = key.removesuffix("_id")
        if (
            namespace is not None
            and key.endswith("_id")
            and entity in GLOBAL_ID_ENTITIES
            and value is not None
        ):
            derived[f"{entity}_global_id"] = compose_global_id(namespace, entity, value)
    return derived


def flatten_provenance(
    block: Mapping[str, Any], prefix: str = ""
) -> list[tuple[str, Any]]:
    """
    The block as ``(key, value)`` rows, for a tabular export.

    Nested keys are joined with ``.`` (``produced_with.mascope_version``) and a
    list becomes one comma-separated value, so a sheet of two columns holds the
    whole block.

    :param block: A block from :func:`build_provenance`.
    :param prefix: Prepended to every key; used for the nesting.
    :return: One row per leaf value, in the block's order.
    :rtype: list[tuple[str, Any]]
    """
    rows: list[tuple[str, Any]] = []
    for key, value in block.items():
        name = f"{prefix}{key}"
        if isinstance(value, Mapping):
            rows.extend(flatten_provenance(value, f"{name}."))
        elif isinstance(value, (list, tuple)):
            rows.append((name, ", ".join(str(each) for each in value)))
        else:
            rows.append((name, value))
    return rows
