"""Seeded test for `c39f8f2c0b3b`, the structured adduct row of every mechanism.

The stairway and drift tests walk the chain against a database created empty,
so they run this revision with no mechanism to fill. It is exercised here
against a database holding mechanisms in both notations - the shipped ones in
the standard one, a legacy row left by the notation migration, a mechanism of
several terms and one written as only the standard notation can - and a
free-text label no notation reads, which has to stop the upgrade before
anything is written, naming the row.

Rows are seeded at the previous revision through raw SQL rather than the ORM,
as the other seeded migration tests are: the ORM maps the schema at head.
"""

from pathlib import Path

import pytest
from alembic.command import downgrade, upgrade
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from mascope_backend.ionization_catalogue import shipped_mechanisms
from mascope_tools.composition.mechanism_notation import to_row


# This checkout's migrations, not MASCOPE_PATH's - see conftest.BACKEND_PATH.
_ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"

REVISION = "c39f8f2c0b3b"
_SCRIPT = ScriptDirectory.from_config(Config(str(_ALEMBIC_INI))).get_revision(REVISION)
PRIOR_REVISION = _SCRIPT.down_revision

_NEW_COLUMNS = {
    "operation",
    "species",
    "species_charge",
    "multimer_n",
    "neutral_loss",
    "name",
}

#: (id, mechanism, polarity): every shipped mechanism, then the rows that are
#: not shipped. Ids fit the 16-character column.
_ROWS = [
    (f"mech-ship-{index:02d}", shipped.notation, shipped.polarity)
    for index, shipped in enumerate(shipped_mechanisms())
] + [
    # A legacy row the notation migration left, its pair holding the
    # standard spelling.
    ("mech-legacy", "+K+", "+"),
    ("mech-ethyl", "[M-C2H5]+", "+"),
    ("mech-terms", "[M+C3H6O+H]+", "+"),
    # Only the standard notation says these.
    ("mech-dimer", "[2M+H]+", "+"),
    ("mech-doubly", "[M+2H]2+", "+"),
    ("mech-loss", "[M+H-H2O]+", "+"),
]

_LABEL = ("mech-label", "+H+ (a free-text label)", "+")


def _seed(engine: Engine, rows) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO ionization_mechanism (ionization_mechanism_id, "
                "ionization_mechanism_polarity, ionization_mechanism) "
                "VALUES (:id, :polarity, :mechanism)"
            ),
            [
                {"id": mechanism_id, "polarity": polarity, "mechanism": mechanism}
                for mechanism_id, mechanism, polarity in rows
            ],
        )


def _columns(engine: Engine) -> set[str]:
    return {c["name"] for c in inspect(engine).get_columns("ionization_mechanism")}


def _version(engine: Engine) -> str:
    with engine.connect() as conn:
        return conn.execute(text("SELECT version_num FROM alembic_version")).scalar()


def _snapshot(engine: Engine) -> dict:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT ionization_mechanism_id, ionization_mechanism, operation, "
                "species, species_charge, multimer_n, neutral_loss, name "
                "FROM ionization_mechanism"
            )
        ).all()
    return {row[0]: row[1:] for row in rows}


@pytest.fixture(scope="module")
def refused(seeded_alembic_config: Config, seeded_engine: Engine) -> dict:
    """The upgrade over a table holding the label, and what it left."""
    upgrade(seeded_alembic_config, PRIOR_REVISION)
    _seed(seeded_engine, [*_ROWS, _LABEL])
    with pytest.raises(RuntimeError) as raised:
        upgrade(seeded_alembic_config, REVISION)
    return {
        "error": str(raised.value),
        "version": _version(seeded_engine),
        "columns": _columns(seeded_engine),
    }


@pytest.fixture(scope="module")
def migrated(
    seeded_alembic_config: Config, seeded_engine: Engine, refused: dict
) -> dict:
    """The upgrade once the label is corrected away."""
    with seeded_engine.begin() as conn:
        conn.execute(
            text(
                "DELETE FROM ionization_mechanism WHERE ionization_mechanism_id = :id"
            ),
            {"id": _LABEL[0]},
        )
    upgrade(seeded_alembic_config, REVISION)
    return _snapshot(seeded_engine)


@pytest.fixture(scope="module")
def downgraded(
    seeded_alembic_config: Config, seeded_engine: Engine, migrated: dict
) -> dict:
    downgrade(seeded_alembic_config, PRIOR_REVISION)
    with seeded_engine.connect() as conn:
        mechanisms = dict(
            conn.execute(
                text(
                    "SELECT ionization_mechanism_id, ionization_mechanism "
                    "FROM ionization_mechanism"
                )
            ).all()
        )
    return {
        "version": _version(seeded_engine),
        "columns": _columns(seeded_engine),
        "mechanisms": mechanisms,
    }


def test_a_row_no_notation_reads_stops_the_upgrade_naming_it(refused: dict) -> None:
    assert _LABEL[0] in refused["error"]
    assert repr(_LABEL[1]) in refused["error"]
    assert refused["error"].startswith("1 ionization mechanism row(s)")
    assert "delete" in refused["error"]
    # Nothing was written: the revision is not reached and the table is as
    # it was.
    assert refused["version"] == PRIOR_REVISION
    assert not refused["columns"] & _NEW_COLUMNS


def test_every_row_is_filled_as_the_notation_module_reads_it(migrated: dict) -> None:
    assert set(migrated) == {mechanism_id for mechanism_id, *_ in _ROWS}
    for mechanism_id, mechanism, _polarity in _ROWS:
        row = to_row(mechanism)
        assert migrated[mechanism_id] == (
            mechanism,
            row.operation,
            row.species,
            row.species_charge,
            row.multimer_n,
            row.neutral_loss,
            None,
        ), mechanism_id


@pytest.mark.parametrize(
    ("mechanism", "columns"),
    [
        ("+K+", ("add", "K", 1, 1, None)),
        ("[M+H]+", ("add", "H", 1, 1, None)),
        ("[M-H]-", ("remove", "H", 1, 1, None)),
        ("[M-H]+", ("remove", "H", -1, 1, None)),
        ("[M+Br]-", ("add", "Br", -1, 1, None)),
        ("[M+CH4N2O+H]+", ("add", "(CH4N2O)H", 1, 1, None)),
        ("[M]+.", ("remove", "", -1, 1, None)),
        ("[M]-.", ("add", "", -1, 1, None)),
        ("[2M+H]+", ("add", "H", 1, 2, None)),
        ("[M+2H]2+", ("add", "(H)2", 2, 1, None)),
        ("[M+H-H2O]+", ("add", "H", 1, 1, "H2O")),
    ],
)
def test_the_columns_say_what_the_mechanism_does(
    migrated: dict, mechanism: str, columns: tuple
) -> None:
    (row,) = [row for row in migrated.values() if row[0] == mechanism]
    assert row[1:6] == columns


def test_a_row_written_after_holds_one_molecule_unless_it_says(
    migrated: dict, seeded_engine: Engine
) -> None:
    _seed(seeded_engine, [("mech-after", "[M+Li]+", "+")])
    with seeded_engine.begin() as conn:
        multimer = conn.execute(
            text(
                "SELECT multimer_n FROM ionization_mechanism "
                "WHERE ionization_mechanism_id = 'mech-after'"
            )
        ).scalar()
        conn.execute(
            text(
                "DELETE FROM ionization_mechanism "
                "WHERE ionization_mechanism_id = 'mech-after'"
            )
        )
    assert multimer == 1


def test_the_downgrade_drops_the_columns_and_keeps_the_mechanisms(
    downgraded: dict,
) -> None:
    assert downgraded["version"] == PRIOR_REVISION
    assert not downgraded["columns"] & _NEW_COLUMNS
    assert downgraded["mechanisms"] == {
        mechanism_id: mechanism for mechanism_id, mechanism, _ in _ROWS
    }


def test_the_upgrade_runs_again_after_the_downgrade(
    seeded_alembic_config: Config, seeded_engine: Engine, downgraded: dict
) -> None:
    upgrade(seeded_alembic_config, REVISION)
    assert _columns(seeded_engine) >= _NEW_COLUMNS
    assert _snapshot(seeded_engine)["mech-dimer"][4] == 2
