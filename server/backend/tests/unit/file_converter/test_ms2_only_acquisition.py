"""An acquisition of fragmentation scans alone is stored, not refused.

Peak detection and the instrument-function fit both read MS1, so a file with
no MS1 scan has nothing for either to run on. It used to be refused for that:
first as a traceback from inside the fit, then as a clean "contains no MS1
scans" failure, and either way its fragmentation scans - real data, readable
by every MS2 route - never reached the filestore.

Such a file is now stored the way a blank measurement is: an empty peak
timeseries and a sample file record with no instrument config. What sets it
apart from a blank is recorded with it, in the scan-stream census of its
props, which holds no MS1 stream.

Three things are pinned here. The extraction answers for such a file under the
real reader. The converter then takes the path that skips the fit and peak
detection - and only for such a file, so an ordinary acquisition is fitted as
before and a blank keeps its own path. And the empty store is written on the
axis of every scan, since there is no MS1 axis to write it on.

No committed file records a fragmentation scan, so the real-reader half
relabels the scans of the committed MS1 file at the OpenTFRaw backend's
scan-list seam, where every selection and the census read from.
"""

from contextlib import contextmanager
from pathlib import Path
from queue import Queue
from threading import Event
from types import SimpleNamespace

import numpy as np
import pytest
from test_utils import captured_logs

import mascope_backend.file_converter.base_processor as base_processor
import mascope_signal.peak as m_peak
from mascope_backend.file_converter.errors import EmptyAcquisitionError
from mascope_signal.instrument_func.fit import InsufficientPeaksError
from mascope_thermo.backend import OpenTFRawBackend
from mascope_thermo.processor import RawProcessor
from mascope_thermo.thermo import NoScansFoundError


# Committed sample (ships in the repo; only the small MS1 files are tracked).
_REPO_ROOT = Path(__file__).resolve().parents[5]
MS1_FILE = (
    _REPO_ROOT / "libraries/thermo/tests/test_files/KORBI2_AMB_POS_20260109174345.raw"
)

#: What a targeted MS2 scan's filter reads like, in place of ``Full ms``.
_MS2 = " Full ms2 137.0960@hcd20.00 ["


def _processor(file_to_process="ORBI-1_ms2.raw"):
    processor = RawProcessor(
        socket_client=None, file_queue=Queue(), shutdown_event=Event()
    )
    processor.file_to_process = str(file_to_process)
    return processor


def _with_handle(processor, file_handle):
    """Point the processor's file context at a stand-in reader handle."""

    @contextmanager
    def _context(_file_path):
        yield file_handle

    processor._file_context_manager = _context
    return processor


class _ReaderOf:
    """Reader stand-in that answers only whether it holds MS1 scans."""

    def __init__(self, *, ms1: bool, scans: int = 3):
        self._ms1 = ms1
        self._scans = scans

    def scan_indices(self, polarity=None, t_min=None, t_max=None, ms_type=None):  # noqa: ARG002
        if not self._scans or (ms_type == "Ms" and not self._ms1):
            raise NoScansFoundError(
                "No scans found matching the specified filters: polarity='None', "
                f"time_range=(None, None), ms_type='{ms_type}'"
            )
        return list(range(1, self._scans + 1))

    def num_scans(self):
        return self._scans


class TestWhetherAFileHoldsSurveyScans:
    def test_a_file_of_fragmentation_scans_holds_none(self):
        processor = _with_handle(_processor(), _ReaderOf(ms1=False))

        assert processor._records_survey_scans is False

    def test_an_ordinary_file_holds_them(self):
        processor = _with_handle(_processor(), _ReaderOf(ms1=True))

        assert processor._records_survey_scans is True

    def test_a_file_with_no_scans_is_neither(self):
        """An empty file must not be stored as a fragmentation-only one."""
        processor = _with_handle(_processor(), _ReaderOf(ms1=False, scans=0))

        with pytest.raises(NoScansFoundError):
            processor._records_survey_scans


@pytest.fixture
def fragmentation_scans_only(monkeypatch):
    """Make the committed MS1 file read as an acquisition of MS2 scans alone.

    The MS order is relabelled where scan selection reads it, and the filter
    where the census reads it, so both say the same thing about every scan.
    """
    if not MS1_FILE.exists():
        pytest.skip(f"sample file missing: {MS1_FILE}")
    monkeypatch.setenv("MASCOPE_THERMO_BACKEND", "opentfraw")
    monkeypatch.delenv("MASCOPE_THERMO_DLL_DIR", raising=False)
    all_scans = OpenTFRawBackend._all_scans

    def relabelled(self):
        return [
            {
                **scan,
                "ms_level": 2,
                "filter_string": scan["filter_string"].replace(" Full ms [", _MS2),
            }
            for scan in all_scans(self)
        ]

    monkeypatch.setattr(OpenTFRawBackend, "_all_scans", relabelled)
    return _processor(MS1_FILE)


class TestExtractionUnderTheRealReader:
    def test_the_properties_are_extracted(self, fragmentation_scans_only):
        props = fragmentation_scans_only._get_sample_file_props()

        assert props.polarity == "+"
        assert props.length > 0
        assert props.interval > 0
        assert props.range == [40.0, 500.0]

    def test_the_census_says_what_the_file_holds(self, fragmentation_scans_only):
        """What tells it from a blank afterwards: streams, and none of them MS1."""
        props = fragmentation_scans_only._get_sample_file_props()

        orders = [stream["signature"]["ms_order"] for stream in props.scan_streams]
        assert orders == [2]

    def test_it_holds_no_survey_scans(self, fragmentation_scans_only):
        assert fragmentation_scans_only._records_survey_scans is False

    def test_nothing_on_the_way_reports_a_fault(self, fragmentation_scans_only):
        """Its trailers are not sampled for MS1 parameters, and that is routine.

        ``acquisition_params`` asks for MS1 scans, finds none, and degrades to
        an empty summary - which must not be logged at a level error
        monitoring subscribes to, for a file that is being imported.
        """
        from mascope_runtime.logging import _SENTRY_LEVELS

        with captured_logs() as records:
            props = fragmentation_scans_only._get_sample_file_props()

        assert props.acquisition_params == {}
        assert [r for r in records if r["level"].name in _SENTRY_LEVELS] == []

    def test_it_is_not_an_empty_acquisition(self, fragmentation_scans_only):
        try:
            fragmentation_scans_only._get_sample_file_props()
        except EmptyAcquisitionError as e:  # pragma: no cover - the regression
            pytest.fail(f"refused as an empty acquisition: {e}")


def _routed(monkeypatch, *, blank=False, survey_scans=True, fit=None):
    """Run ``_process_file`` with everything around its routing recorded.

    :param blank: What the processor's blank classification answers.
    :param survey_scans: Whether the file holds scans to detect peaks in.
    :param fit: An exception the instrument-function fit raises, if any.
    :return: The calls made, in order.
    """
    calls = []

    class _Routed(RawProcessor):
        _is_blank_measurement = property(lambda self: blank)
        _records_survey_scans = property(lambda self: survey_scans)

    processor = _Routed(socket_client=None, file_queue=Queue(), shutdown_event=Event())
    processor.file_to_process = "ORBI-1_ms2.raw"

    def _fit(_props):
        calls.append("fit")
        if fit is not None:
            raise fit
        return "ifunc-001", "peak shape", "resolution function"

    monkeypatch.setattr(processor, "_create_filestore_directory", lambda *_: None)
    monkeypatch.setattr(processor, "_emit_progress_notification", lambda *_: None)
    monkeypatch.setattr(processor, "_create_instrument_config", _fit)
    monkeypatch.setattr(
        processor, "_compute_peaks", lambda *_: calls.append("peak detection")
    )
    monkeypatch.setattr(
        processor,
        "_create_db_record",
        lambda _props, instrument_function_id: calls.append(
            ("record", instrument_function_id)
        ),
    )
    monkeypatch.setattr(
        base_processor,
        "write_empty_peak_timeseries",
        lambda _filename, **kwargs: calls.append(("empty store", kwargs)),
    )

    processor._process_file(SimpleNamespace(filename="ORBI-1_ms2"), "ORBI-1_ms2.raw")
    return calls


class TestHowAFileIsStored:
    def test_one_with_no_survey_scans_skips_the_fit_and_peak_detection(
        self, monkeypatch
    ):
        calls = _routed(monkeypatch, survey_scans=False)

        assert "fit" not in calls
        assert "peak detection" not in calls

    def test_its_empty_store_is_written_on_the_axis_of_every_scan(self, monkeypatch):
        """There is no MS1 axis to write it on, so the default would raise."""
        calls = _routed(monkeypatch, survey_scans=False)

        assert ("empty store", {"scan_type": None}) in calls

    def test_its_record_carries_no_instrument_config(self, monkeypatch):
        """Which is what every reader of a file's peaks takes to mean none."""
        calls = _routed(monkeypatch, survey_scans=False)

        assert calls[-1] == ("record", None)

    def test_an_ordinary_file_is_fitted_and_detected_as_before(self, monkeypatch):
        calls = _routed(monkeypatch)

        assert calls == ["fit", "peak detection", ("record", "ifunc-001")]

    def test_a_blank_keeps_its_own_path(self, monkeypatch):
        """A blank's store keeps the default axis: its scans are the MS1 kind."""
        calls = _routed(monkeypatch, blank=True)

        assert calls == [("empty store", {}), ("record", None)]

    def test_a_blank_is_classified_before_the_survey_scans_are_asked_about(
        self, monkeypatch
    ):
        """Blank first: a TOF blank has survey scans, and is still a blank."""
        calls = _routed(monkeypatch, blank=True, survey_scans=False)

        assert ("empty store", {}) in calls
        assert ("empty store", {"scan_type": None}) not in calls

    def test_a_file_too_weak_to_fit_is_still_stored_as_a_blank(self, monkeypatch):
        calls = _routed(monkeypatch, fit=InsufficientPeaksError("too few peaks"))

        assert calls == ["fit", ("empty store", {}), ("record", None)]


@pytest.fixture
def empty_store(monkeypatch):
    """Write an empty peak store, recording what was asked for and written."""
    seen = {}

    def fake_scan_timestamps(filename, **kwargs):
        seen["asked"] = kwargs
        return np.array([35.1, 37.7, 40.3], dtype=np.float64)

    async def fake_write_peaks(dataset, filename, overwrite):
        seen["written"] = (dataset, filename, overwrite)

    monkeypatch.setattr(m_peak.m_compute, "get_scan_timestamps", fake_scan_timestamps)
    monkeypatch.setattr(m_peak.m_io, "write_peaks", fake_write_peaks)
    return seen


class TestTheEmptyPeakStore:
    def test_it_is_written_on_the_ms1_axis_unless_told_otherwise(self, empty_store):
        m_peak.write_empty_peak_timeseries("ORBI-1_blank")

        assert empty_store["asked"] == {"scan_type": "Ms"}

    def test_it_is_written_on_the_axis_it_is_told_to(self, empty_store):
        m_peak.write_empty_peak_timeseries("ORBI-1_ms2", scan_type=None)

        assert empty_store["asked"] == {"scan_type": None}

    def test_it_holds_that_axis_and_no_peaks(self, empty_store):
        m_peak.write_empty_peak_timeseries("ORBI-1_ms2", scan_type=None)

        dataset, filename, overwrite = empty_store["written"]
        assert (filename, overwrite) == ("ORBI-1_ms2", True)
        np.testing.assert_array_equal(dataset.time.values, [35.1, 37.7, 40.3])
        assert dataset.mz.size == 0
        assert dataset.peak_areas.shape == (0, 3)
        assert dataset.peak_heights.shape == (0, 3)
