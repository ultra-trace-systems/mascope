"""
Every authenticated route is closed to an account that owes a password change.

The gate lives in the two dependencies every authenticated route resolves
through, rather than in ``role_based_access``: most routes depend on
``current_active_user`` directly and never reach the role helper, so gating
there would leave the bulk of the API open. These tests assert the structure
that makes it work - no route binds an ungated dependency, only the two routes
that let a user out of the gate bypass it, and every route that resolves no
gated identity at all is on an explicit known-ungated list, so an identity
dependency created outside this module (a fresh ``fastapi_users.current_user``
closure, a library router's own internals) cannot slip in unnoticed.

Structural on purpose: they walk whatever routes the app has registered, so a
route added later is covered without anyone remembering to come back here.
"""

from fastapi.routing import APIRoute

from mascope_backend.api.new.auth import dependencies as deps
from mascope_backend.app.fast import fast


#: Resolve an identity without enforcing the password change. Anything binding
#: one of these directly is either a gated wrapper or a bug.
RAW_DEPENDENCIES = {
    deps._authenticated_active_user,
    deps._authenticated_superuser,
}

#: The wrappers allowed to bind a raw dependency.
GATED_WRAPPERS = {deps.current_active_user, deps.current_superuser}
EXEMPT_WRAPPERS = {
    deps.password_gate_exempt_active_user,
    deps.password_gate_exempt_guest_user,
}

#: The routes that bypass the gate through an exempt wrapper, asserted as an
#: exact set: a third entry appearing here is a hole, not an improvement.
#: ``GET /api/users/me`` is how the frontend discovers the pending change, and
#: the credentials route is how the user clears it. Not quite the complete set
#: of routes a gated account can reach: logout stays reachable through
#: fastapi-users' own token dependency (see ``KNOWN_UNGATED_ROUTES``) -
#: deliberately, since the password screen's Log out button depends on it.
EXPECTED_EXEMPT_ROUTES = {
    ("GET", "/api/users/me"),
    ("PATCH", "/api/users/me/creds"),
}

#: Every route that resolves no raw dependency at all, asserted as an exact
#: set below. Everything here is either anonymous by design (login, the
#: first-owner bootstrap, instrument-agent pairing), authenticated by
#: fastapi-users' own token dependency (logout, which a gated account must be
#: able to call - the password screen offers Log out), or tus protocol
#: plumbing whose authentication lives on the sibling methods of the same
#: router. A new entry appearing here means a route resolves its identity
#: outside auth.dependencies - gate it, or justify it on this list.
KNOWN_UNGATED_ROUTES = {
    ("POST", "/login"),
    ("POST", "/logout"),
    ("POST", "/pairing/start"),
    ("POST", "/pairing/poll"),
    ("GET", "/api/users/first-owner/status"),
    ("POST", "/api/users/first-owner"),
    ("HEAD", "/api/sample/files/upload/tus/{uuid}"),
    ("OPTIONS", "/api/sample/files/upload/tus/"),
    ("DELETE", "/api/sample/files/upload/tus/{uuid}"),
}


def _flatten(routes):
    """Yield leaf routes, descending through included routers."""
    for route in routes:
        inner = getattr(route, "original_router", None)
        if inner is not None:
            yield from _flatten(inner.routes)
        else:
            yield route


def _api_routes():
    """All registered API routes."""
    return [route for route in _flatten(fast.routes) if isinstance(route, APIRoute)]


def _walk(dependant, parent=None):
    """Yield every (dependant, parent) pair in a route's dependency tree."""
    yield dependant, parent
    for sub in dependant.dependencies:
        yield from _walk(sub, dependant)


def _routes_binding_raw_dependency():
    """Map each route to the wrappers through which it reaches a raw dependency."""
    found: dict[tuple[str, str], set] = {}
    for route in _api_routes():
        for node, parent in _walk(route.dependant):
            if node.call in RAW_DEPENDENCIES:
                for method in route.methods:
                    key = (method, route.path)
                    found.setdefault(key, set()).add(
                        parent.call if parent is not None else None
                    )
    return found


def test_the_app_registers_routes_to_check():
    # Guards the rest of this module: if the routers ever stop being registered
    # at import, every assertion below would pass against an empty set.
    assert len(_api_routes()) > 100


def test_no_route_binds_an_ungated_user_dependency():
    # A route reaching a raw dependency other than through one of the four
    # wrappers is authenticated but not gated - reachable by an account that
    # owes a password change.
    allowed = GATED_WRAPPERS | EXEMPT_WRAPPERS
    offenders = {
        route: {getattr(parent, "__name__", parent) for parent in parents}
        for route, parents in _routes_binding_raw_dependency().items()
        if not parents.issubset(allowed)
    }
    assert offenders == {}


def test_only_the_password_change_routes_bypass_the_gate():
    # Exact-set equality, not a subset check: the point is that nothing else
    # became exempt.
    exempt = {
        route
        for route, parents in _routes_binding_raw_dependency().items()
        if parents & EXEMPT_WRAPPERS
    }
    assert exempt == EXPECTED_EXEMPT_ROUTES


def test_every_route_is_gated_or_known_ungated():
    # Closed-world complement of the tests above. Those prove that routes
    # binding the raw dependencies do so through a gated or exempt wrapper -
    # but a route resolving its identity any other way (a fresh
    # fastapi_users.current_user(...) closure, a library router's internal
    # dependency) binds neither raw object and is invisible to them. Requiring
    # the remainder to match an explicit list turns such a route into a loud
    # failure instead of a silently ungated endpoint.
    bound = _routes_binding_raw_dependency()
    unbound = {
        (method, route.path)
        for route in _api_routes()
        for method in route.methods
        if (method, route.path) not in bound
    }
    assert unbound == KNOWN_UNGATED_ROUTES


def test_ungated_dependencies_are_not_exported():
    # These were unused fastapi-users dependencies that resolved an identity
    # without the gate. Re-adding one gives a future route an easy way to be
    # silently ungated, and the name reads as if it were safe.
    for name in (
        "current_user",
        "current_active_verified_user",
        "get_current_user_token",
    ):
        assert not hasattr(deps, name), (
            f"{name} is an ungated auth dependency; route it through "
            "current_active_user instead."
        )


def test_upload_routes_resolve_the_gated_dependency():
    # The tus upload family binds its auth at router level rather than in any
    # handler signature, so its gating is invisible when reading the handlers.
    upload_routes = {
        route
        for route in _routes_binding_raw_dependency()
        if "/upload" in route[1] or "/files" in route[1]
    }
    assert upload_routes, "expected some upload routes to exist"
    allowed = GATED_WRAPPERS | EXEMPT_WRAPPERS
    binding = _routes_binding_raw_dependency()
    for route in upload_routes:
        assert binding[route].issubset(allowed)
