"""The session store: one directory per conversation, Frames as parquet.

A session is one conversation's scratch space under the toolkit's scratch
root::

    <root>/<session id>/frames/<frame id>.parquet   the rows
    <root>/<session id>/frames/<frame id>.json      the Frame without them

Nothing in it is a source of truth. A Frame's rows are a cache of what its
recorded query returned: evict them and the next :attr:`Frame.df` loads them
again through that query. A session can be thrown away whole.

The root is ``$MASCOPE_AI_SCRATCH`` when set, else ``mascope_ai/sessions``
under the user's cache directory (``$XDG_CACHE_HOME``, else ``~/.cache``).
"""

import json
import os
import re
import secrets
import shutil
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
from loguru import logger

from .frame import Frame


_SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_FRAME_ID = re.compile(r"f_[0-9a-f]{12}")

#: Runs a Frame's recorded query again and returns its rows.
Reloader = Callable[[dict[str, Any]], pd.DataFrame]


def default_root() -> Path:
    """Where sessions live unless told otherwise."""
    scratch = os.environ.get("MASCOPE_AI_SCRATCH")
    if scratch:
        return Path(scratch)
    cache = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(cache) / "mascope_ai" / "sessions"


def new_session_id() -> str:
    """A fresh session ID: the UTC time it was made and six hex digits."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"s-{stamp}-{secrets.token_hex(3)}"


class Session:
    """One conversation's Frames, kept as parquet in a directory of its own.

    :ivar id: The session's ID: its directory's name.
    :ivar path: Its directory.
    :ivar reloader: Runs a recorded query again, for a Frame whose rows were
        evicted. The toolkit sets it to its own loaders.
    """

    def __init__(
        self,
        session_id: str | None = None,
        *,
        root: Path | str | None = None,
        reloader: Reloader | None = None,
    ):
        """Open a session, new or existing.

        :param session_id: The conversation's ID, to reopen its session; a
            new one when None. Letters, digits, ``.``, ``_`` and ``-`` only.
        :param root: The scratch root; :func:`default_root` when None.
        :param reloader: See :attr:`reloader`.
        :raises ValueError: On an ID that is not a plain directory name.
        """
        session_id = session_id or new_session_id()
        if not _SESSION_ID.fullmatch(session_id):
            raise ValueError(
                f"A session ID is letters, digits, '.', '_' and '-' (up to 64), "
                f"not {session_id!r}"
            )
        self.id = session_id
        self.path = Path(root or default_root()) / session_id
        self.reloader = reloader

    def __repr__(self) -> str:
        return f"Session({self.id!r}, path={str(self.path)!r})"

    def _file(self, frame_id: str, suffix: str) -> Path:
        if not _FRAME_ID.fullmatch(frame_id):
            raise ValueError(f"Not a Frame ID: {frame_id!r}")
        return self.path / "frames" / f"{frame_id}{suffix}"

    def save(self, frame: Frame, df: pd.DataFrame) -> None:
        """Keep a Frame and its rows in the session.

        A column parquet cannot hold as it is - values of mixed kinds in one
        object column - is kept as JSON text, and the Frame's file says which.

        :param frame: The Frame.
        :param df: Its rows.
        """
        rows = self._file(frame.id, ".parquet")
        rows.parent.mkdir(parents=True, exist_ok=True)
        stringified = _write_parquet(df, rows)
        meta = {
            **frame.to_dict(),
            "stringified": stringified,
            "saved_ns": time.time_ns(),
        }
        _write_atomic(self._file(frame.id, ".json"), json.dumps(meta, indent=1))

    def frame(self, frame_id: str) -> Frame:
        """A Frame of this session, by ID; its rows are read when asked for.

        :raises KeyError: If the session has no such Frame.
        """
        meta = self._file(frame_id, ".json")
        if not meta.exists():
            raise KeyError(f"Session {self.id} has no Frame {frame_id}")
        return Frame.from_dict(json.loads(meta.read_text()), session=self)

    def frames(self) -> list[Frame]:
        """Every Frame of this session, oldest first."""
        folder = self.path / "frames"
        if not folder.exists():
            return []
        metas = [json.loads(path.read_text()) for path in folder.glob("f_*.json")]
        metas.sort(key=lambda meta: meta.get("saved_ns", 0))
        return [Frame.from_dict(meta, session=self) for meta in metas]

    def rows(self, frame: Frame) -> pd.DataFrame:
        """A Frame's rows: the session's copy, or its query run again.

        :raises RuntimeError: If the rows were evicted and the session has
            no :attr:`reloader` to load them again with.
        """
        path = self._file(frame.id, ".parquet")
        if path.exists():
            return pd.read_parquet(path)
        if self.reloader is None:
            raise RuntimeError(
                f"Frame {frame.id}'s rows were evicted and session {self.id} "
                "has no toolkit to load them again"
            )
        logger.info("Loading Frame {} again from its query", frame.id)
        df = self.reloader(frame.query)
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_parquet(df, path)
        return df

    def evict(self, frame_id: str) -> None:
        """Drop a Frame's rows from the session, keeping what reloads them."""
        self._file(frame_id, ".parquet").unlink(missing_ok=True)

    def discard(self) -> None:
        """Delete the session's directory and everything in it."""
        shutil.rmtree(self.path, ignore_errors=True)


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def _write_parquet(df: pd.DataFrame, path: Path) -> list[str]:
    """Write *df* to *path* as parquet; return the columns kept as JSON text.

    ``attrs`` are left out: the Frame's own file says what they said.
    """
    bare = df.copy(deep=False)
    bare.attrs = {}
    stringified = []
    for column in bare.columns:
        if bare[column].dtype != object:
            continue
        try:
            pa.array(bare[column], from_pandas=True)
        except (pa.ArrowException, TypeError, ValueError):
            bare[column] = bare[column].map(
                lambda value: (
                    None
                    if value is None
                    else json.dumps(value, default=str, sort_keys=True)
                )
            )
            stringified.append(str(column))
    tmp = path.with_name(path.name + ".tmp")
    bare.to_parquet(tmp, index=False)
    os.replace(tmp, path)
    return stringified
