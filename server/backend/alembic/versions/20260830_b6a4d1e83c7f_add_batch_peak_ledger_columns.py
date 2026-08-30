"""Add batch peak ledger columns: max_intensity and isotopologue_of

Two member aggregates materialized onto ``batch_peak`` so the ledger keeps its
defining property -- one cheap read of the batch peaks alone, never a join to
the occurrence table (docs/dev/peak_assignment_batch.md, section 6):

- ``max_intensity``: the brightest member's intensity, in the unit
  ``intensity_variable`` already names. The ledger's sortable intensity column.
- ``isotopologue_of``: the batch peak this one is an isotopologue of.
  Batch peaks are bare m/z anchors and carry no family link of their own, so it
  is derived from the members' per-sample ``PeakAssignment`` rows: an
  ``iso_child`` member's ``owner_peak_assignment_id`` names the owning
  assignment, whose own occurrence in that same sample names the owner's
  anchor. A batch peak is FOLDED as an isotopologue when a strict majority of its
  ASSIGNED members vote for one owner anchor -- consistent with the consensus, which
  decides confidence over the assigned members and keeps prevalence separate.

Both are backfilled here, from the occurrences that are their source of truth,
so a batch folded before this revision folds its isotopologues and shows its
intensities without waiting for someone to press "Compute batch peaks".

Revision ID: b6a4d1e83c7f
Revises: f3d81a6c47b9
Create Date: 2026-08-30 09:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "b6a4d1e83c7f"
down_revision: Union[str, Sequence[str], None] = "f3d81a6c47b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# The brightest member per batch peak. NULL members (a peak folded in without an
# intensity) are skipped rather than counted as zero, so a batch peak with no
# usable intensity keeps NULL and the ledger shows a dash instead of a 0.
_BACKFILL_MAX_INTENSITY = """
    UPDATE batch_peak
    SET max_intensity = agg.max_intensity
    FROM (
        SELECT batch_peak_id, max(intensity) AS max_intensity
        FROM batch_peak_occurrence
        WHERE intensity IS NOT NULL
        GROUP BY batch_peak_id
    ) AS agg
    WHERE batch_peak.batch_peak_id = agg.batch_peak_id
"""

# The same vote the consensus now casts at fold time, expressed once over the
# whole table. `assigned` is the denominator -- members carrying a formula, the
# population the consensus itself measures agreement over. `vote` counts, per
# batch peak, how many of its members are iso_child rows whose owner assignment
# has an occurrence of its own in the SAME sample, keyed by the anchor that
# occurrence belongs to. DISTINCT ON takes the leading owner; the strict
# majority in the UPDATE is what makes that leader unique (two owners cannot
# both hold more than half), so the tie-break in ORDER BY only keeps the plan
# deterministic.
#
# "Carrying a formula" is spelled `IS NOT NULL AND <> ''` because the Python
# side tests the value for truthiness, where the empty string is not a formula.
# A row backfilled under a looser rule than the one its next fold applies would
# disagree with its own recompute, which is the one difference between these two
# implementations that would ever be seen.
_BACKFILL_ISOTOPOLOGUE_OF = """
    WITH assigned AS (
        SELECT batch_peak_id, count(*) AS n_assigned
        FROM batch_peak_occurrence
        WHERE assigned_formula IS NOT NULL AND assigned_formula <> ''
        GROUP BY batch_peak_id
    ),
    vote AS (
        SELECT child.batch_peak_id AS batch_peak_id,
               owner.batch_peak_id AS owner_batch_peak_id,
               count(*) AS votes
        FROM batch_peak_occurrence AS child
        JOIN peak_assignment AS child_assignment
          ON child_assignment.peak_assignment_id = child.peak_assignment_id
        JOIN batch_peak_occurrence AS owner
          ON owner.peak_assignment_id = child_assignment.owner_peak_assignment_id
         AND owner.sample_item_id = child.sample_item_id
        WHERE child_assignment.role = 'iso_child'
          AND child.assigned_formula IS NOT NULL
          AND child.assigned_formula <> ''
          AND owner.batch_peak_id <> child.batch_peak_id
        GROUP BY 1, 2
    ),
    winner AS (
        SELECT DISTINCT ON (vote.batch_peak_id)
               vote.batch_peak_id,
               vote.owner_batch_peak_id,
               vote.votes,
               assigned.n_assigned
        FROM vote
        JOIN assigned ON assigned.batch_peak_id = vote.batch_peak_id
        ORDER BY vote.batch_peak_id, vote.votes DESC, vote.owner_batch_peak_id
    )
    UPDATE batch_peak
    SET isotopologue_of = winner.owner_batch_peak_id
    FROM winner
    WHERE batch_peak.batch_peak_id = winner.batch_peak_id
      AND winner.votes * 2 > winner.n_assigned
"""


def upgrade() -> None:
    op.add_column("batch_peak", sa.Column("max_intensity", sa.Float(), nullable=True))
    op.add_column(
        "batch_peak", sa.Column("isotopologue_of", sa.String(length=16), nullable=True)
    )
    op.create_index(
        op.f("ix_batch_peak_isotopologue_of"),
        "batch_peak",
        ["isotopologue_of"],
        unique=False,
    )
    op.create_foreign_key(
        op.f("fk_batch_peak_isotopologue_of_batch_peak"),
        "batch_peak",
        "batch_peak",
        ["isotopologue_of"],
        ["batch_peak_id"],
        ondelete="SET NULL",
    )
    op.execute(_BACKFILL_MAX_INTENSITY)
    op.execute(_BACKFILL_ISOTOPOLOGUE_OF)


def downgrade() -> None:
    # The columns are derived, so dropping them loses nothing that the
    # occurrences cannot produce again.
    op.drop_index(op.f("ix_batch_peak_isotopologue_of"), table_name="batch_peak")
    op.drop_column("batch_peak", "isotopologue_of")
    op.drop_column("batch_peak", "max_intensity")
