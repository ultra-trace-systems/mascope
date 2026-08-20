"""An acquisition that recorded no scans fails as data, not as a fault.

A raw file with zero scans - an aborted run, or one that wrote a file before
recording anything - has nothing to ingest, so the file still fails and lands
in ``failed_files``. What it must not do is surface as an unexpected
exception: the Thermo path raised the reader's ``NoScansFoundError`` and the
TOF path an ``IndexError`` off the end of an empty array, both reaching error
monitoring with a traceback as if Mascope itself had broken.
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


class TestTofEmptyAcquisition:
    def test_all_zero_buf_times_raise_empty_acquisition(self):
        # The recorder created the file but never wrote a scan, so every buf
        # timestamp is zero and there is no last non-zero index to slice on.
        with pytest.raises(EmptyAcquisitionError):
            _tof([[0.0, 0.0], [0.0, 0.0]]).interval

    def test_no_buf_rows_raise_empty_acquisition(self):
        with pytest.raises(EmptyAcquisitionError):
            _tof(np.zeros((0, 2))).length

    def test_last_write_of_only_zero_bufs_raises_empty_acquisition(self):
        with pytest.raises(EmptyAcquisitionError):
            _tof([[1.0, 2.0], [0.0, 0.0]]).length

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
