"""Frame: a handle to a table that stays in Python.

Tables never pass through a model's context. A loader returns a
:class:`Frame`, whose repr prints the schema, the row count, whether it is
the whole answer, and five rows - so a model that echoes one carelessly
leaks a summary, not 400,000 rows. The rows themselves come out only through
:attr:`Frame.df`, in Python. That is a property of the type, not a request
in a prompt: a Frame is not a DataFrame, cannot be indexed or iterated, and
its repr is bounded whatever the table's size or width.
"""

import html
import math
import secrets
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

import pandas as pd

from .refs import Ref


if TYPE_CHECKING:
    from .session import Session


#: Rows a Frame keeps for its repr.
HEAD_ROWS = 5
#: Columns the repr names in the schema; the rest are counted.
SCHEMA_COLUMNS = 24
#: Columns the repr's head table shows.
HEAD_COLUMNS = 8
#: Characters a value (or a column name) keeps in the repr; longer ones are
#: cut and end in an ellipsis.
CELL_CHARS = 24
#: Source objects the repr names; the rest are counted.
REPR_SOURCES = 3


def new_frame_id() -> str:
    """A fresh Frame ID: ``f_`` and twelve hex digits."""
    return f"f_{secrets.token_hex(6)}"


def _clip(text: str, limit: int = CELL_CHARS) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _quoted(value: Any) -> str:
    """*value* as its repr, a text one cut to :data:`CELL_CHARS` first."""
    return repr(_clip(value)) if isinstance(value, str) else _clip(repr(value))


def jsonable(value: Any) -> Any:
    """*value* as plain JSON values: timestamps as ISO text, NumPy scalars
    as Python ones, NaN and NaT as None, containers converted throughout."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return None if math.isnan(value) else value
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(item) for item in value]
    if value is pd.NaT:
        return None
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return value.isoformat()
    if hasattr(value, "tolist"):  # NumPy scalars and arrays
        return jsonable(value.tolist())
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return str(value)


def _cell(value: Any) -> Any:
    """A head value as the Frame keeps it: JSON, a long text cut short.

    A timestamp is kept whole: its ISO text is bounded anyway.
    """
    if isinstance(value, (datetime, date, pd.Timestamp)) and value is not pd.NaT:
        return value.isoformat()
    value = jsonable(value)
    if isinstance(value, (list, dict)):
        value = str(value)
    return _clip(value) if isinstance(value, str) else value


@dataclass(frozen=True)
class FailureNote:
    """One read a load could not make: where, and with what error.

    :ivar where: What identifies the read - the sample (its ID, name and
        batch), the stage, or the peak - as the SDK's strict loader names it.
    :ivar error_type: The exception's type name.
    :ivar error: Its message.
    """

    where: dict[str, Any]
    error_type: str
    error: str

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "FailureNote":
        """The note of a record from the SDK's ``df.attrs["failures"]``."""
        where = {
            key: jsonable(value)
            for key, value in record.items()
            if key not in ("error_type", "error")
        }
        return cls(
            where, str(record.get("error_type", "")), str(record.get("error", ""))
        )

    def to_dict(self) -> dict[str, Any]:
        return {**self.where, "error_type": self.error_type, "error": self.error}

    def __str__(self) -> str:
        # Bounded like the cells: the names here are the users' own text
        where = ", ".join(
            f"{_clip(key)}={_quoted(value)}" for key, value in self.where.items()
        )
        return f"{where}: {_clip(self.error_type)}: {_clip(self.error, 120)}"


@dataclass(frozen=True, eq=False, repr=False)
class Frame:
    """A loaded table, by handle: its schema, size, completeness and head.

    Made by the toolkit's loaders, never by hand. Its rows are kept in its
    session's directory as parquet and come back through :attr:`df`; when the
    session has evicted them, :attr:`df` loads them again from the recorded
    query.

    :ivar id: The Frame's ID in its session.
    :ivar n_rows: How many rows it has.
    :ivar columns: Its column names.
    :ivar dtypes: Each column's dtype, as text.
    :ivar head5: Its first rows (at most five), each a dict of JSON values,
        long texts cut short.
    :ivar source_refs: The Mascope objects it was loaded from.
    :ivar query: The load that made it, as JSON: ``{"op": ..., "params": ...}``.
    :ivar complete: Whether every read of the load succeeded. False means
        rows are missing, not that the data has none: see :attr:`failures`.
    :ivar failures: Each read that failed.
    :ivar warnings: What the server warned about the reads (peaks a
        time-ranged read left out, say), each warning once.
    :ivar provenance: The server's provenance block - the deployment and the
        software that produced the rows - or None from a server without one.
    """

    id: str
    n_rows: int
    columns: tuple[str, ...]
    dtypes: tuple[str, ...]
    head5: tuple[dict[str, Any], ...]
    source_refs: tuple[Ref, ...]
    query: dict[str, Any]
    complete: bool
    failures: tuple[FailureNote, ...]
    warnings: tuple[str, ...] = ()
    provenance: dict[str, Any] | None = None
    _rows: pd.DataFrame | None = field(default=None, compare=False)
    _session: "Session | None" = field(default=None, compare=False)

    @classmethod
    def from_df(
        cls,
        df: pd.DataFrame,
        *,
        source_refs: "tuple[Ref, ...] | list[Ref]",
        query: dict[str, Any],
        session: "Session | None" = None,
        frame_id: str | None = None,
    ) -> "Frame":
        """The Frame of a loaded table.

        Completeness, failures, warnings and provenance are read off
        ``df.attrs``, where the SDK's loaders put them; a table without
        them is complete.
        """
        attrs = df.attrs
        return cls(
            id=frame_id or new_frame_id(),
            n_rows=len(df),
            columns=tuple(str(column) for column in df.columns),
            dtypes=tuple(str(dtype) for dtype in df.dtypes),
            head5=tuple(
                {str(column): _cell(value) for column, value in row.items()}
                for row in df.head(HEAD_ROWS).to_dict("records")
            ),
            source_refs=tuple(source_refs),
            query=query,
            complete=bool(attrs.get("complete", True)),
            failures=tuple(
                FailureNote.from_record(record) for record in attrs.get("failures", ())
            ),
            warnings=tuple(str(text) for text in attrs.get("warnings", ())),
            provenance=jsonable(attrs["provenance"])
            if isinstance(attrs.get("provenance"), dict)
            else None,
            _rows=df,
            _session=session,
        )

    @property
    def df(self) -> pd.DataFrame:
        """The rows, as a DataFrame of the caller's own.

        Touch this only in Python: it is the whole table. Each call returns a
        fresh copy, so editing it changes neither the Frame nor the session's
        copy.

        :raises RuntimeError: If the Frame holds no rows and no session can
            give them back.
        """
        if self._rows is not None:
            return self._rows.copy()
        if self._session is None:
            raise RuntimeError(
                f"Frame {self.id} has no rows in memory and no session to "
                "load them from"
            )
        return self._session.rows(self)

    def evict(self) -> None:
        """Drop the rows held in memory; :attr:`df` reads the session's copy."""
        object.__setattr__(self, "_rows", None)

    def __len__(self) -> int:
        return self.n_rows

    def summary(self) -> str:
        """The Frame as its repr prints it: bounded, whatever its size."""
        lines = [
            f"Frame {self.id}: {self.n_rows:,} rows x {len(self.columns)} "
            f"columns, {'complete' if self.complete else 'INCOMPLETE'}"
        ]
        if self.failures:
            lines.append(
                f"  {len(self.failures)} read(s) failed, so rows are missing; "
                f"first: {self.failures[0]} (all in .failures)"
            )
        if self.warnings:
            lines.append(
                f"  {len(self.warnings)} server warning(s); first: "
                f"{_clip(self.warnings[0], 160)} (all in .warnings)"
            )
        if self.source_refs:
            named = ", ".join(
                f"{ref.kind} {_clip(ref.name)!r}"
                for ref in self.source_refs[:REPR_SOURCES]
            )
            more = len(self.source_refs) - REPR_SOURCES
            lines.append(f"  from: {named}{f' and {more} more' if more > 0 else ''}")
        lines.append(f"  query: {self.query.get('op', '?')}")

        schema = [
            f"{_clip(column)}: {dtype}"
            for column, dtype in zip(
                self.columns[:SCHEMA_COLUMNS], self.dtypes[:SCHEMA_COLUMNS]
            )
        ]
        more = len(self.columns) - SCHEMA_COLUMNS
        if more > 0:
            schema.append(f"... and {more} more")
        lines.append("  columns: " + ", ".join(schema))

        if self.head5:
            shown = self.columns[:HEAD_COLUMNS]
            head = pd.DataFrame(
                [[row.get(column) for column in shown] for row in self.head5],
                columns=[_clip(column) for column in shown],
            )
            cut = (
                f", first {HEAD_COLUMNS} columns"
                if len(self.columns) > HEAD_COLUMNS
                else ""
            )
            lines.append(f"  head ({len(self.head5)} of {self.n_rows:,} rows{cut}):")
            lines.extend(
                "    " + line for line in head.to_string(index=False).splitlines()
            )
        lines.append("  The rows stay in Python: use .df there.")
        return "\n".join(lines)

    def __repr__(self) -> str:
        return self.summary()

    __str__ = __repr__

    def _repr_html_(self) -> str:
        # A notebook would otherwise render nothing of the kind; the summary
        # is all it gets, as everywhere else.
        return f"<pre>{html.escape(self.summary())}</pre>"

    def to_dict(self) -> dict[str, Any]:
        """The Frame without its rows, as plain JSON values (what a
        transport hands a model, and what the session keeps beside them)."""
        return {
            "id": self.id,
            "n_rows": self.n_rows,
            "columns": list(self.columns),
            "dtypes": list(self.dtypes),
            "head5": [dict(row) for row in self.head5],
            "source_refs": [ref.to_dict() for ref in self.source_refs],
            "query": self.query,
            "complete": self.complete,
            "failures": [note.to_dict() for note in self.failures],
            "warnings": list(self.warnings),
            "provenance": self.provenance,
        }

    @classmethod
    def from_dict(
        cls, data: dict[str, Any], session: "Session | None" = None
    ) -> "Frame":
        """The Frame :meth:`to_dict` wrote, its rows left to *session*."""
        return cls(
            id=data["id"],
            n_rows=data["n_rows"],
            columns=tuple(data["columns"]),
            dtypes=tuple(data["dtypes"]),
            head5=tuple(data["head5"]),
            source_refs=tuple(Ref.from_dict(ref) for ref in data["source_refs"]),
            query=data["query"],
            complete=data["complete"],
            failures=tuple(
                FailureNote.from_record(record) for record in data["failures"]
            ),
            warnings=tuple(data.get("warnings", ())),
            provenance=data.get("provenance"),
            _session=session,
        )
