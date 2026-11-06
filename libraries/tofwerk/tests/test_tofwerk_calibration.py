"""
``mz_calibrate`` fits Tofwerk's mode 0 and its three-parameter mode 2.

Mode 0 is ``i = p1 * sqrt(m) + p2``, with ``i`` the TOF sample index; mode 2
is ``i = p1 * m**p3 + p2``, mode 0 at p3 = 0.5, which bends the axis where a
square root leaves a bow across the calibrants. A mode-2 fit starts from
mode 0's solution and p3 = 0.5, under the same soft-L1 loss, and steps in
p3 at a scale of its own: p1 and p2 are thousands of samples, p3 is 0.5.
"""

import numpy as np
import pytest
from scipy.optimize import least_squares

from mascope_tofwerk import calibration
from mascope_tofwerk.calibration import mz_calibrate, tof_to_mass


#: Calibrant m/z from 41 to 228 Th, as a positive-ion CIMS set spreads them.
MZ = np.array([41.039, 59.073, 86.096, 148.144, 228.196])


def _tof(par: list[float], mz: np.ndarray = MZ) -> np.ndarray:
    """The TOF index a mode-2 axis puts each m/z at."""
    p1, p2, p3 = par
    return p1 * mz**p3 + p2


def _ppm(mz: np.ndarray, exact: np.ndarray = MZ) -> np.ndarray:
    return (mz - exact) / exact * 1e6


@pytest.mark.parametrize(
    "par",
    [
        pytest.param([10850.0, -18500.0, 0.4995], id="slightly below a square root"),
        pytest.param([10850.0, -18500.0, 0.52], id="well above it"),
        pytest.param([13515.0, -42419.0, 0.48], id="another instrument"),
        pytest.param([2832.0, -158.0, 0.503], id="an offset near zero"),
    ],
)
def test_mode_2_recovers_known_parameters(par):
    tof = _tof(par)
    observed = MZ * (1 + 40e-6)

    fit, stats = mz_calibrate(tof, observed, MZ, mode=2)

    assert fit["mode"] == 2
    np.testing.assert_allclose(fit["par"], par, rtol=1e-9)
    np.testing.assert_allclose(stats["new_mz"], MZ, rtol=1e-12)
    np.testing.assert_allclose(stats["post_dmz"], 0.0, atol=1e-6)
    np.testing.assert_allclose(stats["pre_dmz"], 40.0, rtol=1e-9)
    # The record evaluates back to the same axis
    np.testing.assert_allclose(
        tof_to_mass(tof, fit["mode"], fit["par"]), MZ, rtol=1e-12
    )


def test_mode_2_needs_its_scale_for_p3():
    """The default scale, one for every parameter, stops the fit hundreds of
    ppm away from points it could pass through exactly."""
    par = [10850.0, -18500.0, 0.52]
    tof = _tof(par)
    mode0, _ = mz_calibrate(tof, MZ, MZ, mode=0)

    unscaled = least_squares(
        calibration._residuals_mode2,
        x0=[*mode0["par"], 0.5],
        args=(MZ, tof),
        method="trf",
        loss="soft_l1",
        max_nfev=1000,
    )
    fit, stats = mz_calibrate(tof, MZ, MZ, mode=2)

    assert np.abs(_ppm(tof_to_mass(tof, 2, unscaled.x))).max() > 100.0
    assert np.abs(stats["post_dmz"]).max() < 1e-6


def test_mode_2_starts_from_mode_0():
    """On points a square root places exactly, mode 2 stays where mode 0
    ends: p3 at 0.5 and the same axis."""
    tof = 10850.0 * np.sqrt(MZ) - 18500.0

    mode0, _ = mz_calibrate(tof, MZ, MZ, mode=0)
    mode2, stats = mz_calibrate(tof, MZ, MZ, mode=2)

    assert mode2["par"][2] == pytest.approx(0.5, abs=1e-12)
    np.testing.assert_allclose(mode2["par"][:2], mode0["par"], rtol=1e-9)
    np.testing.assert_allclose(stats["post_dmz"], 0.0, atol=1e-6)


def test_mode_2_takes_out_the_bow_mode_0_leaves():
    """Points on a bent axis: mode 0 leaves a bow of several ppm across
    them, mode 2 none."""
    tof = _tof([10850.0, -18500.0, 0.4995])

    _, mode0 = mz_calibrate(tof, MZ, MZ, mode=0)
    _, mode2 = mz_calibrate(tof, MZ, MZ, mode=2)

    assert np.abs(mode0["post_dmz"]).max() > 1.0
    assert np.abs(mode2["post_dmz"]).max() < 1e-6


def test_mode_2_soft_l1_outvotes_a_far_point():
    """The same robust loss as mode 0: one point 400 ppm off, 20 samples
    of index, pulls the others 7 ppm at most, where plain least squares
    lets it pull them almost 100."""
    par = [10850.0, -18500.0, 0.4995]
    mz = np.array([41.039, 59.073, 74.06, 86.096, 117.1, 148.144, 228.196])
    tof = _tof(par, mz)
    tof[3] = _tof(par, mz[3:4] * (1 + 400e-6))[0]
    others = np.delete(np.arange(mz.size), 3)

    _, robust = mz_calibrate(tof, mz, mz, mode=2)
    mode0, _ = mz_calibrate(tof, mz, mz, mode=0)
    plain = least_squares(
        calibration._residuals_mode2,
        x0=[*mode0["par"], 0.5],
        args=(mz, tof),
        method="trf",
        x_scale=[par[0], -par[1], calibration.MODE2_P3_SCALE],
    )

    assert np.abs(robust["post_dmz"][others]).max() < 10.0
    assert np.abs(_ppm(tof_to_mass(tof, 2, plain.x), mz)[others]).max() > 50.0


@pytest.mark.parametrize(
    "mode, points, message",
    [
        (0, 1, "At least two calibration points are required for mode 0."),
        (2, 2, "At least three calibration points are required for mode 2."),
    ],
)
def test_too_few_points_for_the_mode(mode, points, message):
    tof = _tof([10850.0, -18500.0, 0.5])[:points]

    with pytest.raises(ValueError, match=message):
        mz_calibrate(tof, MZ[:points], MZ[:points], mode=mode)


def test_mode_2_fits_as_many_points_as_it_has_parameters():
    """Three points determine mode 2 exactly: the fit passes through them,
    which is why a caller never judges it by its own residuals."""
    par = [10850.0, -18500.0, 0.52]

    fit, stats = mz_calibrate(_tof(par)[:3], MZ[:3], MZ[:3], mode=2)

    np.testing.assert_allclose(fit["par"], par, rtol=1e-9)
    np.testing.assert_allclose(stats["post_dmz"], 0.0, atol=1e-6)


def test_a_mode_2_fit_that_does_not_converge_is_refused(monkeypatch):
    real = calibration.least_squares

    def mode2_fails(fun, *args, **kwargs):
        result = real(fun, *args, **kwargs)
        if fun is calibration._residuals_mode2:
            result.success = False
            result.message = "The maximum number of function evaluations is exceeded."
        return result

    monkeypatch.setattr(calibration, "least_squares", mode2_fails)
    tof = _tof([10850.0, -18500.0, 0.5])

    assert mz_calibrate(tof, MZ, MZ, mode=0)[0]["mode"] == 0
    with pytest.raises(ValueError, match="Calibration failed: The maximum number"):
        mz_calibrate(tof, MZ, MZ, mode=2)


@pytest.mark.parametrize("mode", [1, 3, 4, 5])
def test_a_mode_it_does_not_fit_is_refused(mode):
    """``tof_to_mass`` evaluates them; nothing fits them."""
    with pytest.raises(ValueError, match=f"mode {mode} cannot be fitted"):
        mz_calibrate(_tof([10850.0, -18500.0, 0.5]), MZ, MZ, mode=mode)


def test_mode_0_is_the_default():
    tof = 10850.0 * np.sqrt(MZ) - 18500.0

    fit, _ = mz_calibrate(tof, MZ, MZ)

    assert fit["mode"] == 0
    np.testing.assert_allclose(fit["par"], [10850.0, -18500.0], rtol=1e-9)
