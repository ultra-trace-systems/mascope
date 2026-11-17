"""
The TOF calibration node on real data: one specimen per reagent class.

Each specimen is a derived fixture of a real TOF file
(``libraries/tofwerk/tests/data``): every peak near each calibrant line,
matched or not, the file's acquisition record, the calibrants, and the fit
the node made on the file when it was ingested. Written into a filestore,
it is the file as the node first read it.

Two checks per specimen. The node fits it as it did then: the same lines
kept and the same axis, to 0.01 ppm. And the calibration audit's refit,
which the plan measures every TOF against, is the node's fit to 0.01 ppm on
real data too, not only on the synthetic files of
``test_tof_calibration_audit.py``. A change to the matching window, the S/N
floor, the selection rule or the fit's loss moves the first; re-baseline the
specimens (the fixtures' README) only when such a change is meant.
"""

import copy
from unittest.mock import AsyncMock, patch

import numpy as np
import pytest
import tof_audit_support as support

import mascope_file.name as m_name
from mascope_backend.api.controllers.calibration.lib import (
    tof_calibration_audit as audit,
)
from mascope_backend.api.controllers.calibration.lib.calibration_mz_fit import (
    TofCalibrationHandler,
)
from mascope_tofwerk.calibration import tof_to_mass


NODE = "mascope_backend.api.controllers.calibration.lib.calibration_mz_fit"

#: How far two axes may part over the calibrants' span, in ppm.
SAME_AXIS_PPM = 0.01


@pytest.fixture
def filestore(tmp_path, monkeypatch):
    """A filestore of its own, so no test reads or writes a real one."""
    monkeypatch.setattr(m_name.runtime.meta, "filestore", str(tmp_path))
    monkeypatch.setattr(m_name, "_INSTRUMENT_TYPE", {})
    return tmp_path


async def _node_fit(filename, found):
    """What the node's own handler fits the specimen to."""
    handler = TofCalibrationHandler(filename, support.specimen_params(found))
    isotopes = support.specimen_isotopes(found).drop(columns="ion_label")
    with (
        patch.object(
            TofCalibrationHandler,
            "_resolve_calibration_isotopes",
            AsyncMock(return_value=isotopes),
        ),
        patch(
            f"{NODE}.read_instrument_functions",
            AsyncMock(return_value=({}, support.specimen_resolution(found))),
        ),
    ):
        await handler.fit()
    return handler


def _kept(handler) -> list[float]:
    """The exact m/z of the lines a fit kept, from its record's points."""
    return sorted(
        round(point["target_mz"], 6)
        for point in handler.fitted_on["points"]
        if point["retained"]
    )


def _tof_span(found) -> tuple[float, float]:
    tofs = [point["tof"] for point in found["fit"]["points"] if point["retained"]]
    return min(tofs), max(tofs)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", support.SPECIMENS)
async def test_the_node_fits_a_specimen_as_it_did_at_ingest(filestore, kind):
    found = support.specimen(kind)
    filename = support.write_specimen(filestore, found)

    node = await _node_fit(filename, found)

    assert node.fit_result is not None, node.warning
    expected = found["fit"]
    # The lines it chose from and the ones it kept
    assert [round(p["target_mz"], 6) for p in node.fitted_on["points"]] == [
        round(p["target_mz"], 6) for p in expected["points"]
    ]
    assert _kept(node) == sorted(
        round(p["target_mz"], 6) for p in expected["points"] if p["retained"]
    )
    # The axis
    assert node.fit_result["mode"] == expected["mode"]
    assert (
        audit.axis_difference_ppm(node.fit_result, expected, _tof_span(found))
        < SAME_AXIS_PPM
    )
    residuals = {
        round(p["target_mz"], 6): p["residual_ppm"] for p in node.fitted_on["points"]
    }
    for point in expected["points"]:
        assert residuals[round(point["target_mz"], 6)] == pytest.approx(
            point["residual_ppm"], abs=SAME_AXIS_PPM
        ), point["ion"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", support.SPECIMENS)
async def test_the_audit_refit_of_a_specimen_is_the_node_fit(filestore, kind):
    found = support.specimen(kind)
    filename = support.write_specimen(filestore, found)

    node = await _node_fit(filename, found)
    refit = await audit.refit(
        audit.read_stored_peaks(filename),
        support.specimen_params(found),
        support.specimen_isotopes(found),
        support.specimen_resolution(found),
        model=None,
    )

    assert refit.fit is not None, refit.warning
    assert refit.fit["mode"] == node.fit_result["mode"]
    assert (
        audit.axis_difference_ppm(refit.fit, node.fit_result, _tof_span(found))
        < SAME_AXIS_PPM
    )
    kept = refit.readings[refit.readings.ion_label.isin(refit.retained)]
    assert sorted(kept.mz.round(6)) == _kept(node)
    errors = audit.ppm_error(kept.sample_peak_tof, kept.mz, node.fit_result)
    np.testing.assert_allclose(
        errors,
        audit.ppm_error(kept.sample_peak_tof, kept.mz, refit.fit),
        atol=SAME_AXIS_PPM,
    )


# --- The node's rules, on a specimen with one thing changed ---
#
# Real calibrants all sit well inside every threshold, so on the specimens
# as they are, the residual tolerance, the refine window and the S/N floor
# could move a long way before a fit changed. Each test below changes one
# thing on the bromide specimen, so that the rule decides and a change to
# it shows.


def _line(found: dict, mz: float) -> dict:
    """The specimen's peak nearest an m/z."""
    return min(found["peaks"], key=lambda peak: abs(peak["mz"] - mz))


def _placed(found: dict, exact: float, ppm: float) -> dict:
    """Where the node's fit puts a line read ``ppm`` off its exact m/z: its
    TOF index, and its m/z on the specimen's axis."""
    p1, p2 = found["fit"]["par"]
    tof = p1 * np.sqrt(exact * (1 + ppm * 1e-6)) + p2
    axis = found["axis"]
    mz = tof_to_mass(np.array([tof]), axis["mode"], axis["par"])[0]
    return {"tof": float(tof), "mz": float(mz)}


def _misread(found: dict, exact: float, ppm: float) -> dict:
    """The specimen with the peak of one line read ``ppm`` off it."""
    found = copy.deepcopy(found)
    _line(found, exact).update(_placed(found, exact, ppm))
    found["peaks"].sort(key=lambda peak: peak["mz"])
    return found


def _neighbour(found: dict, exact: float, ppm: float, brighter: float) -> dict:
    """The specimen with a peak ``brighter`` times a line's beside it."""
    found = copy.deepcopy(found)
    peak = copy.deepcopy(_line(found, exact))
    for key in ("area", "sum_area", "sum_height"):
        peak[key] *= brighter
    peak.update(_placed(found, exact, ppm))
    found["peaks"] = sorted([*found["peaks"], peak], key=lambda peak: peak["mz"])
    return found


def _points(handler) -> dict[float, dict]:
    return {
        round(point["target_mz"], 2): point for point in handler.fitted_on["points"]
    }


@pytest.mark.asyncio
async def test_the_residual_tolerance_keeps_a_line_at_nine_ppm_and_drops_one_at_thirty(
    filestore,
):
    """Two lines misread, by 12 and by 30 ppm: the first ends 9 ppm off the
    fit and is kept, the second 30 ppm off it and is left out. A tolerance
    of 5 ppm would drop the first too, a much wider one keep the second."""
    found = support.specimen("bromide")
    found = _misread(found, 157.837, 12.0)
    found = _misread(found, 238.754, 30.0)
    filename = support.write_specimen(filestore, found)

    node = await _node_fit(filename, found)

    points = _points(node)
    assert not points[238.75]["retained"]
    assert points[157.84]["retained"]
    assert points[157.84]["residual_ppm"] == pytest.approx(8.9, abs=0.2)
    assert sum(point["retained"] for point in points.values()) == 6


@pytest.mark.asyncio
async def test_the_refine_window_reaches_a_hundred_ppm_and_no_further(filestore):
    """Two lines changed on the axis the node reads. One, Br3- at 240.8 Th,
    is read 95 ppm off: inside the 100 ppm window, it is matched, and the
    fit leaves it out. Beside the other, Br- at 80.9 Th, sits a peak ten
    times brighter 106 ppm off: outside the window, it is not matched, and
    the line keeps its own peak. A window under 95 ppm loses the first line;
    one that reaches the neighbour takes it in, and the overlap filter then
    drops both, closer than a peak's width as they are."""
    found = support.specimen("bromide")
    own = _line(found, 80.917)["mz"]
    # The fit's ppm, which the node reads 5 and 3.5 ppm lower before it
    found = _misread(found, 240.751, 100.0)
    found = _neighbour(found, 80.917, 110.0, brighter=10.0)
    filename = support.write_specimen(filestore, found)

    node = await _node_fit(filename, found)

    points = _points(node)
    assert points[240.75]["observed_mz"] == pytest.approx(
        240.751 * (1 + 95e-6), rel=2e-6
    )
    assert not points[240.75]["retained"]
    assert points[80.92]["observed_mz"] == pytest.approx(own, rel=1e-9)
    assert points[80.92]["retained"]
    assert len(points) == len(found["fit"]["points"])


@pytest.mark.asyncio
async def test_the_s_n_floor_drops_a_line_below_ten_and_keeps_one_above(filestore):
    """Two Br2- lines set just under and just over the floor of 10: the first
    is no candidate, the second is matched and kept. A floor that moves past
    either changes which."""
    found = support.specimen("bromide")
    _line(found, 157.837)["snr"] = 9.5
    _line(found, 161.833)["snr"] = 10.5
    filename = support.write_specimen(filestore, found)

    node = await _node_fit(filename, found)

    points = _points(node)
    assert 157.84 not in points
    assert points[161.83]["retained"]
    assert len(points) == len(found["fit"]["points"]) - 1
