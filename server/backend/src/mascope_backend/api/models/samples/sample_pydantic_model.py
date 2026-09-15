from datetime import datetime
from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from mascope_backend.api.models.base_pydantic_model import (
    CommonValidators,
    QueryParamsModel,
    RequestBodyModel,
)
from mascope_backend.api.models.sample.items.sample_item_pydantic_model import (
    GetSampleItemsQueryValidator,
)


# TODO_configuration move to sample configs when refactoring
DEFAULT_PEAK_MZ_TOLERANCE_PPM = 1.0


# Columns `sort` accepts (see mascope_backend.api.lib.sorting).
SAMPLE_SORT_COLUMNS = (
    "sample_item_id",
    "sample_file_id",
    "instrument_function_id",
    "sample_batch_id",
    "ionization_mode_id",
    "sample_item_name",
    "sample_item_type",
    "locked",
    "filter_id",
    "tic",
    "polarity",
    "t0",
    "t1",
    "sample_item_utc_created",
    "sample_item_utc_modified",
    "filename",
    "instrument",
    "instrument_type",
    "source_filename",
    "method_file",
    "length",
    "datetime",
    "datetime_utc",
)


class GetSamplesQueryParams(GetSampleItemsQueryValidator, QueryParamsModel):
    """
    This model defines the query parameters that can be passed to the GET /api/samples endpoint
    to control filtering, sorting, ordering, and pagination of sample results.
    """

    sample_item_id: str | None = Field(
        None, description="ID of the specific sample item to filter results by"
    )
    sample_file_id: str | None = Field(
        None, description="ID of the sample file to filter results by"
    )
    sample_batch_id: str | None = Field(
        None,
        description="ID of the sample batch to filter results by. Required for batch match info",
    )
    filename: str | None = Field(
        None, description="Filename of the sample to filter results by"
    )
    instrument: str | None = Field(
        None, description="Instrument name to filter results by"
    )
    sample_item_type: list[str] | None = Field(
        default=None,
        description="Filter by sample item types. Can specify multiple types.",
    )
    datetime_min: datetime | None = Field(
        None, description="Minimum datetime of the sample file to filter results by"
    )
    datetime_max: datetime | None = Field(
        None, description="Maximum datetime of the sample file to filter results by"
    )
    polarity: list[str] | None = Field(
        default=None,
        description="Filter by ion polarity modes (+, -). Can specify multiple polarities.",
    )
    match_category_min: int | None = Field(
        None,
        description="Filter samples by match_category to include samples with specified match category and higher",
    )
    sort: Literal[SAMPLE_SORT_COLUMNS] = Field(
        "datetime_utc",
        description="Column name by which you want to sort the results. Should be one of the Sample table columns (e.g., datetime_utc, filename, sample_item_type).",
    )
    order: str = Field(
        "asc",
        description="Sorting order which can be 'asc' for ascending or 'desc' for descending.",
    )


class GetSamplePeaksQueryParams(CommonValidators, QueryParamsModel):
    """
    Query parameters for retrieving peak data from a sample with optional time filtering.

    Time limits are optional - if not provided, the sample's acquisition time range (t0/t1) will be used.
    M/z range filtering requires both mz_min and mz_max to be provided together.
    Peak data is aggregated across the time dimension after applying all filters, including sample's polarity.
    It can include areas, heights, or both, with configurable aggregation method.
    """

    areas: bool = Field(
        True,
        description="Include peak areas in the response. Represents the integrated area under the curve for each peak, reflecting the total intensity over time.",
    )
    heights: bool = Field(
        True,
        description="Include peak heights in the response. Represents the maximum intensity at the apex of each peak, showing the peak's highest intensity value.",
    )
    average: bool = Field(
        True,
        description="If True, return averaged peak data across time dimension. If False, return summed peak data.",
    )
    t_min: float | None = Field(
        None,
        ge=0,
        description="Minimum time limit in seconds for filtering the peak data. If not provided, uses the sample's acquisition start time. Must be within the sample's acquisition time range",
    )
    t_max: float | None = Field(
        None,
        gt=0,
        description="Maximum time limit in seconds for filtering the peak data. If not provided, uses the sample's acquisition end time. Must be within the sample's acquisition time range",
    )
    mz_min: float | None = Field(
        None, ge=0, description="Start of the m/z range for spectrum filtering"
    )
    mz_max: float | None = Field(
        None, gt=0, description="End of the m/z range for spectrum filtering"
    )
    matches: bool = Field(
        False,
        description="Include match data in the response. If True, match data related to peaks will be included.",
    )

    @model_validator(mode="after")
    def validate_peak_variables(self):
        """
        Validates that at least one peak type (areas or heights) must be requested.
        """
        if not self.areas and not self.heights:
            raise ValueError(
                "You need to request either peak areas, peak heights, or both. At least one of 'areas' or 'heights' must be set to True."
            )
        return self

    @model_validator(mode="after")
    def validate_mz_range(self):
        """
        Validates that both mz_min and mz_max must be provided together.
        """
        mz_min = getattr(self, "mz_min", None)
        mz_max = getattr(self, "mz_max", None)

        if mz_min is not None and mz_max is not None and mz_max <= mz_min:
            raise ValueError("mz_max must be greater than mz_min")

        return self


class GetSamplePeakTimeseriesBody(CommonValidators, RequestBodyModel):
    """
    Request body for retrieving timeseries data of a specific peak in a sample.

    This model defines the parameters needed to extract and return timeseries data
    for a peak identified by either its ``peak_id`` or its ``m/z`` value.
    When ``peak_id`` is provided, the peak's m/z is resolved from the sample's peak data
    and ``peak_mz`` and ``peak_mz_tolerance_ppm`` are ignored.
    """

    peak_id: str | None = Field(
        None,
        description="The unique peak identifier. If provided, peak_mz and peak_mz_tolerance_ppm are ignored.",
    )
    peak_mz: float | None = Field(
        None,
        description="The m/z value of the peak to retrieve timeseries for. Required if peak_id is not provided.",
    )
    peak_mz_tolerance_ppm: float = Field(
        DEFAULT_PEAK_MZ_TOLERANCE_PPM,
        description="Tolerance for m/z difference between the requested peak and the nearest one found in data, specified in parts per million (ppm)",
    )
    t_min: float | None = Field(
        None,
        description="Minimum time limit in seconds for filtering the timeseries data. If not provided, uses the sample's acquisition start time. Must be within the sample's acquisition time range",
    )
    t_max: float | None = Field(
        None,
        description="Maximum time limit in seconds for filtering the timeseries data. If not provided, uses the sample's acquisition end time. Must be within the sample's acquisition time range",
    )

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="after")
    def validate_peak_identifier(self):
        if self.peak_id is None and self.peak_mz is None:
            raise ValueError("Either peak_id or peak_mz must be provided")
        return self


class GetSampleSpectrumQueryParams(CommonValidators, QueryParamsModel):
    """
    Query parameters for sample spectrum data with optional time filtering.

    Time limits are optional - if not provided, the sample's acquisition time range (t0/t1) will be used.
    M/z range filtering requires both mz_min and mz_max to be provided together.
    Inherits polarity and time range validation from CommonValidators.
    """

    t_min: float | None = Field(
        None,
        ge=0,
        description="Minimum time limit in seconds for filtering the spectrum data. If not provided, uses the sample's acquisition start time. Must be within the sample's acquisition time range",
    )
    t_max: float | None = Field(
        None,
        gt=0,
        description="Maximum time limit in seconds for filtering the spectrum data. If not provided, uses the sample's acquisition end time. Must be within the sample's acquisition time range",
    )
    mz_min: float | None = Field(
        None, ge=0, description="Start of the m/z range for spectrum filtering"
    )
    mz_max: float | None = Field(
        None, gt=0, description="End of the m/z range for spectrum filtering"
    )

    @model_validator(mode="after")
    def validate_mz_range(self):
        """
        Validates that both mz_min and mz_max must be provided together.
        """
        mz_min = getattr(self, "mz_min", None)
        mz_max = getattr(self, "mz_max", None)

        # Both must be provided together for m/z filtering
        if (mz_min is None) != (mz_max is None):  # XOR - exactly one is None
            raise ValueError("Both mz_min and mz_max must be provided together")

        if mz_min is not None and mz_max is not None and mz_max <= mz_min:
            raise ValueError("mz_max must be greater than mz_min")

        return self
