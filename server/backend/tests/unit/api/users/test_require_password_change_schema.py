"""
Tests: what counts as acknowledgement for the deployment-wide password change.

The field exists so the endpoint cannot be fired by an empty or accidental
request. It is ``Literal[True]`` rather than ``bool``, which matters: a plain
``bool`` field accepts ``"true"``, ``"yes"`` and ``"on"``, so a client sending a
string would have triggered a fleet-wide action.

Pinned here rather than over HTTP because it is a property of the schema, and
because that route's rate-limit budget lives in Redis on a one-hour window that
outlives a test run - spending it on validation cases makes the outcome depend
on what ran in the previous hour.
"""

import pytest
from pydantic import ValidationError

from mascope_backend.api.new.users.owner.schemas import RequirePasswordChange


@pytest.mark.parametrize("refused", ["true", "yes", "on", "false", 0, 2, None, ""])
def test_only_a_real_acknowledgement_is_accepted(refused):
    with pytest.raises(ValidationError):
        RequirePasswordChange(confirm=refused)


def test_missing_acknowledgement_is_refused():
    with pytest.raises(ValidationError):
        RequirePasswordChange()


def test_an_acknowledgement_is_accepted():
    """Positive control: without it, a schema that refused everything would pass."""
    assert RequirePasswordChange(confirm=True).confirm is True


def test_json_one_is_still_accepted():
    """
    The documented boundary, pinned so it is a decision rather than a surprise.

    A literal schema cannot carry ``strict`` - pydantic raises at schema build
    time - so refusing ``1`` would mean ``StrictBool`` plus a validator again.
    Both ``1`` and ``true`` are deliberate values, and the case this field
    guards is the empty or accidental request.
    """
    assert RequirePasswordChange(confirm=1).confirm is True
