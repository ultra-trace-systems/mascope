"""Time the continuity pass on one batch, phase by phase: step 4.13's benchmark.

Runs the real pass (``continuity.run_batch_continuity``) against the database
of ``MASCOPE_ENV``, with each of its phases wrapped in a timer, and prints one
JSON line per pass: the batch's size (anchors, members, samples, pairs), the
pass's cut (pairs graded and left, anchors marked ``partial``), the grade
counts, the seconds of each phase, the rows written and the process's peak
resident memory above where it started.

It writes the two continuity columns, so point it at a clone, never at a
database someone reads. Scale a demo ledger to ceiling size first with
``expand_batch_peaks.sql``:

    psql -v batch=<id> -v mult=33 -f expand_batch_peaks.sql
    MASCOPE_ENV=<env> uv run python \\
        server/backend/tests/system/benchmark/continuity_ceiling.py <id> --clear

``--clear`` empties the batch's grades first, so the first pass writes every
graded anchor (a first grading, or a pass after a rebuild); the passes after
it write only what moved, as a pass after a fold does. ``--max-pairs`` sets
the pass's cut (``MAX_PAIRS_PER_BATCH``) for the run.

Not a pytest module: nothing collects it, and CI does not run it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import threading
import time

from sqlalchemy import func, select, update

from mascope_backend.api.new.peak_assignments import continuity
from mascope_backend.db import (
    BatchPeak,
    BatchPeakOccurrence,
    async_session,
    configure_database_engine,
)


def _rss_mb() -> float:
    with open(f"/proc/{os.getpid()}/status") as status:
        for line in status:
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024
    return 0.0


class _PeakRss:
    """The process's peak resident memory while it is entered, sampled."""

    def __init__(self, every: float = 0.02):
        self.every = every
        self.base = self.peak = 0.0
        self._stop = threading.Event()

    def _sample(self) -> None:
        while not self._stop.is_set():
            self.peak = max(self.peak, _rss_mb())
            time.sleep(self.every)

    def __enter__(self):
        self.base = self.peak = _rss_mb()
        self._thread = threading.Thread(target=self._sample, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join()
        self.peak = max(self.peak, _rss_mb())


def _timed(seconds: dict, name: str, fn, *, is_async: bool):
    """``fn`` with its time added to ``seconds[name]``."""
    if is_async:

        async def wrapped(*args, **kwargs):
            started = time.perf_counter()
            try:
                return await fn(*args, **kwargs)
            finally:
                seconds[name] = seconds.get(name, 0.0) + time.perf_counter() - started

    else:

        def wrapped(*args, **kwargs):
            started = time.perf_counter()
            try:
                return fn(*args, **kwargs)
            finally:
                seconds[name] = seconds.get(name, 0.0) + time.perf_counter() - started

    return wrapped


async def _sizes(sample_batch_id: str) -> dict:
    async with async_session() as session:
        anchors = (
            await session.execute(
                select(func.count()).where(BatchPeak.sample_batch_id == sample_batch_id)
            )
        ).scalar_one()
        members = (
            await session.execute(
                select(func.count())
                .select_from(BatchPeakOccurrence)
                .join(
                    BatchPeak,
                    BatchPeak.batch_peak_id == BatchPeakOccurrence.batch_peak_id,
                )
                .where(BatchPeak.sample_batch_id == sample_batch_id)
            )
        ).scalar_one()
    return {"anchors": anchors, "members": members}


async def _clear(sample_batch_id: str) -> None:
    async with async_session() as session:
        await session.execute(
            update(BatchPeak.__table__)
            .where(BatchPeak.__table__.c.sample_batch_id == sample_batch_id)
            .values(continuity_grade=None, continuity=None)
        )
        await session.commit()


async def _pass(sample_batch_id: str) -> dict:
    """One pass, its phases timed."""
    seconds: dict[str, float] = {}
    seen: dict = {}
    originals = {
        "_load": continuity._load,
        "form_pairs": continuity.form_pairs,
        "_load_members": continuity._load_members,
        "grade_batch": continuity.grade_batch,
        "_write": continuity._write,
    }

    async def load_members(session, anchor_ids):
        members = await originals["_load_members"](session, anchor_ids)
        seen["paired_anchors"] = len(members)
        seen["paired_members"] = sum(len(m) for m in members.values())
        return members

    def grade(*args, **kwargs):
        records, summary = originals["grade_batch"](*args, **kwargs)
        seen["partial_anchors"] = sum(
            1
            for _, detail in records.values()
            if continuity.WARNING_PARTIAL in detail.get("warnings", [])
        )
        seen["detail_bytes"] = sum(
            len(json.dumps(detail)) for _, detail in records.values()
        )
        return records, summary

    continuity._load = _timed(seconds, "load", originals["_load"], is_async=True)
    continuity.form_pairs = _timed(
        seconds, "form_pairs", originals["form_pairs"], is_async=False
    )
    continuity._load_members = _timed(
        seconds, "load_members", load_members, is_async=True
    )
    continuity.grade_batch = _timed(seconds, "grade", grade, is_async=False)
    continuity._write = _timed(seconds, "write", originals["_write"], is_async=True)
    try:
        with _PeakRss() as rss:
            summary = await continuity.run_batch_continuity(sample_batch_id)
    finally:
        for name, fn in originals.items():
            setattr(continuity, name, fn)
    return {
        "pairs": summary.get("pairs"),
        "pairs_by_kind": summary.get("pairs_by_kind"),
        "pairs_graded": summary.get("pairs_graded"),
        "pairs_skipped": summary.get("pairs_skipped"),
        "time_ordered": summary.get("time_ordered"),
        "grades": summary.get("grades"),
        "anchor_grades": summary.get("anchors"),
        "written": summary.get("written"),
        **seen,
        "seconds": {name: round(value, 3) for name, value in seconds.items()},
        "seconds_total": summary.get("seconds"),
        "rss_base_mb": round(rss.base, 1),
        "rss_peak_over_base_mb": round(rss.peak - rss.base, 1),
        "skipped": summary.get("skipped"),
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("sample_batch_id")
    parser.add_argument("--passes", type=int, default=2)
    parser.add_argument("--clear", action="store_true")
    parser.add_argument("--max-pairs", type=int, default=None)
    args = parser.parse_args()

    await configure_database_engine()
    if args.max_pairs is not None:
        continuity.MAX_PAIRS_PER_BATCH = args.max_pairs
    sizes = await _sizes(args.sample_batch_id)
    if args.clear:
        await _clear(args.sample_batch_id)
    for number in range(1, args.passes + 1):
        reading = await _pass(args.sample_batch_id)
        print(
            json.dumps(
                {
                    "batch": args.sample_batch_id,
                    "pass": number,
                    "max_pairs": continuity.MAX_PAIRS_PER_BATCH,
                    **sizes,
                    **reading,
                }
            ),
            flush=True,
        )


if __name__ == "__main__":
    asyncio.run(main())
