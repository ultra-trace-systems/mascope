"""Datetime helpers shared by the API's controllers."""

from datetime import datetime, timezone


def as_utc(moment: datetime) -> datetime:
    """A bound on a ``*_utc`` column given with no offset is UTC, like the column.

    Left naive, the driver would read it in the server's local time zone.
    """
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)
