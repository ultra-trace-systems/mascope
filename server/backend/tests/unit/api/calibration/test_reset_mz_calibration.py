"""Unit tests for reset_mz_calibration and restore_mz_calibration.

The io layer, calibration handler, and sample-file update are mocked; the
tests assert the reset semantics: which files reset, the exact inverse
scaling applied, and that both persistence sites (zarr props + database
record) are cleared and handed back as they were - and that a restore puts
back exactly that.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mascope_backend.api.controllers.calibration.calibration_controller import (
    ClearedMzCalibration,
    reset_mz_calibration,
    restore_mz_calibration,
)


_CTRL = "mascope_backend.api.controllers.calibration.calibration_controller"


def _sample_file(mz_calibration=None, filename="orbitrap_sample"):
    return SimpleNamespace(
        sample_file_id="sf-1",
        filename=filename,
        mz_calibration=mz_calibration,
        to_dict=lambda: {
            "sample_file_id": "sf-1",
            "filename": filename,
            "mz_calibration": None,
        },
    )


async def _run(sample_file, stored_props, instrument_type="orbi", call=None):
    handler = MagicMock()
    handler.apply = AsyncMock()
    update = AsyncMock()
    m_io = MagicMock()
    m_io.read_props.return_value = {"mz_calibration": stored_props}
    m_name = MagicMock()
    m_name.get_instrument_type.return_value = instrument_type
    with (
        patch(f"{_CTRL}.m_io", m_io),
        patch(f"{_CTRL}.m_name", m_name),
        patch(f"{_CTRL}.get_calibration_handler", return_value=handler),
        patch(f"{_CTRL}.update_sample_file", update),
        patch(f"{_CTRL}.SampleFileUpdate", MagicMock(side_effect=lambda **kw: kw)),
    ):
        result = await (call or reset_mz_calibration)(sample_file)
    return result, handler, update, m_io


@pytest.mark.asyncio
async def test_applies_exact_inverse_and_clears_both_records():
    stored = {"mode": "one-point", "par": {"calibration_factor": 1.0000125}}
    sample_file = _sample_file(mz_calibration=dict(stored))

    result, handler, update, m_io = await _run(sample_file, stored)

    assert result == ClearedMzCalibration(stored=stored, recorded=stored)
    fit = handler.apply.await_args.args[0]
    assert fit["par"]["calibration_factor"] == 1.0
    assert fit["par"]["old_factor_scaling"] == pytest.approx(1 / 1.0000125)
    m_io.update_props.assert_called_once_with(
        "orbitrap_sample", {"mz_calibration": None}
    )
    assert sample_file.mz_calibration is None
    update.assert_awaited_once()


@pytest.mark.asyncio
async def test_tof_files_are_left_alone():
    stored = {"mode": "2nd-degree", "par": {"p0": 1.0}}
    sample_file = _sample_file(mz_calibration=dict(stored), filename="tofwerk_x")

    result, handler, update, _ = await _run(sample_file, stored, instrument_type="tof")

    assert result is None
    handler.apply.assert_not_awaited()
    update.assert_not_awaited()


@pytest.mark.asyncio
async def test_never_calibrated_file_is_a_noop():
    sample_file = _sample_file(mz_calibration=None)

    result, handler, update, _ = await _run(sample_file, None)

    assert result is None
    handler.apply.assert_not_awaited()
    update.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_record_without_factor_still_clears():
    # A failed-calibration marker has no par/factor: nothing to rescale, but
    # the marker must be cleared so the file reads as never calibrated.
    failed = {"status": "failed", "verified": False}
    sample_file = _sample_file(mz_calibration=dict(failed))

    result, handler, update, m_io = await _run(sample_file, None)

    assert result == ClearedMzCalibration(stored=None, recorded=failed)
    handler.apply.assert_not_awaited()
    assert sample_file.mz_calibration is None
    update.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_restore_applies_the_cleared_factor_and_puts_both_records_back():
    """From the acquisition axis the reset left: the factor as it was, and
    the database record with what only it carries."""
    stored = {"mode": "one-point", "par": {"calibration_factor": 1.0000125}}
    recorded = {**stored, "status": "ok", "verified": True}
    sample_file = _sample_file(mz_calibration=None)
    cleared = ClearedMzCalibration(stored=stored, recorded=recorded)

    async def restore(sample_file):
        await restore_mz_calibration(sample_file, cleared)

    _, handler, update, m_io = await _run(sample_file, None, call=restore)

    fit = handler.apply.await_args.args[0]
    assert fit["par"] == {
        "old_factor": 1.0,
        "old_factor_scaling": 1.0000125,
        "calibration_factor": 1.0000125,
    }
    # The apply records the fit in the props, as for any apply
    m_io.update_props.assert_not_called()
    # The fit applied is a copy: what was cleared is not added to
    assert stored == {"mode": "one-point", "par": {"calibration_factor": 1.0000125}}
    assert sample_file.mz_calibration == recorded
    update.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_restore_of_a_record_without_a_factor_writes_it_back():
    failed = {"status": "failed", "verified": False}
    sample_file = _sample_file(mz_calibration=None)
    cleared = ClearedMzCalibration(stored=failed, recorded=failed)

    async def restore(sample_file):
        await restore_mz_calibration(sample_file, cleared)

    _, handler, update, m_io = await _run(sample_file, None, call=restore)

    handler.apply.assert_not_awaited()
    m_io.update_props.assert_called_once_with(
        "orbitrap_sample", {"mz_calibration": failed}
    )
    assert sample_file.mz_calibration == failed
    update.assert_awaited_once()
