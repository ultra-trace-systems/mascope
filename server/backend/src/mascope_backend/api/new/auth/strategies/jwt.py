import time
from typing import Optional

import jwt
from fastapi_users import exceptions
from fastapi_users.authentication import JWTStrategy
from fastapi_users.jwt import generate_jwt
from fastapi_users.manager import BaseUserManager

from mascope_backend.api.new.auth.config import auth_settings
from mascope_backend.api.new.auth.sessions import session_revoked
from mascope_backend.db import User
from mascope_backend.runtime import runtime


class SessionJWTStrategy(JWTStrategy[User, int]):
    """
    The web session token, with an issue time the server can revoke against.

    fastapi-users writes only subject, audience and expiry, which leaves nothing
    to compare a revocation with. This strategy also writes ``iat`` and, on
    every read, refuses a token issued before the account's session cutoff (see
    :mod:`mascope_backend.api.new.auth.sessions`). The refusal is a ``None``
    from :meth:`read_token`, which fastapi-users answers with its usual 401 -
    before any route dependency, so the password and enrolment gates never see
    a revoked session, and neither does ``GET /api/users/me``, which skips them.
    """

    async def write_token(self, user: User) -> str:
        data = {
            "sub": str(user.id),
            "aud": self.token_audience,
            "iat": int(time.time()),
        }
        return generate_jwt(
            data, self.encode_key, self.lifetime_seconds, algorithm=self.algorithm
        )

    async def read_session(
        self, token: Optional[str], user_manager: BaseUserManager[User, int]
    ) -> Optional[tuple[User, Optional[int], Optional[int]]]:
        """
        The account a token belongs to, and the token's issue and expiry times.

        The socket keeps both times beside its session so it can make the same
        checks later without the token: subscribe compares the issue time with
        the account's session cutoff, and a session whose token has expired is
        signed out (:mod:`mascope_backend.socket.eviction`).

        :param token: The encoded token.
        :param user_manager: Resolves the subject to a user.
        :return: ``(user, iat, exp)``, with ``iat`` ``None`` for a token minted
            before tokens carried one and ``exp`` ``None`` for a token without
            an expiry; ``None`` when the token is invalid or expired, its
            account is gone, or its session has been revoked.
        """
        claims = self._decode(token)
        if claims is None:
            return None
        user_id, issued_at, expires_at = claims

        try:
            user = await user_manager.get(user_manager.parse_id(user_id))
        except (exceptions.UserNotExists, exceptions.InvalidID):
            return None

        if session_revoked(user, issued_at):
            runtime.logger.info(
                f"Refused a web session of user {user.id} issued before its "
                "session cutoff"
            )
            return None
        return user, issued_at, expires_at

    def issued_at(self, token: Optional[str]) -> Optional[int]:
        """
        The issue time of a token, without looking its account up.

        For a caller that has already authenticated the token and needs only
        which session it is: logout signs out the sockets that session
        authenticated (:func:`mascope_backend.socket.eviction.sign_out_session`).

        :param token: The encoded token.
        :return: Its ``iat``, or ``None`` when it has none or is invalid.
        """
        claims = self._decode(token)
        return claims[1] if claims else None

    def _decode(
        self, token: Optional[str]
    ) -> Optional[tuple[str, Optional[int], Optional[int]]]:
        """
        A token's subject, issue and expiry times, once its signature, audience
        and expiry hold.

        :param token: The encoded token.
        :return: ``(sub, iat, exp)``, with ``iat`` ``None`` for a token minted
            before tokens carried one and ``exp`` ``None`` for a token without
            one; ``None`` when the token is invalid or expired.
        """
        if token is None:
            return None
        try:
            data = jwt.decode(
                token,
                self.decode_key,
                audience=self.token_audience,
                algorithms=[self.algorithm],
                # The issue time is compared with the cutoff, with a tolerance
                # for clock skew between workers (sessions.py); PyJWT's own
                # check would refuse a token another worker minted a second
                # "in the future" with no tolerance at all.
                options={"verify_iat": False},
            )
        except jwt.PyJWTError:
            return None
        user_id = data.get("sub")
        if user_id is None:
            return None
        issued_at = data.get("iat")
        if issued_at is not None and not isinstance(issued_at, int):
            return None
        # PyJWT has already refused an expired token and a non-numeric expiry.
        expires_at = data.get("exp")
        if expires_at is not None:
            expires_at = int(expires_at)
        return user_id, issued_at, expires_at

    async def read_token(
        self, token: Optional[str], user_manager: BaseUserManager[User, int]
    ) -> Optional[User]:
        session = await self.read_session(token, user_manager)
        return session[0] if session else None


# JWT strategy for cookie authentication
def get_jwt_strategy() -> SessionJWTStrategy:
    """
    Returns a JWT strategy for handling user authentication via cookies.

    This function configures a JWT (JSON Web Token) strategy, which FastAPI Users uses to handle
    user authentication. The JWT is signed using a secret key and has a defined expiration time.

    :return: A configured JWT strategy for handling authentication.
    :rtype: SessionJWTStrategy
    """
    return SessionJWTStrategy(
        secret=auth_settings.JWT_SECRET_KEY,
        lifetime_seconds=auth_settings.JWT_EXPIRATION_SECONDS,
        token_audience=auth_settings.JWT_AUDIENCE,
        algorithm=auth_settings.JWT_ALGORITHM,
    )
