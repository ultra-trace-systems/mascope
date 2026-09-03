"""Configuration for the peak-centric assignment engine."""

import os

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from mascope_backend.api.new.cheminfo.config import cheminfo_config
from mascope_backend.runtime import runtime


# Bump when the assignment algorithm changes in a way that affects results.
# Stored on every PeakAssignmentRun so runs stay reproducible and comparable.
PEAK_ASSIGNMENT_ENGINE_VERSION = "0.3.0"

# The in-app engine's identity, stamped on every run this server computes. It is
# reserved: an import that could stamp it would defeat the provenance badge that
# the whole import trust model leans on, and it is the value the instrument
# recalibration pool filters on.
IN_APP_ENGINE = "mascope"

# The identity of runs the server-side copy service publishes (assignments
# copied from a curated sample onto its batch's other samples, re-scored per
# destination - docs/dev/peak_assignment_copy.md). Reserved for the same reason
# the in-app name is: the UI presents this value as a first-party copy, so an
# external import that could stamp it would forge that presentation. The copy
# service itself passes the reservation through a trusted server-side parameter
# on the import entry point that the HTTP route never forwards.
COPY_ENGINE = "mascope-copy"

# Bump when the copy pipeline changes in a way that affects results (mapping
# tolerance, re-scoring, drop rules). Stamped as engine_version on copied runs.
COPY_ENGINE_VERSION = "0.1.0"

# The identity of the ledger derived from the batch peaks for a sample that has
# no run of its own (fold_view.py). The runs listing presents it as a completed
# run and the UI badges it as first-party, so an import that could claim the
# name would forge that presentation. Reserved like the two above.
FOLD_ENGINE = "batch"

# Engine names a client may not claim. Matched case-insensitively on the
# stripped value: 'Mascope' is not a different engine, it is the same forgery
# with different capitalization.
RESERVED_ENGINE_NAMES = frozenset({IN_APP_ENGINE, COPY_ENGINE, FOLD_ENGINE})

# Rows one import request may carry, mirroring the ledger read's page size for
# the same reason: a dense sample's full ledger with alternatives and provenance
# is tens of thousands of rows at a few KB each, and an unbounded list
# serializes unbounded work through Pydantic on the event loop. An import
# assembles across requests instead. Served to clients on the create response so
# a chunker sizes itself from the server rather than guessing.
MAX_IMPORT_ROWS_PER_REQUEST = 1000

# Byte ceiling on one import request body, ~5 KB per row at the row cap above.
# Rows alone do not bound a body - a row's `alternatives` and `provenance` are
# client JSON of no fixed size - so the two caps are enforced together.
#
# This must stay at or below the `client_max_body_size` that
# both server/frontend/nginx.conf and nginx.http.conf set on the peak-assignment
# location (they are baked into one image and selected at container start, so a
# location in only one silently does not apply to the other deployment mode -
# tooling/validate-nginx-config.sh guards the drift): nginx rejects
# a larger body itself, before the request reaches this process, and its default
# is 1 MB. Raising one without the other either makes this check unreachable or
# turns a documented 413 into an nginx error page.
MAX_IMPORT_BODY_BYTES = 5 * 1024 * 1024

# Byte ceiling on a run's `config` and `calibration` blobs. Both are opaque
# client JSON, so nothing bounds them the way the closed PeakAssignmentConfig
# model bounds an in-app run's, and both are re-served in full by
# `GET /sample/{id}/runs` - a hot path the SDK calls on every ledger read and
# the run selector polls. Generous next to any real engine config; the point is
# that a bound exists.
MAX_IMPORT_JSON_BYTES = 64 * 1024

# Ceilings on the user-supplied run config. The untargeted stage is the
# documented scaling risk: cost grows with the number of peaks fed to it, with
# the mass window (more candidates per peak), and exponentially with the number
# of element species. These bounds keep one API call from scheduling unbounded
# work; they are deliberately generous, well above any sane analysis.
MAX_UNTARGETED_PEAKS_CEILING = 5000
MAX_MZ_PRECISION_PPM = 100.0
MAX_FORMULA_RANGE_SPECIES = 12
MAX_ALTERNATIVES_CEILING = 50


class PeakAssignmentLimits(BaseModel):
    """The run-config bounds, published so a client can enforce the same ones.

    The config form needs min/max for its inputs, and hardcoding them in the
    frontend is how a form drifts from the validation behind it: the input would
    happily accept a value the API then rejects. Serving them from the same
    constants ``PeakAssignmentConfig`` validates against keeps the two honest.
    """

    max_untargeted_peaks_ceiling: int = MAX_UNTARGETED_PEAKS_CEILING
    max_mz_precision_ppm: float = MAX_MZ_PRECISION_PPM
    max_formula_range_species: int = MAX_FORMULA_RANGE_SPECIES
    max_alternatives_ceiling: int = MAX_ALTERNATIVES_CEILING


def peak_assignment_enabled() -> bool:
    """Whether the peak-centric assignment feature is switched on for this env.

    On by default: the feature is generally available. Peak-centric assignment
    coexists with the targeted workflow rather than replacing it, so targeted
    matching behaves the same either way; what this switch decides is whether
    the parts that act on their own are active - assignment on sample ingest,
    the rescored composition search, and the reworked Sample view. A deployment
    that wants the pre-assignment behaviour sets the flag to false, which is
    what an operator reaches for when ingest-time assignment is unwanted.

    Resolution order:
    - ``MASCOPE_PEAK_ASSIGNMENT`` env var (``1``/``true``/``yes``/``on``), which
      lets an operator flip it without editing the env toml;
    - the ``peak_assignment`` flag in the runtime ``[meta]`` config, which is the
      durable setting and is also what the frontend reads.

    It gates the automatic behaviour *and* the API writes. With the flag off,
    the ``/api/peak-assignments`` read endpoints stay open - ledgers written
    while the feature was on remain inspectable after opting out - but the
    write endpoints (launching runs, recording verdicts, refitting the
    calibration, importing an externally computed run) return 403 via
    ``require_peak_assignment_enabled`` in ``routes.py``. That is what makes
    opting out hold for the API as well as for the UI and sample ingest: an
    opted-out deployment cannot accumulate per-peak ledgers, deliberately or
    otherwise. Tests pin the flag through the env override in both directions
    rather than relying on the default. Ledger rows from
    opted-in periods are reclaimed by the ``prune_peak_assignment_runs``
    maintenance script, which a host provisioned with ``tooling/ubuntu.sh``
    runs nightly on a timer; a deployment set up another way schedules it
    itself - see ``docs/maintaining.md``.

    :return: True when the feature is enabled for this environment.
    """
    override = os.environ.get("MASCOPE_PEAK_ASSIGNMENT")
    if override is not None:
        return override.strip().lower() in {"1", "true", "yes", "on"}
    # The fallback matches MetaConfig's own default, so a runtime whose config
    # model predates the field reads the same way as one that carries it.
    return bool(getattr(runtime.meta, "peak_assignment", True))


# The ingest ceiling's default, matching MetaConfig's, for the same reason the
# flag's fallback matches its own. Generous on purpose: it is there to catch a
# pathological acquisition, not a dense instrument.
DEFAULT_INGEST_MAX_PEAKS = 100_000


def peak_assignment_on_ingest() -> bool:
    """Whether a newly processed sample is assigned as it arrives.

    Subordinate to :func:`peak_assignment_enabled`: with the feature off nothing
    assigns at ingest whatever this says. It exists for deployments that want
    the feature - the views, the on-demand runs, imports - without the
    per-sample cost of assigning everything they acquire. An ingest-time run is
    the database stage only, and it writes one ledger row and one batch-peak
    occurrence per detected peak (about 1 KB per peak in all) that the
    retention pass never reclaims; on a high-throughput instrument that is tens
    of gigabytes a month of rows nobody asked for. Off, a sample is assigned
    when someone launches a run on it or on its batch.

    Read from ``peak_assignment_on_ingest`` in the runtime ``[meta]`` config.

    :return: True when ingest assigns automatically (the default).
    """
    return bool(getattr(runtime.meta, "peak_assignment_on_ingest", True))


def peak_assignment_ingest_max_peaks() -> int:
    """Ceiling on the detected-peak count of a sample assigned at ingest.

    A sample above it is logged and left for an explicit run rather than
    assigned as it arrives: at about 1 KB per detected peak, one very dense
    acquisition - a few hundred thousand peaks happens - is hundreds of
    megabytes of ledger written unasked, for a spectrum the untargeted stage
    could not be run over anyway. ``0`` disables the ceiling.

    Read from ``peak_assignment_ingest_max_peaks`` in the runtime ``[meta]``
    config.

    :return: The ceiling, or 0 for none.
    """
    return int(
        getattr(
            runtime.meta, "peak_assignment_ingest_max_peaks", DEFAULT_INGEST_MAX_PEAKS
        )
    )


#: Values of ``peak_assignment_ingest_ledger``: fold the sample into the batch
#: ledger and write no run (the default), or write a per-sample run at ingest
#: and fold it (the behaviour before the batch ledger became the durable object).
INGEST_LEDGER_SAMPLE = "sample"
INGEST_LEDGER_BATCH = "batch"


def peak_assignment_ingest_ledger() -> str:
    """Which ledger an ingest-time assignment writes.

    ``"batch"`` (the default) folds the sample into the batch peaks and writes no
    run: the members carry what the Sample view needs (``fold_view``), and the
    per-sample rows - about a kilobyte per detected peak, most of it
    placeholders for peaks nothing assigned - are never written. ``"sample"``
    writes a per-sample run as well and folds it, as an explicit run does; an
    explicit run on a sample writes one whatever this says.

    Read from ``peak_assignment_ingest_ledger`` in the runtime ``[meta]`` config;
    anything but the two values reads as the default.

    :return: :data:`INGEST_LEDGER_BATCH` or :data:`INGEST_LEDGER_SAMPLE`.
    """
    value = getattr(runtime.meta, "peak_assignment_ingest_ledger", INGEST_LEDGER_BATCH)
    if str(value).strip().lower() == INGEST_LEDGER_SAMPLE:
        return INGEST_LEDGER_SAMPLE
    return INGEST_LEDGER_BATCH


class PeakAssignmentConfig(BaseModel):
    """User-tunable configuration for one peak assignment run.

    The full (resolved) configuration is persisted on the PeakAssignmentRun
    row, together with the engine version.
    """

    model_config = ConfigDict(populate_by_name=True)

    run_untargeted: bool = Field(
        True,
        description=(
            "Run Stage B (untargeted composition search) for peaks that the "
            "database stage left unassigned."
        ),
    )
    mz_precision_ppm: float = Field(
        cheminfo_config.DEFAULT_MZ_PRECISION,
        gt=0.0,
        le=MAX_MZ_PRECISION_PPM,
        description="m/z tolerance in ppm for the untargeted composition search.",
    )
    formula_ranges: str = Field(
        cheminfo_config.DEFAULT_FORMULA_RANGE,
        description=(
            "Element count ranges permitted in untargeted candidates, e.g. "
            "'C0-100 H0-100 O0-100 N0-100'. Enumeration is a tree search whose "
            "depth is the number of element species, so the species count is "
            "capped."
        ),
    )
    max_untargeted_peaks: int = Field(
        300,
        gt=0,
        le=MAX_UNTARGETED_PEAKS_CEILING,
        description=(
            "Upper bound on the number of (most intense) unassigned peaks fed "
            "to the untargeted stage. Composition enumeration is the scaling "
            "risk; this bounds run time on dense spectra."
        ),
    )
    peak_intensity_threshold: float = Field(
        0.0,
        ge=0.0,
        description=(
            "Minimum peak intensity for a peak to enter the untargeted stage."
        ),
    )
    max_alternatives: int = Field(
        5,
        ge=0,
        le=MAX_ALTERNATIVES_CEILING,
        description=(
            "Maximum number of runner-up candidates stored per peak. Sizes a "
            "JSON column on the highest-volume table, so it is capped."
        ),
    )

    # Confidence-tier bands on the EVIDENCE scale -- fit x chemical plausibility, the
    # same product both stages arbitrate a contested peak in (see
    # `engine.tier_for_evidence`). They were on the bare fit scale at 0.8/0.5; the keys
    # are unchanged and only their meaning moved, because a run records the pair it
    # tiered with (`tier_bands`) and nothing else ever needed a second name for it.
    #
    # Why 0.75/0.45 and not the old 0.8/0.5: plausibility is <= 1, so multiplying it in
    # can only move a row down, and the bands have to come down with it or the run
    # silently tiers stricter than it used to. How far was settled by sweeping the pair
    # over a real ledger - all 161 demo samples assigned, 77,911 tiered rows - rather
    # than by taste. Plausibility turns out not to be a broad downshift but a spike at
    # 1.0 with a thin tail: 92.8% of tiered rows score exactly 1.0 (Stage A 98.3%, Stage
    # B 88.4%), so only 7.2% of rows move at all, and the bands need far less taken off
    # them than "evidence <= fit" suggests. 0.75/0.45 was the closest pair in the sweep
    # to the fit-tiered split it replaces: assigned 85.4% against 84.1% today, with the
    # 6.9% of rows that change tier moving in both directions (2,717 up, 1,710 down)
    # rather than draining one band.
    #
    # These are DIRECTIONAL, not calibrated truth - a defensible starting point that
    # keeps the tier histogram close to what it was while letting the chemistry demote
    # the implausible. Per-instrument recalibration against verification labels is the
    # documented follow-up, and P(correct) is the eventual binding once calibration
    # coverage allows it (docs/dev/assignment_confidence.md).
    #
    # One pair, both stages, knowingly: Stage A's fit is ion_score_v2 and Stage B's is
    # score_pattern (v1), so a band means slightly different things to each - on the
    # sweep, holding the upper band at 0.80 costs Stage B 5.3% of its assigned rows and
    # Stage A only 0.5%. Per-stage bands would fit the data better and are deliberately
    # not introduced: the heterogeneity predates this binding (it was there under
    # fit-tiering too), and a second pair of knobs is more apparatus than a directional
    # threshold is worth.
    #
    # The upper band still parses under its old name: this model is built from the
    # assign request body, so a client pinned to the pre-rename field would
    # otherwise silently drop back to the default instead of tiering the run the
    # way it asked. Only the current name is stored - model_dump() lands verbatim
    # in the run's config.
    assigned_threshold: float = Field(
        0.75,
        ge=0.0,
        le=1.0,
        validation_alias=AliasChoices("assigned_threshold", "identified_threshold"),
        description=(
            "Evidence (fit x plausibility) at or above which a peak is tiered "
            "'assigned'."
        ),
    )
    candidate_threshold: float = Field(
        0.45,
        ge=0.0,
        le=1.0,
        description=(
            "Evidence (fit x plausibility) at or above which a peak is tiered "
            "'candidate'."
        ),
    )

    @field_validator("formula_ranges")
    @classmethod
    def _bound_formula_ranges(cls, value: str) -> str:
        """Cap the number of element species the untargeted search enumerates.

        ``find_compositions`` is a depth-first search whose depth is the number
        of element species, so widening the range string is the cheapest way to
        make a run combinatorially expensive. Parsing the ranges belongs to
        ``mascope_tools``; this only bounds the species count.
        """
        species = [token for token in value.split() if token]
        if len(species) > MAX_FORMULA_RANGE_SPECIES:
            raise ValueError(
                f"formula_ranges lists {len(species)} element species; at most "
                f"{MAX_FORMULA_RANGE_SPECIES} are allowed because untargeted "
                "enumeration is exponential in the number of species."
            )
        return value
