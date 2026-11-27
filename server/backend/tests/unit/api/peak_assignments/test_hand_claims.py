"""The source a demoted isotopologue is put back with (``hand_claims``)."""

import pytest

from mascope_backend.api.new.peak_assignments.hand_claims import (
    ACTION_CLAIM_LINE,
    is_claimed_line,
    restored_source,
)


CLAIMED = {"manual": {"action": ACTION_CLAIM_LINE, "at": "2026-11-27T00:00:00"}}


@pytest.mark.parametrize("source", ["database", "untargeted"])
def test_an_engine_source_comes_back_as_it_was(source):
    assert restored_source(source, None) == source


def test_a_line_a_hand_m0_claimed_comes_back_manual():
    assert restored_source("manual", CLAIMED) == "manual"


@pytest.mark.parametrize(
    "provenance",
    [
        None,
        {},
        {"manual": {"action": "set_assignment"}},
        {"manual": "claim_isotope_line"},
        ["manual"],
    ],
)
def test_any_other_manual_row_comes_back_sourceless(provenance):
    """An archive is JSON an import could have written anything into."""
    assert restored_source("manual", provenance) is None
    assert not is_claimed_line(provenance)


def test_an_unknown_source_comes_back_sourceless_even_on_a_claimed_line():
    assert restored_source("imported", CLAIMED) is None
