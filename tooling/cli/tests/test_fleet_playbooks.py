"""
Drift guards for the fleet playbooks under `tooling/fleet/`.

These read the playbooks from the repo and parse them with PyYAML; Ansible
itself is never imported (it is not a dependency of this suite, and the
questions here are about task order and file contents, not execution).
"""

import importlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from mascope_cli.cmd.init import GENERATED_SECRETS, PLACEHOLDER_SECRETS


# The prod package re-exports the `doctor` command, which shadows the module.
doctor = importlib.import_module("mascope_cli.cmd.prod.doctor")

REPO_ROOT = Path(__file__).resolve().parents[3]
UPDATE_PLAYBOOK = REPO_ROOT / "tooling" / "fleet" / "update.yml"
REBOOT_PLAYBOOK = REPO_ROOT / "tooling" / "fleet" / "reboot.yml"
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
    provisions (1.8.0 adds `mfa_encryption_key`, written by `prod update` ->
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


@pytest.mark.parametrize(
    "playbook", [UPDATE_PLAYBOOK, REBOOT_PLAYBOOK], ids=lambda p: p.name
)
def test_a_filling_disk_does_not_halt_the_roll(playbook):
    """
    The doctor gate passes the doctor's disk-filling warning and nothing else.

    Both plays run `serial: 1` with `any_errors_fatal`, so a failed doctor
    stops every server after this one. The doctor exits EXIT_FILLING when the
    stack is healthy but a disk is projected to reach its floor within days:
    that wants an operator, not a fleet left on the old release. Every other
    non-zero exit is a real failure and must still stop the roll.
    """
    tasks = yaml.safe_load(playbook.read_text(encoding="utf-8"))[0]["tasks"]
    gate = tasks[_task_index(tasks, "prod doctor")]
    register = gate.get("register")
    assert register, f"{playbook.name}: the doctor gate must register its result"
    assert gate.get("failed_when") == (
        f"{register}.rc not in [0, {doctor.EXIT_FILLING}]"
    ), (
        f"{playbook.name}: the doctor gate must pass exit 0 and {doctor.EXIT_FILLING} only"
    )


def test_every_runtime_secret_in_compose_is_generated_by_the_cli():
    """
    A compose secret sourced from `.runtime/secrets/` must be one the CLI knows
    how to create, or a deployment that predates it cannot start: compose
    refuses to create the container on a declared-but-absent secret file.
    Shipping the compose change ahead of the CLI that provisions it is what
    made the 1.8.0 rollout order load-bearing.

    This passes today - it guards the next secret somebody adds, and is not a
    substitute for the ordering test above.
    """
    compose = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))

    for name, spec in (compose.get("secrets") or {}).items():
        if name in OPERATOR_SUPPLIED_SECRETS:
            continue
        source = (spec or {}).get("file", "")
        assert Path(source).name in GENERATED_SECRETS + PLACEHOLDER_SECRETS, (
            f"compose secret '{name}' reads {source}, which no CLI code path "
            "creates - add it to GENERATED_SECRETS (and, if it is new in this "
            "release, to AUTO_PROVISIONED_SECRETS), to PLACEHOLDER_SECRETS if "
            "the operator fills it in later, or list it in "
            "OPERATOR_SUPPLIED_SECRETS if the operator brings it by hand"
        )


def test_the_frontend_publishes_its_loopback_listener_on_loopback_only():
    """
    :8443 never asks for the CDN's client certificate, so it must not be
    reachable from anywhere but the host itself.

    With origin pulls on, :443 answers only the CDN, and the loopback listener
    is how the reboot probe and the security suite's origin vantage still reach
    the origin. Published on all interfaces it would be a way round the check
    for anyone the firewall lets through - and every tailnet peer gets through
    (fleet README, "The tailnet bypasses ufw").
    """
    compose = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
    frontend = compose["services"]["frontend"]

    assert sorted(frontend["ports"]) == ["127.0.0.1:8443:8443", "443:443"]
    assert "MASCOPE_ORIGIN_PULL" in frontend["environment"]
    assert "origin_pull_ca" in frontend["secrets"]
    assert compose["secrets"]["origin_pull_ca"]["file"] == (
        ".runtime/secrets/origin_pull_ca.pem"
    )


# curl for the reboot probe: answers STUB_<port> for a URL on that port, and
# logs each port it was asked to STUB_LOG. "000" is curl's own answer when the
# connection is refused: it prints the write-out and exits non-zero.
PROBE_CURL_STUB = """#!/bin/sh
for arg; do url="$arg"; done
port="${url#https://*:}"; port="${port%%/*}"
echo "$port" >> "$STUB_LOG"
eval "code=\\${STUB_$port:-000}"
printf '%s' "$code"
[ "$code" = 000 ] && exit 7
exit 0
"""


@pytest.mark.skipif(not shutil.which("sh"), reason="the probe runs under sh")
@pytest.mark.parametrize(
    ("loopback", "tls", "answer", "asked"),
    [
        # The loopback listener answers: :443 is never asked, so a site with
        # origin pulls on (403 there) passes its gate.
        ("422", "403", "422", ["8443"]),
        # A release from before the listener: the connection is refused, and
        # only then is :443 asked.
        ("000", "422", "422", ["8443", "443"]),
        # A listener that answers wrongly is the answer: falling back on
        # anything but a refused connection would hide a broken :8443.
        ("502", "422", "502", ["8443"]),
    ],
    ids=["loopback", "older-release", "loopback-broken"],
)
def test_the_reboot_probe_asks_the_loopback_listener_first(
    tmp_path, loopback, tls, answer, asked
):
    """
    The probe must reach the origin whether origin pulls are on or not.

    :443 answers 403 to a client without the CDN's certificate once they are
    on, which would fail every reboot's gate and halt the batch.
    """
    tasks = yaml.safe_load(REBOOT_PLAYBOOK.read_text(encoding="utf-8"))[0]["tasks"]
    probe = next(t for t in tasks if t.get("register") == "api_probe")
    cmd = probe["ansible.builtin.shell"]["cmd"].replace(
        "{{ app_host }}", "server.example"
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "curl"
    stub.write_text(PROBE_CURL_STUB, encoding="utf-8")
    stub.chmod(0o755)
    log = tmp_path / "asked"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "STUB_LOG": str(log),
        "STUB_8443": loopback,
        "STUB_443": tls,
    }

    result = subprocess.run(
        ["sh", "-c", cmd], env=env, capture_output=True, text=True, check=False
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == answer
    assert log.read_text(encoding="utf-8").split() == asked
