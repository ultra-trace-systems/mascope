"""
Cloudflare Turnstile on the sign-in form: a human check the web app can solve.

A Managed Challenge at the edge cannot guard the login: the web app signs in
with an XHR, and Cloudflare can only present a challenge on a document load,
so a challenged XHR comes back a 403 the page never shows. Turnstile moves the
check into the page instead. The sign-in form renders Cloudflare's widget,
which hands it a one-use token, and the login route has Cloudflare's siteverify
confirm that token before it looks at the password.

It is off unless the site turns it on, with two values from the site's
Turnstile widget:

- ``MASCOPE_TURNSTILE_SITE_KEY``, the public site key. Set means on. The login
  page reads it from ``GET /api/auth/login/options`` to render the widget.
- The secret key, in the compose secret ``turnstile_secret_key``
  (``.runtime/secrets/turnstile_secret_key.txt``, which the CLI creates empty).
  With the site key set and this file empty, every sign-in is refused: the
  check fails closed rather than letting a half-configured site sign people in
  with no check at all.

Only the password step is checked. The second-factor step needs the pending
token the password step issued, the SDK and the agents authenticate with bearer
tokens, and pairing and the socket never take a password, so none of those
see the widget.

https://developers.cloudflare.com/turnstile/get-started/server-side-validation/
"""

import os

import httpx
from fastapi import APIRouter, Request, status

from mascope_backend.api.lib.api_features import api_route
from mascope_backend.api.lib.exceptions.api_exceptions import CodedHTTPException
from mascope_backend.api.lib.rate_limit import client_ip
from mascope_backend.runtime import runtime


#: Cloudflare's token check. A token is single use and lives five minutes.
SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"

#: The ``action`` the sign-in widget is rendered with. Siteverify echoes it
#: back, so a token solved where the same widget is rendered for something
#: else is refused. Cloudflare's test keys echo none, and a token without one
#: is let through: the secret already ties the token to this widget.
TURNSTILE_ACTION = "login"

#: The longest token Cloudflare issues; anything longer is refused unsent.
MAX_TOKEN_LENGTH = 2048

#: How long the login waits for siteverify before refusing.
SITEVERIFY_TIMEOUT_SECONDS = 10.0

#: The form field the sign-in form sends the token in, the name Cloudflare's
#: widget gives its own hidden input.
TOKEN_FIELD = "cf-turnstile-response"

#: The token was missing, malformed, expired, spent or refused by Cloudflare.
#: The user solves the widget again.
LOGIN_CHALLENGE_FAILED = "login_challenge_failed"

#: The check could not be made: the secret is missing, or siteverify did not
#: answer. Nobody can sign in until the operator or Cloudflare fixes it.
LOGIN_CHALLENGE_UNAVAILABLE = "login_challenge_unavailable"


def turnstile_site_key() -> str | None:
    """
    The site's Turnstile site key, or ``None`` when Turnstile is off.

    Read on every call, so a test can switch it, and blank reads as unset: the
    compose file passes the variable through as empty when a site has none.

    :return: The site key, or ``None``.
    """
    key = os.environ.get("MASCOPE_TURNSTILE_SITE_KEY", "").strip()
    return key or None


def turnstile_secret_key() -> str | None:
    """
    The site's Turnstile secret key, or ``None`` when it has none.

    A missing file and an empty one both read as none: the CLI creates the
    file empty so that compose can start a site that has no Turnstile.

    :return: The secret key, or ``None``.
    """
    try:
        key = runtime.secret("TURNSTILE_SECRET_KEY_FILE", "turnstile_secret_key.txt")
    except (FileNotFoundError, IndexError):
        # IndexError: runtime.secret returns the first line, which an empty
        # file does not have.
        return None
    return key.strip() or None


class LoginChallengeFailedException(CodedHTTPException):
    """
    The sign-in's Turnstile token is missing or was refused (400).

    The code is what the sign-in form branches on: it resets the widget, so the
    user solves a fresh one. The detail is what the user reads.
    """

    error_code = LOGIN_CHALLENGE_FAILED

    def __init__(self):
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The check that you are human did not pass. Please try again.",
        )


class LoginChallengeUnavailableException(CodedHTTPException):
    """
    The sign-in's Turnstile token could not be checked (503).

    The site's secret is missing, or siteverify did not answer. Refused rather
    than let through: a check that is skipped whenever it breaks is no check.
    """

    error_code = LOGIN_CHALLENGE_UNAVAILABLE

    def __init__(self):
        super().__init__(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Signing in is unavailable: the check that you are human "
            "could not be made. Please try again later.",
        )


async def verify_turnstile(request: Request, token: str | None) -> None:
    """
    Confirm a sign-in's Turnstile token, when the site has Turnstile on.

    Does nothing when Turnstile is off. Otherwise the token must be present
    and Cloudflare must accept it for this site's login widget; anything else
    stops the sign-in before the password is checked.

    :param request: The login request, for the client's address.
    :param token: The token the form sent, if any.
    :raises LoginChallengeFailedException: The token is missing or refused.
    :raises LoginChallengeUnavailableException: The secret is not set up, or
        siteverify cannot be reached.
    """
    if turnstile_site_key() is None:
        return

    secret = turnstile_secret_key()
    if secret is None:
        runtime.logger.error(
            "Turnstile is on (MASCOPE_TURNSTILE_SITE_KEY is set) but the "
            "turnstile_secret_key secret is missing or empty, so every sign-in "
            "is refused. Put the widget's secret key in "
            ".runtime/secrets/turnstile_secret_key.txt (docs/maintaining.md)."
        )
        raise LoginChallengeUnavailableException()

    token = (token or "").strip()
    if not token or len(token) > MAX_TOKEN_LENGTH:
        raise LoginChallengeFailedException()

    payload = {"secret": secret, "response": token}
    ip = client_ip(request)
    if ip != "unknown":
        payload["remoteip"] = ip

    try:
        async with httpx.AsyncClient(timeout=SITEVERIFY_TIMEOUT_SECONDS) as client:
            response = await client.post(SITEVERIFY_URL, data=payload)
        response.raise_for_status()
        outcome = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        runtime.logger.warning(f"Turnstile siteverify failed: {exc!r}")
        raise LoginChallengeUnavailableException() from exc

    if outcome.get("success") is not True:
        runtime.logger.debug(
            f"Turnstile refused a sign-in token: {outcome.get('error-codes')}"
        )
        raise LoginChallengeFailedException()
    action = outcome.get("action")
    if action and action != TURNSTILE_ACTION:
        runtime.logger.debug(
            f"Turnstile token was for action {action!r}, not {TURNSTILE_ACTION!r}"
        )
        raise LoginChallengeFailedException()


login_options_router = APIRouter()


@login_options_router.get("/login/options", name="auth:login.options")
@api_route(public=True)
async def login_options_route():
    """
    What the sign-in form needs to know before anyone has signed in.

    Anonymous by design: the login page reads it to decide whether to render
    the Turnstile widget. The site key is public, it is in every page that
    renders the widget.

    :return: A message, and ``turnstile_site_key`` in the data: ``null`` when
        Turnstile is off.
    :rtype: dict
    """
    site_key = turnstile_site_key()
    return {
        "message": "Turnstile is on." if site_key else "Turnstile is off.",
        "data": {"turnstile_site_key": site_key},
    }
