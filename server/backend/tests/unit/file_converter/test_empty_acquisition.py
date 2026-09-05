"""An acquisition that recorded no scans fails as data, not as a fault.

A raw file with too few scans - an aborted run, or one that wrote a file
before recording anything - has nothing to ingest, so the file still fails and
lands in ``failed_files``. What it must not do is surface as an unexpected
exception: the Thermo path raised the reader's ``NoScansFoundError`` and the
TOF path an ``IndexError`` off the end of an empty array, both reaching error
monitoring with a traceback as if Mascope itself had broken.

A file that recorded only fragmentation scans is the same kind of outcome
reached a different way: every property the extraction reads is answerable
from the MS2 scans, so it used to survive extraction and fail deep inside the
instrument-function fit - as a traceback, on a message naming a scan filter.

Two things this must not over-reach on, both covered below. A pre-allocated
BufTimes array means an aborted TOF run ends in unwritten rows of zeros, so
"the last write holds no scans" is not "the file holds no scans" - the scans
before the abort are real and the file's length is measurable from them. And
a run that recorded exactly one scan has no inter-scan spacing to average, so
it must be refused rather than ingested with a NaN interval.

Both reader backends are exercised through their own scan selection rather
than through a stand-in for it, because the guard is only as good as the error
the reader actually raises - and the two backends reach a scanless file by
different code.
"""

from contextlib import contextmanager
from queue import Queue
from threading import Event

import numpy as np
import pytest

from mascope_backend.file_converter.errors import (
    EMPTY_ACQUISITION_MESSAGE,
    NO_MS1_SCANS_MESSAGE,
    SINGLE_SCAN_MESSAGE,
    UNUSABLE_SCAN_TIMES_MESSAGE,
    EmptyAcquisitionError,
    describe_exception,
    is_routine_file_failure,
)
from mascope_backend.runtime import runtime
from mascope_thermo.processor import RawProcessor
from mascope_thermo.thermo import NoScansFoundError
from mascope_tofwerk.processor import H5Processor
from mascope_tofwerk.tofwerk import NoScansRecordedError, recorded_scan_times


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


def _captured(work):
    """Run ``work`` with every log record of the runtime logger captured."""
    records = []
    sink_id = runtime.logger.add(
        lambda message: records.append(message.record), level="TRACE"
    )
    try:
        work()
    finally:
        runtime.logger.remove(sink_id)
    return records


class _ScanlessRawFile:
    """Reader stand-in for a .raw whose scan list is empty.

    Every scan-derived call reports the same thing the real readers do; the
    calls that do not touch scans still answer, so a whole property walk can
    be driven over it.
    """

    def num_scans(self):
        return 0

    def scan_indices(self, polarity=None, t_min=None, t_max=None, ms_type=None):  # noqa: ARG002
        raise NoScansFoundError(
            "No scans found matching the specified filters: polarity='None', "
            f"time_range=(None, None), ms_type='{ms_type}'"
        )

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

    def created(self):
        from datetime import datetime, timezone

        return datetime(2026, 8, 19, 7, 42, tzinfo=timezone.utc)


class _PopulatedRawFile:
    def scan_times(self, ms_type=None):  # noqa: ARG002
        return np.array([0.0, 1.5, 3.0])


class TestThermoEmptyAcquisition:
    def test_length_reports_the_reader_error_unchanged(self):
        # `length` applies no filters, so the reader finding nothing can only
        # mean the file holds no scans at all. The property says what the
        # reader said; naming it as an empty acquisition is the extraction's
        # job, so that no property has to carry its own arm.
        with pytest.raises(NoScansFoundError):
            _thermo(_ScanlessRawFile()).length

    def test_extraction_names_it_an_empty_acquisition(self):
        with pytest.raises(EmptyAcquisitionError, match="contains no scans"):
            _thermo(_ScanlessRawFile())._get_sample_file_props()

    def test_the_reader_error_is_kept_as_the_cause(self):
        try:
            _thermo(_ScanlessRawFile())._get_sample_file_props()
        except EmptyAcquisitionError as e:
            assert isinstance(e.__cause__, NoScansFoundError)
        else:
            pytest.fail("expected EmptyAcquisitionError")

    def test_populated_file_still_reports_its_length(self):
        assert _thermo(_PopulatedRawFile()).length == 3.0

    def test_acquisition_params_does_not_report_a_scanless_file_as_a_fault(self):
        # acquisition_params is read BEFORE the properties that fail the file
        # (SampleFileProps declares it earlier and _get_sample_file_props walks
        # the fields in order), so its generic `except Exception` used to log a
        # traceback at WARNING - the level the monitoring sink subscribes to -
        # for every empty .raw, before the file could be failed as data.
        processor = _thermo(_ScanlessRawFile())
        records = _captured(lambda: processor.acquisition_params)

        assert processor.acquisition_params == {}
        assert [r for r in records if r["level"].name in ("WARNING", "ERROR")] == []
        assert any(r["exception"] is not None for r in records) is False


class _Ms2OnlyRawFile:
    """Reader stand-in for a .raw whose scans are all fragmentation scans.

    Unlike the scanless stand-in, every call the property walk makes answers -
    which is the point: an MS2-only file passes extraction, so the walk cannot
    be what catches it.
    """

    def scan_indices(self, polarity=None, t_min=None, t_max=None, ms_type=None):  # noqa: ARG002
        if ms_type == "Ms":
            raise NoScansFoundError(
                "No scans found matching the specified filters: polarity='None', "
                "time_range=(None, None), ms_type='Ms'"
            )
        return [1, 2, 3]

    def num_scans(self):
        return 3

    def scan_times(self, ms_type=None):  # noqa: ARG002
        return np.array([0.0, 1.5, 3.0])

    def mass_range(self):
        return (40.0, 160.0)

    def acquisition_parameters(self, max_scans=None):  # noqa: ARG002
        # Already guarded in the processor: the real backend selects
        # ms_type="Ms" here, so an MS2-only file raises exactly as a scanless
        # one does and the property degrades to {}.
        raise NoScansFoundError(
            "No scans found matching the specified filters: polarity='None', "
            "time_range=(None, None), ms_type='Ms'"
        )

    def created(self):
        from datetime import datetime, timezone

        return datetime(2026, 9, 4, 16, 12, tzinfo=timezone.utc)


class TestThermoMs1LessAcquisition:
    """A file whose scans are all MS2 fails as data, before the fit.

    Peak detection and the instrument-function fit both read MS1, so such a
    file has nothing for them to run on. It used to reach the fit and die
    there on the reader's own error, which reads as a fault in Mascope: a
    traceback at the level error monitoring subscribes to, and a notification
    quoting a scan filter at the operator.
    """

    def test_extraction_names_the_missing_ms1_scans(self):
        with pytest.raises(EmptyAcquisitionError, match="contains no MS1 scans"):
            _thermo(_Ms2OnlyRawFile())._get_sample_file_props()

    def test_it_is_reported_as_data_not_as_a_fault(self):
        assert is_routine_file_failure(EmptyAcquisitionError(NO_MS1_SCANS_MESSAGE))

    def test_nothing_along_the_way_reports_a_fault(self):
        from mascope_runtime.logging import _SENTRY_LEVELS

        processor = _thermo(_Ms2OnlyRawFile())

        def _walk():
            with pytest.raises(EmptyAcquisitionError):
                processor._get_sample_file_props()

        records = _captured(_walk)
        assert [r for r in records if r["level"].name in _SENTRY_LEVELS] == []

    def test_a_scanless_file_is_still_named_the_empty_acquisition_it_is(self):
        # The guard asks for the unfiltered selection first for exactly this:
        # a file with no scans at all has no MS1 scans either, and would
        # otherwise be reported as the narrower condition.
        with pytest.raises(EmptyAcquisitionError, match="contains no scans"):
            _thermo(_ScanlessRawFile())._get_sample_file_props()

    def test_a_file_with_ms1_scans_is_not_refused(self):
        # The guard must not stand between an ordinary acquisition and its
        # properties; reaching the walk at all is what this pins.
        class _WithMs1(_Ms2OnlyRawFile):
            def scan_indices(self, polarity=None, t_min=None, t_max=None, ms_type=None):  # noqa: ARG002
                return [1, 2, 3]

        processor = _thermo(_WithMs1())
        assert processor._records_ms1_scans is True

    def test_the_message_reaches_the_user_as_written(self):
        assert describe_exception(EmptyAcquisitionError(NO_MS1_SCANS_MESSAGE)) == (
            NO_MS1_SCANS_MESSAGE
        )


class TestTofEmptyAcquisition:
    def test_all_zero_buf_times_raise_empty_acquisition(self):
        # The recorder created the file but never wrote a scan, so every buf
        # timestamp is zero and there is no last recorded index to slice on.
        with pytest.raises(EmptyAcquisitionError, match="contains no scans"):
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

    def test_an_unwritten_slot_before_the_last_scan_is_refused(self):
        # The other route to a NaN interval: a writer that pre-fills with NaN
        # rather than zero leaves one inside the recorded span, and `!= 0` is
        # true of a NaN, so it survives trimming. np.mean would then return
        # NaN for a file that has real scans on both sides of the hole.
        with pytest.raises(EmptyAcquisitionError, match="timestamps are incomplete"):
            _tof([[1.0, np.nan], [3.0, 0.0]]).interval

    def test_a_nan_tail_is_trimmed_like_a_zero_tail(self):
        # A NaN tail is an unwritten tail, not a hole: the scans before it are
        # real and the file ingests with the length it has.
        assert _tof([[1.0, 2.0], [np.nan, np.nan]]).interval == pytest.approx(1.0)
        assert _tof([[1.0, 2.0], [np.nan, np.nan]]).length == pytest.approx(2.0)

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

    def test_buf_times_are_read_once_for_both_properties(self):
        # interval and length are separate schema fields, so the props
        # collector asks for both. Without the per-file cache that is two
        # opens of the h5 file and two full reads of BufTimes for one answer.
        processor = _tof([[0.0, 1.0], [2.0, 3.0]])
        opened = []
        inner = processor._file_context_manager

        @contextmanager
        def _counting(file_path):
            opened.append(file_path)
            with inner(file_path) as handle:
                yield handle

        processor._file_context_manager = _counting

        assert processor.interval == pytest.approx(1.0)
        assert processor.length == pytest.approx(4.0)
        assert len(opened) == 1


class TestRoutineFailureReporting:
    def test_empty_acquisition_is_routine(self):
        # This is what keeps the traceback, and the monitoring event, away.
        assert is_routine_file_failure(EmptyAcquisitionError(EMPTY_ACQUISITION_MESSAGE))

    def test_duplicate_upload_stays_routine(self):
        assert is_routine_file_failure(FileExistsError("already ingested"))

    def test_a_real_fault_is_not_routine(self):
        # An unreadable or corrupt file is still worth a traceback: it can
        # point at a transfer or storage problem rather than the data.
        assert not is_routine_file_failure(OSError("I/O error: Invalid argument"))

    @pytest.mark.parametrize(
        "message",
        [EMPTY_ACQUISITION_MESSAGE, SINGLE_SCAN_MESSAGE, UNUSABLE_SCAN_TIMES_MESSAGE],
    )
    def test_message_reaches_the_user_as_written(self, message):
        # describe_exception prefixes the class name only for cryptic builtin
        # messages; these already read as sentences.
        assert describe_exception(EmptyAcquisitionError(message)) == message

    def test_both_readers_say_the_same_thing_about_a_scanless_file(self):
        # The wording is user-facing, so the two paths must not drift apart.
        with pytest.raises(EmptyAcquisitionError) as thermo:
            _thermo(_ScanlessRawFile())._get_sample_file_props()
        with pytest.raises(EmptyAcquisitionError) as tof:
            _tof([[0.0, 0.0]]).interval

        assert str(thermo.value) == str(tof.value) == EMPTY_ACQUISITION_MESSAGE


class _ScanlessOpenTFRaw:
    """The ``opentfraw.RawFile`` of a .raw the reader opened and found empty."""

    #: Read by OpenTFRawBackend.num_scans(); the run header claims none.
    num_scans = 0
    #: Read by OpenTFRawBackend.created(): an Xcalibur audit timestamp.
    created = 1755589320.0

    def iter_scans(self):
        return iter(())


class TestOpenTFRawScanSelection:
    """The OpenTFRaw reader's own scan selection, not a stand-in for it.

    The processor is built on the reader raising ``NoScansFoundError``, so the
    guard is only as good as that actually happening. On an empty scan list the
    mask comprehensions are empty and numpy infers float64, which made
    ``mask &=`` raise ``TypeError`` - straight past the arm the guard is built
    on. A stubbed reader cannot see this.
    """

    @staticmethod
    def _backend():
        from mascope_thermo.backend import OpenTFRawBackend

        backend = OpenTFRawBackend.__new__(OpenTFRawBackend)
        backend._raw = _ScanlessOpenTFRaw()
        backend._scans = []  # a file the reader opened but found no scans in
        return backend

    def test_ms_type_filter_on_an_empty_file_raises_the_reader_error(self):
        with pytest.raises(NoScansFoundError):
            self._backend()._selected(None, None, None, "Ms")

    def test_polarity_filter_on_an_empty_file_raises_the_reader_error(self):
        with pytest.raises(NoScansFoundError):
            self._backend()._selected("+", None, None, None)

    def test_unfiltered_selection_on_an_empty_file_raises_the_reader_error(self):
        with pytest.raises(NoScansFoundError):
            self._backend()._selected(None, None, None, None)

    def test_the_mass_range_of_an_empty_file_raises_the_reader_error(self):
        # min()/max() over no scans would raise a bare ValueError instead.
        with pytest.raises(NoScansFoundError):
            self._backend().mass_range()

    def test_the_whole_extraction_therefore_fails_as_data(self):
        # End to end over the real reader: every property that asks it for
        # scans reports the error the extraction is built to recognise.
        processor = _thermo(self._backend())
        with pytest.raises(EmptyAcquisitionError):
            processor._get_sample_file_props()


class _ScanlessThermoRawFile:
    """The ``RawFileReaderAdapter`` of a .raw whose run header counts no scans.

    Reaching ``ScanSelector``'s masks needs nothing from pythonnet: with a
    spectra count of zero the per-scan lookups below are never called.
    """

    class _RunHeader:
        SpectraCount = 0
        StartTime = 0.0
        EndTime = 0.0

    class _CreationDate:
        """Stands in for the .NET DateTime of the Xcalibur audit trail."""

        Year, Month, Day, Hour, Minute, Second = 2026, 8, 19, 7, 42, 0

    RunHeaderEx = _RunHeader()
    CreationDate = _CreationDate()

    def GetFilterForScanNumber(self, index):  # noqa: N802
        raise AssertionError(f"no scan {index} to read a filter for")

    def GetScanStatsForScanNumber(self, index):  # noqa: N802
        raise AssertionError(f"no scan {index} to read stats for")


class TestThermoScanSelection:
    """The Thermo reader's own scan selection, the twin of the class above.

    ``ScanSelector`` builds its masks the same way ``OpenTFRawBackend`` does
    and had the same empty-comprehension float64 defect. Covering only one
    backend is what let it survive the first time: the two reach a scanless
    file by different code, and the processor cannot tell them apart.
    """

    @staticmethod
    def _selector(**kwargs):
        from mascope_thermo.thermo import ScanSelector

        return ScanSelector(_ScanlessThermoRawFile(), **kwargs)

    @staticmethod
    def _backend():
        from mascope_thermo.backend import ThermoBackend

        backend = ThermoBackend.__new__(ThermoBackend)
        backend._raw = _ScanlessThermoRawFile()
        return backend

    def test_ms_type_filter_on_an_empty_file_raises_the_reader_error(self):
        with pytest.raises(NoScansFoundError):
            self._selector(ms_type="Ms").scan_indices_1based

    def test_polarity_filter_on_an_empty_file_raises_the_reader_error(self):
        with pytest.raises(NoScansFoundError):
            self._selector(polarity="+", ms_type=None).scan_indices_1based

    def test_time_filter_on_an_empty_file_raises_the_reader_error(self):
        with pytest.raises(NoScansFoundError):
            self._selector(t_min=0.0, t_max=60.0, ms_type=None).scan_indices_1based

    def test_unfiltered_selection_on_an_empty_file_raises_the_reader_error(self):
        with pytest.raises(NoScansFoundError):
            self._selector(ms_type=None).scan_indices_1based

    def test_acquisition_parameters_raise_the_reader_error(self):
        # The path that reached monitoring: acquisition_parameters() always
        # selects ms_type="Ms", and it is read before the property that fails
        # the file, so a TypeError here was logged with a traceback at WARNING.
        with pytest.raises(NoScansFoundError):
            self._backend().acquisition_parameters()

    def test_scan_times_raise_the_reader_error(self):
        with pytest.raises(NoScansFoundError):
            self._backend().scan_times(None, None, None, None)

    def test_acquisition_params_stays_below_the_monitoring_threshold(self):
        # Same assertion as for the OpenTFRaw path, over the real selector.
        from mascope_runtime.logging import _SENTRY_LEVELS

        processor = _thermo(self._backend())
        records = _captured(lambda: processor.acquisition_params)

        assert processor.acquisition_params == {}
        assert [r for r in records if r["level"].name in _SENTRY_LEVELS] == []


class TestRecordedScanTimes:
    """The TOF reader's trim, which every h5 accessor shares."""

    def test_no_written_entry_raises_the_reader_error(self):
        with pytest.raises(NoScansRecordedError):
            recorded_scan_times(np.zeros((2, 3)))

    def test_the_unwritten_tail_is_trimmed(self):
        recorded = recorded_scan_times(np.array([[1.0, 2.0, 0.0], [0.0, 0.0, 0.0]]))
        assert recorded.tolist() == [1.0, 2.0]

    def test_a_nan_tail_is_trimmed_too(self):
        recorded = recorded_scan_times(np.array([[1.0, 2.0], [np.nan, np.nan]]))
        assert recorded.tolist() == [1.0, 2.0]

    def test_the_first_scan_at_zero_is_kept(self):
        # Buf times are relative to acquisition start, so scan one reads 0.0.
        recorded = recorded_scan_times(np.array([[0.0, 1.0, 0.0]]))
        assert recorded.tolist() == [0.0, 1.0]


class TestPropertyExtractionReportsNoFault:
    """Extraction runs several properties before the one that fails.

    ``acquisition_params`` is read before the properties that ask the reader
    for scans, and its generic handler logged the reader's failure at WARNING
    with a traceback - so the file was reported as a fault before it could be
    failed as data. The property that ultimately raises is therefore not the
    whole story; nothing along the way may reach the monitoring threshold
    either.
    """

    @staticmethod
    def _run(handle):
        processor = _thermo(handle)

        def _walk():
            with pytest.raises(EmptyAcquisitionError):
                processor._get_sample_file_props()

        return _captured(_walk)

    @pytest.mark.parametrize(
        "handle",
        [
            pytest.param(_ScanlessRawFile(), id="stubbed-reader"),
            pytest.param(TestOpenTFRawScanSelection._backend(), id="opentfraw"),
            pytest.param(TestThermoScanSelection._backend(), id="thermo"),
        ],
    )
    def test_no_property_along_the_way_reports_a_fault(self, handle):
        from mascope_runtime.logging import _SENTRY_LEVELS

        records = self._run(handle)
        offenders = [
            (r["level"].name, r["message"])
            for r in records
            if r["level"].name in _SENTRY_LEVELS
        ]
        assert offenders == []

    def test_the_ordering_of_the_schema_fields_is_not_load_bearing(self):
        # The extraction names the scanless case for the walk as a whole, so
        # it no longer matters which property gets to the reader first. This
        # pins that the fields the guard covers really are read through the
        # same walk.
        from mascope_backend.file_converter.schema import SampleFileProps

        fields = set(SampleFileProps.model_fields)
        assert {"acquisition_params", "interval", "length", "range"} <= fields
