"""Rename the 'identified' confidence tier to 'assigned'

The top tier of peak assignment was called 'identified', which overclaims what
the engine produces. In mass spectrometry an identification is read as MS2- or
reference-standard-level evidence; what this engine does is assign a molecular
formula from accurate mass and an isotope pattern, which is weaker evidence and
can be wrong in ways an identification would not be. The vocabulary now matches
peaky's - Assigned / Candidate / Below assignability - so the ledger's
strongest word means what a mass spectrometrist reads into it.

A tier is a stored string, not an enum, and no CheckConstraint has ever
constrained one, so this revision is pure row rewriting - no DDL. It reaches
five places:

- peak_assignment.tier
- batch_peak.consensus_tier
- batch_peak_occurrence.tier
- peak_assignment_run.tier_bands, whose object KEY is the tier
- peak_assignment_run.config, the persisted dump of PeakAssignmentConfig, whose
  upper-band field is named after the tier it sets and was renamed with it
  ('identified_threshold' -> 'assigned_threshold')

The last of those turns up in an inventory of the vocabulary rather than in a
reading of the tier columns. Nothing in the running server re-parses a stored
config, so a database left half-renamed behaves correctly today; it is
rewritten anyway, because two spellings of one setting in one column is how a
query written later against the current name reads a run that did record a
threshold and reports it as having none.

Both JSON columns are sqlalchemy's generic JSON, which is Postgres `json` and
not `jsonb`, so no jsonb operator applies to them as stored. Each statement
casts to jsonb, edits the key there, and casts the result back.

Unlike most data migrations this one is losslessly reversible - the change is
one word mapped onto another - so downgrade() applies the same mapping in
reverse rather than dropping the vocabulary on the floor.

Expect it to rewrite nothing on most databases: no deployment has the
assignment workflow turned on yet and no published demo snapshot carries a
ledger, so the rows this reaches are the ones developers have made locally.
That is the reason to write it rather than to skip it - an unmigrated local
database keeps rows spelling the tier the old way, and the reader folds a tier
it does not recognise into 'unassigned', so the ledger would quietly under-count
its strongest tier instead of failing.

Revision ID: c1e7b409f2a5
Revises: e2d4a91c7b06
Create Date: 2026-08-29 10:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text
from sqlalchemy.engine import Connection


# revision identifiers, used by Alembic.
revision: str = "c1e7b409f2a5"
down_revision: Union[str, Sequence[str], None] = "e2d4a91c7b06"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# The two spellings, named in the direction the upgrade moves.
LEGACY_TIER = "identified"
CURRENT_TIER = "assigned"

# The three columns that hold a tier as a value. The batch tables spell the
# column differently from the per-sample one, so each is named with its table.
TIER_COLUMNS = (
    ("peak_assignment", "tier"),
    ("batch_peak", "consensus_tier"),
    ("batch_peak_occurrence", "tier"),
)

# One tier column, one word. Guarded on the old value so that a clean database
# is not rewritten at all, and neither is any row already carrying one of the
# tiers this revision does not touch ('candidate', 'below_assignability',
# 'unassigned') or the NULL an occurrence carries when its sample peak was
# never assigned.
_TIER_VALUE_SQL = """
    UPDATE {table}
    SET {column} = '{new}'
    WHERE {column} = '{old}'
"""

# Rename a KEY inside one of peak_assignment_run's JSON objects. The columns
# are Postgres `json`, so each is cast to jsonb to be edited and back to json
# to be stored.
#
# The new entry sits on the LEFT of `||` and the remainder of the object on the
# right, because the right operand wins a key collision: an object carrying
# both spellings has had one band named twice rather than two bands set, and
# the entry already in the current vocabulary is the one to keep. The legacy
# key is dropped either way, which is the whole point.
#
# `jsonb_exists` rather than the `?` operator, which is indistinguishable from
# a driver's parameter placeholder inside a SQL string. The typeof guard is the
# same kind of care in the other direction: `jsonb - text` removes an array
# ELEMENT and raises on a scalar, so a column that somehow holds either is left
# alone instead of being rewritten into something new.
_JSON_KEY_SQL = """
    UPDATE peak_assignment_run
    SET {column} = (
        jsonb_build_object('{new}', {column}::jsonb -> '{old}')
        || ({column}::jsonb - '{old}')
    )::json
    WHERE {column} IS NOT NULL
      AND jsonb_typeof({column}::jsonb) = 'object'
      AND jsonb_exists({column}::jsonb, '{old}')
"""


def _rewrite(connection: Connection, old: str, new: str) -> tuple[int, int, int]:
    """Rewrite the tier *old* as *new* at all five sites.

    Both directions call this with the two words exchanged, so the downgrade
    cannot drift from the upgrade it undoes.

    :param connection: The migration's connection.
    :type connection: Connection
    :param old: The tier spelling to replace.
    :type old: str
    :param new: The tier spelling to write in its place.
    :type new: str
    :return: Rows rewritten, as ``(tier values, tier_bands, config)``.
    :rtype: tuple[int, int, int]
    """
    tier_rows = 0
    for table, column in TIER_COLUMNS:
        tier_rows += connection.execute(
            text(_TIER_VALUE_SQL.format(table=table, column=column, old=old, new=new))
        ).rowcount

    bands = connection.execute(
        text(_JSON_KEY_SQL.format(column="tier_bands", old=old, new=new))
    )
    # The config's upper-band field is named after the tier it sets, so the same
    # rename reaches into it with the suffix carried along.
    config = connection.execute(
        text(
            _JSON_KEY_SQL.format(
                column="config", old=f"{old}_threshold", new=f"{new}_threshold"
            )
        )
    )
    return tier_rows, bands.rowcount, config.rowcount


def _report(old: str, new: str, counts: tuple[int, int, int]) -> None:
    """Print what was rewritten, unless there was nothing to rewrite.

    :param old: The tier spelling that was replaced.
    :type old: str
    :param new: The tier spelling that was written.
    :type new: str
    :param counts: The row counts returned by `_rewrite`.
    :type counts: tuple[int, int, int]
    """
    tier_rows, bands, config = counts
    if not (tier_rows or bands or config):
        return
    print(
        f"Retiered '{old}' as '{new}': {tier_rows} tiered row(s), "
        f"{bands} run tier_bands and {config} run config(s)"
    )


def upgrade() -> None:
    connection = op.get_bind()
    _report(LEGACY_TIER, CURRENT_TIER, _rewrite(connection, LEGACY_TIER, CURRENT_TIER))


def downgrade() -> None:
    # The exact inverse, which means it also renames rows that were never
    # 'identified': anything a rolling deployment wrote natively as 'assigned'
    # after the upgrade goes back to the word the older code reads. Nothing
    # distinguishes those rows from the migrated ones, and leaving them would
    # hand the restored code a tier it does not know - so rewriting them is the
    # correct inverse, not an overreach.
    connection = op.get_bind()
    _report(CURRENT_TIER, LEGACY_TIER, _rewrite(connection, CURRENT_TIER, LEGACY_TIER))
