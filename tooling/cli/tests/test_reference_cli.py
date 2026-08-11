"""`mascope reference` guard rails.

An activating sync replaces whatever is currently serving annotations, and
`--prune` deletes the load it replaces, so both are gated behind a confirmation
the way `mascope dev db drop` is. These tests pin that the gate is reached
before any database work: declining must abort without a connection being
opened, which is what makes a mistyped command harmless.
"""

import pytest
from typer.testing import CliRunner

from mascope_cli.cmd.reference.main import reference_app


runner = CliRunner()


@pytest.fixture
def dump(tmp_path):
    """A readable file so the argument's exists=True check passes."""
    path = tmp_path / "custom.csv"
    path.write_text("name,formula\nGlucose,C6H12O6\n", encoding="utf-8")
    return path


@pytest.fixture
def no_engine(monkeypatch):
    """Fail loudly if the command reaches the database."""

    def _boom():
        raise AssertionError("the database must not be touched before confirmation")

    monkeypatch.setattr("mascope_cli.cmd.reference.main._sync_engine", _boom)


def test_sync_aborts_when_confirmation_is_declined(dump, no_engine):
    result = runner.invoke(
        reference_app,
        ["sync", "custom", str(dump), "--version", "v1"],
        input="n\n",
    )
    assert result.exit_code != 0


def test_sync_confirmation_names_the_deletion_when_pruning(dump, no_engine):
    result = runner.invoke(
        reference_app,
        ["sync", "custom", str(dump), "--version", "v1", "--prune"],
        input="n\n",
    )
    # The operator is told that prior loads are destroyed, not just replaced.
    assert "DELETE" in result.output
    assert result.exit_code != 0


def test_staging_needs_no_confirmation(dump, monkeypatch):
    # Staging exposes nothing, so it must not prompt. It is allowed to reach the
    # engine; failing there proves the prompt was not the thing that stopped it.
    reached = {}

    def _engine():
        reached["yes"] = True
        raise RuntimeError("stop here")

    monkeypatch.setattr("mascope_cli.cmd.reference.main._sync_engine", _engine)
    runner.invoke(
        reference_app,
        ["sync", "custom", str(dump), "--version", "v1", "--stage"],
        input="",
    )
    assert reached.get("yes") is True


def test_activate_aborts_when_confirmation_is_declined(no_engine):
    result = runner.invoke(
        reference_app,
        ["activate", "custom", "--version", "v1"],
        input="n\n",
    )
    assert result.exit_code != 0
