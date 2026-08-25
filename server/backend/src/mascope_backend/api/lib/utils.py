"""API utility functions."""

import re
from typing import Any

import pandas as pd

from mascope_backend.api.new.peak_assignments.config import (
    peak_assignment_enabled as _peak_assignment_enabled,
)
from mascope_backend.runtime import runtime
from mascope_backend.socket.records import emit_record_reload


def beautify_func_name(func_name: str, max_words: int = None) -> str:
    """
    Beautify a function name by replacing underscores with spaces.
    Optionally, limit the number of words used in the beautified name.

    :param func_name: The function name to beautify.
    :type func_name: str
    :param max_words: Maximum number of words to include in the beautified name.
    :type max_words: int, optional
    :return: The beautified function name.
    :rtype: str
    """
    if not isinstance(func_name, str):
        raise ValueError("Function name must be a string.")

    # Replace underscores with spaces and capitalize the first letter
    words = func_name.replace("_", " ").split()
    beautified_name = " ".join(words[:max_words]) if max_words else " ".join(words)

    return beautified_name


def generate_copy_name(original_name):
    """Generate a copy name for a given original name.

    :param original_name: The original name to copy.
    :type original_name: str
    :return: A new copy name based on the original name.
    :rtype: str
    """
    if not original_name:
        return None

    # Clean the name by collapsing multiple spaces into one and trimming
    cleaned_name = " ".join(original_name.split()).strip()

    # Match the pattern for names like 'name Copy(1)', 'name Copy', etc.
    match = re.match(r"(.*\sCopy)(?:\((\d+)\))?$", cleaned_name)

    if match:
        base_name = match.group(1)
        copy_count = match.group(2)

        if copy_count:
            return f"{base_name}({int(copy_count) + 1})"
        else:
            return f"{base_name}(1)"
    else:
        return f"{cleaned_name} Copy"


#  --- Utilities for handling API decorator reload events ---
def resolve_rooms(
    room_key: str,
    kwargs: dict[str, Any],
    result: dict[str, Any] | None,
) -> list[str]:
    """
    Extract room IDs from controller kwargs or result.

    Search order:
    1. kwargs[room_key]
    2. result['data'][room_key]
    3. result['_notification_data'][room_key]

    :param room_key: Key to look up room IDs
    :param kwargs: Controller function kwargs
    :param result: Controller function result
    :return: List of room IDs (empty if not found)
    """
    room_ids = []

    # Check kwargs first
    if room_key in kwargs:
        room_ids = kwargs[room_key]
    elif result:
        # Check result['data']
        if "data" in result and isinstance(result["data"], dict):
            room_ids = result.get("data", {}).get(room_key)
        # Check result['_notification_data']
        if not room_ids and "_notification_data" in result:
            room_ids = result.get("_notification_data", {}).get(room_key)

    # Normalize to list
    if not isinstance(room_ids, list):
        room_ids = [room_ids] if room_ids else []

    # Filter out None values
    return [room_id for room_id in room_ids if room_id is not None]


async def handle_reloads(
    context: str,
    reload_events: list[tuple[str, str]],
    kwargs: dict[str, Any],
    result: dict[str, Any] | None,
) -> None:
    """
    Emit Socket.IO reload events to specified rooms.

    Processes decorator-defined reload events by:
    - resolving room IDs from kwargs/result
    - emitting reload events via emit_record_reload

    :param context: Context string for logging
    :param reload_events: List of (event_name, room_key) tuples
    :param kwargs: Controller function kwargs
    :param result: Controller function result
    """
    for record_type, room_key in reload_events:
        if record_type == "peak_assignment" and not _peak_assignment_enabled():
            # The reload lists are static decorator args, so the ingest
            # controllers declare this reload unconditionally - but with the
            # feature off no assignment can be written, so the event would be
            # pure noise on every ingest for a deployment that never opted in.
            continue
        rooms = resolve_rooms(room_key, kwargs, result)
        if not rooms:
            # INFO: fires on every affected API call while a route's reload
            # wiring is incomplete; triage via the log files
            runtime.logger.info(
                f"{context}: No room IDs found for '{record_type}_reload' with key '{room_key}'"
            )
            continue

        try:
            await emit_record_reload(record_type=record_type, room=rooms)
        except Exception:
            runtime.logger.exception(
                f"{context}: Failed to emit '{record_type}_reload'"
            )


def strings_json_safe(df: pd.DataFrame) -> pd.DataFrame:
    """Turn missing values in string columns into None before serialization.

    pandas 3 infers its Arrow-backed ``str`` dtype for text columns, and that
    dtype's only missing sentinel is NaN - so a SQL NULL that used to survive
    as ``None`` through an object column now arrives as a float NaN. NaN is not
    JSON and starlette refuses to serialize it (``allow_nan=False``), so it has
    to leave the frame as None or the whole response 500s.

    Only columns pandas actually typed ``str`` are touched, which leaves object
    columns carrying lists or dicts alone. Under pandas 2 nothing has that
    dtype and this is a no-op, which is correct - there ``None`` survives on
    its own.
    """
    df = df.copy()
    for column in df.columns:
        if str(df[column].dtype) == "str" and df[column].isna().any():
            df[column] = df[column].astype(object).where(df[column].notna(), None)
    return df
