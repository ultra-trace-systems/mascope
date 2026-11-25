"""What the cap on an untargeted winner's stored alternatives cut.

``engine.cap_alternatives`` counts every rival formula the cap leaves out
under ``engine.RIVALS_PAST_CAP``. On an untargeted row it also tells them
apart by what their entries carry, here: a losing contender carries its fit
and its isotope label (another reading's line on the peak), a formula of the
finder's shortlist neither. The identification level counts the
formula-only ones as rivals not tested; the contenders are measured by the
rival set (``rival_set.RIVAL_SET``), which counts the ones it does not hold.
"""

from __future__ import annotations

from mascope_tools.composition.utils import formula_identity


#: The provenance key an untargeted winner carries beside
#: ``engine.RIVALS_PAST_CAP``: how many of the rival formulas the cap left out
#: are formula-only, every entry of them measuring nothing on the peak - the
#: finder's shortlist names, which carry no fit and no isotope label. No pass
#: measured them, so the identification level counts them as rivals not
#: tested. Absent where the cap cut none. Rows of every other source do not
#: carry it.
FORMULA_ONLY_PAST_CAP = "formula_only_past_cap"

#: The provenance key an untargeted winner carries where the cap left out a
#: losing contender: the rival formulas it cut that carry their own
#: measurement on the peak (a fit and an isotope label: another reading's
#: line there), sorted. The rival set measures such a reading
#: (``rival_set.RIVAL_SET``); it records how many of them it does not hold,
#: and the level counts those as rivals not tested. Absent where the cap cut
#: none.
CONTENDERS_PAST_CAP = "contenders_past_cap"


def count_untargeted_cut(provenance: dict, past_cap: list, cut: set) -> None:
    """Split the rival formulas the cap cut on an untargeted row.

    The formula-only ones are added to :data:`FORMULA_ONLY_PAST_CAP` and the
    contenders to :data:`CONTENDERS_PAST_CAP`; a formula with a contender among
    its entries is a contender.

    :param provenance: The row's provenance, updated in place.
    :param past_cap: The entries past the cap, in the order they rank.
    :param cut: The rival formulas cut, as :func:`formula_identity` keys.
    """
    measured = {
        formula_identity(entry.get("assigned_formula"))
        for entry in past_cap
        if isinstance(entry, dict) and _measures_on_peak(entry)
    } & cut
    formula_only = len(cut - measured)
    if formula_only:
        provenance[FORMULA_ONLY_PAST_CAP] = (
            int(provenance.get(FORMULA_ONLY_PAST_CAP) or 0) + formula_only
        )
    if measured:
        held = provenance.get(CONTENDERS_PAST_CAP)
        provenance[CONTENDERS_PAST_CAP] = sorted(
            set(held if isinstance(held, list) else []) | measured
        )


def _measures_on_peak(entry: dict) -> bool:
    """Whether a stored alternative carries a measurement of its own on the
    row's peak: a fit and an isotope label, as a losing contender does."""
    return entry.get("fit_score") is not None and bool(entry.get("isotope_label"))
