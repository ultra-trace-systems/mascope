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

# The third peak's timeseries has not been computed: its per-scan heights
# are the NaN placeholders detection allocates, its stored sum is not.
MZ_VALUES = np.array([100.0, 200.0, 300.0])

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


def _peak_store(*args, **kwargs) -> xr.Dataset:
    """What ``load_peak_data`` gives back: two peaks with a timeseries and
    one without, each with its stored sums."""
    heights = np.array([[1.0, 2.0, np.nan], [3.0, 4.0, np.nan]])
    return xr.Dataset(
        {
            "peak_heights": (("time", "mz"), heights),
            "peak_areas": (("time", "mz"), heights * 10),
            "sum_peak_heights": ("mz", [4.0, 6.0, 9.0]),
            "sum_peak_areas": ("mz", [40.0, 60.0, 90.0]),
            "peak_id": ("mz", ["p1", "p2", "p3"]),
            "polarity": ("mz", ["+", "+", "+"]),
            "sparsity": ("mz", [0.0, 0.0, 1.0]),
        },
        coords={"time": [0.0, 1.0], "mz": MZ_VALUES},
        attrs={"props": {"filename": "OrbiTest_1001.01.01_12h00m00s_file-1"}},
    )


async def _run_export(
    tmp_path, instrument_type=lambda filename: "orbi"
) -> pd.DataFrame:
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
        patch(f"{_MOD}.m_name.get_instrument_type", side_effect=instrument_type),
        # A raw file's store: one whose peaks keep their timeseries placeholders
        patch("mascope_file.name.get_sample_file_type", return_value="orbi_raw"),
        patch("mascope_file.io.load_peak_data", _peak_store),
        patch(
            "mascope_backend.api.controllers.samples.lib.samples_peaks.load_peak_data",
            _peak_store,
        ),
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

    # Three peaks per sample
    assert len(frame) == 6
    assert dict(zip(frame.sample_item_id, frame.sample_file_id)) == {
        "item-1": "file-1",
        "item-2": "file-2",
    }
    np.testing.assert_allclose(
        frame[frame.sample_item_id == "item-1"].intensity, [4.0, 6.0, 9.0]
    )


@pytest.mark.asyncio
async def test_a_peak_without_a_timeseries_exports_its_stored_sum(tmp_path):
    """Summing its timeseries would write 0, as if the peak were not there:
    a peak's timeseries is computed only once something asks for it."""
    frame = await _run_export(tmp_path)

    uncomputed = frame[frame.mz == 300.0]
    assert len(uncomputed) == 2
    np.testing.assert_allclose(uncomputed.intensity, [9.0, 9.0])


@pytest.mark.asyncio
async def test_a_tof_file_exports_its_stored_areas(tmp_path):
    """The export's unit follows the instrument: areas for a TOF."""
    frame = await _run_export(tmp_path, instrument_type=lambda filename: "tof")

    np.testing.assert_allclose(
        frame[frame.sample_item_id == "item-1"].intensity, [40.0, 60.0, 90.0]
    )
    assert (frame.unit == "ions").all()


@pytest.mark.asyncio
async def test_a_file_of_an_instrument_without_a_unit_is_skipped(tmp_path):
    """No unit, no value to export: its rows are left out, the others kept."""
    frame = await _run_export(
        tmp_path,
        instrument_type=lambda filename: "other" if "file-2" in filename else "orbi",
    )

    assert set(frame.sample_item_id) == {"item-1"}
