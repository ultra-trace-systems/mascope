"""Add agent_device.instrument and agent_device.last_seen_version

What a paired agent reports about itself, so Paired machines can show which
instrument each machine watches and which agent release it runs.

``instrument`` comes from the pairing request and, for a device paired before
the server knew the field, from the first upload whose metadata carries it; an
upload never overwrites a value already on the row. ``last_seen_version`` is
written next to ``last_seen_at`` from the ``X-Agent-Version`` header, and from
the pairing request at approval.

Both are reported by the agent, not verified, and neither is read by routing:
the instrument of an upload still comes from the file name. Nullable with no
backfill, because a device paired by an agent that predates the fields has
nothing to report until it uploads or renews with one that does.

Revision ID: 8e5f0b3a2d71
Revises: 21a7c103f3b0
Create Date: 2026-09-03 21:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "8e5f0b3a2d71"
down_revision: Union[str, Sequence[str], None] = "21a7c103f3b0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "agent_device",
        sa.Column("instrument", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "agent_device",
        sa.Column("last_seen_version", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_device", "last_seen_version")
    op.drop_column("agent_device", "instrument")
