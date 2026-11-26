"""Seeded test for `b81e376e5236`: a hand-assigned isotope line stores its
own ion.

Stairway and drift run the chain against an empty database, where the
``UPDATE`` matches nothing. Here hand lines in the old shape - the
monoisotopic ion in ``ion_formula``, the line in ``isotope_formula`` - are
seeded at the previous revision beside the rows the migration must leave
alone: a hand M0, a run's line, a hand line that names an owner, and a hand
line that names no line formula (a promoted runner-up, mascope#3682).

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


# This checkout's migrations, not MASCOPE_PATH's - see conftest.BACKEND_PATH.
_ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"

REVISION = "b81e376e5236"
_SCRIPT = ScriptDirectory.from_config(Config(str(_ALEMBIC_INI))).get_revision(REVISION)
PRIOR_REVISION = _SCRIPT.down_revision

M0_ION = "C6H12BrO5+"
LINE = "[81Br]C6H12O5+"

# id -> (source, role, ion_formula, isotope_formula, owner id), owners first
_ROWS = {
    "pa-hand-m0": ("manual", "M0", M0_ION, None, None),
    "pa-hand-line": ("manual", "iso_child", M0_ION, LINE, None),
    "pa-hand-bare": ("manual", "iso_child", M0_ION, None, None),
    "pa-run-line": ("database", "iso_child", M0_ION, LINE, None),
    "pa-owned-line": ("manual", "iso_child", M0_ION, LINE, "pa-hand-m0"),
}


def _seed(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO workspace (workspace_id, workspace_name)"
                " VALUES ('ws-hil', 'Hand isotope line')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO dataset (dataset_id, workspace_id, dataset_name)"
                " VALUES ('ds-hil', 'ws-hil', 'Hand isotope line')"
            )
        )
        conn.execute(
            text("""
                INSERT INTO sample_file (sample_file_id, filename, instrument,
                                         instrument_type,
                                         "datetime", datetime_utc, length,
                                         "range", polarity)
                VALUES ('sf-hil', 'hand-isotope-line.raw', 'instrument-x', 'orbi',
                        :local, :utc, 60.0, CAST('[100.0, 500.0]' AS json), '+')
            """),
            {
                "local": datetime(2026, 11, 1, 12, 0),
                "utc": datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc),
            },
        )
        conn.execute(
            text(
                "INSERT INTO sample_batch (sample_batch_id, dataset_id,"
                " sample_batch_name) VALUES ('sb-hil', 'ds-hil', 'Hand isotope line')"
            )
        )
        conn.execute(
            text("""
                INSERT INTO sample_item (sample_item_id, sample_batch_id,
                                         sample_file_id, sample_item_name,
                                         sample_item_type, polarity)
                VALUES ('si-hil', 'sb-hil', 'sf-hil', 'si-hil', 'sample', '+')
            """)
        )
        conn.execute(
            text("""
                INSERT INTO peak_assignment_run (peak_assignment_run_id,
                                                 sample_item_id, engine,
                                                 engine_version, status)
                VALUES ('run-hil', 'si-hil', 'mascope', '0.3.0', 'completed')
            """)
        )
        conn.execute(
            text("""
                INSERT INTO peak_assignment (peak_assignment_id,
                                             peak_assignment_run_id,
                                             sample_item_id, sample_peak_id,
                                             sample_peak_mz, sample_peak_intensity,
                                             role, tier, source, assigned_formula,
                                             ion_formula, isotope_formula,
                                             owner_peak_assignment_id)
                VALUES (:id, 'run-hil', 'si-hil', :id, 263.0, 100.0, :role,
                        'candidate', :source, 'C6H11BrO5', :ion, :line, :owner)
            """),
            [
                {
                    "id": row_id,
                    "source": source,
                    "role": role,
                    "ion": ion,
                    "line": line,
                    "owner": owner,
                }
                for row_id, (source, role, ion, line, owner) in _ROWS.items()
            ],
        )


def _ions(engine: Engine) -> dict:
    with engine.connect() as conn:
        return dict(
            conn.execute(
                text(
                    "SELECT peak_assignment_id, ion_formula FROM peak_assignment"
                    " WHERE sample_item_id = 'si-hil'"
                )
            ).all()
        )


@pytest.fixture(scope="module")
def migrated(seeded_alembic_config: Config, seeded_engine: Engine) -> dict:
    """Seed the rows at the prior revision, apply the migration, read back."""
    upgrade(seeded_alembic_config, PRIOR_REVISION)
    _seed(seeded_engine)
    upgrade(seeded_alembic_config, REVISION)
    return _ions(seeded_engine)


def test_a_hand_line_with_no_owner_takes_its_line_as_its_ion(migrated: dict) -> None:
    assert migrated["pa-hand-line"] == LINE


def test_a_hand_line_that_names_no_line_keeps_its_ion(migrated: dict) -> None:
    # A runner-up promoted at an isotope line stores no line formula: nulling
    # its ion would lose the only formula it has, with no way back.
    assert migrated["pa-hand-bare"] == M0_ION


def test_every_other_row_is_left_as_it_was(migrated: dict) -> None:
    assert migrated["pa-hand-m0"] == M0_ION
    assert migrated["pa-run-line"] == M0_ION
    assert migrated["pa-owned-line"] == M0_ION


def test_it_downgrades(
    seeded_alembic_config: Config, seeded_engine: Engine, migrated: dict
) -> None:
    downgrade(seeded_alembic_config, PRIOR_REVISION)
    with seeded_engine.connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert version == PRIOR_REVISION
