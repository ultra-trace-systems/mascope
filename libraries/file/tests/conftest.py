"""
Fixtures for mascope_file testing.
Provides test data generators, mocked dependencies, and utilities
for testing I/O functions in isolation.
"""

import os
import shutil
import tempfile
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
import xarray as xr


TEST_FILENAME = "OrbiTest_1001.01.01_12h00m00s_TestFile"
TEST_MZ_SIZE = 20
TEST_TIME_SIZE = 50


@pytest.fixture(scope="session")
def temp_filestore():
    """Create a temporary filestore directory that persists for the test session.

    The directory structure will be:
        temp_dir/OrbiTest/1001.01.01/OrbiTest_1001.01.01_12h00m00s_TestFile/

    Yields the path to the temporary filestore root.
    """
    temp_dir = tempfile.mkdtemp(prefix="mascope_test_filestore_")
    yield temp_dir
    # Cleanup after all tests complete
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture(autouse=True)
def mock_runtime(temp_filestore):
    """Mock the runtime filestore method to use the temporary directory.

    This fixture patches the runtime in mascope_file.name and mascope_file.io
    modules to use our temporary filestore.

    autouse=True ensures this is applied to all tests automatically.
    """

    # Create a mock that returns paths within temp_filestore
    def mock_filestore(*args):
        return os.path.join(temp_filestore, *args)

    mock_logger = MagicMock()

    # Patch both modules that use runtime.filestore
    with (
        patch("mascope_file.name.runtime") as mock_name_runtime,
        patch("mascope_file.io.runtime") as mock_io_runtime,
    ):
        mock_name_runtime.filestore = mock_filestore
        mock_io_runtime.filestore = mock_filestore
        mock_io_runtime.logger = mock_logger

        yield mock_name_runtime


@pytest.fixture
def sample_file_path(temp_filestore):
    """Create the sample file directory structure.

    Returns the full path to the sample file directory.
    """
    sample_path = os.path.join(
        temp_filestore,
        "OrbiTest",
        "1001.01.01",
        TEST_FILENAME,
    )
    os.makedirs(sample_path, exist_ok=True)

    # Create a minimal .props file (required by some functions)
    props_path = os.path.join(sample_path, ".props")
    with open(props_path, "w") as f:
        f.write('{"test": true}')

    yield sample_path


@pytest.fixture
def create_peak_timeseries_dataset():
    """Factory fixture to create a peak_timeseries xr.Dataset.

    Returns a function that creates datasets with configurable parameters.
    """

    def _create(
        mz_size: int = TEST_MZ_SIZE,
        time_size: int = TEST_TIME_SIZE,
        fill_with_nan: bool = True,
        mz_values: np.ndarray | None = None,
        time_values: np.ndarray | None = None,
        seed: int = 42,
    ) -> xr.Dataset:
        """Create a peak_timeseries dataset.

        :param mz_size: Number of m/z values
        :param time_size: Number of time points
        :param fill_with_nan: If True, peak_areas and peak_heights are NaN
        :param mz_values: Custom m/z coordinate values
        :param time_values: Custom time coordinate values
        :param seed: Random seed for reproducibility
        :return: xr.Dataset with peak_timeseries structure
        """
        if mz_values is None:
            mz_values = np.linspace(100.0, 500.0, mz_size)
        if time_values is None:
            time_values = np.linspace(0.0, 100.0, time_size)

        n_mz = len(mz_values)
        n_time = len(time_values)

        # Create coordinates
        tof_values = np.linspace(10.0, 50.0, n_mz)
        peak_ids = [f"peak_{i:04d}" for i in range(n_mz)]

        # Create data variables
        rng = np.random.default_rng(seed)

        if fill_with_nan:
            peak_areas = np.full((n_mz, n_time), np.nan)
            peak_heights = np.full((n_mz, n_time), np.nan)
        else:
            peak_areas = rng.uniform(0, 1000, (n_mz, n_time))
            peak_heights = rng.uniform(0, 100, (n_mz, n_time))

        # 1D variables (per mz)
        is_satellite = np.zeros(n_mz, dtype=bool)
        is_weak = np.zeros(n_mz, dtype=bool)
        is_timeseries_computed = np.zeros(n_mz, dtype=bool)
        sparsity = np.zeros(n_mz, dtype=np.float64)
        sum_peak_areas = np.nansum(peak_areas, axis=1)
        sum_peak_heights = np.nansum(peak_heights, axis=1)
        signal_to_noise = rng.uniform(5, 100, n_mz)
        polarity = np.array(["+"] * n_mz, dtype="<U1")

        ds = xr.Dataset(
            data_vars={
                "is_satellite": (["mz"], is_satellite),
                "is_weak": (["mz"], is_weak),
                "is_timeseries_computed": (["mz"], is_timeseries_computed),
                "sparsity": (["mz"], sparsity),
                "peak_areas": (["mz", "time"], peak_areas),
                "peak_heights": (["mz", "time"], peak_heights),
                "sum_peak_areas": (["mz"], sum_peak_areas),
                "sum_peak_heights": (["mz"], sum_peak_heights),
                "signal_to_noise": (["mz"], signal_to_noise),
                "polarity": (["mz"], polarity),
            },
            coords={
                "mz": mz_values,
                "time": time_values,
                "tof": (["mz"], tof_values),
                "peak_id": (["mz"], peak_ids),
            },
        )

        return ds

    return _create


@pytest.fixture
def peak_timeseries_zarr_path(sample_file_path):
    """Return the path where peak_timeseries.zarr should be created.

    Removes the store afterwards whatever happens. Tests that assert before
    their own cleanup would otherwise leak it into the next test, and the
    tests that require the store to be *absent* then fail too - turning one
    real regression into a handful of unrelated-looking failures.
    """
    path = os.path.join(sample_file_path, "peak_timeseries.zarr")
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def existing_peak_timeseries_zarr(
    peak_timeseries_zarr_path,
    create_peak_timeseries_dataset,
):
    """Create an existing peak_timeseries.zarr file for partial update tests.

    Creates a zarr file with NaN values for peak_areas and peak_heights,
    simulating the initial state before timeseries computation.

    Yields the path to the created zarr file.
    """
    # Create dataset with NaN timeseries
    ds = create_peak_timeseries_dataset(fill_with_nan=True)

    # Calculate chunk size for efficient storage
    mz_chunk_size = min(10, len(ds.mz))  # Small chunks for testing
    time_chunk_size = len(ds.time)  # Full time dimension per chunk

    # Write to zarr with explicit chunking
    encoding = {
        "peak_areas": {"chunks": (mz_chunk_size, time_chunk_size)},
        "peak_heights": {"chunks": (mz_chunk_size, time_chunk_size)},
    }

    ds.to_zarr(peak_timeseries_zarr_path, mode="w", encoding=encoding)

    yield peak_timeseries_zarr_path

    # Cleanup
    if os.path.exists(peak_timeseries_zarr_path):
        shutil.rmtree(peak_timeseries_zarr_path, ignore_errors=True)


@pytest.fixture
def create_update_dataset(create_peak_timeseries_dataset):
    """Factory fixture to create a dataset for partial updates.

    Returns a function that creates a subset dataset with computed values
    for specific m/z indices.
    """

    def _create(
        base_mz_values: np.ndarray,
        time_values: np.ndarray,
        update_indices: list[int],
        seed: int = 42,
    ) -> xr.Dataset:
        """Create an update dataset for specific m/z indices.

        :param base_mz_values: Full m/z coordinate from the base dataset
        :param time_values: Time coordinate from the base dataset
        :param update_indices: Indices of m/z values to include in update
        :param seed: Random seed for reproducibility
        :return: xr.Dataset with computed values for the specified indices
        """
        mz_subset = base_mz_values[update_indices]

        # Create a dataset with actual computed values (not NaN)
        ds = create_peak_timeseries_dataset(
            mz_values=mz_subset,
            time_values=time_values,
            fill_with_nan=False,
            seed=seed,
        )

        # Mark as computed
        ds["is_timeseries_computed"].values[:] = True

        return ds

    return _create
