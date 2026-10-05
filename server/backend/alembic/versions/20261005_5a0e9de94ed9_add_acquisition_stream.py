"""Add acquisition_stream, and the stream a sample item is cut from

A raw Orbitrap file whose acquisition method runs more than one MS1
experiment in a polarity can be processed per experiment: a scan stream, with
a peak list of its own (``docs/dev/ingest_routing_and_splitting.md``,
sections 4.4 and 4.5). A sample item has had only its polarity to say which
scans it is cut from, and two streams of one polarity share that.

``acquisition_stream`` holds one row per stream of a file processed that way,
and ``sample_item.stream_id`` points an item at its stream.

**The table starts empty and the column starts NULL, and nothing fills them
in for what already exists.** NULL means the item spans every MS1 scan of its
polarity, which is what every item made so far means. A multi-stream file
already processed keeps its pooled items until someone re-processes it
(section 9.1): which scans an item covers was decided when it was made, and a
decision is never backfilled.

Nothing writes either yet. The columns here are the ones a stream's census
gives; its fits, its binding and its state arrive with the steps that write
them, each in its own revision.

One row per (file, stream key). The key is a stream's name within its file
and is not stable across files, so the uniqueness is per file and the
identity columns beside it carry no constraint.

``sample_item.stream_id`` has no ON DELETE action. A stream row goes with its
file, or when the file is processed again, and its items go first both times;
an item left pointing at a stream being deleted is a fault to refuse. SET
NULL would turn such an item silently into one over the whole polarity, and
CASCADE would delete a sample because a description of its file was rewritten.

Revision ID: 5a0e9de94ed9
Revises: 7a2d5c8e3b94
Create Date: 2026-10-05 16:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "5a0e9de94ed9"
down_revision: Union[str, Sequence[str], None] = "7a2d5c8e3b94"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# What the models' naming convention produces for these two
# (``models.NAMING_CONVENTION``), so the drift test sees one schema.
_STREAM_FK = "fk_sample_item_stream_id_acquisition_stream"
_STREAM_INDEX = "ix_sample_item_stream_id"


def upgrade() -> None:
    op.create_table(
        "acquisition_stream",
        sa.Column("stream_id", sa.String(length=16), nullable=False),
        sa.Column("sample_file_id", sa.String(length=16), nullable=False),
        sa.Column("stream_key", sa.String(length=512), nullable=False),
        sa.Column("signature_key", sa.String(length=512), nullable=False),
        sa.Column("scan_segment", sa.Integer(), nullable=True),
        sa.Column("scan_event", sa.Integer(), nullable=True),
        sa.Column("signature", sa.JSON(), nullable=False),
        sa.Column("scan_count", sa.Integer(), nullable=False),
        sa.Column("blocks", sa.Integer(), nullable=False),
        sa.Column("t_first", sa.Float(), nullable=False),
        sa.Column("t_last", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(
            ["sample_file_id"],
            ["sample_file.sample_file_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("stream_id"),
        # Its index also finds a file's streams and serves the file's
        # cascade, sample_file_id being its leading column.
        sa.UniqueConstraint(
            "sample_file_id", "stream_key", name="uq_acquisition_stream_file_key"
        ),
    )
    op.add_column("sample_item", sa.Column("stream_id", sa.String(16), nullable=True))
    op.create_foreign_key(
        _STREAM_FK,
        "sample_item",
        "acquisition_stream",
        ["stream_id"],
        ["stream_id"],
    )
    op.create_index(_STREAM_INDEX, "sample_item", ["stream_id"])


def downgrade() -> None:
    # The streams go with the table, and an item that pointed at one reads as
    # an item over its whole polarity again. That is a loss only for a file
    # that was processed per stream, which a re-upgrade does not restore:
    # such a file has to be processed again.
    op.drop_index(_STREAM_INDEX, table_name="sample_item")
    op.drop_constraint(_STREAM_FK, "sample_item", type_="foreignkey")
    op.drop_column("sample_item", "stream_id")
    op.drop_table("acquisition_stream")
