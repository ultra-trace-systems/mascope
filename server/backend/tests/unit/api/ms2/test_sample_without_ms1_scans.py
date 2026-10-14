"""
A sample with no MS1 scans answers its MS1 reads as empty, not as failed.

An acquisition of fragmentation scans alone is imported as a sample, and that
sample has no MS1 scan in any window. The raw readers raise for a selection
that holds no scans, so its spectrum and its MS1 centroids used to come back
400 with the reader's own message - a scan filter, quoted at whoever opened
the sample. Two clients paid for it: the browser showed an error and a chart
that never finished loading, and the MS2 notebook toolkit, which reads the
precursor's intensity from the MS1 centroids and handles an empty answer, met
an exception instead.

Both now answer what is true of the sample: there is no MS1 spectrum. What
must not follow is that every error becomes an empty answer - a window that is
the wrong way round is still the request's fault.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import numpy as np
import pytest

from mascope_backend.api.controllers.samples import samples_controller
from mascope_backend.api.lib.exceptions.api_exceptions import ApiException
from mascope_backend.api.new.ms2 import service as ms2_service
from mascope_thermo.thermo import InvalidRangeError, NoScansFoundError


_SAMPLES = "mascope_backend.api.controllers.samples.samples_controller"
_MS2 = "mascope_backend.api.new.ms2.service"

#: As an MS2 part of a file with no MS1 scans is stored: its window starts at
#: its first fragment scan.
SAMPLE = SimpleNamespace(
    filename="OrbiTest_2026.01.01-12h00m00s_ms2",
    sample_item_name="2026-01-01 12:00:00",
    t0=35.1,
    t1=206.2,
    polarity="+",
)

_NO_MS1 = NoScansFoundError(
    "No scans found matching the specified filters: polarity='+', "
    "time_range=(35.1, 206.2), ms_type='Ms'"
)


async def _spectrum(sum_signal):
    """The spectrum route's answer when the reader behaves as ``sum_signal``."""
    with (
        patch(f"{_SAMPLES}.fetch_sample", AsyncMock(return_value=SAMPLE)),
        patch(f"{_SAMPLES}.m_compute.get_sample_sum_signal", sum_signal),
    ):
        return await samples_controller.get_sample_spectrum("si-1")


async def _ms1_centroids(centroids):
    """The MS1 centroid route's answer when the reader behaves as ``centroids``."""
    with (
        patch(f"{_MS2}.fetch_sample", AsyncMock(return_value=SAMPLE)),
        patch(f"{_MS2}.m_compute.get_orbi_centroids", centroids),
    ):
        return await ms2_service.get_ms1_averaged_centroids("si-1")


def _raises(error):
    def reader(*args, **kwargs):
        raise error

    return reader


@pytest.mark.asyncio
async def test_its_spectrum_is_empty():
    result = await _spectrum(_raises(_NO_MS1))

    assert result["results"] == 0
    assert (result["data"]["mz"], result["data"]["intensity"]) == ([], [])
    assert result["data"]["intensity_unit"] == "counts/s"


@pytest.mark.asyncio
async def test_the_empty_spectrum_says_why_and_quotes_no_scan_filter():
    result = await _spectrum(_raises(_NO_MS1))

    assert "No spectrum data found" in result["message"]
    assert "ms_type" not in result["message"]


@pytest.mark.asyncio
async def test_a_spectrum_request_that_is_wrong_still_fails():
    """Only an empty selection is an empty answer."""
    with pytest.raises(ApiException) as failed:
        await _spectrum(_raises(InvalidRangeError("Invalid time range")))

    assert failed.value.status_code == 400


@pytest.mark.asyncio
async def test_a_sample_with_ms1_scans_keeps_its_spectrum():
    import xarray as xr

    spectrum = xr.DataArray(
        np.array([10.0, 20.0]), dims="mz", coords={"mz": [100.0, 101.0]}
    )

    result = await _spectrum(lambda *args, **kwargs: spectrum)

    assert result["data"]["mz"] == [100.0, 101.0]
    assert result["data"]["intensity"] == [10.0, 20.0]


@pytest.mark.asyncio
async def test_its_ms1_centroids_are_empty():
    result = await _ms1_centroids(AsyncMock(side_effect=_NO_MS1))

    assert result["data"] == {
        "mz": [],
        "intensity": [],
        "resolution": [],
        "signal_to_noise": [],
    }
    assert "no MS1 scans" in result["message"]


@pytest.mark.asyncio
async def test_an_ms1_centroid_request_that_is_wrong_still_fails():
    with pytest.raises(ApiException) as failed:
        await _ms1_centroids(AsyncMock(side_effect=InvalidRangeError("Invalid ppm")))

    assert failed.value.status_code == 400


@pytest.mark.asyncio
async def test_a_sample_with_ms1_scans_keeps_its_centroids():
    masses = np.array([137.096, 138.099])
    centroids = (masses, masses * 10, masses * 100, masses * 2)

    result = await _ms1_centroids(AsyncMock(return_value=centroids))

    assert result["data"]["mz"] == [137.096, 138.099]
    assert len(result["data"]["signal_to_noise"]) == 2
