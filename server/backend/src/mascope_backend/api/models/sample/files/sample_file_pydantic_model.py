import os
from datetime import datetime as dt
from typing import Annotated, Dict, List, Literal, Optional

from fastapi import UploadFile
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, field_validator, model_validator

from mascope_backend.api.models.base_pydantic_model import (
    CommonValidators,
    QueryParamsModel,
    RequestBodyModel,
)


# TODO_configuration Default sample file upload params
FILE_UPLOAD_EXTENSIONS = {".h5", ".raw"}
FILE_UPLOAD_SIZE_LIMIT = 2.5 * 1024**3  # 2.5 GB


class SampleFileBase(BaseModel):
    instrument_function_id: str | None = Field(
        None,
        description=(
            "ID of the instrument config used for processing this file. "
            "None values means the sample file is blank "
            "(has no peaks to compute the instrument function from)."
        ),
    )
    filename: str = Field(..., description="Name of the sample file")
    instrument: str = Field(..., description="Instrument associated with the file")
    method_file: str | None = Field(None, description="Instrument config name")
    datetime: dt = Field(
        ..., description="Datetime (local) of creation of the sample file"
    )
    datetime_utc: dt = Field(
        ..., description="Datetime (UTC) of creation of the sample file"
    )
    length: float = Field(..., description="Length of the sample file")
    range: list[float] = Field(..., description="m/z range of the sample file")
    mz_calibration: dict | None = Field(
        None, description="m/z calibration function parameters of the sample file"
    )
    polarity: str = Field(..., description="Polarities present in the sample file")

    @field_validator("polarity")
    @classmethod
    def validate_polarity(cls, v):
        if v not in ["+", "-", "+-", "-+"]:
            raise ValueError("Polarity must be '+', '-', '+-' or '-+'")
        return v


class SampleFileCreate(SampleFileBase):
    uploaded_by_device_id: int | None = Field(
        None,
        description=(
            "The paired device the upload came from (the converter carries "
            "it through from the upload). None for web and pre-registry "
            "agent uploads. The uploading user is recorded server-side from "
            "the authenticated request, never from the body."
        ),
    )
    acquisition_timezone: str | None = Field(
        None,
        description=(
            "IANA timezone of the uploading machine, when the agent "
            "reported a valid one"
        ),
    )
    utc_offset_source: str | None = Field(
        None,
        description=(
            "What determined the UTC offset applied to datetime_utc: "
            "'file', 'agent' or 'guess'"
        ),
    )


class SampleFileUpdate(BaseModel):
    filename: str = Field(..., description="Name of the sample file")
    instrument: str = Field(..., description="Instrument associated with the file")
    method_file: Optional[str] = Field(
        None, description="The instrument config name associated with the file"
    )
    instrument_function_id: Optional[str] = Field(
        None, description="The ID of the instrument function associated with the file"
    )
    datetime: dt = Field(
        ..., description="Datetime (local) of creation of the sample file"
    )
    datetime_utc: dt = Field(
        ..., description="Datetime (UTC) of creation of the sample file"
    )
    length: float = Field(..., description="Length of the sample file")
    range: List[float] = Field(..., description="m/z range of the sample file")
    mz_calibration: Optional[Dict] = Field(
        None, description="m/z calibration function parameters of the sample file"
    )
    polarity: str = Field("", description="Polarities present in the sample file")


class SampleFilesUpload(BaseModel):
    """Model for validating multiple file uploads with size and extension checks."""

    files: list[UploadFile] = Field(..., description="List of files to upload")

    @field_validator("files")
    @classmethod
    def validate_files(cls, files: list[UploadFile]) -> list[UploadFile]:
        """
        Validate all uploading files for extension and size requirements.

        :param files: List of uploaded files to validate
        :type files: list[UploadFile]
        :return: Validated list of files
        :rtype: list[UploadFile]
        :raises RequestValidationError: If any file fails validation
        """
        if not files:
            raise RequestValidationError(
                [
                    {
                        "loc": ("files",),
                        "msg": "At least one file must be provided",
                        "type": "value_error.empty_list",
                    }
                ]
            )

        for i, file in enumerate(files):
            # Validate extension
            file_ext = os.path.splitext(file.filename or "")[1].lower()
            if file_ext not in FILE_UPLOAD_EXTENSIONS:
                raise RequestValidationError(
                    [
                        {
                            "loc": ("files", i),
                            "msg": f"Invalid file extension for {file.filename}. Allowed: {', '.join(FILE_UPLOAD_EXTENSIONS)}",
                            "type": "value_error.file_extension",
                        }
                    ]
                )

            # Validate size
            if (
                hasattr(file, "size")
                and file.size
                and file.size > FILE_UPLOAD_SIZE_LIMIT
            ):
                size_limit_gb = FILE_UPLOAD_SIZE_LIMIT / (1024**3)
                raise RequestValidationError(
                    [
                        {
                            "loc": ("files", i),
                            "msg": f"File {file.filename} exceeds size limit of {size_limit_gb:.1f} GB",
                            "type": "value_error.file_size",
                        }
                    ]
                )

        return files


class GetSampleFilesQueryParams(QueryParamsModel):
    datetime_min: Optional[dt] = Field(None, description="Minimum datetime filter")
    datetime_max: Optional[dt] = Field(None, description="Maximum datetime filter")
    instrument: Optional[str] = Field(None, description="Filter by instrument")
    filename: Optional[str] = Field(None, description="Filter by filename")
    sort: Optional[str] = Field(
        "datetime_utc",
        description="The column name by which you want to sort the results.",
    )
    order: Optional[str] = Field(
        "asc",
        description="Can either be 'asc' for ascending order or 'desc' for descending order.",
    )
    page: int | None = Field(
        None,
        description="The page number for pagination, optional. None for no pagination.",
    )
    limit: int | None = Field(
        None,
        description="The number of results per page, optional. None for no pagination.",
    )


class GetRecentSampleFilesQueryParams(GetSampleFilesQueryParams):
    days: int = Field(
        1, description="Number of days to look back from current datetime"
    )


class GetSampleFilePeaksQueryParams(QueryParamsModel):
    areas: bool = Field(
        True,
        description="Include peak areas in the response. Represents the integrated area under the curve for each peak, reflecting the total intensity over time.",
    )
    heights: bool = Field(
        True,
        description="Include peak heights in the response. Represents the maximum intensity at the apex of each peak, showing the peak's highest intensity value.",
    )

    @model_validator(mode="after")
    def validate_peak_variables(self):
        if not self.areas and not self.heights:
            raise ValueError(
                "You need to request either peak areas, peak heights, or both. At least one of 'areas' or 'heights' must be set to True. "
            )
        return self


class GetSampleFilePeakTimeseriesBody(BaseModel):
    peak_mz: float
    peak_mz_tolerance_ppm: Optional[float] = 1


class GetSampleFilePeakNoiseBody(CommonValidators, RequestBodyModel):
    """
    Request body for computing peak noise.
    Uses shared polarity and time range validation from CommonValidators.
    """

    mzs: list[float] = Field(
        ..., description="List of peak m/z values to compute noise for"
    )
    t_min: float | None = Field(None, description="Start time (optional)")
    t_max: float | None = Field(None, description="End time (optional)")
    ppm: int | None = Field(1, description="ppm precision for binning, defaults to 1")
    polarity: Literal["+", "-"] | None = Field(
        None, description="Polarity of the scans, '+' or '-', optional"
    )


class GetSpectrumQueryParams(CommonValidators, QueryParamsModel):
    """
    Query parameters for spectrum data with special requirement that both time fields must be provided together.
    Inherits basic time range validation from CommonValidators and adds additional constraint.
    """

    t_min: Annotated[float, Field(ge=0)] | None = Field(
        None, description="Start of the time range"
    )
    t_max: Annotated[float, Field(gt=0)] | None = Field(
        None, description="End of the time range"
    )
    mz_min: Annotated[float, Field(ge=0)] | None = Field(
        None, description="Start of the m/z range"
    )
    mz_max: Annotated[float, Field(gt=0)] | None = Field(
        None, description="End of the m/z range"
    )

    @model_validator(mode="after")
    def validate_time_fields_required_together(self):
        """
        Additional validation: both t_min and t_max must be provided together.
        The basic t_max > t_min validation is inherited from CommonValidators.
        """
        t_min = getattr(self, "t_min", None)
        t_max = getattr(self, "t_max", None)

        # Specific model logic: both must be provided together
        if (t_min is None) != (t_max is None):  # XOR - exactly one is None
            raise ValueError("Both t_min and t_max must be provided together")

        return self

    @model_validator(mode="after")
    def validate_mz_range(self):
        """
        Validates m/z range with same logic as time range.
        """
        mz_min = getattr(self, "mz_min", None)
        mz_max = getattr(self, "mz_max", None)

        if (mz_min is None) != (mz_max is None):
            raise ValueError("Both mz_min and mz_max must be provided together")

        if mz_min is not None and mz_max is not None and mz_max <= mz_min:
            raise ValueError("mz_max must be greater than mz_min")

        return self


class DeleteSampleFilesBody(RequestBodyModel):
    sample_file_ids: list[str] | None = Field(
        None, description="List of sample file IDs to delete", min_length=1
    )
    filenames: list[str] | None = Field(
        None, description="List of filenames to delete", min_length=1
    )

    @field_validator("sample_file_ids")
    @classmethod
    def validate_unique_ids(cls, v: list[str] | None) -> list[str] | None:
        """Validate that sample file IDs are unique."""
        if v is not None and len(set(v)) != len(v):
            raise ValueError("Sample file IDs must be unique")
        return v

    @field_validator("filenames")
    @classmethod
    def validate_unique_filenames(cls, v: list[str] | None) -> list[str] | None:
        """Validate that filenames are unique."""
        if v is not None and len(set(v)) != len(v):
            raise ValueError("Filenames must be unique")
        return v

    @model_validator(mode="after")
    def validate_exactly_one_field(self):
        """Validate that exactly one of sample_file_ids or filenames is provided."""
        has_ids = self.sample_file_ids is not None
        has_filenames = self.filenames is not None

        if not (has_ids or has_filenames):
            raise ValueError("Either sample_file_ids or filenames must be provided")
        if has_ids and has_filenames:
            raise ValueError("Cannot provide both sample_file_ids and filenames")

        return self


class ReprocessSampleFilesBody(RequestBodyModel):
    sample_file_ids: list[str] = Field(
        ..., description="List of sample file IDs to re-process", min_length=1
    )

    @field_validator("sample_file_ids")
    @classmethod
    def validate_unique_ids(cls, v: list[str]) -> list[str]:
        """Validate that sample file IDs are unique."""
        if len(set(v)) != len(v):
            raise ValueError("Sample file IDs must be unique")
        return v
