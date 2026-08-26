"""Seeded test for the duplicate-dataset-name rename in `b8c5e1f4a7d2`.

Stairway and drift both walk the chain against a database created empty, so
they execute `upgrade()` with zero dataset rows: the rename planner never runs
and a green migrations suite says nothing about it. That planner is the one
piece of this change that rewrites customer data, and it cannot be re-run in
reverse, so it is exercised here against a database that actually holds
duplicates.

The whole point of the migration is that "same name" is one thing:
`lower(btrim(dataset_name))`, evaluated by Postgres. Two of the cases below
exist only to pin that down - a Greek pair Postgres folds together but
Python's `str.lower()` does not, and a pair that differs only by trailing
whitespace. Under a planner that keyed on a Python-lowered string, neither is
seen as a duplicate, nothing is renamed, and `CREATE UNIQUE INDEX` aborts the
upgrade. Here they must both be renamed and the upgrade must finish.

Rows are seeded at the previous revision through raw SQL rather than the ORM:
a migration test has to describe the schema as it was, not as the models are
today.
"""

import io
from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic.command import upgrade
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError


# This checkout's migrations, not MASCOPE_PATH's - see conftest.BACKEND_PATH.
_ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"

# The revision under test, read from the script directory. Its parent comes
# from there too, so re-parenting the migration does not silently seed at the
# wrong schema; the module itself is needed for the report tests below, which
# call a helper directly rather than through a database.
REVISION = "b8c5e1f4a7d2"
_SCRIPT = ScriptDirectory.from_config(Config(str(_ALEMBIC_INI))).get_revision(REVISION)
PRIOR_REVISION = _SCRIPT.down_revision
MIGRATION = _SCRIPT.module


def _utc(year: int, month: int = 1, day: int = 1) -> datetime:
    """Build an aware UTC timestamp for seeded `dataset_utc_created` values."""
    return datetime(year, month, day, tzinfo=timezone.utc)


# --- Seed data -------------------------------------------------------------
#
# One workspace per behaviour, so a failure names the behaviour that broke.
# `dataset_id` / `workspace_id` are readable rather than random: the
# assertions below quote them.

_WORKSPACES = [
    ("ws-order", "Dedup Ordering"),
    ("ws-undated", "Dedup Undated"),
    ("ws-acq", "Dedup Acquisition"),
    ("ws-pad", "Dedup Padding"),
    ("ws-clean", "Dedup Clean"),
    ("ws-uni", "Dedup Unicode"),
]

# (dataset_id, workspace_id, dataset_name, dataset_type, instrument, created)
_DATASETS = [
    # Oldest keeps the name; the case variant is the duplicate. " (2)" is
    # already taken - and taken only by case - so the rename must skip to
    # " (3)". Seeded out of chronological order to prove the migration orders
    # by dataset_utc_created rather than by whatever order rows come back in.
    ("ds-ord-take", "ws-order", "BLANKS (2)", "ANALYSIS", None, _utc(2022)),
    ("ds-ord-new", "ws-order", "blanks", "ANALYSIS", None, _utc(2021)),
    ("ds-ord-old", "ws-order", "Blanks", "ANALYSIS", None, _utc(2020)),
    # An undated row predates the timestamp column, so it is the oldest of
    # all and keeps its name.
    ("ds-und-dated", "ws-undated", "legacy", "ANALYSIS", None, _utc(2020)),
    ("ds-und-null", "ws-undated", "Legacy", "ANALYSIS", None, None),
    # ACQUISITION rows are outside the index and must not be touched - two of
    # them share a name legitimately, one per instrument. They are still read,
    # because their names are names a user sees: "Batch (2)" is reserved by an
    # ACQUISITION row, so the ANALYSIS duplicate has to skip past it.
    ("ds-acq-2027a", "ws-acq", "2027", "ACQUISITION", "instrument-a", _utc(2020)),
    ("ds-acq-2027b", "ws-acq", "2027", "ACQUISITION", "instrument-b", _utc(2021)),
    ("ds-acq-res", "ws-acq", "Batch (2)", "ACQUISITION", "instrument-c", _utc(2021, 6)),
    ("ds-acq-keep", "ws-acq", "Batch", "ANALYSIS", None, _utc(2022)),
    ("ds-acq-dup", "ws-acq", "batch", "ANALYSIS", None, _utc(2023)),
    # Padding does not distinguish two names either - the canonical key is
    # lower(btrim(...)), so these two are one name to a user and to the index.
    ("ds-pad-space", "ws-pad", "Padded Name ", "ANALYSIS", None, _utc(2020)),
    ("ds-pad-plain", "ws-pad", "Padded Name", "ANALYSIS", None, _utc(2021)),
    # A workspace with no duplicates must come through untouched - it is not
    # even read by the migration.
    ("ds-cln-a", "ws-clean", "Alpha", "ANALYSIS", None, _utc(2020)),
    ("ds-cln-b", "ws-clean", "Beta", "ANALYSIS", None, _utc(2021)),
]

# Pairs Postgres folds together but Python's `str.lower()` keeps apart. First:
# Greek capital iota + sigma against their lowercase forms - Postgres maps
# U+03A3 to the medial U+03C3, Python applies the final-sigma rule and
# produces U+03C2. Second: U+0130 against ASCII "I" - Postgres lowers it to a
# single "i", Python to "i" + U+0307. Both are only meaningful on a server
# whose collation actually case-maps non-ASCII, which is checked before use.
_UNICODE_CANDIDATES = [
    # "IS" / "is" in Greek capitals and lowercase (medial sigma).
    ("ΙΣ", "ισ"),
    # Latin capital I with dot above, against a plain ASCII "I".
    ("İstanbul", "Istanbul"),
]

_UNICODE_IDS = ("ds-uni-keep", "ds-uni-dup")


@pytest.fixture(scope="module")
def unicode_pair(seeded_engine: Engine) -> tuple[str, str] | None:
    """Pick a name pair this server folds together but Python does not.

    Asked of the running server rather than assumed: the disagreement only
    exists where the database's collation case-maps beyond ASCII, and a test
    that hard-coded a pair would quietly assert nothing on a `C`-collated
    server instead of saying so.

    :param seeded_engine: Engine on the seeded test database.
    :type seeded_engine: Engine
    :return: A ``(keeper, duplicate)`` pair, or None if this server folds
             neither candidate.
    :rtype: tuple[str, str] | None
    """
    with seeded_engine.connect() as conn:
        for keeper, duplicate in _UNICODE_CANDIDATES:
            folds = conn.execute(
                text(
                    "SELECT lower(btrim(CAST(:a AS text)))"
                    " = lower(btrim(CAST(:b AS text)))"
                ),
                {"a": keeper, "b": duplicate},
            ).scalar()
            if folds and keeper.strip().lower() != duplicate.strip().lower():
                return keeper, duplicate
    return None


@pytest.fixture(scope="module")
def migrated_names(
    seeded_alembic_config: Config,
    seeded_engine: Engine,
    unicode_pair: tuple[str, str] | None,
) -> dict[str, str]:
    """Seed duplicates at the prior revision, apply `b8c5e1f4a7d2`, read back.

    Reaching the read at all is itself the assertion the MAJOR failure needs:
    a planner that missed any duplicate leaves `CREATE UNIQUE INDEX` to abort
    the upgrade, and this fixture raises instead of returning.

    :return: Every dataset's name after the migration, by dataset_id.
    :rtype: dict[str, str]
    """
    upgrade(seeded_alembic_config, PRIOR_REVISION)

    datasets = list(_DATASETS)
    if unicode_pair is not None:
        keeper, duplicate = unicode_pair
        datasets += [
            (_UNICODE_IDS[0], "ws-uni", keeper, "ANALYSIS", None, _utc(2020)),
            (_UNICODE_IDS[1], "ws-uni", duplicate, "ANALYSIS", None, _utc(2021)),
        ]

    with seeded_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO workspace (workspace_id, workspace_name,"
                " workspace_utc_created) VALUES (:id, :name, :created)"
            ),
            [
                {"id": ws_id, "name": name, "created": _utc(2019)}
                for ws_id, name in _WORKSPACES
            ],
        )
        conn.execute(
            text(
                "INSERT INTO dataset (dataset_id, workspace_id, dataset_name,"
                " dataset_type, instrument, dataset_utc_created)"
                " VALUES (:id, :ws, :name, :type, :instrument, :created)"
            ),
            [
                {
                    "id": ds_id,
                    "ws": ws_id,
                    "name": name,
                    "type": ds_type,
                    "instrument": instrument,
                    "created": created,
                }
                for ds_id, ws_id, name, ds_type, instrument, created in datasets
            ],
        )

    upgrade(seeded_alembic_config, REVISION)

    with seeded_engine.connect() as conn:
        rows = conn.execute(text("SELECT dataset_id, dataset_name FROM dataset")).all()
    return {row.dataset_id: row.dataset_name for row in rows}


def test_upgrade_reaches_the_revision(
    migrated_names: dict[str, str], seeded_engine: Engine
) -> None:
    """The migration completed, duplicates and all.

    `CREATE UNIQUE INDEX` is the last statement of `upgrade()`, so the chain
    only reaches this revision if the planner resolved every collision
    Postgres recognises - including the ones Python's `str.lower()` does not.
    """
    with seeded_engine.connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert version == REVISION


def test_oldest_row_keeps_the_name(migrated_names: dict[str, str]) -> None:
    """Of a set of duplicates, the oldest is the one left alone.

    The rows were seeded newest-first, so this fails if the planner keeps
    whichever row the database happens to return first.
    """
    assert migrated_names["ds-ord-old"] == "Blanks"


def test_undated_row_counts_as_the_oldest(migrated_names: dict[str, str]) -> None:
    """A row with no dataset_utc_created predates the timestamp column.

    It sorts first (NULLs first) and therefore keeps its name; the dated row
    beside it is the one renamed.
    """
    assert migrated_names["ds-und-null"] == "Legacy"
    assert migrated_names["ds-und-dated"] == "legacy (2)"


def test_lowest_free_suffix_skips_one_taken_only_by_case(
    migrated_names: dict[str, str],
) -> None:
    """The " (2)" slot is taken by "BLANKS (2)", so the rename lands on " (3)".

    Taken only by case: the suffix search asks the same question the index
    does - is `lower(btrim(candidate))` already in this workspace - so a
    differently-cased holder of " (2)" still blocks it. The holder itself is
    not a duplicate of anything and keeps its own name.
    """
    assert migrated_names["ds-ord-new"] == "blanks (3)"
    assert migrated_names["ds-ord-take"] == "BLANKS (2)"


def test_acquisition_rows_are_never_renamed(migrated_names: dict[str, str]) -> None:
    """ACQUISITION datasets sit outside the index and keep their names.

    Two of them share the name "2027" - one per instrument, which is how
    `get_acquisition_dataset` names them - and both must survive that way.
    """
    assert migrated_names["ds-acq-2027a"] == "2027"
    assert migrated_names["ds-acq-2027b"] == "2027"
    assert migrated_names["ds-acq-res"] == "Batch (2)"


def test_acquisition_names_still_reserve_a_suffix(
    migrated_names: dict[str, str],
) -> None:
    """A name only an ACQUISITION row holds is still not free to hand out.

    The index would allow "Batch (2)" here, but the controller's check does
    not ignore ACQUISITION rows, so a rename onto that name would produce a
    dataset the user could not then rename to anything else. The duplicate
    skips past it to " (3)".
    """
    assert migrated_names["ds-acq-keep"] == "Batch"
    assert migrated_names["ds-acq-dup"] == "batch (3)"


def test_padded_look_alikes_are_renamed(migrated_names: dict[str, str]) -> None:
    """Trailing whitespace does not make two names different names.

    The canonical key is `lower(btrim(...))`, so these two rows are the same
    name; before `btrim` joined the key they both survived the migration and
    the workspace list went on showing two entries a user cannot tell apart.
    The keeper's stored padding is left as it is - the index keys past it.
    """
    assert migrated_names["ds-pad-space"] == "Padded Name "
    assert migrated_names["ds-pad-plain"] == "Padded Name (2)"


def test_unicode_pair_postgres_folds_is_renamed(
    migrated_names: dict[str, str], unicode_pair: tuple[str, str] | None
) -> None:
    """A duplicate only Postgres sees is still a duplicate.

    This is the case the planner used to miss: keyed on Python's
    `str.lower()`, the pair looks distinct, nothing is renamed, and
    `CREATE UNIQUE INDEX` aborts the upgrade. Keyed on the value Postgres
    returns, the later row is renamed like any other duplicate.
    """
    if unicode_pair is None:
        pytest.skip(
            "this server's collation case-maps no non-ASCII codepoint, so "
            "Postgres and Python cannot disagree about one here"
        )
    keeper, duplicate = unicode_pair
    assert migrated_names[_UNICODE_IDS[0]] == keeper
    assert migrated_names[_UNICODE_IDS[1]] == f"{duplicate} (2)"


def test_workspace_without_duplicates_is_untouched(
    migrated_names: dict[str, str],
) -> None:
    """A clean workspace is not read, let alone rewritten.

    The overwhelmingly common case: the migration must not invent renames for
    names that were never in conflict.
    """
    assert migrated_names["ds-cln-a"] == "Alpha"
    assert migrated_names["ds-cln-b"] == "Beta"


@pytest.mark.parametrize(
    "name",
    [
        "BLANKS",  # differs from the keeper only in case
        "  blanks  ",  # differs from the keeper only in padding
    ],
)
def test_index_enforces_the_canonical_key_afterwards(
    migrated_names: dict[str, str], seeded_engine: Engine, name: str
) -> None:
    """The index the rename made room for rejects the next look-alike.

    Renaming without then constraining the table would fix today's rows and
    let tomorrow's back in, so the outcome under test is both halves: the
    duplicates are gone *and* `lower(btrim(dataset_name))` is unique per
    workspace. Rolled back, so the seeded state other assertions read stays
    as the migration left it.
    """
    with pytest.raises(IntegrityError):
        with seeded_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO dataset (dataset_id, workspace_id, dataset_name,"
                    " dataset_type) VALUES (:id, 'ws-order', :name, 'ANALYSIS')"
                ),
                {"id": f"ds-late-{len(name)}", "name": name},
            )


# --- The rename report -----------------------------------------------------
#
# No database needed: these call the migration's own formatting helper. The
# report sits between the renames and `CREATE UNIQUE INDEX`, so a name it
# cannot print aborts the upgrade - and a name that is not ASCII is exactly
# what this migration exists to reconcile.


# A name this migration is likelier than most to meet: outside ASCII in three
# different ways, and not representable in cp1252 as a whole.
_NON_ASCII_NAME = "Näyte – ΙΣ"


def test_rename_report_prints_on_a_non_utf8_console() -> None:
    """The report cannot be the thing that aborts the upgrade.

    `alembic upgrade head` prints to whatever stdout the operator has, and on
    Windows a captured or piped one is cp1252 by default - the encoding hazard
    this repository documents for its own CLI. Interpolated raw, a name
    outside that encoding raises UnicodeEncodeError, and because the report is
    printed after the renames but before `CREATE UNIQUE INDEX`, that exception
    aborts the upgrade. (Transactionally, so nothing is left half-applied -
    but the operator is left with an upgrade that will not run.)

    A cp1252 stream with `errors="strict"` is the whole test: the writes
    either go through or they raise, exactly as they would on that console.
    """
    lines = MIGRATION._rename_report(
        [("ws-uni", "ds-uni-dup", _NON_ASCII_NAME, f"{_NON_ASCII_NAME} (2)")]
    )
    assert lines, "a rename must be reported"

    console = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
    for line in lines:
        print(line, file=console)
    console.flush()


def test_rename_report_keeps_what_it_escapes() -> None:
    """Escaping the names must not cost the record they are.

    The downgrade cannot put the old names back, so this output is all that is
    left of them: an escape that dropped or flattened characters would trade
    one kind of data loss for another. `ascii()` keeps every codepoint, and as
    a side benefit it separates a pair no console can - two names differing
    only by a zero-width space, which `lower(btrim(...))` does not strip
    either, so the migration hands such a pair through rather than resolving
    it.
    """
    plain = "Blanks"
    # Written as an escape on purpose: a literal zero-width space in this
    # source would be exactly as invisible here as it is in a report.
    invisible = "Blanks\u200b"

    report = "\n".join(
        MIGRATION._rename_report(
            [
                ("ws-uni", "ds-uni-dup", _NON_ASCII_NAME, f"{_NON_ASCII_NAME} (2)"),
                ("ws-a", "ds-plain", plain, f"{plain} (2)"),
                ("ws-a", "ds-invisible", invisible, f"{invisible} (2)"),
            ]
        )
    )

    # Every codepoint of the old name is still named, and so is the row it
    # belongs to.
    assert "\\xe4" in report and "\\u2013" in report
    assert "\\u03a3" in report
    assert "ws-uni/ds-uni-dup" in report

    old_names = [
        line.split(": ", 1)[1].split(" -> ")[0] for line in report.split("\n")[1:]
    ]
    assert old_names[1] != old_names[2], (
        f"the zero-width pair reads identically: {old_names[1]!r}"
    )

    # A clean database renames nothing and must print nothing at all.
    assert MIGRATION._rename_report([]) == []
