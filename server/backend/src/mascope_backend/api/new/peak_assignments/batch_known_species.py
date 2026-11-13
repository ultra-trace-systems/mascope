"""Known species decided once for the batch: step 4.2's seventh rider (4.2h).

Each file's run decides on its own whether a known species is there - an ion
of the source's own library, or a compound a reference list tags as
background (the cyclic siloxanes first) - so a species present across a batch
is claimed in some files and read as an analyte, or left unread, in the
others. "Decide known species" pools each species' evidence over the files of
its partition and decides it once (``mascope_tools.composition.known_species``):

1. **The species.** A batch peak, monoisotopic and no other's isotope line,
   is a library species where a sample's pre-pass claimed it as an ion of the
   library (a reagent row with no formula and no owner, of any family but a
   fragment, which only its parent's commit in the same file licenses), and a
   listed species where its registry holds a formula a list tags
   ``background``. A file's own test held where its run claimed the ion, or
   read the listed formula at candidate or better - on its own, not on the
   batch's word: a member whose row a decision rewrote, or a run claimed on
   the batch's decision, is the batch's, and so is one with no row to say
   so that the decision before read as the batch's (the record keeps the
   samples it read as their own). The evidence never feeds on its claims.
2. **The verdict.** Present where the species' peak is in two files or more,
   in half the partition's files or more, and one file's own test held;
   absent where it is in enough files and no file's test held; unpooled where
   it is in too few, each file then keeping its own reading.
3. **The claim.** Where a species is present, its member in every file is
   rewritten as the species - a library ion's as the pre-pass writes its claim
   (``reagent_pass.batch_species_row``), the ion's isotope lines with it; a
   listed compound's as the list's reading, at candidate: the file's own lines
   did not commit it, the batch's files say it is there - and so is the
   sample's own in-app row on that peak. A file is kept as it is where a
   person assigned the peak there, where the workspace's target library named
   it, where the peak is another ion's isotope line or another source ion's
   claim, where the file's reading carries isotope lines of its own - for a
   listed compound any, for a library ion one it cannot have (a 13C line
   under a carbon-free ion). An imported run's rows are another engine's
   record and are never rewritten. A curated batch peak is never claimed, and
   neither is one whose files the claim may take would not turn its vote.
   Where the fold split a species' trace over two batch peaks, the one in
   the most files claims it, and keeps a file that reads it on the other.
4. **The record.** Every species read carries ``provenance.known_species``
   (``batch_peaks.KNOWN_SPECIES_KEY``): the species, the verdict and its
   evidence, the co-variation of its trace with its core (display-only, as
   the co-variation reading is, decision 35), what the claim wrote, the files
   it kept with each one's reading, and the files whose own claim stands
   against a verdict that is not present - a file's reading is recorded, never
   silently overridden. Each rewritten member's earlier state is kept in it,
   and each rewritten row says what it read. The run's summary counts per
   partition and lists the species.

A sample's next run takes the batch's word for a library ion
(``reagent_pass.claim_batch_species``, :func:`batch_species_for`): the file
claims it where its own claim did not. A listed compound is the stages' to
read, so a sample assigned again reads it afresh, and a later decision reads
its rows again.

The reading runs outside the fold lock (and off the event loop) and the
writes under it, a chunk of species per transaction, each species' files
read again as the ledger stands then.

Design: ``docs/dev/assignment_quality_plan.md``, step 4.2, the seventh rider.
"""

from __future__ import annotations

import asyncio
import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Optional, Sequence

from sqlalchemy import select

from mascope_backend.api.lib.api_features import api_controller_background_task
from mascope_backend.api.new.ionization.modes.versions import root_ids
from mascope_backend.api.new.peak_assignments.batch_peaks import (
    KNOWN_SPECIES_KEY,
    ROLE_ISO_CHILD,
    ROLE_M0,
    ROLE_REAGENT,
    SOURCE_ROLES,
    candidate_index,
    instrument_type_of,
    known_species_record_of,
    manual_pin_of,
    member_state,
    mz_from_delta,
    reading_of,
    resolve_candidate,
    role_code,
    role_name,
    standing_co_variation_record,
    standing_known_species_record,
    tier_code,
    tier_name,
    vote_consensus,
)
from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
    _acquire_batch_fold_lock,
    _intensity_variable,
    _recompute_consensus,
)
from mascope_backend.api.new.peak_assignments.batch_runs import (
    ACTION_KNOWN_SPECIES,
    complete_run,
    fail_run,
    start_run,
    touch_ledger,
)
from mascope_backend.api.new.peak_assignments.config import (
    IN_APP_ENGINE,
    MAX_ALTERNATIVES_CEILING,
)
from mascope_backend.api.new.peak_assignments.engine import (
    DISPLACED_BY_CLAIM,
    REFERENCE_IDENTITIES_COL,
    SOURCE_MANUAL,
    SOURCE_REAGENT,
    reading_as_alternative,
)
from mascope_backend.api.new.peak_assignments.reagent_pass import (
    REASON_BATCH_KNOWN_SPECIES,
    BatchSpecies,
    batch_species_from,
    batch_species_row,
)
from mascope_backend.api.new.peak_assignments.tiers import (
    TIER_ASSIGNED,
    TIER_CANDIDATE,
    TIER_UNASSIGNED,
)
from mascope_backend.db import (
    BatchPeak,
    BatchPeakOccurrence,
    PeakAssignment,
    PeakAssignmentRun,
    async_session,
)
from mascope_tools.composition.coherence import batch_residuals
from mascope_tools.composition.known_species import (
    KIND_LIBRARY,
    KIND_LISTED,
    PooledEvidence,
    SpeciesVerdict,
    decide_known_species,
    thresholds,
)
from mascope_tools.composition.reagents import (
    FAMILY_CALIBRANT,
    FAMILY_FRAGMENT,
    FAMILY_REAGENT,
    FRAGMENT_LADDERS,
    KIND_CLUSTER,
    REAGENT_CLUSTERS,
    ReagentCluster,
    ion_mz,
)


#: The tag a reference list marks a compound the batch may decide as
#: background with (``listing.reference_listing``'s ``tags``).
BACKGROUND_TAG = "background"

#: The tiers at which a file's reading of a listed compound is its own test
#: holding: a reading that commits the formula.
_COMMITTED = (TIER_ASSIGNED, TIER_CANDIDATE)

#: An isotope a line's label names, with how many atoms of it: ``13C``,
#: ``13C2``, ``2x81Br`` (two), ``81Br(pair)`` (one); ``+`` joins them. And an
#: element of a formula, with its count.
_ISOTOPE = re.compile(r"(?:(\d+)x)?\d+([A-Z][a-z]?)(\d*)(?:\([^()]*\))?")
_ELEMENT = re.compile(r"([A-Z][a-z]?)(\d*)")

#: The tier a listed compound's claim writes: the file's own lines did not
#: commit it, and the batch's files say it is there.
LISTED_CLAIM_TIER = TIER_CANDIDATE

#: A sample that already reads the species: on its file's own test, or on
#: the batch's word (:meth:`Species.own_test` tells them apart).
ALREADY = "already"

#: Why a sample of a present species is kept as it is.
KEPT_HAND_ASSIGNED = "hand_assigned"
KEPT_TARGET_LIBRARY = "target_library"
KEPT_LINE_OF_ANOTHER = "line_of_another"
KEPT_CLAIMED_OTHERWISE = "claimed_otherwise"
KEPT_CARRIES_LINES = "carries_lines"
#: Kept: the sample reads the species on a twin, another batch peak of the
#: species' trace (:func:`settle_twins`).
KEPT_ON_TWIN = "reads_on_twin"

#: A file whose own claim stands against a verdict that is not present.
OWN_CLAIM = "own_claim"
#: A file that reads the species on an earlier decision's word, against a
#: verdict that is not present now.
BATCH_CLAIM = "batch_claim"

#: Why a species decided present was not claimed: its batch peak is pinned
#: by hand; the files the claim may take would not turn its vote; its batch
#: peak's readings moved between the reading and the write; another batch
#: peak of its trace claims it (:func:`settle_twins`).
HELD_CURATED = "curated"
HELD_VOTE = "vote_holds"
HELD_MOVED = "moved"
HELD_TWIN = "twin"

#: The species a run's summary lists at most; the counts are whole, and every
#: species carries its record on the ledger.
MAX_LISTED = 500

#: The files a record lists with their readings at most; its counts are whole.
MAX_RECORDED_FILES = 50

#: Species written per transaction under the fold lock.
WRITE_CHUNK = 50

#: Rows read per statement (asyncpg refuses more than 32767 bind parameters).
ROW_READ_CHUNK = 5000

#: Why a rewritten member's sample row was left as it was: no row is linked
#: (a sample folded without a run, or its run pruned), or the run is an
#: import, another engine's record.
ROW_KEPT_NO_ROW = "no_row"
ROW_KEPT_IMPORTED = "imported"


# --- what is read -------------------------------------------------------------


#: A partition of the batch's anchors: their ionization mode and the
#: instrument type their unit names, the files whose peaks fold together.
#: (Defined here rather than taken from ``batch_isotopologues``, which the
#: run's service imports, and this module is imported by it.)
Partition = tuple[Optional[str], Optional[str]]


def partition_of(anchor: Any) -> Partition:
    """The partition an anchor is read in."""
    return (anchor.ionization_mode_id, instrument_type_of(anchor.intensity_variable))


def trace_centre(anchor_mz: float, members: Iterable[Any]) -> float:
    """The m/z an anchor's members sit at: the median of theirs, the anchor's
    own where it has none."""
    mzs = [member.mz for member in members]
    return statistics.median(mzs) if mzs else anchor_mz


@dataclass(frozen=True)
class Member:
    """What the decision reads of one member.

    :ivar formula: The neutral formula its registry entry names, or None.
    :ivar hand: Whether the sample's row on the peak is a hand assignment.
    :ivar target: Whether the sample's row was matched to a target compound.
    :ivar isotope_label: The isotope its sample's row names it as (``13C``,
        ``13C+81Br``), for an isotope line; None where no row is linked.
    :ivar row_reading: What the linked row reads, (role, formula, ion, tier);
        None where no row is linked.
    :ivar row_batch: Whether the linked row was written on the batch's word:
        rewritten by a decision, or claimed by a run on the batch's decision.
    """

    sample_item_id: str
    sample_peak_id: str
    intensity: Optional[float]
    mz: float
    role: Optional[str] = None
    owner_batch_peak_id: Optional[str] = None
    formula: Optional[str] = None
    ion_formula: Optional[str] = None
    ionization_mechanism_id: Optional[str] = None
    tier: Optional[str] = None
    fit_score: Optional[float] = None
    peak_assignment_id: Optional[str] = None
    hand: bool = False
    target: bool = False
    isotope_label: Optional[str] = None
    row_reading: Optional[tuple[Optional[str], ...]] = None
    row_batch: bool = False

    @property
    def source_claim(self) -> bool:
        """Whether a pre-pass claimed the peak as a source's monoisotopic ion."""
        return (
            self.role == ROLE_REAGENT
            and not self.formula
            and not self.owner_batch_peak_id
        )


@dataclass(frozen=True)
class Species:
    """A known species, as the batch reads it.

    :ivar kind: :data:`KIND_LIBRARY` or :data:`KIND_LISTED`.
    :ivar ion_formula: The ion, with its charge.
    :ivar label: A library ion's label; None for a listed compound.
    :ivar exact_mz: A library ion's m/z, as its claims recorded it.
    :ivar grew_from: A ladder rung's root ion, by label.
    :ivar formula: A listed compound's neutral formula.
    :ivar listing: What the list calls it.
    """

    kind: str
    ion_formula: Optional[str]
    label: Optional[str] = None
    family: Optional[str] = None
    ion_kind: Optional[str] = None
    references: tuple[str, ...] = ()
    observed: Optional[str] = None
    exact_mz: Optional[float] = None
    grew_from: Optional[str] = None
    formula: Optional[str] = None
    ionization_mechanism_id: Optional[str] = None
    listing: Optional[dict] = None

    def to_json(self) -> dict:
        if self.kind == KIND_LIBRARY:
            return {
                "kind": self.kind,
                "ion_formula": self.ion_formula,
                "label": self.label,
                "family": self.family,
                "ion_kind": self.ion_kind,
                "references": list(self.references),
                "observed": self.observed,
                "mz": None if self.exact_mz is None else round(self.exact_mz, 6),
                "grew_from": self.grew_from,
            }
        return {
            "kind": self.kind,
            "formula": self.formula,
            "ion_formula": self.ion_formula,
            "ionization_mechanism_id": self.ionization_mechanism_id,
            "listing": self.listing,
        }

    def reads(self, member: Member) -> bool:
        """Whether a member reads the peak as this species: claimed as the
        ion, or the listed formula at candidate or better - on its file's own
        test or on the batch's word (:meth:`own_test` tells them apart)."""
        if self.kind == KIND_LIBRARY:
            return member.source_claim and member.ion_formula == self.ion_formula
        return self._reading_reads(
            member.role, member.formula, member.ion_formula, member.tier
        )

    def _reading_reads(
        self,
        role: Optional[str],
        formula: Optional[str],
        ion_formula: Optional[str],
        tier: Optional[str],
    ) -> bool:
        if self.kind == KIND_LIBRARY:
            return (
                role == ROLE_REAGENT and not formula and ion_formula == self.ion_formula
            )
        return role == ROLE_M0 and formula == self.formula and tier in _COMMITTED

    def own_test(self, member: Member, earlier: Optional[set[str]]) -> bool:
        """Whether a member reads the species on its file's own test, not on
        the batch's word.

        A member whose row was written on the batch's word (a decision's
        rewrite, or a run's claim on the batch's decision) is the batch's.
        One whose row reads the species is its file's. One with no row that
        says so - none linked, or an import's the decision did not rewrite -
        is its file's only where the batch never decided the species, or
        where the decision before this one read it as its file's own: a
        decision's claim in such a member leaves no other trace.

        :param earlier: The samples the anchor's earlier decision of this
            species read as their file's own; None where there is none.
        """
        if not self.reads(member) or member.row_batch:
            return False
        if member.row_reading is not None and self._reading_reads(*member.row_reading):
            return True
        return earlier is None or member.sample_item_id in earlier

    def can_have(self, isotope_label: Optional[str]) -> bool:
        """Whether a library ion can have an isotope line of this label: every
        isotope it names is of an element of the ion, in no more atoms than
        the ion holds (a ``13C2`` line needs two carbons). A line of no label,
        or of a label read as no isotopes, is not one it can be shown to have.
        """
        parts = (isotope_label or "").split("+")
        wanted: Counter = Counter()
        for part in parts:
            found = _ISOTOPE.fullmatch(part)
            if found is None:
                return False
            times, element, count = found.groups()
            wanted[element] += int(times or 1) * int(count or 1)
        held: Counter = Counter()
        for element, count in _ELEMENT.findall((self.ion_formula or "").rstrip("+-")):
            held[element] += int(count or 1)
        return all(held[element] >= n for element, n in wanted.items())

    def lifts(self, member: Member) -> bool:
        """Whether a member reads a listed compound's own formula below the
        tier its file's own test needs: the claim lifts it to candidate and
        keeps what it measured, its isotope lines included."""
        return (
            self.kind == KIND_LISTED
            and member.role == ROLE_M0
            and member.formula == self.formula
            and not self.reads(member)
        )

    def reading(self) -> dict:
        """The reading an anchor holding this species votes to."""
        if self.kind == KIND_LIBRARY:
            return {
                "role": ROLE_REAGENT,
                "formula": None,
                "ion_formula": self.ion_formula,
                "ionization_mechanism_id": None,
            }
        return {
            "role": None,
            "formula": self.formula,
            "ion_formula": self.ion_formula,
            "ionization_mechanism_id": self.ionization_mechanism_id,
        }

    def as_batch_species(self, record: dict) -> BatchSpecies:
        """The species as the reagent pass's claim takes it (library ions)."""
        composition = (self.ion_formula or "").rstrip("+-")
        signs = (self.ion_formula or "")[len(composition) :]
        return BatchSpecies(
            ReagentCluster(
                composition,
                signs.count("+") - signs.count("-"),
                self.label or self.ion_formula or "",
                self.ion_kind or KIND_CLUSTER,
                family=self.family or FAMILY_REAGENT,
                references=self.references,
                observed=self.observed or "",
            ),
            record,
        )


#: (sample, batch peak) -> the isotope labels of the members of the sample
#: that name the batch peak as the owner of their line.
Carrying = dict[tuple[str, str], tuple[Optional[str], ...]]


def carrying_of(
    lines: Iterable[tuple[str, Optional[str], Optional[str]]],
) -> Carrying:
    """:data:`Carrying` from (sample, owner batch peak, isotope label) per
    member; a member naming no owner is no line."""
    found: dict[tuple[str, str], list[Optional[str]]] = defaultdict(list)
    for sample, owner, label in lines:
        if owner:
            found[(sample, owner)].append(label)
    return {key: tuple(labels) for key, labels in found.items()}


@dataclass
class Row:
    """One batch peak and its members, sample -> member."""

    anchor: Any
    members: dict[str, Member] = field(default_factory=dict)

    @property
    def batch_peak_id(self) -> str:
        return self.anchor.batch_peak_id


@dataclass
class Decision:
    """One species, decided.

    :ivar samples: Sample -> None where the claim rewrites it, :data:`ALREADY`
        where it already reads the species (on its file's own test or on the
        batch's word: ``own`` says which), else why it is kept; filled for a
        present species only.
    :ivar held: Why a present species was not claimed; None where it was or
        where it is not present.
    :ivar own: The samples whose file's own test held (:func:`own_tests`), as
        the members stood when it was decided.
    :ivar twins: The other batch peaks of the species' trace in its
        partition, for the one that claims it (:func:`settle_twins`).
    :ivar twin_of: The batch peak that claims the species, for a twin held
        for it (:data:`HELD_TWIN`); ``twin_of_mz`` its trace's centre, which
        the ledger's mark names.
    """

    row: Row
    species: Species
    verdict: SpeciesVerdict
    samples: dict[str, Optional[str]] = field(default_factory=dict)
    own: list[str] = field(default_factory=list)
    held: Optional[str] = None
    co_variation: Optional[dict] = None
    twins: list[str] = field(default_factory=list)
    twin_of: Optional[str] = None
    twin_of_mz: Optional[float] = None

    @property
    def claims(self) -> bool:
        """Whether the decision writes the species into any member."""
        return (
            self.verdict.present
            and self.held is None
            and any(decision is None for decision in self.samples.values())
        )

    def to_json(self) -> dict:
        """The species as a run's summary lists it."""
        entry = {
            "batch_peak_id": self.row.batch_peak_id,
            "mz": round(trace_centre(self.row.anchor.mz, self.row.members.values()), 6),
            **self.species.to_json(),
            **self.verdict.to_json(),
            "claimed": sum(1 for d in self.samples.values() if d is None),
            "kept": sum(
                1 for d in self.samples.values() if d is not None and d != ALREADY
            ),
        }
        if self.held is not None:
            entry["held"] = self.held
        return entry


@dataclass
class Plan:
    """What the reading found, before anything is written."""

    decisions: list[Decision] = field(default_factory=list)
    counts: dict[Partition, dict] = field(default_factory=dict)


#: The counts a partition keeps, and the run's summary sums.
_COUNTS = (
    "species",
    "library",
    "listed",
    "present",
    "absent",
    "unpooled",
    "held",
    "claimed",
)


def _empty_counts() -> dict:
    return {key: 0 for key in _COUNTS}


# --- the reading (pure) ---------------------------------------------------------


def _signed(formula: str, charge: int) -> str:
    """An ion's formula written with its charge, as a row spells it."""
    return formula + ("+" * charge if charge > 0 else "-" * -charge)


#: Every fragment ion a profile's source makes of an analyte, by its formula
#: written with its charge. A sample folded without a run links no row whose
#: claim says it was a fragment's, so the ion says it instead: a fragment is
#: claimed only where its parent is committed in the same file, which a batch
#: cannot pool.
FRAGMENT_IONS = frozenset(
    _signed(fragment.formula, fragment.charge)
    for ladders in FRAGMENT_LADDERS.values()
    for ladder in ladders
    for fragment in ladder.fragments
)

#: Every ion of a profile's library, by its formula written with its charge,
#: the first profile naming it winning: what a claim names where no row of
#: the claim is left to read it off.
LIBRARY_IONS: dict[str, ReagentCluster] = {}
for _library in REAGENT_CLUSTERS.values():
    for _cluster in _library:
        LIBRARY_IONS.setdefault(_signed(_cluster.formula, _cluster.charge), _cluster)
del _library, _cluster


def _monoisotopic(anchor: Any) -> bool:
    """A batch peak of its own ion: no other's isotope line, and an ion that
    names no isotope (``[13C]C9H15+``)."""
    ion = anchor.consensus_ion_formula
    return not anchor.isotopologue_of and (not ion or "[" not in ion)


def _order(value: Any) -> Any:
    """A value's place in a tie-break: a tuple's parts in order, a None among
    them before any text (an imported row may name no mechanism)."""
    if isinstance(value, tuple):
        return tuple((part is not None, str(part or "")) for part in value)
    return (True, str(value))


def _mode(values: Iterable[Any]) -> Any:
    """The value most often given, the first in :func:`_order` on a tie."""
    counts = Counter(value for value in values if value is not None)
    if not counts:
        return None
    best = max(counts.values())
    return min((value for value, n in counts.items() if n == best), key=_order)


def library_species(row: Row, claims: dict[str, dict]) -> Optional[Species]:
    """The library ion a batch peak is, where a sample's pre-pass claimed it.

    The ion is the one most of its claims name. What the claim recorded - its
    label, family, kind, works and exact mass - is read off the sample rows the
    claims made; where no such row is left (a sample folded without a run, or
    its run pruned), off the profiles' libraries by the ion. A fragment is no
    species: it is claimed only where its parent is committed in the same file.

    :param claims: Peak assignment id -> the ``provenance.reagent`` record of
        the sample row a member links, for the members a pre-pass claimed.
    :return: The ion, or None where no member is a pre-pass's claim, or the
        claims are a fragment's.
    """
    claimed = [
        member
        for member in row.members.values()
        if member.source_claim and member.ion_formula
    ]
    if not claimed:
        return None
    ion = _mode(member.ion_formula for member in claimed)
    if ion in FRAGMENT_IONS:
        return None
    records = [
        claims[member.peak_assignment_id]
        for member in claimed
        if member.ion_formula == ion and member.peak_assignment_id in claims
    ]
    if any(record.get("family") == FAMILY_FRAGMENT for record in records):
        return None
    if records:
        label = _mode(record.get("ion") for record in records)
        record = next(r for r in records if r.get("ion") == label)
        ladder = record.get("ladder") if isinstance(record.get("ladder"), dict) else {}
        grew_from = ladder.get("grew_from")
        mz = record.get("mz")
        return Species(
            kind=KIND_LIBRARY,
            ion_formula=ion,
            label=label,
            family=record.get("family"),
            ion_kind=record.get("kind"),
            references=tuple(record.get("references") or ()),
            observed=record.get("observed") or None,
            exact_mz=float(mz) if isinstance(mz, (int, float)) else None,
            grew_from=grew_from.get("ion") if isinstance(grew_from, dict) else None,
        )
    cluster = LIBRARY_IONS.get(ion)
    composition = ion.rstrip("+-")
    signs = ion[len(composition) :]
    return Species(
        kind=KIND_LIBRARY,
        ion_formula=ion,
        label=cluster.label if cluster else ion,
        family=cluster.family if cluster else None,
        ion_kind=cluster.kind if cluster else None,
        references=tuple(cluster.references) if cluster else (),
        observed=(cluster.observed or None) if cluster else None,
        exact_mz=(
            cluster.mz
            if cluster
            else ion_mz(composition, signs.count("+") - signs.count("-"))
        ),
    )


def listed_species(row: Row) -> Optional[Species]:
    """The listed compound a batch peak is, where its registry holds a formula
    a list tags as background.

    The species is the formula most of its files' own readings commit, read
    through the ion and mechanism most of them name; with none committing it,
    the formula most of its members name.
    """
    registry = [
        entry for entry in (row.anchor.candidates or []) if isinstance(entry, dict)
    ]
    listed = {
        entry.get("formula")
        for entry in registry
        if entry.get("formula")
        and BACKGROUND_TAG in ((entry.get("listing") or {}).get("tags") or [])
    }
    if not listed:
        return None
    members = [
        m for m in row.members.values() if m.formula in listed and m.role == ROLE_M0
    ]
    if not members:
        return None
    held = [m for m in members if m.tier in _COMMITTED] or members
    formula = _mode(m.formula for m in held)
    held = [m for m in held if m.formula == formula]
    reading = _mode((m.ion_formula, m.ionization_mechanism_id) for m in held)
    listing = next(
        (
            entry.get("listing")
            for entry in registry
            if entry.get("formula") == formula
            and isinstance(entry.get("listing"), dict)
        ),
        None,
    )
    return Species(
        kind=KIND_LISTED,
        ion_formula=reading[0],
        formula=formula,
        ionization_mechanism_id=reading[1],
        listing=listing,
    )


def species_of(row: Row, claims: dict[str, dict]) -> Optional[Species]:
    """The known species a batch peak is, a library ion before a listed
    compound; None for any other batch peak."""
    if not _monoisotopic(row.anchor):
        return None
    return library_species(row, claims) or listed_species(row)


def sample_decision(
    member: Member, species: Species, lines: Sequence[Optional[str]] = ()
) -> Optional[str]:
    """What becomes of a present species' member in one file: None where the
    claim rewrites it, :data:`ALREADY` where it already reads the species,
    else why it is kept.

    :param lines: The isotope labels of the members of the file that name the
        batch peak as the owner of their line (None for one no row labels). A
        listed compound's claim keeps a member with lines, which are another
        formula's, unless it reads the compound's own formula
        (:meth:`Species.lifts`). A library ion's claim takes the lines with
        the ion, so it keeps a member with a line the ion cannot have
        (:meth:`Species.can_have`): a 13C line under a carbon-free ion.
    """
    if species.reads(member):
        return ALREADY
    if member.hand:
        return KEPT_HAND_ASSIGNED
    if member.target:
        return KEPT_TARGET_LIBRARY
    if member.role == ROLE_ISO_CHILD or member.owner_batch_peak_id:
        return KEPT_LINE_OF_ANOTHER
    if member.role in SOURCE_ROLES:
        return KEPT_CLAIMED_OTHERWISE
    if lines and species.kind == KIND_LISTED and not species.lifts(member):
        return KEPT_CARRIES_LINES
    if (
        lines
        and species.kind == KIND_LIBRARY
        and not all(species.can_have(label) for label in lines)
    ):
        return KEPT_CARRIES_LINES
    return None


def _claimed_member(member: Member, species: Species) -> dict:
    """A member as the claim leaves it, as the consensus votes on it."""
    if species.lifts(member):
        return {**_as_voted(member), "tier": LISTED_CLAIM_TIER}
    reading = species.reading()
    return {
        "assigned_formula": reading["formula"],
        "ion_formula": reading["ion_formula"],
        "ionization_mechanism_id": reading["ionization_mechanism_id"],
        "tier": TIER_UNASSIGNED if species.kind == KIND_LIBRARY else LISTED_CLAIM_TIER,
        "fit_score": None,
        "intensity": member.intensity,
        "role": ROLE_REAGENT if species.kind == KIND_LIBRARY else ROLE_M0,
        "owner_batch_peak_id": None,
    }


def _as_voted(member: Member) -> dict:
    return {
        "assigned_formula": member.formula,
        "ion_formula": member.ion_formula,
        "ionization_mechanism_id": member.ionization_mechanism_id,
        "tier": member.tier,
        "fit_score": member.fit_score,
        "intensity": member.intensity,
        "role": member.role,
        "owner_batch_peak_id": member.owner_batch_peak_id,
    }


def claim_turns(row: Row, species: Species, samples: dict[str, Optional[str]]) -> bool:
    """Whether the claim makes the species the batch peak's reading: the
    consensus over its members as the claim would leave them reads the
    species' role, formula and ion.

    A claimed member carries no fit, so it votes at the floor weight the
    consensus gives a member without one, but for a member the claim only
    lifts, which keeps its own; every other member votes as it stands.
    """
    members = [
        _claimed_member(member, species)
        if samples.get(sample) is None
        else _as_voted(member)
        for sample, member in row.members.items()
    ]
    consensus = vote_consensus(members, batch_peak_id=row.batch_peak_id)
    expected = species.reading()
    if species.kind == KIND_LIBRARY:
        return (
            consensus.consensus_role == ROLE_REAGENT
            and consensus.consensus_ion_formula == expected["ion_formula"]
        )
    return (
        not consensus.consensus_role
        and consensus.consensus_formula == expected["formula"]
    )


def earlier_own(anchor: Any, species: Species) -> Optional[set[str]]:
    """The samples the anchor's earlier decision of this species read as
    their file's own (:meth:`Species.own_test`); None where the anchor
    carries no record of the species."""
    record = known_species_record_of(anchor)
    earlier = (record or {}).get("species")
    if not isinstance(earlier, dict) or (
        earlier.get("kind"),
        earlier.get("ion_formula"),
        earlier.get("formula"),
    ) != (species.kind, species.ion_formula, species.formula):
        return None
    return set(record.get("own_test_samples") or ())


def own_tests(row: Row, species: Species) -> list[str]:
    """The samples whose file's own test held, as the decision pools them."""
    earlier = earlier_own(row.anchor, species)
    return sorted(
        sample
        for sample, member in row.members.items()
        if species.own_test(member, earlier)
    )


def _evidence(
    row: Row, own: list[str], files: int, samples: dict[str, Optional[str]]
) -> PooledEvidence:
    return PooledEvidence(
        files=files,
        present=len(row.members),
        test_held=len(own),
        claimable=sum(1 for decision in samples.values() if decision is None),
        kept=sum(1 for decision in samples.values() if decision not in (None, ALREADY)),
    )


def species_key(species: Species) -> tuple[str, Optional[str], Optional[str]]:
    """What makes two batch peaks' species one: its kind, ion and formula."""
    return (species.kind, species.ion_formula, species.formula)


def reads_on_twin(member: Optional[Member], species: Species) -> bool:
    """Whether a twin's member reads the species in its sample: claimed as
    the ion, or the listed formula at any tier."""
    return member is not None and (species.reads(member) or species.lifts(member))


def decide_row(
    row: Row,
    species: Species,
    files: int,
    carrying: Carrying,
    twins: Sequence[Row] = (),
) -> Decision:
    """Decide one species once for its partition, and what its claim does in
    each file.

    :param files: The partition's files.
    :param carrying: (sample, batch peak) -> the isotope labels of the members
        of the sample that name the batch peak as the owner of their line.
    :param twins: The other batch peaks of the species' trace, where this one
        claims it (:func:`settle_twins`): a sample that reads the species on
        one of them is kept, so the claim writes no second copy of it there.
    """
    samples = {
        sample: sample_decision(
            member, species, carrying.get((sample, row.batch_peak_id), ())
        )
        for sample, member in sorted(row.members.items())
    }
    for sample, decided in samples.items():
        if decided is None and any(
            reads_on_twin(twin.members.get(sample), species) for twin in twins
        ):
            samples[sample] = KEPT_ON_TWIN
    own = own_tests(row, species)
    verdict = decide_known_species(_evidence(row, own, files, samples))
    decision = Decision(
        row, species, verdict, own=own, twins=[twin.batch_peak_id for twin in twins]
    )
    if not verdict.present:
        return decision
    decision.samples = samples
    if manual_pin_of(row.anchor) is not None:
        decision.held = HELD_CURATED
    elif any(d is None for d in samples.values()) and not claim_turns(
        row, species, samples
    ):
        decision.held = HELD_VOTE
    return decision


def settle_twins(
    decisions: list[Decision], files: int, carrying: Carrying
) -> list[Decision]:
    """Let one batch peak claim each species of a partition.

    Where the fold split a species' trace over two batch peaks, both may be
    decided present, and both would claim it in a sample they share: the
    second claim rewrites what that sample reads there as a second copy of
    the species, an analyte included. Each of the species' batch peaks
    decided present and free to claim is decided again with the others as
    its twins, in the most files first (the first by id on a tie, as
    ``batch_species_for`` picks for a next run), and the first that still
    claims claims it; each other one is held for it (:data:`HELD_TWIN`).
    Where none claims beside its twins, say one held on the vote once the
    samples that read the species on a twin keep their reading, each stands
    as decided with its twins, and none writes.

    :return: The decisions, the free ones of a split trace decided again.
    """
    by_key: dict[tuple, list[Decision]] = defaultdict(list)
    for decision in decisions:
        by_key[species_key(decision.species)].append(decision)
    settled = {id(decision): decision for decision in decisions}
    for group in by_key.values():
        free = [d for d in group if d.verdict.present and d.held is None]
        if len(group) < 2 or not free:
            continue
        again: dict[int, Decision] = {}
        claiming: Optional[Decision] = None
        for candidate in sorted(
            free, key=lambda d: (-len(d.row.members), d.row.batch_peak_id)
        ):
            again[id(candidate)] = decide_row(
                candidate.row,
                candidate.species,
                files,
                carrying,
                twins=[d.row for d in group if d is not candidate],
            )
            if again[id(candidate)].claims:
                claiming = candidate
                break
        if claiming is None:
            settled.update(again)
            continue
        settled[id(claiming)] = again[id(claiming)]
        claiming_mz = round(
            trace_centre(claiming.row.anchor.mz, claiming.row.members.values()), 6
        )
        for decision in free:
            if decision is not claiming:
                decision.held = HELD_TWIN
                decision.twin_of = claiming.row.batch_peak_id
                decision.twin_of_mz = claiming_mz
    return [settled[id(decision)] for decision in decisions]


def _core_of(decision: Decision, library: list[Decision]) -> Optional[Decision]:
    """The species a library ion's trace is read against: the ion its ladder
    grew from, else the partition's brightest reagent or calibrant ion, else
    its brightest library ion. None for the core itself."""
    by_label = {d.species.label: d for d in library}
    core = (
        by_label.get(decision.species.grew_from) if decision.species.grew_from else None
    )
    if core is None:
        primary = [
            d for d in library if d.species.family in (FAMILY_REAGENT, FAMILY_CALIBRANT)
        ] or library
        core = max(
            primary,
            key=lambda d: (d.row.anchor.max_intensity or 0.0, d.row.batch_peak_id),
            default=None,
        )
    if core is None or core.row.batch_peak_id == decision.row.batch_peak_id:
        return None
    return core


def read_co_variations(rows: list[Row], decisions: list[Decision]) -> None:
    """Read each species' trace against its core's, and record it on the
    decision: a library ion against the ion its ladder grew from or the
    source's primary ion, a listed compound as the co-variation reading left
    it against its own other channel. Display-only (decision 35).

    The traces are read as the co-variation reading reads them
    (``coherence.batch_residuals``): log10, the batch's common mode removed,
    read off every monoisotopic, unclaimed batch peak of the partition and the
    species' own. They are not divided by the reagent signal, which a library
    ion is part of.
    """
    library = [d for d in decisions if d.species.kind == KIND_LIBRARY]
    for decision in decisions:
        if decision.species.kind == KIND_LISTED:
            record = standing_co_variation_record(decision.row.anchor)
            if record is not None:
                decision.co_variation = {
                    "core": "channel",
                    "partner_batch_peak_id": record.get("partner_batch_peak_id"),
                    "partner_channel": record.get("partner_channel"),
                    "verdict": record.get("verdict"),
                    "r": record.get("r"),
                    "n_spectra": record.get("n_spectra"),
                    "reason": record.get("reason"),
                }
    pairs = [(d, _core_of(d, library)) for d in library]
    pairs = [(d, core) for d, core in pairs if core is not None]
    if not pairs:
        return
    keys = {
        row.batch_peak_id
        for row in rows
        if _monoisotopic(row.anchor) and not row.anchor.consensus_role
    }
    keys |= {d.row.batch_peak_id for d, _ in pairs}
    keys |= {core.row.batch_peak_id for _, core in pairs}
    by_id = {row.batch_peak_id: row for row in rows}
    residuals = batch_residuals(
        {
            key: {
                sample: member.intensity
                for sample, member in by_id[key].members.items()
                if member.intensity
            }
            for key in sorted(keys)
        }
    )
    for decision, core in pairs:
        reading = residuals.co_variation(
            decision.row.batch_peak_id, core.row.batch_peak_id
        )
        decision.co_variation = {
            "core": "ion",
            "core_batch_peak_id": core.row.batch_peak_id,
            "core_ion": core.species.label,
            **reading.to_json(),
        }


def plan_partition(
    rows: list[Row], claims: dict[str, dict], counts: dict
) -> list[Decision]:
    """One partition's species, decided. ``counts`` is filled in."""
    files = len({sample for row in rows for sample in row.members})
    counts["files"] = files
    carrying = carrying_of(
        (member.sample_item_id, member.owner_batch_peak_id, member.isotope_label)
        for row in rows
        for member in row.members.values()
    )
    decisions = []
    for row in sorted(rows, key=lambda r: r.batch_peak_id):
        species = species_of(row, claims)
        if species is not None:
            decisions.append(decide_row(row, species, files, carrying))
    decisions = settle_twins(decisions, files, carrying)
    for decision in decisions:
        species = decision.species
        counts["species"] += 1
        counts[species.kind] += 1
        counts[decision.verdict.verdict] += 1
        if decision.held is not None:
            counts["held"] += 1
        elif decision.claims:
            counts["claimed"] += 1
    read_co_variations(rows, decisions)
    return decisions


def plan_batch(
    anchors: Iterable[Any],
    members: dict[str, dict[str, Member]],
    claims: dict[str, dict],
) -> Plan:
    """Every known species of the batch, decided once per partition.

    :param anchors: The batch's anchors.
    :param members: Anchor id -> sample -> its member.
    :param claims: Peak assignment id -> the ``provenance.reagent`` record of
        each row a pre-pass claimed (:func:`library_species`).
    :return: The decisions, and the counts.
    """
    by_partition: dict[Partition, list[Row]] = defaultdict(list)
    for anchor in sorted(anchors, key=lambda a: a.batch_peak_id):
        by_partition[partition_of(anchor)].append(
            Row(anchor, members.get(anchor.batch_peak_id, {}))
        )
    plan = Plan()
    for partition in sorted(by_partition, key=lambda p: (str(p[0]), str(p[1]))):
        counts = _empty_counts()
        plan.decisions.extend(plan_partition(by_partition[partition], claims, counts))
        plan.counts[partition] = counts
    return plan


# --- the records ----------------------------------------------------------------


def _member_reading(member: Member, reason: str) -> dict:
    return {
        "sample_item_id": member.sample_item_id,
        "reason": reason,
        "role": member.role,
        "formula": member.formula,
        "ion_formula": member.ion_formula,
        "tier": member.tier,
    }


def files_recorded(decision: Decision) -> tuple[list[dict], dict[str, int]]:
    """The files a record lists with their readings, and their counts by why:
    for a present species the files it kept, for one that is not the files
    that read it against the verdict - on their own test, or on an earlier
    decision's word."""
    row = decision.row
    if decision.verdict.present:
        entries = [
            _member_reading(row.members[sample], reason)
            for sample, reason in sorted(decision.samples.items())
            if reason not in (None, ALREADY)
        ]
    else:
        own = set(decision.own)
        entries = [
            _member_reading(member, OWN_CLAIM if sample in own else BATCH_CLAIM)
            for sample, member in sorted(row.members.items())
            if decision.species.reads(member)
        ]
    counts = dict(sorted(Counter(entry["reason"] for entry in entries).items()))
    return entries[:MAX_RECORDED_FILES], counts


def species_record(
    decision: Decision,
    *,
    reads: dict,
    batch_peak_run_id: Optional[str],
    displaced_members: Optional[dict[str, dict]] = None,
    rows_rewritten: int = 0,
    lines_rewritten: int = 0,
    rows_kept: Optional[dict[str, int]] = None,
) -> dict:
    """The record a batch peak carries of the decision."""
    files, by_reason = files_recorded(decision)
    displaced_members = displaced_members or {}
    return {
        "species": decision.species.to_json(),
        **decision.verdict.to_json(),
        "held": decision.held,
        "twin_of": decision.twin_of,
        "twin_of_mz": decision.twin_of_mz,
        "claimed": bool(displaced_members),
        "members_rewritten": len(displaced_members),
        "rows_rewritten": rows_rewritten,
        "lines_rewritten": lines_rewritten,
        "rows_kept": dict(sorted((rows_kept or {}).items())),
        "files_by_reason": by_reason,
        "files_recorded": files,
        "own_test_samples": list(decision.own),
        "co_variation": decision.co_variation,
        "displaced_members": displaced_members,
        "reads": reads,
        "batch_peak_run_id": batch_peak_run_id,
    }


def row_record(decision: Decision, batch_peak_run_id: Optional[str]) -> dict:
    """What a sample's row the claim rewrote records of the batch's decision."""
    verdict = decision.verdict.to_json()
    return {
        "batch_peak_id": decision.row.batch_peak_id,
        "verdict": verdict["verdict"],
        "files": verdict["files"],
        "present": verdict["present"],
        "test_held": verdict["test_held"],
        "batch_peak_run_id": batch_peak_run_id,
    }


def _tier_reason(*, lifted: bool) -> dict:
    """The tier reason a listed compound's claim names on a sample's row."""
    detail = (
        "the batch decided this listed compound present over its files; this "
        "file's own reading of it stood below candidate"
        if lifted
        else "the batch decided this listed compound present over its files; "
        "this file's own lines did not commit it, so it is a candidate"
    )
    return {"rule": REASON_BATCH_KNOWN_SPECIES, "detail": detail, "caps": False}


def listed_species_row(
    row: dict, exemplar: dict, species: Species, record: dict
) -> dict:
    """The values that rewrite a sample's row as the listed compound its batch
    decided present.

    The list's reading, through the ion and mechanism the files that committed
    it read it through, as a file's own row on it names it (``exemplar``): its
    formula, its source, its target and the list's identities. Its tier is
    candidate and it carries no fit: the batch's files say the compound is
    there, and this file's own lines did not commit it. The reading the row
    carried goes first among its alternatives, and the decision is the row's
    tier reason. The row is left as it is; the caller writes the values.

    :param row: The sample's row on the compound's peak.
    :param exemplar: A row of another sample that committed the compound.
    :param species: The compound.
    :param record: What the batch decided (:func:`row_record`).

    A row that already reads the compound's own formula, below candidate, is
    only lifted: it keeps its reading, its fit and its lines, and takes the
    tier and the reason.
    """
    if row.get("role") == ROLE_M0 and row.get("assigned_formula") == species.formula:
        provenance = dict(row.get("provenance") or {})
        provenance[REASON_BATCH_KNOWN_SPECIES] = {
            **record,
            "read_as": {"tier": row.get("tier")},
        }
        provenance["tier_reasons"] = [
            *(provenance.get("tier_reasons") or []),
            _tier_reason(lifted=True),
        ]
        return {"tier": LISTED_CLAIM_TIER, "provenance": provenance}
    alternatives = list(row.get("alternatives") or [])
    if row.get("assigned_formula"):
        alternatives = [reading_as_alternative(row, DISPLACED_BY_CLAIM), *alternatives]
    error = None
    try:
        exact = float(exemplar["sample_peak_mz"]) / (
            1.0 + float(exemplar["mz_error_ppm"]) / 1e6
        )
        error = round((float(row["sample_peak_mz"]) - exact) / exact * 1e6, 4)
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        error = None
    provenance: dict = {}
    identities = (exemplar.get("provenance") or {}).get(REFERENCE_IDENTITIES_COL)
    if identities:
        provenance[REFERENCE_IDENTITIES_COL] = identities
    provenance[REASON_BATCH_KNOWN_SPECIES] = {
        **record,
        "read_as": {
            "role": row.get("role"),
            "formula": row.get("assigned_formula"),
            "ion_formula": row.get("ion_formula"),
            "ionization_mechanism_id": row.get("ionization_mechanism_id"),
            "source": row.get("source"),
            "tier": row.get("tier"),
        },
    }
    provenance["tier_reasons"] = [_tier_reason(lifted=False)]
    return {
        "role": ROLE_M0,
        "assigned_formula": species.formula,
        "ion_formula": species.ion_formula,
        "ionization_mechanism_id": species.ionization_mechanism_id,
        "isotope_label": None,
        "isotope_formula": None,
        "source": exemplar.get("source"),
        "fit_score": None,
        "mz_error_ppm": error,
        "abundance_error": None,
        "tier": LISTED_CLAIM_TIER,
        "target_compound_id": exemplar.get("target_compound_id"),
        "target_ion_id": exemplar.get("target_ion_id"),
        "owner_peak_assignment_id": None,
        "alternatives": alternatives[:MAX_ALTERNATIVES_CEILING] or None,
        "provenance": provenance,
    }


# --- the run --------------------------------------------------------------------


@dataclass
class Marks:
    """Per member, what its sample's row says the decision must respect.

    :ivar hand: The (batch peak, sample) pairs whose row is a hand assignment.
    :ivar target: Those whose row was matched to a target compound.
    :ivar claims: Peak assignment id -> the ``provenance.reagent`` record of
        every row a pre-pass claimed.
    :ivar rows: (batch peak, sample) -> what the linked row reads, (role,
        formula, ion, tier), its isotope label, and whether it was written on
        the batch's word.
    """

    hand: set[tuple[str, str]] = field(default_factory=set)
    target: set[tuple[str, str]] = field(default_factory=set)
    claims: dict[str, dict] = field(default_factory=dict)
    rows: dict[tuple[str, str], tuple[tuple, Optional[str], bool]] = field(
        default_factory=dict
    )


def _member_from(
    row: Any, anchor_mz: float, registry: Optional[list], marks: Marks
) -> Member:
    identity = resolve_candidate(registry, row.candidate)
    key = (row.batch_peak_id, row.sample_item_id)
    reading, label, batch = marks.rows.get(key, (None, None, False))
    return Member(
        sample_item_id=row.sample_item_id,
        sample_peak_id=str(row.sample_peak_id),
        intensity=row.intensity,
        mz=mz_from_delta(anchor_mz, row.mz_delta_ppm),
        role=role_name(row.role),
        owner_batch_peak_id=row.owner_batch_peak_id,
        formula=identity.get("formula"),
        ion_formula=identity.get("ion_formula"),
        ionization_mechanism_id=identity.get("ionization_mechanism_id"),
        tier=tier_name(row.tier),
        fit_score=row.fit_score,
        peak_assignment_id=row.peak_assignment_id,
        hand=key in marks.hand,
        target=key in marks.target,
        isotope_label=label,
        row_reading=reading,
        row_batch=batch,
    )


async def _row_marks(
    session, sample_batch_id: str, batch_peak_ids: Optional[list[str]] = None
) -> Marks:
    """Per member, what its sample's row says the decision must respect
    (:class:`Marks`). A row was written on the batch's word where it names the
    decision: a library ion's under ``provenance.reagent.reason``, a listed
    compound's under its own key."""
    query = (
        select(
            BatchPeakOccurrence.batch_peak_id,
            BatchPeakOccurrence.sample_item_id,
            PeakAssignment.peak_assignment_id,
            PeakAssignment.source,
            PeakAssignment.target_compound_id,
            PeakAssignment.role,
            PeakAssignment.assigned_formula,
            PeakAssignment.ion_formula,
            PeakAssignment.tier,
            PeakAssignment.isotope_label,
            PeakAssignment.provenance[("reagent", "reason")]
            .as_string()
            .label("reagent_reason"),
            PeakAssignment.provenance[REASON_BATCH_KNOWN_SPECIES]
            .as_string()
            .label("batch_listed"),
        )
        .join(
            PeakAssignment,
            PeakAssignment.peak_assignment_id == BatchPeakOccurrence.peak_assignment_id,
        )
        .join(BatchPeak, BatchPeak.batch_peak_id == BatchPeakOccurrence.batch_peak_id)
        .where(BatchPeak.sample_batch_id == sample_batch_id)
    )
    if batch_peak_ids is not None:
        query = query.where(BatchPeakOccurrence.batch_peak_id.in_(batch_peak_ids))
    marks = Marks()
    reagent_rows: list[str] = []
    for row in (await session.execute(query)).all():
        key = (row.batch_peak_id, row.sample_item_id)
        if row.source == SOURCE_MANUAL:
            marks.hand.add(key)
        if row.target_compound_id:
            marks.target.add(key)
        if row.role == ROLE_REAGENT and row.source == SOURCE_REAGENT:
            reagent_rows.append(row.peak_assignment_id)
        marks.rows[key] = (
            (row.role, row.assigned_formula, row.ion_formula, row.tier),
            row.isotope_label,
            row.reagent_reason == REASON_BATCH_KNOWN_SPECIES
            or row.batch_listed is not None,
        )
    claims = marks.claims
    for start in range(0, len(reagent_rows), ROW_READ_CHUNK):
        for peak_assignment_id, provenance in (
            await session.execute(
                select(
                    PeakAssignment.peak_assignment_id, PeakAssignment.provenance
                ).where(
                    PeakAssignment.peak_assignment_id.in_(
                        reagent_rows[start : start + ROW_READ_CHUNK]
                    )
                )
            )
        ).all():
            record = (provenance or {}).get("reagent")
            if isinstance(record, dict):
                claims[peak_assignment_id] = record
    return marks


_MEMBER_COLUMNS = (
    BatchPeakOccurrence.batch_peak_id,
    BatchPeakOccurrence.sample_item_id,
    BatchPeakOccurrence.sample_peak_id,
    BatchPeakOccurrence.peak_assignment_id,
    BatchPeakOccurrence.intensity,
    BatchPeakOccurrence.mz_delta_ppm,
    BatchPeakOccurrence.role,
    BatchPeakOccurrence.owner_batch_peak_id,
    BatchPeakOccurrence.candidate,
    BatchPeakOccurrence.tier,
    BatchPeakOccurrence.fit_score,
)


async def _load_batch(
    sample_batch_id: str,
) -> tuple[list[BatchPeak], dict[str, dict[str, Member]], dict[str, dict]]:
    """The batch's anchors, every anchor's members, and the claims' records."""
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
                select(*_MEMBER_COLUMNS)
                .join(
                    BatchPeak,
                    BatchPeak.batch_peak_id == BatchPeakOccurrence.batch_peak_id,
                )
                .where(BatchPeak.sample_batch_id == sample_batch_id)
            )
        ).all()
        marks = await _row_marks(session, sample_batch_id)
    by_id = {anchor.batch_peak_id: anchor for anchor in anchors}
    members: dict[str, dict[str, Member]] = defaultdict(dict)
    for row in rows:
        anchor = by_id[row.batch_peak_id]
        members[row.batch_peak_id][row.sample_item_id] = _member_from(
            row, anchor.mz, anchor.candidates, marks
        )
    return list(anchors), dict(members), marks.claims


#: The columns of a sample's row the rewrite reads.
_ROW_FIELDS = (
    "peak_assignment_id",
    "sample_peak_id",
    "sample_peak_mz",
    "sample_peak_intensity",
    "role",
    "assigned_formula",
    "ion_formula",
    "ionization_mechanism_id",
    "isotope_label",
    "isotope_formula",
    "source",
    "fit_score",
    "mz_error_ppm",
    "abundance_error",
    "tier",
    "target_compound_id",
    "target_ion_id",
    "owner_peak_assignment_id",
    "alternatives",
    "provenance",
)


def _as_dict(row: Any) -> dict:
    return {key: getattr(row, key) for key in _ROW_FIELDS}


async def _read_rows(session, ids: list[str]) -> dict[str, Any]:
    rows = {}
    for start in range(0, len(ids), ROW_READ_CHUNK):
        for row in (
            (
                await session.execute(
                    select(PeakAssignment).where(
                        PeakAssignment.peak_assignment_id.in_(
                            ids[start : start + ROW_READ_CHUNK]
                        )
                    )
                )
            )
            .scalars()
            .all()
        ):
            rows[row.peak_assignment_id] = row
    return rows


def _exemplar(decision: Decision, rows: dict[str, Any]) -> Optional[dict]:
    """A row of a file whose own reading committed a listed compound, the
    brightest such, for the reading a claim writes."""
    best = None
    for member in decision.row.members.values():
        row = rows.get(member.peak_assignment_id or "")
        if (
            row is None
            or not decision.species.reads(member)
            or row.mz_error_ppm is None
        ):
            continue
        if best is None or (row.sample_peak_intensity or 0.0) > (
            best.sample_peak_intensity or 0.0
        ):
            best = row
    return None if best is None else _as_dict(best)


def _registry_exemplar(anchor: Any, species: Species) -> dict:
    """The listed compound's reading off the batch peak's registry, where no
    row of a file that committed it is left to read it off (a batch folded
    without runs): the entry's source and target, at the ion's own mass."""
    entry = next(
        (
            e
            for e in anchor.candidates or []
            if isinstance(e, dict)
            and e.get("formula") == species.formula
            and e.get("ion_formula") == species.ion_formula
            and e.get("ionization_mechanism_id") == species.ionization_mechanism_id
        ),
        {},
    )
    composition = (species.ion_formula or "").rstrip("+-")
    signs = (species.ion_formula or "")[len(composition) :]
    try:
        mz = ion_mz(composition, signs.count("+") - signs.count("-"))
    except Exception:  # noqa: BLE001 - a formula the parser refuses has no mass
        mz = None
    return {
        "source": entry.get("source"),
        "target_compound_id": entry.get("target_compound_id"),
        "target_ion_id": None,
        "provenance": {},
        "sample_peak_mz": mz,
        "mz_error_ppm": 0.0 if mz is not None else None,
    }


@dataclass
class Written:
    """What one species' write did."""

    decision: Decision
    claimed: bool
    members_rewritten: int = 0
    rows_rewritten: int = 0
    lines_rewritten: int = 0
    rows_kept: dict[str, int] = field(default_factory=dict)


async def _write_species(
    decision: Decision,
    anchor: Any,
    members: dict[str, Any],
    lines: dict[str, list[tuple[Any, Any]]],
    rows: dict[str, Any],
    in_app_runs: set[str],
    batch_peak_run_id: Optional[str],
    fresh_members: dict[str, Member],
) -> tuple[Written, dict[str, dict]]:
    """Claim one species in its files, on the ledger as it stands.

    :param members: Sample -> the anchor's member row.
    :param lines: Sample -> the (member row, anchor) of each isotope line the
        sample's member owns, for a library ion.
    :param fresh_members: Sample -> the member as the decision read it.
    :return: What was written, and each rewritten member's earlier state.
    """
    species = decision.species
    record = row_record(decision, batch_peak_run_id)
    registry = list(anchor.candidates or [])
    exemplar = (
        (_exemplar(decision, rows) or _registry_exemplar(anchor, species))
        if species.kind == KIND_LISTED
        else None
    )
    batch_species = (
        species.as_batch_species(record) if species.kind == KIND_LIBRARY else None
    )
    displaced: dict[str, dict] = {}
    rows_kept: Counter = Counter()
    rows_rewritten = lines_rewritten = 0
    for sample in sorted(decision.samples):
        if decision.samples[sample] is not None:
            continue
        member = members[sample]
        own_row = rows.get(member.peak_assignment_id or "")
        if own_row is None:
            rows_kept[ROW_KEPT_NO_ROW] += 1
        elif own_row.peak_assignment_run_id not in in_app_runs:
            rows_kept[ROW_KEPT_IMPORTED] += 1
        else:
            values = (
                batch_species_row(
                    _as_dict(own_row), batch_species, exact_mz=species.exact_mz
                )
                if batch_species is not None
                else listed_species_row(_as_dict(own_row), exemplar, species, record)
            )
            for key, value in values.items():
                setattr(own_row, key, value)
            rows_rewritten += 1
        displaced[sample] = member_state(member)
        if species.lifts(fresh_members[sample]):
            # Its own reading of the compound, lifted: what it measured stays.
            member.tier = tier_code(LISTED_CLAIM_TIER)
            continue
        if species.kind == KIND_LIBRARY:
            member.candidate = candidate_index(
                registry, None, species.ion_formula, None, source=SOURCE_REAGENT
            )
            member.role = role_code(ROLE_REAGENT)
            member.tier = tier_code(TIER_UNASSIGNED)
        else:
            member.candidate = candidate_index(
                registry,
                species.formula,
                species.ion_formula,
                species.ionization_mechanism_id,
                source=exemplar.get("source"),
            )
            member.role = role_code(ROLE_M0)
            member.tier = tier_code(LISTED_CLAIM_TIER)
        member.fit_score = None
        member.p_correct = None
        member.owner_batch_peak_id = None
        # A library ion's isotope lines go with it, as the pre-pass claims them.
        for line_member, line_anchor in lines.get(sample, ()):
            line_registry = list(line_anchor.candidates or [])
            line_member.candidate = candidate_index(
                line_registry, None, species.ion_formula, None, source=SOURCE_REAGENT
            )
            if line_registry != (line_anchor.candidates or []):
                line_anchor.candidates = line_registry
            line_member.role = role_code(ROLE_REAGENT)
            line_member.tier = tier_code(TIER_UNASSIGNED)
            line_member.fit_score = None
            line_member.p_correct = None
            line_row = rows.get(line_member.peak_assignment_id or "")
            if (
                own_row is not None
                and line_row is not None
                and line_row.peak_assignment_run_id in in_app_runs
                and line_row.owner_peak_assignment_id == own_row.peak_assignment_id
            ):
                for key, value in batch_species_row(
                    _as_dict(line_row), batch_species, exact_mz=species.exact_mz
                ).items():
                    setattr(line_row, key, value)
            lines_rewritten += 1
    if registry != (anchor.candidates or []):
        anchor.candidates = registry
    return (
        Written(
            decision,
            True,
            members_rewritten=len(displaced),
            rows_rewritten=rows_rewritten,
            lines_rewritten=lines_rewritten,
            rows_kept=dict(rows_kept),
        ),
        displaced,
    )


async def _write_chunk(
    sample_batch_id: str,
    decisions: list[Decision],
    files: dict[Partition, int],
    batch_peak_run_id: Optional[str],
) -> list[Written]:
    """Write a chunk of species in one transaction under the fold lock: claim
    those decided present in their files, read again as the ledger stands,
    record every one, and recompute the consensus of each batch peak the
    claims touched. A claiming species' twins' members are read with it, so
    a sample that reads it on one of them is kept (:func:`settle_twins`);
    what their rows mark is not needed for that."""
    ids = sorted({d.row.batch_peak_id for d in decisions})
    read_ids = sorted(set(ids) | {twin for d in decisions for twin in d.twins})
    async with async_session() as session:
        await _acquire_batch_fold_lock(session, sample_batch_id)
        anchors = {
            anchor.batch_peak_id: anchor
            for anchor in (
                await session.execute(
                    select(BatchPeak).where(BatchPeak.batch_peak_id.in_(read_ids))
                )
            )
            .scalars()
            .all()
        }
        members: dict[str, dict[str, Any]] = defaultdict(dict)
        for member in (
            (
                await session.execute(
                    select(BatchPeakOccurrence).where(
                        BatchPeakOccurrence.batch_peak_id.in_(read_ids)
                    )
                )
            )
            .scalars()
            .all()
        ):
            members[member.batch_peak_id][member.sample_item_id] = member
        # The isotope lines each member owns, in its own sample.
        line_members = (
            (
                await session.execute(
                    select(BatchPeakOccurrence).where(
                        BatchPeakOccurrence.owner_batch_peak_id.in_(ids),
                        BatchPeakOccurrence.role == role_code(ROLE_ISO_CHILD),
                    )
                )
            )
            .scalars()
            .all()
        )
        line_anchor_ids = sorted({m.batch_peak_id for m in line_members} - set(ids))
        line_anchors = dict(anchors)
        if line_anchor_ids:
            for anchor in (
                (
                    await session.execute(
                        select(BatchPeak).where(
                            BatchPeak.batch_peak_id.in_(line_anchor_ids)
                        )
                    )
                )
                .scalars()
                .all()
            ):
                line_anchors[anchor.batch_peak_id] = anchor
        # Every member naming one of these as its owner, with the label its
        # sample's row gives the line.
        carrying = carrying_of(
            (
                await session.execute(
                    select(
                        BatchPeakOccurrence.sample_item_id,
                        BatchPeakOccurrence.owner_batch_peak_id,
                        PeakAssignment.isotope_label,
                    )
                    .outerjoin(
                        PeakAssignment,
                        PeakAssignment.peak_assignment_id
                        == BatchPeakOccurrence.peak_assignment_id,
                    )
                    .where(BatchPeakOccurrence.owner_batch_peak_id.in_(ids))
                )
            ).all()
        )
        marks = await _row_marks(session, sample_batch_id, ids)
        links = [
            m.peak_assignment_id
            for by_sample in members.values()
            for m in by_sample.values()
            if m.peak_assignment_id
        ] + [m.peak_assignment_id for m in line_members if m.peak_assignment_id]
        rows = await _read_rows(session, sorted(set(links)))
        run_ids = sorted({row.peak_assignment_run_id for row in rows.values()})
        in_app_runs = set(
            (
                await session.execute(
                    select(PeakAssignmentRun.peak_assignment_run_id).where(
                        PeakAssignmentRun.peak_assignment_run_id.in_(run_ids),
                        PeakAssignmentRun.engine == IN_APP_ENGINE,
                    )
                )
            )
            .scalars()
            .all()
        )

        def fresh_row(anchor: Any) -> Row:
            return Row(
                anchor,
                {
                    sample: _member_from(member, anchor.mz, anchor.candidates, marks)
                    for sample, member in members.get(anchor.batch_peak_id, {}).items()
                },
            )

        written: list[Written] = []
        displaced_of: dict[str, dict[str, dict]] = {}
        touched: set[str] = set()
        for planned in decisions:
            anchor = anchors.get(planned.row.batch_peak_id)
            if anchor is None:
                continue
            fresh = fresh_row(anchor)
            species = (
                species_of(fresh, {}) if planned.species.kind == KIND_LISTED else None
            )
            if planned.species.kind == KIND_LISTED and (
                species is None or species.formula != planned.species.formula
            ):
                # The registry or the readings moved since: decided again next run.
                planned.held = HELD_MOVED if planned.verdict.present else None
                written.append(Written(planned, False))
                continue
            decision = decide_row(
                fresh,
                planned.species,
                files.get(partition_of(anchor), planned.verdict.evidence.files),
                carrying,
                twins=[fresh_row(anchors[t]) for t in planned.twins if t in anchors],
            )
            decision.co_variation = planned.co_variation
            if planned.held == HELD_TWIN and decision.verdict.present:
                decision.held = HELD_TWIN
                decision.twin_of = planned.twin_of
                decision.twin_of_mz = planned.twin_of_mz
            if decision.claims:
                by_sample: dict[str, list[tuple[Any, Any]]] = defaultdict(list)
                if decision.species.kind == KIND_LIBRARY:
                    for line in line_members:
                        if line.owner_batch_peak_id == anchor.batch_peak_id:
                            by_sample[line.sample_item_id].append(
                                (line, line_anchors[line.batch_peak_id])
                            )
                done, displaced = await _write_species(
                    decision,
                    anchor,
                    members[anchor.batch_peak_id],
                    by_sample,
                    rows,
                    in_app_runs,
                    batch_peak_run_id,
                    fresh.members,
                )
                if done.claimed:
                    touched.add(anchor.batch_peak_id)
                    touched |= {
                        line_anchor.batch_peak_id
                        for pairs in by_sample.values()
                        for _, line_anchor in pairs
                    }
                    displaced_of[anchor.batch_peak_id] = displaced
                written.append(done)
            else:
                written.append(Written(decision, False))
        now = datetime.now(timezone.utc)
        if touched:
            await session.flush()
            await _recompute_consensus(session, touched, now)
        for done in written:
            anchor = anchors.get(done.decision.row.batch_peak_id)
            if anchor is None:
                continue
            provenance = dict(anchor.provenance or {})
            provenance[KNOWN_SPECIES_KEY] = species_record(
                done.decision,
                reads=reading_of(anchor),
                batch_peak_run_id=batch_peak_run_id,
                displaced_members=displaced_of.get(anchor.batch_peak_id),
                rows_rewritten=done.rows_rewritten,
                lines_rewritten=done.lines_rewritten,
                rows_kept=done.rows_kept,
            )
            anchor.provenance = provenance
            anchor.batch_peak_utc_modified = now
        await touch_ledger(session, sample_batch_id)
        await session.commit()
    return written


async def _clear_stale_records(sample_batch_id: str, kept: set[str]) -> int:
    """Take the record off every batch peak this decision did not read: a new
    decision replaces every record."""
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
        cleared = 0
        for anchor in anchors:
            if anchor.batch_peak_id in kept or known_species_record_of(anchor) is None:
                continue
            provenance = dict(anchor.provenance or {})
            provenance.pop(KNOWN_SPECIES_KEY, None)
            anchor.provenance = provenance
            cleared += 1
        if cleared:
            await touch_ledger(session, sample_batch_id)
        await session.commit()
    return cleared


def summarise(plan: Plan, written: list[Written], cleared: int = 0) -> dict:
    """The run's summary: the counts per partition and over the batch, and the
    species, each as the write decided it on the ledger as it stood then."""
    final = {done.decision.row.batch_peak_id: done for done in written}
    by_partition: dict[Partition, dict] = {}
    for partition, counts in plan.counts.items():
        by_partition[partition] = {**_empty_counts(), "files": counts.get("files", 0)}
    decisions = []
    for planned in plan.decisions:
        done = final.get(planned.row.batch_peak_id)
        decision = planned if done is None else done.decision
        decisions.append(decision)
        counts = by_partition[partition_of(planned.row.anchor)]
        counts["species"] += 1
        counts[decision.species.kind] += 1
        counts[decision.verdict.verdict] += 1
        if decision.held is not None:
            counts["held"] += 1
        elif done is not None and done.claimed:
            counts["claimed"] += 1
    claimed = [done for done in written if done.claimed]
    rows_kept: Counter = Counter()
    kept: Counter = Counter()
    for done in claimed:
        rows_kept.update(done.rows_kept)
    for decision in decisions:
        kept.update(
            reason
            for reason in decision.samples.values()
            if reason not in (None, ALREADY)
        )
    rows = [
        {"ionization_mode_id": mode_id, "instrument_type": instrument_type, **counts}
        for (mode_id, instrument_type), counts in sorted(
            by_partition.items(), key=lambda item: (str(item[0][0]), str(item[0][1]))
        )
    ]
    held = Counter(d.held for d in decisions if d.held is not None)
    return {
        "thresholds": thresholds(),
        "partitions": rows,
        **{key: sum(row.get(key, 0) for row in rows) for key in _COUNTS},
        "held_by_reason": dict(sorted(held.items())),
        "members_rewritten": sum(done.members_rewritten for done in claimed),
        "rows_rewritten": sum(done.rows_rewritten for done in claimed),
        "lines_rewritten": sum(done.lines_rewritten for done in claimed),
        "rows_kept": dict(sorted(rows_kept.items())),
        "samples_kept": dict(sorted(kept.items())),
        "records_cleared": cleared,
        "species_listed": [d.to_json() for d in decisions[:MAX_LISTED]],
        "species_listed_at_most": MAX_LISTED,
    }


async def run_batch_known_species(
    sample_batch_id: str, batch_peak_run_id: Optional[str] = None
) -> dict:
    """Decide the batch's known species, claim the present ones, and record
    every one.

    :return: The run's summary (:func:`summarise`).
    """
    anchors, members, claims = await _load_batch(sample_batch_id)
    plan = await asyncio.to_thread(plan_batch, anchors, members, claims)
    files = {partition: counts["files"] for partition, counts in plan.counts.items()}
    written: list[Written] = []
    for start in range(0, len(plan.decisions), WRITE_CHUNK):
        written.extend(
            await _write_chunk(
                sample_batch_id,
                plan.decisions[start : start + WRITE_CHUNK],
                files,
                batch_peak_run_id,
            )
        )
    cleared = await _clear_stale_records(
        sample_batch_id, {d.row.batch_peak_id for d in plan.decisions}
    )
    return summarise(plan, written, cleared)


def deciding_outcome(summary: dict, sample_batch_id: str) -> dict:
    """The controller result the background-task decorator reports from."""
    notification_data = {"sample_batch_id": sample_batch_id}
    if not summary["species"]:
        return {
            "status": "partial",
            "message": (
                "No known species was read: no batch peak is a source ion a "
                "sample's pre-pass claimed, or a compound a list tags as "
                "background."
            ),
            "data": summary,
            "_notification_data": notification_data,
        }
    return {
        "status": "success",
        "message": (
            f"Decided {summary['species']} known species: {summary['present']} "
            f"present, {summary['absent']} absent and {summary['unpooled']} in too "
            f"few samples to pool; {summary['claimed']} claimed in "
            f"{summary['members_rewritten']} more sample"
            f"{'s' if summary['members_rewritten'] != 1 else ''}, "
            f"{summary['held']} held."
        ),
        "data": summary,
        "_notification_data": notification_data,
    }


@api_controller_background_task(
    success_notification_rooms=["sample_batch_id"],
    success_reload=[("peak_assignment", "sample_batch_id")],
    error_notification_rooms=["sample_batch_id"],
)
async def decide_batch_known_species(
    sample_batch_id: str,
    independent_transaction: bool = False,
    user_id: int | None = None,
    process_id: str | None = None,
    parent_id: str | None = None,
) -> dict:
    """Decide the batch's known species, as a batch run. Rewrites the members
    and the samples' in-app rows of each species claimed; emits
    ``peak_assignment_reload`` so the ledgers refresh."""
    run_id = await start_run(
        sample_batch_id, ACTION_KNOWN_SPECIES, config=thresholds(), user_id=user_id
    )
    try:
        summary = await run_batch_known_species(
            sample_batch_id, batch_peak_run_id=run_id
        )
    except Exception as exc:
        await fail_run(sample_batch_id, run_id, f"{type(exc).__name__}: {exc}")
        raise
    await complete_run(sample_batch_id, run_id, summary=summary)
    return deciding_outcome(summary, sample_batch_id)


# --- a sample's run takes the batch's word ----------------------------------------


async def batch_species_for(sample: Any) -> list[BatchSpecies]:
    """The library ions a sample's batch decided present, in the sample's
    partition: what its run's reagent pre-pass claims on the batch's word
    (``reagent_pass.claim_batch_species``).

    One entry per ion: where the fold split an ion's trace over two batch
    peaks, the decision lets one claim it and holds the other
    (:func:`settle_twins`), whose record serves no claim; should two records
    stand all the same, the one in the most files speaks for it. A second
    entry of the ion would claim another peak in the window.

    :param sample: The sample (``sample_batch_id``, ``ionization_mode_id``,
        ``filename``).
    :return: The species; none for a sample with no batch, or a batch that
        decided none.
    """
    sample_batch_id = getattr(sample, "sample_batch_id", None)
    if not sample_batch_id:
        return []
    async with async_session() as session:
        mode_id = getattr(sample, "ionization_mode_id", None)
        mode = (await root_ids(session, [mode_id])).get(mode_id) if mode_id else None
        anchors = (
            (
                await session.execute(
                    select(BatchPeak).where(
                        BatchPeak.sample_batch_id == sample_batch_id,
                        BatchPeak.ionization_mode_id == mode,
                        BatchPeak.intensity_variable
                        == _intensity_variable(getattr(sample, "filename", "") or ""),
                        BatchPeak.consensus_role == ROLE_REAGENT,
                    )
                )
            )
            .scalars()
            .all()
        )
    by_ion: dict[tuple[str, int], BatchSpecies] = {}
    for anchor in sorted(anchors, key=lambda a: a.batch_peak_id):
        record = standing_known_species_record(anchor)
        one = batch_species_from(record, anchor.batch_peak_id) if record else None
        if one is None:
            continue
        ion = (one.cluster.formula, one.cluster.charge)
        if ion not in by_ion or (one.record.get("present") or 0) > (
            by_ion[ion].record.get("present") or 0
        ):
            by_ion[ion] = one
    return list(by_ion.values())


__all__ = [
    "ALREADY",
    "BACKGROUND_TAG",
    "BATCH_CLAIM",
    "HELD_CURATED",
    "HELD_MOVED",
    "HELD_TWIN",
    "HELD_VOTE",
    "KEPT_CARRIES_LINES",
    "KEPT_CLAIMED_OTHERWISE",
    "KEPT_HAND_ASSIGNED",
    "KEPT_LINE_OF_ANOTHER",
    "KEPT_ON_TWIN",
    "KEPT_TARGET_LIBRARY",
    "LISTED_CLAIM_TIER",
    "OWN_CLAIM",
    "Decision",
    "Marks",
    "Member",
    "Plan",
    "Row",
    "Species",
    "batch_species_for",
    "claim_turns",
    "decide_batch_known_species",
    "decide_row",
    "deciding_outcome",
    "earlier_own",
    "listed_species",
    "listed_species_row",
    "library_species",
    "own_tests",
    "plan_batch",
    "run_batch_known_species",
    "sample_decision",
    "settle_twins",
    "species_of",
    "species_record",
    "summarise",
]
