"""When a raw Orbitrap file's first scan is left out of every selection.

Thermo raw files often open on a scan whose spectrum is scaled up as a whole:
every ion of it, and so its TIC, reads a fixed factor above the scans that
follow, while those agree with each other to a percent or two. Nothing about
the scan says so, and the factor is not one value. On one customer
instrument's 92,600 acquisitions, over six months and a dozen methods, it ran
from a few percent to 25 times, with a continuous tail below 5, the bound the
rule used to have: 494 first scans stood four to five times above the rest
and were kept, and showed in every peak timeseries of their file as a spike
on the first point. Some methods open nearly every file high, one by a median
of 21%, another by 89%.

The rule is in one place so that the two reader backends
(:mod:`mascope_thermo.thermo`, :class:`mascope_thermo.backend.OpenTFRawBackend`)
select the same scans. Both call :func:`is_outlier` with the scans the first
scan is compared with: the other scans of the stream asked for, or else those
of its MS order and polarity.

**Two tests, either of which leaves the scan out.**

- *Far above the median.* Its TIC is :data:`FACTOR` times the median of the
  scans it is compared with, or more. This is the rule as it always was, and
  it stays: a scan it left out before stays left out, so no store built
  without the first scan is made stale by this module.
- *Far above its own experiment.* Compared with the other scans recorded by
  the same experiment of the method (the same scan filter, method segment
  and scan event, as the stream keys tell experiments apart), its TIC stands
  above both their median and the very next of them by :data:`MIN_EXCESS` of
  that, and by :data:`SPREAD_FACTOR` of their robust spread (1.4826 times the
  median absolute deviation, relative to their median), whichever is more.

The second test is what reaches the tail below five. Each of its parts guards
an ordinary first scan:

- **The spread** lets an acquisition whose signal moves from scan to scan keep
  its first scan: the bar rises with the movement. A first scan ten robust
  standard deviations above its experiment's median is not a draw from it.
- **The next scan** keeps a signal that starts high and stays high for a
  while - an inlet that opens on a plateau, or a decay over the first
  minute. The quirk is a single scan: the one after it reads as the rest do.
- **The same experiment** keeps the first scan of a method that runs one
  experiment rarely among another of its polarity: compared with the other
  experiment's scans, whose TICs agree with each other, it would read as an
  outlier for measuring something else.
- **The floor** of :data:`MIN_EXCESS` keeps a first scan a little above an
  experiment whose scans agree to a fraction of a percent: there, ten
  standard deviations can be a few percent. It sits above the 5 to 40%
  excess of the methods that open every file a little high, where any bound
  would keep the first scan of one file and leave out that of the next.

On the acquisitions above, the first test left out 3,188 first scans and the
second 4,669 more, from 1.50 to 4.99 times the rest; 153 at 1.5 to 5 times
were kept, all of them among scans that move by ten or twenty percent.
Applied to each acquisition's second scan instead, against the scans after
it, as a control, the rule left out one of 92,620: a second scan 1.78 times
its neighbours, a spike of its own.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np


#: The first scan is left out when its TIC is this many times the median of
#: the scans it is compared with, or more, however they spread.
FACTOR = 5.0

#: Or when it stands this share above the scans of its own experiment, their
#: median and the next of them both, at the least...
MIN_EXCESS = 0.5

#: ...and this many of their robust standard deviations above, where that is
#: more.
SPREAD_FACTOR = 10.0

#: The fewest other scans of its experiment the second test reads a spread
#: from. With one, which of the two is the odd one cannot be told.
MIN_ALIKE = 2

#: The median absolute deviation of a normal distribution, in its standard
#: deviations.
_MAD_TO_SD = 1.4826


def is_outlier(tic: Sequence[float], alike: Sequence[bool]) -> bool:
    """Whether the first of these scans is to be left out.

    :param tic: The TIC of the scans the first scan is compared with, the
        first scan first and the rest in scan order: the other scans of the
        stream asked for, or else of its MS order and polarity.
    :param alike: One per scan of ``tic``: whether it was recorded by the
        experiment that recorded the first scan, as its scan filter, method
        segment and scan event say. The first scan's own is not read.
    :return: True when either test of the module finds the first scan an
        outlier; False when it has nothing to be compared with.
    :rtype: bool
    """
    values = np.asarray(tic, dtype=np.float64)
    # <= 1, not == 1: a file with no scans at all would otherwise index off
    # an empty array, raising IndexError from what is only meant to be an
    # outlier check - and masking the NoScansFoundError the selection raises
    # for exactly that case.
    if values.size <= 1:
        return False
    first = values[0]
    if first >= FACTOR * np.median(values[1:]):
        return True

    same = np.asarray(alike, dtype=bool)
    if same.shape != values.shape:
        raise ValueError(
            f"{same.size} experiment flags were given for {values.size} scans"
        )
    others = values[1:][same[1:]]
    if others.size < MIN_ALIKE:
        return False
    median = float(np.median(others))
    if not (np.isfinite(median) and median > 0):
        return False
    spread = _MAD_TO_SD * float(np.median(np.abs(others / median - 1.0)))
    reference = max(median, float(others[0]))
    return bool(first >= reference * (1.0 + max(MIN_EXCESS, SPREAD_FACTOR * spread)))
