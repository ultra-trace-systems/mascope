"""
`mascope export`: take data out of a deployment.

`mascope export package DATASET` writes a dataset's deposit package: one zip
archive to upload to a data repository such as Zenodo, which tells whoever
downloads it what was measured, how, under what licence, by whom and with
which software (`mascope_backend.deposit` says what it holds).

The package is built by the backend's own code, from the database and the
filestore of the stack that holds the dataset. In a source checkout, a
developer's, that is the dev stack: the package is built in a process of its
own on this machine, from the active env's dev database. Anywhere else it is
the prod stack, whose database is reachable only from its containers, so the
package is built inside the prod backend container, into a directory of the
run's own in the env's temp directory, and copied out with `docker cp`. That
holds for a server's install built from its clone too, which has the
backend's code but not the database's port. `--in-container` asks for the
container from a checkout.
"""

import json
import os
import subprocess
import sys
import uuid
import zipfile
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.markup import escape

from mascope_cli.checkout import source_checkout
from mascope_cli.cmd.prod.db import scripts as prod_scripts
from mascope_cli.runtime import runtime


export_app = typer.Typer()

_BUILDER = "mascope_backend.deposit"


@export_app.callback()
def main() -> None:
    """
    Take data out of a Mascope deployment.
    """


def _outcome(line: str) -> dict | None:
    """
    The builder's outcome, if ``line`` is it.

    The builder prints it as one JSON object, ``{"package": ...}`` or
    ``{"error": ...}``. The logger shares stdout with it, and a colourised
    log line leaves its reset code at the start of the next line, so the
    object is read from the line's first brace.

    :param line: A line the builder printed.
    :return: The outcome, or None when the line is not it.
    """
    start = line.find("{")
    if start < 0:
        return None
    try:
        outcome = json.loads(line[start:])
    except ValueError:
        return None
    if isinstance(outcome, dict) and ("package" in outcome or "error" in outcome):
        return outcome
    return None


def _stream(
    command: list[str], env: dict[str, str] | None = None
) -> tuple[int, dict | None]:
    """
    Run the builder, showing its log as it goes, and keep its outcome.

    :param command: The builder's command line.
    :param env: Its environment; this process's when None.
    :return: Its exit status, and its outcome (None if it printed none).
    """
    outcome = None
    with subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    ) as process:
        for line in process.stdout:
            found = _outcome(line)
            if found is not None:
                outcome = found
            else:
                sys.stdout.write(line)
                sys.stdout.flush()
    return process.returncode, outcome


def _build_here(dataset_id: str, output: Path, env_name: str) -> dict:
    """Build the package on this machine, from the env's dev database."""
    # The builder takes its mode from the runtime state, as a script that
    # `mascope dev db script run` starts does. A prod command before this one
    # may have left it at prod, which would point the builder at the prod
    # stack's database host.
    runtime.state.override("mode", "dev")
    environment = {**os.environ, "MASCOPE_ENV": env_name}
    status, outcome = _stream(
        [sys.executable, "-m", _BUILDER, dataset_id, "--output", str(output)],
        env=environment,
    )
    return _checked(status, outcome)


def _build_in_container(dataset_id: str, output: Path) -> dict:
    """Build the package in the prod backend container and copy it out."""
    container = runtime.full_config.backend.get_backend_container_name("prod")
    python = prod_scripts._resolve_container_python(container)
    if python is None:
        runtime.logger.error(
            f"Could not find a mascope Python in container '{container}'. This "
            "install cannot build a package itself, so it is built inside the "
            "backend container: the stack must be running "
            "(`mascope prod up --detach`)."
        )
        raise typer.Exit(1)

    # Each run builds into a directory of its own, named by this, so that
    # two runs of one dataset share no path, and an interrupted run's builder
    # can be found and stopped.
    run = f"export-{uuid.uuid4().hex}"
    inside = None
    try:
        status, outcome = _stream(
            ["docker", "exec", container, python, "-m", _BUILDER, dataset_id]
            + ["--run", run]
        )
        package = _checked(
            status,
            outcome,
            hint=" If it says there is no module mascope_backend.deposit, or "
            "no argument --run, the release deployed predates the package: "
            "update the deployment.",
        )
        inside = package["archive"]
        target = output / os.path.basename(inside) if output.is_dir() else output
        copied = subprocess.run(
            ["docker", "cp", f"{container}:{inside}", str(target)],
            capture_output=True,
            text=True,
            check=False,
        )
    except KeyboardInterrupt:
        if inside is None:
            _stop_builder(container, run)
        raise
    finally:
        # The container's temp directory is emptied when the backend starts;
        # a package is not left there until then. Only the directory this
        # run named is removed.
        if inside is not None and os.path.basename(os.path.dirname(inside)) == run:
            subprocess.run(
                ["docker", "exec", container, "rm", "-rf", os.path.dirname(inside)],
                capture_output=True,
                text=True,
                check=False,
            )
    if copied.returncode != 0:
        runtime.logger.error(
            f"Could not copy the package out of '{container}': {copied.stderr.strip()}"
        )
        raise typer.Exit(1)
    return {**package, "archive": str(target)}


def _stop_builder(container: str, run: str) -> None:
    """
    Stop the builder of an interrupted run in the container.

    ``docker exec`` without a TTY passes no signal on: the interrupt stops
    this process and its ``docker exec``, but the builder would go on writing
    the archive. Sent SIGINT, the builder removes its run's directory itself.
    """
    runtime.logger.warning(f"Interrupted: stopping the build in '{container}'.")
    subprocess.run(
        ["docker", "exec", container, "pkill", "-INT", "-f", run],
        capture_output=True,
        text=True,
        check=False,
    )


def _checked(status: int, outcome: dict | None, hint: str = "") -> dict:
    """The package the builder reports, or the command's failure."""
    if outcome is not None and "error" in outcome:
        runtime.logger.error(outcome["error"])
        raise typer.Exit(1)
    if status != 0 or outcome is None:
        runtime.logger.error(
            f"Building the package failed (exit {status}); its log is above.{hint}"
        )
        raise typer.Exit(1)
    return outcome["package"]


def _manifest(archive: str) -> dict:
    """The manifest of a package archive."""
    with zipfile.ZipFile(archive) as zf:
        name = next(
            n
            for n in zf.namelist()
            if n.count("/") == 1 and n.endswith("/manifest.json")
        )
        return json.loads(zf.read(name))


def _megabytes(size: int) -> str:
    return f"{size / 1e6:.1f} MB"


def _print_summary(package: dict) -> None:
    """What was written, and what to check before depositing it."""
    # Soft-wrapped, so that a long path stays one line to copy.
    console = Console(highlight=False, soft_wrap=True)
    console.print(
        f"[bold]{escape(package['archive'])}[/bold]  {_megabytes(package['bytes'])}, {package['files']} files"
    )
    console.print(
        f"  {package['raw_files_included']} of {package['raw_files']} raw files, "
        f"{package['batches']} batch spreadsheets, "
        f"{package['target_collections']} target collections"
    )
    missing = package["raw_files"] - package["raw_files_included"]
    if missing:
        runtime.logger.warning(
            f"{missing} sample file(s) have no raw file in the filestore, only "
            "the data converted from them: the package describes them without "
            "the file."
        )
    if package["raw_files_changed"]:
        runtime.logger.warning(
            f"{package['raw_files_changed']} raw file(s) are not the files "
            "uploaded: their SHA-256 differs from the one recorded at upload. "
            "The manifest gives both."
        )
    dataset = _manifest(package["archive"])["dataset"]
    for field, what, them in (
        ("license", "licence", "it"),
        ("authors", "authors", "them"),
    ):
        if not dataset.get(field):
            runtime.logger.warning(
                f"The dataset records no {what}, which a repository asks for. "
                f"Add {them} to the dataset in Mascope and build the package again."
            )
    # Through the logger, so that it comes after the warnings it follows.
    runtime.logger.info(
        "Read its README.md, then upload the archive to a repository such as "
        "Zenodo. Add the DOI it mints to the dataset's related identifiers."
    )


@export_app.command("package")
def package(
    dataset_id: Annotated[
        str,
        typer.Argument(help="The id of the dataset to package."),
    ],
    output: Annotated[
        str,
        typer.Option(
            "--output",
            "-o",
            help="The archive's path, or a directory to write it into under "
            "the package's name. A path ending in / is a directory, made if "
            "it is missing. An archive already there is replaced.",
        ),
    ] = ".",
    in_container: Annotated[
        bool,
        typer.Option(
            "--in-container",
            help="Build the package in the prod backend container, from the "
            "prod database, even when this install could build it itself.",
        ),
    ] = False,
) -> None:
    """
    Write a dataset's deposit package: one zip to upload to a repository.

    The archive holds one directory: a README.md that says what was measured,
    how, under what licence, by whom and with which software; DATA_LICENSE;
    each sample file's raw file and the acquisition record it came with; each
    batch's spreadsheet; the target collections matched and calibrated
    against; the instrument functions; manifest.json, which says all of it
    for a program; and SHA256SUMS, which `sha256sum -c` checks.

    On a server the package is built inside the running prod backend
    container, from that stack's env, and copied out. A source checkout
    builds it here instead, from the active env's dev database and filestore,
    unless --in-container is given. It reads whatever dataset it is given:
    whoever runs it has the database.

    \b
    Examples:
        mascope export package 0a1B2c3D4e5F6g7H
        mascope export package 0a1B2c3D4e5F6g7H -o ~/deposits/
    """
    target = _output(output)
    if in_container or source_checkout() is None:
        written = _build_in_container(dataset_id, target)
    else:
        written = _build_here(dataset_id, target, runtime.state.env)
    _print_summary(written)


def _output(typed: str) -> Path:
    """
    Where the archive goes, from ``--output`` as it was typed.

    A path is read as typed, since ``Path`` drops a trailing separator: one
    that ends in a separator, ``.`` or ``..`` names a directory, which is
    made if it is missing. Whatever is made, the archive or the directory,
    its parent must exist, so a mistyped path is refused rather than built.

    :param typed: The ``--output`` option.
    :return: The archive's path, or the directory to write it into.
    """
    output = Path(typed).expanduser().resolve()
    if not output.is_dir() and not output.parent.is_dir():
        runtime.logger.error(f"No such directory: {output.parent}")
        raise typer.Exit(1)
    if os.path.basename(typed) in ("", ".", "..") and not output.is_dir():
        if output.exists():
            runtime.logger.error(f"Not a directory: {output}")
            raise typer.Exit(1)
        output.mkdir()
    return output
