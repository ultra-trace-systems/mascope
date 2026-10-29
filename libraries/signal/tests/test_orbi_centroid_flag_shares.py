"""The share of each peak's per-scan centroids the instrument flagged
(``mascope_signal.compute.get_orbi_centroid_flag_shares``).

The peaks are on the file's calibrated axis, as ``get_orbi_centroids`` gives
them; a scan's centroids are read as it recorded them. So the peaks are taken
back by the factor they came by before the reader counts what lies under
them.
"""

import asyncio

import numpy as np
import pytest
from signal_test_support import SIGNAL_TEST_FILENAME

import mascope_file.io as m_io
import mascope_signal.compute as m_compute


STREAM = "FTMS - p NSI Full ms [40.0000-160.0000] R=120000 event=2"

FILE = 1 + 300e-6
OWN = 1 - 200e-6
RECORD = {
    "par": {"calibration_factor": FILE},
    "streams": {STREAM: {"calibration_factor": OWN}},
}


@pytest.fixture
def reader(monkeypatch, sample_file_path):
    """The test sample read as a raw Orbitrap file, by a reader that keeps
    what it was asked for."""
    monkeypatch.setattr(m_compute.m_name, "get_sample_file_type", lambda _: "orbi_raw")
    asked = {}

    def shares(datafile_path, masses, resolutions, **kwargs):
        asked.update(masses=masses, resolutions=resolutions, **kwargs)
        return np.full(len(masses), 0.25)

    monkeypatch.setattr(m_compute.m_thermo, "get_centroid_flag_shares", shares)
    return asked


def _shares(**kwargs):
    return asyncio.run(
        m_compute.get_orbi_centroid_flag_shares(
            SIGNAL_TEST_FILENAME,
            np.array([100.0, 200.0]),
            np.array([1e5, 7e4]),
            **kwargs,
        )
    )


def test_the_reader_is_asked_for_the_peaks_and_the_scans_given(reader):
    shares = _shares(polarity="-", stream=STREAM, flag="reference", calibration=None)

    assert shares.tolist() == [0.25, 0.25]
    assert reader["masses"].tolist() == [100.0, 200.0]
    assert reader["resolutions"].tolist() == [1e5, 7e4]
    assert (reader["polarity"], reader["stream"], reader["flag"]) == (
        "-",
        STREAM,
        "reference",
    )


def test_the_flag_asked_for_by_default_is_exception(reader):
    _shares(calibration=None)

    assert reader["flag"] == "exception"


def test_the_peaks_are_taken_back_by_the_files_recorded_factor(reader):
    m_io.update_props(SIGNAL_TEST_FILENAME, {"mz_calibration": RECORD})

    _shares()

    assert reader["masses"] == pytest.approx([100.0 / FILE, 200.0 / FILE], rel=1e-15)


def test_a_streams_peaks_are_taken_back_by_its_own_factor(reader):
    _shares(stream=STREAM, calibration=RECORD)

    assert reader["masses"] == pytest.approx([100.0 / OWN, 200.0 / OWN], rel=1e-15)


def test_the_record_handed_in_is_the_one_read_by(reader):
    """A caller that placed the peaks by a record hands it in, and that one
    is read by, whatever the file records by now."""
    m_io.update_props(SIGNAL_TEST_FILENAME, {"mz_calibration": RECORD})

    _shares(calibration={"par": {"calibration_factor": OWN}})

    assert reader["masses"] == pytest.approx([100.0 / OWN, 200.0 / OWN], rel=1e-15)


def test_an_uncalibrated_files_peaks_are_read_where_they_are(reader):
    _shares()

    assert reader["masses"].tolist() == [100.0, 200.0]


@pytest.mark.parametrize("sample_type", ["tof_h5", "tof_zarr", "orbi_zarr"])
def test_only_a_raw_orbitrap_files_flags_are_read(
    monkeypatch, sample_file_path, sample_type
):
    monkeypatch.setattr(m_compute.m_name, "get_sample_file_type", lambda _: sample_type)

    with pytest.raises(NotImplementedError):
        _shares(calibration=None)
