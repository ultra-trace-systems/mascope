"""Running the engine on a sample: previews, assign, fit views, recalibration."""

from pydantic import BaseModel, Field, field_validator

from mascope_backend.api.new.peak_assignments.config import (
    DEFAULT_PROFILE,
    PeakAssignmentConfig,
    known_preset_name,
)


class ProfilePreviewQueryParams(BaseModel):
    """The two chemistry names a preview resolves, as a run config names them."""

    profile: str = Field(
        DEFAULT_PROFILE,
        description=(
            "Reagent profile to resolve: 'auto' reads it off each sample's "
            "ionization mechanisms, or a preset name."
        ),
    )
    context: str = Field(
        DEFAULT_PROFILE,
        description=(
            "Chemistry context to resolve: 'auto' takes the one the samples' "
            "batch or dataset names, else each profile's own; or a context name."
        ),
    )

    @field_validator("profile", "context")
    @classmethod
    def _known_name(cls, value: str, info) -> str:
        """Refuse a name the run config would refuse, with its message."""
        return known_preset_name(value, info.field_name)


class ProfilePreviewRecord(BaseModel):
    """The chemistry a run would search some of the requested samples under.

    Samples that resolve alike are one record. A run records the same names on
    itself (``config.resolved_profile``) once it has started; this is the answer
    before it starts, from the samples' ionization modes alone.
    """

    profile: str
    profile_label: str
    #: The profile's own polarity; ``""`` for the identity profile.
    profile_polarity: str
    requested_profile: str
    context: str
    context_label: str
    requested_context: str
    #: Where the context came from: ``config`` (the request named it),
    #: ``batch`` or ``dataset`` (``auto`` took the one the samples' batch or
    #: its dataset names), or ``profile`` (the reagent profile's default).
    context_source: str = "profile"
    #: The neutral grid the profile and context give the untargeted stage.
    element_ranges: str
    #: The untargeted stage's m/z window, in ppm, for the samples' instrument
    #: class (or as the run config sets it).
    mz_precision_ppm: float
    #: The polarity of the samples this record counts.
    polarity: str | None = None
    samples: int


class ProfilePreviewResponse(BaseModel):
    """What a run config's profile and context resolve to, per distinct answer."""

    status: str = "success"
    message: str
    results: int
    data: list[ProfilePreviewRecord]


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
    #: The ``{evidence level: weight}`` map the labels were weighed by in the
    #: fit and in both held-out ECEs; None when nothing was fit.
    label_weights: dict[str, float] | None = None
    #: The reagent profile every label was scored under: the one asked for, or
    #: the one all the labels share; None where they span several. Recorded on
    #: the curve, and the only case its corroboration weights are refit in.
    reagent_profile: str | None = None
    #: How many of the labels judged an untargeted (Stage B) row.
    n_untargeted: int = 0
    #: Why a fitted curve was not put in force: ``ece_regression`` when its
    #: held-out ECE is worse than the active curve's on the same labels.
    refusal: str | None = None
    #: The adducts whose corroboration weight the profile's labels refit, each
    #: with its new weight and how many confirmed (``n_pos``) and rejected
    #: (``n_neg``) labels saw it as another channel; None when none were.
    corroboration_refit: dict[str, dict[str, int | float]] | None = None
    #: The corroboration weights the new curve carries, refit and carried forward.
    corroboration_weights: dict[str, float] | None = None
