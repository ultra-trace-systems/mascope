"""A batch's assignment settings: sample_batch.assignment_settings

The untargeted search's settings were each user's, kept in the browser and
sent with each request, so two users re-running one batch could run it under
different settings (``docs/dev/batch_assignment_one_action.md``, 3.4 and 5.4;
decided in MAS-1012). They become the batch's: a nullable JSON on
``sample_batch``, validated as a peak assignment config without its decoy
block (``config.BatchAssignmentSettings``), which every pass of the batch
stages runs on. Null, as every batch is after this revision, means the
engine's defaults, so no backfill.

Revision ID: 4d7b2e9a6c15
Revises: 8c2f4a6d1e93
Create Date: 2026-11-22 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "4d7b2e9a6c15"
down_revision: Union[str, Sequence[str], None] = "8c2f4a6d1e93"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "sample_batch",
        sa.Column(
            "assignment_settings",
            postgresql.JSONB(none_as_null=True, astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    # The passes recorded the settings they ran on in their own runs' config,
    # which stays; only the batch's saved settings go.
    op.drop_column("sample_batch", "assignment_settings")
