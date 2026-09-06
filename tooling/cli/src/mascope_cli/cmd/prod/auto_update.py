"""
Automated update orchestration for `mascope prod update --auto`.

Runs unattended (e.g. from a systemd timer). It resolves the newest pinned
release, classifies it with the preflight, and acts by classification:

- up-to-date: nothing to do.
- fast update (new images, no migration): apply it inside the maintenance
  window, guarded by a post-apply health check. On failure it alerts and
  stops - it never rolls back automatically (an operator decides).
- migration update (downtime): do NOT apply unattended. Record it as a pending
  update and notify, so a human can schedule the window. Applying migration
  updates on confirmation or after a grace period is handled separately.

Finding the latest release and downloading its manifest use the public GitHub
REST API over plain HTTPS - no token and no ``gh`` needed, since the repository
and its release assets are public. A nightly timer stays well within the
anonymous rate limit. Both calls go through small seams that tests stub.
"""

import datetime
import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from loguru import logger

from mascope_cli.cmd.prod.release_manifest import MANIFEST_FILENAME


# Exit codes for the --auto run, chosen so a timer/monitor can distinguish
# "handled" from "needs a human" from "broken" without parsing output.
AUTO_OK = 0  # nothing to do, or a fast update applied cleanly
AUTO_MIGRATION_PENDING = 30  # a migration update was recorded + notified
AUTO_ERROR = 2  # discovery, apply, or health check failed


@dataclass
class PendingUpdate:
    """A migration update seen but not yet applied."""

    version: str
    alembic_head: str
    first_seen_at: str
    # Postpone auto-apply until this local ISO timestamp (set by a snooze).
    snooze_until: Optional[str] = None
    # Operator/customer confirmed: apply at the next window without waiting out
    # the grace period.
    confirmed: bool = False

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "alembic_head": self.alembic_head,
            "first_seen_at": self.first_seen_at,
            "snooze_until": self.snooze_until,
            "confirmed": self.confirmed,
        }


def _now() -> datetime.datetime:
    """Current local time (seam for tests)."""
    return datetime.datetime.now()


def update_dir(mascope_path: str) -> Path:
    """Directory holding the updater's state and status log."""
    return Path(mascope_path) / ".runtime" / "update"


# --- Maintenance window ---


def parse_window(spec: Optional[str]) -> Optional[tuple[int, int]]:
    """
    Parse a ``"HH-HH"`` local-hour maintenance window (e.g. ``"2-5"``).

    Returns None when ``spec`` is empty (meaning "no restriction - any time").

    :raises ValueError: if the spec is malformed or hours are out of range.
    """
    if not spec:
        return None
    try:
        start_s, end_s = spec.split("-", 1)
        start, end = int(start_s), int(end_s)
    except ValueError:
        # The re-raised message fully describes the parse failure; suppress the
        # implicit "During handling of the above exception" chaining noise.
        raise ValueError(
            f"Invalid window '{spec}' - expected 'HH-HH' (e.g. '2-5')"
        ) from None
    for h in (start, end):
        if not 0 <= h <= 23:
            raise ValueError(f"Window hour {h} out of range 0-23")
    return start, end


def in_window(now: datetime.datetime, window: Optional[tuple[int, int]]) -> bool:
    """
    Whether ``now`` falls in the window. A window that wraps midnight
    (start > end, e.g. 22-3) is handled. None means always allowed.
    """
    if window is None:
        return True
    start, end = window
    hour = now.hour
    if start <= end:
        return start <= hour < end
    # Wraps midnight: in-window if at/after start OR before end.
    return hour >= start or hour < end


# --- Pending-update state ---


def load_pending(mascope_path: str) -> Optional[PendingUpdate]:
    """Load the recorded pending update, or None if there is none."""
    path = update_dir(mascope_path) / "state.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8")).get("pending")
    except (OSError, json.JSONDecodeError) as error:
        # A corrupt state file must not crash the CLI, but silently forgetting
        # a pending migration update on a prod host would be worse - say so.
        logger.warning(f"Ignoring unreadable auto-update state {path}: {error}")
        return None
    if not data:
        return None
    return PendingUpdate(
        version=data["version"],
        alembic_head=data["alembic_head"],
        first_seen_at=data["first_seen_at"],
        snooze_until=data.get("snooze_until"),
        confirmed=data.get("confirmed", False),
    )


def save_pending(mascope_path: str, pending: PendingUpdate) -> None:
    """
    Persist the pending update, creating the update dir if needed.

    Written via a temp file and an atomic replace: `load_pending` treats an
    unparseable file as "nothing pending", so a reader catching this write
    half-done (a doctor cron overlapping an update) would silently forget a
    pending migration rather than report it.
    """
    d = update_dir(mascope_path)
    d.mkdir(parents=True, exist_ok=True)
    path = d / "state.json"
    tmp = path.parent / f".{path.name}.{os.getpid()}.tmp"
    try:
        tmp.write_text(
            json.dumps({"pending": pending.to_dict()}, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, path)  # atomic on POSIX and Windows
    finally:
        tmp.unlink(missing_ok=True)


def clear_pending(mascope_path: str) -> None:
    """Remove any recorded pending update."""
    path = update_dir(mascope_path) / "state.json"
    if path.exists():
        path.unlink()


def record_pending(mascope_path: str, version: str, alembic_head: str) -> PendingUpdate:
    """
    Record ``version`` as pending, preserving ``first_seen_at`` if the same
    version was already pending (so the grace clock is not reset each tick).
    """
    existing = load_pending(mascope_path)
    if existing is not None and existing.version == version:
        return existing
    pending = PendingUpdate(
        version=version,
        alembic_head=alembic_head,
        first_seen_at=_now().replace(microsecond=0).isoformat(),
    )
    save_pending(mascope_path, pending)
    return pending


def snooze_pending(mascope_path: str, days: int) -> Optional[PendingUpdate]:
    """
    Postpone auto-apply of the pending update by ``days``. Returns the updated
    record, or None if there is nothing pending.
    """
    pending = load_pending(mascope_path)
    if pending is None:
        return None
    pending.snooze_until = (
        (_now() + datetime.timedelta(days=days)).replace(microsecond=0).isoformat()
    )
    # Snoozing overrides a prior confirmation - the operator changed their mind.
    pending.confirmed = False
    save_pending(mascope_path, pending)
    return pending


def confirm_pending(mascope_path: str) -> Optional[PendingUpdate]:
    """
    Mark the pending update confirmed so it applies at the next window without
    waiting out the grace period. Returns the updated record, or None if there
    is nothing pending.
    """
    pending = load_pending(mascope_path)
    if pending is None:
        return None
    pending.confirmed = True
    pending.snooze_until = None
    save_pending(mascope_path, pending)
    return pending


def should_apply_migration(
    pending: PendingUpdate,
    now: datetime.datetime,
    grace_days: int,
    window: Optional[tuple[int, int]],
) -> bool:
    """
    Decide whether a pending migration update may be applied unattended now.

    True only when inside the maintenance window and not snoozed, and either
    the operator confirmed it or its grace period has elapsed since it was
    first seen.
    """
    if not in_window(now, window):
        return False
    if pending.snooze_until and now < datetime.datetime.fromisoformat(
        pending.snooze_until
    ):
        return False
    if pending.confirmed:
        return True
    grace_deadline = datetime.datetime.fromisoformat(
        pending.first_seen_at
    ) + datetime.timedelta(days=grace_days)
    return now >= grace_deadline


def record_status(mascope_path: str, message: str) -> None:
    """Append a timestamped line to the updater's status log."""
    d = update_dir(mascope_path)
    d.mkdir(parents=True, exist_ok=True)
    stamp = _now().replace(microsecond=0).isoformat()
    with (d / "status.log").open("a", encoding="utf-8") as fh:
        fh.write(f"{stamp} {message}\n")


# --- docker command runner (used by the health check) ---


def _run(cmd: list[str], timeout: Optional[int] = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, capture_output=True, text=True, check=False, timeout=timeout
    )


# --- Disk space guard ---
#
# An update that fills the disk while pulling image layers is worse than no
# update: a full filesystem can wedge Postgres and take the whole stack down.
# Before pulling, refuse when the filesystem backing the docker image store is
# below a floor of free space. This is a guard, not a cleanup - the operator
# frees space (old images are pruned automatically after a successful deploy).

# Minimum free GiB required before pulling update images. Overridable via
# MASCOPE_UPDATE_MIN_FREE_GB; the default clears a typical backend+frontend
# image pair with headroom.
DEFAULT_MIN_FREE_GB = 5.0


def min_free_gb() -> float:
    """Minimum free GiB required before an update may pull (env-overridable)."""
    raw = os.environ.get("MASCOPE_UPDATE_MIN_FREE_GB")
    if not raw:
        return DEFAULT_MIN_FREE_GB
    try:
        return float(raw)
    except ValueError:
        return DEFAULT_MIN_FREE_GB


def docker_root() -> Path:
    """
    Filesystem path where docker stores image layers (its data-root).

    Pulled layers land here, so this is the partition an update can fill. Read
    from ``docker info``; falls back to the stock ``/var/lib/docker`` when it
    cannot be determined (docker unreachable), which is where layers live on a
    default install anyway.
    """
    result = _run(["docker", "info", "--format", "{{.DockerRootDir}}"])
    root = result.stdout.strip() if result.returncode == 0 else ""
    return Path(root) if root else Path("/var/lib/docker")


def free_gb(path: Path) -> Optional[float]:
    """
    Free space in GiB on the filesystem holding ``path``.

    Walks up to the nearest existing ancestor so a data-root that does not exist
    yet still resolves to its mount point. Returns None if it cannot be measured
    (an unmeasurable disk must never block an update).
    """
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        return shutil.disk_usage(probe).free / (1024**3)
    except OSError as error:
        # The precheck is skipped when this returns None - leave a trace
        logger.warning(f"Could not measure free disk space at {probe}: {error}")
        return None


def disk_precheck() -> Optional[str]:
    """
    Guard run before pulling update images.

    Returns a human-readable error message when free space on the docker image
    store is below the configured minimum, or None when there is enough room (or
    it cannot be measured).
    """
    root = docker_root()
    free = free_gb(root)
    if free is None:
        return None
    threshold = min_free_gb()
    if free < threshold:
        return (
            f"Only {free:.1f} GiB free on {root} (docker image store); need at "
            f"least {threshold:.0f} GiB to pull update images safely. Free disk "
            "space and retry (MASCOPE_UPDATE_MIN_FREE_GB tunes the floor; see "
            "docs/maintaining.md)."
        )
    return None


# --- GitHub release discovery (public, tokenless HTTPS) ---

_GITHUB_API = "https://api.github.com"
# GitHub rejects API requests that send no User-Agent.
_HTTP_HEADERS = {"User-Agent": "mascope-cli", "Accept": "application/vnd.github+json"}


def _http_get_json(url: str, timeout: int = 30) -> dict:
    """GET ``url`` and parse the JSON body. Raises on transport/parse errors."""
    request = urllib.request.Request(url, headers=_HTTP_HEADERS)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def _http_download(url: str, dest: Path, timeout: int = 30) -> None:
    """Download ``url`` to ``dest``. Raises on transport errors."""
    request = urllib.request.Request(url, headers=_HTTP_HEADERS)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        with dest.open("wb") as fh:
            shutil.copyfileobj(response, fh)


def latest_release_tag(repo: str) -> Optional[str]:
    """
    Newest published release tag for ``repo`` (e.g. ``v1.4.0``), read from the
    public GitHub REST API without authentication.

    ``/releases/latest`` is the newest release that is neither a draft nor a
    **pre-release**, which is what keeps an unattended update from ever moving
    a deployment onto a ``vX.Y.Z-rc.N``: a pre-release is opt-in, reached only
    by pinning it or checking its tag out. Do not swap this for the releases
    *list* endpoint, whose first entry is simply the most recent release of any
    kind.

    Returns None if it cannot be determined (no releases, no network).
    """
    try:
        data = _http_get_json(f"{_GITHUB_API}/repos/{repo}/releases/latest")
    except (urllib.error.URLError, OSError, ValueError):
        return None
    tag = data.get("tag_name")
    return tag or None


def download_manifest(repo: str, tag: str, dest_dir: Path) -> Optional[Path]:
    """
    Download the release's manifest asset into ``dest_dir``. Returns the path,
    or None if the release has no manifest asset (releases predating the
    manifest) or it could not be fetched.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        release = _http_get_json(f"{_GITHUB_API}/repos/{repo}/releases/tags/{tag}")
    except (urllib.error.URLError, OSError, ValueError):
        return None

    asset_url = next(
        (
            asset.get("browser_download_url")
            for asset in release.get("assets", [])
            if asset.get("name") == MANIFEST_FILENAME
        ),
        None,
    )
    if not asset_url:
        return None

    path = dest_dir / MANIFEST_FILENAME
    try:
        _http_download(asset_url, path)
    except (urllib.error.URLError, OSError):
        return None
    return path if path.exists() else None


# --- Health check ---


def health_status(container: str) -> Optional[str]:
    """Docker health status of a container (healthy/starting/unhealthy), or None."""
    result = _run(
        ["docker", "inspect", "--format", "{{.State.Health.Status}}", container]
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def wait_healthy(container: str, timeout: int = 180, interval: int = 5) -> bool:
    """
    Poll ``container`` until it reports healthy or ``timeout`` seconds elapse.

    A container without a healthcheck (status None) is treated as not-healthy
    here; callers point this at the backend, which defines one.
    """
    deadline = _now() + datetime.timedelta(seconds=timeout)
    while _now() < deadline:
        if health_status(container) == "healthy":
            return True
        _sleep(interval)
    return health_status(container) == "healthy"


def _sleep(seconds: int) -> None:
    """Sleep seam (overridden in tests to avoid real waits)."""
    import time

    time.sleep(seconds)
