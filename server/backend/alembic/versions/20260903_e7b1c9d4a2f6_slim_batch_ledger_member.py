"""Slim the batch ledger member: coded tier and role, single-precision numbers, the anchor offset for the m/z, no formula copy

A member row carried its peak's absolute m/z, its formula as text and its tier
and role as strings, all in double precision - about 190 bytes of tuple for a
row whose information is a handful of numbers and two indexes into its anchor.
With every ingested sample folding one member per detected peak
(``docs/dev/peak_assignment_batch_primary.md``, section 4.2), the member row is
where the per-peak cost of the batch ledger lives, so this revision makes it
carry only what a trace point and a vote need, in the smallest type that holds
it:

- ``tier`` and ``role`` become ``smallint`` codes (the tier's is its rank, the
  spelling the batch engine already compares on);
- ``intensity``, ``fit_score`` and ``p_correct`` become ``real`` - a chart
  y-value, a score in [0, 1] and a probability in [0, 1] need no more;
- ``sample_peak_mz`` becomes ``mz_delta_ppm``, the member's offset from its
  anchor's frozen m/z in ppm, from which the absolute m/z is recovered to well
  below the precision the peak file itself carries;
- ``assigned_formula`` goes: the member's ``candidate`` index into the anchor's
  registry already names it;
- the index on ``peak_assignment_id`` becomes partial, as the ledger's nullable
  references are: a member folded at ingest without a run links to no row.

The conversions keep what a development database holds (the feature is not in
production, so nothing here is a backfill in earnest); the downgrade restores
the wider columns and rebuilds the formula copy from the registry.

Revision ID: e7b1c9d4a2f6
Revises: c4d2e8a1b7f3
Create Date: 2026-09-03 21:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "e7b1c9d4a2f6"
down_revision: Union[str, Sequence[str], None] = "c4d2e8a1b7f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TABLE = "batch_peak_occurrence"

# Tier codes are the tier ranks (tiers.TIER_RANK); role codes are batch_peaks.ROLE_CODES.
_TIER_TO_CODE = (
    "CASE tier WHEN 'assigned' THEN 3 WHEN 'candidate' THEN 2"
    " WHEN 'below_assignability' THEN 1 WHEN 'unassigned' THEN 0 ELSE NULL END"
)
_CODE_TO_TIER = (
    "CASE tier WHEN 3 THEN 'assigned' WHEN 2 THEN 'candidate'"
    " WHEN 1 THEN 'below_assignability' WHEN 0 THEN 'unassigned' ELSE NULL END"
)
_ROLE_TO_CODE = (
    "CASE role WHEN 'unassigned' THEN 0 WHEN 'M0' THEN 1 WHEN 'iso_child' THEN 2"
    " WHEN 'reagent' THEN 3 WHEN 'artifact' THEN 4 ELSE NULL END"
)
_CODE_TO_ROLE = (
    "CASE role WHEN 0 THEN 'unassigned' WHEN 1 THEN 'M0' WHEN 2 THEN 'iso_child'"
    " WHEN 3 THEN 'reagent' WHEN 4 THEN 'artifact' ELSE NULL END"
)


def upgrade() -> None:
    # The offset from the anchor, computed while the absolute m/z is still here.
    op.add_column(_TABLE, sa.Column("mz_delta_ppm", sa.REAL(), nullable=True))
    op.execute(
        f"""
        UPDATE {_TABLE} AS o
        SET mz_delta_ppm = (o.sample_peak_mz - b.mz) / b.mz * 1e6
        FROM batch_peak AS b
        WHERE b.batch_peak_id = o.batch_peak_id
        """
    )
    op.drop_column(_TABLE, "sample_peak_mz")
    op.drop_column(_TABLE, "assigned_formula")
    op.execute(f"ALTER TABLE {_TABLE} ALTER COLUMN intensity TYPE real")
    op.execute(f"ALTER TABLE {_TABLE} ALTER COLUMN fit_score TYPE real")
    op.execute(f"ALTER TABLE {_TABLE} ALTER COLUMN p_correct TYPE real")
    op.execute(
        f"ALTER TABLE {_TABLE} ALTER COLUMN tier TYPE smallint USING ({_TIER_TO_CODE})"
    )
    op.execute(
        f"ALTER TABLE {_TABLE} ALTER COLUMN role TYPE smallint USING ({_ROLE_TO_CODE})"
    )
    op.drop_index(
        op.f("ix_batch_peak_occurrence_peak_assignment_id"), table_name=_TABLE
    )
    op.create_index(
        op.f("ix_batch_peak_occurrence_peak_assignment_id"),
        _TABLE,
        ["peak_assignment_id"],
        unique=False,
        postgresql_where=sa.text("peak_assignment_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_batch_peak_occurrence_peak_assignment_id"), table_name=_TABLE
    )
    op.create_index(
        op.f("ix_batch_peak_occurrence_peak_assignment_id"),
        _TABLE,
        ["peak_assignment_id"],
        unique=False,
    )
    op.execute(
        f"ALTER TABLE {_TABLE} ALTER COLUMN role TYPE VARCHAR(16) USING ({_CODE_TO_ROLE})"
    )
    op.execute(
        f"ALTER TABLE {_TABLE} ALTER COLUMN tier TYPE VARCHAR(24) USING ({_CODE_TO_TIER})"
    )
    op.execute(f"ALTER TABLE {_TABLE} ALTER COLUMN p_correct TYPE double precision")
    op.execute(f"ALTER TABLE {_TABLE} ALTER COLUMN fit_score TYPE double precision")
    op.execute(f"ALTER TABLE {_TABLE} ALTER COLUMN intensity TYPE double precision")
    # The formula copy, from the registry entry the member names.
    op.add_column(
        _TABLE, sa.Column("assigned_formula", sa.String(length=256), nullable=True)
    )
    op.execute(
        f"""
        UPDATE {_TABLE} AS o
        SET assigned_formula = b.candidates -> o.candidate ->> 'formula'
        FROM batch_peak AS b
        WHERE b.batch_peak_id = o.batch_peak_id AND o.candidate IS NOT NULL
        """
    )
    # The absolute m/z, from the anchor and the offset. Nullable on the way
    # back: the column was created NOT NULL by the ORM but rows may have been
    # written after this revision with no offset at all.
    op.add_column(_TABLE, sa.Column("sample_peak_mz", sa.Float(), nullable=True))
    op.execute(
        f"""
        UPDATE {_TABLE} AS o
        SET sample_peak_mz = b.mz * (1 + COALESCE(o.mz_delta_ppm, 0) / 1e6)
        FROM batch_peak AS b
        WHERE b.batch_peak_id = o.batch_peak_id
        """
    )
    op.drop_column(_TABLE, "mz_delta_ppm")
