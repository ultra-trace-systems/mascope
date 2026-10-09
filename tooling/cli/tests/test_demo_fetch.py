"""
Tests for the demo bundle download (``mascope_cli.cmd.demo._fetch``).

Hermetic: ``urllib.request.urlopen`` is replaced by a scripted host, so no
request leaves the process, and the pauses between attempts are recorded
instead of slept. These run standalone:

    uv run pytest tooling/cli/tests/test_demo_fetch.py
"""

import hashlib
import http.client
import io
import json
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass

import pytest
from loguru import logger

from mascope_cli.cmd.demo import _fetch, bundles
from mascope_cli.runtime import runtime


URL = "https://bundles.example/demo-bundle.zip"

# Smaller than one read of the downloader, so a body arrives in a single piece
# and "cut after N bytes" is exact.
PAYLOAD = bytes(range(256)) * 40


class _Response:
    """What ``urlopen`` returns: a status, headers, and a body that may end early."""

    def __init__(
        self, status: int, headers: dict[str, str], body: bytes, then: Exception | None
    ):
        self.status = status
        self.headers = http.client.HTTPMessage()
        for name, value in headers.items():
            self.headers[name] = value
        self._body = io.BytesIO(body)
        self._then = then

    def read(self, amt: int) -> bytes:
        chunk = self._body.read(amt)
        if not chunk and self._then is not None:
            raise self._then
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@dataclass(frozen=True)
class _Answer:
    """
    How the host answers one request.

    :param deliver: Body bytes sent before the connection ends; ``None`` sends
                    the whole body. The headers announce the whole body either
                    way, as a real server's do before a transfer is cut.
    :param then: Raised by the read that follows the last delivered byte, in
                 place of a quiet close.
    :param payload: The file served to this request, where it is not the
                    host's own.
    :param content_range: Sent as ``Content-Range`` on a 206 in place of the
                          true one.
    """

    deliver: int | None = None
    then: Exception | None = None
    payload: bytes | None = None
    content_range: str | None = None


class _Host:
    """
    Stand-in for ``urllib.request.urlopen`` that serves one file.

    Requests are answered in order by ``script``: an exception is raised in
    place of a response, an :class:`_Answer` shapes one. A request the script
    has no entry for fails the test.

    :param ranges: Whether a ``Range`` request is honored with a 206. A host
                   that does not honor it sends the whole file with a 200.
    :param announce: Whether responses carry a ``Content-Length``.
    """

    def __init__(
        self,
        payload: bytes,
        script: list,
        *,
        ranges: bool = True,
        announce: bool = True,
    ):
        self.payload = payload
        self.script = list(script)
        self.ranges = ranges
        self.announce = announce
        self.ranges_asked: list[str | None] = []
        self.timeouts: list[float | None] = []

    def __call__(self, request, timeout=None):
        asked = request.get_header("Range")
        self.ranges_asked.append(asked)
        self.timeouts.append(timeout)

        answer = self.script.pop(0)
        if isinstance(answer, Exception):
            raise answer

        payload = self.payload if answer.payload is None else answer.payload
        status, start, headers = 200, 0, {}
        if asked and self.ranges:
            status = 206
            start = int(asked.removeprefix("bytes=").removesuffix("-"))
            headers["Content-Range"] = answer.content_range or (
                f"bytes {start}-{len(payload) - 1}/{len(payload)}"
            )
        body = payload[start:]
        if self.announce:
            headers["Content-Length"] = str(len(body))
        delivered = body if answer.deliver is None else body[: answer.deliver]
        return _Response(status, headers, delivered, answer.then)


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        URL, code, http.client.responses[code], http.client.HTTPMessage(), None
    )


@pytest.fixture(autouse=True)
def pauses(monkeypatch) -> list[float]:
    """Record the pauses between attempts instead of sleeping them."""
    slept: list[float] = []
    monkeypatch.setattr(_fetch.time, "sleep", slept.append)
    return slept


@pytest.fixture
def serve(monkeypatch):
    """Put a scripted :class:`_Host` in place of ``urllib.request.urlopen``."""

    def _serve(script: list, payload: bytes = PAYLOAD, **kwargs) -> _Host:
        host = _Host(payload, script, **kwargs)
        monkeypatch.setattr(urllib.request, "urlopen", host)
        return host

    return _serve


@pytest.fixture
def log_lines():
    """
    Collect the downloader's log messages.

    Configure first, then add the sink: configuring calls `logger.remove()`,
    which would take a sink added earlier with it.
    """
    runtime.reload_config()
    lines: list[str] = []
    handler = logger.add(
        lambda message: lines.append(message.record["message"]), level="DEBUG"
    )
    yield lines
    logger.remove(handler)


# --- _download: one archive, attempt by attempt ---------------------------


def test_transfer_cut_short_is_resumed_where_it_stopped(serve, pauses, tmp_path):
    """A connection closed early ends the stream without an error; the length tells."""
    host = serve([_Answer(deliver=3000), _Answer()])
    dest = tmp_path / "bundle.zip"

    _fetch._download(URL, dest)

    assert dest.read_bytes() == PAYLOAD
    assert host.ranges_asked == [None, "bytes=3000-"]
    assert pauses == [_fetch._RETRY_PAUSE_S]


def test_host_that_ignores_range_has_the_file_started_over(serve, tmp_path):
    """A 200 to a Range request is the whole file, and must not be appended."""
    host = serve([_Answer(deliver=3000), _Answer()], ranges=False)
    dest = tmp_path / "bundle.zip"

    _fetch._download(URL, dest)

    assert dest.read_bytes() == PAYLOAD
    assert host.ranges_asked == [None, "bytes=3000-"]


def test_part_from_the_wrong_place_is_not_appended(serve, tmp_path):
    """A 206 that is not the part asked for is dropped along with what was kept."""
    elsewhere = f"bytes 0-{len(PAYLOAD) - 1}/{len(PAYLOAD)}"
    host = serve([_Answer(deliver=3000), _Answer(content_range=elsewhere), _Answer()])
    dest = tmp_path / "bundle.zip"

    _fetch._download(URL, dest)

    assert dest.read_bytes() == PAYLOAD
    assert host.ranges_asked == [None, "bytes=3000-", None]


def test_refused_range_starts_the_file_over(serve, tmp_path):
    host = serve([_Answer(deliver=3000), _http_error(416), _Answer()])
    dest = tmp_path / "bundle.zip"

    _fetch._download(URL, dest)

    assert dest.read_bytes() == PAYLOAD
    assert host.ranges_asked == [None, "bytes=3000-", None]


def test_stalled_transfer_is_retried(serve, tmp_path):
    """A read that times out keeps what arrived before it."""
    stall = TimeoutError("The read operation timed out")
    host = serve([_Answer(deliver=3000, then=stall), _Answer()])
    dest = tmp_path / "bundle.zip"

    _fetch._download(URL, dest)

    assert dest.read_bytes() == PAYLOAD
    assert host.ranges_asked == [None, "bytes=3000-"]


@pytest.mark.parametrize(
    "failure",
    [
        _http_error(504),
        _http_error(429),
        urllib.error.URLError(TimeoutError("timed out")),
        TimeoutError("The read operation timed out"),
        http.client.RemoteDisconnected("Remote end closed connection"),
    ],
    ids=["504", "429", "connect-timeout", "header-timeout", "disconnected"],
)
def test_request_that_fails_for_now_is_made_again(failure, serve, pauses, tmp_path):
    host = serve([failure, _Answer()])
    dest = tmp_path / "bundle.zip"

    _fetch._download(URL, dest)

    assert dest.read_bytes() == PAYLOAD
    assert host.ranges_asked == [None, None]
    assert len(pauses) == 1


def test_status_that_will_not_change_is_not_retried(serve, pauses, tmp_path):
    host = serve([_http_error(404), _Answer()])

    with pytest.raises(urllib.error.HTTPError) as raised:
        _fetch._download(URL, tmp_path / "bundle.zip")

    assert raised.value.code == 404
    assert len(host.ranges_asked) == 1
    assert pauses == []


def test_gives_up_after_a_bounded_number_of_attempts(serve, pauses, tmp_path):
    """Every attempt adds 1,000 bytes and is cut; the error says how far it got."""
    attempts = _fetch._MAX_ATTEMPTS
    host = serve([_Answer(deliver=1000)] * (attempts + 1))

    with pytest.raises(RuntimeError) as raised:
        _fetch._download(URL, tmp_path / "bundle.zip")

    message = str(raised.value)
    assert f"failed after {attempts} attempts" in message
    assert f"{1000 * attempts:,} of {len(PAYLOAD):,} bytes" in message
    assert len(host.ranges_asked) == attempts
    assert pauses == [5, 10, 20, 40]


def test_file_started_over_is_counted_from_zero(serve, tmp_path):
    """What a host that ignores Range sends again replaces what was kept."""
    serve([_Answer(deliver=1000)] * _fetch._MAX_ATTEMPTS, ranges=False)

    with pytest.raises(RuntimeError) as raised:
        _fetch._download(URL, tmp_path / "bundle.zip")

    assert f"after 1,000 of {len(PAYLOAD):,} bytes" in str(raised.value)


def test_every_request_is_given_a_timeout(serve, tmp_path):
    host = serve([_Answer(deliver=3000), _Answer()])

    _fetch._download(URL, tmp_path / "bundle.zip")

    assert _fetch._TIMEOUT_S > 0
    assert host.timeouts == [_fetch._TIMEOUT_S, _fetch._TIMEOUT_S]


def test_file_left_by_an_earlier_run_is_not_resumed(serve, tmp_path):
    """Only bytes this download wrote are known to be the start of the file."""
    dest = tmp_path / "bundle.zip"
    dest.write_bytes(b"left over" * 100)
    host = serve([_http_error(503), _Answer()])

    _fetch._download(URL, dest)

    assert dest.read_bytes() == PAYLOAD
    assert host.ranges_asked == [None, None]


def test_body_of_unannounced_length_is_taken_as_it_comes(serve, pauses, tmp_path):
    """Without a Content-Length there is nothing to fall short of; the checksum judges."""
    serve([_Answer(deliver=3000)], announce=False)
    dest = tmp_path / "bundle.zip"

    _fetch._download(URL, dest)

    assert dest.read_bytes() == PAYLOAD[:3000]
    assert pauses == []


# --- fetch: download, checksum, extract, verify ---------------------------


def _bundle_archive() -> bytes:
    """Build a complete, if tiny, bundle: one raw file and the manifest listing it."""
    raw = b"not a spectrum " * 500
    manifest = {
        "raw": [{"name": "sample.raw", "sha256": hashlib.sha256(raw).hexdigest()}]
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(bundles.MANIFEST_NAME, json.dumps(manifest))
        archive.writestr("raw/sample.raw", raw)
    return buffer.getvalue()


def _damaged(archive: bytes) -> bytes:
    """The same archive with one byte changed: the right length, the wrong file."""
    middle = len(archive) // 2
    return archive[:middle] + bytes([archive[middle] ^ 0xFF]) + archive[middle + 1 :]


@pytest.fixture
def published(monkeypatch, tmp_path) -> tuple[bundles.Bundle, bytes]:
    """Register a bundle, cached under ``tmp_path``, and return it with its archive."""
    archive = _bundle_archive()
    bundle = bundles.Bundle(
        version="0.0.1", url=URL, archive_md5=hashlib.md5(archive).hexdigest()
    )
    monkeypatch.setitem(bundles.BUNDLES, bundle.version, bundle)
    monkeypatch.setattr(bundles, "cache_root", lambda: tmp_path / "demo")
    return bundle, archive


def test_fetch_survives_a_transfer_cut_short(serve, published):
    bundle, archive = published
    host = serve([_Answer(deliver=3000), _Answer()], payload=archive)

    dest = _fetch.fetch(bundle.version)

    assert dest == bundles.bundle_dir(bundle.version)
    assert (dest / "raw" / "sample.raw").is_file()
    assert bundles.verify_manifest(bundle.version) == []
    assert host.ranges_asked == [None, "bytes=3000-"]


def test_fetch_downloads_once_more_after_a_checksum_mismatch(
    serve, published, log_lines
):
    """A whole-length file with the wrong checksum is fetched again, and says so."""
    bundle, archive = published
    host = serve([_Answer(payload=_damaged(archive)), _Answer()], payload=archive)

    _fetch.fetch(bundle.version)

    assert bundles.verify_manifest(bundle.version) == []
    assert host.ranges_asked == [None, None]
    assert any("Archive checksum mismatch" in line for line in log_lines), log_lines


def test_fetch_replaces_a_resume_joined_wrongly(serve, published):
    """The checksum has the last word on a resumed file, and the redo keeps none of it."""
    bundle, archive = published
    host = serve(
        [_Answer(deliver=3000), _Answer(payload=_damaged(archive)), _Answer()],
        payload=archive,
    )

    _fetch.fetch(bundle.version)

    assert bundles.verify_manifest(bundle.version) == []
    assert host.ranges_asked == [None, "bytes=3000-", None]


def test_fetch_reports_a_checksum_that_never_matches(serve, published):
    """A second mismatch is not a transfer problem: reported as it always was."""
    bundle, archive = published
    damaged = _damaged(archive)
    host = serve([_Answer(payload=damaged)] * 3, payload=archive)

    with pytest.raises(RuntimeError) as raised:
        _fetch.fetch(bundle.version)

    assert str(raised.value) == (
        f"Archive checksum mismatch for '{bundle.version}': "
        f"expected {bundle.archive_md5[:12]}..., "
        f"got {hashlib.md5(damaged).hexdigest()[:12]}..."
    )
    assert host.ranges_asked == [None, None]
    assert not bundles.is_cached(bundle.version)
