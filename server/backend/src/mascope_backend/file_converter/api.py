"""
Sample file record management module.

This module provides functions to interact with the sample file database
and filestore records via HTTP requests to the API service.
"""

import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import requests

from mascope_backend.api.new.instrument_configs.lib import parse_instrument_functions
from mascope_backend.api.new.instrument_configs.schemas import PeakShape
from mascope_file.name import get_instrument_name

from .runtime import runtime
from .schema import SampleFileProps


HOST = runtime.config.server if runtime.mode == "prod" else "localhost"
URL = f"http://{HOST}:{runtime.meta.api_port}"

# The backend can be saturated during an ingest burst (connection-pool
# congestion answers 503 once its pool_timeout expires). The converter is a
# batch worker: transient server trouble should be waited out - quarantining
# a raw file is only right when processing that file itself fails.
#
# The client timeout must exceed the server's pool_timeout, or a request the
# server would still have answered gets killed from this side first and
# retried into a pool that is no less busy. pool_timeout is configurable while
# this timeout used to be a constant, so the invariant held only by the two
# staying in step by hand; derive the floor from the setting instead.
_POOL_TIMEOUT_S = runtime.full_config.backend.database.pool_timeout
_REQUEST_TIMEOUT_FLOOR_S = 180
_REQUEST_TIMEOUT_MARGIN_S = 60


def _client_timeout(pool_timeout: int) -> int:
    """
    Request timeout that stays above the server's wait for a pooled connection.

    :param pool_timeout: The backend's configured ``pool_timeout``, in seconds.
    :return: Seconds to allow a request, never below the floor.
    """
    return max(_REQUEST_TIMEOUT_FLOOR_S, pool_timeout + _REQUEST_TIMEOUT_MARGIN_S)


_REQUEST_TIMEOUT_S = _client_timeout(_POOL_TIMEOUT_S)
_RETRY_BACKOFF_S = (15, 30, 60)

if _REQUEST_TIMEOUT_S > _REQUEST_TIMEOUT_FLOOR_S:
    # Only when pool_timeout is raised past what the floor already covers, so
    # a stock deployment stays quiet.
    runtime.logger.warning(
        f"Backend pool_timeout is {_POOL_TIMEOUT_S}s, so the converter's "
        f"request timeout is raised to {_REQUEST_TIMEOUT_S}s to stay above it"
    )


def _request_with_retry(method: str, url: str, **kwargs) -> requests.Response:
    """
    Issue an HTTP request, retrying transport errors and 5xx responses.

    Retries with the ``_RETRY_BACKOFF_S`` delays, then returns the last 5xx
    response or re-raises the last transport error. 2xx-4xx responses return
    immediately: client-class statuses are not transient, retrying them only
    hides real bugs.
    """
    kwargs.setdefault("timeout", _REQUEST_TIMEOUT_S)
    last_exc: requests.exceptions.RequestException | None = None
    response: requests.Response | None = None
    for delay in (*_RETRY_BACKOFF_S, None):
        try:
            response = requests.request(method, url, **kwargs)
        except requests.exceptions.RequestException as e:
            response = None
            last_exc = e
            failure = f"{type(e).__name__}: {e}"
        else:
            if response.status_code < 500:
                return response
            failure = f"HTTP {response.status_code}"
        if delay is None:
            break
        runtime.logger.warning(
            f"{method} {url} failed ({failure}), retrying in {delay}s"
        )
        time.sleep(delay)
    if response is None:
        if last_exc is None:
            # Unreachable: the loop always runs, and a missing response means
            # the last attempt raised. Guard so a logic change here can never
            # turn into `raise None` (TypeError).
            raise RuntimeError(f"{method} {url}: retry loop exited without result")
        raise last_exc
    return response


def fetch_instrument_functions(
    filename: str, access_token: str
) -> tuple[dict, callable]:
    """Fetch instrument functions for a sample file via HTTP and parse them.

    Calls "GET /api/instrument_configs/by_filename/{filename}" on the backend
    API and reconstructs the peakshape dict + resolution function callable.

    :param filename: Sample filename whose instrument config to fetch.
    :type filename: str
    :param access_token: Bearer token for request authentication.
    :type access_token: str
    :return: Tuple of (peakshape_dict, resolution_function_callable).
    :rtype: tuple[dict, callable]
    :raises ValueError: If the backend returns no instrument config.
    :raises Exception: If the HTTP request fails.
    """

    headers = {
        "X-Service-Name": "file-converter",
        "Authorization": f"Bearer {access_token}",
    }

    try:
        response = _request_with_retry(
            "GET",
            f"{URL}/api/instrument_configs/by_filename/{filename}",
            headers=headers,
        )
        if response.status_code != 200:
            raise ValueError(
                f"Failed to fetch instrument config for {filename}: HTTP {response.status_code}"
            )

        data = response.json().get("data", {})
        # parse_instrument_functions expects a model with .peakshape and .resolution_function
        instrument_config = SimpleNamespace(
            peakshape=data["peakshape"],
            resolution_function=data["resolution_function"],
        )
        return parse_instrument_functions(instrument_config)

    except requests.exceptions.RequestException as e:
        raise Exception(
            f"Failed to fetch instrument functions for {filename}: {e}"
        ) from e


def create_sample_file_db_record(
    data: SampleFileProps, instrument_function_id: str, access_token: str
) -> None:
    """Create a sample file database record via HTTP request.

    :param data: Sample file object to create
    :type data: SampleFileProps
    :param instrument_function_id: FK to instrument config
    :type instrument_function_id: str
    :param access_token: Access token required for request authentication
    :type access_token: str
    :raises Exception: HTTP request failed
    """
    runtime.logger.info(
        f"Creating sample file database record for file: {data.filename}"
    )

    utc_offset = timedelta(seconds=int(data.utc_offset))
    date = data.timestamp
    date_utc = (
        (datetime.fromisoformat(date) - utc_offset)
        .replace(tzinfo=timezone.utc)
        .isoformat()
    )

    sample_file_db_record = {
        "instrument_function_id": instrument_function_id,
        "filename": data.filename,
        "instrument": get_instrument_name(data.filename),
        "datetime": date,
        "datetime_utc": date_utc,
        "length": data.length,
        "range": data.range,
        "method_file": data.method_file,
        "mz_calibration": data.mz_calibration,
        "polarity": data.polarity,
    }

    headers = {
        "Content-Type": "application/json",
        "X-Service-Name": "file-converter",
        "Authorization": f"Bearer {access_token}",
    }

    try:
        response = _request_with_retry(
            "POST",
            f"{URL}/api/sample/files",
            headers=headers,
            json=sample_file_db_record,
        )

        if response.status_code != 201:
            raise Exception(
                f"Failed to create database record! Status code: {response.status_code}"
            )

    except requests.exceptions.RequestException as e:
        raise Exception(
            f"Failed to create database record due to request error: {e}"
        ) from e


def check_sample_file_db_record(filename: str, access_token: str) -> bool:
    """Check if a sample file database record exists by filename.

    :param filename: Sample filename to check
    :type filename: str
    :param access_token: Access token for request authentication
    :type access_token: str
    :return: True if record exists, False otherwise
    :rtype: bool
    :raises Exception: If request fails unexpectedly
    """
    headers = {
        "X-Service-Name": "file-converter",
        "Authorization": f"Bearer {access_token}",
    }

    params = {"filename": filename, "limit": 1}

    try:
        response = _request_with_retry(
            "GET", f"{URL}/api/sample/files", headers=headers, params=params
        )

        if response.status_code == 200:
            data = response.json()
            return data.get("results") == 1
        else:
            runtime.logger.warning(
                f"Failed to check sample file record for {filename}: HTTP {response.status_code}"
            )
            return False

    except requests.exceptions.RequestException as e:
        # No log here: the raised exception is logged with its traceback by
        # the processing loop's handler
        raise Exception(f"Failed to check sample file record: {e}") from e


def is_blank_sample_file(filename: str, access_token: str) -> bool:
    """Return whether the sample file is a blank measurement (has no peaks).

    Blank files are persisted without an instrument_function_id.

    :param filename: Sample filename to inspect
    :type filename: str
    :param access_token: Access token for request authentication
    :type access_token: str
    :return: True if the sample file is blank, False otherwise
    :rtype: bool
    :raises Exception: If request fails unexpectedly or file is not found
    """
    headers = {
        "X-Service-Name": "file-converter",
        "Authorization": f"Bearer {access_token}",
    }
    params = {"filename": filename, "limit": 1}

    try:
        response = _request_with_retry(
            "GET", f"{URL}/api/sample/files", headers=headers, params=params
        )

        if response.status_code != 200:
            raise Exception(
                f"Failed to fetch sample file metadata for {filename}: HTTP {response.status_code}"
            )

        response_data = response.json()
        sample_files = response_data.get("data", [])
        if not sample_files:
            raise Exception(f"Sample file {filename} not found")

        sample_file = sample_files[0]
        return sample_file.get("instrument_function_id") is None

    except requests.exceptions.RequestException as e:
        # No log here: the raised exception is logged by the caller's handler
        raise Exception(f"Failed to fetch sample file metadata: {e}") from e


def delete_sample_file_by_filename(filename: str, access_token: str) -> bool:
    """Delete sample file from filestore by filename via HTTP request.

    :param filename: Sample filename to delete from filestore
    :type filename: str
    :param access_token: Access token for request authentication
    :type access_token: str
    :return: True if deletion was successful, False if file not found
    :rtype: bool
    :raises Exception: If request fails unexpectedly
    """
    headers = {
        "X-Service-Name": "file-converter",
        "Authorization": f"Bearer {access_token}",
    }

    try:
        response = requests.post(
            f"{URL}/api/sample/files/delete",
            headers=headers,
            json={"filenames": [filename]},
            timeout=30,
        )

        if response.status_code in [200, 207]:
            runtime.logger.debug(f"Successfully deleted file: {filename}")
            return True
        elif response.status_code == 422:
            runtime.logger.debug(f"File not found for deletion: {filename}")
            return False
        else:
            # No log here: the raised exception is logged by the caller's
            # handler
            raise Exception(f"Failed to delete file: HTTP {response.status_code}")

    except requests.exceptions.RequestException as e:
        raise Exception(f"Failed to delete file: {e}") from e


def create_instrument_config_db_record(
    sample_file_props: SampleFileProps,
    peakshape: PeakShape,
    resolution_function: list,
    access_token: str,
) -> str:
    """Create an instrument configuration database record via HTTP request.

    :param instrument_config: Instrument configuration object to create
    :type instrument_config: dict
    :param access_token: Access token required for request authentication
    :type access_token: str
    :return: The created instrument_function_id
    :rtype: str
    :raises Exception: HTTP request failed
    """
    runtime.logger.info(
        f"Creating instrument config database record for file: {sample_file_props.filename}"
    )

    # Construct the request body based on the function parameters
    utc_offset = timedelta(seconds=int(sample_file_props.utc_offset))
    date = sample_file_props.timestamp
    date_utc = (
        (datetime.fromisoformat(date) - utc_offset)
        .replace(tzinfo=timezone.utc)
        .isoformat()
    )

    data = {
        "instrument": get_instrument_name(sample_file_props.filename),
        "datetime_utc": date_utc,
        "peakshape": peakshape.model_dump(),
        "resolution_function": resolution_function,
        "method_file": sample_file_props.method_file,
    }

    # Make the POST request to the instrument_configs endpoint
    headers = {
        "Content-Type": "application/json",
        "X-Service-Name": "file-converter",
        "Authorization": f"Bearer {access_token}",
    }

    try:
        response = _request_with_retry(
            "POST",
            f"{URL}/api/instrument_configs",
            headers=headers,
            json=data,
        )

        if response.status_code != 201:
            raise Exception(
                f"Failed to create database record! Status code: {response.status_code}"
            )

        return (response.json())["data"]["instrument_function_id"]

    except requests.exceptions.RequestException as e:
        raise Exception(
            f"Failed to create database record due to request error: {e}"
        ) from e


def rematch_sample(
    sample_item_id: str, access_token: str, full_remove: bool = False, timeout: int = 30
) -> dict:
    """
    Trigger a rematch for a sample via the backend rematch route.

    :param sample_item_id: Sample item id to rematch
    :param access_token: Bearer token for authentication
    :param full_remove: If True, request a full removal before recompute
    :param timeout: HTTP request timeout in seconds
    :return: Response JSON from the backend (expected keys: 'message', 'process_id')
    :raises Exception: On network error or non-expected HTTP status
    """
    headers = {
        "X-Service-Name": "file-converter",
        "Authorization": f"Bearer {access_token}",
    }
    params = {"full_remove": "true" if full_remove else "false"}

    try:
        resp = requests.post(
            f"{URL}/api/match/rematch/sample/{sample_item_id}",
            headers=headers,
            params=params,
            timeout=timeout,
        )
        # Callers ignore the return value, so an unchecked non-2xx response
        # would make a failed rematch completely invisible.
        if resp.status_code >= 400:
            raise Exception(
                f"Failed to request rematch for {sample_item_id}: "
                f"HTTP {resp.status_code}"
            )
        return resp.json()
    except requests.exceptions.RequestException as e:
        raise Exception(f"Failed to request rematch for {sample_item_id}: {e}") from e
