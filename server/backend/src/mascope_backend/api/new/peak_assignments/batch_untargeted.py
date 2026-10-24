"""Stage B once per anchor: the untargeted search over a batch's unassigned peaks.

The untargeted composition search is the expensive stage of assignment, and a
per-sample run pays for it once per sample - which is why it is off for batches.
In the batch ledger a species is one anchor whatever the sample count, so the
search can run once per anchor instead: on the anchor's **brightest member's
real spectrum** (never a synthetic consensus one), enumerating compositions for
that one peak while the isotope pattern is scored against the sample's whole
peak list. On a 32-sample batch that is about nineteen times fewer enumerations
than a per-sample pass, and the factor grows with the batch.

Every sample's search is collected before any is written, and the readings are
merged across samples (:func:`merge_search_rows`). Within one sample the finder
never enumerates a line an elected envelope has claimed; across samples it
cannot know, because an anchor and its M+1 are often represented in different
samples. So a line one sample's search reads as an isotopologue of another
searched anchor's committed ion takes that reading, its standalone reading from
its own sample is dropped, and that sample is searched again without it.

What the search decides for an anchor is then **propagated** to the anchor's
other members: each sample that holds one is measured against the winning
formula through the shared seeded scoring chain (one peak read, one
match pass per sample), so every member carries a fit of its own and the
sample's derived view agrees with the batch. A member whose peak the seeded
envelope does not reach stays unassigned rather than inheriting a number.

Nothing here writes a per-sample run: results live on the members and, through
the consensus, on the anchors. An explicit run on a sample still supersedes
them for that sample. Design: ``docs/dev/peak_assignment_batch_primary.md``,
section 5.3.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from sqlalchemy import select

from mascope_backend.api.controllers.samples.lib.samples_fetch import fetch_sample
from mascope_backend.api.lib.api_features import api_controller_background_task
from mascope_backend.api.new.cheminfo.utils import to_custom_element_format
from mascope_backend.api.new.match.params import default_match_params
from mascope_backend.api.new.peak_assignments.batch_peaks import (
    ROLE_ISO_CHILD,
    candidate_index,
    member_state,
    role_code,
    tier_code,
)
from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
    _acquire_batch_fold_lock,
    recompute_batch_consensus,
)
from mascope_backend.api.new.peak_assignments.batch_runs import (
    ACTION_SEARCH,
    complete_run,
    fail_run,
    start_run,
)
from mascope_backend.api.new.peak_assignments.config import (
    MAX_UNTARGETED_PEAKS_CEILING,
    PeakAssignmentConfig,
)
from mascope_backend.api.new.peak_assignments.engine import (
    ROLE_ARTIFACT,
    ROLE_REAGENT,
    SOURCE_UNTARGETED,
    SampleMassAccuracy,
    apply_partner_gates,
    evidence_for,
    pattern_scoring_for,
    tier_for_evidence,
    untargeted_matches_to_peak_assignments,
    untargeted_seeds,
    untargeted_targets,
)
from mascope_backend.api.new.peak_assignments.fold_view import fold_run_id
from mascope_backend.api.new.peak_assignments.profiles import (
    ResolvedProfile,
    resolve_profile,
    with_secondary_channels,
)
from mascope_backend.api.new.peak_assignments.seeded_scoring import score_seeds
from mascope_backend.api.new.peak_assignments.service import (
    _resolution_of,
    _searched_mechanisms,
    _seeded_fits,
    _untargeted_ionization_notations,
    fetch_mechanisms_by_notation,
    fetch_sample_mechanisms,
    load_sample_peaks,
    sample_reagent_offset,
)
from mascope_backend.api.new.peak_assignments.tiers import TIER_RANK, TIER_UNASSIGNED
from mascope_backend.db import BatchPeak, BatchPeakOccurrence, async_session
from mascope_backend.runtime import runtime
from mascope_backend.socket.notifications import (
    UserNotification,
    send_progress_user_notification,
)
from mascope_file.name import get_instrument_type
from mascope_tools.composition.finder import assign_compositions
from mascope_tools.composition.reagents import secondary_channels


#: The notification channel the search reports on, start to finish.
NOTIFICATION_TYPE = "search_batch_untargeted"

#: A member row stores its role as a code; these are the two a pre-pass writes,
#: resolved once so the comparison in :func:`choose_representatives` is on plain
#: integers.
CLAIMED_ROLE_CODES = frozenset({role_code(ROLE_REAGENT), role_code(ROLE_ARTIFACT)})

#: How many times a sample is searched again after the merge dropped one of its
#: representatives' standalone readings. Each round withholds more peaks and
#: never fewer, so the walk ends; a round can only free lines a dropped reading
#: had claimed, which may then be enumerated and read as some other anchor's
#: line in turn. The cap is a guard, not a budget: one round settles the case
#: the merge exists for, a parent and its M+1 represented in two samples.
MAX_RESEARCH_ROUNDS = 3


# --- pure helpers ---------------------------------------------------------------


def choose_representatives(members: Iterable[Any]) -> dict[str, Any]:
    """The brightest member of each anchor: the real spectrum its search runs on.

    A member with no intensity counts as the dimmest, not as a candidate.

    A member a pre-pass claimed is not a candidate at all. Its anchor reads as
    unassigned - a reagent or artifact row carries no formula, so the consensus
    has nothing to vote on - but the peak is the source's own chemistry or the
    detector's ringing, and was deliberately taken out of the per-sample stages
    before either ran. Without this the batch search would be the one path that
    puts an analyte formula back onto it, which is the phantom the pre-passes
    exist to prevent. An anchor whose members are all claimed this way is left
    with no representative and is never searched; a mixed anchor is still
    searched, on a member that is a real peak.

    :param members: Occurrence rows (or anything carrying ``batch_peak_id``,
        ``sample_item_id``, ``sample_peak_id``, ``intensity`` and ``role``).
    :return: anchor id -> the member chosen for it.
    """
    best: dict[str, Any] = {}
    for member in members:
        if getattr(member, "role", None) in CLAIMED_ROLE_CODES:
            continue
        current = best.get(member.batch_peak_id)
        if current is None or (member.intensity or 0.0) > (current.intensity or 0.0):
            best[member.batch_peak_id] = member
    return best


def group_by_sample(representatives: dict[str, Any]) -> dict[str, list[Any]]:
    """The representatives by the sample they are in: one search per sample,
    over every anchor that sample represents."""
    by_sample: dict[str, list[Any]] = defaultdict(list)
    for member in representatives.values():
        by_sample[member.sample_item_id].append(member)
    return dict(by_sample)


def owner_anchor_of(
    row: dict, rows_by_id: dict[str, dict], members_by_peak: dict[str, Any]
) -> Optional[str]:
    """The anchor an isotopologue row's owner peak sits in, in the same sample.

    The search names an owner by the assignment id it minted for the owner's
    row; that row names the owner's peak, and the peak's member names its
    anchor. Any missing link means no owner: the isotopologue then stands on
    its own rather than pointing at a peak the batch does not hold.
    """
    if row.get("role") != ROLE_ISO_CHILD or not row.get("owner_peak_assignment_id"):
        return None
    owner_row = rows_by_id.get(row["owner_peak_assignment_id"])
    if owner_row is None:
        return None
    owner_member = members_by_peak.get(owner_row["sample_peak_id"])
    return owner_member.batch_peak_id if owner_member is not None else None


def search_config(resolved_profile: ResolvedProfile, notations: list[str]):
    """The finder's configuration for this search - the orchestrator's, verbatim.

    Kept as a named function even though it now forwards: what makes the batch
    search comparable with a per-sample run is that both are configured from one
    resolution, and a helper that says so is easier to keep honest than a call
    site that happens to match.
    """
    return resolved_profile.search_config(notations)


@dataclass
class Annotation:
    """What the search decided for one anchor: the identity its representative
    won, the role it plays and, for an isotopologue, the anchor of its owner."""

    formula: str
    ion_formula: Optional[str]
    ionization_mechanism_id: Optional[str]
    role: str
    owner_batch_peak_id: Optional[str]


@dataclass(frozen=True)
class Reading:
    """One search row on a member of a searched anchor.

    ``owner_batch_peak_id`` is set when the row reads the member as an
    isotopologue of another searched anchor's ion (:func:`owner_anchor_of`):
    the reading is that anchor's claim on the member. Any other reading stands
    on its own.
    """

    batch_peak_id: str
    sample_item_id: str
    row: dict
    owner_batch_peak_id: Optional[str]

    @property
    def is_claim(self) -> bool:
        return self.owner_batch_peak_id is not None

    def annotation(self) -> Annotation:
        return Annotation(
            formula=self.row["assigned_formula"],
            ion_formula=self.row.get("ion_formula"),
            ionization_mechanism_id=self.row.get("ionization_mechanism_id"),
            role=self.row["role"],
            owner_batch_peak_id=self.owner_batch_peak_id,
        )


@dataclass
class SearchMerge:
    """Every sample's search, merged into one reading per anchor.

    :ivar annotations: Anchor id -> the reading the anchor takes.
    :ivar readings: Sample id -> the readings to write onto that sample's
        members: every standalone reading of an anchor decided standalone,
        and every claim by a committed ion, also one that is not the anchor's
        chosen claim: that sample's member keeps its own sample's reading.
    :ivar withheld: Sample id -> the peaks whose standalone reading was dropped
        for an isotopologue reading another sample gave their anchor. The
        sample is searched again without them, so the dropped election claims
        none of its lines.
    """

    annotations: dict[str, Annotation]
    readings: dict[str, list[Reading]]
    withheld: dict[str, set[str]]


def _reading_rank(reading: Reading) -> tuple:
    """Best first: the higher tier, then the better fit, then fixed keys, so
    a choice between two readings never follows the order samples were
    searched in."""
    fit = reading.row.get("fit_score")
    return (
        -TIER_RANK.get(reading.row.get("tier"), 0),
        -(fit if fit is not None else 0.0),
        reading.owner_batch_peak_id or "",
        reading.sample_item_id,
        str(reading.row.get("sample_peak_id") or ""),
    )


def search_readings(
    rows_by_sample: dict[str, list[dict]], members: Iterable[Any]
) -> dict[str, list[Reading]]:
    """Every sample's search rows as readings of the searched anchors.

    A row on no member of a searched anchor, or with no formula, is no reading.
    Nor is one on a member a pre-pass claimed: it is not a representative, so
    the search did not enumerate it, but an envelope scored against the whole
    spectrum can still land an isotopologue row on it, and that row must not
    overwrite the claim.

    :param rows_by_sample: Sample id -> that sample's search rows.
    :param members: Every member of the searched anchors.
    :return: Anchor id -> its readings, in no meaningful order.
    """
    members_by_sample: dict[str, dict[str, Any]] = defaultdict(dict)
    for member in members:
        members_by_sample[member.sample_item_id][member.sample_peak_id] = member
    readings: dict[str, list[Reading]] = defaultdict(list)
    for sample_item_id, rows in rows_by_sample.items():
        members_by_peak = members_by_sample.get(sample_item_id, {})
        rows_by_id = {row["peak_assignment_id"]: row for row in rows}
        for row in rows:
            member = members_by_peak.get(row["sample_peak_id"])
            if member is None or not row.get("assigned_formula"):
                continue
            if member.role in CLAIMED_ROLE_CODES:
                continue
            readings[member.batch_peak_id].append(
                Reading(
                    batch_peak_id=member.batch_peak_id,
                    sample_item_id=sample_item_id,
                    row=row,
                    owner_batch_peak_id=owner_anchor_of(
                        row, rows_by_id, members_by_peak
                    ),
                )
            )
    return dict(readings)


def merge_search_rows(
    rows_by_sample: dict[str, list[dict]], members: Iterable[Any]
) -> SearchMerge:
    """Merge every sample's search rows into one reading per anchor.

    Each sample's search reads the anchors it represents and, through their
    envelopes, any line of theirs in its spectrum. An anchor can therefore be
    read in two samples: on its own where it is the representative, and as an
    isotopologue where another anchor's ion claimed its peak. Within one sample
    the finder settles that by never enumerating a claimed line, and the merge
    applies the same rule across samples:

    - An anchor some sample reads as an isotopologue of a **committed** ion
      takes that reading. An ion is committed when its anchor's decided
      reading is its own election: the anchor stands on its own and was not
      itself taken as another ion's line.
    - Otherwise the anchor keeps its standalone reading.
    - A claim by an ion that is not committed is dropped. An anchor left with
      nothing else stays unannotated rather than taking a line of an ion the
      batch does not hold.

    A chain resolves from its head: an anchor's claims wait on their owners'
    own decisions, so an M+2 claimed by an ion that another sample read as an
    M+1 is not that ion's line. Anchors are decided lightest first, which
    decides an owner before its lines. Nothing depends on the order of
    ``rows_by_sample``: of several readings the best is taken
    (:func:`_reading_rank`).

    These rules decide the anchor's annotation. The members are written
    sample by sample (:attr:`SearchMerge.readings`): a standalone reading when
    the anchor is decided standalone, and every claim by a committed ion, the
    chosen one or not. A member whose sample read the line as the line of
    another committed ion than the chosen one keeps that sample's reading.

    :param rows_by_sample: Sample id -> that sample's search rows.
    :param members: Every member of the searched anchors (``batch_peak_id``,
        ``sample_item_id``, ``sample_peak_id``, ``role``).
    :return: The merge.
    """
    readings_by_anchor = search_readings(rows_by_sample, members)
    decided: dict[str, Optional[Reading]] = {}
    deciding: set[str] = set()

    def committed(claim: Reading) -> bool:
        # The owner is a target only in the sample it represents, so its one
        # standalone reading is the election that made the claim.
        owner = decide(claim.owner_batch_peak_id)
        return owner is not None and not owner.is_claim

    def decide(anchor: str) -> Optional[Reading]:
        if anchor in decided:
            return decided[anchor]
        if anchor in deciding:
            # A loop of claims, two lines each read as the other's
            # isotopologue. Only an element whose lighter isotope is the rarer
            # one can make it. An ion still being decided is not committed, so
            # the anchor decided first takes the other's claim, and the order
            # below fixes which that is.
            return None
        deciding.add(anchor)
        readings = readings_by_anchor.get(anchor, [])
        claims = [r for r in readings if r.is_claim and committed(r)]
        own = [r for r in readings if not r.is_claim]
        deciding.discard(anchor)
        decided[anchor] = min(claims or own, key=_reading_rank, default=None)
        return decided[anchor]

    def lightest(anchor: str) -> tuple:
        mzs = [
            float(r.row["sample_peak_mz"])
            for r in readings_by_anchor[anchor]
            if r.row.get("sample_peak_mz") is not None
        ]
        return (min(mzs, default=float("inf")), anchor)

    order = sorted(readings_by_anchor, key=lightest)
    for anchor in order:
        decide(anchor)

    annotations: dict[str, Annotation] = {}
    kept: dict[str, list[Reading]] = defaultdict(list)
    withheld: dict[str, set[str]] = defaultdict(set)
    for anchor in order:
        chosen = decided[anchor]
        if chosen is not None:
            annotations[anchor] = chosen.annotation()
        for reading in readings_by_anchor[anchor]:
            if reading.is_claim:
                if committed(reading):
                    kept[reading.sample_item_id].append(reading)
            elif chosen is not None and chosen.is_claim:
                withheld[reading.sample_item_id].add(reading.row["sample_peak_id"])
            else:
                kept[reading.sample_item_id].append(reading)
    return SearchMerge(
        annotations=annotations, readings=dict(kept), withheld=dict(withheld)
    )


# --- the search ---------------------------------------------------------------------


async def _unassigned_anchors_and_members(
    session, sample_batch_id: str
) -> tuple[dict[str, BatchPeak], list[BatchPeakOccurrence]]:
    """The batch's anchors that carry no assignment, with every member of theirs."""
    anchors = (
        (
            await session.execute(
                select(BatchPeak).where(
                    BatchPeak.sample_batch_id == sample_batch_id,
                    BatchPeak.consensus_tier == TIER_UNASSIGNED,
                )
            )
        )
        .scalars()
        .all()
    )
    members = (
        (
            await session.execute(
                select(BatchPeakOccurrence)
                .join(
                    BatchPeak,
                    BatchPeak.batch_peak_id == BatchPeakOccurrence.batch_peak_id,
                )
                .where(
                    BatchPeak.sample_batch_id == sample_batch_id,
                    BatchPeak.consensus_tier == TIER_UNASSIGNED,
                )
            )
        )
        .scalars()
        .all()
    )
    return {anchor.batch_peak_id: anchor for anchor in anchors}, list(members)


async def _search_sample(
    sample_item_id: str, target_peak_ids: set[str], config: PeakAssignmentConfig
) -> tuple[list[dict], int]:
    """Stage B over one sample's representative peaks, in the context of its
    whole spectrum.

    :return: The engine's assignment rows for the peaks it explained - the
        representatives and any isotopologue peaks it paired to them, shaped as
        ledger rows that will never be written - and the number of this sample's
        representatives the cap left unsearched. A batch run has one config for
        many samples and no per-sample run row to stamp, so the count is carried
        out to the batch's own result instead: an anchor nobody searched is not
        an anchor nothing could explain, and the counts are otherwise
        indistinguishable.
    """
    sample = await fetch_sample(sample_item_id)
    peaks_df = load_sample_peaks(sample)
    frame = (
        peaks_df[
            (peaks_df["intensity"] > 0)
            & (peaks_df["intensity"] >= config.peak_intensity_threshold)
        ]
        .sort_values("mz")
        .reset_index(drop=True)
    )
    targets, search_scope = untargeted_targets(
        frame[frame["sample_peak_id"].isin(target_peak_ids)],
        config.max_untargeted_peaks,
        MAX_UNTARGETED_PEAKS_CEILING,
    )
    unsearched = search_scope["eligible_peaks"] - search_scope["searched_peaks"]
    if targets.empty:
        return [], unsearched
    if search_scope["limited"]:
        runtime.logger.info(
            f"Batch untargeted search on sample '{sample.sample_item_name}' "
            f"searches {search_scope['searched_peaks']} of "
            f"{search_scope['eligible_peaks']} representative peaks"
        )
    _, mechanisms = await fetch_sample_mechanisms(sample)
    # The mode's own mechanisms decide whether there is anything to search at
    # all. An opportunistic channel is an addition to a sample's chemistry, not
    # a substitute for it: a mode that declares nothing is a mode nobody has
    # configured, and searching it through a channel the source happens to show
    # would be assigning a sample whose ionization is unknown.
    primary_notations, _ = _untargeted_ionization_notations(mechanisms)
    if not primary_notations:
        runtime.logger.info(
            f"Untargeted batch search skips sample '{sample.sample_item_name}': "
            "no polarity-compatible ionization mechanisms."
        )
        return [], unsearched
    # Resolved per sample, not per batch: a batch can hold more than one
    # ionization mode, and the chemistry belongs to the sample that was measured.
    resolved_profile = resolve_profile(
        config,
        mechanism_notations=[m.ionization_mechanism for m in mechanisms],
        instrument_type=get_instrument_type(sample.filename),
        polarity=sample.polarity,
    )
    # The opportunistic channels are read off this sample's own spectrum, on the
    # whole frame rather than the searched representatives: the evidence is the
    # source's cluster ions, which are bright and belong to no anchor.
    secondary_mechanisms = await fetch_mechanisms_by_notation(
        [
            channel.notation
            for channel in secondary_channels(resolved_profile.profile.name)
        ],
        sample.polarity,
    )
    resolved_profile = with_secondary_channels(
        resolved_profile,
        frame["mz"].to_numpy(),
        frame["intensity"].to_numpy(),
        [m.ionization_mechanism for m in secondary_mechanisms],
    )
    # The per-sample run's own rule, so a secondary channel the mode declares
    # is searched once here too.
    notations, mechanism_id_by_notation = _untargeted_ionization_notations(
        _searched_mechanisms(mechanisms, secondary_mechanisms, resolved_profile)
    )
    runtime.logger.info(
        f"Untargeted batch search of sample '{sample.sample_item_name}' "
        f"searches profile '{resolved_profile.profile.name}' / context "
        f"'{resolved_profile.context.name}': {resolved_profile.element_ranges} "
        f"at {resolved_profile.mz_precision_ppm} ppm"
    )
    # The whole spectrum is the frame - isotope patterns are scored against it -
    # while only the representatives are enumerated.
    match_params = await default_match_params(sample_item_id)
    # No Stage A ran on this path, so nothing has fitted this sample's mass
    # width; the match tolerance stands in for it (see `pattern_scoring_for`).
    # A batch search therefore judges a candidate a little more loosely than a
    # run of the same sample would, which is the honest state of it: the width
    # is a measurement, and this path has not made it.
    scoring = pattern_scoring_for(
        match_params,
        SampleMassAccuracy(),
        resolved_profile.fallback_sigma_ppm,
        await _resolution_of(sample),
    )
    search_columns = [
        column
        for column in ("mz", "intensity", "signal_to_noise")
        if column in frame.columns
    ]
    matches_df, _ = await asyncio.to_thread(
        assign_compositions,
        frame[search_columns],
        search_config(resolved_profile, notations),
        resolved_profile.heuristics_config(),
        targets=targets["mz"].tolist(),
        scoring=scoring,
    )
    fit_by_seed = await _seeded_fits(
        sample,
        match_params,
        untargeted_seeds(
            matches_df, mechanism_id_by_notation, to_custom_element_format
        ),
        # No Stage A ran on this path, so the centre is the one a run of the
        # sample scored at, or the lines this search's profile claims off the
        # whole peak list where no run recorded one.
        reagent_offset=await sample_reagent_offset(
            sample, peaks_df=peaks_df, resolved_profile=resolved_profile
        ),
    )
    rows = untargeted_matches_to_peak_assignments(
        matches_df,
        peaks_df=frame,
        sample_item_id=sample_item_id,
        peak_assignment_run_id=fold_run_id(sample_item_id),
        candidate_threshold=config.candidate_threshold,
        assigned_threshold=config.assigned_threshold,
        mechanism_id_by_notation=mechanism_id_by_notation,
        formula_formatter=to_custom_element_format,
        max_alternatives=config.max_alternatives,
        minor_channels=resolved_profile.minor_channels,
        fit_by_seed=fit_by_seed,
    )
    return gate_search_rows(
        rows,
        resolved_profile=resolved_profile,
        mechanism_id_by_notation=mechanism_id_by_notation,
        tier_bands=config.tier_bands(),
    ), unsearched


def gate_search_rows(
    rows: list[dict],
    *,
    resolved_profile: ResolvedProfile,
    mechanism_id_by_notation: dict[str, str],
    tier_bands: dict[str, float] | None,
) -> list[dict]:
    """The partner gate over a batch search's own rows, in place.

    A batch search runs no Stage A, no mass gate and no cross-channel pass:
    its rows are the finder's, judged by the minor-channel policy alone. The
    partner gate is the one judgement a sample's own rows can make for
    themselves, and without it a batch run keeps the election a run of the
    same sample turns back - tropylium as protonated C7H6 here, toluene less
    a hydride there (``engine.apply_partner_gates``).

    :param rows: The search's rows, as the engine built them.
    :param resolved_profile: The sample's resolved chemistry.
    :param mechanism_id_by_notation: The searched mechanisms.
    :param tier_bands: The batch's evidence bands, for a swapped reading's
        tier.
    :return: The same rows.
    """
    apply_partner_gates(
        rows,
        notation_by_id={
            mechanism_id: notation
            for notation, mechanism_id in mechanism_id_by_notation.items()
        },
        minor_channels=resolved_profile.minor_channels,
        partner_gated_channels=resolved_profile.partner_gated_channels,
        tier_bands=tier_bands,
    )
    return rows


async def _apply_search_rows(
    sample_batch_id: str,
    sample_item_id: str,
    readings: list[Reading],
) -> int:
    """Write the merged search's readings onto one sample's members and their
    anchors' registries, under the batch's fold lock.

    Only members of searched anchors carry a reading (:func:`search_readings`):
    a row the search produced for a peak that already had an assignment (an
    isotopologue it paired to a Stage-A peak, say) is left alone. The members
    are read again under the lock, and one a pre-pass has claimed since is
    left alone too.

    :return: How many members were written.
    """
    if not readings:
        return 0
    written = 0
    async with async_session() as session:
        await _acquire_batch_fold_lock(session, sample_batch_id)
        members = (
            (
                await session.execute(
                    select(BatchPeakOccurrence).where(
                        BatchPeakOccurrence.sample_item_id == sample_item_id,
                        BatchPeakOccurrence.batch_peak_id.in_(
                            sorted({reading.batch_peak_id for reading in readings})
                        ),
                    )
                )
            )
            .scalars()
            .all()
        )
        members_by_peak = {member.sample_peak_id: member for member in members}
        anchors = {
            anchor.batch_peak_id: anchor
            for anchor in (
                await session.execute(
                    select(BatchPeak).where(
                        BatchPeak.batch_peak_id.in_(
                            [member.batch_peak_id for member in members]
                        )
                    )
                )
            )
            .scalars()
            .all()
        }
        for reading in readings:
            row = reading.row
            member = members_by_peak.get(row["sample_peak_id"])
            if member is None or member.role in CLAIMED_ROLE_CODES:
                continue
            anchor = anchors[member.batch_peak_id]
            registry = list(anchor.candidates or [])
            index = candidate_index(
                registry,
                row["assigned_formula"],
                row.get("ion_formula"),
                row.get("ionization_mechanism_id"),
                source=SOURCE_UNTARGETED,
            )
            if len(registry) != len(anchor.candidates or []):
                anchor.candidates = registry
            member.candidate = index
            member.tier = tier_code(row["tier"])
            member.fit_score = row.get("fit_score")
            member.role = role_code(row["role"])
            member.owner_batch_peak_id = reading.owner_batch_peak_id
            member.p_correct = None  # the untargeted stage is uncalibrated
            written += 1
        await session.commit()
    return written


async def _propagate_to_sample(
    sample_batch_id: str,
    sample_item_id: str,
    annotations: dict[str, Annotation],
    config: PeakAssignmentConfig,
    *,
    only_unassigned: bool = True,
    source: str = SOURCE_UNTARGETED,
    skip_candidate: Optional[dict[str, int]] = None,
    displaced: Optional[list] = None,
) -> int:
    """Measure the annotated anchors' formulas against one other sample and
    write the result onto its members of those anchors.

    The seeded chain scores each (formula, mechanism) as an ion against the
    sample's own peaks. A member whose peak the ion's envelope paired to takes
    the ion's fit, tiered under the search's own bands; a member the envelope
    did not reach stays unassigned. A reagent member names its ion's registry
    entry, so it is not an unassigned member here, and the search does not
    write over the claim. Runs outside the fold lock, writes inside.

    The batch curation measures a pinned identity the same way, over every
    member rather than the unassigned ones (``only_unassigned=False``),
    skipping members that already carry it (``skip_candidate``, anchor id ->
    registry index) and archiving what each re-pointed member read before
    (``displaced``, appended to). ``source`` is recorded on a registry entry
    this write creates.

    :return: How many members were assigned.
    """
    seeds = {
        (a.formula, a.ionization_mechanism_id)
        for a in annotations.values()
        if a.formula and a.ionization_mechanism_id
    }
    if not seeds:
        return 0
    sample = await fetch_sample(sample_item_id)
    match_params = await default_match_params(sample_item_id)
    # Centred where this search's own re-score centres the searched sample:
    # the reagent lines its configuration's profile claims.
    ion_by_seed, fit_by_ion, errors_by_pairing, _ = await score_seeds(
        sample, seeds, match_params, config=config
    )
    if not ion_by_seed:
        return 0

    conditions = [
        BatchPeakOccurrence.sample_item_id == sample_item_id,
        BatchPeakOccurrence.batch_peak_id.in_(list(annotations)),
    ]
    if only_unassigned:
        conditions.append(BatchPeakOccurrence.candidate.is_(None))
    assigned = 0
    async with async_session() as session:
        await _acquire_batch_fold_lock(session, sample_batch_id)
        members = (
            (await session.execute(select(BatchPeakOccurrence).where(*conditions)))
            .scalars()
            .all()
        )
        if not members:
            return 0
        anchors = {
            anchor.batch_peak_id: anchor
            for anchor in (
                await session.execute(
                    select(BatchPeak).where(
                        BatchPeak.batch_peak_id.in_([m.batch_peak_id for m in members])
                    )
                )
            )
            .scalars()
            .all()
        }
        for member in members:
            annotation = annotations[member.batch_peak_id]
            if (
                skip_candidate
                and skip_candidate.get(member.batch_peak_id) == member.candidate
            ):
                continue
            ion_id = ion_by_seed.get(
                (annotation.formula, annotation.ionization_mechanism_id)
            )
            if (
                ion_id is None
                or (ion_id, member.sample_peak_id) not in errors_by_pairing
            ):
                continue
            fit = fit_by_ion.get(ion_id)
            evidence = evidence_for(fit, annotation.formula)
            if fit is None or evidence is None:
                continue
            if displaced is not None:
                displaced.append(member_state(member))
            anchor = anchors[member.batch_peak_id]
            registry = list(anchor.candidates or [])
            index = candidate_index(
                registry,
                annotation.formula,
                annotation.ion_formula,
                annotation.ionization_mechanism_id,
                source=source,
            )
            if len(registry) != len(anchor.candidates or []):
                anchor.candidates = registry
            member.candidate = index
            member.tier = tier_code(
                tier_for_evidence(
                    evidence,
                    candidate_threshold=config.candidate_threshold,
                    assigned_threshold=config.assigned_threshold,
                )
            )
            member.fit_score = fit
            member.role = role_code(annotation.role)
            member.owner_batch_peak_id = annotation.owner_batch_peak_id
            member.p_correct = None
            assigned += 1
        await session.commit()
    return assigned


def _progress(
    sample_batch_id: str,
    item_index: int,
    total: int,
    message: str,
    user_id: Optional[int],
    process_id: Optional[str],
    parent_id: Optional[str],
) -> UserNotification:
    return UserNotification(
        process_id=process_id,
        parent_id=parent_id,
        type=NOTIFICATION_TYPE,
        status="pending",
        message=message,
        data={
            "sample_batch_id": sample_batch_id,
            "_room_ids": [sample_batch_id],
            "_user_id": user_id,
            "_total_samples": total,
            "_item_index": item_index,
        },
    )


async def run_batch_untargeted_search(
    sample_batch_id: str,
    config: PeakAssignmentConfig,
    user_id: Optional[int] = None,
    process_id: Optional[str] = None,
    parent_id: Optional[str] = None,
) -> dict:
    """Search every unassigned anchor of a batch once, then propagate.

    Two passes, one progress bar: the search over each sample that holds a
    representative, then the seeded re-score over each sample that holds a
    member of an annotated anchor. Between them the samples' rows are merged
    into one reading per anchor (:func:`merge_search_rows`), a sample whose
    standalone reading of a peak gave way to an isotopologue reading from
    another sample is searched again without that peak, and only then is
    anything written. Every anchor touched is recomputed once at the end, so
    the consensus - and with it the batch ledger and every derived Sample
    view - reflects the new members.

    :return: Counts: anchors searched, anchors annotated, members assigned by
        propagation, samples searched, searched again and re-scored, and the
        anchors that took an isotopologue reading from another sample.
    """
    async with async_session() as session:
        anchors, members = await _unassigned_anchors_and_members(
            session, sample_batch_id
        )
    counts = {
        "anchors_searched": len(anchors),
        # Anchors the cap kept out of the search. Zero unless a caller set
        # `max_untargeted_peaks` or a sample carries more representatives than
        # the ceiling; reported because a blank anchor means something different
        # when nothing looked at it.
        "anchors_unsearched": 0,
        "anchors_annotated": 0,
        "members_propagated": 0,
        "samples_searched": 0,
        # Samples searched a second time after the merge dropped one of their
        # representatives' standalone readings (`merge_search_rows`).
        "samples_researched": 0,
        # Anchors a sample's own search read on their own and that took the
        # isotopologue reading another sample's search gave them instead.
        "isotopologues_carried": 0,
        "samples_rescored": 0,
        "samples_failed": 0,
    }
    if not anchors:
        return counts

    representatives = choose_representatives(members)
    by_sample = group_by_sample(representatives)
    members_by_sample: dict[str, list] = defaultdict(list)
    for member in members:
        members_by_sample[member.sample_item_id].append(member)

    reports = process_id is not None
    total = len(by_sample) + len(members_by_sample)
    step = 0

    async def report(message: str) -> None:
        if reports:
            await send_progress_user_notification(
                _progress(
                    sample_batch_id,
                    step,
                    total,
                    message,
                    user_id,
                    process_id,
                    parent_id,
                )
            )

    # Every sample's rows are collected before any is written: which reading an
    # anchor takes is decided over all of them (`merge_search_rows`), never by
    # the sample that happened to be searched last.
    rows_by_sample: dict[str, list[dict]] = {}
    unsearched_by_sample: dict[str, int] = {}
    withheld: dict[str, set[str]] = defaultdict(set)
    walked_the_batch = False
    try:
        for sample_item_id, sample_representatives in by_sample.items():
            await report(
                f"Searching untargeted compositions, sample {step + 1}/{total}."
            )
            try:
                (
                    rows_by_sample[sample_item_id],
                    unsearched_by_sample[sample_item_id],
                ) = await _search_sample(
                    sample_item_id,
                    {member.sample_peak_id for member in sample_representatives},
                    config,
                )
                counts["samples_searched"] += 1
            except Exception:  # noqa: BLE001 - one bad sample must not abort the search
                # A sample reaches here for reasons that are about that sample
                # alone - a peak file that has moved, a spectrum that will not
                # read - and the other samples' anchors are still searchable.
                # Logged with the traceback: the caller only ever sees a count.
                counts["samples_failed"] += 1
                runtime.logger.exception(
                    f"The untargeted batch search failed for sample '{sample_item_id}'."
                )
            step += 1

        merge = merge_search_rows(rows_by_sample, members)
        for _ in range(MAX_RESEARCH_ROUNDS):
            # A sample whose representative lost its standalone reading to an
            # isotopologue reading from another sample is searched again
            # without that peak: the dropped election may have claimed lines
            # of the sample's other representatives, which now get read.
            fresh = {
                sample_item_id: peaks - withheld[sample_item_id]
                for sample_item_id, peaks in merge.withheld.items()
                if peaks - withheld[sample_item_id]
            }
            if not fresh:
                break
            total += len(fresh)
            for sample_item_id in sorted(fresh):
                withheld[sample_item_id] |= fresh[sample_item_id]
                await report(
                    "Searching again without the isotope lines found in other "
                    f"samples, sample {step + 1}/{total}."
                )
                try:
                    (
                        rows_by_sample[sample_item_id],
                        unsearched_by_sample[sample_item_id],
                    ) = await _search_sample(
                        sample_item_id,
                        {member.sample_peak_id for member in by_sample[sample_item_id]}
                        - withheld[sample_item_id],
                        config,
                    )
                    counts["samples_researched"] += 1
                except Exception:  # noqa: BLE001 - the first search's rows stand
                    # The sample's first search stands. The merge still drops
                    # the readings it would have withheld, and the lines those
                    # claimed, so nothing that contradicts the carried reading
                    # is written; the lines they freed stay unread.
                    runtime.logger.exception(
                        "Searching again failed for sample "
                        f"'{sample_item_id}'; its first search stands."
                    )
                step += 1
            merge = merge_search_rows(rows_by_sample, members)
        counts["anchors_unsearched"] = sum(unsearched_by_sample.values())
        counts["isotopologues_carried"] = len(
            {
                (sample_item_id, peak)
                for dropped in (withheld, merge.withheld)
                for sample_item_id, peaks in dropped.items()
                for peak in peaks
            }
        )

        for sample_item_id, readings in merge.readings.items():
            try:
                await _apply_search_rows(sample_batch_id, sample_item_id, readings)
            except Exception:  # noqa: BLE001 - one bad sample must not abort the search
                counts["samples_failed"] += 1
                runtime.logger.exception(
                    "Writing the untargeted batch search failed for sample "
                    f"'{sample_item_id}'."
                )
        annotations = merge.annotations
        counts["anchors_annotated"] = len(annotations)

        if annotations:
            for sample_item_id in members_by_sample:
                await report(
                    f"Measuring the found compositions, sample {step + 1}/{total}."
                )
                try:
                    counts["members_propagated"] += await _propagate_to_sample(
                        sample_batch_id, sample_item_id, annotations, config
                    )
                    counts["samples_rescored"] += 1
                except Exception:  # noqa: BLE001 - one bad sample must not abort the pass
                    counts["samples_failed"] += 1
                    runtime.logger.exception(
                        "Measuring the found compositions failed for sample "
                        f"'{sample_item_id}'."
                    )
                step += 1
        walked_the_batch = True
    finally:
        # In a finally for the reason `backfill_sample_batch_peaks` sets out at
        # length: every sample above has COMMITTED its members while the anchors
        # still describe the members they had before, and nothing else will fix
        # them - a later arriving fold recomputes only the anchors its own sample
        # touched. The vector is cancellation, which walks past the per-sample
        # `except` arms above because it is a BaseException.
        try:
            await recompute_batch_consensus(sample_batch_id, set(anchors))
        except Exception:
            if walked_the_batch:
                raise
            # Already unwinding. Raising here would REPLACE the exception that
            # interrupted the loop - a CancelledError above all, which has to
            # reach the caller as a cancellation and not as a database error.
            runtime.logger.exception(
                "The untargeted batch search was interrupted and its deferred "
                "consensus pass then failed for batch "
                f"'{sample_batch_id}'. The anchors its committed samples touched "
                "still describe their previous members; rebuilding the ledger "
                "recomputes them."
            )
    return counts


def search_outcome(counts: dict, sample_batch_id: str) -> dict:
    """The controller result the background-task decorator reports from."""
    notification_data = {"sample_batch_id": sample_batch_id}
    if not counts["anchors_searched"]:
        return {
            "status": "partial",
            "message": (
                "No untargeted search was run: every batch peak of this batch "
                "already carries an assignment, or the batch has no batch peaks yet."
            ),
            "data": counts,
            "_notification_data": notification_data,
        }
    # A sample that raised is reported rather than left to the log: the counts
    # below are otherwise indistinguishable from a batch that simply had less
    # to find, and the anchors it holds were not searched.
    left = counts.get("anchors_unsearched", 0)
    unsearched = (
        f" {left} anchor{'s' if left != 1 else ''} "
        f"{'were' if left != 1 else 'was'} left unsearched by the peak cap."
        if left
        else ""
    )
    failed = counts.get("samples_failed", 0)
    skipped = (
        f" {failed} sample{'s' if failed != 1 else ''} could not be read and "
        f"{'were' if failed != 1 else 'was'} skipped."
        if failed
        else ""
    )
    # Said because it is the one place a sample's own reading of a peak was
    # overruled, and a user comparing a sample with the batch will see it.
    carried = counts.get("isotopologues_carried", 0)
    lines = (
        f" {carried} batch peak{'s' if carried != 1 else ''} "
        f"{'were' if carried != 1 else 'was'} read as an isotope line of a "
        "composition found in another sample."
        if carried
        else ""
    )
    return {
        "status": "partial" if failed else "success",
        "message": (
            f"Searched {counts['anchors_searched']} unassigned batch peak"
            f"{'s' if counts['anchors_searched'] != 1 else ''} across "
            f"{counts['samples_searched']} sample"
            f"{'s' if counts['samples_searched'] != 1 else ''}: "
            f"{counts['anchors_annotated']} assigned a composition, and "
            f"{counts['members_propagated']} member peak"
            f"{'s' if counts['members_propagated'] != 1 else ''} in other samples "
            f"measured against it.{lines}{skipped}{unsearched}"
        ),
        "data": counts,
        "_notification_data": notification_data,
    }


@api_controller_background_task(
    success_notification_rooms=["sample_batch_id"],
    success_reload=[("peak_assignment", "sample_batch_id")],
    error_notification_rooms=["sample_batch_id"],
)
async def search_batch_untargeted(
    sample_batch_id: str,
    config: PeakAssignmentConfig | None = None,
    independent_transaction: bool = False,
    user_id: int | None = None,
    process_id: str | None = None,
    parent_id: str | None = None,
) -> dict:
    """Run the untargeted composition search once per unassigned anchor of a
    batch, on each anchor's brightest member, and measure the result against
    the anchor's other members. Writes no per-sample run; emits
    ``peak_assignment_reload`` so the ledgers refresh."""
    search_config = config or PeakAssignmentConfig()
    # A batch run: the search's parameters go on the record, the current
    # run's state is snapshotted before the first anchor is touched, and the
    # run becomes current on completion - or is marked failed, the live
    # ledger then holding whatever the search wrote before it stopped.
    run_id = await start_run(
        sample_batch_id,
        ACTION_SEARCH,
        config=search_config.model_dump(mode="json"),
        user_id=user_id,
    )
    try:
        counts = await run_batch_untargeted_search(
            sample_batch_id,
            search_config,
            user_id=user_id,
            process_id=process_id,
            parent_id=parent_id,
        )
    except Exception as exc:
        await fail_run(sample_batch_id, run_id, f"{type(exc).__name__}: {exc}")
        raise
    await complete_run(sample_batch_id, run_id, summary=counts)
    return search_outcome(counts, sample_batch_id)
