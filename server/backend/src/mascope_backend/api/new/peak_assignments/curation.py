"""
Manual curation of a peak assignment.

The write path behind "use this" on a close alternative and "assign to peak" on
a re-search hit: a person replaces the engine's winner on one ledger row, and
the change persists, marked as human-made.

An override is deliberately **run-local**. It edits a row of the run it names
and nothing else, and a later engine run rebuilds that sample's ledger from the
data and supersedes it. That is the intended lifetime: the durable memory of a
human judgement is the verification layer, which is append-only, keyed on the
peak rather than on a run, and survives re-runs by design.

Three rules keep an edited row honest beside the engine's own:

- **The tier is recomputed, never inherited or accepted.** It is derived here
  with :func:`engine.tier_for_score` under the run's own ``tier_bands``, so a
  curated row sorts, filters and rolls up against the same yardstick as every
  other row of that run.
- **The calibrated fields do not survive.** ``p_correct``, ``calibrated``,
  ``calibration`` and ``corroboration`` are this server's judgement about the
  arbitration that produced the *previous* winner; carried across they would
  read as a calibrated probability for a formula the calibration never saw.
  They are archived inside the override's own record and dropped from the row.
- **Nothing is thrown away.** The previous winner moves to the head of
  ``alternatives`` and is repeated verbatim in ``provenance.manual.previous``,
  so an override can be read back, audited, and undone by hand.
"""

from datetime import datetime as dt
from datetime import timezone

from sqlalchemy import select

from mascope_backend.api.controllers.samples.lib.samples_fetch import fetch_sample
from mascope_backend.api.lib.api_features import api_controller
from mascope_backend.api.lib.exceptions.api_exceptions import (
    ApiException,
    NotFoundException,
)
from mascope_backend.api.new.peak_assignments.config import (
    MAX_ALTERNATIVES_CEILING,
    PeakAssignmentConfig,
)
from mascope_backend.api.new.peak_assignments.engine import (
    ROLE_ISO_CHILD,
    ROLE_M0,
    ROLE_UNASSIGNED,
    SOURCE_MANUAL,
    tier_for_score,
)
from mascope_backend.api.new.peak_assignments.schemas import (
    PromoteAlternativeBody,
    SetAssignmentBody,
)
from mascope_backend.api.new.peak_assignments.tiers import (
    TIER_UNASSIGNED,
    normalize_tier_bands,
)
from mascope_backend.db import (
    IonizationMechanism,
    PeakAssignment,
    PeakAssignmentRun,
    TargetCompound,
    TargetIon,
    async_session,
)
from mascope_tools.composition.heuristic_filter import (
    SCORE_VERSION,
    formula_plausibility,
)


#: Run status a row may be curated in. A run that is still being computed or
#: assembled is being written by something else, and an edit made into it would
#: be overwritten by the writer without either side noticing.
COMPLETED_RUN_STATUS = "completed"

#: Provenance keys that describe the engine's arbitration and calibration of
#: the row's *previous* winner. They are archived with that winner and dropped
#: from the curated row - see the module docstring.
_ENGINE_JUDGEMENT_KEYS = (
    "p_correct",
    "calibrated",
    "calibration",
    "corroboration",
    "confidence",
    "n_candidates",
    "is_tie",
    "evidence",
    "reference_identities",
)

#: Where a curated row's numbers came from, recorded on the row so a reader
#: never has to guess whether a fit was measured by the run or handed over.
SCORED_BY_ALTERNATIVE = "run_alternative"
SCORED_BY_SEARCH = "composition_search"


def _plausibility_of(formula: str | None) -> float | None:
    """Graded chemical plausibility of a formula, or None when it has none.

    Fails open exactly as the engine's own use does: an unparseable formula
    scores 1.0 rather than raising, so this is never a validation gate.
    """
    if not formula:
        return None
    try:
        return round(float(formula_plausibility(formula)), 4)
    except Exception:  # plausibility must never decide whether a write happens
        return None


def _clean(mapping: dict) -> dict:
    """Drop the keys whose value is None, so archived blobs stay readable."""
    return {key: value for key, value in mapping.items() if value is not None}


def _previous_winner(assignment: PeakAssignment) -> dict | None:
    """Snapshot the row's current winner in the shape of an alternative.

    Returned in the ``alternatives`` shape so the displaced winner can be
    pushed straight back into that list and rendered by the inspector with no
    special case - and promoted back later, which is what makes an override
    undoable without a re-run.

    ``ionization_mechanism_id`` rides along even though the engine's own
    alternatives predate it: without the mechanism a re-promotion would restore
    the formula and lose the adduct.

    :param assignment: The row about to be overridden.
    :return: The winner as an alternative entry, or None when the row carried
        no assignment (an `unassigned` placeholder has no winner to displace).
    """
    if not assignment.assigned_formula:
        return None
    provenance = assignment.provenance or {}
    return _clean(
        {
            "assigned_formula": assignment.assigned_formula,
            "ion_formula": assignment.ion_formula,
            "ionization_mechanism_id": assignment.ionization_mechanism_id,
            "isotope_label": assignment.isotope_label,
            "isotope_formula": assignment.isotope_formula,
            "fit_score": assignment.fit_score,
            "mz_error_ppm": assignment.mz_error_ppm,
            "abundance_error": assignment.abundance_error,
            "plausibility": provenance.get("plausibility"),
            "target_compound_id": assignment.target_compound_id,
            "target_ion_id": assignment.target_ion_id,
            "role": assignment.role,
            "tier": assignment.tier,
            "source": assignment.source,
            # The engine's own reading of this winner, kept with it rather than
            # on the curated row: it describes an arbitration that is no longer
            # the row's, and stating it beside a human's formula would read as
            # a calibrated probability for a candidate nothing calibrated.
            "engine_judgement": _clean(
                {key: provenance.get(key) for key in _ENGINE_JUDGEMENT_KEYS}
            )
            or None,
        }
    )


def _push_alternative(existing: list | None, entry: dict | None) -> list | None:
    """Put a displaced winner at the head of a row's alternatives.

    Bounded by the same ceiling the run config is bounded by: the list sits in
    a JSON column on the highest-volume table, and repeated curation of one row
    would otherwise grow it without limit.
    """
    alternatives = list(existing or [])
    if entry is not None:
        alternatives.insert(0, entry)
    return alternatives[:MAX_ALTERNATIVES_CEILING] or None


def _run_bands(run: PeakAssignmentRun) -> tuple[float, float]:
    """The assigned/candidate fit-score thresholds this run tiered with.

    Read from ``tier_bands`` (through :func:`normalize_tier_bands`, since a row
    written before the rename names its upper band ``identified``), falling
    back to the thresholds in the run's stored config for runs predating that
    column, and to the engine defaults for a run that records neither. A
    curated row has to be tiered by the run's own yardstick or its tier means
    something different from every other tier in the same ledger.

    :param run: The run the curated row belongs to.
    :return: ``(assigned_threshold, candidate_threshold)``.
    """
    defaults = PeakAssignmentConfig()
    bands = normalize_tier_bands(run.tier_bands) or {}
    config = run.config or {}
    # The config's own pre-rename spelling of the upper threshold. Not covered
    # by `normalize_tier_bands`, which renames tier KEYS ('identified') and not
    # the config field that carries the same band under another name.
    assigned = bands.get(
        "assigned",
        config.get("assigned_threshold", config.get("identified_threshold")),
    )
    candidate = bands.get("candidate", config.get("candidate_threshold"))
    return (
        float(assigned if assigned is not None else defaults.assigned_threshold),
        float(candidate if candidate is not None else defaults.candidate_threshold),
    )


async def _resolve_mechanism_id(
    session, mechanism_id: str | None, target_ion_id: str | None
) -> str | None:
    """The ionization mechanism a curated winner is assigned under.

    Prefers what the candidate states, and falls back to the mechanism of its
    target ion - which is how an alternative written before alternatives
    carried the mechanism still promotes to a complete assignment.

    :raises ApiException: 422, when the stated mechanism does not exist. It is
        a foreign key on the row, so an unknown id would otherwise surface as a
        500 from the flush rather than as a verdict on the request.
    """
    if mechanism_id:
        if await session.get(IonizationMechanism, mechanism_id) is None:
            raise ApiException(
                "That ionization mechanism does not exist.",
                {"ionization_mechanism_id": mechanism_id},
                422,
            )
        return mechanism_id
    if target_ion_id:
        ion = await session.get(TargetIon, target_ion_id)
        if ion is not None:
            return ion.ionization_mechanism_id
    return None


async def _surviving_target_ids(
    session, target_compound_id: str | None, target_ion_id: str | None
) -> tuple[str | None, str | None]:
    """Keep a candidate's target links only while the targets still exist.

    The ids come out of a JSON blob written when the run was computed, so the
    rows behind them may have been deleted since. They are foreign keys on the
    assignment, so writing a stale one raises from the flush; dropping it keeps
    the override a verdict on chemistry rather than on the target library's
    history.
    """
    if target_compound_id is not None:
        if await session.get(TargetCompound, target_compound_id) is None:
            target_compound_id = None
    if target_ion_id is not None:
        if await session.get(TargetIon, target_ion_id) is None:
            target_ion_id = None
    return target_compound_id, target_ion_id


def _role_for(isotope_label: str | None) -> str:
    """M0 unless the candidate says this peak is one of the ion's satellites.

    A candidate labelled 'M+1' is a claim about a satellite, and committing it
    as an M0 would enter the compound's *satellite* into the ledger as the
    compound's main peak - which every consumer that folds a family onto its M0
    would then believe.
    """
    if isotope_label and isotope_label != "M0":
        return ROLE_ISO_CHILD
    return ROLE_M0


def _manual_provenance(
    action: str,
    scored_by: str,
    user_id: int | None,
    previous: dict | None,
    plausibility: float | None,
    fit_score: float | None,
) -> dict:
    """The provenance a curated row carries.

    Deliberately thin. ``plausibility`` is a property of the formula itself, so
    it is honest for any winner however it was chosen, and ``evidence`` is the
    engine's own definition of it (fit x plausibility) computed from numbers
    that are on the record - the quantity a verification snapshots as its
    calibration label. Everything the calibration layer *derives* is absent, so
    a curated row is never mistaken for a calibrated one.
    """
    evidence = (
        round(fit_score * plausibility, 4)
        if fit_score is not None and plausibility is not None
        else None
    )
    return _clean(
        {
            "plausibility": plausibility,
            "evidence": evidence,
            "score_version": SCORE_VERSION,
            "manual": _clean(
                {
                    "action": action,
                    "scored_by": scored_by,
                    "user_id": user_id,
                    "at": dt.now(timezone.utc).isoformat(),
                    "previous_formula": (previous or {}).get("assigned_formula"),
                    "previous": previous,
                }
            ),
        }
    )


def _demote(child: PeakAssignment, user_id: int | None, owner_formula: str) -> None:
    """Strip an isotopologue satellite of a formula that is no longer its M0's.

    A satellite is not an independent finding - it is the same compound seen
    through one heavy atom - so once a person has rejected the formula it was a
    satellite of, there is nothing left for it to claim. It is demoted rather
    than deleted (the ledger holds one row per detected peak, always) and
    rather than left standing (its owner now carries a different compound, so
    the family would show two).

    ``source`` stays 'manual': the row's state is a person's doing, and the
    ledger's source filter has to show the whole footprint of an override, not
    only the row that gained a formula.
    """
    previous = _previous_winner(child)
    child.alternatives = _push_alternative(child.alternatives, previous)
    child.provenance = _clean(
        {
            "manual": _clean(
                {
                    "action": "demote_satellite",
                    "reason": "owner_overridden",
                    "user_id": user_id,
                    "at": dt.now(timezone.utc).isoformat(),
                    "previous_formula": (previous or {}).get("assigned_formula"),
                    "previous_owner_formula": owner_formula,
                    "previous": previous,
                }
            )
        }
    )
    child.role = ROLE_UNASSIGNED
    child.tier = TIER_UNASSIGNED
    child.source = SOURCE_MANUAL
    child.assigned_formula = None
    child.ion_formula = None
    child.ionization_mechanism_id = None
    child.isotope_label = None
    child.isotope_formula = None
    child.fit_score = None
    child.mz_error_ppm = None
    child.abundance_error = None
    child.target_compound_id = None
    child.target_ion_id = None
    child.owner_peak_assignment_id = None


def _chosen_from_alternative(
    assignment: PeakAssignment, index: int, expected_formula: str | None
) -> dict:
    """Pull one runner-up out of the row's alternatives, checked against what
    the caller believed it was reading.

    :raises ApiException: 422 when the index names nothing or the entry carries
        no formula to commit; 409 when the entry is not the one the caller saw.
    """
    alternatives = list(assignment.alternatives or [])
    if index >= len(alternatives):
        raise ApiException(
            f"This assignment has {len(alternatives)} close "
            f"alternative{'s' if len(alternatives) != 1 else ''}, so there is "
            f"none at position {index}.",
            {
                "peak_assignment_id": assignment.peak_assignment_id,
                "alternative_index": index,
                "alternatives": len(alternatives),
            },
            422,
        )
    chosen = dict(alternatives[index] or {})
    formula = chosen.get("assigned_formula")
    if not formula:
        raise ApiException(
            "That close alternative names no formula, so there is nothing to "
            "assign to the peak.",
            {
                "peak_assignment_id": assignment.peak_assignment_id,
                "alternative_index": index,
            },
            422,
        )
    if expected_formula is not None and expected_formula != formula:
        raise ApiException(
            f"The candidate at that position is now '{formula}', not "
            f"'{expected_formula}' - this assignment changed since you read "
            "it. Reload the peak and choose again.",
            {
                "peak_assignment_id": assignment.peak_assignment_id,
                "alternative_index": index,
                "expected_formula": expected_formula,
                "actual_formula": formula,
            },
            409,
        )
    return chosen


@api_controller()
async def curate_assignment(
    sample_item_id: str,
    peak_assignment_id: str,
    body: PromoteAlternativeBody | SetAssignmentBody,
    user_id: int | None = None,
) -> dict:
    """Replace one ledger row's winner by hand, in place.

    Both actions are the same edit with a different source for the winner:
    ``promote_alternative`` takes it from the row's own stored runner-ups (no
    numbers come from the caller at all), ``set_assignment`` takes it from a
    composition the caller names - the re-search case, where the peak's row is
    usually an `unassigned` placeholder with no runner-ups to promote.

    The row keeps its identity: same ``peak_assignment_id``, same
    ``sample_peak_id``, same peak. What changes is which composition it commits
    to, plus the bookkeeping that says a person changed it.

    :param sample_item_id: Sample the assignment belongs to.
    :param peak_assignment_id: The row to curate.
    :param body: A validated ``PromoteAlternativeBody`` or ``SetAssignmentBody``.
    :param user_id: The curating user, recorded in provenance.
    :return: Status envelope; ``data[0]`` is the curated row, followed by any
        satellite rows the override displaced.
    :raises NotFoundException: The assignment is not this sample's.
    :raises ApiException: 409 when the run is not completed (something else is
        still writing it) or the promoted candidate moved; 422 when the request
        names a candidate, mechanism or formula that cannot be committed.
    """
    sample = await fetch_sample(sample_item_id)

    async with async_session() as session:
        # Locked for the duration: two curators clicking the same row would
        # otherwise both archive the winner they read and the second would
        # bury the first's choice in alternatives with no sign anything raced.
        assignment = await session.get(
            PeakAssignment, peak_assignment_id, with_for_update=True
        )
        if assignment is None or assignment.sample_item_id != sample_item_id:
            raise NotFoundException(
                f"Assignment '{peak_assignment_id}' not found for sample "
                f"'{sample.sample_item_name}'"
            )

        run = await session.get(PeakAssignmentRun, assignment.peak_assignment_run_id)
        if run is None or run.status != COMPLETED_RUN_STATUS:
            raise ApiException(
                f"This assignment belongs to a run that is "
                f"'{run.status if run else 'gone'}', not completed. Wait for "
                "the run to finish before curating its ledger.",
                {
                    "peak_assignment_id": peak_assignment_id,
                    "peak_assignment_run_id": assignment.peak_assignment_run_id,
                    "run_status": run.status if run else None,
                },
                409,
            )

        if body.action == "promote_alternative":
            chosen = _chosen_from_alternative(
                assignment, body.alternative_index, body.expected_formula
            )
            scored_by = SCORED_BY_ALTERNATIVE
            # The promoted entry leaves the list; the displaced winner takes a
            # place at its head, so the row's candidate set is conserved.
            remaining = [
                entry
                for position, entry in enumerate(assignment.alternatives or [])
                if position != body.alternative_index
            ]
        else:
            chosen = _clean(
                {
                    "assigned_formula": body.assigned_formula,
                    "ion_formula": body.ion_formula,
                    "ionization_mechanism_id": body.ionization_mechanism_id,
                    "isotope_label": body.isotope_label,
                    "isotope_formula": body.isotope_formula,
                    "fit_score": body.fit_score,
                    "mz_error_ppm": body.mz_error_ppm,
                    "abundance_error": body.abundance_error,
                    "plausibility": body.plausibility,
                }
            )
            scored_by = SCORED_BY_SEARCH
            remaining = list(assignment.alternatives or [])

        mechanism_id = await _resolve_mechanism_id(
            session,
            chosen.get("ionization_mechanism_id"),
            chosen.get("target_ion_id"),
        )
        target_compound_id, target_ion_id = await _surviving_target_ids(
            session, chosen.get("target_compound_id"), chosen.get("target_ion_id")
        )

        previous = _previous_winner(assignment)
        previous_formula = assignment.assigned_formula
        # Satellites are read before the winner changes; after it, nothing on
        # the row says which compound they were satellites of.
        displaced = (
            (
                await session.execute(
                    select(PeakAssignment)
                    .where(
                        PeakAssignment.owner_peak_assignment_id == peak_assignment_id
                    )
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )

        assigned = chosen.get("assigned_formula")
        # Plausibility of the committed formula, computed here rather than
        # trusted: it is a pure function of the formula, so there is no reason
        # to carry a number a caller could have made up.
        plausibility = _plausibility_of(assigned)
        fit_score = chosen.get("fit_score")
        assigned_band, candidate_band = _run_bands(run)

        assignment.alternatives = _push_alternative(remaining, previous)
        assignment.provenance = _manual_provenance(
            action=body.action,
            scored_by=scored_by,
            user_id=user_id,
            previous=previous,
            plausibility=plausibility,
            fit_score=fit_score,
        )
        assignment.assigned_formula = assigned
        assignment.ion_formula = chosen.get("ion_formula")
        assignment.ionization_mechanism_id = mechanism_id
        assignment.isotope_label = chosen.get("isotope_label")
        assignment.isotope_formula = chosen.get("isotope_formula")
        assignment.fit_score = fit_score
        assignment.mz_error_ppm = chosen.get("mz_error_ppm")
        assignment.abundance_error = chosen.get("abundance_error")
        assignment.target_compound_id = target_compound_id
        assignment.target_ion_id = target_ion_id
        assignment.role = _role_for(chosen.get("isotope_label"))
        assignment.source = SOURCE_MANUAL
        # Keyword arguments on purpose: `tier_for_score` takes the CANDIDATE
        # band as `possible_threshold` and the ASSIGNED band as
        # `probable_threshold`, so a positional call in band order inverts them
        # silently.
        assignment.tier = tier_for_score(
            fit_score,
            possible_threshold=candidate_band,
            probable_threshold=assigned_band,
        )
        # The curated row stands for its formula on its own: the run arbitrated
        # a family for the composition it replaced, not for this one, so there
        # is no owner here that was ever competed for.
        assignment.owner_peak_assignment_id = None

        for child in displaced:
            _demote(child, user_id, previous_formula)

        # Read out before the commit expires these instances. A refresh per row
        # would be the alternative, and an expired attribute read on an async
        # session is a lazy load - which raises rather than reloading.
        sample_peak_id = assignment.sample_peak_id
        records = [assignment.to_dict()] + [child.to_dict() for child in displaced]

        await session.commit()

    displaced_note = (
        f" {len(displaced)} isotopologue satellite"
        f"{'s' if len(displaced) != 1 else ''} of "
        f"'{previous_formula}' demoted to unassigned."
        if displaced
        else ""
    )
    return {
        "status": "success",
        "message": (
            f"Assigned '{assigned}' to peak {sample_peak_id} of sample "
            f"'{sample.sample_item_name}' by hand"
            f"{f' (was {previous_formula!r})' if previous_formula else ''}."
            f"{displaced_note}"
        ),
        "results": len(records),
        "data": records,
    }
