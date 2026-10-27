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

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest
import tof_audit_support as support

import mascope_file.name as m_name
from mascope_backend.api.controllers.calibration import calibration_controller
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


NODE = "mascope_backend.api.controllers.calibration.lib.calibration_mz_fit"

#: The keys a TOF fit adds to its quality block.
NEW_KEYS = {"points", "span", "collection", "model", "node_version"}

#: Each line's S/N, by its exact m/z rounded to whole Th: every peak its own.
SNR = {62: 310.0, 79: 95.0, 81: 90.0, 97: 140.0, 125: 64.0, 188: 21.0, 195: 33.0}


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
    """Five lines, the trimer 40 ppm off: the subset search leaves it out,
    and the record still lists it, with the residual that says why."""
    filename = _write(filestore, off={188: 40.0})
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
    assert [point["retained"] for point in points] == [True, True, True, True, False]
    # By exact m/z, each at the peak it was matched to
    exact = sorted(isotopes[isotopes.relative_abundance >= 0.15].mz)
    assert [point["target_mz"] for point in points] == exact
    stored = audit.read_stored_peaks(filename).peaks
    assert [point["observed_mz"] for point in points] == sorted(stored.mz.values)
    assert [point["snr"] for point in points] == [SNR[round(mz)] for mz in exact]
    # The line left out sits outside the 15 ppm tolerance of the fit to the
    # others, every kept one well inside it
    residuals = [point["residual_ppm"] for point in points]
    assert residuals[-1] > 30.0
    assert max(abs(residual) for residual in residuals[:-1]) < 1.0
    # And the kept ones are the fit's own rows, residual for residual
    *rows, _summary = handler.stats
    assert sorted(row["calibration_mz_error"] for row in rows) == sorted(residuals[:-1])


@pytest.mark.asyncio
async def test_the_residuals_are_the_audits(filestore):
    """The audit reads a line's error under a fit as the fit's m/z at the
    peak's TOF index against the exact m/z; the record's residuals are those,
    for the lines the fit left out as well as the ones it kept."""
    filename = _write(filestore, off={188: 40.0, 79: 3.0})
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
        filestore, support.CALIBRANTS + support.MORE_CALIBRANTS, off={195: 45.0}
    )
    isotopes = support.isotopes(support.CALIBRANTS + support.MORE_CALIBRANTS)

    handler = await _node_fit(filename, isotopes)
    points = handler.fitted_on["points"]

    assert len(points) == 7
    assert [round(point["target_mz"]) for point in points if not point["retained"]] == [
        195
    ]


@pytest.mark.asyncio
async def test_the_span_is_that_of_the_lines_kept(filestore):
    """The trimer at 188 Th was matched and left out: the fit covers the
    lines from NO3- to NO3-.HNO3, and the span says so."""
    filename = _write(filestore, off={188: 40.0})
    isotopes = support.isotopes()

    handler = await _node_fit(filename, isotopes)

    kept = isotopes[isotopes.relative_abundance >= 0.15].mz.sort_values()
    assert handler.fitted_on["span"] == [kept.iloc[0], kept.iloc[-2]]


@pytest.mark.asyncio
async def test_the_span_starts_at_the_lowest_line_kept(filestore):
    """NO3- at 62 Th was matched and left out: the span starts at the Br-
    line above it, not at the lowest line the fit chose from."""
    filename = _write(filestore, off={62: 40.0})
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
    assert handler.fitted_on["model"] == {"mode": 0, "equation": "i = p1*sqrt(m) + p2"}
    assert handler.fitted_on["node_version"] == TOF_CALIBRATION_NODE_VERSION
    assert TOF_CALIBRATION_NODE_VERSION == 1


@pytest.mark.asyncio
async def test_a_fit_that_does_not_come_about_records_nothing(filestore):
    """Two lines are fewer than the model needs: no fit, nothing to record."""
    filename = _write(filestore, ["NO3", "HN2O6"])

    handler = await _node_fit(filename, support.isotopes(["NO3", "HN2O6"]))

    assert handler.fit_result is None
    assert handler.fitted_on is None


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
    filename = _write(filestore, off={188: 40.0})

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
        sent_back["quality"]["points"][-1]["retained"] = True
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
