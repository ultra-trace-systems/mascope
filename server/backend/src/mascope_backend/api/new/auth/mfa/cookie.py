"""
Transport for the pending token.

Carried in its own httpOnly cookie rather than the response body so the token is
never readable from JavaScript, matching how the session itself is carried. The
security attributes are taken from the session cookie's settings so the two
cannot drift apart; only the name and the lifetime differ.
"""

from fastapi import Request, Response

from mascope_backend.api.new.auth.config import auth_settings


def _security_attributes() -> dict:
    """
    The cookie security attributes, taken from the session cookie's settings so
    the two cannot drift apart - only the name and the lifetime differ. Shared
    by set and clear so a delete always names the same attributes as the cookie
    it removes. ``samesite`` is "lax" by default, which keeps the cookie off
    cross-site POSTs so a third-party page cannot drive the verify route with a
    victim's half-finished sign-in; an operator who tightens it to "strict"
    tightens this cookie with the session's.
    """
    return {
        "path": "/",
        "secure": auth_settings.COOKIE_SECURE,
        "httponly": auth_settings.COOKIE_HTTP_ONLY,
        "samesite": auth_settings.COOKIE_SAMESITE,
    }


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
        **_security_attributes(),
    )


def clear_pending_cookie(response: Response) -> None:
    """
    Remove the pending cookie, whether the sign-in completed or failed.

    :param response: The response being returned to the client.
    """
    response.delete_cookie(
        auth_settings.MFA_PENDING_COOKIE_NAME,
        **_security_attributes(),
    )


def read_pending_cookie(request: Request) -> str | None:
    """
    The pending token carried by a request, if any.

    :param request: The incoming request.
    :return: The encoded token, or ``None``.
    """
    return request.cookies.get(auth_settings.MFA_PENDING_COOKIE_NAME)
