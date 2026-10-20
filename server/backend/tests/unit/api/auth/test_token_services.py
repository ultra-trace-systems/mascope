"""Tests: a route's token access narrowed to some services.

``api_route(token_access=("file-converter",))`` opens a route to that service's
tokens alone; ``get_enabled_backends`` refuses any other service's with 401.
``token_access=True`` keeps meaning every allowed service. Which routes are
narrowed, and to what, is pinned in ``unit/api/routes/test_token_access_coverage.py``;
the rematch route's narrowing end to end is
``integration/api/workspace/test_rematch_sample_token.py``.
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import Depends

from mascope_backend.api.lib.api_features import api_route
from mascope_backend.api.new.auth import backend as auth_backend
from mascope_backend.api.new.auth.access_token import cache as token_cache
from mascope_backend.api.new.auth.access_token import util as token_util


def _auth_dep():
    return None


def _route(token_access):
    @api_route(token_access=token_access)
    async def handler(user=Depends(_auth_dep)):
        return {}

    return handler


class TestTheDecorator:
    def test_true_opens_the_route_to_every_service(self):
        handler = _route(True)

        assert handler.token_access is True
        assert handler.token_services is None

    def test_false_opens_it_to_none(self):
        handler = _route(False)

        assert handler.token_access is False
        assert handler.token_services is None

    def test_a_tuple_narrows_it_to_those_services(self):
        handler = _route(("file-converter", "tof-agent"))

        # Still a plain True: the OpenAPI document and the coverage tests ask
        # only whether a route takes a token at all.
        assert handler.token_access is True
        assert handler.token_services == frozenset({"file-converter", "tof-agent"})

    def test_a_bare_string_is_refused(self):
        # Iterated, "file-converter" would be a set of letters; it is refused
        # for what it is, not for naming the letters as services.
        with pytest.raises(ValueError, match="non-empty tuple of service names"):
            _route("file-converter")

    def test_an_empty_tuple_is_refused(self):
        with pytest.raises(ValueError, match="non-empty tuple of service names"):
            _route(())

    def test_a_service_no_token_is_minted_for_is_refused(self):
        with pytest.raises(ValueError, match="file_converter"):
            _route(("file_converter",))


class _Request:
    """The parts of a Request that get_enabled_backends reads."""

    def __init__(self, endpoint, service):
        self.headers = {"authorization": "Bearer tok", "x-service-name": service}
        self.cookies = {}
        self.scope = {"endpoint": endpoint}
        self.state = type("State", (), {})()


@pytest.fixture
def token_of(monkeypatch):
    """Make the token ``tok`` resolve to a service, as the token row would."""
    token_cache.clear()

    def _set(service):
        async def lookup(token, session=None):  # noqa: ARG001
            return service, None, datetime.now(timezone.utc) - timedelta(minutes=1)

        token_cache.clear()
        monkeypatch.setattr(token_util, "get_token_auth_context", lookup)

    yield _set
    token_cache.clear()


class TestTheBackend:
    @pytest.mark.asyncio
    async def test_a_narrowed_route_takes_its_services_token(self, token_of):
        token_of("file-converter")

        backends = await auth_backend.get_enabled_backends(
            _Request(_route(("file-converter",)), "file-converter")
        )

        assert backends == [auth_backend.auth_backend_access_token]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("service", ["mascope_sdk", "ai-agent", "file-agent"])
    async def test_a_narrowed_route_refuses_another_services_token(
        self, token_of, service
    ):
        token_of(service)

        with pytest.raises(auth_backend.HTTPException) as excinfo:
            await auth_backend.get_enabled_backends(
                _Request(_route(("file-converter",)), service)
            )

        assert excinfo.value.status_code == 401

    @pytest.mark.asyncio
    async def test_a_route_open_to_every_service_still_takes_any(self, token_of):
        token_of("mascope_sdk")

        backends = await auth_backend.get_enabled_backends(
            _Request(_route(True), "mascope_sdk")
        )

        assert backends == [auth_backend.auth_backend_access_token]

    @pytest.mark.asyncio
    async def test_a_hand_stamped_endpoint_is_open_to_every_service(self, token_of):
        # The tus upload routes set token_access by hand and no token_services.
        class Endpoint:
            token_access = True

        token_of("file-agent")

        backends = await auth_backend.get_enabled_backends(
            _Request(Endpoint(), "file-agent")
        )

        assert backends == [auth_backend.auth_backend_access_token]
