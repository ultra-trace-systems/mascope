"""Seeded test for the index and key changes in `97c42c48e011`.

The stairway proves the DDL applies and reverts on an empty database. What it
cannot say is whether the revision keeps its promises over rows: that every
occurrence survives losing its surrogate id and comes back with a fresh one on
the way down, that the (batch peak, sample) key still refuses a duplicate
member, and - the one that matters for correctness rather than size - that the
foreign-key actions and lookups which used to ride full indexes still work
over the partial ones and without the two that were dropped.

Rows are seeded at the previous revision through raw SQL rather than the ORM: a
migration test has to describe the schema as it was, not as the models are now.
"""

from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic.command import downgrade, upgrade
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError


# This checkout's migrations, not MASCOPE_PATH's - see conftest.BACKEND_PATH.
_ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"

REVISION = "97c42c48e011"
_SCRIPT = ScriptDirectory.from_config(Config(str(_ALEMBIC_INI))).get_revision(REVISION)
PRIOR_REVISION = _SCRIPT.down_revision

_DROPPED_INDEXES = {
    "ix_peak_assignment_peak_assignment_run_id",
    "ix_peak_assignment_sample_peak_id",
}
_PARTIAL_INDEXES = {
    "ix_peak_assignment_ionization_mechanism_id": "ionization_mechanism_id",
    "ix_peak_assignment_target_compound_id": "target_compound_id",
    "ix_peak_assignment_target_ion_id": "target_ion_id",
    "ix_peak_assignment_owner_peak_assignment_id": "owner_peak_assignment_id",
}


# --- Seed data -------------------------------------------------------------

_WORKSPACE_ID = "ws-sai"
_DATASET_ID = "ds-sai"
_SAMPLE_BATCH_ID = "sb-sai"

# (sample_item_id, sample_file_id, run_id)
_SAMPLES = [
    ("si-sai-1", "sf-sai-1", "run-sai-1"),
    ("si-sai-2", "sf-sai-2", "run-sai-2"),
]

# (peak_assignment_id, run_id, sample_item_id, sample_peak_id, mz, intensity,
#  role, owner_peak_assignment_id)
_ASSIGNMENTS = [
    ("pa-m0-1", "run-sai-1", "si-sai-1", "p1", 181.0707, 5000.0, "M0", None),
    (
        "pa-iso-1",
        "run-sai-1",
        "si-sai-1",
        "p2",
        182.0741,
        350.0,
        "iso_child",
        "pa-m0-1",
    ),
    ("pa-bare-1", "run-sai-1", "si-sai-1", "p3", 250.1000, 300.0, "unassigned", None),
    ("pa-m0-2", "run-sai-2", "si-sai-2", "p1", 181.0707, 6000.0, "M0", None),
]

# (batch_peak_id, mz)
_BATCH_PEAKS = [("bp-m0", 181.0707), ("bp-iso", 182.0741), ("bp-bare", 250.1000)]

# (batch_peak_occurrence_id, batch_peak_id, sample_item_id, peak_assignment_id,
#  sample_peak_id, intensity)
_OCCURRENCES = [
    ("occ-m0-1", "bp-m0", "si-sai-1", "pa-m0-1", "p1", 5000.0),
    ("occ-m0-2", "bp-m0", "si-sai-2", "pa-m0-2", "p1", 6000.0),
    ("occ-iso-1", "bp-iso", "si-sai-1", "pa-iso-1", "p2", 350.0),
    ("occ-bare-1", "bp-bare", "si-sai-1", "pa-bare-1", "p3", 300.0),
]
_MEMBERS = {(bp, si, pa) for _, bp, si, pa, _, _ in _OCCURRENCES}


# --- Seed SQL --------------------------------------------------------------

_WORKSPACE_SQL = """
    INSERT INTO workspace (workspace_id, workspace_name)
    VALUES (:id, 'Slim Assignment Indexes')
"""

_DATASET_SQL = """
    INSERT INTO dataset (dataset_id, workspace_id, dataset_name)
    VALUES (:id, :workspace_id, 'Slim Assignment Indexes')
"""

_SAMPLE_FILE_SQL = """
    INSERT INTO sample_file (sample_file_id, filename, instrument, "datetime",
                             datetime_utc, length, "range", polarity)
    VALUES (:id, :filename, 'instrument-x', :local, :utc, 60.0,
            CAST('[100.0, 500.0]' AS json), '+')
"""

_SAMPLE_BATCH_SQL = """
    INSERT INTO sample_batch (sample_batch_id, dataset_id, sample_batch_name)
    VALUES (:id, :dataset_id, 'Slim Assignment Indexes')
"""

_SAMPLE_ITEM_SQL = """
    INSERT INTO sample_item (sample_item_id, sample_batch_id, sample_file_id,
                             sample_item_name, sample_item_type)
    VALUES (:id, :sample_batch_id, :sample_file_id, :id, 'sample')
"""

_RUN_SQL = """
    INSERT INTO peak_assignment_run (peak_assignment_run_id, sample_item_id,
                                     engine, engine_version, status)
    VALUES (:id, :sample_item_id, 'mascope', '0.3.0', 'completed')
"""

_ASSIGNMENT_SQL = """
    INSERT INTO peak_assignment (peak_assignment_id, peak_assignment_run_id,
                                 sample_item_id, sample_peak_id, sample_peak_mz,
                                 sample_peak_intensity, role, tier,
                                 owner_peak_assignment_id)
    VALUES (:id, :run_id, :sample_item_id, :sample_peak_id, :mz, :intensity,
            :role, :tier, :owner_id)
"""

_BATCH_PEAK_SQL = """
    INSERT INTO batch_peak (batch_peak_id, sample_batch_id, mz, mz_tol_ppm,
                            intensity_variable, consensus_tier)
    VALUES (:id, :sample_batch_id, :mz, 5.0, 'sum_peak_heights', 'assigned')
"""

# The occurrence as the prior revision shaped it, surrogate id and all.
_OCCURRENCE_SQL = """
    INSERT INTO batch_peak_occurrence (batch_peak_occurrence_id, batch_peak_id,
                                       sample_item_id, peak_assignment_id,
                                       sample_peak_id, sample_peak_mz, intensity)
    VALUES (:id, :batch_peak_id, :sample_item_id, :peak_assignment_id,
            :sample_peak_id, :mz, :intensity)
"""

# The occurrence as this revision shapes it: no surrogate to supply.
_DUPLICATE_MEMBER_SQL = """
    INSERT INTO batch_peak_occurrence (batch_peak_id, sample_item_id,
                                       peak_assignment_id, sample_peak_id,
                                       sample_peak_mz, intensity)
    VALUES ('bp-m0', 'si-sai-1', NULL, 'p9', 181.0, 1.0)
"""


def _seed(engine: Engine) -> None:
    """Insert two folded samples with a family link, at PRIOR_REVISION.

    :param engine: Engine on the seeded test database.
    :type engine: Engine
    """
    local = datetime(2026, 9, 1, 12, 0)
    utc = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    with engine.begin() as conn:
        conn.execute(text(_WORKSPACE_SQL), {"id": _WORKSPACE_ID})
        conn.execute(
            text(_DATASET_SQL), {"id": _DATASET_ID, "workspace_id": _WORKSPACE_ID}
        )
        conn.execute(
            text(_SAMPLE_BATCH_SQL),
            {"id": _SAMPLE_BATCH_ID, "dataset_id": _DATASET_ID},
        )
        for sample_item_id, sample_file_id, run_id in _SAMPLES:
            conn.execute(
                text(_SAMPLE_FILE_SQL),
                {
                    "id": sample_file_id,
                    "filename": f"{sample_file_id}.raw",
                    "local": local,
                    "utc": utc,
                },
            )
            conn.execute(
                text(_SAMPLE_ITEM_SQL),
                {
                    "id": sample_item_id,
                    "sample_batch_id": _SAMPLE_BATCH_ID,
                    "sample_file_id": sample_file_id,
                },
            )
            conn.execute(
                text(_RUN_SQL), {"id": run_id, "sample_item_id": sample_item_id}
            )
        # Owners before the rows that name them: the FK is self-referential.
        conn.execute(
            text(_ASSIGNMENT_SQL),
            [
                {
                    "id": assignment_id,
                    "run_id": run_id,
                    "sample_item_id": sample_item_id,
                    "sample_peak_id": sample_peak_id,
                    "mz": mz,
                    "intensity": intensity,
                    "role": role,
                    "tier": "unassigned" if role == "unassigned" else "assigned",
                    "owner_id": owner_id,
                }
                for (
                    assignment_id,
                    run_id,
                    sample_item_id,
                    sample_peak_id,
                    mz,
                    intensity,
                    role,
                    owner_id,
                ) in sorted(_ASSIGNMENTS, key=lambda row: row[7] is not None)
            ],
        )
        conn.execute(
            text(_BATCH_PEAK_SQL),
            [
                {"id": batch_peak_id, "sample_batch_id": _SAMPLE_BATCH_ID, "mz": mz}
                for batch_peak_id, mz in _BATCH_PEAKS
            ],
        )
        conn.execute(
            text(_OCCURRENCE_SQL),
            [
                {
                    "id": occurrence_id,
                    "batch_peak_id": batch_peak_id,
                    "sample_item_id": sample_item_id,
                    "peak_assignment_id": peak_assignment_id,
                    "sample_peak_id": sample_peak_id,
                    "mz": 100.0,
                    "intensity": intensity,
                }
                for (
                    occurrence_id,
                    batch_peak_id,
                    sample_item_id,
                    peak_assignment_id,
                    sample_peak_id,
                    intensity,
                ) in _OCCURRENCES
            ],
        )


# --- Introspection ---------------------------------------------------------


def _columns(conn, table: str) -> dict[str, str]:
    """Column name -> is_nullable for ``table``."""
    rows = conn.execute(
        text(
            "SELECT column_name, is_nullable FROM information_schema.columns"
            " WHERE table_name = :table"
        ),
        {"table": table},
    ).all()
    return {row.column_name: row.is_nullable for row in rows}


def _indexes(conn, table: str) -> dict[str, str]:
    """Index name -> definition for ``table``."""
    rows = conn.execute(
        text("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = :table"),
        {"table": table},
    ).all()
    return {row.indexname: row.indexdef for row in rows}


def _reloptions(conn, table: str) -> list[str]:
    value = conn.execute(
        text("SELECT reloptions FROM pg_class WHERE relname = :table"),
        {"table": table},
    ).scalar()
    return list(value or [])


def _members(conn) -> set[tuple]:
    rows = conn.execute(
        text(
            "SELECT batch_peak_id, sample_item_id, peak_assignment_id"
            " FROM batch_peak_occurrence"
        )
    ).all()
    return {
        (row.batch_peak_id, row.sample_item_id, row.peak_assignment_id) for row in rows
    }


def _plan(conn, sql: str) -> str:
    return "\n".join(row[0] for row in conn.execute(text(f"EXPLAIN {sql}")).all())


# --- Fixtures --------------------------------------------------------------


@pytest.fixture(scope="module")
def migrated(seeded_alembic_config: Config, seeded_engine: Engine) -> dict:
    """Seed at the prior revision, upgrade, and probe what the revision changed.

    Everything is read into plain values (and the probes that write are done
    last, on rows the earlier reads no longer need), so the assertions below do
    not depend on the order pytest collects them in.

    :return: The snapshot as the upgrade left it.
    :rtype: dict
    """
    upgrade(seeded_alembic_config, PRIOR_REVISION)
    _seed(seeded_engine)
    upgrade(seeded_alembic_config, REVISION)

    snapshot: dict = {}
    with seeded_engine.connect() as conn:
        snapshot["version"] = conn.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar()
        snapshot["members"] = _members(conn)
        snapshot["occurrence_columns"] = _columns(conn, "batch_peak_occurrence")
        snapshot["occurrence_indexes"] = _indexes(conn, "batch_peak_occurrence")
        snapshot["ledger_indexes"] = _indexes(conn, "peak_assignment")
        snapshot["reloptions"] = {
            table: _reloptions(conn, table)
            for table in ("batch_peak", "batch_peak_occurrence", "peak_assignment")
        }
        # With sequential scans priced out, the planner reaches for an index if
        # one applies - which is what the SET NULL action and the family lookup
        # need from a partial index: a strict `= $1` implies `IS NOT NULL`.
        conn.execute(text("SET enable_seqscan = off"))
        snapshot["owner_lookup_plan"] = _plan(
            conn,
            "SELECT peak_assignment_id FROM peak_assignment"
            " WHERE owner_peak_assignment_id = 'pa-m0-1'",
        )
        snapshot["run_lookup_plan"] = _plan(
            conn,
            "SELECT peak_assignment_id FROM peak_assignment"
            " WHERE peak_assignment_run_id = 'run-sai-1'",
        )
        conn.execute(text("RESET enable_seqscan"))

    # A second member of one anchor from one sample is what the old unique
    # constraint refused; the new key has to refuse it the same way.
    with seeded_engine.connect() as conn:
        try:
            with conn.begin():
                conn.execute(text(_DUPLICATE_MEMBER_SQL))
            snapshot["duplicate_refused"] = False
        except IntegrityError:
            snapshot["duplicate_refused"] = True

    # The referential actions, exercised for real: SET NULL through the partial
    # owner index, and the run-delete cascade without its dropped index.
    with seeded_engine.begin() as conn:
        conn.execute(
            text("DELETE FROM peak_assignment WHERE peak_assignment_id = 'pa-m0-1'")
        )
        snapshot["orphaned_owner"] = conn.execute(
            text(
                "SELECT owner_peak_assignment_id FROM peak_assignment"
                " WHERE peak_assignment_id = 'pa-iso-1'"
            )
        ).scalar()
        conn.execute(
            text(
                "DELETE FROM peak_assignment_run"
                " WHERE peak_assignment_run_id = 'run-sai-2'"
            )
        )
        snapshot["rows_left_of_run_2"] = conn.execute(
            text(
                "SELECT count(*) FROM peak_assignment"
                " WHERE peak_assignment_run_id = 'run-sai-2'"
            )
        ).scalar()
        snapshot["members_after_prune"] = _members(conn)
    return snapshot


@pytest.fixture(scope="module")
def downgraded(
    seeded_alembic_config: Config, seeded_engine: Engine, migrated: dict
) -> dict:
    """Step back to the prior revision and read the occurrence table again.

    Depends on `migrated` so the upgrade is always the thing being undone.

    :return: What the downgrade left: columns, indexes, ids, storage options.
    :rtype: dict
    """
    downgrade(seeded_alembic_config, PRIOR_REVISION)
    with seeded_engine.connect() as conn:
        ids = (
            conn.execute(
                text("SELECT batch_peak_occurrence_id FROM batch_peak_occurrence")
            )
            .scalars()
            .all()
        )
        return {
            "occurrence_columns": _columns(conn, "batch_peak_occurrence"),
            "occurrence_indexes": _indexes(conn, "batch_peak_occurrence"),
            "ledger_indexes": _indexes(conn, "peak_assignment"),
            "ids": ids,
            "reloptions": {
                table: _reloptions(conn, table)
                for table in ("batch_peak", "batch_peak_occurrence", "peak_assignment")
            },
        }


# --- Upgrade ---------------------------------------------------------------


def test_upgrade_reaches_the_revision(migrated: dict) -> None:
    assert migrated["version"] == REVISION


def test_every_member_survives_losing_its_surrogate(migrated: dict) -> None:
    """The key changed; the rows did not."""
    assert migrated["members"] == _MEMBERS
    assert "batch_peak_occurrence_id" not in migrated["occurrence_columns"]


def test_the_member_identity_is_the_primary_key(migrated: dict) -> None:
    pk = migrated["occurrence_indexes"]["pk_batch_peak_occurrence"]
    assert "(batch_peak_id, sample_item_id)" in pk
    # The unique constraint and the prefix index it made redundant are gone.
    assert (
        "uq_batch_peak_occurrence_batch_peak_id_sample_item_id"
        not in migrated["occurrence_indexes"]
    )
    assert (
        "ix_batch_peak_occurrence_batch_peak_id" not in migrated["occurrence_indexes"]
    )
    # The two lookups that are not prefixes of the key keep their indexes.
    assert "ix_batch_peak_occurrence_sample_item_id" in migrated["occurrence_indexes"]
    assert (
        "ix_batch_peak_occurrence_peak_assignment_id" in migrated["occurrence_indexes"]
    )


def test_a_second_member_per_sample_is_still_refused(migrated: dict) -> None:
    """One y-value per trace per sample: the invariant the old unique
    constraint held is now the primary key's to hold."""
    assert migrated["duplicate_refused"] is True


def test_the_redundant_ledger_indexes_are_gone(migrated: dict) -> None:
    assert not _DROPPED_INDEXES & set(migrated["ledger_indexes"])
    # What replaces the run-id index: the unique constraint that leads with it.
    assert "uq_peak_assignment_run_id_sample_peak_id" in migrated["ledger_indexes"]
    assert "ix_peak_assignment_sample_item_id" in migrated["ledger_indexes"]


def test_the_nullable_references_are_indexed_only_where_set(migrated: dict) -> None:
    for name, column in _PARTIAL_INDEXES.items():
        definition = migrated["ledger_indexes"][name]
        assert f"WHERE ({column} IS NOT NULL)" in definition, definition


def test_a_lookup_by_owner_uses_the_partial_index(migrated: dict) -> None:
    """The SET NULL action on the self-reference asks exactly this question."""
    assert (
        "ix_peak_assignment_owner_peak_assignment_id" in migrated["owner_lookup_plan"]
    )


def test_a_lookup_by_run_rides_the_unique_constraint(migrated: dict) -> None:
    """The run-delete cascade and the ledger read ask this one."""
    assert "uq_peak_assignment_run_id_sample_peak_id" in migrated["run_lookup_plan"]


def test_deleting_an_owner_still_orphans_its_isotopologue(migrated: dict) -> None:
    """ON DELETE SET NULL, through the partial index, over real rows."""
    assert migrated["orphaned_owner"] is None


def test_deleting_a_run_still_cascades_to_its_ledger(migrated: dict) -> None:
    """The cascade used to ride the dropped run-id index."""
    assert migrated["rows_left_of_run_2"] == 0
    # The pruned run's occurrence stays, with its assignment link cleared -
    # the documented retention behaviour, unchanged by the new key.
    assert ("bp-m0", "si-sai-2", None) in migrated["members_after_prune"]


def test_the_churn_tables_carry_their_storage_parameters(migrated: dict) -> None:
    options = migrated["reloptions"]
    assert "fillfactor=70" in options["batch_peak"]
    for table in ("batch_peak", "batch_peak_occurrence", "peak_assignment"):
        assert "autovacuum_vacuum_scale_factor=0.01" in options[table], table
        assert any(o.startswith("autovacuum_vacuum_threshold=") for o in options[table])


# --- Downgrade -------------------------------------------------------------


def test_downgrade_restores_a_unique_surrogate_on_every_row(downgraded: dict) -> None:
    columns = downgraded["occurrence_columns"]
    assert columns["batch_peak_occurrence_id"] == "NO"  # NOT NULL again
    ids = downgraded["ids"]
    assert len(ids) == len(set(ids)) and all(len(i) == 32 for i in ids)
    assert (
        "(batch_peak_occurrence_id)"
        in downgraded["occurrence_indexes"]["pk_batch_peak_occurrence"]
    )
    assert (
        "uq_batch_peak_occurrence_batch_peak_id_sample_item_id"
        in downgraded["occurrence_indexes"]
    )
    assert "ix_batch_peak_occurrence_batch_peak_id" in downgraded["occurrence_indexes"]


def test_downgrade_restores_the_full_ledger_indexes(downgraded: dict) -> None:
    indexes = downgraded["ledger_indexes"]
    assert _DROPPED_INDEXES <= set(indexes)
    for name in _PARTIAL_INDEXES:
        assert "WHERE" not in indexes[name], indexes[name]


def test_downgrade_resets_the_storage_parameters(downgraded: dict) -> None:
    assert all(options == [] for options in downgraded["reloptions"].values())
