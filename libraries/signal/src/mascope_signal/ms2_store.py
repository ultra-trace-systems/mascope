"""The MS2 store: a file's fragmentation scans, read once and kept.

Every MS2 route used to open the raw file, and nothing was kept between
requests. The store is written once, when a file whose scan-stream census
holds a fragmentation stream is converted, beside its peak store
(``ms2.zarr`` in its sample directory), and the routes read it instead. The
raw file is read for a file that has none - one converted before the store
existed, or one whose store could not be written - and for the little a
store cannot answer (below). Design: ``docs/dev/ms2_analysis.md``, section
2.2, decision 1.

**What it holds.** Everything is read through
:func:`mascope_thermo.thermo.get_ms2_store_contents`, the way the MS2 reads
of that module read it, per polarity:

- ``index/``: every scan the reader selects, of every MS order, with its time
  and whether it is an MS2 scan. A sample's window and polarity select from
  it exactly as the reader selects from the raw file, so the scan counts and
  the scans of each group are the reader's.
- ``scans/``: the scan table, one row per MS2 scan, the reader's per-scan
  record (``ms2_scan_records``) and the group the scan is in.
- ``centroids/``: each MS2 scan's centroids (m/z on the instrument's own
  axis, intensity, resolution, S/N), a ragged array indexed by the scan
  table's ``centroid_offsets``.
- ``averaged/``: each group's averaged centroids, and each parent peak's over
  every activation, at no S/N floor: the routes apply theirs on read.
- the attributes: the layout and its version, the grouping tolerance, the
  averaging bin, the groups and the parent peaks, and the vocabularies the
  scan table's text columns index into.

A group is a (polarity, parent peak, activation) group of the whole file, made
at :data:`PARENT_PEAK_TOLERANCE` as the raw read makes it, and keyed one way
everywhere (:func:`group_key`). The calibration factor of the file is applied
on read, as for the raw read, so a recalibration never rewrites the store.

**What a sample reads.** Its file's groups whose scans fall in its window,
in its polarity, as section 2.2 of the note has it. A group is made over the
whole file, so for a window its parent m/z and its scans can differ from what
a raw read of that window alone would group, when a precursor's m/z drifts
from scan to scan: the raw read clusters only the scans it selects. The
summary, the per-scan centroids of a group and a parent peak's fragment scans
are read from the store for any window. An averaged spectrum is stored over a
group's every scan, and a parent peak's merged one over every scan of it, so
the store answers a window that holds every scan of each group it touches;
merged over the activations, the window also has to hold every group of each
parent peak, or one alone. For any other window
:meth:`Ms2Store.averaged_centroids` answers None and the caller reads the raw
file.

The file's props record the store (``ms2_store``): the layout, its version,
the grouping tolerance, the averaging bin, the reader that wrote it, and each
group's polarity, key and scan count. A store of another layout or version
is not read.
"""

from __future__ import annotations

import dataclasses
import math
import os
import shutil
from collections import Counter
from functools import cached_property
from typing import Literal

import numpy as np
import zarr

import mascope_file.io as m_io
import mascope_file.name as m_name
import mascope_thermo.thermo as m_thermo
from mascope_signal.runtime import runtime
from mascope_thermo.backend import MS2_SCAN_RECORD_SOURCES, backend_name


#: What the store's layout is called, in its attributes and in the props.
LAYOUT = "mascope.ms2_store"
#: The layout's version. A store of another one is not read; the backfill
#: writes it again.
VERSION = 1
#: The store's name in the sample directory: ``ms2.zarr``.
STORE_NAME = "ms2"
#: The tolerance [Da] near-duplicate parent peaks are merged at: the MS2
#: routes' default. A request at another tolerance is answered from these
#: groups, and says the tolerance they were made at.
PARENT_PEAK_TOLERANCE = 0.001
#: The bin [ppm] a group's scans are averaged in, as the centroid route
#: averages them.
PPM = 1

#: Where a store being written is staged, beside the one it is to replace.
_STAGED_SUFFIX = ".staged"
#: Where the store it replaces is moved while the staged one takes its name.
_REPLACED_SUFFIX = ".replaced"
#: The largest chunk of a stored array, in elements.
_CHUNK = 1 << 20
#: The record fields stored as numbers, NaN for a field the scan lacks.
_NUMBER_FIELDS = (
    "precursor_mz",
    "nce",
    "isolation_width",
    "low_mass",
    "high_mass",
    "injection_time_ms",
)
#: The record fields stored as an index into a vocabulary of the store's
#: attributes, -1 for a field the scan lacks.
_TEXT_FIELDS = {"activation": "activations", "activation_kind": "activation_kinds"}
_CENTROID_FIELDS = ("mz", "intensity", "resolution", "signal_to_noise")
#: How the reader's centroid dictionaries name the centroid fields.
_READER_CENTROID_FIELDS = dict(
    zip(_CENTROID_FIELDS, ("masses", "intensities", "resolutions", "signal_to_noise"))
)
_POLARITY_CODES = {"+": 1, "-": -1}


def group_key(parent_peak_mz: float, activation: str) -> str:
    """The one string form of an MS2 group's key.

    The parent peak m/z with four decimals, ``@``, the activation:
    ``"137.0960@hcd25.00"``. The store, the group inventory and the scans
    route's ``group`` spell one group this way. A group whose scans' filter
    names no activation is its m/z alone (``"137.0960"``).

    :param parent_peak_mz: The group's parent peak m/z.
    :param activation: Its activation, ``""`` when the filter names none.
    :return: The key.
    """
    mz = f"{float(parent_peak_mz):.4f}"
    return f"{mz}@{activation}" if activation else mz


def parse_group_key(key: str) -> tuple[float, str]:
    """A group key's parent peak m/z, at four decimals, and its activation.

    Reads :func:`group_key`'s form, and any other spelling of the same m/z
    (``"137.096@hcd25.00"``, as the centroid route keys its spectra). A
    chained activation keeps its every stage (``"445.1200@cid30.00@hcd20.00"``).

    :param key: The key.
    :return: ``(parent peak m/z, activation)``.
    :raises ValueError: When the key does not start with a finite m/z.
    """
    mz, _, activation = key.strip().partition("@")
    try:
        parent_peak_mz = float(mz)
    except ValueError:
        parent_peak_mz = math.nan
    if not math.isfinite(parent_peak_mz):
        raise ValueError(
            f"'{key}' is no MS2 group key: one reads '<parent m/z>@<activation>', "
            "such as '137.0960@hcd25.00'"
        )
    return round(parent_peak_mz, 4), activation


def holds_fragmentation(streams: list[dict] | None) -> bool:
    """Whether a file's scan-stream census holds a fragmentation stream.

    What decides whether a file gets an MS2 store. A file with no census -
    converted before it existed, or by a reader that takes none - holds none
    as far as this is concerned.

    :param streams: The census (``SampleFileProps.scan_streams``).
    :return: True when a stream of MS order 2 or higher is recorded.
    """
    for stream in streams or []:
        order = (stream.get("signature") or {}).get("ms_order")
        # bool is an int, and True would read as MS1.
        if isinstance(order, int) and not isinstance(order, bool) and order >= 2:
            return True
    return False


def store_path(base_filename: str) -> str:
    """Where a file's MS2 store is: ``ms2.zarr`` in its sample directory."""
    return m_name.filename_to_zarr_path(base_filename, STORE_NAME)


def _polarity_code(polarity: str | None) -> int:
    return _POLARITY_CODES.get(polarity, 0)


def _offsets(lengths: list[int]) -> np.ndarray:
    return np.concatenate(([0], np.cumsum(lengths, dtype=np.int64))).astype(np.int64)


def _put(group: zarr.Group, name: str, values) -> None:
    values = np.ascontiguousarray(values)
    array = group.create_array(
        name,
        shape=values.shape,
        dtype=values.dtype,
        chunks=(max(1, min(values.shape[0], _CHUNK)),),
    )
    if values.size:
        array[:] = values


def _number(value) -> float:
    return np.nan if value is None else float(value)


def _write(path: str, contents: dict, parent_peak_tolerance: float, ppm: int) -> dict:
    """Write the store at ``path`` from what the reader gave; return its props."""
    index_numbers, index_times, index_polarity, index_ms2 = [], [], [], []
    records, record_group = [], []
    groups, precursors = [], []
    spectra: list[tuple] = []
    for polarity, held in contents["polarities"].items():
        ms2 = set(held["ms2_scan_numbers"])
        index_numbers += held["scan_numbers"]
        index_times += held["times"]
        index_polarity += [_POLARITY_CODES[polarity]] * len(held["scan_numbers"])
        index_ms2 += [n in ms2 for n in held["scan_numbers"]]

        group_of_scan = {}
        own_spectrum = {}
        for (parent_peak_mz, activation), scan_numbers in held["groups"].items():
            for number in scan_numbers:
                group_of_scan[number] = len(groups)
            own_spectrum.setdefault(parent_peak_mz, []).append(len(spectra))
            groups.append(
                {
                    "polarity": polarity,
                    "parent_peak_mz": parent_peak_mz,
                    "activation": activation,
                    "key": group_key(parent_peak_mz, activation),
                    "scan_count": len(scan_numbers),
                    "spectrum": len(spectra),
                }
            )
            spectra.append(held["averaged"][(parent_peak_mz, activation)])
        for parent_peak_mz, own in own_spectrum.items():
            merged = held["merged"].get((parent_peak_mz, ""))
            if merged is None:
                # One activation: its group's scans are every scan of it.
                (spectrum,) = own
            else:
                spectrum = len(spectra)
                spectra.append(merged)
            precursors.append(
                {
                    "polarity": polarity,
                    "parent_peak_mz": parent_peak_mz,
                    "spectrum": spectrum,
                }
            )
        for record in held["records"]:
            records.append(record)
            record_group.append(group_of_scan.get(record["scan_number"], -1))

    order = np.argsort(np.asarray(index_numbers, dtype=np.int64), kind="stable")
    by_scan = sorted(range(len(records)), key=lambda i: records[i]["scan_number"])
    records = [records[i] for i in by_scan]
    record_group = [record_group[i] for i in by_scan]
    vocabularies = {
        attribute: sorted({r[field] for r in records if r[field] is not None})
        for field, attribute in _TEXT_FIELDS.items()
    }
    volts = [record["hcd_energy_v"] or [] for record in records]
    scan_centroids = [contents["centroids"][r["scan_number"]] for r in records]

    root = zarr.open_group(path, mode="w", zarr_format=2)
    index = root.require_group("index")
    _put(index, "scan_number", np.asarray(index_numbers, dtype=np.int64)[order])
    _put(index, "time_s", np.asarray(index_times, dtype=np.float64)[order])
    _put(index, "polarity", np.asarray(index_polarity, dtype=np.int8)[order])
    _put(index, "ms2", np.asarray(index_ms2, dtype=bool)[order])

    scans = root.require_group("scans")
    _put(scans, "scan_number", np.array([r["scan_number"] for r in records], np.int64))
    _put(scans, "time_s", np.array([r["time_s"] for r in records], np.float64))
    _put(
        scans,
        "polarity",
        np.array([_polarity_code(r["polarity"]) for r in records], np.int8),
    )
    for field in _NUMBER_FIELDS:
        _put(scans, field, np.array([_number(r[field]) for r in records], np.float64))
    for field, attribute in _TEXT_FIELDS.items():
        position = {text: i for i, text in enumerate(vocabularies[attribute])}
        _put(
            scans,
            field,
            np.array([position.get(r[field], -1) for r in records], np.int32),
        )
    _put(scans, "tic", np.array([r["tic"] for r in records], np.float64))
    _put(scans, "hcd_energy_v_offsets", _offsets([len(v) for v in volts]))
    _put(
        scans,
        "hcd_energy_v",
        np.array([v for row in volts for v in row], dtype=np.float64),
    )
    _put(scans, "group", np.array(record_group, dtype=np.int32))
    _put(
        scans,
        "centroid_offsets",
        _offsets([len(c["masses"]) for c in scan_centroids]),
    )

    centroids = root.require_group("centroids")
    averaged = root.require_group("averaged")
    _put(averaged, "offsets", _offsets([len(spectrum[0]) for spectrum in spectra]))
    for i, field in enumerate(_CENTROID_FIELDS):
        reader_field = _READER_CENTROID_FIELDS[field]
        _put(
            centroids,
            field,
            np.concatenate(
                [np.asarray(c[reader_field], np.float64) for c in scan_centroids]
                or [np.array([], np.float64)]
            ),
        )
        _put(
            averaged,
            field,
            np.concatenate(
                [np.asarray(s[i], np.float64) for s in spectra]
                or [np.array([], np.float64)]
            ),
        )

    descriptor = {
        "layout": LAYOUT,
        "version": VERSION,
        "parent_peak_tolerance": parent_peak_tolerance,
        "ppm": ppm,
        "reader": backend_name(),
    }
    root.attrs.update(
        {
            **descriptor,
            "groups": groups,
            "precursors": precursors,
            **vocabularies,
            "record_sources": MS2_SCAN_RECORD_SOURCES,
        }
    )
    return {
        **descriptor,
        "groups": [
            {key: group[key] for key in ("polarity", "key", "scan_count")}
            for group in groups
        ],
    }


def write_ms2_store(
    base_filename: str,
    parent_peak_tolerance: float = PARENT_PEAK_TOLERANCE,
    ppm: int = PPM,
) -> dict:
    """Write a stored raw file's MS2 store, and record it in its props.

    Reads the raw file in the sample directory once
    (:func:`mascope_thermo.thermo.get_ms2_store_contents`). The store is
    written beside the one it replaces and takes its name only when whole,
    and the props are updated after it, so a reader meets the old store or
    the new one. Writers of one file's store are serialized
    (:func:`mascope_file.io.zarr_write_lock`).

    :param base_filename: The sample file's stored name.
    :param parent_peak_tolerance: The tolerance [Da] to group parent peaks at.
    :param ppm: The bin [ppm] to average a group's scans in.
    :return: What the props now record of the store (``ms2_store``).
    :raises NotImplementedError: When the file is not a raw Orbitrap file.
    """
    if m_name.get_sample_file_type(base_filename) != "orbi_raw":
        raise NotImplementedError(
            "An MS2 store is only written from an Orbitrap raw file."
        )
    contents = m_thermo.get_ms2_store_contents(
        m_name.filename_to_datafile_path(base_filename),
        parent_peak_tolerance=parent_peak_tolerance,
        ppm=ppm,
    )
    path = store_path(base_filename)
    staged = path + _STAGED_SUFFIX
    replaced = path + _REPLACED_SUFFIX
    with m_io.zarr_write_lock(path):
        for leftover in (staged, replaced):
            m_io.remove_path(leftover)
        try:
            descriptor = _write(staged, contents, parent_peak_tolerance, ppm)
        except BaseException:
            m_io.remove_path(staged)
            raise
        if os.path.exists(path):
            os.replace(path, replaced)
        os.replace(staged, path)
        shutil.rmtree(replaced, ignore_errors=True)
        m_io.update_props(base_filename, {"ms2_store": descriptor})
    return descriptor


def remove_ms2_store(base_filename: str) -> None:
    """Remove a file's MS2 store, and its record in the props, if it has one.

    :param base_filename: The sample file's stored name.
    """
    path = store_path(base_filename)
    with m_io.zarr_write_lock(path):
        props = m_io.read_props(base_filename)
        if props.get("ms2_store"):
            m_io.update_props(base_filename, {"ms2_store": {}})
        m_io.remove_path(path)


def refresh_ms2_store(base_filename: str) -> dict:
    """Write a stored file's MS2 store again, or remove it, by its census.

    What re-processing a file does to its store, and what a backfill does to
    a file converted before the store existed: a file whose census, in its
    props, holds a fragmentation stream (:func:`holds_fragmentation`) gets
    its store written; any other has none.

    :param base_filename: The sample file's stored name.
    :return: What the props now record of the store, ``{}`` for none.
    """
    props = m_io.read_props(base_filename)
    if holds_fragmentation(props.get("scan_streams")):
        return write_ms2_store(base_filename)
    # Nearly every file: nothing to remove, and nothing is touched
    if props.get("ms2_store") or os.path.exists(store_path(base_filename)):
        remove_ms2_store(base_filename)
    return {}


def _window_mask(
    times: np.ndarray, t_min: float | None, t_max: float | None
) -> np.ndarray:
    """The scans of ``times`` a reader's ``t_min``/``t_max`` select.

    As both reader backends select (``ScanSelector._time_mask``,
    ``OpenTFRawBackend._selected``): both bounds widened by machine epsilon
    times the upper one. A missing bound is the first or last scan's time.
    """
    if t_min is None and t_max is None:
        return np.ones(times.shape, dtype=bool)
    low = (times.min() if times.size else 0.0) if t_min is None else t_min
    high = (times.max() if times.size else 0.0) if t_max is None else t_max
    if low > high:
        raise m_thermo.InvalidRangeError(
            f"Invalid time range: t_min={low} s > t_max={high} s"
        )
    eps = np.finfo(np.float64).eps * high
    return (low - eps < times) & (times < high + eps)


@dataclasses.dataclass
class Ms2Store:
    """A file's MS2 store, open for reading.

    Open one with :meth:`open`. Every read takes a sample's selection - its
    window and its polarity - and answers None for one it cannot answer, so
    the caller reads the raw file instead: a selection with no polarity (the
    store's groups are per polarity), and an averaged spectrum over a window
    that cuts a group or, merged over the activations, holds several of a
    parent peak's groups and not all. Arrays are read when first needed, and
    only those a read needs.
    """

    base_filename: str
    root: zarr.Group
    attrs: dict
    _read: dict[str, np.ndarray] = dataclasses.field(
        default_factory=dict, init=False, repr=False
    )

    @classmethod
    def open(cls, base_filename: str) -> Ms2Store | None:
        """Open a file's MS2 store, or None when it has none to read.

        None for a file whose props cannot be read or record no store, or a
        store of another layout or version than this reads; and, with a
        warning, for a store its props record that cannot be opened. The caller then reads the raw
        file, as for a file converted before the store existed.

        :param base_filename: The sample file's stored name.
        """
        try:
            recorded = m_io.read_props(base_filename).get("ms2_store") or {}
        except (FileNotFoundError, ValueError):
            # No props to read: the file is not in the filestore, or its name
            # places it nowhere. What the raw read makes of it is the answer.
            return None
        if not recorded:
            return None
        if recorded.get("layout") != LAYOUT or recorded.get("version") != VERSION:
            runtime.logger.info(
                f"The MS2 store of {base_filename} is of layout "
                f"{recorded.get('layout')!r} version {recorded.get('version')!r}, "
                f"which is not read here ({LAYOUT!r} version {VERSION}); its MS2 "
                "reads read the raw file"
            )
            return None
        try:
            root = zarr.open_group(
                store_path(base_filename), mode="r", use_consolidated=False
            )
            attrs = root.attrs.asdict()
        except Exception:  # noqa: BLE001 - any failure reads the raw file
            runtime.logger.opt(exception=True).warning(
                f"Could not open the MS2 store of {base_filename}; its MS2 reads "
                "read the raw file"
            )
            return None
        return cls(base_filename=base_filename, root=root, attrs=attrs)

    @property
    def parent_peak_tolerance(self) -> float:
        """The tolerance [Da] the store's groups were made at."""
        return float(self.attrs["parent_peak_tolerance"])

    def _array(self, name: str) -> np.ndarray:
        """A whole stored array, read once for this open store."""
        if name not in self._read:
            self._read[name] = np.asarray(self.root[name][:])
        return self._read[name]

    @cached_property
    def _index(self) -> dict[str, np.ndarray]:
        return {
            name: self._array(f"index/{name}")
            for name in ("scan_number", "time_s", "polarity", "ms2")
        }

    @cached_property
    def _scan_numbers(self) -> np.ndarray:
        return self._array("scans/scan_number")

    @cached_property
    def _scan_groups(self) -> np.ndarray:
        return self._array("scans/group")

    def _selected(
        self,
        t_min: float | None,
        t_max: float | None,
        polarity: Literal["+", "-"],
    ) -> np.ndarray:
        """The index rows the selection takes: its polarity, in its window."""
        index = self._index
        return (index["polarity"] == _POLARITY_CODES[polarity]) & _window_mask(
            index["time_s"], t_min, t_max
        )

    def _ms2_rows(
        self,
        t_min: float | None,
        t_max: float | None,
        polarity: Literal["+", "-"],
        selected: np.ndarray | None = None,
    ) -> np.ndarray:
        """The scan-table rows of the selection's MS2 scans, in scan order.

        :param selected: The index rows the selection takes, when in hand.
        """
        index = self._index
        if selected is None:
            selected = self._selected(t_min, t_max, polarity)
        numbers = index["scan_number"][selected & index["ms2"]]
        return np.searchsorted(self._scan_numbers, numbers)

    def _fragment_rows(
        self, t_min: float | None, t_max: float | None, polarity: Literal["+", "-"]
    ) -> np.ndarray:
        """The selection's MS2 scans, refused as the raw read refuses none.

        :raises NoScansFoundError: When the selection holds no MS2 scan, as
            the reader raises it for the raw read of a group's scans.
        """
        rows = self._ms2_rows(t_min, t_max, polarity)
        if not rows.size:
            raise m_thermo.NoScansFoundError(
                "No scans found matching the specified filters: "
                f"polarity='{polarity}', time_range=({t_min}, {t_max}), "
                "ms_type='Ms2'"
            )
        return rows

    def _groups_held(self, rows: np.ndarray) -> list[tuple[int, dict, np.ndarray]]:
        """The groups with a scan among ``rows``, with them.

        The rows of one polarity's scans: a group is one polarity's.

        Ordered by parent peak, then by the first of those scans, as the raw
        read orders the groups of a selection.
        """
        if not rows.size:
            return []
        groups = self._scan_groups[rows]
        # Grouped in one sort; stable, so each group's rows stay in scan order
        order = np.argsort(groups, kind="stable")
        numbers, starts = np.unique(groups[order], return_index=True)
        ends = np.append(starts[1:], order.size)
        held = []
        for number, start, end in zip(numbers, starts, ends, strict=True):
            # A scan in no group: one whose filter names no precursor
            if number >= 0:
                group = self.attrs["groups"][int(number)]
                held.append((int(number), group, rows[order[start:end]]))
        return sorted(
            held,
            key=lambda item: (item[1]["parent_peak_mz"], int(item[2][0])),
        )

    def summary(
        self,
        t_min: float | None,
        t_max: float | None,
        polarity: Literal["+", "-"] | None,
    ) -> dict | None:
        """The MS2 summary of a selection, as the raw read gives it.

        :func:`mascope_thermo.thermo.get_ms2_summary_metadata`, made by the
        same functions from the stored scans, at the store's grouping
        tolerance, which ``parent_peak_tolerance`` says.

        :return: The summary, or None for a selection with no polarity.
        :raises NoScansFoundError: When the selection holds no scan.
        :raises ValueError: When it holds MS2 scans and none stores a width.
        """
        if polarity not in _POLARITY_CODES:
            return None
        selected = self._selected(t_min, t_max, polarity)
        if not selected.any():
            raise m_thermo.NoScansFoundError(
                "No scans found matching the specified filters: "
                f"polarity='{polarity}', time_range=({t_min}, {t_max}), "
                "ms_type='None'"
            )
        rows = self._ms2_rows(t_min, t_max, polarity, selected)
        # The times the reader selects by, as the raw summary reads them
        times = self._index["time_s"][selected & self._index["ms2"]]
        widths = self._array("scans/isolation_width")[rows]
        volts = self._array("scans/hcd_energy_v")
        volt_offsets = self._array("scans/hcd_energy_v_offsets")
        groups = []
        for _, group, own in self._groups_held(rows):
            at = np.searchsorted(rows, own)
            groups.append(
                m_thermo.ms2_group_summary(
                    group["parent_peak_mz"],
                    group["activation"],
                    int(own.size),
                    widths=[float(w) for w in widths[at] if not np.isnan(w)],
                    volts=[
                        [float(v) for v in volts[volt_offsets[r] : volt_offsets[r + 1]]]
                        for r in own
                        if volt_offsets[r + 1] > volt_offsets[r]
                    ],
                    times=[float(t) for t in times[at]],
                )
            )
        return m_thermo.ms2_summary(
            groups,
            [float(w) for w in widths if not np.isnan(w)],
            int(selected.sum()) - int(rows.size),
            int(rows.size),
            self.parent_peak_tolerance,
        )

    def _spectrum(self, number: int) -> tuple[np.ndarray, ...]:
        offsets = self._array("averaged/offsets")
        part = slice(int(offsets[number]), int(offsets[number + 1]))
        return tuple(
            self._array(f"averaged/{name}")[part].copy() for name in _CENTROID_FIELDS
        )

    def averaged_centroids(
        self,
        t_min: float | None,
        t_max: float | None,
        polarity: Literal["+", "-"] | None,
        by_activation: bool = True,
    ) -> dict[tuple[float, str], tuple[np.ndarray, ...]] | None:
        """Each group's averaged centroids, as the raw read averages them.

        :func:`mascope_thermo.thermo.get_ms2_centroids_by_parent` at the
        store's grouping tolerance, ``ppm`` and ``average=True``, on the
        instrument's axis and at no S/N floor: the caller applies the
        calibration factor and its floor. With ``by_activation`` False, one
        spectrum per parent peak over all of its scans in the window, keyed
        with an empty activation.

        :return: ``{(parent peak m/z, activation): (masses, intensities,
            resolutions, signal_to_noise)}``, or None for a selection with
            no polarity, or whose window holds some of a group's scans and
            not all: the stored average is over all of them. With
            ``by_activation`` False, None as well for a window that holds
            several of a parent peak's groups and not all: the store holds
            the average over every group of it and over each one alone.
        :raises NoScansFoundError: When the selection holds no MS2 scan.
        """
        if polarity not in _POLARITY_CODES:
            return None
        rows = self._fragment_rows(t_min, t_max, polarity)
        held = self._groups_held(rows)
        if any(own.size != group["scan_count"] for _, group, own in held):
            return None
        if by_activation:
            return {
                (group["parent_peak_mz"], group["activation"]): self._spectrum(
                    group["spectrum"]
                )
                for _, group, _ in held
            }
        merged = {
            precursor["parent_peak_mz"]: precursor["spectrum"]
            for precursor in self.attrs["precursors"]
            if precursor["polarity"] == polarity
        }
        activations = Counter(
            group["parent_peak_mz"]
            for group in self.attrs["groups"]
            if group["polarity"] == polarity
        )
        held_of: dict[float, list[dict]] = {}
        for _, group, _ in held:
            held_of.setdefault(group["parent_peak_mz"], []).append(group)
        spectra = {}
        for parent_peak_mz, groups in held_of.items():
            if len(groups) == activations[parent_peak_mz]:
                # Every scan of it: its merged spectrum
                spectrum = merged[parent_peak_mz]
            elif len(groups) == 1:
                # Every scan of one activation and none of another: that
                # group's scans, in the same order, so its own spectrum
                (group,) = groups
                spectrum = group["spectrum"]
            else:
                # Several activations of it and not all: no stored average is
                # over just their scans
                return None
            spectra[(parent_peak_mz, "")] = self._spectrum(spectrum)
        return spectra

    def _scan_centroids(self, rows: np.ndarray) -> list[dict]:
        """The centroids of the scan-table ``rows``, as the reader gives them
        (``ms2_centroids_for_scans``), on the instrument's axis."""
        if not rows.size:
            return []
        offsets = self._array("scans/centroid_offsets")
        # The span the rows' centroids lie in, read alone: the centroids are
        # the store's bulk
        first, last = int(offsets[rows.min()]), int(offsets[rows.max() + 1])
        spans = {
            name: np.asarray(self.root[f"centroids/{name}"][first:last])
            for name in _CENTROID_FIELDS
        }
        times = self._array("scans/time_s")
        scans = []
        for row in rows:
            part = slice(int(offsets[row]) - first, int(offsets[row + 1]) - first)
            scans.append(
                {
                    **{
                        _READER_CENTROID_FIELDS[name]: values[part]
                        for name, values in spans.items()
                    },
                    "timestamp": float(times[row]),
                }
            )
        return scans

    def fragment_scans(
        self,
        parent_peak_mz: float,
        t_min: float | None,
        t_max: float | None,
        polarity: Literal["+", "-"] | None,
        parent_peak_tolerance: float = PARENT_PEAK_TOLERANCE,
        activation: str | None = None,
    ) -> tuple[list[dict], list[float]] | None:
        """The per-scan centroids and TICs of one parent peak's scans.

        As :func:`mascope_thermo.thermo.get_ms2_centroids_per_scan_for_parent`
        gives them: the stored parent peak nearest ``parent_peak_mz`` within
        ``parent_peak_tolerance``, every activation of it unless
        ``activation`` names one, its scans in the selection in scan order.
        The groups are the store's.

        :return: ``(centroids, tics)``, empty when no parent peak is near
            enough, or None for a selection with no polarity.
        :raises NoScansFoundError: When the selection holds no MS2 scan.
        """
        if polarity not in _POLARITY_CODES:
            return None
        rows = self._fragment_rows(t_min, t_max, polarity)
        held = self._groups_held(rows)
        candidates = [
            group["parent_peak_mz"]
            for _, group, _ in held
            if abs(group["parent_peak_mz"] - parent_peak_mz) <= parent_peak_tolerance
        ]
        if not candidates:
            return [], []
        closest = min(candidates, key=lambda pp: abs(pp - parent_peak_mz))
        own = [
            own
            for _, group, own in held
            if group["parent_peak_mz"] == closest
            and (activation is None or group["activation"] == activation)
        ]
        if not own:
            return [], []
        rows = np.sort(np.concatenate(own))
        tics = self._array("scans/tic")
        return self._scan_centroids(rows), [float(tics[row]) for row in rows]

    def _records(self, rows: np.ndarray) -> list[dict]:
        """The reader's per-scan records of the scan-table ``rows``."""
        columns = {
            name: self._array(f"scans/{name}")
            for name in (
                "scan_number",
                "time_s",
                "polarity",
                "tic",
                "hcd_energy_v",
                "hcd_energy_v_offsets",
                *_NUMBER_FIELDS,
                *_TEXT_FIELDS,
            )
        }
        polarities = {code: polarity for polarity, code in _POLARITY_CODES.items()}
        offsets = columns["hcd_energy_v_offsets"]
        records = []
        for row in rows:
            volts = columns["hcd_energy_v"][offsets[row] : offsets[row + 1]]
            record = {
                "scan_number": int(columns["scan_number"][row]),
                "time_s": float(columns["time_s"][row]),
                "polarity": polarities.get(int(columns["polarity"][row])),
                **{
                    field: (
                        None
                        if np.isnan(columns[field][row])
                        else float(columns[field][row])
                    )
                    for field in _NUMBER_FIELDS
                },
                **{
                    field: (
                        None
                        if columns[field][row] < 0
                        else self.attrs[attribute][int(columns[field][row])]
                    )
                    for field, attribute in _TEXT_FIELDS.items()
                },
                "hcd_energy_v": [float(v) for v in volts] if volts.size else None,
                "tic": float(columns["tic"][row]),
            }
            # In the reader's field order, then where each field was read
            ordered = {field: record[field] for field in self.attrs["record_sources"]}
            ordered["sources"] = {
                field: None if ordered[field] is None else source
                for field, source in self.attrs["record_sources"].items()
            }
            records.append(ordered)
        return records

    def group_scans(
        self,
        key: str,
        t_min: float | None,
        t_max: float | None,
        polarity: Literal["+", "-"] | None,
    ) -> tuple[dict, list[dict], list[dict]] | None:
        """One group's scan records and per-scan centroids, in the selection.

        :param key: The group's key (:func:`group_key`; any spelling of its
            m/z reads, :func:`parse_group_key`).
        :return: The group (``polarity``, ``parent_peak_mz``, ``activation``,
            ``key``), its scans' records as the reader gives them
            (``ms2_scan_records``) and their centroids
            (``ms2_centroids_for_scans``), in scan order, the centroids on
            the instrument's axis. None for a selection with no polarity, or
            that holds no scan of such a group.
        :raises ValueError: When ``key`` is no group key.
        :raises NoScansFoundError: When the selection holds no MS2 scan.
        """
        if polarity not in _POLARITY_CODES:
            return None
        parent_peak_mz, activation = parse_group_key(key)
        number, group = next(
            (
                (number, group)
                for number, group in enumerate(self.attrs["groups"])
                if group["polarity"] == polarity
                and round(group["parent_peak_mz"], 4) == parent_peak_mz
                and group["activation"] == activation
            ),
            (None, None),
        )
        rows = self._fragment_rows(t_min, t_max, polarity)
        if group is None:
            return None
        own = rows[self._scan_groups[rows] == number]
        if not own.size:
            return None
        described = {
            name: group[name]
            for name in ("polarity", "parent_peak_mz", "activation", "key")
        }
        return described, self._records(own), self._scan_centroids(own)
