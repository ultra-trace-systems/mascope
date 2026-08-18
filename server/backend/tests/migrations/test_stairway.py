"""
Stairway test for Alembic migrations.

For every migration in the chain, the test runs the sequence:
    upgrade(rev) -> downgrade(prev) -> upgrade(rev)

This catches issues that a single `upgrade head` does not:

- Missing or broken `downgrade()` implementations
- Orphaned types/enums/sequences created by `upgrade()` but not cleaned
  up by `downgrade()` (Alembic auto-creates ENUMs but does not auto-drop
  them — a common silent bug)
- Typos or broken SQL in either direction
- Schema objects left behind after rollback that prevent the second
  upgrade from succeeding

Behavior notes:

- Revisions are walked oldest-first via `ScriptDirectory.walk_revisions`
  reversed. Pytest preserves parametrize order within a single test
  function, so state accumulates: after the parametrize step for
  revision N, the database is at revision N, and the next step starts
  there. This is O(n) total upgrades vs O(n^2) for a fresh-DB-per-step
  approach.
- A failure at revision N will likely cascade to N+1, N+2, etc. — that's
  acceptable. The first failure in the report identifies the broken
  migration; later failures are noise.
- Linear chain only. If branched migrations are introduced, the
  iteration order may interleave branches and merge revisions appear;
  the test will fail explicitly on a merge revision (multiple parents)
  rather than silently misbehave.

Reference: https://github.com/alvassin/alembic-quickstart
"""

from pathlib import Path

import pytest
from alembic.command import downgrade, upgrade
from alembic.config import Config
from alembic.script import Script, ScriptDirectory


# This checkout's migrations, not MASCOPE_PATH's - see conftest.BACKEND_PATH.
_ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"


def _walk_revisions_oldest_first() -> list[Script]:
    """Discover all revisions, ordered base -> heads.

    Run at module import time (before pytest collection) so that
    `pytest.mark.parametrize` can consume the list. Reads only the script
    dir — no database connection required.
    """
    cfg = Config(str(_ALEMBIC_INI))
    script = ScriptDirectory.from_config(cfg)
    return list(reversed(list(script.walk_revisions("base", "heads"))))


REVISIONS = _walk_revisions_oldest_first()


@pytest.mark.parametrize(
    "revision",
    [pytest.param(rev, id=rev.revision) for rev in REVISIONS],
)
def test_stairway(stairway_alembic_config: Config, revision: Script) -> None:
    """Apply, rollback, and re-apply a single migration.

    The previous parametrize step left the database at `revision.down_revision`
    (or empty for the first revision). This step:

    1. Upgrades to `revision` — exercises the `upgrade()` function.
    2. Downgrades back to the parent — exercises the `downgrade()` function
       and verifies it reverts everything `upgrade()` created (tables,
       columns, types, indexes, constraints).
    3. Upgrades to `revision` again — verifies the upgrade is idempotent
       and that the rollback didn't leave behind state that would conflict
       with re-applying.

    On success, the database is left at `revision`, ready for the next
    parametrize step.
    """
    # `Script.down_revision` is `str | tuple[str, ...] | None` — the tuple
    # variant occurs for merge revisions (multiple parents). Mascope is on
    # a linear chain, but if a merge is ever introduced, fail explicitly
    # rather than silently feed a tuple to `downgrade()`.
    parent = revision.down_revision
    if isinstance(parent, (tuple, list)):
        raise AssertionError(
            f"Stairway does not support merge revisions; "
            f"{revision.revision} has multiple parents: {parent}"
        )
    down = parent or "-1"

    upgrade(stairway_alembic_config, revision.revision)
    downgrade(stairway_alembic_config, down)
    upgrade(stairway_alembic_config, revision.revision)
