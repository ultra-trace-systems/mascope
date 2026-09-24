"""
Both peak timeseries routes send a NaN peak height as null themselves.

A peak's heights are NaN placeholders in the stored timeseries until its
timeseries is computed, and ``get_peaks`` drops such peaks only for files
without a source data file (``*_zarr``). The sample-level route computes
missing timeseries first and nulls whatever NaN remains. The legacy file-level
route computes nothing, so for a raw-backed file the nearest peak can be all
NaN. ``@api_route`` would render those as null too, but with a WARNING on every
such request, while missing heights are an expected state here: each route
maps them itself, and the render's fallback stays quiet.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import numpy as np
import pytest
import xarray as xr
from test_utils import captured_logs

import mascope_signal.peak as peak_module
from mascope_backend.api.controllers.sample.files import sample_files_controller
from mascope_backend.api.controllers.samples import samples_controller
from mascope_backend.api.models.sample.files.sample_file_pydantic_model import (
    GetSampleFilePeakTimeseriesBody,
)
from mascope_backend.api.models.samples.sample_pydantic_model import (
    GetSamplePeakTimeseriesBody,
)
from mascope_backend.api.routes.sample.files import sample_files_routes
from mascope_backend.api.routes.sample.files.sample_files_routes import (
    get_sample_file_peak_timeseries_route,
)
from mascope_backend.api.routes.samples.samples_routes import (
    get_sample_peak_timeseries_route,
)


FILENAME = "OrbiTest_1001.01.01_12h00m00s_TestFile"
TIME = [0.0, 1.0, 2.0, 3.0]
HEIGHTS = {
    100.0: [10.0, 20.0, 30.0, 40.0],
    200.0: [np.nan] * 4,  # timeseries not computed yet
    300.0: [5.0, np.nan, 7.0, np.nan],
}
USER = SimpleNamespace(to_dict=lambda: {"id": "user_1"})

CASES = [
    pytest.param(100.0, [10.0, 20.0, 30.0, 40.0], id="finite"),
    pytest.param(200.0, [None] * 4, id="all-nan"),
    pytest.param(300.0, [5.0, None, 7.0, None], id="some-nan"),
]


def strict_loads(body: bytes):
    """Parse like a browser's ``JSON.parse``: reject NaN/Infinity literals."""

    def reject(literal):
        raise ValueError(f"non-strict JSON literal: {literal}")

    return json.loads(body, parse_constant=reject)


def peak_dataset() -> xr.Dataset:
    """A stored peak timeseries, as ``load_peak_data`` returns it."""
    mzs = list(HEIGHTS)
    heights = np.array([HEIGHTS[mz] for mz in mzs])
    return xr.Dataset(
        {
            "peak_heights": (("mz", "time"), heights),
            "peak_areas": (("mz", "time"), heights * 2),
        },
        coords={
            "mz": mzs,
            "time": TIME,
            "peak_id": ("mz", [f"peak_{int(mz)}" for mz in mzs]),
        },
        attrs={"props": {"filename": FILENAME}},
    )


@pytest.fixture(autouse=True)
def raw_backed_file():
    """A file with its source data file, so ``get_peaks`` keeps all-NaN peaks."""
    with patch.object(
        peak_module.m_name, "get_sample_file_type", return_value="orbi_raw"
    ):
        yield


@pytest.mark.parametrize("peak_mz, heights", CASES)
@pytest.mark.asyncio
async def test_sample_file_route_sends_nan_heights_as_null(peak_mz, heights):
    with (
        patch.object(
            sample_files_routes, "check_sample_file_instrument_access", AsyncMock()
        ),
        patch.object(
            sample_files_controller,
            "get_sample_file",
            AsyncMock(return_value={"data": {"filename": FILENAME}}),
        ),
        patch.object(
            sample_files_controller, "load_peak_data", return_value=peak_dataset()
        ),
        captured_logs("WARNING") as records,
    ):
        response = await get_sample_file_peak_timeseries_route(
            sample_file_id="sf_1",
            body=GetSampleFilePeakTimeseriesBody(
                peak_mz=peak_mz, peak_mz_tolerance_ppm=1
            ),
            user=USER,
        )

    assert response.status_code == 200
    data = strict_loads(response.body)["data"]
    assert data == {"mz": peak_mz, "height": heights, "time": TIME}
    # Nulled by the route, not by the render's non-finite fallback.
    assert records == []


@pytest.mark.parametrize("peak_mz, heights", CASES)
@pytest.mark.asyncio
async def test_sample_route_sends_nan_heights_as_null(peak_mz, heights):
    sample = SimpleNamespace(
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
        patch.object(
            samples_controller.m_compute,
            "get_scan_timestamps",
            return_value=np.array(TIME),
        ),
        patch.object(
            samples_controller.m_compute,
            "load_peak_timeseries",
            AsyncMock(return_value=peak_dataset()),
        ),
        captured_logs("WARNING") as records,
    ):
        response = await get_sample_peak_timeseries_route(
            sample_item_id="si_1",
            body=GetSamplePeakTimeseriesBody(peak_mz=peak_mz, peak_mz_tolerance_ppm=1),
            user=USER,
            membership=None,
        )

    assert response.status_code == 200
    data = strict_loads(response.body)["data"]
    assert data == {
        "peak_id": f"peak_{int(peak_mz)}",
        "mz": peak_mz,
        "height": heights,
        "time": TIME,
    }
    assert records == []
