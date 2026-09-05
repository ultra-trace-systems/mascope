"""
This module provides functions to read and process Thermo Fisher raw files.

The .NET (pythonnet) assemblies are loaded lazily -- inside the functions that
use them, after ``_ensure_dotnet()`` -- so importing this module does not require
the Thermo RawFileReader DLLs. Mascope ships without those proprietary DLLs and
defaults to the OpenTFRaw backend; the Thermo backend is opt-in via
``MASCOPE_THERMO_DLL_DIR`` (see ``mascope_thermo.lib`` and the README).
"""

from __future__ import annotations

from typing import Iterable, Literal

import dask.array as da
import numpy as np
import pandas as pd
import xarray as xr

from mascope_thermo.backend import open_backend
from mascope_thermo.lib import load_dotnet
from mascope_thermo.runtime import runtime


def _ensure_dotnet() -> None:
    """Load the Thermo RawFileReader .NET assemblies (idempotent).

    Raises a clear error when the DLLs are not configured -- see
    ``mascope_thermo.lib.load_dotnet``."""
    load_dotnet(
        "ThermoFisher.CommonCore.Data",
        "ThermoFisher.CommonCore.RawFileReader",
        "System",
    )


SECONDS_PER_MINUTE = 60


class InvalidRangeError(ValueError):
    pass


class PolarityError(ValueError):
    pass


class ScanTypeError(ValueError):
    pass


class NoScansFoundError(ValueError):
    pass


def _validate_mz_range(
    RawFile, mz_min: float | None, mz_max: float | None
) -> tuple[float, float]:
    low = RawFile.RunHeaderEx.LowMass if mz_min is None else mz_min
    high = RawFile.RunHeaderEx.HighMass if mz_max is None else mz_max
    if low > high:
        raise InvalidRangeError(
            f"Invalid m/z range: mz_min={low}, mz_max={high}, where mz_min > mz_max"
        )
    return low, high


class RawFileManager:
    """Handles safe open/close operations on raw file at datafile_path"""

    def __init__(self, datafile_path: str):
        self.path = datafile_path
        self.RawFile = None

    def __enter__(self):
        _ensure_dotnet()
        from System import NullReferenceException
        from ThermoFisher.CommonCore.Data.Business import Device
        from ThermoFisher.CommonCore.RawFileReader import RawFileReaderAdapter

        try:
            self.RawFile = RawFileReaderAdapter.FileFactory(self.path)
            self.RawFile.SelectInstrument(Device.MS, 1)
            # This includes the base peak into the spectrum
            self.RawFile.IncludeReferenceAndExceptionData = True
        except NullReferenceException:
            raise FileNotFoundError(f"Could not open raw file: {self.path}")
        return self.RawFile

    def __exit__(self, *args):
        if self.RawFile:
            self.RawFile.Dispose()


class ScanSelector:
    """Class to select scans and their indices based on polarity,
    time range, and scan type filters.
    """

    def __init__(
        self,
        RawFile: RawFileReaderAdapter,
        polarity: Literal["+", "-"] | None = None,
        t_min: float | None = None,
        t_max: float | None = None,
        ms_type: Literal["Ms", "Ms2"] | None = "Ms",
    ):
        self._RawFile = RawFile
        self._polarity = polarity
        self._t_min = t_min
        self._t_max = t_max
        self._ms_type = ms_type

        self.raw_scan_filters = [
            self._RawFile.GetFilterForScanNumber(i) for i in self.all_scan_indices
        ]
        self.raw_scan_stats = [
            self._RawFile.GetScanStatsForScanNumber(i) for i in self.all_scan_indices
        ]

    @property
    def all_scan_indices(self) -> list[int]:
        """Returns all scan indices in the raw file."""
        num_of_scans = self._RawFile.RunHeaderEx.SpectraCount
        return list(range(1, num_of_scans + 1))

    def _polarity_mask(self) -> np.ndarray:
        """Creates a boolean mask for the specified polarity."""
        if self._polarity not in ["-", "+"]:
            raise PolarityError(
                f"Invalid polarity '{self._polarity}' provided. "
                "Polarity must be '+' or '-'."
            )
        polarity_verbose = "Negative" if self._polarity == "-" else "Positive"
        return np.array(
            [
                filter.Polarity.ToString() == polarity_verbose
                for filter in self.raw_scan_filters
            ],
            dtype=bool,
        )

    def _time_mask(self) -> np.ndarray:
        """Creates a boolean mask for the specified time range."""
        t_min = (
            self._RawFile.RunHeaderEx.StartTime * SECONDS_PER_MINUTE
            if self._t_min is None
            else self._t_min
        )
        t_max = (
            self._RawFile.RunHeaderEx.EndTime * SECONDS_PER_MINUTE
            if self._t_max is None
            else self._t_max
        )

        if t_min > t_max:
            raise InvalidRangeError(
                f"Invalid time range: t_min={t_min} s > t_max={t_max} s"
            )

        # Adjust time range as `abs_eps(x) ~ |x| * eps` where eps is machine epsilon
        # to account for floating point precision issues
        epsilon = np.finfo(np.float64).eps * t_max
        t_min_adj = t_min - epsilon
        t_max_adj = t_max + epsilon

        start_times_min = np.array([stats.StartTime for stats in self.raw_scan_stats])
        start_times_s = start_times_min * SECONDS_PER_MINUTE

        return np.logical_and(t_min_adj < start_times_s, start_times_s < t_max_adj)

    def _ms_type_mask(self) -> np.ndarray:
        """Creates a boolean mask for the specified MS scan type."""
        if self._ms_type not in ["Ms", "Ms2"]:
            raise ScanTypeError(
                f"Invalid scan type '{self._ms_type}' provided. "
                "MS scan type must be 'Ms' or 'Ms2'."
            )
        return np.array(
            [
                filter.MSOrder.ToString() == self._ms_type
                for filter in self.raw_scan_filters
            ],
            dtype=bool,
        )

    def _bad_first_scan(self) -> bool:
        """Checks if the TIC in the first scan is 5 times higher than
        the median TIC of the other scans

        This is a workaround for a common issue in Thermo raw files where the first scan
        is an outlier with an abnormally high TIC.

        #TODO: can be removed if Thermo releases a fix for this issue
        """
        # <= 1, not == 1: a file with no scans at all would otherwise index
        # off an empty TIC array below, raising IndexError from what is only
        # meant to be an outlier check - and masking the NoScansFoundError
        # that scan_indices_1based raises for exactly that case. Mirrors
        # OpenTFRawBackend._bad_first_scan.
        if len(self.raw_scan_stats) <= 1:
            return False

        tic_values = np.array([stats.TIC for stats in self.raw_scan_stats])
        first_scan_tic = tic_values[0]
        median_other_tic = np.median(tic_values[1:])

        return first_scan_tic >= 5 * median_other_tic

    @property
    def scan_indices_1based(self) -> list[int]:
        """Returns the list of scan indices that match the specified polarity,
        time range, and scan type.
        The scans are 1-based indexed, as per the Thermo library convention.
        """
        # dtype=bool on every mask combined below: for a file with no scans
        # the mask comprehensions are empty and numpy would otherwise infer
        # float64, making `mask &=` raise TypeError instead of letting the
        # empty selection fall through to the NoScansFoundError this property
        # exists to raise. Mirrors OpenTFRawBackend._selected.
        mask = np.ones(len(self.all_scan_indices), dtype=bool)

        if self._polarity:
            mask &= self._polarity_mask()

        if self._t_min is not None or self._t_max is not None:
            mask &= self._time_mask()

        if self._ms_type:
            mask &= self._ms_type_mask()

        if self._bad_first_scan():
            # INFO: a data quirk of the file, re-evaluated on every scan
            # selection for as long as the file is in use
            runtime.logger.info(
                "The first scan appears to be an outlier with abnormally high TIC. "
                "Excluding the first scan from selection."
            )
            mask[0] = False

        filtered_indices = np.array(self.all_scan_indices)[mask]

        if len(filtered_indices) == 0:
            raise NoScansFoundError(
                "No scans found matching the specified filters: "
                f"polarity='{self._polarity}', "
                f"time_range=({self._t_min}, {self._t_max}), "
                f"ms_type='{self._ms_type}'"
            )

        return filtered_indices.tolist()

    @property
    def scan_indices_0based(self) -> list[int]:
        """Returns the list of scan indices converted to 0-based indexing for Python."""
        return [i - 1 for i in self.scan_indices_1based]

    @property
    def scan_indices_dotnet(self) -> List[int]:
        """Returns the list of scan indices as a .NET List[int] for use with
        Thermo library functions."""
        from System.Collections.Generic import List

        net_list = List[int]()
        for index in self.scan_indices_1based:
            net_list.Add(index)
        return net_list

    @property
    def scan_times(self) -> np.ndarray:
        """Returns the scan times [s] for the filtered scan indices."""
        return np.array(
            [
                self.raw_scan_stats[i].StartTime * SECONDS_PER_MINUTE
                for i in self.scan_indices_0based
            ]
        )

    @property
    def scans(self) -> tuple:
        """Returns the scan objects for the filtered scan indices."""
        from ThermoFisher.CommonCore.Data import Extensions

        return tuple(Extensions.GetScans(self._RawFile, self.scan_indices_dotnet))

    @property
    def scan_filters(self) -> tuple:
        """Returns the scan filters for the filtered scan indices."""
        return tuple(
            self._RawFile.GetFilterForScanNumber(i) for i in self.scan_indices_1based
        )

    @property
    def scan_stats(self) -> tuple:
        """Returns the scan stats for the filtered scan indices."""
        return tuple(
            self._RawFile.GetScanStatsForScanNumber(i) for i in self.scan_indices_1based
        )


def get_polarity_options(datafile_path: str) -> str:
    """Reads the polarities present in a raw file.

    :param datafile_path: Path to the Thermo Fisher raw file (.raw) containing the data.
    :type datafile_path: str
    :return: "-" if only negative polarities are present, "+" if only positive, "+-" if
            both are present.
    :rtype: str
    """
    with open_backend(datafile_path) as backend:
        polarities = backend.polarities()

    has_positive = "+" in polarities
    has_negative = "-" in polarities

    if has_positive and has_negative:
        return "+-"
    elif has_positive:
        return "+"
    elif has_negative:
        return "-"
    else:
        raise PolarityError("No valid polarities found in the raw file.")


def get_signal(
    datafile_path: str,
    t_min: float | None = None,
    t_max: float | None = None,
    mz_min: float | None = None,
    mz_max: float | None = None,
    polarity: Literal["+", "-"] | None = None,
) -> xr.Dataset:
    """This function uses the Thermo Fisher libraries to read the raw file and extract
    the scan data. It then merges the scans to have a common m/z scale and converts
    the data to an xarray Dataset.
    Allows slicing by time and m/z range.

    t_min=None is equal to min scan time, t_max=None is equal to max scan time.
    mz_min=None is equal to min m/z, mz_max=None is equal to max m/z.

    :param datafile_path: Path to the Thermo Fisher raw file (.raw) containing the data.
    :type datafile_path: str
    :param t_min: Minimum time [s], optional, defaults to None
    :type t_min: float
    :param t_max: Maximum time [s], optional, defaults to None
    :type t_max: float
    :param mz_min: Minimum m/z, optional, defaults to None
    :type mz_min: float
    :param mz_max: Maximum m/z, optional, defaults to None
    :type mz_max: float
    :param polarity: + or -, Polarity of the scans to be retrieved, optional,
                    defaults to None
    :type polarity: str
    :return: An xarray Dataset containing the signal data
    :rtype: xr.Dataset
    """
    with open_backend(datafile_path) as backend:
        scan_mzs, scan_specs, scan_time = backend.profile_per_scan(
            polarity=polarity, t_min=t_min, t_max=t_max, mz_min=mz_min, mz_max=mz_max
        )

        if not scan_mzs:
            low_mass, high_mass = backend.mass_range()
            raise InvalidRangeError(
                f"""No data found in the specified m/z range.
                M/z range of the raw file: {low_mass} - {high_mass}
                """
            )

    # Create a sorted union of all unique m/z values
    all_mzs = np.unique(np.concatenate(scan_mzs))

    # Initialize output array
    signal_array = np.zeros((len(all_mzs), len(scan_time)), dtype=np.float64)

    # Fill the 2D array using exact mz matching
    for scan_idx, (mz, intensity) in enumerate(zip(scan_mzs, scan_specs)):
        # Find indices where the current scan mz values appear in all_mzs
        indices = np.searchsorted(all_mzs, mz)
        # Only fill values that exist in this scan
        signal_array[indices, scan_idx] = intensity

    signal_dask = da.from_array(signal_array, chunks="auto")

    return xr.Dataset(
        {"signal": (("mz", "time"), signal_dask)},
        coords={"mz": all_mzs, "time": scan_time},
    )


def compute_sum_signal(
    datafile_path: str,
    t_min: float | None = None,
    t_max: float | None = None,
    ppm: int = 1,
    polarity: Literal["+", "-"] | None = None,
    reconstruct: bool = False,
) -> tuple[xr.DataArray, int]:
    """Computes sum signal, binning counts within ``ppm`` value.
    Polarity and time filters may be optionally provided.

    :param datafile_path: Path to the Thermo Fisher raw file (.raw) containing the data.
    :type datafile_path: str
    :param t_min: Start time [s]
    :type t_min: float, optional
    :param t_max: End time [s]
    :type t_max: float, optional
    :param ppm: ppm precision for binning, defaults to 1. This value determines the mass
                tolerance for grouping m/z values, where a smaller ppm value results in
                finer binning and higher precision.
    :type ppm: int, optional
    :param polarity: + or -, Polarity of the scans to be retrieved, optional,
                    defaults to None
    :type polarity: str, optional
    :param reconstruct: When True, return a profile reconstructed as one Gaussian
                    per centroid (overlays the centroids exactly; matches Thermo's
                    profile, which is also a reconstruction) -- intended for
                    display. The default False returns the real measured profile,
                    which the instrument-function fit needs.
    :type reconstruct: bool, optional
    :raises ValueError: If the specified time range is invalid, or if no data is found
                        in the specified filters, or the specified polarity is not found
                        in the raw file.
    :return: The sum signal and the number of combined scans
    :rtype: tuple[xr.DataArray, float]
    """
    with open_backend(datafile_path) as backend:
        indices = backend.scan_indices(polarity=polarity, t_min=t_min, t_max=t_max)
        runtime.logger.debug(
            f"Selected {len(indices)} scans for sum signal computation. "
            f"Polarity: {polarity}, binning ppm: {ppm}, reconstruct: {reconstruct}."
        )
        # average=False restores the sum signal (averaged * scans combined).
        mz, sum_signal, num_of_combined_scans = backend.average_profile(
            indices, ppm=ppm, average=False, reconstruct=reconstruct
        )

    sum_signal_dask = da.from_array(sum_signal, chunks="auto")
    sum_signal = xr.DataArray(
        data=sum_signal_dask, dims=["mz"], coords={"mz": mz}, name="sum_signal"
    )

    return sum_signal, num_of_combined_scans


def get_tic_per_scan(
    datafile_path: str,
    timestamps: Iterable[float] | None = None,
    polarity: Literal["+", "-"] | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Allows filtering by timestamps and polarity.
    If timestamps are provided, the function will return the TIC
    values for the closest scan to each timestamp.

    :param datafile_path: Path to the Thermo Fisher raw file (.raw) containing the data.
    :type datafile_path: str
    :param timestamps: Optional iterable of timestamps [s] to
                       extract TIC values for, defaults to None
    :type timestamps: Iterable[float], optional
    :param polarity: + or -, Polarity of the scans to be retrieved,
                     optional, defaults to None
    :type polarity: str, optional
    :return: Tuple containing the scan timestamps [s] and TIC values as numpy arrays
    :rtype: tuple
    """
    with open_backend(datafile_path) as backend:
        scan_timestamp, scan_tic = backend.tic_per_scan(polarity=polarity)

    if timestamps is not None:
        requested_timestamps = np.asarray(list(timestamps), dtype=np.float64)

        if requested_timestamps.size == 0:
            return requested_timestamps, requested_timestamps

        # Find nearest scan index for each requested timestamp
        right_idx = np.searchsorted(scan_timestamp, requested_timestamps)
        right_idx = np.clip(right_idx, 0, len(scan_timestamp) - 1)
        left_idx = np.clip(right_idx - 1, 0, len(scan_timestamp) - 1)

        left_distance = np.abs(requested_timestamps - scan_timestamp[left_idx])
        right_distance = np.abs(scan_timestamp[right_idx] - requested_timestamps)
        nearest_idx = np.where(left_distance <= right_distance, left_idx, right_idx)

        scan_timestamp = scan_timestamp[nearest_idx]
        scan_tic = scan_tic[nearest_idx]

    return scan_timestamp, scan_tic


def get_scan_timestamps(
    datafile_path: str,
    t_min: float | None = None,
    t_max: float | None = None,
    polarity: Literal["+", "-"] | None = None,
    scan_type: Literal["Ms", "Ms2"] | None = "Ms",
) -> np.ndarray:
    """Extracts the scan timestamps [s] from the raw file,
    with optional polarity, time and scan-type filtering.

    :param datafile_path: Path to the Thermo Fisher raw file (.raw) containing the data.
    :type datafile_path: str
    :param t_min: Minimum time [s], optional, defaults to None
    :type t_min: float
    :param t_max: Maximum time [s], optional, defaults to None
    :type t_max: float
    :param polarity: Polarity of the scans to be retrieved,
                     either '+' or '-', optional, defaults to None
    :type polarity: str
    :param scan_type: Filter by scan type ('Ms' or 'Ms2'), or None for every
                      scan. Defaults to 'Ms', which is what callers reading the
                      MS1 time axis want; pass None to span a whole acquisition.
    :type scan_type: Literal['Ms', 'Ms2'] | None, optional
    :return: Array of filtered scan timestamps [s]
    :rtype: np.ndarray
    """
    with open_backend(datafile_path) as backend:
        return backend.scan_times(
            polarity=polarity, t_min=t_min, t_max=t_max, ms_type=scan_type
        )


def get_peak_timeseries(
    datafile_path: str,
    mzs: Iterable[float],
    t_min: float | None = None,
    t_max: float | None = None,
    polarity: Literal["+", "-"] | None = None,
    ppm: float = 5,
) -> xr.DataArray:
    """Extracts the peak timeseries for the specified m/z values
    in the time range (t_min, t_max).

    :param datafile_path: Path to the Thermo Fisher raw file (.raw) containing the data.
    :type datafile_path: str
    :param mzs: array of m/z values for which peak timeseries are required.
    :type mzs: float
    :param t_min: Start time [s], optional, defaults to None
    :type t_min: float
    :param t_max: End time [s], optional, defaults to None
    :type t_max: float
    :param polarity: + or -, Polarity of the scans to be retrieved,
                     optional, defaults to None
    :type polarity: str
    :param ppm: Mass tolerance in parts-per-million for centroid binning, defaults to 5.
    :type ppm: float, optional
    :return: An xarray DataArray containing the peak timeseries
    :rtype: xr.DataArray
    """
    mzs = np.asarray(mzs, dtype=float)

    with open_backend(datafile_path) as backend:
        intensities_for_mz_values, scan_times = backend.xic(
            mzs, ppm=ppm, polarity=polarity, t_min=t_min, t_max=t_max
        )

    peak_timeseries_dask = da.from_array(intensities_for_mz_values, chunks="auto")

    return xr.DataArray(
        peak_timeseries_dask,
        dims=("mz", "time"),
        coords={"mz": mzs, "time": np.array(scan_times)},
        name="signal",
    )


def _average_scans_centroids(
    RawFile: RawFileReaderAdapter,
    scan_indices_1based: list[int],
    ppm: int = 1,
    average: bool = False,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Average scans and extract centroid data.

    A helper that averages the given scans using Thermo's
    ppm-based binning and returns the centroided peaks. Does not perform
    file I/O or scan selection - the caller is responsible for providing an
    already-open RawFile and the desired scan indices.

    :param RawFile: An already-open RawFileReaderAdapter instance.
    :type RawFile: RawFileReaderAdapter
    :param scan_indices_1based: 1-based scan indices to average.
    :type scan_indices_1based: list[int]
    :param ppm: Mass tolerance in parts-per-million for centroid binning, defaults to 1.
    :type ppm: int, optional
    :param average: If True, return averaged intensities; if False,
                    scale by number of scans.
    :type average: bool, optional
    :return: Tuple of (masses, intensities, resolutions, signal_to_noise) arrays.
    :rtype: tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]
    """
    if ppm <= 0:
        raise ValueError(f"Invalid ppm value: {ppm}. ppm must be > 0.")

    _ensure_dotnet()
    from System.Collections.Generic import List
    from ThermoFisher.CommonCore.Data import Extensions, ToleranceUnits
    from ThermoFisher.CommonCore.Data.Business import MassOptions

    dotnet_indices = List[int]()
    for index in scan_indices_1based:
        dotnet_indices.Add(index)

    mass_option = MassOptions(ppm, ToleranceUnits.ppm)
    average_scan = Extensions.AverageScans(RawFile, dotnet_indices, mass_option)
    averaged_centroids = average_scan.CentroidScan.GetLabelPeaks()
    n_centroids = len(averaged_centroids)

    masses = np.fromiter(
        (c.Mass for c in averaged_centroids),
        dtype=np.float64,
        count=n_centroids,
    )
    intensities = np.fromiter(
        (c.Intensity for c in averaged_centroids),
        dtype=np.float64,
        count=n_centroids,
    )
    resolutions = np.fromiter(
        (c.Resolution for c in averaged_centroids),
        dtype=np.float64,
        count=n_centroids,
    )
    signal_to_noise = np.fromiter(
        (c.SignalToNoise for c in averaged_centroids),
        dtype=np.float64,
        count=n_centroids,
    )

    if not average:
        intensities *= average_scan.ScansCombined

    return masses, intensities, resolutions, signal_to_noise


def get_centroids(
    datafile_path: str,
    t_min: float | None = None,
    t_max: float | None = None,
    average: bool = False,
    ppm: int = 1,
    polarity: Literal["+", "-"] | None = None,
) -> tuple:
    """
    Extract centroided peaks from a Thermo Fisher raw file
    within a specified time range and for specified m/z values.

    This function reads the centroided spectrum by averaging
    scans in the given time window and optionally filtering
    by polarity. The function returns the filtered centroid
    m/z values, their intensities, and resolutions.

    :param datafile_path: Path to the Thermo Fisher raw file (.raw) containing the data.
    :type datafile_path: str
    :param t_min: Minimum time [s] for scan selection,
                  optional, defaults to None (start of run).
    :type t_min: float | None, optional
    :param t_max: Maximum time [s] for scan selection,
                  optional, defaults to None (end of run).
    :type t_max: float | None, optional
    :param average: If True, return averaged intensities; if False,
                    scale intensities by number of scans.
    :type average: bool, optional
    :param ppm: Mass tolerance in parts-per-million for centroid binning, defaults to 1.
    :type ppm: int, optional
    :param polarity: Polarity of scans to use ('+' or '-'),
                     optional, defaults to None (all polarities).
    :type polarity: Literal['+', '-'], optional
    :return: Tuple of (masses, intensities, resolutions,
             signal-to-noise ratios) for centroid peaks matching
             the criteria.
    :rtype: tuple of np.ndarray
    """
    with open_backend(datafile_path) as backend:
        indices = backend.scan_indices(polarity=polarity, t_min=t_min, t_max=t_max)
        return backend.average_centroids(indices, ppm=ppm, average=average)


def get_centroids_per_scan(
    datafile_path: str,
    t_min: float | None = None,
    t_max: float | None = None,
    mz_min: float | None = None,
    mz_max: float | None = None,
    polarity: Literal["+", "-"] | None = None,
    scan_type: Literal["Ms", "Ms2"] | None = None,
) -> list[dict[str, np.ndarray]]:
    """Reads centroided peaks from a Thermo Fisher raw file
    within a specified time range and m/z range.

    t_min=None is equal to min scan time, t_max=None is equal to max scan time.
    mz_min=None is equal to min m/z, mz_max=None is equal to max m/z.

    :param datafile_path: Path to the Thermo Fisher raw file (.raw) containing the data.
    :type datafile_path: str
    :param t_min: Minimum time [s], optional, defaults to None
    :type t_min: float
    :param t_max: Maximum time [s], optional, defaults to None
    :type t_max: float
    :param mz_min: Minimum m/z, optional, defaults to None
    :type mz_min: float
    :param mz_max: Maximum m/z, optional, defaults to None
    :type mz_max: float
    :param polarity: + or -, Polarity of the scans to be retrieved,
                     optional, defaults to None
    :type polarity: str
    :param scan_type: Filter by scan type ('Ms' or 'Ms2'),
                      optional, defaults to None (all scans)
    :type scan_type: str
    :return: List of dictionaries, each containing per-scan
             centroid masses, intensities, resolutions,
             signal-to-noise ratios, and timestamps.
    :rtype: list[dict[str, np.ndarray]]
    """
    with open_backend(datafile_path) as backend:
        return backend.centroids_per_scan(
            polarity=polarity,
            t_min=t_min,
            t_max=t_max,
            ms_type=scan_type,
            mz_min=mz_min,
            mz_max=mz_max,
        )


def _cluster_scans_by_parent(
    scan_events: dict[int, tuple[float, str]],
    parent_peak_tolerance: float = 0.001,
) -> dict[tuple[float, str], list[int]]:
    """Group MS2 scans by precursor m/z *and* activation.

    Takes a ``{scan_number: (precursor_mz, activation)}`` mapping (from a
    backend), clusters near-duplicate precursors within tolerance, and returns a
    mapping of ``(canonical parent peak m/z, activation)`` to scan numbers.
    Backend-agnostic: the event extraction itself lives in the backend
    (``ms2_events_by_scan``).

    The activation belongs in the key because a stepped-energy acquisition
    records one precursor at several collision energies -- as separate scan
    events, and (when stepped by hand) as consecutive blocks of scans rather
    than interleaved. Keying on m/z alone would average those steps into a
    single spectrum whose collision energy is a number the instrument never
    used. Clustering still runs over all the scans of a precursor, so every step
    of one precursor shares one canonical m/z.

    :param scan_events: Mapping of scan number to (precursor m/z, activation).
    :type scan_events: dict[int, tuple[float, str]]
    :param parent_peak_tolerance: Tolerance in Da for merging
                                  near-duplicate parent peaks.
    :type parent_peak_tolerance: float
    :return: Mapping of (canonical parent peak m/z, activation) to scan numbers.
    :rtype: dict[tuple[float, str], list[int]]
    """
    if not scan_events:
        return {}

    raw_parent_peaks = np.array([mz for mz, _ in scan_events.values()])
    sorted_unique = np.sort(np.unique(raw_parent_peaks))

    clusters: list[list[float]] = []
    for value in sorted_unique:
        if clusters and (value - clusters[-1][-1]) <= parent_peak_tolerance:
            clusters[-1].append(value)
        else:
            clusters.append([value])

    clustered_parent_peaks = [round(float(np.median(c)), 4) for c in clusters]

    # Only groups that actually got scans are returned. Clusters are built by
    # chaining adjacent values, so one can grow wider than the tolerance and
    # leave an extreme member further from the median than the tolerance
    # allows; such a scan matches no cluster and is dropped, exactly as before.
    parent_peak_mapping: dict[tuple[float, str], list[int]] = {}
    for scan_idx, (raw_pp, activation) in scan_events.items():
        for clustered_pp in clustered_parent_peaks:
            if abs(raw_pp - clustered_pp) <= parent_peak_tolerance:
                parent_peak_mapping.setdefault((clustered_pp, activation), []).append(
                    scan_idx
                )
                break

    return dict(sorted(parent_peak_mapping.items()))


def get_ms2_centroids_by_parent(
    datafile_path: str,
    t_min: float | None = None,
    t_max: float | None = None,
    polarity: Literal["+", "-"] | None = None,
    mz_min: float | None = None,
    mz_max: float | None = None,
    parent_peak_tolerance: float = 0.001,
    ppm: int = 1,
    average: bool = True,
) -> dict[tuple[float, str], tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]]:
    """Extract averaged centroids for each MS2 group in an MS2 raw file.

    Opens the raw file once, groups MS2 scans by parent peak *and* activation
    (see :func:`_cluster_scans_by_parent`), and averages each group using
    Thermo's native ppm-based binning. A stepped-energy acquisition therefore
    yields one averaged spectrum per collision energy rather than one spectrum
    blending them all.

    :param datafile_path: Path to the Thermo Fisher raw file (.raw).
    :type datafile_path: str
    :param t_min: Minimum time [s], optional.
    :type t_min: float | None, optional
    :param t_max: Maximum time [s], optional.
    :type t_max: float | None, optional
    :param polarity: Polarity filter ('+' or '-'), optional.
    :type polarity: Literal['+', '-'] | None, optional
    :param mz_min: Minimum parent peak m/z to include, optional.
                   Only parent peaks with m/z >= mz_min are processed.
    :type mz_min: float | None, optional
    :param mz_max: Maximum parent peak m/z to include, optional.
                   Only parent peaks with m/z <= mz_max are processed.
    :type mz_max: float | None, optional
    :param parent_peak_tolerance: Tolerance in Da for merging parent peaks.
    :type parent_peak_tolerance: float
    :param ppm: Mass tolerance in ppm for centroid binning, defaults to 1.
    :type ppm: int, optional
    :param average: If True, return averaged intensities; if False,
                    scale by scan count.
    :type average: bool, optional
    :return: Mapping of (parent peak m/z, activation) to
             (masses, intensities, resolutions, signal_to_noise).
    :rtype: dict[tuple[float, str], tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]]
    """
    with open_backend(datafile_path) as backend:
        events = backend.ms2_events_by_scan(polarity=polarity, t_min=t_min, t_max=t_max)
        parent_peak_mapping = _cluster_scans_by_parent(events, parent_peak_tolerance)
        if not parent_peak_mapping:
            return {}

        # Filter parent peaks by m/z range
        if mz_min is not None or mz_max is not None:
            low = mz_min if mz_min is not None else -np.inf
            high = mz_max if mz_max is not None else np.inf
            parent_peak_mapping = {
                key: indices
                for key, indices in parent_peak_mapping.items()
                if low <= key[0] <= high
            }
            if not parent_peak_mapping:
                return {}

        return {
            key: backend.average_centroids(scan_indices, ppm=ppm, average=average)
            for key, scan_indices in parent_peak_mapping.items()
        }


def get_ms2_summary_metadata(
    datafile_path: str,
    t_min: float | None = None,
    t_max: float | None = None,
    polarity: Literal["+", "-"] | None = None,
    parent_peak_tolerance: float = 0.001,
) -> dict:
    """Extract MS2 summary metadata from a Thermo raw file.

    Groups MS2 scans by parent peak and activation, extracts HCD energies and
    isolation width from trailer extra data, and counts MS1/MS2 scans.

    ``parent_peaks`` lists the distinct precursors; ``groups`` describes each
    (precursor, activation) pair separately, so a stepped-energy acquisition is
    reported step by step -- including each step's scan count and time span,
    which is what shows that its steps ran as consecutive blocks rather than
    interleaved. ``hcd_energy_map`` gives the calibrated energies a precursor
    was measured at, one entry per step, in group order.

    :param datafile_path: Path to the Thermo Fisher raw file (.raw).
    :type datafile_path: str
    :param t_min: Minimum time [s], optional.
    :type t_min: float | None, optional
    :param t_max: Maximum time [s], optional.
    :type t_max: float | None, optional
    :param polarity: Polarity filter ('+' or '-'), optional.
    :type polarity: Literal['+', '-'] | None, optional
    :param parent_peak_tolerance: Tolerance in Da for merging parent peaks.
    :type parent_peak_tolerance: float
    :return: Dictionary with parent_peaks, groups, hcd_energy_map,
             isolation_width, ms1_scan_count, ms2_scan_count,
             parent_peak_tolerance.
    :rtype: dict
    """
    with open_backend(datafile_path) as backend:
        all_count = len(backend.scan_indices(polarity, t_min, t_max, ms_type=None))
        # scan_indices raises NoScansFoundError when nothing matches, so an
        # MS1-only file would otherwise blow up here instead of reaching the
        # graceful empty-MS2 return below.
        try:
            ms2_count = len(backend.scan_indices(polarity, t_min, t_max, ms_type="Ms2"))
        except NoScansFoundError:
            ms2_count = 0
        ms1_count = all_count - ms2_count

        if ms2_count == 0:
            return {
                "parent_peaks": [],
                "groups": [],
                "hcd_energy_map": {},
                "isolation_width": None,
                "ms1_scan_count": ms1_count,
                "ms2_scan_count": 0,
                "parent_peak_tolerance": parent_peak_tolerance,
            }

        # Get MS2 scans grouped by (parent peak, activation)
        events = backend.ms2_events_by_scan(polarity, t_min, t_max)
        parent_peak_mapping = _cluster_scans_by_parent(events, parent_peak_tolerance)

        # Isolation width + (calibrated) HCD energy per MS2 scan. Thermo reads
        # these from the trailer ("MS2 Isolation Width:" / "HCD Energy V:");
        # backends that lack the calibrated HCD energy raise NotImplementedError.
        isolation_width, scan_idx_to_hcd = backend.ms2_acquisition_info(
            polarity, t_min, t_max
        )

        # Scan times, so each group can report the span it was acquired over.
        # scan_indices and scan_times select identically, so they pair by
        # position.
        ms2_indices = backend.scan_indices(polarity, t_min, t_max, ms_type="Ms2")
        ms2_times = backend.scan_times(polarity, t_min, t_max, ms_type="Ms2")
        # float(), not the numpy scalar the time array hands back: these go out
        # over the API, and a numpy float is not JSON-serializable.
        scan_idx_to_time = {
            idx: float(t) for idx, t in zip(ms2_indices, ms2_times, strict=True)
        }

        parent_peaks: list[float] = []
        groups: list[dict] = []
        hcd_energy_map: dict[float, list[float]] = {}
        for (pp, activation), scan_indices in parent_peak_mapping.items():
            if pp not in hcd_energy_map:
                parent_peaks.append(pp)
                hcd_energy_map[pp] = []

            # Averaged within the group, so the energies reported are ones the
            # instrument actually used. A value may itself be comma-separated
            # when the instrument stepped the energy *within* one scan, which
            # is a different thing from the per-scan stepping the group key
            # separates - each comma-separated step keeps its own entry.
            raw_values = [
                scan_idx_to_hcd[idx] for idx in scan_indices if idx in scan_idx_to_hcd
            ]
            step_dissociation_values = [
                [float(v) for v in str(val).split(",") if v.strip()]
                for val in raw_values
                if str(val).strip()
            ]
            energies: list[float] = []
            if step_dissociation_values:
                max_steps = max(len(row) for row in step_dissociation_values)
                for step_idx in range(max_steps):
                    step_values = [
                        row[step_idx]
                        for row in step_dissociation_values
                        if step_idx < len(row)
                    ]
                    energies.append(round(float(np.mean(step_values)), 2))
            hcd_energy_map[pp].extend(energies)

            group_times = [
                scan_idx_to_time[idx] for idx in scan_indices if idx in scan_idx_to_time
            ]
            groups.append(
                {
                    "parent_peak_mz": pp,
                    "activation": activation,
                    "hcd_energy": energies,
                    "scan_count": len(scan_indices),
                    "t_min": min(group_times) if group_times else None,
                    "t_max": max(group_times) if group_times else None,
                }
            )

        return {
            "parent_peaks": parent_peaks,
            "groups": groups,
            "hcd_energy_map": hcd_energy_map,
            "isolation_width": isolation_width,
            "ms1_scan_count": ms1_count,
            "ms2_scan_count": ms2_count,
            "parent_peak_tolerance": parent_peak_tolerance,
        }


def get_ms2_centroids_per_scan_for_parent(
    datafile_path: str,
    parent_peak_mz: float,
    t_min: float | None = None,
    t_max: float | None = None,
    polarity: Literal["+", "-"] | None = None,
    parent_peak_tolerance: float = 0.001,
    activation: str | None = None,
) -> tuple[list[dict[str, np.ndarray | float]], list[float]]:
    """Extract per-scan centroids and TIC values for MS2 scans matching a parent peak.

    Every activation of the parent peak is included unless ``activation`` names
    one. Keeping them together is the default because the fragment timeseries of
    a stepped-energy run is exactly the view that shows fragments appearing and
    disappearing as the energy steps.

    :param datafile_path: Path to the Thermo Fisher raw file (.raw).
    :type datafile_path: str
    :param parent_peak_mz: The parent peak m/z to match.
    :type parent_peak_mz: float
    :param t_min: Minimum time [s], optional.
    :type t_min: float | None, optional
    :param t_max: Maximum time [s], optional.
    :type t_max: float | None, optional
    :param polarity: Polarity filter ('+' or '-'), optional.
    :type polarity: Literal['+', '-'] | None, optional
    :param parent_peak_tolerance: Tolerance in Da for matching parent peaks.
    :type parent_peak_tolerance: float
    :param activation: Restrict to one activation (e.g. ``"hcd40.00"``),
                       optional; defaults to every activation of the parent.
    :type activation: str | None, optional
    :return: Tuple of (per-scan centroid dicts, per-scan TIC values).
             Each centroid dict has keys: masses, intensities, resolutions,
             signal_to_noise, timestamp.
    :rtype: tuple[list[dict], list[float]]
    """
    with open_backend(datafile_path) as backend:
        events = backend.ms2_events_by_scan(polarity, t_min, t_max)
        parent_peak_mapping = _cluster_scans_by_parent(events, parent_peak_tolerance)

        # Collect every group of this parent peak (one per activation), so the
        # scans stay in acquisition order across the steps.
        matching_scan_indices: list[int] = []
        for (pp, group_activation), scan_indices in parent_peak_mapping.items():
            if abs(pp - parent_peak_mz) > parent_peak_tolerance:
                continue
            if activation is not None and group_activation != activation:
                continue
            matching_scan_indices.extend(scan_indices)

        if not matching_scan_indices:
            return [], []

        return backend.ms2_centroids_for_scans(sorted(matching_scan_indices))


class RawFileMetadata:
    """Class to access metadata of a Thermo Fisher raw file."""

    def __init__(
        self,
        datafile_path: str,
        t_min: float | None = None,
        t_max: float | None = None,
        polarity: Literal["+", "-"] | None = None,
        scan_type: Literal["Ms", "Ms2"] | None = "Ms",
        **kwargs,
    ):
        self.datafile_path = datafile_path
        self.t_min = t_min
        self.t_max = t_max
        self.polarity = polarity
        self.scan_type = scan_type

    @property
    def instrument_details(self) -> dict:
        """Information about the instrument used for the acquisition.
        ChannelLabels and Units are not included as they are not serializable.
        """
        with open_backend(self.datafile_path) as backend:
            return backend.instrument_details()

    @property
    def scan_acquisition_settings(self) -> dict:
        """Acquisition settings for each scan (injection time, AGC target,
        isolation width, HCD energy, lock-mass info, etc.)
        """
        with open_backend(self.datafile_path) as backend:
            return backend.scan_acquisition_settings(
                self.polarity, self.t_min, self.t_max, self.scan_type
            )

    @property
    def scan_statistics(self) -> dict:
        """Get per scan statistics"""
        with open_backend(self.datafile_path) as backend:
            return backend.scan_statistics(
                self.polarity, self.t_min, self.t_max, self.scan_type
            )


class RawFileMetadataLegacy(RawFileMetadata):
    """Class to access metadata of a Thermo Fisher raw file."""

    @property
    def num_of_scans(self):
        """Number of scans in the raw file."""
        with open_backend(self.datafile_path) as backend:
            return backend.num_scans()

    @property
    def instrument(self):
        instrument_dict = self.instrument_details
        instrument_df = pd.DataFrame.from_dict(
            instrument_dict, orient="index", columns=["Value"]
        )
        return instrument_df

    @property
    def trailer(self):
        """Trailer information of the raw file"""
        trailer_data = self.scan_acquisition_settings
        trailer_dict = trailer_data["settings"]
        header_labels = trailer_data["header_labels"]

        trailer_df = pd.DataFrame.from_dict(trailer_dict, orient="columns")
        trailer_df.set_index(pd.Index(header_labels), inplace=True)
        return trailer_df

    @property
    def statistics(self):
        """Scan statistics of the raw file"""
        scan_stats = self.scan_statistics
        return pd.DataFrame.from_dict(scan_stats, orient="columns")

    @property
    def centroids_meta(self):
        """
        Returns a dictionary with centroided peak data for each scan.

        Structure:
        {
            "time": [...],  # list of scan times [s]
            "data": [
                {
                    "intensities": ...,
                    "mzs": ...,
                    "resolutions": ...,
                    "noises": ...
                    }
                    ...
                ],
                ...
            }
        }
        """
        with open_backend(self.datafile_path) as backend:
            return backend.centroids_meta()

    def to_dict(self):
        """Convert the metadata to a dictionary.

        The dictionary contains the number of scans, statistics per scan, and statistics
        per file. The statistics per scan and per file are represented as dictionaries.
        """
        # Concat scan-related data into a single dataframe
        per_scan_df = pd.concat([self.statistics, self.trailer], axis=0)
        # Merge per file data into a single dataframe (we currently have only one file)
        per_file_df = self.instrument

        return {
            "num_of_scans": self.num_of_scans,
            "stats_per_scan": per_scan_df.to_dict(),
            "stats_per_file": per_file_df.to_dict(),
            "centroids_meta": self.centroids_meta,
        }
