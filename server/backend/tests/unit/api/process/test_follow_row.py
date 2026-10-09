"""
When a method binding moves to another mode row of its own chemistry.

The rule is pure and is applied twice - by live learning, one observation at
a time, and by the backfill, which folds a whole history oldest first - so it
is pinned here on its own rather than only through either caller
(``docs/dev/ingest_routing_and_splitting.md``, section 5.3).

What is being guarded is the asymmetry: one re-bound file must move nothing,
and a method whose files have genuinely moved must be followed within a few
of them.
"""

import pytest

from mascope_backend.api.controllers.sample.files.process.bindings import (
    REPOINT_AFTER,
    follow_row,
)


_HELD = "mode-held"
_OTHER = "mode-other"
_THIRD = "mode-third"


def _walk(current, observations):
    """Fold a sequence of observations through the rule, as a caller does.

    :return: The mode held after each observation, in order.
    """
    candidate, run, seen = None, 0, []
    for observed in observations:
        current, candidate, run = follow_row(current, candidate, run, observed)
        seen.append(current)
    return seen


def test_an_observation_naming_the_row_it_holds_changes_nothing():
    assert follow_row(_HELD, None, 0, _HELD) == (_HELD, None, 0)


def test_one_file_on_another_row_does_not_move_it():
    """The expensive error: a single corrected file dragging a whole method."""
    assert follow_row(_HELD, None, 0, _OTHER) == (_HELD, _OTHER, 1)


def test_it_moves_once_the_run_is_long_enough():
    held = _walk(_HELD, [_OTHER] * REPOINT_AFTER)

    assert held[:-1] == [_HELD] * (REPOINT_AFTER - 1)
    assert held[-1] == _OTHER


def test_the_run_counts_the_last_observations_not_every_disagreement():
    """A method alternating between two rows is not a method that moved."""
    held = _walk(_HELD, [_OTHER, _OTHER, _HELD, _OTHER, _OTHER, _HELD])

    assert held == [_HELD] * 6


def test_a_run_toward_one_row_is_not_credited_to_another():
    assert follow_row(_HELD, _OTHER, 2, _THIRD) == (_HELD, _THIRD, 1)


def test_settling_back_clears_the_run():
    assert follow_row(_HELD, _OTHER, 2, _HELD) == (_HELD, None, 0)


def test_a_row_pointing_nowhere_takes_the_first_mode_offered():
    """Its mode was deleted, so it routes nothing and drags nothing."""
    assert follow_row(None, None, 0, _OTHER) == (_OTHER, None, 0)


def test_a_row_pointing_nowhere_ignores_a_run_it_was_carrying():
    """The mode was deleted mid-run; the candidate is simply the answer now."""
    assert follow_row(None, _THIRD, 2, _OTHER) == (_OTHER, None, 0)


@pytest.mark.parametrize("threshold", [1, 2, 3, 5])
def test_the_threshold_is_the_number_of_agreeing_observations(monkeypatch, threshold):
    """Whatever the constant is set to, that many files move the binding.

    The threshold is meant to be moved on what the disagreement report shows,
    so the rule must hold for any value rather than only for three.
    """
    monkeypatch.setattr(
        "mascope_backend.api.controllers.sample.files.process.bindings.REPOINT_AFTER",
        threshold,
    )

    held = _walk(_HELD, [_OTHER] * (threshold + 1))

    assert held[: threshold - 1] == [_HELD] * (threshold - 1)
    assert held[threshold - 1 :] == [_OTHER, _OTHER]
