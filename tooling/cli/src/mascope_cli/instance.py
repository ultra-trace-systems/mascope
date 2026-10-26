"""
Per-worktree dev-stack instances.

Several checkouts on one machine (e.g. agents working in independent git
worktrees) can each run the app while sharing a single Postgres/Redis pair:
the shared infra serves one database per env (``mascope_<env>``), and only the
local backend/frontend processes need distinct host ports. An *instance* binds
a worktree to a slot ``N``, from which everything else is derived:

    env           = wt-<worktree-basename>
    api_port      = 8090 + N      (backend bind; frontend + file-converter target)
    frontend_port = 5173 + N      (Vite dev server)

Bindings are allocated first-come and persisted to a registry under the shared
``MASCOPE_PATH`` so they are stable across runs and collision-free across
worktrees. A new binding also skips any slot whose API or frontend port another
process already listens on (a bare ``mascope dev run``, or a service outside the
registry), so an instance never shares a port with it; the skipped slot is not
recorded and is offered again once its ports are free. An existing binding is
never probed: a worktree keeps its slot whether its stack runs or not. The registry is the source of truth for the slot->ports mapping; the
runtime honours the ``MASCOPE_ENV`` / ``MASCOPE_API_PORT`` / ``MASCOPE_FRONTEND_PORT``
env vars this module sets, so nothing is written into config files.

This module has no Docker/Postgres dependencies and is safe to import anywhere.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import subprocess
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterator

from loguru import logger


# Slot N maps to API_PORT_BASE + N and FRONTEND_PORT_BASE + N, so slot 0 has
# the default single-dev ports (8090/5173). Allocation skips a slot whose ports
# something already listens on, a bare `mascope dev run` or any other service,
# so the first instance on a shared box may get slot 1 or higher.
API_PORT_BASE = 8090
FRONTEND_PORT_BASE = 5173
# Ceiling on concurrent instances. A guard against runaway allocation, not a
# capacity promise: a shared Postgres has a finite connection budget
# (max_connections defaults to 100), so a dozen busy stacks may need tuning.
MAX_SLOTS = 16


class InstanceError(RuntimeError):
    """Raised when an instance cannot be resolved or allocated."""


@dataclass(frozen=True)
class Instance:
    """A worktree's slot binding and the ports/env derived from it."""

    worktree: str
    slot: int
    env: str
    api_port: int
    frontend_port: int


# --- paths ---


def _home(home: Path | str | None) -> Path:
    """Resolve the shared runtime home (``MASCOPE_PATH`` by default)."""
    if home is not None:
        return Path(home)
    path = os.environ.get("MASCOPE_PATH")
    if not path:
        raise InstanceError(
            "MASCOPE_PATH is not set; cannot locate the instance registry"
        )
    return Path(path)


def _registry_path(home: Path) -> Path:
    return home / ".runtime" / "instances.json"


def _lock_path(home: Path) -> Path:
    return home / ".runtime" / "instances.json.lock"


# --- locking ---


@contextmanager
def _locked(lock_path: Path, timeout: float = 10.0) -> Iterator[None]:
    """
    Cross-platform advisory lock via atomic exclusive file creation.

    Guards the read-modify-write of the registry when several worktrees
    first-run at once. Stale locks (older than 60s, e.g. from a killed
    process) are broken so a crash cannot wedge allocation permanently.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    fd: int | None = None
    while fd is None:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                age = time.time() - lock_path.stat().st_mtime
            except FileNotFoundError:
                continue  # released between the open and the stat; retry
            if age > 60:
                lock_path.unlink(missing_ok=True)
                continue
            if time.monotonic() - start > timeout:
                raise InstanceError(
                    f"Could not acquire instance lock {lock_path} within {timeout:.0f}s"
                )
            time.sleep(0.05)
    try:
        yield
    finally:
        os.close(fd)
        lock_path.unlink(missing_ok=True)


# --- registry io ---


def _read_registry(home: Path) -> dict:
    path = _registry_path(home)
    if not path.exists():
        return {"instances": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as error:
        # Do not reset the registry silently: treating it as empty can
        # reallocate slots (envs, databases, ports) already bound to other
        # worktrees. Warn so the corruption gets investigated.
        logger.warning(
            f"Instance registry {path} is unreadable ({error}); treating as empty"
        )
        return {"instances": {}}
    data.setdefault("instances", {})
    return data


def _write_registry(home: Path, registry: dict) -> None:
    path = _registry_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / (path.name + ".tmp")
    tmp.write_text(json.dumps(registry, indent=2), encoding="utf-8")
    tmp.replace(path)  # atomic on POSIX and Windows


def _record(inst: Instance) -> dict:
    rec = asdict(inst)
    rec.pop("worktree")  # the worktree path is the registry key, not stored twice
    return rec


# --- identity + naming ---


def worktree_key(cwd: str | None = None) -> str:
    """
    Stable identity for the current checkout: its git worktree root.

    Falls back to the resolved working directory when not in a git repo
    (or git is unavailable), so the feature still works outside git.
    """
    cwd = cwd or os.getcwd()
    try:
        result = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return str(Path(result.stdout.strip()).resolve())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        # git is not installed, or the call hung past the timeout: intentionally
        # fall through to the working-directory fallback below.
        pass
    return str(Path(cwd).resolve())


def _slug(text: str) -> str:
    """Lowercase, ``validate_env``-safe slug (``[A-Za-z0-9_-]+``)."""
    slug = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return slug or "wt"


def _env_name_for(worktree: str, taken: set[str]) -> str:
    """Derive ``wt-<basename>``, disambiguating clashes with a path hash."""
    base = f"wt-{_slug(Path(worktree).name)}"
    if base not in taken:
        return base
    suffix = hashlib.sha1(worktree.encode("utf-8")).hexdigest()[:6]
    return f"{base}-{suffix}"


def _lowest_free_slot(used: set[int]) -> int:
    slot = 0
    while slot in used:
        slot += 1
    return slot


# --- port probe ---

_PROBE_HOSTS = ("127.0.0.1", "::1")


def _port_in_use(port: int, timeout: float = 0.2) -> bool:
    """
    Whether something on this host accepts connections on ``port``.

    Connects over loopback, IPv4 and IPv6, rather than trying to bind: a
    listener on 127.0.0.1 alone or on a wildcard address answers either way,
    Vite's ``localhost`` may be ``::1`` only, and a connect means the same on
    POSIX and Windows, where bind semantics differ.
    """
    for host in _PROBE_HOSTS:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            continue  # refused, unreachable, or no IPv6 on this host
    return False


def _busy_ports(slot: int, port_in_use: Callable[[int], bool]) -> list[int]:
    """The slot's API and frontend ports that are already taken on the host."""
    ports = (API_PORT_BASE + slot, FRONTEND_PORT_BASE + slot)
    return [port for port in ports if port_in_use(port)]


# --- public api ---


def resolve_or_allocate(
    worktree: str | None = None,
    home: Path | str | None = None,
    port_in_use: Callable[[int], bool] | None = None,
) -> Instance:
    """
    Return the current worktree's instance, allocating one on first call.

    Idempotent: the same worktree always resolves to the same slot/env/ports,
    without probing them. Allocation takes the lowest slot that is free both in
    the registry and on the host (neither of its ports is listened on), derives
    the env name and ports from it, then persists the binding under the
    registry lock. Slots skipped for their ports are not recorded.

    :param port_in_use: Port probe, ``_port_in_use`` by default; tests pass
        their own so they need no real listener.
    :raises InstanceError: When every slot below ``MAX_SLOTS`` is taken in the
        registry or on the host.
    """
    home = _home(home)
    key = worktree or worktree_key()
    with _locked(_lock_path(home)):
        registry = _read_registry(home)
        instances = registry["instances"]
        if key in instances:
            return Instance(worktree=key, **instances[key])

        probe = port_in_use or _port_in_use
        used_slots = {rec["slot"] for rec in instances.values()}
        skipped: list[int] = []
        slot = _lowest_free_slot(used_slots)
        while slot < MAX_SLOTS:
            busy = _busy_ports(slot, probe)
            if not busy:
                break
            logger.info(
                f"Skipping instance slot {slot}: port(s) "
                f"{', '.join(map(str, busy))} already in use on this host"
            )
            skipped.append(slot)
            slot = _lowest_free_slot(used_slots | set(skipped))
        if slot >= MAX_SLOTS:
            message = f"No free instance slots (max {MAX_SLOTS})."
            if skipped:
                message += (
                    f" Slot(s) {', '.join(map(str, skipped))} were skipped "
                    "because their ports are in use by another process."
                )
            raise InstanceError(
                f"{message} Release one with `mascope instance rm <env>`."
            )
        env = _env_name_for(key, {rec["env"] for rec in instances.values()})
        inst = Instance(
            worktree=key,
            slot=slot,
            env=env,
            api_port=API_PORT_BASE + slot,
            frontend_port=FRONTEND_PORT_BASE + slot,
        )
        instances[key] = _record(inst)
        _write_registry(home, registry)
        return inst


def provision(inst: Instance, home: Path | str | None = None) -> None:
    """
    Ensure the instance's env directory is present and runnable.

    Creates the standard runnable-env subdirectory layout (mirroring the
    built-in ``default`` env). These must exist before the app starts: the
    backend's startup filestore GC does ``os.listdir`` on ``filestore`` and the
    file-converter reads ``filestreams``, so a fresh instance env would
    otherwise crash on first ``mascope dev run``. The env dir also marks the
    env as valid for ``mascope dev db`` / ``env`` commands; the
    ``mascope_<env>`` database is created lazily by ``mascope dev run``.
    """
    home = _home(home)
    env_dir = home / ".runtime" / "env" / inst.env
    for sub in ("filestore", "filestreams", "logs", "temp", "agents/file"):
        (env_dir / sub).mkdir(parents=True, exist_ok=True)


def list_instances(home: Path | str | None = None) -> list[Instance]:
    """All allocated instances, ordered by slot."""
    registry = _read_registry(_home(home))
    instances = [
        Instance(worktree=key, **rec) for key, rec in registry["instances"].items()
    ]
    return sorted(instances, key=lambda i: i.slot)


def release(env: str, home: Path | str | None = None) -> Instance | None:
    """
    Free the slot bound to ``env``. Returns the released instance, or ``None``
    if no instance used that env. Does not touch the database or filestore.
    """
    home = _home(home)
    with _locked(_lock_path(home)):
        registry = _read_registry(home)
        for key, rec in list(registry["instances"].items()):
            if rec["env"] == env:
                del registry["instances"][key]
                _write_registry(home, registry)
                return Instance(worktree=key, **rec)
    return None
