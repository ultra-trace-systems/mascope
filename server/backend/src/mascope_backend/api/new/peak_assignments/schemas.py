"""Request and response schemas for the peak assignments API."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from mascope_backend.api.new.peak_assignments.config import PeakAssignmentConfig


# Verification vocabulary (verification-calibration loop V1). Verdict is the label;
# evidence_level records why the user is confident -- the guardrail that lets the eventual
# calibration weight a reference-standard confirmation above a visual guess.
Verdict = Literal["confirmed", "rejected", "unsure"]
EvidenceLevel = Literal["reference_standard", "msms", "orthogonal", "pattern", "visual"]

# The assignment vocabulary, mirrored from the engine's constants. Typed rather
# than free strings so a misspelled filter is a 422 naming the accepted values,
# not a 200 with an empty ledger that reads as "this sample has no such peaks".
AssignmentTier = Literal["identified", "candidate", "below_assignability", "unassigned"]
AssignmentRole = Literal["M0", "iso_child", "reagent", "artifact", "unassigned"]
AssignmentSource = Literal["database", "untargeted"]

# A run holds one row per detected peak, so an unbounded read serializes tens
# of megabytes through Pydantic on the event loop. Clients page instead; the
# response carries `total` so they know when they have the run.
DEFAULT_PAGE_LIMIT = 1000
MAX_PAGE_LIMIT = 5000


class PeakAssignmentRunRecord(BaseModel):
    """One peak assignment run over a sample."""

    peak_assignment_run_id: str
    sample_item_id: str
    engine_version: str
    status: str
    config: dict | None = None
    error: str | None = None
    peak_assignment_run_utc_created: datetime | None = None
    peak_assignment_run_utc_completed: datetime | None = None


class PeakAssignmentRecord(BaseModel):
    """One observed peak with its committed assignment (slim ledger row).

    The list endpoint serves this slim projection: the `alternatives` and
    `provenance` JSON blobs are roughly three quarters of a full row's bytes
    and are read only when a user inspects a single peak, so they live on
    :class:`PeakAssignmentDetailRecord` (the per-assignment detail endpoint)
    instead. The few provenance-derived scalars the ledger columns render are
    flattened onto the row.
    """

    peak_assignment_id: str
    peak_assignment_run_id: str
    sample_item_id: str
    sample_peak_id: str
    sample_peak_mz: float
    sample_peak_intensity: float
    sample_peak_tof: float | None = None
    role: str
    assigned_formula: str | None = None
    ion_formula: str | None = None
    ionization_mechanism_id: str | None = None
    isotope_label: str | None = None
    isotope_formula: str | None = None
    source: str | None = None
    fit_score: float | None = None
    mz_error_ppm: float | None = None
    abundance_error: float | None = None
    tier: str
    target_compound_id: str | None = None
    target_ion_id: str | None = None
    owner_peak_assignment_id: str | None = None
    #: Calibrated probability the assignment is correct (provenance.p_correct),
    #: flattened for the ledger's sortable P(correct) column.
    p_correct: float | None = None
    #: Whether the calibration curve behind p_correct is provisional.
    p_correct_provisional: bool | None = None
    #: Number of adducts corroborating the compound (provenance.corroboration),
    #: flattened for the ledger's corroboration marker.
    corroboration_adducts: int | None = None


class PeakAssignmentDetailRecord(PeakAssignmentRecord):
    """A full assignment row: the slim ledger row plus the inspector detail."""

    alternatives: list | None = None
    provenance: dict | None = None


class PeakAssignmentsResponse(BaseModel):
    """Peaks-with-assignments for a sample.

    Uses the standard list envelope (status/message/results/data) like every
    other data-bearing endpoint. Run identity is denormalized onto each row
    (``peak_assignment_run_id``); full run metadata is served separately by the
    runs endpoint, so there is no run object echoed here.
    """

    status: str = "success"
    message: str
    results: int
    #: Rows matching the query across every page, so a client knows when paging
    #: is done. ``results`` is the size of this page.
    total: int = 0
    data: list[PeakAssignmentRecord]


class PeakAssignmentDetailResponse(BaseModel):
    """One full assignment record, with the inspector-only JSON detail."""

    status: str = "success"
    message: str
    results: int
    data: list[PeakAssignmentDetailRecord]


class PeakAssignmentRunsResponse(BaseModel):
    """Peak assignment runs of a sample, newest first."""

    status: str = "success"
    message: str
    results: int
    data: list[PeakAssignmentRunRecord]


class PeakAssignmentQueryParams(BaseModel):
    """Optional filters and paging for the peaks-with-assignments query."""

    peak_assignment_run_id: str | None = Field(
        None, description="Specific run to read; defaults to the latest completed run."
    )
    tier: AssignmentTier | None = Field(None, description="Filter by confidence tier.")
    role: AssignmentRole | None = Field(None, description="Filter by peak role.")
    source: AssignmentSource | None = Field(
        None, description="Filter by assignment source."
    )
    limit: int = Field(
        DEFAULT_PAGE_LIMIT,
        ge=1,
        le=MAX_PAGE_LIMIT,
        description=(
            "Maximum rows to return. A run holds one row per detected peak, so a "
            "dense sample runs to tens of thousands; read the whole run by paging "
            "with `offset` until the rows returned reach `total`."
        ),
    )
    offset: int = Field(0, ge=0, description="Rows to skip, for paging.")


class AssignSamplePeaksBody(BaseModel):
    """Request body for launching a peak assignment run."""

    config: PeakAssignmentConfig | None = Field(
        None,
        description=(
            "Optional run configuration; engine defaults are used when omitted."
        ),
    )


class CompositionFitBody(BaseModel):
    """Fit-view aggregate for an assigned composition (isotope table)."""

    assigned_formula: str
    ionization_mechanism_id: str


class CompositionVisualizeBody(BaseModel):
    """Fit-view visualization (sum spectrum + time series) for a composition."""

    assigned_formula: str
    ionization_mechanism_id: str
    peak_min_intensity: float = 0.0
    mz_tolerance: float = 10.0
    isotope_ratio_tolerance: float = 0.5


class VerifyAssignmentBody(BaseModel):
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
    verified_by: int | None = None
    verified_utc: datetime | None = None


class AssignmentVerificationsResponse(BaseModel):
    """Verifications recorded for a sample, newest first."""

    status: str = "success"
    message: str
    results: int
    data: list[AssignmentVerificationRecord]


class RecalibrateResponse(BaseModel):
    """Outcome of refitting an instrument's calibration from verification labels (V2)."""

    status: str = "success"
    message: str
    recalibrated: bool
    instrument: str
    before_ece: float | None = None
    after_ece: float | None = None
    n_pos: int = 0
    n_neg: int = 0
    n_strong_positives: int | None = None
    provisional: bool | None = None
