"""
Hermetic unit tests for the low-level HTTP helpers in ``mascope_sdk._http``.

Unlike ``test_contract.py`` these do not need a running stack: they mock
``requests.post`` and inspect the request that ``requests`` would actually put
on the wire.
"""

import json

import pytest
import requests

from mascope_sdk import _http
from mascope_sdk.exceptions import (
    AuthenticationError,
    MascopeAPIError,
    NotFoundError,
    ServerError,
    ValidationError,
)


def _fake_ok_response() -> requests.Response:
    """A minimal 200 response carrying an empty ``data`` envelope."""
    response = requests.Response()
    response.status_code = 200
    response._content = b'{"data": null}'
    return response


def test_http_post_sends_json_content_type(monkeypatch):
    """POST bodies must go out as ``application/json``.

    Regression test for the bug where ``http_post`` sent
    ``data=json.dumps(body)`` without a Content-Type header. ``requests`` does
    not set ``application/json`` for a raw string body, so FastAPI received the
    body as opaque bytes and rejected it with a 422
    ``model_attributes_type`` error (e.g. on
    ``POST /api/samples/{id}/peaks/timeseries``).

    The request is reconstructed through ``requests``' own preparation so the
    assertion reflects what actually reaches the server, independent of how the
    header gets set.
    """
    captured = {}
    body = {"peak_id": "csgxj7ZPeeVWlpA960Bj"}

    def fake_post(url, **kwargs):
        prepared = requests.Request(
            "POST",
            url,
            headers=kwargs.get("headers"),
            data=kwargs.get("data"),
            json=kwargs.get("json"),
        ).prepare()
        captured["content_type"] = prepared.headers.get("Content-Type")
        captured["body"] = prepared.body
        return _fake_ok_response()

    monkeypatch.setattr(_http.requests, "post", fake_post)

    _http.http_post(
        url="http://testserver",
        path="samples/abc/peaks/timeseries",
        access_token="token",
        data=body,
    )

    assert captured["content_type"] == "application/json"
    # And the body is valid JSON that round-trips to the original dict.
    sent = captured["body"]
    if isinstance(sent, bytes):
        sent = sent.decode()
    assert json.loads(sent) == body


def test_http_post_preserves_auth_headers(monkeypatch):
    """The Authorization / service headers must survive alongside the JSON body."""
    captured = {}

    def fake_post(url, **kwargs):
        captured["headers"] = kwargs.get("headers") or {}
        return _fake_ok_response()

    monkeypatch.setattr(_http.requests, "post", fake_post)

    _http.http_post(
        url="http://testserver",
        path="samples/abc/peaks/timeseries",
        access_token="secret-token",
        data={"peak_id": "x"},
        service_name="mascope_sdk",
    )

    assert captured["headers"]["Authorization"] == "Bearer secret-token"
    assert captured["headers"]["X-Service-Name"] == "mascope_sdk"


def _fake_error_response(status_code: int, payload: dict) -> requests.Response:
    response = requests.Response()
    response.status_code = status_code
    response._content = json.dumps(payload).encode()
    return response


def test_extract_error_message_prefers_backend_error_field():
    """The backend's error shape is ``{"error": <human message>, "detail":
    {"error_id": ...}}``. Regression test for the bug where the opaque detail
    dict was returned instead of the human-readable message.
    """
    response = _fake_error_response(
        404,
        {
            "error": "Failed to Get Sample. Sample not found.",
            "detail": {"error_id": "a1b2c3"},
        },
    )

    message = _http._extract_error_message(response)

    assert message == "Failed to Get Sample. Sample not found."


def test_extract_error_message_falls_back_to_detail():
    """Plain FastAPI-style ``{"detail": ...}`` errors still resolve."""
    response = _fake_error_response(404, {"detail": "Not found"})

    assert _http._extract_error_message(response) == "Not found"


def _response(status: int, body: bytes = b'{"error": "nope"}') -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response._content = body
    return response


@pytest.mark.parametrize("status", [400, 405, 411, 413, 415, 422])
def test_permanent_client_errors_are_terminal(status):
    """A request the server understood and rejected must not be retried.

    Callers decide whether to retry from the exception type, so a 400 that
    arrived as a plain MascopeAPIError was indistinguishable from a transient
    fault and got retried into a timeout - ten attempts for a file whose name
    the server will never accept.
    """
    with pytest.raises(ValidationError) as exc_info:
        _http._raise_for_status(_response(status), "http://server/api/x")

    assert exc_info.value.status_code == status
    assert not _http._is_retryable(exc_info.value)


@pytest.mark.parametrize("status", [408, 409, 425, 429])
def test_transient_client_errors_stay_retryable(status):
    """These clear on their own: a rate limit, a timeout, a TUS offset clash.

    They must NOT become terminal, or a busy deployment would strand uploads
    that a moment's wait would have carried.
    """
    with pytest.raises(MascopeAPIError) as exc_info:
        _http._raise_for_status(_response(status), "http://server/api/x")

    assert not isinstance(exc_info.value, ValidationError)
    assert exc_info.value.status_code == status
    # 409 is resolved by resuming from the server's offset, which only the
    # TUS chunk loop can do; the rest are safe to simply send again.
    assert _http._is_retryable(exc_info.value) is (status != 409)


@pytest.mark.parametrize("status", [502, 503, 504, 507])
def test_transient_server_errors_stay_retryable(status):
    """507 belongs here with the gateway errors, not with the 4xx refusals.

    The backend answers 507 when an upload would leave its disk below the
    free-space floor. That clears when space is freed, and instrument raw data
    is irreplaceable, so a client must keep trying rather than treat the
    refusal as final and set the file aside.
    """
    with pytest.raises(ServerError) as exc_info:
        _http._raise_for_status(_response(status), "http://server/api/x")

    assert exc_info.value.status_code == status
    assert _http._is_retryable(exc_info.value)


def test_auth_and_not_found_keep_their_own_types():
    """The terminal rule must not swallow the types callers act on."""
    with pytest.raises(AuthenticationError):
        _http._raise_for_status(_response(401), "http://server/api/x")
    with pytest.raises(NotFoundError):
        _http._raise_for_status(_response(404), "http://server/api/x")
