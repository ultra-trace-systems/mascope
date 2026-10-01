"""
Sample batch export service for generating batch scope Excel spreadsheets, csv files, etc.

The spreadsheet carries what it takes to trace it back: the ids of the dataset,
batch and target collections it was built from, the ids of the records behind
every row, the acquisition context of each sample, and the provenance block of
the build that wrote it (``mascope_backend.provenance``). Additions append - a
sheet, a column or a row - so a reader written against an earlier export keeps
finding what it found there, where it found it.
"""

import asyncio
from datetime import datetime, timezone
from typing import Any

import pandas as pd
from sqlalchemy import select

from mascope_backend.api.controllers.calibration.calibration_controller import (
    is_unfitted_record,
)
from mascope_backend.api.controllers.dataset.dataset_controller import get_dataset
from mascope_backend.api.controllers.match.targets.batch.match_targets_batch_controller import (
    get_batch_data,
)
from mascope_backend.api.controllers.sample.batches.export.util import (
    auto_adjust_column_width,
)
from mascope_backend.api.lib.api_features import (
    api_controller_background_task,
)
from mascope_backend.api.new.match.records.collection.service import (
    get_match_collection_records,
)
from mascope_backend.api.new.temp.storage import download_name, user_temp_path
from mascope_backend.db import IonizationMode, async_session
from mascope_backend.provenance import build_provenance, flatten_provenance
from mascope_backend.runtime import runtime


@api_controller_background_task(
    success_notification_rooms=["user_id"],
    error_notification_rooms=["user_id"],
)
async def sample_batch_export_spreadsheet(
    sample_batch_id: str,
    independent_transaction: bool = False,
    user_id: int | None = None,
    process_id: str | None = None,
    parent_id: str | None = None,
):
    """
    Export batch data and match information to multi-sheet Excel spreadsheet.

    :param sample_batch_id: ID of the sample batch to export.
    :type sample_batch_id: str
    :param independent_transaction: Flag for independent transaction handling, defaults to False.
    :type independent_transaction: bool, optional
    :param user_id: Current user triggered operation (for user notifications)
    :type user_id: int | None, optional
    :param process_id: Process ID for tracking this background task, defaults to None.
    :type process_id: str, optional
    :param parent_id: Parent process ID if this is a subtask, defaults to None.
    :type parent_id: str, optional
    :return: Dictionary with success message, filename, and download info.
    :rtype: dict
    """
    # --- Fetch batch data ---
    batch_data_result = await get_batch_data(sample_batch_id)
    data = batch_data_result["data"]
    sample_batch = data["sample_batch"]
    sample_batch_name = sample_batch["sample_batch_name"]

    runtime.logger.info(f"Exporting spreadsheet for batch '{sample_batch_name}'")

    # --- Fetch dataset and target collections ---
    dataset_id = sample_batch.get("dataset_id")
    dataset = (await get_dataset(dataset_id)).get("data") if dataset_id else None

    collections = (
        await get_match_collection_records(sample_batch_id=sample_batch_id)
    ).get("data", [])
    collection_names = [col["target_collection_name"] for col in collections]
    collections_str = ", ".join(collection_names) if collection_names else "none"
    collection_ids = [col["target_collection_id"] for col in collections]

    ionization_modes = await _ionization_mode_names(
        {sample.get("ionization_mode_id") for sample in data["samples"]}
    )

    # --- Prepare lookup dictionaries ---
    samples_lookup = {sample["sample_item_id"]: sample for sample in data["samples"]}
    compound_info_lookup = {}
    for compound in data["compounds"]:
        target_id = compound.get("target_compound_id")
        if target_id and target_id not in compound_info_lookup:
            compound_info_lookup[target_id] = {
                "target_compound_name": compound.get("target_compound_name"),
                "target_compound_formula": compound.get("target_compound_formula"),
            }

    # --- Prepare DataFrames for each sheet ---

    # Batch info sheet
    batch_info_data = [
        ["Name", sample_batch_name],
        ["Description", sample_batch["sample_batch_description"]],
        ["Type", sample_batch["sample_batch_type"]],
        ["Polarity", sample_batch["polarity"]],
        ["Dataset", dataset.get("dataset_name") if dataset else None],
        [""],
        ["Target collections", collections_str],
        [""],
        ["Total Samples", batch_data_result["result"]["samples"]],
        ["Total Compounds", batch_data_result["result"]["compounds"]],
        ["Total Ions", batch_data_result["result"]["ions"]],
        ["Total Isotopes", batch_data_result["result"]["isotopes"]],
        [""],
        ["Dataset ID", dataset_id],
        ["Sample batch ID", sample_batch_id],
        ["Target collection IDs", ", ".join(collection_ids) or "none"],
        [""],
        ["Instruments", _distinct(data["samples"], "instrument")],
        ["Method files", _distinct(data["samples"], "method_file")],
    ]
    batch_info_df = pd.DataFrame(batch_info_data)

    # Samples sheet
    samples_data = []
    for sample in data["samples"]:
        match = sample.get("match", {})
        mz_min, mz_max = _mz_range(sample.get("range"))
        calibration, verified, error_ppm = _calibration_summary(
            sample.get("mz_calibration")
        )
        samples_data.append(
            {
                "Sample name": sample.get("sample_item_name"),
                "Filename": sample.get("filename"),
                "Datetime": sample.get("datetime"),
                "Sample type": sample.get("sample_item_type"),
                "TIC": sample.get("tic"),
                "Filter ID": sample.get("filter_id"),
                "Total peak intensity (cps)": (
                    match.get("sample_peak_intensity_sum") if match else None
                ),
                "Match score": match.get("match_score") if match else None,
                "Sample item ID": sample.get("sample_item_id"),
                "Sample file ID": sample.get("sample_file_id"),
                "Datetime UTC": _naive_utc(sample.get("datetime_utc")),
                "Instrument": sample.get("instrument"),
                "Instrument type": sample.get("instrument_type"),
                "Method file": sample.get("method_file"),
                "Polarity": sample.get("polarity"),
                "m/z range min": mz_min,
                "m/z range max": mz_max,
                "Ionization mode": ionization_modes.get(
                    sample.get("ionization_mode_id")
                ),
                "Ionization mode ID": sample.get("ionization_mode_id"),
                "Instrument function ID": sample.get("instrument_function_id"),
                "m/z calibration": calibration,
                "m/z calibration verified": verified,
                "m/z calibration error (ppm)": error_ppm,
            }
        )
    samples_df = pd.DataFrame(samples_data)

    # Match compounds sheet
    compounds_data = []
    for compound in data["compounds"]:
        sample_id = compound.get("sample_item_id")
        sample = samples_lookup.get(sample_id, {})
        compounds_data.append(
            {
                "Sample name": sample.get("sample_item_name"),
                "Filename": sample.get("filename"),
                "Sample type": sample.get("sample_item_type"),
                "Compound name": compound.get("target_compound_name"),
                "Compound formula": compound.get("target_compound_formula"),
                "Total peak intensity (cps)": compound.get("sample_peak_intensity_sum"),
                "Match score": compound.get("match_score"),
                "Sample item ID": sample_id,
                "Target compound ID": compound.get("target_compound_id"),
            }
        )
    compounds_df = pd.DataFrame(compounds_data) if compounds_data else pd.DataFrame()

    # Match ions sheet
    ions_data = []
    for ion in data["ions"]:
        sample_id = ion.get("sample_item_id")
        sample = samples_lookup.get(sample_id, {})

        target_compound_id = ion.get("target_compound_id")
        compound_info = compound_info_lookup.get(target_compound_id, {})

        ions_data.append(
            {
                "Sample name": sample.get("sample_item_name"),
                "Filename": sample.get("filename"),
                "Sample type": sample.get("sample_item_type"),
                "Compound name": compound_info.get("target_compound_name"),
                "Compound formula": compound_info.get("target_compound_formula"),
                "Ionization mechanism": ion.get("ionization_mechanism"),
                "Ion formula": ion.get("target_ion_formula"),
                "Total peak intensity (cps)": ion.get("sample_peak_intensity_sum"),
                "Match score": ion.get("match_score"),
                "Sample item ID": sample_id,
                "Target compound ID": target_compound_id,
                "Target ion ID": ion.get("target_ion_id"),
            }
        )
    ions_df = pd.DataFrame(ions_data) if ions_data else pd.DataFrame()

    # Provenance sheet: the block of the build writing this file, flattened
    provenance = build_provenance(
        inputs={
            "dataset_id": dataset_id,
            "sample_batch_id": sample_batch_id,
            "target_collection_ids": collection_ids,
        }
    )
    provenance_df = pd.DataFrame(flatten_provenance(provenance))

    # --- Generate Excel file ---
    dt_str = datetime.now().isoformat().replace("-", "").replace(":", "").split(".")[0]
    file = download_name(dt_str, sample_batch_name, extension="xlsx")
    filepath = user_temp_path(user_id, file)

    runtime.logger.info(f"Writing spreadsheet to file {file}")

    # Offload blocking I/O to thread pool
    await asyncio.to_thread(
        _write_excel_file,
        filepath,
        batch_info_df,
        samples_df,
        compounds_df,
        ions_df,
        provenance_df,
    )

    message = f"Spreadsheet for sample batch '{sample_batch_name}' was exported to file '{file}'."
    runtime.logger.info(message)

    return {
        "message": message,
        "data": {"file": file},
        "_notification_data": {
            "sample_batch_id": sample_batch_id,
            "download": file,
        },
    }


def _write_excel_file(
    filepath: str,
    batch_info_df: pd.DataFrame,
    samples_df: pd.DataFrame,
    compounds_df: pd.DataFrame,
    ions_df: pd.DataFrame,
    provenance_df: pd.DataFrame,
) -> None:
    """
    Write DataFrames to Excel file with auto-adjusted columns.

    :param filepath: Full path to output Excel file
    :param batch_info_df: Batch metadata DataFrame
    :param samples_df: Samples with match data DataFrame
    :param compounds_df: Match compounds DataFrame
    :param ions_df: Match ions DataFrame
    :param provenance_df: The provenance block as key/value rows
    """
    with pd.ExcelWriter(filepath, engine="openpyxl") as writer:
        batch_info_df.to_excel(writer, sheet_name="Batch", index=False, header=False)
        samples_df.to_excel(writer, sheet_name="Samples", index=False)

        if not compounds_df.empty:
            compounds_df.to_excel(writer, sheet_name="Match compounds", index=False)
        if not ions_df.empty:
            ions_df.to_excel(writer, sheet_name="Match ions", index=False)
        provenance_df.to_excel(
            writer, sheet_name="Provenance", index=False, header=False
        )

        # Auto-adjust column widths for all sheets
        for worksheet in writer.sheets.values():
            auto_adjust_column_width(worksheet)


async def _ionization_mode_names(mode_ids: set[str | None]) -> dict[str, str]:
    """
    The names of the ionization modes the samples were processed with.

    :param mode_ids: Ionization mode ids, None for a sample without one.
    :return: Name by id, for the modes that still exist.
    :rtype: dict[str, str]
    """
    mode_ids = {mode_id for mode_id in mode_ids if mode_id}
    if not mode_ids:
        return {}
    async with async_session() as session:
        rows = await session.execute(
            select(
                IonizationMode.ionization_mode_id, IonizationMode.ionization_mode_name
            ).where(IonizationMode.ionization_mode_id.in_(mode_ids))
        )
        return dict(rows.all())


def _distinct(samples: list[dict], key: str) -> str:
    """
    Every distinct value of ``key`` across the samples, for the Batch sheet.

    A batch can span instruments and methods, so all of them are named rather
    than one standing for the rest; the Samples sheet says which is whose.

    :param samples: Sample dicts from ``get_batch_data``.
    :param key: The sample field to collect.
    :return: The values, sorted and comma-separated; "none" when no sample
        has one.
    :rtype: str
    """
    values = sorted({str(sample[key]) for sample in samples if sample.get(key)})
    return ", ".join(values) or "none"


def _naive_utc(value: Any) -> Any:
    """
    A timezone-aware datetime as naive UTC, which is all Excel can store.

    The column header names the zone; anything else passes through unchanged.
    """
    if isinstance(value, datetime) and value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _mz_range(value: Any) -> tuple[Any, Any]:
    """
    The low and high end of a sample file's m/z range.

    :param value: ``sample_file.range``, stored as ``[low, high]``.
    :return: ``(low, high)``, or ``(None, None)`` when the file records none.
    """
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return value[0], value[-1]
    return None, None


def _calibration_summary(record: dict | None) -> tuple[str, bool | None, Any]:
    """
    What a sample file's m/z calibration record says about the file.

    The record's own verdict rather than the fit itself: the model and its
    parameters mean nothing outside Mascope, while whether a fit was applied,
    whether it cleared the quality bar, and how far off it left the axis are
    what a reader of the export needs to weigh the numbers.

    :param record: ``sample_file.mz_calibration``.
    :return: The status (``ok`` and ``poor`` were applied, ``failed`` was given
        up on, ``unfitted`` is the acquisition's own axis, ``none`` is no
        record at all), whether it is verified, and the mean post-fit m/z
        error in ppm - None where the record does not say.
    """
    if not record:
        return "none", None, None
    if is_unfitted_record(record):
        return "unfitted", None, None
    quality = record.get("quality") or {}
    return (
        record.get("status") or "unknown",
        record.get("verified"),
        quality.get("post_fit_mz_error_ppm"),
    )
