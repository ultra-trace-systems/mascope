"""Verdicts on rows and on batch peaks, and the verification queue."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from mascope_backend.api.new.peak_assignments.claim_schemas import (
    ClaimFields,
    as_utc,
    refuse_bad_claim,
)
from mascope_backend.api.new.peak_assignments.claims import statement, window_problem
from mascope_backend.api.new.peak_assignments.schemas_vocabulary import (
    EvidenceLevel,
    Verdict,
)


class VerifyAssignmentBody(ClaimFields):
    """Request body to record a verification verdict on an assignment (V1 capture)."""

    peak_assignment_id: str = Field(description="The assignment being verified.")
    verdict: Verdict = Field(
        description="confirmed | rejected | unsure. confirmed/rejected are calibration labels."
    )
    evidence_level: EvidenceLevel | None = Field(
        None,
        description=(
            "Why the user is confident: reference_standard (authentic standard) | msms "
            "(MS/MS or diagnostic fragments) | orthogonal (RT, etc.) | pattern (isotope + "
            "adduct corroboration) | visual (manual review only). Required for 'confirmed'."
        ),
    )
    note: str | None = Field(None, description="Optional free-text note.")

    @model_validator(mode="after")
    def _confirmed_needs_evidence(self) -> "VerifyAssignmentBody":
        # A confirmation with no stated basis is exactly the label the confirmation-bias
        # guardrail wants to avoid, so require an evidence level to confirm.
        if self.verdict == "confirmed" and self.evidence_level is None:
            raise ValueError("evidence_level is required when verdict is 'confirmed'")
        refuse_bad_claim(self)
        return self


class AssignmentVerificationRecord(BaseModel):
    """One recorded verification verdict."""

    assignment_verification_id: str
    sample_item_id: str
    peak_assignment_id: str | None = None
    peak_assignment_run_id: str | None = None
    sample_peak_id: str
    assigned_formula: str | None = None
    ionization_mechanism_id: str | None = None
    verdict: str
    evidence_level: str | None = None
    fit_score: float | None = None
    evidence: float | None = None
    p_correct: float | None = None
    note: str | None = None
    #: What the verdict was recorded under: the level mapping's version, on one
    #: recorded since verdicts state a level.
    context: dict | None = None
    verified_by: int | None = None
    verified_utc: datetime | None = None
    #: Null on the current verdict for this identity; set on one a later verdict replaced.
    superseded_utc: datetime | None = None
    #: The structure a confirmation named; null on a verdict that names none.
    claimed_inchikey: str | None = None
    claimed_name: str | None = None
    #: The level the verdict states, fixed when it was recorded (the mapping's
    #: version is in ``context``); null where it states none, and a reader
    #: takes the engine's.
    level: int | None = None
    #: The scale's letter beside ``level`` (``2b``); null at level 1 and on a
    #: verdict recorded under version 1 of the mapping. ``context.claim`` has
    #: the evidence line and the lift.
    level_letter: str | None = None


class AssignmentVerificationsResponse(BaseModel):
    """Verifications recorded for a sample, newest first; superseded ones included."""

    status: str = "success"
    message: str
    results: int
    data: list[AssignmentVerificationRecord]


class VerificationQueueStale(BaseModel):
    """The live verdict a stale queue entry's run read differently."""

    #: ``reassigned``: the run commits another formula or mechanism on the
    #: judged peak. ``rescored``: the same one, at another evidence.
    kind: Literal["reassigned", "rescored"]
    verdict: str
    assigned_formula: str | None = None
    ionization_mechanism_id: str | None = None
    assignment_verification_id: str
    verified_utc: datetime | None = None


class VerificationQueueEntry(BaseModel):
    """One assignment worth a verdict, and why (``verification_queue``).

    Carries no ``p_correct`` and no evidence on purpose: the queue asks for a
    judgment of the data, and a number beside it would anchor it.
    """

    peak_assignment_id: str
    sample_peak_id: str
    #: ``stale``: a live verdict made before this run judged what the run since
    #: read differently. ``band_edge``: near a tier band edge, where few labels
    #: are held. ``spot_check``: a random draw from the rest.
    reason: Literal["stale", "band_edge", "spot_check"]
    #: The tier whose band edge the row sits nearest to, None without bands.
    near_band: str | None = None
    #: Labels the instrument class and reagent profile hold near the row's
    #: evidence: what the ranking weighed.
    labels_near: int
    stale: VerificationQueueStale | None = None


class VerificationQueueCounts(BaseModel):
    """How many entries of the queue each reason put there."""

    stale: int = 0
    band_edge: int = 0
    spot_check: int = 0


class VerificationQueueResponse(BaseModel):
    """A run's verification queue, in the order to ask (guardrail 3)."""

    status: str = "success"
    message: str
    results: int
    total: int
    #: The run ranked; None where the sample has none.
    peak_assignment_run_id: str | None = None
    counts: VerificationQueueCounts
    data: list[VerificationQueueEntry]


#: ``datetime`` under another name, for a model with a field of that name.
Timestamp = datetime


class ScopeVerificationQueryParams(BaseModel):
    """Filters on the verdicts of a batch or dataset."""

    verdict: Verdict | None = Field(None, description="Keep only this verdict.")
    evidence_level: EvidenceLevel | None = Field(
        None, description="Keep only verdicts recorded on this basis."
    )
    since: datetime | None = Field(
        None,
        description=(
            "Keep only verdicts recorded at or after this moment; a time without a "
            "zone is read as UTC."
        ),
    )
    include_superseded: bool = Field(
        False,
        description=(
            "Also list the per-sample verdicts a later one replaced. Batch-level "
            "verdicts are listed live only: a replaced one reaches no sample."
        ),
    )


class ScopeVerificationRecord(BaseModel):
    """One verdict on one sample's peak, as the sample's assignment ledger shows it.

    Either the sample's own verdict (``borrowed`` false, the
    ``assignment_verification_id`` and score snapshot set) or a batch-level
    verdict that reaches it (``borrowed`` true, ``batch_peak_verification_id``,
    ``batch_peak_id`` and ``stale`` set, no score snapshot).
    """

    sample_item_id: str
    sample_item_name: str | None = None
    sample_batch_id: str | None = None
    filename: str | None = None
    datetime: Timestamp | None = None
    datetime_utc: Timestamp | None = None
    sample_peak_id: str
    #: The observed peak's m/z; null when no ledger row or batch member records it.
    sample_peak_mz: float | None = None
    assigned_formula: str | None = None
    ionization_mechanism_id: str | None = None
    verdict: str
    evidence_level: str | None = None
    #: Score snapshot at verification time; null on a borrowed verdict.
    fit_score: float | None = None
    evidence: float | None = None
    p_correct: float | None = None
    note: str | None = None
    context: dict | None = None
    verified_by: int | None = None
    verified_utc: Timestamp | None = None
    superseded_utc: Timestamp | None = None
    #: A batch-level verdict reaching this sample, not one recorded on it.
    borrowed: bool
    assignment_verification_id: str | None = None
    peak_assignment_id: str | None = None
    peak_assignment_run_id: str | None = None
    batch_peak_verification_id: str | None = None
    batch_peak_id: str | None = None
    #: On a borrowed verdict: the batch peak's consensus has left the claim judged.
    stale: bool | None = None
    #: The structure a confirmation named; null on a verdict that names none,
    #: and where a batch-level claim's window leaves this sample out.
    claimed_inchikey: str | None = None
    claimed_name: str | None = None
    #: The level the verdict states for this sample, fixed when it was
    #: recorded (the mapping's version is in ``context``); null where it states
    #: none, and where a batch-level claim's window leaves this sample out, so
    #: a reader takes the engine's.
    level: int | None = None
    level_letter: str | None = None
    #: On a borrowed level-1 claim: the first and last acquisition time it covers.
    window_start_utc: Timestamp | None = None
    window_end_utc: Timestamp | None = None
    #: On a borrowed verdict: its claim's window leaves this sample out, so
    #: neither its structure nor its level reaches the sample's row.
    outside_window: bool | None = None


class ScopeVerificationsResponse(BaseModel):
    """The verdicts over every sample of a batch or dataset."""

    status: str = "success"
    message: str
    results: int
    data: list[ScopeVerificationRecord]


class VerifyBatchPeakBody(ClaimFields):
    """Request body to record a batch-level verdict on a batch peak's species claim."""

    batch_peak_id: str = Field(description="The batch peak (anchor) being judged.")
    verdict: Verdict = Field(
        description="confirmed | rejected | unsure - one judgment per species at this anchor."
    )
    evidence_level: EvidenceLevel | None = Field(
        None,
        description=(
            "Why the user is confident, as for a per-sample verification. Required for "
            "'confirmed'."
        ),
    )
    note: str | None = Field(None, description="Optional free-text note.")
    expected_formula: str | None = Field(
        None,
        description=(
            "The consensus formula the user judged. Required to confirm or reject: an "
            "anchor's claim can change under another sample's fold between the row being "
            "read and this request landing, and a mismatch is refused (409) rather than "
            "recorded against a formula the user never saw."
        ),
    )

    window_start_utc: datetime | None = Field(
        None,
        description=(
            "A level-1 claim's scope on a batch peak: the first acquisition time "
            "its standard covers, required with window_end_utc on a level-1 "
            "claim and refused on any other. A member acquired outside the "
            "window names no structure and reads the engine's level. A time "
            "without a zone is UTC."
        ),
    )
    window_end_utc: datetime | None = Field(
        None, description="The last acquisition time a level-1 claim covers."
    )

    _window_utc = field_validator("window_start_utc", "window_end_utc")(as_utc)

    @model_validator(mode="after")
    def _guards(self) -> "VerifyBatchPeakBody":
        if self.verdict == "confirmed" and self.evidence_level is None:
            raise ValueError("evidence_level is required when verdict is 'confirmed'")
        if self.verdict in ("confirmed", "rejected") and not self.expected_formula:
            raise ValueError(
                "expected_formula is required when verdict is 'confirmed' or 'rejected'"
            )
        refuse_bad_claim(self)
        problem = window_problem(
            statement(
                self.verdict,
                self.evidence_level,
                self.evidence_detail,
                self.independent_line,
            ),
            self.window_start_utc,
            self.window_end_utc,
        )
        if problem:
            raise ValueError(problem)
        return self


class RetractBatchPeakVerdictBody(BaseModel):
    """Request body to withdraw the live batch-level verdict(s) on a batch peak."""

    batch_peak_id: str = Field(description="The batch peak whose verdict to retract.")
    assigned_formula: str | None = Field(
        None,
        description=(
            "Retract only the live verdict on this claim; omit to retract every live "
            "verdict on the batch peak, stale ones included."
        ),
    )
    ionization_mechanism_id: str | None = Field(
        None, description="The claim's mechanism, with assigned_formula."
    )


class BatchPeakVerificationRecord(BaseModel):
    """One batch-level verdict, with the anchor's present claim beside the one judged."""

    batch_peak_verification_id: str
    sample_batch_id: str
    batch_peak_id: str
    assigned_formula: str
    ionization_mechanism_id: str | None = None
    verdict: str
    evidence_level: str | None = None
    note: str | None = None
    context: dict | None = None
    verified_by: int | None = None
    verified_utc: datetime | None = None
    superseded_utc: datetime | None = None
    #: The anchor's consensus now; null when the anchor is gone.
    current_formula: str | None = None
    current_ionization_mechanism_id: str | None = None
    anchor_present: bool
    #: A live verdict about a claim the anchor no longer makes, or whose anchor is gone.
    stale: bool
    #: On anchor-context rows: the focused sample's peak the verdict reaches.
    sample_peak_id: str | None = None
    #: The structure a confirmation named; null on a verdict that names none.
    #: On an anchor-context row, null too where the claim's window leaves the
    #: focused sample out (``outside_window``).
    claimed_inchikey: str | None = None
    claimed_name: str | None = None
    #: The level the verdict states, fixed when it was recorded (the mapping's
    #: version is in ``context``); null where it states none, and a reader
    #: takes the engine's. On an anchor-context row, null too where the
    #: claim's window leaves the focused sample out (``outside_window``).
    level: int | None = None
    #: The scale's letter beside ``level``; null at level 1 and under version 1.
    level_letter: str | None = None
    #: A level-1 claim's scope: the first and last acquisition time it covers.
    window_start_utc: datetime | None = None
    window_end_utc: datetime | None = None
    #: On anchor-context rows: the claim's window leaves the focused sample out,
    #: so neither its structure nor its level reaches the sample's row.
    outside_window: bool | None = None


class BatchPeakVerificationsResponse(BaseModel):
    """Batch-level verdicts, newest first; superseded ones included where listed."""

    status: str = "success"
    message: str
    results: int
    data: list[BatchPeakVerificationRecord]
