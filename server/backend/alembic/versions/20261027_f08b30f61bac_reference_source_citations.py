"""Record the publications a reference source comes from on its source row

A reference list file names the publications it comes from in its header's
``references``, each a citation with its DOI or ISBN. Ingest kept none of it,
so the sources route could cite only a list Mascope ships, read from the
installed file: a list an operator loaded from a file of their own was cited by
nothing, and a shipped list loaded at an older version was cited from the
newer file.

``reference_source.citations`` holds them, a JSON array written at load: a
list file's references, the ones ``reference sync`` is given, and an empty
array for a load that cites none.

**NULL on every existing row.** NULL says the row predates the column, not
that the source cites nothing. ``reference seed`` records a shipped list's
citations on its active row without reloading it, and the sources route reads
a NULL row of a shipped list from the installed file while the version loaded
is the file's.

Revision ID: f08b30f61bac
Revises: b4e1d7a9c352
Create Date: 2026-10-27 18:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "f08b30f61bac"
down_revision: Union[str, Sequence[str], None] = "b4e1d7a9c352"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("reference_source", sa.Column("citations", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("reference_source", "citations")
