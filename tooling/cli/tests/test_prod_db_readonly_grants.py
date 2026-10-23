"""
The fleet's read-only role gets its grants back in a restored or new database.

The role (`tooling/fleet`, role `postgres_readonly`) is cluster-wide, but its
grants live in each database, and every restore the CLI makes passes
`pg_restore --no-acl`. So `grant_readonly_role` re-issues them after a restore
and for a new env's database, through `docker exec ... psql`, and these tests
drive it with `subprocess.run` stubbed to answer as the container would.
"""

import importlib
import importlib.util
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mascope_cli.cmd.env import _sync
from mascope_cli.pg import admin


# The package's `main` attribute is the group's callback, not the module.
prod_db = importlib.import_module("mascope_cli.cmd.prod.db.main")
runner = CliRunner()

CONTAINER = "mascope_prod_postgres"
USER = "mascope_user"
DATABASE = "mascope_site"

SCRIPT = (
    Path(__file__).resolve().parents[3]
    / "tooling"
    / "fleet"
    / "roles"
    / "postgres_readonly"
    / "files"
    / "readonly_role.py"
)


class FakeDocker:
    """`subprocess.run` for `docker exec <postgres> psql`: the role exists or not."""

    def __init__(self, role_exists=True, grant_fails=False, events=None):
        self.role_exists = role_exists
        self.grant_fails = grant_fails
        self.events = events if events is not None else []
        self.calls = []

    def __call__(self, cmd, input=None, **kwargs):  # noqa: A002, ARG002
        self.calls.append((cmd, input))
        database = cmd[cmd.index("--dbname") + 1]
        if "--command" in cmd:
            self.events.append("look for the role")
            out = "1\n" if self.role_exists else ""
            return _completed(cmd, 0, out)
        self.events.append(f"grant in {database}")
        return _completed(cmd, 1 if self.grant_fails else 0, "", "boom")

    @property
    def grants(self):
        """The (database, SQL) of each grant batch."""
        return [
            (cmd[cmd.index("--dbname") + 1], sql)
            for cmd, sql in self.calls
            if sql is not None
        ]


def _completed(cmd, code, out="", err=""):
    import subprocess

    return subprocess.CompletedProcess(cmd, code, stdout=out, stderr=err)


@pytest.fixture
def docker(monkeypatch):
    fake = FakeDocker()
    monkeypatch.setattr(admin.subprocess, "run", fake)
    return fake


# --- the grants -----------------------------------------------------------


def test_a_cluster_without_the_role_is_left_as_it_is(docker):
    docker.role_exists = False

    assert admin.grant_readonly_role(CONTAINER, USER, DATABASE) is False
    assert docker.grants == []
    cmd, _ = docker.calls[0]
    assert cmd[:3] == ["docker", "exec", CONTAINER]
    assert "rolname = 'mascope_readonly'" in cmd[-1]


def test_the_role_is_granted_read_access_in_the_database(docker):
    assert admin.grant_readonly_role(CONTAINER, USER, DATABASE) is True

    [(database, sql)] = docker.grants
    assert database == DATABASE
    cmd, _ = docker.calls[-1]
    assert cmd[:4] == ["docker", "exec", "-i", CONTAINER]
    assert "ON_ERROR_STOP=1" in cmd
    assert f'GRANT CONNECT ON DATABASE "{DATABASE}" TO "mascope_readonly"' in sql
    assert (
        f'ALTER DEFAULT PRIVILEGES FOR ROLE "{USER}" GRANT SELECT ON TABLES '
        'TO "mascope_readonly"' in sql
    )
    assert (
        f'ALTER DEFAULT PRIVILEGES FOR ROLE "{USER}" GRANT USAGE ON SCHEMAS '
        'TO "mascope_readonly"' in sql
    )
    assert "GRANT USAGE ON SCHEMA %I TO %I" in sql
    assert "GRANT SELECT ON ALL TABLES IN SCHEMA %I TO %I" in sql
    # Defaults first, as in the fleet script: no table falls between them and
    # the bulk grant.
    assert sql.index("ALTER DEFAULT PRIVILEGES") < sql.index("ON ALL TABLES")


def test_another_role_name_is_looked_for_and_granted(docker):
    admin.grant_readonly_role(CONTAINER, USER, DATABASE, role="audit_ro")

    assert "rolname = 'audit_ro'" in docker.calls[0][0][-1]
    [(_, sql)] = docker.grants
    assert "mascope_readonly" not in sql
    assert sql.count("'audit_ro'") == 2
    assert sql.count('"audit_ro"') == 3


def test_a_failed_grant_raises(docker):
    docker.grant_fails = True

    with pytest.raises(RuntimeError, match="boom"):
        admin.grant_readonly_role(CONTAINER, USER, DATABASE)


def test_a_failed_lookup_raises_before_any_grant(monkeypatch):
    monkeypatch.setattr(
        admin.subprocess, "run", lambda cmd, **kw: _completed(cmd, 2, "", "down")
    )

    with pytest.raises(RuntimeError, match="down"):
        admin.grant_readonly_role(CONTAINER, USER, DATABASE)


def test_the_grants_are_the_ones_the_fleet_role_would_make(docker):
    """
    What the CLI grants is what `readonly_role.py` plans for a database that
    has none, so the playbook finds nothing left to grant after a restore.
    """
    spec = importlib.util.spec_from_file_location("readonly_role", SCRIPT)
    rr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rr)

    class Args:
        role = "mascope_readonly"
        app_role = USER

    restored = rr.DatabaseState(
        DATABASE, schemas=[rr.SchemaState("public", unreadable=3)]
    )
    planned = [s for _, _, batch in rr.database_changes(restored, Args) for s in batch]
    assert planned, "the fleet script plans nothing for an ungranted database"

    admin.grant_readonly_role(CONTAINER, USER, DATABASE)
    [(_, sql)] = docker.grants
    for statement in planned:
        # The CLI walks the schemas in a DO block, naming each with %I.
        templated = re.sub(r'"(public|mascope_readonly)"', "%I", statement)
        assert statement in sql or templated in sql, statement


# --- the commands ---------------------------------------------------------


@pytest.fixture
def prod_stack(monkeypatch, tmp_path):
    """A running prod Postgres, with the steps before the grants recorded."""
    events = []
    monkeypatch.setattr(prod_db, "check_prerequisites", lambda _mode: True)
    monkeypatch.setattr(prod_db, "is_server_ready", lambda _mode: True)
    monkeypatch.setattr(prod_db, "validate_env", lambda _env: True)
    monkeypatch.setattr(prod_db, "dirs", lambda transfer, mode: (tmp_path, "/backups"))
    monkeypatch.setattr(
        prod_db, "drop_database", lambda *a: events.append("drop database")
    )
    monkeypatch.setattr(
        prod_db,
        "_create_database_if_missing",
        lambda *a: events.append("create database") or True,
    )
    monkeypatch.setattr(prod_db, "pg_restore", lambda *a: events.append("restore"))
    (tmp_path / "site.dump").write_bytes(b"")
    fake = FakeDocker(events=events)
    monkeypatch.setattr(admin.subprocess, "run", fake)
    return fake


def test_restore_grants_the_role_after_restoring(prod_stack):
    result = runner.invoke(prod_db.prod_db_app, ["restore", "site.dump", "--yes"])

    assert result.exit_code == 0, result.output
    assert prod_stack.events == [
        "drop database",
        "create database",
        "restore",
        "look for the role",
        f"grant in {prod_stack.grants[0][0]}",
    ]
    assert prod_stack.grants[0][0].startswith("mascope_")


def test_restore_without_the_role_on_the_cluster_succeeds(prod_stack):
    prod_stack.role_exists = False

    result = runner.invoke(prod_db.prod_db_app, ["restore", "site.dump", "--yes"])

    assert result.exit_code == 0, result.output
    assert prod_stack.grants == []


def test_restore_fails_when_the_grants_fail(prod_stack):
    prod_stack.grant_fails = True

    result = runner.invoke(prod_db.prod_db_app, ["restore", "site.dump", "--yes"])

    assert result.exit_code == 1
    assert prod_stack.events[-1].startswith("grant in ")


def test_create_grants_the_role_in_the_new_database(prod_stack):
    result = runner.invoke(prod_db.prod_db_app, ["create"])

    assert result.exit_code == 0, result.output
    assert prod_stack.events == [
        "create database",
        "look for the role",
        f"grant in {prod_stack.grants[0][0]}",
    ]


def test_env_sync_grants_the_role_after_restoring(monkeypatch, tmp_path):
    events = []
    monkeypatch.setattr(_sync, "is_server_ready", lambda _mode: True)
    monkeypatch.setattr(
        _sync, "drop_database", lambda *a: events.append("drop database")
    )
    monkeypatch.setattr(
        _sync, "admin_create_database", lambda *a: events.append("create database")
    )
    monkeypatch.setattr(_sync, "pg_restore", lambda *a: events.append("restore"))
    fake = FakeDocker(events=events)
    monkeypatch.setattr(admin.subprocess, "run", fake)

    _sync._restore_local("prod", "default", tmp_path / "site.dump")

    assert events[:3] == ["drop database", "create database", "restore"]
    assert events[3:] == ["look for the role", f"grant in {fake.grants[0][0]}"]
