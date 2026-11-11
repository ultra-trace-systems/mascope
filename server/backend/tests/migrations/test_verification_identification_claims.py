"""Seeded test for the identification-claim columns in `f7b795c6335b`.

Stairway and drift run the chain against an empty database, where the backfill
matches nothing and every CHECK is created over an empty table. Here both
verification tables hold verdicts recorded before the columns: a confirmation
on each evidence level, a rejection, an unsure verdict and a superseded one.
After the upgrade a confirmation reads as level 4 with no structure and keeps
its evidence level, which the request layer would now refuse without one; a
rejection and an unsure verdict state no level; nothing else moves, so the
calibration pool selects the same labels.

Rows are seeded through raw SQL rather than the ORM: a migration test has to
describe the schema as it was at PRIOR_REVISION.
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

REVISION = "f7b795c6335b"
_SCRIPT = ScriptDirectory.from_config(Config(str(_ALEMBIC_INI))).get_revision(REVISION)
PRIOR_REVISION = _SCRIPT.down_revision

_TIME = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)

# id -> (verdict, evidence_level, superseded)
_VERDICTS = {
    "v-standard": ("confirmed", "reference_standard", False),
    "v-msms": ("confirmed", "msms", False),
    "v-orthogonal": ("confirmed", "orthogonal", False),
    "v-pattern": ("confirmed", "pattern", False),
    "v-visual": ("confirmed", "visual", False),
    "v-superseded": ("confirmed", "msms", True),
    "v-rejected": ("rejected", "msms", False),
    "v-unsure": ("unsure", None, False),
}

#: table -> what the calibration pool and every other reader selects on.
_KEPT = {
    "assignment_verification": (
        "verdict, evidence_level, fit_score, evidence, p_correct, superseded_utc"
    ),
    "batch_peak_verification": "verdict, evidence_level, superseded_utc",
}
_CLAIMS = "level, claimed_inchikey, claimed_name"


def _seed(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO workspace (workspace_id, workspace_name)"
                " VALUES ('ws-claim', 'Claims')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO dataset (dataset_id, workspace_id, dataset_name)"
                " VALUES ('ds-claim', 'ws-claim', 'Claims')"
            )
        )
        conn.execute(
            text("""
                INSERT INTO sample_file (sample_file_id, filename, instrument,
                                         instrument_type,
                                         "datetime", datetime_utc, length,
                                         "range", polarity)
                VALUES ('sf-claim', 'claims.raw', 'instrument-x', 'orbitrap',
                        :local, :utc, 60.0, CAST('[100.0, 500.0]' AS json), '-')
            """),
            {"local": datetime(2026, 10, 1, 12, 0), "utc": _TIME},
        )
        conn.execute(
            text(
                "INSERT INTO sample_batch (sample_batch_id, dataset_id,"
                " sample_batch_name) VALUES ('sb-claim', 'ds-claim', 'Claims')"
            )
        )
        conn.execute(
            text("""
                INSERT INTO sample_item (sample_item_id, sample_batch_id,
                                         sample_file_id, sample_item_name,
                                         sample_item_type, polarity)
                VALUES ('si-claim', 'sb-claim', 'sf-claim', 'claims',
                        'ACQUISITION', '-')
            """)
        )
        rows = [
            {
                "id": key,
                "peak": f"p{index}",
                "verdict": verdict,
                "evidence_level": evidence_level,
                "superseded": _TIME if superseded else None,
            }
            for index, (key, (verdict, evidence_level, superseded)) in enumerate(
                _VERDICTS.items()
            )
        ]
        conn.execute(
            text("""
                INSERT INTO assignment_verification (
                    assignment_verification_id, sample_item_id, sample_peak_id,
                    assigned_formula, verdict, evidence_level, fit_score,
                    evidence, p_correct, verified_utc, superseded_utc)
                VALUES (:id, 'si-claim', :peak, 'C6H12O6', :verdict,
                        :evidence_level, 0.9, 0.8, 0.7, :time, :superseded)
            """),
            [{**row, "time": _TIME} for row in rows],
        )
        conn.execute(
            text("""
                INSERT INTO batch_peak_verification (
                    batch_peak_verification_id, sample_batch_id, batch_peak_id,
                    assigned_formula, verdict, evidence_level, context,
                    verified_utc, superseded_utc)
                VALUES (:id, 'sb-claim', :peak, 'C6H12O6', :verdict,
                        :evidence_level, CAST('{"n_present": 2}' AS json),
                        :time, :superseded)
            """),
            [{**row, "time": _TIME} for row in rows],
        )


def _read(engine: Engine, columns: dict) -> dict:
    """table -> id -> the table's columns, as a tuple."""
    with engine.connect() as conn:
        return {
            table: {
                row[0]: tuple(row[1:])
                for row in conn.execute(
                    text(f"SELECT {table}_id, {listed} FROM {table}")
                ).all()
            }
            for table, listed in columns.items()
        }


@pytest.fixture(scope="module")
def migrated(seeded_alembic_config: Config, seeded_engine: Engine) -> dict:
    """Seed both tables at the prior revision, apply the migration, read back."""
    upgrade(seeded_alembic_config, PRIOR_REVISION)
    _seed(seeded_engine)
    before = _read(seeded_engine, _KEPT)
    upgrade(seeded_alembic_config, REVISION)
    return {
        "before": before,
        "after": _read(seeded_engine, _KEPT),
        "claims": _read(seeded_engine, dict.fromkeys(_KEPT, _CLAIMS)),
    }


@pytest.mark.parametrize(
    "table", ["assignment_verification", "batch_peak_verification"]
)
def test_an_earlier_confirmation_reads_as_level_four_naming_no_structure(
    migrated: dict, table: str
) -> None:
    claims = migrated["claims"][table]
    for key in (
        "v-standard",
        "v-msms",
        "v-orthogonal",
        "v-pattern",
        "v-visual",
        "v-superseded",
    ):
        assert claims[key] == (4, None, None), key


@pytest.mark.parametrize(
    "table", ["assignment_verification", "batch_peak_verification"]
)
def test_an_earlier_rejection_or_unsure_verdict_states_no_level(
    migrated: dict, table: str
) -> None:
    claims = migrated["claims"][table]
    assert claims["v-rejected"] == (None, None, None)
    assert claims["v-unsure"] == (None, None, None)


def test_every_verdict_keeps_its_evidence_and_snapshot(migrated: dict) -> None:
    """What the calibration pool selects on, so it gathers the same labels."""
    assert migrated["after"] == migrated["before"]


@pytest.mark.parametrize(
    "table", ["assignment_verification", "batch_peak_verification"]
)
def test_the_checks_hold_on_the_migrated_table(
    migrated: dict, seeded_engine: Engine, table: str
) -> None:
    """A level 1 naming no structure is refused by the CHECK the upgrade made."""
    with pytest.raises(IntegrityError, match="structure_level_names_structure"):
        with seeded_engine.begin() as conn:
            conn.execute(
                text(f"UPDATE {table} SET level = 1 WHERE {table}_id = 'v-standard'")
            )


@pytest.mark.parametrize(
    "table", ["assignment_verification", "batch_peak_verification"]
)
def test_the_upgrade_makes_every_check_the_models_declare(
    migrated: dict, seeded_engine: Engine, table: str
) -> None:
    """Drift does not compare CHECKs, so a migration missing one would pass it."""
    with seeded_engine.connect() as conn:
        names = set(
            conn.execute(
                text(
                    "SELECT conname FROM pg_constraint"
                    " WHERE conrelid = CAST(:table AS regclass) AND contype = 'c'"
                ),
                {"table": table},
            ).scalars()
        )
    assert {
        f"ck_{table}_level_valid",
        f"ck_{table}_structure_level_names_structure",
        f"ck_{table}_claim_is_confirmed",
        f"ck_{table}_claimed_inchikey_shape",
    } <= names


def test_it_downgrades(
    seeded_alembic_config: Config, seeded_engine: Engine, migrated: dict
) -> None:
    downgrade(seeded_alembic_config, PRIOR_REVISION)
    with seeded_engine.connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        columns = conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_name = 'assignment_verification'"
            )
        ).scalars()
        assert "level" not in set(columns)
    assert version == PRIOR_REVISION
