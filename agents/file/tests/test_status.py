"""Unit tests for following what becomes of each uploaded file.

Hermetic: the server is a scripted stand-in for ``requests.get``, and time is
a counter the tests move on. What is pinned is what the operator reads: a
line for each stage the agent sees a file at, at a level that says whether to
act, and nothing more once the server has settled the file - or once it
turns out the server cannot say what becomes of files at all.
"""

import threading

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
    def __init__(self, body=None, status_code=200):
        self.status_code = status_code
        self._body = body

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def rows(*found):
    return Response({"data": list(found)})


def version(**capabilities):
    return Response({"data": {"version": "v2.0.0", "capabilities": capabilities}})


CAN_FOLLOW = version(files_listed_by_source_filename=True)


class Server:
    """Answers ``/api/version`` once, and the file list with scripted answers."""

    def __init__(self, version_answer, *answers):
        self.version_answers = (
            list(version_answer)
            if isinstance(version_answer, list)
            else [version_answer]
        )
        self.answers = list(answers)
        self.questions: list[dict] = []
        self.version_questions = 0

    def __call__(self, url, params=None, headers=None, verify=None, timeout=None):
        if url.endswith("/api/version"):
            self.version_questions += 1
            answer = (
                self.version_answers.pop(0)
                if len(self.version_answers) > 1
                else self.version_answers[0]
            )
        else:
            self.questions.append({"url": url, "params": params, "headers": headers})
            answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


def row(processing_status, detail=None):
    return {
        "filename": "Orbi-1_2026.09.21-10h00m00s_x",
        "source_filename": "x.raw",
        "processing_status": processing_status,
        "processing_detail": detail,
    }


@pytest.fixture
def follower():
    logger, clock = RecordingLogger(), Clock()
    follower = status.StatusFollower(
        "https://mascope.example.com", lambda: "live-token", logger, clock=clock
    )
    follower.logger, follower.clock = logger, clock
    return follower


def serve(monkeypatch, version_answer, *answers) -> Server:
    server = Server(version_answer, *answers)
    monkeypatch.setattr(status.requests, "get", server)
    return server


def tick(follower, seconds):
    follower.clock.now += seconds
    follower.poll_due()


def said(follower, *levels):
    return [line for line in follower.logger.lines if line[0] in levels]


# ---------------------------------------------------------------------------
# Following a file
# ---------------------------------------------------------------------------


def test_each_stage_seen_is_logged_once_until_the_file_settles(monkeypatch, follower):
    server = serve(
        monkeypatch,
        CAN_FOLLOW,
        rows(),  # not registered yet
        rows(row("converted")),
        rows(row("converted")),
        rows(row("bound", "Bound by file-name token to 'Bromide' (-).")),
        rows(row("done", "Matched 1 sample.")),
    )
    follower.follow("x.raw")

    for delay in status.POLL_DELAYS[:5]:
        tick(follower, delay)

    assert follower.logger.lines == [
        ("info", "x.raw: converted"),
        (
            "info",
            "x.raw: bound to its ionization modes. Bound by file-name token to "
            "'Bromide' (-).",
        ),
        ("info", "x.raw: processed. Matched 1 sample."),
    ]
    assert follower.following() == []
    assert len(server.questions) == 5


def test_a_file_is_asked_for_by_its_name_here_among_the_latest(monkeypatch, follower):
    """The server keeps the local name; the stored one carries a timestamp."""
    server = serve(monkeypatch, CAN_FOLLOW, rows())
    follower.follow("x.raw")

    tick(follower, status.POLL_DELAYS[0])

    question = server.questions[0]
    assert question["url"] == "https://mascope.example.com/api/sample/files"
    assert question["params"] == {
        "source_filename": "x.raw",
        # Since the upload, on the server's clock, with a margin for skew.
        "registered_within": status.POLL_DELAYS[0] + status.REGISTRATION_MARGIN,
        # Not another agent's file of the same name.
        "uploaded_by_me": "true",
        "sort": "sample_file_utc_created",
        "order": "desc",
        "page": 0,
        "limit": 1,
    }
    assert question["headers"]["Authorization"] == "Bearer live-token"
    assert question["headers"]["X-Service-Name"] == "file-agent"


def test_nothing_is_asked_before_the_first_turn(monkeypatch, follower):
    server = serve(monkeypatch, CAN_FOLLOW, rows(row("done")))
    follower.follow("x.raw")

    tick(follower, status.POLL_DELAYS[0] - 1)

    assert server.questions == []


def test_the_interval_widens_while_the_file_is_on_its_way(monkeypatch, follower):
    server = serve(monkeypatch, CAN_FOLLOW, rows(row("converted")))
    follower.follow("x.raw")

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
            "m/z calibration failed, so not all of it was matched",
        ),
        ("failed", "error", "processing failed"),
    ],
)
def test_an_outcome_that_needs_someone_is_louder(
    monkeypatch, follower, outcome, level, label
):
    serve(monkeypatch, CAN_FOLLOW, rows(row(outcome, "Why it happened.")))
    follower.follow("x.raw")

    tick(follower, status.POLL_DELAYS[0])

    assert follower.logger.lines == [(level, f"x.raw: {label}. Why it happened.")]
    assert follower.following() == []


# ---------------------------------------------------------------------------
# When the server cannot answer
# ---------------------------------------------------------------------------


def test_a_question_that_fails_is_asked_again(monkeypatch, follower):
    serve(
        monkeypatch,
        CAN_FOLLOW,
        requests.exceptions.ConnectionError("down"),
        Response(status_code=503),
        rows(row("done")),
    )
    follower.follow("x.raw")

    for delay in status.POLL_DELAYS[:3]:
        tick(follower, delay)

    assert said(follower, "info", "warning", "error") == [("info", "x.raw: processed")]


def test_a_file_the_server_never_records_is_given_up_on(monkeypatch, follower):
    serve(monkeypatch, CAN_FOLLOW, rows())
    follower.follow("x.raw")

    tick(follower, status.POLL_DELAYS[0])
    tick(follower, status.FOLLOW_FOR)

    ((level, line),) = said(follower, "info", "warning", "error")
    assert level == "warning"
    assert line.startswith("x.raw: the server has no record of it 3 hours after")
    assert follower.following() == []


def test_a_file_never_asked_about_is_not_taken_for_one_never_recorded(
    monkeypatch, follower
):
    """An outage through the whole window says so, not that conversion failed."""
    serve(monkeypatch, CAN_FOLLOW, requests.exceptions.ConnectionError("down"))
    follower.follow("x.raw")

    tick(follower, status.POLL_DELAYS[0])
    tick(follower, status.FOLLOW_FOR)

    ((level, line),) = said(follower, "info", "warning", "error")
    assert level == "warning"
    assert "could not be asked about it" in line
    assert "Converting it may have failed" not in line


def test_a_server_lost_after_it_answered_is_not_taken_for_one_with_no_record(
    monkeypatch, follower
):
    """The first answer comes before the file is registered; then an outage."""
    serve(
        monkeypatch,
        CAN_FOLLOW,
        rows(),
        requests.exceptions.ConnectionError("down"),
    )
    follower.follow("x.raw")

    tick(follower, status.POLL_DELAYS[0])
    tick(follower, status.POLL_DELAYS[1])
    tick(follower, status.FOLLOW_FOR)

    ((level, line),) = said(follower, "info", "warning", "error")
    assert level == "warning"
    assert line == (
        "x.raw: the server could not be asked about it for the last 3 hours; "
        "before that, the server had no record of it. Look for it in Raw files "
        "on the server."
    )


def test_a_server_lost_after_a_stage_says_the_stage_it_last_saw(monkeypatch, follower):
    serve(
        monkeypatch,
        CAN_FOLLOW,
        rows(row("bound")),
        requests.exceptions.ConnectionError("down"),
    )
    follower.follow("x.raw")

    tick(follower, status.POLL_DELAYS[0])
    tick(follower, status.FOLLOW_FOR - status.POLL_DELAYS[0])

    level, line = said(follower, "info", "warning", "error")[-1]
    assert level == "warning"
    assert "could not be asked about it for the last 3 hours" in line
    assert "before that, it was bound to its ionization modes" in line


@pytest.mark.parametrize(
    "answer",
    [
        Response({"data": {"sample_file_id": "sf-1"}}),
        Response(ValueError("not JSON")),
        Response(["not", "an", "object"]),
    ],
)
def test_an_answer_of_the_wrong_shape_is_not_taken_for_one(
    monkeypatch, follower, answer
):
    server = serve(monkeypatch, CAN_FOLLOW, answer, rows(row("done")))
    follower.follow("x.raw")

    tick(follower, status.POLL_DELAYS[0])
    tick(follower, status.POLL_DELAYS[1])

    assert len(server.questions) == 2
    assert said(follower, "info") == [("info", "x.raw: processed")]
    # Read as no answer, not tripped over.
    assert said(follower, "exception") == []


def test_an_error_with_one_file_does_not_hold_the_others(monkeypatch, follower):
    serve(monkeypatch, CAN_FOLLOW, rows(row("done")))
    follower.follow("bad.raw")
    follower.follow("good.raw")
    real_report = follower._report

    def report(followed, found):
        if followed.name == "bad.raw":
            raise RuntimeError("unexpected")
        real_report(followed, found)

    monkeypatch.setattr(follower, "_report", report)

    tick(follower, status.POLL_DELAYS[0])

    assert ("info", "good.raw: processed") in follower.logger.lines
    assert follower.following() == ["bad.raw"]
    tick(follower, status.POLL_DELAYS[0])
    assert follower.following() == ["bad.raw"]  # waits its next, wider turn


def test_a_pass_stops_at_the_first_question_the_server_does_not_answer(
    monkeypatch, follower
):
    """A slow or absent server is not asked about every due file in a row."""
    server = serve(monkeypatch, CAN_FOLLOW, requests.exceptions.Timeout("slow"))
    for name in ("a.raw", "b.raw", "c.raw"):
        follower.follow(name)

    tick(follower, status.POLL_DELAYS[0])

    assert len(server.questions) == 1


def test_the_next_turn_counts_from_the_answer_not_the_start_of_the_pass(
    monkeypatch, follower
):
    class SlowServer(Server):
        def __call__(self, url, **kwargs):
            answer = super().__call__(url, **kwargs)
            if not url.endswith("/api/version"):
                follower.clock.now += 30  # each answer takes 30 s
            return answer

    server = SlowServer(CAN_FOLLOW, rows())
    monkeypatch.setattr(status.requests, "get", server)
    follower.follow("a.raw")
    follower.follow("b.raw")

    tick(follower, status.POLL_DELAYS[0])
    assert len(server.questions) == 2
    # Each was scheduled from its own answer, so neither is due again as the
    # pass ends - counted from its start, both would be.
    follower.poll_due()
    assert len(server.questions) == 2


# ---------------------------------------------------------------------------
# What the server can say
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "answer",
    [
        version(),  # announces other things
        Response(status_code=401),  # predates reading capabilities with a token
    ],
)
def test_a_server_that_cannot_say_is_not_asked(monkeypatch, follower, answer):
    server = serve(monkeypatch, answer, rows(row("done")))
    follower.follow("x.raw")
    follower.follow("y.raw")

    tick(follower, status.POLL_DELAYS[0])
    follower.follow("z.raw")
    tick(follower, status.FOLLOW_FOR)

    assert follower.enabled is False
    assert follower.following() == []
    assert server.questions == []
    assert server.version_questions == 1
    assert follower.logger.lines == [
        (
            "info",
            "The server does not report what becomes of uploaded files, so the "
            "agent will not follow them.",
        )
    ]


def test_a_server_that_cannot_be_reached_is_asked_what_it_can_do_again(
    monkeypatch, follower
):
    server = serve(
        monkeypatch,
        [
            requests.exceptions.ConnectionError("down"),
            Response(status_code=502),
            CAN_FOLLOW,
        ],
        rows(row("done")),
    )
    follower.follow("x.raw")

    for _ in range(3):
        tick(follower, status.POLL_DELAYS[0])

    assert server.version_questions == 3
    assert follower.enabled is True
    assert said(follower, "info") == [("info", "x.raw: processed")]


# ---------------------------------------------------------------------------
# One name, uploaded again
# ---------------------------------------------------------------------------


def test_a_second_upload_of_a_name_is_followed_in_place_of_the_first(
    monkeypatch, follower
):
    serve(monkeypatch, CAN_FOLLOW, rows(row("converted")))
    follower.follow("x.raw")

    follower.follow("x.raw")

    assert follower.following() == ["x.raw"]
    assert follower.logger.lines == [
        (
            "info",
            "x.raw: uploaded again before the server settled the earlier upload; "
            "following the new one.",
        )
    ]


def test_settling_the_earlier_upload_leaves_the_later_one_followed(follower):
    follower.follow("x.raw")
    earlier = follower._followed["x.raw"]
    follower.follow("x.raw")

    follower._forget(earlier)

    assert follower.following() == ["x.raw"]


# ---------------------------------------------------------------------------
# Stopping
# ---------------------------------------------------------------------------


def test_stopping_says_how_many_files_are_left_unfollowed(monkeypatch, follower):
    serve(monkeypatch, CAN_FOLLOW, rows())
    follower.follow("x.raw")
    follower.follow("y.raw")
    stop = threading.Event()
    stop.set()

    follower.run(stop, tick=0)

    assert follower.logger.lines == [
        (
            "info",
            "No longer following 2 uploaded files: the agent is stopping. What "
            "became of them shows in Raw files on the server.",
        )
    ]


def test_an_upload_that_finishes_after_the_follower_stopped_says_so(
    monkeypatch, follower
):
    """Stopping waits for the uploads in flight; the follower is gone by then."""
    serve(monkeypatch, CAN_FOLLOW, rows())
    stop = threading.Event()
    stop.set()
    follower.run(stop, tick=0)

    follower.follow("late.raw")

    assert follower.following() == []
    assert follower.logger.lines == [
        (
            "info",
            "late.raw: not followed, as the agent is stopping. What became of it "
            "shows in Raw files on the server.",
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


def test_an_uploaded_file_is_followed_by_its_name_here(monkeypatch, tmp_path):
    """Not by the prefixed name it was uploaded under."""
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

    assert followed == [("x.raw",)]
