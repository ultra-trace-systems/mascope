"""
Hermetic tests for the loguru handler a ``MascopeClient`` adds.

Each client replaces the handler the previous one added, by its ID. An
application, or a test, that removes every loguru handler in between leaves
that ID stale; the next client must still be made, and still log once.
"""

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
