import asyncio
import hashlib
import json
import os
from contextlib import suppress
from typing import Iterable, Literal

import dask.array as da
import numpy as np
import pandas as pd
import xarray as xr
import zarr

import mascope_file.io as m_io
import mascope_file.name as m_name
import mascope_signal.mz_factor as m_factor
import mascope_signal.stitch as m_stitch
import mascope_thermo.streams as m_streams
import mascope_thermo.thermo as m_thermo
import mascope_tofwerk.tofwerk as m_tofwerk
from mascope_runtime.logging import SENTRY_FINGERPRINT
from mascope_signal.runtime import runtime
from mascope_thermo.backend import averaged_profile_signature
from mascope_tools.alignment.calibration import CentroidedSpectrum, MassAligner, Spectra


# Peak alignment parameters
ALIGNMENT_MIN_INTENSITY = 0.0  # Minimum intensity for mass alignment
ALIGNMENT_WINDOW_FACTOR = 1.0  # Mass alignment window factor (times FWHM)
ALIGNMENT_MIN_FRACTION = 1.0  # Minimum fraction of scans for mass alignment

# Peak aggregation parameters
AGGREGATION_WINDOW_FACTOR = 1  # Peak aggregation window factor (times FWHM)

#: The attribute a cached sum signal of one scan stream names its stream by,
#: the stream's key. A signal cached before streams were calibrated apart
#: carries none.
CACHED_STREAM_ATTR = "stream"


def _refuse_stream_unless_raw_orbitrap(sample_type: str, stream: str | None) -> None:
    """Refuse to read one scan stream from a sample type that has none.

    A scan stream is the scans of one experiment of an acquisition method
    (``mascope_thermo.streams``), and only a raw Orbitrap file is read scan by
    scan under one. Asked of any other type the stream could only be ignored,
    and the caller would be handed every scan as if they were the stream's.

    :param sample_type: The sample file type, e.g. ``"orbi_raw"``
    :type sample_type: str
    :param stream: The stream key asked for, or None
    :type stream: str | None
    :raises ValueError: If a stream is asked of a type that is not a raw
        Orbitrap file
    """
    if stream is not None and sample_type != "orbi_raw":
        raise ValueError(
            f"A '{sample_type}' sample file has no scan streams to read apart; "
            f"stream '{stream}' was asked for."
        )


def get_peak_streams(base_filename: str) -> list[dict]:
    """The scan streams a sample file's peaks are detected per, or ``[]``.

    Only a raw Orbitrap file can hold more than one: its scans are read under
    the experiments of its acquisition method, and the peaks of a file whose
    method runs more than one in a polarity can be detected per experiment
    (:func:`mascope_thermo.streams.peak_streams`). Every other sample type is
    one stream. The census is taken from the file as it reads now, not from
    its ``.props``, because the keys that select a stream's scans are the
    reader's own.

    :param base_filename: Sample file filename
    :type base_filename: str
    :return: The MS1 streams to detect apart, or ``[]`` for a file that is
        detected whole
    :rtype: list[dict]
    """
    if m_name.get_sample_file_type(base_filename) != "orbi_raw":
        return []
    datafile_path = m_name.filename_to_datafile_path(base_filename)
    return m_streams.peak_streams(m_streams.file_scan_streams(datafile_path))


def has_scan_streams(base_filename: str) -> bool:
    """Whether a sample file is read for its scan streams: a raw Orbitrap
    file is, every other type has only what its conversion stored.

    :param base_filename: Sample file filename
    :type base_filename: str
    :return: True for a raw Orbitrap file
    :rtype: bool
    """
    return m_name.get_sample_file_type(base_filename) == "orbi_raw"


def get_scan_streams(base_filename: str) -> list[dict]:
    """The scan streams of a raw Orbitrap file, as it reads now.

    The whole census (:func:`mascope_thermo.streams.scan_streams`), taken from
    the file with the reader of the day, as :func:`get_peak_streams` takes
    the streams peaks are detected per: whatever is built on the two then
    rests on one reading. The census stored at conversion keeps serving the
    readers that take it from ``.props``.

    :param base_filename: Sample file filename
    :type base_filename: str
    :return: The census, or ``[]`` for a sample type that has none
    :rtype: list[dict]
    """
    if not has_scan_streams(base_filename):
        return []
    return m_streams.file_scan_streams(m_name.filename_to_datafile_path(base_filename))


def peak_store_streams(peak_data: xr.Dataset) -> list[str]:
    """The stream keys a peak store's peaks were detected per, or ``[]``.

    A per-stream store labels each peak and each scan with its stream:
    ``stream`` along ``mz`` and ``scan_stream`` along ``time``, both an index
    into the keys the store's ``streams`` attribute lists. A store with
    neither is pooled - one peak list per polarity, as every store was before
    streams could be read apart - and reads the way it always has.

    The keys and the two labels only mean something together, so a dataset
    that carries some of them and not the rest is refused rather than read
    as pooled: filled as one, a per-stream store's peak would be handed the
    scans of every stream.

    :param peak_data: A peak store, or a selection of its peaks or scans
    :type peak_data: xr.Dataset
    :raises ValueError: If the dataset carries only part of what a
        per-stream store does
    :return: The stream keys, in the order the labels index them
    :rtype: list[str]
    """
    return _stream_keys_of(
        peak_data.attrs.get("streams"),
        "stream" in peak_data.variables,
        "scan_stream" in peak_data.variables,
    )


def _stream_keys_of(keys, has_stream: bool, has_scan_stream: bool) -> list[str]:
    """The decision of :func:`peak_store_streams`, from what a store carries:
    its ``streams`` attribute and whether it holds a ``stream`` and a
    ``scan_stream`` array. Shared with :func:`peak_store_stitches`, which
    reads them off the store's metadata, so the two cannot disagree."""
    keys = list(keys or [])
    carried = {
        "the stream keys": bool(keys),
        "the stream of each peak": has_stream,
        "the stream of each scan": has_scan_stream,
    }
    if all(carried.values()):
        return keys
    if not any(carried.values()):
        return []
    raise ValueError(
        "A peak store that holds a peak list per scan stream carries its "
        "stream keys and the stream of each peak and each scan together; "
        "this one is missing "
        + " and ".join(name for name, there in carried.items() if not there)
        + "."
    )


def scans_per_peak(peak_data: xr.Dataset, timestamps: np.ndarray) -> np.ndarray:
    """How many of the given scans each peak holds values on, one per peak.

    A pooled store's peak holds a value on every scan of its polarity, a
    per-stream store's on its own stream's scans only
    (:func:`peak_store_streams`). A sum over a time range's scans is averaged
    by this count and not by the range's scans of every stream of the
    polarity, which would read an ion a short stream measured at a fraction
    of its height - the dilution the stitch exists to remove. The stored
    whole-sample sums are counted by :func:`stored_scans_per_peak`.

    :param peak_data: A peak store, or a selection of its peaks; with
        ``scan_stream`` along ``time`` for a per-stream store
    :type peak_data: xr.Dataset
    :param timestamps: The scans of the range, those the sum is taken over
    :type timestamps: np.ndarray
    :return: One count per peak, never below one
    :rtype: np.ndarray
    """
    streams = peak_store_streams(peak_data)
    if not streams:
        return np.full(peak_data.mz.size, max(len(timestamps), 1), dtype=int)
    scan_streams = np.asarray(
        peak_data.scan_stream.sel(time=timestamps, method="nearest").values, dtype=int
    )
    return _counts_per_peak(peak_data, scan_streams, len(streams))


def stored_scans_per_peak(
    peak_data: xr.Dataset, polarity_scans: np.ndarray
) -> np.ndarray:
    """How many scans each peak's stored sum is over, one per peak.

    ``sum_peak_heights`` and ``sum_peak_areas`` are summed over the scans
    the store's own axis holds for the peak. For a per-stream store that is
    its stream's scans, which the store counts for itself: a file-wide read
    of the polarity's scans can be one short of the axis - the file's first
    scan is left out where its TIC is an outlier among the file's scans and
    not among its own stream's, as a composite method's reagent scan is -
    and counted by that read, the stream's ions would average a quarter
    high. A pooled store's axis holds every scan of the file with no
    polarity on it, so its count is the polarity's scans as the caller read
    them, the same read the store was summed over.

    :param peak_data: A peak store, or a selection of its peaks; with
        ``scan_stream`` along ``time`` for a per-stream store
    :type peak_data: xr.Dataset
    :param polarity_scans: The polarity's scans as read file-wide, the count
        of a pooled store
    :type polarity_scans: np.ndarray
    :return: One count per peak, never below one
    :rtype: np.ndarray
    """
    streams = peak_store_streams(peak_data)
    if not streams:
        return np.full(peak_data.mz.size, max(len(polarity_scans), 1), dtype=int)
    scan_streams = np.asarray(peak_data.scan_stream.values, dtype=int)
    return _counts_per_peak(peak_data, scan_streams, len(streams))


def _counts_per_peak(
    peak_data: xr.Dataset, scan_streams: np.ndarray, n_streams: int
) -> np.ndarray:
    """The scans of each peak's stream among ``scan_streams``, a floor of one."""
    stream = np.asarray(peak_data.stream.values, dtype=int)
    counts = np.bincount(
        scan_streams, minlength=max(n_streams, int(stream.max(initial=-1)) + 1)
    )
    per_peak = counts[stream]
    return np.where(per_peak > 0, per_peak, 1)


def peak_store_stitch_map(peak_data: xr.Dataset) -> dict | None:
    """The stitch map a peak store's composites are read by, or None.

    A per-stream store says which of its peaks make up each polarity's
    composite: ``composite`` along ``mz``, and the map that decided it in its
    ``stitch_map`` attribute (:func:`mascope_signal.stitch.stitch_map`). A
    pooled store has no streams to stitch, and answers None.

    The detection that labels a store's streams draws its map, so a
    per-stream store without one was not written by it, and one that lost
    its mask or its map on the way cannot say what its composites hold. Both
    are refused as stale: detecting the file's peaks again writes all of it.

    :param peak_data: A peak store, or a selection of its peaks or scans
    :type peak_data: xr.Dataset
    :raises ValueError: If the dataset carries only part of what a
        per-stream store does (:func:`peak_store_streams`)
    :raises StalePeakStoreError: If it is a per-stream store with no map or
        no mask
    :return: The map, or None for a pooled store
    :rtype: dict | None
    """
    if not peak_store_streams(peak_data):
        return None
    return _stitch_map_of(peak_data.attrs, "composite" in peak_data.variables)


def _stitch_map_of(attrs, has_composite: bool) -> dict:
    """The map of a per-stream store, from its attributes and whether it
    holds the ``composite`` mask; stale without either. Shared by
    :func:`peak_store_stitch_map` and :func:`peak_store_stitches`."""
    stitch = attrs.get(m_stitch.STITCH_MAP_ATTR)
    if not stitch or not has_composite:
        raise StalePeakStoreError(
            "The peak store holds a peak list per scan stream and no stitch "
            "map of them. Re-run peak detection for this sample file to "
            "rebuild the store."
        )
    return stitch


def peak_store_stitches(base_filename: str, polarity: Literal["+", "-"]) -> bool:
    """Whether a file's peak store stitches the polarity's scan streams.

    Read off the store's metadata alone - its attributes, and whether it
    holds the arrays a per-stream store does, each asked for by name, since
    listing the store's arrays reads the metadata of every array it holds -
    and not the dataset: what asks is deciding whether to open the dataset
    for a stitched read, and a file detected whole, which is nearly every
    file, must not pay for one it has nothing to stitch. The decision is the
    one :func:`peak_store_streams` and :func:`peak_store_stitch_map` make on
    the dataset, through the same code, and refuses what they refuse: a
    store carrying only part of what a per-stream store does, and a
    per-stream store with no map or no mask.

    :param base_filename: Sample file filename
    :type base_filename: str
    :param polarity: The polarity asked about
    :type polarity: str
    :raises FileNotFoundError: If the file has no peak store
    :raises ValueError: If the store carries only part of what a per-stream
        store does
    :raises StalePeakStoreError: If it is a per-stream store with no map or
        no mask
    :return: True where the store's map stitches the polarity
    :rtype: bool
    """
    _keys, stitch = _peak_store_metadata(base_filename)
    return bool(stitch and stitch["runs"].get(polarity))


def _peak_store_metadata(base_filename: str) -> tuple[list[str], dict | None]:
    """A peak store's stream keys and stitch map, off its metadata alone.

    What :func:`peak_store_stitches` decides from, for whoever needs the keys
    as well: the attributes, and whether the store holds the arrays a
    per-stream store does, each asked for by name.

    :param base_filename: Sample file filename
    :type base_filename: str
    :raises FileNotFoundError: If the file has no peak store
    :raises ValueError: If the store carries only part of what a per-stream
        store does
    :raises StalePeakStoreError: If it is a per-stream store with no map or
        no mask
    :return: The stream keys in the order the store's labels index them, and
        the stitch map; ``[]`` and None for a pooled store
    :rtype: tuple[list[str], dict | None]
    """
    path = m_name.filename_to_zarr_path(base_filename, "peak_timeseries")
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    # A membership test on the group opened here reads the live store, as a
    # listing does, and not consolidated metadata that may be stale
    group = m_io.open_zarr_store(path)
    attrs = dict(group.attrs)
    keys = _stream_keys_of(
        attrs.get("streams"), "stream" in group, "scan_stream" in group
    )
    if not keys:
        return [], None
    return keys, _stitch_map_of(attrs, "composite" in group)


def peak_store_composite(
    base_filename: str, polarity: Literal["+", "-"]
) -> dict | None:
    """What a polarity's composite is made of, for whoever shows it.

    The store's stream keys and the polarity's runs under its stitch map,
    read off the store's metadata as :func:`peak_store_stitches` reads it,
    with the m/z calibration factor each stream carries
    (``mascope_signal.mz_factor``): the runs are in m/z as the instrument
    recorded them, and a run is placed on the file's own axis by the factor
    of the stream that owns it (``mascope_signal.stitch.owners``).

    :param base_filename: Sample file filename
    :type base_filename: str
    :param polarity: The polarity asked about
    :type polarity: str
    :raises ValueError: If the store carries only part of what a per-stream
        store does
    :raises StalePeakStoreError: If it is a per-stream store with no map or
        no mask
    :return: None where the file has no store or its store stitches nothing
        of the polarity; else ``{"keys", "runs", "source", "factors"}``:
        the store's stream keys, the polarity's runs ``[lower, upper, stream
        index]`` in m/z order, whether the rule or a layout drew them, and
        the factor of each stream, in the order of the keys
    :rtype: dict | None
    """
    try:
        keys, stitch = _peak_store_metadata(base_filename)
    except FileNotFoundError:
        return None
    runs = stitch["runs"].get(polarity) if stitch else None
    if not runs:
        return None
    calibration = m_io.read_props(base_filename)["mz_calibration"]
    return {
        "keys": keys,
        "runs": [list(run) for run in runs],
        "source": (stitch.get("sources") or {}).get(polarity),
        "factors": m_factor.stream_factors(calibration, keys).tolist(),
    }


def get_scan_timestamps(
    base_filename: str,
    t_min: float | None = None,
    t_max: float | None = None,
    polarity: Literal["+", "-"] | None = None,
    stream: str | None = None,
) -> np.ndarray:
    """
    Retrieve scan timestamps from a given file based on its type.

    :param base_filename: Sample file name.
    :type base_filename: str
    :param t_min: Minimum time [s], optional, defaults to None
    :type t_min: float
    :param t_max: Maximum time [s], optional, defaults to None
    :type t_max: float
    :param stream: Key of the scan stream to read, for a raw Orbitrap file,
        defaults to None (every stream)
    :type stream: str, optional
    :return: An array of scan timestamps extracted from the sample file.
    """
    sample_type = m_name.get_sample_file_type(base_filename)
    _refuse_stream_unless_raw_orbitrap(sample_type, stream)
    match sample_type:
        case "orbi_raw":
            datafile_path = os.path.join(
                m_name.parse_path_from_item_filename(base_filename), "data.raw"
            )
            return m_thermo.get_scan_timestamps(
                datafile_path, t_min, t_max, polarity, stream=stream
            )
        case "tof_h5":
            datafile_path = os.path.join(
                m_name.parse_path_from_item_filename(base_filename), "data.h5"
            )
            return m_tofwerk.get_scan_timestamps(datafile_path, t_min, t_max)
        case "tof_zarr" | "orbi_zarr":
            signal_path = m_name.filename_to_zarr_path(base_filename, "signal")

            z = m_io.open_zarr_store(signal_path)
            time_array = z["time"][:]
            if not time_array.size:
                # Perhaps the coordinate is hiding in groups
                groups = list(z.group_keys())
                # Load time coordinate from each group and concatenate
                time_arrays = [z[group]["time"][:] for group in groups]
                time_array = np.concatenate(time_arrays)

            # Filter by time if t_min and/or t_max are provided
            # Using epsilon to avoid floating point precision issues
            if time_array.size:
                epsilon = np.finfo(np.float64).eps * max(time_array)
                if t_min is not None:
                    time_array = time_array[time_array >= t_min - epsilon]
                if t_max is not None:
                    time_array = time_array[time_array <= t_max + epsilon]

            return time_array
        case _:
            raise NotImplementedError(f"Unsupported sample type: {sample_type}")


def _get_averaging_factor(
    base_filename: str,
    sample_type: str,
    t_min: float | None,
    t_max: float | None,
    polarity: Literal["+", "-"] | None,
    stream: str | None = None,
) -> int:
    """Get deterministic averaging factor for average=True paths."""
    if sample_type in ("tof_zarr", "orbi_zarr"):
        signal = load_signal(base_filename)
        closest_t_min = (
            signal.time.sel(time=t_min, method="nearest").compute().item()
            if t_min is not None
            else signal.time.min().compute().item()
        )
        closest_t_max = (
            signal.time.sel(time=t_max, method="nearest").compute().item()
            if t_max is not None
            else signal.time.max().compute().item()
        )
        signal_slice = signal.sel(time=slice(closest_t_min, closest_t_max))
        return signal_slice.sizes["time"]

    time_coord = get_scan_timestamps(
        base_filename, t_min, t_max, polarity, stream=stream
    )
    return time_coord.size


def get_sum_signal(
    base_filename: str,
    t_min: float | None = None,
    t_max: float | None = None,
    polarity: Literal["+", "-"] | None = None,
    average: bool = False,
    stream: str | None = None,
) -> xr.DataArray:
    """Get sum signal from the sample file for the given time range, polarity
    and scan stream.

    The signal is the measured one, for display as for computation: the
    spectrum endpoints return it as it is, and the instrument-function fit
    reads its peak shapes from it.

    A cached signal is answered before the reader is asked, and a stream's
    signal is cached under its key. So this read, alone among those that
    take a stream, can answer under a key the file no longer holds a stream
    under, with what that key named when the signal was cached, where the
    others hand on the reader's ``UnknownStreamError``. Averaged it does
    refuse, because the scans are counted first. Whoever reads by a key it
    has stored has to ask the reader for the key before it trusts the
    cache, as :func:`get_composite_sum_signal` does.

    :param base_filename: Sample file filename
    :type base_filename: str
    :param t_min: Min time value [s], defaults to None
    :type t_min: float, optional
    :param t_max: Max time value [s], defaults to None
    :type t_max: float, optional
    :param polarity: Polarity of the scan to extract, defaults to None (compute all scans)
    :type polarity: str, optional
    :param average: Whether to return the average signal
    :type average: bool, optional
    :param stream: Key of the scan stream to sum, for a raw Orbitrap file,
        defaults to None (every stream). A stream's signal is cached under a
        name of its own.
    :type stream: str, optional
    :raises RuntimeError: If the sample file is not found or inaccessible
    :raises ValueError: If a stream is asked of a type that has none
    :return: The sum signal as an xarray DataArray
    :rtype: xr.DataArray
    """

    sample_type = m_name.get_sample_file_type(base_filename)
    _refuse_stream_unless_raw_orbitrap(sample_type, stream)
    cached_name = _get_sum_signal_hash_name(
        t_min, t_max, polarity, sample_type, stream=stream
    )
    averaging_factor = None
    if average:
        averaging_factor = _get_averaging_factor(
            base_filename,
            sample_type,
            t_min,
            t_max,
            polarity,
            stream=stream,
        )

    try:
        sum_signal = _get_cached_sum_signal(base_filename, cached_name)
        if average:
            return sum_signal / averaging_factor
        return sum_signal
    except FileNotFoundError:
        # case where file doesn't exist in filestore; no log here - the
        # raised RuntimeError is logged by the caller's handler
        raise RuntimeError(f"Sample file not found or inaccessible: {base_filename}")
    except (KeyError, AttributeError):
        # proceed if sample_file/dataset exists but is missing target sum_signal
        runtime.logger.debug(
            f"No cached sum signal found for {base_filename} with parameters: "
            f"t_min={t_min}, t_max={t_max}, polarity={polarity}, stream={stream}, "
            f"average={average} Computing sum signal..."
        )

    sample_path = m_name.parse_path_from_item_filename(base_filename)
    match sample_type:
        case "orbi_raw":
            datafile_path = os.path.join(sample_path, "data.raw")
            sum_signal, _ = m_thermo.compute_sum_signal(
                datafile_path,
                t_min=t_min,
                t_max=t_max,
                polarity=polarity,
                stream=stream,
            )
        case "tof_h5":
            datafile_path = os.path.join(sample_path, "data.h5")
            sum_signal, _ = m_tofwerk.compute_sum_signal(
                datafile_path,
                t_min=t_min,
                t_max=t_max,
            )
        case "tof_zarr" | "orbi_zarr":
            # Load the 'signal' data for specific time range
            signal = load_signal(base_filename)

            # Find the closest time points in the data to the provided time range
            closest_t_min = (
                signal.time.sel(time=t_min, method="nearest").compute().item()
                if t_min is not None
                else signal.time.min()
            )
            closest_t_max = (
                signal.time.sel(time=t_max, method="nearest").compute().item()
                if t_max is not None
                else signal.time.max()
            )

            # Slice the dataset for the time range
            signal_slice = signal.sel(time=slice(closest_t_min, closest_t_max))

            # Interpolate missing values
            signal_slice = signal_slice.interpolate_na(dim="mz", method="linear")
            # Fill the remaining nan values with zeros if any
            signal_slice = signal_slice.fillna(0)

            sum_signal_dask = da.from_array(
                signal_slice.sum(dim="time").signal.values, chunks="auto"
            )

            sum_signal = xr.DataArray(
                data=sum_signal_dask,
                dims=["mz"],
                coords={"mz": signal_slice.mz},
                name="sum_signal",
            )

    # A sum signal is put on the file's calibrated m/z axis: the one its peaks
    # are on, and its stored sum signals are moved to when a calibration is
    # applied. How it gets there depends on what it was summed from. A TOF
    # file's full signal is the exception, being the reference itself: apply
    # writes the calibrated axis into its store and the filtered ones take it
    # from there, so one summed afresh from the data file keeps that file's axis.
    is_full_sum_signal = t_min is None and t_max is None and polarity is None
    match sample_type:
        case "orbi_raw":
            # Averaged from the raw file, on the acquisition axis, while
            # applying a calibration rescales the stored sum signals in place
            # (OrbiCalibrationHandler) to the acquisition axis times the
            # current factor. So the factor goes on - the full signal's too:
            # one averaged after the file was calibrated, as every one is once
            # a new reader renames the cache, has to start where they are.
            # A stream's signal goes by the stream's own factor, as an apply
            # moves it by.
            calibration = m_io.read_props(base_filename)["mz_calibration"]
            if calibration:
                factor = m_factor.stream_factor(calibration, stream)
                sum_signal = sum_signal.assign_coords(mz=sum_signal.mz.values * factor)
        case "orbi_zarr":
            # Summed from the stored signal, which a calibration rescales in
            # place as well: on the calibrated axis already, full or filtered,
            # so the factor must not go on a second time.
            pass
        case "tof_h5" | "tof_zarr" if not is_full_sum_signal:
            # A filtered signal takes its axis from the full one, which carries
            # the calibration.
            calibration = m_io.read_props(base_filename)["mz_calibration"]
            if calibration:
                full_sum_signal = get_sum_signal(base_filename)
                full_sum_signal_mz = full_sum_signal.mz.values
                if full_sum_signal_mz.size != sum_signal.mz.size:
                    # Reverse compatibility correction on m/z axis
                    # Leave only sum_signal.mz.size last values in full_sum_signal_mz
                    full_sum_signal_mz = full_sum_signal_mz[-sum_signal.mz.size :]
                sum_signal = sum_signal.assign_coords(mz=full_sum_signal_mz)

    if stream is not None:
        # A cached signal is named by a hash, and an m/z calibration moves a
        # stream's signals by that stream's factor: it has to be able to
        # tell whose a signal is.
        sum_signal.attrs[CACHED_STREAM_ATTR] = stream

    # Save the computed sum signal to the sample file for future use
    concurrent_sum_signal = _write_cached_sum_signal(
        base_filename,
        cached_name,
        sum_signal,
    )
    if concurrent_sum_signal is not None:
        sum_signal = concurrent_sum_signal

    if average:
        if averaging_factor is None:
            raise RuntimeError("Averaging factor was not initialized")
        return sum_signal / averaging_factor

    return sum_signal


def _get_sum_signal_hash_name(t_min, t_max, polarity, sample_type, stream=None):
    """Generate a unique hash name for sum signal based on parameters

    The stream joins the name only when one is asked for, so every signal
    cached before a stream could be read apart keeps the name it was cached
    under.
    """
    is_full_sum_signal = (
        t_min is None and t_max is None and polarity is None and stream is None
    )
    if is_full_sum_signal:
        cached_name = "sum_signal"
    else:
        key = [t_min, t_max, polarity]
        if stream is not None:
            key.append(stream)
        key_str = json.dumps(key)
        hash_addition = hashlib.sha1(key_str.encode()).hexdigest()[:12]
        cached_name = f"sum_signal_{hash_addition}"

    return cached_name + sum_signal_suffix(sample_type)


def sum_signal_suffix(sample_type: str) -> str:
    """The suffix a cached sum signal of ``sample_type`` is named with.

    A raw Orbitrap file's profile is averaged from the raw file on demand, so
    its cache names what averaged it - the reader and the averaging, e.g.
    ``.otf2.0.0-g5`` (``averaged_profile_signature``). A profile cached by
    another reader or averaging is then never read back, where it would be
    served, to the spectrum views and to the instrument-function fit alike, as
    if this one had computed it. The other sample types carry no suffix.

    :param sample_type: The sample file type, e.g. ``"orbi_raw"``
    :type sample_type: str
    :return: The suffix, or an empty string
    :rtype: str
    """
    if sample_type == "orbi_raw":
        return f".{averaged_profile_signature()}"
    return ""


def _get_cached_sum_signal(base_filename, cached_name):
    """Helper function to load cached sum signal from the sample file if it exists"""
    sample_file = m_io.load_file(base_filename, vars=[cached_name])
    sum_signal = sample_file.sum_signal
    return sum_signal


def _try_get_cached_sum_signal(
    base_filename: str,
    cached_name: str,
) -> xr.DataArray | None:
    """Try to load cached sum signal, return None if it doesn't exist or
    is inaccessible due to concurrent write."""
    with suppress(FileNotFoundError, KeyError, AttributeError):
        return _get_cached_sum_signal(base_filename, cached_name)
    return None


def _write_cached_sum_signal(
    base_filename: str,
    cached_name: str,
    sum_signal: xr.DataArray,
) -> xr.DataArray | None:
    """Helper function to write the computed sum signal to the sample file with
    concurrency handling. If another process has already written the sum signal
    concurrently, it will load and return the existing cached sum signal.

    :param base_filename: Sample file filename
    :type base_filename: str
    :param cached_name: The name to use for caching the sum signal
    :type cached_name: str
    :param sum_signal: The computed sum signal to cache
    :type sum_signal: xr.DataArray
    :return: The cached sum signal if it was created concurrently, otherwise None
    :rtype: xr.DataArray | None
    """
    filename_sum_signal = m_name.filename_to_zarr_path(base_filename, cached_name)
    # DEBUG: purely informational cache write, fires on every cache miss
    runtime.logger.debug(f"Saving computed sum signal to {filename_sum_signal}")

    with m_io.zarr_write_lock(filename_sum_signal):
        cached_sum_signal = _try_get_cached_sum_signal(base_filename, cached_name)
        if cached_sum_signal is not None:
            # Check cache -> it's there -> return it instead of writing
            runtime.logger.debug(
                f"Using existing cached sum signal at {filename_sum_signal}"
            )
            return cached_sum_signal

        try:
            sum_signal.to_zarr(filename_sum_signal)
        except zarr.errors.ContainsGroupError:
            # Someone else created it just before/during open_group
            runtime.logger.debug(
                f"Sum signal cache was created concurrently at {filename_sum_signal}"
            )
            cached_sum_signal = _try_get_cached_sum_signal(base_filename, cached_name)
            if cached_sum_signal is not None:
                return cached_sum_signal
            raise
        except FileNotFoundError as fe:
            if ".partial" in str(fe):
                raise Exception(
                    f"The path is probably too long: {filename_sum_signal}"
                ) from fe
            raise

    return None


def get_composite_sum_signal(
    base_filename: str,
    polarity: Literal["+", "-"],
    t_min: float | None = None,
    t_max: float | None = None,
    average: bool = False,
) -> xr.DataArray:
    """Get the stitched sum signal of one polarity of a composite acquisition.

    A composite's spectrum is its streams' sum signals, each cut to the m/z
    the stream owns under the file's stitch map and laid end to end on one
    axis (``mascope_signal.stitch``). Every sample is labelled with the
    stream it came from, as ``segment``: an index into the store's stream
    keys, as a peak's ``stream`` is. Nothing is rescaled where two streams
    meet, so the signal can step at a boundary, and where the map has a gap
    the signal has no samples.

    The map is the peak store's, so the signal and the store's ``composite``
    mask cut the file in the same places. It is in m/z as the instrument
    recorded them, and a stream's signal is on the file's calibrated axis,
    so it is cut by the factor the stream carries
    (``mascope_signal.mz_factor``). Two streams calibrated apart can meet
    out of order at a boundary, by the difference of their factors: the
    later run then starts above the last sample of the one before, so that
    the axis only rises.

    Averaged, each sample is divided by the scans of its own stream inside
    the time range, since the streams of a composite hold different numbers
    of scans. A stream with no scan inside the range is left out, and the
    m/z it owns is a gap of that range's signal.

    The reader is asked for every stream before a cached signal is served,
    this one or a stream's own. A store names its streams by key, a key can
    stop naming anything, and a cached signal answers under its key without
    the file being read (:func:`get_sum_signal`). The stitched signal is
    cached beside the streams', under a name that carries the runs it was
    cut by and the keys they index, so a store rebuilt to another map is
    not handed a signal stitched for the last - and, where the file's
    streams are calibrated apart, the factors the runs were placed by, so
    neither is a file calibrated again.

    :param base_filename: Sample file filename
    :type base_filename: str
    :param polarity: The polarity whose composite to read
    :type polarity: str
    :param t_min: Min time value [s], defaults to None
    :type t_min: float, optional
    :param t_max: Max time value [s], defaults to None
    :type t_max: float, optional
    :param average: Whether to return the average signal
    :type average: bool, optional
    :raises FileNotFoundError: If the sample file has no peak store
    :raises ValueError: If its store stitches no streams of that polarity:
        a pooled store, or a polarity with a single stream, whose signal is
        that stream's own
    :raises StalePeakStoreError: If the file holds no stream under a key the
        store lists, or the store carries no map
    :raises mascope_thermo.thermo.NoScansFoundError: If no stream of the
        composite has a scan inside the time range
    :return: The stitched sum signal, with ``segment`` along ``mz``
    :rtype: xr.DataArray
    """
    stored = m_io.load_array(base_filename, var="peak_timeseries")
    stitch = peak_store_stitch_map(stored)
    runs = stitch["runs"].get(polarity) if stitch else None
    if not runs:
        raise ValueError(
            f"'{base_filename}' holds no composite of polarity '{polarity}': "
            "its peak store stitches no scan streams of that polarity."
        )
    keys = peak_store_streams(stored)

    scans = {
        index: _scans_of_stream(base_filename, keys[index], t_min, t_max)
        for index in sorted({index for _lower, _upper, index in runs})
    }
    if not any(scans.values()):
        raise m_thermo.NoScansFoundError(
            f"No scans found for the composite of polarity '{polarity}' of "
            f"'{base_filename}' (t_min={t_min}, t_max={t_max})."
        )

    calibration = m_io.read_props(base_filename)["mz_calibration"]
    factors = m_factor.stream_factors(calibration, keys)
    cached_name = _composite_sum_signal_name(
        t_min,
        t_max,
        polarity,
        m_name.get_sample_file_type(base_filename),
        runs,
        keys,
        factors if (calibration or {}).get(m_factor.STREAM_FACTORS) else None,
    )
    stitched = _try_get_cached_sum_signal(base_filename, cached_name)
    if stitched is None:
        stitched = _stitch_sum_signals(
            base_filename, t_min, t_max, runs, keys, scans, factors
        )
        concurrent = _write_cached_sum_signal(base_filename, cached_name, stitched)
        if concurrent is not None:
            stitched = concurrent

    if average:
        scans_of = np.zeros(len(keys))
        for index, count in scans.items():
            scans_of[index] = count
        return stitched / scans_of[stitched.segment.values]
    return stitched


def get_sample_sum_signal(
    base_filename: str,
    polarity: Literal["+", "-"],
    t_min: float | None = None,
    t_max: float | None = None,
    average: bool = False,
) -> xr.DataArray:
    """Get the sum signal of one polarity of a file, as its sample reads it.

    A file whose peak store stitches the polarity's scan streams into a
    composite answers the stitched signal (:func:`get_composite_sum_signal`):
    each stream's signal within the m/z it owns and, averaged, each divided
    by that stream's own scans - the scans the sample's peak list divides by
    (:func:`stored_scans_per_peak`), so that a listed peak sits on the
    profile it was detected in. Any other file answers the polarity's pooled
    signal (:func:`get_sum_signal`), averaged over every scan of the
    polarity, as its peaks are: a store detected whole, a polarity with a
    single stream, a file with no peak store yet.

    Which of the two is read off the store's metadata
    (:func:`peak_store_stitches`), so a file detected whole does not open the
    dataset, and whatever the stitched read then raises is raised: nothing
    is answered pooled for want of a stitch the store has. A store carrying
    only part of what a per-stream store does is refused as the peak list
    refuses it, a per-stream store the file no longer reads back is refused
    (:class:`StalePeakStoreError`) - the pooled profile would sit under
    peaks that were detected elsewhere - and a time range no stream has a
    scan in is refused as any read of it is.

    :param base_filename: Sample file filename
    :type base_filename: str
    :param polarity: The polarity whose signal to read
    :type polarity: str
    :param t_min: Min time value [s], defaults to None
    :type t_min: float, optional
    :param t_max: Max time value [s], defaults to None
    :type t_max: float, optional
    :param average: Whether to return the average signal
    :type average: bool, optional
    :raises StalePeakStoreError: If a per-stream store no longer reads back
    :return: The polarity's sum signal; with ``segment`` along ``mz`` where
        it is stitched
    :rtype: xr.DataArray
    """
    try:
        stitched = peak_store_stitches(base_filename, polarity)
    except FileNotFoundError:
        stitched = False  # no store yet: the polarity's pooled signal
    if stitched:
        return get_composite_sum_signal(
            base_filename, polarity, t_min, t_max, average=average
        )
    return get_sum_signal(
        base_filename, t_min, t_max, polarity=polarity, average=average
    )


def _composite_sum_signal_name(
    t_min: float | None,
    t_max: float | None,
    polarity: str,
    sample_type: str,
    runs: list,
    keys: list[str],
    factors: np.ndarray | None = None,
) -> str:
    """The name a polarity's stitched sum signal is cached under.

    It is a sum signal like the others of its file, named the same way and
    with the same suffix (:func:`_get_sum_signal_hash_name`), so that
    whatever moves or removes a file's cached sum signals takes it along. It
    is the signal of one map of one set of streams, so the name carries the
    runs and the keys they index.

    :param factors: The m/z calibration factor of each stream, by stream
        index, for a file whose streams are calibrated apart; None for one
        calibrated by a single factor, whose signal keeps the name it had
        before streams were
    :return: The name, with the suffix of ``sample_type``
    :rtype: str
    """
    members = {index: keys[index] for index in sorted({run[2] for run in runs})}
    key = [t_min, t_max, polarity, "composite", runs, members]
    if factors is not None:
        key.append({index: float(factors[index]) for index in members})
    key_str = json.dumps(key)
    hash_addition = hashlib.sha1(key_str.encode()).hexdigest()[:12]
    return f"sum_signal_{hash_addition}" + sum_signal_suffix(sample_type)


def _scans_of_stream(
    base_filename: str, key: str, t_min: float | None, t_max: float | None
) -> int:
    """How many scans of one stream of a per-stream store a time range holds.

    Asked of the reader, which is what makes it the place a stored key is
    found to name nothing any more.

    :raises StalePeakStoreError: If the file holds no stream under the key
    :return: The number of scans, zero where the range holds none
    :rtype: int
    """
    try:
        return get_scan_timestamps(base_filename, t_min, t_max, stream=key).size
    except m_thermo.UnknownStreamError as error:
        raise _stale_stream_key(key) from error
    except m_thermo.NoScansFoundError:
        return 0


def _stitch_sum_signals(
    base_filename: str,
    t_min: float | None,
    t_max: float | None,
    runs: list,
    keys: list[str],
    scans: dict[int, int],
    factors: np.ndarray,
) -> xr.DataArray:
    """The sum signals of a composite's streams, cut by its runs and joined.

    :param runs: The polarity's runs ``[lower, upper, stream index]``, in
        m/z order
    :param keys: The store's stream keys, as the runs index them
    :param scans: How many scans of each stream the time range holds
    :param factors: The m/z calibration factor of each stream, by stream
        index
    :return: The stitched signal, ``segment`` naming each sample's stream
    :rtype: xr.DataArray
    """
    signals: dict[int, xr.DataArray] = {}
    parts = []
    last = None
    for lower, upper, index in runs:
        if not scans[index]:
            continue
        if index not in signals:
            signals[index] = get_sum_signal(
                base_filename, t_min, t_max, stream=keys[index]
            )
        signal = signals[index]
        mz = signal.mz.values
        owned = m_stitch.owned_slice(mz, lower, upper, float(factors[index]))
        start = owned.start
        if last is not None:
            # Past the run before, which another factor can have put above
            # this one's first samples
            start = max(start, int(np.searchsorted(mz, last, side="right")))
        part = signal.isel(mz=slice(start, max(start, owned.stop)))
        if part.mz.size:
            last = float(part.mz.values[-1])
        parts.append(
            part.assign_coords(
                segment=("mz", np.full(part.mz.size, index, dtype=np.int16))
            )
        )
    # One chunk, as a stream's own signal is: the pieces come in the sizes of
    # the runs, and a zarr array is not stored in chunks of unequal size
    return xr.concat(parts, dim="mz").chunk({"mz": -1})


def load_signal(
    base_filename: str,
    t_min: float | None = None,
    t_max: float | None = None,
    mz_min: float | None = None,
    mz_max: float | None = None,
    polarity: Literal["+", "-"] | None = None,
    stream: str | None = None,
) -> xr.Dataset:
    """Load signal from the sample file

    Supports m/z and time slicing.

    :param base_filename: Sample file filename
    :type base_filename: str
    :param t_min: Min time value [s], defaults to None
    :type t_min: float, optional
    :param t_max: Max time value [s], defaults to None
    :type t_max: float, optional
    :param mz_min: Min m/z value, defaults to None
    :type mz_min: float, optional
    :param mz_max: Max m/z value, defaults to None
    :type mz_max: float, optional
    :param polarity: Polarity of the scan to extract, defaults to None (get all scans)
    :type polarity: str, optional
    :param stream: Key of the scan stream to load, for a raw Orbitrap file,
        defaults to None (every stream)
    :type stream: str, optional
    :raises ValueError: If a stream is asked of a type that has none
    :raises mascope_thermo.thermo.UnknownStreamError: If the file holds no
        stream under the key asked for
    :return: The signal with m/z and time coordinates
    :rtype: xr.Dataset
    """
    runtime.logger.debug(f"Loading signal from {base_filename}")

    sample_type = m_name.get_sample_file_type(base_filename)
    # Before the try below, which answers a ValueError with an empty signal:
    # a stream asked of a file that has none is the caller's mistake, not an
    # empty range.
    _refuse_stream_unless_raw_orbitrap(sample_type, stream)
    sample_path = m_name.parse_path_from_item_filename(base_filename)

    if not os.path.exists(sample_path):
        raise FileNotFoundError(sample_path)

    try:
        match sample_type:
            case "orbi_raw":
                datafile_path = os.path.join(sample_path, "data.raw")
                signal = m_thermo.get_signal(
                    datafile_path, t_min, t_max, mz_min, mz_max, polarity, stream=stream
                )
                # Handle m/z axis calibration
                props = m_io.read_props(base_filename)
                calibration = props["mz_calibration"]
                if calibration:
                    fit_parameters = calibration["par"]
                    factor = fit_parameters["calibration_factor"]
                    signal = signal.assign_coords(mz=signal.mz.values * factor)
                return signal
            case "tof_h5":
                datafile_path = os.path.join(sample_path, "data.h5")
                signal = m_tofwerk.get_signal(datafile_path, t_min, t_max)

                # Handle m/z axis calibration, sum_signal m/z values are calibrated
                sum_signal_mz = get_sum_signal(base_filename).mz.values
                mzs_are_equal = np.array_equal(signal.mz.values, sum_signal_mz)
                if not mzs_are_equal:
                    signal = signal.assign_coords(mz=sum_signal_mz)

                signal = signal.sel(mz=slice(mz_min, mz_max))
                return signal
            case "tof_zarr" | "orbi_zarr":
                signal_ds = m_io.load_array(base_filename, "signal")
                signal_ds = signal_ds.chunk(dict(mz=-1, time=-1))
                if sample_type == "tof_zarr":
                    # Correct by scan duration
                    interval_ds = m_io.load_array(base_filename, "signal_period")
                    signal_ds = signal_ds / interval_ds.signal_period

                # Check time range
                t_min = signal_ds.time.min() if t_min is None else t_min
                t_max = signal_ds.time.max() if t_max is None else t_max
                if t_min > t_max:
                    raise ValueError(f"Invalid time range: {t_min} > {t_max}")

                # Check m/z range
                mz_min = signal_ds.mz.min() if mz_min is None else mz_min
                mz_max = signal_ds.mz.max() if mz_max is None else mz_max
                if mz_min > mz_max:
                    raise ValueError(f"Invalid m/z range: {mz_min} > {mz_max}")

                signal_ds_sliced = signal_ds.sel(
                    time=slice(t_min, t_max), mz=slice(mz_min, mz_max)
                )
                # Check if sliced signal contains data
                if not signal_ds_sliced.signal.size:
                    raise ValueError(
                        f"""No data found in the specified time or m/z range.
                M/z range of the sample file: {signal_ds.mz.min():.1f} - {signal_ds.mz.max():.1f}
                Time range: {signal_ds.time.min():.1f} - {signal_ds.mz.max():.1f} s.
                """
                    )
                return signal_ds_sliced.chunk(dict(mz=-1))
            case _:
                raise NotImplementedError(f"Unsupported sample type: {sample_type}")
    except m_thermo.UnknownStreamError:
        # Not an empty range either. The file holds no stream under the key
        # asked for, and answered with an empty signal the caller would read
        # "no data" of a stream that is not there.
        raise
    except Exception as e:
        # Both paths return an empty dataset (callers render "no data"), but
        # expected data conditions (empty range, unsupported type) must not
        # look like faults, and real faults must carry their traceback.
        if isinstance(e, (ValueError, NotImplementedError)):
            runtime.logger.info(f"No signal loaded from {base_filename}: {e}")
        else:
            runtime.logger.exception(f"Error loading signal from {base_filename}")
        # Return empty signal dataset with "mz" and "time" coordinates in case of error
        return xr.Dataset(
            {
                "signal": (["mz", "time"], np.zeros((0, 0))),
                "mz": (["mz"], np.zeros(0)),
                "time": (["time"], np.zeros(0)),
            }
        )


def get_tic_per_scan(
    base_filename: str,
    timestamps: Iterable | None = None,
    polarity: Literal["+", "-"] | None = None,
    stream: str | None = None,
) -> tuple:
    """Get TIC per scan from the sample file depending on the file type

    :param base_filename: Sample file filename
    :type base_filename: str
    :param timestamps: Optional timestamps of the scans, defaults to None
    :type timestamps: Iterable | None
    :param polarity: Polarity of the scan to extract, defaults to None (get all scans)
    :type polarity: str | None
    :param stream: Key of the scan stream to read, for a raw Orbitrap file,
        defaults to None (every stream)
    :type stream: str | None
    :raises ValueError: If a stream is asked of a type that has none
    :return: TIC time and TIC per scan as numpy arrays
    :rtype: tuple
    """
    sample_type = m_name.get_sample_file_type(base_filename)
    _refuse_stream_unless_raw_orbitrap(sample_type, stream)
    match sample_type:
        case "orbi_raw":
            datafile_path = m_name.filename_to_datafile_path(base_filename)
            tic_time, tic_per_scan = m_thermo.get_tic_per_scan(
                datafile_path, timestamps, polarity, stream=stream
            )
        case "tof_h5":
            datafile_path = m_name.filename_to_datafile_path(base_filename)
            tic_time, tic_per_scan = m_tofwerk.get_tic_per_scan(
                datafile_path, timestamps
            )
        case "tof_zarr" | "orbi_zarr":
            zarr_path = m_name.filename_to_zarr_path(base_filename, "signal")
            z = m_io.open_zarr_store(zarr_path)

            # Get sum of counts along mz coordinate for each time coordinate
            signal_array = da.from_zarr(z["signal"])
            tic_per_scan = signal_array.sum(axis=0).compute()
            # Check if TIC values are available
            if not tic_per_scan.size:
                # Get list of groups in zarr file
                groups = list(z.group_keys())

                # Load signal to dask arrays for each group
                signal_arrays = [da.from_zarr(z[group]["signal"]) for group in groups]
                # Sum signal arrays along mz coordinate
                group_tic_per_scan = [
                    da.nan_to_num(array, 0.0).sum(axis=0).compute()
                    for array in signal_arrays
                ]
                # Concatenate TIC values from each group
                tic_per_scan = np.concatenate(group_tic_per_scan, axis=0)

            # Correct TIC values by total TIC value if available
            try:
                total_tic = m_io.read_props(base_filename)["tic"]
                tic_per_scan = tic_per_scan / tic_per_scan.sum() * total_tic
            except KeyError:
                # Expected for files whose props carry no "tic" entry
                runtime.logger.info(
                    "Total TIC value is not available in the sample file"
                )

            # Get time coordinate as numpy array
            tic_time = m_io.load_coord(base_filename, "signal", "time")

            if timestamps:
                # Filter TIC values by timestamps
                timestamps = np.asarray(timestamps)
                # Find closest scan index for each timestamp
                scan_indices = np.searchsorted(tic_time, timestamps)
                # Ensure indices are within valid range
                scan_indices = np.clip(scan_indices, 0, len(tic_time) - 1)
                # Extract scan TIC and scan timestamps values for the closest scan index
                tic_per_scan = tic_per_scan[scan_indices]
                tic_time = tic_time[scan_indices]

    return tic_time, tic_per_scan


def get_sample_tic_per_scan(
    base_filename: str, polarity: Literal["+", "-"]
) -> tuple[np.ndarray, np.ndarray]:
    """TIC per scan of one polarity of a file, as its sample reads it.

    A file whose peak store stitches the polarity's scan streams answers the
    scans of those streams, each as its own stream selects them: the scans
    the store's axis holds for the polarity, which are the scans the
    sample's peaks were detected and are filled over. Read polarity-wide
    instead, the file's first scan is judged against every other scan of the
    file, and a composite file that opens with a reagent scan loses it - a
    scan its own stream keeps.

    Any other file answers the polarity's scans as :func:`get_tic_per_scan`
    reads them: a store detected whole, a polarity with a single stream, a
    file with no peak store yet. Which of the two is read off the store's
    metadata, as :func:`get_sample_sum_signal` decides it.

    :param base_filename: Sample file filename
    :type base_filename: str
    :param polarity: The polarity whose scans to read
    :type polarity: str
    :raises ValueError: If the store carries only part of what a per-stream
        store does
    :raises StalePeakStoreError: If a per-stream store carries no map, or the
        file holds no stream under a key it lists
    :raises mascope_thermo.thermo.NoScansFoundError: If the file holds no
        scan of the polarity
    :return: Scan times [s] and the TIC of each, in time order
    :rtype: tuple[np.ndarray, np.ndarray]
    """
    try:
        keys, stitch = _peak_store_metadata(base_filename)
    except FileNotFoundError:
        keys, stitch = [], None  # no store yet: the polarity's own scans
    if not (stitch and stitch["runs"].get(polarity)):
        return get_tic_per_scan(base_filename, polarity=polarity)
    return _tic_of_streams(base_filename, keys, polarity)


def get_stored_tic_per_scan(base_filename: str) -> tuple[np.ndarray, np.ndarray]:
    """TIC of each scan a file's peak store holds on its time axis.

    For whatever pairs the store's scans with a fresh read of the file by
    position. A store detected whole holds every MS1 scan of the file, as
    :func:`get_tic_per_scan` reads them. A per-stream store holds each
    stream's scans as that stream selects them, which a file-wide read does
    not give back wherever the first-scan rule judges the two differently:
    paired with it, a sound store is refused as stale, and no rebuild
    answers that.

    :param base_filename: Sample file filename
    :type base_filename: str
    :raises ValueError: If the store carries only part of what a per-stream
        store does
    :raises StalePeakStoreError: If a per-stream store carries no map, or the
        file holds no stream under a key it lists
    :return: Scan times [s] and the TIC of each, in time order
    :rtype: tuple[np.ndarray, np.ndarray]
    """
    try:
        keys, _stitch = _peak_store_metadata(base_filename)
    except FileNotFoundError:
        keys = []
    if not keys:
        return get_tic_per_scan(base_filename)
    return _tic_of_streams(base_filename, keys)


def _tic_of_streams(
    base_filename: str, keys: list[str], polarity: Literal["+", "-"] | None = None
) -> tuple[np.ndarray, np.ndarray]:
    """The scans of several streams of a per-stream store, with their TIC.

    Each stream is read under its own key, and the scans joined in time
    order, as the store's axis was built. With a polarity, a stream of the
    other one holds no scan to add and is passed over.

    :param keys: Stream keys of the file's peak store
    :param polarity: Keep only the scans of this polarity, defaults to None
    :raises StalePeakStoreError: If the file holds no stream under a key
    :raises mascope_thermo.thermo.NoScansFoundError: If none of the streams
        holds a scan, of the polarity where one is given
    :return: Scan times [s] and the TIC of each, in time order
    :rtype: tuple[np.ndarray, np.ndarray]
    """
    times, tics = [], []
    for key in keys:
        try:
            stream_time, stream_tic = get_tic_per_scan(
                base_filename, polarity=polarity, stream=key
            )
        except m_thermo.UnknownStreamError as error:
            raise _stale_stream_key(key) from error
        except m_thermo.NoScansFoundError:
            continue
        times.append(np.asarray(stream_time, dtype=float))
        tics.append(np.asarray(stream_tic, dtype=float))
    if not times:
        raise m_thermo.NoScansFoundError(
            f"No scans found in the scan streams of '{base_filename}'"
            + ("." if polarity is None else f" for polarity '{polarity}'.")
        )
    scan_time, scan_tic = np.concatenate(times), np.concatenate(tics)
    order = np.argsort(scan_time, kind="stable")
    return scan_time[order], scan_tic[order]


def get_acquisition_window(
    base_filename: str,
    polarity: Literal["+", "-"] | None = None,
    stream: str | None = None,
) -> tuple[float, float]:
    """First and last scan time [s] of an acquisition, across every scan type.

    This is the window a sample item covers, and it deliberately does not come
    from the TIC: :func:`get_tic_per_scan` reports MS1 scans, so on a file whose
    MS2 scans are recorded as their own block -- a manual MS2 acquisition
    records MS1 first and the fragmentation afterwards, rather than interleaving
    them the way data-dependent acquisition does -- an MS1-derived window ends
    before the first MS2 scan and excludes all of them. Spanning every scan type
    is identical for interleaved files, where MS1 survey scans already bracket
    the run.

    :param base_filename: Sample file name (base, not full path).
    :type base_filename: str
    :param polarity: Polarity of the scans to span ('+' or '-'), optional.
    :type polarity: Literal['+', '-'] | None, optional
    :param stream: Key of the scan stream to span, for a raw Orbitrap file,
        optional. The window is then the stream's own first and last scan:
        a stream is the scans of one experiment, so nothing of another scan
        type is among them to widen it.
    :type stream: str | None, optional
    :return: (t0, t1) in seconds.
    :rtype: tuple[float, float]
    :raises ValueError: When the file reports no scan times to span, or a
        stream is asked of a type that has none.
    """
    sample_type = m_name.get_sample_file_type(base_filename)
    _refuse_stream_unless_raw_orbitrap(sample_type, stream)
    match sample_type:
        case "orbi_raw":
            datafile_path = m_name.filename_to_datafile_path(base_filename)
            times = m_thermo.get_scan_timestamps(
                datafile_path, polarity=polarity, scan_type=None, stream=stream
            )
        case _:
            # No other reader has an MS2 scan type to leave out, so the scan
            # time axis is the acquisition window.
            times = get_scan_timestamps(base_filename, polarity=polarity)

    # The raw readers raise rather than hand back an empty selection, but the
    # zarr time axis can come back empty; say what is wrong rather than let
    # numpy report a zero-size reduction.
    if not len(times):
        raise ValueError(
            f"No scan times to span for '{base_filename}' (polarity={polarity!r}"
            + ("" if stream is None else f", stream={stream!r}")
            + ")."
        )

    return float(np.min(times)), float(np.max(times))


async def get_orbi_centroids(
    base_filename: str,
    u_list: Iterable[float] | None = None,
    t_min: float | None = None,
    t_max: float | None = None,
    polarity: Literal["+", "-"] | None = None,
    ppm: int = 1,
    average: bool = False,
    stream: str | None = None,
) -> tuple:
    """
    Extract centroided peaks from an Orbitrap (Thermo .raw) file for specified m/z values and time range.

    This function determines the sample type and, if the sample file contains the Orbitrap raw file,
    extracts centroided peaks whose m/z values are within +/-0.5 of any value in `u_list` and within the specified
    time range and polarity. Returns the filtered centroid m/z values, their intensities, and resolutions.

    :param base_filename: Sample file name (base, not full path).
    :type base_filename: str
    :param u_list: Iterable of m/z values to select centroid peaks near (within +/-0.5), defaults to None.
    :type u_list: Iterable[float]
    :param t_min: Minimum time [s] for scan selection, optional, defaults to None (start of run).
    :type t_min: float | None, optional
    :param t_max: Maximum time [s] for scan selection, optional, defaults to None (end of run).
    :type t_max: float | None, optional
    :param polarity: Polarity of scans to use ('+' or '-'), optional, defaults to None (all polarities).
    :type polarity: Literal['+', '-'], optional
    :param ppm: Mass tolerance in ppm for centroid binning, defaults to 1.
    :type ppm: int, optional
    :param average: If True, return averaged intensities across scans, defaults to False.
    :type average: bool, optional
    :param stream: Key of the scan stream to average, defaults to None (every stream).
    :type stream: str, optional
    :return: Tuple of (masses, intensities, resolutions, signal-to-noise) for centroid peaks
    matching the criteria.
    :rtype: tuple
    """
    sample_type = m_name.get_sample_file_type(base_filename)

    match sample_type:
        case "orbi_raw":
            datafile_path = m_name.filename_to_datafile_path(base_filename)
            masses, intensities, resolutions, signal_to_noise = await asyncio.to_thread(
                m_thermo.get_centroids,
                datafile_path,
                t_min=t_min,
                t_max=t_max,
                polarity=polarity,
                ppm=ppm,
                average=average,
                stream=stream,
            )
            props = m_io.read_props(base_filename)
            calibration = props["mz_calibration"]
            if calibration:
                masses = masses * m_factor.stream_factor(calibration, stream)
            if u_list:
                # Create a mask for the masses that are within 0.5 of any value in u_list
                mz_mask = np.zeros_like(masses, dtype=bool)
                for mz in u_list:
                    mz_mask |= (masses >= mz - 0.5) & (masses <= mz + 0.5)
                masses = masses[mz_mask]
                intensities = intensities[mz_mask]
                resolutions = resolutions[mz_mask]
                signal_to_noise = signal_to_noise[mz_mask]
        case _:
            raise NotImplementedError(
                "Centroid extraction is only implemented for Orbitrap raw files."
            )
    return masses, intensities, resolutions, signal_to_noise


def get_orbi_centroids_per_scan(
    base_filename: str,
    t_min: float | None = None,
    t_max: float | None = None,
    polarity: Literal["+", "-"] | None = None,
    scan_type: Literal["Ms", "Ms2"] | None = None,
    stream: str | None = None,
) -> list:
    """
    Extract per-scan centroids from an Orbitrap raw file

    :param base_filename: Sample file name (base, not full path).
    :type base_filename: str
    :param t_min: Minimum time [s] for scan selection, optional, defaults to None (start of run).
    :type t_min: float | None, optional
    :param t_max: Maximum time [s] for scan selection, optional, defaults to None (end of run).
    :type t_max: float | None, optional
    :param polarity: Polarity of scans to use ('+' or '-'), optional, defaults to None (all polarities).
    :type polarity: Literal['+', '-'], optional
    :param scan_type: Filter by scan type ('Ms' or 'Ms2'), optional, defaults to None (all scans).
    :type scan_type: Literal['Ms', 'Ms2'] | None, optional
    :param stream: Key of the scan stream to read, optional, defaults to None (every stream).
    :type stream: str | None, optional
    :return: List of dictionaries with per-scan centroid data, each containing
            centroid masses, intensities, resolutions, signal-to-noise ratios, and timestamps.
    :rtype: list
    """
    sample_type = m_name.get_sample_file_type(base_filename)
    match sample_type:
        case "orbi_raw":
            datafile_path = m_name.filename_to_datafile_path(base_filename)
            centroids_per_scan = m_thermo.get_centroids_per_scan(
                datafile_path,
                t_min,
                t_max,
                polarity=polarity,
                scan_type=scan_type,
                stream=stream,
            )
            props = m_io.read_props(base_filename)
            calibration = props["mz_calibration"]
            if calibration:
                factor = m_factor.stream_factor(calibration, stream)
                for scan_centroids in centroids_per_scan:
                    scan_centroids["masses"] = scan_centroids["masses"] * factor

            return centroids_per_scan
        case _:
            raise NotImplementedError(
                "Per-scan centroid extraction is only for Orbitrap raw files."
            )


async def get_orbi_ms2_centroids_by_parent(
    base_filename: str,
    t_min: float | None = None,
    t_max: float | None = None,
    polarity: Literal["+", "-"] | None = None,
    mz_min: float | None = None,
    mz_max: float | None = None,
    parent_peak_tolerance: float = 0.001,
    ppm: int = 1,
    average: bool = True,
    by_activation: bool = True,
) -> dict[tuple[float, str], tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]]:
    """Extract averaged MS2 centroids per (parent peak, activation) group.

    Grouping includes the activation, so a stepped-energy acquisition returns
    one averaged spectrum per collision energy. With ``by_activation`` False
    each parent peak is one group instead, keyed with an empty activation.

    :param base_filename: Sample file name (base, not full path).
    :type base_filename: str
    :param t_min: Minimum time [s], optional.
    :type t_min: float | None, optional
    :param t_max: Maximum time [s], optional.
    :type t_max: float | None, optional
    :param polarity: Polarity filter ('+' or '-'), optional.
    :type polarity: Literal['+', '-'] | None, optional
    :param mz_min: Minimum parent peak m/z to include, optional.
    :type mz_min: float | None, optional
    :param mz_max: Maximum parent peak m/z to include, optional.
    :type mz_max: float | None, optional
    :param parent_peak_tolerance: Tolerance in Da for merging parent peaks.
    :type parent_peak_tolerance: float
    :param ppm: Mass tolerance in ppm for centroid binning, defaults to 1.
    :type ppm: int, optional
    :param average: If True, return averaged intensities, defaults to True.
    :type average: bool, optional
    :param by_activation: If True (the default), group by activation as well as
                          parent peak; if False, one group per parent peak.
    :type by_activation: bool, optional
    :return: Mapping of (parent peak m/z, activation) to
             (masses, intensities, resolutions, signal_to_noise).
    :rtype: dict[tuple[float, str], tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]]
    """
    sample_type = m_name.get_sample_file_type(base_filename)
    match sample_type:
        case "orbi_raw":
            datafile_path = m_name.filename_to_datafile_path(base_filename)
            mapped_ms2_centroids = await asyncio.to_thread(
                m_thermo.get_ms2_centroids_by_parent,
                datafile_path,
                t_min=t_min,
                t_max=t_max,
                polarity=polarity,
                mz_min=mz_min,
                mz_max=mz_max,
                parent_peak_tolerance=parent_peak_tolerance,
                ppm=ppm,
                average=average,
                by_activation=by_activation,
            )
            props = m_io.read_props(base_filename)
            calibration = props["mz_calibration"]
            factor = calibration["par"]["calibration_factor"] if calibration else None

            if factor is not None:
                mapped_ms2_centroids = {
                    key: (masses * factor, intensities, resolutions, signal_to_noise)
                    for key, (
                        masses,
                        intensities,
                        resolutions,
                        signal_to_noise,
                    ) in mapped_ms2_centroids.items()
                }
            return mapped_ms2_centroids
        case _:
            raise NotImplementedError(
                "MS2 centroid extraction is only implemented for Orbitrap raw files."
            )


async def get_ms2_summary(
    base_filename: str,
    t_min: float | None = None,
    t_max: float | None = None,
    polarity: Literal["+", "-"] | None = None,
    parent_peak_tolerance: float = 0.001,
) -> dict:
    """Extract MS2 summary metadata.

    Returns parent peaks, HCD energy map, isolation width, and scan counts.

    :param base_filename: Sample file name (base, not full path).
    :type base_filename: str
    :param t_min: Minimum time [s], optional.
    :type t_min: float | None, optional
    :param t_max: Maximum time [s], optional.
    :type t_max: float | None, optional
    :param polarity: Polarity filter ('+' or '-'), optional.
    :type polarity: Literal['+', '-'] | None, optional
    :param parent_peak_tolerance: Tolerance in Da for merging parent peaks.
    :type parent_peak_tolerance: float
    :return: Dictionary with MS2 summary data.
    :rtype: dict
    """
    sample_type = m_name.get_sample_file_type(base_filename)
    match sample_type:
        case "orbi_raw":
            datafile_path = m_name.filename_to_datafile_path(base_filename)
            return await asyncio.to_thread(
                m_thermo.get_ms2_summary_metadata,
                datafile_path,
                t_min=t_min,
                t_max=t_max,
                polarity=polarity,
                parent_peak_tolerance=parent_peak_tolerance,
            )
        case _:
            raise NotImplementedError(
                "MS2 summary extraction is only implemented for Orbitrap raw files."
            )


async def get_ms2_fragment_timeseries(
    base_filename: str,
    parent_peak_mz: float,
    t_min: float | None = None,
    t_max: float | None = None,
    polarity: Literal["+", "-"] | None = None,
    noise_threshold: float = 10.0,
    parent_peak_tolerance: float = 0.001,
    normalize_by: Literal["tic"] | None = None,
    activation: str | None = None,
) -> dict:
    """Compute fragment timeseries for a single MS2 parent peak.

    Extracts per-scan centroids for MS2 scans matching the parent peak,
    applies noise filtering, builds timeseries via peak clustering, and
    optionally normalizes by TIC.

    :param base_filename: Sample file name (base, not full path).
    :type base_filename: str
    :param parent_peak_mz: The parent peak m/z to get timeseries for.
    :type parent_peak_mz: float
    :param t_min: Minimum time [s], optional.
    :type t_min: float | None, optional
    :param t_max: Maximum time [s], optional.
    :type t_max: float | None, optional
    :param polarity: Polarity filter ('+' or '-'), optional.
    :type polarity: Literal['+', '-'] | None, optional
    :param noise_threshold: Minimum signal-to-noise ratio threshold.
    :type noise_threshold: float
    :param parent_peak_tolerance: Tolerance in Da for matching parent peaks.
    :type parent_peak_tolerance: float
    :param normalize_by: Normalization mode. ``"tic"`` normalizes by scan TIC,
        ``None`` returns raw intensities.
    :type normalize_by: Literal["tic"] | None, optional
    :param activation: Restrict to one activation (e.g. ``"hcd40.00"``),
        optional; defaults to every activation of the parent peak, which is what
        makes the fragment timeseries of a stepped-energy run show the steps.
    :type activation: str | None, optional
    :return: Dictionary with mz_values, time, and values arrays.
    :rtype: dict
    """

    sample_type = m_name.get_sample_file_type(base_filename)
    match sample_type:
        case "orbi_raw":
            datafile_path = m_name.filename_to_datafile_path(base_filename)
            centroids, tic_values = await asyncio.to_thread(
                m_thermo.get_ms2_centroids_per_scan_for_parent,
                datafile_path,
                parent_peak_mz,
                t_min=t_min,
                t_max=t_max,
                polarity=polarity,
                parent_peak_tolerance=parent_peak_tolerance,
                activation=activation,
            )
        case _:
            raise NotImplementedError(
                "MS2 timeseries extraction is only for Orbitrap raw files."
            )

    if not centroids:
        return {"mz_values": [], "time": [], "values": []}

    # Apply calibration factor to fragment masses
    props = m_io.read_props(base_filename)
    calibration = props["mz_calibration"]
    factor = calibration["par"]["calibration_factor"] if calibration else None

    # Apply noise filtering and calibration, build Spectra
    spectra_list = []
    spectra_times = []
    for scan in centroids:
        mask = scan["signal_to_noise"] >= noise_threshold
        mzs = scan["masses"][mask]
        intensities = scan["intensities"][mask]
        sn = scan["signal_to_noise"][mask]
        res = scan["resolutions"][mask]
        if factor is not None:
            mzs = mzs * factor
        if len(mzs) == 0:
            continue
        spectra_list.append(
            CentroidedSpectrum(
                mz=mzs,
                intensity=intensities,
                signal_to_noise=sn,
                resolution=res,
            )
        )
        spectra_times.append(scan["timestamp"])

    if not spectra_list:
        return {"mz_values": [], "time": [], "values": []}

    spectra_obj = Spectra(spectra_list, np.array(spectra_times))
    frag_ts = spectra_obj.get_timeseries()

    if frag_ts.empty:
        return {"mz_values": [], "time": [], "values": []}

    # TIC normalization
    if normalize_by == "tic":
        # Build TIC series aligned to spectra timestamps (only for non-empty scans)
        filtered_tic_times = []
        filtered_tic_vals = []
        for scan, tic in zip(centroids, tic_values):
            mask = scan["signal_to_noise"] >= noise_threshold
            if scan["masses"][mask].size > 0:
                filtered_tic_times.append(scan["timestamp"])
                filtered_tic_vals.append(tic)

        tic_series = pd.Series(
            filtered_tic_vals,
            index=pd.to_datetime(filtered_tic_times, unit="s"),
        )
        aligned = tic_series.reindex(
            frag_ts.columns, method="nearest", tolerance=pd.Timedelta("2s")
        )
        frag_ts = frag_ts.div(aligned, axis=1)

    # Serialize
    time_values = [
        t.isoformat() if hasattr(t, "isoformat") else float(t) for t in frag_ts.columns
    ]
    return {
        "mz_values": [float(m) for m in frag_ts.index],
        "time": time_values,
        "values": frag_ts.values.tolist(),
    }


def _load_deduplicated_peak_data(base_filename: str, mzs_arr: np.ndarray) -> xr.Dataset:
    """Load the peak dataset for the requested m/z values, without duplicates.

    Synchronous and blocking (it opens the zarr store), so callers on the event
    loop must hand it to a worker thread.

    :param base_filename: Sample file filename
    :param mzs_arr: Sorted unique target m/z values
    :return: Peak dataset selected to the nearest m/z, duplicates dropped
    """
    # The whole store: a fill answers any stored row by its m/z, and what
    # asks for one holds its m/z already - a reading the composite leaves out
    # included, where the overlap is read
    peak_timeseries = m_io.load_peak_data(base_filename, composite=False).sel(
        mz=mzs_arr, method="nearest"
    )
    _, unique_idx = np.unique(peak_timeseries.mz.values, return_index=True)
    return peak_timeseries.isel(mz=np.sort(unique_idx))


class StalePeakStoreError(ValueError):
    """A peak store whose scan axis the sample file no longer reads back.

    A store is allocated against the scans of the acquisition and keeps that
    axis for life, while the scans a reader selects are decided anew on every
    read - a file whose first scan reads as a TIC outlier loses it, and that
    rule has not always existed. A store written before it therefore describes
    one more scan than the file now yields.

    Re-running peak detection is the only thing that repairs such a store, and
    it repairs it completely. Nothing downstream can: the store's per-peak
    sums were measured over the scans it was allocated with, so they already
    carry the intensity of the scan the reader now discards. Spreading a
    recomputed timeseries over the scans that remain would move that intensity
    onto them - the artifact the exclusion exists to drop would be smeared
    across the good scans instead of sitting in its own, where it can still be
    seen and skipped.

    A store that holds a peak list per scan stream is read back stream by
    stream, each under the key it was built with, and a key is a name the
    reader computes. One the file no longer holds a stream under is the same
    condition by another road: that stream's peaks have no scans left to be
    read back over, and a rebuild, which keys the file's streams afresh,
    repairs it. So is a per-stream store that carries no stitch map of its
    streams (:func:`peak_store_stitch_map`): the rebuild draws one.

    A ``ValueError`` so the API layer keeps mapping it to a client-class
    failure with its own message rather than a generic 500.
    """


def check_stored_scan_axis(
    live_time: np.ndarray,
    stored_time: np.ndarray,
) -> None:
    """Refuse a peak store the sample file no longer reads back scan for scan.

    Public because the disagreement is a property of the file, not of one
    caller: anything that pairs a store's scan axis with values from a fresh
    read (the per-scan peak export among them) has to answer it the same way.

    :param live_time: Scan timestamps [s] just read from the sample file
    :type live_time: np.ndarray
    :param stored_time: Scan timestamps [s] the peak store was allocated with
    :type stored_time: np.ndarray
    :raises StalePeakStoreError: If the two axes describe different scans
    :return: None
    """
    live = np.asarray(live_time, dtype=float)
    stored = np.asarray(stored_time, dtype=float)

    # The ordinary case, and the only one held to no further standard: the
    # store is being read back exactly as it was written, so an axis that is
    # degenerate by the rules below (a repeated timestamp, say) still reads
    # the way it always has.
    if live.shape == stored.shape and np.array_equal(live, stored):
        return

    def _stale(reason: str) -> StalePeakStoreError:
        return StalePeakStoreError(
            f"The peak store holds {stored.size} scan(s) and the sample file "
            f"now reads back {live.size} ({reason}). Re-run peak detection "
            "for this sample file to rebuild the store."
        )

    if not (live.size and stored.size):
        raise _stale("one of them holds no scans")
    if live.size != stored.size:
        raise _stale("their scan counts differ")
    # Every comparison below is against a timestamp, and a comparison with NaN
    # is False - so a single non-finite scan time would pass each check rather
    # than fail it, and a store nobody can vouch for would be accepted.
    if not (np.isfinite(live).all() and np.isfinite(stored).all()):
        raise _stale("some of the scan times are not finite")

    # Same count, so only a real difference in the timestamps is a mismatch -
    # not the last bit of a float that went to disk and came back. Half the
    # tightest stored spacing is the widest a scan may be off by and still be
    # unambiguously its own; a one-scan store has no spacing to halve, so it
    # falls back to a float-noise bound. Both matter here: declaring a healthy
    # store stale would queue peak detection for it on every read.
    gaps = np.diff(stored)
    noise = 8 * np.finfo(float).eps * max(np.abs(stored).max(), np.abs(live).max(), 1.0)
    tolerance = max(0.5 * gaps.min(), noise) if gaps.size else noise
    if np.any(np.abs(live - stored) > tolerance):
        raise _stale("their scan times do not line up")


async def _read_stream_back(
    base_filename: str, mzs: np.ndarray, key: str
) -> xr.DataArray:
    """One stream of a per-stream store, read back from the file by its key.

    The store names its streams by the keys they had when it was built. What
    the reader calls an experiment depends on what else its file holds, on
    the backend that rendered its scan filter and on the version of the code
    that keys, so a stored key can stop naming anything. The reader refuses
    such a key, and here that is no one's mistake: the store is stale, and is
    said to be, so that whoever meets it asks for the rebuild.

    :param base_filename: Sample file filename
    :type base_filename: str
    :param mzs: The m/z values of the stream's peaks to read back
    :type mzs: np.ndarray
    :param key: The stream's key, as the store lists it
    :type key: str
    :raises StalePeakStoreError: If the file holds no stream under the key
    :return: The peaks' timeseries over the scans of that stream
    :rtype: xr.DataArray
    """
    try:
        return await get_peak_timeseries(base_filename, mzs, stream=key)
    except m_thermo.UnknownStreamError as error:
        raise _stale_stream_key(key) from error


def _stale_stream_key(key: str) -> StalePeakStoreError:
    """What a stream key of a per-stream store that names nothing is raised as."""
    return StalePeakStoreError(
        f"The peak store holds the peaks of scan stream '{key}', and the "
        "sample file now reads back no stream under that key. Re-run peak "
        "detection for this sample file to rebuild the store."
    )


async def check_peak_store(base_filename: str) -> None:
    """Refuse a peak store the sample file does not read back, as matching would.

    Peak detection is what repairs a stale store, so a store it has just
    written is expected to pass - and one that does not is a fault rather than
    the routine after-an-upgrade state: matching its samples fails again, and
    the refresh that meets it queues the same rebuild again. Asked right after
    a rebuild, it is the one place that can tell the two apart.

    The file is read back for one m/z, the way ``load_peak_timeseries`` reads
    it, since the scan axis does not depend on the peak. A per-stream store
    is read back once per stream, each against its own scans of the store's
    axis.

    :param base_filename: Sample file filename
    :type base_filename: str
    :raises StalePeakStoreError: If the store's scan axis is not the file's,
        or the file holds no stream under a key the store lists
    :return: None
    """
    # The whole store: every stream it holds is read back, the ones the
    # composite leaves out included, so a stale key is named whichever it is
    stored = await asyncio.to_thread(
        m_io.load_peak_data, base_filename, composite=False
    )
    if not stored.mz.size:
        # A blank measurement's store holds no peak to read the file back for
        return
    streams = peak_store_streams(stored)
    if streams:
        peak_stream = stored.stream.values
        scan_stream = stored.scan_stream.values
        for index, key in enumerate(streams):
            peaks = np.flatnonzero(peak_stream == index)
            if not peaks.size:
                # A stream that kept no peak has no m/z to read it back for
                continue
            live = await _read_stream_back(
                base_filename, stored.mz.values[peaks[:1]], key
            )
            check_stored_scan_axis(
                live.time.values, stored.time.values[scan_stream == index]
            )
        return
    live = await get_peak_timeseries(base_filename, stored.mz.values[:1])
    check_stored_scan_axis(live.time.values, stored.time.values)


async def load_peak_timeseries(
    base_filename: str,
    mzs: list[float],
) -> xr.Dataset:
    """Loads peak timeseries from the sample file.
    Computes missing peak timeseries if needed.

    Computing them means reading the file back, which takes long enough for
    the store's m/z axis to be rewritten meanwhile: an m/z calibration of the
    file rescales it, and nothing keeps the two apart. The store then refuses
    the fill, whose m/z values are those of the axis as it was
    (``mascope_file.io.MzNotOnAxisError``), and the same peaks are loaded and
    computed once more, on the store as it has become. They are found there
    by their peak ids (:func:`_mzs_of_the_same_peaks`), so what is returned
    is on the store's new axis. A second refusal is raised.

    :param base_filename: Sample file filename
    :type base_filename: str
    :param mzs: List of target m/z values
    :type mzs: list[float]
    :raises MzNotOnAxisError: If the store's m/z axis was rewritten during
        both attempts
    :return: The peak timeseries dataset
    :rtype: xr.Dataset
    """
    mzs_arr = np.unique(np.asarray(mzs))
    peak_timeseries, to_compute_mask = await _load_peaks_to_fill(base_filename, mzs_arr)
    if not np.any(to_compute_mask):
        return peak_timeseries

    # What a second attempt finds these peaks by. Read before the file is,
    # while the store is still the one they were loaded from: once it has
    # been rewritten, this load no longer reads it back reliably.
    peak_ids = await asyncio.to_thread(lambda: peak_timeseries.peak_id.values)
    try:
        await _fill_peak_timeseries(base_filename, peak_timeseries, to_compute_mask)
    except m_io.MzNotOnAxisError:
        # A warning although the second attempt is expected to succeed:
        # nothing else says how often a fill meets a rewritten axis, and that
        # is what decides whether the two want keeping apart instead. Pinned
        # to one issue, since the refusal's message carries the m/z values
        # and monitoring would otherwise group it close to per file.
        runtime.logger.bind(
            **{SENTRY_FINGERPRINT: ["peak-fill-met-a-rewritten-axis"]}
        ).opt(exception=True).warning(
            f"The m/z axis of the peak store of '{base_filename}' was rewritten "
            "while peak timeseries were being computed for it. They were not "
            "stored, and are computed again."
        )
        # A rewritten axis is not a finished calibration: the Orbitrap one
        # records its factor after the axis, and the file is read back by
        # that factor. This is the one fill known to start right behind an
        # apply, so it alone waits for it.
        await asyncio.to_thread(_wait_for_mz_calibration, base_filename)
        mzs_arr = await asyncio.to_thread(
            _mzs_of_the_same_peaks, base_filename, peak_ids, mzs_arr
        )
        peak_timeseries, to_compute_mask = await _load_peaks_to_fill(
            base_filename, mzs_arr
        )
        if np.any(to_compute_mask):
            await _fill_peak_timeseries(base_filename, peak_timeseries, to_compute_mask)

    # --- Return a clean lazy reference ---
    return await asyncio.to_thread(_load_deduplicated_peak_data, base_filename, mzs_arr)


def _wait_for_mz_calibration(base_filename: str) -> None:
    """Return once no m/z calibration of the file is being applied.

    An apply holds the file's calibration lock from its first write to its
    last (``mascope_file.io.mz_calibration_lock_path``). Taking the lock and
    letting go of it at once is waiting for an apply in progress to finish,
    and no wait at all where there is none.

    Synchronous and blocking, so callers on the event loop must hand it to a
    worker thread.

    :param base_filename: Sample file filename
    :type base_filename: str
    :raises TimeoutError: If another process held the lock for longer than
        ``mascope_file.io.ZARR_PROCESS_LOCK_TIMEOUT``
    :return: None
    """
    with m_io.zarr_write_lock(m_io.mz_calibration_lock_path(base_filename)):
        pass


def _mzs_of_the_same_peaks(
    base_filename: str,
    peak_ids: np.ndarray,
    mzs_arr: np.ndarray,
) -> np.ndarray:
    """Where the peaks of a refused fill are on the store's axis as it now is.

    The m/z values a fill is asked for are, for most callers, read off the
    store: labels of the axis as it was. On a rewritten axis the row nearest
    such a label is its own peak only where the axis moved by less than half
    the distance to the next kept peak, and two labels nearer each other than
    that name one row, so the caller would get fewer peaks than it asked for.
    An m/z calibration rewrites the m/z values of a store and nothing else,
    so its peaks are found by their ids.

    Where an id is gone the file's peaks were detected again, and no peak of
    the store is one the fill was for. The m/z values asked for are then all
    there is to go by, for every peak: a new detection replaces every id.

    Synchronous and blocking (it opens the zarr store), so callers on the event
    loop must hand it to a worker thread.

    :param base_filename: Sample file filename
    :type base_filename: str
    :param peak_ids: The ids of the peaks the refused fill had loaded
    :type peak_ids: np.ndarray
    :param mzs_arr: The m/z values it was asked for, sorted and unique
    :type mzs_arr: np.ndarray
    :return: The m/z values to ask for instead, sorted and unique
    :rtype: np.ndarray
    """
    # The whole axis: a refused fill may have loaded a reading the composite
    # leaves out
    stored = m_io.load_peak_data(base_filename, composite=False)
    mz_by_id = dict(zip(stored.peak_id.values.tolist(), stored.mz.values.tolist()))
    peak_ids = peak_ids.tolist()
    if not all(peak_id in mz_by_id for peak_id in peak_ids):
        return mzs_arr
    return np.unique([mz_by_id[peak_id] for peak_id in peak_ids])


async def _load_peaks_to_fill(
    base_filename: str,
    mzs_arr: np.ndarray,
) -> tuple[xr.Dataset, np.ndarray]:
    """Load the peaks asked for, and say which of them have no timeseries yet.

    :param base_filename: Sample file filename
    :type base_filename: str
    :param mzs_arr: Sorted unique target m/z values
    :type mzs_arr: np.ndarray
    :return: The peaks nearest those m/z values, and a mask over them of the
        ones whose timeseries is still to be computed
    :rtype: tuple[xr.Dataset, np.ndarray]
    """
    # --- Load existing peak timeseries from the sample file ---
    peak_timeseries = await asyncio.to_thread(
        _load_deduplicated_peak_data, base_filename, mzs_arr
    )

    runtime.logger.debug(
        f"Loading peak timeseries for {peak_timeseries.mz.size} m/z values from {base_filename}"
    )

    is_computed = await asyncio.to_thread(
        lambda: peak_timeseries.is_timeseries_computed.values
    )
    to_compute_mask = np.invert(is_computed)

    if not np.any(to_compute_mask):
        runtime.logger.debug(
            f"All peak timeseries are cached in {base_filename}, loading from file."
        )
    return peak_timeseries, to_compute_mask


async def _fill_peak_timeseries(
    base_filename: str,
    peak_timeseries: xr.Dataset,
    to_compute_mask: np.ndarray,
) -> None:
    """Compute the timeseries the loaded peaks are missing, and store them.

    :param base_filename: Sample file filename
    :type base_filename: str
    :param peak_timeseries: The peaks asked for, loaded from the store
    :type peak_timeseries: xr.Dataset
    :param to_compute_mask: Which of them have no timeseries yet
    :type to_compute_mask: np.ndarray
    :raises MzNotOnAxisError: If the store's m/z axis was rewritten between
        the load of the peaks and the write
    :return: None
    """
    # --- A per-stream store fills each peak over its own stream's scans ---
    streams = peak_store_streams(peak_timeseries)
    if streams:
        update_dataset = await _stream_timeseries_update(
            base_filename, peak_timeseries, to_compute_mask, streams
        )
        await m_io.write_peaks(update_dataset, base_filename)
        return

    # --- Compute the missing peak timeseries ---
    mz_coords = peak_timeseries.mz.values
    mzs_to_compute = mz_coords[to_compute_mask]

    # Load only the metadata we need (relatively small arrays)
    def _load_update_metadata():
        return (
            peak_timeseries.sum_peak_heights.sel(mz=mzs_to_compute).values,
            peak_timeseries.sum_peak_areas.sel(mz=mzs_to_compute).values,
            peak_timeseries.time.values,
        )

    sum_peak_heights, sum_peak_areas, time_coords = await asyncio.to_thread(
        _load_update_metadata
    )

    # Compute new timeseries (this is the heavy computation)
    new_peak_timeseries = await get_peak_timeseries(base_filename, mzs_to_compute)

    # The store is written scan-by-scan into a fixed-width axis, so the values
    # have to span exactly the scans it was allocated with. Checked here, on
    # the coordinates alone, so a store the file has outgrown is refused
    # before any chunk is read - and refused rather than worked around: only
    # peak detection can rebuild it, and the caller is expected to ask for
    # that on the user's behalf.
    check_stored_scan_axis(new_peak_timeseries.time.values, time_coords)

    # Normalize, restore intensities and build the update dataset. Kept in one
    # worker thread: new_peak_timeseries is dask-backed, so .values below is
    # where the chunks are actually read.
    def _build_update_dataset():
        timeseries_sum = new_peak_timeseries.sum(dim="time")
        timeseries_sum = xr.where(timeseries_sum == 0, 1, timeseries_sum)
        new_peak_timeseries_norm = (new_peak_timeseries / timeseries_sum).values

        # Restore peak timeseries intensities
        new_peak_areas = new_peak_timeseries_norm * sum_peak_areas[:, np.newaxis]
        new_peak_heights = new_peak_timeseries_norm * sum_peak_heights[:, np.newaxis]

        # Determine sparsity: fraction of scans where peak_heights are not
        # positive. NaN values count as missing (sparse) because NaN > 0 is False
        sparsity_values = (
            np.sum(~(new_peak_heights > 0), axis=1) / new_peak_heights.shape[1]
        )

        # This contains only the changed values, fully in memory
        return xr.Dataset(
            data_vars={
                "peak_areas": (["mz", "time"], new_peak_areas),
                "peak_heights": (["mz", "time"], new_peak_heights),
                "is_timeseries_computed": (
                    ["mz"],
                    np.ones(len(mzs_to_compute), dtype=bool),
                ),
                "sparsity": (["mz"], sparsity_values),
            },
            coords={
                "mz": mzs_to_compute,
                "time": time_coords,
            },
        )

    update_dataset = await asyncio.to_thread(_build_update_dataset)

    # --- Write the updates to disk ---
    await m_io.write_peaks(update_dataset, base_filename)


async def _stream_timeseries_update(
    base_filename: str,
    peak_timeseries: xr.Dataset,
    to_compute_mask: np.ndarray,
    streams: list[str],
) -> xr.Dataset:
    """The missing timeseries of a per-stream store, as an update to it.

    Each peak is read back over the scans of its own stream, normalised over
    them and scaled to its summed intensity, as a pooled store's peak is over
    every scan. On the other streams' scans it holds no value at all: the
    instrument was measuring something else then, which is not the same as
    measuring this ion and finding none. Sparsity is likewise the share of
    its own stream's scans the peak is absent from.

    The axis is checked per stream, on the coordinates alone, before any chunk
    is read: a store one of whose streams the file no longer reads back scan
    for scan is refused, as a pooled one is.

    :param base_filename: Sample file filename
    :type base_filename: str
    :param peak_timeseries: The peaks asked for, loaded from the store
    :type peak_timeseries: xr.Dataset
    :param to_compute_mask: Which of them have no timeseries yet
    :type to_compute_mask: np.ndarray
    :param streams: The store's stream keys, as its labels index them
    :type streams: list[str]
    :raises StalePeakStoreError: If a stream's scans are not the file's, or
        the file holds no stream under its key
    :return: The rows to write, on the store's whole time axis
    :rtype: xr.Dataset
    """

    def _load_update_metadata():
        missing = peak_timeseries.isel(mz=np.flatnonzero(to_compute_mask))
        return (
            missing.mz.values,
            missing.stream.values,
            missing.sum_peak_heights.values,
            missing.sum_peak_areas.values,
            peak_timeseries.time.values,
            peak_timeseries.scan_stream.values,
        )

    (
        mzs_to_compute,
        peak_stream,
        sum_peak_heights,
        sum_peak_areas,
        time_coords,
        scan_stream,
    ) = await asyncio.to_thread(_load_update_metadata)

    shape = (mzs_to_compute.size, time_coords.size)
    new_peak_areas = np.full(shape, np.nan, dtype=np.float64)
    new_peak_heights = np.full(shape, np.nan, dtype=np.float64)
    sparsity_values = np.zeros(mzs_to_compute.size, dtype=np.float64)

    for index in np.unique(peak_stream):
        peaks = np.flatnonzero(peak_stream == index)
        scans = np.flatnonzero(scan_stream == index)
        new_peak_timeseries = await _read_stream_back(
            base_filename, mzs_to_compute[peaks], streams[int(index)]
        )
        check_stored_scan_axis(new_peak_timeseries.time.values, time_coords[scans])

        # new_peak_timeseries is dask-backed, so .values is where the chunks
        # are read: off the event loop, like the pooled fill.
        def _normalised(timeseries=new_peak_timeseries):
            values = timeseries.values
            totals = values.sum(axis=1, keepdims=True)
            return values / np.where(totals == 0, 1, totals)

        normalised = await asyncio.to_thread(_normalised)
        heights = normalised * sum_peak_heights[peaks][:, np.newaxis]
        new_peak_areas[np.ix_(peaks, scans)] = (
            normalised * sum_peak_areas[peaks][:, np.newaxis]
        )
        new_peak_heights[np.ix_(peaks, scans)] = heights
        # NaN counts as missing, as in the pooled fill: NaN > 0 is False
        sparsity_values[peaks] = np.sum(~(heights > 0), axis=1) / scans.size

    return xr.Dataset(
        data_vars={
            "peak_areas": (["mz", "time"], new_peak_areas),
            "peak_heights": (["mz", "time"], new_peak_heights),
            "is_timeseries_computed": (
                ["mz"],
                np.ones(mzs_to_compute.size, dtype=bool),
            ),
            "sparsity": (["mz"], sparsity_values),
        },
        coords={
            "mz": mzs_to_compute,
            "time": time_coords,
        },
    )


async def get_peak_timeseries(
    base_filename: str,
    mzs: Iterable[float],
    t_min: float | None = None,
    t_max: float | None = None,
    polarity: Literal["+", "-"] | None = None,
    stream: str | None = None,
) -> xr.DataArray:
    """Get peak timeseries for given peak m/z values in the time range [t_min, t_max]

    :param base_filename: Sample file filename
    :type base_filename: str
    :param mzs: List of target m/z values
    :type mzs: Iterable[float]
    :param t_min: Left border of the time range [s], defaults to None
    :type t_min: float, optional
    :param t_max: Right border of the time range [s], defaults to None
    :type t_max: float, optional
    :param polarity: Polarity of the scan to extract, defaults to None (get all scans)
    :type polarity: str, optional
    :param stream: Key of the scan stream to read, for a raw Orbitrap file,
        defaults to None (every stream)
    :type stream: str, optional
    :raises ValueError: If a stream is asked of a type that has none
    :return: peak timeseries for the given m/z values
    :rtype: xr.DataArray
    """
    sample_type = m_name.get_sample_file_type(base_filename)
    _refuse_stream_unless_raw_orbitrap(sample_type, stream)
    match sample_type:
        case "orbi_raw":
            datafile_path = m_name.filename_to_datafile_path(base_filename)

            # Orbitrap raw files store raw data, mzs need to be uncalibrated
            # before extracting peak timeseries: by the factor of the stream
            # read, which is the one its peaks carry
            props = m_io.read_props(base_filename)
            calibration = props["mz_calibration"]
            factor = 1.0
            if calibration:
                factor = m_factor.stream_factor(calibration, stream)
            uncalibrated_mzs = np.array(mzs) / factor
            peak_timeseries = await asyncio.to_thread(
                m_thermo.get_peak_timeseries,
                datafile_path,
                uncalibrated_mzs,
                t_min,
                t_max,
                polarity,
                stream=stream,
            )
            # Calibrate m/z coordinate
            return peak_timeseries.assign_coords(mz=peak_timeseries.mz.values * factor)
        case "tof_h5":
            # Get calibrated m/z values
            sum_signal_mz = get_sum_signal(base_filename).mz.values
            datafile_path = m_name.filename_to_datafile_path(base_filename)
            return await asyncio.to_thread(
                m_tofwerk.get_peak_timeseries,
                datafile_path,
                mzs,
                sum_signal_mz,
                t_min,
                t_max,
            )
        case "tof_zarr" | "orbi_zarr":
            signal = load_signal(base_filename, t_min, t_max)
            # Interpolate missing values in mz dimension using linear method.
            signal = signal.interpolate_na(dim="mz", method="linear")
            # Fill the remaining nan values with zeros
            signal = signal.fillna(0)
            # Extract the peak timeseries for the closest m/z values
            return signal.sel(mz=mzs, method="nearest").signal
        case _:
            raise NotImplementedError(f"Unsupported sample type: {sample_type}")


def get_polarity_options(base_filename: str) -> str | None:
    """Reads the polarities present in a sample file.

    :param base_filename: Sample file filename
    :type base_filename: str
    :return: Polarity options as "-", "+", or "+-" depending on the data.
    :rtype: str
    """
    sample_type = m_name.get_sample_file_type(base_filename)
    datafile_path = m_name.parse_path_from_item_filename(base_filename)
    match sample_type:
        case "orbi_raw":
            datafile_path = os.path.join(datafile_path, "data.raw")
            return m_thermo.get_polarity_options(datafile_path)
        case "tof_h5":
            datafile_path = os.path.join(datafile_path, "data.h5")
            return m_tofwerk.get_polarity_options(datafile_path)
        case "tof_zarr" | "orbi_zarr":
            polarity = base_filename.split("_")[-1]
            if polarity in ["+", "-"]:
                return polarity
            else:
                # If polarity is not specified, return None (get all scans)
                return None
        case _:
            raise NotImplementedError(f"Unsupported sample type: {sample_type}")


def get_metadata(
    base_filename: str,
) -> m_thermo.RawFileMetadataLegacy | None:
    """Get metadata from the sample file

    #TODO_deprecation: This function is deprecated and should be removed in future versions.
    # Metadata retrieval should be done using the new mascope_signal.metadata module.

    :param base_filename: Sample file filename
    :type base_filename: str
    :return: Metadata class instance or None if the file type is not supported
    :rtype: RawFileMetadataLegacy | None
    """
    # DEBUG: fires on every metadata request; the migration away from this
    # helper is tracked at its call sites
    runtime.logger.debug(
        "Metadata retrieval using compute.get_metadata is deprecated and will be "
        "removed in future versions. "
        "Please use the new mascope_signal.metadata module."
    )
    sample_type = m_name.get_sample_file_type(base_filename)
    match sample_type:
        case "orbi_raw":
            datafile_path = m_name.filename_to_datafile_path(base_filename)
            return m_thermo.RawFileMetadataLegacy(datafile_path)
        case "tof_h5":
            raise NotImplementedError(
                "Metadata retrieval for h5 files is not implemented"
            )
        case "tof_zarr" | "orbi_zarr":
            raise NotImplementedError(
                "Metadata retrieval for zarr files is not implemented"
            )


def sum_peak_collection(
    peak_collection: Spectra,
) -> tuple[CentroidedSpectrum, float, float]:
    """Aligns and sums provided collection of peak arrays.

    :param peak_collection: Peak collection to align and sum
    :type peak_collection: Spectra
    :raises ValueError: If mass alignment fails
    :return: Tuple with summed aligned peaks, min aligned m/z, max aligned m/z
    :rtype: tuple[CentroidedSpectrum, float, float]
    """
    # Perform alignment using virtual lock mass algorithm
    aligned_spectra, vlm_mz_min, vlm_mz_max = align_peak_collection(peak_collection)

    aligned_peak_sum = aligned_spectra.compute_sum_spectrum(
        window_factor=AGGREGATION_WINDOW_FACTOR, average=True
    )

    return aligned_peak_sum, vlm_mz_min, vlm_mz_max


def align_peak_collection(
    peak_collection: Spectra,
) -> tuple[Spectra, float, float]:
    """Aligns provided collection of peak arrays.

    :param peak_collection: Peak collection to align
    :type peak_collection: Spectra
    :raises ValueError: If mass alignment fails
    :return: Tuple with aligned peaks, min aligned m/z, max aligned m/z
    :rtype: tuple[Spectra, float, float]
    """
    # Perform alignment using virtual lock mass algorithm
    vlm_corrector = MassAligner(
        min_peak_intensity=ALIGNMENT_MIN_INTENSITY,
        min_fraction=ALIGNMENT_MIN_FRACTION,
        window_factor=ALIGNMENT_WINDOW_FACTOR,
    )
    vlm_corrector.fit(peak_collection)
    aligned_peaks = vlm_corrector.transform(peak_collection)
    if vlm_corrector.points_mz is None or vlm_corrector.points_mz.size == 0:
        raise ValueError(
            "Mass alignment failed: no alignment points found. "
            "Check your filtering parameters and input data quality."
        )
    else:
        if vlm_corrector.points_mz.size < 2:
            raise ValueError(
                "Mass alignment failed: fewer than 2 alignment points found. "
                "Check your filtering parameters and input data quality."
            )

        # Min and max aligned m/z
        vlm_min_mz = vlm_corrector.points_mz.min()
        vlm_max_mz = vlm_corrector.points_mz.max()

        return aligned_peaks, vlm_min_mz, vlm_max_mz


# --- TODO Refactoring to split logic for different sample types ---
# This is a placeholder for future refactoring to improve maintainability
class OrbiRawComputer:
    pass


class TofH5Computer:
    pass


class TofZarrComputer:
    pass


class OrbiZarrComputer:
    pass


def compute_factory(base_filename: str):
    sample_type = m_name.get_sample_file_type(base_filename)
    match sample_type:
        case "orbi_raw":
            return OrbiRawComputer()
        case "tof_h5":
            return TofH5Computer()
        case "tof_zarr":
            return TofZarrComputer()
        case "orbi_zarr":
            return OrbiZarrComputer()
        case _:
            raise NotImplementedError(f"Unsupported sample type: {sample_type}")
