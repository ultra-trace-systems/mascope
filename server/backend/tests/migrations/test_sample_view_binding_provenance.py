"""Seeded test for `67479416fc78`, which shows the rung through the sample view.

The migration stores nothing: it drops ``sample_view`` and creates it again
with ``bound_by`` and ``method_binding_id`` in it. So what there is to pin is
that the view reads the two off each item's own row - an item a binding bound
with its binding, an item nothing routed with neither - and that the downgrade
leaves a view without them rather than no view at all, since every listing of
samples reads it.

Rows are seeded at the previous revision through raw SQL rather than the ORM,
as the other seeded migration tests are: the ORM maps the schema at head.
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

REVISION = "67479416fc78"
_SCRIPT = ScriptDirectory.from_config(Config(str(_ALEMBIC_INI))).get_revision(REVISION)
PRIOR_REVISION = _SCRIPT.down_revision

_IDS = {
    "workspace": "ws-viewrung0001",
    "dataset": "ds-viewrung0001",
    "batch": "sb-viewrung0001",
    "file": "sf-viewrung0001",
    "mode": "im-viewrung0001",
    "binding": "mb-viewrung0001",
    #: Routed by the rung that reads a binding.
    "routed": "si-viewrung-a",
    #: Made by a person, so nothing routed it.
    "by_hand": "si-viewrung-b",
}

_SEED_SQL = [
    """
    INSERT INTO workspace (workspace_id, workspace_name)
    VALUES (:workspace, 'View binding provenance')
    """,
    """
    INSERT INTO dataset (dataset_id, workspace_id, dataset_name)
    VALUES (:dataset, :workspace, 'View binding provenance')
    """,
    """
    INSERT INTO sample_batch (sample_batch_id, dataset_id, sample_batch_name)
    VALUES (:batch, :dataset, 'View binding provenance')
    """,
    """
    INSERT INTO sample_file (sample_file_id, filename, instrument, "datetime",
                             datetime_utc, length, "range", polarity,
                             instrument_type, method_file)
    VALUES (:file, 'view-binding-provenance.raw', 'instrument-A', :local, :utc,
            60.0, CAST('[100.0, 500.0]' AS json), '-', 'orbi', 'nitrate.meth')
    """,
    """
    INSERT INTO ionization_mode (ionization_mode_id, ionization_mode_name,
                                 ionization_mode_polarity,
                                 ionization_mechanism_ids)
    VALUES (:mode, 'Nitrate view provenance test', '-', CAST('[]' AS json))
    """,
    """
    INSERT INTO method_binding (
        method_binding_id, binding_key, instrument, method_key,
        signature_class, ionization_mode_id, chemistry_keys, state, source,
        first_seen, last_seen, n_streams, n_disagreements, last_chemistry_key,
        n_candidate_streams
    ) VALUES (
        :binding, 'view-provenance-key', 'instrument-A', 'nitrate.meth',
        'FTMS - p NSI Full ms', :mode, CAST('["mech-a"]' AS json), 'learned',
        'token', now(), now(), 1, 0, 'mech-a', 0
    )
    """,
    """
    INSERT INTO sample_item (sample_item_id, sample_batch_id, sample_file_id,
                             sample_item_name, sample_item_type, polarity,
                             ionization_mode_id, bound_by, method_binding_id)
    VALUES (:routed, :batch, :file, 'routed by its method', 'ACQUISITION',
            '-', :mode, 'method', :binding)
    """,
    """
    INSERT INTO sample_item (sample_item_id, sample_batch_id, sample_file_id,
                             sample_item_name, sample_item_type, polarity,
                             ionization_mode_id)
    VALUES (:by_hand, :batch, :file, 'made by a person', 'UNKNOWN', '-', :mode)
    """,
]


def _view_columns(engine: Engine) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'sample_view'"
            )
        ).all()
    return {row[0] for row in rows}


@pytest.fixture(scope="module")
def upgraded(seeded_alembic_config: Config, seeded_engine: Engine) -> Engine:
    """Two items, one routed and one not, then the migration."""
    upgrade(seeded_alembic_config, PRIOR_REVISION)
    with seeded_engine.begin() as conn:
        for statement in _SEED_SQL:
            conn.execute(
                text(statement),
                _IDS
                | {
                    "local": datetime(2026, 10, 9, 12, 0),
                    "utc": datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc),
                },
            )
    # Checked here, where the view is still the old one: the tests below
    # would pass as well against a view that had the columns all along.
    assert not {"bound_by", "method_binding_id"} & _view_columns(seeded_engine)
    upgrade(seeded_alembic_config, REVISION)
    return seeded_engine


def test_the_view_shows_what_bound_each_item(upgraded: Engine):
    with upgraded.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT sample_item_id, bound_by, method_binding_id "
                "FROM sample_view WHERE sample_batch_id = :batch"
            ),
            _IDS,
        ).all()

    assert {item_id: (rung, binding) for item_id, rung, binding in rows} == {
        _IDS["routed"]: ("method", _IDS["binding"]),
        _IDS["by_hand"]: (None, None),
    }


def test_the_view_still_reads_the_file_beside_the_item(upgraded: Engine):
    """Recreated whole, so what it showed before is part of what to check."""
    with upgraded.connect() as conn:
        row = conn.execute(
            text(
                "SELECT filename, instrument, instrument_type, method_file "
                "FROM sample_view WHERE sample_item_id = :routed"
            ),
            _IDS,
        ).one()

    assert tuple(row) == (
        "view-binding-provenance.raw",
        "instrument-A",
        "orbi",
        "nitrate.meth",
    )


def test_the_downgrade_takes_the_columns_out_and_keeps_the_view(
    upgraded: Engine, seeded_alembic_config: Config
):
    # Runs last: it takes the database back to PRIOR_REVISION.
    before = _view_columns(upgraded)

    downgrade(seeded_alembic_config, PRIOR_REVISION)

    after = _view_columns(upgraded)
    assert after, "the downgrade must leave sample_view in place"
    assert before - after == {"bound_by", "method_binding_id"}
    assert after <= before
    # The columns themselves are another revision's, and stay.
    with upgraded.connect() as conn:
        stored = conn.execute(
            text(
                "SELECT bound_by, method_binding_id FROM sample_item "
                "WHERE sample_item_id = :routed"
            ),
            _IDS,
        ).one()
    assert tuple(stored) == ("method", _IDS["binding"])
