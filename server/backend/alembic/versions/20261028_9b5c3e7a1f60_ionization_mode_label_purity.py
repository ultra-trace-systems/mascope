"""Carry the labelled reagent's isotopic purity on the ionization setup

The engine read the purity of a labelled reagent - the 15N of a 15N nitrate -
from one module constant, so a site whose bottle is 99% could not say so, and
a run could not record what it read (``docs/dev/ingest_routing_and_splitting.md``,
phase 8, model 4.3: reagents with purity as configuration). The setup carries
it now, and a run reads its sample's.

Every existing mode takes the constant's value, 0.98, so no result moves.
Changing it on a mode makes the mode's next version, like any field a run
computes from.

Revision ID: 9b5c3e7a1f60
Revises: e2a6f0b4d851
Create Date: 2026-10-27 22:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "9b5c3e7a1f60"
down_revision: Union[str, Sequence[str], None] = "e2a6f0b4d851"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ionization_mode",
        sa.Column(
            "label_purity",
            sa.Float(),
            server_default=sa.text("0.98"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("ionization_mode", "label_purity")
