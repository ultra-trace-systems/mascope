"""Socket.IO notification service."""

from copy import deepcopy
from typing import Any

from mascope_backend.runtime import runtime
from mascope_backend.socket import sio
from mascope_backend.socket.notifications.schemas import UserNotification
from mascope_backend.socket.storage import room_tracker


# Share of a batch match refresh's single progress bar filled by the
# per-sample compute phase; the chunked batch aggregation that follows fills
# the remaining share of the same bar.
MATCH_COMPUTE_PROGRESS_SHARE = 0.7


async def emit_user_notification(
    notification: UserNotification,
    room_id: str | None = None,
    user_id: int | None = None,
) -> None:
    """
    Emit notification with flexible routing logic.

    Routing behavior:
        1. Only user_id → emit to user's personal room (user-{id})
        2. Only room_id → emit to room (all subscribers see it)
        3. Both provided:
           - User in room → emit ONLY to room_id
           - User NOT in room → emit to BOTH room_id AND user-{id}

    Case 3: Active user who triggered request receives notification
    even after navigating away, while other subscribers still get updates.

    :param notification: Notification to send
    :type notification: UserNotification
    :param room_id: Target room (resource ID, instrument, etc.)
    :type room_id: str | None
    :param user_id: Target user ID
    :type user_id: int | None
    :raises ValueError: If neither room_id nor user_id provided
    """
    if not room_id and not user_id:
        raise ValueError("At least one of room_id or user_id must be provided")

    notification_dict = notification.model_dump(exclude_none=True)

    # Case 1: Only user_id → emit to user's personal room
    if user_id and not room_id:
        user_room = f"user-{user_id}"
        runtime.logger.debug(f"Notification: emitting to user room '{user_room}'")
        await sio.emit(
            "user_notification", notification_dict, room=user_room, namespace="/"
        )
        return

    # Case 2: Only room_id → emit to room (all subscribers)
    if room_id and not user_id:
        runtime.logger.debug(f"Notification: emitting to room '{room_id}'")
        await sio.emit(
            "user_notification", notification_dict, room=room_id, namespace="/"
        )
        return

    # Case 3: Both provided → smart dual emission
    user_in_room = await room_tracker.is_in_room(user_id, room_id)

    if user_in_room:
        # User still viewing → emit only to room (user receives it there)
        runtime.logger.debug(
            f"Notification: emitting to room '{room_id}' (user {user_id} present)"
        )
        await sio.emit(
            "user_notification", notification_dict, room=room_id, namespace="/"
        )
    else:
        # User navigated away → emit to BOTH room and user
        runtime.logger.debug(
            f"Notification: dual emit to room '{room_id}' + user '{user_id}' "
            f"(user left room)"
        )
        await sio.emit(
            "user_notification", notification_dict, room=room_id, namespace="/"
        )
        await sio.emit(
            "user_notification",
            notification_dict,
            room=f"user-{user_id}",
            namespace="/",
        )


async def send_progress_user_notification(
    notification: UserNotification, increment: float = None
):
    """
    Send progress notifications with dynamic progress calculation.

    Extracts internal metadata from notification.data, calculates progress,
    and emits to all specified rooms with optional smart routing.

    Internal metadata keys (removed before emission):
        _user_id: User ID for smart routing
        _room_ids: List of room IDs to emit to
        _total_samples: Total items for progress calculation
        _item_index: Current item index
        _batch_weight: This batch's share of the progress bar
        _batch_base: Share of the bar already filled by earlier batches

    :param notification: UserNotification with progress data
    :param increment: Progress increment value
    """
    # Create a deep copy to avoid modifying original
    notification_copy = deepcopy(notification)

    # Extract internal metadata
    user_id = notification_copy.data.pop("_user_id", None)
    room_ids = notification_copy.data.pop("_room_ids", [])
    total_samples = notification_copy.data.pop("_total_samples", None)
    item_index = notification_copy.data.pop("_item_index", None)
    batch_weight = notification_copy.data.pop("_batch_weight", None)
    batch_base = notification_copy.data.pop("_batch_base", None)

    # Clear any remaining internal keys (start with underscore)
    keys_to_remove = [
        key for key in notification_copy.data.keys() if key.startswith("_")
    ]
    for key in keys_to_remove:
        notification_copy.data.pop(key, None)

    # If no other data remains, set data to None
    if not notification_copy.data:
        notification_copy.data = None

    # Calculate progress based on the notification type and provided increment
    if (
        notification_copy.type
        in [
            "match_compute_sample",
            "calibration_mz_fit",
            "calibration_mz_apply",
            "calibration_mz_calibrate_sample",
            "import_sample_items",
            "process_sample_item",
            "copy_sample_items",
        ]
        and increment
    ):
        notification_copy.progress = increment * 100
    # A batch match refresh is one progress bar across two phases: per-sample
    # compute fills the first share, the chunked batch aggregation that
    # follows fills the rest (the message names the current phase).
    if notification_copy.type == "match_compute_batch":
        if total_samples is not None and item_index is not None:
            notification_copy.progress = (
                ((item_index + increment) / total_samples)
                * MATCH_COMPUTE_PROGRESS_SHARE
                * 100
            )
            notification_copy.message = f"Computing sample batch matches, processing sample {item_index + 1}/{total_samples}"

    if notification_copy.type == "match_aggregate_batch":
        # Items are sample chunks, not samples
        if total_samples is not None and item_index is not None:
            notification_copy.progress = (
                MATCH_COMPUTE_PROGRESS_SHARE
                + ((item_index + increment) / total_samples)
                * (1 - MATCH_COMPUTE_PROGRESS_SHARE)
            ) * 100
            notification_copy.message = (
                f"Aggregating batch matches, part {item_index + 1}/{total_samples}"
            )

    if notification_copy.type == "rematch_batches":
        # One bar across the whole run: the batches before this one have
        # filled `batch_base` of it, and this batch fills its own share as it
        # goes. Weighting per batch alone would restart the bar every time.
        notification_copy.progress = (batch_base + increment * batch_weight) * 100

    if notification_copy.type == "sample_batch_export_peaks":
        if total_samples is not None and item_index is not None:
            notification_copy.progress = (
                (item_index + increment) / total_samples
            ) * 100
            notification_copy.message = f"Exporting peak data, processing sample {item_index + 1}/{total_samples}"

    if notification_copy.type == "copy_sample_batch":
        if total_samples is not None and item_index is not None:
            notification_copy.progress = (
                (item_index + increment) / total_samples
            ) * 100

            notification_copy.message = (
                f"Copying sample {item_index + 1}/{total_samples} to new batch."
            )

    # Single-sample peak assignment drives its own progress bar. When nested
    # under a batch assignment (parent_id set) the batch-level bar tracks
    # progress instead, so the per-sample stream stays quiet and does not stack
    # a jumble of overlapping bars.
    if (
        notification_copy.type == "assign_sample_peaks"
        and increment
        and notification_copy.parent_id is None
    ):
        notification_copy.progress = increment * 100

    if notification_copy.type == "assign_sample_batch_peaks":
        if total_samples is not None and item_index is not None:
            # increment is None on the pre-sample tick and 1.0 once the sample is
            # assigned, so the bar steps from item_index/N to (item_index + 1)/N.
            inc = increment if increment is not None else 0.0
            notification_copy.progress = ((item_index + inc) / total_samples) * 100
            notification_copy.message = (
                f"Assigning peaks, processing sample {item_index + 1}/{total_samples}"
            )

    # An assignment copy fans out over the batch's other samples, so it fills one
    # bar the same way a batch assignment does. Unlike that one it reports
    # WITHIN a destination too - reading its peaks, re-scoring the copied
    # formulas against them, publishing the run - because a copy of a two-sample
    # batch would otherwise sit at 0% through the slowest part and then finish.
    # The message is left as the caller wrote it: it names the source sample and
    # the phase, which is more than this function knows.
    if notification_copy.type == "copy_assignments_to_batch":
        if total_samples is not None and item_index is not None:
            inc = increment if increment is not None else 0.0
            notification_copy.progress = ((item_index + inc) / total_samples) * 100

    # A batch-peak backfill folds the batch's samples one at a time, and the
    # message it arrives with already names which one - so only the bar is
    # computed here. Same two ticks per sample as the batch assignment above:
    # increment is None before the fold and 1.0 after it, stepping the bar from
    # item_index/N to (item_index + 1)/N. Guarded on a non-zero N rather than
    # merely a present one: a batch with no samples emits nothing today, and
    # that is not a reason for the arithmetic here to be divisible by it.
    if notification_copy.type == "compute_batch_peaks":
        if total_samples and item_index is not None:
            inc = increment if increment is not None else 0.0
            notification_copy.progress = ((item_index + inc) / total_samples) * 100

    # Emit to all specified rooms with optional smart routing
    for room_id in room_ids:
        await emit_user_notification(
            notification_copy, room_id=room_id, user_id=user_id
        )

    # Fallback for direct user notifications if no rooms specified
    if not room_ids and user_id is not None:
        await emit_user_notification(notification_copy, user_id=user_id)


async def handle_notifications(
    rooms: list[str],
    notification: UserNotification,
    kwargs: dict[str, Any],
    result: dict[str, Any] | None,
) -> None:
    """
    Emit notifications for background tasks with flexible routing.

    Extracts room IDs and optional user_id from controller kwargs/result,
    then emits with appropriate routing strategy.

    Extraction priority:
        room_id: kwargs[key] → result[key] → result['data'][key] → result['_notification_data'][key]
        user_id: kwargs['user_id'] → result['_notification_data']['user_id']

    When neither resolves there is nobody to send to. For an ordinary
    notification that is an actionable fault - a message meant for a user was
    lost - and is logged at WARNING. For a ``silent`` one it is not: that
    packet only ends a progress bar in a browser, and with no audience there
    is no bar, so it is dropped at DEBUG.

    :param rooms: List of room keys (e.g., ["sample_batch_id", "user_id"])
    :type rooms: list[str]
    :param notification: UserNotification instance to be emitted
    :type notification: UserNotification
    :param kwargs:  (may contain room values and user_id)
    :type kwargs: dict[str, Any]
    :param result: Controller result (may contain room values in data/_notification_data)
    :type result: dict[str, Any] | None
    """
    user_id: int | None = kwargs.get("user_id")
    if not user_id and result and isinstance(result, dict):
        if notification_data := result.get("_notification_data"):
            if isinstance(notification_data, dict):
                user_id = notification_data.get("user_id")

    for room_key in rooms:
        room_id = kwargs.get(room_key)

        if not room_id and result and isinstance(result, dict):
            # Try direct key
            room_id = result.get(room_key)

            # Try nested in 'data'
            if not room_id and (data := result.get("data")):
                if isinstance(data, dict):
                    room_id = data.get(room_key)

            # Try nested in '_notification_data'
            if not room_id and (notification_data := result.get("_notification_data")):
                if isinstance(notification_data, dict):
                    room_id = notification_data.get(room_key)

        # Special case: if room_key IS "user_id", emit directly to user
        if room_key == "user_id" or not room_id:
            if user_id is not None:
                await emit_user_notification(notification, user_id=user_id)
            elif notification.silent:
                # A silent packet carries nothing to read: it exists only to
                # end the progress bar the process opened in a browser. With
                # no room and no user there is no browser and no bar, so
                # nothing is lost by not sending it - and nothing is wrong,
                # which is why this is not a warning. A pipeline that runs
                # without a user (tests, background reprocessing) suppresses
                # one such packet per nested warning per retry - up to
                # fourteen for a single sample that will not calibrate - and
                # every WARNING record is exported to error monitoring
                # (mascope_runtime.logging._SENTRY_LEVELS), which would turn
                # a routine no-op into fourteen monitoring events.
                runtime.logger.debug(
                    f"Skipping silent notification with no audience: no room_id for "
                    f"'{room_key}', no user_id available. "
                    f"Notification type: {notification.type}, status: {notification.status}"
                )
            else:
                # Not silent: a message the user was meant to read was lost.
                # That is an actionable operator signal and stays at WARNING.
                runtime.logger.warning(
                    f"Cannot emit notification: no room_id for '{room_key}', no user_id available. "
                    f"Notification type: {notification.type}, status: {notification.status}"
                )
        else:
            # Normal case: room_id with optional user_id for smart routing
            await emit_user_notification(notification, room_id=room_id, user_id=user_id)
