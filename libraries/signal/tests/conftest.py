import os
import shutil
import tempfile
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
import xarray as xr

import mascope_signal.compute as m_compute


SIGNAL_TEST_FILENAME = "OrbiTest_1001.01.01_12h00m00s_TestFile"


@pytest.fixture(scope="session")
def temp_filestore():
    temp_dir = tempfile.mkdtemp(prefix="mascope_signal_test_filestore_")
    yield temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture(autouse=True)
def mock_runtime(temp_filestore):
    def mock_filestore(*args):
        return os.path.join(temp_filestore, *args)

    mock_logger = MagicMock()

    with (
        patch("mascope_file.name.runtime") as mock_name_runtime,
        patch("mascope_file.io.runtime") as mock_io_runtime,
        patch("mascope_signal.compute.runtime") as mock_compute_runtime,
    ):
        mock_name_runtime.filestore = mock_filestore
        mock_io_runtime.filestore = mock_filestore
        mock_io_runtime.logger = mock_logger
        # Its own logger, not the shared one: a test that counts what compute
        # logged must not also be counting what the file library logged
        mock_compute_runtime.logger = MagicMock()

        yield mock_name_runtime


@pytest.fixture
def sample_file_path(temp_filestore):
    sample_path = os.path.join(
        temp_filestore,
        "OrbiTest",
        "1001.01.01",
        SIGNAL_TEST_FILENAME,
    )
    if os.path.exists(sample_path):
        shutil.rmtree(sample_path, ignore_errors=True)
    os.makedirs(sample_path, exist_ok=True)

    props_path = os.path.join(sample_path, ".props")
    with open(props_path, "w") as f:
        f.write('{"mz_calibration": null}')

    yield sample_path

    shutil.rmtree(sample_path, ignore_errors=True)


@pytest.fixture
def compute_logger(mock_runtime):
    """The logger `mascope_signal.compute` writes through.

    `mock_runtime` patches the module's whole runtime, so the library never
    reaches the real sink and `caplog` sees nothing - assert on this instead.
    """
    return m_compute.runtime.logger


@pytest.fixture
def write_peak_store(sample_file_path):
    """Factory writing a peak_timeseries.zarr for the test sample file.

    Mirrors what peak detection allocates: a store carrying the scan axis of
    the acquisition, per-peak summed intensities, and every timeseries still
    uncomputed - the state `load_peak_timeseries` is asked to fill in.
    """

    def _write(
        scan_times: np.ndarray,
        mz_values: np.ndarray,
        sum_peak_areas: np.ndarray,
        sum_peak_heights: np.ndarray,
    ) -> str:
        n_mz = len(mz_values)
        n_time = len(scan_times)
        store = xr.Dataset(
            data_vars={
                "is_satellite": (["mz"], np.zeros(n_mz, dtype=bool)),
                "is_weak": (["mz"], np.zeros(n_mz, dtype=bool)),
                "is_timeseries_computed": (["mz"], np.zeros(n_mz, dtype=bool)),
                "sparsity": (["mz"], np.zeros(n_mz, dtype=np.float64)),
                "peak_areas": (["mz", "time"], np.full((n_mz, n_time), np.nan)),
                "peak_heights": (["mz", "time"], np.full((n_mz, n_time), np.nan)),
                "sum_peak_areas": (["mz"], np.asarray(sum_peak_areas, dtype=float)),
                "sum_peak_heights": (["mz"], np.asarray(sum_peak_heights, dtype=float)),
                "signal_to_noise": (["mz"], np.full(n_mz, 50.0)),
                "polarity": (["mz"], np.array(["-"] * n_mz, dtype="<U1")),
            },
            coords={
                "mz": np.asarray(mz_values, dtype=float),
                "time": np.asarray(scan_times, dtype=float),
                "tof": (["mz"], np.linspace(10.0, 50.0, n_mz)),
                "peak_id": (["mz"], [f"peak_{i:04d}" for i in range(n_mz)]),
            },
        )
        path = os.path.join(sample_file_path, "peak_timeseries.zarr")
        store.to_zarr(
            path,
            mode="w",
            encoding={
                "peak_areas": {"chunks": (n_mz, n_time)},
                "peak_heights": {"chunks": (n_mz, n_time)},
            },
        )
        return path

    return _write


@pytest.fixture
def signal_dataset():
    time_values = np.array([0.0, 1.0, 2.0], dtype=np.float64)
    mz_values = np.array([100.0, 101.0, 102.0], dtype=np.float64)
    signal_values = np.array(
        [
            [1.0, 2.0, 3.0],
            [4.0, 5.0, 6.0],
            [7.0, 8.0, 9.0],
        ],
        dtype=np.float64,
    )
    return xr.Dataset(
        {"signal": (("time", "mz"), signal_values)},
        coords={"time": time_values, "mz": mz_values},
    )
