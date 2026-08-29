"""Seeded test for the batch-peak ledger columns added in `b6a4d1e83c7f`.

Stairway and drift both walk the chain against a database created empty, so the
two backfills in this revision run over zero rows there and a green migrations
suite says nothing about either. The DDL is trivial - two nullable columns - and
the whole point of the revision is what it puts IN them: a batch folded before
it must fold its isotopologue satellites and sort by intensity without anyone
pressing "Compute batch peaks" first.

The satellite backfill is the half worth seeding. It is the same vote the
consensus now casts at fold time, expressed once in SQL over the whole table: a
batch peak is a satellite when a strict majority of its ASSIGNED members are
`iso_child` rows whose owning assignment has an occurrence of its own in the
SAME sample, and they all name one anchor. Every clause there is a way to get it
wrong, so the seed below holds one batch peak per way:

- `bp-sat`  - both members agree; the plain case.
- `bp-flip` - a satellite in one sample, assigned in its own right in the other:
              no majority, so the link must not be drawn.
- `bp-lost` - an `iso_child` whose owner was dropped from the fold and so has no
              occurrence: no anchor to point at.
- `bp-bare` - unassigned members, one of them with no assignment row left at
              all: neither a satellite nor a NULL intensity read as zero.

Rows are seeded at the previous revision through raw SQL rather than the ORM: a
migration test has to describe the schema as it was, not as the models are now.
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

REVISION = "b6a4d1e83c7f"
_SCRIPT = ScriptDirectory.from_config(Config(str(_ALEMBIC_INI))).get_revision(REVISION)
PRIOR_REVISION = _SCRIPT.down_revision


# --- Seed data -------------------------------------------------------------

_WORKSPACE_ID = "ws-bpl"
_DATASET_ID = "ds-bpl"
_SAMPLE_BATCH_ID = "sb-bpl"

# (sample_item_id, sample_file_id)
_SAMPLES = [("si-bpl-1", "sf-bpl-1"), ("si-bpl-2", "sf-bpl-2")]
_RUN_ID = "run-bpl"

# (peak_assignment_id, sample_item_id, sample_peak_id, mz, intensity, role,
#  assigned_formula, owner_peak_assignment_id)
#
# `pa-ghost` is the owner that never made it into the fold: a real assignment
# row with no occurrence, which is what a peak dropped from a contested anchor
# leaves behind.
_ASSIGNMENTS = [
    ("pa-m0-1", "si-bpl-1", "p1", 181.0707, 5000.0, "M0", "C6H12O6", None),
    ("pa-sat-1", "si-bpl-1", "p2", 182.0741, 350.0, "iso_child", "C6H12O6", "pa-m0-1"),
    ("pa-solo-1", "si-bpl-1", "p3", 299.1900, 2000.0, "M0", "C12H18O5", None),
    (
        "pa-flip-1",
        "si-bpl-1",
        "p4",
        300.1930,
        150.0,
        "iso_child",
        "C12H18O5",
        "pa-solo-1",
    ),
    ("pa-bare-1", "si-bpl-1", "p5", 250.1000, 300.0, "unassigned", None, None),
    ("pa-ghost-1", "si-bpl-1", "p6", 499.0000, 60.0, "M0", "C9H10", None),
    ("pa-lost-1", "si-bpl-1", "p7", 500.0000, 80.0, "iso_child", "C9H10", "pa-ghost-1"),
    (
        "pa-blank-1",
        "si-bpl-1",
        "p8",
        183.0000,
        40.0,
        "iso_child",
        "C6H12O6",
        "pa-m0-1",
    ),
    ("pa-m0-2", "si-bpl-2", "p1", 181.0707, 6000.0, "M0", "C6H12O6", None),
    ("pa-sat-2", "si-bpl-2", "p2", 182.0741, 500.0, "iso_child", "C6H12O6", "pa-m0-2"),
    ("pa-flip-2", "si-bpl-2", "p4", 300.1930, 900.0, "M0", "C13H24O3", None),
    ("pa-blank-2", "si-bpl-2", "p8", 183.0000, 30.0, "M0", "", None),
]

# (batch_peak_id, mz)
_BATCH_PEAKS = [
    ("bp-m0", 181.0707),
    ("bp-sat", 182.0741),
    ("bp-solo", 299.1900),
    ("bp-flip", 300.1930),
    ("bp-bare", 250.1000),
    ("bp-lost", 500.0000),
    ("bp-blank", 183.0000),
]

# (batch_peak_occurrence_id, batch_peak_id, sample_item_id, peak_assignment_id,
#  sample_peak_id, intensity, assigned_formula)
#
# `occ-solo-2` carries no intensity and no formula: the member that must be
# skipped by the max rather than counted as zero, and left out of the vote's
# denominator rather than counted as evidence against a satellite.
# `occ-bare-2` carries no assignment at all - the state an occurrence is left in
# when the run behind it is pruned (the FK is ON DELETE SET NULL) - and the
# join must step over it rather than drop the batch peak.
# `occ-blank-2` carries the empty string as its formula, which is not a formula:
# the SQL says so with `<> ''` and the Python says so by testing truthiness, and
# the two have to agree or a backfilled row disagrees with its own next fold.
_OCCURRENCES = [
    ("occ-m0-1", "bp-m0", "si-bpl-1", "pa-m0-1", "p1", 5000.0, "C6H12O6"),
    ("occ-m0-2", "bp-m0", "si-bpl-2", "pa-m0-2", "p1", 6000.0, "C6H12O6"),
    ("occ-sat-1", "bp-sat", "si-bpl-1", "pa-sat-1", "p2", 350.0, "C6H12O6"),
    ("occ-sat-2", "bp-sat", "si-bpl-2", "pa-sat-2", "p2", 500.0, "C6H12O6"),
    ("occ-solo-1", "bp-solo", "si-bpl-1", "pa-solo-1", "p3", 2000.0, "C12H18O5"),
    ("occ-solo-2", "bp-solo", "si-bpl-2", None, "p3", None, None),
    ("occ-flip-1", "bp-flip", "si-bpl-1", "pa-flip-1", "p4", 150.0, "C12H18O5"),
    ("occ-flip-2", "bp-flip", "si-bpl-2", "pa-flip-2", "p4", 900.0, "C13H24O3"),
    ("occ-bare-1", "bp-bare", "si-bpl-1", "pa-bare-1", "p5", 300.0, None),
    ("occ-bare-2", "bp-bare", "si-bpl-2", None, "p5", 100.0, None),
    ("occ-lost-1", "bp-lost", "si-bpl-1", "pa-lost-1", "p7", 80.0, "C9H10"),
    ("occ-blank-1", "bp-blank", "si-bpl-1", "pa-blank-1", "p8", 40.0, "C6H12O6"),
    ("occ-blank-2", "bp-blank", "si-bpl-2", "pa-blank-2", "p8", 30.0, ""),
]


# --- Seed SQL --------------------------------------------------------------
#
# `datetime` and `range` are quoted because they read as type names; the rest of
# each column list is spelled out so a NOT NULL column added later fails here
# loudly rather than silently defaulting.

_WORKSPACE_SQL = """
    INSERT INTO workspace (workspace_id, workspace_name)
    VALUES (:id, 'Batch Peak Ledger')
"""

_DATASET_SQL = """
    INSERT INTO dataset (dataset_id, workspace_id, dataset_name)
    VALUES (:id, :workspace_id, 'Batch Peak Ledger')
"""

_SAMPLE_FILE_SQL = """
    INSERT INTO sample_file (sample_file_id, filename, instrument, "datetime",
                             datetime_utc, length, "range", polarity)
    VALUES (:id, :filename, 'instrument-x', :local, :utc, 60.0,
            CAST('[100.0, 500.0]' AS json), '+')
"""

_SAMPLE_BATCH_SQL = """
    INSERT INTO sample_batch (sample_batch_id, dataset_id, sample_batch_name)
    VALUES (:id, :dataset_id, 'Batch Peak Ledger')
"""

_SAMPLE_ITEM_SQL = """
    INSERT INTO sample_item (sample_item_id, sample_batch_id, sample_file_id,
                             sample_item_name, sample_item_type)
    VALUES (:id, :sample_batch_id, :sample_file_id, :id, 'sample')
"""

_RUN_SQL = """
    INSERT INTO peak_assignment_run (peak_assignment_run_id, sample_item_id,
                                     engine, engine_version, status)
    VALUES (:id, :sample_item_id, 'mascope', '0.2.0', 'completed')
"""

_ASSIGNMENT_SQL = """
    INSERT INTO peak_assignment (peak_assignment_id, peak_assignment_run_id,
                                 sample_item_id, sample_peak_id, sample_peak_mz,
                                 sample_peak_intensity, role, assigned_formula,
                                 tier, owner_peak_assignment_id)
    VALUES (:id, :run_id, :sample_item_id, :sample_peak_id, :mz, :intensity,
            :role, :assigned_formula, :tier, :owner_id)
"""

_BATCH_PEAK_SQL = """
    INSERT INTO batch_peak (batch_peak_id, sample_batch_id, mz, mz_tol_ppm,
                            intensity_variable, consensus_tier)
    VALUES (:id, :sample_batch_id, :mz, 5.0, 'sum_peak_heights', 'assigned')
"""

_OCCURRENCE_SQL = """
    INSERT INTO batch_peak_occurrence (batch_peak_occurrence_id, batch_peak_id,
                                       sample_item_id, peak_assignment_id,
                                       sample_peak_id, sample_peak_mz,
                                       intensity, assigned_formula)
    VALUES (:id, :batch_peak_id, :sample_item_id, :peak_assignment_id,
            :sample_peak_id, :mz, :intensity, :assigned_formula)
"""

# One run per sample, so each sample's assignments hang off their own.
_RUN_IDS = {
    sample_item_id: f"{_RUN_ID}-{i}" for i, (sample_item_id, _) in enumerate(_SAMPLES)
}


def _seed(engine: Engine) -> None:
    """Insert the batch peaks, members and family links the backfill acts on.

    :param engine: Engine on the seeded test database, at PRIOR_REVISION.
    :type engine: Engine
    """
    local = datetime(2026, 8, 1, 12, 0)
    utc = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    with engine.begin() as conn:
        conn.execute(text(_WORKSPACE_SQL), {"id": _WORKSPACE_ID})
        conn.execute(
            text(_DATASET_SQL), {"id": _DATASET_ID, "workspace_id": _WORKSPACE_ID}
        )
        conn.execute(
            text(_SAMPLE_BATCH_SQL),
            {"id": _SAMPLE_BATCH_ID, "dataset_id": _DATASET_ID},
        )
        for sample_item_id, sample_file_id in _SAMPLES:
            conn.execute(
                text(_SAMPLE_FILE_SQL),
                {
                    "id": sample_file_id,
                    "filename": f"{sample_file_id}.raw",
                    "local": local,
                    "utc": utc,
                },
            )
            conn.execute(
                text(_SAMPLE_ITEM_SQL),
                {
                    "id": sample_item_id,
                    "sample_batch_id": _SAMPLE_BATCH_ID,
                    "sample_file_id": sample_file_id,
                },
            )
            conn.execute(
                text(_RUN_SQL),
                {"id": _RUN_IDS[sample_item_id], "sample_item_id": sample_item_id},
            )
        # Owners before the rows that name them: the FK is self-referential.
        conn.execute(
            text(_ASSIGNMENT_SQL),
            [
                {
                    "id": assignment_id,
                    "run_id": _RUN_IDS[sample_item_id],
                    "sample_item_id": sample_item_id,
                    "sample_peak_id": sample_peak_id,
                    "mz": mz,
                    "intensity": intensity,
                    "role": role,
                    "assigned_formula": assigned_formula,
                    "tier": "unassigned" if assigned_formula is None else "assigned",
                    "owner_id": owner_id,
                }
                for (
                    assignment_id,
                    sample_item_id,
                    sample_peak_id,
                    mz,
                    intensity,
                    role,
                    assigned_formula,
                    owner_id,
                ) in sorted(_ASSIGNMENTS, key=lambda row: row[7] is not None)
            ],
        )
        conn.execute(
            text(_BATCH_PEAK_SQL),
            [
                {"id": batch_peak_id, "sample_batch_id": _SAMPLE_BATCH_ID, "mz": mz}
                for batch_peak_id, mz in _BATCH_PEAKS
            ],
        )
        conn.execute(
            text(_OCCURRENCE_SQL),
            [
                {
                    "id": occurrence_id,
                    "batch_peak_id": batch_peak_id,
                    "sample_item_id": sample_item_id,
                    "peak_assignment_id": peak_assignment_id,
                    "sample_peak_id": sample_peak_id,
                    "mz": 100.0,
                    "intensity": intensity,
                    "assigned_formula": assigned_formula,
                }
                for (
                    occurrence_id,
                    batch_peak_id,
                    sample_item_id,
                    peak_assignment_id,
                    sample_peak_id,
                    intensity,
                    assigned_formula,
                ) in _OCCURRENCES
            ],
        )


def _snapshot(engine: Engine) -> dict:
    """Read the two backfilled columns, plus the schema version.

    :param engine: Engine on the seeded test database.
    :type engine: Engine
    :return: 'satellites' and 'intensities' keyed by batch_peak_id, with the
             current 'version' alongside.
    :rtype: dict
    """
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT batch_peak_id, satellite_of, max_intensity FROM batch_peak"
                " ORDER BY batch_peak_id"
            )
        ).all()
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()

    return {
        "version": version,
        "satellites": {row.batch_peak_id: row.satellite_of for row in rows},
        "intensities": {row.batch_peak_id: row.max_intensity for row in rows},
    }


def _columns(engine: Engine) -> set[str]:
    """The column names `batch_peak` currently has."""
    with engine.connect() as conn:
        return {
            row.column_name
            for row in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns"
                    " WHERE table_name = 'batch_peak'"
                )
            ).all()
        }


@pytest.fixture(scope="module")
def migrated(seeded_alembic_config: Config, seeded_engine: Engine) -> dict:
    """Seed a folded batch at the prior revision, apply the backfill, read back.

    :return: The snapshot as the upgrade left it.
    :rtype: dict
    """
    upgrade(seeded_alembic_config, PRIOR_REVISION)
    _seed(seeded_engine)
    upgrade(seeded_alembic_config, REVISION)
    return _snapshot(seeded_engine)


@pytest.fixture(scope="module")
def downgraded(
    seeded_alembic_config: Config, seeded_engine: Engine, migrated: dict
) -> set[str]:
    """Step back to the prior revision and read the table's columns again.

    Depends on `migrated` so the upgrade is always the thing being undone, and
    returns values rather than database state so nothing here depends on which
    test runs first.

    :return: The columns `batch_peak` has after the downgrade.
    :rtype: set[str]
    """
    downgrade(seeded_alembic_config, PRIOR_REVISION)
    return _columns(seeded_engine)


# --- Upgrade ---------------------------------------------------------------


def test_upgrade_reaches_the_revision(migrated: dict) -> None:
    """The migration ran to completion over a batch that was already folded."""
    assert migrated["version"] == REVISION


def test_a_satellite_every_member_agrees_on_is_linked(migrated: dict) -> None:
    """Both members are iso_child rows owned by the same M0, whose own peak
    folded into `bp-m0`. That is the two-hop link the ledger folds rows by."""
    assert migrated["satellites"]["bp-sat"] == "bp-m0"


def test_an_m0_anchor_is_nobody_s_satellite(migrated: dict) -> None:
    """The parent has to stay a top-level row, or there is nothing to fold under."""
    assert migrated["satellites"]["bp-m0"] is None
    assert migrated["satellites"]["bp-solo"] is None


def test_no_majority_leaves_the_anchor_unlinked(migrated: dict) -> None:
    """A satellite in one sample and a peak assigned in its own right in the
    other. One of two assigned members is not a majority, and folding it away on
    a single sample's word would hide a species from the ledger."""
    assert migrated["satellites"]["bp-flip"] is None


def test_a_satellite_whose_owner_never_folded_is_left_unlinked(migrated: dict) -> None:
    """`pa-lost-1` names a real owning assignment that has no occurrence - the
    state a peak dropped from a contested anchor leaves. There is no anchor to
    point at, so the join finds nothing and the row stays top-level."""
    assert migrated["satellites"]["bp-lost"] is None


def test_an_unassigned_anchor_is_never_linked(migrated: dict) -> None:
    """Unassigned members carry no formula, so they are not in the denominator
    and cannot be in the numerator either."""
    assert migrated["satellites"]["bp-bare"] is None


def test_an_empty_formula_is_not_a_formula(migrated: dict) -> None:
    """`bp-blank` has one satellite member and one member whose formula is the
    empty string. The empty string is not a formula - which is how the Python
    side reads it, testing the value for truthiness - so the denominator is one
    and the single vote carries it. Counting the blank as assigned makes this
    one vote of two, no majority, and no link: a row the backfill would leave
    unfolded that the very next fold of the batch would fold."""
    assert migrated["satellites"]["bp-blank"] == "bp-m0"


def test_intensity_is_the_brightest_member(migrated: dict) -> None:
    """Across samples, and over every member rather than the assigned ones: an
    unassigned trace has an intensity worth sorting the ledger by."""
    assert migrated["intensities"]["bp-m0"] == pytest.approx(6000.0)
    assert migrated["intensities"]["bp-sat"] == pytest.approx(500.0)
    assert migrated["intensities"]["bp-flip"] == pytest.approx(900.0)
    assert migrated["intensities"]["bp-bare"] == pytest.approx(300.0)


def test_a_member_with_no_intensity_is_skipped_not_read_as_zero(
    migrated: dict,
) -> None:
    """`bp-solo` has two members and only one carries an intensity. A max that
    counted the NULL as zero would still say 2000 here; one that let the NULL
    propagate would say nothing at all."""
    assert migrated["intensities"]["bp-solo"] == pytest.approx(2000.0)


# --- Downgrade -------------------------------------------------------------


def test_downgrade_drops_both_columns(downgraded: set[str]) -> None:
    """The columns are derived from the occurrences, so dropping them loses
    nothing a re-fold cannot produce again."""
    assert "satellite_of" not in downgraded
    assert "max_intensity" not in downgraded
    # The rest of the row is untouched - this revision adds, it does not rewrite.
    assert {"batch_peak_id", "consensus_tier", "n_present"} <= downgraded
