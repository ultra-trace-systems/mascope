"""
Configuration settings specific to access tokens used for service-to-service authentication.
"""

from typing import List

from pydantic import BaseModel


class AccessTokenConfig(BaseModel):
    """
    Configuration settings for access tokens.
    """

    # Allowed services for access tokens
    ALLOWED_SERVICES: List[str] = [
        "mascope_sdk",  # for Jupyter notebooks API library
        "file-converter",  # for file converter service
        "tof-agent",  # for TOF instrument agent
        "file-agent",  # for File instrument agent
        "export-agent",  # for CSV Export Agent
    ]
    # Access token-based authentication settings for Jupyter library API access
    ACCESS_TOKEN_EXPIRATION_SECONDS: int = (
        360 * 24 * 60 * 60
    )  # Access token lifetime  - 360 days in seconds

    # How long a successful service-token validation is reused before the
    # database is consulted again. A resumable upload revalidates the same
    # token once per chunk, and each validation costs two connections on a
    # path with no admission control - enough of them at once exhausted a
    # production pool and stalled the worker for a minute.
    #
    # The cost of caching is that revoking a token (unpairing a device,
    # clearing a user's credentials) takes effect within this window rather
    # than immediately, per worker. Seconds, deliberately: long enough to
    # collapse an upload's chunk burst into one validation, short enough that
    # revocation is still prompt. Set to 0 to validate every request.
    SERVICE_TOKEN_CACHE_TTL_SECONDS: float = 5.0

    # Device-bound agent tokens live on shared instrument PCs in plaintext, so
    # they expire far sooner than the 360-day default above and the agent
    # renews them automatically. Enforced only for tokens bound to a device
    # (see validation.ensure_device_token_fresh); the 360-day database-strategy
    # cap still applies on top. A token past this is refused, not deleted, so
    # renewal (which issues a fresh one) or re-pairing is the way back.
    DEVICE_TOKEN_LIFETIME_SECONDS: int = 30 * 24 * 60 * 60  # 30 days

    # Tokens kept per device after a renewal: the fresh one plus the token it
    # supersedes. Keeping the previous token gives the overlap that lets an
    # upload in flight during the switch finish on the old credential; it stays
    # usable only until its own lifetime elapses, so this widens no token's
    # life. Older tokens are reaped on renewal.
    DEVICE_TOKENS_KEPT_PER_DEVICE: int = 2
