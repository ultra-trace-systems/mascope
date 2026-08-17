"""
The password policy: length, the breached-password blocklist, and the rules
against echoing the account's own identifiers.

The expectations live in tests/data/password_policy_cases.json because the
policy is implemented twice - here and in the frontend's src/lib/password.js -
and the two must agree on the rules, their order and their exact wording. The
frontend reads the same file.

``validate_password`` touches no database, so a UserManager built over a null
user_db is enough.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi_users.exceptions import InvalidPasswordException

from mascope_backend.api.new.users.password.service import generate_random_password
from mascope_backend.api.new.users.user_manager.common_passwords import (
    is_common_password,
    load_common_passwords,
)
from mascope_backend.api.new.users.user_manager.service import UserManager


CASES_FILE = Path(__file__).parents[3] / "data" / "password_policy_cases.json"
CASES = json.loads(CASES_FILE.read_text(encoding="utf-8"))["cases"]


@pytest.fixture
def user_manager():
    """A UserManager built for policy checks only - it never reaches the DB."""
    return UserManager(None)


async def _policy_error(user_manager, password, email, username):
    """Run the policy and return the message it refused with, or None."""
    user = SimpleNamespace(email=email, username=username)
    try:
        await user_manager.validate_password(password, user)
    except InvalidPasswordException as exception:
        return str(exception.reason)
    return None


@pytest.mark.asyncio
@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
async def test_password_policy_cases(user_manager, case):
    error = await _policy_error(
        user_manager, case["password"], case["email"], case["username"]
    )
    assert error == case["error"]


def test_the_shared_case_table_is_not_empty():
    # Guards the parametrisation: an unreadable or emptied table would make the
    # test above vacuously pass in both languages at once.
    assert len(CASES) >= 10


def test_blocklist_loads_and_is_lowercased():
    entries = load_common_passwords()
    assert len(entries) > 10_000
    assert all(entry == entry.lower() for entry in entries)
    # Every entry must be reachable: the policy rejects shorter passwords
    # before it ever consults the list, so a short entry would be dead weight.
    assert all(len(entry) >= UserManager.MIN_PASSWORD_LENGTH for entry in entries)
    # '#' starts a comment line in the data file, so an entry beginning with one
    # could never match. The generator drops them.
    assert not any(entry.startswith("#") for entry in entries)


@pytest.mark.asyncio
async def test_generated_reset_passwords_always_pass_the_policy(user_manager):
    # An administrator reset that produced a password the policy then refuses
    # would hand the user an unusable credential. The identifiers here are
    # chosen to trip the containment rules if the minimum-identifier-length
    # guard ever regresses.
    for _ in range(1000):
        password = generate_random_password()
        error = await _policy_error(user_manager, password, "ab@example.com", "abc")
        assert error is None, f"{password!r} was refused: {error}"
        assert not is_common_password(password)
