"""What a pass of the batch stages recorded of one of its steps.

A pass is one batch run (``batch_stages.assign_batch``): each stage's steps
write into it, and its summary keeps every stage under ``stages``, with each
step's outcome - its counts under ``data``, the thresholds or settings it ran
on under ``config`` - where a step's own run used to keep them as its summary
and its config. The tests of each stage read them here.
"""

from typing import Any


def step_outcome(run: Any, stage: str, index: int = 0) -> dict:
    """The outcome of a stage's ``index``-th step, as the pass's run
    recorded it (the isotope lines' strip is 0, its stamp 1)."""
    entry = next(e for e in run.summary["stages"] if e["stage"] == stage)
    assert entry["status"] == "done", entry
    return entry["outcomes"][index]
