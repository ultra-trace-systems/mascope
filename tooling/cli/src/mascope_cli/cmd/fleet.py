"""
`mascope fleet` — thin SSH wrappers around the fleet's Ansible inventory.

The Ansible inventory (`tooling/fleet/inventory.local.yml`, gitignored — this
repo is public) is the single source of truth for the production servers:
names, tailnet addresses, deploy user, and SSH key. These commands read that
same file, so there is no second server list to drift, and an agent needs no
private fleet knowledge to pull logs from a server.

Resolution order for the inventory file:

1. ``MASCOPE_FLEET_INVENTORY`` (explicit override)
2. ``<checkout root>/tooling/fleet/inventory.local.yml``
3. ``$MASCOPE_PATH/tooling/fleet/inventory.local.yml``

The MASCOPE_PATH fallback matters for agent worktrees: the inventory is
untracked, so it only exists in the main checkout (the machine's shared
runtime home), not in each worktree.
"""

import os
import shlex
import subprocess
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from mascope_cli.checkout import source_checkout


fleet_app = typer.Typer()

INVENTORY_RELPATH = Path("tooling") / "fleet" / "inventory.local.yml"


@fleet_app.callback()
def main() -> None:
    """
    Operate on the production fleet over SSH (tailnet).

    Servers come from the Ansible inventory; see `mascope fleet list`.
    """


def _inventory_candidates() -> list[Path]:
    """The paths where the inventory may live, in resolution order."""
    override = os.environ.get("MASCOPE_FLEET_INVENTORY")
    if override:
        return [Path(override).expanduser()]
    candidates = []
    root = source_checkout()
    if root:
        candidates.append(root / INVENTORY_RELPATH)
    mascope_path = os.environ.get("MASCOPE_PATH")
    if mascope_path:
        fallback = Path(mascope_path) / INVENTORY_RELPATH
        if fallback not in candidates:
            candidates.append(fallback)
    return candidates


def _load_inventory() -> tuple[dict, dict, Path] | None:
    """
    Parse the first inventory found into (hosts, group_vars, path).

    Returns None (after printing where it looked) when no inventory exists —
    e.g. a fresh machine, or a worktree without MASCOPE_PATH set.
    """
    candidates = _inventory_candidates()
    path = next((c for c in candidates if c.is_file()), None)
    if path is None:
        console = Console(stderr=True)
        console.print("[red]No fleet inventory found.[/red] Looked for:")
        for candidate in candidates or ["(nowhere - no checkout and no MASCOPE_PATH)"]:
            console.print(f"  - {candidate}")
        console.print(
            "Copy tooling/fleet/inventory.example.yml to inventory.local.yml "
            "in the main checkout, or point MASCOPE_FLEET_INVENTORY at it."
        )
        return None
    try:
        import yaml
    except ImportError as error:  # pragma: no cover - a checkout ships pyyaml
        raise ImportError(
            "Reading the fleet inventory requires pyyaml - run `uv sync` in the "
            "source checkout"
        ) from error
    with open(path, encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}
    fleet = data.get("fleet") or {}
    return fleet.get("hosts") or {}, fleet.get("vars") or {}, path


def _host_ssh_argv(name: str, host: dict, group_vars: dict) -> list[str]:
    """`ssh` argv (sans remote command) for one inventory host."""

    def var(key):
        # Ansible semantics: host-level vars override group vars.
        return host.get(key) or group_vars.get(key)

    address = host.get("ansible_host") or name
    user = var("ansible_user")
    key_file = var("ansible_ssh_private_key_file")
    argv = ["ssh"]
    if key_file:
        argv += ["-i", str(Path(key_file).expanduser())]
    argv.append(f"{user}@{address}" if user else address)
    return argv


@fleet_app.command(name="list")
def list_hosts() -> None:
    """List the fleet servers from the Ansible inventory."""
    inventory = _load_inventory()
    if inventory is None:
        raise typer.Exit(1)
    hosts, group_vars, path = inventory

    table = Table(caption=str(path))
    table.add_column("Host", style="cyan", no_wrap=True)
    table.add_column("Address")
    table.add_column("User")
    for name, host in hosts.items():
        host = host or {}
        table.add_row(
            name,
            host.get("ansible_host", ""),
            host.get("ansible_user") or group_vars.get("ansible_user", ""),
        )
    Console().print(table)


@fleet_app.command(
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True}
)
def logs(
    ctx: typer.Context,
    host: Annotated[str, typer.Argument(help="Server name from the inventory")],
) -> None:
    """
    Query a fleet server's logs over SSH.

    All further flags pass straight through to `mascope logs query` on the
    server, which runs against the prod logs unless --dev/--prod is given:

        mascope fleet logs server1 -l error --interval '1 day' --json
    """
    inventory = _load_inventory()
    if inventory is None:
        raise typer.Exit(1)
    hosts, group_vars, path = inventory
    if host not in hosts:
        console = Console(stderr=True)
        console.print(
            f"[red]Unknown fleet host {host!r}.[/red] "
            f"Inventory ({path}) has: {', '.join(hosts) or '(no hosts)'}"
        )
        raise typer.Exit(1)

    passthrough = list(ctx.args)
    if "--prod" not in passthrough and "--dev" not in passthrough:
        passthrough.insert(0, "--prod")
    # One pre-quoted string: ssh joins its command args with spaces and the
    # remote shell re-parses them, so each arg is quoted for POSIX sh here -
    # a grep pattern like "o'clock" must arrive intact.
    remote = " ".join(
        shlex.quote(arg) for arg in ["mascope", "logs", "query", *passthrough]
    )
    argv = _host_ssh_argv(host, hosts[host] or {}, group_vars) + [remote]

    try:
        result = subprocess.run(argv)
    except FileNotFoundError:
        Console(stderr=True).print(
            "[red]ssh not found on PATH[/red] - install an OpenSSH client."
        )
        raise typer.Exit(1) from None
    if result.returncode:
        raise typer.Exit(result.returncode)
