"""Add the m/z bounds, the origin key and the batch recipe, and show them through the sample view

The finder and the refreshable batches (``docs/dev/sample_filters_and_metadata.md``,
sections 4.3 and 6) read four columns that did not exist:

- ``sample_file.mz_min`` and ``mz_max``, the two ends of ``range`` as numbers,
  each indexed. ``range`` is a JSON list, which SQL compares only by casting
  it row by row. **Every row is filled here**, by the rule the model fills
  them by from now on (``models.range_bounds``): the first and last element
  of a list of at least two numbers, so a row that still holds a whole mass
  axis gives the ends of that axis; NULL for anything else.
- ``sample_batch.source_filter``, the recipe of a batch built from a filter;
  NULL on every batch there is, all of them built by hand.
- ``sample_item.origin_key``, which part of the acquisition record a copy was
  made from, indexed with the batch for the rows that carry one. NULL on
  every item there is: a key for the copies made before is offered by
  ``backfill_origin_keys``, not written here.

``sample_view`` gains the origin key and the m/z the sample reads: its
stream's scan ranges where it reads a stream (a composite's are its
segments'), else its file's bounds. The view is dropped and created again,
which is the only way Postgres lets a column into the middle of one.

Both view definitions are spelled out here rather than taken from
``mascope_backend.db.views``, so this revision keeps creating what it created
when it was written.

Revision ID: 13278d0d892f
Revises: 9c68f0e22ed6
Create Date: 2026-11-05 13:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "13278d0d892f"
down_revision: Union[str, Sequence[str], None] = "9c68f0e22ed6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# One end of a row's range, or NULL. The checks nest: Postgres does not
# promise to evaluate the parts of an AND in order, and the length of a value
# that is not a list is an error, as is a cast of what is not a number.
_END = """
    CASE WHEN json_typeof(range) = 'array' THEN
        CASE WHEN json_array_length(range) >= 2
              AND json_typeof(range -> 0) = 'number'
              AND json_typeof(range -> (json_array_length(range) - 1)) = 'number'
        THEN (range ->> {index})::float8
        END
    END
"""

_BACKFILL = f"""
    UPDATE sample_file
    SET mz_min = {_END.format(index="0")},
        mz_max = {_END.format(index="(json_array_length(range) - 1)")}
    WHERE json_typeof(range) = 'array'
"""

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
        si.bound_by,
        si.method_binding_id,
"""

_VIEW_FILE_HEAD = """
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
"""

_VIEW_FILE_TAIL = """
        sf.mz_calibration,
        sf.datetime,
        sf.datetime_utc
    FROM sample_item si
    INNER JOIN sample_file sf ON si.sample_file_id = sf.sample_file_id
"""

VIEW_WITH_ORIGIN = (
    _VIEW_HEAD
    + "        si.origin_key,\n"
    + _VIEW_FILE_HEAD
    + "        COALESCE(stream_mz.mz_min, sf.mz_min) AS mz_min,\n"
    + "        COALESCE(stream_mz.mz_max, sf.mz_max) AS mz_max,\n"
    + _VIEW_FILE_TAIL
    + """
    LEFT JOIN LATERAL (
        SELECT
            min((scan_range ->> 0)::float8) AS mz_min,
            max((scan_range ->> 1)::float8) AS mz_max
        FROM acquisition_stream st
        CROSS JOIN LATERAL json_array_elements(
            CASE
                WHEN json_typeof(st.signature -> 'scan_ranges') = 'array'
                THEN st.signature -> 'scan_ranges'
            END
        ) AS scan_range
        WHERE st.sample_file_id = si.sample_file_id
          AND si.stream_id IN (st.stream_id, st.composite_stream_id)
          AND json_typeof(scan_range -> 0) = 'number'
          AND json_typeof(scan_range -> 1) = 'number'
    ) stream_mz ON true;
"""
)

# What revision 67479416fc78 created.
VIEW_WITHOUT_ORIGIN = _VIEW_HEAD + _VIEW_FILE_HEAD + _VIEW_FILE_TAIL + ";\n"


def upgrade() -> None:
    op.add_column("sample_file", sa.Column("mz_min", sa.Float(), nullable=True))
    op.add_column("sample_file", sa.Column("mz_max", sa.Float(), nullable=True))
    op.execute(_BACKFILL)
    op.create_index("ix_sample_file_mz_min", "sample_file", ["mz_min"])
    op.create_index("ix_sample_file_mz_max", "sample_file", ["mz_max"])

    op.add_column(
        "sample_batch",
        sa.Column(
            "source_filter",
            postgresql.JSONB(none_as_null=True, astext_type=sa.Text()),
            nullable=True,
        ),
    )

    op.add_column(
        "sample_item", sa.Column("origin_key", sa.String(length=64), nullable=True)
    )
    op.create_index(
        "ix_sample_item_sample_batch_id_origin_key",
        "sample_item",
        ["sample_batch_id", "origin_key"],
        postgresql_where=sa.text("origin_key IS NOT NULL"),
    )

    op.execute("DROP VIEW IF EXISTS sample_view")
    op.execute(VIEW_WITH_ORIGIN)


def downgrade() -> None:
    # The view first: it reads the columns about to go.
    op.execute("DROP VIEW IF EXISTS sample_view")
    op.execute(VIEW_WITHOUT_ORIGIN)

    # The keys copies were made with go with the column. A copy made since
    # cannot be told from one made before, so an upgrade does not bring them
    # back: the backfill gives a key to the copies whose window was not
    # edited.
    op.drop_index(
        "ix_sample_item_sample_batch_id_origin_key",
        table_name="sample_item",
        postgresql_where=sa.text("origin_key IS NOT NULL"),
    )
    op.drop_column("sample_item", "origin_key")
    op.drop_column("sample_batch", "source_filter")
    # Derived from range, which stays: an upgrade fills them again.
    op.drop_index("ix_sample_file_mz_max", table_name="sample_file")
    op.drop_index("ix_sample_file_mz_min", table_name="sample_file")
    op.drop_column("sample_file", "mz_max")
    op.drop_column("sample_file", "mz_min")
