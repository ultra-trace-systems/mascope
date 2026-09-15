from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Tuple

import dask.array as da
import h5py
import numpy as np
import xarray as xr


DEFAULT_NONSENSE_MZ = 10.0  # m/z values below this are considered nonsense


class NoScansRecordedError(ValueError):
    """A TofDaq file whose ``BufTimes`` holds no recorded scan at all."""


def recorded_scan_times(buf_times) -> np.ndarray:
    """
    Scan start times actually recorded, with the unwritten tail trimmed off.

    TofDaq pre-allocates ``TimingData/BufTimes`` as (writes x bufs) and fills
    it as the run proceeds, so an aborted acquisition leaves a tail of unwritten
    slots - often whole rows of them. The recorded scans are everything up to
    the last written entry *anywhere* in the array, not just in the last row: a
    run that stopped mid-block still recorded every scan before it.

    An unwritten slot reads as 0.0, and on some writers as NaN, so both are
    what the tail is trimmed against. Index 0 is deliberately kept: buf times
    are relative to acquisition start, so the first scan legitimately reads 0.0
    and is indistinguishable from an unwritten slot by value alone - which is
    why a file whose every timestamp is unwritten is reported as holding no
    scans rather than as one scan at t=0.

    Every reader below trims the same way, so they agree on which scans the
    file holds; the ingestion path adds the further conditions it needs to
    measure a time axis (see ``H5Processor._recorded_scan_times``).

    :param buf_times: The ``TimingData/BufTimes`` dataset.
    :return: 1-D array of recorded scan start times.
    :rtype: numpy.ndarray
    :raises NoScansRecordedError: When no entry was ever written.
    """
    times = np.asarray(buf_times[:]).reshape(-1)
    written = np.where(np.isfinite(times) & (times != 0))[0]
    if written.size == 0:
        raise NoScansRecordedError(
            "The file's TimingData/BufTimes holds no recorded scan; "
            "the acquisition is empty or was aborted."
        )
    return times[: written[-1] + 1]


@contextmanager
def open_h5_file(datafile_path: str):
    """Context manager for safely opening and closing Tofwerk h5-files.

    :param datafile_path: Path to the Tofwerk HDF5 file (.h5) containing the data.
    :type datafile_path: str
    :yield: h5py File object
    """
    # Only the open is wrapped: exceptions raised inside the caller's `with`
    # body must propagate unchanged instead of being relabelled as a failure
    # to open the file. No log here either - the raised exception is logged
    # by the caller's handler.
    try:
        h5_file = h5py.File(datafile_path, "r")
    except Exception as e:
        err_message = f"Failed to open the file {Path(datafile_path).name}: {e}"
        raise Exception(err_message) from e
    try:
        yield h5_file
    finally:
        h5_file.close()


def get_polarity_options(datafile_path: str) -> str:
    """
    Retrieve the polarity based on the IonMode attribute in the HDF5 file.

    :param datafile_path: Path to the HDF5 file containing spectrum data.
    :type datafile_path: str
    :return: polarity value, either "+" or "-".
    :rtype: str
    """
    with open_h5_file(datafile_path) as h5_file:
        ion_mode = h5_file.attrs.get("IonMode")
        if ion_mode is not None:
            ion_mode_str = (
                ion_mode.decode() if isinstance(ion_mode, bytes) else str(ion_mode)
            )
            ion_mode_str = ion_mode_str.strip().lower()
            if ion_mode_str == "negative":
                return "-"
            elif ion_mode_str == "positive":
                return "+"
            else:
                raise ValueError(f"Unexpected IonMode value: {ion_mode_str}")


def get_conversion_coefficient(h5_file) -> float:
    """
    Calculate the conversion coefficient to convert signal intensity from [mV] to [ions/sec].

    :param h5_file: Opened HDF5 file object.
    :type h5_file: h5py.File
    :return: Conversion coefficient.
    :rtype: float
    """
    try:
        single_ion_signal = (
            h5_file["FullSpectra"].attrs["Single Ion Signal"][0] * 1e-9
        )  # [mV·s/ion]
        sample_interval = h5_file["FullSpectra"].attrs["SampleInterval"][0]  # [s]
        tof_period = h5_file["TimingData"].attrs["TofPeriod"][0] * 1e-9  # [s]
        n_extractions = h5_file.attrs["NbrWaveforms"][0]  # [ext]
    except IndexError:
        single_ion_signal = h5_file["FullSpectra"].attrs["Single Ion Signal"] * 1e-9
        sample_interval = h5_file["FullSpectra"].attrs["SampleInterval"]  # [s]
        tof_period = h5_file["TimingData"].attrs["TofPeriod"] * 1e-9  # [s]
        n_extractions = h5_file.attrs["NbrWaveforms"]  # [ext]

    tof_frequency = 1 / tof_period  # [ext/s]

    # Coefficient to convert signal intensity from [mV] -> [ions/sec]
    conversion_coefficient = (
        sample_interval * tof_frequency / single_ion_signal / n_extractions
    )  # [ions/(mV·s)]

    return conversion_coefficient


def get_signal(
    datafile_path: str,
    t_min: float | None = None,
    t_max: float | None = None,
    mz_min: float | None = None,
    mz_max: float | None = None,
) -> xr.Dataset:
    """
    Retrieve a full time-windowed signal from an HDF5 file. Allows slicing by time and m/z range.

    t_min=None is equal to min scan time, t_max=None is equal to max scan time.
    mz_min=None is equal to min m/z, mz_max=None is equal to max m/z.

    :param datafile_path: Path to the HDF5 file containing spectrum data.
    :type datafile_path: str
    :param t_min: Optional start time in seconds (default is None).
    :type t_min: float, optional
    :param t_max: Optional end time in seconds (default is None).
    :type t_max: float, optional
    :param mz_min: Optional start m/z value (default is None).
    :type mz_min: float, optional
    :param mz_max: Optional end m/z value (default is None).
    :type mz_max: float, optional
    :raises ValueError: If start time is greater than end time, or start m/z is greater than end m/z.
    :raises Exception: If an error occurs while opening the HDF5 file.
    :return: An xarray Dataset containing the signal data.
    :rtype: xr.Dataset
    """
    with open_h5_file(datafile_path) as h5_file:
        # Get signal HDF5 dataset reference
        signal_ref = h5_file["FullSpectra"]["TofData"]
        # Get m/z scale
        all_mzs = h5_file["FullSpectra"]["MassAxis"][:]

        # Get time scale, without the unwritten tail of an aborted run
        scan_time = recorded_scan_times(h5_file["TimingData"]["BufTimes"])
        # Total number of scans
        n_scans = scan_time.size

        # Determine m/z range
        mz_min = min(all_mzs) if mz_min is None else mz_min
        mz_max = max(all_mzs) if mz_max is None else mz_max

        # Check m/z range
        if mz_min > mz_max:
            raise ValueError(f"Invalid m/z range: {mz_min} > {mz_max}")
        # Check if provided mzs have intersection with the mzs in the file
        if mz_min > all_mzs[-1] or mz_max < all_mzs[0]:
            raise ValueError(
                f"Provided m/z range ({mz_min}, {mz_max}) is out of the sample file m/z range ({all_mzs[0]:.1f}, {all_mzs[-1]:.1f})"
            )

        # Find indices of m/z range
        mz_start_ind = np.abs(all_mzs - mz_min).argmin()
        mz_end_ind = np.abs(all_mzs - mz_max).argmin()

        # Calculate number of samples (of m/z points)
        n_samples = mz_end_ind - mz_start_ind + 1

        if t_min is None and t_max is None:
            # Slice the signal between mz_start_ind and mz_end_ind
            signal_array = signal_ref[:, :, :, mz_start_ind : mz_end_ind + 1]
            # Reshape the signal array to 2D
            signal_array = signal_array.reshape(-1, signal_array.shape[-1])[:n_scans, :]
            # Convert to dask array
            signal_dask = da.from_array(signal_array, chunks="auto")
            signal = xr.Dataset(
                {"signal": (("mz", "time"), signal_dask.T)},
                coords={
                    "mz": all_mzs[mz_start_ind : mz_end_ind + 1],
                    "time": scan_time,
                },
            )
            signal = signal.where(signal["mz"] >= DEFAULT_NONSENSE_MZ, drop=True)
            # Init and return xarray Dataset with swapped dimensions
            return signal

        if t_min is not None and t_max is not None and t_min > t_max:
            raise ValueError(f"Invalid time range: {t_min} > {t_max}")

        # Find indices of time range
        t_start_ind = 0 if t_min is None else np.abs(scan_time - t_min).argmin()
        t_end_ind = n_scans - 1 if t_max is None else np.abs(scan_time - t_max).argmin()

        # 1. flatten n_writes, n_bufs, n_segments dimensions
        # 2. group dimension coordinates
        # 3. get coordinate groups of the required scans in start:end range
        coordinates = (
            np.indices(signal_ref.shape[:-1])
            .reshape(3, -1)
            .T[t_start_ind : t_end_ind + 1]
        )
        # Preallocate output array
        signal_slice = np.empty(
            (t_end_ind - t_start_ind + 1, n_samples), dtype=signal_ref.dtype
        )
        # Populate result by iterating over the first three dimensions
        for ind, coord in enumerate(coordinates):
            signal_slice[ind, :] = signal_ref[
                coord[0], coord[1], coord[2], mz_start_ind : mz_end_ind + 1
            ]

        # Coefficient to convert signal intensity from [mV] -> [ions/sec]
        conversion_coefficient = get_conversion_coefficient(h5_file)

        # Convert [mV] -> [ions/sec]
        signal_slice *= conversion_coefficient

        signal_dask = da.from_array(signal_slice, chunks="auto")
        signal = xr.Dataset(
            {"signal": (("mz", "time"), signal_dask.T)},
            coords={
                "mz": all_mzs[mz_start_ind : mz_end_ind + 1],
                "time": scan_time[t_start_ind : t_end_ind + 1],
            },
        )
        signal = signal.where(signal["mz"] >= DEFAULT_NONSENSE_MZ, drop=True)

        return signal


def compute_sum_signal(
    datafile_path: str,
    t_min: float | None = None,
    t_max: float | None = None,
) -> Tuple[xr.DataArray, float]:
    """
    Computes sum signal in (t_min, t_max) time range.

    t_min=None is equal to min scan time, t_max=None is equal to max scan time.

    :param datafile_path: Path to the HDF5 file containing spectrum data.
    :type datafile_path: str
    :param t_min: Optional start time in seconds (default is None).
    :type t_min: float, optional
    :param t_max: Optional end time in seconds (default is None).
    :type t_max: float, optional
    :return: An xarray DataArray containing the sum signal and the averaging factor
    :rtype: tuple[xr.core.dataarray.DataArray, float]
    """
    with open_h5_file(datafile_path) as h5_file:
        # Get signal HDF5 dataset reference
        signal_ref = h5_file["FullSpectra"]["TofData"]
        # Get m/z scale
        all_mzs = h5_file["FullSpectra"]["MassAxis"][:]

        # Get time scale, without the unwritten tail of an aborted run
        scan_time = recorded_scan_times(h5_file["TimingData"]["BufTimes"])
        # Total number of scans
        n_scans = scan_time.size

        # Determine time range
        t_start_ind = 0 if t_min is None else np.abs(scan_time - t_min).argmin()
        t_end_ind = n_scans - 1 if t_max is None else np.abs(scan_time - t_max).argmin()

        # 1. flatten n_writes, n_bufs, n_segments dimensions
        # 2. group dimension coordinates
        # 3. get coordinate groups of the scans
        coordinates = np.indices(signal_ref.shape[:-1]).reshape(3, -1).T
        start_coord = coordinates[t_start_ind]
        end_coord = coordinates[t_end_ind]

        # Initialize sum signal array
        sum_signal = np.zeros_like(all_mzs)

        # Process the signal in chunks to save memory
        for i in range(start_coord[0], end_coord[0]):
            chunk = signal_ref[i, :, :, :]
            # Sum the signal within the chunk
            chunk_sum = chunk.sum(axis=(0, 1))
            sum_signal += chunk_sum

        # Sum the signal within the last chunk
        last_chunk = signal_ref[end_coord[0], : end_coord[1] + 1, : end_coord[2] + 1, :]
        last_chunk_sum = last_chunk.sum(axis=(0, 1))
        sum_signal += last_chunk_sum
        averaging_factor = t_end_ind - t_start_ind + 1

        # Coefficient to convert signal intensity from [mV] -> [ions/sec]
        conversion_coefficient = get_conversion_coefficient(h5_file)

        # Convert [mV] -> [ions/sec]
        sum_signal *= conversion_coefficient

        sum_signal_da = xr.DataArray(sum_signal, dims=["mz"], coords={"mz": all_mzs})
        sum_signal_da.name = "sum_signal"
        sum_signal_da = sum_signal_da.where(
            sum_signal_da["mz"] >= DEFAULT_NONSENSE_MZ, drop=True
        )

        return sum_signal_da, averaging_factor


def get_tic_per_scan(
    datafile_path: str, timestamps: Iterable[float] | None = None
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Calculate TIC per scan from HDF5 file.

    :param datafile_path: Path to the HDF5 file containing spectrum data.
    :type datafile_path: str
    :param timestamps: Optional list of timestamps to filter TIC values.
    :type timestamps: Iterable[float], optional
    :return: Tuple of time and TIC values as numpy arrays.
    :rtype: tuple
    """
    with open_h5_file(datafile_path) as h5_file:
        # Get signal HDF5 dataset reference
        signal_ref = h5_file["FullSpectra"]["TofData"]
        # Get time scale, without the unwritten tail of an aborted run
        scan_timestamp = recorded_scan_times(h5_file["TimingData"]["BufTimes"])
        # Total number of recorded scans
        n_scans = scan_timestamp.size

        # 1. flatten n_writes, n_bufs, n_segments dimensions
        # 2. group dimension coordinates
        # 3. get coordinate groups
        coordinates = np.indices(signal_ref.shape[:-1]).reshape(3, -1).T
        coordinates = coordinates[:n_scans]

        # Prealocate scan_tic array
        scan_tic = np.empty(n_scans)
        # Populate TIC array by iterating over the first three dimensions
        for ind, coord in enumerate(coordinates):
            scan_tic[ind] = signal_ref[coord[0], coord[1], coord[2], :].sum()

        sum_spec = h5_file["FullSpectra"]["SumSpectrum"][:]
        # Convert to ions/sec
        sum_spec *= get_conversion_coefficient(h5_file)
        # Get total TIC
        total_tic = sum_spec.sum()
        # Correct TIC per scan by total TIC
        scan_tic = scan_tic / scan_tic.sum() * total_tic

        if timestamps is not None:
            # Filter TIC values by timestamps
            timestamps = np.asarray(timestamps)
            # Find closest scan index for each timestamp
            scan_indices = np.searchsorted(scan_timestamp, timestamps)
            # Ensure indices are within valid range
            scan_indices = np.clip(scan_indices, 0, len(scan_timestamp) - 1)
            # Extract scan TIC and scan timestamps values for the closest scan index
            scan_tic = scan_tic[scan_indices]
            scan_timestamp = scan_timestamp[scan_indices]

        return scan_timestamp, scan_tic


def get_scan_timestamps(
    datafile_path: str, t_min: float | None = None, t_max: float | None = None
) -> np.ndarray:
    """
    Retrieve scan timestamps from the HDF5 file, optionally filtering by a time range.

    :param datafile_path: Path to the HDF5 file containing spectrum data.
    :type datafile_path: str
    :param t_min: Optional start time in seconds (default is None).
    :type t_min: float, optional
    :param t_max: Optional end time in seconds (default is None).
    :type t_max: float, optional
    :return: Array of scan timestamps within the specified time range.
    :rtype: np.ndarray
    """
    with open_h5_file(datafile_path) as h5_file:
        # Get time scale, without the unwritten tail of an aborted run
        scan_timestamp = recorded_scan_times(h5_file["TimingData"]["BufTimes"])

        # Apply time filtering if t_min or t_max is provided
        if t_min is not None:
            scan_timestamp = scan_timestamp[scan_timestamp >= t_min]
        if t_max is not None:
            scan_timestamp = scan_timestamp[scan_timestamp <= t_max]

        return scan_timestamp


def get_peak_timeseries(
    datafile_path: str,
    mzs: Iterable[float],
    true_mz_axis: Iterable[float],
    t_min: float | None = None,
    t_max: float | None = None,
) -> xr.DataArray:
    """Extracts the peak timeseries for the specified m/z values in the time range (t_min, t_max).

    :param datafile_path: Path to the Tofwerk HDF5 file (.h5) containing the data.
    :type datafile_path: str
    :param mzs: array of m/z values for which peak timeseries are required.
    :type mzs: Iterable[float]
    :param true_mz_axis: Calibrated m/z axis values.
    :type true_mz_axis: Iterable[float]
    :param t_min: Start time [s], defaults to None
    :type t_min: float, optional
    :param t_max: End time [s], defaults to None
    :type t_max: float, optional
    :return: An xarray DataArray containing the peak timeseries
    :rtype: xr.DataArray
    """
    with open_h5_file(datafile_path) as h5_file:
        # Make sure mzs are numpy array
        mzs = np.asarray(mzs)
        # Get full time range
        scan_time = recorded_scan_times(h5_file["TimingData"]["BufTimes"])

        # Coefficient to convert signal intensity from [mV] -> [ions/sec]
        conv_coeff = get_conversion_coefficient(h5_file)

        # Check if t_min and t_max are passed
        t_min = scan_time[0] if t_min is None else t_min
        t_max = scan_time[-1] if t_max is None else t_max

        # Filter by time range
        time_mask = (scan_time >= t_min) & (scan_time <= t_max)
        scan_time = scan_time[time_mask]

        # Get signal HDF5 dataset reference
        signal_ref = h5_file["FullSpectra"]["TofData"]

        # Get and calibrate m/z scale
        all_mzs = h5_file["FullSpectra"]["MassAxis"][:]
        proper_mz_indices = np.where(all_mzs >= DEFAULT_NONSENSE_MZ)[0]
        all_mzs[proper_mz_indices] = np.interp(
            all_mzs[proper_mz_indices], true_mz_axis, true_mz_axis
        )

        # Find indices of m/z range
        mz_start_ind = np.abs(all_mzs - mzs.min()).argmin() - 1
        mz_end_ind = np.abs(all_mzs - mzs.max()).argmin() + 1
        mz_slice = all_mzs[mz_start_ind : mz_end_ind + 1]

        # Initialize output array
        peak_timeseries = np.zeros((len(mzs), len(scan_time)))

        # Get the coordinates of the scans
        scan_indices = np.where(time_mask)[0]
        coordinates = np.indices(signal_ref.shape[:-1]).reshape(3, -1).T
        coordinates = coordinates[scan_indices]

        # Populate result by iterating over the scans
        for j, coord in enumerate(coordinates):
            spec_segment = signal_ref[
                coord[0], coord[1], coord[2], mz_start_ind : mz_end_ind + 1
            ]

            spec_segment *= conv_coeff
            peak_timeseries[:, j] = np.interp(
                mzs, mz_slice, spec_segment, left=0.0, right=0.0
            )

        # Convert to dask array
        peak_timeseries_dask = da.from_array(peak_timeseries, chunks=("auto", "auto"))

        # Export xarray array with time and mz coordinates
        return xr.DataArray(
            peak_timeseries_dask,
            dims=("mz", "time"),
            coords={"mz": mzs, "time": scan_time},
            name="signal",
        )
