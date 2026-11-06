"""
The sample peaks route sends each peak's scan counts: how many of the scans
averaged into it hold it (``n_scans``), and how many those are
(``n_scans_total``).

An Orbitrap scan keeps a centroid only where it clears that scan's noise, so a
dim line is missing from part of the scans and its average reads diluted by
the share it missed. Over the whole sample the counts are the store's; over a
time range they are read off each peak's timeseries over the range. A store
that keeps no count sends null for both columns, never zeros, and so does the
batch's Parquet table.
"""

import io
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import numpy as np
import pyarrow.parquet as pq
import pytest
import xarray as xr
from xarray.backends import BackendArray
from xarray.core import indexing

from mascope_backend.api.controllers.samples import batch_peak_table, samples_controller
from mascope_backend.api.controllers.samples.lib import samples_peaks
from mascope_backend.api.models.samples.sample_pydantic_model import (
    GetSamplePeaksQueryParams,
)
from mascope_backend.api.routes.samples.samples_routes import (
    get_sample_peaks_route,
)


FILENAME = "OrbiTest_1001.01.01_12h00m00s_TestFile"
TIME = [0.0, 1.0, 2.0, 3.0]
MZS = [100.0, 101.0, 200.0]
# The anchor in every scan, a dim line in the first two only, a third peak
# whose width the detection could not read
HEIGHTS = np.array(
    [
        [10.0, 10.0, 10.0, 10.0],
        [2.0, 2.0, 0.0, 0.0],
        [5.0, 5.0, 5.0, 0.0],
    ]
)
N_SCANS = [4.0, 2.0, np.nan]
USER = SimpleNamespace(to_dict=lambda: {"id": "user_1"})


def peak_dataset(counts: bool = True) -> xr.Dataset:
    """A stored peak store of one polarity, as ``load_peak_data`` returns it."""
    data = {
        "peak_heights": (("mz", "time"), HEIGHTS),
        "peak_areas": (("mz", "time"), HEIGHTS * 2),
        "sum_peak_heights": ("mz", HEIGHTS.sum(axis=1)),
        "sum_peak_areas": ("mz", HEIGHTS.sum(axis=1) * 2),
        "sparsity": ("mz", np.zeros(len(MZS))),
        "is_timeseries_computed": ("mz", np.ones(len(MZS), dtype=bool)),
        "polarity": ("mz", np.full(len(MZS), "+")),
    }
    if counts:
        data["n_scans"] = ("mz", np.array(N_SCANS))
        data["n_scans_total"] = ("mz", np.full(len(MZS), len(TIME), dtype=np.int64))
    return xr.Dataset(
        data,
        coords={
            "mz": MZS,
            "time": TIME,
            "peak_id": ("mz", [f"peak_{int(mz)}" for mz in MZS]),
        },
    )


def _timestamps(*_, t_min=None, t_max=None, **__) -> np.ndarray:
    """The scan times of the range asked for, of the store's one polarity."""
    return np.array(
        [
            t
            for t in TIME
            if (t_min is None or t >= t_min) and (t_max is None or t <= t_max)
        ]
    )


def _patched(dataset: xr.Dataset):
    """The reads a sample's peak listing makes, answered by ``dataset``."""
    return (
        patch.object(samples_peaks, "load_peak_data", return_value=dataset),
        patch.object(
            samples_peaks.m_compute, "get_scan_timestamps", side_effect=_timestamps
        ),
    )


async def peaks_data(dataset: xr.Dataset, **query) -> dict:
    """The ``data`` of the route's answer for a sample whose store is
    ``dataset``."""
    sample = SimpleNamespace(
        sample_item_id="si_1",
        filename=FILENAME,
        t0=TIME[0],
        t1=TIME[-1],
        polarity="+",
        sample_item_name="Sample 1",
    )
    load, timestamps = _patched(dataset)
    with (
        patch.object(
            samples_controller, "fetch_sample", AsyncMock(return_value=sample)
        ),
        patch.object(samples_controller, "_segments_of", AsyncMock(return_value=None)),
        load,
        timestamps,
    ):
        response = await get_sample_peaks_route(
            sample_item_id="si_1",
            query_params=GetSamplePeaksQueryParams(**query),
            user=USER,
            membership=None,
        )
    assert response.status_code == 200
    return json.loads(response.body)["data"]


@pytest.mark.asyncio
async def test_the_whole_sample_sends_the_stores_counts():
    data = await peaks_data(peak_dataset())

    assert data["n_scans"] == [4, 2, None]
    assert data["n_scans_total"] == [4, 4, 4]


@pytest.mark.asyncio
async def test_a_time_range_counts_the_scans_of_the_range():
    """Over the last three scans the dim line is in one, the third peak in
    two: read off the timeseries, not off the store's whole-sample count."""
    data = await peaks_data(peak_dataset(), t_min=1.0, t_max=3.0)

    assert data["n_scans"] == [3, 1, 2]
    assert data["n_scans_total"] == [3, 3, 3]


@pytest.mark.asyncio
async def test_a_time_range_holding_no_scan_counts_none_present():
    """A range between two scans: no scan, so no peak is present in one."""
    data = await peaks_data(peak_dataset(), t_min=1.2, t_max=1.8)

    assert data["n_scans"] == [0, 0, 0]
    assert data["n_scans_total"] == [0, 0, 0]


class CountedReads(BackendArray):
    """A stored variable opened lazily, as ``xr.open_zarr`` opens one, that
    counts how often its values are read off the store."""

    def __init__(self, array: np.ndarray):
        self.array = array
        self.shape = array.shape
        self.dtype = array.dtype
        self.reads = 0

    def __getitem__(self, key):
        return indexing.explicit_indexing_adapter(
            key, self.shape, indexing.IndexingSupport.BASIC, self._read
        )

    def _read(self, key):
        self.reads += 1
        return self.array[key]


@pytest.mark.parametrize(
    "counts, heights, reads",
    [(True, True, 1), (True, False, 1), (False, True, 1), (False, False, 0)],
    ids=["counted", "counted-areas-only", "uncounted", "uncounted-areas-only"],
)
@pytest.mark.asyncio
async def test_a_time_range_reads_the_heights_once(counts, heights, reads):
    """The heights' sum and the scan counts are both read off the range's
    heights, and a lazily opened store reads them off the disk at each read:
    once for both, and not at all where neither is asked for."""
    dataset = peak_dataset(counts=counts)
    stored = CountedReads(HEIGHTS)
    dataset["peak_heights"] = xr.Variable(
        ("mz", "time"), indexing.LazilyIndexedArray(stored)
    )

    data = await peaks_data(dataset, t_min=1.0, t_max=3.0, areas=True, heights=heights)

    assert stored.reads == reads
    if heights:
        assert data["height"] == pytest.approx([10.0, 2.0 / 3, 10.0 / 3])
    if counts:
        assert data["n_scans"] == [3, 1, 2]


@pytest.mark.parametrize(
    "query", [{}, {"t_min": 1.0, "t_max": 3.0}], ids=["whole-sample", "time-range"]
)
@pytest.mark.asyncio
async def test_a_store_without_counts_sends_null(query):
    data = await peaks_data(peak_dataset(counts=False), **query)

    assert data["n_scans"] is None
    assert data["n_scans_total"] is None
    # The rest of the read is as it was
    assert data["peak_id"] == [f"peak_{int(mz)}" for mz in MZS]


@pytest.mark.parametrize("counts", [True, False], ids=["counted", "uncounted"])
@pytest.mark.asyncio
async def test_the_batch_table_carries_the_counts(counts):
    sample = SimpleNamespace(
        sample_item_id="si_1",
        sample_item_name="Sample 1",
        datetime_utc=None,
        filename=FILENAME,
        polarity="+",
        t0=TIME[0],
        t1=TIME[-1],
        instrument="instrument X",
        instrument_type="orbi",
    )
    load, timestamps = _patched(peak_dataset(counts=counts))
    with (
        load,
        timestamps,
        patch.object(batch_peak_table, "_segments_of", AsyncMock(return_value=None)),
    ):
        table, _ = await batch_peak_table._sample_table(
            sample, matches=False, satellites=False, average=True
        )

    # Read back as the SDK reads the batch's table
    frame = pq.read_table(io.BytesIO(_parquet_bytes(table))).to_pandas()
    if counts:
        assert frame["n_scans"].tolist()[:2] == [4, 2]
        assert frame["n_scans"].isna().tolist() == [False, False, True]
        assert frame["n_scans_total"].tolist() == [4, 4, 4]
    else:
        assert frame["n_scans"].isna().all()
        assert frame["n_scans_total"].isna().all()


def _parquet_bytes(table) -> bytes:
    sink = io.BytesIO()
    pq.write_table(table, sink)
    return sink.getvalue()
