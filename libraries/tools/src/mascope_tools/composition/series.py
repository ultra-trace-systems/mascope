"""Homologous series on a batch's anchors: the units, the walk and its decoys.

A homologous series is a set of formulas one repeat unit apart - CH2, O, CO2,
the siloxane unit C2H6OSi - which on the Kendrick mass scale share a mass
defect (Kendrick, Anal. Chem. 1963, 35, 2146; Hughey et al., Anal. Chem. 2001,
73, 4676). A batch that holds a compound at a confident reading
often holds its homologues too, unassigned or held at candidate because the one
peak each of them makes is all anything saw of it. This module is the pure half
of step 4.1 of the assignment plan (``docs/dev/assignment_quality_plan.md``):
from the batch's assigned anchors it walks the units, proposes a formula at the
exact mass the walk predicts wherever an anchor nobody assigned sits there, and
measures how many of those proposals the batch would have made by chance.

The rule is written from the plan's step and the reference engine's published
documentation of its series stages (peaky's ``docs/GKA.md`` and its changelog),
never from its code (``docs/dev/peaky_mascope_boundary.md``, section 3), and
its thresholds are measured on this project's own batches.

The walk
--------

:func:`walk_series` starts from every source (an assigned anchor's neutral
formula and the mechanism it is ionized through) and steps one unit up and one
down. A step is a formula - the source's counts plus or minus the unit's - and
the ion that formula makes through the source's own mechanism, at its exact
m/z. Where a target (an anchor the batch did not assign) sits within the
window of that m/z, the target is reached and proposed that formula, and the
walk steps on from the proposal: from its exact mass, never from the target's
measured one, so an error does not ride along the chain. A target is reached
once, at the shortest distance from an assigned anchor; the walk ends after
:data:`MAX_DEPTH` steps or where it reaches nothing new.

A step is a formula, so it must be one: no count goes below zero and the result
keeps a carbon, as every grid the engine searches does. A contaminant unit
steps only from a formula that carries its element (:attr:`SeriesUnit.requires`):
a siloxane ladder runs between siloxanes, and a silicon atom stepped onto an
oxidised terpene is a mass coincidence rather than a series. Every unit is a
closed-shell neutral, so a step keeps a molecule a molecule.

The decoys
----------

A dense batch holds anchors one CH2 apart by chance alone, and a walk over it
proposes formulas whether or not a series is there. So every unit is walked
again at its mass shifted by each of :data:`DECOY_OFFSETS_DA`, with the same
formula arithmetic, the same window and the same sources and targets: a shift
of a few hundredths of a dalton is far outside the window and lands on no
chemistry, so what the shifted walk reaches is what chance reaches
(:func:`series_evidence`): the target-decoy estimate of proteomics (Elias and
Gygi, Nat. Methods 2007, 4, 207), on masses. The unit's ``enrichment`` is its own proposals over
the decoys' mean, and its false discovery rate the inverse. A unit's family is
committed only where it holds at least :data:`MIN_LINKS` proposals and an
enrichment of at least :data:`MIN_ENRICHMENT` (:attr:`SeriesEvidence.significant`).
The reference engine uses three offsets, 12 links and an enrichment of 3; the
offsets and thresholds here are this project's own, measured on its batches
(the constants say how, and ``docs/dev/assignment_quality_plan.md``,
step 4.1, gives the measurement).

A proposal is not an assignment. It is a formula to measure: the batch scores
it against every sample's own peaks before anything is written, and a member
whose envelope the measurement does not reach stays as it was.
"""

from __future__ import annotations

import bisect
from collections import defaultdict
from dataclasses import dataclass, field
from functools import cached_property, lru_cache
from typing import Iterable, Mapping, Sequence

from mascope_tools.composition.models import IonizationMechanism
from mascope_tools.composition.utils import (
    composition_mass,
    parse_composition,
    parse_formula_tokens,
    parse_ionization,
)


#: The families a unit belongs to. ``organic`` grows a molecule's own skeleton;
#: the other two are the contaminant ladders a mass spectrometer meets in every
#: laboratory: siloxane from column bleed and silicone tubing, fluorinated from
#: fluoropolymer and the perfluorinated acids.
FAMILY_ORGANIC = "organic"
FAMILY_SILOXANE = "siloxane"
FAMILY_FLUORINATED = "fluorinated"


@dataclass(frozen=True)
class SeriesUnit:
    """One repeat unit: a closed-shell neutral fragment a series steps by.

    :param formula: The unit's composition (``"CH2"``).
    :param family: The family it grows (:data:`FAMILY_ORGANIC`, ...).
    :param requires: The element a source must carry for the unit to step from
        it, or None for a unit that steps from any formula.
    """

    formula: str
    family: str
    requires: str | None = None

    @cached_property
    def counts(self) -> dict[str, int]:
        """The unit's element counts."""
        return parse_formula_tokens(self.formula)

    @cached_property
    def mass(self) -> float:
        """The unit's monoisotopic mass in Da."""
        return composition_mass(parse_composition(self.formula))


#: The units an organic molecule grows by, as the reference engine's
#: documentation lists them: a methylene, an oxygen, a water, a carbonyl, a
#: carboxyl's carbon dioxide and a ketene.
ORGANIC_UNITS: tuple[SeriesUnit, ...] = tuple(
    SeriesUnit(formula, FAMILY_ORGANIC)
    for formula in ("CH2", "O", "H2O", "CO", "CO2", "C2H2O")
)

#: The contaminant ladders: the dimethylsiloxane unit and difluoromethylene.
#: Every shipped reagent profile meets both - a siloxane does not care which
#: reagent ionized it - so the profiles carry no list of their own.
CONTAMINANT_UNITS: tuple[SeriesUnit, ...] = (
    SeriesUnit("C2H6OSi", FAMILY_SILOXANE, requires="Si"),
    SeriesUnit("CF2", FAMILY_FLUORINATED, requires="F"),
)

#: Every unit the batch's series detection walks, in the order it reports them.
SERIES_UNITS: tuple[SeriesUnit, ...] = ORGANIC_UNITS + CONTAMINANT_UNITS

#: The shifts, in Da, at which a unit is walked again to count what chance
#: reaches. A batch is full of formulas that differ by a composition swap -
#: CH4 for O (36.4 mDa), CH2 for N (12.6 mDa), their sums and differences - so
#: a walk shifted onto one of those lands on chemistry, not chance. Measured on
#: the testbed's sixteen Orbitrap batch ledgers (every unit walked one step at
#: shifts from -100 to +100 mDa in 0.5 mDa steps), the reached count peaks at
#: +-11, +-25, +-36, +-48, +-62 and +-73 mDa and has its floor between them;
#: these six sit on that floor for every unit. Two of the reference engine's
#: three (+31.7 and -47.3 mDa) sit on the shoulder of a peak in the same
#: curve, counting chemistry as chance.
DECOY_OFFSETS_DA: tuple[float, ...] = (-0.0925, -0.067, -0.031, 0.0305, 0.0665, 0.0895)

#: The fewest proposals a unit's family must make before it is committed.
#: Measured with the same six decoys over the testbed's twenty batch ledgers,
#: where six more offsets on the curve's floor, each walked as if it were a
#: unit, stand for families that are not there (960 such): at the enrichment
#: below, none of them reaches 10 proposals, and 12 commits exactly the
#: families 10 does. The reference engine's 12 holds.
MIN_LINKS = 12

#: How many times the decoys' mean a unit's proposals must reach before its
#: family is committed. The reference engine's 3 does not hold here: on the
#: testbed it commits every organic unit of the four TOF batches, walked in the
#: TOF's 10 ppm window, where only 40 to 60% of the proposals that land on an
#: anchor the batch reads at candidate agree with its formula, against 90 to
#: 98% on the Orbitrap batches. From 10 no TOF family is committed and the
#: committed ones pool at a false discovery rate of 3.6% on the decoys (59
#: families on 12 of the 20 batches, 6,123 proposals); raising it further
#: drops Orbitrap families and changes neither number.
MIN_ENRICHMENT = 10.0

#: How many units the walk steps from an assigned anchor at most. On the
#: committed families of the testbed the first step makes 81% of the
#: proposals, the second 15% and the third 4%, at the same agreement with the
#: anchors' candidate readings; a fourth would add little the decoys cannot
#: already explain.
MAX_DEPTH = 3


# --- formulas -------------------------------------------------------------------


def _hill(counts: Mapping[str, int]) -> str:
    """A neutral formula as the engine writes one: carbon, hydrogen, then the
    rest alphabetically, a labelled element beside its base element.

    The walk only builds formulas that keep a carbon, so the carbon-first
    order is always the one that applies.
    """

    def key(symbol: str) -> tuple:
        base = symbol.lstrip("^")
        group = 0 if base == "C" else 1 if base == "H" else 2
        return (group, base, symbol.startswith("^"), symbol)

    return "".join(
        symbol if counts[symbol] == 1 else f"{symbol}{counts[symbol]}"
        for symbol in sorted(counts, key=key)
        if counts[symbol] > 0
    )


def step_formula(
    counts: Mapping[str, int], unit: SeriesUnit, n: int
) -> dict[str, int] | None:
    """A formula ``n`` units from ``counts``, or None where it is no formula.

    :param counts: The neutral's element counts.
    :param unit: The unit to step by.
    :param n: How many units, signed: +1 adds one, -1 takes one away.
    :return: The stepped counts, or None where a count would fall below zero,
        the carbon would be lost, or the unit needs an element the source does
        not carry.
    """
    if unit.requires is not None and counts.get(unit.requires, 0) <= 0:
        return None
    stepped = dict(counts)
    for symbol, count in unit.counts.items():
        stepped[symbol] = stepped.get(symbol, 0) + n * count
    if any(count < 0 for count in stepped.values()):
        return None
    stepped = {symbol: count for symbol, count in stepped.items() if count}
    if stepped.get("C", 0) < 1:
        return None
    return stepped


@lru_cache(maxsize=256)
def _mechanism(notation: str) -> IonizationMechanism:
    return parse_ionization(notation)


def ion_mz(counts: Mapping[str, int], notation: str) -> float:
    """The exact m/z of a neutral ionized through a mechanism.

    :param counts: The neutral's element counts.
    :param notation: The mechanism, in either adduct notation.
    :return: The singly charged ion's m/z.
    """
    mechanism = _mechanism(notation)
    neutral = composition_mass(parse_composition(_hill(counts)))
    shift = mechanism.mass if mechanism.addition else -mechanism.mass
    return (neutral + shift) / abs(mechanism.charge)


#: The swaps a labelled nitrate's 15N makes indistinguishable from an
#: organosulfur or a fluorinated reading, as the reference engine's changelog
#: states them: C8S against H ^N O7 (0.27 mDa) and C2F3 against H2 ^N O4
#: (0.21 mDa). The alias keeps every CH2, O and H2O step the reading takes.
LABEL_ALIASES: tuple[dict[str, int], ...] = (
    {"C": 8, "S": 1},
    {"C": 2, "F": 3},
)


def has_label_alias(counts: Mapping[str, int]) -> bool:
    """Whether a reading has a same-ion alias on a labelled-nitrate source.

    Where it has, the series cannot say which of the two readings it is
    stepping: both take the same steps, so a homologue at the alias's mass
    corroborates the alias exactly as well. The walk proposes no such reading
    on a labelled source.

    :param counts: The neutral's element counts.
    :return: True where the formula holds one of :data:`LABEL_ALIASES`.
    """
    return any(
        all(counts.get(symbol, 0) >= count for symbol, count in alias.items())
        for alias in LABEL_ALIASES
    )


# --- the walk ---------------------------------------------------------------------


@dataclass(frozen=True)
class SeriesSource:
    """An anchor the walk starts from: an assigned reading.

    :param key: The anchor's id.
    :param formula: Its neutral formula.
    :param mechanism: The mechanism it is ionized through, a notation.
    """

    key: str
    formula: str
    mechanism: str


@dataclass(frozen=True)
class SeriesTarget:
    """An anchor the walk may reach: one the batch did not assign.

    :param key: The anchor's id.
    :param mz: Its m/z.
    :param formula: The reading it holds at candidate, or None for an
        unassigned anchor.
    """

    key: str
    mz: float
    formula: str | None = None


@dataclass(frozen=True)
class Proposal:
    """A formula the walk proposes for a target.

    :param target: The target's key.
    :param formula: The proposed neutral formula.
    :param mechanism: The mechanism it is read through: its source's.
    :param unit: The unit's formula.
    :param step: +1 where the proposal is one unit heavier than the formula it
        was stepped from, -1 where lighter.
    :param anchor: The key of the anchor it was stepped from: an assigned
        anchor, or a proposal one step nearer one.
    :param root: The assigned anchor its chain starts at.
    :param depth: How many units it sits from ``root``.
    :param ppm: The target's m/z less the proposal's exact m/z, in ppm of it.
    :param exact_mz: The proposal's exact m/z.
    """

    target: str
    formula: str
    mechanism: str
    unit: str
    step: int
    anchor: str
    root: str
    depth: int
    ppm: float
    exact_mz: float


def _nearest(
    mzs: Sequence[float], keys: Sequence[str], mz: float, tolerance_ppm: float
) -> tuple[str, float] | None:
    """The target nearest ``mz`` within the window, and its error in ppm."""
    half = mz * tolerance_ppm * 1e-6
    lo = bisect.bisect_left(mzs, mz - half)
    hi = bisect.bisect_right(mzs, mz + half)
    best: tuple[float, str, float] | None = None
    for index in range(lo, hi):
        ppm = (mzs[index] - mz) / mz * 1e6
        candidate = (abs(ppm), keys[index], ppm)
        if best is None or candidate < best:
            best = candidate
    return None if best is None else (best[1], best[2])


def walk_series(
    sources: Iterable[SeriesSource],
    targets: Iterable[SeriesTarget],
    unit: SeriesUnit,
    *,
    tolerance_ppm: float,
    offset_da: float = 0.0,
    max_depth: int = MAX_DEPTH,
    labelled: bool = False,
) -> dict[str, Proposal]:
    """Walk one unit from every source and propose a formula for each target
    reached.

    Breadth first, so a target reached from two directions is reached at its
    shortest distance from an assigned anchor; between two proposals at one
    distance, the one whose exact mass is nearer the target's takes it, then
    the lighter formula, then the anchor key, so the walk never follows the
    order its sources came in. A target takes the step its nearest exact mass
    reaches only: one exact mass is one ion, and a window holding two anchors
    names the closer.

    :param sources: The assigned anchors.
    :param targets: The anchors the walk may reach.
    :param unit: The unit to step by.
    :param tolerance_ppm: The window, in ppm of the exact m/z, a target must
        sit in.
    :param offset_da: A decoy shift added to the unit's mass, 0 for the unit
        itself. The formula arithmetic is the unit's whatever the shift: a
        decoy walk takes exactly the steps the real one could.
    :param max_depth: The most units a proposal may sit from its root.
    :param labelled: Whether the source is a labelled nitrate, on which a
        reading with a same-ion alias (:func:`has_label_alias`) is not proposed.
    :return: Target key -> its proposal.
    """
    ordered = sorted(targets, key=lambda target: (target.mz, target.key))
    mzs = [target.mz for target in ordered]
    keys = [target.key for target in ordered]
    step_mass = unit.mass + offset_da

    # (key, counts, mechanism, exact m/z, root)
    frontier = []
    for source in sorted(sources, key=lambda source: source.key):
        counts = parse_formula_tokens(source.formula)
        try:
            exact = ion_mz(counts, source.mechanism)
        except Exception:  # noqa: BLE001 - a mechanism the finder cannot read
            # steps nowhere: the walk proposes only ions it can mass.
            continue
        frontier.append((source.key, counts, source.mechanism, exact, source.key))

    proposals: dict[str, Proposal] = {}
    for depth in range(1, max_depth + 1):
        reached: dict[str, tuple[tuple, Proposal, dict]] = {}
        for key, counts, mechanism, exact, root in frontier:
            charge = abs(_mechanism(mechanism).charge)
            for n in (1, -1):
                stepped = step_formula(counts, unit, n)
                if stepped is None or (labelled and has_label_alias(stepped)):
                    continue
                stepped_mz = exact + n * step_mass / charge
                hit = _nearest(mzs, keys, stepped_mz, tolerance_ppm)
                if hit is None or hit[0] in proposals:
                    continue
                target, ppm = hit
                formula = _hill(stepped)
                proposal = Proposal(
                    target=target,
                    formula=formula,
                    mechanism=mechanism,
                    unit=unit.formula,
                    step=n,
                    anchor=key,
                    root=root,
                    depth=depth,
                    ppm=ppm,
                    exact_mz=stepped_mz,
                )
                rank = (abs(ppm), len(formula), formula, key)
                if target not in reached or rank < reached[target][0]:
                    reached[target] = (rank, proposal, stepped)
        if not reached:
            break
        frontier = []
        for target in sorted(reached):
            _, proposal, stepped = reached[target]
            proposals[target] = proposal
            frontier.append(
                (target, stepped, proposal.mechanism, proposal.exact_mz, proposal.root)
            )
    return proposals


# --- the decoys -------------------------------------------------------------------


@dataclass(frozen=True)
class SeriesEvidence:
    """What one unit's walk reached, against what its decoys reached.

    :param unit: The unit's formula.
    :param family: Its family.
    :param n_links: How many targets the walk proposed a formula for.
    :param decoy_links: How many each decoy walk reached, in the order of the
        offsets.
    :param min_links: The floor :attr:`significant` reads.
    :param min_enrichment: The enrichment floor it reads.
    :param proposals: The walk's proposals, by target key.
    """

    unit: str
    family: str
    n_links: int
    decoy_links: tuple[int, ...]
    min_links: int = MIN_LINKS
    min_enrichment: float = MIN_ENRICHMENT
    proposals: dict[str, Proposal] = field(default_factory=dict, compare=False)

    @property
    def decoy_mean(self) -> float:
        """The decoys' mean: what chance reaches at this unit."""
        if not self.decoy_links:
            return 0.0
        return sum(self.decoy_links) / len(self.decoy_links)

    @property
    def enrichment(self) -> float | None:
        """The walk's proposals over the decoys' mean; None where the walk
        reached nothing, and infinite where only the decoys did not."""
        if not self.n_links:
            return None
        if self.decoy_mean == 0:
            return float("inf")
        return self.n_links / self.decoy_mean

    @property
    def fdr(self) -> float | None:
        """The share of the proposals chance would have made: the decoys'
        mean over the proposals, at most 1; None where there are none."""
        if not self.n_links:
            return None
        return min(1.0, self.decoy_mean / self.n_links)

    @property
    def significant(self) -> bool:
        """Whether the family is committed: enough proposals, well enough
        above chance."""
        enrichment = self.enrichment
        return (
            self.n_links >= self.min_links
            and enrichment is not None
            and enrichment >= self.min_enrichment
        )

    def to_json(self) -> dict:
        """The record a batch run keeps of the unit."""
        enrichment = self.enrichment
        fdr = self.fdr
        return {
            "unit": self.unit,
            "family": self.family,
            "n_links": self.n_links,
            "decoy_links": list(self.decoy_links),
            "decoy_mean": round(self.decoy_mean, 3),
            "enrichment": (
                None
                if enrichment is None
                else ("inf" if enrichment == float("inf") else round(enrichment, 3))
            ),
            "fdr": None if fdr is None else round(fdr, 4),
            "significant": self.significant,
        }


def series_evidence(
    sources: Sequence[SeriesSource],
    targets: Sequence[SeriesTarget],
    unit: SeriesUnit,
    *,
    tolerance_ppm: float,
    decoy_offsets_da: Sequence[float] = DECOY_OFFSETS_DA,
    max_depth: int = MAX_DEPTH,
    labelled: bool = False,
    min_links: int = MIN_LINKS,
    min_enrichment: float = MIN_ENRICHMENT,
) -> SeriesEvidence:
    """Walk a unit and each of its decoys over the same anchors.

    :param sources: The assigned anchors.
    :param targets: The anchors the walk may reach.
    :param unit: The unit.
    :param tolerance_ppm: The walk's window.
    :param decoy_offsets_da: The decoy shifts.
    :param max_depth: The walk's depth.
    :param labelled: Whether the source is a labelled nitrate.
    :param min_links: The proposal floor.
    :param min_enrichment: The enrichment floor.
    :return: The unit's evidence, its proposals included.
    """

    def walk(offset: float) -> dict[str, Proposal]:
        return walk_series(
            sources,
            targets,
            unit,
            tolerance_ppm=tolerance_ppm,
            offset_da=offset,
            max_depth=max_depth,
            labelled=labelled,
        )

    proposals = walk(0.0)
    return SeriesEvidence(
        unit=unit.formula,
        family=unit.family,
        n_links=len(proposals),
        decoy_links=tuple(len(walk(offset)) for offset in decoy_offsets_da),
        min_links=min_links,
        min_enrichment=min_enrichment,
        proposals=proposals,
    )


def committed_proposals(
    evidence: Iterable[SeriesEvidence],
) -> dict[str, tuple[Proposal, tuple[str, ...]]]:
    """One proposal per target, over the units whose families are committed.

    A target two committed units reach with the same formula is one proposal
    two series agree on, and keeps both units. Two formulas for one target are
    settled by the number of units naming each, then the shorter distance from
    an assigned anchor, then the nearer exact mass, then the formula, so the
    order the units were walked in decides nothing.

    :param evidence: Every unit's evidence.
    :return: Target key -> the proposal it takes and the units that named its
        formula.
    """
    by_target: dict[str, dict[str, list[Proposal]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for unit_evidence in evidence:
        if not unit_evidence.significant:
            continue
        for target, proposal in unit_evidence.proposals.items():
            by_target[target][proposal.formula].append(proposal)
    chosen: dict[str, tuple[Proposal, tuple[str, ...]]] = {}
    for target, by_formula in by_target.items():

        def rank(item: tuple[str, list[Proposal]]) -> tuple:
            formula, proposals = item
            nearest = min(proposals, key=lambda p: (p.depth, abs(p.ppm), p.unit))
            return (-len(proposals), nearest.depth, abs(nearest.ppm), formula)

        formula, proposals = min(by_formula.items(), key=rank)
        best = min(proposals, key=lambda p: (p.depth, abs(p.ppm), p.unit))
        chosen[target] = (best, tuple(sorted({p.unit for p in proposals})))
    return chosen
