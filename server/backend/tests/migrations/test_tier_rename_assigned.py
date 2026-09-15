"""Seeded test for the 'identified' -> 'assigned' tier rename in `c1e7b409f2a5`.

Stairway and drift both walk the chain against a database created empty, so
they run this `upgrade()` with zero rows in any of the five columns it
rewrites: every statement matches nothing, every branch is trivially taken, and
a green migrations suite says nothing at all about it. The rename is the whole
revision - there is no DDL to fall back on as evidence - so it is exercised
here against a database that actually holds the old vocabulary.

Five sites, and the last two are the ones a reading of the tier columns misses:
`peak_assignment_run.tier_bands` carries the tier as a JSON object KEY, and
`peak_assignment_run.config` carries it inside the field named after it
(`identified_threshold`). Both columns are Postgres `json` rather than `jsonb`,
so the migration casts in and out of jsonb to edit them; a statement that
forgot the cast fails outright, and one that got the `||` operands the wrong
way round quietly keeps the legacy value over the current one. The `run-both`
row exists for exactly that.

Rows are seeded at the previous revision through raw SQL rather than the ORM: a
migration test has to describe the schema and the values as they were, not as
the models are today. The 'identified' literals below are that pre-rename
state, and are the one place in this suite where the word is still correct.
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

# The revision under test, read from the script directory. Its parent comes
# from there too, so re-parenting the migration does not silently seed at the
# wrong schema.
REVISION = "c1e7b409f2a5"
_SCRIPT = ScriptDirectory.from_config(Config(str(_ALEMBIC_INI))).get_revision(REVISION)
PRIOR_REVISION = _SCRIPT.down_revision


# --- Seed data -------------------------------------------------------------
#
# One row per behaviour, with readable ids: the assertions below quote them.
# Everything hangs off a single sample so the fixture seeds one FK chain
# (workspace -> dataset -> sample batch -> sample item, plus the sample file
# the item is derived from).

_WORKSPACE_ID = "ws-tier"
_DATASET_ID = "ds-tier"
_SAMPLE_BATCH_ID = "sb-tier"
_SAMPLE_ITEM_ID = "si-tier"
_SAMPLE_FILE_ID = "sf-tier"

# (peak_assignment_run_id, engine, config, tier_bands)
_RUNS = [
    # The shape both JSON columns were written in before the rename. This is
    # the run every assignment row below belongs to.
    (
        "run-legacy",
        "mascope",
        {
            "run_untargeted": True,
            "identified_threshold": 0.8,
            "candidate_threshold": 0.5,
        },
        {"identified": 0.8, "candidate": 0.5},
    ),
    # Already current: a rolling deployment can have a node writing the new
    # vocabulary before alembic runs, and those rows must come through as they
    # are rather than being rewritten twice.
    (
        "run-current",
        "mascope",
        {
            "run_untargeted": True,
            "assigned_threshold": 0.75,
            "candidate_threshold": 0.4,
        },
        {"assigned": 0.75, "candidate": 0.4},
    ),
    # Both columns NULL - a run predating the tier_bands column. The NULL the
    # WHERE guard has to skip rather than trip over.
    ("run-null", "peaky", None, None),
    # Neither key present: an external engine's opaque config, and a tier_bands
    # naming only the lower band. Nothing here is the migration's business, and
    # `candidate_threshold` is the near-miss it must not mistake for one.
    (
        "run-other",
        "peaky",
        {"engine_setting": 1, "candidate_threshold": 0.5},
        {"candidate": 0.5},
    ),
    # Both spellings in one object, which no Mascope writer produces but a
    # hand-assembled import can. That is one band named twice, not two bands.
    (
        "run-both",
        "peaky",
        {"identified_threshold": 0.9, "assigned_threshold": 0.8},
        {"identified": 0.9, "assigned": 0.8, "candidate": 0.5},
    ),
]

# (peak_assignment_id, sample_peak_id, role, tier, fit_score)
_ASSIGNMENTS = [
    ("pa-legacy", "p0001", "M0", "identified", 0.91),
    ("pa-child", "p0002", "iso_child", "identified", 0.87),
    ("pa-candidate", "p0003", "M0", "candidate", 0.62),
    ("pa-below", "p0004", "M0", "below_assignability", 0.21),
    ("pa-unassigned", "p0005", "unassigned", "unassigned", None),
    ("pa-reagent", "p0006", "reagent", "identified", 0.83),
    ("pa-current", "p0007", "M0", "assigned", 0.95),
]

# (batch_peak_id, consensus_tier)
_BATCH_PEAKS = [
    ("bp-legacy", "identified"),
    ("bp-candidate", "candidate"),
    ("bp-unassigned", "unassigned"),
    ("bp-current", "assigned"),
]

# (batch_peak_occurrence_id, batch_peak_id, tier). A batch peak holds at most
# one member per sample and everything here is one sample, so each occurrence
# gets a batch peak of its own; the pairing carries no other meaning.
_OCCURRENCES = [
    ("occ-legacy", "bp-legacy", "identified"),
    ("occ-null", "bp-candidate", None),
    ("occ-candidate", "bp-unassigned", "candidate"),
    ("occ-current", "bp-current", "assigned"),
]


# --- Seed SQL --------------------------------------------------------------
#
# `datetime` and `range` are quoted because they read as type names; the rest
# of each column list is spelled out so a NOT NULL column added later fails
# here loudly rather than silently defaulting.

_WORKSPACE_SQL = """
    INSERT INTO workspace (workspace_id, workspace_name)
    VALUES (:id, 'Tier Rename')
"""

_DATASET_SQL = """
    INSERT INTO dataset (dataset_id, workspace_id, dataset_name)
    VALUES (:id, :workspace_id, 'Tier Rename')
"""

_SAMPLE_FILE_SQL = """
    INSERT INTO sample_file (sample_file_id, filename, instrument, "datetime",
                             datetime_utc, length, "range", polarity)
    VALUES (:id, 'tier-rename.raw', 'instrument-x', :local, :utc, 60.0,
            CAST('[100.0, 500.0]' AS json), '+')
"""

_SAMPLE_BATCH_SQL = """
    INSERT INTO sample_batch (sample_batch_id, dataset_id, sample_batch_name)
    VALUES (:id, :dataset_id, 'Tier Rename')
"""

_SAMPLE_ITEM_SQL = """
    INSERT INTO sample_item (sample_item_id, sample_batch_id, sample_file_id,
                             sample_item_name, sample_item_type)
    VALUES (:id, :sample_batch_id, :sample_file_id, 'Tier Rename', 'sample')
"""

_RUN_SQL = """
    INSERT INTO peak_assignment_run (peak_assignment_run_id, sample_item_id,
                                     engine, engine_version, status, config,
                                     tier_bands)
    VALUES (:id, :sample_item_id, :engine, '0.2.0', 'completed',
            CAST(:config AS json), CAST(:tier_bands AS json))
"""

_ASSIGNMENT_SQL = """
    INSERT INTO peak_assignment (peak_assignment_id, peak_assignment_run_id,
                                 sample_item_id, sample_peak_id, sample_peak_mz,
                                 sample_peak_intensity, role, tier, fit_score)
    VALUES (:id, :run_id, :sample_item_id, :sample_peak_id, :mz, 1000.0,
            :role, :tier, :fit_score)
"""

_BATCH_PEAK_SQL = """
    INSERT INTO batch_peak (batch_peak_id, sample_batch_id, mz, mz_tol_ppm,
                            consensus_tier)
    VALUES (:id, :sample_batch_id, :mz, 5.0, :tier)
"""

_OCCURRENCE_SQL = """
    INSERT INTO batch_peak_occurrence (batch_peak_occurrence_id, batch_peak_id,
                                       sample_item_id, sample_peak_id,
                                       sample_peak_mz, tier)
    VALUES (:id, :batch_peak_id, :sample_item_id, :sample_peak_id, :mz, :tier)
"""


def _seed(engine: Engine) -> None:
    """Insert the pre-rename rows the migration has to act on.

    :param engine: Engine on the seeded test database, at PRIOR_REVISION.
    :type engine: Engine
    """
    with engine.begin() as conn:
        conn.execute(text(_WORKSPACE_SQL), {"id": _WORKSPACE_ID})
        conn.execute(
            text(_DATASET_SQL), {"id": _DATASET_ID, "workspace_id": _WORKSPACE_ID}
        )
        conn.execute(
            text(_SAMPLE_FILE_SQL),
            {
                "id": _SAMPLE_FILE_ID,
                "local": datetime(2026, 8, 1, 12, 0),
                "utc": datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc),
            },
        )
        conn.execute(
            text(_SAMPLE_BATCH_SQL),
            {"id": _SAMPLE_BATCH_ID, "dataset_id": _DATASET_ID},
        )
        conn.execute(
            text(_SAMPLE_ITEM_SQL),
            {
                "id": _SAMPLE_ITEM_ID,
                "sample_batch_id": _SAMPLE_BATCH_ID,
                "sample_file_id": _SAMPLE_FILE_ID,
            },
        )
        conn.execute(
            text(_RUN_SQL),
            [
                {
                    "id": run_id,
                    "sample_item_id": _SAMPLE_ITEM_ID,
                    "engine": engine_name,
                    # Bound as text and cast in SQL: psycopg2 adapts no Python
                    # dict on its own, and the column is `json`, which stores
                    # what it is given verbatim.
                    "config": None if config is None else json.dumps(config),
                    "tier_bands": None if bands is None else json.dumps(bands),
                }
                for run_id, engine_name, config, bands in _RUNS
            ],
        )
        conn.execute(
            text(_ASSIGNMENT_SQL),
            [
                {
                    "id": assignment_id,
                    "run_id": "run-legacy",
                    "sample_item_id": _SAMPLE_ITEM_ID,
                    "sample_peak_id": peak_id,
                    "mz": 100.0 + index,
                    "role": role,
                    "tier": tier,
                    "fit_score": fit_score,
                }
                for index, (assignment_id, peak_id, role, tier, fit_score) in enumerate(
                    _ASSIGNMENTS
                )
            ],
        )
        conn.execute(
            text(_BATCH_PEAK_SQL),
            [
                {
                    "id": batch_peak_id,
                    "sample_batch_id": _SAMPLE_BATCH_ID,
                    "mz": 200.0 + index,
                    "tier": tier,
                }
                for index, (batch_peak_id, tier) in enumerate(_BATCH_PEAKS)
            ],
        )
        conn.execute(
            text(_OCCURRENCE_SQL),
            [
                {
                    "id": occurrence_id,
                    "batch_peak_id": batch_peak_id,
                    "sample_item_id": _SAMPLE_ITEM_ID,
                    "sample_peak_id": f"q{index:04d}",
                    "mz": 200.0 + index,
                    "tier": tier,
                }
                for index, (occurrence_id, batch_peak_id, tier) in enumerate(
                    _OCCURRENCES
                )
            ],
        )


def _snapshot(engine: Engine) -> dict[str, dict]:
    """Read every rewritten site, plus the schema version, into plain dicts.

    Plain values rather than live rows on purpose: a snapshot taken after the
    upgrade stays readable after the downgrade has run, so the assertions below
    do not depend on the order pytest happens to collect them in.

    :param engine: Engine on the seeded test database.
    :type engine: Engine
    :return: The five sites keyed by row id, under 'tiers', 'roles',
             'batch_tiers', 'occurrence_tiers', 'bands' and 'configs', with the
             current 'version' alongside.
    :rtype: dict[str, dict]
    """
    with engine.connect() as conn:
        assignments = conn.execute(
            text("SELECT peak_assignment_id, role, tier FROM peak_assignment")
        ).all()
        batch_peaks = conn.execute(
            text("SELECT batch_peak_id, consensus_tier FROM batch_peak")
        ).all()
        occurrences = conn.execute(
            text("SELECT batch_peak_occurrence_id, tier FROM batch_peak_occurrence")
        ).all()
        runs = conn.execute(
            text(
                "SELECT peak_assignment_run_id, tier_bands, config"
                " FROM peak_assignment_run"
            )
        ).all()
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()

    return {
        "version": version,
        "tiers": {row.peak_assignment_id: row.tier for row in assignments},
        "roles": {row.peak_assignment_id: row.role for row in assignments},
        "batch_tiers": {row.batch_peak_id: row.consensus_tier for row in batch_peaks},
        "occurrence_tiers": {
            row.batch_peak_occurrence_id: row.tier for row in occurrences
        },
        "bands": {row.peak_assignment_run_id: row.tier_bands for row in runs},
        "configs": {row.peak_assignment_run_id: row.config for row in runs},
    }


@pytest.fixture(scope="module")
def migrated(seeded_alembic_config: Config, seeded_engine: Engine) -> dict[str, dict]:
    """Seed the old vocabulary at the prior revision, apply the rename, read back.

    :return: The snapshot as the upgrade left it.
    :rtype: dict[str, dict]
    """
    upgrade(seeded_alembic_config, PRIOR_REVISION)
    _seed(seeded_engine)
    upgrade(seeded_alembic_config, REVISION)
    return _snapshot(seeded_engine)


@pytest.fixture(scope="module")
def downgraded(
    seeded_alembic_config: Config, seeded_engine: Engine, migrated: dict[str, dict]
) -> dict[str, dict]:
    """Step back to the prior revision and read the same sites again.

    Depends on `migrated` so the upgrade is always the thing being undone, and
    returns a snapshot of its own - the two fixtures hand out values, not
    database state, so nothing here depends on which test runs first.

    :return: The snapshot as the downgrade left it.
    :rtype: dict[str, dict]
    """
    downgrade(seeded_alembic_config, PRIOR_REVISION)
    return _snapshot(seeded_engine)


# --- Upgrade ---------------------------------------------------------------


def test_upgrade_reaches_the_revision(migrated: dict[str, dict]) -> None:
    """The migration ran to completion over rows carrying the old vocabulary."""
    assert migrated["version"] == REVISION


def test_assignment_tiers_are_renamed(migrated: dict[str, dict]) -> None:
    """Every 'identified' peak assignment now reads 'assigned'."""
    assert migrated["tiers"]["pa-legacy"] == "assigned"
    assert migrated["tiers"]["pa-child"] == "assigned"
    assert migrated["tiers"]["pa-reagent"] == "assigned"


def test_other_tiers_are_left_alone(migrated: dict[str, dict]) -> None:
    """The three tiers this rename does not touch come through unchanged.

    Only the top tier overclaimed; 'candidate' and 'below_assignability' say
    what they mean already, and 'unassigned' is the absence of a claim.
    A row already written as 'assigned' is left alone too - it is at the
    destination, and rewriting it would be a second rename of one value.
    """
    assert migrated["tiers"]["pa-candidate"] == "candidate"
    assert migrated["tiers"]["pa-below"] == "below_assignability"
    assert migrated["tiers"]["pa-unassigned"] == "unassigned"
    assert migrated["tiers"]["pa-current"] == "assigned"


def test_role_vocabulary_is_untouched(migrated: dict[str, dict]) -> None:
    """Roles are a separate vocabulary that shares two words with the tiers.

    'unassigned' is both a tier and a role, and 'reagent' and 'artifact' are
    roles that read like confidence statements without being any. None of them
    is part of this rename, so every role survives exactly as seeded.
    """
    assert migrated["roles"] == {
        assignment_id: role for assignment_id, _peak, role, _tier, _fit in _ASSIGNMENTS
    }


def test_batch_peak_consensus_tiers_are_renamed(migrated: dict[str, dict]) -> None:
    """The batch-level roll-up carries the same vocabulary and is renamed with it."""
    assert migrated["batch_tiers"] == {
        "bp-legacy": "assigned",
        "bp-candidate": "candidate",
        "bp-unassigned": "unassigned",
        "bp-current": "assigned",
    }


def test_occurrence_tiers_are_renamed(migrated: dict[str, dict]) -> None:
    """The per-sample members are renamed, and a member with no tier keeps none.

    `batch_peak_occurrence.tier` is the one nullable tier column: an occurrence
    folded in from a peak that was never assigned has NULL there, which is not
    the 'unassigned' tier and must not become one.
    """
    assert migrated["occurrence_tiers"] == {
        "occ-legacy": "assigned",
        "occ-null": None,
        "occ-candidate": "candidate",
        "occ-current": "assigned",
    }


def test_tier_bands_key_is_renamed(migrated: dict[str, dict]) -> None:
    """The upper band is keyed by tier, so the key moves with the vocabulary.

    A `json` column edited without the cast to jsonb would have failed the
    upgrade outright; one whose value did not follow the key would leave the
    band silently at the wrong threshold.
    """
    assert migrated["bands"]["run-legacy"] == {"assigned": 0.8, "candidate": 0.5}


def test_config_threshold_key_is_renamed(migrated: dict[str, dict]) -> None:
    """The stored run config names its upper band after the tier, and follows it.

    This is the site nothing in the running server re-parses, which is what
    makes it easy to leave behind: the rest of the config has to come through
    untouched around it.
    """
    assert migrated["configs"]["run-legacy"] == {
        "run_untargeted": True,
        "assigned_threshold": 0.8,
        "candidate_threshold": 0.5,
    }


def test_runs_without_the_old_key_are_untouched(migrated: dict[str, dict]) -> None:
    """A run that never carried the old spelling is not rewritten at all.

    Three shapes, all common: NULL columns from a run predating tier_bands, an
    external engine's opaque config that shares no key with Mascope's, and a
    run already written in the current vocabulary. `candidate_threshold` is the
    near-miss - it ends the same way as the key being renamed and is not it.
    """
    assert migrated["bands"]["run-null"] is None
    assert migrated["configs"]["run-null"] is None
    assert migrated["bands"]["run-other"] == {"candidate": 0.5}
    assert migrated["configs"]["run-other"] == {
        "engine_setting": 1,
        "candidate_threshold": 0.5,
    }
    assert migrated["bands"]["run-current"] == {"assigned": 0.75, "candidate": 0.4}
    assert migrated["configs"]["run-current"] == {
        "run_untargeted": True,
        "assigned_threshold": 0.75,
        "candidate_threshold": 0.4,
    }


def test_both_spellings_collapse_onto_the_current_one(
    migrated: dict[str, dict],
) -> None:
    """An object carrying both keys keeps the current one's value, not the legacy one.

    Both spellings name one band, so the current entry wins and the legacy key
    is dropped rather than kept beside it - the same rule the server applies to
    a payload that arrives with both. Getting the `||` operands the wrong way
    round passes every other assertion here and silently restores the retired
    threshold.
    """
    assert migrated["bands"]["run-both"] == {"assigned": 0.8, "candidate": 0.5}
    assert migrated["configs"]["run-both"] == {"assigned_threshold": 0.8}


# --- Downgrade -------------------------------------------------------------


def test_downgrade_restores_the_old_vocabulary(downgraded: dict[str, dict]) -> None:
    """Stepping back puts every site into the spelling the older code reads."""
    assert downgraded["version"] == PRIOR_REVISION
    assert downgraded["tiers"]["pa-legacy"] == "identified"
    assert downgraded["batch_tiers"]["bp-legacy"] == "identified"
    assert downgraded["occurrence_tiers"]["occ-legacy"] == "identified"
    assert downgraded["bands"]["run-legacy"] == {"identified": 0.8, "candidate": 0.5}
    assert downgraded["configs"]["run-legacy"] == {
        "run_untargeted": True,
        "identified_threshold": 0.8,
        "candidate_threshold": 0.5,
    }


def test_downgrade_also_rewrites_rows_written_natively_as_assigned(
    downgraded: dict[str, dict],
) -> None:
    """Rows that were never 'identified' are moved back with the rest.

    Nothing distinguishes a row the upgrade rewrote from one a node running the
    new code wrote itself, and the code being restored does not understand
    'assigned' either way - so the inverse is the whole column, not the subset
    the upgrade happened to touch. `run-both` shows the cost of that being a
    mapping rather than an undo: its retired 0.9 was collapsed on the way up
    and does not come back.
    """
    assert downgraded["tiers"]["pa-current"] == "identified"
    assert downgraded["batch_tiers"]["bp-current"] == "identified"
    assert downgraded["occurrence_tiers"]["occ-current"] == "identified"
    assert downgraded["bands"]["run-current"] == {"identified": 0.75, "candidate": 0.4}
    assert downgraded["bands"]["run-both"] == {"identified": 0.8, "candidate": 0.5}
    assert downgraded["configs"]["run-both"] == {"identified_threshold": 0.8}


def test_downgrade_leaves_the_other_tiers_alone(downgraded: dict[str, dict]) -> None:
    """Only the one word maps back; everything outside it is still untouched."""
    assert downgraded["tiers"]["pa-candidate"] == "candidate"
    assert downgraded["tiers"]["pa-below"] == "below_assignability"
    assert downgraded["tiers"]["pa-unassigned"] == "unassigned"
    assert downgraded["occurrence_tiers"]["occ-null"] is None
    assert downgraded["roles"] == {
        assignment_id: role for assignment_id, _peak, role, _tier, _fit in _ASSIGNMENTS
    }
    assert downgraded["bands"]["run-null"] is None
    assert downgraded["configs"]["run-null"] is None
    assert downgraded["bands"]["run-other"] == {"candidate": 0.5}
    assert downgraded["configs"]["run-other"] == {
        "engine_setting": 1,
        "candidate_threshold": 0.5,
    }
