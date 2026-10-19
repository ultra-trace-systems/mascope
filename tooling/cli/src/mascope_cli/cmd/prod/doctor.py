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
import os
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
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
#
# Besides the free space now, the doctor reports how fast each filesystem has
# been filling and when that pace would empty it. It does not keep a history
# of its own: tooling/disk-check.sh records one sample per filesystem every 15
# minutes, and the doctor reads that file and fits the same rates, so the two
# monitors agree on the pace.

_GIB = 1024**3

# Where tooling/disk-check.sh keeps its free-space history, under MASCOPE_PATH.
# One tab-separated line per filesystem per run: epoch seconds, available
# bytes, filesystem size in bytes, mount point.
DISK_HISTORY = Path(".runtime") / "disk-check" / "history.tsv"

# The rate windows in days, and the least history a rate needs before it is
# reported - both as disk-check.sh has them.
RATE_WINDOWS_DAYS = (7, 30)
MIN_HISTORY_DAYS = 1.0

# Flag a filesystem whose fill rate would take it below the free-space floor
# within this many days. Overridable via MASCOPE_DOCTOR_MIN_DAYS_TO_FLOOR; 0
# disables. Shorter than disk-check's 30-day warning on purpose: that timer is
# the early tier, and the doctor's Kuma monitor going down should mean act now.
DEFAULT_MIN_DAYS_TO_FLOOR = 7.0

# Exit code of `mascope prod doctor` when the stack is healthy but a filesystem
# is projected below its floor within the horizon. Not 1: the fleet playbooks
# gate on the doctor, and a disk that will be full next week is no reason to
# stop rolling a release out to the servers after it. Not 2 either, which is a
# usage error and what doctor-push.sh reads as "no doctor in this CLI".
EXIT_FILLING = 3


def min_days_to_floor() -> float:
    """Projection horizon for the doctor's disk flag (env-overridable)."""
    return auto_update.env_float(
        "MASCOPE_DOCTOR_MIN_DAYS_TO_FLOOR", DEFAULT_MIN_DAYS_TO_FLOOR
    )


@dataclass
class FillRate:
    """How fast a filesystem's used space grew over one window."""

    window_days: int
    # History the window actually holds; less than window_days while the
    # history is younger than the window.
    span_days: float
    # Positive while filling, negative while space is being freed.
    gb_per_day: float


@dataclass
class DiskUsage:
    """Free space on the filesystem holding a monitored path."""

    label: str
    path: str
    free_gb: Optional[float]
    free_pct: Optional[float]
    low: bool
    # The filesystem's mount point, as df and the disk-check history name it.
    mount: Optional[str] = None
    # Fill rates over the 7- and 30-day windows; empty until the disk-check
    # history holds a day of samples for this filesystem.
    rates: list[FillRate] = field(default_factory=list)
    # At the faster rate: days until below the floor and until full. None when
    # there is no rate or the filesystem is not filling.
    days_to_floor: Optional[float] = None
    days_to_full: Optional[float] = None
    # Projected below the floor within the horizon (min_days_to_floor).
    filling: bool = False


def read_disk_history(history: Path, mount: str) -> list[tuple[float, float]]:
    """
    ``(epoch seconds, used GiB)`` samples of one mount from the disk-check
    history. Used is size minus available, as disk-check.sh fits it, so a
    filesystem that was grown does not read as one that freed space. A missing
    or unreadable history is no samples; malformed lines are skipped.
    """
    try:
        text = history.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    samples = []
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) < 4 or parts[3] != mount:
            continue
        if not all(p.isascii() and p.isdigit() for p in parts[:3]):
            continue
        t, avail, size = (int(p) for p in parts[:3])
        samples.append((float(t), (size - avail) / _GIB))
    return samples


def fill_rates(
    samples: list[tuple[float, float]], now: float, used_gb: float
) -> list[FillRate]:
    """
    Least-squares growth of used space over each rate window.

    ``samples`` are ``(epoch seconds, used GiB)``; the current reading
    ``used_gb`` at ``now`` is added to them. Mirrors ``fill_trend`` in
    tooling/disk-check.sh: a slope over every sample in the window rather than
    a first-to-last difference, so one transient reading is not projected
    forward; a window is skipped until its history spans MIN_HISTORY_DAYS, and
    the 30-day window until it reaches a day past the 7-day one.
    """
    points = [((t - now) / 86400, used) for t, used in samples if t <= now]
    points.append((0.0, used_gb))
    rates = []
    for window in RATE_WINDOWS_DAYS:
        inside = [(t, u) for t, u in points if t >= -window]
        span = -min(t for t, _ in inside)
        if span < MIN_HISTORY_DAYS:
            continue
        if window != RATE_WINDOWS_DAYS[0] and span <= RATE_WINDOWS_DAYS[0] + 1:
            continue
        n = len(inside)
        st = sum(t for t, _ in inside)
        su = sum(u for _, u in inside)
        stt = sum(t * t for t, _ in inside)
        stu = sum(t * u for t, u in inside)
        denominator = n * stt - st * st
        if denominator <= 0:
            continue
        rates.append(FillRate(window, span, (n * stu - st * su) / denominator))
    return rates


def project(
    free_gb: float, floor_gb: float, rates: list[FillRate]
) -> tuple[Optional[float], Optional[float]]:
    """
    Days until below ``floor_gb`` and until full, at the faster of ``rates``.

    The faster rate is the cautious one: the 7-day rate catches an instrument
    that has just started a long run, the 30-day rate keeps a quiet week from
    hiding a disk that fills in bursts. ``(None, None)`` when there is no rate
    or the filesystem is not filling.
    """
    if not rates:
        return None, None
    rate = max(r.gb_per_day for r in rates)
    if rate <= 0:
        return None, None
    return max(free_gb - floor_gb, 0.0) / rate, free_gb / rate


def _mount_point(path: Path) -> str:
    """
    The mount point holding an existing ``path``, as disk-check.sh records it.

    That is column 6 of ``df -P``, asked of ``df`` itself: the history is keyed
    by that name, and an ``os.path.ismount`` walk names some mounts
    differently - a bind mount shares its parent's device, so the walk passes
    it by. The walk is only the fallback where ``df`` cannot answer.
    """
    try:
        result = _run(["df", "-P", str(path)])
    except (OSError, subprocess.SubprocessError):
        result = None
    if result is not None and result.returncode == 0:
        lines = result.stdout.splitlines()
        fields = lines[1].split() if len(lines) >= 2 else []
        if len(fields) >= 6:
            return fields[5]
    probe = Path(os.path.realpath(path))
    while not os.path.ismount(probe) and probe != probe.parent:
        probe = probe.parent
    return str(probe)


def disk_usage(
    label: str,
    path: Path,
    min_free_gb: float,
    *,
    history: Optional[Path] = None,
    min_days: float = 0.0,
    now: Optional[float] = None,
) -> DiskUsage:
    """
    Free space on ``path``'s filesystem, flagged ``low`` below ``min_free_gb``.

    With a disk-check ``history``, also its fill rates and projection, flagged
    ``filling`` when the floor is projected within ``min_days`` days.
    """
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        usage = shutil.disk_usage(probe)
    except OSError:
        return DiskUsage(label, str(path), None, None, False)
    free_gb = usage.free / _GIB
    free_pct = usage.free * 100 / usage.total if usage.total else 0.0
    disk = DiskUsage(
        label,
        str(path),
        free_gb,
        free_pct,
        free_gb < min_free_gb,
        mount=_mount_point(probe),
    )
    if history is None:
        return disk

    now = time.time() if now is None else now
    samples = read_disk_history(history, disk.mount)
    disk.rates = fill_rates(samples, now, (usage.total - usage.free) / _GIB)
    disk.days_to_floor, disk.days_to_full = project(free_gb, min_free_gb, disk.rates)
    disk.filling = (
        not disk.low
        and min_days > 0
        and disk.days_to_floor is not None
        and disk.days_to_floor < min_days
    )
    return disk


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
        (the remaining sections are info-only, and a filesystem that is only
        projected to reach its floor is a warning - see ``filling``).
        """
        return (
            all(c.ok for c in self.containers)
            and not self.versions.drift
            and not any(d.low for d in self.disks)
        )

    @property
    def filling(self) -> bool:
        """A filesystem is projected below its floor within the horizon."""
        return any(d.filling for d in self.disks)

    @property
    def exit_code(self) -> int:
        """0 healthy, 1 unhealthy, EXIT_FILLING healthy but a disk is filling."""
        if not self.ok:
            return 1
        return EXIT_FILLING if self.filling else 0

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "filling": self.filling,
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
    disk_history: Optional[Path] = None,
    min_days_to_floor: float = 0.0,
    now: Optional[datetime.datetime] = None,
) -> Report:
    """Assemble the report from injected container names and paths."""
    stamp = now.timestamp() if now else None
    disks = [
        disk_usage(
            label,
            path,
            min_free_gb,
            history=disk_history,
            min_days=min_days_to_floor,
            now=stamp,
        )
        for label, path in disk_specs
    ]
    return Report(
        containers=[container_health(label, name) for label, name in container_specs],
        versions=version_status(expected_version, versioned_specs),
        disks=disks,
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


def _days_word(days: float) -> str:
    """A projection in days, worded as disk-check.sh words it."""
    if days < 1:
        return "<1 day"
    if days < 1.5:
        return "~1 day"
    if days > 3650:
        return ">10 years"
    return f"~{days:.0f} days"


def _disk_line(d: DiskUsage, label: str) -> str:
    """One filesystem: free space, then its fill rates and projection."""
    if d.free_gb is None:
        return f"{label} n/a"
    parts = [f"{label} {d.free_gb:.0f} GiB / {d.free_pct:.0f}% free"]
    if d.rates:
        parts.append(
            ", ".join(
                f"{r.gb_per_day:+.1f} GiB/day ({r.span_days:.0f}d)" for r in d.rates
            )
        )
        if d.days_to_full is None:
            parts.append("not filling")
        else:
            parts.append(f"full in {_days_word(d.days_to_full)}")
    flag = "  LOW" if d.low else "  FILLING" if d.filling else ""
    return " · ".join(parts) + flag


def format_text(report: Report) -> str:
    """Render the report as an aligned, human-readable block."""
    lines = []

    stack = " · ".join(f"{c.label} {_container_word(c)}" for c in report.containers)
    lines.append(("Stack", stack))

    lines.append(("Version", _version_line(report.versions)))

    # One row per filesystem, naming every monitored path on it: the state,
    # the filestore and docker usually share one, and three identical rows
    # would bury the one that differs.
    rows: dict = {}
    for d in report.disks:
        key = d.mount if d.mount is not None and d.free_gb is not None else id(d)
        rows.setdefault(key, []).append(d)
    for i, same in enumerate(rows.values()):
        label = ", ".join(d.label for d in same)
        lines.append(("Disk" if i == 0 else "", _disk_line(same[0], label)))

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

    header = {0: "OK", 1: "ATTENTION", EXIT_FILLING: "WARNING"}[report.exit_code]
    body = "\n".join(f"{label.ljust(9)}{value}" for label, value in lines)
    return f"[{header}]\n{body}"
