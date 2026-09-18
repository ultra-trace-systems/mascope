"""
`mascope file`: inspect acquisition files.

`mascope file scans PATH` shows what a Thermo raw file's scans measured: its
scan streams, how they are laid out in time, and the strongest peaks of each
MS1 stream. It is the census the converter writes into a file's `.props`
(`mascope_thermo.streams`), taken again on demand, for a file that is not in
Mascope yet or whose routing needs explaining.

The reader is Mascope's own. A checkout has it installed and runs it in this
process. The standalone operator CLI does not ship it, so there the file is
copied into the running backend container, read there, and removed again.
"""

import importlib.util
import json
import subprocess
import uuid
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from mascope_cli.cmd.prod.db import scripts as prod_scripts
from mascope_cli.runtime import runtime


file_app = typer.Typer()

_MODE = "prod"
_READER_MODULE = "mascope_thermo.streams"


@file_app.callback()
def main() -> None:
    """
    Inspect acquisition files.
    """


def _reader_available() -> bool:
    """Whether this install can read raw files itself (a checkout can)."""
    try:
        return importlib.util.find_spec(_READER_MODULE) is not None
    except ModuleNotFoundError:
        # find_spec imports the parent package first, so an install without
        # mascope_thermo raises here rather than returning None.
        return False


def _report_in_process(path: Path, top: int) -> dict:
    from mascope_thermo.streams import stream_report

    return stream_report(str(path), top=top)


def _parse_report(stdout: str) -> dict:
    """
    The report from the reader's stdout: its last line, as JSON.

    The reader silences logging before it opens the file, but anything logged
    while it was imported is flushed first. A colourised log line leaves its
    reset code at the start of the line after it, so the report is read from
    its first brace.

    :param stdout: What ``python -m mascope_thermo.streams`` printed.
    :type stdout: str
    :return: The parsed report.
    :rtype: dict
    :raises ValueError: When the last line holds no JSON object.
    """
    lines = [line for line in stdout.splitlines() if line.strip()]
    if not lines or "{" not in lines[-1]:
        raise ValueError("the reader printed no report")
    last = lines[-1]
    return json.loads(last[last.index("{") :])


def _report_in_container(path: Path, top: int) -> dict:
    """Read the file inside the backend container, which has the reader."""
    container = runtime.full_config.backend.get_backend_container_name(_MODE)
    python = prod_scripts._resolve_container_python(container)
    if python is None:
        runtime.logger.error(
            f"Could not find a mascope Python in container '{container}'. This "
            "install has no raw-file reader of its own, so the file is read "
            "inside the backend container: the stack must be running "
            "(`mascope prod up --detach`)."
        )
        raise typer.Exit(1)

    # Named uniquely, so two operators reading the same file cannot remove
    # each other's copy.
    target = f"/tmp/mascope-file-scans-{uuid.uuid4().hex}{path.suffix.lower()}"
    copied = subprocess.run(
        ["docker", "cp", str(path), f"{container}:{target}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if copied.returncode != 0:
        runtime.logger.error(
            f"Could not copy {path} into '{container}': {copied.stderr.strip()}"
        )
        raise typer.Exit(1)
    try:
        result = subprocess.run(
            [
                "docker",
                "exec",
                container,
                python,
                "-m",
                _READER_MODULE,
                target,
                "--top",
                str(top),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        subprocess.run(
            ["docker", "exec", container, "rm", "-f", target],
            capture_output=True,
            text=True,
            check=False,
        )
    if result.returncode != 0:
        detail = result.stderr.strip().splitlines()
        runtime.logger.error(
            f"Reading {path.name} in '{container}' failed "
            f"(exit {result.returncode}): {detail[-1] if detail else 'no output'}"
        )
        raise typer.Exit(1)
    try:
        return _parse_report(result.stdout)
    except ValueError as exc:
        runtime.logger.error(f"Reading {path.name} in '{container}' failed: {exc}")
        raise typer.Exit(1) from exc


def _print_report(report: dict) -> None:
    """One stream per paragraph: its key would not fit a table column."""
    console = Console(highlight=False)
    method = Path(str(report.get("method_file") or "").replace("\\", "/")).name
    console.print(
        f"[bold]{report['file']}[/bold]  {report.get('model') or '?'}  "
        f"method: {method or '(none)'}  {report['scans']} scans"
    )
    ms1_by_polarity: dict[str, int] = {}
    for index, stream in enumerate(report["streams"], start=1):
        console.print()
        console.print(f"{index}. [cyan]{stream['key']}[/cyan]")
        console.print(
            f"   {stream['scans']} scans in {stream['blocks']} block(s), "
            f"{stream['t_first']:.1f}-{stream['t_last']:.1f} s"
        )
        if stream.get("top_peaks"):
            peaks = ", ".join(f"{mz:.4f}" for mz, _ in stream["top_peaks"])
            console.print(f"   top peaks: {peaks}")
        signature = stream["signature"]
        if signature.get("ms_order") == 1:
            polarity = signature.get("polarity") or "?"
            ms1_by_polarity[polarity] = ms1_by_polarity.get(polarity, 0) + 1

    for polarity, count in ms1_by_polarity.items():
        if count > 1:
            console.print()
            console.print(
                f"Polarity {polarity} has {count} MS1 streams. Peak detection "
                "pools them into one peak list."
            )


@file_app.command("scans")
def scans(
    path: Annotated[
        Path,
        typer.Argument(help="A Thermo .raw file on this machine."),
    ],
    top: Annotated[
        int,
        typer.Option(
            "--top", "-t", min=0, help="Strongest peaks to show per MS1 stream."
        ),
    ] = 5,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Print the full report as JSON."),
    ] = False,
    in_container: Annotated[
        bool,
        typer.Option(
            "--in-container",
            help="Read the file in the prod backend container even when this "
            "install could read it itself, to see what the deployed reader "
            "makes of it.",
        ),
    ] = False,
) -> None:
    """
    Show a raw file's scan streams.

    A scan stream is the scans that share one scan signature: analyzer,
    polarity, scan type, source, scan mode, MS order, precursors, scan ranges
    and FT resolution. For each stream this lists its scan count, its blocks,
    the time it spans and the strongest peaks of its averaged spectrum. It
    also says when a polarity has more than one MS1 stream, which peak
    detection pools into one peak list. `--json` adds each stream's parsed
    signature and acquisition parameters.

    \b
    Examples:
        mascope file scans ./acquisition.raw
        mascope file scans ./acquisition.raw --top 10 --json
    """
    if not path.is_file():
        runtime.logger.error(f"No such file: {path}")
        raise typer.Exit(1)
    if path.suffix.lower() != ".raw":
        runtime.logger.error(
            f"{path.name} is not a Thermo .raw file. A TofDaq .h5 file has a "
            "single ion mode and one mass axis, so it has one stream."
        )
        raise typer.Exit(1)

    if in_container or not _reader_available():
        report = _report_in_container(path, top)
    else:
        report = _report_in_process(path, top)

    if as_json:
        print(json.dumps(report, indent=2))
    else:
        _print_report(report)
