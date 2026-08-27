"""Refusing a peak store whose scan axis the sample file has outgrown.

A peak store's scan axis is fixed when peak detection allocates it, while the
scans a reader selects are decided anew on every read. The two drift apart on
files where the reader's first-scan-outlier exclusion applies but did not yet
exist when the store was written.

Such a store cannot be recomputed into: its per-peak sums were measured over
the discarded scan too, so distributing a recomputed timeseries over the scans
that remain would smear the very artifact the exclusion exists to drop across
the good scans. It is refused instead, with an error its caller can recognise
and answer by asking for peak detection.
"""

import numpy as np
import pytest
import xarray as xr
from conftest import SIGNAL_TEST_FILENAME

import mascope_file.io as m_io
import mascope_signal.compute as m_compute


SCAN_TIMES = np.array([2.5, 5.1, 7.7, 10.3, 12.9], dtype=float)
MZ_VALUES = np.array([100.0, 200.0, 300.0], dtype=float)
SUM_AREAS = np.array([1000.0, 2000.0, 3000.0], dtype=float)
SUM_HEIGHTS = np.array([10.0, 20.0, 30.0], dtype=float)


def _shares(scan_count):
    """Per-scan shares of a peak's intensity, summing to 1 and all distinct.

    Distinct per scan on purpose: a flat series would read back the same
    whether the scans were written in order, reversed, or rotated.
    """
    weights = np.arange(1.0, scan_count + 1.0)
    return weights / weights.sum()


def _stub_reader(monkeypatch, scan_times):
    """Make `get_peak_timeseries` return a rising series over `scan_times`."""

    async def fake_get_peak_timeseries(base_filename, mzs, *args, **kwargs):
        mzs = np.asarray(mzs, dtype=float)
        return xr.DataArray(
            np.tile(_shares(len(scan_times)), (len(mzs), 1)),
            dims=("mz", "time"),
            coords={"mz": mzs, "time": np.asarray(scan_times, dtype=float)},
            name="signal",
        )

    monkeypatch.setattr(m_compute, "get_peak_timeseries", fake_get_peak_timeseries)


@pytest.mark.asyncio
async def test_a_store_the_file_still_matches_is_computed_as_before(
    monkeypatch, write_peak_store
):
    """The axes agreeing is the ordinary case, and must stay a plain write."""
    write_peak_store(SCAN_TIMES, MZ_VALUES, SUM_AREAS, SUM_HEIGHTS)
    _stub_reader(monkeypatch, SCAN_TIMES)

    result = await m_compute.load_peak_timeseries(SIGNAL_TEST_FILENAME, MZ_VALUES)

    np.testing.assert_allclose(
        result.peak_areas.sel(mz=MZ_VALUES).values, np.outer(SUM_AREAS, _shares(5))
    )
    np.testing.assert_allclose(
        result.peak_heights.sel(mz=MZ_VALUES).values,
        np.outer(SUM_HEIGHTS, _shares(5)),
    )


@pytest.mark.asyncio
async def test_a_store_written_before_the_exclusion_is_refused(
    monkeypatch, write_peak_store
):
    """The production case: the store predates the outlier exclusion.

    The store holds every scan of the acquisition; the reader now drops the
    first one. Its sums were measured over that scan as well, so nothing here
    can recompute the file - the store has to be rebuilt.
    """
    write_peak_store(SCAN_TIMES, MZ_VALUES, SUM_AREAS, SUM_HEIGHTS)
    _stub_reader(monkeypatch, SCAN_TIMES[1:])

    with pytest.raises(m_compute.StalePeakStoreError, match="Re-run peak detection"):
        await m_compute.load_peak_timeseries(SIGNAL_TEST_FILENAME, MZ_VALUES)

    stored = m_io.load_peak_data(SIGNAL_TEST_FILENAME)
    assert not stored.is_timeseries_computed.values.any(), "nothing was written"
    assert np.isnan(stored.peak_areas.values).all(), "no partial recompute was left"


@pytest.mark.asyncio
async def test_a_store_from_another_acquisition_is_refused(
    monkeypatch, write_peak_store
):
    """Scan times that do not line up mean the store describes another file."""
    write_peak_store(SCAN_TIMES, MZ_VALUES, SUM_AREAS, SUM_HEIGHTS)
    _stub_reader(monkeypatch, SCAN_TIMES + 1000.0)

    with pytest.raises(m_compute.StalePeakStoreError, match="do not line up"):
        await m_compute.load_peak_timeseries(SIGNAL_TEST_FILENAME, MZ_VALUES)


@pytest.mark.asyncio
async def test_a_computed_store_is_served_without_reading_the_file(
    monkeypatch, write_peak_store
):
    """A store with nothing left to compute never reaches the check."""
    write_peak_store(SCAN_TIMES, MZ_VALUES, SUM_AREAS, SUM_HEIGHTS)
    _stub_reader(monkeypatch, SCAN_TIMES)
    await m_compute.load_peak_timeseries(SIGNAL_TEST_FILENAME, MZ_VALUES)

    async def refuse(*args, **kwargs):
        raise AssertionError("a computed sample was recomputed")

    monkeypatch.setattr(m_compute, "get_peak_timeseries", refuse)
    result = await m_compute.load_peak_timeseries(SIGNAL_TEST_FILENAME, MZ_VALUES)

    assert result.is_timeseries_computed.values.all()


class TestCheckStoredScanAxis:
    """The check itself, away from the zarr round-trip."""

    def test_identical_axes_pass(self):
        assert m_compute.check_stored_scan_axis(SCAN_TIMES, SCAN_TIMES) is None

    def test_a_dropped_scan_is_refused(self):
        with pytest.raises(m_compute.StalePeakStoreError, match="scan counts differ"):
            m_compute.check_stored_scan_axis(SCAN_TIMES[1:], SCAN_TIMES)

    def test_an_extra_scan_is_refused(self):
        extra = np.append(SCAN_TIMES, 15.5)
        with pytest.raises(m_compute.StalePeakStoreError, match="scan counts differ"):
            m_compute.check_stored_scan_axis(extra, SCAN_TIMES)

    def test_an_axis_read_back_a_few_bits_off_still_passes(self):
        """Float noise must not be mistaken for a stale store.

        The store's axis is float64 on disk and the reader recomputes it, so a
        healthy store can come back differing in the last bits. Calling that
        stale would queue peak detection for the file on every single read.
        """
        drifted = SCAN_TIMES + np.spacing(SCAN_TIMES) * 4
        assert m_compute.check_stored_scan_axis(drifted, SCAN_TIMES) is None

    def test_a_shifted_scan_is_refused(self):
        """Half the tightest spacing is the widest a scan may be off by."""
        # The gaps are 2.6 s, so the bound is 1.3 s
        shifted = SCAN_TIMES.copy()
        shifted[-1] += 1.4
        with pytest.raises(m_compute.StalePeakStoreError, match="do not line up"):
            m_compute.check_stored_scan_axis(shifted, SCAN_TIMES)

    def test_a_scan_just_inside_the_bound_still_passes(self):
        nudged = SCAN_TIMES.copy()
        nudged[-1] += 1.2
        assert m_compute.check_stored_scan_axis(nudged, SCAN_TIMES) is None

    def test_the_tightest_gap_sets_the_bound_on_an_uneven_axis(self):
        """Not the average gap, and not the widest one."""
        uneven = np.array([0.0, 1.0, 11.0, 21.0])  # gaps 1, 10, 10 -> bound 0.5
        assert (
            m_compute.check_stored_scan_axis(np.array([0.0, 1.0, 11.4, 21.0]), uneven)
            is None
        )
        with pytest.raises(m_compute.StalePeakStoreError, match="do not line up"):
            m_compute.check_stored_scan_axis(np.array([0.0, 1.0, 11.6, 21.0]), uneven)

    def test_a_one_scan_store_tolerates_float_noise(self):
        """No spacing to halve is not a reason to call a store stale."""
        single = np.array([1700000000.0])
        assert (
            m_compute.check_stored_scan_axis(single + np.spacing(single) * 4, single)
            is None
        )
        with pytest.raises(m_compute.StalePeakStoreError, match="do not line up"):
            m_compute.check_stored_scan_axis(single + 1.0, single)

    def test_a_non_finite_scan_time_is_refused(self):
        """NaN passes every comparison, so it has to be refused up front."""
        holed = np.array([2.5, np.nan, 7.7, 10.3, 12.9])
        with pytest.raises(m_compute.StalePeakStoreError, match="not finite"):
            m_compute.check_stored_scan_axis(SCAN_TIMES, holed)
        with pytest.raises(m_compute.StalePeakStoreError, match="not finite"):
            m_compute.check_stored_scan_axis(holed, SCAN_TIMES)

    def test_an_empty_scan_axis_is_refused(self):
        """A read that returned nothing must not mark the peaks computed."""
        with pytest.raises(m_compute.StalePeakStoreError, match="no scans"):
            m_compute.check_stored_scan_axis(np.array([]), SCAN_TIMES)
        with pytest.raises(m_compute.StalePeakStoreError, match="no scans"):
            m_compute.check_stored_scan_axis(SCAN_TIMES, np.array([]))

    def test_it_is_a_value_error(self):
        """The API layer maps a ValueError to a client-class failure."""
        assert issubclass(m_compute.StalePeakStoreError, ValueError)
