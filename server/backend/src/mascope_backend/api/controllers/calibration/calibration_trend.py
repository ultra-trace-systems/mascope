"""
The calibration trend of a TOF instrument: what its files' calibration records
say, file after file, in the order the files were acquired.

A TOF fit's record lists the calibrant lines it chose from (``quality.points``,
``TofCalibrationHandler._fitted_on``): each line's exact and observed m/z, its
residual under the fit and whether the fit kept it, and the version of the
calibration collection the fit was offered (``quality.collection``). One record
says how one file was fitted. Read over an instrument's files, one polarity at
a time, they show what no single record does: a calibrant that starts to read
off, the day a chemistry change moved a neighbour onto one, an axis that drifts
before the fit corrects it, and the times the calibrant collection changed.

The trend serves, per file:

- the residual of each calibrant line, kept or left out;
- the fit's mean residual over the lines it kept (``post_fit_ppm``);
- how many lines it kept and how many it chose from;
- the offset of the acquisition axis (``axis_offset_ppm``): the signed mean,
  over the kept lines, of each line's offset on the axis the acquisition
  software wrote (``acquisition_axis.line_offsets``), which every line also
  serves on its own (``offset_ppm``). A record keeps that axis through every
  refit (MAS-937), so a refit reads the same offset as the file's first fit,
  not the near-zero error of the axis the previous fit set. A record written
  before it kept the axis reads its lines' observed offsets, which are the
  acquisition axis's on a first fit only.

A record written before the points were kept has its counts and mean errors
only: its file is listed with no calibrant residuals and no signed offset, and
``pre_fit_abs_ppm`` gives the size of its offset.

A change of calibrant collection is placed at the time a record fitted with
the new version was first written (``fitted_at``); a record from before that
key is placed at its file's acquisition time, which no fit precedes.

A window of more files than a chart can draw one by one is served in bins
instead (:func:`binned_trend`): per hour or per day, the quartiles of each of
those measures over the bin's files, summed up in the database, so that a
year of a busy instrument is read whole and answered in a few hundred bins.
"""

import asyncio
import csv
import io
import json
import math
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Integer,
    Text,
    cast,
    func,
    literal,
    or_,
    select,
    text,
    tuple_,
)
from sqlalchemy.types import TIMESTAMP

from mascope_backend.api.controllers.calibration.lib import acquisition_axis
from mascope_backend.api.controllers.calibration.lib.acquisition_axis import (
    TARGET_MZ_DECIMALS,
)
from mascope_backend.api.lib.api_features import api_controller
from mascope_backend.api.lib.datetimes import as_utc
from mascope_backend.api.new.workspaces.dependencies import (
    WorkspaceScope,
    where_collection_visible,
)
from mascope_backend.db import SampleFile, TargetCollection, async_session
from mascope_backend.db.models import (
    record_collection_id,
    record_collection_sha,
    record_names_a_collection,
)
from mascope_backend.runtime import runtime
from mascope_sdk.csv_cells import INCOMPLETE_LINE, escape_cell


#: The window a trend reads when it is given no start: this many days before
#: its end.
TREND_DEFAULT_DAYS = 90

#: The most files a trend serves one by one, of those on the trend
#: (:func:`on_the_trend`): files still on the acquisition's axis are not
#: counted. A busy TOF instrument acquires 130 to 160 files a day, so this is
#: a month of it. A window holding more is served in bins
#: (:func:`binned_trend`).
TREND_MAX_FILES = 5_000

#: The most files a binned trend reads, the latest of its window on the
#: trend; a window holding more is answered from its latest and ``truncated``
#: set. At 160 files a day this is 20 months.
TREND_MAX_BINNED_FILES = 100_000

#: The most files the export lists one by one, the latest of its window on
#: the trend. It lists files, never bins: at 160 files a day this is four
#: months.
EXPORT_MAX_FILES = 20_000

#: How many files the export reads from the database at a time, and writes
#: out as one piece of its body: a few hundred kilobytes of CSV.
EXPORT_FILES_PER_PIECE = 200

#: A window of this many days or fewer is binned per hour, a longer one per
#: day.
TREND_HOURLY_MAX_DAYS = 30

#: The columns of the export, one row per calibrant line of each file; a file
#: whose record lists no lines has one row, with the line's columns empty.
EXPORT_COLUMNS = (
    "datetime_utc",
    "sample_file_id",
    "filename",
    "status",
    "collection_id",
    "collection_name",
    "collection_isotopes_sha256",
    "model_mode",
    "node_version",
    "n_points",
    "n_candidates",
    "post_fit_ppm",
    "axis_offset_ppm",
    "pre_fit_abs_ppm",
    "ion",
    "target_mz",
    "observed_mz",
    "offset_ppm",
    "residual_ppm",
    "snr",
    "retained",
)


def _number(value) -> float | None:
    """A finite float, or None for anything else (booleans included)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def record_points(record: dict) -> list[dict] | None:
    """The calibrant lines a record lists, each a dict; None for none listed."""
    quality = record.get("quality")
    points = quality.get("points") if isinstance(quality, dict) else None
    if not isinstance(points, list):
        return None
    return [point for point in points if isinstance(point, dict)]


def record_collection(record: dict) -> tuple[str, str] | None:
    """The collection version a record's fit was offered: (id, isotopes hash)."""
    quality = record.get("quality")
    collection = quality.get("collection") if isinstance(quality, dict) else None
    if not isinstance(collection, dict):
        return None
    collection_id, sha = collection.get("id"), collection.get("isotopes_sha256")
    if not isinstance(collection_id, str) or not isinstance(sha, str):
        return None
    return collection_id, sha


def file_summary(record: dict) -> dict:
    """
    What a trend reads of one file's record, the lines aside.

    :param record: A record :func:`on_the_trend` admits.
    :return: ``status``; ``n_points``, the lines the fit kept, and
        ``n_candidates``, the lines it chose from (None where the record lists
        none); ``n_ions``; ``post_fit_ppm`` and ``pre_fit_abs_ppm``, the mean
        size of the kept lines' error after and before the fit;
        ``axis_offset_ppm``, the signed mean of their offsets on the
        acquisition axis (``acquisition_axis.axis_offset_ppm``; None where
        the record lists no lines); ``model_mode``; ``node_version``;
        ``fitted_utc``, when the record was written (None where it does not
        say).
    """
    quality = record.get("quality") if isinstance(record.get("quality"), dict) else {}
    points = record_points(record)
    n_points = quality.get("n_points")
    model = quality.get("model") if isinstance(quality.get("model"), dict) else {}
    mode = model.get("mode", record.get("mode") if quality else None)
    node_version = quality.get("node_version")
    return {
        "status": record.get("status")
        if isinstance(record.get("status"), str)
        else None,
        "n_points": n_points if isinstance(n_points, int) else None,
        "n_candidates": len(points) if points is not None else None,
        "n_ions": quality.get("n_ions")
        if isinstance(quality.get("n_ions"), int)
        else None,
        "post_fit_ppm": _number(quality.get("post_fit_mz_error_ppm")),
        "pre_fit_abs_ppm": _number(quality.get("pre_fit_mz_error_ppm")),
        "axis_offset_ppm": acquisition_axis.axis_offset_ppm(record),
        "model_mode": mode
        if isinstance(mode, int) and not isinstance(mode, bool)
        else None,
        "node_version": (
            node_version
            if isinstance(node_version, int) and not isinstance(node_version, bool)
            else None
        ),
        "fitted_utc": _utc(acquisition_axis.fitted_at(record)),
    }


def calibrant_key(point: dict) -> tuple[str | None, float] | None:
    """A calibrant line's identity across records: its ion and exact m/z."""
    target = _number(point.get("target_mz"))
    if target is None:
        return None
    ion = point.get("ion")
    return (ion if isinstance(ion, str) else None, round(target, TARGET_MZ_DECIMALS))


def _utc(moment: datetime | None) -> str | None:
    """An instant as ISO 8601 in UTC, as the file listings serve it."""
    return as_utc(moment).isoformat() if moment is not None else None


def build_trend(rows: list[tuple]) -> dict:
    """
    The trend of the files given, collection changes aside.

    :param rows: ``(sample_file_id, filename, datetime_utc, record)`` per file,
        oldest first, each record one :func:`on_the_trend` admits.
    :return: ``files``, one entry per row with its :func:`file_summary`, its
        id, name, acquisition time and the index of its collection version in
        ``collections`` (None for a record that names none); ``calibrants``,
        one per line any record lists, by exact m/z, each with its ``ion``,
        ``target_mz`` and, aligned with ``files``, its ``residual_ppm``, its
        ``offset_ppm`` on the acquisition axis
        (``acquisition_axis.line_offsets``) and whether the fit kept it
        (``retained``), None where a file's record does not list the line;
        and ``collections``, each version of a collection the records name:
        ``id``, ``isotopes_sha256``, its first and last file's acquisition
        time, how many files name it, and the first record fitted with it
        (:func:`_first_fitted`), in the order of their first file.
    """
    files: list[dict] = []
    versions: dict[tuple[str, str], dict] = {}
    lines: dict[tuple[str | None, float], dict] = {}
    for index, (sample_file_id, filename, acquired, record) in enumerate(rows):
        version = record_collection(record)
        summary = file_summary(record)
        if version is not None:
            entry = versions.setdefault(
                version,
                {
                    "id": version[0],
                    "isotopes_sha256": version[1],
                    "first_utc": _utc(acquired),
                    "first_file": index,
                    "n_files": 0,
                    "first_fitted_utc": None,
                    "first_fitted_file_id": None,
                },
            )
            entry["last_utc"] = _utc(acquired)
            entry["n_files"] += 1
            fitted = summary["fitted_utc"] or _utc(acquired)
            if entry["first_fitted_utc"] is None or _instant(fitted) < _instant(
                entry["first_fitted_utc"]
            ):
                entry["first_fitted_utc"] = fitted
                entry["first_fitted_file_id"] = sample_file_id
        files.append(
            {
                "sample_file_id": sample_file_id,
                "filename": filename,
                "datetime_utc": _utc(acquired),
                "collection": version,
                **summary,
            }
        )
        points = record_points(record) or []
        offsets = acquisition_axis.line_offsets(record) or []
        for point, offset in zip(points, offsets):
            key = calibrant_key(point)
            if key is None:
                continue
            line = lines.setdefault(key, {})
            residual = _number(point.get("residual_ppm"))
            retained = point.get("retained") is True
            # A line listed twice in one record - which the node does not
            # write - is read once: kept over left out, then the first.
            if index in line and (line[index][1] or not retained):
                continue
            line[index] = (residual, retained, offset)
    order = sorted(versions, key=lambda version: versions[version]["first_file"])
    position = {version: i for i, version in enumerate(order)}
    for entry in files:
        version = entry["collection"]
        entry["collection"] = position[version] if version is not None else None
    calibrants = []
    for (ion, target), line in sorted(lines.items(), key=lambda item: item[0][1]):
        calibrants.append(
            {
                "ion": ion,
                "target_mz": target,
                "residual_ppm": [
                    line[i][0] if i in line else None for i in range(len(files))
                ],
                "offset_ppm": [
                    line[i][2] if i in line else None for i in range(len(files))
                ],
                "retained": [
                    line[i][1] if i in line else None for i in range(len(files))
                ],
            }
        )
    collections = [
        {key: value for key, value in versions[version].items() if key != "first_file"}
        for version in order
    ]
    return {"files": files, "calibrants": calibrants, "collections": collections}


def _instant(value: str) -> datetime:
    """An ISO 8601 time the trend serves, as an instant to compare."""
    return as_utc(datetime.fromisoformat(value))


def collection_changes(
    collections: list[dict],
    seen_before: set[tuple[str, str]],
    last_before: dict[str, str],
    recorded_before: bool,
) -> list[dict]:
    """
    When the calibrant collection the files were fitted with changed.

    A version of a collection (its id and isotope hash) is new where no file
    acquired before the window was fitted with it. It is marked where it was
    first fitted with (``first_fitted_utc``, ``first_fitted_file_id``): when
    the first record naming it was written (``fitted_at``), or, for a record
    from before that key, when its file was acquired, which no fit of it
    precedes. It is marked ``edited`` when another version of the same
    collection was fitted with before it, ``first_used`` when no version of
    that collection was but one of another, and not at all when nothing was
    fitted with a collection before it: the records begin there.

    So a change is placed when it was made, also where the files refitted
    with it were acquired long before, or in another order than they were
    fitted. A version every file has since been refitted away from is named
    by no record, and its change is not marked.

    :param collections: The trend's collection versions, each with its
        ``id``, ``isotopes_sha256``, ``first_fitted_utc`` and
        ``first_fitted_file_id``.
    :param seen_before: The versions some file acquired before the window was
        fitted with.
    :param last_before: Per collection id the isotope hash of the latest file
        acquired before the window that was fitted with it.
    :param recorded_before: Whether any file acquired before the window names
        a collection.
    :return: Per change, oldest first: ``datetime_utc`` and ``sample_file_id``
        of the first record fitted with the version, ``kind``, the version's
        ``collection`` index, ``collection_id`` and ``isotopes_sha256``, and
        ``previous_isotopes_sha256``, the hash of the collection's latest
        version fitted with before it for an ``edited`` change, else None.
    """
    # By when each was first fitted with; a tie in the order served.
    order = sorted(
        range(len(collections)),
        key=lambda i: (_instant(collections[i]["first_fitted_utc"]), i),
    )
    changes = []
    for rank, position in enumerate(order):
        version = collections[position]
        key = (version["id"], version["isotopes_sha256"])
        if key in seen_before:
            continue
        earlier = [collections[i] for i in order[:rank]]
        same = [other for other in earlier if other["id"] == version["id"]]
        previous = (
            same[-1]["isotopes_sha256"] if same else last_before.get(version["id"])
        )
        if previous is not None:
            kind = "edited"
        elif earlier or recorded_before:
            kind = "first_used"
        else:
            continue
        changes.append(
            {
                "datetime_utc": version["first_fitted_utc"],
                "sample_file_id": version["first_fitted_file_id"],
                "kind": kind,
                "collection": position,
                "collection_id": version["id"],
                "isotopes_sha256": version["isotopes_sha256"],
                "previous_isotopes_sha256": previous,
            }
        )
    # Oldest first already: the versions come in the order they were fitted.
    return changes


def bin_unit(start: datetime, end: datetime) -> str:
    """The bin a window's files are summed up in: ``hour`` or ``day``."""
    return "hour" if end - start <= timedelta(days=TREND_HOURLY_MAX_DAYS) else "day"


def _naive_utc(value: str | None) -> str | None:
    """A UTC time the database wrote without an offset, as :func:`_utc` does."""
    if value is None:
        return None
    return _utc(datetime.fromisoformat(value).replace(tzinfo=timezone.utc))


def binned_trend(raw: dict) -> dict:
    """
    A binned trend from what :data:`_BINNED_SQL` sums up.

    A bin is the files of one hour or day fitted with one collection, any of
    its versions, or with none named: the files of a day a collection took
    over are two bins, one in each collection's colour. Per bin it serves the
    quartiles (``[q1, median, q3]``, None where no file of it has the
    measure) of what :func:`file_summary` reads per file, and per calibrant
    line the quartiles of its residual and of its offset on the acquisition
    axis over the bin's files that list it, kept or left out, and how many
    list it and kept it. A line listed twice in one record counts twice; the
    node writes each once.

    :param raw: The statement's ``bins``, ``lines`` and ``versions`` as the
        database aggregates them, None for none.
    :return: ``bins``, oldest first: ``start_utc``, ``collection_id``,
        ``n_files``, ``n_failed``, ``n_without_lines`` (fits whose record
        lists no lines) and the quartiles of ``axis_offset_ppm``,
        ``post_fit_ppm``, ``n_points`` and ``n_candidates``; ``calibrants``,
        one per line by exact m/z, each with its ``ion``, ``target_mz`` and,
        aligned with ``bins``, the quartiles of its ``residual_ppm`` and its
        ``offset_ppm``, ``n_listed`` and ``n_kept``; and ``collections`` as
        :func:`build_trend` serves them, in the order of their first file,
        which :func:`collection_changes` places the changes from.
    """
    versions = sorted(
        raw.get("versions") or [],
        key=lambda version: (
            datetime.fromisoformat(version["first_utc"]),
            version["first_file_id"],
        ),
    )
    collections = [
        {
            "id": version["collection_id"],
            "isotopes_sha256": version["isotopes_sha256"],
            "first_utc": _naive_utc(version["first_utc"]),
            "last_utc": _naive_utc(version["last_utc"]),
            "n_files": version["n_files"],
            "first_fitted_utc": _naive_utc(version["first_fitted_utc"]),
            "first_fitted_file_id": version["first_fitted_file_id"],
        }
        for version in versions
    ]
    bins = [
        {
            "start_utc": _naive_utc(entry["bucket"]),
            "collection_id": entry["collection_id"],
            "n_files": entry["n_files"],
            "n_failed": entry["n_failed"],
            "n_without_lines": entry["n_without_lines"],
            "axis_offset_ppm": entry["axis_offset_ppm"],
            "post_fit_ppm": entry["post_fit_ppm"],
            "n_points": entry["n_points"],
            "n_candidates": entry["n_candidates"],
        }
        for entry in raw.get("bins") or []
    ]
    index = {
        (entry["start_utc"], entry["collection_id"]): i for i, entry in enumerate(bins)
    }
    lines: dict[tuple[str | None, float], dict] = {}
    for entry in raw.get("lines") or []:
        line = lines.setdefault(
            (entry["ion"], entry["target_mz"]),
            {
                "ion": entry["ion"],
                "target_mz": entry["target_mz"],
                "residual_ppm": [None] * len(bins),
                "offset_ppm": [None] * len(bins),
                "n_listed": [0] * len(bins),
                "n_kept": [0] * len(bins),
            },
        )
        i = index[(_naive_utc(entry["bucket"]), entry["collection_id"])]
        line["residual_ppm"][i] = entry["residual_ppm"]
        line["offset_ppm"][i] = entry["offset_ppm"]
        line["n_listed"][i] = entry["n_listed"]
        line["n_kept"][i] = entry["n_kept"]
    return {
        "bins": bins,
        "calibrants": sorted(lines.values(), key=lambda line: line["target_mz"]),
        "collections": collections,
    }


def _instrument_files(instrument: str, polarity: str):
    """The statement's filters: the instrument's TOF files of one polarity."""
    return (
        SampleFile.instrument == instrument,
        SampleFile.polarity == polarity,
        SampleFile.instrument_type == "tof",
    )


def on_the_trend():
    """
    SQL for whether a file's record belongs on its instrument's trend.

    A fit (a record with a quality block, a JSON object) and a calibration the
    pipeline gave up on (``status: "failed"``) do: the second has no numbers,
    but a run of failures is part of a calibrant set's history. The
    converter's record of the acquisition axis, ``unfitted``
    (``is_unfitted_record`` in the calibration controller), carries neither,
    and a fit recorded before quality blocks were has no ``quality``: they say
    nothing about a fit. Admitting in SQL keeps them out of the
    :data:`TREND_MAX_FILES` a trend reads, and their records unread.
    :data:`_BINNED_SQL` spells the same test.
    """
    record = SampleFile.mz_calibration
    return or_(
        func.json_typeof(record["quality"]) == "object",
        record["status"].as_string() == "failed",
    )


def _collection_id_of():
    """SQL for a record's collection id, NULL where it names none: the
    expression ``sample_file``'s collection indexes hold."""
    return record_collection_id(SampleFile.mz_calibration)


def _collection_sha_of():
    """SQL for a record's collection isotope hash, NULL where it names none:
    the expression ``sample_file``'s collection indexes hold."""
    return record_collection_sha(SampleFile.mz_calibration)


def _sql_number(path: str) -> str:
    """SQL for the JSONB value at ``path`` of ``rec`` as a float, NULL where it
    is not a number."""
    return (
        f"CASE WHEN jsonb_typeof(rec #> '{path}') = 'number' "
        f"THEN (rec #>> '{path}')::float8 END"
    )


#: SQL for a calibrant line's offset on the acquisition axis, as
#: ``acquisition_axis.line_offsets`` reads it, for a point ``p`` of a record
#: whose fields ``f`` reads and whose exact m/z is ``l.target``: with an axis,
#: the axis's m/z at the fit's index of the exact m/z (``i.raw``); with kept
#: offsets, the first kept for the line's exact m/z; with neither, the
#: observed m/z against the exact. Every division and power is guarded with
#: a CASE, which alone fixes the order SQL evaluates in.
_LINE_OFFSET_SQL = """
CASE WHEN l.target > 0 THEN
    CASE
    WHEN f.axis IS NOT NULL THEN 1e6 * (i.raw - l.target) / l.target
    WHEN f.kept_offsets IS NOT NULL THEN (
        SELECT CASE WHEN jsonb_typeof(o -> 'offset_ppm') = 'number'
                    THEN (o ->> 'offset_ppm')::float8 END
        FROM jsonb_array_elements(f.kept_offsets) WITH ORDINALITY AS k(o, n)
        WHERE CASE WHEN jsonb_typeof(o -> 'target_mz') = 'number'
                   THEN round((o ->> 'target_mz')::numeric, :decimals) END
              = round(l.target::numeric, :decimals)
        ORDER BY n
        LIMIT 1
    )
    WHEN jsonb_typeof(p -> 'observed_mz') = 'number'
        THEN 1e6 * ((p ->> 'observed_mz')::float8 - l.target) / l.target
    END
END
"""

#: SQL for the acquisition axis's m/z at the fit's index of ``l.target``
#: (:func:`acquisition_axis.index_at`, :func:`acquisition_axis.mz_at`), the
#: index moved by the samples the stored axis starts after.
_RAW_MZ_SQL = """
SELECT CASE f.axis_mode
    WHEN 0 THEN CASE WHEN f.ap0 <> 0 THEN power((j.j - f.ap1) / f.ap0, 2) END
    WHEN 1 THEN CASE WHEN j.j <> f.ap1 THEN power(f.ap0 / (j.j - f.ap1), 2) END
    WHEN 2 THEN CASE WHEN f.ap0 <> 0 AND f.ap2 <> 0 THEN
        CASE WHEN (j.j - f.ap1) / f.ap0 > 0
             THEN power((j.j - f.ap1) / f.ap0, 1 / f.ap2) END END
    WHEN 5 THEN f.ap0 * j.j * j.j + f.ap1 * j.j + f.ap2
END AS raw
FROM (
    SELECT CASE WHEN l.target > 0 THEN
        CASE f.fit_mode
        WHEN 0 THEN f.fp0 * sqrt(l.target) + f.fp1
        WHEN 1 THEN f.fp0 / sqrt(l.target) + f.fp1
        WHEN 2 THEN f.fp0 * power(l.target, f.fp2) + f.fp1
        END
    END + f.axis_shift AS j
) j
"""

#: A binned trend, summed up in the database (:func:`binned_trend`).
#:
#: ``latest`` admits the window's files as :func:`on_the_trend` does and keeps
#: the latest :data:`TREND_MAX_BINNED_FILES`, plus one that says the window
#: held more. ``f`` reads each kept record once, as JSONB, and takes from it
#: what :func:`file_summary` and :func:`record_collection` read; the ``OFFSET
#: 0`` keeps the planner from casting the record again for every field taken.
#: ``pts`` reads each calibrant line of each record once, with its offset on
#: the acquisition axis (:data:`_LINE_OFFSET_SQL`). Per bin of one hour or
#: day and one collection id, ``file_bins`` sums up the files and
#: ``line_bins`` each calibrant line; ``versions`` counts each collection
#: version's files and finds its first file and the first record fitted with
#: it, which is all that :func:`collection_changes` needs. Times leave as UTC
#: without an offset, as JSON writes them whatever the session's time zone.
_BINNED_SQL = text(
    """
WITH latest AS MATERIALIZED (
    SELECT sample_file_id, datetime_utc
    FROM sample_file
    WHERE instrument = :instrument
      AND polarity = :polarity
      AND instrument_type = 'tof'
      AND datetime_utc >= :start
      AND datetime_utc <= :end
      AND (json_typeof(mz_calibration -> 'quality') = 'object'
           OR mz_calibration ->> 'status' = 'failed')
    ORDER BY datetime_utc DESC, sample_file_id DESC
    LIMIT :cap + 1
),
kept AS (
    SELECT sample_file_id FROM latest
    ORDER BY datetime_utc DESC, sample_file_id DESC
    LIMIT :cap
),
f AS MATERIALIZED (
    SELECT
        sample_file_id,
        datetime_utc,
        datetime_utc AT TIME ZONE 'UTC' AS at_utc,
        CASE WHEN jsonb_typeof(rec -> 'fitted_at') = 'string'
              AND (rec ->> 'fitted_at') ~ :fitted_at_pattern
             THEN ((rec ->> 'fitted_at')::timestamptz) AT TIME ZONE 'UTC'
             END AS fitted_utc,
        date_trunc(:unit, datetime_utc AT TIME ZONE 'UTC') AS bucket,
        rec ->> 'status' AS status,
        CASE WHEN jsonb_typeof(rec #> '{quality,collection,id}') = 'string'
              AND jsonb_typeof(rec #> '{quality,collection,isotopes_sha256}')
                  = 'string'
             THEN rec #>> '{quality,collection,id}' END AS collection_id,
        CASE WHEN jsonb_typeof(rec #> '{quality,collection,id}') = 'string'
              AND jsonb_typeof(rec #> '{quality,collection,isotopes_sha256}')
                  = 'string'
             THEN rec #>> '{quality,collection,isotopes_sha256}'
             END AS isotopes_sha256,
        CASE WHEN jsonb_typeof(rec #> '{quality,post_fit_mz_error_ppm}')
                  = 'number'
             THEN (rec #>> '{quality,post_fit_mz_error_ppm}')::float8
             END AS post_fit,
        CASE WHEN (rec #>> '{quality,n_points}') ~ '^-?[0-9]+$'
              AND jsonb_typeof(rec #> '{quality,n_points}') = 'number'
             THEN (rec #>> '{quality,n_points}')::float8 END AS n_points,
        CASE WHEN jsonb_typeof(rec #> '{quality,points}') = 'array'
             THEN rec #> '{quality,points}' END AS points,
        CASE WHEN jsonb_typeof(rec -> 'acquisition_axis') = 'object'
             THEN rec -> 'acquisition_axis' END AS axis,
        CASE WHEN jsonb_typeof(rec -> 'acquisition_offsets') = 'array'
             THEN rec -> 'acquisition_offsets' END AS kept_offsets,
        __FIT_MODE__ AS fit_mode,
        __FP0__ AS fp0,
        __FP1__ AS fp1,
        __FP2__ AS fp2,
        __AXIS_MODE__ AS axis_mode,
        __AP0__ AS ap0,
        __AP1__ AS ap1,
        __AP2__ AS ap2,
        __AXIS_SHIFT__ AS axis_shift
    FROM (
        SELECT sample_file_id, datetime_utc, mz_calibration::jsonb AS rec
        FROM sample_file
        WHERE sample_file_id IN (SELECT sample_file_id FROM kept)
        OFFSET 0
    ) records
),
pts AS MATERIALIZED (
    SELECT
        f.sample_file_id,
        f.bucket,
        f.collection_id,
        p,
        __LINE_OFFSET__ AS line_offset
    FROM f
    CROSS JOIN LATERAL jsonb_array_elements(f.points) AS p
    CROSS JOIN LATERAL (
        SELECT CASE WHEN jsonb_typeof(p -> 'target_mz') = 'number'
                    THEN (p ->> 'target_mz')::float8 END AS target
    ) l
    CROSS JOIN LATERAL (__RAW_MZ__) i
    WHERE jsonb_typeof(p) = 'object'
),
file_lines AS (
    SELECT
        sample_file_id,
        count(*) AS n_candidates,
        avg(line_offset) FILTER (WHERE p -> 'retained' = 'true'::jsonb)
            AS axis_offset
    FROM pts
    GROUP BY sample_file_id
),
per_file AS (
    SELECT f.bucket, f.collection_id, f.status, f.post_fit, f.n_points,
        CASE WHEN f.points IS NOT NULL
             THEN coalesce(lines.n_candidates, 0) END AS n_candidates,
        lines.axis_offset
    FROM f
    LEFT JOIN file_lines lines USING (sample_file_id)
),
file_bins AS (
    SELECT
        bucket,
        collection_id,
        count(*) AS n_files,
        count(*) FILTER (WHERE status = 'failed') AS n_failed,
        count(*) FILTER (
            WHERE status IS DISTINCT FROM 'failed' AND n_candidates IS NULL
        ) AS n_without_lines,
        percentile_cont(ARRAY[0.25, 0.5, 0.75])
            WITHIN GROUP (ORDER BY axis_offset) AS axis_offset_ppm,
        percentile_cont(ARRAY[0.25, 0.5, 0.75])
            WITHIN GROUP (ORDER BY post_fit) AS post_fit_ppm,
        percentile_cont(ARRAY[0.25, 0.5, 0.75])
            WITHIN GROUP (ORDER BY n_points) AS n_points,
        percentile_cont(ARRAY[0.25, 0.5, 0.75])
            WITHIN GROUP (ORDER BY n_candidates) AS n_candidates
    FROM per_file
    GROUP BY bucket, collection_id
),
line_bins AS (
    SELECT
        bucket,
        collection_id,
        CASE WHEN jsonb_typeof(p -> 'ion') = 'string' THEN p ->> 'ion' END AS ion,
        round((p ->> 'target_mz')::numeric, :decimals)::float8 AS target_mz,
        percentile_cont(ARRAY[0.25, 0.5, 0.75]) WITHIN GROUP (
            ORDER BY CASE WHEN jsonb_typeof(p -> 'residual_ppm') = 'number'
                          THEN (p ->> 'residual_ppm')::float8 END
        ) AS residual_ppm,
        percentile_cont(ARRAY[0.25, 0.5, 0.75])
            WITHIN GROUP (ORDER BY line_offset) AS offset_ppm,
        count(*) AS n_listed,
        count(*) FILTER (WHERE p -> 'retained' = 'true'::jsonb) AS n_kept
    FROM pts
    WHERE jsonb_typeof(p -> 'target_mz') = 'number'
    GROUP BY 1, 2, 3, 4
),
versions AS (
    SELECT collection_id, isotopes_sha256,
        min(at_utc) AS first_utc, max(at_utc) AS last_utc, count(*) AS n_files,
        (array_agg(sample_file_id ORDER BY datetime_utc, sample_file_id))[1]
            AS first_file_id,
        min(coalesce(fitted_utc, at_utc)) AS first_fitted_utc,
        (array_agg(
            sample_file_id
            ORDER BY coalesce(fitted_utc, at_utc), datetime_utc, sample_file_id
        ))[1] AS first_fitted_file_id
    FROM f
    WHERE collection_id IS NOT NULL
    GROUP BY collection_id, isotopes_sha256
)
SELECT
    (SELECT count(*) FROM kept) AS n_files,
    (SELECT count(*) FROM latest) > :cap AS truncated,
    (SELECT min(datetime_utc) FROM f) AS earliest,
    (SELECT json_agg(b ORDER BY bucket, collection_id) FROM file_bins b) AS bins,
    (SELECT json_agg(l ORDER BY target_mz, ion, bucket, collection_id)
     FROM line_bins l) AS lines,
    (SELECT json_agg(v) FROM versions v) AS versions
""".replace("__LINE_OFFSET__", _LINE_OFFSET_SQL)
    .replace("__RAW_MZ__", _RAW_MZ_SQL)
    .replace("__FIT_MODE__", _sql_number("{mode}"))
    .replace("__FP0__", _sql_number("{par,0}"))
    .replace("__FP1__", _sql_number("{par,1}"))
    .replace("__FP2__", _sql_number("{par,2}"))
    .replace("__AXIS_MODE__", _sql_number("{acquisition_axis,mode}"))
    .replace("__AP0__", _sql_number("{acquisition_axis,par,0}"))
    .replace("__AP1__", _sql_number("{acquisition_axis,par,1}"))
    .replace("__AP2__", _sql_number("{acquisition_axis,par,2}"))
    .replace("__AXIS_SHIFT__", _sql_number("{acquisition_axis,raw_index_offset}"))
).columns(
    n_files=Integer,
    truncated=Boolean,
    earliest=TIMESTAMP(timezone=True),
    bins=JSON,
    lines=JSON,
    versions=JSON,
)


def _earlier_files(instrument: str, polarity: str, start: datetime):
    """The statement's filters: the instrument's TOF files of one polarity
    acquired before ``start``."""
    return (
        *_instrument_files(instrument, polarity),
        SampleFile.datetime_utc < start,
    )


def _version_look(instrument: str, polarity: str, start: datetime, key):
    """The latest earlier file fitted with the collection version ``key``,
    ``(id, isotope hash)``: a read of
    ``ix_sample_file_instrument_polarity_collection_version``."""
    return (
        select(SampleFile.sample_file_id)
        .where(
            *_earlier_files(instrument, polarity, start),
            _collection_id_of() == key[0],
            _collection_sha_of() == key[1],
        )
        .order_by(SampleFile.datetime_utc.desc())
        .limit(1)
    )


def _collection_look(instrument: str, polarity: str, start: datetime, collection_id):
    """The isotope hash of the latest earlier file fitted with the collection:
    a read of ``ix_sample_file_instrument_polarity_collection``."""
    return (
        select(_collection_sha_of())
        .where(
            *_earlier_files(instrument, polarity, start),
            _collection_id_of() == collection_id,
        )
        .order_by(SampleFile.datetime_utc.desc())
        .limit(1)
    )


def _named_look(instrument: str, polarity: str, start: datetime):
    """
    An earlier file that names a collection, if any: a read of
    ``ix_sample_file_instrument_polarity_collection``.

    Which file does not matter. The index holds the instrument and the
    polarity ahead of the collection id, so the read keeps to this
    polarity's entries: on a TOF that runs both, the other polarity's are
    never read. The look is ordered as that index is, by
    the collection id before the time, so that no other read serves it
    without sorting every file it keeps. Ordered by the time alone, a walk
    back on a time index serves it too, and the planner takes that walk
    where it believes the files that name a collection dense. Its
    statistics are the whole table's: other instruments' files can make them
    dense, and on an instrument whose earlier files name none the walk reads
    every record of its history.
    """
    return (
        select(SampleFile.sample_file_id)
        .where(
            *_earlier_files(instrument, polarity, start),
            record_names_a_collection(SampleFile.mz_calibration),
        )
        .order_by(_collection_id_of().desc(), SampleFile.datetime_utc.desc())
        .limit(1)
    )


async def _before_the_window(
    session,
    instrument: str,
    polarity: str,
    start: datetime,
    collections: list[dict],
) -> tuple[set[tuple[str, str]], dict[str, str], bool]:
    """
    What the files acquired before a window say of the collections in it.

    Each look is one read of an index of the files that name a collection
    (``SampleFile.__table_args__``), back from the window's start: the
    version's own entries, the collection's, or the instrument's. A version
    or a collection never used before finds none at once, rather than
    walking the instrument's history for it.

    :return: The versions in ``collections`` an earlier file was fitted with;
        per collection id of theirs, the isotope hash of the latest earlier
        file fitted with that collection; and whether any earlier file names a
        collection.
    """
    seen, last = set(), {}
    for version in collections:
        key = (version["id"], version["isotopes_sha256"])
        found = await session.execute(_version_look(instrument, polarity, start, key))
        if found.first() is not None:
            seen.add(key)
    for collection_id in {version["id"] for version in collections}:
        found = await session.execute(
            _collection_look(instrument, polarity, start, collection_id)
        )
        sha = found.scalar_one_or_none()
        if sha is not None:
            last[collection_id] = sha
    found = await session.execute(_named_look(instrument, polarity, start))
    recorded = found.first() is not None
    return seen, last, recorded


async def _collection_names(
    session, collection_ids: set[str], scope: WorkspaceScope
) -> dict[str, str]:
    """The names of the collections the asker can read, by id."""
    if not collection_ids:
        return {}
    stmt = select(
        TargetCollection.target_collection_id, TargetCollection.target_collection_name
    ).where(TargetCollection.target_collection_id.in_(collection_ids))
    result = await session.execute(where_collection_visible(stmt, scope))
    return dict(result.all())


def _window(
    datetime_min: datetime | None, datetime_max: datetime | None
) -> tuple[datetime, datetime]:
    """A trend's window: its end, now when None, and its start,
    :data:`TREND_DEFAULT_DAYS` before the end when None."""
    end = as_utc(datetime_max) if datetime_max else datetime.now(timezone.utc)
    start = (
        as_utc(datetime_min)
        if datetime_min
        else end - timedelta(days=TREND_DEFAULT_DAYS)
    )
    return start, end


def _in_the_window(instrument: str, polarity: str, start: datetime, end: datetime):
    """The statement's filters: the instrument's files a trend of the window
    reads (:func:`on_the_trend`)."""
    return (
        *_instrument_files(instrument, polarity),
        SampleFile.datetime_utc >= start,
        SampleFile.datetime_utc <= end,
        on_the_trend(),
    )


async def _count_on_the_trend(
    session, instrument: str, polarity: str, start: datetime, end: datetime, most: int
) -> int:
    """How many of the window's files are on the trend, counted up to ``most``:
    the count stops there, and its records are not read."""
    admitted = (
        select(literal(1))
        .where(*_in_the_window(instrument, polarity, start, end))
        .limit(most)
        .subquery()
    )
    result = await session.execute(select(func.count()).select_from(admitted))
    return result.scalar_one()


def _record_text():
    """SQL for a file's record as the text the database keeps.

    Read as text, a record is parsed where the caller chooses
    (:func:`_parsed`): in a worker thread, not on the event loop, which the
    driver would parse the JSON column on as it fetches it.
    """
    return cast(SampleFile.mz_calibration, Text)


def _parsed(rows) -> list[tuple]:
    """``(sample_file_id, filename, datetime_utc, record)`` per row read with
    :func:`_record_text`, its record parsed."""
    return [
        (sample_file_id, filename, acquired, json.loads(record))
        for sample_file_id, filename, acquired, record in rows
    ]


def _build_trend_of_text(rows) -> dict:
    """:func:`build_trend` of rows read with :func:`_record_text`, oldest
    first; run in a worker thread."""
    return build_trend(_parsed(rows))


async def _read_files(
    session, instrument: str, polarity: str, start: datetime, end: datetime, most: int
) -> tuple[dict, bool, datetime]:
    """
    The window's files on the trend, one by one: the latest ``most`` of them.

    Their records are parsed and the trend built in a worker thread
    (:func:`_build_trend_of_text`), so that a trend of thousands of files
    holds up no other request the worker serves.

    :return: :func:`build_trend`'s trend of them; whether the window held
        more; and where the files read begin, for their changes: the window's
        start, or the earliest file read when it held more, since the files
        left out were acquired before.
    """
    result = await session.execute(
        select(
            SampleFile.sample_file_id,
            SampleFile.filename,
            SampleFile.datetime_utc,
            _record_text(),
        )
        .where(*_in_the_window(instrument, polarity, start, end))
        .order_by(SampleFile.datetime_utc.desc(), SampleFile.sample_file_id.desc())
        .limit(most + 1)
    )
    read = result.all()
    truncated = len(read) > most
    # The latest files of a window that holds too many; then oldest first.
    read = read[:most]
    since = read[-1].datetime_utc if truncated else start
    trend = await asyncio.to_thread(
        _build_trend_of_text, [tuple(row) for row in reversed(read)]
    )
    return trend, truncated, since


async def _read_bins(
    session, instrument: str, polarity: str, start: datetime, end: datetime
) -> tuple[dict, int, bool, datetime]:
    """
    The window's files on the trend, in bins (:func:`binned_trend`): the
    latest :data:`TREND_MAX_BINNED_FILES` of them.

    :return: The binned trend; how many files it sums up; whether the window
        held more; and where the files read begin, as :func:`_read_files`
        says.
    """
    result = await session.execute(
        _BINNED_SQL,
        {
            "instrument": instrument,
            "polarity": polarity,
            "start": start,
            "end": end,
            "unit": bin_unit(start, end),
            "cap": TREND_MAX_BINNED_FILES,
            "decimals": TARGET_MZ_DECIMALS,
            "fitted_at_pattern": acquisition_axis.FITTED_AT_PATTERN.pattern,
        },
    )
    row = result.one()
    since = row.earliest if row.truncated else start
    return binned_trend(row._mapping), row.n_files, row.truncated, since


async def _changes(
    session,
    instrument: str,
    polarity: str,
    since: datetime,
    collections: list[dict],
) -> list[dict]:
    """The :func:`collection_changes` of the versions of files read from
    ``since`` on."""
    if not collections:
        return []
    seen, last, recorded = await _before_the_window(
        session, instrument, polarity, since, collections
    )
    return await asyncio.to_thread(
        collection_changes, collections, seen, last, recorded
    )


async def _name_collections(session, collections: list[dict], scope) -> None:
    """Give each collection version its ``name``: None where the asker cannot
    read the collection or it is gone."""
    names = await _collection_names(
        session, {version["id"] for version in collections}, scope
    )
    for version in collections:
        version["name"] = names.get(version["id"])


async def _read_trend(
    instrument: str,
    polarity: str,
    scope: WorkspaceScope,
    datetime_min: datetime | None,
    datetime_max: datetime | None,
) -> dict:
    """Read a trend: :func:`get_calibration_trend`'s ``data``."""
    start, end = _window(datetime_min, datetime_max)
    async with async_session() as session:
        binned = (
            await _count_on_the_trend(
                session, instrument, polarity, start, end, TREND_MAX_FILES + 1
            )
            > TREND_MAX_FILES
        )
        if binned:
            trend, n_files, truncated, since = await _read_bins(
                session, instrument, polarity, start, end
            )
            trend["files"] = []
        else:
            trend, truncated, since = await _read_files(
                session, instrument, polarity, start, end, TREND_MAX_FILES
            )
            n_files = len(trend["files"])
            trend["bins"] = []
        changes = await _changes(
            session, instrument, polarity, since, trend["collections"]
        )
        await _name_collections(session, trend["collections"], scope)
    return {
        "instrument": instrument,
        "polarity": polarity,
        "datetime_min": start.isoformat(),
        "datetime_max": end.isoformat(),
        "binned": binned,
        "bin": bin_unit(start, end),
        "n_files": n_files,
        "truncated": truncated,
        "files": trend["files"],
        "bins": trend["bins"],
        "calibrants": trend["calibrants"],
        "collections": trend["collections"],
        "changes": changes,
    }


@api_controller()
async def get_calibration_trend(
    instrument: str,
    polarity: str,
    scope: WorkspaceScope,
    datetime_min: datetime | None = None,
    datetime_max: datetime | None = None,
) -> dict:
    """
    The calibration trend of one polarity of a TOF instrument.

    :param instrument: The instrument's name, as its files spell it.
    :param polarity: ``+`` or ``-``.
    :param scope: The workspaces whose collections the asker can read: a
        collection is named only where it is one of them or global.
    :param datetime_min: The earliest acquisition time read, inclusive;
        :data:`TREND_DEFAULT_DAYS` before the end when None.
    :param datetime_max: The latest, inclusive; now when None. A time without
        an offset is read as UTC.
    :return: ``data``: the window (``datetime_min``, ``datetime_max``);
        ``n_files``, how many of its files are on the trend
        (:func:`on_the_trend`); ``binned``, whether they are more than
        :data:`TREND_MAX_FILES` and served in bins, and ``bin``, the bins'
        width for the window (:func:`bin_unit`); ``truncated``, whether the
        window held more files than are read and only the latest are served;
        :func:`build_trend`'s ``files`` one by one, or :func:`binned_trend`'s
        ``bins`` (the other empty), with the ``calibrants`` aligned with them;
        ``collections``, each with its ``name`` (None where the asker cannot
        read it or it is gone); and the :func:`collection_changes` as
        ``changes``.
    """
    data = await _read_trend(instrument, polarity, scope, datetime_min, datetime_max)
    served = (
        f"{data['n_files']} file(s) in {len(data['bins'])} bin(s)"
        if data["binned"]
        else f"{data['n_files']} file(s)"
    )
    return {
        "message": (
            f"Retrieved the calibration trend of {served} "
            f"of instrument '{instrument}' ({polarity})."
        ),
        "data": data,
    }


def _cell(value) -> str | float | int | bool:
    """
    A value as the export writes it: empty for None, and a text that a
    spreadsheet would run as a formula - a file or collection name is typed
    by someone - behind an apostrophe, which shows it as the text it is
    (:func:`mascope_sdk.csv_cells.escape_cell`).
    """
    if value is None:
        return ""
    return escape_cell(value)


def trend_rows(rows: list[tuple], names: dict[str, str]) -> list[list]:
    """
    The export's rows (:data:`EXPORT_COLUMNS`) for files and their records.

    :param rows: ``(sample_file_id, filename, datetime_utc, record)`` per
        file, oldest first, each record one :func:`on_the_trend` admits.
    :param names: The names of the collections the asker can read, by id.
    :return: One row per line of each file's record, in the record's order,
        and one for a file whose record lists none.
    """
    out = []
    for sample_file_id, filename, acquired, record in rows:
        entry = file_summary(record)
        version = record_collection(record)
        collection_id, sha = version if version is not None else (None, None)
        head = [
            _utc(acquired),
            sample_file_id,
            filename,
            entry["status"],
            collection_id,
            names.get(collection_id) if collection_id is not None else None,
            sha,
            entry["model_mode"],
            entry["node_version"],
            entry["n_points"],
            entry["n_candidates"],
            entry["post_fit_ppm"],
            entry["axis_offset_ppm"],
            entry["pre_fit_abs_ppm"],
        ]
        points = record_points(record) or [None]
        offsets = acquisition_axis.line_offsets(record) or [None]
        for point, offset in zip(points, offsets):
            tail = (
                [
                    point.get("ion") if isinstance(point.get("ion"), str) else None,
                    _number(point.get("target_mz")),
                    _number(point.get("observed_mz")),
                    offset,
                    _number(point.get("residual_ppm")),
                    _number(point.get("snr")),
                    point.get("retained") is True,
                ]
                if point is not None
                else [None] * 7
            )
            out.append([_cell(value) for value in head + tail])
    return out


def csv_text(rows: list[list]) -> str:
    """Rows of the export as CSV text, each line ended with a newline."""
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\n").writerows(rows)
    return buffer.getvalue()


def _csv_piece(rows, names: dict[str, str]) -> str:
    """The CSV of files read with :func:`_record_text`, oldest first: one
    piece of the export's body, made in a worker thread."""
    return csv_text(trend_rows(_parsed(rows), names))


def _export_keyset():
    """SQL for the order the export lists files in, oldest first."""
    return tuple_(SampleFile.datetime_utc, SampleFile.sample_file_id)


async def _export_cut(
    session, instrument: str, polarity: str, start: datetime, end: datetime
) -> tuple[datetime, str] | None:
    """
    Where the files the export lists begin: the acquisition time and id of
    the oldest of the window's latest :data:`EXPORT_MAX_FILES` on the trend,
    or None where the window holds no more than those. Read without the
    records, which stay in the database.
    """
    result = await session.execute(
        select(SampleFile.datetime_utc, SampleFile.sample_file_id)
        .where(*_in_the_window(instrument, polarity, start, end))
        .order_by(SampleFile.datetime_utc.desc(), SampleFile.sample_file_id.desc())
        .offset(EXPORT_MAX_FILES - 1)
        .limit(1)
    )
    row = result.first()
    return tuple(row) if row is not None else None


def _exported(
    instrument: str,
    polarity: str,
    start: datetime,
    end: datetime,
    cut: tuple[datetime, str] | None,
):
    """The statement's filters: the files the export lists (:func:`_export_cut`)."""
    window = _in_the_window(instrument, polarity, start, end)
    if cut is None:
        return window
    return (*window, _export_keyset() >= tuple_(*cut))


async def _export_body(
    instrument: str,
    polarity: str,
    start: datetime,
    end: datetime,
    cut: tuple[datetime, str] | None,
    names: dict[str, str],
) -> AsyncIterator[str]:
    """
    The export's CSV, piece by piece: its header, then
    :data:`EXPORT_FILES_PER_PIECE` files at a time, oldest first.

    The records are read from a cursor as they are written out, so neither
    they nor the table are ever held whole; each piece is parsed and written
    in a worker thread (:func:`_csv_piece`). The database session is opened
    when the first piece of files is asked for, and closed with the body
    (``ClosingStreamingResponse``), however the response ends.

    A failure part way is logged and ends the body with
    :data:`~mascope_sdk.csv_cells.INCOMPLETE_LINE` after the last whole
    piece, which ``read_export_csv`` refuses: the status is sent by then.
    """
    yield csv_text([list(EXPORT_COLUMNS)])
    try:
        async with async_session() as session:
            result = await session.stream(
                select(
                    SampleFile.sample_file_id,
                    SampleFile.filename,
                    SampleFile.datetime_utc,
                    _record_text(),
                )
                .where(*_exported(instrument, polarity, start, end, cut))
                .order_by(
                    SampleFile.datetime_utc.asc(), SampleFile.sample_file_id.asc()
                )
                .execution_options(yield_per=EXPORT_FILES_PER_PIECE)
            )
            async for rows in result.partitions():
                yield await asyncio.to_thread(_csv_piece, rows, names)
    except Exception:
        # The response has started: its status cannot say so any more, and
        # the app's middleware ends the body cleanly whatever is raised. So
        # the file itself says it stopped, after its last whole row.
        runtime.logger.exception(
            f"Exporting the calibration trend of instrument '{instrument}' "
            f"({polarity}) failed part way; the CSV ends with the line that "
            "says so"
        )
        yield INCOMPLETE_LINE + "\n"


@api_controller()
async def export_calibration_trend(
    instrument: str,
    polarity: str,
    scope: WorkspaceScope,
    datetime_min: datetime | None = None,
    datetime_max: datetime | None = None,
) -> AsyncIterator[str]:
    """
    The calibration trend as a CSV table, one row per calibrant line of each
    file (:data:`EXPORT_COLUMNS`), oldest file first.

    The window, the files and the collection names are those of
    :func:`get_calibration_trend` for the same arguments, the files always
    one by one, also where the trend serves them in bins: the latest
    :data:`EXPORT_MAX_FILES` of a window that holds more. A text cell that a
    spreadsheet would read as a formula is written with a leading apostrophe
    (:func:`_cell`).

    Where the files begin and the names of their collections are read here,
    so that a window that cannot be read is answered with an error status;
    the records are read as the table is sent (:func:`_export_body`).

    :return: The CSV text, with a header row, as pieces to stream: an async
        generator the caller sends from and closes.
    """
    start, end = _window(datetime_min, datetime_max)
    async with async_session() as session:
        cut = await _export_cut(session, instrument, polarity, start, end)
        result = await session.execute(
            select(_collection_id_of())
            .where(*_exported(instrument, polarity, start, end, cut))
            .distinct()
        )
        ids = {collection_id for collection_id in result.scalars() if collection_id}
        names = await _collection_names(session, ids, scope)
    return _export_body(instrument, polarity, start, end, cut, names)
