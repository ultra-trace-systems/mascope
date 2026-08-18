"""
Transport for the pending token.

Carried in its own httpOnly cookie rather than the response body so the token is
never readable from JavaScript, matching how the session itself is carried. The
security attributes are taken from the session cookie's settings so the two
cannot drift apart; only the name and the lifetime differ.
"""

from fastapi import Request, Response

from mascope_backend.api.new.auth.config import auth_settings


def set_pending_cookie(response: Response, token: str) -> None:
    """
    Attach a pending token to a response.

    :param response: The response being returned to the client.
    :param token: The encoded pending token.
    """
    response.set_cookie(
        auth_settings.MFA_PENDING_COOKIE_NAME,
        token,
        max_age=auth_settings.mfa.PENDING_TOKEN_LIFETIME_SECONDS,
        path="/",
        secure=auth_settings.COOKIE_SECURE,
        httponly=True,
        # "lax" keeps the cookie off cross-site POSTs, so a third-party page
        # cannot drive the verify route with a victim's half-finished sign-in.
        samesite="lax",
    )


def clear_pending_cookie(response: Response) -> None:
    """
    Remove the pending cookie, whether the sign-in completed or failed.

    :param response: The response being returned to the client.
    """
    response.delete_cookie(
        auth_settings.MFA_PENDING_COOKIE_NAME,
        path="/",
        secure=auth_settings.COOKIE_SECURE,
        httponly=True,
        samesite="lax",
    )


def read_pending_cookie(request: Request) -> str | None:
    """
    The pending token carried by a request, if any.

    :param request: The incoming request.
    :return: The encoded token, or ``None``.
    """
    return request.cookies.get(auth_settings.MFA_PENDING_COOKIE_NAME)
