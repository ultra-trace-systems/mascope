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
import shlex
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from mascope_cli.cmd import init as init_cmd
from mascope_cli.cmd.prod import auto_update
from mascope_cli.cmd.prod import units as prod_units


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

    # The tag `mascope prod up` would select (pin, release tag at HEAD, the
    # release a wheel install recorded, latest)
    expected: Optional[str]
    running: list[RunningImage]

    @property
    def drift(self) -> bool:
        """
        Whether a running container is on a different tag than the deployment
        would deploy.

        This is what a silent channel switch looks like from the outside: the
        deployment says one release (its checkout, or the release a wheel
        install recorded), the containers run another, and the next
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
# the early tier, and the doctor's disk monitor going down should mean act now.
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
    # When the unattended apply of the pending release failed: --auto holds it.
    pending_failed_at: Optional[str] = None
    pending_kind: Optional[str] = None


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
        pending_failed_at=pending.failed_at if pending else None,
        pending_kind=pending.kind if pending else None,
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


# --- Units ---

# The units that run a script of their own rather than the mascope executable.
SCRIPT_UNITS = ("mascope-disk-check.service", "mascope-update.service")


@dataclass
class UnitScript:
    """The script one installed unit runs, and whether it is still there."""

    unit: str
    # The script its ExecStart runs, or None when the unit names none.
    script: Optional[str]
    # in-place / venv / missing / absent (the unit is not installed).
    # venv: inside a Python environment, as in a hand-written or pre-release
    # unit that runs the copy bundled in the CLI's venv, which a reinstall of
    # the CLI or another Python minor moves. `units install` never writes
    # that; the unit still runs until the path moves, and re-running
    # `units install` points it at the copies in /usr/local/lib/mascope.
    state: str

    @property
    def ok(self) -> bool:
        """The unit can run its script, or is not installed at all."""
        return self.state != "missing"


def unit_script(unit_file: Path) -> UnitScript:
    """
    The script a unit's ``ExecStart`` runs: the argument after ``/bin/bash``,
    as `units install` writes it, else the command itself.
    """
    try:
        text = unit_file.read_text(encoding="utf-8")
    except OSError:
        return UnitScript(unit_file.name, None, "absent")
    script = None
    for line in text.splitlines():
        key, sep, value = line.strip().partition("=")
        if not sep or key.strip() != "ExecStart":
            continue
        try:
            argv = shlex.split(value)
        except ValueError:
            argv = value.split()
        if len(argv) > 1 and Path(argv[0]).name == "bash":
            script = argv[1]
        elif argv:
            script = argv[0]
    if script is None:
        return UnitScript(unit_file.name, None, "missing")
    path = Path(script)
    if not path.is_file():
        state = "missing"
    elif "site-packages" in path.parts:
        state = "venv"
    else:
        state = "in-place"
    return UnitScript(unit_file.name, script, state)


def unit_scripts(systemd_dir: Optional[Path] = None) -> list[UnitScript]:
    """The script each of :data:`SCRIPT_UNITS` runs, as installed."""
    directory = systemd_dir or prod_units.SYSTEMD_DIR
    return [unit_script(directory / name) for name in SCRIPT_UNITS]


def _installed_values(unit_file: Path) -> Optional[tuple[str, str]]:
    """
    The deploy account and the mascope executable an installed
    ``mascope.service`` was written with: its ``User=`` and the command of its
    ``ExecStart``. None when the unit is not installed or names neither.
    """
    try:
        text = unit_file.read_text(encoding="utf-8")
    except OSError:
        return None
    user = executable = None
    for line in text.splitlines():
        key, sep, value = line.strip().partition("=")
        if not sep:
            continue
        if key.strip() == "User":
            user = value.strip()
        elif key.strip() == "ExecStart":
            try:
                argv = shlex.split(value)
            except ValueError:
                argv = value.split()
            executable = argv[0] if argv else None
    if not user or not executable:
        return None
    return user, executable


def _differs(installed: Path, expected: bytes) -> bool:
    """
    Whether an installed file is not ``expected``: a missing one is, one that
    cannot be read is not (nothing can be said about it).
    """
    try:
        return installed.read_bytes() != expected
    except FileNotFoundError:
        return True
    except OSError:
        return False


def outdated_units(mascope_path: str, systemd_dir: Optional[Path] = None) -> list[str]:
    """
    The installed units and copied scripts that differ from what
    `mascope prod units install` would write now, as paths; empty when the
    units are not installed.

    An update moves the deployment to a newer release but leaves the units and
    the copies in /usr/local/lib/mascope as the release that installed them
    wrote them: they are root's, and the update runs without sudo. So the
    expected content is rendered from this release's templates (the
    checkout's, else the CLI's bundled ones), with the deploy account and the
    executable the installed ``mascope.service`` names, so that only a change
    of release reads as a difference. A unit or a copy this release adds and
    the host lacks is one too.
    """
    directory = systemd_dir or prod_units.SYSTEMD_DIR
    values = _installed_values(directory / "mascope.service")
    if values is None:
        return []
    user, executable = values
    home = Path(mascope_path).absolute()
    sources = prod_units.sources_for(home)
    try:
        rendered = prod_units.render(
            sources, user=user, mascope_bin=executable, home=home
        )
    except (OSError, ValueError):
        return []
    outdated = [
        str(directory / name)
        for name, text in rendered.items()
        if _differs(directory / name, text.encode("utf-8"))
    ]
    for name, target in prod_units.copied_scripts(sources).items():
        try:
            expected = sources.files[name].read_bytes()
        except OSError:
            continue
        if _differs(target, expected):
            outdated.append(str(target))
    return outdated


# --- Secrets ---


@dataclass
class UnreadableSecret:
    """A secret file the backend image cannot read, and who owns it."""

    path: str
    owner_uid: int


def unreadable_secrets(mascope_path: str) -> list[UnreadableSecret]:
    """
    The home's secrets the published images, running as ``IMAGE_UID``, cannot
    read: those of a home made by another user, whose stack stops in db_init.
    """
    secrets_dir = Path(mascope_path) / ".runtime" / "secrets"
    return [
        UnreadableSecret(str(secrets_dir / name), owner)
        for name, owner in init_cmd.secrets_unreadable_by_image(secrets_dir)
    ]


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
    units: list[UnitScript] = field(default_factory=list)
    # Installed units and copied scripts this release would write otherwise.
    units_outdated: list[str] = field(default_factory=list)
    # Secrets the published images could not read. Flagged without failing
    # the report: an image built with its own MASCOPE_UID reads them as that.
    secrets_unreadable: list[UnreadableSecret] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """
        Healthy stack, no version drift, no filesystem below its floor, and
        no installed unit without its script (the remaining sections are
        info-only, a unit whose script is inside a venv is flagged but still
        runs, so does a unit or a copied script an older release wrote, so
        do secrets the published images could not read, and a filesystem
        that is only projected to reach its floor is a warning - see
        ``filling``).
        """
        return (
            all(c.ok for c in self.containers)
            and not self.versions.drift
            and not any(d.low for d in self.disks)
            and all(u.ok for u in self.units)
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
            "units": [asdict(u) for u in self.units],
            "units_outdated": list(self.units_outdated),
            "secrets_unreadable": [asdict(s) for s in self.secrets_unreadable],
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
    systemd_dir: Optional[Path] = None,
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
        units=unit_scripts(systemd_dir),
        units_outdated=outdated_units(mascope_path, systemd_dir),
        secrets_unreadable=unreadable_secrets(mascope_path),
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
    return f"{running}  DRIFT (this deployment deploys {expected})"


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


def _units_line(units: list[UnitScript], outdated: list[str]) -> str:
    """
    The units' scripts: in place, or which unit lost or may lose its own, and
    which installed files an older release wrote.
    """
    installed = [u for u in units if u.state != "absent"]
    if not installed and not outdated:
        return "not installed"
    flagged = [u for u in installed if u.state in ("venv", "missing")]
    if not flagged and not outdated:
        return "scripts in place"
    parts = []
    for u in flagged:
        if u.state == "missing":
            parts.append(f"{u.unit} cannot find {u.script or 'its script'}  MISSING")
        else:
            parts.append(f"{u.unit} runs {u.script} inside a venv  STALE")
    if outdated:
        names = ", ".join(Path(p).name for p in outdated)
        parts.append(f"{names} not as this release writes them  OUTDATED")
    return " · ".join(parts) + " · re-run 'mascope prod units install'"


def _secrets_line(unreadable: list[UnreadableSecret]) -> str:
    """The secrets: readable by the images, or which are not and the fix."""
    if not unreadable:
        return f"readable by uid {init_cmd.IMAGE_UID}"
    uid = init_cmd.IMAGE_UID
    names = ", ".join(Path(s.path).name for s in unreadable)
    owners = ", ".join(sorted({str(s.owner_uid) for s in unreadable}))
    secrets_dir = Path(unreadable[0].path).parent
    return (
        f"{names} owned by uid {owners}, unreadable to uid {uid} the "
        f"published images run as  UNREADABLE · sudo chown {uid} "
        f"{secrets_dir}/* (an image built with another MASCOPE_UID reads "
        "them as that uid)"
    )


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
    if u.pending_version and u.pending_failed_at:
        lines.append(
            (
                "Updates",
                f"{u.pending_kind} update {u.pending_version} failed to apply at "
                f"{u.pending_failed_at}; held - confirm to retry",
            )
        )
    elif u.pending_version:
        lines.append(
            (
                "Updates",
                f"{u.pending_kind or 'migration'} update {u.pending_version} "
                f"pending (first seen {u.pending_first_seen})",
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

    lines.append(("Units", _units_line(report.units, report.units_outdated)))
    lines.append(("Secrets", _secrets_line(report.secrets_unreadable)))

    header = {0: "OK", 1: "ATTENTION", EXIT_FILLING: "WARNING"}[report.exit_code]
    body = "\n".join(f"{label.ljust(9)}{value}" for label, value in lines)
    return f"[{header}]\n{body}"
