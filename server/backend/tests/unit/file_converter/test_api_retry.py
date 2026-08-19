"""
Tests for the file converter's transient-failure retry (``api._request_with_retry``).

During an ingest burst the backend answers 503 once its connection pool's
``pool_timeout`` expires. A single failed call must not quarantine the raw
file - the converter retries transport errors and 5xx responses with backoff,
while client-class statuses (2xx-4xx) return immediately.
"""

from unittest.mock import MagicMock, patch

import pytest
import requests

from mascope_backend.file_converter import api


def _response(status_code: int) -> MagicMock:
    response = MagicMock(spec=requests.Response)
    response.status_code = status_code
    return response


@pytest.fixture(autouse=True)
def _no_sleep():
    with patch.object(api.time, "sleep") as sleep:
        yield sleep


def test_5xx_is_retried_until_success(_no_sleep):
    responses = [_response(503), _response(503), _response(201)]
    with patch.object(api.requests, "request", side_effect=responses) as request:
        result = api._request_with_retry("POST", "http://x/api/y")

    assert result.status_code == 201
    assert request.call_count == 3
    assert _no_sleep.call_count == 2


def test_client_status_returns_immediately(_no_sleep):
    with patch.object(api.requests, "request", return_value=_response(400)) as request:
        result = api._request_with_retry("POST", "http://x/api/y")

    assert result.status_code == 400
    assert request.call_count == 1
    _no_sleep.assert_not_called()


def test_persistent_5xx_returns_last_response():
    with (
        patch.object(api.time, "sleep"),
        patch.object(api.requests, "request", return_value=_response(503)) as request,
    ):
        result = api._request_with_retry("GET", "http://x/api/y")

    assert result.status_code == 503
    assert request.call_count == len(api._RETRY_BACKOFF_S) + 1


def test_persistent_transport_error_is_reraised():
    error = requests.exceptions.ConnectionError("refused")
    with (
        patch.object(api.time, "sleep"),
        patch.object(api.requests, "request", side_effect=error),
    ):
        with pytest.raises(requests.exceptions.ConnectionError):
            api._request_with_retry("GET", "http://x/api/y")


def test_transport_error_then_success_recovers(_no_sleep):
    side_effects = [requests.exceptions.ConnectionError("refused"), _response(200)]
    with patch.object(api.requests, "request", side_effect=side_effects):
        result = api._request_with_retry("GET", "http://x/api/y")

    assert result.status_code == 200


def test_default_timeout_exceeds_server_pool_patience():
    """
    Client timeout must outlast the server's configured pool_timeout.

    Compared against the setting rather than a literal: the two used to be
    independent constants, so nothing caught them drifting apart. A client that
    gives up first abandons requests the server would still have answered and
    retries them into a pool that is no less busy.
    """
    with patch.object(api.requests, "request", return_value=_response(200)) as request:
        api._request_with_retry("GET", "http://x/api/y")

    assert request.call_args.kwargs["timeout"] > api._POOL_TIMEOUT_S


def test_timeout_floor_holds_for_a_short_pool_timeout():
    """A small pool_timeout does not shrink the client timeout below its floor."""
    assert api._REQUEST_TIMEOUT_S >= api._REQUEST_TIMEOUT_FLOOR_S


@pytest.mark.parametrize(
    ("pool_timeout", "expected"),
    [
        (30, 180),  # config default - the floor governs
        (120, 180),  # base/prod - floor still governs, unchanged from before
        (180, 240),  # would have inverted the invariant when hardcoded at 180
        (600, 660),  # far past the floor
    ],
)
def test_client_timeout_is_derived_from_pool_timeout(pool_timeout, expected):
    """The invariant is enforced by derivation, not by two constants agreeing."""
    assert api._client_timeout(pool_timeout) == expected
    assert api._client_timeout(pool_timeout) > pool_timeout


def test_request_timeout_matches_the_configured_pool_timeout():
    """What the module actually uses comes from that derivation."""
    assert api._REQUEST_TIMEOUT_S == api._client_timeout(api._POOL_TIMEOUT_S)
