"""The axis fingerprint of an Orbitrap raw file.

On some Orbitrap models the data system divides the frequency axis into
frequency bands about 16.28 kHz wide, and each mass calibration stores one
m/z offset per band on top of a smooth trend. The lock mass rescales a scan as
a whole, so it cannot remove the steps between bands, and the instrument
flags none of them. The fingerprint reads them back from the stored profile,
without any chemistry: inside one frequency band the profile points of a
scan, converted to frequency, are spaced off the FFT bin by one relative
amount ``s``, which gives back the offset the calibration stored
(``r ~ -(2/3) s``). Mascope measures and serves the fingerprint; it never
alters an m/z with it.

The document (schema ``mascope.axis_fingerprint``, version 1, in
``axis_fingerprint.schema.json`` beside this module) holds, per acquisition
mode - the polarity, FT resolution and scan range of a file's FTMS MS1 full
scans - the table of frequency bands, the grid of band edges it was read on
and the lock mass each scan was corrected with, and for the whole file the
census of every scan. Inside the document the algorithm's own names stay: a
frequency band is a ``segment``, its index ``k`` and its deviation
``s_ppm``.

The algorithm is :mod:`.reference`, the normative implementation that came
with the request for the fingerprint, vendored verbatim: only ``ruff
format`` touched it. Its tie-breaks and its seeded random test are pinned by
the reference documents, so it is not edited here; where its comments cite
``SPEC`` or ``checks/``, they mean the request's specification and the
measurements behind it, which are not in this repository. This module reads
its input through a reader backend and assembles the document as the
reference documents were built.
"""

from __future__ import annotations

import functools
import hashlib
import json
import logging
import os
import time
from collections.abc import Callable
from importlib.resources import files

import numpy as np

from mascope_thermo.axis_fingerprint import reference
from mascope_thermo.backend import CENTROID_FLAGS, ReaderBackend
from mascope_thermo.scan_filter import parse_scan_filter
from mascope_thermo.streams import FT_RESOLUTION, MICROSCANS, scan_stream_numbers
from mascope_thermo.thermo import NoScansFoundError


_log = logging.getLogger(__name__)

#: The reader backend, as ``MASCOPE_THERMO_BACKEND`` names it, whose profile
#: the fingerprint is defined on: the points the file stores.
OPENTFRAW = "opentfraw"

#: The algorithm's parameters on OpenTFRaw's profile. Three chunk-size
#: thresholds are lower than in ``reference.PARAMS``, which counts the
#: zero-intensity points the Thermo library adds around each run of stored
#: points.
PARAMS = reference.PARAMS_OPENTFRAW

#: What the document names as its extractor.
EXTRACTOR_NAME = "mascope_thermo.axis_fingerprint"

#: The JSON schema every document validates against, beside this module.
SCHEMA_FILE = "axis_fingerprint.schema.json"

# The trailer labels the census reads, besides the FT resolution and the
# microscan count the stream census names.
CONVERSION_B = "Conversion Parameter B:"
CONVERSION_C = "Conversion Parameter C:"
LOCKS_FOUND = "Number of LM Found:"
LOCK_CORRECTION = "LM m/z-Correction (ppm):"
LOCK_MASSES = tuple(f"Lock Mass #{i} (m/z):" for i in (1, 2, 3))
_TRAILER_LABELS = (
    FT_RESOLUTION,
    MICROSCANS,
    CONVERSION_B,
    CONVERSION_C,
    LOCKS_FOUND,
    LOCK_CORRECTION,
    *LOCK_MASSES,
)

_REFERENCE_FLAG = CENTROID_FLAGS["reference"]

_SCAN_TABLE_COLUMNS = [
    "scan",
    "rt_min",
    "mode",
    "microscans",
    "B",
    "C",
    "n_lm_found",
    "lm_correction_ppm",
    "lock_used",
]


def compute_axis_fingerprint(backend: ReaderBackend, *, reader_name: str) -> dict:
    """The axis fingerprint of the raw file an open backend reads.

    Computed on OpenTFRaw's profile, the stored points: from 1.4.1 on, the
    reader converts them with the per-chunk correction the file stores, as
    the Thermo library does. Older releases return an m/z on which every
    frequency band reads 0, a document that would look valid and say
    nothing, so on those, and on any other reader backend, the document is
    ``unavailable`` and nothing is read.

    Reads the trailer and filter of every scan, the centroids of every scan
    that names a lock mass (for the lock it was corrected with: the
    centroid flagged Reference), and the profile of at most eight scans per
    mode.

    Never raises for the data: any failure gives the ``error`` document,
    ``"<type>: <message>"``, for the caller to log. A file that holds no
    scans is the exception. It raises
    :class:`~mascope_thermo.thermo.NoScansFoundError`, as the other census
    properties of a file do, so that the conversion refuses it as an empty
    acquisition rather than store a fingerprint for it.

    :param backend: An open reader backend.
    :param reader_name: The reader backend, as ``MASCOPE_THERMO_BACKEND``
        names it: ``"opentfraw"`` or ``"thermo"``.
    :return: The document, valid against the schema whatever its status.
    :raises NoScansFoundError: When the file holds no scans.
    """
    try:
        if reader_name != OPENTFRAW:
            return reference.minimal_document("unavailable")
        import opentfraw

        version = opentfraw.__version__
        if not reference.opentfraw_version_ok(version):
            return reference.minimal_document("unavailable")
        document = _fingerprint(backend, f"{OPENTFRAW} {version}", PARAMS)
        validate_document(document)
        return document
    except NoScansFoundError:
        raise
    except Exception as e:  # noqa: BLE001 - a fingerprint never costs a file
        return reference.minimal_document("error", f"{type(e).__name__}: {e}")


def validate_document(document: dict) -> None:
    """Check a document against the schema (``axis_fingerprint.schema.json``).

    :param document: A fingerprint document, of any status.
    :raises jsonschema.ValidationError: When it breaks the schema.
    """
    _validator().validate(document)


@functools.cache
def _validator():
    """The schema's validator, built once per process."""
    import jsonschema

    schema = json.loads(
        files(__package__).joinpath(SCHEMA_FILE).read_text(encoding="utf-8")
    )
    return jsonschema.Draft202012Validator(schema)


def _fingerprint(backend: ReaderBackend, reader: str, params: dict) -> dict:
    """The full document: census, lock used per scan, modes, source."""
    cost = {}
    started = time.perf_counter()
    census = _census(backend)
    if not census:
        raise NoScansFoundError("No scans found: the file holds no scans.")
    _cross_check_modes(backend, census)
    cost["census"] = time.perf_counter() - started

    started = time.perf_counter()
    for scan in census:
        scan["lock_used"] = _lock_used(backend, scan)
    cost["locks"] = time.perf_counter() - started

    read = [0.0]

    def profile_of(scan_number: int) -> tuple[np.ndarray, np.ndarray]:
        begun = time.perf_counter()
        profile = backend.scan_profile(scan_number)
        read[0] += time.perf_counter() - begun
        return profile

    started = time.perf_counter()
    body = _assemble(census, census[0]["scan"], profile_of, params)
    cost["profile_read"] = read[0]
    cost["compute"] = time.perf_counter() - started - read[0]

    return {
        "schema": reference.SCHEMA,
        "version": reference.VERSION,
        "status": body["status"],
        "extractor": {
            "name": EXTRACTOR_NAME,
            "reader": reader,
            "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "params": dict(params),
        },
        "source": _source(backend, census),
        "scan_table": body["scan_table"],
        "modes": body["modes"],
        "dominant_mode": body["dominant_mode"],
        "flags": body["flags"],
        "cost_seconds": {name: round(value, 3) for name, value in cost.items()},
    }


def _census(backend: ReaderBackend) -> list[dict]:
    """Every scan's filter, start time, trailer numbers and stream, in scan
    order.

    A scan belongs to a mode when its filter is an FTMS MS1 full scan
    (``reference.parse_filter``) and its trailer stores the FT resolution and
    both conversion parameters. The keys the reference functions read
    (``scan``, ``B``, ``C``, ``microscans``, ``n_lm_found``,
    ``lm_correction_ppm``, ``centroid``) keep their names.
    """
    census = []
    for row, stream_key, numbers in scan_stream_numbers(backend, _TRAILER_LABELS):
        polarity, low, high = reference.parse_filter(row["filter"])
        resolution = numbers.get(FT_RESOLUTION)
        microscans = numbers.get(MICROSCANS)
        locks_found = numbers.get(LOCKS_FOUND)
        scan = {
            "scan": int(row["scan"]),
            "rt_min": round(float(row["time_s"]) / 60, 5),
            "filter": row["filter"],
            "stream_key": stream_key,
            "polarity": polarity,
            "low": low,
            "high": high,
            "resolution": None if resolution is None else int(resolution),
            "microscans": None if microscans is None else int(microscans),
            "B": numbers.get(CONVERSION_B),
            "C": numbers.get(CONVERSION_C),
            "n_lm_found": None if locks_found is None else int(locks_found),
            "lm_correction_ppm": numbers.get(LOCK_CORRECTION),
            "configured_locks": [
                mz
                for mz in (numbers.get(label) for label in LOCK_MASSES)
                if mz and mz > 0
            ],
            # The filter's scan data type, as the reference documents read it
            "centroid": reference.filter_info(row["filter"])["data_type"] == "c",
        }
        scan["in_mode"] = (
            polarity is not None
            and scan["resolution"] is not None
            and scan["B"] is not None
            and scan["C"] is not None
        )
        census.append(scan)
    return census


def _cross_check_modes(backend: ReaderBackend, census: list[dict]) -> None:
    """Log the scans whose filter Mascope's scan-filter parser reads
    otherwise than the algorithm's.

    The modes follow ``reference.parse_filter``, which the reference
    documents pin. Each mode names the streams its scans belong to, and
    those come from :mod:`mascope_thermo.scan_filter`, so where the two
    parsers disagree on whether a scan is an FTMS MS1 full scan, or on its
    polarity or scan range, a mode and its streams no longer describe the
    same scans. The document is left as the algorithm makes it; the
    disagreement is a defect of a parser, and is logged as one.
    """
    differ = [
        scan["filter"]
        for scan in census
        if _ms1_full_scan(scan["filter"])
        != (
            None
            if scan["polarity"] is None
            else (scan["polarity"], scan["low"], scan["high"])
        )
    ]
    if differ:
        _log.warning(
            "The axis fingerprint of %s reads %d of %d scan filters otherwise "
            "than the scan-filter parser, the first %r",
            backend.datafile_path,
            len(differ),
            len(census),
            differ[0],
        )


def _ms1_full_scan(text: str) -> tuple[str, float, float] | None:
    """``(polarity, low, high)`` of an FTMS MS1 full scan as
    :func:`~mascope_thermo.scan_filter.parse_scan_filter` reads its filter,
    the scan range from the lowest start to the highest end; ``None`` for
    any other scan."""
    parsed = parse_scan_filter(text)
    if not (
        parsed.analyzer == "FTMS"
        and parsed.ms_order == 1
        and parsed.scan_mode == "Full"
        and parsed.polarity is not None
        and parsed.scan_ranges
    ):
        return None
    return (
        parsed.polarity,
        min(low for low, _ in parsed.scan_ranges),
        max(high for _, high in parsed.scan_ranges),
    )


def _lock_used(backend: ReaderBackend, scan: dict) -> str:
    """The lock mass a scan was corrected with: the configured lock that a
    centroid flagged Reference sits at (``reference.lock_used_from_flags``).

    A scan that names no lock mass used none, and its centroids are not
    read.
    """
    if not scan["configured_locks"]:
        return "none"
    flags = backend.centroid_flags(scan["scan"])
    masses = backend.centroid_masses(scan["scan"])
    if flags.shape != masses.shape:
        raise ValueError(
            f"Scan {scan['scan']} has {masses.size} centroids and "
            f"{flags.size} centroid flags"
        )
    flagged = masses[(flags & _REFERENCE_FLAG) != 0]
    return reference.lock_used_from_flags(
        scan["configured_locks"], flagged, ["Reference"] * flagged.size
    )


def _assemble(
    census: list[dict],
    first_scan: int,
    profile_of: Callable[[int], tuple[np.ndarray, np.ndarray]],
    params: dict,
) -> dict:
    """Modes, scan picks, each mode's fingerprint, the scan table and the
    file flags: ``assemble()`` of the script that built the reference
    documents, with each mode's streams added.

    :param census: :func:`_census`, with each scan's ``lock_used``.
    :param first_scan: The file's first scan, which no pick takes but where
        it is a mode's only profile scan.
    :param profile_of: Reads one scan's profile, ``(mz, intensity)``.
    :param params: The algorithm's parameters.
    """
    modes: dict[tuple, list[dict]] = {}
    for scan in census:
        if scan["in_mode"]:
            key = (scan["polarity"], scan["resolution"], scan["low"], scan["high"])
            modes.setdefault(key, []).append(scan)
    in_modes = sum(len(scans) for scans in modes.values())
    # Stable, so equal counts keep the order the modes are first seen in.
    order = sorted(modes, key=lambda key: -len(modes[key]))
    out = []
    for key in order:
        scans = modes[key]
        picks = reference.pick_scans(scans, first_scan, params["n_scans"])
        profiles = {int(number): profile_of(int(number)) for number in picks}
        locks = sorted({mz for scan in scans for mz in scan["configured_locks"]})
        mode = reference.fingerprint_mode(
            key, scans, profiles, locks, "reference_flag", params
        )
        mode["scan_share"] = round(len(scans) / in_modes, 4)
        # Mascope's streams of the mode's scans, so that a peak list, which
        # is built per stream or per polarity, can be joined to its mode.
        mode["stream_keys"] = list(dict.fromkeys(scan["stream_key"] for scan in scans))
        out.append(mode)

    flags = []
    if len({polarity for polarity, *_ in modes}) > 1:
        flags.append("mixed_polarity")
    if out and out[0]["scan_share"] < 0.9:
        flags.append("mixed_resolution")
    if in_modes < len(census):
        flags.append("scans_outside_modes")

    mode_ids = {key: reference.mode_id(*key) for key in modes}
    rows = [
        [
            scan["scan"],
            scan["rt_min"],
            mode_ids[(scan["polarity"], scan["resolution"], scan["low"], scan["high"])]
            if scan["in_mode"]
            else None,
            scan["microscans"],
            scan["B"],
            scan["C"],
            scan["n_lm_found"],
            scan["lm_correction_ppm"],
            scan["lock_used"],
        ]
        for scan in census
    ]
    return {
        "status": "ok"
        if any(mode.get("segments") for mode in out)
        else "no_fingerprint",
        "scan_table": {"columns": list(_SCAN_TABLE_COLUMNS), "rows": rows},
        "modes": out,
        "dominant_mode": out[0]["mode_id"] if out else None,
        "flags": sorted(flags),
    }


def _source(backend: ReaderBackend, census: list[dict]) -> dict:
    """The raw file the document was computed from: its name, size, a digest
    of its first MiB, its scans and the run's start and end times."""
    path = backend.datafile_path
    with open(path, "rb") as fh:
        digest = hashlib.sha1(fh.read(1 << 20), usedforsecurity=False).hexdigest()
    start_s, end_s = backend.run_time_range()
    source = {
        "raw_name": os.path.basename(path),
        "raw_size_bytes": os.path.getsize(path),
        "raw_sha1_first_mib": digest,
        "first_scan": census[0]["scan"],
        "last_scan": census[-1]["scan"],
        "n_scans": len(census),
        "start_time_min": round(start_s / 60, 5),
        "end_time_min": round(end_s / 60, 5),
    }
    model = backend.instrument_details().get("Model")
    if model:
        source["instrument_model"] = str(model)
    return source
