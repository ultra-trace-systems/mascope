"""Unit tests for the envelope memo (MAS-1172).

Inside an open memo each ion's envelope is predicted once at the same floor,
resolving power and labelled purity, and every later asker gets that
prediction, read-only; another floor, another resolving power or another
purity predicts again; outside a memo nothing is kept. The tiering rules ask
through it, and a judging's stage list opens one. See ``envelope_memo.py``.
"""

import asyncio

import numpy as np
import pytest

from mascope_backend.api.new.peak_assignments import envelope_memo, tiering
from mascope_backend.api.new.peak_assignments.envelope_memo import (
    EnvelopeMemo,
    open_memo,
)
from mascope_tools.composition.custom_elements import labelled_purity
from mascope_tools.composition.line_model import HeightResolution


ION = "C10H17O3+"
FLOOR = 0.01


def orbitrap(mz: float) -> float:
    return 120_000.0 * (200.0 / mz) ** 0.5


class Counting:
    """A predictor that counts its calls, answering as the real one does."""

    def __init__(self):
        self.calls: list[tuple] = []

    def __call__(self, ion_formula, abundance_floor, resolution=None):
        self.calls.append((ion_formula, abundance_floor, resolution))
        return tiering.predicted_envelope.__wrapped__(
            ion_formula, abundance_floor, resolution
        )


@pytest.fixture
def counted():
    predict = Counting()
    return predict, envelope_memo.memoised(predict)


def test_inside_a_memo_an_envelope_is_predicted_once(counted):
    predict, remembered = counted
    with open_memo() as memo:
        first = remembered(ION, FLOOR, orbitrap)
        again = remembered(ION, FLOOR, orbitrap)
    assert len(predict.calls) == 1
    assert again is first
    assert (memo.asked, memo.predicted) == (2, 1)


def test_the_memoised_envelope_is_the_prediction(counted):
    _, remembered = counted
    plain = tiering.predicted_envelope.__wrapped__(ION, FLOOR, orbitrap)
    with open_memo():
        mzs, shares, labels = remembered(ION, FLOOR, orbitrap)
    np.testing.assert_array_equal(mzs, plain[0])
    np.testing.assert_array_equal(shares, plain[1])
    assert list(labels) == list(plain[2])


def test_no_asker_can_change_what_the_next_reads(counted):
    _, remembered = counted
    with open_memo():
        mzs, shares, labels = remembered(ION, FLOOR, orbitrap)
        with pytest.raises(ValueError):
            mzs[0] = 0.0
        with pytest.raises(ValueError):
            shares[1] = 0.0
        assert isinstance(labels, tuple)


@pytest.mark.parametrize(
    "other",
    [
        lambda: ("C10H17O4+", FLOOR, orbitrap),
        lambda: (ION, 0.001, orbitrap),
        # Another function, even one that reads the same: compared by identity.
        lambda: (ION, FLOOR, HeightResolution(orbitrap)),
        lambda: (ION, FLOOR, None),
    ],
    ids=["ion", "floor", "resolving power", "no resolving power"],
)
def test_other_inputs_predict_again(counted, other):
    predict, remembered = counted
    with open_memo():
        remembered(ION, FLOOR, orbitrap)
        remembered(*other())
        remembered(ION, FLOOR, orbitrap)
    assert len(predict.calls) == 2


def test_another_labelled_purity_predicts_again(counted):
    predict, remembered = counted
    with open_memo():
        remembered("C9H16O7^N-", FLOOR, orbitrap)
        with labelled_purity(0.9):
            remembered("C9H16O7^N-", FLOOR, orbitrap)
        remembered("C9H16O7^N-", FLOOR, orbitrap)
    assert len(predict.calls) == 2


def test_an_ion_that_predicts_nothing_is_remembered_as_nothing(counted):
    predict, remembered = counted
    with open_memo():
        assert remembered("not an ion", FLOOR, orbitrap) is None
        assert remembered("not an ion", FLOOR, orbitrap) is None
    assert len(predict.calls) == 1


def test_outside_a_memo_nothing_is_kept(counted):
    predict, remembered = counted
    remembered(ION, FLOOR, orbitrap)
    remembered(ION, FLOOR, orbitrap)
    with open_memo():
        pass
    remembered(ION, FLOOR, orbitrap)
    assert len(predict.calls) == 3


def test_a_memo_handed_over_keeps_what_an_earlier_block_predicted(counted):
    """A batch pass keeps a sample's memo between its judgings."""
    predict, remembered = counted
    kept = EnvelopeMemo()
    with open_memo(kept):
        remembered(ION, FLOOR, orbitrap)
    with open_memo(kept):
        remembered(ION, FLOOR, orbitrap)
    with open_memo():
        remembered(ION, FLOOR, orbitrap)
    assert len(predict.calls) == 2
    kept.clear()
    with open_memo(kept):
        remembered(ION, FLOOR, orbitrap)
    assert len(predict.calls) == 3


@pytest.mark.asyncio
async def test_the_memo_reaches_a_worker_thread(counted):
    """The judging's rounds run off the event loop (``asyncio.to_thread``)."""
    predict, remembered = counted
    with open_memo():
        remembered(ION, FLOOR, orbitrap)
        await asyncio.to_thread(remembered, ION, FLOOR, orbitrap)
    assert len(predict.calls) == 1


def test_the_tiering_rules_ask_through_the_memo():
    """Both rules that ask a row for its lines, and every round, read one
    prediction per ion."""
    with open_memo() as memo:
        for _ in range(3):
            tiering.predicted_envelope(ION, FLOOR, orbitrap)
    assert (memo.asked, memo.predicted) == (3, 1)
