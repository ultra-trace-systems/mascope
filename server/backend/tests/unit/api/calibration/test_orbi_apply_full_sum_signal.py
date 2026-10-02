"""
Applying an Orbitrap calibration when the full sum signal is cached under the
name of the reader that averaged it.

A raw Orbitrap file's sum signals are cached as ``sum_signal.<reader>`` (see
``mascope_signal.compute.sum_signal_suffix``), so a file processed under that
naming has no store called plain ``sum_signal``. ``apply`` reads the full sum
signal for the file's m/z range, and has to ask for it rather than open a store
by a name that no longer exists.
"""

from unittest.mock import MagicMock, patch

import numpy as np
import pytest
import xarray as xr

from mascope_backend.api.controllers.calibration.lib.calibration_mz_fit import (
    OrbiCalibrationHandler,
)


FILENAME = "OrbiTest_1001.01.01_12h00m00s_TestFile"
MODULE = "mascope_backend.api.controllers.calibration.lib.calibration_mz_fit"
CACHED = "sum_signal.otf2.0.0-g2"


def _handler() -> OrbiCalibrationHandler:
    """A handler as far as apply needs one: the fit is handed in, so the file
    is all it reads."""
    return OrbiCalibrationHandler(FILENAME)


def _load_coord(filename, var, coord_name):
    """Stand in for m_io.load_coord over a file holding only the reader-named
    cache: any other name is absent, as the real function reports it."""
    if var != CACHED:
        raise FileNotFoundError(f"{filename}/{var}.zarr")
    return np.linspace(100.0, 200.0, 8)


@pytest.mark.asyncio
async def test_orbi_apply_takes_the_range_from_the_sum_signal_it_asks_for():
    full = xr.DataArray(
        np.zeros(8), dims="mz", coords={"mz": np.linspace(100.1, 200.2, 8)}
    )
    fit = {
        "par": {
            "old_factor_scaling": 1.001,
            "old_factor": 1.0,
            "calibration_factor": 1.001,
        }
    }
    with (
        patch(f"{MODULE}.m_io") as m_io,
        patch(f"{MODULE}.m_name") as m_name,
        patch(f"{MODULE}.m_compute") as m_compute,
        patch(f"{MODULE}.runtime") as runtime,
    ):
        m_compute.get_sum_signal.return_value = full
        m_io.load_coord.side_effect = _load_coord
        m_io.get_file_data_vars.return_value = [CACHED, "peak_timeseries"]
        m_io.read_props.return_value = {"mz_calibration": None}
        m_name.get_sample_file_type.return_value = "orbi_raw"
        runtime.logger = MagicMock()

        await _handler().apply(fit)

        # The reader-named cache is recalibrated like any sum signal...
        updated = [c.args[1] for c in m_io.update_zarr_array_coord.call_args_list]
        assert CACHED in updated
        # ...and the range comes from the full sum signal, asked for by file
        m_compute.get_sum_signal.assert_called_with(FILENAME)
        props = m_io.update_props.call_args.args[1]
        assert props["range"] == pytest.approx((100.1, 200.2))


@pytest.mark.asyncio
async def test_orbi_apply_already_applied_returns_the_full_sum_signal():
    full = xr.DataArray(
        np.zeros(8), dims="mz", coords={"mz": np.linspace(100.1, 200.2, 8)}
    )
    fit = {"par": {"old_factor_scaling": 1.0, "calibration_factor": 1.001}}
    with (
        patch(f"{MODULE}.m_io") as m_io,
        patch(f"{MODULE}.m_name"),
        patch(f"{MODULE}.m_compute") as m_compute,
        patch(f"{MODULE}.runtime") as runtime,
    ):
        m_compute.get_sum_signal.return_value = full
        m_io.load_coord.side_effect = _load_coord
        # The same calibration is on the file already, so apply short-circuits
        m_io.read_props.return_value = {
            "mz_calibration": {"par": {"calibration_factor": 1.001}}
        }
        runtime.logger = MagicMock()

        result = await _handler().apply(fit)

        np.testing.assert_allclose(result, np.linspace(100.1, 200.2, 8))
        m_io.update_zarr_array_coord.assert_not_called()
