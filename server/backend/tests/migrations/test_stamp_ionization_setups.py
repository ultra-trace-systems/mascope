"""Seeded test for `e2a6f0b4d851`, which records setups on runs and curves.

What exists is pointed at version 1 (MAS-351, mint and repoint): an in-app
run's config records its sample's mode as the previous revision left it, and
a curve the setups of the verifications its fit could have drawn on. Pinned
against what each must leave alone: an imported run, a run with no config, a
sample bound under no mode, a verdict made after the fit or superseded before
it, and a curve of another instrument class.

Rows are seeded at the previous revision through raw SQL rather than the ORM:
at PRIOR_REVISION the column this suite is about does not exist yet.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic.command import downgrade, upgrade
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import Engine


# This checkout's migrations, not MASCOPE_PATH's - see conftest.BACKEND_PATH.
_ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"

REVISION = "e2a6f0b4d851"
_SCRIPT = ScriptDirectory.from_config(Config(str(_ALEMBIC_INI))).get_revision(REVISION)
PRIOR_REVISION = _SCRIPT.down_revision

_FIT = datetime(2026, 9, 1, tzinfo=timezone.utc)

_SEED = [
    "INSERT INTO workspace (workspace_id, workspace_name) VALUES ('wsstamp', 'Stamps')",
    "INSERT INTO dataset (dataset_id, workspace_id, dataset_name)"
    " VALUES ('dsstamp', 'wsstamp', 'Stamps')",
    "INSERT INTO sample_batch (sample_batch_id, dataset_id, sample_batch_name)"
    " VALUES ('sbstamp', 'dsstamp', 'Stamps')",
    """
    INSERT INTO sample_file (sample_file_id, filename, instrument, "datetime",
                             datetime_utc, length, "range", polarity,
                             instrument_type)
    VALUES ('sforbi', 'orbi-stamp.raw', 'ORBI-S', now(), now(), 60.0,
            CAST('[100.0, 500.0]' AS json), '-', 'orbi'),
           ('sftof', 'tof-stamp.h5', 'TOF-S', now(), now(), 60.0,
            CAST('[100.0, 500.0]' AS json), '-', 'tof')
    """,
    """
    INSERT INTO ionization_mode (ionization_mode_id, ionization_mode_name,
                                 ionization_mode_polarity,
                                 ionization_mechanism_ids)
    VALUES ('modenitrate', 'Nitrate', '-', CAST('[]' AS json)),
           ('modebromide', 'Bromide', '-', CAST('[]' AS json)),
           ('modelate', 'Late', '-', CAST('[]' AS json))
    """,
    """
    INSERT INTO sample_item (sample_item_id, sample_batch_id, sample_file_id,
                             sample_item_name, sample_item_type, polarity,
                             ionization_mode_id)
    VALUES ('itnitrate', 'sbstamp', 'sforbi', 'n', 'ANALYSIS', '-', 'modenitrate'),
           ('itbromide', 'sbstamp', 'sftof', 'b', 'ANALYSIS', '-', 'modebromide'),
           ('itlate', 'sbstamp', 'sforbi', 'l', 'ANALYSIS', '-', 'modelate'),
           ('itnone', 'sbstamp', 'sforbi', 'x', 'ANALYSIS', '-', NULL)
    """,
    """
    INSERT INTO peak_assignment_run (peak_assignment_run_id, sample_item_id,
                                     engine, engine_version, status, config)
    VALUES ('runinapp', 'itnitrate', 'mascope', '1', 'completed',
            CAST('{"run_untargeted": true}' AS json)),
           ('runimported', 'itnitrate', 'other-engine', '1', 'completed',
            CAST('{"supplied": 1}' AS json)),
           ('runnoconfig', 'itnitrate', 'mascope', '1', 'completed', NULL),
           ('runnomode', 'itnone', 'mascope', '1', 'completed',
            CAST('{"run_untargeted": true}' AS json))
    """,
]


def _verification(conn, vid, item, verdict, verified, superseded=None, run=None):
    conn.execute(
        text(
            "INSERT INTO assignment_verification (assignment_verification_id,"
            " sample_item_id, peak_assignment_run_id, sample_peak_id, verdict,"
            " evidence, verified_utc, superseded_utc)"
            " VALUES (:id, :item, :run, :peak, :verdict, 0.5, :verified,"
            " :superseded)"
        ),
        {
            "id": vid,
            "item": item,
            "run": run,
            "peak": vid,
            "verdict": verdict,
            "verified": verified,
            "superseded": superseded,
        },
    )


@pytest.fixture(scope="module")
def upgraded(seeded_alembic_config: Config, seeded_engine: Engine) -> Engine:
    upgrade(seeded_alembic_config, PRIOR_REVISION)
    with seeded_engine.begin() as conn:
        for statement in _SEED:
            conn.execute(text(statement))
        before = _FIT - timedelta(days=1)
        after = _FIT + timedelta(days=1)
        # In the orbi curve's pool: live at the fit, of an in-app or pruned run.
        _verification(
            conn, "vnitrate", "itnitrate", "confirmed", before, run="runinapp"
        )
        # Out of it: made after the fit; superseded before it; another class.
        _verification(conn, "vlate", "itlate", "rejected", after)
        _verification(
            conn, "vgone", "itlate", "confirmed", before - timedelta(days=1), before
        )
        _verification(conn, "vtof", "itbromide", "rejected", before)
        conn.execute(
            text(
                "INSERT INTO assignment_calibration (instrument, score_version,"
                " a, b, n_pos, n_neg, provisional, is_active, fit_utc,"
                " created_utc)"
                " VALUES ('orbi', 1, 1.0, 0.0, 1, 1, false, true, :fit, :fit),"
                "        ('qq', 1, 1.0, 0.0, 1, 1, false, true, :fit, :fit)"
            ),
            {"fit": _FIT},
        )
    upgrade(seeded_alembic_config, REVISION)
    return seeded_engine


def _configs(engine: Engine) -> dict:
    with engine.connect() as conn:
        return dict(
            conn.execute(
                text("SELECT peak_assignment_run_id, config FROM peak_assignment_run")
            ).all()
        )


def test_an_in_app_run_records_its_samples_mode_as_version_one(upgraded: Engine):
    configs = _configs(upgraded)
    stamp = {"ionization_mode_id": "modenitrate", "version": 1}
    assert configs["runinapp"] == {"run_untargeted": True, "ionization_setup": stamp}


def test_a_run_of_a_sample_with_no_mode_records_none(upgraded: Engine):
    configs = _configs(upgraded)
    assert "ionization_setup" in configs["runnomode"]
    assert configs["runnomode"]["ionization_setup"] is None


def test_an_imported_run_and_one_with_no_config_are_left_alone(upgraded: Engine):
    configs = _configs(upgraded)
    assert configs["runimported"] == {"supplied": 1}
    assert configs["runnoconfig"] is None


def test_a_curve_records_the_setups_its_fit_could_draw_on(upgraded: Engine):
    with upgraded.connect() as conn:
        curves = dict(
            conn.execute(
                text("SELECT instrument, ionization_setups FROM assignment_calibration")
            ).all()
        )
    assert curves["orbi"] == [{"ionization_mode_id": "modenitrate", "version": 1}]
    assert curves["qq"] is None


def test_downgrade_drops_the_column_and_the_key(
    upgraded: Engine, seeded_alembic_config: Config
):
    downgrade(seeded_alembic_config, PRIOR_REVISION)
    configs = _configs(upgraded)
    assert configs["runinapp"] == {"run_untargeted": True}
    assert configs["runimported"] == {"supplied": 1}
    with upgraded.connect() as conn:
        columns = {
            row[0]
            for row in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns"
                    " WHERE table_name = 'assignment_calibration'"
                )
            )
        }
    assert "ionization_setups" not in columns
