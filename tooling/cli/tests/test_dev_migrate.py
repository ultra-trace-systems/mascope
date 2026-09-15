"""
Tests for `mascope dev migrate`'s Alembic resolution.

The migration chain is *source*, so it must be read from the checkout the CLI
was invoked from. `MASCOPE_PATH` is the shared runtime home (database volumes,
secrets, `.runtime`) and normally points at the main checkout, so deriving the
Alembic directory from it makes a worktree migrate its database to *develop's*
head: the branch's own revisions are silently skipped and the app starts up
looking healthy until the missing columns are touched at runtime.

The suite builds throwaway checkouts with hand-written revision files rather
than using the repo's real migrations, so the two trees can be made to differ
the way a feature branch differs from develop.
"""

import subprocess
from pathlib import Path

import pytest

from mascope_cli import checkout
from mascope_cli.cmd.dev import migrate


# --- Fake checkouts ---


def _write_revision(versions: Path, rev: str, down: str | None) -> None:
    """Write a minimal, valid Alembic revision file into `versions`."""
    versions.joinpath(f"{rev}.py").write_text(
        f'"""revision {rev}"""\n\n'
        f'revision = "{rev}"\n'
        f"down_revision = {down!r}\n"
        "branch_labels = None\n"
        "depends_on = None\n\n\n"
        "def upgrade():\n    pass\n\n\n"
        "def downgrade():\n    pass\n",
        encoding="utf-8",
    )


def _alembic_tree(root: Path, revisions: list[str]) -> Path:
    """Build a checkout-shaped tree carrying `revisions` as a linear chain.

    :param root: Directory to treat as the repo root.
    :param revisions: Revision ids, oldest first.
    :return: The tree's ``server/backend`` directory.
    """
    backend = root / "server" / "backend"
    versions = backend / "alembic" / "versions"
    versions.mkdir(parents=True, exist_ok=True)
    (backend / "alembic.ini").write_text(
        "[alembic]\nscript_location = %(here)s/alembic\n", encoding="utf-8"
    )
    down = None
    for rev in revisions:
        _write_revision(versions, rev, down)
        down = rev
    return backend


@pytest.fixture
def diverged(mascope_home, tmp_path, monkeypatch):
    """A worktree one migration ahead of the shared MASCOPE_PATH home.

    Mirrors the reported situation: the runtime home sits on develop
    (``a1 -> b2``) while the invoked worktree adds ``c3``. The home is the
    suite's real MASCOPE_PATH, so the buggy resolution would find a genuine
    (and wrong) migration chain there rather than merely erroring out.

    :return: (worktree root, shared home root)
    """
    _alembic_tree(mascope_home, ["a1", "b2"])
    worktree = tmp_path / "worktree"
    _alembic_tree(worktree, ["a1", "b2", "c3"])

    monkeypatch.setattr(checkout, "source_checkout", lambda *a, **kw: worktree)
    return worktree, mascope_home


# --- Database stub ---


class _FakeConnection:
    def __init__(self, revision: str | None):
        self._revision = revision

    def execute(self, _statement):
        return type("Result", (), {"scalar": lambda _self: self._revision})()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeEngine:
    def __init__(self, revision: str | None):
        self._revision = revision

    def connect(self):
        return _FakeConnection(self._revision)

    def dispose(self):
        pass


def _stub_database(monkeypatch, current_revision: str | None) -> None:
    """Pin the database's `alembic_version` without touching Postgres."""
    monkeypatch.setattr(migrate.runtime, "secret", lambda *a, **kw: "pw")
    monkeypatch.setattr(
        migrate, "create_engine", lambda *a, **kw: _FakeEngine(current_revision)
    )


# --- Resolution ---


def test_backend_path_follows_the_invoked_checkout(diverged):
    worktree, home = diverged

    assert migrate._backend_path() == worktree / "server" / "backend"
    assert migrate._backend_path() != home / "server" / "backend"


def test_backend_path_falls_back_to_mascope_path_outside_a_checkout(
    tmp_path, monkeypatch
):
    # An operator install (wheel): MASCOPE_PATH is the deploy directory and
    # the source tree lives under it, so the old behavior is still correct.
    monkeypatch.setattr(checkout, "source_checkout", lambda *a, **kw: None)
    monkeypatch.setenv("MASCOPE_PATH", str(tmp_path / "deploy"))

    assert migrate._backend_path() == tmp_path / "deploy" / "server" / "backend"


def test_alembic_runs_in_the_worktree_backend(diverged, monkeypatch):
    worktree, _ = diverged
    recorded = {}

    def fake_run(cmd, cwd=None, **kwargs):
        recorded["cmd"] = cmd
        recorded["cwd"] = cwd
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(migrate.subprocess, "run", fake_run)

    migrate._run_alembic(["upgrade", "head"])

    assert recorded["cwd"] == worktree / "server" / "backend"
    assert recorded["cmd"] == ["uv", "run", "alembic", "upgrade", "head"]


# --- The regression ---


def test_pending_check_compares_against_the_worktree_head(diverged, monkeypatch):
    """A worktree that adds a migration reports its own head as pending.

    The database is at ``b2``, which *is* the shared home's head. Resolving
    Alembic from MASCOPE_PATH therefore compared b2 to b2 and reported "up to
    date" - the silent failure that let an instance boot without the branch's
    schema.
    """
    _stub_database(monkeypatch, current_revision="b2")

    assert migrate.check_pending_migrations() is True


def test_pending_check_is_satisfied_by_the_worktree_head(diverged, monkeypatch):
    """Control for the test above: at ``c3`` the database is current.

    `check_pending_migrations` returns True on *any* exception, so without
    this the regression test would pass even if the stubs blew up.
    """
    _stub_database(monkeypatch, current_revision="c3")

    assert migrate.check_pending_migrations() is False


def test_run_migrations_upgrades_the_worktree_chain(diverged, monkeypatch):
    """`run_migrations` drives Alembic in the worktree, not the shared home."""
    worktree, _ = diverged
    recorded = {}

    def fake_run(cmd, cwd=None, **kwargs):
        recorded["cwd"] = cwd
        return subprocess.CompletedProcess(cmd, 0, "", "")

    # The pre-migration backup needs a live container; its failure is
    # documented as non-fatal, so exercise that path rather than mocking pg.
    monkeypatch.setattr(
        migrate,
        "pg_dump",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("no pg")),
    )
    monkeypatch.setattr(migrate.subprocess, "run", fake_run)

    assert migrate.run_migrations() is True
    assert recorded["cwd"] == worktree / "server" / "backend"
