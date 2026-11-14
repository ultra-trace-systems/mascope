"""
Read-path performance benchmarks against a scaled demo stack.

Boots nothing itself: start a demo stack, then opt in with
``MASCOPE_BENCH_TEST=1``. The suite clones the demo dataset up to a
configurable size (thousands of samples and collection ions), then exercises
the endpoints the batch overview and sample browser lean on, asserting two
kinds of budget:

- **per-request latency** - no single HTTP request may exceed
  ``MASCOPE_BENCH_REQUEST_BUDGET_S`` (default 20 s, the frontend's axios
  timeout). This is the exact failure mode that made large batches unusable;
  it is loose enough not to flake on a slow runner yet catches real blowups.
- **response size** - no single response body may exceed
  ``MASCOPE_BENCH_PAYLOAD_BUDGET_MB`` (default 12 MB). Deterministic, so it
  guards against a payload-shape regression (e.g. metadata duplicated per row)
  regardless of runner speed.

Every measurement is also written to ``$GITHUB_STEP_SUMMARY`` as a table so the
nightly job shows a trend, not just pass/fail.

    docker compose -f docker-compose.demo.yaml up -d
    MASCOPE_BENCH_TEST=1 uv run pytest server/backend/tests/system/benchmark/ -v

Configuration (all optional):

- ``MASCOPE_BENCH_APP_URL``: app origin (default ``http://127.0.0.1:8080``).
- ``MASCOPE_BENCH_POSTGRES_CONTAINER``: demo Postgres container name.
- ``MASCOPE_DB_NAME`` / ``MASCOPE_DB_USER``: demo database name / user.
- ``MASCOPE_BENCH_EMAIL`` / ``MASCOPE_BENCH_PASSWORD``: demo login.
- ``MASCOPE_BENCH_SAMPLE_MULT`` / ``MASCOPE_BENCH_ION_MULT``: clone generations
  to add (defaults 25 / 2 -> roughly 2100 samples, 1800 collection ions on the
  published bundle - well past the size that used to time out, with budget
  headroom for a shared runner). Set both to 0 to benchmark the dataset as-is.
- ``MASCOPE_BENCH_REQUEST_BUDGET_S`` / ``MASCOPE_BENCH_PAYLOAD_BUDGET_MB``:
  override the budgets above.
- ``MASCOPE_BENCH_ION_CHUNK``: ions per series request (default 100).

The batch-ledger benchmarks at the end of the module need a stack whose
batches hold a batch ledger, which the demo stack does not; they skip on one
with none. On the bench instance (``tooling/assignment_bench``), or a copy of
its database (the fold-in writes to the batch it grows):

- the anchor-axis growth curve of every ledger batch, read only;
- the fold's cost per arrival as one batch grows to each of
  ``MASCOPE_BENCH_FOLD_SIZES`` samples (default ``100,500,1000,2500,5000``,
  reached in whole generations of the batch's own samples), with
  ``MASCOPE_BENCH_FOLD_ARRIVALS`` arrivals per size (default 3). The fold runs
  in this process, against the stack's database, at
  ``MASCOPE_BENCH_DB_HOST`` / ``MASCOPE_BENCH_DB_PORT`` (default the runtime
  home's Postgres);
- the batch-peak series read for ``MASCOPE_BENCH_SERIES_P`` selected anchors
  (default ``1,10,100,300``) over the grown batch.

The batch grown is not the one the match-record benchmarks scale, where the
stack has another (``MASCOPE_BENCH_LEDGER_BATCH`` names one). Clones are never
removed, so the fold-in's small sizes need a fresh copy of the database.
``docs/dev/peak_assignment_batch_primary.md`` section 7.1 has the readings.
"""

import os
import subprocess
import time
from pathlib import Path

import pytest


APP_URL = os.environ.get("MASCOPE_BENCH_APP_URL", "http://127.0.0.1:8080")
POSTGRES_CONTAINER = os.environ.get(
    "MASCOPE_BENCH_POSTGRES_CONTAINER", "mascope_demo_postgres"
)
DB_NAME = os.environ.get("MASCOPE_DB_NAME", "mascope_demo")
DB_USER = os.environ.get("MASCOPE_DB_USER", "mascope_user")
EMAIL = os.environ.get("MASCOPE_BENCH_EMAIL", "demo@mascope.app")
PASSWORD = os.environ.get("MASCOPE_BENCH_PASSWORD", "mascope-demo")

SAMPLE_MULT = int(os.environ.get("MASCOPE_BENCH_SAMPLE_MULT", "25"))
ION_MULT = int(os.environ.get("MASCOPE_BENCH_ION_MULT", "2"))
ION_CHUNK = int(os.environ.get("MASCOPE_BENCH_ION_CHUNK", "100"))

REQUEST_BUDGET_S = float(os.environ.get("MASCOPE_BENCH_REQUEST_BUDGET_S", "20"))
PAYLOAD_BUDGET_MB = float(os.environ.get("MASCOPE_BENCH_PAYLOAD_BUDGET_MB", "12"))

EXPAND_SQL = Path(__file__).with_name("expand_demo.sql")

requires_bench_stack = pytest.mark.skipif(
    os.environ.get("MASCOPE_BENCH_TEST") != "1",
    reason=(
        "benchmark run not requested; boot a demo stack "
        "(docker compose -f docker-compose.demo.yaml up -d) and set "
        "MASCOPE_BENCH_TEST=1"
    ),
)


# --- Docker / DB helpers (demo Postgres port is not published) ---------------


def _docker(*args: str, stdin: str | None = None, timeout: int = 1800):
    """Run a docker CLI command captured, decoding as UTF-8."""
    return subprocess.run(
        ["docker", *args],
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def _psql(sql: str) -> str:
    """Run one SQL statement in the demo Postgres container, return stdout."""
    res = _docker(
        "exec", POSTGRES_CONTAINER, "psql", "-U", DB_USER, "-d", DB_NAME, "-tAc", sql
    )
    assert res.returncode == 0, f"psql failed for {sql!r}:\n{res.stderr.strip()}"
    return res.stdout.strip()


def _psql_script(sql: str, variables: dict[str, str]) -> str:
    """Pipe a multi-statement SQL script into the demo Postgres container."""
    args = ["exec", "-i", POSTGRES_CONTAINER, "psql", "-U", DB_USER, "-d", DB_NAME]
    for key, value in variables.items():
        args += ["-v", f"{key}={value}"]
    args += ["-f", "-"]
    res = _docker(*args, stdin=sql)
    assert res.returncode == 0, f"psql script failed:\n{res.stderr.strip()}"
    return res.stdout.strip()


# --- Measurement bookkeeping -------------------------------------------------


class Measurement:
    """One timed request group, collected for the summary and asserted on."""

    def __init__(self, name: str):
        self.name = name
        self.n_requests = 0
        self.total_s = 0.0
        self.max_request_s = 0.0
        self.max_body_mb = 0.0
        self.total_body_mb = 0.0
        self.rows = 0

    def record(self, elapsed_s: float, body_bytes: int, rows: int) -> None:
        self.n_requests += 1
        self.total_s += elapsed_s
        self.max_request_s = max(self.max_request_s, elapsed_s)
        body_mb = body_bytes / 1e6
        self.max_body_mb = max(self.max_body_mb, body_mb)
        self.total_body_mb += body_mb
        self.rows += rows

    def assert_budgets(self) -> None:
        assert self.max_request_s <= REQUEST_BUDGET_S, (
            f"{self.name}: slowest request {self.max_request_s:.1f}s exceeds "
            f"the {REQUEST_BUDGET_S:.0f}s per-request budget (UI would time out)"
        )
        assert self.max_body_mb <= PAYLOAD_BUDGET_MB, (
            f"{self.name}: largest response {self.max_body_mb:.1f}MB exceeds "
            f"the {PAYLOAD_BUDGET_MB:.0f}MB payload budget"
        )


# Shared across the ordered test functions and drained by the summary fixture.
MEASUREMENTS: list[Measurement] = []


def _timed(session, method: str, path: str, **kwargs):
    """Issue a request via a requests.Session, return (response, elapsed_s)."""
    start = time.perf_counter()
    resp = session.request(method, f"{APP_URL}{path}", timeout=120, **kwargs)
    elapsed = time.perf_counter() - start
    resp.raise_for_status()
    return resp, elapsed


# --- Fixtures ----------------------------------------------------------------


@pytest.fixture(scope="module")
def bench_stack():
    """Scale the demo dataset, log in, and expose the batch/collection under test."""
    # Pick the largest batch and the largest collection attached to it, so the
    # benchmark needs no hardcoded ids (they are regenerated on every restore).
    batch_id = _psql(
        "SELECT sample_batch_id FROM sample_item "
        "GROUP BY sample_batch_id ORDER BY count(*) DESC LIMIT 1"
    )
    assert batch_id, "no sample batches in the demo database"
    collection_id = _psql(
        "SELECT tcisb.target_collection_id "
        "FROM target_collection_in_sample_batch tcisb "
        f"WHERE tcisb.sample_batch_id = '{batch_id}' "
        "ORDER BY ( "
        "  SELECT count(DISTINCT ti.target_ion_id) FROM target_ion ti "
        "  JOIN target_compound_in_target_collection tcc "
        "    ON tcc.target_compound_id = ti.target_compound_id "
        "  WHERE tcc.target_collection_id = tcisb.target_collection_id "
        ") DESC LIMIT 1"
    )
    assert collection_id, f"batch {batch_id} has no attached target collection"

    # Scale it (idempotent: deterministic ids + ON CONFLICT DO NOTHING).
    _psql_script(
        EXPAND_SQL.read_text(),
        {
            "batch": batch_id,
            "collection": collection_id,
            "mult": str(SAMPLE_MULT),
            "imult": str(ION_MULT),
        },
    )

    sample_count = int(
        _psql(f"SELECT count(*) FROM sample_item WHERE sample_batch_id = '{batch_id}'")
    )
    ion_ids = _psql(
        "SELECT DISTINCT ti.target_ion_id FROM target_ion ti "
        "JOIN target_compound_in_target_collection tcc "
        "  ON tcc.target_compound_id = ti.target_compound_id "
        f"WHERE tcc.target_collection_id = '{collection_id}'"
    ).splitlines()

    import requests

    session = requests.Session()
    resp = session.post(
        f"{APP_URL}/api/auth/login",
        data={"grant_type": "password", "username": EMAIL, "password": PASSWORD},
        timeout=60,
    )
    assert resp.status_code in (200, 204), (
        f"login as {EMAIL} failed ({resp.status_code}); is the stack up at {APP_URL}?"
    )

    yield {
        "session": session,
        "batch_id": batch_id,
        "collection_id": collection_id,
        "ion_ids": [i for i in ion_ids if i],
        "sample_count": sample_count,
    }
    session.close()


@pytest.fixture(scope="module", autouse=True)
def _write_summary():
    """After the module runs, emit a markdown table to the CI step summary."""
    yield
    if not MEASUREMENTS:
        return
    lines = [
        f"### API benchmark ({SAMPLE_MULT}x samples, {ION_MULT}x ions)",
        "",
        "| endpoint | requests | total s | slowest req s | max body MB | rows |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for m in MEASUREMENTS:
        lines.append(
            f"| {m.name} | {m.n_requests} | {m.total_s:.1f} | "
            f"{m.max_request_s:.2f} | {m.max_body_mb:.2f} | {m.rows} |"
        )
    table = "\n".join(lines) + "\n"
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as fh:
            fh.write(table)
    print("\n" + table)


# --- Benchmarks --------------------------------------------------------------


@requires_bench_stack
def test_dataset_is_scaled(bench_stack):
    """Sanity: the clone step produced a genuinely large batch to benchmark."""
    if SAMPLE_MULT > 0:
        assert bench_stack["sample_count"] > 1000, (
            f"expected a scaled batch, got {bench_stack['sample_count']} samples"
        )
    assert bench_stack["ion_ids"], "collection under test has no target ions"


@requires_bench_stack
def test_samples_list(bench_stack):
    """The sample browser lists every sample in the batch in one request."""
    m = Measurement("GET /samples")
    MEASUREMENTS.append(m)
    resp, elapsed = _timed(
        bench_stack["session"],
        "GET",
        f"/api/samples?sample_batch_id={bench_stack['batch_id']}",
    )
    payload = resp.json()
    rows = len(payload.get("data") or payload.get("samples") or [])
    m.record(elapsed, len(resp.content), rows)
    m.assert_budgets()


@requires_bench_stack
def test_collection_focus_ion_aggregate(bench_stack):
    """Focusing a collection loads its batch-level best-match ion aggregate."""
    m = Measurement("POST /match/records/ion (batch aggregate)")
    MEASUREMENTS.append(m)
    resp, elapsed = _timed(
        bench_stack["session"],
        "POST",
        "/api/match/records/ion",
        json={
            "sample_batch_id": bench_stack["batch_id"],
            "target_collection_id": bench_stack["collection_id"],
        },
    )
    m.record(elapsed, len(resp.content), resp.json().get("results", 0))
    m.assert_budgets()


@requires_bench_stack
def test_chart_series_full_load(bench_stack):
    """Loading chart datapoints for the whole collection, chunked as the UI does."""
    m = Measurement("POST /match/records/ion/series (chart data)")
    MEASUREMENTS.append(m)
    ion_ids = bench_stack["ion_ids"]
    for i in range(0, len(ion_ids), ION_CHUNK):
        chunk = ion_ids[i : i + ION_CHUNK]
        resp, elapsed = _timed(
            bench_stack["session"],
            "POST",
            "/api/match/records/ion/series",
            json={
                "sample_batch_id": bench_stack["batch_id"],
                "target_ion_ids": chunk,
            },
        )
        points = sum(
            len(r["match_series"]["sample_item_ids"]) for r in resp.json()["data"]
        )
        m.record(elapsed, len(resp.content), points)
    m.assert_budgets()


# --- Batch ledger benchmarks -------------------------------------------------
#
# The batch layer's three measurements (docs/dev/peak_assignment_batch_primary.md,
# section 7.1): the anchor axis as the batch's own samples built it, the fold's
# cost per arrival as the batch grows, and the batch-peak series read over the
# grown batch. They need a stack whose batches hold a batch ledger - the bench
# instance, or any stack where a batch was folded - and skip on one with none,
# as the demo stack is. The fold runs in this process, on the stack's database,
# so it also needs that database's port: MASCOPE_BENCH_DB_HOST/PORT, defaulting
# to the runtime home's own Postgres.

LEDGER_BATCH = os.environ.get("MASCOPE_BENCH_LEDGER_BATCH", "")
FOLD_SIZES = [
    int(size)
    for size in os.environ.get(
        "MASCOPE_BENCH_FOLD_SIZES", "100,500,1000,2500,5000"
    ).split(",")
]
FOLD_ARRIVALS = int(os.environ.get("MASCOPE_BENCH_FOLD_ARRIVALS", "3"))
#: How much an arrival at the largest size may cost over one at the smallest
#: for the fold to count as bounded: its design is O(P log A) per arrival, so
#: the batch's own size should barely show.
FOLD_GROWTH = float(os.environ.get("MASCOPE_BENCH_FOLD_GROWTH", "2"))
SERIES_SELECTIONS = [
    int(p) for p in os.environ.get("MASCOPE_BENCH_SERIES_P", "1,10,100,300").split(",")
]
#: Batch peaks per series request, as the batch chart asks for them
#: (ChartBatchAssignments/data.js, CHUNK_SIZE); the chart plots at most 300.
SERIES_CHUNK = 100
MARKER = "[bench-clone"


def _psql_read(sql: str) -> list[list[str]]:
    """Run one query in a read-only transaction, return its rows as fields.

    Read-only by the server's own setting, so a reading pointed at a database
    it must not change cannot change it.
    """
    res = _docker(
        "exec",
        "-e",
        "PGOPTIONS=-c default_transaction_read_only=on",
        POSTGRES_CONTAINER,
        "psql",
        "-U",
        DB_USER,
        "-d",
        DB_NAME,
        "-tAF",
        "\t",
        "-c",
        sql,
    )
    assert res.returncode == 0, f"psql failed for {sql!r}:\n{res.stderr.strip()}"
    return [line.split("\t") for line in res.stdout.splitlines() if line]


def _ledger_batches() -> list[str]:
    """Every batch with a batch ledger, the one with the most anchors first."""
    return [
        row[0]
        for row in _psql_read(
            "SELECT sample_batch_id FROM batch_peak "
            "GROUP BY sample_batch_id ORDER BY count(*) DESC, sample_batch_id"
        )
    ]


@pytest.fixture(scope="module")
def ledger_batches():
    """The stack's batches with a ledger; the module's ledger tests skip without."""
    batches = _ledger_batches()
    if not batches:
        pytest.skip(
            "no batch on this stack holds a batch ledger; run the batch-ledger "
            "benchmarks on the bench instance, or fold a batch first"
        )
    return batches


def anchor_growth(sample_batch_id: str) -> list[dict]:
    """The anchor axis of one batch as its own samples built it, per partition.

    The fold mints an anchor when a sample brings a peak no anchor holds, and
    never moves or drops one, so an anchor's first member in acquisition order
    is the sample that minted it. Only the batch's own samples are read: the
    clones this benchmark adds sit on their seeds' anchors and would only
    inflate every occupancy.
    """
    rows = _psql_read(
        "WITH seeds AS ("
        "  SELECT sample_item_id, row_number() OVER ("
        "    ORDER BY datetime_utc, t0, sample_item_id) AS k"
        "  FROM sample_view"
        f"  WHERE sample_batch_id = '{sample_batch_id}'"
        f"    AND sample_item_name NOT LIKE '%{MARKER}%'"
        "    AND EXISTS (SELECT 1 FROM batch_peak_occurrence o"
        "      WHERE o.sample_item_id = sample_view.sample_item_id))"
        " SELECT bp.ionization_mode_id, bp.intensity_variable, bp.consensus_tier,"
        "   min(s.k), count(*), (SELECT count(*) FROM seeds)"
        " FROM batch_peak bp"
        " JOIN batch_peak_occurrence o ON o.batch_peak_id = bp.batch_peak_id"
        " JOIN seeds s ON s.sample_item_id = o.sample_item_id"
        f" WHERE bp.sample_batch_id = '{sample_batch_id}'"
        " GROUP BY bp.batch_peak_id"
    )
    partitions: dict[tuple, list[tuple]] = {}
    n_samples = 0
    for mode, unit, tier, first, members, seeds in rows:
        partitions.setdefault((mode, unit), []).append((int(first), int(members), tier))
        n_samples = int(seeds)
    return [
        growth_reading(anchors, n_samples, unit)
        for (_, unit), anchors in sorted(partitions.items(), key=lambda kv: -len(kv[1]))
    ]


def growth_reading(anchors: list[tuple], n_samples: int, unit: str | None) -> dict:
    """What one partition's anchor axis says.

    :param anchors: ``(first sample, members, consensus tier)`` per anchor, the
        first sample counted from 1 in acquisition order.
    :param n_samples: Samples in the batch with a member anywhere in it.
    :returns: the growth curve (anchors after 1, 10, 25, 50, 75 and 100 % of
        the samples), the anchors a late arrival mints (the last quarter's
        rate), and the occupancy: the singletons the ledger's default floor
        (``min_n_present=2``) hides, the tiers among them, and the share of
        member rows they hold, which is what carrying a singleton on its
        anchor (decision 10.4) would save.
    """
    firsts = sorted(first for first, _, _ in anchors)
    n_samples = max(n_samples, firsts[-1] if firsts else 0)

    def after(fraction: float) -> int:
        k = max(1, round(n_samples * fraction))
        return sum(1 for first in firsts if first <= k)

    curve = {f"{int(f * 100)}%": after(f) for f in (0.01, 0.1, 0.25, 0.5, 0.75, 1.0)}
    late_from = round(n_samples * 0.75)
    late_samples = max(1, n_samples - late_from)
    late = [a for a in anchors if a[0] > late_from]
    singletons = [a for a in anchors if a[1] == 1]
    members = sum(a[1] for a in anchors)
    hidden_tiers: dict[str, int] = {}
    for _, _, tier in singletons:
        hidden_tiers[tier] = hidden_tiers.get(tier, 0) + 1
    return {
        "instrument_type": "tof" if unit == "sum_peak_areas" else "orbitrap",
        "samples": n_samples,
        "anchors": len(anchors),
        "members": members,
        "curve": curve,
        "late_new_per_arrival": len(late) / late_samples,
        "late_singleton_share": (
            sum(1 for a in late if a[1] == 1) / len(late) if late else 0.0
        ),
        "singletons": len(singletons),
        "singleton_share": len(singletons) / len(anchors) if anchors else 0.0,
        "singleton_member_share": len(singletons) / members if members else 0.0,
        "hidden_by_floor_tiers": hidden_tiers,
        "occupancy": {
            "1": len(singletons),
            "2-4": sum(1 for a in anchors if 2 <= a[1] <= 4),
            "5-<50%": sum(1 for a in anchors if 5 <= a[1] < 0.5 * n_samples),
            ">=50%": sum(1 for a in anchors if a[1] >= max(5, 0.5 * n_samples)),
        },
    }


def test_growth_reading_counts():
    """The reading's curve, late rate and occupancy, on an axis made by hand.

    Hermetic, so it runs with the backend suite and not only on a bench stack.
    """
    anchors = [
        (1, 4, "assigned"),
        (1, 1, "assigned"),
        (2, 3, "candidate"),
        (2, 2, "candidate"),
        (3, 1, "unassigned"),
        (4, 1, "unassigned"),
    ]
    r = growth_reading(anchors, 4, "sum_peak_areas")
    assert r["instrument_type"] == "tof"
    assert r["curve"] == {
        "1%": 2,
        "10%": 2,
        "25%": 2,
        "50%": 4,
        "75%": 5,
        "100%": 6,
    }
    assert r["late_new_per_arrival"] == 1.0
    assert r["late_singleton_share"] == 1.0
    assert r["singletons"] == 3
    assert r["members"] == 12
    assert r["singleton_member_share"] == 0.25
    assert r["hidden_by_floor_tiers"] == {"assigned": 1, "unassigned": 2}
    assert r["occupancy"] == {"1": 3, "2-4": 3, "5-<50%": 0, ">=50%": 0}
    assert growth_reading(anchors, 4, "sum_peak_heights")["instrument_type"] == (
        "orbitrap"
    )


LEDGER_READINGS: list[dict] = []
#: The fold-in's readings per size and the series' measurements, kept for the
#: tests that hold them to their bounds.
FOLD_READINGS: list[dict] = []
SERIES_MEASUREMENTS: list[tuple[int, int, Measurement]] = []


@requires_bench_stack
def test_anchor_axis_growth(ledger_batches):
    """The anchor-axis growth curve of every ledger batch, read only.

    Reports what the ledger's default occupancy floor hides: the anchors only
    one sample saw, which the floor leaves off the drawable set, and how many
    of them carried an assignment. Asserts only the invariant the curve rests
    on, that the axis never shrinks.
    """
    for index, sample_batch_id in enumerate(ledger_batches, start=1):
        for reading in anchor_growth(sample_batch_id):
            reading["batch"] = index
            LEDGER_READINGS.append(reading)
            values = list(reading["curve"].values())
            assert values == sorted(values), f"the anchor axis shrank: {reading}"
            assert reading["curve"]["100%"] == reading["anchors"]
    lines = [
        "### Anchor-axis growth (the batches' own samples)",
        "",
        "| batch | type | samples | anchors | 1% | 10% | 25% | 50% | 75% "
        "| new/arrival, last 25% | singletons (hidden by floor 2) "
        "| their tiers | singleton member rows |",
        "| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: "
        "| ---: | --- | ---: |",
    ]
    for r in LEDGER_READINGS:
        c = r["curve"]
        tiers = ", ".join(
            f"{t} {n}" for t, n in sorted(r["hidden_by_floor_tiers"].items())
        )
        lines.append(
            f"| {r['batch']} | {r['instrument_type']} | {r['samples']} | "
            f"{r['anchors']} | {c['1%']} | {c['10%']} | {c['25%']} | {c['50%']} | "
            f"{c['75%']} | {r['late_new_per_arrival']:.1f} | "
            f"{r['singletons']} ({r['singleton_share']:.0%}) | {tiers} | "
            f"{r['singleton_member_share']:.1%} |"
        )
    _summary("\n".join(lines) + "\n")


def _summary(table: str) -> None:
    """Print a table and add it to the CI step summary when there is one."""
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as fh:
            fh.write(table)
    print("\n" + table)


# --- Growing a ledger batch, and folding into it -----------------------------


def _batch_size(sample_batch_id: str) -> int:
    return int(
        _psql(
            f"SELECT count(*) FROM sample_item WHERE sample_batch_id = '{sample_batch_id}'"
        )
    )


def _grow_to(sample_batch_id: str, n_seed: int, target: int) -> int:
    """Clone the batch's samples, ledger members included, to about ``target``.

    In whole generations of the seed (expand_demo.sql), so the size reached is
    the first multiple of the seed at or past ``target``; a batch already
    larger is left as it is. Returns the batch's size afterwards.
    """
    mult = max(0, -(-target // n_seed) - 1)
    _psql_script(
        EXPAND_SQL.read_text(),
        {"batch": sample_batch_id, "collection": "", "mult": str(mult), "imult": "0"},
    )
    return _batch_size(sample_batch_id)


@pytest.fixture(scope="module")
def fold_batch(ledger_batches):
    """The ledger batch the fold and series benchmarks grow, and its seed.

    Not the batch the match-record benchmarks above scale, where the stack has
    another, so the two do not grow one batch twice:
    ``MASCOPE_BENCH_LEDGER_BATCH`` names one. The arrival folded is the seed
    sample with the most members, the worst case for the touched set.
    """
    largest = _psql(
        "SELECT sample_batch_id FROM sample_item "
        "GROUP BY sample_batch_id ORDER BY count(*) DESC LIMIT 1"
    )
    others = [b for b in ledger_batches if b != largest]
    sample_batch_id = LEDGER_BATCH or (others or ledger_batches)[0]
    seed = _psql_read(
        "SELECT si.sample_item_id, count(o.*) FROM sample_item si"
        " JOIN batch_peak_occurrence o ON o.sample_item_id = si.sample_item_id"
        f" WHERE si.sample_batch_id = '{sample_batch_id}'"
        f"   AND si.sample_item_name NOT LIKE '%{MARKER}%'"
        " GROUP BY si.sample_item_id ORDER BY count(o.*) DESC, si.sample_item_id"
    )
    assert seed, f"batch {sample_batch_id} has a ledger but no member samples"
    return {
        "sample_batch_id": sample_batch_id,
        "n_seed": int(
            _psql(
                "SELECT count(*) FROM sample_item WHERE sample_batch_id = "
                f"'{sample_batch_id}' AND sample_item_name NOT LIKE '%{MARKER}%'"
            )
        ),
        "seed_sample_id": seed[0][0],
    }


def _arrival(seed_sample_id: str, label: str) -> str:
    """A new sample in the seed's batch, as a copy of the seed with no members.

    Marked as a clone, so the seed set of a later run leaves it out.
    """
    arrival_id = _psql(f"SELECT substr(md5('{seed_sample_id}:arrival:{label}'), 1, 16)")
    _psql(
        "INSERT INTO sample_item (sample_item_id, sample_batch_id, sample_file_id,"
        " sample_item_name, sample_item_type, locked, sample_item_attributes,"
        " filter_id, tic, polarity, ionization_mode_id, t0, t1,"
        " sample_item_utc_created, sample_item_utc_modified)"
        f" SELECT '{arrival_id}', sample_batch_id, sample_file_id,"
        f" sample_item_name || ' {MARKER} arrival {label}]', sample_item_type,"
        " locked, sample_item_attributes, filter_id, tic, polarity,"
        " ionization_mode_id, t0, t1, now(), now()"
        f" FROM sample_item WHERE sample_item_id = '{seed_sample_id}'"
        " ON CONFLICT (sample_item_id) DO NOTHING"
    )
    return arrival_id


async def _fold_arrivals(seed_sample_id: str, arrival_ids: list[str]) -> list[float]:
    """Fold each arrival as ingest does, timing the fold alone.

    The run-less ingest path: the seed's latest completed run's rows, handed to
    ``fold_sample_into_batch_peaks`` in memory (``persisted=False``), on an
    engine of this process's own pointed at the stack's database.
    """
    from sqlalchemy import select
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import mascope_backend.db as db_module
    from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
        _latest_completed_run_id,
        fold_sample_into_batch_peaks,
    )
    from mascope_backend.db import PeakAssignment
    from mascope_backend.db.secrets import postgres_password

    url = make_url(
        db_module.db_cfg.get_postgres_url(password=postgres_password, env_name="bench")
    ).set(
        database=DB_NAME,
        host=os.environ.get("MASCOPE_BENCH_DB_HOST", db_module.db_cfg.host),
        port=int(os.environ.get("MASCOPE_BENCH_DB_PORT", db_module.db_cfg.port)),
    )
    engine = create_async_engine(url)
    original = db_module.ASYNC_SESSION_MAKER
    db_module.ASYNC_SESSION_MAKER = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with db_module.async_session() as session:
            run_id = await _latest_completed_run_id(session, seed_sample_id)
            assert run_id, f"seed sample {seed_sample_id} has no completed run"
            rows = (
                (
                    await session.execute(
                        select(PeakAssignment).where(
                            PeakAssignment.peak_assignment_run_id == run_id
                        )
                    )
                )
                .scalars()
                .all()
            )
        elapsed = []
        for arrival_id in arrival_ids:
            start = time.perf_counter()
            folded = await fold_sample_into_batch_peaks(
                arrival_id, rows=rows, persisted=False
            )
            elapsed.append(time.perf_counter() - start)
            assert folded, f"arrival {arrival_id} folded nothing"
        return elapsed
    finally:
        db_module.ASYNC_SESSION_MAKER = original
        await engine.dispose()


@requires_bench_stack
def test_fold_in_per_arrival(fold_batch):
    """The fold's cost per arrival as the batch grows from 100 to 5,000 samples.

    At each size, ``FOLD_ARRIVALS`` new samples fold in as ingest folds them;
    the median is the size's cost. The touched set and the members its
    consensus recompute reads are reported beside it: the recompute reads
    every member of every anchor the arrival joins, which is what grows with
    the batch. :func:`test_fold_in_is_bounded` holds the readings to the bound.
    """
    import asyncio
    import statistics

    batch = fold_batch["sample_batch_id"]
    seed = fold_batch["seed_sample_id"]
    readings = []
    for target in FOLD_SIZES:
        size = _grow_to(batch, fold_batch["n_seed"], target)
        arrivals = [_arrival(seed, f"{target}-{j}") for j in range(FOLD_ARRIVALS)]
        elapsed = asyncio.run(_fold_arrivals(seed, arrivals))
        touched, read = _psql_read(
            "SELECT count(*), coalesce(sum(bp.n_present), 0) FROM batch_peak_occurrence o"
            " JOIN batch_peak bp ON bp.batch_peak_id = o.batch_peak_id"
            f" WHERE o.sample_item_id = '{arrivals[-1]}'"
        )[0]
        readings.append(
            {
                "target": target,
                "size": size,
                "median_s": statistics.median(elapsed),
                "max_s": max(elapsed),
                "touched": int(touched),
                "members_read": int(read),
            }
        )
    lines = [
        "### Fold-in per arrival",
        "",
        "| batch size | arrivals | median s | slowest s | anchors touched "
        "| members read | us per member read |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for r in readings:
        lines.append(
            f"| {r['size']} | {FOLD_ARRIVALS} | {r['median_s']:.2f} | "
            f"{r['max_s']:.2f} | {r['touched']} | {r['members_read']} | "
            f"{1e6 * r['median_s'] / max(1, r['members_read']):.1f} |"
        )
    _summary("\n".join(lines) + "\n")
    FOLD_READINGS.extend(readings)


@requires_bench_stack
@pytest.mark.xfail(
    strict=True,
    reason="#3231: the fold's consensus recompute reads every member of every "
    "anchor an arrival joins, so an arrival's cost grows with the batch",
)
def test_fold_in_is_bounded():
    """An arrival into the largest batch costs at most ``FOLD_GROWTH`` times one
    into the smallest: the fold's cost is bounded by the sample, not the batch.
    """
    if not FOLD_READINGS:
        pytest.skip("the fold-in benchmark took no readings")
    smallest, largest = FOLD_READINGS[0], FOLD_READINGS[-1]
    if largest["size"] < 10 * smallest["size"]:
        pytest.skip(
            f"the sizes folded ({smallest['size']} to {largest['size']}) span "
            "less than ten times; the bound says nothing over them"
        )
    assert largest["median_s"] <= FOLD_GROWTH * smallest["median_s"], (
        f"an arrival into {largest['size']} samples costs "
        f"{largest['median_s']:.1f}s, {largest['median_s'] / smallest['median_s']:.1f}x "
        f"one into {smallest['size']} ({smallest['median_s']:.1f}s)"
    )


@requires_bench_stack
def test_batch_peak_series(fold_batch):
    """The batch chart's series read for P selected anchors over the grown batch.

    The P anchors with the most members, asked for in chunks of 100 as the
    chart asks; each request is held to the module's latency budget, and
    :func:`test_batch_peak_series_payload` holds them to its payload budget.
    """
    import requests

    batch = fold_batch["sample_batch_id"]
    size = _grow_to(batch, fold_batch["n_seed"], max(FOLD_SIZES))
    anchors = [
        row[0]
        for row in _psql_read(
            "SELECT batch_peak_id FROM batch_peak"
            f" WHERE sample_batch_id = '{batch}'"
            " ORDER BY n_present DESC, batch_peak_id"
            f" LIMIT {max(SERIES_SELECTIONS)}"
        )
    ]
    session = requests.Session()
    resp = session.post(
        f"{APP_URL}/api/auth/login",
        data={"grant_type": "password", "username": EMAIL, "password": PASSWORD},
        timeout=60,
    )
    assert resp.status_code in (200, 204), (
        f"login as {EMAIL} failed ({resp.status_code}); is the stack up at {APP_URL}?"
    )
    try:
        for p in SERIES_SELECTIONS:
            m = Measurement(f"POST /batch-peaks/records/series (P={p}, {size} samples)")
            selected = anchors[:p]
            for i in range(0, len(selected), SERIES_CHUNK):
                resp, elapsed = _timed(
                    session,
                    "POST",
                    "/api/batch-peaks/records/series",
                    json={
                        "sample_batch_id": batch,
                        "batch_peak_ids": selected[i : i + SERIES_CHUNK],
                    },
                )
                records = resp.json()["data"]
                points = sum(len(r["peak_series"]["sample_item_ids"]) for r in records)
                if p == 1:
                    # The batch's fullest anchor holds every sample, the clones
                    # included, and its occupancy says so: the read measured
                    # is the grown batch's, not its seed's.
                    assert points == size, f"{points} points over {size} samples"
                    assert records[0]["n_present"] == points
                m.record(elapsed, len(resp.content), points)
            MEASUREMENTS.append(m)
            SERIES_MEASUREMENTS.append((p, size, m))
    finally:
        session.close()
    for _, _, m in SERIES_MEASUREMENTS:
        assert m.max_request_s <= REQUEST_BUDGET_S, (
            f"{m.name}: slowest request {m.max_request_s:.1f}s exceeds the "
            f"{REQUEST_BUDGET_S:.0f}s per-request budget (UI would time out)"
        )


@requires_bench_stack
@pytest.mark.xfail(
    strict=True,
    reason="#3232: a series chunk of 100 batch peaks over thousands of samples "
    "is several times the payload budget",
)
def test_batch_peak_series_payload():
    """No series response is larger than the payload budget.

    Held where a full chunk was read over 2,500 samples or more, the scale the
    known failure is at; a smaller reading says nothing about it.
    """
    full = [
        m for p, size, m in SERIES_MEASUREMENTS if p >= SERIES_CHUNK and size >= 2500
    ]
    if not full:
        pytest.skip("no full chunk of the series was read over 2,500 samples")
    for m in full:
        assert m.max_body_mb <= PAYLOAD_BUDGET_MB, (
            f"{m.name}: largest response {m.max_body_mb:.1f}MB exceeds "
            f"the {PAYLOAD_BUDGET_MB:.0f}MB payload budget"
        )
