"""Tests: the agent services that can pair, and what else knows their names.

The services that can pair are one list (``pairing_settings``), the services a
token can be scoped to another, and minting a token by hand names the agents a
third time to decide the role it takes. A pairable service missing from that
check would be minted for anyone signed in, so each check is asserted for
every pairable service rather than for the ones there are today.
"""

import pytest

from mascope_backend.api.new.auth.access_token import routes as access_token_routes
from mascope_backend.api.new.auth.access_token.routes import (
    access_token_regenerate_route,
)
from mascope_backend.api.new.auth.access_token.schemas import AccessTokenRequest
from mascope_backend.api.new.auth.config import auth_settings
from mascope_backend.api.new.auth.exceptions import ForbiddenAccessException
from mascope_backend.api.new.auth.pairing.config import pairing_settings
from mascope_backend.api.new.auth.pairing.schemas import PairingStartRequest
from mascope_backend.roles import ROLE_ACCESS_LEVELS


class _User:
    def __init__(self, role: str):
        self.id = 7
        self.mfa_enabled = False
        self.role_id = ROLE_ACCESS_LEVELS[role]


@pytest.fixture
def minted(monkeypatch):
    """Stand in for the database write: the services tokens were minted for."""
    services = []

    async def regenerate(user, service_name):
        services.append(service_name)
        return {"access_token": "minted"}

    monkeypatch.setattr(access_token_routes, "regenerate_access_token", regenerate)
    return services


def test_the_file_agent_inside_an_instrument_program_pairs_under_its_own_name():
    request = PairingStartRequest(service_name="instrument-agent")

    assert request.service_name == "instrument-agent"


@pytest.mark.parametrize("service", pairing_settings.ALLOWED_SERVICES)
def test_every_pairable_service_can_hold_a_token(service):
    assert service in auth_settings.access_token.ALLOWED_SERVICES
    assert AccessTokenRequest(service_name=service).service_name == service


@pytest.mark.asyncio
@pytest.mark.parametrize("service", pairing_settings.ALLOWED_SERVICES)
async def test_a_guest_cannot_mint_a_token_for_a_pairable_service(minted, service):
    # Pairing takes an editor's approval; a token minted by hand takes no less.
    with pytest.raises(ForbiddenAccessException):
        await access_token_regenerate_route(
            AccessTokenRequest(service_name=service), user=_User("guest")
        )

    assert minted == []


@pytest.mark.asyncio
@pytest.mark.parametrize("service", pairing_settings.ALLOWED_SERVICES)
async def test_an_editor_can_mint_a_token_for_a_pairable_service(minted, service):
    await access_token_regenerate_route(
        AccessTokenRequest(service_name=service), user=_User("editor")
    )

    assert minted == [service]
