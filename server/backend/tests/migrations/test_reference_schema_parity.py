"""Guard the library's Core table handles against the backend ORM.

The ``reference_source`` / ``reference_compound`` tables are defined by the
backend ORM models; ``mascope_reference`` addresses them by column name through
its own lightweight Core handles. If the two drift, ingest and query break
silently. Lives in the backend suite (not the library one) because it needs the
backend importable and configured, which the library test job deliberately is
not.
"""

from mascope_backend.db.models import ReferenceCompound, ReferenceSource
from mascope_reference.schema import reference_compound, reference_source


def test_source_columns_match_orm():
    assert set(reference_source.c.keys()) == set(
        ReferenceSource.__table__.columns.keys()
    )


def test_compound_columns_match_orm():
    assert set(reference_compound.c.keys()) == set(
        ReferenceCompound.__table__.columns.keys()
    )
