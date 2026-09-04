from datetime import datetime

from pydantic import BaseModel, Field


class DeviceRead(BaseModel):
    """A paired agent machine, as listed to its sponsor or an admin."""

    device_id: int
    name: str = Field(description="Machine name, defaults to the paired hostname")
    service_name: str = Field(description="Agent service the device holds a token for")
    instrument: str | None = Field(
        None,
        description="Instrument the agent reports it watches (None until it reports one)",
    )
    sponsor_username: str | None = Field(
        None, description="Who approved the pairing (None when the account is gone)"
    )
    created_at: datetime
    last_seen_at: datetime | None = Field(
        None, description="Last authenticated use, minute precision"
    )
    last_seen_version: str | None = Field(
        None,
        description="Agent release last seen on this device (None until it reports one)",
    )
    revoked_at: datetime | None = Field(
        None, description="Set when the device's credentials were revoked"
    )
    token_count: int = Field(
        0, description="Live tokens bound to this device (0 once revoked)"
    )


class DeviceRename(BaseModel):
    """Rename request for a paired device."""

    name: str = Field(min_length=1, max_length=100)
