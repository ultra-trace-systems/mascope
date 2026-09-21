"""Unit tests for following what becomes of each uploaded file.

Hermetic: the server is a scripted stand-in for ``requests.get``, and time is
a counter the tests move on. What is pinned is what the operator reads: one
line per stage a file reaches, at a level that says whether to act, and
nothing more once the server has settled the file - or once it turns out the
server does not report what becomes of files at all.
"""

import pytest
import requests

from mascope_file_agent import main, status


class RecordingLogger:
    def __init__(self):
        self.lines: list[tuple[str, str]] = []

    def __getattr__(self, level):
        return lambda message: self.lines.append((level, message))


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Response:
    def __init__(self, rows=None, status_code=200):
        self.status_code = status_code
        self._rows = rows

    def json(self):
        return {"data": self._rows or []}


class Server:
    """Answers the file list with scripted rows, one answer per question."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.questions: list[dict] = []

    def __call__(self, url, params=None, headers=None, verify=None, timeout=None):
        self.questions.append({"url": url, "params": params, "headers": headers})
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


def row(processing_status, detail=None):
    return {
        "filename": "Orbi-1_x.raw",
        "processing_status": processing_status,
        "processing_detail": detail,
    }


@pytest.fixture
def follower(monkeypatch):
    logger, clock = RecordingLogger(), Clock()
    follower = status.StatusFollower(
        "https://mascope.example.com", lambda: "live-token", logger, clock=clock
    )
    follower.logger, follower.clock = logger, clock
    return follower


def serve(monkeypatch, *answers) -> Server:
    server = Server(*answers)
    monkeypatch.setattr(status.requests, "get", server)
    return server


def tick(follower, seconds):
    follower.clock.now += seconds
    follower.poll_due()


# ---------------------------------------------------------------------------
# The name the server stores an upload under
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("upload", "instrument", "stored"),
    [
        ("x.raw", "Orbi-1", "Orbi-1_x.raw"),
        ("Orbi-1_x.raw", "Orbi-1", "Orbi-1_x.raw"),
        ("Orbi-12_x.raw", "Orbi-1", "Orbi-1_Orbi-12_x.raw"),
        ("Orbi-1_x.raw", None, "Orbi-1_x.raw"),
    ],
)
def test_the_stored_name_follows_the_servers_rule(upload, instrument, stored):
    assert status.stored_name(upload, instrument) == stored


# ---------------------------------------------------------------------------
# Following a file
# ---------------------------------------------------------------------------


def test_each_stage_is_logged_once_until_the_file_settles(monkeypatch, follower):
    server = serve(
        monkeypatch,
        Response([]),  # not converted yet
        Response([row("converted")]),
        Response([row("converted")]),
        Response([row("bound", "Bound by file-name token to 'Bromide' (-).")]),
        Response([row("done", "Matched 1 sample.")]),
    )
    follower.follow("x.raw", "Orbi-1", "x.raw")

    for delay in status.POLL_DELAYS[:5]:
        tick(follower, delay)

    assert follower.logger.lines == [
        ("info", "x.raw: converted"),
        (
            "info",
            "x.raw: bound to its ionization modes. Bound by file-name token to 'Bromide' (-).",
        ),
        ("info", "x.raw: processed. Matched 1 sample."),
    ]
    assert follower.following() == []
    assert len(server.questions) == 5
    question = server.questions[0]
    assert question["url"] == "https://mascope.example.com/api/sample/files"
    assert question["params"]["filename"] == "Orbi-1_x.raw"
    assert question["headers"]["Authorization"] == "Bearer live-token"
    assert question["headers"]["X-Service-Name"] == "file-agent"


def test_nothing_is_asked_before_the_first_turn(monkeypatch, follower):
    server = serve(monkeypatch, Response([row("done")]))
    follower.follow("x.raw", "Orbi-1", "x.raw")

    tick(follower, status.POLL_DELAYS[0] - 1)

    assert server.questions == []


def test_the_interval_widens_while_the_file_is_on_its_way(monkeypatch, follower):
    server = serve(monkeypatch, Response([row("converted")]))
    follower.follow("x.raw", "Orbi-1", "x.raw")

    tick(follower, status.POLL_DELAYS[0])
    tick(follower, status.POLL_DELAYS[1] - 1)
    assert len(server.questions) == 1

    tick(follower, 1)
    assert len(server.questions) == 2


@pytest.mark.parametrize(
    ("outcome", "level", "label"),
    [
        ("needs_chemistry", "warning", "needs a chemistry"),
        (
            "calibration_failed",
            "warning",
            "m/z calibration failed, so it was not matched",
        ),
        ("failed", "error", "processing failed"),
    ],
)
def test_an_outcome_that_needs_someone_is_louder(
    monkeypatch, follower, outcome, level, label
):
    serve(monkeypatch, Response([row(outcome, "Why it happened.")]))
    follower.follow("x.raw", "Orbi-1", "x.raw")

    tick(follower, status.POLL_DELAYS[0])

    assert follower.logger.lines == [(level, f"x.raw: {label}. Why it happened.")]
    assert follower.following() == []


def test_a_question_that_fails_is_asked_again(monkeypatch, follower):
    serve(
        monkeypatch,
        requests.exceptions.ConnectionError("down"),
        Response(status_code=503),
        Response([row("done")]),
    )
    follower.follow("x.raw", "Orbi-1", "x.raw")

    for delay in status.POLL_DELAYS[:3]:
        tick(follower, delay)

    assert [line for line in follower.logger.lines if line[0] != "debug"] == [
        ("info", "x.raw: processed")
    ]


def test_a_file_the_server_never_records_is_given_up_on(monkeypatch, follower):
    serve(monkeypatch, Response([]))
    follower.follow("x.raw", "Orbi-1", "x.raw")

    tick(follower, status.FOLLOW_FOR + 1)

    ((level, line),) = follower.logger.lines
    assert level == "warning"
    assert line.startswith("x.raw: the server has no record of it 3 hours after")
    assert follower.following() == []


def test_a_server_without_a_status_is_not_asked_again(monkeypatch, follower):
    """An older server lists the file but records no processing status."""
    server = serve(monkeypatch, Response([{"filename": "Orbi-1_x.raw"}]))
    follower.follow("x.raw", "Orbi-1", "x.raw")
    follower.follow("y.raw", "Orbi-1", "y.raw")

    tick(follower, status.POLL_DELAYS[0])
    follower.follow("z.raw", "Orbi-1", "z.raw")
    tick(follower, status.FOLLOW_FOR)

    assert follower.enabled is False
    assert follower.following() == []
    assert len(server.questions) == 1
    assert follower.logger.lines == [
        (
            "info",
            "The server does not report what becomes of uploaded files, so the "
            "agent will not follow them.",
        )
    ]


# ---------------------------------------------------------------------------
# The agent hands each upload over
# ---------------------------------------------------------------------------


class StubConfig:
    mask = "*.raw"
    access_token = "tok"
    filename_prefix = "pre_"
    filename_suffix = ""


class StubRuntime:
    def __init__(self):
        self.logger = RecordingLogger()
        self.config = StubConfig()


def test_an_uploaded_file_is_followed_under_its_upload_name(monkeypatch, tmp_path):
    followed = []

    class Follower:
        def follow(self, *args):
            followed.append(args)

    sample = tmp_path / "x.raw"
    sample.write_bytes(b"data")
    monkeypatch.setattr(main, "runtime", StubRuntime())
    monkeypatch.setattr(main, "URL", "https://mascope.example.com")
    monkeypatch.setattr(main, "_instrument", "Orbi-1")
    monkeypatch.setattr(main, "api_post_file_tus", lambda **kwargs: None)
    monkeypatch.setattr(main, "_status_follower", Follower())

    main.upload_sample_file(str(sample))

    assert followed == [("pre_x.raw", "Orbi-1", "x.raw")]
