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
- the offset of the axis the fit read its peaks on (``axis_offset_ppm``): the
  signed mean error of the kept lines before the fit. On a file's first fit
  that is the acquisition's own axis. A refit reads the peaks on the axis the
  previous fit set, so its offset is that fit's error, near zero; the record
  does not keep the acquisition's coefficients to read the raw axis again.

A record written before the points were kept has its counts and mean errors
only: its file is listed with no calibrant residuals and no signed offset, and
``pre_fit_abs_ppm`` gives the size of its offset.

A window of more files than a chart can draw one by one is served in bins
instead (:func:`binned_trend`): per hour or per day, the quartiles of each of
those measures over the bin's files, summed up in the database, so that a
year of a busy instrument is read whole and answered in a few hundred bins.
"""

import csv
import io
import math
from datetime import datetime, timedelta, timezone

from sqlalchemy import JSON, Boolean, Integer, func, literal, or_, select, text
from sqlalchemy.types import TIMESTAMP

from mascope_backend.api.lib.api_features import api_controller
from mascope_backend.api.lib.datetimes import as_utc
from mascope_backend.api.new.workspaces.dependencies import (
    WorkspaceScope,
    where_collection_visible,
)
from mascope_backend.db import SampleFile, TargetCollection, async_session


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

#: A window of this many days or fewer is binned per hour, a longer one per
#: day.
TREND_HOURLY_MAX_DAYS = 30

#: Decimals a calibrant line's exact m/z is told apart by: a line's exact m/z
#: is the same number in every record that lists it.
TARGET_MZ_DECIMALS = 4

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


def _offset_ppm(point: dict) -> float | None:
    """How far a line read off before the fit: observed against exact m/z, ppm."""
    target, observed = (
        _number(point.get("target_mz")),
        _number(point.get("observed_mz")),
    )
    if target is None or observed is None or target <= 0:
        return None
    return 1e6 * (observed - target) / target


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
        ``axis_offset_ppm``, the signed mean of their error before it (None
        where the record lists no lines); ``model_mode``; ``node_version``.
    """
    quality = record.get("quality") if isinstance(record.get("quality"), dict) else {}
    points = record_points(record)
    offsets = [
        offset
        for point in points or []
        if point.get("retained") is True and (offset := _offset_ppm(point)) is not None
    ]
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
        "axis_offset_ppm": sum(offsets) / len(offsets) if offsets else None,
        "model_mode": mode
        if isinstance(mode, int) and not isinstance(mode, bool)
        else None,
        "node_version": (
            node_version
            if isinstance(node_version, int) and not isinstance(node_version, bool)
            else None
        ),
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
        ``target_mz`` and, aligned with ``files``, its ``residual_ppm`` and
        whether the fit kept it (``retained``), None where a file's record does
        not list the line; and ``collections``, each version of a collection
        the records name: ``id``, ``isotopes_sha256``, its first and last
        file's acquisition time and how many files name it, in the order of
        their first file.
    """
    files: list[dict] = []
    versions: dict[tuple[str, str], dict] = {}
    lines: dict[tuple[str | None, float], dict] = {}
    for index, (sample_file_id, filename, acquired, record) in enumerate(rows):
        version = record_collection(record)
        if version is not None:
            entry = versions.setdefault(
                version,
                {
                    "id": version[0],
                    "isotopes_sha256": version[1],
                    "first_utc": _utc(acquired),
                    "first_file": index,
                    "n_files": 0,
                },
            )
            entry["last_utc"] = _utc(acquired)
            entry["n_files"] += 1
        files.append(
            {
                "sample_file_id": sample_file_id,
                "filename": filename,
                "datetime_utc": _utc(acquired),
                "collection": version,
                **file_summary(record),
            }
        )
        for point in record_points(record) or []:
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
            line[index] = (residual, retained)
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


def collection_changes(
    files: list[dict],
    collections: list[dict],
    seen_before: set[tuple[str, str]],
    last_before: dict[str, str],
    recorded_before: bool,
) -> list[dict]:
    """
    When the calibrant collection the files were fitted with changed.

    A version of a collection (its id and isotope hash) is new where no file
    acquired before it was fitted with it. Its first file is then marked, as
    ``edited`` when an earlier file was fitted with another version of the
    same collection, as ``first_used`` when no earlier file was fitted with
    that collection at all but some file with another, and not at all when
    no earlier file names a collection: the records begin there.

    Files are ordered by when they were acquired, not by when they were
    fitted, which no record says. A file refitted with a newer version
    therefore moves that version's first file back to its own acquisition
    time; a version every file has since been refitted away from is named by
    no record, and its change is not marked.

    :param files: The trend's files, oldest first (:func:`build_trend`).
    :param collections: Its collection versions, in the order of their first
        file.
    :param seen_before: The versions some file acquired before the window was
        fitted with.
    :param last_before: Per collection id the isotope hash of the latest file
        acquired before the window that was fitted with it.
    :param recorded_before: Whether any file acquired before the window names
        a collection.
    :return: Per change, oldest first: ``datetime_utc`` and ``sample_file_id``
        of the first file fitted with the version, ``kind``, the version's
        ``collection`` index, ``collection_id`` and ``isotopes_sha256``, and
        ``previous_isotopes_sha256``, the hash of the collection's latest
        earlier file for an ``edited`` change, else None.
    """
    first_file = {}
    for index, entry in enumerate(files):
        if entry["collection"] is not None:
            first_file.setdefault(entry["collection"], index)
    changes = []
    for position, version in enumerate(collections):
        key = (version["id"], version["isotopes_sha256"])
        if key in seen_before or position not in first_file:
            continue
        index = first_file[position]
        earlier = [
            collections[entry["collection"]]
            for entry in files[:index]
            if entry["collection"] is not None
        ]
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
                "datetime_utc": files[index]["datetime_utc"],
                "sample_file_id": files[index]["sample_file_id"],
                "kind": kind,
                "collection": position,
                "collection_id": version["id"],
                "isotopes_sha256": version["isotopes_sha256"],
                "previous_isotopes_sha256": previous,
            }
        )
    # Oldest first already: the versions come in the order of their first file.
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
    line the quartiles of its residual over the bin's files that list it,
    kept or left out, and how many list it and kept it. A line listed twice
    in one record counts twice; the node writes each once.

    :param raw: The statement's ``bins``, ``lines``, ``versions`` and
        ``runs`` as the database aggregates them, None for none.
    :return: ``bins``, oldest first: ``start_utc``, ``collection_id``,
        ``n_files``, ``n_failed``, ``n_without_lines`` (fits whose record
        lists no lines) and the quartiles of ``axis_offset_ppm``,
        ``post_fit_ppm``, ``n_points`` and ``n_candidates``; ``calibrants``,
        one per line by exact m/z, each with its ``ion``, ``target_mz`` and,
        aligned with ``bins``, the quartiles of its ``residual_ppm``,
        ``n_listed`` and ``n_kept``; ``collections`` as :func:`build_trend`
        serves them; and ``runs``, the file that begins each run of files
        fitted with one version, oldest first, each ``datetime_utc``,
        ``sample_file_id`` and the version's index in ``collections`` (the
        files :func:`collection_changes` needs to place the changes).
    """
    runs = raw.get("runs") or []
    versions = {
        (version["collection_id"], version["isotopes_sha256"]): version
        for version in raw.get("versions") or []
    }
    # In the order of their first file, which begins a run.
    order = list(
        dict.fromkeys((run["collection_id"], run["isotopes_sha256"]) for run in runs)
    )
    position = {version: i for i, version in enumerate(order)}
    collections = [
        {
            "id": version[0],
            "isotopes_sha256": version[1],
            "first_utc": _naive_utc(versions[version]["first_utc"]),
            "last_utc": _naive_utc(versions[version]["last_utc"]),
            "n_files": versions[version]["n_files"],
        }
        for version in order
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
                "n_listed": [0] * len(bins),
                "n_kept": [0] * len(bins),
            },
        )
        i = index[(_naive_utc(entry["bucket"]), entry["collection_id"])]
        line["residual_ppm"][i] = entry["residual_ppm"]
        line["n_listed"][i] = entry["n_listed"]
        line["n_kept"][i] = entry["n_kept"]
    return {
        "bins": bins,
        "calibrants": sorted(lines.values(), key=lambda line: line["target_mz"]),
        "collections": collections,
        "runs": [
            {
                "datetime_utc": _naive_utc(run["at_utc"]),
                "sample_file_id": run["sample_file_id"],
                "collection": position[(run["collection_id"], run["isotopes_sha256"])],
            }
            for run in runs
        ],
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
    """SQL for a record's collection id, NULL where it names none."""
    return SampleFile.mz_calibration["quality"]["collection"]["id"].as_string()


def _collection_sha_of():
    """SQL for a record's collection isotope hash, NULL where it names none."""
    return SampleFile.mz_calibration["quality"]["collection"][
        "isotopes_sha256"
    ].as_string()


#: A binned trend, summed up in the database (:func:`binned_trend`).
#:
#: ``latest`` admits the window's files as :func:`on_the_trend` does and keeps
#: the latest :data:`TREND_MAX_BINNED_FILES`, plus one that says the window
#: held more. ``f`` reads each kept record once, as JSONB, and takes from it
#: what :func:`file_summary` and :func:`record_collection` read; the ``OFFSET
#: 0`` keeps the planner from casting the record again for every field taken.
#: Per bin of one hour or day and one collection id, ``file_bins`` sums up the
#: files and ``line_bins`` each calibrant line; ``versions`` counts each
#: collection version's files, and ``runs`` keeps the first file of each run
#: of files fitted with one version, which is all that
#: :func:`collection_changes` needs of their order. Times leave as UTC without
#: an offset, as JSON writes them whatever the session's time zone.
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
             THEN rec #> '{quality,points}' END AS points
    FROM (
        SELECT sample_file_id, datetime_utc, mz_calibration::jsonb AS rec
        FROM sample_file
        WHERE sample_file_id IN (SELECT sample_file_id FROM kept)
        OFFSET 0
    ) records
),
per_file AS (
    SELECT f.bucket, f.collection_id, f.status, f.post_fit, f.n_points,
        lines.n_candidates, lines.axis_offset
    FROM f
    LEFT JOIN LATERAL (
        SELECT
            count(*) AS n_candidates,
            avg(
                1e6 * ((p ->> 'observed_mz')::float8 - (p ->> 'target_mz')::float8)
                / (p ->> 'target_mz')::float8
            ) FILTER (
                WHERE p -> 'retained' = 'true'::jsonb
                  AND jsonb_typeof(p -> 'observed_mz') = 'number'
                  AND jsonb_typeof(p -> 'target_mz') = 'number'
                  AND (p ->> 'target_mz')::float8 > 0
            ) AS axis_offset
        FROM jsonb_array_elements(f.points) AS p
        WHERE jsonb_typeof(p) = 'object'
        HAVING f.points IS NOT NULL
    ) lines ON true
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
        f.bucket,
        f.collection_id,
        CASE WHEN jsonb_typeof(p -> 'ion') = 'string' THEN p ->> 'ion' END AS ion,
        round((p ->> 'target_mz')::numeric, :decimals)::float8 AS target_mz,
        percentile_cont(ARRAY[0.25, 0.5, 0.75]) WITHIN GROUP (
            ORDER BY CASE WHEN jsonb_typeof(p -> 'residual_ppm') = 'number'
                          THEN (p ->> 'residual_ppm')::float8 END
        ) AS residual_ppm,
        count(*) AS n_listed,
        count(*) FILTER (WHERE p -> 'retained' = 'true'::jsonb) AS n_kept
    FROM f
    CROSS JOIN LATERAL jsonb_array_elements(f.points) AS p
    WHERE jsonb_typeof(p) = 'object' AND jsonb_typeof(p -> 'target_mz') = 'number'
    GROUP BY 1, 2, 3, 4
),
versions AS (
    SELECT collection_id, isotopes_sha256,
        min(at_utc) AS first_utc, max(at_utc) AS last_utc, count(*) AS n_files
    FROM f
    WHERE collection_id IS NOT NULL
    GROUP BY collection_id, isotopes_sha256
),
runs AS (
    SELECT at_utc, sample_file_id, collection_id, isotopes_sha256
    FROM (
        SELECT at_utc, datetime_utc, sample_file_id, collection_id, isotopes_sha256,
            lag(collection_id) OVER files_in_order AS previous_id,
            lag(isotopes_sha256) OVER files_in_order AS previous_sha
        FROM f
        WHERE collection_id IS NOT NULL
        WINDOW files_in_order AS (ORDER BY datetime_utc, sample_file_id)
    ) ordered
    WHERE previous_id IS NULL
       OR previous_id <> collection_id
       OR previous_sha <> isotopes_sha256
)
SELECT
    (SELECT count(*) FROM kept) AS n_files,
    (SELECT count(*) FROM latest) > :cap AS truncated,
    (SELECT min(datetime_utc) FROM f) AS earliest,
    (SELECT json_agg(b ORDER BY bucket, collection_id) FROM file_bins b) AS bins,
    (SELECT json_agg(l ORDER BY target_mz, ion, bucket, collection_id)
     FROM line_bins l) AS lines,
    (SELECT json_agg(v) FROM versions v) AS versions,
    (SELECT json_agg(r ORDER BY at_utc, sample_file_id) FROM runs r) AS runs
"""
).columns(
    n_files=Integer,
    truncated=Boolean,
    earliest=TIMESTAMP(timezone=True),
    bins=JSON,
    lines=JSON,
    versions=JSON,
    runs=JSON,
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

    Each look is one indexed walk back from the window's start, which stops at
    the first file that answers it: soon for a version or a collection in
    use, and only at the instrument's first file for one never used before.

    :return: The versions in ``collections`` an earlier file was fitted with;
        per collection id of theirs, the isotope hash of the latest earlier
        file fitted with that collection; and whether any earlier file names a
        collection.
    """
    filters = (
        *_instrument_files(instrument, polarity),
        SampleFile.datetime_utc < start,
    )
    seen, last = set(), {}
    for version in collections:
        key = (version["id"], version["isotopes_sha256"])
        found = await session.execute(
            select(SampleFile.sample_file_id)
            .where(
                *filters,
                _collection_id_of() == key[0],
                _collection_sha_of() == key[1],
            )
            .order_by(SampleFile.datetime_utc.desc())
            .limit(1)
        )
        if found.first() is not None:
            seen.add(key)
    for collection_id in {version["id"] for version in collections}:
        found = await session.execute(
            select(_collection_sha_of())
            .where(*filters, _collection_id_of() == collection_id)
            .order_by(SampleFile.datetime_utc.desc())
            .limit(1)
        )
        sha = found.scalar_one_or_none()
        if sha is not None:
            last[collection_id] = sha
    recorded = (
        await session.execute(
            select(SampleFile.sample_file_id)
            .where(*filters, _collection_id_of().is_not(None))
            .order_by(SampleFile.datetime_utc.desc())
            .limit(1)
        )
    ).first() is not None
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


async def _read_files(
    session, instrument: str, polarity: str, start: datetime, end: datetime, most: int
) -> tuple[dict, list[tuple], bool, datetime]:
    """
    The window's files on the trend, one by one: the latest ``most`` of them.

    :return: :func:`build_trend`'s trend of them; the rows it was built from,
        ``(sample_file_id, filename, datetime_utc, record)`` aligned with its
        ``files``, for the export to list each file's lines from the very
        records the trend read; whether the window held more; and where the
        files read begin, for their changes: the window's start, or the
        earliest file read when it held more, since the files left out were
        acquired before.
    """
    result = await session.execute(
        select(
            SampleFile.sample_file_id,
            SampleFile.filename,
            SampleFile.datetime_utc,
            SampleFile.mz_calibration,
        )
        .where(*_in_the_window(instrument, polarity, start, end))
        .order_by(SampleFile.datetime_utc.desc(), SampleFile.sample_file_id.desc())
        .limit(most + 1)
    )
    read = result.all()
    truncated = len(read) > most
    # The latest files of a window that holds too many; then oldest first.
    read = read[:most]
    rows = [tuple(row) for row in reversed(read)]
    since = read[-1].datetime_utc if truncated else start
    return build_trend(rows), rows, truncated, since


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
    files: list[dict],
    collections: list[dict],
) -> list[dict]:
    """The :func:`collection_changes` of files read from ``since`` on."""
    if not collections:
        return []
    seen, last, recorded = await _before_the_window(
        session, instrument, polarity, since, collections
    )
    return collection_changes(files, collections, seen, last, recorded)


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
            # The first file of each run of one version, for the changes.
            ordered = trend.pop("runs")
            trend["files"] = []
        else:
            trend, _, truncated, since = await _read_files(
                session, instrument, polarity, start, end, TREND_MAX_FILES
            )
            n_files = len(trend["files"])
            ordered = trend["files"]
            trend["bins"] = []
        changes = await _changes(
            session, instrument, polarity, since, ordered, trend["collections"]
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


#: What a spreadsheet takes a text cell beginning with for a formula.
FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")


def _cell(value) -> str | float | int | bool:
    """
    A value as the export writes it: empty for None, and a text that a
    spreadsheet would run as a formula - a file or collection name is typed
    by someone - behind an apostrophe, which shows it as the text it is.
    """
    if value is None:
        return ""
    if isinstance(value, str) and value.startswith(FORMULA_STARTS):
        return "'" + value
    return value


def trend_rows(trend: dict, rows: list[tuple]) -> list[list]:
    """
    The export's rows (:data:`EXPORT_COLUMNS`) for a trend and its records.

    :param trend: A trend of files one by one (:func:`build_trend`), its
        collections named.
    :param rows: The ``(sample_file_id, filename, datetime_utc, record)`` it
        was built from, aligned with its ``files``.
    :return: One row per line of each file's record, in the record's order,
        and one for a file whose record lists none.
    """
    out = []
    collections = trend["collections"]
    for entry, (_, _, _, record) in zip(trend["files"], rows):
        version = (
            collections[entry["collection"]] if entry["collection"] is not None else {}
        )
        head = [
            entry["datetime_utc"],
            entry["sample_file_id"],
            entry["filename"],
            entry["status"],
            version.get("id"),
            version.get("name"),
            version.get("isotopes_sha256"),
            entry["model_mode"],
            entry["node_version"],
            entry["n_points"],
            entry["n_candidates"],
            entry["post_fit_ppm"],
            entry["axis_offset_ppm"],
            entry["pre_fit_abs_ppm"],
        ]
        points = record_points(record) or [None]
        for point in points:
            tail = (
                [
                    point.get("ion") if isinstance(point.get("ion"), str) else None,
                    _number(point.get("target_mz")),
                    _number(point.get("observed_mz")),
                    _offset_ppm(point),
                    _number(point.get("residual_ppm")),
                    _number(point.get("snr")),
                    point.get("retained") is True,
                ]
                if point is not None
                else [None] * 7
            )
            out.append([_cell(value) for value in head + tail])
    return out


@api_controller()
async def export_calibration_trend(
    instrument: str,
    polarity: str,
    scope: WorkspaceScope,
    datetime_min: datetime | None = None,
    datetime_max: datetime | None = None,
) -> str:
    """
    The calibration trend as a CSV table, one row per calibrant line of each
    file (:data:`EXPORT_COLUMNS`), oldest file first.

    The window, the files and the collection names are those of
    :func:`get_calibration_trend` for the same arguments, the files always
    one by one, also where the trend serves them in bins: the latest
    :data:`EXPORT_MAX_FILES` of a window that holds more. A text cell that a
    spreadsheet would read as a formula is written with a leading apostrophe
    (:func:`_cell`).

    :return: The CSV text, with a header row.
    """
    start, end = _window(datetime_min, datetime_max)
    async with async_session() as session:
        trend, rows, _, _ = await _read_files(
            session, instrument, polarity, start, end, EXPORT_MAX_FILES
        )
        await _name_collections(session, trend["collections"], scope)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(EXPORT_COLUMNS)
    writer.writerows(trend_rows(trend, rows))
    return buffer.getvalue()
