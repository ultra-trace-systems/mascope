"""Reject mass-based and isotope-pinned target compound formulas

Mass-based compounds - a bare number such as ``"136.1252"`` in
``target_compound_formula`` instead of a composition - were retired with the
molmass fork: ions and isotopes are now computed from the formula, so a mass
alone can never produce an isotope pattern. ``validate_compound_formula``
already refuses them, but only on the Pydantic request models. Four call sites
construct ``TargetCompound`` directly through the ORM, and nothing at all
guards a db script, an SDK caller or hand-written SQL, so the rule was
advisory rather than enforced.

A fleet sweep found the deprecation had not actually taken effect anywhere it
mattered: two production databases still held such rows, one of them with
22,832 match_ion rows depending on them. This makes the rule structural.

The constraint is added **NOT VALID** on purpose. That enforces it on every
INSERT and UPDATE from here on - which is the point - while leaving any legacy
row already in the table alone. Validating instead would abort the upgrade of
a server that still holds one, turning a data-hygiene problem into a failed
deployment; and rows can also reappear from an old restored dump. Clean the
legacy rows first, then ``VALIDATE CONSTRAINT`` separately if a hard guarantee
is wanted on a given server.

The pattern mirrors the backend's ``_NUMERIC_MASS`` regex exactly, including
its deliberate refusal to use a float() parse: "NaN" is sodium nitride, a
chemically valid formula, and must keep being accepted.

A second constraint rejects bracket isotope notation (``[13C]C5H12O6``,
``C[13]C5H12O6``). Ions and isotopes are always generated from the formula, so
pinning an isotope on the compound asks for a monoisotopic species where the
pipeline computes a full pattern regardless. Caret isotopes (``^N`` = 15N) are
deliberately still allowed: they name a labelled *reagent* - a different
substance, such as the 15N nitrate behind the ``+^NO3-`` mechanism - and eight
such compounds are in active production use, against a single bracket one
fleet-wide, itself an unused duplicate of a caret compound.

Revision ID: e2d4a91c7b06
Revises: a7f3c2e9b514
Create Date: 2026-08-27 09:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision: str = "e2d4a91c7b06"
down_revision: Union[str, Sequence[str], None] = "a7f3c2e9b514"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


MASS_CONSTRAINT = "ck_target_compound_formula_not_a_mass"
ISOTOPE_CONSTRAINT = "ck_target_compound_formula_no_bracket_isotope"

# Same shape as mascope_backend.api.models.target.compounds
# .target_compound_pydantic_model._NUMERIC_MASS, anchored so it matches the
# whole value: an optional sign, digits with an optional decimal point (or a
# leading point), and an optional exponent.
_NUMERIC_MASS_SQL = r"^[+-]?([0-9]+\.?[0-9]*|\.[0-9]+)([eE][+-]?[0-9]+)?$"

# Bracket isotope notation in both accepted spellings, '[15N]O3' and 'N[15]O3'.
# Caret isotopes ('^N') are deliberately allowed - they name a labelled reagent,
# a different substance, not one isotopologue of an ordinary compound, and eight
# such compounds are in active production use.
_BRACKET_ISOTOPE_SQL = r"\[[0-9]+[A-Za-z]|[A-Za-z]\[[0-9]+\]"


def upgrade() -> None:
    op.execute(
        text(
            f"""
            ALTER TABLE target_compound
            ADD CONSTRAINT {MASS_CONSTRAINT}
            CHECK (target_compound_formula !~ '{_NUMERIC_MASS_SQL}')
            NOT VALID
            """
        )
    )
    op.execute(
        text(
            f"""
            ALTER TABLE target_compound
            ADD CONSTRAINT {ISOTOPE_CONSTRAINT}
            CHECK (target_compound_formula !~ '{_BRACKET_ISOTOPE_SQL}')
            NOT VALID
            """
        )
    )


def downgrade() -> None:
    for name in (ISOTOPE_CONSTRAINT, MASS_CONSTRAINT):
        op.execute(
            text(f"ALTER TABLE target_compound DROP CONSTRAINT IF EXISTS {name}")
        )
