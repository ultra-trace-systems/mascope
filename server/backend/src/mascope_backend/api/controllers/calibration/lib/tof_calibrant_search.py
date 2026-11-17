"""
Find a TOF file's calibrants by a consensus in TOF space.

The TOF fit is absolute: it fits each calibrant peak's TOF index ``i`` to its
calibrant's exact mass, ``i = p1 * sqrt(m) + p2`` (Tofwerk's mode 0). The
file's own m/z axis, the acquisition software's until a fit replaces it, is
used for one thing only: finding the calibrants. Matched within a window of
where that axis puts each line, they are lost as soon as the axis is further
off than the window, and peaks beside them that happen to agree are fitted
instead (GitHub #2776).

So the calibrants are found by the axis on which the most of them agree,
whatever the file's axis says:

1. Each calibrant line has candidates: the most intense peaks within a wide
   search window of where the file's axis puts it.
2. Every two candidates of two different ions make a hypothesis: the mode-0
   axis through both. One whose slope p1 is far from the file's own is
   dropped; two candidates of nearby lines can make an axis of any slope.
3. A hypothesis is scored by how many ions have a candidate within the
   residual tolerance of it, after it is refitted by least squares to the
   lines it holds: the axis through two candidates is exact at them and
   only as good as they are. The fit's own selection then decides which of
   the lines the axis holds the fit keeps, as it always has. Axes that find as many ions are told apart by how many ions
   they find within a tighter tolerance, then by the intensity of the lines
   they found.
4. Each line takes its most intense candidate within the tolerance of the
   winning axis.

A hypothesis need not include the reagent ion. The collection marks none,
and agreement is the evidence: four or five ions within 15 ppm of one axis do
not line up by chance, while a reagent ion whose own line sits further than
the tolerance from the axis the others agree on is one the fit would drop
anyway.

This is a RANSAC search ([Fischler and Bolles, 1981](https://doi.org/10.1145/358669.358692)),
exhaustive rather than random: a TOF collection offers a handful of lines and a
few candidates each, so every pair is tried.
"""

from dataclasses import dataclass

import numpy as np


#: Hypotheses scored at once: each is an array over every candidate.
_CHUNK = 4096


@dataclass(frozen=True)
class Candidates:
    """The peaks a fit may take for each calibrant line.

    ``L`` lines and at most ``K`` candidates each; a line with fewer has its
    remaining places padded (``peak`` -1, ``tof`` NaN).
    """

    #: Each line's exact m/z, (L,).
    exact_mz: np.ndarray
    #: Each line's ion, as an index, (L,): lines of one ion count once.
    ion: np.ndarray
    #: Each candidate's TOF index, (L, K).
    tof: np.ndarray
    #: Each candidate's intensity, (L, K).
    intensity: np.ndarray
    #: Each candidate's peak, as an index into the peaks searched, (L, K).
    peak: np.ndarray


@dataclass(frozen=True)
class Consensus:
    """The axis most calibrant ions agree on, and the peak each line takes."""

    #: The mode-0 axis, ``i = p1 * sqrt(m) + p2``.
    p1: float
    p2: float
    #: Each line's peak, as an index into the peaks searched, or -1 where
    #: no candidate of the line is within the tolerance of the axis, (L,).
    peak: np.ndarray
    #: How many lines, and how many ions, found a peak.
    n_lines: int
    n_ions: int


def search(
    candidates: Candidates,
    reference_p1: float,
    tolerance_ppm: float,
    tight_ppm: float,
    p1_prior: float,
) -> Consensus | None:
    """The axis on which the most calibrant ions find a peak.

    :param candidates: Each line's candidates.
    :param reference_p1: The slope p1 of the file's own axis.
    :param tolerance_ppm: How far a candidate may sit from an axis and still
        count, in ppm of its line's exact m/z: the fit's residual tolerance.
    :param tight_ppm: The tighter tolerance that tells apart axes finding as
        many ions.
    :param p1_prior: The share of ``reference_p1`` a hypothesis' slope may
        differ from it by.
    :return: The winning axis and the peak each line takes from it; None
        when no two candidates of two ions make a hypothesis within the
        prior.
    """
    n_lines, n_candidates = candidates.tof.shape
    flat = np.flatnonzero(np.isfinite(candidates.tof.ravel()))
    line = flat // n_candidates
    x = np.sqrt(candidates.exact_mz)[line]
    y = candidates.tof.ravel()[flat]
    ion = candidates.ion[line]

    first, second = np.triu_indices(flat.size, k=1)
    # Lines are told apart by their m/z, so two of two ions never share one
    pair = ion[first] != ion[second]
    first, second = first[pair], second[pair]
    p1 = (y[first] - y[second]) / (x[first] - x[second])
    p2 = y[first] - p1 * x[first]
    plausible = np.abs(p1 / reference_p1 - 1.0) <= p1_prior
    p1, p2 = p1[plausible], p2[plausible]
    if p1.size == 0:
        return None

    best = None
    for start in range(0, p1.size, _CHUNK):
        chunk = _score(
            candidates,
            p1[start : start + _CHUNK],
            p2[start : start + _CHUNK],
            tolerance_ppm,
            tight_ppm,
        )
        if best is None or chunk[0] > best[0]:
            best = chunk
    _, (axis_p1, axis_p2), chosen = best
    found = chosen >= 0
    peak = np.where(
        found,
        candidates.peak[np.arange(n_lines), np.where(found, chosen, 0)],
        -1,
    )
    return Consensus(
        p1=float(axis_p1),
        p2=float(axis_p2),
        peak=peak,
        n_lines=int(found.sum()),
        n_ions=int(np.unique(candidates.ion[found]).size),
    )


def _score(
    candidates: Candidates,
    p1: np.ndarray,
    p2: np.ndarray,
    tolerance_ppm: float,
    tight_ppm: float,
) -> tuple[tuple, tuple[float, float], np.ndarray]:
    """The best of some hypotheses, refitted to the lines each finds.

    :return: The best one's score, its refitted axis, and the column of the
        candidate each line takes under it, -1 for none.
    """
    _, chosen = _within(candidates, p1, p2, tolerance_ppm)
    p1, p2 = _refit(candidates, p1, p2, chosen)
    error, chosen = _within(candidates, p1, p2, tolerance_ppm)
    tight, _ = _within(candidates, p1, p2, tight_ppm)
    found = chosen >= 0

    n_ions = _ions(candidates, found)
    n_tight = _ions(candidates, np.isfinite(tight).any(axis=2))
    rows = np.arange(p1.size)[:, None]
    lines = np.arange(found.shape[1])[None, :]
    taken = np.where(found, chosen, 0)
    intensity = np.where(
        found, np.nan_to_num(candidates.intensity[lines, taken], nan=0.0), 0.0
    ).sum(axis=1)
    residual = np.where(found, np.abs(error[rows, lines, taken]), 0.0).sum(
        axis=1
    ) / np.maximum(found.sum(axis=1), 1)

    # More ions, then more ions held tight, then more intensity, then the
    # smaller mean residual; among the rest, the first one met
    order = np.lexsort((residual, -intensity, -n_tight, -n_ions))
    winner = int(order[0])
    score = (
        int(n_ions[winner]),
        int(n_tight[winner]),
        float(intensity[winner]),
        -float(residual[winner]),
    )
    return score, (p1[winner], p2[winner]), chosen[winner]


def _within(
    candidates: Candidates, p1: np.ndarray, p2: np.ndarray, tolerance_ppm: float
) -> tuple[np.ndarray, np.ndarray]:
    """Each candidate's error under each axis, and each line's pick.

    :return: The error in ppm of each candidate under each axis, NaN where it
        is outside the tolerance, (H, L, K); and the column of the most
        intense candidate within it on each line, -1 for none, (H, L).
    """
    tof = candidates.tof[None, :, :]
    flight = (tof - p2[:, None, None]) / p1[:, None, None]
    with np.errstate(invalid="ignore"):
        mz = flight**2
        error = 1e6 * (mz / candidates.exact_mz[None, :, None] - 1.0)
        # Mode 0 squares the flight time: a peak before the offset p2 would
        # read a mirrored mass, which is no reading
        within = (np.abs(error) <= tolerance_ppm) & (flight > 0)
    intensity = np.nan_to_num(candidates.intensity, nan=0.0)
    ranked = np.where(within, intensity[None, :, :], -np.inf)
    chosen = np.where(within.any(axis=2), np.argmax(ranked, axis=2), -1)
    return np.where(within, error, np.nan), chosen


def _refit(
    candidates: Candidates, p1: np.ndarray, p2: np.ndarray, chosen: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Each axis refitted by least squares to the lines it found.

    The axis through two candidates passes exactly through them, so a third
    line's error under it is the error of those two as much as its own. An
    axis with fewer than two lines to refit to, which only a degenerate
    hypothesis has, is kept as it was.
    """
    found = chosen >= 0
    lines = np.arange(found.shape[1])[None, :]
    weight = found.astype(np.float64)
    x = np.sqrt(candidates.exact_mz)[None, :]
    y = np.where(found, candidates.tof[lines, np.where(found, chosen, 0)], 0.0)
    sw = weight.sum(axis=1)
    sx = (weight * x).sum(axis=1)
    sy = (weight * y).sum(axis=1)
    sxx = (weight * x * x).sum(axis=1)
    sxy = (weight * x * y).sum(axis=1)
    determinant = sw * sxx - sx * sx
    solvable = (sw >= 2) & (determinant > 0)
    safe = np.where(solvable, determinant, 1.0)
    fitted_p1 = (sw * sxy - sx * sy) / safe
    fitted_p2 = (sy - fitted_p1 * sx) / np.where(solvable, sw, 1.0)
    return np.where(solvable, fitted_p1, p1), np.where(solvable, fitted_p2, p2)


def _ions(candidates: Candidates, found: np.ndarray) -> np.ndarray:
    """How many ions each axis found a line of.

    :param found: Whether each axis found each line, (H, L).
    :return: (H,)
    """
    n_ions = int(candidates.ion.max()) + 1 if candidates.ion.size else 0
    membership = np.zeros((found.shape[1], n_ions), dtype=np.float64)
    membership[np.arange(found.shape[1]), candidates.ion] = 1.0
    return ((found.astype(np.float64) @ membership) > 0).sum(axis=1)
