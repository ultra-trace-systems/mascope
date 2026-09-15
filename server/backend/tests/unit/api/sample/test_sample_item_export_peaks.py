"""Exporting per-scan peak data when the store outlives the scan selection.

Every column of the export but one comes from the sample's peak store; the TIC
is read from the sample file, and the scans a reader selects are decided anew
on every read. A file whose first scan reads as a TIC outlier loses it, and
that rule has not always existed - so a store written before it holds one scan
more than the reader now yields.

Building the frame from the two axes positionally raised "All arrays must be
of the same length", which named neither the store nor what would repair it.
Such a store is not exportable at all: its per-peak sums were measured over
the scan the reader now discards, so every intensity in the frame carries it.
The export refuses it by name instead, and only peak detection puts it right.
"""

import datetime as dt
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from mascope_backend.api.controllers.sample.items import sample_items_controller
from mascope_signal.compute import StalePeakStoreError


_MOD = "mascope_backend.api.controllers.sample.items.sample_items_controller"

# The scans the peak store was allocated with, and the peaks it holds
STORED_TIME = np.array([2.5, 5.1, 7.7, 10.3, 12.9], dtype=float)
MZ_VALUES = np.array([100.0, 200.0, 300.0], dtype=float)
# Distinct per scan and per m/z, so a value read back identifies its own cell
INTENSITIES = np.arange(15, dtype=float).reshape(3, 5) + 1.0
# The first scan is the outlier the reader now excludes
TIC_VALUES = np.array([90000.0, 100.0, 110.0, 120.0, 130.0], dtype=float)

# Undecorated: the notification and reload machinery around the controller is
# not what these tests are about, and it is covered on its own elsewhere.
_export = sample_items_controller.sample_item_export_peaks.__wrapped__


def _sample():
    return SimpleNamespace(
        sample_item_id="item-1",
        sample_batch_id="batch-1",
        sample_file_id="file-1",
        sample_item_name="Test item",
        filename="OrbiTest_1001.01.01_12h00m00s_TestFile",
        datetime=dt.datetime(2024, 1, 1, 12, 0, 0),
        datetime_utc=dt.datetime(2024, 1, 1, 10, 0, 0),
        filter_id="filter-1",
        sample_item_type="ACQUISITION",
        instrument="orbi",
    )


def _peak_store():
    return xr.Dataset(
        {"peak_heights": (("mz", "time"), INTENSITIES)},
        coords={"mz": MZ_VALUES, "time": STORED_TIME},
    )


async def _run_export(tmp_path, live_scan_times, live_tics):
    """Export the stub sample, with the reader covering `live_scan_times`.

    :return: The path the export wrote to, and the CSV read back
    """

    def fake_get_tic_per_scan(base_filename, timestamps=None, polarity=None):
        return (
            np.asarray(live_scan_times, dtype=float),
            np.asarray(live_tics, dtype=float),
        )

    with (
        patch(f"{_MOD}.fetch_sample", AsyncMock(return_value=_sample())),
        patch(
            f"{_MOD}.fetch_sample_batch",
            AsyncMock(
                return_value=SimpleNamespace(
                    sample_batch_id="batch-1", sample_batch_name="Test batch"
                )
            ),
        ),
        patch(f"{_MOD}.get_instrument_type", return_value="orbi"),
        patch(f"{_MOD}.send_progress_user_notification", AsyncMock()),
        patch("mascope_file.io.load_peak_data", return_value=_peak_store()),
        patch("mascope_signal.compute.get_tic_per_scan", fake_get_tic_per_scan),
        # The directory, not user_temp_path itself: that helper is what
        # rejects a filename escaping the user's temp dir, and stubbing it out
        # would take the export's own filename building out of the test.
        patch(
            "mascope_backend.api.new.temp.storage.user_temp_dir",
            return_value=str(tmp_path),
        ),
    ):
        result = await _export("item-1", user_id=1, process_id="process-1")

    written = os.path.join(tmp_path, result["data"]["filename"])
    return written, pd.read_csv(
        written, sep=";", parse_dates=["datetime", "datetime_utc"]
    )


@pytest.mark.asyncio
async def test_a_store_predating_the_outlier_exclusion_is_refused(tmp_path):
    """The reported failure: five stored scans, four the reader still reads.

    Not exportable rather than exportable with a gap - the store's per-peak
    sums were measured over the discarded scan, so every intensity in the
    frame would carry the artifact the exclusion exists to drop.
    """
    with pytest.raises(StalePeakStoreError) as refusal:
        await _run_export(tmp_path, STORED_TIME[1:], TIC_VALUES[1:])

    message = str(refusal.value)
    assert "5 scan(s)" in message and "4" in message
    assert "Re-run peak detection" in message


@pytest.mark.asyncio
async def test_a_refused_export_leaves_no_file_behind(tmp_path):
    """A half-written export would be downloaded as if it were whole."""
    with pytest.raises(StalePeakStoreError):
        await _run_export(tmp_path, STORED_TIME[1:], TIC_VALUES[1:])

    assert os.listdir(tmp_path) == []


@pytest.mark.asyncio
async def test_a_store_from_another_acquisition_is_refused(tmp_path):
    """Scan times that do not line up mean the store describes another file.

    The counts agree here, so only the timestamps can tell the two apart -
    pairing them anyway would give every peak the TIC of a scan it was never
    measured in, an export that looks ordinary and is wrong throughout.
    """
    with pytest.raises(StalePeakStoreError):
        await _run_export(tmp_path, STORED_TIME + 1000.0, TIC_VALUES)


@pytest.mark.asyncio
async def test_a_store_the_file_still_reads_back_exports_every_scan(tmp_path):
    """The ordinary file, where the two axes agree, must be untouched."""
    _, frame = await _run_export(tmp_path, STORED_TIME, TIC_VALUES)

    # One row per stored scan per peak - the store's axis decides the shape
    assert len(frame) == MZ_VALUES.size * STORED_TIME.size
    np.testing.assert_allclose(np.sort(frame.mz.unique()), MZ_VALUES, rtol=0, atol=1e-9)
    assert frame.tic.notna().all()


@pytest.mark.asyncio
async def test_each_tic_lands_on_the_scan_it_was_measured_in(tmp_path):
    """The pairing is by position, so the two axes must agree scan for scan."""
    _, frame = await _run_export(tmp_path, STORED_TIME, TIC_VALUES)

    first_peak = frame[frame.mz == MZ_VALUES[0]].sort_values("datetime")
    np.testing.assert_allclose(first_peak.tic.to_numpy(), TIC_VALUES)
    # The peak intensities are the store's own
    np.testing.assert_allclose(first_peak.intensity.to_numpy(), INTENSITIES[0])
