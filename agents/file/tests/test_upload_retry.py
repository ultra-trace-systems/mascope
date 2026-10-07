"""Unit tests for the upload retry policy in FileUploader.process_file_upload.

Hermetic: the upload call is monkeypatched; no waiting between attempts, no
network.
"""

import pytest

from mascope_file_agent import uploader
from mascope_file_agent.credentials import Credentials
from mascope_file_agent.uploader import FileUploader
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


@pytest.fixture
def file_uploader(monkeypatch, make_settings):
    """An uploader watching the test's folder, with no wait between attempts."""
    monkeypatch.setattr(uploader, "RETRY_DELAY", 0)
    settings = make_settings()
    logger = StubLogger()
    credentials = Credentials(
        "https://mascope.example.com", settings.host, settings.access_token, logger
    )
    return FileUploader(
        settings, "https://mascope.example.com", credentials, logger=logger
    )


def _failing_upload(monkeypatch, file_uploader, exception):
    calls = []

    def fail(filepath):
        calls.append(filepath)
        raise exception

    monkeypatch.setattr(file_uploader, "upload_sample_file", fail)
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
    file_uploader, monkeypatch, tmp_path, exception, expected_guidance
):
    calls = _failing_upload(monkeypatch, file_uploader, exception)
    sample = tmp_path / "x.raw"
    sample.write_text("data")

    file_uploader.process_file_upload(str(sample))

    assert len(calls) == 1  # failed fast, no retries
    assert (tmp_path / "failed_uploads" / "x.raw").exists()
    assert any(expected_guidance in e for e in file_uploader.logger.errors)


def test_retries_on_connection_errors(file_uploader, monkeypatch, tmp_path):
    calls = _failing_upload(
        monkeypatch, file_uploader, MascopeConnectionError("refused")
    )
    sample = tmp_path / "x.raw"
    sample.write_text("data")

    file_uploader.process_file_upload(str(sample), max_retries=3)

    assert len(calls) == 3  # transient errors keep retrying to the cap
    assert (tmp_path / "failed_uploads" / "x.raw").exists()


def test_unknown_instrument_explains_the_filename_rule(
    file_uploader, monkeypatch, tmp_path
):
    """The commonest permanent rejection must name the fix, not just the fault.

    A file the acquisition software named without the instrument is refused
    forever; the operator can fix it at the source or with filename_prefix,
    and neither is guessable from "Invalid value".
    """
    calls = _failing_upload(
        monkeypatch,
        file_uploader,
        ValidationError(
            "Invalid value. Failed to get instrument type for instrument x.",
            status_code=400,
        ),
    )
    sample = tmp_path / "x_run.raw"
    sample.write_text("data")

    file_uploader.process_file_upload(str(sample))

    assert len(calls) == 1
    guidance = " ".join(file_uploader.logger.errors)
    assert "filename_prefix" in guidance
    assert "first underscore" in guidance


def test_rate_limiting_is_still_retried(file_uploader, monkeypatch, tmp_path):
    """429 is the one 4xx worth waiting out - it clears on its own."""
    calls = _failing_upload(
        monkeypatch,
        file_uploader,
        MascopeAPIError("Too many requests", status_code=429),
    )
    sample = tmp_path / "x.raw"
    sample.write_text("data")

    file_uploader.process_file_upload(str(sample), max_retries=3)

    assert len(calls) == 3


def test_the_give_up_line_says_where_the_file_went(
    file_uploader, monkeypatch, tmp_path
):
    """Operators need to find the file and know how to make it try again."""
    _failing_upload(
        monkeypatch, file_uploader, ValidationError("Invalid.", status_code=400)
    )
    sample = tmp_path / "x.raw"
    sample.write_text("data")

    file_uploader.process_file_upload(str(sample))

    tail = " ".join(file_uploader.logger.errors)
    assert "failed_uploads" in tail
    assert "1 attempt." in tail  # not "1 attempts"
    assert "watched folder" in tail


def test_a_vanished_file_does_not_raise_in_the_worker(
    file_uploader, monkeypatch, tmp_path
):
    """A file deleted mid-retry must not throw where nobody sees it.

    process_file_upload runs on a worker thread, where nobody is waiting for
    what it raises, so the copy that preserves a failed file has to fail
    loudly in the log instead of silently ending the upload.
    """
    _failing_upload(
        monkeypatch, file_uploader, ValidationError("Invalid.", status_code=400)
    )
    missing = tmp_path / "gone.raw"

    file_uploader.process_file_upload(str(missing))

    assert any("could not keep a copy" in e for e in file_uploader.logger.errors)
