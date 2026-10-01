"""Exporting a batch's summed peaks to CSV, traceable to its records.

Every row of the batch peak CSV names the sample item and sample file it came
from, and - appended after the columns the export always wrote - the sample
batch and the dataset, so a CSV found on its own can be traced back to the
records that produced it.
"""

import datetime as dt
import os
from unittest.mock import AsyncMock, patch

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from mascope_backend.api.controllers.sample.batches import sample_batches_controller


_MOD = "mascope_backend.api.controllers.sample.batches.sample_batches_controller"

# Undecorated: the notification machinery around the controller is not what
# these tests are about.
_export = sample_batches_controller.sample_batch_export_peaks.__wrapped__

MZ_VALUES = np.array([100.0, 200.0])

#: The columns the export wrote before the batch and dataset ids, in order.
LEGACY_COLUMNS = [
    "mz",
    "intensity",
    "unit",
    "sample_batch_name",
    "sample_item_name",
    "filename",
    "filter_id",
    "sample_item_type",
    "datetime",
    "datetime_utc",
    "sample_file_id",
    "sample_item_id",
    "tic",
    "instrument",
]


def _sample_view(item_id: str, file_id: str) -> dict:
    """A row of ``sample_view``, as ``get_sample`` answers it."""
    return {
        "sample_item_id": item_id,
        "sample_file_id": file_id,
        "sample_batch_id": "batch-1",
        "sample_item_name": f"Sample {item_id}",
        "filename": f"OrbiTest_1001.01.01_12h00m00s_{file_id}",
        "filter_id": "filter-1",
        "sample_item_type": "ANALYSIS",
        "datetime": dt.datetime(2024, 1, 1, 12, 0, 0),
        "datetime_utc": dt.datetime(2024, 1, 1, 10, 0, 0, tzinfo=dt.timezone.utc),
        "tic": 1000.0,
        "instrument": "instrument-x",
    }


def _summed_peaks(*args, **kwargs) -> xr.DataArray:
    """What ``get_peaks`` gives back: peak values over (time, mz)."""
    return xr.DataArray(
        [[1.0, 2.0], [3.0, 4.0]],
        dims=("time", "mz"),
        coords={"time": [0.0, 1.0], "mz": MZ_VALUES},
    )


async def _run_export(tmp_path) -> pd.DataFrame:
    samples = {"item-1": _sample_view("item-1", "file-1")}
    samples["item-2"] = _sample_view("item-2", "file-2")

    with (
        patch(
            f"{_MOD}.get_sample_batch",
            AsyncMock(
                return_value={
                    "data": {
                        "sample_batch_id": "batch-1",
                        "sample_batch_name": "Test batch",
                        "dataset_id": "dataset-1",
                    }
                }
            ),
        ),
        patch(
            f"{_MOD}.fetch_sample_item_ids",
            AsyncMock(return_value=(list(samples), None)),
        ),
        patch(
            f"{_MOD}.get_sample",
            AsyncMock(side_effect=lambda item_id: {"data": samples[item_id]}),
        ),
        patch(f"{_MOD}.m_name.get_instrument_type", return_value="orbi"),
        patch(f"{_MOD}.m_io.load_peak_data", return_value=None),
        patch(f"{_MOD}.get_peaks", _summed_peaks),
        patch(f"{_MOD}.send_progress_user_notification", AsyncMock()),
        # The directory, not user_temp_path itself, so the export's own file
        # naming stays under test.
        patch(
            "mascope_backend.api.new.temp.storage.user_temp_dir",
            return_value=str(tmp_path),
        ),
    ):
        result = await _export("batch-1", user_id=1, process_id="process-1")

    return pd.read_csv(os.path.join(tmp_path, result["data"]["filename"]), sep=";")


@pytest.mark.asyncio
async def test_every_row_names_its_batch_and_dataset_after_the_old_columns(tmp_path):
    frame = await _run_export(tmp_path)

    assert list(frame.columns) == LEGACY_COLUMNS + ["sample_batch_id", "dataset_id"]
    assert (frame.sample_batch_id == "batch-1").all()
    assert (frame.dataset_id == "dataset-1").all()


@pytest.mark.asyncio
async def test_every_row_keeps_its_own_sample_and_file(tmp_path):
    frame = await _run_export(tmp_path)

    # Two peaks per sample, summed over time
    assert len(frame) == 4
    assert dict(zip(frame.sample_item_id, frame.sample_file_id)) == {
        "item-1": "file-1",
        "item-2": "file-2",
    }
    np.testing.assert_allclose(
        frame[frame.sample_item_id == "item-1"].intensity, [4.0, 6.0]
    )
