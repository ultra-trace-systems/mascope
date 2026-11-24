"""Which samples a pass's series stage has to judge again (MAS-1188).

After the series detection a pass judges again every sample with a member a
series record corroborates (``batch_rejudge.series_corroborated``). The
detection writes every record again on each run, so that was every sample on
every pass, though one arriving sample changes few of them.

A judging is a function of what it reads: the sample's members, the batch's
decisions about them, the settings and the engine. So a sample whose members
and decisions read exactly as they did right after its last series judging
would be judged to the same tiers again, and is left as it is. Each pass
records, for every sample it judged at the series stage or left so, a digest
of that state (:func:`judged_state`), in its summary's series entry
(:data:`JUDGED_STATES`); the next pass compares (:func:`settled`).

The digest holds what a judging reads, and what it only cites on a row it
leaves out, since a judging again writes a member's tier and nothing else:

- each member of the sample: its peak, the row it was folded from, its
  reading (the formula and mechanism its candidate names), tier, fit, role,
  owner, P(correct), intensity, mass error, and which stage's mark it carries;
- each batch peak it has a member in: its m/z and consensus; the series
  record standing on it, by the formula and mechanism it vouches for
  (:func:`_series_read`); the stripped line standing on it, by its parent,
  label, share and offset (:func:`_strip_read`);
- its partitions' library ions decided present, one per ion with the batch
  peak that speaks for it (:func:`_species_read`), and their water-ladder
  records, which its claims read whether or not it has a member there;
- the engine's version and the settings' hash.

The versions and run ids the stages stamp on their records, and their times,
are left out: they change on every pass whatever the decision. A sample with
no recorded state, or one that reads otherwise, is judged as before; so is
every sample a pass's stage wrote in. A sample whose decisions moved under its
own judging (its consensus recompute took a record off, or put one back) has
no state recorded: the next pass judges it again.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy import or_, select

from mascope_backend.api.new.peak_assignments.batch_peaks import (
    KNOWN_SPECIES_KEY,
    ROLE_REAGENT,
    WATER_LADDER_KEY,
    batch_mark_of,
    resolve_candidate,
    standing_known_species_record,
    standing_merged_isotopologue_of_record,
    standing_series_record,
    standing_water_ladder_record,
)
from mascope_backend.api.new.peak_assignments.config import (
    PEAK_ASSIGNMENT_ENGINE_VERSION,
    PeakAssignmentConfig,
    settings_hash,
)
from mascope_backend.api.new.peak_assignments.reagent_pass import batch_species_from
from mascope_backend.db import (
    BatchPeak,
    BatchPeakOccurrence,
    BatchPeakRun,
    async_session,
)
from mascope_backend.runtime import runtime


#: The key of a pass's series entry that holds, per sample, the digest of the
#: state it was left in (:func:`judged_state`).
JUDGED_STATES = "judged_states"

#: The stage whose entry holds them.
SERIES_STAGE = "series"

#: How many of the batch's newest runs are read for the states: a pass
#: further back than these is no starting point, and every sample is judged.
RUNS_READ = 20

#: What a record carries that changes on every run whatever it decides.
_VOLATILE = frozenset({"version", "batch_peak_run_id"})


def _plain(value: Any) -> Any:
    """A record without its version, run id and times, JSON-ready."""
    if isinstance(value, dict):
        return {
            str(k): _plain(v)
            for k, v in value.items()
            if k not in _VOLATILE and not str(k).startswith("utc")
        }
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _digest(value: Any) -> str:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode()).hexdigest()[:24]


def _member_part(member: Any, anchor: Any) -> list:
    identity = resolve_candidate(getattr(anchor, "candidates", None), member.candidate)
    mark = batch_mark_of(member)
    return [
        member.batch_peak_id,
        str(member.sample_peak_id),
        member.peak_assignment_id,
        member.candidate,
        identity.get("formula"),
        identity.get("ion_formula"),
        identity.get("ionization_mechanism_id"),
        member.tier,
        member.fit_score,
        member.role,
        member.owner_batch_peak_id,
        member.p_correct,
        member.intensity,
        member.mz_delta_ppm,
        mark.get("stage") if mark is not None else None,
    ]


def _series_read(anchor: Any) -> Optional[list]:
    """What a judging reads of the series record standing on a batch peak: a
    row that reads its formula through its mechanism carries it, and the
    tiering counts it as a second observation (``tiering.series_anchor``).
    The rest of it, the unit, the root, the depth, names the series in the
    row's reasons, which a judging again writes nowhere."""
    record = standing_series_record(anchor)
    if record is None:
        return None
    return [
        record.get("formula"),
        record.get("ionization_mechanism_id"),
        bool(record.get("has_anchor")),
    ]


def _strip_read(anchor: Any) -> Optional[list]:
    """What a judging reads of a stripped line standing on a batch peak
    (``batch_decisions.strip_from``): the parent, its reading, the line, its
    share and its offset. What the strip read over the spectra the row only
    cites."""
    record = standing_merged_isotopologue_of_record(anchor)
    if record is None:
        return None
    offset = record.get("mz_offset")
    return [
        record.get("batch_peak_id"),
        record.get("formula"),
        record.get("ionization_mechanism_id"),
        record.get("label"),
        record.get("ratio"),
        offset if offset is not None else ["mz", record.get("mz")],
        record.get("above"),
        record.get("verdict"),
    ]


def _anchor_part(anchor: Any) -> list:
    return [
        anchor.batch_peak_id,
        anchor.mz,
        anchor.consensus_formula,
        anchor.consensus_role,
        _series_read(anchor),
        _strip_read(anchor),
    ]


def _species_read(anchors: Iterable[Any]) -> list:
    """The library ions a partition's batch decided present, as a sample's run
    reads them (``known_species.batch_species_for``, whose rule this follows):
    one per ion, the batch peak that speaks for it, and the ion. The files and
    counts the decision was made on the row only cites."""
    by_ion: dict[tuple, tuple] = {}
    for anchor in sorted(anchors, key=lambda a: a.batch_peak_id):
        if anchor.consensus_role != ROLE_REAGENT:
            continue
        record = standing_known_species_record(anchor)
        one = batch_species_from(record, anchor.batch_peak_id) if record else None
        if one is None:
            continue
        ion = (one.cluster.formula, one.cluster.charge)
        present = record.get("present") or 0
        if ion not in by_ion or present > by_ion[ion][0]:
            by_ion[ion] = (present, anchor.batch_peak_id, one)
    return [
        [list(ion), batch_peak_id, _plain(vars(one.cluster)), one.reason]
        for ion, (_, batch_peak_id, one) in sorted(by_ion.items())
    ]


def _partition_part(anchors: list) -> list:
    """What a sample's claims read of its partition: the library ions decided
    present (:func:`_species_read`), and the water-ladder records whole but
    for their versions, runs and times."""
    return [
        _species_read(anchors),
        [
            [anchor.batch_peak_id, _plain(record)]
            for anchor in sorted(anchors, key=lambda a: a.batch_peak_id)
            if (record := standing_water_ladder_record(anchor)) is not None
        ],
    ]


def _partition(anchor: Any) -> tuple:
    return (str(anchor.ionization_mode_id), str(anchor.intensity_variable))


def judged_state(
    members: Iterable[Any],
    anchors: dict[str, Any],
    partition_anchors: Iterable[Any],
    key: str,
) -> tuple[str, str]:
    """The digests of what a sample's judging reads (module docstring).

    :param members: The sample's members in the batch.
    :param anchors: Batch peak id -> the batch peak, every one a member is in.
    :param partition_anchors: The batch's peaks that carry a water-ladder or a
        known-species record, or read as a reagent, of any partition: those of
        the sample's own are read.
    :param key: The pass's engine and settings (:func:`pass_key`).
    :return: The decisions' digest, and the whole state's (decisions and
        members).
    """
    members = sorted(members, key=lambda m: m.batch_peak_id)
    own = [anchors[m.batch_peak_id] for m in members if m.batch_peak_id in anchors]
    partitions = {_partition(a) for a in own}
    decisions = _digest(
        [
            key,
            [_anchor_part(a) for a in own],
            [
                _partition_part([a for a in partition_anchors if _partition(a) == p])
                for p in sorted(partitions)
            ],
        ]
    )
    whole = _digest(
        [
            decisions,
            [_member_part(m, anchors.get(m.batch_peak_id)) for m in members],
        ]
    )
    return decisions, whole


def pass_key(config: Optional[PeakAssignmentConfig]) -> str:
    """What a judging reads of the pass besides the ledger: the engine's
    version and the settings' hash."""
    return f"{PEAK_ASSIGNMENT_ENGINE_VERSION}:{settings_hash(config)}"


async def read_states(
    sample_batch_id: str, samples: Iterable[str], key: str
) -> dict[str, tuple[str, str]]:
    """Each sample's state as the ledger reads it now (:func:`judged_state`).

    :return: Sample id -> its two digests; a sample with no member in the
        batch has none.
    """
    wanted = sorted(set(samples))
    if not wanted:
        return {}
    async with async_session() as session:
        rows = (
            await session.execute(
                select(BatchPeakOccurrence, BatchPeak)
                .join(
                    BatchPeak,
                    BatchPeak.batch_peak_id == BatchPeakOccurrence.batch_peak_id,
                )
                .where(
                    BatchPeak.sample_batch_id == sample_batch_id,
                    BatchPeakOccurrence.sample_item_id.in_(wanted),
                )
            )
        ).all()
        partition_anchors = (
            (
                await session.execute(
                    select(BatchPeak).where(
                        BatchPeak.sample_batch_id == sample_batch_id,
                        or_(
                            BatchPeak.provenance[WATER_LADDER_KEY].is_not(None),
                            BatchPeak.provenance[KNOWN_SPECIES_KEY].is_not(None),
                            BatchPeak.consensus_role == ROLE_REAGENT,
                        ),
                    )
                )
            )
            .scalars()
            .all()
        )
    members: dict[str, list] = {}
    anchors: dict[str, Any] = {}
    for member, anchor in rows:
        members.setdefault(member.sample_item_id, []).append(member)
        anchors[anchor.batch_peak_id] = anchor
    return {
        sample: judged_state(own, anchors, partition_anchors, key)
        for sample, own in members.items()
    }


async def last_states(sample_batch_id: str) -> dict[str, str]:
    """The states the newest pass that recorded them left its samples in, by
    sample: from its series entry (:data:`JUDGED_STATES`). Empty where no pass
    recorded any.

    Any pass will do, also one that failed or was cancelled after its series
    stage: a state is what the ledger read right after a judging, and a
    sample that reads so now is judged to the same tiers again however the
    ledger came back to it."""
    async with async_session() as session:
        summaries = (
            await session.execute(
                select(BatchPeakRun.summary)
                .where(BatchPeakRun.sample_batch_id == sample_batch_id)
                .order_by(BatchPeakRun.batch_peak_run_utc_created.desc())
                .limit(RUNS_READ)
            )
        ).scalars()
        for summary in summaries:
            stages = summary.get("stages") if isinstance(summary, dict) else None
            for entry in stages if isinstance(stages, list) else ():
                if not isinstance(entry, dict) or entry.get("stage") != SERIES_STAGE:
                    continue
                states = entry.get(JUDGED_STATES)
                if isinstance(states, dict):
                    return {str(k): str(v) for k, v in states.items()}
    return {}


def settled(
    named: dict[str, set[str]], now: dict[str, tuple[str, str]], last: dict[str, str]
) -> set[str]:
    """The samples named for a judging that read as they did after their last
    one: the digest of their state now is the one recorded then."""
    return {
        sample
        for sample in named
        if sample in now and sample in last and now[sample][1] == last[sample]
    }


@dataclass
class SeriesJudging:
    """The series stage's judging of a pass: the samples it leaves as they
    are (:func:`settled`), and the state each sample it judged is left in.

    :ivar before: Sample -> its digests as the judging began.
    :ivar states: Sample -> the digest of the state it is left in, which the
        pass records (:data:`JUDGED_STATES`).
    :ivar left: How many samples it left as they were.
    """

    sample_batch_id: str
    key: str
    before: dict[str, tuple[str, str]] = field(default_factory=dict)
    states: dict[str, str] = field(default_factory=dict)
    left: int = 0

    @classmethod
    async def narrow(
        cls,
        sample_batch_id: str,
        config: Optional[PeakAssignmentConfig],
        judged: Iterable[str],
        corroborated: dict[str, set[str]],
    ) -> tuple["SeriesJudging", dict[str, set[str]]]:
        """Begin it: the samples the series corroborates in, less those
        that read as they did after their last judging.

        :param judged: The samples judged whatever they read: those the
            stage wrote in.
        :param corroborated: Sample -> the anchors a series record
            corroborates in it (``batch_rejudge.series_corroborated``).
        :return: The judging, and the corroborated samples still to judge.
        """
        judging = cls(sample_batch_id, pass_key(config))
        judged = set(judged)
        try:
            judging.before = await read_states(
                sample_batch_id, judged | set(corroborated), judging.key
            )
            last = await last_states(sample_batch_id)
        except Exception as exc:  # noqa: BLE001 - judging every one is safe
            runtime.logger.warning(
                f"Reading what batch '{sample_batch_id}' was judged at failed; "
                f"every sample is judged again: {exc}"
            )
            judging.before = {}
            return judging, corroborated
        same = settled(corroborated, judging.before, last) - judged
        judging.states = {sample: last[sample] for sample in same}
        judging.left = len(same)
        return judging, {s: a for s, a in corroborated.items() if s not in same}

    async def judged(self, sample_item_id: str) -> None:
        """Record the state a sample's judging left it in, where its
        decisions read as they did when the judging began; else none, and the
        next pass judges it again."""
        self.states.pop(sample_item_id, None)
        before = self.before.get(sample_item_id)
        if before is None:
            return
        try:
            now = (
                await read_states(self.sample_batch_id, [sample_item_id], self.key)
            ).get(sample_item_id)
        except Exception as exc:  # noqa: BLE001 - no state is the safe answer
            runtime.logger.warning(
                f"Reading what sample '{sample_item_id}' was judged at failed: {exc}"
            )
            return
        if now is not None and now[0] == before[0]:
            self.states[sample_item_id] = now[1]

    def outcome(self, judged: dict) -> dict:
        """The judging's outcome, with the samples it left as they were and
        the states to record."""
        if self.left:
            data = judged.setdefault("data", {})
            data["samples_left"] = self.left
            plural = self.left != 1
            judged["message"] = (
                f"{judged.get('message', '')} {self.left} "
                f"sample{'s' if plural else ''} read as "
                f"{'they' if plural else 'it'} did after "
                f"{'their' if plural else 'its'} last judging and "
                f"{'were' if plural else 'was'} left as "
                f"{'they were' if plural else 'it was'}."
            ).strip()
        judged[JUDGED_STATES] = dict(sorted(self.states.items()))
        return judged


def outcome_of(judging: Optional[SeriesJudging], judged: dict) -> dict:
    """A stage's judging outcome, the series judging's states with it where
    there is one (:meth:`SeriesJudging.outcome`)."""
    return judging.outcome(judged) if judging is not None else judged


def states_of(outcomes: Iterable[Any]) -> dict:
    """What a pass's summary entry of a stage keeps of its judging's states:
    ``{JUDGED_STATES: ...}`` where a step's outcome holds them, else nothing.
    A summary keeps of an outcome only its status, message and counts, so
    the states, one digest per sample, stay out of the pass's notifications."""
    for outcome in outcomes:
        if isinstance(outcome, dict) and isinstance(outcome.get(JUDGED_STATES), dict):
            return {JUDGED_STATES: outcome[JUDGED_STATES]}
    return {}


__all__ = [
    "JUDGED_STATES",
    "SeriesJudging",
    "outcome_of",
    "states_of",
    "judged_state",
    "last_states",
    "pass_key",
    "read_states",
    "settled",
]
