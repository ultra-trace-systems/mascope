"""Give every ionization mechanism its structured adduct row

A mechanism is one notation string, ``[M+H]+``. What the string cannot express
cleanly is what a multimer (``[2M+H]+``), a neutral loss (``[M+H-H2O]+``) and
a charge above one (``[M+2H]2+``) need: a row from which the ion's polarity,
charge and mass shift are derived rather than typed (step 4.6 of the
assignment quality plan; the target model's ``Adduct``, section 4.4 of the
ionization design carried into ``docs/dev/ingest_routing_and_splitting.md``).
The row builds on the string; it does not replace it.

``ionization_mechanism`` gains six columns:

- ``operation``, ``"add"`` or ``"remove"``;
- ``species``, the formula added or removed, empty for the electron;
- ``species_charge``, the charge it carries, all of it;
- ``multimer_n``, how many molecules the ion holds, 1 unless it says;
- ``neutral_loss``, the neutral the ion loses, NULL for none;
- ``name``, what people call it, NULL on every row there is.

**Every row is filled here** by parsing its string through the notation
module (``mascope_tools.composition.mechanism_notation.to_row``), the
reading the model writes the columns by from now on whenever the string is
set. Imported rather than restated, unlike the notation migration
(``5193d1e942e0``): that one rewrote the string, the record itself, by the
grammar of its day, where this one fills a mirror of the string that has to
agree with the application reading it. A row written in the legacy notation
(two rows of one mechanism, which that migration left for an operator to
merge) reads as the standard one does.

**A row that reads as neither notation stops the upgrade**, listing every such
row by id and text, and nothing is written: a free-text label an older
validator let through has no row to give. Correct it to a mechanism, or
delete it, and run the upgrade again.

The downgrade drops the columns, ``name`` with them.

Revision ID: c39f8f2c0b3b
Revises: bc7bd6e2ed43
Create Date: 2026-11-05 21:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision: str = "c39f8f2c0b3b"
down_revision: Union[str, Sequence[str], None] = "bc7bd6e2ed43"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _rows(connection) -> list[tuple[str, dict]]:
    """Every mechanism's id and its row's columns.

    :raises RuntimeError: A row reads as neither notation; every such row is
        named.
    """
    from mascope_tools.composition.mechanism_notation import (
        MechanismNotationError,
        to_row,
    )

    rows, unreadable = [], []
    for mechanism_id, notation in connection.execute(
        text(
            "SELECT ionization_mechanism_id, ionization_mechanism "
            "FROM ionization_mechanism ORDER BY ionization_mechanism_id"
        )
    ):
        try:
            row = to_row(notation or "")
        except MechanismNotationError as error:
            unreadable.append(f"  {mechanism_id}: {notation!r} ({error})")
            continue
        rows.append(
            (
                mechanism_id,
                {
                    "operation": row.operation,
                    "species": row.species,
                    "species_charge": row.species_charge,
                    "multimer_n": row.multimer_n,
                    "neutral_loss": row.neutral_loss,
                },
            )
        )
    if unreadable:
        raise RuntimeError(
            f"{len(unreadable)} ionization mechanism row(s) read as neither "
            "the standard adduct notation nor the legacy one, so they have no "
            "structured row. Correct each to a mechanism ('[M+H]+') or delete "
            "it, then upgrade again:\n" + "\n".join(unreadable)
        )
    return rows


def upgrade() -> None:
    connection = op.get_bind()
    # Read and refused before the schema changes, so a refusal leaves the
    # table as it was.
    rows = _rows(connection)

    op.add_column(
        "ionization_mechanism",
        sa.Column("operation", sa.String(length=6), nullable=True),
    )
    op.add_column(
        "ionization_mechanism",
        sa.Column("species", sa.String(length=256), nullable=True),
    )
    op.add_column(
        "ionization_mechanism",
        sa.Column("species_charge", sa.Integer(), nullable=True),
    )
    op.add_column(
        "ionization_mechanism",
        sa.Column("multimer_n", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column(
        "ionization_mechanism",
        sa.Column("neutral_loss", sa.String(length=256), nullable=True),
    )
    op.add_column(
        "ionization_mechanism",
        sa.Column("name", sa.String(length=256), nullable=True),
    )

    update = text(
        "UPDATE ionization_mechanism SET operation = :operation, "
        "species = :species, species_charge = :species_charge, "
        "multimer_n = :multimer_n, neutral_loss = :neutral_loss "
        "WHERE ionization_mechanism_id = :mechanism_id"
    )
    for mechanism_id, columns in rows:
        connection.execute(update, {"mechanism_id": mechanism_id, **columns})


def downgrade() -> None:
    for column in (
        "name",
        "neutral_loss",
        "multimer_n",
        "species_charge",
        "species",
        "operation",
    ):
        op.drop_column("ionization_mechanism", column)
