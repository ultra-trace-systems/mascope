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
  with :func:`engine.tier_for_evidence` under the run's own ``tier_bands``, so a
  curated row sorts, filters and rolls up against the same yardstick as every
  other row of that run. A monoisotopic row is then asked the one rule of the
  tiering pass that is about the spectrum rather than about how its formula
  was elected: whether the lines its envelope predicts are there
  (:func:`tiering.ask_absent_line`, step 3.4e), at the gate the run asked its
  own rows at. The others weigh rivals, channels and lists, which a person's
  choice is not; a missing line is missing whoever named the formula.
- **The engine's reading of the old winner does not survive.** All nine keys
  of :data:`_ENGINE_JUDGEMENT_KEYS` - ``p_correct``, ``calibrated``,
  ``calibration``, ``corroboration``, ``confidence``, ``n_candidates``,
  ``is_tie``, ``evidence`` and ``reference_identities`` - are this server's
  record of the arbitration that produced the *previous* winner; carried
  across they would read as a calibrated probability, an arbitration and a
  known compound's name for a formula none of them describes. A curated row's
  provenance is rebuilt from the candidate being committed rather than edited,
  so nothing of the old blob is inherited and those nine are archived with the
  winner they describe. Two are then re-established for the *new* winner out of
  its own record: ``evidence`` is recomputed here from the committed fit and
  plausibility, and ``reference_identities`` is whatever known-compound names
  the committed candidate carried. That last one is why the displaced winner's
  snapshot repeats it outside ``engine_judgement`` as well - it names the
  winner's own formula rather than judging it, and without the copy promoting
  that winner back would restore the formula and silently lose its identity.
- **Nothing is thrown away.** The previous winner moves to the head of
  ``alternatives`` and is repeated verbatim in ``provenance.manual.previous``,
  so an override can be read back, audited, and undone by hand. The
  isotopologue rows an override strips are archived next to it in
  ``provenance.manual.demoted``, and committing their compound back onto the
  row puts them back on their own rows. That is what makes promoting the
  previous winner a real undo rather than half of one: without it the M0 would
  return to its formula while its family stayed behind as orphaned unassigned
  peaks that only a full re-run could re-attach.
"""

import asyncio
import math
from datetime import datetime as dt
from datetime import timezone
from typing import NamedTuple

import pandas as pd
from sqlalchemy import select

from mascope_backend.api.controllers.samples.lib.samples_fetch import fetch_sample
from mascope_backend.api.lib.api_features import api_controller
from mascope_backend.api.lib.exceptions.api_exceptions import (
    ApiException,
    NotFoundException,
)
from mascope_backend.api.new.ionization.modes.versions import sample_label_purity
from mascope_backend.api.new.peak_assignments.config import (
    IN_APP_ENGINE,
    MAX_ALTERNATIVES_CEILING,
    PeakAssignmentConfig,
)
from mascope_backend.api.new.peak_assignments.curation_message import (
    EditOutcome,
    edit_message,
)
from mascope_backend.api.new.peak_assignments.engine import (
    evidence_for,
    plausibility_for,
    tier_for_evidence,
)
from mascope_backend.api.new.peak_assignments.fold_view import (
    DerivedAssignmentReadOnlyException,
    is_fold_id,
)
from mascope_backend.api.new.peak_assignments.hand_claims import (
    claim_found_lines,
    restored_source,
)
from mascope_backend.api.new.peak_assignments.hand_lines import (
    detach_hand_lines,
    link_hand_lines,
)
from mascope_backend.api.new.peak_assignments.identification_level import (
    IDENTIFICATION_LEVEL,
    inherited_level,
    is_level,
)
from mascope_backend.api.new.peak_assignments.model_readers import (
    provenance_with_calibration,
)
from mascope_backend.api.new.peak_assignments.pipeline import (
    PERSONS_CHOICE,
    RunContext,
    run_stages,
)
from mascope_backend.api.new.peak_assignments.row_terms import (
    ROLE_ARTIFACT,
    ROLE_ISO_CHILD,
    ROLE_M0,
    ROLE_UNASSIGNED,
    SOURCE_MANUAL,
    TIERING_KEY,
)
from mascope_backend.api.new.peak_assignments.sample_inputs import load_sample_peaks
from mascope_backend.api.new.peak_assignments.schemas import (
    PromoteAlternativeBody,
    SetAssignmentBody,
)
from mascope_backend.api.new.peak_assignments.tiering import LineGate
from mascope_backend.api.new.peak_assignments.tiers import (
    TIER_UNASSIGNED,
    TIERS,
    normalize_tier,
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
from mascope_backend.runtime import runtime
from mascope_match import shifted_mz_axis
from mascope_tools.composition.custom_elements import labelled_purity
from mascope_tools.composition.heuristic_filter import SCORE_VERSION


#: Run status a row may be curated in. A run that is still being computed or
#: assembled is being written by something else, and an edit made into it would
#: be overwritten by the writer without either side noticing.
COMPLETED_RUN_STATUS = "completed"

#: Provenance keys that describe the engine's arbitration and calibration of
#: the row's *previous* winner. They are archived with that winner and none of
#: them is inherited by the curated row - see the module docstring.
#: ``reference_identities`` is the odd one out and is deliberately archived
#: twice: it names the winner's own formula rather than judging it, so
#: :func:`_previous_winner` also repeats it at the top level of the snapshot,
#: where a re-promotion of that winner reads it back.
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

#: How many demoted isotopologues one row's archive keeps for restoring. An
#: isotopologue family is a handful of peaks, so this is far above any real
#: one; the bound exists because the archive lives in a JSON column on the
#: highest-volume table and an imported run can point any number of rows at a
#: single owner. An isotopologue past the bound simply stays demoted - its own row
#: still records what it was, so nothing is lost, but putting it back is a
#: re-run rather than a click.
MAX_DEMOTED_ARCHIVE = 32

#: The action `_demote` writes on an isotopologue row it strips. A restore only
#: touches a row whose action still records a demotion (:data:`DEMOTE_ACTIONS`),
#: so the value is compared, not just written - see :func:`_restore_demoted`.
ACTION_DEMOTE_ISOTOPOLOGUE = "demote_isotopologue"

#: Every action value that records a demotion. Rows demoted by earlier builds
#: carry ``demote_satellite``, a name retired because "satellite" is this
#: codebase's word for a signal artifact (an FT side lobe), never for an
#: isotopologue. A restore compared against the current name alone would read
#: each of those rows as curated by hand since and leave it demoted.
DEMOTE_ACTIONS = frozenset({ACTION_DEMOTE_ISOTOPOLOGUE, "demote_satellite"})


def _clean(mapping: dict) -> dict:
    """Drop the keys whose value is None, so archived blobs stay readable."""
    return {key: value for key, value in mapping.items() if value is not None}


def _engine_verdict(entry: dict) -> tuple[str | None, str | None]:
    """The producing engine's tier and level an archived winner carries.

    Unrecognised values are dropped, as a tier restore drops them: a snapshot
    is client-shaped data once it has been through an export, and a bad value
    must not reach a column the filters read. An entry with neither, an
    engine's own alternative, gives two Nones.

    :param entry: An alternatives entry, as :func:`_previous_winner` wrote it.
    :return: ``(engine_tier, engine_level)``.
    """
    tier = normalize_tier(entry.get("engine_tier"))
    level = entry.get("engine_level")
    return (tier if tier in TIERS else None, level if is_level(level) else None)


def _previous_winner(assignment: PeakAssignment) -> dict | None:
    """Snapshot the row's current winner in the shape of an alternative.

    Returned in the ``alternatives`` shape so the displaced winner can be
    pushed straight back into that list and rendered by the inspector with no
    special case - and promoted back later, which is what makes an override
    undoable without a re-run.

    ``ionization_mechanism_id`` rides along even though the engine's own
    alternatives predate it: without the mechanism a re-promotion would restore
    the formula and lose the adduct. ``reference_identities`` rides along at the
    top level for the same reason and against the same failure: it is also
    inside ``engine_judgement``, but only the top level is where the promotion
    path looks - :func:`_validated_candidate` carries it from there and
    :func:`_manual_provenance` writes it back onto the row - so buried it alone
    the undo would restore the formula and lose the known compound's name.

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
            "reference_identities": provenance.get("reference_identities"),
            "target_compound_id": assignment.target_compound_id,
            "target_ion_id": assignment.target_ion_id,
            "role": assignment.role,
            "tier": assignment.tier,
            # The producing engine's own verdict is archived with the winner it
            # judged, for the same reason the nine `engine_judgement` keys are:
            # it describes the formula being displaced, not the one a person is
            # committing. Left on the curated row it would report an engine
            # disagreeing about a formula that engine never saw - and it would
            # do so on exactly the rows someone looked at hardest.
            "engine_tier": assignment.engine_tier,
            # The engine's level is the same kind of verdict and goes with it.
            "engine_level": assignment.engine_level,
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
    """The assigned/candidate evidence thresholds this run tiered with.

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
    session,
    mechanism_id: str | None,
    target_ion_id: str | None,
    polarity: str | None,
    where: dict,
) -> str:
    """The ionization mechanism a curated winner is assigned under.

    Prefers what the candidate states, and falls back to the mechanism of its
    target ion - which is how an alternative written before alternatives
    carried the mechanism still promotes to a complete assignment.

    A candidate that yields neither is refused rather than committed
    adductless. ``SetAssignmentBody`` already makes the mechanism mandatory on
    the other action, for the reason that governs both: a formula without its
    adduct is half an assignment, and the mechanism is part of a verification's
    identity (sample peak + formula + mechanism), so a row that lacks one can
    only ever carry an incomplete verdict. Refusing here is what makes the two
    actions agree instead of one of them enforcing a rule the other lets
    through. The entries this catches are the untargeted stage's
    ``other_candidates`` shortlist, which is formula names and a plausibility
    and nothing else; the way to commit such a formula is the re-search hand
    button, which searches the composition against the sample's own adducts and
    so supplies one.

    The polarity rule is the import path's
    (``validate_reference_ids``): a mechanism of the wrong polarity is not an
    adduct this measurement could have produced, and the in-app engine satisfies
    that structurally by only ever searching the sample's own mechanisms.
    Nothing that reaches this function has that structural guarantee, so the
    checks run on the mechanism this **resolves to** rather than only on the one
    a caller stated. The fallback needs them just as much: ``alternatives`` is
    untyped JSON, an imported run's entries are whatever the publishing client
    sent, and a target compound ordinarily carries ions in both polarities - so
    a candidate naming a negative-mode ion is an ordinary way for an
    opposite-polarity adduct to reach a positive sample's column.

    :param where: Context merged into the error detail.
    :raises ApiException: 422, when the candidate's target ion is gone, when the
        resolved mechanism does not exist or does not match the sample, or when
        the candidate resolves to no mechanism at all. Existence matters because
        it is a foreign key on the row, so an unknown id would otherwise surface
        as a 500 from the flush rather than as a verdict on the request.
    """
    if not mechanism_id and target_ion_id:
        ion = await session.get(TargetIon, target_ion_id)
        if ion is None:
            # The frontend offers "use this" on a candidate that names an ion
            # without knowing whether the ion still exists, so this is a
            # reachable click and deserves its own answer: telling someone the
            # candidate "names no adduct" would be wrong about a candidate that
            # named one perfectly well until the target library moved on.
            raise ApiException(
                "That candidate was scored against a target ion that no longer "
                "exists, so the adduct it was found under cannot be read back. "
                "Search this peak's composition and assign the hit instead, "
                "which comes with an adduct of its own.",
                {**where, "target_ion_id": target_ion_id},
                422,
            )
        mechanism_id = ion.ionization_mechanism_id
    if not mechanism_id:
        raise ApiException(
            "That candidate names no adduct, and a formula without one is half "
            "an assignment - it cannot carry a verification. Search this peak's "
            "composition and assign the hit instead, which comes with the adduct "
            "the formula was found under.",
            {**where, "target_ion_id": target_ion_id},
            422,
        )
    mechanism = await session.get(IonizationMechanism, mechanism_id)
    if mechanism is None:
        raise ApiException(
            "That ionization mechanism does not exist.",
            {**where, "ionization_mechanism_id": mechanism_id},
            422,
        )
    if polarity and mechanism.ionization_mechanism_polarity != polarity:
        raise ApiException(
            f"Ionization mechanism "
            f"'{mechanism.ionization_mechanism}' is "
            f"'{mechanism.ionization_mechanism_polarity}', which does not "
            f"match this sample's polarity '{polarity}'.",
            {
                **where,
                "ionization_mechanism_id": mechanism_id,
                "mechanism_polarity": mechanism.ionization_mechanism_polarity,
                "sample_polarity": polarity,
            },
            422,
        )
    return mechanism_id


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


#: Column widths a promoted candidate's strings land in, mirroring
#: ``PeakAssignment``. An alternative is untyped JSON - Pydantic validates
#: nothing inside the blob, and an imported run's alternatives are whatever the
#: publishing client sent - so what the engine writes is not what may be there.
_CANDIDATE_WIDTHS = {
    "assigned_formula": 256,
    "ion_formula": 4096,
    "ionization_mechanism_id": 16,
    "isotope_label": 64,
    "isotope_formula": 256,
    "target_compound_id": 16,
    "target_ion_id": 16,
}

#: Numeric candidate fields, with the bounds their columns carry. `fit_score`
#: has a CHECK constraint; the other two only have to be finite, since the read
#: path renders with ``allow_nan=False`` and one NaN row breaks the whole run's
#: ledger.
_CANDIDATE_NUMBERS = {
    "fit_score": (0.0, 1.0),
    "mz_error_ppm": None,
    "abundance_error": None,
}


def _validated_candidate(candidate: dict, where: dict) -> dict:
    """Check a stored candidate before any of it reaches a typed column.

    ``alternatives`` is a bare JSON list: no schema validates its entries on the
    way in, an imported run's are client-supplied, and the two engine stages
    already write three different shapes. So a promoted entry can carry a string
    where a float belongs, a formula longer than its column, or a non-finite
    number that Postgres stores happily and the ledger read then chokes on for
    the whole run. Each of those is a class-22 data error or a constraint
    violation out of the flush - a 500 - where the request deserves a 422 that
    names the field.

    :param candidate: The chosen entry, as stored.
    :param where: Context merged into the error detail.
    :return: The candidate with its typed fields coerced and checked.
    :raises ApiException: 422, naming the field that cannot be committed.
    """
    clean: dict = {}
    for field, width in _CANDIDATE_WIDTHS.items():
        value = candidate.get(field)
        if value is None:
            continue
        if not isinstance(value, str):
            raise ApiException(
                f"That candidate's {field} is not text, so it cannot be "
                "committed to the peak.",
                {**where, "field": field},
                422,
            )
        if len(value) > width:
            raise ApiException(
                f"That candidate's {field} is {len(value)} characters, above "
                f"the {width} this column holds.",
                {**where, "field": field, "length": len(value)},
                422,
            )
        clean[field] = value

    for field, bounds in _CANDIDATE_NUMBERS.items():
        value = candidate.get(field)
        if value is None:
            continue
        # bool is an int subclass, and True would silently become 1.0.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ApiException(
                f"That candidate's {field} is not a number.",
                {**where, "field": field},
                422,
            )
        value = float(value)
        if not math.isfinite(value):
            raise ApiException(
                f"That candidate's {field} is not a finite number.",
                {**where, "field": field},
                422,
            )
        if bounds is not None and not (bounds[0] <= value <= bounds[1]):
            raise ApiException(
                f"That candidate's {field} is {value}, outside the "
                f"[{bounds[0]}, {bounds[1]}] this column allows.",
                {**where, "field": field, "value": value},
                422,
            )
        clean[field] = value

    # Carried through unchecked on purpose: they go into the JSON provenance
    # blob, not into a typed column, and the reference identities are whatever
    # shape the reference mirror wrote.
    for field in ("plausibility", "reference_identities"):
        if candidate.get(field) is not None:
            clean[field] = candidate[field]
    return clean


def _role_for(isotope_label: str | None) -> str:
    """M0 unless the candidate says this peak is one of the ion's isotopologues.

    A candidate labelled 'M+1' is a claim about an isotopologue, and committing
    it as an M0 would enter the compound's *isotopologue* into the ledger as the
    compound's main peak - which every consumer that folds a family onto its M0
    would then believe.
    """
    if isotope_label and isotope_label != "M0":
        return ROLE_ISO_CHILD
    return ROLE_M0


def _committed_ion(body) -> str | None:
    """The ion a `set_assignment` row stores: the hit's ion, or for a hit read
    at one of its isotope lines, that line's own formula.

    The search hit names its ion (`target_ion_formula`) at the monoisotopic
    line whichever line it was read at, and the line it was read at in
    `isotope_formula` (`[81Br]CHBr3-`). An untargeted run stores a line as its
    own ion (`untargeted_rows.py`); the database path stores the ion's
    monoisotopic formula, beside a line that names its owner. A hand line names
    no owner, so its ion is all the batch fold reads it by
    (`batch_peaks.is_isotope_line`): stored as the monoisotopic ion, the fold
    filed it as that ion at the line's m/z. A line whose formula the request
    does not name stores no ion rather than the wrong one.
    """
    if _role_for(body.isotope_label) == ROLE_ISO_CHILD:
        return body.isotope_formula
    return body.ion_formula


def _manual_provenance(
    action: str,
    scored_by: str,
    user_id: int | None,
    previous: dict | None,
    plausibility: float | None,
    fit_score: float | None,
    formula: str | None,
    at: str,
    reference_identities: list | None = None,
    demoted: list | None = None,
    restored: list | None = None,
    restore_skipped: list | None = None,
    restore_failed: list | None = None,
) -> dict:
    """The provenance a curated row carries.

    Deliberately thin. ``plausibility`` is a property of the formula itself, so
    it is honest for any winner however it was chosen, and ``evidence`` is the
    engine's own definition of it (fit x plausibility) computed from numbers
    that are on the record - the quantity a verification snapshots as its
    calibration label, and the one this row's tier was read off. Everything the
    calibration layer *derives* is absent, so a curated row is never mistaken for
    a calibrated one.

    :param at: When the edit happened. Passed in rather than read here so the
        row and the isotopologues it strips carry the same instant - the archive's
        skip rule matches on that timestamp, and one act deserves one time.
    :param demoted: Archive of the isotopologues this row has
        stripped and can put back, newest first. Carried across curations that
        strip nothing: an override that demotes nobody must not drop the record
        of one that did, or the compound's family would become unrestorable
        just because the row was edited twice.
    :param restored: Ids of the isotopologues this edit put back,
    :param restore_skipped: ids of the archived isotopologues it deliberately left
        alone because a person had curated them since, and
    :param restore_failed: ids it could not put back at all - the row is gone
        from this run, or the state archived for it cannot be committed. Kept
        apart from the skips because the two say opposite things about what
        happened: one is restraint towards a row somebody else owns, the other
        is an undo that did not reach. All three are audit only - the rows
        themselves say what they are.
    """
    # Through `evidence_for` rather than multiplying the two arguments, so this
    # is byte-for-byte the number the row was TIERED on. The ledger shows it
    # beside the tier, and the one case where the two definitions diverge - a
    # formula the chemistry layer cannot parse, where `plausibility` is None but
    # the tier falls open to the bare fit - is exactly the case where a null here
    # would put a tier on screen with no number to explain it.
    evidence = evidence_for(fit_score, formula)
    return _clean(
        {
            "plausibility": plausibility,
            "evidence": evidence,
            "score_version": SCORE_VERSION,
            # The known-compound identities of the formula being committed, when
            # the candidate carried them. Unlike the calibrated fields, this is
            # not a judgement about the displaced winner - it names the formula
            # the row now holds, read from the reference mirror when the run was
            # computed, so the inspector still names a known compound.
            "reference_identities": reference_identities,
            "manual": _clean(
                {
                    "action": action,
                    "scored_by": scored_by,
                    "user_id": user_id,
                    "at": at,
                    "previous_formula": (previous or {}).get("assigned_formula"),
                    "previous": previous,
                    "demoted": demoted or None,
                    "restored": restored or None,
                    "restore_skipped": restore_skipped or None,
                    "restore_failed": restore_failed or None,
                }
            ),
        }
    )


def _demote(
    child: PeakAssignment,
    user_id: int | None,
    owner_formula: str | None,
    owner_mechanism_id: str | None,
    at: str,
) -> dict:
    """Strip an isotopologue row of a formula that is no longer its M0's.

    An isotopologue is not an independent finding - it is the same compound seen
    through one heavy atom - so once a person has rejected the formula it was an
    isotopologue of, there is nothing left for it to claim. It is demoted rather
    than deleted (the ledger holds one row per detected peak, always) and
    rather than left standing (its owner now carries a different compound, so
    the family would show two).

    ``source`` stays 'manual': the row's state is a person's doing, and the
    ledger's source filter has to show the whole footprint of an override, not
    only the row that gained a formula. An isotope line a person assigned by
    hand never comes here: it is detached instead (``hand_lines``).

    :param owner_formula: The formula the isotopologue belonged to, and
    :param owner_mechanism_id: the adduct it belonged to under. The two
        together key the archive: an isotopologue is stripped because a compound
        was replaced, so it is that compound coming back that puts it back.
    :param at: The instant of the override, shared with the owner's own record
        so a restore can tell an untouched demotion from a later hand edit.
    :return: The archive entry the owner keeps under
        ``provenance.manual.demoted`` - everything needed to put this row back
        as it stood.
    """
    previous = _previous_winner(child)
    # Two kinds of state, both needed to restore the row: the typed columns
    # (snapshotted in the alternatives shape `_previous_winner` writes, the one
    # shape every archive in this module uses) and the provenance blob, kept
    # verbatim because it holds numbers no column does. They overlap on
    # plausibility and the engine judgement; the blob is the one that goes back
    # on the row.
    entry = _clean(
        {
            "peak_assignment_id": child.peak_assignment_id,
            # Named for a reader: within a run the peak id is what identifies a
            # row, and a 32-character key means nothing in an audit view.
            "sample_peak_id": child.sample_peak_id,
            "owner_formula": owner_formula,
            "owner_ionization_mechanism_id": owner_mechanism_id,
            "at": at,
            "previous": previous,
            "provenance": child.provenance,
        }
    )
    child.alternatives = _push_alternative(child.alternatives, previous)
    child.provenance = _clean(
        {
            "manual": _clean(
                {
                    "action": ACTION_DEMOTE_ISOTOPOLOGUE,
                    "reason": "owner_overridden",
                    "user_id": user_id,
                    "at": at,
                    "previous_formula": (previous or {}).get("assigned_formula"),
                    "previous_owner_formula": owner_formula,
                    "previous": previous,
                }
            )
        }
    )
    child.role = ROLE_UNASSIGNED
    child.tier = TIER_UNASSIGNED
    # Archived with the formula it judged (see `_previous_winner`); a verdict
    # about a displaced winner is not a verdict about an unassigned peak.
    child.engine_tier = None
    child.engine_level = None
    # An unassigned peak commits no formula, so it states no level; its old
    # one is in the archived provenance.
    child.level = None
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
    return entry


async def _restore(
    session,
    child: PeakAssignment,
    entry: dict,
    owner_id: str,
    bands: tuple[float, float],
) -> bool:
    """Put one demoted isotopologue back as its archive recorded it.

    The row comes back as what it was, engine source and all, and its manual
    block goes with the rest of the demotion's provenance: after a restore the
    row is not a person's edit any more, it is the engine's row it was before
    one. The record of the round trip lives on the owner, which is the row a
    person actually curated.

    Three things are not taken out of the archive on trust, for the reason the
    curated row's own tier is recomputed: the archive is JSON, an imported
    run's provenance is whatever the publishing client sent, and all of it is
    on its way into typed columns.

    - The **role** is ``iso_child`` unconditionally. The row is being given an
      owner, and being owned is what makes a row an isotopologue row.
    - The **tier** comes back as the archive recorded it, since a restore is an
      undo and not a re-judgement - but only when it is a tier the vocabulary
      knows. Anything else is recomputed from the fit under the run's own
      bands, the same yardstick the curated row itself is tiered by.
    - The **foreign keys** are confirmed to still exist. The archive does not
      cascade, so a mechanism or target deleted since the demotion would
      otherwise raise from the flush instead of the request. The mechanism is
      usually the owner's own and already loaded in this session, so the common
      case costs nothing.

    :param bands: ``(assigned, candidate)`` evidence thresholds of the run.
    :return: True when the row was put back; False when the archived state
        cannot go in the columns at all, in which case the row is untouched.
    """
    previous = entry.get("previous")
    previous = previous if isinstance(previous, dict) else {}
    try:
        state = _validated_candidate(
            previous,
            {"peak_assignment_id": child.peak_assignment_id, "action": "restore"},
        )
    except ApiException:
        # An archive entry too malformed to commit - a formula longer than its
        # column, a fit score outside it. Refusing the whole curation over it
        # would be the wrong verdict, since the request itself is fine, so the
        # entry is dropped and the row stays demoted exactly as it already was.
        return False

    # The demotion pushed this exact snapshot onto the head of the row's own
    # alternatives; popping it back off is what stops a demote/restore round
    # trip from growing the list by one entry every time. Only the head, and
    # only while it is still verbatim what was pushed - an entry the ceiling
    # truncated off the tail back then is not resurrected here.
    alternatives = list(child.alternatives or [])
    if alternatives and alternatives[0] == previous:
        alternatives.pop(0)

    mechanism_id = state.get("ionization_mechanism_id")
    if mechanism_id and await session.get(IonizationMechanism, mechanism_id) is None:
        mechanism_id = None
    target_compound_id, target_ion_id = await _surviving_target_ids(
        session, state.get("target_compound_id"), state.get("target_ion_id")
    )
    fit_score = state.get("fit_score")
    assigned_band, candidate_band = bands
    tier = normalize_tier(previous.get("tier"))
    if tier not in TIERS:
        tier = tier_for_evidence(
            evidence_for(fit_score, state.get("assigned_formula")),
            candidate_threshold=candidate_band,
            assigned_threshold=assigned_band,
        )
    source = previous.get("source")
    restored_provenance = entry.get("provenance")

    child.alternatives = alternatives or None
    child.provenance = (
        restored_provenance if isinstance(restored_provenance, dict) else None
    )
    child.role = ROLE_ISO_CHILD
    child.tier = tier
    # The engine's tier and level, restored with the winner they judged.
    child.engine_tier, child.engine_level = _engine_verdict(previous)
    # An engine's source, or 'manual' on a line a hand M0 claimed, which was
    # demoted with it (``hand_claims``); a hand line linked under an M0 is
    # detached instead (``hand_lines``), so it never reaches the archive.
    child.source = restored_source(source, restored_provenance)
    child.assigned_formula = state.get("assigned_formula")
    child.ion_formula = state.get("ion_formula")
    child.ionization_mechanism_id = mechanism_id
    child.isotope_label = state.get("isotope_label")
    child.isotope_formula = state.get("isotope_formula")
    child.fit_score = fit_score
    child.mz_error_ppm = state.get("mz_error_ppm")
    child.abundance_error = state.get("abundance_error")
    child.target_compound_id = target_compound_id
    child.target_ion_id = target_ion_id
    child.owner_peak_assignment_id = owner_id
    return True


async def _restore_demoted(
    session,
    owner: PeakAssignment,
    archive: list,
    assigned: str | None,
    mechanism_id: str | None,
    bands: tuple[float, float],
) -> tuple[list[PeakAssignment], list[str], list[str], list[dict]]:
    """Put back the isotopologues an earlier override of this row stripped.

    Fires when the compound now being committed is the one an archive entry was
    taken under - the row is being put back to the compound whose family was
    stripped, so the family goes back with it. That is what the inspector's
    "use this to undo" promises, and without this the undo would restore the M0
    and leave its isotopologues unassigned and ownerless.

    The ids are on the owner's own provenance, so each restore is a primary-key
    read - no JSON-path query over the table.

    **An isotopologue someone has curated by hand since the demotion is skipped,
    never overwritten.** The person's judgement is newer than the undo, and a
    restore that silently replaced their assignment with the engine's older one
    would destroy a deliberate act to reverse an accidental one. The tell is
    the row's own provenance: a demotion writes a ``manual.action`` from
    :data:`DEMOTE_ACTIONS` with the override's timestamp, so a row whose manual
    block says anything else, or carries a different instant, has been written
    by someone after the demotion. Such an entry is reported and dropped from
    the archive rather than kept for a later attempt - the row belongs to
    whoever claimed it now.

    **An entry that cannot be put back at all is reported too**, under its own
    heading rather than as a skip: a skip is a deliberate act of restraint
    towards a row somebody else now owns, and reporting a failure as one would
    tell a person their isotopologue was left alone on purpose when in truth the
    undo could not reach it. Silence is the worse option either way - the
    response would say an undo happened while an isotopologue stayed demoted with
    nothing anywhere saying why.

    :param owner: The row being curated, which holds the archive.
    :param archive: ``provenance.manual.demoted`` as it stood *before* this
        edit rewrote the row's provenance.
    :param assigned: The formula being committed, and
    :param mechanism_id: the mechanism it is committed under. Together they are
        the compound an entry has to have been archived under to be restored.
    :param bands: The run's evidence thresholds, for re-tiering a restored row.
    :return: ``(restored rows, skipped ids, unrestorable ids, the entries that
        stay archived)``.
    """
    restored: list[PeakAssignment] = []
    skipped: list[str] = []
    failed: list[str] = []
    remaining: list[dict] = []
    for entry in archive:
        # The only two drops that go unreported, because there is nothing to
        # report: an entry that is not an object, or one that names no row,
        # points at no isotopologue at all. Every drop below names a real row and
        # says so.
        if not isinstance(entry, dict):
            continue
        child_id = entry.get("peak_assignment_id")
        if not isinstance(child_id, str) or not child_id:
            continue
        if (
            entry.get("owner_formula") != assigned
            or entry.get("owner_ionization_mechanism_id") != mechanism_id
        ):
            # Archived under some other compound: this edit says nothing about
            # it, so it waits for the override that does commit that compound.
            remaining.append(entry)
            continue
        child = await session.get(PeakAssignment, child_id, with_for_update=True)
        # The id comes out of a JSON blob, and an imported run's provenance is
        # whatever the publishing client sent, so it can name a row of another
        # run - or of another workspace's sample - entirely. A restore may only
        # ever write rows of the run it is curating.
        if (
            child is None
            or child.peak_assignment_run_id != owner.peak_assignment_run_id
        ):
            # Reported, and the entry is CONSUMED rather than kept. Nothing that
            # happens later turns this into a restorable isotopologue: a deleted
            # row does not come back under the same id, and an id belonging to
            # another run never becomes this one's. Keeping the entry would hold
            # one of the archive's 32 slots to offer an undo that can only ever
            # fail again. (Contrast the failure below, where the row is still
            # here and only the archived state is unusable.)
            failed.append(child_id)
            continue
        manual = (child.provenance or {}).get("manual")
        manual = manual if isinstance(manual, dict) else {}
        # Still the demotion this archive recorded, and nothing since.
        untouched = manual.get("action") in DEMOTE_ACTIONS and manual.get(
            "at"
        ) == entry.get("at")
        if not untouched:
            skipped.append(child_id)
            continue
        if await _restore(session, child, entry, owner.peak_assignment_id, bands):
            restored.append(child)
        else:
            # The archived state cannot go in the columns - a formula longer
            # than its column, a fit score outside it, the shapes an imported
            # run's provenance can carry. Reported like a gone row, but the
            # entry is KEPT: the isotopologue is still here and still demoted, and
            # a re-import that republishes this run's provenance with the entry
            # repaired would make it restorable again. Dropping it would throw
            # away the archive of a row that is still standing, which is the one
            # copy of it a curator can act on from the M0.
            failed.append(child_id)
            remaining.append(entry)
    return restored, skipped, failed, remaining


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


class _LinesAsked(NamedTuple):
    """What asking a hand assignment's lines came to.

    ``reason`` is the absent-line reason where a line is missing; ``unread``
    says the run asks lines and the sample's peaks could not be read, which the
    edit's message says rather than passing over.
    """

    reason: dict | None = None
    unread: bool = False


async def _persons_choice(
    session,
    sample,
    run: PeakAssignmentRun,
    row: dict,
    bands: tuple[float, float],
) -> tuple[dict, _LinesAsked]:
    """A hand assignment through the stage list's person's-choice path
    (``pipeline.PERSONS_CHOICE``): the person's formula takes the row, tiered
    on its evidence under the run's bands, and is asked the spectrum's rules -
    whether the lines it predicts are there - at the gate the run asked its own
    rows at (:meth:`tiering.LineGate.from_record`), over the sample's peaks read
    as the run read them: at the setup's labelled purity, and on a decoy run's
    shifted axis, with the peaks the pre-passes claim read as neither a line
    nor an absence. Then its identification level. The edit claims the lines
    it finds (``hand_claims``), the batch's *Use this* (``batch_curation``) none.

    A run that asked no line asks none of its hand assignments either: an
    imported run, whose configuration is its engine's own, and a run tiered
    before rule set 11 or without the sample's peaks. Nor does one whose
    sample's peaks cannot be read now, which the edit's message says.

    :param session: The edit's session, which reads the run's artifact rows.
    :param sample: The sample view row.
    :param run: The row's run.
    :param row: The curated row as the stages read it, written into.
    :param bands: The run's ``(assigned, candidate)`` bands.
    :return: The row as judged, and what asking its lines came to.
    """

    recorded = (
        run.config
        if run.engine == IN_APP_ENGINE and isinstance(run.config, dict)
        else {}
    )
    decoy = recorded.get("decoy")
    decoy = decoy if isinstance(decoy, dict) else {}
    assigned_band, candidate_band = bands
    # The peaks the run claimed as the detector's ringing are its own record.
    artifact_peak_ids = frozenset(
        str(peak_id)
        for peak_id in (
            await session.execute(
                select(PeakAssignment.sample_peak_id).where(
                    PeakAssignment.peak_assignment_run_id == run.peak_assignment_run_id,
                    PeakAssignment.role == ROLE_ARTIFACT,
                )
            )
        ).scalars()
    )
    with (
        labelled_purity(await sample_label_purity(sample.sample_item_id)),
        shifted_mz_axis(ppm=decoy.get("mz_shift_ppm"), da=decoy.get("mz_shift_da")),
    ):
        # Read only where they will be asked: a monoisotopic row, on a run
        # that asked its own rows' lines.
        unread = False
        peaks_df = pd.DataFrame(columns=["sample_peak_id", "mz", "intensity"])
        if (
            row.get("role") == ROLE_M0
            and LineGate.from_record(recorded.get(TIERING_KEY)) is not None
        ):
            try:
                peaks_df = await asyncio.to_thread(load_sample_peaks, sample)
            except Exception as exc:  # noqa: BLE001 - the edit stands without it
                runtime.logger.warning(
                    f"Peaks of sample '{sample.sample_item_name}' unreadable "
                    f"({exc}); a hand assignment's isotope lines were not checked"
                )
                unread = True

        async def keep(ctx: RunContext) -> list[dict]:
            return ctx.judged.rows

        ctx = await run_stages(
            RunContext(
                path=PERSONS_CHOICE,
                sample=sample,
                config=PeakAssignmentConfig(
                    assigned_threshold=assigned_band,
                    candidate_threshold=candidate_band,
                ),
                run_id=run.peak_assignment_run_id,
                keep=keep,
                strict_instrument=False,
                strict_chemistry=False,
                peaks_df=peaks_df,
                chosen=[row],
                recorded=recorded,
                recorded_artifact_peak_ids=artifact_peak_ids,
            )
        )
    (judged,) = ctx.result
    # Asked where its peaks were read: the stages always hold a gate, so the
    # peaks are what says whether this run asks lines (read above).
    asks = judged.get("role") == ROLE_M0 and not peaks_df.empty
    reasons = (judged.get("provenance") or {}).get("tier_reasons") or []
    return judged, _LinesAsked(
        reason=reasons[0] if reasons and asks else None,
        unread=unread and judged.get("role") == ROLE_M0,
    )


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

    An edit reaches beyond the row in both directions, and both are the same
    rule - an isotopologue belongs to its M0's compound: the isotopologue family of
    a compound being replaced is demoted and archived, and the family of a
    compound being committed *back* is restored from that archive, so promoting
    the previous winner really undoes the override instead of leaving its
    isotopologues behind. An isotopologue curated by hand in the meantime is left
    exactly as they left it and reported as skipped, never overwritten, and one
    the undo cannot reach at all - its row deleted since, or its archived state
    unusable - is reported as such rather than passed over in silence.

    A hand M0 writes a family too: it claims the free rows the lines its
    formula predicts are found on, as a run writes its lines, and names the
    found lines another row holds (``hand_claims``). Those lines are part of
    its click, so an override demotes them with it and the undo restores them.

    A person's choice is never undone as a side effect, and an isotope line
    assigned by hand is one (``hand_lines``). It is linked under the run's one
    M0 of its formula and adduct, and shows that M0's level; an M0 committed
    takes the run's ownerless hand lines of its compound the same way. When
    that M0's compound is replaced, the line is detached rather than demoted:
    it keeps its formula and stands on its own.

    :param sample_item_id: Sample the assignment belongs to.
    :param peak_assignment_id: The row to curate.
    :param body: A validated ``PromoteAlternativeBody`` or ``SetAssignmentBody``.
    :param user_id: The curating user, recorded in provenance.
    :return: Status envelope; ``data[0]`` is the curated row, followed by the
        isotopologue rows the edit demoted, the hand lines it detached, the
        rows it restored, the hand lines it linked under the row and the
        lines it claimed.
    :raises NotFoundException: The assignment is not this sample's.
    :raises ApiException: 409 when the run is not completed (something else is
        still writing it) or the promoted candidate moved; 422 when the request
        names a candidate, mechanism or formula that cannot be committed -
        including a candidate that resolves to no adduct at all, which both
        actions refuse alike.
    """
    sample = await fetch_sample(sample_item_id)
    if is_fold_id(peak_assignment_id):
        # Derived from the batch peaks: there is no row to edit. A 409 rather
        # than a 404, because the id is not stale - the sample has no run.
        raise DerivedAssignmentReadOnlyException(
            peak_assignment_id, sample.sample_item_name, "curated"
        )

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
            # Read before `_validated_candidate` narrows `chosen` to the fields
            # that may reach a column - the engine's tier and level are not, so they
            # would be gone by then. Only an entry :func:`_previous_winner`
            # archived carries one, which is exactly the entry that should put
            # it back: a verdict the engine formed about that winner, promoted
            # back with the winner it was about. An engine's own close
            # alternative carries none, and rightly - the engine tiered its
            # winner, not the runner-up.
            promoted_verdict = _engine_verdict(chosen)
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
                    "ion_formula": _committed_ion(body),
                    "ionization_mechanism_id": body.ionization_mechanism_id,
                    "isotope_label": body.isotope_label,
                    "isotope_formula": body.isotope_formula,
                    "fit_score": body.fit_score,
                    "mz_error_ppm": body.mz_error_ppm,
                    "abundance_error": body.abundance_error,
                }
            )
            promoted_verdict = (None, None)
            scored_by = SCORED_BY_SEARCH
            remaining = list(assignment.alternatives or [])

        where = {
            "peak_assignment_id": peak_assignment_id,
            "action": body.action,
        }
        # Checked for both actions, though only a promoted candidate can really
        # be malformed: a `set_assignment` body is already bounded by its
        # schema, and running it through the same gate keeps one answer to
        # "what may be committed" rather than two that can drift.
        chosen = _validated_candidate(chosen, where)

        mechanism_id = await _resolve_mechanism_id(
            session,
            chosen.get("ionization_mechanism_id"),
            chosen.get("target_ion_id"),
            sample.polarity,
            where,
        )
        target_compound_id, target_ion_id = await _surviving_target_ids(
            session, chosen.get("target_compound_id"), chosen.get("target_ion_id")
        )

        previous = _previous_winner(assignment)
        previous_formula = assignment.assigned_formula
        previous_mechanism_id = assignment.ionization_mechanism_id
        # Read before this edit overwrites the row's provenance: it carries the
        # archive of the isotopologues an EARLIER override of this row stripped,
        # which is the only record of how to put them back. Type-checked on the
        # way out because provenance is JSON an import may have written.
        previous_manual = (assignment.provenance or {}).get("manual")
        archived = (
            previous_manual.get("demoted")
            if isinstance(previous_manual, dict)
            else None
        )
        archived = archived if isinstance(archived, list) else []
        # Isotopologues are read before the winner changes; after it, nothing on
        # the row says which compound they were isotopologues of. They are only
        # DEMOTED when the compound actually changes: committing the formula the
        # row already carries (a different candidate entry for the same
        # composition, or one adduct's row re-confirmed) leaves the family
        # standing for exactly what it stood for before, and stripping it would
        # destroy correct rows to record a change that did not happen.
        family = (
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
        plausibility = plausibility_for(assigned)
        fit_score = chosen.get("fit_score")
        assigned_band, candidate_band = _run_bands(run)
        # The compound is what an isotopologue is an isotopologue OF, and a
        # compound is a formula under an adduct - so the family survives only
        # when both are the ones it was built for.
        same_compound = (
            assigned == previous_formula and mechanism_id == previous_mechanism_id
        )
        displaced = [] if same_compound else family

        # One act, one instant: the owner's record and the isotopologues it moves
        # carry the same timestamp, which is what a later restore matches on to
        # tell an untouched demotion from a row someone has curated since.
        at = dt.now(timezone.utc).isoformat()
        # A line a person assigned by hand is detached rather than demoted.
        displaced, detached, detach_note = detach_hand_lines(
            displaced, previous_formula, at
        )
        (
            restored,
            restore_skipped,
            restore_failed,
            kept_archive,
        ) = await _restore_demoted(
            session,
            assignment,
            archived,
            assigned,
            mechanism_id,
            (assigned_band, candidate_band),
        )
        demoted_archive = [
            _demote(child, user_id, previous_formula, previous_mechanism_id, at)
            for child in displaced
        ]
        # Newest first, and what this edit did not consume rides along: an
        # override that strips nobody must not drop the archive of one that
        # did, or a family would stop being restorable merely because its M0
        # was edited a second time in between.
        archive_now = (demoted_archive + kept_archive)[:MAX_DEMOTED_ARCHIVE]

        assignment.alternatives = _push_alternative(remaining, previous)
        assignment.provenance = _manual_provenance(
            action=body.action,
            scored_by=scored_by,
            user_id=user_id,
            previous=previous,
            plausibility=plausibility,
            fit_score=fit_score,
            formula=assigned,
            at=at,
            reference_identities=chosen.get("reference_identities"),
            demoted=archive_now,
            restored=[child.peak_assignment_id for child in restored],
            restore_skipped=restore_skipped,
            restore_failed=restore_failed,
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
        # Tiered on evidence, like both engine stages, and then asked whether
        # the spectrum holds the lines the formula predicts, as the run asked
        # its own rows: the stage list's person's-choice path
        # (:func:`_persons_choice`). An absent line holds the row at candidate
        # at most, and is the one reason a hand assignment lists. A hand
        # isotope line is not asked: the rule reads a formula's lines from its
        # main one, which is the M0's to ask.
        judged, lines = await _persons_choice(
            session,
            sample,
            run,
            {
                "peak_assignment_id": assignment.peak_assignment_id,
                "sample_peak_id": assignment.sample_peak_id,
                "sample_peak_mz": assignment.sample_peak_mz,
                "sample_peak_intensity": assignment.sample_peak_intensity,
                "role": assignment.role,
                "assigned_formula": assignment.assigned_formula,
                "ion_formula": assignment.ion_formula,
                "ionization_mechanism_id": assignment.ionization_mechanism_id,
                "isotope_label": assignment.isotope_label,
                "source": assignment.source,
                "fit_score": assignment.fit_score,
                "mz_error_ppm": assignment.mz_error_ppm,
                "owner_peak_assignment_id": None,
                "provenance": dict(assignment.provenance or {}),
            },
            (assigned_band, candidate_band),
        )
        assignment.tier = judged["tier"]
        assignment.provenance = judged["provenance"]
        # The engine's verdict is about a FORMULA, not about a row, so it
        # survives exactly as long as the row still holds the formula it judged.
        # Three cases, and only the last is "no engine judged this":
        #
        # - the same composition re-committed leaves it standing, for the same
        #   reason `same_compound` leaves the family standing - nothing the
        #   engine said has been displaced;
        # - promoting the archived winner back restores it, which is what makes
        #   the inspector's "use this to undo" an undo rather than a fresh
        #   assignment wearing the old formula;
        # - anything else drops it, because no engine ever saw the formula a
        #   person has just chosen. The displaced winner's verdict is not lost:
        #   `_previous_winner` archived it with the winner, which is where the
        #   promote above reads it back from.
        if not same_compound:
            # The engine's level goes with its tier, by the same three cases.
            assignment.engine_tier, assignment.engine_level = promoted_verdict
        # Its identification level, which the stages stated of the formula a
        # person chose (``identification_level.hand_level``): refuted where a
        # line it predicts is absent, else rivals not measured, since no
        # shortlist of the run was competed against it. The isotopologues put
        # back with it show its level, and so do the ones a re-commit of the
        # same compound left standing.
        level = assignment.provenance[IDENTIFICATION_LEVEL]
        assignment.level = level["level"]
        for child in [*restored, *(family if same_compound else [])]:
            inherited = inherited_level(level, assignment.peak_assignment_id)
            child.provenance = {
                **(child.provenance or {}),
                IDENTIFICATION_LEVEL: inherited,
            }
            child.level = inherited["level"]
        # A hand line folds under its compound's one M0 on the run, and an M0
        # takes the run's ownerless hand lines of its compound (`hand_lines`).
        link = await link_hand_lines(session, assignment)
        # A hand M0 claims the free rows its found lines sit on (`hand_claims`).
        claims = await claim_found_lines(
            session,
            assignment,
            run,
            user_id=user_id,
            at=at,
            push_previous=lambda row: _push_alternative(
                row.alternatives, _previous_winner(row)
            ),
        )

        # Read out before the commit expires these instances. A refresh per row
        # would be the alternative, and an expired attribute read on an async
        # session is a lazy load - which raises rather than reloading.
        sample_peak_id = assignment.sample_peak_id
        # The run's confidence calibration folded back in, exactly as the
        # assignment detail read does it: these are full detail records, and a
        # row restored from its archive carries a `p_correct` whose curve lives
        # on the run. Without this the same row reads differently here and from
        # the detail endpoint a request later.
        records = []
        moved = [*displaced, *detached, *restored, *link.linked, *claims.claimed]
        for row in [assignment, *moved]:
            record = row.to_dict()
            record["provenance"] = provenance_with_calibration(
                record.get("provenance"), run.confidence_calibration
            )
            records.append(record)

        await session.commit()

    message = edit_message(
        EditOutcome(
            assigned=assigned,
            previous_formula=previous_formula,
            sample_peak_id=sample_peak_id,
            sample_name=sample.sample_item_name,
            demoted=len(displaced),
            restored=len(restored),
            restore_skipped=len(restore_skipped),
            restore_failed=len(restore_failed),
            line_reason=lines.reason,
            lines_unread=lines.unread,
            link_note=link.note,
            claim_note=claims.note,
            detach_note=detach_note,
        )
    )
    return {
        "status": "success",
        "message": message,
        "results": len(records),
        "data": records,
    }
