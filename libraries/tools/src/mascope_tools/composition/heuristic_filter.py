"""
Based on 7 Golden Rules by https://bmcbioinformatics.biomedcentral.com/articles/10.1186/1471-2105-8-105
"""

from functools import lru_cache
from typing import Any

import numpy as np
import polars as pl
from IsoSpecPy import IsoDistribution, IsoThreshold, PeriodicTbl
from pyteomics.mass import Composition
from scipy.spatial.distance import cosine

from mascope_tools.composition.config import (
    ELECTRON_MASS,
    ISOTOPE_ABUNDANCE_THRESHOLD,
    ISOTOPE_MATCHING_INTENSITY_TOLERANCE,
    ISOTOPE_MATCHING_MZ_TOLERANCE_PPM,
)
from mascope_tools.composition.custom_elements import CUSTOM_ELEMENTS
from mascope_tools.composition.models import HeuristicFilterConfig
from mascope_tools.composition.utils import (
    normalize_formula_with_isotopes,
    to_pyteomics,
)


# Limit isotopic matching to the most plausible candidates
ISOTOPE_CANDIDATE_LIMIT = 64

# --- Labelled-reagent custom elements ('^X' notation) ------------------------
# A labelled reagent atom is not 100% pure; e.g. 15N-nitrate is ~98% 15N / 2% 14N.
# We model it as a custom element '^X' whose isotope abundances are the labelled
# distribution, so predict_isotopes yields the heavy base AND the small light
# satellite. `purity` is the heaviest-isotope fraction (e.g. 0.98 for 15N).
# The element definitions come from the shared registry (custom_elements.py) --
# the single source of truth also used by the Mascope backend, so no molmass
# dependency and no duplicated isotope data.
LABELLED_REAGENT_PURITY = CUSTOM_ELEMENTS["^N"].default_purity
# symbol -> (regular_symbol, [(mass, mass_number), ... lightest first])
_CUSTOM_ELEMENT_DATA = {
    sym: (ce.base_element, list(ce.isotopes)) for sym, ce in CUSTOM_ELEMENTS.items()
}


def rule_element_ratio(
    candidates: pl.DataFrame, heuristics_config: HeuristicFilterConfig, **kwargs
) -> tuple[pl.Series, list[str]]:
    log_messages = []

    # Early return if no candidates
    if candidates.is_empty():
        return pl.Series([], dtype=pl.Boolean), log_messages

    formulas = candidates.get_column("formula").to_list()

    # Parse all formulas once and convert to a structured format
    counts_list = [
        Composition(formula=normalize_formula_with_isotopes(f)) for f in formulas
    ]

    # Get all unique elements across all formulas
    all_elements = set()
    for counts in counts_list:
        all_elements.update(counts.keys())
    all_elements = sorted(all_elements)

    # Create a matrix where rows are formulas and columns are elements
    n_formulas = len(counts_list)
    n_elements = len(all_elements)
    element_matrix = np.zeros((n_formulas, n_elements), dtype=np.int32)

    # Fill the matrix
    element_to_idx = {elem: idx for idx, elem in enumerate(all_elements)}
    for i, counts in enumerate(counts_list):
        for elem, count in counts.items():
            element_matrix[i, element_to_idx[elem]] = count

    # Determine which formulas have carbon
    carbon_idx = element_to_idx.get("C")
    has_carbon = carbon_idx is not None and element_matrix[:, carbon_idx] > 0

    # Initialize mask - all True initially
    final_mask = np.ones(n_formulas, dtype=bool)

    def apply_ratio_rules_vectorized(ratio_range, apply_to_mask):
        """Apply ratio rules using vectorized operations"""
        if not ratio_range or not np.any(apply_to_mask):
            return np.ones(n_formulas, dtype=bool)

        rule_mask = np.ones(n_formulas, dtype=bool)

        for ratio, (min_val, max_val) in ratio_range.items():
            num, denom = ratio.split("/")

            # Get indices for numerator and denominator elements
            num_idx = element_to_idx.get(num)
            denom_idx = element_to_idx.get(denom)

            if num_idx is None or denom_idx is None:
                continue

            # Get counts for numerator and denominator
            num_counts = element_matrix[:, num_idx]
            denom_counts = element_matrix[:, denom_idx]

            # Only apply rule where both elements exist and denominator > 0
            has_both_elements = (num_counts > 0) & (denom_counts > 0)
            applicable_mask = apply_to_mask & has_both_elements

            if not np.any(applicable_mask):
                continue

            # Calculate ratios only where applicable (avoid division by zero)
            ratios = np.full(n_formulas, np.inf)
            ratios[applicable_mask] = (
                num_counts[applicable_mask] / denom_counts[applicable_mask]
            )

            # Check if ratios are within bounds
            ratio_valid = (ratios >= min_val) & (ratios <= max_val)

            # Update rule mask: pass if not applicable OR ratio is valid
            rule_mask &= np.logical_not(applicable_mask) | ratio_valid

        return rule_mask

    # Apply carbon-specific ratios to formulas with carbon
    if np.any(has_carbon) and heuristics_config.carbon_element_ratio_range:
        carbon_mask = apply_ratio_rules_vectorized(
            heuristics_config.carbon_element_ratio_range, has_carbon
        )
        final_mask &= carbon_mask

    # Apply non-carbon ratios to formulas without carbon
    no_carbon = np.logical_not(has_carbon)
    if np.any(no_carbon) and heuristics_config.non_carbon_element_ratio_range:
        non_carbon_mask = apply_ratio_rules_vectorized(
            heuristics_config.non_carbon_element_ratio_range, no_carbon
        )
        final_mask &= non_carbon_mask

    return pl.Series(final_mask), log_messages


def rule_valence(candidates: pl.DataFrame, **kwargs) -> tuple[pl.Series, list[str]]:
    """Valence rules (even/odd electron)."""
    # TODO: requires charge and electron count info
    mask = pl.Series([True] * candidates.height)
    log_messages = []
    return mask, log_messages  # Placeholder, always returns True


# Valence states per element for the RDBE / Senior structural check (Golden Rule 2:
# Lewis & Senior), lowest first: ``states[0]`` is the base (classic organic) valence and
# the last entry is the highest state the element can actually reach. The Senior test asks
# whether there EXISTS a valence assignment permitting a connected molecular graph, and the
# inequality in `_senior_feasible` is monotone increasing in every valence, so the exact
# optimum of that existential is simply "every element at its highest reachable state" --
# no search required. Only the genuinely hypervalent elements carry several states
# (S 2/4/6, P 3/5, Cl/Br/I 1/3/5/7; Kind & Fiehn 2007 Rule 2 and common practice).
#
# N stays TRIVALENT on purpose: it cannot exceed an octet, the "N(V)" of nitro/nitrate
# drawings is a formalism whose RDBE is already correct at v=3, and the classic max-H
# bound 2c+2+n that v=3 encodes is never exceeded by a real neutral. Promoting it would
# make over-saturated CHNO formulas (the demo's C6H17NO4 data error) look feasible.
#
# Any element OUTSIDE this table makes the check fail-open (the candidate is never
# rejected on valence grounds) so unusual chemistry is never wrongly cut.
_VALENCE_STATES: dict[str, tuple[int, ...]] = {
    "H": (1,),
    "D": (1,),
    "T": (1,),
    "B": (3,),
    "C": (4,),
    "N": (3,),
    "O": (2,),
    "F": (1,),
    "Si": (4,),
    "P": (3, 5),
    "S": (2, 4, 6),
    "Cl": (1, 3, 5, 7),
    "Br": (1, 3, 5, 7),
    "I": (1, 3, 5, 7),
}

# A hypervalent centre is only realised with electronegative ligands: SF6, PF5, PCl5 and
# H2SO4 exist, SH6 and PH6 do not. An element may therefore only climb its ladder when the
# formula supplies enough such ligand atoms to accept the extra bonds. Without this gate an
# ungated max-valence table calls CH6S, SH4, C2H7Cl and C5H14S feasible -- real
# over-saturation rejections lost; with it those four stay rejected while
# SF6/SF4/PF5/PCl5/IF5/IF7/S2F10 pass. The cost, stated plainly: heavily O-/halogen-
# substituted compositions that used to be cut are now admitted (e.g. CHF3Cl), ~39% of the
# previously-rejected compositions on a CHNOPS grid, dominated by the sulfate/phosphate
# families we WANT back for atmospheric CIMS. Pure CHNO over-saturation is untouched,
# because C/H/N/O are single-state.
_HYPERVALENT_LIGANDS = ("O", "F", "Cl", "Br", "I", "N")


def _effective_valences(counts: dict[str, int]) -> dict[str, int]:
    """Highest valence state each element can actually reach in this formula."""
    valences: dict[str, int] = {}
    for element, n in counts.items():
        states = _VALENCE_STATES[element]
        base = states[0]
        if len(states) == 1:
            valences[element] = base
            continue
        # An atom cannot be its own ligand. The pool is deliberately NOT split between
        # several hypervalent centres: making them compete would only make the test
        # stricter, and this layer fails open by design.
        pool = sum(counts.get(el, 0) for el in _HYPERVALENT_LIGANDS if el != element)
        valences[element] = max(v for v in states if (v - base) * n <= pool)
    return valences


def _senior_feasible(counts: dict[str, int] | None) -> bool:
    """Whether SOME valence assignment admits a connected molecular graph on ``counts``.

    ``rule_senior`` (the boolean gate) and ``senior_plausibility`` (the graded factor)
    both call this, so the hard cut and the plausibility verdict cannot drift apart.

    RDBE >= 0 and Senior's connectivity condition are the SAME inequality, not two rules:
    ``2*RDBE = 2 + sum_i n_i (v_i - 2) = 2 + valence_sum - 2*n_atoms``, so ``2*RDBE >= 0``
    is exactly ``valence_sum >= 2*(n_atoms - 1)``. Only the connectivity form is written.

    Senior's remaining two conditions are deliberately NOT implemented, and should not be
    "completed" later: the parity condition rejects odd-electron radicals, which can be
    genuine (APCI/APPI), and ``valence_sum >= 2*max_valence`` rejects carbene-like species
    and, under a multi-state table, would need a real search over assignments (it rejects
    SF2 outright if S is forced to 6). Both are anti-conservative here.

    Fails open (True) on an empty formula or any element outside ``_VALENCE_STATES``.
    """
    if not counts or any(el not in _VALENCE_STATES for el in counts):
        return True  # fail open on unknown elements / empty
    valences = _effective_valences(counts)
    n_atoms = sum(counts.values())
    valence_sum = sum(valences[el] * n for el, n in counts.items())
    return n_atoms <= 1 or valence_sum >= 2 * (n_atoms - 1)


def rule_senior(candidates: pl.DataFrame, **kwargs) -> tuple[pl.Series, list[str]]:
    """Lewis & Senior structural feasibility (Seven Golden Rules, Rule 2).

    Rejects only a neutral formula that cannot correspond to ANY molecular graph: the sum
    of valences must reach ``2*(N_atoms - 1)`` so the atoms can form a single connected
    graph. That single inequality is equivalent to ``RDBE >= 0`` (over-saturation: more
    atoms than can be bonded); see `_senior_feasible`, which this rule shares with
    `senior_plausibility`. Valences come from the multi-state, ligand-gated model of
    ``_VALENCE_STATES``, so hypervalent species (SF6, PF5, H2SO4) are feasible while SH4
    and PH6 are not.

    Conservative / fail-open by design:
    - **Odd-electron (radical) formulas are NOT rejected.** A non-integer RDBE marks an
      open-shell species, which can be genuine (e.g. APCI/APPI radical ions); only the
      *impossible* (over-saturated) formulas are cut.
    - a formula containing any element outside ``_VALENCE_STATES`` (or that fails to parse)
      is never rejected here, so exotic compositions are never lost to this filter.

    Applies to NEUTRAL formulas only (as produced by ``find_compositions`` before
    ionization). See Kind & Fiehn 2007, BMC Bioinformatics 8:105 (Rule 2).

    Opt-in via ``HeuristicFilterConfig.use_senior``. This rule was previously a
    no-op placeholder, so applying it unconditionally would silently drop
    candidates the existing composition search used to return; callers that want
    the cut ask for it.
    """
    log_messages: list[str] = []
    if candidates.is_empty():
        return pl.Series([], dtype=pl.Boolean), log_messages

    heuristics_config = kwargs.get("heuristics_config") or HeuristicFilterConfig()
    if not heuristics_config.use_senior:
        # Disabled: pass everything through, preserving pre-Rule-2 behaviour.
        return pl.Series([True] * candidates.height, dtype=pl.Boolean), log_messages

    mask: list[bool] = []
    for formula in candidates.get_column("formula").to_list():
        try:
            counts = Composition(formula=normalize_formula_with_isotopes(formula))
            elems = {el: n for el, n in counts.items() if n}
        except Exception:
            mask.append(True)  # unparseable here -> defer, never reject
            continue
        mask.append(_senior_feasible(elems))
    return pl.Series(mask, dtype=pl.Boolean), log_messages


# ---------------------------------------------------------------------------
# Graded chemical plausibility (Seven Golden Rules, Phase 3 / P1).
#
# The rules above (`rule_element_ratio`, `rule_senior`) are BOOLEAN gates: a
# candidate either passes or is hard-cut. The confidence layer
# (docs/dev/assignment_confidence.md) needs the same chemistry expressed as a
# GRADED per-candidate plausibility in [0, 1] so it can *weigh* candidates rather
# than reject them -- a formula whose ratios sit in the common range is
# high-probability, one pushing into the distribution's tail is lower-probability,
# and an impossible structure is ~0.
#
# `chemical_plausibility` composes three independent factors (each in [0, 1]), by
# multiplication, per the confidence-layer design rule that every layer is a
# likelihood:
#   1. Senior/RDBE structural feasibility (Rule 2) -- 1.0 feasible, 0.0 impossible.
#   2. Element-ratio plausibility (Rules 4-5) -- graded across the paper's
#      common / extended / extreme ratio bands.
#   3. Heteroatom co-occurrence probability (Rule 6) -- graded against the
#      multi-element count restrictions.
#
# INVARIANT: the composite is 0.0 **iff** the neutral graph is provably impossible
# (`_senior_feasible` is False); otherwise it lies in [PLAUSIBILITY_FLOOR, 1.0]. The
# graded factors 2-3 multiply, and several of their sub-checks overlap, so without the
# floor an unusual-but-possible formula collapses orders of magnitude below any single
# factor's floor and becomes indistinguishable from an impossible one.
#
# Conservative / fail-open by design: any element outside the standard tables, a
# carbon-free formula (X/C ratios undefined), or an unparseable formula scores
# 1.0 for the affected factor -- unusual chemistry is never wrongly penalised.
# Numbers are taken verbatim from Kind & Fiehn 2007 (BMC Bioinformatics 8:105),
# Tables 2 (element ratios) and 3 (element-count restrictions).
# ---------------------------------------------------------------------------

# Floor for the ratio/Rule-6 tapers AND for their product: the plausibility a wildly
# out-of-range (but not structurally impossible) formula decays to. Non-zero so the graded
# score is never a hard reject -- that job belongs to the boolean rules / arbitration. It
# is a single knob, and it caps this layer's authority at 10:1 against the fit score
# (evidence = fit x plausibility), which is deliberate: the chemistry layer *grades*, it
# does not gate.
PLAUSIBILITY_FLOOR = 0.1

# Element/C ratio bands (Kind & Fiehn 2007, Table 2), keyed by the numerator
# element X (ratio = n_X / n_C). Each entry is
# (common_min, common_max, extended_min, extended_max): the common range covers
# ~99.7% of known formulas, the extended range ~99.99%. Only H/C has a meaningful
# non-zero minimum. A ratio is scored ONLY when both X and C are present, mirroring
# `rule_element_ratio` (absence of an element is not an "unusual ratio").
_RATIO_BANDS: dict[str, tuple[float, float, float, float]] = {
    "H": (0.2, 3.1, 0.1, 6.0),
    "F": (0.0, 1.5, 0.0, 6.0),
    "Cl": (0.0, 0.8, 0.0, 2.0),
    "Br": (0.0, 0.8, 0.0, 2.0),
    "N": (0.0, 1.3, 0.0, 4.0),
    "O": (0.0, 1.2, 0.0, 3.0),
    "P": (0.0, 0.3, 0.0, 2.0),
    "S": (0.0, 0.8, 0.0, 3.0),
    "Si": (0.0, 0.5, 0.0, 1.0),
}

# Multi-element count restrictions (Kind & Fiehn 2007, Table 3, Rule 6): when a
# combination of heteroatoms co-occurs ABOVE the trigger counts, none of them is
# expected to exceed the listed cap. Each check is (trigger, caps): trigger maps
# element -> strict lower bound that must ALL hold for the check to apply; caps
# maps element -> the count above which the formula becomes improbable.
_RULE6_CHECKS: list[tuple[dict[str, int], dict[str, int]]] = [
    ({"N": 1, "O": 1, "P": 1, "S": 1}, {"N": 10, "O": 20, "P": 4, "S": 3}),
    ({"N": 3, "O": 3, "P": 3}, {"N": 11, "O": 22, "P": 6}),
    ({"O": 1, "P": 1, "S": 1}, {"O": 14, "P": 3, "S": 3}),
    ({"P": 1, "S": 1, "N": 1}, {"P": 3, "S": 3, "N": 4}),
    ({"N": 6, "O": 6, "S": 6}, {"N": 19, "O": 14, "S": 8}),
]


def element_counts(formula: str) -> dict[str, int] | None:
    """Base-element counts for a neutral formula (isotopes folded into their base
    element, e.g. ``[13C]`` -> ``C``). Returns ``None`` if the formula cannot be
    parsed -- callers fail open (plausibility 1.0) on ``None``."""
    try:
        counts = Composition(formula=normalize_formula_with_isotopes(formula))
        return {el: n for el, n in counts.items() if n}
    except Exception:
        return None


def _taper(value: float, near: float, far: float) -> float:
    """Plausibility as ``value`` leaves a band. ``near`` is the band edge (score
    1.0) and ``far`` is the next edge (score 0.5); past ``far`` the score decays
    linearly to ``PLAUSIBILITY_FLOOR`` over one more ``|far - near|`` step. Works
    for both an upper edge (far > near) and a lower edge (far < near)."""
    span = abs(far - near)
    if span <= 0:
        return 1.0
    dist = abs(value - near)
    if (far >= near and value <= near) or (far < near and value >= near):
        return 1.0
    if dist <= span:  # between the near and far edges: 1.0 -> 0.5
        return 1.0 - 0.5 * (dist / span)
    # beyond the far edge: 0.5 -> floor over one more span, then flat
    frac = min(1.0, (dist - span) / span)
    return max(PLAUSIBILITY_FLOOR, 0.5 - (0.5 - PLAUSIBILITY_FLOOR) * frac)


def element_ratio_plausibility(counts: dict[str, int]) -> float:
    """Rules 4-5 as a graded likelihood in [0, 1] (Kind & Fiehn 2007, Table 2).

    Product over every X/C ratio (X present, C present) of a per-ratio score that
    is 1.0 inside the common range, tapers to 0.5 at the extended edge, then to
    ``PLAUSIBILITY_FLOOR`` beyond, with the product itself floored at
    ``PLAUSIBILITY_FLOOR`` -- several ratios in the tail otherwise multiply far below the
    per-ratio floor (C1H20O6N6S6 reaches 2e-4). Carbon-free formulas score 1.0 (X/C
    undefined -- fail open)."""
    n_c = counts.get("C", 0)
    if n_c <= 0:
        return 1.0
    score = 1.0
    for element, (c_min, c_max, e_min, e_max) in _RATIO_BANDS.items():
        n_x = counts.get(element, 0)
        if n_x <= 0:
            continue
        ratio = n_x / n_c
        upper = _taper(ratio, c_max, e_max)
        lower = _taper(ratio, c_min, e_min) if c_min > 0 else 1.0
        score *= min(upper, lower)
    return max(PLAUSIBILITY_FLOOR, score)


def heteroatom_probability_plausibility(counts: dict[str, int]) -> float:
    """Rule 6 as a graded likelihood in [0, 1] (Kind & Fiehn 2007, Table 3).

    The paper's checks are NESTED restatements of one restriction on the same heteroatoms
    -- {N,O,P,S}, {N,O,P}, {O,P,S}, {P,S,N} and {N,O,S} all trigger together on an
    NOPS-rich formula -- so multiplying every triggered cap penalises the same element
    three or four times over (a C20H20N12O25P7S4 reached 0.0038). Each element is
    therefore penalised ONCE, by the TIGHTEST cap any triggered check places on it,
    contributing ``cap / count`` as a smooth stand-in for the paper's hard cap; the
    product is floored at ``PLAUSIBILITY_FLOOR``. Formulas that trigger no restriction, or
    stay within every cap, score 1.0."""
    tightest: dict[str, int] = {}
    for trigger, caps in _RULE6_CHECKS:
        if all(counts.get(el, 0) > thr for el, thr in trigger.items()):
            for el, cap in caps.items():
                if counts.get(el, 0) > cap:
                    tightest[el] = min(tightest.get(el, cap), cap)
    score = 1.0
    for el, cap in tightest.items():
        score *= max(PLAUSIBILITY_FLOOR, cap / counts[el])
    return max(PLAUSIBILITY_FLOOR, score)


def senior_plausibility(counts: dict[str, int] | None) -> float:
    """Rule 2 (Lewis/Senior) as a plausibility in [0, 1]. 1.0 whenever some valence
    assignment admits a connected graph -- including odd-electron radicals and any formula
    with an element outside ``_VALENCE_STATES`` (fail open, as in `rule_senior`) -- and
    0.0 only for a *provably impossible* one. Shares `_senior_feasible` with
    `rule_senior`, so the graded factor and the hard gate cannot disagree."""
    return 1.0 if _senior_feasible(counts) else 0.0


# Pure function of the formula string; cached because arbitration re-evaluates
# it per candidate per peak across a whole run's ledger.
@lru_cache(maxsize=65536)
def formula_plausibility(formula: str) -> float:
    """Combined graded chemical plausibility in [0, 1] for a single NEUTRAL formula.

    0.0 **iff** the Senior/RDBE check (Rule 2) proves the graph impossible; otherwise the
    element-ratio (Rules 4-5) and heteroatom co-occurrence (Rule 6) factors multiply and
    the result is floored at ``PLAUSIBILITY_FLOOR``, so a merely unusual formula is never
    mistaken for an impossible one. Unparseable formulas fail open to 1.0. See the section
    header for the design; numbers from Kind & Fiehn 2007."""
    counts = element_counts(formula)
    if counts is None:
        return 1.0
    if not _senior_feasible(counts):
        return 0.0
    return max(
        PLAUSIBILITY_FLOOR,
        element_ratio_plausibility(counts)
        * heteroatom_probability_plausibility(counts),
    )


# Below this, a candidate's chemistry is weak enough to note in the filter log.
PLAUSIBILITY_LOG_THRESHOLD = 0.3


def chemical_plausibility(
    candidates: pl.DataFrame, **kwargs
) -> tuple[pl.Series, list[str]]:
    """Per-candidate graded chemical plausibility in [0, 1] for the confidence
    layer (Seven Golden Rules; docs/dev/assignment_confidence.md, P1).

    Unlike `rule_element_ratio` / `rule_senior`, this does NOT filter -- it returns
    a Float64 plausibility per NEUTRAL formula that the arbitration layer weighs
    against the fit score. Deterministic and fail-open; see `formula_plausibility`.
    """
    log_messages: list[str] = []
    if candidates.is_empty():
        return pl.Series([], dtype=pl.Float64), log_messages
    scores = [
        formula_plausibility(f) for f in candidates.get_column("formula").to_list()
    ]
    for formula, score in zip(candidates.get_column("formula").to_list(), scores):
        if score < PLAUSIBILITY_LOG_THRESHOLD:
            log_messages.append(
                f"Low chemical plausibility ({score:.2f}) for formula '{formula}'."
            )
    return pl.Series(scores, dtype=pl.Float64), log_messages


def rule_known_chemical_space(
    candidates: pl.DataFrame, **kwargs
) -> tuple[pl.Series, list[str]]:
    """Known chemical space (database matching)."""
    # TODO: requires access to some chemical database
    mask = pl.Series([True] * candidates.height)
    log_messages = []
    return mask, log_messages  # Placeholder, always returns True


# From lightweight to heavyweight, these rules are applied in order.
HEURISTIC_RULES = [
    rule_element_ratio,
    rule_valence,
    rule_senior,
    rule_known_chemical_space,
]


def apply_heuristic_rules(
    candidates: list[dict[str, Any]],
    heuristics_config: HeuristicFilterConfig | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Filter candidate formulas using the heuristic rules.
    Returns only those that pass all rules.

    :param candidates: List of candidate formula dicts (or Result objects).
    :return: Filtered list of candidates.
    """
    if heuristics_config is None:
        heuristics_config = HeuristicFilterConfig()
    log_messages = []
    candidates_df = pl.DataFrame(candidates)
    if candidates_df.is_empty():
        log_messages.append("No candidates provided for heuristic filtering.")
        return [], log_messages

    if "()" in candidates_df.get_column("formula").to_list():
        # Skip all rules for ionization peaks
        return (
            candidates_df.filter(pl.col("formula") == "()").to_dicts(),
            log_messages,
        )

    for i, rule in enumerate(HEURISTIC_RULES):
        if candidates_df.is_empty():
            log_messages.append(
                f"No candidates passed the rule: {HEURISTIC_RULES[i - 1].__name__}"
            )
            break
        rule_mask, rule_log_messages = rule(
            candidates_df, heuristics_config=heuristics_config
        )
        log_messages.extend(rule_log_messages)
        candidates_df = candidates_df.filter(rule_mask)

    return candidates_df.to_dicts(), log_messages


def match_isotopic_pattern(
    candidates: list[dict[str, Any]], peaks: pl.DataFrame
) -> tuple[list[dict[str, Any]], list[dict[str, np.ndarray | list[str]]]]:
    """Matches isotopic patterns against candidates.

    :param candidates: List of candidate formula dicts.
    :type candidates: list[dict[str, Any]]
    :param peaks: Sorted dataframe of peaks with 'mz' and 'intensity' columns.
    :type peaks: pl.DataFrame
    :return: Tuple of filtered candidates, and a list of isotope data dicts (per candidate).
    :rtype: tuple[list[dict[str, Any]], list[dict[str, np.ndarray | list[str]]]]
    """
    mzs = peaks["mz"].to_numpy()
    intensities = peaks["intensity"].to_numpy()

    candidates_df = pl.DataFrame(candidates)
    if candidates_df.is_empty():
        candidates_df = candidates_df.with_columns(
            pl.lit(0.0, dtype=pl.Float64).alias("isotopic_pattern_score")
        )
        return candidates_df.to_dicts(), []

    # Keep only the most promising candidates for heavy work
    candidates_df = candidates_df.sort("composition_error_ppm").head(
        ISOTOPE_CANDIDATE_LIMIT
    )

    # If ionization peak: skip isotopic matching and return score 1.0
    if "()" in candidates_df.get_column("formula").to_list():
        candidates_df = candidates_df.with_columns(
            pl.lit(1.0, dtype=pl.Float64).alias("isotopic_pattern_score")
        )
        return candidates_df.to_dicts(), []

    ion_formulas, ion_charges = _extract_formulae_and_charges(
        candidates_df.get_column("ion")
    )

    scores = np.zeros(candidates_df.height, dtype=float)
    all_isotope_data = []

    for ind, (ion_formula, ion_charge) in enumerate(zip(ion_formulas, ion_charges)):
        predicted_mzs, predicted_intensities, isotope_labels = predict_isotopes(
            ion_formula, ion_charge
        )
        is_isotope_predicted = len(predicted_mzs) > 0
        if not is_isotope_predicted:
            all_isotope_data.append(
                {
                    "masses": [],
                    "mass_errors_ppm": [],
                    "intensity_errors": [],
                    "labels": [],
                    "predicted_masses": [],
                    "predicted_intensities": [],
                }
            )
            continue

        observed_masses = np.zeros_like(predicted_mzs)
        observed_intensities = observed_masses.copy()
        observed_mass_errors_ppm = observed_masses.copy()
        observed_intensity_error = observed_masses.copy()

        # Normalize predicted intensities relative to monoisotopic (base) peak
        predicted_rel = predicted_intensities / predicted_intensities[0]

        base_peak_intensity = None
        for i, p_mz in enumerate(predicted_mzs):
            mz_delta = p_mz * ISOTOPE_MATCHING_MZ_TOLERANCE_PPM * 1e-6
            mz_min, mz_max = p_mz - mz_delta, p_mz + mz_delta

            start_idx = np.searchsorted(mzs, mz_min, side="left")
            end_idx = np.searchsorted(mzs, mz_max, side="right")
            no_peaks_in_window = start_idx >= end_idx

            if no_peaks_in_window:
                continue

            window_mzs = mzs[start_idx:end_idx]
            window_intensities = intensities[start_idx:end_idx]
            if not window_mzs.size:
                continue

            matched_index = np.argmin(np.abs(window_mzs - p_mz))
            matched_mz = window_mzs[matched_index]
            matched_intensity = window_intensities[matched_index]
            is_base_peak = i == 0

            if is_base_peak:
                base_peak_intensity = matched_intensity
                observed_intensities[0] = matched_intensity
                observed_masses[0] = matched_mz
                observed_mass_errors_ppm[0] = abs(matched_mz - p_mz) / p_mz * 1e6
                observed_intensity_error[0] = 0.0
                continue  # move to next isotope

            # Require monoisotopic established before evaluating higher isotopes
            if base_peak_intensity is None or base_peak_intensity == 0:
                continue

            predicted_rel_intensity = predicted_rel[i]
            observed_rel_intensity = matched_intensity / base_peak_intensity
            intensity_error = (
                abs(predicted_rel_intensity - observed_rel_intensity)
                / predicted_rel_intensity
            )

            if intensity_error <= ISOTOPE_MATCHING_INTENSITY_TOLERANCE:
                observed_intensities[i] = matched_intensity
                observed_masses[i] = matched_mz
                observed_mass_errors_ppm[i] = abs(matched_mz - p_mz) / p_mz * 1e6
                observed_intensity_error[i] = intensity_error

        scores[ind] = score_pattern(
            observed_masses,
            observed_mass_errors_ppm,
            observed_intensities,
            observed_intensity_error,
            predicted_rel,
        )

        matched_isotopes = {
            "masses": observed_masses,
            "mass_errors_ppm": observed_mass_errors_ppm,
            "intensity_errors": observed_intensity_error,
            "labels": isotope_labels,
            "predicted_masses": predicted_mzs,
            "predicted_intensities": predicted_rel,
        }

        all_isotope_data.append(matched_isotopes)

    candidates_df = candidates_df.with_columns(
        pl.Series(values=scores, name="isotopic_pattern_score")
    ).sort("isotopic_pattern_score", descending=True)

    score_sorted_indices = np.argsort(scores)[::-1]
    all_isotope_data = [all_isotope_data[i] for i in score_sorted_indices]

    return candidates_df.to_dicts(), all_isotope_data


def _custom_isotope_combinations(
    symbol: str, count: int, purity: float
) -> list[tuple[float, float, int]]:
    """Multinomial isotope combinations for `count` atoms of a labelled '^X'
    (two-isotope) element. Returns [(added_mass, probability, n_light), ...]."""
    from math import comb

    isos = _CUSTOM_ELEMENT_DATA[symbol][1]
    (m_light, _), (m_heavy, _) = isos[0], isos[-1]
    p_heavy, p_light = purity, 1.0 - purity
    out = []
    for k in range(count + 1):  # k = number of heavy (labelled) atoms
        n_light = count - k
        prob = comb(count, k) * p_heavy**k * p_light**n_light
        out.append((k * m_heavy + n_light * m_light, prob, n_light))
    return out


def _predict_isotopes_custom(
    ion_formula: str, ion_charge: int, purity: float
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """predict_isotopes for an ion containing labelled '^X' custom elements: base
    (non-custom) envelope via IsoSpec, convolved with the labelled distribution(s)
    (multinomial; Cartesian product for several). Most-abundant peak first."""
    import itertools

    from mascope_tools.composition.utils import parse_composition, to_hill_order

    comp = parse_composition(ion_formula)
    customs = {s: int(comp[s]) for s in comp if s.startswith("^")}
    base = {s: int(comp[s]) for s in comp if not s.startswith("^") and comp[s] > 0}
    base_formula = to_hill_order(base) if base else ""

    if base_formula:
        peaks = IsoThreshold(
            formula=base_formula, threshold=ISOTOPE_ABUNDANCE_THRESHOLD, get_confs=True
        )
        base_masses = [float(m) for m in peaks.masses]
        base_probs = [float(p) for p in peaks.probs]
        base_labels = extract_isotope_labels(base_formula, peaks)
    else:
        base_masses, base_probs, base_labels = [0.0], [1.0], ["M0"]

    combos_per_element = [
        [
            (
                mass,
                prob,
                n_light,
                _CUSTOM_ELEMENT_DATA[sym][0],
                _CUSTOM_ELEMENT_DATA[sym][1][0][1],
            )
            for (mass, prob, n_light) in _custom_isotope_combinations(sym, cnt, purity)
        ]
        for sym, cnt in customs.items()
    ]

    merged: dict[float, list] = {}
    for bm, bp, bl in zip(base_masses, base_probs, base_labels):
        for combo in itertools.product(*combos_per_element):
            mass = bm + sum(c[0] for c in combo)
            prob = bp
            for c in combo:
                prob *= c[1]
            if prob < ISOTOPE_ABUNDANCE_THRESHOLD:
                continue
            deviations = [
                f"{light_mn}{regular}" + (str(n_light) if n_light > 1 else "")
                for (_, _, n_light, regular, light_mn) in combo
                if n_light
            ]
            parts = ([] if bl in ("M0", "", "---") else [bl]) + deviations
            label = "+".join(parts) if parts else "M0"
            key = round(mass, 4)
            if key in merged:
                m0, p0, _ = merged[key]
                tot = p0 + prob
                merged[key][0] = (m0 * p0 + mass * prob) / tot
                merged[key][1] = tot
            else:
                merged[key] = [mass, prob, label]

    items = sorted(merged.values(), key=lambda x: -x[1])  # most-abundant first
    mzs = np.array(
        [(m - ELECTRON_MASS * ion_charge) / abs(ion_charge) for m, _, _ in items]
    )
    probs = np.array([p for _, p, _ in items])
    labels = [lab for _, _, lab in items]
    return mzs, probs, labels


def predict_isotopes(
    ion_formula: str, ion_charge: int, purity: float | None = None
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Predict isotopic pattern for a given ion formula and charge.

    :param ion_formula: Ion formula string (may contain labelled '^X' elements).
    :type ion_formula: str
    :param ion_charge: Ion charge (e.g., +1, -1).
    :type ion_charge: int
    :param purity: Isotopic purity of labelled '^X' elements (heaviest-isotope
        fraction, e.g. 0.98 for a 98% 15N reagent). A property of the labelled
        reagent; the caller passes its value. Ignored for non-labelled ions.
        Defaults to ``LABELLED_REAGENT_PURITY``.
    :type purity: float, optional
    :return: Tuple of predicted m/z values, relative intensities, and isotope labels.
    :rtype: tuple[np.ndarray, np.ndarray, list[str]]
    """
    if "^" in ion_formula:
        try:
            return _predict_isotopes_custom(
                ion_formula,
                ion_charge,
                LABELLED_REAGENT_PURITY if purity is None else purity,
            )
        except Exception:
            return [], [], []
    try:
        predicted_peaks = IsoThreshold(
            formula=ion_formula,
            threshold=ISOTOPE_ABUNDANCE_THRESHOLD,
            get_confs=True,
        )
        predicted_masses_neutral = np.fromiter(predicted_peaks.masses, dtype=float)
        predicted_intensities = np.fromiter(predicted_peaks.probs, dtype=float)
        isotope_labels = extract_isotope_labels(ion_formula, predicted_peaks)
        # Convert neutral masses to m/z
        predicted_mzs = (predicted_masses_neutral - ELECTRON_MASS * ion_charge) / abs(
            ion_charge
        )
    except Exception:
        predicted_mzs, predicted_intensities, isotope_labels = [], [], []

    return predicted_mzs, predicted_intensities, isotope_labels


def extract_isotope_labels(
    ion_formula: str, predicted_isotopes: IsoDistribution
) -> list[str]:
    """Convert isotope configurations to labels.
    Requires IsoDistribution with confs.

    Examples:
        >>> extract_isotope_labels(
        ...     "C6H12O6",
        ...     IsoThreshold(
        ...         formula="C6H12O6",
        ...         threshold=ISOTOPE_ABUNDANCE_THRESHOLD,
        ...         get_confs=True
        ...     )
        ... )
        ['M0', '13C', '18O']

    :param ion_formula: Ion formula string.
    :type ion_formula: str
    :param predicted_isotopes: Predicted isotope distribution.
    :type predicted_isotopes: IsoDistribution
    :return: List of isotope labels.
    :rtype: list[str]
    """
    if ion_formula.endswith(("+", "-")):
        # Remove charge character for parsing
        ion_formula = ion_formula[:-1]
    try:
        composition = Composition(formula=to_pyteomics(ion_formula))
        elements = list(composition.keys())
        elemental_masses = [PeriodicTbl.symbol_to_masses[el] for el in elements]
        isotope_labels = [
            conf_to_label(conf, elements, elemental_masses)
            for conf in predicted_isotopes.confs
        ]
    except AttributeError:
        raise AttributeError(
            "Predicted isotopes must include configurations (confs) for label extraction."
        )
    return isotope_labels


def score_pattern(
    observed_masses: np.ndarray,
    observed_mass_errors_ppm: np.ndarray,
    observed_intensities: np.ndarray,
    observed_intensity_error: np.ndarray,
    predicted_rel: np.ndarray,
) -> float:
    """
    Scores the match between observed and predicted isotopic patterns.
    Returns a score between 0 and 1, where 1 is a perfect match.
    """
    # Require monoisotopic detection
    if observed_intensities[0] > 0:
        observed_rel_intensities = observed_intensities / observed_intensities[0]
        matched_peaks_count = np.sum(observed_masses > 0)

        # 1. Pattern scoring
        cosine_dist = cosine(predicted_rel, observed_rel_intensities)
        pattern_score = 1 - cosine_dist if not np.isnan(cosine_dist) else 0.0

        # 2. Intensity scoring
        total_intensity_error = np.sum(observed_intensity_error)
        avg_intensity_error = (
            total_intensity_error / matched_peaks_count
            if matched_peaks_count > 0
            else ISOTOPE_MATCHING_INTENSITY_TOLERANCE
        )
        intensity_score = max(
            0, 1 - (avg_intensity_error / ISOTOPE_MATCHING_INTENSITY_TOLERANCE)
        )

        # 3. Mass Accuracy Score
        total_mass_error_ppm = np.sum(observed_mass_errors_ppm)
        avg_mass_error = (
            total_mass_error_ppm / matched_peaks_count
            if matched_peaks_count > 0
            else ISOTOPE_MATCHING_MZ_TOLERANCE_PPM
        )
        mass_score = max(0, 1 - (avg_mass_error / ISOTOPE_MATCHING_MZ_TOLERANCE_PPM))

        # 4. Combined score.
        # pattern_score and intensity_score get lower weights because they are less reliable,
        # we may have only base peak detected.
        score = 0.2 * pattern_score + 0.2 * intensity_score + 0.6 * mass_score
    else:
        score = 0.0

    return score


# ---------------------------------------------------------------------------
# Match score, version 2 (detectability-gated, SNR-aware, calibratable).
#
# v1 (`score_pattern`, above) averages errors over the MATCHED peaks only, so an
# incomplete isotope envelope is not penalised, and it normalises mass by a fixed
# 5 ppm. v2 fixes both: it penalises a predicted isotopologue that is ABSENT but
# should have been visible (expected SNR `rel_i*SNR_base >= k_detect`), judges each
# isotopologue's intensity against ITS OWN noise (per-peak SNR), uses a Gaussian
# mass likelihood, and aggregates as a predicted-abundance-weighted geometric mean.
# On the demo golden set vs v1: ROC-AUC 0.876->0.890, held-out calibrated ECE
# 0.020->0.0069 (see tooling/score_eval/DESIGN.md -- untracked scratch, not in the
# repo). v1 is retained byte-identical, and each caller calls the version it wants
# directly (SCORE_VERSION records the newest shipped version; nothing branches on
# it). Inputs use the same matched-array convention as
# v1 (unmatched isotopologues carry 0), PLUS the matched peaks' signal_to_noise --
# which is OPTIONAL: SNR is only ever used to widen a tolerance, so a caller that has
# none (the DB-read aggregation paths) passes None / NaN and is scored in "no-SNR mode"
# (fixed instrument mass width, abundance-floor intensity tolerance, abundance-based
# detectability gate). See `score_pattern_v2` and libraries/tools/docs/fit_score.md §3.3a.
# ---------------------------------------------------------------------------
SCORE_VERSION = 2

# Mass-error Gaussian width used ONLY when the caller does not supply the
# instrument's fitted mass accuracy. The mass term is `exp(-0.5*(ppm/sigma)^2)`, so
# `sigma_ppm` must match the instrument: ~0.5-2 ppm for an Orbitrap, ~5-10 ppm for a
# TOF. This fallback is Orbitrap-appropriate and is WRONG for a TOF (it would tank
# valid low-resolution matches) — pass the per-sample fitted sigma instead.
FALLBACK_SIGMA_PPM = 2.0

# SNR-dependent centroiding contribution to the mass-error width, added in quadrature to
# the fixed (fitted-instrument) sigma: the mass error of a peak scales as
# sigma^2 = sigma_fixed^2 + (MASS_SNR_K / SNR)^2 -- a weak peak's centroid is legitimately
# less precise. Fitted on the demo goldens (sigma vs SNR: floor ~0.23 ppm, k ~2.36 ppm), so
# a trace isotopologue is not scored against the tight high-SNR width. High-SNR peaks are
# unaffected (MASS_SNR_K/SNR -> 0). Set 0 to recover the fixed-width mass term.
MASS_SNR_K = 2.36

# Detectability threshold used when the base peak's SNR is unknown: an ABSENT predicted
# isotopologue at or above this fraction of the base peak is penalised regardless of SNR.
# Without a noise estimate the expected-SNR gate (p_i*s_0 >= k_detect) cannot be evaluated,
# and simply excluding every absent peak makes the score envelope-blind. Predicted
# envelopes run down to 1e-5 relative abundance (ISOTOPE_ABUNDANCE_THRESHOLD), so some
# threshold is mandatory; 0.10 is the ~1:10 dynamic range that any peak strong enough to be
# matched at all demonstrably exceeds, and it covers the diagnostic cases (Cl 32%, Br 97%,
# one 13C on C>=10). Below it, absence is uninformative without a noise estimate.
REL_DETECT_NO_SNR = 0.10

# Platt calibration (raw fit score -> P(correct)) fitted on the demo Br/Ur golden
# set, on the real-per-peak-SNR path — it is NOT calibrated for the no-SNR mode.
# Maps a raw v2 score to a probability; refit per instrument/dataset with the
# score_eval harness (tooling/score_eval; its DESIGN.md §5.3 is untracked scratch)
# for production — a sensible default, not a universal constant.
DEFAULT_CALIBRATION_V2 = (6.0546, -4.1481)  # (a, b) fit on the demo Br/Ur golden set


def calibrate_score(raw, calibration=None):
    """Map a raw v2 score to a probability via `sigmoid(a*raw + b)`."""
    a, b = calibration or DEFAULT_CALIBRATION_V2
    return 1.0 / (1.0 + np.exp(-(a * np.asarray(raw, float) + b)))


def score_pattern_v2(
    observed_mass_errors_ppm: np.ndarray,
    observed_intensities: np.ndarray,
    observed_snr: np.ndarray | None,
    predicted_rel: np.ndarray,
    *,
    k_detect: float = 3.0,
    miss_penalty: float = 0.3,
    sigma_ppm: float | None = None,
    rel_detect_no_snr: float = REL_DETECT_NO_SNR,
) -> float:
    """Detectability-gated, SNR-aware match score in [0, 1].

    Per predicted isotopologue i (predicted relative abundance `predicted_rel[i]`;
    index 0 is the BASE peak — the most abundant predicted isotopologue, which the caller
    puts first and which for a polyhalogenated ion is not the monoisotopic one): a matched
    peak contributes a Gaussian mass likelihood (its width the fitted instrument sigma in
    quadrature with an SNR-dependent centroiding term, `MASS_SNR_K/SNR`) times an
    intensity likelihood whose tolerance is set by the peak's own SNR; an ABSENT peak
    contributes `miss_penalty` iff it should have been detectable
    (`predicted_rel[i]*SNR_base >= k_detect`), else it is excluded (below noise, not
    evidence). Aggregation is a predicted-abundance-weighted geometric mean. Returns
    0 if the base peak is absent. Satellite peaks must be excluded by the
    caller. Pair with `calibrate_score` to get P(correct).

    **SNR is optional.** Every SNR term above is a *concession granted on evidence that a
    peak is noisy* — it only ever WIDENS a tolerance. `observed_snr=None`, or a per-row
    value that is zero, negative, NaN or infinite, therefore grants no concession: that
    row is judged at the instrument width `sigma_ppm` and the abundance floors, exactly as
    a clean high-SNR peak is, and the detectability gate falls back to
    `predicted_rel[i] >= rel_detect_no_snr` when the BASE peak's SNR is unknown. Because
    the real-SNR ratio tolerance only exceeds the 5%-of-abundance floor below SNR ~20, a
    normally-measured envelope scores the same in both modes; the modes diverge only for
    genuinely weak peaks, and then conservatively. The residual: an absent isotopologue
    predicted between `k_detect/SNR_base` and `rel_detect_no_snr` is excluded rather than
    penalised in no-SNR mode (max deviation ~0.085; see fit_score.md §6).

    `sigma_ppm` is the instrument's mass-error std (the mass-term width); pass the
    fitted per-sample value so the score is resolution-correct (Orbitrap vs TOF).
    `observed_mass_errors_ppm` should be offset-centred (subtract the fitted mu).
    When `sigma_ppm` is None, `FALLBACK_SIGMA_PPM` is used — Orbitrap-only; see it.

    :raises ValueError: if the input arrays do not all have one entry per predicted
        isotopologue."""
    if sigma_ppm is None:
        sigma_ppm = FALLBACK_SIGMA_PPM
    oi = np.asarray(observed_intensities, float)
    me = np.asarray(observed_mass_errors_ppm, float)
    pr = np.asarray(predicted_rel, float)
    n = pr.size
    snr = (
        np.full(n, np.nan) if observed_snr is None else np.asarray(observed_snr, float)
    )
    sizes = {
        "observed_mass_errors_ppm": me.size,
        "observed_intensities": oi.size,
        "observed_snr": snr.size,
        "predicted_rel": n,
    }
    if len(set(sizes.values())) > 1:
        # Left to numpy this surfaces far downstream in np.maximum.reduce as an
        # "inhomogeneous shape" error that points nowhere near the mismatched caller.
        raise ValueError(
            "score_pattern_v2 needs one entry per predicted isotopologue in every input "
            "array; got " + ", ".join(f"{k}={v}" for k, v in sizes.items())
        )
    # `not (oi[0] > 0)` also rejects a NaN base intensity, which `oi[0] <= 0` let through.
    if n == 0 or not (oi[0] > 0) or not np.isfinite(me[0]):
        return 0.0
    # A "matched" peak whose mass error is unusable carries no mass evidence: treat it as
    # absent so the detectability gate judges it, rather than handing it a free perfect
    # mass likelihood (or a NaN that drops it out of the weighted mean altogether).
    oi = np.where(np.isfinite(me), oi, 0.0)
    me = np.abs(np.nan_to_num(me, nan=0.0, posinf=0.0, neginf=0.0))
    # A non-finite or non-positive predicted abundance is not a prediction to score
    # against; zeroing it drops that isotopologue from the weighted mean, instead of
    # turning the whole ion's score into a NaN that `wsum <= 0` would not catch.
    pr = np.where(np.isfinite(pr) & (pr > 0), pr, 0.0)
    # An SNR is usable only when finite and positive. Zero/negative/NaN/inf all mean "no
    # SNR for this peak". The SNR terms below only ever WIDEN a tolerance, so an unusable
    # value must contribute nothing: with no evidence that the peak is noisy it is judged
    # at the instrument width and the abundance floors -- the same treatment a clean
    # high-SNR peak gets. Reading missing information as an infinitely noisy peak would
    # instead grant infinite tolerance, i.e. score a bad fit as a perfect one.
    snr_known = np.isfinite(snr) & (snr > 0)
    # The nested `where` keeps the division away from the zeros/NaNs, so no RuntimeWarning.
    inv_snr = np.where(snr_known, 1.0 / np.where(snr_known, snr, 1.0), 0.0)
    base_int = oi[0]

    matched = oi > 0
    # Per-peak mass width: the fixed (fitted-instrument) sigma in quadrature with an
    # SNR-dependent centroiding term, so a low-SNR peak's legitimately larger mass error
    # is not judged against the tight high-SNR width. High-SNR peaks are unchanged, and an
    # unknown SNR falls back to the fixed sigma alone.
    sigma_mass = np.hypot(sigma_ppm, MASS_SNR_K * inv_snr)
    mass_L = np.exp(-0.5 * (me / sigma_mass) ** 2)
    rel_obs = oi / base_int
    sigma_rel = np.maximum.reduce(
        [
            # Unknown SNR contributes 0 here, so the abundance floors below decide.
            rel_obs * np.sqrt(inv_snr**2 + inv_snr[0] ** 2),
            0.05 * pr,
            np.full(n, 1e-3),
        ]
    )
    int_L = np.exp(-0.5 * ((rel_obs - pr) / sigma_rel) ** 2)

    L = np.full(n, np.nan)
    L[0] = mass_L[0]
    m = matched.copy()
    m[0] = False
    L[m] = mass_L[m] * int_L[m]
    absent = ~matched
    absent[0] = False
    if snr_known[0]:
        detectable = absent & (pr * snr[0] >= k_detect)
    else:
        # No base SNR -> the expected-SNR gate is unevaluable; fall back to abundance, so
        # the score stays envelope-aware instead of ignoring every absent isotopologue.
        detectable = absent & (pr >= rel_detect_no_snr)
    L[detectable] = miss_penalty
    include = ~np.isnan(L)
    L = np.where(include, np.maximum(L, 1e-6), 1.0)
    w = pr * include
    wsum = w.sum()
    if wsum <= 0:
        return 0.0
    return float(np.exp((w * np.log(L)).sum() / wsum))


def conf_to_label(conf, elements, isotope_masses):
    """Return isotope label string.

    :param conf: isotope counts for each element in the formula.
    :type conf: list[list[int]]
    :param elements: list of elements in the formula.
    :type elements: list[str]
    :param isotope_masses: list of isotope masses for each element.
    :type isotope_masses: list[list[float]]
    """
    label_parts = []
    for el, iso_counts, iso_masses in zip(elements, conf, isotope_masses):
        for idx, count in enumerate(iso_counts):
            if count == 0:
                continue

            # For the most abundant isotope (usually index 0), skip label unless it's the only one (M0)
            if idx == 0:
                continue

            mass_number = int(round(iso_masses[idx]))

            label_parts.append(f"{mass_number}{el}{count if count > 1 else ''}")

    if not label_parts:
        return "M0"
    return "+".join(label_parts)


def _extract_formulae_and_charges(ions: pl.Series) -> tuple[list[str], list[int]]:
    """Extracts formulae and charges from ion strings

    :param ions: Array of ion strings.
    :type ions: pl.Series
    :return: Tuple of lists containing ion formulas and their charges.
    :rtype: tuple[list[str], list[int]]
    """
    ions_arr = ions.to_numpy().astype(str)
    # Get last character for each ion string
    last_chars = np.array([s[-1] if len(s) >= 1 else "" for s in ions_arr])
    # Check if last char is + or -
    is_charged = np.isin(last_chars, ["+", "-"])
    # Remove last char if charged, else keep as is
    ion_formulas = [
        s[:-1] if charged else s for s, charged in zip(ions_arr, is_charged)
    ]
    # Assign charge: +1 for '+', -1 for '-', else 1
    ion_charges = [1 if c == "+" else -1 if c == "-" else 1 for c in last_chars]
    return ion_formulas, ion_charges
