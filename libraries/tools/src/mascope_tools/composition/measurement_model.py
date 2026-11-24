"""One measurement model per run: where a mass error is read from, how wide
it may be, and how well a line's share is measured.

A run reads every row it scores and every row it caps against one model: the
centre a mass error is measured from, as a function of m/z; the width it is
judged in; the tolerance an isotope line's share is read at; and how many
anchors of which kinds it was fitted on. The fit score's mass term, the mass
gate's distance (``mass_z``), the search's ranking, the untargeted window and
every reader outside the run take that one object, so a row cannot score well
at one estimate of the axis and be capped at another.

It is fitted at two named points and nowhere else:

* **v1** (:data:`MODEL_V1`), after Stage A's match, on the target library's
  matched lines (:class:`mass_accuracy.SampleMassAccuracy`). Where the library
  matched too few ions, the instrument class's width and the reagent lines'
  centre stand in, as they always have. The line-share tolerance is fitted on
  the same match's committed envelopes. The search ranks at v1.
* **v2** (:data:`MODEL_V2`), on the run's corroborated commits and the
  source's own lines: the mass gate's fit, whose centre may follow
  ``ppm = a + b * 1000 / mz`` (:func:`mass_accuracy.fit_mass_trend`). It keeps
  v1's tolerance, which no commit refits.

A run scores and caps at one version. Where v2 is measured and moves the
centre or the width by more than :data:`REFIT_BOUND_WIDTHS` of v1's width
anywhere over the committed m/z range, or where v1 measured nothing of its own
and v2 did, every row is scored again at v2 before any cap, and the caps read
v2 (:func:`refit_adopts`). Otherwise v1 stands for both. A run whose commits
are too few for v2 scores and caps at v1.

Declared in the library rather than in an engine because an outside engine
reading a run through the SDK rebuilds the model from the run's record
(:meth:`MeasurementModel.from_record`) and scores at it, as Mascope's engine
did.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np

from mascope_tools.composition.envelope_tolerance import EnvelopeTolerance
from mascope_tools.composition.mass_accuracy import (
    MASS_TREND_ABS_FLOOR_MDA,
    MU_SOURCE_FITTED,
    MU_SOURCE_NONE,
    MU_SOURCE_REAGENT,
    PRED_SIGMA_PPM,
    MassTrend,
    ReagentOffset,
    SampleMassAccuracy,
    unwiden_sigma_ppm,
    widen_sigma_ppm,
)


#: The model fitted after Stage A's match, on the target library's lines.
MODEL_V1 = 1

#: The model refitted on the run's corroborated commits and the source's
#: lines: the mass gate's fit.
MODEL_V2 = 2

#: How far v2 has to move from v1, in v1's widths, for a run to score again at
#: it. Measured as the largest distance between the two centres over the m/z
#: range the run committed, and as the change in the width.
#:
#: A quarter of a width is where the distinction starts to decide a row. The
#: caps judge a row at three widths; a centre a quarter of a width away moves
#: a row there by three quarters of a unit of its log likelihood and across the
#: cap if it sat within a quarter of it, which is the row the review found
#: scored well at one estimate and capped at another. Inside it, scoring the
#: run again moves every fit by less than its own rounding at the bands.
REFIT_BOUND_WIDTHS = 0.25

#: The anchor kinds a model counts, as its record names them.
ANCHOR_LIBRARY_LINES = "library_lines"
ANCHOR_LIBRARY_IONS = "library_ions"
ANCHOR_REAGENT_LINES = "reagent_lines"
ANCHOR_COMMITS = "commits"
ANCHOR_SOURCE_LINES = "source_lines"
ANCHOR_SOURCE_CAPPED = "source_capped"

#: Where the width a model judges in came from.
SIGMA_SOURCE_FITTED = "fitted"
SIGMA_SOURCE_CLASS = "instrument_class"

#: The width's own floor: the width the search ranked at, which a v2 model
#: never judges narrower than (:attr:`MeasurementModel.width_floor_ppm`).
FLOOR_SOURCE_V1 = "v1"

#: The key a run records its model under.
RECORD_KEY = "measurement_model"

#: The keys a run recorded the two halves of the model under before there was
#: one record: Stage A's scoring snapshot and the mass gate's calibration. Read
#: by :meth:`MeasurementModel.from_run_config` so an older run's ledger stays
#: readable.
LEGACY_SCORING_KEY = "pattern_scoring"
LEGACY_CALIBRATION_KEY = "mass_calibration"

#: The key a run records its line-share tolerance under, beside the model.
TOLERANCE_KEY = "envelope_tolerance"

_DIGITS = 4


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(float(value), _DIGITS)


@dataclass(frozen=True)
class MeasurementModel:
    """What a run reads a mass error and a line's share against.

    :param version: Where it was fitted: :data:`MODEL_V1` or :data:`MODEL_V2`.
    :param mu_ppm: The constant centre, in ppm; None where nothing put one,
        which a scorer reads as no correction.
    :param mu_source: Which measurement :attr:`mu_ppm` is: the model's own fit
        (``fitted``), the reagent lines' stand-in (``reagent``), or none.
    :param trend: The centre as a function of m/z, where the commits demanded
        one (v2 only).
    :param trend_refused: Why a v2 model kept the constant centre, once it had
        one.
    :param trend_rows: The committed monoisotopic rows the trend was asked of.
    :param sigma_ppm: The fitted width, before the prediction's own spread is
        added; None where too few anchors were found and the instrument
        class's stands in.
    :param fallback_sigma_ppm: The instrument class's width, which stands in
        where nothing fitted one; None where the caller named no class, which
        reads as ``score_pattern_v2``'s generic width.
    :param width_floor_ppm: The narrowest width the model judges in. A v2
        model's is v1's width, the one the search ranked at: its anchors are
        the run's best corroborated rows and narrower than the rows it judges,
        and a row cannot be off calibration for a distance its own search was
        told to accept.
    :param envelope_tolerance: How well the run measures a line's share; None
        reads today's numbers.
    :param anchors: How many anchors of which kinds it was fitted on, by the
        ``ANCHOR_*`` names.
    :param reagent: What the reagent pre-pass's lines said of the centre,
        recorded whether or not the model stands on it.
    :param tolerance_record: The run's record of its line-share tolerance
        (``EnvelopeFit.record``), kept so a reader that rebuilt the model has
        the fit's level and lines too; None where none was fitted.
    """

    version: int = MODEL_V1
    mu_ppm: float | None = None
    mu_source: str = MU_SOURCE_NONE
    trend: MassTrend | None = None
    trend_refused: str | None = None
    trend_rows: int = 0
    sigma_ppm: float | None = None
    fallback_sigma_ppm: float | None = None
    width_floor_ppm: float | None = None
    envelope_tolerance: EnvelopeTolerance | None = None
    anchors: Mapping[str, int] = field(default_factory=dict)
    reagent: ReagentOffset | None = None
    tolerance_record: dict | None = field(default=None, compare=False)

    # -- construction ---------------------------------------------------------

    @classmethod
    def v1(
        cls,
        accuracy: SampleMassAccuracy,
        fallback_sigma_ppm: float | None,
        envelope_tolerance: EnvelopeTolerance | None = None,
        tolerance_record: dict | None = None,
    ) -> MeasurementModel:
        """The model Stage A's match fitted.

        :param accuracy: What Stage A measured of the sample's mass error, on
            the target library's lines, with the reagent lines' reading beside
            it.
        :param fallback_sigma_ppm: The instrument class's width.
        :param envelope_tolerance: The line-share tolerance fitted on Stage A's
            committed envelopes.
        :param tolerance_record: Its record, where the caller has one.
        :return: The v1 model.
        """
        return cls(
            version=MODEL_V1,
            mu_ppm=accuracy.scoring_mu_ppm,
            mu_source=accuracy.mu_source,
            sigma_ppm=accuracy.sigma_ppm,
            fallback_sigma_ppm=fallback_sigma_ppm,
            envelope_tolerance=envelope_tolerance,
            anchors={
                ANCHOR_LIBRARY_LINES: int(accuracy.anchors),
                ANCHOR_LIBRARY_IONS: int(accuracy.anchor_ions),
                ANCHOR_REAGENT_LINES: (
                    0 if accuracy.reagent is None else int(accuracy.reagent.lines)
                ),
            },
            reagent=accuracy.reagent,
            tolerance_record=tolerance_record,
        )

    def v2(
        self,
        *,
        mu_ppm: float | None,
        sigma_ppm: float | None,
        commits: int,
        source_lines: int,
        source_capped: int = 0,
        trend: MassTrend | None = None,
        trend_refused: str | None = None,
        trend_rows: int = 0,
    ) -> MeasurementModel:
        """This v1 model refitted on a run's corroborated commits.

        The tolerance, the class's width and the reagent lines' reading carry
        over; the width is floored at this model's (:attr:`width_floor_ppm`).

        :param mu_ppm: The commits' centre, or None where too few were found.
        :param sigma_ppm: Their width, or None.
        :param commits: The corroborated commits among the anchors.
        :param source_lines: The source's own lines among them.
        :param source_capped: The source's lines left out for their
            brightness.
        :param trend: The line the centre follows, where accepted.
        :param trend_refused: The rule that refused one.
        :param trend_rows: The commits the trend was asked of.
        :return: The v2 model.
        """
        return replace(
            self,
            version=MODEL_V2,
            mu_ppm=mu_ppm,
            mu_source=MU_SOURCE_FITTED if mu_ppm is not None else MU_SOURCE_NONE,
            trend=trend,
            trend_refused=trend_refused,
            trend_rows=int(trend_rows),
            sigma_ppm=sigma_ppm,
            width_floor_ppm=self.width_ppm,
            anchors={
                ANCHOR_COMMITS: int(commits),
                ANCHOR_SOURCE_LINES: int(source_lines),
                ANCHOR_SOURCE_CAPPED: int(source_capped),
            },
        )

    # -- what a scorer and a cap read ------------------------------------------

    @property
    def measured(self) -> bool:
        """Whether the model is the sample's own: its centre and its width
        both fitted on its anchors, no stand-in in either. A row's distance is
        stated, and capped, only against such a model: a sample whose centre
        nothing measured would have every row it holds judged from a centre
        it never established."""
        return (
            self.mu_ppm is not None
            and self.mu_source == MU_SOURCE_FITTED
            and self.sigma_ppm is not None
        )

    @property
    def sigma_source(self) -> str:
        """Where the width came from, as a run records it."""
        return SIGMA_SOURCE_FITTED if self.sigma_ppm is not None else SIGMA_SOURCE_CLASS

    @property
    def width_ppm(self) -> float | None:
        """The width a mass error is judged in, by the score and by the caps.

        The fitted width, or the instrument class's where none was fitted,
        widened by the prediction's own spread (``mass_accuracy.PRED_SIGMA_PPM``),
        and never narrower than :attr:`width_floor_ppm`. None where there is
        neither a fit nor a class: ``score_pattern_v2``'s generic width then
        stands in.
        """
        sigma = (
            self.sigma_ppm if self.sigma_ppm is not None else self.fallback_sigma_ppm
        )
        if sigma is None:
            return self.width_floor_ppm
        width = widen_sigma_ppm(sigma)
        if self.width_floor_ppm is not None:
            width = max(width, float(self.width_floor_ppm))
        return width

    @property
    def fit_sigma_ppm(self) -> float | None:
        """The width as a fit score that widens its width itself takes it:
        :attr:`width_ppm` with the prediction's spread taken back off
        (``mass_accuracy.unwiden_sigma_ppm``), so the score widens it to
        :attr:`width_ppm` exactly."""
        width = self.width_ppm
        return None if width is None else unwiden_sigma_ppm(width)

    @property
    def scoring_mu_ppm(self) -> float:
        """The constant centre a scorer subtracts where it reads no m/z: the
        fitted or stand-in centre, else zero, which is "uncorrected"."""
        return float(self.mu_ppm) if self.mu_ppm is not None else 0.0

    def centre_ppm(self, mz: float | None = None) -> float:
        """The centre at ``mz``: the trend's where the model follows one and
        the m/z is known, held at the trend's coverage; the constant centre
        otherwise."""
        if self.trend is not None and mz is not None and np.isfinite(float(mz)):
            return float(self.trend.centre_ppm(float(mz)))
        return self.scoring_mu_ppm

    def centres_ppm(self, mz: Iterable[float] | np.ndarray) -> np.ndarray:
        """:meth:`centre_ppm` over an array of m/z, NaN read as unknown."""
        values = np.asarray(mz, dtype=float)
        constant = np.full(values.shape, self.scoring_mu_ppm)
        if self.trend is None:
            return constant
        known = np.isfinite(values) & (values > 0)
        held = np.clip(np.where(known, values, 1.0), self.trend.mz_lo, self.trend.mz_hi)
        trended = self.trend.intercept_ppm + self.trend.offset_mda * 1000.0 / held
        return np.where(known, trended, constant)

    def widths_ppm(self, mz: Iterable[float] | np.ndarray) -> np.ndarray | None:
        """:attr:`width_ppm` at each m/z, as :meth:`z_of` reads a row there.

        Where the centre follows a trend, the width at an m/z is floored by
        the trend's absolute floor there (``mass_accuracy.trend_width_floor_ppm``):
        below the lowest commit the centre is held and may be wrong by an
        amount nothing measured. A NaN or non-positive m/z is read as unknown
        and keeps the width, as :meth:`centres_ppm` keeps the constant centre.

        :return: One width per m/z; None where there is no width
            (:attr:`width_ppm`).
        """
        width = self.width_ppm
        if width is None:
            return None
        values = np.asarray(mz, dtype=float)
        widths = np.full(values.shape, float(width))
        if self.trend is None:
            return widths
        known = np.isfinite(values) & (values > 0)
        floor = MASS_TREND_ABS_FLOOR_MDA * 1000.0 / np.where(known, values, 1.0)
        return np.where(known, np.maximum(widths, floor), widths)

    def fit_sigmas_ppm(self, mz: Iterable[float] | np.ndarray) -> np.ndarray | None:
        """:meth:`widths_ppm` as a fit score that widens its width itself
        takes them, as :attr:`fit_sigma_ppm` is :attr:`width_ppm`: each with
        the prediction's spread taken back off, so the score widens it to the
        width at its line's m/z exactly."""
        widths = self.widths_ppm(mz)
        if widths is None:
            return None
        return np.sqrt(np.maximum(widths**2 - PRED_SIGMA_PPM**2, 0.0))

    def max_abs_centre_ppm(self) -> float:
        """The farthest the centre sits from zero anywhere: the constant
        centre's distance, or the trend's at either end of its coverage. What
        a window symmetric about a peak has to reach before it reaches any
        width."""
        if self.trend is None:
            return abs(self.scoring_mu_ppm)
        return max(
            abs(self.trend.centre_ppm(self.trend.mz_lo)),
            abs(self.trend.centre_ppm(self.trend.mz_hi)),
        )

    def z_of(self, mz_error_ppm: float | None, mz: float | None = None) -> float | None:
        """Where a mass error sits in the model, in widths.

        Measured from the centre at the row's m/z (:meth:`centre_ppm`), in
        the width at that m/z (:meth:`widths_ppm`): the same centre and width
        its fit score was computed at. Where the centre follows a trend, the
        width is floored by the trend's absolute floor at that m/z; the floor
        passes the width only below about m/z 50 on an Orbitrap.

        :return: The distance; None where the model is not the sample's own
            (:attr:`measured`) or the error is unknown.
        """
        if not self.measured or mz_error_ppm is None:
            return None
        error = float(mz_error_ppm)
        width = self.width_ppm
        if not np.isfinite(error) or width is None:
            return None
        if mz is not None:
            width = float(self.widths_ppm([mz])[0])
        return (error - self.centre_ppm(mz)) / width

    # -- the refit --------------------------------------------------------------

    def moved_from(
        self, earlier: MeasurementModel, mz: Iterable[float] | np.ndarray = ()
    ) -> tuple[float, float]:
        """How far this model sits from ``earlier``, in ``earlier``'s widths.

        :param earlier: The model it was refitted from.
        :param mz: The m/z the run committed, over which the two centres are
            compared; empty compares the constant centres.
        :return: The largest distance between the centres over ``mz``, and
            the width's relative change, both in ``earlier``'s widths.
        """
        width = earlier.width_ppm
        if width is None or width <= 0:
            return math.inf, math.inf
        points = np.asarray(list(mz) if not isinstance(mz, np.ndarray) else mz, float)
        points = points[np.isfinite(points) & (points > 0)]
        if points.size:
            shift = float(
                np.max(np.abs(self.centres_ppm(points) - earlier.centres_ppm(points)))
            )
        else:
            shift = abs(self.scoring_mu_ppm - earlier.scoring_mu_ppm)
        own = self.width_ppm
        change = math.inf if own is None else abs(own - width) / width
        return shift / width, change

    # -- the record ---------------------------------------------------------------

    def record(self) -> dict:
        """What a run records about the model it scored and capped at.

        The version; the centre (its constant, its source, and the line it
        follows where it follows one); the fitted width, the width a mass
        error was judged in, and its floor; the anchors by kind; and what the
        reagent lines said. The line-share tolerance is recorded under its own
        key (:data:`TOLERANCE_KEY`), which :meth:`from_run_config` reads
        beside this one.
        """
        record: dict[str, Any] = {
            "version": int(self.version),
            "mu_ppm": _rounded(self.mu_ppm),
            "mu_source": self.mu_source,
            "sigma_ppm": _rounded(self.sigma_ppm),
            "sigma_source": self.sigma_source,
            "fallback_sigma_ppm": _rounded(self.fallback_sigma_ppm),
            "width_ppm": _rounded(self.width_ppm),
            "width_floor_ppm": _rounded(self.width_floor_ppm),
            # Which centre a row's distance and fit were read from.
            "centre": (
                "trend"
                if self.trend is not None
                else "constant"
                if self.mu_ppm is not None
                else "none"
            ),
            "trend": None if self.trend is None else self._trend_record(),
            "trend_refused": self.trend_refused,
            "anchors": {kind: int(count) for kind, count in self.anchors.items()},
            "measured": self.measured,
        }
        if self.reagent is not None:
            record["reagent_lines"] = int(self.reagent.lines)
            record["reagent_mu_ppm"] = _rounded(self.reagent.mu_ppm)
            record["reagent_beyond_width"] = bool(self.reagent.beyond_width)
        return record

    def _trend_record(self) -> dict:
        trend = self.trend
        return {
            "intercept_ppm": round(trend.intercept_ppm, _DIGITS),
            # The centre at an m/z is intercept_ppm + offset_mda * 1000 / mz,
            # held at mz_lo and mz_hi.
            "offset_mda": round(trend.offset_mda, _DIGITS),
            "sigma_ppm": round(trend.sigma_ppm, _DIGITS),
            "rows": int(self.trend_rows),
            "kept": int(trend.points),
            "mz_lo": round(trend.mz_lo, _DIGITS),
            "mz_hi": round(trend.mz_hi, _DIGITS),
            # The floor a distance's width takes at low m/z (``z_of``).
            "abs_floor_mda": MASS_TREND_ABS_FLOOR_MDA,
        }

    @classmethod
    def from_record(
        cls, record: Mapping[str, Any] | None, tolerance_record: dict | None = None
    ) -> MeasurementModel | None:
        """The model a run's record says it scored and capped at.

        :param record: A run's :data:`RECORD_KEY` record (:meth:`record`).
        :param tolerance_record: The run's :data:`TOLERANCE_KEY` record.
        :return: The model; None where the record is not one.
        """
        if not isinstance(record, Mapping) or "version" not in record:
            return None
        trend_record = record.get("trend")
        trend = None
        if isinstance(trend_record, Mapping):
            trend = MassTrend(
                intercept_ppm=float(trend_record["intercept_ppm"]),
                offset_mda=float(trend_record["offset_mda"]),
                sigma_ppm=float(trend_record["sigma_ppm"]),
                points=int(trend_record.get("kept") or 0),
                mz_lo=float(trend_record["mz_lo"]),
                mz_hi=float(trend_record["mz_hi"]),
            )
        reagent = None
        if "reagent_lines" in record:
            reagent = ReagentOffset(
                mu_ppm=_float(record.get("reagent_mu_ppm")),
                lines=int(record.get("reagent_lines") or 0),
                beyond_width=bool(record.get("reagent_beyond_width")),
            )
        return cls(
            version=int(record["version"]),
            mu_ppm=_float(record.get("mu_ppm")),
            mu_source=str(record.get("mu_source") or MU_SOURCE_NONE),
            trend=trend,
            trend_refused=record.get("trend_refused"),
            trend_rows=int((trend_record or {}).get("rows") or 0),
            sigma_ppm=_float(record.get("sigma_ppm")),
            fallback_sigma_ppm=_float(record.get("fallback_sigma_ppm")),
            width_floor_ppm=_float(record.get("width_floor_ppm")),
            envelope_tolerance=EnvelopeTolerance.from_record(tolerance_record),
            anchors=dict(record.get("anchors") or {}),
            reagent=reagent,
            tolerance_record=tolerance_record,
        )

    @classmethod
    def from_run_config(
        cls, config: Mapping[str, Any] | None, fallback_sigma_ppm: float | None = None
    ) -> MeasurementModel | None:
        """The model a run's config records, the one way every reader outside
        the run reads it.

        A run's :data:`RECORD_KEY` record with its :data:`TOLERANCE_KEY` one.
        A run from before the one record kept the two halves apart: Stage A's
        scoring snapshot (:data:`LEGACY_SCORING_KEY`), at which it scored, and
        the mass gate's calibration (:data:`LEGACY_CALIBRATION_KEY`), at which
        it capped. Such a run is read at what it scored at, which is what a
        reader re-scoring its rows needs to agree with.

        :param config: A run's stored config.
        :param fallback_sigma_ppm: The instrument class's width, for an older
            run's record, which does not name it.
        :return: The model; None where the config records neither.
        """
        if not isinstance(config, Mapping):
            return None
        tolerance = config.get(TOLERANCE_KEY)
        tolerance = tolerance if isinstance(tolerance, dict) else None
        model = cls.from_record(config.get(RECORD_KEY), tolerance)
        if model is not None:
            return model
        return cls.from_legacy_scoring(
            config.get(LEGACY_SCORING_KEY), fallback_sigma_ppm, tolerance
        )

    @classmethod
    def from_legacy_scoring(
        cls,
        snapshot: Mapping[str, Any] | None,
        fallback_sigma_ppm: float | None,
        tolerance_record: dict | None = None,
    ) -> MeasurementModel | None:
        """The v1 model an older run's scoring snapshot recorded.

        :param snapshot: Its :data:`LEGACY_SCORING_KEY` record.
        :param fallback_sigma_ppm: The instrument class's width.
        :param tolerance_record: Its tolerance record.
        :return: The model; None where the snapshot says nothing of the
            centre (a run from before the offset was recorded).
        """
        if not isinstance(snapshot, Mapping):
            return None
        source = snapshot.get("mu_source")
        if source not in (MU_SOURCE_FITTED, MU_SOURCE_REAGENT, MU_SOURCE_NONE) or (
            source != MU_SOURCE_NONE and snapshot.get("mu_ppm") is None
        ):
            return None
        sigma = snapshot.get("sigma_ppm")
        fitted = snapshot.get("sigma_source") == SIGMA_SOURCE_FITTED
        reagent = None
        if source == MU_SOURCE_REAGENT:
            reagent = ReagentOffset(
                mu_ppm=float(snapshot["mu_ppm"]),
                lines=int(snapshot.get("reagent_lines") or 0),
                beyond_width=True,
            )
        elif "reagent_lines" in snapshot:
            reagent = ReagentOffset(
                mu_ppm=_float(snapshot.get("reagent_mu_ppm")),
                lines=int(snapshot.get("reagent_lines") or 0),
            )
        return cls(
            version=MODEL_V1,
            mu_ppm=None if source == MU_SOURCE_NONE else float(snapshot["mu_ppm"]),
            mu_source=source,
            sigma_ppm=(
                unwiden_sigma_ppm(float(sigma))
                if fitted and sigma is not None
                else None
            ),
            # An older run that scored at the class's width recorded it widened.
            fallback_sigma_ppm=(
                fallback_sigma_ppm
                if fallback_sigma_ppm is not None or fitted or sigma is None
                else unwiden_sigma_ppm(float(sigma))
            ),
            envelope_tolerance=EnvelopeTolerance.from_record(tolerance_record),
            anchors={
                ANCHOR_LIBRARY_LINES: int(snapshot.get("fitted_anchors") or 0),
                ANCHOR_LIBRARY_IONS: int(snapshot.get("fitted_anchor_ions") or 0),
                ANCHOR_REAGENT_LINES: int(snapshot.get("reagent_lines") or 0),
            },
            reagent=reagent,
            tolerance_record=tolerance_record,
        )


def _float(value) -> float | None:
    return None if value is None else float(value)


def refit_adopts(
    v1: MeasurementModel,
    v2: MeasurementModel,
    mz: Iterable[float] | np.ndarray = (),
    bound_widths: float = REFIT_BOUND_WIDTHS,
) -> tuple[bool, dict]:
    """Whether a run scores and caps at its v2 model, rather than its v1.

    Where v2 is measured (:attr:`MeasurementModel.measured`) and either v1 was
    not the sample's own, or v2 moved its centre or its width beyond
    ``bound_widths`` of v1's width over the committed m/z range
    (:meth:`MeasurementModel.moved_from`). The run then scores every row again
    at v2 before any cap.

    :param v1: The model the run scored at.
    :param v2: The model the mass gate refitted.
    :param mz: The m/z the run committed.
    :param bound_widths: The bound, in v1's widths.
    :return: Whether v2 is adopted, and the run's record of the decision.
    """
    if not v2.measured:
        return False, {"fitted": False, "adopted": False}
    shift, change = v2.moved_from(v1, mz)
    adopted = (not v1.measured) or shift > bound_widths or change > bound_widths
    return adopted, {
        "fitted": True,
        "adopted": bool(adopted),
        "bound_widths": bound_widths,
        "centre_moved_widths": None if not math.isfinite(shift) else round(shift, 3),
        "width_moved": None if not math.isfinite(change) else round(change, 3),
    }
