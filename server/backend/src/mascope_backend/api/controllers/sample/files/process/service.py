"""
Controller for sample files auto-processing pipeline.

Handles automated creation of ACQUISITION datasets, batches, and sample items, and matching the samples.
"""

import asyncio
from dataclasses import dataclass

from sqlalchemy import and_, delete, select
from sqlalchemy.exc import InterfaceError, OperationalError, SQLAlchemyError
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError

from mascope_backend.api.controllers.calibration.calibration_controller import (
    calibration_mz_calibrate_sample,
    is_unfitted_record,
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
from mascope_backend.api.controllers.sample.files.process.bindings import (
    learn_method_bindings,
)
from mascope_backend.api.controllers.sample.files.process.status import (
    claim_for_processing,
    compose_detail,
    pooled_streams_note,
    read_scan_streams,
    record_processing_status,
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
from mascope_backend.api.lib.api_features import (
    api_controller,
    api_controller_background_task,
)
from mascope_backend.api.lib.exceptions.api_exceptions import (
    ApiException,
    is_expected_client_error,
    raise_api_warning,
)
from mascope_backend.api.models.sample.batches.config import sample_batch_config
from mascope_backend.api.models.sample.batches.sample_batch_pydantic_model import (
    SampleBatchCreate,
)
from mascope_backend.api.models.sample.files.config import ProcessingStatus
from mascope_backend.api.models.sample.items.sample_item_pydantic_model import (
    SampleItemCreate,
)
from mascope_backend.api.new.ionization.modes.util import (
    NoTokenMatchError,
    one_mode_per_polarity,
    resolve_ionization_modes_by_tokens,
)
from mascope_backend.api.new.peak_assignments.service import (
    auto_assign_sample_peaks,
)
from mascope_backend.db import (
    Dataset,
    IonizationMode,
    SampleBatch,
    SampleFile,
    SampleItem,
    Workspace,
    async_session,
)
from mascope_backend.db.id import gen_id
from mascope_backend.runtime import runtime
from mascope_backend.socket.notifications import (
    UserNotification,
    emit_user_notification,
)
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


def _report_given_up(sample_file_id: str, attempts: int, error: Exception) -> None:
    """Report a pipeline that stopped for good, at a level that matches its cause.

    The file is named at INFO whatever the cause. Nothing downstream says which
    file it was: the sample_file row stays, its batch still settles ``ready``,
    and the only trace is an absence - no matched peaks, and no sample items
    either when a retry had already cleared the partial ones.

    Only an ApiException can also need an ERROR here. Anything else still
    reaches the background-task decorator, whose ``process_exception`` logs it
    at the level its class deserves - with the traceback when it is a fault -
    so an ERROR here would report the same incident twice. An ApiException
    passes the decorator unlogged, and is classified on the terms the rest of
    the API uses (:func:`is_expected_client_error`). A routine outcome - a
    raised warning, such as an m/z calibration the match gate will not accept,
    or a 4xx such as a file deleted mid-run - stays at INFO: the decorator
    still hands it to the user, and it is nothing an operator can act on.

    A fault's ERROR names neither the file nor the error. The error-monitoring
    sink groups issues by the formatted message, so text that carries either
    opens an issue per file - one for every queued file when an outage hits an
    ingest burst. The status code stays in the text, so distinct faults still
    group apart.

    :param sample_file_id: File whose pipeline gave up.
    :param attempts: Attempts spent, the last one included.
    :param error: What the last attempt raised.
    """
    runtime.logger.info(
        f"Auto-processing gave up on sample file {sample_file_id} after "
        f"{attempts} attempt(s); it will have no matched peaks: {error}"
    )
    if not isinstance(error, ApiException) or is_expected_client_error(
        error, error.status_code
    ):
        return
    runtime.logger.error(
        f"Auto-processing gave up on a sample file after {attempts} attempt(s) "
        f"(status {error.status_code}); it will have no matched peaks. The file "
        "and the cause are named at INFO in this worker's log"
    )


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


#: What a person can do for a file that needs a chemistry, besides naming
#: it after a mode's token.
CHOOSE_CHEMISTRY = "Or choose its chemistry in Raw files."


def choose_ionization_modes(
    sample_file: SampleFile, ionization_modes: list[IonizationMode]
) -> list[IonizationMode]:
    """The chosen modes that apply to a file: one for each polarity it holds.

    Modes are chosen for several files at once, so a mode of a polarity the
    file does not hold is passed over for that file rather than refused.

    :param sample_file: The file to bind.
    :param ionization_modes: The modes chosen for it.
    :raises ValueError: When a polarity of the file has no chosen mode, or
        more than one.
    :return: One mode per polarity of the file, in the file's polarity order.
    """
    chosen, problems = one_mode_per_polarity(sample_file, ionization_modes)
    if problems:
        raise ValueError(
            f"The chosen ionization modes must include one per polarity of file "
            f"{sample_file.filename}, but {'; '.join(problems)}"
        )
    return chosen


async def fetch_ionization_modes(
    ionization_mode_ids: list[str],
) -> list[IonizationMode]:
    """The ionization modes with these ids.

    :raises ValueError: When an id names no mode.
    """
    async with async_session() as session:
        modes = (
            await session.scalars(
                select(IonizationMode).where(
                    IonizationMode.ionization_mode_id.in_(ionization_mode_ids)
                )
            )
        ).all()
    missing = set(ionization_mode_ids) - {mode.ionization_mode_id for mode in modes}
    if missing:
        raise ValueError(f"No ionization mode has the id {', '.join(sorted(missing))}")
    return list(modes)


def _pipeline_item():
    """Whether a sample item is one auto-processing made for its file.

    An ACQUISITION item in an ACQUISITION batch of an ACQUISITION dataset in a
    system workspace: an instrument's year-dataset, where the pipeline files
    its samples (``get_acquisition_dataset``). No one of these says it alone.
    A person can create an ACQUISITION-typed item in a batch of their own.
    ``is_system`` also marks the "System Workspace" an older deployment's
    datasets were moved into, and a dataset a person creates in an
    instrument's workspace is in a system workspace too. Those datasets are
    ANALYSIS datasets, and an ACQUISITION batch is refused in one.
    """
    return and_(
        SampleItem.sample_item_type == "ACQUISITION",
        SampleItem.sample_batch_id.in_(
            select(SampleBatch.sample_batch_id)
            .join(Dataset, Dataset.dataset_id == SampleBatch.dataset_id)
            .join(Workspace, Workspace.workspace_id == Dataset.workspace_id)
            .where(
                SampleBatch.sample_batch_type == "ACQUISITION",
                Dataset.dataset_type == "ACQUISITION",
                Workspace.is_system.is_(True),
            )
        ),
    )


async def _acquisition_item_mode_ids(sample_file_id: str) -> list[str]:
    """The ionization modes auto-processing made a file's samples under."""
    async with async_session() as session:
        return list(
            await session.scalars(
                select(SampleItem.ionization_mode_id)
                .where(
                    SampleItem.sample_file_id == sample_file_id,
                    _pipeline_item(),
                    SampleItem.ionization_mode_id.is_not(None),
                )
                .distinct()
            )
        )


def _failure_detail(exc: BaseException) -> str:
    """What a person is told about the error that stopped a pipeline.

    The detail is served to everyone who can list the file, so it carries an
    error's own text only where the API already writes that text for its
    user: an ApiException's user message, and a ValueError, the API's
    client-class error. Any other error is a fault whose text can hold SQL
    with its bound parameters, pool internals or file paths. For those the
    detail names only the kind of failure, as ``process_exception`` does for
    the notification, and the worker's log names the file and the error.
    """
    if isinstance(exc, ApiException):
        return str(exc.user_message)
    if isinstance(exc, SQLAlchemyTimeoutError):
        return "The server was too busy to process the file. Process it again."
    if isinstance(exc, SQLAlchemyError):
        return "A database operation failed while the file was processed."
    if isinstance(exc, ValueError):
        return str(exc) or type(exc).__name__
    return "Processing stopped on an unexpected error."


#: How long a pipeline that gave up waits to record that it failed. A run that
#: gave up on pool starvation writes on the same starved pool, and must not
#: wait out the whole pool timeout for its report.
_FAILED_STATUS_TIMEOUT_S = 10


async def _record_failed(sample_file_id: str, error: Exception) -> None:
    """Record that a pipeline stopped for good on ``error``.

    Awaited inside the wrapper's ``except`` clause, where a cancellation would
    bypass that try's ``except asyncio.CancelledError``, so it is reported
    here, as the backoff wait reports its own. A write that does not finish in
    time leaves the file in its last in-progress status, which the next
    startup marks failed.

    :param sample_file_id: File whose pipeline gave up.
    :param error: What the last attempt raised.
    """
    try:
        await asyncio.wait_for(
            record_processing_status(
                sample_file_id, ProcessingStatus.FAILED, _failure_detail(error)
            ),
            timeout=_FAILED_STATUS_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        runtime.logger.info(
            f"Could not record within {_FAILED_STATUS_TIMEOUT_S}s that "
            f"auto-processing failed for sample file {sample_file_id}; it keeps "
            "its last status until a restart marks it failed"
        )
    except asyncio.CancelledError:
        _report_cancelled(sample_file_id, "while recording that it failed")
        raise


def _bound_detail(ionization_modes: list[IonizationMode], by_token: bool) -> str:
    """Name the modes a file was bound to, and what bound it."""
    names = " and ".join(
        f"'{mode.ionization_mode_name}' ({mode.ionization_mode_polarity})"
        for mode in ionization_modes
    )
    if not by_token:
        return f"Bound to {names} without a file-name token."
    tokens = "token" if len(ionization_modes) == 1 else "tokens"
    return f"Bound by file-name {tokens} to {names}."


async def _park_needing_chemistry(
    sample_file: SampleFile, reason: str, streams_note: str | None
) -> dict:
    """Leave a file that binds to no ionization mode waiting for a chemistry.

    Nothing has failed: the file is converted and stored, and only a person
    can say which chemistry it was acquired under. It gets no samples until
    then. Its status says so, which keeps it for the people answerable for
    the instrument (``api/new/notifications``), and the run reports a warning
    rather than an error.

    :param sample_file: The file.
    :param reason: What the routing found.
    :param streams_note: The file's pooled-streams note, if any.
    :return: The run's result.
    """
    reason = reason.strip().rstrip(".") + "."
    # The detail is the file's own reason; Raw files, where it is read, says
    # what to do about it.
    await record_processing_status(
        sample_file.sample_file_id,
        ProcessingStatus.NEEDS_CHEMISTRY,
        compose_detail(reason, streams_note),
    )
    # INFO: a data condition a person resolves, not a fault
    runtime.logger.info(
        f"Sample file '{sample_file.filename}' needs a chemistry: {reason}"
    )
    return {
        "status": "parked",
        "message": f"{reason} {CHOOSE_CHEMISTRY}",
        "_notification_data": {
            "affected_sample_batch_ids": [],
            "affected_sample_item_ids": [],
            "instrument": sample_file.instrument,
            "sample_file_id": sample_file.sample_file_id,
        },
    }


def _calibration_failure_detail(mz_calibration: dict | None) -> str:
    """Say what is wrong with a file's m/z calibration record.

    Reads the record calibration left on the file: a failed fit carries its
    error, a fit below the quality bar its reasons.
    """
    record = mz_calibration or {}
    if record.get("status") == "failed":
        error = str(record.get("error") or "").strip().rstrip(".")
        return (
            f"The m/z calibration failed: {error}."
            if error
            else "The m/z calibration failed."
        )
    issues = [
        str(issue.get("message", "")).strip()
        for issue in record.get("quality_issues") or []
        if isinstance(issue, dict) and issue.get("message")
    ]
    if record.get("status") == "poor" and issues:
        return f"The m/z calibration is below the quality bar: {' '.join(issues)}"
    return "The file's m/z calibration is not verified."


def _is_verified_record(mz_calibration: dict | None) -> bool:
    """Whether matching accepts a file's m/z calibration record.

    The verified gate of ``match_compute_sample``, which refuses every other
    record with a raised warning: no record means the acquisition axis, which
    is accepted, and any record must say it was verified. A TOF file's
    converter record never does (see ``is_unfitted_record``).
    """
    return mz_calibration is None or bool(mz_calibration.get("verified", False))


@dataclass(frozen=True)
class CalibrationOutcome:
    """How a sample's automatic m/z calibration ended.

    The reason travels with the outcome rather than being read back from the
    file's record, which does not always hold it: a failure never overwrites
    an applied fit or an earlier failure's record.
    """

    #: A fit was applied and verified.
    verified: bool
    #: Why the sample is not calibrated, as a sentence for the file's
    #: processing detail; None when verified.
    reason: str | None = None


def _calibration_error_reason(error: ApiException) -> str:
    """The reason a calibration attempt gave, without its final period."""
    data = (
        error.tech_message.get("data") if isinstance(error.tech_message, dict) else None
    )
    reason = (data or {}).get("warning") or (data or {}).get("error")
    return str(reason or error.user_message).strip().rstrip(".")


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

    Only the pipeline's own items are removed (:func:`_pipeline_item`) - a
    sample a person made from the file is never touched, whatever its type.
    """
    async with async_session() as session:
        result = await session.execute(
            delete(SampleItem).where(
                SampleItem.sample_file_id == sample_file_id,
                _pipeline_item(),
            )
        )
        await session.commit()
    if result.rowcount:
        runtime.logger.info(
            f"Removed {result.rowcount} partial ACQUISITION sample item(s) "
            f"for sample file {sample_file_id} before retrying"
        )


async def _reset_calibration(sample_file_id: str) -> None:
    """Restore a file's acquisition m/z axis before it is rebuilt.

    Best effort, as in re-processing: a reset that fails leaves the previous
    calibration in effect, and the file is rebuilt all the same.
    """
    try:
        await reset_mz_calibration(
            await fetch_sample_file(sample_file_id=sample_file_id)
        )
    except Exception:  # noqa: BLE001 - the rebuild matters more than the reset
        runtime.logger.info(
            f"Could not reset the m/z calibration of sample file {sample_file_id} "
            "before rebuilding it; its previous calibration remains in effect"
        )
        runtime.logger.opt(exception=True).warning(
            "Could not reset a sample file's m/z calibration before rebuilding it"
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
    instrument: str | None = None,
    ionization_mode_ids: list[str] | None = None,
    reset_calibration: bool = False,
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
      (blank files skip all of it; calibration is also skipped when no
      calibration collection is set)
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
    :param instrument: The file's instrument. The pipeline itself never reads
        it: it names the room that hears how the run ended. A finished run
        also reports its instrument in its result, but a failed run has no
        result, so this is the only way its error reaches that room.
    :type instrument: str | None, optional
    :param ionization_mode_ids: The modes to bind the file to, chosen by a
        person or kept from its samples. None binds it by its file-name
        tokens, and a file they bind to nothing waits for a chemistry.
    :type ionization_mode_ids: list[str] | None, optional
    :param reset_calibration: Restore the file's acquisition m/z axis before
        the first attempt, as re-processing does, for a file rebuilt under
        other modes.
    :type reset_calibration: bool, optional
    :return: Processing results with affected IDs
    """
    # The keys this run has already taught, shared by every attempt: the
    # backoffs are tens of seconds, so another file of the same key can easily
    # be learned in between, and a guard that only compared with whatever the
    # row last saw would let the retry count again.
    recorded_bindings: set[str] = set()
    for attempt in range(_AUTO_PROCESS_RETRIES + 1):
        try:
            async with _auto_process_gate:
                if reset_calibration and attempt == 0:
                    await _reset_calibration(sample_file_id)
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
                    ionization_mode_ids=ionization_mode_ids,
                    recorded_bindings=recorded_bindings,
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
                # Terminal. Name the file here or the shortfall is only
                # discoverable by counting rows afterwards.
                _report_given_up(sample_file_id, attempts=attempt + 1, error=e)
                await _record_failed(sample_file_id, e)
                raise
            delay = _AUTO_PROCESS_RETRY_DELAYS_S[attempt]
            # INFO: a retry that usually succeeds, and the line names the file,
            # so at WARNING it would open a monitoring issue per file and per
            # attempt. A retry that does not help ends in the give-up above.
            runtime.logger.info(
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
    instrument: str | None = None,
    ionization_mode_ids: list[str] | None = None,
    reset_calibration: bool = False,
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
        "instrument": instrument,
        "ionization_mode_ids": ionization_mode_ids,
        "reset_calibration": reset_calibration,
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
    ionization_mode_ids: list[str] | None = None,
    recorded_bindings: set[str] | None = None,
) -> dict:
    """Gated body of ``auto_process_sample_file`` - see the public wrapper.

    ``recorded_bindings`` is shared by every attempt of one run, so a file
    whose later stages fail and retry teaches its method once rather than
    once per attempt.
    """
    # Initialize collector for affected sample items
    all_affected_sample_item_ids = set()

    # --- Validate sample file existence --- #
    sample_file = await fetch_sample_file(sample_file_id=sample_file_id)
    # Describes the file rather than a stage, so every status this run
    # records carries it.
    scan_streams = await read_scan_streams(sample_file.filename)
    streams_note = pooled_streams_note(scan_streams or [])

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

    # --- Bind the file to its ionization modes --- #
    # After the dataset on purpose: a file that binds to nothing still gets
    # its instrument's workspace, which is where its modes are configured.
    by_token = ionization_mode_ids is None
    if by_token:
        try:
            bound_modes = await resolve_ionization_modes_by_tokens(sample_file)
        except ValueError as e:
            return await _park_needing_chemistry(sample_file, str(e), streams_note)
    else:
        try:
            bound_modes = choose_ionization_modes(
                sample_file, await fetch_ionization_modes(ionization_mode_ids)
            )
        except ValueError as e:
            # A chosen mode was deleted, or changed, while the file waited:
            # it needs a chemistry again, and can be given one.
            return await _park_needing_chemistry(sample_file, str(e), streams_note)

    # What this file's method has now been seen running. Recorded, not read:
    # nothing routes on a method binding yet, and this must never cost the
    # file its processing - learn_method_bindings reports its own failures.
    await learn_method_bindings(
        sample_file,
        bound_modes,
        source="token" if by_token else "explicit",
        streams=scan_streams,
        recorded=recorded_bindings,
    )

    # --- Create ACQUISITION batches and sample items for each ionization mode --- #
    (
        acquisition_samples,
        acquisition_sample_batches,
    ) = await create_acquisition_batches_and_items(
        sample_file=sample_file,
        dataset_id=acquisition_dataset.get("dataset_id"),
        ionization_modes=bound_modes,
    )
    await record_processing_status(
        sample_file_id,
        ProcessingStatus.BOUND,
        compose_detail(_bound_detail(bound_modes, by_token), streams_note),
    )

    # Extract batch and sample IDs for notifications
    affected_sample_batch_ids = [
        batch.get("sample_batch_id") for batch in acquisition_sample_batches
    ]
    all_affected_sample_item_ids.update(
        sample["sample_item_id"] for sample in acquisition_samples
    )

    # Blank files are stored without an instrument config and have no peaks:
    # they skip calibration, matching and peak assignment.
    is_blank_sample_file = sample_file.instrument_function_id is None

    # --- Calibrate every ACQUISITION sample of the file before matching any --- #
    # The m/z calibration belongs to the FILE, not the sample item: applying a
    # fit rescales the whole peak store and removes the matches of every sample
    # item on the file (calibration_mz_apply). A dual-polarity file has one
    # sample item per polarity, so matching one before calibrating the other
    # would lose its matches to the apply.
    ionization_modes = {}
    for sample in acquisition_samples:
        async with async_session() as session:
            ionization_modes[sample["sample_item_id"]] = await session.get(
                IonizationMode, sample["ionization_mode_id"]
            )

    # Each polarity drifts on its own, but a file holds one m/z calibration: a
    # second polarity's fit replaces the first's, so whichever is calibrated
    # last would set the axis for both. Until each polarity can carry its own
    # fit, a file whose samples would calibrate more than once is not
    # calibrated here. With exactly one calibrating sample, its fit rescales
    # the whole file, so the file's other samples are matched on that fit too.
    calibrating_sample_ids = {
        sample_item_id
        for sample_item_id, ionization_mode in ionization_modes.items()
        if ionization_mode
        and ionization_mode.calibration_collection_id
        and not is_blank_sample_file
    }
    shared_calibration = len(calibrating_sample_ids) > 1
    # For the file's final status: why it was not calibrated, as a clause, and
    # why samples went unmatched when calibration stopped them.
    not_calibrated_reason: str | None = None
    unmatched_reason: str | None = None
    if shared_calibration:
        not_calibrated_reason = (
            f"{len(calibrating_sample_ids)} of its samples have a calibration "
            "collection, and a file holds one m/z calibration for all of them"
        )
        # INFO: a data condition, fires for every such file
        runtime.logger.info(
            f"Skipping m/z calibration for '{sample_file.filename}': "
            f"{not_calibrated_reason}."
        )
        calibrating_sample_ids.clear()
    elif not calibrating_sample_ids and not is_blank_sample_file:
        uncalibrated_modes = sorted(
            {
                f"'{mode.ionization_mode_name}'"
                for mode in ionization_modes.values()
                if mode is not None
            }
        )
        not_calibrated_reason = (
            f"ionization mode {', '.join(uncalibrated_modes)} has no "
            "calibration collection"
            if len(uncalibrated_modes) == 1
            else f"ionization modes {', '.join(uncalibrated_modes)} have no "
            "calibration collection"
        )

    matchable_sample_ids: set[str] = set()
    for sample in acquisition_samples:
        sample_item_id = sample["sample_item_id"]
        ionization_mode = ionization_modes[sample_item_id]

        # Perform calibration only when collection is configured and file is not blank.
        if sample_item_id in calibrating_sample_ids:
            outcome = await calibrate_with_retry(
                sample=sample,
                sample_file_id=sample_file.sample_file_id,
                user_id=user_id,
                process_id=process_id,
            )
            if not outcome.verified:
                # The failed or below-bar record calibrate_with_retry leaves
                # would trip the verified gate in match_compute_sample as a
                # raised warning, failing the whole pipeline; skip matching
                # and assignment explicitly - both assume a calibrated m/z
                # axis.
                runtime.logger.info(
                    "Skipping matching and peak assignment for sample "
                    f"'{sample['sample_item_name']}': m/z calibration not verified."
                )
                unmatched_reason = outcome.reason
                continue
            await record_processing_status(
                sample_file_id,
                ProcessingStatus.CALIBRATED,
                compose_detail(streams_note),
            )
        elif is_blank_sample_file:
            # A blank has no peaks, so there is nothing to match or assign
            # either. Held back explicitly, as batch matching does:
            # match_compute_sample refuses a blank with a raised warning, which
            # would end the whole run and report a routine file to the
            # instrument room as a warning.
            runtime.logger.info(
                "Skipping m/z calibration, matching and peak assignment for "
                f"blank file '{sample['sample_item_name']}': it has no peaks."
            )
            continue
        elif not shared_calibration:
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
        matchable_sample_ids.add(sample_item_id)

    # --- Hold back what the match gate would refuse --- #
    # match_compute_sample refuses a sample whose file record is not verified,
    # with a raised warning that ends the whole run - so judge the record here,
    # as calibration left it. A calibration that was not verified leaves the
    # record unverified unless an earlier fit survives it; a skipped
    # calibration leaves whatever an earlier run stored; and a TOF file that
    # was never fitted keeps its converter's record, which matching refuses
    # though the pipeline had no calibrants to fit it with.
    mz_calibration = sample_file.mz_calibration
    if calibrating_sample_ids and matchable_sample_ids:
        mz_calibration = (
            await fetch_sample_file(sample_file_id=sample_file_id)
        ).mz_calibration
    calibration_note: str | None = None
    if matchable_sample_ids and not _is_verified_record(mz_calibration):
        runtime.logger.info(
            "Skipping matching and peak assignment for "
            f"{len(matchable_sample_ids)} sample(s) of '{sample_file.filename}': "
            "the file's m/z calibration is not verified."
        )
        matchable_sample_ids.clear()
        if unmatched_reason is None:
            unmatched_reason = (
                f"Not m/z calibrated: {not_calibrated_reason}. Matching needs a "
                "verified m/z calibration."
                if is_unfitted_record(mz_calibration) and not_calibrated_reason
                else _calibration_failure_detail(mz_calibration)
            )
    elif is_blank_sample_file:
        calibration_note = "Blank measurement: no peaks to calibrate, match or assign."
    elif not_calibrated_reason:
        axis = (
            "the acquisition axis"
            if mz_calibration is None
            else "the calibration already on the file"
        )
        calibration_note = f"Not m/z calibrated: {not_calibrated_reason}."
        if shared_calibration:
            calibration_note += f" Matched on {axis}."
    elif mz_calibration is not None and mz_calibration.get("status") == "poor":
        # Verified under a gate that only warns, and matched on.
        calibration_note = _calibration_failure_detail(mz_calibration)

    # --- Match and assign the samples --- #
    for sample in acquisition_samples:
        sample_item_id = sample["sample_item_id"]
        if sample_item_id not in matchable_sample_ids:
            continue

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

    # Recorded last, so that `done` means the run returned: a failure in
    # anything above is recorded as `failed` by the wrapper instead. `done`
    # also means every sample was matched, or a blank had nothing to match.
    matched = len(matchable_sample_ids)
    unmatched = len(acquisition_sample_item_ids) - matched
    if is_blank_sample_file:
        await record_processing_status(
            sample_file_id,
            ProcessingStatus.DONE,
            compose_detail(calibration_note, streams_note),
        )
    elif not unmatched:
        await record_processing_status(
            sample_file_id,
            ProcessingStatus.DONE,
            compose_detail(
                f"Matched {matched} sample{'s' if matched != 1 else ''}.",
                calibration_note,
                streams_note,
            ),
        )
    else:
        skipped = (
            f"Matching and peak assignment were skipped for {unmatched} of its "
            f"{len(acquisition_sample_item_ids)} samples."
            if matched
            else "Matching and peak assignment were skipped."
        )
        await record_processing_status(
            sample_file_id,
            ProcessingStatus.CALIBRATION_FAILED,
            compose_detail(unmatched_reason, skipped, streams_note),
        )

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


@api_controller()
async def bind_sample_files(
    sample_file_ids: list[str],
    ionization_mode_ids: list[str],
    user_id: int | None = None,
) -> dict:
    """
    Process files under the ionization modes chosen for them.

    For files whose names bind them to no mode: the ones a ``needs_chemistry``
    status names, one that failed before its samples were made, or one bound
    wrongly by hand. Each file is bound to the chosen mode of each polarity it
    holds and processed as a token would have had it processed - its samples
    created, calibrated and matched - in a detached pipeline per file, behind
    the same ingest gate as every upload. A file that has samples already is
    rebuilt under the chosen modes, as re-processing rebuilds one: its m/z
    calibration is reset, and the pipeline replaces its samples.
    Re-processing keeps the modes a file bound here has, since no token binds
    it again.

    A sample a person made from the file is never touched: the pipeline
    replaces only its own. Such a file - one processed by hand into someone's
    batch, say - keeps its m/z calibration rather than having it reset under
    that sample. If a chosen mode calibrates the file, the new fit marks that
    batch for re-matching, as any new calibration of the file does.

    Each file is claimed - marked ``queued`` - before its pipeline starts, so
    a second choice for the same file is refused rather than starting a second
    run. Refused, each with its reason while the others go ahead: a file
    being processed already, and a file the chosen modes do not fit.

    :param sample_file_ids: The files to bind.
    :type sample_file_ids: list[str]
    :param ionization_mode_ids: The modes chosen, at most one per polarity.
    :type ionization_mode_ids: list[str]
    :param user_id: The person who chose, told how each run ends.
    :type user_id: int | None, optional
    :raises ApiException: 422 when no file could be bound, and a 207 warning
        naming the refused files when only some could be.
    :return: The files started.
    :rtype: dict
    """
    try:
        ionization_modes = await fetch_ionization_modes(ionization_mode_ids)
    except ValueError as e:
        raise ApiException(user_message=str(e), tech_message={}, status_code=422) from e

    async with async_session() as session:
        sample_files = {
            sample_file.sample_file_id: sample_file
            for sample_file in await session.scalars(
                select(SampleFile).where(SampleFile.sample_file_id.in_(sample_file_ids))
            )
        }
        with_user_samples = set(
            await session.scalars(
                select(SampleItem.sample_file_id)
                .where(
                    SampleItem.sample_file_id.in_(sample_file_ids),
                    ~_pipeline_item(),
                )
                .distinct()
            )
        )

    candidates: list[tuple[SampleFile, list[str]]] = []
    refused: list[dict] = []

    def refuse(sample_file_id: str, filename: str | None, message: str) -> None:
        refused.append(
            {"sample_file_id": sample_file_id, "filename": filename, "message": message}
        )

    for sample_file_id in sample_file_ids:
        sample_file = sample_files.get(sample_file_id)
        if sample_file is None:
            refuse(
                sample_file_id,
                None,
                f"Sample file with ID '{sample_file_id}' not found",
            )
            continue
        try:
            chosen = choose_ionization_modes(sample_file, ionization_modes)
        except ValueError as e:
            refuse(sample_file_id, sample_file.filename, str(e))
            continue
        candidates.append((sample_file, [mode.ionization_mode_id for mode in chosen]))

    claimed = set(
        await claim_for_processing(
            [sample_file.sample_file_id for sample_file, _ in candidates],
            "Queued for processing under the chosen chemistry.",
        )
    )
    started = []
    for sample_file, mode_ids in candidates:
        if sample_file.sample_file_id in claimed:
            started.append((sample_file, mode_ids))
        else:
            refuse(
                sample_file.sample_file_id,
                sample_file.filename,
                f"{sample_file.filename} is being processed already",
            )

    reasons = "\n".join(f"{refusal['message']}." for refusal in refused)
    if not started:
        raise ApiException(
            user_message=f"No file could be bound:\n{reasons}",
            tech_message={"refused": refused},
            status_code=422,
        )

    for sample_file, mode_ids in started:
        await spawn_auto_process_sample_file(
            sample_file_id=sample_file.sample_file_id,
            independent_transaction=True,
            user_id=user_id,
            instrument=sample_file.instrument,
            ionization_mode_ids=mode_ids,
            reset_calibration=sample_file.sample_file_id not in with_user_samples,
        )

    files = f"{len(started)} file{'' if len(started) == 1 else 's'}"
    message = f"Processing {files} under the chosen chemistry."
    data = {
        "started": [sample_file.sample_file_id for sample_file, _ in started],
        "refused": refused,
    }
    if refused:
        # Partly done, as re-processing and deleting several files report it.
        raise_api_warning(
            f"{message} {len(refused)} could not be bound:\n{reasons}",
            data,
            status_code=207,
        )
    return {"message": message, "data": data}


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
                ~_pipeline_item(),
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
    # Files no token binds, re-processed under the modes their samples have.
    kept_mode_ids: dict[str, list[str]] = {}

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
        no_token: NoTokenMatchError | None = None
        try:
            await resolve_ionization_modes_by_tokens(sample_file)
        except NoTokenMatchError as e:
            no_token = e
        except ValueError as ve:
            # Tokens that match, but not one mode per polarity: a
            # configuration to fix, which no earlier binding stands in for.
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

        if no_token is not None:
            # A file bound without a token - its chemistry chosen by hand -
            # has no token to bind it again, so it keeps the modes its
            # samples were created under. Read before they are cleared, and
            # outside the except clause, where an error would escape the
            # handlers that keep one file's failure its own.
            try:
                kept = await _kept_mode_ids(sample_file)
            except Exception as e:  # noqa: BLE001 - one file's failure
                runtime.logger.info(
                    f"Could not read the modes of sample file {sample_file.filename}'s "
                    f"samples: {e}"
                )
                kept = None
            if kept is None:
                failed_files.append(
                    {
                        "sample_file_id": sample_file.sample_file_id,
                        "filename": sample_file.filename,
                        "message": str(no_token),
                    }
                )
                continue
            kept_mode_ids[sample_file.sample_file_id] = kept

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
            # Before anything of the file is destroyed: until the rebuild
            # records its own stages, the row would still say how the last run
            # ended - `done` on a file with no samples, if a restart cut in -
            # and an in-progress status is what a restart marks failed. A file
            # another run has claimed meanwhile is left to it.
            if not await claim_for_processing(
                [sample_file.sample_file_id], "Queued for re-processing."
            ):
                failed_files.append(
                    {
                        "sample_file_id": sample_file.sample_file_id,
                        "filename": sample_file.filename,
                        "message": f"{sample_file.filename} is being processed already",
                    }
                )
                continue
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

            try:
                cleared_batch_ids = await _clear_sample_items_for_reprocessing(
                    sample_file_id=sample_file.sample_file_id,
                    independent_transaction=independent_transaction,
                )
            except Exception as e:
                # No pipeline runs for the file after this, and the pipeline
                # is what records how a run ended: without this the file
                # would read queued, with no run behind it, until a restart.
                await _record_failed(sample_file.sample_file_id, e)
                raise
            affected_sample_batch_ids.update(cleared_batch_ids)

            result = await auto_process_sample_file(
                sample_file_id=sample_file.sample_file_id,
                independent_transaction=False,
                user_id=user_id,
                process_id=gen_id(8),
                parent_id=process_id,
                ionization_mode_ids=kept_mode_ids.get(sample_file.sample_file_id),
            )
            if result.get("status") == "parked":
                # Its token was removed while the batch waited: it needs a
                # chemistry now, and was not re-processed.
                failed_files.append(
                    {
                        "sample_file_id": sample_file.sample_file_id,
                        "filename": sample_file.filename,
                        "message": result["message"],
                    }
                )
                continue

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


async def modes_to_rebind(sample_file_id: str) -> list[str] | None:
    """The modes to process a file under again, when its tokens do not bind it.

    A file whose chemistry was chosen by hand has no token to bind it again,
    so it keeps the modes its samples were made under, as re-processing
    keeps them. None leaves the binding to the tokens: they bind the file, or
    the run finds that they do not and parks it.

    :param sample_file_id: The file.
    :return: The modes its samples have, or None.
    """
    sample_file = await fetch_sample_file(sample_file_id=sample_file_id)
    try:
        await resolve_ionization_modes_by_tokens(sample_file)
    except NoTokenMatchError:
        return await _kept_mode_ids(sample_file)
    except ValueError:
        return None
    return None


async def _kept_mode_ids(sample_file: SampleFile) -> list[str] | None:
    """The modes a file's samples have, when they still bind it.

    :return: One mode id per polarity of the file, or None when its samples
        do not give one - it has none, or a mode was deleted since.
    """
    mode_ids = await _acquisition_item_mode_ids(sample_file.sample_file_id)
    if not mode_ids:
        return None
    try:
        modes = choose_ionization_modes(
            sample_file, await fetch_ionization_modes(mode_ids)
        )
    except ValueError:
        return None
    return [mode.ionization_mode_id for mode in modes]


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
    sample_file: SampleFile,
    dataset_id: str,
    ionization_modes: list[IonizationMode],
) -> tuple[list[dict], list[dict]]:
    """
    Create ACQUISITION batches and sample items for each ionization mode of sample file.

    For each ionization mode the file was bound to:
    - Get or create daily ACQUISITION batch in provided acquisition dataset
    - Create ACQUISITION sample item within the batch
    - Configure batch with appropriate target collections and ionization mechanisms

    :param sample_file: Sample file record containing polarities and metadata
    :type sample_file: SampleFile
    :param dataset_id: ID of ACQUISITION dataset to create batches in
    :type dataset_id: str
    :param ionization_modes: The modes the file is bound to, one per polarity
    :type ionization_modes: list[IonizationMode]
    :return: Tuple of (created sample items, created/retrieved batches)
    :rtype: tuple[list[dict], list[dict]]
    """
    sample_items_to_create = []
    acquisition_sample_batches = []

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

    Never overwrites an applied fit or an earlier marker: an applied fit must
    survive a later failed re-attempt. The one record it does replace is a
    TOF file's converter record (``is_unfitted_record``), which every TOF
    file has from registration: skipping it left a failed TOF fit without a
    trace. The converter's coefficients are kept on the marker. Best-effort -
    a database error here is logged, not raised, so it cannot fail the
    surrounding pipeline.
    """
    if sample_file_id is None:
        return
    try:
        async with async_session() as session:
            sample_file = await session.get(SampleFile, sample_file_id)
            if sample_file is None:
                return
            existing = sample_file.mz_calibration
            if existing is not None and not is_unfitted_record(existing):
                return
            converter = (
                {key: existing[key] for key in ("mode", "par") if key in existing}
                if existing
                else {}
            )
            sample_file.mz_calibration = {
                **converter,
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


async def _report_calibration_given_up(
    sample: dict,
    error: ApiException,
    attempts: int,
    mz_error_tolerance: float | None,
    user_id: int | None,
    status: str,
) -> None:
    """
    Tell the user once, at the end, that the sample was not calibrated.

    The nested controllers no longer report each attempt (see
    :func:`api_controller_background_task`): they repeated the same sentence
    once per nesting level per attempt, and none of them said how many
    attempts had been spent or that matching and assignment would be skipped.
    This is the only notification that outlives the loop, so it carries both.

    Deliberately parent-less: the frontend displays only parent-less
    notifications, and this is the one report the user gets - the pipeline's
    own notification still reports plain success for a file whose samples all
    failed to calibrate.
    """
    if user_id is None:
        # Nobody to notify - a pipeline started without a user (tests,
        # background reprocessing). The failure is still logged and persisted.
        return
    reason = _calibration_error_reason(error)
    attempted = f" after {attempts} attempts" if attempts > 1 else ""
    tolerance = (
        f" (m/z error tolerance widened to {mz_error_tolerance:g} ppm)"
        if attempts > 1 and mz_error_tolerance is not None
        else ""
    )
    await emit_user_notification(
        UserNotification(
            process_id=gen_id(8),
            type="mz_calibration",
            status=status,
            message=(
                f"Could not m/z calibrate sample "
                f"'{sample['sample_item_name']}'{attempted}{tolerance}: "
                f"{reason}. Matching and peak assignment were skipped for it."
            ),
            data={
                "sample_item_id": sample["sample_item_id"],
                "filename": sample["filename"],
            },
        ),
        user_id=user_id,
    )


async def _report_calibration_below_bar(
    sample: dict, issues: list[dict], user_id: int | None
) -> None:
    """
    Tell the user that the sample was calibrated, but not well enough to use.

    The counterpart of :func:`_report_calibration_given_up` for a fit that
    was applied and stored unverified (see ``stamp_quality_verdict``).
    Parent-less for the same reason.
    """
    reasons = " ".join(issue["message"] for issue in issues)
    runtime.logger.info(
        f"m/z calibration of sample '{sample['sample_item_name']}' is below the "
        f"quality bar; skipping matching and peak assignment. {reasons}"
    )
    if user_id is None:
        return
    await emit_user_notification(
        UserNotification(
            process_id=gen_id(8),
            type="mz_calibration",
            status="warning",
            message=(
                f"m/z calibration of sample '{sample['sample_item_name']}' does "
                f"not meet the quality bar: {reasons} Matching and peak "
                "assignment were skipped for it. Recalibrate it, or accept the "
                "fit from the calibration dialog."
            ),
            data={
                "sample_item_id": sample["sample_item_id"],
                "filename": sample["filename"],
            },
        ),
        user_id=user_id,
    )


async def calibrate_with_retry(
    sample: dict,
    sample_file_id: str | None = None,
    user_id: int | None = None,
    process_id: str | None = None,
) -> CalibrationOutcome:
    """Calibrate sample with retry logic

    If no matching calibration peaks are found, the m/z error tolerance is doubled
    and the calibration is retried, up to CALIBRATION_ITERATIONS times. Only the
    failures a wider tolerance can clear are retried (see
    RETRYABLE_CALIBRATION_STATUS); any other failure stops the loop at once.

    When every attempt fails, the outcome is persisted on the sample file via
    :func:`_record_calibration_failure`, reported to the user once via
    :func:`_report_calibration_given_up`, and an unverified outcome is
    returned so the caller can skip steps that assume a calibrated m/z axis
    (matching, assignment). A fit that is applied but misses the quality bar
    returns an unverified outcome too, after its own report, without
    retrying. Either carries the reason, for the file's processing detail.

    :param sample: Sample dict to calibrate
    :type sample: dict
    :param sample_file_id: Sample file to mark when calibration fails
    :type sample_file_id: str | None, optional
    :param user_id: Current user triggered operation (for user notifications)
    :type user_id: int | None, optional
    :param process_id: Process ID for tracking
    :type process_id: str | None, optional
    :return: Whether a verified fit was applied, and why not when it was not.
    :rtype: CalibrationOutcome
    """
    mz_calibration_params = calibration_params_factory(sample["filename"])
    for i in range(1, CALIBRATION_ITERATIONS + 1):
        try:
            # No ``manual``: this is the automatic pipeline, which has no
            # opinion on whether the file's previous calibration was right, so
            # an acquisition-drift marker already on the record is carried
            # forward (see ``carry_acquisition_drift``).
            result = await calibration_mz_calibrate_sample(
                sample_item_id=sample["sample_item_id"],
                mz_calibration_params=mz_calibration_params,
                independent_transaction=False,
                user_id=user_id,
                process_id=gen_id(8),
                parent_id=process_id,
            )
            data = (result or {}).get("data") or {}
            if data.get("verified", True):
                return CalibrationOutcome(verified=True)
            # Applied, but below the quality bar: the record says so and the
            # verified gate keeps the sample out of matching. A wider
            # tolerance only admits worse calibrants, so no retry.
            issues = data.get("quality_issues") or []
            await _report_calibration_below_bar(sample, issues, user_id=user_id)
            return CalibrationOutcome(
                verified=False,
                reason=_calibration_failure_detail(
                    {"status": "poor", "quality_issues": issues}
                ),
            )
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
                await _report_calibration_given_up(
                    sample,
                    e,
                    attempts=i,
                    mz_error_tolerance=mz_calibration_params.mz_error_tolerance,
                    user_id=user_id,
                    status="error",
                )
                return CalibrationOutcome(
                    verified=False,
                    reason=f"The m/z calibration failed: {_calibration_error_reason(e)}.",
                )
            if i == CALIBRATION_ITERATIONS:
                # INFO: an expected data condition (a spectrum too poor to
                # yield calibration peaks), and this fires per sample of every
                # upload. The nested attempts report nothing themselves, so the
                # summary below is the user's only notice that this sample was
                # left uncalibrated.
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
                await _report_calibration_given_up(
                    sample,
                    e,
                    attempts=i,
                    mz_error_tolerance=mz_calibration_params.mz_error_tolerance,
                    user_id=user_id,
                    status="warning",
                )
                return CalibrationOutcome(
                    verified=False,
                    reason=f"The m/z calibration failed: {_calibration_error_reason(e)}.",
                )
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
    return CalibrationOutcome(verified=False, reason="The m/z calibration failed.")
