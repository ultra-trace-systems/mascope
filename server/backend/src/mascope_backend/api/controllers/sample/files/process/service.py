"""
Controller for sample files auto-processing pipeline.

Handles automated creation of ACQUISITION datasets, batches, and sample items, and matching the samples.
"""

import asyncio

from sqlalchemy import delete, select
from sqlalchemy.exc import InterfaceError, OperationalError, SQLAlchemyError
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError

from mascope_backend.api.controllers.calibration.calibration_controller import (
    calibration_mz_calibrate_sample,
    reset_mz_calibration,
)
from mascope_backend.api.controllers.calibration.lib.calibration_mz_fit import (
    calibration_params_factory,
)
from mascope_backend.api.controllers.dataset.acquisition.service import (
    get_acquisition_dataset,
)
from mascope_backend.api.controllers.match.match_controller import (
    match_compute_sample,
    rematch_samples,
)
from mascope_backend.api.controllers.sample.batches.sample_batches_controller import (
    get_or_create_acquisition_batch,
)
from mascope_backend.api.controllers.sample.items.sample_items_controller import (
    create_sample_items,
)
from mascope_backend.api.controllers.sample.lib.fetch_affected_sample_data import (
    fetch_affected_sample_data,
)
from mascope_backend.api.controllers.sample.lib.sample_file_fetch import (
    fetch_sample_file,
)
from mascope_backend.api.lib.api_features import api_controller_background_task
from mascope_backend.api.lib.exceptions.api_exceptions import (
    ApiException,
    raise_api_warning,
)
from mascope_backend.api.models.sample.batches.config import sample_batch_config
from mascope_backend.api.models.sample.batches.sample_batch_pydantic_model import (
    SampleBatchCreate,
)
from mascope_backend.api.models.sample.items.sample_item_pydantic_model import (
    SampleItemCreate,
)
from mascope_backend.api.new.ionization.modes.util import (
    resolve_ionization_modes_by_tokens,
)
from mascope_backend.api.new.peak_assignments.service import (
    auto_assign_sample_peaks,
)
from mascope_backend.db import (
    IonizationMode,
    SampleBatch,
    SampleFile,
    SampleItem,
    async_session,
)
from mascope_backend.db.id import gen_id
from mascope_backend.runtime import runtime
from mascope_backend.socket.records.service import (
    emit_record_deleted,
)


# Number of calibration fitting attempts before giving up
# Chosen so that final m/z error tolerance for TOF would be around 1000 ppm
CALIBRATION_ITERATIONS = 7

#: ApiException status codes a wider m/z error tolerance can actually clear.
#: 200/207 are the fit warnings, where the spectrum did not yield enough
#: matching calibration peaks ("Not enough calibration peaks", "No calibration
#: peaks found"); 422 is a degenerate fit - a zero polynomial coefficient -
#: which more matches can also resolve. Any other status is a fault (missing
#: calibration collection, database failure) that retrying cannot improve.
#: Note this also excludes the transient _RECOVERABLE_STATUS_CODES below:
#: calibrate_with_retry swallows rather than re-raises, so a pool timeout
#: during calibration leaves the sample uncalibrated instead of reaching the
#: pipeline-level retry.
RETRYABLE_CALIBRATION_STATUS = (200, 207, 422)

#: Detached background work this module owns: the auto-processing pipelines
#: started by :func:`spawn_auto_process_sample_file`, and the fire-and-forget
#: rematch task each completed pipeline spawns. asyncio only keeps weak
#: references, so an unreferenced task can be garbage-collected mid-run; the
#: done-callback below also surfaces failures that would otherwise die as
#: unretrieved exceptions. :func:`drain_auto_process_tasks` drains this set at
#: shutdown, so anything added here is work a restart has to account for.
_background_tasks: set[asyncio.Task] = set()

#: Set once this worker has begun shutting down. A pipeline waiting out a retry
#: backoff watches it and gives up at once rather than sleeping through the
#: drain's whole budget, and cancellations are reported more quietly while it is
#: set (see :func:`_report_cancelled`). Never cleared: shutdown is terminal for
#: the process, and the tests that exercise it reset it explicitly.
_shutdown = asyncio.Event()


def _report_cancelled(sample_file_id: str, when: str) -> None:
    """Report a cancelled pipeline at a level that matches its cause.

    Cancellation during a shutdown drain is expected and arrives in bulk - one
    per file still queued behind the ingest gate - and the error-monitoring
    sink groups issues by the formatted message, so an ERROR carrying the
    sample file id opens a separate issue for every file in an interrupted
    burst. The drain reports the count itself as a single error; the per-file
    detail stays at INFO, where the worker log still records exactly which
    files were truncated.

    A cancellation outside a drain is a fault - nobody asked for it - and keeps
    the ERROR that makes it visible.

    :param sample_file_id: File whose pipeline was cancelled.
    :param when: Phrase naming the point it was cancelled at.
    """
    _log_cancellation(
        f"Auto-processing of sample file {sample_file_id} was cancelled "
        f"{when}; it will have no matched peaks"
    )


def _log_cancellation(message: str) -> None:
    """Log a cancellation at INFO during a drain, ERROR otherwise.

    See :func:`_report_cancelled` for why the level moves.

    :param message: The already-formatted description of what was cancelled.
    """
    if _shutdown.is_set():
        runtime.logger.info(message)
    else:
        runtime.logger.error(message)


def _observe_background_task(task: asyncio.Task) -> None:
    """Log the failure of a background task and release its reference."""
    _background_tasks.discard(task)
    if task.cancelled():
        return
    exception = task.exception()
    if exception is not None:
        runtime.logger.opt(exception=exception).error(
            f"Background task '{task.get_name()}' failed"
        )


# Upper bound on auto-processing pipelines running concurrently in this
# worker. Each pipeline opens several database sessions in turn (batch
# get-or-create, calibration, matching), so an unbounded ingest burst - e.g.
# a whole folder of raw files converted back to back - stacks enough
# concurrent sessions to exhaust the worker's connection pool (pool_size +
# max_overflow), and everything that then waits longer than pool_timeout dies
# with "QueuePool limit reached, connection timed out": the converter's API
# calls fail (files quarantined) and pipelines die between sample_file and
# sample items. Excess files wait here and are processed as slots free up.
_AUTO_PROCESS_CONCURRENCY = 3
_auto_process_gate = asyncio.Semaphore(_AUTO_PROCESS_CONCURRENCY)

# Auto-processing runs as a fire-and-forget background task, so a failed
# pipeline has no caller to retry it and the file is silently lost (converted
# but never producing sample items). Transient infrastructure congestion -
# pool starvation in this worker (SQLAlchemy timeout / 503) or a briefly
# unreachable dependency (502/504) - is retried with growing delays; anything
# else (validation errors, missing records, data corruption) would fail
# identically on every attempt and is raised immediately.
_AUTO_PROCESS_RETRIES = 3
_AUTO_PROCESS_RETRY_DELAYS_S = (30, 60, 120)
_RECOVERABLE_STATUS_CODES = {502, 503, 504}


def _is_recoverable_error(exc: Exception) -> bool:
    """Whether a failed auto-process attempt is worth retrying.

    Nested controllers wrap pool starvation into ApiException 503 (and
    dependency outages into 502/504); database errors from direct session use
    in this module can still surface unwrapped.
    """
    if isinstance(exc, ApiException):
        return exc.status_code in _RECOVERABLE_STATUS_CODES
    return isinstance(exc, (SQLAlchemyTimeoutError, OperationalError, InterfaceError))


async def _delete_partial_acquisition_items(sample_file_id: str) -> None:
    """Delete ACQUISITION sample items an earlier pipeline run left behind.

    Sample items are committed independently before calibration + matching, so
    any run that stopped after that point leaves them in place: a failed
    attempt, a run a restart cancelled, a worker killed outright.

    Runs before every attempt, the first included. A re-triggered pipeline
    (``POST /{sample_file_id}/process``) starts at attempt 0, and
    :func:`create_acquisition_batches_and_items` never looks for existing items
    before creating them - so without this, re-processing a file whose first
    run was cut short gives it a duplicate ACQUISITION item per ionization
    mode. For a file processed for the first time the delete matches nothing
    and costs one statement.

    Only ACQUISITION items are removed - user-created samples referencing the
    file are never touched.
    """
    async with async_session() as session:
        result = await session.execute(
            delete(SampleItem).where(
                SampleItem.sample_file_id == sample_file_id,
                SampleItem.sample_item_type == "ACQUISITION",
            )
        )
        await session.commit()
    if result.rowcount:
        runtime.logger.info(
            f"Removed {result.rowcount} partial ACQUISITION sample item(s) "
            f"for sample file {sample_file_id} before retrying"
        )


async def _rematch_when_slot_free(**kwargs) -> dict:
    """
    Run ``rematch_samples`` under the ingest gate.

    Every completed pipeline fires a rematch task for the samples it affected,
    so an ingest burst otherwise piles up unbounded concurrent rematches on
    top of the gated pipelines - the same pool-exhaustion mechanism the gate
    exists to prevent. Spawned as a task, so waiting for a slot here never
    blocks the pipeline that scheduled it.
    """
    async with _auto_process_gate:
        try:
            return await rematch_samples(**kwargs)
        except asyncio.CancelledError:
            # These share _background_tasks with the pipelines, so the drain
            # cancels them too - and they had the same silence: CancelledError
            # is a BaseException, so the controller decorator's except Exception
            # misses it and _observe_background_task returns early for anything
            # cancelled. Without this the drain's "what was cancelled is named
            # below" promise holds for pipelines and quietly fails for these.
            sample_item_ids = kwargs.get("sample_item_ids") or ()
            _log_cancellation(
                f"Rematch of {len(sample_item_ids)} affected sample(s) was "
                "cancelled; their matches are left as they were"
            )
            raise


@api_controller_background_task(
    success_notification_rooms=["instrument"],
    success_reload=[
        ("match", "affected_sample_batch_ids"),
        ("peak_assignment", "affected_sample_batch_ids"),
    ],
    error_notification_rooms=["instrument"],
    error_reload=[
        ("match", "affected_sample_batch_ids"),
        ("peak_assignment", "affected_sample_batch_ids"),
    ],
)
async def auto_process_sample_file(
    sample_file_id: str,
    independent_transaction: bool = False,
    user_id: int | None = None,
    process_id: str | None = None,
    parent_id: str | None = None,
) -> dict:
    """
    Main orchestrator for automatic sample file processing pipeline.

    Processes uploaded sample files automatically into ACQUISITION datasets,
    creating the all data hierarchy if needed.

    Steps:
    - Validate sample file existence
    - Derive year from sample file datetime (prefers datetime_utc over datetime)
    - Get or create per-instrument workspace and year-based ACQUISITION dataset
      (the uploading user becomes workspace owner if the workspace is newly created)
    - Create ACQUISITION batches and sample items for each sample file ionization mode
    - Perform calibration and match computation for created ACQUISITION samples
      (calibration is skipped for blank files or when no calibration collection is set)
    - Schedule rematch tasks for other affected samples
    - Return processing results with affected IDs or UI reloads

    :param sample_file_id: ID of the uploaded sample file
    :type sample_file_id: str
    :param independent_transaction: Indicates whether this operation should be treated
                                    as a standalone transaction.
    :type independent_transaction: bool, optional
    :param user_id: Current user triggered operation (for user notifications)
    :type user_id: int | None, optional
    :param process_id: Process ID for tracking
    :type process_id: str | None, optional
    :param parent_id: Parent process ID for tracking hierarchical processes
    :type parent_id: str | None, optional
    :return: Processing results with affected IDs
    """
    for attempt in range(_AUTO_PROCESS_RETRIES + 1):
        try:
            async with _auto_process_gate:
                # Any earlier run - a failed attempt, or a whole earlier
                # pipeline a restart cut short - may have committed sample
                # items before dying in calibration/matching. Clear them on
                # every attempt, the first included, so neither a retry nor a
                # re-triggered pipeline can duplicate them.
                await _delete_partial_acquisition_items(sample_file_id)
                return await _auto_process_sample_file(
                    sample_file_id=sample_file_id,
                    independent_transaction=independent_transaction,
                    user_id=user_id,
                    process_id=process_id,
                    parent_id=parent_id,
                )
        except asyncio.CancelledError:
            # CancelledError is a BaseException, so every `except Exception` in
            # this pipeline misses it and a cancelled run vanishes without a
            # single line: sample_file and sample_item committed, no match rows,
            # batch still settling `ready`. Say so, then let cancellation
            # continue - it is not ours to swallow.
            _report_cancelled(sample_file_id, f"on attempt {attempt + 1}")
            raise
        except Exception as e:
            if attempt >= _AUTO_PROCESS_RETRIES or not _is_recoverable_error(e):
                # Terminal. Nothing downstream says which file this was: the
                # sample_file row stays, its batch still settles `ready`, and
                # the only trace is an absence - no matched peaks, and no
                # sample items either when a retry had already cleared the
                # partial ones. Name the file here or the shortfall is only
                # discoverable by counting rows afterwards.
                runtime.logger.error(
                    f"Auto-processing gave up on sample file {sample_file_id} "
                    f"after {attempt + 1} attempt(s); it will have no matched "
                    f"peaks: {e}"
                )
                raise
            delay = _AUTO_PROCESS_RETRY_DELAYS_S[attempt]
            runtime.logger.warning(
                f"Auto-processing attempt {attempt + 1} for sample file "
                f"{sample_file_id} hit a recoverable error ({e}); retrying "
                f"in {delay}s"
            )
            # Waited outside the gate so a queued pipeline can use the slot,
            # and raced against the shutdown signal rather than slept through.
            # This is the pipeline's longest-lived state - up to 210 s per file,
            # entered exactly on the congestion that makes a restart likely - so
            # a draining worker that waited it out would spend its whole budget
            # here, leaving none for the pipelines doing real work.
            #
            # Guarded for cancellation separately from the branch above: an
            # exception raised inside an except clause is not caught by that
            # try's other handlers, so a cancellation landing here would bypass
            # it.
            try:
                await asyncio.wait_for(_shutdown.wait(), timeout=delay)
            except asyncio.TimeoutError:
                continue  # the backoff elapsed undisturbed - take the retry
            except asyncio.CancelledError:
                _report_cancelled(sample_file_id, f"while waiting {delay}s to retry")
                raise
            # Shutting down. Report it and stand down as cancelled rather than
            # failed: the file is not at fault, and raising an ordinary
            # exception would send the user an error notification per queued
            # file on every restart.
            _report_cancelled(
                sample_file_id,
                f"while waiting {delay}s to retry, because the worker is shutting down",
            )
            raise asyncio.CancelledError


#: How long a graceful shutdown waits for detached pipelines to finish.
#:
#: Deliberately far below the 210 s a pipeline can spend in retry backoff: the
#: backoff now watches :data:`_shutdown` and stands down as soon as the drain
#: starts (see :func:`auto_process_sample_file`), so the budget buys time for
#: pipelines doing real work rather than for ones asleep. It also has to fit
#: inside the container's stop grace period - Docker SIGKILLs the worker when
#: that expires, and a SIGKILL raises nothing and logs nothing, which is the
#: silence this whole path exists to remove. ``stop_grace_period`` is set above
#: this value in docker-compose.yaml; raise them together or not at all.
AUTO_PROCESS_DRAIN_TIMEOUT_S = 60

#: How long the drain waits for cancelled pipelines to run their handlers.
#: Bounded on purpose: unwinding a cancelled pipeline awaits a rollback, and a
#: rollback on the saturated pool that caused the backoff in the first place can
#: block. Draining is a courtesy to in-flight work and must never be the reason
#: a worker fails to shut down.
_DRAIN_CANCEL_GRACE_S = 10


async def drain_auto_process_tasks(
    timeout: float = AUTO_PROCESS_DRAIN_TIMEOUT_S,
) -> None:
    """Wait for detached auto-processing pipelines during a graceful shutdown.

    Running the pipeline inside the request's ASGI call used to give this for
    free: uvicorn waits on its connection tasks before shutting down, so an
    in-flight pipeline completed. Detaching it (see
    :func:`spawn_auto_process_sample_file`) removed that guarantee - the loop
    would close on a pipeline that was seconds from finishing, leaving the file
    half-processed.

    Anything still running when the timeout expires is cancelled rather than
    abandoned, so :func:`auto_process_sample_file` gets to name the files that
    were truncated instead of them disappearing with the loop.

    :param timeout: Seconds to wait before cancelling what remains.
    """
    try:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        # Before anything else: a pipeline parked in its retry backoff sees this
        # and stands down immediately instead of holding the drain for up to
        # 120 s doing nothing.
        _shutdown.set()

        def pending() -> set[asyncio.Task]:
            """Tasks this loop still has to account for, re-read each pass."""
            # Re-read rather than snapshotted once: a pipeline's last act is to
            # spawn a rematch task into this same set (see
            # :func:`_auto_process_sample_file`), so a single snapshot leaves
            # that task abandoned when the loop closes - unwaited, uncancelled,
            # unreported, which is exactly the failure the drain exists to stop.
            #
            # Only this loop's tasks are ours. The set is module-global and
            # outlives any single loop, while the test suite and dev reloads
            # create several, and a task belonging to another loop can be
            # neither awaited nor cancelled from here - asyncio raises, and a
            # raise in a shutdown hook takes the whole teardown with it. In
            # production there is one loop per worker, so this filters nothing.
            return {
                task
                for task in _background_tasks
                if not task.done() and task.get_loop() is loop
            }

        outstanding = pending()
        if not outstanding:
            return

        runtime.logger.info(
            f"Waiting up to {timeout:.0f}s for {len(outstanding)} background "
            f"task(s) to finish before shutdown"
        )
        while outstanding and (remaining := deadline - loop.time()) > 0:
            await asyncio.wait(outstanding, timeout=remaining)
            outstanding = pending()

        if not outstanding:
            runtime.logger.info("All background task(s) finished before shutdown")
            return

        for task in outstanding:
            task.cancel()
        # One error for the whole drain, not one per file: the sink groups
        # error-monitoring issues by message text, and an interrupted ingest
        # burst can hold hundreds of queued pipelines. Each file is named at
        # INFO by _report_cancelled.
        runtime.logger.error(
            f"Shutdown cancelled {len(outstanding)} background task(s) still "
            f"running after {timeout:.0f}s; what each was working on is named "
            f"at INFO in this worker's log"
        )
        # Let each cancelled pipeline run its CancelledError handler before the
        # loop closes; without this they are abandoned and report nothing.
        await asyncio.wait(outstanding, timeout=_DRAIN_CANCEL_GRACE_S)
    except Exception:
        # Draining is a courtesy to in-flight work; it must never be the reason a
        # worker fails to shut down.
        runtime.logger.exception(
            "Draining background tasks failed; continuing with shutdown"
        )


async def spawn_auto_process_sample_file(
    sample_file_id: str,
    independent_transaction: bool = False,
    user_id: int | None = None,
    process_id: str | None = None,
    parent_id: str | None = None,
) -> None:
    """Start the auto-processing pipeline detached from the request that triggered it.

    Scheduled through FastAPI's ``BackgroundTasks`` so it still starts only after
    the response (and so after the sample_file commit), but the pipeline itself
    runs as a free-standing task rather than inside the request's ASGI call.

    That matters because the pipeline outlives its request by minutes - the retry
    backoff alone reaches into the hundreds of seconds - while the uploader that
    triggered it works on a fixed client timeout. Note that uvicorn does not in
    fact cancel an ASGI call when its client disconnects, so this does not by
    itself explain a pipeline vanishing; what detaching buys is that the
    pipeline's lifetime stops being tied to a connection at all, and that
    shutdown waits for it explicitly (:func:`drain_auto_process_tasks`) rather
    than implicitly.

    The signature mirrors :func:`auto_process_sample_file` rather than taking
    ``**kwargs`` so a renamed or mistyped argument at a trigger site is caught
    where it is written, not as a TypeError inside a detached task.
    """
    forwarded = {
        "sample_file_id": sample_file_id,
        "independent_transaction": independent_transaction,
        "user_id": user_id,
        "parent_id": parent_id,
    }
    # Omitted rather than forwarded as None. api_controller_background_task
    # reads it as ``kwargs.get("process_id", gen_id(8))``, so an absent key
    # gets a generated id while an explicit None reaches UserNotification,
    # whose process_id is a required str - and that ValidationError is raised
    # outside the decorator's try, killing the pipeline before it starts.
    if process_id is not None:
        forwarded["process_id"] = process_id

    task = asyncio.create_task(auto_process_sample_file(**forwarded))
    # asyncio only holds a weak reference to tasks: keep one so the task cannot
    # be garbage-collected mid-run, and observe its outcome so a failure is
    # logged instead of dying as an unretrieved exception.
    _background_tasks.add(task)
    task.add_done_callback(_observe_background_task)


async def _auto_process_sample_file(
    sample_file_id: str,
    independent_transaction: bool = False,
    user_id: int | None = None,
    process_id: str | None = None,
    parent_id: str | None = None,
) -> dict:
    """Gated body of ``auto_process_sample_file`` - see the public wrapper."""
    # Initialize collector for affected sample items
    all_affected_sample_item_ids = set()

    # --- Validate sample file existence --- #
    sample_file = await fetch_sample_file(sample_file_id=sample_file_id)

    # --- Get ACQUISITION dataset for the instrument --- #
    # The year-dataset and the daily batch inside it must be dated off the SAME
    # clock. `datetime` is the instrument's local time and `datetime_utc` its
    # UTC equivalent, and the batch name and sample item name below are both
    # built from the local one - so taking the year from UTC put a file
    # acquired just after local New Year midnight into the previous year's
    # dataset under a batch named for the new year. One instrument-local day
    # then owned batches in two datasets, which no uniqueness on
    # (dataset, name, polarity) can merge.
    file_dt = sample_file.datetime or sample_file.datetime_utc
    acquisition_dataset = (
        await get_acquisition_dataset(
            instrument=sample_file.instrument,
            year=file_dt.year if file_dt else None,
            user_id=user_id,
        )
    ).get("data")

    # --- Create ACQUISITION batches and sample items for each ionization mode --- #
    (
        acquisition_samples,
        acquisition_sample_batches,
    ) = await create_acquisition_batches_and_items(
        sample_file=sample_file,
        dataset_id=acquisition_dataset.get("dataset_id"),
    )

    # Extract batch and sample IDs for notifications
    affected_sample_batch_ids = [
        batch.get("sample_batch_id") for batch in acquisition_sample_batches
    ]
    all_affected_sample_item_ids.update(
        sample["sample_item_id"] for sample in acquisition_samples
    )

    # Blank files are stored without an instrument config and should skip calibration.
    is_blank_sample_file = sample_file.instrument_function_id is None

    # --- Perform calibration and matching for created ACQUISITION samples --- #
    for sample in acquisition_samples:
        sample_item_id = sample["sample_item_id"]

        # Get ionization mode to check calibration collection
        async with async_session() as session:
            ionization_mode = await session.get(
                IonizationMode, sample["ionization_mode_id"]
            )

        # Perform calibration only when collection is configured and file is not blank.
        if (
            ionization_mode
            and ionization_mode.calibration_collection_id
            and not is_blank_sample_file
        ):
            calibrated = await calibrate_with_retry(
                sample=sample,
                sample_file_id=sample_file.sample_file_id,
                user_id=user_id,
                process_id=process_id,
            )
            if not calibrated:
                # The failure marker written by calibrate_with_retry would
                # trip the verified gate in match_compute_sample as a raised
                # warning, failing the whole pipeline; skip matching and
                # assignment explicitly - both assume a calibrated m/z axis.
                runtime.logger.info(
                    "Skipping matching and peak assignment for sample "
                    f"'{sample['sample_item_name']}': m/z calibration failed."
                )
                continue
        elif is_blank_sample_file:
            runtime.logger.info(
                "Skipping m/z calibration for blank file "
                f"'{sample['sample_item_name']}'. "
                "Calibration is not applicable."
            )
        else:
            ionization_mode_name = (
                ionization_mode.ionization_mode_name if ionization_mode else "unknown"
            )
            # INFO: reflects the user's collection configuration and fires for
            # every ingested file while unset
            runtime.logger.info(
                f"Skipping m/z calibration for sample '{sample['sample_item_name']}': "
                "Calibration collection is not set for the ionization mode "
                f"'{ionization_mode_name}'."
            )

        await match_compute_sample(
            sample_item_id=sample_item_id,
            independent_transaction=False,
            user_id=user_id,
            process_id=gen_id(8),
            parent_id=process_id,
        )

        # Assign peaks (Stage A / database-first only) for the new sample. Runs
        # after matching, isolates its own failures, and lets the parent emit the
        # peak_assignment_reload below.
        await auto_assign_sample_peaks(
            sample_item_id=sample_item_id,
            user_id=user_id,
            parent_id=process_id,
        )

    # --- Schedule rematch tasks for other affected samples --- #
    acquisition_sample_item_ids = {
        sample["sample_item_id"] for sample in acquisition_samples
    }
    # exclude the processed sample
    other_affected_sample_item_ids = (
        all_affected_sample_item_ids - acquisition_sample_item_ids
    )

    if other_affected_sample_item_ids:
        task = asyncio.create_task(
            _rematch_when_slot_free(
                sample_item_ids=other_affected_sample_item_ids,
                independent_transaction=True,  # Handle reloads independently
                user_id=user_id,
                process_id=gen_id(8),
            )
        )
        # asyncio only holds a weak reference to tasks: keep one so the task
        # cannot be garbage-collected mid-run, and observe its outcome so a
        # failure is logged instead of dying as an unretrieved exception.
        _background_tasks.add(task)
        task.add_done_callback(_observe_background_task)

        runtime.logger.info(
            "Started independent rematch task for "
            f"{len(other_affected_sample_item_ids)} affected samples"
        )

    # --- Return processed results with affected IDs for UI reloads --- #
    acquisition_samples = (
        await fetch_affected_sample_data(
            sample_item_ids=[
                sample["sample_item_id"] for sample in acquisition_samples
            ],
            include_objects=True,
        )
    ).affected_samples

    return {
        "message": (
            f"Auto-processing complete for {sample_file.filename}, processed "
            f"{len(acquisition_samples) if acquisition_samples else 0} samples."
        ),
        "data": acquisition_samples,
        "_notification_data": {
            "affected_sample_batch_ids": affected_sample_batch_ids,
            "affected_sample_item_ids": list(all_affected_sample_item_ids),
            "instrument": sample_file.instrument,
        },
    }


@api_controller_background_task(
    success_notification_rooms=["user_id"],
    success_reload=[
        ("match", "affected_sample_batch_ids"),
        ("peak_assignment", "affected_sample_batch_ids"),
    ],
    error_notification_rooms=["user_id"],
    error_reload=[
        ("match", "affected_sample_batch_ids"),
        ("peak_assignment", "affected_sample_batch_ids"),
    ],
)
async def re_process_sample_files(
    sample_file_ids: list[str],
    independent_transaction: bool = False,
    user_id: int | None = None,
    process_id: str | None = None,
) -> dict:
    """
    Re-processes multiple sample files by their unique IDs.

    Steps:
    - Validate all sample files exist and have no user-created samples
    - Delete existing ACQUISITION sample items for all files
    - Run auto-process pipeline for each file
    - Return aggregated results

    :param sample_file_ids: List of IDs of the sample files to re-process
    :type sample_file_ids: list[str]
    :param independent_transaction: Indicates whether this operation should be treated
                                    as a standalone transaction.
    :type independent_transaction: bool, optional
    :param user_id: Current user triggered operation (for user notifications)
    :type user_id: int | None, optional
    :param process_id: Process ID for tracking
    :type process_id: str | None, optional
    :return: Processing results with aggregated data
    :rtype: dict
    """
    processed_files = []
    failed_files = []
    affected_sample_batch_ids = set()
    affected_sample_item_ids = set()

    # --- Validate all sample files exist and collect data --- #
    async with async_session() as session:
        result = await session.execute(
            select(SampleFile).where(SampleFile.sample_file_id.in_(sample_file_ids))
        )
        sample_files = result.scalars().all()

    found_ids = {sf.sample_file_id for sf in sample_files}
    missing_ids = set(sample_file_ids) - found_ids

    for missing_id in missing_ids:
        failed_files.append(
            {
                "sample_file_id": missing_id,
                "filename": "unknown",
                "message": f"Sample file with ID '{missing_id}' not found",
            }
        )

    if not sample_files:
        message = f"None of the {len(sample_file_ids)} sample files found"
        raise ApiException(
            user_message=message,
            tech_message={"failed_files": failed_files},
            status_code=404,
        )

    # --- Check for user-created samples --- #
    async with async_session() as session:
        # Query for found_ids
        result = await session.execute(
            select(SampleItem, SampleBatch)
            .join(
                SampleBatch, SampleItem.sample_batch_id == SampleBatch.sample_batch_id
            )
            .where(
                SampleItem.sample_file_id.in_(found_ids),
                SampleItem.sample_item_type != "ACQUISITION",
            )
        )
        user_created_samples = result.all()

    # Map sample_file_id → (sample_item, batch) for fast lookup
    user_samples_dict = {
        sample_item.sample_file_id: (sample_item, batch)
        for sample_item, batch in user_created_samples
    }

    # --- Validate each file --- #
    valid_sample_files = []

    for sample_file in sample_files:
        # Check for user-created samples
        if sample_file.sample_file_id in user_samples_dict:
            sample_item, batch = user_samples_dict[sample_file.sample_file_id]
            failed_files.append(
                {
                    "sample_file_id": sample_file.sample_file_id,
                    "filename": sample_file.filename,
                    "message": (
                        "Cannot re-process file as it is associated with user-created "
                        f"sample in the batch {batch.sample_batch_name}."
                    ),
                }
            )
            continue

        # Verify ionization modes are defined properly
        try:
            await resolve_ionization_modes_by_tokens(sample_file)
        except ValueError as ve:
            # Ionization mode resolution failed
            failed_files.append(
                {
                    "sample_file_id": sample_file.sample_file_id,
                    "filename": sample_file.filename,
                    "message": str(ve),
                }
            )
            continue
        except Exception as e:
            # Other unexpected errors
            failed_files.append(
                {
                    "sample_file_id": sample_file.sample_file_id,
                    "filename": sample_file.filename,
                    "message": f"Failed to resolve ionization modes: {str(e)}",
                }
            )
            runtime.logger.exception(
                "Unexpected error resolving ionization modes for sample file "
                f"{sample_file.filename}"
            )
            continue

        # Passed all validations
        valid_sample_files.append(sample_file)

    # --- Process valid files --- #
    # Each file is reset, cleared and rebuilt in one pass. Resetting the
    # calibration and deleting the sample items for the whole batch up front
    # would commit destruction the loop has not caught up with yet: a run that
    # stops partway - a worker killed mid-deploy, an unhandled failure - would
    # leave every file it never reached with no sample items at all and no
    # calibration, which is worse than the half-processed state re-processing is
    # meant to repair.
    for sample_file in valid_sample_files:
        try:
            # Orbitrap calibration is cumulative (the file's m/z axes are
            # rescaled in place), so without this a re-processed file silently
            # keeps its previous calibration. A failed reset keeps the old
            # calibration; the file still re-processes, so log instead of
            # failing the whole request.
            try:
                await reset_mz_calibration(sample_file)
            except Exception:  # noqa: BLE001 - reset is best-effort per file
                runtime.logger.exception(
                    "Failed to reset m/z calibration for "
                    f"'{sample_file.filename}' before re-processing; the "
                    "previous calibration remains in effect."
                )

            affected_sample_batch_ids.update(
                await _clear_sample_items_for_reprocessing(
                    sample_file_id=sample_file.sample_file_id,
                    independent_transaction=independent_transaction,
                )
            )

            result = await auto_process_sample_file(
                sample_file_id=sample_file.sample_file_id,
                independent_transaction=False,
                user_id=user_id,
                process_id=gen_id(8),
                parent_id=process_id,
            )

            processed_files.append(
                {
                    "sample_file_id": sample_file.sample_file_id,
                    "filename": sample_file.filename,
                    "message": f"Successfully processed file {sample_file.filename}.",
                }
            )

            # Collect notification data
            file_notification_data = result.get("_notification_data", {})
            if "affected_sample_batch_ids" in file_notification_data:
                affected_sample_batch_ids.update(
                    file_notification_data["affected_sample_batch_ids"]
                )
            if "affected_sample_item_ids" in file_notification_data:
                affected_sample_item_ids.update(
                    file_notification_data["affected_sample_item_ids"]
                )
        except ApiException as ae:
            failed_files.append(
                {
                    "sample_file_id": sample_file.sample_file_id,
                    "filename": sample_file.filename,
                    "message": f"Processing failed: {ae.user_message}",
                }
            )
        except Exception as e:
            failed_files.append(
                {
                    "sample_file_id": sample_file.sample_file_id,
                    "filename": sample_file.filename,
                    "message": f"Processing failed: {str(e)}",
                }
            )

    # --- Prepare response --- #
    total_files = len(sample_file_ids)
    processed_count = len(processed_files)
    failed_count = len(failed_files)
    notification_data = {
        "total_files": total_files,
        "processed_files": processed_files,
        "failed_files": failed_files,
        "summary": {
            "processed": processed_count,
            "failed": failed_count,
            "total": total_files,
        },
        "affected_sample_batch_ids": list(affected_sample_batch_ids),
    }
    # Determine status and message
    if failed_count == 0:
        message = f"Successfully re-processed {processed_count} sample files."
        return {
            "message": message,
            "_notification_data": notification_data,
        }
    elif processed_count == 0:
        message = f"Failed to re-process all {total_files} sample files.\n" + "\n".join(
            [f"{failed['filename']}: {failed['message']}" for failed in failed_files]
        )
        raise ApiException(
            user_message=message, tech_message=notification_data, status_code=422
        )
    else:
        message = (
            f"Re-processed {processed_count} files successfully, "
            f"{failed_count} files failed.\n"
            + "\n".join(
                [
                    f"{failed['filename']}: {failed['message']}"
                    for failed in failed_files
                ]
            )
        )
        raise_api_warning(message, notification_data, status_code=207)


async def _clear_sample_items_for_reprocessing(
    sample_file_id: str,
    independent_transaction: bool,
) -> set[str]:
    """Delete one file's sample items immediately before it is re-processed.

    Called per file from inside :func:`re_process_sample_files`' processing
    loop rather than for the whole batch up front, so the deletion is never
    committed further ahead than the rebuild that follows it.

    Unlike :func:`_delete_partial_acquisition_items` this removes every sample
    item on the file, not only the ACQUISITION ones - re-processing has already
    refused any file carrying user-created samples.

    :param sample_file_id: File whose sample items are being rebuilt.
    :param independent_transaction: Whether to emit deletion events itself.
    :return: Batch IDs the removed items belonged to, for UI reloads.
    """
    async with async_session() as session:
        # Read the fields out inside the session: the rows are gone by the time
        # the notifications below are emitted.
        removed = [
            (item.sample_item_id, item.sample_batch_id)
            for item in (
                (
                    await session.execute(
                        select(SampleItem).where(
                            SampleItem.sample_file_id == sample_file_id
                        )
                    )
                )
                .scalars()
                .all()
            )
        ]
        await session.execute(
            delete(SampleItem).where(SampleItem.sample_file_id == sample_file_id)
        )
        await session.commit()

    affected_sample_batch_ids = {batch_id for _, batch_id in removed}
    if independent_transaction:
        for sample_item_id, sample_batch_id in removed:
            await emit_record_deleted(
                record_type="sample",
                record_id=sample_item_id,
                room=sample_batch_id,
            )
    return affected_sample_batch_ids


async def create_acquisition_batches_and_items(
    sample_file: SampleFile, dataset_id: str
) -> tuple[list[dict], list[dict]]:
    """
    Create ACQUISITION batches and sample items for each ionization mode of sample file.

    For each ionization mode in the sample file:
    - Get or create daily ACQUISITION batch in provided acquisition dataset
    - Create ACQUISITION sample item within the batch
    - Configure batch with appropriate target collections and ionization mechanisms

    :param sample_file: Sample file record containing polarities and metadata
    :type sample_file: SampleFile
    :param dataset_id: ID of ACQUISITION dataset to create batches in
    :type dataset_id: str
    :return: Tuple of (created sample items, created/retrieved batches)
    :rtype: tuple[list[dict], list[dict]]
    """
    sample_items_to_create = []
    acquisition_sample_batches = []

    ionization_modes = await resolve_ionization_modes_by_tokens(sample_file)

    for ionization_mode in ionization_modes:
        # --- Generate daily ACQUISITION batch name for this ionization mode ---
        ion_mode_name = ionization_mode.ionization_mode_name
        batch_name = (
            f"{sample_file.datetime.strftime('%Y-%m-%d')} {ion_mode_name} acquisition"
        )

        # --- Get or create daily ACQUISITION batch for this ionization mode ---
        # Get DIAGNOSTICS and CALIBRATION target collections for ACQUISITION
        # batches. Resolved before the call because it only reads attributes of
        # the already-loaded ionization mode - no query - and the get-or-create
        # needs them ready for the branch that inserts.
        target_collection_ids = []
        if ionization_mode.diagnostic_collection_id:
            target_collection_ids.append(ionization_mode.diagnostic_collection_id)
        if ionization_mode.calibration_collection_id:
            target_collection_ids.append(ionization_mode.calibration_collection_id)

        # Mutual exclusion lives in the database: the batch's natural key is
        # constrained by uq_sample_batch_acquisition_natural_key, and
        # get_or_create_acquisition_batch adopts the winner's row when the
        # insert collides. Nothing in this process can do that job - production
        # runs several uvicorn workers and each converted file is its own
        # load-balanced request, so the files of one watcher scan land on
        # different workers.
        batch_result = await get_or_create_acquisition_batch(
            sample_batch=SampleBatchCreate(
                dataset_id=dataset_id,
                sample_batch_name=batch_name,
                sample_batch_description=(
                    "Auto-generated daily acquisition batch "
                    f"for {sample_file.instrument}"
                ),
                sample_batch_type="ACQUISITION",
                polarity=ionization_mode.ionization_mode_polarity,
                target_collection_ids=target_collection_ids,
            )
        )
        acquisition_sample_batch = batch_result.get("data")

        if batch_result.get("created"):
            if not target_collection_ids:
                runtime.logger.info(
                    "No "
                    f"{', '.join(sample_batch_config.ACQUISITION_COLLECTION_TYPES)}"
                    " target collections found for ACQUISITION batch"
                )
            runtime.logger.debug(
                f"Created new ACQUISITION batch: {batch_name} ({ion_mode_name})"
            )
        else:
            runtime.logger.debug(
                f"Using existing ACQUISITION batch: {batch_name} ({ion_mode_name})"
            )

        acquisition_sample_batches.append(acquisition_sample_batch)

        # Prepare ACQUISITION sample item for this ionization mode
        sample_items_to_create.append(
            SampleItemCreate(
                sample_batch_id=acquisition_sample_batch["sample_batch_id"],
                sample_file_id=sample_file.sample_file_id,
                sample_item_name=sample_file.datetime.strftime("%Y-%m-%d %H:%M:%S"),
                sample_item_type="ACQUISITION",
                sample_item_attributes={},
                polarity=ionization_mode.ionization_mode_polarity,
                ionization_mode_id=ionization_mode.ionization_mode_id,
            )
        )
    # Step 3: Create ACQUISITION sample items
    acquisition_samples = (
        await create_sample_items(
            sample_items=sample_items_to_create, independent_transaction=True
        )
    ).get("data", [])

    return acquisition_samples, acquisition_sample_batches


async def _record_calibration_failure(
    sample_file_id: str | None,
    error: Exception,
    attempts: int,
    mz_error_tolerance: float | None,
) -> None:
    """
    Persist a failed-calibration marker on the sample file.

    A given-up calibration otherwise leaves ``mz_calibration`` NULL, which is
    indistinguishable from "calibration not applicable" (blank files, modes
    without a calibration collection): the sample silently matches on the
    uncalibrated axis and nothing in the UI points at it. The marker record
    (``status: "failed"``, ``verified: False``) makes the outcome visible to
    the sample browser and trips the verified gate in the match computation.

    Never overwrites an existing record: an applied fit must survive a later
    failed re-attempt. Best-effort - a database error here is logged, not
    raised, so it cannot fail the surrounding pipeline.
    """
    if sample_file_id is None:
        return
    try:
        async with async_session() as session:
            sample_file = await session.get(SampleFile, sample_file_id)
            if sample_file is None or sample_file.mz_calibration is not None:
                return
            sample_file.mz_calibration = {
                "status": "failed",
                "verified": False,
                "error": str(error),
                "attempts": attempts,
                "mz_error_tolerance": mz_error_tolerance,
            }
            await session.commit()
    except SQLAlchemyError:
        runtime.logger.exception(
            f"Failed to record calibration failure for sample file {sample_file_id}"
        )


async def calibrate_with_retry(
    sample: dict,
    sample_file_id: str | None = None,
    user_id: int | None = None,
    process_id: str | None = None,
) -> bool:
    """Calibrate sample with retry logic

    If no matching calibration peaks are found, the m/z error tolerance is doubled
    and the calibration is retried, up to CALIBRATION_ITERATIONS times. Only the
    failures a wider tolerance can clear are retried (see
    RETRYABLE_CALIBRATION_STATUS); any other failure stops the loop at once.

    When every attempt fails, the outcome is persisted on the sample file via
    :func:`_record_calibration_failure` and ``False`` is returned so the caller
    can skip steps that assume a calibrated m/z axis (matching, assignment).

    :param sample: Sample dict to calibrate
    :type sample: dict
    :param sample_file_id: Sample file to mark when calibration fails
    :type sample_file_id: str | None, optional
    :param user_id: Current user triggered operation (for user notifications)
    :type user_id: int | None, optional
    :param process_id: Process ID for tracking
    :type process_id: str | None, optional
    :return: True when a fit was applied, False when calibration was given up.
    :rtype: bool
    """
    mz_calibration_params = calibration_params_factory(sample["filename"])
    for i in range(1, CALIBRATION_ITERATIONS + 1):
        try:
            await calibration_mz_calibrate_sample(
                sample_item_id=sample["sample_item_id"],
                mz_calibration_params=mz_calibration_params,
                independent_transaction=False,
                user_id=user_id,
                process_id=gen_id(8),
                parent_id=process_id,
            )
            return True
        except ApiException as e:
            if e.status_code not in RETRYABLE_CALIBRATION_STATUS:
                # A fault rather than a data condition: a wider tolerance
                # cannot clear it, so stop here instead of burning the
                # remaining attempts on it.
                runtime.logger.exception(
                    "Failed to m/z calibrate sample item "
                    f"{sample['sample_item_name']}: {e}"
                )
                await _record_calibration_failure(
                    sample_file_id,
                    e,
                    attempts=i,
                    mz_error_tolerance=mz_calibration_params.mz_error_tolerance,
                )
                return False
            if i == CALIBRATION_ITERATIONS:
                # INFO: an expected data condition (a spectrum too poor to
                # yield calibration peaks), and this fires per sample of every
                # upload. For 200/207 the warning notification has already
                # reached the user; a 422 degenerate fit is recorded only here.
                runtime.logger.info(
                    "Gave up m/z calibration at m/z error tolerance "
                    f"{mz_calibration_params.mz_error_tolerance} "
                    f"for sample item {sample['sample_item_name']}: {e}"
                )
                await _record_calibration_failure(
                    sample_file_id,
                    e,
                    attempts=i,
                    mz_error_tolerance=mz_calibration_params.mz_error_tolerance,
                )
                return False
            else:
                # Double the m/z error tolerance, check refinement window limits, then retry
                old_tolerance = mz_calibration_params.mz_error_tolerance
                mz_calibration_params.mz_error_tolerance *= 2
                if (
                    mz_calibration_params.refine_window
                    <= mz_calibration_params.mz_error_tolerance
                ):
                    mz_calibration_params.refine_window = (
                        mz_calibration_params.mz_error_tolerance + 1
                    )
                # INFO: a retry that usually succeeds; the give-up above is
                # INFO too - only a non-retryable status logs at ERROR
                runtime.logger.info(
                    "Not enough calibration peaks with m/z error tolerance "
                    f"{old_tolerance}, retrying m/z calibration for sample "
                    f"{sample['sample_item_name']} with "
                    f"mz_error_tolerance={mz_calibration_params.mz_error_tolerance}."
                )
    # Unreachable: the final iteration always returns above. Kept so the
    # signature honestly never yields None.
    return False
