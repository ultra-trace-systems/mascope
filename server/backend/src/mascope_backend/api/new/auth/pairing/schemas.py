import re

from pydantic import BaseModel, Field, field_validator

from mascope_backend.api.new.auth.pairing.config import pairing_settings
from mascope_backend.api.new.auth.reported import (
    AGENT_VERSION_MAX_LENGTH,
    INSTRUMENT_NAME_RE,
    clean_reported_text,
)


#: Width of ``AgentDevice.name``, which a reported machine name is stored in.
MACHINE_NAME_MAX_LENGTH = 64


class PairingStartRequest(BaseModel):
    """Request body for an agent starting a pairing."""

    service_name: str = Field(
        ...,
        description="The agent service requesting a token (e.g. 'file-agent').",
    )
    machine_name: str | None = Field(
        None,
        description="Optional hostname of the machine being paired, shown to "
        "the approving user and stamped on the token.",
    )
    # No max_length on either: a Field constraint runs before the validators
    # below, so an over-long value would be refused rather than cleaned - and
    # refusing a pairing request costs the machine its only route to a
    # credential. The instrument's length is part of INSTRUMENT_NAME_RE; the
    # version is cut to fit.
    instrument: str | None = Field(
        None,
        description="Optional name of the instrument the agent watches, kept "
        "on the paired device and shown under Paired machines.",
    )
    agent_version: str | None = Field(
        None,
        description="Optional release of the agent, kept on the paired device.",
    )

    @field_validator("service_name")
    @classmethod
    def validate_service_name(cls, service_name: str) -> str:
        """Only agent services may pair."""
        if service_name not in pairing_settings.ALLOWED_SERVICES:
            raise ValueError(f"Service '{service_name}' cannot be paired.")
        return service_name

    @field_validator("machine_name")
    @classmethod
    def sanitize_machine_name(cls, machine_name: str | None) -> str | None:
        """Strip control characters; the value is displayed to the approver."""
        return clean_reported_text(machine_name, MACHINE_NAME_MAX_LENGTH)

    @field_validator("instrument")
    @classmethod
    def validate_instrument(cls, instrument: str | None) -> str | None:
        """An instrument name the server could file uploads under, or nothing.

        Refused rather than dropped, unlike the version beside it: a name is
        what uploads will be filed under, so a wrong one is worth surfacing
        to the person approving the pairing rather than quietly ignoring.
        """
        # No width: a cut instrument name is a different instrument, and
        # uploads would be filed under it. Over-long is refused below.
        cleaned = clean_reported_text(instrument)
        if cleaned is None:
            return None
        if not INSTRUMENT_NAME_RE.match(cleaned):
            raise ValueError(
                "An instrument name may contain letters, digits and hyphens only."
            )
        return cleaned

    @field_validator("agent_version")
    @classmethod
    def sanitize_agent_version(cls, agent_version: str | None) -> str | None:
        """Strip control characters and cut to fit; shown to the approver.

        Cut rather than refused: a build stamped by ``git describe`` runs
        past the column's width, and a machine must not be unable to pair
        over the label it reports for itself.
        """
        return clean_reported_text(agent_version, AGENT_VERSION_MAX_LENGTH)


class PairingApproveRequest(BaseModel):
    """Request body for a user approving a pairing code in the web app."""

    user_code: str = Field(..., max_length=16)

    @field_validator("user_code")
    @classmethod
    def normalize_user_code(cls, user_code: str) -> str:
        """Accept the code case-insensitively, with or without the dash."""
        return re.sub(r"[^A-Z0-9]", "", user_code.upper())


class PairingPollRequest(BaseModel):
    """Request body for an agent polling its pairing status."""

    device_code: str = Field(..., min_length=16, max_length=128)
