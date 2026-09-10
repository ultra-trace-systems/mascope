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

- ``carbon_cluster`` - DBE per carbon at or above 1. A carbon skeleton that
  unsaturated is graphitic: C60 reads 1.02, C10H2 reads 1.00, while benzene
  reads 0.67 and coronene 0.79, so aromatics are nowhere near it. The DBE is
  :func:`heuristic_filter.effective_counts`', which counts the halogens as
  hydrogen; that is what keeps a perfluorinated chain out (C9HF17O2 reads 0.11)
  without a fluorine exemption, and it is the reason not to add one - a real
  fluorinated cluster should still be named.

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

A formula can carry more than one; :func:`implausible_signatures` returns them
all and :func:`implausible_signature` the first, in the fixed order of
:data:`SIGNATURES` so two callers agree on which one a row is named by.
"""

from __future__ import annotations

from mascope_tools.composition.heuristic_filter import (
    effective_counts,
    element_counts,
)


CARBON_CLUSTER = "carbon_cluster"
OXYGEN_LATTICE = "oxygen_lattice"
CARBON_FREE = "carbon_free"

#: Every signature, in the order a formula carrying more than one is named by.
SIGNATURES = (CARBON_CLUSTER, OXYGEN_LATTICE, CARBON_FREE)

#: DBE per carbon-equivalent at or above which a skeleton is a carbon cluster.
CARBON_CLUSTER_DBE_PER_CARBON = 1.0

#: Oxygens per carbon above which a formula is oxygen-rich for its backbone.
OXYGEN_LATTICE_RATIO = 1.3

#: ...and the absolute oxygen count it takes for that ratio to stop describing
#: a small organic acid. Five, because four is malonic acid: on the assignment
#: gate's 43 samples the oxygen-rich formulas the reference confirms are almost
#: all malonic acid (O4), glycolic acid (O3), formic acid (O2) and oxalic acid
#: (O4), and raising the floor above five spares nothing further while leaving
#: real lattice fits above it. Measured rather than chosen: at O >= 4 the rule
#: takes 260 committed rows of which the reference confirms 35, at O >= 5 it
#: takes 212 and the confirmed rows fall to 3, and it stays at 3 all the way to
#: O >= 9.
OXYGEN_LATTICE_MIN = 5

#: The carbon-free neutrals a chemical-ionization source genuinely presents:
#: the reagent acids and halides themselves, their dimers and their common
#: oxidation products. Anything else with no carbon at all, arrived at by an
#: untargeted mass search, is a coincidence of the grid rather than a species.
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
    carbon, _hydrogen, dbe = effective_counts(counts)
    oxygen = counts.get("O", 0)
    found = []
    if carbon and _ratio(dbe, carbon) >= CARBON_CLUSTER_DBE_PER_CARBON:
        found.append(CARBON_CLUSTER)
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
