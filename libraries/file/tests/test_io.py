"""
Tests for mascope_file.io module.
"""

import os
import shutil
import subprocess
import sys

import fasteners
import numpy as np
import pytest
import xarray as xr
import zarr
from conftest import TEST_FILENAME, TEST_MZ_SIZE, TEST_TIME_SIZE

import mascope_file.io as m_io
import mascope_file.name as m_name
from mascope_file.io import ensure_sparsity_exists, write_peaks


class TestWritePeaks:
    """Tests for write_peaks function and related helpers."""

    @pytest.mark.asyncio
    async def test_full_overwrite_creates_new_zarr(
        self,
        create_peak_timeseries_dataset,
        peak_timeseries_zarr_path,
    ):
        """Test that write_peaks creates a new zarr file when none exists."""
        # Ensure no existing file
        if os.path.exists(peak_timeseries_zarr_path):
            shutil.rmtree(peak_timeseries_zarr_path)

        # Create dataset
        ds = create_peak_timeseries_dataset(fill_with_nan=True)

        # Write peaks (should trigger full overwrite since file doesn't exist)
        await write_peaks(ds, TEST_FILENAME, overwrite=False)

        # Verify file was created
        assert os.path.exists(peak_timeseries_zarr_path)

        # Verify structure
        z = zarr.open(peak_timeseries_zarr_path, mode="r")
        assert "mz" in z
        assert "time" in z
        assert "peak_areas" in z
        assert "peak_heights" in z
        assert "is_timeseries_computed" in z
        assert "is_satellite" in z
        assert "is_weak" in z
        assert "sum_peak_areas" in z
        assert "sum_peak_heights" in z
        assert "signal_to_noise" in z
        assert "polarity" in z

        # Verify dimensions
        assert z["mz"].shape == (TEST_MZ_SIZE,)
        assert z["time"].shape == (TEST_TIME_SIZE,)
        assert z["peak_areas"].shape == (TEST_MZ_SIZE, TEST_TIME_SIZE)

        # Cleanup
        shutil.rmtree(peak_timeseries_zarr_path)

    @pytest.mark.asyncio
    async def test_full_overwrite_with_flag(
        self,
        create_peak_timeseries_dataset,
        peak_timeseries_zarr_path,
    ):
        """Test that write_peaks overwrites existing file when overwrite=True."""
        # Create initial file with specific values
        initial_ds = create_peak_timeseries_dataset(fill_with_nan=True)
        await write_peaks(initial_ds, TEST_FILENAME, overwrite=True)

        # Verify initial file
        initial_zarr = zarr.open(peak_timeseries_zarr_path, mode="r")
        original_mz = initial_zarr["mz"][:]

        # Create new dataset with different m/z values
        new_mz = np.linspace(200.0, 600.0, TEST_MZ_SIZE)  # Different range
        overwritten_ds = create_peak_timeseries_dataset(
            mz_values=new_mz, fill_with_nan=True
        )

        # Overwrite
        await write_peaks(overwritten_ds, TEST_FILENAME, overwrite=True)

        # Verify file was overwritten
        overwritten_zarr = zarr.open(peak_timeseries_zarr_path, mode="r")
        new_mz_stored = overwritten_zarr["mz"][:]

        assert not np.allclose(original_mz, new_mz_stored)
        np.testing.assert_allclose(new_mz, new_mz_stored)

        # Cleanup
        shutil.rmtree(peak_timeseries_zarr_path)

    @pytest.mark.asyncio
    async def test_partial_update_single_mz(
        self,
        existing_peak_timeseries_zarr,
        create_update_dataset,
    ):
        """Test partial update of a single m/z value."""
        # Load existing data to get coordinates
        existing = xr.open_zarr(existing_peak_timeseries_zarr)
        base_mz = existing["mz"].values
        time_vals = existing["time"].values

        # Verify initial state - all NaN
        assert np.all(np.isnan(existing["peak_areas"].values))
        existing.close()

        # Create update for a single m/z (index 5)
        update_indices = [5]
        update_ds = create_update_dataset(base_mz, time_vals, update_indices)

        # Perform partial update
        await write_peaks(update_ds, TEST_FILENAME, overwrite=False)

        # Verify the update
        updated = xr.open_zarr(existing_peak_timeseries_zarr)

        # Index 5 should have non-NaN values
        assert not np.any(np.isnan(updated["peak_areas"].isel(mz=5).values))
        assert not np.any(np.isnan(updated["peak_heights"].isel(mz=5).values))
        assert updated["is_timeseries_computed"].isel(mz=5).values

        # Other indices should still be NaN
        for idx in [0, 1, 2, 3, 4, 6, 7, 8, 9]:
            assert np.all(np.isnan(updated["peak_areas"].isel(mz=idx).values))

        updated.close()

    @pytest.mark.asyncio
    async def test_partial_update_multiple_mz(
        self,
        existing_peak_timeseries_zarr,
        create_update_dataset,
    ):
        """Test partial update of multiple m/z values."""
        # Load existing data
        existing = xr.open_zarr(existing_peak_timeseries_zarr)
        base_mz = existing["mz"].values
        time_vals = existing["time"].values
        existing.close()

        # Create update for multiple m/z values (scattered across chunks)
        update_indices = [2, 7, 12, 18]
        update_ds = create_update_dataset(base_mz, time_vals, update_indices)

        # Perform partial update
        await write_peaks(update_ds, TEST_FILENAME, overwrite=False)

        # Verify the updates
        updated = xr.open_zarr(existing_peak_timeseries_zarr)

        for idx in update_indices:
            assert not np.any(np.isnan(updated["peak_areas"].isel(mz=idx).values)), (
                f"Index {idx} should have non-NaN values"
            )
            assert updated["is_timeseries_computed"].isel(mz=idx).values

        # Verify non-updated indices remain NaN
        non_updated = [i for i in range(TEST_MZ_SIZE) if i not in update_indices]
        for idx in non_updated[:5]:  # Check first few
            assert np.all(np.isnan(updated["peak_areas"].isel(mz=idx).values)), (
                f"Index {idx} should still be NaN"
            )

        updated.close()

    @pytest.mark.asyncio
    async def test_partial_update_preserves_existing_data(
        self,
        existing_peak_timeseries_zarr,
        create_update_dataset,
    ):
        """Test that partial update preserves previously computed values."""
        # Load existing data
        existing = xr.open_zarr(existing_peak_timeseries_zarr)
        base_mz = existing["mz"].values
        time_vals = existing["time"].values
        existing.close()

        # First update: indices 0-4
        update1_indices = [0, 1, 2, 3, 4]
        update1_ds = create_update_dataset(base_mz, time_vals, update1_indices)
        await write_peaks(update1_ds, TEST_FILENAME, overwrite=False)

        # Read the values we just wrote
        after_first = xr.open_zarr(existing_peak_timeseries_zarr)
        first_update_values = (
            after_first["peak_areas"].isel(mz=slice(0, 5)).values.copy()
        )
        after_first.close()

        # Second update: indices 5-9
        update2_indices = [5, 6, 7, 8, 9]
        update2_ds = create_update_dataset(base_mz, time_vals, update2_indices)
        await write_peaks(update2_ds, TEST_FILENAME, overwrite=False)

        # Verify first update values are preserved
        final = xr.open_zarr(existing_peak_timeseries_zarr)

        np.testing.assert_array_equal(
            first_update_values,
            final["peak_areas"].isel(mz=slice(0, 5)).values,
            err_msg="First update values should be preserved after second update",
        )

        # Verify second update was applied
        for idx in update2_indices:
            assert not np.any(np.isnan(final["peak_areas"].isel(mz=idx).values))

        final.close()

    @pytest.mark.asyncio
    async def test_partial_update_data_integrity(
        self,
        existing_peak_timeseries_zarr,
        create_update_dataset,
    ):
        """Test that partial update writes correct values."""
        # Load existing data
        existing = xr.open_zarr(existing_peak_timeseries_zarr)
        base_mz = existing["mz"].values
        time_vals = existing["time"].values
        existing.close()

        # Create update with known values
        update_indices = [10]
        update_ds = create_update_dataset(base_mz, time_vals, update_indices)

        # Store the values we're about to write
        expected_areas = update_ds["peak_areas"].values.copy()
        expected_heights = update_ds["peak_heights"].values.copy()

        # Perform update
        await write_peaks(update_ds, TEST_FILENAME, overwrite=False)

        # Verify exact values
        updated = xr.open_zarr(existing_peak_timeseries_zarr)

        np.testing.assert_array_almost_equal(
            expected_areas,
            updated["peak_areas"].isel(mz=10).values.reshape(1, -1),
            err_msg="peak_areas values should match exactly",
        )
        np.testing.assert_array_almost_equal(
            expected_heights,
            updated["peak_heights"].isel(mz=10).values.reshape(1, -1),
            err_msg="peak_heights values should match exactly",
        )

        updated.close()


class TestWritePeaksEdgeCases:
    """Edge case tests for write_peaks."""

    @pytest.mark.asyncio
    async def test_update_all_mz_values(
        self,
        existing_peak_timeseries_zarr,
        create_update_dataset,
    ):
        """Test updating all m/z values at once."""
        existing = xr.open_zarr(existing_peak_timeseries_zarr)
        base_mz = existing["mz"].values
        time_vals = existing["time"].values
        existing.close()

        # Update all indices
        update_indices = list(range(TEST_MZ_SIZE))
        update_ds = create_update_dataset(base_mz, time_vals, update_indices)

        await write_peaks(update_ds, TEST_FILENAME, overwrite=False)

        # Verify all values are now non-NaN
        updated = xr.open_zarr(existing_peak_timeseries_zarr)
        assert not np.any(np.isnan(updated["peak_areas"].values))
        assert not np.any(np.isnan(updated["peak_heights"].values))
        assert np.all(updated["is_timeseries_computed"].values)

        updated.close()

    @pytest.mark.asyncio
    async def test_update_first_and_last_mz(
        self,
        existing_peak_timeseries_zarr,
        create_update_dataset,
    ):
        """Test updating boundary m/z values (first and last)."""
        existing = xr.open_zarr(existing_peak_timeseries_zarr)
        base_mz = existing["mz"].values
        time_vals = existing["time"].values
        existing.close()

        # Update first and last indices
        update_indices = [0, TEST_MZ_SIZE - 1]
        update_ds = create_update_dataset(base_mz, time_vals, update_indices)

        await write_peaks(update_ds, TEST_FILENAME, overwrite=False)

        updated = xr.open_zarr(existing_peak_timeseries_zarr)

        # First and last should be updated
        assert not np.any(np.isnan(updated["peak_areas"].isel(mz=0).values))
        assert not np.any(np.isnan(updated["peak_areas"].isel(mz=-1).values))

        # Middle values should still be NaN
        assert np.all(np.isnan(updated["peak_areas"].isel(mz=10).values))

        updated.close()

    @pytest.mark.asyncio
    async def test_repeated_updates_same_mz(
        self,
        existing_peak_timeseries_zarr,
        create_update_dataset,
    ):
        """Test that repeated updates to the same m/z overwrite previous values."""
        existing = xr.open_zarr(existing_peak_timeseries_zarr)
        base_mz = existing["mz"].values
        time_vals = existing["time"].values
        existing.close()

        # First update with seed=42
        update_indices = [5]
        update1_ds = create_update_dataset(base_mz, time_vals, update_indices, seed=42)
        await write_peaks(update1_ds, TEST_FILENAME, overwrite=False)

        # Read first values
        after_first = xr.open_zarr(existing_peak_timeseries_zarr)
        first_values = after_first["peak_areas"].isel(mz=5).values.copy()
        after_first.close()

        # Second update with different seed for different values
        update2_ds = create_update_dataset(base_mz, time_vals, update_indices, seed=99)
        await write_peaks(update2_ds, TEST_FILENAME, overwrite=False)

        # Read second values
        after_second = xr.open_zarr(existing_peak_timeseries_zarr)
        second_values = after_second["peak_areas"].isel(mz=5).values
        after_second.close()

        # Values should have changed
        assert not np.allclose(first_values, second_values)


class TestEnsureSparsityExists:
    """Tests for ensure_sparsity_exists backwards compatibility function."""

    def test_returns_false_when_sparsity_already_present(
        self,
        existing_peak_timeseries_zarr,
    ):
        """Test that no migration occurs if sparsity already exists."""
        # The fixture now includes sparsity, so it should already be present
        result = ensure_sparsity_exists(TEST_FILENAME)
        assert result is False

    def test_creates_sparsity_for_zarr_without_it(
        self,
        peak_timeseries_zarr_path,
        create_peak_timeseries_dataset,
    ):
        """Test that sparsity is created when missing from zarr file."""
        # Create a dataset WITHOUT sparsity to simulate an old zarr
        ds = create_peak_timeseries_dataset(fill_with_nan=True)
        ds = ds.drop_vars("sparsity")
        ds.to_zarr(peak_timeseries_zarr_path, mode="w")

        # Verify sparsity is missing
        z = zarr.open(peak_timeseries_zarr_path, mode="r")
        assert "sparsity" not in z

        # Run migration
        result = ensure_sparsity_exists(TEST_FILENAME)
        assert result is True

        # Verify sparsity was created
        z = zarr.open(peak_timeseries_zarr_path, mode="r")
        assert "sparsity" in z
        assert z["sparsity"].shape == (TEST_MZ_SIZE,)
        assert z["sparsity"].dtype == np.float64

        # All should be 0.0 since no timeseries was computed
        assert np.all(z["sparsity"][:] == 0.0)

        # Verify xarray dimension metadata
        assert z["sparsity"].attrs["_ARRAY_DIMENSIONS"] == ["mz"]

        # Cleanup
        shutil.rmtree(peak_timeseries_zarr_path)

    def test_computes_sparsity_for_computed_peaks_with_gaps(
        self,
        peak_timeseries_zarr_path,
        create_peak_timeseries_dataset,
    ):
        """Test that sparsity=True for computed peaks with heights <= 0."""
        ds = create_peak_timeseries_dataset(fill_with_nan=False)
        ds = ds.drop_vars("sparsity")

        # Mark some peaks as computed
        ds["is_timeseries_computed"].values[0] = True
        ds["is_timeseries_computed"].values[1] = True
        ds["is_timeseries_computed"].values[2] = True

        # Make peak 0 have a zero height (sparse)
        ds["peak_heights"].values[0, 5] = 0.0

        # Make peak 1 have a negative height (sparse)
        ds["peak_heights"].values[1, 3] = -1.0

        # Peak 2 remains all positive (not sparse)
        ds["peak_heights"].values[2, :] = np.abs(ds["peak_heights"].values[2, :]) + 1.0

        # Make peak 0 also have a NaN height (counts as sparse)
        ds["peak_heights"].values[0, 6] = np.nan

        ds.to_zarr(peak_timeseries_zarr_path, mode="w")

        # Run migration
        result = ensure_sparsity_exists(TEST_FILENAME)
        assert result is True

        # Verify results
        z = zarr.open(peak_timeseries_zarr_path, mode="r")
        sparsity = z["sparsity"][:]
        n_time = ds.sizes["time"]

        assert sparsity[0] == pytest.approx(2.0 / n_time)  # 1 zero + 1 NaN height
        assert sparsity[1] == pytest.approx(1.0 / n_time)  # 1 negative height
        assert sparsity[2] == 0.0  # all positive
        # Uncomputed peaks default to 0.0
        assert np.all(sparsity[3:] == 0.0)

        # Cleanup
        shutil.rmtree(peak_timeseries_zarr_path)

    def test_returns_false_when_zarr_does_not_exist(
        self,
        sample_file_path,
        peak_timeseries_zarr_path,
    ):
        """Test that no error is raised when zarr file doesn't exist."""
        # Ensure the zarr file does not exist
        if os.path.exists(peak_timeseries_zarr_path):
            shutil.rmtree(peak_timeseries_zarr_path)
        result = ensure_sparsity_exists(TEST_FILENAME)
        assert result is False

    @pytest.mark.asyncio
    async def test_partial_update_includes_sparsity(
        self,
        existing_peak_timeseries_zarr,
        create_update_dataset,
    ):
        """Test that partial updates correctly write sparsity values."""
        existing = xr.open_zarr(existing_peak_timeseries_zarr)
        base_mz = existing["mz"].values
        time_vals = existing["time"].values
        existing.close()

        # Create update where some peaks are sparse
        update_indices = [3, 7]
        update_ds = create_update_dataset(base_mz, time_vals, update_indices)

        # Make peak at index 0 of the update (mz index 3) sparse
        update_ds["peak_heights"].values[0, 2] = -1.0

        # Add sparsity to the update dataset
        sparsity_vals = (
            np.sum(update_ds["peak_heights"].values <= 0, axis=1)
            / update_ds.sizes["time"]
        )
        update_ds["sparsity"] = (["mz"], sparsity_vals)

        await write_peaks(update_ds, TEST_FILENAME, overwrite=False)

        # Verify
        updated = xr.open_zarr(existing_peak_timeseries_zarr)
        assert updated["sparsity"].isel(mz=3).values > 0.0  # has negative height
        assert updated["sparsity"].isel(mz=7).values == 0.0  # all positive
        updated.close()


class TestZarrV2Format:
    """Guards that the filestore keeps its zarr v2 on-disk format.

    zarr 3 reads and updates v2 stores in place but creates new ones as v3, so
    mascope_file.io pins the default. A regression here would split the
    filestore across two formats and block a downgrade to zarr 2.
    """

    @pytest.mark.asyncio
    async def test_full_overwrite_creates_v2_store(
        self,
        create_peak_timeseries_dataset,
        peak_timeseries_zarr_path,
    ):
        """A newly created peak_timeseries store must be zarr v2."""
        if os.path.exists(peak_timeseries_zarr_path):
            shutil.rmtree(peak_timeseries_zarr_path)

        ds = create_peak_timeseries_dataset(fill_with_nan=True)
        await write_peaks(ds, TEST_FILENAME, overwrite=True)

        z = zarr.open(peak_timeseries_zarr_path, mode="r")
        assert z.metadata.zarr_format == 2
        # v2 keeps its metadata in dot-files; v3 would write zarr.json instead
        assert ".zgroup" in os.listdir(peak_timeseries_zarr_path)
        assert "zarr.json" not in os.listdir(peak_timeseries_zarr_path)

        shutil.rmtree(peak_timeseries_zarr_path)

    @pytest.mark.asyncio
    async def test_partial_update_keeps_v2_format(
        self,
        existing_peak_timeseries_zarr,
        create_update_dataset,
    ):
        """Updating an existing v2 store must not migrate it to v3."""
        existing = xr.open_zarr(existing_peak_timeseries_zarr)
        base_mz = existing["mz"].values
        time_vals = existing["time"].values
        existing.close()

        update_ds = create_update_dataset(base_mz, time_vals, [2, 5])
        await write_peaks(update_ds, TEST_FILENAME, overwrite=False)

        z = zarr.open(existing_peak_timeseries_zarr, mode="r")
        assert z.metadata.zarr_format == 2

    def test_sparsity_migration_keeps_v2_format(
        self,
        peak_timeseries_zarr_path,
        create_peak_timeseries_dataset,
    ):
        """The sparsity backfill must add a v2 array to a v2 store.

        Covers the zarr 3 port of this path: Group.create_dataset was removed,
        so it now uses create_array plus an explicit assignment.
        """
        ds = create_peak_timeseries_dataset(fill_with_nan=True)
        ds = ds.drop_vars("sparsity")
        ds.to_zarr(peak_timeseries_zarr_path, mode="w")

        assert ensure_sparsity_exists(TEST_FILENAME) is True

        z = zarr.open(peak_timeseries_zarr_path, mode="r")
        assert z.metadata.zarr_format == 2
        assert z["sparsity"].metadata.zarr_format == 2
        # The backfilled variable must be visible through xarray, which needs
        # both the consolidated metadata and the _ARRAY_DIMENSIONS attribute
        reopened = xr.open_zarr(peak_timeseries_zarr_path)
        assert "sparsity" in reopened.data_vars
        assert reopened["sparsity"].dims == ("mz",)
        reopened.close()

        shutil.rmtree(peak_timeseries_zarr_path)


class TestZarrWriteLock:
    """Guards the cross-process write lock that replaced zarr's synchronizer.

    zarr 3 removed ProcessSynchronizer, and still accepts a ``synchronizer=``
    argument that it silently ignores, so a regression here would be invisible
    at runtime: the backend workers and the file converter would stop being
    serialized against each other.
    """

    def test_lock_file_lives_beside_the_store(self, peak_timeseries_zarr_path):
        """The lock must not sit inside the store, which gets overwritten."""
        lock_path = os.fsdecode(
            m_io.get_zarr_process_lock(peak_timeseries_zarr_path).path
        )
        assert not lock_path.startswith(peak_timeseries_zarr_path + os.sep)
        assert os.path.dirname(lock_path) == os.path.dirname(peak_timeseries_zarr_path)

    def test_lock_excludes_another_process(self, peak_timeseries_zarr_path):
        """A second OS process must not hold the lock at the same time."""
        lock_path = os.fsdecode(
            m_io.get_zarr_process_lock(peak_timeseries_zarr_path).path
        )
        probe = (
            "import sys, fasteners;"
            "lock = fasteners.InterProcessLock(sys.argv[1]);"
            "acquired = lock.acquire(blocking=False);"
            "print('ACQUIRED' if acquired else 'BLOCKED');"
            "lock.release() if acquired else None"
        )

        def probe_other_process() -> str:
            return subprocess.run(
                [sys.executable, "-c", probe, lock_path],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()

        with m_io.zarr_write_lock(peak_timeseries_zarr_path):
            assert probe_other_process() == "BLOCKED"

        # Released again once the context manager exits
        assert probe_other_process() == "ACQUIRED"

    def test_lock_is_released_between_sequential_writes(
        self, peak_timeseries_zarr_path
    ):
        """The lock must be free again after each write, not just not deadlock.

        Named for what it checks: the inter-process lock is not reentrant, so
        the property worth pinning is that leaving the context manager really
        releases the file lock rather than leaking it to the next writer.
        """
        lock_path = os.fsdecode(
            m_io.get_zarr_process_lock(peak_timeseries_zarr_path).path
        )
        for _ in range(3):
            with m_io.zarr_write_lock(peak_timeseries_zarr_path):
                pass
            # An independent lock object must be able to take it between writes
            probe = fasteners.InterProcessLock(lock_path)
            assert probe.acquire(blocking=False), "lock was not released"
            probe.release()


class TestStaleConsolidatedMetadata:
    """Guards the sparsity backfill against a stale .zmetadata.

    zarr 2 always listed the store to answer membership; zarr 3 answers from
    .zmetadata when it is present. A sparsity array can exist on disk while
    .zmetadata does not list it - the backfill is interrupted between creating
    the array and reconsolidating - and reading the consolidated view there
    reports it missing, so the backfill tries to create it again and the store
    becomes permanently unreadable.
    """

    @staticmethod
    def _make_store_with_stale_metadata(path, create_peak_timeseries_dataset):
        """Create a store whose sparsity array is absent from .zmetadata."""
        ds = create_peak_timeseries_dataset(fill_with_nan=True).drop_vars("sparsity")
        ds.to_zarr(path, mode="w")
        zmetadata_path = os.path.join(path, ".zmetadata")
        with open(zmetadata_path) as f:
            without_sparsity = f.read()

        # Real backfill: creates the array on disk and reconsolidates
        assert ensure_sparsity_exists(TEST_FILENAME) is True
        assert os.path.isdir(os.path.join(path, "sparsity"))

        # Roll .zmetadata back, as an interrupted backfill would leave it
        with open(zmetadata_path, "w") as f:
            f.write(without_sparsity)
        return zmetadata_path

    def test_backfill_repairs_stale_metadata(
        self,
        peak_timeseries_zarr_path,
        create_peak_timeseries_dataset,
    ):
        """A sparsity array missing only from .zmetadata is repaired, not recreated."""
        self._make_store_with_stale_metadata(
            peak_timeseries_zarr_path, create_peak_timeseries_dataset
        )

        # Must report "already present" and reconsolidate, not raise
        assert ensure_sparsity_exists(TEST_FILENAME) is False

        # And the repair must make it visible to xarray again
        reopened = xr.open_zarr(peak_timeseries_zarr_path)
        assert "sparsity" in reopened.data_vars
        reopened.close()

        shutil.rmtree(peak_timeseries_zarr_path)

    def test_peak_load_survives_stale_metadata(
        self,
        peak_timeseries_zarr_path,
        create_peak_timeseries_dataset,
    ):
        """load_peak_data must not be permanently broken by a stale .zmetadata."""
        self._make_store_with_stale_metadata(
            peak_timeseries_zarr_path, create_peak_timeseries_dataset
        )

        # Reading twice matters: the first call is what repairs the metadata,
        # and a store that cannot self-heal fails identically every time.
        first = m_io.load_peak_data(TEST_FILENAME)
        assert "sparsity" in first.data_vars
        first.close()
        second = m_io.load_peak_data(TEST_FILENAME)
        assert "sparsity" in second.data_vars
        second.close()

        shutil.rmtree(peak_timeseries_zarr_path)


class TestBatchCacheStore:
    """Covers write_batch_cache, the remaining store-creating path.

    It creates a store from scratch, so it is subject to the same zarr v3
    default as write_peaks, and it takes the write lock that replaced the
    synchronizer.
    """

    @pytest.fixture
    def batch_dataset(self):
        return xr.Dataset(
            {"intensity": (("mz",), np.arange(8, dtype=np.float64))},
            coords={"mz": np.linspace(100.0, 200.0, 8)},
        )

    def test_write_batch_cache_creates_v2_store(self, batch_dataset):
        """A batch cache store must be created as zarr v2, like every other."""
        batch_id = "test-batch-v2"
        m_io.write_batch_cache(batch_id, "batch_peaks", batch_dataset)

        var_path = os.path.join(
            m_name.get_batch_cache_path(batch_id), "batch_peaks.zarr"
        )
        assert zarr.open(var_path, mode="r").metadata.zarr_format == 2
        assert "zarr.json" not in os.listdir(var_path)

        m_io.delete_batch_cache(batch_id)

    def test_batch_cache_roundtrips(self, batch_dataset):
        """Values written must come back unchanged through load_batch_cache."""
        batch_id = "test-batch-roundtrip"
        m_io.write_batch_cache(batch_id, "batch_peaks", batch_dataset)

        loaded = m_io.load_batch_cache(batch_id, "batch_peaks")
        assert np.allclose(
            loaded["intensity"].values, batch_dataset["intensity"].values
        )
        assert np.allclose(loaded["mz"].values, batch_dataset["mz"].values)
        loaded.close()

        m_io.delete_batch_cache(batch_id)

    def test_delete_batch_cache_removes_the_store(self, batch_dataset):
        """Deleting the cache must remove the directory it created."""
        batch_id = "test-batch-delete"
        m_io.write_batch_cache(batch_id, "batch_peaks", batch_dataset)
        batch_path = m_name.get_batch_cache_path(batch_id)
        assert os.path.isdir(batch_path)

        m_io.delete_batch_cache(batch_id)
        assert not os.path.exists(batch_path)


class TestMissingStoreRaisesFileNotFound:
    """Pins the exception type callers catch for an absent store.

    zarr 2 raised zarr.errors.PathNotFoundError, a ValueError, which did not
    cover the FileNotFoundError load_coord raises for a path that is not there.
    zarr 3 removed that class and raises GroupNotFoundError, a subclass of
    FileNotFoundError, so a single except FileNotFoundError covers both. The
    calibration handlers depend on exactly that.
    """

    def test_load_coord_raises_file_not_found(self, sample_file_path):
        """A variable with no directory at all raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            m_io.load_coord(TEST_FILENAME, "peak_timeseries", "mz")

    def test_load_array_raises_file_not_found(self, sample_file_path):
        with pytest.raises(FileNotFoundError):
            m_io.load_array(TEST_FILENAME, "peak_timeseries")

    def test_zarr_missing_group_is_a_file_not_found(self, sample_file_path):
        """A directory that exists but holds no zarr store raises the same kind.

        This is the case load_coord's own os.path.exists guard does not catch,
        and the one that used to surface as PathNotFoundError.
        """
        not_a_store = os.path.join(sample_file_path, "peak_timeseries.zarr")
        os.makedirs(not_a_store, exist_ok=True)
        try:
            with pytest.raises(FileNotFoundError):
                zarr.open(not_a_store, mode="r")
        finally:
            shutil.rmtree(not_a_store, ignore_errors=True)
