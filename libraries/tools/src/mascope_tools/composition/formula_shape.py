"""The formula-shape criteria: one table of hard bound, graded band and tier cap.

The same chemistry used to be checked several times at different thresholds:
a hard reject in the search's filter, a graded factor in the plausibility and a
tier cap in the tiering pass, each with numbers and exemptions of its own
(review MAS-974, finding 5). Decision MAS-979 (the plan owner, 2026-11-22) puts
every criterion in this one table, and every reader reads it:

- **Hard bounds** only where Senior's rules or valence forbid the formula
  (``heuristic_filter.rule_senior``). Every ratio rule is graded or a tier cap,
  so no ratio row below carries a hard bound, and the search's raw ratio rule
  (``heuristic_filter.rule_element_ratio``) refuses nothing by default. A caller
  may still hand it bounds of its own.
- **Graded bands** are Kind & Fiehn 2007 (BMC Bioinformatics 8:105), Tables 2
  and 3, as ``heuristic_filter.formula_plausibility`` reads them: full credit in
  the common range, half at the extended edge, then down to
  :data:`PLAUSIBILITY_FLOOR`.
- **Tier caps** are what the tiering pass asks of a committed row: the context
  priors (``profiles.priors_met``), the implausibility signatures
  (``implausibility``), the carbon skeleton of an organonitrate, and the three
  caps on the ion.

The exemption rule is the second half of the decision, revised the same day
after the demo's uronium targets were read against their spectra (MAS-885):
five of eight user-authored targets named the wrong neutral. A reference list
comes from peer-reviewed work and carries authority; the target library is the
user's own and can hold anything. So a cap on the compound's composition is not
asked of a row a reference list names, a cap on the ion is asked of every row,
and a row only the target library names is exempt from nothing
(:func:`exempt`).

``docs/dev/assignment_confidence.md`` (L1) holds the same table in prose.
"""

from __future__ import annotations

from dataclasses import dataclass


#: Bumped whenever a row of the table moves: a hard bound, a band, a cap's
#: threshold, or which half of the exemption rule a cap falls under. Recorded
#: on the run beside the tiering's rule version, for the same reason: a tier is
#: only comparable across runs together with the rules that produced it.
FORMULA_SHAPE_VERSION = 1

#: The floor of every graded factor and of their product: the plausibility a
#: wildly out-of-range, but not structurally impossible, formula decays to.
#: Non-zero, so a grade is never a reject - 0.0 is Senior's alone, a graph no
#: molecule has - and it caps the chemistry's authority at 10:1 against the fit
#: (evidence = fit x plausibility). The identification level reads a row under
#: it as implausible (5b).
PLAUSIBILITY_FLOOR = 0.1


@dataclass(frozen=True)
class RatioCriterion:
    """One element's count per carbon.

    :param element: The numerator, X of X/C.
    :param hard: The ratio range outside which the search refuses a formula, or
        None where only Senior's rules and valence bound it.
    :param common: The range ~99.7 % of known formulas sit in (Kind & Fiehn
        2007, Table 2): full credit inside. None where the element is not
        graded.
    :param extended: The range ~99.99 % sit in: half credit at its edge.
    """

    element: str
    hard: tuple[float, float] | None = None
    common: tuple[float, float] | None = None
    extended: tuple[float, float] | None = None


#: Every element's ratio to carbon, keyed by the element. A ratio is read only
#: where both the element and carbon are present: the absence of an element is
#: not an unusual ratio. Only H/C has a non-zero minimum.
#:
#: The hard bounds that went with decision MAS-979 were the Wiley database's
#: (``config.DEFAULT_ELEMENTAL_RATIO_RANGE`` until then): H/C 0.1 to 6, N/C and
#: O/C 2, S/C 0.1, P/C 0.05, and every halogen 0.05 per carbon, which refused a
#: chlorine or bromine formula under C20 and a sulfur one under C10 (#3246),
#: and which the halogen-twin re-read opened to 0.8 for its own search.
RATIO_CRITERIA: dict[str, RatioCriterion] = {
    criterion.element: criterion
    for criterion in (
        RatioCriterion("H", common=(0.2, 3.1), extended=(0.1, 6.0)),
        RatioCriterion("F", common=(0.0, 1.5), extended=(0.0, 6.0)),
        RatioCriterion("Cl", common=(0.0, 0.8), extended=(0.0, 2.0)),
        RatioCriterion("Br", common=(0.0, 0.8), extended=(0.0, 2.0)),
        RatioCriterion("I"),
        RatioCriterion("N", common=(0.0, 1.3), extended=(0.0, 4.0)),
        RatioCriterion("O", common=(0.0, 1.2), extended=(0.0, 3.0)),
        RatioCriterion("P", common=(0.0, 0.3), extended=(0.0, 2.0)),
        RatioCriterion("S", common=(0.0, 0.8), extended=(0.0, 3.0)),
        RatioCriterion("Si", common=(0.0, 0.5), extended=(0.0, 1.0)),
    )
}


def hard_ratio_bounds() -> dict[str, tuple[float, float]]:
    """The ratio ranges the search refuses outside, keyed ``"X/C"``.

    Read off :data:`RATIO_CRITERIA` when called, so a table that moves moves
    the filter's default (``models.HeuristicFilterConfig``) with it. Empty
    while every ratio is graded or capped, which is the decision.
    """
    return {
        f"{criterion.element}/C": criterion.hard
        for criterion in RATIO_CRITERIA.values()
        if criterion.hard is not None
    }


def graded_ratio_bands() -> dict[str, tuple[float, float, float, float]]:
    """``(common_min, common_max, extended_min, extended_max)`` per element.

    The graded elements only, keyed by the element, as the plausibility reads
    them.
    """
    return {
        criterion.element: (*criterion.common, *criterion.extended)
        for criterion in RATIO_CRITERIA.values()
        if criterion.common is not None and criterion.extended is not None
    }


#: The heteroatom co-occurrence restrictions (Kind & Fiehn 2007, Table 3,
#: Rule 6), graded: when every element of a trigger is present above its count,
#: none of the capped elements is expected above its cap. Each entry is
#: (trigger, caps).
CO_OCCURRENCE_CAPS: tuple[tuple[dict[str, int], dict[str, int]], ...] = (
    ({"N": 1, "O": 1, "P": 1, "S": 1}, {"N": 10, "O": 20, "P": 4, "S": 3}),
    ({"N": 3, "O": 3, "P": 3}, {"N": 11, "O": 22, "P": 6}),
    ({"O": 1, "P": 1, "S": 1}, {"O": 14, "P": 3, "S": 3}),
    ({"P": 1, "S": 1, "N": 1}, {"P": 3, "S": 3, "N": 4}),
    ({"N": 6, "O": 6, "S": 6}, {"N": 19, "O": 14, "S": 8}),
)


# --- Tier caps on the compound's composition ---------------------------------

#: Sulfur's cap, the context's sulfur prior: a neutral with at least this many
#: sulfur atoms is held at candidate until something beyond its mass fit saw it
#: (``profiles.priors_met``, ``tiering.context_prior_reason``).
SULFUR_PRIOR_MIN = 1

#: Nitrogen's cap, the context's odd-nitrogen prior: at least this many
#: nitrogens...
ODD_NITROGEN_MIN = 2
#: ...and fewer oxygens per nitrogen than this, the nitro group's own count.
ODD_NITROGEN_OXYGEN_PER_N = 2

#: Oxygen's cap, the oxygen-lattice signature (``implausibility``): more oxygens
#: per carbon than this...
OXYGEN_LATTICE_RATIO = 1.3
#: ...and at least this many oxygens, so the small organic acids are spared.
OXYGEN_LATTICE_MIN = 5


# --- The chemistry context's ratio windows -----------------------------------

#: How far outside a context's ratio window the factor falls, as a fraction of
#: the window's width: half credit this far past its edge, the floor twice as
#: far, the shape of the element-ratio bands' common and extended edges. The
#: windows used to refuse a formula outside them (#3188); they grade it now,
#: and refuse only where a context opts in (``ChemistryContext.windows_gate``).
CONTEXT_WINDOW_SPAN = 0.25


# --- Carbon-free formulas ----------------------------------------------------

#: The carbon floor a reagent profile's search grid gets
#: (``profiles.ReagentProfile.min_carbon``). Open: a carbon-free formula is
#: graded (the ratio grades fail open on it, having no carbon to divide by) and
#: capped by the carbon-free signature, not refused by the grid.
GRID_CARBON_FLOOR = 0


# --- The exemption rule ------------------------------------------------------

#: A cap on the compound's composition: a reference list's name for the
#: compound is the authority it doubts, so a row a list names is not asked.
HALF_COMPOSITION = "composition"

#: A cap on the ion: a list names a compound, not the channel it is seen
#: through, so every row is asked, whoever named it.
HALF_ION = "ion"

#: Which half of the rule each tier cap on the formula's shape falls under, by
#: the name the tiering pass records it under.
RULE_HALF: dict[str, str] = {
    "oxygen_lattice": HALF_COMPOSITION,
    "carbon_free": HALF_COMPOSITION,
    "context_prior": HALF_COMPOSITION,
    "nox_skeleton": HALF_COMPOSITION,
    "odd_electron": HALF_ION,
    "polyhalide_cluster": HALF_ION,
    "oxygen_free_cluster": HALF_ION,
    # The cross-channel pass's same-ion rule: which reading of the ion, which
    # neutral through which channel. A target used to settle it; neither a
    # list nor a target decides between readings of one ion (MAS-885).
    "ambiguous_nitrogen": HALF_ION,
    "ambiguous_adduct": HALF_ION,
}


def exempt(rule: str, *, list_named: bool) -> bool:
    """Whether a row is spared a formula-shape cap.

    The one exemption rule the tiering pass and the cross-channel election
    read. A row a reference list names is spared the caps on its compound's
    composition; no row is spared a cap on its ion; a row only the target
    library names is spared nothing. A rule outside :data:`RULE_HALF` is no
    formula-shape cap, and this spares nothing of it.

    :param rule: The cap's name, as the tiering pass records it.
    :param list_named: Whether a reference list names the row's neutral.
    :return: True where the row is not asked.
    """
    return list_named and RULE_HALF.get(rule) == HALF_COMPOSITION
