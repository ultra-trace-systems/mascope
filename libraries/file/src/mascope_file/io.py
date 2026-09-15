"""Storage/zarr I/O infrastructure (locks, load/save arrays)"""

import asyncio
import fnmatch
import glob
import json
import os
import threading
from contextlib import contextmanager
from shutil import rmtree
from typing import Iterator

import fasteners
import numpy as np
import xarray as xr
import zarr

import mascope_file.name as m_name
from mascope_file.runtime import runtime


# Keep writing zarr v2 stores. The filestore is full of v2 data written by
# zarr 2.x; zarr 3 reads and updates those in place, but would create any *new*
# store as v3. Pinning the default keeps the filestore in one format and keeps
# a downgrade to zarr 2 possible.
zarr.config.set({"default_zarr_format": 2})

CONCURRENT_WRITE_LIMIT = 2  # Max number of concurrent writes to prevent OutOfMemory
# Longest a writer waits for another process to finish its write before giving
# up. Generous enough for a full peak_timeseries rewrite over a slow filestore,
# bounded so that a wedged or killed holder surfaces as an error instead of
# blocking a uvicorn worker forever - fasteners' own default is to wait
# indefinitely.
ZARR_PROCESS_LOCK_TIMEOUT = 300.0
# Global lock for zarr file writes - prevents concurrent modifications
_zarr_write_locks: dict[str, threading.Lock] = {}
_zarr_write_locks_lock = threading.Lock()


def open_zarr_store(zarr_path: str, mode: str = "r") -> zarr.Group:
    """Open a zarr store for listing or membership tests, bypassing .zmetadata.

    zarr 3 answers ``x in group``, ``group_keys()`` and ``groups()`` from a
    store's consolidated metadata when it is present, where zarr 2 always
    listed the store. So a store whose ``.zmetadata`` is stale - the window
    between creating an array or group and reconsolidating, reachable via an
    OOM kill, a container restart or a deploy mid-backfill - reports arrays and
    groups that exist on disk as absent.

    On the write paths that is silent rather than loud: the loop over
    ``groups()`` in :func:`update_zarr_array_coord` skips a group and the
    ``not in z`` guard in :func:`_process_chunk_sync` skips a variable, with no
    exception and no log line. Measured on identical on-disk state, zarr 2.18.2
    lists all groups and zarr 3.3.0 lists only the consolidated subset.

    Every membership test and store listing therefore goes through here so that
    it reads the live store. Bulk data reads through ``xr.open_zarr`` are left
    on the consolidated fast path deliberately - they are not membership tests,
    and the sparsity backfill repairs ``.zmetadata`` before they run.

    :param zarr_path: Path to the zarr store
    :type zarr_path: str
    :param mode: File mode, defaults to "r"
    :type mode: str, optional
    :return: Opened zarr group that lists the live store
    :rtype: zarr.Group
    """
    return zarr.open(zarr_path, mode=mode, use_consolidated=False)


def get_zarr_write_lock(zarr_path: str) -> threading.Lock:
    """Get or create a write lock for a specific zarr file.

    :param zarr_path: Path to the zarr file
    :return: Threading lock for this file
    """
    with _zarr_write_locks_lock:
        if zarr_path not in _zarr_write_locks:
            _zarr_write_locks[zarr_path] = threading.Lock()
        return _zarr_write_locks[zarr_path]


def get_zarr_process_lock(zarr_path: str) -> fasteners.InterProcessLock:
    """Get a cross-process file lock guarding a specific zarr file.

    The backend (several uvicorn workers) and the file converter run as separate
    processes over a shared filestore, so a threading lock alone does not
    serialize writes. zarr 2 covered this with ``zarr.ProcessSynchronizer``,
    which zarr 3 removed; this is the same ``fasteners`` lock it used, applied
    around whole read-modify-write operations rather than per chunk.

    The lock file lives *beside* the store, not inside it, so that overwriting
    a store does not delete the lock currently being held.

    :param zarr_path: Path to the zarr file
    :type zarr_path: str
    :return: Inter-process lock for this file
    :rtype: fasteners.InterProcessLock
    """
    parent_dir = os.path.dirname(zarr_path)
    lock_name = os.path.basename(zarr_path).replace(".zarr", "") + ".lock"
    os.makedirs(parent_dir, exist_ok=True)
    return fasteners.InterProcessLock(os.path.join(parent_dir, lock_name))


@contextmanager
def zarr_write_lock(
    zarr_path: str, timeout: float | None = ZARR_PROCESS_LOCK_TIMEOUT
) -> Iterator[None]:
    """Serialize writes to a zarr file across both threads and processes.

    Always take the thread lock first, then the file lock, so the ordering is
    the same everywhere and cannot deadlock.

    The file lock is taken with a bounded wait. ``fasteners`` waits forever by
    default, and these writes are entered synchronously from async request
    handlers, so an unbounded wait on a holder in *another* process (a sibling
    uvicorn worker, or the file converter) can pin a worker's event loop
    indefinitely. A timeout turns that into a visible error instead.

    :param zarr_path: Path to the zarr file
    :type zarr_path: str
    :param timeout: Seconds to wait for the cross-process lock, None to wait
        forever, defaults to :data:`ZARR_PROCESS_LOCK_TIMEOUT`
    :type timeout: float | None, optional
    :raises TimeoutError: Another process held the lock for longer than
        ``timeout``
    """
    with get_zarr_write_lock(zarr_path):
        process_lock = get_zarr_process_lock(zarr_path)
        if not process_lock.acquire(timeout=timeout):
            raise TimeoutError(
                f"Timed out after {timeout}s waiting for another process to "
                f"finish writing {zarr_path}"
            )
        try:
            yield
        finally:
            process_lock.release()


def remove_path(path: str) -> None:
    """Delete a store directory or a side-car file left beside one.

    The glob-and-delete sweeps over a sample directory match a store's
    side-car as well as the store itself. Under zarr 2 that side-car was the
    ``<var>.sync`` *directory*, which ``rmtree`` removed happily; the lock that
    replaced it is a plain *file*, and ``rmtree`` raises ``NotADirectoryError``
    on those. Handle both so the sweeps stay total.

    :param path: Path to remove
    :type path: str
    """
    if os.path.isdir(path):
        rmtree(path)
    elif os.path.exists(path):
        os.remove(path)


def get_file_data_vars(filepath):
    """Get list of available variables in a sample file

    :param filepath: Full path to the sample file
    :type filepath: str
    :return: List of available variables
    :rtype: list
    """
    file_dirs = next(os.walk(filepath))[1]
    zarrs = []
    for var in fnmatch.filter(file_dirs, "*.zarr"):
        zarrs.append(var.removesuffix(".zarr"))
    return zarrs


def load_array(base_filename, var, prev_array=None):
    """Load a stored mfzarr variable from file into a xr.Dataset object.
       If the variable receives another chunk of mfzarr data, then subsequent
       load_array calls with non-empty prev_array will update
       previously created dataset from the updated variable.

    :param base_filename: Sample file filename
    :type base_filename: str
    :param var: Sample file variable name
    :type var: str
    :param prev_array: Previously loaded array to update with the updated var, defaults to None
    :type prev_array: xr.DataArray, optional
    :raises FileNotFoundError: No such sample file or variable in it.
    :return: Loaded sample file object
    :rtype: xr.Dataset
    """
    runtime.logger.debug(f"Loading array {base_filename} : {var}")
    var_path = m_name.filename_to_zarr_path(base_filename, var)

    if not os.path.exists(var_path):
        if var == "signal":
            # INFO: usage hint next to the raised FileNotFoundError
            runtime.logger.info("Use load_signal to access signal array")
        raise FileNotFoundError(var_path)

    # Load data from file
    def is_multifile():
        z = open_zarr_store(var_path)
        groups = list(z.group_keys())
        return bool(len(groups))

    if is_multifile():
        # Multi-file (grouped)
        dataset = open_mfzarr(var_path, prev_array=prev_array)
    else:
        # Single file
        dataset = xr.open_zarr(var_path)

    return dataset


def load_coord(base_filename, var, coord_name):
    """Load coordinate array of a sample file

    :param base_filename: Sample file filename
    :type base_filename: str
    :param var: Sample file variable name
    :type var: str
    :param coord_name: Coordinate axis name
    :type coord_name: str
    :return: Requested coordinate array
    :rtype: np.array
    """
    var_path = m_name.filename_to_zarr_path(base_filename, var)

    if not os.path.exists(var_path):
        if var == "signal":
            # INFO: usage hint next to the raised FileNotFoundError
            runtime.logger.info("Use load_signal to access signal array")
        raise FileNotFoundError(var_path)

    z = open_zarr_store(var_path)
    coord = z[coord_name]
    coord_array = coord[:]
    # Check if array is empty
    if not coord_array.size:
        # Perhaps the coordinate is hiding in groups
        # Get list of groups
        groups = list(z.group_keys())

        # Load time coordinate from each group and concatenate
        coord_arrays = [z[group][coord_name][:] for group in groups]
        coord_array = np.concatenate(coord_arrays)

    return coord_array


def load_peak_data(base_filename: str, drop_bad_peaks: bool = True) -> xr.Dataset:
    """Load peak data from sample file.
    The function DOES NOT guarantee that the timeseries data is complete.

    Peak data has the following structure:
    Dimensions:
    - mz
    - time
    Coordinates:
    - peak_id (mz)
    - time (time)
    - tof (mz)
    Data variables:
    - is_satellite (mz)
    - sparsity (mz)
    - is_timeseries_computed (mz)
    - is_weak (mz)
    - peak_areas (mz, time)
    - peak_heights (mz, time)
    - polarity (mz)
    - signal_to_noise (mz)
    - sum_peak_areas (mz)
    - sum_peak_heights (mz)

    :param base_filename: Sample file filename
    :type base_filename: str
    :param drop_bad_peaks: Flag to drop weak and satellite peaks, defaults to True
    :type drop_bad_peaks: bool, optional
    :return: Loaded peak data with sample file properties attached
    :rtype: xr.Dataset
    """
    peak_data = load_array(base_filename, var="peak_timeseries")
    # Ensure backwards compatibility: add sparsity if missing
    if "sparsity" not in peak_data.data_vars:
        ensure_sparsity_exists(base_filename)
        peak_data = load_array(base_filename, var="peak_timeseries")
    # Replace any NaN sparsity values with 0.0 (can occur if zarr chunks were
    # never physically written — xarray uses fill_value=NaN for float64 by default)
    peak_data["sparsity"] = peak_data.sparsity.fillna(0.0)
    if drop_bad_peaks:
        bad_peak_mask = peak_data.is_weak | peak_data.is_satellite
        peak_data = peak_data.sel(mz=peak_data.mz.values[~bad_peak_mask])
    # Add zarr file properties to attributes for reverse compatibility
    props = read_props(base_filename)
    peak_data.attrs["props"] = props
    return peak_data


def load_file(base_filename, vars=None, prev_dataset=None):
    """Load stored mfzarr variables into an xr.Dataset object.
       If the variables receive another chunk of data, then subsequent
       load_file calls with non-empty prev_dataset will update
       previously created dataset from updated variables.

    :param base_filename: Sample file filename
    :type base_filename: str
    :param vars: List of variable (zarr array) names to load, defaults to None. If None, all variables are loaded.
    :type vars: list, optional
    :param prev_dataset: Previously loaded dataset to update with updated vars, defaults to None.
    :type prev_dataset: xr.Dataset, optional
    :raises FileNotFoundError: No such sample file
    :return: Loaded sample file object
    :rtype: xr.Dataset
    """

    filepath = m_name.parse_path_from_item_filename(base_filename)
    if not os.path.exists(filepath):
        # No log here: the raised FileNotFoundError is logged by the caller
        raise FileNotFoundError(filepath)
    if vars is None:
        # Get all saved variable names
        zarrs = get_file_data_vars(filepath)
        vars = [zarr.removesuffix(".zarr") for zarr in zarrs]
    if "signal" in vars:
        # INFO: usage deprecation, fires on every affected load_file call
        runtime.logger.info(
            "Loading signal with load_file is depricated. Use load_signal instead."
        )
        vars.pop(vars.index("signal"))
    # Load arrays from mfzarrs
    runtime.logger.info(f"Loading {', '.join(vars)} from {base_filename}")
    datasets = []
    zarr_groups = {}
    # Load requested data arrays
    for var in vars:
        prev_item = None if prev_dataset is None else prev_dataset.get(var)
        if prev_item is not None:
            prev_item.attrs["zarr_groups"] = prev_dataset.attrs.get(
                "zarr_groups", {}
            ).get(var, [])
        try:
            var_dataset = load_array(base_filename, var, prev_item)
        except FileNotFoundError:
            # Expected for optional variables missing from older files
            runtime.logger.info(f"[load_file] {var} not found for {base_filename}:")
            continue
        datasets.append(var_dataset)
        zarr_groups[var] = var_dataset.attrs.get("zarr_groups", [])
    # Add previously loaded arrays
    if prev_dataset is not None:
        for prev_var, prev_var_dataset in prev_dataset.data_vars.items():
            if prev_var not in vars:
                datasets.append(prev_var_dataset)
    # Merge datasets per variable into one dataset
    dataset = xr.merge(datasets)
    # Load properties
    prop_path = os.path.join(filepath, ".props")
    with open(prop_path, "r") as f:
        props = json.load(f)
    # Attach to dataset
    dataset.attrs["props"] = props
    dataset.attrs["zarr_groups"] = zarr_groups
    return dataset


def open_mfzarr(path, mode="r", concat_dim="time", prev_array=None):
    """Load data from a multi-file zarr into a xr.Dataset

    :param path: Full path to the multi-file zarr array
    :type path: str
    :param mode: File mode, defaults to "r"
    :type mode: str, optional
    :param concat_dim: Dimension name along which to concatenate the files, defaults to "time"
    :type concat_dim: str, optional
    :param prev_array: Previously loaded array to update with new mfzarr data chunk, defaults to None
    :type prev_array: xr.DataArray, optional
    :return: Concatenated data
    :rtype: xr.Dataset
    """

    z = open_zarr_store(path, mode=mode)
    groups = list(z.group_keys())

    if prev_array is not None:
        prev_groups = prev_array.attrs.get("zarr_groups", [])
        for g in prev_groups:
            runtime.logger.debug(f"group {g} already loaded")
            groups.remove(g)
    if not groups:
        runtime.logger.debug("no new groups")
        return prev_array
    runtime.logger.debug(f"loading groups: {groups}")
    x = xr.concat(
        [xr.open_zarr(path, g, consolidated=False) for g in groups],
        concat_dim,
    )
    if prev_array is not None:
        x = xr.concat([prev_array.to_dataset(), x], concat_dim)
    x.attrs = z.attrs.asdict()
    x.attrs["zarr_groups"] = groups
    return x


def read_props(base_filename: str) -> dict:
    """Read properties from a sample file's .props JSON.

    :param base_filename: Sample file filename
    :type base_filename: str
    :return: Properties dictionary
    :rtype: dict
    """
    prop_path = os.path.join(
        m_name.parse_path_from_item_filename(base_filename), ".props"
    )
    with open(prop_path, "r") as f:
        return json.load(f)


def write_props(base_filename, props):
    """Write properties into sample file.

    :param base_filename: Sample file filename
    :type base_filename: str
    :param props: Properties to write
    :type props: dict
    """
    sample_data_path = m_name.parse_path_from_item_filename(base_filename)
    # Write properties
    prop_path = os.path.join(sample_data_path, ".props")
    with open(prop_path, "w") as f:
        json.dump(props, f, indent=4)


def update_props(base_filename, props_to_update):
    """Update sample file properties and write to file. Properties given are updated, rest (if any) remain as is.

    :param base_filename: Sample file filename
    :type base_filename: str
    :param props_to_update: Properties to update,
    :type props_to_update: dict
    """
    sample_data_path = m_name.parse_path_from_item_filename(base_filename)
    # Update properties
    prop_path = os.path.join(sample_data_path, ".props")
    with open(prop_path, "r") as f:
        props = json.load(f)
    props.update(props_to_update)
    with open(prop_path, "w") as f:
        json.dump(props, f, indent=4)


def update_zarr_array_coord(base_filename, var, dim, coord):
    """Update coordinates of a zarr array

    :param base_filename: Sample file filename
    :type base_filename: str
    :param var: Sample file variable name
    :type var: str
    :param dim: Name of the coordinate dimension
    :type dim: str
    :param coord: New coordinate array
    :type coord: np.array
    """
    array_path = m_name.filename_to_zarr_path(base_filename, var)
    with zarr_write_lock(array_path):
        # Live listing, not the consolidated one: a group present on disk but
        # missing from a stale .zmetadata would otherwise be skipped silently,
        # leaving a recalibrated store whose groups sit on two different m/z
        # axes. See open_zarr_store.
        zarr_array = open_zarr_store(array_path, mode="a")
        zarr_array[dim][:] = coord
        for group_name, group in zarr_array.groups():
            group[dim][:] = coord


def get_dataset_vars(dataset: xr.Dataset) -> list:
    """Extracts dataset variables and their properties.

    :param dataset: xr.Dataset
    :type dataset: xr.Dataset
    :return: List of variables with their properties
    :rtype: list
    """
    variables = []
    for name, da in dataset.data_vars.items():
        dims = tuple(da.dims)
        dtype = da.dtype
        variables.append({"name": name, "dims": dims, "dtype": dtype})
    return variables


def calculate_mz_chunk_size(
    time_coord_size: int, variables: list, desired_chunk_mb: int = 50
) -> int:
    """Calculates an appropriate chunk size along the 'mz' dimension based on the desired chunk size in MB.

    :param time_coord_size: Size of the 'time' coordinate dimension
    :type time_coord_size: int
    :param variables: List of dataset variables with their properties
    :type variables: list
    :param desired_chunk_mb: Desired chunk size in megabytes, defaults to 50
    :type desired_chunk_mb: int, optional
    :return: Calculated chunk size along 'mz' dimension
    :rtype: int
    """
    desired_bytes = desired_chunk_mb * 1024 * 1024
    bytes_per_mz = 0  # Bytes consumed per mz index across all variables
    for v in variables:
        dtype = np.dtype(v["dtype"])
        if v["dims"] == ("mz", "time") or v["dims"] == ("time", "mz"):
            bytes_per_mz += dtype.itemsize * time_coord_size
        elif v["dims"] == ("mz",):
            bytes_per_mz += dtype.itemsize
        elif dtype.kind == "U":
            if v["dims"] == ("mz",):
                bytes_per_mz += dtype.itemsize
    chunk_mz = desired_bytes // bytes_per_mz
    return int(chunk_mz)


async def write_peaks(
    peak_timeseries: xr.Dataset,
    filename: str,
    overwrite: bool = False,
) -> None:
    """Write fitted peak areas and peak heights to zarr files.

    This function handles two scenarios:
    1. Full overwrite: Creates a new zarr file from scratch
    2. Partial update: Updates specific m/z values in an existing zarr file

    The partial update uses a read-modify-write pattern on individual chunks
    to minimize memory usage.

    :param peak_timeseries: Dataset containing peak areas and peak heights
    :type peak_timeseries: xr.Dataset
    :param filename: Sample file name
    :type filename: str
    :param overwrite: Flag to overwrite peaks if they already exist, defaults to False
    :type overwrite: bool, optional
    :raises Exception: If the path is too long or other I/O errors occur
    :return: None
    """
    peak_timeseries_path = m_name.filename_to_zarr_path(filename, "peak_timeseries")

    # --- Handle full overwrite scenario ---
    file_not_exists = not os.path.exists(peak_timeseries_path)
    if overwrite or file_not_exists:
        await _full_overwrite_peaks(peak_timeseries, peak_timeseries_path)
        return

    # --- Handle partial update scenario ---
    await _partial_update_peaks(peak_timeseries, peak_timeseries_path)


async def _full_overwrite_peaks(
    peak_timeseries: xr.Dataset,
    peak_timeseries_path: str,
) -> None:
    """Perform a full overwrite of the peak timeseries zarr file.

    :param peak_timeseries: Dataset to write
    :param peak_timeseries_path: Path to the zarr file
    """

    def _write():
        with zarr_write_lock(peak_timeseries_path):
            runtime.logger.debug(
                f"Full overwrite of peak_timeseries at {peak_timeseries_path}"
            )
            if os.path.exists(peak_timeseries_path):
                try:
                    rmtree(peak_timeseries_path)
                except Exception:
                    runtime.logger.exception(
                        "Failed to remove existing peak timeseries"
                    )
                    raise

            peak_timeseries.to_zarr(
                peak_timeseries_path,
                mode="w",
                encoding={"sparsity": {"_FillValue": 0.0}},
            )

    try:
        await asyncio.to_thread(_write)
    except FileNotFoundError as e:
        if ".partial" in str(e):
            raise Exception(
                f"The path is probably too long: {peak_timeseries_path}"
            ) from e
        raise


async def _partial_update_peaks(
    peak_timeseries: xr.Dataset,
    peak_timeseries_path: str,
) -> None:
    """Update specific m/z values in an existing peak timeseries zarr file.

    This function:
    1. Ensures the input data is fully materialized (not lazy)
    2. Calculates which zarr chunks need to be updated
    3. Performs writes

    :param peak_timeseries: Dataset containing updates (must be fully loaded to memory)
    :param peak_timeseries_path: Path to the zarr file
    """
    runtime.logger.debug(f"Writing new peak timeseries into {peak_timeseries_path}...")

    # Ensure data load before any I/O operations.
    if hasattr(peak_timeseries, "compute"):
        peak_timeseries = peak_timeseries.compute()

    # Extract metadata from existing file
    chunk_info = _get_chunk_metadata(peak_timeseries, peak_timeseries_path)

    mz_update = peak_timeseries.coords["mz"].values
    runtime.logger.debug(
        f"Updating {len(chunk_info['indexer'])} peaks across "
        f"{len(chunk_info['unique_chunks'])} chunks."
    )

    # Prepare chunk updates
    chunk_tasks = _prepare_chunk_tasks(peak_timeseries, chunk_info)

    # Materialize all subset data before entering the write phase
    # This ensures no lazy evaluation happens during writes
    for task in chunk_tasks:
        if hasattr(task["subset_data"], "compute"):
            task["subset_data"] = task["subset_data"].compute()

    def _write_all_chunks():
        """Write all chunks one by one under a single lock."""
        with zarr_write_lock(peak_timeseries_path):
            for task in chunk_tasks:
                _process_chunk_sync(
                    task["chunk_start"],
                    task["chunk_end"],
                    peak_timeseries_path,
                    task["subset_data"],
                    task["relevant_indices"],
                    chunk_info["max_mz_idx"],
                )

    await asyncio.to_thread(_write_all_chunks)

    runtime.logger.debug(
        f"Successfully saved peak timeseries for {len(mz_update)} mz values."
    )


def _get_chunk_metadata(
    peak_timeseries: xr.Dataset,
    peak_timeseries_path: str,
) -> dict:
    """Extract chunk metadata from the existing zarr file.

    Uses the actual zarr chunk size from the file, not a calculated value,
    to ensure alignment with physical storage.

    :param peak_timeseries: Dataset with m/z values to update
    :param peak_timeseries_path: Path to the zarr file
    :return: Dictionary with indexer, chunk size, and other metadata
    """
    z = open_zarr_store(peak_timeseries_path)

    # Get dimensions
    max_mz_idx = z["mz"].shape[0]
    time_coord_size = z["time"].shape[0]

    # Get the actual chunk size from the zarr file
    actual_mz_chunk_size = None
    for var_name in ["peak_areas", "peak_heights"]:
        if var_name in z:
            chunks = z[var_name].chunks
            if chunks is not None and len(chunks) >= 1:
                actual_mz_chunk_size = chunks[0]
                runtime.logger.debug(
                    f"Using actual zarr chunk size: {actual_mz_chunk_size}"
                )
                break

    if actual_mz_chunk_size is None:
        # Fallback to calculated
        variables = get_dataset_vars(peak_timeseries)
        actual_mz_chunk_size = calculate_mz_chunk_size(time_coord_size, variables)
        runtime.logger.warning(
            f"Could not determine actual zarr chunk size, using calculated: {actual_mz_chunk_size}"
        )

    # Get indexer for the m/z values we want to update
    mz_update = peak_timeseries.coords["mz"].values
    existing_mz = z["mz"][:]

    # Find indices for matching
    indexer = np.searchsorted(existing_mz, mz_update)

    # Clip indices to valid range before comparison
    clipped_indexer = np.clip(indexer, 0, len(existing_mz) - 1)

    # Verify exact matches
    exact_match_mask = np.isclose(existing_mz[clipped_indexer], mz_update)

    if not np.all(exact_match_mask):
        missing_mz = mz_update[np.invert(exact_match_mask)]
        raise ValueError(
            f"Cannot update m/z values not present in existing data: {missing_mz}. "
            "Running peak detection first should resolve this issue."
        )

    chunk_indices = indexer // actual_mz_chunk_size
    unique_chunks = np.unique(chunk_indices)

    return {
        "indexer": indexer,
        "mz_chunk_size": actual_mz_chunk_size,
        "max_mz_idx": max_mz_idx,
        "chunk_indices": chunk_indices,
        "unique_chunks": unique_chunks,
    }


def _prepare_chunk_tasks(
    peak_timeseries: xr.Dataset,
    chunk_info: dict,
) -> list[dict]:
    """Prepare tasks for each chunk that needs updating.

    :param peak_timeseries: Dataset with updates
    :param chunk_info: Chunk metadata from _get_chunk_metadata
    :return: List of task dictionaries
    """
    tasks = []
    indexer = chunk_info["indexer"]
    mz_chunk_size = chunk_info["mz_chunk_size"]

    for c_idx in chunk_info["unique_chunks"]:
        c_start = int(c_idx * mz_chunk_size)
        c_end = int((c_idx + 1) * mz_chunk_size)

        mask = (indexer >= c_start) & (indexer < c_end)
        relevant_indices = indexer[mask]
        subset_data = peak_timeseries.isel(mz=np.where(mask)[0])

        tasks.append(
            {
                "chunk_start": c_start,
                "chunk_end": c_end,
                "subset_data": subset_data,
                "relevant_indices": relevant_indices,
            }
        )

    return tasks


def _process_chunk_sync(
    chunk_start: int,
    chunk_end: int,
    peak_timeseries_path: str,
    update_data: xr.Dataset,
    update_indices_in_chunk: np.ndarray,
    max_mz_idx: int,
) -> None:
    """Perform a read-modify-write operation on a single zarr chunk.

    IMPORTANT: This function assumes it is called under a write lock.
    It does NOT handle its own synchronization.

    :param chunk_start: Starting m/z index of this chunk
    :param chunk_end: Ending m/z index of this chunk (exclusive)
    :param peak_timeseries_path: Path to the zarr file
    :param update_data: Dataset containing new values for this chunk
    :param update_indices_in_chunk: Global indices that map to this chunk
    :param max_mz_idx: Maximum m/z index in the file (to handle last chunk)
    """
    # Handle last chunk boundary
    current_chunk_end = min(chunk_end, max_mz_idx)

    # Convert global indices to local chunk indices
    local_indices = update_indices_in_chunk - chunk_start

    # Live listing, not the consolidated one: a variable present on disk but
    # missing from a stale .zmetadata would otherwise fail the guard below and
    # have its write dropped silently. See open_zarr_store.
    z = open_zarr_store(peak_timeseries_path, mode="r+")

    # Update each variable
    for var_name in update_data.data_vars:
        if var_name not in z:
            runtime.logger.warning(
                f"Variable {var_name} is absent from {peak_timeseries_path}, "
                "so its update was skipped."
            )
            continue

        _update_zarr_variable(
            zarr_array=z[var_name],
            update_values=update_data[var_name].values,
            chunk_start=chunk_start,
            chunk_end=current_chunk_end,
            local_indices=local_indices,
        )


def _update_zarr_variable(
    zarr_array: zarr.Array,
    update_values: np.ndarray,
    chunk_start: int,
    chunk_end: int,
    local_indices: np.ndarray,
) -> None:
    """Update a single zarr variable using read-modify-write.

    :param zarr_array: The zarr array to update
    :param update_values: New values to write
    :param chunk_start: Starting index of the chunk
    :param chunk_end: Ending index of the chunk
    :param local_indices: Indices within the chunk to update
    """
    if zarr_array.ndim == 1:
        chunk_data = zarr_array[chunk_start:chunk_end]
        chunk_data[local_indices] = update_values
        zarr_array[chunk_start:chunk_end] = chunk_data
    else:
        chunk_data = zarr_array[chunk_start:chunk_end, :]
        chunk_data[local_indices, :] = update_values
        zarr_array[chunk_start:chunk_end, :] = chunk_data


def ensure_sparsity_exists(base_filename: str) -> bool:
    """Ensure the 'sparsity' variable exists in peak_timeseries.zarr.

    For backwards compatibility with older zarr files that lack the variable,
    this function creates 'sparsity' if missing. For peaks with computed
    timeseries, it derives the fraction of scans with peak_heights <= 0.
    For uncomputed peaks, it defaults to 0.0.

    :param base_filename: Sample file filename
    :type base_filename: str
    :return: True if the variable was created (migration occurred), False if already present
    :rtype: bool
    """
    peak_timeseries_path = m_name.filename_to_zarr_path(
        base_filename, "peak_timeseries"
    )
    if not os.path.exists(peak_timeseries_path):
        return False

    # Quick check outside the lock to avoid locking in the common case.
    # The live listing is required here, not incidental (see open_zarr_store):
    # reading the consolidated view would report a sparsity array that exists
    # on disk as missing whenever .zmetadata is stale, skipping the repair
    # below and falling through to create it a second time. That second create
    # raises ContainsArrayError without touching the store - nothing on disk is
    # damaged - but the migration never completes, so every load_peak_data for
    # this file keeps failing until .zmetadata is reconsolidated.
    z = open_zarr_store(peak_timeseries_path)
    if "sparsity" in z:
        # Reconsolidate in case .zmetadata is stale (xr.open_zarr reads from it)
        zarr.consolidate_metadata(peak_timeseries_path)
        return False

    runtime.logger.info(
        f"Migrating peak_timeseries.zarr: adding 'sparsity' variable for {base_filename}"
    )

    # Compute sparsity for all peaks (read-only, safe outside lock)
    mz_size = z["mz"].shape[0]
    is_computed = z["is_timeseries_computed"][:]
    sparsity_values = np.zeros(mz_size, dtype=np.float64)

    # For computed peaks, derive from peak_heights using chunk-aligned reads
    computed_indices = np.where(is_computed)[0]
    if len(computed_indices) > 0:
        peak_heights = z["peak_heights"]
        mz_chunk_size = peak_heights.chunks[0] if peak_heights.chunks else mz_size
        n_time = peak_heights.shape[1]

        for chunk_start in range(0, mz_size, mz_chunk_size):
            chunk_end = min(chunk_start + mz_chunk_size, mz_size)
            # Find computed indices within this chunk
            mask = (computed_indices >= chunk_start) & (computed_indices < chunk_end)
            if not np.any(mask):
                continue
            indices_in_chunk = computed_indices[mask]
            local_indices = indices_in_chunk - chunk_start
            # Single bulk read for the entire chunk
            chunk_data = peak_heights[chunk_start:chunk_end, :]
            # Vectorized sparsity computation
            # ~(> 0) counts NaN as missing (sparse)
            sparsity_values[indices_in_chunk] = (
                np.sum(~(chunk_data[local_indices] > 0), axis=1) / n_time
            )

    # Write the new variable under the write lock with a re-check
    with zarr_write_lock(peak_timeseries_path):
        # Live listing for the same reason as the check above: this re-check
        # has to see the real store, including an array a different process
        # created since.
        z = open_zarr_store(peak_timeseries_path, mode="r+")

        # Re-check inside the lock — another process may have created it
        if "sparsity" in z:
            zarr.consolidate_metadata(peak_timeseries_path)
            return False

        # Get chunk size from existing 1D variables
        mz_chunk_size = (
            z["is_timeseries_computed"].chunks[0]
            if z["is_timeseries_computed"].chunks
            else mz_size
        )
        sparsity = z.create_array(
            "sparsity",
            shape=sparsity_values.shape,
            chunks=(mz_chunk_size,),
            dtype=np.float64,
        )
        sparsity[:] = sparsity_values
        # Write .zattrs for xarray compatibility (dimension metadata)
        sparsity_attrs = {"_ARRAY_DIMENSIONS": ["mz"]}
        sparsity.attrs.update(sparsity_attrs)

        # Reconsolidate metadata so xr.open_zarr sees the new variable
        zarr.consolidate_metadata(peak_timeseries_path)

    runtime.logger.info(f"Migration complete: 'sparsity' added to {base_filename}")
    return True


def delete_peaks(base_filename: str) -> None:
    """Delete sample file peaks.

    :param base_filename: Sample file filename
    :type base_filename: str
    """
    sample_data_path = m_name.parse_path_from_item_filename(base_filename)
    peak_dirs = glob.glob(os.path.join(sample_data_path, "peak_*"))
    for dir in peak_dirs:
        remove_path(dir)


def load_batch_cache(
    sample_batch_id: str,
    zarr_filename: str,
) -> xr.Dataset:
    """Load batch cached data from the zarr file.

    :param sample_batch_id: Sample batch ID
    :type sample_batch_id: str
    :param zarr_filename: Name of a zarr file
    :type zarr_filename: str
    :raises FileNotFoundError: Batch cache file not found
    :return: Loaded batch cache data
    :rtype: xr.Dataset
    """
    batch_path = m_name.get_batch_cache_path(sample_batch_id)
    var_path = os.path.join(batch_path, f"{zarr_filename}.zarr")
    if not os.path.exists(var_path):
        raise FileNotFoundError(f"Batch cache file not found: {var_path}")
    return xr.open_zarr(var_path)


def write_batch_cache(
    sample_batch_id: str,
    zarr_filename: str,
    batch_peaks: xr.Dataset,
) -> None:
    """Write batch cache data to the zarr file.

    :param sample_batch_id: Sample batch ID
    :type sample_batch_id: str
    :param zarr_filename: Name of a zarr file
    :type zarr_filename: str
    :param batch_peaks: Batch cache data to write
    :type batch_peaks: xr.Dataset
    """
    batch_path = m_name.get_batch_cache_path(sample_batch_id)
    var_path = os.path.join(batch_path, f"{zarr_filename}.zarr")
    with zarr_write_lock(var_path):
        batch_peaks.to_zarr(var_path, mode="w")


def delete_batch_cache(sample_batch_id: str) -> None:
    """Delete sample batch cache.

    :param sample_batch_id: Sample batch ID
    :type sample_batch_id: str
    """
    batch_path = m_name.get_batch_cache_path(sample_batch_id)
    if os.path.exists(batch_path):
        rmtree(batch_path)
