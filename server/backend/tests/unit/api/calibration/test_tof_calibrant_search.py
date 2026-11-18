"""
The TOF node's search for its calibrants: the axis most calibrant ions agree on.

First the search alone, on candidates built by hand (``tof_calibrant_search``);
then the node on a sample file on disk (``tof_audit_support``), where each of
the search's numbers - the window, the candidates per line, the slope prior,
the tight tolerance - decides one case.
"""

from unittest.mock import AsyncMock, patch

import numpy as np
import pandas as pd
import pytest
import tof_audit_support as support

import mascope_file.name as m_name
from mascope_backend.api.controllers.calibration.lib import (
    tof_calibrant_search as search,
)
from mascope_backend.api.controllers.calibration.lib import (
    tof_calibration_audit as audit,
)
from mascope_backend.api.controllers.calibration.lib.calibration_mz_fit import (
    TofCalibrationHandler,
)
from mascope_backend.api.models.calibration.config import calibration_config


NODE = "mascope_backend.api.controllers.calibration.lib.calibration_mz_fit"

P1, P2 = support.TRUE_FIT["par"]


def _tof(mz, ppm=0.0):
    """The TOF index the true axis gives an m/z read ``ppm`` off."""
    return P1 * np.sqrt(np.asarray(mz, dtype=np.float64) * (1 + ppm * 1e-6)) + P2


def _candidates(lines, ions, offsets, intensities=None):
    """Candidates by hand: per line, one per offset (ppm off the line)."""
    width = max(len(row) for row in offsets)
    tof = np.full((len(lines), width), np.nan)
    intensity = np.full((len(lines), width), np.nan)
    peak = np.full((len(lines), width), -1)
    number = 0
    for row, (mz, line_offsets) in enumerate(zip(lines, offsets)):
        for column, ppm in enumerate(line_offsets):
            tof[row, column] = _tof(mz, ppm)
            intensity[row, column] = (
                100.0 if intensities is None else intensities[row][column]
            )
            peak[row, column] = number
            number += 1
    return search.Candidates(
        exact_mz=np.asarray(lines, dtype=np.float64),
        ion=np.asarray(ions),
        tof=tof,
        intensity=intensity,
        peak=peak,
    )


def _search(candidates, **update):
    kwargs = {
        "reference_p1": P1,
        "tolerance_ppm": 15.0,
        "tight_ppm": 5.0,
        "p1_prior": calibration_config.TOF_SEARCH_P1_PRIOR,
    }
    return search.search(candidates, **{**kwargs, **update})


LINES = [61.9884, 78.9189, 80.9168, 124.984, 187.9797]
IONS = [0, 1, 1, 2, 3]


# --- The search alone ---


def test_the_axis_is_the_one_every_ion_agrees_on():
    """Each line has its own peak and a neighbour on either side, 150 to 400
    ppm off and no two lines' alike: the axis through the lines' own peaks
    holds all four ions, and is the true one."""
    offsets = [
        [-300.0, 0.0, 250.0],
        [-200.0, 0.0, 350.0],
        [-350.0, 0.0, 150.0],
        [-250.0, 0.0, 300.0],
        [-150.0, 0.0, 400.0],
    ]
    candidates = _candidates(LINES, IONS, offsets)

    found = _search(candidates)

    assert (found.n_ions, found.n_lines) == (4, 5)
    assert found.p1 == pytest.approx(P1, rel=1e-12)
    assert found.p2 == pytest.approx(P2, rel=1e-12)
    np.testing.assert_array_equal(found.peak, candidates.peak[:, 1])


def test_one_ion_or_none_makes_no_axis():
    """Two lines of one ion: no pair of two ions, no hypothesis."""
    one_ion = _candidates(LINES[1:3], [1, 1], [[0.0], [0.0]])

    assert _search(one_ion) is None


def test_a_slope_past_the_prior_is_no_hypothesis():
    """The file's own slope 0.5 % off the true one is inside the prior of
    1 %, 2 % off is outside it."""
    candidates = _candidates(LINES, IONS, [[0.0]] * len(LINES))

    assert _search(candidates, reference_p1=P1 * 1.005) is not None
    assert _search(candidates, reference_p1=P1 * 1.02) is None


def test_ions_count_before_intensity():
    """Four ions on the true axis, faint; three on another 400 ppm off,
    each a hundred times brighter: the four win."""
    offsets = [[0.0, 400.0]] * 4 + [[0.0]]
    intensities = [[1.0, 100.0]] * 4 + [[1.0]]
    candidates = _candidates(LINES, IONS, offsets, intensities)

    found = _search(candidates)

    assert found.n_ions == 4
    np.testing.assert_array_equal(found.peak, candidates.peak[:, 0])


def test_lines_of_one_ion_count_once():
    """Two ions offer two lines each, as Br- does at 79 and 81 Th. The true
    axis, faint, holds three ions on one line each; another 400 ppm off, a
    hundred times brighter, holds both lines of the two ions: four lines but
    two ions. The three ions win; counted by lines, the four would."""
    ions = [0, 1, 1, 2, 2]
    offsets = [[0.0], [0.0, 400.0], [400.0], [0.0, 400.0], [400.0]]
    intensities = [[1.0], [1.0, 100.0], [100.0], [1.0, 100.0], [100.0]]
    candidates = _candidates(LINES, ions, offsets, intensities)

    found = _search(candidates)

    assert (found.n_ions, found.n_lines) == (3, 3)
    assert found.p1 == pytest.approx(P1, rel=1e-12)
    true_peaks = candidates.peak[[0, 1, 3], 0]
    np.testing.assert_array_equal(
        found.peak, [true_peaks[0], true_peaks[1], -1, true_peaks[2], -1]
    )


def test_ions_held_tight_count_before_intensity():
    """The true axis holds four ions, all on it; another 400 ppm off holds
    four too, but scattered by 10 ppm, and a hundred times brighter. At the
    residual tolerance the two tie; within 5 ppm only the true one holds
    them, and it wins. With the tight tolerance as wide as the other, the
    brighter one would."""
    scatter = [10.0, -10.0, -10.0, 10.0, -10.0]
    offsets = [[0.0, 400.0 + ppm] for ppm in scatter]
    intensities = [[1.0, 100.0]] * len(LINES)
    candidates = _candidates(LINES, IONS, offsets, intensities)

    tight = _search(candidates)
    loose = _search(candidates, tight_ppm=15.0)

    np.testing.assert_array_equal(tight.peak, candidates.peak[:, 0])
    np.testing.assert_array_equal(loose.peak, candidates.peak[:, 1])


def test_equal_axes_are_told_apart_by_intensity():
    """Two axes 400 ppm apart, each holding every ion within 1 ppm: the
    brighter wins, whichever it is."""
    offsets = [[0.0, 400.0]] * len(LINES)
    for bright, column in (([100.0, 1.0], 0), ([1.0, 100.0], 1)):
        candidates = _candidates(LINES, IONS, offsets, [bright] * len(LINES))
        found = _search(candidates)
        np.testing.assert_array_equal(found.peak, candidates.peak[:, column])


def test_a_line_takes_its_most_intense_candidate_within_the_tolerance():
    """Two peaks of the trimer within 15 ppm of the axis, the fainter on it:
    the trimer takes the brighter."""
    offsets = [[0.0]] * 4 + [[0.0, 8.0]]
    intensities = [[100.0]] * 4 + [[10.0, 50.0]]
    candidates = _candidates(LINES, IONS, offsets, intensities)

    found = _search(candidates)

    assert found.peak[-1] == candidates.peak[-1, 1]


def test_a_brighter_peak_past_the_tolerance_is_not_taken():
    """NO3-.HNO3, between the other lines, has its own peak on the axis and
    one a hundred times brighter 40 ppm off, which no axis holding the others
    reaches: it keeps its own."""
    offsets = [[0.0]] * 3 + [[0.0, 40.0]] + [[0.0]]
    intensities = [[100.0]] * 3 + [[10.0, 1000.0]] + [[100.0]]
    candidates = _candidates(LINES, IONS, offsets, intensities)

    found = _search(candidates)

    assert found.peak[3] == candidates.peak[3, 0]
    assert found.n_lines == 5


def test_the_axis_is_refitted_to_the_lines_it_found():
    """The lines scatter by up to 12 ppm about the true axis. No axis
    through two of them holds all five within 15 ppm, but one refitted by
    least squares to the four lines it holds does."""
    scatter = [-12.0, 8.0, 8.0, 0.0, -8.0]
    candidates = _candidates(LINES, IONS, [[ppm] for ppm in scatter])

    found = _search(candidates)

    assert (found.n_ions, found.n_lines) == (4, 5)


def test_a_peak_before_the_offset_is_no_reading():
    """Mode 0 squares the flight time, so an index below the offset p2 maps
    to a mass too, mirrored; it is not a reading of the line."""
    candidates = _candidates(LINES, IONS, [[0.0]] * len(LINES))
    mirrored = 2 * P2 - candidates.tof[0, 0]
    candidates.tof[0, 0] = mirrored

    found = _search(candidates)

    assert found.peak[0] == -1
    assert found.n_lines == 4


def test_axes_tied_on_ions_and_intensity_take_the_smaller_residual():
    """Two axes 400 ppm apart, each holding every ion within 5 ppm, the
    lines of equal intensity: the one its lines sit closer to wins, though
    the other is met first."""
    scatter = [3.0, -3.0, -3.0, 3.0, -3.0]
    offsets = [[400.0 + ppm, 0.0] for ppm in scatter]
    candidates = _candidates(LINES, IONS, offsets)

    found = _search(candidates)

    np.testing.assert_array_equal(found.peak, candidates.peak[:, 1])


def test_the_hypotheses_are_scored_in_chunks_alike():
    """Scored a few at a time or all at once, the winner is the same."""
    offsets = [
        [-300.0, 0.0, 250.0],
        [-200.0, 0.0, 350.0],
        [-350.0, 0.0, 150.0],
        [-250.0, 0.0, 300.0],
        [-150.0, 0.0, 400.0],
    ]
    candidates = _candidates(LINES, IONS, offsets)
    whole = _search(candidates)

    with patch.object(search, "_CHUNK", 7):
        chunked = _search(candidates)

    np.testing.assert_array_equal(chunked.peak, whole.peak)
    assert (chunked.p1, chunked.p2) == (whole.p1, whole.p2)


# --- The node on a file ---


@pytest.fixture
def filestore(tmp_path, monkeypatch):
    """A filestore of its own, so no test reads or writes a real one."""
    monkeypatch.setattr(m_name.runtime.meta, "filestore", str(tmp_path))
    monkeypatch.setattr(m_name, "_INSTRUMENT_TYPE", {})
    return tmp_path


async def _node_fit(
    filename, isotopes, resolution=support.resolution
) -> TofCalibrationHandler:
    handler = TofCalibrationHandler(filename, support.params())
    with (
        patch.object(
            TofCalibrationHandler,
            "_resolve_calibration_isotopes",
            AsyncMock(return_value=isotopes.drop(columns="ion_label")),
        ),
        patch(
            f"{NODE}.read_instrument_functions",
            AsyncMock(return_value=({}, resolution)),
        ),
    ):
        await handler.fit()
    return handler


def _kept(handler) -> list[int]:
    """The lines a fit kept, by their m/z rounded to whole Th."""
    return [round(row["mz"]) for row in handler.stats if "mz" in row]


@pytest.mark.parametrize(
    "off, found", [(450.0, True), (-450.0, True), (550.0, False), (-550.0, False)]
)
@pytest.mark.asyncio
async def test_the_search_window_reaches_five_hundred_ppm(filestore, off, found):
    """The file's axis read 450 ppm off: every line's own peak is inside
    the 500 ppm search window, and the fit lands on the true axis. At 550
    ppm none is."""
    filename = support.write_file(
        filestore, support.calibrant_peaks(), stored=support.axis_off(off)
    )

    handler = await _node_fit(filename, support.isotopes())

    if found:
        assert _kept(handler) == [62, 79, 81, 125, 188]
        assert handler.fitted_on["search"]["window_ppm"] == 500.0
    else:
        assert handler.fit_result is None
        assert handler.warning == "Not enough calibration peaks"


@pytest.mark.parametrize("off, window", [(240.0, 250.0), (260.0, 500.0)])
@pytest.mark.asyncio
async def test_the_search_looks_within_250_ppm_first(filestore, off, window):
    """The file's axis read 240 ppm off: the search finds the calibrants
    within 250 ppm and looks no further. At 260 ppm it finds none there and
    looks to 500 ppm; the fit is the same."""
    filename = support.write_file(
        filestore, support.calibrant_peaks(), stored=support.axis_off(off)
    )

    handler = await _node_fit(filename, support.isotopes())

    assert _kept(handler) == [62, 79, 81, 125, 188]
    assert handler.fitted_on["search"] == {
        "window_ppm": window,
        "n_lines": 5,
        "n_ions": 4,
    }


def _true_errors(handler) -> np.ndarray:
    """How far from the true axis each line the fit kept sits, in ppm."""
    kept = [row for row in handler.stats if "mz" in row]
    return audit.ppm_error(
        [row["sample_peak_tof"] for row in kept],
        [row["mz"] for row in kept],
        support.TRUE_FIT,
    )


@pytest.mark.asyncio
async def test_three_ions_agreeing_near_the_file_s_axis_keep_the_search_there(
    filestore,
):
    """Three ions agree within 250 ppm, faint, on the true axis; NO3-.(HNO3)2
    has no peak there. 400 ppm off, all four agree, a hundred times brighter:
    so many peaks sit that far off, the drifting TOF's files showed, that
    some agree by chance. The search stays with the three."""
    peaks = [
        peak
        for peak in _decoys([0.0, 0.0, 0.0, 0.0, 0.0], off=400.0)
        if not (round(peak["mz"]) == 188 and peak["area"] == 1.0)
    ]
    filename = support.write_file(filestore, peaks)

    handler = await _node_fit(filename, support.isotopes())

    assert _kept(handler) == [62, 79, 81, 125]
    assert np.max(np.abs(_true_errors(handler))) < 1e-6
    assert handler.fitted_on["search"] == {
        "window_ppm": 250.0,
        "n_lines": 4,
        "n_ions": 3,
    }


@pytest.mark.asyncio
async def test_two_ions_agreeing_near_the_file_s_axis_are_no_consensus(filestore):
    """The file's axis reads 400 ppm off the true one, and two peaks, of NO3-
    and NO3-.HNO3, sit where it puts their lines: two ions always make an
    axis. The search looks to 500 ppm, where all four ions agree on the true
    axis, and the fit is made there."""
    # Placed by their m/z, as the file is written in the order of the peaks'
    near = [
        {"mz": peak["mz"] * (1 - 400e-6), "area": 100.0}
        for peak in support.calibrant_peaks()
        if round(peak["mz"]) in (62, 125)
    ]
    filename = support.write_file(
        filestore,
        support.calibrant_peaks() + near,
        stored=support.axis_off(400.0),
    )

    handler = await _node_fit(filename, support.isotopes())

    assert _kept(handler) == [62, 79, 81, 125, 188]
    assert np.max(np.abs(_true_errors(handler))) < 1e-6
    assert handler.fitted_on["search"] == {
        "window_ppm": 500.0,
        "n_lines": 5,
        "n_ions": 4,
    }


@pytest.mark.asyncio
async def test_an_axis_further_off_holding_no_more_ions_is_not_taken(filestore):
    """NO3- and NO3-.HNO3 alone have peaks on the true axis, faint, and as
    many a hundred times brighter 400 ppm off. Two ions are no consensus, so
    the search looks to 500 ppm, but the brighter pair holds no more ions
    than the near one, and the search keeps the near pair, too few to fit."""
    lines = [
        peak["mz"]
        for peak in support.calibrant_peaks()
        if round(peak["mz"]) in (62, 125)
    ]
    peaks = [{"mz": mz, "area": 1.0} for mz in lines] + [
        {"mz": mz, "ppm": 400.0, "area": 100.0} for mz in lines
    ]
    filename = support.write_file(filestore, peaks)
    stored = audit.read_stored_peaks(filename)

    refit = await audit.refit(
        stored, support.params(), support.isotopes(), support.resolution
    )

    assert refit.fit is None
    readings = refit.readings
    assert sorted(round(mz) for mz in readings.mz) == [62, 125]
    errors = audit.ppm_error(readings.sample_peak_tof, readings.mz, support.TRUE_FIT)
    assert np.max(np.abs(errors)) < 1e-6


def _crowded(n_brighter: int) -> list[dict]:
    """The calibrant lines, NO3-.HNO3's with brighter peaks beside it, each
    60 ppm from the next: apart by more than a peak's width, inside its
    search window."""
    peaks = support.calibrant_peaks()
    steps = [60.0, -60.0, 120.0, -120.0, 180.0, -180.0, 240.0, -240.0, 300.0]
    peaks += [
        {"mz": 124.984 * (1 + step * 1e-6), "area": 1000.0}
        for step in steps[:n_brighter]
    ]
    return peaks


@pytest.mark.parametrize("n_brighter, kept", [(7, True), (8, False)])
@pytest.mark.asyncio
async def test_a_line_has_eight_candidates(filestore, n_brighter, kept):
    """Seven brighter peaks in NO3-.HNO3's window leave its own peak the
    eighth candidate, and it is matched; eight leave it out of the eight,
    and the line goes unmatched while the others fit."""
    filename = support.write_file(filestore, _crowded(n_brighter))

    handler = await _node_fit(filename, support.isotopes())

    assert (125 in _kept(handler)) is kept
    assert {62, 79, 81, 188} <= set(_kept(handler))


def _broad(mz):
    """A resolving power of 3,000, a TOF's: one FWHM is 333 ppm, three times
    the overlap filter's reach."""
    return np.full_like(np.asarray(mz, dtype=np.float64), 3000.0)


def _beside(apart_ppm: float) -> list[dict]:
    """The calibrant lines, NO3-.HNO3's with a peak as strong this far off."""
    return support.calibrant_peaks() + [{"mz": 124.984 * (1 + apart_ppm * 1e-6)}]


@pytest.mark.parametrize("apart_ppm, kept", [(200.0, True), (60.0, False)])
@pytest.mark.asyncio
async def test_a_neighbour_overlaps_a_calibrant_within_a_hundred_ppm(
    filestore, apart_ppm, kept
):
    """Peaks 333 ppm wide: a neighbour 200 ppm from NO3-.HNO3 is within one
    width of it and inside its search window, but further than the overlap
    filter looks, and the line is fitted, as when the refine window kept the
    neighbour out of sight. One 60 ppm off overlaps it, and both go."""
    filename = support.write_file(filestore, _beside(apart_ppm))

    handler = await _node_fit(filename, support.isotopes(), resolution=_broad)

    assert (125 in _kept(handler)) is kept
    assert {62, 79, 81, 188} <= set(_kept(handler))


@pytest.mark.asyncio
async def test_a_version_2_refit_looks_a_peak_width_for_a_neighbour(filestore):
    """The file's axis reads 75 ppm high and a neighbour sits 150 ppm below
    NO3-.HNO3: both inside its refine window, 333 ppm wide. The audit
    reproducing a version-2 fit drops both, as that node did; searching, it
    looks no further than 100 ppm and keeps the line."""
    filename = support.write_file(
        filestore, _beside(-150.0), stored=support.axis_off(75.0)
    )
    stored = audit.read_stored_peaks(filename)
    params = support.params()
    isotopes = support.isotopes()

    searched = await audit.refit(stored, params, isotopes, _broad)
    windowed = await audit.refit(stored, params, isotopes, _broad, windowed=True)

    def lines(refit):
        return {round(mz) for mz in refit.readings.mz}

    assert lines(searched) == {62, 79, 81, 125, 188}
    assert lines(windowed) == {62, 79, 81, 188}


@pytest.mark.asyncio
async def test_the_axis_need_not_hold_the_reagent_ion(filestore):
    """NO3-, the reagent and much the brightest line, sits 40 ppm off the
    axis its three other ions agree on. A rule that the axis must hold the
    reagent ion would take an axis through NO3- holding fewer ions; the
    search takes the three and leaves NO3- unmatched."""
    peaks = support.calibrant_peaks(off={62: 40.0})
    peaks[0]["area"] = 1e5
    filename = support.write_file(filestore, peaks)

    handler = await _node_fit(filename, support.isotopes())

    assert _kept(handler) == [79, 81, 125, 188]
    assert [round(p["target_mz"]) for p in handler.fitted_on["points"]] == [
        79,
        81,
        125,
        188,
    ]
    assert handler.fitted_on["search"] == {
        "window_ppm": 250.0,
        "n_lines": 4,
        "n_ions": 3,
    }


def _decoys(scatter: list[float], off: float = 150.0) -> list[dict]:
    """The calibrant lines, faint, and beside each a peak a hundred times
    brighter on an axis ``off`` ppm off, scattered about it by ``scatter``."""
    peaks = [{**peak, "area": 1.0} for peak in support.calibrant_peaks()]
    decoys = [
        {"mz": peak["mz"], "ppm": off + ppm, "area": 100.0}
        for peak, ppm in zip(peaks, scatter)
    ]
    return peaks + decoys


@pytest.mark.asyncio
async def test_the_node_tells_tied_axes_apart_within_a_third_of_the_tolerance(
    filestore,
):
    """Both axes hold all four ions within 15 ppm: the brighter one 150 ppm
    off, its lines scattered by 7 ppm, and the true one. Within 5 ppm, a
    third of the tolerance, only the true one holds them, and the fit is
    made on its lines. Within 10 ppm both would, and the brighter would win."""
    filename = support.write_file(filestore, _decoys([7.0, -7.0, -7.0, 7.0, -7.0]))

    handler = await _node_fit(filename, support.isotopes())

    errors = audit.ppm_error(
        [row["sample_peak_tof"] for row in handler.stats if "mz" in row],
        [row["mz"] for row in handler.stats if "mz" in row],
        support.TRUE_FIT,
    )
    assert np.max(np.abs(errors)) < 1e-6
    assert _kept(handler) == [62, 79, 81, 125, 188]


@pytest.mark.asyncio
async def test_the_search_reads_the_axis_the_file_carries(filestore):
    """The search is handed the slope of the file's own axis, the residual
    tolerance, a third of it for the tight pass, and the slope prior."""
    filename = support.write_file(
        filestore, support.calibrant_peaks(), stored=support.axis_off(300.0)
    )
    seen = {}
    real = search.search

    def spy(candidates, **kwargs):
        seen.update(kwargs)
        return real(candidates, **kwargs)

    with patch.object(search, "search", spy):
        await _node_fit(filename, support.isotopes())

    assert seen["reference_p1"] == pytest.approx(
        support.axis_off(300.0)["par"][0], rel=1e-9
    )
    assert seen["tolerance_ppm"] == 15.0
    assert seen["tight_ppm"] == pytest.approx(5.0)
    assert seen["p1_prior"] == 0.01


@pytest.mark.asyncio
async def test_an_ion_offered_twice_is_one_ion(filestore):
    """NO3- offered under a second ion id, as an ion reached through two
    mechanisms is: one line, of one ion, by its formula."""
    filename = support.write_file(filestore, support.calibrant_peaks())
    isotopes = support.isotopes()
    nitrate = isotopes[isotopes.target_ion_formula == "NO3-"]
    twice = pd.concat(
        [isotopes, nitrate.assign(target_ion_id="another-ion")], ignore_index=True
    )

    handler = await _node_fit(filename, twice)

    assert handler.fitted_on["search"] == {
        "window_ppm": 250.0,
        "n_lines": 5,
        "n_ions": 4,
    }


@pytest.mark.asyncio
async def test_a_line_below_the_abundance_floor_is_no_line_of_the_search(filestore):
    """An ion whose one line is below the abundance floor, 300 ppm from
    NO3-.HNO3, with a peak on it: the fit never takes such a line, so the
    search does not count it."""
    isotopes = support.isotopes()
    faint = isotopes[isotopes.target_ion_formula == "HN2O6-"].iloc[[0]]
    faint = faint.assign(
        target_ion_id="faint-ion",
        target_isotope_id="faint-isotope",
        target_ion_formula="Faint-",
        target_isotope_formula="Faint",
        mz=faint.mz * (1 + 300e-6),
        relative_abundance=0.1,
        ion_label="Faint- 125.0465",
    )
    peaks = support.calibrant_peaks()
    peaks.append({"mz": float(faint.mz.iloc[0]), "area": 100.0})
    filename = support.write_file(filestore, peaks)

    handler = await _node_fit(filename, pd.concat([isotopes, faint]))

    assert handler.fitted_on["search"] == {
        "window_ppm": 250.0,
        "n_lines": 5,
        "n_ions": 4,
    }


@pytest.mark.asyncio
async def test_a_minor_line_brings_no_peak_to_read(filestore):
    """A peak beside a minor line of NO3-.HNO3 alone, whose timeseries the
    store does not hold: the search never reads it, where the match within
    the refine window of node version 2 read every line's window, and the
    audit, reproducing it, ranks such a peak by its stored sum."""
    isotopes = support.isotopes()
    minor = isotopes[
        (isotopes.target_ion_formula == "HN2O6-")
        & (
            isotopes.relative_abundance
            < calibration_config.DEFAULT_ISOTOPE_ABUNDANCE_MIN
        )
    ].mz.iloc[0]
    peaks = support.calibrant_peaks()
    peaks.append({"mz": float(minor) * (1 + 20e-6), "area": 5.0, "computed": False})
    filename = support.write_file(filestore, peaks)
    counts = {}
    for windowed in (False, True):
        stored = audit.read_stored_peaks(filename)
        refit = await audit.refit(
            stored, support.params(), isotopes, support.resolution, windowed=windowed
        )
        assert refit.fit is not None
        counts[windowed] = len(stored.estimated)

    assert counts == {False: 0, True: 1}
