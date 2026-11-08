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


__all__ = [
    "CO_VARYING_R",
    "FLAT_SPREAD",
    "MIN_COMMON_MODE_TRACES",
    "MIN_SPECTRA",
    "PERSISTENT_SHARE",
    "REASON_FEW_SPECTRA",
    "REASON_FLAT",
    "VERDICTS",
    "VERDICT_CO_VARYING",
    "VERDICT_NOT_CO_VARYING",
    "VERDICT_UNTESTABLE",
    "BatchResiduals",
    "CoVariation",
    "batch_residuals",
    "by_strength",
    "read_co_variation",
    "strength",
    "strongest",
]
