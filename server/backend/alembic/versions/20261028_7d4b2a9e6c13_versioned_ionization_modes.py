"""Make an ionization mode a versioned setup

A mode was edited in place, so nothing could say which adduct panel a run used
or which collections a calibration was fitted under
(``docs/dev/ingest_routing_and_splitting.md``, phase 8, model 4.3). From here
each row is one version: an edit of the polarity, the mechanisms or the
collections inserts the next version and marks the row it replaces inactive,
and a sample item keeps the version it was bound under.

**Every existing mode is its own version 1** (MAS-351, mint and repoint):
``version`` 1, ``is_active`` true, ``supersedes_id`` NULL. Every reference to
a mode - a sample item, a method binding, a batch ledger anchor - already
points at the row that is now that version 1, so none has to move.

**Uniqueness is among the active versions.** A superseded version keeps the
token and the system key it had, and its successor usually has the same ones,
so the two token indexes and the system-key constraint become partial on
``is_active``. The system key's constraint becomes a partial unique index
under the same name.

Downgrade refuses while any version is inactive: the rows would all read as
modes again, sharing tokens and keys the restored constraints refuse.

Revision ID: 7d4b2a9e6c13
Revises: 3c8e1f5a9d27
Create Date: 2026-10-27 21:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "7d4b2a9e6c13"
down_revision: Union[str, Sequence[str], None] = "3c8e1f5a9d27"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_SYSTEM_KEY = "uq_ionization_mode_system_key"
_TOKEN_GLOBAL = "uq_ionization_mode_token_global"
_TOKEN_PER_INSTRUMENT = "uq_ionization_mode_token_per_instrument"


def _token_indexes(active_only: bool) -> None:
    also = " AND is_active" if active_only else ""
    op.create_index(
        _TOKEN_GLOBAL,
        "ionization_mode",
        ["ionization_mode_token"],
        unique=True,
        postgresql_where=sa.text(f"instrument IS NULL{also}"),
    )
    op.create_index(
        _TOKEN_PER_INSTRUMENT,
        "ionization_mode",
        ["ionization_mode_token", sa.text("lower(instrument)")],
        unique=True,
        postgresql_where=sa.text(f"instrument IS NOT NULL{also}"),
    )


def upgrade() -> None:
    op.add_column(
        "ionization_mode",
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
    )
    op.add_column(
        "ionization_mode",
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
    )
    op.add_column(
        "ionization_mode",
        sa.Column("supersedes_id", sa.String(length=16), nullable=True),
    )
    op.create_foreign_key(
        "fk_ionization_mode_supersedes_id_ionization_mode",
        "ionization_mode",
        "ionization_mode",
        ["supersedes_id"],
        ["ionization_mode_id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_ionization_mode_supersedes_id", "ionization_mode", ["supersedes_id"]
    )

    op.drop_index(_TOKEN_PER_INSTRUMENT, table_name="ionization_mode")
    op.drop_index(_TOKEN_GLOBAL, table_name="ionization_mode")
    _token_indexes(active_only=True)

    op.drop_constraint(_SYSTEM_KEY, "ionization_mode", type_="unique")
    op.create_index(
        _SYSTEM_KEY,
        "ionization_mode",
        ["system_key"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )


def downgrade() -> None:
    superseded = (
        op.get_bind()
        .execute(sa.text("SELECT count(*) FROM ionization_mode WHERE NOT is_active"))
        .scalar_one()
    )
    if superseded:
        raise RuntimeError(
            f"Cannot drop the ionization mode versions: {superseded} superseded "
            "versions are kept for the samples bound under them, and without "
            "the version columns they would read as modes again, sharing the "
            "tokens and system keys of the versions that replaced them. Move "
            "their samples onto the current versions and delete them first."
        )
    op.drop_index(_SYSTEM_KEY, table_name="ionization_mode")
    op.create_unique_constraint(_SYSTEM_KEY, "ionization_mode", ["system_key"])

    op.drop_index(_TOKEN_PER_INSTRUMENT, table_name="ionization_mode")
    op.drop_index(_TOKEN_GLOBAL, table_name="ionization_mode")
    _token_indexes(active_only=False)

    op.drop_index("ix_ionization_mode_supersedes_id", table_name="ionization_mode")
    op.drop_constraint(
        "fk_ionization_mode_supersedes_id_ionization_mode",
        "ionization_mode",
        type_="foreignkey",
    )
    op.drop_column("ionization_mode", "supersedes_id")
    op.drop_column("ionization_mode", "is_active")
    op.drop_column("ionization_mode", "version")
