"""A sample file's peak listing gives every peak its value, timeseries or not.

Peak detection allocates every peak with an all-NaN per-scan timeseries, and
a series is computed only once something asks for it. Each peak's stored sums
exist from detection on, so the file's listing reads those - as the sample's
own listing does - rather than averaging a timeseries that is NaN for every
peak nothing has asked about yet.

A file can hold peaks of both polarities, and a peak's stored sum is over the
scans its own polarity (or, per stream, its own stream) holds, so the
listing's averages must match the sample listing of each polarity.
"""

import asyncio

import numpy as np
import pytest
from composite_acquisition import COMPOSITE, MICROSCANS, POSITIVE
from scripted_acquisition import SAMPLE_FILENAME

import mascope_signal.compute as m_compute
import mascope_signal.peak as m_peak
from mascope_backend.api.controllers.sample.files import sample_files_controller
from mascope_backend.api.controllers.samples.lib.samples_peaks import extract_peaks
from mascope_file.io import load_peak_data


@pytest.fixture(params=[True, False], ids=["per-stream", "pooled"])
def both_polarities(request, acquire, instrument_functions):
    """The composite file and two positive scans after it, its peaks just
    detected: no peak has a timeseries."""
    acquire(COMPOSITE + [(POSITIVE, 5, {59.0: 70.0, 250.0: 20.0})] * 2, MICROSCANS)
    m_peak.compute_peaks(
        SAMPLE_FILENAME, instrument_functions, per_stream=request.param
    )


def _file_listing(monkeypatch) -> dict:
    """The file peaks route's answer for the scripted file."""

    async def _get_sample_file(_sample_file_id):
        return {"data": {"filename": SAMPLE_FILENAME}}

    monkeypatch.setattr(sample_files_controller, "get_sample_file", _get_sample_file)
    return asyncio.run(
        sample_files_controller.get_sample_file_peaks(
            "file-1", areas=True, heights=True
        )
    )["data"]


def _per_polarity(values: list, polarity: str) -> list:
    """The entries of a whole-file list that belong to one polarity's peaks."""
    at = np.flatnonzero(load_peak_data(SAMPLE_FILENAME).polarity.values == polarity)
    return [values[i] for i in at]


def test_no_peak_is_computed_yet(both_polarities):
    """What the rest is about: every per-scan series is still a placeholder."""
    data = load_peak_data(SAMPLE_FILENAME)
    assert set(data.polarity.values) == {"+", "-"}
    assert np.isnan(data.peak_heights.values).all()


def test_every_peak_has_a_value(both_polarities, monkeypatch):
    listing = _file_listing(monkeypatch)

    assert len(listing["mz"]) == load_peak_data(SAMPLE_FILENAME).mz.size
    assert None not in listing["height"]
    assert None not in listing["area"]


@pytest.mark.parametrize("polarity", ["+", "-"])
def test_a_polarity_reads_as_the_sample_listing_reads_it(
    both_polarities, monkeypatch, polarity
):
    """Each peak averaged over its own polarity's scans, the same values the
    sample's own listing gives without a time range."""
    listing = _file_listing(monkeypatch)
    sample = extract_peaks(SAMPLE_FILENAME, polarity, 0.0, 0.0)

    assert sample.count > 0
    assert _per_polarity(listing["mz"], polarity) == sample.mz_values
    assert _per_polarity(listing["height"], polarity) == pytest.approx(sample.heights)
    assert _per_polarity(listing["area"], polarity) == pytest.approx(sample.areas)
    assert _per_polarity(listing["sparsity"], polarity) == sample.sparsity


@pytest.mark.parametrize("polarity", ["+", "-"])
def test_a_summed_read_is_the_stored_sum(both_polarities, polarity):
    """Not averaged, a peak's value is its stored sum, never a 0 for a peak
    with no timeseries."""
    listing = sample_files_controller._sync_load_sample_file_peaks(
        SAMPLE_FILENAME, areas=True, heights=True, average=False
    )
    sample = extract_peaks(SAMPLE_FILENAME, polarity, 0.0, 0.0, average=False)

    heights = _per_polarity(listing["height"], polarity)
    assert heights == pytest.approx(sample.heights)
    assert 0.0 not in heights


def _mean_over_own_scans(data, variable: str) -> list:
    """Each peak's series averaged over its own polarity's scans. A store
    detected whole spans every scan of the file, the other polarity's at 0;
    a per-stream one leaves another stream's scans NaN, which the mean skips."""
    means = []
    for i, polarity in enumerate(data.polarity.values):
        scans = m_compute.get_scan_timestamps(SAMPLE_FILENAME, polarity=str(polarity))
        series = data[variable].isel(mz=i)
        means.append(float(series.sel(time=np.isin(series.time, scans)).mean()))
    return means


def test_a_computed_peak_reads_as_the_mean_of_its_series(both_polarities, monkeypatch):
    """Once every series is computed, the stored sums give what averaging a
    peak's series over its own polarity's scans gives."""
    every_mz = load_peak_data(SAMPLE_FILENAME).mz.values.tolist()
    asyncio.run(m_compute.load_peak_timeseries(SAMPLE_FILENAME, every_mz)).compute()
    data = load_peak_data(SAMPLE_FILENAME)
    assert not np.isnan(data.peak_heights.values).all(axis=0).any()

    listing = _file_listing(monkeypatch)

    assert listing["height"] == pytest.approx(
        _mean_over_own_scans(data, "peak_heights")
    )
    assert listing["area"] == pytest.approx(_mean_over_own_scans(data, "peak_areas"))


def test_the_polarities_average_over_different_scans(both_polarities):
    """What makes the per-polarity count matter: the file's polarities do not
    hold the same number of scans."""
    assert len(m_compute.get_scan_timestamps(SAMPLE_FILENAME, polarity="+")) != len(
        m_compute.get_scan_timestamps(SAMPLE_FILENAME, polarity="-")
    )
