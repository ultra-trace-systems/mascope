"""
``mz_calibrate`` on real TOF calibrants: one specimen per reagent class.

Each fixture in ``data/`` is derived from a real TOF file (``data/README.md``).
Its ``fit`` holds the lines the calibration node chose from, each with its
peak's TOF index, and which of them it kept. Refitting the kept lines in the
fit's mode must give the node's parameters and its residual at every line,
kept or not: what the model and its loss make of real points, where the
synthetic tests place them on an exact axis.

Real calibrants sit within a few hundredths of a TOF sample of the fit, where
the soft-L1 loss is least squares: no loss tells them apart. So the loss is
pinned on a specimen with one line misread, the case it is there for.
"""

import json
from pathlib import Path

import numpy as np
import pytest

from mascope_tofwerk.calibration import mz_calibrate, tof_to_mass


DATA = Path(__file__).parent / "data"
SPECIMENS = ("bromide", "nitrate", "propylamine")

#: How far a refit may move a line, in ppm.
SAME_AXIS_PPM = 0.01
#: How far a line's m/z on the fixture's axis may be from the node's reading
#: of it: the axis is the converter's record on the store's TOF index, which
#: the summed spectrum's axis follows to a few hundredths of a ppm.
READING_PPM = 0.05


def _specimen(kind: str) -> dict:
    with open(DATA / f"tof_specimen_{kind}.json") as fixture:
        return json.load(fixture)


def _ppm(mz, exact):
    return (np.asarray(mz) - exact) / exact * 1e6


@pytest.mark.parametrize("kind", SPECIMENS)
def test_a_refit_of_the_kept_lines_is_the_node_fit(kind):
    fit = _specimen(kind)["fit"]
    points = fit["points"]
    kept = [point for point in points if point["retained"]]
    tof = np.array([point["tof"] for point in kept])
    exact = np.array([point["target_mz"] for point in kept])
    observed = np.array([point["observed_mz"] for point in kept])

    refit, stats = mz_calibrate(tof, observed, exact, mode=fit["mode"])

    assert refit["mode"] == fit["mode"]
    np.testing.assert_allclose(refit["par"], fit["par"], rtol=1e-7)
    # Every line's residual, the ones the node left out included
    every_tof = np.array([point["tof"] for point in points])
    every_exact = np.array([point["target_mz"] for point in points])
    residuals = _ppm(tof_to_mass(every_tof, refit["mode"], refit["par"]), every_exact)
    for point, residual in zip(points, residuals):
        assert residual == pytest.approx(point["residual_ppm"], abs=SAME_AXIS_PPM), (
            point["ion"]
        )
    np.testing.assert_allclose(
        stats["post_dmz"],
        [point["residual_ppm"] for point in kept],
        atol=SAME_AXIS_PPM,
    )


@pytest.mark.parametrize("kind", SPECIMENS)
def test_a_specimen_s_points_are_its_peaks(kind):
    """The fit's points are peaks of the fixture, which read on its axis
    what the node read at them."""
    found = _specimen(kind)
    axis = found["axis"]
    peaks = {round(peak["tof"], 6): peak for peak in found["peaks"]}
    for point in found["fit"]["points"]:
        peak = peaks[round(point["tof"], 6)]
        assert peak["mz"] == pytest.approx(point["observed_mz"], rel=READING_PPM * 1e-6)
        assert tof_to_mass(np.array([peak["tof"]]), axis["mode"], axis["par"])[
            0
        ] == pytest.approx(peak["mz"], rel=1e-12)


def test_the_loss_holds_the_axis_against_a_misread_line():
    """The bromide specimen's kept lines with one of them, Br2- at 159.8 Th,
    read 400 ppm high: about 20 samples, where the loss is far from least
    squares. The other lines stay within 3.5 ppm; plain least squares would
    move them by 50 to 70."""
    fit = _specimen("bromide")["fit"]
    kept = [point for point in fit["points"] if point["retained"]]
    exact = np.array([point["target_mz"] for point in kept])
    tof = np.array([point["tof"] for point in kept])
    observed = np.array([point["observed_mz"] for point in kept])
    misread = int(np.argmin(np.abs(exact - 159.835)))
    p1, p2 = fit["par"]
    tof[misread] = p1 * np.sqrt(exact[misread] * (1 + 400e-6)) + p2

    refit, stats = mz_calibrate(tof, observed, exact, mode=0)

    np.testing.assert_allclose(
        refit["par"], [10866.454862404576, -34362.562832268384], rtol=1e-7
    )
    others = np.delete(stats["post_dmz"], misread)
    assert np.abs(others).max() < 3.5
    assert stats["post_dmz"][misread] == pytest.approx(397.5, abs=0.1)
