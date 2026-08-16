"""
Integration tests for the forced password change.

An account that owes a password change still authenticates, but the API is
closed to it until it stores a new one. Two routes stay open - the profile
read the frontend uses to discover the requirement, and the credentials route
that clears it - and closing either of them strands the user: the app treats a
failed profile read as "not signed in", which sends them to the sign-in screen,
where signing in puts them straight back.

The gate is scoped to the browser session (the auth cookie), so service bearer
tokens keep working; their strength does not depend on the account password and
their holders cannot render a password screen.

Every test here uses a throwaway user. The shared ``test_users`` fixture is
session-scoped, so requiring a password change on one of those would leak into
unrelated suites.
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from mascope_backend.api.new.auth.config import auth_settings
from mascope_backend.api.new.auth.exceptions import PASSWORD_CHANGE_REQUIRED_CODE
from mascope_backend.app.fast import fast
from mascope_backend.db import User


#: Long enough for the policy, not in the blocklist, and containing neither of
#: the throwaway account's identifiers.
COMPLIANT_PASSWORD = "sixteen tonnes of quartz"
ORIGINAL_PASSWORD = "nine bushels of slate"


@pytest_asyncio.fixture
async def flagged_user(async_session_factory, roles):
    """A throwaway guest account that owes a password change."""
    from fastapi_users.password import PasswordHelper

    async with async_session_factory() as session:
        user = User(
            email="pwgate@test.com",
            username="pwgate_user",
            hashed_password=PasswordHelper().hash(ORIGINAL_PASSWORD),
            is_active=True,
            is_verified=False,
            role_id=roles["guest"].role_id,
            must_change_password=True,
            password_change_reason="policy",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

    yield user

    async with async_session_factory() as session:
        stored = await session.get(User, user.id)
        if stored is not None:
            await session.delete(stored)
            await session.commit()


@pytest_asyncio.fixture
async def flagged_client(flagged_user, create_jwt_auth_token):
    """AsyncClient carrying the flagged account's auth cookie."""
    token = create_jwt_auth_token(flagged_user)
    async with AsyncClient(
        transport=ASGITransport(app=fast),
        base_url="http://test",
        cookies={auth_settings.COOKIE_NAME: token},
    ) as client:
        yield client


def _gate_code(response):
    """The machine-readable code on an error response, if any."""
    return (response.json().get("detail") or {}).get("code")


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/workspaces", "/api/samples", "/api/users"])
async def test_ordinary_routes_are_refused_with_the_gate_code(flagged_client, path):
    resp = await flagged_client.get(path)
    assert resp.status_code == 403, resp.text
    assert _gate_code(resp) == PASSWORD_CHANGE_REQUIRED_CODE


@pytest.mark.asyncio
async def test_an_ordinary_forbidden_carries_no_gate_code(guest_client, test_users):
    # A plain role refusal shares the 403 status, so the code is the only thing
    # that distinguishes the two for the client.
    resp = await guest_client.get("/api/roles/owner")
    assert resp.status_code == 403
    assert _gate_code(resp) != PASSWORD_CHANGE_REQUIRED_CODE


@pytest.mark.asyncio
async def test_profile_read_still_answers_and_reports_the_requirement(flagged_client):
    # The frontend treats anything but 200/401 here as "not signed in", so a
    # gated profile read would produce a silent sign-in loop.
    resp = await flagged_client.get("/api/users/me")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["must_change_password"] is True
    assert data["password_change_reason"] == "policy"


@pytest.mark.asyncio
async def test_updating_the_username_is_refused(flagged_client):
    # Only the credentials route is exempt; the rest of the profile waits.
    resp = await flagged_client.patch("/api/users/me", json={"username": "renamed"})
    assert resp.status_code == 403
    assert _gate_code(resp) == PASSWORD_CHANGE_REQUIRED_CODE


@pytest.mark.asyncio
async def test_changing_the_password_clears_the_requirement(
    flagged_client, flagged_user, async_session_factory
):
    resp = await flagged_client.patch(
        "/api/users/me/creds",
        json={
            "current_password": ORIGINAL_PASSWORD,
            "new_password": COMPLIANT_PASSWORD,
            "verify_new_password": COMPLIANT_PASSWORD,
        },
    )
    assert resp.status_code == 200, resp.text

    async with async_session_factory() as session:
        stored = await session.get(User, flagged_user.id)
        assert stored.must_change_password is False
        assert stored.password_change_reason is None
        assert stored.password_changed_at is not None

    # And the app is open again.
    assert (await flagged_client.get("/api/workspaces")).status_code == 200


@pytest.mark.asyncio
async def test_a_refused_password_change_leaves_the_requirement_in_place(
    flagged_client, flagged_user, async_session_factory
):
    resp = await flagged_client.patch(
        "/api/users/me/creds",
        json={
            "current_password": ORIGINAL_PASSWORD,
            "new_password": "qwerty123456",
            "verify_new_password": "qwerty123456",
        },
    )
    assert resp.status_code == 400, resp.text

    async with async_session_factory() as session:
        stored = await session.get(User, flagged_user.id)
        assert stored.must_change_password is True


@pytest.mark.asyncio
async def test_the_wrong_current_password_does_not_clear_the_requirement(
    flagged_client, flagged_user, async_session_factory
):
    resp = await flagged_client.patch(
        "/api/users/me/creds",
        json={
            "current_password": "not the right one",
            "new_password": COMPLIANT_PASSWORD,
            "verify_new_password": COMPLIANT_PASSWORD,
        },
    )
    assert resp.status_code >= 400

    async with async_session_factory() as session:
        stored = await session.get(User, flagged_user.id)
        assert stored.must_change_password is True


@pytest.mark.asyncio
async def test_many_accounts_behind_one_address_can_all_comply(
    async_session_factory, roles, create_jwt_auth_token
):
    # The per-address budget on the credentials route used to be 10/hour. After
    # a deployment-wide requirement this route is the only way back into the
    # app, so a shared office address would have locked everyone out on the
    # eleventh attempt. The real budget is per account. Counts for real against
    # this package's in-memory limiter backend; in the default ASGI setup the
    # limiter fails open and this test would pass vacuously.
    from fastapi_users.password import PasswordHelper

    created = []
    async with async_session_factory() as session:
        for index in range(12):
            user = User(
                email=f"pwgate{index}@test.com",
                username=f"pwgate_user_{index}",
                hashed_password=PasswordHelper().hash(ORIGINAL_PASSWORD),
                is_active=True,
                is_verified=False,
                role_id=roles["guest"].role_id,
                must_change_password=True,
                password_change_reason="policy",
            )
            session.add(user)
            created.append(user)
        await session.commit()
        for user in created:
            await session.refresh(user)

    try:
        for user in created:
            async with AsyncClient(
                transport=ASGITransport(app=fast),
                base_url="http://test",
                cookies={auth_settings.COOKIE_NAME: create_jwt_auth_token(user)},
            ) as client:
                resp = await client.patch(
                    "/api/users/me/creds",
                    json={
                        "current_password": ORIGINAL_PASSWORD,
                        "new_password": COMPLIANT_PASSWORD,
                        "verify_new_password": COMPLIANT_PASSWORD,
                    },
                )
                assert resp.status_code == 200, f"{user.username}: {resp.text}"
    finally:
        async with async_session_factory() as session:
            for user in created:
                stored = await session.get(User, user.id)
                if stored is not None:
                    await session.delete(stored)
            await session.commit()


@pytest.mark.asyncio
async def test_wrong_password_guesses_exhaust_the_account_budget(flagged_client):
    # The per-account limiter is the oracle protection: 20 consecutive failed
    # current-password verifications refuse the 21st attempt outright. Runs
    # against the in-memory limiter backend from this package's conftest - in
    # the default ASGI setup the limiter fails open and asserts nothing.
    body = {
        "current_password": "not the right one",
        "new_password": COMPLIANT_PASSWORD,
        "verify_new_password": COMPLIANT_PASSWORD,
    }
    for attempt in range(20):
        resp = await flagged_client.patch("/api/users/me/creds", json=body)
        assert resp.status_code != 429, f"attempt {attempt}: {resp.text}"
    resp = await flagged_client.patch("/api/users/me/creds", json=body)
    # No Retry-After assertion: the api_route exception pipeline rebuilds the
    # response and drops custom headers from in-handler HTTPExceptions - a
    # pre-existing, app-wide behaviour shared by every in-body limiter.
    assert resp.status_code == 429, resp.text


@pytest.mark.asyncio
async def test_policy_rejections_do_not_burn_the_account_budget(flagged_client):
    # A refusal of the NEW password is not an oracle guess - the browser's
    # blocklist is only the head of the server's, so a user can run into
    # server-only rejections repeatedly with a green client-side checklist.
    # Proving the current password resets the budget, so more than 20 such
    # attempts stay possible; anything else strands them at the mandatory
    # password screen.
    body = {
        "current_password": ORIGINAL_PASSWORD,
        "new_password": "qwerty123456",
        "verify_new_password": "qwerty123456",
    }
    for attempt in range(25):
        resp = await flagged_client.patch("/api/users/me/creds", json=body)
        assert resp.status_code == 400, f"attempt {attempt}: {resp.text}"


@pytest.mark.asyncio
async def test_a_successful_change_leaves_no_burnt_budget(
    flagged_client, live_rate_limiter
):
    # The per-account counter is cleared and the per-IP backstop increment is
    # refunded on success, so a whole site complying with a forced change in
    # the same hour spends nothing but its failures. Asserted on the limiter
    # backend directly: exercising the 100-request backstop end to end would
    # need a hundred accounts.
    resp = await flagged_client.patch(
        "/api/users/me/creds",
        json={
            "current_password": ORIGINAL_PASSWORD,
            "new_password": COMPLIANT_PASSWORD,
            "verify_new_password": COMPLIANT_PASSWORD,
        },
    )
    assert resp.status_code == 200, resp.text

    burnt = {
        key: count
        for key, count in live_rate_limiter.values.items()
        if key.startswith("mascope:ratelimit:creds-change") and count > 0
    }
    assert burnt == {}


@pytest.mark.asyncio
async def test_targeted_clear_matches_emails_case_insensitively(
    async_session_factory, roles
):
    # Login accepts any casing of an address (FastAPI Users lowercases both
    # sides), so the casing an operator knows an account by is not necessarily
    # the casing it was stored with. The targeted undo must not silently
    # release nobody over that mismatch.
    from fastapi_users.password import PasswordHelper

    from mascope_backend.db.admin.user.require_password_change import (
        clear_password_change_requirement,
    )

    async with async_session_factory() as session:
        user = User(
            email="PwGate.Case@Test.com",
            username="pwgate_case_user",
            hashed_password=PasswordHelper().hash(ORIGINAL_PASSWORD),
            is_active=True,
            is_verified=False,
            role_id=roles["guest"].role_id,
            must_change_password=True,
            password_change_reason="policy",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

    try:
        result = await clear_password_change_requirement(["pwgate.case@test.com"])
        assert result["data"]["cleared_count"] == 1
        assert result["data"]["user_ids"] == [user.id]

        async with async_session_factory() as session:
            stored = await session.get(User, user.id)
            assert stored.must_change_password is False
    finally:
        async with async_session_factory() as session:
            stored = await session.get(User, user.id)
            if stored is not None:
                await session.delete(stored)
                await session.commit()


@pytest.mark.asyncio
async def test_anonymous_requests_are_still_unauthorized(flagged_user):
    # The gate must not turn a missing session into a 403; that would tell an
    # anonymous caller that the account exists.
    async with AsyncClient(
        transport=ASGITransport(app=fast), base_url="http://test"
    ) as client:
        resp = await client.get("/api/workspaces")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_owner_requires_a_change_for_every_account(
    owner_client, test_users, async_session_factory
):
    resp = await owner_client.post(
        "/api/users/owner/require-password-change", json={"confirm": True}
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["flagged_count"] == data["total_users"]

    async with async_session_factory() as session:
        rows = (await session.execute(select(User))).scalars().all()
        # Nobody is exempt, the acting owner included.
        assert all(user.must_change_password for user in rows)
        assert all(user.password_change_reason == "policy" for user in rows)

    # Idempotent: a second call reports nothing new to do. The owner is now
    # behind the gate, so this goes through the helper rather than the route.
    from mascope_backend.db.admin.user.require_password_change import (
        require_password_change_for_all_users,
    )

    again = await require_password_change_for_all_users()
    assert again["data"]["flagged_count"] == 0
    # The autouse fixture in this package's conftest releases every account.


@pytest.mark.asyncio
async def test_the_owner_route_refuses_an_unacknowledged_call(owner_client):
    assert (
        await owner_client.post("/api/users/owner/require-password-change")
    ).status_code == 422
    assert (
        await owner_client.post(
            "/api/users/owner/require-password-change", json={"confirm": False}
        )
    ).status_code == 422
    # Acknowledgement is Literal[True], which narrows pydantic's bool coercion:
    # a plain `bool` field accepts every string below as true, so a client that
    # sent "yes" would have fired a deployment-wide action. Note the boundary -
    # JSON `1` is still accepted, because a literal bool schema cannot carry
    # `strict` and rejecting it would need StrictBool plus a validator again.
    # Both `1` and `true` are explicit values, so the gap is a shape question,
    # not a safety one; the "empty or accidental request" case is what matters
    # and is covered above.
    for truthy in ("true", "yes", "on"):
        assert (
            await owner_client.post(
                "/api/users/owner/require-password-change", json={"confirm": truthy}
            )
        ).status_code == 422, f"{truthy!r} was accepted as acknowledgement"
    # State-changing, so not reachable by navigation.
    assert (
        await owner_client.get("/api/users/owner/require-password-change")
    ).status_code == 405


@pytest.mark.asyncio
async def test_only_an_owner_may_require_a_password_change(admin_client, editor_client):
    for client in (admin_client, editor_client):
        resp = await client.post(
            "/api/users/owner/require-password-change", json={"confirm": True}
        )
        assert resp.status_code == 403, resp.text
