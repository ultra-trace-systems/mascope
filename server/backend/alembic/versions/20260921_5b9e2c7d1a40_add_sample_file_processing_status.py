"""Add sample_file processing status columns and sample_file_utc_created

Auto-processing records on each sample file how far it got: bound to its
ionization modes, calibrated, done, or stopped because the file needs a
chemistry, its calibration failed or processing failed. Until now a file
that ended with no samples, or with samples that were never matched, said
nothing about why.

``sample_file_utc_created`` records when the converter registered the file,
for listing the files added recently (#482). The database sets it on
insert.

All four columns are nullable and nothing is backfilled: a row that predates
them keeps NULL, which reads as "not recorded". In particular no registration
time is made up: a file's samples are recreated when it is re-processed, so
their times do not say when it arrived. The created time gets its default
only after it is added, because Postgres gives existing rows a default that
is present when the column is added - every archived file would then read as
registered on the day of this upgrade. The status and the created time are
indexed for the lists that pick out files by them.

Revision ID: 5b9e2c7d1a40
Revises: c2d9f4a71b3e
Create Date: 2026-09-21 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "5b9e2c7d1a40"
down_revision: Union[str, Sequence[str], None] = "c2d9f4a71b3e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "sample_file",
        sa.Column("processing_status", sa.String(length=24), nullable=True),
    )
    op.add_column(
        "sample_file",
        sa.Column("processing_detail", sa.Text(), nullable=True),
    )
    op.add_column(
        "sample_file",
        sa.Column("processing_updated_utc", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    # Bare first, then the default: added with it, the column would give every
    # existing row the time of this upgrade.
    op.add_column(
        "sample_file",
        sa.Column(
            "sample_file_utc_created", sa.TIMESTAMP(timezone=True), nullable=True
        ),
    )
    op.alter_column(
        "sample_file",
        "sample_file_utc_created",
        existing_type=sa.TIMESTAMP(timezone=True),
        existing_nullable=True,
        server_default=sa.text("now()"),
    )
    op.create_index(
        op.f("ix_sample_file_processing_status"),
        "sample_file",
        ["processing_status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_sample_file_sample_file_utc_created"),
        "sample_file",
        ["sample_file_utc_created"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_sample_file_sample_file_utc_created"), table_name="sample_file"
    )
    op.drop_index(op.f("ix_sample_file_processing_status"), table_name="sample_file")
    op.drop_column("sample_file", "sample_file_utc_created")
    op.drop_column("sample_file", "processing_updated_utc")
    op.drop_column("sample_file", "processing_detail")
    op.drop_column("sample_file", "processing_status")
