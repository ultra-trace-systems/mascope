"""Tighten autovacuum thresholds on the churn-heavy match tables

Match refresh removes and re-persists whole per-sample match sets, so the
match result tables see delete+insert churn that dwarfs their live size
(production match_isotope: ~1.1 billion row deletes against ~42 million live
rows). With the default autovacuum_vacuum_scale_factor of 0.2, a 42M-row
table accumulates ~8.4M dead tuples before autovacuum even starts, so each
refresh cycle extends the heap and indexes instead of reusing the space the
previous cycle freed - the table grows without bound (observed ~20x bloat,
hundreds of GB of dead space).

Per-table thresholds make autovacuum run after ~100k + 1% of rows are dead,
so churned space returns to the free space map between refresh cycles and
steady-state size stays a small multiple of the live data. This bounds
future growth only; space already leaked must be reclaimed once with
VACUUM FULL (see remove_unmatched_match_isotopes docstring).

Setting autovacuum storage parameters takes only a SHARE UPDATE EXCLUSIVE
lock, so this migration does not block concurrent reads or writes.

Revision ID: d8f3a6c1e9b4
Revises: a1f8c25d9e47
Create Date: 2026-08-11 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "d8f3a6c1e9b4"
down_revision: Union[str, Sequence[str], None] = "a1f8c25d9e47"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# The three large match tables rewritten by refresh; the remaining match
# tables (match_collection, match_sample, match_rating) stay in the
# hundreds of thousands of rows where the default thresholds are fine.
TABLES = ("match_isotope", "match_ion", "match_compound")

AUTOVACUUM_OPTIONS = (
    "autovacuum_vacuum_scale_factor = 0.01, autovacuum_vacuum_threshold = 100000"
)


def upgrade() -> None:
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} SET ({AUTOVACUUM_OPTIONS})")


def downgrade() -> None:
    for table in TABLES:
        op.execute(
            f"ALTER TABLE {table} RESET "
            "(autovacuum_vacuum_scale_factor, autovacuum_vacuum_threshold)"
        )
