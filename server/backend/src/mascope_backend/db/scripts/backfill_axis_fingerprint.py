"""
Maintenance script to compute the axis fingerprint of Orbitrap files that
carry none, or carry one computed otherwise.

``.props["axis_fingerprint"]`` is the m/z offset a raw Orbitrap file's mass
calibration stored in each frequency band of its axis, read back from the
stored profile (``mascope_thermo.axis_fingerprint``). The file converter
computes it as it converts a file, and nothing computes it afterwards: the
routes serve a file that carries none the ``missing`` document, and never
compute one on request, since opening a raw file and running a second of
numpy per GET is a load problem for a web worker.

So every file converted before the fingerprint shipped, or while a
deployment set ``[backend] axis_fingerprint = "off"``, carries none. The raw
data is kept beside the converted sample, so this reopens each such file and
writes its document. Nothing is re-converted and no database row changes:
``POST /api/sample/files/reprocess`` is not the way to do this, since it
deletes a file's items and runs auto-processing again.

A file is (re)computed when its props hold:

* no document, the ordinary case;
* a document of status ``unavailable`` (the reader of the day could not read
  the stored points) or ``error`` (the computation failed);
* a document whose ``version`` or ``extractor.params`` differ from what this
  build computes, so a change of the algorithm reaches the files computed
  before it.

**Orbitrap files only** (``sample_file.instrument_type = 'orbi'``): no other
instrument has a fingerprint. And only under a reader that can compute one,
OpenTFRaw 1.4.1 or later: under any other every document would come out
``unavailable``, so the run refuses to start rather than write those.

Reading a raw file per document is slow, so the run is **bounded and
resumable**, as the scan-stream census backfill is: a file whose document is
current is never reopened, ``AXF_LIMIT`` caps how many files one run reads,
and every attempt that left no usable document is recorded in the file's
props so the next run moves past it instead of retrying the same head of the
list forever. Run it again until it reports nothing left to do.
``AXF_RETRY=1`` clears that memory and tries the failures again, after
whatever made them fail has been dealt with.

A backfilled document is the one the converter would have stored but for
``source.raw_name``: the converter names the file it was uploaded as, a name
no record keeps, and this names the file it reads, the sample's ``data.raw``.
A client tells files apart by ``source.raw_sha1_first_mib`` and
``raw_size_bytes``, which are the same either way.

``DRY_RUN=1`` reports what the run would do - the counts all come from the
survey - and computes a handful of documents to show what they hold, writing
nothing.

Usage:
    mascope dev db script run backfill_axis_fingerprint
    mascope prod db script run backfill_axis_fingerprint

    DRY_RUN=1 mascope prod db script run backfill_axis_fingerprint
    AXF_LIMIT=5000 mascope prod db script run backfill_axis_fingerprint
    AXF_RETRY=1 mascope prod db script run backfill_axis_fingerprint

Date: 2026-10-26
"""

import asyncio
import os
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import text

import mascope_file.io as m_io
import mascope_file.name as m_name
from mascope_backend.db import async_session, configure_database_engine
from mascope_backend.runtime import runtime
from mascope_thermo import axis_fingerprint as m_axf
from mascope_thermo.axis_fingerprint import reference
from mascope_thermo.backend import backend_name, open_backend
from mascope_thermo.thermo import NoScansFoundError


#: The props field holding the document, as ``SampleFileProps`` writes it and
#: the axis fingerprint routes read it.
DOCUMENT_FIELD = "axis_fingerprint"

#: The props field recording an attempt that left no usable document, so that
#: a bounded run makes progress instead of reopening the same files. Written
#: by this script alone; nothing reads it but this script.
ATTEMPT_FIELD = "axis_fingerprint_backfill"

#: The instrument class whose files have a fingerprint.
ORBITRAP = "orbi"

#: The statuses of a document computed in full. A stored document of any
#: other status - ``unavailable`` (the reader could not read the stored
#: points) or ``error`` (the computation failed) - is computed again.
DESCRIBED = ("ok", "no_fingerprint")

#: Props read at once while surveying. Each is a small local JSON load, so
#: this bounds open descriptors rather than speed.
_PROPS_CONCURRENCY = 16

#: Raw files read at once. A document reads every scan's trailer and filter
#: and the profile of up to eight scans per mode, then runs about a second of
#: numpy, so this is about the load the run puts on a server that is also
#: ingesting, and is deliberately modest.
_READ_CONCURRENCY = 2

#: Files listed individually in the report.
_PREVIEW_LIMIT = 20

#: Files a dry run actually computes, to show what a document holds.
_DRY_RUN_SAMPLE = 5

#: Reader failures logged with a traceback.
_TRACEBACK_LIMIT = 3

#: A progress line every this many files read.
_PROGRESS_EVERY = 200

#: Files surveyed, and read, per gathered batch: what is pending at once, as
#: in the census backfill.
_SURVEY_BATCH = 2000
_READ_BATCH = 100

#: Every Orbitrap file, newest first: a bounded run reaches the files a
#: client is most likely to ask about. A run carried through to the end
#: reaches the same state whichever way it goes.
_CANDIDATES_SQL = """
    SELECT sf.sample_file_id AS sample_file_id, sf.filename AS filename
    FROM sample_file sf
    WHERE sf.instrument_type = :instrument_type
    ORDER BY sf.datetime_utc DESC, sf.filename DESC
"""


class BadLimit(ValueError):
    """``AXF_LIMIT`` was set to something that is not a bound."""


class NoReader(RuntimeError):
    """The reader backend cannot compute a fingerprint."""


async def _candidates() -> list[dict]:
    """Every Orbitrap file, newest first.

    :return: One dict per file: sample_file_id and filename.
    :rtype: list[dict]
    """
    async with async_session() as session:
        result = await session.execute(
            text(_CANDIDATES_SQL), {"instrument_type": ORBITRAP}
        )
        return [dict(row._mapping) for row in result]


def why_compute(props: dict) -> str | None:
    """Why the document in ``props`` should be computed, or None when it is
    current.

    :param props: A sample file's ``.props``.
    :return: ``"missing"`` when there is no document, ``"status"`` when it is
        ``unavailable`` or ``error`` (or a status no stored document has),
        ``"version"`` when another version of the algorithm computed it,
        ``"params"`` when it ran with other parameters; None otherwise.
    :rtype: str | None
    """
    document = props.get(DOCUMENT_FIELD)
    if not isinstance(document, dict) or not document:
        return "missing"
    if document.get("status") not in DESCRIBED:
        return "status"
    if document.get("version") != reference.VERSION:
        return "version"
    if (document.get("extractor") or {}).get("params") != m_axf.PARAMS:
        return "params"
    return None


def _attempted(props: dict) -> bool:
    """Whether a previous run already tried this file and got no document."""
    return bool(props.get(ATTEMPT_FIELD))


def _read_props(filename: str) -> dict | None:
    """One sample file's ``.props``, or None when there are none to read."""
    try:
        return m_io.read_props(filename)
    except Exception:  # noqa: BLE001
        return None


def _has_raw(filename: str) -> bool:
    """Whether the sample still holds the raw file the document is read from,
    asked without opening it."""
    try:
        return m_name.get_sample_file_type(filename).endswith("_raw")
    except Exception:  # noqa: BLE001
        return False


def _reader() -> str:
    """The reader backend the documents are computed under.

    :return: Its name, as ``MASCOPE_THERMO_BACKEND`` gives it.
    :raises NoReader: When it cannot compute a fingerprint: any reader but
        OpenTFRaw 1.4.1 or later. Every document would be ``unavailable``,
        and writing those would only record that this server cannot.
    """
    name = backend_name()
    if name != m_axf.OPENTFRAW:
        raise NoReader(
            f"The axis fingerprint is computed on OpenTFRaw's profile, and this "
            f"server reads raw files with the {name!r} backend "
            f"(MASCOPE_THERMO_BACKEND). Nothing was read."
        )
    import opentfraw

    if not reference.opentfraw_version_ok(opentfraw.__version__):
        raise NoReader(
            f"OpenTFRaw {opentfraw.__version__} returns no corrected profile "
            f"m/z, and the axis fingerprint needs 1.4.1 or later. Nothing was "
            f"read."
        )
    return name


def _compute(
    filename: str, reader: str
) -> tuple[dict | None, str | None, Exception | None]:
    """The axis fingerprint of one file, read from its raw data.

    :param filename: Sample file name (base, not full path).
    :param reader: The reader backend, from :func:`_reader`.
    :return: ``(document, None, None)`` for any document the library hands
        back, an ``error`` one included; or ``(None, reason, error)`` where
        reason is ``"no_raw"`` when the raw data is not there to read,
        ``"empty"`` when the file holds no scans and ``"reader"`` when the
        reader refused it.
    :rtype: tuple[dict | None, str | None, Exception | None]
    """
    try:
        path = m_name.filename_to_datafile_path(filename)
    except FileNotFoundError:
        return None, "no_raw", None
    if not os.path.isfile(path):
        return None, "no_raw", None
    try:
        with open_backend(path) as backend:
            return (
                m_axf.compute_axis_fingerprint(backend, reader_name=reader),
                None,
                None,
            )
    except NoScansFoundError:
        return None, "empty", None
    except Exception as exc:  # noqa: BLE001
        return None, "reader", exc


def _write_document(filename: str, document: dict) -> None:
    """Store a computed document, leaving the rest of the props.

    Clears any attempt a previous run recorded, so a retried file does not
    keep a note of a failure next to the document that replaced it.
    """
    m_io.update_props(filename, {DOCUMENT_FIELD: document, ATTEMPT_FIELD: None})


def _write_error(filename: str, document: dict) -> None:
    """Store an ``error`` document and record the attempt in one write.

    The document is what the routes serve, and says more than ``missing``
    does; the attempt is what moves the next run past the file, which an
    ``error`` document alone would not: its status asks for it to be computed
    again.
    """
    m_io.update_props(
        filename, {DOCUMENT_FIELD: document, ATTEMPT_FIELD: _attempt("error")}
    )


def _write_attempt(filename: str, result: str) -> None:
    """Record that this file was read and left no document."""
    m_io.update_props(filename, {ATTEMPT_FIELD: _attempt(result)})


def _attempt(result: str) -> dict:
    return {"at": datetime.now(timezone.utc).isoformat(), "result": result}


async def _survey(
    candidates: list[dict],
    read_props: Callable[[str], dict | None],
    has_raw: Callable[[str], bool],
    limit: int | None = None,
    retry: bool = False,
) -> tuple[list[str], dict[str, int]]:
    """The files to read, and counts for the candidates left out.

    Stops as soon as ``limit`` files have been found, between batches, so
    repeated capped runs do not each read every props on the server.

    :param candidates: Rows from :func:`_candidates`, best first.
    :param read_props: Reads a file's props: :func:`_read_props`, or a
        stand-in.
    :param has_raw: Whether the raw data is still there: :func:`_has_raw`,
        or a stand-in.
    :param limit: Stop once this many files are found; None surveys them all.
    :param retry: Include files a previous run tried and got no document from.
    :return: The filenames to read, best first, and the counts: ``surveyed``,
        ``current``, ``no_props``, ``no_raw``, ``already_tried``, and one
        ``why_<reason>`` per reason of :func:`why_compute` among the files to
        read.
    :rtype: tuple[list[str], dict[str, int]]
    """
    gate = asyncio.Semaphore(_PROPS_CONCURRENCY)

    async def one(filename: str) -> tuple[str, dict | None]:
        async with gate:
            return filename, await asyncio.to_thread(read_props, filename)

    pending: list[str] = []
    counts = {
        "surveyed": 0,
        "current": 0,
        "no_props": 0,
        "no_raw": 0,
        "already_tried": 0,
        "why_missing": 0,
        "why_status": 0,
        "why_version": 0,
        "why_params": 0,
    }
    for start in range(0, len(candidates), _SURVEY_BATCH):
        batch = candidates[start : start + _SURVEY_BATCH]
        for filename, props in await asyncio.gather(
            *(one(c["filename"]) for c in batch)
        ):
            counts["surveyed"] += 1
            if props is None:
                counts["no_props"] += 1
                continue
            reason = why_compute(props)
            if reason is None:
                counts["current"] += 1
            elif _attempted(props) and not retry:
                counts["already_tried"] += 1
            elif not await asyncio.to_thread(has_raw, filename):
                counts["no_raw"] += 1
            else:
                counts[f"why_{reason}"] += 1
                pending.append(filename)
        if limit is not None and len(pending) >= limit:
            return pending[:limit], counts
    return pending, counts


async def _fill(
    pending: list[str],
    compute: Callable[[str], tuple[dict | None, str | None, Exception | None]],
    write: Callable[[str, dict], None],
    write_error: Callable[[str, dict], None],
    write_attempt: Callable[[str, str], None],
) -> dict[str, int]:
    """Compute each pending file's document and write it, a few at a time.

    Written as each one finishes rather than at the end, so an interrupted
    run keeps what it did. A file that leaves no usable document has the
    attempt recorded, so the next run moves past it. A write that fails is
    counted and the run carries on.

    :param pending: Filenames to read, from :func:`_survey`.
    :param compute: Computes one document as ``(document, reason, error)``:
        :func:`_compute` with the reader bound, or a stand-in.
    :param write: Stores an ``ok`` or ``no_fingerprint`` document.
    :param write_error: Stores an ``error`` document with the attempt.
    :param write_attempt: Records a read that left no document.
    :return: The per-outcome counts.
    :rtype: dict[str, int]
    """
    gate = asyncio.Semaphore(_READ_CONCURRENCY)
    counts = {
        "ok": 0,
        "no_fingerprint": 0,
        "error": 0,
        "unexpected": 0,
        "no_raw": 0,
        "empty": 0,
        "reader": 0,
        "unwritable": 0,
    }
    previewed = 0
    traced = 0
    done = 0

    def store(how: Callable, filename: str, value) -> bool:
        try:
            how(filename, value)
        except Exception as exc:  # noqa: BLE001
            counts["unwritable"] += 1
            runtime.logger.info(f"  Cannot write {filename}: {exc}")
            return False
        return True

    async def one(filename: str) -> None:
        nonlocal previewed, traced, done
        async with gate:
            document, reason, error = await asyncio.to_thread(compute, filename)
            status = document.get("status") if document else None
            if reason == "no_raw":
                # Nothing is recorded: the survey leaves these out by itself.
                counts["no_raw"] += 1
            elif reason == "reader":
                counts["reader"] += 1
                if traced < _TRACEBACK_LIMIT:
                    traced += 1
                    runtime.logger.opt(exception=error).info(
                        f"  Reader refused {filename}"
                    )
                else:
                    runtime.logger.info(f"  Reader refused {filename}")
                await asyncio.to_thread(store, write_attempt, filename, "reader")
            elif reason == "empty":
                counts["empty"] += 1
                runtime.logger.info(f"  {filename}: no scans")
                await asyncio.to_thread(store, write_attempt, filename, "empty")
            elif status == "error":
                counts["error"] += 1
                runtime.logger.info(f"  {filename}: {document.get('error')}")
                await asyncio.to_thread(store, write_error, filename, document)
            elif status in DESCRIBED:
                if await asyncio.to_thread(store, write, filename, document):
                    counts[status] += 1
                    if previewed < _PREVIEW_LIMIT:
                        previewed += 1
                        runtime.logger.info(f"  {filename}: {_summary(document)}")
            else:
                # The reader was checked before the run, so an unavailable
                # document here means it changed under the run. Writing it
                # would only record that; the attempt moves past the file.
                counts["unexpected"] += 1
                runtime.logger.info(f"  {filename}: status {status!r}")
                await asyncio.to_thread(
                    store, write_attempt, filename, str(status or "none")
                )
            done += 1
            if done % _PROGRESS_EVERY == 0:
                runtime.logger.info(f"  ... {done} of {len(pending)} files read")

    for start in range(0, len(pending), _READ_BATCH):
        await asyncio.gather(
            *(one(filename) for filename in pending[start : start + _READ_BATCH])
        )
    return counts


def _summary(document: dict) -> str:
    """One line on what a document holds: its status, and its modes with the
    number of frequency bands each read."""
    modes = [
        f"{mode['mode_id']} ({len((mode.get('segments') or {}).get('rows') or [])} "
        f"frequency bands)"
        for mode in document.get("modes") or []
    ]
    return f"{document['status']}, {', '.join(modes) or 'no modes'}"


def _limit() -> int | None:
    """How many files one run may read, from ``AXF_LIMIT``.

    :return: The cap, or None when the variable is unset.
    :raises BadLimit: The variable is set to something that is not a positive
        whole number: an operator who set it wanted a bounded run.
    """
    raw = os.environ.get("AXF_LIMIT")
    if not raw:
        return None
    try:
        limit = int(raw)
    except ValueError as exc:
        raise BadLimit(
            f"AXF_LIMIT={raw!r} is not a whole number. Unset it to read every "
            f"file whose axis fingerprint is missing or out of date."
        ) from exc
    if limit <= 0:
        raise BadLimit(
            f"AXF_LIMIT={raw!r} is not a positive number. Unset it to read "
            f"every file whose axis fingerprint is missing or out of date."
        )
    return limit


async def run() -> None:
    """Find the Orbitrap files whose axis fingerprint is missing or out of
    date, and compute it."""
    dry_run = os.environ.get("DRY_RUN") == "1"
    retry = os.environ.get("AXF_RETRY") == "1"
    limit = _limit()
    reader = _reader()
    await configure_database_engine()

    candidates = await _candidates()
    if not candidates:
        runtime.logger.info("No Orbitrap files.")
        return
    runtime.logger.info(f"Orbitrap files: {len(candidates)}")

    # A dry run surveys every candidate: the counts are the point of it.
    pending, survey = await _survey(
        candidates, _read_props, _has_raw, None if dry_run else limit, retry
    )
    capped = survey["surveyed"] < len(candidates)
    runtime.logger.info(
        f"Surveyed {survey['surveyed']} of {len(candidates)}: "
        f"{len(pending)} to read (no document {survey['why_missing']}, "
        f"unavailable or error {survey['why_status']}, "
        f"other version {survey['why_version']}, "
        f"other parameters {survey['why_params']}), "
        f"current {survey['current']}, raw data not kept {survey['no_raw']}, "
        f"tried before {survey['already_tried']}, no props {survey['no_props']}"
    )
    if survey["already_tried"] and not retry:
        runtime.logger.info(
            "  Files tried before left no document; AXF_RETRY=1 tries them again."
        )
    if not pending:
        runtime.logger.info("Nothing left to do.")
        return

    def compute(filename: str):
        return _compute(filename, reader)

    if dry_run:
        sample = pending[:_DRY_RUN_SAMPLE]
        runtime.logger.info(
            f"DRY_RUN=1: {len(pending)} files would be read"
            f"{f' (capped at {limit} per run)' if limit else ''}. "
            f"Computing {len(sample)} of them to show what is there; "
            f"nothing is written."
        )
        counts = await _fill(sample, compute, _no_write, _no_write, _no_write)
        runtime.logger.info("=" * 80)
        runtime.logger.info("BACKFILL AXIS FINGERPRINT COMPLETE (DRY RUN)")
        runtime.logger.info(
            f"Orbitrap files: {len(candidates)}, would read: {len(pending)}, "
            f"current: {survey['current']}, "
            f"raw data not kept: {survey['no_raw']}, "
            f"sampled {len(sample)}: {counts['ok']} ok, "
            f"{counts['no_fingerprint']} no_fingerprint, {counts['error']} error, "
            f"{counts['empty'] + counts['reader']} unreadable"
        )
        runtime.logger.info("=" * 80)
        return

    if limit and len(pending) >= limit:
        runtime.logger.info(f"AXF_LIMIT={limit}: reading {len(pending)} this run.")

    counts = await _fill(
        pending, compute, _write_document, _write_error, _write_attempt
    )

    runtime.logger.info("=" * 80)
    runtime.logger.info("BACKFILL AXIS FINGERPRINT COMPLETE")
    runtime.logger.info(
        f"Orbitrap files: {len(candidates)}, surveyed: {survey['surveyed']}, "
        f"written: {counts['ok']} ok and {counts['no_fingerprint']} "
        f"no_fingerprint, error: {counts['error']}, "
        f"no scans: {counts['empty']}, reader refused: {counts['reader']}, "
        f"other status: {counts['unexpected']}, "
        f"raw data not kept: {survey['no_raw'] + counts['no_raw']}, "
        f"could not be written: {counts['unwritable']}, "
        f"no props: {survey['no_props']}"
    )
    if capped:
        runtime.logger.info(
            "The survey stopped at the cap, so run this again until it reports "
            "nothing left to do."
        )
    runtime.logger.info("=" * 80)
    failed = (
        counts["error"]
        + counts["empty"]
        + counts["reader"]
        + counts["unexpected"]
        + counts["unwritable"]
    )
    if failed:
        runtime.logger.warning(
            f"Axis fingerprint backfill: {failed} files were left without a "
            f"usable document ({counts['error']} error, {counts['empty']} no "
            f"scans, {counts['reader']} reader refused, {counts['unexpected']} "
            f"other status, {counts['unwritable']} could not be written)."
        )


def _no_write(*_args) -> None:
    """A write that does nothing, for the dry run."""


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
