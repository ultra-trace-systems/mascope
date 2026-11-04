"""MS2 peak hygiene: the centroids that are no ions, flagged and never deleted.

Two kinds of MS2 centroid are no ion, and read as sub-formulas of almost
anything - of a large O/N-rich window ion nearly every m/z is one:

* **Harmonic ghosts** (:func:`flag_harmonics`). An Orbitrap reads m/z off an
  ion's axial frequency f (m/z ~ 1/f^2), and a strong line's transient carries
  weak harmonics at 2f and 3f: peaks at about m/z / 4 and m/z / 9 that no ion
  made. Every line of a spectrum is tested, a co-isolate's and a strong
  fragment's as much as the precursor's.
* **Side lobes** (:func:`flag_satellites`). A peak list processed from
  Orbitrap profiles carries weak centroids a few line widths beside a much
  brighter line, which the profile does not hold.

Both are read by PLACE across one window's spectra (one per collision
energy): a place some spectrum shows as an ion is an ion's at every energy, so
a product that is weak at its first energies, or whose line is gone at a later
one, is read at all of them and never half erased.

:func:`read_window` reads a window's spectra with :func:`.readings.read_fragments`
and makes each flagged peak ONE row with no formula, its ``role``
``'harmonic'`` or ``'satellite'`` and its ``flag_note`` naming the line: out of
every fragment, every sum and every ladder step (:func:`.readings.ladder_gate`
reads it as no reading), and, in :func:`spectrum_counts`, out of the spectrum's
TIC and peak count, counted apart. ``keep=True`` reads every peak as before,
for debugging (peaky's ``--keep-harmonics``).

The rules are peaky's (``peaky/ms2/readings.py`` ``harmonic_ghosts`` and
``harmonic_ions``, peaky PR 82; ``peaky/ms2/attribute.py``
``satellite_fragments``, peaky PR 80), ported with peaky's known answers as
tests; ``libraries/tools/docs/ms2.md`` section 5 documents the rules,
section 6 every threshold, and section 10 where the port differs.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace

import numpy as np

from mascope_tools.composition.ms2 import readings as R
from mascope_tools.composition.ms2.readings import (
    DEFAULT_TOLERANCE,
    K_MAX_DEFAULT,
    ROLE_HARMONIC,
    ROLE_SATELLITE,
    Reading,
    Tolerance,
    read_fragments,
)


__all__ = [
    "HARMONIC_CAP",
    "HARMONIC_CLEAR_SN",
    "HARMONIC_ORDERS",
    "HARMONIC_PPM",
    "SATELLITE_FWHM",
    "SATELLITE_RATIO",
    "PeakFlag",
    "SpectrumCounts",
    "flag_harmonics",
    "flag_satellites",
    "read_window",
    "spectrum_counts",
]

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------
#: The harmonics read (n: the line's n-th harmonic, nf, seen at m/z / n^2).
#: 2f and 3f: peaky's exploratory fixture's 179.107 window carries its
#: precursor's 2f ghost at m/z 44.777 in the server's S/N-3 spectra at NCE 1,
#: 5, 10 and 15 (0.08-0.10 % of the line, constant while the precursor falls);
#: a 3f ghost holds a few per cent of its line and is seen wherever m/z / 9
#: lies above the scan's first mass. 4f (m/z / 16) was not seen at all.
HARMONIC_ORDERS: tuple[int, ...] = (2, 3)
#: How far (ppm of m/z / n^2) a ghost may sit from its nominal place. m/z is
#: not exactly proportional to 1 / f^2 (the calibration has higher terms), so
#: the ghost sits off m/z / n^2 by an amount that depends on the calibration
#: and the m/z: -2 ppm on peaky's fixture, tens of ppm on other files; 40 ppm
#: holds that with margin (2 mDa at m/z 50).
HARMONIC_PPM = 40.0
#: The most a ghost holds of its line in the SAME spectrum, by order: above it
#: the peak is an ion. 2f: 0.5 %, five times the fixture's ghost (0.08-0.10 %;
#: the real ions seen at m/z / 4 of a line hold a few % to tens of % of it).
#: 3f: 8 % - a 3f ghost holds a few per cent of its line, and up to about twice
#: that where it is read near the S/N floor.
HARMONIC_CAP: dict[int, float] = {2: 0.005, 3: 0.08}
#: The S/N a peak in a harmonic band, above the cap of every line whose band
#: holds it, needs for that reading to make its place an ion's at every energy.
#: Near the S/N floor a peak is seen only where the noise lifts it - read up to
#: about two noise sd high, so its share of its line up to s / (s - 2) of the
#: true one at S/N s: twice it at S/N 4, 1.25 times at 10. At S/N 10 a 3f ghost
#: of a few per cent stays under the 8 % cap. A peak with no S/N known clears
#: its place.
HARMONIC_CLEAR_SN = 10.0
#: A side lobe holds at most 1 / SATELLITE_RATIO of its line: peaky's MS1
#: side-lobe guard's ratio (``chem.sidelobes.SIDELOBE_RATIO``), which its MS2
#: rule took over.
SATELLITE_RATIO = 50.0
#: ... and sits within SATELLITE_FWHM line widths of it, the width being the
#: line's m/z over its reported resolving power: the outer edge of peaky's MS1
#: side-lobe band (``chem.sidelobes.SIDELOBE_BAND_FWHM``, 1 to 5 widths).
SATELLITE_FWHM = 5.0


# ---------------------------------------------------------------------------
# The flags
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PeakFlag:
    """Why one peak of a spectrum is no ion.

    ``role`` is ``'harmonic'`` or ``'satellite'``; ``mz`` the peak's m/z as
    given; ``line`` the 0-based position of the line it belongs to among the
    same spectrum's peaks, ``line_mz`` that line's m/z; ``share`` the peak's
    intensity over the line's. A harmonic ghost has its ``order`` n (seen at
    m/z / n^2) and ``offset``, ppm from m/z(line) / n^2. A side lobe has
    ``order`` 0 and ``offset`` in line widths from the line, signed (negative
    below it), the width being the line's m/z over its resolving power.
    """

    role: str
    mz: float
    line: int
    line_mz: float
    share: float
    order: int
    offset: float

    @property
    def note(self) -> str:
        """The flag in words, as :attr:`.readings.Reading.flag_note` carries it:
        ``'2f ghost of the line at 179.1068 (m/z / 4): -1.9 ppm, 0.0985 % of
        it'`` (peaky's wording), ``'side lobe of the line at 161.0961: -2.32
        line widths (-2.20 mDa), 1.01 % of it'``."""
        if self.role == ROLE_HARMONIC:
            n = self.order
            return (
                f"{n}f ghost of the line at {self.line_mz:.4f} (m/z / {n * n}): "
                f"{self.offset:+.1f} ppm, {self.share * 100:.3g} % of it"
            )
        return (
            f"side lobe of the line at {self.line_mz:.4f}: {self.offset:+.2f} line "
            f"widths ({(self.mz - self.line_mz) * 1e3:+.2f} mDa), "
            f"{self.share * 100:.3g} % of it"
        )


#: per spectrum: (key, m/z, intensity, S/N, resolving power), NaN where unknown
_Spectrum = tuple[str, np.ndarray, np.ndarray, np.ndarray, np.ndarray]


def _column(peaks, name: str, n: int) -> np.ndarray:
    """An optional per-peak column of ``peaks`` as floats (None -> NaN), all
    NaN when there is none."""
    if (isinstance(peaks, Mapping) or hasattr(peaks, "columns")) and name in peaks:
        values = np.asarray(peaks[name], dtype=np.float64).reshape(-1)
        if len(values) != n:
            raise ValueError(f"peaks' 'mz' and {name!r} differ in length")
        return values
    return np.full(n, np.nan)


def _spectra(spectra) -> list[_Spectrum]:
    """A window's spectra, ``{key: peaks}`` in their order, as arrays; the peaks
    as :func:`.readings.read_fragments` takes them, with ``signal_to_noise``
    and ``resolution`` read when given."""
    if not isinstance(spectra, Mapping):
        raise ValueError(
            f"spectra must be a mapping spectrum key -> peaks, got "
            f"{type(spectra).__name__}"
        )
    out = []
    for key, peaks in spectra.items():
        if not isinstance(key, str):
            raise ValueError(f"spectrum keys must be strings, got {key!r}")
        mz, inten = R._peak_arrays(peaks)
        out.append(
            (
                key,
                mz,
                inten,
                _column(peaks, "signal_to_noise", len(mz)),
                _column(peaks, "resolution", len(mz)),
            )
        )
    return out


def _near(m: np.ndarray, places: np.ndarray, tolerance: Tolerance) -> np.ndarray:
    """Which of ``m`` lie within the reading window (:class:`.Tolerance`, the
    window's half-width at each ``m``) of one of the sorted ``places``."""
    if not len(places):
        return np.zeros(len(m), dtype=bool)
    tol = np.maximum(tolerance.ppm * np.abs(m) * 1e-6, tolerance.mda * 1e-3)
    k = np.searchsorted(places, m)
    lo = places[np.clip(k - 1, 0, len(places) - 1)]
    hi = places[np.clip(k, 0, len(places) - 1)]
    return (np.abs(m - lo) <= tol) | (np.abs(m - hi) <= tol)


def _harmonic_places(spectra: list[_Spectrum]) -> np.ndarray:
    """The places the window reads as ions, sorted: the m/z of every peak that,
    in its own spectrum, no line's cap holds - in no line's band (a ghost
    exists only beside its line: a product of a precursor gone at that energy,
    one a ppm of jitter puts out of the band, a spectrum of that peak alone),
    or above the cap of every line whose band holds it, read at an S/N of at
    least :data:`HARMONIC_CLEAR_SN` or with none known. A peak with no finite
    positive intensity is neither."""
    out: list[float] = []
    for _, mz, inten, sn, _ in spectra:
        ok = np.isfinite(inten) & (inten > 0)
        if not ok.any():
            continue
        o = np.argsort(mz[ok], kind="stable")
        m, i, s = mz[ok][o], inten[ok][o], sn[ok][o]
        in_band = np.zeros(len(m), dtype=bool)
        under = np.zeros(len(m), dtype=bool)
        for n in HARMONIC_ORDERS:
            t = m / float(n) ** 2  # each line's place
            lo = np.searchsorted(m, t * (1.0 - HARMONIC_PPM * 1e-6), side="left")
            hi = np.searchsorted(m, t * (1.0 + HARMONIC_PPM * 1e-6), side="right")
            for j in np.flatnonzero(hi > lo):  # the lines with a peak in band
                k = np.arange(lo[j], hi[j])  # never j: m/z(L) / n^2 is not m/z(L)
                in_band[k] = True
                under[k] |= i[k] <= HARMONIC_CAP[n] * i[j]
        with np.errstate(invalid="ignore"):
            near_floor = in_band & (s < HARMONIC_CLEAR_SN)  # NaN: not near
        out.extend(m[~under & ~near_floor].tolist())
    return np.sort(np.asarray(out, dtype=np.float64))


def _ghosts(
    mz: np.ndarray, inten: np.ndarray, places: np.ndarray, tolerance: Tolerance
) -> dict[int, PeakFlag]:
    """The harmonic ghosts of ONE spectrum, by peak position: for each order n
    and each line L, the brightest of the peaks within :data:`HARMONIC_PPM` of
    m/z(L) / n^2 holding at most ``HARMONIC_CAP[n]`` of L - not L itself, and
    none within the reading window of an ion's place. A peak that is the ghost
    of several lines is named after the brightest (of equally bright lines,
    the lower order, then the earlier peak)."""
    ok = np.isfinite(inten) & (inten > 0)
    cand = ok & ~_near(mz, places, tolerance)
    out: dict[int, tuple[float, PeakFlag]] = {}
    for n in HARMONIC_ORDERS:
        target = mz / float(n) ** 2
        for j in np.flatnonzero(ok):
            t = target[j]
            with np.errstate(invalid="ignore"):
                band = (
                    cand
                    & (np.abs(mz - t) <= HARMONIC_PPM * 1e-6 * t)
                    & (inten <= HARMONIC_CAP[n] * inten[j])
                )
            if not band.any():
                continue
            k = int(np.flatnonzero(band)[np.argmax(inten[band])])
            if k in out and out[k][0] >= inten[j]:
                continue
            out[k] = (
                float(inten[j]),
                PeakFlag(
                    ROLE_HARMONIC,
                    float(mz[k]),
                    int(j),
                    float(mz[j]),
                    float(inten[k] / inten[j]),
                    int(n),
                    float((mz[k] - t) / t * 1e6),
                ),
            )
    return {k: v[1] for k, v in sorted(out.items())}


def flag_harmonics(
    spectra: Mapping[str, object], *, tolerance: Tolerance = DEFAULT_TOLERANCE
) -> dict[str, tuple[PeakFlag | None, ...]]:
    """The Orbitrap harmonic ghosts of one window's spectra: per spectrum key, a
    flag per peak (``None`` for an ion's peak), in the peaks' order.

    In each spectrum every line L is tested: for each order n of
    :data:`HARMONIC_ORDERS`, the peaks within :data:`HARMONIC_PPM` ppm of
    m/z(L) / n^2 that hold at most ``HARMONIC_CAP[n]`` of L's intensity; of
    several, the brightest is the ghost - one line has one ghost per order, and
    a second, weaker peak in the band is something else. Where a peak is the
    ghost of several lines it is named after the brightest.

    A place some spectrum of the window reads as an ion is an ion's at every
    energy: a peak within the reading window (``tolerance``) of the m/z of a
    peak that, in its own spectrum, sits in no line's band, or holds more than
    the cap of every line whose band holds it at an S/N of at least
    :data:`HARMONIC_CLEAR_SN` (or none known), is never a ghost, and of the
    others in its band the brightest is. m/z ratios within one spectrum: the
    axis offset cancels, so the peaks' own m/z are read.

    :param spectra: ``{spectrum key: peaks}``, one window's spectra; the peaks as
        :func:`.readings.read_fragments` takes them, with ``intensity`` (peaks
        without it are never flagged) and optionally ``signal_to_noise``.
    :param tolerance: The reading window an ion's place is matched within.
    :raises ValueError: For spectra that are not such a mapping, malformed
        peaks, or a bad tolerance.
    """
    return _harmonic_flags(_spectra(spectra), R._check_tolerance(tolerance))


def _harmonic_flags(
    parsed: list[_Spectrum], tolerance: Tolerance
) -> dict[str, tuple[PeakFlag | None, ...]]:
    places = _harmonic_places(parsed)
    out = {}
    for key, mz, inten, _, _ in parsed:
        ghosts = _ghosts(mz, inten, places, tolerance)
        out[key] = tuple(ghosts.get(p) for p in range(len(mz)))
    return out


def flag_satellites(
    spectra: Mapping[str, object], *, tolerance: Tolerance = DEFAULT_TOLERANCE
) -> dict[str, tuple[PeakFlag | None, ...]]:
    """The side lobes of one window's spectra: per spectrum key, a flag per peak
    (``None`` for an ion's peak), in the peaks' order.

    A peak is a lobe candidate in its spectrum when another peak there, at least
    :data:`SATELLITE_RATIO` times brighter, lies within :data:`SATELLITE_FWHM`
    of its line widths of it, the width being that brighter peak's m/z over its
    ``resolution``; the line named is the brightest such peak. A peak is a side
    lobe when every peak at its place - within the reading window
    (``tolerance``), in every spectrum of the window - is a candidate: a peak
    with no such neighbour, or whose neighbours report no resolution, makes its
    place an ion's at every energy, so a real fragment one of whose peaks
    clears the band or the ratio is kept. A peak with no finite positive
    intensity is neither. Spectra whose peaks report no resolution are never
    flagged.

    :func:`read_window` gives the flag to every peak but a window ion's
    precursor (``docs/ms2.md`` section 5.2).

    :param spectra: ``{spectrum key: peaks}``, one window's spectra; the peaks as
        :func:`.readings.read_fragments` takes them, with ``intensity`` and
        ``resolution`` (the resolving power, m/z over the line width).
    :param tolerance: The reading window a place is matched within.
    :raises ValueError: For spectra that are not such a mapping, malformed
        peaks, or a bad tolerance.
    """
    return _satellite_flags(_spectra(spectra), R._check_tolerance(tolerance))


def _satellite_flags(
    parsed: list[_Spectrum], tolerance: Tolerance
) -> dict[str, tuple[PeakFlag | None, ...]]:
    candidates: list[dict[int, PeakFlag]] = []
    places: list[float] = []
    for _, mz, inten, _, res in parsed:
        with np.errstate(invalid="ignore", divide="ignore"):
            width = np.where(np.isfinite(res) & (res > 0), mz / res, np.nan)
        found: dict[int, PeakFlag] = {}
        for p in range(len(mz)):
            ip = float(inten[p])
            if not (math.isfinite(ip) and ip > 0):
                continue  # neither a lobe nor an ion's place
            # never p itself: no peak is 50 times brighter than itself
            with np.errstate(invalid="ignore"):
                near = (inten >= SATELLITE_RATIO * ip) & (
                    np.abs(mz - mz[p]) <= SATELLITE_FWHM * width
                )
            if not near.any():
                places.append(float(mz[p]))
                continue
            j = int(np.flatnonzero(near)[np.argmax(inten[near])])  # the brightest
            found[p] = PeakFlag(
                ROLE_SATELLITE,
                float(mz[p]),
                j,
                float(mz[j]),
                ip / float(inten[j]),
                0,
                float((mz[p] - mz[j]) / width[j]),
            )
        candidates.append(found)
    ions_at = np.sort(np.asarray(places, dtype=np.float64))
    out = {}
    for (key, mz, *_), found in zip(parsed, candidates):
        at_ion = _near(mz, ions_at, tolerance)
        out[key] = tuple(
            found[p] if p in found and not at_ion[p] else None for p in range(len(mz))
        )
    return out


# ---------------------------------------------------------------------------
# A window's readings, flagged
# ---------------------------------------------------------------------------
def _flag_rows(
    rows: tuple[Reading, ...], flags: tuple[PeakFlag | None, ...]
) -> list[Reading]:
    """One spectrum's readings with each flagged peak's rows replaced by ONE row
    laid out as an unexplained one, its ``role`` and ``flag_note`` the flag's,
    at the place its first row held. A side lobe a window ion reads as its
    precursor keeps its readings. Whole peaks only: nothing to re-rank."""
    out: list[Reading] = []
    for peak, group in itertools.groupby(rows, key=lambda r: r.peak):
        group = list(group)
        flag = flags[peak]
        if (
            flag is not None
            and flag.role == ROLE_SATELLITE
            and any(r.is_precursor for r in group)
        ):
            flag = None
        if flag is None:
            out.extend(group)
            continue
        first = group[0]
        out.append(
            replace(
                R._unexplained(
                    first.spectrum, first.peak, first.mz, first.mz_cal, first.intensity
                ),
                role=flag.role,
                flag_note=flag.note,
            )
        )
    return out


def read_window(
    spectra: Mapping[str, object],
    window_ions,
    *,
    k_max: int = K_MAX_DEFAULT,
    tolerance: Tolerance = DEFAULT_TOLERANCE,
    offset_ppm: float = 0.0,
    allow_radical: bool = True,
    keep: bool = False,
) -> tuple[Reading, ...]:
    """Read every spectrum of ONE isolation window against its window ions, its
    harmonic ghosts and side lobes flagged.

    Each spectrum is read by :func:`.readings.read_fragments` under its own key
    (``spectrum``), with the same window ions, ``k_max``, tolerance, offset and
    radical switch. Then each peak :func:`flag_harmonics` flags, and each peak
    :func:`flag_satellites` flags that no window ion reads as its precursor -
    a ghost first where both flag one - is ONE row with no formula (``role``
    ``'harmonic'`` or ``'satellite'``, ``flag_note`` naming the line), at the
    place its first reading held. Every other row is as ``read_fragments``
    writes it. The rows come in spectrum order, then ``read_fragments``' order,
    so :func:`.readings.ladder_gate` takes them as they are (read at
    ``k_max=LADDER_K``); it reads a flagged row as no reading.

    :param spectra: ``{spectrum key: peaks}``, the window's spectra (one per
        collision energy) in their order; the peaks as ``read_fragments`` takes
        them, with ``intensity``, ``signal_to_noise`` and ``resolution`` where
        known (``docs/ms2.md`` section 5).
    :param window_ions: As ``read_fragments``' ``window_ions``.
    :param keep: Read every peak, flagging none - for debugging, as peaky's
        ``--keep-harmonics``.
    :raises ValueError: As ``read_fragments``, and for spectra that are not
        such a mapping or a ``keep`` that is not a bool.
    """
    if not isinstance(keep, (bool, np.bool_)):
        raise ValueError(f"keep must be a bool, got {keep!r}")
    tolerance = R._check_tolerance(tolerance)
    parsed = _spectra(spectra)
    if not parsed:
        R._window_ions(window_ions)  # refused as read_fragments would
        return ()
    harmonic = {} if keep else _harmonic_flags(parsed, tolerance)
    satellite = {} if keep else _satellite_flags(parsed, tolerance)
    out: list[Reading] = []
    for key, peaks in spectra.items():
        rows = read_fragments(
            peaks,
            window_ions,
            k_max=k_max,
            tolerance=tolerance,
            offset_ppm=offset_ppm,
            allow_radical=allow_radical,
            spectrum=key,
        )
        if keep:
            out.extend(rows)
            continue
        flags = tuple(
            h if h is not None else s for h, s in zip(harmonic[key], satellite[key])
        )
        out.extend(_flag_rows(rows, flags))
    return tuple(out)


# ---------------------------------------------------------------------------
# The spectrum's TIC and peak count
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SpectrumCounts:
    """One spectrum's TIC and peak count, the flagged peaks out of both and
    counted apart. ``tic`` sums the intensity of every peak that is not
    flagged (a peak with no intensity adds 0), ``n_peaks`` counts them;
    ``n_harmonic`` / ``harmonic_intensity`` and ``n_satellite`` /
    ``satellite_intensity`` count and sum the flagged ones."""

    spectrum: str
    tic: float
    n_peaks: int
    n_harmonic: int
    harmonic_intensity: float
    n_satellite: int
    satellite_intensity: float


def spectrum_counts(readings: Iterable[Reading]) -> tuple[SpectrumCounts, ...]:
    """Per spectrum of the readings, in the order the spectra first appear: its
    TIC and peak count over the peaks that are ions' or unexplained, and the
    harmonic ghosts and side lobes counted apart. Each (spectrum, peak) counts
    once, by its first row: a harmonic ghost is its line seen again, and a side
    lobe a processing artifact of its line, so counting either would count the
    line twice."""
    seen: set[tuple[str, int]] = set()
    sums: dict[str, list] = {}
    for r in readings:
        if (r.spectrum, r.peak) in seen:
            continue
        seen.add((r.spectrum, r.peak))
        s = sums.setdefault(r.spectrum, [0.0, 0, 0, 0.0, 0, 0.0])
        i = r.intensity if math.isfinite(r.intensity) else 0.0
        if r.role == ROLE_HARMONIC:
            s[2] += 1
            s[3] += i
        elif r.role == ROLE_SATELLITE:
            s[4] += 1
            s[5] += i
        else:
            s[0] += i
            s[1] += 1
    return tuple(SpectrumCounts(key, *s) for key, s in sums.items())
