"""The hermetic MS2 fixture: three acquisition shapes on the committed MS1 file.

No committed file records a fragmentation scan, and the demo bundle has none,
so every MS2 test reads the committed MS1 file as an acquisition that does.
The file is opened as it always is, through the OpenTFRaw backend, and the
reader object that backend reads everything from is wrapped: the wrapper
presents a scan list of its own, in which each survey scan is one of the
file's two real scans and each fragment scan is synthetic. Everything the
wrapper does not override is the real file's: its instrument, its creation
date, its sample information.

Three shapes (``SHAPES``):

- ``manual``: fragmentation by hand with no method loaded. The MS1 block, then
  the fragment scans of one precursor; no scan records a method event or a
  master scan.
- ``interleaved``: a data-dependent method. Each cycle is a survey scan and
  two dependent scans, and one precursor's group stores two isolation widths.
- ``stepped``: a targeted stepped-energy method. One precursor at four
  collision energies, the other at one.

Each fragment scan carries the centroids of its step's spectrum exactly: the
precursor with a known survival, named fragments at the exact m/z of known
sub-formulas, one co-isolate, one harmonic ghost and one unexplained
centroid. So every later reading, breakdown and verdict has a known answer.

**The answers are one file.** ``ms2_fixture.json`` beside this module holds
the shapes, the spectra and what a reader must answer for each shape; this
module only builds from it. A change to the fixture is a change there.

Importable by the library tests too (``thermo_test_support.ms2_fixture``):
nothing here imports the backend, except :func:`convert`, which runs the
backend's converter and imports it when called.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from functools import cache
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from mascope_thermo.backend import OpenTFRawBackend


_HERE = Path(__file__).resolve().parent
ANSWERS_FILE = _HERE / "ms2_fixture.json"
#: The committed MS1 file every shape is built on.
SOURCE_FILE = (
    _HERE.parents[2]
    / "libraries/thermo/tests/test_files/KORBI2_AMB_POS_20260109174345.raw"
)

#: What the scan index holds for an event or segment that was never set.
_UNSET = 0xFFFF
_SECONDS_PER_MINUTE = 60.0


@cache
def _answers() -> dict:
    return json.loads(ANSWERS_FILE.read_text(encoding="utf-8"))


def answers() -> dict:
    """The fixture's known answers: ``ms2_fixture.json``, parsed.

    A fresh copy each call, so a test that edits what it reads cannot change
    what the next one does.
    """
    return json.loads(json.dumps(_answers()))


SHAPES: tuple[str, ...] = tuple(_answers()["shapes"])


def scan_time_s(scan_number: int) -> float:
    """When scan ``scan_number`` was acquired [s], as the reader reports it."""
    return scan_number * _answers()["scan_interval_min"] * _SECONDS_PER_MINUTE


def _filter(shape: dict, scan: dict) -> str:
    precursor = _answers()["precursors"][scan["precursor"]]
    low, high = shape["scan_ranges"][scan["precursor"]]
    dependent = " d" if shape["dependent"] else ""
    return (
        f"FTMS + c NSI{dependent} Full ms2 {precursor['isolation_mz']:.4f}"
        f"@hcd{scan['nce']:.2f} [{low:.4f}-{high:.4f}]"
    )


class _Scan(SimpleNamespace):
    """One scan of a shape: its scan-table row, and what it reads as.

    ``real`` is the file's own scan a survey scan stands on; ``None`` for a
    fragment scan, whose ``centroids`` and ``trailer`` are its own.
    """


def _scans(raw, shape_name: str) -> list[_Scan]:
    """Every scan of ``shape_name``, built over the open file ``raw``."""
    fixture = _answers()
    shape = fixture["shapes"][shape_name]
    real_table = raw.scan_table()
    columns = list(real_table)
    real_rows = {
        int(number): dict(zip(columns, values))
        for number, values in zip(
            real_table["scan_number"], zip(*(real_table[c] for c in columns))
        )
    }
    noise = float(fixture["noise"])
    # A fragment scan's trailer has the labels of the file's own, in its order.
    template = raw.scan_parameters(1) or {}

    scans = []
    for number, scan in enumerate(shape["scans"], start=1):
        event = scan.get("event")
        index_event = _UNSET if event is None else event - 1
        master = scan.get("master")
        linkage = {
            "Scan Segment:": 0 if event is None else 1,
            "Scan Event:": 0 if event is None else event,
            "Master Index:": 0 if master is None else master,
            "Master Scan Number:": 0 if master is None else master,
        }
        retention_time = number * fixture["scan_interval_min"]
        if "ms1" in scan:
            real = int(scan["ms1"])
            row = {
                **real_rows[real],
                "scan_number": number,
                "scan_event": index_event,
                "retention_time": retention_time,
            }
            trailer = {**(raw.scan_parameters(real) or {}), **linkage}
            scans.append(_Scan(row=row, real=real, trailer=trailer, centroids=None))
            continue

        spectrum = fixture["spectra"][f"{scan['precursor']}@{scan['nce']}"]
        precursor = fixture["precursors"][scan["precursor"]]
        settings = shape["fragment_trailer"]
        mz = np.array([c["mz"] for c in spectrum["centroids"]], dtype=np.float64)
        intensity = np.array(
            [c["intensity"] for c in spectrum["centroids"]], dtype=np.float64
        )
        resolution = np.round(settings["FT Resolution:"] * np.sqrt(200.0 / mz))
        centroids = {
            "mz": mz,
            "intensity": intensity,
            "resolution": resolution,
            "noise": np.full(mz.shape, noise),
            "signal_to_noise": intensity / noise,
            # OpenTFRaw's flag word per centroid: none flagged.
            "descriptor": np.zeros(mz.shape, dtype=np.uint32),
        }
        low, high = shape["scan_ranges"][scan["precursor"]]
        filter_string = _filter(shape, scan)
        apex = int(np.argmax(intensity))
        row = {
            **real_rows[1],
            "scan_number": number,
            "scan_event": index_event,
            "data_size": int(mz.size),
            "ms_level": 2,
            "scan_mode": "centroid",
            "retention_time": retention_time,
            "filter_string": filter_string,
            "total_ion_current": float(intensity.sum()),
            "base_peak_mz": float(mz[apex]),
            "base_peak_intensity": float(intensity[apex]),
            "low_mz": float(low),
            "high_mz": float(high),
            "ion_injection_time_ms": float(settings["Ion Injection Time (ms):"]),
            "charge": precursor["charge"],
            "precursor_mz": precursor["isolation_mz"],
            "isolation_target_mz": precursor["isolation_mz"],
            "isolation_width": float(scan["width"]),
            "collision_energy": float(scan["nce"]),
            "collision_energy_is_nce": True,
            "activation": "HCD",
            "master_scan_number": master,
            # OpenTFRaw's own decoded extras: nothing past the reader's
            # opening reads them, and a survey scan's would say otherwise.
            "extra": {},
        }
        trailer = {
            **template,
            **linkage,
            "Micro Scan Count:": 1,
            "Charge State:": precursor["charge"],
            "Monoisotopic M/Z:": precursor["isolation_mz"],
            **settings,
            "MS2 Isolation Width:": float(scan["width"]),
            "HCD Energy:": float(scan["nce"]),
            "HCD Energy V:": float(spectrum["volts"]),
        }
        scans.append(_Scan(row=row, real=None, trailer=trailer, centroids=centroids))
    return scans


class Ms2Acquisition:
    """An ``opentfraw.RawFile`` that reads as one shape of the fixture.

    Wraps the reader the OpenTFRaw backend opened. A survey scan reads as the
    real scan it stands on, but for its number, its time and its method
    event; a fragment scan reads as its step's spectrum. Everything else is
    the wrapped file's.
    """

    def __init__(self, raw, shape: str):
        self._raw = raw
        self.shape = shape
        self._scans = _scans(raw, shape)

    def __getattr__(self, name):
        return getattr(self._raw, name)

    def _scan(self, scan_number) -> _Scan:
        number = int(scan_number)
        if not 1 <= number <= len(self._scans):
            raise IndexError(f"scan {number} is not in the {self.shape} shape")
        return self._scans[number - 1]

    @property
    def num_scans(self) -> int:
        return len(self._scans)

    def scan_table(self) -> dict:
        rows = [scan.row for scan in self._scans]
        return {column: [row[column] for row in rows] for column in rows[0]}

    def scan_filter(self, scan_number) -> str:
        return self._scan(scan_number).row["filter_string"]

    def scan_parameters(self, scan_number) -> dict:
        return dict(self._scan(scan_number).trailer)

    def centroid_labels(self, scan_number) -> dict:
        scan = self._scan(scan_number)
        if scan.real is not None:
            return self._raw.centroid_labels(scan.real)
        return {name: values.copy() for name, values in scan.centroids.items()}

    def profile(self, scan_number):
        """A fragment scan is a centroid scan, and stores no profile: empty
        arrays, as OpenTFRaw answers for one."""
        scan = self._scan(scan_number)
        if scan.real is not None:
            return self._raw.profile(scan.real)
        return np.array([], dtype=np.float64), np.array([], dtype=np.float64)

    def scan(self, scan_number) -> dict:
        scan = self._scan(scan_number)
        if scan.real is not None:
            peaks = self._raw.scan(scan.real)
            return {**scan.row, "mz": peaks["mz"], "intensity": peaks["intensity"]}
        return {
            **scan.row,
            "mz": scan.centroids["mz"].copy(),
            "intensity": scan.centroids["intensity"].copy(),
        }


@contextmanager
def presented(shape: str) -> Iterator[Path]:
    """Read the committed MS1 file, and any copy of it, as ``shape``.

    Every file the OpenTFRaw backend opens while this is entered reads as the
    shape, so a copy the converter makes in its filestore does too; the
    backend is chosen for the duration.

    :param shape: One of :data:`SHAPES`.
    :return: The path of the committed file, as the shape's raw file.
    """
    if shape not in SHAPES:
        raise ValueError(f"no MS2 fixture shape {shape!r}; the shapes are {SHAPES}")
    enter = OpenTFRawBackend.__enter__

    def entered(self):
        backend = enter(self)
        backend._raw = Ms2Acquisition(backend._raw, shape)
        return backend

    with (
        patch.dict(os.environ, {"MASCOPE_THERMO_BACKEND": "opentfraw"}),
        patch.object(OpenTFRawBackend, "__enter__", entered),
    ):
        yield SOURCE_FILE


@contextmanager
def converted(shape: str, filestore: Path) -> Iterator[SimpleNamespace]:
    """Run the backend's converter over ``shape`` into ``filestore``.

    :func:`convert`, with the shape presented. The shape stays presented, and
    the filestore in place, while entered, so what reads the stored file
    afterwards reads the shape.

    Backend only: imports the converter when called.

    :param shape: One of :data:`SHAPES`.
    :param filestore: An empty directory to stand for the filestore.
    :return: What :func:`convert` returns.
    """
    with presented(shape) as source, convert(source, filestore) as done:
        yield done


@contextmanager
def convert(source: Path, filestore: Path) -> Iterator[SimpleNamespace]:
    """Run the backend's converter over the raw file ``source`` into ``filestore``.

    The converter as it runs on an upload - the properties and the census,
    the filestore directory and its copy of the file, the instrument-function
    fit and peak detection - with the two calls to the server answered here:
    the instrument config gets the id ``"instrument-config"``, and the
    sample-file record is recorded instead of made. The file is read as it
    reads while this is entered: a presentation entered around it, a shape of
    this fixture or another reading of a committed file, is what the
    converter reads.

    Backend only: imports the converter when called.

    :param source: The raw file to convert.
    :param filestore: An empty directory to stand for the filestore.
    :return: ``props`` (the extracted ``SampleFileProps``), ``sample_dir``
        and ``records``: the record the converter asked the server for, as
        ``(props, instrument_function_id)``.
    """
    from queue import Queue
    from threading import Event

    import mascope_backend.file_converter.base_processor as base_processor
    from mascope_file.name import parse_path_from_item_filename
    from mascope_thermo.processor import RawProcessor

    def in_filestore(*parts):
        return os.path.join(filestore, *parts)

    records = []
    with ExitStack() as stack:
        for module in ("mascope_file.name", "mascope_file.io"):
            runtime = stack.enter_context(patch(f"{module}.runtime"))
            runtime.filestore = in_filestore
        stack.enter_context(
            patch.object(
                base_processor,
                "create_instrument_config_db_record",
                lambda *args, **kwargs: "instrument-config",
            )
        )
        stack.enter_context(
            patch.object(
                base_processor,
                "create_sample_file_db_record",
                lambda props, function_id, **kwargs: records.append(
                    (props, function_id)
                ),
            )
        )
        processor = RawProcessor(
            socket_client=None, file_queue=Queue(), shutdown_event=Event()
        )
        processor.file_to_process = str(source)
        processor._emit_progress_notification = lambda progress: None
        processor._get_file_context = lambda: SimpleNamespace(
            access_token="token", device_id=None
        )
        props = processor._get_sample_file_props()
        processor._process_file(props, str(source))
        yield SimpleNamespace(
            props=props,
            sample_dir=Path(parse_path_from_item_filename(props.filename)),
            records=records,
        )
