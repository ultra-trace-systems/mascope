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
    #: ``batch`` is the ledger derived from the batch peaks for a sample that
    #: has no run of its own - listed after the real runs, always completed,
    #: and what the ledger read falls back to (see ``fold_view``).
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
    #: The confidence calibration the run's P(correct) values were read off -
    #: instrument class, provisional flag, source - recorded once per run and
    #: folded back into each row's provenance by the assignment detail read.
    #: Null when the run was not calibrated, and for imported runs.
    confidence_calibration: dict | None = None
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
    #: The tier the producing engine itself concluded, when it said so. Absent
    #: on in-app rows, whose engine tier is ``tier``. Where the two differ, the
    #: engines disagree about how much confidence the evidence supports - which
    #: is what makes an imported run worth reading beside an in-app one.
    engine_tier: str | None = None
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
    #: The batch peak this row's peak is a member of: carried by a row derived
    #: from the batch ledger (``fold_view``), looked up on read for a run's own
    #: row; None when the peak is not in the ledger. What the sample ledger
    #: plots in the batch chart.
    batch_peak_id: str | None = None


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


class AlternativeScoreRecord(BaseModel):
    """One formula-only alternative, measured against the peak on demand.

    Either scored - `fit_score` and the adduct it was measured under are
    present - or blocked, where `blocked_reason` says in plain language why no
    adduct puts the formula on this peak. Never both.

    These numbers are session data, not run data: they are computed per request
    and never written onto the stored row, so a client that wants to commit one
    sends it back as a `set_assignment` (the composition-search action) rather
    than promoting a stored alternative.
    """

    #: Position of the entry in the row's stored `alternatives` list. Clients
    #: should match on `assigned_formula` instead where the list they render is
    #: filtered, since the index is only meaningful against the stored order.
    alternative_index: int
    assigned_formula: str
    #: Seven Golden Rules plausibility, a pure function of the formula.
    plausibility: float | None = None
    #: How many of the sample's adducts were tried, and how many placed the
    #: formula on this peak. `adducts_matched` is absent when none did.
    adducts_tried: int
    adducts_matched: int | None = None
    fit_score: float | None = None
    mz_error_ppm: float | None = None
    abundance_error: float | None = None
    #: `fit x plausibility`, the currency a tier is read off.
    evidence: float | None = None
    ionization_mechanism_id: str | None = None
    ionization_mechanism: str | None = None
    ion_formula: str | None = None
    #: Always "M0" when scored: the shortlist proposes a composition for this
    #: peak's own mass, so only the ion's monoisotopic peak was allowed to pair.
    isotope_label: str | None = None
    blocked_reason: str | None = None


class AlternativeScoresResponse(BaseModel):
    """A row's formula-only alternatives, measured against its peak."""

    status: str = "success"
    message: str
    results: int
    data: list[AlternativeScoreRecord]


class MeasuredIsotopologueRecord(BaseModel):
    """One predicted isotopologue of a measured composition, with the peak it
    paired to and the errors of that pairing (absent when it paired to none)."""

    isotope_label: str | None = None
    isotope_formula: str | None = None
    #: Theoretical m/z and relative abundance (a fraction of the M0).
    mz: float | None = None
    relative_abundance: float | None = None
    sample_peak_id: str | None = None
    mz_error_ppm: float | None = None
    abundance_error: float | None = None


class DerivedEvidenceRecord(BaseModel):
    """A derived row's family measured against its sample on demand: what a
    run would have stored beside the fit and the tier.

    Keyed by the family's M0 (``peak_assignment_id``); every member reads its
    own numbers off ``isotopologues`` by ``sample_peak_id``. Session data,
    computed per request and never written onto the ledger.
    """

    peak_assignment_id: str
    sample_peak_id: str
    assigned_formula: str | None = None
    ionization_mechanism_id: str | None = None
    ion_formula: str | None = None
    #: The stored fit, which the tier was read off; the measurement's own fit
    #: is reported beside it.
    fit_score: float | None = None
    measured_fit_score: float | None = None
    plausibility: float | None = None
    #: The stored fit times the plausibility, the currency the tier is banded on.
    evidence: float | None = None
    mz_error_ppm: float | None = None
    abundance_error: float | None = None
    isotopologues: list[MeasuredIsotopologueRecord] = []
    blocked_reason: str | None = None


class DerivedEvidenceResponse(BaseModel):
    """A derived row's on-demand measurement (one entry), or nothing for a
    run's own row."""

    status: str = "success"
    message: str
    results: int
    data: list[DerivedEvidenceRecord]


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
    engine_tier: AssignmentTier | None = Field(
        None,
        description=(
            "Filter by the tier the producing engine itself concluded. Only "
            "imported runs carry one; an in-app run's engine tier is its `tier`."
        ),
    )
    tier_disagrees: bool | None = Field(
        None,
        description=(
            "Keep only rows where the engine's own tier differs from this "
            "server's (`engine_tier` is set and not equal to `tier`), or with "
            "false only rows where they agree. Rows carrying no engine tier are "
            "excluded either way: absence is not agreement. This is the "
            "comparison an imported run exists for, so it is a filter rather "
            "than something to reconstruct client-side over a paged read."
        ),
    )
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
    tier: AssignmentTier | None = Field(
        None,
        description=(
            "This server's tier for the row. **Optional, and better omitted**: "
            "it is a pure function of `fit_score`, `assigned_formula` and the "
            "run's declared `tier_bands`, all of which the server already "
            "holds, so when it is absent the server derives it with the same "
            "call it would otherwise check it against. Sending it means "
            "reproducing this deployment's chemical-plausibility function "
            "exactly - a second implementation of one rule, which drifts, and "
            "whose drift refuses the whole import. A supplied value is still "
            "validated as before. To record a tier this engine reached its own "
            "way, use `engine_tier` rather than this field."
        ),
    )
    engine_tier: AssignmentTier | None = Field(
        None,
        description=(
            "Optional: the tier THIS ENGINE concluded, when that is a different "
            "judgement from `tier`. `tier` must agree with the evidence under "
            "the run's declared bands and is refused otherwise, so an engine "
            "that tiers by its own rules - arbitration, degeneracy, "
            "composition heuristics - has no way to record a demotion those "
            "rules produce. This field is that way: it is stored as supplied, "
            "checked only against the tier vocabulary, and read by no roll-up. "
            "Omit it where the engine stated no tier for the row; absence is "
            "not agreement."
        ),
    )
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


class VerifyBatchPeakBody(BaseModel):
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

    @model_validator(mode="after")
    def _guards(self) -> "VerifyBatchPeakBody":
        if self.verdict == "confirmed" and self.evidence_level is None:
            raise ValueError("evidence_level is required when verdict is 'confirmed'")
        if self.verdict in ("confirmed", "rejected") and not self.expected_formula:
            raise ValueError(
                "expected_formula is required when verdict is 'confirmed' or 'rejected'"
            )
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


class BatchPeakVerificationsResponse(BaseModel):
    """Batch-level verdicts, newest first; superseded ones included where listed."""

    status: str = "success"
    message: str
    results: int
    data: list[BatchPeakVerificationRecord]


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


class ReleaseBatchPeakCurationBody(BaseModel):
    """Request body to undo a batch peak's manual curation."""

    batch_peak_id: str = Field(description="The batch peak whose curation to release.")


class BatchImportRow(BaseModel):
    """One identity of an external engine's batch-level result: the m/z it was
    found at and the composition it was given. Matched to the batch peak nearest
    the m/z and measured against that peak's members by this server, so the
    engine's own scores are not part of the row - send them, and they are
    ignored; keep them client-side as the engine's provenance.
    """

    model_config = ConfigDict(extra="ignore")

    mz: float = Field(gt=0, allow_inf_nan=False)
    #: The neutral formula.
    formula: str = Field(min_length=1, max_length=256)
    ion_formula: str | None = Field(None, max_length=4096)
    #: The adduct, as this deployment's mechanism id. A row without one cannot
    #: be measured and is counted as skipped rather than refused.
    ionization_mechanism_id: str | None = Field(None, max_length=16)


class ImportBatchRunBody(BaseModel):
    """Request body for importing an external engine's batch-level result."""

    engine: str = Field(
        max_length=64,
        description=(
            "The external engine that produced the rows, stamped on the run and on "
            "every registry entry the import creates. The in-app identity is "
            "reserved."
        ),
    )
    engine_version: str = Field(min_length=1, max_length=64)
    config: dict | None = Field(
        None,
        description=(
            "The engine's own record of the run - its parameters, its summary - "
            "kept verbatim on the batch run for the run selector to show."
        ),
    )
    mz_tolerance_ppm: float = Field(
        5.0,
        ge=0.1,
        le=50.0,
        description="How far a row's m/z may sit from the batch peak it lands on.",
    )
    rows: list[BatchImportRow] = Field(min_length=1, max_length=5000)


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


class BatchPeakRunRecord(BaseModel):
    """One batch run: a batch-level operation that rewrote the batch ledger."""

    batch_peak_run_id: str
    sample_batch_id: str
    #: fold | rebuild | search_untargeted | import
    action: str
    engine: str
    engine_version: str
    #: running | completed | failed
    status: str
    #: The run whose state the live ledger holds; exactly one per batch.
    current: bool
    is_current: bool
    config: dict | None = None
    summary: dict | None = None
    error: str | None = None
    created_by: int | None = None
    batch_peak_run_utc_created: datetime | None = None
    batch_peak_run_utc_completed: datetime | None = None
    #: When this run's ledger state was captured - set as the next run started.
    snapshot_utc: datetime | None = None


class BatchPeakRunsResponse(BaseModel):
    """A batch's runs, newest first."""

    status: str = "success"
    message: str
    results: int
    data: list[BatchPeakRunRecord]


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
