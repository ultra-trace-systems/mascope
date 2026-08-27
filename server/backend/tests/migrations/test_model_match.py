"""
Drift detection: assert that the migration history produces a schema
identical to what the SQLAlchemy models describe.

Catches the common failure mode of editing a SQLAlchemy model without
creating the corresponding migration.

Fixtures `alembic_config` and `alembic_engine` are provided in
`conftest.py` and point pytest-alembic at the dedicated drift database
(`DRIFT_DB_NAME`, `mascope_test_<env>_migrations_drift`).

Re-exports pytest-alembic's `test_model_definitions_match_ddl`. See
`server/backend/tests/README.md` (Migration tests) for rationale and the relationship
to the stairway test.
"""

from pytest_alembic.tests import test_model_definitions_match_ddl


__all__ = ["test_model_definitions_match_ddl"]
