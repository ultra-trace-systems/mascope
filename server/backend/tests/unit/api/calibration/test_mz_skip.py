"""Unit tests for the calibration skip marker.

The database, the sample-file update and the socket emit are mocked; what is
asserted is the semantics: the record written into
``SampleFile.mz_calibration``, which prior states may be replaced by it, and
that clearing it touches nothing else.

The record is deliberately ``verified: True``. The match computation reads that
flag (``match_controller``) and treats a missing record as verified, so a
skipped sample keeps matching exactly as an uncalibrated one did - the point of
the marker is attribution, not gating.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mascope_backend.api.controllers.calibration.calibration_controller import (
    calibration_mz_skip,
    calibration_mz_unskip,
    reset_mz_calibration,
)
from mascope_backend.api.lib.exceptions.api_exceptions import ApiException


_CTRL = "mascope_backend.api.controllers.calibration.calibration_controller"

_APPLIED = {
    "status": "ok",
    "verified": True,
    "mode": "one-point",
    "par": {"calibration_factor": 1.0000125},
}
_FAILED = {"status": "failed", "verified": False, "error": "no calibration peaks"}
_SKIPPED = {"status": "skipped", "verified": True, "reason": "blank file"}
#: What every converted Tofwerk h5 file carries from the moment it lands: the
#: instrument's own acquisition mass calibration, copied onto the sample file
#: by the file converter. No status, no ``verified`` - and no fit of Mascope's.
_TOF_ACQUISITION = {"mode": 2, "par": [1234.5, 0.5, -0.0001]}


def _sample_file(mz_calibration=None, filename="orbitrap_sample"):
    return SimpleNamespace(
        sample_file_id="sf-1",
        filename=filename,
        mz_calibration=mz_calibration,
        to_dict=lambda: {"sample_file_id": "sf-1", "filename": filename},
    )


def _session_returning(user):
    """A stand-in for ``async_session`` whose session resolves the user."""
    session = MagicMock()
    session.get = AsyncMock(return_value=user)
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    return factory


class _Run:
    """What one controller call left behind: the mocks it touched and its result."""

    def __init__(self, sample_file, update, emit, result=None):
        self.sample_file = sample_file
        self.update = update
        self.emit = emit
        self.result = result

    @property
    def record(self):
        return self.sample_file.mz_calibration


async def _run(
    controller,
    sample_file,
    batch_ids=("batch-1",),
    username="operator_a",
    **kwargs,
):
    update = AsyncMock()
    emit = AsyncMock()
    affected = SimpleNamespace(
        affected_sample_item_ids=["item-1"],
        affected_sample_batch_ids=list(batch_ids),
    )
    user = SimpleNamespace(username=username) if username else None
    with (
        patch(f"{_CTRL}.fetch_sample_file", AsyncMock(return_value=sample_file)),
        patch(f"{_CTRL}.update_sample_file", update),
        patch(f"{_CTRL}.SampleFileUpdate", MagicMock(side_effect=lambda **kw: kw)),
        patch(f"{_CTRL}.fetch_affected_sample_data", AsyncMock(return_value=affected)),
        patch(f"{_CTRL}.emit_record_reload", emit),
        patch(f"{_CTRL}.async_session", _session_returning(user)),
    ):
        result = await controller(**kwargs)
    return _Run(sample_file, update, emit, result)


# ============= Writing the marker =============


@pytest.mark.asyncio
async def test_skip_writes_an_attributed_record():
    sample_file = _sample_file()

    run = await _run(
        calibration_mz_skip,
        sample_file,
        filename="orbitrap_sample",
        reason="Blank file, nothing to calibrate against",
        user_id=7,
    )

    assert run.record["status"] == "skipped"
    assert run.record["reason"] == "Blank file, nothing to calibrate against"
    assert run.record["skipped_by"] == "operator_a"
    assert run.record["skipped_by_user_id"] == 7
    assert run.record["skipped_utc"]
    run.update.assert_awaited_once()
    assert run.result["data"]["mz_calibration"] == run.record


@pytest.mark.asyncio
async def test_skip_does_not_gate_matching():
    """``verified`` stays True: skipping is a label, not a failure."""
    run = await _run(
        calibration_mz_skip,
        _sample_file(),
        filename="orbitrap_sample",
        reason="not needed",
        user_id=7,
    )

    assert run.record["verified"] is True


@pytest.mark.asyncio
async def test_skip_replaces_a_failed_marker():
    """The state a skip most often resolves is a given-up automatic attempt."""
    run = await _run(
        calibration_mz_skip,
        _sample_file(mz_calibration=dict(_FAILED)),
        filename="orbitrap_sample",
        reason="no calibrant collection for this mode",
        user_id=7,
    )

    assert run.record["status"] == "skipped"
    assert "error" not in run.record


@pytest.mark.asyncio
async def test_skip_re_attributes_an_existing_skip():
    """Re-skipping is an edit of the label, not a conflict."""
    run = await _run(
        calibration_mz_skip,
        _sample_file(mz_calibration=dict(_SKIPPED)),
        filename="orbitrap_sample",
        reason="corrected reason",
        user_id=9,
    )

    assert run.record["reason"] == "corrected reason"
    assert run.record["skipped_by_user_id"] == 9


@pytest.mark.asyncio
async def test_skip_keeps_an_unknown_account_out_of_the_label():
    """A user_id that resolves to nothing leaves the display name empty."""
    run = await _run(
        calibration_mz_skip,
        _sample_file(),
        username=None,
        filename="orbitrap_sample",
        reason="blank",
        user_id=7,
    )

    assert run.record["skipped_by"] is None
    assert run.record["skipped_by_user_id"] == 7


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "existing,filename",
    [
        (_TOF_ACQUISITION, "tof_sample"),
        (_TOF_ACQUISITION, "tof_blank"),
    ],
    ids=["tof-sample", "tof-blank"],
)
async def test_skip_accepts_the_instruments_acquisition_calibration(existing, filename):
    """A statusless record is not automatically a fit Mascope applied.

    Tofwerk h5 files arrive carrying the instrument's own mass calibration, so
    a guard that reads "record present, no status" as "already calibrated"
    refuses every TOF file on the deployment - blanks included, which are the
    archetypal skip target - and the feature is unreachable for the whole
    instrument class.
    """
    run = await _run(
        calibration_mz_skip,
        _sample_file(mz_calibration=dict(existing), filename=filename),
        filename=filename,
        reason="blank file",
        user_id=7,
    )

    assert run.record["status"] == "skipped"
    assert run.record["reason"] == "blank file"
    # The acquisition record is replaced, not merged into.
    assert "mode" not in run.record


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "existing",
    [_APPLIED, {"verified": True, "mode": "one-point"}],
    ids=["applied", "legacy-without-status"],
)
async def test_skip_refuses_a_calibrated_file(existing):
    """A file whose m/z axis was actually rewritten cannot be called skipped.

    The axis stays calibrated whatever the record says, so overwriting it here
    would make the badge lie and would drop the fit's quality block with it.
    """
    sample_file = _sample_file(mz_calibration=dict(existing))

    with pytest.raises(ApiException) as excinfo:
        await _run(
            calibration_mz_skip,
            sample_file,
            filename="orbitrap_sample",
            reason="blank",
            user_id=7,
        )

    assert excinfo.value.status_code == 409
    assert sample_file.mz_calibration == existing


# ============= Announcing the change =============


@pytest.mark.asyncio
async def test_skip_reloads_the_affected_batches():
    """The sample browser repaints its badge off ``match_reload``."""
    run = await _run(
        calibration_mz_skip,
        _sample_file(),
        batch_ids=("batch-1", "batch-2"),
        filename="orbitrap_sample",
        reason="blank",
        user_id=7,
    )

    run.emit.assert_awaited_once_with(record_type="match", room=["batch-1", "batch-2"])


@pytest.mark.asyncio
async def test_skip_of_an_unreferenced_file_is_not_broadcast():
    """No batches means no rooms - and ``room=None`` broadcasts to everyone."""
    run = await _run(
        calibration_mz_skip,
        _sample_file(),
        batch_ids=(),
        filename="orbitrap_sample",
        reason="blank",
        user_id=7,
    )

    run.emit.assert_not_awaited()


# ============= Surviving a re-process =============


@pytest.mark.asyncio
async def test_reprocessing_keeps_a_skip_marker():
    """Re-processing restores the acquisition axis; it does not un-declare a skip.

    ``reset_mz_calibration`` runs per file at the top of a re-process. Left to
    its usual path it would clear the whole record for an Orbitrap file - the
    attributed marker gone with no notification, the badge back to the
    ambiguous blank the feature exists to remove - while a TOF file kept it,
    because the instrument-type check returns first.
    """
    sample_file = _sample_file(mz_calibration=dict(_SKIPPED))
    update = AsyncMock()

    with (
        patch(f"{_CTRL}.m_name") as m_name,
        patch(f"{_CTRL}.m_io") as m_io,
        patch(f"{_CTRL}.update_sample_file", update),
    ):
        m_name.get_instrument_type.return_value = "orbi"
        reset = await reset_mz_calibration(sample_file)

    assert reset is False
    assert sample_file.mz_calibration == _SKIPPED
    update.assert_not_awaited()
    m_io.update_props.assert_not_called()


@pytest.mark.asyncio
async def test_reprocessing_still_clears_an_applied_fit():
    """The guard above is about skips only - a real fit must still be reset."""
    sample_file = _sample_file(mz_calibration=dict(_APPLIED))
    update = AsyncMock()

    with (
        patch(f"{_CTRL}.m_name") as m_name,
        patch(f"{_CTRL}.m_io") as m_io,
        patch(f"{_CTRL}.SampleFileUpdate", MagicMock(side_effect=lambda **kw: kw)),
        patch(f"{_CTRL}.update_sample_file", update),
    ):
        m_name.get_instrument_type.return_value = "orbi"
        m_io.read_props.return_value = {"mz_calibration": {"par": {}}}
        reset = await reset_mz_calibration(sample_file)

    assert reset is True
    assert sample_file.mz_calibration is None


# ============= Clearing the marker =============


@pytest.mark.asyncio
async def test_unskip_clears_the_record():
    run = await _run(
        calibration_mz_unskip,
        _sample_file(mz_calibration=dict(_SKIPPED)),
        filename="orbitrap_sample",
        user_id=7,
    )

    assert run.record is None
    run.update.assert_awaited_once()
    run.emit.assert_awaited_once_with(record_type="match", room=["batch-1"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "existing",
    [None, _FAILED, _APPLIED],
    ids=["never-attempted", "failed", "applied"],
)
async def test_unskip_touches_nothing_but_a_skip(existing):
    """Clearing is an undo of the marker, not a way to erase other records."""
    sample_file = _sample_file(
        mz_calibration=dict(existing) if existing else None,
    )

    with pytest.raises(ApiException) as excinfo:
        await _run(
            calibration_mz_unskip,
            sample_file,
            filename="orbitrap_sample",
            user_id=7,
        )

    assert excinfo.value.status_code == 409
    assert sample_file.mz_calibration == existing
