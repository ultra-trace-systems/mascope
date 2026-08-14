"""
Every authenticated route is closed to an account that owes a password change.

The gate lives in the two dependencies every authenticated route resolves
through, rather than in ``role_based_access``: most routes depend on
``current_active_user`` directly and never reach the role helper, so gating
there would leave the bulk of the API open. These tests assert the structure
that makes it work - no route binds an ungated dependency, and only the two
routes that let a user out of the gate bypass it.

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

#: The complete set of routes reachable while a password change is pending.
#: ``GET /api/users/me`` is how the frontend discovers the pending change, and
#: the credentials route is how the user clears it. Asserted as an exact set:
#: a third entry appearing here is a hole, not an improvement.
EXPECTED_EXEMPT_ROUTES = {
    ("GET", "/api/users/me"),
    ("PATCH", "/api/users/me/creds"),
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
