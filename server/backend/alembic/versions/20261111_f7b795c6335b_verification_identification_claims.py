"""Identification claims on the verification tables: claimed_inchikey, claimed_name, level

A verdict judges a formula and a mechanism and had nowhere to name a
structure. A claim is a confirmed verdict that names one
(``docs/dev/identification_claims.md`` sections 5.1, 5.2 and 5.4), so both
verification tables, ``assignment_verification`` and
``batch_peak_verification``, gain the same three nullable columns:

- ``claimed_inchikey``: the structure the confirmation names, indexed;
- ``claimed_name``: the name that goes with it;
- ``level``: the identification level the verdict states, 1 to 5, fixed when
  it is recorded.

Four CHECKs on each table hold them together: a level is 1 to 5; a level 1
to 3 names a structure; only a confirmation names one; the InChIKey has a
target compound's shape. The request layer adds the rule the CHECKs cannot:
a reference-standard, MS/MS or orthogonal confirmation names a structure. A
CHECK would refuse every such verdict recorded before these columns, and the
supersede stamp that later replaces one.

Those earlier verdicts keep their evidence level and name no structure. A
confirmed one reads as level 4, the formula confirmed: the backfill writes 4
on every confirmed row, live or superseded. A rejection or an unsure verdict
states no level.

Downgrade drops the columns, and with them every claim recorded since.

Revision ID: f7b795c6335b
Revises: b7e4c2d9f1a3
Create Date: 2026-11-11 10:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "f7b795c6335b"
down_revision: Union[str, Sequence[str], None] = "b7e4c2d9f1a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ("assignment_verification", "batch_peak_verification")

#: name -> condition, as ``models._claim_checks`` declares them.
_CHECKS = {
    "level_valid": "level IS NULL OR level BETWEEN 1 AND 5",
    "structure_level_names_structure": (
        "level IS NULL OR level > 3 OR claimed_inchikey IS NOT NULL"
    ),
    "claim_is_confirmed": "claimed_inchikey IS NULL OR verdict = 'confirmed'",
    "claimed_inchikey_shape": (
        "claimed_inchikey IS NULL OR claimed_inchikey ~ '^[A-Z]{14}-[A-Z]{10}-[A-Z]$'"
    ),
}


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(
            table, sa.Column("claimed_inchikey", sa.String(length=27), nullable=True)
        )
        op.add_column(table, sa.Column("claimed_name", sa.Text(), nullable=True))
        op.add_column(table, sa.Column("level", sa.SmallInteger(), nullable=True))
        op.create_index(
            op.f(f"ix_{table}_claimed_inchikey"), table, ["claimed_inchikey"]
        )
        # Before the CHECKs, though none refuses a 4: the earlier confirmations
        # confirmed a formula and named no structure.
        op.execute(f"UPDATE {table} SET level = 4 WHERE verdict = 'confirmed'")
        for name, condition in _CHECKS.items():
            op.create_check_constraint(op.f(f"ck_{table}_{name}"), table, condition)


def downgrade() -> None:
    for table in _TABLES:
        for name in _CHECKS:
            op.drop_constraint(op.f(f"ck_{table}_{name}"), table, type_="check")
        op.drop_index(op.f(f"ix_{table}_claimed_inchikey"), table_name=table)
        op.drop_column(table, "level")
        op.drop_column(table, "claimed_name")
        op.drop_column(table, "claimed_inchikey")
