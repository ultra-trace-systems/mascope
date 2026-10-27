"""The axis fingerprint a raw Orbitrap file is converted with.

The converter computes the file's axis fingerprint
(``mascope_thermo.axis_fingerprint``) as it extracts the file's properties,
and stores it in the file's ``.props`` with the rest: ``SampleFileProps``
names it, so every processor has to answer for it, and ``write_props``
writes it before the raw file is copied in. Like the scan-stream census it
is a measurement served to others, so it never costs a file: what the
library reports as an error is stored as one and logged at WARNING, and the
file converts all the same. The deployment's ``[backend] axis_fingerprint``
can switch it off, which stores ``{}``.

Most of these run on a stand-in reader handle and a stand-in computation,
and say what the processor does with each kind of answer. The committed MS1
file, read with the real reader, pins that the document reaches ``.props``
whole, and that a computation failing inside the library leaves the file
converted.
"""

import json
from contextlib import contextmanager
from pathlib import Path
from queue import Queue
from threading import Event

import pytest
from pydantic import ValidationError
from test_utils import captured_logs

import mascope_backend.file_converter.base_processor as base_processor
import mascope_file.name as m_name
import mascope_thermo.processor as m_processor
from mascope_backend.file_converter.base_processor import (
    BaseFileProcessor,
    computes_axis_fingerprints,
)
from mascope_runtime.config import BackendConfig
from mascope_runtime.logging import _SENTRY_LEVELS
from mascope_thermo.axis_fingerprint import PARAMS, reference
from mascope_thermo.backend import OpenTFRawBackend
from mascope_thermo.processor import RawProcessor
from mascope_thermo.thermo import NoScansFoundError
from mascope_tofwerk.processor import H5Processor


_REPO_ROOT = Path(__file__).resolve().parents[5]
#: A committed MS1 file from an Orbitrap without frequency bands: its
#: document is "ok", with no band grid measured.
MS1_FILE = (
    _REPO_ROOT / "libraries/thermo/tests/test_files/KORBI2_AMB_NEG_20260108144525.raw"
)

FILE = "ORBI-1_run.raw"
#: The reader handle the processor's file context opens, standing in.
HANDLE = object()
#: A document as the library hands one back, as far as the processor reads
#: it: by its status.
OK = {
    "schema": reference.SCHEMA,
    "version": reference.VERSION,
    "status": "ok",
    "modes": [],
}


@pytest.fixture(autouse=True)
def as_shipped(monkeypatch):
    """The deployment as shipped, whatever this machine's config says: the
    fingerprint computed, on the open reader."""
    monkeypatch.setattr(
        base_processor.runtime.full_config.backend, "axis_fingerprint", "ingest"
    )
    monkeypatch.setenv("MASCOPE_THERMO_BACKEND", "opentfraw")
    monkeypatch.delenv("MASCOPE_THERMO_DLL_DIR", raising=False)


def _setting(monkeypatch, value):
    monkeypatch.setattr(
        base_processor.runtime.full_config.backend, "axis_fingerprint", value
    )


class _Computation:
    """Stands in for ``compute_axis_fingerprint``: records each call, and
    answers with a document or raises."""

    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def __call__(self, backend, *, reader_name):
        self.calls.append((backend, reader_name))
        if isinstance(self.answer, BaseException):
            raise self.answer
        return self.answer


@pytest.fixture
def computes(monkeypatch):
    """Put a stand-in computation where the processor calls the library."""

    def _install(answer):
        computation = _Computation(answer)
        monkeypatch.setattr(m_processor, "compute_axis_fingerprint", computation)
        return computation

    return _install


def _processor():
    """A processor whose file context opens the stand-in handle."""
    processor = RawProcessor(
        socket_client=None, file_queue=Queue(), shutdown_event=Event()
    )
    processor.file_to_process = FILE

    @contextmanager
    def _context(_file_path):
        yield HANDLE

    processor._file_context_manager = _context
    return processor


def _read(processor):
    """The property, and the records logged while it was read."""
    with captured_logs() as records:
        document = processor.axis_fingerprint
    return document, records


def _faults(records):
    """The records error monitoring receives."""
    return [r for r in records if r["level"].name in _SENTRY_LEVELS]


class TestTheSetting:
    def test_the_shipped_default_computes_it(self):
        """Read off the model, not off this machine's config, which a
        developer may have set either way."""
        assert BackendConfig.model_fields["axis_fingerprint"].default == "ingest"

    def test_it_is_ingest_or_off(self):
        assert BackendConfig(name="backend", axis_fingerprint="off").axis_fingerprint
        with pytest.raises(ValidationError):
            BackendConfig(name="backend", axis_fingerprint="on")

    def test_the_converter_reads_it_from_the_backend_config(self, monkeypatch):
        _setting(monkeypatch, "off")
        assert computes_axis_fingerprints() is False

        _setting(monkeypatch, "ingest")
        assert computes_axis_fingerprints() is True

    def test_a_config_with_no_backend_section_computes_it(self, monkeypatch):
        """The converter reads another module's section, which a pared-down
        config may not carry: the default holds."""
        monkeypatch.setattr(base_processor.runtime._full_config, "backend", None)

        assert computes_axis_fingerprints() is True

    def test_off_stores_nothing_and_computes_nothing(self, monkeypatch, computes):
        _setting(monkeypatch, "off")
        computation = computes(OK)

        document, records = _read(_processor())

        assert document == {}
        assert computation.calls == []
        assert _faults(records) == []


class TestWhatTheProcessorStores:
    def test_every_processor_answers_for_it(self):
        """``SampleFileProps`` names it, so the metaclass makes it abstract,
        and a processor that does not answer cannot be made."""
        assert "axis_fingerprint" in BaseFileProcessor.__abstractmethods__

    def test_the_document_computed_from_the_open_file(self, computes):
        computation = computes(OK)

        document, records = _read(_processor())

        assert document == OK
        assert computation.calls == [(HANDLE, "opentfraw")]
        assert _faults(records) == []

    def test_the_reader_named_is_the_one_the_deployment_selects(
        self, monkeypatch, computes
    ):
        """The document is defined on the open reader's stored points, so
        the library is told which reader opened the file."""
        monkeypatch.setenv("MASCOPE_THERMO_BACKEND", "Thermo")
        computation = computes(reference.minimal_document("unavailable"))

        _read(_processor())

        assert computation.calls == [(HANDLE, "thermo")]

    def test_an_unavailable_document_is_stored_without_a_fault(self, computes):
        """How the deployment is set up, not something wrong with the file."""
        computes(reference.minimal_document("unavailable"))

        document, records = _read(_processor())

        assert document == reference.minimal_document("unavailable")
        assert _faults(records) == []
        assert any(
            r["level"].name == "INFO" and "unavailable" in r["message"] for r in records
        )

    def test_an_error_document_is_stored_and_reported(self, computes):
        error = reference.minimal_document("error", "ValueError: no m/z")
        computes(error)

        document, records = _read(_processor())

        assert document == error
        (fault,) = _faults(records)
        assert fault["level"].name == "WARNING"
        assert FILE in fault["message"]
        assert "ValueError: no m/z" in fault["message"]

    def test_a_failure_around_the_computation_stores_an_error_document(self, computes):
        computes(RuntimeError("reader exploded"))

        document, records = _read(_processor())

        assert document == reference.minimal_document(
            "error", "RuntimeError: reader exploded"
        )
        (fault,) = _faults(records)
        assert fault["level"].name == "WARNING"
        assert fault["exception"] is not None

    def test_a_file_with_no_scans_stores_nothing_and_reports_no_fault(self, computes):
        """It is refused as an empty acquisition by the property that reads
        its length, as data, so nothing on the way may report a fault."""
        computes(NoScansFoundError("No scans found"))

        document, records = _read(_processor())

        assert document == {}
        assert _faults(records) == []

    def test_a_tofdaq_file_has_none(self):
        processor = H5Processor(
            socket_client=None, file_queue=Queue(), shutdown_event=Event()
        )

        assert processor.axis_fingerprint == {}


@pytest.fixture
def converted(monkeypatch, tmp_path):
    """Convert the committed MS1 file with the real reader into a sample
    directory under tmp_path, the instrument functions, the peak detection
    and the record stubbed.

    :return: A function that converts it and returns the sample directory,
        the stored ``.props``, the steps run and the records logged.
    """
    if not MS1_FILE.exists():
        pytest.skip(f"sample file missing: {MS1_FILE}")

    def _sample_dir(filename):
        return str(tmp_path / filename)

    monkeypatch.setattr(m_name, "parse_path_from_item_filename", _sample_dir)
    monkeypatch.setattr(base_processor, "parse_path_from_item_filename", _sample_dir)

    def _convert():
        processor = RawProcessor(
            socket_client=None, file_queue=Queue(), shutdown_event=Event()
        )
        processor.file_to_process = str(MS1_FILE)
        steps = []
        monkeypatch.setattr(processor, "_emit_progress_notification", lambda _: None)
        monkeypatch.setattr(
            processor,
            "_create_instrument_config",
            lambda _props: steps.append("functions") or ("ifunc-001", None, None),
        )
        monkeypatch.setattr(
            processor, "_compute_peaks", lambda *_: steps.append("peaks")
        )
        monkeypatch.setattr(
            processor, "_create_db_record", lambda *_: steps.append("record")
        )
        with captured_logs() as records:
            props = processor._get_sample_file_props()
            processor._process_file(props, str(MS1_FILE))
        sample_dir = Path(_sample_dir(props.filename))
        stored = json.loads((sample_dir / ".props").read_text())
        return sample_dir, stored, steps, records

    return _convert


class TestUnderTheRealReader:
    def test_the_document_reaches_props_whole(self, converted):
        sample_dir, stored, steps, records = converted()

        document = stored["axis_fingerprint"]
        assert document["status"] == "ok"
        # What a later computation is told apart by
        assert document["version"] == reference.VERSION
        assert document["extractor"]["params"] == PARAMS
        (mode,) = document["modes"]
        assert "grid_unmeasured" in mode["flags"]
        # What an item is joined to its mode by
        assert mode["stream_keys"] == [s["key"] for s in stored["scan_streams"]]
        assert steps == ["functions", "peaks", "record"]
        assert (sample_dir / "data.raw").is_file()
        assert _faults(records) == []

    def test_a_computation_that_fails_leaves_the_file_converted(
        self, converted, monkeypatch
    ):
        def unreadable(self, scan_number):
            raise RuntimeError("profile unreadable")

        monkeypatch.setattr(OpenTFRawBackend, "scan_profile", unreadable)

        sample_dir, stored, steps, records = converted()

        assert stored["axis_fingerprint"] == reference.minimal_document(
            "error", "RuntimeError: profile unreadable"
        )
        assert steps == ["functions", "peaks", "record"]
        assert (sample_dir / "data.raw").is_file()
        (fault,) = _faults(records)
        assert "RuntimeError: profile unreadable" in fault["message"]
