"""Seeded test for the claim's letter and the batch claim's window in `2989ee6e6bde`.

Both verification tables hold claims recorded under version 1 of the
evidence-to-level mapping: a reference-standard confirmation at level 1 naming
a structure, an MS/MS one at 2, an orthogonal one at 3, a pattern one at 4, a
rejection that states none, and a superseded one. After the upgrade each keeps
its level, its version and no letter (step 5.8f: claims made under version 1
keep their level and their version), and what the calibration pool selects on
is unchanged. The narrowed CHECK takes a class claim (3b) naming no structure
and still refuses a level 2 naming none; the window's CHECKs hold on the batch
table.

Rows are seeded through raw SQL rather than the ORM: a migration test has to
describe the schema as it was at PRIOR_REVISION.
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
from sqlalchemy.exc import IntegrityError


# This checkout's migrations, not MASCOPE_PATH's - see conftest.BACKEND_PATH.
_ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"

REVISION = "2989ee6e6bde"
_SCRIPT = ScriptDirectory.from_config(Config(str(_ALEMBIC_INI))).get_revision(REVISION)
PRIOR_REVISION = _SCRIPT.down_revision

_TIME = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)
_GLUCOSE = "WQZGKKKJIJFFOK-GASJEMHNSA-N"
_V1 = json.dumps({"level_mapping_version": 1})

# id -> (verdict, evidence_level, level, inchikey, superseded)
_CLAIMS = {
    "v-standard": ("confirmed", "reference_standard", 1, _GLUCOSE, False),
    "v-msms": ("confirmed", "msms", 2, _GLUCOSE, False),
    "v-orthogonal": ("confirmed", "orthogonal", 3, _GLUCOSE, False),
    "v-pattern": ("confirmed", "pattern", 4, None, False),
    "v-superseded": ("confirmed", "reference_standard", 1, _GLUCOSE, True),
    "v-rejected": ("rejected", "reference_standard", None, None, False),
}

_TABLES = ("assignment_verification", "batch_peak_verification")

#: table -> what the calibration pool and every other reader selects on.
_KEPT = {
    "assignment_verification": (
        "verdict, evidence_level, fit_score, evidence, p_correct, superseded_utc, "
        "level, claimed_inchikey, claimed_name"
    ),
    "batch_peak_verification": (
        "verdict, evidence_level, superseded_utc, level, claimed_inchikey, claimed_name"
    ),
}


def _seed(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO workspace (workspace_id, workspace_name)"
                " VALUES ('ws-letter', 'Letters')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO dataset (dataset_id, workspace_id, dataset_name)"
                " VALUES ('ds-letter', 'ws-letter', 'Letters')"
            )
        )
        conn.execute(
            text("""
                INSERT INTO sample_file (sample_file_id, filename, instrument,
                                         instrument_type,
                                         "datetime", datetime_utc, length,
                                         "range", polarity)
                VALUES ('sf-letter', 'letters.raw', 'instrument-x', 'orbitrap',
                        :local, :utc, 60.0, CAST('[100.0, 500.0]' AS json), '-')
            """),
            {"local": datetime(2026, 11, 12, 12, 0), "utc": _TIME},
        )
        conn.execute(
            text(
                "INSERT INTO sample_batch (sample_batch_id, dataset_id,"
                " sample_batch_name) VALUES ('sb-letter', 'ds-letter', 'Letters')"
            )
        )
        conn.execute(
            text("""
                INSERT INTO sample_item (sample_item_id, sample_batch_id,
                                         sample_file_id, sample_item_name,
                                         sample_item_type, polarity)
                VALUES ('si-letter', 'sb-letter', 'sf-letter', 'letters',
                        'ACQUISITION', '-')
            """)
        )
        rows = [
            {
                "id": key,
                "peak": f"p{index}",
                "verdict": verdict,
                "evidence_level": evidence_level,
                "level": level,
                "inchikey": inchikey,
                "name": "D-glucose" if inchikey else None,
                "superseded": _TIME if superseded else None,
                "time": _TIME,
                "context": _V1,
            }
            for index, (
                key,
                (verdict, evidence_level, level, inchikey, superseded),
            ) in enumerate(_CLAIMS.items())
        ]
        conn.execute(
            text("""
                INSERT INTO assignment_verification (
                    assignment_verification_id, sample_item_id, sample_peak_id,
                    assigned_formula, verdict, evidence_level, fit_score,
                    evidence, p_correct, context, claimed_inchikey, claimed_name,
                    level, verified_utc, superseded_utc)
                VALUES (:id, 'si-letter', :peak, 'C6H12O6', :verdict,
                        :evidence_level, 0.9, 0.8, 0.7, CAST(:context AS json),
                        :inchikey, :name, :level, :time, :superseded)
            """),
            rows,
        )
        conn.execute(
            text("""
                INSERT INTO batch_peak_verification (
                    batch_peak_verification_id, sample_batch_id, batch_peak_id,
                    assigned_formula, verdict, evidence_level, context,
                    claimed_inchikey, claimed_name, level, verified_utc,
                    superseded_utc)
                VALUES (:id, 'sb-letter', :peak, 'C6H12O6', :verdict,
                        :evidence_level, CAST(:context AS json), :inchikey, :name,
                        :level, :time, :superseded)
            """),
            rows,
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
        "letters": _read(
            seeded_engine, dict.fromkeys(_TABLES, "level_letter, context")
        ),
    }


def test_every_claim_keeps_its_level_its_structure_and_what_the_pool_reads(
    migrated: dict,
) -> None:
    """Claims made under version 1 keep their level, and the calibration pool
    selects the same labels."""
    assert migrated["after"] == migrated["before"]
    assert migrated["after"]["assignment_verification"]["v-standard"][6] == 1


@pytest.mark.parametrize("table", _TABLES)
def test_a_version_one_claim_keeps_its_version_and_no_letter(
    migrated: dict, table: str
) -> None:
    for key, (letter, context) in migrated["letters"][table].items():
        assert letter is None, key
        assert context == {"level_mapping_version": 1}, key


@pytest.mark.parametrize("table", _TABLES)
def test_the_narrowed_check_takes_a_class_claim_and_no_other(
    migrated: dict, seeded_engine: Engine, table: str
) -> None:
    with seeded_engine.begin() as conn:
        conn.execute(
            text(
                f"UPDATE {table} SET level = 3, level_letter = 'b',"
                " claimed_inchikey = NULL, claimed_name = 'a carboxylic acid'"
                f" WHERE {table}_id = 'v-pattern'"
            )
        )
    with pytest.raises(IntegrityError, match="structure_level_names_structure"):
        with seeded_engine.begin() as conn:
            conn.execute(
                text(
                    f"UPDATE {table} SET level = 2, level_letter = 'b'"
                    f" WHERE {table}_id = 'v-pattern'"
                )
            )
    with pytest.raises(IntegrityError, match="level_letter_valid"):
        with seeded_engine.begin() as conn:
            conn.execute(
                text(
                    f"UPDATE {table} SET level_letter = 'a'"
                    f" WHERE {table}_id = 'v-standard'"
                )
            )


def test_the_window_checks_hold_on_the_batch_table(
    migrated: dict, seeded_engine: Engine
) -> None:
    table = "batch_peak_verification"
    with seeded_engine.begin() as conn:
        conn.execute(
            text(
                f"UPDATE {table} SET window_start_utc = :t, window_end_utc = :t"
                f" WHERE {table}_id = 'v-standard'"
            ),
            {"t": _TIME},
        )
    for assignment, check in (
        ("window_start_utc = NULL", "window_complete"),
        (
            "window_start_utc = window_end_utc + interval '1 day'",
            "window_ordered",
        ),
    ):
        with pytest.raises(IntegrityError, match=check):
            with seeded_engine.begin() as conn:
                conn.execute(
                    text(
                        f"UPDATE {table} SET {assignment}"
                        f" WHERE {table}_id = 'v-standard'"
                    )
                )
    with pytest.raises(IntegrityError, match="window_scopes_level_one"):
        with seeded_engine.begin() as conn:
            conn.execute(
                text(
                    f"UPDATE {table} SET window_start_utc = :t, window_end_utc = :t"
                    f" WHERE {table}_id = 'v-msms'"
                ),
                {"t": _TIME},
            )


@pytest.mark.parametrize("table", _TABLES)
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
    expected = {
        f"ck_{table}_level_valid",
        f"ck_{table}_level_letter_valid",
        f"ck_{table}_structure_level_names_structure",
        f"ck_{table}_claim_is_confirmed",
        f"ck_{table}_claimed_inchikey_shape",
    }
    if table == "batch_peak_verification":
        expected |= {
            f"ck_{table}_window_complete",
            f"ck_{table}_window_ordered",
            f"ck_{table}_window_scopes_level_one",
        }
    assert expected <= names


def test_it_downgrades_with_a_class_claim_reading_the_engines_level(
    seeded_alembic_config: Config, seeded_engine: Engine, migrated: dict
) -> None:
    table = "assignment_verification"
    with seeded_engine.begin() as conn:
        conn.execute(
            text(
                f"UPDATE {table} SET level = 3, level_letter = 'b',"
                " claimed_inchikey = NULL, claimed_name = 'an amine'"
                f" WHERE {table}_id = 'v-superseded'"
            )
        )
    downgrade(seeded_alembic_config, PRIOR_REVISION)
    with seeded_engine.connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        columns = set(
            conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns"
                    " WHERE table_name = 'batch_peak_verification'"
                )
            ).scalars()
        )
        class_level = conn.execute(
            text(f"SELECT level FROM {table} WHERE {table}_id = 'v-superseded'")
        ).scalar()
        standard = conn.execute(
            text(f"SELECT level FROM {table} WHERE {table}_id = 'v-standard'")
        ).scalar()
    assert version == PRIOR_REVISION
    assert not {"level_letter", "window_start_utc", "window_end_utc"} & columns
    assert class_level is None
    assert standard == 1
