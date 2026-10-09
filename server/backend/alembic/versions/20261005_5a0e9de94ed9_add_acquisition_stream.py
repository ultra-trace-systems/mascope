"""Add acquisition_stream, and the stream a sample item reads

A raw Orbitrap file whose acquisition method runs more than one MS1
experiment in a polarity is processed per experiment: a scan stream, with a
peak list of its own, and where the streams of a polarity are m/z ranges of
one chemistry they are stitched into one spectrum, the polarity's composite
(``docs/dev/ingest_routing_and_splitting.md``, sections 4.4 and 4.5). A
sample item has had only its polarity to say which scans it is cut from, and
two streams of one polarity share that.

``acquisition_stream`` holds one row per stream of a file, a composite being
a row like any stream: its segments point at it through
``composite_stream_id``, and it carries the map they were stitched by in its
``stitch``. ``sample_item.stream_id`` points an item at the stream it reads,
the composite where its polarity has one.

**The table starts empty and the column starts NULL, and nothing fills them
in for what already exists.** NULL means the item spans every MS1 scan of its
polarity, which is what every item made so far means. A multi-stream file
already processed keeps its pooled items until someone re-processes it
(section 9.1): which scans an item covers was decided when it was made, and a
decision is never backfilled.

Nothing writes either yet. The columns here are the ones a stream's census
gives, and the two a composite needs; a stream's fits, its binding and its
state arrive with the steps that write them, each in its own revision.

**What a row is.** A stream carries its census - signature key, segment and
event, parsed signature, acquisition parameters, scan count, blocks and time
span - and no map. A composite carries the map and no census: it has no
scans of its own, so the census columns are NULL on it, and its ``signature``
holds its polarity. A CHECK pins the two shapes, and another that no stream
is its own composite.

**Where a row belongs.** One row per (file, stream key): the key is a
stream's name within its file and is not stable across files, so the
uniqueness is per file and the identity columns beside it carry no
constraint. Both references carry the file - an item points at a stream of
its own file, and a segment at a composite of its own file - so a pointer
into another file cannot be written.

**What a rebuild does.** A file's rows are updated in place, matched on
(file, key): a stream keeps its id, and whatever points at it still does. A
composite's key is fixed by its polarity, so it comes out the same on every
run. A row the new census no longer gives is deleted - unless an item still
reads it, which the database refuses; such a row is kept and the file's
processing detail names it and the item, until the item is gone.

``sample_item.stream_id`` has no ON DELETE action. A stream row goes with its
file, and on a rebuild only where nothing reads it; an item left pointing at
a stream being deleted is a fault to refuse. SET NULL would turn such an
item silently into one over the whole polarity, and CASCADE would delete a
sample because a description of its file was rewritten.
``composite_stream_id`` has none either: a composite goes only with its file
or when the new census no longer gives it, its segments unpointed first, and
a segment left pointing at a composite being deleted on its own is the same
fault.

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


# What the models' naming convention produces for these
# (``models.NAMING_CONVENTION``), so the drift test sees one schema.
_STREAM_FK = "fk_sample_item_sample_file_id_acquisition_stream"
_STREAM_INDEX = "ix_sample_item_stream_id"
_COMPOSITE_INDEX = "ix_acquisition_stream_composite_stream_id"

# A stream carries a census and no map; a composite the map and no census.
_CENSUS_OR_MAP = (
    "(stitch IS NULL"
    " AND signature_key IS NOT NULL AND scan_count IS NOT NULL"
    " AND blocks IS NOT NULL AND t_first IS NOT NULL AND t_last IS NOT NULL)"
    " OR (stitch IS NOT NULL AND composite_stream_id IS NULL"
    " AND signature_key IS NULL AND acquisition_params IS NULL"
    " AND scan_count IS NULL AND blocks IS NULL"
    " AND t_first IS NULL AND t_last IS NULL)"
)
_NOT_ITS_OWN_COMPOSITE = (
    "composite_stream_id IS NULL OR composite_stream_id <> stream_id"
)


def upgrade() -> None:
    op.create_table(
        "acquisition_stream",
        sa.Column("stream_id", sa.String(length=16), nullable=False),
        sa.Column("sample_file_id", sa.String(length=16), nullable=False),
        sa.Column("stream_key", sa.String(length=512), nullable=False),
        # The census of a stream; NULL on a composite, which has no scans of
        # its own
        sa.Column("signature_key", sa.String(length=512), nullable=True),
        sa.Column("scan_segment", sa.Integer(), nullable=True),
        sa.Column("scan_event", sa.Integer(), nullable=True),
        # The parsed key fields; a composite's holds its polarity
        sa.Column("signature", sa.JSON(), nullable=False),
        # The attributes of the stream's scans as the census samples them,
        # with their variation; NULL on a composite
        sa.Column("acquisition_params", sa.JSON(), nullable=True),
        sa.Column("scan_count", sa.Integer(), nullable=True),
        sa.Column("blocks", sa.Integer(), nullable=True),
        sa.Column("t_first", sa.Float(), nullable=True),
        sa.Column("t_last", sa.Float(), nullable=True),
        # A segment's composite; NULL on a composite itself and on every
        # stream of a file that has none
        sa.Column("composite_stream_id", sa.String(length=16), nullable=True),
        # On a composite: the map it was stitched by; NULL elsewhere
        sa.Column("stitch", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(
            ["sample_file_id"],
            ["sample_file.sample_file_id"],
            ondelete="CASCADE",
        ),
        # The reference carries the file: a segment points at a composite of
        # its own file, and nothing else can be written
        sa.ForeignKeyConstraint(
            ["sample_file_id", "composite_stream_id"],
            ["acquisition_stream.sample_file_id", "acquisition_stream.stream_id"],
        ),
        sa.PrimaryKeyConstraint("stream_id"),
        # Its index also finds a file's streams and serves the file's
        # cascade, sample_file_id being its leading column.
        sa.UniqueConstraint(
            "sample_file_id", "stream_key", name="uq_acquisition_stream_file_key"
        ),
        # What the two references on the pair point at
        sa.UniqueConstraint(
            "sample_file_id", "stream_id", name="uq_acquisition_stream_file_stream"
        ),
        sa.CheckConstraint(_CENSUS_OR_MAP, name="census_or_map"),
        sa.CheckConstraint(_NOT_ITS_OWN_COMPOSITE, name="not_its_own_composite"),
    )
    # The question asked of a composite: its segments
    op.create_index(_COMPOSITE_INDEX, "acquisition_stream", ["composite_stream_id"])
    op.add_column("sample_item", sa.Column("stream_id", sa.String(16), nullable=True))
    # The reference carries the file: an item points at a stream of its own
    # file. A NULL stream_id leaves the pair unchecked, so every existing item
    # is as it was.
    op.create_foreign_key(
        _STREAM_FK,
        "sample_item",
        "acquisition_stream",
        ["sample_file_id", "stream_id"],
        ["sample_file_id", "stream_id"],
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
    # The composite index, references and checks go with the table
    op.drop_table("acquisition_stream")
