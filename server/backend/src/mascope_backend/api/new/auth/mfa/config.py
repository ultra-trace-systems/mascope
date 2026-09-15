"""
Configuration for TOTP multi-factor authentication.
"""

from pydantic import BaseModel


class MfaConfig(BaseModel):
    """
    Settings for the second authentication factor.

    The TOTP parameters are the RFC 6238 defaults, which is what every
    authenticator app assumes when scanning a provisioning URI. Changing them
    would silently break codes for anyone already enrolled.
    """

    # Label shown in the authenticator app's account list. Deployment-agnostic
    # on purpose: the account's email is the per-user part of the URI.
    ISSUER: str = "Mascope"
    DIGITS: int = 6
    PERIOD_SECONDS: int = 30
    # Counters either side of the current one that still verify, absorbing clock
    # skew between the server and the user's phone. One step each way is the
    # usual compromise; each extra step widens the replay window that
    # ``User.mfa_last_timestep`` then has to close.
    VALID_WINDOW: int = 1

    # Short-lived token proving the password step passed, exchanged for a
    # session by the verify route. Its own audience so the session strategy,
    # which validates ``mascope-users:auth``, can never accept one.
    PENDING_TOKEN_AUDIENCE: str = "mascope-users:mfa"
    PENDING_TOKEN_LIFETIME_SECONDS: int = 5 * 60
    # Wrong codes allowed against one pending token before it is burned and the
    # user starts again from the password step.
    PENDING_TOKEN_MAX_ATTEMPTS: int = 5

    # How long a presented code keeps authorizing the actions that hand out
    # credentials outliving the session (see mfa/reauth.py). Long enough to mint
    # a token and pair an agent in one sitting, short enough that a session
    # stolen later is outside it.
    REAUTH_WINDOW_SECONDS: int = 5 * 60

    RECOVERY_CODE_COUNT: int = 10
    # Characters per recovery code, drawn from the 31-symbol alphabet in
    # service.py: 16 gives about 79 bits. These are the whole fallback if the
    # authenticator is lost and are stored only as a SHA-256 digest, so they are
    # sized to resist an offline search of a leaked database dump rather than to
    # be convenient to type.
    RECOVERY_CODE_LENGTH: int = 16


mfa_settings = MfaConfig()
