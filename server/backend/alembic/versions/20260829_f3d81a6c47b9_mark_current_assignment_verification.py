"""Mark the current assignment verification instead of deriving it

`assignment_verification` is append-only, and every consumer was left to
re-derive "the current verdict" as the latest by `verified_utc` within a stable
identity. The frontend did that; `recalibrate_instrument` did not, and so fit
its Platt curve on the whole history - a user who changed their mind
contributed one label to each class, at an identical evidence value.

This adds `superseded_utc`: NULL on the one live verdict per identity, and on a
replaced verdict the moment it was replaced. The history is kept - a superseded
row's score snapshot is still a valid (score, label) pair for the score it was
judged against, which is why the rows are stamped rather than deleted.

The backfill stamps every row that a later verdict on the same identity
replaced, using that successor's `verified_utc`. Ordering breaks ties on
`verified_utc` with `assignment_verification_id` so the choice of survivor is
deterministic and exactly one row per identity is left live - which is what
lets the partial unique index be created immediately afterwards.

NULLS NOT DISTINCT on that index: `assigned_formula` and
`ionization_mechanism_id` are both nullable, and under the default NULLS
DISTINCT two live verdicts on a formula-less peak would both be accepted.

Revision ID: f3d81a6c47b9
Revises: c1e7b409f2a5
Create Date: 2026-08-29 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "f3d81a6c47b9"
down_revision: Union[str, Sequence[str], None] = "c1e7b409f2a5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# PARTITION BY groups NULLs together (unlike a default unique index), which is
# why the index below is created NULLS NOT DISTINCT - the two must agree or the
# backfill would leave a pair the index then rejects.
_BACKFILL = """
UPDATE assignment_verification AS av
SET superseded_utc = successor.next_verified_utc
FROM (
    SELECT
        assignment_verification_id,
        LEAD(verified_utc) OVER (
            PARTITION BY
                sample_item_id,
                sample_peak_id,
                assigned_formula,
                ionization_mechanism_id
            ORDER BY verified_utc, assignment_verification_id
        ) AS next_verified_utc
    FROM assignment_verification
) AS successor
WHERE av.assignment_verification_id = successor.assignment_verification_id
  AND successor.next_verified_utc IS NOT NULL
"""


def upgrade() -> None:
    op.add_column(
        "assignment_verification",
        sa.Column("superseded_utc", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.execute(_BACKFILL)
    op.create_index(
        "uq_assignment_verification_current",
        "assignment_verification",
        [
            "sample_item_id",
            "sample_peak_id",
            "assigned_formula",
            "ionization_mechanism_id",
        ],
        unique=True,
        postgresql_where=sa.text("superseded_utc IS NULL"),
        postgresql_nulls_not_distinct=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_assignment_verification_current",
        table_name="assignment_verification",
    )
    op.drop_column("assignment_verification", "superseded_utc")
