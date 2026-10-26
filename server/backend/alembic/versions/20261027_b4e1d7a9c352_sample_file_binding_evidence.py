"""Keep what the chemistry detector read beside what bound the file

Rung 5 of the binding ladder reads a file's chemistry off its own spectrum
(``docs/dev/ingest_routing_and_splitting.md``, section 5.4). It ships as an
audit: it binds nothing, and what it calls is stored beside the rung that bound
the file, so that its precision can be measured before it routes.

``sample_file.binding_evidence`` holds it, one entry per MS1 polarity: the
mode that bound the polarity and the rung its sample records, the profile that
mode names, whether the detector's call agrees, and the call with every
candidate's evidence. On the file rather than on its stream rows, which the
design's table puts it on: a TOF file and a parked file have no stream rows,
and the binding is made per polarity until phase 4 carries it to the stream.

**NULL on every existing row, and filled in only by a run.** A file's evidence
is read when it is bound or parked; re-processing a file writes it.

Not in the sample view: it is a kilobyte or two a row, and nothing reads it
per sample.

Revision ID: b4e1d7a9c352
Revises: 762236a8e559
Create Date: 2026-10-27 13:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "b4e1d7a9c352"
down_revision: Union[str, Sequence[str], None] = "762236a8e559"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "sample_file", sa.Column("binding_evidence", sa.JSON(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("sample_file", "binding_evidence")
