"""
Maintenance script to fill ``method_file`` on Orbitrap sample files that were
ingested without it.

``sample_file.method_file`` names the instrument method an acquisition ran
with. The Orbitrap processor stopped reading it when ingestion moved onto the
OpenTFRaw reader and reported "" for every file instead, until the reader
backend exposed it again. The name was never lost: it is in the raw file's own
sample information, so it is read back from there.

The converter posts the same value into the instrument config it creates for
each file, so a repaired file's ``instrument_function`` row is filled too - but
only while that row's ``method_file`` is still empty, and only when every
sample file pointing at it resolves to one and the same method. A config the
files disagree about is reported and left alone.

Only the file header is read. A file missing from the filestore, or one the
reader cannot open, is reported and skipped.

Set DRY_RUN=1 to report what would change without writing.

Usage:
    mascope dev db script run populate_orbitrap_method_file
    mascope prod db script run populate_orbitrap_method_file

Date: 2026-09-18
"""

import asyncio
import os
from collections import defaultdict
from typing import Callable

from sqlalchemy import bindparam, text

import mascope_file.name as m_name
from mascope_backend.db import async_session, configure_database_engine
from mascope_backend.runtime import runtime
from mascope_thermo.backend import open_backend


# Maximum number of repaired rows to log individually.
_PREVIEW_LIMIT = 20

# Instrument config ids per lookup statement.
_ID_CHUNK = 5000


async def _candidates() -> list[dict]:
    """Orbitrap sample files with no method recorded.

    :return: One dict per file: sample_file_id, filename, instrument_function_id.
    :rtype: list[dict]
    """
    async with async_session() as session:
        result = await session.execute(
            text("""
                SELECT sample_file_id, filename, instrument_function_id
                FROM sample_file
                WHERE instrument_type = 'orbi'
                  AND (method_file IS NULL OR method_file = '')
                ORDER BY datetime
            """)
        )
        return [dict(row._mapping) for row in result]


async def _configs(instrument_function_ids: set[str]) -> dict[str, dict]:
    """The empty-method configs among ``instrument_function_ids``, with every
    sample file that points at each.

    :param instrument_function_ids: Configs the candidate files point at.
    :return: ``{instrument_function_id: {"files": [{"filename", "method_file"}]}}``
        for each config whose own ``method_file`` is empty.
    :rtype: dict[str, dict]
    """
    ids = sorted(instrument_function_ids)
    configs: dict[str, dict] = defaultdict(lambda: {"files": []})
    async with async_session() as session:
        # Chunked: each id is a bind parameter, and the wire protocol caps a
        # statement at 32767 of them.
        for start in range(0, len(ids), _ID_CHUNK):
            result = await session.execute(
                text("""
                    SELECT inf.instrument_function_id, sf.filename, sf.method_file
                    FROM instrument_function inf
                    JOIN sample_file sf
                      ON sf.instrument_function_id = inf.instrument_function_id
                    WHERE inf.instrument_function_id IN :ids
                      AND (inf.method_file IS NULL OR inf.method_file = '')
                """).bindparams(bindparam("ids", expanding=True)),
                {"ids": ids[start : start + _ID_CHUNK]},
            )
            for row in result:
                configs[row.instrument_function_id]["files"].append(
                    {"filename": row.filename, "method_file": row.method_file}
                )
        return dict(configs)


def _read_method(filename: str) -> str | None:
    """The instrument method recorded in one sample file's raw data.

    :param filename: Sample file name (base, not full path).
    :return: The method path, "" when the file records none, or None when the
        file cannot be read.
    :rtype: str | None
    """
    try:
        with open_backend(m_name.filename_to_datafile_path(filename)) as reader:
            return reader.method_file()
    except Exception as exc:  # noqa: BLE001
        runtime.logger.warning(f"  Cannot read {filename}: {exc}")
        return None


def _plan(
    candidates: list[dict],
    configs: dict[str, dict],
    read: Callable[[str], str | None],
) -> dict:
    """Decide every row to write, reading each candidate file once.

    :param candidates: Rows from :func:`_candidates`.
    :param configs: Result of :func:`_configs` for the candidates' configs.
    :param read: Reads a file's method: :func:`_read_method`, or a stand-in.
    :return: ``sample_files`` and ``instrument_functions`` (the updates, as
        ``{"id", "method_file"}``) plus the ``unreadable``, ``unrecorded`` and
        ``ambiguous_configs`` counts.
    :rtype: dict
    """
    read_methods: dict[str, str] = {}
    unreadable = 0
    unrecorded = 0
    sample_files: list[dict] = []
    for candidate in candidates:
        method = read(candidate["filename"])
        if method is None:
            unreadable += 1
            continue
        if not method:
            # The file itself records no method; there is nothing to restore.
            unrecorded += 1
            continue
        read_methods[candidate["filename"]] = method
        sample_files.append({"id": candidate["sample_file_id"], "method_file": method})

    instrument_functions: list[dict] = []
    ambiguous_configs = 0
    for config_id, config in configs.items():
        # A file that already carried a method keeps it; the others resolve to
        # what was just read, or to nothing when their file could not be.
        resolved = {
            f["method_file"] or read_methods.get(f["filename"], "")
            for f in config["files"]
        }
        if len(resolved) == 1 and "" not in resolved:
            instrument_functions.append(
                {"id": config_id, "method_file": resolved.pop()}
            )
        elif len(resolved - {""}) > 1:
            ambiguous_configs += 1
            runtime.logger.warning(
                f"  Instrument config {config_id} is shared by files recorded "
                f"with different methods {sorted(resolved - {''})}; left empty"
            )

    return {
        "sample_files": sample_files,
        "instrument_functions": instrument_functions,
        "unreadable": unreadable,
        "unrecorded": unrecorded,
        "ambiguous_configs": ambiguous_configs,
    }


async def _apply(sample_files: list[dict], instrument_functions: list[dict]) -> None:
    """Write the planned methods back, in one transaction.

    The ``method_file`` guards keep a row that was filled since it was read -
    by a concurrent ingest or an earlier run - from being overwritten.

    :param sample_files: ``{"id", "method_file"}`` per sample file.
    :param instrument_functions: ``{"id", "method_file"}`` per config.
    """
    async with async_session() as session:
        if sample_files:
            await session.execute(
                text("""
                    UPDATE sample_file
                    SET method_file = :method_file
                    WHERE sample_file_id = :id
                      AND (method_file IS NULL OR method_file = '')
                """),
                sample_files,
            )
        if instrument_functions:
            await session.execute(
                text("""
                    UPDATE instrument_function
                    SET method_file = :method_file
                    WHERE instrument_function_id = :id
                      AND (method_file IS NULL OR method_file = '')
                """),
                instrument_functions,
            )
        await session.commit()


async def run() -> None:
    """Find, read and fill the missing Orbitrap method names."""
    await configure_database_engine()
    dry_run = os.environ.get("DRY_RUN") == "1"

    candidates = await _candidates()
    if not candidates:
        runtime.logger.info("No Orbitrap sample files are missing a method.")
        return

    runtime.logger.info(f"Orbitrap sample files with no method: {len(candidates)}")

    configs = await _configs(
        {c["instrument_function_id"] for c in candidates if c["instrument_function_id"]}
    )
    plan = await asyncio.to_thread(_plan, candidates, configs, _read_method)

    by_id = {c["sample_file_id"]: c["filename"] for c in candidates}
    for update in plan["sample_files"][:_PREVIEW_LIMIT]:
        runtime.logger.info(f"  {by_id[update['id']]}: {update['method_file']}")
    if len(plan["sample_files"]) > _PREVIEW_LIMIT:
        runtime.logger.info(
            f"  ... and {len(plan['sample_files']) - _PREVIEW_LIMIT} more"
        )

    if dry_run:
        runtime.logger.info(
            f"DRY_RUN=1: {len(plan['sample_files'])} sample files and "
            f"{len(plan['instrument_functions'])} instrument configs would be updated."
        )
    else:
        await _apply(plan["sample_files"], plan["instrument_functions"])

    filled = "would fill" if dry_run else "filled"
    runtime.logger.info("=" * 80)
    runtime.logger.info("POPULATE ORBITRAP METHOD FILE COMPLETE")
    runtime.logger.info(
        f"Candidates: {len(candidates)}, "
        f"sample files {filled}: {len(plan['sample_files'])}, "
        f"instrument configs {filled}: {len(plan['instrument_functions'])}, "
        f"no method in file: {plan['unrecorded']}, "
        f"unreadable: {plan['unreadable']}, "
        f"configs with conflicting methods: {plan['ambiguous_configs']}"
    )
    runtime.logger.info("=" * 80)


def main() -> None:
    """Entry point for the Orbitrap method-file backfill script."""
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        runtime.logger.info("Cancelled by user (Ctrl+C)")
    except Exception:
        runtime.logger.exception("Script failed")
        raise


if __name__ == "__main__":
    main()
