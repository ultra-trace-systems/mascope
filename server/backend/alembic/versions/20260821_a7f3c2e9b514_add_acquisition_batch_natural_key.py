"""Add a natural-key unique index for daily ACQUISITION batches

``create_acquisition_batches_and_items`` is a read-then-write get-or-create with
no constraint on its natural key (dataset_id, sample_batch_name, polarity), so
concurrent ingest could insert duplicate daily batches - files converted in one
watcher scan share a day and an ionization mode, resolve to one batch name, and
all read "absent" together. The day's samples then split across the duplicates,
each carrying its own calibration and match state.

Duplicate batches own sample items, so they are merged rather than deleted: per
natural key the oldest batch (sample_batch_utc_created, NULLs last, ties broken
by the smaller primary key - the same order the runtime lookup uses, so both
converge on the same row) is kept, sample items of the newer duplicates are
repointed to it, and the emptied duplicates are removed. Their target-collection
links are carried over only where the keeper does not already have them, and
their batch peaks are left to cascade: batch peaks are derived anchors computed
over a batch's members, so two independent sets cannot be concatenated. Any
keeper that absorbed samples is marked 'rematch' to have them recomputed.

Then the key is constrained with a partial unique index (ANALYSIS batches are
user-named and have no such invariant) so the race fails loudly and is recovered
in ``get_or_create_acquisition_batch``.

Revision ID: a7f3c2e9b514
Revises: d7c2b9e4f1a6
Create Date: 2026-08-21 10:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision: str = "a7f3c2e9b514"
down_revision: Union[str, Sequence[str], None] = "d7c2b9e4f1a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


INDEX_NAME = "uq_sample_batch_acquisition_natural_key"

# Oldest batch per ACQUISITION natural key. NULLS LAST matches
# `_find_acquisition_batch`: a row with no creation timestamp is unknown-aged,
# not oldest, and the runtime lookup and this merge must agree on the keeper.
_KEEPERS_CTE = """
    SELECT DISTINCT ON (dataset_id, sample_batch_name, polarity)
           sample_batch_id AS keep_id, dataset_id, sample_batch_name, polarity
    FROM sample_batch
    WHERE sample_batch_type = 'ACQUISITION'
    ORDER BY dataset_id, sample_batch_name, polarity,
             COALESCE(sample_batch_utc_created, 'infinity'::timestamptz),
             sample_batch_id
"""

# Rows of the duplicates, paired with the keeper they belong to. Every statement
# below narrows this same set, so they cannot disagree about what a duplicate is.
_DUPLICATES_CTE = """
    SELECT sb.sample_batch_id AS dup_id, k.keep_id
    FROM sample_batch sb
    JOIN keepers k
      ON sb.dataset_id = k.dataset_id
     AND sb.sample_batch_name = k.sample_batch_name
     AND sb.polarity = k.polarity
    WHERE sb.sample_batch_type = 'ACQUISITION'
      AND sb.sample_batch_id <> k.keep_id
"""

_REPOINT_ITEMS_SQL = f"""
    WITH keepers AS ({_KEEPERS_CTE}), duplicates AS ({_DUPLICATES_CTE})
    UPDATE sample_item si
    SET sample_batch_id = d.keep_id
    FROM duplicates d
    WHERE si.sample_batch_id = d.dup_id
"""

# Composite primary key (target_collection_id, sample_batch_id): the keeper can
# hold a collection at most once, and two duplicates of the same keeper can each
# hold one it lacks. Insert the union instead of repointing rows: an UPDATE
# evaluates a NOT EXISTS guard against the statement's starting snapshot, so it
# cannot see a link the same statement just moved onto the keeper, and the second
# duplicate's row would collide on the primary key. DISTINCT covers the links two
# duplicates both carry, ON CONFLICT the ones the keeper already has. The
# duplicates' own rows are left where they are and cascade away with them
# (fk_target_collection_in_sample_batch_sample_batch_id_sample_batch is ON DELETE
# CASCADE), so this must stay ahead of the DELETE below.
_COPY_COLLECTIONS_SQL = f"""
    WITH keepers AS ({_KEEPERS_CTE}), duplicates AS ({_DUPLICATES_CTE})
    INSERT INTO target_collection_in_sample_batch
        (target_collection_id, sample_batch_id)
    SELECT DISTINCT tc.target_collection_id, d.keep_id
    FROM target_collection_in_sample_batch tc
    JOIN duplicates d ON tc.sample_batch_id = d.dup_id
    ON CONFLICT (target_collection_id, sample_batch_id) DO NOTHING
"""

# Batch peaks are anchors derived from a batch's members; the keeper's set no
# longer describes the samples it just absorbed. Flag it for recomputation
# (the duplicates' own peaks cascade away with them).
_FLAG_KEEPERS_SQL = f"""
    WITH keepers AS ({_KEEPERS_CTE}), duplicates AS ({_DUPLICATES_CTE})
    UPDATE sample_batch sb
    SET status = 'rematch'
    FROM (SELECT DISTINCT keep_id FROM duplicates) k
    WHERE sb.sample_batch_id = k.keep_id
"""

_DELETE_DUPLICATES_SQL = f"""
    WITH keepers AS ({_KEEPERS_CTE}), duplicates AS ({_DUPLICATES_CTE})
    DELETE FROM sample_batch sb
    USING duplicates d
    WHERE sb.sample_batch_id = d.dup_id
"""


def upgrade() -> None:
    connection = op.get_bind()
    repointed = connection.execute(text(_REPOINT_ITEMS_SQL))
    connection.execute(text(_COPY_COLLECTIONS_SQL))
    connection.execute(text(_FLAG_KEEPERS_SQL))
    deleted = connection.execute(text(_DELETE_DUPLICATES_SQL))
    if deleted.rowcount:
        print(
            f"Merged {deleted.rowcount} duplicate ACQUISITION batch(es) "
            f"({repointed.rowcount} sample item(s) repointed to the oldest; "
            "the surviving batches are marked 'rematch')"
        )
    op.create_index(
        INDEX_NAME,
        "sample_batch",
        ["dataset_id", "sample_batch_name", "polarity"],
        unique=True,
        postgresql_where=text("sample_batch_type = 'ACQUISITION'"),
    )


def downgrade() -> None:
    op.drop_index(INDEX_NAME, table_name="sample_batch")
