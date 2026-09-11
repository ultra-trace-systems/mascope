"""Named signatures of a formula that fits a mass and not a chemistry.

:func:`heuristic_filter.formula_plausibility` grades every formula on the Seven
Golden Rules and the engine multiplies its fit by that grade. This module is the
other half: a small set of formula shapes that are not merely *less* plausible
but are the recognisable products of a mass search rather than of a source. Each
one is named, so a row demoted on it can say which shape it has, and each is a
pure function of the neutral formula - no spectrum, no run, no instrument.

They are separate from the graded score because they are used differently. The
score competes candidates against each other, where a soft factor is right; a
signature answers a question about one committed formula on its own, where the
only useful answer is a name and a reason. A signature never picks a winner and
never rescues one - it is read after the run has already committed, by the
tiering, and it can only cost a row its tier.

What each one is, and what it is not:

- ``oxygen_lattice`` - more than 1.3 oxygens per carbon AND at least
  ``OXYGEN_LATTICE_MIN`` oxygens. The ratio alone is ordinary atmospheric
  chemistry: malonic acid is 1.33, glycolic 1.5, formic 2.0, and a rule keyed
  on it would take the small organic acids a CIMS source is built to see. It is
  the ratio TOGETHER with an absolute count that stops being chemistry and
  starts being a mass fit absorbing an error into oxygens.

- ``carbon_free`` - no carbon and not one of the small inorganics these sources
  actually make (:data:`CARBON_FREE_ALLOWLIST`). Deliberately narrow: a curated
  identity is an authored claim rather than a mass fit, so this is for what an
  untargeted search writes, and a caller that holds a curated row should not ask.
  That holds for every signature here and not only this one - each names what a
  mass search produces - and the tiering applies it to all of them.

  On the assignment gate that leaves it with nothing to name, and the reason is
  worth stating rather than reading as a clean result: every resolved grid there
  floors carbon at one, so no untargeted row can BE carbon-free, and every
  carbon-free row committed on those 43 samples is curated - nitric and iodic
  acid, sulfuric acid, ammonia, the bromine of the dibromide reagent ion, and
  trisulfur (written HS3) six times at assigned tier. Asked of those rows anyway,
  the signature took seven, and the reference confirms none of them - it commits
  nothing on the trisulfur peaks and reads the dibromide one as its reagent - so
  what the exemption spares is unmeasured rather than wrong, and it is taken on
  the principle. What was measured is that nothing untargeted reaches the
  signature, not that the allowlist is right.

A formula can carry more than one; :func:`implausible_signatures` returns them
all and :func:`implausible_signature` the first, in the fixed order of
:data:`SIGNATURES` so two callers agree on which one a row is named by.

The carbon cluster that is not here
-----------------------------------

A third signature was written and withdrawn on the measurement. "DBE per carbon
at or above 1" reads like a statement about a graphitic skeleton - C60 is 1.02 -
but the arithmetic says otherwise: with the DBE ``effective_counts`` computes,
``DBE/C >= 1`` reduces to ``H <= 2 + N``, which is a statement about hydrogen
count and not about size. On the assignment gate it took formic acid, oxalic
acid, glyoxylic acid and a nitrogen heterocycle the reference confirms - 46 rows
of which 8 were right - and no carbon cluster, because there were none to take.
Requiring the unsaturation to exceed what the heteroatoms can carry
(``DBE > O + N``) narrows it to three rows on 43 samples, one of them still a
confirmed aromatic.

What keeps a carbon cluster out of a run is the chemistry CONTEXT, and not this
module or the heuristic filter's own ratio band. The filter GRADES a formula and
rejects none on H/C - C60, C24 and C10H2 pass its rules at plausibility 1.0 -
while ``ambient-air`` caps DBE/C at 0.75 and floors H/C at 0.7, and ``uronium``
at 1.1 and 0.4. Those windows apply from ``config.CONTEXT_RATIO_MIN_CARBON``
carbons up, which is exactly the population this signature misjudged: the C1 and
C2 acids it took sit BELOW that floor, where the context deliberately says
nothing, and above it the context has already refused what this would have
caught.

One thing that leaves open, stated rather than closed here: a run under context
``none`` has no such window, so a cluster is committable there - and this
signature would not have closed that gap either, since the threshold that
catches the cluster takes the acids with it.
"""

from __future__ import annotations

from mascope_tools.composition.heuristic_filter import (
    effective_counts,
    element_counts,
)


OXYGEN_LATTICE = "oxygen_lattice"
CARBON_FREE = "carbon_free"

#: Every signature, in the order a formula carrying more than one is named by.
SIGNATURES = (OXYGEN_LATTICE, CARBON_FREE)

#: Oxygens per carbon above which a formula is oxygen-rich for its backbone.
OXYGEN_LATTICE_RATIO = 1.3

#: ...and the absolute oxygen count it takes for that ratio to stop describing
#: a small organic acid. Five, because four is malonic acid: on the assignment
#: gate's 43 samples the oxygen-rich formulas the reference confirms are almost
#: all malonic acid (O4), glycolic acid (O3), formic acid (O2) and oxalic acid
#: (O4), and raising the floor above five spares nothing further. What is
#: measured is what the floor SPARES: at O >= 4 the signature reaches 260
#: committed rows of which the reference confirms 35, at O >= 5 it reaches 212
#: and the confirmed rows fall to 3, and it stays at 3 all the way to O >= 9.
#:
#: What it TAKES is not measured to the same standard, and the difference
#: matters. Of those 212 rows the reference contradicts 17, splits 3 and is
#: SILENT on 189 - and 174 of the 212 are on the two TOF sets, where it is
#: silent by construction. On the five Orbitrap sets the signature reaches 28
#: rows: 3 confirmed, 3 contradicted, 2 split and 20 unjudged. So on the
#: instruments the gate can judge it is three right and three wrong, not three
#: against two hundred; a tiering weighing it should read it as a guard whose
#: cost is known and whose benefit is mostly unmeasured.
OXYGEN_LATTICE_MIN = 5

#: The carbon-free neutrals a chemical-ionization source genuinely presents:
#: the reagent acids and halides themselves, their dimers and their common
#: oxidation products. Anything else with no carbon at all, arrived at by an
#: untargeted mass search, is a coincidence of the grid rather than a species.
#:
#: The bromine atom is not here, although the dibromide reagent ion reaches a
#: committed row as one. That row is a curated target's, which no signature
#: judges; an untargeted grid cannot write it, since every grid floors carbon at
#: one; and a bare halogen atom is a radical the tiering's odd-electron rule
#: caps on its own. What it shows is a reagent ion matched as an analyte, which
#: is the curated library's question and not this list's.
CARBON_FREE_ALLOWLIST = frozenset(
    {
        "H2O",
        "H2O2",
        "NH3",
        "HNO2",
        "HNO3",
        "H2N2O6",  # the nitric acid dimer, which a nitrate source makes
        "N2O5",
        "HCl",
        "HBr",
        "Br2",
        "HI",
        "I2",
        "HIO3",
        "H2SO4",
        "SO2",
        "H2S",
        "O3",
    }
)


def _ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def _counts_key(counts: dict[str, int]) -> tuple[tuple[str, int], ...]:
    """A formula's composition, spelled one way.

    The allowlist is matched on counts and not on the string, because the same
    neutral reaches this module written both ways: a curated row spells nitric
    acid ``H1N1O3`` and the untargeted stage ``HNO3``, and an allowlist keyed on
    one of those silently does not hold for the other.
    """
    return tuple(sorted((element, n) for element, n in counts.items() if n))


#: The allowlist as compositions, so the spelling of a formula cannot decide it.
_ALLOWED_CARBON_FREE = frozenset(
    _counts_key(element_counts(formula) or {}) for formula in CARBON_FREE_ALLOWLIST
)


def implausible_signatures(formula: str | None) -> tuple[str, ...]:
    """Every implausibility signature a neutral formula carries.

    Fails open, like every other chemistry rule here: a formula that cannot be
    parsed carries no signature, so an unreadable formula is never demoted on a
    test that could not run.

    :param formula: A neutral formula in Hill order.
    :return: The signatures it carries, in :data:`SIGNATURES` order; empty when
        it carries none.
    """
    counts = element_counts(formula or "")
    if not counts:
        return ()
    carbon, _hydrogen, _dbe = effective_counts(counts)
    oxygen = counts.get("O", 0)
    found = []
    if (
        carbon
        and oxygen >= OXYGEN_LATTICE_MIN
        and _ratio(oxygen, carbon) > OXYGEN_LATTICE_RATIO
    ):
        found.append(OXYGEN_LATTICE)
    if not carbon and _counts_key(counts) not in _ALLOWED_CARBON_FREE:
        found.append(CARBON_FREE)
    return tuple(found)


def implausible_signature(formula: str | None) -> str | None:
    """The one signature a formula is named by, or None when it carries none.

    :param formula: A neutral formula in Hill order.
    :return: The first of :func:`implausible_signatures`.
    """
    signatures = implausible_signatures(formula)
    return signatures[0] if signatures else None
