"""
Maintenance script to fill the model of instruments whose files predate its
recording.

Each instrument is a row of its own (``db.models.Instrument``). A file's
registration fills the row's ``model`` from what the reader reports (a Thermo
file's ``instrument_details()["Model"]``, recorded in the props as
``instrument_model``) and ``cv_term`` from the PSI-MS term of that model
(``api.new.instruments.registry.record_instrument_seen``). The migration that
made the table knew no model, so an instrument that has registered no file
since keeps ``model`` NULL, and the instrument list, the export and the
deposit package show none for it.

The model is still on record for most of them. This looks, for each such
instrument, through its Orbitrap files newest first, and takes the first
model one names:

1. the file's props: ``instrument_model``, written at conversion since the
   model was read, else ``axis_fingerprint.source.instrument_model``, written
   wherever the axis fingerprint was computed;
2. only when no file's props name one, the header of the raw file kept beside
   the newest few files that still have one (``_RAW_ATTEMPTS``).

It writes the model and its PSI-MS term together, as a registration does:
``cv_term`` is the accession ``vocabulary.instrument_model_term`` gives the
model, NULL for one the vocabulary has no term for. A row is written only
while its model is still NULL, so a file registered during the run, which
knows better, is never overwritten, and a second run changes nothing.

**Orbitrap files only** (``sample_file.instrument_type = 'orbi'``): a TofDaq
file names its digitizer, not its instrument, so a TOF instrument has no
model to find and is counted, not read.

``DRY_RUN=1`` reports what the run would write, writing nothing.

Usage:
    mascope dev db script run backfill_instrument_model
    mascope prod db script run backfill_instrument_model

    DRY_RUN=1 mascope prod db script run backfill_instrument_model

Date: 2026-11-02
"""

import asyncio
import os
from typing import Callable

from sqlalchemy import text

import mascope_file.io as m_io
import mascope_signal.metadata as m_metadata
from mascope_backend import vocabulary
from mascope_backend.api.new.instruments.registry import model_in_props
from mascope_backend.db import async_session, configure_database_engine
from mascope_backend.runtime import runtime
from mascope_file.name import get_sample_file_type


#: The instrument class whose files name a model.
ORBITRAP = "orbi"

#: Raw file headers read per instrument, newest first, before it is given up:
#: a file whose raw data is kept but will not open should not end the search,
#: and an instrument whose props name nothing should not open every file.
_RAW_ATTEMPTS = 3

#: Every instrument with no model, with its class, and how many of its files
#: are Orbitrap files.
_INSTRUMENTS_SQL = """
    SELECT i.instrument_id AS instrument_id,
           i.name AS name,
           i.instrument_type AS instrument_type,
           count(sf.sample_file_id) AS orbitrap_files
    FROM instrument i
    LEFT JOIN sample_file sf
           ON sf.instrument_id = i.instrument_id
          AND sf.instrument_type = :instrument_type
    WHERE i.model IS NULL
    GROUP BY i.instrument_id, i.name, i.instrument_type
    ORDER BY i.name
"""

#: One instrument's Orbitrap files, newest first.
_FILES_SQL = """
    SELECT sf.filename AS filename
    FROM sample_file sf
    WHERE sf.instrument_id = :instrument_id
      AND sf.instrument_type = :instrument_type
    ORDER BY sf.datetime_utc DESC NULLS LAST, sf.filename DESC
"""

#: The write: only while the row still has no model.
_UPDATE_SQL = """
    UPDATE instrument
    SET model = :model, cv_term = :cv_term
    WHERE instrument_id = :instrument_id AND model IS NULL
"""


def _read_props(filename: str) -> dict | None:
    """One sample file's ``.props``, or None when there are none to read."""
    try:
        return m_io.read_props(filename)
    except Exception:  # noqa: BLE001
        return None


def _has_raw(filename: str) -> bool:
    """Whether the sample still holds its raw Orbitrap file, asked without
    opening it."""
    try:
        return get_sample_file_type(filename) == "orbi_raw"
    except Exception:  # noqa: BLE001
        return False


def _model_in_raw(filename: str) -> str | None:
    """The instrument model one sample file's raw file header names, or None
    when it names none or cannot be read. Blocking; run it in a thread."""
    try:
        model = m_metadata.metadata_factory(filename).instrument_details.get("Model")
    except Exception as error:  # noqa: BLE001
        runtime.logger.info(f"  Cannot read the raw file of {filename}: {error}")
        return None
    if not model:
        return None
    return str(model).strip() or None


def find_model(
    filenames: list[str],
    read_props: Callable[[str], dict | None],
    has_raw: Callable[[str], bool],
    model_in_raw: Callable[[str], str | None],
) -> tuple[str | None, str]:
    """The model an instrument's files name, and where it was found.

    Blocking; run it in a thread.

    :param filenames: The instrument's Orbitrap files, newest first.
    :param read_props: Reads a file's props: :func:`_read_props`, or a
        stand-in.
    :param has_raw: Whether a file's raw data is kept: :func:`_has_raw`, or a
        stand-in.
    :param model_in_raw: Reads the model off a file's raw data:
        :func:`_model_in_raw`, or a stand-in.
    :return: ``(model, "props")`` from the newest file whose props name one,
        else ``(model, "raw")`` from the newest kept raw file that names one
        among the first ``_RAW_ATTEMPTS`` kept, else ``(None, "none")``.
    :rtype: tuple[str | None, str]
    """
    for filename in filenames:
        model = model_in_props(read_props(filename))
        if model:
            return model, "props"
    attempts = 0
    for filename in filenames:
        if attempts >= _RAW_ATTEMPTS:
            break
        if not has_raw(filename):
            continue
        attempts += 1
        model = model_in_raw(filename)
        if model:
            return model, "raw"
    return None, "none"


def cv_term_of(model: str) -> str | None:
    """The PSI-MS accession a registration would store beside ``model``."""
    term = vocabulary.instrument_model_term(model)
    return term.accession if term else None


async def _instruments() -> list[dict]:
    async with async_session() as session:
        result = await session.execute(
            text(_INSTRUMENTS_SQL), {"instrument_type": ORBITRAP}
        )
        return [dict(row._mapping) for row in result]


async def _files(instrument_id: str) -> list[str]:
    async with async_session() as session:
        result = await session.execute(
            text(_FILES_SQL),
            {"instrument_id": instrument_id, "instrument_type": ORBITRAP},
        )
        return [row.filename for row in result]


async def _write(instrument_id: str, model: str, cv_term: str | None) -> bool:
    """Store the model and its term on a row that still has none.

    :return: Whether the row was written; False when a registration filled it
        first.
    """
    async with async_session() as session:
        result = await session.execute(
            text(_UPDATE_SQL),
            {"instrument_id": instrument_id, "model": model, "cv_term": cv_term},
        )
        await session.commit()
        return result.rowcount == 1


async def backfill(
    instruments: list[dict],
    files: Callable,
    find: Callable[[list[str]], tuple[str | None, str]],
    write: Callable,
) -> dict[str, int]:
    """Find and write the model of each instrument that has none.

    :param instruments: Rows from :func:`_instruments`.
    :param files: An instrument's Orbitrap files, newest first, by id:
        :func:`_files`, or a stand-in.
    :param find: :func:`find_model` with its readers bound, or a stand-in.
    :param write: Stores ``(instrument_id, model, cv_term)`` and answers
        whether the row was written: :func:`_write`, or a stand-in that
        writes nothing for a dry run.
    :return: The counts: ``without_model``, ``tof`` (TOF instruments, whose
        files name no model), ``no_files`` (any other with no Orbitrap file:
        a name only a mode or a device gave), ``from_props``, ``from_raw``,
        ``with_term`` (of those, a model the vocabulary has a term for),
        ``not_found``, ``filled_meanwhile`` (a registration wrote the row
        first) and ``unwritable``.
    :rtype: dict[str, int]
    """
    counts = {
        "without_model": len(instruments),
        "tof": 0,
        "no_files": 0,
        "from_props": 0,
        "from_raw": 0,
        "with_term": 0,
        "not_found": 0,
        "filled_meanwhile": 0,
        "unwritable": 0,
    }
    for instrument in instruments:
        name = instrument["name"]
        if not instrument["orbitrap_files"]:
            counts["tof" if instrument["instrument_type"] == "tof" else "no_files"] += 1
            continue
        filenames = await files(instrument["instrument_id"])
        model, where = await asyncio.to_thread(find, filenames)
        if not model:
            counts["not_found"] += 1
            runtime.logger.info(f"  {name}: no file names a model")
            continue
        cv_term = cv_term_of(model)
        try:
            written = await write(instrument["instrument_id"], model, cv_term)
        except Exception as exc:  # noqa: BLE001
            counts["unwritable"] += 1
            runtime.logger.info(f"  Cannot write {name}: {exc}")
            continue
        if not written:
            counts["filled_meanwhile"] += 1
            continue
        counts[f"from_{where}"] += 1
        if cv_term:
            counts["with_term"] += 1
        runtime.logger.info(
            f"  {name}: {model} ({cv_term or 'no PSI-MS term'}, from {where})"
        )
    return counts


async def run() -> None:
    """Fill the model of every instrument that has none and whose files name
    one."""
    dry_run = os.environ.get("DRY_RUN") == "1"
    await configure_database_engine()

    instruments = await _instruments()
    if not instruments:
        runtime.logger.info("Every instrument has a model. Nothing to do.")
        return
    runtime.logger.info(f"Instruments without a model: {len(instruments)}")

    def find(filenames: list[str]) -> tuple[str | None, str]:
        return find_model(filenames, _read_props, _has_raw, _model_in_raw)

    async def no_write(*_args) -> bool:
        return True

    counts = await backfill(instruments, _files, find, no_write if dry_run else _write)

    filled = counts["from_props"] + counts["from_raw"]
    runtime.logger.info("=" * 80)
    runtime.logger.info(
        "BACKFILL INSTRUMENT MODEL COMPLETE" + (" (DRY RUN)" if dry_run else "")
    )
    runtime.logger.info(
        f"Without a model: {counts['without_model']}, "
        f"{'would fill' if dry_run else 'filled'}: {filled} "
        f"({counts['from_props']} from props, {counts['from_raw']} from the raw "
        f"file; {counts['with_term']} with a PSI-MS term), "
        f"no file names one: {counts['not_found']}, "
        f"TOF (no model to find): {counts['tof']}, "
        f"no Orbitrap files: {counts['no_files']}, "
        f"filled by a registration meanwhile: {counts['filled_meanwhile']}, "
        f"could not be written: {counts['unwritable']}"
    )
    runtime.logger.info("=" * 80)


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
