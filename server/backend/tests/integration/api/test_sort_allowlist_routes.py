"""Integration tests: ``sort`` on the list routes, against a real database.

Every value a route's ``sort`` enum accepts orders the results, in either
direction and on the query branches that add joins or ``DISTINCT ON``; every
other value is a 422 from request validation. The INJ-02 pentest control sends
``GET /api/users?sort=1' OR '1'='1`` and flags a 5xx.

``/api/users`` is open to every active user, and a caller below admin reads ids
and usernames only, so such a caller may neither filter by role nor order by a
column that view omits.
"""

from datetime import datetime, timezone
from typing import get_args

import pytest
import pytest_asyncio
from fastapi.routing import iter_route_contexts

from mascope_backend.api.controllers.match.compounds.match_compounds_controller import (
    get_match_compounds,
)
from mascope_backend.api.controllers.match.ions.match_ions_controller import (
    get_match_ions,
)
from mascope_backend.api.controllers.match_rating.match_rating_controller import (
    get_match_ratings,
)
from mascope_backend.api.lib.exceptions.api_exceptions import ApiException
from mascope_backend.api.models.match.compounds.match_compound_pydantic_model import (
    MatchCompoundSortColumn,
)
from mascope_backend.api.models.match.ions.match_ion_pydantic_model import (
    MatchIonSortColumn,
)
from mascope_backend.api.models.match_rating.match_rating_pydantic_model import (
    MatchRatingSortColumn,
)
from mascope_backend.api.new.users.service import get_users
from mascope_backend.app.fast import fast
from mascope_backend.db import Dataset, SampleBatch, User, Workspace, WorkspaceMember
from mascope_backend.db.id import gen_id


INJECTION = "1' OR '1'='1"
_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)

#: Routes the HTTP sweep leaves out, and why. Their ``sort`` enums are still
#: checked against the model by the unit tests.
_NOT_SWEPT = {
    # Reads ``sample_view``, which the test schema (``create_all`` over the ORM
    # models) does not build, so it fails whatever ``sort`` says.
    "/api/samples",
    # The route requires a sample item the caller can read, or a batch - which
    # these two controllers resolve through ``sample_view``. The controllers
    # need neither, so they are swept directly (``CONTROLLER_SWEPT``).
    "/api/match_ratings",
    "/api/match/compounds",
    "/api/match/ions",
}

CONTROLLER_SWEPT = [
    (get_match_ratings, MatchRatingSortColumn),
    (get_match_compounds, MatchCompoundSortColumn),
    (get_match_ions, MatchIonSortColumn),
]

#: Routes that refuse a request naming neither a sample item nor a batch.
_BATCH_SCOPED = {
    "/api/match/collections",
    "/api/match/isotopes",
    "/api/match/samples",
}


def _sort_routes():
    """``(path, {param: schema})`` for GET routes without path parameters whose
    query string takes ``sort``, with every query parameter's schema."""
    paths = fast.openapi()["paths"]
    routes = {}
    for route in iter_route_contexts(fast.routes):
        # Plain Starlette routes (static files, the SPA fallback) have no dependant.
        if getattr(route, "dependant", None) is None:
            continue
        if "GET" not in (route.methods or ()) or "{" in route.path_format:
            continue
        parameters = {
            parameter["name"]: parameter
            for parameter in paths[route.path_format]["get"].get("parameters", [])
            if parameter["in"] == "query"
        }
        if "sort" in parameters:
            routes[route.path_format] = parameters
    return dict(sorted(routes.items()))


SORT_ROUTES = _sort_routes()


def _enum(schema):
    for variant in schema.get("anyOf", [schema]):
        if "enum" in variant:
            return variant["enum"]
    raise AssertionError(f"sort is not a closed set: {schema}")


def _placeholder(schema):
    """A well-formed value for a required query parameter the test does not care about."""
    variant = next(
        (v for v in schema.get("anyOf", [schema]) if v.get("type") != "null"), schema
    )
    return {"integer": 0, "number": 0, "boolean": "false", "array": [gen_id()]}.get(
        variant.get("type"), gen_id()
    )


def _valid_sort_requests():
    for path, parameters in SORT_ROUTES.items():
        if path in _NOT_SWEPT:
            continue
        required = {
            name: _placeholder(parameter["schema"])
            for name, parameter in parameters.items()
            if parameter.get("required")
        }
        for value in _enum(parameters["sort"]["schema"]):
            for order in ("asc", "desc"):
                yield pytest.param(
                    path,
                    {**required, "sort": value, "order": order},
                    id=f"{path}?sort={value}&order={order}",
                )


@pytest_asyncio.fixture
async def batch_id(async_session_factory, test_users):
    """An empty sample batch every test user can read."""
    ids = {"workspace": gen_id(), "dataset": gen_id(), "batch": gen_id()}
    async with async_session_factory() as session:
        session.add(
            Workspace(
                workspace_id=ids["workspace"],
                workspace_name=f"Sort WS {ids['workspace']}",
                workspace_description="Sort allowlist test workspace",
                workspace_status="active",
                workspace_utc_created=_NOW,
                workspace_utc_modified=_NOW,
            )
        )
        for role_name, user in test_users.items():
            session.add(
                WorkspaceMember(
                    workspace_member_id=gen_id(),
                    workspace_id=ids["workspace"],
                    user_id=user.id,
                    workspace_role=role_name,
                    granted_at=_NOW,
                    granted_by=user.id,
                )
            )
        session.add(
            Dataset(
                dataset_id=ids["dataset"],
                workspace_id=ids["workspace"],
                dataset_name=f"Sort Dataset {ids['dataset']}",
                dataset_type="ANALYSIS",
                dataset_utc_created=_NOW,
            )
        )
        session.add(
            SampleBatch(
                sample_batch_id=ids["batch"],
                dataset_id=ids["dataset"],
                sample_batch_name="Sort Batch",
                sample_batch_utc_created=_NOW,
            )
        )
        await session.commit()
    return ids["batch"]


def test_the_route_sweep_is_not_vacuous():
    assert "/api/users" in SORT_ROUTES
    assert "/api/sample/files" in SORT_ROUTES
    assert "/api/target/isotopes" in SORT_ROUTES
    assert len(SORT_ROUTES) >= 15


@pytest.mark.asyncio
@pytest.mark.parametrize("path", SORT_ROUTES)
@pytest.mark.parametrize("sort", [INJECTION, "__class__", "hashed_password"])
async def test_unsortable_value_is_refused_with_422(owner_client, path, sort):
    """Owner, so no route refuses on access before it validates the query."""
    response = await owner_client.get(path, params={"sort": sort})
    assert response.status_code == 422, (path, response.status_code, response.text)


@pytest.mark.asyncio
@pytest.mark.parametrize(("path", "params"), list(_valid_sort_requests()))
async def test_every_accepted_sort_value_orders_the_results(
    owner_client, batch_id, path, params
):
    """A value the enum accepts is neither a 5xx nor refused by the controller
    (a 400 would mean the controller checks a different allowlist)."""
    if path in _BATCH_SCOPED:
        params = {**params, "sample_batch_id": batch_id}
    response = await owner_client.get(path, params=params)
    assert response.status_code < 500, (path, params, response.text)
    assert response.status_code not in (400, 422), (path, params, response.text)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("controller", "sort"),
    [
        pytest.param(controller, sort, id=f"{controller.__name__}?sort={sort}")
        for controller, sort_column in CONTROLLER_SWEPT
        for sort in get_args(sort_column)
    ],
)
@pytest.mark.parametrize("order", ["asc", "desc"])
async def test_unswept_controllers_order_by_every_accepted_value(
    test_users, controller, sort, order
):
    """Each value the route accepts, handed to the controller it calls."""
    response = await controller(sort=sort, order=order)
    assert "data" in response


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "sorts"),
    [
        ("/api/target/ions", ["target_ion_formula", "target_compound_id"]),
        ("/api/target/isotopes", ["mz", "target_isotope_formula", "resolution"]),
    ],
)
@pytest.mark.parametrize("show_target_collection", ["false", "true"])
@pytest.mark.parametrize("order", ["asc", "desc"])
async def test_batch_filter_sorts_by_columns_other_than_its_distinct_key(
    owner_client, batch_id, path, sorts, show_target_collection, order
):
    """The batch filter selects DISTINCT ON the row id, which Postgres requires
    to lead any ORDER BY; ordering by another column must still work."""
    for sort in sorts:
        response = await owner_client.get(
            path,
            params={
                "sample_batch_id": batch_id,
                "show_target_collection": show_target_collection,
                "sort": sort,
                "order": order,
            },
        )
        assert response.status_code == 200, (path, sort, response.text)


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
@pytest.mark.parametrize("sort", ["id", "username"])
async def test_guest_sorts_users_by_a_public_column(guest_client, sort):
    response = await guest_client.get(
        "/api/users", params={"sort": sort, "order": "asc"}
    )
    assert response.status_code == 200
    # Only the fixture accounts: their initials differ, so the database
    # collation and Python agree on their order whatever else the run created.
    fixture_names = {"admin_user", "editor_user", "guest_user", "owner_user"}
    rows = [
        user for user in response.json()["data"] if user["username"] in fixture_names
    ]
    assert set(rows[0]) == {"id", "username"}
    key = "username" if sort == "username" else "id"
    assert [row[key] for row in rows] == sorted(row[key] for row in rows)


@pytest_asyncio.fixture
async def backfilled_user(async_session_factory, roles):
    """The newest account by id, registered before every other: an imported one."""
    async with async_session_factory() as session:
        user = User(
            email="sort-backfilled@test.com",
            username="sort_backfilled_user",
            hashed_password="123456",
            is_active=True,
            is_verified=False,
            role_id=roles["guest"].role_id,
            registered_at=datetime(2000, 1, 1, tzinfo=timezone.utc),
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


@pytest.mark.asyncio
async def test_guest_default_order_does_not_follow_registration(
    guest_client, backfilled_user
):
    """With no sort, a caller below admin gets id order, not registration order:
    the backfilled account comes first, where registration order puts it last."""
    response = await guest_client.get("/api/users")
    assert response.status_code == 200
    ids = [user["id"] for user in response.json()["data"]]
    assert ids == sorted(ids, reverse=True)
    assert ids[0] == backfilled_user.id


@pytest.mark.asyncio
async def test_guest_cannot_sort_users_by_registration(guest_client):
    response = await guest_client.get("/api/users", params={"sort": "registered_at"})
    assert response.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "params", [{"role_name_min": "admin"}, {"role_name_max": "guest"}]
)
async def test_guest_cannot_filter_users_by_role(guest_client, params):
    """Filtering by role would list who holds it, which the public view omits."""
    response = await guest_client.get("/api/users", params=params)
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_admin_filters_and_sorts_users_by_registration(admin_client):
    response = await admin_client.get(
        "/api/users",
        params={"role_name_min": "admin", "sort": "registered_at", "order": "asc"},
    )
    assert response.status_code == 200
    users = response.json()["data"]
    assert {user["role_name"] for user in users} <= {"admin", "owner"}
    stamps = [user["registered_at"] for user in users]
    assert stamps == sorted(stamps)


@pytest.mark.asyncio
async def test_service_refuses_an_unsortable_column_without_the_query_model(
    test_users,
):
    """A caller that reaches the service directly still cannot name a column
    outside the allowlist; the refusal is a 400, not a 500."""
    with pytest.raises(ApiException) as excinfo:
        await get_users(sort="hashed_password", caller=test_users["admin"])
    assert excinfo.value.status_code == 400
