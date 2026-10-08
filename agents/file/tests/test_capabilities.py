"""Unit tests for asking the server what it can do.

Hermetic: the server is a scripted stand-in for ``requests.get``, and time is
a counter the tests move on. What is pinned is when the server is asked: not
for every file, again while it has not answered, again once the answer is old,
and again once a refused credential has been replaced.
"""

import pytest
import requests

from mascope_file_agent import Agent, capabilities, credentials, uploader
from mascope_file_agent.capabilities import ASK_AGAIN_AFTER, ServerCapabilities
from mascope_file_agent.wizard import CREDENTIAL_OK
from mascope_sdk import _http, acquisition


URL = "https://mascope.example.com"


class Logger:
    def __getattr__(self, level):
        return lambda message: None


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


def announcing(**announced):
    return Response({"data": {"version": "v2.0.0", "capabilities": announced}})


class Server:
    """Answers ``/api/version`` with each scripted answer in turn; the last
    one from then on."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.questions: list[dict] = []

    def __call__(self, url, headers=None, verify=None, timeout=None):
        self.questions.append({"url": url, "headers": headers, "verify": verify})
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def serve(monkeypatch):
    def serve(*answers):
        server = Server(*answers)
        monkeypatch.setattr(capabilities.requests, "get", server)
        return server

    return serve


def asking(clock, token=lambda: "tok", verify=True):
    return ServerCapabilities(URL, token, Logger(), verify=verify, clock=clock)


def test_the_server_is_asked_with_the_agents_credential(serve, clock):
    server = serve(announcing(files_listed_by_source_filename=True))

    announced = asking(clock, verify=False).ask()

    assert announced == {"files_listed_by_source_filename": True}
    (question,) = server.questions
    assert question["url"] == f"{URL}/api/version"
    assert question["headers"]["Authorization"] == "Bearer tok"
    assert question["headers"]["X-Service-Name"] == "file-agent"
    assert question["verify"] is False


def test_an_answer_serves_every_question_for_a_while(serve, clock):
    server = serve(announcing(a=True))
    asked = asking(clock)

    assert asked.has("a") is True
    clock.now += ASK_AGAIN_AFTER - 1
    assert asked.has("a") is True
    assert asked.has("b") is False

    assert len(server.questions) == 1


def test_a_server_is_asked_again_once_its_answer_is_old(serve, clock):
    """An agent runs for months, and its server is updated under it."""
    server = serve(announcing(), announcing(a=True))
    asked = asking(clock)

    assert asked.has("a") is False
    clock.now += ASK_AGAIN_AFTER
    assert asked.has("a") is True

    assert len(server.questions) == 2


@pytest.mark.parametrize(
    "no_answer",
    [requests.exceptions.ConnectionError("down"), Response(status_code=502)],
)
def test_a_server_that_did_not_answer_is_asked_the_next_time(serve, clock, no_answer):
    server = serve(no_answer, announcing(a=True))
    asked = asking(clock)

    assert asked.has("a") is None
    assert asked.has("a") is True

    assert len(server.questions) == 2


def test_an_old_answer_does_not_stand_in_for_a_server_that_is_not_answering(
    serve, clock
):
    serve(announcing(a=True), requests.exceptions.Timeout("slow"))
    asked = asking(clock)
    assert asked.has("a") is True

    clock.now += ASK_AGAIN_AFTER

    assert asked.has("a") is None


@pytest.mark.parametrize(
    "answer",
    [
        announcing(),
        Response({"data": {"version": "v1.9.0"}}),
        Response({"data": {"capabilities": ["a"]}}),
        Response(ValueError("not JSON")),
        Response(["not", "an", "object"]),
    ],
)
def test_a_server_that_announces_nothing_can_do_nothing_new(serve, clock, answer):
    serve(answer)

    assert asking(clock).has("a") is False


def test_only_true_announces_a_capability(serve, clock):
    serve(announcing(a="yes", b=1, c=True))
    asked = asking(clock)

    assert (asked.has("a"), asked.has("b"), asked.has("c")) == (False, False, True)


def test_a_refusal_stands_until_the_credential_is_replaced(serve, clock):
    """A server that refuses the question predates it - or refused this
    machine. Paired again, the agent must not go on taking the refusal for
    what the server can do."""
    server = serve(Response(status_code=401), announcing(a=True))
    token = ["revoked"]
    asked = asking(clock, token=lambda: token[0])

    assert asked.has("a") is False
    assert asked.has("a") is False
    assert len(server.questions) == 1

    token[0] = "paired-again"

    assert asked.has("a") is True
    assert len(server.questions) == 2


def test_an_answer_is_not_asked_for_again_when_the_token_is_renewed(serve, clock):
    server = serve(announcing(a=True))
    token = ["first"]
    asked = asking(clock, token=lambda: token[0])
    assert asked.has("a") is True

    token[0] = "renewed"

    assert asked.has("a") is True
    assert len(server.questions) == 1


def test_one_asking_serves_the_whole_agent(serve, monkeypatch, make_settings, tmp_path):
    """The follower and the uploads want to know of the same server."""
    server = serve(
        announcing(
            **{acquisition.CAPABILITY: True, "files_listed_by_source_filename": True}
        )
    )
    monkeypatch.setattr(
        credentials, "check_credential", lambda host, token, verify: (CREDENTIAL_OK, "")
    )
    uploads = []
    monkeypatch.setattr(
        uploader, "api_post_file_tus", lambda **kwargs: uploads.append(kwargs)
    )
    sample = tmp_path / "x.raw"
    sample.write_bytes(b"data")
    agent = Agent(make_settings(), logger=Logger())

    agent.uploader.upload_sample_file(str(sample))
    assert agent.status_follower._ask_server() is True

    # Both were answered: the upload went with what this server keeps, and
    # the follower knows it can ask about it.
    assert "sha256" in uploads[0]
    assert agent.status_follower.enabled is True
    assert len(server.questions) == 1


@pytest.mark.parametrize("status_code", [408, 425, 429])
def test_a_server_that_asks_for_another_try_has_not_answered(serve, clock, status_code):
    """A rate limit is not a server too old to keep records. Taken for one, it
    would stand for an hour, and the records of that hour would never be
    sent."""
    server = serve(Response(status_code=status_code), announcing(a=True))
    asked = asking(clock)

    assert asked.has("a") is None
    assert asked.refused is False
    assert asked.has("a") is True

    assert len(server.questions) == 2


def test_the_statuses_that_are_no_answer_are_the_ones_an_upload_is_sent_again_for():
    assert capabilities.ASK_AGAIN_FOR == _http._RETRYABLE_CLIENT_STATUS_CODES


def test_a_refusal_is_known_as_one(serve, clock):
    """To whoever has to say why: a refused credential is not an old server."""
    serve(Response(status_code=401), announcing())
    token = ["revoked"]
    asked = asking(clock, token=lambda: token[0])
    assert asked.refused is False  # nothing is known yet

    assert asked.has("a") is False
    assert asked.refused is True

    token[0] = "paired-again"
    assert asked.has("a") is False
    assert asked.refused is False  # it answered, and announces nothing


def test_an_agent_asks_as_its_settings_say_to_verify(serve, make_settings):
    """A deployment with a certificate of its own: asked with verification
    on, the server would never be heard to answer, and every file with a
    record would wait for it."""
    server = serve(announcing())
    agent = Agent(make_settings(verify_tls=False), logger=Logger())

    agent.server.ask()

    assert server.questions[0]["verify"] is False
