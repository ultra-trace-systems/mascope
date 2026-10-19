"""Index the sample file list's look-backs and its order

The Raw files list (``GET /api/sample/files`` and ``/api/sample/files/recent``,
both ``get_sample_files``) keeps the files acquired since a time - of every
instrument, or of the one in focus - or, recent by processing, the files whose
processing status was written since a time, and pages through them newest or
oldest first. Each page load runs two statements: a count of every row it
keeps, for the paginator, and the page. ``sample_file`` had no index on any of
those columns, so both were a sequential scan of the whole table.

Three indexes:

- ``ix_sample_file_datetime_utc`` on ``(datetime_utc, sample_file_id)``: the
  look-back and the list's order. The controller orders by the time and then
  the id, both in the sort's direction, with no ``NULLS LAST`` on this NOT
  NULL column, so newest first is the index read backwards and oldest first
  the index read forwards. The page is then the first rows of an index scan,
  and the count an index-only scan.
- ``ix_sample_file_instrument_datetime_utc`` on ``(instrument, datetime_utc,
  sample_file_id)``: the same, for one instrument.
- ``ix_sample_file_processing_updated_utc``: the look-back of recent by
  processing, which sorts what it keeps by acquisition time.

Indexes only; no row changes. ``CREATE INDEX`` holds off writes to
``sample_file`` while it builds: under two seconds for half a million files.

Revision ID: 1658f7bece0a
Revises: 206bf313dee6
Create Date: 2026-10-16 15:30:00.000000

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "1658f7bece0a"
down_revision: Union[str, Sequence[str], None] = "206bf313dee6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Named as the models name them (``models.NAMING_CONVENTION``), so the drift
# test sees one schema.
_INDEXES = {
    "ix_sample_file_datetime_utc": ["datetime_utc", "sample_file_id"],
    "ix_sample_file_instrument_datetime_utc": [
        "instrument",
        "datetime_utc",
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
