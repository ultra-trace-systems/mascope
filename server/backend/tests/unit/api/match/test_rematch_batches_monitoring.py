"""
The batch rematch aggregate pages only for faults.

``rematch_batches`` swallows each nested ``rematch_batch`` failure so the run
can continue, and logged every one of them with ``runtime.logger.exception``:
an ERROR record carrying the exception. ``mascope_runtime.logging`` installs
its error-monitoring sink at WARNING, and that sink calls
``sentry_sdk.capture_exception`` for any record carrying one - so each
swallowed failure filed a monitoring event, whatever it actually was.

Most of them are not faults. The nested rematch is a dependent task, so its
partial-success warning ("Matching produced no results for 3 of 12 samples")
is re-raised to this aggregate as an ``ApiException`` with status 200, and a
batch id naming something that is not there arrives as a 404. Both are
routine - ``process_exception`` classifies them as such and logs them at INFO
- and both were paging.

What must not move:

- a routine client-class outcome keeps its server-side record, reason
  included, at INFO where the monitoring sink does not reach;
- a genuine fault is untouched: still ERROR, still carrying the traceback the
  sink reports, still under the same message and attributed to
  ``rematch_batches``.

The aggregate is driven through ``__wrapped__`` with the per-batch rematch,
the progress notifications and the name lookup mocked, so no database and no
Socket.IO server are involved.
"""

from unittest.mock import AsyncMock, patch

import pytest
from conftest import counting_session

from mascope_backend.api.controllers.match.match_controller import rematch_batches
from mascope_backend.api.lib.exceptions.api_exceptions import (
    ApiException,
    NotFoundException,
)
from mascope_backend.runtime import runtime


_CTRL = "mascope_backend.api.controllers.match.match_controller"

#: The level the error-monitoring sink is installed at (see
#: ``mascope_runtime.logging.RuntimeLogging.configure``). Anything at or above
#: it becomes a GlitchTip event; only INFO and below are free.
_MONITORED_LEVEL = "WARNING"


class _Records:
    """
    Everything the run logged, and the subset error monitoring would export.

    A loguru sink at DEBUG so the INFO record that must survive is visible
    too - the point of the fix is to stop paging, not to lose the record.
    """

    def __init__(self):
        self.records = []

    def __enter__(self):
        self._sink_id = runtime.logger.add(
            lambda message: self.records.append(message.record), level="DEBUG"
        )
        return self

    def __exit__(self, *exc_info):
        runtime.logger.remove(self._sink_id)
        return False

    @property
    def monitored(self) -> list:
        """The records the monitoring sink would pick up (WARNING and above)."""
        threshold = runtime.logger.level(_MONITORED_LEVEL).no
        return [record for record in self.records if record["level"].no >= threshold]

    def at(self, level_name: str) -> list:
        return [record for record in self.records if record["level"].name == level_name]


async def _rematch_logging(outcome: Exception) -> _Records:
    """
    Rematch two batches where the first one raises, capturing the log.

    The second batch succeeds so the aggregate takes its partial-success
    path, which is what a real mixed run does.

    :param outcome: The exception the first batch's rematch raises.
    :type outcome: Exception
    :return: The records the aggregate emitted.
    :rtype: _Records
    """

    async def _one_batch(sample_batch_id: str, **kwargs) -> dict:
        if sample_batch_id == "b1":
            raise outcome
        return {"status": "success", "data": {"computed_samples_count": 1}}

    with (
        patch(f"{_CTRL}.rematch_batch", AsyncMock(side_effect=_one_batch)),
        patch(f"{_CTRL}.async_session", counting_session),
        patch(f"{_CTRL}.send_progress_user_notification", AsyncMock()),
        patch(f"{_CTRL}._fetch_sample_batch_names", AsyncMock(return_value={})),
        _Records() as records,
        pytest.raises(ApiException),
    ):
        await rematch_batches.__wrapped__(
            sample_batch_ids=["b1", "b2"],
            user_id=1,
            process_id="batches",
        )
    return records


@pytest.mark.parametrize(
    "routine",
    [
        pytest.param(
            ApiException("Matching produced no results for 3 of 12 samples", {}, 200),
            id="partial-success warning re-raised by the dependent task",
        ),
        pytest.param(
            ApiException("Some samples could not be matched", {}, 207),
            id="multi-status warning",
        ),
        pytest.param(
            ApiException("Sample batch not found", {}, 404),
            id="404 as the background-task decorator re-raises it",
        ),
        pytest.param(
            NotFoundException("Sample batch b1 not found"),
            id="404 raised directly",
        ),
    ],
)
@pytest.mark.asyncio
async def test_a_routine_batch_failure_is_not_a_monitoring_event(routine):
    """
    A result and a client naming something that is not there are both normal
    operation. Neither is anything an operator can act on, so neither may
    reach error monitoring - and both keep their record one level down.
    """
    records = await _rematch_logging(routine)

    assert records.monitored == [], [
        (record["level"].name, record["message"]) for record in records.monitored
    ]
    # The exact record, not merely "b1 appears somewhere in the log": the
    # aggregate's own summary line names the batch and its reason too, so a
    # substring check here would pass with this record gone.
    logged = [record["message"] for record in records.at("INFO")]
    assert f"Batch b1 did not rematch: {routine}" in logged, logged


@pytest.mark.asyncio
async def test_a_server_fault_still_pages_with_its_traceback():
    """
    The half that must not move. An exception the aggregate never mapped is
    an internal fault: same ERROR, same message, and the traceback the
    monitoring sink reports with ``capture_exception``.
    """
    fault = RuntimeError("connection pool exhausted")

    records = await _rematch_logging(fault)

    errors = records.at("ERROR")
    assert len(errors) == 1
    assert errors[0]["message"] == "Unexpected error rematching batch b1"
    # capture_exception needs the (type, value, traceback) loguru attaches.
    assert errors[0]["exception"] is not None
    assert errors[0]["exception"].value is fault
    # Attributed to the aggregate, not to the helper that picks the level.
    assert errors[0]["function"] == "rematch_batches"
    assert records.monitored == errors


@pytest.mark.asyncio
async def test_a_5xx_api_exception_still_pages():
    """
    An ``ApiException`` is not by itself routine - the background-task
    decorator wraps a database failure into one too. The status decides,
    exactly as it does in ``process_exception``.
    """
    records = await _rematch_logging(
        ApiException("Failed to rematch batch. Database operation failed.", {}, 500)
    )

    errors = records.at("ERROR")
    assert len(errors) == 1
    assert errors[0]["message"] == "Unexpected error rematching batch b1"
    assert errors[0]["exception"] is not None
