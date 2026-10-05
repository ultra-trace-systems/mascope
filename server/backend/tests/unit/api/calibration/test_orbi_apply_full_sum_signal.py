"""
The full sum signal of a raw Orbitrap file through an m/z calibration.

Two things hold it together. A raw Orbitrap file caches its sum signals as
``sum_signal.<reader>`` (see ``mascope_signal.compute.sum_signal_suffix``), so
a file processed under that naming has no store called plain ``sum_signal``:
``apply`` has to ask for the full sum signal rather than open it by name. And
applying a calibration rescales every stored sum signal in place, so a stored
axis is the acquisition axis times the file's current calibration factor -
including a full signal averaged long after the file was calibrated, as every
one is once a new reader renames the cache. The first half is pinned with the
stores patched out, the second against real stores in a temporary filestore.
"""

import json
import os
import uuid
from unittest.mock import MagicMock, patch

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


FILENAME = "OrbiTest_1001.01.01_12h00m00s_TestFile"
MODULE = "mascope_backend.api.controllers.calibration.lib.calibration_mz_fit"
CACHED = "sum_signal.otf2.0.0-g4"


def _handler(filename: str = FILENAME) -> OrbiCalibrationHandler:
    """A handler as far as apply needs one: the fit is handed in, so the file
    is all it reads."""
    return OrbiCalibrationHandler(filename)


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
        patch(f"{MODULE}.m_io") as m_io_,
        patch(f"{MODULE}.m_name") as m_name_,
        patch(f"{MODULE}.m_compute") as m_compute_,
        patch(f"{MODULE}.runtime") as runtime,
    ):
        m_compute_.get_sum_signal.return_value = full
        m_io_.load_coord.side_effect = _load_coord
        m_io_.get_file_data_vars.return_value = [CACHED, "peak_timeseries"]
        m_io_.read_props.return_value = {"mz_calibration": None}
        m_name_.get_sample_file_type.return_value = "orbi_raw"
        runtime.logger = MagicMock()

        await _handler().apply(fit)

        # The reader-named cache is recalibrated like any sum signal...
        updated = [c.args[1] for c in m_io_.update_zarr_array_coord.call_args_list]
        assert CACHED in updated
        # ...and the range comes from the full sum signal, asked for by file
        m_compute_.get_sum_signal.assert_called_with(FILENAME)
        props = m_io_.update_props.call_args.args[1]
        assert props["range"] == pytest.approx((100.1, 200.2))


@pytest.mark.asyncio
async def test_orbi_apply_already_applied_returns_the_full_sum_signal():
    full = xr.DataArray(
        np.zeros(8), dims="mz", coords={"mz": np.linspace(100.1, 200.2, 8)}
    )
    fit = {"par": {"old_factor_scaling": 1.0, "calibration_factor": 1.001}}
    with (
        patch(f"{MODULE}.m_io") as m_io_,
        patch(f"{MODULE}.m_name"),
        patch(f"{MODULE}.m_compute") as m_compute_,
        patch(f"{MODULE}.runtime") as runtime,
    ):
        m_compute_.get_sum_signal.return_value = full
        m_io_.load_coord.side_effect = _load_coord
        # The same calibration is on the file already, so apply short-circuits
        m_io_.read_props.return_value = {
            "mz_calibration": {"par": {"calibration_factor": 1.001}}
        }
        runtime.logger = MagicMock()

        result = await _handler().apply(fit)

        np.testing.assert_allclose(result, np.linspace(100.1, 200.2, 8))
        m_io_.update_zarr_array_coord.assert_not_called()


# --- Against real stores ---

#: The axis the raw reader averages, every time it is asked.
ACQUISITION_MZ = np.array([100.0, 200.0, 300.0])
#: The one-point calibration on the file: 3 ppm.
FACTOR = 1.000003


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
def calibrated_raw_orbitrap_file(tmp_path, monkeypatch):
    """A raw Orbitrap sample file in a temporary filestore, calibrated with
    FACTOR, with no sum signal cached under the current reader: every such
    file, the first time it is read after an upgrade. The raw reader is stubbed
    to average ACQUISITION_MZ."""
    monkeypatch.setattr(
        file_runtime, "filestore", lambda *parts: os.path.join(tmp_path, *parts)
    )
    filename = f"OrbiApply_1001.01.01_12h00m00s_{uuid.uuid4().hex[:8]}"
    sample_dir = m_name.parse_path_from_item_filename(filename)
    os.makedirs(sample_dir)
    # The data file only has to be there: it is what makes the file raw
    open(os.path.join(sample_dir, "data.raw"), "wb").close()
    calibration = {"mode": "one-point", "par": {"calibration_factor": FACTOR}}
    with open(os.path.join(sample_dir, ".props"), "w") as f:
        json.dump({"instrument_type": "orbi", "mz_calibration": calibration}, f)

    def average(datafile_path, **kwargs):
        profile = xr.DataArray(
            np.ones(ACQUISITION_MZ.size),
            dims=["mz"],
            coords={"mz": ACQUISITION_MZ},
            name="sum_signal",
        )
        return profile, 1

    monkeypatch.setattr(m_compute.m_thermo, "compute_sum_signal", average)
    return filename


def _stored_full_mz(filename: str) -> np.ndarray:
    """The m/z axis of the full sum signal as stored, read off the store."""
    name = m_compute._get_sum_signal_hash_name(None, None, None, "orbi_raw")
    return m_io.load_coord(filename, name, "mz")


@pytest.mark.asyncio
async def test_reset_and_recalibration_keep_the_full_signal_on_the_current_axis(
    calibrated_raw_orbitrap_file,
):
    filename = calibrated_raw_orbitrap_file

    # First read after the upgrade: on the calibrated axis, not the acquisition
    first = m_compute.get_sum_signal(filename)
    np.testing.assert_allclose(first.mz.values, ACQUISITION_MZ * FACTOR, atol=1e-9)

    # A reset divides the factor out of every store
    await _handler(filename).apply(_one_point(FACTOR, 1.0))
    m_io.update_props(filename, {"mz_calibration": None})
    np.testing.assert_allclose(_stored_full_mz(filename), ACQUISITION_MZ, atol=1e-9)

    # ...and calibrating from scratch finds the same factor and puts it back
    await _handler(filename).apply(_one_point(1.0, FACTOR))
    expected = ACQUISITION_MZ * FACTOR
    np.testing.assert_allclose(_stored_full_mz(filename), expected, atol=1e-9)
    # A window averaged now lands on the same axis as the full signal
    window = m_compute.get_sum_signal(filename, 0.0, 2.0, "+")
    np.testing.assert_allclose(window.mz.values, expected, atol=1e-9)


@pytest.mark.asyncio
async def test_a_full_signal_first_averaged_by_apply_is_rescaled_with_the_rest(
    calibrated_raw_orbitrap_file,
):
    """Apply reads the full signal for the file's range. Averaged only after
    the stores were rescaled, it would be put on the factor the properties
    still hold - the old one - and kept there."""
    filename = calibrated_raw_orbitrap_file
    new_factor = FACTOR * 1.000002

    await _handler(filename).apply(_one_point(FACTOR, new_factor))

    stored = _stored_full_mz(filename)
    np.testing.assert_allclose(stored, ACQUISITION_MZ * new_factor, atol=1e-9)
    props = m_io.read_props(filename)
    assert props["mz_calibration"]["par"]["calibration_factor"] == new_factor
    assert props["range"] == pytest.approx((stored[0], stored[-1]))
