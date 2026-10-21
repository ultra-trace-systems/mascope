"""The provenance manifest: what each answer of a session rests on.

Every session keeps a ``manifest.jsonl`` beside its Frames, one JSON object
a line, appended to and never rewritten:

``toolkit``
    Written once by each toolkit opened on the session, before its first
    call: the ``mascope_ai`` and ``mascope_sdk`` versions, Python's, the
    server and the workspace.
``call``
    One per call of the toolkit (``find``, ``describe``, a loader, the
    oracle, ``derive``): the operation and its parameters, the Mascope
    objects it touched (``refs``), the Frame it made, the server's
    provenance block, which values in it Mascope returned and which the
    SDK computed client-side, and how it ended (``ok``, or the error).
``answer``
    One per :meth:`~mascope_ai.toolkit.Toolkit.answer`: the answer's text,
    the code that produced it, the calls since the answer before it, every
    object those touched, and the split between values Mascope returned,
    values the SDK computed client-side, candidates Mascope did not score,
    and statements the model inferred.

A record's ``seq`` counts the lines of the file, so a session reopened by
another toolkit numbers on. The manifest is a record, not a source of truth
for the data: the Frames' queries are what reload them.
"""

import json
import platform
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from .frame import jsonable
from .refs import Ref


#: The columns the SDK computes itself rather than reads from Mascope, and
#: how. A peak load (``load_peaks``, ``load_peaks_by_stage``) with matches
#: carries them; the server's own ion and compound scores, which the
#: oracle's tree carries, are another computation.
CLIENT_COMPUTED = {
    "match_score_ion": (
        "computed by mascope_sdk from Mascope's isotope scores: their sum "
        "weighted by relative abundance, per ion"
    ),
    "match_score_compound": (
        "computed by mascope_sdk from its ion scores: the best, per compound"
    ),
}

#: The operations whose Frames can carry :data:`CLIENT_COMPUTED` columns.
CLIENT_OPS = ("load_peaks", "load_peaks_by_stage")


def package_version(name: str) -> str | None:
    """An installed distribution's version, None if it is not installed."""
    try:
        return version(name)
    except PackageNotFoundError:
        return None


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def ref_list(refs) -> list[dict[str, Any]]:
    """*refs* as JSON, each object once, in the order first seen."""
    out, seen = [], set()
    for ref in refs:
        key = (ref.kind, ref.id)
        if key not in seen:
            seen.add(key)
            out.append(ref.to_dict())
    return out


class Manifest:
    """A session's ``manifest.jsonl``.

    :ivar path: The file.
    """

    def __init__(self, path: Path | str):
        self.path = Path(path)

    def __repr__(self) -> str:
        return f"Manifest({str(self.path)!r})"

    def records(self, kind: str | None = None) -> list[dict[str, Any]]:
        """The records, oldest first; only those of type *kind* when given."""
        if not self.path.exists():
            return []
        records = [
            json.loads(line) for line in self.path.read_text().splitlines() if line
        ]
        return [r for r in records if kind is None or r.get("type") == kind]

    def append(self, record: dict[str, Any]) -> dict[str, Any]:
        """Add *record*, numbered and stamped; return it as written."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        seq = (
            sum(1 for line in self.path.read_text().splitlines() if line)
            if self.path.exists()
            else 0
        )
        written = jsonable({"seq": seq, "at": now(), **record})
        with self.path.open("a") as out:
            out.write(json.dumps(written, sort_keys=False) + "\n")
        return written

    def touched(self) -> list[Ref]:
        """Every Mascope object a call of the session touched, each once,
        in the order first touched."""
        seen: set[tuple[str, str]] = set()
        refs = []
        for record in self.records("call"):
            for data in record.get("refs", ()):
                ref = Ref.from_dict(data)
                if (ref.kind, ref.id) not in seen:
                    seen.add((ref.kind, ref.id))
                    refs.append(ref)
        return refs

    def since_last_answer(self) -> list[dict[str, Any]]:
        """The call records after the newest answer (all, before the first)."""
        calls: list[dict[str, Any]] = []
        for record in self.records():
            if record.get("type") == "answer":
                calls = []
            elif record.get("type") == "call":
                calls.append(record)
        return calls


def toolkit_record(client: Any, session_id: str) -> dict[str, Any]:
    """The ``toolkit`` record of a toolkit over *client*."""
    return {
        "type": "toolkit",
        "session": session_id,
        "mascope_ai": package_version("mascope_ai"),
        "mascope_sdk": package_version("mascope_sdk"),
        "python": platform.python_version(),
        "server": getattr(client, "url", None),
        "workspace": {
            "id": getattr(client, "workspace_id", None),
            "name": getattr(client, "workspace_name", None),
        },
    }


def frame_values(frame: Any) -> dict[str, Any]:
    """Which of a Frame's columns Mascope returned and which the SDK
    computed client-side, and how."""
    client = set(frame.client_columns)
    return {
        "mascope": [column for column in frame.columns if column not in client],
        "client": {column: CLIENT_COMPUTED[column] for column in frame.client_columns},
    }
