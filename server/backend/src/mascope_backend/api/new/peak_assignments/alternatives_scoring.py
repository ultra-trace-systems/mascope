"""Score an untargeted row's formula-only alternatives, on demand.

Stage B stores two kinds of entry in a row's ``alternatives``. The losing
contenders for the peak are scored - they were competed against the winner and
carry a fit, a mass error and the adduct they were found under. The finder's
``other_candidates`` shortlist is not: it is the raw list of compositions whose
mass fits the peak, frozen before the heuristic filter and the isotope-pattern
ranking chose a winner, and it reaches the row as a formula and a chemical
plausibility and nothing else. Scoring every one of them during a run would
mean an isotope-envelope match per candidate per peak across a whole sample,
which is why a run does not do it by default.

The inspector is a different budget. One peak at a time, one shortlist of at
most the run's ``max_alternatives`` formulas, and a peak read that
``compute_match_isotopes`` narrows to the seeded m/z windows - so the same
measurement the run declined to make wholesale is cheap to make when somebody
is actually looking at the peak.

A run can afford the same measurement once it is bounded, and takes it as an
option (``PeakAssignmentConfig.stage_b_confidence``, step 4.15 of the
assignment quality plan): :func:`measure_winner_confidence` measures the
shortlists of the run's brightest untargeted winners, up to a per-run budget,
in one seeded pass, and records each winner's arbitration confidence and tie
flag in its provenance, as Stage A records them. Both paths place a formula on
its peak through :func:`placements`, so the inspector and the run cannot read
one seeded pass two ways.

What the inspector gets back is a measurement, not a record: its endpoint
writes nothing, and the scores are never stored onto the run's rows. A run is
the append-only account of what the engine did, and these numbers are not
something it did. A run that measured its own shortlists stores the share they
make, not the fits either: the alternatives stay formula-only. Committing
one of these candidates is therefore a ``set_assignment`` - the same action the
re-search hand button uses, which records in provenance that the numbers came
from a composition search rather than from the run's own arbitration.
"""

import re
import time

import pandas as pd

from mascope_backend.api.controllers.samples.lib.samples_fetch import fetch_sample
from mascope_backend.api.lib.api_features import api_controller
from mascope_backend.api.lib.exceptions.api_exceptions import NotFoundException
from mascope_backend.api.new.match.params import default_match_params
from mascope_backend.api.new.peak_assignments.engine import (
    ROLE_M0,
    SOURCE_UNTARGETED,
    evidence_for,
    formula_identity,
    monoisotopic_row,
)
from mascope_backend.api.new.peak_assignments.fold_view import is_fold_id
from mascope_backend.api.new.peak_assignments.seeded_scoring import (
    finite_or_none,
    score_or_none,
    score_seeds,
)
from mascope_backend.api.new.peak_assignments.service import fetch_sample_mechanisms
from mascope_backend.db import PeakAssignment, async_session
from mascope_tools.composition.arbitration import arbitrate_candidates
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
    same reference the engine's roles and labels count from
    (``engine.monoisotopic_row``): the isotopologue whose formula names no
    isotope beyond the ion's own label, or the lightest where the frame carries
    no formula; reading it positionally would depend on frame order, which the
    matcher does not promise.

    :param scored_df: The gated, fit-scored seeded frame.
    :return: ion id -> its monoisotopic row.
    """
    if scored_df.empty or "target_ion_id" not in scored_df.columns:
        return {}
    return {
        str(ion_id): monoisotopic_row(group)
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


def placements(
    formula: str,
    mechanisms: list,
    *,
    ion_by_seed: dict[tuple[str, str], str],
    fit_by_ion: dict[str, float | None],
    m0_by_ion: dict[str, pd.Series],
    sample_peak_id: str,
) -> tuple[bool, list[dict]]:
    """Every adduct that puts one shortlist formula on one observed peak.

    The measurement the inspector makes of one entry and a run makes of every
    entry it measures (:func:`measure_winner_confidence`), written once so the
    two cannot read the same seeded pass differently: an adduct counts only
    where its ion's monoisotopic row paired to the peak and carries a fit.

    :param formula: The shortlist formula, as stored.
    :param mechanisms: The adducts it was seeded under.
    :param ion_by_seed: ``(formula, mechanism id)`` -> ion id, from the pass.
    :param fit_by_ion: Ion id -> fit, from the pass.
    :param m0_by_ion: Ion id -> its monoisotopic row (:func:`_m0_by_ion`).
    :param sample_peak_id: The peak the formula is proposed for.
    :return: Whether any adduct turned the formula into an ion at all, and one
        entry per adduct that placed it on the peak, best evidence first, then
        the smaller mass error, then the adduct's name.
    """
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
        candidates.append(
            {
                "ionization_mechanism_id": mechanism.ionization_mechanism_id,
                "ionization_mechanism": mechanism.ionization_mechanism,
                "ion_formula": m0.get("target_ion_formula"),
                "fit_score": fit,
                "mz_error_ppm": finite_or_none(m0.get("match_mz_error")),
                "abundance_error": finite_or_none(m0.get("match_abundance_error")),
                # Recomputed from the fit rather than multiplied here, so a
                # formula that will not parse reads as the fit alone - the
                # same fail-open every other caller of `evidence_for` gets.
                "evidence": evidence_for(fit, formula),
            }
        )
    candidates.sort(
        key=lambda c: (
            -(c["evidence"] if c["evidence"] is not None else 0.0),
            abs(c["mz_error_ppm"]) if c["mz_error_ppm"] is not None else float("inf"),
            c["ionization_mechanism"] or "",
        )
    )
    return generated, candidates


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
    if is_fold_id(peak_assignment_id):
        # A derived row has no formula-only shortlist of its own: its
        # alternatives are the other identities its anchor's members carried,
        # each already scored in the sample it came from.
        return {
            "status": "success",
            "message": (
                f"Assignment '{peak_assignment_id}' is derived from the batch "
                "ledger; it has no formula-only alternatives to measure."
            ),
            "results": 0,
            "data": [],
        }
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
        generated, candidates = placements(
            formula,
            mechanisms,
            ion_by_seed=ion_by_seed,
            fit_by_ion=fit_by_ion,
            m0_by_ion=m0_by_ion,
            sample_peak_id=sample_peak_id,
        )
        entry = {
            "alternative_index": index,
            "assigned_formula": formula,
            "plausibility": round(float(formula_plausibility(formula)), 4),
            "adducts_tried": len(mechanism_by_id),
        }
        if not candidates:
            entry["blocked_reason"] = _blocked_reason(len(mechanism_by_id), generated)
            data.append(entry)
            continue
        best = candidates[0]
        entry.update(best)
        # M0 by construction: the shortlist proposes a composition for this
        # peak's own mass, and only the ion's monoisotopic peak was allowed to
        # pair with it above. Said out loud because `set_assignment` reads it
        # to decide whether the committed row is a compound or an isotopologue.
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


#: Where a run records its winner-confidence pass, inside its ``search_scope``:
#: the untargeted stage's record of what it searched, which this pass extends.
WINNER_CONFIDENCE_KEY = "winner_confidence"


def stage_b_winners(rows: list[dict]) -> list[dict]:
    """The committed untargeted winners a confidence is read for.

    The monoisotopic rows only: an isotopologue was predicted from its owner,
    not searched, so there was no field of candidates for it to win against,
    and Stage A records none on one either.

    :param rows: A run's committed rows.
    :return: Its untargeted M0 rows that commit a formula, in ledger order.
    """
    return [
        row
        for row in rows
        if row.get("source") == SOURCE_UNTARGETED
        and row.get("role") == ROLE_M0
        and row.get("assigned_formula")
    ]


def recorded_rivals(row: dict) -> list[tuple[str, float, str | None]]:
    """The candidates a row's alternatives already carry a fit for.

    The losing contenders Stage B competed for the peak and the list readings
    it displaced, each with the fit it was measured at and the ion it was read
    as. A reading of the winner's own ion (``same_ion``) is not among them: it
    is the winner's measurement split another way between analyte and adduct,
    which the finder elects before anything is ranked, so it is no competitor
    here either (``arbitration.candidate_density``).

    :param row: A committed row.
    :return: ``(formula, fit, ion formula)`` per such alternative, in stored
        order; the ion is None where the entry names none.
    """
    rivals: list[tuple[str, float, str | None]] = []
    for entry in row.get("alternatives") or []:
        if not isinstance(entry, dict) or entry.get("same_ion"):
            continue
        formula = entry.get("assigned_formula")
        fit = score_or_none(entry.get("fit_score"))
        if not formula or not isinstance(formula, str) or fit is None:
            continue
        ion = entry.get("ion_formula")
        rivals.append((formula, fit, ion if isinstance(ion, str) else None))
    return rivals


def ion_identity(ion_formula: str | None) -> tuple[str, str] | None:
    """An ion's identity, however its neutral and adduct were split.

    ``C10H18O3`` read through ``[M+Br]-`` and ``C10H19BrO3`` read through
    ``[M-H]-`` are one ion, ``C10H18BrO3-``: the same mass and the same
    envelope, so the spectrum cannot tell them apart and one of them is no
    rival of the other. Read as a composition and a charge, so two spellings
    of one ion agree.

    :param ion_formula: An ion formula with its charge, such as ``C6H11O2-``.
        Its charge is the signs it ends in, as the engine writes it.
    :return: ``(composition, charge)``, or None where there is no ion named.
    """
    if not ion_formula or not isinstance(ion_formula, str):
        return None
    text = ion_formula.strip()
    match = _CHARGE.search(text)
    charge = match.group(0) if match else ""
    return formula_identity(text[: match.start()] if match else text), charge


#: The charge an ion formula ends in: its signs, one per charge (``-``,
#: ``++``), never a count, which would read as the last element's
#: (``target_ions_compute.charge_string``).
_CHARGE = re.compile(r"[+-]+$")


def winner_arbitration(
    row: dict, measured: list[tuple[str, float, str | None]]
) -> dict:
    """A winner's confidence among its peak's scored candidates, as Stage A has it.

    The same three provenance keys Stage A writes, from the same
    ``arbitrate_candidates``: the winner's share of the evidence
    (``confidence``), how many distinct candidates it was shared across
    (``n_candidates``), and whether the evidence could not separate it from the
    best of them (``is_tie``).

    A candidate is an ION on the peak, as it is to the finder: readings of one
    ion are one hypothesis about the peak (:func:`ion_identity`). One that
    reads the winner's own ion is the winner and does not compete with it; of
    the readings of any other ion, the one with the best evidence stands for
    it, so two splits of one ion neither share its weight nor tie with each
    other. The shortlist carries such pairs: it lists every composition whose
    mass fits, under every adduct of the search. Formulas are compared as
    compositions (:func:`engine.formula_identity`), so one neutral spelled two
    ways is one hypothesis too.

    The share is read, not acted on: a winner a measured entry outweighs keeps
    its peak and its tier, and says so by a confidence below the rival's.

    :param row: A committed untargeted winner.
    :param measured: ``(formula, fit, ion formula)`` of each shortlist entry
        the pass placed on the row's peak.
    :return: ``confidence``, ``n_candidates`` and ``is_tie``.
    """
    winner = formula_identity(row["assigned_formula"])
    winner_ion = ion_identity(row.get("ion_formula"))
    best_by_ion: dict[tuple, tuple[str, float, float]] = {}
    for formula, fit, ion in recorded_rivals(row) + measured:
        identity = formula_identity(formula)
        key = ion_identity(ion) or ("formula", identity)
        if winner_ion is not None and key == winner_ion:
            continue
        evidence = fit * float(formula_plausibility(identity))
        held = best_by_ion.get(key)
        if held is None or (evidence, identity) > (held[2], held[0]):
            best_by_ion[key] = (identity, fit, evidence)
    candidates = [(winner, score_or_none(row.get("fit_score")) or 0.0)] + [
        (identity, fit) for identity, fit, _evidence in best_by_ion.values()
    ]
    arbitrated = arbitrate_candidates(candidates)
    mine = next(c for c in arbitrated if c.formula == winner)
    return {
        "confidence": round(mine.confidence, 4),
        "n_candidates": len(arbitrated),
        "is_tie": mine.is_tie,
    }


def _shortlist(row: dict) -> list[str]:
    """The formulas of a row's shortlist the run has not measured, once each.

    The inspector's selection (:func:`unscorable_alternatives`) less any entry
    that already carries a fit: a contender whose adduct the run could not name
    was still measured, and is one of :func:`recorded_rivals`.
    """
    alternatives = row.get("alternatives") or []
    return list(
        dict.fromkeys(
            formula
            for index, formula in unscorable_alternatives(alternatives)
            if score_or_none(alternatives[index].get("fit_score")) is None
        )
    )


async def measure_winner_confidence(
    sample,
    rows: list[dict],
    mechanisms: list,
    match_params,
    *,
    peak_budget: int,
    reagent_offset,
    envelope_tolerance,
    accuracy,
) -> dict:
    """Measure the shortlists of a run's untargeted winners and record a confidence.

    The run-level form of :func:`score_row_alternatives`: the same shortlist
    (:func:`unscorable_alternatives`, so at most the run's
    ``max_alternatives``), each formula seeded under every adduct the run
    searched and placed on the peak by the same rule (:func:`placements`). What
    the inspector does for one peak on request this does for the run's
    winners, the most intense first, up to ``peak_budget`` of them, in ONE
    seeded pass, so the cost is one ``compute_match_isotopes`` read however
    many winners it covers.

    The pass is scored at the measurement the winners' own fits were scored
    at (``accuracy``): the run's seeded re-score's, which was fitted on the
    committed winners. So the winner and its rivals are judged at one width,
    as Stage A judges its candidates inside one frame. It is never one
    fitted off the seeds: a shortlist is mostly wrong formulas, and a frame of
    those fitted on its own lines would judge every one of them against the
    width their chance matches happened to make.

    A winner whose shortlist the budget did not reach records nothing: a share
    taken of part of the field would read as a share of all of it. A winner
    with no shortlist records its share among the rivals its row already
    measured, which costs nothing. Only provenance is written; the tier, the
    fit and the alternatives stay what the run decided.

    :param sample: The sample being assigned.
    :param rows: The run's committed rows; winners' provenance is updated in
        place.
    :param mechanisms: The adducts a shortlist formula is tried under: every
        one the run searched, the secondary channels it opened included, since
        the finder proposed the shortlist through them all. In a decoy arm
        that swaps adducts, the swapped set.
    :param match_params: The sample's resolved match parameters.
    :param peak_budget: How many winners' shortlists to measure.
    :param reagent_offset: The run's scoring centre, as its re-score takes it.
    :param envelope_tolerance: The tolerance the run fitted.
    :param accuracy: The measurement the winners' fits were scored at, which
        the pass is scored at (``seeded_scoring.scored_accuracy``).
    :return: What the pass did, for the run's record.
    """
    started = time.perf_counter()
    winners = stage_b_winners(rows)
    shortlists = {id(row): _shortlist(row) for row in winners}
    # Peaks above a signal rank first: the budget goes to the winners a reader
    # is likeliest to look at, and a dim peak's shortlist is the one whose
    # envelope the spectrum can least tell apart anyway.
    waiting = sorted(
        (row for row in winners if shortlists[id(row)]),
        key=lambda row: (
            -(finite_or_none(row.get("sample_peak_intensity")) or 0.0),
            str(row.get("sample_peak_id")),
        ),
    )
    measured_rows = waiting[:peak_budget]
    unreached = {id(row) for row in waiting[peak_budget:]}

    seeds = {
        (formula, mechanism.ionization_mechanism_id)
        for row in measured_rows
        for formula in shortlists[id(row)]
        for mechanism in mechanisms
    }
    ion_by_seed, fit_by_ion, _errors, scored_df = await score_seeds(
        sample,
        seeds,
        match_params,
        reagent_offset=reagent_offset,
        envelope_tolerance=envelope_tolerance,
        inherited_accuracy=accuracy,
    )
    m0_by_ion = _m0_by_ion(scored_df)

    placed = 0
    ties = 0
    for row in winners:
        if id(row) in unreached:
            continue
        measured: list[tuple[str, float, str | None]] = []
        for formula in shortlists[id(row)]:
            _generated, candidates = placements(
                formula,
                mechanisms,
                ion_by_seed=ion_by_seed,
                fit_by_ion=fit_by_ion,
                m0_by_ion=m0_by_ion,
                sample_peak_id=row["sample_peak_id"],
            )
            if candidates:
                best = candidates[0]
                measured.append((formula, best["fit_score"], best["ion_formula"]))
        placed += len(measured)
        arbitration = winner_arbitration(row, measured)
        ties += arbitration["is_tie"]
        row["provenance"] = {**(row.get("provenance") or {}), **arbitration}

    return {
        "peak_budget": peak_budget,
        "winners": len(winners),
        "measured": len(measured_rows),
        "unreached": len(unreached),
        "formulas": sum(len(shortlists[id(row)]) for row in measured_rows),
        "placed": placed,
        "ties": ties,
        "seconds": round(time.perf_counter() - started, 2),
    }
