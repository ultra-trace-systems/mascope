"""Keep an upload's acquisition record and hash on its sample file

A raw file says what the instrument measured. The program that controls the
instrument knows the rest - which step of which sequence run asked for the
file, in which mode, under which chemistry - and can send it with the file's
upload as a small JSON document, the acquisition record
(``docs/dev/acquisition_sidecar.md``). Until now the server had nowhere to
put one.

``sample_file.acquisition`` is the record as it was sent. Its four
identifiers are columns of their own, so that "every file of this run" is one
indexed query instead of a scan through JSON, and so that they can be
exported beside Mascope's own ids:

- ``acquisition_id``: this file's acquisition. Unique, because an acquisition
  is one file - a record naming one another file already is is not stored.
- ``step_id``, ``sequence_run_id``, ``agent_id``: the step, the run of the
  sequence, and the installation that wrote the record. Indexed.

They are Postgres ``uuid``: the control program mints UUIDs so that they are
unique without a registry, and a column of that type refuses anything else.

``sample_file.sha256`` is the file's SHA-256 as it was uploaded, recorded
only where the uploader reported a hash and the bytes received had it.

**Every column is NULL on every existing row, and stays NULL.** None of this
can be worked out afterwards: the record was never sent for those files, and
a hash computed now would say what the server holds, not what was uploaded.

The sample view gains the identifiers and the hash, which is where the
spreadsheet export reads a sample's file from. It does not gain the record
itself, which is up to 16 KB a row.

Revision ID: 8b3f5d2a6c47
Revises: 5a0e9de94ed9
Create Date: 2026-10-07 18:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "8b3f5d2a6c47"
down_revision: Union[str, Sequence[str], None] = "5a0e9de94ed9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_IDS = ("acquisition_id", "step_id", "sequence_run_id", "agent_id")

# The names the models' naming convention produces (``models.NAMING_CONVENTION``),
# so the drift test sees one schema.
_UNIQUE = "uq_sample_file_acquisition_id"
_INDEXED = ("step_id", "sequence_run_id", "agent_id")


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
        sf.instrument_type,
        sf.source_filename,
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

VIEW_WITH_PROVENANCE = (
    _VIEW_HEAD
    + "".join(f"        sf.{name},\n" for name in (*_IDS, "sha256"))
    + _VIEW_TAIL
)
VIEW_WITHOUT_PROVENANCE = _VIEW_HEAD + _VIEW_TAIL


def upgrade() -> None:
    op.add_column("sample_file", sa.Column("acquisition", sa.JSON(), nullable=True))
    for name in _IDS:
        op.add_column(
            "sample_file", sa.Column(name, sa.Uuid(as_uuid=False), nullable=True)
        )
    op.add_column(
        "sample_file", sa.Column("sha256", sa.String(length=64), nullable=True)
    )
    op.create_unique_constraint(_UNIQUE, "sample_file", ["acquisition_id"])
    for name in _INDEXED:
        op.create_index(f"ix_sample_file_{name}", "sample_file", [name])
    op.execute("DROP VIEW IF EXISTS sample_view")
    op.execute(VIEW_WITH_PROVENANCE)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS sample_view")
    op.execute(VIEW_WITHOUT_PROVENANCE)
    for name in _INDEXED:
        op.drop_index(f"ix_sample_file_{name}", table_name="sample_file")
    op.drop_constraint(_UNIQUE, "sample_file", type_="unique")
    op.drop_column("sample_file", "sha256")
    for name in reversed(_IDS):
        op.drop_column("sample_file", name)
    op.drop_column("sample_file", "acquisition")
