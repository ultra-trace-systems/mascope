"""Add sample_file.instrument_type and sample_file.source_filename

The instrument class of a file - Orbitrap or TOF - was parsed from its
instrument name, which therefore had to contain "orbi" or "tof". It is now
recorded by the reader that converts the file, so an upload can be filed
under any instrument name, in particular the one the File Agent reports.

``instrument_type`` is backfilled from the name with the same rule the code
used to apply, then made NOT NULL: every existing row passed that rule when
it was created, so none is left empty. ``source_filename`` is the file's name
on the uploading machine before the server filed it under an instrument;
nullable, nothing to backfill.

``sample_view`` is recreated to carry both, which needs a drop and create -
a view cannot be altered in place, and no earlier migration touched it.

Revision ID: c2d9f4a71b3e
Revises: 8e5f0b3a2d71
Create Date: 2026-09-05 15:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "c2d9f4a71b3e"
down_revision: Union[str, Sequence[str], None] = "8e5f0b3a2d71"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_VIEW_HEAD = """
    CREATE VIEW sample_view AS
    SELECT
        si.sample_item_id,
        sf.sample_file_id,
        sf.instrument_function_id,
        si.sample_batch_id,
        si.ionization_mode_id,
        si.sample_item_name,
        si.sample_item_type,
        si.locked,
        si.sample_item_attributes,
        si.filter_id,
        si.tic,
        si.polarity,
        si.t0,
        si.t1,
        si.sample_item_utc_created,
        si.sample_item_utc_modified,
        sf.filename,
        sf.instrument,
"""

_VIEW_TAIL = """
        sf.method_file,
        sf.length,
        sf.range,
        sf.mz_calibration,
        sf.datetime,
        sf.datetime_utc
    FROM sample_item si
    INNER JOIN sample_file sf ON si.sample_file_id = sf.sample_file_id;
"""

VIEW_WITH_TYPE = (
    _VIEW_HEAD
    + "        sf.instrument_type,\n        sf.source_filename,\n"
    + _VIEW_TAIL
)
VIEW_WITHOUT_TYPE = _VIEW_HEAD + _VIEW_TAIL

# The rule the code applied until now: "orbi" anywhere in the lower-cased
# name wins, then "tof" or "api".
_BACKFILL = """
    UPDATE sample_file
    SET instrument_type = CASE
        WHEN position('orbi' in lower(instrument)) > 0 THEN 'orbi'
        WHEN position('tof' in lower(instrument)) > 0
             OR position('api' in lower(instrument)) > 0 THEN 'tof'
    END
    WHERE instrument_type IS NULL
"""


def upgrade() -> None:
    op.add_column(
        "sample_file",
        sa.Column("instrument_type", sa.String(length=8), nullable=True),
    )
    op.add_column(
        "sample_file",
        sa.Column("source_filename", sa.String(length=256), nullable=True),
    )
    op.execute(_BACKFILL)
    op.alter_column("sample_file", "instrument_type", nullable=False)
    op.execute("DROP VIEW IF EXISTS sample_view")
    op.execute(VIEW_WITH_TYPE)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS sample_view")
    op.execute(VIEW_WITHOUT_TYPE)
    op.drop_column("sample_file", "source_filename")
    op.drop_column("sample_file", "instrument_type")
