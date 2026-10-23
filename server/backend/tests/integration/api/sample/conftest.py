"""
Fixtures for the sample integration tests.
"""

import os
import time

import pytest


@pytest.fixture
def server_zone():
    """Set the process's local time zone, which the driver reads a naive
    bound in, and put it back afterwards.

    Restores ``TZ`` alone: ``monkeypatch.undo()`` would also undo the
    session maker that ``bind_db`` patched, before the other fixtures tear
    down."""
    if not hasattr(time, "tzset"):  # Windows: the zone cannot be steered.
        pytest.skip("TZ cannot be applied without time.tzset()")
    before = os.environ.get("TZ")

    def set_zone(name: str) -> None:
        os.environ["TZ"] = name
        time.tzset()

    yield set_zone
    if before is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = before
    time.tzset()
