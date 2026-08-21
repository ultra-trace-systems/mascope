"""An acquisition that recorded no scans fails as data, not as a fault.

A raw file with too few scans - an aborted run, or one that wrote a file
before recording anything - has nothing to ingest, so the file still fails and
lands in ``failed_files``. What it must not do is surface as an unexpected
exception: the Thermo path raised the reader's ``NoScansFoundError`` and the
TOF path an ``IndexError`` off the end of an empty array, both reaching error
monitoring with a traceback as if Mascope itself had broken.

Two things this must not over-reach on, both covered below. A pre-allocated
BufTimes array means an aborted TOF run ends in unwritten rows of zeros, so
"the last write holds no scans" is not "the file holds no scans" - the scans
before the abort are real and the file's length is measurable from them. And
a run that recorded exactly one scan has no inter-scan spacing to average, so
it must be refused rather than ingested with a NaN interval.
"""

from contextlib import contextmanager
from queue import Queue
from threading import Event

import numpy as np
import pytest

from mascope_backend.file_converter.errors import (
    EmptyAcquisitionError,
    describe_exception,
    is_routine_file_failure,
)
from mascope_backend.runtime import runtime
from mascope_thermo.processor import RawProcessor
from mascope_thermo.thermo import NoScansFoundError
from mascope_tofwerk.processor import H5Processor


EMPTY_MESSAGE = "The file contains no scans; the acquisition is empty or was aborted."


def _with_handle(processor, file_handle):
    """Point the processor's file context at a stand-in reader handle."""

    @contextmanager
    def _context(_file_path):
        yield file_handle

    processor._file_context_manager = _context
    return processor


def _thermo(file_handle):
    processor = RawProcessor(
        socket_client=None, file_queue=Queue(), shutdown_event=Event()
    )
    processor.file_to_process = "ORBI-1_empty.raw"
    return _with_handle(processor, file_handle)


def _tof(buf_times):
    processor = H5Processor(
        socket_client=None, file_queue=Queue(), shutdown_event=Event()
    )
    processor.file_to_process = "TOF-1_empty.h5"
    handle = {"TimingData": {"BufTimes": np.array(buf_times, dtype=float)}}
    return _with_handle(processor, handle)


class _ScanlessRawFile:
    """Reader stand-in for a .raw whose scan list is empty."""

    def scan_times(self, ms_type=None):  # noqa: ARG002
        raise NoScansFoundError(
            "No scans found matching the specified filters: polarity='None', "
            "time_range=(None, None), ms_type='None'"
        )

    def acquisition_parameters(self, max_scans=None):  # noqa: ARG002
        # The real backend selects ms_type="Ms" here, so a scanless file
        # raises the same reader error as scan_times does.
        raise NoScansFoundError(
            "No scans found matching the specified filters: polarity='None', "
            "time_range=(None, None), ms_type='Ms'"
        )


class _PopulatedRawFile:
    def scan_times(self, ms_type=None):  # noqa: ARG002
        return np.array([0.0, 1.5, 3.0])


class TestThermoEmptyAcquisition:
    def test_scanless_file_raises_empty_acquisition(self):
        # `length` applies no filters, so the reader finding nothing can only
        # mean the file holds no scans at all.
        with pytest.raises(EmptyAcquisitionError):
            _thermo(_ScanlessRawFile()).length

    def test_the_reader_error_is_kept_as_the_cause(self):
        try:
            _thermo(_ScanlessRawFile()).length
        except EmptyAcquisitionError as e:
            assert isinstance(e.__cause__, NoScansFoundError)
        else:
            pytest.fail("expected EmptyAcquisitionError")

    def test_populated_file_still_reports_its_length(self):
        assert _thermo(_PopulatedRawFile()).length == 3.0

    def test_acquisition_params_does_not_report_a_scanless_file_as_a_fault(self):
        # acquisition_params is read BEFORE length (SampleFileProps declares
        # it earlier and _get_sample_file_props walks the fields in order), so
        # its generic `except Exception` used to log a traceback at WARNING -
        # the level the monitoring sink subscribes to - for every empty .raw,
        # before length could fail it cleanly as data.
        records = []
        sink_id = runtime.logger.add(
            lambda message: records.append(message.record), level="TRACE"
        )
        try:
            assert _thermo(_ScanlessRawFile()).acquisition_params == {}
        finally:
            runtime.logger.remove(sink_id)

        assert [r for r in records if r["level"].name in ("WARNING", "ERROR")] == []
        assert any(r["exception"] is not None for r in records) is False


class TestTofEmptyAcquisition:
    def test_all_zero_buf_times_raise_empty_acquisition(self):
        # The recorder created the file but never wrote a scan, so every buf
        # timestamp is zero and there is no last non-zero index to slice on.
        with pytest.raises(EmptyAcquisitionError):
            _tof([[0.0, 0.0], [0.0, 0.0]]).interval

    def test_no_buf_rows_raise_empty_acquisition(self):
        with pytest.raises(EmptyAcquisitionError):
            _tof(np.zeros((0, 2))).length

    def test_a_single_recorded_scan_is_not_measurable(self):
        # One scan gives np.diff nothing to average. Before this guard the
        # mean of an empty slice returned NaN, which pydantic accepts and the
        # converter then stored as the sample's interval and length - only to
        # fail later at JSON render, where allow_nan is False.
        with pytest.raises(EmptyAcquisitionError, match="only one scan"):
            _tof([[3.0, 0.0]]).interval
        with pytest.raises(EmptyAcquisitionError, match="only one scan"):
            _tof([[3.0, 0.0]]).length

    def test_an_unfilled_last_write_is_not_an_empty_acquisition(self):
        # The regression this replaces: BufTimes is pre-allocated, so an
        # aborted run leaves whole trailing rows of zeros. Reading only the
        # last row made a file with real scans look empty, and because that
        # is classified routine it was discarded with no traceback to show
        # the misdiagnosis. The recorded scans are 1.0 and 2.0.
        assert _tof([[1.0, 2.0], [0.0, 0.0]]).length == pytest.approx(2.0)
        assert _tof([[1.0, 2.0], [0.0, 0.0]]).interval == pytest.approx(1.0)

    def test_an_aborted_run_keeps_every_scan_before_the_abort(self):
        # Five scans at 1 s spacing, then two unwritten write blocks.
        buf_times = [[1.0, 2.0, 3.0], [4.0, 5.0, 0.0], [0.0, 0.0, 0.0]]
        assert _tof(buf_times).interval == pytest.approx(1.0)
        assert _tof(buf_times).length == pytest.approx(5.0)  # (5-1) + 1

    def test_populated_file_still_reports_its_interval(self):
        assert _tof([[0.0, 1.0], [2.0, 3.0]]).interval == pytest.approx(1.0)

    def test_populated_file_still_reports_its_length(self):
        # 3.0 - 0.0, plus the mean inter-scan interval of 1.0.
        assert _tof([[0.0, 1.0], [2.0, 3.0]]).length == pytest.approx(4.0)


class TestRoutineFailureReporting:
    def test_empty_acquisition_is_routine(self):
        # This is what keeps the traceback, and the monitoring event, away.
        assert is_routine_file_failure(EmptyAcquisitionError(EMPTY_MESSAGE))

    def test_duplicate_upload_stays_routine(self):
        assert is_routine_file_failure(FileExistsError("already ingested"))

    def test_a_real_fault_is_not_routine(self):
        # An unreadable or corrupt file is still worth a traceback: it can
        # point at a transfer or storage problem rather than the data.
        assert not is_routine_file_failure(OSError("I/O error: Invalid argument"))

    def test_message_reaches_the_user_as_written(self):
        # describe_exception prefixes the class name only for cryptic builtin
        # messages; this one already reads as a sentence.
        assert describe_exception(EmptyAcquisitionError(EMPTY_MESSAGE)) == EMPTY_MESSAGE


class TestRealScanSelection:
    """The reader's own scan selection, not a stand-in for it.

    ``RawProcessor.length`` catches ``NoScansFoundError``, so the guard is
    only as good as the reader actually raising it. On an empty scan list the
    mask comprehensions are empty and numpy infers float64, which made
    ``mask &=`` raise ``TypeError`` - straight past the except clause and into
    monitoring as an unexpected fault. A stubbed reader cannot see this.
    """

    @staticmethod
    def _scanless_backend():
        from mascope_thermo.backend import OpenTFRawBackend

        backend = OpenTFRawBackend.__new__(OpenTFRawBackend)
        backend._raw = None
        backend._scans = []  # a file the reader opened but found no scans in
        return backend

    def test_ms_type_filter_on_an_empty_file_raises_the_reader_error(self):
        with pytest.raises(NoScansFoundError):
            self._scanless_backend()._selected(None, None, None, "Ms")

    def test_polarity_filter_on_an_empty_file_raises_the_reader_error(self):
        with pytest.raises(NoScansFoundError):
            self._scanless_backend()._selected("+", None, None, None)

    def test_unfiltered_selection_on_an_empty_file_raises_the_reader_error(self):
        with pytest.raises(NoScansFoundError):
            self._scanless_backend()._selected(None, None, None, None)

    def test_the_processor_guard_therefore_catches_it(self):
        # End to end over the real reader: the error the processor catches is
        # the error the reader raises.
        backend = self._scanless_backend()
        processor = _thermo(backend)
        with pytest.raises(EmptyAcquisitionError):
            processor.length


class _ScanlessReaderHandle:
    """Every scan-derived reader call on a file that recorded nothing."""

    def num_scans(self):
        return 0

    def scan_times(self, ms_type=None):  # noqa: ARG002
        raise NoScansFoundError(
            "No scans found matching the specified filters: polarity='None', "
            "time_range=(None, None), ms_type='None'"
        )

    def acquisition_parameters(self):
        raise NoScansFoundError("No scans to sample.")

    def created(self):
        from datetime import datetime, timezone

        return datetime(2026, 8, 19, 7, 42, tzinfo=timezone.utc)


class TestPropertyExtractionReportsNoFault:
    """Extraction runs several properties before the one that fails.

    ``acquisition_params`` is read before ``length``, and its generic handler
    logged the reader's failure at WARNING with a traceback - so the file was
    reported as a fault before ``length`` could fail it as data. The property
    that ultimately raises is therefore not the whole story; nothing along the
    way may reach the monitoring threshold either.
    """

    @staticmethod
    def _extract():
        processor = _thermo(_ScanlessReaderHandle())
        processor.file_to_process = "ORBI-1_empty.raw"

        records = []
        sink_id = runtime.logger.add(
            lambda message: records.append(message.record), level="TRACE"
        )
        try:
            with pytest.raises(EmptyAcquisitionError):
                processor._get_sample_file_props()
        finally:
            runtime.logger.remove(sink_id)
        return records

    def test_extraction_fails_as_an_empty_acquisition(self):
        self._extract()  # the pytest.raises inside is the assertion

    def test_no_property_along_the_way_reports_a_fault(self):
        from mascope_runtime.logging import _SENTRY_LEVELS

        records = self._extract()
        offenders = [
            (r["level"].name, r["message"])
            for r in records
            if r["level"].name in _SENTRY_LEVELS
        ]
        assert offenders == []

    def test_acquisition_params_is_read_before_length(self):
        # Pins the ordering the test above depends on: if length ever moved
        # first, that test would pass without exercising the earlier property.
        from mascope_backend.file_converter.schema import SampleFileProps

        fields = list(SampleFileProps.model_fields)
        assert fields.index("acquisition_params") < fields.index("length")
