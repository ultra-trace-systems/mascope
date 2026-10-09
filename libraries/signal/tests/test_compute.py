import json
import os

import numpy as np
import pytest
import xarray as xr
import zarr
from signal_test_support import SIGNAL_TEST_FILENAME

import mascope_file.name as m_name
import mascope_signal.compute as m_compute


class TestGetSumSignalCaching:
    def test_get_sum_signal_reuses_hashed_cache(
        self,
        monkeypatch,
        sample_file_path,
        signal_dataset,
    ):
        load_count = 0

        def fake_load_signal(base_filename):
            nonlocal load_count
            load_count += 1
            assert base_filename == SIGNAL_TEST_FILENAME
            return signal_dataset

        monkeypatch.setattr(
            m_compute.m_name, "get_sample_file_type", lambda _: "tof_zarr"
        )
        monkeypatch.setattr(m_compute, "load_signal", fake_load_signal)

        result = m_compute.get_sum_signal(SIGNAL_TEST_FILENAME, t_min=0.0, t_max=2.0)
        cached = m_compute.get_sum_signal(SIGNAL_TEST_FILENAME, t_min=0.0, t_max=2.0)

        expected = np.array([12.0, 15.0, 18.0], dtype=np.float64)
        np.testing.assert_allclose(result.compute().values, expected)
        np.testing.assert_allclose(cached.compute().values, expected)
        assert load_count == 1

        cached_name = m_compute._get_sum_signal_hash_name(0.0, 2.0, None, "tof_zarr")
        cache_path = m_name.filename_to_zarr_path(SIGNAL_TEST_FILENAME, cached_name)
        assert cache_path.startswith(sample_file_path)
        assert os.path.exists(cache_path)
        # The sum-signal cache is the most frequently created store in the
        # filestore, so it has to honour the zarr v2 pin like every other one.
        cache_store = zarr.open(cache_path, mode="r")
        assert cache_store.metadata.zarr_format == 2

    def test_get_sum_signal_recovers_from_contains_group_error(
        self,
        monkeypatch,
        sample_file_path,
        signal_dataset,
    ):
        monkeypatch.setattr(
            m_compute.m_name, "get_sample_file_type", lambda _: "tof_zarr"
        )
        monkeypatch.setattr(m_compute, "load_signal", lambda _: signal_dataset)

        original_to_zarr = xr.DataArray.to_zarr
        injected_error = {"raised": False}

        def racing_to_zarr(self, *args, **kwargs):
            original_to_zarr(self, *args, **kwargs)
            if not injected_error["raised"]:
                injected_error["raised"] = True
                raise zarr.errors.ContainsGroupError("")

        monkeypatch.setattr(xr.DataArray, "to_zarr", racing_to_zarr)

        result = m_compute.get_sum_signal(SIGNAL_TEST_FILENAME, t_min=0.0, t_max=2.0)

        expected = np.array([12.0, 15.0, 18.0], dtype=np.float64)
        np.testing.assert_allclose(result.compute().values, expected)
        assert injected_error["raised"] is True

    def test_get_sum_signal_average_after_concurrent_cache_write(
        self,
        monkeypatch,
        sample_file_path,
        signal_dataset,
    ):
        monkeypatch.setattr(
            m_compute.m_name, "get_sample_file_type", lambda _: "tof_zarr"
        )
        monkeypatch.setattr(m_compute, "load_signal", lambda _: signal_dataset)
        monkeypatch.setattr(
            m_compute,
            "get_scan_timestamps",
            lambda *args, **kwargs: np.array([0.0, 1.0, 2.0], dtype=np.float64),
        )

        original_to_zarr = xr.DataArray.to_zarr
        injected_error = {"raised": False}

        def racing_to_zarr(self, *args, **kwargs):
            original_to_zarr(self, *args, **kwargs)
            if not injected_error["raised"]:
                injected_error["raised"] = True
                raise zarr.errors.ContainsGroupError("")

        monkeypatch.setattr(xr.DataArray, "to_zarr", racing_to_zarr)

        result = m_compute.get_sum_signal(
            SIGNAL_TEST_FILENAME,
            t_min=0.0,
            t_max=2.0,
            average=True,
        )

        expected = np.array([4.0, 5.0, 6.0], dtype=np.float64)
        np.testing.assert_allclose(result.compute().values, expected)
        assert injected_error["raised"] is True


def _calibrate(sample_file_path: str, factor: float) -> None:
    """Record a one-point m/z calibration in the test sample's props."""
    calibration = {"mode": "one-point", "par": {"calibration_factor": factor}}
    with open(os.path.join(sample_file_path, ".props"), "w") as f:
        json.dump({"mz_calibration": calibration}, f)


def _profile(values) -> xr.DataArray:
    """A three-point averaged profile, as the raw reader returns one."""
    return xr.DataArray(
        np.asarray(values, dtype=np.float64),
        dims=["mz"],
        coords={"mz": np.array([100.0, 101.0, 102.0])},
        name="sum_signal",
    )


class TestRawOrbitrapSumSignalCache:
    """A raw Orbitrap file's profile is averaged from the raw file on demand and
    cached per window. Nothing in the cache used to say which reader averaged
    it, so a file processed before a reader upgrade kept serving the old
    reader's profile - to the spectrum views and the instrument-function fit
    alike. The cache is now named after what computed it.
    """

    @pytest.fixture(autouse=True)
    def _raw_orbitrap(self, monkeypatch, sample_file_path):
        monkeypatch.setattr(
            m_compute.m_name, "get_sample_file_type", lambda _: "orbi_raw"
        )
        self.computed = []

        def fake_compute_sum_signal(datafile_path, **kwargs):
            self.computed.append(m_compute.averaged_profile_signature())
            return _profile([1.0, 2.0, 3.0]), 1

        monkeypatch.setattr(
            m_compute.m_thermo, "compute_sum_signal", fake_compute_sum_signal
        )

    def test_the_cache_is_named_after_what_computed_it(self, monkeypatch):
        monkeypatch.setattr(m_compute, "averaged_profile_signature", lambda: "otfX-g9")

        full = m_compute._get_sum_signal_hash_name(None, None, None, "orbi_raw")
        window = m_compute._get_sum_signal_hash_name(0.0, 2.0, "+", "orbi_raw")
        assert full == "sum_signal.otfX-g9"
        assert window.startswith("sum_signal_") and window.endswith(".otfX-g9")
        # The other types are not averaged by the raw reader
        tof = m_compute._get_sum_signal_hash_name(0.0, 2.0, "+", "tof_h5")
        assert tof == window.removesuffix(".otfX-g9")

    def test_a_profile_cached_before_the_names_carried_it_is_not_served(
        self, sample_file_path
    ):
        # What an older reader cached for the window, under the name it had then
        window = m_compute._get_sum_signal_hash_name(0.0, 2.0, "+", "orbi_raw")
        unnamed = window.removesuffix(m_compute.sum_signal_suffix("orbi_raw"))
        _profile([9.0, 9.0, 9.0]).to_zarr(
            os.path.join(sample_file_path, f"{unnamed}.zarr")
        )

        result = m_compute.get_sum_signal(SIGNAL_TEST_FILENAME, 0.0, 2.0, "+")

        np.testing.assert_allclose(result.compute().values, [1.0, 2.0, 3.0])
        assert len(self.computed) == 1

    def test_a_reader_change_averages_the_profile_again(self, monkeypatch):
        monkeypatch.setattr(m_compute, "averaged_profile_signature", lambda: "otfA-g2")
        m_compute.get_sum_signal(SIGNAL_TEST_FILENAME, 0.0, 2.0, "+")
        m_compute.get_sum_signal(SIGNAL_TEST_FILENAME, 0.0, 2.0, "+")
        assert self.computed == ["otfA-g2"], "the same reader reads its own cache"

        monkeypatch.setattr(m_compute, "averaged_profile_signature", lambda: "otfB-g2")
        m_compute.get_sum_signal(SIGNAL_TEST_FILENAME, 0.0, 2.0, "+")
        assert self.computed == ["otfA-g2", "otfB-g2"]

    def test_a_full_signal_averaged_after_calibration_is_on_the_calibrated_axis(
        self, sample_file_path
    ):
        """Applying a calibration rescales every stored sum signal in place, so
        a stored axis is the acquisition axis times the current factor. A full
        signal averaged after the file was calibrated - as every one is once a
        new reader renames the cache - has to start there too, like a window.
        """
        _calibrate(sample_file_path, 1.000003)

        full = m_compute.get_sum_signal(SIGNAL_TEST_FILENAME)
        window = m_compute.get_sum_signal(SIGNAL_TEST_FILENAME, 0.0, 2.0, "+")

        calibrated = np.array([100.0, 101.0, 102.0]) * 1.000003
        np.testing.assert_allclose(full.mz.values, calibrated, rtol=0, atol=1e-9)
        np.testing.assert_allclose(window.mz.values, calibrated, rtol=0, atol=1e-9)


class TestOrbitrapZarrSumSignal:
    """An orbi_zarr file keeps no raw file to average: its signal is the stored
    ``signal.zarr``, which a calibration rescales in place along with its sum
    signals and its peaks. Whatever is summed from it, the full signal or a
    window, is on the calibrated axis already, so the factor must not go on
    twice - on a window it used to, which put the window one factor off the
    file's peaks.
    """

    @pytest.mark.parametrize(
        ("t_min", "t_max", "polarity"),
        [
            (None, None, None),
            (0.0, 2.0, None),
            (None, None, "+"),
            (0.0, 2.0, "+"),
        ],
        ids=["full", "time-window", "polarity", "time-window-and-polarity"],
    )
    def test_keeps_the_axis_of_its_stored_signal(
        self, monkeypatch, sample_file_path, signal_dataset, t_min, t_max, polarity
    ):
        monkeypatch.setattr(
            m_compute.m_name, "get_sample_file_type", lambda _: "orbi_zarr"
        )
        monkeypatch.setattr(m_compute, "load_signal", lambda _: signal_dataset)
        _calibrate(sample_file_path, 1.000003)

        summed = m_compute.get_sum_signal(SIGNAL_TEST_FILENAME, t_min, t_max, polarity)

        np.testing.assert_allclose(
            summed.mz.values, signal_dataset.mz.values, rtol=0, atol=1e-9
        )


class TestGetAcquisitionWindow:
    """``get_acquisition_window`` spans every scan type, not just MS1.

    The window a sample item covers used to come from the TIC, which reports
    MS1 scans only. A manual MS2 acquisition records its MS1 scans first and
    the fragmentation afterwards instead of interleaving them, so an
    MS1-derived window ended before the first MS2 scan and every endpoint that
    selects within it found no MS2 data at all.
    """

    def test_asks_the_reader_for_every_scan_type(self, monkeypatch):
        captured = {}

        def fake_scan_timestamps(datafile_path, **kwargs):
            captured.update(kwargs)
            # MS1 scans first, then a block of MS2 scans, as a manual MS2
            # acquisition records them.
            return np.array([1.0, 2.0, 3.0, 40.0, 41.0], dtype=np.float64)

        monkeypatch.setattr(
            m_compute.m_name, "get_sample_file_type", lambda _: "orbi_raw"
        )
        monkeypatch.setattr(
            m_compute.m_name, "filename_to_datafile_path", lambda _: "data.raw"
        )
        monkeypatch.setattr(
            m_compute.m_thermo, "get_scan_timestamps", fake_scan_timestamps
        )

        t0, t1 = m_compute.get_acquisition_window("sample", polarity="+")

        assert captured["scan_type"] is None, (
            "the window must span every scan type; scan_type='Ms' would cut the "
            "MS2 block of a manual MS2 acquisition out of the sample"
        )
        assert captured["polarity"] == "+"
        assert (t0, t1) == (1.0, 41.0)

    def test_falls_back_to_the_scan_axis_for_readers_without_ms2(self, monkeypatch):
        monkeypatch.setattr(
            m_compute.m_name, "get_sample_file_type", lambda _: "tof_zarr"
        )
        monkeypatch.setattr(
            m_compute,
            "get_scan_timestamps",
            lambda *args, **kwargs: np.array([0.5, 1.5, 2.5], dtype=np.float64),
        )

        assert m_compute.get_acquisition_window("sample") == (0.5, 2.5)
