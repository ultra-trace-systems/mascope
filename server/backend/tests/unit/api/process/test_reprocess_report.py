"""
A re-processing run reports counts and its first failures, never every file.

``re_process_sample_files`` takes any number of files - Raw files can select
every file of an instrument - and its completion notification, like every
Socket.IO emit, is published to every backend process through Redis pub/sub.
A report with a record per file, and a message with a line per failure, was
the size of the request. The message names the first
``MAX_LISTED_REPROCESS_FAILURES`` failures and counts the rest, as the batch
calibration and rematch aggregates do, and the notification carries the
counts and the failures the message names.

The batches the run touched are carried on the failure paths as well, where
the background-task decorator resolves the run's reload rooms: a run that
re-processed some files and failed others has still rebuilt those files'
samples, and the views showing them have to reload.

All external dependencies are scripted - no database, file or Socket.IO.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mascope_backend.api.controllers.sample.files.process import service
from mascope_backend.api.lib.exceptions.api_exceptions import ApiException


_SVC = "mascope_backend.api.controllers.sample.files.process.service"
# The background-task decorator's module, which imports handle_notifications
# by name: patched there, the decorator sees the mock.
_FEATURES = "mascope_backend.api.lib.api_features"
_UTILS = "mascope_backend.api.lib.utils"

LISTED = service.MAX_LISTED_REPROCESS_FAILURES
BATCH = "batch-0000000001"


def _sample_file(index: int) -> MagicMock:
    sample_file = MagicMock()
    sample_file.sample_file_id = f"file-{index:011d}"
    sample_file.filename = f"2026.01.01_instrumentX_file{index:05d}"
    return sample_file


class _Session:
    """The two queries the run makes: the files, then their user-made samples."""

    def __init__(self, files):
        self._files = files

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def execute(self, _statement):
        result = MagicMock()
        result.scalars.return_value.all.return_value = list(self._files)
        result.all.return_value = []
        return result


async def _re_process(files: int, failing: set[int], decorated: bool = False):
    """Re-process ``files`` files, of which those in ``failing`` are claimed
    by another run already. Returns the result, or raises what the run raised.
    """
    sample_files = [_sample_file(index) for index in range(files)]
    busy = {sample_files[index].sample_file_id for index in failing}

    async def claim(sample_file_ids, _detail):
        return [] if sample_file_ids[0] in busy else list(sample_file_ids)

    pipeline = AsyncMock(
        return_value={
            "_notification_data": {
                "affected_sample_batch_ids": [BATCH],
                "affected_sample_item_ids": [],
            }
        }
    )
    run = service.re_process_sample_files
    with (
        patch(f"{_SVC}.async_session", lambda: _Session(sample_files)),
        patch(f"{_SVC}.resolve_ionization_modes_by_tokens", AsyncMock()),
        patch(f"{_SVC}.claim_for_processing", AsyncMock(side_effect=claim)),
        patch(f"{_SVC}.reset_mz_calibration", AsyncMock()),
        patch(
            f"{_SVC}._clear_sample_items_for_reprocessing",
            AsyncMock(return_value=[BATCH]),
        ),
        patch(f"{_SVC}.auto_process_sample_file", pipeline),
    ):
        if not decorated:
            return await run.__wrapped__(
                sample_file_ids=[sf.sample_file_id for sf in sample_files],
                user_id=1,
                process_id="reprocess",
            )
        return await run(
            sample_file_ids=[sf.sample_file_id for sf in sample_files],
            independent_transaction=True,
            user_id=1,
            process_id="reprocess",
        )


def _weight(payload) -> int:
    return len(json.dumps(payload).encode())


@pytest.mark.asyncio
async def test_a_failed_run_names_its_first_files_and_counts_the_rest():
    extra = 5
    files = LISTED + extra

    with pytest.raises(ApiException) as excinfo:
        await _re_process(files, failing=set(range(files)))

    error = excinfo.value
    assert error.status_code == 422
    lines = error.user_message.splitlines()
    assert lines[0] == f"Failed to re-process all {files} sample files."
    assert len(lines) == LISTED + 2
    assert lines[1] == (
        "2026.01.01_instrumentX_file00000: "
        "2026.01.01_instrumentX_file00000 is being processed already"
    )
    assert lines[-1] == f"...and {extra} more."

    report = error.tech_message["_notification_data"]
    assert report["summary"] == {"processed": 0, "failed": files, "total": files}
    assert len(report["failed_files"]) == LISTED


@pytest.mark.asyncio
async def test_a_successful_run_weighs_the_same_whatever_its_size():
    small = await _re_process(5, failing=set())
    large = await _re_process(200, failing=set())

    assert large["_notification_data"]["summary"] == {
        "processed": 200,
        "failed": 0,
        "total": 200,
    }
    assert large["_notification_data"]["affected_sample_batch_ids"] == [BATCH]
    # Forty times the files, the same report: only the counts' digits differ.
    assert (
        _weight(large["_notification_data"]) - _weight(small["_notification_data"]) < 8
    )


@pytest.mark.asyncio
async def test_a_partial_run_reports_counts_and_its_first_failures():
    files = 60
    failing = set(range(0, files, 2))

    with (
        patch(f"{_FEATURES}.handle_notifications", AsyncMock()) as notify,
        patch(f"{_UTILS}.emit_record_reload", AsyncMock()),
    ):
        result = await _re_process(files, failing=failing, decorated=True)

    assert result is None  # reported, not returned: a warning
    notification = notify.await_args.args[1]
    assert notification.status == "warning"
    assert len(notification.message.splitlines()) == LISTED + 2
    report = notification.error["detail"]["_notification_data"]
    assert report["summary"] == {
        "processed": files - len(failing),
        "failed": len(failing),
        "total": files,
    }
    assert len(report["failed_files"]) == LISTED


@pytest.mark.asyncio
async def test_a_partial_run_still_reloads_the_batches_it_rebuilt():
    """The files that did re-process have new samples, and their views must
    reload - the failures alongside do not change that."""
    with (
        patch(f"{_FEATURES}.handle_notifications", AsyncMock()),
        patch(f"{_UTILS}.emit_record_reload", AsyncMock()) as reload,
    ):
        await _re_process(4, failing={1}, decorated=True)

    reloaded = {
        (call.kwargs["record_type"], tuple(call.kwargs["room"]))
        for call in reload.await_args_list
    }
    assert ("match", (BATCH,)) in reloaded
