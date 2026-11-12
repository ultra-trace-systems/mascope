"""Predicted diagnostic isotopologues on the batch ledger: step 4.2's second rider.

The 15N, 18O, 34S, 29Si, 30Si, 37Cl and 81Br lines of a committed ion sit below
the peak picker's edge in most files of a batch and stand only where a plume
lifts them, so the run that reads one file - and the fold of its rows - seldom
holds them as the ion's lines. The batch holds them anyway: wherever one stood
it folded into a batch peak of its own, as an unassigned peak or as whatever
the file's search made of it. "Stamp isotopologues" reads every committed
monoisotopic batch peak's diagnostic isotopologues
(``mascope_tools.composition.diagnostic_isotopologues``) where the batch shows
them:

1. **Predicted at the parent's trace centre.** The parent's m/z is its members'
   median offset from its frozen anchor, and each isotope line sits its
   predicted offset above that, at the resolving power the parent's anchor was
   minted at; an isotope line a peak of that width cannot show on its own is
   *unresolved* and not judged.
2. **Found.** The batch peak whose own trace centre is within its frozen
   tolerance of that m/z is the line; none, and the isotope line is predicted
   and not found.
3. **Judged in each sample** that holds both the parent and the line: the
   line's intensity over the parent's against the predicted ratio, inside the
   envelope tolerance that sample's run fitted (step 3.5b,
   ``envelope_fit.recorded_envelope_tolerances``), the two peaks' noise read
   off the sample's peak list.
4. **Stamped across the track** where enough samples were judged and the ratio
   holds in a large enough share of them
   (``diagnostic_isotopologues.read_track``). The line is stamped in each
   sample where its ratio held. A stamp whose member the sample's own run read
   as the parent's isotopologue is the peak picker's line, confirmed
   (``picker``); every other one was ``predicted`` here.

A line two parents stamp is the isotopologue of the one whose ratio holds in
the larger share of its samples (then in more of them, then the lower id); the
other parent's record names it as taken where that parent's track held too.

The parent carries what it was read for under
``provenance.diagnostic_isotopologues``
(``batch_peaks.DIAGNOSTIC_ISOTOPOLOGUES_KEY``): its formula and mechanism, each
isotope line predicted with its m/z, ratio and whether it is resolved, the line
found, the samples judged and holding, the verdict and whether it was stamped.
A stamped line carries ``provenance.stamped_isotopologue_of``
(``batch_peaks.STAMPED_ISOTOPOLOGUE_OF_KEY``): its parent, the isotope line,
the track's reading, and per sample the stamp's source. A new stamping replaces
every record an earlier one left. The consensus keeps a parent's record while
it names that reading, and a parent whose reading moves takes its stamps off
its lines (``batch_peaks_controller``); one whose reading moves, or that goes,
while the stamping runs gets no record, nor do its lines (:func:`still_read`).

Nothing moves: no tier, reason, member or per-sample run. The stamps are
facts the later steps (4.2g's batch isotope checks, the tiers) count.

Design: ``docs/dev/assignment_quality_plan.md``, step 4.2, the second rider.
"""

from __future__ import annotations

import asyncio
import bisect
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from sqlalchemy import select

from mascope_backend.api.controllers.samples.lib.samples_fetch import fetch_sample
from mascope_backend.api.lib.api_features import api_controller_background_task
from mascope_backend.api.new.peak_assignments.batch_peaks import (
    DIAGNOSTIC_ISOTOPOLOGUES_KEY,
    ROLE_ISO_CHILD,
    ROLE_REAGENT,
    STAMP_PICKER,
    STAMP_PREDICTED,
    STAMPED_ISOTOPOLOGUE_OF_KEY,
    diagnostic_isotopologues_record_reads,
    instrument_type_of,
    mz_from_delta,
    resolution_from_tol_ppm,
    role_name,
)
from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
    _acquire_batch_fold_lock,
)
from mascope_backend.api.new.peak_assignments.batch_runs import (
    ACTION_ISOTOPOLOGUES,
    complete_run,
    fail_run,
    start_run,
    touch_ledger,
)
from mascope_backend.api.new.peak_assignments.envelope_fit import (
    recorded_envelope_tolerances,
)
from mascope_backend.api.new.peak_assignments.service import load_sample_peaks
from mascope_backend.api.new.peak_assignments.tiers import TIER_UNASSIGNED
from mascope_backend.db import BatchPeak, BatchPeakOccurrence, async_session
from mascope_backend.runtime import runtime
from mascope_tools.composition.diagnostic_isotopologues import (
    HOLD_WIDTHS,
    MAX_SPAN,
    MIN_JUDGED,
    REASON_UNRESOLVED,
    TRACK_SHARE,
    VERDICT_STAMPED,
    VERDICT_UNTESTABLE,
    DiagnosticIsotopologue,
    TrackReading,
    judge_ratio,
    predict_diagnostic_isotopologues,
    read_track,
    stamps_in,
)
from mascope_tools.composition.envelope_tolerance import (
    DEFAULT_TOLERANCE,
    EnvelopeTolerance,
)


#: A partition of the batch's anchors: their ionization mode and the
#: instrument type their unit names. An isotope line is looked for in its
#: parent's partition only.
Partition = tuple[Optional[str], Optional[str]]

#: Why an isotope line judged across the track was not stamped on its line: the
#: line is another parent's isotopologue, whose ratio holds more widely.
REASON_TAKEN = "another_parent"

#: Why a resolved isotope line was not judged: no batch peak stands at its m/z.
REASON_NOT_FOUND = "not_found"


def _monoisotopic(ion_formula: Optional[str]) -> bool:
    """Whether an anchor's ion is its monoisotopic line: an isotope line's ion
    names its isotope in brackets (``[13C]C9H15+``)."""
    return bool(ion_formula) and "[" not in ion_formula


def is_parent(anchor: Any) -> bool:
    """Whether an anchor is a committed monoisotopic batch peak, whose
    isotope lines are predicted: a formula at a tier that commits one, its own
    monoisotopic ion with its charge, no source role, and no isotopologue of
    another batch peak."""
    return (
        bool(anchor.consensus_formula)
        and anchor.consensus_tier != TIER_UNASSIGNED
        and not anchor.consensus_role
        and not anchor.isotopologue_of
        and _monoisotopic(anchor.consensus_ion_formula)
    )


@dataclass(frozen=True)
class Member:
    """What the stamping reads of one member.

    :ivar sample_peak_id: The sample's peak.
    :ivar intensity: Its intensity, in the anchor's unit.
    :ivar mz: Its m/z, recovered from the stored offset.
    :ivar role: Its per-sample role, by name.
    :ivar owner_batch_peak_id: The anchor its owner folded into, for a line.
    """

    sample_peak_id: str
    intensity: Optional[float]
    mz: float
    role: Optional[str] = None
    owner_batch_peak_id: Optional[str] = None


@dataclass
class Trace:
    """One anchor and its members, sample -> member.

    :ivar centre: The m/z its members sit at: their median offset from the
        frozen anchor.
    """

    anchor: Any
    members: dict[str, Member] = field(default_factory=dict)
    centre: float = 0.0


def trace_centre(anchor_mz: float, members: Iterable[Member]) -> float:
    """The m/z an anchor's members sit at: the median of theirs, the anchor's
    own where it has none."""
    mzs = [member.mz for member in members]
    return statistics.median(mzs) if mzs else anchor_mz


def partition_of(anchor: Any) -> Partition:
    """The partition an anchor is read in."""
    return (anchor.ionization_mode_id, instrument_type_of(anchor.intensity_variable))


class CentreIndex:
    """One partition's traces, sorted by centre, for the line a predicted m/z
    falls on."""

    def __init__(self, traces: Iterable[Trace]):
        self._traces = sorted(traces, key=lambda trace: trace.centre)
        self._centres = [trace.centre for trace in self._traces]

    def find(self, mz: float, exclude: Optional[str] = None) -> Optional[Trace]:
        """The trace whose centre is nearest ``mz`` within its own frozen
        tolerance, or None."""
        best: Optional[tuple[float, Trace]] = None
        j = bisect.bisect_left(self._centres, mz)
        # Tolerances differ between anchors, so look either side for the
        # nearest that holds the m/z.
        for k in range(max(0, j - 4), min(len(self._traces), j + 4)):
            trace = self._traces[k]
            if trace.anchor.batch_peak_id == exclude:
                continue
            distance = abs(trace.centre - mz) / mz * 1e6
            if distance <= trace.anchor.mz_tol_ppm and (
                best is None or distance < best[0]
            ):
                best = (distance, trace)
        return best[1] if best else None


@dataclass
class Prediction:
    """One isotopologue of one parent, and where the batch shows it.

    :ivar judged: Per sample holding both lines, the parent's and the line's
        members.
    """

    parent: Trace
    isotopologue: DiagnosticIsotopologue
    mz: float
    line: Optional[Trace] = None
    judged: dict[str, tuple[Member, Member]] = field(default_factory=dict)


def plan_batch(
    anchors: Iterable[Any], members: dict[str, dict[str, Member]]
) -> list[Prediction]:
    """Every committed monoisotopic anchor's diagnostic isotopologues, each with
    the line it falls on and the samples holding both.

    :param anchors: The batch's anchors.
    :param members: Anchor id -> sample -> its member.
    :return: One prediction per isotope line, in parent id and isotope order.
    """
    traces: dict[Partition, list[Trace]] = defaultdict(list)
    by_id: dict[str, Trace] = {}
    for anchor in sorted(anchors, key=lambda a: a.batch_peak_id):
        own = members.get(anchor.batch_peak_id, {})
        trace = Trace(anchor, own, trace_centre(anchor.mz, own.values()))
        traces[partition_of(anchor)].append(trace)
        by_id[anchor.batch_peak_id] = trace
    indexes = {part: CentreIndex(found) for part, found in traces.items()}
    predictions = []
    for parent in by_id.values():
        anchor = parent.anchor
        if not is_parent(anchor):
            continue
        index = indexes[partition_of(anchor)]
        resolution = resolution_from_tol_ppm(anchor.mz_tol_ppm)
        for isotopologue in predict_diagnostic_isotopologues(
            anchor.consensus_ion_formula, resolution
        ):
            mz = parent.centre + isotopologue.mz_offset
            prediction = Prediction(parent, isotopologue, mz)
            predictions.append(prediction)
            if not isotopologue.resolved:
                continue
            line = index.find(mz, exclude=anchor.batch_peak_id)
            if line is None:
                continue
            prediction.line = line
            for sample_item_id, member in sorted(line.members.items()):
                own = parent.members.get(sample_item_id)
                if (
                    own is not None
                    and _positive(own.intensity)
                    and _positive(member.intensity)
                ):
                    prediction.judged[sample_item_id] = (own, member)
    return predictions


def _positive(intensity: Optional[float]) -> bool:
    """Whether a member's intensity is one a ratio can be read off."""
    return intensity is not None and intensity > 0


def needed_peaks(predictions: Iterable[Prediction]) -> dict[str, set[str]]:
    """Sample -> the peaks whose noise the judging reads."""
    needed: dict[str, set[str]] = defaultdict(set)
    for prediction in predictions:
        for sample_item_id, (own, member) in prediction.judged.items():
            needed[sample_item_id].update((own.sample_peak_id, member.sample_peak_id))
    return dict(needed)


@dataclass
class Judgement:
    """A prediction's reading over the track, before lines are shared out."""

    prediction: Prediction
    holding: list[str]
    reading: TrackReading


def _stamp_source(member: Member, parent_id: str) -> str:
    """Where a stamp on a member came from: the run's own isotope match read
    the member as the parent's line, or it did not."""
    if member.role in (ROLE_ISO_CHILD, ROLE_REAGENT) and (
        member.owner_batch_peak_id == parent_id
    ):
        return STAMP_PICKER
    return STAMP_PREDICTED


def judge(
    prediction: Prediction,
    tolerances: dict[str, EnvelopeTolerance],
    snr: dict[str, dict[str, Optional[float]]],
) -> Judgement:
    """Read one prediction in every sample that holds both lines, and over the
    track. The samples it would stamp the line in are those whose reading
    could tell and held (``diagnostic_isotopologues.stamps_in``)."""
    holding = []
    readings = []
    for sample_item_id, (own, member) in sorted(prediction.judged.items()):
        noise = snr.get(sample_item_id, {})
        reading = judge_ratio(
            member.intensity / own.intensity,
            prediction.isotopologue.ratio,
            tolerance=tolerances.get(sample_item_id, DEFAULT_TOLERANCE),
            parent_snr=noise.get(own.sample_peak_id),
            line_snr=noise.get(member.sample_peak_id),
        )
        readings.append(reading)
        if stamps_in(reading):
            holding.append(sample_item_id)
    return Judgement(prediction, holding, read_track(readings))


def _rank(judgement: Judgement) -> tuple:
    """The order two parents claiming one line are ranked in: the larger share
    holding, then more samples holding, then the lower parent id."""
    reading = judgement.reading
    return (
        -(reading.share or 0.0),
        -reading.holds,
        judgement.prediction.parent.anchor.batch_peak_id,
    )


def read_batch(
    predictions: list[Prediction],
    tolerances: dict[str, EnvelopeTolerance],
    snr: dict[str, dict[str, Optional[float]]],
    *,
    batch_peak_run_id: Optional[str] = None,
) -> tuple[dict[str, dict], dict[str, dict], dict]:
    """Judge every prediction and share the stamped lines out.

    :param predictions: The batch's predictions (:func:`plan_batch`).
    :param tolerances: Sample -> the envelope tolerance its run fitted.
    :param snr: Sample -> peak -> its signal-to-noise, where read.
    :param batch_peak_run_id: The run doing it, for the records.
    :return: Parent id -> its diagnostic isotopologues record, line id -> its
        stamp record, and the run's summary.
    """
    judgements = [judge(p, tolerances, snr) for p in predictions]
    # A line two parents stamp goes to the one ranked first.
    claims: dict[str, list[Judgement]] = defaultdict(list)
    for judgement in judgements:
        line = judgement.prediction.line
        if line is not None and judgement.reading.verdict == VERDICT_STAMPED:
            claims[line.anchor.batch_peak_id].append(judgement)
    winners = {line_id: min(found, key=_rank) for line_id, found in claims.items()}

    by_parent: dict[str, list[Judgement]] = defaultdict(list)
    for judgement in judgements:
        by_parent[judgement.prediction.parent.anchor.batch_peak_id].append(judgement)

    parents: dict[str, dict] = {}
    lines: dict[str, dict] = {}
    partitions: dict[Partition, dict] = defaultdict(_empty_counts)
    for parent_id, found in sorted(by_parent.items()):
        parent = found[0].prediction.parent
        counts = partitions[partition_of(parent.anchor)]
        counts["parents"] += 1
        entries = []
        for judgement in found:
            prediction = judgement.prediction
            isotopologue = prediction.isotopologue
            line = prediction.line
            reading = judgement.reading
            counts["predicted"] += 1
            entry = {
                **isotopologue.to_json(),
                "mz": round(prediction.mz, 6),
                "batch_peak_id": line.anchor.batch_peak_id if line else None,
                **reading.to_json(),
                "stamped": False,
            }
            if not isotopologue.resolved:
                counts["unresolved"] += 1
                entry["reason"] = REASON_UNRESOLVED
                entry["verdict"] = VERDICT_UNTESTABLE
            elif line is None:
                counts["not_found"] += 1
                entry["reason"] = REASON_NOT_FOUND
            else:
                counts["found"] += 1
                counts[reading.verdict] += 1
            winner = winners.get(line.anchor.batch_peak_id) if line else None
            if winner is judgement:
                stamps = {
                    sample_item_id: _stamp_source(
                        prediction.judged[sample_item_id][1], parent_id
                    )
                    for sample_item_id in judgement.holding
                }
                n_predicted = sum(s == STAMP_PREDICTED for s in stamps.values())
                n_picker = len(stamps) - n_predicted
                entry.update(stamped=True, n_predicted=n_predicted, n_picker=n_picker)
                counts["lines_stamped"] += 1
                counts["stamps_predicted"] += n_predicted
                counts["stamps_picker"] += n_picker
                lines[line.anchor.batch_peak_id] = {
                    "batch_peak_id": parent_id,
                    "formula": parent.anchor.consensus_formula,
                    "ion_formula": parent.anchor.consensus_ion_formula,
                    "ionization_mechanism_id": parent.anchor.ionization_mechanism_id,
                    "label": isotopologue.label,
                    "ratio": round(isotopologue.ratio, 6),
                    **reading.to_json(),
                    "n_predicted": n_predicted,
                    "n_picker": n_picker,
                    "stamps": stamps,
                    "batch_peak_run_id": batch_peak_run_id,
                }
            elif winner is not None and reading.verdict == VERDICT_STAMPED:
                # Only a track that held can lose its line to another.
                counts["taken"] += 1
                entry["taken_by"] = winner.prediction.parent.anchor.batch_peak_id
                entry["reason"] = REASON_TAKEN
            entries.append(entry)
        parents[parent_id] = {
            "formula": parent.anchor.consensus_formula,
            "ion_formula": parent.anchor.consensus_ion_formula,
            "ionization_mechanism_id": parent.anchor.ionization_mechanism_id,
            "mz": round(parent.centre, 6),
            "resolution": _rounded(resolution_from_tol_ppm(parent.anchor.mz_tol_ppm)),
            "predicted": entries,
            "n_predicted": len(entries),
            "n_found": sum(1 for e in entries if e["batch_peak_id"]),
            "n_stamped": sum(1 for e in entries if e["stamped"]),
            "batch_peak_run_id": batch_peak_run_id,
        }
    summary = _summary(partitions)
    summary["parents_recorded"] = len(parents)
    return parents, lines, summary


def _rounded(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(value, 1)


#: The counts a partition keeps, and the run's summary sums.
_COUNTS = (
    "parents",
    "predicted",
    "unresolved",
    "not_found",
    "found",
    "stamped",
    "not_stamped",
    "untestable",
    "taken",
    "lines_stamped",
    "stamps_predicted",
    "stamps_picker",
)


def _empty_counts() -> dict:
    return {key: 0 for key in _COUNTS}


def _summary(partitions: dict[Partition, dict]) -> dict:
    rows = [
        {"ionization_mode_id": mode_id, "instrument_type": instrument_type, **counts}
        for (mode_id, instrument_type), counts in sorted(
            partitions.items(), key=lambda item: (str(item[0][0]), str(item[0][1]))
        )
    ]
    return {
        "thresholds": {
            "hold_widths": HOLD_WIDTHS,
            "max_span": MAX_SPAN,
            "min_judged": MIN_JUDGED,
            "track_share": TRACK_SHARE,
        },
        "partitions": rows,
        **{key: sum(row[key] for row in rows) for key in _COUNTS},
    }


# --- the run ------------------------------------------------------------------


async def _load_batch(
    sample_batch_id: str,
) -> tuple[list[BatchPeak], dict[str, dict[str, Member]]]:
    """The batch's anchors and every anchor's members."""
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
    return list(anchors), dict(members)


async def _sample_noise(sample_item_id: str, peaks: set[str]) -> dict[str, float]:
    """The signal-to-noise of some of a sample's peaks, off its peak list; none
    where the list records none or cannot be read, which widens nothing."""
    try:
        sample = await fetch_sample(sample_item_id)
        frame = await asyncio.to_thread(load_sample_peaks, sample)
    except Exception as exc:  # noqa: BLE001 - noise is best-effort
        runtime.logger.debug(
            f"Isotopologue stamping reads no noise for sample '{sample_item_id}': {exc}"
        )
        return {}
    if "signal_to_noise" not in frame.columns:
        return {}
    wanted = frame[frame["sample_peak_id"].isin(peaks)]
    return {
        str(peak_id): float(value)
        for peak_id, value in zip(wanted["sample_peak_id"], wanted["signal_to_noise"])
        if value == value
    }


async def read_noise(needed: dict[str, set[str]]) -> dict[str, dict[str, float]]:
    """Sample -> peak -> signal-to-noise, for the peaks the judging reads."""
    return {
        sample_item_id: await _sample_noise(sample_item_id, peaks)
        for sample_item_id, peaks in sorted(needed.items())
    }


def still_read(
    anchors: Iterable[Any], parents: dict[str, dict], lines: dict[str, dict]
) -> tuple[dict[str, dict], dict[str, dict]]:
    """The records a stamping writes on the ledger as it stands at the write.

    The stamping reads the ledger outside the fold lock and writes under it,
    so a fold or a curation can land in between. A parent whose reading moved
    there, or that went, has had its stamps taken off by its recompute
    (``batch_peaks_controller``) before these were written, and its line's
    record is served whatever the parent now reads: the parent's record and
    its lines' are left out, as if the parent had moved after the write.

    :param anchors: The batch's anchors, read under the lock.
    :param parents: Parent id -> its diagnostic isotopologues record
        (:func:`read_batch`).
    :param lines: Line id -> its stamp record.
    :return: The parents' records whose parent still reads them, and their
        lines'.
    """
    by_id = {anchor.batch_peak_id: anchor for anchor in anchors}
    kept = {
        parent_id: record
        for parent_id, record in parents.items()
        if parent_id in by_id
        and diagnostic_isotopologues_record_reads(record, by_id[parent_id])
    }
    return kept, {
        line_id: record
        for line_id, record in lines.items()
        if record["batch_peak_id"] in kept
    }


async def _write_records(
    sample_batch_id: str, parents: dict[str, dict], lines: dict[str, dict]
) -> int:
    """Replace the batch's stamping records with this run's, under the fold
    lock. Every record an earlier stamping left comes off first: a stamping
    reads the ledger as it stands. Only the records change, and only those
    whose parent still reads them (:func:`still_read`).

    :return: How many parents' records were left out, the parent having
        moved or gone since the stamping read it.
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
        kept, lines = still_read(anchors, parents, lines)
        for anchor in anchors:
            provenance = dict(anchor.provenance or {})
            before = dict(provenance)
            for key, records in (
                (DIAGNOSTIC_ISOTOPOLOGUES_KEY, kept),
                (STAMPED_ISOTOPOLOGUE_OF_KEY, lines),
            ):
                record = records.get(anchor.batch_peak_id)
                if record is None:
                    provenance.pop(key, None)
                else:
                    provenance[key] = record
            if provenance != before:
                anchor.provenance = provenance
        await touch_ledger(session, sample_batch_id)
        await session.commit()
    return len(parents) - len(kept)


async def run_batch_isotopologues(
    sample_batch_id: str, batch_peak_run_id: Optional[str] = None
) -> dict:
    """Stamp the batch's isotope lines and record them on the anchors.

    :return: The run's summary (:func:`read_batch`).
    """
    anchors, members = await _load_batch(sample_batch_id)
    predictions = await asyncio.to_thread(plan_batch, anchors, members)
    tolerances = await recorded_envelope_tolerances(sample_batch_id)
    snr = await read_noise(needed_peaks(predictions))
    parents, lines, summary = await asyncio.to_thread(
        read_batch,
        predictions,
        tolerances,
        snr,
        batch_peak_run_id=batch_peak_run_id,
    )
    summary["samples_read"] = len(snr)
    summary["samples_with_noise"] = sum(1 for values in snr.values() if values)
    summary["parents_moved"] = await _write_records(sample_batch_id, parents, lines)
    return summary


def stamping_outcome(counts: dict, sample_batch_id: str) -> dict:
    """The controller result the background-task decorator reports from."""
    notification_data = {"sample_batch_id": sample_batch_id}
    if not counts["parents"]:
        return {
            "status": "partial",
            "message": (
                "No isotope line was predicted: no batch peak commits a formula "
                "holding nitrogen, oxygen, sulfur, silicon, chlorine or bromine."
            ),
            "data": counts,
            "_notification_data": notification_data,
        }
    lines = counts["lines_stamped"]
    return {
        "status": "success",
        "message": (
            f"Predicted {counts['predicted']} isotope line"
            f"{'s' if counts['predicted'] != 1 else ''} of {counts['parents']} "
            f"batch peak{'s' if counts['parents'] != 1 else ''}: {counts['found']} "
            f"found in the batch, {lines} stamped "
            f"({counts['stamps_predicted']} sample stamp"
            f"{'s' if counts['stamps_predicted'] != 1 else ''} predicted, "
            f"{counts['stamps_picker']} found by the peak picker), "
            f"{counts['not_stamped']} not holding their ratio and "
            f"{counts['untestable']} on too few samples. No tier moved."
        ),
        "data": counts,
        "_notification_data": notification_data,
    }


@api_controller_background_task(
    success_notification_rooms=["sample_batch_id"],
    success_reload=[("peak_assignment", "sample_batch_id")],
    error_notification_rooms=["sample_batch_id"],
)
async def stamp_batch_isotopologues(
    sample_batch_id: str,
    independent_transaction: bool = False,
    user_id: int | None = None,
    process_id: str | None = None,
    parent_id: str | None = None,
) -> dict:
    """Stamp the batch's predicted diagnostic isotopologues, as a batch run.
    Writes no member and no per-sample run; emits ``peak_assignment_reload``
    so the ledgers refresh."""
    config = {
        "hold_widths": HOLD_WIDTHS,
        "max_span": MAX_SPAN,
        "min_judged": MIN_JUDGED,
        "track_share": TRACK_SHARE,
    }
    run_id = await start_run(
        sample_batch_id, ACTION_ISOTOPOLOGUES, config=config, user_id=user_id
    )
    try:
        counts = await run_batch_isotopologues(
            sample_batch_id, batch_peak_run_id=run_id
        )
    except Exception as exc:
        await fail_run(sample_batch_id, run_id, f"{type(exc).__name__}: {exc}")
        raise
    await complete_run(sample_batch_id, run_id, summary=counts)
    return stamping_outcome(counts, sample_batch_id)


__all__ = [
    "CentreIndex",
    "Member",
    "Prediction",
    "Trace",
    "is_parent",
    "judge",
    "needed_peaks",
    "plan_batch",
    "read_batch",
    "read_noise",
    "run_batch_isotopologues",
    "stamping_outcome",
    "stamp_batch_isotopologues",
    "still_read",
    "trace_centre",
]
