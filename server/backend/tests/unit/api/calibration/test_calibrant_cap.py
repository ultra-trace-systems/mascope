"""The calibrant cap and the offset an Orbitrap fit reports.

The brightest lines are the worst calibrants: on the assignment gate's
Orbitrap sets a line carrying a third of the ion current or more reads
0.6 to 4 ppm above the weaker lines of its own file, and a fit that is the
median of its lines lands between them. The m/z calibration node leaves out
a line brighter than a cap, by its share of the ion current or its
intensity, and records the cap and what it left out in the fit's quality
block. It also fits the offset its calibrants carry beside the factor and
reports it there, without applying it.

The per-stream fit's cap and its pooled offset are pinned in
``tests/unit/libraries/signal/test_calibration_per_stream.py``.
"""

import math
from unittest.mock import AsyncMock, patch

import numpy as np
import pandas as pd
import pytest
import tof_audit_support as support

from mascope_backend.api.controllers.calibration.lib.calibration_mz_fit import (
    OrbiCalibrationHandler,
    TofCalibrationHandler,
    offset_report,
)
from mascope_backend.api.models.calibration.calibration_pydantic_model import (
    CalibrationFitParams,
    MzCalibrationParams,
    OrbiCalibrationParams,
    TofCalibrationParams,
)
from mascope_backend.api.models.calibration.config import (
    calibrants_over_cap,
    calibration_config,
)


NODE = "mascope_backend.api.controllers.calibration.lib.calibration_mz_fit"
FILENAME = "OrbiFile_2026.10.20_12h00m00s_cap"

#: The mean ion current per scan of the test file's polarity
TIC = 1000.0
#: The factor the file carries from an earlier fit, which its peaks are on
OLD_FACTOR = 1 + 5e-6


# -- the rule ------------------------------------------------------------------------


def test_a_line_over_either_cap_is_over():
    over = calibrants_over_cap(
        intensity=[10.0, 500.0, 50.0],
        tic_share=[0.4, 0.01, 0.01],
        tic_share_max=0.25,
        peak_intensity_max=100.0,
    )

    assert over.tolist() == [True, True, False]


def test_a_line_at_the_cap_is_not_over():
    over = calibrants_over_cap([100.0], [0.25], 0.25, 100.0)

    assert over.tolist() == [False]


def test_no_cap_leaves_out_nothing():
    assert calibrants_over_cap([1e12], [1.0], None, None).tolist() == [False]


def test_a_share_not_known_is_not_over():
    assert calibrants_over_cap([10.0], [math.nan], 0.25, None).tolist() == [False]


def test_the_orbitrap_default_caps_the_share_and_the_tof_default_does_not():
    orbitrap, tof = OrbiCalibrationParams(), TofCalibrationParams()

    assert orbitrap.calibrant_tic_share_max == 0.25
    assert tof.calibrant_tic_share_max is None
    assert orbitrap.peak_intensity_max is None
    assert tof.peak_intensity_max is None


def test_a_dialogs_params_take_the_instruments_cap_where_they_name_none():
    """The calibration dialog sends what it knows of: a cap it leaves out
    is the instrument's, so a manual fit is capped as the pipeline's is."""
    resolved = MzCalibrationParams(refine_window=50).with_defaults(
        OrbiCalibrationParams(peak_intensity_max=1e6)
    )

    assert resolved.calibrant_tic_share_max == 0.25
    assert resolved.peak_intensity_max == 1e6


def test_a_dialogs_own_cap_is_kept():
    resolved = MzCalibrationParams(
        refine_window=50, calibrant_tic_share_max=0.5, peak_intensity_max=10.0
    ).with_defaults(OrbiCalibrationParams())

    assert resolved.calibrant_tic_share_max == 0.5
    assert resolved.peak_intensity_max == 10.0


# -- an Orbitrap fit of a file -------------------------------------------------------


def _line(ion, mz, intensity, ppm):
    """A matched calibrant whose peak the instrument recorded `ppm` off,
    on the factor the file carries."""
    observed = mz * (1 + ppm * 1e-6) * OLD_FACTOR
    return {
        "target_ion_id": ion,
        "target_isotope_formula": ion,
        "mz": mz,
        "relative_abundance": 1.0,
        "sample_peak_mz": observed,
        "sample_peak_intensity": intensity,
        "match_mz_error": 1e6 * (observed - mz) / mz,
    }


#: The reagent's base peak at 40 % of the ion current, 4 ppm above two
#: weaker lines that agree with each other
BRIGHT = _line("bright", 61.04, 400.0, 4.0)
WEAK = [_line("weak-1", 158.15, 10.0, 0.2), _line("weak-2", 218.19, 5.0, -0.2)]


def _ratio(lines):
    """The scaling a fit on these lines asks for."""
    return float(np.median([line["mz"] / line["sample_peak_mz"] for line in lines]))


async def _orbi_fit(lines, tic=TIC, **params):
    handler = OrbiCalibrationHandler(
        FILENAME,
        CalibrationFitParams(
            calibration_collection_id="tc-1",
            ionization_mechanism_ids=["im-1"],
            polarity="-",
            **{**OrbiCalibrationParams().model_dump(), **params},
        ),
    )
    matches = pd.DataFrame(lines)
    tic_read = patch(
        f"{NODE}.m_compute.get_tic_per_scan",
        return_value=(np.arange(4), np.full(4, tic)),
    )
    with (
        patch.object(
            OrbiCalibrationHandler, "_has_peaks", new_callable=lambda: property(bool)
        ),
        patch.object(
            OrbiCalibrationHandler,
            "_resolve_calibration_isotopes",
            AsyncMock(return_value=matches),
        ),
        patch.object(OrbiCalibrationHandler, "_polarity_streams", lambda self: None),
        patch.object(
            OrbiCalibrationHandler,
            "_match_calibration_compounds",
            AsyncMock(return_value=(matches, matches)),
        ),
        patch.object(
            OrbiCalibrationHandler, "_get_old_factor", lambda self: OLD_FACTOR
        ),
        patch.object(OrbiCalibrationHandler, "_send_progress", AsyncMock()),
        tic_read as read,
    ):
        await handler.fit()
    handler.tic_read = read
    return handler


def _ions(handler):
    *points, _summary = handler.stats
    return [point["target_ion_id"] for point in points]


@pytest.mark.asyncio
async def test_a_line_brighter_than_the_cap_is_left_out_of_the_fit():
    handler = await _orbi_fit([BRIGHT, *WEAK])

    assert sorted(_ions(handler)) == ["weak-1", "weak-2"]
    assert handler.fit_result["par"]["old_factor_scaling"] == pytest.approx(
        _ratio(WEAK), rel=1e-15
    )
    assert handler.calibrant_cap == {
        "tic_share_max": 0.25,
        "peak_intensity_max": None,
        "n_excluded": 1,
        "fell_back": False,
    }


@pytest.mark.asyncio
async def test_with_no_cap_the_bright_line_pulls_the_fit():
    handler = await _orbi_fit([BRIGHT, *WEAK], calibrant_tic_share_max=None)

    assert sorted(_ions(handler)) == ["bright", "weak-1", "weak-2"]
    assert handler.fit_result["par"]["old_factor_scaling"] == pytest.approx(
        _ratio([BRIGHT, *WEAK]), rel=1e-15
    )
    assert _ratio([BRIGHT, *WEAK]) != pytest.approx(_ratio(WEAK), rel=1e-8)
    assert handler.calibrant_cap == {
        "tic_share_max": None,
        "peak_intensity_max": None,
        "n_excluded": 0,
        "fell_back": False,
    }


@pytest.mark.asyncio
async def test_an_absolute_cap_leaves_out_the_lines_brighter_than_it():
    handler = await _orbi_fit(
        [BRIGHT, *WEAK], calibrant_tic_share_max=None, peak_intensity_max=8.0
    )

    assert _ions(handler) == ["weak-2"]
    assert handler.calibrant_cap["peak_intensity_max"] == 8.0
    assert handler.calibrant_cap["n_excluded"] == 2


@pytest.mark.asyncio
async def test_a_cap_that_leaves_no_line_is_set_aside_and_said():
    """A fit on the bright lines is the fit the file had before the cap,
    and the quality bar still judges it; no fit at all would leave the file
    uncalibrated."""
    handler = await _orbi_fit([BRIGHT, *WEAK], calibrant_tic_share_max=0.001)

    assert sorted(_ions(handler)) == ["bright", "weak-1", "weak-2"]
    assert handler.calibrant_cap == {
        "tic_share_max": 0.001,
        "peak_intensity_max": None,
        "n_excluded": 0,
        "fell_back": True,
    }


@pytest.mark.asyncio
async def test_a_lines_share_is_of_its_polaritys_ion_current():
    """The line's height is averaged over the scans of the sample's
    polarity, so on a file of both its share is of their ion current."""
    handler = await _orbi_fit([BRIGHT, *WEAK])

    handler.tic_read.assert_called_once_with(FILENAME, polarity="-")
    *points, _ = handler.stats
    for point in points:
        assert point["calibrant_to_tic"] == pytest.approx(
            point["sample_peak_intensity"] / TIC
        )


@pytest.mark.asyncio
async def test_the_fit_reports_the_offset_its_calibrants_carry():
    """Lines on an axis 0.12 mDa low, beside a factor of +0.5 ppm, as the
    instrument recorded them: the report reads both back off the m/z the
    instrument recorded, under the factor the file carries."""
    lines = [
        _line(f"ion-{mz:g}", mz, 10.0, 0.5 - 0.12 * 1000 / mz)
        for mz in (61.04, 80.05, 121.07, 158.15, 279.16)
    ]

    handler = await _orbi_fit(lines)

    assert handler.offset["offset_mda"] == pytest.approx(-0.12, abs=1e-6)
    assert handler.offset["factor_ppm"] == pytest.approx(0.5, abs=1e-6)
    assert handler.offset["offset_se_mda"] == pytest.approx(0.0, abs=1e-6)
    assert handler.offset["n_lines"] == 5
    assert handler.offset["span"] == [61.04, 279.16]
    assert handler.offset["reason"] is None


@pytest.mark.asyncio
async def test_the_offset_is_fitted_over_the_lines_the_fit_kept():
    handler = await _orbi_fit([BRIGHT, *WEAK])

    assert handler.offset["n_lines"] == 2
    assert handler.offset["span"] == [158.15, 218.19]


@pytest.mark.asyncio
async def test_a_fit_on_no_calibrant_reports_no_offset():
    handler = await _orbi_fit([])

    assert handler.fit_result is None
    assert handler.offset is None
    assert handler.calibrant_cap is None


# -- the offset report ---------------------------------------------------------------


def _recorded(target, factor_ppm, offset_mda):
    target = np.asarray(target, dtype=float)
    return target * (1 + (factor_ppm + offset_mda * 1000 / target) * 1e-6)


def test_an_offset_and_a_factor_are_read_back_from_their_lines():
    target = [62.0, 100.0, 180.0, 330.0, 650.0]

    report = offset_report(target, _recorded(target, -0.4, 0.2))

    assert report["offset_mda"] == pytest.approx(0.2, abs=1e-9)
    assert report["factor_ppm"] == pytest.approx(-0.4, abs=1e-9)
    assert report["offset_se_mda"] == pytest.approx(0.0, abs=1e-9)
    assert report["n_lines"] == 5
    assert report["span"] == [62.0, 650.0]
    assert report["reason"] is None


def test_the_offsets_standard_error_is_its_scatters():
    target = np.array([60.0, 90.0, 140.0, 220.0, 400.0])
    noise = np.array([0.3, -0.2, -0.3, 0.1, 0.1])
    recorded = _recorded(target, 0.0, 0.0) * (1 + noise * 1e-6)

    report = offset_report(target, recorded)

    design = np.column_stack([np.ones(5), 1000 / target])
    coef, *_ = np.linalg.lstsq(design, noise, rcond=None)
    residual = noise - design @ coef
    covariance = residual @ residual / 3 * np.linalg.inv(design.T @ design)
    assert report["offset_mda"] == pytest.approx(coef[1], rel=1e-6)
    assert report["factor_ppm"] == pytest.approx(coef[0], rel=1e-6)
    assert report["offset_se_mda"] == pytest.approx(
        math.sqrt(covariance[1, 1]), rel=1e-6
    )


def test_two_lines_are_too_few_to_tell_an_offset_from_a_factor():
    target = [62.0, 650.0]

    report = offset_report(target, _recorded(target, 0.0, 0.2))

    assert report["offset_mda"] is None
    assert report["factor_ppm"] is None
    assert report["offset_se_mda"] is None
    assert report["n_lines"] == 2
    assert report["span"] == [62.0, 650.0]
    assert "2 calibrant lines" in report["reason"]


def test_three_lines_are_enough():
    target = [62.0, 180.0, 650.0]

    report = offset_report(target, _recorded(target, 0.0, 0.2))

    assert calibration_config.ORBI_OFFSET_MIN_LINES == 3
    assert report["offset_mda"] == pytest.approx(0.2, abs=1e-9)
    assert report["offset_se_mda"] is not None


def test_lines_close_in_m_z_cannot_tell_an_offset_from_a_factor():
    """A bromide set's two lines and their neighbour: 1000 / mz differs by
    0.04 across them, where an offset reads as a factor."""
    target = [238.75, 239.75, 240.75]

    report = offset_report(target, _recorded(target, 0.0, 0.2))

    assert report["offset_mda"] is None
    assert report["factor_ppm"] is None
    assert report["n_lines"] == 3
    assert "too narrow" in report["reason"]


def test_a_span_at_the_limit_is_fitted():
    """1000 / mz at 8, 6.25 and 5: exactly the limit of 3 across them."""
    target = [125.0, 160.0, 200.0]

    report = offset_report(target, _recorded(target, 0.0, 0.2))

    assert calibration_config.ORBI_OFFSET_MIN_INVERSE_SPAN == 3.0
    assert report["reason"] is None
    assert report["offset_mda"] == pytest.approx(0.2, abs=1e-9)


def test_a_span_short_of_the_limit_is_not():
    target = [125.0, 160.0, 199.0]

    report = offset_report(target, _recorded(target, 0.0, 0.2))

    assert report["offset_mda"] is None
    assert "too narrow" in report["reason"]


def test_a_line_not_known_is_left_out_and_none_is_no_span():
    report = offset_report([62.0, math.nan], [62.0, 70.0])

    assert report["n_lines"] == 1
    assert offset_report([], [])["span"] is None


# -- a TOF fit -----------------------------------------------------------------------


@pytest.fixture
def filestore(tmp_path, monkeypatch):
    import mascope_file.name as m_name

    monkeypatch.setattr(m_name.runtime.meta, "filestore", str(tmp_path))
    monkeypatch.setattr(m_name, "_INSTRUMENT_TYPE", {})
    return tmp_path


async def _tof_fit(filestore, areas, **params):
    """Fit the TOF test file, its calibrants' peaks of the given areas, the
    ion current at one ion per bin (a peak's share is its area)."""
    peaks = [
        {**peak, "area": area}
        for peak, area in zip(support.calibrant_peaks(), areas, strict=True)
    ]
    filename = support.write_file(filestore, peaks)
    handler = TofCalibrationHandler(filename, support.params(**params))
    with (
        patch.object(
            TofCalibrationHandler,
            "_resolve_calibration_isotopes",
            AsyncMock(return_value=support.isotopes().drop(columns="ion_label")),
        ),
        patch(
            f"{NODE}.read_instrument_functions",
            AsyncMock(return_value=({}, support.resolution)),
        ),
        patch.object(TofCalibrationHandler, "_total_ion_current", lambda self: 1.0),
    ):
        await handler.fit()
    return handler


@pytest.mark.asyncio
async def test_a_tof_fit_is_not_capped_by_default_and_says_so(filestore):
    handler = await _tof_fit(filestore, [0.9, 0.05, 0.03, 0.02, 0.02])

    assert handler.fit_result is not None
    assert handler.calibrant_cap == {
        "tic_share_max": None,
        "peak_intensity_max": None,
        "n_excluded": 0,
        "fell_back": False,
    }
    assert handler.offset is None


@pytest.mark.asyncio
async def test_a_tof_cap_leaves_out_a_bright_line(filestore):
    handler = await _tof_fit(
        filestore, [0.9, 0.05, 0.03, 0.02, 0.02], calibrant_tic_share_max=0.25
    )

    assert handler.fit_result is not None
    assert len(handler.stats) - 1 == 4
    assert handler.calibrant_cap["n_excluded"] == 1
    assert not handler.calibrant_cap["fell_back"]
    # A line the cap turned down never reached the fit, and is not listed
    assert len(handler.fitted_on["points"]) == 4


@pytest.mark.asyncio
async def test_a_tof_cap_that_leaves_too_few_for_the_model_is_set_aside(filestore):
    """Two lines left, where the TOF model needs three."""
    handler = await _tof_fit(
        filestore, [0.9, 0.5, 0.3, 0.02, 0.02], calibrant_tic_share_max=0.25
    )

    assert handler.fit_result is not None
    assert len(handler.stats) - 1 == 5
    assert handler.calibrant_cap["n_excluded"] == 0
    assert handler.calibrant_cap["fell_back"]
