"""``bench run``: a ref's in-app engine on the bench sets, beside the pinned reference.

    bench.py run <ref> [--sets A,D,...] [--decoys] [--baseline] [--post PR] [--no-wait] [--jobs N] [--budget-min M] [--workers N]
    bench.py baseline [CARD]

The command the bench exists for (step 6.5 of the assignment quality plan).
One run, behind the bench's lock:

1. It checks the ref out into the bench's worktree (``uv sync --frozen``). It
   migrates the bench database forward with the ref's alembic, and refuses a
   ref that does not know the database's revision: a later build migrated it.
   Before it migrates for a ref whose head is not on develop, it dumps the
   database, to put it back in step 6. It starts the ref's backend on the
   bench slot with peak assignment on and ``--workers`` uvicorn workers.
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
6. It stops the ref's backend, puts the database back from the dump of step
   1 if it took one, and removes the ref's worktree. The instance stays. A
   dump not yet put back, by a run that was stopped, is put back by the next
   command that migrates, before anything else.
7. With ``--post <pr>`` it posts the card on the PR as one comment, edited in
   place on a re-run (``post.py``): the card, a refused card's refusal, or a
   failed run's error, each scrubbed again. ``--post`` diffs against develop's
   baseline as ``--baseline`` does, but not against one older than the PR's
   merge base.

Before any of it, unless ``--fresh``, the run hashes the head's engine inputs
(:func:`engine_digest`) and looks for a complete card of the same digest,
sets and arms. Finding one, it runs nothing and takes no lock: it writes that
card as the head's, and posts it with a line that names the commit it was
made at (``BenchRun.reuse``). A run of develop's head with ``--baseline``
always runs.

Each run writes ``cards/<ref>/<timestamp>/`` in the bench's home: the card
(``card.md``, ``card.json`` and one pair per set), the diff against the
baseline (``diff.md``, ``diff.json``) and the run's summary (``run.json``),
all scrubbed; and the private ``run.log`` and ``work/``, which name samples
and never leave the box.

This module holds the run; ``bench.py`` gives it the instance, the state and
the lock.
"""

from __future__ import annotations

import contextlib
import datetime
import fcntl
import hashlib
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
from typing import Callable, Iterable, Protocol


_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import post as bench_post  # noqa: E402
from stopping import STOPPER, Stopped  # noqa: E402


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
#: Eight took the engine over Db and Du from 27 minutes at four to 17 on the
#: agent box, the card unchanged, at a load average of about 7 to 8 (MAS-934).
JOBS = 8
BUDGET_MIN = 120.0
POLL_S = 10.0

#: The ref's backend's uvicorn workers: one per sample run at once, since an
#: engine run is CPU-bound in its worker. The card reads the rows' rule
#: records off each sample's ledger, so it makes no detail reads on a ref that
#: serves them (MAS-847); only an older ref's card falls back to them.
WORKERS = 8

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

#: A card's verdicts once it is written whole.
WRITTEN = ("pass", "fail", "written")

#: Why a sample is left off, where another run of the same inputs could
#: read it: a card that left one out for these is no card to reuse. The
#: other reasons (not ready, no reference at the pin, ineligible) come from
#: the bench's state and the engine's code, which the engine digest holds.
PASSING = ("reference_moved", "failed", "not_latest", "over_budget")

#: A decoy arm's outcomes that another run of the same inputs might not have.
PASSING_DECOY = ("failed", "cancelled", "lost", "over_budget", "stopped")

#: The paths of a ref's tree that cannot move its card, which the engine
#: digest leaves out: everything else counts, so a new folder counts until
#: it is listed here. Besides these, every ``*.md`` file, every path under a
#: ``tests`` folder, and ``tooling/`` but for :data:`COUNTED_TOOLING`.
IGNORED = ("server/frontend/", "docs/", "changelog.d/", ".github/", "agents/")

#: The tooling a card is made with: the bench and the comparison.
COUNTED_TOOLING = ("tooling/assignment_bench/", "tooling/assignment_compare/")

#: The caller's own copies of that tooling, which make the card (step 8 of a
#: run): a card made by another copy of them is not this checkout's card.
TOOL = (REPO / "tooling" / "assignment_bench", REPO / "tooling" / "assignment_compare")


class RunError(RuntimeError):
    """A step of ``bench run`` that cannot go on; the message says why."""


class TransientError(RuntimeError):
    """The server did not answer, or answered 5xx: the same request may pass later."""


# ------------------------------------------------------------ engine digest


def counts(path: str) -> bool:
    """Whether a path of a ref's tree can move the ref's card (:data:`IGNORED`)."""
    if path.lower().endswith(".md") or path.startswith(IGNORED):
        return False
    if path.startswith("tooling/") and not path.startswith(COUNTED_TOOLING):
        return False
    return "tests" not in path.split("/")[:-1]


def git_tree(repo: Path, sha: str) -> list[tuple[str, str]]:
    """A commit's whole tree as ``(path, "mode type object")``, from git
    alone: no checkout."""
    out = subprocess.run(
        ["git", "-C", str(repo), "ls-tree", "-r", "-z", "--full-tree", sha],
        capture_output=True,
        check=False,
    )
    if out.returncode != 0:
        raise RunError(
            f"cannot list the tree of {sha[:9]}: "
            f"{out.stderr.decode('utf-8', 'replace').strip()[-300:]}"
        )
    entries = []
    for item in out.stdout.split(b"\0"):
        if item:
            meta, path = item.split(b"\t", 1)
            entries.append((path.decode("utf-8", "surrogateescape"), meta.decode()))
    return entries


def tree_digest(entries: Iterable[tuple[str, str]]) -> str:
    """The digest of the tree's paths that count, by their blobs: two commits
    that differ only in paths that cannot move the card have one digest. The
    migrations are in it, so a new migration head is a new digest."""
    h = hashlib.sha256()
    for path, meta in sorted(entries):
        if counts(path):
            h.update(f"{meta}\t{path}\0".encode("utf-8", "surrogateescape"))
    return h.hexdigest()


def tool_digest(roots: Iterable[Path]) -> str:
    """The digest of the caller's own bench and comparison, as they are on
    disk: they read the sets and make the card, whatever the ref holds."""
    h = hashlib.sha256()
    for root in roots:
        for path in sorted(root.rglob("*")):
            if (
                not path.is_file()
                or "__pycache__" in path.parts
                or path.suffix in (".pyc", ".pyo")
                or path.suffix.lower() == ".md"
            ):
                continue
            name = f"{root.name}/{path.relative_to(root).as_posix()}"
            h.update(
                f"{name}\0{hashlib.sha256(path.read_bytes()).hexdigest()}\0".encode()
            )
    return h.hexdigest()


def bench_digest(state: dict) -> str:
    """The digest of what the bench holds that a card reads: the manifest it
    was built from, each set's samples and how they are read, and the
    reference at its pin, run by run. ``init --rebuild``, a manifest that
    changed, a new pin or a republished sample are each a new digest."""
    reference = state.get("reference") or {}
    held = {
        "manifest": (state.get("manifest") or {}).get("digest"),
        "sets": {
            name: {
                **{
                    k: s.get(k)
                    for k in (
                        "mode",
                        "instrument_class",
                        "profile",
                        "reference_window_ppm",
                        "truth",
                    )
                },
                "samples": sorted(
                    [
                        sorted(i["sample_item_id"] for i in r.get("items") or []),
                        sorted(r.get("problems") or []),
                    ]
                    for r in s["samples"].values()
                ),
            }
            for name, s in (state.get("sets") or {}).items()
        },
        "reference": {
            **{k: reference.get(k) for k in ("pin", "version", "library", "block")},
            "runs": {
                name: {
                    sample: [entry.get("run_id"), entry.get("digest")]
                    for sample, entry in runs.items()
                }
                for name, runs in (reference.get("runs") or {}).items()
            },
            "unrunnable": reference.get("unrunnable") or {},
        },
    }
    text = json.dumps(held, sort_keys=True, default=str)
    return hashlib.sha256(text.encode()).hexdigest()


def engine_digest(instance, sha: str, state: dict, tool: Iterable[Path]) -> dict:
    """Everything a bench run's card can depend on, as one digest: the ref's
    tree where it counts, the caller's bench and comparison, and what the
    bench holds. A card of one digest is a card of every head that has it.

    :return: ``{"digest", "tree", "tool", "bench"}``, the digest and its parts.
    """
    parts = {
        "tree": tree_digest(instance.tree(sha)),
        "tool": tool_digest(tool),
        "bench": bench_digest(state),
    }
    text = "".join(f"{k} {v}\n" for k, v in parts.items())
    return {"digest": hashlib.sha256(text.encode()).hexdigest(), **parts}


def passing_problems(sets: dict) -> list[str]:
    """What a run left out, or a decoy outcome, that another run of the same
    inputs might not have: a card with any is no card to reuse."""
    out = []
    for name, s in sets.items():
        # A set over its budget leaves every sample out as ``over_budget``.
        out += [
            f"set {name}: {reason}"
            for reason in s.get("left_out") or {}
            if reason in PASSING
        ]
        out += [
            f"set {name}: decoy {arm} {status}"
            for arm, tally in (s.get("decoys") or {}).items()
            for status in tally
            if status in PASSING_DECOY
        ]
    return out


# ---------------------------------------------------------------------- log

#: Open run logs every line of ``bench.log`` is copied into (``RunLog.tee``).
TEES: list["RunLog"] = []


def now() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _say(line: str) -> None:
    """A console line of a command that has no run log (yet)."""
    print(f"{datetime.datetime.now():%H:%M:%S} {line}", flush=True)


@contextlib.contextmanager
def _guard(path: Path):
    """A lock of its own around one read and write of the state's card list."""
    with open(path, "a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


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


def migrate(
    instance,
    ref: str,
    sha: str,
    say: Callable[[str], None],
    restore_point: bool = False,
) -> dict:
    """Move the bench database to the checked-out ref's head, or refuse an
    older ref: one that does not know the database's revision, since a later
    build migrated it and alembic cannot go back on a ref that lacks it.

    A restore point an earlier run left is put back first (:func:`restore`).
    With ``restore_point`` a migration is preceded by a dump of the database,
    recorded in the bench's home until :func:`restore` puts it back. A run of
    a ref whose head is not on develop asks for one, so that the revision of
    a branch's own migration never stays on the bench (#3171).

    :return: ``{"from": [...], "to": [...], "restore_point": bool}``, the
        revisions before and after, and whether a dump was taken.
    """
    restore(instance, say)
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
        return {"from": current, "to": heads, "restore_point": False}
    if restore_point:
        say(
            f"{ref} is not on develop: dumping the database at "
            f"{', '.join(current) or 'nothing'}, to put it back after the run"
        )
        dump = instance.snapshot()
        instance.home.write_private(
            instance.home.restore_point,
            json.dumps(
                {"dump": str(dump), "revisions": current, "ref": ref, "sha": sha},
                indent=1,
            )
            + "\n",
        )
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
    return {"from": current, "to": after, "restore_point": restore_point}


def restore(instance, say: Callable[[str], None]) -> bool:
    """Put the database back from the restore point :func:`migrate` took, if
    one is recorded; nothing may hold the database open.

    The record goes, and its dump with it, only once the database is back at
    the revisions it was dumped at. Until then every bench command that
    migrates tries again first, so a run stopped between its migration and
    its restore leaves nothing a later run of develop is refused on.

    :return: Whether there was one to put back.
    """
    point = instance.home.restore_point
    if not point.exists():
        return False
    record = json.loads(point.read_text(encoding="utf-8"))
    dump = Path(record["dump"])
    if not dump.exists():
        # Checked before anything is dropped: without its dump the record
        # can only empty the database, again on every retry.
        raise RunError(
            f"the restore point {point} names a dump that is gone ({dump}); the "
            "database is left as it is. `bench.py init --rebuild` starts the bench "
            "over, or delete the record once the migration of "
            f"{record['ref']} is on develop"
        )
    say(
        f"putting the database back to {', '.join(record['revisions']) or 'nothing'} "
        f"from the dump taken before {record['ref']} at {record['sha'][:9]} migrated it"
    )
    instance.restore(dump)
    after = sorted(instance.db_revisions())
    if after != sorted(record["revisions"]):
        raise RunError(
            f"the restored database is at {', '.join(after) or 'nothing'}, not at "
            f"{', '.join(record['revisions']) or 'nothing'}; the restore point is kept"
        )
    point.unlink()
    dump.unlink(missing_ok=True)
    return True


def discard_restore_point(home, say: Callable[[str], None]) -> bool:
    """Drop a restore point and its dump without putting it back: for
    ``init --rebuild``, which replaces the database the dump would be
    restored over, and the state that describes it.

    :return: Whether there was one.
    """
    point = home.restore_point
    if not point.exists():
        return False
    record = json.loads(point.read_text(encoding="utf-8"))
    dump = Path(record["dump"])
    dump.unlink(missing_ok=True)
    point.unlink()
    say(f"discarded the restore point taken before {record['ref']} migrated ({dump})")
    return True


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
        absolute: bool = False,
    ) -> int:
        """Run it into ``log``; stop it when the log has not grown for
        ``stall_s`` seconds. ``absolute`` judges the gate on the card alone
        even with a baseline (``--absolute-gate``): a card of develop's head."""
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
            if absolute:
                command.append("--absolute-gate")
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


def _card_words(made: dict, cr) -> set[str]:
    """The words a card prints of its own: its engines' names and vocabulary."""
    return set(_words(f"{made['engines']['a']} {made['engines']['b']}")) | set(
        cr.card_vocabulary(made)
    )


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
    :ivar tool: The bench and comparison folders the card is made with, for
        the engine digest (:func:`tool_digest`).
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
    tool: tuple[Path, ...] = TOOL
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
        post: bench_post.Poster | None = None,
        fresh: bool = False,
    ) -> int:
        """One run of ``ref``; 0 when the card passes with nothing left out,
        1 when it fails its gate or something was left out, 2 when no card was
        written.

        With ``post`` the run diffs against develop's baseline, as with
        ``baseline``, and its outcome goes on the PR: the card, the scrub's
        refusal, or, for a run that stops on an error, the error's tail.

        Unless ``fresh``, a complete card of the same engine digest, sets and
        arms is reused first (:meth:`reuse`), quietly when there is none: the
        caller asked before it took the lock, and a run ahead of it in the
        queue may have made one since.
        """
        if not fresh:
            code = self.reuse(ref, sets, decoys, baseline, post, quiet=True)
            if code is not None:
                return code
        if post is None:
            return self._run_once(ref, sets, decoys, baseline, None, {})
        if post.pr is None:
            post.open()
        progress: dict = {}
        try:
            return self._run_once(ref, sets, decoys, True, post, progress)
        except bench_post.PostError:
            raise
        except (Exception, Stopped, KeyboardInterrupt) as error:
            if not progress.get("recorded"):
                # A stop after the card was recorded leaves the card to
                # `bench.py post`: it is no failure.
                self._post_failure(post, ref, error, progress)
            raise

    def _run_once(self, ref, sets, decoys, baseline, post, progress) -> int:
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
                post,
                progress,
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
        post=None,
        progress=None,
    ) -> int:
        say = runlog.say
        started = self.clock()
        progress = {} if progress is None else progress
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
            "engine": None,
            "reused": None,
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
        no_diff = (
            "No diff: there is no baseline of develop yet."
            if post is not None and base is None
            else None
        )

        self.instance.check_env()
        self.instance.bind()
        card = None
        results: dict[str, dict] = {}
        progress["results"] = results
        stop: BaseException | None = None
        try:
            # An earlier run's backend that outlived it is stopped first: it
            # holds the port, and the database a restore must drop.
            self.instance.free_port()
            sha = self.instance.checkout(ref)
            develop = self.instance.develop_sha()
            summary.update(sha=sha, develop=develop)
            progress["sha"] = sha
            say(f"{ref} is {sha[:9]}" + (" (develop's head)" if sha == develop else ""))
            summary["engine"] = engine_digest(self.instance, sha, state, self.tool)
            say(f"engine digest {summary['engine']['digest'][:12]}")
            if post is not None and base is not None:
                base, no_diff = self._pr_baseline(
                    base, state, ref, sha, chosen, post, say
                )
            summary["migration"] = migrate(
                self.instance,
                ref,
                sha,
                say,
                restore_point=not self.instance.is_on_develop(sha),
            )
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
                absolute=sha == develop,
            )
        except (Stopped, KeyboardInterrupt) as error:
            stop = error
        except BaseException:
            with STOPPER.shield():
                self._cleanup(summary, runlog)
            raise

        # Shielded from the cleanup to the record: a signal that comes now
        # waits until the card is on record, which `bench.py post` then posts.
        with STOPPER.shield():
            self._cleanup(summary, runlog)
            for name in chosen:
                summary["sets"][name] = self._set_summary(
                    plans[name], results.get(name)
                )
            summary["finished"] = now()
            summary["minutes"] = round((self.clock() - started) / 60, 1)
            if stop is not None:
                self._stopped(ref, plans, results, summary, state, out, runlog, stop)
            else:
                summary["card"] = {
                    k: card[k] for k in ("verdict", "gate", "sets") if k in card
                }
                summary["no_diff"] = no_diff
                problems = run_problems(summary["sets"])
                if base and card.get("diff") is not None:
                    summary["baseline"] = {
                        k: base[k] for k in ("ref", "sha", "at", "path")
                    }
                promoted = None
                if baseline and card["verdict"] in ("pass", "fail"):
                    promoted = self._promote(ref, summary, problems, state, out, say)
                summary["is_baseline"] = promoted is not None
                self._write_outputs(out, summary, card, runlog)
                self._record(state, ref, summary, card, base, out)
                progress["recorded"] = card["verdict"] in WRITTEN
        if stop is not None:
            raise stop
        code = self._report(chosen, summary, card, problems, out, say)
        if post is not None:
            try:
                self._post_card(post, summary, card, out, problems, no_diff, runlog)
            except bench_post.PostError as error:
                if progress["recorded"]:
                    raise bench_post.PostError(
                        f"{error}; the card is written: `bench.py post {ref} --pr "
                        f"{post.pr.number}` posts it"
                    ) from None
                raise
        return code

    def _cleanup(self, summary: dict, runlog: RunLog) -> None:
        """Stop the ref's backend, put the database back, remove the worktree."""
        say = runlog.say
        self.instance.stop()
        # After the backend, which holds the database open; before the
        # worktree goes. A restore that fails keeps its record, and the
        # next bench command that migrates tries it again.
        try:
            if restore(self.instance, say) and summary["migration"]:
                summary["migration"]["restored"] = True
        except Exception as error:  # noqa: BLE001
            if summary["migration"]:
                summary["migration"]["restored"] = False
            runlog.note(f"restore failed: {type(error).__name__}: {error}")
            say(
                "the database could not be put back (run.log says why); the "
                "next bench command that migrates tries again"
            )
        self.instance.remove_worktree()
        say("the ref's backend is stopped and its worktree removed; the instance stays")

    def _stopped(self, ref, plans, results, summary, state, out, runlog, stop) -> None:
        """Write and record a stopped run: ``run.json`` with a ``stopped``
        verdict and how far the sets got, and the card list's entry."""
        why = stop.why if isinstance(stop, Stopped) else "KeyboardInterrupt"
        runlog.say(f"the run was stopped ({why}); no card")
        summary["card"] = {"verdict": "stopped", "sets": []}
        summary["stopped"] = why
        try:
            deny = self._deny(state, plans, results)
        except Exception as error:  # noqa: BLE001 - the database may be gone too
            runlog.note(f"the deny list could not be built: {error}")
            deny = ([], [], [])
        card = {
            "verdict": "stopped",
            "sets": [],
            "deny": deny,
            "diff": None,
            "own": set(_words(why)),
        }
        self._write_outputs(out, summary, card, runlog)
        self._record(state, ref, summary, card, None, out)

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
            try:
                return work(sample)
            except BaseException:
                # Set here, in the thread that broke: its worker takes the
                # next queued sample at once, before the main thread wakes.
                stop.set()
                raise

        def work(sample: str) -> tuple[str, Outcome, dict[str, Outcome]]:
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

    def _url(self) -> str | None:
        if not self.home.env_file.exists():
            return None
        return read_env_file(self.home.env_file).get("MASCOPE_URL")

    def _kind(self, token: str, deny) -> str:
        """The kind of name a refused token is, for what is posted: never the token."""
        names, ids, strict = deny
        hosts = _compare_module().host_names(self._url())
        return bench_post.token_kind(
            token, {"ids": ids, "strict": strict, "hosts": hosts, "names": names}
        )

    def _tokens(self, deny, set_names, own_words) -> list[str]:
        cr = _compare_module()
        names, ids, strict = deny
        url = self._url()
        return cr.deny_tokens(
            names,
            ids,
            set_names,
            strict=strict,
            hosts=cr.host_names(url),
            own_words=own_words,
        )

    def _card(
        self,
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
        absolute=False,
    ) -> dict:
        """Compare, scrub again, and only then write the card where it may leave.

        Against a baseline the gate reads this ref's misses against develop's
        (``compare_runs.judge_against``), unless ``absolute``: a card of
        develop's head is judged on its own, its misses being develop's."""
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
            # The set's authors' list, which `init` kept from the manifest's
            # folder: the card reads both engines against it.
            truth = (state["sets"].get(name) or {}).get("truth")
            if truth:
                manifest["sets"][name]["truth"] = list(truth["formulas"])
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
            absolute=absolute,
        )
        staged = staging / "card.json"
        if not staged.exists():
            shutil.rmtree(staging, ignore_errors=True)
            output = _compare_output(runlog.path)
            if code == 2:
                say(
                    "the card was refused by the scrub: nothing written (run.log names the token)"
                )
                card["verdict"] = "refused"
                found = bench_post.refused_token(output)
                if found:
                    card["refusal"] = (found[0], self._kind(found[1], deny))
            else:
                say(f"the comparison wrote no card (exit {code}); see run.log")
                card["verdict"] = "error"
                card["code"] = code
                card["tail"] = bench_post.tail(output)
            return card

        files = {
            path.name: path.read_text(encoding="utf-8")
            for path in sorted(staging.iterdir())
            if path.is_file()
        }
        made = json.loads(files["card.json"])
        cr = _compare_module()
        own = _card_words(made, cr)
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
            card["refusal"] = (refused.where, self._kind(refused.token, deny))
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
        path = work / "baseline.json"
        path.write_text(
            json.dumps(self._baseline_json(base, read)) + "\n", encoding="utf-8"
        )
        return path

    def _baseline_json(self, base: dict, read: list[str]) -> dict:
        """The baseline card as :meth:`_baseline_for` gives it, as JSON."""
        card = json.loads(base["card"].read_text(encoding="utf-8"))
        if sorted(read) != sorted(base.get("sets") or []):
            card["columns"] = {
                name: column
                for name, column in card.get("columns", {}).items()
                if name in read
            }
        return card

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
            # A reused card names the folder and ref of the card it reuses.
            | set(_words(" ".join(map(str, (summary.get("reused") or {}).values()))))
        )
        if summary.get("no_diff"):
            # Why there is no diff, in the comment's own sentences.
            own |= bench_post.WORDS
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
        """Add the card to the state's card list, as the state is on disk now.

        A reuse records its card without the bench's lock, so a run that began
        before it reads the state again here rather than write its own older
        copy over the reuse's entry; only a run that became the baseline
        writes the baseline.
        """
        entry = {
            "at": summary["finished"],
            "ref": ref,
            "sha": summary["sha"],
            "path": str(out.relative_to(self.home.cards)),
            "verdict": card["verdict"],
            "sets": card["sets"],
            "decoys": summary["decoys"],
            "baseline": base["path"] if base else None,
            "digest": (summary.get("engine") or {}).get("digest"),
            "reused": (summary.get("reused") or {}).get("path"),
        }
        with _guard(self.home.root / "state.lock"):
            current = self.load_state()
            current["cards"] = ((current.get("cards") or []) + [entry])[-50:]
            if summary.get("is_baseline"):
                current["baseline"] = state.get("baseline")
            self.save_state(current)
        state["cards"] = current["cards"]

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
        if card["verdict"] in WRITTEN:
            say(f"card: {out}")
        if card["verdict"] not in WRITTEN:
            return 2
        return 1 if card["verdict"] == "fail" or problems else 0

    # --------------------------------------------------------------- reuse

    def reuse(
        self,
        ref: str,
        sets: list[str] | None = None,
        decoys: bool = False,
        baseline: bool = False,
        post: bench_post.Poster | None = None,
        quiet: bool = False,
    ) -> int | None:
        """``ref``'s card without a run, where its engine inputs are unchanged.

        It looks for the newest complete card (:meth:`_source`) of the ref's
        head's engine digest (:func:`engine_digest`) and the same sets, with
        the decoy arms where they are asked for: a card with them serves a
        request without them, not the reverse. Finding one, it takes no lock,
        starts no backend and touches neither the database nor the worktree.
        It writes a card folder for the head whose ``run.json`` names the
        source, with the source's card in it (diffed afresh where the
        baseline it is read against is not the source's), records it, and
        with ``post`` posts it with a line that says whose card it is. A run
        of develop's head with ``--baseline`` is never served so: it is the
        run that makes the baseline.

        :param quiet: Say nothing when there is no card to reuse.
        :return: The exit code the source's run had, or None when the ref
            has to run.
        """
        say = (lambda line: None) if quiet else _say
        # A card posted on a PR is diffed against develop's baseline (:meth:`run`).
        baseline = baseline or post is not None
        state = self.load_state()
        chosen = choose_sets(state, sets)
        if post is not None and post.pr is None:
            post.open()
        sha = self.instance.resolve(ref)
        develop = self.instance.develop_sha()
        if baseline and post is None and sha == develop:
            say(
                f"{ref} is develop's head, and with --baseline it makes the baseline: it runs"
            )
            return None
        engine = engine_digest(self.instance, sha, state, self.tool)
        source = self._source(state, engine["digest"], chosen, decoys)
        if source is None:
            say(
                f"engine digest {engine['digest'][:12]}: no complete card of it with "
                f"set(s) {', '.join(chosen)}"
                + (" and the decoy arms" if decoys else "")
                + "; it runs"
            )
            return None
        return self._reuse(
            ref, sha, develop, chosen, engine, source, state, baseline, post, say
        )

    def _source(
        self, state: dict, digest: str, chosen: list[str], decoys: bool
    ) -> dict | None:
        """The newest card a run made of ``digest`` that can stand for a run
        now: written whole, of the same sets, with the arms if asked for, and
        nothing left out that another run might read (:func:`passing_problems`).

        :return: Its state record with its ``run.json`` as ``summary``, or None.
        """
        for record in reversed(state.get("cards") or []):
            if (
                record.get("digest") != digest
                or record.get("reused")
                or record.get("verdict") not in WRITTEN
                or (decoys and not record.get("decoys"))
            ):
                continue
            folder = self.home.cards / record["path"]
            try:
                summary = json.loads((folder / "run.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if (
                not (folder / "card.json").exists()
                or sorted(summary.get("sets") or {}) != sorted(chosen)
                or passing_problems(summary["sets"])
            ):
                continue
            return {**record, "summary": summary}
        return None

    def _reuse(
        self, ref, sha, develop, chosen, engine, source, state, baseline, post, say
    ) -> int | None:
        """Write, record, report and post the source's card as ``ref``'s."""
        cr = _compare_module()
        src = self.home.cards / source["path"]
        was = source["summary"]
        # The baseline a run of the head would read (:meth:`_run`), said in
        # the card's run log once there is one.
        base = self._baseline_card(state) if baseline else None
        no_diff, said = None, []
        if post is not None:
            if base is None:
                no_diff = "No diff: there is no baseline of develop yet."
            else:
                base, no_diff = self._pr_baseline(
                    base, state, ref, sha, chosen, post, said.append
                )
        read = list((was.get("card") or {}).get("sets") or [])
        made = json.loads((src / "card.json").read_text(encoding="utf-8"))
        # A card of develop's head is gated on its own, any other against
        # its baseline (``_card``). The source's files hold for the head when
        # both read none, or the same baseline with both develop's head or
        # neither; else the card is read again (:func:`regate`).
        absolute = sha == develop
        same = (base or {}).get("path") == (was.get("baseline") or {}).get("path")
        if same and (
            base is None or absolute == (was.get("sha") == was.get("develop"))
        ):
            files = {
                path.name: path.read_text(encoding="utf-8")
                for path in sorted(src.iterdir())
                if path.is_file() and path.name.startswith("card")
            }
        else:
            against = self._baseline_json(base, read) if base else None
            try:
                regate(made, against, absolute, cr)
            except SystemExit as why:  # a baseline of another card format
                say(
                    f"the source card cannot be read against the baseline ({why}); "
                    "it runs"
                )
                return None
            files = cr.card_files(made)
        deny = self._deny(state, {}, {})
        own = _card_words(made, cr)
        try:
            cr.scrub(files, self._tokens(deny, read, sorted(own)))
        except cr.CardRefused as refused:
            say(
                f"the card to reuse was refused by the scrub: {refused.where} holds "
                f"{self._kind(refused.token, deny)}; it runs"
            )
            return None
        diff = made.get("baseline")
        gate = made.get("gate")
        verdict = gate["verdict"] if gate else was["card"]["verdict"]
        out = self._card_dir(ref)
        with RunLog(out / "run.log") as runlog:
            runlog.say(
                f"bench run {ref}: {sha[:9]}"
                + (" (develop's head)" if sha == develop else "")
                + f", engine digest {engine['digest'][:12]}: its engine inputs are "
                f"unchanged since {was['ref']} at {str(was['sha'])[:9]}, whose card of "
                f"{was.get('finished')} this is; nothing runs"
            )
            runlog.note(f"the card reused: {src}")
            for line in said:
                runlog.say(line)
            for name, text in files.items():
                (out / name).write_text(text, encoding="utf-8")
            stamp = now()
            summary = {
                "format": SUMMARY_FORMAT,
                "ref": ref,
                "sha": sha,
                "develop": develop,
                "started": stamp,
                "finished": stamp,
                "minutes": None,
                "reference": was.get("reference"),
                "decoys": bool(was.get("decoys")),
                "migration": None,
                "engine": engine,
                "reused": {
                    "path": source["path"],
                    "ref": was["ref"],
                    "sha": was["sha"],
                    "at": was.get("finished"),
                },
                "sets": was["sets"],
                "card": {
                    **was["card"],
                    "verdict": verdict,
                    "gate": cr.gate_line(gate) if gate else None,
                },
                "baseline": (
                    {k: base[k] for k in ("ref", "sha", "at", "path")}
                    if base and diff is not None
                    else None
                ),
                "is_baseline": False,
                "no_diff": no_diff,
            }
            card = {
                "verdict": verdict,
                "sets": read,
                "files": sorted(files),
                "markdown": files.get("card.md", ""),
                "own": own,
                "gate": cr.gate_line(gate) if gate else None,
                "diff": diff,
                "deny": deny,
            }
            with STOPPER.shield():
                self._write_outputs(out, summary, card, runlog)
                self._record(state, ref, summary, card, base, out)
            problems = run_problems(summary["sets"])
            code = self._report(chosen, summary, card, problems, out, runlog.say)
            if post is not None:
                try:
                    self._post_card(post, summary, card, out, problems, no_diff, runlog)
                except bench_post.PostError as error:
                    raise bench_post.PostError(
                        f"{error}; the card is written: `bench.py post {ref} --pr "
                        f"{post.pr.number}` posts it"
                    ) from None
        return code

    # ---------------------------------------------------------------- post

    def _stale(self, base: dict, sha: str, branch: str) -> str | None:
        """Why the card is not diffed against develop's baseline on this PR,
        or None: a baseline older than the PR's merge base would read the
        changes merged since as the PR's own."""
        onto = f"origin/{branch}"
        merge_base = self.instance.merge_base(sha, onto)
        if merge_base is None:
            return f"No diff: the merge base of this PR ({sha[:9]} and {onto}) could not be found."
        if self.instance.contains(merge_base, base["sha"]):
            return None
        return (
            f"No diff: develop's baseline ({base['sha'][:9]}, made {base['at']}) is "
            f"older than this PR's merge base {merge_base[:9]}; the nightly run of "
            f"develop's head makes a newer one."
        )

    def _pr_baseline(self, base, state, ref, sha, chosen, post, say):
        """The baseline a card on ``post``'s PR is diffed against: develop's
        rolling baseline, or where it is older than the PR's merge base the
        newest card of develop behind ``sha`` (:meth:`_newest_ancestor`), or
        none.

        :return: The baseline or None, and why there is no diff or None.
        """
        no_diff = self._stale(base, sha, post.pr.base_branch)
        if not no_diff:
            return base, None
        older = self._newest_ancestor(state, sha, chosen)
        if older is None:
            say(no_diff)
            return None, no_diff
        say(self._stand_in(older, base, ref, sha, post.pr.base_branch))
        return older, None

    def _stand_in(
        self, older: dict, base: dict, ref: str, sha: str, branch: str
    ) -> str:
        """The log line for a card of develop read in place of a rolling
        baseline older than the PR's merge base. Every develop card behind
        ``sha`` is at or behind that merge base, so the line says how far
        behind it the card is read, and that develop's merges after the
        card count as the branch's (#3362)."""
        card = f"develop's card of {older['at']} ({older['ref']} at {older['sha'][:9]})"
        merge_base = self.instance.merge_base(sha, f"origin/{branch}")
        if merge_base is None:
            return (
                f"baseline: {card}, the newest card of develop behind {ref}. The "
                f"merge base of this PR could not be found, so what develop merged "
                f"after that card may count as the branch's."
            )
        if older["sha"] == merge_base:
            return (
                f"baseline: {card}, the newest card of develop behind {ref}, at "
                f"this PR's merge base."
            )
        behind = (
            f"It is older than this PR's merge base {merge_base[:9]}: what develop "
            f"merged between the two counts as the branch's."
        )
        if older["sha"] == base["sha"]:
            return (
                f"baseline: {card}, the rolling baseline, as no newer card of "
                f"develop is behind {ref}. {behind}"
            )
        return (
            f"baseline: {card}, the newest card of develop behind {ref}, in place "
            f"of the rolling baseline ({base['sha'][:9]}, made {base['at']}). "
            f"{behind}"
        )

    def _newest_ancestor(self, state: dict, sha: str, chosen) -> dict | None:
        """The newest card of develop behind ``sha`` that can be its baseline,
        or None: a card of a commit on develop and an ancestor of ``sha``, with
        a gate verdict, reading every chosen set with nothing left out. Of two
        such, the one whose commit is behind the other's is the older."""
        # Newest first, so of two cards of one commit the later one is read.
        records = list(reversed(state.get("cards") or []))
        if state.get("baseline"):
            records.append(state["baseline"])
        best, seen = None, {}
        for record in records:
            other = record.get("sha")
            if not other or record.get("verdict", "pass") not in ("pass", "fail"):
                continue
            if not set(chosen) <= set(record.get("sets") or []):
                continue
            folder = self.home.cards / record["path"]
            if (
                not (folder / "card.json").exists()
                or not (folder / "run.json").exists()
            ):
                continue
            if best is not None and (
                other == best["sha"] or not self.instance.contains(best["sha"], other)
            ):
                continue
            if other not in seen:
                seen[other] = self.instance.contains(
                    other, sha
                ) and self.instance.is_on_develop(other)
            if not seen[other]:
                continue
            summary = json.loads((folder / "run.json").read_text(encoding="utf-8"))
            read = {n: s for n, s in (summary.get("sets") or {}).items() if n in chosen}
            if run_problems(read):
                continue
            best = {
                "ref": record["ref"],
                "sha": other,
                "at": record["at"],
                "path": record["path"],
                "sets": list(record.get("sets") or []),
                "card": folder / "card.json",
            }
        return best

    def _post_card(self, post, summary, card, out, problems, no_diff, runlog) -> None:
        """Post what the run made: the card, its refusal, or why there is none."""
        cr = _compare_module()
        own = (
            set(card.get("own") or ())
            | _key_words(summary)
            | set(_words(summary["ref"]))
            | bench_post.WORDS
        )
        if (out / "diff.json").exists():
            own |= _key_words(json.loads((out / "diff.json").read_text("utf-8")))
        tokens = self._tokens(card["deny"], list(summary["sets"]), sorted(own))
        verdict = card["verdict"]
        if verdict in WRITTEN and (out / "card.json").exists():
            body = bench_post.card_body(
                summary, out, post.pr, problems, cr, no_diff=no_diff
            )
            self._publish(
                post, body, tokens, card["deny"], summary, runlog.say, runlog.note
            )
            return
        text = None
        if verdict == "refused":
            where, kind = card.get("refusal") or ("the card", bench_post.UNKNOWN_KIND)
            body = bench_post.refused_body(summary, post.pr, where, kind)
            what = f"The card was refused by the scrub: {where} holds {kind}."
        elif verdict == "error":
            text, withheld = self._scrubbed(
                card.get("tail"), tokens, card["deny"], runlog
            )
            what = f"The comparison wrote no card (exit {card.get('code')})."
            body = bench_post.failed_body(
                summary, post.pr, what, text, withheld=withheld
            )
        else:
            what = "No set could be read."
            body = bench_post.failed_body(summary, post.pr, what, None)
        self._publish_failure(
            post,
            body,
            bench_post.failure_line(summary, what, text),
            tokens,
            card["deny"],
            summary,
            runlog.say,
            runlog.note,
        )

    def _scrubbed(
        self, text, tokens, deny, runlog=None
    ) -> tuple[str | None, str | None]:
        """An error's text if the scrub lets it through, else why it is withheld."""
        if not text:
            return None, None
        cr = _compare_module()
        try:
            cr.scrub({"the error": text}, tokens)
        except cr.CardRefused as refused:
            if runlog is not None:
                runlog.note(f"the scrub refused the error's text: {refused}")
            return None, (
                f"The error's text was refused by the scrub: it holds "
                f"{self._kind(refused.token, deny)}. It is printed where the run ran."
            )
        return text, None

    def _publish(self, post, body, tokens, deny, summary, say, note) -> None:
        """Scrub the comment and post it; a comment the scrub refuses is
        replaced by the refusal, which names the kind of name, not the name."""
        cr = _compare_module()
        try:
            cr.scrub({"the comment": body}, tokens)
        except cr.CardRefused as refused:
            note(f"the scrub refused the comment: {refused}")
            kind = self._kind(refused.token, deny)
            say(
                f"the comment was refused by the scrub: it holds {kind}; posting the refusal"
            )
            body = bench_post.refused_body(summary, post.pr, bench_post.COMMENT, kind)
            try:
                cr.scrub({"the refusal": body}, tokens)
            except cr.CardRefused:
                raise bench_post.PostError(
                    "the scrub refused even the comment's refusal: nothing was posted"
                ) from None
        url, edited = post.post(body)
        say(
            ("edited the bench comment" if edited else "posted the bench comment")
            + f" on PR #{post.pr.number}: {url}"
        )

    def _publish_failure(self, post, body, line, tokens, deny, summary, say, note):
        """A run with no card: one line under the PR's bench comment, which
        keeps the card it holds; the whole failure only when there is none.

        The line is scrubbed on its own: the comment it goes under was
        scrubbed when it was posted, and is left as it is.
        """
        cr = _compare_module()
        try:
            cr.scrub({"the failure's line": line}, tokens)
        except cr.CardRefused as refused:
            note(f"the scrub refused the failure's line: {refused}")
            line = bench_post.failure_line(
                summary, f"The error holds {self._kind(refused.token, deny)}."
            )
        url = post.add_line(line)
        if url is None:
            self._publish(post, body, tokens, deny, summary, say, note)
            return
        say(f"added the failure under the bench comment on PR #{post.pr.number}: {url}")

    def _post_failure(self, post, ref, error, progress) -> None:
        """Post a run that stopped on an error or a signal: one line under
        the PR's bench comment, or the error's tail, scrubbed, where there is
        no comment yet."""
        summary = {"ref": ref, "sha": progress.get("sha"), "sets": {}}
        kind = type(error).__name__
        if isinstance(error, (Stopped, KeyboardInterrupt)):
            why = error.why if isinstance(error, Stopped) else kind
            what, detail = f"The run was stopped: {why}.", None
        else:
            what, detail = f"The run stopped: {kind}.", bench_post.tail(str(error))
        say = lambda line: print(
            f"{datetime.datetime.now():%H:%M:%S} {line}", flush=True
        )  # noqa: E731
        try:
            state = self.load_state()
            deny = self._deny(state, {}, progress.get("results") or {})
            own = set(_words(ref)) | set(_words(what)) | bench_post.WORDS
            tokens = self._tokens(deny, list(state.get("sets") or {}), sorted(own))
        except Exception as why:  # the state or the database could not be read
            say(
                f"the deny list could not be built ({type(why).__name__}): the error's text is not posted"
            )
            body = bench_post.failed_body(
                summary,
                post.pr,
                what,
                None,
                withheld="The deny list could not be built to scrub the error, so its text is not posted.",
            )
            try:
                url = post.add_line(bench_post.failure_line(summary, what))
                if url is None:
                    url, edited = post.post(body)
                say(f"posted the failure on PR #{post.pr.number}: {url}")
            except bench_post.PostError as failed:
                say(f"the failure could not be posted: {failed}")
            return
        text, withheld = self._scrubbed(detail, tokens, deny)
        body = bench_post.failed_body(summary, post.pr, what, text, withheld=withheld)
        try:
            self._publish_failure(
                post,
                body,
                bench_post.failure_line(summary, what, text),
                tokens,
                deny,
                summary,
                say,
                lambda line: None,
            )
        except bench_post.PostError as failed:
            say(f"the failure could not be posted: {failed}")

    # ---------------------------------------------------------------- post

    def post(self, ref: str, poster: bench_post.Poster) -> int:
        """``bench.py post <ref> --pr N``: post the newest written card of
        ``ref``'s current head on the PR, running nothing.

        The card is the newest of the bench's cards of ``ref`` (named as the
        run named it) that was written whole. It is refused when it is of
        another commit than the ref's head now: a card of an older head is
        no reading of the PR.
        """
        if poster.pr is None:
            poster.open()
        state = self.load_state()
        head = self.instance.resolve(ref)
        written = [
            record
            for record in state.get("cards") or []
            if record.get("ref") == ref
            and record.get("verdict") in WRITTEN
            and (self.home.cards / record["path"] / "card.json").exists()
            and (self.home.cards / record["path"] / "run.json").exists()
        ]
        if not written:
            raise RunError(
                f"the bench has no written card of {ref}: `bench.py run {ref} "
                f"--post {poster.number}` makes one"
            )
        record = written[-1]
        if record["sha"] != head:
            raise RunError(
                f"the newest card of {ref} is of {record['sha'][:9]}, not of its "
                f"head {head[:9]}: `bench.py run {ref} --post {poster.number}` "
                f"makes one of the head"
            )
        out = self.home.cards / record["path"]
        summary = json.loads((out / "run.json").read_text(encoding="utf-8"))
        made = json.loads((out / "card.json").read_text(encoding="utf-8"))
        cr = _compare_module()
        # The card's run ids are not kept: its files were scrubbed against
        # them when they were written, and are scrubbed here against the rest.
        deny = self._deny(state, {}, {})
        own = (
            _card_words(made, cr)
            | _key_words(summary)
            | set(_words(summary["ref"]))
            | bench_post.WORDS
        )
        if (out / "diff.json").exists():
            own |= _key_words(json.loads((out / "diff.json").read_text("utf-8")))
        tokens = self._tokens(deny, list(summary["sets"]), sorted(own))
        body = bench_post.card_body(
            summary,
            out,
            poster.pr,
            run_problems(summary["sets"]),
            cr,
            no_diff=summary.get("no_diff"),
        )
        with RunLog(out / "run.log") as runlog:
            runlog.say(
                f"posting the card of {ref} at {record['sha'][:9]}, made "
                f"{record['at']}, on PR #{poster.number}"
            )
            self._publish(poster, body, tokens, deny, summary, runlog.say, runlog.note)
        return 0

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
        if summary.get("reused"):
            raise RunError(
                f"{folder} reuses the card of {summary['reused']['path']}, made at "
                f"{str(summary['reused']['sha'])[:9]}: make that card the baseline"
            )
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


def regate(made: dict, baseline: dict | None, absolute: bool, cr) -> None:
    """Read a card again against another baseline, or none, as the
    comparison would have (``compare_runs.run_card``): its gate, against the
    baseline unless ``absolute``, and its diff.

    :raises SystemExit: On a baseline of another card format, as the
        comparison does.
    """
    thresholds = made.get("thresholds")
    if thresholds:
        made["gate"] = (
            cr.judge_against(made, baseline, thresholds)
            if baseline is not None and not absolute
            else cr.judge(made, thresholds)
        )
    made["baseline"] = cr.diff_cards(baseline, made) if baseline is not None else None


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


def _compare_output(log: Path) -> str:
    """What the last comparison wrote into the run log."""
    text = log.read_text(encoding="utf-8") if log.exists() else ""
    start = text.rfind("$ compare_runs.py")
    return text[start:].split("\n", 1)[1] if start >= 0 and "\n" in text[start:] else ""


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
