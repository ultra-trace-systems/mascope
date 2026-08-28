"""
Functionalities related to the m/z fitting calibration processes.

General calibration workflow:
- Load calibration compounds from the selected target collection and fetch their
   isotopes for the requested ionization mechanisms and instrument resolution.
- Load detected peaks from the sample file and filter candidate peaks by polarity,
   signal-to-noise ratio, proximity to target m/z values, and instrument
   resolution to remove overlapping peaks.
- Load peak time series for the remaining candidates, reduce them to scan
   timestamps, and extract instrument-specific peak intensities.
- Match each target isotope to the most intense sample peak within the refine
   window and calculate match statistics.
- Keep only calibration candidates that satisfy abundance, intensity, m/z error,
   and match-score thresholds.
- Fit an instrument-specific calibration model:
   - TOF: multi-point fit using tof and m/z pairs.
   - Orbitrap: one-point scaling based on observed-to-target m/z ratios.
- Refine the calibration set by removing peaks with unacceptable residual errors;
   for small match sets, evaluate all subsets to find the most self-consistent fit.
- Build calibration statistics, summary metrics, and warnings when calibration is
   missing, underdetermined, or inaccurate.
- Apply the accepted calibration by updating stored calibration parameters and
   rewriting relevant m/z coordinates in the sample file.
"""

import asyncio
import math
import os
from abc import abstractmethod
from itertools import combinations

import numpy as np
import pandas as pd

import mascope_file.io as m_io
import mascope_file.name as m_name
import mascope_signal.compute as m_compute
from mascope_backend.api.controllers.target.associations.target_compound_in_target_collection_controller import (
    get_target_compound_in_target_collection,
)
from mascope_backend.api.controllers.target.isotopes.target_isotopes_controller import (
    get_target_isotopes,
)
from mascope_backend.api.lib.api_features import (
    api_controller,
)
from mascope_backend.api.models.calibration.calibration_pydantic_model import (
    CalibrationFitParams,
    MzCalibrationParams,
    OrbiCalibrationParams,
    TofCalibrationParams,
)
from mascope_backend.api.new.instrument_configs.lib import (
    read_instrument_functions,
)
from mascope_backend.runtime import runtime
from mascope_backend.socket.notifications import (
    UserNotification,
    send_progress_user_notification,
)
from mascope_match.compute.isotopes import (
    calculate_match_stats,
)
from mascope_match.params import (
    UnmatchedIsotopeParams,
)
from mascope_tofwerk.calibration import mz_calibrate, tof_to_mass


TOF_MINIMUM_CALIBRATION_POINTS = 3
ORBI_MINIMUM_CALIBRATION_POINTS = 1
LARGE_SAMPLE_SIZE_THRESHOLD = 5

#: Local-dominance guard for Orbitrap candidate peaks. FTMS centroiding of
#: short transients leaves weak sidelobe ("satellite") peaks around every
#: intense centroid; they can carry SNR far above the calibration threshold
#: while being orders of magnitude weaker than their parent. A candidate is
#: rejected when a peak at least ORBI_DOMINANCE_RATIO times more intense lies
#: within ORBI_DOMINANCE_WINDOW_PPM: either that stronger peak is the real
#: calibrant (and wins the max-intensity match itself), or the candidate is
#: its sidelobe - in both cases the candidate must not anchor the fit. Real
#: calibrants are unaffected because sidelobes are never within a factor
#: ORBI_DOMINANCE_RATIO of their parent's intensity.
ORBI_DOMINANCE_WINDOW_PPM = 100.0
ORBI_DOMINANCE_RATIO = 10.0

#: Warnings for a calibration that has nothing to calibrate against. Reported
#: the same way as "The sample file has no peaks.": no fit, no stats, nothing
#: written, and the controller turns the warning into an API warning.
EMPTY_CALIBRATION_COLLECTION_WARNING = (
    "Calibration collection is empty - nothing to calibrate against."
)
#: Both causes belong in the text: the isotopes are selected by the mode's
#: mechanisms *and* by the resolution the instrument dictates, so a collection
#: that only carries HIGH-resolution isotopes fails on a TOF sample for a
#: reason that has nothing to do with the ionization mode.
NO_CALIBRATION_ISOTOPES_WARNING = (
    "Calibration collection has no isotopes for this ionization mode at the "
    "instrument's resolution - nothing to calibrate against."
)


class BaseCalibrationHandler:
    #: Local-dominance guard (see ORBI_DOMINANCE_WINDOW_PPM). Disabled here
    #: (window 0); instrument handlers whose peak lists carry sidelobe
    #: artifacts override these.
    _dominance_window_ppm: float = 0.0
    _dominance_ratio: float = 0.0

    def __init__(
        self,
        filename: str,
        params: CalibrationFitParams | None = None,
        notification: UserNotification | None = None,
    ):
        self.filename = filename
        self.params = params
        self.notification = notification
        self.fit_result = None
        self.stats = None
        self.error = None
        self.warning = None

    def _calibration_lock_path(self) -> str:
        """Path naming the lock that guards a whole ``apply`` for this sample.

        Not a store - only the name a lock file is derived from. It sits beside
        the sample's stores and is deliberately distinct from any of them, so
        taking it does not collide with the per-array locks the writes inside
        ``_apply_sync`` take for themselves.
        """
        return os.path.join(
            m_name.parse_path_from_item_filename(self.filename), "mz_calibration"
        )

    def _guarded_apply(self, fit: dict):
        """Run ``_apply_sync`` under a lock covering the whole recalibration.

        ``_apply_sync`` reads the stored calibration, rewrites several m/z
        axes, and only then records the new one - an unguarded
        read-modify-write. It used to be serialised for free: the body ran on
        the event loop with no await in it, so no other coroutine in the worker
        could interleave. Offloading it to a thread gives that up, and gives it
        up more completely than a yield point would, because two callers get
        two pool threads and run at the same time. On the Orbitrap path the
        factor is cumulative, so two applies admitted past
        ``_is_calibration_already_applied`` scale the axis twice while the
        properties record one application.

        The per-array ``zarr_write_lock`` inside does not cover this: it is
        released between arrays and keyed per store. This one is keyed on the
        sample and held across the lot, and being the same inter-process lock
        the writes use, it also excludes a sibling worker or the file
        converter - which the event loop never did.
        """
        with m_io.zarr_write_lock(self._calibration_lock_path()):
            return self._apply_sync(fit)

    def _nothing_to_calibrate_against(self, warning: str) -> None:
        """Record a calibration that never got as far as matching.

        Leaves the handler in the same state as the no-peaks path: no fit and
        no stats, so nothing is applied and nothing is written, while
        ``calibration_mz_fit`` turns the warning into an API warning for the
        caller (dialog message for a manual run, failure marker for the
        automatic pipeline).
        """
        self.fit_result = None
        self.stats = None
        self.warning = warning

    async def _resolve_calibration_isotopes(self) -> pd.DataFrame | None:
        """Target isotopes of the configured calibration collection.

        Returns ``None`` - having recorded the matching warning - when there is
        nothing to calibrate against, either because the collection holds no
        compounds or because none of them has an isotope for this ionization
        mode and instrument resolution. Both cases must stop here: an empty
        compound list used to be dropped from the isotope query as falsy, which
        fitted the sample against *every* target isotope in the database,
        across all collections and workspaces; an empty isotope result then
        built a column-less DataFrame and crashed the request.

        :return: Isotopes to calibrate against, or None when there are none.
        :rtype: pd.DataFrame | None
        """
        target_compounds_result = await get_target_compound_in_target_collection(
            target_collection_id=self.params.calibration_collection_id,
        )
        target_compound_ids = [
            item["target_compound_id"] for item in target_compounds_result["data"]
        ]
        if not target_compound_ids:
            self._nothing_to_calibrate_against(EMPTY_CALIBRATION_COLLECTION_WARNING)
            return None

        instrument_type = m_name.get_instrument_type(self.filename)
        match instrument_type:
            case "tof":
                isotope_resolution = "LOW"
            case "orbi":
                isotope_resolution = "HIGH"

        target_isotopes_result = await get_target_isotopes(
            target_compound_ids=target_compound_ids,
            ionization_mechanism_ids=self.params.ionization_mechanism_ids,
            resolution=isotope_resolution,
        )
        target_isotopes_df = pd.DataFrame(target_isotopes_result["data"])
        if target_isotopes_df.empty:
            self._nothing_to_calibrate_against(NO_CALIBRATION_ISOTOPES_WARNING)
            return None
        return target_isotopes_df

    async def _match_calibration_compounds(
        self,
        target_isotopes_df: pd.DataFrame,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Match calibration compounds in the sample file.

        :param target_isotopes_df: Non-empty isotopes of the calibration
            collection, as resolved by :meth:`_resolve_calibration_isotopes`.
        :type target_isotopes_df: pd.DataFrame
        """
        peaks = await self._load_and_filter_peaks(
            target_mzs=target_isotopes_df.mz,
        )

        match_df = target_isotopes_df.copy().assign(
            sample_peak_id=np.nan,
            sample_peak_mz=np.nan,
            sample_peak_intensity=np.nan,
            sample_peak_intensity_relative=np.nan,
            match_abundance_error=np.nan,
            match_mz_error=np.nan,
            match_score=np.nan,
            sample_peak_tof=np.nan,
            matched_peak_idx=np.nan,
        )

        def _averaged_peaks_dict():
            averaged_peaks = peaks.mean(dim="time").compute()
            return {
                "mz": averaged_peaks.mz.values,
                "tof": averaged_peaks.tof.values,
                "intensity": averaged_peaks.values,
            }

        averaged_peaks_dict = await asyncio.to_thread(_averaged_peaks_dict)
        match_df = match_df.apply(
            self._match_max_in_range,
            args=(averaged_peaks_dict,),
            axis=1,
        ).reset_index(drop=True)

        matched_mask = ~match_df["sample_peak_mz"].isna()
        if matched_mask.any():
            match_df = match_df[matched_mask]
            match_df = calculate_match_stats(match_df)
        else:
            # No calibration peaks matched
            # Return empty DataFrame for both match_df and good_matches_df
            return match_df.iloc[0:0], match_df.iloc[0:0]

        # Ensure correct dtypes for match_df columns to avoid warnings
        match_df = match_df.astype(
            {
                "sample_peak_id": "string",
                "sample_peak_mz": "float64",
                "sample_peak_intensity": "float64",
                "sample_peak_intensity_relative": "float64",
                "match_abundance_error": "float64",
                "match_mz_error": "float64",
                "match_score": "float64",
                "sample_peak_tof": "float64",
            }
        )
        # Fill np.nan with serializable defaults for unmatched isotopes
        default_unmatched_params = UnmatchedIsotopeParams().model_dump()
        match_df = match_df.fillna(default_unmatched_params)
        # Matches contain duplicates for every ionization mechanism, we drop them
        match_df = (
            match_df.sort_values(by=["sample_peak_mz", "target_ion_id"])
            .drop_duplicates(subset="sample_peak_mz", keep="first")
            .reset_index(drop=True)
        )

        good_matches_df = match_df[
            (match_df.relative_abundance >= self.params.isotope_abundance_min)
            & (match_df.sample_peak_intensity >= self.params.peak_intensity_min)
            & (abs(match_df.match_mz_error) <= self.params.refine_window)
            & (match_df.match_score >= self.params.match_score_min)
        ]

        return match_df, good_matches_df

    async def _load_and_filter_peaks(
        self,
        target_mzs: pd.Series,
    ):
        """Load peak timeseries of the potential calibration peaks.

        Performs filtering based on:
        - Polarity: Only peaks matching the specified polarity are retained.
        - SNR threshold: Only peaks with signal-to-noise ratio above the specified
            threshold are retained.
        - m/z proximity: Only peaks within the refine window (in ppm) of any target
            m/z are retained.
        - Local dominance: peaks in the shadow of a much stronger nearby peak
            (sidelobe artifacts) are rejected.
        - Instrument resolution: peaks that are too close to each other based on
            the instrument resolution.

        :param target_mzs: Series of target m/z values to be matched against the sample peaks.
        :type target_mzs: pd.Series
        :return: DataArray containing detected peaks with their m/z, intensity, and time information.
        :rtype: xarray.DataArray
        """
        # Offloaded through the filters, not just the load. `load_peak_data`
        # returns a dask-backed dataset, so the store reads happen where its
        # variables are evaluated - `signal_to_noise.values` in the SNR filter
        # and `sum_peak_heights.values` in the dominance filter, plus that
        # filter's per-candidate searchsorted loop. Wrapping only the loader
        # would move the metadata into the thread and leave every chunk read
        # on the event loop.
        candidate_mzs = await asyncio.to_thread(
            self._sync_load_and_filter_peaks, np.asarray(target_mzs)
        )
        # Stays on the loop: it awaits the instrument config.
        candidate_mzs = await self._filter_overlapping_peaks(candidate_mzs)

        peak_timeseries = await self._load_peak_timeseries(candidate_mzs)
        peak_timeseries = await asyncio.to_thread(
            self._drop_empty_peak_timeseries, peak_timeseries
        )

        return self._extract_intensity(peak_timeseries)

    def _sync_load_and_filter_peaks(self, target_mzs: np.ndarray) -> np.ndarray:
        """Synchronous body of :meth:`_load_and_filter_peaks` up to the awaits.

        One unit so the lazy dataset is both opened and evaluated in the worker
        thread. See the call site for why the split matters.
        """
        peak_data = m_io.load_peak_data(self.filename)

        candidate_mzs = self._filter_mzs_by_polarity_and_snr(peak_data)
        candidate_mzs = self._filter_mzs_by_refine_window(candidate_mzs, target_mzs)
        return self._filter_dominated_peaks(candidate_mzs, peak_data)

    def _filter_mzs_by_polarity_and_snr(self, peak_data) -> np.ndarray:
        """Filter m/z values based on polarity and signal-to-noise ratio (SNR) thresholds."""
        all_mzs = peak_data.mz.values
        polarity_mask = peak_data.polarity == self.params.polarity
        snr_mask = peak_data.signal_to_noise.values >= self.params.snr_threshold
        return all_mzs[polarity_mask & snr_mask]

    def _filter_dominated_peaks(
        self,
        peak_mzs: np.ndarray,
        peak_data,
    ) -> np.ndarray:
        """Reject candidates that sit in the shadow of a much stronger peak.

        A candidate is dominated when a same-polarity peak at least
        ``_dominance_ratio`` times more intense lies within
        ``_dominance_window_ppm`` of it. Such candidates are sidelobe
        artifacts of the stronger peak (or minor peaks unresolvable from its
        sidelobes) and must not anchor the fit. The comparison runs against
        the full same-polarity peak list without the SNR gate, so a true
        calibrant outside the refine window still disqualifies its in-window
        sidelobes. No-op when the handler's dominance window is 0.

        :param peak_mzs: Candidate calibration peak m/z values.
        :type peak_mzs: np.ndarray
        :param peak_data: Full peak dataset with mz, polarity and
            sum_peak_heights.
        :return: Candidates without dominated peaks.
        :rtype: np.ndarray
        """
        if self._dominance_window_ppm <= 0 or peak_mzs.size == 0:
            return peak_mzs

        polarity_mask = (peak_data.polarity == self.params.polarity).values
        all_mzs = peak_data.mz.values[polarity_mask]
        all_heights = np.nan_to_num(
            peak_data.sum_peak_heights.values[polarity_mask], nan=0.0
        )

        order = np.argsort(all_mzs)
        sorted_mzs = all_mzs[order]
        sorted_heights = all_heights[order]

        keep_mask = np.ones(peak_mzs.size, dtype=bool)
        for i, mz in enumerate(peak_mzs):
            window = self._dominance_window_ppm * 1e-6 * mz
            lo = np.searchsorted(sorted_mzs, mz - window, side="left")
            hi = np.searchsorted(sorted_mzs, mz + window, side="right")
            neighborhood = sorted_heights[lo:hi]
            if neighborhood.size == 0:
                continue
            own_height = neighborhood[np.argmin(np.abs(sorted_mzs[lo:hi] - mz))]
            keep_mask[i] = neighborhood.max() < self._dominance_ratio * own_height
        return peak_mzs[keep_mask]

    def _filter_mzs_by_refine_window(
        self,
        peak_mzs: np.ndarray,
        target_mzs: np.ndarray,
    ) -> np.ndarray:
        """Filter m/z values to retain only those within the refine window of any target m/z."""
        mz_mask = np.any(
            np.abs(peak_mzs[:, None] - target_mzs[None, :])
            <= self.params.refine_window * 1e-6 * target_mzs[None, :],
            axis=1,
        )
        return peak_mzs[mz_mask]

    async def _filter_overlapping_peaks(self, peak_mzs: np.ndarray) -> np.ndarray:
        """Filter out peaks that are too close to each other based on the instrument resolution."""
        if peak_mzs.size <= 1:
            return peak_mzs
        _, resolution_function = await read_instrument_functions(self.filename)
        return self._remove_overlapping_mzs(peak_mzs, resolution_function)

    @staticmethod
    def _remove_overlapping_mzs(
        peak_mzs: np.ndarray,
        resolution_function,
    ) -> np.ndarray:
        """Remove peaks that are too close to each other based on the instrument resolution."""
        sorted_idx = np.argsort(peak_mzs)
        sorted_mzs = peak_mzs[sorted_idx]

        fwhm = sorted_mzs / resolution_function(sorted_mzs)
        left_edges = sorted_mzs - fwhm / 2
        right_edges = sorted_mzs + fwhm / 2

        overlap_with_next = np.logical_and(
            right_edges[:-1] >= left_edges[1:],
            left_edges[:-1] <= right_edges[1:],
        )
        keep_mask = np.ones(sorted_mzs.size, dtype=bool)
        keep_mask[:-1] &= ~overlap_with_next
        keep_mask[1:] &= ~overlap_with_next
        return sorted_mzs[keep_mask]

    async def _load_peak_timeseries(self, peak_mzs: np.ndarray):
        """Load peak timeseries for the given m/z values and filter to scan timestamps."""
        scan_timestamps = await asyncio.to_thread(
            m_compute.get_scan_timestamps,
            self.filename,
            polarity=self.params.polarity,
        )
        return (await m_compute.load_peak_timeseries(self.filename, peak_mzs)).sel(
            time=scan_timestamps, method="nearest"
        )

    def _drop_empty_peak_timeseries(self, peak_timeseries):
        """Drop peaks with empty timeseries (all None values)
        for orbi_zarr, tof_zarr sample file types.
        """
        sample_file_type = m_name.get_sample_file_type(self.filename)
        if sample_file_type in ["orbi_zarr", "tof_zarr"]:
            return peak_timeseries.dropna(dim="mz", how="all")
        return peak_timeseries

    def _extract_intensity(self, peak_timeseries: "xarray.Dataset") -> "Dataarray":  # type: ignore # noqa: F821
        """
        Extract peak intensities from the timeseries dataset based on the instrument type.

        :param peak_timeseries: Timeseries dataset of peaks.
        :type peak_timeseries: xarray.Dataset
        :return: DataArray of peak intensities (either heights or areas).
        :rtype: xarray.DataArray
        """
        instrument_type = m_name.get_instrument_type(self.filename)

        match instrument_type:
            case "orbi":
                return peak_timeseries.peak_heights
            case "tof":
                return peak_timeseries.peak_areas

    def _match_max_in_range(
        self,
        isotope_row: pd.Series,
        peaks: dict,
    ):
        """Match the isotope to the peak with the highest intensity within the m/z tolerance range."""
        target_mz = isotope_row["mz"]
        mz_tolerance = self.params.refine_window * 1e-6 * target_mz

        delta_mz = np.abs(peaks["mz"] - target_mz)
        # Increase the threshold by one float step to include peaks that are exactly on the boundary
        in_range_mask = delta_mz <= mz_tolerance + np.finfo(float).eps * target_mz
        mz_in_range = peaks["mz"][in_range_mask]
        tof_in_range = peaks["tof"][in_range_mask]
        intensity_in_range = peaks["intensity"][in_range_mask]

        if np.any(in_range_mask):
            max_peak_idx = np.argmax(intensity_in_range)
            isotope_row["sample_peak_mz"] = mz_in_range[max_peak_idx]
            isotope_row["sample_peak_tof"] = tof_in_range[max_peak_idx]
            isotope_row["sample_peak_intensity"] = intensity_in_range[max_peak_idx]
            isotope_row["matched_peak_idx"] = max_peak_idx

        return isotope_row

    @abstractmethod
    def _fit_matches(self, matches_df: pd.DataFrame) -> tuple[dict, dict]:
        pass

    @abstractmethod
    def _evaluate_fit(self, matches_df: pd.DataFrame, fit_result: dict) -> dict:
        pass

    @property
    @abstractmethod
    def _minimum_calibration_points(self) -> int:
        pass

    def _build_calibration_df(
        self,
        matches_df: pd.DataFrame,
        fit_stats: dict,
    ) -> pd.DataFrame:
        """Build a DataFrame containing calibration results and statistics for each matched peak."""
        return matches_df.copy().assign(
            calibration_mz=fit_stats["new_mz"],
            calibration_mz_error=fit_stats["post_dmz"],
            mz_error_diff=abs(fit_stats["post_dmz"]) - abs(fit_stats["pre_dmz"]),
        )

    def _mz_error_mask(self, calibration_df: pd.DataFrame) -> pd.Series:
        """Create a boolean mask to identify matches with acceptable calibration m/z error."""
        return (
            calibration_df["calibration_mz_error"].abs()
            <= self.params.mz_error_tolerance
        )

    def _candidate_subset_sizes(self, total_points: int) -> range:
        """Generate a range of subset sizes to consider for small sample calibration fitting."""
        min_points = self._minimum_calibration_points
        return range(total_points - 1, min_points - 1, -1)

    def _select_from_many_matches(self, matches_df: pd.DataFrame) -> pd.DataFrame:
        """Select matches with acceptable residuals after fitting all matches."""
        fit_result, _ = self._fit_matches(matches_df)
        evaluated_df = self._build_calibration_df(
            matches_df,
            self._evaluate_fit(matches_df, fit_result),
        )
        retained_df = evaluated_df[self._mz_error_mask(evaluated_df)].copy()
        if retained_df.empty:
            return matches_df.copy()
        return retained_df

    def _select_from_few_matches(self, matches_df: pd.DataFrame) -> pd.DataFrame:
        """Implements an Exact RANSAC (random sample consensus) algorithm by evaluating all
        possible subsets of matches and selecting the one with the best combination of number
        of matches and residual errors that meet the m/z error tolerance criteria.

        The number of combinations for <=5 matches is manageable for a brute force search,
        thus we do not implement a random sampling approach as in traditional RANSAC.
        """
        fit_result, _ = self._fit_matches(matches_df)
        unfiltered_calibration_df = self._build_calibration_df(
            matches_df,
            self._evaluate_fit(matches_df, fit_result),
        )

        all_peaks_within_tolerance = self._mz_error_mask(
            unfiltered_calibration_df
        ).all()
        if all_peaks_within_tolerance:
            return matches_df

        # Reset index to ensure correct indexing when evaluating subsets
        matches_df = matches_df.reset_index(drop=True)
        best_candidate = None
        all_indices = tuple(range(len(matches_df)))
        for subset_size in self._candidate_subset_sizes(len(matches_df)):
            for subset_indices in combinations(all_indices, subset_size):
                matches_subset = matches_df.iloc[list(subset_indices)].copy()
                subset_fit_result, _ = self._fit_matches(matches_subset)
                calibration_candidates_df = self._build_calibration_df(
                    matches_df,
                    self._evaluate_fit(matches_df, subset_fit_result),
                )
                retained_mask = calibration_candidates_df.index.isin(subset_indices)
                retained_errors = calibration_candidates_df.loc[
                    retained_mask, "calibration_mz_error"
                ].abs()
                excluded_errors = calibration_candidates_df.loc[
                    ~retained_mask, "calibration_mz_error"
                ].abs()

                retained_consistent = bool(
                    np.all(retained_errors <= self.params.mz_error_tolerance)
                )
                excluded_inconsistent = excluded_errors.empty or bool(
                    np.all(excluded_errors > self.params.mz_error_tolerance)
                )
                if not (retained_consistent and excluded_inconsistent):
                    continue

                # Score candidates based on:
                # - first by number of retained matches (higher is better)
                # - then by lower mean retained error
                # - then by higher mean excluded error
                excluded_mean_error = (
                    float(excluded_errors.mean()) if not excluded_errors.empty else 0.0
                )
                candidate_score = (
                    len(subset_indices),
                    -float(retained_errors.mean()),
                    excluded_mean_error,
                )
                if best_candidate is None or candidate_score > best_candidate[0]:
                    best_candidate = (
                        candidate_score,
                        calibration_candidates_df.loc[retained_mask].copy(),
                    )

            # If we found at least one valid candidate at this
            # subset_size, we can stop. Any smaller subset_size will result
            # in a lower 'best_score' due to the length component.
            if best_candidate is not None:
                return best_candidate[1]

        # No subset found that cleanly separates in-tolerance from out-of-tolerance points.
        # Signal that calibration should be skipped by returning an empty DataFrame
        # (upstream callers treat an empty set as "not enough calibration peaks").
        self.warning = (
            "No suitable subset of calibration peaks found; skipping calibration."
        )
        return matches_df.iloc[0:0]

    def _select_retained_matches(self, matches_df: pd.DataFrame) -> pd.DataFrame:
        """Splits the logic for selecting retained matches based on the number of matches"""
        if len(matches_df) <= LARGE_SAMPLE_SIZE_THRESHOLD:
            return self._select_from_few_matches(matches_df)
        return self._select_from_many_matches(matches_df)

    def _fit_retained_matches(
        self,
        matches_df: pd.DataFrame,
    ) -> tuple[dict | None, pd.DataFrame]:
        """Fit the calibration model to the retained matches and build the final calibration DataFrame."""
        selected_matches_df = self._select_retained_matches(matches_df)

        # No retained points means no fit attempt.
        if selected_matches_df.empty:
            if self.warning is None:
                self.warning = "No calibration peaks found after filtering."
            return None, selected_matches_df

        final_fit_result, _ = self._fit_matches(selected_matches_df)
        final_calibration_df = self._build_calibration_df(
            selected_matches_df,
            self._evaluate_fit(selected_matches_df, final_fit_result),
        )
        return final_fit_result, final_calibration_df

    def _get_summary_row(self, calibration_df):
        """Calculate summary statistics for the calibration results to be included as a summary row
        in the output."""
        match_mz_error = abs(calibration_df["match_mz_error"]).mean()
        calibration_mz_error = abs(calibration_df["calibration_mz_error"]).mean()
        mz_error_diff = abs(match_mz_error - calibration_mz_error)
        calibrant_to_tic = sum(calibration_df["calibrant_to_tic"])
        return {
            "match_mz_error": match_mz_error,
            "calibration_mz_error": calibration_mz_error,
            "mz_error_diff": mz_error_diff,
            "calibrant_to_tic": calibrant_to_tic,
        }

    @property
    def _has_peaks(self):
        """Check if the sample file contains any detected peaks that can be used for calibration."""
        peak_data = m_io.load_peak_data(self.filename)
        return not peak_data.mz.values.size == 0

    @api_controller()
    @abstractmethod
    async def fit(self):
        """Fit the m/z calibration model to the sample file and selected
        calibration compounds."""
        pass

    async def _send_progress(self, progress: float) -> None:
        if self.notification is not None:
            await send_progress_user_notification(self.notification, progress)

    @abstractmethod
    async def apply(self, fit: dict):
        """Apply the m/z calibration fit to the sample file by updating relevant m/z coordinates and calibration parameters."""
        pass

    def to_dict(self):
        return {
            "fit": self.fit_result,
            "stats": self.stats,
            "error": self.error,
            "warning": self.warning,
        }


class TofCalibrationHandler(BaseCalibrationHandler):
    @property
    def _minimum_calibration_points(self) -> int:
        return TOF_MINIMUM_CALIBRATION_POINTS

    def _fit_matches(self, matches_df: pd.DataFrame) -> tuple[dict, dict]:
        """Fit the m/z calibration model to the matched peaks using the tofwerk mz_calibrate function."""
        return mz_calibrate(
            matches_df["sample_peak_tof"],
            matches_df["sample_peak_mz"],
            matches_df["mz"],
        )

    def _evaluate_fit(self, matches_df: pd.DataFrame, fit_result: dict) -> dict:
        """Evaluate the fit by calculating the calibrated m/z values and errors for each matched peak."""
        fit_mode = fit_result["mode"]
        fit_parameters = fit_result["par"]
        target_mzs = matches_df["mz"].to_numpy()
        observed_mzs = matches_df["sample_peak_mz"].to_numpy()
        observed_tofs = matches_df["sample_peak_tof"].to_numpy()
        calibrated_mzs = tof_to_mass(observed_tofs, fit_mode, fit_parameters)

        return {
            "mz": target_mzs,
            "new_mz": calibrated_mzs,
            "pre_dmz": 1e6 * (observed_mzs - target_mzs) / target_mzs,
            "post_dmz": 1e6 * (calibrated_mzs - target_mzs) / target_mzs,
        }

    @api_controller()
    async def fit(self):
        """Fit the m/z calibration for a TOF instrument."""
        await self._send_progress(0.25)

        if not await asyncio.to_thread(lambda: self._has_peaks):
            self.fit_result = None
            self.stats = None
            self.warning = "The sample file has no peaks."
            return

        target_isotopes_df = await self._resolve_calibration_isotopes()
        if target_isotopes_df is None:
            return

        _, tic_per_scan = await asyncio.to_thread(
            m_compute.get_tic_per_scan, self.filename
        )
        tic = np.sum(tic_per_scan)

        await self._send_progress(0.35)

        match_isotope_df, good_matches_df = await self._match_calibration_compounds(
            target_isotopes_df
        )

        n_relevant_isotopes = len(
            match_isotope_df[
                (
                    match_isotope_df.relative_abundance
                    >= self.params.isotope_abundance_min
                )
            ]
        )
        calibrant_signal_intensity = good_matches_df["sample_peak_intensity"]
        calibrant_to_tic = calibrant_signal_intensity / tic

        await self._send_progress(0.75)

        if (
            n_relevant_isotopes >= self._minimum_calibration_points
            and len(good_matches_df) >= self._minimum_calibration_points
        ):
            calibration_matches_df = good_matches_df.copy().assign(
                calibrant_to_tic=calibrant_to_tic,
            )
            initial_fit_result, _ = self._fit_matches(calibration_matches_df)
            zero_coefficient = any(coef == 0 for coef in initial_fit_result["par"])
            if zero_coefficient:
                self.fit_result = None
                self.stats = good_matches_df.to_dict("records")
                self.error = "Calibration failed"
                return

            self.fit_result, selected_matches_df = self._fit_retained_matches(
                calibration_matches_df,
            )
            if (
                self.fit_result is None
                or len(selected_matches_df) < self._minimum_calibration_points
            ):
                self.fit_result = None
                self.stats = selected_matches_df.to_dict("records")
                self.warning = (
                    "Not enough calibration peaks after residual filtering. "
                    "At least 3 peaks are required for a reliable calibration fit."
                )
                return

            zero_coefficient = any(coef == 0 for coef in self.fit_result["par"])
            if zero_coefficient:
                self.fit_result = None
                self.stats = selected_matches_df.to_dict("records")
                self.error = "Calibration failed"
                return

            self.stats = selected_matches_df.to_dict("records")
            summary_row = self._get_summary_row(selected_matches_df)
            self.stats.append(summary_row)

            isotopes_from_single_ion = (
                len(selected_matches_df.target_ion_id.unique()) == 1
            )
            mz_err_too_high = (
                abs(summary_row["calibration_mz_error"])
                > self.params.mz_error_tolerance
            )
            calibration_inaccurate = mz_err_too_high or isotopes_from_single_ion
            if calibration_inaccurate:
                self.warning = "Calibration inaccurate"

            await self._send_progress(0.95)
        else:
            self.fit_result = None
            self.stats = good_matches_df.to_dict("records")
            self.warning = "Not enough calibration peaks"

    async def apply(self, fit: dict):
        """Applies the m/z calibration fit to the sample file.
        NOTE: fit is passed externally since fit() and apply() used in different controllers
        and the instance of TofCalibrationHandler is not passed between them."""
        # Offloaded as one unit rather than per zarr call: the body recalibrates
        # several arrays, and a reader must not see signal.zarr on the new m/z
        # axis while peak_timeseries.zarr is still on the old one. Running it in
        # a thread is what makes that possible without blocking the loop, but a
        # thread is not on its own exclusive - see _guarded_apply for the lock
        # that keeps the recalibration one unit.
        return await asyncio.to_thread(self._guarded_apply, fit)

    def _apply_sync(self, fit: dict):
        """Synchronous body of :meth:`apply`. See there for why it is one unit."""
        fit_mode = fit["mode"]
        fit_parameters = fit["par"]

        nbr_samples = m_compute.get_sum_signal(self.filename).size
        tof = np.arange(nbr_samples)
        new_mz_axis = np.asarray(
            tof_to_mass(tof, fit_mode, fit_parameters),
            dtype=np.float64,
        )
        new_mz_range = new_mz_axis[0], new_mz_axis[-1]

        runtime.logger.info(f"Calibrating file: {self.filename}")
        sample_file_type = m_name.get_sample_file_type(self.filename)
        m_io.update_props(self.filename, {"range": new_mz_range, "mz_calibration": fit})
        if sample_file_type == "tof_zarr":
            m_io.update_zarr_array_coord(self.filename, "signal", "mz", new_mz_axis)

        # Update m/z axis for all sum signals
        sample_data_path = m_name.parse_path_from_item_filename(self.filename)
        sample_file_vars = m_io.get_file_data_vars(sample_data_path)
        for sum_signal_var in sample_file_vars:
            if sum_signal_var.startswith("sum_signal"):
                m_io.update_zarr_array_coord(
                    self.filename, sum_signal_var, "mz", new_mz_axis
                )

        # Only the lookup may legitimately be absent - a sample file with no
        # peaks yet. The write stays outside, so a genuine I/O failure there
        # propagates instead of being reported as "no peak_timeseries" while
        # the file is already flagged as calibrated.
        try:
            peak_tofs = m_io.load_coord(self.filename, "peak_timeseries", "tof")
        except FileNotFoundError:
            runtime.logger.warning(
                f"peak_timeseries not found in {self.filename}, "
                "thus their m/z coordinates were not updated."
            )
        else:
            new_peak_mz = tof_to_mass(peak_tofs, fit_mode, fit_parameters)
            m_io.update_zarr_array_coord(
                self.filename, "peak_timeseries", "mz", new_peak_mz
            )
        return new_mz_axis


class OrbiCalibrationHandler(BaseCalibrationHandler):
    _dominance_window_ppm = ORBI_DOMINANCE_WINDOW_PPM
    _dominance_ratio = ORBI_DOMINANCE_RATIO

    @property
    def _minimum_calibration_points(self) -> int:
        return ORBI_MINIMUM_CALIBRATION_POINTS

    def _fit_matches(self, matches_df: pd.DataFrame) -> tuple[dict, dict]:
        """Fit the m/z calibration model to the matched peaks using a one-point calibration approach."""
        target_mzs = matches_df["mz"].to_numpy()
        observed_mzs = matches_df["sample_peak_mz"].to_numpy()
        old_factor_scaling = np.median(target_mzs / observed_mzs)
        old_factor = self._get_old_factor()
        calibration_factor = old_factor * old_factor_scaling
        fit_result = {
            "mode": "one-point",
            "par": {
                "old_factor": old_factor,
                "old_factor_scaling": old_factor_scaling,
                "calibration_factor": calibration_factor,
            },
        }
        fit_stats = self._evaluate_fit(matches_df, fit_result)
        return fit_result, fit_stats

    def _evaluate_fit(self, matches_df: pd.DataFrame, fit_result: dict) -> dict:
        """Evaluate the fit by calculating the calibrated m/z values and errors
        for each matched peak based on the one-point calibration factor.
        """
        target_mzs = matches_df["mz"].to_numpy()
        observed_mzs = matches_df["sample_peak_mz"].to_numpy()
        old_factor_scaling = fit_result["par"]["old_factor_scaling"]
        calibrated_mzs = observed_mzs * old_factor_scaling

        return {
            "mz": target_mzs,
            "new_mz": calibrated_mzs,
            "pre_dmz": 1e6 * (observed_mzs - target_mzs) / target_mzs,
            "post_dmz": 1e6 * (calibrated_mzs - target_mzs) / target_mzs,
        }

    @api_controller()
    async def fit(self):
        """Fit the m/z calibration for an Orbitrap instrument."""
        await self._send_progress(0.25)

        if not await asyncio.to_thread(lambda: self._has_peaks):
            self.fit_result = None
            self.stats = None
            self.warning = "The sample file has no peaks."
            return

        target_isotopes_df = await self._resolve_calibration_isotopes()
        if target_isotopes_df is None:
            return

        match_isotope_df, good_matches_df = await self._match_calibration_compounds(
            target_isotopes_df
        )

        await self._send_progress(0.75)

        if good_matches_df.empty:
            self.warning = "No calibration peaks found"
            return

        _, tic_per_scan = await asyncio.to_thread(
            m_compute.get_tic_per_scan, self.filename
        )
        tic = np.sum(tic_per_scan)
        calibrant_signal_intensity = good_matches_df["sample_peak_intensity"]
        calibrant_to_tic = calibrant_signal_intensity / tic

        calibration_matches_df = good_matches_df.copy().assign(
            calibrant_to_tic=calibrant_to_tic,
        )
        self.fit_result, calibration_df = self._fit_retained_matches(
            calibration_matches_df,
        )

        if self.fit_result is None or calibration_df.empty:
            self.fit_result = None
            self.stats = calibration_df.to_dict("records")
            if self.warning is None:
                self.warning = "No calibration peaks found after filtering."
            return

        calibration_inaccurate = np.all(
            np.abs(calibration_df["calibration_mz_error"])
            > self.params.mz_error_tolerance
        )
        if calibration_inaccurate:
            self.warning = "Calibration inaccurate"

        self.stats = calibration_df.to_dict("records")
        summary_row = self._get_summary_row(calibration_df)
        self.stats.append(summary_row)

        await self._send_progress(0.95)

    async def apply(self, fit: dict):
        """Applies the m/z calibration fit to the sample file.
        M/z scales for existing signals and peaks are scaled based on a new calibration.
        A new calibration factor is stored for new sum signals and
        signals to be generated later.
        """
        # Offloaded as one unit, for the same reason as the TOF handler - and
        # the lock matters more here: this calibration is cumulative, so a
        # second apply admitted past the _is_calibration_already_applied guard
        # below would double-apply old_factor_scaling.
        return await asyncio.to_thread(self._guarded_apply, fit)

    def _apply_sync(self, fit: dict):
        """Synchronous body of :meth:`apply`. See there for why it is one unit."""
        fit_parameters = fit["par"]
        old_factor_scaling = fit_parameters["old_factor_scaling"]
        if self._is_calibration_already_applied(fit):
            runtime.logger.info("Same calibration already applied; skipping.")
            return m_io.load_coord(self.filename, "sum_signal", "mz")

        runtime.logger.info(f"Calibrating file: {self.filename}")

        # Update m/z axis for all existing sum signals
        sample_data_path = m_name.parse_path_from_item_filename(self.filename)
        sample_file_vars = m_io.get_file_data_vars(sample_data_path)
        for var in sample_file_vars:
            if var.startswith("sum_signal"):
                new_mz_axis = (
                    m_io.load_coord(self.filename, var, "mz") * old_factor_scaling
                )
                m_io.update_zarr_array_coord(self.filename, var, "mz", new_mz_axis)

        # Update m/z axis for signal and peak arrays if they exist
        sample_file_type = m_name.get_sample_file_type(self.filename)
        if sample_file_type == "orbi_zarr":
            new_signal_mz = (
                m_io.load_array(self.filename, "signal").mz.values * old_factor_scaling
            )
            m_io.update_zarr_array_coord(self.filename, "signal", "mz", new_signal_mz)
        # Only the lookup may legitimately be absent; see the Tof handler.
        try:
            peak_mz = m_io.load_coord(self.filename, "peak_timeseries", "mz")
        except FileNotFoundError:
            runtime.logger.warning(
                f"Peak_areas/heights not found in {self.filename}, "
                "thus their m/z coordinates were not updated."
            )
        else:
            m_io.update_zarr_array_coord(
                self.filename, "peak_timeseries", "mz", peak_mz * old_factor_scaling
            )

        # Remove excessive items
        fit["par"].pop("old_factor", None)
        fit["par"].pop("old_factor_scaling", None)
        # Update sample file properties
        full_sum_signal_mz = m_io.load_coord(self.filename, "sum_signal", "mz")
        new_mz_range = full_sum_signal_mz[0], full_sum_signal_mz[-1]
        m_io.update_props(self.filename, {"range": new_mz_range, "mz_calibration": fit})

        return full_sum_signal_mz

    def _get_old_factor(self) -> float:
        """Retrieve the old calibration factor from the file properties if exists."""
        props = m_io.read_props(self.filename)
        old_mz_calibration = props["mz_calibration"]
        if old_mz_calibration is None:
            old_factor = 1.0
        else:
            old_factor = old_mz_calibration["par"]["calibration_factor"]
        return old_factor

    def _is_calibration_already_applied(self, fit: dict) -> bool:
        """Check if the calibration has already been applied."""
        props = m_io.read_props(self.filename)
        existing_calibration = props["mz_calibration"]
        if (
            existing_calibration
            and existing_calibration["par"]["calibration_factor"]
            == fit["par"]["calibration_factor"]
        ):
            return True
        return False


def get_calibration_handler(
    filename: str,
    calibration_params: CalibrationFitParams | None,
    notification: UserNotification | None,
) -> BaseCalibrationHandler:
    instrument_type = m_name.get_instrument_type(filename)
    match instrument_type:
        case "tof":
            return TofCalibrationHandler(filename, calibration_params, notification)
        case "orbi":
            return OrbiCalibrationHandler(filename, calibration_params, notification)
        case _:
            raise ValueError(f"Unsupported instrument type: {instrument_type}")


def calibration_params_factory(filename: str, **kwargs) -> MzCalibrationParams:
    instrument_type = m_name.get_instrument_type(filename)
    match instrument_type:
        case "tof":
            return TofCalibrationParams(**kwargs)
        case "orbi":
            return OrbiCalibrationParams(**kwargs)
        case _:
            raise ValueError(f"Unknown instrument type: {instrument_type}")


def _finite_or_none(value) -> float | None:
    """Coerce to a JSON-safe float: None for non-numeric or non-finite values."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def fit_quality(
    stats: list[dict] | None, params: MzCalibrationParams | None
) -> dict | None:
    """
    Compact fit-quality summary derived from a handler's per-point stats.

    Stored inside the persisted ``sample_file.mz_calibration`` record so the
    quality of an applied fit (how many calibration points anchored it, and the
    mean absolute m/z error before and after, in ppm) stays inspectable after
    the fact without refitting. A sidelobe-anchored or single-point fit is
    visible here even though it applied without warnings.

    The handlers append an aggregate summary row (no ``mz`` key) after the
    per-point rows; stats lists without one (warning paths) yield point counts
    only. Values are coerced to JSON-safe floats: the record is stored in a
    JSON column, so numpy scalars and NaN must not leak through.

    :param stats: Handler ``stats`` list (per-point rows + summary row).
    :param params: Resolved calibration params the fit ran with.
    :return: Quality dict, or None when there are no stats at all.
    """
    if not stats:
        return None
    summary = stats[-1] if "mz" not in stats[-1] else None
    point_rows = stats[:-1] if summary is not None else stats
    return {
        "n_points": len(point_rows),
        "pre_fit_mz_error_ppm": (
            _finite_or_none(summary.get("match_mz_error")) if summary else None
        ),
        "post_fit_mz_error_ppm": (
            _finite_or_none(summary.get("calibration_mz_error")) if summary else None
        ),
        "calibrant_to_tic": (
            _finite_or_none(summary.get("calibrant_to_tic")) if summary else None
        ),
        "mz_error_tolerance": (
            _finite_or_none(params.mz_error_tolerance) if params else None
        ),
        "refine_window": _finite_or_none(params.refine_window) if params else None,
    }
