"""Base resource class for Mascope SDK resources."""

from typing import TYPE_CHECKING, Any

import pandas as pd
from loguru import logger

from .._http import http_get, http_post


if TYPE_CHECKING:
    from ..client import MascopeClient


def _coerce_datetime_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Convert known datetime columns to proper datetime types.

    Columns ending with a UTC suffix are converted to ``datetime64[ns, UTC]``.
    Columns matching a local datetime name are converted to ``datetime64[ns]``.
    """
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            continue
        if "datetime" in col:
            is_utc = "utc" in col
            try:
                df[col] = pd.to_datetime(df[col], utc=is_utc)
            except Exception as e:
                # INFO: fires per column per DataFrame on odd data
                logger.info(f"Failed to convert column {col} to datetime: {e}")
    return df


def _coerce_utc_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Convert ``*_utc_*`` audit columns to timezone-aware datetimes.

    The run and assignment records name their timestamps
    ``peak_assignment_run_utc_created`` / ``..._utc_completed``, which the
    shared ``_coerce_datetime_columns`` (keyed on ``datetime`` in the column
    name) does not catch.
    """
    for col in df.columns:
        if "_utc" not in col or pd.api.types.is_datetime64_any_dtype(df[col]):
            continue
        try:
            df[col] = pd.to_datetime(df[col], utc=True)
        except Exception as e:
            # INFO: fires per column per DataFrame on odd data
            logger.info(f"Failed to convert column {col} to datetime: {e}")
    return df


#: What the server puts in front of each warning it folds into a response's
#: ``message`` (``"Loaded 3 peaks ... Warning: 2 peak(s) were excluded ..."``).
_WARNING_MARKER = "Warning:"


def _api_warnings(message: Any) -> list[str]:
    """The warnings a response's ``message`` carries, in the order given.

    The server reports a condition the caller should know about - peaks left
    out of a time-ranged read, say - by appending ``Warning: <text>`` to the
    message, once per warning. Only that marker counts: a message that merely
    contains the word, in a sample's name for one, carries none.

    :param message: The envelope's ``message``, whatever it holds.
    :return: Each warning's text, empty when there is none.
    :rtype: list[str]
    """
    if not isinstance(message, str) or _WARNING_MARKER not in message:
        return []
    _, *warnings = message.split(_WARNING_MARKER)
    return [text.strip() for text in warnings if text.strip()]


def _log_api_message(message: Any) -> list[str]:
    """Log a response's ``message``, and each warning in it at WARNING.

    :param message: The envelope's ``message``.
    :return: The warnings it carried, for a caller that also hands them on.
    :rtype: list[str]
    """
    if not message:
        return []
    logger.debug(f"API response message: {message}")
    warnings = _api_warnings(message)
    for warning in warnings:
        # WARNING: the answer is incomplete in a way its rows do not show
        logger.warning(f"API warning: {warning}")
    return warnings


class BaseResource:
    """Base class for all API resource classes.

    Provides common functionality for making API requests using the
    client's credentials.
    """

    def __init__(self, client: "MascopeClient"):
        """Initialize the resource with a client reference.

        :param client: The MascopeClient instance to use for requests.
        :type client: MascopeClient
        """
        self._client = client

    def _get_envelope(
        self, path: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """GET *path* and return the full response envelope.

        :meth:`_get` unwraps a response to its ``data`` field, which drops the
        ``total`` a paged read needs to know when it has everything - so a
        paging loop reads the envelope itself.

        :param path: API path (without /api/ prefix).
        :type path: str
        :param params: Query parameters.
        :type params: dict[str, Any], optional
        :return: The whole JSON body.
        :rtype: dict[str, Any]
        """
        response = http_get(
            url=self._client.url,
            path=path,
            access_token=self._client.access_token,
            params=params,
            timeout=self._client._timeout,
            verify_ssl=self._client._verify_ssl,
            service_name=self._client._service_name,
        )
        return response.json()

    def _get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        stream: bool = False,
    ) -> Any:
        """Make a GET request to the API.

        :param path: API path (without /api/ prefix).
        :type path: str
        :param params: Query parameters.
        :type params: dict[str, Any], optional
        :param stream: Whether to stream the response.
        :type stream: bool, optional
        :return: Parsed JSON response data.
        :rtype: Any
        """
        response = http_get(
            url=self._client.url,
            path=path,
            access_token=self._client.access_token,
            params=params,
            stream=stream,
            timeout=self._client._timeout,
            verify_ssl=self._client._verify_ssl,
            service_name=self._client._service_name,
        )
        if stream:
            return response

        body = response.json()
        _log_api_message(body.get("message"))
        return body.get("data")

    def _get_with_warnings(
        self, path: str, params: dict[str, Any] | None = None
    ) -> tuple[Any, list[str]]:
        """GET *path* and return its ``data`` with the response's warnings.

        :meth:`_get` logs a warning and returns the data alone, which leaves a
        script nothing to test: the frame it gets looks complete. A read whose
        answer can be silently short hands the warnings on as well, so its
        caller can attach them to what it returns.

        :param path: API path (without /api/ prefix).
        :type path: str
        :param params: Query parameters.
        :type params: dict[str, Any], optional
        :return: The parsed ``data``, and each warning's text (logged already).
        :rtype: tuple[Any, list[str]]
        """
        body = self._get_envelope(path, params)
        return body.get("data"), _log_api_message(body.get("message"))

    def _post(
        self,
        path: str,
        data: dict[str, Any],
    ) -> Any:
        """Make a POST request to the API.

        :param path: API path (without /api/ prefix).
        :type path: str
        :param data: Request body data.
        :type data: dict[str, Any]
        :return: Parsed JSON response data.
        :rtype: Any
        """
        response = http_post(
            url=self._client.url,
            path=path,
            access_token=self._client.access_token,
            data=data,
            timeout=self._client._timeout,
            verify_ssl=self._client._verify_ssl,
            service_name=self._client._service_name,
        )
        return response.json().get("data")
