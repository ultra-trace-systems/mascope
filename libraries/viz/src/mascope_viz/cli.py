"""``mascope-viz``: the package's command line.

::

    mascope-viz scopes ...        what a scope covers: its batches, files
                                  and span, or the candidates of a name that
                                  matches several
    mascope-viz store sync ...    bring the scope's campaign store up to date
    mascope-viz store status ...  what the store holds and how old it is
    mascope-viz store rm ...      delete the store
    mascope-viz view NAME ...     a view of the store: its summary, and with
                                  --csv the frame written to a file

Every command takes the same scope options (``--dataset``, ``--batches``,
``--instrument``, ``--from``, ``--to``, ``--polarity``, ``--mode``,
``--collection``, ``--tz``) and prints a summary, as text or, with
``--json``, as one JSON object. Only summaries and paths are printed, never
a frame (``docs/dev/visual_digest.md``, section 2.7). The output is ASCII:
a name is printed as a JSON string, other characters escaped, so it reads
the same in any console and stays a value, never part of the command's own
sentences.

The server's URL and the token come from the environment or a ``.env``
file, as the SDK reads them (``MASCOPE_URL``, ``MASCOPE_ACCESS_TOKEN``), and
so does the workspace, ``MASCOPE_WORKSPACE``, when the token's user has more
than one. ``--server`` and ``--workspace`` override the URL and the
workspace; no option takes the token. A store lives in
its server's directory (:func:`~mascope_viz.data.store.store_path`), so
``--server`` selects that too.

The exit status is 0 when the command did what it was asked, 1 when it
failed (the error is printed, as JSON with ``--json``) or a sync left a
batch behind, and 2 for a usage error, such as an unknown option or view:
click's message on stderr, and no JSON even with ``--json``.
"""

import json
import math
import os
from collections.abc import Callable, Sequence
from functools import wraps
from pathlib import Path
from typing import Annotated, Any, NoReturn, Optional

import numpy as np
import pandas as pd
import typer
from dotenv import dotenv_values, find_dotenv

from mascope_sdk import MascopeClient
from mascope_sdk.exceptions import ConfigurationError

from .data import views
from .data.scope import INSTRUMENT_TZ, ResolvedScope, Scope, resolve
from .data.store import CampaignStore, StoreStatus, store_path, sync
from .errors import VizError, sdk_errors


#: The views ``view`` computes, as :mod:`~mascope_viz.data.views` names them.
VIEWS = ("series", "diurnal", "periods", "coverage", "composition", "spectrum", "top")

#: How many rows of a list (batches, keys, gaps) a command prints by default.
LIMIT = 50

#: The setting of the SDK's log level, and the level the command line runs
#: the SDK at when it is not set: its INFO lines, a few per call, would bury
#: the summary.
LOG_LEVEL = "MASCOPE_SDK_LOG_LEVEL"
QUIET = "WARNING"

#: The options each view takes besides the scope's, ``--csv``, ``--limit``
#: and ``--json``.
_TAKES = {
    "series": ("keys", "cadence", "normalise", "stat", "window"),
    "diurnal": ("keys", "by", "normalise", "window"),
    "periods": ("keys", "period", "normalise", "window"),
    "coverage": ("cadence", "min_gap", "window"),
    "composition": ("keys", "period", "by", "normalise", "window"),
    "spectrum": ("keys", "normalise", "window", "other"),
    "top": ("keys", "count", "by", "period", "normalise", "window"),
}

#: Each of those options as the command line spells it.
_SPELLED = {
    "keys": "--keys",
    "cadence": "--cadence",
    "normalise": "--normalise",
    "stat": "--stat",
    "by": "--by",
    "period": "--period",
    "count": "-n",
    "min_gap": "--min-gap",
    "window": "--window-from/--window-to",
    "other": "--other-from/--other-to",
}

#: The views that need ``--keys``.
_NEED_KEYS = ("series", "diurnal", "periods")


# --- failures ----------------------------------------------------------------


class NotConfigured(VizError):
    """No client: no URL or token, or a workspace that does not resolve."""

    code = "not_configured"


class Ambiguous(VizError):
    """A scope with a name that matches several objects: nothing was done."""

    code = "ambiguous"

    def __init__(self, resolved: ResolvedScope):
        self.candidates = resolved.candidates
        fields = ", ".join(
            f"{name} matches {len(found)}" for name, found in self.candidates.items()
        )
        super().__init__(
            f"The scope is ambiguous ({fields}): name one by its ID. "
            "`mascope-viz scopes` with the same options lists them."
        )

    def details(self) -> dict[str, Any]:
        return {"candidates": _candidates(self.candidates)}


class NotSynced(VizError):
    """A view of a scope whose store was never synced."""

    code = "not_synced"

    def __init__(self, path: Path):
        self.path = path
        super().__init__(
            f"No store of this scope at {path}: run `mascope-viz store sync` "
            "with the same scope options first. A time without a zone is read "
            "in --tz, so give the sync's --tz too."
        )

    def details(self) -> dict[str, Any]:
        return {"path": str(self.path)}


class InvalidArgument(VizError, ValueError):
    """An option a command cannot take as written."""

    code = "invalid_argument"


class NotRemoved(VizError):
    """A store whose delete left files behind."""

    code = "not_removed"

    def __init__(self, path: Path, n_files: int, size_bytes: int):
        self.path = path
        self.n_files = n_files
        self.size_bytes = size_bytes
        super().__init__(
            f"The store at {path} was not removed: {n_files:,} file(s), "
            f"{_size(size_bytes)}, are still there. A file another program "
            "holds open, or a directory without write permission, keeps them: "
            "close or fix that and run `mascope-viz store rm` again."
        )

    def details(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "n_files": self.n_files,
            "size_bytes": self.size_bytes,
        }


# --- output ------------------------------------------------------------------


def _plain(value: Any) -> Any:
    """*value* as plain JSON values: times as ISO strings, durations in
    seconds, NaN as None."""
    if isinstance(value, dict):
        return {str(name): _plain(item) for name, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_plain(item) for item in value]
    if value is None or value is pd.NaT:
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, pd.Timedelta):
        return value.total_seconds()
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def quoted(text: Any) -> str:
    """A name as the command line prints it: a JSON string, ASCII, with every
    other character escaped."""
    return json.dumps(str(text))


def _first_set(*values: Any, default: Any = "") -> Any:
    """The first of *values* that is set, else *default*: None, NaN and an
    empty string are not. A missing string in a frame is NaN, which is
    truthy, so ``value or default`` would print it as ``nan``."""
    for value in values:
        if value is None or (pd.api.types.is_scalar(value) and pd.isna(value)):
            continue
        if value != "":
            return value
    return default


def _ascii(text: str) -> str:
    return text.encode("ascii", "backslashreplace").decode("ascii")


def _number(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, (bool, np.bool_)):
        return "yes" if value else "no"
    if isinstance(value, (int, np.integer)):
        return f"{int(value):,}"
    value = float(value)
    return f"{value:.4g}" if math.isfinite(value) else "-"


def _when(moment: Any, zone: str | None = None) -> str:
    if moment is None or moment is pd.NaT or pd.isna(moment):
        return "-"
    moment = pd.Timestamp(moment)
    if zone is not None and moment.tzinfo is not None:
        moment = moment.tz_convert(zone)
    return moment.isoformat()


def _age(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    left, parts = int(seconds), []
    for unit, size in (("d", 86400), ("h", 3600), ("min", 60)):
        if left >= size:
            parts.append(f"{left // size} {unit}")
            left %= size
    return " ".join(parts[:2]) or f"{left} s"


def _size(n_bytes: int) -> str:
    if n_bytes < 1000:
        return f"{n_bytes} B"
    size, unit = float(n_bytes), "B"
    for unit in ("kB", "MB", "GB"):
        size /= 1000
        if size < 1000:
            break
    return f"{size:.1f} {unit}"


def _table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    """*rows* under *header* in aligned columns, two spaces in."""
    widths = [
        max(len(cell) for cell in column) for column in zip(header, *rows, strict=True)
    ]
    return [
        "  " + "  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip()
        for row in (header, *rows)
    ]


def _more(shown: int, total: int, what: str) -> list[str]:
    if total <= shown:
        return []
    return [f"  ... and {total - shown:,} more {what}; --limit 0 lists them all"]


def _limited(items: list[Any], limit: int) -> list[Any]:
    return items[:limit] if limit else items


def _print(payload: dict[str, Any], lines: list[str], as_json: bool) -> None:
    if as_json:
        typer.echo(json.dumps(_plain(payload), indent=1, allow_nan=False))
    else:
        for line in lines:
            typer.echo(_ascii(line))


def _fail(error: VizError, as_json: bool) -> NoReturn:
    if as_json:
        typer.echo(json.dumps(_plain(error.to_dict()), indent=1, allow_nan=False))
    else:
        typer.echo(_ascii(f"error: {error.message}"), err=True)
    raise typer.Exit(1)


def _command(function: Callable[..., None]) -> Callable[..., None]:
    """A command that prints its failures as the rest of its output."""

    @wraps(function)
    def run(*args: Any, **kwargs: Any) -> None:
        as_json = bool(kwargs.get("as_json"))
        try:
            function(*args, **kwargs)
        except VizError as e:
            _fail(e, as_json)

    return run


# --- the client and the scope ------------------------------------------------


def _setting(name: str) -> str | None:
    """*name* from the environment, else from the ``.env`` file the SDK
    would read."""
    if os.environ.get(name):
        return os.environ[name]
    dotenv = find_dotenv(usecwd=True)
    return (dotenv_values(dotenv).get(name) if dotenv else None) or None


def configured_workspace(given: str | None = None) -> str | None:
    """The workspace to open: *given*, else ``MASCOPE_WORKSPACE`` from the
    environment, else from the ``.env`` file the SDK would read."""
    return given or _setting("MASCOPE_WORKSPACE")


#: Where the command line reads a setting from, as the SDK does.
_READ_FROM = "in the environment or a .env file"


def _not_configured(error: ConfigurationError, server: str | None) -> NotConfigured:
    """The SDK's *error* as this command line's: which setting is missing,
    and where it takes it. The SDK's own message names its Python
    parameters."""
    if not (server or _setting("MASCOPE_URL")):
        return NotConfigured(
            f"Mascope URL not configured: set MASCOPE_URL {_READ_FROM}, or pass "
            "--server."
        )
    if not _setting("MASCOPE_ACCESS_TOKEN"):
        return NotConfigured(
            f"Mascope access token not configured: set MASCOPE_ACCESS_TOKEN "
            f"{_READ_FROM}. No option takes it: --server gives the URL alone."
        )
    # The workspace: the user has none, none of that name, or several
    return NotConfigured(
        f"{error} With mascope-viz, name it with --workspace, or "
        f"MASCOPE_WORKSPACE {_READ_FROM}."
    )


def connect(server: str | None = None, workspace: str | None = None) -> MascopeClient:
    """An SDK client, its credentials read as the SDK reads them.

    :param server: The server's URL, in place of ``MASCOPE_URL``.
    :param workspace: The workspace, by name or ID, in place of
        ``MASCOPE_WORKSPACE``.
    :raises NotConfigured: When the URL, the token or the workspace cannot
        be determined.
    """
    if _setting(LOG_LEVEL) is None:
        # The client sets its log level from this when it is made
        os.environ[LOG_LEVEL] = QUIET
    try:
        with sdk_errors("connect to Mascope"):
            return MascopeClient(url=server, workspace=configured_workspace(workspace))
    except ConfigurationError as e:
        raise _not_configured(e, server) from e


def _scope(
    dataset: str | None,
    batches: list[str] | None,
    instrument: str | None,
    start: str | None,
    end: str | None,
    polarity: str | None,
    mode: str | None,
    collection: str | None,
    tz: str | None,
) -> Scope:
    return Scope(
        dataset=dataset,
        batches=list(batches) if batches else None,
        instrument=instrument,
        start=start,
        end=end,
        polarity=polarity,
        mode=mode,
        collection=collection,
        tz=tz or INSTRUMENT_TZ,
    )


def _resolved(client: MascopeClient, scope: Scope) -> ResolvedScope:
    resolved = resolve(client, scope)
    if resolved.ambiguous:
        raise Ambiguous(resolved)
    return resolved


def _candidates(candidates: dict[str, Sequence[Any]]) -> dict[str, list[Any]]:
    return {
        name: [candidate.to_dict() for candidate in found]
        for name, found in candidates.items()
    }


# --- the options -------------------------------------------------------------

DatasetOption = Annotated[
    Optional[str],
    typer.Option(
        "--dataset", help="The dataset: its name, part of the name, or its ID."
    ),
]
BatchesOption = Annotated[
    Optional[list[str]],
    typer.Option(
        "--batches",
        "--batch",
        help="A batch, by name, part of one or ID; repeat it for several. "
        "Looked for in --dataset when given.",
    ),
]
InstrumentOption = Annotated[
    Optional[str],
    typer.Option("--instrument", help="Only this instrument's samples and files."),
]
FromOption = Annotated[
    Optional[str],
    typer.Option(
        "--from",
        help="Only what was acquired at this time or later: ISO 8601, a time "
        "without a zone in --tz.",
    ),
]
ToOption = Annotated[
    Optional[str],
    typer.Option("--to", help="Only what was acquired before this time."),
]
PolarityOption = Annotated[
    Optional[str],
    typer.Option("--polarity", help="+ or - (positive, negative): only that one."),
]
ModeOption = Annotated[
    Optional[str],
    typer.Option("--mode", help="Only the samples of this ionization mode."),
]
CollectionOption = Annotated[
    Optional[str],
    typer.Option(
        "--collection",
        help="The target collection whose ions are read, among the batches'.",
    ),
]
TzOption = Annotated[
    Optional[str],
    typer.Option(
        "--tz",
        help="The zone times are shown in, and a time without a zone is in: "
        "an IANA name such as UTC, or 'instrument' for the instrument's "
        "acquisition zone (the default).",
    ),
]
ServerOption = Annotated[
    Optional[str],
    typer.Option(
        "--server",
        help="The server's URL (default: MASCOPE_URL). Stores are kept per "
        "server, so it also selects the store directory.",
    ),
]
WorkspaceOption = Annotated[
    Optional[str],
    typer.Option(
        "--workspace", help="The workspace, by name or ID (default: MASCOPE_WORKSPACE)."
    ),
]
JsonOption = Annotated[bool, typer.Option("--json", help="Print one JSON object.")]
LimitOption = Annotated[
    int,
    typer.Option(
        "--limit", min=0, help="How many rows of each list to print; 0 for all."
    ),
]


app = typer.Typer(
    name="mascope-viz",
    help="Visual digests of Mascope data: the scope, its campaign store and "
    "its views. Only summaries and paths are printed, never a frame.",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode=None,
    pretty_exceptions_enable=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)
store_app = typer.Typer(
    help="A scope's campaign store: a local parquet cache of its data.",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode=None,
    pretty_exceptions_enable=False,
)
app.add_typer(store_app, name="store")


# --- scopes ------------------------------------------------------------------


def _scope_report(
    client: MascopeClient, resolved: ResolvedScope, limit: int
) -> tuple[dict[str, Any], list[str]]:
    if resolved.ambiguous:
        payload = {
            "ambiguous": True,
            "candidates": _candidates(resolved.candidates),
            "key": None,
        }
        lines = ["The scope is ambiguous: name one of these by its ID."]
        for name, found in resolved.candidates.items():
            lines.append(f"{name} matches {len(found)}:")
            rows = [
                [
                    candidate.id,
                    quoted(candidate.name),
                    f"in {candidate.parent}" if candidate.parent else "",
                ]
                for candidate in found
            ]
            lines += _table(["ID", "NAME", ""], rows)
        return payload, lines

    zone = resolved.display_tz
    batches, samples, files = resolved.batches, resolved.samples, resolved.files
    unfiled = int((~files["in_batch"].astype(bool)).sum())
    instruments = sorted(
        {str(name) for name in files["instrument"].dropna()}
        | {str(name) for name in samples["instrument"].dropna()}
    )
    store = CampaignStore(store_path(client, resolved))
    first = samples["datetime_utc"].min() if not samples.empty else None
    last = samples["datetime_utc"].max() if not samples.empty else None
    first_file = files["datetime_utc"].min() if not files.empty else None
    last_file = files["datetime_utc"].max() if not files.empty else None
    batch_rows = [
        {
            "sample_batch_id": row.sample_batch_id,
            "sample_batch_name": row.sample_batch_name,
            "dataset_id": row.dataset_id,
            "dataset_name": row.dataset_name,
            "polarity": row.polarity,
            "n_samples": int(row.n_samples),
            "first_datetime_utc": row.first_datetime_utc,
            "last_datetime_utc": row.last_datetime_utc,
            "sample_batch_utc_modified": row.sample_batch_utc_modified,
        }
        for row in batches.itertuples()
    ]
    collection_rows = [
        {
            "target_collection_id": row.target_collection_id,
            "target_collection_name": row.target_collection_name,
            "target_collection_type": row.target_collection_type,
            "n_batches": len(row.sample_batch_ids),
        }
        for row in resolved.collections.itertuples()
    ]
    payload = {
        "ambiguous": False,
        "candidates": {},
        "key": resolved.key,
        "scope": resolved.scope.content(),
        "server": {
            "url": client.url,
            "workspace_id": client.workspace_id,
            "workspace_name": getattr(client, "workspace_name", None),
        },
        "timezone": resolved.timezone,
        "display_tz": zone,
        "start_utc": resolved.start_utc,
        "end_utc": resolved.end_utc,
        "collection": resolved.collection,
        "instruments": instruments,
        "n_batches": len(batches),
        "n_samples": len(samples),
        "first_sample_utc": first,
        "last_sample_utc": last,
        "n_files": len(files),
        "n_unfiled": unfiled,
        "first_file_utc": first_file,
        "last_file_utc": last_file,
        "store": {"path": str(store.path), "synced": store.exists()},
        "batches": _limited(batch_rows, limit),
        "n_collections": len(collection_rows),
        "collections": _limited(collection_rows, limit),
    }

    lines = [
        f"scope {resolved.key}: {len(batches):,} batch(es), {len(samples):,} "
        f"sample(s); {len(files):,} file(s) acquired, {unfiled:,} in no batch",
        f"  times in {zone}",
        f"  samples {_when(first, zone)} .. {_when(last, zone)}",
        f"  files   {_when(first_file, zone)} .. {_when(last_file, zone)}",
    ]
    if instruments:
        lines.append(
            "  instrument(s) " + ", ".join(quoted(name) for name in instruments)
        )
    if resolved.collection is not None:
        lines.append(f"  collection {resolved.collection}")
    synced = "synced" if store.exists() else "not synced"
    lines.append(f"  store {store.path} ({synced})")
    if batch_rows:
        lines.append(f"batches ({len(batch_rows):,}):")
        shown = _limited(batch_rows, limit)
        lines += _table(
            ["ID", "NAME", "POLARITY", "SAMPLES", "FIRST", "LAST"],
            [
                [
                    str(row["sample_batch_id"]),
                    quoted(row["sample_batch_name"]),
                    str(_first_set(row["polarity"], default="-")),
                    _number(row["n_samples"]),
                    _when(row["first_datetime_utc"], zone),
                    _when(row["last_datetime_utc"], zone),
                ]
                for row in shown
            ],
        )
        lines += _more(len(shown), len(batch_rows), "batch(es)")
    if collection_rows:
        lines.append(f"collections on these batches ({len(collection_rows):,}):")
        shown = _limited(collection_rows, limit)
        lines += _table(
            ["ID", "NAME", "TYPE", "BATCHES"],
            [
                [
                    str(row["target_collection_id"]),
                    quoted(row["target_collection_name"]),
                    str(_first_set(row["target_collection_type"], default="-")),
                    _number(row["n_batches"]),
                ]
                for row in shown
            ],
        )
        lines += _more(len(shown), len(collection_rows), "collection(s)")
    return payload, lines


@app.command()
@_command
def scopes(
    dataset: DatasetOption = None,
    batches: BatchesOption = None,
    instrument: InstrumentOption = None,
    start: FromOption = None,
    end: ToOption = None,
    polarity: PolarityOption = None,
    mode: ModeOption = None,
    collection: CollectionOption = None,
    tz: TzOption = None,
    limit: LimitOption = LIMIT,
    server: ServerOption = None,
    workspace: WorkspaceOption = None,
    as_json: JsonOption = False,
) -> None:
    """What a scope covers: its batches, its files and their span.

    A name that matches several objects lists them as candidates: give the
    one meant by its ID. Nothing is read beyond the server's listings.
    """
    scope = _scope(
        dataset, batches, instrument, start, end, polarity, mode, collection, tz
    )
    client = connect(server, workspace)
    payload, lines = _scope_report(client, resolve(client, scope), limit)
    _print(payload, lines, as_json)


# --- store -------------------------------------------------------------------


def _incomplete(status: StoreStatus) -> list[str]:
    """Why *status* is not complete, one reason each."""
    reasons = []
    if (
        status.synced_utc is None
        or status.sync_started_utc is None
        or status.synced_utc < status.sync_started_utc
    ):
        reasons.append("the last sync was stopped before its end")
    if status.failed:
        reasons.append(f"{len(status.failed):,} batch(es) not brought up to date")
    if status.pending_rollups:
        reasons.append(f"the rollups of {len(status.pending_rollups):,} day(s) pending")
    return reasons


@store_app.command("sync")
@_command
def store_sync(
    dataset: DatasetOption = None,
    batches: BatchesOption = None,
    instrument: InstrumentOption = None,
    start: FromOption = None,
    end: ToOption = None,
    polarity: PolarityOption = None,
    mode: ModeOption = None,
    collection: CollectionOption = None,
    tz: TzOption = None,
    pause: Annotated[
        float,
        typer.Option(
            "--pause",
            min=0.0,
            help="Seconds to wait between two batches' reads, so a long first "
            "sync does not read the server flat out.",
        ),
    ] = 0.5,
    server: ServerOption = None,
    workspace: WorkspaceOption = None,
    as_json: JsonOption = False,
) -> None:
    """Bring the scope's store up to date with the server.

    The first sync reads every batch; a later one reads again only the
    batches that changed, and those holding a sample from the last day.
    A batch whose read fails is skipped and recorded, and the next sync
    tries it again: the exit status is then 1.
    """
    scope = _scope(
        dataset, batches, instrument, start, end, polarity, mode, collection, tz
    )
    client = connect(server, workspace)
    report = sync(client, scope, pause=pause)
    if report.store is None:
        raise Ambiguous(report.resolved)
    status = report.store.status()
    payload = {
        **report.to_dict(),
        "complete": status.complete,
        "n_fetched": len(report.fetched),
        "n_unchanged": len(report.unchanged),
        "n_failed": len(report.failed),
        "n_removed": len(report.removed),
    }
    reasons: dict[str, int] = {}
    for why in report.fetched.values():
        reasons[why] = reasons.get(why, 0) + 1
    why = ", ".join(f"{n:,} {reason}" for reason, n in sorted(reasons.items()))
    lines = [
        f"synced scope {report.resolved.key} in {report.seconds:.1f} s: "
        f"{len(report.fetched):,} batch(es) read{f' ({why})' if why else ''}, "
        f"{len(report.unchanged):,} unchanged, {len(report.removed):,} removed",
        f"  store {report.store.path}",
    ]
    if report.failed:
        names = dict(
            zip(
                report.resolved.batches["sample_batch_id"],
                report.resolved.batches["sample_batch_name"],
            )
        )
        lines.append(
            f"  {len(report.failed):,} batch(es) failed; the store keeps what it "
            "held of them, and the next sync tries them again:"
        )
        lines += [
            f"    {batch_id} {quoted(names.get(batch_id, ''))}: {quoted(message)}"
            for batch_id, message in report.failed.items()
        ]
    _print(payload, lines, as_json)
    if report.failed:
        raise typer.Exit(1)


def _status_lines(status: StoreStatus, key: str, limit: int) -> list[str]:
    lines = [f"store {key} at {status.path}"]
    if not status.exists:
        lines.append(
            "  not synced: `mascope-viz store sync` with these options fills it"
        )
        return lines
    reasons = _incomplete(status)
    state = "complete" if status.complete else "NOT complete: " + "; ".join(reasons)
    age = None if status.age is None else status.age.total_seconds()
    lines.append(
        f"  synced {_when(status.synced_utc)} ({_age(age)} ago); {state}"
        if status.synced_utc is not None
        else f"  never synced to its end; {state}"
    )
    batches = status.batches
    n_samples = int(pd.to_numeric(batches["n_samples"]).fillna(0).sum())
    states = batches["state"].value_counts()
    counted = ", ".join(f"{n:,} {state}" for state, n in sorted(states.items()))
    lines.append(
        f"  {len(batches):,} batch(es) ({counted}), {n_samples:,} sample(s); "
        f"{status.n_files:,} file(s), {status.n_unfiled:,} in no batch; "
        f"{_size(status.size_bytes)} on disk"
    )
    lines.append(
        "  rows: " + ", ".join(f"{table} {n:,}" for table, n in status.rows.items())
    )
    if not status.runs.empty:
        mixed = (
            "mixed: a formula's series joins readings of different runs"
            if status.mixed_runs
            else "one reading per polarity"
            if len(status.runs) > 1
            else "one reading"
        )
        lines.append(f"  formula readings ({len(status.runs):,}, {mixed}):")
        lines += _table(
            ["READING", "BATCHES", "FIRST (UTC)", "LAST (UTC)"],
            [
                [
                    quoted(row.reading),
                    _number(row.n_batches),
                    _when(row.first_datetime_utc),
                    _when(row.last_datetime_utc),
                ]
                for row in status.runs.itertuples()
            ],
        )
    for change in status.run_changes:
        lines.append(
            f"  reading changes at {_when(change.at)} (batch {change.sample_batch_id},"
            f" polarity {change.polarity}): {quoted(change.before)} -> "
            f"{quoted(change.after)}"
        )
    if not batches.empty:
        shown = _limited(list(batches.itertuples()), limit)
        lines.append(f"batches ({len(batches):,}), times in UTC:")
        lines += _table(
            ["ID", "NAME", "STATE", "SAMPLES", "FIRST", "LAST", "SYNCED"],
            [
                [
                    str(row.sample_batch_id),
                    quoted(row.sample_batch_name),
                    str(row.state) + (" (run in flight)" if row.run_in_flight else ""),
                    _number(row.n_samples),
                    _when(row.first_datetime_utc),
                    _when(row.last_datetime_utc),
                    _when(row.synced_utc),
                ]
                for row in shown
            ],
        )
        lines += _more(len(shown), len(batches), "batch(es)")
    for batch_id, error in status.failed.items():
        lines.append(f"  {batch_id}: {quoted(error)}")
    lines.append(
        "  read from the disk alone: what the server holds now, the next sync finds out"
    )
    return lines


@store_app.command("status")
@_command
def store_status(
    dataset: DatasetOption = None,
    batches: BatchesOption = None,
    instrument: InstrumentOption = None,
    start: FromOption = None,
    end: ToOption = None,
    polarity: PolarityOption = None,
    mode: ModeOption = None,
    collection: CollectionOption = None,
    tz: TzOption = None,
    limit: LimitOption = LIMIT,
    server: ServerOption = None,
    workspace: WorkspaceOption = None,
    as_json: JsonOption = False,
) -> None:
    """What the scope's store holds and how old it is.

    It also says which engine runs read the formulas, and whether they mix.
    The scope is resolved on the server to find its store; what the store
    holds is read from the disk.
    """
    scope = _scope(
        dataset, batches, instrument, start, end, polarity, mode, collection, tz
    )
    client = connect(server, workspace)
    resolved = _resolved(client, scope)
    status = CampaignStore(store_path(client, resolved)).status()
    payload = status.to_dict()
    payload.update(
        key=resolved.key,
        incomplete=_incomplete(status) if status.exists else [],
        n_batches=len(payload["batches"]),
        batches=_limited(payload["batches"], limit),
    )
    _print(payload, _status_lines(status, resolved.key, limit), as_json)


def _on_disk(path: Path) -> tuple[int, int]:
    """How many files are under *path*, and their bytes."""
    sizes = [found.stat().st_size for found in path.rglob("*") if found.is_file()]
    return len(sizes), sum(sizes)


@store_app.command("rm")
@_command
def store_rm(
    dataset: DatasetOption = None,
    batches: BatchesOption = None,
    instrument: InstrumentOption = None,
    start: FromOption = None,
    end: ToOption = None,
    polarity: PolarityOption = None,
    mode: ModeOption = None,
    collection: CollectionOption = None,
    tz: TzOption = None,
    server: ServerOption = None,
    workspace: WorkspaceOption = None,
    as_json: JsonOption = False,
) -> None:
    """Delete the scope's store. It is a cache: the next sync rebuilds it.

    A delete that leaves files behind, such as one another program holds
    open, fails: the exit status is then 1.
    """
    scope = _scope(
        dataset, batches, instrument, start, end, polarity, mode, collection, tz
    )
    client = connect(server, workspace)
    resolved = _resolved(client, scope)
    store = CampaignStore(store_path(client, resolved))
    held = store.path.exists()
    _, size = _on_disk(store.path)
    store.remove()
    # The delete gives up silently on what it cannot remove
    if store.path.exists():
        raise NotRemoved(store.path, *_on_disk(store.path))
    payload = {
        "key": resolved.key,
        "path": str(store.path),
        "removed": held,
        "size_bytes": size,
    }
    line = (
        f"removed store {resolved.key} at {store.path} ({_size(size)})"
        if held
        else f"no store of scope {resolved.key} at {store.path}: nothing to remove"
    )
    _print(payload, [line], as_json)


# --- view --------------------------------------------------------------------


def _check_view(name: str, given: dict[str, bool], keys: list[str]) -> None:
    """Refuse a view the command line cannot compute as written."""
    if name not in VIEWS:
        raise typer.BadParameter(
            f"{name!r} is no view: the views are {', '.join(VIEWS)}",
            param_hint="'VIEW'",
        )
    takes = _TAKES[name]
    stray = [option for option, set_ in given.items() if set_ and option not in takes]
    if stray:
        raise InvalidArgument(
            f"View {name} takes no {', '.join(_SPELLED[o] for o in stray)}: it "
            f"takes {', '.join(_SPELLED[o] for o in takes)}."
        )
    if name in _NEED_KEYS and not keys:
        raise InvalidArgument(
            f"View {name} needs --keys: series keys as the store spells them "
            "(ion:<target_ion_id>, formula:<formula>|<mechanism>) or tic. "
            "`mascope-viz view top` lists the store's brightest."
        )


#: What :func:`_species` gives of a key.
_NAMES = ("name", "formula", "ion_formula")


def _species(store: CampaignStore, keys: Sequence[str]) -> dict[str, dict[str, Any]]:
    """Each key's ``name``, ``formula`` and ``ion_formula``, as the store's
    species have them: two ions of one compound share its name."""
    wanted = [key for key in keys if key != views.TIC]
    found = {views.TIC: {"name": "TIC", "formula": None, "ion_formula": None}}
    if wanted:
        species = store.table("species", keys=wanted)
        for row in species[["series_key", *_NAMES]].to_dict("records"):
            found[str(row.pop("series_key"))] = row
    return found


def _per_key(
    frame: pd.DataFrame,
    key: str,
    value: str,
    at: str,
    group: str | None = None,
    share: bool = False,
) -> list[dict[str, Any]]:
    """Per key of *frame*: its rows, how many have a *value*, their median
    and maximum, and where (*at*, and *group* when given) the maximum is;
    with *share*, the mean of its ``share`` column, biggest first.

    A key of None (a formula with no class) is a key too: None.
    """
    entries = []
    for name, rows in frame.groupby(
        frame[key].astype(object), sort=False, dropna=False
    ):
        values = rows[value].astype("float64")
        finite = values[np.isfinite(values)]
        entry: dict[str, Any] = {
            "key": None if pd.isna(name) else name,
            "rows": len(rows),
            "n": len(finite),
            "median": float(finite.median()) if len(finite) else None,
            "max": float(finite.max()) if len(finite) else None,
            "max_at": rows.at[finite.idxmax(), at] if len(finite) else None,
        }
        if group is not None:
            entry["max_group"] = (
                rows.at[finite.idxmax(), group] if len(finite) else None
            )
        if share:
            entry["share"] = float(rows["share"].mean())
        entries.append(entry)
    if share:
        entries.sort(key=lambda entry: -entry["share"])
    return entries


def _span(frame: pd.DataFrame, column: str) -> dict[str, Any]:
    times = frame[column] if column in frame else pd.Series(dtype=object)
    if times.empty:
        return {"first": None, "last": None}
    return {"first": times.min(), "last": times.max()}


def _summary(
    name: str, result: Any, store: CampaignStore, limit: int
) -> dict[str, Any]:
    """What ``view`` prints of a view: never the frame itself."""
    if name == "coverage":
        frame = result.bins
        gaps = result.gaps.sort_values(
            ["duration", "start"], ascending=[False, True], kind="stable"
        )
        # A file of no sample in the store has no mode, and may have no
        # polarity: None, which is no polarity's name. A mode's name is one
        # per mode, so it splits no group
        groups = (
            frame.groupby(["polarity", "mode", "mode_name"], sort=True, dropna=False)[
                ["n_files", "n_unfiled", "n_samples"]
            ]
            .sum()
            .reset_index()
            .astype({"polarity": object, "mode": object, "mode_name": object})
        )
        groups = groups.where(groups.notna(), None)
        extra = {
            "n_files": result.n_files,
            "n_unfiled": result.n_unfiled,
            "n_samples": result.n_samples,
            "groups": groups.to_dict("records"),
            "n_gaps": len(gaps),
            "gaps_seconds": float(gaps["duration"].dt.total_seconds().sum()),
            "gaps": [
                {
                    "start": row.start,
                    "end": row.end,
                    "duration": row.duration,
                    "n_files": row.n_files,
                    "n_unfiled": row.n_unfiled,
                }
                for row in _limited(list(gaps.itertuples()), limit)
            ],
        }
        return {"frame": frame, "span": _span(frame, "t"), "entries": [], **extra}

    frame = result
    if name in ("series", "diurnal", "periods") or (
        name == "composition" and frame.attrs.get("by") is None
    ):
        value, at = {
            "series": ("value", "t"),
            "diurnal": ("value", "hour"),
            "periods": ("median", "period"),
            "composition": ("median", "period"),
        }[name]
        group = "group" if name == "diurnal" and "group" in frame else None
        entries = _per_key(frame, "key", value, at, group, share=name == "composition")
        names = _species(store, [entry["key"] for entry in entries])
        for entry in entries:
            entry.update(names[entry["key"]])
    elif name == "composition":
        entries = _per_key(frame, frame.attrs["by"], "mean", "period", share=True)
    elif name == "spectrum":
        other = "diff" in frame
        order = (
            frame["diff"].abs().sort_values(ascending=False, kind="stable").index
            if other
            else frame["value"].sort_values(ascending=False, kind="stable").index
        )
        columns = ["key", "name", "formula", "ion_formula", "mz", "value", "n"]
        if other:
            columns += ["other_value", "other_n", "diff", "ratio"]
        entries = frame.loc[order, columns].to_dict("records")
    else:
        columns = ["rank", "key", "name", "formula", "value", "median", "first"]
        entries = frame[[*columns, "last", "ratio", "new"]].to_dict("records")
        # Named as series names them: the view's own names have no TIC
        names = _species(store, [entry["key"] for entry in entries])
        for entry in entries:
            entry.update(names[entry["key"]])

    at = {"series": "t", "periods": "period", "composition": "period"}.get(name)
    return {
        "frame": frame,
        "span": _span(frame, at) if at else {"first": None, "last": None},
        "entries": entries,
    }


def _view_lines(name: str, payload: dict[str, Any], status: StoreStatus) -> list[str]:
    attrs = payload["attrs"]
    zone = attrs.get("tz")
    lines = [
        f"view {name} of store {payload['key']}: {payload['rows']:,} row(s)"
        + (f", {payload['n_keys']:,} key(s)" if payload["n_keys"] else ""),
    ]
    how = []
    if name != "spectrum":
        auto = " (auto)" if attrs.get("auto") else ""
        how.append(f"cadence {attrs.get('cadence')}{auto}")
    if name == "series":
        band = attrs.get("band")
        how.append(
            f"{attrs.get('stat')} per bin"
            + (f", p{band[0]:g}-p{band[1]:g} band" if band else "")
        )
    if attrs.get("by") is not None:
        how.append(f"by {attrs['by']}")
    if attrs.get("min_gap") is not None:
        how.append(f"gaps from {_age(pd.Timedelta(attrs['min_gap']).total_seconds())}")
    if "normalise" in _TAKES[name]:
        normalise = attrs.get("normalise")
        named = attrs.get("normalise_name")
        how.append(
            "normalise "
            + (normalise if normalise in ("none", views.TIC) else quoted(normalise))
            + (f" ({quoted(named)})" if named else "")
        )
    how.append(f"times in {zone}")
    lines.append("  " + ", ".join(how))
    span = payload["span"]
    if span["first"] is not None:
        lines.append(f"  span {_when(span['first'])} .. {_when(span['last'])}")
    if attrs.get("start") or attrs.get("end"):
        lines.append(
            f"  window {_when(attrs.get('start'), zone)} .. "
            f"{_when(attrs.get('end'), zone)}"
        )
    if name == "top":
        lines.append(
            f"  ranked by {attrs.get('by')}, first {attrs.get('period')} "
            f"{_when(attrs.get('first_period'))}, last "
            f"{_when(attrs.get('last_period'))}"
        )
    if name == "spectrum":
        for window in ("period", "other"):
            bounds = attrs.get(window)
            # A spectrum of every sample has a period of no bounds
            if bounds and any(bound is not None for bound in bounds):
                lines.append(
                    f"  {window} {_when(bounds[0], zone)} .. {_when(bounds[1], zone)}"
                )

    entries = payload["entries"]
    if name == "coverage":
        lines.append(
            f"  {payload['n_files']:,} file(s), {payload['n_unfiled']:,} in no "
            f"batch; {payload['n_samples']:,} sample(s)"
        )
        # A name is not unique: two modes of one name are told apart by ID
        modes_named: dict[str | None, set[str | None]] = {}
        for group in payload["groups"]:
            modes_named.setdefault(group["mode_name"], set()).add(group["mode"])

        def mode_label(group: dict[str, Any]) -> str:
            if group["mode"] is None:
                return "(none)"
            if group["mode_name"] is None:
                # The ID of a mode the server did not name
                return quoted(group["mode"])
            if len(modes_named[group["mode_name"]]) > 1:
                return f"{quoted(group['mode_name'])} ({quoted(group['mode'])})"
            return quoted(group["mode_name"])

        lines += _table(
            ["POLARITY", "MODE", "FILES", "UNFILED", "SAMPLES"],
            [
                [
                    str(group["polarity"] or "(none)"),
                    mode_label(group),
                    _number(group["n_files"]),
                    _number(group["n_unfiled"]),
                    _number(group["n_samples"]),
                ]
                for group in payload["groups"]
            ],
        )
        lines.append(
            f"gaps ({payload['n_gaps']:,}, {_age(payload['gaps_seconds'])} in all), "
            "longest first:"
        )
        if payload["gaps"]:
            lines += _table(
                ["START", "END", "DURATION", "FILES", "UNFILED"],
                [
                    [
                        _when(gap["start"]),
                        _when(gap["end"]),
                        _age(gap["duration"].total_seconds()),
                        _number(gap["n_files"]),
                        _number(gap["n_unfiled"]),
                    ]
                    for gap in payload["gaps"]
                ],
            )
        lines += _more(len(payload["gaps"]), payload["n_gaps"], "gap(s)")
    elif name == "spectrum":
        other = "diff" in (entries[0] if entries else {})
        header = ["KEY", "NAME", "MZ", "VALUE", "N"]
        if other:
            header += ["OTHER", "DIFF", "RATIO"]
        lines += _table(
            header,
            [
                [
                    quoted(entry["key"]),
                    quoted(_first_set(entry["name"], entry["formula"])),
                    f"{entry['mz']:.4f}",
                    _number(entry["value"]),
                    _number(entry["n"]),
                ]
                + (
                    [
                        _number(entry["other_value"]),
                        _number(entry["diff"]),
                        _number(entry["ratio"]),
                    ]
                    if other
                    else []
                )
                for entry in entries
            ],
        )
    elif name == "top":
        lines += _table(
            ["RANK", "KEY", "NAME", "VALUE", "MEDIAN", "FIRST", "LAST", "NEW"],
            [
                [
                    _number(entry["rank"]),
                    quoted(entry["key"]),
                    quoted(_first_set(entry["name"], entry["formula"])),
                    _number(entry["value"]),
                    _number(entry["median"]),
                    _number(entry["first"]),
                    _number(entry["last"]),
                    _number(entry["new"]),
                ]
                for entry in entries
            ],
        )
    elif entries:
        share = "share" in entries[0]
        grouped = "max_group" in entries[0]
        # The groups of a composition by class or carbon number have no name
        named = "name" in entries[0]
        header = ["KEY"] + (["NAME"] if named else []) + ["N", "MEDIAN", "MAX"]
        header.append("HOUR" if name == "diurnal" else "AT")
        if grouped:
            header.append("GROUP")
        if share:
            header.append("SHARE")
        lines += _table(
            header,
            [
                ["(none)" if entry["key"] is None else quoted(entry["key"])]
                + (
                    [quoted(_first_set(entry["name"], entry["formula"]))]
                    if named
                    else []
                )
                + [
                    _number(entry["n"]),
                    _number(entry["median"]),
                    _number(entry["max"]),
                    _number(entry["max_at"])
                    if name == "diurnal"
                    else _when(entry["max_at"]),
                ]
                + ([str(entry["max_group"])] if grouped else [])
                + ([_number(entry["share"])] if share else [])
                for entry in entries
            ],
        )
    lines += _more(len(entries), payload["n_keys"], "key(s)")

    changes = attrs.get("run_changes") or []
    if attrs.get("mixed_runs"):
        lines.append(
            f"  mixed runs: the formulas are read by more than one engine run "
            f"({len(changes):,} change(s) in the window):"
        )
        lines += [
            f"    {_when(change['at'])} (batch {change['sample_batch_id']}): "
            f"{quoted(change['before'])} -> {quoted(change['after'])}"
            for change in changes
        ]
    if not status.complete:
        reasons = "; ".join(_incomplete(status))
        lines.append(f"  warning: the store is not complete ({reasons})")
    for path in payload["csv"]:
        lines.append(f"  csv {path}")
    return lines


@app.command()
@_command
def view(
    name: Annotated[
        str,
        typer.Argument(
            metavar="VIEW",
            help="series, diurnal, periods, coverage, composition, spectrum or top.",
            show_default=False,
        ),
    ],
    dataset: DatasetOption = None,
    batches: BatchesOption = None,
    instrument: InstrumentOption = None,
    start: FromOption = None,
    end: ToOption = None,
    polarity: PolarityOption = None,
    mode: ModeOption = None,
    collection: CollectionOption = None,
    tz: TzOption = None,
    keys: Annotated[
        Optional[list[str]],
        typer.Option(
            "--keys",
            "--key",
            "-k",
            help="A series key, as the store spells it (ion:<target_ion_id>, "
            "formula:<formula>|<mechanism>) or tic; repeat it for several. "
            "series, diurnal and periods need one; the others take every key "
            "of the store without.",
        ),
    ] = None,
    cadence: Annotated[
        Optional[str],
        typer.Option(
            "--cadence",
            help="series: auto (the default), raw, or a pandas offset (10min, "
            "1h, 1D, W, MS); coverage: an offset, 1h by default.",
        ),
    ] = None,
    normalise: Annotated[
        Optional[str],
        typer.Option(
            "--normalise",
            help="none (the default), tic, or a series key such as "
            "a reagent ion's: each value is divided by it, sample by sample.",
        ),
    ] = None,
    stat: Annotated[
        Optional[str],
        typer.Option(
            "--stat",
            help="series: a bin's value, median (the default), mean, min or max.",
        ),
    ] = None,
    by: Annotated[
        Optional[str],
        typer.Option(
            "--by",
            help="diurnal: month, season or weekday; composition: class or n_c; "
            "top: median (the default), ratio or new.",
        ),
    ] = None,
    period: Annotated[
        Optional[str],
        typer.Option(
            "--period",
            help="periods: day (the default), week or month; composition: all "
            "(the default), day, week or month; top: day, week (the default) "
            "or month.",
        ),
    ] = None,
    count: Annotated[
        Optional[int],
        typer.Option("-n", "--count", min=1, help="top: how many keys (10)."),
    ] = None,
    min_gap: Annotated[
        Optional[str],
        typer.Option(
            "--min-gap",
            help="coverage: the shortest stretch without a sample that is a gap (1h).",
        ),
    ] = None,
    window_from: Annotated[
        Optional[str],
        typer.Option(
            "--window-from",
            help="Only the samples from this time on; spectrum: the period's start.",
        ),
    ] = None,
    window_to: Annotated[
        Optional[str],
        typer.Option(
            "--window-to",
            help="Only the samples before this time; spectrum: the period's end.",
        ),
    ] = None,
    other_from: Annotated[
        Optional[str],
        typer.Option(
            "--other-from",
            help="spectrum: the start of another period to compare with.",
        ),
    ] = None,
    other_to: Annotated[
        Optional[str],
        typer.Option("--other-to", help="spectrum: that period's end."),
    ] = None,
    csv: Annotated[
        Optional[Path],
        typer.Option(
            "--csv",
            help="Write the view's frame to this file (coverage: also its gaps, "
            "beside it as <name>.gaps.csv).",
        ),
    ] = None,
    limit: LimitOption = LIMIT,
    server: ServerOption = None,
    workspace: WorkspaceOption = None,
    as_json: JsonOption = False,
) -> None:
    """Compute a view of the scope's store and print its summary.

    The summary is the view's rows and span, and per key how many values,
    their median and their maximum with its time. The frame itself is never
    printed: --csv writes it.

    \b
    series       each key over time, binned at a cadence
    diurnal      the hour-of-day cycle
    periods      the distribution per day, week or month
    coverage     files and samples per hour, the gaps, the files in no batch
    composition  the assigned formulas per period, or their class budget
    spectrum     a period's average spectrum, and against another period
    top          the brightest keys, the biggest changers, the new ones

    Run `mascope-viz store sync` with the same scope options first.
    """
    keys = list(keys or [])
    given = {
        "keys": bool(keys),
        "cadence": cadence is not None,
        "normalise": normalise is not None,
        "stat": stat is not None,
        "by": by is not None,
        "period": period is not None,
        "count": count is not None,
        "min_gap": min_gap is not None,
        "window": window_from is not None or window_to is not None,
        "other": other_from is not None or other_to is not None,
    }
    _check_view(name, given, keys)
    scope = _scope(
        dataset, batches, instrument, start, end, polarity, mode, collection, tz
    )
    client = connect(server, workspace)
    resolved = _resolved(client, scope)
    store = CampaignStore(store_path(client, resolved))
    if not store.exists():
        raise NotSynced(store.path)

    shared = {"normalise": normalise or "none", "tz": tz}
    window = {"start": window_from, "end": window_to}
    try:
        if name == "series":
            result = views.series(
                store,
                keys,
                cadence=cadence or "auto",
                stat=stat or "median",
                **shared,
                **window,
            )
        elif name == "diurnal":
            result = views.diurnal(store, keys, by=by, **shared, **window)
        elif name == "periods":
            result = views.periods(store, keys, period or "day", **shared, **window)
        elif name == "coverage":
            result = views.coverage(
                store,
                cadence=cadence or "1h",
                min_gap=min_gap or "1h",
                tz=tz,
                **window,
            )
        elif name == "composition":
            result = views.composition(
                store, period or "all", keys=keys or None, by=by, **shared, **window
            )
        elif name == "spectrum":
            result = views.spectrum(
                store,
                (window_from, window_to) if given["window"] else None,
                (other_from, other_to) if given["other"] else None,
                keys=keys or None,
                **shared,
            )
        else:
            result = views.top(
                store,
                count or 10,
                by or "median",
                keys=keys or None,
                period=period or "week",
                **shared,
                **window,
            )
    except ValueError as e:
        raise InvalidArgument(str(e)) from e

    summary = _summary(name, result, store, limit)
    frame = summary.pop("frame")
    entries = summary.pop("entries")
    written = []
    if csv is not None:
        csv.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(csv, index=False)
        written.append(csv)
        if name == "coverage":
            gaps = csv.with_name(f"{csv.stem}.gaps{csv.suffix or '.csv'}")
            result.gaps.to_csv(gaps, index=False)
            written.append(gaps)
    status = store.status()
    payload = {
        "view": name,
        "key": resolved.key,
        "store": {
            "path": str(store.path),
            "synced_utc": status.synced_utc,
            "complete": status.complete,
        },
        "rows": len(frame),
        "columns": [str(column) for column in frame.columns],
        "attrs": {k: v for k, v in frame.attrs.items() if k != "keys"},
        "n_keys": len(entries),
        "entries": _limited(entries, limit),
        **summary,
        "csv": [str(path) for path in written],
    }
    _print(payload, _view_lines(name, payload, status), as_json)
