"""Let a method binding follow the mode row its files now use

A binding was written once and, while its chemistry held, nothing moved it.
The first fleet measurement showed what that costs
(``docs/dev/ingest_routing_and_splitting.md``, section 5.7): where a filename
token and a binding disagree, the binding points at an older mode row that the
site has stopped using - it could not edit a mode already in use, so it made a
second row for the same reagent - and the binding reads `learned` with no
disagreements, because unanimity is judged on the chemistry and both rows name
the same one. Every state on the row says healthy. By the measure those states
use, it is.

So the row follows its newest observations, with a run counter rather than a
straight move: ``candidate_mode_id`` is the row the recent observations name
while that is not the one the binding holds, and ``n_candidate_streams`` is how
many in a row have named it. At three (``bindings.REPOINT_AFTER``) the binding
re-points. Any observation agreeing with the current row clears the run, so it
is the last three and not three spread over a year.

Why a run at all: the two ways of being wrong cost different amounts. Moving
eagerly lets one corrected file drag a whole method's routing; moving slowly is
nearly free, because this rung sits below the token.

Existing rows start settled - no candidate, a run of zero - which is what every
row that has never seen a second mode for its chemistry should read. The
default is dropped once the existing rows have it, so the column matches
``n_streams`` and ``n_disagreements``: not null, with the model supplying the
value.

**Nothing re-points on this migration**, and no sample is re-bound. A binding
moves when its next three observations say to, and the items already written
keep the mode they were bound to - which the column added in `6e4a9c2b7f31`
records, beside the binding they took, so a row that has since moved is a query
rather than a reconstruction.

Revision ID: 7a2d5c8e3b94
Revises: 6e4a9c2b7f31
Create Date: 2026-10-01 11:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "7a2d5c8e3b94"
down_revision: Union[str, Sequence[str], None] = "6e4a9c2b7f31"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_CANDIDATE_INDEX = "ix_method_binding_candidate_mode_id"


def upgrade() -> None:
    op.add_column(
        "method_binding",
        sa.Column("candidate_mode_id", sa.String(16), nullable=True),
    )
    op.add_column(
        "method_binding",
        sa.Column(
            "n_candidate_streams",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    # Dropped now that every existing row carries the zero: the column is
    # written on every insert the learner and the backfill make, and its two
    # sibling counters have no server default either.
    op.alter_column("method_binding", "n_candidate_streams", server_default=None)
    # SET NULL, as the mode the binding points at already is: a deleted mode
    # must not take the key's history with it, and a candidate that has
    # vanished is simply no candidate.
    op.create_foreign_key(
        "fk_method_binding_candidate_mode_id_ionization_mode",
        "method_binding",
        "ionization_mode",
        ["candidate_mode_id"],
        ["ionization_mode_id"],
        ondelete="SET NULL",
    )
    op.create_index(op.f(_CANDIDATE_INDEX), "method_binding", ["candidate_mode_id"])


def downgrade() -> None:
    # The run is lost, not translated: a binding that was two observations
    # into a move goes back to looking settled, and the next three
    # observations after a re-upgrade make the move again.
    op.drop_index(op.f(_CANDIDATE_INDEX), table_name="method_binding")
    op.drop_constraint(
        "fk_method_binding_candidate_mode_id_ionization_mode",
        "method_binding",
        type_="foreignkey",
    )
    op.drop_column("method_binding", "n_candidate_streams")
    op.drop_column("method_binding", "candidate_mode_id")
