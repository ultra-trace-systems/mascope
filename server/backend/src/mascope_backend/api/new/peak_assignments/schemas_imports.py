"""Import payloads: an external engine's run over a sample or a batch."""

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from mascope_backend.api.new.peak_assignments.config import MAX_IMPORT_ROWS_PER_REQUEST
from mascope_backend.api.new.peak_assignments.schemas_vocabulary import (
    AssignmentRole,
    AssignmentSource,
    AssignmentTier,
    EngineLevel,
)


class TierBands(BaseModel):
    """The evidence thresholds a run tiered its rows with.

    Evidence is fit x chemical plausibility (`tier_for_evidence`), the number a
    tier is read off; the fit itself is stored beside it as the pure
    measurement. Lifted out of the opaque run `config` into a first-class field
    because the server validates every imported row's tier against it: the two
    engines share the evidence *scale*, but the *bands* are run configuration,
    so 'assigned' means nothing comparable until the bands that produced it are
    on the record.

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
        description=(
            "Evidence (fit x plausibility) at or above which a row is 'assigned'."
        ),
    )
    candidate: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Evidence (fit x plausibility) at or above which a row is 'candidate'."
        ),
    )

    @model_validator(mode="after")
    def _ordered(self) -> "TierBands":
        if self.candidate > self.assigned:
            raise ValueError("tier_bands.candidate must not exceed tier_bands.assigned")
        return self


class ScoringWidth(BaseModel):
    """The width an imported run's engine judged a mass error against.

    The same four numbers the runs listing reads off an in-app run's
    ``measurement_model`` record, so a reader compares two runs' widths
    without knowing which engine made either. Disclosed beside
    ``calibration``, and like it read only from the request that creates the
    run.
    """

    sigma_ppm: float = Field(
        gt=0.0,
        le=1e6,
        description="The mass-error width the engine scored at [ppm].",
    )
    sigma_source: str = Field(
        min_length=1,
        max_length=64,
        description=(
            "Where the width came from, in the engine's own words: the "
            "in-app engine says `fitted` or `instrument_class`."
        ),
    )
    mu_ppm: float | None = Field(
        None,
        ge=-1e6,
        le=1e6,
        description="The mass-error centre the engine corrected by [ppm].",
    )
    mu_source: str | None = Field(
        None,
        max_length=64,
        description="Where the centre came from, in the engine's own words.",
    )


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
    engine_level: EngineLevel | None = Field(
        None,
        description=(
            "Optional: the identification level THIS ENGINE stated for the "
            "row, in the scale both engines share (`5a`, `5b`, `4a`, `4b`, "
            "`3a` to `3d`, `2a`, `2b`); anything else is refused, `1` "
            "included: level 1 is a person's claim in review. Stored as "
            "supplied beside `engine_tier` and read by no roll-up; the row's "
            "own `level` stays this server's and an import never sets it."
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
    scoring_width: ScoringWidth | None = Field(
        None,
        description=(
            "Optional: the mass-error width the engine scored at, served on "
            "the run as `sigma_ppm`, `sigma_source`, `mu_ppm` and `mu_source`. "
            "Read from the request that creates the run. Omitted, the run "
            "answers null for all four."
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


class BatchImportRow(BaseModel):
    """One identity of an external engine's batch-level result: the m/z it was
    found at and the composition it was given. Matched to the batch peak nearest
    the m/z and measured against that peak's members by this server, so the
    engine's own scores are not part of the row - send them, and they are
    ignored; keep them client-side as the engine's provenance. The engine's
    verdict is: ``engine_tier`` and ``engine_claim`` are kept on the batch peak
    beside the tier this server derives, never in place of it.
    """

    model_config = ConfigDict(extra="ignore")

    mz: float = Field(gt=0, allow_inf_nan=False)
    #: The neutral formula.
    formula: str = Field(min_length=1, max_length=256)
    ion_formula: str | None = Field(None, max_length=4096)
    #: The adduct, as this deployment's mechanism id. A row without one cannot
    #: be measured and is counted as skipped rather than refused.
    ionization_mechanism_id: str | None = Field(None, max_length=16)
    engine_tier: AssignmentTier | None = Field(
        None,
        description=(
            "Optional: the tier THIS ENGINE concluded for the identity, in the "
            "sample import's vocabulary. Stored on the batch peak the row lands "
            "on, beside the consensus tier this server measures from the "
            "members, and read by no roll-up and no fold. Omit it where the "
            "engine stated no tier; absence is not agreement."
        ),
    )
    engine_claim: str | None = Field(
        None,
        max_length=1024,
        description=(
            "Optional: the engine's own words about the identity - its "
            "evidence level, the rule that decided it - kept verbatim beside "
            "`engine_tier` for a reader. Free text; nothing reads it."
        ),
    )
    engine_level: EngineLevel | None = Field(
        None,
        description=(
            "Optional: the identification level THIS ENGINE stated for the "
            "identity, in the scale both engines share: Schymanski's numbers "
            "with a letter for the kind of evidence (`5a`, `5b`, `4a`, `4b`, "
            "`3a` to `3d`, `2a`, `2b`); anything else is refused, `1` "
            "included: level 1 is a confirmed structure, a person's claim in "
            "review, which no engine states. Stored "
            "on the batch peak the row lands on beside `engine_tier` and "
            "`engine_claim`, so the reference's level reads beside the levels "
            "Mascope's runs stated rather than translated. Read by no roll-up "
            "and no fold."
        ),
    )
    engine_evidence: str | None = Field(
        None,
        max_length=1024,
        description=(
            "Optional: the engine's one line naming what earned `engine_level`, "
            "kept and shown as the engine wrote it."
        ),
    )
    engine_lift: str | None = Field(
        None,
        max_length=1024,
        description=(
            "Optional: what the engine says would raise `engine_level`, kept and "
            "shown as the engine wrote it."
        ),
    )


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
