"""Show through the sample view which rung bound a sample's chemistry

``sample_item.bound_by`` and ``sample_item.method_binding_id`` say how
auto-processing decided an item's chemistry (revision ``6e4a9c2b7f31``), and
until now could only be read with SQL. The item routes read the table and
serialise the two from there. Everything else that lists samples - ``GET
/api/samples``, which the web app and the SDK both read, the row a created
item is answered with, and the socket events of a sample - reads
``sample_view``, which selects its columns by name and so did not have them.

The view gains the two columns. Nothing is stored: the view is dropped and
created again, which is the only way Postgres lets a column into the middle
of one, and nothing else in the schema is built on it.

Both view definitions are spelled out here rather than taken from
``mascope_backend.db.views``, so this revision keeps creating what it created
when it was written.

Revision ID: 67479416fc78
Revises: 8b3f5d2a6c47
Create Date: 2026-10-09 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "67479416fc78"
down_revision: Union[str, Sequence[str], None] = "8b3f5d2a6c47"
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
"""

_VIEW_TAIL = """
        sf.filename,
        sf.instrument,
        sf.instrument_type,
        sf.source_filename,
        sf.acquisition_id,
        sf.step_id,
        sf.sequence_run_id,
        sf.agent_id,
        sf.sha256,
        sf.method_file,
        sf.length,
        sf.range,
        sf.mz_calibration,
        sf.datetime,
        sf.datetime_utc
    FROM sample_item si
    INNER JOIN sample_file sf ON si.sample_file_id = sf.sample_file_id;
"""

VIEW_WITH_BINDING = (
    _VIEW_HEAD + "        si.bound_by,\n        si.method_binding_id,\n" + _VIEW_TAIL
)
VIEW_WITHOUT_BINDING = _VIEW_HEAD + _VIEW_TAIL


def upgrade() -> None:
    op.execute("DROP VIEW IF EXISTS sample_view")
    op.execute(VIEW_WITH_BINDING)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS sample_view")
    op.execute(VIEW_WITHOUT_BINDING)
