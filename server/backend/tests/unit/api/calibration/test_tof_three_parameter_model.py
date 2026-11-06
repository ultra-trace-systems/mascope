"""
When the TOF node fits Tofwerk's three-parameter mode 2 instead of mode 0.

The node selects its calibrants under mode 0 as it always did - the 15 ppm
residual rule, the exact subset search for five matches or fewer - and then
fits mode 2, ``i = p1*m^p3 + p2``, to the calibrants it kept as well. It
keeps mode 2 only where mode 2 predicts each calibrant ion, fitted on the
others, better than mode 0 does by more than 0.5 ppm on average, and only
on four ions or more: the two lines of a bromide ion read the axis at one
place, and one left in would hold it where the other is read. The record
says which model it kept and what each one's held-out error was.

A fit in one model alone, as the calibration audit asks for, selects its
calibrants under that model too, and a model never takes as few calibrants
as it has parameters: it would fit them exactly, and the subset search,
which keeps the set that agrees with itself best, would keep any such set.

The files are the audit's synthetic TOF file (``tof_audit_support``), with
the peaks on a mode-2 axis bent away from a square root by as much as a
TOF's axis bends across its calibrants: a few ppm.
"""

import os
from unittest.mock import AsyncMock, patch

import numpy as np
import pandas as pd
import pytest
import tof_audit_support as support
import xarray as xr

import mascope_file.io as m_io
import mascope_file.name as m_name
from mascope_backend.api.controllers.calibration.lib import calibration_mz_fit
from mascope_backend.api.controllers.calibration.lib.calibration_mz_fit import (
    TOF_MODE2_MIN_GAIN_PPM,
    TofCalibrationHandler,
)
from mascope_tofwerk.calibration import mz_calibrate, tof_to_mass


NODE = "mascope_backend.api.controllers.calibration.lib.calibration_mz_fit"

#: Seven lines of six ions: the five below, HSO4- at 97 Th and HSO4-.H2SO4
#: at 195.
SEVEN = support.CALIBRANTS + support.MORE_CALIBRANTS
#: Five lines of four ions: NO3- at 62 Th, Br- at 79 and 81, NO3-.HNO3 at
#: 125 and NO3-.(HNO3)2 at 188.
FIVE = support.CALIBRANTS
#: Four lines of three ions: the five less the trimer at 188 Th.
FOUR = ["NO3", "HN2O6", "Br"]
#: Three lines of three ions: the nitrate series alone.
THREE = ["NO3", "HN2O6", "H2N3O9"]

#: A bend that mode 0 leaves as a bow of about 3 ppm across the lines, and
#: misses each of the seven by 2.3 ppm on average when fitted on the other
#: ions.
BENT = 0.49997
#: A bend too slight for mode 2 to gain 0.5 ppm on the seven: 0.4.
SLIGHT = 0.499995


@pytest.fixture
def filestore(tmp_path, monkeypatch):
    """A filestore of its own, so no test reads or writes a real one."""
    monkeypatch.setattr(m_name.runtime.meta, "filestore", str(tmp_path))
    monkeypatch.setattr(m_name, "_INSTRUMENT_TYPE", {})
    return tmp_path


def lines(formulas) -> np.ndarray:
    """The exact m/z of the formulas' lines above the abundance floor."""
    isotopes = support.isotopes(formulas)
    return np.sort(isotopes[isotopes.relative_abundance >= 0.15].mz.to_numpy())


def _write(filestore, formulas, axis=None, off=None) -> str:
    """A file with one peak per line, each at the TOF index ``axis`` gives it.

    :param axis: Mode-2 parameters; the support's square-root axis if None
    :param off: How far some peaks sit off that axis, ppm, by whole Th
    """
    return support.write_file(filestore, support.bent_peaks(formulas, axis, off))


async def _fit(filename, formulas, model=None) -> TofCalibrationHandler:
    """The node's handler, fitted on the file under the formulas' lines."""
    handler = TofCalibrationHandler(filename, support.params(), model=model)
    isotopes = support.isotopes(formulas)
    with (
        patch.object(
            TofCalibrationHandler,
            "_resolve_calibration_isotopes",
            AsyncMock(return_value=isotopes.drop(columns="ion_label")),
        ),
        patch(
            f"{NODE}.read_instrument_functions",
            AsyncMock(return_value=({}, support.resolution)),
        ),
    ):
        await handler.fit()
    return handler


def _held_out(handler) -> dict[int, float | None]:
    return {
        entry["mode"]: entry["held_out_ppm"]
        for entry in handler.fitted_on["model"]["compared"]
    }


def _rows(handler) -> list[dict]:
    """The handler's per-calibrant stats, less the summary row."""
    *rows, summary = handler.stats
    assert "mz" not in summary
    return rows


# --- The node's choice ---


@pytest.mark.asyncio
async def test_a_bent_axis_is_fitted_in_mode_2(filestore):
    axis = support.bent_axis(BENT)
    filename = _write(filestore, SEVEN, axis)

    handler = await _fit(filename, SEVEN)

    assert handler.warning is None
    assert handler.fit_result["mode"] == 2
    np.testing.assert_allclose(handler.fit_result["par"], axis, rtol=1e-6)
    # Its own residuals are the stats, not mode 0's
    rows = _rows(handler)
    assert len(rows) == 7
    assert max(abs(row["calibration_mz_error"]) for row in rows) < 1e-3
    assert abs(handler.stats[-1]["calibration_mz_error"]) < 1e-3
    assert max(abs(p["residual_ppm"]) for p in handler.fitted_on["points"]) < 1e-3
    # The record says which won and by how much
    model = handler.fitted_on["model"]
    assert model["mode"] == 2
    assert model["equation"] == "i = p1*m^p3 + p2"
    assert model["min_gain_ppm"] == TOF_MODE2_MIN_GAIN_PPM == 0.5
    held_out = _held_out(handler)
    assert list(held_out) == [0, 2]
    assert held_out[0] == pytest.approx(2.29, abs=0.01)
    assert held_out[2] < 1e-3


@pytest.mark.asyncio
async def test_a_square_root_axis_keeps_mode_0(filestore):
    """Mode 2 predicts the lines of a square-root axis no better, and the
    node keeps mode 0 as it was."""
    filename = _write(filestore, SEVEN)

    handler = await _fit(filename, SEVEN)
    alone = await _fit(filename, SEVEN, model=0)

    assert handler.fit_result == alone.fit_result
    assert handler.fit_result["mode"] == 0
    # Every line's reading and residual alike (the isotopes' ids are new
    # with each fit)
    read = ["mz", "sample_peak_tof", "calibration_mz", "calibration_mz_error"]
    pd.testing.assert_frame_equal(
        pd.DataFrame(handler.stats)[read], pd.DataFrame(alone.stats)[read]
    )
    held_out = _held_out(handler)
    assert held_out[0] < 1e-3
    assert held_out[2] < 1e-3
    assert handler.fitted_on["model"]["mode"] == 0


@pytest.mark.asyncio
async def test_mode_2_is_kept_only_for_more_than_the_gain(filestore):
    filename = _write(filestore, SEVEN, support.bent_axis(BENT))
    handler = await _fit(filename, SEVEN)
    held_out = _held_out(handler)
    gain = held_out[0] - held_out[2]

    with patch.object(calibration_mz_fit, "TOF_MODE2_MIN_GAIN_PPM", gain - 0.01):
        assert (await _fit(filename, SEVEN)).fit_result["mode"] == 2
    with patch.object(calibration_mz_fit, "TOF_MODE2_MIN_GAIN_PPM", gain):
        assert (await _fit(filename, SEVEN)).fit_result["mode"] == 0
    with patch.object(calibration_mz_fit, "TOF_MODE2_MIN_GAIN_PPM", gain + 0.01):
        kept = await _fit(filename, SEVEN)
    assert kept.fit_result["mode"] == 0
    # Compared all the same
    assert _held_out(kept) == held_out


@pytest.mark.asyncio
async def test_a_slight_bend_keeps_mode_0(filestore):
    """Mode 2 would gain 0.4 ppm on these seven lines: less than 0.5."""
    filename = _write(filestore, SEVEN, support.bent_axis(SLIGHT))

    handler = await _fit(filename, SEVEN)

    held_out = _held_out(handler)
    assert 0.3 < held_out[0] - held_out[2] < TOF_MODE2_MIN_GAIN_PPM
    assert handler.fit_result["mode"] == 0


@pytest.mark.asyncio
async def test_four_ions_are_enough_to_compare(filestore):
    filename = _write(filestore, FIVE, support.bent_axis(BENT))

    handler = await _fit(filename, FIVE)

    assert len(_rows(handler)) == 5
    assert handler.fit_result["mode"] == 2
    assert _held_out(handler)[0] > 2.0


@pytest.mark.parametrize(
    "formulas", [FOUR, THREE], ids=["four lines of three ions", "three lines"]
)
@pytest.mark.asyncio
async def test_three_ions_are_not_compared(filestore, formulas):
    """Left one ion out of three, mode 2 would have its three parameters to
    fit at two places on the axis: the node keeps mode 0 and the record
    compares nothing, however bent the axis."""
    filename = _write(filestore, formulas, support.bent_axis(BENT))

    handler = await _fit(filename, formulas)

    assert len(_rows(handler)) == len(lines(formulas))
    assert handler.fit_result["mode"] == 0
    assert handler.fitted_on["model"]["compared"] is None
    assert handler.fitted_on["model"]["min_gain_ppm"] == 0.5


#: Five bromide ions, Br-.(H2O)n for n = 0 to 4: ten lines in pairs 2 Th
#: apart.
BROMIDE = ["Br", "H2OBr", "H4O2Br", "H6O3Br", "H8O4Br"]


@pytest.mark.asyncio
async def test_an_ion_is_held_out_with_both_its_lines(filestore):
    """Bromide ions on a square-root axis, each read a few ppm off as a
    scatter of peaks reads. Leaving out one line of a pair would read the
    axis where its partner, still fitted, holds it, and mode 2 would seem to
    predict the pair better than it does: held out by the line, it gains
    0.5 ppm here. Held out by the ion, it predicts them worse than mode 0."""
    off = {}
    for formula, ppm in zip(BROMIDE, [-2.7, -0.9, 2.6, 1.7, 0.3]):
        for mz in lines([formula]):
            off[round(mz)] = ppm
    filename = _write(filestore, BROMIDE, off=off)

    handler = await _fit(filename, BROMIDE)

    assert len(_rows(handler)) == 10
    held_out = _held_out(handler)
    assert held_out[2] > held_out[0]
    assert handler.fit_result["mode"] == 0

    retained = pd.DataFrame(_rows(handler))
    by_line = retained.assign(target_ion_id=[f"line-{i}" for i in range(10)])
    line_held_out = {mode: handler._held_out_error(by_line, mode) for mode in (0, 2)}
    assert line_held_out[0] - line_held_out[2] > TOF_MODE2_MIN_GAIN_PPM


@pytest.mark.asyncio
async def test_the_calibrants_are_those_mode_0_selects(filestore):
    """HSO4-.H2SO4 at 195 Th 40 ppm off: mode 0's residual rule drops it, and
    mode 2 is fitted and compared on the six lines it kept."""
    filename = _write(filestore, SEVEN, support.bent_axis(BENT), off={195: 40.0})

    handler = await _fit(filename, SEVEN)

    assert handler.fit_result["mode"] == 2
    assert [round(row["mz"]) for row in _rows(handler)] == [62, 79, 81, 97, 125, 188]
    points = handler.fitted_on["points"]
    assert [point["retained"] for point in points] == [True] * 6 + [False]
    # The line left out, read under the mode-2 fit the record carries
    assert points[-1]["residual_ppm"] > 30.0
    np.testing.assert_allclose(
        handler.fit_result["par"],
        mz_calibrate(
            [row["sample_peak_tof"] for row in _rows(handler)],
            [row["sample_peak_mz"] for row in _rows(handler)],
            [row["mz"] for row in _rows(handler)],
            mode=2,
        )[0]["par"],
        rtol=1e-12,
    )


@pytest.mark.parametrize(
    "failing_mode, held_out_fit, every",
    [(2, False, True), (2, True, True), (2, True, False), (0, True, True)],
    ids=[
        "mode 2 on them all",
        "mode 2 held out",
        "one mode 2 held out",
        "mode 0 held out",
    ],
)
@pytest.mark.asyncio
async def test_a_fit_that_fails_keeps_mode_0(
    filestore, failing_mode, held_out_fit, every
):
    """A least-squares failure in the comparison costs mode 2 its chance,
    not the file its fit: mode 0's, made before it, stays. One held-out fit
    that fails is enough: the model is not judged on the ions whose fits
    did not fail."""
    filename = _write(filestore, FIVE, support.bent_axis(BENT))
    alone = await _fit(filename, FIVE, model=0)
    calls = []
    failed = []

    def fit(tof, mz, exact, mode=0):
        calls.append(mode)
        # Every fit of all five lines comes before the held-out ones
        failing = mode == failing_mode and (len(tof) < 5) == held_out_fit
        if failing and (every or not failed):
            failed.append(len(tof))
            raise ValueError("Calibration failed: no convergence")
        return mz_calibrate(tof, mz, exact, mode=mode)

    with patch(f"{NODE}.mz_calibrate", fit):
        handler = await _fit(filename, FIVE)

    assert 2 in calls
    assert handler.error is None
    assert handler.fit_result == alone.fit_result
    held_out = _held_out(handler)
    assert held_out[failing_mode] is None
    other = 0 if failing_mode == 2 else 2
    assert held_out[other] == (
        pytest.approx(3.64, abs=0.01) if other == 0 else pytest.approx(0, abs=1e-3)
    )


def test_a_held_out_line_read_at_no_mass_fails_the_model():
    """A fit whose offset lies past a held-out line's index reads it at no
    mass: the model has no held-out error, rather than a NaN one."""
    mz = np.array([41.039, 59.073, 86.096, 148.144, 228.196])
    retained = _calibrants([2832.0, -150.0, 0.4995], mz)
    handler = TofCalibrationHandler("file", support.params())
    beyond = {"mode": 2, "par": [2832.0, 1e9, 0.4995]}

    with patch.object(handler, "_fit_matches", return_value=(beyond, {})):
        assert handler._held_out_error(retained, 2) is None
    assert handler._held_out_error(retained, 2) < 1e-3


def _calibrants(axis: list[float], mz: np.ndarray) -> pd.DataFrame:
    """Matched calibrants on a mode-2 axis, as the selection hands them on."""
    p1, p2, p3 = axis
    return pd.DataFrame(
        {
            "mz": mz,
            "sample_peak_mz": mz,
            "sample_peak_tof": p1 * mz**p3 + p2,
            "target_ion_id": [f"ion-{i}" for i in range(mz.size)],
        }
    )


@pytest.mark.parametrize("offset, kept", [(-150.0, 2), (150.0, 0)])
def test_mode_2_is_kept_only_where_the_axis_starts_after_its_offset(offset, kept):
    """An offset p2 past index 0 leaves the first samples of the axis with
    no mass under mode 2: such a fit is not kept, however it compares."""
    mz = np.array([41.039, 59.073, 86.096, 148.144, 228.196])
    axis = [2832.0, offset, 0.4995]
    retained = _calibrants(axis, mz)
    handler = TofCalibrationHandler("file", support.params())
    mode0, _ = handler._fit_matches(retained)

    fit, chosen, compared = handler._choose_model(mode0, retained)

    assert fit["mode"] == kept
    held_out = {entry["mode"]: entry["held_out_ppm"] for entry in compared}
    assert held_out[0] > 1.0
    assert (held_out[2] is None) == (kept == 0)
    if kept == 2:
        assert chosen.calibration_mz_error.abs().max() < 1e-3
    else:
        assert chosen is retained


@pytest.mark.parametrize(
    "par, whole",
    [
        ([2832.0, -1.0, 0.5], True),
        ([2832.0, 0.0, 0.5], False),
        ([-2832.0, -1.0, 0.5], False),
        ([2832.0, -1.0, 0.0], False),
    ],
    ids=["offset before index 0", "offset at it", "p1 negative", "p3 zero"],
)
def test_a_mode_2_axis_starts_after_its_offset(par, whole):
    assert calibration_mz_fit._axis_starts_after_its_offset({"par": par}) is whole


# --- A fit in one model alone ---


@pytest.mark.asyncio
async def test_mode_2_alone_selects_under_mode_2(filestore):
    """Five lines on a bent axis, one 40 ppm off: mode 2 alone drops it by
    its own residuals and keeps the four, which it fits exactly."""
    axis = support.bent_axis(BENT)
    filename = _write(filestore, FIVE, axis, off={125: 40.0})

    handler = await _fit(filename, FIVE, model=2)

    assert handler.fit_result["mode"] == 2
    assert [round(row["mz"]) for row in _rows(handler)] == [62, 79, 81, 188]
    np.testing.assert_allclose(handler.fit_result["par"], axis, rtol=1e-6)
    # Nothing compared: the model was asked for
    assert handler.fitted_on["model"]["compared"] is None


@pytest.mark.asyncio
async def test_mode_2_alone_never_keeps_an_exact_three_point_fit(filestore):
    """Four lines, one 40 ppm off. Mode 2 fits any three exactly, so every
    three-line subset would agree with itself perfectly and leave out a
    line that disagrees: the search would keep one of them. A mode-2 fit
    takes four calibrants at least, so the search stops there and fits
    nothing, where mode 0 keeps the three that agree."""
    filename = _write(filestore, FOUR, support.bent_axis(BENT), off={81: 40.0})

    alone = await _fit(filename, FOUR, model=2)
    node = await _fit(filename, FOUR)
    with patch.dict(calibration_mz_fit.TOF_MINIMUM_CALIBRATION_POINTS, {2: 3}):
        exact = await _fit(filename, FOUR, model=2)

    assert alone.fit_result is None
    assert alone.warning == (
        "Not enough calibration peaks after residual filtering. At least 4 "
        "peaks are required for a reliable calibration fit."
    )
    assert node.fit_result["mode"] == 0
    assert [round(row["mz"]) for row in _rows(node)] == [62, 79, 125]
    assert node.fitted_on["model"]["compared"] is None
    # What the minimum keeps out: three lines fitted exactly
    assert exact.fit_result["mode"] == 2
    assert len(_rows(exact)) == 3
    assert max(abs(row["calibration_mz_error"]) for row in _rows(exact)) < 1e-6


@pytest.mark.asyncio
async def test_mode_2_alone_needs_four_calibrants(filestore):
    filename = _write(filestore, THREE, support.bent_axis(BENT))

    alone = await _fit(filename, THREE, model=2)

    assert alone.fit_result is None
    assert alone.warning == "Not enough calibration peaks"


def test_each_model_takes_one_calibrant_more_than_its_parameters():
    assert calibration_mz_fit.TOF_MINIMUM_CALIBRATION_POINTS == {0: 3, 2: 4}
    node = TofCalibrationHandler("file", support.params())
    alone = TofCalibrationHandler("file", support.params(), model=2)
    assert node._minimum_calibration_points == 3
    assert alone._minimum_calibration_points == 4
    assert list(node._candidate_subset_sizes(5)) == [4, 3]
    assert list(alone._candidate_subset_sizes(5)) == [4]
    assert list(alone._candidate_subset_sizes(4)) == []


def test_a_model_the_node_does_not_fit_is_refused():
    with pytest.raises(ValueError, match="Model 5 cannot be fitted; fittable: 0, 2"):
        TofCalibrationHandler("file", support.params(), model=5)


# --- A mode-2 record on the file ---


def _applicable(filestore, filename: str = support.FILENAME) -> str:
    """The bent file of :data:`SEVEN`, with the scan periods a sum signal is
    read with, as a converted TofDaq file has them."""
    support.write_file(
        filestore,
        support.bent_peaks(SEVEN, support.bent_axis(BENT)),
        filename=filename,
    )
    periods = xr.Dataset(
        {"signal_period": ("time", np.ones(support.N_SCANS))},
        coords={"time": np.arange(support.N_SCANS, dtype=np.float64)},
    )
    periods.to_zarr(
        os.path.join(
            m_name.parse_path_from_item_filename(filename), "signal_period.zarr"
        ),
        mode="w",
        consolidated=True,
    )
    return filename


def _on_disk(filename) -> dict:
    """What an apply writes: the record and range in ``.props``, and the m/z
    of the signal's axis and of the stored peaks."""
    props = m_io.read_props(filename)
    return {
        "record": props["mz_calibration"],
        "range": list(props["range"]),
        "axis": m_io.load_coord(filename, "signal", "mz"),
        "peaks": m_io.load_coord(filename, "peak_timeseries", "mz"),
    }


@pytest.mark.asyncio
async def test_a_mode_2_record_applies_and_a_mode_0_one_takes_it_back(filestore):
    """A TOF fit is absolute: the axis is recomputed from the TOF index on
    every apply, whatever the model. A mode-2 record sets the axis it says,
    once or twice alike, and a mode-0 record applied after it sets the axis
    it would have set had mode 2 never been applied."""
    filename = _applicable(filestore)
    fresh = _applicable(filestore, "AuditTof_2026.10.20_13h00m00s_fresh")
    mode2 = (await _fit(filename, SEVEN)).fit_result
    assert mode2["mode"] == 2
    mode0 = (await _fit(filename, SEVEN, model=0)).fit_result
    tofs = m_io.load_coord(filename, "peak_timeseries", "tof")
    n_samples = m_io.load_coord(filename, "signal", "mz").size

    await TofCalibrationHandler(filename).apply(mode2)
    once = _on_disk(filename)
    await TofCalibrationHandler(filename).apply(mode2)
    twice = _on_disk(filename)

    axis = tof_to_mass(np.arange(n_samples), 2, mode2["par"])
    assert np.all(np.isfinite(axis)) and np.all(np.diff(axis) > 0)
    np.testing.assert_array_equal(once["axis"], axis)
    np.testing.assert_array_equal(once["peaks"], tof_to_mass(tofs, 2, mode2["par"]))
    assert once["range"] == [axis[0], axis[-1]]
    assert once["record"] == mode2
    for key in ("axis", "peaks"):
        np.testing.assert_array_equal(twice[key], once[key])
    assert twice["range"] == once["range"]
    # The peaks sit where the bent axis put them
    np.testing.assert_allclose(sorted(once["peaks"]), lines(SEVEN), rtol=1e-9)

    await TofCalibrationHandler(filename).apply(mode0)
    await TofCalibrationHandler(fresh).apply(mode0)
    back, direct = _on_disk(filename), _on_disk(fresh)
    for key in ("axis", "peaks"):
        np.testing.assert_array_equal(back[key], direct[key])
    assert back["range"] == direct["range"]
    assert back["record"] == mode0
