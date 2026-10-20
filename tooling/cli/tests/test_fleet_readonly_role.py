"""
The fleet role `postgres_readonly`: what its converge script plans, and how the
role is wired into the playbook.

The script runs on a server with that server's python3 and reaches Postgres
only through `docker exec ... psql`, so these tests load it from the repo and
hand its planning functions the catalog state a server would report. Ansible
is never imported, as in test_fleet_playbooks.py.
"""

import ast
import base64
import importlib.util
import json
import os
import re
from pathlib import Path

import pytest
import yaml

from mascope_cli.cmd.init import GENERATED_SECRETS


REPO_ROOT = Path(__file__).resolve().parents[3]
FLEET = REPO_ROOT / "tooling" / "fleet"
ROLE = FLEET / "roles" / "postgres_readonly"
SCRIPT = ROLE / "files" / "readonly_role.py"
SECRET_NAME = "postgres_readonly_password.txt"

spec = importlib.util.spec_from_file_location("readonly_role", SCRIPT)
rr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rr)

# What PostgreSQL 16 stored for `CREATE ROLE ... PASSWORD 'known-test-password'`.
POSTGRES_VERIFIER = (
    "SCRAM-SHA-256$4096:p/wAPn/AoMfSLCwiBeF/Yw==$vSEhJba9tXnDjDeLujN46mdYq2sioMm9rcOUGjxLgYI="
    ":DsB77HuVarnz8ebStRq8Yc+9NYxh89GV1L6tlE57aVM="
)


class Args:
    role = "mascope_readonly"
    app_role = "mascope_user"
    connection_limit = 4


def converged_role(password="pw"):
    return rr.RoleState(
        True,
        attributes=dict(rr.ATTRIBUTES),
        connection_limit=4,
        verifier=rr.scram_verifier(password),
        settings=[rr.READ_ONLY],
    )


def converged_database():
    return rr.DatabaseState(
        "mascope_site",
        connect=True,
        schemas=[rr.SchemaState("public", usage=True)],
        defaults={
            ("mascope_user", "", "r", "SELECT"),
            ("mascope_user", "", "n", "USAGE"),
        },
    )


def statements(changes):
    return [statement for _, _, batch in changes for statement in batch]


# --- the password ------------------------------------------------------------------


def test_the_verifier_is_the_one_postgres_computes():
    salt = base64.b64decode(POSTGRES_VERIFIER.split("$")[1].split(":")[1])
    assert rr.scram_verifier("known-test-password", salt, 4096) == POSTGRES_VERIFIER
    assert rr.password_matches(POSTGRES_VERIFIER, "known-test-password")
    assert not rr.password_matches(POSTGRES_VERIFIER, "known-test-passwore")


@pytest.mark.parametrize(
    "verifier, password",
    [("md5" + "0" * 32, "pw"), ("", "pw"), (None, "pw"), (POSTGRES_VERIFIER, None)],
)
def test_anything_but_a_matching_scram_verifier_is_a_mismatch(verifier, password):
    assert not rr.password_matches(verifier, password)


def test_a_new_salt_each_time_and_the_password_never_in_the_sql():
    changes = rr.role_changes(rr.RoleState(False), Args, "the-secret")
    sql = " ".join(statements(changes))
    assert "the-secret" not in sql and "PASSWORD 'SCRAM-SHA-256$4096:" in sql
    assert rr.scram_verifier("x") != rr.scram_verifier("x")
    assert (
        rr.redact("ERROR near 'SCRAM-SHA-256$4096:abc$def:ghi'")
        == "ERROR near '<verifier>"
    )


# --- the role ------------------------------------------------------------------------------


def test_a_missing_role_is_created_read_only():
    ((what, database, batch),) = rr.role_changes(rr.RoleState(False), Args, "pw")
    assert database == "postgres"
    assert batch[0].startswith(
        'CREATE ROLE "mascope_readonly" LOGIN NOSUPERUSER NOINHERIT'
    )
    assert "CONNECTION LIMIT 4 PASSWORD" in batch[0]
    assert (
        batch[1]
        == 'ALTER ROLE "mascope_readonly" SET default_transaction_read_only = on'
    )


def test_a_converged_role_needs_nothing():
    assert rr.role_changes(converged_role(), Args, "pw") == []


@pytest.mark.parametrize(
    "drift, expected",
    [
        (
            lambda s: s.attributes.update(rolsuper=True),
            "set the role's attributes (super)",
        ),
        (
            lambda s: s.attributes.update(rolcanlogin=False),
            "set the role's attributes (canlogin)",
        ),
        (
            lambda s: setattr(s, "connection_limit", -1),
            "set the role's attributes (connection limit -1)",
        ),
        (
            lambda s: setattr(s, "settings", ["default_transaction_read_only=off"]),
            "make the role read-only",
        ),
        (
            lambda s: s.database_settings.update(
                mascope_site=["default_transaction_read_only=off"]
            ),
            "drop the role's own default_transaction_read_only in mascope_site",
        ),
        (
            lambda s: setattr(s, "verifier", rr.scram_verifier("old")),
            "set the role's password",
        ),
        (lambda s: s.member_of.append("writers"), "take the role out of writers"),
    ],
)
def test_role_drift_is_put_back(drift, expected):
    state = converged_role()
    drift(state)
    changes = rr.role_changes(state, Args, "pw")
    assert len(changes) == 1 and changes[0][0].startswith(expected)


def test_other_role_settings_are_left_alone():
    state = converged_role()
    state.settings.append("statement_timeout=60000")
    state.database_settings["mascope_site"] = ["work_mem=64MB"]
    assert rr.role_changes(state, Args, "pw") == []


# --- the databases ----------------------------------------------------------------------------


def test_a_fresh_database_gets_select_usage_and_default_privileges():
    db = rr.DatabaseState(
        "mascope_site",
        connect=True,
        schemas=[
            rr.SchemaState("public", usage=True, unreadable=40),
            rr.SchemaState("extra", unreadable=2),
        ],
    )
    sql = statements(rr.database_changes(db, Args))
    assert sql == [
        'GRANT SELECT ON ALL TABLES IN SCHEMA "public" TO "mascope_readonly"',
        'GRANT USAGE ON SCHEMA "extra" TO "mascope_readonly"',
        'GRANT SELECT ON ALL TABLES IN SCHEMA "extra" TO "mascope_readonly"',
        'ALTER DEFAULT PRIVILEGES FOR ROLE "mascope_user" GRANT SELECT ON TABLES TO "mascope_readonly"',
        'ALTER DEFAULT PRIVILEGES FOR ROLE "mascope_user" GRANT USAGE ON SCHEMAS TO "mascope_readonly"',
    ]


def test_a_converged_database_needs_nothing():
    assert rr.database_changes(converged_database(), Args) == []


def test_write_rights_are_taken_back():
    db = converged_database()
    db.create = "role"
    db.schemas = [
        rr.SchemaState("public", usage=True, create="role", writable=3, sequences=2)
    ]
    db.defaults.add(("mascope_user", "public", "r", "INSERT"))
    db.defaults.add(("someone", "", "S", "USAGE"))
    assert statements(rr.database_changes(db, Args)) == [
        'REVOKE CREATE ON DATABASE "mascope_site" FROM "mascope_readonly"',
        'REVOKE CREATE ON SCHEMA "public" FROM "mascope_readonly"',
        'REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON ALL TABLES IN SCHEMA "public" '
        'FROM "mascope_readonly"',
        'REVOKE USAGE, UPDATE ON ALL SEQUENCES IN SCHEMA "public" FROM "mascope_readonly"',
        'ALTER DEFAULT PRIVILEGES FOR ROLE "mascope_user" IN SCHEMA "public" REVOKE INSERT ON TABLES '
        'FROM "mascope_readonly"',
        'ALTER DEFAULT PRIVILEGES FOR ROLE "someone" REVOKE USAGE ON SEQUENCES FROM "mascope_readonly"',
    ]


def test_create_held_through_public_is_reported_not_revoked():
    db = converged_database()
    db.schemas[0].create = "public"
    assert rr.database_changes(db, Args) == []
    assert rr.unfixable([db]) == [
        "mascope_site.public: PUBLIC may CREATE in the schema"
    ]


def test_names_are_quoted():
    assert rr.ident('we"ird') == '"we""ird"'
    assert rr.literal("it's") == "'it''s'"


# --- the password file ------------------------------------------------------------------------------


@pytest.fixture
def owner():
    pwd = pytest.importorskip(
        "pwd", reason="the password file is made on a POSIX server"
    )
    return pwd.getpwuid(os.getuid()).pw_name


def test_check_mode_makes_no_password_file(tmp_path, owner):
    path = tmp_path / SECRET_NAME
    changes = rr.prepare_file(str(path), owner, check=True)
    assert changes == ["create the role's password file"]
    assert not path.exists() and rr.read_password(str(path)) is None


def test_the_password_file_is_made_once_and_kept_private(tmp_path, owner):
    path = tmp_path / SECRET_NAME
    changes = rr.prepare_file(str(path), owner, check=False)
    password = rr.read_password(str(path))
    assert changes == ["create the role's password file"] and len(password) >= 40
    assert path.stat().st_mode & 0o777 == 0o600
    assert rr.prepare_file(str(path), owner, check=False) == []
    assert rr.read_password(str(path)) == password
    assert all(password not in change for change in changes)
    path.chmod(0o644)
    assert rr.prepare_file(str(path), owner, check=True) == [
        f"make the password file mode 0600 and {owner}'s"
    ]
    assert path.stat().st_mode & 0o777 == 0o644
    rr.prepare_file(str(path), owner, check=False)
    assert path.stat().st_mode & 0o777 == 0o600


def test_an_empty_password_file_or_no_checkout_is_refused(tmp_path, owner):
    path = tmp_path / SECRET_NAME
    path.write_text("\n")
    with pytest.raises(rr.Failed, match="is empty"):
        rr.read_password(str(path))
    with pytest.raises(rr.Failed, match="deployment checkout"):
        rr.prepare_file(str(tmp_path / "nope" / SECRET_NAME), owner, check=False)


@pytest.mark.parametrize(
    "argv, message",
    [
        (["--role", "mascope_user"], "cannot be the app's role"),
        (["--role", "Bad-Name"], "not a plain role name"),
        (["--connection-limit", "0"], "at least 1"),
    ],
)
def test_bad_arguments_are_refused_before_anything_runs(capsys, argv, message):
    assert rr.main(["--file", "/nowhere/x", "--file-owner", "nobody", *argv]) == 1
    assert message in json.loads(capsys.readouterr().out)["failed"]


def test_the_script_runs_on_an_older_server_python():
    ast.parse(SCRIPT.read_text(encoding="utf-8"), feature_version=(3, 8))


# --- the wiring ----------------------------------------------------------------------------------------


def test_the_role_runs_after_deploy_env_in_site_yml():
    """The secret's default path is deploy_env's mascope_path, the checkout it validates."""
    roles = yaml.safe_load((FLEET / "site.yml").read_text(encoding="utf-8"))[0]["roles"]
    assert roles.index("deploy_env") < roles.index("postgres_readonly")


def test_check_mode_reaches_the_script_and_the_script_changes_nothing_then():
    """
    `--check --diff` is how drift shows. Ansible skips a script task in check
    mode unless the task opts out, and the script only holds back when told
    --check: drop either and a check run shows nothing, or applies.
    """
    task = yaml.safe_load((ROLE / "tasks" / "main.yml").read_text(encoding="utf-8"))[0]
    assert task["check_mode"] is False
    assert (
        "{{ '--check' if ansible_check_mode else '' }}"
        in task["ansible.builtin.script"]["cmd"]
    )
    assert "readonly_role.py" in task["ansible.builtin.script"]["cmd"]


def test_every_flag_the_task_passes_is_one_the_script_takes(capsys):
    """A flag renamed on one side only would fail the role on every server."""
    task = yaml.safe_load((ROLE / "tasks" / "main.yml").read_text(encoding="utf-8"))[0]
    flags = set(
        re.findall(r"(?<![\w-])--[a-z][a-z-]*", task["ansible.builtin.script"]["cmd"])
    )
    with pytest.raises(SystemExit):
        rr.main(["--help"])
    usage = capsys.readouterr().out
    assert flags and {flag for flag in flags if flag not in usage} == set()


def _compose_default(text, variable):
    match = re.search(r"\$\{" + variable + r":-([^}]+)\}", text)
    assert match, f"docker-compose.yaml no longer defaults {variable}"
    return match.group(1)


def test_the_defaults_name_what_docker_compose_runs():
    compose = (REPO_ROOT / "docker-compose.yaml").read_text(encoding="utf-8")
    defaults = yaml.safe_load(
        (ROLE / "defaults" / "main.yml").read_text(encoding="utf-8")
    )
    assert defaults["mascope_readonly_postgres_container"] == _compose_default(
        compose, "MASCOPE_DB_CONTAINER_NAME"
    )
    assert defaults["mascope_readonly_backend_container"] == _compose_default(
        compose, "MASCOPE_BACKEND_CONTAINER_NAME"
    )
    assert defaults["mascope_readonly_app_role"] == _compose_default(
        compose, "MASCOPE_DB_USER"
    )
    assert defaults["mascope_readonly_secret"].endswith(
        "/.runtime/secrets/" + SECRET_NAME
    )


def test_the_password_file_is_no_container_secret():
    """No container mounts it and the CLI never makes it: it is the role's, and a job's, alone."""
    assert SECRET_NAME not in (REPO_ROOT / "docker-compose.yaml").read_text(
        encoding="utf-8"
    )
    assert SECRET_NAME not in GENERATED_SECRETS
