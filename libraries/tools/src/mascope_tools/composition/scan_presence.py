"""How many of its scans hold an Orbitrap line, and what that says of the line.

An Orbitrap scan keeps a centroid only where it clears that scan's noise, and a
peak list averaged over the scans divides each peak's sum by all of them, so a
dim isotope line missing from part of the scans is read diluted by the share it
missed. Measured on the gate's committed 13C lines at an expected S/N of 10 to
50 (design note, step 5.3): 0.94 of the share with no scan missed, 0.88 with up
to a fifth, 0.72 with a fifth to a half, 0.44 with more. The fit score's
response curve models that loss per file (:mod:`envelope_tolerance`, decision
32). A raw Orbitrap file's peak store counts it instead: per peak, the scans
holding a centroid within half its width (``n_scans``) and the scans averaged
(``n_scans_total``; ``mascope_signal.peak.N_SCANS``). Where a list carries the
counts, the fit score reads a matched line by them (step 3.5d):

- **A matched line is read at its mean per scan**, against its anchor read
  the same way (:func:`scan_levels`, :func:`presence_corrected`). A peak at an
  averaged S/N of ``S`` in ``n`` of the ``N`` scans averaged reads ``xbar = S
  * sqrt(N) / n`` in each scan that holds it: averaging ``N`` scans lowers the
  noise by ``sqrt(N)``, and the averaged peak carries ``n / N`` of the
  per-scan signal (``mascope_thermo.backend.OpenTFRawBackend.average_centroids``
  scales the per-scan S/N by exactly that; the vendor library reads its S/N
  off the noise-reduced averaged spectrum). A scan reads the line at ``x ~
  N(mu, 1)``, in units of its noise, and keeps it where ``x`` clears the
  floor ``F``, so the scans that keep a dim line are its upward
  fluctuations: their mean ``xbar`` overshoots ``mu``, as the mean over every
  scan, the averaged list's, undershoots it. ``mu`` is read as the
  censored-normal (Tobit) maximum-likelihood estimate with known noise
  (Tobin 1958; Amemiya 1984), the root of ``n (xbar - mu) = (N - n)
  phi(F - mu) / Phi(F - mu)``: the kept scans' excess over ``mu`` balanced
  against the missed scans' expected shortfall. The log-likelihood is
  concave in ``mu``, a sum of the logarithms of normal densities and
  distribution functions, so the root is unique, and Newton's method from
  ``xbar`` reaches it from one side. The line's intensity ``I`` then reads
  ``I * mu * sqrt(N) / S`` per scan, which is ``I`` where every scan holds
  the line, and its share is that over its anchor's read alike. Under the
  floor model the estimate reads a line's share at 0.99 to 1.00 of it,
  median over simulated lines, from 20 scans up at every presence, and at
  0.99 to 1.08 on four scans; the mean over the kept scans reads 1.40 at
  half presence and 1.76 at 0.31, and the mean over every scan 0.70 and
  0.54.
- **An absent line could be charged where the list would have held it**
  (:func:`absent_line_detectable`), which the fit score does not do yet. An
  anchor at an averaged S/N of ``S`` in ``n`` of the ``N`` scans reads ``s =
  S * sqrt(N) / n`` in each scan that holds it, as above, and a line of
  share ``p`` then reads ``x ~ N(p * s, 1)`` per scan, kept where ``x``
  clears the floor ``F``. What the kept scans add to the averaged list at
  its m/z, beyond what the noise there would add with no line, is
  ``(E[x; x >= F] - E[z; z >= F]) * n / sqrt(N)`` with ``z ~ N(0, 1)``:
  ``(p s Phi(p s - F) + phi(p s - F) - phi(F)) * n / sqrt(N)`` for a Gaussian
  (the first moment of a truncated normal; Johnson, Kotz & Balakrishnan
  1994). The line is charged where that is at least the threshold the list's
  own cut sets (``k_detect``, 3). With no floor this is today's rule, ``p * S
  >= 3``; with one, a line under it counts as far as its upward fluctuations
  still reach the list. At 3, an anchor in every scan puts the threshold at an
  expected S/N of 3.2 on a file of one scan, 4.2 of 4, 5.3 of 10, 9.2 of 100
  and 14.4 of 1000, rising with the scans as the dilution outgrows what the
  fluctuations recover; the noise's own share is taken off so that it never
  falls again as the scans grow. As a hard gate it charges the full miss
  penalty on lines the list holds about half the time, and on the demo
  bundle it lowered top-1: 0.8461 with the matched-line reading alone and
  0.8447 with both at a floor of 2, 0.8481 and 0.8443 at 2.8. So the fit
  score gates an absent line at the curve's threshold, counts or not, until
  a soft gate charges it by the chance the list would have held it
  (mascope#3123).

The floor is the S/N under which an Orbitrap scan writes no centroid:
:data:`SCAN_FLOOR_SNR`.

The estimate is the floor model's, and three things it does not model move
it:

- **The floor.** :data:`SCAN_FLOOR_SNR` is the demo raws', one number for
  every file. Read at a floor of 2 on a file whose scans keep lines from 3,
  a line kept by 30% of the scans reads 0.77 of its share and by half 0.86;
  on one whose scans keep them from 1, 2.06 and 1.36. A floor read per
  file is mascope#3111.
- **The averaged S/N's law.** ``xbar`` is read through ``S * sqrt(N) / n``.
  On the published demo raws (4 to 176 scans, the median per bin of
  presence) ``S`` is 1.00 to 1.04 times the measured kept mean times ``n /
  sqrt(N)`` with OpenTFRaw and 0.90 to 0.96 with the vendor library. An S/N
  10% low reads a line's share 2% high at a presence of 0.31 and 1% at
  0.69; one 4% high reads it under 1% low.
- **An analyte that varies within the averaged window.** Its dim line's kept
  scans are also its high-concentration scans, a bias the noise-only model
  does not cover: it reads such a line high, and a compound present in only
  part of the file is read as if its absent scans were censored. The
  targeted matcher's paired per-scan ratio (step 3.5c) is robust to both;
  the count is not.

Where the estimate cannot be read, for a line some scans missed that has no
S/N or that the estimate puts at or below the noise, the line keeps the
list's reading, as a list without counts does.

References: Johnson, N. L., Kotz, S. & Balakrishnan, N., *Continuous
Univariate Distributions*, vol. 1, 2nd ed. (Wiley, 1994), ch. 13 (the
truncated normal's moments); Tobin, J., "Estimation of relationships for
limited dependent variables", *Econometrica* 26, 24-36 (1958); Amemiya, T.,
"Tobit models: a survey", *Journal of Econometrics* 24, 3-61 (1984) (the
censored normal's likelihood).
"""

from __future__ import annotations

import numpy as np
from scipy.special import erfcx, ndtr


#: The per-scan S/N under which an Orbitrap scan writes no centroid. On the
#: published demo raws the centroids a scan writes reach down to an S/N of
#: about 2: over eight files of 4 to 20 scans, the 5th percentile of the
#: per-scan centroid S/N is 2.04 to 2.18 and the 1st 1.82 to 1.90.
SCAN_FLOOR_SNR = 2.0

_INV_SQRT_2PI = 1.0 / np.sqrt(2.0 * np.pi)
_SQRT_2_OVER_PI = np.sqrt(2.0 / np.pi)

#: Newton's steps :func:`censored_scan_mean` takes at most: from 2 to 10,000
#: scans averaged and a kept mean of 2 to 1,000 it needs 9 or fewer where the
#: estimate is above the noise, and 13 where it is not.
_NEWTON_STEPS = 50
_NEWTON_TOLERANCE = 1e-12


def presence_fractions(n_scans, n_scans_total, size: int | None = None) -> np.ndarray:
    """Each line's presence fraction: the scans holding it over the scans
    averaged.

    :param n_scans: Per line, the scans holding its peak, or None.
    :param n_scans_total: Per line, the scans averaged, or None.
    :param size: How many lines, for a caller that passes None.
    :return: Per line ``n_scans / n_scans_total``, NaN where either is
        missing or not a count: a count that is not finite and positive, or
        above the scans averaged, says nothing of the line.
    """
    if n_scans is None or n_scans_total is None:
        return np.full(0 if size is None else size, np.nan)
    present = np.asarray(n_scans, dtype=float)
    total = np.broadcast_to(np.asarray(n_scans_total, dtype=float), present.shape)
    usable = np.isfinite(present) & np.isfinite(total) & (present > 0)
    usable &= present <= total
    return np.where(usable, present / np.where(usable, total, 1.0), np.nan)


def scan_levels(
    snr,
    n_scans,
    n_scans_total,
    size: int | None = None,
    floor: float = SCAN_FLOOR_SNR,
) -> np.ndarray:
    """Per line, the factor that turns its intensity in the averaged list into
    its mean per scan, the scans that missed it under their floor counted.

    :param snr: Per line, its averaged S/N, or None.
    :param n_scans: Per line, the scans holding its peak, or None.
    :param n_scans_total: Per line, the scans averaged, or None.
    :param size: How many lines, for a caller that passes None.
    :param floor: The per-scan S/N under which a scan writes no centroid.
    :return: Per line ``mu * sqrt(N) / S``, ``mu`` the line's censored-normal
        estimate (:func:`censored_scan_mean`); exactly 1 where every scan
        holds the line, whatever its S/N. NaN where it cannot be read: a
        count missing (:func:`presence_fractions`), a line some scans missed
        with no S/N above 0, or one the estimate puts at or below the noise.
    """
    presence = presence_fractions(n_scans, n_scans_total, size)
    levels = np.where(presence == 1.0, 1.0, np.nan)
    if snr is None:
        return levels
    snr = np.broadcast_to(np.asarray(snr, dtype=float), presence.shape)
    read = (presence < 1.0) & np.isfinite(snr) & (snr > 0)
    if not read.any():
        return levels
    present = np.broadcast_to(np.asarray(n_scans, dtype=float), presence.shape)[read]
    total = np.broadcast_to(np.asarray(n_scans_total, dtype=float), presence.shape)
    root = np.sqrt(total[read])
    snr = snr[read]
    # A kept scan reads the line at S sqrt(N) / n on average, the averaging's
    # own law (the module's docstring)
    mean = censored_scan_mean(snr * root / present, present, total[read], floor)
    levels[read] = np.where(mean > 0, mean * root / snr, np.nan)
    return levels


def censored_scan_mean(
    kept_mean, n_scans, n_scans_total, floor: float = SCAN_FLOOR_SNR
) -> np.ndarray:
    """Each line's per-scan S/N over every scan averaged, from the scans that
    kept it: the censored-normal maximum-likelihood estimate with unit noise.

    :param kept_mean: Per line, its mean per-scan S/N over the scans that
        kept it.
    :param n_scans: Per line, those scans; at least one.
    :param n_scans_total: Per line, the scans averaged; at least
        ``n_scans``.
    :param floor: The per-scan S/N under which a scan writes no centroid.
    :return: Per line the root ``mu`` of ``n (kept_mean - mu) = (N - n)
        phi(floor - mu) / Phi(floor - mu)``, ``kept_mean`` where ``n = N``.
    """
    kept_mean = np.asarray(kept_mean, dtype=float)
    kept = np.asarray(n_scans, dtype=float)
    missed = np.asarray(n_scans_total, dtype=float) - kept
    # The score falls by between n and N per unit of mu and is concave in it,
    # so Newton's steps from kept_mean, where it is at most 0, fall onto the
    # root from above without passing it. Each line stops on its own step, so
    # its estimate does not depend on the lines it is read beside.
    mean = kept_mean.copy()
    moving = np.ones(mean.shape, dtype=bool)
    for _ in range(_NEWTON_STEPS):
        below = floor - mean
        mills = _mills_ratio(below)
        score = kept * (kept_mean - mean) - missed * mills
        slope = -kept - missed * mills * (below + mills)
        step = np.where(moving, score / slope, 0.0)
        mean = mean - step
        moving &= np.abs(step) > _NEWTON_TOLERANCE * np.maximum(1.0, np.abs(mean))
        if not moving.any():
            break
    return mean


def _mills_ratio(x) -> np.ndarray:
    """``phi(x) / Phi(x)``, the standard normal's density over its
    distribution function, without the cancellation their quotient has far
    below 0: ``sqrt(2 / pi) / erfcx(-x / sqrt(2))``."""
    return _SQRT_2_OVER_PI / erfcx(-np.asarray(x, dtype=float) / np.sqrt(2.0))


def presence_corrected(
    rel_obs: np.ndarray, levels: np.ndarray, anchor_levels: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Each line's share read at its mean per scan, against its anchor read at
    the anchor's.

    :param rel_obs: Each line's intensity over its anchor's, as the list
        reads them: over every scan averaged.
    :param levels: Each line's factor (:func:`scan_levels`).
    :param anchor_levels: Its anchor's, per line.
    :return: The shares, and which of them were read by presence: those whose
        line and anchor both have a factor. The rest are as given.
    """
    rel_obs = np.asarray(rel_obs, dtype=float)
    read = np.isfinite(levels) & np.isfinite(anchor_levels)
    with np.errstate(divide="ignore", invalid="ignore"):
        corrected = rel_obs * levels / anchor_levels
    return np.where(read, corrected, rel_obs), read


def expected_listed_snr(
    expected_snr: np.ndarray,
    anchor_scans: np.ndarray,
    scans_total: np.ndarray,
    floor: float = SCAN_FLOOR_SNR,
) -> np.ndarray:
    """The S/N a line is expected to reach in the averaged list, the scans that
    miss it under their floor counted.

    :param expected_snr: Each line's expected S/N in the averaged list had
        every scan kept it: its share times the anchor's averaged S/N.
    :param anchor_scans: Per line, the scans holding its anchor.
    :param scans_total: Per line, the scans averaged.
    :param floor: The per-scan S/N under which a scan writes no centroid.
    :return: Per line ``(E[x; x >= floor] - E[z; z >= floor]) * n / sqrt(N)``
        with ``x`` the line's per-scan reading in the anchor's scans and ``z``
        the noise's alone; NaN where a count is missing.
    """
    expected_snr = np.asarray(expected_snr, dtype=float)
    n = np.asarray(anchor_scans, dtype=float)
    total = np.asarray(scans_total, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        dilution = n / np.sqrt(total)
        per_scan = expected_snr / dilution
    margin = per_scan - floor
    kept = per_scan * ndtr(margin) + _density(margin) - _density(-floor)
    return kept * dilution


def _density(x) -> np.ndarray:
    """The standard normal density."""
    return _INV_SQRT_2PI * np.exp(-0.5 * np.asarray(x, dtype=float) ** 2)


def absent_line_detectable(
    expected_snr: np.ndarray,
    anchor_scans: np.ndarray,
    scans_total: np.ndarray,
    k_detect: float,
    floor: float = SCAN_FLOOR_SNR,
) -> np.ndarray:
    """Whether the averaged list would have held each absent line.

    :param expected_snr: Each line's share times its anchor's averaged S/N.
    :param anchor_scans: Per line, the scans holding its anchor.
    :param scans_total: Per line, the scans averaged.
    :param k_detect: The S/N from which the list holds a line.
    :param floor: The per-scan S/N under which a scan writes no centroid.
    :return: Per line, whether :func:`expected_listed_snr` reaches
        ``k_detect``; False where it cannot be read.
    """
    listed = expected_listed_snr(expected_snr, anchor_scans, scans_total, floor)
    return np.isfinite(listed) & (listed >= k_detect)
