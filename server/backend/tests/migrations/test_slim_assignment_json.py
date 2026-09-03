"""Seeded test for the JSON rewrites in `21a7c103f3b0`.

The stairway walks the revision over an empty database, where both rewrites are
no-ops. What the revision is for only shows over rows: that a formula-only
alternative is packed and everything else is left a dict, in the order it had;
that a run's calibration is read off its rows once and the copies stripped from
every row while the rest of each row's provenance survives; that an
uncalibrated run records nothing; and that the downgrade puts both back in the
shape the engine used to write.

Rows are seeded at the previous revision through raw SQL rather than the ORM: a
migration test has to describe the schema as it was, not as the models are now -
and here in particular, the ORM type on `alternatives` would pack the seed.
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


# This checkout's migrations, not MASCOPE_PATH's - see conftest.BACKEND_PATH.
_ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"

REVISION = "21a7c103f3b0"
_SCRIPT = ScriptDirectory.from_config(Config(str(_ALEMBIC_INI))).get_revision(REVISION)
PRIOR_REVISION = _SCRIPT.down_revision


# --- Seed data -------------------------------------------------------------

_WORKSPACE_ID = "ws-saj"
_DATASET_ID = "ds-saj"
_SAMPLE_BATCH_ID = "sb-saj"
_SAMPLE_ITEM_ID = "si-saj-1"
_SAMPLE_FILE_ID = "sf-saj-1"

_CALIBRATED_RUN = "run-saj-cal"
_UNCALIBRATED_RUN = "run-saj-raw"

_CURVE = {"instrument": "orbi", "provisional": True, "source": "seeded curve"}

_FORMULA_ONLY = {
    "assigned_formula": "C10H14O8",
    "plausibility": 1.0,
    "source": "untargeted",
}
_FORMULA_ONLY_NULL_PLAUS = {
    "assigned_formula": "C9H12O9",
    "plausibility": None,
    "source": "untargeted",
}
_SCORED = {
    "assigned_formula": "C8H12O4",
    "ion_formula": "C8H12BrO4-",
    "fit_score": 0.27,
    "mz_error_ppm": -0.59,
    "plausibility": 1.0,
    "source": "database",
}
# Formula-only in every key but one: an entry an external engine published with
# a note of its own. Not the finder's shape, so it must stay a dict.
_ANNOTATED = {
    "assigned_formula": "C7H8O",
    "plausibility": 0.8,
    "source": "untargeted",
    "note": "published",
}
# Arrays an external engine published. `alternatives` is unvalidated client
# JSON, so a stored array need not be one this revision packed: the packed form
# is a string paired with a number or null, and anything else has to come back
# out of both directions as it went in.
_CLIENT_PAIR = ["C6H12O6", "isomer of the winner"]
_CLIENT_TRIPLE = ["C6H12O6", 0.9, {"note": "published"}]
# An imported row. `calibrated` is not in SERVER_OWNED_PROVENANCE_KEYS, so the
# import stores the publishing client's own key verbatim while stripping
# `p_correct` - and the downgrade can only restore rows that carry `p_correct`,
# so a strip that took this key would be one-way.
_IMPORTED_PROVENANCE = {"engine_note": "external", "calibrated": True}

# (peak_assignment_id, run_id, sample_peak_id, source, alternatives, provenance)
_ASSIGNMENTS = [
    (
        "pa-cal-db",
        _CALIBRATED_RUN,
        "p1",
        "database",
        [_SCORED],
        {
            "confidence": 1.0,
            "n_candidates": 1,
            "plausibility": 1.0,
            "evidence": 0.85,
            "is_tie": False,
            "score_version": 2,
            "p_correct": 0.91,
            "calibrated": True,
            "calibration": _CURVE,
            "corroboration": {"adducts": ["+H+", "+Na+"], "n_adducts": 2},
        },
    ),
    (
        "pa-cal-un",
        _CALIBRATED_RUN,
        "p2",
        "untargeted",
        [_FORMULA_ONLY, _SCORED, _FORMULA_ONLY_NULL_PLAUS, _ANNOTATED],
        {"plausibility": 1.0, "evidence": 0.42, "score_version": 2},
    ),
    ("pa-cal-none", _CALIBRATED_RUN, "p3", None, None, None),
    (
        "pa-raw-db",
        _UNCALIBRATED_RUN,
        "p1",
        "database",
        None,
        {
            "confidence": 1.0,
            "n_candidates": 1,
            "plausibility": 1.0,
            "evidence": 0.6,
            "is_tie": False,
            "score_version": 2,
            "p_correct": None,
            "calibrated": False,
            "calibration": None,
        },
    ),
    (
        "pa-cal-imp",
        _CALIBRATED_RUN,
        "p4",
        "database",
        [_CLIENT_PAIR, _CLIENT_TRIPLE],
        _IMPORTED_PROVENANCE,
    ),
]


# --- Seed SQL --------------------------------------------------------------

_WORKSPACE_SQL = """
    INSERT INTO workspace (workspace_id, workspace_name)
    VALUES (:id, 'Slim Assignment JSON')
"""

_DATASET_SQL = """
    INSERT INTO dataset (dataset_id, workspace_id, dataset_name)
    VALUES (:id, :workspace_id, 'Slim Assignment JSON')
"""

_SAMPLE_FILE_SQL = """
    INSERT INTO sample_file (sample_file_id, filename, instrument, "datetime",
                             datetime_utc, length, "range", polarity)
    VALUES (:id, :filename, 'instrument-x', :local, :utc, 60.0,
            CAST('[100.0, 500.0]' AS json), '+')
"""

_SAMPLE_BATCH_SQL = """
    INSERT INTO sample_batch (sample_batch_id, dataset_id, sample_batch_name)
    VALUES (:id, :dataset_id, 'Slim Assignment JSON')
"""

_SAMPLE_ITEM_SQL = """
    INSERT INTO sample_item (sample_item_id, sample_batch_id, sample_file_id,
                             sample_item_name, sample_item_type)
    VALUES (:id, :sample_batch_id, :sample_file_id, :id, 'sample')
"""

_RUN_SQL = """
    INSERT INTO peak_assignment_run (peak_assignment_run_id, sample_item_id,
                                     engine, engine_version, status)
    VALUES (:id, :sample_item_id, 'mascope', '0.3.0', 'completed')
"""

_ASSIGNMENT_SQL = """
    INSERT INTO peak_assignment (peak_assignment_id, peak_assignment_run_id,
                                 sample_item_id, sample_peak_id, sample_peak_mz,
                                 sample_peak_intensity, role, tier, source,
                                 alternatives, provenance)
    VALUES (:id, :run_id, :sample_item_id, :sample_peak_id, 100.0, 1.0,
            :role, :tier, :source, CAST(:alternatives AS json),
            CAST(:provenance AS json))
"""


def _seed(engine: Engine) -> None:
    """Insert one calibrated and one uncalibrated run, at PRIOR_REVISION.

    :param engine: Engine on the seeded test database.
    :type engine: Engine
    """
    local = datetime(2026, 9, 1, 12, 0)
    utc = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    with engine.begin() as conn:
        conn.execute(text(_WORKSPACE_SQL), {"id": _WORKSPACE_ID})
        conn.execute(
            text(_DATASET_SQL), {"id": _DATASET_ID, "workspace_id": _WORKSPACE_ID}
        )
        conn.execute(
            text(_SAMPLE_BATCH_SQL),
            {"id": _SAMPLE_BATCH_ID, "dataset_id": _DATASET_ID},
        )
        conn.execute(
            text(_SAMPLE_FILE_SQL),
            {
                "id": _SAMPLE_FILE_ID,
                "filename": f"{_SAMPLE_FILE_ID}.raw",
                "local": local,
                "utc": utc,
            },
        )
        conn.execute(
            text(_SAMPLE_ITEM_SQL),
            {
                "id": _SAMPLE_ITEM_ID,
                "sample_batch_id": _SAMPLE_BATCH_ID,
                "sample_file_id": _SAMPLE_FILE_ID,
            },
        )
        for run_id in (_CALIBRATED_RUN, _UNCALIBRATED_RUN):
            conn.execute(
                text(_RUN_SQL), {"id": run_id, "sample_item_id": _SAMPLE_ITEM_ID}
            )
        conn.execute(
            text(_ASSIGNMENT_SQL),
            [
                {
                    "id": assignment_id,
                    "run_id": run_id,
                    "sample_item_id": _SAMPLE_ITEM_ID,
                    "sample_peak_id": sample_peak_id,
                    "role": "unassigned" if source is None else "M0",
                    "tier": "unassigned" if source is None else "assigned",
                    "source": source,
                    # The SQL NULL / JSON null distinction is the engine's own:
                    # it writes the JSON literal for an absent blob.
                    "alternatives": json.dumps(alternatives),
                    "provenance": json.dumps(provenance),
                }
                for (
                    assignment_id,
                    run_id,
                    sample_peak_id,
                    source,
                    alternatives,
                    provenance,
                ) in _ASSIGNMENTS
            ],
        )


# --- Introspection ---------------------------------------------------------


def _rows(conn) -> dict[str, dict]:
    """Every seeded row's JSON, parsed, plus the stored text of alternatives."""
    rows = conn.execute(
        text(
            "SELECT peak_assignment_id, alternatives::text AS alternatives,"
            " provenance::text AS provenance FROM peak_assignment"
        )
    ).all()
    return {
        row.peak_assignment_id: {
            "alternatives_text": row.alternatives,
            "alternatives": json.loads(row.alternatives),
            "provenance": json.loads(row.provenance),
        }
        for row in rows
    }


def _runs(conn) -> dict[str, dict | None]:
    rows = conn.execute(
        text(
            "SELECT peak_assignment_run_id, confidence_calibration::text AS curve"
            " FROM peak_assignment_run"
        )
    ).all()
    return {
        row.peak_assignment_run_id: (
            None if row.curve is None else json.loads(row.curve)
        )
        for row in rows
    }


def _run_columns(conn) -> set[str]:
    return {
        row.column_name
        for row in conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_name = 'peak_assignment_run'"
            )
        ).all()
    }


# --- Fixtures --------------------------------------------------------------


@pytest.fixture(scope="module")
def migrated(seeded_alembic_config: Config, seeded_engine: Engine) -> dict:
    """Seed at the prior revision, upgrade, and read everything back.

    :return: The rows and runs as the upgrade left them.
    :rtype: dict
    """
    upgrade(seeded_alembic_config, PRIOR_REVISION)
    _seed(seeded_engine)
    upgrade(seeded_alembic_config, REVISION)
    with seeded_engine.connect() as conn:
        return {
            "version": conn.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar(),
            "rows": _rows(conn),
            "runs": _runs(conn),
        }


@pytest.fixture(scope="module")
def downgraded(
    seeded_alembic_config: Config, seeded_engine: Engine, migrated: dict
) -> dict:
    """Step back to the prior revision and read the rows again.

    :return: The rows as the downgrade left them, and the run table's columns.
    :rtype: dict
    """
    downgrade(seeded_alembic_config, PRIOR_REVISION)
    with seeded_engine.connect() as conn:
        return {"rows": _rows(conn), "run_columns": _run_columns(conn)}


# --- Upgrade ---------------------------------------------------------------


def test_upgrade_reaches_the_revision(migrated: dict) -> None:
    assert migrated["version"] == REVISION


def test_formula_only_alternatives_are_packed_in_place(migrated: dict) -> None:
    """The finder's shortlist entries become two-element lists; the scored
    contender and the annotated entry keep their dicts; the order holds."""
    packed = migrated["rows"]["pa-cal-un"]["alternatives"]
    assert packed == [
        ["C10H14O8", 1.0],
        _SCORED,
        ["C9H12O9", None],
        _ANNOTATED,
    ]


def test_a_row_with_nothing_to_pack_is_left_alone(migrated: dict) -> None:
    assert migrated["rows"]["pa-cal-db"]["alternatives"] == [_SCORED]
    # A JSON null blob is untouched, not turned into SQL NULL or an empty list.
    assert migrated["rows"]["pa-cal-none"]["alternatives_text"] == "null"


def test_the_run_records_the_curve_its_rows_carried(migrated: dict) -> None:
    assert migrated["runs"][_CALIBRATED_RUN] == _CURVE


def test_an_uncalibrated_run_records_nothing(migrated: dict) -> None:
    """Its rows carried `calibration: null`, which is not a curve."""
    assert migrated["runs"][_UNCALIBRATED_RUN] is None


def test_the_per_row_copies_are_stripped_and_nothing_else_is(migrated: dict) -> None:
    calibrated = migrated["rows"]["pa-cal-db"]["provenance"]
    assert "calibration" not in calibrated and "calibrated" not in calibrated
    assert calibrated["p_correct"] == pytest.approx(0.91)
    assert calibrated["corroboration"] == {"adducts": ["+H+", "+Na+"], "n_adducts": 2}
    assert calibrated["score_version"] == 2

    uncalibrated = migrated["rows"]["pa-raw-db"]["provenance"]
    assert "calibration" not in uncalibrated and "calibrated" not in uncalibrated
    assert uncalibrated["p_correct"] is None

    # An untargeted row never carried the pair; its blob is byte-for-byte the seed.
    assert migrated["rows"]["pa-cal-un"]["provenance"] == {
        "plausibility": 1.0,
        "evidence": 0.42,
        "score_version": 2,
    }
    assert migrated["rows"]["pa-cal-none"]["provenance"] is None


def test_an_importers_own_calibrated_key_is_not_stripped(migrated: dict) -> None:
    """`calibrated` is server-owned only beside a `p_correct` the server wrote.
    On an imported row it is the client's, stored verbatim by contract - and
    the downgrade restores only rows carrying `p_correct`, so taking it here
    would delete it for good."""
    assert migrated["rows"]["pa-cal-imp"]["provenance"] == _IMPORTED_PROVENANCE


def test_client_published_arrays_are_not_mistaken_for_packed_entries(
    migrated: dict,
) -> None:
    """Neither is the packed shape, so the pack leaves both as they are."""
    assert migrated["rows"]["pa-cal-imp"]["alternatives"] == [
        _CLIENT_PAIR,
        _CLIENT_TRIPLE,
    ]


# --- Downgrade -------------------------------------------------------------


def test_downgrade_expands_the_packed_alternatives(downgraded: dict) -> None:
    assert downgraded["rows"]["pa-cal-un"]["alternatives"] == [
        _FORMULA_ONLY,
        _SCORED,
        _FORMULA_ONLY_NULL_PLAUS,
        _ANNOTATED,
    ]


def test_downgrade_puts_the_pair_back_beside_p_correct(downgraded: dict) -> None:
    """Read off the run again, in the shape the engine used to write - for the
    uncalibrated run too, whose rows said `calibrated: false` explicitly."""
    calibrated = downgraded["rows"]["pa-cal-db"]["provenance"]
    assert calibrated["calibrated"] is True
    assert calibrated["calibration"] == _CURVE

    uncalibrated = downgraded["rows"]["pa-raw-db"]["provenance"]
    assert uncalibrated["calibrated"] is False
    assert uncalibrated["calibration"] is None

    # Rows without a `p_correct` never had the pair and do not get one.
    assert "calibrated" not in downgraded["rows"]["pa-cal-un"]["provenance"]
    assert "confidence_calibration" not in downgraded["run_columns"]


def test_downgrade_leaves_client_published_arrays_alone(downgraded: dict) -> None:
    """The unpack claims exactly what the pack produced - a string paired with
    a number or null. An array of any other shape is a client's, and rewriting
    it would drop elements and invent a source it never named."""
    assert downgraded["rows"]["pa-cal-imp"]["alternatives"] == [
        _CLIENT_PAIR,
        _CLIENT_TRIPLE,
    ]
    assert downgraded["rows"]["pa-cal-imp"]["provenance"] == _IMPORTED_PROVENANCE
