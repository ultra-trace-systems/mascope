"""The batch isotope checks on the batch ledger: step 4.2's sixth rider.

Three facts only a batch can state of a batch peak's reading, each read off
what the ledger already holds - the lines step 4.2c stamped, the members of
every batch peak, the co-variation step 4.2 read - through
``mascope_tools.composition.batch_checks``. "Read batch checks" records them
on the anchor:

- **The halogen lock** (``halogen_lock``), on a committed monoisotopic batch
  peak whose ion carries chlorine or bromine: its 37Cl or 81Br line, as the
  isotopologue stamping read it, stamped across the track and sitting at the
  exact spacing - the median offset of the line's members from the parent's,
  over the samples it was stamped in, within the partition's position window
  (step 4.2f's, four times the mass scatter, 1 ppm at least). Where that
  window cannot tell a 30Si line from the 37Cl one, the 29Si line a silicon
  reading implies is looked for at its batch peak and judged across the
  track as a stamp is: held, the lock is silicon's.
- **The label twin** (``label_twin``), on a labelled ion - a reagent core or
  an analyte cluster carrying the setup's labelled atom: its 14N twin one unit
  down at the label's impurity share - the ionization setup's
  ``label_purity``, times the factor the partition's own labelled reagent
  ions read it at where they hold their twins in three samples or more -,
  judged in each sample whose run would charge it absent (3.5b's detection
  threshold, the labelled ion's signal-to-noise read off the sample's peak
  list): present, absent or untestable, with the twin's batch peak and how
  far it runs above the impurity's share.
- **The neutral pair** (``neutral_pair``), on each of a neutral's uronium
  channels, protonated and urea-clustered: the co-variation reading's verdict
  on the two (established where they co-vary), with its r and the spectra
  counted.

The anchor carries them under ``provenance.batch_checks``
(``batch_peaks.BATCH_CHECKS_KEY``) with the reading they were read for, its
ion, formula and mechanism. A new reading replaces every record an earlier one
left. The consensus keeps a record while it names that reading, and a fact
read off another run's record - a lock off the stamping's, a pair off the
co-variation reading's - is served only while that record stands
(``batch_peaks.standing_batch_checks_record``). The reading runs outside the
fold lock and writes under it, so a batch peak whose reading moved in between,
or that went, gets no record, and a fact naming a batch peak that went is
left out (:func:`still_read`).

Nothing moves: no tier, reason, member or per-sample run. The facts are
display-only until their reliability is read on the bench (decision 35's
posture); the step that counts them follows on its own.

Design: ``docs/dev/assignment_quality_plan.md``, step 4.2, the sixth rider.
"""

from __future__ import annotations

import asyncio
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from sqlalchemy import select

from mascope_backend.api.lib.api_features import api_controller_background_task
from mascope_backend.api.new.peak_assignments.batch_isotopologues import (
    CentreIndex,
    Member,
    Partition,
    Trace,
    is_parent,
    partition_of,
    read_noise,
    trace_centre,
)
from mascope_backend.api.new.peak_assignments.batch_peaks import (
    BATCH_CHECK_FACTS,
    BATCH_CHECKS_KEY,
    ROLE_REAGENT,
    batch_checks_record_reads,
    mz_from_delta,
    resolution_from_tol_ppm,
    role_name,
    stamped_isotopologue_of_record,
    standing_co_variation_record,
    standing_diagnostic_isotopologues_record,
)
from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
    _acquire_batch_fold_lock,
)
from mascope_backend.api.new.peak_assignments.batch_runs import (
    ACTION_BATCH_CHECKS,
    complete_run,
    fail_run,
    start_run,
    touch_ledger,
)
from mascope_backend.api.new.peak_assignments.cross_channel import neutral_key
from mascope_backend.api.new.peak_assignments.envelope_fit import (
    recorded_envelope_tolerances,
)
from mascope_backend.api.new.peak_assignments.tiers import TIER_UNASSIGNED
from mascope_backend.db import (
    BatchPeak,
    BatchPeakOccurrence,
    IonizationMechanism,
    IonizationMode,
    async_session,
)
from mascope_tools.composition.batch_checks import (
    HALOGEN_LINES,
    LOCK_LOCKED,
    LOCK_NOT_LOCKED,
    LOCK_SILICON,
    PAIR_ESTABLISHED,
    PAIR_NOT_ESTABLISHED,
    TWIN_ABSENT,
    TWIN_PRESENT,
    HalogenLock,
    SiliconLine,
    SiliconReading,
    TwinSample,
    core_factor,
    element_count,
    impurity_factor,
    offset_error_ppm,
    pair_partner,
    read_halogen_lock,
    read_neutral_pair,
    read_twin,
    silicon_in_window,
    silicon_line,
    thresholds,
    twin_isotopologue,
)
from mascope_tools.composition.custom_elements import labelled_purity
from mascope_tools.composition.diagnostic_isotopologues import (
    DiagnosticIsotopologue,
    ion_charge,
    judge_ratio,
    read_track,
)
from mascope_tools.composition.envelope_tolerance import (
    DEFAULT_TOLERANCE,
    EnvelopeTolerance,
)
from mascope_tools.composition.mechanism_notation import mechanism_key
from mascope_tools.composition.merged_isotopologues import (
    mass_scatter_ppm,
    position_window_ppm,
)


def _monoisotopic(anchor: Any) -> bool:
    """Whether an anchor reads an ion's monoisotopic line: no family link, and
    an ion that names no isotope in brackets (``[13C]C9H15+``)."""
    ion = anchor.consensus_ion_formula
    return bool(ion) and "[" not in ion and not anchor.isotopologue_of


def is_labelled_ion(anchor: Any) -> bool:
    """Whether an anchor reads a labelled ion whose twin is read: monoisotopic,
    carrying a labelled atom (``^N``), and either the reagent's own ion or an
    analyte at a tier that commits its formula."""
    if not _monoisotopic(anchor) or "^" not in anchor.consensus_ion_formula:
        return False
    if anchor.consensus_role == ROLE_REAGENT:
        return True
    return (
        not anchor.consensus_role
        and bool(anchor.consensus_formula)
        and anchor.consensus_tier != TIER_UNASSIGNED
    )


def _positive(intensity: Optional[float]) -> bool:
    return intensity is not None and intensity > 0


# --- the plan -----------------------------------------------------------------


@dataclass
class LockPlan:
    """A parent's halogen lock, read before the silicon test.

    :ivar stamped_in: The samples the line was stamped in, holding both.
    :ivar median_offset: The line's median offset from the parent there, in
        Da of m/z; None where none was read.
    :ivar silicon: The 29Si line a silicon reading implies, where the lock
        reaches the silicon test; with ``silicon_trace``, the batch peak there.
    """

    parent: Trace
    element: str
    entry: dict
    n_ion: int
    n_neutral: int
    both_halogens: bool
    window_ppm: float
    stamping_run_id: Optional[str]
    line: Optional[Trace] = None
    stamped_in: list[str] = field(default_factory=list)
    offset_error: Optional[float] = None
    median_offset: Optional[float] = None
    silicon: Optional[SiliconLine] = None
    silicon_trace: Optional[Trace] = None

    def read(self, reading: Optional[SiliconReading] = None) -> HalogenLock:
        return read_halogen_lock(
            self.element,
            self.entry,
            n_ion=self.n_ion,
            n_neutral=self.n_neutral,
            both_halogens=self.both_halogens,
            offset_error=self.offset_error,
            window_ppm=self.window_ppm,
            silicon=reading,
        )


@dataclass
class TwinPlan:
    """A labelled ion's twin: where it is predicted, the batch peak there.

    :ivar purity: The label's purity it was predicted at; None for the
        library's default.
    :ivar factor: The partition's label impurity as a factor of that purity's,
        read off its labelled reagent ions; None where none measured it.
    """

    labelled: Trace
    twin: DiagnosticIsotopologue
    mz: float
    purity: Optional[float]
    trace: Optional[Trace] = None
    factor: Optional[float] = None

    @property
    def read_ratio(self) -> float:
        """The share the twin is read at: the setup's purity's, times the
        batch's own factor where its reagent ions measured one."""
        return self.twin.ratio * (self.factor if self.factor is not None else 1.0)

    def pairs(self) -> list[tuple[float, float]]:
        """The labelled ion's and the twin's intensities, per sample holding
        both."""
        if self.trace is None:
            return []
        return [
            (member.intensity, self.trace.members[sample].intensity)
            for sample, member in sorted(self.labelled.members.items())
            if sample in self.trace.members
        ]


@dataclass
class Plan:
    """What a reading reads, before the noise is read.

    :ivar pairs: Anchor id -> its neutral pair fact, read off the co-variation
        record alone.
    :ivar counts: Partition -> the counts read while planning.
    """

    traces: dict[str, Trace] = field(default_factory=dict)
    locks: list[LockPlan] = field(default_factory=list)
    twins: list[TwinPlan] = field(default_factory=list)
    pairs: dict[str, dict] = field(default_factory=dict)
    counts: dict[Partition, dict] = field(default_factory=dict)


#: The counts a partition keeps, and the run's summary sums.
_COUNTS = (
    "halogen_parents",
    "halogen_unstamped",
    "locked",
    "locked_cl",
    "locked_br",
    "silicon",
    "not_locked",
    "halogen_untestable",
    "labelled_ions",
    "twin_present",
    "twin_absent",
    "twin_untestable",
    "pair_channels",
    "pair_channels_unread",
    "pairs",
    "pairs_established",
    "pairs_not_established",
    "pairs_untestable",
)


def _empty_counts() -> dict:
    return {key: 0 for key in _COUNTS}


def _entry(record: dict, label: str) -> Optional[dict]:
    """A diagnostic isotopologues record's entry for one isotope line."""
    for entry in record.get("predicted") or []:
        if isinstance(entry, dict) and entry.get("label") == label:
            return entry
    return None


def halogens_of(anchor: Any) -> dict[str, int]:
    """The halogens an anchor's ion carries, bromine first, with their counts."""
    ion = anchor.consensus_ion_formula
    counts = {element: element_count(ion, element) for element in ("Br", "Cl")}
    return {element: n for element, n in counts.items() if n > 0}


def plan_lock(
    parent: Trace,
    record: dict,
    by_id: dict[str, Trace],
    index: CentreIndex,
    window_ppm: float,
) -> LockPlan:
    """A halogen parent's lock, its silicon test planned where it is reached.

    An ion carrying both halogens is read once, as its bromine line: the M+2
    peak holds both lines, and :func:`read_halogen_lock` reads it untestable.

    :param parent: A parent whose ion carries chlorine or bromine
        (:func:`halogens_of`).
    :param record: Its standing diagnostic isotopologues record (step 4.2c).
    :param by_id: Every anchor's trace.
    :param index: The partition's traces by centre.
    :param window_ppm: The partition's position window.
    """
    anchor = parent.anchor
    ion = anchor.consensus_ion_formula
    held = halogens_of(anchor)
    element = next(iter(held))
    entry = _entry(record, HALOGEN_LINES[element]) or {
        "batch_peak_id": None,
        "resolved": False,
    }
    plan = LockPlan(
        parent=parent,
        element=element,
        entry=entry,
        n_ion=held[element],
        n_neutral=element_count(anchor.consensus_formula, element),
        both_halogens=len(held) > 1,
        window_ppm=window_ppm,
        stamping_run_id=record.get("batch_peak_run_id"),
        line=by_id.get(entry.get("batch_peak_id") or ""),
    )
    if plan.line is not None and entry.get("stamped"):
        stamp = stamped_isotopologue_of_record(plan.line.anchor) or {}
        stamps = (
            stamp.get("stamps")
            if stamp.get("batch_peak_id") == anchor.batch_peak_id
            else None
        )
        plan.stamped_in = sorted(
            sample
            for sample in (stamps or {})
            if sample in parent.members and sample in plan.line.members
        )
        offsets = [
            plan.line.members[s].mz - parent.members[s].mz for s in plan.stamped_in
        ]
        plan.median_offset = statistics.median(offsets) if offsets else None
        plan.offset_error = offset_error_ppm(
            offsets, entry.get("mz_offset") or 0.0, parent.centre
        )
    if (
        element == "Cl"
        and plan.read().verdict == LOCK_LOCKED
        and plan.median_offset is not None
        and silicon_in_window(
            plan.median_offset, ion_charge(ion), parent.centre, window_ppm
        )
    ):
        ratios = [
            plan.line.members[s].intensity / parent.members[s].intensity
            for s in plan.stamped_in
            if _positive(plan.line.members[s].intensity)
            and _positive(parent.members[s].intensity)
        ]
        if ratios:
            plan.silicon = silicon_line(
                ion,
                statistics.median(ratios),
                resolution_from_tol_ppm(anchor.mz_tol_ppm),
            )
        if plan.silicon is not None:
            plan.silicon_trace = index.find(
                parent.centre + plan.silicon.mz_offset, exclude=anchor.batch_peak_id
            )
    return plan


def plan_twin(
    labelled: Trace, index: CentreIndex, purity: Optional[float]
) -> Optional[TwinPlan]:
    """A labelled ion's twin and the batch peak at it; None where the ion
    predicts none."""
    anchor = labelled.anchor
    with labelled_purity(purity):
        twin = twin_isotopologue(
            anchor.consensus_ion_formula, resolution_from_tol_ppm(anchor.mz_tol_ppm)
        )
    if twin is None:
        return None
    mz = labelled.centre + twin.mz_offset
    return TwinPlan(
        labelled=labelled,
        twin=twin,
        mz=mz,
        purity=purity,
        trace=index.find(mz, exclude=anchor.batch_peak_id),
    )


def pair_channel(anchor: Any, notation: Optional[str]) -> Optional[str]:
    """The other channel of the neutral pair an anchor reads one side of, by
    its key: a committed monoisotopic, unclaimed reading through a mechanism a
    pair names. None for any other anchor."""
    partner_key = pair_partner(notation)
    if (
        partner_key is None
        or not _monoisotopic(anchor)
        or anchor.consensus_role
        or not anchor.consensus_formula
        or anchor.consensus_tier == TIER_UNASSIGNED
    ):
        return None
    return partner_key


def pair_fact(
    anchor: Any, notation: Optional[str], partner_key: str, by_id: dict[str, Trace]
) -> Optional[dict]:
    """A uronium channel's neutral pair fact, off its co-variation record: the
    strongest partner the record names through the pair's other channel. None
    where no standing record names one.

    :param anchor: One channel of the pair (:func:`pair_channel`).
    :param notation: Its mechanism's notation.
    :param partner_key: The pair's other channel, by its key.
    :param by_id: Every anchor's trace.
    """
    record = standing_co_variation_record(anchor)
    if record is None:
        return None
    partners = [
        partner
        for partner in record.get("partners") or []
        if isinstance(partner, dict)
        and mechanism_key(partner.get("channel") or "") == partner_key
    ]
    if not partners:
        return None
    best = partners[0]
    verdict, reason = read_neutral_pair(best)
    partner = by_id.get(best.get("batch_peak_id") or "")
    return {
        "channel": notation,
        "n_present": len(by_id[anchor.batch_peak_id].members),
        "partner_batch_peak_id": best.get("batch_peak_id"),
        "partner_channel": best.get("channel"),
        "partner_ion_formula": best.get("ion_formula"),
        "partner_n_present": len(partner.members) if partner else None,
        "r": best.get("r"),
        "n_spectra": best.get("n_spectra"),
        "verdict": verdict,
        "reason": reason,
        "co_variation_run_id": record.get("batch_peak_run_id"),
    }


def plan_pairs(
    traces: list[Trace],
    notation_by_id: dict[str, str],
    by_id: dict[str, Trace],
    plan: Plan,
    counts: dict,
) -> None:
    """One partition's neutral pair facts, into ``plan.pairs``.

    A channel is read where its neutral stands in the partition through the
    pair's other channel too. Where no co-variation record pairs the two, the
    channel is counted unread (``pair_channels_unread``): *Read co-variation*
    has not run since, or its reading moved.
    """
    channels: dict[str, set[str]] = defaultdict(set)
    sides = []
    for trace in traces:
        anchor = trace.anchor
        notation = notation_by_id.get(anchor.ionization_mechanism_id or "")
        partner_key = pair_channel(anchor, notation)
        if partner_key is None:
            continue
        neutral = neutral_key(anchor.consensus_formula)
        channels[neutral].add(mechanism_key(notation))
        sides.append((anchor, notation, partner_key, neutral))
    for anchor, notation, partner_key, neutral in sides:
        if partner_key not in channels[neutral]:
            continue
        fact = pair_fact(anchor, notation, partner_key, by_id)
        if fact is None:
            counts["pair_channels_unread"] += 1
        else:
            plan.pairs[anchor.batch_peak_id] = fact


def plan_batch(
    anchors: Iterable[Any],
    members: dict[str, dict[str, Member]],
    notation_by_id: dict[str, str],
    purity_by_mode: dict[Optional[str], Optional[float]],
) -> Plan:
    """Every fact the batch can read, before the noise is read.

    :param anchors: The batch's anchors.
    :param members: Anchor id -> sample -> its member.
    :param notation_by_id: Mechanism id -> its notation.
    :param purity_by_mode: Ionization mode id -> its label purity.
    """
    plan = Plan()
    by_partition: dict[Partition, list[Trace]] = defaultdict(list)
    for anchor in sorted(anchors, key=lambda a: a.batch_peak_id):
        own = members.get(anchor.batch_peak_id, {})
        trace = Trace(anchor, own, trace_centre(anchor.mz, own.values()))
        plan.traces[anchor.batch_peak_id] = trace
        by_partition[partition_of(anchor)].append(trace)
    for part, traces in sorted(
        by_partition.items(), key=lambda item: (str(item[0][0]), str(item[0][1]))
    ):
        counts = plan.counts.setdefault(part, _empty_counts())
        scatter = mass_scatter_ppm(
            [member.mz for member in trace.members.values()] for trace in traces
        )
        window = position_window_ppm(scatter)
        # Recorded beside the counts, never summed: what the partition was read at.
        counts["scatter_ppm"] = None if scatter is None else round(scatter, 3)
        counts["window_ppm"] = round(window, 3)
        index = CentreIndex(traces)
        twins: list[TwinPlan] = []
        for trace in traces:
            anchor = trace.anchor
            if is_parent(anchor) and halogens_of(anchor):
                counts["halogen_parents"] += 1
                record = standing_diagnostic_isotopologues_record(anchor)
                if record is None:
                    # The stamping has not read this reading: nothing to lock.
                    counts["halogen_unstamped"] += 1
                else:
                    plan.locks.append(
                        plan_lock(trace, record, plan.traces, index, window)
                    )
            if is_labelled_ion(anchor):
                twin = plan_twin(
                    trace, index, purity_by_mode.get(anchor.ionization_mode_id)
                )
                if twin is not None:
                    twins.append(twin)
        # The label's impurity, read off the partition's own reagent ions.
        cores = [
            core_factor(twin.pairs(), twin.twin.ratio)
            for twin in twins
            if twin.labelled.anchor.consensus_role == ROLE_REAGENT
        ]
        factor = impurity_factor(cores)
        counts["impurity_factor"] = None if factor is None else round(factor, 4)
        counts["impurity_cores"] = sum(1 for f in cores if f is not None)
        for twin in twins:
            twin.factor = factor
        plan.twins.extend(twins)
        plan_pairs(traces, notation_by_id, plan.traces, plan, counts)
    return plan


def needed_peaks(plan: Plan) -> dict[str, set[str]]:
    """Sample -> the peaks whose noise the judging reads: every labelled ion's
    and its twin's, and a silicon test's parent and 29Si line."""
    needed: dict[str, set[str]] = defaultdict(set)
    for twin in plan.twins:
        for sample, member in twin.labelled.members.items():
            needed[sample].add(member.sample_peak_id)
            if twin.trace is not None and sample in twin.trace.members:
                needed[sample].add(twin.trace.members[sample].sample_peak_id)
    for lock in plan.locks:
        if lock.silicon_trace is None:
            continue
        for sample, member in lock.silicon_trace.members.items():
            if sample in lock.parent.members:
                needed[sample].update(
                    (member.sample_peak_id, lock.parent.members[sample].sample_peak_id)
                )
    return dict(needed)


# --- the judging --------------------------------------------------------------


def judge_silicon(
    lock: LockPlan,
    tolerances: dict[str, EnvelopeTolerance],
    snr: dict[str, dict[str, Optional[float]]],
) -> Optional[SiliconReading]:
    """A chlorine lock's silicon test across the track: the batch peak at the
    29Si line read in every sample holding it and the parent, as step 4.2c
    reads a stamp. None where the test was not reached."""
    if lock.silicon is None:
        return None
    trace = lock.silicon_trace
    if trace is None:
        return SiliconReading(lock.silicon)
    readings = []
    for sample, member in sorted(trace.members.items()):
        own = lock.parent.members.get(sample)
        if own is None or not (
            _positive(own.intensity) and _positive(member.intensity)
        ):
            continue
        noise = snr.get(sample, {})
        readings.append(
            judge_ratio(
                member.intensity / own.intensity,
                lock.silicon.ratio,
                tolerance=tolerances.get(sample, DEFAULT_TOLERANCE),
                parent_snr=noise.get(own.sample_peak_id),
                line_snr=noise.get(member.sample_peak_id),
            )
        )
    return SiliconReading(
        lock.silicon, trace.anchor.batch_peak_id, read_track(readings)
    )


def twin_samples(
    twin: TwinPlan,
    tolerances: dict[str, EnvelopeTolerance],
    snr: dict[str, dict[str, Optional[float]]],
) -> list[TwinSample]:
    """One reading per sample holding the labelled ion."""
    samples = []
    for sample, member in sorted(twin.labelled.members.items()):
        if not _positive(member.intensity):
            continue
        noise = snr.get(sample, {})
        found = twin.trace.members.get(sample) if twin.trace is not None else None
        samples.append(
            TwinSample(
                labelled=member.intensity,
                twin=found.intensity if found is not None else None,
                labelled_snr=noise.get(member.sample_peak_id),
                twin_snr=noise.get(found.sample_peak_id) if found else None,
                tolerance=tolerances.get(sample, DEFAULT_TOLERANCE),
            )
        )
    return samples


def _lock_fact(lock: LockPlan, read: HalogenLock) -> dict:
    return {
        **read.to_json(),
        "n_stamped_in": len(lock.stamped_in),
        "stamping_run_id": lock.stamping_run_id,
    }


def _twin_fact(twin: TwinPlan, samples: list[TwinSample]) -> dict:
    track = read_twin(samples, twin.read_ratio)
    other = twin.trace.anchor if twin.trace is not None else None
    return {
        **twin.twin.to_json(),
        "mz": round(twin.mz, 6),
        "purity": twin.purity,
        "impurity_factor": None if twin.factor is None else round(twin.factor, 4),
        "read_ratio": round(twin.read_ratio, 6),
        "batch_peak_id": other.batch_peak_id if other is not None else None,
        "twin_formula": other.consensus_formula if other is not None else None,
        "twin_ion_formula": other.consensus_ion_formula if other is not None else None,
        "twin_role": other.consensus_role if other is not None else None,
        **track.to_json(),
    }


def _record(anchor: Any, notation: Optional[str], run_id: Optional[str]) -> dict:
    return {
        "formula": anchor.consensus_formula,
        "ion_formula": anchor.consensus_ion_formula,
        "ionization_mechanism_id": anchor.ionization_mechanism_id,
        "channel": notation,
        "batch_peak_run_id": run_id,
    }


def read_batch(
    plan: Plan,
    notation_by_id: dict[str, str],
    tolerances: dict[str, EnvelopeTolerance],
    snr: dict[str, dict[str, Optional[float]]],
    *,
    batch_peak_run_id: Optional[str] = None,
) -> tuple[dict[str, dict], dict]:
    """Judge every planned fact and gather them per anchor.

    :param plan: :func:`plan_batch`.
    :param notation_by_id: Mechanism id -> its notation.
    :param tolerances: Sample -> the envelope tolerance its run fitted.
    :param snr: Sample -> peak -> its signal-to-noise, where read.
    :param batch_peak_run_id: The run doing it, for the records.
    :return: Anchor id -> its record, and the run's summary.
    """
    records: dict[str, dict] = {}

    def record_of(anchor: Any) -> dict:
        return records.setdefault(
            anchor.batch_peak_id,
            _record(
                anchor,
                notation_by_id.get(anchor.ionization_mechanism_id or ""),
                batch_peak_run_id,
            ),
        )

    for lock in plan.locks:
        read = lock.read(judge_silicon(lock, tolerances, snr))
        counts = plan.counts[partition_of(lock.parent.anchor)]
        if read.verdict == LOCK_LOCKED:
            counts["locked"] += 1
            counts[f"locked_{read.element.lower()}"] += 1
        elif read.verdict == LOCK_SILICON:
            counts["silicon"] += 1
        elif read.verdict == LOCK_NOT_LOCKED:
            counts["not_locked"] += 1
        else:
            counts["halogen_untestable"] += 1
        record_of(lock.parent.anchor)["halogen_lock"] = _lock_fact(lock, read)
    for twin in plan.twins:
        fact = _twin_fact(twin, twin_samples(twin, tolerances, snr))
        counts = plan.counts[partition_of(twin.labelled.anchor)]
        counts["labelled_ions"] += 1
        counts[
            {TWIN_PRESENT: "twin_present", TWIN_ABSENT: "twin_absent"}.get(
                fact["verdict"], "twin_untestable"
            )
        ] += 1
        record_of(twin.labelled.anchor)["label_twin"] = fact
    counted: set[frozenset[str]] = set()
    for anchor_id, fact in sorted(plan.pairs.items()):
        anchor = plan.traces[anchor_id].anchor
        counts = plan.counts[partition_of(anchor)]
        counts["pair_channels"] += 1
        # Each pair once, from whichever of its channels names it: a channel's
        # fact names its strongest partner alone, so one neutral seen through
        # two batch peaks of a channel is two pairs, one named from one side.
        pair = frozenset((anchor_id, fact["partner_batch_peak_id"]))
        if pair not in counted:
            counted.add(pair)
            counts["pairs"] += 1
            counts[
                {
                    PAIR_ESTABLISHED: "pairs_established",
                    PAIR_NOT_ESTABLISHED: "pairs_not_established",
                }.get(fact["verdict"], "pairs_untestable")
            ] += 1
        record_of(anchor)["neutral_pair"] = fact
    rows = [
        {"ionization_mode_id": mode_id, "instrument_type": instrument_type, **counts}
        for (mode_id, instrument_type), counts in sorted(
            plan.counts.items(), key=lambda item: (str(item[0][0]), str(item[0][1]))
        )
    ]
    summary = {
        "thresholds": thresholds(),
        "partitions": rows,
        **{key: sum(row[key] for row in rows) for key in _COUNTS},
        "anchors_recorded": len(records),
    }
    return records, summary


# --- the run ------------------------------------------------------------------


async def _load_batch(
    sample_batch_id: str,
) -> tuple[
    list[BatchPeak],
    dict[str, dict[str, Member]],
    dict[str, str],
    dict[Optional[str], Optional[float]],
]:
    """The batch's anchors, every anchor's members, its mechanisms' notations
    and its ionization modes' label purities."""
    async with async_session() as session:
        anchors = (
            (
                await session.execute(
                    select(BatchPeak).where(
                        BatchPeak.sample_batch_id == sample_batch_id
                    )
                )
            )
            .scalars()
            .all()
        )
        rows = (
            await session.execute(
                select(
                    BatchPeakOccurrence.batch_peak_id,
                    BatchPeakOccurrence.sample_item_id,
                    BatchPeakOccurrence.sample_peak_id,
                    BatchPeakOccurrence.intensity,
                    BatchPeakOccurrence.mz_delta_ppm,
                    BatchPeakOccurrence.role,
                    BatchPeakOccurrence.owner_batch_peak_id,
                )
                .join(
                    BatchPeak,
                    BatchPeak.batch_peak_id == BatchPeakOccurrence.batch_peak_id,
                )
                .where(BatchPeak.sample_batch_id == sample_batch_id)
            )
        ).all()
        mechanism_ids = sorted(
            {a.ionization_mechanism_id for a in anchors if a.ionization_mechanism_id}
        )
        notation_by_id = {
            mechanism_id: notation
            for mechanism_id, notation in (
                await session.execute(
                    select(
                        IonizationMechanism.ionization_mechanism_id,
                        IonizationMechanism.ionization_mechanism,
                    ).where(
                        IonizationMechanism.ionization_mechanism_id.in_(mechanism_ids)
                    )
                )
            ).all()
            if notation
        }
        mode_ids = sorted(
            {a.ionization_mode_id for a in anchors if a.ionization_mode_id}
        )
        purity_by_mode = dict(
            (
                await session.execute(
                    select(
                        IonizationMode.ionization_mode_id, IonizationMode.label_purity
                    ).where(IonizationMode.ionization_mode_id.in_(mode_ids))
                )
            ).all()
        )
    mz_of = {anchor.batch_peak_id: anchor.mz for anchor in anchors}
    members: dict[str, dict[str, Member]] = defaultdict(dict)
    for row in rows:
        members[row.batch_peak_id][row.sample_item_id] = Member(
            sample_peak_id=str(row.sample_peak_id),
            intensity=row.intensity,
            mz=mz_from_delta(mz_of[row.batch_peak_id], row.mz_delta_ppm),
            role=role_name(row.role),
            owner_batch_peak_id=row.owner_batch_peak_id,
        )
    return list(anchors), dict(members), notation_by_id, purity_by_mode


#: Where a fact names another batch peak, the key it names it under.
_NAMED = {
    "halogen_lock": "batch_peak_id",
    "label_twin": "batch_peak_id",
    "neutral_pair": "partner_batch_peak_id",
}


def still_read(anchors: Iterable[Any], records: dict[str, dict]) -> dict[str, dict]:
    """The records a reading writes on the ledger as it stands at the write.

    The reading reads the ledger outside the fold lock and writes under it, so
    a fold or a curation can land in between. A batch peak whose reading moved
    there, or that went, gets no record: the consensus would have taken it off
    had it been written before. A fact naming a batch peak that went - the
    lock's line, the twin, the pair's partner - is left out, and a record left
    with no fact is not written.

    :param anchors: The batch's anchors, read under the lock.
    :param records: Anchor id -> its record (:func:`read_batch`).
    :return: The records to write.
    """
    by_id = {anchor.batch_peak_id: anchor for anchor in anchors}
    kept: dict[str, dict] = {}
    for anchor_id, record in records.items():
        anchor = by_id.get(anchor_id)
        if anchor is None or not batch_checks_record_reads(
            record,
            anchor.consensus_ion_formula,
            anchor.consensus_formula,
            anchor.ionization_mechanism_id,
        ):
            continue
        facts = {
            key: record[key]
            for key in BATCH_CHECK_FACTS
            if key in record
            and (not record[key].get(_NAMED[key]) or record[key][_NAMED[key]] in by_id)
        }
        if facts:
            kept[anchor_id] = {
                **{k: v for k, v in record.items() if k not in BATCH_CHECK_FACTS},
                **facts,
            }
    return kept


async def _write_records(sample_batch_id: str, records: dict[str, dict]) -> int:
    """Replace the batch's checks records with this reading's, under the fold
    lock. Every record an earlier reading left comes off first: a reading is
    of the ledger as it stands. Only the records change, and only those whose
    batch peak still reads them (:func:`still_read`).

    :return: How many batch peaks' records were left out, their reading having
        moved or gone since it was read.
    """
    async with async_session() as session:
        await _acquire_batch_fold_lock(session, sample_batch_id)
        anchors = (
            (
                await session.execute(
                    select(BatchPeak).where(
                        BatchPeak.sample_batch_id == sample_batch_id
                    )
                )
            )
            .scalars()
            .all()
        )
        kept = still_read(anchors, records)
        for anchor in anchors:
            record = kept.get(anchor.batch_peak_id)
            provenance = dict(anchor.provenance or {})
            if record is None:
                if BATCH_CHECKS_KEY not in provenance:
                    continue
                provenance.pop(BATCH_CHECKS_KEY)
            else:
                provenance[BATCH_CHECKS_KEY] = record
            anchor.provenance = provenance
        await touch_ledger(session, sample_batch_id)
        await session.commit()
    return len(records) - len(kept)


async def run_batch_checks(
    sample_batch_id: str, batch_peak_run_id: Optional[str] = None
) -> dict:
    """Read the batch's isotope checks and record them on the anchors.

    :return: The run's summary (:func:`read_batch`).
    """
    anchors, members, notation_by_id, purity_by_mode = await _load_batch(
        sample_batch_id
    )
    plan = await asyncio.to_thread(
        plan_batch, anchors, members, notation_by_id, purity_by_mode
    )
    tolerances = await recorded_envelope_tolerances(sample_batch_id)
    snr = await read_noise(needed_peaks(plan))
    records, summary = await asyncio.to_thread(
        read_batch,
        plan,
        notation_by_id,
        tolerances,
        snr,
        batch_peak_run_id=batch_peak_run_id,
    )
    summary["samples_read"] = len(snr)
    summary["samples_with_noise"] = sum(1 for values in snr.values() if values)
    summary["anchors_moved"] = await _write_records(sample_batch_id, records)
    return summary


def checks_outcome(counts: dict, sample_batch_id: str) -> dict:
    """The controller result the background-task decorator reports from."""
    notification_data = {"sample_batch_id": sample_batch_id}
    read = (
        counts["halogen_parents"]
        - counts["halogen_unstamped"]
        + counts["labelled_ions"]
        + counts["pair_channels"]
    )
    if not read:
        waiting = []
        if counts["halogen_unstamped"]:
            waiting.append("Stamp isotopologues")
        if counts["pair_channels_unread"]:
            waiting.append("Read co-variation")
        return {
            "status": "partial",
            "message": (
                "Nothing was checked: no batch peak's halogen line was stamped, "
                "no labelled ion was read, and no uronium pair was read for "
                "co-variation."
                + (f" Run {' and '.join(waiting)} first." if waiting else "")
            ),
            "data": counts,
            "_notification_data": notification_data,
        }
    return {
        "status": "success",
        "message": (
            f"Read the batch checks: {counts['locked']} halogen lock"
            f"{'s' if counts['locked'] != 1 else ''} ({counts['locked_cl']} "
            f"chlorine, {counts['locked_br']} bromine; {counts['silicon']} read "
            f"as silicon instead), {counts['twin_present']} of "
            f"{counts['labelled_ions']} labelled ion"
            f"{'s' if counts['labelled_ions'] != 1 else ''} with their 14N twin "
            f"({counts['twin_absent']} without), and {counts['pairs_established']} "
            f"of {counts['pairs']} uronium pair"
            f"{'s' if counts['pairs'] != 1 else ''} established. No tier moved."
        ),
        "data": counts,
        "_notification_data": notification_data,
    }


@api_controller_background_task(
    success_notification_rooms=["sample_batch_id"],
    success_reload=[("peak_assignment", "sample_batch_id")],
    error_notification_rooms=["sample_batch_id"],
)
async def read_batch_checks(
    sample_batch_id: str,
    independent_transaction: bool = False,
    user_id: int | None = None,
    process_id: str | None = None,
    parent_id: str | None = None,
) -> dict:
    """Read the batch's isotope checks, as a batch run. Writes no member and no
    per-sample run; emits ``peak_assignment_reload`` so the ledgers refresh."""
    run_id = await start_run(
        sample_batch_id, ACTION_BATCH_CHECKS, config=thresholds(), user_id=user_id
    )
    try:
        counts = await run_batch_checks(sample_batch_id, batch_peak_run_id=run_id)
    except Exception as exc:
        await fail_run(sample_batch_id, run_id, f"{type(exc).__name__}: {exc}")
        raise
    await complete_run(sample_batch_id, run_id, summary=counts)
    return checks_outcome(counts, sample_batch_id)


__all__ = [
    "LockPlan",
    "Plan",
    "TwinPlan",
    "checks_outcome",
    "halogens_of",
    "is_labelled_ion",
    "judge_silicon",
    "needed_peaks",
    "pair_channel",
    "pair_fact",
    "plan_pairs",
    "plan_batch",
    "plan_lock",
    "plan_twin",
    "read_batch",
    "read_batch_checks",
    "run_batch_checks",
    "still_read",
    "twin_samples",
]
