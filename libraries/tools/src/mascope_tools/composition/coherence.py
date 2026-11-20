"""Co-variation of a neutral's channels over a batch: the traces and the reading.

A compound a reagent-ionization source sees through two channels - protonated
and ammoniated, deprotonated and clustered with the reagent anion - makes two
peaks, and across a batch each peak is a trace: its height in every spectrum
of the batch. Where both peaks are one molecule, the two traces rise and fall
with that molecule's abundance; where they are two compounds a formula
coincidence put together, they need not. This module is the pure half of
step 4.2 of the assignment plan (``docs/dev/assignment_quality_plan.md``): it
reads the two traces against each other the way decision 35 states the
reading, and returns what it found. It moves nothing: no tier or level reads
it until the null-model benchmark of step 4.13 gives it a likelihood ratio.

The traces
----------

:func:`batch_residuals` takes a partition's traces (one ionization mode on one
instrument type, so every trace is in one unit) and returns each one as the
reading compares it:

1. **In log10.** A change in how favourable a channel is - a source state that
   suits the radical anion more than the deprotonated ion, a humidity that
   moves a cluster's binding - multiplies a channel's signal by a factor, and
   a factor in intensity is a shift in log space. A constant one moves the
   whole trace and leaves the correlation as it was.
2. **Reagent-normalised.** Each spectrum's trace values are divided by the
   reagent signal of that spectrum (the summed intensity of the partition's
   reagent ions), the normalisation chemical-ionization measurements are
   reported in (Lee et al., Environ. Sci. Technol. 2014, 48, 6309). Every
   trace of a spectrum is divided by the same number, so the common mode
   below would take it out too; it is done first so that the common mode is
   read off normalised traces, the unit a reader of the ratio series (step
   4.11) shares. A partition with no reagent ion is not normalised, and says
   so (:attr:`BatchResiduals.normalised`).
3. **The batch's common mode removed.** Much of what moves one trace moves
   all of them: the source's sensitivity, the sample flow, a dilution, the
   day's cycle. Two unrelated compounds that both follow it correlate, which
   is how a correlation over raw traces says less than it seems to. The
   common mode of a spectrum is the median, over the batch's persistent
   traces (those present in at least :data:`PERSISTENT_SHARE` of the
   spectra), of each trace's value there less its own median: one sweep of
   Tukey's median polish (Exploratory Data Analysis, 1977), robust to the few
   traces that move on their own. A spectrum whose common mode rests on fewer
   than :data:`MIN_COMMON_MODE_TRACES` traces has none and drops out.

The reading
-----------

:meth:`BatchResiduals.co_variation` correlates two residual traces over the
spectra both hold - Pearson's r (Proc. R. Soc. Lond. 1895, 58, 240) - and
records the count with it (:class:`CoVariation`). At or above
:data:`CO_VARYING_R` the two co-vary, below they do not. Two readings are
withheld: under :data:`MIN_SPECTRA` shared spectra r says nothing, and a trace
whose residual spread is under :data:`FLAT_SPREAD` holds nothing to follow,
so its r is the noise's. Both are *untestable*, with the reason.

Anti-correlation is a reading like any other and demotes nothing: a correct
composition can run against its own parent channel when the source state
moves (decision 35). The thresholds are measured on this project's own
batches; the constants say how.

A flat series
-------------

The same traces answer a second question of each one alone (the fourth rider
of step 4.2, 4.2e): does it hold a constant ratio to the batch's normaliser?
A source ion, an inlet contaminant or a constant reagent cluster is there in
every spectrum at the share of the source's signal the source gives it, while
an analyte rises and falls with what was sampled - a plume, the day's cycle, a
cylinder opened for an hour. :func:`read_flatness` reads it per partition:

1. **The normaliser** (:func:`batch_normaliser`) is each spectrum's summed
   reagent signal where the partition holds reagent ions, else its total ion
   current (TIC): a reagent-ionization measurement is reported per reagent
   ion (Lee et al. 2014, above), and a source whose reagent ions sit below the
   acquired window is read against the TIC. A normaliser has to clear the bar
   it is used to judge by: one whose own cv is :data:`FLAT_CV` or more moves
   more than a flat trace, and dividing by it would put its swings into every
   trace rather than take the source's out, so the traces are read as they
   are, and the reading says so.
2. **The trace's cv** (:func:`normalised_cv`): the coefficient of variation,
   the standard deviation over the mean, of the trace's linear values divided
   by the normaliser, over the spectra the trace is present in. No common mode
   is removed: what moves every trace is exactly what a constant ratio to the
   source does not.
3. **The verdict** (:func:`read_flat_series`): *flat* where the cv is under
   :data:`FLAT_CV` and the trace is present in at least
   :data:`FLAT_MIN_SHARE` of the partition's spectra and in
   :data:`MIN_SPECTRA` or more; *varying* where the cv is at or above it on
   :data:`MIN_SPECTRA` spectra or more; *untestable* otherwise, with the
   reason. A partition whose traces mostly
   hold still - a steady flow, a held chamber, replicate files of one sample -
   is flat everywhere, and there flatness tells a background from nothing:
   fewer than :data:`MIN_VARYING_SHARE` of its read traces varying and no
   trace of it is read flat (:data:`REASON_STEADY`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Hashable, Iterable, Mapping, Optional, Sequence

import numpy as np


#: Pearson r at or above which two channels' traces read as co-varying: the
#: plan's line (step 4.2, decision 35).
CO_VARYING_R = 0.6

#: The fewest spectra both traces must hold for r to be read. Measured on the
#: testbed's 20 batch ledgers (2026-11-08), against decoy pairs - a trace
#: read against one of another neutral in the same partition, of a similar
#: prevalence: over 3 to 9 shared spectra a decoy pair reads r >= 0.6 in 28 %
#: of pairs (two channels of one neutral: 45 %), over 10 to 19 in 14 % (23 %),
#: over 20 to 49 in 5 % (12 %). Under ten, more than a quarter of the pairs
#: chance puts together read as co-varying.
MIN_SPECTRA = 10

#: The residual spread, as a standard deviation in log10, under which a trace
#: is flat: nothing moves in it beyond its own noise, and r against it is the
#: noise's. Measured on the same ledgers, over ten or more spectra: two
#: channels of one neutral whose flatter residual trace spreads under 0.02
#: read r >= 0.6 in 4 % of 185 pairs, against 19 % between 0.02 and 0.04 and
#: 39 % or more above 0.04. Under it the reading cannot see a co-variation
#: that is there.
FLAT_SPREAD = 0.02

#: The share of a partition's spectra a trace must be present in for the
#: common mode to be read off it. A trace seen only where it is bright would
#: pull every spectrum's median towards its own peaks.
PERSISTENT_SHARE = 0.5

#: The fewest persistent traces a spectrum's common mode is read from.
MIN_COMMON_MODE_TRACES = 5

VERDICT_CO_VARYING = "co_varying"
VERDICT_NOT_CO_VARYING = "not_co_varying"
VERDICT_UNTESTABLE = "untestable"

#: Every verdict, the strongest reading first.
VERDICTS = (VERDICT_CO_VARYING, VERDICT_NOT_CO_VARYING, VERDICT_UNTESTABLE)

#: Why a pair is untestable: too few spectra hold both traces.
REASON_FEW_SPECTRA = "few_spectra"
#: Why a pair is untestable: one of the two traces is flat.
REASON_FLAT = "flat"

#: The cv of a trace over the batch's normaliser under which the trace is
#: flat: a constant ratio to the source. The reference engine's line (its
#: ``FLAT_CV``, peaky #53), the first reading the plan names. Read on the
#: testbed copy's batch ledgers (2026-11-16): on the cylinder batch, whose 280
#: files read reagent-normalised, the readings present in 1 to 13 % of the
#: files vary at a cv of 0.2 to 1.4, one in every file at 2.4, and those in
#: every file that read flat at 0.02 to 0.23; across the copy's large ledgers
#: that vary, up to 68 % of the assigned batch peaks read under it. The flag
#: moves no tier (the plan owner's call on step 4.2e), so this is the
#: reading's default until a bench reads the flag.
FLAT_CV = 0.25

#: The share of a partition's spectra a trace must be present in to be read
#: flat: a background is there in every spectrum, and a trace present in a few
#: holds still over those few whatever it is. A rarer trace on
#: :data:`MIN_SPECTRA` spectra or more at :data:`FLAT_CV` or over it still
#: reads varying; one on fewer is not read at all. The persistent
#: traces of :data:`PERSISTENT_SHARE`; on the cylinder batch every certified
#: component falls under it but toluene, which varies.
FLAT_MIN_SHARE = PERSISTENT_SHARE

#: The share of a partition's read traces that must vary (cv at or above
#: :data:`FLAT_CV`) for a flat trace to mean background: in a batch that holds
#: still, every trace is flat. The reference engine's ``MIN_VARYING_FRAC``.
#: On the copy, two ledgers of a steady flow fall under it (8 and 16 % of
#: their traces varying, 82 and 93 % of their assigned rows flat), the rest
#: read 31 to 83 %.
MIN_VARYING_SHARE = 0.20

#: The normaliser a partition's traces are divided by: its reagent ions'
#: summed signal, or the spectrum's total ion current.
NORMALISER_REAGENT = "reagent"
NORMALISER_TIC = "tic"

VERDICT_FLAT = "flat"
VERDICT_VARYING = "varying"

#: Every flatness verdict, the flag's first.
FLAT_VERDICTS = (VERDICT_FLAT, VERDICT_VARYING, VERDICT_UNTESTABLE)

#: Why a trace's flatness is untestable: under :data:`FLAT_CV`, but in under
#: :data:`FLAT_MIN_SHARE` of its partition's spectra.
REASON_RARE = "rare"
#: Why a trace's flatness is untestable: its partition holds still
#: (:data:`MIN_VARYING_SHARE`).
REASON_STEADY = "steady_batch"


@dataclass(frozen=True)
class CoVariation:
    """What two channels' traces say of each other across a batch.

    :ivar verdict: :data:`VERDICT_CO_VARYING`, :data:`VERDICT_NOT_CO_VARYING`
        or :data:`VERDICT_UNTESTABLE`.
    :ivar n_spectra: The spectra both residual traces hold.
    :ivar r: Pearson's r over them; None where the pair is untestable.
    :ivar reason: Why it is untestable (:data:`REASON_FEW_SPECTRA`,
        :data:`REASON_FLAT`); None otherwise.
    :ivar spread: Each trace's residual spread over those spectra, the
        standard deviation in log10, in the order the traces were given;
        None where fewer than two spectra hold both.
    """

    verdict: str
    n_spectra: int
    r: Optional[float] = None
    reason: Optional[str] = None
    spread: Optional[tuple[float, float]] = None

    @property
    def testable(self) -> bool:
        """Whether r was read."""
        return self.verdict != VERDICT_UNTESTABLE

    def to_json(self) -> dict:
        """The reading, JSON-ready and rounded as a record keeps it."""
        return {
            "verdict": self.verdict,
            "r": None if self.r is None else round(self.r, 3),
            "n_spectra": self.n_spectra,
            "reason": self.reason,
            "spread": (
                None
                if self.spread is None
                else [round(value, 4) for value in self.spread]
            ),
        }


def read_co_variation(
    a: np.ndarray,
    b: np.ndarray,
    *,
    min_spectra: int = MIN_SPECTRA,
    flat_spread: float = FLAT_SPREAD,
    co_varying_r: float = CO_VARYING_R,
) -> CoVariation:
    """Read two residual traces against each other.

    :param a: One trace on the batch's spectra, NaN where it has no value.
    :param b: The other, on the same spectra.
    :param min_spectra: The fewest shared spectra r is read over
        (:data:`MIN_SPECTRA`).
    :param flat_spread: The residual spread under which a trace is flat
        (:data:`FLAT_SPREAD`).
    :param co_varying_r: The r at or above which the two co-vary
        (:data:`CO_VARYING_R`).
    :return: The reading.
    """
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    both = np.isfinite(a) & np.isfinite(b)
    n = int(both.sum())
    spread = (float(np.std(a[both])), float(np.std(b[both]))) if n >= 2 else None
    if n < min_spectra:
        return CoVariation(
            VERDICT_UNTESTABLE, n, reason=REASON_FEW_SPECTRA, spread=spread
        )
    # A trace with no spread at all has no r, whatever the floor is set to.
    if min(spread) < flat_spread or min(spread) == 0.0:
        return CoVariation(VERDICT_UNTESTABLE, n, reason=REASON_FLAT, spread=spread)
    r = float(np.corrcoef(a[both], b[both])[0, 1])
    verdict = VERDICT_CO_VARYING if r >= co_varying_r else VERDICT_NOT_CO_VARYING
    return CoVariation(verdict, n, r=r, spread=spread)


def strength(reading: CoVariation) -> tuple:
    """A sort key, strongest first: the testable readings by r, highest
    first, then the untestable ones by their shared spectra, most first."""
    if reading.testable:
        return (0, -reading.r)
    return (1, -reading.n_spectra)


def by_strength(readings: Iterable[CoVariation]) -> list[CoVariation]:
    """Several partners' readings, the one a row shows first
    (:func:`strongest`); a stable sort, so ties keep the order given."""
    return sorted(readings, key=strength)


def strongest(readings: Iterable[CoVariation]) -> Optional[CoVariation]:
    """The reading a row shows of several partners': the highest r among the
    testable ones, else the untestable one with the most shared spectra.
    None for no reading at all."""
    ordered = by_strength(readings)
    return ordered[0] if ordered else None


def _log_matrix(
    traces: Mapping[Hashable, Mapping[str, float]], samples: Sequence[str]
) -> np.ndarray:
    """``traces`` in log10 on the ``samples`` axis, NaN where a trace has no
    positive, finite value."""
    column = {sample: index for index, sample in enumerate(samples)}
    matrix = np.full((len(traces), len(samples)), np.nan)
    for row, trace in enumerate(traces.values()):
        for sample, value in trace.items():
            index = column.get(sample)
            if index is None or value is None:
                continue
            value = float(value)
            if value > 0 and np.isfinite(value):
                matrix[row, index] = np.log10(value)
    return matrix


def _normaliser(
    reagent_traces: Mapping[Hashable, Mapping[str, float]], samples: Sequence[str]
) -> Optional[np.ndarray]:
    """The log10 of each spectrum's summed reagent signal, NaN where no
    reagent ion is present; None where the partition has no reagent ion."""
    if not reagent_traces:
        return None
    linear = 10.0 ** _log_matrix(reagent_traces, samples)
    total = np.nansum(linear, axis=0)
    present = np.isfinite(linear).any(axis=0)
    out = np.full(len(samples), np.nan)
    out[present] = np.log10(total[present])
    return out


@dataclass(frozen=True)
class BatchResiduals:
    """A partition's traces as the reading compares them.

    :ivar samples: The spectra, in the order of every array's columns.
    :ivar keys: The traces, in the order of :attr:`residuals`' rows.
    :ivar residuals: Each trace in log10, reagent-normalised, the common mode
        removed; NaN where the trace has no value or the spectrum no common
        mode or no reagent signal.
    :ivar normaliser: Each spectrum's log10 reagent signal, None where the
        partition has no reagent ion.
    :ivar common_mode: Each spectrum's common mode, NaN where it has none.
    :ivar common_mode_traces: How many persistent traces each spectrum's
        common mode was read from.
    :ivar persistent: How many traces the common mode is read off.
    """

    samples: tuple[str, ...]
    keys: tuple[Hashable, ...]
    residuals: np.ndarray
    normaliser: Optional[np.ndarray]
    common_mode: np.ndarray
    common_mode_traces: np.ndarray
    persistent: int

    @property
    def normalised(self) -> bool:
        """Whether the traces were divided by a reagent signal."""
        return self.normaliser is not None

    def trace(self, key: Hashable) -> np.ndarray:
        """One trace's residuals."""
        return self.residuals[self.keys.index(key)]

    def co_variation(self, a: Hashable, b: Hashable, **thresholds) -> CoVariation:
        """Read two of the partition's traces against each other
        (:func:`read_co_variation`)."""
        return read_co_variation(self.trace(a), self.trace(b), **thresholds)

    def summary(self) -> dict:
        """What the traces were read through, for a run's record."""
        return {
            "spectra": len(self.samples),
            "spectra_read": int(np.isfinite(self.common_mode).sum()),
            "traces": len(self.keys),
            "common_mode_traces": self.persistent,
            "normalised": self.normalised,
        }


def batch_residuals(
    traces: Mapping[Hashable, Mapping[str, float]],
    *,
    reagent_traces: Optional[Mapping[Hashable, Mapping[str, float]]] = None,
    samples: Optional[Iterable[str]] = None,
    persistent_share: float = PERSISTENT_SHARE,
    min_common_mode_traces: int = MIN_COMMON_MODE_TRACES,
) -> BatchResiduals:
    """Every trace of a partition in log10, reagent-normalised, with the
    batch's common mode removed.

    :param traces: Trace key -> spectrum -> intensity, one trace per anchor:
        the partition's analyte peaks, every one of which the common mode may
        be read off. A spectrum a trace has no positive value in is a gap.
    :param reagent_traces: The partition's reagent ions, in the same form; the
        normaliser. None or empty for no normalisation.
    :param samples: The spectra, in order; every spectrum a trace or a reagent
        ion holds, sorted, when omitted.
    :param persistent_share: The share of the spectra a trace must be present
        in for the common mode to be read off it (:data:`PERSISTENT_SHARE`).
    :param min_common_mode_traces: The fewest persistent traces a spectrum's
        common mode rests on (:data:`MIN_COMMON_MODE_TRACES`).
    :return: The residuals.
    """
    reagent_traces = reagent_traces or {}
    if samples is None:
        held = {s for trace in traces.values() for s in trace}
        held |= {s for trace in reagent_traces.values() for s in trace}
        samples = sorted(held)
    samples = tuple(samples)
    keys = tuple(traces)

    logs = _log_matrix(traces, samples)
    normaliser = _normaliser(reagent_traces, samples)
    normalised = logs if normaliser is None else logs - normaliser[None, :]

    present = np.isfinite(normalised).sum(axis=1)
    persistent = present >= max(1.0, persistent_share * len(samples))
    if persistent.any() and samples:
        rows = normalised[persistent]
        with np.errstate(all="ignore"):
            centred = rows - np.nanmedian(rows, axis=1, keepdims=True)
        counts = np.isfinite(centred).sum(axis=0)
        common_mode = np.full(len(samples), np.nan)
        enough = counts >= min_common_mode_traces
        if enough.any():
            common_mode[enough] = np.nanmedian(centred[:, enough], axis=0)
    else:
        counts = np.zeros(len(samples), dtype=int)
        common_mode = np.full(len(samples), np.nan)

    return BatchResiduals(
        samples=samples,
        keys=keys,
        residuals=normalised - common_mode[None, :],
        normaliser=normaliser,
        common_mode=common_mode,
        common_mode_traces=counts,
        persistent=int(persistent.sum()),
    )


# --- a flat series (step 4.2e) ------------------------------------------------


def _cv(values: np.ndarray) -> Optional[float]:
    """The coefficient of variation of positive values: the sample standard
    deviation over the mean; None under two values."""
    if values.size < 2:
        return None
    mean = float(np.mean(values))
    if not mean > 0:
        return None
    return float(np.std(values, ddof=1)) / mean


@dataclass(frozen=True)
class Normaliser:
    """What a partition's traces are divided by before their flatness is read.

    :ivar kind: :data:`NORMALISER_REAGENT` or :data:`NORMALISER_TIC`; None
        where the partition holds neither.
    :ivar values: Each spectrum's linear normaliser, NaN where it has none;
        None where :attr:`kind` is.
    :ivar cv: The normaliser's own cv over the spectra that hold it.
    :ivar rejected: Whether that cv is :data:`FLAT_CV` or more, so the traces
        are read undivided.
    """

    kind: Optional[str]
    values: Optional[np.ndarray]
    cv: Optional[float]
    rejected: bool = False

    @property
    def divisor(self) -> Optional[np.ndarray]:
        """What a trace is divided by: None where there is no normaliser, or
        it was rejected."""
        return None if self.rejected else self.values

    def to_json(self) -> dict:
        return {
            "normaliser": self.kind,
            "normaliser_cv": None if self.cv is None else round(self.cv, 4),
            "normaliser_rejected": self.rejected,
            "normalised": self.divisor is not None,
        }


def _linear_matrix(
    traces: Mapping[Hashable, Mapping[str, float]], samples: Sequence[str]
) -> np.ndarray:
    """``traces`` on the ``samples`` axis, NaN where a trace has no positive,
    finite value."""
    with np.errstate(all="ignore"):
        return 10.0 ** _log_matrix(traces, samples)


def batch_normaliser(
    samples: Sequence[str],
    *,
    reagent_traces: Optional[Mapping[Hashable, Mapping[str, float]]] = None,
    tic: Optional[Mapping[str, float]] = None,
    flat_cv: float = FLAT_CV,
) -> Normaliser:
    """A partition's normaliser: its reagent ions' summed signal per spectrum
    where it holds any, else each spectrum's total ion current.

    :param samples: The partition's spectra, in order.
    :param reagent_traces: The partition's reagent ions, trace key -> spectrum
        -> intensity.
    :param tic: Spectrum -> its total ion current, for a partition with no
        reagent ion.
    :param flat_cv: The bar the normaliser must clear (:data:`FLAT_CV`).
    """
    samples = tuple(samples)
    values: Optional[np.ndarray] = None
    kind: Optional[str] = None
    if reagent_traces:
        linear = _linear_matrix(reagent_traces, samples)
        present = np.isfinite(linear).any(axis=0)
        if present.any():
            values = np.full(len(samples), np.nan)
            values[present] = np.nansum(linear[:, present], axis=0)
            kind = NORMALISER_REAGENT
    if values is None and tic:
        values = _linear_matrix({"tic": tic}, samples)[0]
        if np.isfinite(values).any():
            kind = NORMALISER_TIC
        else:
            values = None
    if values is None:
        return Normaliser(None, None, None)
    cv = _cv(values[np.isfinite(values)])
    return Normaliser(kind, values, cv, rejected=cv is not None and cv >= flat_cv)


def normalised_cv(
    trace: Mapping[str, float],
    samples: Sequence[str],
    divisor: Optional[np.ndarray] = None,
) -> tuple[Optional[float], int]:
    """A trace's cv over the normaliser, over the spectra it is present in.

    :param trace: Spectrum -> intensity.
    :param samples: The partition's spectra, the axis of ``divisor``.
    :param divisor: Each spectrum's normaliser (:attr:`Normaliser.divisor`);
        None to read the trace as it is.
    :return: The cv (None under two values) and the count of spectra it was
        read over: those where the trace is positive and the divisor is too.
    """
    values = _linear_matrix({"trace": trace}, samples)[0]
    if divisor is not None:
        with np.errstate(all="ignore"):
            values = values / np.where(divisor > 0, divisor, np.nan)
    values = values[np.isfinite(values) & (values > 0)]
    return _cv(values), int(values.size)


@dataclass(frozen=True)
class FlatSeries:
    """Whether one trace holds a constant ratio to its batch's normaliser.

    :ivar verdict: :data:`VERDICT_FLAT`, :data:`VERDICT_VARYING` or
        :data:`VERDICT_UNTESTABLE`.
    :ivar cv: The trace's cv over the normaliser; None where under two spectra
        hold it.
    :ivar n_present: The spectra it was read over.
    :ivar n_spectra: Its partition's spectra.
    :ivar reason: Why it is untestable (:data:`REASON_FEW_SPECTRA`,
        :data:`REASON_RARE`, :data:`REASON_STEADY`); None otherwise.
    """

    verdict: str
    cv: Optional[float]
    n_present: int
    n_spectra: int
    reason: Optional[str] = None

    @property
    def share(self) -> float:
        """The share of the partition's spectra the trace was read over."""
        return self.n_present / self.n_spectra if self.n_spectra else 0.0

    @property
    def flat(self) -> bool:
        return self.verdict == VERDICT_FLAT

    def to_json(self) -> dict:
        return {
            "verdict": self.verdict,
            "cv": None if self.cv is None else round(self.cv, 4),
            "n_present": self.n_present,
            "n_spectra": self.n_spectra,
            "share": round(self.share, 4),
            "reason": self.reason,
        }


def read_flat_series(
    cv: Optional[float],
    n_present: int,
    n_spectra: int,
    *,
    steady: bool = False,
    flat_cv: float = FLAT_CV,
    min_share: float = FLAT_MIN_SHARE,
    min_spectra: int = MIN_SPECTRA,
) -> FlatSeries:
    """The verdict on one trace's cv.

    :param cv: Its cv over the normaliser (:func:`normalised_cv`).
    :param n_present: The spectra it was read over.
    :param n_spectra: Its partition's spectra.
    :param steady: Whether the partition holds still
        (:data:`MIN_VARYING_SHARE`): a trace that would read flat is then
        untestable.
    :param flat_cv: The bar (:data:`FLAT_CV`).
    :param min_share: The share of the spectra the trace must be in
        (:data:`FLAT_MIN_SHARE`).
    :param min_spectra: The fewest spectra (:data:`MIN_SPECTRA`).
    """
    if n_present < min_spectra or cv is None:
        return FlatSeries(
            VERDICT_UNTESTABLE, cv, n_present, n_spectra, REASON_FEW_SPECTRA
        )
    if cv >= flat_cv:
        return FlatSeries(VERDICT_VARYING, cv, n_present, n_spectra)
    if not n_spectra or n_present / n_spectra < min_share:
        return FlatSeries(VERDICT_UNTESTABLE, cv, n_present, n_spectra, REASON_RARE)
    if steady:
        return FlatSeries(VERDICT_UNTESTABLE, cv, n_present, n_spectra, REASON_STEADY)
    return FlatSeries(VERDICT_FLAT, cv, n_present, n_spectra)


@dataclass(frozen=True)
class Flatness:
    """A partition's flatness reading: its normaliser, how much of it varies,
    and each trace's verdict.

    :ivar normaliser: What the traces were divided by.
    :ivar series: Trace key -> its verdict.
    :ivar traces_read: The traces whose cv was read over :data:`MIN_SPECTRA`
        spectra or more.
    :ivar varying_share: The share of those that vary.
    :ivar steady: Whether that share is under :data:`MIN_VARYING_SHARE`.
    """

    normaliser: Normaliser
    series: dict
    traces_read: int
    varying_share: Optional[float]
    steady: bool

    def summary(self) -> dict:
        """What the reading was read through, for a run's record."""
        counts = {verdict: 0 for verdict in FLAT_VERDICTS}
        for one in self.series.values():
            counts[one.verdict] += 1
        return {
            **self.normaliser.to_json(),
            "traces_read": self.traces_read,
            "varying_share": (
                None if self.varying_share is None else round(self.varying_share, 4)
            ),
            "steady": self.steady,
            "flat": counts[VERDICT_FLAT],
            "varying": counts[VERDICT_VARYING],
            "untestable": counts[VERDICT_UNTESTABLE],
        }


def read_flatness(
    traces: Mapping[Hashable, Mapping[str, float]],
    *,
    samples: Optional[Iterable[str]] = None,
    reagent_traces: Optional[Mapping[Hashable, Mapping[str, float]]] = None,
    tic: Optional[Mapping[str, float]] = None,
    read: Optional[Iterable[Hashable]] = None,
    flat_cv: float = FLAT_CV,
    min_share: float = FLAT_MIN_SHARE,
    min_spectra: int = MIN_SPECTRA,
    min_varying_share: float = MIN_VARYING_SHARE,
) -> Flatness:
    """Read every trace of a partition for a constant ratio to its normaliser.

    :param traces: Trace key -> spectrum -> intensity, every trace of the
        partition: how much of the partition varies is read over all of them.
    :param samples: The partition's spectra; every spectrum a trace or a
        reagent ion holds, sorted, when omitted.
    :param reagent_traces: The partition's reagent ions (:func:`batch_normaliser`).
    :param tic: Spectrum -> its total ion current.
    :param read: The traces to give a verdict; every trace when omitted.
    :return: The reading.
    """
    reagent_traces = reagent_traces or {}
    if samples is None:
        held = {s for trace in traces.values() for s in trace}
        held |= {s for trace in reagent_traces.values() for s in trace}
        samples = sorted(held)
    samples = tuple(samples)
    normaliser = batch_normaliser(
        samples, reagent_traces=reagent_traces, tic=tic, flat_cv=flat_cv
    )
    divisor = normaliser.divisor
    cvs = {key: normalised_cv(trace, samples, divisor) for key, trace in traces.items()}
    read_cvs = [cv for cv, n in cvs.values() if cv is not None and n >= min_spectra]
    varying_share = (
        sum(cv >= flat_cv for cv in read_cvs) / len(read_cvs) if read_cvs else None
    )
    steady = varying_share is None or varying_share < min_varying_share
    keys = list(traces) if read is None else [key for key in read if key in cvs]
    series = {
        key: read_flat_series(
            *cvs[key],
            len(samples),
            steady=steady,
            flat_cv=flat_cv,
            min_share=min_share,
            min_spectra=min_spectra,
        )
        for key in keys
    }
    return Flatness(
        normaliser=normaliser,
        series=series,
        traces_read=len(read_cvs),
        varying_share=varying_share,
        steady=steady,
    )


def read_pair(
    traces: Mapping[Hashable, Mapping[str, float]],
    a: Hashable,
    b: Hashable,
    *,
    reagent_traces: Optional[Mapping[Hashable, Mapping[str, float]]] = None,
) -> CoVariation:
    """How two of a batch's traces co-vary, read as the batch's co-variation
    reading reads every pair (MAS-983): both in log10, divided by each
    spectrum's reagent signal, with the batch's common mode removed
    (:func:`batch_residuals`), and correlated over the spectra both hold.

    One normalisation for every reader: the reagent signal of every reagent
    ion but the two read. An analyte's channels are not reagent ions, so for
    them it is the partition's whole reagent signal, as the reading divides
    by; a reagent ion read against its own core - a known species' library
    ion - is not divided by itself.

    :param traces: Trace key -> spectrum -> intensity: the partition's
        traces the common mode is read off, the two read among them.
    :param a: The one trace.
    :param b: The other.
    :param reagent_traces: The partition's reagent ions; None for no
        normalisation.
    :return: The reading.
    """
    reagents = (
        None
        if reagent_traces is None
        else {key: trace for key, trace in reagent_traces.items() if key not in (a, b)}
    )
    return batch_residuals(traces, reagent_traces=reagents).co_variation(a, b)


__all__ = [
    "CO_VARYING_R",
    "FLAT_CV",
    "FLAT_MIN_SHARE",
    "FLAT_SPREAD",
    "FLAT_VERDICTS",
    "MIN_COMMON_MODE_TRACES",
    "MIN_SPECTRA",
    "MIN_VARYING_SHARE",
    "NORMALISER_REAGENT",
    "NORMALISER_TIC",
    "PERSISTENT_SHARE",
    "REASON_FEW_SPECTRA",
    "REASON_FLAT",
    "REASON_RARE",
    "REASON_STEADY",
    "VERDICTS",
    "VERDICT_CO_VARYING",
    "VERDICT_FLAT",
    "VERDICT_NOT_CO_VARYING",
    "VERDICT_UNTESTABLE",
    "VERDICT_VARYING",
    "BatchResiduals",
    "CoVariation",
    "FlatSeries",
    "Flatness",
    "Normaliser",
    "batch_normaliser",
    "batch_residuals",
    "by_strength",
    "normalised_cv",
    "read_pair",
    "read_co_variation",
    "read_flat_series",
    "read_flatness",
    "strength",
    "strongest",
]
