"""
Export produced match results as golden peaks for the reproducibility test.

The demo bundle ships a frozen ``expected/peaks.parquet`` that a from-raw
rebuild must reproduce within the manifest's tolerances. The golden rows are the
*found* isotope peaks: matching scores every possible isotopologue, but the vast
majority have negligible abundance and are never detected (``match_score == 0``),
so only ``match_score > 0`` rows are exported.

Each row is keyed by ``(filename, target_isotope_id)`` - both stable across a
rebuild (the raw ``filename`` is unique; ``target_isotope_id`` comes from the
restored seed), unlike ``sample_item_id`` which is regenerated on every
ingestion. That stable key is what lets the reproducibility comparison join a
fresh rebuild's peaks back to the goldens.

This module owns only the read: it returns plain rows so the backend stays free
of a pandas/pyarrow dependency. The demo CLI (``build_bundle.export_goldens``)
turns the rows into the parquet artifact.

Usage:
    mascope dev db script run export_goldens   # prints a row count
    python -m mascope_backend.db.scripts.export_goldens --json <path>
                                               # also dump the rows as JSON
    (invoked in-process by `mascope demo snapshot --update`)

The ``--json <path>`` form is what the reproducibility test uses to export the
produced peaks from inside the backend container (the logger owns stdout, so
the rows go to a file rather than the console).
"""

import asyncio

from sqlalchemy import select

from mascope_backend.db import (
    MatchIon,
    MatchIsotope,
    SampleFile,
    SampleItem,
    TargetIon,
    TargetIsotope,
    async_session,
    configure_database_engine,
)
from mascope_backend.runtime import runtime


async def _query_golden_peaks() -> list[dict]:
    """Read the found isotope peaks from the active database, ordered stably."""
    await configure_database_engine()
    stmt = (
        select(
            SampleFile.filename,
            MatchIsotope.target_isotope_id,
            TargetIsotope.target_isotope_formula,
            MatchIsotope.sample_peak_mz,
            MatchIsotope.sample_peak_intensity,
            MatchIsotope.match_score,
        )
        .join(
            TargetIsotope,
            TargetIsotope.target_isotope_id == MatchIsotope.target_isotope_id,
        )
        .join(SampleItem, SampleItem.sample_item_id == MatchIsotope.sample_item_id)
        .join(SampleFile, SampleFile.sample_file_id == SampleItem.sample_file_id)
        # Only the peaks that were actually found; the rest are negligible-
        # abundance isotopologues scored 0 (see module docstring).
        .where(MatchIsotope.match_score > 0)
        # Deterministic order so the exported golden file is stable across runs.
        .order_by(
            SampleFile.filename,
            TargetIsotope.target_isotope_formula,
            MatchIsotope.sample_peak_mz,
        )
    )
    async with async_session() as session:
        rows = (await session.execute(stmt)).all()

    return [
        {
            "filename": row.filename,
            "target_isotope_id": row.target_isotope_id,
            "target_isotope_formula": row.target_isotope_formula,
            "mz": row.sample_peak_mz,
            "height": row.sample_peak_intensity,
            "match_score": row.match_score,
        }
        for row in rows
    ]


def get_golden_peaks() -> list[dict]:
    """
    Fetch the golden peak rows from the active (demo) database.

    Each row is one found isotope peak (``match_score > 0``) with its stable key
    ``filename`` + ``target_isotope_id``, the ``target_isotope_formula``, the
    produced ``mz`` and ``height`` (peak intensity), and the ``match_score``.
    Empty if nothing has been matched yet.

    :return: One dict per found isotope peak.
    """
    return asyncio.run(_query_golden_peaks())


async def _query_golden_ions() -> list[dict]:
    """Read the per-ION match scores from the active database, ordered stably.

    This is the level at which the consolidated v2 match score operates (the
    legacy per-isotopologue ``MatchIsotope.match_score`` is unchanged by it), so a
    golden ``ions.parquet`` is needed to compare v1 vs v2 scoring."""
    await configure_database_engine()
    stmt = (
        select(
            SampleFile.filename,
            MatchIon.target_ion_id,
            TargetIon.target_ion_formula,
            MatchIon.match_score,
            MatchIon.match_category,
        )
        .join(TargetIon, TargetIon.target_ion_id == MatchIon.target_ion_id)
        .join(SampleItem, SampleItem.sample_item_id == MatchIon.sample_item_id)
        .join(SampleFile, SampleFile.sample_file_id == SampleItem.sample_file_id)
        .where(MatchIon.match_score > 0)
        .order_by(SampleFile.filename, TargetIon.target_ion_formula)
    )
    async with async_session() as session:
        rows = (await session.execute(stmt)).all()

    return [
        {
            "filename": row.filename,
            "target_ion_id": row.target_ion_id,
            "target_ion_formula": row.target_ion_formula,
            "match_score": row.match_score,
            "match_category": row.match_category,
        }
        for row in rows
    ]


def get_golden_ions() -> list[dict]:
    """Fetch the golden per-ION match rows (the level the v2 score operates at)."""
    return asyncio.run(_query_golden_ions())


def main() -> None:
    """
    Entry point for the script runner: report how many golden peaks exist.

    With ``--json <path>`` the rows are also written to ``path`` as a JSON
    array, for consumers outside this process (the reproducibility test).
    """
    import json
    import sys

    json_path: str | None = None
    argv = sys.argv[1:]
    if "--json" in argv:
        idx = argv.index("--json")
        if idx + 1 >= len(argv):
            runtime.logger.error("--json requires an output path")
            raise SystemExit(2)
        json_path = argv[idx + 1]

    try:
        rows = get_golden_peaks()
    except KeyboardInterrupt:
        runtime.logger.info("\nGolden export cancelled by user (Ctrl+C)")
        return
    except Exception:
        runtime.logger.exception("Golden export query failed")
        raise
    runtime.logger.success(f"Found {len(rows)} matched isotope peak(s) to export")

    if json_path is not None:
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(rows, f)
        runtime.logger.success(f"Wrote {len(rows)} row(s) to {json_path}")


if __name__ == "__main__":
    main()
