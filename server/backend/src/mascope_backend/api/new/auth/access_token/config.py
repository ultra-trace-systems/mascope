"""
Configuration settings specific to access tokens used for service-to-service authentication.
"""

from typing import List

from pydantic import BaseModel, Field


class AccessTokenConfig(BaseModel):
    """
    Configuration settings for access tokens.
    """

    # Allowed services for access tokens
    ALLOWED_SERVICES: List[str] = [
        "mascope_sdk",  # for Jupyter notebooks API library
        "file-converter",  # for file converter service
        "tof-agent",  # for the TOF agent
        "file-agent",  # for the File Agent
        "export-agent",  # for CSV Export Agent
        "instrument-agent",  # for the File Agent inside an instrument control program
    ]
    # How long a bearer token (SDK, agent, converter) authenticates after it
    # is minted. Its holder regenerates it from the profile dialog, or the
    # agent renews or re-pairs; the services below with a shorter life of
    # their own are held to that one too (see lifetime_for).
    ACCESS_TOKEN_EXPIRATION_SECONDS: int = 90 * 24 * 60 * 60  # 90 days

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
    #
    # The ceiling is here rather than in a comment because this is how long a
    # revoked credential keeps working, not a tuning knob: one minute is where
    # "seconds, deliberately" stops being true. Nothing supplies this value
    # from outside the source - AccessTokenConfig is a plain BaseModel with no
    # env or toml layer - so the bound guards a future edit, and an edit past
    # it fails at import, which is to say at startup. A negative value is
    # clamped to "off" by the cache rather than refused (see cache._ttl).
    SERVICE_TOKEN_CACHE_TTL_SECONDS: float = Field(5.0, le=60.0)

    # Device-bound agent tokens live on shared instrument PCs in plaintext, so
    # they expire far sooner than the 90-day default above and the agent
    # renews them automatically. Enforced only for tokens bound to a device
    # (see validation.ensure_device_token_fresh); the 90-day database-strategy
    # cap still applies on top. A token past this is refused, not deleted, so
    # renewal (which issues a fresh one) or re-pairing is the way back.
    DEVICE_TOKEN_LIFETIME_SECONDS: int = 30 * 24 * 60 * 60  # 30 days

    # Tokens kept per device after a renewal: the fresh one plus the token it
    # supersedes. Keeping the previous token gives the overlap that lets an
    # upload in flight during the switch finish on the old credential; it stays
    # usable only until its own lifetime elapses, so this widens no token's
    # life. Older tokens are reaped on renewal.
    DEVICE_TOKENS_KEPT_PER_DEVICE: int = 2
    # The file converter's token is minted for the request that hands the
    # converter its work - an upload, a peak computation - and carried with
    # that work only, since the server keeps no copy of a token it could hand
    # out again. It has to outlast the longest queue the work can wait in,
    # and nothing more: the rows of a user's that are past it are reaped at
    # the user's next mint.
    FILE_CONVERTER_TOKEN_LIFETIME_SECONDS: int = 30 * 24 * 60 * 60  # 30 days

    def lifetime_for(self, service_name: str | None) -> int:
        """
        How long a token of the service authenticates after it is minted.

        :param service_name: The token's service scope.
        :type service_name: str | None
        :return: Seconds; the default lifetime, or the converter's shorter one.
        :rtype: int
        """
        if service_name == "file-converter":
            return min(
                self.FILE_CONVERTER_TOKEN_LIFETIME_SECONDS,
                self.ACCESS_TOKEN_EXPIRATION_SECONDS,
            )
        return self.ACCESS_TOKEN_EXPIRATION_SECONDS
