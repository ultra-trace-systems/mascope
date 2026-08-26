"""
Acquisition-date filtering for the `mascope env sync` filestore transfer.

The filestore lays samples out as
`filestore/<instrument>/<YYYY.MM.DD>/<sample_name>/`, where the date directory
is derived from the acquisition timestamp parsed out of the sample filename
(`libraries/file/src/mascope_file/name.py` → `parse_path_from_item_filename`).
A date range is therefore a *path* filter: rsync applies it directly, no
database query is involved, and the dates select on acquisition date rather
than on file modification time (which records upload or reprocessing, not
measurement).

Rules are anchored at `/filestore/` because the rsync transfer root is the
whole env directory — `agents/`, `filestreams/`, `logs/`, `temp/` and the
`*.mascope.toml` overlays must keep transferring unfiltered. `filestore` is
the directory name `[meta] filestore` resolves to inside an env directory
(`base.mascope.toml`: `./filestore`).

Contains no Typer commands — implementation only.
"""

import datetime
import re
import subprocess
from pathlib import PurePosixPath

from mascope_cli.cmd.env._paths import local_env_dir, remote_env_dir
from mascope_cli.cmd.env._ssh import cygwin_bin, get_identity_args


# The CLI date format matches `logs gc --before`; the directory format is the
# one `parse_path_from_item_filename` writes into the filestore.
CLI_DATE_FORMAT = "%Y-%m-%d"
DIR_DATE_FORMAT = "%Y.%m.%d"

_DATE_DIR_RE = re.compile(r"^\d{4}\.\d{2}\.\d{2}$")


def parse_option_date(value: str, option: str) -> datetime.date:
    """
    Parse a `--from` / `--to` option value.

    :param value: Raw option value from the CLI.
    :type value: str
    :param option: Option name, used in the error message (e.g. `"--from"`).
    :type option: str
    :return: The parsed date.
    :rtype: datetime.date
    :raises ValueError: If the value is not a `YYYY-MM-DD` date.
    """
    try:
        return datetime.datetime.strptime(value, CLI_DATE_FORMAT).date()
    except ValueError:
        raise ValueError(
            f"Invalid {option} value '{value}' — expected YYYY-MM-DD, e.g. 2026-03-01."
        ) from None


def parse_date_dir(name: str) -> datetime.date | None:
    """
    Parse a filestore date directory name.

    :param name: Directory name (e.g. `"2026.03.01"`).
    :type name: str
    :return: The date, or `None` if the name is not a date directory
             (`sample_batches`, an instrument name, `2026.02.31`, ...).
    :rtype: datetime.date | None
    """
    if not _DATE_DIR_RE.match(name):
        return None
    try:
        return datetime.datetime.strptime(name, DIR_DATE_FORMAT).date()
    except ValueError:
        return None  # syntactically a date dir but not a real day, e.g. 2026.02.31


def select_date_dirs(
    names: list[str],
    from_date: datetime.date | None,
    to_date: datetime.date | None,
) -> list[str]:
    """
    Select the date directory names that fall inside the requested window.

    Both bounds are inclusive and either may be `None` (open-ended). Names
    that are not date directories are never selected.

    :param names: Candidate directory names, typically from
                  `source_date_dir_names`.
    :type names: list[str]
    :param from_date: Earliest acquisition date to keep, or `None`.
    :type from_date: datetime.date | None
    :param to_date: Latest acquisition date to keep, or `None`.
    :type to_date: datetime.date | None
    :return: Sorted, de-duplicated subset of `names`.
    :rtype: list[str]
    """
    selected = set()
    for name in names:
        day = parse_date_dir(name)
        if day is None:
            continue
        if from_date is not None and day < from_date:
            continue
        if to_date is not None and day > to_date:
            continue
        selected.add(name)
    return sorted(selected)


def build_filter_rules(
    date_dirs: list[str],
    include_batches: bool = True,
) -> list[str]:
    """
    Build the rsync filter rules that restrict the filestore to `date_dirs`.

    rsync applies the first matching rule, so the order matters:

    1. descend into the filestore and (optionally) keep the batch cache —
       `sample_batches/` sits at the instrument level but has no date in its
       path, so it must be decided before the date rules run;
    2. allow the instrument directories themselves;
    3. include each selected date directory and everything below it (`***`
       matches the directory *and* its contents);
    4. drop everything else one level under an instrument directory — rsync
       then never descends into the excluded date directories.

    No rule mentions anything outside `/filestore/`, so the rest of the env
    directory transfers exactly as it does without a filter.

    Empty instrument directories are created on the target for instruments
    with no selected dates. `--prune-empty-dirs` is deliberately not used: it
    would also prune legitimately empty directories elsewhere in the env
    (`temp/`, an unused `filestreams/`). The app's own `gc_filestore()` sweeps
    empty filestore directories.

    :param date_dirs: Date directory names to keep (`YYYY.MM.DD`).
    :type date_dirs: list[str]
    :param include_batches: Keep `filestore/sample_batches/` (a rebuildable
                            cache) in the transfer. Default: `True`.
    :type include_batches: bool
    :return: rsync filter rules, one per line, in evaluation order.
    :rtype: list[str]
    """
    rules = ["+ /filestore/"]
    if include_batches:
        rules.append("+ /filestore/sample_batches/***")
    rules.append("+ /filestore/*/")
    rules += [f"+ /filestore/*/{name}/***" for name in date_dirs]
    rules.append("- /filestore/*/*")
    return rules


def source_date_dir_names(
    remote: str | None,
    env_name: str,
    control_args: list[str] | None = None,
) -> list[str]:
    """
    List every second-level directory name under the source filestore.

    These are the `<YYYY.MM.DD>` directories under each instrument, plus the
    batch-cache ids under `sample_batches/`; `select_date_dirs` discards the
    latter. Names are returned rather than paths because the filter rules
    match a date directory under any instrument.

    :param remote: Remote identifier (`USER@HOST`) or `None` for local.
    :type remote: str | None
    :param env_name: Name of the source runtime environment.
    :type env_name: str
    :param control_args: SSH multiplexing flags from `SshMux` to reuse an
                         existing ControlMaster connection. Pass `[]` or
                         `None` for a standalone connection.
    :type control_args: list[str] | None
    :return: Sorted, de-duplicated directory names. Empty when the source
             filestore holds no directories, or, locally, when there is no
             filestore at all.
    :rtype: list[str]
    :raises RuntimeError: If the remote listing did not complete - a missing
                          source env, an unreadable instrument directory, a
                          dangling symlink. A partial listing is never
                          returned: it would silently narrow the transfer.
    """
    if remote is None:
        filestore = local_env_dir(env_name) / "filestore"
        if not filestore.is_dir():
            return []
        return sorted({p.name for p in filestore.glob("*/*") if p.is_dir()})

    env_dir = remote_env_dir(remote, env_name, control_args)
    # -L so a filestore symlinked onto a data volume (docs/maintaining.md →
    # "The filestore on a data volume") is still traversed.
    cmd = f"find -L {env_dir}/filestore -mindepth 2 -maxdepth 2 -type d"
    result = subprocess.run(
        [cygwin_bin("ssh")]
        + get_identity_args()
        + (control_args or [])
        + [remote, "bash", "-l", "-c", f"'{cmd}'"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        # find prints what it could read before exiting non-zero, so a partial
        # listing is indistinguishable from a complete one at this point.
        # Filtering against it would drop whole dates from the transfer while
        # rsync still reported success.
        detail = (result.stderr or "").strip() or "no error output"
        raise RuntimeError(
            f"Could not list the filestore of env '{env_name}' on {remote} "
            f"(find exited {result.returncode}): {detail}"
        )
    return sorted(
        {
            PurePosixPath(line.strip()).name
            for line in result.stdout.splitlines()
            if line.strip()
        }
    )
