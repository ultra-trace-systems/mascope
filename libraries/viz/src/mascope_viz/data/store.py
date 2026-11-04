"""The campaign store: a scope's data as a local parquet cache.

A long time series is cheap only if it is not rebuilt from per-sample calls
each time. :func:`sync` fills a directory per server and scope key with the
tables of ``docs/dev/visual_digest.md`` (section 2.2), one call per batch per
series kind, and extends it incrementally; :class:`CampaignStore` reads them
back and says what it holds (:meth:`CampaignStore.status`). The store is a
cache, never a source of truth: delete it at any time and sync again.

The tables, all times in UTC:

- ``samples``: one row per sample in the scope, with ``t_mid_utc`` (its
  file's start plus the middle of its window, ``(t0 + t1) / 2``).
- ``files``: one row per file acquired in the range, ``in_batch`` saying
  whether a batch of the scope holds a sample of it.
- ``ion_series``: one row per (sample, target ion) with a match record, keyed
  ``ion:<target_ion_id>``; a match record without a match is NaN, never 0.
- ``peak_series``: one row per (sample, batch peak) the batch ledger holds a
  formula for, keyed ``formula:<formula>|<mechanism>``, with the ledger run
  that read it (``run_id``).
- ``species``: one row per series key: name, formula, ion formula,
  mechanism, class, m/z, element counts, O/C, H/C and OSc.
- ``rollups``: per hour and per day, the median, mean, 10th and 90th
  percentiles and count of each series key's values.

On disk, under ``<root>/<server>/<workspace>/<scope key>/``::

    store.json                    the scope, the server, the provenance and
                                  each batch's sync stamp
    files.parquet                 the files listing, rewritten every sync
    samples/<batch>.parquet       one file per batch per table
    ion_series/<batch>.parquet
    peak_series/<batch>.parquet
    species/<batch>.parquet
    rollups/<YYYY-MM-DD>.parquet  one file per UTC day: a day's median is
                                  no median of its batches' medians

One sync at a time per store: two at once would each rewrite ``store.json``.
"""

import hashlib
import json
import os
import re
import shutil
import sys
import time
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from mascope_sdk.exceptions import NotFoundError

from ..errors import NotAuthenticated, VizError, sdk_errors
from .scope import ResolvedScope, Scope, resolve


if TYPE_CHECKING:
    from mascope_sdk import MascopeClient


#: The layout of a store's directory; a store of another is rebuilt.
FORMAT = 1

#: The tables, in the order the note lists them.
TABLES = ("samples", "files", "ion_series", "peak_series", "species", "rollups")

#: The tables kept as one file per batch.
BATCH_TABLES = ("samples", "ion_series", "peak_series", "species")

#: The environment variable naming the directory stores live under.
ROOT_ENV = "MASCOPE_VIZ_STORE"

#: A batch with a sample acquired less than this long ago is refetched on
#: every sync: it is the batch holding today, still filling, in any zone.
LIVE = pd.Timedelta(days=1)

#: The rollups' bins, as pandas offsets, and what ``rollups.bin`` calls them.
BINS = {"1h": "hour", "1D": "day"}

#: The rollups' percentiles, as ``rollups`` names their columns.
PERCENTILES = {"p10": 0.1, "p90": 0.9}

_UTC = pa.timestamp("ns", tz="UTC")

#: Each table's columns and types, as the store writes them.
SCHEMAS = {
    "samples": pa.schema(
        [
            ("sample_item_id", pa.string()),
            ("sample_batch_id", pa.string()),
            ("sample_file_id", pa.string()),
            ("t_mid_utc", _UTC),
            ("datetime_utc", _UTC),
            ("t0", pa.float64()),
            ("t1", pa.float64()),
            ("tic", pa.float64()),
            ("polarity", pa.string()),
            ("instrument", pa.string()),
            ("instrument_type", pa.string()),
            ("filename", pa.string()),
            ("ionization_mode_id", pa.string()),
        ]
    ),
    "files": pa.schema(
        [
            ("sample_file_id", pa.string()),
            ("filename", pa.string()),
            ("instrument", pa.string()),
            ("instrument_type", pa.string()),
            ("datetime_utc", _UTC),
            ("acquisition_timezone", pa.string()),
            ("length", pa.float64()),
            ("polarity", pa.string()),
            ("in_batch", pa.bool_()),
        ]
    ),
    "ion_series": pa.schema(
        [
            ("sample_item_id", pa.string()),
            ("series_key", pa.string()),
            ("intensity", pa.float64()),
            ("match_category", pa.int64()),
        ]
    ),
    "peak_series": pa.schema(
        [
            ("sample_item_id", pa.string()),
            ("series_key", pa.string()),
            ("intensity", pa.float64()),
            ("tier", pa.string()),
            ("batch_peak_id", pa.string()),
            ("run_id", pa.string()),
        ]
    ),
    "species": pa.schema(
        [
            ("series_key", pa.string()),
            ("name", pa.string()),
            ("formula", pa.string()),
            ("ion_formula", pa.string()),
            ("mechanism", pa.string()),
            ("class", pa.string()),
            ("mz", pa.float64()),
            ("n_c", pa.int64()),
            ("n_h", pa.int64()),
            ("n_n", pa.int64()),
            ("n_o", pa.int64()),
            ("n_s", pa.int64()),
            ("o_c", pa.float64()),
            ("h_c", pa.float64()),
            ("osc", pa.float64()),
        ]
    ),
    "rollups": pa.schema(
        [
            ("bin", pa.string()),
            ("t", _UTC),
            ("series_key", pa.string()),
            ("reading", pa.string()),
            ("median", pa.float64()),
            ("mean", pa.float64()),
            ("p10", pa.float64()),
            ("p90", pa.float64()),
            ("n", pa.int64()),
        ]
    ),
}

#: The columns read back as categoricals, as the synthetic campaign has them:
#: a month of series is millions of rows over a few thousand keys.
CATEGORICAL = {
    "samples": ("sample_batch_id",),
    "ion_series": ("sample_item_id", "series_key"),
    "peak_series": ("sample_item_id", "series_key", "tier", "batch_peak_id", "run_id"),
    "rollups": ("bin", "series_key", "reading"),
}

#: The key of a parquet file's metadata that holds what the store knows of it.
METADATA_KEY = b"mascope"


def _now() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC")


def _iso(moment: Any) -> str | None:
    if moment is None or moment is pd.NaT or pd.isna(moment):
        return None
    return pd.Timestamp(moment).tz_convert("UTC").isoformat()


def _moment(text: str | None) -> pd.Timestamp | None:
    return None if text is None else pd.Timestamp(text)


# --- where a store lives ------------------------------------------------------


def default_root() -> Path:
    """The directory stores live under: ``$MASCOPE_VIZ_STORE``, else the
    user's cache directory (``~/.cache/mascope_viz/stores`` on Linux)."""
    named = os.environ.get(ROOT_ENV)
    if named:
        return Path(named).expanduser()
    if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        cache = Path(os.environ["LOCALAPPDATA"])
    elif sys.platform == "darwin":
        cache = Path.home() / "Library" / "Caches"
    else:
        cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return cache / "mascope_viz" / "stores"


def server_name(url: str) -> str:
    """A server's directory name: its host, port and path, as one safe word.

    ``https://example.mascope.app`` is ``example.mascope.app``,
    ``http://localhost:8090`` is ``localhost_8090``.
    """
    parts = urlsplit(url if "//" in url else f"//{url}")
    name = (parts.hostname or "").lower()
    if parts.port:
        name += f"_{parts.port}"
    path = parts.path.strip("/")
    if path:
        name += f"_{path}"
    return re.sub(r"[^a-z0-9.-]+", "_", name.lower()).strip("_") or "server"


def _file_name(batch_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", batch_id) + ".parquet"


def store_path(
    client: "MascopeClient", resolved: ResolvedScope, root: Path | str | None = None
) -> Path:
    """The directory of *resolved*'s store on *client*'s server.

    :param root: The directory stores live under; :func:`default_root`
        when None.
    """
    base = Path(root) if root is not None else default_root()
    return base / server_name(client.url) / client.workspace_id / resolved.key


# --- formulas -------------------------------------------------------------------

_TOKEN = re.compile(
    r"\(|\)(\d*)"  # a group opens, or closes with its multiplier
    r"|\[\d+([A-Z][a-z]?)\](\d*)"  # an isotope in brackets: [15N], [13C]2
    r"|\^?([A-Z][a-z]?)(\d*)"  # an element, or a heavy isotope: ^N
)


def element_counts(formula: Any) -> dict[str, int] | None:
    """The elements of a formula as Mascope writes it, by symbol.

    Groups multiply (``(HNO3)2``, ``CO(NH2)2``), and a heavy isotope, written
    ``^N`` for 15N or in brackets (``[18O]``, ``[13C]2``), counts as its
    element. None for what is not a formula.
    """
    if not isinstance(formula, str) or not formula.strip():
        return None
    text = "".join(formula.split())
    stack: list[dict[str, int]] = [{}]
    position = 0
    while position < len(text):
        token = _TOKEN.match(text, position)
        if token is None:
            return None
        position = token.end()
        if token.group(0) == "(":
            stack.append({})
        elif token.group(0).startswith(")"):
            if len(stack) == 1:
                return None
            group = stack.pop()
            times = int(token.group(1) or 1)
            for element, n in group.items():
                stack[-1][element] = stack[-1].get(element, 0) + n * times
        else:
            element = token.group(2) or token.group(4)
            n = int(token.group(3) or token.group(5) or 1)
            stack[-1][element] = stack[-1].get(element, 0) + n
    if len(stack) != 1 or not stack[0]:
        return None
    return stack[0]


def compound_class(counts: dict[str, int] | None) -> str | None:
    """The compound class atmospheric chemistry groups by: ``CHO``,
    ``CHON``, ``CHOS``, ``CHONS``, ``CH``, ...

    The letters of C, H, O, N and S the formula holds, in that order, with
    ``X`` after them for any other element (a halogen, a metal);
    ``inorganic`` for a formula without carbon.
    """
    if not counts:
        return None
    if not counts.get("C"):
        return "inorganic"
    letters = "".join(e for e in "CHONS" if counts.get(e))
    return letters + ("X" if set(counts) - set("CHONS") else "")


def _species_row(
    key: str,
    name: Any,
    formula: Any,
    ion_formula: Any,
    mechanism: Any,
    mz: float = np.nan,
) -> dict[str, Any]:
    """A ``species`` row. OSc is the carbon oxidation state estimated as
    ``2 O/C - H/C`` (Kroll et al. 2011)."""
    counts = element_counts(formula)
    row: dict[str, Any] = {
        "series_key": key,
        "name": None if name is None or pd.isna(name) else str(name),
        "formula": formula if isinstance(formula, str) else None,
        "ion_formula": ion_formula if isinstance(ion_formula, str) else None,
        "mechanism": mechanism if isinstance(mechanism, str) else None,
        "class": compound_class(counts),
        "mz": float(mz) if mz is not None and not pd.isna(mz) else np.nan,
    }
    for element in ("C", "H", "N", "O", "S"):
        row[f"n_{element.lower()}"] = None if counts is None else counts.get(element, 0)
    carbon = (counts or {}).get("C", 0)
    if carbon:
        o_c = counts.get("O", 0) / carbon
        h_c = counts.get("H", 0) / carbon
        row.update(o_c=o_c, h_c=h_c, osc=2.0 * o_c - h_c)
    else:
        row.update(o_c=np.nan, h_c=np.nan, osc=np.nan)
    return row


# --- series keys ----------------------------------------------------------------


def ion_key(target_ion_id: str) -> str:
    """The series key of a target ion (section 2.3)."""
    return f"ion:{target_ion_id}"


def formula_key(formula: str, mechanism: str) -> str:
    """The series key of an assigned formula read under a mechanism, its
    adduct in Mascope's notation (``[M+I]-``): section 2.3."""
    return f"formula:{' '.join(formula.split())}|{mechanism}"


def reading_of(run: dict[str, Any] | None) -> str | None:
    """What a ledger run reads formulas as: its engine and version, and a
    digest of its configuration when it has one.

    A run is a batch's own, so two batches never share a run ID; two runs
    with the same reading read the same formulas the same way, and a scope
    whose batches carry two readings mixes runs. An import's ``rows``, the
    number of rows sent for its one batch, is no part of a reading.
    """
    if not run:
        return None
    reading = f"{run.get('engine') or 'unknown'} {run.get('engine_version') or '?'}"
    config = run.get("config")
    if isinstance(config, dict):
        config = {name: value for name, value in config.items() if name != "rows"}
    if config:
        text = json.dumps(config, sort_keys=True, default=str)
        reading += f" config {hashlib.sha256(text.encode()).hexdigest()[:8]}"
    return reading


# --- parquet --------------------------------------------------------------------


def _frame(rows: "pd.DataFrame | list[dict[str, Any]]", table: str) -> pd.DataFrame:
    """*rows* with exactly *table*'s columns, in order."""
    frame = pd.DataFrame(rows) if not isinstance(rows, pd.DataFrame) else rows
    columns = SCHEMAS[table].names
    for column in columns:
        if column not in frame:
            frame = frame.assign(**{column: None})
    return frame[columns].reset_index(drop=True)


def _write(path: Path, frame: pd.DataFrame, table: str, about: dict[str, Any]) -> None:
    """Write *frame* as *table*'s parquet at *path*, whole or not at all."""
    path.parent.mkdir(parents=True, exist_ok=True)
    arrow = pa.Table.from_pandas(
        _frame(frame, table), schema=SCHEMAS[table], preserve_index=False
    )
    metadata = dict(arrow.schema.metadata or {})
    metadata[METADATA_KEY] = json.dumps(about, sort_keys=True, default=str).encode()
    arrow = arrow.replace_schema_metadata(metadata)
    partial = path.with_name(path.name + ".partial")
    pq.write_table(arrow, partial, compression="zstd")
    os.replace(partial, path)


def _write_json(path: Path, content: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    partial.write_text(json.dumps(content, indent=1, sort_keys=True, default=str))
    os.replace(partial, path)


def _read(paths: Sequence[Path], table: str, filters: Any = None) -> pd.DataFrame:
    schema = SCHEMAS[table]
    tables = [
        pq.read_table(path, schema=schema, filters=filters).replace_schema_metadata(
            None
        )
        for path in paths
        if path.exists()
    ]
    arrow = pa.concat_tables(tables) if tables else schema.empty_table()
    frame = arrow.to_pandas()
    for column in CATEGORICAL.get(table, ()):
        frame[column] = frame[column].astype("category")
    return frame


# --- the store ------------------------------------------------------------------


@dataclass(frozen=True)
class RunChange:
    """Where the ledger reading changes from one batch to the next of the
    same polarity.

    Batches of two polarities can interleave, and a formula key is read in
    one polarity only (its mechanism carries the charge), so the batches are
    followed per polarity.

    :ivar at: The first sample time of the batch that carries the new one.
    :ivar sample_batch_id: That batch.
    :ivar polarity: The batches' polarity, as the batch listing gives it.
    :ivar before: The reading of the ledger batch before it.
    :ivar after: Its own.
    """

    at: pd.Timestamp
    sample_batch_id: str
    polarity: str | None
    before: str
    after: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "at": _iso(self.at),
            "sample_batch_id": self.sample_batch_id,
            "polarity": self.polarity,
            "before": self.before,
            "after": self.after,
        }


@dataclass(frozen=True)
class StoreStatus:
    """What a store holds, how old it is, and which runs read it.

    :ivar path: The store's directory.
    :ivar exists: Whether anything was ever synced there.
    :ivar key: The resolved scope's key.
    :ivar scope: The resolved scope, as :meth:`Scope.content` gives it.
    :ivar server: The server's URL and workspace.
    :ivar synced_utc: When the last sync that ran to its end finished; None
        if none has.
    :ivar sync_started_utc: When the last sync started, whether it ran to
        its end or was stopped.
    :ivar age: How long ago the last sync finished.
    :ivar batches: One row per batch held, oldest first:
        ``sample_batch_id``, ``sample_batch_name``, ``polarity``,
        ``n_samples``, ``first_datetime_utc``, ``last_datetime_utc``,
        ``sample_batch_utc_modified`` (as the server last listed it),
        ``synced_utc`` (when its tables were last fetched), ``age``,
        ``state`` (``synced``; ``failed`` when its last fetch failed and
        nothing is held for it; ``stale`` when that fetch failed and an
        earlier one's tables are held; ``unchecked`` when the last sync
        could not list its ledger runs, so the tables an earlier fetch left
        may or may not be current), ``error``, ``run_id``, ``reading``
        (:func:`reading_of` its run; None without a ledger),
        ``run_in_flight`` (a batch run was rewriting its ledger when its
        tables were fetched, so its batch-peak series can mix two runs; the
        sync after the run ends fetches it again), the rows it holds per
        table (``n_ion_series``, ``n_peak_series``), and ``n_shared_keys``,
        the formula keys two or more of its batch peaks carry (a sample can
        then hold two values of one key).
    :ivar runs: One row per reading the batches carry: ``reading``,
        ``engine``, ``engine_version``, ``n_batches``, ``first_datetime_utc``
        and ``last_datetime_utc``.
    :ivar run_changes: Where the reading changes from one ledger batch to
        the next of the same polarity, in time order.
    :ivar rows: Rows per table.
    :ivar n_files: Files acquired in the range.
    :ivar n_unfiled: Those of them no batch of the scope holds.
    :ivar size_bytes: The store's size on disk.
    :ivar provenance: The provenance block of the newest fetch.
    :ivar pending_rollups: The UTC days whose rollups the last sync left
        uncomputed (it was interrupted); the next sync computes them.
    """

    path: Path
    exists: bool
    key: str | None = None
    scope: dict[str, Any] = field(default_factory=dict)
    server: dict[str, Any] = field(default_factory=dict)
    synced_utc: pd.Timestamp | None = None
    sync_started_utc: pd.Timestamp | None = None
    age: pd.Timedelta | None = None
    batches: pd.DataFrame = field(default_factory=pd.DataFrame)
    runs: pd.DataFrame = field(default_factory=pd.DataFrame)
    run_changes: tuple[RunChange, ...] = ()
    rows: dict[str, int] = field(default_factory=dict)
    n_files: int = 0
    n_unfiled: int = 0
    size_bytes: int = 0
    provenance: dict[str, Any] | None = None
    pending_rollups: tuple[str, ...] = ()

    @property
    def mixed_runs(self) -> bool:
        """Whether the batches carry more than one reading: a formula's
        series then joins readings of different runs, and a chart says so."""
        return len(self.runs) > 1

    @property
    def failed(self) -> dict[str, str]:
        """The batches the last sync could not bring up to date, by ID, with
        the error: their fetch failed, or their runs listing did."""
        if self.batches.empty:
            return {}
        failed = self.batches[self.batches["state"] != "synced"]
        return dict(zip(failed["sample_batch_id"], failed["error"]))

    @property
    def complete(self) -> bool:
        """Whether the last sync ran to its end and every batch of it is
        held as that sync fetched or found it."""
        finished = (
            self.synced_utc is not None
            and self.sync_started_utc is not None
            and self.synced_utc >= self.sync_started_utc
        )
        return self.exists and finished and not self.failed and not self.pending_rollups

    def to_dict(self) -> dict[str, Any]:
        """The status as plain JSON values."""

        def records(frame: pd.DataFrame) -> list[dict[str, Any]]:
            out = []
            for row in frame.to_dict("records"):
                out.append(
                    {
                        name: (
                            _iso(value)
                            if isinstance(value, pd.Timestamp)
                            else value.total_seconds()
                            if isinstance(value, pd.Timedelta)
                            else None
                            if value is None
                            or (isinstance(value, float) and np.isnan(value))
                            or value is pd.NaT
                            else value.item()
                            if isinstance(value, np.generic)
                            else value
                        )
                        for name, value in row.items()
                    }
                )
            return out

        return {
            "path": str(self.path),
            "exists": self.exists,
            "key": self.key,
            "scope": self.scope,
            "server": self.server,
            "synced_utc": _iso(self.synced_utc),
            "sync_started_utc": _iso(self.sync_started_utc),
            "age_seconds": None if self.age is None else self.age.total_seconds(),
            "complete": self.complete,
            "failed": self.failed,
            "mixed_runs": self.mixed_runs,
            "runs": records(self.runs),
            "run_changes": [change.to_dict() for change in self.run_changes],
            "rows": self.rows,
            "n_files": self.n_files,
            "n_unfiled": self.n_unfiled,
            "size_bytes": self.size_bytes,
            "provenance": self.provenance,
            "pending_rollups": list(self.pending_rollups),
            "batches": records(self.batches),
        }


class CampaignStore:
    """A scope's store on disk: its tables, and what it holds.

    Open one with the directory :func:`store_path` names, or take the one
    :func:`sync` returns.

    :ivar path: The store's directory.
    """

    def __init__(self, path: Path | str):
        self.path = Path(path)

    def __repr__(self) -> str:
        return f"CampaignStore({str(self.path)!r})"

    @property
    def meta_path(self) -> Path:
        return self.path / "store.json"

    @property
    def meta(self) -> dict[str, Any]:
        """``store.json`` as it is on disk; empty for a store never synced."""
        try:
            return json.loads(self.meta_path.read_text())
        except FileNotFoundError:
            return {}

    def exists(self) -> bool:
        return self.meta_path.exists()

    def batch_ids(self) -> list[str]:
        """The batches the store holds tables for, oldest first."""
        stamps = self.meta.get("batches", {})
        held = [b for b, stamp in stamps.items() if stamp.get("synced_utc")]
        return sorted(held, key=lambda b: (stamps[b]["first_datetime_utc"] or "", b))

    def _paths(self, table: str, batches: Iterable[str] | None) -> list[Path]:
        if table == "files":
            return [self.path / "files.parquet"]
        if table == "rollups":
            return sorted((self.path / "rollups").glob("*.parquet"))
        ids = self.batch_ids() if batches is None else list(batches)
        return [self.path / table / _file_name(b) for b in ids]

    def table(
        self,
        name: str,
        *,
        batches: Iterable[str] | None = None,
        keys: Iterable[str] | None = None,
    ) -> pd.DataFrame:
        """One of the store's tables, every batch's rows together.

        :param name: One of :data:`TABLES`.
        :param batches: Only these batches' rows (not for ``files`` and
            ``rollups``, which are not kept per batch).
        :param keys: Only these series keys' rows (for the tables that
            have them).
        :return: The rows, with the store's columns and types; empty, with
            them, when there are none. ``attrs["provenance"]`` is the block
            the store's newest fetch carried.
        """
        if name not in TABLES:
            raise ValueError(f"No table {name!r}: the tables are {', '.join(TABLES)}")
        if batches is not None and name not in BATCH_TABLES:
            raise ValueError(f"Table {name!r} is not kept per batch")
        filters = None
        if keys is not None:
            if "series_key" not in SCHEMAS[name].names:
                raise ValueError(f"Table {name!r} has no series keys")
            filters = [("series_key", "in", list(keys))]
        frame = _read(self._paths(name, batches), name, filters)
        if name == "species":
            # A key read in several batches: its newest batch's row
            frame = frame.drop_duplicates("series_key", keep="last", ignore_index=True)
        provenance = self.meta.get("provenance")
        if provenance:
            frame.attrs["provenance"] = provenance
        return frame

    def status(self) -> StoreStatus:
        """What the store holds, how old it is, and which runs read it.

        Read from the disk alone: what the server holds now is what the
        next :func:`sync` finds out.
        """
        meta = self.meta
        if not meta:
            return StoreStatus(path=self.path, exists=False)
        now = _now()
        stamps = meta.get("batches", {})
        rows = []
        for batch_id, stamp in stamps.items():
            run = stamp.get("run") or {}
            synced = _moment(stamp.get("synced_utc"))
            counts = stamp.get("rows") or {}
            if stamp.get("error"):
                state = "stale" if synced is not None else "failed"
            elif stamp.get("unchecked"):
                state = "unchecked"
            else:
                state = "synced"
            rows.append(
                {
                    "sample_batch_id": batch_id,
                    "sample_batch_name": stamp.get("sample_batch_name"),
                    "polarity": stamp.get("polarity"),
                    "n_samples": stamp.get("n_samples"),
                    "first_datetime_utc": _moment(stamp.get("first_datetime_utc")),
                    "last_datetime_utc": _moment(stamp.get("last_datetime_utc")),
                    "sample_batch_utc_modified": _moment(
                        stamp.get("sample_batch_utc_modified")
                    ),
                    "synced_utc": synced,
                    "age": None if synced is None else now - synced,
                    "state": state,
                    "error": stamp.get("error") or stamp.get("unchecked"),
                    "run_id": run.get("run_id"),
                    "reading": stamp.get("reading"),
                    "run_in_flight": (stamp.get("newest_run") or {}).get("status")
                    == "running",
                    "engine": run.get("engine"),
                    "engine_version": run.get("engine_version"),
                    "n_ion_series": counts.get("ion_series", 0),
                    "n_peak_series": counts.get("peak_series", 0),
                    "n_shared_keys": stamp.get("shared_keys", 0),
                }
            )
        batches = pd.DataFrame(rows, columns=_STATUS_BATCH_COLUMNS)
        if not batches.empty:
            batches = batches.sort_values(
                ["first_datetime_utc", "sample_batch_id"], kind="stable"
            ).reset_index(drop=True)

        ledger = batches[batches["reading"].notna()]
        runs = (
            ledger.groupby("reading", sort=False)
            .agg(
                engine=("engine", "first"),
                engine_version=("engine_version", "first"),
                n_batches=("sample_batch_id", "size"),
                first_datetime_utc=("first_datetime_utc", "min"),
                last_datetime_utc=("last_datetime_utc", "max"),
            )
            .reset_index()
            .sort_values("first_datetime_utc", kind="stable")
            .reset_index(drop=True)
            if not ledger.empty
            else pd.DataFrame(columns=_STATUS_RUN_COLUMNS)
        )
        changes = []
        previous: dict[Any, str] = {}
        for row in ledger.itertuples():
            before = previous.get(row.polarity)
            if before is not None and row.reading != before:
                changes.append(
                    RunChange(
                        at=row.first_datetime_utc,
                        sample_batch_id=row.sample_batch_id,
                        polarity=row.polarity,
                        before=before,
                        after=row.reading,
                    )
                )
            previous[row.polarity] = row.reading

        held = self.batch_ids()
        counted = {
            table: sum(
                (stamps[b].get("rows") or {}).get(table, 0)
                for b in held
                if stamps[b].get("synced_utc")
            )
            for table in BATCH_TABLES
        }
        counted["files"] = meta.get("files", {}).get("rows", 0)
        counted["rollups"] = sum(
            pq.ParquetFile(path).metadata.num_rows
            for path in self._paths("rollups", None)
        )
        synced = _moment(meta.get("synced_utc"))
        return StoreStatus(
            path=self.path,
            exists=True,
            key=meta.get("key"),
            scope=meta.get("scope", {}),
            server=meta.get("server", {}),
            synced_utc=synced,
            sync_started_utc=_moment(meta.get("sync_started_utc")),
            age=None if synced is None else now - synced,
            batches=batches,
            runs=runs,
            run_changes=tuple(changes),
            rows=counted,
            n_files=meta.get("files", {}).get("rows", 0),
            n_unfiled=meta.get("files", {}).get("unfiled", 0),
            size_bytes=sum(
                path.stat().st_size for path in self.path.rglob("*") if path.is_file()
            ),
            provenance=meta.get("provenance"),
            pending_rollups=tuple(meta.get("pending_rollups", [])),
        )

    def remove(self) -> None:
        """Delete the store: its directory and everything in it."""
        shutil.rmtree(self.path, ignore_errors=True)


_STATUS_BATCH_COLUMNS = [
    "sample_batch_id",
    "sample_batch_name",
    "polarity",
    "n_samples",
    "first_datetime_utc",
    "last_datetime_utc",
    "sample_batch_utc_modified",
    "synced_utc",
    "age",
    "state",
    "error",
    "run_id",
    "reading",
    "run_in_flight",
    "engine",
    "engine_version",
    "n_ion_series",
    "n_peak_series",
    "n_shared_keys",
]
_STATUS_RUN_COLUMNS = [
    "reading",
    "engine",
    "engine_version",
    "n_batches",
    "first_datetime_utc",
    "last_datetime_utc",
]


# --- one batch -------------------------------------------------------------------


def _sample_table(samples: pd.DataFrame) -> pd.DataFrame:
    """The ``samples`` table of a batch's listed samples."""
    frame = samples.copy()
    for column in SCHEMAS["samples"].names:
        if column not in frame:
            frame[column] = None
    start = pd.to_datetime(frame["datetime_utc"], utc=True)
    t0 = pd.to_numeric(frame["t0"], errors="coerce")
    t1 = pd.to_numeric(frame["t1"], errors="coerce")
    middle = pd.to_timedelta((t0 + t1) / 2, unit="s")
    # A sample with no window is placed at its file's start
    frame["t_mid_utc"] = (start + middle).fillna(start)
    frame["datetime_utc"] = start
    for column in ("t0", "t1", "tic"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return _frame(frame, "samples")


def _ion_tables(
    ions: pd.DataFrame, sample_ids: set[str]
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """``ion_series`` and the ions' ``species`` rows, from
    ``batches.ion_series``."""
    ions = ions[ions["sample_item_id"].isin(sample_ids)]
    keys = ions["target_ion_id"].map(ion_key)
    intensity = ions["intensity"].astype("float64")
    # A record without a match is no reading, not a zero one
    intensity = intensity.where(ions["match_category"] > 0)
    series = pd.DataFrame(
        {
            "sample_item_id": ions["sample_item_id"].astype(str),
            "series_key": keys,
            "intensity": intensity,
            "match_category": ions["match_category"].astype("int64"),
        }
    )
    species = [
        _species_row(
            ion_key(row.target_ion_id),
            row.target_compound_name,
            row.target_compound_formula,
            row.ion_formula,
            row.ionization_mechanism,
        )
        for row in ions.drop_duplicates("target_ion_id").itertuples()
    ]
    return series, species


def _peak_tables(
    points: pd.DataFrame,
    anchors: pd.DataFrame | None,
    mechanisms: dict[str, str],
    sample_ids: set[str],
    run_id: str | None,
) -> tuple[pd.DataFrame, list[dict[str, Any]], int]:
    """``peak_series`` and the formulas' ``species`` rows, from
    ``batch_peaks.series`` and ``batch_peaks.list``.

    Kept are the batch peaks with a consensus formula and mechanism, and of
    those not the isotopologues, whose formula is their parent's: the parent
    carries the series. Also returns how many keys more than one of the
    kept batch peaks carries.
    """
    if anchors is None or anchors.empty or points.empty:
        return _frame([], "peak_series"), [], 0
    anchors = anchors.copy()
    if "isotopologue_of" not in anchors:
        anchors["isotopologue_of"] = None
    anchors["mechanism"] = anchors["ionization_mechanism_id"].map(mechanisms)
    anchors = anchors[
        anchors["consensus_formula"].map(lambda f: isinstance(f, str) and bool(f))
        & anchors["mechanism"].notna()
        & anchors["isotopologue_of"].isna()
    ]
    anchors["series_key"] = [
        formula_key(f, m)
        for f, m in zip(anchors["consensus_formula"], anchors["mechanism"])
    ]
    key_of = dict(zip(anchors["batch_peak_id"], anchors["series_key"]))
    points = points[
        points["batch_peak_id"].isin(key_of.keys())
        & points["sample_item_id"].isin(sample_ids)
    ]
    series = pd.DataFrame(
        {
            "sample_item_id": points["sample_item_id"].astype(str),
            "series_key": points["batch_peak_id"].map(key_of),
            "intensity": points["intensity"].astype("float64"),
            "tier": points["tier"],
            "batch_peak_id": points["batch_peak_id"].astype(str),
            "run_id": run_id,
        }
    )
    kept = anchors[anchors["batch_peak_id"].isin(set(points["batch_peak_id"]))]
    shared = int((kept["series_key"].value_counts() > 1).sum())
    species = [
        _species_row(
            key,
            group["consensus_formula"].iloc[0],
            group["consensus_formula"].iloc[0],
            group["consensus_ion_formula"].iloc[0],
            group["mechanism"].iloc[0],
            float(group["mz"].median()) if "mz" in group else np.nan,
        )
        for key, group in kept.groupby("series_key", sort=True)
    ]
    return series, species, shared


def _run_record(row: pd.Series, names: Sequence[str]) -> dict[str, Any]:
    """*names* of a runs listing's row, as a stamp records them."""

    def value(name: str) -> Any:
        found = row.get(name)
        return (
            None if found is None or (np.isscalar(found) and pd.isna(found)) else found
        )

    return {
        "run_id" if name == "batch_peak_run_id" else name: value(name) for name in names
    }


def _ledger_runs(
    client: "MascopeClient", batch_id: str
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """The batch's current ledger run, and its newest run; None for each
    when it has no ledger.

    The two differ while a batch run (a rebuild, a search, an import) is in
    flight, and after one failed: the current run's ID then reads the live
    ledger as that run left it, which is no longer what the current run
    made.
    """
    try:
        runs = client.batch_peaks.runs(batch_id)
    except NotFoundError:
        # A server with no batch ledger at all
        return None, None
    if runs is None or runs.empty:
        return None, None
    # Newest first, as the route lists them
    newest = _run_record(runs.iloc[0], ("batch_peak_run_id", "action", "status"))
    current = runs[runs["current"].astype(bool)] if "current" in runs else runs
    names = ("batch_peak_run_id", "action", "engine", "engine_version", "status")
    run = None if current.empty else _run_record(current.iloc[0], (*names, "config"))
    return run, newest


def _why_fetch(
    stamp: dict[str, Any] | None,
    batch: Any,
    run: dict[str, Any] | None,
    newest: dict[str, Any] | None,
    now: pd.Timestamp,
) -> str | None:
    """Why a batch's tables are fetched again; None when they are not."""
    if stamp is None:
        return "new"
    if stamp.get("error"):
        return "failed before"
    if not stamp.get("synced_utc"):
        return "new"
    if stamp.get("sample_batch_utc_modified") != _iso(batch.sample_batch_utc_modified):
        return "modified"
    if stamp.get("n_samples") != int(batch.n_samples):
        return "sample count"
    if (stamp.get("run") or {}).get("run_id") != (run or {}).get("run_id"):
        return "ledger run"
    if stamp.get("newest_run") != newest:
        return "batch run"
    if batch.last_datetime_utc >= now - LIVE:
        return "today"
    return None


def _days(first: str | None, last: str | None) -> list[str]:
    """The UTC days from *first* to *last*, as ``YYYY-MM-DD``."""
    if first is None or last is None:
        return []
    days = pd.date_range(
        pd.Timestamp(first).floor("1D"), pd.Timestamp(last).floor("1D"), freq="1D"
    )
    return [f"{day:%Y-%m-%d}" for day in days]


# --- rollups ---------------------------------------------------------------------


def rollup(values: pd.DataFrame) -> pd.DataFrame:
    """The ``rollups`` rows of series values with their times.

    :param values: ``t_mid_utc``, ``series_key``, ``reading`` and
        ``intensity``, one row per value.
    :return: Per bin of :data:`BINS` (``bin`` its name, ``t`` its start in
        UTC), series key and reading: the median, mean, 10th and 90th
        percentiles (linear interpolation, as pandas' ``quantile``) and
        count ``n`` of the values that are not NaN. A bin where a key has
        none is no row.
    """
    values = values[values["intensity"].notna()]
    frames = []
    for offset, name in BINS.items():
        if values.empty:
            break
        grouped = values.assign(t=values["t_mid_utc"].dt.floor(offset)).groupby(
            ["t", "series_key", "reading"], dropna=False, observed=True, sort=True
        )["intensity"]
        stats = grouped.agg(median="median", mean="mean", n="size")
        for column, q in PERCENTILES.items():
            stats[column] = grouped.quantile(q)
        frames.append(stats.reset_index().assign(bin=name))
    if not frames:
        return _frame([], "rollups")
    frame = pd.concat(frames, ignore_index=True)
    frame["reading"] = frame["reading"].astype(object).where(frame["reading"].notna())
    return _frame(frame, "rollups")


def _batch_values(
    store: CampaignStore, batch_id: str, reading: str | None
) -> pd.DataFrame:
    """A batch's series values with their sample times, as :func:`rollup`
    takes them."""
    samples = _read([store.path / "samples" / _file_name(batch_id)], "samples")
    when = pd.Series(
        samples["t_mid_utc"].to_numpy(), index=samples["sample_item_id"].astype(object)
    )
    frames = []
    for table, of_table in (("ion_series", None), ("peak_series", reading)):
        rows = _read([store.path / table / _file_name(batch_id)], table)
        frames.append(
            pd.DataFrame(
                {
                    "t_mid_utc": rows["sample_item_id"].astype(object).map(when),
                    "series_key": rows["series_key"].astype(object),
                    "reading": of_table,
                    "intensity": rows["intensity"],
                }
            )
        )
    values = pd.concat(frames, ignore_index=True)
    return values[values["t_mid_utc"].notna()]


def _roll_days(
    store: CampaignStore, stamps: dict[str, Any], days: Iterable[str]
) -> Iterator[str]:
    """Compute the rollups of each of *days* (UTC, ``YYYY-MM-DD``) from the
    raw rows the store holds, yielding each day once it is written.

    A day takes the rows of every batch with a sample in it; a batch's are
    read once for the days in a row that it spans.
    """
    held: dict[str, pd.DataFrame] = {}
    for day in sorted(days):
        lo = pd.Timestamp(day, tz="UTC")
        hi = lo + pd.Timedelta(days=1)
        spanning = [
            batch_id
            for batch_id, stamp in stamps.items()
            if stamp.get("synced_utc")
            and stamp.get("first_t_mid_utc") is not None
            and pd.Timestamp(stamp["first_t_mid_utc"]) < hi
            and pd.Timestamp(stamp["last_t_mid_utc"]) >= lo
        ]
        held = {
            b: held[b]
            if b in held
            else _batch_values(store, b, stamps[b].get("reading"))
            for b in spanning
        }
        values = [
            frame[(frame["t_mid_utc"] >= lo) & (frame["t_mid_utc"] < hi)]
            for frame in held.values()
        ]
        frame = rollup(pd.concat(values, ignore_index=True)) if values else None
        path = store.path / "rollups" / f"{day}.parquet"
        if frame is None or frame.empty:
            path.unlink(missing_ok=True)
        else:
            _write(path, frame, "rollups", {"day": day, "bins": "UTC"})
        yield day


# --- sync ------------------------------------------------------------------------


@dataclass(frozen=True)
class SyncReport:
    """What a sync did.

    :ivar resolved: The scope as it resolved.
    :ivar store: Its store; None when the scope was ambiguous and nothing
        was synced (see ``resolved.candidates``).
    :ivar fetched: The batches fetched, by ID, with why: ``new``,
        ``modified``, ``sample count``, ``ledger run`` (its current run
        changed), ``batch run`` (a batch run started or ended since, however
        it ended: its newest run or that run's status changed), ``today`` (it
        holds a sample from the last day), ``failed before``.
    :ivar unchanged: The batches left as they were.
    :ivar failed: The batches whose fetch or runs listing failed, by ID,
        with the error; they are skipped, and tried again by the next sync.
    :ivar removed: The batches the scope no longer holds, whose tables were
        deleted.
    :ivar seconds: How long the sync took.
    """

    resolved: ResolvedScope
    store: CampaignStore | None
    fetched: dict[str, str] = field(default_factory=dict)
    unchanged: tuple[str, ...] = ()
    failed: dict[str, str] = field(default_factory=dict)
    removed: tuple[str, ...] = ()
    seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """The report as plain JSON values."""
        return {
            "key": self.resolved.key,
            "ambiguous": self.resolved.ambiguous,
            "candidates": {
                name: [candidate.to_dict() for candidate in found]
                for name, found in self.resolved.candidates.items()
            },
            "store": None if self.store is None else str(self.store.path),
            "fetched": self.fetched,
            "unchanged": list(self.unchanged),
            "failed": self.failed,
            "removed": list(self.removed),
            "seconds": self.seconds,
        }


def _fetch_batch(
    client: "MascopeClient",
    resolved: ResolvedScope,
    batch: Any,
    run: dict[str, Any] | None,
    newest: dict[str, Any] | None,
    mechanisms: dict[str, str],
) -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    """One batch's tables, and what its stamp records of them."""
    batch_id, name = batch.sample_batch_id, batch.sample_batch_name
    listed = resolved.samples[resolved.samples["sample_batch_id"] == batch_id]
    samples = _sample_table(listed)
    sample_ids = set(samples["sample_item_id"])

    provenance = None
    with sdk_errors(f"read the ion series of batch {name!r}"):
        ions = client.batches.ion_series(batch_id, collection=resolved.collection)
    provenance = ions.attrs.get("provenance") or provenance
    ion_series, species = _ion_tables(ions, sample_ids)

    peak_series, shared = _frame([], "peak_series"), 0
    if run is not None:
        with sdk_errors(f"read the batch-peak series of batch {name!r}"):
            points = client.batch_peaks.series(batch_id, run_id=run["run_id"])
            anchors = client.batch_peaks.list(
                batch_id, min_n_present=2, run_id=run["run_id"]
            )
        provenance = points.attrs.get("provenance") or provenance
        peak_series, formulas, shared = _peak_tables(
            points, anchors, mechanisms, sample_ids, run["run_id"]
        )
        species += formulas

    tables = {
        "samples": samples,
        "ion_series": ion_series,
        "peak_series": peak_series,
        "species": _frame(species, "species"),
    }
    times = samples["t_mid_utc"].dropna()
    stamp = {
        "sample_batch_name": name,
        "dataset_id": batch.dataset_id,
        "polarity": batch.polarity,
        "n_samples": int(batch.n_samples),
        "sample_batch_utc_modified": _iso(batch.sample_batch_utc_modified),
        "first_datetime_utc": _iso(batch.first_datetime_utc),
        "last_datetime_utc": _iso(batch.last_datetime_utc),
        "first_t_mid_utc": _iso(times.min()) if not times.empty else None,
        "last_t_mid_utc": _iso(times.max()) if not times.empty else None,
        "run": run,
        "newest_run": newest,
        "reading": reading_of(run),
        "shared_keys": shared,
        "provenance": provenance,
        "rows": {table: len(frame) for table, frame in tables.items()},
    }
    return tables, stamp


def sync(
    client: "MascopeClient",
    scope: Scope,
    *,
    pause: float = 0.5,
    root: Path | str | None = None,
) -> SyncReport:
    """Bring a scope's store up to date with the server.

    Resolves the scope afresh (the client's listing cache is cleared
    first), then goes through its batches oldest first, one at a time. A
    batch is fetched again only when the server's listing has it modified
    since, its sample count changed, its ledger's current run changed, a
    batch run started or ended on its ledger, it holds a sample acquired
    within the last day (the batch holding today), or its last fetch
    failed; a batch the scope no longer holds is deleted. A fetch reads the
    batch's ion series (one call per collection, or one for the scope's
    collection), and when it has a ledger its batch-peak series and batch
    peaks (one call each); the runs listing that names the current and the
    newest run is read for every batch, and is no series call.

    A batch whose fetch fails is recorded, with its error, and skipped:
    the store keeps what an earlier fetch gave for it, the status says so,
    and the next sync fetches it again. A batch whose runs listing fails is
    recorded and skipped too, but keeps its tables as they were: the status
    calls it ``unchecked``, and the next sync decides on the listing again.
    A refused token stops the sync.

    :param client: An SDK client; its workspace is the scope's.
    :param scope: What to sync.
    :param pause: Seconds to wait between two batches' fetches, so a long
        first sync does not read the server flat out.
    :param root: The directory stores live under; :func:`default_root`
        when None.
    :return: What was fetched, left, failed and removed, and the store.
    :raises NotFound: When a name of the scope matches nothing.
    :raises NotAuthenticated: When the server refuses the token.
    :raises Unavailable: When the scope cannot be resolved.
    """
    started = time.monotonic()
    client.clear_cache()
    resolved = resolve(client, scope)
    if resolved.ambiguous:
        return SyncReport(
            resolved=resolved, store=None, seconds=time.monotonic() - started
        )

    store = CampaignStore(store_path(client, resolved, root))
    meta = store.meta
    if meta.get("format") != FORMAT or meta.get("key") != resolved.key:
        if store.path.exists():
            store.remove()
        meta = {}
    stamps: dict[str, dict[str, Any]] = meta.get("batches", {})
    pending = set(meta.get("pending_rollups", []))
    now = _now()
    meta.update(
        format=FORMAT,
        key=resolved.key,
        scope=resolved.scope.content(),
        server={
            "url": client.url,
            "workspace_id": client.workspace_id,
            "workspace_name": getattr(client, "workspace_name", None),
        },
        collection=resolved.collection,
        timezone=resolved.timezone,
        display_tz=resolved.display_tz,
        sync_started_utc=_iso(now),
        batches=stamps,
    )

    def save() -> None:
        meta["pending_rollups"] = sorted(pending)
        _write_json(store.meta_path, meta)

    # Before anything is written: a sync stopped from here on is no
    # complete one
    save()

    files = resolved.files.copy()
    _write(
        store.path / "files.parquet",
        files,
        "files",
        {"start_utc": _iso(resolved.start_utc), "end_utc": _iso(resolved.end_utc)},
    )
    meta["files"] = {
        "rows": len(files),
        "unfiled": int((~files["in_batch"].astype(bool)).sum()),
    }

    in_scope = set(resolved.batches["sample_batch_id"])
    removed = tuple(sorted(b for b in stamps if b not in in_scope))
    for batch_id in removed:
        stamp = stamps.pop(batch_id)
        pending.update(_days(stamp.get("first_t_mid_utc"), stamp.get("last_t_mid_utc")))
        for table in BATCH_TABLES:
            (store.path / table / _file_name(batch_id)).unlink(missing_ok=True)
    save()

    mechanisms: dict[str, str] | None = None
    fetched: dict[str, str] = {}
    unchanged: list[str] = []
    failed: dict[str, str] = {}
    for batch in resolved.batches.itertuples():
        batch_id = batch.sample_batch_id
        stamp = stamps.get(batch_id)
        checked = False
        try:
            with sdk_errors(
                f"list the ledger runs of batch {batch.sample_batch_name!r}"
            ):
                run, newest = _ledger_runs(client, batch_id)
            checked = True
            why = _why_fetch(stamp, batch, run, newest, now)
            if why is None:
                unchanged.append(batch_id)
                # Listed again, and found as it was
                stamp.pop("unchecked", None)
                continue
            if fetched or failed:
                time.sleep(pause)
            fetched[batch_id] = why
            if run is not None and mechanisms is None:
                with sdk_errors("list the ionization mechanisms"):
                    listed = client.ionization.list()
                mechanisms = (
                    {}
                    if listed is None
                    else dict(
                        zip(
                            listed["ionization_mechanism_id"],
                            listed["ionization_mechanism"],
                        )
                    )
                )
            tables, fresh = _fetch_batch(
                client, resolved, batch, run, newest, mechanisms or {}
            )
        except NotAuthenticated:
            save()
            raise
        except Exception as e:
            message = (
                e.message if isinstance(e, VizError) else f"{type(e).__name__}: {e}"
            )
            failed[batch_id] = message
            fetched.pop(batch_id, None)
            stamp = dict(stamp or {})
            if not checked and stamp.get("synced_utc"):
                # Only its runs listing failed: nothing was fetched, so its
                # tables stay as the earlier fetch left them, and the next
                # sync decides on the listing again
                stamp.update(unchecked=message, attempted_utc=_iso(_now()))
            else:
                if not stamp.get("synced_utc"):
                    # Nothing held for it: the listing's word is all there is
                    stamp.update(
                        sample_batch_name=batch.sample_batch_name,
                        polarity=batch.polarity,
                        n_samples=int(batch.n_samples),
                        first_datetime_utc=_iso(batch.first_datetime_utc),
                        last_datetime_utc=_iso(batch.last_datetime_utc),
                    )
                stamp.update(error=message, attempted_utc=_iso(_now()))
            stamps[batch_id] = stamp
            save()
            continue

        about = {
            "sample_batch_id": batch_id,
            "run_id": (run or {}).get("run_id"),
            "provenance": fresh["provenance"],
        }
        for table, frame in tables.items():
            _write(store.path / table / _file_name(batch_id), frame, table, about)
        if stamp:
            pending.update(
                _days(stamp.get("first_t_mid_utc"), stamp.get("last_t_mid_utc"))
            )
        pending.update(_days(fresh["first_t_mid_utc"], fresh["last_t_mid_utc"]))
        fresh.update(synced_utc=_iso(_now()), error=None)
        stamps[batch_id] = fresh
        if fresh["provenance"]:
            meta["provenance"] = fresh["provenance"]
        save()

    for day in _roll_days(store, stamps, list(pending)):
        pending.discard(day)
        save()
    meta["synced_utc"] = _iso(_now())
    save()
    return SyncReport(
        resolved=resolved,
        store=store,
        fetched=fetched,
        unchanged=tuple(unchanged),
        failed=failed,
        removed=removed,
        seconds=time.monotonic() - started,
    )
