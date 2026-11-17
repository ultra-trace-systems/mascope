"""
`pg_restore` that ends early stops the restore inside the container.

`docker exec` leaves its command running in the container when the client is
killed, so a restore cut short by a timeout or by Ctrl-C would go on loading
into the database with nobody reading its exit status. `pg_restore` has no
time limit by default, and when it does end early it terminates the restore's
connections. A timeout then raises a `RuntimeError` saying the database is
incomplete, which the commands report as an error rather than a traceback. A
Ctrl-C logs that and lets the interrupt through, so the command ends: a
caller that goes on past a `RuntimeError` (`env sync`) must not go on past it.
"""

import importlib
import subprocess

import pytest
from typer.testing import CliRunner

from mascope_cli.pg import admin


prod_db = importlib.import_module("mascope_cli.cmd.prod.db.main")
env_main = importlib.import_module("mascope_cli.cmd.env.main")
env_sync = importlib.import_module("mascope_cli.cmd.env._sync")
runner = CliRunner()

CONTAINER = "postgres_container"
USER = "mascope_user"
DATABASE = "mascope_site"


class FakeDocker:
    """`subprocess.run` for `docker exec`: the dump is there, restore as told."""

    def __init__(self, restore=None, stop_code=0, stop_raises=None):
        #: What the pg_restore call does: None returns 0, an exception raises.
        self.restore = restore
        self.stop_code = stop_code
        self.stop_raises = stop_raises
        self.calls = []

    def __call__(self, cmd, input=None, timeout=None, **kwargs):  # noqa: A002, ARG002
        self.calls.append((cmd, timeout))
        inner = cmd[3:] if cmd[2] == "-i" else cmd[2:]
        inner = inner[1:]  # drop the container name
        if inner[0] == "test":
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if inner[0] == "pg_restore":
            if self.restore is not None:
                raise self.restore
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if inner[0] == "psql":
            if self.stop_raises is not None:
                raise self.stop_raises
            return subprocess.CompletedProcess(cmd, self.stop_code, "", "refused")
        raise AssertionError(f"unexpected command {cmd}")

    def of(self, program):
        return [(c, t) for c, t in self.calls if program in c]


@pytest.fixture
def dump(tmp_path):
    path = tmp_path / "site.dump"
    path.write_bytes(b"")
    return path


def _install(monkeypatch, fake):
    monkeypatch.setattr(admin.subprocess, "run", fake)
    return fake


def _timeout():
    return subprocess.TimeoutExpired(["docker", "exec"], 600)


@pytest.fixture
def logged_errors(monkeypatch):
    collected = []
    monkeypatch.setattr(admin.runtime.logger, "error", collected.append)
    return collected


def _assert_stopped_last(fake):
    """The stop terminated the restore's connections, and nothing ran after it."""
    [(cmd, _)] = fake.of("psql")
    assert cmd[:3] == ["docker", "exec", CONTAINER]
    sql = cmd[cmd.index("--command") + 1]
    assert "pg_terminate_backend(pid)" in sql
    assert f"datname = '{DATABASE}'" in sql
    assert "application_name = 'pg_restore'" in sql
    # The stop is the last thing done: nothing after it reloads the database.
    assert fake.calls[-1][0] == cmd


def test_a_restore_has_no_time_limit(monkeypatch, dump):
    fake = _install(monkeypatch, FakeDocker())

    admin.pg_restore(CONTAINER, USER, DATABASE, dump, "/backups")

    [(cmd, timeout)] = fake.of("pg_restore")
    assert timeout is None
    assert "--jobs" not in cmd
    assert cmd[-1] == "/backups/site.dump"


def test_jobs_become_pg_restore_jobs(monkeypatch, dump):
    fake = _install(monkeypatch, FakeDocker())

    admin.pg_restore(CONTAINER, USER, DATABASE, dump, "/backups", jobs=4)

    [(cmd, _)] = fake.of("pg_restore")
    assert cmd[cmd.index("--jobs") + 1] == "4"
    assert cmd[-1] == "/backups/site.dump"


def test_a_given_timeout_is_passed_on(monkeypatch, dump):
    fake = _install(monkeypatch, FakeDocker())

    admin.pg_restore(CONTAINER, USER, DATABASE, dump, "/backups", timeout=30)

    [(_, timeout)] = fake.of("pg_restore")
    assert timeout == 30


def test_a_timed_out_restore_is_stopped_in_the_container(monkeypatch, dump):
    fake = _install(monkeypatch, FakeDocker(restore=_timeout()))

    with pytest.raises(RuntimeError) as raised:
        admin.pg_restore(CONTAINER, USER, DATABASE, dump, "/backups", timeout=30)

    assert "exceeded the 30s timeout" in str(raised.value)
    assert f"'{DATABASE}' is incomplete" in str(raised.value)
    _assert_stopped_last(fake)


def test_an_interrupted_restore_is_stopped_and_the_interrupt_goes_on(
    monkeypatch, dump, logged_errors
):
    fake = _install(monkeypatch, FakeDocker(restore=KeyboardInterrupt()))

    with pytest.raises(KeyboardInterrupt):
        admin.pg_restore(CONTAINER, USER, DATABASE, dump, "/backups")

    _assert_stopped_last(fake)
    [said] = logged_errors
    assert "interrupted and stopped" in said
    assert f"'{DATABASE}' is incomplete" in said


def test_a_failed_stop_says_the_restore_may_still_run(monkeypatch, dump):
    _install(monkeypatch, FakeDocker(restore=_timeout(), stop_code=2))

    with pytest.raises(RuntimeError, match="may still be running") as raised:
        admin.pg_restore(CONTAINER, USER, DATABASE, dump, "/backups", timeout=30)

    assert "refused" in str(raised.value)


def test_a_failed_stop_after_an_interrupt_is_logged_and_the_interrupt_goes_on(
    monkeypatch, dump, logged_errors
):
    _install(monkeypatch, FakeDocker(restore=KeyboardInterrupt(), stop_code=2))

    with pytest.raises(KeyboardInterrupt):
        admin.pg_restore(CONTAINER, USER, DATABASE, dump, "/backups")

    [said] = logged_errors
    assert "may still be running" in said
    assert "refused" in said


def test_a_stop_that_times_out_says_the_restore_may_still_run(monkeypatch, dump):
    fake = FakeDocker(restore=_timeout(), stop_raises=_timeout())
    _install(monkeypatch, fake)

    with pytest.raises(RuntimeError, match="may still be running"):
        admin.pg_restore(CONTAINER, USER, DATABASE, dump, "/backups", timeout=30)

    [(_, timeout)] = fake.of("psql")
    assert timeout == 60


# --- the command ----------------------------------------------------------


@pytest.fixture
def prod_stack(monkeypatch, tmp_path):
    """A running prod Postgres whose database drop and create succeed."""
    events = []
    monkeypatch.setattr(prod_db, "check_prerequisites", lambda _mode: True)
    monkeypatch.setattr(prod_db, "is_server_ready", lambda _mode: True)
    monkeypatch.setattr(prod_db, "validate_env", lambda _env: True)
    monkeypatch.setattr(prod_db, "dirs", lambda transfer, mode: (tmp_path, "/backups"))
    monkeypatch.setattr(prod_db, "drop_database", lambda *a: None)
    monkeypatch.setattr(prod_db, "_create_database_if_missing", lambda *a: True)
    monkeypatch.setattr(
        prod_db, "_grant_readonly_role", lambda *a: events.append("grant")
    )
    (tmp_path / "site.dump").write_bytes(b"")
    return events


def test_restore_reports_a_timed_out_restore_without_a_traceback(
    monkeypatch, prod_stack
):
    fake = _install(monkeypatch, FakeDocker(restore=_timeout()))

    result = runner.invoke(prod_db.prod_db_app, ["restore", "site.dump", "--yes"])

    assert result.exit_code == 1, result.output
    assert not isinstance(result.exception, subprocess.TimeoutExpired)
    assert len(fake.of("psql")) == 1
    assert prod_stack == []  # no grants on an incomplete database


def test_restore_aborts_on_an_interrupted_restore(
    monkeypatch, prod_stack, logged_errors
):
    fake = _install(monkeypatch, FakeDocker(restore=KeyboardInterrupt()))

    result = runner.invoke(prod_db.prod_db_app, ["restore", "site.dump", "--yes"])

    # Click ends an interrupted command with 128 + SIGINT, not a traceback.
    assert result.exit_code == 130, result.output
    assert isinstance(result.exception, SystemExit)
    assert len(fake.of("psql")) == 1
    assert any("is incomplete" in said for said in logged_errors)
    assert prod_stack == []


def test_restore_passes_jobs_on(monkeypatch, prod_stack):
    fake = _install(monkeypatch, FakeDocker())

    result = runner.invoke(
        prod_db.prod_db_app, ["restore", "site.dump", "--jobs", "4", "--yes"]
    )

    assert result.exit_code == 0, result.output
    [(cmd, timeout)] = fake.of("pg_restore")
    assert cmd[cmd.index("--jobs") + 1] == "4"
    assert timeout is None
    assert prod_stack == ["grant"]


def test_restore_refuses_zero_jobs(monkeypatch, prod_stack):
    fake = _install(monkeypatch, FakeDocker())

    result = runner.invoke(
        prod_db.prod_db_app, ["restore", "site.dump", "--jobs", "0", "--yes"]
    )

    assert result.exit_code == 2
    assert fake.calls == []


@pytest.fixture
def local_sync(monkeypatch, tmp_path):
    """`env sync` between two local envs, down to the real `pg_restore`."""
    stages = []
    dump_file = tmp_path / "site.dump"
    dump_file.write_bytes(b"")
    monkeypatch.setattr(env_main, "env_exists_local", lambda name: True)
    monkeypatch.setattr(env_sync, "_dump_local", lambda *a: dump_file)
    monkeypatch.setattr(env_sync, "is_server_ready", lambda _mode: True)
    monkeypatch.setattr(
        env_sync, "dirs", lambda transfer, mode: (tmp_path, "/transfer")
    )
    monkeypatch.setattr(env_sync, "drop_database", lambda *a: None)
    monkeypatch.setattr(env_sync, "admin_create_database", lambda *a: None)
    monkeypatch.setattr(
        env_sync, "grant_readonly_role", lambda *a: stages.append("grant")
    )
    monkeypatch.setattr(
        env_sync, "_cleanup_transfer", lambda *a: stages.append("cleanup")
    )
    monkeypatch.setattr(
        env_main, "sync_filestore", lambda *a, **kw: stages.append("filestore")
    )
    return stages


def test_env_sync_stops_at_an_interrupted_restore(
    monkeypatch, local_sync, logged_errors
):
    fake = _install(monkeypatch, FakeDocker(restore=KeyboardInterrupt()))

    result = runner.invoke(env_main.env_app, ["sync", "src", "dev", "dst", "dev"])

    assert result.exit_code == 130, result.output
    assert len(fake.of("psql")) == 1
    assert any("is incomplete" in said for said in logged_errors)
    # Neither the grants, nor the dump's cleanup, nor the filestore stage.
    assert local_sync == []


def test_env_sync_goes_on_to_the_filestore_after_a_finished_restore(
    monkeypatch, local_sync
):
    _install(monkeypatch, FakeDocker())

    result = runner.invoke(env_main.env_app, ["sync", "src", "dev", "dst", "dev"])

    assert result.exit_code == 0, result.output
    assert local_sync == ["grant", "cleanup", "filestore"]
