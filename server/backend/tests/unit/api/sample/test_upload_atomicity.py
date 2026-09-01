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

The multipart endpoint (``upload_sample_files``) gates on converter availability
because it can refuse before writing anything. The tus completion handler
(``upload_sample_file``) does not: by the time it runs the transfer is already
complete, so it stores the file unconditionally and the gate lives up front in
the tus pre_create hook instead.
"""

import asyncio
import io
import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

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
async def test_tus_completion_stores_the_file_without_regating_on_converter(
    filestreams, tmp_path_factory
):
    """The tus completion handler stores the transferred file, converter or not.

    The gate is enforced up front in the pre_create hook; re-checking at
    completion could only refuse a file already transferred in full, which
    tuspyserver has marked complete - so the client would read success while the
    bytes were dropped. Even with the converter reported absent (a converter
    that dropped mid-transfer), the file must land in the filestore, where the
    watcher recovers it on reconnect, rather than being lost.
    """
    staging = tmp_path_factory.mktemp("tus-staging")
    staged = staging / "sample.raw"
    staged.write_bytes(b"raw file bytes")

    with (
        patch(f"{_CTRL}.is_service_connected", new=AsyncMock(return_value=False)),
        patch(f"{_CTRL}.event_emitter.emit", new=AsyncMock()) as emit,
    ):
        result = await sample_files_controller.upload_sample_file(
            file_path=str(staged), user=_fake_user(), access_token="token"
        )

    assert result["status"] == "success"
    # The staged source was moved into the filestore under its final name.
    assert not staged.exists()
    assert (filestreams / "sample.raw").read_bytes() == b"raw file bytes"
    assert [p.name for p in filestreams.iterdir()] == ["sample.raw"]
    # Context is still emitted so a connected converter processes it at once.
    emit.assert_awaited_once()


@pytest.mark.asyncio
async def test_multipart_upload_registers_context_before_staging_anything(
    filestreams,
):
    """The converter must know the file before any of its bytes are written.

    The watcher queues a file once its size is stable, and the converter reads
    the uploader's identity from an in-memory context keyed by filename. A
    context emitted after the rename is racing that poll across Redis pub/sub;
    losing the race quarantines the file in ``failed_files``, which nothing
    retries.

    Emitting between staging and publication would order it correctly against
    the watcher but put the upload's only await where a cancellation destroys
    the staged bytes, so the assertion is the stronger one: at emit time
    nothing exists in the filestore at all.
    """
    seen_at_emit = []

    async def _record(*_args, **_kwargs):
        seen_at_emit.append(sorted(p.name for p in filestreams.iterdir()))

    upload = _FakeUploadFile("sample.raw", io.BytesIO(b"raw file bytes"))

    with (
        patch(f"{_CTRL}.is_service_connected", new=AsyncMock(return_value=True)),
        patch(f"{_CTRL}.event_emitter.emit", new=AsyncMock(side_effect=_record)),
    ):
        result = await sample_files_controller.upload_sample_files(
            files=[upload], user=_fake_user(), access_token="token"
        )

    assert result["status"] == "success"
    # Exactly one emit, and nothing was on disk when it happened - not the
    # final name, and not a staging file either.
    assert len(seen_at_emit) == 1
    assert seen_at_emit[0] == []
    assert [p.name for p in filestreams.iterdir()] == ["sample.raw"]


@pytest.mark.asyncio
async def test_tus_completion_registers_context_before_staging_anything(
    filestreams, tmp_path_factory
):
    """Same ordering contract on the tus completion path.

    Here the source is the transferred upload rather than a request body, so
    the emit must also come before the move that consumes it.
    """
    seen_at_emit = []

    staging = tmp_path_factory.mktemp("tus-staging")
    staged = staging / "sample.raw"
    staged.write_bytes(b"raw file bytes")

    async def _record(*_args, **_kwargs):
        seen_at_emit.append(
            (sorted(p.name for p in filestreams.iterdir()), staged.exists())
        )

    with (
        patch(f"{_CTRL}.is_service_connected", new=AsyncMock(return_value=True)),
        patch(f"{_CTRL}.event_emitter.emit", new=AsyncMock(side_effect=_record)),
    ):
        result = await sample_files_controller.upload_sample_file(
            file_path=str(staged), user=_fake_user(), access_token="token"
        )

    assert result["status"] == "success"
    assert len(seen_at_emit) == 1
    # Nothing staged in the filestore yet, and the transferred file untouched.
    assert seen_at_emit[0] == ([], True)
    assert (filestreams / "sample.raw").read_bytes() == b"raw file bytes"


@pytest.mark.asyncio
async def test_a_cancelled_registration_leaves_the_transferred_file_intact(
    filestreams, tmp_path_factory
):
    """A cancellation at the registration must not consume the upload.

    Registering is the only await on this path, and it is the slow one - it
    crosses Redis pub/sub, and it is slowest under exactly the load the
    ordering exists for. A request task can be cancelled at it: the client
    drops the connection, or the worker is shut down mid-request.

    Between the staging move and the publish, that cancellation would run the
    cleanup on the only copy of the file - the source is gone, nothing is
    published - and destroy it. tuspyserver marks the upload complete before it
    runs this handler, so the client reads it as stored. That is the silent
    loss this whole ordering exists to prevent, so it must not be reintroduced
    by where the emit sits.
    """
    staging = tmp_path_factory.mktemp("tus-staging")
    staged = staging / "sample.raw"
    staged.write_bytes(b"raw file bytes")

    # CancelledError, not a plain exception: it is what a dropped connection
    # raises, and being a BaseException it passes straight through the
    # controller's own error handling to the cleanup in the finally.
    with (
        patch(f"{_CTRL}.is_service_connected", new=AsyncMock(return_value=True)),
        patch(
            f"{_CTRL}.event_emitter.emit",
            new=AsyncMock(side_effect=asyncio.CancelledError),
        ),
        pytest.raises(asyncio.CancelledError),
    ):
        await sample_files_controller.upload_sample_file(
            file_path=str(staged), user=_fake_user(), access_token="token"
        )

    # The transferred file is still where tus left it, so nothing is lost:
    # it is the source that has not been consumed, not a copy.
    assert staged.read_bytes() == b"raw file bytes"
    assert [p.name for p in filestreams.iterdir()] == []


@pytest.mark.asyncio
async def test_concurrent_uploads_of_one_name_do_not_share_a_staging_file(
    filestreams, tmp_path_factory
):
    """A restarted transfer must not corrupt the upload it overlaps.

    Staging used a single "<final>.part" per destination, so two uploads of the
    same filename - what a client does when it restarts an interrupted transfer
    - wrote to the same path. One mover's bytes replaced the other's, one rename
    consumed the shared file, and the loser's rename raised FileNotFoundError on
    a path that had already been moved away.
    """
    staging = tmp_path_factory.mktemp("tus-staging")
    # threading, not asyncio: these are set and waited on from two different
    # threads, each running its own event loop, which an asyncio.Event does not
    # support - it would only happen to work here because nothing ever awaits
    # one of them.
    started = threading.Event()
    release = threading.Event()
    real_move = sample_files_controller.shutil.move

    def _move_then_hold(src, dst):
        # Suspend the first upload with its bytes staged but not yet published,
        # which is the window the two uploads used to share. Holding at the
        # converter emit instead would prove nothing: before this fix that emit
        # ran *after* the rename, so the staged file was already gone.
        result = real_move(src, dst)
        if not started.is_set():
            started.set()
            while not release.is_set():
                time.sleep(0.01)
        return result

    async def _run(source: bytes, hold: bool):
        # Same basename, different source directories: the destination name is
        # derived from the basename, so both uploads target one file in
        # filestreams - which is what makes their staging paths collide.
        source_dir = staging / uuid4().hex
        source_dir.mkdir()
        staged = source_dir / "sample.raw"
        staged.write_bytes(source)
        with (
            patch(f"{_CTRL}.is_service_connected", new=AsyncMock(return_value=True)),
            patch(f"{_CTRL}.event_emitter.emit", new=AsyncMock()),
        ):
            if hold:
                with patch.object(
                    sample_files_controller.shutil, "move", _move_then_hold
                ):
                    return await sample_files_controller.upload_sample_file(
                        file_path=str(staged), user=_fake_user(), access_token="token"
                    )
            return await sample_files_controller.upload_sample_file(
                file_path=str(staged), user=_fake_user(), access_token="token"
            )

    # The hold is a blocking sleep, so the first upload has to run off the event
    # loop for the second to make progress against it.
    first = asyncio.create_task(asyncio.to_thread(asyncio.run, _run(b"first", True)))
    try:
        while not started.is_set():
            await asyncio.sleep(0.01)

        second = await _run(b"second upload", False)
    finally:
        # Always, even if the wait or the second upload raised. The held
        # upload is blocking in a worker thread from the default executor,
        # whose threads are non-daemon and joined at interpreter exit: leaving
        # it spinning hangs the whole run, and it hangs during shutdown, so
        # pytest never gets to report the failure that caused it.
        release.set()

    assert second["status"] == "success"
    assert (await first)["status"] == "success"

    # Both completed, and no staging litter survived either of them.
    assert [p.name for p in filestreams.iterdir()] == ["sample.raw"]
