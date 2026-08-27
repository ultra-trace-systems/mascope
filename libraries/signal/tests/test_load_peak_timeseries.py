"""Placing freshly read scans on a peak store's own time axis.

A peak store's scan axis is fixed when peak detection allocates it, while the
scans a reader selects are decided anew on every read. The two drift apart on
files where the reader's first-scan-outlier exclusion applies but did not yet
exist when the store was written, and the recomputed timeseries then has to be
placed by timestamp rather than by position.
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
    whether the scans were placed in order, reversed, or rotated.
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
async def test_scans_the_reader_still_covers_are_written_positionally(
    monkeypatch, write_peak_store, compute_logger
):
    """The axes agreeing is the ordinary case, and must stay a plain write."""
    write_peak_store(SCAN_TIMES, MZ_VALUES, SUM_AREAS, SUM_HEIGHTS)
    _stub_reader(monkeypatch, SCAN_TIMES)

    result = await m_compute.load_peak_timeseries(SIGNAL_TEST_FILENAME, MZ_VALUES)

    expected = np.outer(SUM_AREAS, _shares(5))
    np.testing.assert_allclose(result.peak_areas.sel(mz=MZ_VALUES).values, expected)
    np.testing.assert_allclose(
        result.peak_heights.sel(mz=MZ_VALUES).values,
        np.outer(SUM_HEIGHTS, _shares(5)),
    )
    assert compute_logger.info.call_count == 0


@pytest.mark.asyncio
async def test_a_scan_the_reader_dropped_is_stored_as_no_data(
    monkeypatch, write_peak_store, compute_logger
):
    """The production case: the store predates the outlier exclusion.

    The store holds every scan of the acquisition; the reader now drops the
    first one. The recomputed values belong to the scans that remain, and the
    dropped one has to read back as "not measured" rather than as a zero.
    """
    write_peak_store(SCAN_TIMES, MZ_VALUES, SUM_AREAS, SUM_HEIGHTS)
    _stub_reader(monkeypatch, SCAN_TIMES[1:])

    result = await m_compute.load_peak_timeseries(SIGNAL_TEST_FILENAME, MZ_VALUES)

    areas = result.peak_areas.sel(mz=MZ_VALUES).values
    heights = result.peak_heights.sel(mz=MZ_VALUES).values
    assert areas.shape == (3, 5), "the store's own scan axis is left untouched"
    assert np.isnan(areas[:, 0]).all(), "the dropped scan holds no measurement"
    assert np.isnan(heights[:, 0]).all()
    # The four scans that were read keep their own shares, in their own order
    np.testing.assert_allclose(areas[:, 1:], np.outer(SUM_AREAS, _shares(4)))
    np.testing.assert_allclose(heights[:, 1:], np.outer(SUM_HEIGHTS, _shares(4)))
    assert compute_logger.info.call_count == 1
    logged = compute_logger.info.call_args.args[0]
    assert "5 scans" in logged and "4" in logged and "1 stored scan(s)" in logged


@pytest.mark.asyncio
async def test_sparsity_counts_only_the_scans_that_were_read(
    monkeypatch, write_peak_store
):
    """A scan nobody measured is not evidence that the peak is sparse."""
    write_peak_store(SCAN_TIMES, MZ_VALUES, SUM_AREAS, SUM_HEIGHTS)
    _stub_reader(monkeypatch, SCAN_TIMES[1:])

    result = await m_compute.load_peak_timeseries(SIGNAL_TEST_FILENAME, MZ_VALUES)

    # Every scan the reader returned carries signal, so nothing is sparse
    np.testing.assert_allclose(result.sparsity.sel(mz=MZ_VALUES).values, 0.0)


@pytest.mark.asyncio
async def test_a_store_from_another_acquisition_is_refused(
    monkeypatch, write_peak_store
):
    """Scan times that do not line up mean the store describes another file.

    Placing them anyway would blank rows that readers then average over, so
    this is the one case that has to stop rather than repair itself.
    """
    write_peak_store(SCAN_TIMES, MZ_VALUES, SUM_AREAS, SUM_HEIGHTS)
    _stub_reader(monkeypatch, SCAN_TIMES + 1000.0)

    with pytest.raises(ValueError, match="do not line up"):
        await m_compute.load_peak_timeseries(SIGNAL_TEST_FILENAME, MZ_VALUES)

    stored = m_io.load_peak_data(SIGNAL_TEST_FILENAME)
    assert not stored.is_timeseries_computed.values.any(), "nothing was written"


@pytest.mark.asyncio
async def test_a_repaired_store_stays_repaired(monkeypatch, write_peak_store):
    """Recovery has to hold: the next read is served from disk, unchanged."""
    write_peak_store(SCAN_TIMES, MZ_VALUES, SUM_AREAS, SUM_HEIGHTS)
    _stub_reader(monkeypatch, SCAN_TIMES[1:])

    repaired = await m_compute.load_peak_timeseries(SIGNAL_TEST_FILENAME, MZ_VALUES)
    repaired_areas = repaired.peak_areas.sel(mz=MZ_VALUES).values.copy()

    async def refuse(*args, **kwargs):
        raise AssertionError("a repaired sample was recomputed")

    monkeypatch.setattr(m_compute, "get_peak_timeseries", refuse)
    result = await m_compute.load_peak_timeseries(SIGNAL_TEST_FILENAME, MZ_VALUES)

    assert result.is_timeseries_computed.values.all()
    np.testing.assert_array_equal(
        result.peak_areas.sel(mz=MZ_VALUES).values, repaired_areas
    )


class TestStoredScanPositions:
    """The placement itself, away from the zarr round-trip."""

    def test_identical_axes_need_no_placement(self):
        assert m_compute._stored_scan_positions(SCAN_TIMES, SCAN_TIMES) is None

    def test_a_dropped_leading_scan_shifts_every_later_one(self):
        positions = m_compute._stored_scan_positions(SCAN_TIMES[1:], SCAN_TIMES)
        np.testing.assert_array_equal(positions, [1, 2, 3, 4])

    def test_a_scan_too_far_from_any_stored_one_is_refused(self):
        """Half the tightest spacing is the widest a scan may be off by.

        Wider than that and the nearest stored scan is no longer unambiguous,
        which is the point at which placing the values guesses.
        """
        # The gaps are 2.6 s, so the bound is 1.3 s
        drifted = np.array([5.1, 7.7, 10.3, 12.9 + 1.4])
        with pytest.raises(ValueError, match="do not line up"):
            m_compute._stored_scan_positions(drifted, SCAN_TIMES)

    def test_a_scan_just_inside_the_bound_still_matches(self):
        nudged = np.array([5.1, 7.7, 10.3, 12.9 + 1.2])
        positions = m_compute._stored_scan_positions(nudged, SCAN_TIMES)
        np.testing.assert_array_equal(positions, [1, 2, 3, 4])

    def test_float_noise_still_matches(self):
        nudged = SCAN_TIMES[1:] + 1e-9
        positions = m_compute._stored_scan_positions(nudged, SCAN_TIMES)
        np.testing.assert_array_equal(positions, [1, 2, 3, 4])

    def test_two_scans_may_not_claim_one_stored_scan(self):
        crowded = np.array([5.1, 5.1, 7.7], dtype=float)
        with pytest.raises(ValueError, match="same stored scan"):
            m_compute._stored_scan_positions(crowded, SCAN_TIMES)

    def test_an_unordered_stored_axis_is_refused(self):
        with pytest.raises(ValueError, match="out of order"):
            m_compute._stored_scan_positions(SCAN_TIMES, SCAN_TIMES[::-1])

    def test_a_non_finite_scan_time_is_refused(self):
        """NaN passes every comparison, so it has to be refused up front.

        A stored axis holding one leaves the ordering check vacuous and the
        tolerance NaN, which would place the values on a guess.
        """
        holed = np.array([2.5, np.nan, 7.7, 10.3, 12.9])
        with pytest.raises(ValueError, match="not finite"):
            m_compute._stored_scan_positions(np.array([2.5, 7.7, 10.3, 12.9]), holed)
        with pytest.raises(ValueError, match="not finite"):
            m_compute._stored_scan_positions(np.array([2.5, np.nan]), SCAN_TIMES)

    def test_an_empty_scan_axis_is_refused(self):
        """A read that returned nothing must not mark the peaks computed."""
        with pytest.raises(ValueError, match="no scans"):
            m_compute._stored_scan_positions(np.array([]), SCAN_TIMES)
        with pytest.raises(ValueError, match="no scans"):
            m_compute._stored_scan_positions(SCAN_TIMES, np.array([]))

    def test_the_tightest_gap_sets_the_bound_on_an_uneven_axis(self):
        """Not the average gap, and not the widest one."""
        uneven = np.array([0.0, 1.0, 11.0, 21.0])  # gaps 1, 10, 10 -> bound 0.5
        np.testing.assert_array_equal(
            m_compute._stored_scan_positions(np.array([1.0, 11.4, 21.0]), uneven),
            [1, 2, 3],
        )
        with pytest.raises(ValueError, match="do not line up"):
            m_compute._stored_scan_positions(np.array([1.0, 11.6, 21.0]), uneven)

    def test_placement_leaves_uncovered_scans_empty(self):
        values = np.array([[1.0, 2.0], [3.0, 4.0]])
        placed = m_compute._place_on_stored_scans(values, np.array([0, 2]), 3)
        np.testing.assert_array_equal(placed[:, [0, 2]], values)
        assert np.isnan(placed[:, 1]).all()
