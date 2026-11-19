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
``test_tof_calibration_audit.py``. A change to the search for the calibrants,
the S/N floor, the selection rule or the fit's loss moves the first;
re-baseline the specimens (the fixtures' README) only when such a change is
meant. The search that replaced the refine window (node version 3) fits both
as they were fitted at ingest.
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
# as they are, the residual tolerance, the search window and the S/N floor
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
async def test_the_residual_tolerance_takes_a_line_at_nine_ppm_and_not_one_at_twenty_six(
    filestore,
):
    """Two lines misread, by 12 and by 24 ppm. The first sits 9 ppm off the
    axis the other lines agree on: the search takes it, and the fit keeps
    it. The second sits 26 ppm off it, outside the tolerance of 15 ppm: the
    search takes no peak for it, and the record does not list it. A
    tolerance of 9 ppm would leave out the first line too; one of 18 ppm
    lets an axis through the second win."""
    found = support.specimen("bromide")
    found = _misread(found, 157.837, 12.0)
    found = _misread(found, 238.754, 24.0)
    filename = support.write_specimen(filestore, found)

    node = await _node_fit(filename, found)

    points = _points(node)
    assert 238.75 not in points
    assert points[157.84]["retained"]
    assert points[157.84]["residual_ppm"] == pytest.approx(8.9, abs=0.2)
    assert len(points) == 6
    assert all(point["retained"] for point in points.values())


@pytest.mark.asyncio
async def test_the_search_window_reaches_five_hundred_ppm(filestore):
    """The specimen read on an axis 450 ppm off the one it carried, as an
    acquisition axis that far off would read it: the search still finds
    every line, and the fit is the one made at ingest. Matched within the
    100 ppm refine window, as up to node version 2, no line had a peak."""
    found = copy.deepcopy(support.specimen("bromide"))
    p1, p2 = found["axis"]["par"]
    found["axis"]["par"] = [p1 / np.sqrt(1 + 450e-6), p2]
    for peak in found["peaks"]:
        peak["mz"] = float(
            tof_to_mass(np.array([peak["tof"]]), 0, found["axis"]["par"])[0]
        )
    filename = support.write_specimen(filestore, found)

    node = await _node_fit(filename, found)
    windowed = await audit.refit(
        audit.read_stored_peaks(filename),
        support.specimen_params(found),
        support.specimen_isotopes(found),
        support.specimen_resolution(found),
        windowed=True,
    )

    assert _kept(node) == sorted(
        round(p["target_mz"], 6) for p in found["fit"]["points"] if p["retained"]
    )
    assert (
        audit.axis_difference_ppm(node.fit_result, found["fit"], _tof_span(found))
        < SAME_AXIS_PPM
    )
    assert windowed.fit is None
    assert windowed.readings.empty


@pytest.mark.parametrize("off_ppm, kept", [(110.0, True), (60.0, False)])
@pytest.mark.asyncio
async def test_a_neighbour_within_a_hundred_ppm_drops_the_line(
    filestore, off_ppm, kept
):
    """Beside Br- at 80.9 Th sits a peak ten times brighter, closer than a
    bromide peak is wide (about 115 ppm). At 110 ppm, 106 on the axis the
    node reads, the search window takes it in, but the overlap filter looks
    no further than 100 ppm, and Br- 80.9 keeps its own peak, as when the 100
    ppm refine window of node version 2 stopped short of the neighbour. At
    60 ppm the filter drops both, and Br- is read on its line at 78.9 Th
    alone; so did version 2, which had both in its window."""
    found = _neighbour(support.specimen("bromide"), 80.917, off_ppm, brighter=10.0)
    filename = support.write_specimen(filestore, found)

    node = await _node_fit(filename, found)
    windowed = await audit.refit(
        audit.read_stored_peaks(filename),
        support.specimen_params(found),
        support.specimen_isotopes(found),
        support.specimen_resolution(found),
        windowed=True,
    )

    points = _points(node)
    assert (80.92 in points) is kept
    assert len(points) == len(found["fit"]["points"]) - (0 if kept else 1)
    assert all(point["retained"] for point in points.values())
    assert any(label.startswith("Br- 80.9") for label in windowed.retained) is kept
    if kept:
        # Its own peak, not the brighter neighbour
        assert points[80.92]["observed_mz"] == pytest.approx(
            _line(found, 80.917)["mz"], rel=1e-9
        )


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


def _read_ppm(point: dict) -> float:
    """How far off its exact m/z the node read a line, in ppm."""
    return (point["observed_mz"] / point["target_mz"] - 1) * 1e6


@pytest.mark.asyncio
async def test_a_line_off_the_axis_takes_no_peak_however_bright(filestore):
    """Br- 80.9 Th's own peak read 80 ppm low, with one ten times brighter
    60 ppm high: 140 ppm apart, further than a bromide peak is wide (about
    115 ppm), so the overlap filter keeps both. Neither is within 15 ppm of
    the axis the other lines agree on, and the line takes no peak. Matched
    within the 100 ppm refine window, as up to node version 2, it took the
    brighter, and the fit then left it out."""
    found = _misread(support.specimen("bromide"), 80.917, -80.0)
    found = _neighbour(found, 80.917, 60.0, brighter=10.0)
    filename = support.write_specimen(filestore, found)

    node = await _node_fit(filename, found)
    windowed = await audit.refit(
        audit.read_stored_peaks(filename),
        support.specimen_params(found),
        support.specimen_isotopes(found),
        support.specimen_resolution(found),
        windowed=True,
    )

    points = _points(node)
    assert 80.92 not in points
    assert len(points) == 6
    assert all(point["retained"] for point in points.values())
    reading = windowed.readings.set_index(windowed.readings.mz.round(2))
    read = (reading.loc[80.92, "sample_peak_mz"] / reading.loc[80.92, "mz"] - 1) * 1e6
    assert read == pytest.approx(58.5, abs=0.5)
    assert not any(label.startswith("Br- 80.9") for label in windowed.retained)


@pytest.mark.asyncio
async def test_seven_matches_take_the_one_pass_rule_not_the_subset_search(
    filestore,
):
    """Br- 78.9 Th read 60 ppm low, matched within the refine window as the
    audit reproduces a version-2 fit. Seven matches are more than five, so
    the fit keeps the lines within the tolerance of one fit of all seven:
    the misread line pulls that fit far enough that Br- 80.9 Th falls
    outside it too, and five lines are kept. The subset search, which takes
    five matches or fewer, would keep six, all but the misread line
    (#3281). The node's own search does not match the misread line at all,
    and keeps the other six."""
    found = _misread(support.specimen("bromide"), 78.919, -60.0)
    filename = support.write_specimen(filestore, found)

    windowed = await audit.refit(
        audit.read_stored_peaks(filename),
        support.specimen_params(found),
        support.specimen_isotopes(found),
        support.specimen_resolution(found),
        windowed=True,
    )
    node = await _node_fit(filename, found)

    assert len(windowed.readings) == 7
    assert sorted(round(label_mz, 2) for label_mz in windowed.readings.mz) == sorted(
        _points_of(found)
    )
    kept = {round(float(label.split()[1]), 2) for label in windowed.retained}
    assert 78.92 not in kept
    assert 80.92 not in kept
    assert len(kept) == 5
    points = _points(node)
    assert 78.92 not in points
    assert len(points) == 6
    assert all(point["retained"] for point in points.values())


def _points_of(found: dict) -> list[float]:
    """The specimen's lines at the abundance floor or above, rounded."""
    return [
        round(line["mz"], 2)
        for line in found["calibrants"]
        if line["relative_abundance"] >= 0.15
    ]


@pytest.mark.asyncio
async def test_a_positive_fit_takes_no_negative_peak(filestore):
    """The propylamine specimen, a positive-mode file, with a negative peak
    beside each of its peaks, ten times brighter and 10 ppm off, and one on
    the line of the propylamine trimer at 178.2 Th, which has no positive
    peak within the tolerance. The node reads only peaks of the fit's
    polarity: it fits the specimen as it did at ingest, and the trimer takes
    no peak. Read without the polarity, each negative peak would drop the
    line beside it as an overlapping neighbour, and the trimer would take
    the one on its line."""
    found = copy.deepcopy(support.specimen("propylamine"))
    trimer = 178.227774
    negatives = []
    for peak in found["peaks"]:
        twin = copy.deepcopy(peak)
        twin["polarity"] = "-"
        for key in ("area", "sum_area", "sum_height"):
            twin[key] *= 10.0
        p1, p2 = found["axis"]["par"]
        twin["tof"] = p1 * np.sqrt(peak["mz"] * (1 + 10e-6)) + p2
        twin["mz"] = float(tof_to_mass(np.array([twin["tof"]]), 0, [p1, p2])[0])
        negatives.append(twin)
    on_trimer = copy.deepcopy(max(found["peaks"], key=lambda peak: peak["snr"]))
    on_trimer.update(_placed(found, trimer, 0.0), polarity="-")
    negatives.append(on_trimer)
    found["peaks"] = sorted([*found["peaks"], *negatives], key=lambda p: p["mz"])
    filename = support.write_specimen(filestore, found)

    node = await _node_fit(filename, found)
    refit = await audit.refit(
        audit.read_stored_peaks(filename),
        support.specimen_params(found),
        support.specimen_isotopes(found),
        support.specimen_resolution(found),
        model=None,
    )

    points = _points(node)
    assert 178.23 not in points
    assert [round(p["target_mz"], 6) for p in node.fitted_on["points"]] == [
        round(p["target_mz"], 6) for p in found["fit"]["points"]
    ]
    assert _kept(node) == sorted(
        round(p["target_mz"], 6) for p in found["fit"]["points"] if p["retained"]
    )
    assert (
        audit.axis_difference_ppm(node.fit_result, found["fit"], _tof_span(found))
        < SAME_AXIS_PPM
    )
    assert refit.fit is not None, refit.warning
    assert (
        audit.axis_difference_ppm(refit.fit, node.fit_result, _tof_span(found))
        < SAME_AXIS_PPM
    )
    assert not any(label.startswith("C9H28N3+") for label in refit.retained)
