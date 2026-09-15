from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from mascope_backend.api.models.base_pydantic_model import QueryParamsModel


# Columns `sort` accepts (see mascope_backend.api.lib.sorting).
INSTRUMENT_CONFIG_SORT_COLUMNS = (
    "instrument_function_id",
    "instrument",
    "method_file",
    "datetime_utc",
)


class GetInstrumentConfigsQueryParams(QueryParamsModel):
    filename: str | None = Field(None, description="Filter by filename")
    instrument: str | None = Field(None, description="Filter by instrument name.")
    method_file: str | None = Field(None, description="Filter by method file name.")
    sort: Literal[INSTRUMENT_CONFIG_SORT_COLUMNS] | None = Field(
        None, description="Field to sort by."
    )
    order: str | None = Field(
        None, description="Order of sorting, can be either 'asc' or 'desc'."
    )
    page: int | None = Field(None, description="Page number for pagination.")
    limit: int | None = Field(None, description="Number of items per page.")


class PeakShape(BaseModel):
    x: list[float] = Field(
        ...,
        description="X-axis values representing the mass-to-charge ratio (m/z) for the peak shape.",
    )
    y: list[float] = Field(
        ...,
        description="Y-axis values representing intensity for each corresponding m/z value in the peak shape.",
    )


class InstrumentFunctionData(BaseModel):
    instrument: str = Field(..., description="Instrument name")
    datetime_utc: datetime = Field(
        ...,
        description="UTC timestamp of the sample file used to fit the instrument functions.",
    )
    peakshape: PeakShape = Field(..., description="Peak shape data")
    resolution_function: list[float] = Field(
        ...,
        description="Parameters defining the resolution function, which is used to scale the width of peaks accurately during peak fitting.",
    )


class InstrumentConfigFitParams(BaseModel):
    threshold: float = Field(
        default=0.95,
        description="R-squared threshold filtering non-(skewed) Gaussian peaks from instrument function evaluation",
    )

    @field_validator("threshold")
    @classmethod
    def validate_threshold(cls, v):
        if not 0 <= v < 1:
            raise ValueError(
                "R-squared threshold must be between 0 and 1, inclusive of 0."
            )
        return v


class CreateInstrumentConfigBody(BaseModel):
    instrument: str | None = Field(None, description="Instrument name")
    datetime_utc: datetime | None = Field(
        None,
        description="UTC timestamp of the sample file used to fit the instrument functions.",
    )
    peakshape: PeakShape | None = Field(None, description="Peak shape data")
    resolution_function: list[float] = Field(
        None,
        description="Parameters defining the resolution function, which is used to scale the width of peaks accurately during peak fitting.",
    )
    method_file: str = Field(
        ...,
        description="Name of the instrument config. Must be unique for each instrument.",
    )


class SetInstrumentConfigBody(BaseModel):
    new_record: CreateInstrumentConfigBody | None = Field(
        None, description="A new instrument config to create"
    )
    instrument_function_id: str | None = Field(
        None, description="An existing instrument config id to use"
    )

    @model_validator(mode="after")
    def either_new_or_existing(self):
        expected_either = "Instrument config: expecting either an instrument_function_id argument or a new_record"
        both = self.instrument_function_id and self.new_record
        if both:
            raise ValueError(f"{expected_either} but both were provided.")
        neither = (not self.instrument_function_id) and (not self.new_record)
        if neither:
            raise ValueError(f"{expected_either} but neither was provided.")

        return self


class FitInstrumentConfigBody(BaseModel):
    filename: str = Field(..., description="The filename of the file used for the fit")
    instrument_config_params: InstrumentConfigFitParams = Field(
        InstrumentConfigFitParams(),
        description="The instrument function fitting parameters",
    )
