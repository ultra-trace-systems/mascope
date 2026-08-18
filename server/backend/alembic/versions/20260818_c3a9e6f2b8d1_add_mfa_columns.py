"""Add TOTP multi-factor authentication columns and recovery codes

An account may hold a second authentication factor. The columns land in the
"no second factor" state and existing rows are backfilled the same way: this
migration runs unattended on every production start, so enrolling or requiring
anything here would lock out every user of every server on a routine update.
Arming a factor is always a deliberate act by the account holder.

``mfa_secret`` holds the TOTP seed encrypted at rest, so a database dump alone
does not yield working seeds; the key lives outside the database, in the
deployment's secrets directory.

Revision ID: c3a9e6f2b8d1
Revises: b7e4c9a2f1d3
Create Date: 2026-08-18 10:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "c3a9e6f2b8d1"
down_revision: Union[str, Sequence[str], None] = "b7e4c9a2f1d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("user", sa.Column("mfa_secret", sa.String(length=512), nullable=True))
    op.add_column(
        "user",
        sa.Column(
            "mfa_enabled",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        "user",
        sa.Column("mfa_confirmed_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column(
        "user", sa.Column("mfa_last_timestep", sa.BigInteger(), nullable=True)
    )

    op.create_table(
        "user_recovery_code",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("used_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "code_hash", name="uq_recovery_code_user_hash"),
    )
    op.create_index(
        "ix_user_recovery_code_user_id", "user_recovery_code", ["user_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_user_recovery_code_user_id", table_name="user_recovery_code")
    op.drop_table("user_recovery_code")
    op.drop_column("user", "mfa_last_timestep")
    op.drop_column("user", "mfa_confirmed_at")
    op.drop_column("user", "mfa_enabled")
    op.drop_column("user", "mfa_secret")
