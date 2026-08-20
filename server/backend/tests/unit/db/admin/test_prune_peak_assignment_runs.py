"""
Unit tests for the peak-assignment retention selection and its operator knobs.

``_select_prunable_run_ids`` decides which runs get deleted, and deleting a run
cascades to its whole ledger - so a mistake here is silent, irreversible data
loss. The decision is split across three layers, and each needs its own kind of
coverage:

- the Python ranking, which is fed the rows a query returned. Tested through a
  mocked session.
- the SQL, which decides *which* rows the ranking ever sees. A mocked session
  cannot see it at all, so the statements are executed against an in-memory
  SQLite copy of the run table, and compiled against the Postgres dialect where
  the two engines disagree (explicit null ordering) so the guard is asserted in
  the dialect that actually runs it.
- the batching, which is what stands between the script and the wire-protocol
  bind-parameter cap on the backlog it exists to clear. Tested by recording the
  statements a full prune issues.

Everything here stays hermetic - no server, no migration - so it runs on every
CI job rather than only where a database happens to be up.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from mascope_backend.db import PeakAssignmentRun
from mascope_backend.db.admin.peak_assignments import prune_runs
from mascope_backend.db.admin.peak_assignments.prune_runs import (
    DEFAULT_KEEP_FAILED_HOURS,
    DEFAULT_KEEP_IMPORTING_HOURS,
    DEFAULT_KEEP_PER_SAMPLE,
    DEFAULT_KEEP_PER_SAMPLE_TOTAL,
    DEFAULT_KEEP_RUNNING_HOURS,
    IMPORTING_STATUS,
    IN_FLIGHT_STATUSES,
    MIN_KEEP_IMPORTING_HOURS,
    MIN_KEEP_RUNNING_HOURS,
    NON_TERMINAL_STATUSES,
    _chunked,
    _completed_runs_statement,
    _select_prunable_run_ids,
    _stale_runs_statement,
    prune_peak_assignment_runs,
)
from mascope_backend.db.scripts import prune_peak_assignment_runs as prune_script


#: The engine the in-app assignment path stamps on the runs it creates. Runs
#: are ranked per (sample, engine), so most tests here name it explicitly.
IN_APP = "mascope"


def _session(completed_rows, stale_ids):
    """A session whose two queries return the given completed rows / stale ids.

    ``_select_prunable_run_ids`` issues exactly two statements: the completed-run
    scan (``.all()``) and the stale non-completed scan (``.scalars()``).
    """
    completed_result = MagicMock()
    completed_result.all.return_value = completed_rows

    stale_result = MagicMock()
    stale_result.scalars.return_value = stale_ids

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[completed_result, stale_result])
    return session


@pytest.mark.asyncio
async def test_keeps_the_newest_n_completed_per_sample():
    """Only runs past the keep window are prunable, and per sample."""
    rows = [
        # si1, newest first (as the query orders them)
        ("s1-new", "si1", IN_APP),
        ("s1-mid", "si1", IN_APP),
        ("s1-old", "si1", IN_APP),
        ("s1-older", "si1", IN_APP),
        # si2 has fewer than the keep count
        ("s2-only", "si2", IN_APP),
    ]
    session = _session(rows, [])

    prunable = await _select_prunable_run_ids(
        session, keep_per_sample=2, keep_failed_hours=24
    )

    assert prunable == ["s1-old", "s1-older"]


@pytest.mark.asyncio
async def test_keep_window_is_per_sample_not_global():
    """A sample with few runs is never pruned because another sample has many."""
    rows = [
        ("a1", "si1", IN_APP),
        ("a2", "si1", IN_APP),
        ("a3", "si1", IN_APP),
        ("b1", "si2", IN_APP),
    ]
    session = _session(rows, [])

    prunable = await _select_prunable_run_ids(
        session, keep_per_sample=1, keep_failed_hours=24
    )

    assert prunable == ["a2", "a3"]
    assert "b1" not in prunable


@pytest.mark.asyncio
async def test_stale_non_completed_runs_are_added():
    """Failed/interrupted runs past the grace period are prunable too."""
    session = _session([("keep", "si1", IN_APP)], ["failed-1", "failed-2"])

    prunable = await _select_prunable_run_ids(
        session, keep_per_sample=3, keep_failed_hours=24
    )

    assert prunable == ["failed-1", "failed-2"]


@pytest.mark.asyncio
async def test_result_is_deduplicated_and_order_stable():
    """The same run never appears twice, and order is preserved for dry runs."""
    rows = [("r1", "si1", IN_APP), ("r2", "si1", IN_APP), ("r3", "si1", IN_APP)]
    session = _session(rows, ["r3", "r4"])

    prunable = await _select_prunable_run_ids(
        session, keep_per_sample=1, keep_failed_hours=24
    )

    assert prunable == ["r2", "r3", "r4"]
    assert len(set(prunable)) == len(prunable)


@pytest.mark.asyncio
async def test_nothing_prunable_returns_empty():
    session = _session([("only", "si1", IN_APP)], [])

    assert (
        await _select_prunable_run_ids(session, keep_per_sample=3, keep_failed_hours=24)
        == []
    )


class TestTheTotalBoundsTheEngineQuotas:
    """The per-engine quota needs an outer bound, because `engine` is client input.

    Nothing constrains what an importing client puts in `engine`, so one naming
    itself per build or per release mints a fresh keep_per_sample quota on every
    import and the table grows without limit - defeating the pass entirely. The
    per-sample total caps the sum. The in-app engine is exempt from it, or a
    burst of imports would fill the total and start evicting exactly the history
    the per-engine quota exists to protect.
    """

    @pytest.mark.asyncio
    async def test_a_client_cycling_engine_names_cannot_grow_without_limit(self):
        """Five engines at the per-engine quota, over a total of six."""
        rows = [
            (f"{engine}-{index}", "si1", engine)
            for engine in ("e1", "e2", "e3", "e4", "e5")
            for index in range(2)
        ]
        session = _session(rows, [])

        prunable = await _select_prunable_run_ids(
            session, keep_per_sample=2, keep_failed_hours=24, keep_per_sample_total=6
        )

        # Six kept, the rest reclaimed - where the per-engine quota alone would
        # have kept all ten.
        assert len(rows) - len(prunable) == 6

    @pytest.mark.asyncio
    async def test_the_in_app_engine_is_exempt_from_the_total(self):
        """Imports fill the total; the in-app run behind them still survives.

        Ordered newest-first within the sample, so every import is newer than
        the in-app run - the arrangement that would evict it if the total
        applied to every engine alike.
        """
        rows = [(f"import-{index}", "si1", "peaky") for index in range(4)]
        rows += [("in-app-1", "si1", IN_APP)]
        session = _session(rows, [])

        prunable = await _select_prunable_run_ids(
            session, keep_per_sample=4, keep_failed_hours=24, keep_per_sample_total=4
        )

        assert "in-app-1" not in prunable
        assert prunable == []

    @pytest.mark.asyncio
    async def test_the_in_app_engine_is_still_bounded_by_its_own_quota(self):
        """Exempt from the total is not exempt from everything."""
        rows = [(f"in-app-{index}", "si1", IN_APP) for index in range(5)]
        session = _session(rows, [])

        prunable = await _select_prunable_run_ids(
            session, keep_per_sample=2, keep_failed_hours=24, keep_per_sample_total=2
        )

        assert prunable == ["in-app-2", "in-app-3", "in-app-4"]

    @pytest.mark.asyncio
    async def test_a_condemned_run_does_not_consume_a_slot_in_the_total(self):
        """The total counts runs kept, not runs seen.

        Otherwise runs already over their engine's quota would eat the total's
        budget on the way past and evict runs that are within theirs.
        """
        rows = [(f"peaky-{index}", "si1", "peaky") for index in range(5)]
        rows += [("other-1", "si1", "other")]
        session = _session(rows, [])

        prunable = await _select_prunable_run_ids(
            session, keep_per_sample=2, keep_failed_hours=24, keep_per_sample_total=3
        )

        assert "other-1" not in prunable

    @pytest.mark.asyncio
    async def test_a_total_below_the_per_engine_quota_is_refused(self):
        """It would evict runs the per-engine budget promises to keep."""
        with pytest.raises(ValueError, match="keep_per_sample_total"):
            await prune_peak_assignment_runs(
                dry_run=True, keep_per_sample=3, keep_per_sample_total=2
            )

    def test_the_default_total_leaves_room_for_several_engines(self):
        """A deployment with a couple of real engines never meets the cap."""
        assert DEFAULT_KEEP_PER_SAMPLE_TOTAL >= 4 * DEFAULT_KEEP_PER_SAMPLE


class TestBudgetIsPerSampleAndEngine:
    """Imported runs and in-app runs must not compete for one sample's quota.

    A run can be computed here or published from an external engine, and the
    read model serves whichever completed last. On a shared budget that makes
    publishing destructive: republishing an import ``keep_per_sample`` times
    would evict every in-app run for that sample, its whole ledger cascading
    with it - the exact opposite of what "an import only ever appends" promises
    a reader. Separate quotas are what make that promise true.
    """

    @pytest.mark.asyncio
    async def test_imports_cannot_evict_a_samples_in_app_runs(self):
        """The case the shared budget got wrong: three imports, one in-app run."""
        rows = [
            ("import-3", "si1", "peaky"),
            ("import-2", "si1", "peaky"),
            ("import-1", "si1", "peaky"),
            ("in-app-1", "si1", IN_APP),
        ]
        session = _session(rows, [])

        prunable = await _select_prunable_run_ids(
            session, keep_per_sample=3, keep_failed_hours=24
        )

        assert prunable == []

    @pytest.mark.asyncio
    async def test_each_engine_ages_out_of_its_own_quota(self):
        """Over budget on one engine never reaches the other's runs."""
        rows = [
            ("import-new", "si1", "peaky"),
            ("import-old", "si1", "peaky"),
            ("in-app-new", "si1", IN_APP),
            ("in-app-old", "si1", IN_APP),
        ]
        session = _session(rows, [])

        prunable = await _select_prunable_run_ids(
            session, keep_per_sample=1, keep_failed_hours=24
        )

        assert sorted(prunable) == ["import-old", "in-app-old"]

    @pytest.mark.asyncio
    async def test_the_same_engine_on_another_sample_is_untouched(self):
        """Grouping is by the pair, not by engine alone."""
        rows = [
            ("a-new", "si1", "peaky"),
            ("a-old", "si1", "peaky"),
            ("b-only", "si2", "peaky"),
        ]
        session = _session(rows, [])

        prunable = await _select_prunable_run_ids(
            session, keep_per_sample=1, keep_failed_hours=24
        )

        assert prunable == ["a-old"]

    def test_the_scan_orders_by_recency_within_a_sample(self):
        """Per-sample recency, which is what both budgets are ranked over.

        The scan used to group by engine as well, and this asserted that. It no
        longer does: a second budget - the per-sample total - has to be ranked
        over the same single pass, and only a per-sample ordering serves both.
        It serves the per-engine quota too, because an ordering that is
        newest-first within a sample is also newest-first within any engine's
        subset of it; the reverse does not hold, which is why this is the order
        that survived.
        """
        sql = str(_completed_runs_statement().compile(dialect=postgresql.dialect()))
        order_by = sql.split("ORDER BY", 1)[1]

        assert order_by.index("sample_item_id") < order_by.index(
            "peak_assignment_run_utc_created"
        )
        assert "engine" not in order_by

    @pytest.mark.asyncio
    async def test_the_per_engine_quota_holds_when_engines_interleave(self):
        """The behaviour dropping `engine` from the ORDER BY could have broken.

        Runs arrive newest-first across the whole sample, so the two engines
        alternate rather than arriving in contiguous blocks. Each engine's
        quota still has to be counted over its own subset.
        """
        rows = [
            ("peaky-3", "si1", "peaky"),
            ("in-app-3", "si1", IN_APP),
            ("peaky-2", "si1", "peaky"),
            ("in-app-2", "si1", IN_APP),
            ("peaky-1", "si1", "peaky"),
            ("in-app-1", "si1", IN_APP),
        ]
        session = _session(rows, [])

        prunable = await _select_prunable_run_ids(
            session, keep_per_sample=2, keep_failed_hours=24
        )

        # The third of each engine, not the last two rows of the scan.
        assert sorted(prunable) == ["in-app-1", "peaky-1"]


def _hours_ago(hours: float) -> datetime:
    """A UTC timestamp ``hours`` in the past."""
    return datetime.now(timezone.utc) - timedelta(hours=hours)


def _run(
    run_id: str,
    status: str,
    sample: str = "si1",
    created=None,
    completed=None,
    engine: str = IN_APP,
):
    """One `peak_assignment_run` row, with only the columns these tests read."""
    return {
        "peak_assignment_run_id": run_id,
        "sample_item_id": sample,
        "engine": engine,
        "engine_version": "test",
        "status": status,
        "peak_assignment_run_utc_created": created,
        "peak_assignment_run_utc_completed": completed,
    }


@pytest.fixture
def run_table():
    """An in-memory SQLite copy of `peak_assignment_run`, one per test.

    Only that table is created. Its foreign key points at a `sample_item` table
    that is never materialized, which is harmless: SQLite does not enforce
    foreign keys unless asked to, and nothing here inserts through the ORM.
    """
    engine = sa.create_engine("sqlite://")
    PeakAssignmentRun.__table__.create(engine)
    try:
        yield engine
    finally:
        engine.dispose()


def _seed(engine, rows):
    """Insert the given run rows."""
    with engine.begin() as connection:
        connection.execute(sa.insert(PeakAssignmentRun), rows)


def _stale_ids(
    engine, keep_failed_hours=24, keep_running_hours=72, keep_importing_hours=24
):
    """Run the stale-run statement for real and return the ids it selects."""
    now = datetime.now(timezone.utc)
    statement = _stale_runs_statement(
        failed_cutoff=now - timedelta(hours=keep_failed_hours),
        running_cutoff=now - timedelta(hours=keep_running_hours),
        importing_cutoff=now - timedelta(hours=keep_importing_hours),
    )
    with engine.connect() as connection:
        return list(connection.execute(statement).scalars())


class TestCompletedScanOrdering:
    """The ordering that decides which run keeps a slot, and which is deleted."""

    def test_postgres_sql_orders_nulls_last(self):
        """The explicit NULLS LAST must survive in the dialect that runs it.

        Postgres orders DESC as NULLS FIRST and SQLite as NULLS LAST, so an
        execution test proves nothing about the deployment target: dropping
        ``.nullslast()`` would still pass on SQLite while ranking a
        null-timestamped run as the newest on Postgres - taking a keep slot and
        pushing a live run past the cutoff. Only the rendered SQL shows it.
        """
        sql = str(_completed_runs_statement().compile(dialect=postgresql.dialect()))

        assert "peak_assignment_run_utc_created DESC NULLS LAST" in sql
        assert "peak_assignment_run_id DESC" in sql

    def test_a_null_timestamp_ranks_oldest_and_is_pruned_first(self, run_table):
        """A run with no timestamp loses to any timestamped run of its sample."""
        _seed(
            run_table,
            [
                _run("older", "completed", created=_hours_ago(48)),
                _run("no-timestamp", "completed", created=None),
                _run("newest", "completed", created=_hours_ago(1)),
            ],
        )

        with run_table.connect() as connection:
            ordered = list(connection.execute(_completed_runs_statement()))

        assert [row[0] for row in ordered] == ["newest", "older", "no-timestamp"]

    @pytest.mark.asyncio
    async def test_the_live_run_survives_a_null_timestamped_sibling(self, run_table):
        """End to end: the real scan feeds the ranking, and keeps the live run."""
        _seed(
            run_table,
            [
                _run("no-timestamp", "completed", created=None),
                _run("newest", "completed", created=_hours_ago(1)),
            ],
        )
        with run_table.connect() as connection:
            ordered = list(connection.execute(_completed_runs_statement()))

        prunable = await _select_prunable_run_ids(
            _session(ordered, []), keep_per_sample=1, keep_failed_hours=24
        )

        assert prunable == ["no-timestamp"]


class TestInFlightRunsAreProtected:
    """A run that may still be executing is the engine's write target.

    Deleting it mid-flight makes the terminal ledger insert fail on the foreign
    key and loses the whole run, so the ordinary failed grace must not reach it.
    """

    def test_a_run_past_the_failed_grace_but_inside_its_own_is_kept(self, run_table):
        """The knob an operator is most likely to shorten cannot reach a live run."""
        _seed(
            run_table,
            [
                _run("in-flight", "running", created=_hours_ago(48)),
                _run("terminal", "failed", created=_hours_ago(48)),
            ],
        )

        assert _stale_ids(run_table, keep_failed_hours=1) == ["terminal"]

    def test_a_run_far_past_its_own_grace_is_reclaimed(self, run_table):
        """The long grace still bounds growth on a server that never restarts."""
        _seed(run_table, [_run("abandoned", "running", created=_hours_ago(200))])

        assert _stale_ids(run_table, keep_running_hours=72) == ["abandoned"]

    def test_pending_is_treated_as_in_flight(self, run_table):
        """'pending' is the column default, so a row can exist before it runs."""
        _seed(run_table, [_run("queued", "pending", created=_hours_ago(48))])

        assert _stale_ids(run_table, keep_failed_hours=1) == []
        assert "pending" in IN_FLIGHT_STATUSES

    def test_an_in_flight_run_without_timestamps_is_not_ancient(self, run_table):
        """A missing timestamp reads as 'just started', not as 'long dead'.

        The terminal branch treats a run with neither timestamp as ancient, since
        it would otherwise be immortal. An in-flight row gets the opposite
        treatment because there the two cases are indistinguishable - and it is
        not stranded either: the startup reaper moves it to 'failed', after which
        the terminal branch reclaims it.
        """
        _seed(
            run_table,
            [
                _run("in-flight", "running"),
                _run("terminal", "failed"),
            ],
        )

        assert _stale_ids(run_table) == ["terminal"]

    def test_completed_runs_are_never_stale(self, run_table):
        """Age alone never deletes a completed run; only the keep-N rule does."""
        _seed(run_table, [_run("ancient", "completed", completed=_hours_ago(10_000))])

        assert _stale_ids(run_table) == []

    def test_a_fresh_failure_stays_inspectable(self, run_table):
        """The failed grace exists so its error can still be read."""
        _seed(run_table, [_run("just-failed", "failed", completed=_hours_ago(1))])

        assert _stale_ids(run_table, keep_failed_hours=24) == []


class TestImportsHaveTheirOwnGrace:
    """An assembling import is non-terminal, but nothing is executing it.

    It must not be swept by the failed grace (it has not failed), nor held by
    the in-flight grace (no worker is writing into it, and it blocks new work on
    its sample while it sits there), so it gets a grace of its own.
    """

    def test_importing_is_not_reclaimed_by_the_failed_grace(self, run_table):
        """The trap of treating any non-terminal status as terminal.

        The import grace is held wide open so the only rule that could select
        this row is the terminal one - which is exactly what must not reach it.
        """
        _seed(run_table, [_run("uploading", IMPORTING_STATUS, created=_hours_ago(48))])

        assert _stale_ids(run_table, keep_failed_hours=1, keep_importing_hours=96) == []

    def test_a_live_upload_is_not_reclaimed(self, run_table):
        """A recent import is a client still sending chunks."""
        _seed(run_table, [_run("uploading", IMPORTING_STATUS, created=_hours_ago(1))])

        assert _stale_ids(run_table, keep_importing_hours=24) == []

    def test_an_abandoned_upload_is_reclaimed_on_its_own_grace(self, run_table):
        """Otherwise it blocks every later run for its sample indefinitely."""
        _seed(run_table, [_run("abandoned", IMPORTING_STATUS, created=_hours_ago(48))])

        assert _stale_ids(run_table, keep_importing_hours=24) == ["abandoned"]

    def test_the_import_grace_is_independent_of_the_in_flight_one(self, run_table):
        """Each non-terminal kind answers only to its own knob."""
        _seed(
            run_table,
            [
                _run("uploading", IMPORTING_STATUS, created=_hours_ago(48)),
                _run("computing", "running", created=_hours_ago(48)),
            ],
        )

        assert _stale_ids(
            run_table, keep_running_hours=12, keep_importing_hours=96
        ) == ["computing"]
        assert _stale_ids(
            run_table, keep_running_hours=96, keep_importing_hours=12
        ) == ["uploading"]

    def test_importing_is_non_terminal_but_not_in_flight(self):
        """The two sets differ, which is what gives it a separate grace."""
        assert IMPORTING_STATUS not in IN_FLIGHT_STATUSES
        assert IMPORTING_STATUS in NON_TERMINAL_STATUSES
        assert set(IN_FLIGHT_STATUSES) < set(NON_TERMINAL_STATUSES)

    def test_an_importing_run_without_timestamps_is_not_ancient(self, run_table):
        """Same reasoning as an in-flight run: unknown age reads as 'new'."""
        _seed(run_table, [_run("uploading", IMPORTING_STATUS)])

        assert _stale_ids(run_table) == []


def _in_clause_ids(statement):
    """The run ids bound into a statement's IN clause.

    SQLAlchemy binds an IN list as one expanding parameter, so the compiled
    parameters carry the whole batch under a single key - and its length is
    exactly what counts against the wire-protocol parameter cap.
    """
    params = statement.compile().params
    return next(value for value in params.values() if isinstance(value, list))


class _RecordingSession:
    """Stands in for the session a prune opens, recording the batches it asks for.

    ``_select_prunable_run_ids`` is short-circuited (no completed runs, every
    prunable id arriving from the stale scan) so the test is about batching only.
    """

    def __init__(self, prunable_ids, rows_per_run=1000):
        self.prunable_ids = list(prunable_ids)
        self.rows_per_run = rows_per_run
        self.count_batches: list[int] = []
        self.delete_batches: list[int] = []
        self._selects = 0

    async def execute(self, statement):
        if isinstance(statement, sa.Delete):
            batch = _in_clause_ids(statement)
            self.delete_batches.append(len(batch))
            result = MagicMock()
            result.rowcount = len(batch)
            return result

        self._selects += 1
        result = MagicMock()
        result.all.return_value = []
        result.scalars.return_value = self.prunable_ids if self._selects == 2 else []
        return result

    async def scalar(self, statement):
        batch = _in_clause_ids(statement)
        self.count_batches.append(len(batch))
        return len(batch) * self.rows_per_run

    async def commit(self):
        return None


def _session_factory(session):
    """A stand-in for ``async_session``: called, then entered as a context."""
    factory = MagicMock()
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=session)
    context.__aexit__ = AsyncMock(return_value=False)
    factory.return_value = context
    return factory


class TestBatching:
    """No statement may grow with the backlog.

    Every id in the selection binds one parameter, and the protocol caps a
    statement at 32767 of them. The selection is unbounded - it is precisely the
    accumulated backlog the prune exists to clear - so an unbatched statement
    fails on the deployments that need the prune most.
    """

    def test_chunks_cover_everything_in_order(self):
        chunks = list(_chunked([str(index) for index in range(7)], 3))

        assert [list(chunk) for chunk in chunks] == [
            ["0", "1", "2"],
            ["3", "4", "5"],
            ["6"],
        ]

    def test_no_chunk_exceeds_the_size(self):
        chunks = list(_chunked([str(index) for index in range(100)], 25))

        assert [len(chunk) for chunk in chunks] == [25, 25, 25, 25]

    def test_empty_selection_yields_no_chunks(self):
        assert list(_chunked([], 25)) == []

    @pytest.mark.asyncio
    async def test_the_row_count_is_batched_like_the_deletes(self, monkeypatch):
        """The count query is the one that used to take the whole selection."""
        ids = [f"run-{index:05d}" for index in range(120)]
        session = _RecordingSession(ids)
        monkeypatch.setattr(prune_runs, "async_session", _session_factory(session))

        result = await prune_peak_assignment_runs(batch_size=25)

        assert session.count_batches == [25, 25, 25, 25, 20]
        assert session.delete_batches == [25, 25, 25, 25, 20]
        assert result["deleted_runs"] == 120
        # Summed across batches, not reported from the last one.
        assert result["deleted_assignments"] == 120 * 1000

    @pytest.mark.asyncio
    async def test_a_dry_run_batches_its_count_too(self, monkeypatch):
        """The dry run reports the same total without issuing a delete."""
        ids = [f"run-{index:05d}" for index in range(60)]
        session = _RecordingSession(ids)
        monkeypatch.setattr(prune_runs, "async_session", _session_factory(session))

        result = await prune_peak_assignment_runs(dry_run=True, batch_size=25)

        assert session.count_batches == [25, 25, 10]
        assert session.delete_batches == []
        assert result["status"] == "dry_run"
        assert result["prunable_runs"] == 60


class TestPruneGuards:
    """The prune refuses configurations that would delete more than intended."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"keep_per_sample": 0},
            {"keep_per_sample": -1},
            {"keep_failed_hours": -1},
            {"keep_running_hours": 0},
            {"keep_running_hours": MIN_KEEP_RUNNING_HOURS - 1},
            {"keep_importing_hours": 0},
            {"keep_importing_hours": MIN_KEEP_IMPORTING_HOURS - 1},
            {"batch_size": 0},
        ],
    )
    async def test_invalid_policy_is_rejected(self, kwargs):
        """keep_per_sample=0 would delete every run of every sample."""
        with pytest.raises(ValueError):
            await prune_peak_assignment_runs(**kwargs)

    def test_the_import_floor_is_shorter_than_the_in_flight_one(self):
        """Deliberate: the two protect against different losses.

        Deleting an in-flight run loses a ledger a worker is still computing.
        Deleting an assembling import loses staged rows the client still holds
        and can send again - and leaving it there blocks the sample - so the
        import floor can be much shorter without risking anything comparable.
        """
        assert MIN_KEEP_IMPORTING_HOURS < MIN_KEEP_RUNNING_HOURS
        assert MIN_KEEP_IMPORTING_HOURS >= 1
        assert DEFAULT_KEEP_IMPORTING_HOURS >= MIN_KEEP_IMPORTING_HOURS


class TestEnvOverrides:
    """The cron entry point's env overrides, which are how a prune is tuned."""

    def test_an_override_below_the_floor_names_the_variable(self, monkeypatch):
        """A cron job's failure message has to identify what an operator set.

        Letting the value through means dying deeper in on a message that names
        only a Python parameter, which is not a name the operator ever typed.
        """
        monkeypatch.setenv("MASCOPE_PRUNE_KEEP_RUNNING_HOURS", "1")

        with pytest.raises(ValueError, match="MASCOPE_PRUNE_KEEP_RUNNING_HOURS"):
            prune_script._int_env(
                "MASCOPE_PRUNE_KEEP_RUNNING_HOURS",
                DEFAULT_KEEP_RUNNING_HOURS,
                minimum=MIN_KEEP_RUNNING_HOURS,
            )

    def test_a_negative_override_is_rejected(self, monkeypatch):
        monkeypatch.setenv("MASCOPE_PRUNE_KEEP_PER_SAMPLE", "-3")

        with pytest.raises(ValueError, match="MASCOPE_PRUNE_KEEP_PER_SAMPLE"):
            prune_script._int_env("MASCOPE_PRUNE_KEEP_PER_SAMPLE", 3, minimum=1)

    def test_a_non_integer_falls_back_to_the_default(self, monkeypatch):
        """A typo has no discernible intent, so the documented policy wins."""
        monkeypatch.setenv("MASCOPE_PRUNE_KEEP_PER_SAMPLE", "three")

        assert prune_script._int_env("MASCOPE_PRUNE_KEEP_PER_SAMPLE", 3, minimum=1) == 3

    def test_an_unset_variable_uses_the_default(self, monkeypatch):
        monkeypatch.delenv("MASCOPE_PRUNE_KEEP_PER_SAMPLE", raising=False)

        assert prune_script._int_env("MASCOPE_PRUNE_KEEP_PER_SAMPLE", 3, minimum=1) == 3


def test_grace_defaults_hold_the_intended_order():
    """The in-flight grace must be the longer one, and above its own floor."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=DEFAULT_KEEP_FAILED_HOURS)
    assert cutoff < datetime.now(timezone.utc)
    assert DEFAULT_KEEP_FAILED_HOURS > 0
    assert DEFAULT_KEEP_RUNNING_HOURS >= MIN_KEEP_RUNNING_HOURS
    assert DEFAULT_KEEP_RUNNING_HOURS > DEFAULT_KEEP_FAILED_HOURS


class TestReaperLeavesImportsAlone:
    """The reaper's premise covers 'running' and does not extend to 'importing'.

    It fails every 'running' row at startup, correctly, because startup happens
    before any worker is spawned so nothing can legitimately be running. An
    import is not running - it is a client sending chunks at its own pace, with
    no server task attached - so a routine deploy under the broader reading
    would fail a live upload, and the client's next chunk would land on a
    'failed' run. The statement is executed for real here rather than compared
    as text, so widening the filter later fails this test.
    """

    @pytest.mark.asyncio
    async def test_only_running_rows_are_failed(self, run_table, monkeypatch):
        from mascope_backend.db.admin.peak_assignments import reset_running_runs

        _seed(
            run_table,
            [
                _run("computing", "running", created=_hours_ago(1)),
                _run("uploading", IMPORTING_STATUS, created=_hours_ago(1)),
                _run("queued", "pending", created=_hours_ago(1)),
                _run("done", "completed", created=_hours_ago(1)),
            ],
        )

        captured = []

        class _CapturingSession:
            async def execute(self, statement):
                captured.append(statement)
                with run_table.begin() as connection:
                    return connection.execute(statement)

            async def commit(self):
                return None

        monkeypatch.setattr(
            reset_running_runs,
            "async_session",
            _session_factory(_CapturingSession()),
        )

        await reset_running_runs.reset_running_peak_assignment_runs()

        assert len(captured) == 1
        with run_table.connect() as connection:
            statuses = dict(
                connection.execute(
                    sa.select(
                        PeakAssignmentRun.peak_assignment_run_id,
                        PeakAssignmentRun.status,
                    )
                ).all()
            )

        assert statuses["computing"] == "failed"
        assert statuses["uploading"] == IMPORTING_STATUS
        assert statuses["queued"] == "pending"
        assert statuses["done"] == "completed"


class TestReaperResilience:
    """The startup reaper must never be able to stop the server booting."""

    @pytest.mark.asyncio
    async def test_missing_table_is_swallowed(self, monkeypatch):
        """A database without the table (not yet migrated) must not break boot.

        The reaper runs in init_main_process alongside the other startup tasks,
        which share one try block - so a raise here aborts startup entirely.
        """
        from mascope_backend.db.admin.peak_assignments import reset_running_runs

        def _boom(*_args, **_kwargs):
            raise RuntimeError('relation "peak_assignment_run" does not exist')

        monkeypatch.setattr(reset_running_runs, "async_session", _boom)

        result = await reset_running_runs.reset_running_peak_assignment_runs()

        assert result["status"] == "skipped"
        assert result["data"]["reset_count"] == 0
