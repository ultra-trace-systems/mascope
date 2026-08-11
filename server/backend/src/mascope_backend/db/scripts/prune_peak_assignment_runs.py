"""
Maintenance script to prune superseded peak assignment runs.

Peak assignment writes one row per observed peak per run, and runs are never
superseded automatically, so `peak_assignment` grows without bound wherever
assignment is re-run. This keeps the newest few completed runs per sample and
drops the rest, plus non-completed runs past a short grace period. Deleting a
run cascades to its assignment rows.

Set MASCOPE_PRUNE_DRY_RUN=1 to report what would be deleted without deleting.
Override the policy with MASCOPE_PRUNE_KEEP_PER_SAMPLE (default 3),
MASCOPE_PRUNE_KEEP_FAILED_HOURS (default 24) and MASCOPE_PRUNE_KEEP_RUNNING_HOURS
(default 72, floored at MIN_KEEP_RUNNING_HOURS - runs that may still be executing
are not something an operator should be able to shorten into the danger zone).

Usage:
    mascope dev db script run prune_peak_assignment_runs
    mascope prod db script run prune_peak_assignment_runs

Date: 2026-07-27
"""

import asyncio
import os

from mascope_backend.db import configure_database_engine
from mascope_backend.db.admin.peak_assignments.prune_runs import (
    DEFAULT_KEEP_FAILED_HOURS,
    DEFAULT_KEEP_PER_SAMPLE,
    DEFAULT_KEEP_RUNNING_HOURS,
    MIN_KEEP_RUNNING_HOURS,
    prune_peak_assignment_runs,
)
from mascope_backend.runtime import runtime


def _int_env(name: str, default: int, minimum: int) -> int:
    """Read an integer override from the environment, floored at ``minimum``.

    The two failure modes are treated differently on purpose. A value that is
    not an integer at all is a typo with no discernible intent, and a cron job
    that keeps pruning on the documented policy beats one that stops, so the
    default is used. A value below ``minimum`` is a parseable instruction to
    retain less than the prune is willing to retain: substituting the default
    would silently ignore what the operator asked for, so it fails here - naming
    the variable and the floor - instead of dying further in on an inner
    ValueError that mentions only a parameter name the operator never typed.

    :param name: Environment variable to read.
    :param default: Value used when unset or unparseable.
    :param minimum: Smallest accepted value.
    :return: The override, or the default.
    :raises ValueError: When the variable parses but is below ``minimum``.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        runtime.logger.warning(f"{name}='{raw}' is not an integer; using {default}")
        return default
    if value < minimum:
        raise ValueError(f"{name}={value} is below the minimum of {minimum}")
    return value


async def run() -> None:
    """Initialize the database and prune superseded assignment runs."""
    await configure_database_engine()
    dry_run = os.environ.get("MASCOPE_PRUNE_DRY_RUN", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    result = await prune_peak_assignment_runs(
        dry_run=dry_run,
        keep_per_sample=_int_env(
            "MASCOPE_PRUNE_KEEP_PER_SAMPLE", DEFAULT_KEEP_PER_SAMPLE, minimum=1
        ),
        keep_failed_hours=_int_env(
            "MASCOPE_PRUNE_KEEP_FAILED_HOURS", DEFAULT_KEEP_FAILED_HOURS, minimum=0
        ),
        keep_running_hours=_int_env(
            "MASCOPE_PRUNE_KEEP_RUNNING_HOURS",
            DEFAULT_KEEP_RUNNING_HOURS,
            minimum=MIN_KEEP_RUNNING_HOURS,
        ),
    )
    runtime.logger.info("=" * 80)
    runtime.logger.info("PRUNE PEAK ASSIGNMENT RUNS COMPLETE")
    runtime.logger.info(f"Status: {result['status']}")
    runtime.logger.info(f"Prunable runs: {result['prunable_runs']}")
    runtime.logger.info(f"Deleted runs: {result['deleted_runs']}")
    runtime.logger.info(f"Deleted assignments: {result['deleted_assignments']}")
    runtime.logger.info("=" * 80)


def main() -> None:
    """Entry point for the assignment-run prune script."""
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        runtime.logger.info("Cancelled by user (Ctrl+C)")
    except Exception:
        runtime.logger.exception("Script failed")
        raise


if __name__ == "__main__":
    main()
