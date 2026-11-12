"""Batch isotope checks as positive facts: what only a batch can state.

One file shows an ion once; a batch shows it in every spectrum the ion stands
in, and some things an ion's lines say hold only over that track. This module
is the pure half of the sixth rider of step 4.2 of the assignment plan
(``docs/dev/assignment_quality_plan.md``, step 4.2g; the reference engine's
batch isotope checks, written from its published description, never ported):
three facts, each a reading of lines the batch ledger already holds. They are
display-only: nothing here moves a tier or a level until their reliability is
read on the bench.

The halogen lock
----------------

A chlorine or bromine ion carries its heavy isotope line two units up: one
37Cl atom in 0.32 of a monochloride's height, one 81Br in 0.97 of a
monobromide's (n atoms, n times that). Step 4.2c stamps that line where its
height holds across the batch
(``diagnostic_isotopologues``). :func:`read_halogen_lock` reads the stamp as
the element's lock where the line also sits at the exact spacing: its offset
from the parent, the median over the samples it was stamped in, within the
batch's own position window of the predicted one (step 4.2f's
``merged_isotopologues.position_window_ppm``, four times the batch's mass
scatter, at least 1 ppm). At that spacing a 34S line, 1.25 mDa under 37Cl, is
already another peak on an Orbitrap; a 30Si line is not. 30Si sits 0.21 mDa
under 37Cl, inside a 1 ppm window from m/z 206 up, and n silicons can give it
any height. Where the window cannot tell the two, the 29Si line decides
(:func:`silicon_line`): a silicon reading of the line, n = ratio / (a30 /
a28) atoms, puts a 29Si line at n a29 / a28 of the parent, 1.5 times the line
itself, one unit up; where the batch holds that line at that height as 4.2c
holds a stamp, the lock is silicon's, not chlorine's. An ion carrying both
halogens shows two lines in one M+2 peak, and no spacing names one; it is not
locked. The lock is a fact about the ion: where the reagent is the halogen
(a bromide cluster), it confirms the cluster; the record says which atoms
are the neutral's.

The label twin
--------------

A labelled reagent is not pure. A 98% 15N nitrate carries 2% 14N, so every
ion it makes, its own cores and its analyte clusters alike, carries a 14N twin
one unit down at (1 - p) / p of its height per labelled atom, p the purity
the ionization setup records (``custom_elements.label_purity``); ambient 14N
nitrate can only add to the twin. A label's purity is a certificate's
minimum, and a reagent can be purer: on the labelled-nitrate set C2 the
reagent's own ions carry their 14N lines at 0.40 to 0.43 of the share 98%
gives them, a reagent of about 99.2%. So the batch reads its impurity off its
own labelled reagent ions where it holds them (:func:`core_factor`,
:func:`impurity_factor`): how far each one's twin runs from the share the
setup's purity gives it, the median over the samples holding both, and the
median over the ions. Every labelled ion's twin is read at the setup's share
times that factor, the setup's share alone where no reagent ion measures it.
:func:`read_twin` reads it in every sample
where that share is one an absent line would be charged for - the twin's
expected signal-to-noise at or above the detection threshold the sample's run
fitted (step 3.5b, from the minor-line response step 5.3 measured;
``envelope_tolerance.EnvelopeTolerance.detect_snr``) - and finds it present
where its height over the labelled ion's does not fall under that share by
more than :data:`~diagnostic_isotopologues.HOLD_WIDTHS` of its width, read as
4.2c reads a share. The twin is *present* where it is seen so in more than a
fifth of those samples, *absent* where it is missing from at least
:data:`ABSENT_SHARE` of them: an ion read as labelled whose impurity twin is
missing where it must show is not the label's (a chlorinated ion sits 0.044
mDa from a labelled nitrate reading with two more carbons). The 14N line it
finds is the twin's batch peak, and how far it runs above the purity's share
(its *excess*) says how much of that line the label's impurity does not
explain: an excess 14N line keeps its neutral's cluster only beside this
partner.

The neutral pair
----------------

A uronium source reads a neutral protonated and clustered with urea, 60.032
Da apart. Where both channels of one neutral stand in the batch and their
traces co-vary over it, as step 4.2's co-variation reading
(``coherence.read_co_variation``) measures them, the pair establishes the
neutral (:func:`read_neutral_pair`): two ions of one molecule follow its
abundance together; two compounds a formula coincidence put under one neutral
need not.

References: isotope masses and abundances, the IUPAC values as ``pyteomics``
carries them (Meija et al., Pure Appl. Chem. 2016, 88, 293); halogen and
silicon isotope clusters as element evidence, McLafferty & Turecek,
Interpretation of Mass Spectra, 4th ed. 1993, ch. 2, and Kind & Fiehn, BMC
Bioinformatics 2007, 8, 105 (rule 4, the isotopic pattern); the batch checks
as the reference engine publishes them (peaky, ``docs/EVIDENCE_LEVELS.md``
section 3.2 and ``docs/OUTPUTS.md``, its rules H, K and U).
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import numpy as np
from pyteomics.mass import nist_mass

from mascope_tools.composition.diagnostic_isotopologues import (
    HOLD_WIDTHS,
    MIN_DOMINANCE,
    MIN_JUDGED,
    PREDICTION_DEPTH,
    REASON_FEW_SPECTRA,
    REASON_UNRESOLVED,
    TRACK_SHARE,
    VERDICT_NOT_STAMPED,
    VERDICT_STAMPED,
    VERDICT_UNTESTABLE,
    DiagnosticIsotopologue,
    TrackReading,
    ion_charge,
    judge_ratio,
    predict_diagnostic_isotopologues,
)
from mascope_tools.composition.envelope_tolerance import (
    DEFAULT_DETECT_SNR,
    DEFAULT_TOLERANCE,
    EnvelopeTolerance,
)
from mascope_tools.composition.heuristic_filter import (
    group_unresolved_lines,
    predict_isotopes,
)
from mascope_tools.composition.mechanism_notation import mechanism_key
from mascope_tools.composition.utils import parse_formula_tokens


def _mass(symbol: str, number: int) -> float:
    return float(nist_mass[symbol][number][0])


def _abundance(symbol: str, number: int) -> float:
    return float(nist_mass[symbol][number][1])


# --- the halogen lock ---------------------------------------------------------

#: The halogens a lock is read for, each with the heavy isotope line that shows
#: it, as the envelope predictor labels the line.
HALOGEN_LINES = {"Cl": "37Cl", "Br": "81Br"}

#: The 37Cl - 35Cl spacing, and the 30Si - 28Si spacing 0.21 mDa under it.
CL37_SPACING = _mass("Cl", 37) - _mass("Cl", 35)
SI30_SPACING = _mass("Si", 30) - _mass("Si", 28)

#: The 29Si - 28Si spacing: where a silicon reading's 29Si line sits.
SI29_SPACING = _mass("Si", 29) - _mass("Si", 28)

#: A silicon reading's 29Si line over its 30Si line: n atoms give n a29 / a28
#: and n a30 / a28, so the count cancels.
SI29_PER_SI30 = _abundance("Si", 29) / _abundance("Si", 30)

#: The line a silicon reading of a chlorine lock is decided by.
SILICON_LINE = "29Si"

#: A halogen lock's verdicts: the element's line holds at the exact spacing;
#: it does, but its 29Si line says the line is silicon's; it does not; or the
#: batch cannot say.
LOCK_LOCKED = "locked"
LOCK_SILICON = "silicon"
LOCK_NOT_LOCKED = "not_locked"
LOCK_UNTESTABLE = "untestable"

#: Why a lock was not read, or did not hold.
REASON_BOTH_HALOGENS = "both_halogens"
REASON_NOT_FOUND = "not_found"
REASON_RATIO = "ratio"
REASON_ANOTHER_PARENT = "another_parent"
REASON_OFFSET = "offset"
REASON_NO_OFFSET = "no_offset"
REASON_SILICON_UNTESTABLE = "silicon_untestable"


def _neutral_part(ion_formula: str) -> str:
    """The formula of an ion without its charge."""
    return str(ion_formula).strip().rstrip("+-").strip()


def element_count(formula: str | None, symbol: str) -> int:
    """How many natural atoms of an element a formula holds; its charge, if
    any, left off. A labelled atom (``^N``) is not its element's."""
    if not formula:
        return 0
    return parse_formula_tokens(_neutral_part(formula)).get(symbol, 0)


@dataclass(frozen=True)
class SiliconLine:
    """The +1 peak a silicon reading of an ion's M+2 line implies.

    :ivar mz_offset: The centre of the peak the 29Si line falls in, above the
        ion's monoisotopic line.
    :ivar ratio: That peak's height over the monoisotopic one under the
        silicon reading: the 29Si line and the ion's own lines it merges with.
    :ivar own_ratio: The 29Si line's own share.
    :ivar resolved: Whether the 29Si line carries at least
        :data:`~diagnostic_isotopologues.MIN_DOMINANCE` of that peak.
    """

    mz_offset: float
    ratio: float
    own_ratio: float
    resolved: bool

    def to_json(self) -> dict:
        return {
            "label": SILICON_LINE,
            "mz_offset": round(self.mz_offset, 6),
            "ratio": round(self.ratio, 6),
            "own_ratio": round(self.own_ratio, 6),
            "resolved": self.resolved,
        }


def silicon_line(
    ion_formula: str, m2_ratio: float, resolution: float | None = None
) -> SiliconLine | None:
    """Where a silicon reading of an ion's M+2 line puts its 29Si line.

    Read as 30Si, a line at ``m2_ratio`` of the parent is ``m2_ratio / (a30 /
    a28)`` silicons, whose 29Si line is :data:`SI29_PER_SI30` times the line,
    :data:`SI29_SPACING` up. At the ion's resolving power it merges with the
    ion's own +1 lines (the 13C line, 3.8 mDa above it) as the fit score
    merges lines, so the peak the batch would show there is that group.

    :param ion_formula: The ion, with its charge.
    :param m2_ratio: The M+2 line's height over the parent's, as the batch
        reads it.
    :param resolution: The resolving power the lines are read at, or None,
        which merges nothing.
    :return: The peak, or None for an ion with no charge or no prediction.
    """
    charge = ion_charge(ion_formula)
    if not charge or not m2_ratio > 0:
        return None
    try:
        mzs, intensities, labels = predict_isotopes(
            _neutral_part(ion_formula), charge, None, PREDICTION_DEPTH
        )
    except Exception:  # noqa: BLE001 - a formula the predictor refuses has none
        return None
    labels = list(labels)
    if "M0" not in labels:
        return None
    mzs = np.asarray(mzs, dtype=float)
    intensities = np.asarray(intensities, dtype=float)
    mono = labels.index("M0")
    share = float(m2_ratio) * SI29_PER_SI30 * float(intensities[mono])
    mzs = np.append(mzs, mzs[mono] + SI29_SPACING / abs(charge))
    intensities = np.append(intensities, share)
    groups = _groups(mzs, intensities, resolution)
    group_of = {index: members for members in groups for index in members}
    line = mzs.size - 1
    members = group_of[line]
    mono_members = group_of[mono]
    group_share = float(intensities[members].sum())
    return SiliconLine(
        mz_offset=_centre(mzs, intensities, members)
        - _centre(mzs, intensities, mono_members),
        ratio=group_share / float(intensities[mono_members].sum()),
        own_ratio=share / float(intensities[mono]),
        resolved=share >= MIN_DOMINANCE * group_share,
    )


def _groups(
    mzs: np.ndarray, intensities: np.ndarray, resolution: float | None
) -> list[list[int]]:
    """The lines one peak of the file's width holds, as index lists."""
    if resolution is None or not resolution > 0 or mzs.size < 2:
        return [[index] for index in range(mzs.size)]
    return [
        list(map(int, members))
        for members in group_unresolved_lines(
            mzs, intensities, lambda _mz: float(resolution)
        )
    ]


def _centre(mzs: np.ndarray, intensities: np.ndarray, members: list[int]) -> float:
    weights = intensities[members]
    total = float(weights.sum())
    if total <= 0:
        return float(mzs[members].mean())
    return float(np.sum(mzs[members] * weights) / total)


def offset_error_ppm(
    offsets: Iterable[float], predicted: float, mz: float
) -> float | None:
    """How far a line sits from its predicted spacing: the median of its
    offsets from the parent, one per sample, less the predicted offset, in ppm
    of the parent's m/z. None where no offset was read."""
    values = [float(o) for o in offsets if o is not None and math.isfinite(o)]
    if not values or not mz > 0:
        return None
    return (statistics.median(values) - float(predicted)) / float(mz) * 1e6


def silicon_in_window(
    offset: float, charge: int | None, mz: float, window_ppm: float
) -> bool:
    """Whether a line at ``offset`` above its parent could be a 30Si line: it
    sits within the window of the 30Si spacing, so its position cannot tell
    the two (from m/z 206 up at 1 ppm, the two spacings being 0.21 mDa
    apart).

    :param offset: The line's median offset from the parent, in Da of m/z.
    :param charge: The ion's charge.
    :param mz: The parent's m/z.
    :param window_ppm: The batch's position window.
    """
    if not charge or not mz > 0:
        return False
    return abs(offset - SI30_SPACING / abs(charge)) / mz * 1e6 <= window_ppm


@dataclass(frozen=True)
class SiliconReading:
    """A chlorine lock's silicon test: the 29Si line a silicon reading
    implies, and how the batch holds it.

    :ivar line: The peak the silicon reading predicts.
    :ivar batch_peak_id: The batch peak standing there, or None.
    :ivar track: Its height read across the batch against the line's ratio,
        as step 4.2c reads a stamp; None where no batch peak stands there.
    """

    line: SiliconLine
    batch_peak_id: str | None = None
    track: TrackReading | None = None

    @property
    def verdict(self) -> str:
        """``stamped`` where the batch holds the 29Si line, ``untestable``
        where it cannot say, ``not_stamped`` where the line is not there at
        that height or not there at all."""
        if self.track is None:
            return VERDICT_NOT_STAMPED
        return self.track.verdict

    def to_json(self) -> dict:
        return {
            **self.line.to_json(),
            "batch_peak_id": self.batch_peak_id,
            **(self.track.to_json() if self.track is not None else {}),
            "verdict": self.verdict,
        }


@dataclass(frozen=True)
class HalogenLock:
    """The halogen lock of one ion.

    :ivar element: ``Cl`` or ``Br``.
    :ivar label: The heavy isotope line read (``37Cl``).
    :ivar n_ion: The element's atoms in the ion.
    :ivar n_neutral: Those in the neutral; the rest are the reagent's.
    :ivar batch_peak_id: The batch peak the line stands at, or None.
    :ivar judged: The samples step 4.2c could judge the line in.
    :ivar holds: Those whose ratio held.
    :ivar share: The share holding; None on none.
    :ivar offset_error_ppm: The line's median offset from the parent less the
        predicted one, in ppm of the parent's m/z.
    :ivar window_ppm: The batch's position window it was read against.
    :ivar silicon: The silicon test, where it ran.
    :ivar verdict: :data:`LOCK_LOCKED`, :data:`LOCK_SILICON`,
        :data:`LOCK_NOT_LOCKED` or :data:`LOCK_UNTESTABLE`.
    :ivar reason: Why it is not locked, or untestable; else None.
    """

    element: str
    label: str
    n_ion: int
    n_neutral: int
    batch_peak_id: str | None
    judged: int
    holds: int
    share: float | None
    offset_error_ppm: float | None
    window_ppm: float
    verdict: str
    reason: str | None = None
    silicon: SiliconReading | None = None

    @property
    def locked(self) -> bool:
        return self.verdict == LOCK_LOCKED

    def to_json(self) -> dict:
        return {
            "element": self.element,
            "label": self.label,
            "n_ion": self.n_ion,
            "n_neutral": self.n_neutral,
            "batch_peak_id": self.batch_peak_id,
            "judged": self.judged,
            "holds": self.holds,
            "share": None if self.share is None else round(self.share, 4),
            "offset_error_ppm": (
                None
                if self.offset_error_ppm is None
                else round(self.offset_error_ppm, 3)
            ),
            "window_ppm": round(self.window_ppm, 3),
            "silicon": None if self.silicon is None else self.silicon.to_json(),
            "verdict": self.verdict,
            "reason": self.reason,
        }


def read_halogen_lock(
    element: str,
    entry: dict,
    *,
    n_ion: int,
    n_neutral: int,
    both_halogens: bool,
    offset_error: float | None,
    window_ppm: float,
    silicon: SiliconReading | None = None,
) -> HalogenLock:
    """Whether an ion's halogen line locks its element.

    In order: an ion carrying both halogens is not read; then the stamping's
    reading of the line (step 4.2c's record entry) - unresolved or on too few
    samples is untestable, a line no batch peak stands at, one whose ratio did
    not hold, or one another parent took is not locked; a stamped line whose
    offset was read in no sample is untestable, one whose offset is outside
    the window is not locked, one within it is locked; and a chlorine lock
    whose silicon test ran is silicon's where the batch holds the 29Si line,
    untestable where the test could not tell.

    :param element: ``Cl`` or ``Br``.
    :param entry: The parent's diagnostic isotopologues record entry for the
        element's line: ``batch_peak_id``, ``resolved``, ``judged``,
        ``holds``, ``share``, ``verdict``, ``stamped``.
    :param n_ion: The element's atoms in the ion.
    :param n_neutral: Those in the neutral.
    :param both_halogens: Whether the ion carries both chlorine and bromine.
    :param offset_error: :func:`offset_error_ppm` over the samples the line
        was stamped in, or None where none of them still holds both lines.
    :param window_ppm: The batch's position window.
    :param silicon: The silicon test, where the line's position could be a
        30Si line's (:func:`silicon_in_window`); None otherwise.
    """
    line = entry.get("batch_peak_id")
    share = entry.get("share")

    def lock(verdict: str, reason: str | None = None) -> HalogenLock:
        return HalogenLock(
            element=element,
            label=HALOGEN_LINES[element],
            n_ion=n_ion,
            n_neutral=n_neutral,
            batch_peak_id=line,
            judged=int(entry.get("judged") or 0),
            holds=int(entry.get("holds") or 0),
            share=None if share is None else float(share),
            offset_error_ppm=offset_error,
            window_ppm=window_ppm,
            verdict=verdict,
            reason=reason,
            silicon=silicon,
        )

    if both_halogens:
        return lock(LOCK_UNTESTABLE, REASON_BOTH_HALOGENS)
    if not entry.get("resolved", True):
        return lock(LOCK_UNTESTABLE, REASON_UNRESOLVED)
    if not line:
        return lock(LOCK_NOT_LOCKED, REASON_NOT_FOUND)
    verdict = entry.get("verdict")
    if verdict == VERDICT_UNTESTABLE:
        return lock(LOCK_UNTESTABLE, entry.get("reason") or REASON_FEW_SPECTRA)
    if verdict != VERDICT_STAMPED:
        return lock(LOCK_NOT_LOCKED, REASON_RATIO)
    if not entry.get("stamped"):
        return lock(LOCK_NOT_LOCKED, REASON_ANOTHER_PARENT)
    if offset_error is None:
        # No spacing read: the batch cannot say where the line sits.
        return lock(LOCK_UNTESTABLE, REASON_NO_OFFSET)
    if abs(offset_error) > window_ppm:
        return lock(LOCK_NOT_LOCKED, REASON_OFFSET)
    if silicon is not None:
        if silicon.verdict == VERDICT_STAMPED:
            return lock(LOCK_SILICON)
        if silicon.verdict == VERDICT_UNTESTABLE:
            return lock(LOCK_UNTESTABLE, REASON_SILICON_UNTESTABLE)
    return lock(LOCK_LOCKED)


# --- the label twin -----------------------------------------------------------

#: The line a labelled ion's twin is, as the envelope predictor labels a
#: labelled atom's light remainder.
TWIN_LINE = "14N"

#: Twin verdicts.
TWIN_PRESENT = "present"
TWIN_ABSENT = "absent"
TWIN_UNTESTABLE = "untestable"

#: The share of the samples that can tell a twin must be missing from for it
#: to be absent: :data:`~diagnostic_isotopologues.TRACK_SHARE`, the share a
#: stamp's ratio must hold in, the reference's own bar for an absent line
#: ("absent in >= 80 % of its spectra", its rule K).
ABSENT_SHARE = TRACK_SHARE


def core_factor(pairs: Iterable[tuple[float, float]], ratio: float) -> float | None:
    """How far a labelled reagent ion's twin runs from the share the setup's
    purity gives it: the median, over the samples holding both, of the twin's
    height over the ion's, over that share. None under
    :data:`~diagnostic_isotopologues.MIN_JUDGED` such samples.

    :param pairs: ``(ion, twin)`` intensities, one per sample holding both.
    :param ratio: The twin's share at the setup's purity
        (:func:`twin_isotopologue`).
    """
    if not ratio > 0:
        return None
    factors = [
        twin / ion / ratio
        for ion, twin in pairs
        if ion is not None and twin is not None and ion > 0 and twin > 0
    ]
    if len(factors) < MIN_JUDGED:
        return None
    return statistics.median(factors)


def impurity_factor(factors: Iterable[float | None]) -> float | None:
    """The batch's label impurity as a factor of the setup's: the median of its
    labelled reagent ions' :func:`core_factor`; None where none measured one."""
    found = [f for f in factors if f is not None and math.isfinite(f) and f > 0]
    return statistics.median(found) if found else None


def twin_isotopologue(
    ion_formula: str, resolution: float | None = None
) -> DiagnosticIsotopologue | None:
    """A labelled ion's 14N twin: its offset (one unit down) and its share of
    the ion at the label's purity in effect
    (``custom_elements.labelled_purity``); None for an ion with no labelled
    atom."""
    if not ion_formula or "^" not in ion_formula:
        return None
    found = predict_diagnostic_isotopologues(ion_formula, resolution, (TWIN_LINE,))
    return found[0] if found else None


@dataclass(frozen=True)
class TwinSample:
    """One sample's reading of a labelled ion and its twin.

    :ivar labelled: The labelled ion's intensity.
    :ivar twin: The twin's batch peak's intensity there, or None.
    :ivar labelled_snr: The labelled ion's signal-to-noise, or None.
    :ivar twin_snr: The twin's, or None.
    :ivar tolerance: The envelope tolerance the sample's run fitted.
    """

    labelled: float
    twin: float | None = None
    labelled_snr: float | None = None
    twin_snr: float | None = None
    tolerance: EnvelopeTolerance = DEFAULT_TOLERANCE


def _usable(snr: float | None) -> float | None:
    if snr is None:
        return None
    value = float(snr)
    return value if math.isfinite(value) and value > 0 else None


def can_tell(sample: TwinSample, ratio: float) -> bool:
    """Whether a sample would show the twin: its expected signal-to-noise,
    the labelled ion's times the twin's share, reaches the threshold at which
    the sample's run charges an absent line (step 3.5b's ``detect_snr``,
    today's default where it fitted none). A sample with no noise read cannot
    place it."""
    snr = _usable(sample.labelled_snr)
    if snr is None or not sample.labelled > 0:
        return False
    detect = sample.tolerance.detect_snr
    return snr * ratio >= (DEFAULT_DETECT_SNR if detect is None else detect)


def twin_present(sample: TwinSample, ratio: float) -> bool:
    """Whether the twin stands in a sample at the label's purity: its height
    over the labelled ion's is not under the admitted band of the twin's share
    by more than :data:`~diagnostic_isotopologues.HOLD_WIDTHS` of its width.
    Above the band is no miss: ambient 14N adds to the twin."""
    if sample.twin is None or not sample.twin > 0 or not sample.labelled > 0:
        return False
    reading = judge_ratio(
        sample.twin / sample.labelled,
        ratio,
        tolerance=sample.tolerance,
        parent_snr=sample.labelled_snr,
        line_snr=sample.twin_snr,
    )
    return reading.deviation >= -HOLD_WIDTHS


@dataclass(frozen=True)
class TwinTrack:
    """A labelled ion's twin read over the batch.

    :ivar judged: The samples that would show the twin (:func:`can_tell`).
    :ivar present: Those it stands in at the label's purity.
    :ivar excess: The median, over the samples holding both, of the twin's
        height over the labelled ion's, as a multiple of the purity's share:
        1 where the impurity explains the 14N line, above where ambient 14N
        or another ion adds to it. None where no sample holds both.
    :ivar verdict: :data:`TWIN_PRESENT`, :data:`TWIN_ABSENT` or
        :data:`TWIN_UNTESTABLE`.
    :ivar reason: Why it is untestable; else None.
    """

    judged: int
    present: int
    excess: float | None
    verdict: str
    reason: str | None = None

    @property
    def share(self) -> float | None:
        return self.present / self.judged if self.judged else None

    def to_json(self) -> dict:
        share = self.share
        return {
            "judged": self.judged,
            "present": self.present,
            "share": None if share is None else round(share, 4),
            "excess": None if self.excess is None else round(self.excess, 4),
            "verdict": self.verdict,
            "reason": self.reason,
        }


def read_twin(samples: Sequence[TwinSample], ratio: float) -> TwinTrack:
    """A labelled ion's twin over the batch.

    :param samples: One per sample holding the labelled ion.
    :param ratio: The twin's predicted share of the labelled ion
        (:func:`twin_isotopologue`).
    """
    both = [
        s.twin / s.labelled / ratio
        for s in samples
        if s.twin is not None and s.twin > 0 and s.labelled > 0 and ratio > 0
    ]
    excess = statistics.median(both) if both else None
    judged = [sample for sample in samples if can_tell(sample, ratio)]
    present = sum(1 for sample in judged if twin_present(sample, ratio))
    if len(judged) < MIN_JUDGED:
        return TwinTrack(
            len(judged), present, excess, TWIN_UNTESTABLE, REASON_FEW_SPECTRA
        )
    missing = len(judged) - present
    verdict = TWIN_ABSENT if missing >= ABSENT_SHARE * len(judged) else TWIN_PRESENT
    return TwinTrack(len(judged), present, excess, verdict)


# --- the neutral pair ---------------------------------------------------------

#: The neutral pairs a batch reads: two channels of one neutral, both present
#: and co-varying, establish it. The uronium source's protonated and
#: urea-clustered ions (the uronium profile's detection channel and its
#: protonation), by the notation the mechanism table compares them by.
NEUTRAL_PAIRS = ((mechanism_key("[M+H]+"), mechanism_key("[M+CH4N2O+H]+")),)

#: Pair verdicts.
PAIR_ESTABLISHED = "established"
PAIR_NOT_ESTABLISHED = "not_established"
PAIR_UNTESTABLE = "untestable"

#: Why a pair is untestable where no co-variation reading names it.
REASON_NOT_READ = "not_read"


def pair_partner(notation: str | None) -> str | None:
    """The other channel of the neutral pair a mechanism is one side of, by
    its key; None for a mechanism no pair names."""
    if not notation:
        return None
    key = mechanism_key(notation)
    for first, second in NEUTRAL_PAIRS:
        if key == first:
            return second
        if key == second:
            return first
    return None


def read_neutral_pair(reading: dict | None) -> tuple[str, str | None]:
    """A pair's verdict from the co-variation reading of its two channels.

    :param reading: The co-variation reading of the two (``verdict``,
        ``reason``, as ``coherence.CoVariation.to_json`` writes it), or None
        where none names the pair.
    :return: The verdict and, for an untestable pair, why.
    """
    if not isinstance(reading, dict) or not reading.get("verdict"):
        return PAIR_UNTESTABLE, REASON_NOT_READ
    verdict = reading["verdict"]
    if verdict == "co_varying":
        return PAIR_ESTABLISHED, None
    if verdict == "not_co_varying":
        return PAIR_NOT_ESTABLISHED, None
    return PAIR_UNTESTABLE, reading.get("reason")


def thresholds() -> dict:
    """The constants a reading was made under, as a run records them."""
    return {
        "absent_share": ABSENT_SHARE,
        "hold_widths": HOLD_WIDTHS,
        "min_judged": MIN_JUDGED,
        "track_share": TRACK_SHARE,
        "default_detect_snr": DEFAULT_DETECT_SNR,
        "si29_per_si30": round(SI29_PER_SI30, 6),
    }


__all__ = [
    "ABSENT_SHARE",
    "CL37_SPACING",
    "HALOGEN_LINES",
    "LOCK_LOCKED",
    "LOCK_NOT_LOCKED",
    "LOCK_SILICON",
    "LOCK_UNTESTABLE",
    "NEUTRAL_PAIRS",
    "PAIR_ESTABLISHED",
    "PAIR_NOT_ESTABLISHED",
    "PAIR_UNTESTABLE",
    "REASON_ANOTHER_PARENT",
    "REASON_BOTH_HALOGENS",
    "REASON_NOT_FOUND",
    "REASON_NOT_READ",
    "REASON_NO_OFFSET",
    "REASON_OFFSET",
    "REASON_RATIO",
    "REASON_SILICON_UNTESTABLE",
    "SI29_PER_SI30",
    "SI29_SPACING",
    "SI30_SPACING",
    "SILICON_LINE",
    "TWIN_ABSENT",
    "TWIN_LINE",
    "TWIN_PRESENT",
    "TWIN_UNTESTABLE",
    "HalogenLock",
    "SiliconLine",
    "SiliconReading",
    "TwinSample",
    "TwinTrack",
    "can_tell",
    "core_factor",
    "element_count",
    "impurity_factor",
    "offset_error_ppm",
    "pair_partner",
    "read_halogen_lock",
    "read_neutral_pair",
    "read_twin",
    "silicon_in_window",
    "silicon_line",
    "thresholds",
    "twin_isotopologue",
    "twin_present",
]
