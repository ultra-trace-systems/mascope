"""Seeded test for the member fields and candidate registry added in `c4d2e8a1b7f3`.

Stairway and drift walk the chain against an empty database, so the three
backfills in this revision run over zero rows there and a green migrations
suite says nothing about them. The DDL is five nullable columns; the point of
the revision is what it puts IN them, and every clause of that is a way to get
it wrong. The seed below holds one anchor per way:

- `bp-m0`   - two samples agree on one identity: one registry entry, both
              members at index 0, each with its own P(correct) and role.
- `bp-iso`  - an isotopologue whose owner has an occurrence: the member names
              the owner's anchor.
- `bp-alt`  - two samples disagree: two registry entries in a deterministic
              order, each member at its own.
- `bp-bare` - an unassigned member: no registry, no index, its role kept.
- `bp-dead` - a member whose ledger row is already gone: its denormalized
              formula becomes an identity with no ion formula behind it.
- `bp-lost` - an isotopologue whose owner never folded: no owner anchor.

Rows are seeded at the previous revision through raw SQL rather than the ORM: a
migration test has to describe the schema as it was, not as the models are now.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic.command import downgrade, upgrade
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import Engine


# This checkout's migrations, not MASCOPE_PATH's - see conftest.BACKEND_PATH.
_ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"

REVISION = "c4d2e8a1b7f3"
_SCRIPT = ScriptDirectory.from_config(Config(str(_ALEMBIC_INI))).get_revision(REVISION)
PRIOR_REVISION = _SCRIPT.down_revision

_NEW_OCCURRENCE_COLUMNS = {"candidate", "role", "owner_batch_peak_id", "p_correct"}
_OWNER_INDEX = "ix_batch_peak_occurrence_owner_batch_peak_id"
_OWNER_FK = "fk_batch_peak_occurrence_owner_batch_peak_id_batch_peak"


# --- Seed data -------------------------------------------------------------

_WORKSPACE_ID = "ws-blm"
_DATASET_ID = "ds-blm"
_SAMPLE_BATCH_ID = "sb-blm"

# (sample_item_id, sample_file_id, run_id)
_SAMPLES = [
    ("si-blm-1", "sf-blm-1", "run-blm-1"),
    ("si-blm-2", "sf-blm-2", "run-blm-2"),
]

_GLUCOSE = ("C6H12O6", "C6H13O6+")

# (peak_assignment_id, run_id, sample_item_id, sample_peak_id, mz, intensity,
#  role, formula, ion_formula, owner_peak_assignment_id, provenance)
_ASSIGNMENTS = [
    (
        "pa-m0-1",
        "run-blm-1",
        "si-blm-1",
        "p1",
        181.0707,
        5000.0,
        "M0",
        *_GLUCOSE,
        None,
        '{"p_correct": 0.91}',
    ),
    (
        "pa-iso-1",
        "run-blm-1",
        "si-blm-1",
        "p2",
        182.0741,
        350.0,
        "iso_child",
        *_GLUCOSE,
        "pa-m0-1",
        '{"p_correct": 0.6}',
    ),
    # A JSON null is not a number: the member's P(correct) must stay NULL.
    (
        "pa-alt-1",
        "run-blm-1",
        "si-blm-1",
        "p3",
        300.1000,
        900.0,
        "M0",
        "C13H24O3",
        "C13H25O3+",
        None,
        '{"p_correct": null}',
    ),
    (
        "pa-bare-1",
        "run-blm-1",
        "si-blm-1",
        "p4",
        250.1000,
        300.0,
        "unassigned",
        None,
        None,
        None,
        None,
    ),
    # An owner that exists in the ledger but never folded (no occurrence).
    (
        "pa-lost-owner-1",
        "run-blm-1",
        "si-blm-1",
        "p5",
        499.0000,
        100.0,
        "M0",
        "C9H10O",
        "C9H11O+",
        None,
        None,
    ),
    (
        "pa-lost-1",
        "run-blm-1",
        "si-blm-1",
        "p6",
        500.0000,
        40.0,
        "iso_child",
        "C9H10O",
        "C9H11O+",
        "pa-lost-owner-1",
        None,
    ),
    (
        "pa-m0-2",
        "run-blm-2",
        "si-blm-2",
        "p1",
        181.0707,
        6000.0,
        "M0",
        *_GLUCOSE,
        None,
        '{"p_correct": 0.87}',
    ),
    # The same anchor as pa-alt-1, a different formula.
    (
        "pa-alt-2",
        "run-blm-2",
        "si-blm-2",
        "p3",
        300.1000,
        1200.0,
        "M0",
        "C12H18O5",
        "C12H19O5+",
        None,
        '{"evidence": 0.8}',
    ),
]

# (batch_peak_id, mz)
_BATCH_PEAKS = [
    ("bp-m0", 181.0707),
    ("bp-iso", 182.0741),
    ("bp-alt", 300.1000),
    ("bp-bare", 250.1000),
    ("bp-dead", 400.0000),
    ("bp-lost", 500.0000),
]

# (batch_peak_id, sample_item_id, peak_assignment_id, sample_peak_id,
#  intensity, tier, fit_score, assigned_formula)
_OCCURRENCES = [
    ("bp-m0", "si-blm-1", "pa-m0-1", "p1", 5000.0, "assigned", 0.95, "C6H12O6"),
    ("bp-m0", "si-blm-2", "pa-m0-2", "p1", 6000.0, "assigned", 0.92, "C6H12O6"),
    ("bp-iso", "si-blm-1", "pa-iso-1", "p2", 350.0, "assigned", 0.88, "C6H12O6"),
    ("bp-alt", "si-blm-1", "pa-alt-1", "p3", 900.0, "assigned", 0.80, "C13H24O3"),
    ("bp-alt", "si-blm-2", "pa-alt-2", "p3", 1200.0, "assigned", 0.86, "C12H18O5"),
    ("bp-bare", "si-blm-1", "pa-bare-1", "p4", 300.0, "unassigned", None, None),
    # Its ledger row is gone; the formula survives on the occurrence alone.
    ("bp-dead", "si-blm-2", None, "p9", 700.0, "assigned", 0.7, "C20H30O2"),
    ("bp-lost", "si-blm-1", "pa-lost-1", "p6", 40.0, "assigned", 0.5, "C9H10O"),
]


# --- Seed SQL --------------------------------------------------------------

_WORKSPACE_SQL = """
    INSERT INTO workspace (workspace_id, workspace_name)
    VALUES (:id, 'Batch Ledger Members')
"""

_DATASET_SQL = """
    INSERT INTO dataset (dataset_id, workspace_id, dataset_name)
    VALUES (:id, :workspace_id, 'Batch Ledger Members')
"""

_SAMPLE_FILE_SQL = """
    INSERT INTO sample_file (sample_file_id, filename, instrument, "datetime",
                             datetime_utc, length, "range", polarity)
    VALUES (:id, :filename, 'instrument-x', :local, :utc, 60.0,
            CAST('[100.0, 500.0]' AS json), '+')
"""

_SAMPLE_BATCH_SQL = """
    INSERT INTO sample_batch (sample_batch_id, dataset_id, sample_batch_name)
    VALUES (:id, :dataset_id, 'Batch Ledger Members')
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
                                 sample_peak_intensity, role, assigned_formula,
                                 ion_formula, tier, owner_peak_assignment_id,
                                 provenance)
    VALUES (:id, :run_id, :sample_item_id, :sample_peak_id, :mz, :intensity,
            :role, :formula, :ion_formula, :tier, :owner_id,
            CAST(:provenance AS json))
"""

_BATCH_PEAK_SQL = """
    INSERT INTO batch_peak (batch_peak_id, sample_batch_id, mz, mz_tol_ppm,
                            intensity_variable, consensus_tier)
    VALUES (:id, :sample_batch_id, :mz, 5.0, 'sum_peak_heights', 'assigned')
"""

# The occurrence as the prior revision shaped it: keyed by member, no surrogate.
_OCCURRENCE_SQL = """
    INSERT INTO batch_peak_occurrence (batch_peak_id, sample_item_id,
                                       peak_assignment_id, sample_peak_id,
                                       sample_peak_mz, intensity, tier,
                                       fit_score, assigned_formula)
    VALUES (:batch_peak_id, :sample_item_id, :peak_assignment_id,
            :sample_peak_id, :mz, :intensity, :tier, :fit, :formula)
"""


def _seed(engine: Engine) -> None:
    """Insert two folded samples covering every backfill case, at PRIOR_REVISION.

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
                    "formula": formula,
                    "ion_formula": ion_formula,
                    "tier": "unassigned" if role == "unassigned" else "assigned",
                    "owner_id": owner_id,
                    "provenance": provenance,
                }
                for (
                    assignment_id,
                    run_id,
                    sample_item_id,
                    sample_peak_id,
                    mz,
                    intensity,
                    role,
                    formula,
                    ion_formula,
                    owner_id,
                    provenance,
                ) in sorted(_ASSIGNMENTS, key=lambda row: row[9] is not None)
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
                    "batch_peak_id": batch_peak_id,
                    "sample_item_id": sample_item_id,
                    "peak_assignment_id": peak_assignment_id,
                    "sample_peak_id": sample_peak_id,
                    "mz": 100.0,
                    "intensity": intensity,
                    "tier": tier,
                    "fit": fit,
                    "formula": formula,
                }
                for (
                    batch_peak_id,
                    sample_item_id,
                    peak_assignment_id,
                    sample_peak_id,
                    intensity,
                    tier,
                    fit,
                    formula,
                ) in _OCCURRENCES
            ],
        )


# --- Introspection ---------------------------------------------------------


def _columns(conn, table: str) -> dict[str, str]:
    rows = conn.execute(
        text(
            "SELECT column_name, data_type FROM information_schema.columns"
            " WHERE table_name = :table"
        ),
        {"table": table},
    ).all()
    return {name: data_type for name, data_type in rows}


def _indexes(conn, table: str) -> dict[str, str]:
    rows = conn.execute(
        text("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = :table"),
        {"table": table},
    ).all()
    return {name: definition for name, definition in rows}


def _foreign_keys(conn, table: str) -> set[str]:
    rows = conn.execute(
        text(
            "SELECT conname FROM pg_constraint"
            " WHERE conrelid = CAST(:table AS regclass) AND contype = 'f'"
        ),
        {"table": table},
    ).all()
    return {name for (name,) in rows}


def _json(value):
    """A JSON column value as Python, whether the driver decoded it or not."""
    return json.loads(value) if isinstance(value, str) else value


def _registries(conn) -> dict[str, list | None]:
    rows = conn.execute(text("SELECT batch_peak_id, candidates FROM batch_peak")).all()
    return {batch_peak_id: _json(candidates) for batch_peak_id, candidates in rows}


def _members(conn) -> dict[tuple[str, str], dict]:
    rows = conn.execute(
        text(
            "SELECT batch_peak_id, sample_item_id, candidate, role,"
            " owner_batch_peak_id, p_correct FROM batch_peak_occurrence"
        )
    ).mappings()
    return {(r["batch_peak_id"], r["sample_item_id"]): dict(r) for r in rows}


def _identity(formula, ion_formula=None, mechanism=None) -> dict:
    return {
        "formula": formula,
        "ion_formula": ion_formula,
        "ionization_mechanism_id": mechanism,
    }


# --- Fixtures --------------------------------------------------------------


@pytest.fixture(scope="module")
def migrated(seeded_alembic_config: Config, seeded_engine: Engine) -> dict:
    """Seed at the prior revision, upgrade, and read what the backfills wrote.

    Everything is read into plain values, and the one probe that writes (the
    referential action) is done last, so the assertions below do not depend on
    the order pytest collects them in.

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
        snapshot["anchor_columns"] = _columns(conn, "batch_peak")
        snapshot["occurrence_columns"] = _columns(conn, "batch_peak_occurrence")
        snapshot["occurrence_indexes"] = _indexes(conn, "batch_peak_occurrence")
        snapshot["occurrence_foreign_keys"] = _foreign_keys(
            conn, "batch_peak_occurrence"
        )
        snapshot["registries"] = _registries(conn)
        snapshot["members"] = _members(conn)

    # The referential action, exercised for real: the owner anchor goes, and
    # the isotopologue member that named it is left naming nothing.
    with seeded_engine.begin() as conn:
        conn.execute(text("DELETE FROM batch_peak WHERE batch_peak_id = 'bp-m0'"))
        snapshot["orphaned_owner"] = conn.execute(
            text(
                "SELECT owner_batch_peak_id FROM batch_peak_occurrence"
                " WHERE batch_peak_id = 'bp-iso'"
            )
        ).scalar()
    return snapshot


@pytest.fixture(scope="module")
def downgraded(
    seeded_alembic_config: Config, seeded_engine: Engine, migrated: dict
) -> dict:
    """Step back to the prior revision and read both tables' shapes again.

    Depends on `migrated` so the upgrade is always the thing being undone.

    :return: What the downgrade left: columns, indexes, foreign keys.
    :rtype: dict
    """
    downgrade(seeded_alembic_config, PRIOR_REVISION)
    with seeded_engine.connect() as conn:
        return {
            "anchor_columns": _columns(conn, "batch_peak"),
            "occurrence_columns": _columns(conn, "batch_peak_occurrence"),
            "occurrence_indexes": _indexes(conn, "batch_peak_occurrence"),
            "occurrence_foreign_keys": _foreign_keys(conn, "batch_peak_occurrence"),
        }


# --- Upgrade: shape --------------------------------------------------------


def test_upgrade_reaches_the_revision(migrated: dict) -> None:
    assert migrated["version"] == REVISION


def test_the_new_columns_exist_with_their_types(migrated: dict) -> None:
    assert migrated["anchor_columns"]["candidates"] == "json"
    occurrence = migrated["occurrence_columns"]
    assert occurrence["candidate"] == "smallint"
    assert occurrence["role"] == "character varying"
    assert occurrence["owner_batch_peak_id"] == "character varying"
    assert occurrence["p_correct"] == "double precision"


def test_the_owner_link_is_a_partial_index_and_a_set_null_foreign_key(
    migrated: dict,
) -> None:
    definition = migrated["occurrence_indexes"][_OWNER_INDEX]
    assert "WHERE (owner_batch_peak_id IS NOT NULL)" in definition
    assert _OWNER_FK in migrated["occurrence_foreign_keys"]
    # SET NULL, exercised: bp-m0 was deleted after the snapshot was read.
    assert migrated["orphaned_owner"] is None


# --- Upgrade: the backfill -------------------------------------------------


def test_members_that_agree_share_one_registry_entry(migrated: dict) -> None:
    assert migrated["registries"]["bp-m0"] == [_identity(*_GLUCOSE)]
    first = migrated["members"][("bp-m0", "si-blm-1")]
    second = migrated["members"][("bp-m0", "si-blm-2")]
    assert (first["candidate"], second["candidate"]) == (0, 0)
    assert (first["role"], second["role"]) == ("M0", "M0")
    assert (first["p_correct"], second["p_correct"]) == (0.91, 0.87)
    assert (first["owner_batch_peak_id"], second["owner_batch_peak_id"]) == (
        None,
        None,
    )


def test_an_isotopologue_names_the_anchor_its_owner_folded_into(
    migrated: dict,
) -> None:
    member = migrated["members"][("bp-iso", "si-blm-1")]
    assert member["role"] == "iso_child"
    assert member["owner_batch_peak_id"] == "bp-m0"
    assert member["candidate"] == 0
    assert migrated["registries"]["bp-iso"] == [_identity(*_GLUCOSE)]


def test_members_that_disagree_get_their_own_entries_in_a_stable_order(
    migrated: dict,
) -> None:
    # Ordered by formula, so the later sample's identity comes first here.
    assert migrated["registries"]["bp-alt"] == [
        _identity("C12H18O5", "C12H19O5+"),
        _identity("C13H24O3", "C13H25O3+"),
    ]
    assert migrated["members"][("bp-alt", "si-blm-1")]["candidate"] == 1
    assert migrated["members"][("bp-alt", "si-blm-2")]["candidate"] == 0


def test_a_json_null_probability_stays_null(migrated: dict) -> None:
    assert migrated["members"][("bp-alt", "si-blm-1")]["p_correct"] is None
    # ... and so does a provenance with no such key.
    assert migrated["members"][("bp-alt", "si-blm-2")]["p_correct"] is None


def test_an_unassigned_member_has_no_registry_and_no_index(migrated: dict) -> None:
    assert migrated["registries"]["bp-bare"] is None
    member = migrated["members"][("bp-bare", "si-blm-1")]
    assert member["candidate"] is None
    assert member["role"] == "unassigned"
    assert member["p_correct"] is None


def test_a_member_whose_ledger_row_is_gone_keeps_its_formula_and_no_more(
    migrated: dict,
) -> None:
    """Its formula survived on the occurrence; the ion formula and mechanism
    did not, and the registry says so rather than borrowing them."""
    assert migrated["registries"]["bp-dead"] == [_identity("C20H30O2")]
    member = migrated["members"][("bp-dead", "si-blm-2")]
    assert member["candidate"] == 0
    assert member["role"] is None
    assert member["p_correct"] is None


def test_an_isotopologue_whose_owner_never_folded_names_no_anchor(
    migrated: dict,
) -> None:
    member = migrated["members"][("bp-lost", "si-blm-1")]
    assert member["role"] == "iso_child"
    assert member["owner_batch_peak_id"] is None


# --- Downgrade -------------------------------------------------------------


def test_downgrade_drops_the_columns_the_index_and_the_foreign_key(
    downgraded: dict,
) -> None:
    assert "candidates" not in downgraded["anchor_columns"]
    assert not (_NEW_OCCURRENCE_COLUMNS & set(downgraded["occurrence_columns"]))
    assert _OWNER_INDEX not in downgraded["occurrence_indexes"]
    assert _OWNER_FK not in downgraded["occurrence_foreign_keys"]
