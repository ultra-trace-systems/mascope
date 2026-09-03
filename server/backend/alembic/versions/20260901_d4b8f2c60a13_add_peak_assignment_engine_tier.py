"""Add peak_assignment.engine_tier: the producing engine's own verdict

An imported row's ``tier`` is not the importer's choice. It is checked against
the run's declared ``tier_bands`` and must be exactly the tier the row's
evidence -- ``fit_score`` weighted by the chemical plausibility of
``assigned_formula`` -- falls in (``import_validation.tier_coherence_error``).
That rule earns its keep: it is what lets an ``assigned`` row from one engine
sort, filter and roll up beside an ``assigned`` row from another and mean the
same thing.

It also means an external engine cannot record a tier it reached any other way,
and a demotion is refused as firmly as an inflation. That is not a gap in the
rule, it is the rule -- but it leaves nowhere to put a verdict an engine
reached mechanically rather than by thresholding. peaky is exactly that case:
it tiers on window uniqueness, isotopologue corroboration, a mass-degeneracy
audit and composition heuristics, all of which can knock a peak below what its
evidence alone would earn. Publishing such a run today either loses that
judgement or is refused outright.

``engine_tier`` is where it goes: nullable, exempt from the coherence check,
and deliberately excluded from every roll-up. The pair reads as

    tier         what THIS SERVER'S banding says about the row's evidence
    engine_tier  what the engine that produced the row concluded

so the two are comparable precisely because only one of them is derived here.
A row where they differ is the interesting one -- it is a disagreement about
how much confidence the evidence supports, which is the thing running two
engines over one sample is meant to expose.

Nullable with **no backfill**, and that is a deliberate choice rather than a
shortcut. For an in-app run the engine's tier *is* ``tier``; copying it into a
second column would assert a separate source of truth that must then be kept in
step with every re-tiering path (curation, recalibration, the copy service).
NULL means "this engine stated no tier for this row", which is also the honest
answer for the rows an external engine leaves untiered -- peaky, for instance,
tiers only its committed M0 rows and says nothing about isotopologue children
or the unexplained residual.

The width matches ``tier`` (String(24)) because it holds the same vocabulary.
What keeps a value inside it is the closed ``AssignmentTier`` literal on the
payload field, not a length check: ``test_row_field_bounds_match_the_columns``
lists this field under ``VOCABULARY_BOUNDED`` and skips it, exactly as it skips
``role``, ``source`` and ``tier``. So a *widened vocabulary* is what this column
would have to follow, and nothing fails if it does not - keep the two in step by
hand when a tier name is added.

Revision ID: d4b8f2c60a13
Revises: 97c42c48e011
Create Date: 2026-09-01 17:10:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "d4b8f2c60a13"
down_revision: Union[str, Sequence[str], None] = "97c42c48e011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "peak_assignment",
        sa.Column("engine_tier", sa.String(length=24), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("peak_assignment", "engine_tier")
