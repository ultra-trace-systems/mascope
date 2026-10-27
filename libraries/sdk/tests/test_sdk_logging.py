"""
Hermetic tests for the loguru handler a ``MascopeClient`` adds.

Each client replaces the handler the previous one added, by its ID. An
application, or a test, that removes every loguru handler in between leaves
that ID stale; the next client must still be made, and still log once.

The first client drops loguru's default stderr handler, and only that one:
handlers the application added before it made a client keep working.
"""

import subprocess
import sys

import pytest
from loguru import logger

from mascope_sdk import MascopeClient
from mascope_sdk import client as sdk_client


@pytest.fixture(autouse=True)
def _sdk_handler():
    """Start without the SDK's handler and drop the one the test leaves."""
    yield
    if sdk_client._log_handler_id is not None:
        try:
            logger.remove(sdk_client._log_handler_id)
        except ValueError:
            pass
        sdk_client._log_handler_id = None


def _client(monkeypatch, tmp_path) -> MascopeClient:
    monkeypatch.setattr(
        MascopeClient, "_resolve_workspace", lambda self, workspace: ("ws-1", "WS")
    )
    monkeypatch.delenv("MASCOPE_SDK_LOG_LEVEL", raising=False)
    empty_env = tmp_path / ".env"
    empty_env.write_text("")
    return MascopeClient(
        url="http://testserver/", access_token="token", env_file=empty_env
    )


def test_a_client_is_made_after_every_loguru_handler_was_removed(
    monkeypatch, tmp_path, capsys
):
    _client(monkeypatch, tmp_path)
    logger.remove()

    _client(monkeypatch, tmp_path)

    # The new client's handler is in place, and only one of them
    capsys.readouterr()
    logger.patch(lambda record: record.update(name="mascope_sdk.test")).info(
        "after the reset"
    )
    assert capsys.readouterr().err.count("after the reset") == 1


def test_the_next_client_replaces_the_handler_of_the_last(
    monkeypatch, tmp_path, capsys
):
    _client(monkeypatch, tmp_path)
    _client(monkeypatch, tmp_path)

    capsys.readouterr()
    logger.patch(lambda record: record.update(name="mascope_sdk.test")).info("one line")
    assert capsys.readouterr().err.count("one line") == 1


def test_the_first_client_keeps_the_applications_own_handlers(monkeypatch, tmp_path):
    received: list[str] = []
    sink = logger.add(lambda message: received.append(message.record["message"]))
    try:
        _client(monkeypatch, tmp_path)

        logger.patch(lambda record: record.update(name="my_app")).info("app line")
    finally:
        logger.remove(sink)

    assert received == ["app line"]


def test_the_first_client_drops_loguru_s_default_handler(monkeypatch, tmp_path, capsys):
    # Loguru never hands ID 0 out again once it is removed, so a handler of
    # the test's own stands in for the default one here
    default_lines: list[str] = []
    default = logger.add(lambda message: default_lines.append(message))
    monkeypatch.setattr(sdk_client, "_DEFAULT_HANDLER_ID", default)
    try:
        _client(monkeypatch, tmp_path)

        capsys.readouterr()
        logger.patch(lambda record: record.update(name="mascope_sdk.test")).info(
            "sdk line"
        )
    finally:
        try:
            logger.remove(default)
        except ValueError:
            pass

    # Written once, by the SDK's handler, and not again by the default one
    assert capsys.readouterr().err.count("sdk line") == 1
    assert default_lines == []


def test_the_default_handler_id_is_the_one_loguru_installs():
    # A fresh interpreter, since this one's tests have removed handlers
    out = subprocess.run(
        [
            sys.executable,
            "-c",
            "from loguru import logger; print(list(logger._core.handlers))",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert out.strip() == f"[{sdk_client._DEFAULT_HANDLER_ID}]"


def test_the_first_client_is_made_when_the_default_handler_is_gone(
    monkeypatch, tmp_path, capsys
):
    try:
        logger.remove(0)
    except ValueError:
        pass

    _client(monkeypatch, tmp_path)

    capsys.readouterr()
    logger.patch(lambda record: record.update(name="mascope_sdk.test")).info("one line")
    assert capsys.readouterr().err.count("one line") == 1
