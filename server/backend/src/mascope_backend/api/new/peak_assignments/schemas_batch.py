"""A batch's peaks and its assignment status: curation, candidates, runs."""

from datetime import datetime

from pydantic import BaseModel, Field

from mascope_backend.api.new.peak_assignments.schemas_ledger import (
    MeasuredIsotopologueRecord,
)
from mascope_backend.api.new.peak_assignments.schemas_vocabulary import AssignmentTier


class CurateBatchPeakBody(BaseModel):
    """Request body to pin one of a batch peak's identities as its species, batch-wide."""

    batch_peak_id: str = Field(description="The batch peak (anchor) being curated.")
    candidate: int = Field(
        ge=0,
        description=(
            "Index into the batch peak's identity registry - the `candidate` an "
            "alternative of a derived row carries. The identities a batch peak can be "
            "assigned are the ones its members have carried."
        ),
    )
    expected_formula: str | None = Field(
        None,
        description=(
            "The consensus formula the user saw. A mismatch is refused (409): the "
            "claim can move under another sample's fold between the row being read "
            "and this request landing."
        ),
    )
    sample_item_id: str | None = Field(
        None,
        description=(
            "The sample the identity was measured on before the pin "
            "(`measure-candidate`). Measured there again and recorded on the pin "
            "as `provenance.manual.measured`; none recorded when left out."
        ),
    )


class BatchPeakCandidateReading(BaseModel):
    """What a sample's member of a batch peak reads now."""

    assigned_formula: str | None = None
    ion_formula: str | None = None
    ionization_mechanism_id: str | None = None
    fit_score: float | None = None
    #: `fit x plausibility`, the currency the tier is banded on.
    evidence: float | None = None
    tier: AssignmentTier | None = None


class BatchPeakCandidateMeasurementRecord(BaseModel):
    """One of a batch peak's identities measured on one sample's member before
    it is pinned: what the pin would write there, beside what it reads now.

    Session data, never written onto the ledger; a pin made from this sample
    records the same measurement under `provenance.manual.measured`.
    """

    batch_peak_id: str
    sample_item_id: str
    sample_peak_id: str
    #: The registry index measured, as `curate` names it.
    candidate: int
    assigned_formula: str | None = None
    ion_formula: str | None = None
    ionization_mechanism_id: str | None = None
    #: Seven Golden Rules plausibility, a pure function of the formula.
    plausibility: float | None = None
    #: The ion's fit, the evidence and the tier a pin writes on the member;
    #: absent when the envelope does not reach the member's peak.
    fit_score: float | None = None
    evidence: float | None = None
    tier: AssignmentTier | None = None
    mz_error_ppm: float | None = None
    abundance_error: float | None = None
    #: Every isotopologue the ion predicts, each with the peak of this sample
    #: it paired to (absent when the spectrum holds none there).
    lines: list[MeasuredIsotopologueRecord] = []
    lines_found: int = 0
    #: Whether a pin re-points this member; False keeps what it reads.
    takes_member: bool = False
    blocked_reason: str | None = None
    current: BatchPeakCandidateReading


class BatchPeakCandidateMeasurementResponse(BaseModel):
    """A batch peak identity measured on one sample (one entry)."""

    status: str = "success"
    message: str
    results: int
    data: list[BatchPeakCandidateMeasurementRecord]


class ReleaseBatchPeakCurationBody(BaseModel):
    """Request body to undo a batch peak's manual curation."""

    batch_peak_id: str = Field(description="The batch peak whose curation to release.")


class SampleAssignmentRunRecord(BaseModel):
    """A sample's latest completed assignment run of its own, in brief."""

    peak_assignment_run_id: str
    engine: str
    engine_version: str
    peak_assignment_run_utc_created: datetime | None = None


class BatchSampleAssignmentStatusRecord(BaseModel):
    """One sample of a batch: its latest completed run of its own, if any, and
    what the batch ledger holds for it. A sample folded into the ledger is
    served from it even without a run, so both are reported."""

    sample_item_id: str
    run: SampleAssignmentRunRecord | None = None
    #: The sample's members of the batch ledger, and how many carry an assignment.
    n_members: int
    n_assigned: int


class BatchSampleAssignmentStatusResponse(BaseModel):
    """Every sample of a batch with its assignment status."""

    status: str = "success"
    message: str
    results: int
    data: list[BatchSampleAssignmentStatusRecord]


class BatchAssignmentUser(BaseModel):
    """Who started a pass."""

    id: int
    username: str | None = None


class BatchAssignmentLastPass(BaseModel):
    """The batch's newest completed pass: what ``behind`` is measured against."""

    batch_peak_run_id: str
    #: assign, or the step whose own route started the pass.
    action: str
    #: The stage it started at: ``fold`` where it folded first.
    first_stage: str | None = None
    utc_started: datetime | None = None
    utc_completed: datetime | None = None
    started_by: BatchAssignmentUser | None = None
    engine_version: str
    #: The hash of the settings it ran on; null for a pass from before the
    #: batch kept assignment settings of its own (MAS-1014).
    settings_hash: str | None = None
    #: The samples it covered.
    n_samples: int


class BatchAssignmentStoppedPass(BaseModel):
    """The batch's newest pass, where it failed or was cancelled."""

    batch_peak_run_id: str
    action: str
    #: failed | cancelled
    status: str
    first_stage: str | None = None
    #: The stage it stopped in; null where it stopped before any.
    stopped_at: str | None = None
    error: str | None = None
    utc_started: datetime | None = None
    utc_completed: datetime | None = None
    started_by: BatchAssignmentUser | None = None


class BatchAssignmentRunning(BaseModel):
    """The pass, or the batch run, in flight."""

    batch_peak_run_id: str | None = None
    action: str | None = None
    #: The process its progress packets go out as; null for a run that sends
    #: none of its own, or before its first stage starts.
    process_id: str | None = None
    started_by: BatchAssignmentUser | None = None
    utc_started: datetime | None = None
    #: The stage it is in (``fold`` while it folds), its label, which of how
    #: many, and when it started.
    stage: str | None = None
    label: str | None = None
    stage_index: int | None = None
    n_stages: int | None = None
    utc_stage_started: datetime | None = None
    #: The share of its stages finished, 0 to 1.
    progress: float | None = None
    cancel_requested_utc: datetime | None = None


class BatchAssignmentBehindReason(BaseModel):
    """One reason the batch is behind its last pass."""

    #: new_samples | removed_samples | resampled | library_changed |
    #: settings_changed | engine_changed | stages_stale
    reason: str
    count: int
    #: The sample reasons: the first of the samples, by id.
    sample_item_ids: list[str] | None = None
    #: library_changed: the collections and lists by id; settings_changed:
    #: the pass's settings hash and the batch's; engine_changed: the pass's
    #: version and today's; stages_stale: each stage and its mark.
    detail: dict | list | None = None


class BatchAssignmentStatus(BaseModel):
    """Where a batch's assignment stands (``batch_assignment_status``)."""

    sample_batch_id: str
    #: none | folded_only | running | current | behind | failed | cancelled
    state: str
    #: The engine version a pass would run under now.
    engine_version: str
    #: The hash of the batch's assignment settings, the defaults where it
    #: saved none: what a pass would record now, unless a step's route is
    #: given settings of its own.
    settings_hash: str | None = None
    n_samples: int
    n_batch_peaks: int
    last_pass: BatchAssignmentLastPass | None = None
    stopped_pass: BatchAssignmentStoppedPass | None = None
    running: BatchAssignmentRunning | None = None
    #: Every stage in order, as the ledger serves them, each with the samples
    #: it could not read and skipped (``skipped_samples``, by id and name).
    stages: list[dict]
    #: The stale stages, first first: the next pass starts at the first.
    stale_stages: list[str]
    #: Whether the next pass folds every sample first, and why.
    needs_fold: bool
    fold_reasons: list[str]
    behind: list[BatchAssignmentBehindReason]


class BatchAssignmentStatusResponse(BaseModel):
    """Where a batch's assignment stands, and why it is behind."""

    status: str = "success"
    message: str
    results: int
    data: BatchAssignmentStatus


class BatchPeakRunRecord(BaseModel):
    """One batch run: a batch-level operation that rewrote the batch ledger."""

    batch_peak_run_id: str
    sample_batch_id: str
    #: fold | rebuild | import | assign (a pass of the batch stages) | the
    #: step whose own route started a pass: search_untargeted | detect_series
    #: | read_co_variation | stamp_isotopologues | strip_isotopologues |
    #: read_batch_checks | decide_known_species | measure_water_ladders
    action: str
    engine: str
    engine_version: str
    #: running | completed | failed | cancelled (a pass a person stopped)
    status: str
    #: The run whose state the live ledger holds; exactly one per batch.
    current: bool
    is_current: bool
    #: A pass: ``from_stage``, ``fold`` and the ``settings`` it was given.
    config: dict | None = None
    #: A pass: ``stages``, one entry per stage in order with its ``status``
    #: (done, failed, cancelled, not_run), start, end and the outcome of each
    #: of its steps; the ``first_stage``; the ``fold`` and why; the
    #: ``samples`` it covered and the ``library`` it read
    #: (``batch_stages.assign_batch``). Another run: its counts.
    summary: dict | None = None
    error: str | None = None
    created_by: int | None = None
    batch_peak_run_utc_created: datetime | None = None
    batch_peak_run_utc_completed: datetime | None = None
    #: When this run's ledger state was captured - set as the next run started.
    snapshot_utc: datetime | None = None
    #: When the live ledger was last written while this run was current: a
    #: fold, a curation, or a search's or an import's rows, which make no run
    #: of their own. Null until the first such write.
    ledger_utc_modified: datetime | None = None
    #: When a person asked the pass to stop; null where nobody did.
    cancel_requested_utc: datetime | None = None


class BatchPeakRunsResponse(BaseModel):
    """A batch's runs, newest first."""

    status: str = "success"
    message: str
    results: int
    data: list[BatchPeakRunRecord]
