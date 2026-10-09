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

Also here: filling a store two of whose peaks share an m/z
(``TestPeaksSharingAnMz``).
"""

import threading
import time

import numpy as np
import pytest
import xarray as xr
from signal_test_support import SIGNAL_TEST_FILENAME

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


def _stub_reader(
    monkeypatch,
    scan_times,
    rescaling=None,
    rescaled_reads=0,
    new_ids=False,
    meanwhile=None,
):
    """Make `get_peak_timeseries` return a rising series over `scan_times`.

    With `rescaling`, the store's m/z axis is multiplied by it during each of
    the first `rescaled_reads` reads, as an m/z calibration applied while the
    file is being read back rescales it. With `new_ids` its peaks are given
    new ids as well, as detecting the file's peaks again gives them.
    `meanwhile` is called during the first read, for whatever else is to
    happen to the store while the file is read back.

    :return: The m/z values each read was asked for, in the order of the reads
    """
    asked = []

    async def fake_get_peak_timeseries(base_filename, mzs, *args, **kwargs):
        mzs = np.asarray(mzs, dtype=float)
        asked.append(mzs.tolist())
        if meanwhile is not None and len(asked) == 1:
            meanwhile()
        if len(asked) <= rescaled_reads:
            stored = m_io.load_coord(base_filename, "peak_timeseries", "mz")
            m_io.update_zarr_array_coord(
                base_filename, "peak_timeseries", "mz", stored * rescaling
            )
            if new_ids:
                m_io.update_zarr_array_coord(
                    base_filename,
                    "peak_timeseries",
                    "peak_id",
                    [f"anew_{i:04d}" for i in range(stored.size)],
                )
        return xr.DataArray(
            np.tile(_shares(len(scan_times)), (len(mzs), 1)),
            dims=("mz", "time"),
            coords={"mz": mzs, "time": np.asarray(scan_times, dtype=float)},
            name="signal",
        )

    monkeypatch.setattr(m_compute, "get_peak_timeseries", fake_get_peak_timeseries)
    return asked


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


@pytest.mark.asyncio
async def test_a_peak_asked_for_off_the_axis_is_filled_at_its_stored_mz(
    monkeypatch, write_peak_store
):
    """An m/z asked for names the nearest peak, and the fill carries that peak's.

    What is asked for need not be on the store's axis: the API takes a peak's
    m/z from its caller and serves the nearest peak within a tolerance. The
    store takes a fill only at an m/z on its axis exactly, so the fill has to
    be built from the m/z read off the store, not the one asked for.
    """
    # 1.3 ppm above what is asked for, and no round numbers
    stored_mz = MZ_VALUES * (1 + 1.3e-6)
    write_peak_store(SCAN_TIMES, stored_mz, SUM_AREAS, SUM_HEIGHTS)
    _stub_reader(monkeypatch, SCAN_TIMES)

    result = await m_compute.load_peak_timeseries(SIGNAL_TEST_FILENAME, MZ_VALUES)

    np.testing.assert_array_equal(result.mz.values, stored_mz)
    assert result.is_timeseries_computed.values.all()
    np.testing.assert_allclose(
        result.peak_heights.values, np.outer(SUM_HEIGHTS, _shares(5))
    )


class TestAFillMeetingARewrittenAxis:
    """A fill whose store was recalibrated while the file was being read back.

    Reading a file back takes seconds, and nothing keeps an m/z calibration
    of the same file from being applied meanwhile. The fill then carries the
    m/z values of an axis the store no longer has, and the store refuses it.
    The peaks asked for are still there, on the axis as it has become, so
    they are loaded and computed once more.
    """

    @pytest.mark.parametrize(
        "rescaling",
        [
            # The first row at or above a peak's old m/z is then its own
            pytest.param(1 + 2e-6, id="axis moved up"),
            # And then the next peak's
            pytest.param(1 - 2e-6, id="axis moved down"),
        ],
    )
    @pytest.mark.asyncio
    async def test_the_peaks_are_computed_again_on_the_axis_as_it_has_become(
        self, rescaling, monkeypatch, write_peak_store
    ):
        write_peak_store(SCAN_TIMES, MZ_VALUES, SUM_AREAS, SUM_HEIGHTS)
        asked = _stub_reader(monkeypatch, SCAN_TIMES, rescaling, rescaled_reads=1)

        result = await m_compute.load_peak_timeseries(SIGNAL_TEST_FILENAME, MZ_VALUES)

        rescaled = MZ_VALUES * rescaling
        np.testing.assert_array_equal(result.mz.values, rescaled)
        assert result.is_timeseries_computed.values.all()
        np.testing.assert_allclose(
            result.peak_heights.values, np.outer(SUM_HEIGHTS, _shares(5))
        )
        np.testing.assert_allclose(
            result.peak_areas.values, np.outer(SUM_AREAS, _shares(5))
        )
        # Read back for the axis as it was loaded, then for the one it became
        assert asked == [MZ_VALUES.tolist(), rescaled.tolist()]

    # Rows 1 and 2 are 3 ppm apart: nearer each other than twice the 2 ppm
    # the axis moves by
    CLOSE_MZ = np.array([100.0, 200.0, 200.0006, 300.0])
    CLOSE_AREAS = np.array([1000.0, 2000.0, 3000.0, 4000.0])
    CLOSE_HEIGHTS = np.array([10.0, 20.0, 30.0, 40.0])

    @pytest.mark.parametrize(
        "rescaling",
        [
            pytest.param(1 + 2e-6, id="axis moved up"),
            pytest.param(1 - 2e-6, id="axis moved down"),
        ],
    )
    @pytest.mark.asyncio
    async def test_peaks_nearer_each_other_than_the_axis_moved_are_all_computed(
        self, rescaling, monkeypatch, write_peak_store
    ):
        """The m/z values asked for were read off the store: labels of the
        axis as it was. Taken to the nearest row of the axis as it has
        become, both labels of the close pair name one row, and whoever
        asked for four peaks is handed three and not told. The same peaks
        are found by their ids instead, which a calibration leaves alone."""
        write_peak_store(
            SCAN_TIMES, self.CLOSE_MZ, self.CLOSE_AREAS, self.CLOSE_HEIGHTS
        )
        _stub_reader(monkeypatch, SCAN_TIMES, rescaling, rescaled_reads=1)

        result = await m_compute.load_peak_timeseries(
            SIGNAL_TEST_FILENAME, self.CLOSE_MZ
        )

        assert result.peak_id.values.tolist() == [
            "peak_0000",
            "peak_0001",
            "peak_0002",
            "peak_0003",
        ]
        np.testing.assert_array_equal(result.mz.values, self.CLOSE_MZ * rescaling)
        assert result.is_timeseries_computed.values.all()
        # Each scaled to its own summed intensity, so each on its own row
        np.testing.assert_allclose(
            result.peak_heights.values, np.outer(self.CLOSE_HEIGHTS, _shares(5))
        )

    @pytest.mark.asyncio
    async def test_peaks_detected_again_are_found_by_the_mz_asked_for(
        self, monkeypatch, write_peak_store
    ):
        """A new detection gives every peak a new id, so no peak of the store
        is one the refused fill had loaded. The m/z values asked for are all
        there is to go by then, as they are for any call."""
        write_peak_store(SCAN_TIMES, MZ_VALUES, SUM_AREAS, SUM_HEIGHTS)
        _stub_reader(monkeypatch, SCAN_TIMES, 1 + 2e-6, rescaled_reads=1, new_ids=True)

        result = await m_compute.load_peak_timeseries(SIGNAL_TEST_FILENAME, MZ_VALUES)

        assert result.peak_id.values.tolist() == ["anew_0000", "anew_0001", "anew_0002"]
        np.testing.assert_array_equal(result.mz.values, MZ_VALUES * (1 + 2e-6))
        assert result.is_timeseries_computed.values.all()

    @pytest.mark.asyncio
    async def test_a_refused_fills_peaks_are_known_before_the_store_is_replaced(
        self, monkeypatch, write_peak_store
    ):
        """The ids a second attempt goes by are read while the store is still
        the one the peaks were loaded from. Read after the refusal, off the
        first attempt's load, they are whatever ids the store now holds on
        the same rows: found in it, and leading to peaks nobody asked for.
        Here a new detection puts a peak at 50 on the row the peak at 100 was
        on, and it is neither returned nor filled."""
        write_peak_store(SCAN_TIMES, MZ_VALUES, SUM_AREAS, SUM_HEIGHTS)
        detected_again = np.array([50.0, 100.0002, 200.0004])

        def detect_the_peaks_again():
            write_peak_store(SCAN_TIMES, detected_again, SUM_AREAS, SUM_HEIGHTS)
            m_io.update_zarr_array_coord(
                SIGNAL_TEST_FILENAME,
                "peak_timeseries",
                "peak_id",
                ["anew_0000", "anew_0001", "anew_0002"],
            )

        asked = _stub_reader(monkeypatch, SCAN_TIMES, meanwhile=detect_the_peaks_again)

        result = await m_compute.load_peak_timeseries(SIGNAL_TEST_FILENAME, MZ_VALUES)

        # The peaks nearest what was asked for: 300 is nearest the last one
        assert result.peak_id.values.tolist() == ["anew_0001", "anew_0002"]
        assert asked == [MZ_VALUES.tolist(), detected_again[1:].tolist()]
        stored = m_io.load_peak_data(SIGNAL_TEST_FILENAME)
        assert stored.is_timeseries_computed.values.tolist() == [False, True, True]

    @pytest.mark.asyncio
    async def test_the_second_attempt_waits_for_the_calibration_to_finish(
        self, monkeypatch, write_peak_store
    ):
        """A rewritten axis is not a finished calibration. The Orbitrap one
        records its factor after the axis, and the file is read back by that
        factor: read again before it is recorded, the file is read at m/z
        values off by the calibration, and what is found there is stored as
        the peak's. The fill that was refused starts right behind the apply,
        so it waits for the lock the apply holds."""
        write_peak_store(SCAN_TIMES, MZ_VALUES, SUM_AREAS, SUM_HEIGHTS)
        events = []
        applying = threading.Event()

        def the_rest_of_the_apply():
            lock = m_io.mz_calibration_lock_path(SIGNAL_TEST_FILENAME)
            with m_io.zarr_write_lock(lock):
                applying.set()
                time.sleep(0.2)
                events.append("the apply finished")

        async def reader(base_filename, mzs, *args, **kwargs):
            mzs = np.asarray(mzs, dtype=float)
            events.append("read")
            if events == ["read"]:
                stored = m_io.load_coord(base_filename, "peak_timeseries", "mz")
                m_io.update_zarr_array_coord(
                    base_filename, "peak_timeseries", "mz", stored * (1 + 2e-6)
                )
                threading.Thread(target=the_rest_of_the_apply).start()
                assert applying.wait(5)
            return xr.DataArray(
                np.tile(_shares(len(SCAN_TIMES)), (len(mzs), 1)),
                dims=("mz", "time"),
                coords={"mz": mzs, "time": SCAN_TIMES},
                name="signal",
            )

        monkeypatch.setattr(m_compute, "get_peak_timeseries", reader)

        result = await m_compute.load_peak_timeseries(SIGNAL_TEST_FILENAME, MZ_VALUES)

        assert events == ["read", "the apply finished", "read"]
        assert result.is_timeseries_computed.values.all()

    @pytest.mark.asyncio
    async def test_it_is_logged_as_one_issue_for_error_monitoring_to_count(
        self, monkeypatch, write_peak_store
    ):
        """Nothing else says how often a fill meets a rewritten axis. The
        refusal's message carries the m/z values, so left to group itself
        the event would open an issue close to per file."""
        write_peak_store(SCAN_TIMES, MZ_VALUES, SUM_AREAS, SUM_HEIGHTS)
        _stub_reader(monkeypatch, SCAN_TIMES, 1 + 2e-6, rescaled_reads=1)
        logger = m_compute.runtime.logger
        logger.reset_mock()

        await m_compute.load_peak_timeseries(SIGNAL_TEST_FILENAME, MZ_VALUES)

        logger.bind.assert_called_once_with(
            sentry_fingerprint=["peak-fill-met-a-rewritten-axis"]
        )
        logger.bind.return_value.opt.assert_called_once_with(exception=True)
        warning = logger.bind.return_value.opt.return_value.warning
        warning.assert_called_once()
        assert SIGNAL_TEST_FILENAME in warning.call_args.args[0]

    @pytest.mark.asyncio
    async def test_a_fill_that_is_not_refused_logs_nothing(
        self, monkeypatch, write_peak_store
    ):
        write_peak_store(SCAN_TIMES, MZ_VALUES, SUM_AREAS, SUM_HEIGHTS)
        asked = _stub_reader(monkeypatch, SCAN_TIMES)
        logger = m_compute.runtime.logger
        logger.reset_mock()

        await m_compute.load_peak_timeseries(SIGNAL_TEST_FILENAME, MZ_VALUES)

        assert len(asked) == 1
        logger.bind.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_second_refusal_is_raised(self, monkeypatch, write_peak_store):
        """Once more, not until it works: an axis rewritten during both
        attempts is not a calibration that happened to be applied meanwhile."""
        write_peak_store(SCAN_TIMES, MZ_VALUES, SUM_AREAS, SUM_HEIGHTS)
        asked = _stub_reader(monkeypatch, SCAN_TIMES, 1 + 2e-6, rescaled_reads=2)

        with pytest.raises(m_io.MzNotOnAxisError, match="not present in existing"):
            await m_compute.load_peak_timeseries(SIGNAL_TEST_FILENAME, MZ_VALUES)

        assert len(asked) == 2
        stored = m_io.load_peak_data(SIGNAL_TEST_FILENAME)
        assert not stored.is_timeseries_computed.values.any(), "nothing was written"
        assert np.isnan(stored.peak_areas.values).all()

    @pytest.mark.asyncio
    async def test_no_other_refusal_is_answered_by_computing_again(
        self, monkeypatch, write_peak_store
    ):
        """A store the file has outgrown is refused the same on every read,
        and it is a ValueError as well. Only peak detection repairs it."""
        write_peak_store(SCAN_TIMES, MZ_VALUES, SUM_AREAS, SUM_HEIGHTS)
        asked = _stub_reader(monkeypatch, SCAN_TIMES[1:])

        with pytest.raises(m_compute.StalePeakStoreError):
            await m_compute.load_peak_timeseries(SIGNAL_TEST_FILENAME, MZ_VALUES)

        assert len(asked) == 1


class TestPeaksSharingAnMz:
    """Filling a store two of whose peaks sit on the same m/z.

    A file that switches polarity keeps both polarities' peaks on one axis,
    and a peak of each can hold exactly the same m/z. Such a pair must not
    cost the file's other peaks their timeseries, and the kept peak of a pair
    gets its own.
    """

    # Rows 1 and 2 share an m/z, one of each polarity
    MZ = np.array([100.0, 200.0, 200.0, 300.0])
    AREAS = np.array([1000.0, 2000.0, 3000.0, 4000.0])
    HEIGHTS = np.array([10.0, 20.0, 30.0, 40.0])
    POLARITY = ["+", "+", "-", "+"]

    @pytest.mark.asyncio
    async def test_a_dropped_pair_leaves_the_other_peaks_their_timeseries(
        self, monkeypatch, write_peak_store
    ):
        """Two noise peaks at one m/z: what such a pair usually is."""
        write_peak_store(
            SCAN_TIMES,
            self.MZ,
            self.AREAS,
            self.HEIGHTS,
            is_weak=[False, True, True, False],
            polarity=self.POLARITY,
        )
        _stub_reader(monkeypatch, SCAN_TIMES)

        result = await m_compute.load_peak_timeseries(
            SIGNAL_TEST_FILENAME, [100.0, 300.0]
        )

        np.testing.assert_allclose(
            result.peak_heights.values, np.outer(self.HEIGHTS[[0, 3]], _shares(5))
        )

    @pytest.mark.parametrize("dropped", [1, 2])
    @pytest.mark.asyncio
    async def test_the_kept_peak_of_a_pair_gets_its_own_timeseries(
        self, dropped, monkeypatch, write_peak_store
    ):
        """Scaled to its own summed intensity, on its own row, and for good.

        The fill is written by m/z. Landing on the dropped peak's row it
        would leave the kept one empty and uncomputed, to be read back from
        the file again on every ask.
        """
        kept = 3 - dropped
        is_weak = np.zeros(4, dtype=bool)
        is_weak[dropped] = True
        write_peak_store(
            SCAN_TIMES,
            self.MZ,
            self.AREAS,
            self.HEIGHTS,
            is_weak=is_weak,
            polarity=self.POLARITY,
        )
        _stub_reader(monkeypatch, SCAN_TIMES)

        result = await m_compute.load_peak_timeseries(SIGNAL_TEST_FILENAME, [200.0])

        assert result.peak_id.values.tolist() == [f"peak_{kept:04d}"]
        np.testing.assert_allclose(
            result.peak_heights.values, np.outer(self.HEIGHTS[[kept]], _shares(5))
        )
        np.testing.assert_allclose(
            result.peak_areas.values, np.outer(self.AREAS[[kept]], _shares(5))
        )

        async def refuse(*args, **kwargs):
            raise AssertionError("a computed peak was recomputed")

        monkeypatch.setattr(m_compute, "get_peak_timeseries", refuse)
        again = await m_compute.load_peak_timeseries(SIGNAL_TEST_FILENAME, [200.0])
        assert again.is_timeseries_computed.values.all()


class TestCheckPeakStore:
    """A store peak detection has just written, read back as matching reads it.

    Asked after a rebuild: a store that still disagrees with its file will
    fail matching again, and the refresh that meets it will queue the same
    rebuild again, so it has to be told apart from one that is merely old.
    """

    @pytest.mark.asyncio
    async def test_a_store_the_file_reads_back_passes(
        self, monkeypatch, write_peak_store
    ):
        write_peak_store(SCAN_TIMES, MZ_VALUES, SUM_AREAS, SUM_HEIGHTS)
        _stub_reader(monkeypatch, SCAN_TIMES)

        assert await m_compute.check_peak_store(SIGNAL_TEST_FILENAME) is None

    @pytest.mark.asyncio
    async def test_a_store_the_file_does_not_read_back_is_refused(
        self, monkeypatch, write_peak_store
    ):
        write_peak_store(SCAN_TIMES, MZ_VALUES, SUM_AREAS, SUM_HEIGHTS)
        _stub_reader(monkeypatch, SCAN_TIMES[1:])

        with pytest.raises(m_compute.StalePeakStoreError, match="scan counts differ"):
            await m_compute.check_peak_store(SIGNAL_TEST_FILENAME)

    @pytest.mark.asyncio
    async def test_a_store_without_peaks_does_not_read_the_file(self, monkeypatch):
        """A blank measurement's store has no peak to read the file back for."""
        empty = xr.Dataset(coords={"mz": np.array([]), "time": SCAN_TIMES})
        monkeypatch.setattr(m_io, "load_peak_data", lambda _filename, **_kwargs: empty)

        async def refuse(*args, **kwargs):
            raise AssertionError("the file was read for a store without peaks")

        monkeypatch.setattr(m_compute, "get_peak_timeseries", refuse)

        assert await m_compute.check_peak_store(SIGNAL_TEST_FILENAME) is None


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
