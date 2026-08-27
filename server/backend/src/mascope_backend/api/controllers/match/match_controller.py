"""
Match Controller

This module contains all the functionalities and endpoints related to
the matching/rematching processes and related operations.
"""

from sqlalchemy import delete, func, select

from mascope_backend.api.controllers.match.aggregate.match_aggregate_controller import (
    aggregate_and_create_matches,
)
from mascope_backend.api.controllers.match.lib.match_compute import (
    compute_and_create_sample_match_isotope_data,
)
from mascope_backend.api.controllers.match.lib.match_remove import remove_matches
from mascope_backend.api.controllers.sample.batches.status.service import (
    update_sample_batch_status,
)
from mascope_backend.api.controllers.sample.lib.fetch_affected_sample_data import (
    fetch_affected_sample_data,
)
from mascope_backend.api.controllers.sample.lib.sample_batches_fetch import (
    fetch_sample_batch,
)
from mascope_backend.api.controllers.sample.lib.sample_modified_timestamps_manager import (
    update_sample_modified_timestamps,
)
from mascope_backend.api.controllers.samples.lib.samples_fetch import fetch_sample
from mascope_backend.api.controllers.target.lib.fetch.target_isotopes_fetch import (
    fetch_existing_main_isotope_references,
    fetch_sample_unmatched_target_isotopes,
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
from mascope_backend.api.new.match.params import default_match_params
from mascope_backend.db import (
    MatchCollection,
    MatchCompound,
    MatchIon,
    MatchIsotope,
    MatchSample,
    Sample,
    SampleBatch,
    async_session,
)
from mascope_backend.db.id import gen_id
from mascope_backend.runtime import runtime
from mascope_backend.socket.notifications import (
    UserNotification,
    send_progress_user_notification,
)


def _is_blank_sample(sample: Sample) -> bool:
    """Return whether the sample is blank (has no peaks)."""
    # Blank samples are created without an instrument config and should not be
    # blocked by m/z calibration verification.
    return sample.instrument_function_id is None


async def _incomplete_aggregate_sample_ids(sample_batch_id: str) -> list[str]:
    """
    Samples of the batch with stored match isotopes but no MatchSample row -
    the signature of a missing or previously-failed aggregation (MatchSample
    rows are cleared when an aggregation starts and restored per chunk, so an
    interrupted aggregation leaves its unfinished samples in this state).

    :param sample_batch_id: The batch to probe.
    :type sample_batch_id: str
    :return: The affected sample item ids (empty when aggregates are complete).
    :rtype: list[str]
    """
    async with async_session() as session:
        stmt = (
            select(MatchIsotope.sample_item_id)
            .join(Sample, Sample.sample_item_id == MatchIsotope.sample_item_id)
            .where(
                Sample.sample_batch_id == sample_batch_id,
                ~select(MatchSample.match_sample_id)
                .where(MatchSample.sample_item_id == MatchIsotope.sample_item_id)
                .exists(),
            )
            .distinct()
        )
        return list((await session.execute(stmt)).scalars())


async def _batch_aggregates_incomplete(sample_batch_id: str) -> bool:
    """Returns whether any sample of the batch has incomplete aggregates."""
    return bool(await _incomplete_aggregate_sample_ids(sample_batch_id))


# -------------------------------------------------------------------
# Sample level
# -------------------------------------------------------------------


@api_controller_background_task(
    success_notification_rooms=["user_id"],
    success_reload=[("match", "sample_batch_id")],
    error_notification_rooms=["user_id"],
    error_reload=[("match", "sample_batch_id")],
)
async def rematch_sample(
    sample_item_id: str,
    full_remove: bool = False,
    independent_transaction: bool = False,
    user_id: int | None = None,
    process_id: str | None = None,
    parent_id: str | None = None,
) -> dict:
    """
    Performs a complete rematch of a sample by removing orphaned/all matches and recomputing.

    :param sample_item_id: ID of the sample item for rematching
    :type sample_item_id: str
    :param full_remove: If True, removes all existing matches before recomputing, defaults to False
    :type full_remove: bool
    :param independent_transaction: Flag for transaction handling
    :param user_id: Current user triggered operation (for user notifications)
    :type user_id: int | None, optional
    :param process_id: Process identifier
    :param parent_id: Parent process identifier
    """
    sample = await fetch_sample(sample_item_id)
    operation_type = "Full" if full_remove else "Partial"
    runtime.logger.info(
        f"{operation_type} rematching sample '{sample.sample_item_name}'"
    )

    # Step 1: Remove matches (partial or full)
    remove_result = await match_remove_sample(
        sample_item_id=sample_item_id,
        full_remove=full_remove,
        independent_transaction=False,
        user_id=user_id,
        process_id=gen_id(8),
        parent_id=process_id,
    )

    # Step 2: Compute new matches
    compute_result = await match_compute_sample(
        sample_item_id=sample_item_id,
        independent_transaction=False,
        user_id=user_id,
        process_id=gen_id(8),
        parent_id=process_id,
    )

    # Step 3: Determine final status and message
    remove_status = remove_result["status"]
    compute_status = compute_result["status"]
    removed_count = remove_result.get("data", {}).get("removed_match_isotopes_count", 0)
    removed = f"all {removed_count}" if full_remove else f"{removed_count} orphaned"
    remove_summary = f"{removed if removed_count else 'no'} match isotopes removed"

    match (remove_status, compute_status):
        case ("skipped", "skipped"):
            # No orphaned matches to remove, no new matches to compute
            rematch_status = "skipped"
            message = (
                f"Sample '{sample.sample_item_name}' rematch skipped: no changes needed"
            )

        case ("skipped", "success") | ("success", "skipped") | ("success", "success"):
            # Either operation succeeded, or both succeeded
            rematch_status = "success"
            operation_type = "fully" if full_remove else "partially"
            message = f"Sample '{sample.sample_item_name}' is {operation_type} rematched successfully: {remove_summary}"
            if compute_status == "success":
                message += ", new matches computed"

        case ("skipped", "failed") | ("success", "failed"):
            # Computation failed but removal may have worked
            rematch_status = "failed"
            message = f"Sample '{sample.sample_item_name}' rematch failed: {remove_summary}, but match computation failed"

        case _:
            # Unexpected status combination - fallback to safe handling
            rematch_status = "failed"
            message = f"Sample '{sample.sample_item_name}' rematch failed with unexpected error"
            runtime.logger.error(
                f"Unexpected rematch_sample status combination: remove={remove_status}, compute={compute_status}"
            )
    runtime.logger.info(f"{message}. Operation status: {rematch_status}")

    return {
        "status": rematch_status,
        "message": message,
        "data": {
            "removed_match_isotopes_count": removed_count,
        },
        "_notification_data": {
            "sample_batch_id": sample.sample_batch_id,
            "sample_item_id": sample_item_id,
        },
    }


@api_controller_background_task(
    success_notification_rooms=["user_id"],
    success_reload=[("match", "affected_sample_batch_ids")],
    error_notification_rooms=["user_id"],
    error_reload=[("match", "affected_sample_batch_ids")],
)
async def rematch_samples(
    sample_item_ids: list[str],
    full_remove: bool = False,
    independent_transaction: bool = False,
    user_id: int | None = None,
    process_id: str | None = None,
    parent_id: str | None = None,
) -> dict:
    """
    Performs a rematch of multiple samples by removing and/or computing matches based on the specified parameters.
    Thin wrapper arpund the `rematch_sample` controller.

    This function handles the rematch process of the samples by first removing matches associated with removed
    target compounds or ionization mechanisms and then adding matches for added compounds or mechanisms.
    If no parameters are provided, it performs a complete rematch by removing all existing sample matches and
    recomputing them.

    Steps:
    1. Rematch each sample in seqeuence
    2. Retrieve affected sample/batch IDs for reloads

    :param sample_item_ids: IDs of the sample items for which the rematch is to be performed.
    :type sample_item_ids: list[str]
    :param full_remove: If True, removes all existing matches before recomputing, defaults to False
    :type full_remove: bool
    :param independent_transaction: Flag indicating whether the ramtching is an independent transaction, which affects event emission, defaults to False
    :type independent_transaction: bool, optional
    :param user_id: Current user triggered operation (for user notifications)
    :type user_id: int | None, optional
    :param process_id: Process identifier
    :type process_id: str | None, optional
    :param parent_id: Parent process identifier
    :type parent_id: str | None, optional
    :return: The dict with rematched Sample object.
    rtype: dict

    Notes:
        - If `removed_*` parameters are provided, the function removes matches related to these parameters.
        - If `added_*` parameters are provided, the function computes new matches related to these parameters.
        - If no `added_*` or `removed_*` parameters are provided, the function removes all existing matches and computes new matches for all targets.
    """
    # Step 1. rematch each sample in sequence
    for sample_item_id in sample_item_ids:
        await rematch_sample(
            sample_item_id=sample_item_id,
            full_remove=full_remove,
            independent_transaction=False,
            user_id=user_id,
            process_id=gen_id(8),
            parent_id=process_id,
        )
    # Step 2. Retrieve affected sample item and batch IDs for reloads
    (
        affected_sample_item_ids,
        affected_sample_batch_ids,
        *_,
    ) = await fetch_affected_sample_data(sample_item_ids=sample_item_ids)
    return {
        "message": f"Rematched {len(sample_item_ids)} samples",
        "_notification_data": {
            "affected_sample_item_ids": affected_sample_item_ids,
            "affected_sample_batch_ids": affected_sample_batch_ids,
        },
    }


@api_controller_background_task(
    success_notification_rooms=["user_id"],
    success_reload=[("match", "affected_sample_batch_ids")],
    error_notification_rooms=["user_id"],
)
async def match_remove_sample(
    sample_item_id: str,
    full_remove: bool = False,
    independent_transaction: bool = False,
    user_id: int | None = None,
    process_id: str | None = None,
    parent_id: str | None = None,
) -> dict:
    """
    Removes matches for a specific sample.

    By default, performs removal by comparing existing matches against
    current sample-target associations. Use full_remove=True for full removal.

    :param sample_item_id: Unique identifier for the sample
    :type sample_item_id: str
    :param full_remove: If True, removes all matches; if False, removes only orphaned matches, defaults to False.
    :type full_remove: bool
    :param independent_transaction: Flag for transaction handling, defaults to False.
    :type independent_transaction: bool
    :param user_id: Current user triggered operation (for user notifications)
    :type user_id: int | None, optional
    :param process_id: Process identifier
    :type process_id: str | None, optional
    :param parent_id: Parent process identifier
    :type parent_id: str | None, optional
    """
    sample = await fetch_sample(sample_item_id)
    runtime.logger.info(
        f"...{'Fully' if full_remove else 'Partially'} removing matches "
        f"for sample '{sample.sample_item_name}' with ID '{sample_item_id}' ..."
    )
    result = await remove_matches(
        sample=sample,
        full_remove=full_remove,
    )
    status = result.get("status")
    removed_match_isotopes_count = result.get("data", {}).get(
        "removed_match_isotopes_count", 0
    )

    if status == "success":
        await update_sample_modified_timestamps(sample_item_ids=[sample_item_id])
        operation_type = (
            "All matches removed" if full_remove else "Orphaned matches removed"
        )
        message = f"{operation_type} for sample '{sample.sample_item_name}'."
    else:
        message = f"No orphaned matches found for sample '{sample.sample_item_name}' - nothing to remove."

    return {
        "status": status,
        "message": message,
        "data": {
            "removed_match_isotopes_count": removed_match_isotopes_count,
        },
        "_notification_data": {"affected_sample_batch_ids": [sample.sample_batch_id]},
    }


@api_controller_background_task(
    success_notification_rooms=["instrument"],
    success_reload=[("match", "sample_batch_id")],
    error_notification_rooms=["instrument"],
    error_reload=[("match", "sample_batch_id")],
)
async def match_compute_sample(
    sample_item_id: str,
    independent_transaction: bool = False,
    user_id: int | None = None,
    process_id: str | None = None,
    parent_id: str | None = None,
) -> dict:
    """
    Computes new matches for a specific sample by identifying missing target isotope matches.
    - Automatically determines which target isotopes need computation
    - Handles m/z calibration verification and polarity filtering
    - Aggregates higher-level matches based on computed isotopes

    :param sample_item_id: Sample item identifier for match computation
    :type sample_item_id: str
    :param independent_transaction: Controls event emission behavior, defaults to False
    :type independent_transaction: bool, optional
    :param user_id: Current user triggered operation (for user notifications)
    :type user_id: int | None, optional
    :param process_id: Process identifier for progress tracking
    :type process_id: str, optional
    :param parent_id: Parent process identifier
    :type parent_id: str, optional
    :raises ApiException: When sample not found, calibration issues, or no new targets available
    :return: A dictionary with status message.
    :rtype: dict
    """
    # -- Gather sample information
    sample = await fetch_sample(sample_item_id)

    if _is_blank_sample(sample):
        # Early exit for samples with no peaks (blank samples)
        warning_message = (
            f"Sample '{sample.sample_item_name}' has no peaks. Matching is skipped."
        )
        raise_api_warning(warning_message, {"sample_item_id": sample_item_id})
        return {
            "status": "skipped",
            "message": warning_message,
            "_notification_data": {
                "sample_batch_id": sample.sample_batch_id,
                "sample_item_id": sample_item_id,
            },
        }

    # -- Check if m/z calibration is verified for the sample
    # TODO_calibration split on orbi/tof?
    verified = (
        sample.mz_calibration.get("verified", False)
        if sample.mz_calibration is not None
        else True
    )

    if not verified:
        warning_message = f"m/z calibration is not verified for sample file: {sample.filename}. Please try to calibrate the file."
        raise_api_warning(warning_message, {"sample_item_id": sample_item_id})

    runtime.logger.info(
        f"...Computing match isotopes for sample {sample.sample_item_name}: {sample_item_id} ..."
    )

    # -- Fetch target isotopes needing computation, applies all filters centrally on db lvl
    match_params = await default_match_params(sample_item_id)
    target_isotopes_df = await fetch_sample_unmatched_target_isotopes(
        sample, match_params
    )

    # -- Fetch existing main isotope references for abundance error calculation
    existing_reference_df = None
    if target_isotopes_df is not None and not target_isotopes_df.empty:
        unmatched_ion_ids = target_isotopes_df["target_ion_id"].unique().tolist()
        existing_reference_df = await fetch_existing_main_isotope_references(
            sample.sample_item_id, unmatched_ion_ids
        )

    # -- Compute match_isotopes for the sample
    computed_match_isotopes_count = 0
    if target_isotopes_df is not None and not target_isotopes_df.empty:
        progress_notification = UserNotification(
            process_id=process_id,
            parent_id=parent_id,
            type="match_compute_sample",
            status="pending",
            message=f"Computing match isotopes for sample '{sample.sample_item_name}'.",
            data={
                "sample_item_id": sample_item_id,
                "_room_ids": [sample.instrument],
                "_user_id": user_id,
            },
        )
        match_data = await compute_and_create_sample_match_isotope_data(
            sample, target_isotopes_df, existing_reference_df, progress_notification
        )
        computed_match_isotopes_count = len(match_data["match_isotopes"])

    # -- Aggregate higher-level matches and update timestamps
    match_aggregate_result = await aggregate_and_create_matches(
        sample_item_id=sample_item_id
    )
    match_aggregate_status = match_aggregate_result.get("status")

    if match_aggregate_status in ("success", "partial"):
        runtime.logger.debug(
            f"Aggregated new higher-level matches for sample '{sample.sample_item_name}'."
        )
        await update_sample_modified_timestamps(sample_item_ids=[sample_item_id])

    # -- Determine status based on outcomes
    message = f"Finished computing matches ({match_aggregate_status}) for sample '{sample.sample_item_name}'."
    if computed_match_isotopes_count > 0:
        compute_status = "success"
        message += f" Computed {computed_match_isotopes_count} new match isotope{'s' if computed_match_isotopes_count != 1 else ''}."
    else:
        compute_status = "skipped"
        message += (
            f" No new match isotopes found for sample '{sample.sample_item_name}'."
        )

    if match_aggregate_status in ("success", "partial"):
        compute_status = "success"
    else:
        # Nothing computed, nothing aggregated
        compute_status = "skipped"
    message += f" (status: {compute_status})"
    runtime.logger.debug(message)

    # -- Return sample with computed match data and status message
    return {
        "status": compute_status,
        "message": message,
        "_notification_data": {
            "instrument": sample.instrument,  # For notification routing
            "sample_batch_id": sample.sample_batch_id,  # For reload events
        },
    }


@api_controller_background_task(
    success_notification_rooms=["user_id"],
    success_reload=[("match", "affected_sample_batch_ids")],
    error_notification_rooms=["user_id"],
    error_reload=[("match", "affected_sample_batch_ids")],
)
async def match_compute_samples(
    sample_item_ids: list[str],
    independent_transaction: bool = False,
    user_id: int | None = None,
    process_id: str | None = None,
    parent_id: str | None = None,
) -> dict:
    """
    Computes new matches for multiple samples by processing each individually.
    - Thin wrapper around match_compute_sample for processing sample lists
    - Uses simplified automatic target detection per sample
    - Provides basic progress tracking and error collection

    Steps:
    1. Compute match samples
    2. Retrieve affected sample/batch IDs for reloads

    :param sample_item_ids: ID of the sample item for which matches are to be computed.
    :type sample_item_ids: str
    :param independent_transaction: Flag indicating whether the sample match computing is an independent transaction, which affects event emission, defaults to False
    :type independent_transaction: bool, optional
    :param user_id: Current user triggered operation (for user notifications)
    :type user_id: int | None, optional
    :param process_id: Process identifier for progress tracking
    :type process_id: str | None, optional
    :param parent_id: Parent process identifier
    :type parent_id: str | None, optional
    :raises ApiException: Raised when no new target isotopes are available for match computation or if other critical preconditions are not met.
    :return: A dictionary containing the rematched sample object and a status message.
    :rtype: dict
    """
    # Step 1. Compute match samples
    for sample_item_id in sample_item_ids:
        await match_compute_sample(
            sample_item_id=sample_item_id,
            independent_transaction=False,
            user_id=user_id,
            process_id=gen_id(8),
            parent_id=process_id,
        )
    # Step 2. Retrieve affected sample/batch IDs for reloads
    (
        affected_sample_item_ids,
        affected_sample_batch_ids,
        *_,
    ) = await fetch_affected_sample_data(sample_item_ids=sample_item_ids)
    return {
        "message": f"Match isotopes computed for {len(sample_item_ids)} samples.",
        "_notification_data": {
            "affected_sample_item_ids": affected_sample_item_ids,
            "affected_sample_batch_ids": affected_sample_batch_ids,
        },
    }


# -------------------------------------------------------------------
# Batch level
# -------------------------------------------------------------------


# How many batches the rematch summary names one by one before it reports the
# rest as a count. The message is the only part of the outcome that reaches
# the user - the notification pane renders type, status and message, and
# nothing in the tree consumes the per-batch ids carried in the payload - so
# it has to name them, without letting a large selection turn one notification
# into a wall of text.
MAX_LISTED_REMATCH_BATCHES = 10


async def _fetch_sample_batch_names(sample_batch_ids: list[str]) -> dict[str, str]:
    """
    Display names for the given batches, resolved in a single query.

    Called only once a run has something to report, so a clean run pays
    nothing and a run with problems pays one query rather than one per batch.

    Resolving a name is a courtesy to the summary and never a reason for it to
    fail: a batch deleted mid-run simply has no row, and a database that
    cannot answer at all yields an empty mapping. Callers fall back to the id.

    :param sample_batch_ids: The batches to name.
    :type sample_batch_ids: list[str]
    :return: Mapping of sample batch id to name, missing whatever it could not
        resolve.
    :rtype: dict[str, str]
    """
    if not sample_batch_ids:
        return {}
    try:
        async with async_session() as session:
            rows = await session.execute(
                select(
                    SampleBatch.sample_batch_id, SampleBatch.sample_batch_name
                ).where(SampleBatch.sample_batch_id.in_(sample_batch_ids))
            )
            return dict(rows.all())
    except Exception:
        runtime.logger.exception(
            "Could not resolve sample batch names for the rematch summary"
        )
        return {}


def _batch_display_name(sample_batch_id: str, batch_names: dict[str, str]) -> str:
    """
    Name a batch the way the UI does, degrading to its id.

    :param sample_batch_id: The batch to name.
    :type sample_batch_id: str
    :param batch_names: Names resolved by :func:`_fetch_sample_batch_names`.
    :type batch_names: dict[str, str]
    :return: The batch name, or the id when no name is known.
    :rtype: str
    """
    return batch_names.get(sample_batch_id) or sample_batch_id


def _user_facing_reason(error: Exception) -> str:
    """
    The part of a failure that is safe to put in front of a user.

    An ``ApiException`` carries a ``user_message`` written for a human - it is
    what every other layer surfaces, and by the time a nested rematch reaches
    the aggregate that is what its failures are, the background task decorator
    having already put anything else through ``process_exception``.

    That message is taken as it stands, and this adds no guarantee to it.
    ``process_exception`` genericizes most internals behind a "ref:" id, but
    not all of them - a ``ValueError`` is answered with its own ``str()``,
    filesystem path and all - so a reason here says exactly what the same
    batch rematched on its own would say, no more and no less.

    What this does guarantee is that the aggregate leaks nothing extra: an
    exception that reaches it unwrapped never went through that mapping, and
    its ``str()`` is an internal detail (a filesystem path, an attribute name,
    a driver message), so it is dropped; the caller logs the traceback.

    :param error: The exception a single batch's rematch raised.
    :type error: Exception
    :return: A reason suitable for the aggregate's notification message.
    :rtype: str
    """
    if isinstance(error, ApiException):
        return error.user_message
    return "Unexpected error."


def _log_batch_rematch_failure(sample_batch_id: str, error: Exception) -> None:
    """
    Record a batch that did not rematch, at the level that failure earns.

    Must be called from inside the ``except`` block, so the traceback is still
    the one being handled.

    ``runtime.logger.exception`` writes an ERROR carrying the exception, and
    every record at WARNING or above is exported to error monitoring
    (``mascope_runtime.logging``), which files an event for whatever reaches
    it. Most of what reaches this handler is not a fault. A nested rematch is
    a dependent task, so its partial-success warning ("matching produced no
    results for 3 of 12 samples") is re-raised to this aggregate as an
    ``ApiException`` with status 200; a batch id that names nothing arrives as
    a 404. Neither is actionable: the first is a result, the second is the
    caller asking for something that is not there. ``process_exception``
    already classifies both as routine and logs them at INFO - and by the time
    they get here it has, because the background-task decorator put them
    through it - so paging on the aggregate's second copy of the same incident
    is the whole of the defect.

    :func:`~mascope_backend.api.lib.exceptions.api_exceptions.is_expected_client_error`
    is that same classification, so this picks the level on exactly the terms
    the rest of the API uses. A routine outcome keeps its record, reason and
    all, one level down where monitoring does not follow; a genuine fault is
    untouched - same ERROR, same traceback, same message, still attributed to
    ``rematch_batches`` rather than to this helper.

    A failure with no status never went through that mapping, which means it
    never came out of the nested rematch at all (the decorator wraps
    everything that leaves it) but out of this loop's own body. That is an
    internal fault and is treated as one.

    :param sample_batch_id: The batch whose rematch raised.
    :type sample_batch_id: str
    :param error: The exception it raised.
    :type error: Exception
    :return: None
    """
    status_code = getattr(error, "status_code", None)
    if isinstance(status_code, int) and is_expected_client_error(error, status_code):
        with runtime.logger.contextualize(status_code=status_code):
            runtime.logger.info(f"Batch {sample_batch_id} did not rematch: {error}")
        return
    runtime.logger.opt(depth=1).exception(
        f"Unexpected error rematching batch {sample_batch_id}"
    )


def _compose_rematch_problem_lines(
    failure_reasons: list[tuple[str, str]],
    locked_batch_ids: list[str],
    batch_names: dict[str, str],
) -> list[str]:
    """
    Say which batches did not rematch, and - for the failures - why.

    Same shape as the batch calibration aggregate builds for its own partial
    failures: a count, then one line per failure naming the batch and its
    reason, truncated to ``MAX_LISTED_REMATCH_BATCHES`` entries.

    The two buckets are reported differently because they are different
    outcomes. A failure is an error whose cause the user cannot guess, so each
    one gets its reason. A lock is the routine outcome of the batch already
    being processed - one shared, self-explanatory cause - so those batches are
    only named, on one line, with the remedy (wait and retry) stated once.

    :param failure_reasons: ``(sample_batch_id, reason)`` per failed batch, in
        the order the batches were processed.
    :type failure_reasons: list[tuple[str, str]]
    :param locked_batch_ids: Batches skipped because they were already being
        processed.
    :type locked_batch_ids: list[str]
    :param batch_names: Names resolved by :func:`_fetch_sample_batch_names`.
    :type batch_names: dict[str, str]
    :return: Lines to append to the summary message (empty when nothing went
        wrong).
    :rtype: list[str]
    """
    lines = []

    if failure_reasons:
        listed = failure_reasons[:MAX_LISTED_REMATCH_BATCHES]
        lines.append(f"Failed to rematch {len(failure_reasons)} batch(es):")
        lines.extend(
            f"{_batch_display_name(sample_batch_id, batch_names)}: {reason}"
            for sample_batch_id, reason in listed
        )
        remaining = len(failure_reasons) - len(listed)
        if remaining:
            lines.append(f"...and {remaining} more.")

    if locked_batch_ids:
        listed_ids = locked_batch_ids[:MAX_LISTED_REMATCH_BATCHES]
        names = [
            _batch_display_name(sample_batch_id, batch_names)
            for sample_batch_id in listed_ids
        ]
        remaining = len(locked_batch_ids) - len(listed_ids)
        if remaining:
            names.append(f"and {remaining} more")
        # One locked batch is the common case - a user retrying the batch they
        # just started - so the remedy is worth saying in the singular rather
        # than telling them to try "these" again once "they" finish.
        remedy = (
            "try this one again once it finishes"
            if len(locked_batch_ids) == 1
            else "try these again once they finish"
        )
        lines.append(
            f"Already being processed, so rematch was locked - {remedy}: "
            f"{', '.join(names)}."
        )

    return lines


@api_controller_background_task(
    success_notification_rooms=["user_id"],
    success_reload=[("match", "affected_sample_batch_ids")],
    error_notification_rooms=["user_id"],
    error_reload=[("match", "affected_sample_batch_ids")],
)
async def rematch_batches(
    sample_batch_ids: list[str],
    full_remove: bool = False,
    force: bool = False,
    independent_transaction: bool = False,
    user_id: int | None = None,
    process_id: str | None = None,
    parent_id: str | None = None,
) -> dict:
    """
    Performs rematch operation on multiple sample batches with status tracking.

    Result categorization:
    - successful_batches: Batches that completed successfully (status: "success")
    - failed_batches: Batches that encountered critical failures (status: "failed")
    - partial_batches: Batches with mixed results (status: "partial")
    - skipped_batches: Batches with no changes needed (status: "skipped")
    - locked_batches: Batches already being processed (status: "locked")

    The summary message names every batch that failed (with its reason) or was
    locked: the nested rematches are dependent tasks whose own warnings are
    suppressed, so this is the only report the user gets of them.

    :param sample_batch_ids: A list of sample batch IDs to be rematched.
    :type sample_batch_ids: list[str]
    :param full_remove: If True, removes all matches; if False, removes only orphaned matches
    :type full_remove: bool
    :param force: If True, bypasses batch status checks and forces rematch regardless of current status
    :type force: bool
    :param independent_transaction: Flag for transaction handling
    :type independent_transaction: bool
    :param user_id: Current user triggered operation (for user notifications)
    :type user_id: int | None, optional
    :param process_id: Process identifier for progress tracking
    :type process_id: str | None
    :param parent_id: Parent process identifier
    :type parent_id: str | None
    :raises NotFoundException: When batch not found
    :raises ApiException: When batch is already processing or rematch fails
    :return: Rematch results with batch categorization and aggregate statistics
    :rtype: dict
    """
    total_batches_count = len(sample_batch_ids)
    if not total_batches_count:
        # Nothing to iterate over - a dataset-wide refresh of a dataset that
        # holds no batches lands here. Reported as a plain result rather than
        # falling through to the "all batches failed" branch below, which
        # would call an empty run a failure.
        message = "No sample batches to rematch."
        runtime.logger.info(message)
        return {
            "message": message,
            "data": {
                "processed_batches": {"total_batches_count": 0},
                "processed_samples": {
                    "removed_match_isotopes_count": 0,
                    "computed_samples_count": 0,
                    "failed_samples_count": 0,
                    "skipped_samples_count": 0,
                    "total_samples_count": 0,
                },
            },
            "_notification_data": {"affected_sample_batch_ids": []},
        }

    runtime.logger.info(
        f"Starting {'full' if full_remove else 'partial'} rematch for {total_batches_count} batches"
    )
    # Step 1: Collect processing statistics
    batch_collections = {
        "success_batches": [],
        "skipped_batches": [],
        "partial_batches": [],
        "failed_batches": [],
        "locked_batches": [],
    }
    # Why each failed batch failed, paired with its id and in the same order
    # as batch_collections["failed_batches"]. Kept beside that list rather than
    # in it so the payload's shape is unchanged for anything reading the ids.
    failure_reasons: list[tuple[str, str]] = []

    processed_samples = {
        "removed_match_isotopes_count": 0,
        "computed_samples_count": 0,
        "failed_samples_count": 0,
        "skipped_samples_count": 0,
        "total_samples_count": 0,
    }

    # Sample counts only weight the progress bar, so they are counted in one
    # grouped query rather than by reading every batch's samples: rematching a
    # whole dataset would otherwise load every sample of every batch, with its
    # nested match data, before starting any actual work.
    async with async_session() as session:
        batch_sample_counts = dict(
            (
                await session.execute(
                    select(Sample.sample_batch_id, func.count())
                    .where(Sample.sample_batch_id.in_(sample_batch_ids))
                    .group_by(Sample.sample_batch_id)
                )
            ).all()
        )
    samples_per_batch = [
        batch_sample_counts.get(sample_batch_id, 0)
        for sample_batch_id in sample_batch_ids
    ]
    total_samples_count = sum(samples_per_batch)

    processed_samples["total_samples_count"] = total_samples_count
    # Each batch's share of the progress bar. Batches with no samples still
    # take a turn, so a run over empty batches falls back to equal shares
    # rather than to a bar that never moves.
    batch_weights = [
        samples / total_samples_count
        if total_samples_count
        else 1 / total_batches_count
        for samples in samples_per_batch
    ]

    # Step 2: Process each batch
    completed_weight = 0.0
    for batch_index, (sample_batch_id, batch_weight) in enumerate(
        zip(sample_batch_ids, batch_weights), start=1
    ):
        notification = UserNotification(
            process_id=process_id,
            type="rematch_batches",
            status="pending",
            message=f"Rematching sample batch {batch_index}/{total_batches_count}.",
            data={
                "sample_batch_id": sample_batch_id,
                "_user_id": user_id,
                "_batch_weight": batch_weight,
                # How much of the run is already behind this batch: the bar
                # advances through its own share from there, so it climbs
                # once across the whole run instead of restarting per batch.
                "_batch_base": completed_weight,
            },
        )
        await send_progress_user_notification(notification, 0.2)

        try:
            runtime.logger.debug(
                f"Processing batch {batch_index}/{total_batches_count}: {sample_batch_id}"
            )
            batch_result = await rematch_batch(
                sample_batch_id=sample_batch_id,
                full_remove=full_remove,
                force=force,
                independent_transaction=False,
                user_id=user_id,
                process_id=gen_id(8),
                parent_id=process_id,
            )

            # Aggregate sample metrics
            batch_data = batch_result.get("data", {})
            for key in processed_samples:
                if key != "total_samples_count":  # Skip total as it's pre-calculated
                    processed_samples[key] += batch_data.get(key, 0)

            # Categorize batch rematch result
            batch_collections[f"{batch_result['status']}_batches"].append(
                sample_batch_id
            )

        except Exception as e:
            batch_collections["failed_batches"].append(sample_batch_id)
            # The child's warning is suppressed (it is a dependent task, so
            # this aggregate reports for it), which makes this the only place
            # the reason can still reach the user - keep it. The full detail
            # stays in the server log below either way, with the traceback
            # when the failure is one.
            failure_reasons.append((sample_batch_id, _user_facing_reason(e)))
            _log_batch_rematch_failure(sample_batch_id, e)

        # Update proress user notification
        notification.message = (
            f"Finished rematching sample batch {batch_index}/{total_batches_count}."
        )
        await send_progress_user_notification(notification, 0.8)
        completed_weight += batch_weight

    # Calculate summary metrics
    processed_batches_count = (
        len(batch_collections["success_batches"])
        + len(batch_collections["skipped_batches"])
        + len(batch_collections["partial_batches"])
    )
    problematic_batches_count = len(batch_collections["failed_batches"]) + len(
        batch_collections["locked_batches"]
    )

    message_parts = []
    for status in ["success", "skipped", "partial", "locked", "failed"]:
        count = len(batch_collections[f"{status}_batches"])
        if count > 0:
            message_parts.append(f"{count} {status}")

    message = f"Rematch of sample batches completed: {processed_batches_count}/{total_batches_count} batches processed ({', '.join(message_parts)})"

    # Add sample processing statistics if operations occurred
    if (
        processed_samples["computed_samples_count"] > 0
        or processed_samples["removed_match_isotopes_count"] > 0
    ):
        stats_parts = []
        if processed_samples["removed_match_isotopes_count"] > 0:
            stats_parts.append(
                f"{processed_samples['removed_match_isotopes_count']} match isotopes removed"
            )
        if processed_samples["computed_samples_count"] > 0:
            stats_parts.append(
                f"{processed_samples['computed_samples_count']} samples computed"
            )
        if processed_samples["failed_samples_count"] > 0:
            stats_parts.append(
                f"{processed_samples['failed_samples_count']} sample failures"
            )
        if processed_samples["skipped_samples_count"] > 0:
            stats_parts.append(
                f"{processed_samples['skipped_samples_count']} samples skipped"
            )

        message += f". Sample rematch processing: {', '.join(stats_parts)}"

    # Name whatever did not rematch. The counts above say how much went wrong;
    # only the names say what to go and look at, and the nested rematches no
    # longer report for themselves - this aggregate reports for them.
    problem_lines = []
    problem_batch_ids = (
        batch_collections["failed_batches"] + batch_collections["locked_batches"]
    )
    if problem_batch_ids:
        problem_lines = _compose_rematch_problem_lines(
            failure_reasons,
            batch_collections["locked_batches"],
            await _fetch_sample_batch_names(problem_batch_ids),
        )
        message = "\n".join([message] + problem_lines)

    runtime.logger.info(message)

    # Error handling based on results
    response_data = {
        "data": {
            "processed_batches": {
                "total_batches_count": total_batches_count,
                "success_batches_count": len(batch_collections["success_batches"]),
                "skipped_batches_count": len(batch_collections["skipped_batches"]),
                "partial_batches_count": len(batch_collections["partial_batches"]),
                "failed_batches_count": len(batch_collections["failed_batches"]),
                "locked_batches_count": len(batch_collections["locked_batches"]),
                **batch_collections,
            },
            "processed_samples": processed_samples,
        },
        "_notification_data": {"affected_sample_batch_ids": sample_batch_ids},
    }
    if len(batch_collections["failed_batches"]) == total_batches_count:
        # Every batch genuinely failed - critical error, and the case where
        # naming them matters most, so the detail travels with the error too
        # and not only with the partial-success warning.
        #
        # The test is on the failed count rather than on "nothing was
        # processed", because a locked batch is not a failure: it is one that
        # is mid-processing and was deliberately left alone. A dataset-wide
        # refresh over a dataset that happens to be busy is entirely locked,
        # and reporting that as "all N batches failed" contradicts what this
        # operation promises.
        raise ApiException(
            "\n".join(
                [f"All {total_batches_count} batches failed to process"] + problem_lines
            ),
            response_data,
            500,
        )
    elif problematic_batches_count > 0:
        # Mixed results, or nothing but locked batches - warning
        raise_api_warning(message, response_data)

    return {
        "message": message,
        **response_data,
    }


@api_controller_background_task(
    success_notification_rooms=["sample_batch_id"],
    success_reload=[("match", "sample_batch_id")],
    error_notification_rooms=["sample_batch_id"],
    error_reload=[("match", "sample_batch_id")],
)
async def rematch_batch(
    sample_batch_id: str,
    full_remove: bool = False,
    force: bool = False,
    independent_transaction: bool = False,
    user_id: int | None = None,
    process_id: str | None = None,
    parent_id: str | None = None,
) -> dict:
    """
    Performs rematch operation on sample batch by removing and recomputing matches.
    - Sets batch status to processing during operation, which locks concurrent rematching
    - Removes orphaned/all matches based on full_remove parameter
    - Computes missing matches
    - Updates batch status to ready on success or rematch on failure

    Operation status logic:
    - skipped: No changes made to batch (no orphans to remove, no new matches to compute)
    - success: Operations completed successfully (match data was modified)
    - partial: Mixed results (some operations succeeded, some failed)
    - failed: Critical failures occurred during processing

    :param sample_batch_id: ID of the sample batch for rematching
    :type sample_batch_id: str
    :param full_remove: If True, removes all matches; if False, removes only orphaned matches
    :type full_remove: bool
    :param force: If True, bypasses status checks and forces rematch regardless of current status
    :type force: bool
    :param independent_transaction: Flag for transaction handling
    :type independent_transaction: bool
    :param user_id: Current user triggered operation (for user notifications)
    :type user_id: int | None, optional
    :param process_id: Process identifier for progress tracking
    :type process_id: str | None
    :param parent_id: Parent process identifier
    :type parent_id: str | None
    :raises NotFoundException: When batch not found
    :raises ApiException: When batch is already processing or rematch fails
    :return: Batch data with rematch results, status, and aggregated statistics
    :rtype: dict
    """
    # Step 1: Retrieve and handle current batch status
    sample_batch = await fetch_sample_batch(sample_batch_id)

    sample_batch_name = sample_batch.sample_batch_name
    batch_status = sample_batch.status
    operation_type = "Full" if full_remove else "Partial"
    runtime.logger.info(
        f"{operation_type} rematching for batch '{sample_batch_name}' (current status: {batch_status})"
    )

    # Check batch status - processing is never bypassable
    if not force and batch_status != "rematch" or batch_status == "processing":
        async with async_session() as session:
            skipped_samples_count = await session.scalar(
                select(func.count()).where(Sample.sample_batch_id == sample_batch_id)
            )
        response_data = {
            "data": {
                "removed_match_isotopes_count": 0,
                "computed_samples_count": 0,
                "failed_samples_count": 0,
                "skipped_samples_count": skipped_samples_count,
            },
            "_notification_data": {"sample_batch_id": sample_batch_id},
        }

        if batch_status == "processing":
            status, message = (
                "locked",
                f"Sample batch '{sample_batch_name}' is currently being processed - rematch is locked.",
            )
            # Routine concurrency outcome, reported to the user via the response
            runtime.logger.info(message)
        else:  # ready status without force
            status, message = (
                "skipped",
                f"Sample batch '{sample_batch_name}' status is '{batch_status}' - rematch skipped.",
            )
            runtime.logger.info(
                f"{message} Use force=True to override the status check."
            )

        return {"status": status, "message": message, **response_data}

    await update_sample_batch_status(
        sample_batch_ids=[sample_batch_id],
        status="processing",
        independent_transaction=True,  # reload UI status icons
    )

    try:
        # Step 2: Send initial progress notification
        progress_notification = UserNotification(
            process_id=process_id,
            parent_id=parent_id,
            type="rematch_batch",
            status="pending",
            message=f"{operation_type} for batch '{sample_batch_name}'",
            data={
                "sample_batch_id": sample_batch_id,
                "_room_ids": [sample_batch_id],
                "_user_id": user_id,
            },
        )
        await send_progress_user_notification(progress_notification)

        # Step 3: Remove existing matches (partially or fully)
        remove_result = await match_remove_batch(
            sample_batch_id=sample_batch_id,
            full_remove=full_remove,  # conditionally remove all existing matches
            independent_transaction=False,
            user_id=user_id,
            process_id=gen_id(8),
            parent_id=process_id,
        )

        # Step 4: Compute new matches. The removal-affected samples join the
        # compute step's aggregation scope (and a no-op removal contributes
        # nothing, letting a no-op refresh skip aggregation entirely).
        compute_result = await match_compute_batch(
            sample_batch_id=sample_batch_id,
            independent_transaction=False,
            user_id=user_id,
            process_id=gen_id(8),
            parent_id=process_id,
            removed_sample_item_ids=remove_result.get("data", {}).get(
                "orphaned_sample_item_ids", []
            ),
        )

        # Step 5: Determine final status and message
        remove_status = remove_result["status"]
        compute_status = compute_result["status"]
        removed_count = remove_result.get("data", {}).get(
            "removed_match_isotopes_count", 0
        )
        compute_data = compute_result.get("data", {})
        computed_count = compute_data.get("computed_samples_count", 0)
        failed_count = compute_data.get("failed_samples_count", 0)
        skipped_count = compute_data.get("skipped_samples_count", 0)
        total_samples = compute_data.get("total_samples_count", 0)

        removed = f"all {removed_count}" if full_remove else f"{removed_count} orphaned"
        remove_summary = f"{removed if removed_count else 'no'} match isotopes removed"
        compute_summary = f"{computed_count}/{total_samples} samples computed missing matches successfully"
        failed_summary = (
            f"match computation failed for {failed_count}/{total_samples} samples"
        )
        skipped_summary = f"{skipped_count}/{total_samples} samples skipped match computation due to missing calibration or no new target associations"
        match (remove_status, compute_status):
            case ("skipped", "skipped"):
                if total_samples == 0:
                    rematch_status = "skipped"
                    result_batch_status = "ready"
                    message = f"Sample batch '{sample_batch_name}' has no samples - nothing to rematch."
                else:
                    # No orphaned matches to remove, no new matches to compute
                    rematch_status = "skipped"
                    result_batch_status = "ready"  # nothing to rematch
                    message = f"Sample batch '{sample_batch_name}' has skipped rematching: {skipped_summary}."
            case (
                ("skipped", "success") | ("success", "skipped") | ("success", "success")
            ):
                # Either operation succeeded without failures, or both succeeded
                rematch_status = "success"
                result_batch_status = "ready"
                message = f"Sample batch '{sample_batch_name}' rematched successfully: {remove_summary}"
                if compute_status == "success" and skipped_count == 0:
                    message += f", {compute_summary}."
                elif compute_status == "success" and skipped_count > 0:
                    message += f", {compute_summary}, but {skipped_summary}."
                else:
                    message += f"{skipped_summary}."

            case ("skipped", "partial") | ("success", "partial"):
                # Computation had mixed results (some succeeded, some failed)
                rematch_status = "partial"  # some operations failed, may need retry
                result_batch_status = "rematch"
                message = f"Sample batch '{sample_batch_name}' partially rematched:  {remove_summary}, {compute_summary}, {failed_summary}."

            case ("skipped", "failed") | ("success", "failed"):
                # Computation completely failed
                rematch_status = "failed"
                result_batch_status = "rematch"  # operation failed, needs retry
                message = f"Sample batch '{sample_batch_name}' rematch failed: {remove_summary}, but {failed_summary}."
            case _:
                # Unexpected status combination - fallback to safe handling
                rematch_status = "failed"
                result_batch_status = "rematch"  # operation failed, needs retry
                message = f"Sample batch '{sample_batch_name}' rematch failed with unexpected error."
                runtime.logger.error(
                    f"Unexpected rematch_batch status combination: remove={remove_status}, compute={compute_status}"
                )

        # Update batch status
        await update_sample_batch_status(
            sample_batch_ids=[sample_batch_id],
            status=result_batch_status,
            independent_transaction=True,  # reload UI status icons
        )

        # Step 6: Log final outcome and return structured response
        runtime.logger.info(
            f"{message} rematch status: {rematch_status}, batch status - {result_batch_status}."
        )

        return {
            "status": rematch_status,
            "message": message,
            "data": {
                "removed_match_isotopes_count": removed_count,
                "computed_samples_count": computed_count,
                "failed_samples_count": failed_count,
                "skipped_samples_count": skipped_count,
            },
            "_notification_data": {"sample_batch_id": sample_batch_id},
        }

    except ApiException as e:
        # Step 7: Revert status on failure to allow retry rematching
        await update_sample_batch_status(
            sample_batch_ids=[sample_batch_id],
            status="rematch",
            independent_transaction=True,  # reload UI status icons
        )
        runtime.logger.exception(
            f"Rematch failed for batch '{sample_batch_name}': {e.user_message}"
        )
        raise


@api_controller_background_task(
    success_notification_rooms=["sample_batch_id"],
    success_reload=[("match", "sample_batch_id")],
    error_notification_rooms=["sample_batch_id"],
)
async def match_remove_batch(
    sample_batch_id: str,
    full_remove: bool = False,
    independent_transaction: bool = False,
    user_id: int | None = None,
    process_id: str | None = None,
    parent_id: str | None = None,
) -> dict:
    """
    Removes matches for all samples in the specified sample batch.

    By default, performs removal by comparing existing matches against
    current sample-target associations. Use full_remove=True for full removal.

    :param sample_batch_id: ID of the sample batch for which matches are to be removed.
    :type sample_batch_id: str
    :param full_remove: If True, removes all matches; if False, removes only orphaned matches, defaults to False.
    :type full_remove: bool
    :param independent_transaction: Flag indicating if the operation should be an independent transaction, default to False.
    :type independent_transaction: bool
    :param user_id: Current user triggered operation (for user notifications)
    :type user_id: int | None, optional
    :param process_id: Process identifier for progress tracking
    :param parent_id: Parent process identifier
    :return: Batch data with removal results and status message
    :rtype: dict
    """
    # Step 1: Retrieve batch data
    sample_batch = await fetch_sample_batch(sample_batch_id)

    runtime.logger.info(
        f"...{'Fully' if full_remove else 'Partially'} removing matches "
        f"for sample batch '{sample_batch.sample_batch_name}' with ID '{sample_batch_id}' ..."
    )

    # Step 2: Remove match data and associated sample batch.
    result = await remove_matches(sample_batch=sample_batch, full_remove=full_remove)
    status = result.get("status")
    removed_match_isotopes_count = result.get("data", {}).get(
        "removed_match_isotopes_count", 0
    )

    if status == "success":
        await update_sample_modified_timestamps(sample_batch_ids=[sample_batch_id])
        remove_message = f"{f'All {removed_match_isotopes_count}' if full_remove else f'{removed_match_isotopes_count} orphaned'} match isotopes removed"
    else:
        remove_message = "No orphaned matches found"

    message = f"{remove_message} for sample batch '{sample_batch.sample_batch_name}'."
    runtime.logger.debug(f"{message} Operation status: {status}.")
    # Step 4: Return sample batch data and message
    return {
        "status": status,
        "message": message,
        "data": {
            "removed_match_isotopes_count": removed_match_isotopes_count,
            "orphaned_sample_item_ids": result.get("data", {}).get(
                "orphaned_sample_item_ids", []
            ),
        },
        "_notification_data": {"sample_batch_id": sample_batch_id},
    }


@api_controller_background_task(
    success_notification_rooms=["sample_batch_id"],
    success_reload=[("match", "sample_batch_id")],
    error_notification_rooms=["sample_batch_id"],
    error_reload=[("match", "sample_batch_id")],
)
async def match_compute_batch(
    sample_batch_id: str,
    independent_transaction: bool = False,
    user_id: int | None = None,
    process_id: str | None = None,
    parent_id: str | None = None,
    removed_sample_item_ids: list[str] | None = None,
) -> dict:
    """
    Computes new matches for all samples within a batch, processing each sample:
    - Filters which target isotopes need computation
    - Aggregates higher-level matches, scoped to the affected samples only
      (computed or failed this run, touched by the preceding removal, or with
      incomplete stored aggregates); skipped entirely when no sample is
      affected

    :param sample_batch_id: The identifier of the sample batch for which match computation is to be performed.
    :type sample_batch_id: str
    :param independent_transaction: Controls event emission behavior
    :type independent_transaction: bool
    :param user_id: Current user triggered operation (for user notifications)
    :type user_id: int | None, optional
    :param process_id: Process identifier for progress tracking
    :param parent_id: Parent process identifier
    :param removed_sample_item_ids: Samples whose match rows the preceding
        removal step touched (rematch flow); they join the aggregation scope
    :type removed_sample_item_ids: list[str] | None
    :raises NotFoundException: When batch not found
    :raises ApiException: When batch has no samples or critical failures occur
    :return: Batch data with computation results and status message
    :rtype: dict
    """
    # Step 1: Retrieve sample batch and all associated samples.
    sample_batch = await fetch_sample_batch(sample_batch_id)

    async with async_session() as session:
        samples = (
            (
                await session.execute(
                    select(Sample).where(Sample.sample_batch_id == sample_batch_id)
                )
            )
            .scalars()
            .all()
        )

    sample_batch_name = sample_batch.sample_batch_name
    total_samples_count = len(samples)

    if total_samples_count == 0:
        message = f"Sample batch '{sample_batch.sample_batch_name}' has no samples"
        runtime.logger.debug(message)
        return {
            "status": "skipped",
            "message": message,
            "data": {
                "computed_samples_count": 0,
                "failed_samples_count": 0,
                "skipped_samples_count": 0,
                "total_samples_count": total_samples_count,
            },
            "_notification_data": {"sample_batch_id": sample_batch_id},
        }

    runtime.logger.info(
        f"Computing matches for sample batch '{sample_batch_name}' ({total_samples_count} samples)"
    )

    # Step 2: Process each sample for match computation
    computed_samples = []
    failed_samples = []
    skipped_samples = []
    for item_index, sample in enumerate(samples):
        progress_notification = UserNotification(
            process_id=process_id,
            parent_id=parent_id,
            type="match_compute_batch",
            status="pending",
            message=f"Processing sample {item_index + 1}/{total_samples_count} in sample batch '{sample_batch_name}'",
            data={
                "sample_batch_id": sample_batch_id,
                "_room_ids": [sample_batch_id],
                "_user_id": user_id,
                "_total_samples": total_samples_count,
                "_item_index": item_index,
            },
        )
        try:
            runtime.logger.info(
                f"Computing match isotopes for sample {item_index + 1}/{total_samples_count}: '{sample.sample_item_name}'"
            )
            if _is_blank_sample(sample):
                # Skip matching for blank samples
                skipped_samples.append(sample.sample_item_id)
                runtime.logger.debug(
                    f"Skipped blank sample '{sample.sample_item_name}' with ID '{sample.sample_item_id}'."
                )
                continue
            # Check if m/z calibration (if exists) is verified for the sample
            if sample.mz_calibration and not sample.mz_calibration.get(
                "verified", False
            ):
                skipped_samples.append(sample.sample_item_id)
                runtime.logger.debug(
                    f"Skipped uncalibrated sample '{sample.sample_item_name}': "
                    f"m/z calibration not verified for sample file: {sample.filename}."
                )
                continue

            # Fetch unmatched target isotopes for the sample
            match_params = await default_match_params(sample.sample_item_id)
            target_isotopes_df = await fetch_sample_unmatched_target_isotopes(
                sample, match_params
            )
            if target_isotopes_df is None or target_isotopes_df.empty:
                # Only sample level aggregate to compute for this sample
                skipped_samples.append(sample.sample_item_id)
                runtime.logger.info(
                    f"No new target isotopes to compute match isotopes for the sample '{sample.sample_item_name}'."
                )
            else:
                # Fetch existing main isotope references for abundance error calculation
                unmatched_ion_ids = (
                    target_isotopes_df["target_ion_id"].unique().tolist()
                )
                existing_reference_df = await fetch_existing_main_isotope_references(
                    sample.sample_item_id, unmatched_ion_ids
                )

                # Step 3: Compute match_isotopes if the sample has passed all checks.
                match_data = await compute_and_create_sample_match_isotope_data(
                    sample,
                    target_isotopes_df,
                    existing_reference_df,
                    progress_notification,
                )
                # Track samples with matches
                if not match_data["match_isotopes"].empty:
                    computed_samples.append(sample.sample_item_id)

        except Exception as e:
            # Catch any per-sample failure (not just ApiException): a single bad
            # sample - corrupt file, unreadable metadata, a transient DB error -
            # must fail only that sample, never abort matching for the rest of
            # the batch. CancelledError is a BaseException and is not caught.
            # INFO per sample; the batch summary below warns once for the
            # whole batch.
            runtime.logger.info(
                f"Computing match isotopes for sample '{sample.sample_item_name}' "
                f"failed: {e}"
            )
            failed_samples.append(sample.sample_item_id)
            continue

        await send_progress_user_notification(progress_notification, 1.0)

    # Step 4: Aggregate higher-level matches once for the whole batch and update
    # timestamps. Aggregating per batch instead of per sample avoids re-running
    # the batch-level target queries for every sample. A failure here must not
    # discard the per-sample isotopes already computed and saved above, so it is
    # caught and reflected in the batch status rather than propagated.
    #
    # The aggregation is scoped to the affected samples: computed this run
    # (or failed - a failure after the isotope insert would leave stored rows
    # unaggregated), touched by the preceding removal, or left with
    # incomplete aggregates by an interrupted earlier run. Aggregate levels
    # are per-sample, so unaffected samples' stored aggregates are already
    # correct; when no sample is affected the aggregation is skipped
    # entirely. (One deliberate behavior change: aggregates saved via the
    # /save routes with custom match_params are no longer silently
    # canonicalized back to defaults by a no-op refresh.)
    aggregation_failed = False
    match_aggregate_result = {}
    aggregation_scope = sorted(
        set(computed_samples)
        | set(failed_samples)
        | set(removed_sample_item_ids or [])
        | set(await _incomplete_aggregate_sample_ids(sample_batch_id))
    )
    if not aggregation_scope:
        runtime.logger.info(
            f"Skipped higher-level match aggregation for sample batch "
            f"'{sample_batch_name}': no match data changed."
        )
    else:
        try:
            # Per-chunk progress: on a large batch the aggregation runs for
            # minutes after the per-sample bar reaches 100%, so it reports
            # its own phase through the same tracked process.
            aggregation_notification = UserNotification(
                process_id=process_id,
                parent_id=parent_id,
                type="match_aggregate_batch",
                status="pending",
                message=f"Aggregating matches for sample batch '{sample_batch_name}'",
                data={
                    "sample_batch_id": sample_batch_id,
                    "_room_ids": [sample_batch_id],
                    "_user_id": user_id,
                },
            )
            match_aggregate_result = await aggregate_and_create_matches(
                sample_batch_id=sample_batch_id,
                notification=aggregation_notification,
                # None = whole batch; an explicit subset skips re-aggregating
                # the untouched samples
                sample_item_ids=(
                    None
                    if len(aggregation_scope) >= total_samples_count
                    else aggregation_scope
                ),
            )
        except Exception:
            aggregation_failed = True
            match_aggregate_result = {}
            # No cleanup needed here: aggregate_and_create_matches clears the
            # scope's MatchSample rows up front and restores them chunk by
            # chunk, so any failure - including a process kill this handler
            # would never see - already leaves the completeness probe above
            # reporting incomplete for the next refresh.
            runtime.logger.exception(
                f"Higher-level match aggregation failed for sample batch "
                f"'{sample_batch_name}'"
            )
    match_aggregate_status = match_aggregate_result.get("status")
    if match_aggregate_status in ("success", "partial"):
        runtime.logger.debug(
            f"Aggregated new higher-level matches for sample batch '{sample_batch_name}'."
        )
        affected_sample_item_ids = match_aggregate_result.get("data", {}).get(
            "affected_sample_item_ids", []
        )
        if affected_sample_item_ids:
            await update_sample_modified_timestamps(
                sample_item_ids=affected_sample_item_ids
            )

    # Step 5: Determine status based on outcomes
    computed_samples_count = len(computed_samples)
    failed_samples_count = len(failed_samples)
    skipped_samples_count = len(skipped_samples)

    if failed_samples_count > 0 and computed_samples_count == 0:
        status = "failed"  # Only failures, no successes
    elif failed_samples_count > 0 and computed_samples_count > 0:
        status = "partial"  # Mixed results
    elif skipped_samples_count > 0:
        status = "skipped"
    else:
        status = "success"

    # Isotopes were computed but not fully aggregated into higher-level matches:
    # never report clean success.
    if aggregation_failed and status in ("success", "skipped"):
        status = "partial" if computed_samples_count > 0 else "failed"

    message = (
        f"Finished computing matches ({status}) for sample batch '{sample_batch.sample_batch_name}'. "
        f"{computed_samples_count} sample{'s' if computed_samples_count != 1 else ''} processed successfully, "
        f"{failed_samples_count} failed, "
        f"{skipped_samples_count} skipped due to missing calibration or no new targets."
    )
    if failed_samples_count > 0:
        # One aggregated warning per problem batch; the per-sample failures
        # above are logged at INFO.
        runtime.logger.warning(message)
    else:
        runtime.logger.debug(message)

    return {
        "status": status,
        "message": message,
        "data": {
            "computed_samples_count": computed_samples_count,
            "failed_samples_count": failed_samples_count,
            "skipped_samples_count": skipped_samples_count,
            "total_samples_count": total_samples_count,
        },
        "_notification_data": {"sample_batch_id": sample_batch_id},
    }


# -------------------------------------------------------------------
# App level
# -------------------------------------------------------------------


@api_controller()
async def match_remove_all():
    """
    Deletes all match data across match-related tables in the database.
    """
    counts = {}
    async with async_session() as session:
        # Deleting match isotopes
        result = await session.execute(delete(MatchIsotope))
        counts["match_isotopes"] = result.rowcount

        # Deleting match ions
        result = await session.execute(delete(MatchIon))
        counts["match_ions"] = result.rowcount

        # Deleting match compounds
        result = await session.execute(delete(MatchCompound))
        counts["match_compounds"] = result.rowcount

        # Deleting match collections
        result = await session.execute(delete(MatchCollection))
        counts["match_collections"] = result.rowcount

        # Deleting match samples
        result = await session.execute(delete(MatchSample))
        counts["match_samples"] = result.rowcount

        # Commit all deletions
        await session.commit()

    message = "All match data has been successfully deleted:"
    for key, value in counts.items():
        message += f" {value} {key},"

    return {"message": message}
