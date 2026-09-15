"""Add machine account type and device machine-account link

A user account is now either a person or a machine (an instrument agent's
identity). ``user.account_type`` defaults to 'person', so every existing row -
and every account created the ordinary way - is a person and behaves exactly as
before. ``agent_device.machine_user_id`` records the machine account a device
authenticates as; pairing approval creates that account.

Revision ID: c9f1a7e35d82
Revises: b4e7d19a6c2f
Create Date: 2026-08-19 15:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "c9f1a7e35d82"
down_revision: Union[str, Sequence[str], None] = "b4e7d19a6c2f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "user",
        sa.Column(
            "account_type",
            sa.String(length=16),
            server_default=sa.text("'person'"),
            nullable=False,
        ),
    )

    op.add_column(
        "agent_device", sa.Column("machine_user_id", sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        "fk_agent_device_machine_user_id_user",
        "agent_device",
        "user",
        ["machine_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_agent_device_machine_user_id",
        "agent_device",
        ["machine_user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_agent_device_machine_user_id", table_name="agent_device")
    op.drop_constraint(
        "fk_agent_device_machine_user_id_user", "agent_device", type_="foreignkey"
    )
    op.drop_column("agent_device", "machine_user_id")

    op.drop_column("user", "account_type")
