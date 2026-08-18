"""Tests: accounts are created with a server-generated hand-over password.

An administrator no longer invents one. The properties worth holding are that
omitting it is allowed where someone else is being handed the account, that it
is still required where the holder chooses their own, and that what gets
generated would pass the policy the holder's replacement has to pass.
"""

from types import SimpleNamespace

import pytest

from mascope_backend.api.new.users.first_owner.schemas import FirstOwnerCreate
from mascope_backend.api.new.users.password.generate import generate_random_password
from mascope_backend.api.new.users.schemas import UserCreate
from mascope_backend.api.new.users.user_manager.service import UserManager
from mascope_backend.roles import ROLE_ACCESS_LEVELS


def _create(**overrides):
    values = {
        "email": "someone@example.org",
        "username": "someone",
        "role_id": ROLE_ACCESS_LEVELS["editor"],
    }
    values.update(overrides)
    return UserCreate(**values)


def test_creating_an_account_without_a_password_is_allowed():
    # The step this replaces: an administrator inventing a password the holder
    # replaces at first sign-in anyway.
    assert _create().password is None


def test_a_supplied_password_is_still_accepted():
    # Kept working for API callers that pass one; the UI no longer does.
    assert _create(password="a-supplied-password").password == "a-supplied-password"


def test_the_first_owner_must_still_choose_a_password():
    # Nobody is handing that account over, so there is nothing to generate, and
    # the optional field on the parent schema must not relax it. Asserted on the
    # field rather than by constructing one: the server-secret validator runs
    # first and would mask a missing password with its own refusal.
    assert FirstOwnerCreate.model_fields["password"].is_required()
    assert not UserCreate.model_fields["password"].is_required()


def test_generated_passwords_are_unique():
    assert len({generate_random_password() for _ in range(50)}) == 50


@pytest.mark.asyncio
async def test_a_generated_password_passes_the_real_policy():
    # It is handed over to be replaced, but it is a live credential until then,
    # and it must survive the same validation the replacement does. Run against
    # the policy itself rather than a restatement of its rules, so tightening
    # the policy without adjusting the generator fails here.
    manager = UserManager(None)
    account = SimpleNamespace(email="someone@example.org", username="someone")
    for _ in range(25):
        await manager.validate_password(generate_random_password(), account)
