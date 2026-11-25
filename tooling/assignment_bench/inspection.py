"""``bench inspect``: a read-only look inside the bench, its names scrubbed.

    bench.py inspect

What a worker debugging a run or checking a card needs to see of the bench's
database and env folder, through one fixed command: no SQL, no table name and
no path is taken from the caller. It prints

- the bench: its env, slot and port, each set by letter with its counts, the
  reference pin, the env's config layer (the backend's workers) and the
  filestore's size, with the converter's queue;
- the lock: free, or which command holds it and since when, and how many wait;
- the schema: the database's alembic revisions, and whether this checkout
  knows them;
- the runs: the assignment runs by engine, arm and status, the batch runs by
  status; the newest card that ran, its runs per set by arm and status and
  its failed runs by the shape of their error; and the runs of the run that
  holds the lock, while one does;
- the storage: per assignment table its rows and each JSON column's stored
  bytes as the planner's statistics hold them, its stored size and its
  indexes'; and, counted exactly, the ledger rows of the newest card's runs
  and their provenance and alternatives, per arm.

It names sets by letter, as the card does, and nothing else of what the
instance holds: no sample, file, batch, workspace or instrument name, no
path, no id. Every line goes through the card's own scrub
(``compare_runs.scrub``) with the deny list a card is scrubbed with
(``BenchRun._deny``), the in-app runs' ids added; a line the scrub refuses is
not printed, and a line says which kind of name it held, never the name.

It opens every database session read-only (``default_transaction_read_only``,
``DevInstance(read_only=True)``), writes nothing in the bench's home, signs in
nowhere and takes neither the bench's lock nor the state's, so it runs while
a run holds the bench.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import run as bench_run  # noqa: E402


#: The tables of the assignment whose rows and sizes are shown, in this
#: order. A table the database does not hold yet (an older revision) is left
#: out; its JSON columns are read off the schema.
TABLES = (
    "peak_assignment_run",
    "peak_assignment",
    "sample_measurement_model",
    "assignment_calibration",
    "assignment_verification",
    "batch_peak_run",
    "batch_peak_stage",
    "batch_peak",
    "batch_peak_run_anchor",
)

#: The ledger's JSON columns whose stored size is shown for a card's runs.
LEDGER_COLUMNS = ("provenance", "alternatives")

#: The first line of the env's own config layer when the bench wrote it
#: (``bench.CONFIG_MARK``).
CONFIG_MARK = (
    "# Written by tooling/assignment_bench/bench.py: the bench backend's workers."
)

#: A table or column name as the schema gives it, and nothing else.
_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")

#: How long a failed run's error shape may be.
SHAPE_LIMIT = 100


class InspectError(RuntimeError):
    """The bench cannot be inspected: nothing is printed but why."""


# ----------------------------------------------------------- the queries


class Reader:
    """The fixed queries ``inspect`` makes, over a read-only ``query``.

    Each query is a ``select``; none takes a name from its caller. The two
    that read a card's runs take a time window, which :func:`stamp` has
    already parsed and written out again.
    """

    def __init__(self, query: Callable[[str], list[dict]]):
        self.query = query

    def tables(self) -> dict[str, list[str]]:
        """Of :data:`TABLES`, those the database holds, each with its JSON
        columns, in the schema's order."""
        listed = ", ".join(f"'{t}'" for t in TABLES)
        rows = self.query(
            "select c.relname as table_name, a.attname as column_name, "
            "format_type(a.atttypid, a.atttypmod) as type "
            "from pg_class c join pg_namespace n on n.oid = c.relnamespace "
            "join pg_attribute a on a.attrelid = c.oid "
            "where n.nspname = current_schema() and c.relkind = 'r' "
            f"and c.relname in ({listed}) and a.attnum > 0 and not a.attisdropped "
            "order by c.relname, a.attnum"
        )
        found: dict[str, list[str]] = {}
        for row in rows:
            table, column = row["table_name"], row["column_name"]
            if not (_IDENTIFIER.match(table) and _IDENTIFIER.match(column)):
                continue
            columns = found.setdefault(table, [])
            if row["type"] in ("json", "jsonb"):
                columns.append(column)
        return {t: found[t] for t in TABLES if t in found}

    def run_statuses(self) -> list[dict]:
        """Every assignment run, counted by engine, arm and status."""
        return self.query(
            "select engine, decoy, status, count(*) as n from peak_assignment_run "
            "group by engine, decoy, status order by engine, decoy nulls first, status"
        )

    def batch_statuses(self) -> list[dict]:
        """Every batch run, counted by status."""
        return self.query(
            "select status, count(*) as n from batch_peak_run "
            "group by status order by status"
        )

    def run_ids(self) -> list[str]:
        """Every assignment run's id, for the deny list."""
        return [
            r["id"]
            for r in self.query(
                "select peak_assignment_run_id as id from peak_assignment_run"
            )
        ]

    def batch_run_ids(self) -> list[str]:
        """Every batch run's id, for the deny list."""
        return [
            r["id"]
            for r in self.query("select batch_peak_run_id as id from batch_peak_run")
        ]

    def runs_between(self, start: str, end: str | None) -> list[dict]:
        """The in-app engine's runs made from ``start`` to ``end`` (or now)."""
        return self.query(
            "select sample_item_id, decoy, status, error from peak_assignment_run "
            f"where engine = '{bench_run.ENGINE}' "
            + _window("peak_assignment_run_utc_created", start, end)
        )

    def storage(self) -> dict[str, dict]:
        """Of :data:`TABLES`, each table's rows, stored bytes (TOAST
        included) and indexes' bytes, and each column's average stored width
        and share of nulls.

        The rows and the widths are the planner's statistics (``reltuples``,
        ``pg_stats``), as the last ``ANALYZE`` left them, not a count: a count
        and a sum over every row of the ledger reads the whole table, which
        on the bench takes minutes. The sizes are exact.
        """
        listed = ", ".join(f"'{t}'" for t in TABLES)
        sizes = self.query(
            "select c.relname as table_name, c.reltuples::bigint as rows, "
            "pg_table_size(c.oid) as table_bytes, "
            "pg_indexes_size(c.oid) as index_bytes "
            "from pg_class c join pg_namespace n on n.oid = c.relnamespace "
            "where n.nspname = current_schema() and c.relkind = 'r' "
            f"and c.relname in ({listed})"
        )
        widths = self.query(
            "select tablename as table_name, attname as column_name, avg_width, "
            "null_frac from pg_stats where schemaname = current_schema() "
            f"and tablename in ({listed})"
        )
        out = {
            r["table_name"]: {**r, "columns": {}}
            for r in sizes
            if r.get("table_name") in TABLES
        }
        for r in widths:
            if r.get("table_name") in out:
                out[r["table_name"]]["columns"][r.get("column_name")] = r
        return out

    def ledger(self, start: str, end: str | None) -> list[dict]:
        """The ledger rows of the in-app runs made from ``start`` to ``end``,
        and their provenance and alternatives' stored bytes, by arm: exact,
        and read through the ledger's index on its run."""
        parts = ["r.engine", "r.decoy", "count(*) as rows"]
        for c in LEDGER_COLUMNS:
            parts.append(f'coalesce(sum(pg_column_size(a."{c}")), 0) as "{c}:bytes"')
        return self.query(
            f"select {', '.join(parts)} from peak_assignment_run r "
            "join peak_assignment a on a.peak_assignment_run_id = r.peak_assignment_run_id "
            f"where r.engine = '{bench_run.ENGINE}' "
            + _window("r.peak_assignment_run_utc_created", start, end)
            + "group by r.engine, r.decoy order by r.engine, r.decoy nulls first"
        )


def stamp(text) -> str | None:
    """A time a card or the lock recorded, parsed and written out again, so
    that only a time reaches a query; None for anything that is not one."""
    if not isinstance(text, str):
        return None
    try:
        when = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.astimezone()
    return when.isoformat()


def _window(column: str, start: str, end: str | None) -> str:
    start = stamp(start)
    if start is None:
        raise InspectError("a card's window has no start")
    clause = f"and {column} >= '{start}' "
    end = stamp(end) if end is not None else None
    if end is not None:
        clause += f"and {column} <= '{end}' "
    return clause


# ------------------------------------------------------------- the shapes


_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")
_PATH_LIKE = re.compile(r"(?:[A-Za-z]:)?[\\/][^\s,;:]*")
#: A word with a digit in it, eight characters or more: an id or a hash.
_ID_LIKE = re.compile(r"\b(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9_-]{8,}\b")
_NUMBER = re.compile(r"(?<![A-Za-z])[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")


def error_shape(error) -> str:
    """A failed run's error, as its shape: the first line, with what is
    quoted, a path, an id and a number each replaced by a mark, cut short.

    The server keeps a run's error as its message alone (``str(exc)``), with
    no class, so runs that failed the same way share a shape where their
    messages differ only in the sample, the peak or the number they name.
    """
    text = str(error or "").strip().splitlines()
    if not text:
        return "(no error recorded)"
    line = _QUOTED.sub("'<q>'", text[0])
    line = _PATH_LIKE.sub("<path>", line)
    line = _ID_LIKE.sub("<id>", line)
    line = _NUMBER.sub("<n>", line)
    line = " ".join(line.split())
    return line if len(line) <= SHAPE_LIMIT else line[: SHAPE_LIMIT - 3] + "..."


def size(n) -> str:
    """Bytes in the unit a person reads them in."""
    n = float(n or 0)
    for unit in ("B", "kB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"  # pragma: no cover


def _count(n) -> str:
    return f"{int(n or 0):,}"


def _tally(counts: dict[str, int]) -> str:
    return ", ".join(f"{k} {_count(v)}" for k, v in counts.items()) or "none"


def _arm(decoy) -> str:
    return "control" if decoy is None else f"decoy {decoy}"


# --------------------------------------------------------------- the look


@dataclass
class Line:
    """One line of the output: its own words, and the values it shows.

    The words of ``text`` are the command's own and go to the scrub as its
    vocabulary, as a card's labels do; the values are what the database and
    the home hold, scrubbed as they are.
    """

    text: str
    values: dict = field(default_factory=dict)

    def render(self) -> str:
        return self.text.format(**self.values)


@dataclass
class Inspect:
    """``bench inspect`` over one bench home and its read-only instance.

    :ivar home: The bench's home (``bench.Home``): read, never written.
    :ivar instance: The instance (``bench.DevInstance(read_only=True)``, a
        fake in the tests): its database revisions, its converter queue and
        the names the deny list holds.
    :ivar reader: The fixed queries (:class:`Reader`).
    :ivar runner: A ``BenchRun`` over the same home and instance, for the
        card's deny list and scrub; it is given nothing that writes.
    :ivar state: The bench's state, as ``load_state`` reads it.
    :ivar env_dir: The env's folder in the runtime home, or None.
    :ivar checkout: This checkout's commit and alembic revisions
        (``DevInstance.checkout_revisions``).
    """

    home: object
    instance: object
    reader: Reader
    runner: object
    state: dict
    env_dir: Path | None
    checkout: Callable[[], dict]
    lines: list[Line] = field(default_factory=list)
    _tables: dict[str, list[str]] = field(default_factory=dict)

    def say(self, text: str, **values) -> None:
        self.lines.append(Line(text, values))

    # ------------------------------------------------------------ sections

    def bench(self) -> None:
        state = self.state
        self.say(
            "env       {env}, slot {slot}, api port {port}",
            env=state.get("env"),
            slot=state.get("slot"),
            port=state.get("api_port"),
        )
        for name, held in (state.get("sets") or {}).items():
            records = list((held.get("samples") or {}).values())
            ready = sum(1 for r in records if not r.get("problems"))
            items = sum(len(r.get("items") or []) for r in records)
            self.say(
                "set {name}  {klass}, {ready} of {files} files ready, {items} samples",
                name=name,
                klass=held.get("instrument_class", "?"),
                ready=ready,
                files=len(records),
                items=items,
            )
        reference = state.get("reference") or {}
        self.say(
            "reference pin {pin}, version {version}",
            pin=reference.get("pin") or "none",
            version=reference.get("version") or "-",
        )
        self.settings()
        self.filestore()

    def settings(self) -> None:
        if self.env_dir is None:
            self.say("settings  the env's folder is not known here")
            return
        path = self.env_dir / "dev.mascope.toml"
        if not path.is_file():
            self.say("settings  no config layer of the env's own: the dev defaults")
            return
        text = path.read_text(encoding="utf-8")
        try:
            config = tomllib.loads(text)
        except tomllib.TOMLDecodeError:
            self.say("settings  the env's config layer does not parse")
            return
        workers = (config.get("backend") or {}).get("workers")
        if text.startswith(CONFIG_MARK):
            self.say(
                "settings  the bench's config layer: backend workers {workers}",
                workers=workers,
            )
            return
        keys = sorted(_keys(config))
        self.say(
            "settings  a config layer the bench did not write: backend workers "
            "{workers}; it sets {n} keys",
            workers=workers if isinstance(workers, int) else "unset",
            n=len(keys),
        )

    def filestore(self) -> None:
        if self.env_dir is None:
            return
        files, total = _tree_size(self.env_dir / "filestore")
        self.say(
            "filestore {size} in {files} files", size=size(total), files=_count(files)
        )
        waiting, failed = self.instance.streams()
        self.say(
            "converter {waiting} files waiting, {failed} quarantined",
            waiting=len(waiting),
            failed=len(failed),
        )

    def lock(self) -> dict | None:
        """The lock's holder as its file says, while one holds it."""
        held = _holder(self.home.root / "bench.lock")
        waiting = _waiting(self.home.root / "queue")
        if held is None:
            self.say("lock      free; {n} waiting", n=waiting)
            return None
        self.say(
            "lock      held by {what} since {since}, {alive}; {n} waiting",
            what=held.get("what") or "a bench command",
            since=held.get("since") or "?",
            alive="its process runs" if held.get("alive") else "its process is gone",
            n=waiting,
        )
        return held

    def schema(self) -> None:
        current = list(self.instance.db_revisions())
        self.say(
            "schema    the database is at {revisions}",
            revisions=", ".join(current) or "no revision",
        )
        try:
            mine = self.checkout()
        except Exception:  # noqa: BLE001 - said, the rest still prints
            # Not its text: the probe's error names this checkout's paths.
            self.say("schema    this checkout's migrations could not be read")
            return
        known = set(mine["revisions"])
        unknown = [r for r in current if r not in known]
        heads = sorted(mine["heads"])
        if unknown:
            verdict = "does not know {unknown}: a later build migrated the database"
        elif sorted(current) == heads:
            verdict = "knows it: the database is at its head"
        else:
            verdict = "knows it: its head {heads} is newer, a run migrates forward"
        self.say(
            "schema    this checkout at {sha} " + verdict,
            sha=str(mine.get("sha") or "?")[:9],
            unknown=", ".join(unknown),
            heads=", ".join(heads),
        )

    def runs(self) -> None:
        by: dict[tuple, dict[str, int]] = {}
        for row in self.reader.run_statuses():
            key = (row.get("engine"), _arm(row.get("decoy")))
            by.setdefault(key, {})[row.get("status")] = int(row.get("n") or 0)
        if not by:
            self.say("runs      none")
        for (engine, arm), counts in by.items():
            self.say(
                "runs      {engine} {arm}: {tally}",
                engine=engine,
                arm=arm,
                tally=_tally(counts),
            )
        if "batch_peak_run" in self._tables:
            counts = {
                r.get("status"): int(r.get("n") or 0)
                for r in self.reader.batch_statuses()
            }
            self.say("batch     batch runs: {tally}", tally=_tally(counts))

    def card(self) -> dict | None:
        """The newest card that ran, and its runs; None when no card ran."""
        cards = self.state.get("cards") or []
        if not cards:
            self.say("card      none yet")
            return None
        newest = cards[-1]
        ran = next((c for c in reversed(cards) if not c.get("reused")), None)
        if newest is not ran:
            self.say(
                "card      the newest, {at}, {ref} at {sha}, reused an earlier "
                "card: it ran nothing",
                at=newest.get("at"),
                ref=newest.get("ref"),
                sha=str(newest.get("sha"))[:9],
            )
        if ran is None:
            return None
        summary = _summary(self.home.cards / str(ran.get("path") or ""))
        start, end = summary.get("started"), summary.get("finished")
        self.say(
            "card      {at}, {ref} at {sha}, {verdict}; ran from {start} to {end}",
            at=ran.get("at"),
            ref=ran.get("ref"),
            sha=str(ran.get("sha"))[:9],
            verdict=ran.get("verdict") or "-",
            start=start or "?",
            end=end or "?",
        )
        if stamp(start) is None:
            self.say("card      its run.json names no start: its runs are not read")
            return None
        self.window_runs("card", start, end)
        return {"start": start, "end": end}

    def going(self, held: dict | None) -> None:
        """The runs of the run that holds the lock, while it does."""
        if not held or not str(held.get("what") or "").startswith("run "):
            return
        since = held.get("since")
        if stamp(since) is None:
            return
        self.window_runs("going", since, None)

    def window_runs(self, label: str, start: str, end: str | None) -> None:
        sets = self._sets_of_items()
        tally: dict[tuple, dict[str, int]] = {}
        shapes: dict[str, int] = {}
        for row in self.reader.runs_between(start, end):
            where = sets.get(row.get("sample_item_id"), "another")
            key = (where, _arm(row.get("decoy")))
            counts = tally.setdefault(key, {})
            counts[row.get("status")] = counts.get(row.get("status"), 0) + 1
            if row.get("status") == "failed":
                shape = error_shape(row.get("error"))
                shapes[shape] = shapes.get(shape, 0) + 1
        if not tally:
            self.say(label.ljust(9) + " no in-app runs in its window")
        order = {name: i for i, name in enumerate(self.state.get("sets") or {})}
        for (where, arm), counts in sorted(
            tally.items(), key=lambda kv: (order.get(kv[0][0], 999), kv[0][1])
        ):
            self.say(
                label.ljust(9) + " set {where} {arm}: {tally}",
                where=where,
                arm=arm,
                tally=_tally(counts),
            )
        for shape, n in sorted(shapes.items(), key=lambda kv: (-kv[1], kv[0])):
            self.say(
                label.ljust(9) + " failed {n}: {shape}",
                n=n,
                shape=shape,
            )

    def storage(self, window: dict | None) -> None:
        sizes = self.reader.storage()
        for table, columns in self._tables.items():
            row = sizes.get(table) or {}
            rows = int(row.get("rows") if row.get("rows") is not None else -1)
            json_sizes = []
            for c in columns:
                width = (row.get("columns") or {}).get(c)
                if rows <= 0 or not width:
                    continue
                share = 1 - float(width.get("null_frac") or 0)
                bytes_ = rows * share * float(width.get("avg_width") or 0)
                if bytes_:
                    json_sizes.append(
                        f"{c} {size(bytes_)} ({size(width.get('avg_width'))} a row)"
                    )
            self.say(
                "storage   {table}: {rows}, {table_size} stored, {index_size} of "
                "indexes" + ("; json about {json}" if json_sizes else ""),
                table=table,
                rows="no planner statistics yet"
                if rows < 0
                else f"about {_count(rows)} rows",
                table_size=size(row.get("table_bytes")),
                index_size=size(row.get("index_bytes")),
                json=", ".join(json_sizes),
            )
        if window is not None and "peak_assignment" in self._tables:
            self._ledger(
                "ledger    the card's {engine} {arm}",
                self.reader.ledger(window["start"], window["end"]),
            )

    def _ledger(self, head: str, rows: list[dict]) -> None:
        for row in rows:
            n = int(row.get("rows") or 0)
            parts = ", ".join(
                f"{c} {size(row.get(f'{c}:bytes'))} ({size((row.get(f'{c}:bytes') or 0) / n)} a row)"
                for c in LEDGER_COLUMNS
                if n
            )
            self.say(
                head + ": {rows} rows; {parts}",
                engine=row.get("engine"),
                arm=_arm(row.get("decoy")),
                rows=_count(n),
                parts=parts or "-",
            )

    # -------------------------------------------------------------- output

    def gather(self) -> None:
        self._tables = self.reader.tables()
        self.bench()
        held = self.lock()
        self.schema()
        self.runs()
        window = self.card()
        self.going(held)
        self.storage(window)

    def deny(self) -> tuple[list, list, list]:
        names, ids, strict = self.runner._deny(self.state, {}, {})
        more = list(self.reader.run_ids())
        if "batch_peak_run" in self._tables:
            more += self.reader.batch_run_ids()
        ids = sorted(set(ids) | {i for i in more if i and str(i).strip()})
        return names, ids, strict

    def print(self, out=None) -> int:
        """Print every line the scrub lets through; 0 when that is all of
        them, 1 when a line was held back, 2 when nothing could be read."""
        out = out or sys.stdout
        try:
            self.gather()
            deny = self.deny()
        except InspectError as error:
            print(f"bench inspect: {error}", file=out)
            return 2
        own = set()
        for line in self.lines:
            own |= set(bench_run._words(line.text))
        own |= set(TABLES) | {c for cs in self._tables.values() for c in cs}
        own |= set(LEDGER_COLUMNS)
        # The refs the cards were run for, as ``run.json``'s scrub lets a
        # card name its own ref (``BenchRun._outputs``).
        for card in self.state.get("cards") or []:
            own |= set(bench_run._words(str(card.get("ref") or "")))
        tokens = self.runner._tokens(
            deny, list(self.state.get("sets") or {}), sorted(own)
        )
        cr = bench_run._compare_module()
        denied = matcher(tokens)
        held_back = 0
        print("bench inspect: read-only; names scrubbed", file=out)
        for line in self.lines:
            text = line.render()
            try:
                found = denied(text)
                if found is not None:
                    # The card's own scrub refuses it, and names the token.
                    cr.scrub({"line": text}, found)
            except cr.CardRefused as refused:
                held_back += 1
                print(
                    "(a line held back: it holds "
                    f"{self.runner._kind(refused.token, deny)})",
                    file=out,
                )
                continue
            print(text, file=out)
        return 1 if held_back else 0

    # ------------------------------------------------------------- helpers

    def _sets_of_items(self) -> dict[str, str]:
        out = {}
        for name, held in (self.state.get("sets") or {}).items():
            for record in (held.get("samples") or {}).values():
                for item in record.get("items") or []:
                    if item.get("sample_item_id"):
                        out[item["sample_item_id"]] = name
        return out


def matcher(tokens: list[str]) -> Callable[[str], list[str] | None]:
    """The card's scrub (``compare_runs.scrub``) as one pattern over every
    token: a token matches whole, in any case, where no letter or digit
    adjoins it. ``scrub`` compiles a pattern per token per text, which over
    the bench's tens of thousands of run ids takes minutes for a few dozen
    lines; this compiles once.

    :return: For a text, None when no token matches, else the tokens that
        matched there, for ``scrub`` itself to refuse it with.
    """
    if not tokens:
        return lambda text: None
    pattern = re.compile(
        r"(?<![A-Za-z0-9])(?:"
        + "|".join(re.escape(t) for t in tokens)
        + r")(?![A-Za-z0-9])",
        flags=re.IGNORECASE,
    )

    def found(text: str) -> list[str] | None:
        m = pattern.search(text)
        if m is None:
            return None
        hit = m.group(0).lower()
        return [t for t in tokens if t.lower() == hit] or list(tokens)

    return found


def _keys(value, prefix="") -> list[str]:
    out = []
    for k, v in value.items():
        if isinstance(v, dict):
            out += _keys(v, f"{prefix}{k}.")
        else:
            out.append(f"{prefix}{k}")
    return out


def _tree_size(root: Path) -> tuple[int, int]:
    """The files under ``root`` and their bytes, links not followed."""
    files = total = 0
    if not root.is_dir():
        return 0, 0
    for folder, _dirs, names in os.walk(root):
        for name in names:
            try:
                st = os.lstat(os.path.join(folder, name))
            except OSError:
                continue
            files += 1
            total += st.st_size
    return files, total


def _summary(folder: Path) -> dict:
    try:
        return json.loads((folder / "run.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _alive(pid) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _holder(path: Path) -> dict | None:
    """Who holds the lock, as its file says, read without opening it for
    writing: a holder writes the file once it has the lock and empties it
    when it lets go."""
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text:
        return None
    try:
        held = json.loads(text)
    except ValueError:
        return {"what": "an older bench's command", "alive": True}
    if not isinstance(held, dict):
        return None
    held["alive"] = _alive(held.get("pid"))
    return held


def _waiting(queue: Path) -> int:
    """The tickets in the queue whose process runs; none is dropped here."""
    if not queue.is_dir():
        return 0
    n = 0
    for path in queue.glob("*.json"):
        try:
            ticket = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(ticket, dict) and _alive(ticket.get("pid")):
            n += 1
    return n
