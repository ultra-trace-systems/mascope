"""Internal HTTP helpers for Mascope agents (file-agent).

These are low-level request wrappers used by agent services that upload
files to the Mascope backend. They are NOT part of the public SDK API.
New user-facing code should use :class:`MascopeClient` instead.
"""

import base64
import contextlib
import json
import os
import sys
import time

import requests
import urllib3
from loguru import logger
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import RequestException, Timeout

from ._http import _is_retryable, _raise_for_status
from .exceptions import (
    MascopeAPIError,
    MascopeConnectionError,
    MascopeTimeoutError,
    TusNotSupportedError,
)


# Suppress InsecureRequestWarning from urllib3, which fires only when an agent
# has been configured with TLS verification off (VERIFY_TLS = False).
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Default service name sent in request headers.
# Agents override this at the package level: mascope_sdk.SERVICE_NAME = "file-agent"
SERVICE_NAME = "mascope_sdk"

# Whether agent requests verify the server's TLS certificate. On by default;
# an agent overrides it at the package level for a self-signed deployment:
# ``mascope_sdk.VERIFY_TLS = False``. Read per request via _get_verify() so the
# agent can set it after import, exactly like SERVICE_NAME.
VERIFY_TLS = True

#: Version of the agent making the requests, sent as the ``X-Agent-Version``
#: header on every request so the server can show which release each paired
#: machine runs. None, the SDK's own default, sends no header. An agent sets it
#: at the package level exactly like SERVICE_NAME:
#: ``mascope_sdk.AGENT_VERSION = "1.2.3"``. A server that does not know the
#: header ignores it.
AGENT_VERSION = None

#: Name of the request header that carries AGENT_VERSION.
AGENT_VERSION_HEADER = "X-Agent-Version"

#: Path of the device-token renewal endpoint, relative to /api/.
RENEW_TOKEN_PATH = "auth/devices/token"

#: Path of the backend's TUS upload endpoint, relative to /api/.
TUS_UPLOAD_PATH = "sample/files/upload/tus"

#: Bytes sent per PATCH request. Each chunk is one HTTP request, so keep it
#: safely under reverse-proxy body limits (Cloudflare caps request bodies
#: at 100 MB) while staying large enough that overhead is negligible.
TUS_CHUNK_SIZE = 50 * 1024**2

#: Floor for adaptive chunk shrinking. A chunk that repeatedly dies
#: mid-request is halved down to this floor: some server/proxy chains
#: abort large request bodies (observed with tuspyserver 4.1.3 behind a
#: Cloudflare tunnel, where PATCH bodies over ~25 MB were cut while
#: smaller ones passed), and smaller chunks get through where a fixed
#: size would fail forever.
TUS_MIN_CHUNK_SIZE = 5 * 1024**2

#: Consecutive failed chunk attempts before the upload is abandoned.
#: Generous enough that halving from TUS_CHUNK_SIZE can reach the floor.
TUS_MAX_ATTEMPTS = 6

#: Base for the exponential backoff between resumed chunk attempts
#: (2 s, 4 s, 8 s - matching the retry policy in ``_http``).
TUS_RETRY_BACKOFF_BASE = 2


def _get_service_name() -> str:
    """Return the current SERVICE_NAME from the package namespace."""
    pkg = sys.modules.get("mascope_sdk")
    if pkg is not None:
        return getattr(pkg, "SERVICE_NAME", SERVICE_NAME)
    return SERVICE_NAME


def _get_verify():
    """Return the current VERIFY_TLS from the package namespace."""
    pkg = sys.modules.get("mascope_sdk")
    if pkg is not None:
        return getattr(pkg, "VERIFY_TLS", VERIFY_TLS)
    return VERIFY_TLS


def _get_agent_version() -> str | None:
    """Return the current AGENT_VERSION from the package namespace."""
    pkg = sys.modules.get("mascope_sdk")
    if pkg is not None:
        return getattr(pkg, "AGENT_VERSION", AGENT_VERSION)
    return AGENT_VERSION


def _agent_headers(access_token: str) -> dict:
    """The headers every authenticated agent request carries.

    The bearer token, the service name the token must be scoped to, and the
    agent's version when one is set.
    """
    headers = {
        "Authorization": f"Bearer {access_token}",
        "X-Service-Name": _get_service_name(),
    }
    version = _get_agent_version()
    if version:
        headers[AGENT_VERSION_HEADER] = str(version)
    return headers


def _sanitize_upload_filename(upload_filename: str) -> str:
    """Validate that an upload filename carries no path components.

    :param upload_filename: The filename requested by the caller
    :type upload_filename: str
    :return: The validated filename
    :rtype: str
    :raises ValueError: if the name contains path components.
    """
    from pathlib import PurePosixPath, PureWindowsPath

    sanitized = PurePosixPath(PureWindowsPath(upload_filename).name).name
    if sanitized != upload_filename:
        raise ValueError(
            f"upload_filename contains path components: {upload_filename!r}"
        )
    return sanitized


def api_post_file(
    url: str,
    path: str,
    access_token: str,
    filepath: str,
    upload_filename: str | None = None,
    timezone: str | None = None,
) -> requests.Response:
    """Send a POST request with a file upload.

    :param url: The base URL of the server.
    :param path: The API path to append to the base URL.
    :param access_token: Authorization token for API access.
    :param filepath: Path to the file to upload.
    :param upload_filename: Optional filename override for the uploaded file.
        If provided, the server will see this filename instead of the one on disk.
    :type upload_filename: str, optional
    :param timezone: Optional IANA timezone of the uploading machine, used by
        the converter to resolve the acquisition time to UTC. Omitted when the
        machine could not name its zone; the server then falls back to its own.
    :type timezone: str, optional
    :return: The response object on success.
    :rtype: requests.Response
    :raises ValueError: if ``upload_filename`` contains path components.
    :raises MascopeTimeoutError: if the request times out.
    :raises MascopeConnectionError: if the server cannot be reached.
    :raises MascopeAPIError: on an error response; the concrete subclass
        (``AuthenticationError``, ``NotFoundError``, ``ValidationError``,
        ``ServerError``) and message carry the specific cause so callers can
        act on it (e.g. not retry on a rejected token).
    """
    full_url = url + "/api/" + path
    headers = _agent_headers(access_token)
    with open(filepath, "rb") as file:
        if upload_filename:
            files = [("files", (_sanitize_upload_filename(upload_filename), file))]
        else:
            files = [("files", file)]
        try:
            resp = requests.post(
                full_url,
                files=files,
                data={"timezone": timezone} if timezone else None,
                headers=headers,
                verify=_get_verify(),
                timeout=60,
            )
        except Timeout as e:
            raise MascopeTimeoutError(
                "The upload request timed out.", url=full_url
            ) from e
        except RequestException as e:
            raise MascopeConnectionError(
                "Could not connect to the server. Please check the URL "
                f"and your network connection ({e.__class__.__name__}).",
                url=full_url,
            ) from e

    # Raises a typed MascopeAPIError subclass carrying the server's message.
    _raise_for_status(resp, full_url)

    try:
        message = json.loads(resp.content).get("message", None)
    except (json.JSONDecodeError, AttributeError):
        message = None
    if message is not None:
        logger.debug(message)
    return resp


def _tus_headers(access_token: str) -> dict:
    """Common headers for every TUS request."""
    return {**_agent_headers(access_token), "Tus-Resumable": "1.0.0"}


def _tus_offset(upload_url: str, access_token: str) -> int | None:
    """Ask the server how many bytes of an upload it has (TUS HEAD).

    :param upload_url: Full URL of the upload resource
    :type upload_url: str
    :param access_token: Authorization token for API access
    :type access_token: str
    :return: The server-side offset, or None if it cannot be determined
    :rtype: int | None
    """
    try:
        resp = requests.head(
            upload_url,
            headers=_tus_headers(access_token),
            verify=_get_verify(),
            timeout=30,
        )
    except RequestException:
        return None
    if resp.status_code != 200:
        return None
    try:
        return int(resp.headers["Upload-Offset"])
    except (KeyError, ValueError):
        return None


def _tus_delete(upload_url: str, access_token: str) -> None:
    """Best-effort removal of an abandoned upload resource (TUS DELETE).

    Failures are ignored: the backend wipes its temp directory on every
    startup, so a resource that could not be deleted is cleaned up then.
    """
    with contextlib.suppress(RequestException):
        requests.delete(
            upload_url,
            headers=_tus_headers(access_token),
            verify=_get_verify(),
            timeout=30,
        )


def api_renew_agent_token(url: str, access_token: str) -> tuple[str, int]:
    """Rotate a paired agent's device token before it expires.

    Calls the backend's device-token renewal endpoint with the current token
    and returns a fresh one. The agent persists the new token and schedules the
    next renewal from the returned lifetime. A server that has no renewal
    endpoint (older release) raises :class:`TusNotSupportedError` so the caller
    can keep its existing token; an expired or revoked token raises
    :class:`AuthenticationError` (the machine must re-pair).

    :param url: The base URL of the server.
    :param access_token: The agent's current device token.
    :return: ``(new_token, expires_in_seconds)``.
    :rtype: tuple[str, int]
    :raises TusNotSupportedError: the server has no renewal endpoint (404) -
        keep the current token.
    :raises AuthenticationError: the current token is expired or revoked.
    :raises MascopeTimeoutError: if the request times out.
    :raises MascopeConnectionError: if the server cannot be reached.
    :raises MascopeAPIError: on any other error response.
    """
    full_url = f"{url}/api/{RENEW_TOKEN_PATH}"
    headers = _agent_headers(access_token)
    try:
        resp = requests.post(
            full_url, headers=headers, verify=_get_verify(), timeout=30
        )
    except Timeout as e:
        raise MascopeTimeoutError("The renewal request timed out.", url=full_url) from e
    except RequestException as e:
        raise MascopeConnectionError(
            "Could not connect to the server for token renewal "
            f"({e.__class__.__name__}).",
            url=full_url,
        ) from e

    if resp.status_code == 404:
        # No renewal endpoint on this server (older release): the caller keeps
        # its current long-lived token, exactly as the tus fallback does.
        raise TusNotSupportedError(
            "The server does not support device-token renewal.",
            status_code=404,
            url=full_url,
        )

    # Raises the typed subclass (AuthenticationError on 401 for an expired or
    # revoked token) carrying the server's message.
    _raise_for_status(resp, full_url)

    data = (resp.json() or {}).get("data") or {}
    new_token = data.get("access_token")
    if not new_token:
        raise MascopeAPIError(
            "The renewal response contained no token.",
            status_code=resp.status_code,
            url=full_url,
        )
    return new_token, int(data.get("expires_in", 0))


def api_post_file_tus(
    url: str,
    access_token: str,
    filepath: str,
    upload_filename: str | None = None,
    chunk_size: int = TUS_CHUNK_SIZE,
    timezone: str | None = None,
    instrument: str | None = None,
) -> None:
    """Upload a file with the resumable TUS protocol.

    Creates an upload resource on the backend's TUS endpoint and sends the
    file in ``chunk_size`` PATCH requests. A failed chunk is retried from
    the server-confirmed offset (TUS HEAD), so a network drop mid-file
    costs at most one chunk, not the whole upload - and no single request
    body ever exceeds ``chunk_size``, which keeps uploads of any size
    within reverse-proxy body limits. Chunks that fail on connection
    errors are halved down to ``TUS_MIN_CHUNK_SIZE``, so servers or
    proxies that abort large request bodies still make progress. The
    backend processes the file when the last chunk arrives.

    :param url: The base URL of the server.
    :param access_token: Authorization token for API access.
    :param filepath: Path to the file to upload.
    :param upload_filename: Optional filename override for the uploaded file.
        If provided, the server will see this filename instead of the one on disk.
    :type upload_filename: str, optional
    :param chunk_size: Bytes per PATCH request.
    :type chunk_size: int, optional
    :param timezone: Optional IANA timezone of the uploading machine, used by
        the converter to resolve the acquisition time to UTC. Omitted when the
        machine could not name its zone; the server then falls back to its own.
    :type timezone: str, optional
    :param instrument: Optional name of the instrument the agent is configured
        to file uploads under. Sent as upload metadata next to the on-disk file
        name; a server that does not read it ignores it.
    :type instrument: str, optional
    :raises ValueError: if ``upload_filename`` contains path components.
    :raises AuthenticationError: if the credential is rejected (401), e.g. a
        revoked device, an expired device token, or a deployment that accepts
        only paired agent credentials. There is no fallback: the caller must
        report it rather than retry against another endpoint.
    :raises MascopeTimeoutError: if a request times out (after retries).
    :raises MascopeConnectionError: if the server cannot be reached (after retries).
    :raises MascopeAPIError: on an error response; the concrete subclass
        (``AuthenticationError``, ``NotFoundError``, ``ValidationError``,
        ``ServerError``) and message carry the specific cause, for the
        creation request and the chunk transfer alike.
    """
    if upload_filename:
        filename = _sanitize_upload_filename(upload_filename)
    else:
        filename = os.path.basename(filepath)
    size = os.path.getsize(filepath)

    def b64(value: str) -> str:
        return base64.b64encode(value.encode("utf-8")).decode("ascii")

    # Create the upload resource. The server requires both filename and
    # filetype in the metadata; the type is not used for processing. The
    # timezone rides along when the machine could name it - an older server
    # ignores metadata keys it does not read, so sending it is safe.
    metadata = [
        f"filename {b64(filename)}",
        f"filetype {b64('application/octet-stream')}",
    ]
    if timezone:
        metadata.append(f"timezone {b64(timezone)}")
    # The name on disk before any configured prefix or suffix, and the
    # instrument the agent is configured to file uploads under. A current
    # server reads neither and ignores them; a later one can route on them
    # instead of on the file name.
    metadata.append(f"source_filename {b64(os.path.basename(filepath))}")
    if instrument:
        metadata.append(f"instrument {b64(instrument)}")
    create_url = f"{url}/api/{TUS_UPLOAD_PATH}/"
    create_headers = {
        **_tus_headers(access_token),
        "Upload-Length": str(size),
        "Upload-Metadata": ",".join(metadata),
    }
    try:
        resp = requests.post(
            create_url, headers=create_headers, verify=_get_verify(), timeout=60
        )
    except Timeout as e:
        raise MascopeTimeoutError(
            "The upload request timed out.", url=create_url
        ) from e
    except RequestException as e:
        raise MascopeConnectionError(
            "Could not connect to the server. Please check the URL "
            f"and your network connection ({e.__class__.__name__}).",
            url=create_url,
        ) from e
    # Every error keeps its own type, creation included. A 401 here is a
    # rejected credential - a revoked device, an expired device token, or a
    # deployment that requires paired devices - and callers must surface it
    # as such; it was once read as "this server predates token-accessible
    # TUS uploads", which sent agents to the capped legacy endpoint and
    # reported a server-version problem instead of the credential one.
    _raise_for_status(resp, create_url)

    # The Location header's host/scheme depend on proxy headers; only its
    # upload id is trustworthy. Address the upload via our own base URL.
    location = resp.headers.get("Location", "")
    upload_id = location.rstrip("/").rsplit("/", 1)[-1]
    if not upload_id:
        raise MascopeAPIError(
            "The server did not return an upload location.", url=create_url
        )
    upload_url = f"{url}/api/{TUS_UPLOAD_PATH}/{upload_id}"

    if size == 0:
        return  # the server completes empty uploads at creation

    try:
        _transfer_chunks(upload_url, access_token, filepath, size, chunk_size)
    except Exception:
        # Abandoning the transfer would otherwise leave a partial upload
        # on the server until its next restart - and the caller's outer
        # retry creates a fresh resource per attempt, multiplying them.
        _tus_delete(upload_url, access_token)
        raise
    logger.debug(f"TUS upload of {filename} completed ({size} bytes)")


def _transfer_chunks(
    upload_url: str,
    access_token: str,
    filepath: str,
    size: int,
    chunk_size: int,
) -> None:
    """Send the file content in resumable PATCH chunks.

    :param upload_url: Full URL of the created upload resource
    :type upload_url: str
    :param access_token: Authorization token for API access
    :type access_token: str
    :param filepath: Path to the file to upload
    :type filepath: str
    :param size: Declared upload length in bytes
    :type size: int
    :param chunk_size: Starting bytes per PATCH request; halved down to
        :data:`TUS_MIN_CHUNK_SIZE` on connection failures
    :type chunk_size: int
    """
    offset = 0
    failures = 0
    with open(filepath, "rb") as file:
        while offset < size:
            file.seek(offset)
            chunk = file.read(chunk_size)
            if not chunk:
                # The file shrank on disk mid-upload (e.g. rewritten by
                # the instrument); PATCHing empty bodies would loop
                # forever since the offset never advances.
                raise MascopeAPIError(
                    "The file changed on disk during the upload "
                    f"(expected {size} bytes, found only {offset}).",
                    url=upload_url,
                )
            try:
                resp = requests.patch(
                    upload_url,
                    data=chunk,
                    headers={
                        **_tus_headers(access_token),
                        "Upload-Offset": str(offset),
                        "Content-Type": "application/offset+octet-stream",
                    },
                    verify=_get_verify(),
                    timeout=(10, 600),
                )
                _raise_for_status(resp, upload_url)
            except Exception as e:
                # Transient failures resume from the server-confirmed
                # offset: timeouts, dropped connections, retryable server
                # errors, and 409 (our offset disagrees with the server's,
                # e.g. a chunk partially arrived before a drop). Anything
                # else - rejected token, vanished upload, validation, or
                # a non-connection request error (SSL, proxy config) -
                # fails fast, matching the retry policy in _http.
                conflict = isinstance(e, MascopeAPIError) and e.status_code == 409
                transient = (
                    conflict
                    or isinstance(e, (Timeout, RequestsConnectionError))
                    or _is_retryable(e)
                )
                if not transient:
                    if isinstance(e, RequestException):
                        raise MascopeConnectionError(
                            f"The upload request failed: {e}", url=upload_url
                        ) from e
                    raise
                failures += 1
                if failures >= TUS_MAX_ATTEMPTS:
                    if isinstance(e, Timeout):
                        raise MascopeTimeoutError(
                            "The upload timed out.", url=upload_url
                        ) from e
                    if isinstance(e, RequestsConnectionError):
                        raise MascopeConnectionError(
                            "Lost the connection while uploading "
                            f"({e.__class__.__name__}).",
                            url=upload_url,
                        ) from e
                    raise
                if isinstance(e, (Timeout, RequestsConnectionError)):
                    # A connection that dies mid-chunk can mean the
                    # server/proxy chain cannot take a body this large
                    # (tuspyserver 4.1.3 behind Cloudflare cut PATCHes
                    # over ~25 MB) - halve so the upload makes progress
                    # instead of retrying the same doomed size, and keep
                    # the reduced size for the rest of the file.
                    chunk_size = max(
                        chunk_size // 2, min(chunk_size, TUS_MIN_CHUNK_SIZE)
                    )
                delay = TUS_RETRY_BACKOFF_BASE**failures
                logger.info(
                    "Chunk upload failed (attempt {}/{}), resuming in {}s "
                    "with {} MB chunks: {}",
                    failures,
                    TUS_MAX_ATTEMPTS,
                    delay,
                    round(chunk_size / 1024**2, 1),
                    e,
                )
                time.sleep(delay)
                server_offset = _tus_offset(upload_url, access_token)
                if server_offset is not None:
                    offset = server_offset
                continue
            failures = 0
            offset += len(chunk)
