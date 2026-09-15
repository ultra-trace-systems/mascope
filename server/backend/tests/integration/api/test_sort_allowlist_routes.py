"""Integration tests: an unsortable ``sort`` value is a client error on every list route.

The INJ-02 pentest control sends ``GET /api/users?sort=1' OR '1'='1``. It was
never SQL injection - the value reached ``getattr(User, sort)`` - but the
resulting ``AttributeError`` answered 500, and any mapped attribute was a
column the caller could order by. Every route that takes ``sort`` now refuses
a value outside its allowlist during request validation.
"""

import pytest
from fastapi.routing import iter_route_contexts

from mascope_backend.api.lib.exceptions.api_exceptions import ApiException
from mascope_backend.api.new.users.service import get_users
from mascope_backend.app.fast import fast


INJECTION = "1' OR '1'='1"


def _unparameterised_sort_routes():
    """GET routes without path parameters whose query string takes ``sort``."""
    routes = []
    for route in iter_route_contexts(fast.routes):
        # Plain Starlette routes (static files, the SPA fallback) have no dependant.
        dependant = getattr(route, "dependant", None)
        if dependant is None or "GET" not in (route.methods or ()):
            continue
        if "{" in route.path_format:
            continue
        pending = [dependant]
        while pending:
            dependant = pending.pop()
            if any(param.name == "sort" for param in dependant.query_params):
                routes.append(route.path_format)
                break
            pending.extend(dependant.dependencies)
    return sorted(set(routes))


SORT_ROUTES = _unparameterised_sort_routes()


def test_the_route_sweep_is_not_vacuous():
    assert "/api/users" in SORT_ROUTES
    assert "/api/sample/files" in SORT_ROUTES
    assert len(SORT_ROUTES) >= 15


@pytest.mark.asyncio
@pytest.mark.parametrize("path", SORT_ROUTES)
@pytest.mark.parametrize("sort", [INJECTION, "__class__", "hashed_password"])
async def test_unsortable_value_is_refused_with_422(owner_client, path, sort):
    """Owner, so no route refuses on access before it validates the query."""
    response = await owner_client.get(path, params={"sort": sort})
    assert response.status_code == 422, (path, response.status_code, response.text)


@pytest.mark.asyncio
async def test_users_sort_payload_from_the_pentest_is_a_client_error(guest_client):
    """The exact INJ-02 request, as the least privileged active user."""
    response = await guest_client.get("/api/users", params={"sort": INJECTION})
    assert response.status_code == 422
    assert INJECTION not in response.json()["error"]


@pytest.mark.asyncio
async def test_users_cannot_be_ordered_by_a_password_hash(guest_client):
    response = await guest_client.get("/api/users", params={"sort": "hashed_password"})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_users_sort_by_an_allowlisted_column_still_works(guest_client):
    response = await guest_client.get(
        "/api/users", params={"sort": "username", "order": "asc"}
    )
    assert response.status_code == 200
    # Only the fixture accounts: their initials differ, so the database
    # collation and Python agree on their order whatever else the run created.
    fixture_names = {"admin_user", "editor_user", "guest_user", "owner_user"}
    usernames = [
        user["username"]
        for user in response.json()["data"]
        if user["username"] in fixture_names
    ]
    assert usernames == sorted(fixture_names)


@pytest.mark.asyncio
async def test_service_refuses_an_unsortable_column_without_the_query_model(
    test_users,
):
    """A caller that reaches the service directly still cannot name a column
    outside the allowlist; the refusal is a 400, not a 500."""
    with pytest.raises(ApiException) as excinfo:
        await get_users(sort="hashed_password")
    assert excinfo.value.status_code == 400
