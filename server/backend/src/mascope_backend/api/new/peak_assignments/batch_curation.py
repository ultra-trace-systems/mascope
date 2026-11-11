"""Manual curation of a batch peak: pin the species claim for the whole batch.

The anchor-scoped counterpart of ``curation.py``. A per-sample override edits one
row of one run; with the batch ledger primary, the species is the anchor and the
sample rows are its members, so "use this" on a derived row acts on the anchor
(``docs/dev/peak_assignment_batch_primary.md`` section 6.3). Three parts, in
order:

- **Pin.** The chosen identity - one of the anchor's registry entries, so a
  formula some member of the batch actually carried - is written under
  ``provenance.manual`` on the anchor, with the displaced consensus archived as
  ``previous``. Every consensus recompute honours the pin
  (:func:`batch_peaks.compute_consensus` with ``manual=``): the anchor claims the
  pinned formula whatever the vote says, its tier rolled up over the members that
  carry it, and the vote's own winner is kept beside it so a disagreement stays
  visible.
- **Propagate.** The pinned identity is measured on every sample holding a
  member that does not already carry it, through the seeded chain the untargeted
  search propagates with: a member the ion's envelope reaches takes the identity
  with a fit of its own; one it does not reach keeps what it had. Nothing is
  inferred from another sample's spectrum. What each re-pointed member read
  before is archived on the pin (``displaced_members``), so release is an undo
  and not a re-vote over rewritten evidence.
- **Recompute**, once, so the batch ledger and every derived Sample view agree.

**Release** puts the archived member states back where nobody has changed them
since, drops the pin and recomputes the plain vote.

**Measure first** (design note step 3.4c). An identity the registry holds was
measured in the sample that brought it, not in the one the inspector shows, so
before "use this" the inspector asks :func:`measure_batch_peak_candidate` what
the pin would make of this sample's member: the ion's fit, its envelope's lines
and which of them the spectrum holds, the plausibility, the evidence and the
tier, beside what the member reads now. Nothing is written. The measurement is
the propagation's own (``batch_untargeted.score_in_sample`` and
``measured_reading``), so the numbers shown are the ones the pin then writes on
that member; a pin made from that sample records the measurement under
``provenance.manual.measured``.

The claim is guarded as a verdict's is: the client names the consensus formula it
saw (``expected_formula``), and a mismatch is refused rather than a pin recorded
over a consensus the user never read.

An identity whose ion has its monoisotopic line elsewhere is refused too. The
registry holds every identity a member carried, isotope lines included - the
13C line of an ion one mass unit below - and pinned here such a line would be
measured as an ion of its own: an M0 carrying its parent's envelope fit, at its
parent's tier, with no family link. The refusal names the batch peak that holds
the monoisotopic line, where the compound can be pinned.
"""

from __future__ import annotations

import asyncio
from datetime import datetime as dt
from datetime import timezone
from typing import Any, Optional

from fastapi import status
from sqlalchemy import select

from mascope_backend.api.lib.api_features import (
    api_controller,
    api_controller_background_task,
)
from mascope_backend.api.lib.exceptions.api_exceptions import (
    CodedHTTPException,
    NotFoundException,
)
from mascope_backend.api.new.peak_assignments.batch_peak_verification import (
    ClaimChangedException,
)
from mascope_backend.api.new.peak_assignments.batch_peaks import (
    ROLE_M0,
    Anchor,
    AnchorSet,
    manual_pin_of,
    member_state,
    resolve_candidate,
    tier_name,
)
from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
    _acquire_batch_fold_lock,
    recompute_batch_consensus,
)
from mascope_backend.api.new.peak_assignments.batch_runs import touch_ledger
from mascope_backend.api.new.peak_assignments.batch_untargeted import (
    Annotation,
    _propagate_to_sample,
    measured_reading,
    score_in_sample,
)
from mascope_backend.api.new.peak_assignments.config import PeakAssignmentConfig
from mascope_backend.api.new.peak_assignments.derived_evidence import (
    isotopologue_rows,
)
from mascope_backend.api.new.peak_assignments.engine import (
    evidence_for,
    monoisotopic_row,
)
from mascope_backend.api.new.peak_assignments.seeded_scoring import (
    build_seeded_isotopes_df,
    fetch_mechanisms_by_id,
)
from mascope_backend.db import BatchPeak, BatchPeakOccurrence, async_session
from mascope_backend.runtime import runtime
from mascope_backend.socket.notifications import (
    UserNotification,
    send_progress_user_notification,
)
from mascope_tools.composition.heuristic_filter import formula_plausibility


#: The notification channel the curation reports on, start to finish.
NOTIFICATION_TYPE = "curate_batch_peak"

#: The one action an anchor knows: promote a registry entry to the claim.
ACTION_PROMOTE_IDENTITY = "promote_identity"

#: Machine-readable codes on the refusals a client has to react to.
UNKNOWN_CANDIDATE_CODE = "batch_peak_candidate_unknown"
ISOTOPE_LINE_CODE = "batch_peak_candidate_isotope_line"
NOT_CURATED_CODE = "batch_peak_not_curated"

#: How far from an identity's monoisotopic line, in m/z, a batch peak may sit
#: and still be that line. Every other line of an ion is a mass unit or more
#: away: its 13C line 1.003 above, a 15N reagent's unlabelled remainder 0.997
#: below.
MONOISOTOPIC_WINDOW = 0.5

#: How far around the monoisotopic m/z the batch's peaks are read to find the
#: one holding it, in ppm. Wider than any batch peak's own tolerance, which
#: then decides (:meth:`batch_peaks.AnchorSet.find`).
PARENT_SEARCH_PPM = 500.0


class UnknownCandidateException(CodedHTTPException):
    """The registry index names no identity on this anchor (422)."""

    error_code = UNKNOWN_CANDIDATE_CODE

    def __init__(self, batch_peak_id: str, candidate: int):
        super().__init__(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"Batch peak '{batch_peak_id}' has no candidate {candidate}; the "
                "identities it can be assigned are the ones its members have carried"
            ),
        )


class IsotopeLineException(CodedHTTPException):
    """The identity is an isotope line of an ion whose monoisotopic line is
    elsewhere (422); the message names the batch peak that holds it."""

    error_code = ISOTOPE_LINE_CODE

    def __init__(
        self,
        anchor: Any,
        entry: dict,
        monoisotopic_mz: float,
        parent: Optional[Any],
    ):
        where = (
            f"the batch peak at m/z {parent.mz:.4f} ('{parent.batch_peak_id}'); "
            "assign the compound there"
            if parent is not None
            else f"at m/z {monoisotopic_mz:.4f}, which no batch peak holds"
        )
        super().__init__(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"Batch peak '{anchor.batch_peak_id}' at m/z {anchor.mz:.4f} reads "
                f"{entry.get('ion_formula') or entry.get('formula')} as an isotope "
                f"line of {entry.get('formula')}, not as an ion of its own. The "
                f"ion's monoisotopic line is {where}"
            ),
        )


class NotCuratedException(CodedHTTPException):
    """A release on an anchor nobody pinned (409)."""

    error_code = NOT_CURATED_CODE

    def __init__(self, batch_peak_id: str):
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Batch peak '{batch_peak_id}' carries no manual curation to release",
        )


# --- pure parts -------------------------------------------------------------------


def registry_entry(anchor: Any, candidate: int) -> Optional[dict]:
    """The registry entry ``candidate`` names, or None when the index names
    nothing an anchor can be assigned (out of range, malformed, formula-less)."""
    registry = anchor.candidates if isinstance(anchor.candidates, list) else []
    if not isinstance(candidate, int) or not 0 <= candidate < len(registry):
        return None
    entry = registry[candidate]
    if not isinstance(entry, dict) or not entry.get("formula"):
        return None
    return entry


def monoisotopic_mz(formula: str, mechanism: Any) -> Optional[float]:
    """The m/z of the monoisotopic line of the ion ``formula`` makes through
    ``mechanism`` - for a labelled reagent's ion, its labelled line - or None
    when the ion cannot be generated.

    :param formula: The identity's neutral formula.
    :param mechanism: Its mechanism (``ionization_mechanism_id``,
        ``ionization_mechanism``, ``ionization_mechanism_polarity``).
    """
    isotopes, _ = build_seeded_isotopes_df(
        {(formula, mechanism.ionization_mechanism_id)},
        {mechanism.ionization_mechanism_id: mechanism},
        "HIGH",
        0.0,
    )
    if isotopes.empty:
        return None
    return float(monoisotopic_row(isotopes)["mz"])


def manual_pin(
    anchor: Any,
    candidate: int,
    user_id: Optional[int],
    at: str,
    measured: Optional[dict] = None,
) -> dict:
    """The pin a curated anchor carries under ``provenance.manual``.

    The identity is read off the registry, never from the client, and the
    consensus being displaced is archived whole - the row path's ``previous`` -
    so the note can say what the species read before and a release can be
    checked against it. ``measured`` is what :func:`measure_batch_peak_candidate`
    measured on the sample the pin was made from, kept as the record of what
    the curator judged it on; absent when the pin named no sample.
    """
    identity = resolve_candidate(anchor.candidates, candidate)
    pin = {
        "action": ACTION_PROMOTE_IDENTITY,
        "candidate": candidate,
        "formula": identity.get("formula"),
        "ion_formula": identity.get("ion_formula"),
        "ionization_mechanism_id": identity.get("ionization_mechanism_id"),
        "user_id": user_id,
        "at": at,
        "previous": {
            "consensus_formula": anchor.consensus_formula,
            "consensus_ion_formula": anchor.consensus_ion_formula,
            "ionization_mechanism_id": anchor.ionization_mechanism_id,
            "consensus_tier": anchor.consensus_tier,
            "best_fit_score": anchor.best_fit_score,
            "support_fraction": anchor.support_fraction,
            "is_ambiguous": bool(anchor.is_ambiguous),
        },
        "displaced_members": [],
    }
    if measured is not None:
        pin["measured"] = measured
    return pin


def restore_member(member: Any, state: dict) -> None:
    """Put an archived state back on a member."""
    member.candidate = state.get("candidate")
    member.tier = state.get("tier")
    member.fit_score = state.get("fit_score")
    member.role = state.get("role")
    member.owner_batch_peak_id = state.get("owner_batch_peak_id")
    member.p_correct = state.get("p_correct")


def curation_outcome(counts: dict, sample_batch_id: str) -> dict:
    """The task's closing message."""
    measured = counts["samples_measured"]
    repointed = counts["members_repointed"]
    # A sample that raised is reported rather than left to the log: it still
    # reads whatever it did before the pin, which the counts alone would not say.
    failed = counts.get("samples_failed", 0)
    message = (
        f"Pinned {counts['formula']} on the batch peak; measured it in {measured} "
        f"sample{'s' if measured != 1 else ''}, {repointed} now read"
        f"{'s' if repointed == 1 else ''} it."
        + (
            f" {failed} sample{'s' if failed != 1 else ''} could not be measured "
            f"and keep{'s its' if failed == 1 else ' their'} previous identity."
            if failed
            else ""
        )
    )
    return {
        "status": "partial" if failed else "success",
        "message": message,
        "results": repointed,
        "data": counts,
        "_notification_data": {"sample_batch_id": sample_batch_id},
    }


def _progress(
    sample_batch_id: str,
    item_index: int,
    total: int,
    message: str,
    user_id: Optional[int],
    process_id: Optional[str],
    parent_id: Optional[str],
) -> UserNotification:
    return UserNotification(
        process_id=process_id,
        parent_id=parent_id,
        type=NOTIFICATION_TYPE,
        status="pending",
        message=message,
        data={
            "sample_batch_id": sample_batch_id,
            "_room_ids": [sample_batch_id],
            "_user_id": user_id,
            "_total_samples": total,
            "_item_index": item_index,
        },
    )


# --- the writes -------------------------------------------------------------------


def _check(anchor: Any, sample_batch_id: str, batch_peak_id: str) -> None:
    if anchor is None or anchor.sample_batch_id != sample_batch_id:
        raise NotFoundException(f"Batch peak '{batch_peak_id}' not found in this batch")


def _check_claim(
    anchor: Any, batch_peak_id: str, candidate: int, expected_formula: Optional[str]
) -> dict:
    entry = registry_entry(anchor, candidate)
    if entry is None:
        raise UnknownCandidateException(batch_peak_id, candidate)
    if expected_formula is not None and expected_formula != anchor.consensus_formula:
        raise ClaimChangedException(
            batch_peak_id, expected_formula, anchor.consensus_formula or "unassigned"
        )
    return entry


async def _check_monoisotopic(session, anchor: Any, entry: dict) -> None:
    """Refuse an identity whose ion has its monoisotopic line elsewhere.

    Read off the ion itself rather than off how its formula is spelled: a
    labelled reagent's ion carries its label in brackets as a 13C line does,
    and only the line's mass tells a monoisotopic reading from an isotope one.
    An identity whose ion cannot be generated is not refused here.
    """
    mechanism_id = entry.get("ionization_mechanism_id")
    mechanism = (await fetch_mechanisms_by_id({mechanism_id})).get(mechanism_id)
    if mechanism is None:
        return
    mono = await asyncio.to_thread(monoisotopic_mz, entry["formula"], mechanism)
    if mono is None or abs(anchor.mz - mono) <= MONOISOTOPIC_WINDOW:
        return
    window = mono * PARENT_SEARCH_PPM * 1e-6
    nearby = (
        (
            await session.execute(
                select(BatchPeak).where(
                    BatchPeak.sample_batch_id == anchor.sample_batch_id,
                    BatchPeak.ionization_mode_id.is_not_distinct_from(
                        anchor.ionization_mode_id
                    ),
                    # The parent is in the anchor's own partition: an anchor
                    # of the batch's other instrument type is another trace.
                    BatchPeak.intensity_variable.is_not_distinct_from(
                        anchor.intensity_variable
                    ),
                    BatchPeak.mz.between(mono - window, mono + window),
                )
            )
        )
        .scalars()
        .all()
    )
    by_id = {peak.batch_peak_id: peak for peak in nearby}
    peaks = AnchorSet(
        Anchor(peak.batch_peak_id, peak.mz, peak.mz_tol_ppm) for peak in nearby
    )
    found = peaks.find(mono)
    parent = by_id[peaks.get(found).batch_peak_id] if found is not None else None
    raise IsotopeLineException(anchor, entry, mono, parent)


@api_controller()
async def validate_curation(
    sample_batch_id: str,
    batch_peak_id: str,
    candidate: int,
    expected_formula: Optional[str] = None,
) -> dict:
    """The route's synchronous check before the task is launched, so a stale
    claim, an unknown candidate or a foreign anchor is refused on the request
    rather than reported through a notification.

    :return: The identity about to be pinned.
    """
    async with async_session() as session:
        anchor = await session.get(BatchPeak, batch_peak_id)
        _check(anchor, sample_batch_id, batch_peak_id)
        entry = _check_claim(anchor, batch_peak_id, candidate, expected_formula)
        await _check_monoisotopic(session, anchor, entry)
    return {"formula": entry.get("formula"), "mz": anchor.mz}


def _reading_of(member: Any, anchor: Any) -> dict:
    """What a member reads now, in the measurement's terms."""
    identity = resolve_candidate(anchor.candidates, member.candidate)
    formula = identity.get("formula")
    return {
        "assigned_formula": formula,
        "ion_formula": identity.get("ion_formula"),
        "ionization_mechanism_id": identity.get("ionization_mechanism_id"),
        "fit_score": member.fit_score,
        "evidence": evidence_for(member.fit_score, formula) if formula else None,
        "tier": tier_name(member.tier),
    }


def candidate_measurement(
    entry: dict,
    sample_peak_id: str,
    scored: tuple,
    config: PeakAssignmentConfig,
) -> dict:
    """The numbers a pin of ``entry`` would write on the member at
    ``sample_peak_id``, read off one seeded scoring of the identity.

    The fit, evidence and tier are :func:`batch_untargeted.measured_reading`'s,
    the very reading the propagation writes; ``takes_member`` says whether the
    pin re-points the member at all (False: the envelope did not reach its
    peak, and it keeps what it reads). The lines are every isotopologue the ion
    predicts, each with the peak of this sample it paired to, if any.

    :param entry: The registry entry being measured.
    :param sample_peak_id: The member's peak.
    :param scored: ``score_in_sample``'s four maps.
    :param config: The configuration the pin is measured under.
    """
    ion_by_seed, fit_by_ion, errors_by_pairing, scored_df = scored
    formula = entry["formula"]
    annotation = Annotation(
        formula=formula,
        ion_formula=entry.get("ion_formula"),
        ionization_mechanism_id=entry.get("ionization_mechanism_id"),
        role=ROLE_M0,
        owner_batch_peak_id=None,
    )
    reading = measured_reading(
        annotation, sample_peak_id, ion_by_seed, fit_by_ion, errors_by_pairing, config
    )
    ion_id = ion_by_seed.get((formula, annotation.ionization_mechanism_id))
    lines: list[dict] = []
    if (
        ion_id is not None
        and not scored_df.empty
        and "target_ion_id" in scored_df.columns
    ):
        lines = isotopologue_rows(
            scored_df[scored_df["target_ion_id"].astype(str) == str(ion_id)]
        )
    errors = errors_by_pairing.get((ion_id, sample_peak_id), {}) if ion_id else {}
    return {
        "plausibility": round(float(formula_plausibility(formula)), 4),
        "fit_score": reading.fit if reading else None,
        "evidence": reading.evidence if reading else None,
        "tier": reading.tier if reading else None,
        "mz_error_ppm": errors.get("mz_error_ppm"),
        "abundance_error": errors.get("abundance_error"),
        "lines": lines,
        "lines_found": sum(1 for line in lines if line.get("sample_peak_id")),
        "takes_member": reading is not None,
        "blocked_reason": None
        if reading is not None
        else (
            "the ion could not be generated under its adduct"
            if ion_id is None
            else "the ion's envelope does not reach this sample's peak, so a pin "
            "leaves the sample reading what it does now"
        ),
    }


@api_controller()
async def measure_batch_peak_candidate(
    sample_batch_id: str,
    batch_peak_id: str,
    candidate: int,
    sample_item_id: str,
    config: PeakAssignmentConfig | None = None,
) -> dict:
    """Measure one of a batch peak's identities on one sample's member, before
    it is pinned: what "use this" would write there, beside what it reads now.

    Nothing is written. An identity the pin would refuse (an isotope line of an
    ion whose monoisotopic line is elsewhere) is answered with the refusal as
    its ``blocked_reason``; one the member already carries is answered with the
    member's own numbers, which a pin leaves as they are.

    :param sample_batch_id: The batch.
    :param batch_peak_id: The batch peak (anchor).
    :param candidate: Index into its identity registry.
    :param sample_item_id: The sample whose member is measured.
    :param config: The configuration the pin measures under; the default's,
        as :func:`curate_batch_peak` uses.
    :return: Status envelope with one entry.
    :raises NotFoundException: The batch peak is not this batch's, or the
        sample holds no member of it.
    :raises UnknownCandidateException: The index names no identity.
    """
    config = config or PeakAssignmentConfig()
    async with async_session() as session:
        anchor = await session.get(BatchPeak, batch_peak_id)
        _check(anchor, sample_batch_id, batch_peak_id)
        entry = registry_entry(anchor, candidate)
        if entry is None:
            raise UnknownCandidateException(batch_peak_id, candidate)
        member = (
            await session.execute(
                select(BatchPeakOccurrence).where(
                    BatchPeakOccurrence.batch_peak_id == batch_peak_id,
                    BatchPeakOccurrence.sample_item_id == sample_item_id,
                )
            )
        ).scalar_one_or_none()
        if member is None:
            raise NotFoundException(
                f"Sample '{sample_item_id}' holds no member of batch peak "
                f"'{batch_peak_id}'"
            )
        refusal: Optional[str] = None
        try:
            await _check_monoisotopic(session, anchor, entry)
        except IsotopeLineException as refused:
            refusal = refused.detail
        current = _reading_of(member, anchor)
        sample_peak_id = str(member.sample_peak_id)
        carried = member.candidate == candidate
    measurement: dict[str, Any] = {
        "batch_peak_id": batch_peak_id,
        "sample_item_id": sample_item_id,
        "sample_peak_id": sample_peak_id,
        "candidate": candidate,
        "assigned_formula": entry.get("formula"),
        "ion_formula": entry.get("ion_formula"),
        "ionization_mechanism_id": entry.get("ionization_mechanism_id"),
        "current": current,
    }
    if refusal is not None:
        measurement.update(
            plausibility=round(float(formula_plausibility(entry["formula"])), 4),
            takes_member=False,
            blocked_reason=refusal,
        )
    elif carried:
        measurement.update(
            plausibility=round(float(formula_plausibility(entry["formula"])), 4),
            fit_score=current["fit_score"],
            evidence=current["evidence"],
            tier=current["tier"],
            takes_member=False,
            blocked_reason="this sample already reads this identity",
        )
    elif not entry.get("ionization_mechanism_id"):
        measurement.update(
            plausibility=round(float(formula_plausibility(entry["formula"])), 4),
            takes_member=False,
            blocked_reason="the identity names no adduct, and the ion is built from one",
        )
    else:
        scored = await score_in_sample(
            sample_item_id,
            {(entry["formula"], entry["ionization_mechanism_id"])},
            config,
        )
        measurement.update(candidate_measurement(entry, sample_peak_id, scored, config))
    scored_ok = measurement.get("takes_member")
    return {
        "status": "success",
        "message": (
            f"Measured {entry.get('formula')} on the sample's peak at batch peak "
            f"m/z {anchor.mz:.4f}"
            if scored_ok
            else f"{entry.get('formula')} was not measured on the sample's peak: "
            f"{measurement.get('blocked_reason')}"
        ),
        "results": 1,
        "data": [measurement],
    }


async def _pin(
    sample_batch_id: str,
    batch_peak_id: str,
    candidate: int,
    expected_formula: Optional[str],
    user_id: Optional[int],
    measured: Optional[dict] = None,
) -> tuple[dict, list[str]]:
    """Write the pin under the fold lock, re-checking the claim: it may have moved
    between the route's check and this task starting.

    :return: The pin, and the samples holding a member not yet carrying it.
    """
    async with async_session() as session:
        await _acquire_batch_fold_lock(session, sample_batch_id)
        anchor = await session.get(BatchPeak, batch_peak_id)
        _check(anchor, sample_batch_id, batch_peak_id)
        entry = _check_claim(anchor, batch_peak_id, candidate, expected_formula)
        await _check_monoisotopic(session, anchor, entry)
        now = dt.now(timezone.utc)
        pin = manual_pin(anchor, candidate, user_id, now.isoformat(), measured)
        provenance = (
            dict(anchor.provenance) if isinstance(anchor.provenance, dict) else {}
        )
        provenance["manual"] = pin
        anchor.provenance = provenance
        anchor.batch_peak_utc_modified = now
        samples = (
            (
                await session.execute(
                    select(BatchPeakOccurrence.sample_item_id)
                    .where(
                        BatchPeakOccurrence.batch_peak_id == batch_peak_id,
                        BatchPeakOccurrence.candidate.is_distinct_from(candidate),
                    )
                    .order_by(BatchPeakOccurrence.sample_item_id)
                )
            )
            .scalars()
            .all()
        )
        await touch_ledger(session, sample_batch_id)
        await session.commit()
    return pin, list(samples)


async def _archive_displaced(
    sample_batch_id: str, batch_peak_id: str, displaced: list[dict]
) -> None:
    """Record what the re-pointed members read before, on the pin."""
    if not displaced:
        return
    async with async_session() as session:
        await _acquire_batch_fold_lock(session, sample_batch_id)
        anchor = await session.get(BatchPeak, batch_peak_id)
        pin = manual_pin_of(anchor) if anchor is not None else None
        if pin is None:
            return
        provenance = dict(anchor.provenance)
        provenance["manual"] = {**pin, "displaced_members": displaced}
        anchor.provenance = provenance
        await touch_ledger(session, sample_batch_id)
        await session.commit()


async def run_batch_peak_curation(
    sample_batch_id: str,
    batch_peak_id: str,
    candidate: int,
    expected_formula: Optional[str],
    config: PeakAssignmentConfig,
    user_id: Optional[int] = None,
    process_id: Optional[str] = None,
    parent_id: Optional[str] = None,
    measured_sample_item_id: Optional[str] = None,
) -> dict:
    """Pin, propagate, recompute - see the module docstring.

    :param measured_sample_item_id: The sample the curator measured the
        identity on before pinning it; measured again here, on the server, and
        the result recorded on the pin. A measurement that fails leaves the pin
        without one rather than unmade.
    :return: Counts: the formula pinned, samples measured, members re-pointed.
    """
    measured = None
    if measured_sample_item_id is not None:
        try:
            result = await measure_batch_peak_candidate(
                sample_batch_id,
                batch_peak_id,
                candidate,
                measured_sample_item_id,
                config,
            )
            measured = result["data"][0]
        except Exception:  # noqa: BLE001 - the record must not cost the pin
            runtime.logger.exception(
                f"Measuring candidate {candidate} of batch peak '{batch_peak_id}' "
                f"on sample '{measured_sample_item_id}' for the pin failed."
            )
    pin, samples = await _pin(
        sample_batch_id, batch_peak_id, candidate, expected_formula, user_id, measured
    )
    counts = {
        "formula": pin["formula"],
        "samples_measured": 0,
        "members_repointed": 0,
        "samples_failed": 0,
    }
    annotation = Annotation(
        formula=pin["formula"],
        ion_formula=pin.get("ion_formula"),
        ionization_mechanism_id=pin.get("ionization_mechanism_id"),
        role=ROLE_M0,
        owner_batch_peak_id=None,
    )
    displaced: list[dict] = []
    total = len(samples)
    walked_the_batch = False
    try:
        for step, sample_item_id in enumerate(samples):
            if process_id is not None:
                await send_progress_user_notification(
                    _progress(
                        sample_batch_id,
                        step,
                        total,
                        f"Measuring {pin['formula']}, sample {step + 1}/{total}.",
                        user_id,
                        process_id,
                        parent_id,
                    )
                )
            try:
                counts["members_repointed"] += await _propagate_to_sample(
                    sample_batch_id,
                    sample_item_id,
                    {batch_peak_id: annotation},
                    config,
                    only_unassigned=False,
                    skip_candidate={batch_peak_id: candidate},
                    displaced=displaced,
                )
                counts["samples_measured"] += 1
            except Exception:  # noqa: BLE001 - one bad sample must not abort the pin
                # The pin is already written and the other samples still hold the
                # peak; a sample that cannot be measured keeps what it read
                # before, which is what `release-curation` would restore anyway.
                counts["samples_failed"] += 1
                runtime.logger.exception(
                    f"Measuring the pinned {pin['formula']} failed for sample "
                    f"'{sample_item_id}'."
                )
        walked_the_batch = True
    finally:
        # Both of these belong to every sample already committed above, so they
        # run whether or not the loop finished - the archive above all: it is the
        # only record of what the re-pointed members read before, and
        # `release_batch_peak_curation` restores from nothing else. Written from
        # the partial list, it puts back exactly the members that were moved.
        try:
            await _archive_displaced(sample_batch_id, batch_peak_id, displaced)
            await recompute_batch_consensus(sample_batch_id, {batch_peak_id})
        except Exception:
            if walked_the_batch:
                raise
            # Already unwinding. Raising here would REPLACE the exception that
            # interrupted the loop - a CancelledError above all, which has to
            # reach the caller as a cancellation and not as a database error.
            runtime.logger.exception(
                "The batch curation was interrupted and its deferred archive and "
                f"consensus pass then failed for batch '{sample_batch_id}'. The "
                "members it re-pointed may have no undo record; rebuilding the "
                "ledger recomputes the anchor."
            )
    return counts


@api_controller_background_task(
    success_notification_rooms=["sample_batch_id"],
    success_reload=[("peak_assignment", "sample_batch_id")],
    error_notification_rooms=["sample_batch_id"],
)
async def curate_batch_peak(
    sample_batch_id: str,
    batch_peak_id: str,
    candidate: int,
    expected_formula: Optional[str] = None,
    config: PeakAssignmentConfig | None = None,
    independent_transaction: bool = False,
    user_id: int | None = None,
    process_id: str | None = None,
    parent_id: str | None = None,
    measured_sample_item_id: str | None = None,
) -> dict:
    """Pin one of a batch peak's identities as its species for the whole batch,
    measure it in every sample holding the peak, and recompute the anchor.
    Emits ``peak_assignment_reload`` so the ledgers refresh."""
    counts = await run_batch_peak_curation(
        sample_batch_id,
        batch_peak_id,
        candidate,
        expected_formula,
        config or PeakAssignmentConfig(),
        user_id=user_id,
        process_id=process_id,
        parent_id=parent_id,
        measured_sample_item_id=measured_sample_item_id,
    )
    return curation_outcome(counts, sample_batch_id)


@api_controller()
async def release_batch_peak_curation(sample_batch_id: str, batch_peak_id: str) -> dict:
    """Undo a manual curation: put the re-pointed members back where nobody has
    changed them since, drop the pin, recompute the plain vote.

    A member is restored only while it still carries the pinned identity - one a
    later fold or curation has moved on is somebody else's now and is left
    alone. Nothing is deleted: the identities stay in the registry, and the
    vote runs over what the members read.

    :return: Status envelope with the members restored and left alone.
    """
    async with async_session() as session:
        await _acquire_batch_fold_lock(session, sample_batch_id)
        anchor = await session.get(BatchPeak, batch_peak_id)
        _check(anchor, sample_batch_id, batch_peak_id)
        pin = manual_pin_of(anchor)
        if pin is None:
            raise NotCuratedException(batch_peak_id)
        archived = {
            state["sample_item_id"]: state
            for state in pin.get("displaced_members") or []
            if isinstance(state, dict) and state.get("sample_item_id")
        }
        restored = skipped = 0
        if archived:
            members = (
                (
                    await session.execute(
                        select(BatchPeakOccurrence).where(
                            BatchPeakOccurrence.batch_peak_id == batch_peak_id,
                            BatchPeakOccurrence.sample_item_id.in_(list(archived)),
                        )
                    )
                )
                .scalars()
                .all()
            )
            for member in members:
                if member.candidate == pin.get("candidate"):
                    restore_member(member, archived[member.sample_item_id])
                    restored += 1
                else:
                    skipped += 1
        provenance = dict(anchor.provenance)
        provenance.pop("manual", None)
        anchor.provenance = provenance
        anchor.batch_peak_utc_modified = dt.now(timezone.utc)
        formula = pin.get("formula")
        await touch_ledger(session, sample_batch_id)
        await session.commit()
    await recompute_batch_consensus(sample_batch_id, {batch_peak_id})
    return {
        "status": "success",
        "message": (
            f"Released the manual curation ({formula}) on the batch peak; "
            f"{restored} sample{'s' if restored != 1 else ''} put back, "
            f"{skipped} left as since changed."
        ),
        "results": restored,
        "data": [{"restored": restored, "skipped": skipped, "formula": formula}],
    }


__all__ = [
    "ACTION_PROMOTE_IDENTITY",
    "ISOTOPE_LINE_CODE",
    "NOTIFICATION_TYPE",
    "NOT_CURATED_CODE",
    "UNKNOWN_CANDIDATE_CODE",
    "curate_batch_peak",
    "curation_outcome",
    "candidate_measurement",
    "manual_pin",
    "measure_batch_peak_candidate",
    "member_state",
    "monoisotopic_mz",
    "registry_entry",
    "release_batch_peak_curation",
    "restore_member",
    "run_batch_peak_curation",
    "validate_curation",
]
