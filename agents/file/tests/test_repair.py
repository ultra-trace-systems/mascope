"""Unit tests for the in-place re-pair offer.

When the server refuses this machine's credential the agent asks, in the
console the operator already has open, whether to pair again - rather than
telling them to relaunch it with a flag. Recovery is the moment a
non-technical user is involved, so the prompt has to appear exactly once,
survive being declined, and never block a machine started without a console.
"""

import pytest

from mascope_file_agent import main
from mascope_sdk.exceptions import AuthenticationError


class StubLogger:
    def error(self, message):
        pass

    def warning(self, message):
        pass

    def info(self, message):
        pass

    def debug(self, message):
        pass

    def exception(self, message):
        pass


class StubConfig:
    mask = "*.raw"
    access_token = "dead-token"
    filename_prefix = ""
    filename_suffix = ""
    verify_tls = True
    source = ""


class StubRuntime:
    def __init__(self):
        self.logger = StubLogger()
        self.config = StubConfig()


@pytest.fixture
def agent(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "runtime", StubRuntime())
    monkeypatch.setattr(main, "URL", "https://mascope.example.com")
    monkeypatch.setattr(main, "HOST", "mascope.example.com")
    monkeypatch.setattr(main, "_repair_declined", False)
    monkeypatch.setattr(main, "_persist_token", lambda token: None)
    main._set_access_token("dead-token")
    monkeypatch.setattr(main.sys.stdin, "isatty", lambda: True, raising=False)
    # Giving up on a file copies it beside the watched folder; point that at
    # the tmp dir so the suite cannot litter the repo.
    monkeypatch.setattr(StubConfig, "source", str(tmp_path))
    sample = tmp_path / "x.raw"
    sample.write_bytes(b"data")
    return str(sample)


def test_offers_to_pair_and_retries_the_file(monkeypatch, agent):
    """Accepting the offer swaps the credential and the upload is retried."""
    monkeypatch.setattr("builtins.input", lambda _: "y")
    monkeypatch.setattr(main, "run_pairing", lambda host, verify: "fresh-token")

    attempts = []

    def flaky_upload(path):
        attempts.append(main.current_access_token())
        if len(attempts) == 1:
            raise AuthenticationError("Credential refused", status_code=401)

    monkeypatch.setattr(main, "upload_sample_file", flaky_upload)

    main.process_file_upload(agent, max_retries=3)

    assert attempts == ["dead-token", "fresh-token"]


def test_declining_stops_asking(monkeypatch, agent):
    """A "no" is remembered - the console must not nag once per file."""
    asked = []

    def answer(prompt):
        asked.append(prompt)
        return "n"

    monkeypatch.setattr("builtins.input", answer)
    monkeypatch.setattr(
        main, "run_pairing", lambda host, verify: pytest.fail("must not pair")
    )
    monkeypatch.setattr(
        main,
        "upload_sample_file",
        lambda path: (_ for _ in ()).throw(
            AuthenticationError("Credential refused", status_code=401)
        ),
    )

    main.process_file_upload(agent, max_retries=2)
    main.process_file_upload(agent, max_retries=2)

    assert len(asked) == 1


def test_no_console_falls_back_to_the_log(monkeypatch, agent):
    """A machine started without a console must not block on input()."""
    monkeypatch.setattr(main.sys.stdin, "isatty", lambda: False, raising=False)
    monkeypatch.setattr(
        "builtins.input", lambda _: pytest.fail("must not prompt without a console")
    )
    monkeypatch.setattr(
        main,
        "upload_sample_file",
        lambda path: (_ for _ in ()).throw(
            AuthenticationError("Credential refused", status_code=401)
        ),
    )

    main.process_file_upload(agent, max_retries=2)


def test_a_concurrent_repair_is_reused(monkeypatch, agent):
    """If another worker already paired, retry on its token instead of asking."""
    monkeypatch.setattr(
        "builtins.input", lambda _: pytest.fail("another worker already fixed it")
    )
    main._set_access_token("someone-elses-fresh-token")

    assert main._offer_repair("dead-token", "refused") is True
