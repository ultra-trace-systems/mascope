"""Mark the ionization modes Mascope ships

``system_key`` names the chemistry a mode stands for, the same string on every
server, so routing by acquisition method has an identity to route to. NULL on
a mode the deployment made, which is every mode that exists before this runs.

The rows themselves are not inserted here. Which of them a server can hold
depends on the ionization mechanisms it already has, and a mechanism cannot
simply be inserted: creating one through the API also builds the target ions
of every compound in the library, and every compound created afterwards gains
ions for it. A migration that inserted mechanism rows would both skip that
work, leaving an adopted mode unable to match anything in an existing library,
and enlarge every later compound import at sites that never use the chemistry.
:mod:`mascope_backend.db.admin.ionization.ensure_system_modes` seeds instead,
at startup, from the mechanisms the deployment already has.

Revision ID: 9a3c5e71b8d4
Revises: a7d3e9c1f5b2
Create Date: 2026-09-24 14:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "9a3c5e71b8d4"
down_revision: Union[str, Sequence[str], None] = "a7d3e9c1f5b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The name the model's ``unique=True`` renders under the metadata naming
# convention: a unique index instead would read as drift from the models.
SYSTEM_KEY_CONSTRAINT = "uq_ionization_mode_system_key"


def upgrade() -> None:
    op.add_column(
        "ionization_mode",
        sa.Column("system_key", sa.String(length=64), nullable=True),
    )
    op.create_unique_constraint(
        SYSTEM_KEY_CONSTRAINT,
        "ionization_mode",
        ["system_key"],
    )


def downgrade() -> None:
    # The rows stay, as ordinary modes. Deleting them would take the chemistry
    # off any sample or batch-peak anchor that had been bound to one, which is
    # a loss no downgrade should cause; seeding can claim them again by id.
    op.drop_constraint(SYSTEM_KEY_CONSTRAINT, "ionization_mode", type_="unique")
    op.drop_column("ionization_mode", "system_key")
