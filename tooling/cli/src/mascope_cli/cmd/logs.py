import os
from typing import Annotated

import typer

from mascope_cli.runtime import runtime


logs_app = typer.Typer()


@logs_app.callback()
def main():
    """
    Manage mascope log files
    """


@logs_app.command()
def query(
    level: Annotated[
        str | None,
        typer.Option(
            "--level",
            "-l",
            help="The minimum log level to show",
        ),
    ] = "info",
    from_datetime: Annotated[
        str | None,
        typer.Option(
            "--from",
            "-f",
            help="The starting date or datetime to query from",
        ),
    ] = None,
    interval: Annotated[
        str | None,
        typer.Option(
            "--interval",
            "-i",
            help="A range of time to query across, e.g. '60d' or '60 days'. Relative to --to or --from if provided, or to now otherwise.",
        ),
    ] = None,
    to_datetime: Annotated[
        str | None,
        typer.Option(
            "--to",
            "-t",
            help="The ending date or datetime to query to",
        ),
    ] = None,
    grep: Annotated[
        str | None,
        typer.Option(
            "--grep",
            "-g",
            help="Highlight lines matching the grep pattern in the terminal logs",
        ),
    ] = None,
    grep_context: Annotated[
        int,
        typer.Option(
            "--grep-ctx",
            "-c",
            help="Number of lines to show before and after grepped lines",
        ),
    ] = 25,
    max: Annotated[
        int | None,
        typer.Option(
            "--max",
            "-m",
            help="Print only the N most recent matching lines",
        ),
    ] = None,
    service: Annotated[
        str | None,
        typer.Option(
            "--service",
            "-s",
            help="Only logs of one service/module (e.g. 'backend', 'file-converter')",
        ),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option(
            "--json",
            "-j",
            help="Print raw NDJSON log records (machine-readable, no colors)",
        ),
    ] = False,
    dev: Annotated[
        bool | None,
        typer.Option(help="Query the 'dev' mode logs of the active environment"),
    ] = False,
    prod: Annotated[
        bool | None,
        typer.Option(help="Query the 'prod' mode logs of the active environment"),
    ] = False,
):
    """
    Query the active runtime environment's log files.
    """
    if from_datetime and to_datetime and interval:
        runtime.logger.error(
            "mascope log query: cannot use '--from', '--to' and '--interval' together"
        )
        return
    if dev and prod:
        runtime.logger.error(
            "mascope log query can only be used with <i>either</i> --dev or --prod, not both."
        )
        return
    elif dev:
        mode = "dev"
    elif prod:
        mode = "prod"
    else:
        mode = None  # automatic
    if grep:
        os.environ["MASCOPE_LOGGREP"] = grep
    runtime.logging.query(
        level=level,
        limit=max,
        from_datetime=from_datetime,
        to_datetime=to_datetime,
        grep=grep,
        grep_context=grep_context,
        mode=mode,
        interval=interval,
        service=service,
        json_output=json_output,
    )


@logs_app.command()
def gc(
    before: Annotated[
        str | None,
        typer.Option(
            "--before",
            "-b",
            help="The latest day for which to retain logs",
        ),
    ] = None,
    retain: Annotated[
        str | None,
        typer.Option(
            "--retain",
            "-r",
            help="Keep logs newer than this interval, e.g. '14d' or '2 weeks'",
        ),
    ] = None,
    dev: Annotated[
        bool | None,
        typer.Option(help="GC the 'dev' mode logs of the active environment"),
    ] = False,
    prod: Annotated[
        bool | None,
        typer.Option(help="GC the 'prod' mode logs of the active environment"),
    ] = False,
    dryrun: Annotated[
        bool | None,
        typer.Option(help="Only show planned deletion rather than actually deleting"),
    ] = False,
):
    """
    Garbage collect old log files.
    """

    if dev and prod:
        runtime.logger.error(
            "mascope log gc can only be used with <i>either</i> --dev or --prod, not both."
        )
        return
    elif dev:
        mode = "dev"
    elif prod:
        mode = "prod"
    else:
        runtime.logger.error(
            "mascope log gc: must specify --dev or --prod to specify which mode to GC"
        )
        return
    if (before and retain) or not (before or retain):
        runtime.logger.error(
            "mascope log gc: must specify either --before or --retain (not both)"
        )
        return
    runtime.logging.gc(mode=mode, before=before, retain=retain, dryrun=dryrun)
