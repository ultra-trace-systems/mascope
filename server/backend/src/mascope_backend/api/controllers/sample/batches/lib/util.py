"""
Sample batch utility functions for batch operations and change detection.

This module contains helper functions for sample batch management operations,
including change detection, validation utilities, and data transformation
functions used across batch-related controllers.
"""

import asyncio
from typing import Literal

import numpy as np
from sqlalchemy import select

import mascope_file.io as m_io
import mascope_file.name as m_name
import mascope_signal.compute as m_compute
from mascope_backend.api.lib.exceptions.api_exceptions import (
    NotFoundException,
)
from mascope_backend.api.new.instrument_configs.lib import (
    read_instrument_functions,
)
from mascope_backend.db import Sample, async_session
from mascope_backend.runtime import runtime
from mascope_tools.alignment.calibration import CentroidedSpectrum


def detect_update_batch_changes(existing_batch, sample_batch_update) -> dict[str, bool]:
    """
    Detects changes between existing sample batch and update data.

    Compares current batch state with proposed updates to determine which
    fields have actually changed. This enables efficient updates by only
    modifying changed fields and triggering appropriate reload events.

    :param existing_batch: Current sample batch entity from database
    :type existing_batch: SampleBatch
    :param sample_batch_update: Pydantic model containing proposed update values
    :type sample_batch_update: SampleBatchUpdate
    :return: Dictionary mapping change types to boolean flags
    :rtype: dict[str, bool]
    """
    # Extract current state for comparison
    current_collections = {
        tc.target_collection_id for tc in existing_batch.target_collection
    }

    # Extract proposed new state using dot notation
    new_collections = set(sample_batch_update.target_collection_ids)

    # Calculate collection changes
    collections_to_add = new_collections - current_collections
    collections_to_remove = current_collections - new_collections
    collections_changed = len(collections_to_add) > 0 or len(collections_to_remove) > 0

    # Basic field changes
    name_changed = (
        sample_batch_update.sample_batch_name is not None
        and existing_batch.sample_batch_name != sample_batch_update.sample_batch_name
    )
    description_changed = (
        sample_batch_update.sample_batch_description is not None
        and existing_batch.sample_batch_description
        != sample_batch_update.sample_batch_description
    )

    runtime.logger.debug(
        "Detected sample batch changes:\n"
        f"  collections_changed: {collections_changed}\n"
        f"  collections_to_add: {list(collections_to_add)}\n"
        f"  collections_to_remove: {list(collections_to_remove)}\n"
        f"  name_changed: {name_changed}\n"
        f"  description_changed: {description_changed}"
    )

    return {
        "collections": collections_changed,
        "collections_to_add": collections_to_add,
        "collections_to_remove": collections_to_remove,
        "name": name_changed,
        "description": description_changed,
    }


def load_existing_batch_cache(sample_batch: dict) -> dict:
    """Helper to load existing batch cache"""
    sample_batch_id = sample_batch["sample_batch_id"]
    batch_peaks = m_io.load_batch_cache(sample_batch_id, "peaks")

    # --- Validate cache timestamps --- #
    old_timestamp = batch_peaks.attrs["sample_batch_utc_modified"]
    new_timestamp = str(sample_batch["sample_batch_utc_modified"])
    if old_timestamp != new_timestamp:
        m_io.delete_batch_cache(sample_batch_id)
        raise FileNotFoundError(
            f"Batch cache for sample batch ID '{sample_batch_id}' is outdated "
            "due to batch modification."
        )

    mz = batch_peaks.mz.values.tolist()
    intensity = batch_peaks.intensity.values.tolist()
    # Make sure peak ids list of lists are converted to lists
    peak_id = batch_peaks.peak_id.values.tolist()
    min_aligned_mz = float(batch_peaks.attrs["min_aligned_mz"])
    max_aligned_mz = float(batch_peaks.attrs["max_aligned_mz"])
    intensity_variable = batch_peaks.attrs["intensity_variable"]
    return {
        "data": {
            "mzs": mz,
            "intensities": intensity,
            "peak_ids": peak_id,
            "min_aligned_mz": min_aligned_mz,
            "max_aligned_mz": max_aligned_mz,
            "intensity_variable": intensity_variable,
        },
        "message": f"Retrieved aligned peak data for sample batch with ID '{sample_batch_id}' from batch cache.",
    }


async def collect_spectra_per_ionization_mode(
    sample_batch_id: str,
) -> tuple[dict[str, list[CentroidedSpectrum]], str]:
    """Collects CentroidedSpectrum objects for all samples in a sample batch, grouped by ionization mode.

    :param sample_batch_id: ID of the sample batch to collect spectra from.
    :type sample_batch_id: str
    :raises NotFoundException: if no samples are found in the specified sample batch.
    :return: Tuple of dictionary mapping ionization mode IDs to lists of CentroidedSpectrum objects
             and the intensity variable.
    :rtype: tuple[dict[str, list[CentroidedSpectrum]], str]
    """
    # --- Fetch Sample objects --- #
    async with async_session() as session:
        stmt = select(Sample).where(Sample.sample_batch_id == sample_batch_id)
        result = await session.execute(stmt)
        samples = result.scalars().all()

    if not samples:
        raise NotFoundException(
            f"No samples found in the sample batch with ID {sample_batch_id}."
        )

    # --- Infer intensity variable from instrument types of samples --- #
    intensity_variable = _get_intensity_variable_from_samples(samples)

    # --- Load resolution functions for each sample file --- #
    resolution_functions = dict()
    for sample in samples:
        _, resolution_func = await read_instrument_functions(sample.filename)
        resolution_functions[sample.filename] = resolution_func

    # Peaks will be grouped and aligned by ionization mode
    ionization_modes = set([sample.ionization_mode_id for sample in samples])
    spectra = {ionization_mode: [] for ionization_mode in ionization_modes}

    # Bound concurrency to avoid too many open files / blocking the loop
    semaphore = asyncio.Semaphore(6)

    # --- Load sample files and prepare CentroidedSpectrum objects --- #
    runtime.logger.debug("Loading samples and preparing spectra...")
    collected_specs = await asyncio.gather(
        *[
            _prepare_spec(sample, semaphore, resolution_functions, intensity_variable)
            for sample in samples
        ]
    )
    for ionization_mode, spec in collected_specs:
        spectra[ionization_mode].append(spec)

    return spectra, intensity_variable


def _get_intensity_variable_from_samples(samples: list[Sample]) -> str:
    """Infers the intensity variable based on the instrument type of the provided samples.

    Returns "sum_peak_areas" for TOF instruments and "sum_peak_heights" for Orbitrap instruments.

    :param samples: List of Sample objects to infer the intensity variable from.
    :type samples: list[Sample]
    :raises ValueError: if samples are from different instruments
    :return: The inferred intensity variable.
    :rtype: str
    """
    # --- Validate single instrument type --- #
    instrument_types = {
        m_name.get_instrument_type(sample.filename) for sample in samples
    }
    if len(instrument_types) > 1:
        raise ValueError(
            "Batch contains samples from different instruments. "
            "Aligning samples from different instruments is not supported."
        )

    # --- Infer intensity variable from instrument type --- #
    instrument_type = instrument_types.pop()
    intensity_variable = (
        "sum_peak_areas" if instrument_type == "tof" else "sum_peak_heights"
    )
    return intensity_variable


async def _prepare_spec(
    sample: Sample,
    semaphore: asyncio.Semaphore,
    resolution_functions: dict,
    intensity_variable: Literal["sum_peak_areas", "sum_peak_heights"],
) -> tuple[str, CentroidedSpectrum]:
    """Creates a CentroidSpectrum object for a sample.

    :param sample: Sample to prepare the spectrum for.
    :type sample: Sample
    :param semaphore: Semaphore to limit concurrent file loading.
    :type semaphore: asyncio.Semaphore
    :param resolution_functions: Dictionary of resolution functions per filename.
    :type resolution_functions: dict
    :return: Tuple of ionization mode ID and CentroidedSpectrum object.
    :rtype: tuple[str, CentroidedSpectrum]
    """
    filename = sample.filename
    polarity = sample.polarity
    ionization_mode = sample.ionization_mode_id

    async with semaphore:
        mz, intensity, peak_id = await asyncio.to_thread(
            _sync_load_peak_data, filename, polarity, intensity_variable
        )
    resolution = resolution_functions[filename](mz)
    signal_to_noise = np.ones(mz.size)

    # Placeholder S/N values since they are not required for alignment
    spec = CentroidedSpectrum(
        mz=mz,
        intensity=intensity,
        resolution=resolution,
        signal_to_noise=signal_to_noise,
        peak_id=peak_id,
    )
    return ionization_mode, spec


def _sync_load_peak_data(
    filename: str,
    polarity: Literal["+", "-"],
    intensity_variable: Literal["sum_peak_areas", "sum_peak_heights"],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Synchronous helper to load peak data from file:
    - Loads scan timestamps corresponding to the sample polarity
    - Loads peak data
    - Filters peaks corresponding to the sample polarity
    - Averages peak intensities by number of scans (timestamps)
    - Returns m/z values, intensities, and peak IDs

    :param filename: Filename to load peak data from.
    :type filename: str
    :param polarity: Polarity to filter peaks by.
    :type polarity: Literal["+", "-"]
    :param intensity_variable: Intensity variable to use.
    :type intensity_variable: Literal["sum_peak_areas", "sum_peak_heights"]
    :return: Tuple of m/z values, intensities, and peak IDs.
    :rtype: tuple[np.ndarray, np.ndarray, np.ndarray]
    """
    timestamps = m_compute.get_scan_timestamps(filename, polarity=polarity)

    try:
        peak_data = m_io.load_peak_data(filename)
    except FileNotFoundError as e:
        raise NotFoundException(
            f"Could not load peak data for sample file '{filename}'. "
            "Ensure that the sample file has been processed and peak data is available."
        ) from e
    peak_id = peak_data["peak_id"].values
    polarity_coord = peak_data["polarity"].values
    # Per peak: a stored sum is over the scans the store's axis holds for
    # the peak - a stream's peak over its own stream's scans
    scans = m_compute.stored_scans_per_peak(peak_data, timestamps)
    peak_data = peak_data[intensity_variable]
    mz_mask = polarity_coord == polarity
    mz = peak_data["mz"].values[mz_mask]
    intensity = peak_data.values[mz_mask] / scans[mz_mask]
    peak_id = peak_id[mz_mask]

    return mz, intensity, peak_id
