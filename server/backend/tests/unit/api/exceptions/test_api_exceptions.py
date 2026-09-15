"""
Unit tests for API exception processing.

Focus: error responses must never leak internal details to clients.
Tracebacks and raw messages of unexpected exceptions stay in the server
logs, correlated to the response through an opaque ``error_id``.
"""

import json

import pytest
from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import SQLAlchemyError

from mascope_backend.api.lib.exceptions.api_exceptions import (
    ApiException,
    api_e_response_json,
    compose_user_message,
    handle_exception,
    process_exception,
)
from mascope_backend.runtime import runtime


def _raise_and_process(exc: Exception, context: str = "Test context") -> ApiException:
    """Process an exception from inside an except block, as production code does."""
    try:
        raise exc
    except Exception as e:
        return process_exception(e, context)


class TestProcessExceptionDoesNotLeakInternals:
    def test_unexpected_exception_detail_is_opaque(self):
        secret_path = "/app/.runtime/env/data/temp/secret-file"
        api_exc = _raise_and_process(FileNotFoundError(2, "No such file", secret_path))

        serialized = json.dumps(api_exc.tech_message)
        assert "traceback" not in serialized.lower()
        assert secret_path not in serialized
        assert set(api_exc.tech_message) == {"error_id"}
        assert len(api_exc.tech_message["error_id"]) == 32

    def test_unexpected_exception_user_message_is_generic(self):
        secret_path = "/app/.runtime/env/data/temp/secret-file"
        api_exc = _raise_and_process(FileNotFoundError(2, "No such file", secret_path))

        assert api_exc.status_code == 500
        assert secret_path not in api_exc.user_message
        assert api_exc.user_message.startswith("Test context. Unexpected error")
        # The message carries a short reference to the logged error_id so the
        # user can quote it to support.
        assert f"(ref: {api_exc.tech_message['error_id'][:8]})" in api_exc.user_message

    def test_response_json_contains_no_traceback(self):
        try:
            raise KeyError("internal_column_name")
        except Exception as e:
            response = handle_exception(e, "Test context")

        body = json.loads(response.body)
        assert response.status_code == 500
        assert "traceback" not in json.dumps(body).lower()
        assert list(body["detail"]) == ["error_id"]

    def test_api_exception_payload_is_preserved(self):
        payload = {"skipped_items": ["a", "b"]}
        api_exc = _raise_and_process(
            ApiException("Partial success", payload, status_code=207)
        )

        assert api_exc.status_code == 207
        assert api_exc.user_message == "Partial success"
        assert api_exc.tech_message["skipped_items"] == ["a", "b"]
        assert "error_id" in api_exc.tech_message
        assert "traceback" not in api_exc.tech_message

    def test_value_error_keeps_validation_message(self):
        api_exc = _raise_and_process(ValueError("mz must be positive"))

        assert api_exc.status_code == 400
        assert "mz must be positive" in api_exc.user_message
        assert set(api_exc.tech_message) == {"error_id"}

    def test_attribute_error_message_is_generic(self):
        api_exc = _raise_and_process(
            AttributeError("'NoneType' object has no attribute '_engine'")
        )

        # 500, not 400: an attribute the code did not expect is a fault here,
        # and nothing the caller sent can fix it. Clients act on the class -
        # the File Agent sets a file aside for good on a 4xx and retries a
        # 5xx - so reporting a server fault as a client error stranded files.
        assert api_exc.status_code == 500
        assert "_engine" not in api_exc.user_message
        assert api_exc.user_message.startswith("Test context. Unexpected error")

    def test_runtime_error_message_is_generic(self):
        secret_path = "/app/.runtime/secrets/jwt_secret_key.txt"
        api_exc = _raise_and_process(RuntimeError(f"cannot open {secret_path}"))

        assert api_exc.status_code == 500
        assert secret_path not in api_exc.user_message
        assert api_exc.user_message.startswith("Test context. Unexpected error")

    def test_validation_error_input_never_reaches_client_or_log(self):
        # A RequestValidationError renders the offending "input" (which can be a
        # password) in both its str() and the logged traceback's final line.
        secret = "hunter2-must-not-leak"
        exc = RequestValidationError(
            [
                {
                    "type": "missing",
                    "loc": ("body", "password"),
                    "msg": "Field required",
                    "input": {"username": "demo", "password": secret},
                }
            ]
        )

        logged: list[str] = []
        sink_id = runtime.logger.add(
            lambda message: logged.append(str(message)), level="TRACE"
        )
        try:
            api_exc = _raise_and_process(exc)
        finally:
            runtime.logger.remove(sink_id)

        assert api_exc.status_code == 422
        # Client sees the validator message (field location) but never the value.
        assert secret not in json.dumps(api_exc.tech_message)
        assert secret not in api_exc.user_message
        assert "Field required" in api_exc.user_message
        # And the value never reaches the server log either.
        assert secret not in "".join(logged)

    def test_api_exception_string_payload_keeps_error_id(self):
        api_exc = _raise_and_process(
            ApiException("Cannot change scope", "collection is locked", 409)
        )

        assert api_exc.status_code == 409
        assert "error_id" in api_exc.tech_message
        assert api_exc.tech_message["detail"] == "collection is locked"


class TestApiExceptionStr:
    """
    ``str(ApiException)`` must carry the user message: error monitoring
    groups issues by it, and callers that log a bare ``f"{e}"`` would
    otherwise record nothing about the cause.
    """

    def test_str_is_the_user_message(self):
        exc = ApiException("Calibration warning: few peaks", {"data": {}}, 200)

        assert str(exc) == "Calibration warning: few peaks"
        assert f"gave up: {exc}" == "gave up: Calibration warning: few peaks"

    def test_str_excludes_the_tech_message(self):
        secret_path = "/app/.runtime/secrets/jwt_secret_key.txt"
        exc = ApiException("Upload failed", {"path": secret_path}, 500)

        assert str(exc) == "Upload failed"
        assert secret_path not in str(exc)

    def test_processed_log_line_names_the_cause(self):
        records = []
        sink_id = runtime.logger.add(
            lambda message: records.append(message.record), level="TRACE"
        )
        try:
            _raise_and_process(
                ApiException("m/z fitting warning: few peaks", {"data": {}}, 200),
                context="Warning during Calibration Mz Fit",
            )
        finally:
            runtime.logger.remove(sink_id)

        assert len(records) == 1
        assert "m/z fitting warning: few peaks" in records[0]["message"]


class TestProcessExceptionLogLevels:
    """
    Routine client errors must stay below WARNING (records at WARNING and
    above are exported to error monitoring), while server-side faults must
    log at ERROR with their traceback attached.
    """

    def _process_and_capture(self, exc: Exception) -> dict:
        records = []
        sink_id = runtime.logger.add(
            lambda message: records.append(message.record), level="TRACE"
        )
        try:
            _raise_and_process(exc)
        finally:
            runtime.logger.remove(sink_id)
        assert len(records) == 1
        return records[0]

    @pytest.mark.parametrize(
        "exc",
        [
            HTTPException(status_code=401, detail="Not authenticated"),
            HTTPException(status_code=404, detail="Sample not found"),
            HTTPException(status_code=409, detail="Duplicate file"),
            ValueError("mz must be positive"),
            ApiException("Partial success", {"skipped_items": []}, status_code=207),
            RequestValidationError(
                [
                    {
                        "type": "missing",
                        "loc": ("body", "name"),
                        "msg": "Field required",
                        "input": {},
                    }
                ]
            ),
        ],
    )
    def test_expected_client_errors_log_at_info(self, exc):
        record = self._process_and_capture(exc)
        assert record["level"].name == "INFO"

    @pytest.mark.parametrize(
        "exc",
        [
            FileNotFoundError(2, "No such file", "/some/path"),  # default -> 500
            RuntimeError("boom"),  # 500
            HTTPException(status_code=500, detail="Upstream failed"),
            # Faults that reach the pipeline as ordinary Python exceptions
            AttributeError("'NoneType' object has no attribute '_engine'"),
            SQLAlchemyError("connection reset"),
        ],
    )
    def test_server_faults_log_at_error_with_traceback(self, exc):
        record = self._process_and_capture(exc)
        assert record["level"].name == "ERROR"
        assert record["exception"] is not None


class TestComposeUserMessage:
    """Nested error contexts must not stack into "Failed to X. Failed to Y. ..."."""

    def test_prepends_context_to_plain_message(self):
        assert (
            compose_user_message("Failed to Update Workspace", "Name is required.")
            == "Failed to Update Workspace. Name is required."
        )

    def test_does_not_stack_failed_to_prefixes(self):
        inner = "Failed to Create Sample Item. Sample not found."
        assert compose_user_message("Failed to Import Sample Items", inner) == inner

    def test_does_not_repeat_identical_context(self):
        inner = "Failed to Update Workspace. Name is required."
        assert compose_user_message("Failed to Update Workspace", inner) == inner

    def test_empty_inner_message_returns_context(self):
        assert compose_user_message("Failed to Update Workspace", "") == (
            "Failed to Update Workspace."
        )

    def test_http_exception_detail_is_not_duplicated(self):
        api_exc = _raise_and_process(
            HTTPException(
                status_code=429,
                detail="Too many requests. Please wait and try again.",
            ),
            context="Request failed",
        )

        assert api_exc.status_code == 429
        assert api_exc.user_message == (
            "Request failed. Too many requests. Please wait and try again."
        )
        assert api_exc.user_message.count("Too many requests") == 1

    def test_http_exception_with_failed_detail_keeps_single_prefix(self):
        api_exc = _raise_and_process(
            HTTPException(status_code=500, detail="Failed to get metadata."),
            context="Failed to Get Sample File Metadata",
        )

        assert api_exc.user_message == "Failed to get metadata."


class TestApiEResponseJson:
    def test_detail_field_carries_tech_message(self):
        exc = ApiException("User message", {"error_id": "abc123"}, status_code=500)
        response = api_e_response_json(exc)

        body = json.loads(response.body)
        assert body == {"error": "User message", "detail": {"error_id": "abc123"}}


@pytest.mark.parametrize(
    "exc, expected_status",
    [
        (RuntimeError("boom"), 500),
        (ValueError("bad value"), 400),
        (AttributeError("missing attr"), 500),
    ],
)
def test_status_codes_and_opaque_detail(exc, expected_status):
    api_exc = _raise_and_process(exc)
    assert api_exc.status_code == expected_status
    assert set(api_exc.tech_message) == {"error_id"}


def test_pool_timeout_maps_to_503():
    """
    Connection-pool starvation is transient server congestion: it must answer
    503, the status that says "busy, come back", rather than the generic
    SQLAlchemyError 500, so batch clients like the file converter retry
    promptly instead of treating it as a fault to report.
    """
    from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError

    api_exc = _raise_and_process(
        SQLAlchemyTimeoutError("QueuePool limit of size 3 overflow 7 reached")
    )

    assert api_exc.status_code == 503
    assert "busy" in api_exc.user_message.lower()
    assert "QueuePool" not in api_exc.user_message
