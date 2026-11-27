"""What a committed reading's peak held against it, as the search hands it on.

The formula search ranks a peak's candidates at its own scale and commits one.
Beside the row it hands on the candidates its ranking could not separate from
the committed one (:data:`CANDIDATE_RIVALS`), which the run measures again at
its model as the row's rival set (``rival_set`` in the backend). With
``CompositionSearchConfig.scored_rivals`` on it hands on every other formula it
scored as well (:data:`SCORED_RIVALS`), with how near the search had each come
to the reading: a measurement's input (MAS-1184), which nothing the search
ranks or commits reads.
"""

from __future__ import annotations

from mascope_tools.composition import utils
from mascope_tools.composition.arbitration import (
    CANDIDATE_DENSITY,
    tie_gaps,
    unseparated,
)
from mascope_tools.composition.models import CompositionSearchConfig


#: On a committed reading's monoisotopic row, the formulas its peak's
#: candidates could not separate from it, each with the ion and channel that
#: read it: what the run measures again at its model.
CANDIDATE_RIVALS = "candidate_rivals"

#: On a committed reading's monoisotopic row, with
#: ``CompositionSearchConfig.scored_rivals`` on: every other formula its
#: peak's candidates scored, but those of its own ion, each with its ion,
#: channel and the fit it ranked at, and how far the reading's evidence had it
#: in tie gaps (``arbitration.tie_gaps``), nearest first. Absent with the
#: option off. A run measures them only to count them (MAS-1184).
SCORED_RIVALS = "scored_rivals"


def unseparated_rivals(
    candidates: list[dict],
    committed: dict,
    config: CompositionSearchConfig | None = None,
) -> dict:
    """What a committed reading's own candidates could not separate from it.

    The count :func:`arbitration.candidate_density` makes, anchored on the
    committed formula, and the formulas it counted, each named by the reading
    that scored it best, so the run can measure them again at its model
    beside the row (:data:`CANDIDATE_RIVALS`). Here the search ranks; the run
    records the rival set it measures (``rival_set`` in the backend).

    :param candidates: The peak's scored candidates.
    :param committed: The reading the row commits.
    :param config: The search's config; with its ``scored_rivals`` on, every
        other formula scored is handed over too (:data:`SCORED_RIVALS`).
    :return: ``{CANDIDATE_DENSITY: n, CANDIDATE_RIVALS: [...]}``, and
        :data:`SCORED_RIVALS` with the option on.
    """
    best = _best_by_formula(candidates)
    counted = unseparated(
        [
            {"formula": formula, "fit_score": c.get("isotopic_pattern_score")}
            for formula, c in best.items()
        ],
        around=committed["formula"],
    )
    held = {
        CANDIDATE_DENSITY: len(counted),
        CANDIDATE_RIVALS: [
            {
                "formula": formula,
                "ion": best[formula].get("ion"),
                "ionization_mechanism": best[formula].get("ionization_mechanism"),
            }
            for formula in counted
            if formula != committed["formula"] and formula in best
        ],
    }
    if config is not None and config.scored_rivals:
        held[SCORED_RIVALS] = list(
            scored_rivals(candidates, committed["formula"], committed.get("ion"))
        )
    return held


def scored_rivals(candidates: list, own_formula: str, own_ion) -> tuple[dict, ...]:
    """Every formula scored for a reading's peak but its own ion's, each by
    the reading that scored it best, with how far the reading's evidence had it
    in tie gaps (:func:`arbitration.tie_gaps`), nearest first
    (:data:`SCORED_RIVALS`). A reading of the same ion is the same hypothesis
    split another way, no rival, as the run's rival set reads it."""
    best = _best_by_formula(candidates)
    gaps = tie_gaps(
        [(formula, c.get("isotopic_pattern_score")) for formula, c in best.items()],
        around=own_formula,
    )
    ion = utils.ion_identity(own_ion)
    named = [
        {
            "formula": formula,
            "ion": candidate.get("ion"),
            "ionization_mechanism": candidate.get("ionization_mechanism"),
            "fit_score": float(candidate.get("isotopic_pattern_score") or 0.0),
            "tie_gaps": gaps[formula],
        }
        for formula, candidate in best.items()
        if formula != own_formula
        and formula in gaps
        and utils.ion_identity(candidate.get("ion")) != ion
    ]
    return tuple(sorted(named, key=lambda r: (r["tie_gaps"], r["formula"])))


def _best_by_formula(candidates: list) -> dict[str, dict]:
    """Each formula's best-fitting reading among a peak's scored candidates."""
    best: dict[str, dict] = {}
    for candidate in candidates:
        formula = str(candidate.get("formula"))
        fit = float(candidate.get("isotopic_pattern_score") or 0.0)
        if fit > float(best.get(formula, {}).get("isotopic_pattern_score") or -1.0):
            best[formula] = candidate
    return best
