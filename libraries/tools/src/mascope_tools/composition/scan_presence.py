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
counts, the fit score reads them (step 3.5d):

- **A matched line is read over the scans that hold it**, against its anchor
  read over the scans that hold the anchor (:func:`presence_corrected`): with
  ``f`` a peak's presence fraction, ``n_scans / n_scans_total``, the share is
  ``(I_line / f_line) / (I_anchor / f_anchor)``. Where the anchor is in every
  scan, that is the line's mean over its own scans; where the compound is in
  part of the file only, both are read over their own. It is the targeted
  matcher's reading (``mascope_match.compute.isotopes.present_scan_ratios``,
  step 3.5c) from the counts, for a reader that has no per-scan series.
- **An absent line is charged where the list would have held it**
  (:func:`absent_line_detectable`). An anchor at an averaged S/N of ``S`` in
  ``n`` of the ``N`` scans averaged reads ``s = S * sqrt(N) / n`` in each scan
  that holds it: averaging ``N`` scans lowers the noise by ``sqrt(N)``, and the
  averaged peak carries ``n / N`` of the per-scan signal
  (``mascope_thermo.backend.OpenTFRawBackend.average_centroids`` scales the
  per-scan S/N by exactly that; the vendor library reads its S/N off the
  noise-reduced averaged spectrum). A line of share ``p`` then reads ``x ~
  N(p * s, 1)`` per scan, in units of the scan's noise, and a scan keeps it
  where ``x`` clears the floor ``F``. What the kept scans add to the averaged
  list at its m/z, beyond what the noise there would add with no line, is
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
  falls again as the scans grow. The curve's threshold is then the fallback,
  for a list without counts.

The floor is the S/N under which an Orbitrap scan writes no centroid:
:data:`SCAN_FLOOR_SNR`.

Known limit: the scans that keep a dim line are its upward fluctuations, so
the mean over them overshoots its share where the mean over every scan
undershoots it: by about a quarter at a per-scan S/N of 3 with half the scans
keeping the line, by a few percent when nine in ten do. The censoring-aware
estimate, which reads the share from how far above the floor the line's
presence fraction puts it, needs the per-scan noise of the line itself, which
the store does not keep.

References: Johnson, N. L., Kotz, S. & Balakrishnan, N., *Continuous
Univariate Distributions*, vol. 1, 2nd ed. (Wiley, 1994), ch. 13 (the
truncated normal's moments).
"""

from __future__ import annotations

import numpy as np
from scipy.special import ndtr


#: The per-scan S/N under which an Orbitrap scan writes no centroid. On the
#: published demo raws the centroids a scan writes reach down to an S/N of
#: about 2: over eight files of 4 to 20 scans, the 5th percentile of the
#: per-scan centroid S/N is 2.04 to 2.18 and the 1st 1.82 to 1.90.
SCAN_FLOOR_SNR = 2.0

_INV_SQRT_2PI = 1.0 / np.sqrt(2.0 * np.pi)


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


def presence_corrected(
    rel_obs: np.ndarray, presence: np.ndarray, anchor_presence: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Each line's share read over the scans that hold it, against its anchor
    read over the scans that hold the anchor.

    :param rel_obs: Each line's intensity over its anchor's, as the list
        reads them: over every scan averaged.
    :param presence: Each line's presence fraction
        (:func:`presence_fractions`).
    :param anchor_presence: Its anchor's, per line.
    :return: The shares, and which of them were read by presence: those whose
        line and anchor both have a fraction. The rest are as given.
    """
    rel_obs = np.asarray(rel_obs, dtype=float)
    read = np.isfinite(presence) & np.isfinite(anchor_presence)
    with np.errstate(divide="ignore", invalid="ignore"):
        corrected = rel_obs * anchor_presence / presence
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
