"""Index the sample file list's look-backs and its order

The Raw files list (``GET /api/sample/files`` and ``/api/sample/files/recent``,
both ``get_sample_files``) keeps the files acquired since a time - of every
instrument, or of the one in focus - or, recent by processing, the files whose
processing status was written since a time, and pages through them newest
first. Each page load runs the statement twice: a count of every row it keeps,
for the paginator, and the page. ``sample_file`` had no index on any of those
columns, so both were a sequential scan of the whole table, and the count
sorted every row the look-back kept, because the statement it counts carries
the list's ORDER BY.

Three indexes:

- ``ix_sample_file_datetime_utc`` on ``(datetime_utc DESC NULLS LAST,
  sample_file_id)``: the list's default order exactly as the controller writes
  it, the id being its tiebreak. The page is then the first rows of an index
  scan, and the count an index-only scan with nothing to sort. A plain
  ``datetime_utc`` index read backwards is ``DESC NULLS FIRST``, which the
  planner does not take for ``NULLS LAST`` although the column is NOT NULL:
  it bounds the scan with it and sorts all the same.
- ``ix_sample_file_instrument_datetime_utc`` on ``(instrument, datetime_utc
  DESC NULLS LAST, sample_file_id)``: the same, for one instrument.
- ``ix_sample_file_processing_updated_utc``: the look-back of recent by
  processing, which sorts what it keeps by acquisition time.

The order is the default's, newest first. Sorted oldest first
(``ASC NULLS LAST``), the list is bounded by the same index and sorted, which
a plain index would have spared it at the cost of the default.

Indexes only; no row changes. ``CREATE INDEX`` holds off writes to
``sample_file`` while it builds: under two seconds for half a million files.

Revision ID: 1658f7bece0a
Revises: 206bf313dee6
Create Date: 2026-10-16 15:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "1658f7bece0a"
down_revision: Union[str, Sequence[str], None] = "206bf313dee6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Named as the models name them (``models.NAMING_CONVENTION``), so the drift
# test sees one schema.
_INDEXES = {
    "ix_sample_file_datetime_utc": [
        sa.text("datetime_utc DESC NULLS LAST"),
        "sample_file_id",
    ],
    "ix_sample_file_instrument_datetime_utc": [
        "instrument",
        sa.text("datetime_utc DESC NULLS LAST"),
        "sample_file_id",
    ],
    "ix_sample_file_processing_updated_utc": ["processing_updated_utc"],
}


def upgrade() -> None:
    for name, columns in _INDEXES.items():
        op.create_index(name, "sample_file", columns)


def downgrade() -> None:
    for name in reversed(_INDEXES):
        op.drop_index(name, table_name="sample_file")
