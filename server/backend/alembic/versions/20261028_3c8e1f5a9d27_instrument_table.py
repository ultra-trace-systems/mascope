"""Keep each instrument as a row of its own

Until now an instrument was ``SELECT DISTINCT sample_file.instrument``, the
first segment of a file's name, and eight tables spelled it out as free text:
delete the last file of an instrument and nothing in the database knew it any
more (``docs/dev/ingest_routing_and_splitting.md``, phase 8, model 4.1). The
``instrument`` table holds it now, and outlives its files.

**The table.** ``name`` is the string the files carry, spelled exactly, and
unique: one row per distinct string, as the instrument list groups them. The
rest is what a file's reader reports, filled as files of the instrument are
registered from now on; this migration knows only the class of the files
already registered under a name and the vendor that follows from it, so
``model``, ``serial_number``, ``software_version`` and ``cv_term`` start NULL.

**The links.** Every table with a free-text ``instrument`` naming an
instrument gains a nullable ``instrument_id`` beside it: ``notification``,
``agent_device``, ``dataset``, ``dataset_tombstone``, ``sample_file``,
``ionization_mode``, ``method_binding`` and ``instrument_function``. The string
columns stay, and every read still uses them. ``assignment_calibration`` is
not among them: its ``instrument`` is the instrument class ("orbi", "tof"),
which names no instrument.

**The backfill.** One row per distinct non-empty string in any of the eight
columns - a name a mode's scope or a paired device gave and no file has
carried is an instrument too - and every row that carries a name is linked to
its row. ``created_utc`` is the first registration of a file of it, else now;
``last_seen_utc`` the last, else NULL.

Downgrade drops the links and the table; the strings were never touched.

Revision ID: 3c8e1f5a9d27
Revises: 9ec8e03ec3c9
Create Date: 2026-10-27 20:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from nanoid import generate


# revision identifiers, used by Alembic.
revision: str = "3c8e1f5a9d27"
down_revision: Union[str, Sequence[str], None] = "9ec8e03ec3c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz1234567890"

#: The tables whose ``instrument`` names an instrument, in the order they are
#: linked.
LINKED_TABLES = (
    "notification",
    "agent_device",
    "dataset",
    "dataset_tombstone",
    "sample_file",
    "ionization_mode",
    "method_binding",
    "instrument_function",
)

#: As ``api.new.instruments.registry.VENDOR_BY_TYPE``, written out: a
#: migration does not read the code it migrates for.
_VENDOR_BY_TYPE = {"orbi": "Thermo Fisher Scientific", "tof": "TOFWERK"}


def _gen_id(length=16):
    return generate(_ALPHABET, length)


def _names_sql() -> str:
    return " UNION ".join(
        f"SELECT instrument AS name FROM {table}"
        f" WHERE instrument IS NOT NULL AND btrim(instrument) <> ''"
        for table in LINKED_TABLES
    )


def upgrade() -> None:
    op.create_table(
        "instrument",
        sa.Column("instrument_id", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("instrument_type", sa.String(length=8), nullable=True),
        sa.Column("vendor", sa.String(length=64), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("serial_number", sa.String(length=64), nullable=True),
        sa.Column("software_version", sa.String(length=64), nullable=True),
        sa.Column("cv_term", sa.String(length=32), nullable=True),
        sa.Column("created_utc", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("last_seen_utc", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("instrument_id", name="pk_instrument"),
        sa.UniqueConstraint("name", name="uq_instrument_name"),
    )
    for table in LINKED_TABLES:
        op.add_column(
            table, sa.Column("instrument_id", sa.String(length=16), nullable=True)
        )
        op.create_foreign_key(
            f"fk_{table}_instrument_id_instrument",
            table,
            "instrument",
            ["instrument_id"],
            ["instrument_id"],
            ondelete="SET NULL",
        )
    op.create_index("ix_sample_file_instrument_id", "sample_file", ["instrument_id"])

    conn = op.get_bind()
    names = (
        conn.execute(
            sa.text(
                f"""
                SELECT n.name,
                       min(sf.instrument_type),
                       min(sf.sample_file_utc_created),
                       max(sf.sample_file_utc_created)
                FROM ({_names_sql()}) n
                LEFT JOIN sample_file sf ON sf.instrument = n.name
                GROUP BY n.name
                """
            )
        )
        .tuples()
        .all()
    )
    if names:
        conn.execute(
            sa.text(
                "INSERT INTO instrument (instrument_id, name, instrument_type,"
                " vendor, created_utc, last_seen_utc)"
                " VALUES (:instrument_id, :name, :instrument_type, :vendor,"
                " coalesce(:created_utc, now()), :last_seen_utc)"
            ).bindparams(
                sa.bindparam("created_utc", type_=sa.TIMESTAMP(timezone=True)),
                sa.bindparam("last_seen_utc", type_=sa.TIMESTAMP(timezone=True)),
            ),
            [
                {
                    "instrument_id": _gen_id(),
                    "name": name,
                    "instrument_type": instrument_type,
                    "vendor": _VENDOR_BY_TYPE.get(instrument_type or ""),
                    "created_utc": first,
                    "last_seen_utc": last,
                }
                for name, instrument_type, first, last in names
            ],
        )
    for table in LINKED_TABLES:
        conn.execute(
            sa.text(
                f"UPDATE {table} AS t SET instrument_id = i.instrument_id"
                " FROM instrument AS i WHERE t.instrument = i.name"
            )
        )


def downgrade() -> None:
    op.drop_index("ix_sample_file_instrument_id", table_name="sample_file")
    for table in reversed(LINKED_TABLES):
        op.drop_constraint(
            f"fk_{table}_instrument_id_instrument", table, type_="foreignkey"
        )
        op.drop_column(table, "instrument_id")
    op.drop_table("instrument")
