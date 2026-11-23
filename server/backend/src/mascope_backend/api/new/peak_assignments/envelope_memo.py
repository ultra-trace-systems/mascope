"""Each ion's predicted envelope, predicted once while a memo is open (MAS-1172).

The tiering rules ask a committed row for the lines its formula predicts
(``tiering.predicted_envelope``): the expected-lines rule asks, the
envelope-neighbour rule asks again, the peak separation and the envelope claims
ask, and every round of a judging asks once more. The prediction reads nothing
but the ion, the envelope floor, the file's resolving power and the purity
labelled ions are read at, so inside an open memo each ion is predicted once at
those inputs and every later asker gets that prediction. Its arrays are
handed out read-only, so no asker changes what the next one reads.

A judging opens one memo for its stages (``pipeline.run_stages``). A batch pass
keeps one per sample for the whole pass, with the resolving power the sample's
envelopes are predicted at (``kept_measurement.KeptMeasurement``), so a later
judging of the sample predicts only the ions no earlier one did.

The resolving power is a function, told apart by identity: a memo hands an
envelope back only to an asker holding the very function it was predicted at.
It keeps that function alive while it holds the envelope, so no other
function can come to have its id.
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from mascope_tools.composition.custom_elements import CUSTOM_ELEMENTS, label_purity


#: The memo open in this context, if any. Carried into the worker threads the
#: stages run their judging on (``asyncio.to_thread`` copies the context).
_OPEN: ContextVar[EnvelopeMemo | None] = ContextVar("envelope_memo", default=None)


@dataclass
class EnvelopeMemo:
    """Envelopes predicted so far: by ion, floor, resolving power and purity,
    each with the resolving power it was predicted at."""

    envelopes: dict[tuple, tuple[Any, Any]] = field(default_factory=dict)
    #: How many envelopes were asked for, and how many of them predicted.
    asked: int = 0
    predicted: int = 0

    def clear(self) -> None:
        """Forget every envelope: the sample's resolving power was read again."""
        self.envelopes.clear()


@contextmanager
def open_memo(memo: EnvelopeMemo | None = None) -> Iterator[EnvelopeMemo]:
    """Predict each envelope once inside the block.

    :param memo: The memo to keep them in; None opens an empty one.
    :return: The open memo.
    """
    memo = EnvelopeMemo() if memo is None else memo
    token = _OPEN.set(memo)
    try:
        yield memo
    finally:
        _OPEN.reset(token)


def _frozen(envelope: tuple | None) -> tuple | None:
    """An envelope no asker can change: read-only arrays, a tuple of labels."""
    if envelope is None:
        return None
    mzs, shares, labels = envelope
    mzs, shares = np.array(mzs), np.array(shares)
    mzs.setflags(write=False)
    shares.setflags(write=False)
    return mzs, shares, tuple(labels)


def memoised(predict: Callable) -> Callable:
    """``tiering.predicted_envelope`` asking the open memo first, if one is.

    Outside a memo it predicts as it always did.
    """

    @functools.wraps(predict)
    def remembered(ion_formula, abundance_floor: float, resolution=None):
        memo = _OPEN.get()
        if memo is None:
            return predict(ion_formula, abundance_floor, resolution)
        key = (
            str(ion_formula or "").strip(),
            float(abundance_floor),
            id(resolution),
            tuple(label_purity(symbol) for symbol in sorted(CUSTOM_ELEMENTS)),
        )
        memo.asked += 1
        if key not in memo.envelopes:
            memo.predicted += 1
            envelope = _frozen(predict(ion_formula, abundance_floor, resolution))
            # The function is held with its envelopes: while it is alive, no
            # other one can take its id.
            memo.envelopes[key] = (resolution, envelope)
        return memo.envelopes[key][1]

    return remembered


__all__ = ["EnvelopeMemo", "memoised", "open_memo"]
