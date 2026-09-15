from fastapi import HTTPException, status

from mascope_backend.api.lib.exceptions.api_exceptions import CodedHTTPException


#: Code carried in the error payload's ``detail`` when an action needs a freshly
#: presented second factor. The frontend branches on this to prompt for a code
#: and retry, so it is part of the API contract.
MFA_REAUTH_REQUIRED_CODE = "mfa_reauth_required"


class MfaReauthRequiredException(CodedHTTPException):
    """
    The action needs a recently presented code, and none is on record (403).

    Not 401: the session is valid and the caller stays signed in. What is
    refused is this action, until they prove they still hold the factor. The
    code is what separates it from an ordinary permission failure carrying the
    same status.
    """

    error_code = MFA_REAUTH_REQUIRED_CODE

    def __init__(
        self,
        detail: str = ("Enter a code from your authenticator app to continue."),
    ):
        super().__init__(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


#: Code carried in the error payload's ``detail`` when the deployment requires
#: this account to hold a second factor and it does not yet. The frontend
#: branches on this to swap in the enrolment screen, so it is part of the API
#: contract.
MFA_ENROLLMENT_REQUIRED_CODE = "mfa_enrollment_required"


class MfaEnrollmentRequiredException(CodedHTTPException):
    """
    The account may not use the application until it enrols (403).

    Not 401, for the same reason as the password gate: the session is valid and
    the client must not be sent back to the sign-in screen, where signing in
    would land here again. What is refused is the action, not the identity.
    """

    error_code = MFA_ENROLLMENT_REQUIRED_CODE

    def __init__(
        self,
        detail: str = (
            "This account must use two-factor authentication. Set it up to continue."
        ),
    ):
        super().__init__(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


class MfaRequiredByPolicyException(HTTPException):
    """
    Turning the factor off is refused because the deployment requires it (409).

    Separate from the enrolment gate: this account *has* a factor and is trying
    to give it up, which is a conflict with the policy rather than a missing
    prerequisite.
    """

    def __init__(
        self,
        detail: str = (
            "Two-factor authentication is required for this account and cannot "
            "be turned off. An administrator can reset it if you have lost your "
            "authenticator."
        ),
    ):
        super().__init__(status_code=status.HTTP_409_CONFLICT, detail=detail)


class MfaNotConfiguredException(HTTPException):
    """
    The deployment has no MFA encryption key, so seeds cannot be stored (503).

    Only reachable from the enrollment routes. A deployment in this state has no
    enrolled accounts by construction, so no login is affected.
    """

    def __init__(
        self,
        detail: str = (
            "Multi-factor authentication is not configured on this server. "
            "Ask an administrator to create the MFA encryption key."
        ),
    ):
        super().__init__(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=detail)


class InvalidMfaCodeException(HTTPException):
    """
    A submitted TOTP or recovery code did not verify (400).

    400 rather than 401, matching how the login route reports bad credentials.
    A 401 would be read by the frontend's response interceptor as an expired
    session and bounce the user out of the half-finished sign-in they are in the
    middle of completing; the sign-in attempt is still alive, only the code was
    wrong.
    """

    def __init__(self, detail: str = "That code is not valid. Please try again."):
        super().__init__(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


class InvalidPendingTokenException(HTTPException):
    """
    The half-finished sign-in is gone: no token, or one that is malformed,
    expired, already spent, or burned by too many wrong codes (401).

    401 on purpose - unlike a wrong code, this one *should* return the user to
    the sign-in screen, which is what the frontend does with an unhandled 401.
    Deliberately one exception for all of those causes: distinguishing them
    would tell a holder of a stolen token which of its guesses exhausted it.
    """

    def __init__(
        self,
        detail: str = "Your sign-in attempt has expired. Please sign in again.",
    ):
        super().__init__(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


class MfaAlreadyEnabledException(HTTPException):
    """Enrollment was started for an account that already has a confirmed factor (409)."""

    def __init__(
        self,
        detail: str = (
            "Multi-factor authentication is already enabled for this account. "
            "Turn it off before enrolling again."
        ),
    ):
        super().__init__(status_code=status.HTTP_409_CONFLICT, detail=detail)


class MfaNotEnrolledException(HTTPException):
    """An enrollment confirmation arrived with no enrollment in progress (409)."""

    def __init__(
        self,
        detail: str = "No enrollment is in progress. Start again to get a new code.",
    ):
        super().__init__(status_code=status.HTTP_409_CONFLICT, detail=detail)
