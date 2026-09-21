"""Add the notification table

Notifications were live only: a Socket.IO message reached the browsers open
when it was sent, and nobody else. A notification row is kept for one person
until they read it, so the outcome of an instrument's processing that needs
someone - files that failed, need a chemistry, or could not be calibrated -
reaches the people answerable for the instrument even when none of them was
signed in at the time (#1910).

One unread row per person, kind and instrument stands for every such file
until it is read; the partial unique index holds that to one open row.

Revision ID: 8d2f6a1c4e93
Revises: 5b9e2c7d1a40
Create Date: 2026-09-21 13:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "8d2f6a1c4e93"
down_revision: Union[str, Sequence[str], None] = "5b9e2c7d1a40"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "notification",
        sa.Column("notification_id", sa.String(length=16), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("instrument", sa.String(length=64), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("count", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("created_utc", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("updated_utc", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("read_utc", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("resolved_utc", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
            name=op.f("fk_notification_user_id_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("notification_id", name=op.f("pk_notification")),
    )
    op.create_index(
        op.f("ix_notification_user_id"), "notification", ["user_id"], unique=False
    )
    op.create_index(
        "uq_notification_open",
        "notification",
        ["user_id", "kind", "instrument"],
        unique=True,
        postgresql_where=sa.text("read_utc IS NULL"),
        postgresql_nulls_not_distinct=True,
    )


def downgrade() -> None:
    op.drop_index("uq_notification_open", table_name="notification")
    op.drop_index(op.f("ix_notification_user_id"), table_name="notification")
    op.drop_table("notification")
