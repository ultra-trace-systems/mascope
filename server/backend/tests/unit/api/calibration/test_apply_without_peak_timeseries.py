"""
Applying a calibration to a sample file that has no peak_timeseries.zarr.

Both handlers warn and carry on rather than failing the calibration. The
exception they catch is load-bearing and changed with zarr 3: zarr 2 raised
`zarr.errors.PathNotFoundError`, a ValueError that did not cover the
`FileNotFoundError` `m_io.load_coord` raises for a path that is not there at
all. zarr 3 removed that class and raises `GroupNotFoundError`, a subclass of
`FileNotFoundError`, so one `except FileNotFoundError` now covers both.
"""

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from mascope_backend.api.controllers.calibration.lib.calibration_mz_fit import (
    OrbiCalibrationHandler,
    TofCalibrationHandler,
    calibration_params_factory,
)
from mascope_backend.api.models.calibration.calibration_pydantic_model import (
    CalibrationFitParams,
)


FILENAME = "OrbiTest_1001.01.01_12h00m00s_TestFile"


def _params(instrument: str) -> CalibrationFitParams:
    resolved = calibration_params_factory(filename=instrument)
    return CalibrationFitParams(
        calibration_collection_id="cc_1",
        ionization_mechanism_ids=["im_1"],
        polarity="+",
        **resolved.model_dump(),
    )


def _load_coord_missing_peak_timeseries(filename, var, coord_name):
    """Stand in for m_io.load_coord: every var resolves except peak_timeseries.

    Mirrors the real function, which raises FileNotFoundError as soon as the
    variable's directory is absent.
    """
    if var == "peak_timeseries":
        raise FileNotFoundError(f"{filename}/peak_timeseries.zarr")
    return np.linspace(100.0, 200.0, 8)


@pytest.mark.asyncio
async def test_orbi_apply_warns_when_peak_timeseries_is_absent():
    """OrbiCalibrationHandler.apply must not fail on a file with no peaks."""
    handler = OrbiCalibrationHandler(_params("orbitrap"), None)
    handler.filename = FILENAME
    fit = {
        "par": {
            "old_factor_scaling": 1.001,
            "old_factor": 1.0,
            "calibration_factor": 1.001,
        }
    }

    module = "mascope_backend.api.controllers.calibration.lib.calibration_mz_fit"
    with (
        patch(f"{module}.m_io") as m_io,
        patch(f"{module}.m_name") as m_name,
        patch(f"{module}.runtime") as runtime,
    ):
        m_io.load_coord.side_effect = _load_coord_missing_peak_timeseries
        m_io.get_file_data_vars.return_value = ["sum_signal"]
        # No calibration on the file yet, so apply() does not short-circuit
        m_io.read_props.return_value = {"mz_calibration": None}
        m_name.get_sample_file_type.return_value = "orbi_raw"
        runtime.logger = MagicMock()

        await handler.apply(fit)

        # The peak coordinate must be skipped, not written
        updated_vars = [c.args[1] for c in m_io.update_zarr_array_coord.call_args_list]
        assert "peak_timeseries" not in updated_vars
        # ...and the sum signal must still have been calibrated
        assert "sum_signal" in updated_vars
        assert runtime.logger.warning.called


@pytest.mark.asyncio
async def test_tof_apply_warns_when_peak_timeseries_is_absent():
    """TofCalibrationHandler.apply must not fail on a file with no peaks."""
    handler = TofCalibrationHandler(_params("tofwerk"), None)
    handler.filename = FILENAME
    fit = {"mode": "poly", "par": {"p": [0.0, 1.0]}}

    module = "mascope_backend.api.controllers.calibration.lib.calibration_mz_fit"
    with (
        patch(f"{module}.m_io") as m_io,
        patch(f"{module}.m_name") as m_name,
        patch(f"{module}.m_compute") as m_compute,
        patch(f"{module}.tof_to_mass") as tof_to_mass,
        patch(f"{module}.runtime") as runtime,
    ):
        m_compute.get_sum_signal.return_value = np.zeros(8)
        tof_to_mass.return_value = np.linspace(100.0, 200.0, 8)
        m_io.load_coord.side_effect = _load_coord_missing_peak_timeseries
        m_io.get_file_data_vars.return_value = ["sum_signal"]
        m_name.get_sample_file_type.return_value = "tof_h5"
        runtime.logger = MagicMock()

        result = await handler.apply(fit)

        assert result is not None
        updated_vars = [c.args[1] for c in m_io.update_zarr_array_coord.call_args_list]
        assert "peak_timeseries" not in updated_vars
        assert "sum_signal" in updated_vars
        assert runtime.logger.warning.called
