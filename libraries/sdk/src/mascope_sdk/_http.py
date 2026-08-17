"""Internal HTTP helpers for the Mascope SDK.

This module provides low-level HTTP request functions with proper exception handling.
These are internal implementation details and should not be used directly by SDK users.
"""

import json
import time
from typing import Any

import requests
from loguru import logger
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import RequestException, Timeout

from .exceptions import (
    AuthenticationError,
    MascopeAPIError,
    MascopeConnectionError,
    MascopeTimeoutError,
    NotFoundError,
    ServerError,
    ValidationError,
)


# Default timeout values (connect, read) in seconds
DEFAULT_TIMEOUT = (30, 300)

#: Status codes that are safe to retry (transient server errors).
_RETRYABLE_STATUS_CODES = {502, 503, 504}

#: Default retry settings.
RETRY_MAX_ATTEMPTS = 4
RETRY_BACKOFF_BASE = 2  # seconds; delays will be 2, 4, 8, ...


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


def _raise_for_status(response: requests.Response, url: str) -> None:
    """Raise appropriate exception based on HTTP status code."""
    if response.ok:
        return

    status_code = response.status_code
    message = _extract_error_message(response)

    if status_code in (401, 403):
        raise AuthenticationError(
            message=f"{message}. Please check your API token.",
            status_code=status_code,
            url=url,
        )
    elif status_code == 404:
        raise NotFoundError(
            message=message,
            status_code=status_code,
            url=url,
        )
    elif status_code == 422:
        raise ValidationError(
            message=message,
            status_code=status_code,
            url=url,
        )
    elif status_code in (411, 413):
        # Length Required / Payload Too Large: a client error the request can
        # never satisfy by retrying - e.g. an upload over the per-upload cap.
        # Raise a terminal (non-retryable) error so callers fail fast with the
        # server's reason instead of retrying the same rejected request.
        raise ValidationError(
            message=message,
            status_code=status_code,
            url=url,
        )
    elif status_code is not None and status_code >= 500:
        raise ServerError(
            message=message,
            status_code=status_code,
            url=url,
        )
    else:
        raise MascopeAPIError(
            message=message,
            status_code=status_code,
            url=url,
        )


def _is_retryable(exc: Exception) -> bool:
    """Return True if the exception is transient and the request can be retried."""
    if isinstance(exc, (MascopeConnectionError, MascopeTimeoutError)):
        return True
    if isinstance(exc, ServerError) and exc.status_code in _RETRYABLE_STATUS_CODES:
        return True
    return False


def http_get(
    url: str,
    path: str,
    access_token: str,
    params: dict[str, Any] | None = None,
    stream: bool = False,
    timeout: tuple[int, int] = DEFAULT_TIMEOUT,
    service_name: str = "mascope_sdk",
    verify_ssl: bool = False,
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

    for attempt in range(1, RETRY_MAX_ATTEMPTS + 1):
        try:
            response = requests.get(
                full_url,
                params=params,
                headers=headers,
                verify=verify_ssl,
                timeout=timeout,
                stream=stream,
            )
            _raise_for_status(response, full_url)
            return response

        except Timeout as e:
            if attempt == RETRY_MAX_ATTEMPTS:
                raise MascopeTimeoutError(
                    f"Request timed out: {e}", url=full_url
                ) from e
            exc_for_log: Exception = e
        except RequestsConnectionError as e:
            if attempt == RETRY_MAX_ATTEMPTS:
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
            if not _is_retryable(e) or attempt == RETRY_MAX_ATTEMPTS:
                raise
            exc_for_log = e

        delay = RETRY_BACKOFF_BASE**attempt
        # INFO: transient retry that usually succeeds; the final failure raises
        logger.info(
            "GET {} failed (attempt {}/{}), retrying in {}s: {}",
            full_url,
            attempt,
            RETRY_MAX_ATTEMPTS,
            delay,
            exc_for_log,
        )
        time.sleep(delay)

    assert False, "unreachable"  # noqa: B011


def http_post(
    url: str,
    path: str,
    access_token: str,
    data: dict[str, Any],
    timeout: int | tuple[int, int] = 30,
    service_name: str = "mascope_sdk",
    verify_ssl: bool = False,
) -> requests.Response:
    """Send a POST request to the specified API endpoint.

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

    for attempt in range(1, RETRY_MAX_ATTEMPTS + 1):
        try:
            response = requests.post(
                full_url,
                json=data,
                headers=headers,
                verify=verify_ssl,
                timeout=timeout,
            )
            _raise_for_status(response, full_url)
            return response

        except Timeout as e:
            if attempt == RETRY_MAX_ATTEMPTS:
                raise MascopeTimeoutError(
                    f"Request timed out: {e}", url=full_url
                ) from e
            exc_for_log: Exception = e
        except RequestsConnectionError as e:
            if attempt == RETRY_MAX_ATTEMPTS:
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
            if not _is_retryable(e) or attempt == RETRY_MAX_ATTEMPTS:
                raise
            exc_for_log = e

        delay = RETRY_BACKOFF_BASE**attempt
        # INFO: transient retry that usually succeeds; the final failure raises
        logger.info(
            "POST {} failed (attempt {}/{}), retrying in {}s: {}",
            full_url,
            attempt,
            RETRY_MAX_ATTEMPTS,
            delay,
            exc_for_log,
        )
        time.sleep(delay)

    assert False, "unreachable"  # noqa: B011


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
