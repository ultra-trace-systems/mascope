"""Add a case-insensitive unique index for dataset names within a workspace

Nothing has ever stopped two datasets in one workspace sharing a name, so the
workspace list could show entries a user cannot tell apart. The controller now
refuses such a name, but its check is a read followed by a write in a separate
statement: two concurrent creates both pass it and both insert. This index
closes that, and the loser's IntegrityError is translated back into the same
409 in ``create_dataset``.

Two names are the same name when they share the canonical key
``lower(btrim(dataset_name))`` - case and surrounding padding do not
distinguish them, because they do not distinguish the two rows in the
workspace list either. That key is evaluated **by Postgres** everywhere it is
needed: in the index, in the controller's check, and in the rename planner
below. Python's ``str.lower()`` is a different function from Postgres
``lower()`` - they disagree on 35 BMP codepoints, and Python alone applies the
Greek final-sigma rule - so a planner that keyed on a Python-lowered string
would miss duplicates Postgres sees and then abort on ``CREATE UNIQUE INDEX``,
which is the exact failure the rename step exists to prevent.

Deployed databases can already hold duplicates, which would abort
``CREATE UNIQUE INDEX``, so they are resolved first. Unlike the ACQUISITION
duplicates merged by ``e4b7a2c8d1f6``, these are not derived names: an ANALYSIS
dataset is named by a person and owns its sample batches, so both rows are kept
and the name is disambiguated instead. Per (workspace_id, canonical key) the
oldest dataset keeps its name (dataset_utc_created, NULLs first - an undated
row predates the timestamp column; ties broken by the smaller primary key,
matching ``e4b7a2c8d1f6``) and every later one gets the lowest free " (n)"
suffix, n starting at 2.

Free is decided on the same canonical key against every name in that
workspace, including the ACQUISITION ones this index ignores (the controller's
check does not ignore them, and handing out a name it would refuse helps
nobody) and including names that are themselves about to be renamed away.
Names taken this way are never released, so no two rows can be handed the same
one.

The index is partial. ACQUISITION datasets are named after the calendar year
and auto-created by ``get_acquisition_dataset``, which recovers from a
duplicate-key insert only by re-finding an ACQUISITION row; covering them here
would let a user-created dataset named e.g. "2027" in an instrument workspace
turn that year's rollover into an unrecoverable IntegrityError.
``uq_dataset_acquisition_natural_key`` constrains those rows on their own key
shape, and the two indexes are complementary.

The downgrade drops the index. It cannot put the old names back - the rename is
one-way, which is why every one of them is printed.

Revision ID: b8c5e1f4a7d2
Revises: a7f3c2e9b514
Create Date: 2026-08-26 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy import Text, bindparam, literal_column, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.engine import Connection


# revision identifiers, used by Alembic.
revision: str = "b8c5e1f4a7d2"
down_revision: Union[str, Sequence[str], None] = "a7f3c2e9b514"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


INDEX_NAME = "uq_dataset_workspace_name_ci"

# The canonical key: the one expression that decides whether two dataset names
# are the same name. Written once and used for the index, for the duplicate
# search, and for canonicalising the candidate names the planner invents.
NAME_KEY_SQL = "lower(btrim(dataset_name))"

# dataset_name is String(256) - a suffixed name still has to fit the column.
NAME_MAX_LENGTH = 256

# How many " (n)" candidates to canonicalise per round trip. Only a workspace
# that already holds a run of numbered look-alikes needs more than the first
# few, so one batch answers in practice; the loop widens if it does not.
CANDIDATE_BATCH = 16

# Every dataset of every workspace that holds at least one duplicate, oldest
# first, with the canonical key computed by Postgres alongside each row.
# Whole workspaces, not just the duplicate rows: picking a free name needs to
# know all the names already in use there, ACQUISITION included. Workspaces
# without duplicates are not read at all, so on the overwhelmingly common
# clean database this is the only statement that touches any row.
_DUPLICATE_WORKSPACE_ROWS_SQL = f"""
    SELECT dataset_id, workspace_id, dataset_name, dataset_type,
           {NAME_KEY_SQL} AS name_key
    FROM dataset
    WHERE workspace_id IN (
        SELECT workspace_id
        FROM dataset
        WHERE dataset_type <> 'ACQUISITION'
        GROUP BY workspace_id, {NAME_KEY_SQL}
        HAVING count(*) > 1
    )
    ORDER BY workspace_id,
             COALESCE(dataset_utc_created, '-infinity'::timestamptz),
             dataset_id
"""

# Canonicalise a batch of candidate names the same way, in the same database.
# The candidates are built in Python but their keys never are: they come back
# from `lower(btrim(...))` evaluated by Postgres, so "is this name free?" is
# asked in exactly the terms the unique index will answer it in.
_CANDIDATE_KEYS_SQL = """
    SELECT lower(btrim(candidate)) AS name_key
    FROM unnest(CAST(:candidates AS text[])) WITH ORDINALITY AS c(candidate, ord)
    ORDER BY c.ord
"""

_RENAME_SQL = """
    UPDATE dataset SET dataset_name = :dataset_name WHERE dataset_id = :dataset_id
"""


def _suffixed(name: str, n: int) -> str:
    """Build the ``n``-th disambiguated form of *name*.

    The stem is trimmed rather than the suffix when the result would overflow
    ``String(256)``, so a name already at the column limit still gets numbered
    instead of overflowing.

    :param name: The duplicate's current name, whose case is preserved.
    :type name: str
    :param n: The suffix number.
    :type n: int
    :return: ``name`` with a ``" (n)"`` suffix, at most NAME_MAX_LENGTH long.
    :rtype: str
    """
    suffix = f" ({n})"
    return f"{name[: NAME_MAX_LENGTH - len(suffix)].rstrip()}{suffix}"


def _canonical_keys(connection: Connection, candidates: list[str]) -> list[str]:
    """Ask Postgres for the canonical key of each candidate, order preserved.

    :param connection: The migration's connection.
    :type connection: Connection
    :param candidates: Candidate names, non-empty.
    :type candidates: list[str]
    :return: One key per candidate, in the same order.
    :rtype: list[str]
    """
    rows = connection.execute(
        text(_CANDIDATE_KEYS_SQL).bindparams(
            bindparam("candidates", value=candidates, type_=ARRAY(Text))
        )
    ).all()
    return [row.name_key for row in rows]


def _free_name(connection: Connection, name: str, taken: set[str]) -> tuple[str, str]:
    """Suffix *name* with the lowest number whose key is free in the workspace.

    Terminates, and the bound is checked rather than trusted. Distinct ``n``
    give distinct keys: the key ends in the ASCII run ``" (n)"``, which
    ``lower()`` and ``btrim()`` both leave untouched (the string ends in
    ``")"``, so btrim trims nothing there, and lower() maps ASCII digits,
    spaces and parentheses to themselves under every collation). Reading two
    such keys from the right, either two digits differ or one key has ``"("``
    where the other still has a digit - so no two ``n`` collide. Among
    ``len(taken) + 1`` distinct keys at least one is therefore outside the
    finite set *taken*, which does not grow while this runs. The loop stops at
    that bound and raises rather than spinning if the argument ever fails to
    hold.

    :param connection: The migration's connection, which computes the keys.
    :type connection: Connection
    :param name: The duplicate's current name, whose case is preserved.
    :type name: str
    :param taken: Canonical keys already in use in this workspace, as returned
                  by Postgres.
    :type taken: set[str]
    :return: A ``(new_name, canonical_key)`` pair no other dataset carries.
    :rtype: tuple[str, str]
    :raises RuntimeError: If no free suffix was found within the bound.
    """
    highest = len(taken) + 2
    n = 2
    while n <= highest:
        stop = min(n + CANDIDATE_BATCH, highest + 1)
        candidates = [_suffixed(name, k) for k in range(n, stop)]
        for candidate, key in zip(candidates, _canonical_keys(connection, candidates)):
            if key not in taken:
                return candidate, key
        n = stop
    raise RuntimeError(
        f"No free ' (n)' suffix for dataset name {name!r} below {highest}; "
        "the canonical keys of distinct suffixes are not distinct on this "
        "database, which should be impossible."
    )


def _plan_renames(connection: Connection) -> list[tuple[str, str, str, str]]:
    """Decide the new name of every duplicate, without writing anything.

    :param connection: The migration's connection.
    :type connection: Connection
    :return: One ``(workspace_id, dataset_id, old_name, new_name)`` per row to
             rename, in the order they were found.
    :rtype: list[tuple[str, str, str, str]]
    """
    rows = connection.execute(text(_DUPLICATE_WORKSPACE_ROWS_SQL)).all()

    # Keys a rename must avoid, per workspace. Seeded with every existing
    # name - a row that is itself about to be renamed keeps its old key
    # reserved, so nothing can be handed a name that was only just freed.
    # Every key here was computed by Postgres, never by Python.
    taken: dict[str, set[str]] = {}
    for row in rows:
        taken.setdefault(row.workspace_id, set()).add(row.name_key)

    # Rows arrive oldest-first within a workspace, so the first one seen for a
    # key is the keeper and every later one is a duplicate.
    keepers: set[tuple[str, str]] = set()
    renames: list[tuple[str, str, str, str]] = []
    for row in rows:
        if row.dataset_type == "ACQUISITION":
            continue
        key = (row.workspace_id, row.name_key)
        if key not in keepers:
            keepers.add(key)
            continue
        new_name, new_key = _free_name(
            connection, row.dataset_name, taken[row.workspace_id]
        )
        taken[row.workspace_id].add(new_key)
        renames.append((row.workspace_id, row.dataset_id, row.dataset_name, new_name))
    return renames


def _rename_report(renames: list[tuple[str, str, str, str]]) -> list[str]:
    """Render the record of what was renamed, as ASCII-only lines.

    ASCII-only is a requirement, not tidiness. This runs inside
    ``alembic upgrade head``, whose stdout is whatever console the operator
    happens to have - and on Windows that is routinely a cp1252 stream when
    the output is captured or piped. A raw ``print`` of a name outside that
    encoding raises UnicodeEncodeError, and since the printing sits between
    the renames and ``CREATE UNIQUE INDEX`` it would abort the upgrade. Names
    that are not ASCII are precisely what this migration exists to reconcile,
    so that is not a remote possibility.

    Nothing is lost by escaping. ``ascii()`` quotes each name and escapes what
    falls outside ASCII, which also tells apart the pair a report most needs
    to distinguish: two names differing only by an invisible codepoint. The
    finished line is encoded once more with ``backslashreplace`` so the
    guarantee covers the ids too - they are ``gen_id`` output, from a fixed
    ASCII alphabet, but they are read back out of the database rather than
    trusted.

    :param renames: The planned renames, as returned by `_plan_renames`.
    :type renames: list[tuple[str, str, str, str]]
    :return: The report lines, empty when nothing was renamed.
    :rtype: list[str]
    """
    if not renames:
        return []
    lines = [f"Renamed {len(renames)} duplicate dataset name(s):"]
    lines += [
        f"  {workspace_id}/{dataset_id}: {ascii(old_name)} -> {ascii(new_name)}"
        for workspace_id, dataset_id, old_name, new_name in renames
    ]
    return [line.encode("ascii", "backslashreplace").decode("ascii") for line in lines]


def upgrade() -> None:
    connection = op.get_bind()

    renames = _plan_renames(connection)
    for workspace_id, dataset_id, _old_name, new_name in renames:
        connection.execute(
            text(_RENAME_SQL), {"dataset_name": new_name, "dataset_id": dataset_id}
        )
    # Printed in full, not counted: the downgrade cannot restore these names,
    # so this output is the only record of what was changed.
    for line in _rename_report(renames):
        print(line)

    op.create_index(
        INDEX_NAME,
        "dataset",
        ["workspace_id", literal_column(NAME_KEY_SQL)],
        unique=True,
        postgresql_where=text("dataset_type <> 'ACQUISITION'"),
    )


def downgrade() -> None:
    op.drop_index(INDEX_NAME, table_name="dataset")
