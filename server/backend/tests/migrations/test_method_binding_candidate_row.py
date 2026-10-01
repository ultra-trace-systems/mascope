"""Seeded test for `7a2d5c8e3b94`, the pending move on a method binding.

Two columns are added and nothing re-points on the migration itself, so what
there is to pin is the state every existing row lands in and what the new
reference costs.

A row learned before this must read as settled - no candidate, a run of zero
- because that is what a binding whose files all agree looks like, and it is
the state the next observation expects to extend. Reading NULL on the counter
instead would make every arithmetic on it a special case.

The candidate reference must behave like the mode the binding already holds:
deleting a mode is allowed and leaves the key's history intact, so a vanished
candidate is simply no candidate.
"""

from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic.command import downgrade, upgrade
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError


# This checkout's migrations, not MASCOPE_PATH's - see conftest.BACKEND_PATH.
_ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"

REVISION = "7a2d5c8e3b94"
_SCRIPT = ScriptDirectory.from_config(Config(str(_ALEMBIC_INI))).get_revision(REVISION)
PRIOR_REVISION = _SCRIPT.down_revision

_HELD_MODE = "im-cand-held0001"
_OTHER_MODE = "im-cand-othr0001"
_BINDING_ID = "mb-candidate0001"

_MODE_SQL = """
    INSERT INTO ionization_mode (ionization_mode_id, ionization_mode_name,
                                 ionization_mode_polarity,
                                 ionization_mechanism_ids)
    VALUES (:id, :name, '-', CAST('["mech-a"]' AS json))
"""

_BINDING_SQL = """
    INSERT INTO method_binding (
        method_binding_id, binding_key, instrument, method_key,
        signature_class, ionization_mode_id, chemistry_keys, state, source,
        first_seen, last_seen, n_streams, n_disagreements, last_chemistry_key
    ) VALUES (
        :id, 'candidate-key', 'instrument-A', 'nitrate.meth',
        'FTMS - p NSI Full ms', :mode, CAST('["mech-a"]' AS json), 'learned',
        'token', :seen, :seen, 4, 0, 'mech-a'
    )
"""


def _pending(engine: Engine) -> tuple[str | None, str | None, int]:
    """Where the binding points, what it is moving toward, and how far."""
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT ionization_mode_id, candidate_mode_id, "
                "n_candidate_streams FROM method_binding "
                "WHERE method_binding_id = :id"
            ),
            {"id": _BINDING_ID},
        ).one()
    return row.ionization_mode_id, row.candidate_mode_id, row.n_candidate_streams


@pytest.fixture(scope="module")
def upgraded(seeded_alembic_config: Config, seeded_engine: Engine) -> Engine:
    """A binding learned before the columns existed, then the migration."""
    upgrade(seeded_alembic_config, PRIOR_REVISION)
    with seeded_engine.begin() as conn:
        for mode_id, name in (
            (_HELD_MODE, "Nitrate candidate held"),
            (_OTHER_MODE, "Nitrate candidate other"),
        ):
            conn.execute(text(_MODE_SQL), {"id": mode_id, "name": name})
        conn.execute(
            text(_BINDING_SQL),
            {
                "id": _BINDING_ID,
                "mode": _HELD_MODE,
                "seen": datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc),
            },
        )
    upgrade(seeded_alembic_config, REVISION)
    return seeded_engine


def test_a_binding_learned_before_the_columns_reads_as_settled(upgraded: Engine):
    assert _pending(upgraded) == (_HELD_MODE, None, 0)


def test_a_binding_can_record_a_pending_move(upgraded: Engine):
    with upgraded.begin() as conn:
        conn.execute(
            text(
                "UPDATE method_binding SET candidate_mode_id = :other, "
                "n_candidate_streams = 2 WHERE method_binding_id = :id"
            ),
            {"other": _OTHER_MODE, "id": _BINDING_ID},
        )

    assert _pending(upgraded) == (_HELD_MODE, _OTHER_MODE, 2)


def test_deleting_the_candidate_mode_leaves_the_binding(upgraded: Engine):
    """The history in chemistry_keys is still worth keeping; the move is not."""
    with upgraded.begin() as conn:
        conn.execute(
            text("DELETE FROM ionization_mode WHERE ionization_mode_id = :id"),
            {"id": _OTHER_MODE},
        )

    held, candidate, run = _pending(upgraded)
    assert (held, candidate) == (_HELD_MODE, None)
    # The counter is left as it was: SET NULL clears the reference and nothing
    # else, so the next observation naming another row starts a fresh run and
    # a stale count cannot survive past it.
    assert run == 2


def test_the_counter_refuses_to_be_null(upgraded: Engine):
    """It is counted with, never checked for; NULL would be a special case."""
    with pytest.raises(IntegrityError):
        with upgraded.begin() as conn:
            conn.execute(
                text(
                    "UPDATE method_binding SET n_candidate_streams = NULL "
                    "WHERE method_binding_id = :id"
                ),
                {"id": _BINDING_ID},
            )


def test_the_downgrade_removes_both_columns(
    upgraded: Engine, seeded_alembic_config: Config
):
    # Runs last: it leaves the database at PRIOR_REVISION.
    downgrade(seeded_alembic_config, PRIOR_REVISION)
    with upgraded.connect() as conn:
        present = (
            conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'method_binding' AND column_name IN "
                    "('candidate_mode_id', 'n_candidate_streams')"
                )
            )
            .scalars()
            .all()
        )
    assert present == []
