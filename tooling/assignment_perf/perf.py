"""Time peak assignment's two uses on a dev env (MAS-1179).

Assignment is used two ways, and the two ask different things of its speed
(``docs/dev/assignment_performance.md``):

1. **Fold-in**: a sample arrives during continuous acquisition, is folded
   into a batch that already has its decisions, and the batch stages bring
   the batch back to consistent. :func:`fold_in` times one arrival.
2. **Whole batch**: **Assign batch** from nothing. :func:`whole_batch` times
   one pass with the fold first.

Both run in this process against an env's database and filestore, with the
backend's own code, so nothing else may write in that env while they run.
The batch's size is set by moving the samples past the first N, in
acquisition order, into a holding batch of the same dataset
(:func:`limit_batch`); an arrival moves the earliest held sample back.

What is measured, per step (the arrival's fold, the pass), per batch stage,
and per judging of a sample:

- wall time, and the process's CPU time beside it: CPU over wall is the
  number of cores busy, so 1.0 is one core working and the rest idle;
- the time spent executing SQL and the number of statements, with the reads
  that repeat a read already made in the same step (same statement, same
  parameters);
- the peak resident memory of the process while the step ran.

The stages' own timings are read where they exist (each stage's start and
end in the pass's run summary); the rest are timers this script puts around
the backend's functions without changing them.

Usage (from a checkout, ``MASCOPE_ENV`` naming a copy of the demo env)::

    uv run python tooling/assignment_perf/perf.py limit BATCH N
    uv run python tooling/assignment_perf/perf.py whole BATCH OUT.json
    uv run python tooling/assignment_perf/perf.py foldin BATCH OUT.json
    uv run python tooling/assignment_perf/perf.py arrivals BATCH OUT.json --count C --every K
    uv run python tooling/assignment_perf/perf.py table OUT.json
    uv run python tooling/assignment_perf/perf.py digest BATCH OUT.json

``README.md`` beside this file has the whole recipe.
"""

from __future__ import annotations

import argparse
import asyncio
import gc
import hashlib
import json
import os
import sys
import threading
import time
from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


#: The holding batch's name: the samples a limited batch leaves out wait there.
HOLDING_NAME = "perf holding (MAS-1179)"

#: Statements longer than this are cut in the record of the costliest ones.
STATEMENT_CHARS = 160


# --- the meter ---------------------------------------------------------------


@dataclass
class Scope:
    """One timed span: a step, a stage, a judging or a pipeline stage."""

    kind: str
    name: str
    meta: dict = field(default_factory=dict)
    wall0: float = 0.0
    cpu0: float = 0.0
    db0: float = 0.0
    queries0: int = 0
    rss_peak: int = 0
    reads: Counter = field(default_factory=Counter)
    repeat_s: float = 0.0
    statements: dict = field(default_factory=dict)


def _cpu() -> float:
    t = os.times()
    return t.user + t.system


def _rss() -> int:
    """The process's resident memory now, in bytes."""
    try:
        with open("/proc/self/statm") as fh:
            return int(fh.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except OSError:  # not Linux: no reading
        return 0


def statement_key(statement: str, parameters: Any) -> Optional[str]:
    """The key two identical reads share: the statement and its parameters.
    None for a statement that writes, which repeats by design."""
    words = statement.split(None, 1)
    if not words or words[0].upper() not in {"SELECT", "WITH"}:
        return None
    digest = hashlib.sha1(repr(parameters).encode(), usedforsecurity=False)
    return f"{hashlib.sha1(statement.encode(), usedforsecurity=False).hexdigest()}:{digest.hexdigest()}"


class Meter:
    """Counts the database and the memory under nested scopes.

    The flow it measures is one chain of awaits, so the scopes nest as a
    stack. SQL time is the time between SQLAlchemy's before and after cursor
    events, wherever the statement runs; a scope's share is the counter's
    rise while it was open.
    """

    def __init__(self) -> None:
        self.db_s = 0.0
        self.queries = 0
        self.stack: list[Scope] = []
        self.records: list[dict] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._sampler: Optional[threading.Thread] = None

    # The database --------------------------------------------------------

    def attach_database(self) -> None:
        from sqlalchemy import event
        from sqlalchemy.engine import Engine

        def before(conn, cursor, statement, parameters, context, executemany):
            conn.info.setdefault("perf_t0", []).append(time.perf_counter())

        def after(conn, cursor, statement, parameters, context, executemany):
            took = time.perf_counter() - conn.info["perf_t0"].pop()
            self.count(statement, parameters, took)

        event.listen(Engine, "before_cursor_execute", before)
        event.listen(Engine, "after_cursor_execute", after)

    def count(self, statement: str, parameters: Any, took: float) -> None:
        """One statement executed: its time to every open scope, a repeat of
        a read the scope already made counted as one, and the step's own
        record of its costliest statements."""
        key = statement_key(statement, parameters)
        with self._lock:
            self.db_s += took
            self.queries += 1
            for scope in self.stack:
                if key is not None:
                    if scope.reads[key]:
                        scope.repeat_s += took
                    scope.reads[key] += 1
                if scope.kind == "step":
                    text = " ".join(statement.split())[:STATEMENT_CHARS]
                    entry = scope.statements.setdefault(text, [0, 0.0])
                    entry[0] += 1
                    entry[1] += took

    # The memory ----------------------------------------------------------

    def start_sampler(self, every_s: float = 0.1) -> None:
        def sample() -> None:
            while not self._stop.wait(every_s):
                rss = _rss()
                with self._lock:
                    for scope in self.stack:
                        scope.rss_peak = max(scope.rss_peak, rss)

        self._sampler = threading.Thread(target=sample, daemon=True)
        self._sampler.start()

    def stop_sampler(self) -> None:
        self._stop.set()

    # The scopes ----------------------------------------------------------

    @contextmanager
    def scope(self, kind: str, name: str, **meta: Any):
        scope = Scope(
            kind=kind,
            name=name,
            meta=meta,
            wall0=time.perf_counter(),
            cpu0=_cpu(),
            db0=self.db_s,
            queries0=self.queries,
            rss_peak=_rss(),
        )
        with self._lock:
            self.stack.append(scope)
        parent = self.stack[-2] if len(self.stack) > 1 else None
        try:
            yield scope
        finally:
            with self._lock:
                self.stack.remove(scope)
            wall = time.perf_counter() - scope.wall0
            reads = sum(scope.reads.values())
            record = {
                "kind": kind,
                "name": name,
                "parent": f"{parent.kind}:{parent.name}" if parent else None,
                "wall_s": wall,
                "cpu_s": _cpu() - scope.cpu0,
                "db_s": self.db_s - scope.db0,
                "queries": self.queries - scope.queries0,
                "reads": reads,
                "repeat_reads": reads - len(scope.reads),
                "repeat_read_s": scope.repeat_s,
                "rss_peak_mb": scope.rss_peak / 2**20,
                **meta,
            }
            if kind == "step":
                record["top_statements"] = sorted(
                    ([t, n, s] for t, (n, s) in scope.statements.items()),
                    key=lambda r: -r[2],
                )[:15]
            self.records.append(record)


METER = Meter()


# --- the backend's functions, timed ------------------------------------------


#: The folds a pass and an arrival call, by the module that calls them: each
#: module holds its own name for the function, so each is wrapped where it is
#: called from.
FOLD_TARGETS = (
    ("batch_fold", "fold_sample_into_batch_peaks"),
    ("batch_fold", "fold_sample_peaks_without_run"),
    ("service", "fold_sample_peaks_without_run"),
)


def instrument() -> None:
    """Put a timer around the backend functions a pass and a fold run
    through. The functions themselves are not changed: each wrapper calls
    the original and records the span."""
    from mascope_backend.api.new.peak_assignments import (
        batch_fold,
        batch_rejudge,
        batch_stages,
        pipeline,
        service,
    )

    # Each pipeline stage (resolve, claim, propose, score, elect, ...), under
    # whichever judging or fold runs it.
    for stage, fn in list(pipeline.STAGE_FUNCTIONS.items()):

        def wrap(fn=fn, stage=stage):
            async def timed(ctx):
                with METER.scope("pipeline", stage.value):
                    return await fn(ctx)

            return timed

        pipeline.STAGE_FUNCTIONS[stage] = wrap()

    real_rejudge = batch_rejudge.rejudge_sample

    async def timed_rejudge(sample_batch_id, sample_item_id, config, **kw):
        with METER.scope("judging", sample_item_id) as scope:
            counts = await real_rejudge(sample_batch_id, sample_item_id, config, **kw)
            scope.meta["counts"] = dict(counts)
            return counts

    batch_rejudge.rejudge_sample = timed_rejudge

    real_run_stage = batch_stages._run_stage

    async def timed_stage(spec, call, *rest):
        with METER.scope("stage", spec.stage.value):
            return await real_run_stage(spec, call, *rest)

    batch_stages._run_stage = timed_stage

    real_judge_again = batch_stages.judge_again

    async def timed_judge_again(stage, *args, **kw):
        with METER.scope("judge_again", stage.value):
            return await real_judge_again(stage, *args, **kw)

    batch_stages.judge_again = timed_judge_again

    real_rebuild = batch_stages.rebuild_batch_ledger

    async def timed_rebuild(*args, **kw):
        with METER.scope("stage", "fold"):
            return await real_rebuild(*args, **kw)

    batch_stages.rebuild_batch_ledger = timed_rebuild

    for module, name in FOLD_TARGETS:
        module = {"batch_fold": batch_fold, "service": service}[module]
        real = getattr(module, name, None)
        if real is None:
            # A renamed fold would leave its rows empty and the run looking
            # whole: refuse it instead.
            raise SystemExit(
                f"{module.__name__}.{name} is gone: update FOLD_TARGETS in perf.py."
            )
        if not getattr(real, "_perf_wrapped", False):

            def wrap_fold(real=real, name=name):
                async def timed(sample_item_id, *args, **kw):
                    with METER.scope("sample_fold", sample_item_id, via=name):
                        return await real(sample_item_id, *args, **kw)

                timed._perf_wrapped = True
                return timed

            setattr(module, name, wrap_fold())


# --- the batch, its size and its samples --------------------------------------


async def _session():
    from mascope_backend.db import async_session

    return async_session()


async def acquisition_order(session, sample_batch_id: str) -> list[str]:
    """The batch's samples and the held ones, earliest acquisition first."""
    from sqlalchemy import text

    rows = await session.execute(
        text(
            "select s.sample_item_id from sample_item s "
            "join sample_file f using (sample_file_id) "
            "where s.sample_batch_id = :b or s.sample_batch_id = ("
            "  select h.sample_batch_id from sample_batch h "
            "  join sample_batch b on b.dataset_id = h.dataset_id "
            "  where b.sample_batch_id = :b and h.sample_batch_name = :h) "
            "order by f.datetime_utc, s.sample_item_id"
        ),
        {"b": sample_batch_id, "h": HOLDING_NAME},
    )
    return [r[0] for r in rows]


async def holding_batch(session, sample_batch_id: str) -> str:
    """The holding batch of the batch's dataset, made on first use."""
    from sqlalchemy import text

    from mascope_backend.db.id import gen_id

    found = (
        await session.execute(
            text(
                "select h.sample_batch_id from sample_batch h "
                "join sample_batch b on b.dataset_id = h.dataset_id "
                "where b.sample_batch_id = :b and h.sample_batch_name = :h"
            ),
            {"b": sample_batch_id, "h": HOLDING_NAME},
        )
    ).scalar()
    if found:
        return found
    new_id = gen_id(16)
    await session.execute(
        text(
            "insert into sample_batch (sample_batch_id, dataset_id, "
            "sample_batch_name, sample_batch_type, status, locked, polarity, "
            "sample_batch_utc_created, sample_batch_utc_modified) "
            "select :n, dataset_id, :h, sample_batch_type, status, 0, polarity, "
            "now(), now() from sample_batch where sample_batch_id = :b"
        ),
        {"n": new_id, "h": HOLDING_NAME, "b": sample_batch_id},
    )
    return new_id


async def limit_batch(sample_batch_id: str, n: int) -> list[str]:
    """Keep the batch's first ``n`` samples in acquisition order and move the
    rest into the holding batch; move held ones back where ``n`` asks for
    more. Meant for a batch with no ledger yet: it moves no member."""
    from sqlalchemy import text

    async with await _session() as session:
        order = await acquisition_order(session, sample_batch_id)
        held = await holding_batch(session, sample_batch_id)
        keep, hold = order[:n], order[n:]
        await session.execute(
            text(
                "update sample_item set sample_batch_id = :b where sample_item_id = any(:s)"
            ),
            {"b": sample_batch_id, "s": keep},
        )
        await session.execute(
            text(
                "update sample_item set sample_batch_id = :h where sample_item_id = any(:s)"
            ),
            {"h": held, "s": hold},
        )
        await session.commit()
    return keep


async def next_arrival(sample_batch_id: str) -> Optional[str]:
    """Move the earliest held sample into the batch: one arrival."""
    from sqlalchemy import text

    async with await _session() as session:
        order = await acquisition_order(session, sample_batch_id)
        members = set(
            (
                await session.execute(
                    text(
                        "select sample_item_id from sample_item where sample_batch_id = :b"
                    ),
                    {"b": sample_batch_id},
                )
            )
            .scalars()
            .all()
        )
        waiting = [s for s in order if s not in members]
        if not waiting:
            return None
        await session.execute(
            text(
                "update sample_item set sample_batch_id = :b where sample_item_id = :s"
            ),
            {"b": sample_batch_id, "s": waiting[0]},
        )
        await session.commit()
        return waiting[0]


# --- what the ledger decided --------------------------------------------------


def _compared(key: str) -> bool:
    """A field two copies of one dataset can share: not an id, a version or a
    time, nor a per-file list, which grows with every arrival and whose
    outcome is the member's own tier."""
    return (
        not key.endswith("_id")
        and "version" not in key
        and "utc" not in key
        and key not in PER_FILE_KEYS
        and key not in NESTED_READINGS
    )


#: A record's lists of what each file or sample did, left out of the
#: comparison.
PER_FILE_KEYS = frozenset(
    {"files_recorded", "rows_kept", "displaced_members", "claimed_samples"}
)

#: Readings nested in a record: a known-species claim's co-variation test is
#: the co-variation's reading (decision 35), not the claim's decision.
NESTED_READINGS = frozenset({"co_variation"})

#: Records read by their words alone, top level included: their counts are
#: of the files that hold the species or the rung, and grow with each arrival.
WORDS_ONLY_RECORDS = frozenset(
    {"known_species", "water_ladder", "merged_isotopologues"}
)


def _nested_words(value: Any) -> Any:
    """A record nested in a record (a batch check's fact, a predicted
    isotopologue line), by its words and flags alone: its verdict, reason,
    label, ``stamped``, ``claimed``. Its numbers, counts included, are what
    each sample read moves (an r, a share, the files holding it)."""
    if isinstance(value, dict):
        out = {}
        for k, v in sorted(value.items()):
            if not _compared(k):
                continue
            v = _nested_words(v)
            if v is not None and v != {} and v != []:
                out[k] = v
        return out
    if isinstance(value, list):
        return [_nested_words(v) for v in value]
    return value if isinstance(value, (str, bool)) else None


def _record_shape(value: Any) -> Any:
    """A provenance record as two databases can compare it: its scalar
    fields, and the words of the records nested in it, without ids,
    versions or times."""
    if not isinstance(value, dict):
        return value is not None
    out = {}
    for k, v in sorted(value.items()):
        if not _compared(k):
            continue
        if isinstance(v, (str, int, float, bool)):
            out[k] = v
        elif isinstance(v, (dict, list)):
            out[k] = _nested_words(v)
    return out


def record_of(key: str, value: Any) -> Any:
    """A batch peak's record ``key`` as the digest keeps it."""
    if key in WORDS_ONLY_RECORDS:
        return _nested_words(value) if value is not None else False
    return _record_shape(value)


#: The batch peak's records a digest reads (``batch_peaks``).
RECORD_KEYS = (
    "series",
    "co_variation",
    "batch_checks",
    "diagnostic_isotopologues",
    "known_species",
    "water_ladder",
    "merged_isotopologues",
)

#: The records that hold a decision. The co-variation is a reading, shown
#: and never acted on (decision 35), and its numbers move with every sample;
#: the flat-series flag is display only (decision 36) and is not read.
DECISION_RECORDS = (
    "series",
    "batch_checks",
    "diagnostic_isotopologues",
    "known_species",
    "water_ladder",
    "merged_isotopologues",
)


def decision_of(records: Any) -> Any:
    """What of a member's records is a decision: the series, batch checks
    and stamped lines, by their words and counts. Measured numbers (an r, a
    ppm, a ratio) are left out: they move with each sample read."""
    if not isinstance(records, dict):
        return records
    out = {}
    for key in DECISION_RECORDS:
        value = records.get(key)
        if isinstance(value, dict):
            value = {k: v for k, v in value.items() if not isinstance(v, float)}
        out[key] = value
    return out


async def ledger_digest(sample_batch_id: str) -> dict[str, list]:
    """Each member's decision, keyed by its sample and peak, which two copies
    of one dataset share: the member's tier, role and candidate, and its batch
    peak's consensus formula, ion, tier, isotope line and records."""
    from sqlalchemy import text

    async with await _session() as session:
        rows = await session.execute(
            text(
                "select o.sample_item_id, o.sample_peak_id, o.tier, o.role, "
                "o.candidate, b.consensus_formula, b.consensus_ion_formula, "
                "b.consensus_tier, b.engine_tier, b.isotopologue_of is not null, "
                "b.consensus_role, b.provenance::text "
                "from batch_peak_occurrence o join batch_peak b using (batch_peak_id) "
                "where b.sample_batch_id = :b"
            ),
            {"b": sample_batch_id},
        )
        digest = {}
        for r in rows:
            provenance = json.loads(r[11]) if r[11] else {}
            records = {k: record_of(k, provenance.get(k)) for k in RECORD_KEYS}
            digest[f"{r[0]}/{r[1]}"] = [*map(_plain, r[2:11]), records]
    return digest


def _plain(value: Any) -> Any:
    return (
        value
        if isinstance(value, (str, int, float, bool)) or value is None
        else str(value)
    )


#: The fields of a digest entry, in order.
DIGEST_FIELDS = (
    "tier",
    "role",
    "candidate",
    "consensus_formula",
    "consensus_ion",
    "consensus_tier",
    "engine_tier",
    "isotope_line",
    "consensus_role",
    "records",
)

#: The decisions an arrival may change, and the digest fields each reads.
DECISIONS = {
    "consensus formula": ("consensus_formula", "consensus_ion"),
    "tier": ("tier", "consensus_tier", "engine_tier"),
    "isotope line": ("isotope_line",),
    "series and records": ("records",),
}


def _field(entry: list, i: int, name: str) -> Any:
    return decision_of(entry[i]) if name == "records" else entry[i]


def digest_changes(before: dict, after: dict, only: Optional[set] = None) -> dict:
    """How many members' decisions differ between two digests, per decision,
    over the members both hold (or ``only`` those)."""
    keys = set(before) & set(after)
    if only is not None:
        keys &= only
    changed: dict[str, int] = {name: 0 for name in DECISIONS}
    any_changed = 0
    index = {name: i for i, name in enumerate(DIGEST_FIELDS)}
    for key in keys:
        a, b = before[key], after[key]
        moved = False
        for name, fields in DECISIONS.items():
            if any(_field(a, index[f], f) != _field(b, index[f], f) for f in fields):
                changed[name] += 1
                moved = True
        any_changed += moved
    return {"members_compared": len(keys), "members_changed": any_changed, **changed}


async def landing(
    sample_batch_id: str, sample_item_id: str, decided_before: set, existed_before: set
) -> dict:
    """Where an arrival's peaks landed: on a batch peak that already had a
    decision, on one that existed without one, or on a new one."""
    from sqlalchemy import text

    async with await _session() as session:
        rows = await session.execute(
            text(
                "select o.batch_peak_id from batch_peak_occurrence o "
                "join batch_peak b using (batch_peak_id) "
                "where b.sample_batch_id = :b and o.sample_item_id = :s"
            ),
            {"b": sample_batch_id, "s": sample_item_id},
        )
        ids = [r[0] for r in rows]
    return {
        "peaks": len(ids),
        "on_decided": sum(1 for i in ids if i in decided_before),
        "on_undecided": sum(
            1 for i in ids if i in existed_before and i not in decided_before
        ),
        "new": sum(1 for i in ids if i not in existed_before),
    }


async def batch_peaks_now(sample_batch_id: str) -> tuple[set, set]:
    """The batch's batch peaks, and those with a consensus formula."""
    from sqlalchemy import text

    async with await _session() as session:
        rows = (
            await session.execute(
                text(
                    "select batch_peak_id, consensus_formula is not null from batch_peak where sample_batch_id = :b"
                ),
                {"b": sample_batch_id},
            )
        ).all()
    return {r[0] for r in rows}, {r[0] for r in rows if r[1]}


async def newest_pass(sample_batch_id: str) -> tuple[Optional[str], Optional[dict]]:
    """The newest pass's run id and the stages' own record of it: each
    stage's start and end (``batch_stages.PassRecord``)."""
    from sqlalchemy import text

    async with await _session() as session:
        found = (
            await session.execute(
                text(
                    "select batch_peak_run_id, summary::text from batch_peak_run "
                    "where sample_batch_id = :b "
                    "order by batch_peak_run_utc_created desc limit 1"
                ),
                {"b": sample_batch_id},
            )
        ).first()
    if found is None:
        return None, None
    return found[0], json.loads(found[1]) if found[1] else None


def stage_spans(summary: Optional[dict]) -> dict[str, float]:
    """Seconds per stage, from the times the pass recorded itself."""
    from datetime import datetime

    spans = {}
    for entry in (summary or {}).get("stages", []) or []:
        start, end = entry.get("utc_started"), entry.get("utc_ended")
        if start and end:
            spans[entry["stage"]] = (
                datetime.fromisoformat(end) - datetime.fromisoformat(start)
            ).total_seconds()
    return spans


# --- the two uses -------------------------------------------------------------


async def _start() -> None:
    from mascope_backend.db import configure_database_engine

    await configure_database_engine()
    instrument()
    METER.attach_database()
    METER.start_sampler()


async def _batch_size(sample_batch_id: str) -> int:
    from sqlalchemy import text

    async with await _session() as session:
        return (
            await session.execute(
                text("select count(*) from sample_item where sample_batch_id = :b"),
                {"b": sample_batch_id},
            )
        ).scalar()


#: The one status of a pass this script measures: every stage ran and ended
#: current (``batch_stages``' outcome; "partial", "cancelled" and "skipped"
#: are the others).
PASS_DONE = "success"


async def run_pass(sample_batch_id: str, fold: str) -> dict:
    """One pass of the batch stages, timed as one step. Its stages are read
    from the run it opened: a pass that opened none has no stages of its own."""
    from mascope_backend.api.new.peak_assignments import batch_stages

    before, _ = await newest_pass(sample_batch_id)
    with METER.scope("step", "pass", fold=fold) as scope:
        outcome = await batch_stages.assign_batch(
            sample_batch_id, fold=fold, independent_transaction=True
        )
        scope.meta["status"] = outcome.get("status")
    run_id, summary = await newest_pass(sample_batch_id)
    opened = run_id is not None and run_id != before
    return {
        "status": outcome.get("status"),
        "message": outcome.get("message"),
        "stage_spans": stage_spans(summary) if opened else {},
    }


def refusal(result: dict) -> Optional[str]:
    """Why a run is no measurement, or None: an arrival that landed no peak
    (ingest skipped or swallowed its fold), or a pass that did not finish."""
    steps = result.get("arrivals") or [result]
    for entry in steps:
        land = entry.get("landing")
        if land is not None and not land.get("peaks"):
            return (
                f"the arrival of {entry.get('sample')} landed no peak in the batch: "
                "ingest skipped or failed its fold (see the log above)"
            )
        run = entry.get("pass")
        if run is not None and run.get("status") != PASS_DONE:
            return f"a pass ended {run.get('status')!r}: {run.get('message')}"
    return None


async def arrive(sample_batch_id: str) -> tuple[str, dict]:
    """One sample arrives as ingest folds it (``auto_assign_sample_peaks``).
    Ingest swallows a failed fold and skips one it may not run, so the
    landing is what tells whether the fold happened."""
    from mascope_backend.api.new.peak_assignments import service

    existed, decided = await batch_peaks_now(sample_batch_id)
    sample = await next_arrival(sample_batch_id)
    if sample is None:
        raise SystemExit("No held sample is left to arrive.")
    with METER.scope("step", "arrival fold", sample=sample):
        await service.auto_assign_sample_peaks(sample)
    return sample, await landing(sample_batch_id, sample, decided, existed)


def _environment() -> dict:
    import subprocess

    def git(*args):
        try:
            return subprocess.run(
                ["git", *args], capture_output=True, text=True, check=True
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            return None

    load = os.getloadavg() if hasattr(os, "getloadavg") else None
    return {
        "commit": git("rev-parse", "--short=9", "HEAD"),
        "env": os.environ.get("MASCOPE_ENV"),
        "cpus": os.cpu_count(),
        "load_at_start": load,
        "utc_started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def _write(out: str, result: dict) -> None:
    """Write the run and print its tables; then refuse it, non-zero, if a
    step did not do what it was timed for. The file is kept for the reading."""
    result["records"] = METER.records
    why = refusal(result)
    result["refused"] = why
    Path(out).write_text(json.dumps(result, indent=1, default=str))
    print(render(result), flush=True)
    if why:
        raise SystemExit(f"Not a measurement: {why}.")


async def whole_batch(sample_batch_id: str, out: str) -> None:
    """**Assign batch** from nothing: fold every sample, then every stage."""
    await _start()
    n = await _batch_size(sample_batch_id)
    result = {"use": "whole", "n": n, **_environment()}
    result["pass"] = await run_pass(sample_batch_id, fold="always")
    result["digest"] = await ledger_digest(sample_batch_id)
    _write(out, result)


def spill(path: Path, digest: dict) -> str:
    """Write a digest beside the run's output and hand back its path, so the
    script holds no copy of the ledger while a pass runs: the pass's peak
    memory is then the backend's alone. About 0.2 GB at N = 80."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(digest))
    return str(path)


def unspill(digest: Any) -> dict:
    """A digest, read back from where :func:`spill` wrote it if it is a path."""
    return json.loads(Path(digest).read_text()) if isinstance(digest, str) else digest


def _spill_dir(out: str) -> Path:
    return Path(f"{out}.digests")


async def fold_in(sample_batch_id: str, out: str) -> None:
    """One more sample into a batch of N that a pass left consistent: its
    fold, then the pass that brings the batch back to consistent."""
    await _start()
    n = await _batch_size(sample_batch_id)
    before = spill(
        _spill_dir(out) / "before.json", await ledger_digest(sample_batch_id)
    )
    gc.collect()
    result = {"use": "foldin", "n": n, **_environment()}
    sample, landed = await arrive(sample_batch_id)
    result["sample"] = sample
    result["landing"] = landed
    result["pass"] = await run_pass(sample_batch_id, fold="auto")
    after = await ledger_digest(sample_batch_id)
    result["changes"] = digest_changes(unspill(before), after)
    result["digest"] = after
    _write(out, result)


async def arrivals(sample_batch_id: str, out: str, count: int, every: int) -> None:
    """``count`` arrivals one after another, a pass after every ``every``-th
    and after the last (never, with ``every`` 0: the light fold alone); the
    ledger's decisions read after each arrival. Each arrival's digest waits
    on disk until the last, and the output holds them all."""
    await _start()
    result = {
        "use": "arrivals",
        "n": await _batch_size(sample_batch_id),
        "every": every,
        **_environment(),
        "arrivals": [],
    }
    spilled = _spill_dir(out)
    for i in range(1, count + 1):
        before = spill(
            spilled / f"before-{i}.json", await ledger_digest(sample_batch_id)
        )
        gc.collect()
        sample, landed = await arrive(sample_batch_id)
        entry = {"i": i, "sample": sample, "landing": landed}
        if every and (i % every == 0 or i == count):
            entry["pass"] = await run_pass(sample_batch_id, fold="auto")
        after = await ledger_digest(sample_batch_id)
        # What the arrival and its pass (if any) changed of the decisions the
        # batch held before it came: its own members are new, not changed.
        entry["changes"] = digest_changes(unspill(before), after)
        entry["digest"] = spill(spilled / f"after-{i}.json", after)
        del after
        gc.collect()
        result["arrivals"].append(entry)
        print(
            f"arrival {i}: {sample} {json.dumps(landed)} {json.dumps(entry['changes'])}",
            flush=True,
        )
        Path(out).write_text(
            json.dumps({**result, "records": METER.records}, default=str)
        )
        if refusal(result):
            break
    for entry in result["arrivals"]:
        entry["digest"] = unspill(entry["digest"])
    _write(out, result)


async def write_digest(sample_batch_id: str, out: str) -> None:
    """The ledger's decisions as they stand, as a run's output ``diff``
    reads: to compare two envs' ledgers without a run in either."""
    from mascope_backend.db import configure_database_engine

    await configure_database_engine()
    digest = await ledger_digest(sample_batch_id)
    Path(out).write_text(
        json.dumps({"use": "digest", **_environment(), "digest": digest})
    )
    print(f"{len(digest)} members")


# --- reading saved runs again --------------------------------------------------


def last_digest(result: dict) -> dict:
    """The ledger a saved run left: its digest, or its last arrival's (a run
    stopped midway holds the path of each arrival's spilled digest)."""
    if result.get("arrivals"):
        return unspill(result["arrivals"][-1]["digest"])
    return unspill(result["digest"])


def recount(result: dict, start: dict) -> dict:
    """Count a saved run's changes again from its digests: each arrival
    against the ledger before it, the first against ``start``'s."""
    before = last_digest(start)
    for entry in result.get("arrivals") or [result]:
        after = unspill(entry["digest"])
        entry["changes"] = digest_changes(before, after)
        before = after
    return result


def converge(every_time: dict, other: dict) -> list[dict]:
    """Per arrival, how many members' decisions differ between a sequence
    with a pass after every arrival and one with fewer passes. Only members
    both hold are compared."""
    rows = []
    for a, b in zip(every_time["arrivals"], other["arrivals"]):
        rows.append(
            {
                "i": a["i"],
                "pass": b.get("pass") is not None,
                **digest_changes(unspill(a["digest"]), unspill(b["digest"])),
            }
        )
    return rows


def converge_table(rows: list[dict], every: int) -> str:
    lines = [
        f"| arrival | pass here (every {every}) | members compared | differ | formula | tier "
        "| isotope line | series and records |",
        "| -- | -- | -- | -- | -- | -- | -- | -- |",
    ]
    for r in rows:
        lines.append(
            f"| {r['i']} | {'yes' if r['pass'] else 'no'} | {r['members_compared']} | "
            f"{r['members_changed']} | {r['consensus formula']} | {r['tier']} | "
            f"{r['isotope line']} | {r['series and records']} |"
        )
    return "\n".join(lines)


# --- the table ------------------------------------------------------------------


def _f(value: Any, digits: int = 1) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:,.{digits}f}"
    return f"{value:,}" if isinstance(value, int) else str(value)


def _row(r: dict, label: str) -> str:
    cores = r["cpu_s"] / r["wall_s"] if r["wall_s"] else 0.0
    return (
        f"| {label} | {_f(r['wall_s'])} | {_f(r['cpu_s'])} | {cores:.2f} | "
        f"{_f(r['db_s'])} | {_f(r['queries'])} | {_f(r['repeat_reads'])} | "
        f"{_f(r['repeat_read_s'])} | {_f(r['rss_peak_mb'], 0)} |"
    )


HEADER = (
    "| {first} | wall s | CPU s | cores | SQL s | statements | repeated reads "
    "| repeated reads s | peak RSS MB |\n| -- | -- | -- | -- | -- | -- | -- | -- | -- |"
)


def _landing_table(arrivals: list[dict]) -> str:
    """Per arrival: where its peaks landed, whether a pass ran, and how many
    of the members the batch held before it changed a decision."""
    lines = [
        "| arrival | peaks | on a decided batch peak | on an undecided one | new | pass | "
        "members compared | changed | formula | tier | isotope line | series and records |",
        "| -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |",
    ]
    for a in arrivals:
        land = a.get("landing") or {}
        ch = a.get("changes") or {}
        ran = "yes" if a.get("pass") is not None else "no"
        lines.append(
            f"| {a.get('i')} | {land.get('peaks', '-')} | {land.get('on_decided', '-')} | "
            f"{land.get('on_undecided', '-')} | {land.get('new', '-')} | {ran} | "
            f"{ch.get('members_compared', '-')} | {ch.get('members_changed', '-')} | "
            f"{ch.get('consensus formula', '-')} | {ch.get('tier', '-')} | "
            f"{ch.get('isotope line', '-')} | {ch.get('series and records', '-')} |"
        )
    return "\n".join(lines)


def step_status(record: dict, landed: dict) -> str:
    """What a step did, beside its time: a pass's status, an arrival's
    landed peaks. A failed step pasted without it reads as a fast one."""
    if record["name"] == "pass":
        return record.get("status") or "status unknown"
    land = landed.get(record.get("sample"))
    return f"{land['peaks']} peaks landed" if land else "landing unknown"


def render(result: dict) -> str:
    """The run as Markdown: the steps, the stages, and inside the judgings.

    Pasted as it is into a PR body or the note."""
    records = result.get("records", [])
    lines = [
        f"**{result.get('use')}**, N = {result.get('n')}, commit "
        f"`{result.get('commit')}`, {result.get('utc_started')}, "
        f"load {_f((result.get('load_at_start') or [None])[0])} on {result.get('cpus')} CPUs",
        "",
        HEADER.format(first="step"),
    ]
    if result.get("refused"):
        lines[1:1] = ["", f"**Not a measurement:** {result['refused']}."]
    landed = {
        e.get("sample"): e.get("landing") for e in result.get("arrivals") or [result]
    }
    for r in records:
        if r["kind"] == "step":
            lines.append(_row(r, f"{r['name']} ({step_status(r, landed)})"))
    stages = [r for r in records if r["kind"] == "stage"]
    if stages:
        judged = defaultdict(lambda: [0.0, 0])
        for r in records:
            if r["kind"] == "judge_again":
                judged[r["name"]][0] += r["wall_s"]
        for r in records:
            if (
                r["kind"] == "judging"
                and r["parent"]
                and r["parent"].startswith("judge_again:")
            ):
                judged[r["parent"].split(":", 1)[1]][1] += 1
        lines += [
            "",
            HEADER.format(first="stage")
            .replace("| wall s |", "| wall s | of it judging s | samples judged |", 1)
            .replace("| -- | -- |", "| -- | -- | -- | -- |", 1),
        ]
        for r in stages:
            j = judged.get(r["name"], [0.0, 0])
            row = _row(r, r["name"]).split(" | ")
            row.insert(2, f"{_f(j[0])} | {j[1]}")
            lines.append(" | ".join(row))
    inner = defaultdict(lambda: [0.0, 0.0, 0])
    for r in records:
        if r["kind"] == "pipeline":
            where = (r["parent"] or "").split(":", 1)[0]
            key = (where, r["name"])
            inner[key][0] += r["wall_s"]
            inner[key][1] += r["db_s"]
            inner[key][2] += 1
    if inner:
        lines += [
            "",
            "| in | pipeline stage | wall s | SQL s | calls |",
            "| -- | -- | -- | -- | -- |",
        ]
        for (where, name), (wall, db, calls) in sorted(
            inner.items(), key=lambda kv: -kv[1][0]
        ):
            lines.append(f"| {where} | {name} | {_f(wall)} | {_f(db)} | {calls} |")
    if result.get("landing"):
        lines += ["", _landing_table([{"i": 1, **result}])]
    if result.get("arrivals"):
        lines += ["", _landing_table(result["arrivals"])]
    folds = [
        r
        for r in records
        if r["kind"] == "sample_fold"
        and r.get("via") == "fold_sample_peaks_without_run"
    ]
    judgings = [r for r in records if r["kind"] == "judging"]
    if folds or judgings:
        lines += [
            "",
            "| per sample | count | median s | max s | total s |",
            "| -- | -- | -- | -- | -- |",
        ]
        for label, rs in (("fold without a run", folds), ("judging", judgings)):
            if rs:
                walls = sorted(r["wall_s"] for r in rs)
                lines.append(
                    f"| {label} | {len(rs)} | {_f(walls[len(walls) // 2])} | "
                    f"{_f(walls[-1])} | {_f(sum(walls))} |"
                )
    return "\n".join(lines)


# --- the command line ---------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("limit", help="keep the batch's first N samples")
    p.add_argument("batch")
    p.add_argument("n", type=int)
    for name in ("whole", "foldin"):
        p = sub.add_parser(name)
        p.add_argument("batch")
        p.add_argument("out")
    p = sub.add_parser("arrivals")
    p.add_argument("batch")
    p.add_argument("out")
    p.add_argument("--count", type=int, required=True)
    p.add_argument("--every", type=int, default=1)
    p = sub.add_parser("digest", help="write the ledger's decisions as they stand")
    p.add_argument("batch")
    p.add_argument("out")
    p = sub.add_parser("table", help="print a run's tables again")
    p.add_argument("out")
    p.add_argument("--start", help="the run before it, to count its changes again")
    p = sub.add_parser("diff", help="how many decisions two runs' ledgers differ in")
    p.add_argument("one")
    p.add_argument("two")
    p = sub.add_parser(
        "converge", help="arrivals with fewer passes against every arrival's"
    )
    p.add_argument("every_time")
    p.add_argument("other")
    args = parser.parse_args(argv)

    if args.command == "table":
        result = json.loads(Path(args.out).read_text())
        if args.start:
            result = recount(result, json.loads(Path(args.start).read_text()))
        print(render(result))
        return
    if args.command == "diff":
        one, two = (json.loads(Path(f).read_text()) for f in (args.one, args.two))
        print(json.dumps(digest_changes(last_digest(one), last_digest(two))))
        return
    if args.command == "converge":
        other = json.loads(Path(args.other).read_text())
        rows = converge(json.loads(Path(args.every_time).read_text()), other)
        print(converge_table(rows, other.get("every")))
        return
    if not os.environ.get("MASCOPE_ENV"):
        raise SystemExit("Set MASCOPE_ENV to the env this measures: it writes there.")
    os.environ.setdefault("MASCOPE_PEAK_ASSIGNMENT", "1")
    if args.command == "limit":

        async def limit() -> list[str]:
            from mascope_backend.db import configure_database_engine

            await configure_database_engine()
            return await limit_batch(args.batch, args.n)

        kept = asyncio.run(limit())
        print(f"{len(kept)} samples in the batch")
    elif args.command == "whole":
        asyncio.run(whole_batch(args.batch, args.out))
    elif args.command == "digest":
        asyncio.run(write_digest(args.batch, args.out))
    elif args.command == "foldin":
        asyncio.run(fold_in(args.batch, args.out))
    else:
        asyncio.run(arrivals(args.batch, args.out, args.count, args.every))
    METER.stop_sampler()


if __name__ == "__main__":
    sys.exit(main())
