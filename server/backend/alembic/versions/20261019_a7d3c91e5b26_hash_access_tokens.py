"""Store access tokens as SHA-256 digests

The ``access_token`` table held each bearer token in clear, so a copy of the
table was a set of working credentials. Each row now holds the token's
SHA-256 digest instead (``token_hash``, the primary key), and every lookup
hashes the presented value. The upgrade hashes what is stored, in place, so
every token already issued keeps working; the plaintext column goes.

The downgrade cannot recover a token from its digest, so it deletes every row:
each SDK and agent token has to be regenerated, or the agent re-paired, after
it.

Revision ID: a7d3c91e5b26
Revises: 1658f7bece0a
Create Date: 2026-10-19 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "a7d3c91e5b26"
down_revision: Union[str, Sequence[str], None] = "1658f7bece0a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "access_token", sa.Column("token_hash", sa.String(length=64), nullable=True)
    )
    # The same digest mascope_backend.db.hash_access_token computes: SHA-256
    # of the token's UTF-8 bytes, in lowercase hex.
    op.execute(
        "UPDATE access_token "
        "SET token_hash = encode(sha256(convert_to(token, 'UTF8')), 'hex')"
    )
    op.alter_column("access_token", "token_hash", nullable=False)
    op.drop_constraint("pk_access_token", "access_token", type_="primary")
    op.drop_column("access_token", "token")
    op.create_primary_key("pk_access_token", "access_token", ["token_hash"])


def downgrade() -> None:
    # A digest gives no token back, and the column cannot be left empty.
    op.execute("DELETE FROM access_token")
    op.drop_constraint("pk_access_token", "access_token", type_="primary")
    op.drop_column("access_token", "token_hash")
    op.add_column(
        "access_token", sa.Column("token", sa.String(length=43), nullable=False)
    )
    op.create_primary_key("pk_access_token", "access_token", ["token"])
