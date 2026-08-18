from pydantic import BaseModel, Field


class MfaCodeRequest(BaseModel):
    """A code from the authenticator app, or a recovery code standing in for one."""

    # Loose enough to carry either shape: a 6-digit TOTP code or a formatted
    # recovery code. Which one it is, is decided by what it matches, not by its
    # shape - so a mistyped TOTP code cannot be reported as an invalid recovery
    # code or the reverse.
    code: str = Field(min_length=1, max_length=32)


class MfaEnrollResponse(BaseModel):
    """What the client needs to render an enrollment QR."""

    secret: str
    provisioning_uri: str


class MfaConfirmResponse(BaseModel):
    """Recovery codes, returned exactly once when a factor is armed."""

    recovery_codes: list[str]


class MfaStatusResponse(BaseModel):
    """The account's second-factor state, for the profile screen."""

    enabled: bool
    available: bool
    confirmed_at: str | None = None
    unused_recovery_codes: int = 0
