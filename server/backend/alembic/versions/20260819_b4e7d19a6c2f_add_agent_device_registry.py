"""Add the agent device registry and upload attribution columns

A paired agent machine becomes a first-class row (``agent_device``), access
tokens can point at the machine holding them, and ``sample_file`` records who
uploaded it - the device for agent uploads, the user for interactive ones -
plus how its UTC timestamp was derived (reported IANA zone and offset source).

Every new column is nullable and existing rows are left NULL: this migration
runs unattended on production start, and rows created before the registry are
genuinely unattributable - recording NULL is the honest backfill. Tokens
issued before the registry keep working; the ``require_device_tokens``
deployment flag is what ends that, per deployment, at rollout.

Revision ID: b4e7d19a6c2f
Revises: c3a9e6f2b8d1
Create Date: 2026-08-19 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "b4e7d19a6c2f"
down_revision: Union[str, Sequence[str], None] = "c3a9e6f2b8d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_device",
        sa.Column("device_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("service_name", sa.String(length=50), nullable=False),
        sa.Column("sponsor_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["sponsor_user_id"], ["user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("device_id"),
    )
    op.create_index(
        "ix_agent_device_sponsor_user_id",
        "agent_device",
        ["sponsor_user_id"],
        unique=False,
    )

    op.add_column("access_token", sa.Column("device_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_access_token_device_id_agent_device",
        "access_token",
        "agent_device",
        ["device_id"],
        ["device_id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_access_token_device_id", "access_token", ["device_id"], unique=False
    )

    op.add_column(
        "sample_file", sa.Column("uploaded_by_user_id", sa.Integer(), nullable=True)
    )
    op.add_column(
        "sample_file", sa.Column("uploaded_by_device_id", sa.Integer(), nullable=True)
    )
    op.add_column(
        "sample_file",
        sa.Column("acquisition_timezone", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "sample_file",
        sa.Column("utc_offset_source", sa.String(length=8), nullable=True),
    )
    op.create_foreign_key(
        "fk_sample_file_uploaded_by_user_id_user",
        "sample_file",
        "user",
        ["uploaded_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_sample_file_uploaded_by_device_id_agent_device",
        "sample_file",
        "agent_device",
        ["uploaded_by_device_id"],
        ["device_id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_sample_file_uploaded_by_device_id",
        "sample_file",
        ["uploaded_by_device_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_sample_file_uploaded_by_device_id", table_name="sample_file")
    op.drop_constraint(
        "fk_sample_file_uploaded_by_device_id_agent_device",
        "sample_file",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_sample_file_uploaded_by_user_id_user", "sample_file", type_="foreignkey"
    )
    op.drop_column("sample_file", "utc_offset_source")
    op.drop_column("sample_file", "acquisition_timezone")
    op.drop_column("sample_file", "uploaded_by_device_id")
    op.drop_column("sample_file", "uploaded_by_user_id")

    op.drop_index("ix_access_token_device_id", table_name="access_token")
    op.drop_constraint(
        "fk_access_token_device_id_agent_device", "access_token", type_="foreignkey"
    )
    op.drop_column("access_token", "device_id")

    op.drop_index("ix_agent_device_sponsor_user_id", table_name="agent_device")
    op.drop_table("agent_device")
