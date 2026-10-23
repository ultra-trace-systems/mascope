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

An MS2 acquisition has a golden of its own (:func:`get_golden_ms2`): every
(precursor, activation) group of each sample with fragmentation scans, with
the group's averaged centroids down to the peak store's floor. Its rows are
keyed by ``(filename, polarity, parent_peak_mz, activation)`` and, within a
group, by m/z - nothing about an MS2 spectrum is stored in the database, so
the key is what the scan filters say, which a rebuild reads the same.

Usage:
    mascope dev db script run export_goldens   # prints a row count
    python -m mascope_backend.db.scripts.export_goldens --json <path>
                                               # also dump the rows as JSON
    python -m mascope_backend.db.scripts.export_goldens --ms2-json <path>
                                               # the MS2 rows as JSON
    (invoked in-process by `mascope demo snapshot --update`)

The ``--json <path>`` and ``--ms2-json <path>`` forms are what the
reproducibility test uses to export the produced peaks and MS2 spectra from
inside the backend container (the logger owns stdout, so the rows go to a
file rather than the console).
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


async def _query_ingested_filenames() -> list[str]:
    """Read every ingested sample file's filename from the active database."""
    await configure_database_engine()
    stmt = select(SampleFile.filename).distinct().order_by(SampleFile.filename)
    async with async_session() as session:
        rows = (await session.execute(stmt)).all()

    return [row.filename for row in rows]


def get_ingested_filenames() -> list[str]:
    """
    Fetch the filenames of every file that has been ingested.

    This is the *conversion* side of the pipeline, one step upstream of the
    golden peaks: a file that converted but matched nothing has a row here and
    none in :func:`get_golden_peaks`. The demo bundle's authoring guard needs
    both to tell "never ingested" apart from "ingested but produced no peaks"
    (``build_bundle.check_raw_coverage``).

    Note that the stored filename is the one the converter reconstructs from the
    file's own metadata (it re-inserts the acquisition timestamp), not the
    uploaded filename - see ``mascope_thermo.processor.RawProcessor.filename``.

    :return: One filename per ingested sample file, ordered.
    """
    return asyncio.run(_query_ingested_filenames())


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


async def _query_golden_ms2() -> list[dict]:
    """Read every sample's MS2 spectra, as the MS2 routes serve them.

    One row per averaged centroid of one (precursor, activation) group of one
    sample, read through the same compute path as
    ``GET /api/samples/{id}/ms2/centroids?by_activation=true`` - the sample's
    file, polarity and window, the m/z axis rescaled by the file's calibration
    - and kept down to the peak store's floor
    (``mascope_signal.peak.SIGNAL_TO_NOISE_THRESHOLD``) rather than the route's
    default, so the golden is as deep as the MS1 goldens are. Each row also
    carries its group's scan count and isolation width from the MS2 summary,
    which pins that route too.

    Which samples hold fragmentation scans is read off each file's scan-stream
    census, never by opening the file: an MS1-only bundle opens no raw file
    here and exports nothing. A file's samples are read once per polarity and
    window, so a second item over the same scans adds no rows.
    """
    import numpy as np

    import mascope_signal.compute as m_compute
    from mascope_backend.api.controllers.sample.files.process.census import (
        fragmentation_polarities,
    )
    from mascope_backend.api.controllers.sample.files.process.status import (
        read_scan_streams,
    )
    from mascope_signal.peak import SIGNAL_TO_NOISE_THRESHOLD

    await configure_database_engine()
    stmt = (
        select(
            SampleFile.filename,
            SampleFile.polarity.label("file_polarity"),
            SampleItem.polarity,
            SampleItem.t0,
            SampleItem.t1,
        )
        .join(SampleFile, SampleFile.sample_file_id == SampleItem.sample_file_id)
        .distinct()
        .order_by(
            SampleFile.filename, SampleItem.polarity, SampleItem.t0, SampleItem.t1
        )
    )
    async with async_session() as session:
        samples = (await session.execute(stmt)).all()

    rows: list[dict] = []
    census: dict[str, list[dict] | None] = {}
    for sample in samples:
        if sample.filename not in census:
            census[sample.filename] = await read_scan_streams(sample.filename)
        fragmented = fragmentation_polarities(
            census[sample.filename], sample.file_polarity
        )
        if sample.polarity not in fragmented:
            continue
        groups = await m_compute.get_orbi_ms2_centroids_by_parent(
            sample.filename,
            t_min=sample.t0,
            t_max=sample.t1,
            polarity=sample.polarity,
            by_activation=True,
        )
        if not groups:
            continue
        summary = await m_compute.get_ms2_summary(
            sample.filename,
            t_min=sample.t0,
            t_max=sample.t1,
            polarity=sample.polarity,
        )
        described = {
            (group["parent_peak_mz"], group["activation"]): group
            for group in summary.get("groups", [])
        }
        for (parent_peak_mz, activation), spectrum in sorted(groups.items()):
            mz, intensity, resolution, snr = (np.asarray(a) for a in spectrum)
            group = described.get((parent_peak_mz, activation), {})
            kept = np.flatnonzero(snr >= SIGNAL_TO_NOISE_THRESHOLD)
            for i in kept[np.argsort(mz[kept], kind="stable")]:
                rows.append(
                    {
                        "filename": sample.filename,
                        "polarity": sample.polarity,
                        "parent_peak_mz": float(parent_peak_mz),
                        "activation": activation,
                        "scan_count": group.get("scan_count"),
                        "isolation_width": group.get("isolation_width"),
                        "mz": float(mz[i]),
                        "intensity": float(intensity[i]),
                        "resolution": float(resolution[i]),
                        "signal_to_noise": float(snr[i]),
                    }
                )
    return rows


def get_golden_ms2() -> list[dict]:
    """
    Fetch the golden MS2 rows from the active (demo) database.

    One row per averaged centroid, at or above the peak store's floor, of
    each (precursor, activation) group of each sample whose file records
    fragmentation scans in its polarity: ``filename``, ``polarity``,
    ``parent_peak_mz``, ``activation``, the group's ``scan_count`` and
    ``isolation_width``, and the centroid's ``mz``, ``intensity``,
    ``resolution`` and ``signal_to_noise``. Empty for an MS1-only bundle.

    :return: One dict per MS2 centroid.
    """
    return asyncio.run(_query_golden_ms2())


def main() -> None:
    """
    Entry point for the script runner: report how many golden peaks exist.

    With ``--json <path>`` the rows are also written to ``path`` as a JSON
    array, for consumers outside this process (the reproducibility test);
    ``--ms2-json <path>`` does the same for the MS2 rows.
    """
    import json
    import sys

    argv = sys.argv[1:]

    def _path_after(flag: str) -> str | None:
        if flag not in argv:
            return None
        idx = argv.index(flag)
        if idx + 1 >= len(argv):
            runtime.logger.error(f"{flag} requires an output path")
            raise SystemExit(2)
        return argv[idx + 1]

    json_path = _path_after("--json")
    ms2_json_path = _path_after("--ms2-json")

    try:
        rows = get_golden_peaks()
        ms2_rows = get_golden_ms2() if ms2_json_path is not None else []
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
    if ms2_json_path is not None:
        with open(ms2_json_path, "w", encoding="utf-8") as f:
            json.dump(ms2_rows, f)
        runtime.logger.success(
            f"Wrote {len(ms2_rows)} MS2 centroid row(s) to {ms2_json_path}"
        )


if __name__ == "__main__":
    main()
