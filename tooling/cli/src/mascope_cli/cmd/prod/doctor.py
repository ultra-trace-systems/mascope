"""
Data gathering for `mascope prod doctor` - one-glance operational status.

Collects cheap, local signals - container state, free disk, the recorded
pending update, backup freshness, and the docker image footprint - into a
structured report. Read-only and network-free: it never pulls, never changes
anything, and is safe to run anytime or to poll from a monitor. All docker
interaction goes through the small ``_run`` seam so tests can stub it.

The command layer (``main.doctor``) resolves container names and paths from
config and passes them in; everything here takes its inputs explicitly so it is
straightforward to test without a live stack.
"""

import datetime
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from mascope_cli.cmd.prod import auto_update


def _run(cmd: list[str], timeout: Optional[int] = 15) -> subprocess.CompletedProcess:
    """Run a command capturing output; never raises on non-zero exit."""
    return subprocess.run(
        cmd, capture_output=True, text=True, check=False, timeout=timeout
    )


# --- Containers ---


@dataclass
class ContainerHealth:
    """State of one stack container."""

    label: str
    name: str
    # running / exited / restarting / absent / unknown
    state: str
    # healthy / unhealthy / starting, or None when the container has no healthcheck
    health: Optional[str]

    @property
    def ok(self) -> bool:
        """Running, and healthy if it defines a healthcheck."""
        if self.state != "running":
            return False
        return self.health in (None, "healthy")


def container_health(label: str, name: str) -> ContainerHealth:
    """Inspect one container's run state and (optional) health status."""
    fmt = (
        "{{.State.Status}}|"
        "{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}"
    )
    result = _run(["docker", "inspect", "--format", fmt, name])
    if result.returncode != 0:
        return ContainerHealth(label, name, "absent", None)
    status, _, health = result.stdout.strip().partition("|")
    return ContainerHealth(
        label,
        name,
        status or "unknown",
        None if health in ("none", "") else health,
    )


# --- Version ---


@dataclass
class RunningImage:
    """The image tag one container was actually created from."""

    label: str
    # None when the container is absent, or pinned by digest rather than tag
    tag: Optional[str]


@dataclass
class VersionStatus:
    """What the deployment would deploy now versus what it is running."""

    # The tag `mascope prod up` would select (pin, release tag at HEAD, latest)
    expected: Optional[str]
    running: list[RunningImage]

    @property
    def drift(self) -> bool:
        """
        Whether a running container is on a different tag than the deployment
        would deploy.

        This is what a silent channel switch looks like from the outside: the
        checkout says one release, the containers run another, and the next
        `up` (a reboot, a restart) moves the stack without anyone asking.
        """
        if not self.expected:
            return False
        return any(r.tag is not None and r.tag != self.expected for r in self.running)


def _image_tag(reference: str) -> Optional[str]:
    """
    Tag part of a docker image reference, or None when there is none.

    The registry host may carry a port (`host:5000/repo:tag`), so only the last
    path segment is inspected; a digest-pinned reference has no tag at all.
    """
    name = reference.rsplit("/", 1)[-1]
    if "@" in name:
        return None
    _, sep, tag = name.partition(":")
    return tag if sep else None


def running_image(label: str, name: str) -> RunningImage:
    """Read the image reference one container was created from."""
    result = _run(["docker", "inspect", "--format", "{{.Config.Image}}", name])
    if result.returncode != 0:
        return RunningImage(label, None)
    return RunningImage(label, _image_tag(result.stdout.strip()))


def version_status(
    expected: Optional[str], specs: list[tuple[str, str]]
) -> VersionStatus:
    """Compare the deploy tag against the tags the given containers run."""
    running = [running_image(label, name) for label, name in specs]
    return VersionStatus(expected=expected, running=running)


# --- Disk ---


@dataclass
class DiskUsage:
    """Free space on the filesystem holding a monitored path."""

    label: str
    path: str
    free_gb: Optional[float]
    free_pct: Optional[float]
    low: bool


def disk_usage(label: str, path: Path, min_free_gb: float) -> DiskUsage:
    """Free space on ``path``'s filesystem, flagged ``low`` below ``min_free_gb``."""
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        usage = shutil.disk_usage(probe)
    except OSError:
        return DiskUsage(label, str(path), None, None, False)
    free_gb = usage.free / (1024**3)
    free_pct = usage.free * 100 / usage.total if usage.total else 0.0
    return DiskUsage(label, str(path), free_gb, free_pct, free_gb < min_free_gb)


# --- Backups ---


@dataclass
class BackupStatus:
    """Local database-dump freshness."""

    count: int
    latest_age_hours: Optional[float]


def backup_status(
    backups_dir: Path, now: Optional[datetime.datetime] = None
) -> BackupStatus:
    """Count local dumps and the age of the newest, from mtimes."""
    now = now or datetime.datetime.now()
    if not backups_dir.exists():
        return BackupStatus(0, None)
    dumps = list(backups_dir.glob("*.dump"))
    if not dumps:
        return BackupStatus(0, None)
    newest = max(d.stat().st_mtime for d in dumps)
    return BackupStatus(len(dumps), (now.timestamp() - newest) / 3600)


# --- Updates ---


@dataclass
class UpdateStatus:
    """The recorded pending update and the last updater status line."""

    pending_version: Optional[str]
    pending_first_seen: Optional[str]
    last_status: Optional[str]


def update_status(mascope_path: str) -> UpdateStatus:
    """Read the pending-update state file and the tail of the status log."""
    pending = auto_update.load_pending(mascope_path)
    log_path = auto_update.update_dir(mascope_path) / "status.log"
    last = None
    if log_path.exists():
        try:
            lines = [
                ln
                for ln in log_path.read_text(encoding="utf-8").splitlines()
                if ln.strip()
            ]
            last = lines[-1] if lines else None
        except OSError:
            last = None
    return UpdateStatus(
        pending_version=pending.version if pending else None,
        pending_first_seen=pending.first_seen_at if pending else None,
        last_status=last,
    )


# --- Images ---


@dataclass
class ImageFootprint:
    """Docker image count and disk footprint (from ``docker system df``)."""

    count: Optional[int]
    size: Optional[str]
    reclaimable: Optional[str]


def image_footprint() -> ImageFootprint:
    """Parse the Images row of ``docker system df``."""
    result = _run(
        [
            "docker",
            "system",
            "df",
            "--format",
            "{{.Type}}|{{.TotalCount}}|{{.Size}}|{{.Reclaimable}}",
        ]
    )
    if result.returncode != 0:
        return ImageFootprint(None, None, None)
    for line in result.stdout.splitlines():
        parts = line.split("|")
        if len(parts) >= 4 and parts[0].strip() == "Images":
            try:
                count = int(parts[1])
            except ValueError:
                count = None
            return ImageFootprint(count, parts[2].strip(), parts[3].strip())
    return ImageFootprint(None, None, None)


# --- Report ---


@dataclass
class Report:
    """The full doctor report."""

    containers: list[ContainerHealth]
    versions: VersionStatus
    disks: list[DiskUsage]
    updates: UpdateStatus
    backups: BackupStatus
    images: ImageFootprint

    @property
    def ok(self) -> bool:
        """
        Healthy stack, no version drift, and no filesystem below its floor
        (the remaining sections are info-only).
        """
        return (
            all(c.ok for c in self.containers)
            and not self.versions.drift
            and not any(d.low for d in self.disks)
        )

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "containers": [asdict(c) for c in self.containers],
            # drift is derived, so asdict() does not carry it
            "versions": {**asdict(self.versions), "drift": self.versions.drift},
            "disks": [asdict(d) for d in self.disks],
            "updates": asdict(self.updates),
            "backups": asdict(self.backups),
            "images": asdict(self.images),
        }


def build_report(
    *,
    mascope_path: str,
    container_specs: list[tuple[str, str]],
    versioned_specs: list[tuple[str, str]],
    expected_version: Optional[str],
    disk_specs: list[tuple[str, Path]],
    backups_dir: Path,
    min_free_gb: float,
    now: Optional[datetime.datetime] = None,
) -> Report:
    """Assemble the report from injected container names and paths."""
    return Report(
        containers=[container_health(label, name) for label, name in container_specs],
        versions=version_status(expected_version, versioned_specs),
        disks=[disk_usage(label, path, min_free_gb) for label, path in disk_specs],
        updates=update_status(mascope_path),
        backups=backup_status(backups_dir, now=now),
        images=image_footprint(),
    )


# --- Text rendering ---


def _container_word(c: ContainerHealth) -> str:
    if c.state == "running":
        return c.health or "running"
    return c.state


def _version_line(v: VersionStatus) -> str:
    """One line on the deployed release, spelling out any drift."""
    expected = v.expected or "unknown"
    tagged = [r for r in v.running if r.tag is not None]
    if not tagged:
        return f"{expected} (no versioned container running)"
    if not v.drift:
        return expected
    running = " · ".join(f"{r.label} {r.tag}" for r in tagged)
    return f"{running}  DRIFT (this checkout deploys {expected})"


def _age_word(hours: Optional[float]) -> str:
    if hours is None:
        return "unknown"
    if hours < 1:
        return f"{int(hours * 60)}m ago"
    if hours < 48:
        return f"{int(hours)}h ago"
    return f"{int(hours / 24)}d ago"


def format_text(report: Report) -> str:
    """Render the report as an aligned, human-readable block."""
    lines = []

    stack = " · ".join(f"{c.label} {_container_word(c)}" for c in report.containers)
    lines.append(("Stack", stack))

    lines.append(("Version", _version_line(report.versions)))

    disk_bits = []
    for d in report.disks:
        if d.free_gb is None:
            disk_bits.append(f"{d.label} n/a")
        else:
            flag = "  LOW" if d.low else ""
            disk_bits.append(
                f"{d.label} {d.free_gb:.0f} GiB / {d.free_pct:.0f}% free{flag}"
            )
    lines.append(("Disk", "   ·   ".join(disk_bits)))

    u = report.updates
    if u.pending_version:
        lines.append(
            (
                "Updates",
                f"migration update {u.pending_version} pending "
                f"(first seen {u.pending_first_seen})",
            )
        )
    else:
        lines.append(("Updates", "no pending migration recorded"))

    b = report.backups
    if b.count == 0:
        lines.append(("Backups", "no local dumps found"))
    else:
        lines.append(
            (
                "Backups",
                f"{b.count} local dump(s) · newest {_age_word(b.latest_age_hours)}",
            )
        )

    im = report.images
    if im.count is None:
        lines.append(("Images", "(docker unavailable)"))
    else:
        lines.append(
            ("Images", f"{im.count} images · {im.size} ({im.reclaimable} reclaimable)")
        )

    header = "OK" if report.ok else "ATTENTION"
    body = "\n".join(f"{label.ljust(9)}{value}" for label, value in lines)
    return f"[{header}]\n{body}"
