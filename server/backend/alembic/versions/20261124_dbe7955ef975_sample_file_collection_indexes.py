"""Index the calibration trend's look back before its window

The calibration trend (``GET /api/calibration/trend`` and ``/trend/export``)
asks of the files an instrument acquired before its window three things:
whether one was fitted with each collection version the window holds, which
version the latest one fitted with each of their collections was, and whether
any names a collection at all (``calibration_trend._before_the_window``). The
version is read from inside the ``mz_calibration`` record, so each look was a
walk back on ``ix_sample_file_instrument_datetime_utc`` that parsed every
record on its way, and a version or a collection never used before, the very
one a change is marked for, walked the instrument's whole history.

Two partial indexes, over the records that name a collection:

- ``ix_sample_file_instrument_polarity_collection_version`` on the
  instrument, the polarity, the record's collection id and isotope hash, and
  ``datetime_utc``: whether a version was fitted with before.
- ``ix_sample_file_instrument_polarity_collection`` on the instrument, the
  polarity, the collection id and ``datetime_utc``: the latest earlier file
  of a collection, and whether any earlier file names one.

Every look is of one polarity, and both indexes hold it: without it, on a
TOF that runs both, a look for one polarity read every entry of the other's
that matched the rest, to check the polarity on its row.

The expressions are spelled here as ``SampleFile.__table_args__`` and the
trend spell them (``models.record_collection_id`` and
``record_collection_sha``), with the record's path written into the SQL:
a query is read off an expression index only where it holds the same
expression, and a path sent as a parameter is not that to a prepared
statement's generic plan. The look for any collection is ordered as the
second index is, by the collection id before the time, so that no walk back
on a time index serves it without sorting every row it keeps.

Indexes only; no row changes. ``CREATE INDEX`` holds off writes to
``sample_file`` while it builds, and each of the two parses every record
once, Orbitrap records included, so the time follows the records' size more
than their number. Measured on Postgres 16: 0.3 s for both on a history of
170,000 files whose records are small (mostly Orbitrap, about 130 bytes
each); 2.8 s for 200,000 TOF records of 12 lines (1.3 kB); 26 s for 100,000
TOF records of 40 lines, stored compressed out of line (3.3 kB).

Revision ID: dbe7955ef975
Revises: 9c4e7a2b5d18
Create Date: 2026-11-24 03:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "dbe7955ef975"
down_revision: Union[str, Sequence[str], None] = "9c4e7a2b5d18"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# As the models spell them, so the drift test sees one schema.
_ID = "(mz_calibration #>> '{quality,collection,id}')"
_SHA = "(mz_calibration #>> '{quality,collection,isotopes_sha256}')"
_NAMED = f"{_ID} IS NOT NULL"

# Named as the models name them (``models.NAMING_CONVENTION``).
_INDEXES = {
    "ix_sample_file_instrument_polarity_collection_version": [
        "instrument",
        "polarity",
        sa.text(_ID),
        sa.text(_SHA),
        "datetime_utc",
    ],
    "ix_sample_file_instrument_polarity_collection": [
        "instrument",
        "polarity",
        sa.text(_ID),
        "datetime_utc",
    ],
}


def upgrade() -> None:
    for name, columns in _INDEXES.items():
        op.create_index(name, "sample_file", columns, postgresql_where=sa.text(_NAMED))


def downgrade() -> None:
    for name in reversed(_INDEXES):
        op.drop_index(name, table_name="sample_file")
