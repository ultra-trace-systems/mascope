"""
`mascope prod units`: install and remove a deployment's systemd units.

The units start the stack at boot (``mascope.service``), run the unattended
update (``mascope-update.*``, installed disabled), the disk check and the
peak-assignment retention pass (both enabled). They are templates with
``@@TOKEN@@`` placeholders, filled here from the runtime home, the running
``mascope`` executable and the deploy account, and written under
``/etc/systemd/system`` through ``sudo``. The env files beside them are seeded
into ``/etc/mascope`` once and never overwritten.

A checkout install takes the templates from its checkout (``tooling/systemd``,
``tooling/disk-check.sh``), which ``tooling/ubuntu.sh install`` relies on; a
wheel install takes the copies bundled in the CLI (``mascope_cli/data/systemd``),
which a test keeps identical to the checkout's.
"""

import getpass
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Annotated, Optional

import typer

from mascope_cli.runtime import runtime


units_app = typer.Typer()

SYSTEMD_DIR = Path("/etc/systemd/system")
CONFIG_DIR = Path("/etc/mascope")
ENVIRONMENT_FILE = Path("/etc/environment")

# Templated units, in the order they are written.
TEMPLATED_UNITS = (
    "mascope.service",
    "mascope-update.service",
    "mascope-disk-check.service",
    "mascope-assignment-prune.service",
)
# Units copied as they are.
PLAIN_UNITS = (
    "mascope-update.timer",
    "mascope-disk-check.timer",
    "mascope-assignment-prune.timer",
)
UNITS = TEMPLATED_UNITS + PLAIN_UNITS

# The order `remove` stops and disables them in: timers before the services
# they fire, the boot service last.
REMOVE_ORDER = (
    "mascope-disk-check.timer",
    "mascope-disk-check.service",
    "mascope-assignment-prune.timer",
    "mascope-assignment-prune.service",
    "mascope-update.timer",
    "mascope-update.service",
    "mascope.service",
)

# Seeded into /etc/mascope once (chmod 600, owned by the deploy account):
# installed name -> template name.
ENV_FILES = {
    "update.env": "update.env.example",
    "disk-check.env": "disk-check.env.example",
    "prune.env": "prune.env.example",
}

# What a checkout keeps where; the bundled copies are all in one directory.
_CHECKOUT_PATHS = {
    **{name: Path("tooling", "systemd", name) for name in UNITS},
    "mascope-update.sh": Path("tooling", "systemd", "mascope-update.sh"),
    "update.env.example": Path("tooling", "systemd", "update.env.example"),
    "prune.env.example": Path("tooling", "systemd", "prune.env.example"),
    "disk-check.sh": Path("tooling", "disk-check.sh"),
    "disk-check.env.example": Path("tooling", "disk-check.env.example"),
}
# Every file a deployment's units need, by its bundled name.
SOURCE_FILES = tuple(_CHECKOUT_PATHS)


@dataclass(frozen=True)
class Sources:
    """Where the unit templates and the scripts they run are read from."""

    files: dict[str, Path]
    origin: str  # "checkout" or "bundled", for messages

    def text(self, name: str) -> str:
        return self.files[name].read_text(encoding="utf-8")


@dataclass(frozen=True)
class Step:
    """One privileged action, printed under --dry-run and run otherwise."""

    argv: list[str]
    note: str
    # Written to the command's stdin (a file's content, for `sudo tee`).
    stdin: Optional[str] = None
    # A failure is expected and ignored (stopping a unit that is not loaded).
    check: bool = True


def bundled_dir() -> Path:
    """The directory of the unit templates bundled in the CLI."""
    return Path(str(resources.files("mascope_cli") / "data" / "systemd"))


def sources_for(home: Path) -> Sources:
    """
    The templates for the deployment at ``home``.

    A checkout's own, when ``home`` is one, so the units match its checked-out
    release and the disk check runs the checkout's script (which is where
    ``tooling/ubuntu.sh`` has always pointed it); otherwise the CLI's bundled
    copies.
    """
    in_checkout = {name: home / rel for name, rel in _CHECKOUT_PATHS.items()}
    if all(path.is_file() for path in in_checkout.values()):
        return Sources(in_checkout, "checkout")
    bundled = bundled_dir()
    return Sources({name: bundled / name for name in SOURCE_FILES}, "bundled")


def render(
    sources: Sources, *, user: str, mascope_bin: str, home: Path
) -> dict[str, str]:
    """
    Every unit's content as it is installed, by unit name.

    :raises ValueError: A template keeps a placeholder nothing fills.
    """
    values = {
        "@@USER@@": user,
        "@@MASCOPE_BIN@@": mascope_bin,
        "@@MASCOPE_PATH@@": str(home),
        "@@DISK_CHECK@@": str(sources.files["disk-check.sh"]),
        "@@UPDATE_SCRIPT@@": str(sources.files["mascope-update.sh"]),
    }
    rendered = {}
    for name in UNITS:
        text = sources.text(name)
        for token, value in values.items():
            text = text.replace(token, value)
        if "@@" in text:
            raise ValueError(f"{name} keeps a placeholder nothing fills")
        rendered[name] = text
    return rendered


def running_executable() -> Optional[str]:
    """
    The ``mascope`` executable this process was started as, as an absolute path.

    Not resolved through symlinks: uv links the tool into ``~/.local/bin``,
    and that link is what stays put when the tool is reinstalled. Falls back to
    ``mascope`` on PATH when the process was not started as ``mascope`` (for
    example ``python -m``).
    """
    argv0 = sys.argv[0] if sys.argv else ""
    if Path(argv0).name in ("mascope", "mascope.exe"):
        found = argv0 if os.sep in argv0 else shutil.which(argv0)
        if found and Path(found).is_file():
            return str(Path(found).absolute())
    found = shutil.which("mascope")
    return str(Path(found).absolute()) if found else None


def named_executable(value: str) -> Optional[str]:
    """
    An executable named with ``--bin``, as an absolute path, or None.

    A bare name is looked up on PATH. Not resolved through symlinks, as
    :func:`running_executable`. None when the path is not an executable file:
    systemd refuses a relative ``ExecStart``, and a typo would only show at the
    next boot.
    """
    if os.sep not in value and not value.startswith("~"):
        found = shutil.which(value)
        if found is None:
            return None
        value = found
    path = Path(value).expanduser()
    if not path.is_file() or not os.access(path, os.X_OK):
        return None
    return str(path.absolute())


def environment_mascope_path(path: Optional[Path] = None) -> Optional[str]:
    """``MASCOPE_PATH`` as ``/etc/environment`` sets it, or None."""
    path = path or ENVIRONMENT_FILE
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return None
    value = None
    for line in lines:
        key, sep, raw = line.strip().partition("=")
        if sep and key.strip() == "MASCOPE_PATH":
            value = raw.strip().strip("'\"")
    return value


def _tee(path: Path, content: str, note: str) -> Step:
    return Step(["sudo", "tee", str(path)], note, stdin=content)


def install_steps(
    sources: Sources,
    rendered: dict[str, str],
    *,
    user: str,
    home: Path,
    set_environment: bool,
    environment_text: str = "",
) -> list[Step]:
    """
    What `install` does, in order: the steps `tooling/ubuntu.sh` took.

    :param set_environment: ``/etc/environment`` lacks ``MASCOPE_PATH`` and gets
        it appended, so the units, which read it from there, see this home.
    :param environment_text: The file's current content, to know whether the
        appended line needs a newline before it.
    """
    steps = []
    if set_environment:
        lead = "" if not environment_text or environment_text.endswith("\n") else "\n"
        steps.append(
            Step(
                ["sudo", "tee", "-a", str(ENVIRONMENT_FILE)],
                f"set MASCOPE_PATH={home} in {ENVIRONMENT_FILE}",
                stdin=f"{lead}MASCOPE_PATH={home}\n",
            )
        )
    for name in UNITS:
        steps.append(
            _tee(SYSTEMD_DIR / name, rendered[name], f"write {SYSTEMD_DIR / name}")
        )
    steps.append(
        Step(
            ["sudo", "install", "-d", "-m", "755", str(CONFIG_DIR)],
            f"make {CONFIG_DIR}",
        )
    )
    for installed, template in ENV_FILES.items():
        target = CONFIG_DIR / installed
        if target.exists():
            continue
        steps.append(
            Step(
                [
                    "sudo",
                    "install",
                    "-m",
                    "600",
                    "-o",
                    user,
                    "-g",
                    user,
                    str(sources.files[template]),
                    str(target),
                ],
                f"seed {target}",
            )
        )
    steps += [
        Step(["sudo", "systemctl", "daemon-reload"], "reload systemd"),
        Step(
            ["sudo", "systemctl", "enable", "mascope.service"],
            "start the stack at boot",
        ),
        Step(
            ["sudo", "systemctl", "enable", "--now", "mascope-disk-check.timer"],
            "check the disks every 15 minutes",
        ),
        Step(
            ["sudo", "systemctl", "enable", "--now", "mascope-assignment-prune.timer"],
            "prune superseded assignment runs nightly",
        ),
    ]
    return steps


def remove_steps() -> list[Step]:
    """What `remove` does: stop, disable and delete every unit, then reload."""
    steps = []
    for unit in REMOVE_ORDER:
        steps += [
            Step(["sudo", "systemctl", "stop", unit], f"stop {unit}", check=False),
            Step(
                ["sudo", "systemctl", "disable", unit], f"disable {unit}", check=False
            ),
            Step(["sudo", "rm", "-f", str(SYSTEMD_DIR / unit)], f"delete {unit}"),
        ]
    steps.append(Step(["sudo", "systemctl", "daemon-reload"], "reload systemd"))
    return steps


def _run(step: Step) -> subprocess.CompletedProcess:
    """Run one step; a seam the tests replace."""
    return subprocess.run(
        step.argv,
        input=step.stdin,
        text=True,
        stdout=subprocess.DEVNULL if step.stdin is not None else None,
        check=False,
    )


def _carry_out(steps: list[Step], dry_run: bool) -> None:
    """Print each step under --dry-run, else run it and stop at a failure."""
    for step in steps:
        if dry_run:
            typer.echo(f"# {step.note}")
            typer.echo(" ".join(step.argv))
            if step.stdin is not None:
                typer.echo("    " + step.stdin.rstrip("\n").replace("\n", "\n    "))
            typer.echo("")
            continue
        result = _run(step)
        if step.check and result.returncode != 0:
            runtime.logger.error(
                f"Could not {step.note}: '{' '.join(step.argv)}' exited "
                f"{result.returncode}."
            )
            raise typer.Exit(1)


def _require_linux() -> None:
    if platform.system() != "Linux":
        runtime.logger.error(
            "The Mascope units are systemd units, so they install on Linux "
            f"only (this is {platform.system()}). Start the stack with "
            "'mascope prod up --detach' instead."
        )
        raise typer.Exit(1)


def _home() -> Path:
    return Path(os.environ["MASCOPE_PATH"]).absolute()


@units_app.callback()
def main() -> None:
    """
    Install or remove the deployment's systemd units.

    \b
        mascope prod units install --dry-run
        mascope prod units install
        mascope prod units remove
    """


@units_app.command("install")
def install(
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Print what would be done, do nothing."),
    ] = False,
    mascope_bin: Annotated[
        Optional[str],
        typer.Option(
            "--bin",
            help="The mascope executable the units run. Defaults to the one "
            "running this command.",
        ),
    ] = None,
) -> None:
    """
    Install the units that start the stack at boot and keep it maintained.

    Writes them to /etc/systemd/system with the deploy account, this runtime
    home and the mascope executable filled in, seeds the settings files in
    /etc/mascope once (never overwriting one), and enables the boot service,
    the disk check and the assignment-run retention. The unattended update is
    installed but left disabled: enable mascope-update.timer when ready.
    Runs its privileged steps through sudo; run it as the deploy account,
    again after a release changes the units.
    """
    _require_linux()
    user = getpass.getuser()
    if user == "root":
        runtime.logger.error(
            "Run this as the account that runs the deployment, not as root: "
            "the units run as the account that installs them, and the "
            "privileged steps go through sudo."
        )
        raise typer.Exit(1)
    if mascope_bin is not None:
        executable = named_executable(mascope_bin)
        if executable is None:
            runtime.logger.error(
                f"--bin {mascope_bin} is not an executable file: name the "
                "mascope executable the units should run."
            )
            raise typer.Exit(1)
    else:
        executable = running_executable()
    if not executable:
        runtime.logger.error(
            "Cannot tell which mascope executable the units should run: name "
            "it with --bin."
        )
        raise typer.Exit(1)
    home = _home()

    # The units read MASCOPE_PATH from /etc/environment, and the disk check
    # needs it. A home named there is the one they will run on.
    named = environment_mascope_path()
    if named is not None and Path(named) != home:
        runtime.logger.error(
            f"{ENVIRONMENT_FILE} sets MASCOPE_PATH={named}, but this is "
            f"{home}: the units would run on the other one. Correct "
            f"{ENVIRONMENT_FILE}, or run this with MASCOPE_PATH={named}."
        )
        raise typer.Exit(1)
    try:
        environment_text = ENVIRONMENT_FILE.read_text(encoding="utf-8")
    except FileNotFoundError:
        environment_text = ""

    sources = sources_for(home)
    rendered = render(sources, user=user, mascope_bin=executable, home=home)
    steps = install_steps(
        sources,
        rendered,
        user=user,
        home=home,
        set_environment=named is None,
        environment_text=environment_text,
    )
    if dry_run:
        typer.echo(
            f"# Would install the units for {home} ({sources.origin} templates), "
            f"running {executable} as {user}:\n"
        )
    _carry_out(steps, dry_run)
    if dry_run:
        return
    runtime.logger.success(
        f"mascope.service enabled for user '{user}' (bin: {executable}). The disk "
        "check (every 15 min) and the assignment-run retention (nightly; keeps "
        "the newest 2 completed runs per sample) are enabled; tune them in "
        f"{CONFIG_DIR}/disk-check.env and prune.env."
    )
    runtime.logger.info(
        f"Set HEALTHCHECK_URL in {CONFIG_DIR}/disk-check.env to get alerted: "
        "until then the disk check alerts nobody. See docs/maintaining.md."
    )
    runtime.logger.info(
        "Auto-updates are INSTALLED but DISABLED. To turn them on: "
        "'sudo systemctl enable --now mascope-update.timer'. See "
        "docs/maintaining.md."
    )


@units_app.command("remove")
def remove(
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Print what would be done, do nothing."),
    ] = False,
) -> None:
    """
    Stop, disable and delete the units. The settings in /etc/mascope stay.
    """
    _require_linux()
    _carry_out(remove_steps(), dry_run)
    if not dry_run:
        runtime.logger.success(
            f"Removed the Mascope units (kept the settings in {CONFIG_DIR})."
        )
