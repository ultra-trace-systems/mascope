"""The assignment bench: one persistent instance holding fixed sample sets.

    bench.py init <manifest> [--ref REF] [--rebuild] [--checkout DIR] [--keep-running] [--wait]
    bench.py reference --pin vX.Y.Z [--reinstall] [--jobs N] [--set NAME ...] [--wait]
    bench.py status

``init`` builds the bench instance (env ``bench``) from a sets manifest
(``manifest.py``; ``manifests/demo.json`` is the public one): it binds the env
to a free instance slot in the runtime home's registry, checks the ref out
into the bench's own worktree and starts its backend and file converter on
that slot, seeds each set's ionization mode and target collections through the
API and the shipped reference lists with ``mascope reference seed``, uploads
every file the instance does not hold yet through the resumable upload route,
waits for the pipeline, and checks that every sample resolved its set's mode
and has peaks. It records each sample's id under its set's name in
``state.json``, then stops the backend; the instance's database and filestore
stay. A second ``init`` over the same manifest finds nothing to do and starts
nothing; ``--rebuild`` drops the database and the filestore and builds again.

``reference --pin`` installs peaky at a release tag and publishes its run of
every sample into the instance beside the in-app runs (``reference.py``).

Everything the bench keeps lives in its home, ``$MASCOPE_PATH/.runtime/bench``
(``--home`` or ``MASCOPE_BENCH_HOME`` elsewhere): a copy of the manifest with
``state.json`` beside it, the bench account's credentials, ``bench.env`` (the
instance's URL and an SDK token), the worktree, the logs, the cards. The
runtime home is private to the box, so the sample ids and a private
manifest's corpus names never reach a checkout.

Run it from a checkout with the dev stack up (``mascope dev up``):
``uv run python tooling/assignment_bench/bench.py init tooling/assignment_bench/manifests/demo.json``.
"""

from __future__ import annotations

import argparse
import collections
import contextlib
import datetime
import fcntl
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Protocol


_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import manifest as bench_manifest  # noqa: E402
import reference as bench_reference  # noqa: E402
from manifest import Manifest, ManifestError, ResolvedFile  # noqa: E402


REPO = _HERE.parents[1]
STATE_SCHEMA = "mascope-bench-state/1"
DEFAULT_ENV = "bench"
BENCH_EMAIL = "bench@mascope.app"
BENCH_USERNAME = "assignmentbench"

#: A file's processing statuses while the pipeline still works on it
#: (``ProcessingStatus`` in the backend; ``None`` is a row the converter has
#: only just written).
RUNNING = {None, "converted", "queued", "bound", "calibrated"}

#: How long the pipeline may take over one pass of uploads, and how often
#: and how many times in a row it must look settled.
WAIT_S = 3600.0
POLL_S = 15.0
SETTLED_POLLS = 3

#: Upload attempts per file. The server refuses an upload with 503 while no
#: file converter is connected, which it is not for a while after start.
UPLOAD_ATTEMPTS = 20
UPLOAD_PAUSE_S = 15.0


class BenchError(RuntimeError):
    """A step of the bench that cannot go on; the message says why."""


class ApiError(BenchError):
    """The instance's API answered a read with an error; the message says which."""


class UploadError(BenchError):
    """An upload the server did not take.

    :ivar retry: Whether the same upload can succeed later: a 503 while no
        file converter is connected, or a connection that failed. A refused
        token or a refused name or hash comes back the same every time.
    """

    def __init__(self, message: str, retry: bool):
        super().__init__(message)
        self.retry = retry


#: The names an env may have: they go into a database name, unquoted SQL
#: text and a path.
_ENV_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,47}$")


def log(message: str) -> None:
    print(f"{datetime.datetime.now():%H:%M:%S} {message}", flush=True)


def now() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


# --------------------------------------------------------------------- home


def default_home() -> Path:
    if os.environ.get("MASCOPE_BENCH_HOME"):
        return Path(os.environ["MASCOPE_BENCH_HOME"])
    if not os.environ.get("MASCOPE_PATH"):
        raise BenchError("MASCOPE_PATH is not set: the bench lives in the runtime home")
    return Path(os.environ["MASCOPE_PATH"]) / ".runtime" / "bench"


@dataclass
class Home:
    """The bench's folder and what it keeps there."""

    root: Path

    @property
    def manifest(self) -> Path:
        return self.root / "manifest.json"

    @property
    def state(self) -> Path:
        return self.root / "state.json"

    @property
    def creds(self) -> Path:
        return self.root / "creds.json"

    @property
    def env_file(self) -> Path:
        return self.root / "bench.env"

    @property
    def worktree(self) -> Path:
        return self.root / "worktree"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def cards(self) -> Path:
        return self.root / "cards"

    @property
    def pid(self) -> Path:
        return self.root / "backend.pid"

    def make(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.root.chmod(0o700)
        for sub in (self.logs, self.cards):
            sub.mkdir(exist_ok=True)

    @contextlib.contextmanager
    def lock(self, wait: bool = False):
        """One bench command at a time on this home."""
        self.make()
        handle = open(self.root / "bench.lock", "a+")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        except BlockingIOError:
            handle.seek(0)
            holder = handle.read().strip() or "?"
            handle.close()
            raise BenchError(
                f"another bench command holds the lock ({holder}); --wait to queue"
            ) from None
        handle.seek(0)
        handle.truncate()
        handle.write(f"pid {os.getpid()} since {now()}\n")
        handle.flush()
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
            handle.close()

    def write_private(self, path: Path, text: str) -> None:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.chmod(0o600)
        tmp.replace(path)


# -------------------------------------------------------------------- state


def new_state(env: str) -> dict:
    return {
        "schema": STATE_SCHEMA,
        "env": env,
        "slot": None,
        "api_port": None,
        "build": None,
        "manifest": None,
        "seeded": {"modes": {}, "collections": {}, "reference_lists": []},
        "sets": {},
        "reference": None,
        "cards": [],
        "created": now(),
        "updated": None,
    }


def load_state(home: Home, env: str) -> dict:
    if not home.state.exists():
        return new_state(env)
    state = json.loads(home.state.read_text(encoding="utf-8"))
    if state.get("schema") != STATE_SCHEMA:
        raise BenchError(f"{home.state} is not a bench state ({state.get('schema')!r})")
    if state["env"] != env:
        raise BenchError(
            f"{home.state} is the state of env {state['env']!r}, not {env!r}"
        )
    return state


def save_state(home: Home, state: dict) -> None:
    state["updated"] = now()
    home.write_private(home.state, json.dumps(state, indent=1) + "\n")


# --------------------------------------------------------------------- plan


@dataclass
class Plan:
    """What an ``init`` has to do to bring the instance to the manifest."""

    modes: list[str] = field(default_factory=list)
    collections: list[str] = field(default_factory=list)
    reference_lists: list[str] = field(default_factory=list)
    uploads: list[str] = field(default_factory=list)
    recheck: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (
            self.modes
            or self.collections
            or self.reference_lists
            or self.uploads
            or self.recheck
        )


def reference_lists_of(m: Manifest) -> list[str]:
    """The reference lists the manifest's sets ask for, in their order."""
    out: list[str] = []
    for s in m.sets.values():
        for name in (
            ("default",) if s.reference_lists == "default" else s.reference_lists
        ):
            if name not in out:
                out.append(name)
    return out


def plan(m: Manifest, state: dict) -> Plan:
    """Compare a manifest with what the state says the instance holds.

    The bench only grows: a set or file the instance holds that the manifest
    no longer names, or a set whose mode changed under samples already bound
    to the old one, is refused, since the instance cannot forget it; the
    answer is ``--rebuild``.
    """
    p = Plan()
    seeded = state["seeded"]
    for s in m.sets.values():
        if s.mode not in seeded["modes"] and s.mode not in p.modes:
            p.modes.append(s.mode)
        for name in s.collections:
            key = f"{s.seed.key}#{name}"
            if key not in seeded["collections"] and name not in p.collections:
                p.collections.append(name)
    p.reference_lists = [
        n for n in reference_lists_of(m) if n not in seeded["reference_lists"]
    ]

    wanted = {ref.key: name for name, ref in m.files()}
    for set_name, held in state["sets"].items():
        s = m.sets.get(set_name)
        if s is None:
            p.refused.append(
                f"set {set_name} is in the instance but not in the manifest"
            )
            continue
        if held["mode"] != s.mode and held["samples"]:
            p.refused.append(
                f"set {set_name}'s mode changed from {held['mode']!r} to {s.mode!r}"
            )
        for key in held["samples"]:
            if wanted.get(key) != set_name:
                p.refused.append(
                    f"set {set_name}'s {key} is in the instance but not in the set"
                )
    for key, set_name in wanted.items():
        held = state["sets"].get(set_name, {}).get("samples", {}).get(key)
        # A file the instance never held (its uploads refused, or lost twice)
        # is sent again; one it holds is only looked at again.
        if held is None or held.get("sample_file_id") is None:
            p.uploads.append(key)
        elif held.get("problems"):
            p.recheck.append(key)
    return p


# --------------------------------------------------------------------- seed


_COPY = re.compile(r"^COPY (\S+) \(([^)]*)\) FROM stdin;\n(.*?)^\\\.$", re.S | re.M)
_ESCAPE = re.compile(r"\\(\\|[btnrfv]|[0-7]{1,3}|x[0-9A-Fa-f]{1,2})")
_ESCAPES = {
    "b": "\b",
    "t": "\t",
    "n": "\n",
    "r": "\r",
    "f": "\f",
    "v": "\v",
    "\\": "\\",
}


def _unescape(text: str) -> str | None:
    if text == r"\N":
        return None

    def one(m: re.Match) -> str:
        code = m.group(1)
        if code in _ESCAPES:
            return _ESCAPES[code]
        if code[0] == "x":
            return chr(int(code[1:], 16))
        return chr(int(code, 8))

    return _ESCAPE.sub(one, text)


def parse_copy(sql: str) -> dict[str, list[dict]]:
    """The rows of every ``COPY ... FROM stdin`` block of a plain SQL dump."""
    tables: dict[str, list[dict]] = {}
    for m in _COPY.finditer(sql):
        table = m.group(1).split(".")[-1].strip('"')
        columns = [c.strip().strip('"') for c in m.group(2).split(",")]
        rows = []
        for line in m.group(3).split("\n"):
            if line:
                rows.append(
                    dict(zip(columns, (_unescape(v) for v in line.split("\t"))))
                )
        tables[table] = rows
    return tables


def seed_from_tables(tables: dict[str, list[dict]]) -> dict:
    """A demo bundle seed's tables in the corpus seed's shape.

    The corpus seed (``seed.json``) lists mechanisms, modes, collections,
    compounds and collection members by their ids; a bundle's seed is a
    ``pg_dump`` of the same tables. Modes the server ships itself
    (``system_key``) are left out: every instance has them.
    """
    modes = []
    for row in tables.get("ionization_mode", []):
        if row.get("system_key"):
            continue
        modes.append(
            {
                "ionization_mode_id": row["ionization_mode_id"],
                "ionization_mode_name": row["ionization_mode_name"],
                "ionization_mode_token": row["ionization_mode_token"],
                "ionization_mode_polarity": row["ionization_mode_polarity"],
                "ionization_mechanism_ids": json.loads(
                    row["ionization_mechanism_ids"] or "[]"
                ),
                "calibration_collection_id": row["calibration_collection_id"],
                "diagnostic_collection_id": row["diagnostic_collection_id"],
            }
        )
    return {
        "ionization_mechanisms": tables.get("ionization_mechanism", []),
        "ionization_modes": modes,
        "target_collections": tables.get("target_collection", []),
        "target_compounds": tables.get("target_compound", []),
        "collection_members": tables.get("target_compound_in_target_collection", []),
    }


def read_bundle_seed(dump: Path, container: str) -> dict:
    """Read a bundle's ``seed/`` dump with the dev Postgres's ``pg_restore``."""
    with open(dump, "rb") as fh:
        out = subprocess.run(
            ["docker", "exec", "-i", container, "pg_restore", "--data-only", "-f", "-"],
            stdin=fh,
            capture_output=True,
            check=False,
        )
    if out.returncode != 0:
        raise BenchError(
            f"pg_restore could not read {dump}: {out.stderr.decode()[-400:]}"
        )
    return seed_from_tables(parse_copy(out.stdout.decode("utf-8")))


def select_seed(seed: dict, modes: Iterable[str], collections: Iterable[str]) -> dict:
    """The part of a seed that the named modes and collections need.

    A mode brings its mechanisms and its calibration and diagnostic
    collections; a named collection comes by its name, which must be unique
    in the seed (the corpus seed has collections that share a name).
    """
    modes, collections = list(modes), list(collections)
    by_name = {m["ionization_mode_name"]: m for m in seed["ionization_modes"]}
    missing = [n for n in modes if n not in by_name]
    if missing:
        raise BenchError(f"the seed has no mode {missing}")
    chosen = [by_name[n] for n in modes]
    coll_ids = []
    for m in chosen:
        for key in ("calibration_collection_id", "diagnostic_collection_id"):
            if m[key] and m[key] not in coll_ids:
                coll_ids.append(m[key])
    named = collections_by_name(seed)
    for name in collections:
        ids = named.get(name, [])
        if len(ids) != 1:
            raise BenchError(
                f"the seed has {len(ids)} collections named {name!r}; a set names one"
            )
        if ids[0] not in coll_ids:
            coll_ids.append(ids[0])
    mech_ids = {i for m in chosen for i in m["ionization_mechanism_ids"]}
    members = [
        link
        for link in seed["collection_members"]
        if link["target_collection_id"] in coll_ids
    ]
    compound_ids = {link["target_compound_id"] for link in members}
    by_id = {c["target_collection_id"]: c for c in seed["target_collections"]}
    return {
        "ionization_mechanisms": [
            m
            for m in seed["ionization_mechanisms"]
            if m["ionization_mechanism_id"] in mech_ids
        ],
        "ionization_modes": chosen,
        "target_collections": [by_id[i] for i in coll_ids if i in by_id],
        "target_compounds": [
            c
            for c in seed["target_compounds"]
            if c["target_compound_id"] in compound_ids
        ],
        "collection_members": members,
    }


def collections_by_name(seed: dict) -> dict[str, list[str]]:
    out = collections.defaultdict(list)
    for c in seed["target_collections"]:
        out[c["target_collection_name"]].append(c["target_collection_id"])
    return out


def _spellings(mechanism: dict) -> list[tuple[str, str]]:
    """The spellings a seed mechanism may be stored under, likeliest first.

    An older seed spells mechanisms as the fleet stored them (``+Br-``); the
    server stores the standard adduct notation (``[M+Br]-``).
    """
    try:
        from mascope_tools.composition.mechanism_notation import standard_notation
    except ImportError:  # pragma: no cover - the workspace always has it
        standard_notation = None
    out = [mechanism["ionization_mechanism"]]
    if standard_notation is not None:
        try:
            std = standard_notation(mechanism["ionization_mechanism"])
        except ValueError:
            std = None
        if std and std not in out:
            out.append(std)
    return [(s, mechanism["ionization_mechanism_polarity"]) for s in out]


def load_seed(api: "Api", seed: dict, source: str, seeded: dict) -> list[str]:
    """Load a selected seed into the instance through the API.

    The way the corpus loader does it (paja's ``corpus/load-seed.py``):
    mechanisms first, then each collection with its compounds inline, then
    the modes, every reference remapped to the ids the server gave. A mode
    the server already holds by name is not created again, nor a collection
    ``seeded`` records, so a second load changes nothing. ``seeded`` is
    updated in place as rows land, so an interrupted load resumes.

    :return: The problems, empty when everything loaded.
    """
    problems: list[str] = []
    have = collections.defaultdict(list)
    for row in api.get_list("/api/ionization_mechanisms"):
        key = (row["ionization_mechanism"], row["ionization_mechanism_polarity"])
        have[key].append(row["ionization_mechanism_id"])
    idmap: dict[str, str] = {}
    for mech in seed["ionization_mechanisms"]:
        found = next((have[s][0] for s in _spellings(mech) if s in have), None)
        if found is None:
            created = api.post(
                "/api/ionization_mechanisms",
                {
                    "ionization_mechanism": mech["ionization_mechanism"],
                    "ionization_mechanism_polarity": mech[
                        "ionization_mechanism_polarity"
                    ],
                },
            )
            found = (created or {}).get("ionization_mechanism_id")
        if found is None:
            problems.append(
                f"mechanism {mech['ionization_mechanism']} could not be created"
            )
        else:
            idmap[mech["ionization_mechanism_id"]] = found

    existing_modes = {
        m["ionization_mode_name"] for m in api.get_list("/api/ionization/modes")
    }
    compound = {c["target_compound_id"]: c for c in seed["target_compounds"]}
    members = collections.defaultdict(list)
    for link in seed["collection_members"]:
        members[link["target_collection_id"]].append(link["target_compound_id"])
    for coll in seed["target_collections"]:
        key = f"{source}#{coll['target_collection_name']}"
        if key in seeded["collections"]:
            idmap[coll["target_collection_id"]] = seeded["collections"][key]
            continue
        payload = {
            "target_collection_name": coll["target_collection_name"],
            "target_collection_description": coll.get("target_collection_description")
            or "",
            "target_collection_type": coll["target_collection_type"],
        }
        comps = [
            {
                "target_compound_name": compound[pid].get("target_compound_name") or "",
                "target_compound_formula": compound[pid]["target_compound_formula"],
                "cas_number": compound[pid].get("cas_number"),
            }
            for pid in members.get(coll["target_collection_id"], [])
            if pid in compound
        ]
        if comps:
            payload["target_compounds_create"] = comps
        created = api.post("/api/target/collections", payload)
        server_id = (created or {}).get("target_collection_id")
        if server_id is None:
            problems.append(
                f"collection {coll['target_collection_name']!r} could not be created"
            )
            continue
        idmap[coll["target_collection_id"]] = server_id
        seeded["collections"][key] = server_id

    for mode in seed["ionization_modes"]:
        name = mode["ionization_mode_name"]
        if name in existing_modes:
            seeded["modes"].setdefault(name, source)
            continue
        lost = [
            field_
            for field_ in ("calibration_collection_id", "diagnostic_collection_id")
            if mode[field_] and mode[field_] not in idmap
        ]
        lost += [m for m in mode["ionization_mechanism_ids"] if m not in idmap]
        if lost:
            problems.append(f"mode {name!r} is missing {lost}; not created")
            continue
        payload = {
            "ionization_mode_name": name,
            "ionization_mode_polarity": mode["ionization_mode_polarity"],
            "ionization_mechanism_ids": [
                idmap[m] for m in mode["ionization_mechanism_ids"]
            ],
        }
        if mode.get("ionization_mode_token"):
            payload["ionization_mode_token"] = mode["ionization_mode_token"]
        for field_ in ("calibration_collection_id", "diagnostic_collection_id"):
            if mode[field_]:
                payload[field_] = idmap[mode[field_]]
        if api.post("/api/ionization/modes", payload) is None:
            problems.append(f"mode {name!r} could not be created")
            continue
        seeded["modes"][name] = source
    return problems


# ---------------------------------------------------------------------- api


class Api(Protocol):
    """What the bench asks of the instance's API (a fake in the tests)."""

    def get_list(self, path: str) -> list[dict]: ...

    def post(self, path: str, payload: dict) -> dict | None: ...

    def upload(self, file: ResolvedFile) -> None:
        """Upload one file; :class:`UploadError` when the server did not take it."""

    def peak_count(self, sample_item_id: str) -> int:
        """A sample's peak count; :class:`ApiError` when the API answers an error."""


class HttpApi:
    """The instance's API over HTTP, signed in as the bench's owner."""

    def __init__(self, base: str, home: Home):
        import requests

        self.base = base.rstrip("/")
        self.home = home
        self.session = requests.Session()
        self.errors: list[str] = []
        self._agent_token: str | None = None

    # -- account

    def sign_in(self, owner_secret: Path) -> None:
        """Sign in as the bench's owner, registering it on a fresh instance."""
        if not self.home.creds.exists():
            secret = owner_secret.read_text(encoding="utf-8").strip()
            # A throwaway owner for the bench's own database. The password
            # must not hold the email's local part or the username.
            password = secrets.token_urlsafe(18) + "aZ7!"
            r = self.session.post(
                f"{self.base}/api/users/first-owner",
                json={
                    "email": BENCH_EMAIL,
                    "username": BENCH_USERNAME,
                    "password": password,
                    "server_secret": secret,
                },
                timeout=30,
            )
            if r.status_code not in (200, 201):
                raise BenchError(
                    f"first-owner registration answered {r.status_code}: {r.text[:300]}"
                )
            self.home.write_private(
                self.home.creds,
                json.dumps({"email": BENCH_EMAIL, "password": password}),
            )
        creds = json.loads(self.home.creds.read_text(encoding="utf-8"))
        r = self.session.post(
            f"{self.base}/api/auth/login",
            data={
                "grant_type": "password",
                "username": creds["email"],
                "password": creds["password"],
            },
            timeout=30,
        )
        if r.status_code not in (200, 204):
            raise BenchError(
                f"the bench account cannot sign in ({r.status_code}); a database "
                f"rebuilt without the bench? --rebuild starts it over"
            )

    def token(self, service: str) -> str:
        r = self.session.post(
            f"{self.base}/api/auth/access_token/regenerate",
            json={"service_name": service},
            timeout=30,
        )
        if r.status_code != 200:
            raise BenchError(f"no {service} token ({r.status_code}): {r.text[:300]}")
        body = r.json()
        token = body.get("access_token") or (body.get("data") or {}).get("access_token")
        if not token:
            raise BenchError(f"the {service} token answer holds no token: {list(body)}")
        return token

    def write_env_file(self) -> None:
        """``bench.env``: the instance's URL and an SDK token, for the later steps."""
        sdk = self.token("mascope_sdk")
        self.home.write_private(
            self.home.env_file,
            f"MASCOPE_URL={self.base}\nMASCOPE_ACCESS_TOKEN={sdk}\n",
        )

    # -- Api

    def get_list(self, path: str) -> list[dict]:
        r = self.session.get(
            f"{self.base}{path}", params={"page": 0, "limit": 10000}, timeout=300
        )
        r.raise_for_status()
        body = r.json()
        rows = body.get("data", body) if isinstance(body, dict) else body
        return rows or []

    def post(self, path: str, payload: dict) -> dict | None:
        r = self.session.post(f"{self.base}{path}", json=payload, timeout=900)
        if r.status_code not in (200, 201):
            self.errors.append(f"POST {path}: {r.status_code} {r.text[:300]}")
            return None
        try:
            body = r.json()
        except ValueError:
            return {}
        data = body.get("data", body) if isinstance(body, dict) else {}
        return data if isinstance(data, dict) else {}

    def upload(self, file: ResolvedFile) -> None:
        """Upload one file as the File Agent does, its hash with it."""
        import mascope_sdk
        from mascope_sdk import api_post_file_tus
        from mascope_sdk.exceptions import MascopeError

        if self._agent_token is None:
            self._agent_token = self.token("file-agent")
        mascope_sdk.SERVICE_NAME = "file-agent"
        try:
            api_post_file_tus(
                url=self.base,
                access_token=self._agent_token,
                filepath=str(file.path),
                upload_filename=file.name,
                sha256=file.sha256,
            )
        except MascopeError as error:
            raise upload_error(error) from None

    def peak_count(self, sample_item_id: str) -> int:
        r = self.session.get(
            f"{self.base}/api/samples/{sample_item_id}/peaks",
            params={"areas": "false", "heights": "true"},
            timeout=300,
        )
        if r.status_code != 200:
            raise ApiError(f"peaks answered {r.status_code}")
        return count_peaks(r.json())


def upload_error(error: Exception) -> UploadError:
    """An SDK upload error as an :class:`UploadError`, retried only when it may pass.

    The server answers 503 while no file converter is connected, which it is
    not for a while after start, and a connection can drop; anything else
    (a refused token, a refused name or hash) comes back the same.
    """
    from mascope_sdk.exceptions import MascopeConnectionError

    retry = (
        isinstance(error, MascopeConnectionError)
        or getattr(error, "status_code", None) == 503
    )
    return UploadError(str(error), retry=retry)


def count_peaks(body) -> int:
    """How many peaks a sample peaks answer holds, whatever its wrapping."""
    data = body.get("data", body) if isinstance(body, dict) else body
    if isinstance(data, dict):
        for key in ("mz", "sample_peak_mz", "peaks"):
            if isinstance(data.get(key), list):
                return len(data[key])
        return 0
    return len(data) if isinstance(data, list) else 0


# ----------------------------------------------------------------- instance


class Instance(Protocol):
    """The instance's processes, database and files (a fake in the tests)."""

    env: str

    def check_env(self) -> None:
        """Refuse an env that is not the bench's own, before anything touches it."""

    def bind(self) -> tuple[int, int]: ...

    def checkout(self, ref: str) -> str: ...

    def start(self, peak_assignment: bool = False) -> str:
        """Start the backend; ``peak_assignment`` switches the feature on for it."""

    def stop(self) -> None: ...

    def drop(self) -> None: ...

    def reference_seed(self, lists: list[str]) -> None: ...

    def samples(self) -> dict[str, dict]: ...

    def sample_workspaces(self) -> dict[str, str]:
        """Each sample's workspace id, by sample id."""

    def streams(self) -> tuple[list[str], list[str]]: ...

    def bundle_seed(self, version: str) -> dict: ...

    def owner_secret(self) -> Path: ...


class DevInstance:
    """The bench env on this box's dev stack (``mascope dev``)."""

    def __init__(
        self,
        home: Home,
        env: str = DEFAULT_ENV,
        repo: Path = REPO,
        runtime: Path | None = None,
    ):
        if not _ENV_NAME.match(env):
            raise BenchError(
                f"{env!r} is not an env name (letters, digits, '-' and '_')"
            )
        self.home = home
        self.env = env
        self.repo = repo
        self._runtime = runtime
        self.api_port: int | None = None

    # -- paths and names

    @property
    def runtime(self) -> Path:
        """The runtime home, ``MASCOPE_PATH``; only what touches the instance needs it."""
        if self._runtime is None:
            if not os.environ.get("MASCOPE_PATH"):
                raise BenchError(
                    "MASCOPE_PATH is not set: the bench's instance lives in the runtime home"
                )
            self._runtime = Path(os.environ["MASCOPE_PATH"])
        return self._runtime

    @property
    def env_dir(self) -> Path:
        return self.runtime / ".runtime" / "env" / self.env

    def _db(self) -> tuple[str, str, str]:
        from mascope_cli.runtime import runtime

        db_cfg = runtime.full_config.backend.database
        return (
            db_cfg.get_postgres_container_name(mode="dev"),
            db_cfg.user,
            db_cfg.get_postgres_database_name(self.env),
        )

    def _cli(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        env = {**os.environ, "MASCOPE_ENV": self.env, "PYTHONUTF8": "1"}
        env.pop("MASCOPE_INSTANCE", None)
        out = subprocess.run(
            ["uv", "run", "--no-sync", "mascope", *args],
            cwd=self.home.worktree,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        if check and out.returncode != 0:
            raise BenchError(
                f"mascope {' '.join(args)} failed: {(out.stdout + out.stderr)[-600:]}"
            )
        return out

    def _database_exists(self) -> bool:
        container, user, database = self._db()
        out = subprocess.run(
            [
                "docker",
                "exec",
                container,
                "psql",
                "-U",
                user,
                "-d",
                "postgres",
                "-Atc",
                f"select 1 from pg_database where datname = '{database}'",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if out.returncode != 0:
            raise BenchError(f"cannot list the databases: {out.stderr.strip()[-300:]}")
        return out.stdout.strip() == "1"

    # -- Instance

    def check_env(self) -> None:
        """Refuse an env that is not the bench's own.

        The env is the bench's when the registry binds it to the bench's
        worktree, or when nothing of it exists yet: no binding, no database,
        no env folder. Any other env is someone else's instance, which
        ``--rebuild`` would drop and ``bind`` would hand to the bench's
        backend to migrate.
        """
        from mascope_cli.instance import list_instances

        worktree = str(self.home.worktree.resolve())
        bound = [i for i in list_instances(self.runtime) if i.env == self.env]
        if bound:
            if bound[0].worktree != worktree:
                raise BenchError(
                    f"env {self.env!r} is bound to {bound[0].worktree}, not to the "
                    f"bench's worktree {worktree}: it is not the bench's"
                )
            return
        found = []
        if self.env_dir.exists():
            found.append(f"its folder {self.env_dir}")
        if self._database_exists():
            found.append(f"database {self._db()[2]}")
        if found:
            raise BenchError(
                f"env {self.env!r} is bound to no worktree but has {' and '.join(found)}: "
                f"another instance's, not the bench's; name another --env"
            )

    def bind(self) -> tuple[int, int]:
        """Bind the bench worktree to a slot under the bench's env name."""
        from mascope_cli.instance import InstanceError, provision, resolve_or_allocate

        try:
            inst = resolve_or_allocate(
                worktree=str(self.home.worktree.resolve()),
                home=self.runtime,
                env=self.env,
            )
        except InstanceError as error:
            raise BenchError(str(error)) from None
        provision(inst, home=self.runtime)
        self.api_port = inst.api_port
        return inst.slot, inst.api_port

    def checkout(self, ref: str) -> str:
        """Check ``ref`` out into the bench worktree and sync its environment."""
        subprocess.run(
            ["git", "-C", str(self.repo), "fetch", "-q", "origin"], check=False
        )
        sha = None
        for candidate in (f"origin/{ref}", ref):
            p = subprocess.run(
                [
                    "git",
                    "-C",
                    str(self.repo),
                    "rev-parse",
                    "--verify",
                    "-q",
                    f"{candidate}^{{commit}}",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if p.returncode == 0:
                sha = p.stdout.strip()
                break
        if sha is None:
            raise BenchError(f"no commit {ref!r} (tried origin/{ref} and {ref})")
        wt = self.home.worktree
        if (wt / ".git").exists():
            subprocess.run(
                ["git", "-C", str(wt), "checkout", "-q", "--detach", sha], check=True
            )
        else:
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(self.repo),
                    "worktree",
                    "add",
                    "-q",
                    "--detach",
                    str(wt),
                    sha,
                ],
                check=True,
            )
        log(f"uv sync in {wt}")
        subprocess.run(["uv", "sync", "--all-groups", "-q"], cwd=wt, check=True)
        return sha

    def start(self, peak_assignment: bool = False) -> str:
        """Start the worktree's backend and file converter on the bench slot.

        ``peak_assignment`` switches the peak-assignment feature on for this
        start (``MASCOPE_PEAK_ASSIGNMENT``): its write routes, the import of a
        reference run among them, answer 403 without it.
        """
        if self.api_port is None:
            raise BenchError("bind the instance before starting it")
        base = f"http://localhost:{self.api_port}"
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", self.api_port)) == 0:
                raise BenchError(
                    f"port {self.api_port} is already listened on: is the bench running?"
                )
        logfile = (
            self.home.logs / f"backend-{datetime.datetime.now():%Y%m%d-%H%M%S}.log"
        )
        env = {**os.environ, "PYTHONUTF8": "1"}
        env.pop("MASCOPE_ENV", None)
        if peak_assignment:
            env["MASCOPE_PEAK_ASSIGNMENT"] = "1"
        with open(logfile, "ab") as out:
            proc = subprocess.Popen(
                [
                    "uv",
                    "run",
                    "--no-sync",
                    "mascope",
                    "dev",
                    "run",
                    "backend",
                    "file-converter",
                    "--instance",
                ],
                cwd=self.home.worktree,
                env=env,
                stdout=out,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        self.home.pid.write_text(str(proc.pid))
        import requests

        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise BenchError(
                    f"the backend exited ({proc.returncode}); see {logfile}"
                )
            try:
                if (
                    requests.get(
                        f"{base}/api/users/first-owner/status", timeout=3
                    ).status_code
                    == 200
                ):
                    log(f"backend answers on {base} (log {logfile})")
                    return base
            except requests.RequestException:
                pass
            time.sleep(2)
        self.stop()
        raise BenchError(f"the backend did not answer within 10 min; see {logfile}")

    def stop(self) -> None:
        if not self.home.pid.exists():
            return
        pid = int(self.home.pid.read_text())
        if not self._is_backend(pid):
            # A pid file left by a crash or a reboot: the pid may be another
            # process's by now, and its group is not the bench's to signal.
            log(
                f"process {pid} is not the bench's backend; its stale pid file is dropped"
            )
            self.home.pid.unlink(missing_ok=True)
            return
        with contextlib.suppress(ProcessLookupError):
            pgid = os.getpgid(pid)
            os.killpg(pgid, signal.SIGTERM)
            for _ in range(30):
                time.sleep(1)
                try:
                    os.killpg(pgid, 0)
                except ProcessLookupError:
                    break
            else:
                os.killpg(pgid, signal.SIGKILL)
        self.home.pid.unlink(missing_ok=True)
        log("backend stopped; the database and the filestore stay")

    def _is_backend(self, pid: int) -> bool:
        """Whether ``pid`` is the backend ``start`` launched: a group leader
        running ``mascope dev run`` in the bench's worktree."""
        try:
            if os.getpgid(pid) != pid:
                return False
            argv = Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="replace")
            cwd = Path(os.readlink(f"/proc/{pid}/cwd"))
        except OSError:
            return False
        return runs_dev_run(argv.split("\0")) and cwd == self.home.worktree.resolve()

    def drop(self) -> None:
        """Drop the env's database and empty its filestore and streams."""
        container, user, database = self._db()
        subprocess.run(
            [
                "docker",
                "exec",
                container,
                "psql",
                "-U",
                user,
                "-d",
                "postgres",
                "-qc",
                f'DROP DATABASE IF EXISTS "{database}"',
            ],
            check=True,
            capture_output=True,
        )
        for sub in ("filestore", "filestreams"):
            shutil.rmtree(self.env_dir / sub, ignore_errors=True)
        log(f"dropped {database} and emptied {self.env_dir}")

    def reference_seed(self, lists: list[str]) -> None:
        """``mascope reference seed``: the default lists, then any named one."""
        names = [n for n in lists if n != "default"]
        calls = ([[]] if "default" in lists else []) + ([names] if names else [])
        for chosen in calls:
            self._cli("reference", "seed", "--yes", *chosen)

    def _query(self, sql: str) -> list[dict]:
        container, user, database = self._db()
        out = subprocess.run(
            [
                "docker",
                "exec",
                container,
                "psql",
                "-U",
                user,
                "-d",
                database,
                "-Atc",
                f"select coalesce(json_agg(t), '[]') from ({sql}) t",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if out.returncode != 0:
            raise BenchError(f"cannot read {database}: {out.stderr.strip()[-300:]}")
        return json.loads(out.stdout)

    def samples(self) -> dict[str, dict]:
        """The instance's sample files by hash, each with its samples and modes."""
        rows = self._query(
            "select f.sha256, f.sample_file_id, f.filename, f.processing_status, "
            "f.processing_detail, coalesce(json_agg(json_build_object("
            "'sample_item_id', i.sample_item_id, 'mode', m.ionization_mode_name)) "
            "filter (where i.sample_item_id is not null), '[]') as items "
            "from sample_file f left join sample_item i on i.sample_file_id = f.sample_file_id "
            "left join ionization_mode m on m.ionization_mode_id = i.ionization_mode_id "
            "where f.sha256 is not null group by f.sample_file_id"
        )
        return {r["sha256"]: r for r in rows}

    def sample_workspaces(self) -> dict[str, str]:
        rows = self._query(
            "select i.sample_item_id, d.workspace_id from sample_item i "
            "join sample_batch b on b.sample_batch_id = i.sample_batch_id "
            "join dataset d on d.dataset_id = b.dataset_id"
        )
        return {r["sample_item_id"]: r["workspace_id"] for r in rows}

    def streams(self) -> tuple[list[str], list[str]]:
        """The converter's queue: files waiting, and files it quarantined."""
        streams = self.env_dir / "filestreams"

        def names(folder: Path) -> list[str]:
            return (
                sorted(p.name for p in folder.iterdir() if p.is_file())
                if folder.is_dir()
                else []
            )

        return names(streams), names(streams / "failed_files")

    def bundle_seed(self, version: str) -> dict:
        from mascope_cli.cmd.demo import bundles

        listing = bundles.load_manifest(version)
        dump = bundles.bundle_dir(version) / listing["seed"]["dump"]
        return read_bundle_seed(dump, self._db()[0])

    def owner_secret(self) -> Path:
        return self.runtime / ".runtime" / "secrets" / "server_owner_secret_key.txt"


def runs_dev_run(argv: list[str]) -> bool:
    """Whether a command line runs ``mascope dev run``, through ``uv run`` or not."""
    return any(
        Path(arg).name == "mascope" and argv[i + 1 : i + 3] == ["dev", "run"]
        for i, arg in enumerate(argv)
    )


def bundle_dir(version: str) -> Path:
    """A demo bundle's cache directory, fetched and verified first if need be."""
    from mascope_cli.cmd.demo import _fetch, bundles

    if not bundles.is_cached(version):
        log(f"fetching demo bundle {version}")
        _fetch.fetch(version)
    return bundles.bundle_dir(version)


# --------------------------------------------------------------------- init


@dataclass
class Bench:
    """``init`` and ``status`` over one home, one instance and its API."""

    home: Home
    instance: Instance
    connect: Callable[[str], Api]
    bundle_dir: Callable[[str], Path] = bundle_dir
    wait_s: float = WAIT_S
    poll_s: float = POLL_S
    upload_pause_s: float = UPLOAD_PAUSE_S
    sleep: Callable[[float], None] = time.sleep

    def init(
        self,
        manifest_path: Path,
        ref: str = "develop",
        rebuild: bool = False,
        checkout: Path | None = None,
        keep_running: bool = False,
    ) -> int:
        m = bench_manifest.load(manifest_path)
        if checkout is None and m.corpus_checkout:
            checkout = Path(m.corpus_checkout).expanduser()
        files = bench_manifest.resolve(m, self.bundle_dir, checkout)
        self.home.make()
        state = load_state(self.home, self.instance.env)
        if rebuild:
            self.instance.check_env()
            log(f"--rebuild: dropping env {self.instance.env}'s database and filestore")
            self.instance.stop()
            self.instance.drop()
            for path in (self.home.creds, self.home.env_file):
                path.unlink(missing_ok=True)
            state = new_state(self.instance.env)

        todo = plan(m, state)
        if todo.refused:
            raise BenchError(
                "the instance holds what the manifest no longer says; --rebuild to start over:\n  "
                + "\n  ".join(todo.refused)
            )
        if todo.empty:
            if (state["manifest"] or {}).get("digest") == m.digest:
                built = state["build"] or {}
                log(
                    f"the bench already holds manifest {m.name!r} ({m.digest[:12]}): nothing to do "
                    f"(last started at {str(built.get('sha'))[:9]}; --rebuild builds it again)"
                )
                return 0
            # The same files and seeds, read another way (a profile, a
            # window, a label): recorded, with nothing to start.
            self._record_manifest(m, manifest_path, state)
            self._describe_sets(m, state)
            save_state(self.home, state)
            log(
                f"manifest {m.name!r} ({m.digest[:12]}) changed only how its sets are "
                f"read: recorded in {self.home.state}, nothing started"
            )
            return 0

        self.instance.check_env()
        slot, port = self.instance.bind()
        state["slot"], state["api_port"] = slot, port
        sha = self.instance.checkout(ref)
        # Samples recorded before builds were kept per file were all ingested
        # at the build the state named until now.
        earlier = (state["build"] or {}).get("sha")
        for held in state["sets"].values():
            for record in held["samples"].values():
                record.setdefault("build", earlier)
        state["build"] = {"ref": ref, "sha": sha, "at": now()}
        self._record_manifest(m, manifest_path, state)
        save_state(self.home, state)

        log(
            f"env {self.instance.env}, slot {slot}, api :{port}, ref {ref} at {sha[:9]}"
        )
        base = self.instance.start()
        try:
            api = self.connect(base)
            problems = self._seed(api, m, todo, state, checkout)
            problems += self._ingest(api, m, files, todo, state, sha)
        finally:
            if not keep_running:
                self.instance.stop()
        save_state(self.home, state)
        self._summary(m, state)
        if problems:
            log(f"{len(problems)} problem(s):")
            for problem in problems:
                print(f"  {problem}", flush=True)
            return 1
        return 0

    def _record_manifest(self, m: Manifest, manifest_path: Path, state: dict) -> None:
        """The manifest's copy beside the state, and its name and digest in it."""
        self.home.write_private(
            self.home.manifest, json.dumps(m.source, indent=2) + "\n"
        )
        state["manifest"] = {
            "name": m.name,
            "digest": m.digest,
            "from": str(Path(manifest_path).resolve()),
        }

    def _describe_sets(self, m: Manifest, state: dict) -> None:
        """Each set's label, profile, class and window, as the manifest says."""
        for s in m.sets.values():
            entry = state["sets"].setdefault(
                s.name,
                {"mode": s.mode, "instrument_class": s.instrument_class, "samples": {}},
            )
            entry.update(
                {
                    "label": s.label,
                    "profile": s.profile,
                    "instrument_class": s.instrument_class,
                    "reference_window_ppm": (
                        vars(s.reference_window_ppm) if s.reference_window_ppm else None
                    ),
                }
            )

    def _seed(
        self, api: Api, m: Manifest, todo: Plan, state: dict, checkout: Path | None
    ) -> list[str]:
        problems: list[str] = []
        by_source: dict[str, tuple[bench_manifest.Seed, list[str], list[str]]] = {}
        for s in m.sets.values():
            entry = by_source.setdefault(s.seed.key, (s.seed, [], []))
            if s.mode in todo.modes and s.mode not in entry[1]:
                entry[1].append(s.mode)
            for name in s.collections:
                if name in todo.collections and name not in entry[2]:
                    entry[2].append(name)
        for key, (seed_ref, modes, colls) in by_source.items():
            if not (modes or colls):
                continue
            if seed_ref.bundle:
                full = self.instance.bundle_seed(seed_ref.bundle)
            else:
                if checkout is None:
                    raise BenchError(f"seed {key} needs a corpus checkout")
                full = json.loads(
                    (checkout / seed_ref.corpus).read_text(encoding="utf-8")
                )
            chosen = select_seed(full, modes, colls)
            log(
                f"seeding {len(chosen['ionization_modes'])} mode(s) and "
                f"{len(chosen['target_collections'])} collection(s) from {key}"
            )
            problems += load_seed(api, chosen, key, state["seeded"])
            save_state(self.home, state)
        if todo.reference_lists:
            log(f"seeding reference lists: {', '.join(todo.reference_lists)}")
            self.instance.reference_seed(todo.reference_lists)
            state["seeded"]["reference_lists"] += todo.reference_lists
            save_state(self.home, state)
        return problems

    def _ingest(
        self,
        api: Api,
        m: Manifest,
        files: dict[str, ResolvedFile],
        todo: Plan,
        state: dict,
        sha: str,
    ) -> list[str]:
        """Upload, wait, check; each record names the build that ingested it."""
        held = self.instance.samples()
        uploads = [files[k] for k in todo.uploads if files[k].sha256 not in held]
        if len(uploads) < len(todo.uploads):
            log(
                f"{len(todo.uploads) - len(uploads)} file(s) are in the instance already"
            )
        unsent = self._upload(api, uploads)
        held = self._wait(files, todo.uploads + todo.recheck)
        # One more pass for what the converter quarantined or never got: a
        # file whose context arrived before a converter was connected.
        lost = [f for f in uploads if f.sha256 not in held and f.name not in unsent]
        if lost:
            log(f"{len(lost)} file(s) did not come through; uploading them once more")
            unsent += self._upload(api, lost)
            held = self._wait(files, todo.uploads + todo.recheck)

        problems: list[str] = []
        for key in todo.uploads + todo.recheck:
            set_name = next(n for n, ref in m.files() if ref.key == key)
            s = m.sets[set_name]
            record = self._check(api, s, files[key], held.get(files[key].sha256))
            entry = state["sets"].setdefault(
                set_name,
                {"mode": s.mode, "instrument_class": s.instrument_class, "samples": {}},
            )
            entry["mode"] = s.mode
            # A recheck looks again at what an earlier build ingested.
            earlier = entry["samples"].get(key) or {}
            record["build"] = (
                earlier.get("build") if key in todo.recheck else None
            ) or sha
            entry["samples"][key] = record
            problems += [
                f"set {set_name}: {files[key].name}: {p}" for p in record["problems"]
            ]
        self._describe_sets(m, state)
        return problems

    def _upload(self, api: Api, uploads: list[ResolvedFile]) -> list[str]:
        """Upload each file, holding on while the server refuses; the names never sent."""
        unsent = []
        for i, file in enumerate(uploads, 1):
            error = None
            for _ in range(UPLOAD_ATTEMPTS):
                try:
                    api.upload(file)
                except UploadError as refused:
                    error = refused
                    if not refused.retry:
                        break
                    self.sleep(self.upload_pause_s)
                else:
                    error = None
                    break
            if error is not None:
                unsent.append(file.name)
                log(f"upload of {file.name} failed: {error}")
            if i % 25 == 0 or i == len(uploads):
                log(f"uploaded {i - len(unsent)}/{len(uploads)}")
        return unsent

    def _wait(self, files: dict[str, ResolvedFile], keys: list[str]) -> dict[str, dict]:
        """Wait until the pipeline has settled over ``keys``; the samples by hash."""
        wanted = {files[k].sha256 for k in keys}
        deadline = time.monotonic() + self.wait_s
        last, stable = None, 0
        while True:
            held = self.instance.samples()
            pending, _ = self.instance.streams()
            busy = sorted(
                held[sha]["sample_file_id"]
                for sha in wanted & set(held)
                if held[sha]["processing_status"] in RUNNING
            )
            signature = (len(wanted & set(held)), tuple(busy), len(pending))
            settled = not pending and not busy
            stable = stable + 1 if settled and signature == last else 0
            last = signature
            log(
                f"{len(wanted & set(held))}/{len(wanted)} in, {len(busy)} processing, "
                f"{len(pending)} queued"
            )
            if wanted <= set(held) and settled or stable >= SETTLED_POLLS:
                return held
            if time.monotonic() > deadline:
                log(f"the pipeline did not settle within {self.wait_s / 60:.0f} min")
                return held
            self.sleep(self.poll_s)

    def _check(
        self, api: Api, s: bench_manifest.BenchSet, file: ResolvedFile, row: dict | None
    ) -> dict:
        """A sample file's record for the state, and what is wrong with it."""
        if row is None:
            return {
                "name": file.name,
                "sample_file_id": None,
                "items": [],
                "problems": ["never ingested"],
            }
        problems = []
        if row["processing_status"] in RUNNING:
            problems.append(f"still processing ({row['processing_status']})")
        elif row["processing_status"] != "done":
            problems.append(
                f"{row['processing_status']}: {row.get('processing_detail') or ''}".strip()
            )
        items = []
        for item in row["items"]:
            try:
                peaks = api.peak_count(item["sample_item_id"])
            except ApiError as error:
                # Not a verdict on the data: the count could not be read.
                peaks = None
                problems.append(f"sample {item['sample_item_id']}: {error}")
            items.append(
                {
                    "sample_item_id": item["sample_item_id"],
                    "mode": item["mode"],
                    "peaks": peaks,
                }
            )
            if item["mode"] is None:
                problems.append(f"sample {item['sample_item_id']} resolved no mode")
            elif item["mode"] != s.mode:
                problems.append(
                    f"sample {item['sample_item_id']} resolved {item['mode']!r}, not {s.mode!r}"
                )
            if peaks == 0:
                problems.append(f"sample {item['sample_item_id']} has no peaks")
        if not items:
            problems.append("no samples")
        return {
            "name": file.name,
            "sample_file_id": row["sample_file_id"],
            "status": row["processing_status"],
            "items": items,
            "problems": problems,
        }

    def _summary(self, m: Manifest, state: dict) -> None:
        for name, s in state["sets"].items():
            if name not in m.sets:
                continue
            ok = sum(1 for r in s["samples"].values() if not r["problems"])
            items = sum(len(r["items"]) for r in s["samples"].values())
            log(
                f"set {name}: {ok}/{len(s['samples'])} files ready, {items} samples, mode {s['mode']!r}"
            )

    # ---------------------------------------------------------------- status

    def status(self, out=None) -> int:
        out = out or sys.stdout
        if not self.home.state.exists():
            print(
                f"no bench in {self.home.root}: run `bench.py init <manifest>`",
                file=out,
            )
            return 1
        state = load_state(self.home, self.instance.env)
        build = state.get("build") or {}
        manifest = state.get("manifest") or {}
        print(
            f"env       {state['env']}  (slot {state['slot']}, api :{state['api_port']})",
            file=out,
        )
        print(f"home      {self.home.root}", file=out)
        print(
            f"manifest  {manifest.get('name')} ({str(manifest.get('digest'))[:12]})",
            file=out,
        )
        print(
            f"started   {build.get('ref')} at {str(build.get('sha'))[:9]}, {build.get('at')}",
            file=out,
        )
        print("sets", file=out)
        for name, s in state["sets"].items():
            records = s["samples"].values()
            ok = sum(1 for r in records if not r["problems"])
            items = sum(len(r["items"]) for r in records)
            window = s.get("reference_window_ppm")
            window_text = (
                f"{window['trust']:g}/{window['search']:g} ppm"
                if window
                else "reference default"
            )
            # A record from before builds were kept per file was ingested at
            # the build the state names, as ``init`` fills it in.
            builds = sorted(
                {str(r.get("build") or build.get("sha"))[:9] for r in records}
            )
            print(
                f"  {name:<4} {s.get('instrument_class', '?'):<8} {s.get('profile', '?'):<6} "
                f"{ok}/{len(records)} files ready, {items} samples, mode {s['mode']!r}, "
                f"window {window_text}, built at {', '.join(builds) or '-'}",
                file=out,
            )
        for line in bench_reference.status_lines(state.get("reference")):
            print(line, file=out)
        cards = state.get("cards") or []
        if cards:
            print("cards", file=out)
            for card in cards[-5:]:
                print(
                    f"  {card.get('at')}  {card.get('ref')}  {card.get('path')}",
                    file=out,
                )
        else:
            print("cards     none yet", file=out)
        return 0


# ---------------------------------------------------------------------- cli


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bench.py", description=__doc__.split("\n\n")[0]
    )
    parser.add_argument(
        "--home",
        type=Path,
        help="the bench's folder (default $MASCOPE_PATH/.runtime/bench)",
    )
    parser.add_argument(
        "--env", default=DEFAULT_ENV, help="the instance's env (default bench)"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p_init = sub.add_parser(
        "init", help="build or update the bench instance from a manifest"
    )
    p_init.add_argument("manifest", type=Path)
    p_init.add_argument(
        "--ref",
        default="develop",
        help="the build whose backend ingests (default develop)",
    )
    p_init.add_argument(
        "--rebuild", action="store_true", help="drop the database and filestore first"
    )
    p_init.add_argument(
        "--checkout", type=Path, help="a corpus checkout, for corpus objects"
    )
    p_init.add_argument(
        "--keep-running", action="store_true", help="leave the backend running"
    )
    p_init.add_argument(
        "--wait", action="store_true", help="queue behind another bench command"
    )
    p_ref = sub.add_parser(
        "reference",
        help="install peaky at a release tag and publish its run of every sample",
    )
    p_ref.add_argument("--pin", required=True, help="peaky's release tag, vX.Y.Z")
    p_ref.add_argument(
        "--reinstall",
        action="store_true",
        help="install the pin again, resolving its dependencies afresh",
    )
    p_ref.add_argument(
        "--jobs", type=int, default=2, help="samples run at once (default 2)"
    )
    p_ref.add_argument(
        "--set",
        action="append",
        dest="sets",
        metavar="NAME",
        help="only this set (repeatable; default every set)",
    )
    p_ref.add_argument(
        "--peaky-url",
        default=bench_reference.PEAKY_URL,
        help="where peaky is cloned from, the first time",
    )
    p_ref.add_argument(
        "--wait", action="store_true", help="queue behind another bench command"
    )
    sub.add_parser("status", help="the env, slot, sets, reference pin and last cards")
    args = parser.parse_args(argv)

    try:
        home = Home(args.home or default_home())
        instance = DevInstance(home, env=args.env)

        def connect(base: str) -> Api:
            api = HttpApi(base, home)
            api.sign_in(instance.owner_secret())
            api.write_env_file()
            return api

        bench = Bench(home=home, instance=instance, connect=connect)
        if args.command == "status":
            return bench.status()
        if args.command == "reference":
            runner = bench_reference.Reference(
                peaky=bench_reference.Peaky(
                    home.root / "reference", url=args.peaky_url
                ),
                instance=instance,
                connect=connect,
                env_file=home.env_file,
                load_state=lambda: load_state(home, instance.env),
                save_state=lambda state: save_state(home, state),
                jobs=args.jobs,
            )
            with home.lock(wait=args.wait):
                return runner.run(args.pin, reinstall=args.reinstall, only=args.sets)
        with home.lock(wait=args.wait):
            return bench.init(
                args.manifest,
                ref=args.ref,
                rebuild=args.rebuild,
                checkout=args.checkout,
                keep_running=args.keep_running,
            )
    except (BenchError, ManifestError, bench_reference.ReferenceError) as error:
        print(f"bench: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
