"""Add forced password change columns to user

An account can be required to replace its password before it may use the
application again, which is how a deployment brings passwords predating the
current policy up to it. The columns land in the "nothing required" state and
existing rows are backfilled the same way: this migration runs unattended on
every production start, so arming the requirement here would lock out every
user of every server on a routine update. Requiring a change is always a
deliberate act, through the owner endpoint or the maintenance script.

Revision ID: b7e4c9a2f1d3
Revises: d8f3a6c1e9b4
Create Date: 2026-08-14 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "b7e4c9a2f1d3"
down_revision: Union[str, Sequence[str], None] = "d8f3a6c1e9b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "user",
        sa.Column(
            "must_change_password",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        "user",
        sa.Column("password_change_reason", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "user",
        sa.Column("password_changed_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user", "password_changed_at")
    op.drop_column("user", "password_change_reason")
    op.drop_column("user", "must_change_password")
