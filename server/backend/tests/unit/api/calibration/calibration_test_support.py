"""
Calibration handlers and peak data shared by the calibration API unit tests.

A plain module rather than conftest.py content, so the tests can import it:
`from conftest import ...` binds to whichever conftest pytest loaded last,
which in a run spanning several test directories is another directory's.
"""

import numpy as np
import xarray as xr

from mascope_backend.api.controllers.calibration.lib.calibration_mz_fit import (
    calibration_params_factory,
    get_calibration_handler,
)
from mascope_backend.api.models.calibration.calibration_pydantic_model import (
    CalibrationFitParams,
    MzCalibrationParams,
)
from mascope_backend.db.models import IonizationMode


def get_test_calibration_handler(filename: str, polarity: str):
    """Helper function to create a base calibration handler for testing."""
    ionization_mode = IonizationMode(
        ionization_mode_id="im_123",
        ionization_mode_name="Mock Ionization Mode",
        ionization_mode_token="mock_token",
        ionization_mode_polarity=polarity,
        ionization_mechanism_ids=["im_1", "im_2"],
        calibration_collection_id="calib_coll_123",
        diagnostic_collection_id="diag_coll_123",
    )

    mz_calibration_params = MzCalibrationParams(refine_window=100)
    default_calibration_params = calibration_params_factory(filename=filename)
    resolved_mz_params = mz_calibration_params.with_defaults(default_calibration_params)

    calibration_fit_params = CalibrationFitParams(
        calibration_collection_id=ionization_mode.calibration_collection_id,
        ionization_mechanism_ids=ionization_mode.ionization_mechanism_ids,
        polarity=ionization_mode.ionization_mode_polarity,
        **resolved_mz_params.model_dump(),
    )
    handler = get_calibration_handler(
        filename, calibration_fit_params, notification=None
    )
    return handler


def get_small_peak_data(polarity: str = "mixed"):
    mz = np.array([50.0, 100.0, 250.0, 300.0, 350.0, 400.0], dtype=np.float64)
    time = np.array([0.1, 0.5, 1.0, 1.5, 2.0, 2.5], dtype=np.float32)
    snr = np.array([3.0, 5.0, 20.0, 30.0, 60.0, 80.0], dtype=np.float32)
    if polarity == "+":
        polarity_var = np.array(["+"] * len(mz))
    elif polarity == "-":
        polarity_var = np.array(["-"] * len(mz))
    else:
        polarity_var = np.array(["-", "+", "-", "+", "+", "+"])

    return xr.Dataset(
        data_vars={"signal_to_noise": ("mz", snr), "polarity": ("mz", polarity_var)},
        coords=dict(mz=("mz", mz), time=("time", time)),
    )
