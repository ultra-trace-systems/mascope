"""Which mass calibration a file was acquired on, read without the fingerprint.

An Orbitrap scan converts frequency to m/z with the two conversion parameters
of its trailer, ``m/z = B/f^2 + C/f^4``. Their ratio C/B^2 is fixed by the
mass calibration rather than by the scan: within a file it is constant (a
relative spread of at most 1.5e-10 over the scans of one mode, measured on
the reference instrument), within one calibration it spreads by at most
2e-13, and two calibrations differ by at least 1.3e-10. So files whose
streams share C/B^2 to within about 1e-11 share a calibration. Compare the
numbers with that tolerance, never as printed digits: rounded to four
decimals, the files of one calibration can straddle a rounding boundary.
:func:`stream_calibrations` reports the ratio per stream; grouping files by
it is the client's.

The instrument's status log says when the calibration was made, once it is
old enough to be overdue (25 h): its ``Performance:`` text then names
``Mass Calibration neg ... Overdue (YYYY-MM-DD hh:mm)``, per polarity.
:func:`calibration_status` reads those entries.

The axis fingerprint's ``calibration`` block carries both per mode; this is
for a client that has no fingerprint of the file.
"""

from __future__ import annotations

import math
import re
import statistics

from mascope_thermo.backend import ReaderBackend
from mascope_thermo.streams import scan_stream_keys


CONVERSION_B = "Conversion Parameter B:"
CONVERSION_C = "Conversion Parameter C:"

# One overdue calibration in the status log's text: the polarity, then,
# before the next calibration is named, the time in brackets. The polarity
# may stand in brackets itself, and be spelled out.
_OVERDUE = re.compile(
    r"Mass\s+Calibration\s*\(?\s*(neg|pos)\w*\s*\)?"
    r"(?:(?!Mass\s+Calibration).)*?"
    r"Overdue\s*\(\s*(\d{4}-\d{2}-\d{2}[ T]\d{1,2}:\d{2}(?::\d{2})?)\s*\)",
    re.IGNORECASE | re.DOTALL,
)


def _number(value) -> float | None:
    """A trailer value as a finite float, or None.

    The Thermo backend gives trailer values as text, OpenTFRaw as numbers.
    """
    if value is None:
        return None
    try:
        number = float(str(value).strip())
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def stream_calibrations(backend: ReaderBackend) -> dict[str, dict]:
    """The conversion parameters of every stream of the file.

    Per stream key (:func:`mascope_thermo.streams.scan_stream_keys`), over
    every scan of the stream whose trailer carries both parameters, with B
    not zero:

    - ``scans``: how many scans that is;
    - ``b``, ``c``: the median of B and of C;
    - ``c_over_b2``, ``c_over_b2_min``, ``c_over_b2_max``: the median,
      smallest and largest C/B^2 of a scan.

    A stream with no such scan is left out. The values are as precise as the
    reader gives them: OpenTFRaw's at full precision, the Thermo library's
    rounded to the digits it displays, which can be too few to tell two
    calibrations apart.

    :param backend: An open reader backend.
    :return: ``{stream key: {...}}``, in the order the streams first appear.
    """
    per_stream: dict[str, tuple[list[float], list[float], list[float]]] = {}
    keys = scan_stream_keys(backend)
    for row, key in zip(backend.scan_filters(), keys):
        trailer = backend.scan_trailer(row["scan"])
        b = _number(trailer.get(CONVERSION_B))
        c = _number(trailer.get(CONVERSION_C))
        if b is None or c is None or b == 0:
            continue
        bs, cs, ratios = per_stream.setdefault(key, ([], [], []))
        bs.append(b)
        cs.append(c)
        ratios.append(c / (b * b))
    return {
        key: {
            "scans": len(ratios),
            "b": statistics.median(bs),
            "c": statistics.median(cs),
            "c_over_b2": statistics.median(ratios),
            "c_over_b2_min": min(ratios),
            "c_over_b2_max": max(ratios),
        }
        for key, (bs, cs, ratios) in per_stream.items()
    }


def overdue_calibrations(status_log: dict) -> list[dict]:
    """The overdue mass calibrations a status-log record names.

    Every text value of the record is read, the ``Performance:`` entry being
    where instruments write them.

    :param status_log: One status-log record, ``{label: value}``.
    :return: ``[{"polarity": "neg" | "pos", "calibrated": "YYYY-MM-DD hh:mm"}]``
        in the order the record names them, the time as recorded (the
        instrument's local time); empty when none is overdue.
    """
    entries = []
    for value in status_log.values():
        if not isinstance(value, str):
            continue
        for match in _OVERDUE.finditer(value):
            entries.append(
                {"polarity": match.group(1).lower(), "calibrated": match.group(2)}
            )
    return entries


def calibration_status(backend: ReaderBackend) -> dict | None:
    """The overdue mass calibrations of the file's first and last scan.

    ``{"first_scan": {...}, "last_scan": {...}}``, each ``{"scan": N,
    "overdue": [...]}`` as :func:`overdue_calibrations` reads the scan's
    status-log record, or None for a scan the reader gives no record for.
    An empty ``overdue`` says the record names none: every calibration was
    younger than the instrument's 25 h.

    :param backend: An open reader backend.
    :return: The status, or None when the reader gives no record for either
        scan.
    """
    rows = backend.scan_filters()
    if not rows:
        return None
    status = {}
    for name, row in (("first_scan", rows[0]), ("last_scan", rows[-1])):
        record = backend.status_log(row["scan"])
        status[name] = (
            None
            if record is None
            else {"scan": row["scan"], "overdue": overdue_calibrations(record)}
        )
    if all(value is None for value in status.values()):
        return None
    return status
