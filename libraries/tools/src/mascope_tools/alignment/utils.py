import os

import numpy as np
import pandas as pd

import mascope_sdk as msdk

from .caching import CacheManager
from .calibration import CentroidedSpectrum, Spectra


def _is_notebook():
    try:
        from IPython import get_ipython

        shell = get_ipython().__class__.__name__
        return shell == "ZMQInteractiveShell"
    except Exception:
        return False


if _is_notebook():
    from tqdm.notebook import tqdm
else:
    from tqdm import tqdm


CACHE_FOLDER = os.path.abspath(os.path.join(os.getcwd(), "cached_spectra"))
# TODO increase chunk size after resolving the issue with blocked server
DOWNLOAD_CHUNK_SIZE = 1
MAX_RETRIES = 5  # Number of retries for fetching data from server

# Satellite peak detection defaults
NEUTRON_MASS = 1.00866491606  # Da


def create_cache_folder():
    os.makedirs(CACHE_FOLDER, exist_ok=True)


def ppm_to_da(mz0: float, ppm: float) -> float:
    return mz0 * ppm * 1e-6


def collect_spectra(
    mascope_url: str,
    access_token: str,
    samples: pd.DataFrame,
    update_cached: bool = False,
) -> Spectra:
    """
    Collects centroided spectra and their corresponding timestamps from a set of samples.

    :param mascope_url: URL of the Mascope server.
    :type mascope_url: str
    :param access_token: User's Jupyter access token.
    :type access_token: str
    :param samples: DataFrame containing sample metadata, must include 'datetime', 'sample_file_id', and 'polarity' columns.
    :type samples: pd.DataFrame
    :param update_cached: If True, will update the cache with new spectra, defaults to False.
    :type update_cached: bool, optional
    :return: Spectra object containing the collected centroided spectra and their timestamps.
    :rtype: Spectra
    """
    create_cache_folder()
    cache = CacheManager(CACHE_FOLDER)
    samples = samples.copy()
    samples["datetime"] = pd.to_datetime(samples["datetime"])
    samples = samples.sort_values("datetime").reset_index(drop=True)

    time_since_first_sample_s = (
        (samples.datetime - samples.datetime[0]).dt.total_seconds().values
    )
    sample_item_ids = samples.sample_item_id.values.tolist()

    # Decide which IDs to fetch vs load based on cache state and update flag
    if update_cached:
        to_fetch = sample_item_ids
        to_load = []
    else:
        to_fetch = [sid for sid in sample_item_ids if not cache.exists(sid)]
        to_load = [sid for sid in sample_item_ids if cache.exists(sid)]

    # Collect all data (per sample_item_id) into a dict
    per_sample_data: dict[str, tuple[list[CentroidedSpectrum], np.ndarray]] = {}

    # Load from cache
    for sid in tqdm(to_load, desc="Loading centroided spectra from cache"):
        loaded = cache.load(sid)
        if loaded is None:
            # Corrupted/missing file -> re-fetch this one
            to_fetch.append(sid)
        else:
            per_sample_data[sid] = loaded

    # Fetch missing (or all, if update_cached)
    if to_fetch:
        num_chunks = (len(to_fetch) + DOWNLOAD_CHUNK_SIZE - 1) // DOWNLOAD_CHUNK_SIZE
        for chunk_idx in tqdm(range(num_chunks), desc="Fetching centroided spectra"):
            chunk = to_fetch[
                chunk_idx * DOWNLOAD_CHUNK_SIZE : (chunk_idx + 1) * DOWNLOAD_CHUNK_SIZE
            ]
            for attempt in range(1, MAX_RETRIES + 1):
                centroided_map = msdk.get_sample_centroids_per_scan(
                    mascope_url=mascope_url,
                    access_token=access_token,
                    sample_item_ids=chunk,
                )
                if centroided_map:
                    break
                if attempt == MAX_RETRIES:
                    raise ValueError(
                        f"No centroided data found for sample_item_ids: {chunk} after {MAX_RETRIES} attempts. "
                        "Check if Mascope server is running and has centroided data."
                    )
            for sample_item_id, centroids in centroided_map.items():
                # Build CentroidedSpectrum list per scan
                spec_list = [
                    CentroidedSpectrum(
                        mz=mzs,
                        intensity=intensities,
                        resolution=resolutions,
                        signal_to_noise=snr,
                        metadata={"sample_item_id": sample_item_id},
                    )
                    for mzs, intensities, resolutions, snr in zip(
                        centroids["masses"],
                        centroids["intensities"],
                        centroids["resolutions"],
                        centroids["signal_to_noise"],
                    )
                ]
                timestamps = np.asarray(centroids["timestamp"], dtype=float)
                # Save/refresh cache
                cache.save(sample_item_id, (spec_list, timestamps))
                per_sample_data[sample_item_id] = (spec_list, timestamps)
    # Gather outputs in the exact input order with correct time offsets
    spectra: list[CentroidedSpectrum] = []
    batch_scan_timestamps: list[float] = []
    for row_idx, sample_item_id in enumerate(sample_item_ids):
        spec_list, timestamps = per_sample_data[sample_item_id]
        spectra.extend(spec_list)
        batch_scan_timestamps.extend(timestamps + time_since_first_sample_s[row_idx])

    return Spectra(spectra, np.asarray(batch_scan_timestamps, dtype=float))


def average_sample_item_spectra(
    mascope_url: str,
    access_token: str,
    sample_item_ids: list[str],
    calibration_factors: list[float] | None = None,
    method: str = "mean",
    update_cached: bool = False,
) -> dict[str, np.ndarray]:
    """
    Calculate the averaged spectrum from the spectra of multiple sample items.

    :param mascope_url: URL of the Mascope server.
    :type mascope_url: str
    :param access_token: User's Jupyter access token
    :type access_token: str
    :param sample_item_ids: List of sample item IDs for which to average spectra.
    :type sample_item_ids: list[str]
    :param calibration_factors: List of m/z calibration factors for each sample item ID, defaults to None
    :param method: Averaging method, defaults to "mean"
    :type method: str, optional
    :param update_cached: If True, will update the cache with new averaged spectra, defaults to False.
    :type update_cached: bool, optional
    :raises ValueError: If the method is not 'mean' or 'median'.
    :return: A dictionary with 'mz' and 'intensity' keys, where 'mz' is the common m/z grid and 'intensity' is the averaged intensity at each m/z.
    :rtype: dict
    """
    create_cache_folder()
    cache = CacheManager(CACHE_FOLDER)

    if calibration_factors is not None:
        calibration_factors = np.asarray(calibration_factors, dtype=float)
    else:
        calibration_factors = np.ones(len(sample_item_ids), dtype=float)

    avg_cache_key = {
        "type": "averaged_spectrum",
        "sample_item_ids": tuple(sample_item_ids),
        "calibration_factors": tuple(calibration_factors.tolist()),
        "method": method,
    }

    if not update_cached:
        cached_result = cache.load(avg_cache_key)
        if cached_result is not None:
            return cached_result

    per_sample_avg_keys = [
        (
            "averaged_spectrum_per_sample",
            sample_item_id,
            float(calibration_factors[i]),
            method,
        )
        for i, sample_item_id in enumerate(sample_item_ids)
    ]

    averaged_specs: list[dict[str, np.ndarray] | None] = [None] * len(sample_item_ids)
    loaded_mask = np.zeros(len(sample_item_ids), dtype=bool)

    for i, key in enumerate(
        tqdm(
            per_sample_avg_keys, desc="Check if some of averaged spectra are cached..."
        )
    ):
        if not update_cached:
            cached = cache.load(key)
            if cached is not None:
                averaged_specs[i] = cached
                loaded_mask[i] = True

    to_fetch_indices = (
        np.where(~loaded_mask)[0]
        if not update_cached
        else np.arange(len(sample_item_ids))
    )
    if len(to_fetch_indices) > 0:
        for chunk_start in tqdm(
            range(0, len(to_fetch_indices), DOWNLOAD_CHUNK_SIZE),
            desc="Fetching missing averaged spectra from server",
        ):
            chunk_indices = to_fetch_indices[
                chunk_start : chunk_start + DOWNLOAD_CHUNK_SIZE
            ]
            chunk_sample_ids = [sample_item_ids[i] for i in chunk_indices]
            chunk_cal_factors = [float(calibration_factors[i]) for i in chunk_indices]
            chunk_keys = [per_sample_avg_keys[i] for i in chunk_indices]

            for attempt in range(1, MAX_RETRIES + 1):
                chunk_averaged_specs = msdk.get_samples_spectra(
                    mascope_url=mascope_url,
                    access_token=access_token,
                    sample_item_ids=chunk_sample_ids,
                )
                if chunk_averaged_specs and len(chunk_averaged_specs) == len(
                    chunk_sample_ids
                ):
                    break
                if attempt == MAX_RETRIES:
                    raise ValueError(
                        f"No spectra found for sample_item_ids: {chunk_sample_ids} after {MAX_RETRIES} attempts. "
                        "Check if Mascope server is running and has spectrum data."
                    )

            for spec, cal, key, arr_idx in zip(
                chunk_averaged_specs, chunk_cal_factors, chunk_keys, chunk_indices
            ):
                mz_arr = np.asarray(spec["mz"], dtype=float) * cal
                spec["mz"] = mz_arr
                cache.save(key, spec)
                averaged_specs[arr_idx] = spec

    if any(spec is None for spec in averaged_specs):
        missing = [i for i, spec in enumerate(averaged_specs) if spec is None]
        raise RuntimeError(
            f"Failed to load or fetch averaged spectra for indices: {missing}"
        )

    union_mz = np.unique(np.concatenate([spec["mz"] for spec in averaged_specs]))
    union_mz = np.sort(union_mz)

    n = len(averaged_specs)
    if method == "mean":
        sum_intensity = np.zeros_like(union_mz)
        for spec in averaged_specs:
            sum_intensity += np.interp(
                union_mz, spec["mz"], spec["intensity"], left=0, right=0
            )
        avg_intensity = sum_intensity / n
    elif method == "median":
        # For median, chunking is not possible; fallback to stacking for correctness
        interpolated_spectra = np.empty((n, union_mz.size), dtype=float)
        for i, spec in enumerate(averaged_specs):
            interpolated_spectra[i] = np.interp(
                union_mz, spec["mz"], spec["intensity"], left=0, right=0
            )
        avg_intensity = np.median(interpolated_spectra, axis=0)
    else:
        raise ValueError("method must be 'mean' or 'median'")

    result = {"mz": union_mz, "intensity": avg_intensity}
    cache.save(avg_cache_key, result)
    return result


def filter_centroids(
    spectra: Spectra, min_intensity: float = 0, snr_threshold: float = 3
) -> Spectra:
    """
    Filters out noise centroids from the spectra based on minimum intensity and SNR threshold.

    :param spectra: Spectra object containing centroided spectra.
    :type spectra: Spectra
    :param min_intensity: Minimum intensity threshold for filtering, defaults to 0.
    :type min_intensity: float, optional
    :param snr_threshold: Minimum signal-to-noise ratio threshold for filtering, defaults to 3.
    :type snr_threshold: float, optional
    :return: Filtered Spectra object with noise centroids removed.
    :rtype: Spectra
    """
    filtered_spectra = []
    for spec in spectra:
        mask = (spec.intensity >= min_intensity) & (
            spec.signal_to_noise >= snr_threshold
        )
        filtered_spectra.append(
            CentroidedSpectrum(
                mz=spec.mz[mask],
                intensity=spec.intensity[mask],
                resolution=spec.resolution[mask],
                signal_to_noise=spec.signal_to_noise[mask],
                metadata=spec.metadata,
            )
        )
    return Spectra(filtered_spectra, spectra.timestamps)


def flag_satellite_peaks(
    peaks: pd.DataFrame,
    base_peak_percentile: float = 99.0,
    top_n_bases: int | None = 20,
    window_ppm: float = 350.0,
    ratio_max: float = 0.04,
    ratio_min: float = 1e-6,
    symmetry_tolerance_ppm: float = 1.5,
    symmetry_ratio_similarity_min: float = 0.25,
    tight_window_ppm: float = 25.0,
    isotope_tolerance_ppm: float = 2.0,
    charge_range: tuple[int, int] = (1, 2),
) -> pd.DataFrame:
    """Flag Thermo/FTMS satellite peaks around very intense base peaks.
    Adds a boolean column 'is_satellite_peak' to the returned DataFrame.

    Heuristics:
    - Satellites are much weaker than the base peak and lie around them.
    - They tend to appear as mirror pairs around the base: matching offsets
      are strong evidence even when the pair's intensities differ severalfold
      (real FTMS sidelobe pairs are intensity-asymmetric, so the similarity
      gate must stay loose).
    - Weak unpaired peaks very close to the base are sidelobes too; short
      transients push them out to tens of ppm, hence the tight window default.
    - Isotopes (+1.003355/z) are excluded.

    :param peaks: DataFrame containing peaks with 'mz' and 'intensity' columns.
    :type peaks: pd.DataFrame
    :param base_peak_percentile: Percentile for selecting base peaks.
    :type base_peak_percentile: float, optional
    :param top_n_bases: If specified, overrides the percentile and selects the top N bases.
    :type top_n_bases: int | None, optional
    :param window_ppm: Search window around base peaks in ppm.
    :type window_ppm: float, optional
    :param ratio_max: Maximum intensity ratio for satellite peaks relative to base peaks.
    :type ratio_max: float, optional
    :param ratio_min: Minimum intensity ratio for satellite peaks relative to base peaks.
    :type ratio_min: float, optional
    :param symmetry_tolerance_ppm: Tolerance for symmetric pairing around the base peak in ppm.
    :type symmetry_tolerance_ppm: float, optional
    :param symmetry_ratio_similarity_min: Minimum intensity-ratio similarity
        (min/max of the pair's base-relative ratios) for a mirror pair to count.
    :type symmetry_ratio_similarity_min: float, optional
    :param tight_window_ppm: Window around the base within which weak unpaired
        peaks are flagged as single-sided satellites.
    :type tight_window_ppm: float, optional
    :param isotope_tolerance_ppm: Tolerance for excluding +1 isotopes in ppm.
    :type isotope_tolerance_ppm: float, optional
    :param charge_range: Range of charge states to consider for isotopes.
    :type charge_range: tuple[int, int], optional
    :return: DataFrame with an additional boolean column 'is_satellite_peak' indicating satellite peaks.
    :rtype: pd.DataFrame
    """
    if peaks.empty:
        out = peaks.copy()
        out["is_satellite_peak"] = False
        return out

    if "mz" not in peaks.columns or "intensity" not in peaks.columns:
        raise ValueError("peaks must contain 'mz' and 'intensity' columns.")

    df = peaks.copy()
    mz = df["mz"].to_numpy(dtype=float)
    intensity = df["intensity"].to_numpy(dtype=float)

    # Remove non-positive intensities early (cannot be parents nor satellites).
    valid_mask = intensity > 0
    if not np.all(valid_mask):
        mz = mz[valid_mask]
        intensity = intensity[valid_mask]
        original_index = np.flatnonzero(valid_mask)
    else:
        original_index = np.arange(mz.size)

    if mz.size == 0:
        out = peaks.copy()
        out["is_satellite_peak"] = False
        return out

    # Sort by m/z for efficient window querying.
    order = np.argsort(mz)
    mz_sorted = mz[order]
    intensity_sorted = intensity[order]
    n_peaks = mz_sorted.size

    # Select base peaks (intensity-based).
    base_thr = np.quantile(intensity_sorted, base_peak_percentile / 100.0)
    base_candidates = np.flatnonzero(intensity_sorted >= base_thr)
    if top_n_bases is not None and base_candidates.size > top_n_bases:
        # Keep top_n_bases highest-intensity indices.
        strongest_local = np.argsort(intensity_sorted[base_candidates])[::-1][
            :top_n_bases
        ]
        base_indices = np.sort(base_candidates[strongest_local])
    else:
        base_indices = base_candidates

    if base_indices.size == 0:
        # No bases found at this percentile; return all False.
        result = peaks.copy()
        result["is_satellite_peak"] = False
        return result

    # Precompute charges and isotope delta masses.
    charges = np.arange(charge_range[0], charge_range[1] + 1, dtype=int)
    isotope_deltas = NEUTRON_MASS / charges  # Da

    is_satellite_sorted = np.zeros(n_peaks, dtype=bool)

    # Precompute ppm to Da helper inline (avoid extra function call in tight loop).
    def ppm_to_da_local(mass: float, ppm: float) -> float:
        return mass * ppm * 1e-6

    symmetry_ppm = symmetry_tolerance_ppm
    isotope_ppm = isotope_tolerance_ppm
    win_ppm = window_ppm
    ratio_lo = ratio_min
    ratio_hi = ratio_max

    for base_idx in base_indices:
        parent_mz = mz_sorted[base_idx]
        parent_intensity = intensity_sorted[base_idx]
        if parent_intensity <= 0:
            continue

        win_da = ppm_to_da_local(parent_mz, win_ppm)
        left = np.searchsorted(mz_sorted, parent_mz - win_da, side="left")
        right = np.searchsorted(mz_sorted, parent_mz + win_da, side="right")

        if right - left <= 1:
            continue

        cand_idx = np.arange(left, right)
        cand_idx = cand_idx[cand_idx != base_idx]
        if cand_idx.size == 0:
            continue

        rel_ratio = intensity_sorted[cand_idx] / parent_intensity
        ratio_mask = (rel_ratio >= ratio_lo) & (rel_ratio <= ratio_hi)
        cand_idx = cand_idx[ratio_mask]
        if cand_idx.size == 0:
            continue

        cand_mz = mz_sorted[cand_idx]
        dmz = cand_mz - parent_mz

        # Exclude +1 isotopes (only dmz > 0).
        pos_mask = dmz > 0
        if np.any(pos_mask):
            dmz_pos = dmz[pos_mask]
            cand_idx_pos = cand_idx[pos_mask]
            exclude_iso = np.zeros(dmz_pos.size, dtype=bool)
            # Vectorized isotope exclusion.
            for iso_da in isotope_deltas:
                tolerance_da = ppm_to_da_local(parent_mz + iso_da, isotope_ppm)
                exclude_iso |= np.abs(dmz_pos - iso_da) <= tolerance_da
            keep_pos = ~exclude_iso
            # Recombine positive + negative side indices.
            cand_idx = np.concatenate([cand_idx[~pos_mask], cand_idx_pos[keep_pos]])
            dmz = mz_sorted[cand_idx] - parent_mz

        if cand_idx.size == 0:
            continue

        # Symmetry detection: match |dmz_left| ≈ dmz_right within tolerance.
        left_mask = dmz < 0
        right_mask = dmz > 0
        left_dmz = -dmz[left_mask]
        right_dmz = dmz[right_mask]
        left_idx = cand_idx[left_mask]
        right_idx = cand_idx[right_mask]

        # Tolerance (Da) computed at parent m/z.
        sym_tol_da = ppm_to_da_local(parent_mz, symmetry_ppm)

        # Use sorted arrays for matching.
        if left_dmz.size and right_dmz.size:
            # For each right offset, search approximate left match.
            # Sort left_dmz for binary search.
            left_order = np.argsort(left_dmz)
            left_dmz_sorted = left_dmz[left_order]
            left_idx_sorted = left_idx[left_order]

            for r_off, r_i in zip(right_dmz, right_idx):
                lo = np.searchsorted(left_dmz_sorted, r_off - sym_tol_da, side="left")
                hi = np.searchsorted(left_dmz_sorted, r_off + sym_tol_da, side="right")
                if hi <= lo:
                    continue
                # Choose closest left offset.
                segment = left_dmz_sorted[lo:hi]
                closest_rel = np.argmin(np.abs(segment - r_off))
                l_i = left_idx_sorted[lo + closest_rel]

                # Compare intensity ratios for similarity.
                r_ratio = intensity_sorted[r_i] / parent_intensity
                l_ratio = intensity_sorted[l_i] / parent_intensity
                ratio_similarity = min(r_ratio, l_ratio) / max(r_ratio, l_ratio)
                if ratio_similarity >= symmetry_ratio_similarity_min:
                    is_satellite_sorted[r_i] = True
                    is_satellite_sorted[l_i] = True

        # Single-sided satellites (weak, very near parent).
        # Restrict to those still unflagged.
        unresolved = cand_idx[~is_satellite_sorted[cand_idx]]
        if unresolved.size:
            tight_window_da = ppm_to_da_local(
                parent_mz, min(win_ppm * 0.5, tight_window_ppm)
            )
            near_mask = np.abs(mz_sorted[unresolved] - parent_mz) <= tight_window_da
            weak_mask = (intensity_sorted[unresolved] / parent_intensity) <= ratio_hi
            final_mask = near_mask & weak_mask
            is_satellite_sorted[unresolved[final_mask]] = True

    # Map back to original indexing.
    back_flags = np.zeros(n_peaks, dtype=bool)
    back_flags[order] = is_satellite_sorted
    full_flags = np.zeros(df.shape[0], dtype=bool)
    full_flags[original_index] = back_flags

    out = peaks.copy()
    out["is_satellite_peak"] = full_flags
    return out
