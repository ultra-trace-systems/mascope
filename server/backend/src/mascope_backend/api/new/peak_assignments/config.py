"""Configuration for the peak-centric assignment engine."""

import os

from pydantic import BaseModel, Field, field_validator

from mascope_backend.api.new.cheminfo.config import cheminfo_config
from mascope_backend.runtime import runtime


# Bump when the assignment algorithm changes in a way that affects results.
# Stored on every PeakAssignmentRun so runs stay reproducible and comparable.
PEAK_ASSIGNMENT_ENGINE_VERSION = "0.2.0"

# The in-app engine's identity, stamped on every run this server computes. It is
# reserved: an import that could stamp it would defeat the provenance badge that
# the whole import trust model leans on, and it is the value the instrument
# recalibration pool filters on.
IN_APP_ENGINE = "mascope"

# Engine names a client may not claim. Matched case-insensitively on the
# stripped value: 'Mascope' is not a different engine, it is the same forgery
# with different capitalization.
RESERVED_ENGINE_NAMES = frozenset({IN_APP_ENGINE})

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


class PeakAssignmentConfig(BaseModel):
    """User-tunable configuration for one peak assignment run.

    The full (resolved) configuration is persisted on the PeakAssignmentRun
    row, together with the engine version.
    """

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

    # Confidence-tier bands on the FIT-SCORE scale (score_pattern_v2), not the legacy
    # match_params scale. The fit score demotes lone mass-only matches by design, so its
    # bands sit lower than v1's 0.8/0.7; these are the DESIGN.md v2 estimates
    # (identified >= 0.8, candidate >= 0.5), pending per-instrument recalibration.
    identified_threshold: float = Field(
        0.8,
        ge=0.0,
        le=1.0,
        description="Fit score at or above which a peak is tiered 'identified'.",
    )
    candidate_threshold: float = Field(
        0.5,
        ge=0.0,
        le=1.0,
        description="Fit score at or above which a peak is tiered 'candidate'.",
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
