"""MS2 analysis service.

Provides MS2 data extraction and aggregation.
Compute functions (in mascope_signal) use asyncio.to_thread internally, so the
event loop is not blocked; however, each request still awaits the result
synchronously—long-running operations may exceed client-side timeouts.

A per-request ``timeout`` parameter (default 120 s) guards against indefinite
waits: if the computation exceeds the limit, the server responds with 504.
"""

import asyncio
from typing import Literal

import mascope_signal.compute as m_compute
from mascope_backend.api.controllers.samples.lib.samples_fetch import fetch_sample
from mascope_backend.api.lib.api_features import api_controller
from mascope_backend.api.lib.exceptions.api_exceptions import ApiException


DEFAULT_TIMEOUT = 120  # seconds


def ms2_group_key(parent_peak_mz: float, activation: str) -> str:
    """JSON key for one (parent peak, activation) MS2 group.

    ``"137.096@hcd40.00"``, mirroring the instrument's scan-filter notation, so
    the step a spectrum belongs to is readable straight off the key. Falls back
    to the bare m/z when the filter carried no activation.
    """
    return f"{parent_peak_mz}@{activation}" if activation else f"{parent_peak_mz}"


@api_controller()
async def get_ms2_summary(
    sample_item_id: str,
    parent_peak_tolerance: float = 0.001,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict:
    """Retrieve MS2 summary for a sample.

    Returns parent peaks, HCD energy map, isolation width, and scan counts.

    :param sample_item_id: Unique identifier for the sample.
    :param parent_peak_tolerance: Tolerance in Da for merging duplicate parent peaks.
    :param timeout: Maximum seconds to wait for the computation.
    :return: Dictionary with MS2 summary data.
    """
    sample = await fetch_sample(sample_item_id)

    try:
        summary = await asyncio.wait_for(
            m_compute.get_ms2_summary(
                sample.filename,
                t_min=sample.t0,
                t_max=sample.t1,
                polarity=sample.polarity,
                parent_peak_tolerance=parent_peak_tolerance,
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        raise ApiException(
            f"MS2 summary timed out after {timeout}s for sample",
            f"asyncio.TimeoutError in get_ms2_summary (timeout={timeout}s)",
            504,
        )

    return {
        "message": f"MS2 summary for sample '{sample.sample_item_name}'.",
        "data": summary,
    }


@api_controller()
async def get_ms2_averaged_centroids(
    sample_item_id: str,
    noise_threshold: float = 10.0,
    parent_peak_tolerance: float = 0.001,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict:
    """Retrieve averaged MS2 centroids for each (parent peak, activation) group.

    Performs centroid extraction, noise filtering, grouping by parent peak and
    activation, and centroid averaging. Keys are ``"<parent m/z>@<activation>"``
    (e.g. ``"137.096@hcd40.00"``), mirroring the instrument's own scan-filter
    notation -- a stepped-energy acquisition returns one spectrum per step
    rather than one that blends them.

    :param sample_item_id: Unique identifier for the sample.
    :param noise_threshold: Minimum signal-to-noise ratio threshold.
    :param parent_peak_tolerance: Tolerance in Da for merging parent peaks.
    :param timeout: Maximum seconds to wait for the computation.
    :return: Dictionary with averaged MS2 centroids keyed by group.
    """
    sample = await fetch_sample(sample_item_id)

    try:
        ms2_by_parent = await asyncio.wait_for(
            m_compute.get_orbi_ms2_centroids_by_parent(
                sample.filename,
                t_min=sample.t0,
                t_max=sample.t1,
                polarity=sample.polarity,
                parent_peak_tolerance=parent_peak_tolerance,
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        raise ApiException(
            f"MS2 centroid extraction timed out after {timeout}s for sample",
            f"asyncio.TimeoutError in get_ms2_averaged_centroids (timeout={timeout}s)",
            504,
        )

    # Convert to serializable format with noise filtering
    averaged = {}
    for (pp, activation), (
        masses,
        intensities,
        resolutions,
        signal_to_noise,
    ) in ms2_by_parent.items():
        mask = signal_to_noise >= noise_threshold
        averaged[ms2_group_key(pp, activation)] = {
            "parent_peak_mz": pp,
            "activation": activation,
            "mz": masses[mask].tolist(),
            "intensity": intensities[mask].tolist(),
            "resolution": resolutions[mask].tolist(),
            "signal_to_noise": signal_to_noise[mask].tolist(),
        }

    return {
        "message": (
            f"Averaged MS2 centroids for {len(ms2_by_parent)} parent peak groups"
            f" in sample '{sample.sample_item_name}'."
        ),
        "results": len(ms2_by_parent),
        "data": averaged,
    }


@api_controller()
async def get_ms1_averaged_centroids(
    sample_item_id: str,
    ppm: int = 1,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict:
    """Retrieve averaged MS1 centroids for a sample.

    Uses Thermo's native scan averaging with ppm-based binning to compute
    averaged centroided MS1 spectrum over the sample's time range.

    :param sample_item_id: Unique identifier for the sample.
    :param ppm: Mass tolerance in ppm for centroid binning.
    :param timeout: Maximum seconds to wait for the computation.
    :return: Dictionary with mz, intensity, resolution, and signal_to_noise arrays.
    """
    sample = await fetch_sample(sample_item_id)

    try:
        (
            masses,
            intensities,
            resolutions,
            signal_to_noise,
        ) = await asyncio.wait_for(
            m_compute.get_orbi_centroids(
                sample.filename,
                t_min=sample.t0,
                t_max=sample.t1,
                polarity=sample.polarity,
                ppm=ppm,
                average=True,
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        raise ApiException(
            f"MS1 centroid extraction timed out after {timeout}s for sample",
            f"asyncio.TimeoutError in get_ms1_averaged_centroids (timeout={timeout}s)",
            504,
        )

    return {
        "message": f"Averaged MS1 centroids for sample '{sample.sample_item_name}'.",
        "data": {
            "mz": masses.tolist(),
            "intensity": intensities.tolist(),
            "resolution": resolutions.tolist(),
            "signal_to_noise": signal_to_noise.tolist(),
        },
    }


@api_controller()
async def get_ms2_timeseries(
    sample_item_id: str,
    parent_peak_mz: float,
    noise_threshold: float = 10.0,
    parent_peak_tolerance: float = 0.001,
    normalize_by: Literal["tic"] | None = None,
    activation: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict:
    """Retrieve fragment timeseries for a single parent peak.

    :param sample_item_id: Unique identifier for the sample.
    :param parent_peak_mz: The parent peak m/z to get timeseries for.
    :param noise_threshold: Minimum signal-to-noise ratio threshold.
    :param parent_peak_tolerance: Tolerance in Da for matching parent peaks.
    :param normalize_by: Normalization mode. ``"tic"`` normalizes by scan TIC,
        ``None`` returns raw intensities.
    :param activation: Restrict to one activation (e.g. ``"hcd40.00"``);
        defaults to every activation of the parent peak, so a stepped-energy
        run shows its fragments changing as the energy steps.
    :param timeout: Maximum seconds to wait for the computation.
    :return: Dictionary with fragment timeseries data.
    """
    sample = await fetch_sample(sample_item_id)

    try:
        timeseries = await asyncio.wait_for(
            m_compute.get_ms2_fragment_timeseries(
                sample.filename,
                parent_peak_mz,
                t_min=sample.t0,
                t_max=sample.t1,
                polarity=sample.polarity,
                noise_threshold=noise_threshold,
                parent_peak_tolerance=parent_peak_tolerance,
                normalize_by=normalize_by,
                activation=activation,
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        raise ApiException(
            f"MS2 timeseries extraction timed out after {timeout}s for sample",
            f"asyncio.TimeoutError in get_ms2_timeseries (timeout={timeout}s)",
            504,
        )

    return {
        "message": (
            f"MS2 timeseries for parent peak {parent_peak_mz:.4f} m/z"
            f" in sample '{sample.sample_item_name}'."
        ),
        "data": timeseries,
    }
