"""Slim the assignment ledger's indexes and key occurrences by member identity

The peak-assignment tables are the fastest-growing thing a deployment writes
once assignment runs at ingest: one ledger row and one batch-peak occurrence
per detected peak per sample, about a kilobyte in all, of which a third to a
half was index. Measured on a 213k-row ledger and a 29k-row occurrence table,
this revision takes 12 % off the ledger and 27 % off the occurrences without
changing what either table holds:

- ``ix_peak_assignment_peak_assignment_run_id`` is dropped. The unique
  constraint on (run, peak) leads with the run id, so every lookup by run -
  the ledger read, the fold, the run-delete cascade - already had an index.
- ``ix_peak_assignment_sample_peak_id`` is dropped. No query filters the
  ledger by a bare peak id (verification identity, occurrence and import
  lookups filter other tables or go through the run-scoped constraint), and
  it was the third-largest structure on the table.
- The four nullable references (owner, target compound, target ion,
  ionization mechanism) are indexed only where they are set. They serve the
  foreign keys' SET NULL actions and the family/target lookups, none of which
  ever asks for a NULL, while most rows carry one (every unassigned peak,
  every peak that is not an isotopologue). A strict ``col = $1`` implies
  ``col IS NOT NULL``, so the partial indexes answer the same queries.
- ``batch_peak_occurrence`` is keyed by (batch_peak_id, sample_item_id) - a
  member's identity, already unique - instead of a 32-char surrogate that
  nothing ever read. The surrogate's primary-key index and the prefix index on
  ``batch_peak_id`` go with it.

It also sets the storage parameters the fold-in churn needs. Every fold
rewrote every anchor it touched and a backfill re-folds a whole batch, so
``batch_peak`` was measured at 18x its compacted size; the occurrence table is
delete-and-reinsert on every re-fold, and the ledger loses whole runs to the
prune and single rows to curation. Per-table autovacuum thresholds (the shape
``d8f3a6c1e9b4`` gave the match tables) return that space to reuse between
passes, and a fill factor on ``batch_peak`` leaves room on each page for the
consensus rewrites to be HOT updates instead of index churn.

The index builds are plain, not CONCURRENTLY: migrations run before the stack
serves, so the share lock they take blocks nothing, and the ledger is empty
anyway on every deployment that predates ingest-time assignment.

Revision ID: 97c42c48e011
Revises: b6a4d1e83c7f
Create Date: 2026-09-03 09:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "97c42c48e011"
down_revision: Union[str, Sequence[str], None] = "b6a4d1e83c7f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# The ledger's nullable references, indexed only where they are set.
_PARTIAL_INDEX_COLUMNS = (
    "ionization_mechanism_id",
    "target_compound_id",
    "target_ion_id",
    "owner_peak_assignment_id",
)

# Vacuum after 1 % of a table plus a floor is dead, instead of the default
# 20 %, so churned space returns to the free-space map between passes. The
# floors follow the tables' sizes: anchors run to thousands of rows per batch,
# occurrences and ledger rows to thousands per sample. `fillfactor` applies to
# batch_peak alone - the one table that is rewritten in place.
_STORAGE_OPTIONS = {
    "batch_peak": (
        "autovacuum_vacuum_scale_factor = 0.01, "
        "autovacuum_vacuum_threshold = 1000, fillfactor = 70"
    ),
    "batch_peak_occurrence": (
        "autovacuum_vacuum_scale_factor = 0.01, autovacuum_vacuum_threshold = 10000"
    ),
    "peak_assignment": (
        "autovacuum_vacuum_scale_factor = 0.01, autovacuum_vacuum_threshold = 100000"
    ),
}
_STORAGE_RESET = {
    "batch_peak": (
        "autovacuum_vacuum_scale_factor, autovacuum_vacuum_threshold, fillfactor"
    ),
    "batch_peak_occurrence": (
        "autovacuum_vacuum_scale_factor, autovacuum_vacuum_threshold"
    ),
    "peak_assignment": "autovacuum_vacuum_scale_factor, autovacuum_vacuum_threshold",
}


def upgrade() -> None:
    # --- peak_assignment: two redundant indexes gone, four made partial ---
    op.drop_index(
        op.f("ix_peak_assignment_peak_assignment_run_id"),
        table_name="peak_assignment",
    )
    op.drop_index(
        op.f("ix_peak_assignment_sample_peak_id"), table_name="peak_assignment"
    )
    for column in _PARTIAL_INDEX_COLUMNS:
        name = op.f(f"ix_peak_assignment_{column}")
        op.drop_index(name, table_name="peak_assignment")
        op.create_index(
            name,
            "peak_assignment",
            [column],
            unique=False,
            postgresql_where=sa.text(f"{column} IS NOT NULL"),
        )

    # --- batch_peak_occurrence: a member's identity as its key ---
    op.drop_constraint(
        op.f("pk_batch_peak_occurrence"), "batch_peak_occurrence", type_="primary"
    )
    op.drop_constraint(
        "uq_batch_peak_occurrence_batch_peak_id_sample_item_id",
        "batch_peak_occurrence",
        type_="unique",
    )
    op.drop_index(
        op.f("ix_batch_peak_occurrence_batch_peak_id"),
        table_name="batch_peak_occurrence",
    )
    op.drop_column("batch_peak_occurrence", "batch_peak_occurrence_id")
    op.create_primary_key(
        op.f("pk_batch_peak_occurrence"),
        "batch_peak_occurrence",
        ["batch_peak_id", "sample_item_id"],
    )

    # --- storage parameters for the churn ---
    for table, options in _STORAGE_OPTIONS.items():
        op.execute(f"ALTER TABLE {table} SET ({options})")


def downgrade() -> None:
    for table, options in _STORAGE_RESET.items():
        op.execute(f"ALTER TABLE {table} RESET ({options})")

    # The surrogate is minted afresh. It was random to begin with and nothing
    # referenced it, so any unique 32-character string is as good as the one
    # the row used to carry.
    op.drop_constraint(
        op.f("pk_batch_peak_occurrence"), "batch_peak_occurrence", type_="primary"
    )
    op.add_column(
        "batch_peak_occurrence",
        sa.Column("batch_peak_occurrence_id", sa.String(length=32), nullable=True),
    )
    op.execute(
        "UPDATE batch_peak_occurrence SET batch_peak_occurrence_id = md5("
        "random()::text || clock_timestamp()::text || batch_peak_id || sample_item_id)"
    )
    op.alter_column("batch_peak_occurrence", "batch_peak_occurrence_id", nullable=False)
    op.create_primary_key(
        op.f("pk_batch_peak_occurrence"),
        "batch_peak_occurrence",
        ["batch_peak_occurrence_id"],
    )
    op.create_unique_constraint(
        "uq_batch_peak_occurrence_batch_peak_id_sample_item_id",
        "batch_peak_occurrence",
        ["batch_peak_id", "sample_item_id"],
    )
    op.create_index(
        op.f("ix_batch_peak_occurrence_batch_peak_id"),
        "batch_peak_occurrence",
        ["batch_peak_id"],
        unique=False,
    )

    for column in _PARTIAL_INDEX_COLUMNS:
        name = op.f(f"ix_peak_assignment_{column}")
        op.drop_index(name, table_name="peak_assignment")
        op.create_index(name, "peak_assignment", [column], unique=False)
    op.create_index(
        op.f("ix_peak_assignment_sample_peak_id"),
        "peak_assignment",
        ["sample_peak_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_peak_assignment_peak_assignment_run_id"),
        "peak_assignment",
        ["peak_assignment_run_id"],
        unique=False,
    )
