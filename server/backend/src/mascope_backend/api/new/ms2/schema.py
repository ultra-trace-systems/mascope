from typing import Literal

from pydantic import Field

from mascope_backend.api.models.base_pydantic_model import QueryParamsModel


DEFAULT_PARENT_PEAK_TOLERANCE = 0.001
DEFAULT_NOISE_THRESHOLD = 10.0
DEFAULT_TIMEOUT = 120.0  # seconds


class GetMs2SummaryQueryParams(QueryParamsModel):
    """Query parameters for retrieving MS2 summary data."""

    parent_peak_tolerance: float = Field(
        DEFAULT_PARENT_PEAK_TOLERANCE,
        description="Tolerance in Da for merging near-duplicate parent peaks",
    )
    timeout: float = Field(
        DEFAULT_TIMEOUT,
        description="Maximum seconds to wait for the computation before returning 504",
        gt=0,
        le=600,
    )


class GetMs1CentroidsQueryParams(QueryParamsModel):
    """Query parameters for retrieving averaged MS1 centroids."""

    ppm: int = Field(1, description="Mass tolerance in ppm for centroid binning")
    timeout: float = Field(
        DEFAULT_TIMEOUT,
        description="Maximum seconds to wait for the computation before returning 504",
        gt=0,
        le=600,
    )


class GetMs2CentroidsQueryParams(QueryParamsModel):
    """Query parameters for retrieving averaged MS2 centroids."""

    noise_threshold: float = Field(
        DEFAULT_NOISE_THRESHOLD,
        description="Minimum signal-to-noise ratio threshold",
    )
    parent_peak_tolerance: float = Field(
        DEFAULT_PARENT_PEAK_TOLERANCE,
        description="Tolerance in Da for merging near-duplicate parent peaks",
    )
    timeout: float = Field(
        DEFAULT_TIMEOUT,
        description="Maximum seconds to wait for the computation before returning 504",
        gt=0,
        le=600,
    )


class GetMs2TimeseriesQueryParams(QueryParamsModel):
    """Query parameters for retrieving fragment timeseries for a parent peak."""

    parent_peak_mz: float = Field(
        ..., description="Parent peak m/z to get fragment timeseries for"
    )
    noise_threshold: float = Field(
        DEFAULT_NOISE_THRESHOLD,
        description="Minimum signal-to-noise ratio threshold",
    )
    parent_peak_tolerance: float = Field(
        DEFAULT_PARENT_PEAK_TOLERANCE,
        description="Tolerance in Da for matching parent peaks",
    )
    normalize_by: Literal["tic"] | None = Field(
        None, description="Normalization mode: 'tic' or None"
    )
    activation: str | None = Field(
        None,
        description=(
            "Restrict to one activation, e.g. 'hcd40.00'. Defaults to every "
            "activation of the parent peak, so a stepped-energy acquisition "
            "shows its fragments changing across the steps"
        ),
    )
    timeout: float = Field(
        DEFAULT_TIMEOUT,
        description="Maximum seconds to wait for the computation before returning 504",
        gt=0,
        le=600,
    )
