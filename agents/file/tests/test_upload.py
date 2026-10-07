"""Unit tests for the agent's TUS upload path.

Hermetic: the SDK upload function is monkeypatched, and the uploader is
built on its own, with no agent around it.

There is no legacy fallback. Every supported server accepts agent TUS
uploads, so a refusal at upload creation is a real failure - a rejected
credential above all - and has to reach the caller with its own type. It
used to be read as "this server predates token-accessible TUS", which sent
the agent to the capped legacy endpoint for the rest of the process and
blamed the server version for what was a revoked or expired credential.
"""

import pytest

from mascope_file_agent import agent, credentials, main, uploader, watcher
from mascope_file_agent.credentials import Credentials
from mascope_file_agent.uploader import FileUploader
from mascope_sdk.exceptions import AuthenticationError, NotFoundError


URL = "https://mascope.example.com"


class StubLogger:
    def error(self, message):
        pass

    def warning(self, message):
        pass

    def info(self, message):
        pass

    def debug(self, message):
        pass


@pytest.fixture
def make_uploader(make_settings):
    """An uploader as the agent builds it, with nothing set but what is given."""

    def make(access_token="tok", **arguments):
        settings = make_settings(access_token=access_token)
        credentials = Credentials(URL, settings.host, access_token, StubLogger())
        return FileUploader(
            settings, URL, credentials, logger=StubLogger(), **arguments
        )

    return make


@pytest.fixture
def sample_file(tmp_path):
    sample = tmp_path / "x.raw"
    sample.write_bytes(b"data")
    return str(sample)


def test_uploads_via_tus(monkeypatch, make_uploader, sample_file):
    tus_calls = []
    monkeypatch.setattr(
        uploader, "api_post_file_tus", lambda **kwargs: tus_calls.append(kwargs)
    )

    make_uploader().upload_sample_file(sample_file)

    assert len(tus_calls) == 1
    assert tus_calls[0]["filepath"] == sample_file
    # Nothing configured: no instrument is reported, and the server keeps
    # reading it from the file name.
    assert tus_calls[0]["instrument"] is None


def test_reports_the_configured_instrument(monkeypatch, make_uploader, sample_file):
    tus_calls = []
    monkeypatch.setattr(
        uploader, "api_post_file_tus", lambda **kwargs: tus_calls.append(kwargs)
    )

    make_uploader(instrument="Orbi-Lab2").upload_sample_file(sample_file)

    assert tus_calls[0]["instrument"] == "Orbi-Lab2"


def test_uploads_with_the_live_token_not_the_config_snapshot(
    monkeypatch, make_uploader, sample_file
):
    """Renewal rotates the token under the uploader; the send must follow it.

    The server keeps only the newest two tokens per device, so the one the
    agent booted with is reaped at the second renewal - about 15 days in for
    a 30-day credential. An uploader reading the settings' snapshot would
    send that dead token from then on, and a 401 is terminal in
    process_file_upload: no retry, straight to failed_uploads. Renewal keeps
    succeeding on the live token meanwhile, so nothing else complains and a
    headless agent has only a log line to show for it.
    """
    tus_calls = []
    monkeypatch.setattr(
        uploader, "api_post_file_tus", lambda **kwargs: tus_calls.append(kwargs)
    )
    # The settings still hold what the agent booted with; renewal has since
    # rotated the live token.
    file_uploader = make_uploader(
        access_token="stale-boot-token", timezone="Europe/Helsinki"
    )
    file_uploader.credentials.set_access_token("live-token")

    file_uploader.upload_sample_file(sample_file)

    assert tus_calls[0]["access_token"] == "live-token"
    # The zone resolved at start travels with every upload; without it the
    # converter resolves the acquisition time in the server's zone instead.
    assert tus_calls[0]["timezone"] == "Europe/Helsinki"


@pytest.mark.parametrize(
    "error",
    [
        AuthenticationError("Credential rejected", status_code=401),
        NotFoundError("No such route", status_code=404),
    ],
)
def test_upload_errors_keep_their_type(monkeypatch, make_uploader, sample_file, error):
    """A refused upload must surface as itself, not as a server-version problem.

    401 is the one that matters: a revoked device, a device token that
    expired while the agent was offline, and a deployment that accepts only
    paired credentials all answer this way, and the operator needs to be
    told that rather than sent to look at the server's version.
    """

    def failing_tus(**kwargs):
        raise error

    monkeypatch.setattr(uploader, "api_post_file_tus", failing_tus)

    with pytest.raises(type(error)):
        make_uploader().upload_sample_file(sample_file)


def test_no_legacy_upload_path_remains():
    """The fallback cannot creep back: nothing may call the legacy endpoint.

    A latch that survives one bad response degrades every later upload in
    the process - capped size, no resumability - so the absence of the path
    is pinned here rather than left to review.
    """
    assert not hasattr(main, "api_post_file")
    assert not hasattr(main, "_legacy_upload")
    assert not hasattr(main, "FILE_UPLOAD_SIZE_LIMIT")
    # main held the whole agent when this was written; the same holds for
    # every module its code has since moved to.
    for module in (agent, credentials, uploader, watcher):
        assert not hasattr(module, "api_post_file")
        assert not hasattr(module, "_legacy_upload")
        assert not hasattr(module, "FILE_UPLOAD_SIZE_LIMIT")


def test_upload_has_no_size_cap(monkeypatch, make_uploader, sample_file):
    """TUS uploads are chunked; the agent imposes no size limit of its own."""
    tus_calls = []
    monkeypatch.setattr(
        uploader, "api_post_file_tus", lambda **kwargs: tus_calls.append(kwargs)
    )
    monkeypatch.setattr(uploader.os.path, "getsize", lambda _: 50 * 1024**3)

    make_uploader().upload_sample_file(sample_file)

    assert len(tus_calls) == 1


def test_rejects_extension_not_matching_mask(make_uploader, tmp_path):
    wrong = tmp_path / "x.txt"
    wrong.write_bytes(b"data")

    with pytest.raises(ValueError, match="not an allowed file extension"):
        make_uploader().upload_sample_file(str(wrong))
