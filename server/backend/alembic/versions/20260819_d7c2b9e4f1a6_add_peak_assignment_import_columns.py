"""Add peak assignment run import columns

Makes the run ledger able to hold assignment runs computed outside Mascope
(docs/dev/sdk_peak_assignment.md section 8). All additive:

- peak_assignment_run.engine: which engine produced the run. Existing rows are
  the in-app engine, so the column lands NOT NULL with server_default 'mascope'
  and Postgres backfills every existing row to it as part of ADD COLUMN. A
  NULL-means-in-app sentinel was rejected deliberately: it pushes a tri-state
  onto every consumer, and `engine <> 'peaky'` silently drops NULLs. The value
  space is constrained the same way engine_version is (String(64)), and the
  in-app identity is reserved from client payloads so the provenance badge
  cannot be forged.
- peak_assignment_run.tier_bands: the identified/candidate fit-score thresholds
  the producing engine tiered with. A first-class column rather than a key in
  the opaque `config`, because the server validates every row's tier against it.
- peak_assignment_run.calibration: the importing client's calibration state.
  Also its own column rather than a reserved key in `config`: `config` is stored
  verbatim, and `calibration` is a plausible key in a client-side-calibrating
  engine's own config, so nesting it would risk a silent collision on exactly
  the field the run's calibration badge depends on.
- peak_assignment_run.import_key: the importing client's own id for the logical
  import, which makes the request that creates a run idempotent. The row-offset
  check that makes the later chunks idempotent cannot help the first one -
  there is no run id yet to be idempotent about - so without this an HTTP retry
  of the create mints a second run for the sample. Unique per sample; NULL for
  in-app runs and for imports that supplied none, and Postgres treats NULLs in a
  unique constraint as distinct, so many such rows coexist.
- peak_assignment.owner_sample_peak_id: the client-side owner reference an
  imported iso_child row carries. Client payloads cannot supply
  owner_peak_assignment_id (those ids do not exist until the server mints
  them), and an import is assembled over several requests, so the reference is
  staged on the row and resolved to owner_peak_assignment_id when the import
  finalizes. NULL for in-app runs, which link owners directly.
- ix_peak_assignment_run_sample_item_id_status: admission reads durable run
  state - "does this sample have a non-terminal run" - before every import and
  every in-app assign, which is a (sample_item_id, status) lookup.

The 'importing' status the import lifecycle adds needs no DDL: status is an
unconstrained String column.

Revision ID: d7c2b9e4f1a6
Revises: c3a9e6f2b8d1
Create Date: 2026-08-19 09:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "d7c2b9e4f1a6"
down_revision: Union[str, Sequence[str], None] = "c3a9e6f2b8d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "peak_assignment_run",
        sa.Column(
            "engine",
            sa.String(length=64),
            server_default=sa.text("'mascope'"),
            nullable=False,
        ),
    )
    op.add_column(
        "peak_assignment_run", sa.Column("tier_bands", sa.JSON(), nullable=True)
    )
    op.add_column(
        "peak_assignment_run", sa.Column("calibration", sa.JSON(), nullable=True)
    )
    op.add_column(
        "peak_assignment_run",
        sa.Column("import_key", sa.String(length=64), nullable=True),
    )
    op.create_unique_constraint(
        "uq_peak_assignment_run_sample_item_id_import_key",
        "peak_assignment_run",
        ["sample_item_id", "import_key"],
    )
    op.create_index(
        op.f("ix_peak_assignment_run_sample_item_id_status"),
        "peak_assignment_run",
        ["sample_item_id", "status"],
        unique=False,
    )
    op.add_column(
        "peak_assignment",
        sa.Column("owner_sample_peak_id", sa.String(length=20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("peak_assignment", "owner_sample_peak_id")
    op.drop_index(
        op.f("ix_peak_assignment_run_sample_item_id_status"),
        table_name="peak_assignment_run",
    )
    op.drop_constraint(
        "uq_peak_assignment_run_sample_item_id_import_key",
        "peak_assignment_run",
        type_="unique",
    )
    op.drop_column("peak_assignment_run", "import_key")
    op.drop_column("peak_assignment_run", "calibration")
    op.drop_column("peak_assignment_run", "tier_bands")
    op.drop_column("peak_assignment_run", "engine")
