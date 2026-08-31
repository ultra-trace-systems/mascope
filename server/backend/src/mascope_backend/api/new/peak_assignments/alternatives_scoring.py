"""Score an untargeted row's formula-only alternatives, on demand.

Stage B stores two kinds of entry in a row's ``alternatives``. The losing
contenders for the peak are scored - they were competed against the winner and
carry a fit, a mass error and the adduct they were found under. The finder's
``other_candidates`` shortlist is not: it is the raw list of compositions whose
mass fits the peak, frozen before the heuristic filter and the isotope-pattern
ranking chose a winner, and it reaches the row as a formula and a chemical
plausibility and nothing else. Scoring every one of them during a run would
mean an isotope-envelope match per candidate per peak across a whole sample,
which is why the run does not do it.

The inspector is a different budget. One peak at a time, one shortlist of at
most the run's ``max_alternatives`` formulas, and a peak read that
``compute_match_isotopes`` narrows to the seeded m/z windows - so the same
measurement the run declined to make wholesale is cheap to make when somebody
is actually looking at the peak.

What comes back is a measurement, not a record: nothing here writes, and the
scores are never stored onto the run's rows. A run is the append-only account
of what the engine did, and these numbers are not something it did. Committing
one of these candidates is therefore a ``set_assignment`` - the same action the
re-search hand button uses, which records in provenance that the numbers came
from a composition search rather than from the run's own arbitration.
"""

import pandas as pd

from mascope_backend.api.controllers.samples.lib.samples_fetch import fetch_sample
from mascope_backend.api.lib.api_features import api_controller
from mascope_backend.api.lib.exceptions.api_exceptions import NotFoundException
from mascope_backend.api.new.match.params import default_match_params
from mascope_backend.api.new.peak_assignments.engine import evidence_for
from mascope_backend.api.new.peak_assignments.seeded_scoring import (
    finite_or_none,
    score_or_none,
    score_seeds,
)
from mascope_backend.api.new.peak_assignments.service import fetch_sample_mechanisms
from mascope_backend.db import PeakAssignment, async_session
from mascope_tools.composition.heuristic_filter import formula_plausibility


#: Ceiling on the formulas one call will score. Each one costs a seed per adduct
#: of the sample, so the work is the product of two numbers a caller can raise:
#: a run's ``max_alternatives`` goes to ``MAX_ALTERNATIVES_CEILING`` (50), and an
#: imported run's ``alternatives`` are whatever the publishing client sent. The
#: default run stores 5, so this bites only on a row deliberately configured -
#: or published - well past it, and what it drops is REPORTED rather than
#: silently missing: an unmeasured entry says it was not measured.
MAX_SCORED_FORMULAS = 16


def unscorable_alternatives(alternatives: list | None) -> list[tuple[int, str]]:
    """The entries of a row's shortlist that carry a formula and no adduct.

    The same test the inspector disables its "use this" control on: an entry
    that names neither a mechanism nor a target ion to read one off cannot be
    committed, because a formula without its adduct is half an assignment.
    Those are exactly the entries worth measuring.

    :param alternatives: The row's stored ``alternatives`` list, or None.
    :return: ``(stored index, formula)`` for each such entry, in stored order.
    """
    found: list[tuple[int, str]] = []
    for index, entry in enumerate(alternatives or []):
        if not isinstance(entry, dict):
            continue
        formula = entry.get("assigned_formula")
        if not formula or not isinstance(formula, str):
            continue
        if entry.get("ionization_mechanism_id") or entry.get("target_ion_id"):
            continue
        found.append((index, formula))
    return found


def _m0_by_ion(scored_df: pd.DataFrame) -> dict[str, pd.Series]:
    """The monoisotopic row of each seeded ion in a scored frame.

    A shortlist entry is a composition the finder proposed *for this peak's
    own mass*, so the claim being measured is that the peak IS that ion's
    monoisotopic peak - not that it is some isotopologue of it. The M0 is the
    lightest isotopologue the generator emitted for the ion, so it is the one
    at the minimum m/z; reading it positionally would depend on frame order,
    which the matcher does not promise.

    :param scored_df: The gated, fit-scored seeded frame.
    :return: ion id -> its monoisotopic row.
    """
    if scored_df.empty or "target_ion_id" not in scored_df.columns:
        return {}
    # Positionally off a sort rather than `.loc[idxmin()]`: the frame reaches
    # here through a gate and a scorer, and a duplicated index would make that
    # lookup hand back a frame where every caller expects one row.
    return {
        str(ion_id): group.sort_values("mz").iloc[0]
        for ion_id, group in scored_df.groupby("target_ion_id", sort=False)
    }


def _paired_to(row: pd.Series | None, sample_peak_id: str) -> bool:
    """Whether a scored isotopologue row paired to this observed peak."""
    if row is None:
        return False
    peak_id = row.get("sample_peak_id")
    if peak_id is None or (isinstance(peak_id, float) and pd.isna(peak_id)):
        return False
    return str(peak_id) == str(sample_peak_id)


def _blocked_reason(mechanism_count: int, generated: bool) -> str:
    """Plain-language reason a formula could not be scored on this peak."""
    if mechanism_count == 0:
        return (
            "This sample has no adducts recorded for its ionization mode, so "
            "there is nothing to measure this formula under."
        )
    if not generated:
        return (
            "This formula could not be turned into an ion, so there is nothing "
            "to measure against the peak."
        )
    adducts = f"{mechanism_count} adduct{'s' if mechanism_count != 1 else ''}"
    return (
        f"None of this sample's {adducts} put this formula on this peak within "
        "the matcher's mass tolerance, so there is no measured fit to show and "
        "nothing to assign it under."
    )


@api_controller()
async def score_row_alternatives(
    sample_item_id: str,
    peak_assignment_id: str,
) -> dict:
    """Measure a row's formula-only alternatives against the peak they name.

    For each such formula, every adduct this sample is recorded under is tried
    in one seeded pass, and the one whose monoisotopic peak lands on this row's
    observed peak with the strongest evidence is reported. A formula no adduct
    places on the peak is reported blocked, with the reason said plainly.

    Ranked by evidence - ``fit x plausibility``, the currency both engine
    stages arbitrate a contested peak in and the one a tier is read off since
    the tier became evidence-based - so a formula that fits beautifully while
    describing an unlikely molecule does not outrank a plausible one here
    either. Ties go to the smaller mass error, then to the adduct's own name,
    so a dead heat resolves by the data rather than by dictionary order.

    :param sample_item_id: The sample the assignment belongs to.
    :param peak_assignment_id: The row whose shortlist is being measured.
    :return: Status envelope; ``data`` is one entry per formula-only
        alternative, each either scored or blocked with a reason.
    :raises NotFoundException: The assignment is not this sample's.
    """
    async with async_session() as session:
        assignment = await session.get(PeakAssignment, peak_assignment_id)
        if assignment is None or assignment.sample_item_id != sample_item_id:
            raise NotFoundException(
                f"Assignment '{peak_assignment_id}' not found for sample "
                f"'{sample_item_id}'"
            )
        alternatives = list(assignment.alternatives or [])
        sample_peak_id = assignment.sample_peak_id

    selected = unscorable_alternatives(alternatives)
    wanted = selected[:MAX_SCORED_FORMULAS]
    over_cap = selected[MAX_SCORED_FORMULAS:]
    if not wanted:
        return {
            "status": "success",
            "message": "This assignment has no formula-only alternatives.",
            "results": 0,
            "data": [],
        }

    sample = await fetch_sample(sample_item_id)
    _, mechanisms = await fetch_sample_mechanisms(sample)
    match_params = await default_match_params(sample_item_id)

    formulas = {formula for _, formula in wanted}
    seeds = {
        (formula, mechanism.ionization_mechanism_id)
        for formula in formulas
        for mechanism in mechanisms
    }
    ion_by_seed, fit_by_ion, _errors, scored_df = await score_seeds(
        sample, seeds, match_params
    )
    m0_by_ion = _m0_by_ion(scored_df)
    mechanism_by_id = {m.ionization_mechanism_id: m for m in mechanisms}

    data = []
    for index, formula in wanted:
        plausibility = round(float(formula_plausibility(formula)), 4)
        generated = False
        candidates = []
        for mechanism in mechanisms:
            ion_id = ion_by_seed.get((formula, mechanism.ionization_mechanism_id))
            if ion_id is None:
                continue
            generated = True
            m0 = m0_by_ion.get(str(ion_id))
            if not _paired_to(m0, sample_peak_id):
                continue
            fit = score_or_none(fit_by_ion.get(str(ion_id)))
            if fit is None:
                continue
            mz_error_ppm = finite_or_none(m0.get("match_mz_error"))
            candidates.append(
                {
                    "ionization_mechanism_id": mechanism.ionization_mechanism_id,
                    "ionization_mechanism": mechanism.ionization_mechanism,
                    "ion_formula": m0.get("target_ion_formula"),
                    "fit_score": fit,
                    "mz_error_ppm": mz_error_ppm,
                    "abundance_error": finite_or_none(m0.get("match_abundance_error")),
                    # Recomputed from the fit rather than multiplied here, so a
                    # formula that will not parse reads as the fit alone - the
                    # same fail-open every other caller of `evidence_for` gets.
                    "evidence": evidence_for(fit, formula),
                }
            )
        entry = {
            "alternative_index": index,
            "assigned_formula": formula,
            "plausibility": plausibility,
            "adducts_tried": len(mechanism_by_id),
        }
        if not candidates:
            entry["blocked_reason"] = _blocked_reason(len(mechanism_by_id), generated)
            data.append(entry)
            continue
        candidates.sort(
            key=lambda c: (
                -(c["evidence"] if c["evidence"] is not None else 0.0),
                abs(c["mz_error_ppm"])
                if c["mz_error_ppm"] is not None
                else float("inf"),
                c["ionization_mechanism"] or "",
            )
        )
        best = candidates[0]
        entry.update(best)
        # M0 by construction: the shortlist proposes a composition for this
        # peak's own mass, and only the ion's monoisotopic peak was allowed to
        # pair with it above. Said out loud because `set_assignment` reads it
        # to decide whether the committed row is a compound or a satellite.
        entry["isotope_label"] = "M0"
        entry["adducts_matched"] = len(candidates)
        data.append(entry)

    # Anything past the cap is reported as unmeasured rather than left out of
    # the response. Omitting it would reach the card as an ordinary blocked
    # entry - "no adduct puts this on the peak" - which is a claim about the
    # chemistry that nothing here actually checked.
    for index, formula in over_cap:
        data.append(
            {
                "alternative_index": index,
                "assigned_formula": formula,
                "plausibility": round(float(formula_plausibility(formula)), 4),
                "adducts_tried": 0,
                "blocked_reason": (
                    f"This peak has more alternatives than one request measures "
                    f"({MAX_SCORED_FORMULAS}), and this one was not reached, so "
                    "nothing here says whether it fits."
                ),
            }
        )

    scored = sum(1 for entry in data if "fit_score" in entry)
    return {
        "status": "success",
        "message": (
            f"Measured {len(data)} formula-only "
            f"alternative{'s' if len(data) != 1 else ''} against peak "
            f"{sample_peak_id}; {scored} could be scored."
        ),
        "results": len(data),
        "data": data,
    }
