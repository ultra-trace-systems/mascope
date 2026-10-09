"""The sample spectrum and the match view's profile read the polarity's
signal as the sample does - stitched where the store stitches it, pooled
otherwise (``mascope_signal.compute.get_sample_sum_signal``) - and not the
pooled signal whatever the store. The listed peaks of a stitched file are
averaged over their own stream's scans; a profile averaged over every scan
of the polarity would sit under them by each stream's share of the scans.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import numpy as np
import pytest
import xarray as xr

from mascope_backend.api.controllers.samples import samples_controller
from mascope_backend.api.controllers.visualization import visualization_controller


def _signal() -> xr.DataArray:
    return xr.DataArray(
        np.array([1.0, 2.0, 3.0]), dims=("mz",), coords={"mz": [100.0, 100.5, 101.0]}
    )


@pytest.mark.asyncio
async def test_the_sample_spectrum_asks_for_the_samples_signal():
    sample = SimpleNamespace(
        filename="F", t0=0.0, t1=10.0, polarity="-", sample_item_name="S"
    )
    asked = []

    def _sample_signal(filename, polarity, t_min, t_max, average):
        asked.append((filename, polarity, t_min, t_max, average))
        return _signal()

    with (
        patch.object(
            samples_controller, "fetch_sample", AsyncMock(return_value=sample)
        ),
        patch.object(
            samples_controller.m_compute, "get_sample_sum_signal", _sample_signal
        ),
        patch.object(
            samples_controller.m_compute,
            "get_sum_signal",
            side_effect=AssertionError("the pooled signal was asked"),
        ),
    ):
        body = await samples_controller.get_sample_spectrum("si_1")

    assert asked == [("F", "-", 0.0, 10.0, True)]
    assert body["data"]["mz"] == [100.0, 100.5, 101.0]
    assert body["data"]["intensity"] == [1.0, 2.0, 3.0]


@pytest.mark.asyncio
async def test_the_match_views_profile_is_the_samples_signal():
    sample = SimpleNamespace(filename="F", t0=0.0, t1=10.0, polarity="-")
    isotopes = [SimpleNamespace(sample_peak_mz=100.5)]
    asked = []

    def _sample_signal(filename, polarity, t_min, t_max, average):
        asked.append((filename, polarity, t_min, t_max, average))
        return _signal()

    store = xr.Dataset(
        {"peak_heights": (("mz", "time"), np.ones((1, 2)))},
        coords={"mz": [100.5], "time": [0.0, 1.0]},
    )
    with (
        patch.object(
            visualization_controller, "get_instrument_type", return_value="orbi"
        ),
        patch.object(
            visualization_controller.m_compute, "get_sample_sum_signal", _sample_signal
        ),
        patch.object(
            visualization_controller.m_compute,
            "get_sum_signal",
            side_effect=AssertionError("the pooled signal was asked"),
        ),
        patch.object(
            visualization_controller.m_io, "load_peak_data", return_value=store
        ),
        patch.object(
            visualization_controller.m_compute,
            "load_peak_timeseries",
            AsyncMock(return_value=store),
        ),
        patch.object(
            visualization_controller.m_compute,
            "get_scan_timestamps",
            return_value=np.array([0.0, 1.0]),
        ),
        patch.object(
            visualization_controller, "get_peaks", lambda data, _kind: data.peak_heights
        ),
        patch.object(visualization_controller.m_io, "read_props", return_value={}),
    ):
        (
            _ts,
            _means,
            signal,
        ) = await visualization_controller._load_peaks_and_averaged_signal(
            sample, isotopes
        )

    assert asked == [("F", "-", 0.0, 10.0, True)]
    # The sample's signal, cut to the window around the isotope
    assert signal.values.tolist() == [2.0]
