"""Scope an ionization mode to one instrument

An ionization mode may now belong to a single instrument, or to every
instrument as they all did before (issue #1463). The scope is a filter on the
automatic rungs: it decides which modes a file name's tokens are matched
against, so a site can spell "nitrate" the same way in the file names of two
instruments and mean a different chemistry by it, and a mode only ever run on
one instrument stops competing for every other instrument's file names.

Measured on the production fleet before building this: on the largest server,
13 of 16 instruments have run more than one chemistry and one has run 55, while
78 of its 116 modes carry a token. Tokens that cannot be scoped are the reason
the fleet regression corpus has to prefix its own before routing.

**The token's uniqueness changes shape.** It was unique outright, which made
reuse across instruments impossible - the point of the feature. What has to
hold instead is that a token is unique among the modes that could match the
same file:

- at most one unscoped mode per token, and
- at most one mode per (token, instrument), comparing the instrument folded -
  ``ORBI-1`` and ``orbi-1`` are one instrument, as they are everywhere else
  (``method_keys.instrument_key``).

Two partial indexes rather than one composite, because Postgres counts NULLs as
distinct and a composite would let two unscoped modes share a token - the
ambiguity the rule exists to prevent. Both may hold for one token at once: an
unscoped mode and a scoped one, where the scoped one wins for its instrument.

Every existing row gets NULL, so every mode goes on applying to every
instrument and no routing changes until someone scopes a mode.

**The downgrade can fail, by design.** Restoring the single unique constraint
is impossible once two modes share a token, which is exactly what this revision
allows. It reports which tokens are duplicated rather than dropping a mode to
make room.

Revision ID: 5c1d8b3a7e29
Revises: 7c2e9a4b5d18
Create Date: 2026-09-28 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "5c1d8b3a7e29"
down_revision: Union[str, Sequence[str], None] = "7c2e9a4b5d18"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TOKEN_UNIQUE = "uq_ionization_mode_ionization_mode_token"


def upgrade() -> None:
    op.add_column(
        "ionization_mode",
        sa.Column("instrument", sa.String(64), nullable=True),
    )
    op.drop_constraint(_TOKEN_UNIQUE, "ionization_mode", type_="unique")
    op.create_index(
        "uq_ionization_mode_token_global",
        "ionization_mode",
        ["ionization_mode_token"],
        unique=True,
        postgresql_where=sa.text("instrument IS NULL"),
    )
    op.create_index(
        "uq_ionization_mode_token_per_instrument",
        "ionization_mode",
        ["ionization_mode_token", sa.text("lower(instrument)")],
        unique=True,
        postgresql_where=sa.text("instrument IS NOT NULL"),
    )


def downgrade() -> None:
    duplicated = (
        op.get_bind()
        .execute(
            sa.text(
                """
                SELECT ionization_mode_token
                FROM ionization_mode
                WHERE ionization_mode_token IS NOT NULL
                GROUP BY ionization_mode_token
                HAVING count(*) > 1
                """
            )
        )
        .scalars()
        .all()
    )
    if duplicated:
        raise RuntimeError(
            "Cannot restore the single unique constraint on "
            "ionization_mode.ionization_mode_token: these tokens are used by "
            f"more than one mode - {', '.join(sorted(duplicated))}. Point the "
            "instruments that share a token at one mode, or rename the tokens, "
            "and run this again."
        )
    op.drop_index(
        "uq_ionization_mode_token_per_instrument", table_name="ionization_mode"
    )
    op.drop_index("uq_ionization_mode_token_global", table_name="ionization_mode")
    op.create_unique_constraint(
        _TOKEN_UNIQUE, "ionization_mode", ["ionization_mode_token"]
    )
    op.drop_column("ionization_mode", "instrument")
