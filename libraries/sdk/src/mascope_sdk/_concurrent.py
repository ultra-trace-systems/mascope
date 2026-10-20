"""Shared progress bar and concurrent execution helpers."""

import sys
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Callable, TypeVar

import pandas as pd
from loguru import logger
from tqdm import tqdm


#: Default tqdm keyword arguments used across the SDK.
_TQDM_DEFAULTS = {
    "file": sys.stderr,
    "bar_format": "{l_bar}{bar:30}{r_bar}",
    "colour": "green",
}

T = TypeVar("T")


@dataclass(frozen=True)
class TaskFailure:
    """A task of :func:`run_concurrent` that raised.

    :ivar args: The args-tuple the task was called with, so the caller can
        say which sample (or stage, or peak) it was.
    :ivar error: What it raised.
    """

    args: tuple[Any, ...]
    error: Exception


def failure_record(failure: TaskFailure, **where: Any) -> dict[str, Any]:
    """A failed task as a frame reports it: where it failed, and with what.

    Plain values only - the error's type name and message, not the exception
    itself - so the record survives the copies pandas makes of ``attrs``.

    :param failure: The task that raised.
    :param where: What identifies the task to a reader (the sample, the stage).
    :return: ``where`` with ``error_type`` and ``error`` added.
    """
    return {
        **where,
        "error_type": type(failure.error).__name__,
        "error": str(failure.error),
    }


def mark_complete(
    frame: pd.DataFrame | None, failed: list[dict[str, Any]]
) -> pd.DataFrame | None:
    """Say on a frame whether it is the whole answer.

    Sets ``attrs["complete"]`` (no task failed) and ``attrs["failures"]`` (the
    records of those that did). When nothing was loaded because every task
    failed, None would read as "nothing there"; an empty frame carrying the
    failures is returned instead.

    :param frame: What the loads produced, or None when they produced nothing.
    :param failed: The records of the failed tasks (:func:`failure_record`).
    :return: The frame, marked; None only when nothing was loaded and nothing
        failed.
    """
    if frame is None:
        if not failed:
            return None
        frame = pd.DataFrame()
    frame.attrs["complete"] = not failed
    frame.attrs["failures"] = failed
    return frame


def progress_bar(total: int, desc: str, unit: str = "it") -> tqdm:
    """Create a tqdm progress bar with the SDK's default styling."""
    return tqdm(total=total, desc=desc, unit=unit, **_TQDM_DEFAULTS)


def run_concurrent(
    func: Callable[..., T | None],
    tasks: list[tuple[Any, ...]],
    *,
    max_workers: int = 8,
    desc: str = "Processing",
    unit: str = "it",
    on_result: Callable[[tuple[Any, ...], T], None] | None = None,
) -> tuple[list[T], list[TaskFailure]]:
    """Execute *func* concurrently over *tasks* with a progress bar.

    Each element of *tasks* is an args-tuple unpacked into *func*.
    Returns the non-None results and the tasks that failed, both in the order
    the tasks were given (not the order they finished), so a load's rows and
    its failures come back the same way on every run.

    Individual task failures (``Exception``) do not stop the remaining tasks:
    each is returned as a :class:`TaskFailure` beside the results, so the
    caller can tell a partial answer from a whole one, and a summary is
    logged at the end when any task failed. ``BaseException`` (e.g.
    ``KeyboardInterrupt``) still propagates immediately after cancelling
    pending futures.

    *on_result*, when given, is called with each task's args-tuple and its
    non-None result as the result arrives, in the thread that collects them
    (in the order tasks finish). It is how a caller watches a load as it runs
    - a running row count against a cap, say. Whatever it raises stops the
    run: the tasks not yet started are cancelled, those already running are
    waited for and their results dropped, and the exception propagates. It is
    not a task failure.
    """
    if max_workers > 8:
        raise ValueError("max_workers cannot exceed 8 to avoid overloading the API")

    if not tasks:
        return [], []

    results: list[tuple[int, T]] = []
    failed: list[tuple[int, TaskFailure]] = []
    futures: dict[Future[T | None], tuple[int, tuple[Any, ...]]] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for index, args in enumerate(tasks):
            futures[executor.submit(func, *args)] = (index, args)

        pbar = progress_bar(len(futures), desc=desc, unit=unit)
        try:
            for future in as_completed(futures):
                index, args = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    failed.append((index, TaskFailure(args, exc)))
                    # INFO per task; the summary below warns once for the run
                    logger.info("Task failed ({}): {}", desc, exc)
                else:
                    if result is not None:
                        results.append((index, result))
                        if on_result is not None:
                            on_result(args, result)
                pbar.update(1)
        except BaseException:
            # Cancel remaining futures so shutdown() doesn't block
            for f in futures:
                f.cancel()
            raise
        finally:
            pbar.close()

    ordered = [result for _, result in sorted(results, key=lambda r: r[0])]
    failures = [failure for _, failure in sorted(failed, key=lambda f: f[0])]
    if failures:
        logger.warning(
            "{} of {} task(s) failed - returning {} partial result(s)",
            len(failures),
            len(tasks),
            len(ordered),
        )

    return ordered, failures
