"""
Where a composition match search's result waits for the browser that asked.

``match_compositions_by_mz`` runs as a background task and reports its end in a
Socket.IO ``user_notification``. That notification used to carry the whole
result: every candidate with its matched isotope pattern, about 14 MB for a peak
with a couple of thousand candidates. Behind ``AsyncRedisManager`` every emit is
published to every backend process through Redis pub/sub, and Redis disconnects
a subscriber whose unread output passes its pub/sub buffer limit - so each such
search knocked the backend's Socket.IO subscribers off Redis, and whatever was
in flight in the gap was lost, the search result often among it.

So the result is kept here instead, and the notification only says that it is
ready. The pane fetches it over HTTP (``GET /api/cheminfo/mz/match/result/
{process_id}``): one ordinary response on one connection, with no copy per
process and no pub/sub buffer to overrun.

A result is stored under the user who ran the search and the task's process id,
and expires after ``MATCH_RESULT_TTL_SECONDS``. Another account asking for the
same process id finds nothing, the same answer an expired result gives, which
keeps the route from confirming that someone else's search exists.
"""

import json

from mascope_backend.api.new.cheminfo.config import cheminfo_config
from mascope_backend.json_safe import non_finite_to_none
from mascope_backend.socket.storage import redis_storage_client


def _key(user_id: int, process_id: str) -> str:
    return f"mascope:cheminfo:match:{user_id}:{process_id}"


def _redis():
    """The shared async Redis client (separate hook for tests)."""
    return redis_storage_client.client


async def save_match_result(user_id: int, process_id: str, result: dict) -> None:
    """Keep a finished search's result for the user who ran it.

    Non-finite floats are stored as null - an isotope the sample has no peak for
    has a NaN m/z error - which is how the Socket.IO codec sent them when the
    result still travelled in the notification, and what lets the route render
    it as strict JSON.

    :param user_id: The account that ran the search.
    :type user_id: int
    :param process_id: The search task's process id, which its completion
        notification carries.
    :type process_id: str
    :param result: The result as the pane reads it.
    :type result: dict
    """
    await _redis().set(
        _key(user_id, process_id),
        json.dumps(non_finite_to_none(result), allow_nan=False),
        ex=cheminfo_config.MATCH_RESULT_TTL_SECONDS,
    )


async def load_match_result(user_id: int, process_id: str) -> dict | None:
    """A finished search's result, if this user ran it and it has not expired.

    :param user_id: The account asking.
    :type user_id: int
    :param process_id: The search task's process id.
    :type process_id: str
    :return: The result as it was saved, or None when this user has none under
        this process id: it expired, another account ran it, or no search did.
    :rtype: dict | None
    """
    stored = await _redis().get(_key(user_id, process_id))
    return json.loads(stored) if stored is not None else None
