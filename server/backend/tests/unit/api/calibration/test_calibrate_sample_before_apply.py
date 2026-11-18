"""
The caller's last word on a fit (``before_apply``) in
``calibration_mz_calibrate_sample``, and a calibration nobody asked for in the
app.

The TOF re-calibration runner (``recalibrate_tof``) calibrates a file as
ingest does, but turns down a fit below the quality bar that would replace one
that cleared it, and records what the file had before one it lets through.
So the hook sees the fit before anything of the file changes, and a fit it
turns down changes nothing. The runner has no user: its progress has nobody
to go to, which must not fail the calibration.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from mascope_backend.api.controllers.calibration.calibration_controller import (
    _user_rooms,
    calibration_mz_calibrate_sample,
)
from mascope_backend.api.controllers.sample.lib.fetch_affected_sample_data import (
    AffectedSampleData,
)


_CTRL = "mascope_backend.api.controllers.calibration.calibration_controller"

FIT = {"mode": 0, "par": [10850.0, -18500.0], "quality": {"n_points": 5}}


async def _calibrate(before_apply=None, user_id=7):
    sample = SimpleNamespace(
        sample_item_id="si-1",
        sample_item_name="sample 1",
        sample_file_id="sf-1",
        filename="TOF-1_file",
        instrument="TOF-1",
    )
    apply = AsyncMock()
    progress = AsyncMock()
    with (
        patch(f"{_CTRL}.move_to_current_versions", AsyncMock()),
        patch(f"{_CTRL}.fetch_sample", AsyncMock(return_value=sample)),
        patch(
            f"{_CTRL}.fetch_affected_sample_data",
            AsyncMock(return_value=AffectedSampleData(["si-1"], ["sb-1"], [], [])),
        ),
        patch(f"{_CTRL}.send_progress_user_notification", progress),
        patch(
            f"{_CTRL}.calibration_mz_fit",
            AsyncMock(return_value={"data": {"fit": dict(FIT)}}),
        ),
        patch(f"{_CTRL}.calibration_mz_apply", apply),
    ):
        result = await calibration_mz_calibrate_sample.__wrapped__(
            sample_item_id="si-1",
            mz_calibration_params=None,
            before_apply=before_apply,
            user_id=user_id,
        )
    return result, apply, progress


@pytest.mark.asyncio
async def test_a_fit_turned_down_is_not_applied():
    seen = []

    async def no(fit):
        seen.append(fit)
        return False

    result, apply, _ = await _calibrate(before_apply=no)

    apply.assert_not_awaited()
    assert seen == [FIT]
    assert result["data"] == {"withheld": True}
    assert result["_notification_data"]["affected_sample_batch_ids"] == ["sb-1"]


@pytest.mark.asyncio
async def test_a_fit_let_through_is_applied_as_it_was_shown():
    async def yes(fit):
        return True

    result, apply, _ = await _calibrate(before_apply=yes)

    apply.assert_awaited_once()
    assert apply.await_args.kwargs["fit"] == FIT
    assert "withheld" not in result["data"]


@pytest.mark.asyncio
async def test_without_a_last_word_every_fit_is_applied():
    _, apply, _ = await _calibrate()

    apply.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_calibration_nobody_asked_for_tells_nobody():
    _, apply, progress = await _calibrate(user_id=None)

    apply.assert_awaited_once()
    for call in progress.await_args_list:
        assert call.args[0].data["_room_ids"] == []


def test_a_user_who_asked_is_told():
    assert _user_rooms(7) == [7]
    assert _user_rooms(None) == []


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
            ionization_mechanism_ids=["mech-1"],
            ionization_mode_polarity="-",
        )


@pytest.mark.parametrize(("user_id", "rooms"), [(None, []), (7, [7])])
@pytest.mark.asyncio
async def test_a_fit_reports_its_progress_to_whoever_asked(user_id, rooms):
    """The fit's progress travels with the handler, which reports it as it
    goes: a fit nobody asked for must have no room of None to report to."""
    from mascope_backend.api.controllers.calibration import calibration_controller
    from mascope_backend.api.models.calibration.calibration_pydantic_model import (
        MzCalibrationParams,
        TofCalibrationParams,
    )

    sample = SimpleNamespace(
        sample_item_id="si-1",
        sample_item_name="tof",
        sample_file_id="sf-1",
        filename="TOF-1_file",
        ionization_mode_id="im-1",
    )
    handed = {}

    def handler(filename, params, notification):
        handed["notification"] = notification
        return SimpleNamespace(
            fit=AsyncMock(),
            to_dict=lambda: {
                "fit": None,
                "stats": None,
                "error": None,
                "warning": None,
            },
            segments=None,
            calibrant_cap=None,
            offset=None,
            fitted_on=None,
        )

    with (
        patch(f"{_CTRL}.fetch_sample", AsyncMock(return_value=sample)),
        patch(f"{_CTRL}.async_session", _Session),
        patch(
            f"{_CTRL}.fetch_affected_sample_data",
            AsyncMock(return_value=([], [], [], [])),
        ),
        patch(
            f"{_CTRL}.calibration_params_factory",
            lambda filename: TofCalibrationParams(),
        ),
        patch(f"{_CTRL}.get_calibration_handler", handler),
    ):
        await calibration_controller.calibration_mz_fit.__wrapped__(
            sample_item_id="si-1",
            mz_calibration_params=MzCalibrationParams(refine_window=100),
            user_id=user_id,
        )

    assert handed["notification"].data["_room_ids"] == rooms
