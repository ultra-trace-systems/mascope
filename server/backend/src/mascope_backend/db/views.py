"""Database views for read-only combined data access.

This module defines SQLAlchemy mappings for database views created via migrations.
Views combine data from multiple tables for efficient read-only querying.

Views defined here:
    - sample_view: Combines sample_item and sample_file via FK relationship
"""

from datetime import datetime as dt
from typing import Optional

from sqlalchemy import (
    JSON,
    TIMESTAMP,
    Column,
    Float,
    ForeignKey,
    Integer,
    String,
    Table,
    text,
)
from sqlalchemy.orm import Mapped, registry


# Separate mapper registry for views
# Keeps views isolated from Base model hierarchy (models.py)
mapper_registry = registry()

# --- Sample view definition --- #
sample_view_table = Table(
    "sample_view",
    mapper_registry.metadata,
    # Primary key
    Column("sample_item_id", String(16), primary_key=True),
    # Foreign keys
    Column("sample_file_id", String(16)),
    Column(
        "instrument_function_id",
        String(32),
        ForeignKey("instrument_function.instrument_function_id"),
    ),
    Column("sample_batch_id", String(16), ForeignKey("sample_batch.sample_batch_id")),
    Column(
        "ionization_mode_id",
        String(16),
        ForeignKey("ionization_mode.ionization_mode_id"),
    ),
    # Sample item columns
    Column("sample_item_name", String(256)),
    Column("sample_item_type", String(64)),
    Column("locked", Integer),
    Column("sample_item_attributes", JSON),
    Column("filter_id", String(6)),
    Column("tic", Float),
    Column("polarity", String(1)),
    Column("t0", Float),
    Column("t1", Float),
    Column("sample_item_utc_created", TIMESTAMP),
    Column("sample_item_utc_modified", TIMESTAMP),
    # Sample file columns (joined via FK)
    Column("filename", String(256)),
    Column("instrument", String(64)),
    Column("instrument_type", String(8)),
    Column("source_filename", String(256)),
    Column("method_file", String(256)),
    Column("length", Float),
    Column("range", JSON),
    Column("mz_calibration", JSON),
    Column("datetime", TIMESTAMP),
    Column("datetime_utc", TIMESTAMP),
)


@mapper_registry.mapped
class Sample:
    """
    Read-only ORM mapping to sample_view database view.

    Combines sample_item and sample_file data via FK join for convenient querying.
    This view is created by database migrations and cannot be modified via ORM.

    Note:
    - This is a READ-ONLY view. Modifications must be done via SampleItem/SampleFile tables.
    - All columns from both tables are accessible as if they were a single entity.
    - The view is created/managed by database migrations, not by SQLAlchemy.
    """

    __table__ = sample_view_table
    __mapper_args__ = {"primary_key": [sample_view_table.c.sample_item_id]}

    # Type hints for Pylint/IDE/type checkers

    # Primary key
    sample_item_id: Mapped[str]

    # Foreign keys
    sample_file_id: Mapped[str]
    instrument_function_id: Mapped[Optional[str]]  # Nullable (SET NULL on delete)
    sample_batch_id: Mapped[str]
    ionization_mode_id: Mapped[Optional[str]]  # Nullable (SET NULL on delete)

    # Sample item columns
    sample_item_name: Mapped[str]
    sample_item_type: Mapped[str]
    locked: Mapped[int]  # NOT NULL (has default)
    sample_item_attributes: Mapped[Optional[dict]]
    filter_id: Mapped[Optional[str]]
    tic: Mapped[Optional[float]]
    polarity: Mapped[Optional[str]]
    t0: Mapped[Optional[float]]
    t1: Mapped[Optional[float]]
    sample_item_utc_created: Mapped[Optional[dt]]
    sample_item_utc_modified: Mapped[Optional[dt]]

    # Sample file columns (joined via FK)
    filename: Mapped[str]
    instrument: Mapped[str]
    method_file: Mapped[Optional[str]]
    length: Mapped[float]
    range: Mapped[dict]
    mz_calibration: Mapped[Optional[dict]]
    datetime: Mapped[dt]
    datetime_utc: Mapped[dt]

    @classmethod
    def create_view(cls) -> str:
        """
        Generate raw SQL CREATE VIEW statement for sample_view.

        Returns plain SQL string (not wrapped in text()) for use with op.execute().

        Example:
            # In Alembic migration:
            from mascope_backend.db.views import Sample

            def upgrade():
                op.execute(Sample.create_view())

        :param cls: The Sample class
        :type cls: Type[Sample]
        :return: Raw SQL CREATE VIEW statement
        :rtype: str
        """
        return """
            CREATE VIEW sample_view AS
            SELECT
                -- Primary key
                si.sample_item_id,
                -- Foreign keys
                sf.sample_file_id,
                sf.instrument_function_id,
                si.sample_batch_id,
                si.ionization_mode_id,
                -- Sample item columns
                si.sample_item_name,
                si.sample_item_type,
                si.locked,
                si.sample_item_attributes,
                si.filter_id,
                si.tic,
                si.polarity,
                si.t0,
                si.t1,
                si.sample_item_utc_created,
                si.sample_item_utc_modified,
                -- Sample file columns (joined via FK)
                sf.filename,
                sf.instrument,
                sf.instrument_type,
                sf.source_filename,
                sf.method_file,
                sf.length,
                sf.range,
                sf.mz_calibration,
                sf.datetime,
                sf.datetime_utc
            FROM sample_item si
            INNER JOIN sample_file sf ON si.sample_file_id = sf.sample_file_id;
        """

    @classmethod
    def drop_view(cls) -> str:
        """
        Generate raw SQL DROP VIEW statement for sample_view.

        :param cls: The Sample class
        :type cls: Type[Sample]
        :return: Raw SQL DROP VIEW statement
        :rtype: str
        """
        return "DROP VIEW IF EXISTS sample_view"

    @classmethod
    async def update_view(cls, session) -> None:
        """
        Update the sample_view by dropping and recreating it.

        This is useful in migrations when the view definition needs to change
        due to schema updates or bug fixes.

        Example:
            # In migration script:
            from mascope_backend.db import Sample, async_session

            async with async_session() as session:
                await Sample.update_view(session)
                await session.commit()

        :param session: Active AsyncSession to use for executing statements
        :type session: AsyncSession
        :return: None
        """
        await session.execute(text(cls.drop_view()))
        await session.execute(text(cls.create_view()))
