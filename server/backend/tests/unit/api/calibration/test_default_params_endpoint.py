"""Unit tests for the default-calibration-params controller."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from mascope_backend.api.controllers.calibration.calibration_controller import (
    get_default_calibration_params,
)


_CTRL = "mascope_backend.api.controllers.calibration.calibration_controller"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("filename", "refine_window", "snr_threshold"),
    [
        ("orbitrap_sample", 50, 50.0),
        ("tofwerk_sample", 100, 10.0),
    ],
)
async def test_returns_instrument_defaults(filename, refine_window, snr_threshold):
    sample = SimpleNamespace(filename=filename)
    with patch(f"{_CTRL}.fetch_sample", AsyncMock(return_value=sample)):
        result = await get_default_calibration_params(sample_item_id="si-1")

    params = result["data"]["params"]
    assert params["refine_window"] == refine_window
    assert params["snr_threshold"] == snr_threshold
    # The full parameter set is exposed so the dialog can seed every field.
    assert "mz_error_tolerance" in params
    assert "isotope_abundance_min" in params
