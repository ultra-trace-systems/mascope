"""``bench run``: a ref's in-app engine on the bench sets, beside the pinned reference.

    bench.py run <ref> [--sets A,D,...] [--decoys] [--baseline] [--no-wait] [--jobs N] [--budget-min M] [--workers N]
    bench.py baseline [CARD]

The command the bench exists for (step 6.5 of the assignment quality plan).
One run, behind the bench's lock:

1. It checks the ref out into the bench's worktree (``uv sync --frozen``). It
   migrates the bench database forward with the ref's alembic, and refuses a
   ref that does not know the database's revision: a later build migrated it.
   It starts the ref's backend on the bench slot with peak assignment on and
   ``--workers`` uvicorn workers.
2. It refuses to read a sample whose reference run is missing, or is not the
   run the pinned reference published (``bench reference``).
3. It runs the in-app engine on every other sample of the chosen sets with the
   plan's default run config, an assign request with no config: the engine's
   own defaults. With ``--decoys`` it then runs the decoy arms of the set's
   instrument class (step 5.2) against that run as their control. It waits for
   every run, within a time budget per set.
4. It runs ``compare_runs.py --card --gate`` (``--decoy``) over the sets read,
   one card per set and one pooled, against the reference's runs; one that
   writes nothing for half an hour is taken for hung and stopped. With
   ``--baseline`` the card is diffed against develop's rolling baseline card,
   and a run of develop's head with ``--baseline`` becomes the next baseline.
5. It scrubs the card again with the bench's whole deny list, the instance's
   own names and ids included, before anything is written.
6. It stops the ref's backend and removes its worktree. The instance stays.

Each run writes ``cards/<ref>/<timestamp>/`` in the bench's home: the card
(``card.md``, ``card.json`` and one pair per set), the diff against the
baseline (``diff.md``, ``diff.json``) and the run's summary (``run.json``),
all scrubbed; and the private ``run.log`` and ``work/``, which name samples
and never leave the box.

This module holds the run; ``bench.py`` gives it the instance, the state and
the lock.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol


_HERE = Path(__file__).resolve().parent
REPO = _HERE.parents[1]
COMPARE = REPO / "tooling" / "assignment_compare" / "compare_runs.py"

#: The in-app engine's name on its runs, and the reference's.
ENGINE = "mascope"
REFERENCE_ENGINE = "peaky"

#: The plan's default run config: none, so the engine takes its own defaults,
#: as the gate's runs did.
DEFAULT_CONFIG: dict | None = None

#: The decoy arms of step 5.2 per instrument class: the axis shifted 9 ppm
#: either way on an Orbitrap, 0.35 Da on a TOF (inside whose window a ppm
#: shift is refused), and the adducts swapped on both.
DECOY_ARMS: dict[str, tuple[dict, ...]] = {
    "orbitrap": ({"mz_shift_ppm": 9}, {"mz_shift_ppm": -9}, {"swap_adducts": True}),
    "tof": ({"mz_shift_da": 0.35}, {"swap_adducts": True}),
}

#: A run's statuses nothing moves on from.
TERMINAL = {"completed", "failed", "cancelled"}

#: How the engine says a decoy it refused at run time, in the run's error.
DECOY_REFUSAL = "no decoy:"

#: Samples run at once, minutes a set may take, seconds between two looks.
JOBS = 4
BUDGET_MIN = 120.0
POLL_S = 10.0

#: The ref's backend's uvicorn workers: one per sample run at once, since an
#: engine run and the card's detail reads are CPU-bound in their worker.
WORKERS = 4

#: Minutes the comparison may go without writing a line before it is taken
#: for hung and stopped. It writes one per sample it reads; its whole run
#: grows with the rows it reads, so it has no budget of its own.
COMPARE_STALL_MIN = 30.0

#: How often a request that found the server busy or unreachable is sent again.
TRANSIENT_TRIES = 6

#: ``run.json``'s format.
SUMMARY_FORMAT = 1

#: Why a sample is left off the card, in the order the summary lists them.
LEFT_OUT = (
    "not_ready",
    "no_reference",
    "reference_moved",
    "ineligible",
    "failed",
    "not_latest",
    "over_budget",
)

#: What a run writes that may leave the box, every one scrubbed.
OUTPUTS = ("diff.md", "diff.json", "run.json")


class RunError(RuntimeError):
    """A step of ``bench run`` that cannot go on; the message says why."""


class TransientError(RuntimeError):
    """The server did not answer, or answered 5xx: the same request may pass later."""


# ---------------------------------------------------------------------- log

#: Open run logs every line of ``bench.log`` is copied into (``RunLog.tee``).
TEES: list["RunLog"] = []


def now() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


class RunLog:
    """The run's private log: what the console shows, and what it must not.

    ``say`` prints a line and writes it; ``note`` only writes it. Sample ids,
    file names and the server's own error texts go through ``note`` alone:
    the console is what an agent copies into a PR.
    """

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._fh = None

    def __enter__(self) -> "RunLog":
        self._fh = open(self.path, "a", encoding="utf-8")
        os.chmod(self.path, 0o600)
        TEES.append(self)
        return self

    def __exit__(self, kind, error, trace) -> None:
        if error is not None:
            self.note(f"stopped: {kind.__name__}: {error}")
        TEES.remove(self)
        self._fh.close()
        self._fh = None

    def write(self, line: str) -> None:
        with self._lock:
            if self._fh is not None:
                self._fh.write(line + "\n")
                self._fh.flush()

    def say(self, message: str) -> None:
        line = f"{datetime.datetime.now():%H:%M:%S} {message}"
        print(line, flush=True)
        self.write(line)

    def note(self, message: str) -> None:
        self.write(f"{datetime.datetime.now():%H:%M:%S}   {message}")


# ---------------------------------------------------------------------- api


@dataclass
class Launch:
    """What an assign request came back with.

    :ivar run_id: The run it created (202).
    :ivar busy: Another run of the sample is in flight (409): ask again later.
    :ivar code: The server's code for a refusal (``sample_ineligible``,
        ``decoy_refused``), or ``http_<status>``.
    :ivar reason: The server's reason for it.
    """

    run_id: str | None = None
    busy: bool = False
    code: str | None = None
    reason: str | None = None


class RunApi(Protocol):
    """What a run asks of the instance's API (a fake in the tests)."""

    def assign(self, sample: str, config: dict | None) -> Launch:
        """Launch a run; :class:`TransientError` when the server did not answer."""

    def runs(self, sample: str, include_decoys: bool = False) -> list[dict]:
        """The sample's runs, newest first; :class:`TransientError` as above."""


class TokenApi:
    """The two routes a run needs, over HTTP with the bench's SDK token.

    A token outlives a signed-in session, which a run of hours may not.
    """

    def __init__(self, base: str, token: str):
        import requests

        self._requests = requests
        self.base = base.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update(
            {"Authorization": f"Bearer {token}", "X-Service-Name": "mascope_sdk"}
        )

    def _send(self, method: str, path: str, **kwargs):
        try:
            r = self.session.request(
                method, f"{self.base}{path}", timeout=120, **kwargs
            )
        except self._requests.RequestException as error:
            raise TransientError(f"{method} {path}: {error}") from None
        if r.status_code >= 500:
            raise TransientError(f"{method} {path}: {r.status_code}")
        return r

    def assign(self, sample: str, config: dict | None) -> Launch:
        r = self._send(
            "POST",
            f"/api/peak-assignments/sample/{sample}/assign",
            json={} if config is None else {"config": config},
        )
        return launch_of(r.status_code, _json(r))

    def runs(self, sample: str, include_decoys: bool = False) -> list[dict]:
        r = self._send(
            "GET",
            f"/api/peak-assignments/sample/{sample}/runs",
            params={"include_decoys": "true"} if include_decoys else None,
        )
        if r.status_code != 200:
            raise RunError(f"the runs of a sample answered {r.status_code}")
        body = _json(r)
        rows = body.get("data", body) if isinstance(body, dict) else body
        return rows or []


def _json(response) -> dict:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {"data": body}


def launch_of(status: int, body: dict) -> Launch:
    """An assign answer as a :class:`Launch`.

    202 carries the run; 409 is another run of the sample in flight; a 422
    names its code (``sample_ineligible``, ``decoy_refused``) and reason in
    ``detail``; anything else is refused as ``http_<status>``.
    """
    if status == 202:
        return Launch(run_id=body["data"][0]["peak_assignment_run_id"])
    detail = body.get("detail")
    detail = detail if isinstance(detail, dict) else {}
    code = detail.get("code") or f"http_{status}"
    if status == 409:
        return Launch(busy=True, code=code)
    return Launch(code=code, reason=detail.get("reason") or body.get("error"))


def read_env_file(path: Path) -> dict[str, str]:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            out[key.strip()] = value.strip()
    return out


# --------------------------------------------------------------------- plan


def arm_name(arm: dict) -> str:
    """An arm's name as the run row records it (the server's ``PeakAssignmentDecoy.arm``)."""
    if arm.get("mz_shift_ppm") is not None:
        return f"mz_shift_ppm={arm['mz_shift_ppm']:+g}"
    if arm.get("mz_shift_da") is not None:
        return f"mz_shift_da={arm['mz_shift_da']:+g}"
    return "swap_adducts"


def ref_slug(ref: str) -> str:
    """A ref as one folder name: ``origin/develop`` is ``origin-develop``."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", ref).strip("-.")
    return slug or "ref"


@dataclass
class SetPlan:
    """One set of a run: the samples to run, and those left off the card, by why."""

    name: str
    instrument: str
    total: int = 0
    samples: list[str] = field(default_factory=list)
    reference: dict[str, str] = field(default_factory=dict)
    left_out: dict[str, list[tuple[str, str]]] = field(default_factory=dict)

    def leave_out(self, reason: str, sample: str, why: str) -> None:
        if sample in self.samples:
            self.samples.remove(sample)
        self.left_out.setdefault(reason, []).append((sample, why))

    def counts(self) -> dict[str, int]:
        return {r: len(self.left_out[r]) for r in LEFT_OUT if self.left_out.get(r)}

    def snapshot(self) -> dict[str, int]:
        """How many each reason has left out so far."""
        return {r: len(rows) for r, rows in self.left_out.items()}


def choose_sets(state: dict, sets: list[str] | None) -> list[str]:
    if not state["sets"]:
        raise RunError("the bench holds no sets yet: run `bench.py init` first")
    names = list(sets) if sets else list(state["sets"])
    unknown = [n for n in names if n not in state["sets"]]
    if unknown:
        raise RunError(
            f"the bench has no set {', '.join(unknown)}; it holds {', '.join(state['sets'])}"
        )
    return list(dict.fromkeys(names))


def plan_sets(state: dict, chosen: list[str], reference: dict) -> dict[str, SetPlan]:
    """Every sample of the chosen sets, and those the run cannot read.

    A sample ``init`` left with a problem is not ready; one with no run of
    the pinned reference (none published, or its publish failed) is not read,
    since the card would read it by the intrinsic metrics alone and mix it
    into a reading of both engines.
    """
    plans = {}
    for name in chosen:
        held = state["sets"][name]
        p = SetPlan(name=name, instrument=held.get("instrument_class") or "orbitrap")
        runs = (reference.get("runs") or {}).get(name, {})
        unrunnable = (reference.get("unrunnable") or {}).get(name)
        for key, record in held["samples"].items():
            items = [i["sample_item_id"] for i in record.get("items") or []]
            if not items:
                p.total += 1
                p.leave_out("not_ready", key, "; ".join(record.get("problems") or []))
                continue
            for sample in items:
                p.total += 1
                if record.get("problems"):
                    p.leave_out("not_ready", sample, "; ".join(record["problems"]))
                    continue
                entry = runs.get(sample) or {}
                if not entry.get("run_id"):
                    p.leave_out(
                        "no_reference",
                        sample,
                        entry.get("error")
                        or unrunnable
                        or f"no reference run at {reference['pin']}",
                    )
                    continue
                p.samples.append(sample)
                p.reference[sample] = entry["run_id"]
        plans[name] = p
    return plans


def newest(runs: list[dict], engine: str, status: str = "completed") -> dict | None:
    """The newest run of an engine in a status, from a listing newest first
    and without decoys, as the card reads it."""
    return next(
        (r for r in runs if r.get("engine") == engine and r.get("status") == status),
        None,
    )


def reference_problem(runs: list[dict], run_id: str, version: str) -> str | None:
    """Why the reference the card would read is not the pin's, or None.

    The card reads a sample's newest completed reference run, so that run
    must be the one ``bench reference`` published at the pin, under the
    pin's version string.
    """
    latest = newest(runs, REFERENCE_ENGINE)
    if latest is None:
        return "no completed reference run on the server"
    if latest.get("peak_assignment_run_id") != run_id:
        return (
            f"the newest reference run is {latest.get('peak_assignment_run_id')}, "
            f"not the pin's {run_id}"
        )
    if latest.get("engine_version") != version:
        return (
            f"the reference run is version {latest.get('engine_version')}, "
            f"not the pin's {version}"
        )
    return None


@dataclass
class Outcome:
    """What became of one run the bench asked for.

    :ivar status: ``completed``, ``failed``, ``cancelled``, ``refused``
        (the request was refused, or a decoy the engine refused at run time),
        ``lost`` (the run left the listing), ``over_budget``, or ``stopped``
        (the bench stopped waiting: Ctrl-C, or another sample's job broke).
    """

    status: str
    run_id: str | None = None
    code: str | None = None
    detail: str | None = None


def migrate(instance, ref: str, sha: str, say: Callable[[str], None]) -> dict:
    """Move the bench database to the checked-out ref's head, or refuse an
    older ref: one that does not know the database's revision, since a later
    build migrated it and alembic cannot go back on a ref that lacks it.

    :return: ``{"from": [...], "to": [...]}``, the revisions before and after.
    """
    current = sorted(instance.db_revisions())
    heads, known = instance.ref_revisions()
    heads = sorted(heads)
    unknown = [r for r in current if r not in set(known)]
    if unknown:
        raise RunError(
            f"the bench database is at alembic revision {', '.join(unknown)}, which "
            f"{ref} at {sha[:9]} does not know: the ref is older than the database's "
            f"head (a later build migrated it). Bench a ref that holds it, or rebuild "
            f"the bench (`bench.py init <manifest> --rebuild`)"
        )
    if current == heads:
        say(f"the database is at the ref's head ({', '.join(heads)})")
        return {"from": current, "to": heads}
    say(
        f"migrating the database from {', '.join(current) or 'nothing'} "
        f"to the ref's head {', '.join(heads)}"
    )
    instance.migrate()
    after = sorted(instance.db_revisions())
    if after != heads:
        raise RunError(
            f"the migration left the database at {', '.join(after)}, not at "
            f"the ref's head {', '.join(heads)}"
        )
    return {"from": current, "to": after}


# ---------------------------------------------------------------- comparison


@dataclass
class CompareRuns:
    """``compare_runs.py --card --gate`` from this checkout, as a subprocess.

    The card is computed by the tool of the checkout ``bench.py`` runs from,
    never by the ref's own copy: a branch that changed the metrics would
    otherwise judge itself by them.
    """

    script: Path = COMPARE
    python: str = sys.executable
    poll_s: float = POLL_S

    def __call__(
        self,
        *,
        sets: Path,
        out: Path,
        decoy: bool,
        baseline: Path | None,
        deny: Path,
        env: dict[str, str],
        log: Path,
        stall_s: float,
    ) -> int:
        """Run it into ``log``; stop it when the log has not grown for
        ``stall_s`` seconds."""
        command = [
            self.python,
            str(self.script),
            "--sets",
            str(sets),
            "--card",
            str(out),
            "--gate",
            "--deny",
            str(deny),
        ]
        if decoy:
            command.append("--decoy")
        if baseline is not None:
            command += ["--baseline", str(baseline)]
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(f"$ compare_runs.py {' '.join(command[2:])}\n")
            fh.flush()
            proc = subprocess.Popen(
                command,
                cwd=sets.parent,
                env=env,
                stdout=fh,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
            )
            try:
                size, moved = log.stat().st_size, time.monotonic()
                while True:
                    try:
                        return proc.wait(timeout=self.poll_s)
                    except subprocess.TimeoutExpired:
                        pass
                    grown = log.stat().st_size
                    if grown != size:
                        size, moved = grown, time.monotonic()
                    elif time.monotonic() - moved > stall_s:
                        raise RunError(
                            f"the comparison wrote nothing for {stall_s / 60:.0f} min "
                            f"and was stopped"
                        )
            finally:
                # Stalled, or the bench itself stopped: none runs on without it.
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()


def _compare_module():
    """``compare_runs`` itself, for the scrub and the diff's wording."""
    folder = str(COMPARE.parent)
    if folder not in sys.path:
        sys.path.insert(0, folder)
    import compare_runs

    return compare_runs


def _words(text: str) -> list[str]:
    return [w for w in re.split(r"[^A-Za-z0-9]+", text) if w]


def _key_words(value) -> set[str]:
    """Every word of every key of a JSON value, at any depth."""
    if isinstance(value, dict):
        out = {w for k in value for w in _words(str(k))}
        for v in value.values():
            out |= _key_words(v)
        return out
    if isinstance(value, list):
        return set().union(*map(_key_words, value)) if value else set()
    return set()


# --------------------------------------------------------------------- run


@dataclass
class BenchRun:
    """``bench run`` and ``bench baseline`` over one bench home and its instance.

    :ivar home: The bench's home (``bench.Home``).
    :ivar instance: Its instance (``bench.DevInstance``, a fake in the tests).
    :ivar connect: Signs in on a started instance and writes ``bench.env``.
    :ivar compare: Makes the card (:class:`CompareRuns`, a fake in the tests).
    :ivar api_of: The run's API, from the bench's URL and SDK token.
    :ivar workers: The ref's backend's uvicorn workers.
    :ivar stall_s: Seconds the comparison may write nothing before it is stopped.
    """

    home: object
    instance: object
    connect: Callable[[str], object]
    load_state: Callable[[], dict]
    save_state: Callable[[dict], None]
    compare: Callable[..., int] = field(default_factory=CompareRuns)
    api_of: Callable[[str, str], RunApi] = TokenApi
    jobs: int = JOBS
    budget_s: float = BUDGET_MIN * 60
    workers: int = WORKERS
    stall_s: float = COMPARE_STALL_MIN * 60
    poll_s: float = POLL_S
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    stamp: Callable[[], str] = lambda: datetime.datetime.now().strftime("%Y%m%d-%H%M%S")

    # ------------------------------------------------------------------ run

    def run(
        self,
        ref: str,
        sets: list[str] | None = None,
        decoys: bool = False,
        baseline: bool = False,
    ) -> int:
        """One run of ``ref``; 0 when the card passes with nothing left out,
        1 when it fails its gate or something was left out, 2 when no card was
        written."""
        state = self.load_state()
        chosen = choose_sets(state, sets)
        reference = state.get("reference")
        if not reference or not reference.get("pin"):
            raise RunError(
                "the bench has no reference at a pin: run `bench.py reference --pin vX.Y.Z` first"
            )
        plans = plan_sets(state, chosen, reference)
        if not any(p.samples for p in plans.values()):
            raise RunError(
                "no sample of "
                + ", ".join(chosen)
                + " can be read: "
                + "; ".join(f"{n} {p.counts()}" for n, p in plans.items())
            )
        base = self._baseline_card(state) if baseline else None

        out = self._card_dir(ref)
        work = out / "work"
        work.mkdir(mode=0o700)
        with RunLog(out / "run.log") as runlog:
            return self._run(
                ref,
                chosen,
                plans,
                reference,
                decoys,
                baseline,
                base,
                out,
                work,
                runlog,
                state,
            )

    def _card_dir(self, ref: str) -> Path:
        parent = self.home.cards / ref_slug(ref)
        parent.mkdir(parents=True, exist_ok=True)
        stamp = self.stamp()
        out, n = parent / stamp, 1
        while out.exists():
            n += 1
            out = parent / f"{stamp}-{n}"
        out.mkdir()
        return out

    def _baseline_card(self, state: dict) -> dict | None:
        """The rolling baseline, if one exists and its card is still there."""
        record = state.get("baseline")
        if not record:
            return None
        card = self.home.cards / record["path"] / "card.json"
        if not card.exists():
            return None
        return {**record, "card": card}

    def _run(
        self,
        ref,
        chosen,
        plans,
        reference,
        decoys,
        baseline,
        base,
        out,
        work,
        runlog,
        state,
    ) -> int:
        say = runlog.say
        started = self.clock()
        summary = {
            "format": SUMMARY_FORMAT,
            "ref": ref,
            "sha": None,
            "develop": None,
            "started": now(),
            "finished": None,
            "reference": {"pin": reference["pin"], "version": reference.get("version")},
            "decoys": decoys,
            "migration": None,
            "sets": {},
            "card": None,
            "baseline": None,
            "is_baseline": False,
        }
        say(
            f"bench run {ref}: set(s) {', '.join(chosen)}"
            + (", decoy arms" if decoys else "")
            + f", reference {reference['pin']}; card into {out}"
        )
        for p in plans.values():
            say(f"set {p.name}: {len(p.samples)} of {p.total} sample(s) to run")
            self._note_left_out(runlog, p)
        if baseline:
            say(
                f"baseline: develop's card of {base['at']} ({base['ref']} at {base['sha'][:9]})"
                if base
                else "baseline: none yet (`bench.py run origin/develop --baseline` makes one)"
            )

        self.instance.check_env()
        self.instance.bind()
        card = None
        results: dict[str, dict] = {}
        try:
            sha = self.instance.checkout(ref)
            develop = self.instance.develop_sha()
            summary.update(sha=sha, develop=develop)
            say(f"{ref} is {sha[:9]}" + (" (develop's head)" if sha == develop else ""))
            summary["migration"] = migrate(self.instance, ref, sha, say)
            base_url = self.instance.start(peak_assignment=True, workers=self.workers)
            self.connect(base_url)
            creds = read_env_file(self.home.env_file)
            api = self.api_of(creds["MASCOPE_URL"], creds["MASCOPE_ACCESS_TOKEN"])
            self._check_references(api, plans, reference, runlog)
            for name in chosen:
                if plans[name].samples:
                    results[name] = self._run_set(api, plans[name], decoys, runlog)
            self._check_controls(api, plans, results, reference, runlog)
            card = self._card(
                ref,
                chosen,
                plans,
                results,
                decoys,
                base,
                out,
                work,
                runlog,
                state,
                creds,
            )
        finally:
            self.instance.stop()
            self.instance.remove_worktree()
            say(
                "the ref's backend is stopped and its worktree removed; the instance stays"
            )

        for name in chosen:
            summary["sets"][name] = self._set_summary(plans[name], results.get(name))
        summary["finished"] = now()
        summary["minutes"] = round((self.clock() - started) / 60, 1)
        summary["card"] = {k: card[k] for k in ("verdict", "gate", "sets") if k in card}
        problems = run_problems(summary["sets"])
        if base and card.get("diff") is not None:
            summary["baseline"] = {k: base[k] for k in ("ref", "sha", "at", "path")}
        promoted = None
        if baseline and card["verdict"] in ("pass", "fail"):
            promoted = self._promote(ref, summary, problems, state, out, say)
        summary["is_baseline"] = promoted is not None
        self._write_outputs(out, summary, card, runlog)
        self._record(state, ref, summary, card, base, out)
        return self._report(chosen, summary, card, problems, out, say)

    # -------------------------------------------------------------- build

    # ------------------------------------------------------------- engine

    def _runs(
        self, api: RunApi, sample: str, include_decoys: bool = False
    ) -> list[dict]:
        """A sample's runs, asking again while the server does not answer."""
        for attempt in range(TRANSIENT_TRIES):
            try:
                return api.runs(sample, include_decoys=include_decoys)
            except TransientError:
                if attempt == TRANSIENT_TRIES - 1:
                    raise
                self.sleep(self.poll_s)
        raise AssertionError("unreachable")

    def _check_references(self, api, plans, reference, runlog) -> None:
        """Leave out a sample whose newest reference run is not the pin's."""
        for p in plans.values():
            before = p.snapshot()
            for sample in list(p.samples):
                try:
                    runs = self._runs(api, sample)
                except (TransientError, RunError) as error:
                    p.leave_out(
                        "failed", sample, f"its runs could not be read: {error}"
                    )
                    continue
                why = reference_problem(
                    runs, p.reference[sample], reference.get("version")
                )
                if why:
                    p.leave_out("reference_moved", sample, why)
            self._note_left_out(runlog, p, before)

    def _run_set(self, api: RunApi, p: SetPlan, decoys: bool, runlog: RunLog) -> dict:
        """The set's control runs, then its decoy arms, within its budget.

        Samples run side by side; one sample's runs follow each other, since
        the server holds one run of a sample at a time. When the main thread
        leaves the pool on an exception, a Ctrl-C or any sample's job that
        broke, the jobs still running stop at their next poll and launch
        nothing more, rather than hold the lock and the backend until the
        budget runs out.
        """
        arms = DECOY_ARMS.get(p.instrument, ()) if decoys else ()
        before = p.snapshot()
        start = self.clock()
        deadline = start + self.budget_s
        lock = threading.Lock()
        stop = threading.Event()
        done = [0]

        def job(sample: str) -> tuple[str, Outcome, dict[str, Outcome]]:
            control = self._one(api, sample, DEFAULT_CONFIG, deadline, stop)
            outcomes: dict[str, Outcome] = {}
            if control.status == "completed":
                for arm in arms:
                    outcomes[arm_name(arm)] = self._one(
                        api, sample, {"decoy": arm}, deadline, stop
                    )
            with lock:
                done[0] += 1
                runlog.note(
                    f"set {p.name}: {sample}: control {control.status} {control.run_id or ''}"
                    + (f" ({control.code}: {control.detail})" if control.detail else "")
                    + "".join(
                        f"; {arm} {o.status}"
                        + (f" ({o.code}: {o.detail})" if o.detail else "")
                        for arm, o in outcomes.items()
                    )
                )
                if done[0] % 10 == 0 or done[0] == len(p.samples):
                    runlog.say(
                        f"set {p.name}: {done[0]}/{len(p.samples)} sample(s) done, "
                        f"{(self.clock() - start) / 60:.1f} min"
                    )
            return sample, control, outcomes

        runlog.say(
            f"set {p.name}: running the engine on {len(p.samples)} sample(s)"
            + (f" and {len(arms)} decoy arm(s) each" if arms else "")
            + f", {self.jobs} at once, within {self.budget_s / 60:.0f} min"
        )
        with ThreadPoolExecutor(max_workers=max(1, self.jobs)) as pool:
            futures = []
            try:
                futures = [pool.submit(job, sample) for sample in p.samples]
                # Wait on every job, not in sample order: a job that broke
                # behind a sample still running is seen when it breaks.
                finished, _ = wait(futures, return_when=FIRST_EXCEPTION)
                for future in futures:
                    if future in finished and future.exception() is not None:
                        raise future.exception()
                rows = [future.result() for future in futures]
            except BaseException:
                # Leaving the block waits for the running jobs, which this
                # stops, and would start the queued ones, which this cancels.
                stop.set()
                for future in futures:
                    future.cancel()
                raise

        controls: dict[str, Outcome] = {}
        decoy_outcomes: dict[str, dict[str, Outcome]] = {}
        over = False
        for sample, control, outcomes in rows:
            controls[sample] = control
            decoy_outcomes[sample] = outcomes
            over = over or control.status == "over_budget"
            over = over or any(o.status == "over_budget" for o in outcomes.values())
        for sample, control in controls.items():
            if control.status == "completed":
                continue
            if control.status == "over_budget":
                p.leave_out("over_budget", sample, "the set's budget ran out")
            elif control.status == "refused" and control.code == "sample_ineligible":
                p.leave_out("ineligible", sample, control.detail or "")
            else:
                p.leave_out(
                    "failed",
                    sample,
                    f"{control.status}: {control.code or ''} {control.detail or ''}".strip(),
                )
        if over:
            # A set the budget cut short is not a reading of the set: none of
            # it goes on the card, so a diff never compares half a set.
            for sample in list(p.samples):
                p.leave_out("over_budget", sample, "the set's budget ran out")
            runlog.say(
                f"set {p.name}: over its budget of {self.budget_s / 60:.0f} min; "
                f"not on the card"
            )
        self._note_left_out(runlog, p, before)
        return {
            "minutes": round((self.clock() - start) / 60, 1),
            "controls": controls,
            "decoys": decoy_outcomes,
            "over_budget": over,
            "arms": [arm_name(a) for a in arms],
        }

    def _one(
        self,
        api: RunApi,
        sample: str,
        config: dict | None,
        deadline: float,
        stop: threading.Event,
    ) -> Outcome:
        """Launch one run of a sample and wait for it, while the budget lasts
        and until ``stop`` is set."""
        transient = 0
        while True:
            if self.clock() > deadline:
                return Outcome("over_budget")
            if stop.is_set():
                return Outcome("stopped")
            try:
                launch = api.assign(sample, config)
            except TransientError as error:
                transient += 1
                if transient >= TRANSIENT_TRIES:
                    return Outcome("failed", code="unreachable", detail=str(error))
                self.sleep(self.poll_s)
                continue
            if launch.run_id:
                break
            if launch.busy:
                self.sleep(self.poll_s)
                continue
            return Outcome("refused", code=launch.code, detail=launch.reason)
        transient = 0
        while True:
            try:
                runs = api.runs(sample, include_decoys=True)
            except TransientError as error:
                transient += 1
                if transient >= TRANSIENT_TRIES:
                    return Outcome(
                        "failed", launch.run_id, code="unreachable", detail=str(error)
                    )
                runs = None
            except RunError as error:
                return Outcome(
                    "failed", launch.run_id, code="unreadable", detail=str(error)
                )
            if runs is not None:
                transient = 0
                run = next(
                    (
                        r
                        for r in runs
                        if r.get("peak_assignment_run_id") == launch.run_id
                    ),
                    None,
                )
                if run is None:
                    return Outcome(
                        "lost", launch.run_id, detail="the run left the listing"
                    )
                status = run.get("status")
                if status in TERMINAL:
                    error = run.get("error")
                    if (
                        status != "completed"
                        and isinstance(error, str)
                        and error.startswith(DECOY_REFUSAL)
                    ):
                        return Outcome("refused", launch.run_id, "decoy_refused", error)
                    return Outcome(status, launch.run_id, detail=error)
            if self.clock() > deadline:
                return Outcome("over_budget", launch.run_id)
            if stop.is_set():
                return Outcome("stopped", launch.run_id)
            self.sleep(self.poll_s)

    def _check_controls(self, api, plans, results, reference, runlog) -> None:
        """The card reads each sample's newest completed in-app run and newest
        reference run: they must be this run's control and the pin's."""
        for name, result in results.items():
            p = plans[name]
            before = p.snapshot()
            for sample in list(p.samples):
                control = result["controls"][sample]
                try:
                    runs = self._runs(api, sample)
                except (TransientError, RunError) as error:
                    p.leave_out(
                        "failed", sample, f"its runs could not be read: {error}"
                    )
                    continue
                latest = newest(runs, ENGINE)
                if (
                    latest is None
                    or latest.get("peak_assignment_run_id") != control.run_id
                ):
                    p.leave_out(
                        "not_latest",
                        sample,
                        f"the newest completed in-app run is "
                        f"{(latest or {}).get('peak_assignment_run_id')}, not this run's "
                        f"{control.run_id}",
                    )
                    continue
                why = reference_problem(
                    runs, p.reference[sample], reference.get("version")
                )
                if why:
                    p.leave_out("reference_moved", sample, why)
            self._note_left_out(runlog, p, before)

    def _note_left_out(
        self, runlog: RunLog, p: SetPlan, before: dict | None = None
    ) -> None:
        """Say how many samples each reason left out since ``before`` (the
        counts then), and name them in the run log only."""
        before = before or {}
        for reason, rows in p.left_out.items():
            new = rows[before.get(reason, 0) :]
            if not new:
                continue
            runlog.say(
                f"set {p.name}: {len(new)} sample(s) not read: {reason} (run.log)"
            )
            for sample, why in new:
                runlog.note(f"set {p.name}: {sample}: {reason}: {why}")

    # --------------------------------------------------------------- card

    def _deny(self, state: dict, plans: dict, results: dict) -> tuple[list, list, list]:
        """What no output may hold: the instance's names and ids, every
        sample's file name and id, and the ids of the runs read."""
        private = self.instance.private_names()
        names = list(private.get("names", []))
        ids = list(private.get("ids", []))
        strict = []
        for held in state["sets"].values():
            names.append(held.get("mode") or "")
            names.append(held.get("label") or "")
            for record in held["samples"].values():
                strict.append(record.get("name") or "")
                ids.append(record.get("sample_file_id") or "")
                ids += [i["sample_item_id"] for i in record.get("items") or []]
        for runs in ((state.get("reference") or {}).get("runs") or {}).values():
            ids += [e.get("run_id") or "" for e in runs.values()]
        for result in results.values():
            ids += [o.run_id or "" for o in result["controls"].values()]
            for outcomes in result["decoys"].values():
                ids += [o.run_id or "" for o in outcomes.values()]
        clean = lambda xs: sorted({x for x in xs if x and str(x).strip()})  # noqa: E731
        return clean(names), clean(ids), clean(strict)

    def _tokens(self, deny, set_names, own_words) -> list[str]:
        cr = _compare_module()
        names, ids, strict = deny
        url = read_env_file(self.home.env_file).get("MASCOPE_URL")
        return cr.deny_tokens(
            names,
            ids,
            set_names,
            strict=strict,
            hosts=cr.host_names(url),
            own_words=own_words,
        )

    def _card(
        self, ref, chosen, plans, results, decoys, base, out, work, runlog, state, creds
    ) -> dict:
        """Compare, scrub again, and only then write the card where it may leave."""
        say = runlog.say
        read = [n for n in chosen if plans[n].samples]
        card: dict = {"verdict": None, "sets": read, "files": [], "diff": None}
        deny = self._deny(state, plans, results)
        card["deny"] = deny
        if not read:
            say("no set was read: there is no card")
            card["verdict"] = "none"
            return card
        workspaces = self.instance.sample_workspaces()
        manifest = {"sets": {}, "deny": deny[2]}
        for name in read:
            p = plans[name]
            manifest["sets"][name] = {
                "workspace": workspaces[p.samples[0]],
                "samples": list(p.samples),
                "instrument": p.instrument,
            }
        sets_file = work / "sets.json"
        sets_file.write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
        deny_file = work / "deny.txt"
        deny_file.write_text(
            "\n".join(deny[0] + deny[1] + deny[2]) + "\n", encoding="utf-8"
        )
        staging = work / "card"
        env = {k: v for k, v in os.environ.items() if not k.startswith("MASCOPE_")}
        env.update(
            {
                "MASCOPE_URL": creds["MASCOPE_URL"],
                "MASCOPE_ACCESS_TOKEN": creds["MASCOPE_ACCESS_TOKEN"],
                "PYTHONUTF8": "1",
                # Its per-sample lines reach the run log as they are made.
                "PYTHONUNBUFFERED": "1",
            }
        )
        say(
            f"comparing {', '.join(read)} with the reference"
            + (" and the decoy arms" if decoys else "")
            + (f", against the baseline of {base['at']}" if base else "")
        )
        code = self.compare(
            sets=sets_file,
            out=staging,
            decoy=decoys,
            baseline=self._baseline_for(base, read, work) if base else None,
            deny=deny_file,
            env=env,
            log=runlog.path,
            stall_s=self.stall_s,
        )
        staged = staging / "card.json"
        if not staged.exists():
            shutil.rmtree(staging, ignore_errors=True)
            if code == 2:
                say(
                    "the card was refused by the scrub: nothing written (run.log names the token)"
                )
                card["verdict"] = "refused"
            else:
                say(f"the comparison wrote no card (exit {code}); see run.log")
                card["verdict"] = "error"
            return card

        files = {
            path.name: path.read_text(encoding="utf-8")
            for path in sorted(staging.iterdir())
            if path.is_file()
        }
        made = json.loads(files["card.json"])
        cr = _compare_module()
        own = set(
            _words(f"{made['engines']['a']} {made['engines']['b']}")
        ) | cr.card_vocabulary(made)
        tokens = self._tokens(deny, read, sorted(own))
        try:
            cr.scrub(files, tokens)
        except cr.CardRefused as refused:
            runlog.note(f"the bench's scrub refused the card: {refused}")
            say(
                f"the card was refused by the scrub: {refused.where} holds a denied "
                f"name; nothing written (run.log names it)"
            )
            shutil.rmtree(staging, ignore_errors=True)
            card["verdict"] = "refused"
            return card
        for name in files:
            shutil.move(str(staging / name), str(out / name))
        shutil.rmtree(staging, ignore_errors=True)
        card["files"] = sorted(files)
        card["markdown"] = files.get("card.md", "")
        card["own"] = own
        gate = made.get("gate")
        card["verdict"] = gate["verdict"] if gate else "written"
        card["gate"] = cr.gate_line(gate) if gate else None
        card["diff"] = made.get("baseline")
        return card

    def _baseline_for(self, base: dict, read: list[str], work: Path) -> Path:
        """The baseline card to diff against: as it is when this card reads
        its sets, else only those sets' columns. A pooled column is a reading
        of the sets it pools, so one pooling other sets is no baseline for it:
        the card then names its pooled column as not on the baseline."""
        if sorted(read) == sorted(base.get("sets") or []):
            return base["card"]
        card = json.loads(base["card"].read_text(encoding="utf-8"))
        card["columns"] = {
            name: column
            for name, column in card.get("columns", {}).items()
            if name in read
        }
        path = work / "baseline.json"
        path.write_text(json.dumps(card) + "\n", encoding="utf-8")
        return path

    # ------------------------------------------------------------ outputs

    def _set_summary(self, p: SetPlan, result: dict | None) -> dict:
        out = {
            "samples": p.total,
            "read": len(p.samples),
            "left_out": p.counts(),
            "minutes": result["minutes"] if result else None,
            "over_budget": bool(result and result["over_budget"]),
            "decoys": {},
        }
        if result:
            for arm in result["arms"]:
                tally: dict[str, int] = {}
                for outcomes in result["decoys"].values():
                    if arm in outcomes:
                        status = outcomes[arm].status
                        tally[status] = tally.get(status, 0) + 1
                out["decoys"][arm] = dict(sorted(tally.items()))
        return out

    def _promote(self, ref, summary, problems, state, out, say) -> dict | None:
        """Make this card develop's rolling baseline, when it is one."""
        if summary["sha"] != summary["develop"]:
            why = f"{ref} is not develop's head ({str(summary['develop'])[:9]})"
        else:
            why = not_whole(summary["card"]["sets"], state["sets"], problems)
        if why:
            if summary["sha"] == summary["develop"]:
                say(f"not made the baseline: {why}")
            return None
        record = {
            "ref": ref,
            "sha": summary["sha"],
            "at": summary["finished"],
            "path": str(out.relative_to(self.home.cards)),
            "sets": list(summary["card"]["sets"]),
            "decoys": summary["decoys"],
        }
        state["baseline"] = record
        say("this card is develop's rolling baseline now")
        return record

    def _write_outputs(
        self, out: Path, summary: dict, card: dict, runlog: RunLog
    ) -> None:
        """``run.json``, and with a diff ``diff.md`` and ``diff.json``, scrubbed."""
        texts = {"run.json": json.dumps(summary, indent=1) + "\n"}
        if card.get("diff") is not None:
            diff = {"baseline": summary["baseline"], **card["diff"]}
            texts["diff.json"] = json.dumps(diff, indent=1) + "\n"
            texts["diff.md"] = diff_markdown(diff)
        own = (
            set(card.get("own") or ())
            | _key_words(summary)
            | set(_words(summary["ref"]))
        )
        if card.get("diff") is not None:
            own |= _key_words(json.loads(texts["diff.json"]))
        tokens = self._tokens(card["deny"], card["sets"], sorted(own))
        cr = _compare_module()
        for name, text in texts.items():
            try:
                cr.scrub({name: text}, tokens)
            except cr.CardRefused as refused:
                runlog.note(f"the scrub refused {name}: {refused}")
                runlog.say(
                    f"{name} was refused by the scrub: a denied name; not written"
                )
                continue
            (out / name).write_text(text, encoding="utf-8")

    def _record(self, state, ref, summary, card, base, out) -> None:
        state.setdefault("cards", []).append(
            {
                "at": summary["finished"],
                "ref": ref,
                "sha": summary["sha"],
                "path": str(out.relative_to(self.home.cards)),
                "verdict": card["verdict"],
                "sets": card["sets"],
                "decoys": summary["decoys"],
                "baseline": base["path"] if base else None,
            }
        )
        state["cards"] = state["cards"][-50:]
        self.save_state(state)

    def _report(self, chosen, summary, card, problems, out, say) -> int:
        for name in chosen:
            s = summary["sets"][name]
            arms = ", ".join(
                f"{arm} " + "/".join(f"{n} {k}" for k, n in tally.items())
                for arm, tally in s["decoys"].items()
            )
            say(
                f"set {name}: {s['read']} of {s['samples']} sample(s) on the card"
                + (f", {s['minutes']} min" if s["minutes"] is not None else "")
                + (f"; decoys {arms}" if arms else "")
                + ("; over budget" if s["over_budget"] else "")
            )
        if card.get("markdown"):
            print(card["markdown"], flush=True)
        if card.get("gate"):
            say(card["gate"])
        if card.get("diff") is not None:
            cr = _compare_module()
            say(f"BASELINE: {cr.baseline_line(card['diff'])}")
        if problems:
            say(f"{len(problems)} problem(s):")
            for problem in problems:
                print(f"  {problem}", flush=True)
        if card["verdict"] in ("pass", "fail", "written"):
            say(f"card: {out}")
        if card["verdict"] not in ("pass", "fail", "written"):
            return 2
        return 1 if card["verdict"] == "fail" or problems else 0

    # ------------------------------------------------------------ baseline

    def baseline(self, card: str | None = None, out=None) -> int:
        """Print develop's rolling baseline, or make a card of develop it."""
        out = out or sys.stdout
        state = self.load_state()
        if card is None:
            record = state.get("baseline")
            if not record:
                print(
                    "no baseline yet: `bench.py run origin/develop --baseline` makes one",
                    file=out,
                )
                return 1
            print(
                f"baseline  {record['ref']} at {record['sha'][:9]}, {record['at']}, "
                f"sets {', '.join(record['sets'])}"
                + (", decoy arms" if record.get("decoys") else "")
                + f"\n          {self.home.cards / record['path']}",
                file=out,
            )
            return 0
        folder = Path(card)
        if not folder.is_absolute():
            folder = self.home.cards / folder
        folder = folder.resolve()
        cards = self.home.cards.resolve()
        if cards not in folder.parents:
            raise RunError(f"{card} is not a card of this bench ({cards})")
        summary_file = folder / "run.json"
        if not (folder / "card.json").exists() or not summary_file.exists():
            raise RunError(f"{folder} holds no written card")
        summary = json.loads(summary_file.read_text(encoding="utf-8"))
        if not self.instance.is_on_develop(summary["sha"]):
            raise RunError(
                f"{summary['ref']} at {summary['sha'][:9]} is not on develop: the "
                f"baseline is develop's"
            )
        # The rule a run of develop's head is held to before it becomes the
        # baseline, but for the head: an earlier card of develop is the point.
        if summary["card"]["verdict"] not in ("pass", "fail"):
            raise RunError(f"{folder} holds no card with a gate verdict")
        why = not_whole(
            summary["card"]["sets"], state["sets"], run_problems(summary["sets"])
        )
        if why:
            raise RunError(f"{folder} cannot be the baseline: {why}")
        state["baseline"] = {
            "ref": summary["ref"],
            "sha": summary["sha"],
            "at": summary["finished"],
            "path": str(folder.relative_to(cards)),
            "sets": list(summary["card"]["sets"]),
            "decoys": summary["decoys"],
        }
        self.save_state(state)
        print(
            f"baseline  {summary['ref']} at {summary['sha'][:9]}, {summary['finished']}",
            file=out,
        )
        return 0


def run_problems(sets: dict) -> list[str]:
    """What makes a run exit 1 beside a failed gate, read off ``run.json``'s
    sets: anything left out, a set over its budget among them, and a decoy
    arm that failed, was cancelled or was lost rather than refused."""
    problems = []
    for name, s in sets.items():
        for reason, n in s["left_out"].items():
            problems.append(f"set {name}: {n} sample(s) {reason}")
        failed = sum(
            n
            for tally in s["decoys"].values()
            for status, n in tally.items()
            if status in ("failed", "cancelled", "lost")
        )
        if failed:
            problems.append(f"set {name}: {failed} decoy run(s) failed")
    return problems


def not_whole(card_sets: list[str], bench_sets, problems: list[str]) -> str | None:
    """Why a card of develop is no baseline, or None: it must read every set
    the bench holds, with nothing left out."""
    if set(card_sets) != set(bench_sets):
        return "it reads only some of the bench's sets"
    if problems:
        return "samples were left out (" + "; ".join(problems) + ")"
    return None


def diff_markdown(diff: dict) -> str:
    """The readings that moved since the baseline, as the card words them."""
    cr = _compare_module()
    base = diff.get("baseline") or {}
    lines = [
        "# Since the baseline",
        "",
        f"Against develop's baseline card: {base.get('ref')} at "
        f"{str(base.get('sha'))[:9]}, made {base.get('at')}.",
        "",
        cr.baseline_line(diff) + ".",
    ]
    if diff["moved"]:
        lines += ["", "| set | metric | was | now |", "|---|---|---|---|"]
        for one in diff["moved"]:
            unit = cr.unit_of(one["metric"])
            lines.append(
                f"| {one['set']} | {cr.label_of(one['metric'])} | "
                f"{cr.format_reading(one['was'], unit)} | "
                f"{cr.format_reading(one['now'], unit)} |"
            )
    return "\n".join(lines) + "\n"
