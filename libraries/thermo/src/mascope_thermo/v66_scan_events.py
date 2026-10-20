"""The scan events of a version-66 raw file whose records carry names.

**Interim, for GitHub issue #2391.** Delete this module once the pinned
OpenTFRaw reads these records itself (MAS-170), together with its import in
``mascope_thermo.backend``, the ``try``/``except`` around the call to
:func:`redecode_if_misread` in ``OpenTFRawBackend.__enter__``, its tests
(``test_v66_scan_events.py``) and its section of ``docs/backend.md``.

In a version-66 raw file the scan-event stream holds one record per scan, from
four bytes past the run header's scan trailer address to its scan parameters
address. A record is a 136-byte preamble and a 136-byte body, and the body's
last four bytes count the UTF-16 code units that follow it. A targeted MS2
scan run from an inclusion list stores the list entry's name there, so its
record is ``272 + 2 * count`` bytes, and the records of one file are not all
one size.

OpenTFRaw 2.0.0 does not read that count. It takes one record size for the
whole stream, and where the stream does not divide evenly by the number of
records, its average. From the second record on it decodes at an offset that
drifts, so each scan's polarity, MS order, analyzer and filter come from
whatever bytes are there, and so does the calibration that converts its
profile from frequency to m/z. The scan index, the trailer and the centroids
are read from elsewhere and are right.

**When this engages.** Only for a version-66 file whose records, walked by
their own counts, end exactly at the scan parameters address after one record
per scan event, whose names all read as text, and whose record boundaries
differ from the ones OpenTFRaw reads. Every other file is returned as
OpenTFRaw opened it.

**What it re-reads.** For an engaged file :func:`redecode_if_misread` returns
a stand-in for the OpenTFRaw ``RawFile``. It decodes each scan event at its
own record and derives from it what OpenTFRaw derives from the scan event:
the scan table's polarity, MS order, analyzer, scan mode, filter string and
precursor fields, :meth:`scan_filter`, the same keys of :meth:`scan`, and the
m/z axis of :meth:`profile`. The rest is the ``RawFile``'s own. The scan
filter follows OpenTFRaw's grammar exactly, so a file's stream keys are the
ones a reader that reads these records will give it.

**The backstop.** A stand-in that cannot be right is refused, and the file
does not open (:class:`UnreadableScanEventsError`), before the warning that
it was read: when a scan reads as an analyzer its instrument cannot have, by
the rule the backend judges every file by (:mod:`mascope_thermo.scan_events`),
or when the scans of one trailer scan event (of one segment: a method numbers
its events within each) read as more than one MS order. Polarity is not
compared within an event, since a polarity-switching full scan alternates
under one, and scan event 0, which a manual acquisition records on every
scan, is no event.

The byte layouts and the derivations are ported from OpenTFRaw v2.0.0
(https://github.com/Sigilweaver/OpenTFRaw, ``crates/opentfraw/src``):
``run_header.rs`` and ``raw_file_info.rs`` (addresses), ``reader.rs`` (the
record-size inference and the trailer accessors), ``scan_event.rs`` (the
preamble accessors and ``read_v66``), ``scan_filter.rs`` (``build_filter``),
``mzml.rs`` (``scan_metadata``), ``scan_index.rs`` and ``scan_data.rs`` (the
profile and ``freq_to_mz``), and ``opentfraw-py/src/lib.rs`` (the Python
keys). Copyright 2026 Sigilweaver Holdings LLC, licensed under the Apache
License, Version 2.0. Translated to Python and reduced to the version-66
Orbitrap paths for Mascope.
"""

from __future__ import annotations

import math
import os
import re
import struct
import threading
from dataclasses import dataclass

import numpy as np

from mascope_runtime.logging import SENTRY_FINGERPRINT
from mascope_thermo.run_header import _address_table, _NotFollowed
from mascope_thermo.scan_events import MisreadScanEventsError, misread_analyzers


_VERSION = 66
_PREAMBLE_SIZE = 136
_RECORD_SIZE = _PREAMBLE_SIZE + 136
# Where in a record the body's last word, the name's length, sits.
_NAME_LENGTH_AT = _RECORD_SIZE - 4

# OpenTFRaw's fallback when the stream does not divide evenly: the tribrid
# primary and dependent record sizes.
_TRIBRID_PRIMARY = 232
_TRIBRID_DEPENDENT = 344

# The run header of a version 64+ file: a 592-byte sample info, thirteen
# 520-byte file names with two doubles among them, ten integers, then the
# 64-bit stream addresses.
_RH_FIRST_SCAN = 8
_RH_LAST_SCAN = 12
_RH_NTRAILER = 7376
_RH_SCAN_INDEX_ADDR = 7408
_RH_DATA_ADDR = 7416
_RH_SCAN_TRAILER_ADDR = 7448
_RH_SCAN_PARAMS_ADDR = 7456
_RH_SIZE = 7464

# A version-66 scan index entry, and where in it the packet's offset sits.
_SCAN_INDEX_ENTRY_SIZE = 88
_SCAN_INDEX_PACKET_OFFSET = 72
_PACKET_HEADER_SIZE = 40
# The first scan's data size below which OpenTFRaw reads a file as SRM.
_FLAT_PEAKS_DATA_SIZE = 100

# Preamble bytes.
_POLARITY, _SCAN_MODE, _MS_POWER, _SCAN_TYPE = 4, 5, 6, 7
_DEPENDENT, _IONIZATION, _ACTIVATION, _WIDEBAND, _ANALYZER = 10, 11, 24, 32, 40

_ANALYZERS = {0: "ITMS", 1: "TQMS", 2: "SQMS", 3: "TOFMS", 4: "FTMS", 5: "Sector"}
_IONIZATIONS = {
    0: "EI",
    1: "CI",
    2: "FABI",
    3: "ESI",
    4: "APCI",
    5: "NSI",
    6: "TSI",
    7: "FDI",
    8: "MALDI",
    9: "GDI",
}
# Undefined (5) and an unknown byte render as "Full".
_SCAN_TYPES = {0: "Full", 1: "Z", 2: "SIM", 3: "SRM", 4: "CRM", 6: "Q1MS", 7: "Q3MS"}
_HCD, _CID, _ETHCD = 1, 4, 12
_ACTIVATIONS = {
    _HCD: "hcd",
    2: "mpid",
    3: "etd",
    _CID: "cid",
    5: "ecd",
    6: "irmpd",
    7: "pd",
    8: "pqd",
    9: "uvpd",
    10: "sid",
    _ETHCD: "ethcd",
}

_SCAN_EVENT = "Scan Event:"
_SCAN_SEGMENT = "Scan Segment:"

# What str::parse::<f64> accepts, which Python's float() is laxer than.
_RUST_FLOAT = re.compile(
    r"[+-]?(?:inf|infinity|nan|(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?)", re.IGNORECASE
)


class UnreadableScanEventsError(MisreadScanEventsError):
    """A raw file whose scan events read past OpenTFRaw cannot be right.

    A :class:`MisreadScanEventsError`, so a caller handles both refusals as
    one and nothing changes for it when this module goes (MAS-170); its text
    names the cause, the inclusion-list names the reader does not yet read.
    """


@dataclass(frozen=True)
class _Layout:
    """Where an engaged file's scan events and scan packets are."""

    data_addr: int
    scan_index_addr: int
    num_scans: int
    #: Each record's preamble and body, in record order.
    records: tuple[tuple[bytes, bytes], ...]


@dataclass(frozen=True)
class _Event:
    """One scan event as ``ScanEvent::read_v66`` decodes it."""

    preamble: bytes
    window: tuple[float, float] | None
    coefficients: tuple[float, ...]
    #: ``(precursor m/z, energy)`` per reaction.
    reactions: tuple[tuple[float, float], ...]

    def byte(self, at: int) -> int:
        return self.preamble[at]


_warned: set[tuple[str, int, int]] = set()
_warned_lock = threading.Lock()


def redecode_if_misread(raw, datafile_path: str):
    """``raw``, or a stand-in that reads its scan events past OpenTFRaw.

    :param raw: The ``opentfraw.RawFile`` the backend opened.
    :param datafile_path: Path to the same raw file.
    :return: ``raw`` itself unless the file's scan-event records carry names
        that OpenTFRaw misreads; then a stand-in for it (module docstring).
    :raises UnreadableScanEventsError: When the scan events of an engaged
        file, read by their own record sizes, still cannot be right.
    """
    layout = _misread_layout(datafile_path)
    if (
        layout is None
        or layout.num_scans == 0
        or layout.num_scans != int(raw.num_scans)
    ):
        return raw
    table = raw.scan_table()
    first = int(raw.first_scan)
    if list(table.get("scan_number") or []) != list(
        range(first, first + layout.num_scans)
    ):
        return raw
    if int(table["data_size"][0]) < _FLAT_PEAKS_DATA_SIZE:
        # OpenTFRaw reads this as an SRM file, where it renders no scan
        # event, and nothing here is about one.
        return raw
    stand_in = _RedecodedRawFile(raw, datafile_path, layout, table)
    _warn_once(datafile_path, stand_in)
    return stand_in


# -- detection --------------------------------------------------------------


def _misread_layout(datafile_path: str) -> _Layout | None:
    """The layout of a file whose scan-event records OpenTFRaw misreads, or
    None for any other file, one that cannot be followed included."""
    try:
        with open(datafile_path, "rb") as f:
            version, data_addr, addresses = _address_table(f)
            if version != _VERSION:
                return None
            header = _ms_run_header(f, addresses, data_addr)
            if header is None:
                return None
            first, last, n_events, scan_index_addr, start, end = header
            stream_bytes = max(end - start, 0)
            # Every record is at least the fixed size, so a stream of no more
            # than that per record either cannot be walked to its end or
            # holds only unnamed records, which OpenTFRaw reads right.
            if n_events == 0 or stream_bytes <= n_events * _RECORD_SIZE:
                return None
            f.seek(start)
            records = _walk_named_records(f, stream_bytes, n_events)
    except (OSError, _NotFollowed, struct.error):
        return None
    if records is None or not _differs_from_opentfraw(stream_bytes, records):
        return None
    return _Layout(
        data_addr=data_addr,
        scan_index_addr=scan_index_addr,
        num_scans=last - first + 1 if last >= first else 0,
        records=tuple(records),
    )


def _ms_run_header(f, addresses: list[int], data_addr: int) -> tuple | None:
    """The run header of the controller OpenTFRaw reads: the first whose
    trailer counts scan events, or whose scan data address is the file's."""
    for address in addresses:
        if address == 0:
            continue
        f.seek(address)
        header = f.read(_RH_SIZE)
        if len(header) != _RH_SIZE:
            return None
        (n_trailer,) = struct.unpack_from("<I", header, _RH_NTRAILER)
        (own_data_addr,) = struct.unpack_from("<Q", header, _RH_DATA_ADDR)
        if n_trailer > 0 or own_data_addr == data_addr:
            first, last = struct.unpack_from("<2I", header, _RH_FIRST_SCAN)
            (scan_index_addr,) = struct.unpack_from("<Q", header, _RH_SCAN_INDEX_ADDR)
            (trailer_addr,) = struct.unpack_from("<Q", header, _RH_SCAN_TRAILER_ADDR)
            (params_addr,) = struct.unpack_from("<Q", header, _RH_SCAN_PARAMS_ADDR)
            return (
                first,
                last,
                n_trailer,
                scan_index_addr,
                trailer_addr + 4,
                params_addr,
            )
    return None


def _walk_named_records(
    f, stream_bytes: int, n_events: int
) -> list[tuple[bytes, bytes]] | None:
    """Each record's preamble and body, walked by its name's length from
    where ``f`` stands, or None unless the walk ends exactly at the end of the
    stream and every name reads as text.

    Read record by record, so a stream of other records stops the walk where
    they first fail to be named ones, without reading the rest.
    """
    records, at = [], 0
    for _ in range(n_events):
        if at + _RECORD_SIZE > stream_bytes:
            return None
        record = f.read(_RECORD_SIZE)
        if len(record) != _RECORD_SIZE:
            return None
        (length,) = struct.unpack_from("<I", record, _NAME_LENGTH_AT)
        if at + _RECORD_SIZE + 2 * length > stream_bytes:
            return None
        name = f.read(2 * length)
        if len(name) != 2 * length or not _is_text(name):
            return None
        records.append((record[:_PREAMBLE_SIZE], record[_PREAMBLE_SIZE:] + name))
        at += _RECORD_SIZE + 2 * length
    return records if at == stream_bytes else None


def _is_text(name: bytes) -> bool:
    try:
        text = name.decode("utf-16-le")
    except UnicodeDecodeError:
        return False
    return all(0x20 <= ord(c) != 0x7F for c in text)


def _differs_from_opentfraw(
    stream_bytes: int, records: list[tuple[bytes, bytes]]
) -> bool:
    """Whether OpenTFRaw 2.0.0 reads other record boundaries than ``records``.

    The size it infers (``RawFileReader::open``): the stream divided evenly,
    else the tribrid primary and dependent sizes where they add up to it,
    else the floor average, applied per record by whether the preamble at the
    offset it reads names a primary scan.
    """
    n_events = len(records)
    if stream_bytes % n_events == 0:
        primary = dependent = stream_bytes // n_events
    else:
        gap = _TRIBRID_DEPENDENT - _TRIBRID_PRIMARY
        numerator = max(n_events * _TRIBRID_DEPENDENT - stream_bytes, 0)
        n_primary = numerator // gap
        n_dependent = max(n_events - n_primary, 0)
        if (
            numerator % gap == 0
            and n_primary * _TRIBRID_PRIMARY + n_dependent * _TRIBRID_DEPENDENT
            == stream_bytes
        ):
            primary, dependent = _TRIBRID_PRIMARY, _TRIBRID_DEPENDENT
        else:
            primary = dependent = stream_bytes // n_events
    primary = max(primary, _PREAMBLE_SIZE)
    dependent = max(dependent, _PREAMBLE_SIZE)
    # In step up to each record compared, so OpenTFRaw reads that record's
    # own preamble.
    for preamble, body in records:
        is_primary = preamble[_MS_POWER] <= 1 and preamble[_DEPENDENT] != 1
        if (primary if is_primary else dependent) != _PREAMBLE_SIZE + len(body):
            return True
    return False


# -- decoding ---------------------------------------------------------------


def _f64(data: bytes, at: int) -> float:
    return struct.unpack_from("<d", data, at)[0]


def _u32(data: bytes, at: int) -> int:
    return struct.unpack_from("<I", data, at)[0]


def _plausible_window(low: float, high: float) -> bool:
    return (
        math.isfinite(low)
        and math.isfinite(high)
        and low >= 0.1
        and low <= high
        and high <= 50_000.0
    )


def _decode_event(preamble: bytes, body: bytes) -> _Event:
    """``ScanEvent::read_v66`` for a body read at its own size, which reads
    it as one of a file of one record layout (no tribrid dependent)."""
    size = len(body)
    window, window_at = None, None
    for at in (64, 8, 128, max(size - 80, 0)):
        if at + 16 > size:
            continue
        low, high = _f64(body, at), _f64(body, at + 8)
        if _plausible_window(low, high):
            window, window_at = (low, high), at
            break

    coefficients: tuple[float, ...] = ()
    params_at = 80 if window_at == 64 else max(size - 64, 0)
    if params_at + 4 <= size:
        count = min(_u32(body, params_at), max(size - (params_at + 4), 0) // 8)
        coefficients = tuple(_f64(body, params_at + 4 + 8 * i) for i in range(count))

    ms_n = preamble[_MS_POWER] >= 2
    dependent = preamble[_DEPENDENT] == 1
    reactions: list[tuple[float, float]] = []
    if (ms_n or dependent) and size >= 8:
        count = _u32(body, 4)
        most = max(size - (8 + 32), 0) // 32
        if count != 0 and count <= max(most, 1):
            for i in range(count):
                at = 8 + 32 * i
                if at + 32 > size:
                    break
                mz = _f64(body, at)
                if math.isfinite(mz) and mz >= 0.0:
                    reactions.append((mz, _f64(body, at + 16)))
    # An Exploris dependent scan stores its one reaction from body offset 4,
    # with no count before it.
    if not reactions and (ms_n or dependent) and size >= 36:
        mz = _f64(body, 4)
        if math.isfinite(mz) and 0.0 < mz < 50_000.0:
            reactions.append((mz, _f64(body, 20)))
    return _Event(preamble, window, coefficients, tuple(reactions))


def _ms_level(event: _Event | None) -> int:
    power = event.byte(_MS_POWER) if event else 0
    return power if 2 <= power <= 8 else 1


def _activation_str(analyzer: str | None, code: int) -> str:
    if code == _CID and analyzer == "FTMS":
        return "hcd"
    return _ACTIVATIONS[code]


# -- the trailer, as OpenTFRaw's ScanParams accessors read it ---------------
#
# OpenTFRaw's typed getters take a value of the type they ask for and nothing
# else. scan_parameters() gives each value its stored type, so a float is a
# float (f32 or f64) and an int one of the integer types; a bool is no int.


def _get_f64(params: dict, label: str) -> float | None:
    value = params.get(label)
    return value if type(value) is float else None


def _get_i32(params: dict, label: str) -> int | None:
    # The one getter laxer than OpenTFRaw's, which takes Int8, Int16 and
    # Int32 only: an unsigned integer is an int here as well.
    value = params.get(label)
    return value if type(value) is int else None


def _get_str(params: dict, label: str) -> str | None:
    value = params.get(label)
    return value if type(value) is str else None


def _parse_f64(text: str) -> float | None:
    return float(text) if _RUST_FLOAT.fullmatch(text) else None


def _first_f64(params: dict, *labels: str) -> float | None:
    for label in labels:
        value = _get_f64(params, label)
        if value is not None:
            return value
    return None


def _monoisotopic_mz(params: dict) -> float | None:
    value = _first_f64(
        params,
        "Monoisotopic M/Z:",
        "MS2 Isolation M/Z:",
        "Isolation Center M/Z:",
        "Precursor M/Z:",
    )
    return value if value is not None and value > 0.0 else None


def _activation_energy(params: dict) -> tuple[float | None, bool]:
    """The primary activation energy, and whether it is an NCE."""
    for label in ("HCD Energy:", "HCD Energy V:", "CE:"):
        text = _get_str(params, label)
        if text is not None:
            value = _parse_f64(text.strip().rstrip("%"))
            if value is not None and value > 0.0:
                return value, True
    value = _get_f64(params, "Normalized Collision Energy:")
    if value is not None and value > 0.0:
        return value, True
    for label in ("HCD Energy (eV):", "HCD Energy eV:", "Collision Energy (eV):"):
        value = _get_f64(params, label)
        if value is not None and value > 0.0:
            return value, False
    return None, False


def _supplemental_energy(params: dict) -> float | None:
    value = _get_f64(params, "Supplemental Activation CE:")
    if value is not None:
        return value
    text = _get_str(params, "Supplemental Activation:")
    return None if text is None else _parse_f64(text.strip().rstrip("%"))


def _energy(value: float | None) -> str:
    return "" if value is None else f"{value:.2f}"


def _positive(value):
    return value if value is not None and value > 0 else None


# -- what a scan's event gives it ---------------------------------------------


def _scan_filter(event: _Event, low_mz: float, high_mz: float, params: dict) -> str:
    """``build_filter``, with the precursor and energies from the trailer as
    ``RawFileReader::scan_filter`` takes them."""
    analyzer = _ANALYZERS.get(event.byte(_ANALYZER))
    out = [analyzer or "MS", " "]
    out.append({0: "-", 1: "+"}.get(event.byte(_POLARITY), "+") + " ")
    mode = {0: "c", 1: "p"}.get(event.byte(_SCAN_MODE))
    if mode:
        out.append(mode + " ")
    ionization = _IONIZATIONS.get(event.byte(_IONIZATION))
    if ionization:
        out.append(ionization + " ")
    if event.byte(_DEPENDENT) == 1:
        out.append("d ")
    out.append(_SCAN_TYPES.get(event.byte(_SCAN_TYPE), "Full") + " ")
    n = _ms_level(event)
    out.append("ms" if n == 1 else f"ms{n}")

    if n >= 2:
        code = event.byte(_ACTIVATION)
        act = code if code in _ACTIVATIONS else None
        energy, _ = _activation_energy(params)
        ethcd = f"@etd{_energy(energy)}@hcd{_energy(_supplemental_energy(params))}"
        reactions = event.reactions
        if (
            n == 2
            and len(reactions) >= 2
            and sum(1 for mz, _ in reactions if mz > 0.0) == 1
            and analyzer == "FTMS"
            and act in (_CID, _HCD)
        ):
            # A tribrid EThcD scan: two reactions, one with a precursor.
            mz = next(mz for mz, _ in reactions if mz > 0.0)
            out.append(f" {mz:.4f}@etd@hcd{_energy(energy)}")
        elif reactions:
            last = len(reactions) - 1
            for i, (mz, stored) in enumerate(reactions):
                if mz <= 0.0:
                    continue
                out.append(f" {mz:.4f}")
                if act is None:
                    continue
                stored = stored if stored > 0.0 else None
                if i == last and act == _ETHCD:
                    out.append(ethcd)
                    continue
                # The last reaction takes the trailer's energy over its own,
                # the ones before it their own over the trailer's.
                shown = (
                    (energy if energy is not None else stored)
                    if i == last
                    else (stored if stored is not None else energy)
                )
                out.append(f"@{_activation_str(analyzer, act)}{_energy(shown)}")
        else:
            mz = _monoisotopic_mz(params)
            if mz is not None:
                out.append(f" {mz:.4f}")
                if act == _ETHCD:
                    out.append(ethcd)
                elif act is not None:
                    out.append(f"@{_activation_str(analyzer, act)}{_energy(energy)}")

    if (
        math.isfinite(low_mz)
        and math.isfinite(high_mz)
        and low_mz > 0.0
        and high_mz > low_mz
    ):
        out.append(f" [{low_mz:.4f}-{high_mz:.4f}]")
    elif event.window and event.window[0] > 0.0 and event.window[1] > event.window[0]:
        out.append(f" [{event.window[0]:.4f}-{event.window[1]:.4f}]")
    return "".join(out)


def _precursor(event: _Event | None, params: dict) -> dict:
    """The precursor keys of an MSn scan, as ``scan_metadata`` fills them."""
    reaction = event.reactions[0] if event and event.reactions else None
    target = _get_f64(params, "MS2 Isolation Offset:")
    if target is None:
        target = _get_f64(params, "Target M/Z:")
    target = _positive(target)
    if target is None:
        target = _monoisotopic_mz(params)
    if target is None and reaction is not None:
        target = _positive(reaction[0])
    selected = _monoisotopic_mz(params)
    if selected is None:
        selected = target
    charge = _get_i32(params, "Charge State:")
    energy, is_nce = _activation_energy(params)
    if energy is None and reaction is not None:
        energy = _positive(reaction[1])
    master = _get_i32(params, "Master Scan Number:")
    if master is None:
        master = _get_i32(params, "Master Index:")
    analyzer = _ANALYZERS.get(event.byte(_ANALYZER)) if event else None
    code = event.byte(_ACTIVATION) if event else 0
    return {
        "charge": _positive(charge),
        "precursor_mz": selected,
        "isolation_target_mz": target,
        "isolation_width": _first_f64(
            params,
            "MS2 Isolation Width:",
            "MSn Isolation Width:",
            "Isolation Width (M/Z):",
            "MS2 Isolation Width (M/Z):",
        ),
        "collision_energy": energy,
        "collision_energy_is_nce": is_nce,
        "activation": (
            _activation_str(analyzer, code) if code in _ACTIVATIONS else None
        ),
        "master_scan_number": _positive(master),
    }


_NO_PRECURSOR = {
    "charge": None,
    "precursor_mz": None,
    "isolation_target_mz": None,
    "isolation_width": None,
    "collision_energy": None,
    "collision_energy_is_nce": False,
    "activation": None,
    "master_scan_number": None,
}


def _scan(event: _Event | None, low_mz: float, high_mz: float, params: dict) -> dict:
    """The keys of a scan that ``scan_metadata`` derives from its event."""
    level = _ms_level(event)
    fields = {
        "ms_level": level,
        "is_dia": False,
        "is_wideband": False,
        "polarity": "",
        "scan_mode": None,
        "analyzer": None,
        "filter_string": None,
    }
    if event is not None:
        fields.update(
            is_dia=event.byte(_MS_POWER) >= 2 and event.byte(_DEPENDENT) != 1,
            is_wideband=event.byte(_WIDEBAND) == 1,
            polarity={0: "-", 1: "+"}.get(event.byte(_POLARITY), ""),
            scan_mode={0: "centroid", 1: "profile"}.get(event.byte(_SCAN_MODE)),
            analyzer=_ANALYZERS.get(event.byte(_ANALYZER)),
            filter_string=_scan_filter(event, low_mz, high_mz, params),
        )
    fields.update(_NO_PRECURSOR if level == 1 else _precursor(event, params))
    return fields


def _freq_to_mz(freq: np.ndarray, coefficients: tuple[float, ...]) -> np.ndarray:
    """``freq_to_mz``, over a scan's whole frequency axis."""
    with np.errstate(divide="ignore", invalid="ignore"):
        if len(coefficients) == 4:
            a, b, c = coefficients[1:4]
            mz = a + b / freq + c / (freq * freq)
        elif len(coefficients) in (5, 7):
            a, b, c = coefficients[2:5]
            f2 = freq * freq
            mz = a + b / f2 + c / (f2 * f2)
        else:
            return freq.copy()
    return np.where(freq == 0.0, 0.0, mz)


# -- the stand-in ---------------------------------------------------------------


class _RedecodedRawFile:
    """An ``opentfraw.RawFile`` whose scan events are read at their records.

    Everything not overridden here is the ``RawFile``'s own, except what reads
    every scan's event and Mascope does not call, which raises rather than
    answer from the misread events.
    """

    _NOT_REDECODED = frozenset({"iter_scans", "iter_records", "to_mzml"})

    def __init__(self, raw, datafile_path: str, layout: _Layout, table: dict):
        self._raw = raw
        self._path = datafile_path
        self._layout = layout
        self._first = int(raw.first_scan)
        self._events = [_decode_event(p, b) for p, b in layout.records]
        self._scans: list[dict] = []
        self._trailer_events: list[tuple[int | None, int] | None] = []
        for row, scan_number in enumerate(table["scan_number"]):
            params = raw.scan_parameters(int(scan_number)) or {}
            event = self._event(int(scan_number))
            self._scans.append(
                _scan(event, table["low_mz"][row], table["high_mz"][row], params)
            )
            self._trailer_events.append(_trailer_scan_event(params))
        self._packet_offsets: np.ndarray | None = None
        _refuse_if_implausible(raw.instrument_model, self._scans, self._trailer_events)

    def __getattr__(self, name):
        if name in self._NOT_REDECODED:
            raise NotImplementedError(
                f"{name}() would read the scan events OpenTFRaw misreads in "
                "this file (GitHub issue #2391)"
            )
        return getattr(self._raw, name)

    def _index(self, scan_number: int) -> int | None:
        index = int(scan_number) - self._first
        return index if 0 <= index < len(self._scans) else None

    def _event(self, scan_number: int) -> _Event | None:
        index = int(scan_number) - self._first
        return self._events[index] if 0 <= index < len(self._events) else None

    @property
    def redecoded_records(self) -> int:
        """How many of the file's records carry a name."""
        return sum(1 for _, body in self._layout.records if len(body) > 136)

    def scan_table(self) -> dict:
        table = {name: list(values) for name, values in self._raw.scan_table().items()}
        for row, scan_number in enumerate(table["scan_number"]):
            index = self._index(scan_number)
            if index is None:
                continue
            self._patch(table, row, self._scans[index])
        return table

    def scan(self, scan_number: int) -> dict:
        record = self._raw.scan(scan_number)
        index = self._index(scan_number)
        if index is not None:
            scan = self._scans[index]
            record.update(scan)
            record["extra"] = _patched_extra(record.get("extra"), scan)
        return record

    def scan_filter(self, scan_number: int) -> str | None:
        index = self._index(scan_number)
        if index is None:
            return self._raw.scan_filter(scan_number)
        return self._scans[index]["filter_string"]

    def profile(self, scan_number: int) -> tuple[np.ndarray, np.ndarray]:
        index = self._index(scan_number)
        if index is None:
            return self._raw.profile(scan_number)
        event = self._event(scan_number)
        return _read_profile(
            self._path,
            self._layout.data_addr + int(self._offsets()[index]),
            event.coefficients if event else (),
        )

    def _offsets(self) -> np.ndarray:
        if self._packet_offsets is None:
            size = self._layout.num_scans * _SCAN_INDEX_ENTRY_SIZE
            with open(self._path, "rb") as f:
                f.seek(self._layout.scan_index_addr)
                index = f.read(size)
            if len(index) != size:
                raise OSError(f"the scan index of {self._path} ends early")
            entries = np.frombuffer(index, dtype=np.uint8).reshape(
                -1, _SCAN_INDEX_ENTRY_SIZE
            )
            self._packet_offsets = (
                entries[:, _SCAN_INDEX_PACKET_OFFSET : _SCAN_INDEX_PACKET_OFFSET + 8]
                .copy()
                .view("<u8")
                .ravel()
            )
        return self._packet_offsets

    @staticmethod
    def _patch(table: dict, row: int, scan: dict) -> None:
        for key, value in scan.items():
            if key in table:
                table[key][row] = value
        if "extra" in table:
            table["extra"][row] = _patched_extra(table["extra"][row], scan)


def _patched_extra(extra, scan: dict):
    if not isinstance(extra, dict):
        return extra
    extra = dict(extra)
    extra["opentfraw.is_dia"] = "true" if scan["is_dia"] else "false"
    extra["opentfraw.is_wideband"] = "true" if scan["is_wideband"] else "false"
    return extra


def _read_profile(
    path: str, packet_addr: int, coefficients: tuple[float, ...]
) -> tuple[np.ndarray, np.ndarray]:
    """A scan's profile, as ``profile()`` converts it, on its own event's
    calibration."""
    with open(path, "rb") as f:
        f.seek(packet_addr)
        header = f.read(_PACKET_HEADER_SIZE)
        if len(header) != _PACKET_HEADER_SIZE:
            raise OSError(f"the scan packet at {packet_addr} of {path} ends early")
        profile_words, layout = _u32(header, 4), _u32(header, 12)
        if profile_words == 0:
            return np.array([], dtype=np.float64), np.array([], dtype=np.float64)
        profile = f.read(4 * profile_words)
    first_value, step = struct.unpack_from("<2d", profile, 0)
    n_chunks = _u32(profile, 16)
    # A chunk is its first bin, its bin count, a fudge where the layout says
    # so, and its signal, all in 4-byte words; the chunks start at word 6.
    header_words = 3 if layout & 0xFF != 0 else 2
    words = np.frombuffer(profile, dtype="<u4", count=len(profile) // 4).tolist()
    first_bins, counts, fudges, starts, at = [], [], [], [], 6
    for _ in range(n_chunks):
        first_bins.append(words[at])
        counts.append(words[at + 1])
        fudges.append(
            struct.unpack_from("<f", profile, 4 * (at + 2))[0]
            if header_words == 3
            else 0.0
        )
        starts.append(at + header_words)
        at += header_words + words[at + 1]
    counts = np.array(counts, dtype=np.int64)
    # Each sample's position within its chunk.
    within = np.arange(counts.sum(), dtype=np.int64) - np.repeat(
        np.cumsum(counts) - counts, counts
    )
    signal = np.frombuffer(profile, dtype="<f4", count=len(profile) // 4)
    intensity = signal[np.repeat(np.array(starts, dtype=np.int64), counts) + within]
    bins = (np.repeat(np.array(first_bins, dtype=np.int64), counts) + within).astype(
        np.float64
    )
    freq = first_value + bins * step
    mz = _freq_to_mz(freq, coefficients) + np.repeat(
        np.array(fudges, dtype=np.float64), counts
    )
    return mz, intensity.astype(np.float64)


# -- the backstop -----------------------------------------------------------------


def _trailer_int(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _trailer_scan_event(params: dict) -> tuple[int | None, int] | None:
    """The scan's trailer ``(segment, event)``, or None for event 0 or none.

    A method numbers its scan events within each of its segments, so an
    event is one experiment only together with its segment.
    """
    event = _trailer_int(params.get(_SCAN_EVENT))
    if not event:
        return None
    return _trailer_int(params.get(_SCAN_SEGMENT)), event


def _refuse_if_implausible(
    model: str | None,
    scans: list[dict],
    trailer_events: list[tuple[int | None, int] | None],
) -> None:
    """Refuse a stand-in whose scan events still cannot be right."""
    problem = misread_analyzers(model, [scan["analyzer"] for scan in scans])
    if problem:
        raise UnreadableScanEventsError(_refusal(problem))
    levels: dict[tuple[int | None, int], set[int]] = {}
    for scan, event in zip(scans, trailer_events):
        if event:
            levels.setdefault(event, set()).add(scan["ms_level"])
    mixed = sum(1 for orders in levels.values() if len(orders) > 1)
    if mixed:
        raise UnreadableScanEventsError(
            _refusal(
                f"the scans of {mixed} of its {len(levels)} scan events read "
                "as more than one MS order"
            )
        )


def _refusal(problem: str) -> str:
    return (
        "The scan events of this file cannot be read: its records carry the "
        "names of an inclusion list, which the raw-file reader does not yet "
        f"read, and read by those names, {problem}. The file was not "
        "processed (GitHub issue #2391)."
    )


def _warn_once(datafile_path: str, stand_in: _RedecodedRawFile) -> None:
    """One warning per file and process: an engaged file is rare, and each is
    one to re-process once the reader reads it (MAS-169)."""
    try:
        stat = os.stat(datafile_path)
        key = (os.path.realpath(datafile_path), stat.st_size, stat.st_mtime_ns)
    except OSError:
        key = (datafile_path, -1, -1)
    with _warned_lock:
        if key in _warned:
            return
        _warned.add(key)
    from mascope_thermo.runtime import runtime

    runtime.logger.bind(**{SENTRY_FINGERPRINT: ["v66-named-scan-events"]}).warning(
        f"Read the scan events of {datafile_path} past OpenTFRaw: "
        f"{stand_in.redecoded_records} of its records carry inclusion-list "
        "names, which the pinned reader misreads (GitHub issue #2391)."
    )
