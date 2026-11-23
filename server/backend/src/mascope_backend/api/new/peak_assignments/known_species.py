"""The batch's known species as a sample's run reads them.

A species of the batch - a library ion or a listed compound, read across the
partition of the batch its samples fold into - and what a run takes on the
batch's word: the ions it claims for the source (``batch_species_for``)
and the listed compound's row at the batch's reading. The batch stage that
decides them is ``batch_known_species``; ``batch_decisions`` reads them into
a run.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import select

from mascope_backend.api.new.ionization.modes.versions import root_ids
from mascope_backend.api.new.peak_assignments.batch_peaks import (
    ROLE_ISO_CHILD,
    ROLE_M0,
    ROLE_REAGENT,
    SOURCE_ROLES,
    _intensity_variable,
    instrument_type_of,
    standing_known_species_record,
)
from mascope_backend.api.new.peak_assignments.config import MAX_ALTERNATIVES_CEILING
from mascope_backend.api.new.peak_assignments.engine import (
    DISPLACED_BY_CLAIM,
    reading_as_alternative,
)
from mascope_backend.api.new.peak_assignments.identification_level import (
    IDENTIFICATION_LEVEL,
    hand_level,
)
from mascope_backend.api.new.peak_assignments.reagent_pass import (
    REASON_BATCH_KNOWN_SPECIES,
    BatchSpecies,
    batch_species_from,
)
from mascope_backend.api.new.peak_assignments.row_terms import REFERENCE_IDENTITIES_COL
from mascope_backend.api.new.peak_assignments.tiers import TIER_ASSIGNED, TIER_CANDIDATE
from mascope_backend.db import BatchPeak, async_session
from mascope_tools.composition.known_species import KIND_LIBRARY, KIND_LISTED
from mascope_tools.composition.reagents import (
    FAMILY_REAGENT,
    KIND_CLUSTER,
    ReagentCluster,
    ion_mz,
)


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


#: Why a claim on the batch's word keeps a file's reading of a peak
#: (:func:`claim_keeps`): a person assigned it; the target library named it;
#: a reference list named it (a water-ladder rung's claim only); it is
#: another reading's isotope line; another source claim took it; it carries
#: isotope lines the claimed species cannot have.
KEPT_HAND_ASSIGNED = "hand_assigned"
KEPT_TARGET_LIBRARY = "target_library"
KEPT_LISTED = "listed"
KEPT_LINE_OF_ANOTHER = "line_of_another"
KEPT_CLAIMED_OTHERWISE = "claimed_otherwise"
KEPT_CARRIES_LINES = "carries_lines"
#: Kept: the sample reads the species on a twin, another batch peak of the
#: species' trace (:func:`settle_twins`).
KEPT_ON_TWIN = "reads_on_twin"


@dataclass(frozen=True)
class PeakReading:
    """What a claim on the batch's word asks of one file's reading of a peak,
    read off a batch peak's member (the batch's own claims) or off a run's
    rows on the peak (a run's claim, ``batch_decisions.claim_source_ions``).

    :ivar hand: A person assigned the peak.
    :ivar target: The reading names a compound of the workspace's target
        library.
    :ivar listed: The reading names a reference list's identity.
    :ivar line_of_another: The peak is another reading's isotope line.
    :ivar claimed_otherwise: Another source claim took the peak: a reagent,
        artifact or fragment claim.
    :ivar lines: The isotope lines the reading carries, as the caller's
        ``owns_line`` reads them.
    """

    hand: bool = False
    target: bool = False
    listed: bool = False
    line_of_another: bool = False
    claimed_otherwise: bool = False
    lines: tuple[Any, ...] = ()


def claim_keeps(
    reading: PeakReading,
    *,
    owns_line: Callable[[Any], bool],
    keep_listed: bool = False,
) -> Optional[str]:
    """Why a claim on the batch's word keeps a file's reading of a peak, or
    None where the claim takes it: the one rule the batch's own claims
    (``batch_known_species.sample_decision``,
    ``batch_water_ladder.sample_decision``) and a run's claim on the batch's
    word (``batch_decisions.claim_source_ions``) read, in this order.

    :param reading: What the file reads on the peak.
    :param owns_line: Whether a line the reading carries is the claimed
        species' own: one it can have, so the claim takes it with the peak.
    :param keep_listed: Whether a reference list's reading keeps the peak: a
        water-ladder rung's claim keeps one, a known species' does not.
    :return: A ``KEPT_*`` reason, or None.
    """
    if reading.hand:
        return KEPT_HAND_ASSIGNED
    if reading.target:
        return KEPT_TARGET_LIBRARY
    if keep_listed and reading.listed:
        return KEPT_LISTED
    if reading.line_of_another:
        return KEPT_LINE_OF_ANOTHER
    if reading.claimed_otherwise:
        return KEPT_CLAIMED_OTHERWISE
    if not all(owns_line(line) for line in reading.lines):
        return KEPT_CARRIES_LINES
    return None


# --- what is read -------------------------------------------------------------


#: A partition of the batch's anchors: their ionization mode and the
#: instrument type their unit names, the files whose peaks fold together.
#: (Defined here rather than taken from ``batch_isotopologues``, which the
#: run's service imports, and this module is imported by it.)
Partition = tuple[Optional[str], Optional[str]]


def partition_of(anchor: Any) -> Partition:
    """The partition an anchor is read in."""
    return (anchor.ionization_mode_id, instrument_type_of(anchor.intensity_variable))


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
    :ivar batch_stage: The batch stage whose mark the member carries
        (``batch_peaks.batch_mark``): the member reads what a stage wrote, not
        its file's own run or fold. None on a member no stage wrote.
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
    batch_stage: Optional[str] = None

    @property
    def source_claim(self) -> bool:
        """Whether a pre-pass claimed the peak as a source's monoisotopic ion."""
        return (
            self.role == ROLE_REAGENT
            and not self.formula
            and not self.owner_batch_peak_id
        )


def member_reading(
    member: Member, lines: Sequence[Any] = (), *, listed: bool = False
) -> PeakReading:
    """What a claim on the batch's word asks of a batch peak's member
    (:func:`claim_keeps`).

    :param lines: The isotope lines the members of the file that name the
        batch peak as their owner carry.
    :param listed: Whether the member's reading names a reference list's
        identity.
    """
    return PeakReading(
        hand=member.hand,
        target=member.target,
        listed=listed,
        line_of_another=member.role == ROLE_ISO_CHILD
        or bool(member.owner_batch_peak_id),
        claimed_otherwise=member.role in SOURCE_ROLES,
        lines=tuple(lines),
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

    def own_test(
        self,
        member: Member,
        earlier: Optional[set[str]],
        claimed: frozenset[str] = frozenset(),
    ) -> bool:
        """Whether a member reads the species on its file's own test, not on
        the batch's word.

        A member whose row was written on the batch's word (a decision's
        rewrite, a run's claim on the batch's decision, or the batch's water
        ladder) is the batch's. One whose row reads the species is its
        file's. One with no row that says so - none linked, or an import's
        the batch did not rewrite - is the batch's where a batch stage wrote
        it, by the mark it carries (``batch_peaks.batch_mark``), which
        outlives the record of the decision that wrote it (MAS-891), or where
        its water ladder claimed it; else its file's only where the batch
        never decided the species, or where the decision before this one read
        it as its file's own: a claim from before the marks leaves no other
        trace.

        :param earlier: The samples the anchor's earlier decision of this
            species read as their file's own; None where there is none.
        :param claimed: The samples the batch's water ladder claimed the
            species in at the anchor (:func:`ladder_claimed`).
        """
        if not self.reads(member) or member.row_batch:
            return False
        if member.row_reading is not None and self._reading_reads(*member.row_reading):
            return True
        if member.batch_stage or member.sample_item_id in claimed:
            return False
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
    tier reason. Its identification level is what the mapping can state of a
    formula no stage of its run elected (``identification_level.hand_level``):
    no shortlist of the run was competed against it. The row is left as it
    is; the caller writes the values.

    :param row: The sample's row on the compound's peak.
    :param exemplar: A row of another sample that committed the compound.
    :param species: The compound.
    :param record: What the batch decided (:func:`row_record`).

    A row that already reads the compound's own formula, below candidate, is
    only lifted: it keeps its reading, its fit, its lines and its level, and
    takes the tier and the reason.
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
    values = {
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
    level = hand_level(values)
    provenance[IDENTIFICATION_LEVEL] = level
    values["level"] = level["level"]
    return values


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


# --- a sample's run takes the batch's word ----------------------------------------


async def sample_partition(session, sample: Any) -> Partition:
    """The partition of its batch a sample's peaks fold into: its ionization
    mode's root and the intensity variable its file names.

    :param sample: The sample (``ionization_mode_id``, ``filename``).
    """
    mode_id = getattr(sample, "ionization_mode_id", None)
    mode = (await root_ids(session, [mode_id])).get(mode_id) if mode_id else None
    return mode, _intensity_variable(getattr(sample, "filename", "") or "")


async def batch_species_for(sample: Any) -> list[BatchSpecies]:
    """The library ions a sample's batch decided present, in the sample's
    partition: what its run claims on the batch's word
    (``batch_decisions.claim_source_ions``).

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
        mode, variable = await sample_partition(session, sample)
        anchors = (
            (
                await session.execute(
                    select(BatchPeak).where(
                        BatchPeak.sample_batch_id == sample_batch_id,
                        BatchPeak.ionization_mode_id == mode,
                        BatchPeak.intensity_variable == variable,
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
    "KEPT_CARRIES_LINES",
    "KEPT_CLAIMED_OTHERWISE",
    "KEPT_HAND_ASSIGNED",
    "KEPT_LINE_OF_ANOTHER",
    "KEPT_LISTED",
    "KEPT_ON_TWIN",
    "KEPT_TARGET_LIBRARY",
    "LISTED_CLAIM_TIER",
    "Member",
    "PeakReading",
    "Species",
    "claim_keeps",
    "member_reading",
    "batch_species_for",
    "listed_species_row",
    "sample_partition",
]
