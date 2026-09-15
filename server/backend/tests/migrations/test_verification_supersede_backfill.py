"""Seeded test for the current-verdict backfill in `f3d81a6c47b9`.

Stairway and drift both walk the chain against a database created empty, so
they run this `upgrade()` with zero verification rows: the backfill matches
nothing and the partial unique index is created over an empty table. A green
migrations suite would say nothing about either. Both are exercised here
against a database that actually holds a verdict history.

The two halves have to agree on how NULL groups, and they express it
differently, which is the subtle failure this file exists to catch. The
backfill's `PARTITION BY` treats two NULL formulas as the *same* group (window
functions compare NULLs as equal); a unique index does the opposite unless it
is declared NULLS NOT DISTINCT. Get one of them wrong and the formula-less
rows either come through with two live verdicts (index too lax) or fail the
index creation outright (backfill too lax). `av-null-*` is that case.

Ties on `verified_utc` are the other trap: two verdicts recorded in the same
transaction share a timestamp, and `LEAD` over an ambiguous ordering could
leave both live, which the index then rejects and the whole upgrade fails on.
`av-tie-*` pins the tiebreaker.

Rows are seeded at the previous revision through raw SQL rather than the ORM: a
migration test has to describe the schema as it was, and at PRIOR_REVISION the
`superseded_utc` column this suite is about does not exist yet.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic.command import downgrade, upgrade
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError


# This checkout's migrations, not MASCOPE_PATH's - see conftest.BACKEND_PATH.
_ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"

# The revision under test, read from the script directory. Its parent comes
# from there too, so re-parenting the migration does not silently seed at the
# wrong schema.
REVISION = "f3d81a6c47b9"
_SCRIPT = ScriptDirectory.from_config(Config(str(_ALEMBIC_INI))).get_revision(REVISION)
PRIOR_REVISION = _SCRIPT.down_revision


# --- Seed data -------------------------------------------------------------

_WORKSPACE_ID = "ws-verif"
_DATASET_ID = "ds-verif"
_SAMPLE_BATCH_ID = "sb-verif"
_SAMPLE_ITEM_ID = "si-verif"
_SAMPLE_FILE_ID = "sf-verif"

_BASE_TIME = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)

# (id, sample_peak_id, assigned_formula, ionization_mechanism_id, verdict,
#  minutes after _BASE_TIME)
#
# One group per behaviour. The ids are quoted by the assertions below, and the
# minute offsets are what the backfill orders on - a superseded row is stamped
# with its *successor's* timestamp, so the expected values are readable off
# this table.
_VERIFICATIONS = [
    # Never revisited: the single verdict on its identity stays live.
    ("av-solo", "p0001", "C6H12O6", "H+", "confirmed", 0),
    # Changed their mind twice. Only the last is live; the first two are
    # stamped with the time of the verdict that replaced each.
    ("av-flip-1", "p0002", "C8H10N4O2", "H+", "confirmed", 10),
    ("av-flip-2", "p0002", "C8H10N4O2", "H+", "rejected", 20),
    ("av-flip-3", "p0002", "C8H10N4O2", "H+", "confirmed", 30),
    # Same peak, different formula: a different identity, so both stay live.
    # This is the group that fails if the backfill partitions on the peak alone.
    ("av-alt-a", "p0003", "C10H16O", "H+", "confirmed", 40),
    ("av-alt-b", "p0003", "C9H12O2", "H+", "rejected", 50),
    # Same peak and formula, different adduct: also a different identity.
    ("av-adduct-h", "p0004", "C12H22O11", "H+", "confirmed", 60),
    ("av-adduct-na", "p0004", "C12H22O11", "Na+", "rejected", 70),
    # Formula-less peak judged twice. Both identity halves are NULL, so this
    # group only collapses if the index is NULLS NOT DISTINCT, and only stays
    # legal if the backfill grouped the NULLs the same way.
    ("av-null-1", "p0005", None, None, "rejected", 80),
    ("av-null-2", "p0005", None, None, "unsure", 90),
    # Two verdicts sharing a timestamp, as two writes in one transaction would.
    # Exactly one must come out live whichever way the tie breaks.
    ("av-tie-a", "p0006", "C5H5N5", "H+", "confirmed", 100),
    ("av-tie-b", "p0006", "C5H5N5", "H+", "rejected", 100),
    # A different sample would be a different identity too, but the FK chain
    # for a second sample buys nothing this file does not already cover.
]


# --- Seed SQL --------------------------------------------------------------
#
# `datetime` and `range` are quoted because they read as type names; the rest
# of each column list is spelled out so a NOT NULL column added later fails
# here loudly rather than silently defaulting.

_WORKSPACE_SQL = """
    INSERT INTO workspace (workspace_id, workspace_name)
    VALUES (:id, 'Verdict Supersede')
"""

_DATASET_SQL = """
    INSERT INTO dataset (dataset_id, workspace_id, dataset_name)
    VALUES (:id, :workspace_id, 'Verdict Supersede')
"""

_SAMPLE_FILE_SQL = """
    INSERT INTO sample_file (sample_file_id, filename, instrument, "datetime",
                             datetime_utc, length, "range", polarity)
    VALUES (:id, 'verdict-supersede.raw', 'instrument-x', :local, :utc, 60.0,
            CAST('[100.0, 500.0]' AS json), '+')
"""

_SAMPLE_BATCH_SQL = """
    INSERT INTO sample_batch (sample_batch_id, dataset_id, sample_batch_name)
    VALUES (:id, :dataset_id, 'Verdict Supersede')
"""

_SAMPLE_ITEM_SQL = """
    INSERT INTO sample_item (sample_item_id, sample_batch_id, sample_file_id,
                             sample_item_name, sample_item_type)
    VALUES (:id, :sample_batch_id, :sample_file_id, 'Verdict Supersede', 'sample')
"""

_VERIFICATION_SQL = """
    INSERT INTO assignment_verification (assignment_verification_id,
                                         sample_item_id, sample_peak_id,
                                         assigned_formula,
                                         ionization_mechanism_id, verdict,
                                         evidence_level, fit_score, evidence,
                                         p_correct, verified_utc)
    VALUES (:id, :sample_item_id, :sample_peak_id, :formula, :mechanism,
            :verdict, 'visual', 0.9, 0.8, 0.7, :verified_utc)
"""

# Inserted after the upgrade to prove the index refuses a second live verdict.
_LIVE_INSERT_SQL = """
    INSERT INTO assignment_verification (assignment_verification_id,
                                         sample_item_id, sample_peak_id,
                                         assigned_formula,
                                         ionization_mechanism_id, verdict,
                                         verified_utc, superseded_utc)
    VALUES (:id, :sample_item_id, :sample_peak_id, :formula, :mechanism,
            'confirmed', :verified_utc, NULL)
"""


def _seed(engine: Engine) -> None:
    """Insert the verdict history the backfill has to collapse.

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
                "utc": _BASE_TIME,
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
            text(_VERIFICATION_SQL),
            [
                {
                    "id": verification_id,
                    "sample_item_id": _SAMPLE_ITEM_ID,
                    "sample_peak_id": peak_id,
                    "formula": formula,
                    "mechanism": mechanism,
                    "verdict": verdict,
                    "verified_utc": _BASE_TIME + timedelta(minutes=minutes),
                }
                for (
                    verification_id,
                    peak_id,
                    formula,
                    mechanism,
                    verdict,
                    minutes,
                ) in _VERIFICATIONS
            ],
        )


def _snapshot(engine: Engine) -> dict:
    """Read the supersession state, plus the schema version, into plain values.

    Plain values rather than live rows on purpose: a snapshot taken after the
    upgrade stays readable after the downgrade has run, so the assertions below
    do not depend on the order pytest happens to collect them in.

    :param engine: Engine on the seeded test database.
    :type engine: Engine
    :return: 'superseded' maps each row id to its superseded_utc (None when
             live), 'columns' lists the table's columns, and 'version' is the
             current schema revision.
    :rtype: dict
    """
    with engine.connect() as conn:
        columns = {
            row[0]
            for row in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns"
                    " WHERE table_name = 'assignment_verification'"
                )
            ).all()
        }
        superseded = {}
        if "superseded_utc" in columns:
            superseded = {
                row.assignment_verification_id: row.superseded_utc
                for row in conn.execute(
                    text(
                        "SELECT assignment_verification_id, superseded_utc"
                        " FROM assignment_verification"
                    )
                ).all()
            }
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()

    return {"version": version, "superseded": superseded, "columns": columns}


def _at(minutes: int) -> datetime:
    """The seed timestamp `minutes` after the base time.

    :param minutes: Offset used in `_VERIFICATIONS`.
    :type minutes: int
    :return: The corresponding timezone-aware timestamp.
    :rtype: datetime
    """
    return _BASE_TIME + timedelta(minutes=minutes)


@pytest.fixture(scope="module")
def migrated(seeded_alembic_config: Config, seeded_engine: Engine) -> dict:
    """Seed a verdict history at the prior revision, apply the backfill, read back.

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
) -> dict:
    """Step back to the prior revision and read the same sites again.

    Depends on `migrated` so the upgrade is always the thing being undone, and
    returns a snapshot of its own - the two fixtures hand out values, not
    database state, so nothing here depends on which test runs first.

    :return: The snapshot as the downgrade left it.
    :rtype: dict
    """
    downgrade(seeded_alembic_config, PRIOR_REVISION)
    return _snapshot(seeded_engine)


# --- Upgrade ---------------------------------------------------------------


def test_upgrade_reaches_the_revision(migrated: dict) -> None:
    """The migration ran to completion over a table holding a verdict history.

    Reaching the revision at all is a real assertion here: the index is created
    immediately after the backfill, so a backfill that left any identity with
    two live rows fails the upgrade rather than producing a wrong snapshot.
    """
    assert migrated["version"] == REVISION
    assert "superseded_utc" in migrated["columns"]


def test_a_single_verdict_stays_live(migrated: dict) -> None:
    """An identity judged once is the current verdict, not a superseded one."""
    assert migrated["superseded"]["av-solo"] is None


def test_replaced_verdicts_are_stamped_with_their_successor(migrated: dict) -> None:
    """Each superseded row records when it was replaced, and only the last is live.

    The stamp is the successor's `verified_utc`, so the history reads as a
    chain of intervals rather than a pile of rows with one arbitrary marker.
    """
    assert migrated["superseded"]["av-flip-1"] == _at(20)
    assert migrated["superseded"]["av-flip-2"] == _at(30)
    assert migrated["superseded"]["av-flip-3"] is None


def test_a_different_formula_on_one_peak_is_a_different_identity(
    migrated: dict,
) -> None:
    """Two formulas judged on the same peak are two findings, both current.

    A backfill that partitioned on the peak alone would supersede one of these,
    silently discarding a verdict the user never retracted.
    """
    assert migrated["superseded"]["av-alt-a"] is None
    assert migrated["superseded"]["av-alt-b"] is None


def test_a_different_adduct_is_a_different_identity(migrated: dict) -> None:
    """The ionization mechanism is part of the identity, so both stay live.

    The decision that one verdict covers an isotopologue family explicitly does
    not extend across adducts - the protonated and sodiated forms are separate
    findings and are judged separately.
    """
    assert migrated["superseded"]["av-adduct-h"] is None
    assert migrated["superseded"]["av-adduct-na"] is None


def test_formula_less_verdicts_collapse_like_any_other_identity(
    migrated: dict,
) -> None:
    """Two verdicts on a peak with no formula are one identity, not two.

    Both identity halves are NULL here. The backfill's PARTITION BY groups
    those together; the index only agrees because it is declared NULLS NOT
    DISTINCT. If it were not, this pair would come through with two live rows -
    the exact state the index exists to make unrepresentable.
    """
    assert migrated["superseded"]["av-null-1"] == _at(90)
    assert migrated["superseded"]["av-null-2"] is None


def test_verdicts_sharing_a_timestamp_leave_exactly_one_live(migrated: dict) -> None:
    """A tie on verified_utc still resolves to a single current verdict.

    Two verdicts written in one transaction share a timestamp, so `LEAD` needs
    a tiebreaker to order them at all; without one the upgrade could leave both
    live and fail on the index. Which of the two survives is arbitrary and not
    asserted - that exactly one does is the invariant.
    """
    tie = [migrated["superseded"]["av-tie-a"], migrated["superseded"]["av-tie-b"]]
    assert tie.count(None) == 1
    assert _at(100) in tie


def test_index_refuses_a_second_live_verdict(
    migrated: dict, seeded_engine: Engine
) -> None:
    """The invariant is enforced by the database, not just produced by the backfill.

    This is the half a data-only assertion cannot reach: the backfill leaving
    one live row per identity says nothing about whether a later writer can add
    a second. Inserting one against an identity that already has a live verdict
    must be rejected.
    """
    with pytest.raises(IntegrityError):
        with seeded_engine.begin() as conn:
            conn.execute(
                text(_LIVE_INSERT_SQL),
                {
                    "id": "av-duplicate",
                    "sample_item_id": _SAMPLE_ITEM_ID,
                    "sample_peak_id": "p0001",
                    "formula": "C6H12O6",
                    "mechanism": "H+",
                    "verified_utc": _at(200),
                },
            )


def test_index_refuses_a_second_live_formula_less_verdict(
    migrated: dict, seeded_engine: Engine
) -> None:
    """NULLS NOT DISTINCT, so the formula-less identity is guarded too.

    Under the default NULLS DISTINCT this insert would be accepted and the
    peak would carry two current verdicts - which is why the index spells the
    behaviour out rather than relying on the default.
    """
    with pytest.raises(IntegrityError):
        with seeded_engine.begin() as conn:
            conn.execute(
                text(_LIVE_INSERT_SQL),
                {
                    "id": "av-duplicate-null",
                    "sample_item_id": _SAMPLE_ITEM_ID,
                    "sample_peak_id": "p0005",
                    "formula": None,
                    "mechanism": None,
                    "verified_utc": _at(210),
                },
            )


# --- Downgrade -------------------------------------------------------------


def test_downgrade_drops_the_column_and_index(downgraded: dict) -> None:
    """Stepping back leaves the table as the older code reads it.

    The verdict rows themselves survive - the downgrade drops the marker, not
    the history - so older code goes back to deriving the current verdict by
    max `verified_utc` over the same rows it always had.
    """
    assert downgraded["version"] == PRIOR_REVISION
    assert "superseded_utc" not in downgraded["columns"]
