"""The demo's reference seed: the shipped lists, in place of the legacy list.

`mascope demo` runs this on every start, so it has to be idempotent, and a demo
database an older build seeded carries a hand-picked `demo` source that the
shipped lists replace. Both are pinned against a SQLite stand-in for the mirror:
the seed runs on a synchronous engine of its own, which would otherwise be the
machine's runtime database rather than a test one.
"""

import pytest
from sqlalchemy import create_engine, text

import mascope_backend.db.scripts.seed_reference_demo as demo_seed


_SOURCE_DDL = """
CREATE TABLE reference_source (
    reference_source_id INTEGER PRIMARY KEY,
    name TEXT,
    version TEXT,
    license TEXT,
    record_count INTEGER,
    is_active BOOLEAN,
    ingested_at TEXT
)
"""

_COMPOUND_DDL = """
CREATE TABLE reference_compound (
    reference_compound_id INTEGER PRIMARY KEY,
    reference_source_id INTEGER,
    formula TEXT,
    monoisotopic_mass REAL,
    charge INTEGER,
    inchikey TEXT,
    name TEXT,
    smiles TEXT,
    inchi TEXT,
    source_native_id TEXT,
    xrefs JSON,
    license TEXT
)
"""


@pytest.fixture
def mirror(tmp_path, monkeypatch):
    """A mirror holding only the legacy demo list, wired into the seed."""
    engine = create_engine(f"sqlite:///{tmp_path / 'reference.db'}")
    with engine.begin() as conn:
        conn.exec_driver_sql(_SOURCE_DDL)
        conn.exec_driver_sql(_COMPOUND_DDL)
        conn.exec_driver_sql(
            "INSERT INTO reference_source VALUES "
            "(1, 'demo', 'demo-seed', 'public-domain', 1, 1, '2026-01-01')"
        )
        conn.exec_driver_sql(
            "INSERT INTO reference_compound "
            "(reference_compound_id, reference_source_id, formula, name, license) "
            "VALUES (1, 1, 'C10H16O3', 'Pinonic acid', 'public-domain')"
        )
    monkeypatch.setattr(demo_seed, "_sync_engine", lambda: engine)
    yield engine
    engine.dispose()


def _scalar(engine, sql: str):
    with engine.connect() as conn:
        return conn.execute(text(sql)).scalar()


def _active(engine) -> set[str]:
    with engine.connect() as conn:
        return set(
            conn.execute(
                text("SELECT name FROM reference_source WHERE is_active")
            ).scalars()
        )


def test_the_demo_loads_the_shipped_lists_in_place_of_the_legacy_one(mirror):
    demo_seed.seed_reference_demo()
    active = _active(mirror)
    assert demo_seed.LEGACY_DEMO_SOURCE not in active
    assert (
        _scalar(mirror, "SELECT count(*) FROM reference_source WHERE name = 'demo'")
        == 0
    )
    # The legacy list's compound went with its source: every compound left
    # belongs to a load the seed made. (Its id cannot tell - a freed rowid is
    # the first one the next source is given.)
    assert _scalar(mirror, "SELECT count(*) FROM reference_compound") == _scalar(
        mirror, "SELECT sum(record_count) FROM reference_source"
    )
    assert {"monoterpene-hom-kang2021", "contaminants-keller2008"} <= active
    # The radical list is opt-in, and the demo loads only the defaults.
    assert "monoterpene-ro2-kang2021" not in active


def test_starting_the_demo_twice_changes_nothing(mirror):
    demo_seed.seed_reference_demo()
    compounds = _scalar(mirror, "SELECT count(*) FROM reference_compound")
    sources = _scalar(mirror, "SELECT count(*) FROM reference_source")
    demo_seed.seed_reference_demo()
    assert _scalar(mirror, "SELECT count(*) FROM reference_compound") == compounds
    assert _scalar(mirror, "SELECT count(*) FROM reference_source") == sources
