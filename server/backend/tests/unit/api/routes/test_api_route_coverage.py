"""
Every route renders its response through ``@api_route``, bar a known few.

``@api_route`` is where a response is rendered safely: a non-finite float is
sent as null with a WARNING naming the route, and a body that cannot be
rendered is a 500 logged at ERROR (see ``test_api_route_render.py``). It is
also where a route that binds no auth dependency is refused at import. A
route outside it renders through FastAPI's own JSONResponse, where the same
NaN raises a ValueError that the app's handler answers 400 at INFO. So the
routes outside it are pinned as an exact set: a new one appearing here must
render through the decorator, or justify itself on this list.

Structural, like ``test_password_gate_coverage.py``: it walks whatever routes
the app registers, so a route added later is covered without anyone having to
come back here.
"""

from fastapi.routing import iter_route_contexts

from mascope_backend.api.lib.api_features import api_route
from mascope_backend.app.fast import fast


#: The routes that do not render through ``@api_route``. None of their bodies
#: carries a float. Two kinds:
#:
#: - Sign-in, sign-out, the second-factor step, agent pairing and access-token
#:   regeneration. They answer with cookies, tokens, codes and whole-second
#:   intervals, and several build their own Response to set a cookie.
#: - The tus upload protocol. tuspyserver generates these routes, so the
#:   decorator cannot be applied, and they answer with headers and empty
#:   bodies.
ROUTES_OUTSIDE_API_ROUTE = {
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/logout"),
    ("POST", "/api/auth/mfa/verify"),
    ("POST", "/api/auth/pairing/start"),
    ("POST", "/api/auth/pairing/poll"),
    ("POST", "/api/auth/pairing/approve"),
    ("POST", "/api/auth/access_token/regenerate"),
    ("HEAD", "/api/sample/files/upload/tus/{uuid}"),
    ("PATCH", "/api/sample/files/upload/tus/{uuid}"),
    ("OPTIONS", "/api/sample/files/upload/tus/"),
    ("POST", "/api/sample/files/upload/tus/"),
    ("POST", "/api/sample/files/upload/tus"),
    ("DELETE", "/api/sample/files/upload/tus/{uuid}"),
}


def _api_route_code():
    """
    The code object of ``@api_route``'s wrapper, which every route it wraps
    shares.

    Nothing else identifies the wrapper: ``functools.wraps`` copies the
    handler's name and attributes onto it, and the tus endpoints carry a
    hand-stamped ``token_access`` as well.
    """

    @api_route(public=True)
    async def probe():
        return None

    return probe.__code__


def _routes() -> list[tuple[str, str, bool]]:
    """(method, path, rendered through ``@api_route``) for every route."""
    wrapper_code = _api_route_code()
    return [
        (
            method,
            context.path_format,
            getattr(context.endpoint, "__code__", None) is wrapper_code,
        )
        for context in iter_route_contexts(fast.routes)
        for method in context.methods or ()
    ]


def test_the_app_registers_routes_to_check():
    # Guards the assertion below against passing on an empty walk.
    assert sum(wrapped for *_, wrapped in _routes()) > 200


def test_only_the_known_routes_render_outside_api_route():
    outside = {(method, path) for method, path, wrapped in _routes() if not wrapped}

    assert outside == ROUTES_OUTSIDE_API_ROUTE
