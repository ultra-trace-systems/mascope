"""Internal HTTP helpers for the Mascope SDK.

This module provides low-level HTTP request functions with proper exception handling.
These are internal implementation details and should not be used directly by SDK users.
"""

import json
import time
from collections.abc import Callable
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import requests
from loguru import logger
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import ConnectTimeout, RequestException, Timeout
from urllib3.exceptions import ConnectTimeoutError, NewConnectionError

from .exceptions import (
    AuthenticationError,
    ConflictError,
    MascopeAPIError,
    MascopeConnectionError,
    MascopeTimeoutError,
    NotFoundError,
    PermissionDeniedError,
    ServerError,
    ValidationError,
)


# Default timeout values (connect, read) in seconds
DEFAULT_TIMEOUT = (30, 300)

#: Status codes that are safe to retry (transient server errors). 507 is the
#: server refusing an upload because its disk is nearly full: a squeeze that
#: clears when space is freed, and instrument raw data is irreplaceable, so a
#: client must keep trying rather than set the file aside.
_RETRYABLE_STATUS_CODES = {502, 503, 504, 507}

#: Client errors (4xx) that a later identical request can still satisfy, so
#: they stay retryable while every other 4xx is terminal. 408 and 425 are the
#: server asking for the request again; 429 is a rate limit, which nginx
#: applies to /api/ (see docs/hosting.md) and which clears on its own; 409 is
#: the TUS offset disagreement the chunk loop resolves by resuming from the
#: server's offset.
_TRANSIENT_CLIENT_STATUS_CODES = {408, 409, 425, 429}

#: Of those, the ones a caller may simply send again. 409 is excluded: an
#: offset disagreement is resolved by resuming from the offset the server
#: reports, which only the TUS chunk loop knows how to do - repeating the
#: request unchanged would just disagree again.
_RETRYABLE_CLIENT_STATUS_CODES = _TRANSIENT_CLIENT_STATUS_CODES - {409}

#: Of those, the ones that show the server did not act on the request, so a
#: write that must be carried out at most once is still sent again on them.
#: nginx answers 429 on /api/ before the request reaches the app, and the
#: app's rate limit refuses before the route runs; 425 is the server declining
#: to process a request it may see replayed. 408 is left out: a proxy can send
#: it after it read the request, and then it is unclear whether it was acted on.
_NOT_ACTED_ON_STATUS_CODES = {425, 429}

#: Default retry settings.
RETRY_MAX_ATTEMPTS = 4
RETRY_BACKOFF_BASE = 2  # seconds; delays will be 2, 4, 8, ...

#: The longest ``Retry-After`` the SDK waits out between two attempts, in
#: seconds. A server asking for longer is raised at once: the few attempts left
#: would not see the limit clear, and a script should not hang silently on it.
RETRY_AFTER_MAX = 60


def _extract_error_message(response: requests.Response) -> str:
    """Extract error message from API response."""
    try:
        content = response.json()
        # Try common error message locations. The backend's canonical error
        # shape is {"error": <human message>, "detail": {"error_id": ...}} -
        # prefer the human-readable "error" field over the opaque detail dict.
        if isinstance(content, dict):
            error = content.get("error")
            if isinstance(error, str) and error:
                return error
            if "message" in content:
                return content["message"]
            if "detail" in content:
                detail = content["detail"]
                if isinstance(detail, dict):
                    return detail.get("error_message", str(detail))
                return str(detail)
        return response.text[:200] if response.text else "Unknown error"
    except (json.JSONDecodeError, ValueError):
        return response.text[:200] if response.text else "Unknown error"


def _error_body(response: requests.Response) -> Any:
    """The parsed JSON body of an error response, or None if it is not JSON."""
    try:
        return response.json()
    except (json.JSONDecodeError, ValueError):
        return None


def _raise_for_status(response: requests.Response, url: str) -> None:
    """Raise appropriate exception based on HTTP status code.

    Every exception keeps the parsed body (``body``, and its ``detail`` and
    ``code``), which is where the server puts what a client reacts to: the
    message alone is written for a person.
    """
    if response.ok:
        return
    raise status_error(
        response.status_code,
        _extract_error_message(response),
        url,
        body=_error_body(response),
    )


def status_error(
    status_code: int | None, message: str, url: str, body: Any = None
) -> MascopeAPIError:
    """The exception an error answer of this status is raised as.

    Also what a read that reports a failure inside a successful answer - one
    sample of a batch's peak lists - turns that failure into, so it reads as
    the failed request of that sample would have.

    :param status_code: The answer's HTTP status.
    :param message: What the server said.
    :param url: The URL asked.
    :param body: The answer's parsed JSON body, kept on the exception.
    :return: The exception, not raised.
    """
    if status_code == 403:
        # The token was accepted; its user lacks the role. Checking the token
        # would not help, so the hint 401 gets is left out here.
        return PermissionDeniedError(
            message=message,
            status_code=status_code,
            url=url,
            body=body,
        )
    elif status_code == 401:
        return AuthenticationError(
            message=f"{message}. Please check your API token.",
            status_code=status_code,
            url=url,
            body=body,
        )
    elif status_code == 404:
        return NotFoundError(
            message=message,
            status_code=status_code,
            url=url,
            body=body,
        )
    elif status_code == 422:
        return ValidationError(
            message=message,
            status_code=status_code,
            url=url,
            body=body,
        )
    elif status_code == 409:
        # Transient for the TUS chunk loop, which resumes from the offset the
        # server reports, and final for a write the server refuses over the
        # state it is in: either way the caller reads why from the detail.
        return ConflictError(
            message=message,
            status_code=status_code,
            url=url,
            body=body,
        )
    elif (
        status_code is not None
        and 400 <= status_code < 500
        and status_code not in _TRANSIENT_CLIENT_STATUS_CODES
    ):
        # The server understood the request and rejected it: an upload over the
        # per-upload cap (413), a length it must declare up front (411), a name
        # that does not identify an instrument (400). Repeating it byte for byte
        # cannot change the answer, so raise a terminal error and let the caller
        # report the server's reason once instead of retrying into a timeout.
        return ValidationError(
            message=message,
            status_code=status_code,
            url=url,
            body=body,
        )
    elif status_code is not None and status_code >= 500:
        return ServerError(
            message=message,
            status_code=status_code,
            url=url,
            body=body,
        )
    else:
        return MascopeAPIError(
            message=message,
            status_code=status_code,
            url=url,
            body=body,
        )


def _is_retryable(exc: Exception) -> bool:
    """Return True if the exception is transient and the request can be retried."""
    if isinstance(exc, (MascopeConnectionError, MascopeTimeoutError)):
        return True
    if isinstance(exc, ServerError) and exc.status_code in _RETRYABLE_STATUS_CODES:
        return True
    if (
        isinstance(exc, MascopeAPIError)
        and exc.status_code in _RETRYABLE_CLIENT_STATUS_CODES
    ):
        # A rate limit or a "send it again" clears on its own. Without this a
        # chunk refused mid-transfer ended the whole upload, and the caller
        # started the file again from its first byte rather than resuming.
        return True
    return False


def _never_sent(exc: RequestsConnectionError) -> bool:
    """True if the connection failed before any of the request was sent.

    ``requests`` raises the same ``ConnectionError`` for a refused or
    unresolvable connection and for one dropped after the request went out, so
    the urllib3 cause tells them apart: only a connect-phase failure (a new
    connection that could not be made, or timed out while being made) proves
    the server never saw the request.
    """
    if isinstance(exc, ConnectTimeout):
        return True
    cause = exc.args[0] if exc.args else None
    reason = getattr(cause, "reason", cause)
    return isinstance(reason, (NewConnectionError, ConnectTimeoutError))


def _retry_after(response: requests.Response) -> float | None:
    """The seconds an error answer's ``Retry-After`` asks to wait, if it says.

    The header is either a number of seconds or an HTTP date; one that is
    neither is ignored, and the backoff alone decides the wait.
    """
    if response.ok:
        return None
    value = response.headers.get("Retry-After")
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())


def _send_with_retries(
    method: str,
    full_url: str,
    send: Callable[[], requests.Response],
    attempts: int,
    retry_timeouts: bool = True,
    once: bool = False,
) -> requests.Response:
    """Send a request, retrying what is transient, and map its status.

    The wait between two attempts is the backoff, or the answer's
    ``Retry-After`` when that is longer; one longer than
    :data:`RETRY_AFTER_MAX` raises the answer at once.

    :param method: The HTTP method, for the log line of a retry.
    :type method: str
    :param full_url: The URL, for the log line and the exceptions.
    :type full_url: str
    :param send: Sends the request once and returns the response.
    :type send: Callable[[], requests.Response]
    :param attempts: How many times to try a transient failure.
    :type attempts: int
    :param retry_timeouts: Try a read that timed out again. False for a read
        the server takes minutes to answer, which would only start over.
    :type retry_timeouts: bool
    :param once: The request must be carried out at most once. Only the
        failures that show the server did not act on it are tried again: a
        connection that could not be made, a 425 or a 429. A read timeout, a
        connection lost after the send and a 5xx can come after the server
        acted, and raise at the first.
    :type once: bool
    :return: The response of the first attempt that succeeded.
    :rtype: requests.Response
    """
    for attempt in range(1, attempts + 1):
        retry_after = None
        try:
            response = send()
            retry_after = _retry_after(response)
            _raise_for_status(response, full_url)
            return response

        except Timeout as e:
            if (
                attempt == attempts
                or not retry_timeouts
                or (once and not isinstance(e, ConnectTimeout))
            ):
                raise MascopeTimeoutError(
                    f"Request timed out: {e}", url=full_url
                ) from e
            exc_for_log: Exception = e
        except RequestsConnectionError as e:
            if attempt == attempts or (once and not _never_sent(e)):
                raise MascopeConnectionError(
                    (
                        "Could not connect. "
                        f"Please check the URL and your network connection: {e}"
                    ),
                    url=full_url,
                ) from e
            exc_for_log = e
        except RequestException as e:
            # Non-transient request error (SSL, proxy, invalid URL, …)
            raise MascopeConnectionError(
                f"Request failed: {e}",
                url=full_url,
            ) from e
        except Exception as e:
            if not _is_retryable(e) or attempt == attempts:
                raise
            if once and not (
                isinstance(e, MascopeAPIError)
                and e.status_code in _NOT_ACTED_ON_STATUS_CODES
            ):
                raise
            if retry_after is not None and retry_after > RETRY_AFTER_MAX:
                raise
            exc_for_log = e

        delay = max(RETRY_BACKOFF_BASE**attempt, retry_after or 0)
        # INFO: transient retry that usually succeeds; the final failure raises
        logger.info(
            "{} {} failed (attempt {}/{}), retrying in {}s: {}",
            method,
            full_url,
            attempt,
            attempts,
            delay,
            exc_for_log,
        )
        time.sleep(delay)

    assert False, "unreachable"  # noqa: B011


def http_get(
    url: str,
    path: str,
    access_token: str,
    params: dict[str, Any] | None = None,
    stream: bool = False,
    timeout: tuple[int, int] = DEFAULT_TIMEOUT,
    service_name: str = "mascope_sdk",
    verify_ssl: bool = False,
    max_attempts: int | None = None,
    retry_timeouts: bool = True,
) -> requests.Response:
    """Send a GET request to the specified API endpoint.

    :param url: The base URL of the server.
    :type url: str
    :param path: The specific API path to be appended to the base URL.
    :type path: str
    :param access_token: Authorization token for API access.
    :type access_token: str
    :param params: Query parameters to include in the request.
    :type params: dict[str, Any], optional
    :param stream: Whether to stream the response content.
    :type stream: bool, optional
    :param timeout: Tuple of (connect timeout, read timeout) in seconds.
    :type timeout: tuple[int, int], optional
    :param service_name: Service name for request header.
    :type service_name: str, optional
    :param verify_ssl: Whether to verify SSL certificates.
    :type verify_ssl: bool, optional
    :param max_attempts: How many times to try a transient failure before
        raising it; :data:`RETRY_MAX_ATTEMPTS` when None. 1 for a request whose
        failure the caller tolerates, so it costs one attempt and no backoff.
    :type max_attempts: int, optional
    :param retry_timeouts: Try a request that timed out again, as any other
        transient failure. False for a read the server builds in full before
        it answers: sent again, it would only be built again from the start.
    :type retry_timeouts: bool, optional
    :return: The response object.
    :rtype: requests.Response
    :raises AuthenticationError: If authentication fails (401/403).
    :raises NotFoundError: If resource is not found (404).
    :raises ValidationError: If request validation fails (422).
    :raises ServerError: If server error occurs (5xx).
    :raises MascopeAPIError: For other HTTP errors.
    :raises MascopeConnectionError: If unable to connect.
    :raises MascopeTimeoutError: If request times out.
    """
    full_url = f"{url}/api/{path}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "X-Service-Name": service_name,
    }

    return _send_with_retries(
        "GET",
        full_url,
        lambda: requests.get(
            full_url,
            params=params,
            headers=headers,
            verify=verify_ssl,
            timeout=timeout,
            stream=stream,
        ),
        max_attempts or RETRY_MAX_ATTEMPTS,
        retry_timeouts=retry_timeouts,
    )


def http_post(
    url: str,
    path: str,
    access_token: str,
    data: dict[str, Any],
    timeout: int | tuple[int, int] = 30,
    service_name: str = "mascope_sdk",
    verify_ssl: bool = False,
    max_attempts: int | None = None,
    once: bool = False,
) -> requests.Response:
    """Send a POST request to the specified API endpoint.

    A POST is sent again on a timeout, a lost connection or a 502 to 504, as
    a read is. That is safe only for a POST the server can take twice: a read
    sent as a POST, or a write that sending again cannot repeat. A write the
    server would carry out twice - a verdict appended, a run launched - is
    sent with ``once=True``, so a lost answer raises instead of doing it
    again. It is still sent again when the server shows it did not act: a
    connection that could not be made, a 425 or a 429.

    :param url: The base URL of the server.
    :type url: str
    :param path: The specific API path to be appended to the base URL.
    :type path: str
    :param access_token: Authorization token for API access.
    :type access_token: str
    :param data: The data payload to send in the POST request.
    :type data: dict[str, Any]
    :param timeout: Request timeout in seconds.
    :type timeout: int | tuple[int, int], optional
    :param service_name: Service name for request header.
    :type service_name: str, optional
    :param verify_ssl: Whether to verify SSL certificates.
    :type verify_ssl: bool, optional
    :param max_attempts: How many times to send it on a transient failure;
        :data:`RETRY_MAX_ATTEMPTS` when None.
    :type max_attempts: int, optional
    :param once: A write that must not be carried out twice: sent again only
        on a failure that shows the server did not act on it.
    :type once: bool, optional
    :return: The response object.
    :rtype: requests.Response
    :raises AuthenticationError: If authentication fails (401/403).
    :raises NotFoundError: If resource is not found (404).
    :raises ValidationError: If request validation fails (422).
    :raises ConflictError: If the server refuses it over its state (409).
    :raises ServerError: If server error occurs (5xx).
    :raises MascopeAPIError: For other HTTP errors.
    :raises MascopeConnectionError: If unable to connect.
    :raises MascopeTimeoutError: If request times out.
    """
    full_url = f"{url}/api/{path}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "X-Service-Name": service_name,
    }

    return _send_with_retries(
        "POST",
        full_url,
        lambda: requests.post(
            full_url,
            json=data,
            headers=headers,
            verify=verify_ssl,
            timeout=timeout,
        ),
        max_attempts or RETRY_MAX_ATTEMPTS,
        once=once,
    )


def http_request(
    method: str,
    url: str,
    path: str,
    access_token: str,
    data: Any = None,
    params: dict[str, Any] | None = None,
    timeout: int | tuple[int, int] = 30,
    service_name: str = "mascope_sdk",
    verify_ssl: bool = False,
    max_attempts: int | None = None,
    once: bool = False,
) -> requests.Response:
    """Send a request of any method, for the writes that are not a POST.

    A PATCH or a DELETE takes the same headers, the same status mapping and
    the same retries as :func:`http_post`, and the same ``max_attempts`` and
    ``once``.

    :param method: The HTTP method, such as ``"PATCH"`` or ``"DELETE"``.
    :type method: str
    :param url: The base URL of the server.
    :type url: str
    :param path: The specific API path to be appended to the base URL.
    :type path: str
    :param access_token: Authorization token for API access.
    :type access_token: str
    :param data: The JSON body, if the request has one.
    :type data: Any, optional
    :param params: Query parameters to include in the request.
    :type params: dict[str, Any], optional
    :param timeout: Request timeout in seconds.
    :type timeout: int | tuple[int, int], optional
    :param service_name: Service name for request header.
    :type service_name: str, optional
    :param verify_ssl: Whether to verify SSL certificates.
    :type verify_ssl: bool, optional
    :param max_attempts: How many times to send it on a transient failure;
        :data:`RETRY_MAX_ATTEMPTS` when None.
    :type max_attempts: int, optional
    :param once: A write that must not be carried out twice: sent again only
        on a failure that shows the server did not act on it.
    :type once: bool, optional
    :return: The response object.
    :rtype: requests.Response
    :raises PermissionDeniedError: If the token's user lacks the role (403).
    :raises AuthenticationError: If authentication fails (401).
    :raises NotFoundError: If resource is not found (404).
    :raises ValidationError: If request validation fails (4xx).
    :raises ServerError: If server error occurs (5xx).
    :raises MascopeConnectionError: If unable to connect.
    :raises MascopeTimeoutError: If request times out.
    """
    full_url = f"{url}/api/{path}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "X-Service-Name": service_name,
    }
    return _send_with_retries(
        method,
        full_url,
        lambda: requests.request(
            method,
            full_url,
            json=data,
            params=params,
            headers=headers,
            verify=verify_ssl,
            timeout=timeout,
        ),
        max_attempts or RETRY_MAX_ATTEMPTS,
        once=once,
    )


def http_post_file(
    url: str,
    path: str,
    access_token: str,
    filepath: str,
    timeout: int = 60,
    service_name: str = "mascope_sdk",
    verify_ssl: bool = False,
) -> requests.Response:
    """Send a POST request with a file to upload.

    :param url: The base URL of the server.
    :type url: str
    :param path: The specific API path to be appended to the base URL.
    :type path: str
    :param access_token: Authorization token for API access.
    :type access_token: str
    :param filepath: Path to the file to be uploaded.
    :type filepath: str
    :param timeout: Request timeout in seconds.
    :type timeout: int, optional
    :param service_name: Service name for request header.
    :type service_name: str, optional
    :param verify_ssl: Whether to verify SSL certificates.
    :type verify_ssl: bool, optional
    :return: The response object.
    :rtype: requests.Response
    :raises AuthenticationError: If authentication fails (401/403).
    :raises NotFoundError: If resource is not found (404).
    :raises ServerError: If server error occurs (5xx).
    :raises MascopeAPIError: For other HTTP errors.
    :raises MascopeConnectionError: If unable to connect.
    :raises MascopeTimeoutError: If request times out.
    :raises FileNotFoundError: If the file does not exist.
    """
    full_url = f"{url}/api/{path}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "X-Service-Name": service_name,
    }

    try:
        with open(filepath, "rb") as file:
            response = requests.post(
                full_url,
                files=[("files", file)],
                headers=headers,
                verify=verify_ssl,
                timeout=timeout,
            )
        _raise_for_status(response, full_url)
        return response

    except Timeout as e:
        raise MascopeTimeoutError(f"Request timed out: {e}", url=full_url) from e
    except RequestException as e:
        raise MascopeConnectionError(
            f"Could not connect. Please check the URL and your network connection: {e}",
            url=full_url,
        ) from e
