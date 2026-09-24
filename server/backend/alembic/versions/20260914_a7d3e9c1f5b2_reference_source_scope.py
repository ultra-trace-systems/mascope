"""Record a reference source's window, radical allowance and polarity

Stage A of peak assignment matched every active reference source inside one
window - C/H/N/O/S, at most 40 carbons, at most 700 Da - whatever the source
was. A source row now records how its own compounds may be matched:

- ``known_window``: an object of ``elements``, ``max_carbon`` and ``max_mass``,
  where a null field is unbounded on that axis;
- ``allow_radicals``: whether the source's odd-electron formulas may be matched;
- ``polarity``: ``positive`` or ``negative``, NULL for both.

The window's column default is the one window every source was matched inside
until now, and ``allow_radicals`` defaults to false, so every existing row is
backfilled with that window, no radicals and both polarities: a database mirror
loaded before this revision stays bounded until it is loaded again, and an
insert that names no window is bounded the same way. An unbounded window is
written only by a load that says so: the shipped lists' rows by ``reference
seed``, which brings an already-active list's row up to date, and a list file, a
custom CSV or an explicit flag by ``reference sync``.

Revision ID: a7d3e9c1f5b2
Revises: 8d2f6a1c4e93
Create Date: 2026-09-14 16:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "a7d3e9c1f5b2"
down_revision: Union[str, Sequence[str], None] = "8d2f6a1c4e93"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Written out rather than imported: a migration records the window as it stood
# when the column was added, whatever the library's constant says later.
MIRROR_WINDOW_SQL = (
    """'{"elements": ["C", "H", "N", "O", "S"], "max_carbon": 40, "max_mass": 700.0}'"""
)


def upgrade() -> None:
    op.add_column(
        "reference_source",
        sa.Column(
            "known_window",
            sa.JSON(),
            server_default=sa.text(MIRROR_WINDOW_SQL),
            nullable=False,
        ),
    )
    op.add_column(
        "reference_source",
        sa.Column(
            "allow_radicals",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        "reference_source",
        sa.Column("polarity", sa.String(length=8), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("reference_source", "polarity")
    op.drop_column("reference_source", "allow_radicals")
    op.drop_column("reference_source", "known_window")
