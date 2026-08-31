"""Request and response schemas for the peak assignments API."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import (
    AliasChoices,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    model_validator,
)

from mascope_backend.api.new.peak_assignments.config import (
    MAX_IMPORT_ROWS_PER_REQUEST,
    PeakAssignmentConfig,
)
from mascope_backend.api.new.peak_assignments.tiers import normalize_tier


# Verification vocabulary (verification-calibration loop V1). Verdict is the label;
# evidence_level records why the user is confident -- the guardrail that lets the eventual
# calibration weight a reference-standard confirmation above a visual guess.
Verdict = Literal["confirmed", "rejected", "unsure"]
EvidenceLevel = Literal["reference_standard", "msms", "orthogonal", "pattern", "visual"]

# The assignment vocabulary, mirrored from the engine's constants. Typed rather
# than free strings so a misspelled filter is a 422 naming the accepted values,
# not a 200 with an empty ledger that reads as "this sample has no such peaks".
#
# The tier normalizes legacy spellings before that check (see
# `tiers.LEGACY_TIER_ALIASES`), and one Annotated type covers both directions on
# purpose: it types the imported row's tier AND the ledger's read filter, so a
# client that still says 'identified' - an external engine publishing to the
# older spec, or an SDK reader filtering by what its documentation taught it -
# is answered rather than 422'd, and stores or reads the current spelling.
AssignmentTier = Annotated[
    Literal["assigned", "candidate", "below_assignability", "unassigned"],
    BeforeValidator(normalize_tier),
]
AssignmentRole = Literal["M0", "iso_child", "reagent", "artifact", "unassigned"]
# 'manual' is a peer of the two stages rather than a flag beside them: it says
# which decision produced the row, and on a curated row that decision was a
# person's. It is in this shared literal - which types the ledger's read filter
# AND an imported row - so overrides are filterable in the ledger and survive a
# round trip through export/import (and, later, a copy between samples).
AssignmentSource = Literal["database", "untargeted", "manual"]

# A run holds one row per detected peak, so an unbounded read serializes tens
# of megabytes through Pydantic on the event loop. Clients page instead; the
# response carries `total` so they know when they have the run.
DEFAULT_PAGE_LIMIT = 1000
MAX_PAGE_LIMIT = 5000


class PeakAssignmentRunRecord(BaseModel):
    """One peak assignment run over a sample."""

    peak_assignment_run_id: str
    sample_item_id: str
    #: Which engine produced this run: the in-app engine, or an external one
    #: that published its ledger here. Never null - existing rows were
    #: backfilled to the in-app identity - so a reader can compare it without
    #: handling a sentinel. The run selector's provenance badge renders this.
    engine: str
    engine_version: str
    status: str
    config: dict | None = None
    #: The assigned/candidate fit-score thresholds this run tiered with.
    #: Served because 'assigned' means nothing comparable across engines
    #: until the bands that produced it are visible: the server validates every
    #: imported row against these, and a reader needs the same yardstick.
    #: Null only for runs predating the column.
    tier_bands: dict | None = None
    #: What the producing engine calibrated against, as it disclosed at import.
    #: An import bypasses the server-side m/z verification gate because it
    #: calibrates client-side, and this disclosure is what replaces that gate -
    #: which only works if a reader can actually see it. Null for in-app runs,
    #: whose calibration state is the sample's own.
    calibration: dict | None = None
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
    #: The evidence this row's tier was read off - ``fit_score`` weighted by the
    #: chemical plausibility of ``assigned_formula`` (provenance.evidence),
    #: flattened because the tier chip displays it beside the tier it produced.
    evidence: float | None = None
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


class AssignSampleRecord(BaseModel):
    """The run a sample assign request created."""

    sample_item_id: str
    #: The run this request created and handed to the engine. It exists, in a
    #: non-terminal state, before the response is sent - so a client polls one
    #: known run instead of diffing run sets to guess which one is its own.
    peak_assignment_run_id: str
    #: 'pending' until the background task adopts the run, then 'running'.
    run_status: str


class AssignSampleResponse(BaseModel):
    """Acknowledgement of a launched per-sample assignment run."""

    status: str = "success"
    message: str
    results: int
    data: list[AssignSampleRecord]


class SkippedSampleRecord(BaseModel):
    """A batch sample the engine cannot usefully assign, and why."""

    sample_item_id: str
    #: Short prose from the shared eligibility rule, e.g. "blank sample (no
    #: peaks)" or "m/z calibration not verified".
    reason: str


class AssignBatchRecord(BaseModel):
    """The eligibility partition a batch assign request will execute."""

    sample_batch_id: str
    #: Samples that will be assigned, in the order the batch visits them. No run
    #: ids: a batch creates each run only as it reaches that sample, so any id
    #: here would either be a run nothing is executing yet - which durable
    #: admission would then refuse - or one stranded by a batch that stopped
    #: early. Poll the admitted samples' runs instead.
    admitted: list[str]
    #: Samples that will not be assigned, each with its reason.
    skipped: list[SkippedSampleRecord]


class AssignBatchResponse(BaseModel):
    """Acknowledgement of a launched batch assignment."""

    status: str = "success"
    message: str
    results: int
    data: list[AssignBatchRecord]


class CopyDestinationRecord(BaseModel):
    """One batch sibling of a copy source, with its eligibility verdict."""

    sample_item_id: str
    sample_item_name: str
    #: Whether the fan-out will publish a copied run onto this sample.
    eligible: bool
    #: Why not, when it will not - e.g. "different polarity", "blank sample
    #: (no peaks)", "assignment run in flight". Null for eligible destinations.
    reason: str | None = None


class CopyAssignmentsPreviewRecord(BaseModel):
    """What a copy launched now would do: the source run and the partition.

    Computed by the same partition the launch executes, so the dialog that
    renders this lists exactly the destinations a confirm would copy to.
    """

    sample_item_id: str
    sample_batch_id: str
    #: The run whose current rows the copy would remap - the source's latest
    #: completed run. Null when the sample has none, in which case a launch is
    #: refused; the eligibility list is still served so the dialog can say why.
    source_peak_assignment_run_id: str | None = None
    source_engine: str | None = None
    destinations: list[CopyDestinationRecord]


class CopyAssignmentsPreviewResponse(BaseModel):
    """Eligibility preview for copying a sample's assignments to its batch."""

    status: str = "success"
    message: str
    results: int
    data: list[CopyAssignmentsPreviewRecord]


class CopyAssignmentsRecord(BaseModel):
    """The partition a copy launch will execute (mirrors AssignBatchRecord)."""

    sample_item_id: str
    sample_batch_id: str
    #: The source run being copied.
    source_peak_assignment_run_id: str
    #: Destinations that will receive a copied run, in fan-out order. No run
    #: ids: each destination's run is created only as the fan-out reaches it,
    #: exactly as a batch assign's runs are.
    admitted: list[str]
    #: Destinations that will not, each with its reason.
    skipped: list[SkippedSampleRecord]


class CopyAssignmentsResponse(BaseModel):
    """Acknowledgement of a launched copy fan-out."""

    status: str = "success"
    message: str
    results: int
    data: list[CopyAssignmentsRecord]


class TierBands(BaseModel):
    """The fit-score thresholds a run tiered its rows with.

    Lifted out of the opaque run `config` into a first-class field because the
    server validates every imported row's tier against it: the two engines share
    a fit-score *scale*, but the *bands* are run configuration, so 'assigned'
    means nothing comparable until the bands that produced it are on the record.

    The upper band is keyed 'assigned' and accepts the legacy 'identified' for
    the same reason the tier itself does: an engine built against the older spec
    declares its bands under the name that spec gave them. Only the current key
    is ever stored, so the bands a run is judged by read the same however they
    were declared.
    """

    model_config = ConfigDict(populate_by_name=True)

    assigned: float = Field(
        ge=0.0,
        le=1.0,
        validation_alias=AliasChoices("assigned", "identified"),
        description="Fit score at or above which a row is 'assigned'.",
    )
    candidate: float = Field(
        ge=0.0, le=1.0, description="Fit score at or above which a row is 'candidate'."
    )

    @model_validator(mode="after")
    def _ordered(self) -> "TierBands":
        if self.candidate > self.assigned:
            raise ValueError("tier_bands.candidate must not exceed tier_bands.assigned")
        return self


class ImportChunk(BaseModel):
    """Assembly control for one request of a multi-request import.

    ``index`` is an offset in rows, not a sequence number - the same shape the
    resumable file upload uses - so a client that lost a response resynchronises
    from the row count the server reports rather than guessing.

    ``index`` cannot make the *first* request idempotent, though: a create has
    no run id to be idempotent about, so a retry of it is a second run rather
    than a repeat of the first. ``import_id`` closes that gap, and is required
    for exactly that reason - see its description.
    """

    run_id: str | None = Field(
        None,
        description=(
            "The run being appended to. Omitted on the first request, which "
            "creates the run and returns its id."
        ),
    )
    import_id: str = Field(
        min_length=1,
        max_length=64,
        description=(
            "Client-chosen id for this logical import, unique per sample. "
            "**Required**: it is the only thing that can make the request that "
            "creates the run idempotent, since an HTTP retry of it is otherwise "
            "indistinguishable from a second import - and the SDK retries POSTs "
            "on timeouts with no way to opt out. Re-sending it returns the run "
            "already created for it instead of creating another. Any id unique "
            "to this import will do (a UUID is the obvious choice)."
        ),
    )
    index: int = Field(
        0,
        ge=0,
        description=(
            "Row offset this chunk starts at; must equal the rows the run "
            "already holds. Re-sending the last chunk is an idempotent no-op."
        ),
    )
    complete: bool = Field(
        False,
        description=(
            "Whether this is the final chunk. Finalizes the run: payload-wide "
            "validation, owner resolution, and the batch fold-in."
        ),
    )


class ImportAssignmentRow(BaseModel):
    """One imported per-peak assignment.

    Every column the read model serves, minus the fields the server owns:
    the ids it mints (``peak_assignment_id``, ``peak_assignment_run_id``,
    ``sample_item_id``), the owner link (supplied as ``owner_sample_peak_id``
    and resolved at finalize), and the flattened confidence scalars
    (``p_correct``, ``p_correct_provisional``, ``corroboration_adducts``), which
    are this server's calibrated judgement and stay empty on an imported row.
    Sending those is not an error - unknown fields are ignored - but they are
    not stored, including via the provenance keys they are read from.
    """

    model_config = ConfigDict(extra="ignore")

    # Every `max_length` below mirrors the width of the `peak_assignment`
    # column the value lands in. Not decoration: without them an over-long
    # string passes every payload rule and reaches the insert, where Postgres
    # raises a class-22 data exception that surfaces to the client as a 500
    # rather than the 422 every other payload rule produces.
    # `test_row_field_bounds_match_the_columns` fails if the two drift.
    #
    # `allow_inf_nan=False` on the floats closes the same class of hole from
    # the other side, and it has to be stated: `json.loads` accepts the `NaN`
    # and `Infinity` literals, and turns the RFC-valid `1e999` into `inf`, so
    # a non-finite value arrives through an ordinary request body. Postgres
    # stores both in a double precision column quite happily, and the damage
    # lands on the *read*: the ledger response renders with `allow_nan=False`,
    # so one such row makes `GET /sample/{id}` fail for the whole run - which,
    # being 'completed', the abandon endpoint will not release. The in-app
    # engine has the same rule as `_float_or_none` in engine.py; an import
    # states it here instead of silently nulling, because a client that sent a
    # number should be told it was rejected. `fit_score` needs no flag: its
    # `ge`/`le` already exclude both.
    sample_peak_id: str = Field(
        max_length=20,
        description="The observed peak this row assigns; must exist in the sample.",
    )
    sample_peak_mz: float = Field(allow_inf_nan=False)
    sample_peak_intensity: float = Field(allow_inf_nan=False)
    sample_peak_tof: float | None = Field(None, allow_inf_nan=False)
    role: AssignmentRole
    assigned_formula: str | None = Field(None, max_length=256)
    ion_formula: str | None = Field(None, max_length=4096)
    ionization_mechanism_id: str | None = Field(
        None,
        max_length=16,
        description=(
            "Optional: an external engine names adducts by notation, not by a "
            "deployment's mechanism ids. A supplied id must exist and match the "
            "sample's polarity."
        ),
    )
    isotope_label: str | None = Field(None, max_length=64)
    isotope_formula: str | None = Field(None, max_length=256)
    source: AssignmentSource | None = None
    fit_score: float | None = Field(None, ge=0.0, le=1.0)
    mz_error_ppm: float | None = Field(None, allow_inf_nan=False)
    abundance_error: float | None = Field(None, allow_inf_nan=False)
    tier: AssignmentTier
    target_compound_id: str | None = Field(
        None,
        max_length=16,
        description=(
            "Optional reference to the curated target library. A supplied id "
            "must exist on this deployment."
        ),
    )
    target_ion_id: str | None = Field(
        None,
        max_length=16,
        description=(
            "Optional reference to the curated target library. A supplied id "
            "must exist on this deployment."
        ),
    )
    owner_sample_peak_id: str | None = Field(
        None,
        max_length=20,
        description=(
            "For an iso_child: the sample_peak_id of its owner row within this "
            "import. Resolved to the minted owner assignment id at finalize."
        ),
    )
    alternatives: list | None = None
    provenance: dict | None = None


class ImportRunBody(BaseModel):
    """Request body for importing an externally computed assignment run."""

    engine: str = Field(
        max_length=64,
        description=(
            "The external engine that produced this run, stamped on the run as "
            "its provenance. The in-app identity is reserved. Name the engine, "
            "not the build - this is the key retention budgets each engine's "
            "runs under, so a value that varies per run fragments that budget."
        ),
    )
    engine_version: str = Field(
        max_length=64, description="The external engine's version string."
    )
    tier_bands: TierBands | None = Field(
        None,
        description=(
            "The thresholds this run tiered with. Required on the request that "
            "creates the run."
        ),
    )
    calibration: dict | None = Field(
        None,
        description=(
            "The engine's calibration state, plus the sample's server-side "
            "verification state at import time. Required on the request that "
            "creates the run: an import bypasses the m/z verification gate, so "
            "disclosure is what replaces it. An engine that calibrated nothing "
            "says so here."
        ),
    )
    config: dict | None = Field(
        None,
        description=(
            "The engine's run configuration. Opaque: stored verbatim, never "
            "read or written into by the server. Size-capped."
        ),
    )
    rows: list[ImportAssignmentRow] = Field(
        default_factory=list,
        max_length=MAX_IMPORT_ROWS_PER_REQUEST,
        description="This chunk's assignment rows.",
    )
    chunk: ImportChunk = Field(
        description=(
            "Assembly control. Required even for a single-request import: its "
            "`import_id` is what makes that one request safe to retry, which is "
            "the case a row offset cannot cover."
        ),
    )


class PeakAssignmentImportRecord(BaseModel):
    """The state of an import after one request."""

    peak_assignment_run_id: str
    #: 'importing' while the run is assembling, 'completed' once finalized.
    run_status: str
    #: Rows the run holds. The next chunk's ``index`` must equal this.
    rows: int
    #: Rows one request may carry, so a client sizes its chunks from the server
    #: instead of hardcoding a guess that a later release could invalidate.
    max_rows_per_request: int = MAX_IMPORT_ROWS_PER_REQUEST


class PeakAssignmentImportResponse(BaseModel):
    """Acknowledgement of one import request."""

    status: str = "success"
    message: str
    results: int
    data: list[PeakAssignmentImportRecord]


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
    #: Null on the current verdict for this identity; set on one a later verdict replaced.
    superseded_utc: datetime | None = None


class AssignmentVerificationsResponse(BaseModel):
    """Verifications recorded for a sample, newest first; superseded ones included."""

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


# ---------------------------------------------------------------------------
# Manual curation
# ---------------------------------------------------------------------------


class PromoteAlternativeBody(BaseModel):
    """Commit one of a row's stored runner-ups as its assignment.

    The candidate is named by its position in the row's ``alternatives`` list,
    because an alternative has no id of its own - the list is a JSON blob whose
    entries the three producers write in three different shapes. Position is
    only meaningful against the list the caller actually read, which is what
    ``expected_formula`` is for.
    """

    action: Literal["promote_alternative"]
    alternative_index: int = Field(
        ge=0,
        description=(
            "Index into the assignment's `alternatives` list, as served by the "
            "detail endpoint."
        ),
    )
    expected_formula: str | None = Field(
        None,
        max_length=256,
        description=(
            "The formula the caller believes sits at that index. Supplied, it "
            "is checked before anything is written and a mismatch is refused "
            "with 409 - so a second curator's override, landing between the "
            "read and this call, cannot silently turn a click on one candidate "
            "into a commitment to another."
        ),
    )


class SetAssignmentBody(BaseModel):
    """Commit a composition the caller supplies, for the re-search case.

    The scores are the ones this server's own composition search reported for
    the formula against this sample; they are recorded as the caller's
    declaration, are re-tiered here under the run's bands, and are labelled in
    provenance with where they came from. The calibrated fields (`p_correct`
    and friends) are deliberately absent: they are this server's judgement
    about its own engine's arbitration and are never taken from a client - the
    same rule the import path enforces with
    ``strip_server_owned_provenance``.
    """

    action: Literal["set_assignment"]
    assigned_formula: str = Field(
        min_length=1,
        max_length=256,
        description="Neutral formula to commit for this peak.",
    )
    ionization_mechanism_id: str = Field(
        min_length=1,
        max_length=16,
        description=(
            "The adduct the formula is assigned under. Required: a formula "
            "without its mechanism is half an assignment, and the mechanism is "
            "part of a verification's identity."
        ),
    )
    ion_formula: str | None = Field(None, max_length=4096)
    isotope_label: str | None = Field(
        None,
        max_length=64,
        description=(
            "Which isotopologue of the ion this peak is: 'M0' (or omitted) for "
            "the main one, otherwise 'M+1', 'M+2' ... A row labelled anything "
            "but M0 is committed as an `iso_child`, so a satellite is never "
            "recorded as a compound's main peak."
        ),
    )
    isotope_formula: str | None = Field(None, max_length=256)
    fit_score: float | None = Field(None, ge=0.0, le=1.0)
    mz_error_ppm: float | None = Field(None, allow_inf_nan=False)
    abundance_error: float | None = Field(None, allow_inf_nan=False)
    # No `plausibility` here on purpose: it is a pure function of the formula
    # (Seven Golden Rules), so the server computes it from what is committed
    # rather than accepting a number about chemistry from a caller.


#: The two curation actions, discriminated on `action` so an unknown one is a
#: 422 naming the accepted values rather than a silently ignored body.
CurateAssignmentBody = Annotated[
    PromoteAlternativeBody | SetAssignmentBody, Field(discriminator="action")
]


class AssignmentCurationResponse(BaseModel):
    """The rows a manual override rewrote.

    `data[0]` is the curated row. After it come the satellite rows the same
    edit moved, in two groups and always in this order:

    1. The isotopologue satellites the override **demoted**: the family of the
       formula it replaced, stripped to `unassigned` rather than left claiming
       a compound their M0 no longer carries. Empty when the edit commits the
       formula and mechanism the row already held, since then the family still
       stands for exactly what it stood for.
    2. The satellites it **restored**: the family of the compound now being
       committed, put back from the archive an earlier override of this row
       left behind. This is what makes promoting the displaced winner back a
       real undo rather than one that revives the M0 and leaves its satellites
       unassigned and ownerless.

    A demoted satellite that someone has curated by hand since is deliberately
    not restored, and so is not in `data` either - their judgement is newer
    than the undo. How many were left alone that way is in `message`.
    `message` counts a second group apart from those, and it means the
    opposite: satellites the undo could not put back at all - the row gone from
    this run, or the state archived for it unusable - which are missing from
    `data` not out of restraint towards a row somebody else now owns but
    because the restore did not reach them. The curated row's
    `provenance.manual` carries the ids behind both, under `restore_skipped`
    and `restore_failed`, beside the `restored` ids of the ones that did go
    back.

    Full detail records throughout, so a client can refresh every row the edit
    touched without a follow-up read.
    """

    status: str = "success"
    message: str
    results: int
    data: list[PeakAssignmentDetailRecord]
