"""Tests for `mascope prod mfa` - the two-factor lockout escape hatch.

The command exists for the case nothing else can reach, so the risks worth
covering are it doing more than it claims (touching a password), doing nothing
when it should refuse loudly, and the email argument reaching SQL unescaped.
"""

from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from mascope_cli.cmd.prod import mfa


runner = CliRunner()


def test_quoting_escapes_an_apostrophe():
    # The email arrives from a shell. Unescaped, an apostrophe would end the
    # literal and change what the statement does.
    assert mfa._quote("o'brien@example.org") == "'o''brien@example.org'"


def test_quoting_wraps_an_ordinary_address():
    assert mfa._quote("someone@example.org") == "'someone@example.org'"


@pytest.fixture
def running(monkeypatch):
    """Pretend the stack and database are up."""
    monkeypatch.setattr(mfa, "is_container_running", lambda _mode: True)
    monkeypatch.setattr(mfa, "is_database_ready", lambda mode, env: True)


def test_reset_refuses_when_the_stack_is_down(monkeypatch):
    monkeypatch.setattr(mfa, "is_container_running", lambda _mode: False)
    result = runner.invoke(mfa.mfa_app, ["reset", "someone@example.org", "--yes"])
    assert result.exit_code == 1


def test_reset_refuses_when_the_database_is_absent(monkeypatch):
    monkeypatch.setattr(mfa, "is_container_running", lambda _mode: True)
    monkeypatch.setattr(mfa, "is_database_ready", lambda mode, env: False)
    result = runner.invoke(mfa.mfa_app, ["reset", "someone@example.org", "--yes"])
    assert result.exit_code == 1


def test_reset_refuses_an_unknown_account(running):
    with patch.object(mfa, "_psql", return_value="") as psql:
        result = runner.invoke(mfa.mfa_app, ["reset", "nobody@example.org", "--yes"])
    assert result.exit_code == 1
    # Only the lookup ran; nothing was written.
    assert psql.call_count == 1


def test_reset_is_a_no_op_when_the_account_has_no_factor(running):
    with patch.object(mfa, "_psql", return_value="7|f") as psql:
        result = runner.invoke(mfa.mfa_app, ["reset", "someone@example.org", "--yes"])
    assert result.exit_code == 0
    assert psql.call_count == 1


def test_reset_clears_the_factor_and_its_recovery_codes(running):
    with patch.object(mfa, "_psql", side_effect=["7|t", ""]) as psql:
        result = runner.invoke(mfa.mfa_app, ["reset", "someone@example.org", "--yes"])
    assert result.exit_code == 0
    written = psql.call_args_list[1].args[0]
    assert "DELETE FROM user_recovery_code WHERE user_id = 7" in written
    assert "mfa_enabled = false" in written
    assert "mfa_secret = NULL" in written
    # One transaction, so a failure between the two halves cannot leave codes
    # behind for a factor that is gone.
    assert written.startswith("BEGIN;") and written.rstrip().endswith("COMMIT;")


def test_reset_never_touches_the_password(running):
    # The command's whole claim is that it is not a way in.
    with patch.object(mfa, "_psql", side_effect=["7|t", ""]) as psql:
        runner.invoke(mfa.mfa_app, ["reset", "someone@example.org", "--yes"])
    for call in psql.call_args_list:
        assert "hashed_password" not in call.args[0]
        assert "password" not in call.args[0].lower()


def test_reset_uses_the_id_from_the_lookup_not_the_typed_email(running):
    # The write is keyed on an integer id parsed from the lookup, so nothing
    # from the command line reaches the UPDATE.
    with patch.object(mfa, "_psql", side_effect=["7|t", ""]) as psql:
        runner.invoke(mfa.mfa_app, ["reset", "o'brien@example.org", "--yes"])
    assert "o'brien" not in psql.call_args_list[1].args[0]


def test_reset_prompts_before_writing_without_yes(running):
    with patch.object(mfa, "_psql", side_effect=["7|t", ""]) as psql:
        result = runner.invoke(
            mfa.mfa_app, ["reset", "someone@example.org"], input="n\n"
        )
    assert result.exit_code != 0
    # Aborted at the prompt: the lookup ran, the write did not.
    assert psql.call_count == 1


def test_status_reports_each_account(running):
    rows = "a@example.org|t|8\nb@example.org|f|0"
    with patch.object(mfa, "_psql", return_value=rows):
        result = runner.invoke(mfa.mfa_app, ["status"])
    assert result.exit_code == 0
