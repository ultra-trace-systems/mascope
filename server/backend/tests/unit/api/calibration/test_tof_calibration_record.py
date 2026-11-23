"""
What a TOF fit's record says it was fitted on.

The quality block of a TOF fit lists the points the fit chose from - every
line past the match filters, kept or left out, with its residual and its
peak's S/N - the m/z span of the ones it kept, the version of the calibration
collection, the model and the node's version. Re-calibration selects files by
them, and the calibration dialog shows the points.

The node's own handler fits a sample file on disk (``tof_audit_support``),
and the calibration audit - the plan's yardstick - refits the same file, so
the residuals the record lists are held against the audit's. A record written
before the keys existed is judged and applied as it was.
"""

import copy
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import h5py
import numpy as np
import pandas as pd
import pytest
import tof_audit_support as support
import xarray as xr

import mascope_file.name as m_name
from mascope_backend.api.controllers.calibration import calibration_controller
from mascope_backend.api.controllers.calibration.lib import acquisition_axis
from mascope_backend.api.controllers.calibration.lib import (
    tof_calibration_audit as audit,
)
from mascope_backend.api.controllers.calibration.lib.calibration_mz_fit import (
    TOF_CALIBRATION_NODE_VERSION,
    TofCalibrationHandler,
    calibration_isotopes_sha256,
    calibration_quality_issues,
)
from mascope_backend.api.controllers.sample.lib.fetch_affected_sample_data import (
    AffectedSampleData,
)
from mascope_backend.api.models.calibration.calibration_pydantic_model import (
    MzCalibrationParams,
)
from mascope_tofwerk.calibration import tof_to_mass


NODE = "mascope_backend.api.controllers.calibration.lib.calibration_mz_fit"

#: The keys a TOF fit adds to its quality block.
NEW_KEYS = {
    "points",
    "span",
    "n_lines",
    "collection",
    "model",
    "search",
    "node_version",
    "strong_peaks",
}

#: The lines scattered about the true axis so that the search's axis holds
#: all five within 15 ppm, and the fit to all five leaves NO3- outside it:
#: the subset search leaves NO3- out, 26 ppm off the fit to the other four.
SCATTERED = {62: -12.0, 79: 8.0, 81: 8.0, 188: -8.0}

#: Each line's S/N, by its exact m/z rounded to whole Th: every peak its own.
SNR = {62: 310.0, 79: 95.0, 81: 90.0, 97: 140.0, 125: 64.0, 188: 21.0, 195: 33.0}

#: Sample ions a collection could add to the reagent lines, each one line:
#: C3H3O4- at 103 Th, C5H7O4- at 131, C7H9O4- at 157 and C8H11O4- at 171.
SAMPLE_IONS = ["C3H3O4", "C5H7O4", "C7H9O4", "C8H11O4"]


@pytest.fixture
def filestore(tmp_path, monkeypatch):
    """A filestore of its own, so no test reads or writes a real one."""
    monkeypatch.setattr(m_name.runtime.meta, "filestore", str(tmp_path))
    # The instrument type is remembered by name, and every test's file has
    # the same name
    monkeypatch.setattr(m_name, "_INSTRUMENT_TYPE", {})
    return tmp_path


def _write(filestore, formulas=support.CALIBRANTS, off=None) -> str:
    """A file with one peak per line of ``formulas``, each with its own S/N."""
    peaks = support.calibrant_peaks(formulas, off=off)
    for peak in peaks:
        peak["snr"] = SNR[round(peak["mz"])]
    return support.write_file(filestore, peaks)


def _patched(isotopes):
    """The node's reads of the database and the instrument, answered."""
    return (
        patch.object(
            TofCalibrationHandler,
            "_resolve_calibration_isotopes",
            AsyncMock(return_value=isotopes.drop(columns="ion_label")),
        ),
        patch(
            f"{NODE}.read_instrument_functions",
            AsyncMock(return_value=({}, support.resolution)),
        ),
    )


async def _node_fit(filename, isotopes, params=None) -> TofCalibrationHandler:
    """The node's own handler, fitted on the file."""
    handler = TofCalibrationHandler(filename, params or support.params())
    resolved, functions = _patched(isotopes)
    with resolved, functions:
        await handler.fit()
    return handler


# --- The points ---


@pytest.mark.asyncio
async def test_every_line_the_fit_chose_from_is_a_point_kept_or_left_out(filestore):
    """Five lines the search's axis holds within 15 ppm: the subset search
    leaves NO3- out, and the record still lists it, with the residual that
    says why."""
    filename = _write(filestore, off=SCATTERED)
    isotopes = support.isotopes()

    handler = await _node_fit(filename, isotopes)
    points = handler.fitted_on["points"]

    assert [point["ion"] for point in points] == [
        "NO3-",
        "Br-",
        "Br-",
        "HN2O6-",
        "H2N3O9-",
    ]
    assert [point["retained"] for point in points] == [False, True, True, True, True]
    # By exact m/z, each at the peak it was matched to
    exact = sorted(isotopes[isotopes.relative_abundance >= 0.15].mz)
    assert [point["target_mz"] for point in points] == exact
    stored = audit.read_stored_peaks(filename).peaks
    assert [point["observed_mz"] for point in points] == sorted(stored.mz.values)
    assert [point["snr"] for point in points] == [SNR[round(mz)] for mz in exact]
    # The line left out sits outside the 15 ppm tolerance of the fit to the
    # others, every kept one well inside it
    residuals = [point["residual_ppm"] for point in points]
    assert residuals[0] < -20.0
    assert max(abs(residual) for residual in residuals[1:]) < 1.0
    # And the kept ones are the fit's own rows, residual for residual
    *rows, _summary = handler.stats
    assert sorted(row["calibration_mz_error"] for row in rows) == sorted(residuals[1:])


@pytest.mark.asyncio
async def test_a_line_off_the_axis_the_others_agree_on_is_no_point(filestore):
    """The trimer 40 ppm off the axis the other four lines agree on: the
    search finds no peak of it within 15 ppm of that axis, so it never
    reaches the fit, and the record does not list it."""
    filename = _write(filestore, off={188: 40.0})

    handler = await _node_fit(filename, support.isotopes())

    points = handler.fitted_on["points"]
    assert [round(point["target_mz"]) for point in points] == [62, 79, 81, 125]
    assert all(point["retained"] for point in points)
    assert handler.fitted_on["search"] == {
        "window_ppm": 250.0,
        "n_lines": 4,
        "n_ions": 3,
    }


@pytest.mark.asyncio
async def test_the_residuals_are_the_audits(filestore):
    """The audit reads a line's error under a fit as the fit's m/z at the
    peak's TOF index against the exact m/z; the record's residuals are those,
    for the lines the fit left out as well as the ones it kept."""
    filename = _write(filestore, off=SCATTERED)
    isotopes = support.isotopes()
    params = support.params()

    handler = await _node_fit(filename, isotopes, params)
    refit = await audit.refit(
        audit.read_stored_peaks(filename), params, isotopes, support.resolution
    )

    readings = refit.readings.sort_values("mz")
    audited = audit.ppm_error(
        readings.sample_peak_tof.to_numpy(), readings.mz.to_numpy(), refit.fit
    )
    points = handler.fitted_on["points"]
    assert [point["target_mz"] for point in points] == readings.mz.tolist()
    np.testing.assert_allclose(
        [point["residual_ppm"] for point in points], audited, rtol=0, atol=1e-9
    )


@pytest.mark.asyncio
async def test_a_large_pool_marks_the_lines_its_residual_rule_dropped(filestore):
    """Seven lines take the one-pass residual rule instead of the subset
    search: the line it drops is listed, left out."""
    filename = _write(
        filestore,
        support.CALIBRANTS + support.MORE_CALIBRANTS,
        off={**SCATTERED, 62: -14.0, 195: -8.0},
    )
    isotopes = support.isotopes(support.CALIBRANTS + support.MORE_CALIBRANTS)

    handler = await _node_fit(filename, isotopes)
    points = handler.fitted_on["points"]

    assert len(points) == 7
    assert [round(point["target_mz"]) for point in points if not point["retained"]] == [
        62
    ]


@pytest.mark.asyncio
async def test_the_span_is_that_of_the_lines_kept(filestore):
    """The trimer at 188 Th was not found on the axis the others agree on:
    the fit covers the lines from NO3- to NO3-.HNO3, and the span says so."""
    filename = _write(filestore, off={188: 40.0})
    isotopes = support.isotopes()

    handler = await _node_fit(filename, isotopes)

    kept = isotopes[isotopes.relative_abundance >= 0.15].mz.sort_values()
    assert handler.fitted_on["span"] == [kept.iloc[0], kept.iloc[-2]]


@pytest.mark.asyncio
async def test_the_span_starts_at_the_lowest_line_kept(filestore):
    """NO3- at 62 Th was matched and left out: the span starts at the Br-
    line above it, not at the lowest line the fit chose from."""
    filename = _write(filestore, off=SCATTERED)
    isotopes = support.isotopes()

    handler = await _node_fit(filename, isotopes)

    points = handler.fitted_on["points"]
    assert [point["retained"] for point in points] == [False, True, True, True, True]
    kept = isotopes[isotopes.relative_abundance >= 0.15].mz.sort_values()
    assert handler.fitted_on["span"] == [kept.iloc[1], kept.iloc[-1]]


@pytest.mark.asyncio
async def test_the_record_names_the_collection_the_model_and_the_node(filestore):
    filename = _write(filestore)
    isotopes = support.isotopes()

    handler = await _node_fit(filename, isotopes)

    assert handler.fitted_on["collection"] == {
        "id": "collection",
        "isotopes_sha256": calibration_isotopes_sha256(isotopes),
    }
    model = handler.fitted_on["model"]
    assert {key: model[key] for key in ("mode", "equation", "min_gain_ppm")} == {
        "mode": 0,
        "equation": "i = p1*sqrt(m) + p2",
        "min_gain_ppm": 0.5,
    }
    # Four ions: the two models were compared, and mode 2 gained nothing on
    # a square-root axis
    assert [entry["mode"] for entry in model["compared"]] == [0, 2]
    assert handler.fitted_on["search"] == {
        "window_ppm": 250.0,
        "n_lines": 5,
        "n_ions": 4,
    }
    assert handler.fitted_on["node_version"] == TOF_CALIBRATION_NODE_VERSION
    assert TOF_CALIBRATION_NODE_VERSION == 3


@pytest.mark.asyncio
async def test_a_fit_that_does_not_come_about_records_nothing(filestore):
    """Two lines are fewer than the model needs: no fit, nothing to record."""
    filename = _write(filestore, ["NO3", "HN2O6"])

    handler = await _node_fit(filename, support.isotopes(["NO3", "HN2O6"]))

    assert handler.fit_result is None
    assert handler.fitted_on is None


# --- How far the strong peaks reach ---

#: Sample peaks beside the calibrant lines, each far from every line: strong
#: ones of the fit's polarity below and above them, and peaks the reach
#: leaves out: weak ones, one of the other polarity, a satellite.
SAMPLE_PEAKS = [
    *({"mz": mz, "snr": 50.0} for mz in (20.0, 25.0, 30.0)),
    {"mz": 35.0, "snr": 20.0},
    *({"mz": mz, "snr": 50.0} for mz in (400.0, 500.0, 600.0, 700.0, 800.0, 900.0)),
    {"mz": 10.0, "snr": 19.0},
    {"mz": 1200.0, "snr": 19.0},
    {"mz": 1300.0, "snr": 400.0, "polarity": "+"},
    {"mz": 1400.0, "snr": 400.0, "satellite": True},
]


def _write_with_sample_peaks(filestore) -> str:
    peaks = support.calibrant_peaks()
    for peak in peaks:
        peak["snr"] = SNR[round(peak["mz"])]
    return support.write_file(filestore, [*peaks, *SAMPLE_PEAKS])


@pytest.mark.asyncio
async def test_the_record_says_how_far_the_strong_peaks_reach(filestore):
    """The strong peaks are those of the fit's polarity with S/N 20 or more,
    20 itself included: the five calibrant lines and ten sample peaks. Their reach is read at the
    fifth from either end, past 35 Th below and short of 600 Th above."""
    filename = _write_with_sample_peaks(filestore)

    handler = await _node_fit(filename, support.isotopes())
    strong = handler.fitted_on["strong_peaks"]

    assert strong["n_peaks"] == 15
    assert strong["span"] == [
        pytest.approx(61.988, abs=1e-2),
        pytest.approx(500.0, abs=1e-2),
    ]
    assert (strong["min_snr"], strong["tail"]) == (20.0, 5)
    # The fit itself is the one it would be without the sample peaks
    assert handler.fitted_on["span"] == [
        pytest.approx(61.988, abs=1e-3),
        pytest.approx(187.980, abs=1e-3),
    ]


@pytest.mark.asyncio
async def test_a_few_strong_peaks_reach_from_their_middle(filestore):
    """Five strong peaks are fewer than twice five: both ends are read at
    the third, so the lower never lies above the higher."""
    filename = _write(filestore)

    handler = await _node_fit(filename, support.isotopes())

    middle = sorted(support.isotopes().query("relative_abundance >= 0.15").mz)[2]
    assert handler.fitted_on["strong_peaks"]["span"] == [
        pytest.approx(middle, abs=1e-2),
        pytest.approx(middle, abs=1e-2),
    ]


@pytest.mark.asyncio
async def test_the_fit_flags_peaks_far_past_its_calibrants(filestore):
    """Calibrated on 62-188 Th, the strong peaks reach 500 Th: 1.5 widths of
    the span past its top in sqrt(m/z). The fit the dialog previews says so,
    under its seal."""
    filename = _write_with_sample_peaks(filestore)

    fit, _ = await _fit_answer(filename, support.isotopes())

    assert fit["quality_issues"] == [
        {
            "code": "extrapolated",
            "message": "Calibrated on 62\u2013188 Th; peaks to 500 Th are extrapolated.",
        }
    ]


# --- The collection's version ---


class TestIsotopesHash:
    """The hash tells one version of a collection from another, and nothing
    else: not the database it was read from, nor the order of its rows."""

    def test_ids_and_order_do_not_change_it(self):
        isotopes = support.isotopes()
        renumbered = isotopes.assign(
            target_isotope_id=[f"other-{i}" for i in range(len(isotopes))],
            target_ion_id="other-ion",
        ).iloc[::-1]

        assert calibration_isotopes_sha256(renumbered) == calibration_isotopes_sha256(
            isotopes
        )

    def test_an_isotope_offered_twice_counts_once(self):
        isotopes = support.isotopes()
        twice = isotopes.iloc[[0, *range(len(isotopes))]]

        assert calibration_isotopes_sha256(twice) == calibration_isotopes_sha256(
            isotopes
        )

    def test_a_difference_below_what_a_fit_reads_does_not_change_it(self):
        isotopes = support.isotopes()
        nudged = isotopes.assign(mz=isotopes.mz + 1e-9)

        assert calibration_isotopes_sha256(nudged) == calibration_isotopes_sha256(
            isotopes
        )

    @pytest.mark.parametrize(
        "edit",
        [
            pytest.param(lambda df: df.iloc[1:], id="an isotope removed"),
            pytest.param(
                lambda df: df.assign(mz=df.mz.where(df.index != 0, df.mz + 1e-4)),
                id="an exact m/z",
            ),
            pytest.param(
                lambda df: df.assign(
                    relative_abundance=df.relative_abundance.where(
                        df.index != 0, df.relative_abundance / 2
                    )
                ),
                id="an abundance",
            ),
            pytest.param(
                lambda df: df.assign(
                    target_isotope_formula=df.target_isotope_formula.where(
                        df.index != 0, "C9-"
                    )
                ),
                id="a formula",
            ),
        ],
    )
    def test_an_edit_changes_it(self, edit):
        isotopes = support.isotopes()

        assert calibration_isotopes_sha256(edit(isotopes)) != (
            calibration_isotopes_sha256(isotopes)
        )

    def test_another_collection_hashes_otherwise(self):
        assert calibration_isotopes_sha256(
            support.isotopes(["Br"])
        ) != calibration_isotopes_sha256(support.isotopes())


# --- The record ---


class _Session:
    """A database session that knows one ionization mode."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, _model, _key):
        return SimpleNamespace(
            ionization_mode_id="im-1",
            calibration_collection_id="collection",
            ionization_mechanism_ids=[support.RADICAL_ANION.ionization_mechanism_id],
            ionization_mode_polarity="-",
        )


async def _fit_answer(filename, isotopes) -> tuple[dict, TofCalibrationHandler]:
    """What ``calibration_mz_fit`` answers for the file, fitted by the node."""
    sample = SimpleNamespace(
        sample_item_id="si-1",
        sample_item_name="tof",
        sample_file_id="sf-1",
        filename=filename,
        ionization_mode_id="im-1",
    )
    handler = TofCalibrationHandler(filename, support.params())
    resolved, functions = _patched(isotopes)
    with (
        resolved,
        functions,
        patch.object(
            calibration_controller, "fetch_sample", AsyncMock(return_value=sample)
        ),
        patch.object(calibration_controller, "async_session", _Session),
        patch.object(
            calibration_controller,
            "fetch_affected_sample_data",
            AsyncMock(return_value=([], [], [], [])),
        ),
        patch.object(
            calibration_controller, "get_calibration_handler", lambda *a: handler
        ),
        patch.object(calibration_controller, "derive_token_secret", lambda _: "key"),
    ):
        answer = await calibration_controller.calibration_mz_fit(
            sample_item_id="si-1",
            mz_calibration_params=MzCalibrationParams(refine_window=100),
        )
    return answer["data"]["fit"], handler


@pytest.mark.asyncio
async def test_the_fits_record_carries_them_under_its_seal(filestore):
    """The keys go into the quality block the fit is sealed over, so an
    apply can trust them: one edited point, and the seal no longer holds."""
    filename = _write(filestore, off=SCATTERED)

    fit, handler = await _fit_answer(filename, support.isotopes())

    quality = fit["quality"]
    assert {key: quality[key] for key in NEW_KEYS} == handler.fitted_on
    # Beside what the block said before, which still counts the kept lines
    assert quality["n_points"] == 4
    assert quality["n_ions"] == 3
    # Through the dialog and back, as JSON
    sent_back = json.loads(json.dumps(fit))
    with patch.object(calibration_controller, "derive_token_secret", lambda _: "key"):
        assert calibration_controller.fit_seal_valid(sent_back, filename)
        sent_back["quality"]["points"][0]["retained"] = True
        assert not calibration_controller.fit_seal_valid(sent_back, filename)


#: A TOF quality block as the node wrote it before it named its points.
BEFORE = {
    "n_points": 5,
    "n_ions": 3,
    "pre_fit_mz_error_ppm": 82.0,
    "post_fit_mz_error_ppm": 2.1,
    "calibrant_to_tic": 0.04,
    "mz_error_tolerance": 15.0,
    "refine_window": 100.0,
    "axis_correction_ppm": None,
}


@pytest.mark.asyncio
async def test_the_keys_do_not_change_how_a_fit_is_judged(filestore):
    filename = _write(filestore)
    _, handler = await _fit_answer(filename, support.isotopes())
    after = {**BEFORE, **handler.fitted_on}

    assert calibration_quality_issues(BEFORE, support.FILENAME) == []
    assert calibration_quality_issues(after, support.FILENAME) == []
    # Nor what it is judged on: a block below the bar is below it either way
    poor = {"post_fit_mz_error_ppm": 12.0}
    assert calibration_quality_issues(
        {**BEFORE, **poor}, support.FILENAME
    ) == calibration_quality_issues({**after, **poor}, support.FILENAME)


async def _apply(filename: str, fit: dict) -> dict:
    """Apply a sealed ``fit`` to a TOF file with no previous record.

    :return: The record persisted.
    """
    sample_file = SimpleNamespace(
        sample_file_id="sf-1",
        filename=filename,
        instrument="TOF-1",
        mz_calibration=None,
        range=None,
        to_dict=dict,
    )
    handler = SimpleNamespace(apply=AsyncMock())
    sum_signal = SimpleNamespace(mz=SimpleNamespace(values=[10.0, 300.0]))
    ctrl = calibration_controller
    with (
        patch.object(ctrl, "derive_token_secret", lambda _: "key"),
        patch.object(ctrl, "calibration_quality_gate", lambda: "enforce"),
        patch.object(ctrl, "fetch_sample_file", AsyncMock(return_value=sample_file)),
        patch.object(
            ctrl,
            "fetch_affected_sample_data",
            AsyncMock(return_value=AffectedSampleData([], [], [], [])),
        ),
        patch.object(ctrl, "update_sample_batch_status", AsyncMock()),
        patch.object(ctrl, "send_progress_user_notification", AsyncMock()),
        patch.object(ctrl, "get_calibration_handler", MagicMock(return_value=handler)),
        patch.object(ctrl, "get_sum_signal", MagicMock(return_value=sum_signal)),
        patch.object(ctrl, "update_sample_file", AsyncMock()),
        patch.object(ctrl, "SampleFileUpdate", MagicMock()),
    ):
        ctrl.seal_fit(fit, filename)
        await ctrl.calibration_mz_apply.__wrapped__(
            fit=fit, filename=filename, manual=True, user_id=7
        )
    handler.apply.assert_awaited_once()
    return sample_file.mz_calibration


@pytest.mark.parametrize("named", [False, True], ids=["before", "with the new keys"])
@pytest.mark.asyncio
async def test_a_record_with_or_without_the_keys_applies(filestore, named):
    """A fit from before the keys and one with them apply alike, verified,
    and the record keeps what the block carried."""
    filename = _write(filestore)
    _, handler = await _fit_answer(filename, support.isotopes())
    quality = {**BEFORE, **handler.fitted_on} if named else dict(BEFORE)
    fit = {"mode": 0, "par": support.TRUE_FIT["par"], "quality": quality}

    stored = await _apply(filename, json.loads(json.dumps(fit)))

    assert stored["status"] == "ok"
    assert stored["verified"] is True
    assert stored["quality"] == json.loads(json.dumps(quality))
    assert (NEW_KEYS <= stored["quality"].keys()) is named


@pytest.mark.asyncio
async def test_a_mode_2_fit_is_sealed_judged_and_applied(filestore):
    """A file on a bent axis is fitted in mode 2: the record carries its
    three parameters and the comparison under the seal, clears the bar as a
    mode-0 fit would, and is stored verified."""
    formulas = support.CALIBRANTS + support.MORE_CALIBRANTS
    filename = support.write_file(
        filestore, support.bent_peaks(formulas, support.bent_axis(0.49997))
    )

    fit, _ = await _fit_answer(filename, support.isotopes(formulas))

    assert fit["mode"] == 2
    assert len(fit["par"]) == 3
    assert fit["quality"]["model"]["mode"] == 2
    compared = {
        e["mode"]: e["held_out_ppm"] for e in fit["quality"]["model"]["compared"]
    }
    assert compared[0] - compared[2] > 0.5
    assert fit["quality_issues"] == []
    sent_back = json.loads(json.dumps(fit))
    with patch.object(calibration_controller, "derive_token_secret", lambda _: "key"):
        assert calibration_controller.fit_seal_valid(sent_back, filename)
        sent_back["quality"]["model"]["mode"] = 0
        assert not calibration_controller.fit_seal_valid(sent_back, filename)

    stored = await _apply(filename, json.loads(json.dumps(fit)))

    assert stored["status"] == "ok"
    assert stored["verified"] is True
    assert (stored["mode"], stored["par"]) == (2, fit["par"])
    assert stored["quality"]["model"] == fit["quality"]["model"]


# --- The share of the collection's lines kept ---


@pytest.mark.asyncio
async def test_the_record_counts_each_line_the_collection_offers_once(filestore):
    """Five lines above the abundance floor: NO3-, NO3-.HNO3, NO3-.(HNO3)2
    and both bromide lines. NO3- offered a second time, as an ion through
    another mechanism would, at an m/z computed a float step apart, is still
    one line; one below the floor is none."""
    filename = _write(filestore)
    isotopes = support.isotopes()
    first = isotopes.iloc[[0]]
    twice = pd.concat(
        [isotopes, first.assign(target_ion_id="another-ion", mz=first.mz + 1e-9)],
        ignore_index=True,
    )
    assert (isotopes.relative_abundance < 0.15).any()

    handler = await _node_fit(filename, twice)

    assert handler.fitted_on["n_lines"] == 5


def _crowded_file(filestore, axis_off_ppm: float) -> str:
    """Reagent lines and sample ions, three of the sample ions with a weaker
    peak 50 ppm above their line, as a sample ion among dense peaks has.

    :param axis_off_ppm: How far off the file's acquisition axis reads every
        line's own peak, ppm; the neighbours stay where they are.
    """
    peaks = support.calibrant_peaks(support.CALIBRANTS + SAMPLE_IONS)
    for peak in peaks:
        peak["ppm"] = axis_off_ppm
    neighbours = [
        {"mz": peak["mz"], "ppm": 50.0, "area": 30.0}
        for peak in peaks
        if round(peak["mz"]) in (131, 157, 171)
    ]
    return support.write_file(filestore, peaks + neighbours)


@pytest.mark.asyncio
async def test_an_axis_off_past_the_refine_window_fits_the_lines_not_their_neighbours(
    filestore,
):
    """GitHub #2776's case. The acquisition axis reads 170 ppm off, past the
    100 ppm refine window: no line's own peak is near where the axis puts
    it, while three sample ions each have a neighbour there, and the three
    neighbours agree with each other. Matched within the window, the node
    fitted the three neighbours. The search finds the axis all nine lines
    agree on instead: the fit keeps every line and verifies, and the record
    still says how far the acquisition axis read off."""
    filename = _crowded_file(filestore, axis_off_ppm=-170.0)
    isotopes = support.isotopes(support.CALIBRANTS + SAMPLE_IONS)

    fit, _ = await _fit_answer(filename, isotopes)

    quality = fit["quality"]
    lines = sorted(isotopes[isotopes.relative_abundance >= 0.15].mz)
    assert [point["target_mz"] for point in quality["points"]] == lines
    assert all(point["retained"] for point in quality["points"])
    assert (quality["n_points"], quality["n_lines"]) == (9, 9)
    assert quality["search"] == {
        "window_ppm": 250.0,
        "n_lines": 9,
        "n_ions": 8,
    }
    # On each line's own peak, every one of them on the fitted axis
    assert max(abs(point["residual_ppm"]) for point in quality["points"]) < 1e-6
    # Measured on the axis the file carried: the peaks sit 170 ppm low on
    # the true axis, which the file reads 6 ppm low again
    assert quality["pre_fit_mz_error_ppm"] == pytest.approx(176.0, abs=0.5)
    assert fit["quality_issues"] == []
    stored = await _apply(filename, json.loads(json.dumps(fit)))
    assert stored["verified"] is True
    # And it is reported as drift of the acquisition axis, signed: the
    # record's offset on that axis (MAS-937), where the pre-fit error above
    # is a mean size
    assert stored["acquisition_drift"] is True
    assert stored["acquisition_drift_ppm"] == pytest.approx(-176.0, abs=0.5)


@pytest.mark.parametrize("off", [300.0, -300.0])
@pytest.mark.asyncio
async def test_an_axis_300_ppm_off_fits_the_true_lines(filestore, off):
    """The file's axis reads every line 300 ppm off, three times the refine
    window: matched within it, no line had a peak, and the file was left
    with "Not enough calibration peaks". The search finds the lines, the fit
    lands on the axis the peaks were placed by, and the record measures the
    pre-fit error and the drift against the axis the file carried."""
    filename = support.write_file(
        filestore,
        [{**peak, "snr": SNR[round(peak["mz"])]} for peak in support.calibrant_peaks()],
        stored=support.axis_off(off),
    )
    isotopes = support.isotopes()

    fit, handler = await _fit_answer(filename, isotopes)

    quality = fit["quality"]
    points = quality["points"]
    assert [round(point["target_mz"]) for point in points] == [62, 79, 81, 125, 188]
    assert max(abs(point["residual_ppm"]) for point in points) < 1e-6
    np.testing.assert_allclose(fit["par"], support.TRUE_FIT["par"], rtol=1e-9)
    assert quality["pre_fit_mz_error_ppm"] == pytest.approx(abs(off), rel=1e-3)
    assert quality["search"] == {"window_ppm": 500.0, "n_lines": 5, "n_ions": 4}
    assert fit["quality_issues"] == []
    stored = await _apply(filename, json.loads(json.dumps(fit)))
    assert stored["acquisition_drift"] is True
    assert abs(stored["acquisition_drift_ppm"]) == pytest.approx(abs(off), rel=1e-3)


@pytest.mark.asyncio
async def test_the_same_collection_on_a_right_axis_verifies_on_every_line(filestore):
    """The same file with its axis right: each line's own peak outweighs
    its neighbour, the fit keeps all nine lines and verifies."""
    filename = _crowded_file(filestore, axis_off_ppm=0.0)
    isotopes = support.isotopes(support.CALIBRANTS + SAMPLE_IONS)

    fit, _ = await _fit_answer(filename, isotopes)

    quality = fit["quality"]
    lines = sorted(isotopes[isotopes.relative_abundance >= 0.15].mz)
    assert [point["target_mz"] for point in quality["points"]] == lines
    assert all(point["retained"] for point in quality["points"])
    assert (quality["n_points"], quality["n_lines"]) == (9, 9)
    assert fit["quality_issues"] == []
    stored = await _apply(filename, json.loads(json.dumps(fit)))
    assert stored["verified"] is True


# --- The acquisition axis through a first fit and its refits (MAS-937) ---

#: Samples of the stored axis the files below carry: few, so the apply that
#: rewrites them is quick. The peaks are placed by index, not on it.
N_STORED = 64


def _acquired_file(filestore, ppm: float) -> str:
    """A file the node has not fitted: its acquisition axis reads every line
    ``ppm`` high, its record is the converter's, and its stored axis is that
    axis from the raw axis's first sample."""
    acquisition = support.axis_off(ppm)
    filename = support.write_file(
        filestore,
        support.calibrant_peaks(),
        record={**acquisition, "status": "unfitted"},
        stored=acquisition,
    )
    path = os.path.join(filestore, *support._relative_path(filename))
    mz = np.asarray(tof_to_mass(np.arange(float(N_STORED)), 0, acquisition["par"]))
    times = np.arange(support.N_SCANS, dtype=np.float64)
    xr.Dataset(
        {"signal": (("mz", "time"), np.ones((N_STORED, support.N_SCANS)))},
        coords={"mz": mz, "time": times},
    ).to_zarr(os.path.join(path, "signal.zarr"), mode="w", consolidated=True)
    xr.Dataset({"sum_signal": ("mz", np.ones(N_STORED))}, coords={"mz": mz}).to_zarr(
        os.path.join(path, "sum_signal.zarr"), mode="w", consolidated=True
    )
    return filename


def _write_tofdaq(filename: str, ppm: float) -> str:
    """The file's TofDaq file, as far as the acquisition axis goes: the raw
    axis, as long as the stored one, and the converter's coefficients."""
    acquisition = support.axis_off(ppm)
    path = m_name.parse_path_from_item_filename(filename)
    raw = np.asarray(tof_to_mass(np.arange(float(N_STORED)), 0, acquisition["par"]))
    with h5py.File(os.path.join(path, "data.h5"), "w") as h5_file:
        group = h5_file.create_group("FullSpectra")
        group.create_dataset("MassAxis", data=raw.astype(np.float32))
        group.attrs["MassCalibMode"] = np.array([0])
        group.attrs["MassCalibration nbrParameters"] = np.array([2])
        for i, value in enumerate(acquisition["par"]):
            group.attrs[f"MassCalibration p{i + 1}"] = np.array([value])
    return os.path.join(path, "data.h5")


async def _apply_on_disk(sample_file, fit: dict, manual: bool = False) -> dict:
    """Apply a sealed ``fit`` to the file on disk, with the node's own
    handler, over the record ``sample_file`` holds.

    :return: The record written, a copy.
    """
    ctrl = calibration_controller
    with (
        patch.object(ctrl, "derive_token_secret", lambda _: "key"),
        patch.object(ctrl, "calibration_quality_gate", lambda: "enforce"),
        patch.object(ctrl, "fetch_sample_file", AsyncMock(return_value=sample_file)),
        patch.object(
            ctrl,
            "fetch_affected_sample_data",
            AsyncMock(return_value=AffectedSampleData([], [], [], [])),
        ),
        patch.object(ctrl, "update_sample_batch_status", AsyncMock()),
        patch.object(ctrl, "send_progress_user_notification", AsyncMock()),
        patch.object(ctrl, "update_sample_file", AsyncMock()),
        patch.object(ctrl, "SampleFileUpdate", MagicMock()),
    ):
        ctrl.seal_fit(fit, sample_file.filename)
        await ctrl.calibration_mz_apply.__wrapped__(
            fit=fit, filename=sample_file.filename, manual=manual, user_id=7
        )
    return copy.deepcopy(sample_file.mz_calibration)


def _sample_file(filename: str, record: dict) -> SimpleNamespace:
    return SimpleNamespace(
        sample_file_id="sf-1",
        filename=filename,
        instrument="TOF-1",
        mz_calibration=copy.deepcopy(record),
        range=None,
        to_dict=dict,
    )


async def _fit_and_apply(sample_file, before_apply=None) -> dict:
    """Fit the file with the node and apply the fit. ``before_apply`` writes
    a file the apply alone reads, and gives its path to remove after."""
    fit, _ = await _fit_answer(sample_file.filename, support.isotopes())
    written = before_apply() if before_apply else None
    try:
        return await _apply_on_disk(sample_file, json.loads(json.dumps(fit)))
    finally:
        if written:
            # The node's fit reads a TofDaq file's spectra, which this one
            # does not hold.
            os.remove(written)


def _kept_offsets(record: dict) -> list[float]:
    """The kept lines' offsets on the acquisition axis, by the record."""
    points = record["quality"]["points"]
    offsets = acquisition_axis.line_offsets(record)
    return [o for p, o in zip(points, offsets) if p["retained"]]


@pytest.mark.parametrize("ppm", [80.0, 30.0], ids=["past the drift limit", "within"])
@pytest.mark.asyncio
async def test_a_first_fit_and_two_refits_read_the_same_raw_axis_offsets(
    filestore, ppm
):
    """The first fit reads its lines ``ppm`` high on the acquisition axis;
    each refit observes them on the axis the fit before set, near zero, and
    reads the same offsets on the acquisition axis it kept. Below the drift
    limit too: the record keeps the axis, not a flag."""
    filename = _acquired_file(filestore, ppm)
    sample_file = _sample_file(
        filename, {**support.axis_off(ppm), "status": "unfitted"}
    )

    records = [await _fit_and_apply(sample_file) for _ in range(3)]

    observed = [acquisition_axis.observed_offsets(r) for r in records]
    assert [e["offset_ppm"] for e in observed[0]] == pytest.approx([ppm] * 5, abs=0.1)
    for refit in observed[1:]:
        assert [e["offset_ppm"] for e in refit] == pytest.approx([0.0] * 5, abs=0.1)
    first = _kept_offsets(records[0])
    assert first == pytest.approx([ppm] * len(first), abs=0.1)
    for refit in records[1:]:
        assert _kept_offsets(refit) == pytest.approx(first, abs=1e-6)
    for record in records:
        assert record["acquisition_axis"] == {
            "mode": 0,
            "par": support.axis_off(ppm)["par"],
            "raw_index_offset": 0,
        }
        assert acquisition_axis.fitted_at(record) is not None
        assert "acquisition_offsets" not in record
    # The drift marker reads the same value on every record
    flagged = ppm > 50.0
    assert [r.get("acquisition_drift", False) for r in records] == [flagged] * 3
    if flagged:
        assert [r["acquisition_drift_ppm"] for r in records] == pytest.approx(
            [acquisition_axis.axis_offset_ppm(r) for r in records]
        )
    # Nor did a refit move the axis the fits put the peaks on
    assert records[2]["par"] == pytest.approx(records[1]["par"], rel=1e-9)


@pytest.mark.parametrize("h5", [True, False], ids=["raw file", "kept offsets"])
@pytest.mark.asyncio
async def test_a_refit_of_a_record_from_before_the_axis_keeps_what_it_knew(
    filestore, h5
):
    """A file fitted once before records kept the axis: its record is a fit
    whose lines were observed on the acquisition axis, and the converter's
    record is gone. The refit reads the axis from the TofDaq file where there
    is one, and keeps the first fit's per-line offsets where there is not; a
    second refit loses neither."""
    filename = _acquired_file(filestore, 80.0)
    sample_file = _sample_file(
        filename, {**support.axis_off(80.0), "status": "unfitted"}
    )
    old = await _fit_and_apply(sample_file)
    for key in ("acquisition_axis", "fitted_at"):
        old.pop(key)
    sample_file.mz_calibration = copy.deepcopy(old)
    first = _kept_offsets(old)
    assert first == pytest.approx([80.0] * len(first), abs=0.1)

    tofdaq = (lambda: _write_tofdaq(filename, 80.0)) if h5 else None
    refits = [
        await _fit_and_apply(sample_file, before_apply=tofdaq),
        # The second has what the first kept, from the record it replaces
        await _fit_and_apply(sample_file),
    ]

    for refit in refits:
        assert ("acquisition_axis" in refit) is h5
        assert ("acquisition_offsets" in refit) is not h5
        assert _kept_offsets(refit) == pytest.approx(first, abs=1e-3 if h5 else 1e-9)
        assert refit["acquisition_drift_ppm"] == pytest.approx(
            sum(first) / len(first), abs=1e-3
        )
    if not h5:
        assert refits[1]["acquisition_offsets"] == refits[0]["acquisition_offsets"]
