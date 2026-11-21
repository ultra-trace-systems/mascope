"""A pass of the batch stages as one batch run: action 'assign', status 'cancelled', a cancel flag

A pass of the batch stages (``batch_stages.py``, MAS-1011) records itself as
one batch run, with one snapshot, where each stage used to record its own.

- The run's ``action`` has to be allowed to name the pass: the action check
  gains ``assign``.
- A pass a person cancels stops between stages, keeps what it wrote and is
  recorded as cancelled, which is neither completed (it never becomes
  current) nor failed (nothing went wrong): the status check gains
  ``cancelled``.
- ``batch_peak_run.cancel_requested_utc`` is the flag the cancel route sets
  and the pass reads between stages and between the search's samples. It is
  on the run, in the database, because the request can reach another backend
  process than the one running the pass. Nullable with no backfill: no run
  was asked to stop before this revision.

Revision ID: 9c3e5a7b1d24
Revises: 6fa4357c5378
Create Date: 2026-11-21 06:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "9c3e5a7b1d24"
down_revision: Union[str, Sequence[str], None] = "6fa4357c5378"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ACTION_CONSTRAINT = "ck_batch_peak_run_action_valid"
STATUS_CONSTRAINT = "ck_batch_peak_run_status_valid"
ACTIONS_BEFORE = (
    "action IN ('fold', 'rebuild', 'search_untargeted', 'import', "
    "'detect_series', 'read_co_variation', 'stamp_isotopologues', "
    "'strip_isotopologues', 'read_batch_checks', 'decide_known_species', "
    "'measure_water_ladders')"
)
ACTIONS_AFTER = (
    "action IN ('fold', 'rebuild', 'search_untargeted', 'import', "
    "'detect_series', 'read_co_variation', 'stamp_isotopologues', "
    "'strip_isotopologues', 'read_batch_checks', 'decide_known_species', "
    "'measure_water_ladders', 'assign')"
)
STATUS_BEFORE = "status IN ('running', 'completed', 'failed')"
STATUS_AFTER = "status IN ('running', 'completed', 'failed', 'cancelled')"


def upgrade() -> None:
    op.drop_constraint(op.f(ACTION_CONSTRAINT), "batch_peak_run", type_="check")
    op.create_check_constraint(op.f(ACTION_CONSTRAINT), "batch_peak_run", ACTIONS_AFTER)
    op.drop_constraint(op.f(STATUS_CONSTRAINT), "batch_peak_run", type_="check")
    op.create_check_constraint(op.f(STATUS_CONSTRAINT), "batch_peak_run", STATUS_AFTER)
    op.add_column(
        "batch_peak_run",
        sa.Column("cancel_requested_utc", sa.TIMESTAMP(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("batch_peak_run", "cancel_requested_utc")
    # A cancelled run stopped before its end and never became current, as a
    # failed one: the old constraint names it so.
    op.execute("UPDATE batch_peak_run SET status = 'failed' WHERE status = 'cancelled'")
    op.drop_constraint(op.f(STATUS_CONSTRAINT), "batch_peak_run", type_="check")
    op.create_check_constraint(op.f(STATUS_CONSTRAINT), "batch_peak_run", STATUS_BEFORE)
    # A pass cannot be named once the value is gone: its runs go with it, as
    # each earlier revision's did. What it wrote stays on the ledger, and the
    # stages' rows still say where each stands. The batch's current run, if
    # it was a pass, goes too; the next fold mints the implicit one again.
    op.execute("DELETE FROM batch_peak_run WHERE action = 'assign'")
    op.drop_constraint(op.f(ACTION_CONSTRAINT), "batch_peak_run", type_="check")
    op.create_check_constraint(
        op.f(ACTION_CONSTRAINT), "batch_peak_run", ACTIONS_BEFORE
    )
