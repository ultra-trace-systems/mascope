"""A re-processed file's peaks are detected under the setting as it is now.

Whether a file's peaks are detected per scan stream is decided at its first
conversion and recorded beside it, and a rebuild of its store goes by the
record. A re-processing decides again
(``mascope_backend.api.controllers.sample.files.process.peaks``): it hands
peak detection the deployment's ``[backend] composite_scan_streams`` as set
at that moment, for the files where that can give another store than the
one they have, and leaves every other file's store alone.

On a scripted acquisition, as the detection's own tests are
(``test_peak_streams``): no committed file holds more than one stream.
"""

import asyncio
import json
import os
from types import SimpleNamespace

import pytest
from scripted_acquisition import SAMPLE_FILENAME

import mascope_file.io as m_io
import mascope_signal.compute as m_compute
import mascope_signal.peak as m_peak
from mascope_backend.api.controllers.sample.files.process import peaks


NEG = "FTMS - p NSI Full ms [40.0000-600.0000]"
POS = "FTMS + p NSI Full ms [40.0000-600.0000]"

SETTLING = f"{NEG} R=120000 event=1"
MEASURING = f"{NEG} R=120000 event=2"

# Two experiments in one polarity that no filter tells apart: two scans while
# the source settles at one microscan, then four of the measurement at ten.
TWO_EXPERIMENTS = [
    (NEG, 1, {62.0: 100.0, 125.0: 10.0}),
    (NEG, 1, {62.0: 100.0, 125.0: 10.0}),
    (NEG, 2, {62.0: 1000.0, 188.0: 50.0}),
    (NEG, 2, {62.0: 1000.0, 188.0: 50.0}),
    (NEG, 2, {62.0: 1000.0}),
    (NEG, 2, {62.0: 1000.0, 188.0: 50.0}),
]
MICROSCANS = {1: 1, 2: 10}

# The same scans read back as one experiment: nothing to detect apart.
AS_ONE_EXPERIMENT = [(text, 1, peaks_) for text, _event, peaks_ in TWO_EXPERIMENTS]

ONE_PER_POLARITY = [(NEG, 1, {62.0: 100.0}), (POS, 2, {59.0: 70.0})] * 2

#: A file with an instrument config, which every file but a blank has.
FILE = SimpleNamespace(filename=SAMPLE_FILENAME, instrument_function_id="config")


@pytest.fixture
def setting(monkeypatch):
    """Set ``composite_scan_streams`` for the test, as a deployment does."""

    def _set(value: bool) -> None:
        monkeypatch.setattr(
            peaks.runtime.full_config.backend, "composite_scan_streams", value
        )

    return _set


@pytest.fixture
def redetect(monkeypatch, instrument_functions):
    """Detect the file's peaks again, as a re-processing does: by what
    :func:`peaks.redetection_decision` says, where it says anything."""

    async def functions(filename):
        return instrument_functions

    monkeypatch.setattr(peaks, "read_instrument_functions", functions)

    def _redetect() -> bool | None:
        decision = asyncio.run(peaks.redetection_decision(FILE))
        if decision is not None:
            asyncio.run(peaks.redetect_peaks(FILE, decision))
        return decision

    return _redetect


def _store():
    return m_io.load_peak_data(
        SAMPLE_FILENAME, drop_bad_peaks=False, composite=False
    ).compute()


def _props(sample_file_path):
    with open(os.path.join(sample_file_path, ".props")) as f:
        return json.load(f)


def _converted(instrument_functions, per_stream: bool) -> None:
    """The file's first conversion, under a deployment set this way."""
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=per_stream)


# -- the setting, as set now ----------------------------------------------------


def test_the_setting_is_read_from_the_backend_config(setting):
    setting(True)
    assert peaks.composites_scan_streams() is True

    setting(False)
    assert peaks.composites_scan_streams() is False


def test_a_deployment_without_a_backend_section_detects_whole(monkeypatch):
    monkeypatch.setattr(peaks.runtime._full_config, "backend", None)

    assert peaks.composites_scan_streams() is False


# -- what a re-processing decides, and what it then writes ----------------------


def test_a_file_converted_before_the_setting_was_on_is_stitched(
    acquire, instrument_functions, sample_file_path, setting, redetect
):
    """The files a site holds from before it switched the setting on: pooled,
    with nothing recorded. Re-processed, their peaks are detected per stream
    and stitched, and the record says so for every rebuild after."""
    acquire(TWO_EXPERIMENTS, MICROSCANS)
    _converted(instrument_functions, per_stream=False)
    assert m_compute.peak_store_streams(_store()) == []
    assert _props(sample_file_path) == {"mz_calibration": None}

    setting(True)
    assert redetect() is True

    store = _store()
    assert m_compute.peak_store_streams(store) == [SETTLING, MEASURING]
    assert m_compute.peak_store_stitch_map(store)["runs"]
    assert _props(sample_file_path) == {
        "mz_calibration": None,
        "peaks_per_stream": True,
    }
    # And a rebuild goes by that record from then on
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions)
    assert m_compute.peak_store_streams(_store()) == [SETTLING, MEASURING]


def test_a_stitched_file_is_pooled_again_where_the_setting_is_off(
    acquire, instrument_functions, sample_file_path, setting, redetect
):
    """The other direction, which is what lets a site read the same files
    both ways: the setting of the moment decides, and is recorded."""
    acquire(TWO_EXPERIMENTS, MICROSCANS)
    _converted(instrument_functions, per_stream=True)

    setting(False)
    assert redetect() is False

    assert m_compute.peak_store_streams(_store()) == []
    assert _props(sample_file_path) == {
        "mz_calibration": None,
        "peaks_per_stream": False,
    }


def test_a_stitched_file_is_detected_again_under_the_setting_it_was_built_by(
    acquire, instrument_functions, setting, redetect, monkeypatch
):
    """Its map is drawn by the rule of the day, so a re-processing redraws
    it even where the decision is the one the file carries."""
    acquire(TWO_EXPERIMENTS, MICROSCANS)
    _converted(instrument_functions, per_stream=True)
    drawn = []
    stitch_map = m_peak.m_stitch.stitch_map
    monkeypatch.setattr(
        m_peak.m_stitch,
        "stitch_map",
        lambda streams, *args, **kwargs: (
            drawn.append(len(streams)) or stitch_map(streams, *args, **kwargs)
        ),
    )

    setting(True)
    assert redetect() is True

    assert drawn == [2]
    assert m_compute.peak_store_streams(_store()) == [SETTLING, MEASURING]


def test_a_file_decided_against_is_stitched_once_the_setting_is_on(
    acquire, instrument_functions, sample_file_path, setting, redetect
):
    acquire(TWO_EXPERIMENTS, MICROSCANS)
    _converted(instrument_functions, per_stream=True)
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=False)
    assert _props(sample_file_path)["peaks_per_stream"] is False

    setting(True)
    assert redetect() is True

    assert m_compute.peak_store_streams(_store()) == [SETTLING, MEASURING]
    assert _props(sample_file_path)["peaks_per_stream"] is True


def test_a_record_for_per_stream_is_replaced_though_its_store_is_pooled(
    acquire, instrument_functions, sample_file_path, setting, redetect
):
    """A rebuild that found one stream leaves a pooled store under a record
    for per stream (``test_peak_streams``). The setting off, a re-processing
    decides against, so that the store is not taken apart again by the next
    rebuild that reads the file's streams back."""
    acquire(TWO_EXPERIMENTS, MICROSCANS)
    _converted(instrument_functions, per_stream=True)
    acquire(AS_ONE_EXPERIMENT)
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions)
    assert m_compute.peak_store_streams(_store()) == []

    setting(False)
    assert redetect() is False

    assert _props(sample_file_path)["peaks_per_stream"] is False


@pytest.mark.parametrize(
    "now, streams",
    [(False, []), (True, [SETTLING, MEASURING])],
    ids=["setting-off", "setting-on"],
)
def test_a_per_stream_store_is_decided_for_though_its_record_is_gone(
    acquire, instrument_functions, sample_file_path, setting, redetect, now, streams
):
    """The store is what the file's samples read, so it says per stream as
    plainly as the record does: a file whose ``.props`` lost the record is
    not left per stream under a setting that is off."""
    acquire(TWO_EXPERIMENTS, MICROSCANS)
    _converted(instrument_functions, per_stream=True)
    with open(os.path.join(sample_file_path, ".props"), "w") as f:
        json.dump({"mz_calibration": None}, f)

    setting(now)
    assert redetect() is now

    assert m_compute.peak_store_streams(_store()) == streams


def test_a_store_labelled_in_part_is_detected_again(
    acquire, instrument_functions, setting, monkeypatch
):
    """Nothing reads such a store - it is refused wherever its streams are
    asked for - and detecting the file's peaks writes all of it, so it is
    decided for like the per-stream store it was meant to be."""
    acquire(AS_ONE_EXPERIMENT)
    _converted(instrument_functions, per_stream=False)

    def refused(_store):
        raise ValueError("The peak store carries only part of its stream labels")

    monkeypatch.setattr(peaks.m_compute, "peak_store_streams", refused)

    setting(False)
    assert asyncio.run(peaks.redetection_decision(FILE)) is False


# -- the files whose store stands -----------------------------------------------


@pytest.mark.parametrize(
    "scans",
    [AS_ONE_EXPERIMENT, ONE_PER_POLARITY],
    ids=["one-experiment", "one-per-polarity"],
)
def test_a_file_with_nothing_to_detect_apart_keeps_its_store(
    acquire, instrument_functions, sample_file_path, setting, redetect, scans
):
    """Nearly every file. Its store is the one any decision gives it, so it
    is not written, and neither is anything beside it: its peaks keep their
    ids, whichever way the deployment is set."""
    acquire(scans)
    _converted(instrument_functions, per_stream=False)
    ids = _store().peak_id.values.tolist()

    setting(True)
    assert redetect() is None

    assert _store().peak_id.values.tolist() == ids
    assert _props(sample_file_path) == {"mz_calibration": None}


def test_a_pooled_file_is_not_read_for_its_streams_where_the_setting_is_off(
    acquire, instrument_functions, setting
):
    """A deployment that never switched the setting on re-processes a file
    as it always did: nothing about streams is asked of it."""
    acquisition = acquire(TWO_EXPERIMENTS, MICROSCANS)
    _converted(instrument_functions, per_stream=False)

    setting(False)
    assert asyncio.run(peaks.redetection_decision(FILE)) is None

    assert acquisition.trailer_reads == 0


def test_only_a_raw_orbitrap_file_is_decided_for(monkeypatch, setting):
    """Every other sample type is one stream, and is not read at all: not
    its record, which a TOF file's ``.props`` does not carry, nor its store."""
    monkeypatch.setattr(m_compute.m_name, "get_sample_file_type", lambda _: "tof_h5")
    monkeypatch.setattr(
        peaks.m_io, "read_props", lambda _: pytest.fail("read its .props")
    )

    setting(True)
    assert asyncio.run(peaks.redetection_decision(FILE)) is None


def test_a_blank_measurement_is_not_decided_for(acquire, setting, monkeypatch):
    """Stored without an instrument config and with an empty peak store that
    no detection writes, so there is nothing to detect it with."""
    acquire(TWO_EXPERIMENTS, MICROSCANS)
    monkeypatch.setattr(peaks, "_decide", lambda _: pytest.fail("asked the filestore"))
    blank = SimpleNamespace(filename=SAMPLE_FILENAME, instrument_function_id=None)

    setting(True)
    assert asyncio.run(peaks.redetection_decision(blank)) is None


def test_a_file_whose_streams_cannot_be_read_is_not_decided_for(
    acquire, instrument_functions, sample_file_path, setting, monkeypatch
):
    """Unread, a file of several experiments cannot be told from one with
    nothing to detect apart, so the answer is neither: the error reaches the
    re-processing, which refuses the file before it touches it."""
    acquire(TWO_EXPERIMENTS, MICROSCANS)
    _converted(instrument_functions, per_stream=False)

    def unreadable(_filename):
        raise OSError("the raw file is gone")

    monkeypatch.setattr(peaks.m_compute, "get_peak_streams", unreadable)

    setting(True)
    with pytest.raises(OSError, match="the raw file is gone"):
        asyncio.run(peaks.redetection_decision(FILE))

    assert m_compute.peak_store_streams(_store()) == []
    assert _props(sample_file_path) == {"mz_calibration": None}


@pytest.mark.parametrize("now", [True, False], ids=["setting-on", "setting-off"])
def test_a_per_stream_file_whose_streams_cannot_be_read_is_not_decided_for(
    acquire, instrument_functions, sample_file_path, setting, monkeypatch, now
):
    """Its record already says how its peaks are detected, and they are to
    be detected again whichever way the setting stands. That takes the raw
    file, so it is read for its streams here, before the re-processing has
    claimed the file or reset its calibration: unreadable, it is refused as
    it stands."""
    acquire(TWO_EXPERIMENTS, MICROSCANS)
    _converted(instrument_functions, per_stream=True)
    before = _props(sample_file_path)
    ids = _store().peak_id.values.tolist()

    def unreadable(_filename):
        raise OSError("the raw file is gone")

    monkeypatch.setattr(peaks.m_compute, "get_peak_streams", unreadable)

    setting(now)
    with pytest.raises(OSError, match="the raw file is gone"):
        asyncio.run(peaks.redetection_decision(FILE))

    assert _store().peak_id.values.tolist() == ids
    assert _props(sample_file_path) == before


def test_a_per_stream_store_whose_record_is_gone_is_read_for_its_streams_too(
    acquire, instrument_functions, sample_file_path, setting, monkeypatch
):
    acquire(TWO_EXPERIMENTS, MICROSCANS)
    _converted(instrument_functions, per_stream=True)
    with open(os.path.join(sample_file_path, ".props"), "w") as f:
        json.dump({"mz_calibration": None}, f)

    def unreadable(_filename):
        raise OSError("the raw file is gone")

    monkeypatch.setattr(peaks.m_compute, "get_peak_streams", unreadable)

    setting(False)
    with pytest.raises(OSError, match="the raw file is gone"):
        asyncio.run(peaks.redetection_decision(FILE))


# -- a detection that cannot write its store ------------------------------------


def _failing_write(monkeypatch):
    """Make the store's write fail after the peaks are detected."""

    async def out_of_space(self, overwrite=True):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(m_peak.BasePeakDetector, "write_peaks_to_zarr", out_of_space)


def test_a_detection_that_cannot_write_leaves_the_file_as_it_was_decided(
    acquire, instrument_functions, sample_file_path, setting, monkeypatch
):
    """The file's samples read the store it has, so that store stays and so
    does the decision it was built by: a record saying pooled beside a store
    detected per stream would have the next rebuild write another store than
    the samples were made against."""
    acquire(TWO_EXPERIMENTS, MICROSCANS)
    _converted(instrument_functions, per_stream=True)
    before = _props(sample_file_path)
    ids = _store().peak_id.values.tolist()
    _failing_write(monkeypatch)

    with pytest.raises(OSError, match="No space left on device"):
        m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=False)

    assert _props(sample_file_path) == before
    assert before[m_peak.PER_STREAM_PROP] is True
    assert _store().peak_id.values.tolist() == ids
    assert m_compute.peak_store_streams(_store()) == [SETTLING, MEASURING]


def test_a_file_nobody_had_decided_for_is_undecided_again(
    acquire, instrument_functions, sample_file_path, monkeypatch
):
    """Its ``.props`` are what they were, the entry gone and not null."""
    acquire(TWO_EXPERIMENTS, MICROSCANS)
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions)
    assert _props(sample_file_path) == {"mz_calibration": None}
    _failing_write(monkeypatch)

    with pytest.raises(OSError):
        m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=True)

    assert _props(sample_file_path) == {"mz_calibration": None}
    assert m_compute.peak_store_streams(_store()) == []


def test_a_first_detection_that_cannot_write_keeps_its_decision(
    acquire, instrument_functions, sample_file_path, monkeypatch
):
    """The file has no store for a record to disagree with: the decision
    stands, and is what its first store is built by."""
    acquire(TWO_EXPERIMENTS, MICROSCANS)
    _failing_write(monkeypatch)

    with pytest.raises(OSError):
        m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=True)

    assert _props(sample_file_path)[m_peak.PER_STREAM_PROP] is True


def test_a_detection_that_writes_keeps_the_decision_it_recorded(
    acquire, instrument_functions, sample_file_path
):
    acquire(TWO_EXPERIMENTS, MICROSCANS)
    _converted(instrument_functions, per_stream=True)

    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=False)

    assert _props(sample_file_path)[m_peak.PER_STREAM_PROP] is False
    assert m_compute.peak_store_streams(_store()) == []


def test_a_file_with_no_peak_store_is_decided_by_its_streams(
    acquire, setting, sample_file_path
):
    """Nothing to rebuild, which is not a reason to leave a file of several
    experiments without the store its re-processing reads."""
    acquire(TWO_EXPERIMENTS, MICROSCANS)

    setting(True)
    assert asyncio.run(peaks.redetection_decision(FILE)) is True

    setting(False)
    assert asyncio.run(peaks.redetection_decision(FILE)) is None
