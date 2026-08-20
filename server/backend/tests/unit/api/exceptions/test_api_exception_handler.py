"""An ApiException that escapes a route must not read as a server fault.

``@api_route`` converts ApiException into a response itself, so most routes
never exercise this. The tus upload routes do: tuspyserver generates them, so
they cannot take ``@api_route`` (see
``tests/integration/api/upload/test_tus_token_access.py``), while the handler
they call reaches ``@api_controller``-decorated code, which *raises*. With no
handler registered for the type, the exception fell through to the catch-all
``Exception`` handler -
and Starlette's ServerErrorMiddleware re-raises after that handler responds, so
the Sentry ASGI integration captured it. An upload token expiring mid-transfer,
a routine 401, was landing in error monitoring as an unhandled fault.
"""

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from mascope_backend.api.lib.exceptions.api_exceptions import (
    ApiException,
    api_e_response_json,
    handle_exception,
)
from mascope_backend.app.fast import api_exception_handler, fast


EXPIRED_TOKEN = ApiException(
    "Failed to get access token. Please sign in to the Mascope.",
    {"error_id": "0123456789abcdef"},
    401,
)


def _app(*, with_handler: bool) -> FastAPI:
    """A route that raises ApiException, as an undecorated tus route does."""
    app = FastAPI()

    @app.exception_handler(Exception)
    async def _catch_all(request, exc):  # noqa: ARG001
        return handle_exception(exc, "Unhandled exception", response_type="http")

    if with_handler:
        app.add_exception_handler(ApiException, api_exception_handler)

    @app.patch("/upload")
    async def _upload():
        raise EXPIRED_TOKEN

    return app


class TestEscapedApiException:
    def test_the_real_app_registers_a_handler_for_it(self):
        assert fast.exception_handlers.get(ApiException) is api_exception_handler

    def test_it_is_answered_with_its_own_status_and_message(self):
        client = TestClient(_app(with_handler=True))
        response = client.patch("/upload")

        assert response.status_code == 401
        assert response.json()["error"] == EXPIRED_TOKEN.user_message

    def test_it_no_longer_escapes_the_app(self):
        # The regression: without a handler for the type, the catch-all
        # responds and Starlette re-raises anyway, which is what error
        # monitoring recorded. TestClient surfaces that re-raise.
        client = TestClient(_app(with_handler=False))
        with pytest.raises(ApiException):
            client.patch("/upload")

    def test_the_response_matches_what_api_route_would_have_returned(self):
        # A route that does not own its decoration must not answer differently
        # from one that does.
        client = TestClient(_app(with_handler=True))
        response = client.patch("/upload")
        expected = api_e_response_json(EXPIRED_TOKEN)

        assert response.status_code == expected.status_code
        assert response.json() == {
            "error": EXPIRED_TOKEN.user_message,
            "detail": EXPIRED_TOKEN.tech_message,
        }


class TestHandlerIsSerializationOnly:
    @pytest.mark.asyncio
    async def test_it_does_not_relog_an_already_logged_exception(self):
        # process_exception logged the ApiException at its proper level when
        # it was built; logging again here would double-count it.
        from mascope_backend.runtime import runtime

        records = []
        sink_id = runtime.logger.add(
            lambda message: records.append(message.record), level="TRACE"
        )
        try:
            response = await api_exception_handler(None, EXPIRED_TOKEN)
        finally:
            runtime.logger.remove(sink_id)

        assert isinstance(response, JSONResponse)
        assert response.status_code == 401
        assert records == []
