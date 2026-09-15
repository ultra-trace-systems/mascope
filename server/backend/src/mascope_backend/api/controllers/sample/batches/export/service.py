"""
Sample batch export service for generating batch scope Excel spreadsheets, csv files, etc.
"""

import asyncio
from datetime import datetime

import pandas as pd

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
        ["Dataset", dataset.get("dataset_name")],
        [""],
        ["Target collections", collections_str],
        [""],
        ["Total Samples", batch_data_result["result"]["samples"]],
        ["Total Compounds", batch_data_result["result"]["compounds"]],
        ["Total Ions", batch_data_result["result"]["ions"]],
        ["Total Isotopes", batch_data_result["result"]["isotopes"]],
    ]
    batch_info_df = pd.DataFrame(batch_info_data)

    # Samples sheet
    samples_data = []
    for sample in data["samples"]:
        match = sample.get("match", {})
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
            }
        )
    ions_df = pd.DataFrame(ions_data) if ions_data else pd.DataFrame()

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
) -> None:
    """
    Write DataFrames to Excel file with auto-adjusted columns.

    :param filepath: Full path to output Excel file
    :param batch_info_df: Batch metadata DataFrame
    :param samples_df: Samples with match data DataFrame
    :param compounds_df: Match compounds DataFrame
    :param ions_df: Match ions DataFrame
    """
    with pd.ExcelWriter(filepath, engine="openpyxl") as writer:
        batch_info_df.to_excel(writer, sheet_name="Batch", index=False, header=False)
        samples_df.to_excel(writer, sheet_name="Samples", index=False)

        if not compounds_df.empty:
            compounds_df.to_excel(writer, sheet_name="Match compounds", index=False)
        if not ions_df.empty:
            ions_df.to_excel(writer, sheet_name="Match ions", index=False)

        # Auto-adjust column widths for all sheets
        for worksheet in writer.sheets.values():
            auto_adjust_column_width(worksheet)
