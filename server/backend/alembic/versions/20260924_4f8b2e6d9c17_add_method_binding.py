"""Add method_binding: what chemistry an acquisition method has been seen to run

Revision ID: 4f8b2e6d9c17
Revises: 9a3c5e71b8d4
Create Date: 2026-09-24

The table starts empty. Rows arrive as files route - each file that binds on
a declaration, a person's choice or its filename token teaches its method's
key - and a deployment can fill it from what it has already routed with the
``backfill_method_bindings`` script. Nothing reads the rows to route yet.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "4f8b2e6d9c17"
down_revision: Union[str, Sequence[str], None] = "9a3c5e71b8d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "method_binding",
        sa.Column("method_binding_id", sa.String(length=16), nullable=False),
        sa.Column("binding_key", sa.String(length=64), nullable=False),
        sa.Column("instrument", sa.String(length=64), nullable=False),
        sa.Column("method_key", sa.String(length=256), nullable=False),
        sa.Column("signature_class", sa.String(length=512), nullable=False),
        sa.Column("ionization_mode_id", sa.String(length=16), nullable=True),
        sa.Column("chemistry_keys", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("first_seen", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("last_seen", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("n_streams", sa.Integer(), nullable=False),
        sa.Column("n_disagreements", sa.Integer(), nullable=False),
        sa.Column("last_sample_file_id", sa.String(length=16), nullable=True),
        sa.Column("last_chemistry_key", sa.String(length=512), nullable=True),
        sa.ForeignKeyConstraint(
            ["ionization_mode_id"],
            ["ionization_mode.ionization_mode_id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("method_binding_id"),
        sa.UniqueConstraint("binding_key", name="uq_method_binding_key"),
    )
    op.create_index(
        op.f("ix_method_binding_ionization_mode_id"),
        "method_binding",
        ["ionization_mode_id"],
    )


def downgrade() -> None:
    # The learned history goes with the table. Nothing else refers to it, and
    # a re-upgrade relearns it from the files that taught it, either as they
    # are reprocessed or through the backfill script.
    op.drop_index(
        op.f("ix_method_binding_ionization_mode_id"), table_name="method_binding"
    )
    op.drop_table("method_binding")
