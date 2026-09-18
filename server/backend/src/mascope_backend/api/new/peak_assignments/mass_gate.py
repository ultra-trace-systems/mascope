"""The run's own mass calibration, and what a row's distance from it costs.

A sample's committed rows do not scatter around zero ppm; they scatter around
whatever offset that acquisition actually sat at, with whatever spread that
instrument achieved that day. Both are measurable from the run's own output -
the rows it has more than a mass fit for - and once measured, every committed
row has a position in that distribution. ``mass_z`` is that position, and this
is where it is put on the row.

The centre is not always one number. On an Orbitrap the calibration's residual
below about m/z 120 is closer to a constant absolute offset, so in ppm it grows
as 1/mz: on the sparse Orbitrap set the run's commits sit near 0 ppm above m/z
120, at -0.6 to -0.8 at m/z 80-120 and at -1.4 to -1.5 below m/z 80, in every
sample. Where a run's commits demand it, its centre follows
``ppm = a + b * 1000 / mz`` and a row is judged at its own m/z; a run whose
commits do not keeps the constant centre exactly. The width stays one number.

The gate on top of it is deliberately narrow. A row whose isotope envelope the
run confirmed is never demoted: an isotopologue that tracks its parent is a
second place in the spectrum agreeing with the formula, and that is evidence
the mass error does not overrule. Every other commit beyond
:data:`OFF_CALIBRATION_Z` is capped at ``candidate``, and beyond
:data:`BELOW_ASSIGNABILITY_Z` at ``below_assignability`` - a compound of the
workspace's own target library among them. The library's rows anchor the
calibration, because a list assembled for this data names compounds the
sample holds, but a list does not say where each of their lines has to sit.
A reference mirror's rows do not even anchor it: a mirror is a prior matched
against every sample rather than a library somebody assembled for this data.
The formula stays on the row either way: what the run is withdrawing is its
confidence, not its reading.

An isotopologue that does not track its parent is not ``assigned`` either,
wherever it sits. Whether it tracks is judged twice. Within the class's
precision it tracks, and corroborates. Beyond that, a line can still miss its
parent by no more than it can deliver - a line near the noise floor is placed
less well than the class's precision says, and a line with another peak close
beside it is pushed off its place - and such a line is in doubt: held at
``candidate``, and never taken lower for a distance its own quality explains.
A line that misses by more than that too is a peak the matching window happened
to reach, capped like any other commit and at ``candidate`` at least.

What this is worth, measured rather than assumed: on the 43-sample gate its
distance cap takes 30 rows - one of them the target library's, which its
curation used to spare - and moves G1 by at most 1.7 points on one set. The
isotopologue verdicts hold 97 lines at candidate, 85 of them in doubt; the
reference engine reads 28 of the 97 as isotopologues of the same formula. That
is not the lever it was designed to be, because the failure it was
designed to catch has already been closed upstream - the finder ranks candidates
on the v2 fit at the sample's own width (step 2.1), so a formula three sigma out
does not win its peak in the first place. The widest mass error on any committed
top-tier Orbitrap row is now 0.92 ppm. The gate stays because a guard that
nothing trips is what a guard looks like when the thing it guards against is
absent, and because the ``mass_z`` it records is what step 2.4's mechanical
tiers read; it is not what will move the agreement metrics.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from mascope_backend.api.new.peak_assignments.engine import (
    ROLE_ISO_CHILD,
    ROLE_M0,
    SampleMassAccuracy,
    is_target_library_row,
)
from mascope_backend.api.new.peak_assignments.envelope_claims import is_claimed
from mascope_backend.api.new.peak_assignments.tiers import (
    TIER_BELOW_ASSIGNABILITY,
    TIER_CANDIDATE,
    TIER_RANK,
)
from mascope_tools.composition.mass_accuracy import (
    MASS_TREND_ABS_FLOOR_MDA,
    MassTrend,
    fit_mass_accuracy,
    fit_mass_trend,
    scoring_sigma_ppm,
    trend_width_floor_ppm,
)


#: Beyond this many fitted sigma from the run's own centre, an uncorroborated
#: commit is capped at ``candidate``. Three because that is the distance at
#: which a correct assignment stops being a plausible member of the
#: distribution the run's confirmed rows describe.
OFF_CALIBRATION_Z = 3.0

#: And beyond this, ``below_assignability``: at six sigma the row is not a tail
#: of the run's own accuracy under any reading, so what is left is a formula
#: nobody should act on rather than one to look at again.
BELOW_ASSIGNABILITY_Z = 6.0

#: How many of the instrument class's precision an isotopologue's mass error may
#: sit from its parent's and still be read as the same ion. Three, because the
#: quantity being tested is a DIFFERENCE of two measurements and this is a three
#: sigma test on it: the difference is about as wide as the class's precision on
#: the sets that have real envelopes (0.28 to 0.45 ppm against a 0.3 ppm class),
#: so testing at the bare precision is a two-thirds-of-one-sigma test and throws
#: away a third to a half of the genuine children. Measured on the gate, the
#: share of children kept at one, two and three times the precision: A 59, 84,
#: 91%; B 66, 88, 95%; C 67, 78, 81%; C2 66, 82, 90%; D 50, 74, 84%. What the
#: anchors then fit does not move at any of those multiples (A +0.02 ppm at 0.12
#: wide throughout, D -0.17 at 0.35 to 0.40), so this number does not decide the
#: calibration - it decides which rows a run calls corroborated, which is the
#: flag step 2.4 reads.
#:
#: On a TOF no multiple separates the two populations, because a coincidental
#: pairing is spread evenly across the matching window rather than clustered:
#: the same three sets keep 37-46% at one and 77-82% at three. There the test is
#: a purity choice rather than a separation, which is why 2.4 weighs isotope
#: corroboration by instrument class rather than counting it.
TRACKING_SIGMAS = 3.0

#: The signal-to-noise at which the class's precision describes a line. Below
#: it a line is placed less well, by the square root of how much fainter it is.
#: Measured on the gate's Orbitrap sets, over the isotopologues the reference
#: engine confirms, the child-minus-parent error narrows as 1.15 / sqrt(SNR)
#: ppm - a width of 0.47 ppm at SNR 5-10, 0.30 at 10-20, 0.21 at 20-50 and 0.15
#: above - which is the Orbitrap class's 0.3 ppm at 15.
PRECISION_SNR = 15.0

#: How close another peak has to sit to a line, in the line's own FWHM, to move
#: its centroid. Two lines of one ion 1.3 FWHM apart in theory measured 1.6 to
#: 1.7 FWHM apart on the gate's broad-window nitrate set: the push itself
#: widens the spacing it is judged on, so the reach is taken past it.
OVERLAP_REACH_FWHM = 2.0

#: How far such a peak can push the line, in the line's FWHM, when it is at
#: least as tall as the line; a shorter one pushes in proportion to its height.
#: The 13C2 and 18O lines of that set's strongest ion, about 8.7 ppm wide and
#: about as tall as each other, sit 1.2 to 2.1 ppm off their places.
OVERLAP_PUSH_FWHM = 0.25

#: How an isotopologue's mass error follows its parent's.
#: Inside the class's precision: the two lines measure one axis.
TRACKING_TRACKS = "tracks"
#: Beyond it, but by no more than the line's noise and neighbours explain.
TRACKING_IN_DOUBT = "in_doubt"
#: Beyond that too, or with an error missing on either side.
TRACKING_UNTRACKED = "untracked"

#: The reasons a capped row carries, which is the vocabulary step 2.4's
#: ``tier_reasons`` collects.
REASON_OFF_CALIBRATION = "off_calibration"
REASON_ISOTOPOLOGUE_IN_DOUBT = "isotopologue_in_doubt"
REASON_ISOTOPOLOGUE_UNTRACKED = "isotopologue_untracked"

#: A curated identity: the row won its peak for a compound of the workspace's
#: own target library, so a library somebody assembled for this data - not this
#: run's own search - proposed the formula. It anchors the run's calibration.
#:
#: It does not exempt the row from the cap. A list names a compound, not where
#: each line of it has to sit, and the lines such an exemption held at the top
#: tier were exactly the ones a mass error has reason to doubt: on the
#: assignment gate, three isotopologues more than three widths off a
#: monoisotopic row within one width - a weak M+2 at 1e-5 to 1e-4 of the base
#: peak, and both lines of a partly resolved 13C2/18O pair that push each other
#: apart, which the gate now reads as in doubt rather than as off calibration. A
#: library row an isotopologue tracks is corroborated by that
#: (:data:`CORROBORATED_ISOTOPOLOGUE`), recorded ahead of its curation.
#:
#: A reference mirror's row does not qualify, although Stage A matches it in the
#: same frame. A mirror is a prior matched against every sample, so on a TOF most
#: of its pairings are lines the match window happened to reach. Counted as
#: corroborated, those lines anchored the calibration and widened it: on the
#: gate's three TOF sets with the default seed loaded, this fit's own width went
#: from 3.0, 4.3 and 2.1 ppm to 7.0, 7.3 and 6.7. A mirror's row is corroborated
#: the way a search result is, by an isotopologue that tracks it.
CORROBORATED_CURATED = "curated"

#: A confirmed envelope: the ion committed a monoisotopic peak AND at least one
#: isotopologue of the same reading whose own mass error TRACKS its parent's, so
#: the spectrum agrees with the formula in more than one place.
#:
#: The tracking test is what makes this mean anything on a crowded spectrum. An
#: isotopologue is paired within the instrument class's matching window - 15 ppm on
#: a TOF - so on a dense TOF spectrum a peak that is nobody's isotopologue lands
#: inside that window by coincidence, and counting it as agreement lets the
#: coincidence corroborate the reading it was matched to. Measured on the gate:
#: the child-minus-parent error is 0.28 to 0.45 ppm wide on the Orbitrap sets
#: with 98-99% of children inside 3 ppm of their parent, and 4.6 to 6.5 ppm wide
#: on the three TOF sets with 37-46% inside it. Two lines of one ion differ only
#: by what centroiding does to each, so the class's own precision is the bar.
CORROBORATED_ISOTOPOLOGUE = "isotopologue"


@dataclass(frozen=True)
class MassCalibration:
    """What a run measured of its own mass accuracy, from its own commits.

    Distinct from :class:`engine.SampleMassAccuracy`, which is what STAGE A
    measured before the untargeted stage ran and is what that stage was scored
    at. This is measured afterwards, over everything the run went on to commit
    and corroborate, and it is what a committed row's distance is expressed in.
    The two answer different questions and a run records both: on a sample whose
    curated library holds two targets the first has nothing to say and the
    second has thousands of rows.

    The centre is :attr:`mu_ppm` unless the run's commits demanded one that
    follows the mass range, which is then :attr:`trend`.
    """

    mu_ppm: float | None = None
    sigma_ppm: float | None = None
    anchors: int = 0
    #: The width a row's distance is actually judged in, which is this fit's
    #: own width or the width the SEARCH scored at, whichever is wider. They
    #: differ because the two describe different populations: the anchors are
    #: the run's best-corroborated rows and are intrinsically its best measured
    #: ones (0.06-0.17 ppm on the sparse Orbitrap set), while the rows the gate
    #: judges rest on the mass fit alone and spread wider (0.36 ppm on the same
    #: set). Judging the second population by the first condemns its tails by
    #: construction - measured, it demoted 105 rows the reference confirms on
    #: that set alone. A row cannot be off calibration for a distance its own
    #: search was told to accept, so the search's width is the floor.
    gate_sigma_ppm: float | None = None
    #: The centre as a function of m/z, where the run's commits demanded one.
    trend: MassTrend | None = None
    #: Why the run judged at the constant centre instead, once it had one.
    trend_refused: str | None = None
    #: The committed monoisotopic rows the trend was asked of.
    trend_rows: int = 0

    @property
    def measured(self) -> bool:
        """Whether a row's distance from this can be stated at all.

        Both numbers are needed and neither substitutes: without a width there
        is no scale to divide by, and without an offset the distance would be
        measured from a centre the run never established - which on a sample
        sitting 1.2 ppm out condemns every row it has.
        """
        return self.mu_ppm is not None and self.sigma_ppm is not None

    def z_of(self, mz_error_ppm: float | None, mz: float | None = None) -> float | None:
        """Where a mass error sits in this run's own distribution, in sigma.

        In :attr:`gate_sigma_ppm`, not in the fitted width, so that one number
        on the row means one thing: a row reads beyond 3 exactly when the gate
        would cap it for being there.

        Measured from the centre at the row's own m/z where the run has a
        :attr:`trend`, in that width floored by the trend's absolute floor
        (:data:`MASS_TREND_ABS_FLOOR_MDA`); from the constant centre otherwise,
        and for a row whose m/z is unknown.
        """
        if not self.measured or mz_error_ppm is None:
            return None
        error = float(mz_error_ppm)
        if not np.isfinite(error):
            return None
        centre = float(self.mu_ppm)
        width = float(self.gate_sigma_ppm or self.sigma_ppm)
        if self.trend is not None and mz is not None:
            centre = self.trend.centre_ppm(mz)
            width = max(width, trend_width_floor_ppm(mz))
        return (error - centre) / width

    def snapshot(self) -> dict:
        """What the run records about the calibration it judged its rows at."""
        return {
            "mu_ppm": None if self.mu_ppm is None else round(float(self.mu_ppm), 4),
            "sigma_ppm": (
                None if self.sigma_ppm is None else round(float(self.sigma_ppm), 4)
            ),
            "anchors": int(self.anchors),
            # What the fit measured, and what a row was judged in. Recorded
            # apart because a reader comparing two runs needs to know whether a
            # row escaped the cap on its own accuracy or on the search's.
            "gate_sigma_ppm": (
                None
                if self.gate_sigma_ppm is None
                else round(float(self.gate_sigma_ppm), 4)
            ),
            # As on the scoring snapshot: "none" means the run measured nothing,
            # which is a different statement from measuring zero.
            "mu_source": "fitted" if self.mu_ppm is not None else "none",
            "sigma_source": "fitted" if self.sigma_ppm is not None else "none",
            # Which centre a row's distance was measured from: the line below,
            # or the constant one, with the rule that refused the line.
            "centre": (
                "trend"
                if self.trend is not None
                else "constant"
                if self.measured
                else "none"
            ),
            "trend": None if self.trend is None else _trend_snapshot(self),
            "trend_refused": self.trend_refused,
        }


def _trend_snapshot(calibration: MassCalibration) -> dict:
    """The run's record of the line its centre followed."""
    trend = calibration.trend
    return {
        "intercept_ppm": round(trend.intercept_ppm, 4),
        # The absolute offset, the same at every mass: the centre at an m/z is
        # intercept_ppm + offset_mda * 1000 / mz, held at mz_lo and mz_hi.
        "offset_mda": round(trend.offset_mda, 4),
        "sigma_ppm": round(trend.sigma_ppm, 4),
        "rows": int(calibration.trend_rows),
        "kept": int(trend.points),
        "mz_lo": round(trend.mz_lo, 4),
        "mz_hi": round(trend.mz_hi, 4),
        "abs_floor_mda": MASS_TREND_ABS_FLOOR_MDA,
    }


def peak_mz(row: dict) -> float | None:
    """The m/z of the peak a row sits on, where it has a usable one."""
    value = row.get("sample_peak_mz")
    if value is None:
        return None
    mz = float(value)
    return mz if np.isfinite(mz) and mz > 0 else None


def is_committed(row: dict) -> bool:
    """Whether a ledger row commits a formula to a peak.

    The pre-passes are not commits and are excluded here rather than filtered by
    their source: a reagent hit and a ringing artifact both name no formula, so
    neither has a mass error that means "this composition is this many ppm out"
    - the reagent row's error is the distance to a cluster ion the run
    deliberately did not assign, and the artifact row has none at all.
    """
    return bool(row.get("assigned_formula")) and row.get("role") in (
        ROLE_M0,
        ROLE_ISO_CHILD,
    )


def tracking_tolerance_ppm(precision_ppm: float) -> float:
    """How far an isotopologue's mass error may sit from its parent's.

    :param precision_ppm: The instrument class's precision.
    :return: The bar, :data:`TRACKING_SIGMAS` of it.
    """
    return TRACKING_SIGMAS * float(precision_ppm)


def tracks_its_parent(
    child_error_ppm: float | None,
    parent_error_ppm: float | None,
    tolerance_ppm: float,
) -> bool:
    """Whether an isotopologue's mass error is its parent's, within precision.

    Two lines of one ion are one measurement of one axis: their mass errors
    differ only by what centroiding does to each peak, so a child whose error
    sits further from its parent's than the instrument class can explain is not
    that ion's isotopologue - it is another peak the matching window happened to
    reach. A row missing either error cannot be shown to track and does not.

    :param child_error_ppm: The isotopologue's own mass error.
    :param parent_error_ppm: Its owner's.
    :param tolerance_ppm: The bar, from :func:`tracking_tolerance_ppm`.
    :return: Whether the pair may be read as one envelope.
    """
    if child_error_ppm is None or parent_error_ppm is None:
        return False
    child, parent = float(child_error_ppm), float(parent_error_ppm)
    if not (np.isfinite(child) and np.isfinite(parent)):
        return False
    return abs(child - parent) <= float(tolerance_ppm)


@dataclass(frozen=True)
class LineQuality:
    """How well one peak can place its line.

    :param snr: The peak's own signal-to-noise, where the file records one.
    :param push_ppm: How far the tallest other peak within
        :data:`OVERLAP_REACH_FWHM` of it can move its centroid.
    """

    snr: float | None = None
    push_ppm: float = 0.0

    @property
    def noise_ratio(self) -> float:
        """How much wider this line's own noise makes it, in variance.

        One for a line at or above :data:`PRECISION_SNR` and for a line whose
        noise nobody measured, which the class's precision then describes.
        """
        if self.snr is None or not math.isfinite(self.snr) or self.snr <= 0:
            return 1.0
        return max(1.0, PRECISION_SNR / float(self.snr))


#: A line the run has nothing to read with: the class's precision describes it,
#: and nothing beside it is known to push it.
UNREAD_LINE = LineQuality()


class SpectrumLines:
    """Every peak of one sample, read for how well each can place its line.

    :param peak_ids: The peaks' ids.
    :param mz: Their m/z, in the same order.
    :param intensity: Their intensities, in the quantity the run assigned on.
    :param snr: Their signal-to-noise, or None where the file records none.
    :param resolution: The file's resolving power as a function of m/z, or None
        where the run could not read one. Without it no line is read as pushed.
    """

    def __init__(
        self,
        peak_ids: Sequence,
        mz: Sequence[float],
        intensity: Sequence[float],
        snr: Sequence[float] | None = None,
        resolution: Callable[[float], float] | None = None,
    ):
        mz_values = np.asarray(mz, dtype=float)
        order = np.argsort(mz_values, kind="stable")
        self._mz = mz_values[order]
        self._intensity = np.nan_to_num(np.asarray(intensity, dtype=float)[order])
        self._snr = None if snr is None else np.asarray(snr, dtype=float)[order]
        self._position = {str(peak_ids[index]): at for at, index in enumerate(order)}
        self._resolution = resolution
        self._read: dict[str, LineQuality] = {}

    @classmethod
    def from_peaks(
        cls,
        peaks_df: pd.DataFrame,
        resolution: Callable[[float], float] | None = None,
    ) -> SpectrumLines:
        """Read a sample's peak frame, as the service loads it."""
        return cls(
            peaks_df["sample_peak_id"].astype(str).tolist(),
            peaks_df["mz"].to_numpy(),
            peaks_df["intensity"].to_numpy(),
            (
                peaks_df["signal_to_noise"].to_numpy()
                if "signal_to_noise" in peaks_df.columns
                else None
            ),
            resolution,
        )

    def fwhm_ppm(self, mz: float) -> float | None:
        """A line's full width at half maximum at ``mz``, or None if unknown."""
        if self._resolution is None:
            return None
        try:
            resolving_power = float(self._resolution(float(mz)))
        except (TypeError, ValueError, ZeroDivisionError, ArithmeticError):
            return None
        if not math.isfinite(resolving_power) or resolving_power <= 0:
            return None
        return 1e6 / resolving_power

    def of(self, peak_id) -> LineQuality:
        """How well the peak with this id can place its line."""
        key = str(peak_id)
        quality = self._read.get(key)
        if quality is None:
            position = self._position.get(key)
            if position is None:
                quality = UNREAD_LINE
            else:
                snr = None if self._snr is None else float(self._snr[position])
                quality = LineQuality(
                    snr=snr if snr is not None and math.isfinite(snr) else None,
                    push_ppm=self._push_ppm(position),
                )
            self._read[key] = quality
        return quality

    def _push_ppm(self, position: int) -> float:
        mz = float(self._mz[position])
        fwhm = self.fwhm_ppm(mz)
        if fwhm is None:
            return 0.0
        reach = mz * fwhm * 1e-6 * OVERLAP_REACH_FWHM
        low = int(np.searchsorted(self._mz, mz - reach, side="left"))
        high = int(np.searchsorted(self._mz, mz + reach, side="right"))
        others = np.delete(self._intensity[low:high], position - low)
        tallest = float(others.max()) if others.size else 0.0
        if tallest <= 0:
            return 0.0
        own = float(self._intensity[position])
        return OVERLAP_PUSH_FWHM * fwhm * (1.0 if own <= 0 else min(1.0, tallest / own))

    def snapshot(self) -> dict:
        """What the run had to read its lines with, for its record."""
        return {
            "noise": self._snr is not None and bool(np.isfinite(self._snr).any()),
            "resolution": self._resolution is not None,
            "precision_snr": PRECISION_SNR,
            "overlap_reach_fwhm": OVERLAP_REACH_FWHM,
            "overlap_push_fwhm": OVERLAP_PUSH_FWHM,
        }


def line_tolerance_ppm(
    precision_ppm: float,
    child: LineQuality = UNREAD_LINE,
    parent: LineQuality = UNREAD_LINE,
) -> float:
    """How far two lines of one ion can miss each other on their own quality.

    The class's bar (:func:`tracking_tolerance_ppm`) widened by what each line
    can deliver: its noise in quadrature, as the bar's own width is, and each
    neighbour's push on top, since a push is a shift rather than a scatter.
    Equal to the class's bar for two lines the class's precision describes.

    :param precision_ppm: The instrument class's precision.
    :param child: The isotopologue's line.
    :param parent: Its parent's.
    :return: The widest miss the two lines' own quality explains.
    """
    noise = math.sqrt((child.noise_ratio + parent.noise_ratio) / 2.0)
    return (
        tracking_tolerance_ppm(precision_ppm) * noise + child.push_ppm + parent.push_ppm
    )


def tracking_of(
    child_error_ppm: float | None,
    parent_error_ppm: float | None,
    *,
    precision_ppm: float,
    child: LineQuality = UNREAD_LINE,
    parent: LineQuality = UNREAD_LINE,
) -> str:
    """How an isotopologue's mass error follows its parent's.

    :param child_error_ppm: The isotopologue's own mass error.
    :param parent_error_ppm: Its parent's.
    :param precision_ppm: The instrument class's precision.
    :param child: How well the isotopologue's peak places its line.
    :param parent: How well its parent's does.
    :return: :data:`TRACKING_TRACKS`, :data:`TRACKING_IN_DOUBT` or
        :data:`TRACKING_UNTRACKED`.
    """
    if child_error_ppm is None or parent_error_ppm is None:
        return TRACKING_UNTRACKED
    child_error, parent_error = float(child_error_ppm), float(parent_error_ppm)
    if not (math.isfinite(child_error) and math.isfinite(parent_error)):
        return TRACKING_UNTRACKED
    miss = abs(child_error - parent_error)
    if miss <= tracking_tolerance_ppm(precision_ppm):
        return TRACKING_TRACKS
    if miss <= line_tolerance_ppm(precision_ppm, child, parent):
        return TRACKING_IN_DOUBT
    return TRACKING_UNTRACKED


def isotopologue_tracking(
    assignments: list[dict],
    *,
    precision_ppm: float,
    lines: SpectrumLines | None = None,
) -> dict[str, str]:
    """How every committed isotopologue with an owner follows it.

    :param assignments: Every row built for this sample.
    :param precision_ppm: The instrument class's precision.
    :param lines: The sample's peaks, read for their quality; without them
        every line is one the class's precision describes.
    :return: :func:`tracking_of`'s answer keyed by ``peak_assignment_id``.
    """
    by_id = {
        str(row["peak_assignment_id"]): row
        for row in assignments
        if row.get("peak_assignment_id")
    }
    tracking: dict[str, str] = {}
    for row in assignments:
        owner_id = row.get("owner_peak_assignment_id")
        if not is_committed(row) or row.get("role") != ROLE_ISO_CHILD or not owner_id:
            continue
        owner = by_id.get(str(owner_id)) or {}
        tracking[str(row["peak_assignment_id"])] = tracking_of(
            row.get("mz_error_ppm"),
            owner.get("mz_error_ppm"),
            precision_ppm=precision_ppm,
            child=UNREAD_LINE if lines is None else lines.of(row.get("sample_peak_id")),
            parent=(
                UNREAD_LINE
                if lines is None or not owner
                else lines.of(owner.get("sample_peak_id"))
            ),
        )
    return tracking


def corroboration_of(
    assignments: list[dict],
    *,
    precision_ppm: float,
) -> dict[str, str | None]:
    """Per committed row: what this run has for it beyond the mass fit.

    Three answers. The first two anchor the run's calibration; only the first
    exempts a row from the gate's cap:

    - :data:`CORROBORATED_ISOTOPOLOGUE` - the reading committed a monoisotopic
      peak and at least one isotopologue of the same ion WHOSE MASS ERROR
      TRACKS ITS PARENT'S, so the spectrum agrees in a second place rather than
      in a place the matching window happened to reach. Both rows of such a
      pair are corroborated by it; a child that does not track corroborates
      nothing, including itself. Nor does a line a claim read as the ion's
      (:mod:`envelope_claims`), even where it tracks: the run committed it as
      something else first, which is the doubt the claim records. Answered
      ahead of curation, since it is the answer the cap reads.
    - :data:`CORROBORATED_CURATED` - Stage A matched it to a compound of the
      workspace's target library (:func:`engine.is_target_library_row`), so the
      formula was proposed by a library assembled for this data rather than by
      this run's own search over the mass. A reference mirror's Stage A row is
      not curated in this sense.
    - ``None`` - the row rests on the mass fit alone. A reference mirror's row
      that no isotopologue tracks is one of these.

    :param assignments: Every row built for this sample, in any order.
    :param precision_ppm: The instrument class's precision. The bar a child's
        error must meet against its parent's is :data:`TRACKING_SIGMAS` of it,
        which is a three sigma test on their difference.
    :return: Corroboration keyed by ``peak_assignment_id``, committed rows only.
    """
    tolerance = tracking_tolerance_ppm(precision_ppm)
    error_by_id = {
        str(row["peak_assignment_id"]): row.get("mz_error_ppm")
        for row in assignments
        if row.get("peak_assignment_id")
    }
    # The children that are their parents' isotopologues, and the parents they
    # confirm. Resolved first, because a row's own corroboration depends on the
    # whole envelope rather than on the row.
    tracking_children: set[str] = set()
    confirmed_owners: set[str] = set()
    for row in assignments:
        owner_id = row.get("owner_peak_assignment_id")
        if not is_committed(row) or not owner_id or is_claimed(row):
            continue
        if tracks_its_parent(
            row.get("mz_error_ppm"), error_by_id.get(str(owner_id)), tolerance
        ):
            tracking_children.add(str(row["peak_assignment_id"]))
            confirmed_owners.add(str(owner_id))

    corroboration: dict[str, str | None] = {}
    for row in assignments:
        if not is_committed(row):
            continue
        row_id = str(row["peak_assignment_id"])
        if row_id in tracking_children or row_id in confirmed_owners:
            corroboration[row_id] = CORROBORATED_ISOTOPOLOGUE
        elif is_target_library_row(row):
            corroboration[row_id] = CORROBORATED_CURATED
        else:
            corroboration[row_id] = None
    return corroboration


def fit_run_mass_accuracy(
    assignments: list[dict],
    corroboration: dict[str, str | None],
) -> MassCalibration:
    """Fit the run's mass accuracy over the rows it corroborated.

    The same robust fit the rest of the engine measures a sample with
    (``fit_mass_accuracy``: median and scaled MAD), over a different and much
    larger set of anchors. Stage A's fit is over the target library's matched
    isotopologues, which on a sample whose library holds two targets is nothing;
    this is over everything the run committed and had a second reason for, which
    on the same sample is hundreds of rows. That is what makes the calibration
    the run's own rather than the library's. A reference mirror's monoisotopic
    row anchors it only when an isotopologue tracks it, because its curation is
    not a second reason (:data:`CORROBORATED_CURATED`).

    Only corroborated rows anchor it, and that is the point rather than a
    limitation: fitting over every commit would measure the spread of the rows
    being judged, so the distribution would widen to accommodate whatever sits
    in its tail and the gate would be unable to find anything by construction.
    A target library's row anchors it and is still judged against it, which a
    median and a scaled MAD allow: a line of the list off calibration is one
    anchor among the run's corroborated commits, and it moves neither.

    Monoisotopic rows only. An isotopologue is the same ion measured on a weaker
    peak, so it is the wider row wherever it is real - 0.35 ppm against 0.12 for
    the M0 rows it belongs to on the sparse Orbitrap set - and where it is not
    real it is a coincidence of the matching window. Letting isotopologues anchor
    made the recorded calibration theirs on every TOF set: on the bromide TOF
    set they and the M0 rows they "confirmed" were two thirds of the anchors and
    put the run at +0.4 to +1.6 ppm and 4.4 to 4.6 ppm wide, while the run's own
    uncorroborated M0 rows sat at -0.1 to +0.1 and 2.7 to 2.9 wide.

    Where that fit is measured, the centre may follow the mass range, and that
    line is fitted over every committed monoisotopic row rather than over the
    anchors (``fit_mass_trend``, whose acceptance rule is peaky's). The anchors
    thin out exactly where the shape lives, because a small ion's isotopologue
    is too weak to track: on the sparse Orbitrap set three or four of about
    forty anchors sit below m/z 104, where 27 to 29 commits sit at -1.1 to -1.3
    ppm, and the rule's lever guard - five points in each half of the fitted
    range - refuses the anchors' line on every sample. Over the commits the
    line is accepted on all six at -0.11 to -0.14 mDa, which is where the
    anchors' own line sits once that guard is relaxed. What keeps the rows
    being judged out of the WIDTH does not carry to this line: a width fitted
    over them stretches to cover their tail, while a line of two parameters
    trimmed at three widths is not bent by one, and the rule refuses a line the
    commits do not demand. That rule is also what keeps a trend off the
    long-range Orbitrap set, whose anchors alone accept a 1/mz line through
    their dip to -0.9 ppm at m/z 300-450, which about 140 commits above m/z
    450, sitting near 0 ppm, contradict.

    :param assignments: Every row built for this sample.
    :param corroboration: What :func:`corroboration_of` answered for them.
    :return: The offset and width, each None where too few anchors were found,
        and where both were found, the trend or the rule that refused it.
    """
    errors = [
        row["mz_error_ppm"]
        for row in assignments
        if row.get("role") == ROLE_M0
        and corroboration.get(str(row.get("peak_assignment_id"))) is not None
        and row.get("mz_error_ppm") is not None
    ]
    mu, sigma = fit_mass_accuracy(errors)
    calibration = MassCalibration(mu_ppm=mu, sigma_ppm=sigma, anchors=len(errors))
    if not calibration.measured:
        return calibration
    commits = [
        (peak_mz(row), row["mz_error_ppm"])
        for row in assignments
        if row.get("role") == ROLE_M0
        and str(row.get("peak_assignment_id")) in corroboration
        and row.get("mz_error_ppm") is not None
        and peak_mz(row) is not None
    ]
    trend, refused = fit_mass_trend(
        [mz for mz, _ in commits], [error for _, error in commits]
    )
    return replace(
        calibration, trend=trend, trend_refused=refused, trend_rows=len(commits)
    )


def apply_mass_gate(
    assignments: list[dict],
    *,
    stage_a_accuracy: SampleMassAccuracy | None = None,
    fallback_sigma_ppm: float,
    lines: SpectrumLines | None = None,
) -> dict:
    """Record every commit's ``mass_z`` and cap the outliers no envelope confirms.

    Modifies the rows in place, and runs after both stages and both pre-passes
    have built them: the corroboration it reads is a property of the whole
    ledger (which M0 kept an isotopologue, which peak Stage A claimed), so no
    stage can answer it alone.

    Nothing is capped for its distance when the run measured no calibration - a
    sample with too few corroborated commits to fit an offset and a width has
    not earned the right to demote anything on it, and standing down is
    recorded rather than being indistinguishable from a run that found nothing
    to demote. An isotopologue that does not track its parent is held at
    ``candidate`` either way, since that compares two of the run's own lines
    and needs no calibration.

    :param assignments: Every row built for this sample, modified in place.
    :param stage_a_accuracy: What Stage A measured, for the run's record. It is
        reported beside this fit rather than folded into it: the two are
        measured over different rows and a reader comparing a run's scoring to
        its gating needs to see both. It is also half of the width the search
        scored at, which is the floor on the width this gate judges in.
    :param fallback_sigma_ppm: The instrument class's width, which is both the
        other half of that floor and the precision an isotopologue has to track
        its parent within (``profiles.resolve_fallback_sigma_ppm``).
    :param lines: The sample's peaks, read for how well each places its line.
        Without them every line is one the class's precision describes, so no
        isotopologue is in doubt: it tracks or it does not.
    :return: A JSON-serializable summary for the run's config.
    """
    # The width the untargeted search actually scored a mass error at, rebuilt
    # from the same two numbers `pattern_scoring_for` builds it from, so the
    # floor below is the search's own width rather than an approximation of it.
    search_sigma = scoring_sigma_ppm(
        None if stage_a_accuracy is None else stage_a_accuracy.sigma_ppm,
        float(fallback_sigma_ppm),
    )
    corroboration = corroboration_of(assignments, precision_ppm=fallback_sigma_ppm)
    tracking = isotopologue_tracking(
        assignments, precision_ppm=fallback_sigma_ppm, lines=lines
    )
    calibration = fit_run_mass_accuracy(assignments, corroboration)
    if calibration.sigma_ppm is not None:
        calibration = replace(
            calibration,
            gate_sigma_ppm=max(float(calibration.sigma_ppm), float(search_sigma)),
        )
    summary = {
        **calibration.snapshot(),
        "cap_z": OFF_CALIBRATION_Z,
        "floor_z": BELOW_ASSIGNABILITY_Z,
        # What the search judged a mass error at, recorded so the gate width
        # above can be read as the max of the two it is.
        "search_sigma_ppm": round(float(search_sigma), 4),
        "precision_ppm": float(fallback_sigma_ppm),
        "applied": calibration.measured,
        "committed": len(corroboration),
        "corroborated": sum(1 for value in corroboration.values() if value),
        # Rows capped for their distance from the centre.
        "capped": 0,
        "below_assignability": 0,
        # Of the capped rows, the target library's: its rows anchor the fit
        # above, and this is what judging them against it cost.
        "capped_curated": 0,
        # How the committed isotopologues follow their parents, what the run
        # read their lines with, and the ones held at candidate for it alone.
        "isotopologues": {
            outcome: sum(1 for value in tracking.values() if value == outcome)
            for outcome in (TRACKING_TRACKS, TRACKING_IN_DOUBT, TRACKING_UNTRACKED)
        },
        "lines": (lines or SpectrumLines([], [], [])).snapshot(),
        "capped_in_doubt": 0,
        "capped_untracked": 0,
    }
    if stage_a_accuracy is not None:
        # What Stage A had to score the untargeted search with, beside what the
        # run went on to be able to measure for itself. On the gate sets these
        # differ by an order of magnitude in anchor count. The Stage A count is
        # the target library's matched lines alone, whether or not a reference
        # mirror is loaded (`engine.target_library_rows`).
        summary["stage_a_anchors"] = int(stage_a_accuracy.anchors)

    for row in assignments:
        row_id = str(row.get("peak_assignment_id"))
        if row_id not in corroboration:
            continue
        corroborated = corroboration[row_id]
        gate: dict = {"corroborated_by": corroborated}
        followed = tracking.get(row_id)
        if followed is not None:
            gate["tracking"] = followed
        z = calibration.z_of(row.get("mz_error_ppm"), peak_mz(row))
        provenance = row.setdefault("provenance", {})
        if z is not None:
            provenance["mass_z"] = round(z, 2)
        capped, reason = _cap_of(corroborated, followed, z)
        # Only ever downwards. A row the bands already put below the cap is not
        # lifted onto it, and the gate's word for such a row is silence: it did
        # not decide that tier and must not appear to have.
        if capped is not None and TIER_RANK[row["tier"]] > TIER_RANK[capped]:
            row["tier"] = capped
            gate["capped"] = capped
            gate["reason"] = reason
            if reason == REASON_OFF_CALIBRATION:
                summary["capped"] += 1
                summary["capped_curated"] += corroborated == CORROBORATED_CURATED
                if capped == TIER_BELOW_ASSIGNABILITY:
                    summary["below_assignability"] += 1
            elif reason == REASON_ISOTOPOLOGUE_IN_DOUBT:
                summary["capped_in_doubt"] += 1
            else:
                summary["capped_untracked"] += 1
        provenance["mass_gate"] = gate
    return summary


def _cap_of(
    corroborated: str | None, followed: str | None, z: float | None
) -> tuple[str | None, str | None]:
    """The strongest tier a commit may hold here, and the reason it is held.

    :param corroborated: What :func:`corroboration_of` answered for the row.
    :param followed: How it follows its parent, for an isotopologue.
    :param z: Its distance from the run's centre, where one was measured.
    """
    if corroborated == CORROBORATED_ISOTOPOLOGUE or followed == TRACKING_TRACKS:
        # A pair of lines that measure one axis. A line that tracks and still
        # corroborates nothing is one a claim read as the ion's, and its claim
        # already holds it at candidate.
        return None, None
    if followed == TRACKING_IN_DOUBT:
        # Its distance from the centre is no more telling than its distance from
        # its parent, which its own line explains.
        return TIER_CANDIDATE, REASON_ISOTOPOLOGUE_IN_DOUBT
    distance = None if z is None else _cap_for(abs(z))
    if distance is not None:
        return distance, REASON_OFF_CALIBRATION
    if followed == TRACKING_UNTRACKED:
        return TIER_CANDIDATE, REASON_ISOTOPOLOGUE_UNTRACKED
    return None, None


def _cap_for(abs_z: float) -> str | None:
    """The strongest tier a row this far off the run's centre may hold."""
    if abs_z > BELOW_ASSIGNABILITY_Z:
        return TIER_BELOW_ASSIGNABILITY
    if abs_z > OFF_CALIBRATION_Z:
        return TIER_CANDIDATE
    return None
