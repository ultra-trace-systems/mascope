"""Shared fixtures for mascope_reference tests.

Builds a throwaway SQLite database whose ``reference_source`` /
``reference_compound`` tables mirror the backend ORM schema, so the ingest and
query paths (which target those tables by name via the library's Core handles)
can be exercised without Postgres or the backend.
"""

from pathlib import Path

import pytest
from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
)


metadata = MetaData()

Table(
    "reference_source",
    metadata,
    Column("reference_source_id", Integer, primary_key=True, autoincrement=True),
    Column("name", String),
    Column("version", String),
    Column("license", String),
    Column("record_count", Integer),
    Column("is_active", Boolean),
    Column("ingested_at", DateTime(timezone=True)),
)

Table(
    "reference_compound",
    metadata,
    Column("reference_compound_id", Integer, primary_key=True, autoincrement=True),
    Column(
        "reference_source_id",
        Integer,
        ForeignKey("reference_source.reference_source_id"),
    ),
    Column("formula", String),
    Column("monoisotopic_mass", Float),
    Column("charge", Integer),
    Column("inchikey", String),
    Column("name", Text),
    Column("smiles", Text),
    Column("inchi", Text),
    Column("source_native_id", String),
    Column("xrefs", JSON),
    Column("license", String),
)


@pytest.fixture
def db_path(tmp_path) -> Path:
    """Path to a fresh file-backed SQLite database with the reference tables."""
    path = tmp_path / "reference.sqlite"
    engine = create_engine(f"sqlite:///{path}")
    metadata.create_all(engine)
    engine.dispose()
    return path


@pytest.fixture
def sync_engine(db_path):
    """A synchronous engine over the reference test database."""
    engine = create_engine(f"sqlite:///{db_path}")
    yield engine
    engine.dispose()
