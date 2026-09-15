"""
Sample item pydantic models for API validation and serialization.

Defines data models for sample item related requests and responses
with validation rules and business logic constraints.
"""

import re
from datetime import datetime
from typing import Dict, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mascope_backend.api.models.base_pydantic_model import (
    CommonValidators,
    QueryParamsModel,
)
from mascope_backend.api.models.sample.items.config import sample_item_config


class SampleItemBaseValidator:
    """Base validator with common validations for all sample item models."""

    @field_validator("sample_item_name")
    @classmethod
    def validate_sample_item_name(cls, sample_item_name: str | None) -> str | None:
        """Validate sample item name is not empty or whitespace."""
        if sample_item_name is not None and sample_item_name.strip() == "":
            raise ValueError(
                "Sample item name cannot be empty or contain only whitespace."
            )
        return sample_item_name

    @field_validator("filter_id")
    @classmethod
    def validate_filter_id_format(cls, filter_id):
        """Validate filter_id matches required format (6 uppercase alphanumeric chars)."""
        if filter_id and not re.match(sample_item_config.FILTER_ID_REGEX, filter_id):
            raise ValueError(
                "Invalid filter_id format. Must be 6 characters long and contain only uppercase letters and numbers."
            )
        return filter_id


class SampleItemValidator(SampleItemBaseValidator, CommonValidators):
    """Full validator for sample items with all field validations."""

    @field_validator("sample_item_type")
    @classmethod
    def validate_sample_item_type(cls, sample_type):
        """Validate sample_item_type is in allowed types list."""
        if sample_type and sample_type not in sample_item_config.all_sample_types:
            allowed_types = ", ".join(sample_item_config.all_sample_types)
            raise ValueError(
                f"Invalid sample item type '{sample_type}'. Allowed types are: {allowed_types}."
            )
        return sample_type

    @field_validator("t0", "t1")
    @classmethod
    def validate_time_values(cls, v):
        """Validate time values are non-negative."""
        if v is not None and v < 0:
            raise ValueError("Time values must be non-negative")
        return v

    @model_validator(mode="after")
    def validate_time_range(self):
        """Validate t0 is less than t1 when both are provided."""
        if self.t0 is not None and self.t1 is not None and self.t0 >= self.t1:
            raise ValueError("t0 must be less than t1")
        return self

    @model_validator(mode="after")
    def validate_sample_type_filter_requirements(self):
        """Validate filter_id requirements based on sample_item_type."""
        if (
            self.sample_item_type in sample_item_config.SAMPLE_TYPES_FILTER_ID_REQUIRED
            and not self.filter_id
        ):
            raise ValueError(
                f"Sample item type '{self.sample_item_type}' requires a filter ID."
            )
        elif (
            self.sample_item_type
            in sample_item_config.SAMPLE_TYPES_FILTER_ID_NOT_ALLOWED
            and self.filter_id
        ):
            raise ValueError(
                f"Sample item type '{self.sample_item_type}' cannot have a filter ID."
            )

        return self


class SampleItemBase(BaseModel):
    """Base model with common fields for sample items."""

    sample_batch_id: str = Field(..., description="ID of the associated sample batch")
    sample_file_id: str = Field(..., description="ID of the associated sample file")
    sample_item_name: str = Field(..., description="Name of the sample item")
    sample_item_type: str = Field(..., description="Type of the sample item")
    sample_item_attributes: Dict = Field(
        ..., description="Attributes of the sample item"
    )
    filter_id: Optional[str] = Field(
        None, description="Optional filter_id of the sample item"
    )
    tic: float = Field(..., description="TIC of the sample item")
    polarity: str = Field(..., description="Polarity of the sample item")
    ionization_mode_id: str = Field(
        ..., description="ID of the ionization mode used for the sample item"
    )
    t0: float = Field(..., description="Start time of the sample item")
    t1: float = Field(..., description="End time of the sample item")

    model_config = ConfigDict(from_attributes=True)


class SampleItemCreate(SampleItemValidator, SampleItemBase):
    """Model for creating sample items with optional time and TIC values."""

    tic: float | None = Field(None, description="TIC of the sample item")
    t0: float | None = Field(None, description="Start time of the sample item")
    t1: float | None = Field(None, description="End time of the sample item")
    ionization_mode_id: str | None = Field(
        None,
        description=(
            "ID of the ionization mode used for the sample item."
            "Optional for creation, as it will be inferred from the filename."
        ),
    )


class SampleItemRead(SampleItemBase):
    """Model for reading sample items - includes database fields."""

    sample_item_id: str = Field(
        ..., description="Unique identifier for the sample item"
    )
    polarity: str | None = Field(
        None,
        description="Polarity of the sample item, made optional for old samples without polarity",
    )
    ionization_mode_id: str | None = Field(
        None, description="ID of the ionization mode used for the sample item"
    )
    locked: int = Field(
        ..., description="Lock status of the sample item (0=unlocked, 1=locked)"
    )
    sample_item_utc_created: datetime = Field(
        ..., description="Timestamp when sample item was created"
    )
    sample_item_utc_modified: datetime | None = Field(
        None, description="Timestamp when sample item was last modified"
    )


class SampleItemUpdate(SampleItemValidator, SampleItemBase):
    """Model for updating sample items - excludes system-only sample item types."""

    @field_validator("sample_item_type")
    @classmethod
    def validate_user_editable_sample_type(cls, sample_type):
        """Validate sample_item_type is user-editable (excludes system-only types)."""
        if (
            sample_type
            and sample_type not in sample_item_config.user_editable_sample_types
        ):
            raise ValueError(
                f"Cannot manually set sample item type to '{sample_type}'. "
                f"This type is system-managed."
            )
        return sample_type


class GetSampleItemsQueryValidator:
    """Validator with common validations for common sample items query parameters."""

    @field_validator("polarity")
    @classmethod
    def validate_polarity_list(cls, polarities: list[str] | None) -> list[str] | None:
        """Validate polarities in the list."""
        if polarities:
            valid_polarities = sample_item_config.SAMPLE_POLARITY
            for polarity in polarities:
                if polarity not in valid_polarities:
                    raise ValueError(
                        f"Invalid polarity '{polarity}'. Must be one of: {', '.join(valid_polarities)}"
                    )
        return polarities

    @field_validator("sample_item_type")
    @classmethod
    def validate_sample_item_type_list(
        cls, sample_item_types: list[str] | None
    ) -> list[str] | None:
        """Validate sample item types in the list."""
        if sample_item_types:
            valid_types = sample_item_config.all_sample_types
            for sample_item_type in sample_item_types:
                if sample_item_type not in valid_types:
                    raise ValueError(
                        f"Invalid sample item type '{sample_item_type}'. Must be one of: {valid_types}"
                    )
        return sample_item_types


# Columns `sort` accepts (see mascope_backend.api.lib.sorting).
SAMPLE_ITEM_SORT_COLUMNS = (
    "sample_item_id",
    "sample_batch_id",
    "sample_file_id",
    "sample_item_name",
    "sample_item_type",
    "locked",
    "filter_id",
    "tic",
    "polarity",
    "ionization_mode_id",
    "t0",
    "t1",
    "sample_item_utc_created",
    "sample_item_utc_modified",
)


class GetSampleItemsQueryParams(GetSampleItemsQueryValidator, QueryParamsModel):
    """
    This model defines the query parameters that can be passed to the GET /api/sample/items endpoint
    to control filtering, sorting, ordering, and pagination of sample results.
    Inherits polarity validation from CommonValidators and URL decoding from QueryParamsModel.
    """

    sample_batch_id: str = Field(
        ...,
        description="The sample batch ID for which you want to fetch the sample items",
    )
    sample_file_id: str | None = Field(
        None,
        description="The sample file ID for which you want to fetch the sample items",
    )
    sample_item_type: list[str] | None = Field(
        default=None,
        description="Filter by sample item types. Can specify multiple types.",
    )
    polarity: list[str] | None = Field(
        default=None,
        description="Filter by ion polarity modes (+, -). Can specify multiple polarities.",
    )
    sort: Literal[SAMPLE_ITEM_SORT_COLUMNS] | None = Field(
        "sample_item_utc_created",
        description="Column name by which to sort the results. Default is 'sample_item_utc_created'",
    )
    order: str | None = Field(
        "asc",
        description="Sorting order, either 'asc' for ascending or 'desc' for descending. Default is 'asc'",
    )
    page: int | None = Field(
        None, description="Page number for pagination. Default is None (return all)"
    )
    limit: int | None = Field(
        None, description="Number of results per page. Default is None (return all)"
    )


class SampleItemUpdateBody(BaseModel):
    sample_item: SampleItemUpdate = Field(
        ..., description="The sample item fields to update"
    )


class SampleItemsDeleteBody(BaseModel):
    sample_item_ids: list[str] = Field(
        ..., min_length=1, description="The sample item IDs of samples to be deleted"
    )


class SampleItemsCopyBody(BaseModel):
    sample_batch_id: str = Field(
        ..., description="ID of the sample batch where to copy sample items"
    )
    sample_item_ids: list[str] = Field(
        ..., min_length=1, description="Sample item IDs to copy"
    )


class SampleItemsMoveBody(BaseModel):
    sample_batch_id: str = Field(
        ..., description="ID of the sample batch where to move sample items"
    )
    sample_item_ids: list[str] = Field(
        ..., min_length=1, description="Sample item IDs to move"
    )
