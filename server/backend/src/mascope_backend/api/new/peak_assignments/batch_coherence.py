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

The same run reads every committed reading's trace alone (step 4.2e, the
fourth rider): divided by its partition's normaliser - the reagent ions'
summed signal, else each spectrum's total ion current, neither where it moves
more than a flat trace - its coefficient of variation over the spectra it is
present in, flat under 0.25 where it is present in half the partition's
spectra and the partition itself varies (``coherence.read_flatness``). Every
reading read carries ``provenance.flat_series``
(``batch_peaks.FLAT_SERIES_KEY``), which on a flat reading names the ion of
the batch's own source list its ion is, where one does. It is an origin flag
and display-only, as the co-variation is: a flat trace says where an ion
comes from, not whether its formula is right (the plan owner's call on
4.2e, beside decision 35). No tier, role, member or per-sample run moves on
it, and claiming a source ion is the known-species step's.

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
  reagent pass claimed, their isotope lines left out; for the flat reading,
  each spectrum's total ion current where the partition holds none.
- **The flat reading** reads every trace the common mode is read off, and
  records a verdict on each that commits a formula, at any tier. The source's
  list is the libraries of the profiles the batch's own runs resolved, each
  sample's latest completed run; a batch folded without runs names none.

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
    FLAT_SERIES_KEY,
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
from mascope_backend.api.new.peak_assignments.reagent_pass import library_ion
from mascope_backend.api.new.peak_assignments.tiers import (
    TIER_ASSIGNED,
    TIER_UNASSIGNED,
)
from mascope_backend.db import (
    BatchPeak,
    BatchPeakOccurrence,
    IonizationMechanism,
    PeakAssignmentRun,
    SampleItem,
    async_session,
)
from mascope_tools.composition.coherence import (
    CO_VARYING_R,
    FLAT_CV,
    FLAT_MIN_SHARE,
    FLAT_SPREAD,
    MIN_COMMON_MODE_TRACES,
    MIN_SPECTRA,
    MIN_VARYING_SHARE,
    PERSISTENT_SHARE,
    REASON_FEW_SPECTRA,
    REASON_FLAT,
    VERDICT_CO_VARYING,
    VERDICT_NOT_CO_VARYING,
    CoVariation,
    Flatness,
    FlatSeries,
    batch_residuals,
    read_flatness,
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
    :ivar readings: Anchor id -> the reading its flatness is recorded for:
        every trace that commits a formula.
    """

    traces: list[str] = field(default_factory=list)
    reagents: list[str] = field(default_factory=list)
    neutrals: dict[str, list[Channel]] = field(default_factory=dict)
    readings: dict[str, "Reading"] = field(default_factory=dict)


@dataclass(frozen=True)
class Reading:
    """The reading a flat-series record is read on: what the anchor's
    consensus names.

    :ivar formula: The neutral formula.
    :ivar ion_formula: Its ion.
    :ivar ionization_mechanism_id: The mechanism it is read through.
    :ivar tier: Its tier.
    """

    formula: str
    ion_formula: Optional[str]
    ionization_mechanism_id: Optional[str]
    tier: Optional[str]


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
        if formula and anchor.consensus_tier != TIER_UNASSIGNED:
            part.readings[anchor.batch_peak_id] = Reading(
                formula=formula,
                ion_formula=anchor.consensus_ion_formula,
                ionization_mechanism_id=anchor.ionization_mechanism_id,
                tier=anchor.consensus_tier,
            )
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


# --- a flat series (step 4.2e) -------------------------------------------------


def _source_ion(ion_formula: Optional[str], profiles: Iterable[str]) -> Optional[dict]:
    """The ion of the batch's own source list (step 3.3's) an ion formula
    names, for the record; None where the list does not name it.

    :param profiles: The profiles the batch's runs resolved in the partition.
    """
    cluster = library_ion(ion_formula, profiles)
    if cluster is None:
        return None
    return {
        "ion_formula": cluster.formula
        + ("+" * cluster.charge if cluster.charge > 0 else "-" * -cluster.charge),
        "label": cluster.label,
        "family": cluster.family,
        "kind": cluster.kind,
        "mz": round(cluster.mz, 5),
    }


def flat_series_record(
    reading: Reading,
    series: FlatSeries,
    flatness: Flatness,
    *,
    profiles: Iterable[str] = (),
    batch_peak_run_id: Optional[str],
) -> dict:
    """What an anchor carries of its trace's flatness.

    :param reading: The reading the record is read on.
    :param series: The trace's verdict.
    :param flatness: Its partition's reading, for the normaliser and how much
        of the partition varies.
    :param profiles: The profiles the batch's runs resolved in the
        partition, whose source lists a flat reading's ion is read against.
    :param batch_peak_run_id: The run that read it.
    :return: The record, JSON-ready (``batch_peaks.FLAT_SERIES_KEY``): a
        source ion is named only on a flat trace, where the flag says
        background.
    """
    return {
        "formula": reading.formula,
        "ion_formula": reading.ion_formula,
        "ionization_mechanism_id": reading.ionization_mechanism_id,
        **series.to_json(),
        **flatness.normaliser.to_json(),
        "varying_share": (
            None if flatness.varying_share is None else round(flatness.varying_share, 4)
        ),
        "flat_cv": FLAT_CV,
        "min_share": FLAT_MIN_SHARE,
        "source_ion": (
            _source_ion(reading.ion_formula, profiles) if series.flat else None
        ),
        "batch_peak_run_id": batch_peak_run_id,
    }


def read_partition_flatness(
    part: PartitionAnchors,
    traces: dict[str, dict[str, float]],
    tic: dict[str, float],
    *,
    profiles: Iterable[str] = (),
    batch_peak_run_id: Optional[str] = None,
) -> tuple[dict[str, dict], dict]:
    """Read every committed reading of one partition for a flat trace.

    :param part: The partition's anchors (:func:`partition_anchors`).
    :param traces: Anchor id -> sample -> intensity, its members' values.
    :param tic: Sample -> its total ion current, the normaliser where the
        partition holds no reagent ion.
    :param profiles: The profiles the batch's runs resolved in the partition.
    :param batch_peak_run_id: The run doing it, for the records.
    :return: Anchor id -> its record, and the partition's counts: what the
        traces were divided by, how much of the partition varies, each verdict
        over the readings recorded, and of the flat ones those the source's
        list names.
    """
    flatness = read_flatness(
        {key: traces.get(key, {}) for key in part.traces},
        reagent_traces={key: traces.get(key, {}) for key in part.reagents},
        tic=tic,
        read=sorted(part.readings),
    )
    profiles = sorted(set(profiles))
    records = {
        key: flat_series_record(
            part.readings[key],
            series,
            flatness,
            profiles=profiles,
            batch_peak_run_id=batch_peak_run_id,
        )
        for key, series in flatness.series.items()
    }
    flat = [key for key, record in records.items() if record["verdict"] == "flat"]
    counts = {
        **flatness.summary(),
        "readings": len(records),
        # The flat readings at assigned: the background a reader would most
        # want to see flagged. No tier moves on it.
        "flat_assigned": sum(
            1 for key in flat if part.readings[key].tier == TIER_ASSIGNED
        ),
        "flat_source_ions": sum(1 for key in flat if records[key]["source_ion"]),
    }
    return records, counts


#: The flat-series counts a run's summary sums over its partitions.
_FLAT_SUMMED = (
    "readings",
    "flat",
    "varying",
    "untestable",
    "flat_assigned",
    "flat_source_ions",
)


def read_batch_flatness(
    anchors: Iterable[Any],
    notation_by_id: dict[str, str],
    traces: dict[str, dict[str, float]],
    tic: dict[str, float],
    *,
    profile_by_sample: Optional[dict[str, str]] = None,
    batch_peak_run_id: Optional[str] = None,
) -> tuple[dict[str, dict], dict]:
    """Read every partition of a batch for flat traces.

    :param profile_by_sample: Sample -> the profile its latest completed run
        resolved; a partition's source list is its samples' profiles'.
    :return: Anchor id -> its flat-series record, and the run's summary: the
        thresholds, every partition's counts, and their sums.
    """
    profile_by_sample = profile_by_sample or {}
    records: dict[str, dict] = {}
    partitions = []
    for (mode_id, instrument_type), part in sorted(
        partition_anchors(anchors, notation_by_id).items(),
        key=lambda item: (str(item[0][0]), str(item[0][1])),
    ):
        held = {
            sample
            for key in (*part.traces, *part.reagents)
            for sample in traces.get(key, {})
        }
        profiles = sorted(
            {profile_by_sample[s] for s in held if profile_by_sample.get(s)}
        )
        found, counts = read_partition_flatness(
            part,
            traces,
            tic,
            profiles=profiles,
            batch_peak_run_id=batch_peak_run_id,
        )
        counts["profiles"] = profiles
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
            "flat_cv": FLAT_CV,
            "min_share": FLAT_MIN_SHARE,
            "min_spectra": MIN_SPECTRA,
            "min_varying_share": MIN_VARYING_SHARE,
        },
        "partitions": partitions,
        **{key: sum(p[key] for p in partitions) for key in _FLAT_SUMMED},
    }
    return records, summary


# --- the run ------------------------------------------------------------------


@dataclass
class LoadedBatch:
    """What a reading reads of a batch.

    :ivar anchors: Its anchors.
    :ivar notation_by_id: Its mechanisms' notations.
    :ivar traces: Anchor id -> sample -> its member's intensity.
    :ivar tic: Sample -> its total ion current.
    :ivar profile_by_sample: Sample -> the profile its latest completed run
        resolved (``config.resolved_profile.profile``); a decoy arm's run is
        not the sample's.
    """

    anchors: list
    notation_by_id: dict[str, str]
    traces: dict[str, dict[str, float]]
    tic: dict[str, float]
    profile_by_sample: dict[str, str]


async def _load_batch(sample_batch_id: str) -> LoadedBatch:
    """What a reading reads of the batch (:class:`LoadedBatch`)."""
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
        tic = {
            sample_item_id: value
            for sample_item_id, value in (
                await session.execute(
                    select(SampleItem.sample_item_id, SampleItem.tic).where(
                        SampleItem.sample_batch_id == sample_batch_id,
                        SampleItem.tic.is_not(None),
                    )
                )
            ).all()
        }
        runs = (
            await session.execute(
                select(
                    PeakAssignmentRun.sample_item_id,
                    PeakAssignmentRun.config["resolved_profile"]["profile"].as_string(),
                )
                .join(
                    SampleItem,
                    SampleItem.sample_item_id == PeakAssignmentRun.sample_item_id,
                )
                .where(
                    SampleItem.sample_batch_id == sample_batch_id,
                    PeakAssignmentRun.status == "completed",
                    PeakAssignmentRun.decoy.is_(None),
                )
                .order_by(PeakAssignmentRun.peak_assignment_run_utc_created)
            )
        ).all()
    traces: dict[str, dict[str, float]] = defaultdict(dict)
    for batch_peak_id, sample_item_id, intensity in rows:
        traces[batch_peak_id][sample_item_id] = intensity
    # The latest run speaks for its sample; one that resolved no profile (an
    # import) leaves an earlier one standing.
    profile_by_sample = {sample: profile for sample, profile in runs if profile}
    return LoadedBatch(
        anchors=list(anchors),
        notation_by_id=notation_by_id,
        traces=dict(traces),
        tic=tic,
        profile_by_sample=profile_by_sample,
    )


def _replace(provenance: dict, key: str, record: Optional[dict]) -> bool:
    """Put ``record`` under ``key``, or take the key off for None; whether
    anything changed."""
    if record is None:
        return provenance.pop(key, None) is not None
    if provenance.get(key) == record:
        return False
    provenance[key] = record
    return True


async def _write_records(
    sample_batch_id: str,
    records: dict[str, dict],
    flat_records: Optional[dict[str, dict]] = None,
) -> None:
    """Replace the batch's co-variation and flat-series records with this
    run's, under the fold lock.

    Every record an earlier reading left comes off first: a reading is of the
    ledger as it stands, and a pair or a trace it no longer reads is no longer
    read. Only the records change; the consensus keeps them from here on while
    it names the reading each was read for.
    """
    flat_records = flat_records or {}
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
            provenance = dict(anchor.provenance or {})
            changed = _replace(
                provenance, CO_VARIATION_KEY, records.get(anchor.batch_peak_id)
            )
            if _replace(
                provenance, FLAT_SERIES_KEY, flat_records.get(anchor.batch_peak_id)
            ):
                changed = True
            if changed:
                anchor.provenance = provenance
        await touch_ledger(session, sample_batch_id)
        await session.commit()


async def run_batch_co_variation(
    sample_batch_id: str, batch_peak_run_id: Optional[str] = None
) -> dict:
    """Read the batch's co-variation and its flat series, and record both on
    the anchors.

    :return: The run's summary (:func:`read_batch`), with the flat reading's
        under ``flat_series`` (:func:`read_batch_flatness`).
    """
    batch = await _load_batch(sample_batch_id)
    records, summary = await asyncio.to_thread(
        read_batch,
        batch.anchors,
        batch.notation_by_id,
        batch.traces,
        batch_peak_run_id=batch_peak_run_id,
    )
    flat_records, flat_summary = await asyncio.to_thread(
        read_batch_flatness,
        batch.anchors,
        batch.notation_by_id,
        batch.traces,
        batch.tic,
        profile_by_sample=batch.profile_by_sample,
        batch_peak_run_id=batch_peak_run_id,
    )
    await _write_records(sample_batch_id, records, flat_records)
    summary["flat_series"] = flat_summary
    return summary


def _flat_sentence(flat: Optional[dict]) -> str:
    """What the flat reading found, in a sentence; '' where it read nothing."""
    if not flat or not flat.get("readings"):
        return ""
    sources = flat.get("flat_source_ions", 0)
    return (
        f" {flat.get('flat', 0)} of {flat['readings']} batch peak"
        f"{'s' if flat['readings'] != 1 else ''} read flat against the batch's "
        f"normaliser, {sources} of them an ion the source's list names."
    )


def co_variation_outcome(counts: dict, sample_batch_id: str) -> dict:
    """The controller result the background-task decorator reports from."""
    notification_data = {"sample_batch_id": sample_batch_id}
    neutrals = counts["neutrals_read"]
    flat = _flat_sentence(counts.get("flat_series"))
    if not neutrals:
        return {
            "status": "partial",
            "message": (
                "No co-variation was read: no compound in the batch peaks is "
                "seen through two ionization channels."
                + flat
                + (" No tier moved." if flat else "")
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
            f"on too few spectra, {counts['untestable_flat']} flat)."
            + flat
            + " No tier moved."
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
    """Read the co-variation of the batch's channels and the flatness of its
    readings, as a batch run. Writes no member and no per-sample run; emits
    ``peak_assignment_reload`` so the ledgers refresh."""
    config = {
        "co_varying_r": CO_VARYING_R,
        "min_spectra": MIN_SPECTRA,
        "flat_spread": FLAT_SPREAD,
        "flat_cv": FLAT_CV,
        "flat_min_share": FLAT_MIN_SHARE,
        "min_varying_share": MIN_VARYING_SHARE,
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
    "LoadedBatch",
    "PartitionAnchors",
    "Reading",
    "co_variation_outcome",
    "co_variation_record",
    "flat_series_record",
    "partition_anchors",
    "partition_of",
    "read_batch",
    "read_batch_co_variation",
    "read_batch_flatness",
    "read_partition",
    "read_partition_flatness",
    "run_batch_co_variation",
]
