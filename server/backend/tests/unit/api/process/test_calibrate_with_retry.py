"""
Unit tests for ``calibrate_with_retry``.

The retry loop exists to widen the m/z error tolerance until a poor spectrum
yields enough calibration peaks. Tests verify which failures it retries, and
that giving up on a data condition stays below the error-monitoring threshold
(records at WARNING and above are exported; see mascope_runtime.logging).

All external dependencies are mocked - no DB, file I/O, or Socket.IO required.
"""

from unittest.mock import AsyncMock, patch

import pytest

from mascope_backend.api.controllers.sample.files.process.service import (
    CALIBRATION_ITERATIONS,
    calibrate_with_retry,
)
from mascope_backend.api.lib.exceptions.api_exceptions import ApiException
from mascope_backend.runtime import runtime


_SVC = "mascope_backend.api.controllers.sample.files.process.service"

_SAMPLE = {
    "sample_item_id": "si-001",
    "sample_item_name": "2025-09-20 10:30:00",
    "filename": "ORBIONSTAR_2025.09.20_test_file.raw",
}


async def _run(side_effect) -> tuple[AsyncMock, list]:
    """Run calibrate_with_retry against a mocked calibration, capturing logs.

    Also mocks the failure recorder so no database is needed; the mock and the
    return value are exposed as attributes on the returned calibrate mock
    (``recorder`` / ``result``) for outcome assertions.
    """
    records = []
    sink_id = runtime.logger.add(
        lambda message: records.append(message.record), level="TRACE"
    )
    calibrate = AsyncMock(side_effect=side_effect)
    recorder = AsyncMock()
    try:
        with (
            patch(f"{_SVC}.calibration_mz_calibrate_sample", calibrate),
            patch(f"{_SVC}._record_calibration_failure", recorder),
        ):
            result = await calibrate_with_retry(sample=_SAMPLE, sample_file_id="sf-001")
    finally:
        runtime.logger.remove(sink_id)
    calibrate.recorder = recorder
    calibrate.result = result
    return calibrate, records


def _monitored(records: list) -> list:
    """Records that would reach error monitoring (WARNING and above)."""
    return [r for r in records if r["level"].no >= runtime.logger.level("WARNING").no]


@pytest.mark.asyncio
async def test_succeeds_without_retrying():
    calibrate, records = await _run(None)

    assert calibrate.await_count == 1
    assert _monitored(records) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [200, 207, 422])
async def test_retryable_statuses_use_every_attempt(status_code):
    """A fit warning or a failed fit is what the widening tolerance targets."""
    calibrate, _ = await _run(
        ApiException("m/z fitting warning: few peaks", {}, status_code)
    )

    assert calibrate.await_count == CALIBRATION_ITERATIONS


@pytest.mark.asyncio
async def test_giving_up_on_a_poor_spectrum_is_not_monitored():
    """Expected data condition, reported to the user through notifications."""
    _, records = await _run(ApiException("m/z fitting warning: few peaks", {}, 200))

    assert _monitored(records) == []
    give_up = [r for r in records if "Gave up m/z calibration" in r["message"]]
    assert len(give_up) == 1
    # The cause travels with the record instead of an empty tail.
    assert "few peaks" in give_up[0]["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [404, 500, 503])
async def test_faults_stop_the_loop_and_are_monitored(status_code):
    """A wider tolerance cannot clear these, so do not spend attempts on them."""
    calibrate, records = await _run(ApiException("Database failed", {}, status_code))

    assert calibrate.await_count == 1
    monitored = _monitored(records)
    assert len(monitored) == 1
    assert monitored[0]["level"].name == "ERROR"
    assert monitored[0]["exception"] is not None
    assert "Database failed" in monitored[0]["message"]


@pytest.mark.asyncio
async def test_tolerance_doubles_between_attempts():
    calls = []

    async def _record(**kwargs):
        calls.append(kwargs["mz_calibration_params"].mz_error_tolerance)
        raise ApiException("m/z fitting warning: few peaks", {}, 200)

    await _run(_record)

    assert len(calls) == CALIBRATION_ITERATIONS
    for previous, current in zip(calls, calls[1:]):
        assert current == previous * 2


@pytest.mark.asyncio
async def test_success_returns_true_and_records_nothing():
    calibrate, _ = await _run(None)

    assert calibrate.result is True
    calibrate.recorder.assert_not_awaited()


@pytest.mark.asyncio
async def test_give_up_records_failure_and_returns_false():
    """The persisted marker is what makes the failure visible in the UI."""
    calibrate, _ = await _run(ApiException("m/z fitting warning: few peaks", {}, 200))

    assert calibrate.result is False
    calibrate.recorder.assert_awaited_once()
    kwargs = calibrate.recorder.await_args.kwargs
    assert calibrate.recorder.await_args.args[0] == "sf-001"
    assert kwargs["attempts"] == CALIBRATION_ITERATIONS


@pytest.mark.asyncio
async def test_fault_records_failure_and_returns_false():
    calibrate, _ = await _run(ApiException("Database failed", {}, 500))

    assert calibrate.result is False
    calibrate.recorder.assert_awaited_once()
    assert calibrate.recorder.await_args.kwargs["attempts"] == 1
