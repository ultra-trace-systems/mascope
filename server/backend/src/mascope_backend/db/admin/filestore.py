from __future__ import annotations

import asyncio
import glob
import os
from typing import Literal

from mascope_backend.api.controllers.sample.lib.sample_file_fetch import (
    fetch_sample_files,
)
from mascope_backend.api.new.instrument_configs.lib import (
    read_instrument_functions,
)
from mascope_backend.db import init_db
from mascope_backend.runtime import runtime
from mascope_file.io import remove_path
from mascope_file.name import parse_path_from_item_filename
from mascope_signal.peak import (
    compute_peaks,
)


# Actions


async def delete_sum_signal(cached_only=False):
    """Delete "sum_signal" from all sample files in the database.
    if cached_only=False, delete both cached and full sum signals.
    cached_only=True will only delete cached sum signals.

    cached sum signals are those with names like "sum_signal_a1b2c3d4.zarr"
    full sum signals are those with names like "sum_signal.zarr"
    """
    # Get all sample files
    sample_files = await fetch_sample_files()

    # Delete sum_signal from all sample files
    for i, sample_file in enumerate(sample_files):
        runtime.logger.info(
            (
                f"Deleting sum_signal from sample file {sample_file.filename}: ",
                f"{i + 1}/{len(sample_files)}",
            )
        )
        sample_data_path = parse_path_from_item_filename(sample_file.filename)
        pattern = "sum_signal_*" if cached_only else "sum_signal*"
        sum_signal_dirs = glob.glob(
            os.path.join(
                sample_data_path,
                pattern,
            )
        )
        for zarr_dir in sum_signal_dirs:
            # The glob matches a store's side-car lock file as well as the
            # store directory, and rmtree raises NotADirectoryError on a file.
            remove_path(zarr_dir)


async def refit_peaks():
    """Refit all peaks for all sample files in the database."""
    # Get all sample files
    sample_files = await fetch_sample_files()

    # Compute all sample file peaks
    for i, sample_file in enumerate(sample_files):
        runtime.logger.info(
            (
                f"Computing peaks for sample file {sample_file.filename}: ",
                f"{i + 1}/{len(sample_files)}",
            )
        )
        try:
            instrument_functions = await read_instrument_functions(
                filename=sample_file.filename
            )
            compute_peaks(sample_file.filename, instrument_functions)
        except FileNotFoundError:
            runtime.logger.error(
                f"Error computing peaks for sample file {sample_file.filename}. File not found."
            )
            continue
        except ValueError as e:
            runtime.logger.error(
                f"Error computing peaks for sample file {sample_file.filename}: {e}."
            )
            continue


# CLI entry point


ACTIONS = {
    "delete-sum-signal": delete_sum_signal,
    "refit-peaks": refit_peaks,
}
ActionTypes = Literal[tuple(ACTIONS.keys())]


def run_action(action_name: ActionTypes) -> None:
    """Run a specific action based on the action name.

    :param action_name: Name of the action to perform.
    :type action_name: Actions
    """
    asyncio.run(init_db())
    asyncio.run(ACTIONS[action_name]())
