"""Record on each item which rung bound it

An ACQUISITION sample item carries an ``ionization_mode_id`` and no account of
how it got one. Everything about routing therefore has to be reconstructed:
the first fleet measurement of the method bindings had to reproduce the token
rule in SQL and validate it against what files actually bound to, and even
then could not say which binding row a given file would have used, because
the signature class lives in each file's ``.props`` rather than in the
database (``docs/dev/ingest_routing_and_splitting.md``, section 5.7).

Two columns end that. ``bound_by`` is the rung - "declared", "explicit",
"token" or "method" - and ``method_binding_id`` is the binding row, for the
rung that is one. From here the same question is a ``GROUP BY``, which is what
makes the learned rung observable when it is switched on per site.

**Nothing is backfilled, and nothing ever will be.** Both columns are NULL on
every existing item, and NULL means "decided before this was recorded". The
token rule could be re-run over the history to guess a rung, and that is
exactly the move section 9.1 forbids: facts a raw file holds are restored
fleet-wide, decisions are not. A guess here would also be a guess about
configuration as it stood months ago - tokens and modes have been edited
since - and would read in a report as though it had been recorded at the time.

An item a person builds by hand gets NULL too, and for the plainer reason
that no rung bound it. The ingest pipeline is the only writer: the request
models the item routes take carry no provenance field, so nothing outside can
claim a rung.

``method_binding_id`` is indexed and ``bound_by`` is not. The index is there
for the query that reads by binding - which items did this binding bind, and
does it still name their mode - and to keep a deleted binding from scanning
every item to set the column NULL. ``bound_by`` holds four values and is read
by counting all of them, which scans whatever is indexed.

Revision ID: 6e4a9c2b7f31
Revises: 5c1d8b3a7e29
Create Date: 2026-10-01 09:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "6e4a9c2b7f31"
down_revision: Union[str, Sequence[str], None] = "5c1d8b3a7e29"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Both names are what the models' naming convention produces for these two
# (``models.NAMING_CONVENTION``), so the drift test sees one schema.
_BINDING_FK = "fk_sample_item_method_binding_id_method_binding"
_BINDING_INDEX = "ix_sample_item_method_binding_id"


def upgrade() -> None:
    op.add_column("sample_item", sa.Column("bound_by", sa.String(16), nullable=True))
    op.add_column(
        "sample_item", sa.Column("method_binding_id", sa.String(16), nullable=True)
    )
    # SET NULL rather than CASCADE: a binding is a summary of how files routed
    # and deleting one must not delete the samples it routed.
    op.create_foreign_key(
        _BINDING_FK,
        "sample_item",
        "method_binding",
        ["method_binding_id"],
        ["method_binding_id"],
        ondelete="SET NULL",
    )
    op.create_index(_BINDING_INDEX, "sample_item", ["method_binding_id"])


def downgrade() -> None:
    op.drop_index(_BINDING_INDEX, table_name="sample_item")
    op.drop_constraint(_BINDING_FK, "sample_item", type_="foreignkey")
    op.drop_column("sample_item", "method_binding_id")
    op.drop_column("sample_item", "bound_by")
