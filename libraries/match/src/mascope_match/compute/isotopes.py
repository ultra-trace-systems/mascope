from bisect import bisect_left, insort
from typing import Literal

import numpy as np
import pandas as pd

import mascope_file.io as m_io
import mascope_signal.compute as m_compute
from mascope_file.name import get_instrument_type, get_sample_file_type
from mascope_match.id import generate_id
from mascope_match.params import BaseMatchParams, unmatched_isotope_params
from mascope_match.runtime import runtime


MATCH_WINDOW_AMU = 0.5  # Da


def _within_window_mask(
    values: np.ndarray, targets: np.ndarray, window: float
) -> np.ndarray:
    """Boolean mask of `values` whose nearest `target` is within `window`.

    Uses binary search on a sorted copy of `targets` (O((n+m) log m) time,
    O(n) memory).
    """
    values = np.asarray(values)
    targets = np.asarray(targets)
    if values.size == 0 or targets.size == 0:
        return np.zeros(values.shape, dtype=bool)

    targets_sorted = np.sort(targets)
    # Insertion positions of each value among the sorted targets; the nearest
    # target is one of the two neighbours straddling that position.
    pos = np.searchsorted(targets_sorted, values)
    left = np.clip(pos - 1, 0, targets_sorted.size - 1)
    right = np.clip(pos, 0, targets_sorted.size - 1)
    # fmin (not minimum) so a NaN target - which np.sort pushes to the end, and
    # which would otherwise poison the distance of every value that lands next
    # to it - is ignored rather than propagated.
    nearest_dist = np.fmin(
        np.abs(values - targets_sorted[left]),
        np.abs(values - targets_sorted[right]),
    )
    return nearest_dist <= window


async def compute_match_isotopes(
    filename: str,
    target_isotopes_df: pd.DataFrame,
    match_params: BaseMatchParams | None = None,
    polarity: Literal["+", "-"] | None = None,
    existing_reference_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """
    Compute matches between target isotopes and sample file peaks.

    This function identifies the best matching peaks within the sample spectrum for each target isotope
    based on their m/z values and computes match statistics. For isotopes without matching peaks,
    default values are assigned with a match score of 0.

    Steps:
    - Apply filtering only if match_params provided (backwards compatibility)
    - Load sample peaks
    - Initialize match DataFrame with placeholders for all target isotopes
    - Extract peak data and perform matching
    - Calculate match stats and assign defaults for unmatched isotopes
    - Return a DataFrame containing match details for all target isotopes

    :param filename: Path to the sample file to be analyzed for matches.
    :type filename: str
    :param target_isotopes_df: DataFrame containing target isotopes with their m/z values and other properties.
    :type target_isotopes_df: pd.DataFrame
    :param match_params: Match parameters containing settings for the matching process, default to None
    :type match_params: BaseMatchParams | None
    :param polarity: Polarity of the sample, either "+" or "-".
    :type polarity: Literal["+", "-"], optional
    :param existing_reference_df: DataFrame containing existing main isotope reference data
        (target_ion_id, sample_peak_intensity, relative_abundance) for proper abundance error
        calculation when computing additional isotopes for ions that already have matches.
    :type existing_reference_df: pd.DataFrame | None, optional
    :return: DataFrame with match details for all target isotopes, including those without matches
    :rtype: pd.DataFrame
    :raises ValueError: If an error occurs during the matching process.

    Notes:
    - Matching is done at the isotope level. Ion, compound and collection level matches are
      aggregated in a separate process.
    - Isotopes without matching peaks are assigned a match score of 0 and placeholder values
      for the required database fields.
    - Supports both database-pre-filtered isotopes (when match_params=None)
      and legacy filtering (when match_params provided for backwards compatibility).
      TODO remove match_params and target isotopes filtering form here? keep only actual computing
    """
    runtime.logger.debug("Start matching...")
    try:
        # --- Apply filtering only if match_params provided (backwards compatibility) ---
        instrument_type = get_instrument_type(filename)
        if match_params is not None:
            # Filter isotopes below threshold and with incorrect resolution
            resolution_type = (  # noqa: F841
                "LOW" if instrument_type == "tof" else "HIGH"
            )
            query = "resolution == @resolution_type"
            target_isotopes_df = target_isotopes_df.query(query).reset_index(drop=True)
        else:
            runtime.logger.debug("Using database-pre-filtered target isotopes")

        if target_isotopes_df.empty:
            runtime.logger.debug("No target isotopes to process")
            return pd.DataFrame()

        # --- Load sample peak timeseries ---
        runtime.logger.debug("Load and parse sample peaks")
        peak_timeseries = await load_peaks(
            filename=filename,
            target_mzs=target_isotopes_df.mz,
            polarity=polarity,
        )
        # Get positive-intensity, averaged peaks
        parsed_peaks = _parse_and_filter_peaks(peak_timeseries)

        runtime.logger.debug("Perform isotope matching")

        # --- Initialize match DataFrame with placeholders for all target isotopes ---
        match_isotope_df = target_isotopes_df.copy().assign(
            match_isotope_id=[
                generate_id(length=32) for _ in range(len(target_isotopes_df))
            ],
            sample_peak_id=np.nan,
            sample_peak_mz=np.nan,
            sample_peak_intensity=np.nan,
            sample_peak_intensity_relative=np.nan,
            match_abundance_error=np.nan,
            match_mz_error=np.nan,
            match_score=unmatched_isotope_params.match_score,
            sample_peak_tof=np.nan,
            signal_to_noise=np.nan,  # carried for the v2 (SNR-aware) match score
            is_satellite=False,  # v2 treats satellite-matched isotopologues as absent
        )

        # Ensure sample_peak_id dtype is an object
        match_isotope_df["sample_peak_id"] = match_isotope_df["sample_peak_id"].astype(
            "object"
        )

        # --- Extract peak data and perform matching ---
        match_isotope_df = _match_assign(match_isotope_df, parsed_peaks)

        # --- Calculate match stats and assign defaults for unmatched isotopes ---
        # Create a mask for matched isotopes (those with actual peak data)
        matched_mask = ~match_isotope_df["sample_peak_mz"].isna()

        # Calculate match stats for isotopes with actual matches
        if matched_mask.any():
            runtime.logger.debug("Calculate match statistics for matched isotopes")
            match_isotope_df = calculate_match_stats(
                match_isotope_df, existing_reference_df
            )

        # Set default values for unmatched isotopes
        unmatched_mask = ~matched_mask
        if unmatched_mask.any():
            runtime.logger.debug("Assign default values to unmatched isotopes")
            match_isotope_df = assign_defaults_to_unmatched(
                match_isotope_df, unmatched_mask
            )

        # Drop helper column
        match_isotope_df.drop(columns=["matched_peak_idx"], inplace=True)

        # --- Return a DataFrame containing match details for all target isotopes ---
        return match_isotope_df
    except Exception as e:
        # No log here: the raised ValueError is logged by the caller (per
        # sample, with a per-batch summary) - a pre-log would double every
        # failure in batch runs
        error_message = f"Computing matches failed: {e}"
        raise ValueError(error_message) from e


async def load_peaks(
    filename: str,
    target_mzs: pd.Series,
    polarity: Literal["+", "-"] | None = None,
):
    """Loads timeseries of required polarity from the sample file.
    The dataset contains mzs of target_mzs +- MATCH_WINDOW_AMU.
    Loads peak_heights for Orbitrap files and peak_areas for TOF files.

    :param filename: Path to the sample file to be analyzed for matches.
    :type filename: str
    :param target_mzs: Series of target m/z values to be matched against the sample peaks.
    :type target_mzs: pd.Series
    :param polarity: Polarity of the sample, either "+", "-", or "+-". Defaults to None.
    :type polarity: Literal["+", "-"], optional
    :return: DataArray containing detected peaks with their m/z, intensity, and time information.
    :rtype: xarray.DataArray
    """
    instrument_type = get_instrument_type(filename)
    target_mzs = np.asarray(target_mzs)
    peak_data = m_io.load_peak_data(filename)

    # Compute all peak timeseries within MATCH_WINDOW_AMU of target m/z values
    # to not compute them later in Match tab visualization.
    # A peak qualifies if its nearest target is within the window. Found via
    # binary search on sorted targets - O((n+m) log m) time and O(n) memory.
    all_mzs = peak_data.mz.values
    mz_to_compute = all_mzs[_within_window_mask(all_mzs, target_mzs, MATCH_WINDOW_AMU)]

    peak_timeseries = await m_compute.load_peak_timeseries(filename, mz_to_compute)

    match instrument_type:
        case "orbi":
            peaks = peak_timeseries.peak_heights
        case "tof":
            peaks = peak_timeseries.peak_areas

    sample_file_type = get_sample_file_type(filename)
    if sample_file_type in ["orbi_zarr", "tof_zarr"]:
        peaks = peaks.dropna(dim="mz", how="all")

    if polarity:
        # Filter peaks based on polarity
        time_scan = m_compute.get_scan_timestamps(filename, polarity=polarity)
        peaks = peaks.sel(time=time_scan, method="nearest")

    # Carry signal_to_noise (per m/z) as a coord on the peaks array so the SNR-aware
    # v2 match score can use it. Defensive: any issue degrades to no-SNR (v2 proxy),
    # never affects matching or the v1 score.
    try:
        for _var in ("signal_to_noise", "is_satellite"):
            if _var in peak_timeseries:
                vals = peak_timeseries[_var].sel(mz=peaks["mz"]).values
                peaks = peaks.assign_coords({_var: ("mz", vals)})
    except Exception:  # noqa: BLE001 — best-effort; degrades to no-SNR / v2 proxy
        pass

    return peaks


def _parse_and_filter_peaks(peaks: "xarray.DataArray") -> dict:  # type: ignore # noqa: F821
    """
    Parse and filter peaks from the detected peaks DataArray.

    :param peaks: Detected peaks DataArray containing m/z, intensity, and time information.
    :type peaks: xarray.DataArray
    :return: Dictionary containing parsed peak intensities, m/z values, and TOF values.
    :rtype: dict
    """
    peak_intensities = peaks.mean(dim="time").values
    non_zero_mask = peak_intensities > 0

    # signal_to_noise / is_satellite (per m/z) are carried as coords by load_peaks when
    # available; pass through for the v2 score (None when absent -> v2 proxy / no-op).
    snr = peaks["signal_to_noise"].values if "signal_to_noise" in peaks.coords else None
    sat = peaks["is_satellite"].values if "is_satellite" in peaks.coords else None

    return {
        "peak_intensities": peak_intensities[non_zero_mask],
        "peak_mzs": peaks.mz.values[non_zero_mask],
        "peak_ids": peaks.peak_id.values[non_zero_mask],
        "peak_tofs": peaks.tof.values[non_zero_mask],
        "signal_to_noise": snr[non_zero_mask] if snr is not None else None,
        "is_satellite": sat[non_zero_mask] if sat is not None else None,
        "non_zero_mask": non_zero_mask,
    }


def _match_assign(match_isotope_df: pd.DataFrame, parsed_peaks: dict) -> pd.DataFrame:
    """Match target isotopes with the closest peak in the sample spectrum.

    Rules:
    - Sample peaks must be unique within each ion (no sharing between isotopes of the same ion).
    - Different ions may share the same sample peaks.
    - When two isotopes of the same ion compete for the same peak, the higher relative abundance wins.
    - Within each ion, the ordering of assigned peak m/z must follow the ordering of target isotope m/z.
    - If no suitable peak exists within the window, the isotope stays unmatched.

    :param match_isotope_df: DataFrame containing target isotope properties.
    :type match_isotope_df: pd.DataFrame
    :param parsed_peaks: Parsed peak data containing intensities, m/z values, and TOF values.
    :type parsed_peaks: dict
    :return: DataFrame with matched peak information
    :rtype: pd.DataFrame
    """
    # --- Extract arrays for easier access ---
    peak_mzs = np.asarray(parsed_peaks["peak_mzs"])
    peak_ids = np.asarray(parsed_peaks["peak_ids"])
    peak_tofs = np.asarray(parsed_peaks["peak_tofs"])
    peak_intensities = np.asarray(parsed_peaks["peak_intensities"])

    target_mzs = match_isotope_df["mz"].to_numpy()
    target_rel_abundances = match_isotope_df["relative_abundance"].to_numpy()
    ion_ids = match_isotope_df["target_ion_id"].to_numpy()
    n_targets = len(target_mzs)

    # --- Get sorted candidate peak indices per target isotope that are within MATCH_WINDOW_AMU ---
    # Each target's candidates are the peaks whose m/z falls in
    # [target - window, target + window]. Locating that range with binary search
    # on the sorted peak m/z array avoids the dense (n_targets x n_peaks)
    # difference matrix; only per-target candidate slices are materialized.
    peak_order = np.argsort(peak_mzs, kind="stable")
    peak_mzs_sorted = peak_mzs[peak_order]
    candidate_lists: list[list[int]] = []
    for i in range(n_targets):
        target_mz = target_mzs[i]
        lo = np.searchsorted(peak_mzs_sorted, target_mz - MATCH_WINDOW_AMU, side="left")
        hi = np.searchsorted(
            peak_mzs_sorted, target_mz + MATCH_WINDOW_AMU, side="right"
        )
        candidates = peak_order[lo:hi]
        # Order candidates by absolute m/z difference, breaking ties by m/z.
        cand_diffs = np.abs(peak_mzs[candidates] - target_mz)
        sort_idx = np.lexsort((peak_mzs[candidates], cand_diffs))
        candidate_lists.append(candidates[sort_idx].tolist())

    # --- Assign peaks to isotopes ---
    # Sort isotopes by decreasing relative abundance and m/z to prioritize matching
    iso_order = np.lexsort((target_mzs, -target_rel_abundances))
    matched_peak_indices = np.full(n_targets, -1, dtype=int)

    # Track per-ion assignments to enforce m/z ordering and uniqueness within the ion
    ion_assignments: dict[str, list[tuple[float, float, int]]] = {
        ion_id: [] for ion_id in np.unique(ion_ids)
    }
    # Track used peaks per ion
    ion_used_peaks: dict[str, set[int]] = {
        ion_id: set() for ion_id in np.unique(ion_ids)
    }

    for iso_idx in iso_order:
        target_mz = target_mzs[iso_idx]
        ion_id = ion_ids[iso_idx]
        ion_list = ion_assignments[ion_id]

        # Precompute per-ion boundaries for m/z ordering
        ion_target_mzs = [t for t, _, _ in ion_list]
        insert_pos = bisect_left(ion_target_mzs, target_mz)
        lower_peak_mz = ion_list[insert_pos - 1][1] if insert_pos > 0 else None
        upper_peak_mz = ion_list[insert_pos][1] if insert_pos < len(ion_list) else None

        candidates = candidate_lists[iso_idx]
        chosen_peak_idx = None
        for peak_idx in candidates:
            if peak_idx in ion_used_peaks[ion_id]:
                # Peak already used within this ion, skip
                continue

            peak_mz = peak_mzs[peak_idx]
            if (lower_peak_mz is not None and peak_mz <= lower_peak_mz) or (
                upper_peak_mz is not None and peak_mz >= upper_peak_mz
            ):
                # Violates m/z ordering within the ion, skip
                continue

            chosen_peak_idx = peak_idx
            break

        if chosen_peak_idx is None:
            # No suitable peak found for this isotope, treat as unmatched
            continue

        # Assign peak and register usage within this ion
        ion_used_peaks[ion_id].add(chosen_peak_idx)
        matched_peak_indices[iso_idx] = chosen_peak_idx
        insort(ion_list, (target_mz, peak_mzs[chosen_peak_idx], chosen_peak_idx))

    # --- Assign matched peak data to match_isotope_df ---
    matched_mask = matched_peak_indices >= 0
    matched_indices = matched_peak_indices[matched_mask]
    assign_cols = {
        "sample_peak_id": peak_ids[matched_indices],
        "sample_peak_mz": peak_mzs[matched_indices],
        "sample_peak_tof": peak_tofs[matched_indices],
        "sample_peak_intensity": peak_intensities[matched_indices],
    }
    # Attach the matched peaks' signal_to_noise / is_satellite when carried (for v2).
    peak_snr = parsed_peaks.get("signal_to_noise")
    if peak_snr is not None:
        assign_cols["signal_to_noise"] = np.asarray(peak_snr)[matched_indices]
    peak_sat = parsed_peaks.get("is_satellite")
    if peak_sat is not None:
        assign_cols["is_satellite"] = np.asarray(peak_sat)[matched_indices]
    match_isotope_df.loc[matched_mask, list(assign_cols)] = pd.DataFrame(
        assign_cols, index=match_isotope_df.index[matched_mask]
    )

    # --- Store matched peak indices for later calculations ---
    matched_peak_idx_series = pd.Series(
        matched_peak_indices, index=match_isotope_df.index
    )
    matched_peak_idx_series.replace(-1, pd.NA, inplace=True)
    match_isotope_df["matched_peak_idx"] = matched_peak_idx_series.astype("Int64")

    return match_isotope_df


def calculate_match_stats(
    match_isotope_df: pd.DataFrame,
    existing_reference_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Calculate match statistics for isotopes.

    :param match_isotope_df: DataFrame containing matched isotopes with their properties.
    :type match_isotope_df: pd.DataFrame
    :param existing_reference_df: Optional DataFrame containing existing main isotope reference
        data (target_ion_id, sample_peak_intensity, relative_abundance) from previously computed
        matches. When provided, these references take priority for abundance error calculation.
    :type existing_reference_df: pd.DataFrame | None, optional
    :return: DataFrame with match statistics for each isotope, including relative peak intensities,
              abundance matching errors, isotope similarities, m/z errors, and match scores.
    :rtype: pd.DataFrame
    """

    # --- Collect abundance and intensity reference values ---
    # Find the rows with maximum relative_abundance per target ion; "main isotopes"
    idx_max_abundance = (
        match_isotope_df.groupby("target_ion_id")["relative_abundance"].idxmax().values
    )
    # Get the sample_peak_intensity values for the main isotopes
    main_isotope_df = match_isotope_df.loc[
        idx_max_abundance,
        ["target_ion_id", "sample_peak_intensity", "relative_abundance"],
    ].reset_index(drop=True)

    # If existing reference data is provided, use it to override main isotope references
    ## for ions that have prior computed matches (e.g., when adding additional isotopes)
    if existing_reference_df is not None and not existing_reference_df.empty:
        # Get ion IDs that have existing references
        existing_ion_ids = set(existing_reference_df["target_ion_id"].unique())

        # For ions with existing references, replace with the existing data
        # Keep only new ions in main_isotope_df
        main_isotope_df = main_isotope_df[
            ~main_isotope_df["target_ion_id"].isin(existing_ion_ids)
        ]

        # Combine: existing references + new main isotopes
        main_isotope_df = pd.concat(
            [existing_reference_df, main_isotope_df], ignore_index=True
        )
        runtime.logger.debug(
            f"Using {len(existing_ion_ids)} existing main isotope references "
            f"for abundance error calculation"
        )

    # Join the main isotopes with the full match_isotope_df to get the reference values
    abundance_reference_df = pd.merge(
        match_isotope_df,
        main_isotope_df.rename(
            columns={
                "sample_peak_intensity": "sample_peak_intensity_reference",
                "relative_abundance": "relative_abundance_reference",
            }
        ),
        on="target_ion_id",
        how="left",
    )
    # --- Compute match abundance error ---
    # Compute relative peak intensities -> [0, 1]
    sample_peak_intensity_relative = match_isotope_df["sample_peak_intensity"].div(
        abundance_reference_df["sample_peak_intensity_reference"]
    )
    match_isotope_df.loc[:, "sample_peak_intensity_relative"] = (
        sample_peak_intensity_relative.where(
            np.isfinite(sample_peak_intensity_relative), 0.0
        )
    )
    # Normalize relative abundances by the main isotope's relative abundance -> [0, 1]
    match_isotope_df.loc[:, "relative_abundance_norm"] = (
        match_isotope_df["relative_abundance"]
        / abundance_reference_df["relative_abundance_reference"]
    )
    # Calculate abundance error as relative difference
    match_isotope_df.loc[:, "match_abundance_error"] = (
        match_isotope_df["sample_peak_intensity_relative"]
        / match_isotope_df["relative_abundance_norm"]
        - 1.0
    )
    match_isotope_df.drop(columns=["relative_abundance_norm"], inplace=True)

    # --- Calculate m/z errors (in ppm) ---
    match_isotope_df.loc[:, "match_mz_error"] = (
        1e6
        * (match_isotope_df["sample_peak_mz"] - match_isotope_df["mz"])
        / match_isotope_df["mz"]
    )

    # --- Calculate match scores ---
    abundance_term = 1.0 - np.minimum(
        1.0, np.abs(match_isotope_df["match_abundance_error"].values)
    )
    mz_term = np.maximum(
        0.0, 1.0 - 1e-2 * np.abs(match_isotope_df["match_mz_error"].values)
    )
    match_isotope_df["match_score"] = abundance_term * mz_term

    return match_isotope_df


def assign_defaults_to_unmatched(
    match_isotope_df: pd.DataFrame,
    unmatched_mask: pd.Series,
) -> pd.DataFrame:
    """
    Assign default values to unmatched isotopes in the match DataFrame.

    This function sets default values for isotopes that did not find a matching peak,
    ensuring that all required fields are populated with appropriate defaults.

    :param match_isotope_df: DataFrame containing matched isotopes with their properties.
    :type match_isotope_df: pd.DataFrame
    :param unmatched_mask: Boolean mask indicating which isotopes do not have matching peaks.
    :type unmatched_mask: pd.Series
    :return: DataFrame with default values assigned to unmatched isotopes.
    :rtype: pd.DataFrame
    """
    unmatched_count = unmatched_mask.sum()
    runtime.logger.info(f"Found {unmatched_count} isotopes without matching peaks")

    # Set sample_peak_mz for unmatched isotopes using target m/z values
    match_isotope_df.loc[unmatched_mask, "sample_peak_mz"] = match_isotope_df.loc[
        unmatched_mask, "mz"
    ]

    # Apply all defaults except sample_peak_mz which was handled above
    for column, value in unmatched_isotope_params.model_dump().items():
        if column != "sample_peak_mz":
            match_isotope_df.loc[unmatched_mask, column] = value

    return match_isotope_df
