"""A shifted m/z axis: every peak read inside a block sees its m/z moved.

The backend's decoy runs re-run the peak assignment on a sample with its mass
axis moved off every true formula, by a few ppm or by a fraction of a dalton
(``docs/dev/assignment_quality_plan.md``, step 5.2). What the engine still
assigns there is what it would assign if its chemistry were wrong. For that to
hold, every stage has to read the moved axis: the peak table the run loads,
and the series the targeted matcher reads for each target off the file store.
The second read sits several calls deep under the first, so the shift is set
once around the run rather than handed down each of them.

Context-local, as ``mascope_tools.composition.custom_elements.labelled_purity``
is: concurrent runs each read their own axis, and ``asyncio.to_thread`` and the
tasks a block starts carry it. Outside a block every read is as stored.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class MzShift:
    """How far the axis is moved: scaled by ``ppm``, then offset by ``da``.

    :param ppm: The relative shift, in parts per million of each m/z.
    :param da: The absolute shift, in daltons (m/z units at charge one).
    """

    ppm: float = 0.0
    da: float = 0.0

    def apply(self, mz):
        """The m/z values on the moved axis."""
        return np.asarray(mz, dtype=float) * (1.0 + self.ppm * 1e-6) + self.da


_MZ_SHIFT: ContextVar[MzShift | None] = ContextVar("mz_shift", default=None)


@contextmanager
def shifted_mz_axis(
    ppm: float | None = None, da: float | None = None
) -> Iterator[None]:
    """Read every peak's m/z moved by ``ppm`` and ``da`` inside the block.

    Neither given, or both zero, the block reads the axis as stored, so a
    caller can enter it unconditionally.

    :param ppm: The relative shift in ppm, or None.
    :param da: The absolute shift in daltons, or None.
    """
    shift = MzShift(ppm=float(ppm or 0.0), da=float(da or 0.0))
    token = _MZ_SHIFT.set(shift if shift.ppm or shift.da else None)
    try:
        yield
    finally:
        _MZ_SHIFT.reset(token)


def current_mz_shift() -> MzShift | None:
    """The shift the current block reads at, or None outside one."""
    return _MZ_SHIFT.get()


def shift_mz(mz):
    """Peak m/z values as the current block reads them.

    :param mz: The stored m/z values.
    :return: The values moved by the block's shift, as a float array; the
        values themselves, untouched, outside a block.
    """
    shift = _MZ_SHIFT.get()
    return mz if shift is None else shift.apply(mz)
