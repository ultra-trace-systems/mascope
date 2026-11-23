"""
The acquisition axis of a TOF file, kept on every record a fit writes.

A TOF fit's record lists, per calibrant line, the m/z its peak was observed
at (``quality.points[].observed_mz``), read on the axis the file carries when
the fit runs. On a file's first fit that is the acquisition software's axis,
so the line's offset says how far the instrument read it off. On a refit it is
the axis the previous fit set, and the offset reads near zero. The
acquisition's own coefficients, the converter's record (``mode`` and ``par``
of a record ``is_unfitted_record`` admits), are replaced by the first fit.

So every record a fit writes keeps them (MAS-937):

- ``acquisition_axis``: ``{mode, par, raw_index_offset}``, the Tofwerk mode
  and parameters of the acquisition axis, and how many samples of the raw
  ``.h5`` axis the stored axis starts after (the converter cuts the samples
  below ``DEFAULT_NONSENSE_MZ``; the coefficients count from the raw axis's
  first sample, a fit from the stored axis's). It comes from the record being
  replaced when that record has it, from the converter's record on a first
  fit, and otherwise from the raw file, read again (:func:`read_raw_axis`).
- ``acquisition_offsets``: where none of those gives the axis - a file with
  no ``.h5`` whose first fit happened before the key existed - the per-line
  offsets that were read on the acquisition axis: the record being replaced
  keeps its own lines' offsets (its ``observed_mz`` against the exact m/z,
  which is the acquisition axis's reading on a first fit), or carries the
  ones it was given. A record never ends with less raw-axis information than
  the one it replaces.
- ``fitted_at``: when the record was written, UTC.

A line's raw-axis offset (:func:`line_offsets`) is then the acquisition
axis's m/z at the fit's TOF index of the line's exact m/z, against that exact
m/z. It reads the same on a file's first fit and on every refit, since the
fit's index of a line is where its peak is. A record with neither key - one
written before them - reads its lines' observed offsets as it always did.

The calibration trend reads the same values, in SQL for a binned trend
(``calibration_trend``): the modes read here are the ones with a closed form,
which SQL can spell. A fit's TOF index of an m/z is read in modes 0, 1 and 2
(the node fits 0 and 2, and a transfer gives one of those); the acquisition
axis's m/z of an index in modes 0, 1, 2 and 5. Another mode reads no offset.
"""

import copy
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np

import mascope_file.name as m_name
import mascope_signal.compute as m_compute
from mascope_backend.runtime import runtime
from mascope_tofwerk.tofwerk import open_h5_file, read_mz_calibration


#: The key of a record's acquisition axis.
ACQUISITION_AXIS = "acquisition_axis"

#: The key of the per-line offsets kept where the axis cannot be had.
ACQUISITION_OFFSETS = "acquisition_offsets"

#: The key of the time a record was written.
FITTED_AT = "fitted_at"

#: Decimals a calibrant line's exact m/z is told apart by: a line's exact m/z
#: is the same number in every record that lists it.
TARGET_MZ_DECIMALS = 4

#: Modes whose TOF index of an m/z :func:`index_at` reads.
INDEX_MODES = (0, 1, 2)

#: Modes whose m/z of a TOF index :func:`mz_at` reads.
MZ_MODES = (0, 1, 2, 5)

#: How many parameters each mode read here needs.
_N_PARAMETERS = {0: 2, 1: 2, 2: 3, 5: 3}

#: How close, in ppm, the acquisition axis must give the stored axis's first
#: m/z at the index it is inferred at (:func:`infer_index_offset`). The stored
#: axis is the converter's axis written as float32 or recomputed from the
#: coefficients, a fraction of a ppm apart; one sample is about 20 ppm at
#: 100 Th.
INDEX_MATCH_PPM = 1.0


def _real(value) -> float | None:
    """A finite float, or None for anything else (booleans included)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _mode(value) -> int | None:
    """A mode number: an integral real, as JSON may write it either way."""
    value = _real(value)
    return int(value) if value is not None and value.is_integer() else None


def _parameters(mode, par, modes) -> tuple[int, list[float]] | None:
    """A mode of ``modes`` and its parameters, all real; None otherwise."""
    mode = _mode(mode)
    if mode not in modes or not isinstance(par, list):
        return None
    needed = _N_PARAMETERS[mode]
    values = [_real(value) for value in par[:needed]]
    if len(values) < needed or any(value is None for value in values):
        return None
    return mode, values


def _finite(value: float) -> float | None:
    return value if math.isfinite(value) else None


def index_at(mz: float, mode, par) -> float | None:
    """The TOF index a calibration of mode 0, 1 or 2 gives an m/z.

    ``i = p1 * sqrt(m) + p2``, ``i = p1 / sqrt(m) + p2`` and
    ``i = p1 * m**p3 + p2``. None for another mode, a parameter that is not
    a real number, or an m/z that is not positive.
    """
    parameters = _parameters(mode, par, INDEX_MODES)
    mz = _real(mz)
    if parameters is None or mz is None or mz <= 0:
        return None
    mode, p = parameters
    try:
        match mode:
            case 0:
                return _finite(p[0] * math.sqrt(mz) + p[1])
            case 1:
                return _finite(p[0] / math.sqrt(mz) + p[1])
            case _:
                return _finite(p[0] * mz ** p[2] + p[1])
    except OverflowError:
        return None


def mz_at(index: float, mode, par) -> float | None:
    """The m/z a calibration of mode 0, 1, 2 or 5 gives a TOF index.

    As ``mascope_tofwerk.calibration.tof_to_mass`` reads them, where they are
    defined: None for another mode, a parameter that is not a real number, a
    zero divisor, or a fractional power of a negative number.
    """
    parameters = _parameters(mode, par, MZ_MODES)
    index = _real(index)
    if parameters is None or index is None:
        return None
    mode, p = parameters
    try:
        match mode:
            case 0:
                return _finite(((index - p[1]) / p[0]) ** 2) if p[0] != 0 else None
            case 1:
                return _finite((p[0] / (index - p[1])) ** 2) if index != p[1] else None
            case 2:
                if p[0] == 0 or p[2] == 0:
                    return None
                base = (index - p[1]) / p[0]
                return _finite(base ** (1 / p[2])) if base > 0 else None
            case _:
                return _finite(p[0] * index * index + p[1] * index + p[2])
    except OverflowError:
        return None


def _points(record: dict) -> list[dict] | None:
    """The calibrant lines a record lists, each a dict; None for none listed."""
    quality = record.get("quality")
    points = quality.get("points") if isinstance(quality, dict) else None
    if not isinstance(points, list):
        return None
    return [point for point in points if isinstance(point, dict)]


def _observed_offset(point: dict) -> float | None:
    """How far a line read off on the axis it was observed on, ppm."""
    target, observed = _real(point.get("target_mz")), _real(point.get("observed_mz"))
    if target is None or observed is None or target <= 0:
        return None
    return 1e6 * (observed - target) / target


def _line_key(target) -> float | None:
    target = _real(target)
    return round(target, TARGET_MZ_DECIMALS) if target is not None else None


def _kept_offsets(entries: list) -> dict[float, float | None]:
    """Kept offsets by their line's exact m/z; the first listed of a line wins."""
    kept: dict[float, float | None] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        key = _line_key(entry.get("target_mz"))
        if key is not None and key not in kept:
            kept[key] = _real(entry.get("offset_ppm"))
    return kept


def knows_raw_axis(record: dict | None) -> bool:
    """Whether a record carries either key that says how the acquisition
    axis read its lines, rather than only the axis its fit was read on."""
    return isinstance(record, dict) and (
        isinstance(record.get(ACQUISITION_AXIS), dict)
        or isinstance(record.get(ACQUISITION_OFFSETS), list)
    )


def line_offsets(record: dict) -> list[float | None] | None:
    """Each listed line's offset on the acquisition axis, in ppm.

    Aligned with the record's points (the dicts of ``quality.points``), None
    for none listed. With an ``acquisition_axis``, the axis's m/z at the
    fit's (``mode``, ``par``) TOF index of the line's exact m/z, against that
    exact m/z; with ``acquisition_offsets`` instead, the offset kept for the
    line's exact m/z, None for a line not kept; with neither, the line's
    observed m/z against its exact m/z, which is the acquisition axis's
    reading only on a first fit.
    """
    points = _points(record)
    if points is None:
        return None
    axis = record.get(ACQUISITION_AXIS)
    if isinstance(axis, dict):
        shift = _real(axis.get("raw_index_offset"))
        offsets = []
        for point in points:
            target = _real(point.get("target_mz"))
            index = index_at(target, record.get("mode"), record.get("par"))
            raw = (
                mz_at(index + shift, axis.get("mode"), axis.get("par"))
                if index is not None and shift is not None
                else None
            )
            offsets.append(
                _finite(1e6 * (raw - target) / target) if raw is not None else None
            )
        return offsets
    entries = record.get(ACQUISITION_OFFSETS)
    if isinstance(entries, list):
        kept = _kept_offsets(entries)
        return [kept.get(_line_key(point.get("target_mz"))) for point in points]
    return [_observed_offset(point) for point in points]


def axis_offset_ppm(record: dict) -> float | None:
    """The signed mean of the kept lines' acquisition-axis offsets, ppm.

    None where the record lists no kept line with an offset.
    """
    points = _points(record) or []
    offsets = line_offsets(record) or []
    kept = [
        offset
        for point, offset in zip(points, offsets)
        if point.get("retained") is True and offset is not None
    ]
    return sum(kept) / len(kept) if kept else None


#: The shape of a ``fitted_at`` the trend reads: ISO 8601 with its offset, as
#: :func:`stamp` writes it. The binned trend's SQL reads the same.
FITTED_AT_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?[+-]\d{2}:\d{2}$"
)


def fitted_at(record: dict) -> datetime | None:
    """When a record was written, UTC; None where it does not say, or not as
    :data:`FITTED_AT_PATTERN` writes it."""
    value = record.get(FITTED_AT)
    if not isinstance(value, str) or not FITTED_AT_PATTERN.match(value):
        return None
    try:
        return datetime.fromisoformat(value).astimezone(timezone.utc)
    except ValueError:
        return None


def observed_offsets(record: dict | None) -> list[dict]:
    """A record's lines' observed offsets, as ``acquisition_offsets`` keeps
    them: ``ion``, ``target_mz`` and ``offset_ppm`` per line with one."""
    out = []
    for point in _points(record or {}) or []:
        offset = _observed_offset(point)
        if offset is None:
            continue
        ion = point.get("ion")
        out.append(
            {
                "ion": ion if isinstance(ion, str) else None,
                "target_mz": _real(point.get("target_mz")),
                "offset_ppm": offset,
            }
        )
    return out


def infer_index_offset(first_mz: float, mode, par) -> int | None:
    """The raw index the stored axis starts at, from its first m/z.

    The stored axis of a file still on its acquisition axis is the
    converter's coefficients counted from the sample the cut left first.
    That sample is the coefficients' index of the stored axis's first m/z,
    rounded; it must give back that m/z within :data:`INDEX_MATCH_PPM`.
    None where the mode has no closed inverse or nothing matches.
    """
    first_mz = _real(first_mz)
    index = index_at(first_mz, mode, par)
    if index is None or first_mz <= 0:
        return None
    candidate = round(index)
    back = mz_at(candidate, mode, par)
    if back is None or abs(back - first_mz) / first_mz * 1e6 > INDEX_MATCH_PPM:
        return None
    return int(candidate)


def read_raw_axis(filename: str) -> dict | None:
    """The acquisition axis of a TOF file, read again from its raw ``.h5``.

    The converter's coefficients (``mascope_tofwerk.tofwerk.
    read_mz_calibration``) and the samples the stored axis starts after: the
    raw axis's length less the stored sum signal's, as
    ``tof_calibration_transfer.raw_index_offset`` counts them. Synchronous:
    it reads the filestore.

    :return: ``{mode, par, raw_index_offset}``; None for a file without its
        ``.h5``, a mode :func:`mz_at` does not read, or a stored axis longer
        than the raw one.
    """
    if m_name.get_sample_file_type(filename) != "tof_h5":
        return None
    with open_h5_file(m_name.filename_to_datafile_path(filename)) as h5_file:
        calibration = read_mz_calibration(h5_file)
        raw = int(h5_file["FullSpectra"]["MassAxis"].shape[0])
    if _parameters(calibration["mode"], calibration["par"], MZ_MODES) is None:
        return None
    offset = raw - int(m_compute.get_sum_signal(filename).size)
    if offset < 0:
        return None
    return {**calibration, "raw_index_offset": offset}


def _converter_axis(filename: str, record: dict) -> dict | None:
    """The acquisition axis of a file still on it, from the converter's
    coefficients on its record and the stored axis's first m/z."""
    mode, par = record.get("mode"), record.get("par")
    if _parameters(mode, par, MZ_MODES) is None:
        return None
    first = np.asarray(m_compute.get_sum_signal(filename).mz.values[:1], np.float64)
    offset = infer_index_offset(float(first[0]), mode, par) if first.size else None
    if offset is None:
        return None
    return {
        "mode": _mode(mode),
        "par": [float(value) for value in par],
        "raw_index_offset": offset,
    }


@dataclass(frozen=True)
class Acquisition:
    """What a fit's record keeps of the acquisition axis, and from where."""

    #: Where it came from: ``record`` (the record being replaced had it),
    #: ``converter`` (its coefficients, on a file still on its axis), ``raw
    #: file`` (read again from the ``.h5``), ``kept offsets`` (per-line
    #: offsets), or ``none``.
    source: str
    #: The keys to stamp on the record: ``acquisition_axis`` or
    #: ``acquisition_offsets``, or neither.
    keys: dict


def _try(what: str, filename: str, read):
    """One read of the filestore, which may fail without failing the apply:
    the record then keeps what the next source gives."""
    try:
        return read()
    except Exception as error:  # noqa: BLE001 - the apply goes on regardless
        runtime.logger.info(
            f"Could not read the acquisition axis of '{filename}' from {what}: "
            f"{type(error).__name__}: {error}"
        )
        return None


def resolve(
    filename: str, previous: dict | None, fit: dict, on_acquisition_axis: bool
) -> Acquisition:
    """What a TOF fit about to be applied keeps of the acquisition axis.

    Synchronous: it may read the filestore, so it runs in a worker thread,
    and before the apply, while the stored axis is still the one
    ``on_acquisition_axis`` says it is.

    In order: the replaced record's ``acquisition_axis``; on a file still on
    its acquisition axis, the converter's coefficients on the replaced record
    (a converter record, or a failed calibration's marker that kept them);
    the raw file's (:func:`read_raw_axis`); and where none gives the axis,
    per-line offsets: on a file still on its acquisition axis the fit's own
    lines read it, else the replaced record's kept offsets, else its own
    lines' observed offsets, which a first fit read on that axis.

    :param filename: The file.
    :param previous: The record being replaced, if any.
    :param fit: The fit about to be applied.
    :param on_acquisition_axis: Whether the file's stored axis is still the
        acquisition's (no applied fit has moved it).
    """
    previous = previous if isinstance(previous, dict) else {}
    axis = previous.get(ACQUISITION_AXIS)
    if isinstance(axis, dict):
        return Acquisition("record", {ACQUISITION_AXIS: copy.deepcopy(axis)})
    if on_acquisition_axis:
        axis = _try(
            "its converter record",
            filename,
            lambda: _converter_axis(filename, previous),
        )
        if axis is not None:
            return Acquisition("converter", {ACQUISITION_AXIS: axis})
    axis = _try("its raw file", filename, lambda: read_raw_axis(filename))
    if axis is not None:
        return Acquisition("raw file", {ACQUISITION_AXIS: axis})
    if on_acquisition_axis:
        offsets = observed_offsets(fit)
    elif isinstance(previous.get(ACQUISITION_OFFSETS), list):
        offsets = copy.deepcopy(previous[ACQUISITION_OFFSETS])
    else:
        offsets = observed_offsets(previous)
    if offsets:
        return Acquisition("kept offsets", {ACQUISITION_OFFSETS: offsets})
    return Acquisition("none", {})


def stamp(fit: dict, acquisition: Acquisition | None) -> None:
    """Stamp a record about to be written with when it was fitted and what
    it keeps of the acquisition axis.

    Any of the three keys the fit arrived with is dropped first: ``/mz_apply``
    takes its fit straight off the request body.

    :param fit: The record (mutated).
    :param acquisition: From :func:`resolve`; None for a file that is not a
        TOF, which keeps no acquisition axis.
    """
    for key in (ACQUISITION_AXIS, ACQUISITION_OFFSETS, FITTED_AT):
        fit.pop(key, None)
    fit[FITTED_AT] = datetime.now(timezone.utc).isoformat()
    if acquisition is not None:
        fit.update(acquisition.keys)
