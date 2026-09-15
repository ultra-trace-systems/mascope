"""
Implementation of the virtual lock mass (VLM) algorithm based on https://doi.org/10.1038/s41598-019-44923-8
"""

import heapq
import warnings
from abc import ABC, abstractmethod
from collections import deque

import numpy as np
import pandas as pd


class Spectrum(ABC):
    """Abstract base class for a mass spectrum"""

    @property
    @abstractmethod
    def mz(self) -> np.ndarray:
        pass

    @property
    @abstractmethod
    def intensity(self) -> np.ndarray:
        pass

    @property
    @abstractmethod
    def resolution(self) -> np.ndarray:
        pass

    @property
    @abstractmethod
    def metadata(self) -> dict:
        pass


class CentroidedSpectrum(Spectrum):
    def __init__(
        self, mz, intensity, resolution, signal_to_noise, peak_id=None, metadata=None
    ):
        self._mz = np.array(mz)
        self._intensity = np.array(intensity)
        self._resolution = np.array(resolution)
        self._signal_to_noise = np.array(signal_to_noise)
        if peak_id is not None:
            self._peak_id = np.array(peak_id)
        else:
            self._peak_id = np.arange(len(self._mz))
        self._metadata = metadata or {}

    @property
    def mz(self):
        return self._mz

    @property
    def intensity(self):
        return self._intensity

    @property
    def resolution(self):
        return self._resolution

    @property
    def signal_to_noise(self):
        return self._signal_to_noise

    @property
    def peak_id(self):
        return self._peak_id

    @property
    def metadata(self):
        return self._metadata


class Spectra:
    def __init__(self, spectra: list[Spectrum], timestamps: np.ndarray):
        self.spectra = spectra
        self.timestamps = pd.to_datetime(timestamps, unit="s")

        non_empty_spectra = [s for s in spectra if s.mz.size > 0]
        if not non_empty_spectra:
            self.mz_min, self.mz_max = 0, 0
        else:
            self.mz_min = min(s.mz.min() for s in non_empty_spectra)
            self.mz_max = max(s.mz.max() for s in non_empty_spectra)

    def __getitem__(self, index: int) -> Spectrum:
        return self.spectra[index]

    def __len__(self) -> int:
        return len(self.spectra)

    def __iter__(self):
        return iter(self.spectra)

    def _cluster_and_map_peaks(self, window_factor: float) -> pd.DataFrame:
        """
        A private helper method that contains the core logic for flattening,
        sorting, and clustering peaks from all spectra.

        Returns a pandas DataFrame mapping every single peak to a final cluster.
        """
        if not self.spectra:
            return pd.DataFrame()

        # 1. Concatenate data from all spectra, keeping track of the original scan index
        all_data = []
        for i, s in enumerate(self.spectra):
            for j in range(len(s.mz)):
                all_data.append(
                    (
                        i,
                        s.mz[j],
                        s.intensity[j],
                        s.resolution[j],
                        s.signal_to_noise[j],
                        s.peak_id[j],
                    )
                )

        if not all_data:
            return pd.DataFrame()

        # Create a DataFrame and sort by m/z for efficient processing
        df = pd.DataFrame(
            all_data,
            columns=[
                "scan_idx",
                "mz",
                "intensity",
                "resolution",
                "signal_to_noise",
                "peak_id",
            ],
        )
        df.sort_values("mz", inplace=True)
        df.reset_index(drop=True, inplace=True)

        # 2. Iteratively find clusters and assign a unique cluster_id to each peak
        cluster_id = 0
        current_index = 0
        cluster_assignments = np.full(len(df), -1, dtype=int)

        while current_index < len(df):
            # Define cluster window based on the current peak's measured resolution
            start_mz = df.at[current_index, "mz"]
            start_resolution = df.at[current_index, "resolution"]

            # Peak width (FWHM) = m/z / R
            peak_width = start_mz / start_resolution if start_resolution > 0 else 0
            delta = peak_width * window_factor

            # Find all peaks within the window
            end_index = df["mz"].searchsorted(start_mz + delta, side="right")

            # Assign the current cluster_id to all peaks in this window
            cluster_assignments[current_index:end_index] = cluster_id

            cluster_id += 1
            current_index = end_index

        df["cluster_id"] = cluster_assignments
        return df

    def compute_sum_spectrum(
        self, window_factor=1, average=False
    ) -> CentroidedSpectrum:
        """
        Compute sum spectrum, optionally averaged.
        """
        clustered_df = self._cluster_and_map_peaks(window_factor)
        if clustered_df.empty:
            return CentroidedSpectrum(
                mz=[],
                intensity=[],
                resolution=[],
                signal_to_noise=[],
                peak_id=[],
                metadata={},
            )

        # Group by the cluster_id to aggregate peaks
        grouped = clustered_df.groupby("cluster_id")

        # Calculate total intensity, SNR, and weighted averages for m/z and resolution.
        # Every array below is taken as an owned copy: pandas hands out read-only views
        # into its own buffers, and the spectrum we build scales them in place and
        # outlives the frame they came from.
        total_intensity = grouped["intensity"].sum().to_numpy(copy=True)

        weighted_avg_mz = grouped[["mz", "intensity"]].apply(
            lambda g: np.average(g["mz"], weights=g["intensity"])
        )
        weighted_avg_res = grouped[["resolution", "intensity"]].apply(
            lambda g: np.average(g["resolution"], weights=g["intensity"])
        )

        def compute_total_snr(g):
            S = g["intensity"].values
            R = g["signal_to_noise"].values
            numerator = np.sum(S)
            denominator = np.sqrt(np.sum((S / R) ** 2))
            return numerator / denominator if denominator > 0 else 0.0

        total_snr = (
            grouped[["signal_to_noise", "intensity"]]
            .apply(compute_total_snr)
            .to_numpy(copy=True)
        )

        if average and len(self.spectra) > 0:
            total_intensity /= len(self.spectra)
            total_snr /= len(self.spectra)

        # Create lists of grouped peak IDs
        peak_ids = (
            grouped["peak_id"].apply(lambda ids: ids.tolist()).to_numpy(copy=True)
        )

        return CentroidedSpectrum(
            mz=weighted_avg_mz.to_numpy(copy=True),
            intensity=total_intensity,
            resolution=weighted_avg_res.to_numpy(copy=True),
            signal_to_noise=total_snr,
            peak_id=peak_ids,
            metadata={"average": average, "window_factor": window_factor},
        )

    def get_timeseries(self, window_factor=1) -> pd.DataFrame:
        """
        Returns a DataFrame with timestamps as columns and aggregated m/z as indices.
        """
        clustered_df = self._cluster_and_map_peaks(window_factor)
        if clustered_df.empty:
            return pd.DataFrame()

        # Use pandas pivot_table for an efficient transformation
        timeseries_df = clustered_df.pivot_table(
            index="cluster_id",
            columns="scan_idx",
            values="intensity",
            aggfunc="sum",
            fill_value=0.0,
        )

        # Calculate the representative m/z for each cluster_id to use as the final index
        weighted_avg_mz = clustered_df.groupby("cluster_id").apply(
            lambda g: np.average(g["mz"], weights=g["intensity"])
        )

        # Set the DataFrame index to the m/z values and columns to timestamps
        timeseries_df.index = weighted_avg_mz
        timeseries_df.columns = self.timestamps[timeseries_df.columns]
        timeseries_df.index.name = "m/z"

        return timeseries_df


class MassAligner:
    """
    Identifies and applies mass correction based on the algorithms described in
    "Mass spectra alignment using virtual lock-masses" by Brochu et al.

    This class implements the heap-based detection (Algorithm 4), overlap
    deletion (Algorithm 5), and single-pass correction (Algorithm 6).
    """

    def __init__(
        self,
        window_factor: float,
        min_peak_intensity: float = 0.0,
        min_fraction: float = 1.0,
    ):
        """
        :param window_factor: Factor to determine the window size for alignment points.
                              This is multiplied by the FWHM of the peaks.
        :param min_peak_intensity: Minimum intensity for a peak to be considered.
        :param min_fraction: Minimum fraction of spectra that must contain a peak for it
                             to be considered a point. Set to 1.0 for strict VLMs.
        """
        self.window_factor = window_factor
        self.min_peak_intensity = min_peak_intensity
        self.min_fraction = min_fraction
        self.points_mz: np.ndarray | None = None
        self.points_resolution: np.ndarray | None = None

    def fit(self, spectra: Spectra) -> np.ndarray:
        """
        Finds VLM or Alignment points in the spectra.
        This method implements Algorithm 4 from the paper.

        :param spectra: Spectra object to analyze.
        :return: Array of m/z values for the found points.
        """
        num_spectra = len(spectra)
        min_matches = int(np.ceil(num_spectra * self.min_fraction))

        # 1. Filter spectra by intensity and prepare for processing
        filtered_spectra_list = []
        for s in spectra:
            mask = s.intensity >= self.min_peak_intensity
            filtered_spectra_list.append(
                (s.mz[mask], s.intensity[mask], s.resolution[mask])
            )

        # 2. Initialize data structures from the paper
        heap = _Heap(filtered_spectra_list)
        active_seq = _ActiveSequence(num_spectra, self.window_factor, min_matches)

        found_point_mzs = []
        found_point_resolutions = []
        found_flag = False

        # 3. Main loop of Algorithm 4
        while not heap.empty():
            if active_seq.is_valid(heap):
                found_flag = True

            if not active_seq.insert(heap):
                if found_flag:
                    found_point_mzs.append(active_seq.calculate_average_mz())
                    found_point_resolutions.append(
                        active_seq.calculate_average_resolution()
                    )
                    found_flag = False
                active_seq.advance_lower_bound()

        # Final check after heap is exhausted
        while active_seq.peak_deque:
            if active_seq.is_valid(heap):
                found_point_mzs.append(active_seq.calculate_average_mz())
                found_point_resolutions.append(
                    active_seq.calculate_average_resolution()
                )
                break
            active_seq.advance_lower_bound()

        # 4. Remove overlaps as per Algorithm 5
        unique_mzs, unique_indices = np.unique(found_point_mzs, return_index=True)
        unique_resolutions = np.array(found_point_resolutions)[unique_indices]
        self.points_mz, self.points_resolution = self._delete_overlaps(
            unique_mzs, unique_resolutions, self.window_factor
        )
        return self.points_mz

    def _delete_overlaps(
        self, point_mzs: np.ndarray, point_resolutions: np.ndarray, window_factor: float
    ) -> np.ndarray:
        """Implements Algorithm 5: deleteOverlaps."""
        if point_mzs.size < 2:
            return point_mzs, point_resolutions

        sort_indices = np.argsort(point_mzs)
        sorted_mzs = point_mzs[sort_indices]
        sorted_res = point_resolutions[sort_indices]

        point_fwhms = sorted_mzs / sorted_res
        point_intervals = point_fwhms * self.window_factor

        is_overlapping = np.zeros(sorted_mzs.shape, dtype=bool)

        # Vectorized overlap check
        end_i = sorted_mzs[:-1] + point_intervals[:-1]
        start_next = sorted_mzs[1:] - point_intervals[1:]
        overlaps = end_i >= start_next

        is_overlapping[:-1][overlaps] = True
        is_overlapping[1:][overlaps] = True

        non_overlapping_original_indices = sort_indices[~is_overlapping]
        return (
            point_mzs[non_overlapping_original_indices],
            point_resolutions[non_overlapping_original_indices],
        )

    def transform(self, spectra: Spectra) -> Spectra:
        """
        Applies correction to spectra based on fitted points.
        This method implements Algorithm 6 from the paper.

        :param spectra: Spectra object to correct.
        :return: A new Spectra object with corrected m/z values.
        """
        if self.points_mz is None or len(self.points_mz) < 2:
            warnings.warn(
                "Correction requires at least 2 alignment points. Returning original spectra."
            )
            return spectra

        corrected_spectra_list = [self._apply_correction(s) for s in spectra]
        return Spectra(corrected_spectra_list, spectra.timestamps)

    def _apply_correction(self, spectrum: Spectrum) -> Spectrum:
        """Applies correction to a single spectrum."""
        mz = spectrum.mz
        if mz.size == 0 or self.points_mz is None or self.points_mz.size < 2:
            return spectrum

        # 1. Find insertion points for alignment points in the spectrum's m/z array
        insertion_indices = np.searchsorted(mz, self.points_mz)

        # Handle cases where insertion index is out of bounds
        insertion_indices = np.clip(insertion_indices, 1, len(mz) - 1)

        # 2. For each alignment point, the closest peak is either at index i or i-1
        dist1 = np.abs(mz[insertion_indices] - self.points_mz)
        dist2 = np.abs(mz[insertion_indices - 1] - self.points_mz)

        # 3. Choose the index corresponding to the smaller distance
        is_prev_closer = dist2 < dist1
        observed_indices = insertion_indices - is_prev_closer.astype(int)
        min_dists = np.where(is_prev_closer, dist2, dist1)

        # 4. Filter out bad matches where the closest peak is still too far away
        point_fwhms = self.points_mz / self.points_resolution
        max_allowed_dist = point_fwhms * self.window_factor

        valid_match_mask = min_dists <= max_allowed_dist
        observed_indices[~valid_match_mask] = -1

        # --- Perform linear interpolation with filters ---
        corrected_mz = np.copy(mz)
        for i in range(len(self.points_mz) - 1):
            idx_start = observed_indices[i]
            idx_end = observed_indices[i + 1]

            if idx_start == -1 or idx_end == -1:
                continue

            mz_obs_start = mz[idx_start]
            mz_obs_end = mz[idx_end]

            if mz_obs_end <= mz_obs_start:
                continue

            vlm_true_start = self.points_mz[i]
            vlm_true_end = self.points_mz[i + 1]

            slope = (vlm_true_end - vlm_true_start) / (mz_obs_end - mz_obs_start)

            intercept = vlm_true_start - slope * mz_obs_start

            mask = (np.arange(len(mz)) >= idx_start) & (np.arange(len(mz)) <= idx_end)
            corrected_mz[mask] = slope * mz[mask] + intercept

        return CentroidedSpectrum(
            mz=corrected_mz,
            intensity=spectrum.intensity,
            resolution=spectrum.resolution,
            signal_to_noise=spectrum.signal_to_noise,
            peak_id=spectrum.peak_id,
            metadata=spectrum.metadata,
        )


def calibrate_aligned_spectra(
    spectra: Spectra,
    target_mz: float,
    tol_ppm: float = 10.0,
) -> tuple[Spectra, list[float]]:
    """
    Calibrates spectra using a proportional (multiplicative) one-point correction.

    :param spectra: Collection of spectra to calibrate.
    :type spectra: Spectra
    :param target_mz: The m/z value to align peaks to.
    :type target_mz: float
    :param tol_ppm: Tolerance in ppm for matching m/z values, defaults to 10.0.
    :type tol_ppm: float, optional
    :return: A tuple containing:
        - A new Spectra object with calibrated m/z values.
        - A list of correction factors applied to each spectrum.
    :rtype: tuple[Spectra, list[float]]
    :raises ValueError: If no peaks are found within the specified tolerance.
    """
    delta = target_mz * tol_ppm * 1e-6
    calibrated_spectra = []
    correction_factors = []
    for spectrum in spectra:
        mask = np.abs(spectrum.mz - target_mz) <= delta
        if not np.any(mask):
            raise ValueError(
                f"No peaks found within {tol_ppm} ppm of target m/z {target_mz}."
            )

        observed_mz = spectrum.mz[mask]
        observed_intensity = spectrum.intensity[mask]

        max_intensity_index = np.argmax(observed_intensity)
        observed_lock_mass = observed_mz[max_intensity_index]

        correction_factors.append(target_mz / observed_lock_mass)

        calibrated_spectrum = CentroidedSpectrum(
            mz=spectrum.mz * correction_factors[-1],
            intensity=spectrum.intensity,
            resolution=spectrum.resolution,
            signal_to_noise=spectrum.signal_to_noise,
            peak_id=spectrum.peak_id,
            metadata=spectrum.metadata,
        )
        calibrated_spectra.append(calibrated_spectrum)

    return Spectra(calibrated_spectra, spectra.timestamps), correction_factors


class _Heap:
    def __init__(self, spectra_list: list[tuple[np.ndarray, np.ndarray, np.ndarray]]):
        self._heap = []
        self._spectra_list = spectra_list
        # For each spectrum, track the index of the next peak to be added
        self._next_peak_indices = [0] * len(self._spectra_list)
        self._init_heap()

    def _init_heap(self):
        """Initializes the heap with the first peak of each spectrum."""
        for spec_idx, spectrum in enumerate(self._spectra_list):
            if spectrum[0].size > 0:
                # Push (m/z, spectrum_index, resolution) to the heap
                mz = spectrum[0][0]
                resolution = spectrum[0][2]
                heapq.heappush(self._heap, (mz, spec_idx, resolution))
                self._next_peak_indices[spec_idx] = 1

    def top(self) -> tuple[float, int, float] | None:
        """Returns the (m/z, spec_idx, resolution) of the peak at the top of the heap."""
        return self._heap[0] if not self.empty() else None

    def pop_and_feed(self) -> tuple[float, int, float] | None:
        """Removes the top peak and adds the next peak from the same spectrum. Returns (m/z, spec_idx, resolution)."""
        if self.empty():
            return None

        # Pop the lowest m/z peak from the heap
        mz, spec_idx, resolution = heapq.heappop(self._heap)

        # Feed the next peak from the same spectrum, if available
        next_idx = self._next_peak_indices[spec_idx]
        spectrum_mzs = self._spectra_list[spec_idx][0]
        spectrum_resolutions = self._spectra_list[spec_idx][2]
        if next_idx < len(spectrum_mzs):
            heapq.heappush(
                self._heap,
                (spectrum_mzs[next_idx], spec_idx, spectrum_resolutions[next_idx]),
            )
            self._next_peak_indices[spec_idx] += 1

        return (mz, spec_idx, resolution)

    def empty(self) -> bool:
        return not self._heap


class _ActiveSequence:
    """Implements the 'active sequence' data structure A from the paper."""

    def __init__(self, num_spectra: int, window_factor: float, min_peaks: int):
        self.peak_deque = (
            deque()
        )  # Doubly linked list for peaks (mz, spec_idx, resolution)
        self.spectrum_flag = [
            False
        ] * num_spectra  # Boolean vector to track spectra in peak_deque
        self.last_removed_mz = -1.0  # m/z of the last removed peak
        self.average_mz = 0.0  # Average m/z of peaks in peak_deque
        self.average_resolution = 0.0  # Average resolution of peaks in peak_deque
        self.window_factor = window_factor  # Times of FWHM for the window
        self.m = num_spectra  # Total number of spectra
        self.min_peaks = min_peaks  # Minimum peaks for a valid sequence

    def calculate_average_mz(self) -> float:
        return self.average_mz

    def calculate_average_resolution(self) -> float:
        return self.average_resolution

    def is_valid(self, heap: "_Heap") -> bool:
        """Checks if peak_deque is a valid VLM/Alignment sequence (Algorithm 1 modified)."""
        if len(self.peak_deque) < self.min_peaks:
            return False

        # This is the key modification for Alignment Points vs. VLMs.
        # For strict VLMs, this would be `len(self.peak_deque) != self.m`.
        # By using min_peaks, we allow for fractional presence.
        if self.min_peaks == self.m and len(self.peak_deque) != self.m:
            return False

        average_FWHM = (
            self.average_mz / self.average_resolution
            if self.average_resolution > 0
            else 0.0
        )

        # Check if the next peak from the heap is isolated
        if (
            not heap.empty()
            and heap.top()[0] <= self.average_mz + average_FWHM * self.window_factor
        ):
            return False

        # Check if all peaks in peak_deque are within the window of the average
        if self.peak_deque[-1][0] > self.average_mz + average_FWHM * self.window_factor:
            return False
        if self.peak_deque[0][0] < self.average_mz - average_FWHM * self.window_factor:
            return False

        # Check if the last removed peak is outside the window
        if self.last_removed_mz >= self.average_mz - average_FWHM * self.window_factor:
            return False

        return True

    def advance_lower_bound(self):
        """Removes the first peak from peak_deque (Algorithm 2)."""
        if not self.peak_deque:
            return
        mz, spec_idx, resolution = self.peak_deque.popleft()
        self.spectrum_flag[spec_idx] = False
        self.last_removed_mz = mz

        # Recalculate the average
        if self.peak_deque:
            old_mz_sum = self.average_mz * (len(self.peak_deque) + 1)
            self.average_mz = (old_mz_sum - mz) / len(self.peak_deque)

            old_resolution_sum = self.average_resolution * (len(self.peak_deque) + 1)
            self.average_resolution = (old_resolution_sum - resolution) / len(
                self.peak_deque
            )
        else:
            self.average_mz = 0.0
            self.average_resolution = 0.0

    def insert(self, heap: "_Heap") -> bool:
        """Tries to insert the top peak from the heap into peak_deque (Algorithm 3)."""
        if heap.empty():
            return False
        top_peak_mz, top_peak_spec_idx, top_peak_resolution = heap.top()

        # If a peak from this spectrum is already in the sequence, cannot add.
        if self.spectrum_flag[top_peak_spec_idx]:
            return False

        # Calculate the new average
        new_average_mz = (self.average_mz * len(self.peak_deque) + top_peak_mz) / (
            len(self.peak_deque) + 1
        )
        new_average_resolution = (
            self.average_resolution * len(self.peak_deque) + top_peak_resolution
        ) / (len(self.peak_deque) + 1)
        new_average_FWHM = (
            new_average_mz / new_average_resolution
            if new_average_resolution > 0
            else 0.0
        )

        # Check if the window condition holds with the new peak
        front_mz = self.peak_deque[0][0] if self.peak_deque else top_peak_mz
        if (top_peak_mz <= new_average_mz + new_average_FWHM * self.window_factor) and (
            front_mz >= new_average_mz - new_average_FWHM * self.window_factor
        ):
            mz, spec_idx, resolution = heap.pop_and_feed()
            self.spectrum_flag[spec_idx] = True
            self.peak_deque.append((mz, spec_idx, resolution))
            self.average_mz = new_average_mz
            self.average_resolution = new_average_resolution
            return True

        return False
