"""The bench's reference: peaky at a tagged release, published into the instance.

    bench.py reference --pin vX.Y.Z [--reinstall] [--jobs N] [--set NAME ...] [--wait]

Decision 29 of the assignment quality plan reads the reference engine at a
tagged release, never at a main commit. ``bench reference --pin`` makes that a
command:

1. It clones peaky into the bench's home (once) and fetches its tags. It
   exports the tag's tree and installs it with ``uv tool install --python
   3.12`` into a tool environment of its own under the bench's home
   (``UV_TOOL_DIR`` and ``UV_TOOL_BIN_DIR`` point there), so nothing else on
   the box sees that peaky. A pin installed before is reused unless
   ``--reinstall`` asks to resolve its dependencies again.
2. It asks the installed peaky for its version string, the version of the
   ``mascope-tools`` library it scores with, and which of the sets' profiles
   it knows.
3. For every sample of every set it runs ``peaky assign`` with the set's
   profile and window against the bench instance, then ``peaky publish``
   with the engine badge ``peaky`` and that version string. Peaky's batch
   command picks its own samples, so the bench runs each sample alone, as
   the gate did. A per-sample publish carries every role and peaky's own
   tier on each row, not only its Assigned rows.

A published sample is cached by (pin, set, sample, library version), and
by the profile and window it ran with: a second call at the same pin
publishes nothing and starts nothing; a set whose profile or window the
manifest changed runs again; a new pin, or a reinstall that resolved another
``mascope-tools``, publishes every set again and lists the sets whose
reference changed, for the boundary review of each peaky release (step 5.1). A set whose profile the pinned peaky does not know
is reported, not skipped silently.

This module holds the tool and the plan; ``bench.py`` gives it the instance,
the state and the lock.
"""

from __future__ import annotations

import csv
import datetime
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import threading
import tomllib
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


PEAKY_URL = "https://github.com/ultra-trace-systems/peaky.git"
PYTHON = "3.12"
ENGINE = "peaky"

#: A release tag. A pre-release (``v0.10.0b1``) or a branch is not a pin.
PIN = re.compile(r"^v\d+\.\d+\.\d+$")

#: The ledger columns that say what the reference concluded about a peak; a
#: sample's reference "changed" between two pins when these differ.
DIGEST_COLUMNS = ("peak_id", "role", "neutral_formula", "adduct", "tier")

#: The installed peaky's answers: its version string as ``publish`` stamps
#: it, the ``mascope-tools`` it scores with, and each profile name resolved
#: through its own registry (``None`` when it has no such profile).
PROBE = """
import json, sys
from importlib import metadata
from peaky.assignment import assign
from peaky.chem import profiles
from peaky.io import publish

known = {}
for name in sys.argv[1:]:
    try:
        known[name] = profiles.resolve(name).name
    except (KeyError, ValueError):
        known[name] = None
print(json.dumps({
    "version": publish.engine_version({"module_versions": assign.module_versions()}),
    "library": metadata.version("mascope-tools"),
    "profiles": known,
}))
"""

_PUBLISHED = re.compile(r"published run (\S+) \((\d+) rows, (\w+)\)")


class ReferenceError(RuntimeError):
    """A step of ``bench reference`` that cannot go on; the message says why."""


def log(message: str) -> None:
    print(f"{datetime.datetime.now():%H:%M:%S} {message}", flush=True)


def now() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def check_pin(pin: str) -> str:
    if not PIN.match(pin):
        raise ReferenceError(
            f"{pin!r} is not a release tag (vX.Y.Z): the reference is read at a "
            f"tagged release, never a pre-release or a branch"
        )
    return pin


def read_env_file(path: Path) -> dict[str, str]:
    """``KEY=VALUE`` lines, as ``bench.env`` holds them."""
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            out[key.strip()] = value.strip()
    return out


def ledger_digest(ledger: Path) -> str:
    """A digest of what a ledger concluded, peak by peak, in no particular order."""
    with open(ledger, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        columns = [c for c in DIGEST_COLUMNS if c in (reader.fieldnames or [])]
        rows = sorted(json.dumps([row[c] for c in columns]) for row in reader)
    return hashlib.sha256(
        json.dumps({"columns": columns, "rows": rows}).encode()
    ).hexdigest()


def import_id(block: str, job: "Job", digest: str) -> str:
    """The import's idempotency key: the same ledger resumes, another starts anew.

    The server returns the run it already made for a key it has seen, so a
    publish that broke off resumes under the same one; a sample assigned
    again (another digest, profile or window) gets a fresh run. ``block``
    is the reference block's own id, which fixes its pin and library: a
    later block, even one back at an earlier pin, never replays an old
    block's run.
    """
    window = json.dumps(job.window, sort_keys=True)
    text = f"{block}|{job.set_name}|{job.sample}|{job.profile}|{window}|{digest}"
    return "bench-" + hashlib.sha256(text.encode()).hexdigest()[:40]


# --------------------------------------------------------------------- tool


@dataclass
class Peaky:
    """peaky, cloned and installed at a tag under ``root`` (``<bench home>/reference``)."""

    root: Path
    url: str = PEAKY_URL
    python: str = PYTHON

    @property
    def clone(self) -> Path:
        return self.root / "peaky"

    @property
    def tools(self) -> Path:
        return self.root / "tools"

    @property
    def bin(self) -> Path:
        return self.root / "bin"

    @property
    def runs(self) -> Path:
        return self.root / "runs"

    @property
    def installed(self) -> Path:
        return self.root / "installed.json"

    @property
    def executable(self) -> Path:
        return self.bin / "peaky"

    def src(self, pin: str) -> Path:
        return self.root / "src" / pin

    def _git(self, *args: str) -> subprocess.CompletedProcess:
        out = subprocess.run(
            ["git", "-C", str(self.clone), *args],
            capture_output=True,
            check=False,
        )
        if out.returncode != 0:
            raise ReferenceError(
                f"git {' '.join(args)} failed: {out.stderr.decode(errors='replace').strip()[-400:]}"
            )
        return out

    def tag_commit(self, pin: str) -> str:
        """The commit a release tag names, from a clone fetched just now."""
        self.root.mkdir(parents=True, exist_ok=True)
        if not (self.clone / ".git").exists():
            log(f"cloning peaky from {self.url}")
            out = subprocess.run(
                ["git", "clone", "-q", self.url, str(self.clone)],
                capture_output=True,
                text=True,
                check=False,
            )
            if out.returncode != 0:
                raise ReferenceError(
                    f"cannot clone peaky from {self.url}: {out.stderr.strip()[-400:]}"
                )
        self._git("fetch", "-q", "--tags", "--force", "origin")
        out = subprocess.run(
            [
                "git",
                "-C",
                str(self.clone),
                "rev-parse",
                "--verify",
                "-q",
                f"refs/tags/{pin}^{{commit}}",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if out.returncode != 0:
            raise ReferenceError(f"peaky has no tag {pin}")
        return out.stdout.strip()

    def record(self) -> dict | None:
        if not self.installed.exists():
            return None
        return json.loads(self.installed.read_text(encoding="utf-8"))

    def tool_python(self, dist: str) -> Path:
        return self.tools / dist / "bin" / "python"

    def install(self, pin: str, commit: str, reinstall: bool = False) -> dict:
        """Install peaky at ``pin`` into the bench's tool environment, once.

        The tag's tree is exported (not checked out: the clone stays as it is)
        and installed from there. An install of the same pin and commit is
        reused unless ``reinstall`` asks to resolve its dependencies afresh.
        """
        record = self.record()
        if (
            not reinstall
            and record
            and record.get("pin") == pin
            and record.get("commit") == commit
            and self.executable.exists()
            and self.tool_python(record["dist"]).exists()
        ):
            return record
        src = self.src(pin)
        shutil.rmtree(src, ignore_errors=True)
        src.mkdir(parents=True)
        tree = self._git("archive", "--format=tar", commit).stdout
        with tarfile.open(fileobj=io.BytesIO(tree)) as tar:
            tar.extractall(src, filter="data")
        project = tomllib.loads((src / "pyproject.toml").read_text(encoding="utf-8"))
        dist = project["project"]["name"]
        env = {
            **os.environ,
            "UV_TOOL_DIR": str(self.tools),
            "UV_TOOL_BIN_DIR": str(self.bin),
        }
        # A tool installed under the same name from another source is
        # replaced, not upgraded in place.
        subprocess.run(
            ["uv", "tool", "uninstall", dist], env=env, capture_output=True, check=False
        )
        log(f"installing peaky {pin} ({commit[:9]}) into {self.tools}")
        command = ["uv", "tool", "install", "--python", self.python]
        if reinstall:
            command.append("--refresh")
        out = subprocess.run(
            [*command, str(src)], env=env, capture_output=True, text=True, check=False
        )
        if out.returncode != 0 or not self.executable.exists():
            raise ReferenceError(
                f"uv tool install of peaky {pin} failed: "
                f"{(out.stdout + out.stderr).strip()[-600:]}"
            )
        record = {"pin": pin, "commit": commit, "dist": dist, "at": now()}
        self.installed.write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
        return record

    def probe(self, record: dict, profiles: list[str]) -> dict:
        """The installed peaky's version string, library version and profiles."""
        out = subprocess.run(
            [str(self.tool_python(record["dist"])), "-c", PROBE, *profiles],
            env=self.env({}),
            capture_output=True,
            text=True,
            check=False,
        )
        if out.returncode != 0:
            raise ReferenceError(
                f"the installed peaky did not answer: {out.stderr.strip()[-600:]}"
            )
        return json.loads(out.stdout.strip().splitlines()[-1])

    def env(self, creds: dict[str, str]) -> dict[str, str]:
        """A process environment that reaches the bench and nothing else.

        The caller's own Mascope variables are dropped: peaky reads them
        before its ``--env`` file, so a sourced testbed env would send the
        reference to the testbed. ``HOME`` is the bench's, so peaky's peak
        cache and its ``~/.mascope/.env`` fallback are the bench's too.
        """
        env = {k: v for k, v in os.environ.items() if not k.startswith("MASCOPE_")}
        home = self.root / "home"
        home.mkdir(parents=True, exist_ok=True)
        env.update(
            {
                "HOME": str(home),
                "MPLBACKEND": "Agg",
                "PEAKY_PROGRESS": "0",
                "PYTHONUTF8": "1",
            }
        )
        for key in ("MASCOPE_URL", "MASCOPE_ACCESS_TOKEN"):
            if key in creds:
                env[key] = creds[key]
        return env

    def _peaky(
        self, args: list[str], creds: dict, logfile: Path
    ) -> subprocess.CompletedProcess:
        with open(logfile, "a", encoding="utf-8") as out:
            out.write(f"$ peaky {' '.join(args)}\n")
            out.flush()
            proc = subprocess.run(
                [str(self.executable), *args],
                cwd=logfile.parent,
                env=self.env(creds),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                check=False,
            )
            out.write(proc.stdout or "")
        return proc

    def assign(
        self,
        creds: dict,
        sample: str,
        workspace: str,
        profile: str,
        window: dict | None,
        out_dir: Path,
    ) -> Path:
        """``peaky assign`` on one sample; the ledger it wrote."""
        args = [
            "--workspace",
            workspace,
            "assign",
            "--sample-id",
            sample,
            "--reagent",
            profile,
            "--no-cache",
            "--output-dir",
            str(out_dir),
        ]
        if window:
            args += [
                "--ppm",
                f"{window['trust']:g}",
                "--search-ppm",
                f"{window['search']:g}",
            ]
        proc = self._peaky(args, creds, out_dir / "peaky.log")
        ledgers = sorted(out_dir.glob(f"{sample}_*_ledger.csv"))
        if proc.returncode != 0 or len(ledgers) != 1:
            raise ReferenceError(
                f"peaky assign exited {proc.returncode} with {len(ledgers)} ledger(s): "
                f"{_tail(proc.stdout)}"
            )
        return ledgers[0]

    def publish(
        self,
        creds: dict,
        ledger: Path,
        sample: str,
        workspace: str,
        version: str,
        key: str,
    ) -> dict:
        """``peaky publish`` of one ledger; the run it made."""
        args = [
            "--workspace",
            workspace,
            "publish",
            str(ledger),
            "--sample-id",
            sample,
            "--engine-version",
            version,
            "--import-id",
            key,
        ]
        proc = self._peaky(args, creds, ledger.parent / "peaky.log")
        found = _PUBLISHED.search(proc.stdout or "")
        if proc.returncode != 0 or found is None:
            raise ReferenceError(
                f"peaky publish exited {proc.returncode}: {_tail(proc.stdout)}"
            )
        return {
            "run_id": found.group(1),
            "rows": int(found.group(2)),
            "status": found.group(3),
        }


def _tail(text: str | None, n: int = 400) -> str:
    return " ".join((text or "").split())[-n:]


# --------------------------------------------------------------------- plan


@dataclass
class Job:
    set_name: str
    sample: str
    profile: str
    window: dict | None


@dataclass
class ReferencePlan:
    """What a ``bench reference`` call has to run, and what it reports."""

    jobs: list[Job] = field(default_factory=list)
    cached: int = 0
    stale: int = 0
    unrunnable: dict[str, str] = field(default_factory=dict)
    not_ready: list[str] = field(default_factory=list)


def new_reference(record: dict, probe: dict, previous: dict | None) -> dict:
    """The state's reference block for a pin and library not published before."""
    return {
        "block": uuid.uuid4().hex,
        "pin": record["pin"],
        "commit": record["commit"],
        "version": probe["version"],
        "library": probe["library"],
        "runs": {},
        "unrunnable": {},
        "changed": None,
        "previous": _summary(previous) if previous else None,
    }


def _summary(reference: dict) -> dict:
    """What a later pin compares against: the pin and each sample's digest."""
    return {
        "pin": reference["pin"],
        "version": reference.get("version"),
        "library": reference.get("library"),
        "digests": {
            set_name: {
                sample: entry["digest"]
                for sample, entry in runs.items()
                if entry.get("run_id") and entry.get("digest")
            }
            for set_name, runs in reference.get("runs", {}).items()
        },
    }


def key_of(reference: dict) -> dict:
    return {"pin": reference["pin"], "library": reference["library"]}


def plan(
    state: dict, reference: dict, profiles: dict, only: list[str] | None = None
) -> ReferencePlan:
    """Every sample of every set that has no published reference at this key."""
    p = ReferencePlan()
    unknown = [n for n in only or [] if n not in state["sets"]]
    if unknown:
        raise ReferenceError(f"the bench has no set {unknown}")
    for set_name, held in state["sets"].items():
        if only and set_name not in only:
            continue
        profile = held.get("profile")
        if not profiles.get(profile):
            p.unrunnable[set_name] = (
                f"peaky {reference['pin']} has no profile {profile!r} for this "
                f"set's chemistry"
            )
            continue
        # The runs of one block share its pin and library (a new key starts a
        # new block), so a sample with a run in it is published at this key;
        # it is current while the set's profile and window are those it ran
        # with (``init`` updates both in place).
        window = held.get("reference_window_ppm")
        runs = reference["runs"].get(set_name, {})
        for record in held["samples"].values():
            if record.get("problems"):
                p.not_ready.append(f"set {set_name}: {record['name']} is not ready")
                continue
            for item in record["items"]:
                entry = runs.get(item["sample_item_id"]) or {}
                if entry.get("run_id"):
                    if (entry.get("profile"), entry.get("window")) == (profile, window):
                        p.cached += 1
                        continue
                    p.stale += 1
                p.jobs.append(Job(set_name, item["sample_item_id"], profile, window))
    return p


def changed_sets(reference: dict) -> dict | None:
    """Per set, how many samples the reference reads differently than at the last pin."""
    previous = reference.get("previous")
    if not previous:
        return None
    out = {}
    # A set run at only one of the two pins is listed too, as compared on
    # no sample.
    names = dict.fromkeys([*reference["runs"], *previous["digests"]])
    for set_name in names:
        runs = reference["runs"].get(set_name, {})
        before = previous["digests"].get(set_name, {})
        both = [
            s
            for s, entry in runs.items()
            if entry.get("digest") and entry.get("run_id") and s in before
        ]
        changed = sum(1 for s in both if runs[s]["digest"] != before[s])
        out[set_name] = {"changed": changed, "compared": len(both)}
    return {"against": previous["pin"], "sets": out}


# ---------------------------------------------------------------------- run


@dataclass
class Reference:
    """``bench reference`` over one bench home, its instance and its state.

    :ivar load_state: Reads the bench's state.
    :ivar save_state: Writes it back.
    :ivar connect: Signs in on a started instance and writes ``bench.env``.
    """

    peaky: Peaky
    instance: object
    connect: Callable[[str], object]
    env_file: Path
    load_state: Callable[[], dict]
    save_state: Callable[[dict], None]
    jobs: int = 2

    def run(
        self, pin: str, reinstall: bool = False, only: list[str] | None = None
    ) -> int:
        check_pin(pin)
        state = self.load_state()
        if not state["sets"]:
            raise ReferenceError(
                "the bench holds no sets yet: run `bench.py init` first"
            )
        before = json.dumps(state, sort_keys=True)
        commit = self.peaky.tag_commit(pin)
        record = self.peaky.install(pin, commit, reinstall=reinstall)
        profiles = sorted(
            {s.get("profile") for s in state["sets"].values() if s.get("profile")}
        )
        probe = self.peaky.probe(record, profiles)
        reference = state.get("reference")
        if reference is None or key_of(reference) != {
            "pin": pin,
            "library": probe["library"],
        }:
            if reference is not None:
                log(
                    f"the reference moves from {reference['pin']} "
                    f"(mascope-tools {reference.get('library')}) to {pin} "
                    f"(mascope-tools {probe['library']}): every set is published again"
                )
            reference = new_reference(record, probe, reference)
        reference.update(
            {
                "commit": record["commit"],
                "version": probe["version"],
                "installed": record["at"],
            }
        )
        state["reference"] = reference
        todo = plan(state, reference, probe["profiles"], only)
        for set_name, why in todo.unrunnable.items():
            reference["unrunnable"][set_name] = why
        for set_name in [
            n for n in reference["unrunnable"] if n not in todo.unrunnable
        ]:
            if not only or set_name in only:
                reference["unrunnable"].pop(set_name)
        log(
            f"peaky {pin} ({probe['version']}, mascope-tools {probe['library']}): "
            f"{todo.cached} sample(s) published already, {len(todo.jobs)} to run"
            + (
                f" ({todo.stale} again, at a profile or window the manifest changed)"
                if todo.stale
                else ""
            )
        )

        failures: list[str] = []
        if todo.jobs:
            failures = self._run_jobs(state, reference, todo.jobs)
        reference["changed"] = changed_sets(reference)
        if json.dumps(state, sort_keys=True) != before:
            self.save_state(state)
        return self._report(reference, todo, failures)

    def _run_jobs(self, state: dict, reference: dict, jobs: list[Job]) -> list[str]:
        self.instance.check_env()
        self.instance.bind()
        base = self.instance.start(peak_assignment=True)
        failures: list[str] = []
        lock = threading.Lock()
        try:
            self.connect(base)
            creds = read_env_file(self.env_file)
            workspaces = self.instance.sample_workspaces()
            key = key_of(reference)

            def one(job: Job) -> None:
                entry = {
                    "key": key,
                    "profile": job.profile,
                    "window": job.window,
                    "at": now(),
                }
                try:
                    workspace = workspaces.get(job.sample)
                    if workspace is None:
                        raise ReferenceError("the instance has no such sample")
                    entry.update(self._one(creds, reference, job, workspace))
                except (ReferenceError, OSError) as error:
                    entry["error"] = str(error)
                with lock:
                    reference["runs"].setdefault(job.set_name, {})[job.sample] = entry
                    self.save_state(state)
                    if "error" in entry:
                        failures.append(
                            f"set {job.set_name}: sample {job.sample}: {entry['error']}"
                        )
                    done = sum(len(r) for r in reference["runs"].values())
                    log(
                        f"set {job.set_name}: {job.sample} "
                        + (
                            f"failed: {entry['error'][:200]}"
                            if "error" in entry
                            else f"published run {entry['run_id']} ({entry['rows']} rows)"
                        )
                        + f" [{done} on record]"
                    )

            with ThreadPoolExecutor(max_workers=max(1, self.jobs)) as pool:
                list(pool.map(one, jobs))
        finally:
            self.instance.stop()
        return failures

    def _one(self, creds: dict, reference: dict, job: Job, workspace: str) -> dict:
        out_dir = self.peaky.runs / reference["pin"] / job.set_name / job.sample
        shutil.rmtree(out_dir, ignore_errors=True)
        out_dir.mkdir(parents=True)
        ledger = self.peaky.assign(
            creds, job.sample, workspace, job.profile, job.window, out_dir
        )
        digest = ledger_digest(ledger)
        key = import_id(reference["block"], job, digest)
        published = self.peaky.publish(
            creds, ledger, job.sample, workspace, reference["version"], key
        )
        if published["status"] != "completed":
            raise ReferenceError(
                f"run {published['run_id']} ended {published['status']!r}, not completed"
            )
        return {**published, "digest": digest, "import_id": key}

    def _report(self, reference: dict, todo: ReferencePlan, failures: list[str]) -> int:
        for set_name, runs in reference["runs"].items():
            ok = sum(1 for e in runs.values() if e.get("run_id"))
            log(
                f"set {set_name}: {ok}/{len(runs)} sample(s) carry the reference at {reference['pin']}"
            )
        changed = reference.get("changed")
        if changed:
            against = changed["against"]
            moved = {n: c for n, c in changed["sets"].items() if c["changed"]}
            compared = [n for n, c in changed["sets"].items() if c["compared"]]
            uncompared = [n for n, c in changed["sets"].items() if not c["compared"]]
            if moved:
                log(
                    f"sets whose reference changed since {against}: "
                    + ", ".join(
                        f"{n} ({c['changed']}/{c['compared']} samples)"
                        for n, c in moved.items()
                    )
                )
            elif compared:
                log(f"no set's reference changed since {against}")
            if uncompared:
                log(
                    f"not compared with {against} (no sample published at both "
                    f"pins): {', '.join(uncompared)}"
                )
        problems = (
            [f"set {n} not run: {why}" for n, why in reference["unrunnable"].items()]
            + todo.not_ready
            + failures
        )
        if problems:
            log(f"{len(problems)} problem(s):")
            for problem in problems:
                print(f"  {problem}", flush=True)
            return 1
        return 0


def status_lines(reference: dict | None) -> list[str]:
    """What ``bench status`` says about the reference."""
    if not reference:
        return ["reference none yet"]
    runs = reference.get("runs", {})
    published = sum(1 for r in runs.values() for e in r.values() if e.get("run_id"))
    failed = sum(1 for r in runs.values() for e in r.values() if e.get("error"))
    lines = [
        f"reference {reference['pin']}  version {reference.get('version')}, "
        f"mascope-tools {reference.get('library')}, {published} sample(s) published"
        + (f", {failed} failed" if failed else "")
    ]
    for set_name, why in (reference.get("unrunnable") or {}).items():
        lines.append(f"  {set_name:<4} not run: {why}")
    changed = reference.get("changed")
    if changed:
        sets = changed["sets"]
        moved = [
            f"{n} {c['changed']}/{c['compared']}"
            for n, c in sets.items()
            if c["changed"]
        ]
        if any(c["compared"] for c in sets.values()):
            lines.append(
                f"  changed since {changed['against']}: {', '.join(moved) or 'none'}"
            )
        uncompared = [n for n, c in sets.items() if not c["compared"]]
        if uncompared:
            lines.append(
                f"  not compared with {changed['against']}: {', '.join(uncompared)}"
            )
    return lines
