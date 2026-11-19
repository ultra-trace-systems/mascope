"""Robust estimates shared by the composition library and the engine.

:func:`robust_spread` is the scaled median absolute deviation: 1.4826 times
the median absolute deviation estimates a normal distribution's standard
deviation, and one stray reading moves it far less than it moves the
standard deviation itself (Rousseeuw and Croux, "Alternatives to the median
absolute deviation", J. Am. Stat. Assoc. 88 (1993) 1273-1283).
"""

from __future__ import annotations

from typing import Optional

import numpy as np
from numpy.typing import ArrayLike


#: 1.4826 x MAD estimates a normal standard deviation (1 / Phi^-1(3/4)).
MAD_TO_SIGMA = 1.4826


def robust_spread(values: ArrayLike, centre: Optional[float] = None) -> float:
    """The scaled median absolute deviation of ``values`` about ``centre``.

    :param values: The readings; every one is read, so leave out None and
        non-finite values first.
    :param centre: The point the deviations are taken from; the median of
        ``values`` when None.
    :return: ``MAD_TO_SIGMA`` times the median absolute deviation.
    """
    data = np.asarray(values, dtype=float)
    if centre is None:
        centre = float(np.median(data))
    return float(MAD_TO_SIGMA * np.median(np.abs(data - centre)))


__all__ = ["MAD_TO_SIGMA", "robust_spread"]
