#!/usr/bin/env python3
"""Converge one server's read-only Postgres role; the fleet role postgres_readonly runs this.

The role (mascope_readonly by default) logs in with a password kept in the
deployment checkout's .runtime/secrets/, is read-only by default
(default_transaction_read_only = on), and may SELECT every table and view of
the app databases (mascope_<env>) and nothing else. Default privileges on the
app's role make the tables later migrations create readable as well.

Every statement runs through `docker exec <postgres container> psql` as the
app's role, which is the cluster's bootstrap superuser, over the container's
local socket. The password never reaches Postgres in the clear: the role is
given a SCRAM verifier computed here, and is checked against the file the same
way, so a run where nothing drifted changes nothing.

    readonly_role.py --file PATH --file-owner USER [--check] [options]

Prints one JSON object, {"changed": ..., "changes": [...], "verified": ...};
with --check it changes nothing and lists what would change. After applying,
it reads everything back and fails if the role still differs, which is how a
privilege only PUBLIC holds (and revoking from the role cannot remove) shows
up. Last, unless the backend container is down, it connects as the role from
that container the way a prod job does and checks the session is read-only.

Python 3.8 or later, standard library only: it runs with the server's python3.
"""

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import subprocess
import sys
from dataclasses import dataclass, field


ROLE_NAME = re.compile(r"[a-z_][a-z0-9_]*")
# What the role must be, column by column of pg_authid.
ATTRIBUTES = {
    "rolcanlogin": True,
    "rolsuper": False,
    "rolinherit": False,
    "rolcreaterole": False,
    "rolcreatedb": False,
    "rolreplication": False,
    "rolbypassrls": False,
}
ROLE_OPTIONS = (
    "LOGIN NOSUPERUSER NOINHERIT NOCREATEROLE NOCREATEDB NOREPLICATION NOBYPASSRLS"
)
READ_ONLY = "default_transaction_read_only=on"
TABLE_WRITES = "INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER"
# pg_default_acl.defaclobjtype, as ALTER DEFAULT PRIVILEGES names it.
DEFAULT_OBJECTS = {
    "r": "TABLES",
    "S": "SEQUENCES",
    "f": "FUNCTIONS",
    "T": "TYPES",
    "n": "SCHEMAS",
}
WANTED_DEFAULTS = {("r", "SELECT"), ("n", "USAGE")}
SCRAM = re.compile(
    r"SCRAM-SHA-256\$(\d+):([A-Za-z0-9+/=]+)\$([A-Za-z0-9+/=]+):([A-Za-z0-9+/=]+)"
)


class Failed(Exception):
    """The role cannot be converged; the message says why."""


def ident(name):
    return '"' + name.replace('"', '""') + '"'


def literal(text):
    return "'" + text.replace("'", "''") + "'"


# --- the password -------------------------------------------------------------


def scram_verifier(password, salt=None, iterations=4096):
    """The SCRAM-SHA-256 verifier Postgres stores for password (RFC 5802, 7677)."""
    salt = os.urandom(16) if salt is None else salt
    salted = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    client_key = hmac.new(salted, b"Client Key", "sha256").digest()
    server_key = hmac.new(salted, b"Server Key", "sha256").digest()
    stored_key = hashlib.sha256(client_key).digest()

    def b64(data):
        return base64.b64encode(data).decode("ascii")

    return "SCRAM-SHA-256${}:{}${}:{}".format(
        iterations, b64(salt), b64(stored_key), b64(server_key)
    )


def password_matches(verifier, password):
    """Whether verifier, as pg_authid holds it, is password's."""
    m = SCRAM.fullmatch(verifier or "")
    if not m or password is None:
        return False
    expected = scram_verifier(password, base64.b64decode(m.group(2)), int(m.group(1)))
    return hmac.compare_digest(expected, verifier)


def redact(text):
    return re.sub(r"SCRAM-SHA-256\$\S+", "<verifier>", text)


def prepare_file(path, owner, check):
    """Make the password file when there is none, owned by owner and mode 0600; return the changes.

    The password stays out of what this returns, which is reported.
    """
    import pwd  # here, so the tests can import this module where there is no pwd

    directory = os.path.dirname(path)
    if not os.path.isdir(directory):
        raise Failed(
            "{} does not exist: is this the deployment checkout?".format(directory)
        )
    user = pwd.getpwnam(owner)
    if not os.path.exists(path):
        if not check:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as fh:
                fh.write(secrets.token_urlsafe(32) + "\n")
            os.chown(path, user.pw_uid, user.pw_gid)
        return ["create the role's password file"]
    st = os.stat(path)
    if st.st_mode & 0o777 == 0o600 and st.st_uid == user.pw_uid:
        return []
    if not check:
        os.chown(path, user.pw_uid, user.pw_gid)
        os.chmod(path, 0o600)
    return ["make the password file mode 0600 and {}'s".format(owner)]


def read_password(path):
    """The password in path, or None when there is no file yet (a --check before the first run)."""
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        password = fh.readline().strip()
    if not password:
        raise Failed(
            "{} is empty: delete it and run again for a new password".format(path)
        )
    return password


# --- what the cluster holds -------------------------------------------------------------


def psql(args, sql, database):
    """Run sql in database as the app's role; the rows, tab-separated fields."""
    p = subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            args.container,
            "psql",
            "-X",
            "-q",
            "-A",
            "-t",
            "-F",
            "\t",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            args.app_role,
            "-d",
            database,
        ],
        input=sql,
        capture_output=True,
        text=True,
    )
    if p.returncode != 0:
        raise Failed(
            "psql in {} failed: {}".format(
                database, redact(p.stderr.strip() or p.stdout.strip())
            )
        )
    return [line.split("\t") for line in p.stdout.splitlines() if line.strip()]


@dataclass
class RoleState:
    exists: bool
    attributes: dict = field(default_factory=dict)
    connection_limit: int = -1
    verifier: str = ""
    settings: list = field(default_factory=list)  # role-wide, "name=value"
    database_settings: dict = field(
        default_factory=dict
    )  # database -> its per-database settings
    member_of: list = field(default_factory=list)


def role_oid(args):
    return "(SELECT oid FROM pg_roles WHERE rolname = {})".format(literal(args.role))


def read_role(args):
    rows = psql(
        args,
        "SELECT {}, rolconnlimit, coalesce(rolpassword, '') FROM pg_authid WHERE rolname = {}".format(
            ", ".join(ATTRIBUTES), literal(args.role)
        ),
        "postgres",
    )
    if not rows:
        return RoleState(False)
    row = rows[0]
    state = RoleState(
        True,
        attributes={name: value == "t" for name, value in zip(ATTRIBUTES, row)},
        connection_limit=int(row[len(ATTRIBUTES)]),
        verifier=row[len(ATTRIBUTES) + 1],
    )
    for database, config in psql(
        args,
        "SELECT coalesce(d.datname, ''), array_to_string(s.setconfig, chr(31)) FROM pg_db_role_setting s "
        "LEFT JOIN pg_database d ON d.oid = s.setdatabase WHERE s.setrole = {}".format(
            role_oid(args)
        ),
        "postgres",
    ):
        if database:
            state.database_settings[database] = config.split(chr(31))
        else:
            state.settings = config.split(chr(31))
    state.member_of = [
        row[0]
        for row in psql(
            args,
            "SELECT r.rolname FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.roleid "
            "WHERE m.member = {} ORDER BY 1".format(role_oid(args)),
            "postgres",
        )
    ]
    return state


@dataclass
class SchemaState:
    name: str
    usage: bool = False
    create: str = (
        ""  # "role" when granted to the role itself, "public" when held another way
    )
    unreadable: int = 0  # tables and views the role cannot SELECT
    writable: int = 0  # tables it holds a write privilege on
    sequences: int = 0  # sequences it may use or update


@dataclass
class DatabaseState:
    name: str
    connect: bool = False
    create: str = ""  # as for a schema
    schemas: list = field(default_factory=list)
    defaults: set = field(
        default_factory=set
    )  # (grantor role, schema or "", object type, privilege)


def _held(has, direct):
    return "" if has != "t" else ("role" if direct == "t" else "public")


def read_databases(args, exists):
    """Each app database and what the role may do in it.

    A role that does not exist yet would have what PUBLIC has, so that is what a
    --check before the first run plans against. The image's own database, named
    after the app's role, is not an app database.
    """
    who = literal(
        args.role if exists else "public"
    )  # the has_*_privilege functions take "public"
    oid = role_oid(
        args
    )  # NULL while the role does not exist: then it is granted nothing directly
    databases = []
    for name, connect, create, direct in psql(
        args,
        "SELECT d.datname, has_database_privilege({who}, d.oid, 'CONNECT'), "
        "has_database_privilege({who}, d.oid, 'CREATE'), "
        "EXISTS (SELECT 1 FROM aclexplode(coalesce(d.datacl, acldefault('d', d.datdba))) a "
        "WHERE a.grantee = {r} AND a.privilege_type = 'CREATE') "
        "FROM pg_database d WHERE d.datallowconn AND NOT d.datistemplate AND starts_with(d.datname, {p}) "
        "AND d.datname <> {app} ORDER BY 1".format(
            who=who, r=oid, p=literal(args.database_prefix), app=literal(args.app_role)
        ),
        "postgres",
    ):
        db = DatabaseState(name, connect=connect == "t", create=_held(create, direct))
        # CASE, not AND: Postgres may call a has_*_privilege function before the
        # relkind test, and has_sequence_privilege fails on a table.
        relation = "FROM pg_class c WHERE c.relnamespace = n.oid AND CASE WHEN c.relkind IN ('r', 'p', 'v', 'm', 'f')"
        for row in psql(
            args,
            "SELECT n.nspname, has_schema_privilege({who}, n.oid, 'USAGE'), "
            "has_schema_privilege({who}, n.oid, 'CREATE'), "
            "EXISTS (SELECT 1 FROM aclexplode(coalesce(n.nspacl, acldefault('n', n.nspowner))) a "
            "WHERE a.grantee = {r} AND a.privilege_type = 'CREATE'), "
            "(SELECT count(*) {rel} THEN NOT has_table_privilege({who}, c.oid, 'SELECT') END), "
            "(SELECT count(*) {rel} THEN has_table_privilege({who}, c.oid, '{w}') "
            "OR has_any_column_privilege({who}, c.oid, 'INSERT, UPDATE, REFERENCES') END), "
            "(SELECT count(*) FROM pg_class c WHERE c.relnamespace = n.oid "
            "AND CASE WHEN c.relkind = 'S' THEN has_sequence_privilege({who}, c.oid, 'USAGE, UPDATE') END) "
            "FROM pg_namespace n WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema' "
            "ORDER BY 1".format(who=who, r=oid, rel=relation, w=TABLE_WRITES),
            name,
        ):
            schema, usage, create, direct, unreadable, writable, sequences = row
            db.schemas.append(
                SchemaState(
                    schema,
                    usage == "t",
                    _held(create, direct),
                    int(unreadable),
                    int(writable),
                    int(sequences),
                )
            )
        db.defaults = {
            tuple(row)
            for row in psql(
                args,
                "SELECT pg_get_userbyid(d.defaclrole), coalesce(n.nspname, ''), d.defaclobjtype, a.privilege_type "
                "FROM pg_default_acl d LEFT JOIN pg_namespace n ON n.oid = d.defaclnamespace, "
                "aclexplode(d.defaclacl) a WHERE a.grantee = {}".format(oid),
                name,
            )
        }
        databases.append(db)
    return databases


# --- what to change --------------------------------------------------------------------------
#
# Each change is (what it does, the database to run it in, its statements).


def role_changes(state, args, password):
    role = ident(args.role)
    options = "{} CONNECTION LIMIT {}".format(ROLE_OPTIONS, args.connection_limit)
    verifier = scram_verifier(password) if password else None
    with_password = " PASSWORD {}".format(literal(verifier)) if verifier else ""
    if not state.exists:
        return [
            (
                "create the role {}, read-only by default".format(args.role),
                "postgres",
                [
                    "CREATE ROLE {} {}{}".format(role, options, with_password),
                    "ALTER ROLE {} SET default_transaction_read_only = on".format(role),
                ],
            )
        ]
    changes = []
    wrong = [
        name[3:]
        for name, want in ATTRIBUTES.items()
        if state.attributes.get(name) != want
    ]
    if state.connection_limit != args.connection_limit:
        wrong.append("connection limit {}".format(state.connection_limit))
    if wrong:
        changes.append(
            (
                "set the role's attributes ({})".format(", ".join(wrong)),
                "postgres",
                ["ALTER ROLE {} {}".format(role, options)],
            )
        )
    if READ_ONLY not in state.settings:
        changes.append(
            (
                "make the role read-only by default",
                "postgres",
                ["ALTER ROLE {} SET default_transaction_read_only = on".format(role)],
            )
        )
    for database, settings in sorted(state.database_settings.items()):
        if any(s.startswith("default_transaction_read_only=") for s in settings):
            changes.append(
                (
                    "drop the role's own default_transaction_read_only in {}".format(
                        database
                    ),
                    "postgres",
                    [
                        "ALTER ROLE {} IN DATABASE {} RESET default_transaction_read_only".format(
                            role, ident(database)
                        )
                    ],
                )
            )
    if not password_matches(state.verifier, password):
        changes.append(
            (
                "set the role's password from the password file",
                "postgres",
                ["ALTER ROLE {}{}".format(role, with_password)],
            )
        )
    for other in state.member_of:
        changes.append(
            (
                "take the role out of {}".format(other),
                "postgres",
                ["REVOKE {} FROM {}".format(ident(other), role)],
            )
        )
    return changes


def database_changes(db, args):
    role, owner, out = ident(args.role), ident(args.app_role), []
    if not db.connect:
        out.append(
            (
                "{}: let the role connect".format(db.name),
                "postgres",
                ["GRANT CONNECT ON DATABASE {} TO {}".format(ident(db.name), role)],
            )
        )
    if db.create == "role":
        out.append(
            (
                "{}: take CREATE on the database from the role".format(db.name),
                "postgres",
                ["REVOKE CREATE ON DATABASE {} FROM {}".format(ident(db.name), role)],
            )
        )
    for s in db.schemas:
        where = "{}.{}".format(db.name, s.name)
        schema = ident(s.name)
        if not s.usage:
            out.append(
                (
                    "{}: grant USAGE on the schema".format(where),
                    db.name,
                    ["GRANT USAGE ON SCHEMA {} TO {}".format(schema, role)],
                )
            )
        if s.create == "role":
            out.append(
                (
                    "{}: take CREATE on the schema from the role".format(where),
                    db.name,
                    ["REVOKE CREATE ON SCHEMA {} FROM {}".format(schema, role)],
                )
            )
        if s.unreadable:
            out.append(
                (
                    "{}: grant SELECT on {} table(s) and view(s)".format(
                        where, s.unreadable
                    ),
                    db.name,
                    [
                        "GRANT SELECT ON ALL TABLES IN SCHEMA {} TO {}".format(
                            schema, role
                        )
                    ],
                )
            )
        if s.writable:
            out.append(
                (
                    "{}: take write privileges on {} table(s) from the role".format(
                        where, s.writable
                    ),
                    db.name,
                    [
                        "REVOKE {} ON ALL TABLES IN SCHEMA {} FROM {}".format(
                            TABLE_WRITES, schema, role
                        )
                    ],
                )
            )
        if s.sequences:
            out.append(
                (
                    "{}: take USAGE and UPDATE on {} sequence(s) from the role".format(
                        where, s.sequences
                    ),
                    db.name,
                    [
                        "REVOKE USAGE, UPDATE ON ALL SEQUENCES IN SCHEMA {} FROM {}".format(
                            schema, role
                        )
                    ],
                )
            )
    wanted = {
        (args.app_role, "", objtype, privilege)
        for objtype, privilege in WANTED_DEFAULTS
    }
    if (args.app_role, "", "r", "SELECT") not in db.defaults:
        out.append(
            (
                "{}: make tables {} creates later readable".format(
                    db.name, args.app_role
                ),
                db.name,
                [
                    "ALTER DEFAULT PRIVILEGES FOR ROLE {} GRANT SELECT ON TABLES TO {}".format(
                        owner, role
                    )
                ],
            )
        )
    if (args.app_role, "", "n", "USAGE") not in db.defaults:
        out.append(
            (
                "{}: grant USAGE on schemas {} creates later".format(
                    db.name, args.app_role
                ),
                db.name,
                [
                    "ALTER DEFAULT PRIVILEGES FOR ROLE {} GRANT USAGE ON SCHEMAS TO {}".format(
                        owner, role
                    )
                ],
            )
        )
    for grantor, schema, objtype, privilege in sorted(db.defaults - wanted):
        scope = " IN SCHEMA {}".format(ident(schema)) if schema else ""
        out.append(
            (
                "{}: drop the default {} on {} from {}".format(
                    db.name, privilege, DEFAULT_OBJECTS.get(objtype, objtype), grantor
                ),
                db.name,
                [
                    "ALTER DEFAULT PRIVILEGES FOR ROLE {}{} REVOKE {} ON {} FROM {}".format(
                        ident(grantor),
                        scope,
                        privilege,
                        DEFAULT_OBJECTS.get(objtype, objtype),
                        role,
                    )
                ],
            )
        )
    return out


def unfixable(databases):
    """What revoking from the role cannot change: privileges it holds through PUBLIC."""
    out = []
    for db in databases:
        if db.create == "public":
            out.append("{}: PUBLIC may CREATE in the database".format(db.name))
        for s in db.schemas:
            if s.create == "public":
                out.append(
                    "{}.{}: PUBLIC may CREATE in the schema".format(db.name, s.name)
                )
    return out


def apply(args, changes):
    for _, database, statements in changes:
        psql(args, "".join(statement + ";\n" for statement in statements), database)


# --- the check from the backend container ------------------------------------------------------------

PROBE = (
    "import os, psycopg2\n"
    "c = psycopg2.connect(dbname=os.environ['MASCOPE_DB_NAME'], connect_timeout=10)\n"
    "cur = c.cursor()\n"
    "cur.execute(\"SELECT current_user, current_setting('transaction_read_only'), "
    '(SELECT count(*) FROM alembic_version)")\n'
    "print('\\t'.join(str(v) for v in cur.fetchone()))\n"
)


def verify_connection(args, password):
    """Connect as the role from the backend container, as a prod job does; a note on what was seen."""
    running = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", args.verify_container],
        capture_output=True,
        text=True,
    )
    if running.stdout.strip() != "true":
        return "skipped: {} is not running".format(args.verify_container)
    p = subprocess.run(
        [
            "docker",
            "exec",
            "-e",
            "PGHOST=" + args.db_host,
            "-e",
            "PGUSER=" + args.role,
            "-e",
            "PGPASSWORD",
            args.verify_container,
            args.verify_python,
            "-c",
            PROBE,
        ],
        env=dict(os.environ, PGPASSWORD=password),
        capture_output=True,
        text=True,
        timeout=120,
    )
    fields = p.stdout.strip().split("\t")
    if p.returncode != 0 or fields[:2] != [args.role, "on"]:
        raise Failed(
            "connecting as {} from {} did not give a read-only session: {}".format(
                args.role,
                args.verify_container,
                (p.stderr.strip() or p.stdout.strip())[-500:],
            )
        )
    return "connected as {} from {}: read-only, reads alembic_version".format(
        args.role, args.verify_container
    )


# --- the run ---------------------------------------------------------------------------------------------------


def converge(args):
    report = prepare_file(args.file, args.file_owner, args.check)
    password = read_password(args.file)

    role = read_role(args)
    planned = role_changes(role, args, password)
    report += [what for what, _, _ in planned]
    if planned and not args.check:
        apply(args, planned)
        role = read_role(args)
        left = role_changes(role, args, password)
        if left:
            raise Failed(
                "the role still differs after the changes: "
                + "; ".join(what for what, _, _ in left)
            )

    databases = read_databases(args, role.exists)
    planned = [change for db in databases for change in database_changes(db, args)]
    report += [what for what, _, _ in planned]
    if planned and not args.check:
        apply(args, planned)
        databases = read_databases(args, True)
        left = [change for db in databases for change in database_changes(db, args)]
        if left:
            raise Failed(
                "still differs after the changes: "
                + "; ".join(what for what, _, _ in left)
            )
    problems = unfixable(databases)
    if problems:
        raise Failed(
            "the role would inherit write privileges through PUBLIC: "
            + "; ".join(problems)
        )
    if not databases:
        report.append("note: no database named {}* yet".format(args.database_prefix))

    if args.check and report:
        verified = "skipped: --check with changes pending"
    elif not args.verify_container:
        verified = "skipped"
    else:
        verified = verify_connection(args, password)
    changed = any(not line.startswith("note:") for line in report)
    return {"changed": changed, "changes": report, "verified": verified}


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--file",
        required=True,
        help="the role's password file, under the checkout's .runtime/secrets/",
    )
    parser.add_argument(
        "--file-owner",
        required=True,
        help="the deploy user, who owns the password file",
    )
    parser.add_argument("--role", default="mascope_readonly")
    parser.add_argument(
        "--app-role",
        default="mascope_user",
        help="the app's role: the superuser, and the tables' owner",
    )
    parser.add_argument("--container", default="mascope_prod_postgres")
    parser.add_argument(
        "--database-prefix",
        default="mascope_",
        help="the app databases' names start with this",
    )
    parser.add_argument("--connection-limit", type=int, default=4)
    parser.add_argument(
        "--verify-container",
        default="mascope_prod_backend",
        help="'' to skip the connection check",
    )
    parser.add_argument("--verify-python", default="/opt/uv/tools/mascope/bin/python")
    parser.add_argument(
        "--db-host",
        default="postgres",
        help="the database host, from the backend container",
    )
    parser.add_argument(
        "--check", action="store_true", help="change nothing; list what would change"
    )
    args = parser.parse_args(argv)
    try:
        for name in (args.role, args.app_role):
            if not ROLE_NAME.fullmatch(name):
                raise Failed("{!r} is not a plain role name".format(name))
        if args.role == args.app_role:
            raise Failed("the read-only role cannot be the app's role")
        if args.connection_limit < 1:
            raise Failed("the connection limit must be at least 1")
        result = converge(args)
    except (Failed, OSError, subprocess.SubprocessError) as exc:
        print(json.dumps({"changed": False, "changes": [], "failed": str(exc)}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
