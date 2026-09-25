"""Seeded test for the move to the standard adduct notation in `5193d1e942e0`.

The stairway and drift tests walk the chain against a database created empty,
so they run this revision with no mechanism and no calibration to rewrite, and
a green migrations suite says nothing about it. It is exercised here against a
database holding every legacy spelling a deployment stores, and the rows the
map has to leave alone: a mechanism written natively in the standard notation,
a free-text label, and a pair whose rewrite would take a spelling the other
already holds.

The migration restates the map rather than importing it from mascope_tools, so
the first tests pin the two to agree on every spelling: a migration that wrote
one spelling and a reader that expected another would pass everything else here.

Rows are seeded at the previous revision through raw SQL rather than the ORM,
which would read a stored mechanism back in the standard notation whatever the
row holds.
"""

import importlib.util
import json
from pathlib import Path

import pytest
from alembic.command import downgrade, upgrade
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import Engine

from mascope_tools.composition.mechanism_notation import (
    legacy_notation,
    standard_notation,
)


# This checkout's migrations, not MASCOPE_PATH's - see conftest.BACKEND_PATH.
_ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"

REVISION = "5193d1e942e0"
_SCRIPT = ScriptDirectory.from_config(Config(str(_ALEMBIC_INI))).get_revision(REVISION)
PRIOR_REVISION = _SCRIPT.down_revision

# The revision's own module, for its map.
_SPEC = importlib.util.spec_from_file_location("notation_revision", _SCRIPT.path)
_MIGRATION = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MIGRATION)


#: Every legacy spelling the fleet's servers store, with its standard one and
#: the polarity it is stored under.
_STORED = [
    ("+", "[M]+.", "+"),
    ("-", "[M]-.", "-"),
    ("+H+", "[M+H]+", "+"),
    ("-H+", "[M-H]-", "-"),
    ("+Br-", "[M+Br]-", "-"),
    ("+Br2-", "[M+Br2]-", "-"),
    ("+Br3-", "[M+Br3]-", "-"),
    ("+I-", "[M+I]-", "-"),
    ("+I2-", "[M+I2]-", "-"),
    ("+I3-", "[M+I3]-", "-"),
    ("+NO3-", "[M+NO3]-", "-"),
    ("+^NO3-", "[M+^NO3]-", "-"),
    ("+CO3-", "[M+CO3]-", "-"),
    ("+HSO4-", "[M+HSO4]-", "-"),
    ("+(HNO3)NO3-", "[M+HNO3+NO3]-", "-"),
    ("+^NH4+", "[M+^NH4]+", "+"),
    ("+Na+", "[M+Na]+", "+"),
    ("+C4H11N+", "[M+C4H11N]+", "+"),
    ("+(CH4N2O)H+", "[M+CH4N2O+H]+", "+"),
    ("+(CH4N2O)2H+", "[M+(CH4N2O)2H]+", "+"),
    ("+(C3H6O)H+", "[M+C3H6O+H]+", "+"),
    ("+(C6H10O2)H+", "[M+C6H10O2+H]+", "+"),
    ("+(C6H15N)H+", "[M+C6H15N+H]+", "+"),
]

#: Spellings the map must carry both ways that no deployment stores yet.
_UNUSUAL = [
    ("-H-", "[M-H]+"),
    ("+[15N]O3-", "[M+[15N]O3]-"),
    ("+((CH3CH2)2NH)H+", "[M+(CH3CH2)2NH+H]+"),
    ("+(A)(B)+", "[M+A+(B)]+"),
    ("+(CH4N2O)+", "[M+(CH4N2O)]+"),
]

# The rows the map must leave alone, and why. The ammonium pair would collide:
# the legacy row cannot take the spelling the standard one already holds. Ids
# fit the 16-character column.
_NATIVE = ("mech-native", "[M-CH3]+", "+")
_LABEL = ("mech-label", "+H+ (a free-text label)", "+")
_PAIR_LEGACY = ("mech-pair-old", "+NH4+", "+")
_PAIR_STANDARD = ("mech-pair-new", "[M+NH4]+", "+")

# (id, corroboration_weights) - the keys are mechanisms.
_CALIBRATIONS = [
    (1, {"+Br-": 2.28, "+NH4+": 0.83, "+(CH4N2O)H+": 0.7, "-H+": 0.0}),
    # Both spellings of one adduct: the one already standard is kept.
    (2, {"+Br-": 1.0, "[M+Br]-": 2.0, "unknown": 0.1}),
    (3, None),
]


def _mechanism_rows():
    rows = [
        (f"mech-{index:02d}", legacy, polarity)
        for index, (legacy, _standard, polarity) in enumerate(_STORED)
    ]
    return rows + [_NATIVE, _LABEL, _PAIR_LEGACY, _PAIR_STANDARD]


@pytest.mark.parametrize(
    ("legacy", "standard"), [row[:2] for row in _STORED] + _UNUSUAL
)
def test_the_migration_writes_what_the_library_reads(legacy, standard):
    assert _MIGRATION.to_standard(legacy) == standard == standard_notation(legacy)
    assert _MIGRATION.to_legacy(standard) == legacy == legacy_notation(standard)


@pytest.mark.parametrize(
    "neither", ["+H+ (a free-text label)", "H+", "++", "[M]+", "[M+Na-2H]-", ""]
)
def test_the_migration_reads_neither_notation_as_neither(neither):
    assert _MIGRATION.to_standard(neither) is None
    assert _MIGRATION.to_legacy(neither) is None


def _seed(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO ionization_mechanism (ionization_mechanism_id, "
                "ionization_mechanism_polarity, ionization_mechanism) "
                "VALUES (:id, :polarity, :mechanism)"
            ),
            [
                {"id": mechanism_id, "polarity": polarity, "mechanism": mechanism}
                for mechanism_id, mechanism, polarity in _mechanism_rows()
            ],
        )
        conn.execute(
            text(
                "INSERT INTO assignment_calibration (assignment_calibration_id, "
                "instrument, score_version, a, b, n_pos, n_neg, provisional, "
                "corroboration_weights, is_active, created_utc) "
                "VALUES (:id, 'orbi', 1, 5.7, -3.4, 0, 0, true, "
                "CAST(:weights AS json), false, now())"
            ),
            [
                {
                    "id": calibration_id,
                    # Bound as text and cast in SQL: the column is `json`.
                    "weights": None if weights is None else json.dumps(weights),
                }
                for calibration_id, weights in _CALIBRATIONS
            ],
        )


def _snapshot(engine: Engine) -> dict:
    with engine.connect() as conn:
        mechanisms = conn.execute(
            text(
                "SELECT ionization_mechanism_id, ionization_mechanism "
                "FROM ionization_mechanism"
            )
        ).all()
        weights = conn.execute(
            text(
                "SELECT assignment_calibration_id, corroboration_weights "
                "FROM assignment_calibration"
            )
        ).all()
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    return {
        "version": version,
        "mechanisms": dict(mechanisms),
        "weights": {row[0]: row[1] for row in weights},
    }


@pytest.fixture(scope="module")
def migrated(seeded_alembic_config: Config, seeded_engine: Engine) -> dict:
    upgrade(seeded_alembic_config, PRIOR_REVISION)
    _seed(seeded_engine)
    upgrade(seeded_alembic_config, REVISION)
    return _snapshot(seeded_engine)


@pytest.fixture(scope="module")
def downgraded(
    seeded_alembic_config: Config, seeded_engine: Engine, migrated: dict
) -> dict:
    downgrade(seeded_alembic_config, PRIOR_REVISION)
    return _snapshot(seeded_engine)


def test_upgrade_reaches_the_revision(migrated: dict) -> None:
    assert migrated["version"] == REVISION


def test_every_stored_spelling_is_rewritten(migrated: dict) -> None:
    for index, (_legacy, standard, _polarity) in enumerate(_STORED):
        assert migrated["mechanisms"][f"mech-{index:02d}"] == standard


def test_what_the_map_cannot_rewrite_is_left_as_it_is(migrated: dict) -> None:
    for mechanism_id, mechanism, _polarity in (_NATIVE, _LABEL):
        assert migrated["mechanisms"][mechanism_id] == mechanism
    # The legacy row of the pair stays legacy rather than collide.
    assert migrated["mechanisms"][_PAIR_LEGACY[0]] == "+NH4+"
    assert migrated["mechanisms"][_PAIR_STANDARD[0]] == "[M+NH4]+"


def test_the_weights_are_keyed_in_the_standard_notation(migrated: dict) -> None:
    assert migrated["weights"][1] == {
        "[M+Br]-": 2.28,
        "[M+NH4]+": 0.83,
        "[M+CH4N2O+H]+": 0.7,
        "[M-H]-": 0.0,
    }
    # The standard key was there already and wins; the unreadable one stays.
    assert migrated["weights"][2] == {"[M+Br]-": 2.0, "unknown": 0.1}
    assert migrated["weights"][3] is None


def test_downgrade_restores_every_stored_spelling(downgraded: dict) -> None:
    assert downgraded["version"] == PRIOR_REVISION
    for index, (legacy, _standard, _polarity) in enumerate(_STORED):
        assert downgraded["mechanisms"][f"mech-{index:02d}"] == legacy
    assert downgraded["weights"][1] == dict(_CALIBRATIONS[0][1])


def test_downgrade_writes_a_native_row_in_the_legacy_notation(
    downgraded: dict,
) -> None:
    """The code a downgrade restores reads the legacy notation only, so a row
    written natively in the standard one is rewritten too."""
    assert downgraded["mechanisms"][_NATIVE[0]] == "-CH3-"
    assert downgraded["mechanisms"][_LABEL[0]] == _LABEL[1]
    # The pair collides the other way now: the standard row stays standard.
    assert downgraded["mechanisms"][_PAIR_LEGACY[0]] == "+NH4+"
    assert downgraded["mechanisms"][_PAIR_STANDARD[0]] == "[M+NH4]+"
    assert downgraded["weights"][2] == {"+Br-": 2.0, "unknown": 0.1}
