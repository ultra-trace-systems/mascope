"""Record the ionization setup on assignment runs and calibration curves

A run's stored config said nothing about the adduct panel it searched, and a
confidence curve keyed by ``(instrument, score_version)`` could not say which
reagent chemistry its labels came from
(``docs/dev/ingest_routing_and_splitting.md``, phase 8; the retired ionization
note's 8.2 and 8.3). From here a run records the version of its sample's mode
under ``config.ionization_setup``, and a curve the versions its labels were
scored under in ``assignment_calibration.ionization_setups``.

**The backfill points what exists at version 1** (MAS-351, mint and repoint:
every reference explicit, no reader special-casing an unrecorded one):

- every in-app run with a config records its sample's mode as it stands now,
  which the previous revision made that mode's version 1 - or None for a
  sample bound under no mode. A mode edited in place before versions existed
  may have read otherwise when the run ran; nothing recorded that, and
  version 1 is what the mode reads as today. Imported runs are left as their
  editor supplied them;
- every curve records the setups of the verifications its fit could have
  drawn on: live confirmed or rejected verdicts with evidence, on samples of
  its instrument class, made by the time it was fit and not superseded by
  then, of in-app runs or of runs since pruned - the pool
  ``recalibrate_instrument`` reads. A curve none of whose labels can be
  traced keeps NULL, which the lookup reads as "not known".

Downgrade drops the column and the run key.

Revision ID: e2a6f0b4d851
Revises: 7d4b2a9e6c13
Create Date: 2026-10-27 23:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "e2a6f0b4d851"
down_revision: Union[str, Sequence[str], None] = "7d4b2a9e6c13"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: ``config.IN_APP_ENGINE``, written out: a migration does not read the code
#: it migrates for.
_IN_APP_ENGINE = "mascope"


def upgrade() -> None:
    op.add_column(
        "assignment_calibration",
        sa.Column("ionization_setups", sa.JSON(), nullable=True),
    )
    conn = op.get_bind()
    conn.execute(
        sa.text(
            """
            UPDATE peak_assignment_run AS r
            SET config = (
                r.config::jsonb || jsonb_build_object(
                    'ionization_setup',
                    CASE WHEN m.ionization_mode_id IS NULL THEN 'null'::jsonb
                         ELSE jsonb_build_object(
                             'ionization_mode_id', m.ionization_mode_id,
                             'version', m.version)
                    END
                )
            )::json
            FROM sample_item AS si
            LEFT JOIN ionization_mode AS m
                ON m.ionization_mode_id = si.ionization_mode_id
            WHERE si.sample_item_id = r.sample_item_id
              AND r.config IS NOT NULL
              AND json_typeof(r.config) = 'object'
              AND r.engine = :engine
            """
        ),
        {"engine": _IN_APP_ENGINE},
    )
    conn.execute(
        sa.text(
            """
            UPDATE assignment_calibration AS c
            SET ionization_setups = pool.setups
            FROM (
                SELECT c2.assignment_calibration_id,
                       jsonb_agg(DISTINCT jsonb_build_object(
                           'ionization_mode_id', m.ionization_mode_id,
                           'version', m.version))::json AS setups
                FROM assignment_calibration AS c2
                JOIN sample_file AS sf
                    ON sf.instrument_type = c2.instrument
                JOIN sample_item AS si
                    ON si.sample_file_id = sf.sample_file_id
                JOIN ionization_mode AS m
                    ON m.ionization_mode_id = si.ionization_mode_id
                JOIN assignment_verification AS av
                    ON av.sample_item_id = si.sample_item_id
                LEFT JOIN peak_assignment_run AS r
                    ON r.peak_assignment_run_id = av.peak_assignment_run_id
                WHERE av.verdict IN ('confirmed', 'rejected')
                  AND av.evidence IS NOT NULL
                  AND av.verified_utc <= coalesce(c2.fit_utc, c2.created_utc)
                  AND (av.superseded_utc IS NULL
                       OR av.superseded_utc > coalesce(c2.fit_utc, c2.created_utc))
                  -- A label whose run has been pruned joins no run, and is
                  -- the in-app engine's as recalibrate_instrument takes it.
                  AND (r.peak_assignment_run_id IS NULL OR r.engine = :engine)
                GROUP BY c2.assignment_calibration_id
            ) AS pool
            WHERE pool.assignment_calibration_id = c.assignment_calibration_id
            """
        ),
        {"engine": _IN_APP_ENGINE},
    )


def downgrade() -> None:
    op.get_bind().execute(
        sa.text(
            "UPDATE peak_assignment_run"
            " SET config = (config::jsonb - 'ionization_setup')::json"
            " WHERE config IS NOT NULL AND json_typeof(config) = 'object'"
            " AND config::jsonb ? 'ionization_setup'"
        )
    )
    op.drop_column("assignment_calibration", "ionization_setups")
