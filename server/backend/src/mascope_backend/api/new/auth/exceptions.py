from fastapi import HTTPException, status

from mascope_backend.api.lib.exceptions.api_exceptions import (
    ClientFacingDetail,
    CodedHTTPException,
)


#: Code carried in the error payload's ``detail`` when an account owes a
#: password change. The frontend branches on this to swap in the mandatory
#: password screen, so it is part of the API contract.
PASSWORD_CHANGE_REQUIRED_CODE = "password_change_required"


class ForbiddenAccessException(HTTPException):
    """
    Exception for Forbidden (403) access.
    Used when a user does not have sufficient permissions to access a resource.
    """

    def __init__(
        self,
        detail: str = "You do not have permission to perform this action.",
    ):
        super().__init__(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


class PasswordChangeRequiredException(CodedHTTPException):
    """
    Exception for an authenticated account that must replace its password (403).

    Not 401: the session is valid and the client must not be sent back to the
    sign-in screen. What is refused is the action, not the identity. The code is
    what separates this from an ordinary ForbiddenAccessException, which carries
    the same status.
    """

    error_code = PASSWORD_CHANGE_REQUIRED_CODE

    def __init__(
        self,
        detail: str = (
            "You must set a new password before you can continue using Mascope."
        ),
    ):
        super().__init__(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


class InvalidTokenException(HTTPException):
    """Exception for invalid or missing authentication token."""

    def __init__(
        self, detail: str = "Invalid authentication token or missing service."
    ):
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
        )


class AgentCredentialRefusedException(InvalidTokenException, ClientFacingDetail):
    """An agent credential was refused, with remediation the operator can act on.

    Kept an :class:`InvalidTokenException` on purpose: the token validation
    path, the converter-token lookup and the socket handshake all catch that
    type, and a refusal that escaped them would be re-raised as a generic
    "Token validation failed" - losing this message and logging an error for
    a routine, expected outcome.
    """
