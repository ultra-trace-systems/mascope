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
