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


def _lists_warnings(body: Any) -> bool:
    """Whether the server sent its warnings as a list of their own.

    One that does not is older than the list - and older, too, than the
    warning on an answer with every peak left out.
    """
    return isinstance(body, dict) and isinstance(body.get("warnings"), list)


def _api_warnings(body: Any) -> list[str]:
    """The warnings of a response, in the order given.

    The server reports a condition the caller should know about - peaks left
    out of a time-ranged read, say - as a ``warnings`` list beside ``data``,
    and that list is taken as it is.

    A server from before the list only appends ``Warning: <text>`` to the
    ``message``, once per warning, and for it the message is split on that
    marker. Only the marker counts: a message that merely contains the word
    carries none. The message also quotes names the user chose, though, so a
    sample named ``Warning: blank`` reads as a warning there. That cannot be
    told apart from inside a sentence, which is what the list is for.

    :param body: The response envelope, whatever it holds.
    :return: Each warning's text, empty when there is none.
    :rtype: list[str]
    """
    if not isinstance(body, dict):
        return []
    if _lists_warnings(body):
        listed = body["warnings"]
        return [text.strip() for text in listed if isinstance(text, str) and text]

    message = body.get("message")
    if not isinstance(message, str) or _WARNING_MARKER not in message:
        return []
    _, *warnings = message.split(_WARNING_MARKER)
    return [text.strip() for text in warnings if text.strip()]


def _log_api_message(body: Any, hint: str | None = None) -> list[str]:
    """Log a response's ``message``, and each of its warnings at WARNING.

    :param body: The response envelope.
    :param hint: What this client can do about a warning, said after each.
        The server names a remedy in the API's terms; the name this version
        of the SDK gives it is the SDK's to add.
    :return: The warnings as the server gave them, without the hint, for a
        caller that also hands them on.
    :rtype: list[str]
    """
    message = body.get("message") if isinstance(body, dict) else None
    if message:
        logger.debug(f"API response message: {message}")
    warnings = _api_warnings(body)
    for warning in warnings:
        # WARNING: the answer is incomplete in a way its rows do not show
        logger.warning(f"API warning: {warning}{f' {hint}' if hint else ''}")
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
        if stream:
            return http_get(
                url=self._client.url,
                path=path,
                access_token=self._client.access_token,
                params=params,
                stream=True,
                timeout=self._client._timeout,
                verify_ssl=self._client._verify_ssl,
                service_name=self._client._service_name,
            )

        # The one way a response is unwrapped: a read that also hands the
        # warnings on (get_peaks) takes the envelope and logs it the same way.
        body = self._get_envelope(path, params)
        _log_api_message(body)
        return body.get("data")

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
