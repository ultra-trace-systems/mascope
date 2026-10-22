"""
The sample peaks route sends a peak with no signal-to-noise as null itself.

A TOF peak's noise is measured on the baseline around it, and a peak with no
baseline left there, or a flat one, has no ratio: the store holds NaN for it.
``@api_route`` would render that as null too, but with a WARNING on every
such request, while a missing estimate is an expected state: the route maps
it itself, and the render's fallback stays quiet.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import numpy as np
import pytest
import xarray as xr
from test_utils import captured_logs

from mascope_backend.api.controllers.samples import samples_controller
from mascope_backend.api.controllers.samples.lib import samples_peaks
from mascope_backend.api.models.samples.sample_pydantic_model import (
    GetSamplePeaksQueryParams,
)
from mascope_backend.api.routes.samples.samples_routes import (
    get_sample_peaks_route,
)


FILENAME = "TofTest_1001.01.01_12h00m00s_TestFile"
TIME = [0.0, 1.0, 2.0, 3.0]
MZS = [100.0, 200.0, 300.0, 400.0]
SIGNAL_TO_NOISE = [12.5, np.nan, 3.0, np.inf]
USER = SimpleNamespace(to_dict=lambda: {"id": "user_1"})

RANGES = [
    pytest.param({}, id="whole-sample"),
    pytest.param({"t_min": TIME[0], "t_max": TIME[-1]}, id="time-range"),
]


def strict_loads(body: bytes):
    """Parse like a browser's ``JSON.parse``: reject NaN/Infinity literals."""

    def reject(literal):
        raise ValueError(f"non-strict JSON literal: {literal}")

    return json.loads(body, parse_constant=reject)


def peak_dataset(signal_to_noise=SIGNAL_TO_NOISE) -> xr.Dataset:
    """A stored peak store of one polarity, as ``load_peak_data`` returns it."""
    heights = np.ones((len(MZS), len(TIME)))
    data = {
        "peak_heights": (("mz", "time"), heights),
        "peak_areas": (("mz", "time"), heights * 2),
        "sum_peak_heights": ("mz", heights.sum(axis=1)),
        "sum_peak_areas": ("mz", heights.sum(axis=1) * 2),
        "sparsity": ("mz", np.zeros(len(MZS))),
        "is_timeseries_computed": ("mz", np.ones(len(MZS), dtype=bool)),
        "polarity": ("mz", np.full(len(MZS), "+")),
    }
    if signal_to_noise is not None:
        data["signal_to_noise"] = ("mz", np.array(signal_to_noise))
    return xr.Dataset(
        data,
        coords={
            "mz": MZS,
            "time": TIME,
            "peak_id": ("mz", [f"peak_{int(mz)}" for mz in MZS]),
        },
    )


async def peaks_answer(dataset: xr.Dataset, time_range: dict):
    """The route's response for a sample whose store is ``dataset``, and the
    warnings logged while it was made and rendered."""
    sample = SimpleNamespace(
        sample_item_id="si_1",
        filename=FILENAME,
        t0=TIME[0],
        t1=TIME[-1],
        polarity="+",
        sample_item_name="Sample 1",
    )
    with (
        patch.object(
            samples_controller, "fetch_sample", AsyncMock(return_value=sample)
        ),
        patch.object(samples_controller, "_segments_of", AsyncMock(return_value=None)),
        patch.object(samples_peaks, "load_peak_data", return_value=dataset),
        patch.object(
            samples_peaks.m_compute,
            "get_scan_timestamps",
            return_value=np.array(TIME),
        ),
        captured_logs("WARNING") as records,
    ):
        response = await get_sample_peaks_route(
            sample_item_id="si_1",
            query_params=GetSamplePeaksQueryParams(**time_range),
            user=USER,
            membership=None,
        )
    return response, records


@pytest.mark.parametrize("time_range", RANGES)
@pytest.mark.asyncio
async def test_a_peak_with_no_signal_to_noise_is_sent_as_null(time_range):
    response, records = await peaks_answer(peak_dataset(), time_range)

    assert response.status_code == 200
    data = strict_loads(response.body)["data"]
    assert data["peak_id"] == [f"peak_{int(mz)}" for mz in MZS]
    assert data["signal_to_noise"] == [12.5, None, 3.0, None]
    # Nulled by the route, not by the render's non-finite fallback.
    assert records == []


@pytest.mark.parametrize("time_range", RANGES)
@pytest.mark.asyncio
async def test_a_file_with_no_estimate_still_sends_null_for_the_whole_column(
    time_range,
):
    response, records = await peaks_answer(peak_dataset(None), time_range)

    assert response.status_code == 200
    assert strict_loads(response.body)["data"]["signal_to_noise"] is None
    assert records == []
