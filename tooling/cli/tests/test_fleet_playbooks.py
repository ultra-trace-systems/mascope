"""
Drift guards for the fleet playbooks under `tooling/fleet/`.

These read the playbooks from the repo and parse them with PyYAML; Ansible
itself is never imported (it is not a dependency of this suite, and the
questions here are about task order and file contents, not execution).
"""

from pathlib import Path

import yaml

from mascope_cli.cmd.init import GENERATED_SECRETS


REPO_ROOT = Path(__file__).resolve().parents[3]
UPDATE_PLAYBOOK = REPO_ROOT / "tooling" / "fleet" / "update.yml"
COMPOSE_FILE = REPO_ROOT / "docker-compose.yaml"

# Compose secrets the operator supplies by hand rather than the CLI generating
# them - the TLS certificate and its key, which have to come from a CA. Listed
# by name so that adding a secret is a deliberate choice between "the CLI mints
# this" and "the operator brings this", rather than something a path pattern
# quietly decides.
OPERATOR_SUPPLIED_SECRETS = ("ssl_certificate", "ssl_secret_key")


def _task_index(tasks: list[dict], fragment: str) -> int:
    """Index of the first task whose command contains `fragment`."""
    for index, task in enumerate(tasks):
        for module in ("ansible.builtin.shell", "ansible.builtin.command"):
            spec = task.get(module)
            cmd = spec.get("cmd", "") if isinstance(spec, dict) else str(spec or "")
            if fragment in cmd:
                return index
    raise AssertionError(f"no task in {UPDATE_PLAYBOOK.name} runs '{fragment}'")


def test_cli_is_reinstalled_before_the_stack_update():
    """
    The CLI reinstall must run between the checkout and `prod update`.

    The deployment checkout *is* MASCOPE_PATH, so checking out the release
    tag swaps in that release's `docker-compose.yaml` before anything else
    runs. A release may add a compose secret that only the matching CLI
    provisions (2.0.0 adds `mfa_encryption_key`, written by `prod update` ->
    `_ensure_secrets`). Update the stack first and the still-installed older
    CLI drives the newer compose file: compose refuses to create the backend
    on the missing secret file, having already stopped the running one, so
    the server ends the play with its backend down - and `any_errors_fatal`
    halts the rest of the rollout.
    """
    tasks = yaml.safe_load(UPDATE_PLAYBOOK.read_text(encoding="utf-8"))[0]["tasks"]
    names = [task.get("name") for task in tasks]

    checkout = _task_index(tasks, "git checkout")
    reinstall = _task_index(tasks, "uv tool install")
    stack_update = _task_index(tasks, "prod update")

    assert checkout < reinstall < stack_update, (
        "update.yml must reinstall the CLI after checking out the release tag "
        "and BEFORE 'mascope prod update' - a release that adds a compose "
        f"secret takes the backend down otherwise. Order is: {names[checkout:]}"
    )


def test_every_runtime_secret_in_compose_is_generated_by_the_cli():
    """
    A compose secret sourced from `.runtime/secrets/` must be one the CLI knows
    how to create, or a deployment that predates it cannot start: compose
    refuses to create the container on a declared-but-absent secret file.
    Shipping the compose change ahead of the CLI that provisions it is what
    made the 2.0.0 rollout order load-bearing.

    This passes today - it guards the next secret somebody adds, and is not a
    substitute for the ordering test above.
    """
    compose = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))

    for name, spec in (compose.get("secrets") or {}).items():
        if name in OPERATOR_SUPPLIED_SECRETS:
            continue
        source = (spec or {}).get("file", "")
        assert Path(source).name in GENERATED_SECRETS, (
            f"compose secret '{name}' reads {source}, which no CLI code path "
            "creates - add it to GENERATED_SECRETS (and, if it is new in this "
            "release, to AUTO_PROVISIONED_SECRETS), or list it in "
            "OPERATOR_SUPPLIED_SECRETS if the operator brings it by hand"
        )
