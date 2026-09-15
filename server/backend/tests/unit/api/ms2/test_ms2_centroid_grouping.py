"""
The MS2 centroids route answers per parent peak unless asked to split by
activation.

Clients written against the route - the published SDK, notebooks, scripts -
read one spectrum per parent peak keyed by its bare m/z. Splitting a
stepped-energy acquisition into one spectrum per collision energy changes both
the keys and the spectra, so it is an option a caller asks for, and the default
keeps the shape those clients read.
"""

import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import numpy as np
import pytest

from mascope_backend.api.new.ms2 import service as ms2_service
from mascope_backend.api.new.ms2.schema import GetMs2CentroidsQueryParams


_SERVICE = "mascope_backend.api.new.ms2.service"

SAMPLE = SimpleNamespace(
    filename="OrbiTest_2026.01.01-12h00m00s_ms2",
    sample_item_name="MS2 sample",
    t0=0.0,
    t1=60.0,
    polarity="+",
)


def _spectrum(*mz: float):
    masses = np.array(mz)
    return (
        masses,
        np.full(masses.shape, 1000.0),
        np.full(masses.shape, 60000.0),
        np.full(masses.shape, 50.0),
    )


async def _centroids(groups: dict, **query) -> tuple[dict, AsyncMock]:
    compute = AsyncMock(return_value=groups)
    with (
        patch(f"{_SERVICE}.fetch_sample", AsyncMock(return_value=SAMPLE)),
        patch(f"{_SERVICE}.m_compute.get_orbi_ms2_centroids_by_parent", compute),
    ):
        result = await ms2_service.get_ms2_averaged_centroids("si-1", **query)
    return result, compute


def test_the_query_defaults_to_one_spectrum_per_parent_peak():
    assert GetMs2CentroidsQueryParams().by_activation is False


def test_the_route_can_hand_every_query_field_to_the_service():
    """The route passes ``model_dump()`` straight through as keyword arguments."""
    accepted = inspect.signature(ms2_service.get_ms2_averaged_centroids).parameters
    assert set(GetMs2CentroidsQueryParams.model_fields) <= set(accepted)


@pytest.mark.asyncio
async def test_by_default_spectra_are_keyed_by_the_bare_parent_mz():
    result, compute = await _centroids(
        {(137.096, ""): _spectrum(59.05, 77.04), (200.5, ""): _spectrum(95.1)}
    )

    assert compute.await_args.kwargs["by_activation"] is False
    assert list(result["data"]) == ["137.096", "200.5"]
    assert result["data"]["137.096"]["mz"] == [59.05, 77.04]
    assert "2 parent peaks" in result["message"]


@pytest.mark.asyncio
async def test_by_activation_keys_each_step_of_a_precursor():
    result, compute = await _centroids(
        {
            (137.096, "hcd20.00"): _spectrum(59.05),
            (137.096, "hcd40.00"): _spectrum(41.02),
        },
        by_activation=True,
    )

    assert compute.await_args.kwargs["by_activation"] is True
    assert list(result["data"]) == ["137.096@hcd20.00", "137.096@hcd40.00"]
    step = result["data"]["137.096@hcd40.00"]
    assert step["parent_peak_mz"] == 137.096
    assert step["activation"] == "hcd40.00"
    assert "2 parent peak groups" in result["message"]
