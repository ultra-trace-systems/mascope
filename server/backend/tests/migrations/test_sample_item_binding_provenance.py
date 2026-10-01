"""Seeded test for `6e4a9c2b7f31`, which records the rung on each item.

Two columns are added to ``sample_item`` and nothing is written to them, so
what there is to pin is what NULL means and what the binding reference costs.

An item processed before the migration must read NULL on both and keep
reading NULL: the rung it took could be guessed by re-running the token rule
over the history, and guessing it is what section 9.1 of the design note
forbids - a decision is never backfilled, and a guess would read in a report
as though it had been recorded at the time.

The reference to the binding must survive the binding's deletion. A binding
is a summary of how files routed, and the samples it routed are the data; a
CASCADE here would make deleting a summary delete them.
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

REVISION = "6e4a9c2b7f31"
_SCRIPT = ScriptDirectory.from_config(Config(str(_ALEMBIC_INI))).get_revision(REVISION)
PRIOR_REVISION = _SCRIPT.down_revision

_WORKSPACE_ID = "ws-provenance01"
_DATASET_ID = "ds-provenance01"
_BATCH_ID = "sb-provenance01"
_FILE_ID = "sf-provenance01"
_MODE_ID = "im-provenance01"
_BINDING_ID = "mb-provenance01"
#: Processed before the columns existed.
_OLD_ITEM = "si-provenance-a"
#: Processed after, by the rung that reads a binding.
_NEW_ITEM = "si-provenance-b"


_SEED_SQL = [
    """
    INSERT INTO workspace (workspace_id, workspace_name)
    VALUES (:workspace, 'Binding provenance')
    """,
    """
    INSERT INTO dataset (dataset_id, workspace_id, dataset_name)
    VALUES (:dataset, :workspace, 'Binding provenance')
    """,
    """
    INSERT INTO sample_batch (sample_batch_id, dataset_id, sample_batch_name)
    VALUES (:batch, :dataset, 'Binding provenance')
    """,
    """
    INSERT INTO sample_file (sample_file_id, filename, instrument, "datetime",
                             datetime_utc, length, "range", polarity,
                             instrument_type, method_file)
    VALUES (:file, 'binding-provenance.raw', 'instrument-A', :local, :utc,
            60.0, CAST('[100.0, 500.0]' AS json), '-', 'orbi', 'nitrate.meth')
    """,
    """
    INSERT INTO ionization_mode (ionization_mode_id, ionization_mode_name,
                                 ionization_mode_polarity,
                                 ionization_mechanism_ids)
    VALUES (:mode, 'Nitrate provenance test', '-', CAST('[]' AS json))
    """,
    """
    INSERT INTO method_binding (
        method_binding_id, binding_key, instrument, method_key,
        signature_class, ionization_mode_id, chemistry_keys, state, source,
        first_seen, last_seen, n_streams, n_disagreements, last_chemistry_key
    ) VALUES (
        :binding, 'provenance-key', 'instrument-A', 'nitrate.meth',
        'FTMS - p NSI Full ms', :mode, CAST('["mech-a"]' AS json), 'learned',
        'token', now(), now(), 1, 0, 'mech-a'
    )
    """,
    """
    INSERT INTO sample_item (sample_item_id, sample_batch_id, sample_file_id,
                             sample_item_name, sample_item_type, polarity,
                             ionization_mode_id)
    VALUES (:old_item, :batch, :file, 'before the columns', 'ACQUISITION',
            '-', :mode)
    """,
]

_IDS = {
    "workspace": _WORKSPACE_ID,
    "dataset": _DATASET_ID,
    "batch": _BATCH_ID,
    "file": _FILE_ID,
    "mode": _MODE_ID,
    "binding": _BINDING_ID,
    "old_item": _OLD_ITEM,
}


def _provenance(engine: Engine, item_id: str) -> tuple[str | None, str | None]:
    """The rung and binding recorded on an item, with its mode for company."""
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT bound_by, method_binding_id, ionization_mode_id "
                "FROM sample_item WHERE sample_item_id = :id"
            ),
            {"id": item_id},
        ).one()
    assert row.ionization_mode_id == _MODE_ID
    return row.bound_by, row.method_binding_id


@pytest.fixture(scope="module")
def upgraded(seeded_alembic_config: Config, seeded_engine: Engine) -> Engine:
    """A routed item and the binding for its method, then the migration."""
    upgrade(seeded_alembic_config, PRIOR_REVISION)
    with seeded_engine.begin() as conn:
        for statement in _SEED_SQL:
            conn.execute(
                text(statement),
                _IDS
                | {
                    "local": datetime(2026, 9, 1, 12, 0),
                    "utc": datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc),
                },
            )
    upgrade(seeded_alembic_config, REVISION)
    return seeded_engine


def test_an_item_processed_before_the_columns_records_no_rung(upgraded: Engine):
    assert _provenance(upgraded, _OLD_ITEM) == (None, None)


def test_an_item_can_record_the_binding_that_bound_it(upgraded: Engine):
    with upgraded.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO sample_item ("
                "  sample_item_id, sample_batch_id, sample_file_id,"
                "  sample_item_name, sample_item_type, polarity,"
                "  ionization_mode_id, bound_by, method_binding_id"
                ") VALUES (:id, :batch, :file, 'routed by its method',"
                "          'ACQUISITION', '-', :mode, 'method', :binding)"
            ),
            _IDS | {"id": _NEW_ITEM},
        )

    assert _provenance(upgraded, _NEW_ITEM) == ("method", _BINDING_ID)


def test_deleting_the_binding_keeps_the_item_and_its_mode(upgraded: Engine):
    """A binding is a summary of routing; the samples it routed are the data."""
    with upgraded.begin() as conn:
        conn.execute(
            text("DELETE FROM method_binding WHERE method_binding_id = :binding"),
            {"binding": _BINDING_ID},
        )

    # The rung stays: this item was bound by its method, and that remains
    # true after the row recording the method is gone.
    assert _provenance(upgraded, _NEW_ITEM) == ("method", None)


def test_the_downgrade_removes_both_columns(
    upgraded: Engine, seeded_alembic_config: Config
):
    # Runs last: it leaves the database at PRIOR_REVISION.
    downgrade(seeded_alembic_config, PRIOR_REVISION)
    with upgraded.connect() as conn:
        present = (
            conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'sample_item' "
                    "AND column_name IN ('bound_by', 'method_binding_id')"
                )
            )
            .scalars()
            .all()
        )
    assert present == []
