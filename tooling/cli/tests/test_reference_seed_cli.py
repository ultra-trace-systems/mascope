"""`mascope reference seed`: the shipped lists, loaded through the real ingest.

Seeding activates sources, so it is gated behind the same confirmation as
`sync`, reached before any database work. `--list` never touches the database,
and an unknown list id is refused before the prompt is shown.
"""

import pytest
from sqlalchemy import create_engine, text
from typer.testing import CliRunner

import mascope_cli.cmd.reference.main as reference_main
from mascope_cli.cmd.reference.main import reference_app


runner = CliRunner()

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

# Every column the ingest writes, unlike the four the `status` tests need.
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
def no_engine(monkeypatch):
    """Fail loudly if the command reaches the database."""

    def _boom():
        raise AssertionError("the database must not be touched here")

    monkeypatch.setattr(reference_main, "_sync_engine", _boom)


@pytest.fixture
def mirror(tmp_path, monkeypatch):
    """An empty SQLite reference mirror, wired into `_sync_engine`."""
    engine = create_engine(f"sqlite:///{tmp_path / 'reference.db'}")
    with engine.begin() as conn:
        conn.exec_driver_sql(_SOURCE_DDL)
        conn.exec_driver_sql(_COMPOUND_DDL)
    monkeypatch.setattr(reference_main, "_sync_engine", lambda: engine)
    yield engine
    engine.dispose()


def _active(engine) -> set[str]:
    with engine.connect() as conn:
        return set(
            conn.execute(
                text("SELECT name FROM reference_source WHERE is_active")
            ).scalars()
        )


def test_list_shows_the_catalogue_without_the_database(no_engine, monkeypatch):
    monkeypatch.setattr(reference_main.console, "width", 200)
    result = runner.invoke(reference_app, ["seed", "--list"])
    assert result.exit_code == 0, result.output
    assert "monoterpene-hom-kang2021" in result.output
    assert "monoterpene-ro2-kang2021" in result.output


def test_an_unknown_list_is_refused_before_the_prompt(no_engine):
    result = runner.invoke(reference_app, ["seed", "no-such-list"], input="")
    assert result.exit_code == 1
    assert "Continue?" not in result.output


def test_seeding_aborts_when_confirmation_is_declined(no_engine):
    result = runner.invoke(reference_app, ["seed"], input="n\n")
    assert result.exit_code != 0
    assert "Continue?" in result.output


def test_the_confirmation_names_the_deletion_when_pruning(no_engine):
    result = runner.invoke(reference_app, ["seed", "--prune"], input="n\n")
    assert "DELETE" in result.output
    assert result.exit_code != 0


def test_seeding_loads_the_default_lists_and_not_the_opt_in_ones(mirror):
    result = runner.invoke(reference_app, ["seed", "--yes"])
    assert result.exit_code == 0, result.output
    active = _active(mirror)
    assert "monoterpene-hom-kang2021" in active
    assert "contaminants-keller2008" in active
    assert "monoterpene-ro2-kang2021" not in active

    # Named, an opt-in list loads, and the others are left as they were.
    result = runner.invoke(reference_app, ["seed", "monoterpene-ro2-kang2021", "--yes"])
    assert result.exit_code == 0, result.output
    assert _active(mirror) == active | {"monoterpene-ro2-kang2021"}
