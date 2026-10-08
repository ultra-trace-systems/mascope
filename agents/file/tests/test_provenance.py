"""Unit tests for what goes with an upload to say where the file came from.

Hermetic: the SDK upload is monkeypatched and the server's capabilities are
a scripted stand-in. What is pinned is the promise the module makes: a file's
acquisition record and its hash go to a server that keeps them, and neither
ever costs a file its upload.
"""

import hashlib
import json
import os

import pytest
from watchdog.events import FileCreatedEvent, FileMovedEvent

from mascope_file_agent import provenance, uploader
from mascope_file_agent.credentials import Credentials
from mascope_file_agent.provenance import UploadProvenance, file_sha256
from mascope_file_agent.uploader import FAILED_UPLOADS_DIR, FileUploader
from mascope_file_agent.watcher import FileSystemWatcher
from mascope_sdk import acquisition
from mascope_sdk.exceptions import (
    MascopeAPIError,
    MascopeConnectionError,
    NotFoundError,
    ValidationError,
)


URL = "https://mascope.example.com"

RECORD = {
    "schema": "mascope-acquisition/1",
    "source_filename": "x.raw",
    "agent_id": "3f0e8f0c-5d0b-4c7e-9a43-0d8f6c1b2a10",
    "sequence_run_id": "0199b6a0-7c00-7000-8000-000000000001",
    "step_id": "0199b6a0-7c00-7000-8000-000000000002",
    "acquisition_id": "0199b6a0-7c00-7000-8000-000000000003",
    "ionization": "NO3",
}


class RecordingLogger:
    def __init__(self):
        self.lines: list[tuple[str, str]] = []

    def __getattr__(self, level):
        return lambda message: self.lines.append((level, message))

    def said(self, level):
        return [message for lvl, message in self.lines if lvl == level]


class Server:
    """Stands in for asking the server: says what it is set to say."""

    def __init__(self, keeps: bool | None = True):
        self.keeps = keeps
        self.asked = 0
        #: Whether "keeps nothing" stands for a refusal of the question.
        self.refused = False

    def has(self, capability):
        assert capability == "files_accept_acquisition_metadata"
        self.asked += 1
        return self.keeps


class Uploads:
    """Stands in for the SDK's upload: records each call, refuses as scripted."""

    def __init__(self, *errors):
        self.calls: list[dict] = []
        self.errors = list(errors)

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if self.errors:
            error = self.errors.pop(0)
            if error is not None:
                raise error


@pytest.fixture
def sample(tmp_path):
    sample = tmp_path / "x.raw"
    sample.write_bytes(b"raw-bytes")
    return sample


@pytest.fixture
def sidecar(sample):
    """The sample's acquisition record, written beside it."""
    path = sample.parent / "x.raw.mascope.json"
    path.write_text(json.dumps(RECORD, indent=2), encoding="utf-8")
    return path


@pytest.fixture
def build(monkeypatch, make_settings):
    """An uploader with a stand-in server, and the uploads it makes."""

    def build(keeps: bool | None = True, *errors):
        uploads = Uploads(*errors)
        monkeypatch.setattr(uploader, "api_post_file_tus", uploads)
        monkeypatch.setattr(uploader, "RETRY_DELAY", 0)
        logger = RecordingLogger()
        server = Server(keeps)
        file_uploader = FileUploader(
            make_settings(),
            URL,
            Credentials(URL, "tok", logger),
            logger=logger,
            provenance=UploadProvenance(server, logger),
        )
        return file_uploader, uploads, server

    return build


# ---------------------------------------------------------------------------
# A server that keeps them
# ---------------------------------------------------------------------------


def test_a_files_record_and_hash_go_with_its_upload(build, sample, sidecar):
    file_uploader, uploads, _ = build()

    file_uploader.upload_sample_file(str(sample))

    (call,) = uploads.calls
    # The document as it was written, not one the agent wrote again.
    assert call["acquisition"] == sidecar.read_bytes()
    assert call["sha256"] == hashlib.sha256(b"raw-bytes").hexdigest()
    assert file_uploader.logger.said("info") == [
        "File upload of file x.raw succeeded!",
        "x.raw: its acquisition record went with it.",
    ]


def test_a_file_with_no_record_goes_with_its_hash(build, sample):
    file_uploader, uploads, _ = build()

    file_uploader.upload_sample_file(str(sample))

    (call,) = uploads.calls
    assert "acquisition" not in call
    assert call["sha256"] == hashlib.sha256(b"raw-bytes").hexdigest()
    assert file_uploader.logger.said("warning") == []


def test_the_hash_is_of_the_whole_file(tmp_path):
    large = tmp_path / "large.raw"
    content = bytes(range(256)) * 20_000  # 5 MB: more than one read
    large.write_bytes(content)

    assert file_sha256(str(large)) == hashlib.sha256(content).hexdigest()


# ---------------------------------------------------------------------------
# A server that does not
# ---------------------------------------------------------------------------


def test_a_server_that_keeps_neither_is_sent_neither(build, sample, sidecar):
    file_uploader, uploads, _ = build(False)

    file_uploader.upload_sample_file(str(sample))

    (call,) = uploads.calls
    assert "acquisition" not in call
    assert "sha256" not in call
    assert file_uploader.logger.said("info") == ["File upload of file x.raw succeeded!"]


def test_the_log_says_once_that_the_records_are_not_kept(build, sample, sidecar):
    file_uploader, uploads, _ = build(False)
    other = sample.parent / "y.raw"
    other.write_bytes(b"more")
    (sample.parent / "y.raw.mascope.json").write_text(
        json.dumps({**RECORD, "source_filename": "y.raw"}), encoding="utf-8"
    )

    file_uploader.upload_sample_file(str(sample))
    file_uploader.upload_sample_file(str(other))

    assert len(uploads.calls) == 2
    (line,) = file_uploader.logger.said("warning")
    assert line.startswith("x.raw has an acquisition record beside it")
    assert line.endswith("until the server is updated.")


def test_nothing_is_said_of_records_where_there_are_none(build, sample):
    file_uploader, _, _ = build(False)

    file_uploader.upload_sample_file(str(sample))

    assert file_uploader.logger.said("warning") == []


# ---------------------------------------------------------------------------
# A server that has not said
# ---------------------------------------------------------------------------


def test_a_file_with_a_record_waits_for_a_server_that_has_not_answered(
    build, sample, sidecar
):
    """Uploaded now, the record would be left behind for good; a moment later
    the server can say whether it keeps one."""
    file_uploader, uploads, server = build(None)

    with pytest.raises(MascopeConnectionError, match="acquisition record of x.raw"):
        file_uploader.upload_sample_file(str(sample))

    assert uploads.calls == []


def test_the_wait_is_one_of_the_uploads_attempts(build, sample, sidecar):
    file_uploader, uploads, server = build(None)
    answers = iter([None, None, True])
    server.has = lambda capability: next(answers)

    file_uploader.process_file_upload(str(sample))

    (call,) = uploads.calls
    assert call["acquisition"] == sidecar.read_bytes()
    assert not (sample.parent / FAILED_UPLOADS_DIR).exists()
    assert [
        line for line in file_uploader.logger.said("info") if "attempt" in line
    ] == [
        "Upload attempt 1/10 for file x.raw failed: The server could not be "
        "asked whether it keeps the acquisition record of x.raw.",
        "Upload attempt 2/10 for file x.raw failed: The server could not be "
        "asked whether it keeps the acquisition record of x.raw.",
    ]


def test_a_file_with_no_record_does_not_wait(build, sample):
    file_uploader, uploads, _ = build(None)

    file_uploader.upload_sample_file(str(sample))

    (call,) = uploads.calls
    assert "acquisition" not in call
    assert "sha256" not in call


# ---------------------------------------------------------------------------
# A record that cannot be used
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content, reason",
    [
        (b"{not json", "it is not JSON"),
        (
            json.dumps({**RECORD, "source_filename": "w.raw"}).encode(),
            "it is the record of 'w.raw', not of this file",
        ),
        (
            json.dumps({**RECORD, "schema": "mascope-acquisition/2"}).encode(),
            "its schema is 'mascope-acquisition/2'",
        ),
        (json.dumps({**RECORD, "later": "p" * 20_000}).encode(), "16384 at most"),
    ],
)
def test_a_file_goes_without_a_record_that_cannot_be_used(
    build, sample, content, reason
):
    file_uploader, uploads, _ = build()
    (sample.parent / "x.raw.mascope.json").write_bytes(content)

    file_uploader.upload_sample_file(str(sample))

    (call,) = uploads.calls
    assert "acquisition" not in call
    assert call["sha256"] == hashlib.sha256(b"raw-bytes").hexdigest()
    (line,) = file_uploader.logger.said("warning")
    assert line.startswith("x.raw: the acquisition record beside it is not sent, as ")
    assert reason in line
    assert line.endswith("The file is uploaded without it.")


def test_an_unusable_record_is_said_once_for_all_of_a_files_attempts(build, sample):
    file_uploader, uploads, _ = build(True, MascopeConnectionError("down"), None)
    (sample.parent / "x.raw.mascope.json").write_bytes(b"[]")

    file_uploader.process_file_upload(str(sample))

    assert len(uploads.calls) == 2
    assert len(file_uploader.logger.said("warning")) == 1


def test_an_unusable_record_does_not_wait_for_the_server(build, sample):
    file_uploader, uploads, _ = build(None)
    (sample.parent / "x.raw.mascope.json").write_bytes(b"[]")

    file_uploader.upload_sample_file(str(sample))

    assert len(uploads.calls) == 1


# ---------------------------------------------------------------------------
# A record the server refuses
# ---------------------------------------------------------------------------


def refusal(status_code=431):
    return ValidationError(
        "Request Header Fields Too Large", status_code=status_code, url=URL
    )


def test_a_file_refused_with_its_record_is_uploaded_without_it(build, sample, sidecar):
    """A proxy in front of the server can refuse the record for its size, and
    that must not set an acquisition aside."""
    file_uploader, uploads, _ = build(True, refusal(), None)

    file_uploader.process_file_upload(str(sample))

    with_record, without = uploads.calls
    assert with_record["acquisition"] == sidecar.read_bytes()
    assert "acquisition" not in without
    assert without["sha256"] == with_record["sha256"]
    (line,) = file_uploader.logger.said("warning")
    assert line.startswith("x.raw: refused with its acquisition record (")
    assert "Request Header Fields Too Large" in line
    assert line.endswith("Uploading it without the record.")
    assert file_uploader.logger.said("info") == ["File upload of file x.raw succeeded!"]
    assert not (sample.parent / FAILED_UPLOADS_DIR).exists()


def test_a_file_refused_either_way_is_refused(build, sample, sidecar):
    file_uploader, uploads, _ = build(True, refusal(400), refusal(400))

    with pytest.raises(ValidationError):
        file_uploader.upload_sample_file(str(sample))

    assert len(uploads.calls) == 2


def test_a_refusal_of_a_file_with_no_record_is_not_tried_again(build, sample):
    file_uploader, uploads, _ = build(True, refusal(400))

    with pytest.raises(ValidationError):
        file_uploader.upload_sample_file(str(sample))

    assert len(uploads.calls) == 1


# ---------------------------------------------------------------------------
# The record and the watched folder
# ---------------------------------------------------------------------------


def test_a_record_is_set_aside_with_its_file(build, sample, sidecar):
    """Put back in the watched folder together, they are uploaded together."""
    file_uploader, _, _ = build(True, NotFoundError("gone", status_code=404, url=URL))

    file_uploader.process_file_upload(str(sample))

    failed = sample.parent / FAILED_UPLOADS_DIR
    assert (failed / "x.raw").read_bytes() == b"raw-bytes"
    assert (failed / "x.raw.mascope.json").read_bytes() == sidecar.read_bytes()


def test_a_file_with_no_record_is_set_aside_alone(build, sample):
    file_uploader, _, _ = build(True, NotFoundError("gone", status_code=404, url=URL))

    file_uploader.process_file_upload(str(sample))

    failed = sample.parent / FAILED_UPLOADS_DIR
    assert [path.name for path in failed.iterdir()] == ["x.raw"]


@pytest.mark.parametrize("mask", ["*.raw", "*", "*.json"])
def test_a_record_is_never_a_file_to_upload(tmp_path, mask):
    """Whatever the mask takes: a record goes with its file's upload."""
    watcher = FileSystemWatcher(str(tmp_path), mask, logger=RecordingLogger())
    record = str(tmp_path / "x.raw.mascope.json")

    watcher.handler.dispatch(FileCreatedEvent(record))
    watcher.handler.dispatch(FileMovedEvent(str(tmp_path / "x.tmp"), record))

    assert watcher._seen.empty()


def test_the_mask_still_takes_what_it_took(tmp_path):
    watcher = FileSystemWatcher(str(tmp_path), "*.raw", logger=RecordingLogger())

    watcher.handler.dispatch(FileCreatedEvent(str(tmp_path / "x.raw")))

    assert watcher._seen.get_nowait() == str(tmp_path / "x.raw")


def test_an_uploader_built_without_it_sends_neither(
    monkeypatch, make_settings, sample, sidecar
):
    uploads = Uploads()
    monkeypatch.setattr(uploader, "api_post_file_tus", uploads)
    logger = RecordingLogger()
    file_uploader = FileUploader(
        make_settings(), URL, Credentials(URL, "tok", logger), logger=logger
    )

    file_uploader.upload_sample_file(str(sample))

    (call,) = uploads.calls
    assert "acquisition" not in call
    assert "sha256" not in call


def test_the_capability_asked_for_is_the_one_the_schema_names():
    assert acquisition.CAPABILITY == "files_accept_acquisition_metadata"


# ---------------------------------------------------------------------------
# A server that would not say
# ---------------------------------------------------------------------------


def test_a_refusal_to_say_is_not_called_a_server_to_update(build, sample, sidecar):
    """A refused credential answers like a server too old to be asked, and
    "update the server" is the wrong thing to tell its operator."""
    file_uploader, uploads, server = build(False)
    server.refused = True

    file_uploader.upload_sample_file(str(sample))
    file_uploader.upload_sample_file(str(sample))

    assert all("acquisition" not in call for call in uploads.calls)
    (line,) = file_uploader.logger.said("warning")
    assert line == (
        "x.raw has an acquisition record beside it, and the server refused to "
        "say whether it keeps them: it is too old to be asked, or it refused "
        "this machine's credential. This attempt to upload the file goes "
        "without its record."
    )


def test_a_server_that_then_answers_and_keeps_none_is_said_to_keep_none(
    build, sample, sidecar
):
    file_uploader, _, server = build(False)
    server.refused = True
    file_uploader.upload_sample_file(str(sample))

    server.refused = False
    file_uploader.upload_sample_file(str(sample))

    refused, not_kept = file_uploader.logger.said("warning")
    assert "refused to say" in refused
    assert not_kept.endswith("until the server is updated.")


# ---------------------------------------------------------------------------
# Whatever reading a record raises
# ---------------------------------------------------------------------------


def test_a_file_goes_without_its_record_whatever_reading_it_raised(
    build, sample, sidecar, monkeypatch
):
    """Not only the faults a record is known to have. An error let out of
    here would be taken for an upload that failed, and after ten of them the
    file would be set aside without one upload having been made."""

    def fails(path):
        raise RuntimeError("out of the blue")

    monkeypatch.setattr(provenance.acquisition, "read_sidecar", fails)
    file_uploader, uploads, _ = build()

    file_uploader.process_file_upload(str(sample))

    (call,) = uploads.calls
    assert "acquisition" not in call
    assert not (sample.parent / FAILED_UPLOADS_DIR).exists()
    (line,) = file_uploader.logger.said("warning")
    assert line == (
        "x.raw: the acquisition record beside it is not sent, as reading it "
        "failed (RuntimeError: out of the blue). The file is uploaded without it."
    )


def test_a_sidecar_of_little_but_brackets_costs_its_file_nothing(build, sample):
    """On some Pythons 2 KB of them exhaust the JSON reader, with an error
    that is no ValueError; on the others they are read, and are a document
    nobody should have to walk. Either way the file is uploaded."""
    (sample.parent / "x.raw.mascope.json").write_bytes(
        b'{"later": ' + b"[" * 2000 + b"]" * 2000 + b"}"
    )
    file_uploader, uploads, _ = build()

    file_uploader.process_file_upload(str(sample))

    assert len(uploads.calls) == 1
    (line,) = file_uploader.logger.said("warning")
    assert "nests more than 32 levels deep" in line


@pytest.mark.skipif(os.name != "nt", reason="names differ by case elsewhere")
def test_a_record_names_its_file_as_windows_tells_names_apart(build, sample):
    (sample.parent / "x.raw.mascope.json").write_text(
        json.dumps({**RECORD, "source_filename": "X.RAW"}), encoding="utf-8"
    )
    file_uploader, uploads, _ = build()

    file_uploader.upload_sample_file(str(sample))

    assert "acquisition" in uploads.calls[0]


@pytest.mark.skipif(os.name == "nt", reason="names do not differ by case on Windows")
def test_a_record_naming_a_file_of_another_case_is_not_this_files(build, sample):
    (sample.parent / "x.raw.mascope.json").write_text(
        json.dumps({**RECORD, "source_filename": "X.RAW"}), encoding="utf-8"
    )
    file_uploader, uploads, _ = build()

    file_uploader.upload_sample_file(str(sample))

    assert "acquisition" not in uploads.calls[0]


# ---------------------------------------------------------------------------
# One hash for an upload
# ---------------------------------------------------------------------------


@pytest.fixture
def hashes(monkeypatch):
    """The files hashed, in order: each is a whole read of a file."""
    hashed = []
    real = provenance.file_sha256

    def counted(path):
        hashed.append(os.path.basename(path))
        return real(path)

    monkeypatch.setattr(provenance, "file_sha256", counted)
    return hashed


def test_a_file_is_hashed_once_however_many_attempts_its_upload_takes(
    build, sample, sidecar, hashes
):
    """An outage is ten attempts, and a file of a few GB read whole for each
    is the instrument computer's disk kept busy for nothing."""
    down = MascopeConnectionError("down")
    file_uploader, uploads, _ = build(True, down, down, down, None)

    file_uploader.process_file_upload(str(sample))

    assert len(uploads.calls) == 4
    assert hashes == ["x.raw"]
    assert {call["sha256"] for call in uploads.calls} == {
        hashlib.sha256(b"raw-bytes").hexdigest()
    }


@pytest.mark.parametrize(
    "rewritten, seconds_on",
    [(b"raw-bytes, and more", 0), (b"RAW-BYTES", 2)],
    ids=["another size at the same time", "the same size at another time"],
)
def test_a_file_that_changed_between_two_attempts_is_hashed_again(
    build, sample, hashes, monkeypatch, rewritten, seconds_on
):
    """Told by its size and by its time of last change, and by either alone:
    a file rewritten at the length it had is as much another file."""
    file_uploader, uploads, _ = build(True)
    was = os.stat(sample)

    def change_then_fail(**kwargs):
        uploads.calls.append(kwargs)
        if len(uploads.calls) == 1:
            sample.write_bytes(rewritten)
            os.utime(sample, ns=(was.st_atime_ns, was.st_mtime_ns + seconds_on * 10**9))
            raise MascopeConnectionError("down")

    monkeypatch.setattr(uploader, "api_post_file_tus", change_then_fail)
    file_uploader.process_file_upload(str(sample))

    first, second = uploads.calls
    assert hashes == ["x.raw", "x.raw"]
    assert first["sha256"] == hashlib.sha256(b"raw-bytes").hexdigest()
    assert second["sha256"] == hashlib.sha256(rewritten).hexdigest()


def test_what_was_kept_for_an_upload_is_dropped_when_it_is_over(build, sample, hashes):
    """The same file uploaded again is hashed again, and is told again of a
    record that cannot be used: nothing is kept past the upload it was for."""
    (sample.parent / "x.raw.mascope.json").write_bytes(b"[]")
    file_uploader, _, _ = build()

    file_uploader.process_file_upload(str(sample))
    file_uploader.process_file_upload(str(sample))

    assert hashes == ["x.raw", "x.raw"]
    assert len(file_uploader.logger.said("warning")) == 2


def test_it_is_dropped_for_a_file_that_was_set_aside_too(build, sample, hashes):
    gone = NotFoundError("gone", status_code=404, url=URL)
    file_uploader, _, _ = build(True, gone, gone)

    file_uploader.process_file_upload(str(sample))
    file_uploader.process_file_upload(str(sample))

    assert hashes == ["x.raw", "x.raw"]


def test_it_is_dropped_however_the_upload_ended(build, sample, monkeypatch):
    """An error nobody foresaw must not leave a file's hash kept for good."""
    file_uploader, _, _ = build()
    dropped = []
    monkeypatch.setattr(file_uploader.provenance, "forget", dropped.append)

    def fails(filepath, max_retries):
        raise RuntimeError("unforeseen")

    monkeypatch.setattr(file_uploader, "_upload_or_set_aside", fails)

    with pytest.raises(RuntimeError):
        file_uploader.process_file_upload(str(sample))

    assert dropped == [str(sample)]


# ---------------------------------------------------------------------------
# A refusal that is not the record's
# ---------------------------------------------------------------------------


def test_a_conflict_is_not_answered_by_sending_the_file_without_its_record(
    build, sample, sidecar
):
    """A server that will not have an upload at all can say so with a 409,
    and is not sent the file anyway, recordless."""
    conflict = MascopeAPIError("already here", status_code=409, url=URL)
    file_uploader, uploads, _ = build(True, conflict)

    with pytest.raises(MascopeAPIError):
        file_uploader.upload_sample_file(str(sample))

    (call,) = uploads.calls
    assert call["acquisition"] == sidecar.read_bytes()


# ---------------------------------------------------------------------------
# Setting a record aside
# ---------------------------------------------------------------------------


def test_setting_aside_a_file_with_no_record_says_nothing_of_records(build, sample):
    file_uploader, _, _ = build(True, NotFoundError("gone", status_code=404, url=URL))

    file_uploader.process_file_upload(str(sample))

    assert file_uploader.logger.said("warning") == []


def test_a_record_that_cannot_be_copied_does_not_keep_its_file_from_being_set_aside(
    build, sample, sidecar, monkeypatch
):
    real = uploader.shutil.copyfile

    def copy(source, target):
        if source.endswith(".mascope.json"):
            raise PermissionError("held open")
        return real(source, target)

    monkeypatch.setattr(uploader.shutil, "copyfile", copy)
    file_uploader, _, _ = build(True, NotFoundError("gone", status_code=404, url=URL))

    file_uploader.process_file_upload(str(sample))

    failed = sample.parent / FAILED_UPLOADS_DIR
    assert (failed / "x.raw").read_bytes() == b"raw-bytes"
    assert file_uploader.logger.said("warning") == [
        "Could not keep the acquisition record of x.raw with its copy: held open"
    ]
    # The file's own copy was kept, and the log does not say otherwise.
    (gave_up,) = [
        line for line in file_uploader.logger.said("error") if "Gave up" in line
    ]
    assert "A copy is in" in gave_up
