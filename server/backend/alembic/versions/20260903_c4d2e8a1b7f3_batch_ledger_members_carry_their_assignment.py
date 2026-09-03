"""Batch ledger members carry their assignment: candidate registry, role, family link, P(correct)

The batch ledger is derived from the per-sample ledger, and until this revision
it stayed dependent on it: recomputing an anchor's consensus joined every
member back to its ``peak_assignment`` row for the ion formula, the ionization
mechanism, the calibrated probability, the member's role and its family link.
A member whose row had gone - a run deleted outright, or pruned - contributed
nothing to those, and an anchor recomputed over such members lost its ion
formula, mechanism and isotopologue link for good.

This revision puts on the member what the vote reads, so the batch ledger
stands on its own (``docs/dev/peak_assignment_batch_primary.md``, the first
step of that plan):

- ``batch_peak.candidates``: the anchor's registry of the assignment
  identities its members have carried - a list of
  ``{formula, ion_formula, ionization_mechanism_id}`` - append-only, so a
  member's index into it stays valid.
- ``batch_peak_occurrence.candidate``: which registry entry this member's
  assignment is; NULL for an unassigned member.
- ``batch_peak_occurrence.role`` and ``owner_batch_peak_id``: the member's
  per-sample role and, for an isotopologue, the anchor its owning peak folded
  into in the same sample - the hop the recompute used to walk through the
  ledger.
- ``batch_peak_occurrence.p_correct``: the member's calibrated probability.

All four are backfilled from the ledger rows still linked. A member whose row
is already gone keeps the formula its occurrence denormalized and gets a
registry entry with no ion formula or mechanism - exactly what the recompute
could recover for it before, and no less. Plain SQL, batched by nothing: the
batch tables exist only on deployments that already run ingest-time
assignment, and the largest such ledger this was tried on rewrote in seconds.

Revision ID: c4d2e8a1b7f3
Revises: 21a7c103f3b0
Create Date: 2026-09-03 17:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "c4d2e8a1b7f3"
down_revision: Union[str, Sequence[str], None] = "21a7c103f3b0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# The member's own fields, from the ledger row it still links to. The owner
# anchor is the one the owning assignment's own occurrence sits in; an owner
# with no occurrence (dropped from the fold) resolves to NULL, as it did when
# the recompute walked the same hop. `p_correct` is read only when the JSON
# holds a number - a JSON null, or a provenance with no such key, stays NULL.
_BACKFILL_MEMBER_FIELDS = """
    UPDATE batch_peak_occurrence AS o
    SET role = p.role,
        p_correct = CASE
            WHEN jsonb_typeof(p.provenance::jsonb -> 'p_correct') = 'number'
            THEN (p.provenance::jsonb ->> 'p_correct')::float8
        END,
        owner_batch_peak_id = CASE
            WHEN p.role = 'iso_child' THEN own.batch_peak_id
        END
    FROM peak_assignment AS p
    LEFT JOIN batch_peak_occurrence AS own
        ON own.peak_assignment_id = p.owner_peak_assignment_id
    WHERE p.peak_assignment_id = o.peak_assignment_id
"""

# Every distinct assignment identity an anchor's members carry, numbered per
# anchor in a deterministic order. The formula comes from the occurrence (it is
# denormalized there and survives the ledger row), the ion formula and
# mechanism from the ledger row where it still exists - so a dead-linked
# member's formula becomes an identity of its own, without the two.
_NUMBERED_IDENTITIES = """
    WITH identities AS (
        SELECT DISTINCT o.batch_peak_id,
               o.assigned_formula AS formula,
               p.ion_formula,
               p.ionization_mechanism_id
        FROM batch_peak_occurrence AS o
        LEFT JOIN peak_assignment AS p
            ON p.peak_assignment_id = o.peak_assignment_id
        WHERE o.assigned_formula IS NOT NULL
    ),
    numbered AS (
        SELECT batch_peak_id, formula, ion_formula, ionization_mechanism_id,
               row_number() OVER (
                   PARTITION BY batch_peak_id
                   ORDER BY formula,
                            ion_formula NULLS LAST,
                            ionization_mechanism_id NULLS LAST
               ) - 1 AS idx
        FROM identities
    )
"""

_BACKFILL_CANDIDATES = (
    _NUMBERED_IDENTITIES
    + """
    UPDATE batch_peak AS b
    SET candidates = (
        SELECT json_agg(
                   json_build_object(
                       'formula', n.formula,
                       'ion_formula', n.ion_formula,
                       'ionization_mechanism_id', n.ionization_mechanism_id
                   )
                   ORDER BY n.idx
               )
        FROM numbered AS n
        WHERE n.batch_peak_id = b.batch_peak_id
    )
    WHERE EXISTS (
        SELECT 1 FROM numbered AS n WHERE n.batch_peak_id = b.batch_peak_id
    )
"""
)

# Each assigned member points at the entry matching its own identity, with
# IS NOT DISTINCT FROM so a NULL ion formula matches a NULL ion formula.
_BACKFILL_MEMBER_CANDIDATE = (
    _NUMBERED_IDENTITIES
    + """
    UPDATE batch_peak_occurrence AS o
    SET candidate = m.idx
    FROM (
        SELECT o2.batch_peak_id, o2.sample_item_id, n.idx
        FROM batch_peak_occurrence AS o2
        LEFT JOIN peak_assignment AS p
            ON p.peak_assignment_id = o2.peak_assignment_id
        JOIN numbered AS n
            ON n.batch_peak_id = o2.batch_peak_id
           AND n.formula = o2.assigned_formula
           AND n.ion_formula IS NOT DISTINCT FROM p.ion_formula
           AND n.ionization_mechanism_id
               IS NOT DISTINCT FROM p.ionization_mechanism_id
        WHERE o2.assigned_formula IS NOT NULL
    ) AS m
    WHERE o.batch_peak_id = m.batch_peak_id
      AND o.sample_item_id = m.sample_item_id
"""
)


def upgrade() -> None:
    op.add_column("batch_peak", sa.Column("candidates", sa.JSON(), nullable=True))
    op.add_column(
        "batch_peak_occurrence",
        sa.Column("candidate", sa.SmallInteger(), nullable=True),
    )
    op.add_column(
        "batch_peak_occurrence",
        sa.Column("role", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "batch_peak_occurrence",
        sa.Column("owner_batch_peak_id", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "batch_peak_occurrence", sa.Column("p_correct", sa.Float(), nullable=True)
    )
    op.create_foreign_key(
        op.f("fk_batch_peak_occurrence_owner_batch_peak_id_batch_peak"),
        "batch_peak_occurrence",
        "batch_peak",
        ["owner_batch_peak_id"],
        ["batch_peak_id"],
        ondelete="SET NULL",
    )
    # Partial, as the ledger's nullable references are: most members are not
    # isotopologues, and the SET NULL action above is served by `= $1`, which
    # implies IS NOT NULL.
    op.create_index(
        op.f("ix_batch_peak_occurrence_owner_batch_peak_id"),
        "batch_peak_occurrence",
        ["owner_batch_peak_id"],
        unique=False,
        postgresql_where=sa.text("owner_batch_peak_id IS NOT NULL"),
    )
    op.execute(_BACKFILL_MEMBER_FIELDS)
    op.execute(_BACKFILL_CANDIDATES)
    op.execute(_BACKFILL_MEMBER_CANDIDATE)


def downgrade() -> None:
    op.drop_index(
        op.f("ix_batch_peak_occurrence_owner_batch_peak_id"),
        table_name="batch_peak_occurrence",
    )
    op.drop_constraint(
        op.f("fk_batch_peak_occurrence_owner_batch_peak_id_batch_peak"),
        "batch_peak_occurrence",
        type_="foreignkey",
    )
    op.drop_column("batch_peak_occurrence", "p_correct")
    op.drop_column("batch_peak_occurrence", "owner_batch_peak_id")
    op.drop_column("batch_peak_occurrence", "role")
    op.drop_column("batch_peak_occurrence", "candidate")
    op.drop_column("batch_peak", "candidates")
