"""
Tests for production compose invocation error propagation.

CI builds release images via `mascope prod build` and relies on its exit
status; a swallowed docker compose failure means jobs continue against
stale or missing images.

`_run_compose` is also the single place a running deployment provisions the
secrets a newer release added - centralised there so the unattended update
path cannot skip it. That writes into the live secret store, so the three
things it must never do (overwrite a key already in use, invent one of the
long-standing secrets, or run on a command that is not an "up") are pinned
here as well.
"""

import importlib
import shutil
import subprocess

import pytest
import typer

from mascope_cli.cmd.init import AUTO_PROVISIONED_SECRETS, GENERATED_SECRETS


# The prod package re-exports a `main` function that shadows the module of
# the same name, so import the module explicitly.
prod_main = importlib.import_module("mascope_cli.cmd.prod.main")

# The secret whose absence on an existing deployment means "this release added
# it". Named literally rather than looped over AUTO_PROVISIONED_SECRETS: a loop
# would pass vacuously if that tuple were ever emptied, which is exactly one of
# the regressions these tests exist to catch.
AUTO_SECRET = "mfa_encryption_key.txt"

# The secrets `mascope init` generates for a fresh home but that an already
# running deployment must never have re-invented under it: a fresh
# postgres_password breaks against the existing database, a fresh
# jwt_secret_key ends every session, and a fresh server_owner_secret_key
# invalidates the owner bootstrap. Their absence is an error to surface.
LONG_STANDING_SECRETS = (
    "postgres_password.txt",
    "jwt_secret_key.txt",
    "server_owner_secret_key.txt",
)

# Every argv shape the `up` command builds (see the `up` command body, which
# appends --build and --detach independently), plus the bare interactive form.
UP_SHAPES = (
    ["up"],
    ["up", "--detach"],
    ["up", "--build"],
    ["up", "--build", "--detach"],
)


@pytest.fixture
def compose_env(monkeypatch):
    """Stub out config resolution and the subprocess itself."""
    monkeypatch.setattr(
        prod_main,
        "_compose_env",
        lambda building=False: {"MASCOPE_DB_NAME": "db", "MASCOPE_TIMEZONE": "UTC"},
    )

    def set_result(returncode: int):
        monkeypatch.setattr(
            prod_main.lib,
            "run",
            lambda **kwargs: subprocess.CompletedProcess([], returncode),
        )

    return set_result


@pytest.fixture
def secrets_dir(mascope_home):
    """An absent .runtime/secrets under the temp home, before and after."""
    path = mascope_home / ".runtime" / "secrets"
    shutil.rmtree(path, ignore_errors=True)
    yield path
    shutil.rmtree(path, ignore_errors=True)


# --- exit-code propagation ---


def test_compose_failure_propagates_exit_code(compose_env):
    compose_env(17)

    with pytest.raises(typer.Exit) as excinfo:
        prod_main._run_compose(["build"], building=True)

    assert excinfo.value.exit_code == 17


def test_compose_success_returns_normally(compose_env):
    compose_env(0)

    prod_main._run_compose(["build"], building=True)  # must not raise


# --- secret provisioning on "up" ---


def test_up_never_overwrites_an_existing_secret(compose_env, secrets_dir):
    # The MFA key decrypts every enrolled TOTP seed, so rotating it out from
    # under a running deployment locks every enrolled user out at once - the
    # provisioning path must be additive only.
    compose_env(0)
    secrets_dir.mkdir(parents=True, exist_ok=True)
    key = secrets_dir / AUTO_SECRET
    key.write_bytes(b"the-key-the-enrolled-seeds-were-encrypted-with\n")

    prod_main._run_compose(["up", "--detach"])

    assert key.read_bytes() == b"the-key-the-enrolled-seeds-were-encrypted-with\n"


@pytest.mark.parametrize("args", UP_SHAPES, ids=lambda a: " ".join(a))
def test_every_up_shape_creates_a_missing_secret(compose_env, secrets_dir, args):
    # Compose refuses to start on a declared-but-absent secret, so a deployment
    # predating a newly added one must gain it on an ordinary `up` - whichever
    # form the operator or the unattended updater used. A trigger keyed on the
    # full argv rather than the subcommand would strand the bare `mascope prod
    # up` an operator types interactively.
    compose_env(0)

    prod_main._run_compose(args)

    key = secrets_dir / AUTO_SECRET
    assert key.is_file(), f"'{' '.join(args)}' did not provision {AUTO_SECRET}"
    assert len(key.read_text(encoding="utf-8").strip()) >= 32


@pytest.mark.parametrize("args", [["build"], ["down"], ["ps"], ["pull"]])
def test_only_an_up_provisions_secrets(compose_env, secrets_dir, args):
    # Provisioning is scoped to the command that would otherwise fail on a
    # missing secret. `pull` matters most: `prod update` runs it immediately
    # before the `up`, and it must not be the thing that writes the key.
    # Asserted on the secret file rather than on the directory: pre-creating an
    # empty .runtime/secrets is harmless, writing a key into it is not.
    compose_env(0)

    prod_main._run_compose(args)

    assert not (secrets_dir / AUTO_SECRET).exists(), f"'{args[0]}' wrote a secret"


def test_up_never_invents_a_long_standing_secret(compose_env, secrets_dir):
    compose_env(0)

    prod_main._run_compose(["up", "--detach"])

    for name in LONG_STANDING_SECRETS:
        assert not (secrets_dir / name).exists(), (
            f"{name} was auto-provisioned; a missing one means an existing "
            "secret was lost, which must be surfaced rather than replaced"
        )


def test_auto_provisioned_secrets_are_declared_secrets():
    # Anything auto-provisioned on `up` must also be generated by `mascope
    # init`, or a freshly initialized home and an updated deployment disagree
    # about which files the compose secrets resolve to.
    assert set(AUTO_PROVISIONED_SECRETS) <= set(GENERATED_SECRETS)


def test_the_long_standing_secrets_are_the_ones_not_auto_provisioned():
    # Keeps the lists above honest: a secret added to GENERATED_SECRETS has to
    # be classified as auto-provisionable or not, deliberately.
    assert set(AUTO_PROVISIONED_SECRETS) == {AUTO_SECRET}
    assert set(LONG_STANDING_SECRETS) == set(GENERATED_SECRETS) - set(
        AUTO_PROVISIONED_SECRETS
    )
