"""Seeded test for the FAIR Phase 1 schema in `206bf313dee6`.

The migration only adds: five metadata columns on ``dataset``, three identity
columns on ``target_compound`` and the ``dataset_tombstone`` table. What can
go wrong is what the drift test does not see:

- a backfill, or a default that lands on existing rows - a dataset that had
  no licence must still have none, a compound no identity;
- a constraint name the naming convention prefixed twice: the drift test
  compares the migration with the models, so a name doubled the same way in
  both passes it. The names here are read off the live catalogue;
- a foreign key on the tombstone, which would take its rows down with the
  dataset or workspace they describe.

Rows are inserted through raw SQL rather than the ORM: at PRIOR_REVISION the
columns do not exist.
"""

from datetime import datetime, timezone
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.command import downgrade, upgrade
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import Engine


# This checkout's migrations, not MASCOPE_PATH's - see conftest.BACKEND_PATH.
_ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"

REVISION = "206bf313dee6"
_SCRIPT = ScriptDirectory.from_config(Config(str(_ALEMBIC_INI))).get_revision(REVISION)
PRIOR_REVISION = _SCRIPT.down_revision

_DATASET_COLUMNS = ("license", "authors", "keywords", "related_identifiers", "funding")
_COMPOUND_COLUMNS = ("inchikey", "inchi", "smiles")

_NOW = datetime(2026, 10, 16, 9, 0, tzinfo=timezone.utc)


def _seed(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO workspace (workspace_id, workspace_name,"
                " workspace_utc_created) VALUES ('ws-fair', 'Workspace', :now)"
            ),
            {"now": _NOW},
        )
        conn.execute(
            text(
                "INSERT INTO dataset (dataset_id, workspace_id, dataset_name,"
                " dataset_type) VALUES ('ds-fair', 'ws-fair', 'Before', 'ANALYSIS')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO target_compound (target_compound_id,"
                " target_compound_name, target_compound_formula, cas_number)"
                " VALUES ('tc-fair', 'Caffeine', 'C8H10N4O2', '58-08-2')"
            )
        )


def _row(engine: Engine, table: str, key: str, value: str) -> dict:
    with engine.connect() as conn:
        return dict(
            conn.execute(text(f"SELECT * FROM {table} WHERE {key} = :v"), {"v": value})
            .mappings()
            .one()
        )


def _tombstone(conn, dataset_id: str, deleted_via: str) -> None:
    conn.execute(
        text(
            "INSERT INTO dataset_tombstone (dataset_id, workspace_id,"
            " dataset_name, deleted_utc, deleted_via)"
            " VALUES (:id, 'ws-fair', 'Gone', :now, :via)"
        ),
        {"id": dataset_id, "now": _NOW, "via": deleted_via},
    )


@pytest.fixture(scope="module")
def upgraded(seeded_alembic_config: Config, seeded_engine: Engine) -> Engine:
    """A dataset and a compound from before the migration, then the migration."""
    upgrade(seeded_alembic_config, PRIOR_REVISION)
    _seed(seeded_engine)
    upgrade(seeded_alembic_config, REVISION)
    return seeded_engine


def test_an_existing_dataset_has_no_metadata(upgraded: Engine):
    row = _row(upgraded, "dataset", "dataset_id", "ds-fair")
    assert {name: row[name] for name in _DATASET_COLUMNS} == dict.fromkeys(
        _DATASET_COLUMNS
    )


def test_an_existing_compound_has_no_identity(upgraded: Engine):
    row = _row(upgraded, "target_compound", "target_compound_id", "tc-fair")
    assert {name: row[name] for name in _COMPOUND_COLUMNS} == dict.fromkeys(
        _COMPOUND_COLUMNS
    )
    assert row["cas_number"] == "58-08-2"


def test_the_new_columns_are_nullable_without_defaults(upgraded: Engine):
    inspector = sa.inspect(upgraded)
    for table, names in (
        ("dataset", _DATASET_COLUMNS),
        ("target_compound", _COMPOUND_COLUMNS),
    ):
        columns = {c["name"]: c for c in inspector.get_columns(table)}
        for name in names:
            assert columns[name]["nullable"], (table, name)
            assert columns[name]["default"] is None, (table, name)


def test_metadata_and_identity_round_trip(upgraded: Engine):
    with upgraded.begin() as conn:
        conn.execute(
            text(
                "UPDATE dataset SET license = 'CC-BY-4.0',"
                " authors = CAST(:authors AS json) WHERE dataset_id = 'ds-fair'"
            ),
            {"authors": '[{"name": "A. Author", "orcid": null}]'},
        )
        conn.execute(
            text(
                "UPDATE target_compound SET inchikey = :key"
                " WHERE target_compound_id = 'tc-fair'"
            ),
            {"key": "RYYVLZVUVIJVGH-UHFFFAOYSA-N"},
        )
    dataset = _row(upgraded, "dataset", "dataset_id", "ds-fair")
    assert dataset["license"] == "CC-BY-4.0"
    assert dataset["authors"] == [{"name": "A. Author", "orcid": None}]
    compound = _row(
        upgraded, "target_compound", "inchikey", "RYYVLZVUVIJVGH-UHFFFAOYSA-N"
    )
    assert compound["target_compound_id"] == "tc-fair"


def test_constraint_and_index_names_are_prefixed_once(upgraded: Engine):
    with upgraded.connect() as conn:
        constraints = dict(
            conn.execute(
                text(
                    "SELECT conname, contype FROM pg_constraint"
                    " WHERE conrelid = 'dataset_tombstone'::regclass"
                )
            ).all()
        )
        indexes = set(
            conn.execute(
                text(
                    "SELECT indexname FROM pg_indexes"
                    " WHERE tablename = 'target_compound'"
                    " AND indexdef LIKE '%(inchikey)%'"
                )
            ).scalars()
        )
    assert constraints == {
        "pk_dataset_tombstone": "p",
        "ck_dataset_tombstone_deleted_via_known": "c",
    }
    assert indexes == {"ix_target_compound_inchikey"}


def test_a_tombstone_outlives_its_dataset_and_workspace(upgraded: Engine):
    with upgraded.begin() as conn:
        _tombstone(conn, "ds-fair", "workspace")
        conn.execute(text("DELETE FROM workspace WHERE workspace_id = 'ws-fair'"))
    with upgraded.connect() as conn:
        dataset_left = conn.execute(
            text("SELECT count(*) FROM dataset WHERE dataset_id = 'ds-fair'")
        ).scalar_one()
    assert dataset_left == 0
    tombstone = _row(upgraded, "dataset_tombstone", "dataset_id", "ds-fair")
    assert tombstone["workspace_id"] == "ws-fair"
    assert tombstone["deleted_via"] == "workspace"


@pytest.mark.parametrize("deleted_via", ["dataset", "workspace"])
def test_a_tombstone_names_a_known_delete_path(upgraded: Engine, deleted_via: str):
    with upgraded.begin() as conn:
        _tombstone(conn, f"ds-{deleted_via}", deleted_via)


def test_a_tombstone_refuses_an_unknown_delete_path(upgraded: Engine):
    with pytest.raises(sa.exc.IntegrityError, match="deleted_via_known"):
        with upgraded.begin() as conn:
            _tombstone(conn, "ds-other", "trigger")


def test_the_downgrade_removes_everything(
    upgraded: Engine, seeded_alembic_config: Config
):
    # Runs last: it leaves the database at PRIOR_REVISION.
    downgrade(seeded_alembic_config, PRIOR_REVISION)
    inspector = sa.inspect(upgraded)
    assert "dataset_tombstone" not in inspector.get_table_names()
    dataset = {c["name"] for c in inspector.get_columns("dataset")}
    compound = {c["name"] for c in inspector.get_columns("target_compound")}
    assert dataset.isdisjoint(_DATASET_COLUMNS)
    assert compound.isdisjoint(_COMPOUND_COLUMNS)
    assert "ix_target_compound_inchikey" not in {
        i["name"] for i in inspector.get_indexes("target_compound")
    }
