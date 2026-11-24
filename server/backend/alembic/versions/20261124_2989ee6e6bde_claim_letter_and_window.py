"""A claim's letter and a level-1 batch claim's window, on the verification tables

Step 5.8f of the assignment quality plan moves the claims' evidence-to-level
mapping to version 2 (``docs/dev/identification_claims.md`` section 5.4): a
claim states the shared scale's level, its number and its letter.

- ``level_letter`` on both ``assignment_verification`` and
  ``batch_peak_verification``: the scale's letter beside ``level`` (``2b`` is
  level 2, letter ``b``). Null at level 1, which has none, and on every claim
  recorded under version 1, which keeps its level, its version and no letter.
  A CHECK keeps the pair in the scale's vocabulary.
- ``structure_level_names_structure`` narrows: a class claim (3b, one MS/MS
  loss or orthogonal class evidence) names a class in ``claimed_name`` and no
  InChIKey, so a level 1, a level 2 and a level 3 of any other letter still
  name a structure.
- ``window_start_utc`` and ``window_end_utc`` on ``batch_peak_verification``:
  the first and last acquisition time a level-1 batch claim covers. Outside
  it a member's row names no structure and reads the engine's level. Both or
  neither, in order, and only on a level-1 claim. A per-sample claim is
  scoped by its key and needs none.

Nothing is backfilled: the rows this revision finds were stated under
version 1 and keep what they state.

Downgrade drops the columns. A class claim, the one claim at 1 to 3 without a
structure, cannot stand under the old CHECK, so it loses its level first and
reads the engine's, as a confirmation that states none does.

Revision ID: 2989ee6e6bde
Revises: dbe7955ef975
Create Date: 2026-11-24 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "2989ee6e6bde"
down_revision: Union[str, Sequence[str], None] = "dbe7955ef975"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ("assignment_verification", "batch_peak_verification")

#: The CHECK this revision narrows, before and after.
_STRUCTURE_CHECK = "structure_level_names_structure"
_STRUCTURE_BEFORE = "level IS NULL OR level > 3 OR claimed_inchikey IS NOT NULL"
_STRUCTURE_AFTER = (
    "level IS NULL OR level > 3 OR claimed_inchikey IS NOT NULL "
    "OR (level = 3 AND level_letter = 'b')"
)

#: The letter's CHECK, as ``models._claim_checks`` declares it.
_LETTER_CHECK = "level_letter_valid"
_LETTER_CONDITION = (
    "level_letter IS NULL "
    "OR (level = 2 AND level_letter IN ('a', 'b')) "
    "OR (level = 3 AND level_letter IN ('a', 'b', 'c', 'd')) "
    "OR (level IN (4, 5) AND level_letter IN ('a', 'b'))"
)

#: The window's CHECKs on the batch table, as ``models._window_checks``.
_WINDOW_CHECKS = {
    "window_complete": "(window_start_utc IS NULL) = (window_end_utc IS NULL)",
    "window_ordered": "window_start_utc IS NULL OR window_start_utc <= window_end_utc",
    "window_scopes_level_one": "window_start_utc IS NULL OR level = 1",
}


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(table, sa.Column("level_letter", sa.String(length=1)))
        op.create_check_constraint(
            op.f(f"ck_{table}_{_LETTER_CHECK}"), table, _LETTER_CONDITION
        )
        op.drop_constraint(op.f(f"ck_{table}_{_STRUCTURE_CHECK}"), table, type_="check")
        op.create_check_constraint(
            op.f(f"ck_{table}_{_STRUCTURE_CHECK}"), table, _STRUCTURE_AFTER
        )
    table = "batch_peak_verification"
    op.add_column(table, sa.Column("window_start_utc", sa.TIMESTAMP(timezone=True)))
    op.add_column(table, sa.Column("window_end_utc", sa.TIMESTAMP(timezone=True)))
    for name, condition in _WINDOW_CHECKS.items():
        op.create_check_constraint(op.f(f"ck_{table}_{name}"), table, condition)


def downgrade() -> None:
    table = "batch_peak_verification"
    for name in _WINDOW_CHECKS:
        op.drop_constraint(op.f(f"ck_{table}_{name}"), table, type_="check")
    op.drop_column(table, "window_end_utc")
    op.drop_column(table, "window_start_utc")
    for table in _TABLES:
        op.drop_constraint(op.f(f"ck_{table}_{_STRUCTURE_CHECK}"), table, type_="check")
        # A class claim names no structure, which the old CHECK refuses at 3.
        op.execute(
            f"UPDATE {table} SET level = NULL "
            "WHERE level = 3 AND level_letter = 'b' AND claimed_inchikey IS NULL"
        )
        op.create_check_constraint(
            op.f(f"ck_{table}_{_STRUCTURE_CHECK}"), table, _STRUCTURE_BEFORE
        )
        op.drop_constraint(op.f(f"ck_{table}_{_LETTER_CHECK}"), table, type_="check")
        op.drop_column(table, "level_letter")
