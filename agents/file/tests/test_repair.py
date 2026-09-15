"""Unit tests for the in-place re-pair offer.

When the server refuses this machine's credential the agent asks, in the
console the operator already has open, whether to pair again - rather than
telling them to relaunch it with a flag. Recovery is the moment a
non-technical user is involved, so the prompt has to appear exactly once,
survive being declined, and never block a machine started without a console.

The same offer runs once at startup, so a machine revoked while it was off
is fixed before an acquisition needs it - but only on an answered refusal,
never when the server simply could not be reached.
"""

import pytest

from mascope_file_agent import main
from mascope_file_agent.wizard import (
    CREDENTIAL_OK,
    CREDENTIAL_REJECTED,
    CREDENTIAL_UNREACHABLE,
)
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
    monkeypatch.setattr(
        main, "run_pairing", lambda host, verify, instrument=None: "fresh-token"
    )

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
        main,
        "run_pairing",
        lambda host, verify, instrument=None: pytest.fail("must not pair"),
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


def test_startup_offers_to_pair_when_the_credential_is_refused(monkeypatch, agent):
    """A machine revoked while it was off is fixed before any file needs it."""
    monkeypatch.setattr(
        main,
        "check_credential",
        lambda host, token, verify: (CREDENTIAL_REJECTED, "refused"),
    )
    monkeypatch.setattr("builtins.input", lambda _: "y")
    monkeypatch.setattr(
        main, "run_pairing", lambda host, verify, instrument=None: "fresh-token"
    )

    main._check_credential_at_start()

    assert main.current_access_token() == "fresh-token"


def test_startup_does_not_prompt_when_the_server_is_unreachable(monkeypatch, agent):
    """No network yet is not a refusal - pairing cannot fix it, so do not ask.

    This is the case that makes the check safe on an instrument PC that
    starts the agent at sign-in, before the network is up.
    """
    monkeypatch.setattr(
        main,
        "check_credential",
        lambda host, token, verify: (CREDENTIAL_UNREACHABLE, "no route to host"),
    )
    monkeypatch.setattr(
        "builtins.input", lambda _: pytest.fail("must not prompt when unreachable")
    )
    monkeypatch.setattr(
        main,
        "run_pairing",
        lambda host, verify, instrument=None: pytest.fail("must not pair"),
    )

    main._check_credential_at_start()

    assert main.current_access_token() == "dead-token"


def test_startup_is_silent_when_the_credential_is_good(monkeypatch, agent):
    monkeypatch.setattr(
        main, "check_credential", lambda host, token, verify: (CREDENTIAL_OK, "")
    )
    monkeypatch.setattr(
        "builtins.input", lambda _: pytest.fail("a good credential must not prompt")
    )

    main._check_credential_at_start()

    assert main.current_access_token() == "dead-token"
