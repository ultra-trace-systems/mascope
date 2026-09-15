from fastapi import Response
from fastapi_users.authentication import CookieTransport

from mascope_backend.api.new.auth.config import auth_settings


# Cookie-based authentication for web app (Mascope web-based interface)
cookie_transport = CookieTransport(
    cookie_name=auth_settings.COOKIE_NAME,
    cookie_max_age=auth_settings.COOKIE_MAX_AGE_SECONDS,
    cookie_secure=auth_settings.COOKIE_SECURE,
    cookie_httponly=auth_settings.COOKIE_HTTP_ONLY,
    cookie_samesite=auth_settings.COOKIE_SAMESITE,
)


def session_token_from_response(response: Response) -> str | None:
    """
    The session token this transport just set on ``response``, if any.

    Reads every ``Set-Cookie`` header via ``getlist`` rather than only the
    first. A login response can carry more than one - the second-factor route
    clears the pending cookie beside the session it issues - and
    ``headers["set-cookie"]`` returns whichever was appended first, so picking
    that one couples the caller to the order two unrelated lines happen to run
    in.

    Returns ``None`` when no session cookie was set, so a caller can say so
    plainly instead of raising an ``IndexError`` for a broad ``except`` to bury.

    :param response: The response returned by the login backend.
    :return: The encoded JWT, or ``None``.
    """
    prefix = f"{auth_settings.COOKIE_NAME}="
    for header in response.headers.getlist("set-cookie"):
        if header.startswith(prefix):
            return header[len(prefix) :].split(";")[0]
    return None
