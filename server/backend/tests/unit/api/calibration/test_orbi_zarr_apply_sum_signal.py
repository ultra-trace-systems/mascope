"""
The sum signals of an orbi_zarr file through an m/z calibration.

An Orbitrap sample that keeps no ``data.raw`` is an orbi_zarr file: its signal
is the stored ``signal.zarr``, and applying a calibration rescales that store
in place along with the sum signals and the peak store
(``OrbiCalibrationHandler``). Everything summed from the stored signal is
therefore on the calibrated axis already. A window averaged after a
calibration used to get the calibration factor on top, which put it one factor
off the file's peaks, its full signal and the windows cached before - and
every later apply rescaled it with the extra factor kept.

The sample is written to a temporary filestore and calibrated through
``OrbiCalibrationHandler.apply``, the path an accepted fit and a reset take.
"""

import json
import os
import uuid

import numpy as np
import pytest
import xarray as xr

import mascope_file.io as m_io
import mascope_file.name as m_name
import mascope_signal.compute as m_compute
from mascope_backend.api.controllers.calibration.lib.calibration_mz_fit import (
    OrbiCalibrationHandler,
)
from mascope_file.runtime import runtime as file_runtime


#: The acquisition axis of the stored signal, 1 mDa apart around its one peak.
ACQUISITION_MZ = np.round(np.linspace(299.99, 300.01, 21), 6)
#: The peak sits on a point of the axis, so the profile's top is the peak.
PEAK_MZ = 300.0
PEAK_HEIGHT = 1e6
SCAN_TIMES = np.arange(6, dtype=float)
#: A 3 ppm calibration, and a recalibration 2 ppm on top of it.
FACTOR = 1.000003
SECOND_FACTOR = FACTOR * 1.000002


def _one_point(old_factor: float, new_factor: float) -> dict:
    """The fit apply is handed to move a file from one factor to another."""
    return {
        "mode": "one-point",
        "par": {
            "old_factor": old_factor,
            "old_factor_scaling": new_factor / old_factor,
            "calibration_factor": new_factor,
        },
    }


@pytest.fixture
def orbi_zarr_file(tmp_path, monkeypatch):
    """An uncalibrated orbi_zarr sample file in a temporary filestore: its
    props, a stored signal holding one peak in every scan, and the peak store
    peak detection would have written for it."""
    monkeypatch.setattr(
        file_runtime, "filestore", lambda *parts: os.path.join(tmp_path, *parts)
    )
    filename = f"OrbiZarrApply_1001.01.01_12h00m00s_{uuid.uuid4().hex[:8]}"
    sample_dir = m_name.parse_path_from_item_filename(filename)
    os.makedirs(sample_dir)
    with open(os.path.join(sample_dir, ".props"), "w") as f:
        json.dump({"instrument_type": "orbi", "mz_calibration": None}, f)

    profile = PEAK_HEIGHT * np.exp(-0.5 * ((ACQUISITION_MZ - PEAK_MZ) / 0.002) ** 2)
    per_scan = np.repeat(profile[:, np.newaxis], SCAN_TIMES.size, axis=1)
    xr.Dataset(
        {"signal": (("mz", "time"), per_scan)},
        coords={"mz": ACQUISITION_MZ, "time": SCAN_TIMES},
    ).to_zarr(os.path.join(sample_dir, "signal.zarr"))

    heights = np.full((1, SCAN_TIMES.size), PEAK_HEIGHT)
    xr.Dataset(
        data_vars={
            "is_satellite": ("mz", [False]),
            "is_weak": ("mz", [False]),
            "is_timeseries_computed": ("mz", [True]),
            "sparsity": ("mz", [0.0]),
            "peak_areas": (("mz", "time"), heights),
            "peak_heights": (("mz", "time"), heights),
            "sum_peak_areas": ("mz", heights.sum(axis=1)),
            "sum_peak_heights": ("mz", heights.sum(axis=1)),
            "signal_to_noise": ("mz", [1000.0]),
            "polarity": ("mz", np.array(["+"], dtype="<U1")),
        },
        coords={
            "mz": [PEAK_MZ],
            "time": SCAN_TIMES,
            "tof": ("mz", [0.0]),
            "peak_id": ("mz", ["peak_0000"]),
        },
    ).to_zarr(os.path.join(sample_dir, "peak_timeseries.zarr"))

    # No data.raw beside the stores: the stored signal is all the file has
    assert m_name.get_sample_file_type(filename) == "orbi_zarr"
    return filename


def _window(filename: str, t_min: float, t_max: float) -> xr.DataArray:
    """A window's averaged profile, asked for as the spectrum views ask."""
    return m_compute.get_sum_signal(filename, t_min, t_max, "+", average=True)


def _assert_on_axis(mz: np.ndarray, expected: np.ndarray) -> None:
    np.testing.assert_allclose(mz, expected, rtol=0, atol=1e-9)


@pytest.mark.asyncio
async def test_a_window_averaged_after_calibration_sits_on_the_file_s_peaks(
    orbi_zarr_file,
):
    filename = orbi_zarr_file
    _assert_on_axis(_window(filename, 0.0, 2.0).mz.values, ACQUISITION_MZ)

    await OrbiCalibrationHandler(filename).apply(_one_point(1.0, FACTOR))

    # Apply rescaled the stored signal, the full sum signal, the peaks, and
    # the window cached before it
    calibrated = ACQUISITION_MZ * FACTOR
    _assert_on_axis(m_compute.load_signal(filename).mz.values, calibrated)
    _assert_on_axis(m_compute.get_sum_signal(filename).mz.values, calibrated)
    peak_mz = m_io.load_peak_data(filename).mz.values
    _assert_on_axis(peak_mz, [PEAK_MZ * FACTOR])
    _assert_on_axis(_window(filename, 0.0, 2.0).mz.values, calibrated)

    # A window first averaged now is on that axis too, its top on the peak
    window = _window(filename, 1.0, 3.0)
    _assert_on_axis(window.mz.values, calibrated)
    _assert_on_axis([window.mz.values[np.argmax(window.values)]], peak_mz)

    # ...and the peak's own trace is read off the stored signal at the peak
    trace = await m_compute.get_peak_timeseries(filename, peak_mz)
    _assert_on_axis(trace.mz.values, peak_mz)
    np.testing.assert_allclose(trace.values.ravel(), PEAK_HEIGHT)


@pytest.mark.asyncio
async def test_windows_stay_on_the_file_s_axis_through_recalibration_and_reset(
    orbi_zarr_file,
):
    """Every apply rescales the cached windows in place, so a window put off
    the axis when it is first averaged stays off it for good: through a
    recalibration, and through a reset to the acquisition axis."""
    filename = orbi_zarr_file
    await OrbiCalibrationHandler(filename).apply(_one_point(1.0, FACTOR))
    _window(filename, 0.0, 2.0)

    await OrbiCalibrationHandler(filename).apply(_one_point(FACTOR, SECOND_FACTOR))

    recalibrated = ACQUISITION_MZ * SECOND_FACTOR
    _assert_on_axis(_window(filename, 0.0, 2.0).mz.values, recalibrated)
    _assert_on_axis(_window(filename, 1.0, 3.0).mz.values, recalibrated)
    _assert_on_axis(m_io.load_peak_data(filename).mz.values, [PEAK_MZ * SECOND_FACTOR])

    # A reset divides the factor back out of every store and clears it
    await OrbiCalibrationHandler(filename).apply(_one_point(SECOND_FACTOR, 1.0))
    m_io.update_props(filename, {"mz_calibration": None})

    for t_min, t_max in ((0.0, 2.0), (1.0, 3.0), (2.0, 4.0)):
        _assert_on_axis(_window(filename, t_min, t_max).mz.values, ACQUISITION_MZ)
    _assert_on_axis(m_compute.get_sum_signal(filename).mz.values, ACQUISITION_MZ)
    _assert_on_axis(m_io.load_peak_data(filename).mz.values, [PEAK_MZ])
