"""Slim the assignment ledger's JSON: pack formula-only alternatives, move the calibration record to the run

The two JSON columns of ``peak_assignment`` are 42 % of its heap, and most of
that is not information but repetition. Measured on a 213k-row ledger:

- ``alternatives`` held 192k entries of which 99 % were the untargeted
  finder's formula-only shortlist, ``{"assigned_formula": "C10H14O8",
  "plausibility": 1.0, "source": "untargeted"}`` - about 70 bytes each for
  16 bytes of payload. Stored as ``["C10H14O8", 1.0]`` they are a fifth of
  that. The ORM type on the column (``CompactAlternatives``) packs and expands
  them on every write and read, so readers keep seeing the dict; this revision
  packs the rows that were written before it. A stored entry is a list exactly
  when it was formula-only, so the two forms cannot be confused.
- every database-sourced row repeated ``provenance.calibration``
  (instrument, provisional flag, source - 90 bytes) and ``calibrated`` beside
  its ``p_correct``, though one curve serves a whole run. The pair now lives
  once on the run as ``confidence_calibration`` and the detail read folds it
  back into each row, so the row shape the inspector and the SDK see is
  unchanged. This revision records each run's curve from its rows and strips
  the copies.

Together about 13 % off the ledger on the measured data. Both rewrites are
plain SQL over the JSON, batched by nothing: on every deployment that predates
ingest-time assignment the ledger is empty, and the largest ledger this was
tried on rewrote in seconds.

Revision ID: 21a7c103f3b0
Revises: d4b8f2c60a13
Create Date: 2026-09-03 14:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "21a7c103f3b0"
down_revision: Union[str, Sequence[str], None] = "d4b8f2c60a13"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# An entry is formula-only when it carries exactly the three keys the finder's
# shortlist writes and names the untargeted source. Anything else - a scored
# contender, a displaced winner, an entry an external engine published - keeps
# its dict, which is what makes the packed list unambiguous on the way back.
_FORMULA_ONLY = (
    "jsonb_typeof(e) = 'object'"
    " AND e ? 'assigned_formula' AND e ? 'plausibility' AND e ? 'source'"
    " AND (e - 'assigned_formula' - 'plausibility' - 'source') = '{}'::jsonb"
    " AND e->>'source' = 'untargeted'"
    " AND jsonb_typeof(e->'assigned_formula') = 'string'"
)

_PACK_ALTERNATIVES = f"""
    UPDATE peak_assignment
    SET alternatives = (
        SELECT jsonb_agg(
            CASE WHEN {_FORMULA_ONLY}
                 THEN jsonb_build_array(e->'assigned_formula', e->'plausibility')
                 ELSE e END
            ORDER BY ord
        )
        FROM jsonb_array_elements(peak_assignment.alternatives::jsonb)
             WITH ORDINALITY AS t(e, ord)
    )::json
    WHERE alternatives IS NOT NULL
      AND jsonb_typeof(alternatives::jsonb) = 'array'
      AND EXISTS (
          SELECT 1 FROM jsonb_array_elements(peak_assignment.alternatives::jsonb) AS x(e)
          WHERE {_FORMULA_ONLY}
      )
"""

_UNPACK_ALTERNATIVES = """
    UPDATE peak_assignment
    SET alternatives = (
        SELECT jsonb_agg(
            CASE WHEN jsonb_typeof(e) = 'array'
                 THEN jsonb_build_object(
                     'assigned_formula', e->0,
                     'plausibility', e->1,
                     'source', 'untargeted')
                 ELSE e END
            ORDER BY ord
        )
        FROM jsonb_array_elements(peak_assignment.alternatives::jsonb)
             WITH ORDINALITY AS t(e, ord)
    )::json
    WHERE alternatives IS NOT NULL
      AND jsonb_typeof(alternatives::jsonb) = 'array'
      AND EXISTS (
          SELECT 1 FROM jsonb_array_elements(peak_assignment.alternatives::jsonb) AS x(e)
          WHERE jsonb_typeof(x.e) = 'array'
      )
"""

# One curve per run, so any row of the run that carries the block says which.
# Rows of an uncalibrated run carry `"calibration": null`, which is not an
# object, so such a run keeps NULL - the same thing `calibration_meta(None)`
# records for a run computed after this revision.
_RECORD_RUN_CALIBRATION = """
    UPDATE peak_assignment_run AS run
    SET confidence_calibration = rows.calibration::json
    FROM (
        SELECT DISTINCT ON (peak_assignment_run_id)
               peak_assignment_run_id,
               provenance::jsonb -> 'calibration' AS calibration
        FROM peak_assignment
        WHERE provenance IS NOT NULL
          AND jsonb_typeof(provenance::jsonb) = 'object'
          AND jsonb_typeof(provenance::jsonb -> 'calibration') = 'object'
        ORDER BY peak_assignment_run_id
    ) AS rows
    WHERE run.peak_assignment_run_id = rows.peak_assignment_run_id
"""

_STRIP_ROW_CALIBRATION = """
    UPDATE peak_assignment
    SET provenance = (provenance::jsonb - 'calibration' - 'calibrated')::json
    WHERE provenance IS NOT NULL
      AND jsonb_typeof(provenance::jsonb) = 'object'
      AND (provenance::jsonb ? 'calibration' OR provenance::jsonb ? 'calibrated')
"""

# The pair goes back beside `p_correct` on the rows that had it - the database
# stage's, which are the only rows that carry the key - read off the run again.
_RESTORE_ROW_CALIBRATION = """
    UPDATE peak_assignment AS row
    SET provenance = (
        row.provenance::jsonb || jsonb_build_object(
            'calibrated',
            jsonb_typeof(coalesce(run.confidence_calibration::jsonb, 'null'::jsonb))
                = 'object',
            'calibration',
            coalesce(run.confidence_calibration::jsonb, 'null'::jsonb)
        )
    )::json
    FROM peak_assignment_run AS run
    WHERE run.peak_assignment_run_id = row.peak_assignment_run_id
      AND row.provenance IS NOT NULL
      AND jsonb_typeof(row.provenance::jsonb) = 'object'
      AND row.provenance::jsonb ? 'p_correct'
      AND NOT row.provenance::jsonb ? 'calibration'
"""


def upgrade() -> None:
    op.execute(_PACK_ALTERNATIVES)
    op.add_column(
        "peak_assignment_run",
        sa.Column("confidence_calibration", sa.JSON(), nullable=True),
    )
    op.execute(_RECORD_RUN_CALIBRATION)
    op.execute(_STRIP_ROW_CALIBRATION)


def downgrade() -> None:
    op.execute(_RESTORE_ROW_CALIBRATION)
    op.drop_column("peak_assignment_run", "confidence_calibration")
    op.execute(_UNPACK_ALTERNATIVES)
