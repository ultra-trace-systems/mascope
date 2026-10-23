"""
Build a dataset's deposit package from the command line::

    python -m mascope_backend.deposit <dataset id> [--output PATH]

The operator command ``mascope export package`` runs this: on the host, in a
checkout, or in the backend container. ``--output`` is the archive's path or
a directory to write it into under the package's name. Without it the
archive goes to ``temp/packages/`` in the env's directory, which the backend
empties whenever it starts; the operator command copies it out of the
container from there.

The logger owns stdout, so the outcome is the last line printed: one JSON
object, ``{"package": {...}}`` with what ``write_package`` reports, or
``{"error": "..."}`` with the exit status 1.
"""

import argparse
import asyncio
import json
import os
import sys

from mascope_backend.db import configure_database_engine, dispose_engine
from mascope_backend.deposit import DatasetNotFound, build_package
from mascope_backend.runtime import runtime


def main(argv: list[str] | None = None) -> int:
    """
    Build the package the arguments name.

    :param argv: The arguments, without the program; ``sys.argv`` when None.
    :return: The exit status: 0 when the archive was written, else 1.
    :rtype: int
    """
    parser = argparse.ArgumentParser(
        prog="python -m mascope_backend.deposit",
        description="Build the deposit package of a dataset.",
    )
    parser.add_argument("dataset_id", help="The dataset to package.")
    parser.add_argument(
        "--output",
        "-o",
        help="The archive's path, or a directory to write it into. Default: "
        "temp/packages/ in the env's directory.",
    )
    args = parser.parse_args(argv)
    output = args.output or _default_directory()

    try:
        summary = asyncio.run(_build(args.dataset_id, output))
    except DatasetNotFound as error:
        _report({"error": str(error)})
        return 1
    except Exception as error:  # noqa: BLE001 - reported, then the exit status
        runtime.logger.exception("Building the package failed")
        _report({"error": f"Building the package failed: {error}"})
        return 1
    runtime.logger.success(
        f"Wrote {summary['archive']}: {summary['files']} files, "
        f"{summary['bytes']} bytes"
    )
    _report({"package": summary})
    return 0


async def _build(dataset_id: str, output: str) -> dict:
    await configure_database_engine()
    try:
        return await build_package(dataset_id, output)
    finally:
        await dispose_engine()


def _default_directory() -> str:
    """``temp/packages/`` in the env's directory, made if missing."""
    path = runtime.env.path("temp", "packages")
    os.makedirs(path, exist_ok=True)
    return path


def _report(outcome: dict) -> None:
    """The outcome, as the last line of stdout."""
    sys.stdout.write(json.dumps(outcome) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    sys.exit(main())
