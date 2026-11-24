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

Which anchors are searched is decided twice. A representative is taken where
its height clears the run's intensity floor and the run's cap reaches it, as a
run's own search takes peaks, and also where its anchor is **persistent**: its
peak is present in more of its partition's spectra than the batch's own split
of their occurrence distribution (:func:`occurrence_split`), however faint.
Noise does not come back at one m/z in most of a batch's spectra; ions do. An
anchor searched on persistence alone carries that admission on the record
(``batch_peaks.PERSISTENCE_KEY``), and the reading the search elects there is
held at candidate until a second observation corroborates it
(``batch_peaks.hold_persistence_only``): persistence says the ion is there,
not which formula it is. Design: ``docs/dev/assignment_quality_plan.md``, step
4.2b.

What the search decides for an anchor is then **propagated** to the anchor's
other members: each sample that holds one is measured against the winning
formula through the shared seeded scoring chain (one peak read, one
match pass per sample), so every member carries a fit of its own and the
sample's derived view agrees with the batch. A member whose peak the seeded
envelope does not reach stays unassigned rather than inheriting a number.

An anchor the last search found nothing for is not searched again while
nothing that search read of it has changed (``batch_search_records``).

Nothing here writes a per-sample run: results live on the members and, through
the consensus, on the anchors. An explicit run on a sample still supersedes
them for that sample. Design: ``docs/dev/peak_assignment_batch_primary.md``,
section 5.3.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Iterable, Optional

import numpy as np
from sqlalchemy import func, or_, select

from mascope_backend.api.controllers.samples.lib.samples_fetch import fetch_sample
from mascope_backend.api.lib.api_features import api_controller_background_task
from mascope_backend.api.new.peak_assignments.batch_library import (
    chemistry_read,
    library_read,
)
from mascope_backend.api.new.peak_assignments.batch_lock import _acquire_batch_fold_lock
from mascope_backend.api.new.peak_assignments.batch_peaks import (
    MARK_SEARCH,
    REASON_PERSISTENCE_ONLY,
    ROLE_ISO_CHILD,
    ROLE_UNASSIGNED,
    TIER_REASONS_KEY,
    batch_mark_of,
    candidate_index,
    mark_member,
    measured_state,
    resolve_candidate,
    role_code,
    tier_code,
)
from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
    recompute_batch_consensus,
)
from mascope_backend.api.new.peak_assignments.batch_runs import (
    ACTION_SEARCH,
    in_batch_run,
    touch_ledger,
)
from mascope_backend.api.new.peak_assignments.batch_search_records import (
    search_key,
    searched_before_note,
    searched_empty_record_of,
    searched_in_vain,
    write_search_records,
)
from mascope_backend.api.new.peak_assignments.batch_staleness import count_unread
from mascope_backend.api.new.peak_assignments.config import PeakAssignmentConfig
from mascope_backend.api.new.peak_assignments.engine import (
    evidence_for,
    formula_identity,
)
from mascope_backend.api.new.peak_assignments.fold_view import fold_run_id
from mascope_backend.api.new.peak_assignments.pipeline import (
    BATCH_SEARCH,
    PROPAGATION,
    AssignmentPath,
    RunContext,
    keep_search_rows,
    run_stages,
)
from mascope_backend.api.new.peak_assignments.row_terms import (
    ROLE_ARTIFACT,
    ROLE_M0,
    ROLE_REAGENT,
    SOURCE_UNTARGETED,
)
from mascope_backend.api.new.peak_assignments.tiers import (
    TIER_CANDIDATE,
    TIER_RANK,
    TIER_UNASSIGNED,
)
from mascope_backend.db import BatchPeak, BatchPeakOccurrence, async_session
from mascope_backend.runtime import runtime
from mascope_backend.socket.notifications import (
    UserNotification,
    send_progress_user_notification,
)


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

#: The fewest spectra a partition must hold before its occurrence distribution
#: is split. Under ten a share moves in steps of a tenth or more, and the split
#: falls between two of a handful of values wherever they happen to lie: on the
#: testbed's ledgers folded from three to six samples it lands at 0.50, 0.58,
#: 0.70 or 0.83 with nothing between the modes to tell them apart.
MIN_PERSISTENCE_SPECTRA = 10

#: The band the split is held inside. Otsu's method returns a split whatever
#: the distribution, also one with no two modes to separate: a batch whose
#: peaks are nearly all transient would split near its low end and take every
#: recurring noise line, one whose peaks are nearly all persistent near its
#: top and take nothing. Where the distribution is the bimodal one a batch
#: shows - nearly every trace in under a tenth of the spectra or in over nine
#: tenths - the split falls well inside: on the testbed's ledgers of 87 to 547
#: samples, Orbitrap and TOF, at 0.48 to 0.56 every time.
SPLIT_BOUNDS = (0.25, 0.75)


# --- pure helpers ---------------------------------------------------------------


def occurrence_split(shares: Iterable[float], n_spectra: int) -> Optional[float]:
    """The batch's own split of a partition's occurrence distribution, or None.

    Each anchor is one trace, so the distribution is the share of the
    partition's spectra each anchor's peak is present in, one value per
    anchor: an ion present in five hundred spectra and a noise spike seen
    twice count once each. The split is Otsu's: the threshold that maximises
    the variance between the shares below it and those above (Otsu 1979,
    IEEE Trans. Syst. Man Cybern. 9, 62-66), placed midway between the two
    neighbouring shares it falls between, so no share sits on it. Held inside
    :data:`SPLIT_BOUNDS`.

    :param shares: Each anchor's share of the partition's spectra, in [0, 1].
    :param n_spectra: How many spectra the partition holds.
    :return: The split, or None where the partition holds fewer than
        :data:`MIN_PERSISTENCE_SPECTRA` spectra or every anchor's share is the
        same: there is then nothing to split, and no anchor is persistent.
    """
    if n_spectra < MIN_PERSISTENCE_SPECTRA:
        return None
    values, counts = np.unique(
        np.asarray(list(shares), dtype=float), return_counts=True
    )
    if len(values) < 2:
        return None
    weights = counts / counts.sum()
    below = np.cumsum(weights)[:-1]
    above = 1.0 - below
    below_sum = np.cumsum(weights * values)[:-1]
    total = float((weights * values).sum())
    between = below * above * (below_sum / below - (total - below_sum) / above) ** 2
    cut = int(np.argmax(between))
    split = 0.5 * float(values[cut] + values[cut + 1])
    low, high = SPLIT_BOUNDS
    return min(max(split, low), high)


@dataclass(frozen=True)
class Occurrence:
    """How often one anchor's peak recurs across its partition's spectra.

    :ivar n_present: The spectra holding the peak.
    :ivar n_samples: The spectra the partition holds.
    :ivar split: The partition's split (:func:`occurrence_split`).
    """

    n_present: int
    n_samples: int
    split: float

    @property
    def share(self) -> float:
        return self.n_present / self.n_samples

    def record(
        self, annotation: Optional["Annotation"], batch_peak_run_id: Optional[str]
    ) -> dict:
        """The anchor's persistence record (``batch_peaks.PERSISTENCE_KEY``):
        the admission and the reading the search elected on it, where it
        elected a monoisotopic one of the anchor's own."""
        # An isotope line of another batch peak's ion is no reading of the
        # anchor's own; only a line names an owner.
        elected = annotation is not None and annotation.role == ROLE_M0
        return {
            "share": round(self.share, 4),
            "n_present": self.n_present,
            "n_samples": self.n_samples,
            "split": round(self.split, 4),
            "formula": annotation.formula if elected else None,
            "ion_formula": annotation.ion_formula if elected else None,
            "ionization_mechanism_id": (
                annotation.ionization_mechanism_id if elected else None
            ),
            "batch_peak_run_id": batch_peak_run_id,
        }


def persistent_anchors(
    counts: Iterable[tuple[str, tuple, int]], spectra: dict[tuple, int]
) -> tuple[dict[str, Occurrence], dict[tuple, Optional[float]]]:
    """The anchors whose peak is present in more of their partition's spectra
    than the partition's split.

    :param counts: (anchor id, partition, the spectra its peak is in), for
        every anchor of the batch whatever it reads: the distribution is the
        batch's, not the unassigned anchors'.
    :param spectra: Partition -> the spectra it holds.
    :return: The persistent anchors' occurrences, and each partition's split
        (None where it has none).
    """
    by_partition: dict[tuple, list[tuple[str, int]]] = defaultdict(list)
    for anchor, partition, n_present in counts:
        by_partition[partition].append((anchor, n_present))
    persistent: dict[str, Occurrence] = {}
    splits: dict[tuple, Optional[float]] = {}
    for partition, anchors in by_partition.items():
        n_samples = spectra.get(partition, 0)
        split = (
            occurrence_split((n / n_samples for _, n in anchors), n_samples)
            if n_samples
            else None
        )
        splits[partition] = split
        if split is None:
            continue
        for anchor, n_present in anchors:
            if n_present / n_samples > split:
                persistent[anchor] = Occurrence(n_present, n_samples, split)
    return persistent, splits


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

    Nor is a member that already reads a formula. An anchor most of whose
    members a pre-pass claimed stays unassigned however its other members read
    (``batch_peaks._source_consensus``), and a peak its sample's ledger
    explains is never searched, as a run never searches its Stage A's peaks
    (:attr:`pipeline.RunContext.explained_peak_ids`). The anchor is searched on
    the brightest member that reads nothing.

    :param members: Occurrence rows (or anything carrying ``batch_peak_id``,
        ``sample_item_id``, ``sample_peak_id``, ``intensity``, ``role`` and
        ``candidate``).
    :return: anchor id -> the member chosen for it.
    """
    best: dict[str, Any] = {}
    for member in members:
        if getattr(member, "role", None) in CLAIMED_ROLE_CODES:
            continue
        if getattr(member, "candidate", None) is not None:
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


async def _readings_to_measure_again(
    session, sample_batch_id: str
) -> tuple[dict[str, Annotation], set[str]]:
    """The readings the search assigned that a member of their batch peak
    does not carry: a sample folded in since the search, or folded again from
    a new run of its own, brings a member that reads nothing there.

    A batch peak the search assigned is no unassigned anchor any more, so the
    search never searches it again (:func:`_unassigned_anchors_and_members`),
    and such a member would stay unmeasured. Its batch peak's reading is
    proposed to it instead, as the search's own propagation proposes a found
    composition to every other sample's member (MAS-983, the batch stages'
    design note 4.3). A batch peak is the search's where a member carries the
    search's mark (``batch_peaks.batch_mark``) and reads the batch peak's
    consensus; a member that reads a reading of its own is its file's, and
    is not touched.

    :return: Anchor id -> its reading, an isotope line's naming its owner,
        and the samples whose member of an owner reads nothing.
    """
    rows = (
        await session.execute(
            select(BatchPeak, BatchPeakOccurrence)
            .join(
                BatchPeakOccurrence,
                BatchPeakOccurrence.batch_peak_id == BatchPeak.batch_peak_id,
            )
            .where(
                BatchPeak.sample_batch_id == sample_batch_id,
                BatchPeak.consensus_tier != TIER_UNASSIGNED,
                BatchPeak.consensus_formula.is_not(None),
                BatchPeak.consensus_role.is_(None),
            )
        )
    ).all()
    searched: dict[str, BatchPeak] = {}
    unread: dict[str, set[str]] = defaultdict(set)
    for anchor, member in rows:
        if member.candidate is None and member.role == role_code(ROLE_UNASSIGNED):
            unread[anchor.batch_peak_id].add(member.sample_item_id)
            continue
        mark = batch_mark_of(member)
        if mark is None or mark.get("stage") != MARK_SEARCH:
            continue
        identity = resolve_candidate(anchor.candidates, member.candidate)
        if identity.get("formula") == anchor.consensus_formula and str(
            identity.get("ionization_mechanism_id")
        ) == str(anchor.ionization_mechanism_id):
            searched[anchor.batch_peak_id] = anchor
    # A monoisotopic reading a member lacks, with the search's isotope lines
    # of it, which the measurement reads beside it as the search's own
    # propagation does; a line alone is measured through its owner.
    owners = {
        batch_peak_id
        for batch_peak_id, anchor in searched.items()
        if not anchor.isotopologue_of
        and unread.get(batch_peak_id)
        and anchor.ionization_mechanism_id
    }
    annotations = {
        batch_peak_id: Annotation(
            formula=anchor.consensus_formula,
            ion_formula=anchor.consensus_ion_formula,
            ionization_mechanism_id=anchor.ionization_mechanism_id,
            role=ROLE_ISO_CHILD if anchor.isotopologue_of else ROLE_M0,
            owner_batch_peak_id=anchor.isotopologue_of or None,
        )
        for batch_peak_id, anchor in searched.items()
        if batch_peak_id in owners or anchor.isotopologue_of in owners
    }
    samples = {sample for key in owners for sample in unread[key]}
    return annotations, samples


async def _occurrence_table(
    session, sample_batch_id: str
) -> tuple[dict[str, Occurrence], dict[tuple, Optional[float]]]:
    """The batch's persistent anchors and each partition's split, read off the
    fold: every anchor's members and every partition's samples, counted.

    A partition is the fold's: the ionization mode and the instrument type an
    anchor was minted under, whose samples are the spectra its peaks can be
    in. Counted off the members rather than read off ``n_present``, which is
    as fresh as the anchor's last consensus.

    :return: :func:`persistent_anchors`' answer.
    """
    partition = (BatchPeak.ionization_mode_id, BatchPeak.intensity_variable)
    joined = BatchPeakOccurrence.batch_peak_id == BatchPeak.batch_peak_id
    counts = (
        await session.execute(
            select(BatchPeak.batch_peak_id, *partition, func.count())
            .join(BatchPeakOccurrence, joined)
            .where(BatchPeak.sample_batch_id == sample_batch_id)
            .group_by(BatchPeak.batch_peak_id, *partition)
        )
    ).all()
    spectra = (
        await session.execute(
            select(
                *partition,
                func.count(func.distinct(BatchPeakOccurrence.sample_item_id)),
            )
            .join(BatchPeakOccurrence, joined)
            .where(BatchPeak.sample_batch_id == sample_batch_id)
            .group_by(*partition)
        )
    ).all()
    return persistent_anchors(
        ((anchor, (mode, unit), int(n)) for anchor, mode, unit, n in counts),
        {(mode, unit): int(n) for mode, unit, n in spectra},
    )


async def _held_by_persistence(sample_batch_id: str, anchor_ids: set[str]) -> int:
    """How many of ``anchor_ids`` the persistence rule holds at candidate:
    their reading is the one elected on persistence alone, nothing
    corroborates it, and the samples' vote did not already put it lower."""
    if not anchor_ids:
        return 0
    async with async_session() as session:
        anchors = (
            (
                await session.execute(
                    select(BatchPeak).where(
                        BatchPeak.sample_batch_id == sample_batch_id,
                        BatchPeak.batch_peak_id.in_(sorted(anchor_ids)),
                    )
                )
            )
            .scalars()
            .all()
        )
    return sum(
        anchor.consensus_tier == TIER_CANDIDATE
        and any(
            isinstance(reason, dict) and reason.get("rule") == REASON_PERSISTENCE_ONLY
            for reason in (anchor.provenance or {}).get(TIER_REASONS_KEY) or []
        )
        for anchor in anchors
    )


async def _search_sample(
    sample_item_id: str,
    target_peak_ids: set[str],
    config: PeakAssignmentConfig,
    persistent_peak_ids: frozenset[str] = frozenset(),
) -> tuple[list[dict], int, set[str]]:
    """The search over one sample's representative peaks, in the context of its
    whole spectrum: the stage list's BATCH_SEARCH path (``pipeline.py``).

    The run's stages, every one of them, with the search on the representatives
    proposed and the sample's recorded measurement: its chemistry resolved and
    its source's peaks claimed as a run's are, the search and the halogen-twin
    re-read after it, the seeded re-score, and the judging a run's rows get
    (the mass gate at the sample's recorded calibration, the partner gate told
    what the lists name, the cross-channel pass, the tiering rules and the
    claims), then the identification level. What the judging weighs a reading
    against is what the search read in this sample: the batch ledger holds
    the sample's other readings.

    :param persistent_peak_ids: The representatives whose anchors are
        persistent, which the search takes however faint.
    :return: The judged rows on the peaks it explained - the representatives
        and any isotopologue peaks it paired to them, shaped as ledger rows
        that will never be written - the number of this sample's
        representatives the cap left unsearched, and the representatives
        persistence alone admitted. A batch run has one config for many
        samples and no per-sample run row to stamp, so the count is carried
        out to the batch's own result instead: an anchor nobody searched is not
        an anchor nothing could explain, and the counts are otherwise
        indistinguishable.
    """
    sample = await fetch_sample(sample_item_id)
    ctx = await run_stages(
        RunContext(
            path=BATCH_SEARCH,
            sample=sample,
            config=config,
            run_id=fold_run_id(sample_item_id),
            keep=keep_search_rows,
            targets=frozenset(target_peak_ids),
            persistent_peak_ids=frozenset(persistent_peak_ids),
            explained_peak_ids=await _explained_peaks(sample),
        )
    )
    return ctx.result


async def _explained_peaks(sample) -> frozenset[str]:
    """The peaks of a sample its batch ledger already explains: a member that
    reads a formula, and one a pre-pass claimed.

    What a run's Stage A and pre-passes are to its search, the batch ledger is
    to the batch search: a peak it explains is neither a halogen twin's peak
    nor its twin, and the search writes no row onto it.

    :param sample: The sample view row.
    :return: Their sample peak ids.
    """
    async with async_session() as session:
        peaks = (
            (
                await session.execute(
                    select(BatchPeakOccurrence.sample_peak_id)
                    .join(
                        BatchPeak,
                        BatchPeak.batch_peak_id == BatchPeakOccurrence.batch_peak_id,
                    )
                    .where(
                        BatchPeak.sample_batch_id == sample.sample_batch_id,
                        BatchPeakOccurrence.sample_item_id == sample.sample_item_id,
                        or_(
                            BatchPeakOccurrence.candidate.is_not(None),
                            BatchPeakOccurrence.role.in_(sorted(CLAIMED_ROLE_CODES)),
                        ),
                    )
                )
            )
            .scalars()
            .all()
        )
    return frozenset(str(peak) for peak in peaks)


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
            mark_member(member, MARK_SEARCH)
            member.candidate = index
            member.tier = tier_code(row["tier"])
            member.fit_score = row.get("fit_score")
            member.role = role_code(row["role"])
            member.owner_batch_peak_id = reading.owner_batch_peak_id
            member.p_correct = None  # the untargeted stage is uncalibrated
            written += 1
        await touch_ledger(session, sample_batch_id)
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
    prefer_better: bool = False,
    path: AssignmentPath = PROPAGATION,
    series: Optional[dict[str, dict]] = None,
    mark: Optional[str] = None,
) -> int:
    """Measure the annotated anchors' readings on one sample and write the
    result onto its members of those anchors.

    The stage list's PROPAGATION path (``pipeline.py``): every stage of a run,
    with one given reading proposed per member and the sample's recorded
    measurement. Each annotated anchor's reading is proposed to its member as
    a list proposes its formula to a peak, measured as an ion in one seeded
    pass over the sample's peaks, elected as Stage A elects a list's reading,
    read as the row a run of the sample makes of it - a list's where the
    sample's lists hold its ion, the search's otherwise - and judged as a run
    judges that row - the mass gate at the sample's recorded calibration, the
    partner gate, the cross-channel pass, the tiering rules, the claims - so a
    member takes the tier a run of its sample gives the same reading
    (MAS-858). A member whose peak the reading's envelope did not take keeps
    what it reads, and so does one whose reading the judging read another way.
    A reagent member names its ion's registry entry, so it is not an
    unassigned member here, and the search does not write over the claim.
    Runs outside the fold lock, writes inside.

    The batch curation measures a pinned identity the same way, over every
    member rather than the unassigned ones (``only_unassigned=False``),
    skipping members that already carry it (``skip_candidate``, anchor id ->
    registry index) and archiving what each re-pointed member read before
    (``displaced``, appended to): a member the persistence rule holds at
    candidate at the tier it was measured at (``batch_peaks.measured_state``).
    ``source`` is recorded on a registry entry this write creates. A pin is a
    person's choice (``path=pipeline.PERSONS_CHOICE``): its reading is asked
    the spectrum's rules alone, as a hand assignment on a sample's row is.

    The series detection measures its proposals over every member too, and
    with ``prefer_better``: a member that already reads its peak another way
    takes the new reading only where its evidence is at least the member's
    own, one that already reads the same formula is left as it is, and one a
    pre-pass claimed or read as another peak's isotope line is not touched. A
    homologue is a reason to measure a formula, not to overrule a better
    measured reading. It hands over its records (``series``, anchor id -> the
    record of the series that proposed the reading): a member's row that reads
    its anchor's proposal carries the record, as a run of the sample reads it
    off the batch once the anchor's consensus names the formula
    (``batch_lines.stamp_series``), since the series is the second observation a
    proposal rests on. While the proposals are measured the batch serves no
    reader the record yet (``batch_peaks.standing_series_record``).

    :return: How many members were assigned.
    """
    conditions = [
        BatchPeakOccurrence.sample_item_id == sample_item_id,
        BatchPeakOccurrence.batch_peak_id.in_(list(annotations)),
    ]
    if only_unassigned:
        conditions.append(BatchPeakOccurrence.candidate.is_(None))
    async with async_session() as session:
        held = (
            await session.execute(
                select(
                    BatchPeakOccurrence.batch_peak_id,
                    BatchPeakOccurrence.sample_peak_id,
                ).where(*conditions)
            )
        ).all()
    held_anchors = {anchor for anchor, _ in held}
    given = {
        anchor: annotation
        for anchor, annotation in annotations.items()
        if anchor in held_anchors
        and annotation.formula
        and annotation.ionization_mechanism_id
    }
    if not given:
        return 0
    records = series or {}

    async def keep(ctx: RunContext) -> int:
        return await _write_measured_members(
            sample_batch_id,
            sample_item_id,
            given,
            ctx.judged.rows,
            conditions,
            source=source,
            skip_candidate=skip_candidate,
            displaced=displaced,
            prefer_better=prefer_better,
            mark=mark,
        )

    ctx = await run_stages(
        RunContext(
            path=path,
            sample=await fetch_sample(sample_item_id),
            config=config,
            run_id=fold_run_id(sample_item_id),
            keep=keep,
            given=given,
            series_by_peak={
                str(peak): records[anchor] for anchor, peak in held if anchor in records
            },
        )
    )
    return ctx.result


async def measure_on_sample(
    sample_item_id: str,
    annotations: dict[str, Annotation],
    config: PeakAssignmentConfig,
    path: AssignmentPath,
) -> RunContext:
    """Run a path's stages for annotated readings on one sample and keep
    nothing: what a propagation or a pin would write there, read back off the
    judged ledger (``batch_curation.measure_batch_peak_candidate``).

    :return: The pass, its judged rows on :attr:`RunContext.judged`.
    """

    async def keep(ctx: RunContext) -> RunContext:
        return ctx

    return await run_stages(
        RunContext(
            path=path,
            sample=await fetch_sample(sample_item_id),
            config=config,
            run_id=fold_run_id(sample_item_id),
            keep=keep,
            given=annotations,
        )
    )


def reads_annotation(row: dict, annotation: Annotation) -> bool:
    """Whether a judged row reads an annotated anchor's reading: its formula,
    through its mechanism, in its role."""
    return (
        formula_identity(row.get("assigned_formula"))
        == formula_identity(annotation.formula)
        and str(row.get("ionization_mechanism_id"))
        == str(annotation.ionization_mechanism_id)
        and row.get("role") == annotation.role
    )


async def _write_measured_members(
    sample_batch_id: str,
    sample_item_id: str,
    annotations: dict[str, Annotation],
    rows: list[dict],
    conditions: list,
    *,
    source: str,
    skip_candidate: Optional[dict[str, int]],
    displaced: Optional[list],
    prefer_better: bool,
    mark: Optional[str] = None,
) -> int:
    """Write the propagation's judged rows onto the sample's members, under the
    batch's fold lock: each member whose peak the judged ledger reads as its
    anchor's reading takes it, at the judged tier
    (:func:`_propagate_to_sample` says which members are written), and the
    batch's mark of the stage that wrote it (``mark``; none for a person's
    or an import's reading, which takes the batch's mark off).

    :return: How many members were assigned.
    """
    by_peak = {str(row["sample_peak_id"]): row for row in rows}
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
            annotation = annotations.get(member.batch_peak_id)
            if annotation is None:
                continue
            if (
                skip_candidate
                and skip_candidate.get(member.batch_peak_id) == member.candidate
            ):
                continue
            row = by_peak.get(str(member.sample_peak_id))
            if row is None or not reads_annotation(row, annotation):
                continue
            anchor = anchors[member.batch_peak_id]
            if prefer_better and not _takes_the_member(
                member,
                anchor,
                annotation,
                evidence_for(row.get("fit_score"), annotation.formula) or 0.0,
            ):
                continue
            if displaced is not None:
                displaced.append(measured_state(member, anchor))
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
            mark_member(member, mark)
            member.candidate = index
            member.tier = tier_code(row["tier"])
            member.fit_score = row.get("fit_score")
            member.role = role_code(annotation.role)
            member.owner_batch_peak_id = annotation.owner_batch_peak_id
            member.p_correct = None
            assigned += 1
        await touch_ledger(session, sample_batch_id)
        await session.commit()
    return assigned


def _takes_the_member(
    member: Any, anchor: Any, annotation: Annotation, evidence: float
) -> bool:
    """Whether a reading measured at ``evidence`` takes a member that may
    already read its peak (``_propagate_to_sample``'s ``prefer_better``).

    :param member: The member, as loaded.
    :param anchor: Its anchor, for the registry its index names.
    :param annotation: The reading being measured.
    :param evidence: Its evidence on this member.
    :return: True where the member has no reading, or a weaker one of another
        formula; False for a claimed member, an isotope line, the same formula,
        or a reading at least as well measured.
    """
    if member.role in CLAIMED_ROLE_CODES or member.role == role_code(ROLE_ISO_CHILD):
        return False
    if member.candidate is None:
        return True
    current = resolve_candidate(anchor.candidates, member.candidate)
    if current.get("formula") == annotation.formula:
        return False
    held = evidence_for(member.fit_score, current.get("formula"))
    return held is None or evidence >= held


def _new_counts(
    n_searched: int,
    n_searched_before: int,
    again: dict[str, "Annotation"],
    splits: dict[tuple, Optional[float]],
) -> dict:
    """The counts a search reports (:func:`run_batch_untargeted_search`),
    before it searches."""
    return {
        "anchors_searched": n_searched,
        # Unassigned batch peaks the last search searched and found nothing
        # for, left out because nothing that search read of them has changed.
        "anchors_searched_before": n_searched_before,
        # Batch peaks the search assigned before whose reading is measured
        # again on the members that read nothing yet.
        "anchors_measured_again": sum(
            1 for annotation in again.values() if annotation.role == ROLE_M0
        ),
        # Anchors the cap kept out of the search. Zero unless a caller set
        # `max_untargeted_peaks` or a sample carries more representatives than
        # the ceiling; reported because a blank anchor means something different
        # when nothing looked at it.
        "anchors_unsearched": 0,
        # Anchors searched because their peak is persistent, which the floor or
        # the cap would have left out, and how many of their readings the
        # persistence rule holds at candidate after the pass.
        "anchors_admitted_by_persistence": 0,
        "anchors_held_by_persistence": 0,
        # Each partition's split of its occurrence distribution, None where it
        # has none (too few spectra, or no two shares apart).
        "persistence_splits": [
            {
                "ionization_mode_id": mode,
                "intensity_variable": unit,
                "split": None if split is None else round(split, 4),
            }
            for (mode, unit), split in sorted(
                splits.items(), key=lambda item: (str(item[0][0]), str(item[0][1]))
            )
        ],
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
    batch_peak_run_id: Optional[str] = None,
    stop: Optional[Callable[[], Awaitable[None]]] = None,
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

    An anchor the last search found nothing for is left out while nothing
    that search read of it has changed (:func:`searched_in_vain`), never
    under a cap; a sample left with no representative is not searched.

    Before any search the batch's occurrence table is read
    (:func:`_occurrence_table`): which anchors are persistent, so each
    sample's search takes their representatives however faint. An anchor
    persistence alone admitted is marked once the samples' rows are merged,
    before anything else is written, so the consensus pass at the end - also
    one that runs after a stop - holds the reading the search elected there.

    :param batch_peak_run_id: The batch run the search writes under, named on
        the persistence records.
    :return: Counts: anchors searched, anchors annotated, members assigned by
        propagation, samples searched, searched again and re-scored, the
        anchors that took an isotopologue reading from another sample, the
        anchors persistence alone admitted and those the rule holds at
        candidate, and each partition's split.
    """
    async with async_session() as session:
        anchors, members = await _unassigned_anchors_and_members(
            session, sample_batch_id
        )
        persistent, splits = await _occurrence_table(session, sample_batch_id)
        again, again_samples = await _readings_to_measure_again(
            session, sample_batch_id
        )
        key = (
            search_key(
                config,
                await library_read(session, sample_batch_id),
                await chemistry_read(session, sample_batch_id),
            )
            if anchors and config.max_untargeted_peaks is None
            else None
        )
    representatives = choose_representatives(members)
    # The anchors the last search found nothing for, which this one would
    # read as that one did: left out.
    searched_before = {
        anchor_id: member
        for anchor_id, member in representatives.items()
        if searched_in_vain(
            # .get: the anchors and the members are two reads, and a fold
            # that commits between them can give a member of an anchor the
            # first did not return. No record reads as one to search.
            searched_empty_record_of(anchors.get(anchor_id)),
            key,
            member,
            anchor_id in persistent,
        )
    }
    counts = _new_counts(
        len(anchors) - len(searched_before), len(searched_before), again, splits
    )
    if not anchors and not again:
        return counts

    by_sample = group_by_sample(
        {
            anchor_id: member
            for anchor_id, member in representatives.items()
            if anchor_id not in searched_before
        }
    )
    # The representatives of persistent anchors, per sample: the peaks its
    # search takes however faint.
    persistent_peaks = {
        sample_item_id: frozenset(
            member.sample_peak_id
            for member in sample_representatives
            if member.batch_peak_id in persistent
        )
        for sample_item_id, sample_representatives in by_sample.items()
    }
    members_by_sample: dict[str, list] = defaultdict(list)
    for member in members:
        members_by_sample[member.sample_item_id].append(member)
    for sample_item_id in sorted(again_samples - set(members_by_sample)):
        members_by_sample[sample_item_id] = []

    reports = process_id is not None
    total = len(by_sample) + len(members_by_sample)
    step = 0

    async def report(message: str) -> None:
        # Before each sample: where a person asked the pass to stop, the stop
        # raises here, between two samples, and the finally below still
        # writes the consensus of what the samples before it committed.
        if stop is not None:
            await stop()
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
    # Per sample, the representatives its latest search took on persistence
    # alone.
    admitted_by_sample: dict[str, set[str]] = {}
    marked: set[str] = set()
    withheld: dict[str, set[str]] = defaultdict(set)
    # Samples whose search again failed: their first search stands, and
    # vouches for nothing it found nothing for.
    research_failed: set[str] = set()
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
                    admitted_by_sample[sample_item_id],
                ) = await _search_sample(
                    sample_item_id,
                    {member.sample_peak_id for member in sample_representatives},
                    config,
                    persistent_peaks[sample_item_id],
                )
                counts["samples_searched"] += 1
            except Exception:  # noqa: BLE001 - one bad sample must not abort the search
                # A sample reaches here for reasons that are about that sample
                # alone - a peak file that has moved, a spectrum that will not
                # read - and the other samples' anchors are still searchable.
                # Logged with the traceback: the caller only ever sees a count.
                count_unread(counts, sample_item_id)
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
                        admitted_by_sample[sample_item_id],
                    ) = await _search_sample(
                        sample_item_id,
                        {member.sample_peak_id for member in by_sample[sample_item_id]}
                        - withheld[sample_item_id],
                        config,
                        persistent_peaks[sample_item_id],
                    )
                    counts["samples_researched"] += 1
                except Exception:  # noqa: BLE001 - the first search's rows stand
                    research_failed.add(sample_item_id)
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
        # Marked before anything else is written: the consensus pass below
        # reads the record, also after a stop, and a member written without
        # it would read as a reading the search elected on its height.
        marked, counts["anchors_admitted_by_persistence"] = await write_search_records(
            sample_batch_id,
            key=key,
            persistent=persistent,
            searched_before={a: anchors[a] for a in searched_before},
            by_sample={s: by_sample[s] for s in rows_by_sample},
            read=set(search_readings(rows_by_sample, members)),
            annotations=merge.annotations,
            admitted_by_sample=admitted_by_sample,
            vouched=lambda sample_item_id: (
                not unsearched_by_sample.get(sample_item_id)
                and sample_item_id not in research_failed
            ),
            batch_peak_run_id=batch_peak_run_id,
        )
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
                count_unread(counts, sample_item_id)
                runtime.logger.exception(
                    "Writing the untargeted batch search failed for sample "
                    f"'{sample_item_id}'."
                )
        counts["anchors_annotated"] = len(merge.annotations)
        # The readings found now, and those the search found before that a
        # member folded in since does not carry yet.
        annotations = {**again, **merge.annotations}

        if annotations:
            for sample_item_id in members_by_sample:
                await report(
                    f"Measuring the found compositions, sample {step + 1}/{total}."
                )
                try:
                    counts["members_propagated"] += await _propagate_to_sample(
                        sample_batch_id,
                        sample_item_id,
                        annotations,
                        config,
                        mark=MARK_SEARCH,
                    )
                    counts["samples_rescored"] += 1
                except Exception:  # noqa: BLE001 - one bad sample must not abort the pass
                    count_unread(counts, sample_item_id)
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
            await recompute_batch_consensus(sample_batch_id, set(anchors) | set(again))
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
    counts["anchors_held_by_persistence"] = await _held_by_persistence(
        sample_batch_id, marked
    )
    return counts


def search_outcome(counts: dict, sample_batch_id: str) -> dict:
    """The controller result the background-task decorator reports from."""
    notification_data = {"sample_batch_id": sample_batch_id}
    before = counts.get("anchors_searched_before", 0)
    if (
        not counts["anchors_searched"]
        and not counts.get("anchors_measured_again")
        and not before
    ):
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
    # Said because these batch peaks were not searched: their reading stood,
    # and only the members folded in since were measured against it.
    measured_again = counts.get("anchors_measured_again", 0)
    again = (
        f" {measured_again} batch peak{'s' if measured_again != 1 else ''} the "
        "search assigned before "
        f"{'were' if measured_again != 1 else 'was'} measured again on the "
        "members folded in since."
        if measured_again
        else ""
    )
    searched_before = searched_before_note(before)
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
    # Said because the floor and the cap the user set did not decide these,
    # and a reading held at candidate on them needs its reason.
    admitted = counts.get("anchors_admitted_by_persistence", 0)
    held = counts.get("anchors_held_by_persistence", 0)
    if not held:
        holding = ""
    elif admitted == 1:
        holding = "; its reading is held at candidate"
    else:
        holding = f"; {held} of them {'are' if held != 1 else 'is'} held at candidate"
    persistence = (
        f" {admitted} batch peak{'s' if admitted != 1 else ''} under the "
        f"intensity floor or past the peak cap {'were' if admitted != 1 else 'was'} "
        f"searched because the peak recurs across the batch{holding}"
        + (
            " until an isotope line, another channel or a series of the batch "
            "corroborates the reading."
            if held
            else "."
        )
        if admitted
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
            f"measured against it.{searched_before}{again}{lines}{persistence}"
            f"{skipped}"
            f"{unsearched}"
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
    batch_peak_run_id: str | None = None,
    stop: Optional[Callable[[], Awaitable[None]]] = None,
) -> dict:
    """Run the untargeted composition search once per unassigned anchor of a
    batch, on each anchor's brightest member, and measure the result against
    the anchor's other members. Writes no per-sample run; emits
    ``peak_assignment_reload`` so the ledgers refresh.

    :param batch_peak_run_id: The run of the pass of the batch stages it runs
        in, which it writes into instead of opening a run of its own.
    :param stop: Awaited before each sample: the pass's cancel check, which
        raises where a person asked the pass to stop.
    """
    search_config = config or PeakAssignmentConfig()
    settings = search_config.model_dump(mode="json")
    # A batch run: the search's parameters go on the record, the current
    # run's state is snapshotted before the first anchor is touched, and the
    # run becomes current on completion - or is marked failed, the live
    # ledger then holding whatever the search wrote before it stopped.
    counts = await in_batch_run(
        sample_batch_id,
        ACTION_SEARCH,
        lambda run_id: run_batch_untargeted_search(
            sample_batch_id,
            search_config,
            user_id=user_id,
            process_id=process_id,
            parent_id=parent_id,
            batch_peak_run_id=run_id,
            stop=stop,
        ),
        batch_peak_run_id=batch_peak_run_id,
        config=settings,
        user_id=user_id,
    )
    return {**search_outcome(counts, sample_batch_id), "config": settings}
