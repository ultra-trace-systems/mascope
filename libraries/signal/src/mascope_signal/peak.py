import asyncio
import math
import os
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor

import dask
import dask.array as da
import numpy as np
import pandas as pd
import xarray
from scipy.signal._peak_finding_utils import (  # ty:ignore[unresolved-import]
    _select_by_peak_distance,
)

import mascope_file.io as m_io
import mascope_file.name as m_name
import mascope_signal.compute as m_compute
import mascope_signal.fitting as m_fitting
import mascope_signal.stitch as m_stitch
from mascope_backend.db.id import gen_id
from mascope_match.params import (
    ORBI_FITTING_THRESHOLD,
    TOF_FITTING_THRESHOLD,
)
from mascope_signal.runtime import runtime
from mascope_thermo.thermo import NoScansFoundError
from mascope_tools.alignment.utils import flag_satellite_peaks


# Restrict large chunks for dask
dask.config.set(**{"array.slicing.split_large_chunks": True})

cpu_cores = os.cpu_count() or 1
max_workers = max(1, cpu_cores // 2)
_EXECUTOR: ProcessPoolExecutor | None = None
_EXECUTOR_LOCK = threading.Lock()


def _get_executor() -> ProcessPoolExecutor:
    """Get or create the global ProcessPoolExecutor (lazy initialization).

    The external check makes sure we don't acquire the lock unnecessarily
    after the executor is created.

    The internal check is needed to avoid race condition where multiple
    threads pass the first check before the executor is initialized.
    """
    global _EXECUTOR
    if _EXECUTOR is None:
        with _EXECUTOR_LOCK:
            if _EXECUTOR is None:
                _EXECUTOR = ProcessPoolExecutor(max_workers=max_workers)
    return _EXECUTOR


# Define the delta m/z around unit masses for peak detection
DMZ = 0.5
SIGNAL_TO_NOISE_THRESHOLD = 3

PEAK_ID_LENGTH = 20

#: The ``.props`` entry that records the decision a file's peak store is built
#: by: true where its peaks are detected per scan stream. Absent where nobody
#: has decided, which is every file converted before streams could be read
#: apart, and false once per stream has been decided against. It is what a
#: rebuild of the store goes by, so that re-detecting a file's peaks follows
#: what was decided for the file and never the setting of the day. It lists
#: no streams: the store's own ``streams`` attribute is the list its labels
#: index.
PER_STREAM_PROP = "peaks_per_stream"

#: The least relative distance between two rows of the m/z axis of a store
#: that holds more than one peak list, a per-stream store or a pooled one of
#: two polarities: about a part in a trillion, some thousands of steps of a
#: float. Far below anything the pipeline can tell apart, and far enough
#: that rescaling the axis cannot bring two rows back onto one double
#: (:func:`_strictly_increasing`).
MZ_ROW_SEPARATION = 2.0**-40


class PeakDetectionError(Exception):
    """Custom exception for peak detection errors."""

    pass


def _strictly_increasing(mz: np.ndarray) -> np.ndarray:
    """A sorted m/z axis with every row set apart from the one before it.

    The peak store finds a peak's row by its m/z, so two rows may not share
    one. Within one averaged spectrum they do not, but two streams of a file
    are two spectra on one axis, as its two polarities are, and two of their
    centroids can land on the same double. The later one is moved up by
    :data:`MZ_ROW_SEPARATION` of its m/z, which no tolerance in the pipeline
    can see.

    One step of a float would separate them, but not for good. An m/z
    calibration multiplies the stored axis by a factor near one, again at
    every recalibration, and a product is rounded: two neighbouring doubles
    can come out as one. Rows this far apart stay apart, since a rescale
    keeps their relative distance and its rounding can cost a pair one step
    of the thousands between them.

    :param mz: m/z values, sorted ascending
    :type mz: np.ndarray
    :return: The same values, each at least the separation above the last
    :rtype: np.ndarray
    """
    out = np.array(mz, dtype=np.float64)
    for i in range(1, out.size):
        least = out[i - 1] * (1.0 + MZ_ROW_SEPARATION)
        if out[i] < least:
            out[i] = least
    return out


class BasePeakDetector(ABC):
    def __init__(self, filename: str, instrument_functions: tuple):
        self._filename = filename
        self._peak_shape, self._resolution_function = instrument_functions

        self._sample_file_props = m_io.read_props(self._filename)
        self._sum_signal = m_compute.get_sum_signal(self._filename)

        self._peak_timeseries: xarray.Dataset | None = None
        # The decision this detection was handed: per stream, pooled, or none
        # for a rebuild. Only the raw Orbitrap detector is handed one.
        self._per_stream: bool | None = None
        # The scan streams the peaks are detected per, as the census gives
        # them; empty for a file detected whole, which is every file but a
        # raw Orbitrap one its detector takes stream by stream.
        self._streams: list[dict] = []

    @property
    def peak_timeseries(self) -> xarray.Dataset:
        if self._peak_timeseries is None:
            raise PeakDetectionError("Peak timeseries has not been allocated.")
        return self._peak_timeseries

    @property
    def u_list(self):
        """List of unique integer m/z values in the summed spectrum"""
        mz_values = self._sum_signal.mz.values
        u_list = np.unique(mz_values.astype(int))
        return u_list

    def _allocate_peak_timeseries(self, peaks: xarray.Dataset) -> None:
        """Allocate peak timeseries dataset structure."""
        # Get the tof values corresponding to the peak mzs
        mz_axis = self._sum_signal.mz.values
        peak_mzs = peaks.mz.values
        # Interpolate the index (tof) for each peak m/z
        unique_tofs = np.interp(peak_mzs, mz_axis, np.arange(len(mz_axis)))

        time_coord, scan_stream = self._scan_axis()
        peak_ids = [gen_id(PEAK_ID_LENGTH) for _ in range(peaks.mz.size)]
        data_coords = {
            "mz": peaks.mz,
            "time": time_coord,
            "tof": (("mz"), unique_tofs),
            "peak_id": (("mz"), peak_ids),
        }

        # Allocate dask arrays for peak areas and heights with NaN initialization
        # to save memory
        data_shape = (peaks.mz.size, time_coord.size)
        peak_areas = da.full(data_shape, np.nan, dtype=np.float64)
        peak_heights = da.full(data_shape, np.nan, dtype=np.float64)
        peak_timeseries_computed = da.full(peaks.mz.size, False, dtype=bool)
        sparsity = da.full(peaks.mz.size, 0.0, dtype=np.float64)
        data_vars = {
            "peak_areas": (("mz", "time"), peak_areas),
            "peak_heights": (("mz", "time"), peak_heights),
            "is_timeseries_computed": (("mz"), peak_timeseries_computed),
            "sparsity": (("mz"), sparsity),
        }
        if scan_stream is not None:
            data_vars["scan_stream"] = (("time"), scan_stream)

        peak_timeseries = xarray.Dataset(
            data_vars=data_vars,
            coords=data_coords,
        )
        self._peak_timeseries = xarray.merge([peak_timeseries, peaks])
        if self._streams:
            # After the merge, which keeps no attribute of what it merges
            self._peak_timeseries.attrs["streams"] = [
                stream["key"] for stream in self._streams
            ]

    def _scan_axis(self) -> tuple[np.ndarray, np.ndarray | None]:
        """The scans the store's time axis holds, and each one's stream.

        :return: The scan times [s], and for a file detected per stream the
            index of each scan's stream among ``self._streams``, else None
        :rtype: tuple[np.ndarray, np.ndarray | None]
        """
        return m_compute.get_scan_timestamps(self._filename), None

    def record_decision(self) -> None:
        """Record in the file's ``.props`` the decision its store is built by.

        Whether a file's peaks are detected per scan stream is decided by
        whoever processes the file: its first conversion, or an explicit
        re-processing. The record is that decision and nothing else. A
        rebuild is handed none, so it reads the record and never writes it,
        and the decision holds also through a rebuild that finds nothing to
        detect apart: the store is pooled meanwhile, and per stream again
        once the file reads back its streams.

        Called once the peaks are detected, before the store is written: the
        store follows the record, so that a store whose write is cut short is
        rebuilt the way it was being built. A file nobody has decided for,
        with nothing to detect apart, is left alone, so its ``.props`` stays
        what it was.
        """
        decision = self._per_stream
        if decision is None:
            return
        recorded = self._sample_file_props.get(PER_STREAM_PROP)
        if recorded is None and not (decision and self._streams):
            return
        if recorded is not decision:
            m_io.update_props(self._filename, {PER_STREAM_PROP: decision})

    async def write_peaks_to_zarr(self, overwrite=True):
        if self.peak_timeseries is None:
            raise PeakDetectionError("No peak timeseries to write to zarr.")

        runtime.logger.info("Writing peak timeseries to the sample file...")
        await m_io.write_peaks(
            self.peak_timeseries,
            self._filename,
            overwrite=overwrite,
        )
        runtime.logger.info("Writing peak timeseries completed.")

    @abstractmethod
    async def detect_peaks(
        self, progress_callback: Callable[[int], None] | None = None
    ):
        pass

    @abstractmethod
    def _flag_weak_peaks(self):
        pass

    @abstractmethod
    def _flag_satellite_peaks(self):
        pass


class OrbiPeakDetector(BasePeakDetector):
    """Peak detection for a raw Orbitrap file.

    A file is detected whole, every MS1 scan of a polarity averaged into one
    peak list, unless it is detected per scan stream: then each experiment of
    its acquisition method that recorded MS1 scans gets a peak list of its
    own, averaged over its own scans only (``mascope_thermo.streams``).
    Pooling two experiments divides each ion by the scans of both, so an ion
    only one of them measures is diluted.

    A file detected per stream is stitched as well: the streams of one
    polarity are the segments of one composite spectrum, in which each m/z is
    taken from the one stream that owns it (``mascope_signal.stitch``). Its
    store says which peaks that leaves, and by which map.

    :param per_stream: Whether a file whose method measures more than one
        thing in a polarity is detected per stream
        (``mascope_thermo.streams.peak_streams``). None goes by the decision
        the file's ``.props`` records, which is what a rebuild of the store
        wants: a file's samples are defined against its store as it was
        decided, and only an explicit re-processing may change that.
    """

    def __init__(
        self,
        filename: str,
        instrument_functions: tuple,
        per_stream: bool | None = None,
    ):
        super().__init__(filename, instrument_functions)
        self._per_stream = per_stream

    def _peak_streams(self) -> list[dict]:
        """The streams this file's peaks are to be detected per, or ``[]``.

        Asked for per stream, a file's streams have to be read: where that
        fails, the detection fails. The converter's own census of the same
        file is best-effort, because nothing is processed by it. This one
        decides how the file is processed. Detected whole instead, a file of
        several experiments would get the pooled store the decision was made
        to prevent, with nothing to show for it, and a file of one experiment
        cannot be told from it while its streams are unread.

        :raises PeakDetectionError: If the file's streams are asked for and
            cannot be read
        """
        per_stream = self._per_stream
        if per_stream is None:
            per_stream = bool(self._sample_file_props.get(PER_STREAM_PROP))
        if not per_stream:
            return []
        try:
            return m_compute.get_peak_streams(self._filename)
        except Exception as error:
            raise PeakDetectionError(
                f"Could not read the scan streams of '{self._filename}', which "
                "its peaks were to be detected per, so none were detected. "
                "Detected whole, a file of several experiments would have "
                "been pooled."
            ) from error

    async def detect_peaks(
        self, progress_callback: Callable[[int], None] | None = None, **kwargs
    ):
        """Detect peaks in the summed Orbitrap spectrum.

        :param progress_callback: Optional callback invoked with progress percentage (0-100).
        :type progress_callback: Callable[[int], None] | None
        :return: Sample file data with updated peak information
        :rtype: xarray.Dataset
        """
        # Handle None progress callback by using a no-op function
        progress_callback = progress_callback or (lambda progress: None)

        progress_callback(10)
        runtime.logger.debug("Reading centroids from the Thermo file...")
        self._streams = self._peak_streams()
        if self._streams:
            peaks = await self._extract_peaks_per_stream()
        else:
            peaks = await self._extract_peaks_per_polarity()

        progress_callback(80)
        runtime.logger.debug("Computing peak timeseries...")
        self._allocate_peak_timeseries(peaks)
        self._flag_weak_peaks()
        self._flag_satellite_peaks()
        if self._streams:
            self._stitch()
        progress_callback(100)

    def _stitch(self) -> None:
        """Stitch a file detected per stream: mark the peaks of its composites.

        Adds what a reader of the store needs to take one spectrum per
        polarity out of several peak lists:

        - ``composite`` along ``mz``: whether the peak's own stream owns its
          m/z under the stitch map. Every peak of a polarity with a single
          stream is its composite's, so the mask reads the same way for any
          per-stream store;
        - the map itself as the ``stitch_map`` attribute, so that nobody has
          to draw it again to know where the boundaries are, and a store
          stitched under another rule can be told from this one;
        - the ``stitch_overlaps`` attribute: what two streams read where
          both measure, which is what the composite leaves unused
          (:func:`mascope_signal.stitch.overlap_readings`).

        The map is in m/z as the instrument recorded them, and the peaks are
        on the file's calibrated axis, so a peak is placed by the factor the
        file carries. A file's mask is then the same whenever its peaks are
        detected, before its calibration or after.
        """
        store = self.peak_timeseries
        calibration = self._sample_file_props.get("mz_calibration")
        factor = calibration["par"]["calibration_factor"] if calibration else 1.0
        peak_mz, peak_stream = store.mz.values, store.stream.values

        stitch = m_stitch.stitch_map(self._streams)
        composite = m_stitch.composite_mask(
            peak_mz, peak_stream, store.polarity.values, stitch, calibration=factor
        )
        overlaps = m_stitch.overlap_readings(
            self._streams,
            peak_mz,
            peak_stream,
            store.sum_peak_heights.values,
            # The peaks a load of the store keeps
            ~(store.is_weak.values | store.is_satellite.values),
            np.bincount(store.scan_stream.values, minlength=len(self._streams)),
            calibration=factor,
        )
        self._peak_timeseries = store.assign({"composite": (("mz"), composite)})
        self._peak_timeseries.attrs[m_stitch.STITCH_MAP_ATTR] = stitch
        self._peak_timeseries.attrs[m_stitch.STITCH_OVERLAPS_ATTR] = overlaps

    async def _extract_peaks_per_polarity(self) -> xarray.Dataset:
        """The peaks of a file detected whole: one list per polarity."""
        # Get CALIBRATED centroids for each polarity present in the file.
        # Only a genuinely-absent polarity (NoScansFoundError) is skipped; any
        # other failure is a real error and must propagate with its true cause
        # rather than being masked as an empty-concatenate downstream.
        datasets = []
        for polarity in ("+", "-"):
            try:
                datasets.append(await self._extract_peaks_for_polarity(polarity))
            except NoScansFoundError:
                runtime.logger.debug(
                    f"No {polarity} polarity scans in the file; skipping."
                )

        if not datasets:
            raise PeakDetectionError(
                f"No usable scans found for either polarity in '{self._filename}'."
            )
        # Two polarities are two spectra on one axis, as two streams are, and
        # a centroid of each can land on the same m/z. One polarity is one
        # spectrum, which does not tie with itself.
        peaks = xarray.concat(datasets, dim="mz").sortby("mz")
        return peaks.assign_coords(mz=("mz", _strictly_increasing(peaks.mz.values)))

    async def _extract_peaks_per_stream(self) -> xarray.Dataset:
        """The peaks of a file detected per stream: one list per MS1 stream.

        Each peak carries its stream as an index into ``self._streams``. A
        stream the reader finds no scans for is not skipped the way an absent
        polarity is: the census just named it, so that is a fault.
        """
        runtime.logger.info(
            f"Detecting the peaks of '{self._filename}' per scan stream: "
            f"{'; '.join(stream['key'] for stream in self._streams)}"
        )
        datasets = []
        for index, stream in enumerate(self._streams):
            peaks = await self._extract_peaks_for_polarity(
                stream["signature"]["polarity"], stream=stream["key"]
            )
            datasets.append(
                peaks.assign(
                    stream=(("mz"), np.full(peaks.mz.shape, index, dtype=np.int16))
                )
            )
        peaks = xarray.concat(datasets, dim="mz").sortby("mz")
        return peaks.assign_coords(mz=("mz", _strictly_increasing(peaks.mz.values)))

    def _scan_axis(self) -> tuple[np.ndarray, np.ndarray | None]:
        """Detected per stream, a file's axis is its streams' scans, each as
        its own stream selects them, in time order."""
        if not self._streams:
            return super()._scan_axis()
        times, labels = [], []
        for index, stream in enumerate(self._streams):
            stream_times = m_compute.get_scan_timestamps(
                self._filename, stream=stream["key"]
            )
            times.append(stream_times)
            labels.append(np.full(stream_times.shape, index, dtype=np.int16))
        times, labels = np.concatenate(times), np.concatenate(labels)
        order = np.argsort(times, kind="stable")
        return times[order], labels[order]

    async def _extract_peaks_for_polarity(
        self, polarity: str, stream: str | None = None
    ) -> xarray.Dataset:
        """A workaround to extract peaks for a given polarity from Thermo Orbitrap files.

        :param stream: Key of the one scan stream to average, of that
            polarity; None averages every MS1 scan of the polarity.
        """
        (
            peak_mzs,
            peak_heights,
            resolutions,
            signal_to_noise,
        ) = await m_compute.get_orbi_centroids(
            self._filename, polarity=polarity, stream=stream
        )

        # The m/z peak area is the integral of the analytic peak model, so it is
        # computed on a self-built grid inside calculate_peak_area and does not
        # need a profile-window slice here (the OpenTFRaw profile is sparse and
        # would otherwise leave many centroids in empty windows).
        peak_areas = np.array(
            [
                m_fitting.calculate_peak_area(
                    None,
                    self._peak_shape,
                    (peak_mzs[i], peak_heights[i], resolutions[i]),
                    sample_interval=None,
                )
                for i in range(len(peak_mzs))
            ]
        )

        return xarray.Dataset(
            {
                "sum_peak_areas": (("mz"), peak_areas),
                "sum_peak_heights": (("mz"), peak_heights),
                "signal_to_noise": (("mz"), signal_to_noise),
                "polarity": (("mz"), np.full(peak_mzs.shape, polarity)),
            }
        ).assign_coords(mz=("mz", peak_mzs))

    def _flag_weak_peaks(self):
        """Flag weak peaks based on signal-to-noise ratio."""
        low_snr = (
            self.peak_timeseries.signal_to_noise.values < SIGNAL_TO_NOISE_THRESHOLD
        )
        is_weak = low_snr
        self._peak_timeseries = self.peak_timeseries.assign(
            {"is_weak": (("mz"), is_weak)}
        )

    def _flag_satellite_peaks(self):
        peaks_df = pd.DataFrame(
            {
                "mz": self.peak_timeseries.mz.values,
                "intensity": self.peak_timeseries.sum_peak_heights.values,
            }
        )
        if not self._streams:
            peaks_df = flag_satellite_peaks(peaks_df)
            is_satellite = peaks_df["is_satellite_peak"].values
        else:
            # A satellite is a sidelobe of a strong peak of its own spectrum,
            # and a per-stream store's axis holds one spectrum per stream:
            # judged together, a real peak of one stream would be read as the
            # sidelobe of a strong peak of another.
            is_satellite = np.zeros(len(peaks_df), dtype=bool)
            peak_stream = self.peak_timeseries.stream.values
            for index in range(len(self._streams)):
                in_stream = peak_stream == index
                if in_stream.any():
                    flagged = flag_satellite_peaks(
                        peaks_df[in_stream].reset_index(drop=True)
                    )
                    is_satellite[in_stream] = flagged["is_satellite_peak"].values
        self._peak_timeseries = self.peak_timeseries.assign(
            {"is_satellite": (("mz"), is_satellite)}
        )


class TofPeakDetector(BasePeakDetector):
    async def detect_peaks(
        self,
        progress_callback: Callable[[int], None] | None = None,
        max_n_peaks: int = 5,
        **kwargs,
    ) -> None:
        """Detect peaks in the summed TOF spectrum around each unit mass in u_list.

        :param max_n_peaks: Maximum number of peaks to fit per unit mass, by default 5
        :type max_n_peaks: int, optional
        :param progress_callback: Optional callback invoked with progress percentage (0-100).
        :type progress_callback: Callable[[int], None] | None
        """
        # Handle None progress callback by using a no-op function
        progress_callback = progress_callback or (lambda progress: None)

        self._sample_interval = self._sample_file_props.get("sample_interval", 0.25)
        specs_to_fit = self._segment_spectrum_for_fitting()

        loop = asyncio.get_event_loop()
        executor = _get_executor()

        # Fill in asynchronous operations
        futures = [
            loop.run_in_executor(
                executor,
                m_fitting.fit_n_peaks,
                mz_chunk,
                spec_chunk,
                self._peak_shape,
                self._resolution_function,
                self.peak_fitting_threshold,
                self._sample_interval,
                max_n_peaks,
            )
            for mz_chunk, spec_chunk in specs_to_fit
        ]

        peaks = []
        last_progress = 0
        fit_warnings = set()
        runtime.logger.debug("Run peak detection")
        for i, future in enumerate(asyncio.as_completed(futures)):
            fit, detected_peaks, captured_warnings = await future
            if fit:
                peaks.extend(detected_peaks)
            for warning in captured_warnings:
                fit_warnings.add(warning)
            progress = 100 * (i + 1) / len(futures)
            rounded_progress = math.floor(progress / 10) * 10
            if rounded_progress != last_progress:
                runtime.logger.info(f"Peak detection progress: {rounded_progress}%")
                progress_callback(rounded_progress)
            last_progress = rounded_progress

        # Log unique warnings
        for warning in fit_warnings:
            runtime.logger.debug(f"Peak detection warning: {warning}")

        if len(peaks) > 0:
            peak_mzs, peak_heights, peak_areas = zip(
                *[(p[0], p[1], p[3]) for p in peaks]
            )
        else:
            # Nothing was fitted
            peak_mzs, peak_heights, peak_areas = [], [], []

        peak_mzs = np.array(peak_mzs)
        peak_heights = np.array(peak_heights)
        peak_areas = np.array(peak_areas)

        positive_mask = (peak_heights > 0) & (peak_areas > 0)
        peak_mzs = peak_mzs[positive_mask]
        peak_heights = peak_heights[positive_mask]
        peak_areas = peak_areas[positive_mask]

        # Sort peaks by m/z before deduplication to ensure correct grouping of close peaks
        sort_indices = np.argsort(peak_mzs)
        peak_mzs = peak_mzs[sort_indices]
        peak_heights = peak_heights[sort_indices]
        peak_areas = peak_areas[sort_indices]

        peak_mzs, peak_heights, peak_areas = self._deduplicate_peaks(
            peak_mzs,
            peak_heights,
            peak_areas,
        )

        polarity = m_compute.get_polarity_options(self._filename)
        signal_to_noise = self._compute_snr(peak_mzs, peak_heights)
        peaks = xarray.Dataset(
            {
                "sum_peak_areas": (("mz"), peak_areas),
                "sum_peak_heights": (("mz"), peak_heights),
                "signal_to_noise": (("mz"), signal_to_noise),
                "polarity": (("mz"), np.full(peak_mzs.shape, polarity)),
            }
        ).assign_coords(mz=("mz", peak_mzs))

        runtime.logger.debug("Computing peak timeseries...")
        self._allocate_peak_timeseries(peaks)
        self._flag_weak_peaks()
        self._flag_satellite_peaks()

    @property
    def peak_fitting_threshold(self):
        return TOF_FITTING_THRESHOLD

    def _segment_spectrum_for_fitting(self):
        """Segment the summed TOF spectrum into chunks around each unit mass in u_list"""
        runtime.logger.debug("Segment TOF spectrum for peak detection")
        sum_mz = self._sum_signal.mz.values
        sum_values = self._sum_signal.values
        specs_to_fit = [
            (
                sum_mz[(sum_mz >= u - DMZ) & (sum_mz <= u + DMZ)],
                sum_values[(sum_mz >= u - DMZ) & (sum_mz <= u + DMZ)],
            )
            for u in self.u_list
        ]
        return specs_to_fit

    def _compute_snr(
        self,
        peak_mzs: np.ndarray,
        peak_heights: np.ndarray,
    ) -> np.ndarray:
        """Compute signal-to-noise ratio for given peaks

        :param peak_mzs: Fitted peak m/z values
        :type peak_mzs: np.ndarray
        :param peak_heights: Fitted peak heights
        :type peak_heights: np.ndarray
        :return: Signal-to-noise ratio array
        :rtype: np.ndarray
        """
        mz_axis = self._sum_signal.mz.values
        signal = self._sum_signal.values
        snr = np.empty(len(peak_mzs), dtype=np.float64)

        # Compute exclusion zone from the resolution function
        resolutions = self._resolution_function(peak_mzs)
        exclusion = peak_mzs / resolutions

        # Compute baseline window as 10 times the exclusion zone
        window = 10 * exclusion

        # Vectorized baseline window calculation
        left_min = peak_mzs - window
        left_max = peak_mzs - exclusion
        right_min = peak_mzs + exclusion
        right_max = peak_mzs + window

        # For each peak, select baseline regions and compute noise std
        for i in range(len(peak_mzs)):
            left_mask = (mz_axis >= left_min[i]) & (mz_axis <= left_max[i])
            right_mask = (mz_axis >= right_min[i]) & (mz_axis <= right_max[i])
            baseline = signal[left_mask | right_mask]
            # Use robust estimator if baseline is non-Gaussian
            noise_std = np.std(baseline) if baseline.size > 0 else np.nan
            snr[i] = peak_heights[i] / noise_std if noise_std > 0 else np.nan

        return snr

    def _flag_weak_peaks(self):
        """Flag weak peaks. Currently no weak peak criteria for TOF data."""
        is_weak = np.full(len(self.peak_timeseries.mz), False, dtype=bool)
        self._peak_timeseries = self.peak_timeseries.assign(
            {"is_weak": (("mz"), is_weak)}
        )

    def _flag_satellite_peaks(self):
        """Flag satellite peaks. Currently no satellite peak criteria for TOF data."""
        is_satellite = np.full(len(self.peak_timeseries.mz), False, dtype=bool)
        self._peak_timeseries = self.peak_timeseries.assign(
            {"is_satellite": (("mz"), is_satellite)}
        )

    def _deduplicate_peaks(
        self,
        peak_mzs: np.ndarray,
        peak_heights: np.ndarray,
        peak_areas: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Remove numerically duplicated peaks. This may occur at segment edges.
        Assumes peaks are sorted by m/z.

        Algorithm:
        - Compute an absolute tolerance based on the mz_axis spacing
        - Group peaks with near-equal m/z values within this tolerance.
        - For each group, keep the peak with the highest height (and latest index in
            case of ties).
        """
        if peak_mzs.size <= 1:
            return peak_mzs, peak_heights, peak_areas

        # Compute m/z spacing tolerance based on the minimum spacing in the mz axis
        mz_axis = self._sum_signal.mz.values
        mz_axis_spacing_diffs = np.abs(np.diff(mz_axis))
        spacing_tol = np.min(mz_axis_spacing_diffs) / 2

        # Group consecutive near-equal m/z values
        same_as_prev = np.isclose(
            peak_mzs[1:], peak_mzs[:-1], rtol=0.0, atol=spacing_tol
        )
        group_id = np.empty(peak_mzs.size, dtype=np.intp)
        group_id[0] = 0
        group_id[1:] = np.cumsum(~same_as_prev)

        # Pick best per group:
        # - higher peak_heights first
        # - for ties, later index first
        peak_indices = np.arange(peak_mzs.size, dtype=np.intp)
        order = np.lexsort((-peak_indices, -peak_heights, group_id))
        ordered_groups = group_id[order]
        first_in_group = np.r_[True, ordered_groups[1:] != ordered_groups[:-1]]
        keep_indices = order[first_in_group]
        filtered_out_indices = order[~first_in_group]

        if filtered_out_indices.size > 0:
            filtered_out_peaks = (
                peak_mzs[filtered_out_indices],
                peak_heights[filtered_out_indices],
                peak_areas[filtered_out_indices],
            )
            runtime.logger.debug(
                f"Deduplicated {filtered_out_peaks[0].size} close peaks: "
                f"m/z={filtered_out_peaks[0]}, heights={filtered_out_peaks[1]}"
            )

        return (
            peak_mzs[keep_indices],
            peak_heights[keep_indices],
            peak_areas[keep_indices],
        )


class OrbiZarrPeakDetector(TofPeakDetector):
    def _segment_spectrum_for_fitting(self):
        """Segment the summed Orbi spectrum into chunks around each unit mass in u_list"""
        runtime.logger.debug("Segment Orbi spectrum for peak detection")
        sum_signal_mz = self._sum_signal.mz.values
        sum_signal = self._sum_signal.values
        # Stack mass ranges
        u_range = np.vstack([self.u_list - DMZ, self.u_list + DMZ])
        # Broadcast the u_range array to have the same shape as mz
        u_range = u_range[:, :, np.newaxis]
        # Create boolean masks indicating which elements of spec fall within each range
        mask_u_list = (sum_signal_mz >= u_range[0]) & (sum_signal_mz <= u_range[1])
        mask_u_list = mask_u_list.any(axis=0)
        # Update mz and spec
        mz = sum_signal_mz[mask_u_list]
        sum_spec = sum_signal[mask_u_list]

        if sum_spec.size == 0:
            # Nothing to fit
            return []

        # Remove tiny noise from the sum spectrum
        threshold = n_scans = m_compute.get_scan_timestamps(  # noqa: F841
            self._filename
        ).size
        sum_spec[sum_spec < threshold] = 0
        # Get non-zero indices
        non_zero_indices = np.flatnonzero(sum_spec)
        if len(non_zero_indices) == 0:
            # Return an empty list if there are no non-zero indices
            return []
        # Split in chunks taking into account repeating zeros
        non_zero_indices = np.split(
            non_zero_indices, np.where(np.diff(non_zero_indices) > 2)[0] + 1
        )
        # Add zeros on chunk borders
        non_zero_indices = [
            np.concatenate(([chunk[0] - 1], chunk, [chunk[-1] + 1]))
            for chunk in non_zero_indices
        ]
        # Check wrong indices on the spectrum ends
        if non_zero_indices[0][0] < 0:
            # Remove negative index in the first chunk
            non_zero_indices[0] = non_zero_indices[0][1:]
        if non_zero_indices[-1][-1] == len(sum_spec):
            # Remove out-of-range index from the last chunk
            non_zero_indices[-1] = non_zero_indices[-1][:-1]
        # Remove short chunks
        non_zero_indices = [chunk for chunk in non_zero_indices if len(chunk) > 4]

        specs_to_fit = [(mz[chunk], sum_spec[chunk]) for chunk in non_zero_indices]

        return specs_to_fit

    @property
    def peak_fitting_threshold(self):
        return ORBI_FITTING_THRESHOLD

    def _flag_satellite_peaks(self):
        peaks_df = pd.DataFrame(
            {
                "mz": self.peak_timeseries.mz.values,
                "intensity": self.peak_timeseries.sum_peak_heights.values,
            }
        )
        peaks_df = flag_satellite_peaks(peaks_df)
        self._peak_timeseries = self.peak_timeseries.assign(
            {"is_satellite": (("mz"), peaks_df["is_satellite_peak"].values)}
        )


class TofZarrPeakDetector(TofPeakDetector):
    pass


def compute_peaks(
    filename: str,
    instrument_functions: tuple,
    progress_callback: Callable[[int], None] | None = None,
    per_stream: bool | None = None,
):
    """Compute peaks for a sample file.

    :param filename: Filename of the sample file.
    :type filename: str
    :param instrument_functions: Tuple containing peak shape and resolution function.
    :type instrument_functions: tuple
    :param progress_callback: Optional callback invoked with progress percentage (0-100).
    :type progress_callback: Callable[[int], None] | None
    :param per_stream: For a raw Orbitrap file, whether one that holds more
        than one MS1 scan stream in a polarity gets a peak list per stream.
        Given by whoever decides how the file is processed: its first
        conversion, or an explicit re-processing. None, the default, rebuilds
        the store by the decision the file's ``.props`` records and leaves
        that record as it is, so that re-detecting a file's peaks never
        changes what was decided for it.
    :type per_stream: bool | None
    :raises TimeoutError: If another process was detecting the same file's
        peaks for longer than ``mascope_file.io.ZARR_PROCESS_LOCK_TIMEOUT``
    """
    # One detection of a file at a time, in whichever process: the detector
    # reads the recorded decision when it is made, and a rebuild that read it
    # before an explicit decision was recorded would write the store the old
    # record describes over the one the new record does.
    with m_io.zarr_write_lock(m_io.peak_detection_lock_path(filename)):
        peak_detector = get_peak_detector(filename, instrument_functions, per_stream)
        asyncio.run(peak_detector.detect_peaks(progress_callback=progress_callback))
        peak_detector.record_decision()
        asyncio.run(peak_detector.write_peaks_to_zarr())


def write_empty_peak_timeseries(filename: str) -> None:
    """Write an empty peak_timeseries.zarr for blank measurements.

    :param filename: Filename of the sample file.
    :type filename: str
    """
    time_coord = m_compute.get_scan_timestamps(filename)
    empty_dataset = xarray.Dataset(
        data_vars={
            "peak_areas": (
                ("mz", "time"),
                np.empty((0, time_coord.size), dtype=np.float64),
            ),
            "peak_heights": (
                ("mz", "time"),
                np.empty((0, time_coord.size), dtype=np.float64),
            ),
            "is_timeseries_computed": (("mz",), np.empty(0, dtype=bool)),
            "sparsity": (("mz",), np.empty(0, dtype=np.float64)),
            "is_weak": (("mz",), np.empty(0, dtype=bool)),
            "is_satellite": (("mz",), np.empty(0, dtype=bool)),
            "signal_to_noise": (("mz",), np.empty(0, dtype=np.float64)),
            "sum_peak_areas": (("mz",), np.empty(0, dtype=np.float64)),
            "sum_peak_heights": (("mz",), np.empty(0, dtype=np.float64)),
            "polarity": (("mz",), np.empty(0, dtype="<U2")),
        },
        coords={
            "mz": ("mz", np.empty(0, dtype=np.float64)),
            "time": time_coord,
            "tof": (("mz",), np.empty(0, dtype=np.float64)),
            "peak_id": (("mz",), np.empty(0, dtype=f"<U{PEAK_ID_LENGTH}")),
        },
    )
    asyncio.run(m_io.write_peaks(empty_dataset, filename, overwrite=True))


def get_peak_detector(
    filename: str,
    instrument_functions: tuple,
    per_stream: bool | None = None,
):
    """Factory function to get the appropriate peak detector based on the sample file type.

    :param filename: Path to the sample file.
    :type filename: str
    :param instrument_functions: Tuple containing peak shape and resolution function.
    :type instrument_functions: tuple
    :param per_stream: See :func:`compute_peaks`. Read by the raw Orbitrap
        detector alone: no other sample type holds more than one scan stream.
    :type per_stream: bool | None
    :raises PeakDetectionError: If the sample file type is unsupported.
    :return: An instance of the appropriate peak detector.
    :rtype: BasePeakDetector
    """
    sample_file_type = m_name.get_sample_file_type(filename)
    match sample_file_type:
        case "orbi_raw":
            return OrbiPeakDetector(filename, instrument_functions, per_stream)
        case "tof_h5":
            return TofPeakDetector(filename, instrument_functions)
        case "orbi_zarr":
            return OrbiZarrPeakDetector(filename, instrument_functions)
        case "tof_zarr":
            return TofZarrPeakDetector(filename, instrument_functions)
        case _:
            raise PeakDetectionError(
                f"Unsupported sample file type: {sample_file_type}"
            )


def filter_peaks(
    peaks: xarray.DataArray,
    mz_range: tuple | None = None,
    t_range: tuple | None = None,
    intensity: float | None = None,
    distance: float | None = None,
) -> xarray.DataArray:
    """
    Filter peaks by m/z range, time range, intensity, and minimum distance.

    :param peaks: Peak data array.
    :type peaks: xarray.DataArray
    :param mz_range: Tuple (min_mz, max_mz) to filter m/z, defaults to None.
    :type mz_range: tuple, optional
    :param t_range: Tuple (min_time, max_time) to filter time, defaults to None.
    :type t_range: tuple, optional
    :param intensity: Minimum intensity threshold, defaults to None.
    :type intensity: float, optional
    :param distance: Minimum distance between peaks, defaults to None.
    :type distance: float, optional
    :return: Filtered peaks as xarray.DataArray.
    :rtype: xarray.DataArray
    """
    # --- Lazy coordinate slicing ---
    if mz_range is not None:
        peaks = peaks.sel(mz=slice(*mz_range))
    if t_range is not None:
        peaks = peaks.sel(time=slice(*t_range))

    # Remove empty mz rows
    peaks = peaks.dropna(dim="mz", how="all")

    # --- Compute peak intensities ---
    if "time" in peaks.dims:
        peak_intensities = peaks.sum("time")
    else:
        peak_intensities = peaks

    # --- Intensity filtering ---
    if intensity is not None:
        peaks = peaks.where(peak_intensities > intensity, drop=True)
        peak_intensities = peak_intensities.where(
            peak_intensities > intensity, drop=True
        )

    # --- Distance filtering (here we need numpy arrays, so we materialize) ---
    if distance is not None:
        tof_vals = peaks["tof"].values.astype(np.intp)
        heights = peak_intensities.values.astype(np.float64)
        keep = _select_by_peak_distance(tof_vals, heights, distance)
        peaks = peaks.isel(mz=keep)

    return peaks


def get_peaks(sample_file: xarray.Dataset, intensity_mode="area"):
    """
    Retrieve peak data from a sample file.

    :param sample_file: Sample file dataset containing peak data.
    :type sample_file: xarray.Dataset
    :param intensity_mode: Which intensity to return, "area" or "height". Defaults to "area".
    :type intensity_mode: str, optional
    :raises ValueError: If intensity_mode is not "area" or "height".
    :return: Peak data array (areas or heights).
    :rtype: xarray.DataArray
    """
    if intensity_mode == "area":
        peaks = sample_file.peak_areas
    elif intensity_mode == "height":
        peaks = sample_file.peak_heights
    else:
        raise ValueError("intensity_mode must be either 'height' or 'area'")
    sample_file_type = m_name.get_sample_file_type(sample_file.props["filename"])
    if sample_file_type == "tof_zarr" or sample_file_type == "orbi_zarr":
        peaks = peaks.dropna(dim="mz", how="all")
    return peaks


def find_closest_indices(axis, values):
    # axis and values must be 1D numpy arrays
    idxs = np.searchsorted(axis, values)
    idxs = np.clip(idxs, 1, len(axis) - 1)
    left = axis[idxs - 1]
    right = axis[idxs]
    idxs -= values - left < right - values
    return idxs
