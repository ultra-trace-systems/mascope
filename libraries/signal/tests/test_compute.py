import os

import numpy as np
import xarray as xr
import zarr
from conftest import SIGNAL_TEST_FILENAME

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

        cached_name = m_compute._get_sum_signal_hash_name(0.0, 2.0, None)
        cache_path = m_name.filename_to_zarr_path(SIGNAL_TEST_FILENAME, cached_name)
        assert cache_path.startswith(sample_file_path)
        assert os.path.exists(cache_path)
        # The sum-signal cache is the most frequently created store in the
        # filestore, so it has to honour the zarr v2 pin like every other one.
        assert zarr.open(cache_path, mode="r").metadata.zarr_format == 2

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
