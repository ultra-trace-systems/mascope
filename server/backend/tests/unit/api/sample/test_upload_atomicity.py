"""
Unit tests for atomic sample file uploads (``upload_sample_files``).

The filestreams folder is polled by the file converter's watcher, which queues
a file once its size is stable across two scans. These tests pin the atomicity
contract: bytes must never be observable under the final watched name until
the upload is complete, and a failed upload must leave nothing behind - a
partially written file under its final name gets picked up by the watcher and
its remaining bytes are silently lost (the cause of nondeterministic sample
drops in the reproducibility CI run).

The controller is called directly with a fake ``UploadFile``; the filestore
path, the converter-availability gate, and the file-converter event emission
are mocked - no DB, HTTP, Redis, or Socket.IO required.
"""

import io
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from mascope_backend.api.controllers.sample.files import sample_files_controller
from mascope_backend.api.lib.exceptions.api_exceptions import ApiException


_CTRL = "mascope_backend.api.controllers.sample.files.sample_files_controller"


@contextmanager
def _converter_available():
    """Pass the availability gate and swallow the converter event emission."""
    with (
        patch(f"{_CTRL}.is_service_connected", new=AsyncMock(return_value=True)),
        patch(f"{_CTRL}.event_emitter.emit", new=AsyncMock()),
    ):
        yield


def _fake_user():
    return SimpleNamespace(id=1, username="tester", role_id=1)


class _FakeUploadFile:
    """Duck-typed stand-in for FastAPI's UploadFile."""

    def __init__(self, filename: str, reader):
        self.filename = filename
        self.file = reader


class _ExplodingReader(io.BytesIO):
    """Yields one chunk, then fails - simulates an upload dying mid-write."""

    def __init__(self):
        super().__init__(b"first chunk")
        self._reads = 0

    def read(self, *args, **kwargs):
        self._reads += 1
        if self._reads > 1:
            raise OSError("connection lost mid-upload")
        return super().read(*args, **kwargs)


@pytest.fixture
def filestreams(tmp_path, monkeypatch):
    """Point the controller's filestreams directory at a temp folder."""
    monkeypatch.setattr(
        sample_files_controller.runtime.config, "filestreams", str(tmp_path)
    )
    return tmp_path


@pytest.mark.asyncio
async def test_successful_upload_lands_only_the_final_file(filestreams):
    upload = _FakeUploadFile("sample.raw", io.BytesIO(b"raw file bytes"))

    with _converter_available():
        result = await sample_files_controller.upload_sample_files(
            files=[upload], user=_fake_user(), access_token="token"
        )

    assert result["status"] == "success"
    assert (filestreams / "sample.raw").read_bytes() == b"raw file bytes"
    assert [p.name for p in filestreams.iterdir()] == ["sample.raw"]


@pytest.mark.asyncio
async def test_failed_upload_leaves_no_file_behind(filestreams):
    """
    A write that dies partway must leave neither the final name (the watcher
    would ingest a truncated file) nor temp-file litter.
    """
    upload = _FakeUploadFile("sample.raw", _ExplodingReader())

    with _converter_available():
        result = await sample_files_controller.upload_sample_files(
            files=[upload], user=_fake_user(), access_token="token"
        )

    assert result["status"] == "error"
    assert result["data"]["failed_uploads"][0]["filename"] == "sample.raw"
    assert list(filestreams.iterdir()) == []


@pytest.mark.asyncio
async def test_upload_is_refused_when_no_converter_is_connected(filestreams):
    """
    Uploads 503 before touching the filestore: the converter learns of a file
    only from the emitted socket payload, so a file accepted with no converter
    connected would sit unregistered until the watcher quarantines it.
    """
    upload = _FakeUploadFile("sample.raw", io.BytesIO(b"raw file bytes"))

    with (
        patch(f"{_CTRL}.is_service_connected", new=AsyncMock(return_value=False)),
        patch(f"{_CTRL}.event_emitter.emit", new=AsyncMock()) as emit,
    ):
        with pytest.raises(ApiException) as exc_info:
            await sample_files_controller.upload_sample_files(
                files=[upload], user=_fake_user(), access_token="token"
            )

    assert exc_info.value.status_code == 503
    emit.assert_not_awaited()
    assert list(filestreams.iterdir()) == []


@pytest.mark.asyncio
async def test_tus_upload_is_refused_when_no_converter_is_connected(
    filestreams, tmp_path_factory
):
    """The TUS completion path refuses before moving the staged file."""
    staging = tmp_path_factory.mktemp("tus-staging")
    staged = staging / "upload-1"
    staged.write_bytes(b"raw file bytes")

    with (
        patch(f"{_CTRL}.is_service_connected", new=AsyncMock(return_value=False)),
        patch(f"{_CTRL}.event_emitter.emit", new=AsyncMock()) as emit,
    ):
        with pytest.raises(ApiException) as exc_info:
            await sample_files_controller.upload_sample_file(
                file_path=str(staged), user=_fake_user(), access_token="token"
            )

    assert exc_info.value.status_code == 503
    emit.assert_not_awaited()
    # The staged source is untouched and nothing landed in the filestore.
    assert staged.read_bytes() == b"raw file bytes"
    assert list(filestreams.iterdir()) == []
