"""The sample view the migrations build is the one the models map.

``sample_view`` is written down three times: the SQL of the newest migration
that recreated it, ``Sample.create_view()``, and the columns of
``sample_view_table``. Nothing derives one from another. A deployment has the
first, the integration tests build theirs from the second, and every listing
of samples serialises the columns of the third. The drift test compares the
models' tables with the migrations' and does not look at a view, which lives
in a registry of its own.

So a column added to two of the three is invisible to every other test: the
integration suite passes on a view no deployment has, or a deployment answers
a listing with an error for a column its view does not select.

Run at head rather than at a revision, so it holds for the next migration
that changes the view as well.
"""

from alembic.command import upgrade
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from mascope_backend.db.views import Sample, sample_view_table


def _view_columns(conn: Connection) -> list[str]:
    """The view's columns, in the order it selects them."""
    return [
        row[0]
        for row in conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'sample_view' ORDER BY ordinal_position"
            )
        )
    ]


def test_the_migrated_view_is_the_view_the_models_map(
    seeded_alembic_config: Config, seeded_engine: Engine
):
    upgrade(seeded_alembic_config, "head")

    with seeded_engine.connect() as conn:
        migrated = _view_columns(conn)
        # DDL is transactional in Postgres: the models' view is built in
        # place of the migrated one, read, and rolled back.
        conn.execute(text(Sample.drop_view()))
        conn.execute(text(Sample.create_view()))
        from_models = _view_columns(conn)
        conn.rollback()

    mapped = [column.name for column in sample_view_table.columns]
    assert migrated, "the migrations must leave sample_view in place"
    assert migrated == from_models
    assert migrated == mapped
