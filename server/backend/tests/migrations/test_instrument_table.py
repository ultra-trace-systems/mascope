"""Seeded test for `3c8e1f5a9d27`, which keeps each instrument as a row.

Stairway and drift walk the chain against an empty database, so the backfill
makes no row there. Here it runs over names the way a deployment holds them:
one instrument with several files, a second spelling of it that the instrument
list counts as another instrument, a TOF, a name only a mode's scope gives,
and rows that name none. What is pinned is the brief's "one row per distinct
instrument string, every row that carries it linked", plus what the backfill
can know about each row: the class of its files, the vendor that follows,
and when a file of it was first and last registered.

Rows are seeded at the previous revision through raw SQL rather than the ORM:
at PRIOR_REVISION the columns this suite is about do not exist yet.
"""

from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic.command import downgrade, upgrade
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


# This checkout's migrations, not MASCOPE_PATH's - see conftest.BACKEND_PATH.
_ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"

REVISION = "3c8e1f5a9d27"
_SCRIPT = ScriptDirectory.from_config(Config(str(_ALEMBIC_INI))).get_revision(REVISION)
PRIOR_REVISION = _SCRIPT.down_revision

_FIRST = datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc)
_LAST = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)

# (sample_file_id, instrument, class, registered)
_FILES = [
    ("sf-instr-a1", "ORBI-1", "orbi", _FIRST),
    ("sf-instr-a2", "ORBI-1", "orbi", _LAST),
    ("sf-instr-a3", "orbi-1", "orbi", _LAST),
    ("sf-instr-t1", "TOF-A", "tof", _FIRST),
]

_LINKED = (
    "notification",
    "agent_device",
    "dataset",
    "dataset_tombstone",
    "sample_file",
    "ionization_mode",
    "method_binding",
    "instrument_function",
)


def _seed(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO sample_file (sample_file_id, filename, instrument,
                    "datetime", datetime_utc, length, "range", polarity,
                    instrument_type, sample_file_utc_created)
                VALUES (:id, :filename, :instrument, :local, :utc, 60.0,
                        CAST('[100.0, 500.0]' AS json), '+', :type, :created)
                """
            ),
            [
                {
                    "id": file_id,
                    "filename": f"{instrument}_2026.09.01-12h00m00s_{file_id}",
                    "instrument": instrument,
                    "local": datetime(2026, 9, 1, 12, 0),
                    "utc": created,
                    "type": instrument_type,
                    "created": created,
                }
                for file_id, instrument, instrument_type, created in _FILES
            ],
        )
        conn.execute(
            text(
                """
                INSERT INTO ionization_mode (ionization_mode_id,
                    ionization_mode_name, ionization_mode_polarity,
                    ionization_mechanism_ids, instrument)
                VALUES ('modescoped00001', 'Scoped', '-', CAST('[]' AS json),
                        'ORBI-1'),
                       ('modebench000001', 'Bench only', '-',
                        CAST('[]' AS json), 'Bench-X'),
                       ('modeglobal00001', 'Everywhere', '+',
                        CAST('[]' AS json), NULL)
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO method_binding (method_binding_id, binding_key,
                    instrument, method_key, signature_class, chemistry_keys,
                    state, source, first_seen, last_seen, n_streams,
                    n_disagreements, n_candidate_streams)
                VALUES ('mbinstrument0001', 'digest-instrument', 'TOF-A',
                        'm.meth', 'TOF -', CAST('[]' AS json), 'learned',
                        'token', now(), now(), 1, 0, 0)
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO instrument_function (instrument_function_id,
                    instrument, method_file, datetime_utc)
                VALUES ('ifinstrument00000000000000000001', 'ORBI-1', '',
                        now())
                """
            )
        )


@pytest.fixture(scope="module")
def upgraded(seeded_alembic_config: Config, seeded_engine: Engine) -> Engine:
    upgrade(seeded_alembic_config, PRIOR_REVISION)
    _seed(seeded_engine)
    upgrade(seeded_alembic_config, REVISION)
    return seeded_engine


def _instruments(engine: Engine) -> dict[str, dict]:
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT * FROM instrument")).mappings().all()
    return {row["name"]: dict(row) for row in rows}


def test_one_row_per_distinct_string(upgraded: Engine):
    """The spellings stay apart, as the instrument list keeps them."""
    assert sorted(_instruments(upgraded)) == ["Bench-X", "ORBI-1", "TOF-A", "orbi-1"]


def test_every_sample_file_is_linked_to_its_own_name(upgraded: Engine):
    with upgraded.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT sf.sample_file_id, sf.instrument, i.name"
                " FROM sample_file sf"
                " LEFT JOIN instrument i ON i.instrument_id = sf.instrument_id"
            )
        ).all()
    assert len(rows) == len(_FILES)
    assert all(instrument == name for _, instrument, name in rows)


def test_the_other_tables_are_linked_where_they_name_one(upgraded: Engine):
    with upgraded.connect() as conn:
        modes = dict(
            conn.execute(
                text(
                    "SELECT m.ionization_mode_id, i.name FROM ionization_mode m"
                    " LEFT JOIN instrument i ON i.instrument_id = m.instrument_id"
                )
            ).all()
        )
        binding = conn.execute(
            text(
                "SELECT i.name FROM method_binding b"
                " JOIN instrument i ON i.instrument_id = b.instrument_id"
            )
        ).scalar_one()
        function = conn.execute(
            text(
                "SELECT i.name FROM instrument_function f"
                " JOIN instrument i ON i.instrument_id = f.instrument_id"
            )
        ).scalar_one()
    assert modes == {
        "modescoped00001": "ORBI-1",
        "modebench000001": "Bench-X",
        "modeglobal00001": None,
    }
    assert binding == "TOF-A"
    assert function == "ORBI-1"


def test_what_the_files_say_fills_the_row(upgraded: Engine):
    instruments = _instruments(upgraded)
    orbi = instruments["ORBI-1"]
    assert orbi["instrument_type"] == "orbi"
    assert orbi["vendor"] == "Thermo Fisher Scientific"
    assert orbi["created_utc"] == _FIRST
    assert orbi["last_seen_utc"] == _LAST
    assert instruments["TOF-A"]["vendor"] == "TOFWERK"
    for column in ("model", "serial_number", "software_version", "cv_term"):
        assert orbi[column] is None


def test_a_name_no_file_carries_has_no_class_and_was_never_seen(upgraded: Engine):
    bench = _instruments(upgraded)["Bench-X"]
    assert bench["instrument_type"] is None
    assert bench["vendor"] is None
    assert bench["last_seen_utc"] is None
    assert bench["created_utc"] is not None


def test_the_row_outlives_its_last_file(upgraded: Engine):
    """The point of the table: deleting the files leaves the instrument."""
    with upgraded.begin() as conn:
        conn.execute(text("DELETE FROM sample_file WHERE instrument = 'TOF-A'"))
        conn.execute(text("DELETE FROM method_binding"))
    assert "TOF-A" in _instruments(upgraded)


def test_downgrade_drops_the_table_and_the_links(
    upgraded: Engine, seeded_alembic_config: Config
):
    downgrade(seeded_alembic_config, PRIOR_REVISION)
    inspector = inspect(upgraded)
    assert "instrument" not in inspector.get_table_names()
    for table in _LINKED:
        assert "instrument_id" not in {
            column["name"] for column in inspector.get_columns(table)
        }
