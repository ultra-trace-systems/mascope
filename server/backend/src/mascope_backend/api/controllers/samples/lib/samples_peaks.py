"""Peak data loading and aggregation helpers.

This module handles loading peak data from sample files, applying polarity
and m/z filtering, and computing area/height aggregations either from
pre-computed sums (full sample) or from timeseries data (time-filtered).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import xarray as xr

import mascope_signal.compute as m_compute
from mascope_backend.api.lib.exceptions.api_exceptions import NotFoundException
from mascope_file.io import load_peak_data


@dataclass
class PeakData:
    """Extracted and aggregated peak data ready for response formatting."""

    peak_ids: list[str]
    mz_values: list[float]
    areas: list[float] | None = None
    heights: list[float] | None = None
    sparsity: list[float] | None = None
    # Per-peak signal-to-noise, when the file carries one. What makes a peak's
    # noise floor readable by anything working off this read rather than off a
    # match frame - the assignment engine's untargeted stage, which judges a
    # predicted isotopologue's absence against the noise. ``None`` for a file
    # that stores none, which is an honest "no estimate" and never a zero.
    signal_to_noise: list[float] | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        """Number of peaks"""
        return len(self.mz_values)


def _load_and_filter(
    filename: str,
    polarity: str,
    mz_min: float | None,
    mz_max: float | None,
) -> xr.Dataset:
    """Load peak data and apply polarity + m/z range filters.

    :raises NotFoundException: If the sample file is missing.
    """
    try:
        data = load_peak_data(filename)
    except FileNotFoundError as e:
        raise NotFoundException(
            f"Sample file with name '{filename}' was not found or has not been processed"
        ) from e

    polarity_mask = np.where(data.polarity.values == polarity)[0]
    data = data.isel(mz=polarity_mask)

    if mz_min is not None or mz_max is not None:
        data = data.sel(mz=slice(mz_min, mz_max))

    return data


def _peak_signal_to_noise(data: xr.Dataset) -> list[float] | None:
    """The per-peak noise estimate, or None for a file that stores none.

    Old files predate the variable, so its absence is a fact about the file and
    not an error: every consumer treats a missing estimate as "not measured"
    and judges the peak at the instrument's own width instead.
    """
    if "signal_to_noise" not in data:
        return None
    return data.signal_to_noise.values.tolist()


def _aggregate_full_sample(
    data: xr.Dataset,
    filename: str,
    polarity: Literal["+", "-"],
    *,
    areas: bool,
    heights: bool,
    average: bool,
) -> PeakData:
    """Aggregate peak data over the full sample (pre-computed sums)."""
    if average:
        timestamps = m_compute.get_scan_timestamps(filename, polarity=polarity)
        average_factor = len(timestamps) if len(timestamps) > 0 else 1
    else:
        average_factor = 1

    return PeakData(
        peak_ids=data.peak_id.values.tolist(),
        mz_values=data.mz.values.tolist(),
        areas=(data.sum_peak_areas.values / average_factor).tolist() if areas else None,
        heights=(
            (data.sum_peak_heights.values / average_factor).tolist()
            if heights
            else None
        ),
        sparsity=data.sparsity.values.tolist(),
        signal_to_noise=_peak_signal_to_noise(data),
    )


def _aggregate_time_range(
    data: xr.Dataset,
    filename: str,
    polarity: Literal["+", "-"],
    t_min: float,
    t_max: float,
    *,
    areas: bool,
    heights: bool,
    average: bool,
) -> PeakData:
    """Aggregate peak data over a specific time range using timeseries data.

    Peaks without pre-computed timeseries are excluded and a warning is added
    to the result so the caller can inform the user.
    """
    warnings: list[str] = []

    # Check which peaks have timeseries data
    has_ts = data.is_timeseries_computed.values
    if not np.all(has_ts):
        n_missing = int(np.sum(~has_ts))
        warnings.append(
            f"{n_missing} peak(s) were excluded because their timeseries "
            f"have not been computed yet. Re-run peak detection to include them."
        )
        data = data.isel(mz=np.where(has_ts)[0])

    if data.mz.size == 0:
        return PeakData(
            peak_ids=[],
            mz_values=[],
            areas=[] if areas else None,
            heights=[] if heights else None,
            sparsity=[],
            warnings=warnings,
        )

    # Get scan timestamps in the requested time range
    timestamps = m_compute.get_scan_timestamps(
        filename, t_min=t_min, t_max=t_max, polarity=polarity
    )
    if len(timestamps) == 0:
        return PeakData(
            peak_ids=data.peak_id.values.tolist(),
            mz_values=data.mz.values.tolist(),
            areas=[0.0] * data.mz.size if areas else None,
            heights=[0.0] * data.mz.size if heights else None,
            sparsity=data.sparsity.values.tolist(),
            signal_to_noise=_peak_signal_to_noise(data),
            warnings=warnings,
        )

    # Select the time slice and sum
    time_slice = data.sel(time=timestamps, method="nearest")
    average_factor = len(timestamps) if average else 1

    return PeakData(
        peak_ids=data.peak_id.values.tolist(),
        mz_values=data.mz.values.tolist(),
        areas=(
            (np.nansum(time_slice.peak_areas.values, axis=1) / average_factor).tolist()
            if areas
            else None
        ),
        heights=(
            (
                np.nansum(time_slice.peak_heights.values, axis=1) / average_factor
            ).tolist()
            if heights
            else None
        ),
        sparsity=data.sparsity.values.tolist(),
        signal_to_noise=_peak_signal_to_noise(data),
        warnings=warnings,
    )


def extract_peaks(
    filename: str,
    polarity: Literal["+", "-"],
    sample_t0: float,
    sample_t1: float,
    *,
    areas: bool = True,
    heights: bool = True,
    average: bool = True,
    t_min: float | None = None,
    t_max: float | None = None,
    mz_min: float | None = None,
    mz_max: float | None = None,
) -> PeakData:
    """Load peak data from a sample file and aggregate areas/heights.

    When ``t_min`` or ``t_max`` is provided, aggregation is performed over the
    requested time window using per-scan timeseries data.  Peaks whose
    timeseries have not been computed are excluded and a warning is returned.

    When neither time parameter is provided, pre-computed sums are used (fast
    path, existing behaviour).

    :param filename: Sample file name.
    :param polarity: Sample polarity (``"+"`` or ``"-"``).
    :param sample_t0: Sample acquisition start time in seconds.
    :param sample_t1: Sample acquisition end time in seconds.
    :param areas: Include peak areas. Defaults to True.
    :param heights: Include peak heights. Defaults to True.
    :param average: Return averaged data. Defaults to True.
    :param t_min: Minimum time in seconds for time-range filtering.
    :param t_max: Maximum time in seconds for time-range filtering.
    :param mz_min: Minimum m/z value for filtering.
    :param mz_max: Maximum m/z value for filtering.
    :raises NotFoundException: If the sample file is missing.
    :raises ValueError: If the time range is outside the sample window.
    """
    from mascope_backend.api.controllers.samples.samples_controller import (
        _validate_time_range,
    )

    data = _load_and_filter(filename, polarity, mz_min, mz_max)

    if data.mz.size == 0:
        return PeakData(
            peak_ids=[],
            mz_values=[],
            areas=[] if areas else None,
            heights=[] if heights else None,
            sparsity=[],
        )

    has_time_filter = t_min is not None or t_max is not None
    if has_time_filter:
        t_min_eff, t_max_eff, _ = _validate_time_range(
            t_min, t_max, sample_t0, sample_t1
        )
        return _aggregate_time_range(
            data,
            filename,
            polarity,
            t_min_eff,
            t_max_eff,
            areas=areas,
            heights=heights,
            average=average,
        )

    return _aggregate_full_sample(
        data, filename, polarity, areas=areas, heights=heights, average=average
    )
