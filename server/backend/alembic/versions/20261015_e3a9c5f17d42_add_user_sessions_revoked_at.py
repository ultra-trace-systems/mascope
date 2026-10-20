"""Add sessions_revoked_at to user

A web session is a signed token the server keeps no record of, so until now
nothing could end one before it expired. The token now carries the time it was
issued, and a session issued before this cutoff - or before the account's last
password change, whichever is later - is refused. Stamped by an account disable,
the forced-password-change sweep, and the sign-out-everywhere action.

Lands NULL on every row: no cutoff, so no session is ended by the upgrade
through this column.

Revision ID: e3a9c5f17d42
Revises: 67479416fc78
Create Date: 2026-10-15 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "e3a9c5f17d42"
down_revision: Union[str, Sequence[str], None] = "67479416fc78"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "user",
        sa.Column("sessions_revoked_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user", "sessions_revoked_at")
