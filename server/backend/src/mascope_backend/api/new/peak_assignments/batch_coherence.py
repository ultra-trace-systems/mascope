"""Co-variation on the batch ledger: step 4.2 of the assignment plan.

A neutral the batch reads through two channels - an acid deprotonated and
clustered with nitrate, a ketone protonated and ammoniated - is two batch
peaks, and each batch peak is a trace across the batch's spectra: its
members' intensities, the batch time series the series route serves. Where
the two readings are one molecule, the two traces follow its abundance;
where a formula coincidence put two compounds under one neutral, they need
not. "Read co-variation" reads every such pair, per ionization mode and
instrument type, through ``mascope_tools.composition.coherence``: log
intensities, divided by the spectrum's reagent signal, the batch's common
mode removed, and Pearson's r over the spectra both channels hold, with the
count. At or above 0.6 the two co-vary, below they do not, and a pair under
ten shared spectra or with a flat trace is untestable.

The reading is display-only (decision 35): it moves no tier and no level,
and anti-correlation demotes nothing - a correct composition can run against
its own parent channel when the source state moves. It waits for the null
model of step 4.13 to give it a likelihood ratio before anything reads it as
evidence.

The anchor carries the reading under ``provenance.co_variation``
(``batch_peaks.CO_VARIATION_KEY``): its formula and mechanism, the strongest
partner's verdict, r and count, and every partner channel's in that order.
The consensus keeps the record for as long as it names that formula through
that mechanism, so a curation or a fold that moves the reading takes it off;
a new reading replaces every record. The ledger, a run's snapshot and the
inspector's detail read serve it (``standing_co_variation_record``). Nothing
here writes a member or a per-sample run.

Which batch peaks are read:

- **Channels.** A monoisotopic batch peak whose consensus names a formula
  through a mechanism, at any tier that commits one. An isotope line is its
  parent's ion again, and a claimed peak (a source ion, ringing) reads no
  formula. Two batch peaks of one neutral are partners where their channels
  differ; two through one channel are the same evidence twice
  (``cross_channel``'s rule), and neither is the other's partner.
- **The common mode** is read off the partition's monoisotopic, unclaimed
  batch peaks, a formula or not: every trace the batch holds.
- **The normaliser** is the partition's reagent ions: the batch peaks the
  reagent pass claimed, their isotope lines left out.

Design: ``docs/dev/assignment_quality_plan.md``, step 4.2.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from sqlalchemy import select

from mascope_backend.api.lib.api_features import api_controller_background_task
from mascope_backend.api.new.peak_assignments.batch_peaks import (
    CO_VARIATION_KEY,
    ROLE_REAGENT,
    instrument_type_of,
)
from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
    _acquire_batch_fold_lock,
)
from mascope_backend.api.new.peak_assignments.batch_runs import (
    ACTION_CO_VARIATION,
    complete_run,
    fail_run,
    start_run,
    touch_ledger,
)
from mascope_backend.api.new.peak_assignments.cross_channel import neutral_key
from mascope_backend.api.new.peak_assignments.tiers import TIER_UNASSIGNED
from mascope_backend.db import (
    BatchPeak,
    BatchPeakOccurrence,
    IonizationMechanism,
    async_session,
)
from mascope_tools.composition.coherence import (
    CO_VARYING_R,
    FLAT_SPREAD,
    MIN_COMMON_MODE_TRACES,
    MIN_SPECTRA,
    PERSISTENT_SHARE,
    REASON_FEW_SPECTRA,
    REASON_FLAT,
    VERDICT_CO_VARYING,
    VERDICT_NOT_CO_VARYING,
    CoVariation,
    batch_residuals,
    strength,
)


#: A partition of the batch's anchors: their ionization mode and the
#: instrument type their unit names. Two partitions' traces are never read
#: against each other.
Partition = tuple[Optional[str], Optional[str]]


def _monoisotopic(ion_formula: Optional[str]) -> bool:
    """Whether an anchor's ion is its monoisotopic line: an isotope line's ion
    names its isotope in brackets (``[13C]C9H15+``)."""
    return not ion_formula or "[" not in ion_formula


def partition_of(anchor: Any) -> Partition:
    """The partition an anchor's trace is read in."""
    return (anchor.ionization_mode_id, instrument_type_of(anchor.intensity_variable))


@dataclass(frozen=True)
class Channel:
    """An anchor reading a neutral through one channel.

    :ivar batch_peak_id: The anchor.
    :ivar formula: Its consensus formula.
    :ivar ion_formula: Its consensus ion.
    :ivar ionization_mechanism_id: The mechanism the formula is read through.
    :ivar channel: That mechanism's notation.
    """

    batch_peak_id: str
    formula: str
    ion_formula: Optional[str]
    ionization_mechanism_id: str
    channel: str


@dataclass
class PartitionAnchors:
    """One partition's anchors, as the reading uses them.

    :ivar traces: The anchors whose traces the common mode is read off.
    :ivar reagents: The reagent ions, the normaliser.
    :ivar neutrals: Neutral key -> the anchors reading it, each through a
        channel.
    """

    traces: list[str] = field(default_factory=list)
    reagents: list[str] = field(default_factory=list)
    neutrals: dict[str, list[Channel]] = field(default_factory=dict)


def partition_anchors(
    anchors: Iterable[Any], notation_by_id: dict[str, str]
) -> dict[Partition, PartitionAnchors]:
    """The batch's anchors, per partition, sorted into the reading's roles.

    :param anchors: The batch's anchors (``BatchPeak`` rows or anything with
        their attributes).
    :param notation_by_id: Mechanism id -> its notation.
    :return: Partition -> its anchors.
    """
    partitions: dict[Partition, PartitionAnchors] = defaultdict(PartitionAnchors)
    for anchor in sorted(anchors, key=lambda a: a.batch_peak_id):
        # An isotope line repeats its parent's trace, the reagent's included.
        if anchor.isotopologue_of or not _monoisotopic(anchor.consensus_ion_formula):
            continue
        part = partitions[partition_of(anchor)]
        if anchor.consensus_role == ROLE_REAGENT:
            part.reagents.append(anchor.batch_peak_id)
            continue
        if anchor.consensus_role:
            continue
        part.traces.append(anchor.batch_peak_id)
        formula = anchor.consensus_formula
        notation = notation_by_id.get(anchor.ionization_mechanism_id or "")
        if not formula or not notation or anchor.consensus_tier == TIER_UNASSIGNED:
            continue
        part.neutrals.setdefault(neutral_key(formula), []).append(
            Channel(
                batch_peak_id=anchor.batch_peak_id,
                formula=formula,
                ion_formula=anchor.consensus_ion_formula,
                ionization_mechanism_id=anchor.ionization_mechanism_id,
                channel=notation,
            )
        )
    return dict(partitions)


def _partner_entry(partner: Channel, reading: CoVariation) -> dict:
    return {
        "batch_peak_id": partner.batch_peak_id,
        "formula": partner.formula,
        "ion_formula": partner.ion_formula,
        "ionization_mechanism_id": partner.ionization_mechanism_id,
        "channel": partner.channel,
        **reading.to_json(),
    }


def co_variation_record(
    own: Channel,
    partners: list[tuple[Channel, CoVariation]],
    *,
    normalised: bool,
    batch_peak_run_id: Optional[str],
) -> dict:
    """What an anchor carries of its reading against its partners.

    :param own: The anchor's own channel.
    :param partners: Every partner channel with its reading, the anchor's
        trace first in each reading's ``spread``.
    :param normalised: Whether the partition's traces were divided by a
        reagent signal.
    :param batch_peak_run_id: The run that read it.
    :return: The record, JSON-ready: the strongest partner's verdict, r,
        count, reason and spread at the top, every partner strongest first.
    """
    ranked = sorted(partners, key=lambda pair: strength(pair[1]))
    best_partner, best = ranked[0]
    return {
        "formula": own.formula,
        "ionization_mechanism_id": own.ionization_mechanism_id,
        "channel": own.channel,
        **best.to_json(),
        "partner_batch_peak_id": best_partner.batch_peak_id,
        "partner_channel": best_partner.channel,
        "partners": [_partner_entry(partner, reading) for partner, reading in ranked],
        "normalised": normalised,
        "batch_peak_run_id": batch_peak_run_id,
    }


#: The count an untestable pair is tallied under, by its reason.
_UNTESTABLE_COUNTS = {
    REASON_FEW_SPECTRA: "untestable_few_spectra",
    REASON_FLAT: "untestable_flat",
}


def _tally(counts: dict, reading: CoVariation) -> None:
    if reading.verdict == VERDICT_CO_VARYING:
        counts["co_varying"] += 1
    elif reading.verdict == VERDICT_NOT_CO_VARYING:
        counts["not_co_varying"] += 1
    else:
        counts[_UNTESTABLE_COUNTS[reading.reason]] += 1


def read_partition(
    part: PartitionAnchors,
    traces: dict[str, dict[str, float]],
    *,
    batch_peak_run_id: Optional[str] = None,
) -> tuple[dict[str, dict], dict]:
    """Read every neutral of one partition seen through two channels or more.

    :param part: The partition's anchors (:func:`partition_anchors`).
    :param traces: Anchor id -> sample -> intensity, its members' values.
    :param batch_peak_run_id: The run doing it, for the records.
    :return: Anchor id -> its record, and the partition's counts: what the
        traces were read through, the neutrals read and every pair of
        channels by its verdict (each pair once).
    """
    residuals = batch_residuals(
        {key: traces.get(key, {}) for key in part.traces},
        reagent_traces={key: traces.get(key, {}) for key in part.reagents},
    )
    counts = {
        **residuals.summary(),
        "reagent_ions": len(part.reagents),
        "neutrals_read": 0,
        "pairs": 0,
        "co_varying": 0,
        "not_co_varying": 0,
        "untestable_few_spectra": 0,
        "untestable_flat": 0,
    }
    records: dict[str, dict] = {}
    for _, channels in sorted(part.neutrals.items()):
        if len({channel.channel for channel in channels}) < 2:
            continue
        counts["neutrals_read"] += 1
        for own in channels:
            partners = []
            for other in channels:
                if other.channel == own.channel:
                    continue
                reading = residuals.co_variation(own.batch_peak_id, other.batch_peak_id)
                partners.append((other, reading))
                # Each pair once: the reading is symmetric but for the order
                # of its spreads.
                if own.batch_peak_id < other.batch_peak_id:
                    counts["pairs"] += 1
                    _tally(counts, reading)
            records[own.batch_peak_id] = co_variation_record(
                own,
                partners,
                normalised=residuals.normalised,
                batch_peak_run_id=batch_peak_run_id,
            )
    return records, counts


#: The counts a run's summary sums over its partitions.
_SUMMED = (
    "neutrals_read",
    "pairs",
    "co_varying",
    "not_co_varying",
    "untestable_few_spectra",
    "untestable_flat",
)


def read_batch(
    anchors: Iterable[Any],
    notation_by_id: dict[str, str],
    traces: dict[str, dict[str, float]],
    *,
    batch_peak_run_id: Optional[str] = None,
) -> tuple[dict[str, dict], dict]:
    """Read every partition of a batch.

    :return: Anchor id -> its record, and the run's summary: the thresholds,
        every partition's counts, and their sums.
    """
    records: dict[str, dict] = {}
    partitions = []
    for (mode_id, instrument_type), part in sorted(
        partition_anchors(anchors, notation_by_id).items(),
        key=lambda item: (str(item[0][0]), str(item[0][1])),
    ):
        found, counts = read_partition(
            part, traces, batch_peak_run_id=batch_peak_run_id
        )
        records.update(found)
        partitions.append(
            {
                "ionization_mode_id": mode_id,
                "instrument_type": instrument_type,
                **counts,
            }
        )
    summary = {
        "thresholds": {
            "co_varying_r": CO_VARYING_R,
            "min_spectra": MIN_SPECTRA,
            "flat_spread": FLAT_SPREAD,
            "persistent_share": PERSISTENT_SHARE,
            "min_common_mode_traces": MIN_COMMON_MODE_TRACES,
        },
        "partitions": partitions,
        **{key: sum(p[key] for p in partitions) for key in _SUMMED},
        "anchors_recorded": len(records),
    }
    return records, summary


# --- the run ------------------------------------------------------------------


async def _load_batch(
    sample_batch_id: str,
) -> tuple[list[BatchPeak], dict[str, str], dict[str, dict[str, float]]]:
    """The batch's anchors, its mechanisms' notations, and every anchor's
    trace: sample -> its member's intensity."""
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
        rows = (
            await session.execute(
                select(
                    BatchPeakOccurrence.batch_peak_id,
                    BatchPeakOccurrence.sample_item_id,
                    BatchPeakOccurrence.intensity,
                )
                .join(
                    BatchPeak,
                    BatchPeak.batch_peak_id == BatchPeakOccurrence.batch_peak_id,
                )
                .where(
                    BatchPeak.sample_batch_id == sample_batch_id,
                    BatchPeakOccurrence.intensity.is_not(None),
                )
            )
        ).all()
    traces: dict[str, dict[str, float]] = defaultdict(dict)
    for batch_peak_id, sample_item_id, intensity in rows:
        traces[batch_peak_id][sample_item_id] = intensity
    return list(anchors), notation_by_id, dict(traces)


async def _write_records(sample_batch_id: str, records: dict[str, dict]) -> None:
    """Replace the batch's co-variation records with this run's, under the
    fold lock.

    Every record an earlier reading left comes off first: a reading is of the
    ledger as it stands, and a pair it no longer finds is no longer read.
    Only the records change; the consensus keeps them from here on while it
    names the reading each was read for.
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
        for anchor in anchors:
            record = records.get(anchor.batch_peak_id)
            provenance = dict(anchor.provenance or {})
            if record is None:
                if CO_VARIATION_KEY not in provenance:
                    continue
                provenance.pop(CO_VARIATION_KEY)
            else:
                provenance[CO_VARIATION_KEY] = record
            anchor.provenance = provenance
        await touch_ledger(session, sample_batch_id)
        await session.commit()


async def run_batch_co_variation(
    sample_batch_id: str, batch_peak_run_id: Optional[str] = None
) -> dict:
    """Read the batch's co-variation and record it on the anchors.

    :return: The run's summary (:func:`read_batch`).
    """
    anchors, notation_by_id, traces = await _load_batch(sample_batch_id)
    records, summary = await asyncio.to_thread(
        read_batch,
        anchors,
        notation_by_id,
        traces,
        batch_peak_run_id=batch_peak_run_id,
    )
    await _write_records(sample_batch_id, records)
    return summary


def co_variation_outcome(counts: dict, sample_batch_id: str) -> dict:
    """The controller result the background-task decorator reports from."""
    notification_data = {"sample_batch_id": sample_batch_id}
    neutrals = counts["neutrals_read"]
    if not neutrals:
        return {
            "status": "partial",
            "message": (
                "No co-variation was read: no compound in the batch peaks is "
                "seen through two ionization channels."
            ),
            "data": counts,
            "_notification_data": notification_data,
        }
    untestable = counts["untestable_few_spectra"] + counts["untestable_flat"]
    return {
        "status": "success",
        "message": (
            f"Read {neutrals} compound{'s' if neutrals != 1 else ''} seen "
            f"through two channels or more: {counts['co_varying']} pair"
            f"{'s' if counts['co_varying'] != 1 else ''} of channels co-vary "
            f"across the batch, {counts['not_co_varying']} "
            f"{'do' if counts['not_co_varying'] != 1 else 'does'} not, and "
            f"{untestable} could not be read ({counts['untestable_few_spectra']} "
            f"on too few spectra, {counts['untestable_flat']} flat). No tier "
            "moved."
        ),
        "data": counts,
        "_notification_data": notification_data,
    }


@api_controller_background_task(
    success_notification_rooms=["sample_batch_id"],
    success_reload=[("peak_assignment", "sample_batch_id")],
    error_notification_rooms=["sample_batch_id"],
)
async def read_batch_co_variation(
    sample_batch_id: str,
    independent_transaction: bool = False,
    user_id: int | None = None,
    process_id: str | None = None,
    parent_id: str | None = None,
) -> dict:
    """Read the co-variation of the batch's channels, as a batch run. Writes
    no member and no per-sample run; emits ``peak_assignment_reload`` so the
    ledgers refresh."""
    config = {
        "co_varying_r": CO_VARYING_R,
        "min_spectra": MIN_SPECTRA,
        "flat_spread": FLAT_SPREAD,
    }
    run_id = await start_run(
        sample_batch_id, ACTION_CO_VARIATION, config=config, user_id=user_id
    )
    try:
        counts = await run_batch_co_variation(sample_batch_id, batch_peak_run_id=run_id)
    except Exception as exc:
        await fail_run(sample_batch_id, run_id, f"{type(exc).__name__}: {exc}")
        raise
    await complete_run(sample_batch_id, run_id, summary=counts)
    return co_variation_outcome(counts, sample_batch_id)


__all__ = [
    "Channel",
    "PartitionAnchors",
    "co_variation_outcome",
    "co_variation_record",
    "partition_anchors",
    "partition_of",
    "read_batch",
    "read_batch_co_variation",
    "read_partition",
    "run_batch_co_variation",
]
