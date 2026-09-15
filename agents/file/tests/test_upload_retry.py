"""Unit tests for the upload retry policy in main.process_file_upload.

Hermetic: the upload call and runtime are monkeypatched; no sleeping, no
network.
"""

import pytest

from mascope_file_agent import main
from mascope_sdk.exceptions import (
    AuthenticationError,
    MascopeAPIError,
    MascopeConnectionError,
    NotFoundError,
    ValidationError,
)


class StubLogger:
    def __init__(self):
        self.errors = []

    def error(self, message):
        self.errors.append(message)

    def warning(self, message):
        pass

    def info(self, message):
        pass

    def debug(self, message):
        pass


class StubConfig:
    def __init__(self, source):
        self.source = source


class StubRuntime:
    def __init__(self, source):
        self.logger = StubLogger()
        self.config = StubConfig(source)


@pytest.fixture
def stub_runtime(monkeypatch, tmp_path):
    runtime = StubRuntime(str(tmp_path))
    monkeypatch.setattr(main, "runtime", runtime)
    monkeypatch.setattr(main.time, "sleep", lambda seconds: None)
    return runtime


def _failing_upload(monkeypatch, exception):
    calls = []

    def fail(filepath):
        calls.append(filepath)
        raise exception

    monkeypatch.setattr(main, "upload_sample_file", fail)
    return calls


@pytest.mark.parametrize(
    "exception, expected_guidance",
    [
        (
            NotFoundError("Not found.", status_code=404, url="http://x/api/upload"),
            "not the Mascope API",
        ),
        (ValidationError("Invalid.", status_code=422), "cannot succeed"),
        (AuthenticationError("Rejected.", status_code=401), "Retrying will not help"),
    ],
)
def test_no_retry_on_client_errors(
    stub_runtime, monkeypatch, tmp_path, exception, expected_guidance
):
    calls = _failing_upload(monkeypatch, exception)
    sample = tmp_path / "x.raw"
    sample.write_text("data")

    main.process_file_upload(str(sample))

    assert len(calls) == 1  # failed fast, no retries
    assert (tmp_path / "failed_uploads" / "x.raw").exists()
    assert any(expected_guidance in e for e in stub_runtime.logger.errors)


def test_retries_on_connection_errors(stub_runtime, monkeypatch, tmp_path):
    calls = _failing_upload(monkeypatch, MascopeConnectionError("refused"))
    sample = tmp_path / "x.raw"
    sample.write_text("data")

    main.process_file_upload(str(sample), max_retries=3)

    assert len(calls) == 3  # transient errors keep retrying to the cap
    assert (tmp_path / "failed_uploads" / "x.raw").exists()


def test_unknown_instrument_explains_the_filename_rule(
    stub_runtime, monkeypatch, tmp_path
):
    """The commonest permanent rejection must name the fix, not just the fault.

    A file the acquisition software named without the instrument is refused
    forever; the operator can fix it at the source or with filename_prefix,
    and neither is guessable from "Invalid value".
    """
    calls = _failing_upload(
        monkeypatch,
        ValidationError(
            "Invalid value. Failed to get instrument type for instrument x.",
            status_code=400,
        ),
    )
    sample = tmp_path / "x_run.raw"
    sample.write_text("data")

    main.process_file_upload(str(sample))

    assert len(calls) == 1
    guidance = " ".join(stub_runtime.logger.errors)
    assert "filename_prefix" in guidance
    assert "first underscore" in guidance


def test_rate_limiting_is_still_retried(stub_runtime, monkeypatch, tmp_path):
    """429 is the one 4xx worth waiting out - it clears on its own."""
    calls = _failing_upload(
        monkeypatch, MascopeAPIError("Too many requests", status_code=429)
    )
    sample = tmp_path / "x.raw"
    sample.write_text("data")

    main.process_file_upload(str(sample), max_retries=3)

    assert len(calls) == 3


def test_the_give_up_line_says_where_the_file_went(stub_runtime, monkeypatch, tmp_path):
    """Operators need to find the file and know how to make it try again."""
    _failing_upload(monkeypatch, ValidationError("Invalid.", status_code=400))
    sample = tmp_path / "x.raw"
    sample.write_text("data")

    main.process_file_upload(str(sample))

    tail = " ".join(stub_runtime.logger.errors)
    assert "failed_uploads" in tail
    assert "1 attempt." in tail  # not "1 attempts"
    assert "watched folder" in tail


def test_a_vanished_file_does_not_raise_in_the_worker(
    stub_runtime, monkeypatch, tmp_path
):
    """A file deleted mid-retry must not throw where nobody sees it.

    process_file_upload runs in a thread pool whose exceptions are never
    collected, so the copy that preserves a failed file has to fail loudly in
    the log instead of silently killing the task.
    """
    _failing_upload(monkeypatch, ValidationError("Invalid.", status_code=400))
    missing = tmp_path / "gone.raw"

    main.process_file_upload(str(missing))

    assert any("could not keep a copy" in e for e in stub_runtime.logger.errors)
