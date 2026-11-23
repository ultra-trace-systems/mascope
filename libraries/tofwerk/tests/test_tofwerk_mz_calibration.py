"""
The acquisition's mass calibration as a TofDaq file records it
(``read_mz_calibration``): the converter stores it as a file's first
calibration record, and a fit's record keeps it as its acquisition axis.
"""

import h5py
import numpy as np
import pytest

from mascope_tofwerk.tofwerk import open_h5_file, read_mz_calibration


def _write(path, mode, par, wrap):
    with h5py.File(path, "w") as h5_file:
        attrs = h5_file.create_group("FullSpectra").attrs
        attrs["MassCalibMode"] = wrap(mode)
        attrs["MassCalibration nbrParameters"] = wrap(len(par))
        for i, value in enumerate(par):
            attrs[f"MassCalibration p{i + 1}"] = wrap(value)


@pytest.mark.parametrize(
    "wrap",
    [lambda value: np.array([value]), lambda value: value],
    ids=["one-element arrays", "scalars"],
)
def test_reads_the_mode_and_its_parameters(tmp_path, wrap):
    """TofDaq writes each attribute as a one-element array; some writers as
    a bare scalar."""
    path = tmp_path / "data.h5"
    _write(path, 2, [2830.5, -4200.25, 0.4999], wrap)

    with open_h5_file(str(path)) as h5_file:
        calibration = read_mz_calibration(h5_file)

    assert calibration == {"mode": 2, "par": [2830.5, -4200.25, 0.4999]}
    assert type(calibration["mode"]) is int
    assert all(type(value) is float for value in calibration["par"])


def test_reads_as_many_parameters_as_the_file_says(tmp_path):
    path = tmp_path / "data.h5"
    _write(path, 0, [2830.5, -4200.25], np.array)
    with h5py.File(path, "a") as h5_file:
        h5_file["FullSpectra"].attrs["MassCalibration p3"] = np.array([9.0])

    with open_h5_file(str(path)) as h5_file:
        assert read_mz_calibration(h5_file)["par"] == [2830.5, -4200.25]


def test_a_file_without_a_calibration_is_refused(tmp_path):
    path = tmp_path / "data.h5"
    with h5py.File(path, "w") as h5_file:
        h5_file.create_group("FullSpectra")

    with open_h5_file(str(path)) as h5_file, pytest.raises(KeyError):
        read_mz_calibration(h5_file)
