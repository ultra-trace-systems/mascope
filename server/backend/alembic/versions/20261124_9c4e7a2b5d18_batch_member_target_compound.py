"""A batch member's own target compound: batch_peak_occurrence.target_compound_id

A batch peak's registry keeps one entry per identity (formula, ion formula,
mechanism), and an entry records the target compound of the first member that
brought it from the target library. Two members that matched two isomeric
compounds through the same ion share the entry, so the second compound was
lost: the batch ledger named the first for the whole anchor, and a sample read
off it showed the first on every derived row (MAS-992).

- ``batch_peak_occurrence.target_compound_id`` is the compound the member's
  own sample matched, written by the fold. Nullable with no default, so the
  column is added without rewriting the table, the batch ledger's largest.
  Nothing is backfilled and nothing re-folded: a member folded before this
  revision reads NULL, and the readers answer it off its registry entry as
  before, until its sample folds again.
- A foreign key to ``target_compound``, ``ON DELETE SET NULL`` as
  ``peak_assignment.target_compound_id`` has, so a deleted compound is no
  longer named and no longer counts against the members that agree. The
  index that serves the action is partial (``IS NOT NULL``), as the ledger's
  other nullable references are: most members name no target, so it holds only
  the ones that do and a compound's deletion reads it rather than the table.

Revision ID: 9c4e7a2b5d18
Revises: b5e3a9d2c7f1
Create Date: 2026-11-24 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "9c4e7a2b5d18"
down_revision: Union[str, Sequence[str], None] = "b5e3a9d2c7f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "batch_peak_occurrence",
        sa.Column("target_compound_id", sa.String(length=16), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_batch_peak_occurrence_target_compound_id_target_compound"),
        "batch_peak_occurrence",
        "target_compound",
        ["target_compound_id"],
        ["target_compound_id"],
        ondelete="SET NULL",
    )
    op.create_index(
        op.f("ix_batch_peak_occurrence_target_compound_id"),
        "batch_peak_occurrence",
        ["target_compound_id"],
        unique=False,
        postgresql_where=sa.text("target_compound_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_batch_peak_occurrence_target_compound_id"),
        table_name="batch_peak_occurrence",
    )
    op.drop_constraint(
        op.f("fk_batch_peak_occurrence_target_compound_id_target_compound"),
        "batch_peak_occurrence",
        type_="foreignkey",
    )
    op.drop_column("batch_peak_occurrence", "target_compound_id")
