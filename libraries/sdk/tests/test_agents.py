"""Hermetic unit tests for the agent upload helper in ``mascope_sdk._agents``.

Regression tests for the bug where ``api_post_file`` logged the specific
failure cause (rejected token, connection error, server message) but returned
``None``, leaving callers with nothing better than a generic "upload failed".
"""

import base64
import json

import pytest
import requests

from mascope_sdk import _agents
from mascope_sdk.exceptions import (
    AuthenticationError,
    MascopeAPIError,
    MascopeConnectionError,
    NotFoundError,
    ServerError,
    TusNotSupportedError,
    ValidationError,
)


def _fake_response(
    status_code: int, payload: dict | None = None, headers: dict | None = None
) -> requests.Response:
    response = requests.Response()
    response.status_code = status_code
    response._content = json.dumps(payload or {}).encode()
    if headers:
        response.headers.update(headers)
    return response


@pytest.fixture
def upload_file(tmp_path):
    path = tmp_path / "sample.raw"
    path.write_bytes(b"raw-bytes")
    return str(path)


def test_rejected_token_raises_authentication_error(monkeypatch, upload_file):
    monkeypatch.setattr(
        _agents.requests,
        "post",
        lambda *a, **k: _fake_response(
            401, {"error": "Authorization failed. Please sign in to the Mascope."}
        ),
    )

    with pytest.raises(AuthenticationError) as exc_info:
        _agents.api_post_file(
            "http://testserver", "sample/files/upload", "bad", upload_file
        )

    # The server's message and the token hint both reach the caller.
    assert "Authorization failed" in str(exc_info.value)
    assert "API token" in str(exc_info.value)


def test_renew_agent_token_returns_new_token_and_lifetime(monkeypatch):
    monkeypatch.setattr(
        _agents.requests,
        "post",
        lambda *a, **k: _fake_response(
            200, {"data": {"access_token": "fresh", "expires_in": 2592000}}
        ),
    )
    token, expires_in = _agents.api_renew_agent_token("http://testserver", "current")
    assert token == "fresh"
    assert expires_in == 2592000


def test_renew_agent_token_uses_configured_tls_verification(monkeypatch):
    import mascope_sdk

    captured = {}

    def fake_post(url, headers, verify, timeout):
        captured["verify"] = verify
        return _fake_response(200, {"data": {"access_token": "fresh", "expires_in": 1}})

    monkeypatch.setattr(_agents.requests, "post", fake_post)
    # The agent sets TLS verification at the package level (mascope_sdk.VERIFY_TLS),
    # which _get_verify() reads per request.
    monkeypatch.setattr(mascope_sdk, "VERIFY_TLS", False)
    _agents.api_renew_agent_token("http://testserver", "current")
    assert captured["verify"] is False


def test_renew_agent_token_missing_endpoint_signals_fallback(monkeypatch):
    # A 404 means the server has no renewal endpoint (older release); the agent
    # keeps its current token, exactly as the tus fallback does.
    monkeypatch.setattr(
        _agents.requests, "post", lambda *a, **k: _fake_response(404, {})
    )
    with pytest.raises(TusNotSupportedError):
        _agents.api_renew_agent_token("http://testserver", "current")


def test_renew_agent_token_rejected_token_raises_auth_error(monkeypatch):
    # A 401 means the token is expired or revoked - the machine must re-pair.
    monkeypatch.setattr(
        _agents.requests,
        "post",
        lambda *a, **k: _fake_response(401, {"error": "expired"}),
    )
    with pytest.raises(AuthenticationError):
        _agents.api_renew_agent_token("http://testserver", "current")


def test_server_error_carries_backend_message(monkeypatch, upload_file):
    monkeypatch.setattr(
        _agents.requests,
        "post",
        lambda *a, **k: _fake_response(
            500,
            {"error": "Failed to process sample file.", "detail": {"error_id": "x"}},
        ),
    )

    with pytest.raises(ServerError) as exc_info:
        _agents.api_post_file(
            "http://testserver", "sample/files/upload", "t", upload_file
        )

    assert "Failed to process sample file." in str(exc_info.value)


def test_connection_failure_raises_connection_error(monkeypatch, upload_file):
    def fake_post(*a, **k):
        raise requests.exceptions.ConnectionError("refused")

    monkeypatch.setattr(_agents.requests, "post", fake_post)

    with pytest.raises(MascopeConnectionError):
        _agents.api_post_file(
            "http://testserver", "sample/files/upload", "t", upload_file
        )


def test_success_returns_response(monkeypatch, upload_file):
    monkeypatch.setattr(
        _agents.requests,
        "post",
        lambda *a, **k: _fake_response(201, {"message": "uploaded"}),
    )

    resp = _agents.api_post_file(
        "http://testserver", "sample/files/upload", "t", upload_file
    )

    assert resp.status_code == 201


# --- api_post_file_tus ---


TUS_LOCATION = {"Location": "http://proxy.internal/api/sample/files/upload/tus/abc123"}


@pytest.fixture(autouse=True)
def tus_deletes(monkeypatch):
    """Record (and neutralize) TUS DELETE calls made on abandoned uploads."""
    calls: list[str] = []

    def fake_delete(url, headers, verify, timeout):
        calls.append(url)
        return _fake_response(204)

    monkeypatch.setattr(_agents.requests, "delete", fake_delete)
    return calls


def _fake_create(captured: dict):
    def fake_post(url, headers, verify, timeout):
        captured.update(url=url, headers=headers)
        return _fake_response(201, headers=TUS_LOCATION)

    return fake_post


def test_tus_upload_chunks_whole_file(monkeypatch, upload_file):
    created: dict = {}
    chunks: list[tuple[str, bytes]] = []

    def fake_patch(url, data, headers, verify, timeout):
        chunks.append((headers["Upload-Offset"], bytes(data)))
        return _fake_response(204)

    monkeypatch.setattr(_agents.requests, "post", _fake_create(created))
    monkeypatch.setattr(_agents.requests, "patch", fake_patch)

    _agents.api_post_file_tus("http://testserver", "tok", upload_file, chunk_size=4)

    # created against our own base URL, with the TUS protocol headers
    assert created["url"] == "http://testserver/api/sample/files/upload/tus/"
    assert created["headers"]["Authorization"] == "Bearer tok"
    assert created["headers"]["Tus-Resumable"] == "1.0.0"
    assert created["headers"]["Upload-Length"] == "9"  # len(b"raw-bytes")
    assert created["headers"]["Upload-Metadata"].startswith("filename ")
    # the 9-byte file went out in 4-byte chunks with advancing offsets
    assert [offset for offset, _ in chunks] == ["0", "4", "8"]
    assert b"".join(data for _, data in chunks) == b"raw-bytes"


def test_tus_upload_addresses_chunks_via_own_base_url(monkeypatch, upload_file):
    # The Location header points at whatever host/scheme the proxy chain
    # reported; only the upload id may be trusted.
    urls: list[str] = []

    def fake_patch(url, data, headers, verify, timeout):
        urls.append(url)
        return _fake_response(204)

    monkeypatch.setattr(_agents.requests, "post", _fake_create({}))
    monkeypatch.setattr(_agents.requests, "patch", fake_patch)

    _agents.api_post_file_tus("http://testserver", "tok", upload_file)

    assert urls == ["http://testserver/api/sample/files/upload/tus/abc123"]


def test_tus_upload_resumes_from_server_offset(monkeypatch, upload_file):
    monkeypatch.setattr(_agents.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(_agents.requests, "post", _fake_create({}))
    monkeypatch.setattr(
        _agents.requests,
        "head",
        lambda *a, **k: _fake_response(200, headers={"Upload-Offset": "3"}),
    )
    attempts = {"n": 0}
    chunks: list[tuple[str, bytes]] = []

    def fake_patch(url, data, headers, verify, timeout):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise requests.exceptions.ConnectionError("dropped mid-chunk")
        chunks.append((headers["Upload-Offset"], bytes(data)))
        return _fake_response(204)

    monkeypatch.setattr(_agents.requests, "patch", fake_patch)

    _agents.api_post_file_tus("http://testserver", "tok", upload_file)

    # resumed from the server-confirmed offset, not from zero
    assert chunks == [("3", b"raw-bytes"[3:])]


def test_tus_upload_halves_chunk_size_on_connection_failures(monkeypatch, upload_file):
    # A proxy cutting long requests on a slow uplink surfaces as repeated
    # connection errors; the client must shrink chunks so uploads still
    # make progress instead of retrying the same too-large chunk forever.
    monkeypatch.setattr(_agents.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(_agents, "TUS_MIN_CHUNK_SIZE", 2)
    monkeypatch.setattr(_agents.requests, "post", _fake_create({}))
    monkeypatch.setattr(
        _agents.requests,
        "head",
        lambda *a, **k: _fake_response(200, headers={"Upload-Offset": "0"}),
    )
    attempts = {"n": 0}
    chunks: list[bytes] = []

    def fake_patch(url, data, headers, verify, timeout):
        attempts["n"] += 1
        if attempts["n"] <= 2:
            raise requests.exceptions.ConnectionError("proxy cut the request")
        chunks.append(bytes(data))
        return _fake_response(204)

    monkeypatch.setattr(_agents.requests, "patch", fake_patch)

    _agents.api_post_file_tus("http://testserver", "tok", upload_file, chunk_size=8)

    # two failures halved 8 -> 4 -> 2; the reduced size sticks afterwards
    assert [len(c) for c in chunks] == [2, 2, 2, 2, 1]
    assert b"".join(chunks) == b"raw-bytes"


def test_tus_upload_gives_up_after_max_attempts(monkeypatch, upload_file, tus_deletes):
    monkeypatch.setattr(_agents.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(_agents.requests, "post", _fake_create({}))
    monkeypatch.setattr(_agents.requests, "head", lambda *a, **k: _fake_response(404))
    attempts = {"n": 0}

    def fake_patch(*args, **kwargs):
        attempts["n"] += 1
        raise requests.exceptions.ConnectionError("network down")

    monkeypatch.setattr(_agents.requests, "patch", fake_patch)

    with pytest.raises(MascopeConnectionError):
        _agents.api_post_file_tus("http://testserver", "tok", upload_file)

    assert attempts["n"] == _agents.TUS_MAX_ATTEMPTS
    # the abandoned partial upload was removed from the server
    assert tus_deletes == ["http://testserver/api/sample/files/upload/tus/abc123"]


def test_tus_upload_does_not_retry_client_errors(monkeypatch, upload_file):
    monkeypatch.setattr(_agents.requests, "post", _fake_create({}))
    attempts = {"n": 0}

    def fake_patch(*args, **kwargs):
        attempts["n"] += 1
        return _fake_response(422, {"error": "Invalid."})

    monkeypatch.setattr(_agents.requests, "patch", fake_patch)

    with pytest.raises(ValidationError):
        _agents.api_post_file_tus("http://testserver", "tok", upload_file)

    assert attempts["n"] == 1  # a rejected request cannot heal by waiting


@pytest.mark.parametrize("status", [404, 401])
def test_tus_create_failure_signals_fallback(monkeypatch, upload_file, status):
    # The File Agent falls back to the legacy endpoint on this: a 404
    # means no TUS route (old server), a 401 means the route is not
    # token-accessible (old server) or a genuinely bad token.
    monkeypatch.setattr(
        _agents.requests, "post", lambda *a, **k: _fake_response(status)
    )

    with pytest.raises(TusNotSupportedError) as exc_info:
        _agents.api_post_file_tus("http://testserver", "tok", upload_file)

    assert exc_info.value.status_code == status


def test_tus_mid_transfer_404_is_not_a_fallback_signal(monkeypatch, upload_file):
    # An upload vanishing mid-transfer (e.g. backend restart clearing the
    # temp dir) must keep its normal type: TusNotSupportedError would
    # latch the agent onto the 100 MB legacy path for its lifetime.
    monkeypatch.setattr(_agents.requests, "post", _fake_create({}))
    monkeypatch.setattr(_agents.requests, "patch", lambda *a, **k: _fake_response(404))

    with pytest.raises(NotFoundError):
        _agents.api_post_file_tus("http://testserver", "tok", upload_file)


def test_tus_upload_fails_fast_on_non_connection_request_error(
    monkeypatch, upload_file
):
    monkeypatch.setattr(_agents.requests, "post", _fake_create({}))
    attempts = {"n": 0}

    def fake_patch(*args, **kwargs):
        attempts["n"] += 1
        raise requests.exceptions.InvalidURL("bad proxy configuration")

    monkeypatch.setattr(_agents.requests, "patch", fake_patch)

    with pytest.raises(MascopeConnectionError):
        _agents.api_post_file_tus("http://testserver", "tok", upload_file)

    assert attempts["n"] == 1  # a config error cannot heal by waiting


def test_tus_upload_detects_file_shrinking_mid_upload(monkeypatch, upload_file):
    # If the watched file is rewritten smaller mid-upload, read() returns
    # b"" while offset < size - without the guard the loop would PATCH
    # empty bodies forever. Shrink the file between the size probe and
    # the chunk loop (the creation request sits between the two).
    def fake_post(url, headers, verify, timeout):
        open(upload_file, "wb").close()  # truncate to 0 bytes
        return _fake_response(201, headers=TUS_LOCATION)

    monkeypatch.setattr(_agents.requests, "post", fake_post)
    monkeypatch.setattr(
        _agents.requests,
        "patch",
        lambda *a, **k: pytest.fail("no PATCH expected for a vanished file"),
    )

    with pytest.raises(MascopeAPIError, match="changed on disk"):
        _agents.api_post_file_tus("http://testserver", "tok", upload_file, chunk_size=4)


def test_tus_upload_empty_file_completes_at_creation(monkeypatch, tmp_path):
    empty = tmp_path / "empty.raw"
    empty.write_bytes(b"")
    monkeypatch.setattr(_agents.requests, "post", _fake_create({}))
    monkeypatch.setattr(
        _agents.requests,
        "patch",
        lambda *a, **k: pytest.fail("no PATCH expected for an empty file"),
    )

    _agents.api_post_file_tus("http://testserver", "tok", str(empty))


def test_tus_upload_rejects_filename_with_path(monkeypatch, upload_file):
    with pytest.raises(ValueError, match="path components"):
        _agents.api_post_file_tus(
            "http://testserver", "tok", upload_file, upload_filename="a/b.raw"
        )


def test_post_file_sends_the_timezone_as_a_form_field(monkeypatch, upload_file):
    """The converter reads it as the ``timezone`` form field on the legacy path."""
    captured = {}

    def capture(*args, **kwargs):
        captured.update(kwargs)
        return _fake_response(201, {"message": "ok"})

    monkeypatch.setattr(_agents.requests, "post", capture)

    _agents.api_post_file(
        url="http://server",
        path="sample/files/upload",
        access_token="t",
        filepath=upload_file,
        timezone="Europe/Helsinki",
    )

    assert captured["data"] == {"timezone": "Europe/Helsinki"}


def test_post_file_omits_the_timezone_when_unknown(monkeypatch, upload_file):
    """A machine that cannot name its zone sends no field at all."""
    captured = {}

    def capture(*args, **kwargs):
        captured.update(kwargs)
        return _fake_response(201, {"message": "ok"})

    monkeypatch.setattr(_agents.requests, "post", capture)

    _agents.api_post_file(
        url="http://server",
        path="sample/files/upload",
        access_token="t",
        filepath=upload_file,
    )

    assert captured["data"] is None


def test_tus_create_carries_the_timezone_in_upload_metadata(monkeypatch, upload_file):
    """The resumable path carries it as a base64 Upload-Metadata pair."""
    captured = {}

    def capture(*args, **kwargs):
        captured.update(kwargs)
        # Fail the creation immediately; the metadata header is the assertion.
        return _fake_response(404)

    monkeypatch.setattr(_agents.requests, "post", capture)

    with pytest.raises(TusNotSupportedError):
        _agents.api_post_file_tus(
            url="http://server",
            access_token="t",
            filepath=upload_file,
            timezone="Europe/Helsinki",
        )

    metadata = captured["headers"]["Upload-Metadata"]
    pairs = dict(part.split(" ", 1) for part in metadata.split(","))
    assert set(pairs) == {"filename", "filetype", "timezone"}
    assert base64.b64decode(pairs["timezone"]).decode() == "Europe/Helsinki"


def test_tus_create_omits_the_timezone_when_unknown(monkeypatch, upload_file):
    captured = {}

    def capture(*args, **kwargs):
        captured.update(kwargs)
        return _fake_response(404)

    monkeypatch.setattr(_agents.requests, "post", capture)

    with pytest.raises(TusNotSupportedError):
        _agents.api_post_file_tus(
            url="http://server", access_token="t", filepath=upload_file
        )

    metadata = captured["headers"]["Upload-Metadata"]
    assert "timezone" not in metadata
