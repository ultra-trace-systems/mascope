"""
Maintenance script to re-calibrate the TOF files already stored on a server:
refit each file of one instrument with the node's own calibration, apply it,
and rematch what the apply cleared, paced against the live ingest, with a
ledger that every change can be rolled back from.

The runner of the TOF mass calibration plan (``docs/dev/tof_calibration.md``,
section 6; MAS-192 decided how). A TOF fit is absolute: an apply recomputes
every m/z axis of the file from the stored TOF index. So a re-calibration is
a refit from the stored peaks followed by an apply, with no re-detection.

**Three actions** (``TOF_RECAL_ACTION``):

- ``dry-run`` (the default) refits every file read-only, with the audit's
  handler (``tof_calibration_audit``) and the tolerances ingest would widen
  through, and reports what a run would do to each: apply an ``ok`` or a
  ``poor`` fit, keep an ``ok`` record a ``poor`` fit would replace, or fail;
  and how far it would move the m/z axis in each mass band. It writes
  nothing, the ledger included.
- ``run`` calibrates each file as ingest does (``calibrate_with_retry``: the
  same fit, the same widening tolerance, the same verdict under the server's
  ``calibration_quality_gate``, the same failure marker), with one
  exception: a fit below the quality bar never replaces a record that
  cleared it (``status: ok``). That file keeps its record and the ledger says
  so. Before each apply the ledger records what the file had. After each
  batch of files the runner matches the files' acquisition samples, as
  ingest does, and rematches every other batch the applies set to
  ``rematch``. It touches no peak-assignment run: it lists those on the
  files it applied.
- ``rollback`` puts back what the ledger recorded before an apply, for a
  file or a batch of the run (``TOF_RECAL_ROLLBACK``): the database record
  and the ``.props`` record as they were, and every m/z axis of the file
  bit for bit. Then it rematches, as after an apply.

**Which files.** The instrument's TOF files acquired in the range, newest
first, whose one acquisition sample to calibrate is in a mode with a
calibration collection: the files ingest calibrates. A file already on the
target - its record fitted by this node version from the mode's collection as
it is now (``quality.node_version``, ``quality.collection``) - is skipped, so a
run that stopped resumes where it stopped. So is a file the ledger already
has an outcome for under the same target: kept, failed, not refit-ready or
rolled back (``TOF_RECAL_RETRY=1`` tries those again). Files of a mode
without calibrants are left for calibration transfer (MAS-187) and counted.

**Pacing**, the pattern of the composite cut's re-processing (MAS-90):
batches of ``TOF_RECAL_BATCH`` files, a gap of ``TOF_RECAL_GAP`` seconds
between them, and a backlog guard that holds before each file and each
rematch while the live ingest has more than ``TOF_RECAL_BACKLOG`` files in
progress. A file ``STOP`` in the ledger's directory ends the run at the next
file; the next run rematches what is left first.

**An apply cut off** - Ctrl+C, a restart, a fault part-way - leaves an
``apply`` event with no outcome. A run and a rollback first close each one by
what the file's record holds (:func:`close_open_apply`): the apply's fit, and
the apply is finished and ``applied``; what the file had before, and what the
apply may have written of its axes is put back (``restored``); anything else,
and another apply has rewritten the file since (``superseded``). An apply that
fails part-way in a run is put back at once the same way.

**The ledger** is ``<filestore>/tof_recalibration/<instrument>/ledger.jsonl``
(``TOF_RECAL_LEDGER`` names another directory), one JSON event per line,
appended and synced as it happens, in the filestore so the site's backup
keeps it. Per file it holds the records before and after, the parameters, the
record version, the residuals and the axis change per band. Where the old
record does not give the file's stored axes back bit for bit, the axes it
cannot give are kept beside it in ``axes/``: the peaks' m/z of a file still on
the converter's axis, a few kB.

Configuration, all through the environment:

- ``TOF_RECAL_INSTRUMENT``: the instrument, as files name it. Required.
- ``TOF_RECAL_ACTION``: ``dry-run`` (default), ``run`` or ``rollback``.
- ``TOF_RECAL_FROM`` / ``TOF_RECAL_TO``: acquisition dates, inclusive,
  ``YYYY-MM-DD`` (UTC). Default: every file.
- ``TOF_RECAL_MODE``: only the files of the ionization mode of this name.
- ``TOF_RECAL_BATCH`` (25), ``TOF_RECAL_GAP`` (60 s), ``TOF_RECAL_BACKLOG``
  (20): the pacing.
- ``TOF_RECAL_LIMIT``: at most this many files this run; 0 (default) for all.
- ``TOF_RECAL_RETRY=1``: try again the files the ledger has an outcome for.
- ``TOF_RECAL_ROLLBACK``: what to roll back, comma separated: ``batch:<n>``
  (a batch number the ledger and the log give) or ``file:<sample file id>``.
- ``TOF_RECAL_BANDS``: mass band edges in Th; default
  ``40,100,150,200,300,500``.
- ``TOF_RECAL_LEDGER``: the ledger's directory.
- ``TOF_RECAL_JSON=1``: also print the report as one JSON document, every
  file included, as the last line of stdout.

Usage:
    TOF_RECAL_INSTRUMENT=<name> mascope prod db script run recalibrate_tof
    TOF_RECAL_INSTRUMENT=<name> TOF_RECAL_ACTION=run \\
        mascope prod db script run recalibrate_tof
    TOF_RECAL_INSTRUMENT=<name> TOF_RECAL_ACTION=rollback \\
        TOF_RECAL_ROLLBACK=batch:3 mascope prod db script run recalibrate_tof

Date: 2026-11-18
"""

import asyncio
import copy
import json
import os
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from datetime import time as day_time

import numpy as np
from sqlalchemy import func, select

import mascope_file.io as m_io
import mascope_file.name as m_name
from mascope_backend.api.controllers.calibration.calibration_controller import (
    APPLIED_STATUSES,
)
from mascope_backend.api.controllers.calibration.lib import (
    tof_calibration_audit as audit,
)
from mascope_backend.api.controllers.calibration.lib.calibration_mz_fit import (
    TOF_CALIBRATION_NODE_VERSION,
    TofCalibrationHandler,
    calibration_isotopes_sha256,
    calibration_params_factory,
    calibration_quality_issues,
    fit_quality,
)
from mascope_backend.api.controllers.match.match_controller import (
    match_compute_sample,
    match_remove_sample,
    rematch_batch,
)
from mascope_backend.api.controllers.sample.batches.status.service import (
    update_sample_batch_status,
)
from mascope_backend.api.controllers.sample.files.process.service import (
    CALIBRATION_ITERATIONS,
    _is_verified_record,
    calibrate_with_retry,
    widen_mz_error_tolerance,
)
from mascope_backend.api.controllers.sample.files.sample_files_controller import (
    update_sample_file,
)
from mascope_backend.api.controllers.sample.lib.fetch_affected_sample_data import (
    fetch_affected_sample_data,
)
from mascope_backend.api.controllers.sample.lib.sample_file_fetch import (
    fetch_sample_file,
)
from mascope_backend.api.models.calibration.calibration_pydantic_model import (
    CalibrationFitParams,
)
from mascope_backend.api.models.sample.files.config import IN_PROGRESS, STALLED_AFTER
from mascope_backend.api.models.sample.files.sample_file_pydantic_model import (
    SampleFileUpdate,
)
from mascope_backend.api.new.instrument_configs.lib import read_instrument_functions
from mascope_backend.api.new.ionization.modes.versions import current_versions
from mascope_backend.db import (
    IonizationMode,
    PeakAssignmentRun,
    SampleBatch,
    SampleFile,
    SampleItem,
    async_session,
    configure_database_engine,
)
from mascope_backend.db.id import gen_id
from mascope_backend.db.scripts.audit_tof_calibration import (
    _collection_isotopes,
    _day_env,
    _env,
    _number_env,
)
from mascope_backend.runtime import runtime
from mascope_backend.socket.notifications import without_audience
from mascope_file.runtime import runtime as file_runtime
from mascope_runtime.atomic import write_json
from mascope_tofwerk.calibration import tof_to_mass


DRY_RUN, RUN, ROLLBACK = "dry-run", "run", "rollback"
ACTIONS = (DRY_RUN, RUN, ROLLBACK)

#: The pacing of the composite cut's re-processing on a site (MAS-90): 25
#: files a batch, a minute between batches, and the live ingest's backlog
#: held under about twenty files.
DEFAULT_BATCH = 25
DEFAULT_GAP_S = 60.0
DEFAULT_BACKLOG = 20
#: How often the backlog guard looks again while it holds.
BACKLOG_POLL_S = 30.0

#: Where the ledger goes in the filestore, under the instrument's name.
LEDGER_ROOT = "tof_recalibration"
LEDGER_FILE = "ledger.jsonl"
#: The directory beside the ledger that keeps the axes an old record cannot
#: give back.
SNAPSHOTS = "axes"
#: The file in the ledger's directory that ends a run.
STOP_FILE = "STOP"

#: The points of a file's axis its change per band is read at: evenly over
#: its samples. Ample for a change that moves smoothly with the index.
BAND_POINTS = 2000

#: The modes whose axis has a closed inverse, which finds the index the
#: converter's axis starts at (:func:`_index_of`).
_INVERTIBLE_MODES = (0, 1, 2)

#: Ledger events that settle a file under its target until a retry.
_SETTLED = ("applied", "kept", "failed", "skipped", "rollback")
#: Ledger events that close a file's open apply (:meth:`Ledger.open_apply`):
#: it landed, was undone, was put back, or another apply rewrote the file.
#: Nothing else does: an error, a kept or a failed fit leaves it open.
_CLOSES_APPLY = ("applied", "rollback", "restored", "superseded")


# --- Configuration ---


@dataclass(frozen=True)
class Config:
    action: str
    instrument: str
    first_day: date | None
    last_day: date | None
    mode: str | None
    batch_size: int
    gap_s: float
    backlog: int
    limit: int
    retry: bool
    rollback: tuple[str, ...]
    band_edges: tuple[float, ...]
    ledger_dir: str
    as_json: bool


def _parse_rollback(raw: str | None) -> tuple[str, ...]:
    if raw is None:
        return ()
    items = tuple(item.strip() for item in raw.split(",") if item.strip())
    for item in items:
        kind, _, value = item.partition(":")
        if kind not in ("batch", "file") or not value:
            raise ValueError(
                f"TOF_RECAL_ROLLBACK: '{item}' is not batch:<n> or file:<id>."
            )
        if kind == "batch" and not value.isdigit():
            raise ValueError(f"TOF_RECAL_ROLLBACK: '{item}' names no batch number.")
    return items


def _parse_bands(raw: str | None) -> tuple[float, ...]:
    if raw is None:
        return audit.DEFAULT_BAND_EDGES
    try:
        edges = tuple(float(edge) for edge in raw.split(","))
    except ValueError:
        raise ValueError(f"TOF_RECAL_BANDS={raw!r} is not a list of numbers.")
    if list(edges) != sorted(set(edges)):
        raise ValueError("TOF_RECAL_BANDS must rise.")
    return edges


def read_config() -> Config:
    """The runner's configuration, from the environment.

    :raises ValueError: On anything that cannot be read.
    """
    instrument = _env("TOF_RECAL_INSTRUMENT")
    if instrument is None:
        raise ValueError("Set TOF_RECAL_INSTRUMENT to the instrument to re-calibrate.")
    action = _env("TOF_RECAL_ACTION") or DRY_RUN
    if action not in ACTIONS:
        raise ValueError(
            f"TOF_RECAL_ACTION={action!r} is not one of {', '.join(ACTIONS)}."
        )
    first_day = _day_env("TOF_RECAL_FROM", None)
    last_day = _day_env("TOF_RECAL_TO", None)
    if first_day and last_day and first_day > last_day:
        raise ValueError("TOF_RECAL_FROM is after TOF_RECAL_TO.")
    rollback = _parse_rollback(_env("TOF_RECAL_ROLLBACK"))
    if action == ROLLBACK and not rollback:
        raise ValueError("Set TOF_RECAL_ROLLBACK to batch:<n> or file:<id>.")
    if action != ROLLBACK and rollback:
        raise ValueError("TOF_RECAL_ROLLBACK is read by TOF_RECAL_ACTION=rollback.")
    return Config(
        action=action,
        instrument=instrument,
        first_day=first_day,
        last_day=last_day,
        mode=_env("TOF_RECAL_MODE"),
        batch_size=_number_env("TOF_RECAL_BATCH", DEFAULT_BATCH, int, 1),
        gap_s=_number_env("TOF_RECAL_GAP", DEFAULT_GAP_S, float, 0.0),
        backlog=_number_env("TOF_RECAL_BACKLOG", DEFAULT_BACKLOG, int, 0),
        limit=_number_env("TOF_RECAL_LIMIT", 0, int, 0),
        retry=_env("TOF_RECAL_RETRY") == "1",
        rollback=rollback,
        band_edges=_parse_bands(_env("TOF_RECAL_BANDS")),
        ledger_dir=_env("TOF_RECAL_LEDGER")
        or file_runtime.filestore(LEDGER_ROOT, instrument),
        as_json=_env("TOF_RECAL_JSON") == "1",
    )


# --- The ledger ---


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


class Ledger:
    """The run's record, one JSON event per line, appended and synced.

    Every event carries ``event``, ``at`` and its place in the ledger,
    ``seq``. The events about a file carry its ``sample_file_id``.
    """

    def __init__(self, directory: str, writable: bool):
        self.directory = directory
        self.path = os.path.join(directory, LEDGER_FILE)
        self.writable = writable
        self.events: list[dict] = []
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as ledger:
                lines = ledger.read().split("\n")
            for number, line in enumerate(lines, 1):
                if not line.strip():
                    continue
                try:
                    self.events.append(json.loads(line))
                except json.JSONDecodeError:
                    # A line cut short by a run that died while writing it:
                    # only ever the last one, which the next write ends
                    runtime.logger.warning(
                        f"Ledger line {number} is not whole; it is passed over."
                    )
        if writable:
            os.makedirs(os.path.join(directory, SNAPSHOTS), exist_ok=True)
            if os.path.exists(self.path) and os.path.getsize(self.path):
                with open(self.path, "rb") as ledger:
                    ledger.seek(-1, os.SEEK_END)
                    whole = ledger.read(1) == b"\n"
                if not whole:
                    with open(self.path, "a", encoding="utf-8") as ledger:
                        ledger.write("\n")

    def write(self, event: dict) -> dict:
        """Append an event and sync it to disk before going on."""
        if not self.writable:
            raise RuntimeError("A dry run writes no ledger.")
        event = {"event": event.pop("event"), "at": _utc_now(), **event}
        event["seq"] = (self.events[-1].get("seq", 0) if self.events else 0) + 1
        with open(self.path, "a", encoding="utf-8") as ledger:
            ledger.write(json.dumps(event, default=_json_default) + "\n")
            ledger.flush()
            os.fsync(ledger.fileno())
        self.events.append(event)
        return event

    def next_batch(self) -> int:
        return 1 + max((e.get("batch") or 0 for e in self.events), default=0)

    def of_file(self, sample_file_id: str) -> list[dict]:
        return [e for e in self.events if e.get("sample_file_id") == sample_file_id]

    def settled(self, target_of: Callable[[str], dict | None]) -> set[str]:
        """The files with an outcome under the target they would be run to.

        A file with an open apply is never settled, whatever came after it.

        :param target_of: The target a file's mode has now, by its mode id.
        """
        last: dict[str, dict] = {}
        for event in self.events:
            if event.get("event") in _SETTLED and event.get("sample_file_id"):
                last[event["sample_file_id"]] = event
        still_open = self.open_applies()
        return {
            file_id
            for file_id, event in last.items()
            if file_id not in still_open
            and (
                event["event"] == "rollback"
                or event.get("target") == target_of(event.get("mode_id"))
            )
        }

    def open_applies(self) -> dict[str, dict]:
        """Each file's apply event that nothing closed since (``applied``, a
        rollback, a put-back, or another apply over it): one a run that was
        cut off, or an apply that failed part-way, left. Such an apply may have
        written the file's axes, its ``.props``, its record, or all of them."""
        still_open: dict[str, dict] = {}
        for event in self.events:
            kind, sample_file_id = event.get("event"), event.get("sample_file_id")
            if kind == "apply":
                still_open[sample_file_id] = event
            elif kind in _CLOSES_APPLY:
                still_open.pop(sample_file_id, None)
        return still_open

    def open_apply(self, sample_file_id: str) -> dict | None:
        """The file's open apply, if it has one (:meth:`open_applies`)."""
        open_event = None
        for event in self.of_file(sample_file_id):
            kind = event.get("event")
            if kind == "apply":
                open_event = event
            elif kind in _CLOSES_APPLY:
                open_event = None
        return open_event

    def last_applied(self, sample_file_id: str) -> dict | None:
        """The apply event of the file's last completed apply, if no rollback
        undid it since."""
        applies: dict[int, dict] = {}
        last = None
        for event in self.of_file(sample_file_id):
            kind = event.get("event")
            if kind == "apply":
                applies[event["seq"]] = event
            elif kind == "applied":
                last = applies.get(event.get("apply_seq"))
            elif kind == "rollback":
                last = None
        return last

    def unmatched(self) -> list[dict]:
        """The applied or rolled-back files whose acquisition samples were not
        matched since: the outcome event of each."""
        pending: dict[str, dict] = {}
        for event in self.events:
            kind = event.get("event")
            if kind in ("applied", "rollback") and event.get("sample_file_id"):
                pending[event["sample_file_id"]] = event
            elif kind == "matched":
                pending.pop(event.get("sample_file_id"), None)
        return list(pending.values())

    def touched_batches(self) -> set[str]:
        """Every batch other than an acquisition one that an apply, a rollback
        or a put-back of the ledger set to ``rematch``."""
        return {
            batch_id
            for event in self.events
            if event.get("event") in ("apply", "rollback", "restored")
            for batch_id in event.get("batches") or ()
        }

    def stop_requested(self) -> bool:
        return os.path.exists(os.path.join(self.directory, STOP_FILE))


def _json_default(value):
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"{type(value).__name__} is not JSON")


# --- Which files ---


@dataclass(frozen=True)
class Candidate:
    """A TOF file and the sample of it ingest calibrates."""

    sample_file_id: str
    filename: str
    acquired: datetime
    record: dict | None
    range: list | None
    #: None for a blank: no instrument functions, no peaks
    instrument_function_id: str | None
    #: The acquisition samples of the file: id, name, current mode id, and
    #: whether that mode has a calibration collection.
    samples: tuple[dict, ...]

    @property
    def calibrating(self) -> list[dict]:
        return [sample for sample in self.samples if sample["collection_id"]]

    @property
    def mode_id(self) -> str | None:
        calibrating = self.calibrating
        return calibrating[0]["mode_id"] if len(calibrating) == 1 else None


async def select_candidates(config: Config) -> list[Candidate]:
    """The instrument's TOF files in the range, newest first, each with its
    acquisition samples under the current versions of their modes."""
    conditions = [
        SampleFile.instrument == config.instrument,
        SampleFile.instrument_type == "tof",
    ]
    if config.first_day:
        conditions.append(
            SampleFile.datetime_utc
            >= datetime.combine(config.first_day, day_time.min, tzinfo=UTC)
        )
    if config.last_day:
        conditions.append(
            SampleFile.datetime_utc
            < datetime.combine(
                config.last_day + timedelta(days=1), day_time.min, tzinfo=UTC
            )
        )
    async with async_session() as session:
        files = (
            await session.execute(
                select(
                    SampleFile.sample_file_id,
                    SampleFile.filename,
                    SampleFile.datetime_utc,
                    SampleFile.mz_calibration,
                    SampleFile.range,
                    SampleFile.instrument_function_id,
                )
                .where(*conditions)
                .order_by(SampleFile.datetime_utc.desc(), SampleFile.filename)
            )
        ).all()
        items = (
            await session.execute(
                select(
                    SampleItem.sample_file_id,
                    SampleItem.sample_item_id,
                    SampleItem.sample_item_name,
                    SampleItem.ionization_mode_id,
                )
                .join(
                    SampleBatch,
                    SampleBatch.sample_batch_id == SampleItem.sample_batch_id,
                )
                .join(
                    SampleFile, SampleFile.sample_file_id == SampleItem.sample_file_id
                )
                .where(*conditions, SampleBatch.sample_batch_type == "ACQUISITION")
                .order_by(SampleItem.sample_item_id)
            )
        ).all()
        mode_ids = {item.ionization_mode_id for item in items} - {None}
        heads = await current_versions(session, mode_ids) if mode_ids else {}
        modes = {
            mode.ionization_mode_id: mode
            for mode in (
                await session.execute(
                    select(IonizationMode).where(
                        IonizationMode.ionization_mode_id.in_(mode_ids)
                    )
                )
            )
            .scalars()
            .all()
        }

    def current(mode_id: str | None) -> IonizationMode | None:
        if mode_id is None:
            return None
        return heads.get(mode_id) or modes.get(mode_id)

    samples_of = defaultdict(list)
    for item in items:
        mode = current(item.ionization_mode_id)
        samples_of[item.sample_file_id].append(
            {
                "sample_item_id": item.sample_item_id,
                "sample_item_name": item.sample_item_name,
                "mode_id": mode.ionization_mode_id if mode else None,
                "mode_name": mode.ionization_mode_name if mode else None,
                "collection_id": mode.calibration_collection_id if mode else None,
            }
        )
    return [
        Candidate(
            sample_file_id=row.sample_file_id,
            filename=row.filename,
            acquired=row.datetime_utc,
            record=row.mz_calibration,
            range=row.range,
            instrument_function_id=row.instrument_function_id,
            samples=tuple(samples_of.get(row.sample_file_id, ())),
        )
        for row in files
    ]


# --- The target: the mode's collection as it is now, and this node ---


@dataclass
class ModeTarget:
    mode: IonizationMode
    params: CalibrationFitParams
    #: The collection's isotopes for the mode, labelled; None when it offers
    #: none, which leaves the mode's files nothing to fit against.
    isotopes: object
    #: What a record fitted to the target carries: ``collection`` and
    #: ``node_version``, as ``quality`` holds them.
    target: dict | None


async def prepare_target(mode_id: str, filename: str) -> ModeTarget:
    """The mode's collection as a fit would be given it now.

    :param filename: One of the mode's files, which tells the instrument type
        the isotopes are read at.
    """
    async with async_session() as session:
        mode = await session.get(IonizationMode, mode_id)
    params = CalibrationFitParams(
        calibration_collection_id=mode.calibration_collection_id,
        ionization_mechanism_ids=mode.ionization_mechanism_ids,
        polarity=mode.ionization_mode_polarity,
        **calibration_params_factory(filename).model_dump(),
    )
    isotopes = await _collection_isotopes(
        filename, params, mode.calibration_collection_id
    )
    target = None
    if isotopes is not None:
        target = {
            "collection": {
                "id": mode.calibration_collection_id,
                "isotopes_sha256": calibration_isotopes_sha256(isotopes),
            },
            "node_version": TOF_CALIBRATION_NODE_VERSION,
        }
    return ModeTarget(mode=mode, params=params, isotopes=isotopes, target=target)


def on_target(record: dict | None, target: dict | None) -> bool:
    """Whether an applied record was fitted by this node from the collection
    as it is now: re-fitting it would change nothing."""
    if not target or not isinstance(record, dict):
        return False
    quality = record.get("quality")
    return (
        record.get("status") in APPLIED_STATUSES
        and isinstance(quality, dict)
        and quality.get("collection") == target["collection"]
        and quality.get("node_version") == target["node_version"]
    )


def record_status(record: dict | None) -> str:
    """What kind of record a file holds, as the ledger and the report say it."""
    if not isinstance(record, dict):
        return "none"
    if record.get("status"):
        return str(record["status"])
    if "verified" in record:
        return "legacy"
    return "unfitted"


# --- The axes before: what gives them back bit for bit ---


def _formula(index: np.ndarray, record: dict, offset: float = 0.0) -> np.ndarray:
    """The m/z a record gives a TOF index, the index counted from ``offset``."""
    index = np.asarray(index, dtype=np.float64) + offset
    return np.asarray(tof_to_mass(index, record["mode"], record["par"]), np.float64)


def _index_of(mz: float, record: dict) -> float | None:
    """The TOF index a record gives an m/z, where its mode has an inverse."""
    mode, par = record.get("mode"), record.get("par") or []
    try:
        match mode:
            case 0:
                return par[0] * np.sqrt(mz) + par[1]
            case 1:
                return par[0] / np.sqrt(mz) + par[1]
            case 2:
                return par[0] * mz ** par[2] + par[1]
    except (IndexError, TypeError, ValueError):
        return None
    return None


def _axis_vars(filename: str) -> list[str]:
    """The stores an apply writes the m/z axis of, but the peaks'."""
    path = m_name.parse_path_from_item_filename(filename)
    names = [
        var for var in m_io.get_file_data_vars(path) if var.startswith("sum_signal")
    ]
    if m_name.get_sample_file_type(filename) == "tof_zarr":
        names.append("signal")
    return sorted(names)


def _read_axes(filename: str) -> tuple[dict[str, np.ndarray], tuple | None]:
    """Every m/z axis an apply writes, and the peaks' TOF index and m/z."""
    axes = {var: m_io.load_coord(filename, var, "mz") for var in _axis_vars(filename)}
    try:
        peaks = (
            m_io.load_coord(filename, "peak_timeseries", "tof"),
            m_io.load_coord(filename, "peak_timeseries", "mz"),
        )
    except FileNotFoundError:
        peaks = None
    return axes, peaks


def restore_plan(filename: str, props_record: dict | None) -> tuple[dict, dict]:
    """How the file's m/z axes go back to what they are now, bit for bit.

    An apply writes the axes from the TOF index alone, so an old record that
    was an applied fit gives them all back: it made them. The converter's
    record does not. Its coefficients count samples from the start of the raw
    file's axis, which the converter cut, so the stored axes are the record's
    from the index where the cut ended: that index is found and checked. Its
    peaks' m/z are where the peaks were found on that axis, which no record
    gives exactly, so they are kept as they are.

    Synchronous: it reads the filestore.

    :return: The plan for the ledger - the ``offset`` the record's index
        starts at, None where no offset gives the axes back, and the stores
        ``kept`` as arrays - and the arrays to keep, by store.
    """
    axes, peaks = _read_axes(filename)
    offset = None
    record = props_record if isinstance(props_record, dict) else {}
    if record.get("mode") in _INVERTIBLE_MODES and axes:
        reference = next(iter(axes.values()))
        candidates = [0.0]
        if reference.size:
            start = _index_of(float(reference[0]), record)
            if start is not None and np.isfinite(start):
                candidates.append(float(round(start)))
        for candidate in dict.fromkeys(candidates):
            if all(
                np.array_equal(
                    _formula(np.arange(array.size), record, candidate).astype(
                        array.dtype
                    ),
                    array,
                )
                for array in axes.values()
            ):
                offset = candidate
                break
    kept = {} if offset is not None else dict(axes)
    if peaks is not None:
        tofs, mzs = peaks
        if offset is None or not np.array_equal(
            _formula(tofs, record, offset).astype(mzs.dtype), mzs
        ):
            kept["peak_timeseries"] = mzs
    return {"offset": offset, "kept": sorted(kept)}, kept


def _snapshot_path(ledger: Ledger, sample_file_id: str) -> str:
    """A new snapshot's path, relative to the ledger's directory."""
    count = sum(
        1 for event in ledger.of_file(sample_file_id) if event.get("event") == "apply"
    )
    return os.path.join(SNAPSHOTS, f"{sample_file_id}.{count + 1}.npz")


def write_snapshot(directory: str, relative: str, arrays: dict) -> None:
    """Keep the arrays an old record cannot give back, synced to disk."""
    path = os.path.join(directory, relative)
    with open(path, "wb") as target:
        np.savez_compressed(target, **arrays)
        target.flush()
        os.fsync(target.fileno())


# --- The axis change per band ---


def axis_change(
    old: Callable[[np.ndarray], np.ndarray],
    new_fit: dict,
    n_samples: int,
    edges,
) -> list[dict]:
    """How far a fit moves the m/z axis in each mass band, in ppm.

    Read at :data:`BAND_POINTS` samples evenly over the axis; each band, by
    the m/z it holds now, gets the median change and its smallest and
    largest.

    :param old: The m/z the file holds at a sample index
    """
    if n_samples <= 0:
        return []
    index = np.unique(
        np.linspace(0, n_samples - 1, min(n_samples, BAND_POINTS)).round()
    ).astype(np.int64)
    before = old(index)
    after = _formula(index, new_fit)
    with np.errstate(invalid="ignore", divide="ignore"):
        change = 1e6 * (after - before) / before
    good = np.isfinite(change) & (before > 0)
    labels = audit.band_labels(edges)
    bands = audit.band_of(before[good], edges)
    out = []
    for number, label in enumerate(labels):
        values = change[good][bands == number]
        if values.size:
            out.append(
                {
                    "band": label,
                    "median": float(np.median(values)),
                    "min": float(values.min()),
                    "max": float(values.max()),
                }
            )
    return out


def old_axis(filename: str, props_record: dict | None, offset: float | None):
    """The m/z the file holds at a sample index, and its number of samples."""
    axes, _ = _read_axes(filename)
    if not axes:
        return None, 0
    reference = next(iter(axes.values()))
    if offset is not None:
        return (
            lambda index: _formula(index, props_record, offset),
            int(reference.size),
        )
    stored = np.asarray(reference, dtype=np.float64)
    return (lambda index: stored[index]), int(reference.size)


# --- The fit as ingest makes it, read-only (the dry run) ---


@dataclass
class DryFit:
    fit: dict | None
    quality: dict | None
    issues: list
    reason: str | None
    attempts: int


async def dry_fit(candidate: Candidate, target: ModeTarget) -> DryFit:
    """The fit ingest's calibration would apply, made without writing.

    The audit's handler, which reads the stored peaks and ranks a candidate
    whose timeseries the store does not hold by its stored sum, attempt by
    attempt at the tolerances ingest widens through
    (``widen_mz_error_tolerance``), until one gives a fit with no warning.
    """
    stored = await asyncio.to_thread(audit.read_stored_peaks, candidate.filename)
    _, resolution = await read_instrument_functions(
        candidate.filename, polarity=target.mode.ionization_mode_polarity
    )
    params = target.params.model_copy(deep=True)
    reason = None
    for attempt in range(1, CALIBRATION_ITERATIONS + 1):
        handler = audit.AuditCalibrationHandler(
            stored, params, target.isotopes, resolution, model=None
        )
        await handler.fit()
        if handler.fit_result is not None and not (handler.warning or handler.error):
            quality = fit_quality(handler.stats, params)
            if quality is not None and handler.fitted_on is not None:
                quality.update(handler.fitted_on)
            return DryFit(
                fit={
                    "mode": handler.fit_result["mode"],
                    "par": handler.fit_result["par"],
                },
                quality=quality,
                issues=calibration_quality_issues(quality, candidate.filename),
                reason=None,
                attempts=attempt,
            )
        reason = handler.error or handler.warning
        if attempt < CALIBRATION_ITERATIONS:
            widen_mz_error_tolerance(params)
    return DryFit(None, None, [], reason, CALIBRATION_ITERATIONS)


# --- Pacing ---


async def fresh_in_progress() -> int:
    """The files the live ingest is processing: in progress, and not stalled."""
    async with async_session() as session:
        return int(
            await session.scalar(
                select(func.count()).where(
                    SampleFile.processing_status.in_([str(s) for s in IN_PROGRESS]),
                    SampleFile.processing_updated_utc
                    >= datetime.now(UTC) - STALLED_AFTER,
                )
            )
            or 0
        )


async def hold_for_backlog(config: Config, ledger: Ledger, sleep=asyncio.sleep) -> bool:
    """Wait while the live ingest has more than its share in progress.

    :return: False when a ``STOP`` came while it waited.
    """
    said = False
    while True:
        waiting = await fresh_in_progress()
        if waiting <= config.backlog:
            if said:
                runtime.logger.info("Backlog cleared; going on.")
            return True
        if ledger.stop_requested():
            return False
        if not said:
            runtime.logger.info(
                f"The ingest has {waiting} files in progress, over "
                f"{config.backlog}: holding."
            )
            said = True
        await sleep(BACKLOG_POLL_S)


async def pause(seconds: float, ledger: Ledger, sleep=asyncio.sleep) -> bool:
    """The gap between batches. :return: False when a ``STOP`` came."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if ledger.stop_requested():
            return False
        await sleep(min(1.0, end - time.monotonic()))
    return not ledger.stop_requested()


# --- What an apply touches ---


async def _file_state(sample_file_id: str):
    async with async_session() as session:
        return await session.get(SampleFile, sample_file_id)


async def _other_batches(sample_file_id: str) -> dict[str, str]:
    """The batches other than acquisition ones that hold a sample of the
    file, those an apply sets to ``rematch``, and the status of each."""
    async with async_session() as session:
        return dict(
            (
                await session.execute(
                    select(SampleBatch.sample_batch_id, SampleBatch.status)
                    .join(
                        SampleItem,
                        SampleItem.sample_batch_id == SampleBatch.sample_batch_id,
                    )
                    .where(
                        SampleItem.sample_file_id == sample_file_id,
                        SampleBatch.sample_batch_type != "ACQUISITION",
                    )
                    .distinct()
                    .order_by(SampleBatch.sample_batch_id)
                )
            ).all()
        )


async def _assignment_runs(sample_file_id: str) -> list[str]:
    """The peak-assignment runs on the file's samples, which the runner
    leaves as they are (MAS-192) and lists."""
    async with async_session() as session:
        return list(
            (
                await session.execute(
                    select(PeakAssignmentRun.peak_assignment_run_id)
                    .join(
                        SampleItem,
                        SampleItem.sample_item_id == PeakAssignmentRun.sample_item_id,
                    )
                    .where(SampleItem.sample_file_id == sample_file_id)
                    .order_by(PeakAssignmentRun.peak_assignment_run_id)
                )
            )
            .scalars()
            .all()
        )


def _compact(record: dict | None) -> dict | None:
    """A record's parameters, version, status and residuals, for the ledger."""
    if not isinstance(record, dict):
        return None
    quality = record.get("quality") if isinstance(record.get("quality"), dict) else {}
    return {
        "mode": record.get("mode"),
        "par": record.get("par"),
        "status": record_status(record),
        "verified": record.get("verified"),
        "node_version": quality.get("node_version"),
        "collection": quality.get("collection"),
        "n_points": quality.get("n_points"),
        "post_fit_mz_error_ppm": quality.get("post_fit_mz_error_ppm"),
        "pre_fit_mz_error_ppm": quality.get("pre_fit_mz_error_ppm"),
        "residuals": [
            [p.get("ion"), p.get("target_mz"), p.get("residual_ppm"), p.get("retained")]
            for p in quality.get("points") or []
        ],
        "issues": [issue.get("code") for issue in record.get("quality_issues") or []],
    }


def _props_before(filename: str) -> dict:
    """The ``.props`` keys an apply writes, as they are; an absent key is left
    out, and a rollback takes it out again."""
    props = m_io.read_props(filename)
    return {key: props[key] for key in ("mz_calibration", "range") if key in props}


# --- One file, run ---


@dataclass
class Outcome:
    kind: str
    detail: dict = field(default_factory=dict)


async def run_file(
    candidate: Candidate,
    target: ModeTarget,
    ledger: Ledger,
    batch: int,
    edges,
) -> Outcome:
    """Calibrate one file as ingest does, keeping an ``ok`` record from a
    ``poor`` fit, with the ledger written before the apply.

    The file is read again at its turn, which can come a day after the run
    chose it: what it holds then is what the ``ok`` rule judges and what the
    ledger records as before. A file calibrated onto the target since is left.
    """
    stored = await _file_state(candidate.sample_file_id)
    if stored is None:
        return Outcome("gone")
    if on_target(stored.mz_calibration, target.target):
        return Outcome("on target already")
    candidate = replace(candidate, record=stored.mz_calibration, range=stored.range)
    sample = candidate.calibrating[0]
    old_record = copy.deepcopy(candidate.record)
    base = {
        "sample_file_id": candidate.sample_file_id,
        "acquired": candidate.acquired,
        "mode_id": target.mode.ionization_mode_id,
        "mode": target.mode.ionization_mode_name,
        "batch": batch,
        "target": target.target,
    }
    state: dict = {}

    async def judge(fit: dict) -> bool:
        issues = calibration_quality_issues(fit.get("quality"), candidate.filename)
        new_status = "poor" if issues else "ok"
        if record_status(old_record) == "ok" and new_status != "ok":
            ledger.write(
                {
                    "event": "kept",
                    **base,
                    "reason": "an ok record is not replaced by a poor fit",
                    "old": _compact(old_record),
                    "new": _compact({**fit, "status": new_status}),
                    "issues": [issue["code"] for issue in issues],
                }
            )
            state["kept"] = True
            return False
        carried = state.get("apply")
        if carried is not None:
            # An earlier attempt's apply that did not finish may have written
            # the axes and the props: what the file had is that attempt's
            before, plan = carried["before"], carried["restore"]
        else:
            props = await asyncio.to_thread(_props_before, candidate.filename)
            plan, kept = await asyncio.to_thread(
                restore_plan, candidate.filename, props.get("mz_calibration")
            )
            if kept:
                plan["snapshot"] = _snapshot_path(ledger, candidate.sample_file_id)
                await asyncio.to_thread(
                    write_snapshot, ledger.directory, plan["snapshot"], kept
                )
            before = {"record": old_record, "range": candidate.range, "props": props}
        axis, n_samples = await asyncio.to_thread(
            old_axis,
            candidate.filename,
            before["props"].get("mz_calibration"),
            plan["offset"],
        )
        batches = await _other_batches(candidate.sample_file_id)
        # A batch the unfinished attempt held at processing waited on a
        # recalibration if it did when that attempt began
        pending = {b for b, status in batches.items() if status == "recalibrate"}
        pending.update((carried or {}).get("recalibrate") or ())
        state["apply"] = ledger.write(
            {
                "event": "apply",
                **base,
                "before": before,
                "restore": plan,
                "old": _compact(old_record),
                "new": _compact({**fit, "status": new_status}),
                "bands": (axis_change(axis, fit, n_samples, edges) if axis else []),
                "batches": list(batches),
                "recalibrate": sorted(pending),
                "assignment_runs": await _assignment_runs(candidate.sample_file_id),
            }
        )
        return True

    async def before_apply(fit: dict) -> bool:
        # The runner's own failure - a store it cannot read, a ledger or a
        # snapshot it cannot write - withholds the fit, so it never reaches
        # the node as the file's calibration failing: that would log an error
        # and leave a failure marker over a converter record
        try:
            return await judge(fit)
        except Exception as error:  # noqa: BLE001 - the runner's, not the file's
            runtime.logger.opt(exception=True).info(
                f"Recording {candidate.filename} before its apply raised"
            )
            state["hook_error"] = f"{type(error).__name__}: {error}"
            return False

    outcome = await calibrate_with_retry(
        sample={
            "sample_item_id": sample["sample_item_id"],
            "sample_item_name": sample["sample_item_name"],
            "filename": candidate.filename,
        },
        sample_file_id=candidate.sample_file_id,
        # A process of its own, as the pipeline gives each file: without one
        # a fit's warning has no parent to be raised to and never reaches
        # the retry loop
        process_id=gen_id(8),
        before_apply=before_apply,
    )
    stored = await _file_state(candidate.sample_file_id)
    record = stored.mz_calibration if stored else None
    if "apply" in state:
        apply = state["apply"]
        if _same_fit(record, apply["new"]):
            ledger.write(
                {
                    "event": "applied",
                    **base,
                    "apply_seq": apply["seq"],
                    "status": record_status(record),
                    "verified": bool(record.get("verified")),
                }
            )
            return Outcome(record_status(record))
        error = state.get("hook_error") or outcome.reason
        ledger.write(
            {"event": "error", **base, "apply_incomplete": True, "error": error}
        )
        # What the apply may have written is put back now, not by a later run
        await _close_now(apply, ledger)
        return Outcome("error", {"error": error})
    if "hook_error" in state:
        # The next run tries the file again
        ledger.write({"event": "error", **base, "error": state["hook_error"]})
        return Outcome("error", {"error": state["hook_error"]})
    if state.get("kept"):
        return Outcome("kept")
    if outcome.fault:
        # Not the spectrum's: the next run tries the file again
        ledger.write({"event": "error", **base, "error": outcome.reason})
        return Outcome("error", {"error": outcome.reason})
    ledger.write(
        {
            "event": "failed",
            **base,
            "reason": outcome.reason,
            "old": _compact(old_record),
            "record": record_status(record),
        }
    )
    return Outcome("failed", {"reason": outcome.reason})


async def _close_now(apply: dict, ledger: Ledger) -> None:
    """Close an apply that did not land, or leave it for the next run's
    :func:`reconcile` when that fails too."""
    try:
        await close_open_apply(apply, ledger)
    except Exception as error:  # noqa: BLE001 - the next run tries again
        runtime.logger.opt(exception=True).info(
            f"Closing the open apply of {apply['sample_file_id']} raised"
        )
        ledger.write(
            {
                "event": "error",
                "sample_file_id": apply["sample_file_id"],
                "open_apply": apply["seq"],
                "error": f"{type(error).__name__}: {error}",
            }
        )


# --- Matching what was applied ---


async def _acquisition_samples(sample_file_id: str) -> list[str]:
    async with async_session() as session:
        return list(
            (
                await session.execute(
                    select(SampleItem.sample_item_id)
                    .join(
                        SampleBatch,
                        SampleBatch.sample_batch_id == SampleItem.sample_batch_id,
                    )
                    .where(
                        SampleItem.sample_file_id == sample_file_id,
                        SampleBatch.sample_batch_type == "ACQUISITION",
                    )
                    .order_by(SampleItem.sample_item_id)
                )
            )
            .scalars()
            .all()
        )


async def _batches_at_rematch(batch_ids) -> list[str]:
    if not batch_ids:
        return []
    async with async_session() as session:
        return list(
            (
                await session.execute(
                    select(SampleBatch.sample_batch_id)
                    .where(
                        SampleBatch.sample_batch_id.in_(sorted(batch_ids)),
                        SampleBatch.status == "rematch",
                    )
                    .order_by(SampleBatch.sample_batch_id)
                )
            )
            .scalars()
            .all()
        )


async def settle(config: Config, ledger: Ledger) -> dict:
    """Match what the applies and rollbacks cleared, under the backlog guard.

    The file's acquisition samples are matched as ingest matches them, where
    the file's record is verified (``match_compute_sample``); and every other
    batch an apply set to ``rematch`` is rematched, which computes the
    matches its samples of the file lack (``rematch_batch``).

    :return: Counts, and the batches still at ``rematch``.
    """
    counts = Counter()
    for event in ledger.unmatched():
        if not await hold_for_backlog(config, ledger):
            counts["stopped"] += 1
            break
        sample_file_id = event["sample_file_id"]
        stored = await _file_state(sample_file_id)
        if stored is None:
            ledger.write(
                {"event": "matched", "sample_file_id": sample_file_id, "gone": True}
            )
            continue
        samples = await _acquisition_samples(sample_file_id)
        matched, failed = [], {}
        if _is_verified_record(stored.mz_calibration):
            for sample_item_id in samples:
                try:
                    await match_compute_sample(
                        sample_item_id=sample_item_id,
                        independent_transaction=False,
                        process_id=gen_id(8),
                    )
                    matched.append(sample_item_id)
                except Exception as error:  # noqa: BLE001 - one sample's failure
                    failed[sample_item_id] = f"{type(error).__name__}: {error}"
        ledger.write(
            {
                "event": "matched",
                "sample_file_id": sample_file_id,
                "matched": matched,
                "failed": failed,
                "unverified": not _is_verified_record(stored.mz_calibration),
            }
        )
        counts["files"] += 1
        counts["samples"] += len(matched)
        counts["failed"] += len(failed)

    for sample_batch_id in await _batches_at_rematch(ledger.touched_batches()):
        if counts["stopped"] or not await hold_for_backlog(config, ledger):
            counts["stopped"] += 1
            break
        try:
            result = await rematch_batch(
                sample_batch_id=sample_batch_id,
                full_remove=False,
                independent_transaction=False,
                process_id=gen_id(8),
            )
            status = (result or {}).get("status")
        except Exception as error:  # noqa: BLE001 - one batch's failure
            status = f"raised {type(error).__name__}"
        ledger.write(
            {"event": "rematched", "sample_batch_id": sample_batch_id, "status": status}
        )
        counts["batches"] += 1
    left = await _batches_at_rematch(ledger.touched_batches())
    return {**counts, "left_at_rematch": left}


# --- Rollback ---


class CannotRestore(Exception):
    """A file's axes cannot all be given back as the ledger recorded them."""


def restore_axes(
    filename: str, before: dict, plan: dict, kept: dict
) -> tuple[dict[str, np.ndarray], np.ndarray | None]:
    """Every m/z axis of the file as it was before the apply, and its peaks',
    decided before any is written.

    A store the ledger kept takes the kept array; any other takes the old
    record's axis from the plan's offset. A store made since the apply, such
    as the ``sum_signal_<hash>`` cache a time window of the file leaves, is in
    neither when no offset gives the axes back: it takes the file's axis the
    ledger kept, which is what a cache is on, when one kept axis has its size.

    Synchronous: it reads the filestore.

    :raises CannotRestore: On a store no axis can be decided for, before
        anything is written.
    """
    record = before["props"].get("mz_calibration") or {}
    offset = plan.get("offset")
    kept_axes = [kept[name] for name in sorted(kept) if name != "peak_timeseries"]
    axes = {}
    for var in _axis_vars(filename):
        if var in kept:
            axes[var] = kept[var]
            continue
        size = m_io.load_coord(filename, var, "mz").size
        if offset is not None:
            axes[var] = _formula(np.arange(size), record, offset)
            continue
        same = [axis for axis in kept_axes if axis.size == size]
        if not same or any(not np.array_equal(same[0], axis) for axis in same[1:]):
            raise CannotRestore(
                f"the store {var} was made after the apply, and no axis the "
                "ledger kept is its size"
            )
        axes[var] = same[0]
    try:
        tofs = m_io.load_coord(filename, "peak_timeseries", "tof")
    except FileNotFoundError:
        tofs = None
    peaks = None
    if tofs is not None:
        if "peak_timeseries" in kept:
            peaks = kept["peak_timeseries"]
        elif offset is not None:
            peaks = _formula(tofs, record, offset)
        else:
            raise CannotRestore(
                "the peaks were stored after the apply, and no offset gives their m/z"
            )
    return axes, peaks


class RestoreHandler(TofCalibrationHandler):
    """Writes a file's m/z axes and ``.props`` back as the ledger recorded
    them, under the locks an apply holds (``_guarded_apply``)."""

    def __init__(self, filename: str, before: dict, plan: dict, kept: dict):
        super().__init__(filename, None, None)
        self._before = before
        self._plan = plan
        self._kept = kept

    def _apply_sync(self, fit: dict):
        # Every axis decided under the lock before the first is written, so a
        # store that cannot be given back leaves the file as it is
        axes, peaks = restore_axes(self.filename, self._before, self._plan, self._kept)
        for var, axis in axes.items():
            m_io.update_zarr_array_coord(self.filename, var, "mz", axis)
        if peaks is not None:
            m_io.update_zarr_array_coord(self.filename, "peak_timeseries", "mz", peaks)
        props_before = self._before["props"]
        path = os.path.join(
            m_name.parse_path_from_item_filename(self.filename), ".props"
        )
        props = m_io.read_props(self.filename)
        for key in ("mz_calibration", "range"):
            if key in props_before:
                props[key] = props_before[key]
            else:
                props.pop(key, None)
        write_json(path, props, indent=4)


def _same_fit(record: dict | None, other: dict | None) -> bool:
    """Whether two records hold the same fit: the same parameters, fitted by
    the same node version from the same collection.

    The parameters alone do not tell an apply that landed from one that did
    not: a refit from the same peaks and calibrants gives the parameters the
    file already had, and only the stamp of the collection or the node it was
    made by is new. Either may be a full record, whose stamp is in its
    ``quality``, or the ledger's compact one (:func:`_compact`).
    """

    def fingerprint(value):
        # No record, and a failure marker the node made of none, hold none
        value = value if isinstance(value, dict) else {}
        stamp = value["quality"] if isinstance(value.get("quality"), dict) else value
        return (
            value.get("mode"),
            value.get("par"),
            stamp.get("collection"),
            stamp.get("node_version"),
        )

    return fingerprint(record) == fingerprint(other)


def _kept_arrays(ledger: Ledger, plan: dict) -> dict:
    if not plan.get("snapshot"):
        return {}
    with np.load(os.path.join(ledger.directory, plan["snapshot"])) as arrays:
        return {name: arrays[name] for name in arrays.files}


async def _held_batches(sample_file_id: str, pending_before=()):
    """The file's samples, the batches other than acquisition ones that hold
    them, and of those the ones that wait on a recalibration: at
    ``recalibrate`` now, or at ``processing`` and at ``recalibrate`` when the
    apply began (``pending_before``)."""
    affected = await fetch_affected_sample_data(
        sample_file_ids=[sample_file_id], include_objects=True
    )
    batches = [
        batch
        for batch in affected.affected_sample_batches or []
        if batch.sample_batch_type != "ACQUISITION"
    ]
    pending = [
        batch.sample_batch_id
        for batch in batches
        if batch.status == "recalibrate"
        or (batch.status == "processing" and batch.sample_batch_id in pending_before)
    ]
    return affected, [batch.sample_batch_id for batch in batches], pending


async def _release(held: list[str], pending: list[str]) -> None:
    """The batches back to waiting, as an apply leaves them: a pending
    recalibration kept, every other at ``rematch``."""
    if pending:
        await update_sample_batch_status(
            sample_batch_ids=pending, status="recalibrate", independent_transaction=True
        )
    await update_sample_batch_status(
        sample_batch_ids=held,
        status="rematch",
        independent_transaction=True,
        never_lower=True,
    )


async def _remove_matches(affected) -> None:
    for sample in affected.affected_samples or []:
        await match_remove_sample(
            sample_item_id=sample.sample_item_id,
            full_remove=True,
            independent_transaction=False,
            process_id=gen_id(8),
        )


async def _write_back(
    sample_file, event: dict, ledger: Ledger, *, remove_matches: bool
) -> list[str]:
    """Write back what the file had before the apply the event records: its
    m/z axes and ``.props``, then its database record and range.

    Its batches other than acquisition ones are held at ``processing`` while
    it does, and go to ``rematch`` after, as around an apply
    (``calibration_mz_apply``). Not through the apply itself: it judges and
    stamps every fit it is given, and this writes the old record back as it
    was.

    :param remove_matches: Whether the file's samples lose their matches: they
        were made on the axes being taken back.
    :raises CannotRestore: Before anything is touched.
    :return: The batches it set to ``rematch``.
    """
    plan, before = event["restore"], event["before"]
    kept = await asyncio.to_thread(_kept_arrays, ledger, plan)
    # Decided before the batches are held: a refusal touches nothing
    await asyncio.to_thread(restore_axes, sample_file.filename, before, plan, kept)
    affected, held, pending = await _held_batches(
        sample_file.sample_file_id, event.get("recalibrate") or ()
    )
    await update_sample_batch_status(
        sample_batch_ids=held, status="processing", independent_transaction=True
    )
    try:
        await RestoreHandler(sample_file.filename, before, plan, kept).apply({})
        sample_file.mz_calibration = copy.deepcopy(before["record"])
        sample_file.range = copy.deepcopy(before["range"])
        await update_sample_file(
            sample_file.sample_file_id, SampleFileUpdate(**sample_file.to_dict())
        )
        if remove_matches:
            await _remove_matches(affected)
    finally:
        await _release(held, pending)
    return held


async def rollback_file(event: dict, ledger: Ledger) -> Outcome:
    """Put back what a file had before the apply the event records.

    The file's samples lose their matches and its batches other than
    acquisition ones go to ``rematch``, as after an apply; :func:`settle` then
    matches them again.
    """
    sample_file_id = event["sample_file_id"]
    sample_file = await fetch_sample_file(sample_file_id=sample_file_id)
    reason = None
    if not _same_fit(sample_file.mz_calibration, event.get("new")):
        reason = "the record changed since the runner applied it"
    else:
        try:
            held = await _write_back(sample_file, event, ledger, remove_matches=True)
        except CannotRestore as error:
            reason = str(error)
    if reason is not None:
        ledger.write(
            {
                "event": "rollback_refused",
                "sample_file_id": sample_file_id,
                "apply_seq": event["seq"],
                "reason": reason,
            }
        )
        return Outcome("refused", {"reason": reason})
    ledger.write(
        {
            "event": "rollback",
            "sample_file_id": sample_file_id,
            "apply_seq": event["seq"],
            "of_batch": event.get("batch"),
            "mode_id": event.get("mode_id"),
            "batches": held,
        }
    )
    return Outcome("rolled_back")


# --- An apply a run left open ---


async def close_open_apply(event: dict, ledger: Ledger) -> str:
    """Bring a file whose apply no outcome followed to a state the ledger
    knows, by what its database record holds now.

    - The apply's fit: the apply landed, and the run was cut off before it
      said so. What the apply had left undone after its record - the
      samples' matches removed, the batches at ``rematch`` - is done, and the
      apply is ``applied``: matched by :func:`settle`, and a rollback can
      undo it.
    - What the file had before: the apply did not land, but may have written
      some of the axes and the ``.props``. They are put back as the ledger
      recorded them, and the record too, over a failure marker the node may
      have left on a converter record (``restored``). The samples' matches
      are on those axes, and are kept. The file is then offered again.
    - Anything else: an apply since, by a person or a re-process, rewrote
      every axis of the file and its record, and the open apply is
      ``superseded``.

    :return: Which of them, or ``left open`` with the reason logged and in
        the ledger when the axes cannot be given back.
    """
    sample_file_id = event["sample_file_id"]
    ids = {
        "sample_file_id": sample_file_id,
        "apply_seq": event["seq"],
        "mode_id": event.get("mode_id"),
        "target": event.get("target"),
        "batch": event.get("batch"),
    }
    sample_file = await _file_state(sample_file_id)
    if sample_file is None:
        ledger.write({"event": "superseded", **ids, "reason": "the file is gone"})
        return "superseded"
    record = sample_file.mz_calibration
    if _same_fit(record, event.get("new")):
        affected, held, pending = await _held_batches(
            sample_file_id, event.get("recalibrate") or ()
        )
        try:
            await _remove_matches(affected)
        finally:
            await _release(held, pending)
        ledger.write(
            {
                "event": "applied",
                **ids,
                "status": record_status(record),
                "verified": bool(record.get("verified")),
                "reconciled": True,
            }
        )
        return "applied"
    if _same_fit(record, event["before"]["record"]):
        try:
            held = await _write_back(sample_file, event, ledger, remove_matches=False)
        except CannotRestore as error:
            runtime.logger.info(
                f"The open apply of {sample_file.filename} is left open: {error}."
            )
            ledger.write({"event": "restore_refused", **ids, "reason": str(error)})
            return "left open"
        ledger.write({"event": "restored", **ids, "batches": held})
        return "restored"
    ledger.write(
        {
            "event": "superseded",
            **ids,
            "reason": "another apply replaced the record since",
            "record": _compact(record),
        }
    )
    return "superseded"


async def reconcile(ledger: Ledger) -> Counter:
    """Close every apply the ledger has open (:func:`close_open_apply`), as
    the first thing a run or a rollback does."""
    counts = Counter()
    for event in list(ledger.open_applies().values()):
        try:
            counts[await close_open_apply(event, ledger)] += 1
        except Exception as error:  # noqa: BLE001 - one file's failure
            runtime.logger.opt(exception=True).info(
                f"Closing the open apply of {event['sample_file_id']} raised"
            )
            ledger.write(
                {
                    "event": "error",
                    "sample_file_id": event["sample_file_id"],
                    "open_apply": event["seq"],
                    "error": f"{type(error).__name__}: {error}",
                }
            )
            counts["left open"] += 1
    return counts


def rollback_events(ledger: Ledger, selection: tuple[str, ...]) -> list[dict]:
    """The apply events to undo: each named file's last, or a batch's."""
    wanted_files: set[str] = set()
    wanted_batches: set[int] = set()
    for item in selection:
        kind, _, value = item.partition(":")
        if kind == "file":
            wanted_files.add(value)
        else:
            wanted_batches.add(int(value))
    candidates = {
        event["sample_file_id"]
        for event in ledger.events
        if event.get("event") == "apply"
        and (
            event["sample_file_id"] in wanted_files
            or event.get("batch") in wanted_batches
        )
    }
    events = []
    for sample_file_id in sorted(candidates):
        last = ledger.last_applied(sample_file_id)
        if last is None:
            continue
        if sample_file_id in wanted_files or last.get("batch") in wanted_batches:
            events.append(last)
    return events


# --- The runs ---


@dataclass
class Report:
    action: str
    instrument: str
    counts: Counter = field(default_factory=Counter)
    #: Files of modes without calibrants, which wait for calibration transfer
    no_calibrants: int = 0
    files: list = field(default_factory=list)
    #: Per band: each file's median change
    bands: dict = field(default_factory=lambda: defaultdict(list))
    assignment_runs: int = 0
    #: How the applies an earlier run left open were closed
    reconciled: Counter = field(default_factory=Counter)
    settle: dict | None = None
    batches: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "action": self.action,
            "instrument": self.instrument,
            "counts": dict(self.counts),
            "no_calibrants": self.no_calibrants,
            "bands": {
                band: audit.spread(values) for band, values in self.bands.items()
            },
            "assignment_runs": self.assignment_runs,
            "reconciled": dict(self.reconciled),
            "settle": self.settle,
            "batches": self.batches,
            "files": self.files,
        }


async def _targets(candidates: list[Candidate], config: Config) -> dict:
    """Each fittable mode's target, prepared once."""
    targets: dict[str, ModeTarget] = {}
    for candidate in candidates:
        mode_id = candidate.mode_id
        if mode_id is None or mode_id in targets:
            continue
        targets[mode_id] = await prepare_target(mode_id, candidate.filename)
    if config.mode is not None:
        targets = {
            mode_id: target
            for mode_id, target in targets.items()
            if target.mode.ionization_mode_name == config.mode
        }
    return targets


def _why_not_fitted(candidate: Candidate) -> str | None:
    """Why ingest would not calibrate the file, if it would not."""
    if not candidate.samples:
        return "no sample"
    if len(candidate.calibrating) > 1:
        return "more than one sample to calibrate"
    if candidate.instrument_function_id is None:
        return "a blank: no instrument functions"
    return None


def _refit_ready(filename: str) -> str | None:
    """Why the file's stores cannot be refitted, if they cannot."""
    path = m_name.parse_path_from_item_filename(filename)
    if not os.path.isdir(path):
        return "no directory"
    if not os.path.isfile(os.path.join(path, ".props")):
        return "no .props"
    if not os.path.isdir(os.path.join(path, "peak_timeseries.zarr")):
        return "no peak store"
    if not any(var.startswith("sum_signal") for var in m_io.get_file_data_vars(path)):
        return "no sum signal"
    return None


async def run_runner(config: Config) -> Report:
    """A dry run or a run over the instrument's files."""
    writable = config.action == RUN
    ledger = Ledger(config.ledger_dir, writable=writable)
    report = Report(action=config.action, instrument=config.instrument)
    log = runtime.logger.info

    if writable:
        # What a stopped run left goes first: its open applies closed, then
        # what it left unmatched matched
        report.reconciled = await reconcile(ledger)
        report.settle = await settle(config, ledger)

    candidates = await select_candidates(config)
    targets = await _targets(candidates, config)
    target_of = {mode_id: t.target for mode_id, t in targets.items()}
    settled = set() if config.retry else ledger.settled(target_of.get)
    still_open = ledger.open_applies()
    log(
        f"TOF re-calibration of {config.instrument} ({config.action}): "
        f"{len(candidates)} TOF file(s)"
        + (
            f", acquired {config.first_day} to {config.last_day}"
            if config.first_day or config.last_day
            else ""
        )
        + f"; ledger {ledger.path}."
    )

    queue: list[tuple[Candidate, ModeTarget]] = []
    for candidate in candidates:
        if candidate.samples and not candidate.calibrating:
            report.no_calibrants += 1
            continue
        why = _why_not_fitted(candidate)
        mode_id = candidate.mode_id
        if why is None and mode_id not in targets:
            # Another mode than the one asked for
            continue
        if why is None and targets[mode_id].target is None:
            why = "the mode's collection offers no isotopes"
        if why is not None:
            report.counts[f"not fitted by ingest: {why}"] += 1
            continue
        target = targets[mode_id]
        if candidate.sample_file_id in still_open:
            # One the run could not close: the log and the ledger say why
            report.counts["an apply left open"] += 1
            continue
        if on_target(candidate.record, target.target):
            report.counts["on target already"] += 1
            continue
        if candidate.sample_file_id in settled:
            report.counts["in the ledger already"] += 1
            continue
        # Before the batches, so a file with nothing to refit takes no turn
        # in them, nor a gap
        if (
            why := await asyncio.to_thread(_refit_ready, candidate.filename)
        ) is not None:
            report.counts[f"not refit-ready: {why}"] += 1
            if writable:
                ledger.write(
                    {
                        "event": "skipped",
                        "sample_file_id": candidate.sample_file_id,
                        "mode_id": target.mode.ionization_mode_id,
                        "target": target.target,
                        "reason": f"not refit-ready: {why}",
                    }
                )
            else:
                report.files.append(
                    {
                        "sample_file_id": candidate.sample_file_id,
                        "outcome": f"not refit-ready: {why}",
                    }
                )
            continue
        queue.append((candidate, target))
    if config.limit:
        report.counts["left for a later run"] += max(0, len(queue) - config.limit)
        queue = queue[: config.limit]
    log(
        f"  {len(queue)} file(s) to {'refit' if writable else 'read'}; "
        f"{report.no_calibrants} in modes without calibrants wait for "
        "calibration transfer."
    )

    batch = ledger.next_batch() if writable else 1
    for start in range(0, len(queue), config.batch_size):
        chunk = queue[start : start + config.batch_size]
        if start and writable and not await pause(config.gap_s, ledger):
            report.counts["stopped"] += 1
            break
        stopped = False
        taken = 0
        for candidate, target in chunk:
            if ledger.stop_requested() or not await hold_for_backlog(config, ledger):
                stopped = True
                break
            taken += 1
            if writable:
                outcome = await _run_one(candidate, target, ledger, batch, config)
            else:
                outcome = await _dry_one(candidate, target, config, report)
            report.counts[outcome.kind] += 1
        if writable:
            report.batches.append(batch)
            settled_now = await settle(config, ledger)
            report.settle = _merge_settle(report.settle, settled_now)
            log(
                f"  batch {batch}: {taken} file(s); "
                + ", ".join(f"{k} {v}" for k, v in sorted(report.counts.items()))
            )
            batch += 1
        if stopped:
            report.counts["stopped"] += 1
            break

    if writable:
        for event in ledger.events:
            if event.get("event") == "apply" and event.get("batch") in report.batches:
                report.assignment_runs += len(event.get("assignment_runs") or [])
                for band in event.get("bands") or []:
                    report.bands[band["band"]].append(band["median"])
    return report


def _merge_settle(total: dict | None, more: dict) -> dict:
    merged = dict(total or {})
    for key, value in more.items():
        if key == "left_at_rematch":
            merged[key] = value
        else:
            merged[key] = merged.get(key, 0) + value
    return merged


async def _run_one(candidate, target, ledger, batch, config) -> Outcome:
    try:
        return await run_file(candidate, target, ledger, batch, config.band_edges)
    except Exception as error:  # noqa: BLE001 - one file's failure
        runtime.logger.opt(exception=True).info(
            f"Re-calibrating {candidate.filename} raised"
        )
        ledger.write(
            {
                "event": "error",
                "sample_file_id": candidate.sample_file_id,
                "mode_id": target.mode.ionization_mode_id,
                "batch": batch,
                "error": f"{type(error).__name__}: {error}",
                "apply_incomplete": ledger.open_apply(candidate.sample_file_id)
                is not None,
            }
        )
        return Outcome("error")


async def _dry_one(candidate, target, config, report) -> Outcome:
    entry = {
        "sample_file_id": candidate.sample_file_id,
        "acquired": candidate.acquired.isoformat(),
        "mode": target.mode.ionization_mode_name,
        "old": record_status(candidate.record),
    }
    try:
        result = await dry_fit(candidate, target)
    except Exception as error:  # noqa: BLE001 - one file's failure
        entry["outcome"] = "error"
        entry["error"] = type(error).__name__
        report.files.append(entry)
        return Outcome("error")
    if result.fit is None:
        entry["outcome"] = "would fail"
        entry["reason"] = result.reason
        report.files.append(entry)
        return Outcome("would fail")
    new_status = "poor" if result.issues else "ok"
    entry["new"] = {"mode": result.fit["mode"], "par": result.fit["par"]}
    entry["attempts"] = result.attempts
    if record_status(candidate.record) == "ok" and new_status != "ok":
        entry["outcome"] = "would keep its ok record"
        entry["issues"] = [issue["code"] for issue in result.issues]
        report.files.append(entry)
        return Outcome(entry["outcome"])
    props_record = (await asyncio.to_thread(m_io.read_props, candidate.filename)).get(
        "mz_calibration"
    )
    plan, _ = await asyncio.to_thread(restore_plan, candidate.filename, props_record)
    axis, n_samples = await asyncio.to_thread(
        old_axis, candidate.filename, props_record, plan["offset"]
    )
    entry["bands"] = (
        axis_change(axis, result.fit, n_samples, config.band_edges) if axis else []
    )
    for band in entry["bands"]:
        report.bands[band["band"]].append(band["median"])
    entry["outcome"] = f"would apply {new_status}"
    entry["assignment_runs"] = len(await _assignment_runs(candidate.sample_file_id))
    report.assignment_runs += entry["assignment_runs"]
    report.files.append(entry)
    return Outcome(entry["outcome"])


async def run_rollback(config: Config) -> Report:
    ledger = Ledger(config.ledger_dir, writable=True)
    report = Report(action=ROLLBACK, instrument=config.instrument)
    # A file a run left half-applied is closed first, so the last completed
    # apply of each file is the one to undo
    report.reconciled = await reconcile(ledger)
    events = rollback_events(ledger, config.rollback)
    runtime.logger.info(
        f"Rolling back {len(events)} file(s) of {config.instrument} from {ledger.path}."
    )
    for event in events:
        try:
            outcome = await rollback_file(event, ledger)
        except Exception as error:  # noqa: BLE001 - one file's failure
            runtime.logger.opt(exception=True).info(
                f"Rolling back {event['sample_file_id']} raised"
            )
            ledger.write(
                {
                    "event": "error",
                    "sample_file_id": event["sample_file_id"],
                    "rollback_of": event["seq"],
                    "error": f"{type(error).__name__}: {error}",
                }
            )
            outcome = Outcome("error")
        report.counts[outcome.kind] += 1
    report.settle = await settle(config, ledger)
    return report


def log_report(report: Report) -> None:
    log = runtime.logger.info
    log("=" * 80)
    log(f"TOF RE-CALIBRATION ({report.action.upper()}) of {report.instrument}")
    for kind, count in sorted(report.reconciled.items()):
        log(f"  an apply an earlier run left open, now {kind}: {count}")
    for kind, count in sorted(report.counts.items()):
        log(f"  {kind}: {count}")
    if report.action != ROLLBACK:
        log(
            f"  in modes without calibrants, left for calibration transfer: "
            f"{report.no_calibrants}"
        )
        log(
            f"  peak-assignment runs on the files, left as they are: "
            f"{report.assignment_runs}"
        )
    if report.bands:
        log("  Axis change per band, ppm: median of the files' medians [IQR], files")
        for band, values in report.bands.items():
            stats = audit.spread(values)
            if stats:
                log(
                    f"    {band:>9}: {stats['median']:+.2f} "
                    f"[{stats['q25']:+.2f}, {stats['q75']:+.2f}], {stats['n']}"
                )
    if report.settle:
        left = report.settle.get("left_at_rematch") or []
        log(
            f"  Matched {report.settle.get('samples', 0)} acquisition sample(s) of "
            f"{report.settle.get('files', 0)} file(s); rematched "
            f"{report.settle.get('batches', 0)} batch(es); "
            f"{len(left)} touched batch(es) still at rematch"
            + (f": {', '.join(left)}" if left else "")
        )
    log("=" * 80)


async def run() -> None:
    config = read_config()
    await configure_database_engine()
    # Nobody asked for this run in the app, so nobody is told: what the
    # controllers report goes to the rooms of the pages open on it, if any
    with without_audience():
        if config.action == ROLLBACK:
            report = await run_rollback(config)
        else:
            report = await run_runner(config)
    log_report(report)
    if config.as_json:
        await runtime.logger.complete()
        sys.stdout.write(json.dumps(report.as_dict(), default=_json_default) + "\n")
        sys.stdout.flush()


def main() -> None:
    """Entry point for ``mascope dev|prod db script run``."""
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        runtime.logger.info("Cancelled by user (Ctrl+C)")
    except Exception as e:
        runtime.logger.exception(f"Script failed: {e}")
        raise


if __name__ == "__main__":
    main()
