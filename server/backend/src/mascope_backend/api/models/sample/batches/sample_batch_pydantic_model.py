"""
Sample batch pydantic models for API validation and serialization.

Defines data models for sample batch related requests and responses
with validation rules and business logic constraints.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mascope_backend.api.models.base_pydantic_model import QueryParamsModel
from mascope_backend.api.models.sample.batches.config import sample_batch_config
from mascope_backend.api.models.sample.items.sample_item_pydantic_model import (
    SampleItemCreate,
)


class SampleBatchBaseValidator:
    """Base validation logic for sample batch shared fields."""

    @field_validator("sample_batch_name")
    @classmethod
    def validate_sample_batch_name(cls, sample_batch_name: str | None) -> str | None:
        """Validate sample batch name is not null, empty or whitespace.

        A field validator does not run on a default, so on an update that
        leaves the name out this never sees None: a None here is an explicit
        null, which the NOT NULL column cannot store."""
        if sample_batch_name is None:
            raise ValueError("Sample batch name cannot be null.")
        if sample_batch_name.strip() == "":
            raise ValueError(
                "Sample batch name cannot be empty or contain only whitespace."
            )
        return sample_batch_name


class SampleBatchValidator(SampleBatchBaseValidator):
    """Validators for all fields."""

    @field_validator("sample_batch_type")
    @classmethod
    def validate_sample_batch_type(cls, sample_batch_type: str | None) -> str | None:
        """Complete validation logic for all sample batch fields."""
        if (
            sample_batch_type
            and sample_batch_type not in sample_batch_config.SAMPLE_BATCH_TYPES
        ):
            raise ValueError(
                f"Invalid sample batch type. Must be one of: {', '.join(sample_batch_config.SAMPLE_BATCH_TYPES)}"
            )
        return sample_batch_type

    @field_validator("polarity")
    @classmethod
    def validate_polarity(cls, polarity: str | None) -> str | None:
        """Validate polarity values."""
        if polarity and polarity not in sample_batch_config.all_sample_batch_polarities:
            raise ValueError(
                f"Invalid sample batch polarity. Must be one of: {', '.join(sample_batch_config.all_sample_batch_polarities)}"
            )
        return polarity

    @model_validator(mode="after")
    def validate_polarity_by_batch_type(self):
        """Validate polarity constraints based on sample batch type."""
        if self.sample_batch_type == "ACQUISITION":
            if self.polarity not in sample_batch_config.ACQUISITION_POLARITY:
                raise ValueError(
                    f"Invalid acquisition batch polarity. Must be one of: {', '.join(sample_batch_config.ACQUISITION_POLARITY)}. "
                    f"Got: '{self.polarity}'"
                )
        elif self.sample_batch_type == "ANALYSIS":
            if self.polarity != sample_batch_config.ANALYSIS_POLARITY:
                raise ValueError(
                    f"Analysis batch should have polarity '{sample_batch_config.ANALYSIS_POLARITY}'. "
                    f"Got: '{self.polarity}'"
                )

        return self


class SampleBatchBase(BaseModel):
    """Base model with common fields for SampleBatch.

    Fields only: a response model built on this reports a stored row as it is
    (see SampleBatchRead). The write models mix the validators in."""

    dataset_id: str = Field(
        ..., description="ID of the dataset associated with the sample batch"
    )
    sample_batch_name: str = Field(..., description="Name of the sample batch")
    sample_batch_description: str | None = Field(
        "", description="Description of the sample batch"
    )
    sample_batch_type: str = Field(
        default=sample_batch_config.DEFAULT_SAMPLE_BATCH_TYPE,
        description="Type of sample batch (ACQUISITION or ANALYSIS)",
    )
    polarity: str = Field(
        default=sample_batch_config.ANALYSIS_POLARITY,
        description="Polarity of the sample batch (+, -, or +-)",
    )
    model_config = ConfigDict(from_attributes=True)


_SEED_BATCH_PEAKS = (
    "Whether the batch ledger seeds its batch peaks from the batch's target "
    "ions: the first fold of each ionization mode and instrument type puts a "
    "batch peak at every target ion's m/z before the sample's own peaks fold. "
    "Off by default. Turning it on for a batch whose ledger exists seeds "
    "nothing until a rebuild asks to re-seed."
)


class SampleBatchCreate(SampleBatchValidator, SampleBatchBase):
    """Model used for sample batch creation requests."""

    target_collection_ids: list[str] = Field(
        ..., description="IDs of target collections associated with the sample batch"
    )
    seed_batch_peaks: bool = Field(False, description=_SEED_BATCH_PEAKS)


class SampleBatchRead(SampleBatchBase):
    """Sample batch response model with added database fields.

    Not validated: `polarity` defaults to '+-' in the database, which
    `validate_polarity_by_batch_type` refuses on an ACQUISITION batch, so
    re-running the write validators here would answer 400 for a whole
    listing over one such row."""

    sample_batch_id: str = Field(
        ..., description="Unique identifier for the sample batch"
    )
    status: str = Field(
        ...,
        description="Processing status of the sample batch (ready, processing, rematch)",
    )
    locked: int = Field(
        ..., description="Lock status of the sample batch (0=unlocked, 1=locked)"
    )
    sample_batch_utc_created: datetime = Field(
        ..., description="Timestamp when sample batch was created"
    )
    sample_batch_utc_modified: datetime | None = Field(
        None, description="Timestamp when sample batch was last modified"
    )
    seed_batch_peaks: bool = Field(False, description=_SEED_BATCH_PEAKS)


class SampleBatchListed(SampleBatchRead):
    """A batch as the batch listing gives it: with the span of its samples.

    The span lets a caller after a time range pass over a batch that holds
    none of it without listing its samples (``mascope_viz``'s ``resolve``).
    A sample's time is its file's start, as the samples listing gives it."""

    first_sample_datetime_utc: datetime | None = Field(
        None,
        description=(
            "The earliest start of a file of the batch's samples; null for a "
            "batch with no sample"
        ),
    )
    last_sample_datetime_utc: datetime | None = Field(
        None,
        description=(
            "The latest start of a file of the batch's samples; null for a "
            "batch with no sample"
        ),
    )
    sample_instruments: list[str] = Field(
        default_factory=list,
        description=(
            "The instruments of the batch's samples' files, sorted; empty for "
            "a batch with no sample"
        ),
    )


class SampleBatchUpdate(SampleBatchBaseValidator, BaseModel):
    """
    Model for updating sample batches - only user-editable fields.

    A partial update: a field left out stays as it is. The name cannot be
    null (the column is NOT NULL), the description can. Target collection
    associations are handled separately on service level: a list replaces
    them, an empty list detaches them all, and leaving the field out (or
    sending null) leaves them as they are.
    """

    sample_batch_name: str | None = Field(
        None,
        description=(
            "Name of the sample batch; absent, it stays as it is; null is refused."
        ),
    )
    sample_batch_description: str | None = Field(
        None, description="Description of the sample batch"
    )
    target_collection_ids: list[str] | None = Field(
        None,
        description=(
            "The target collections the batch is to have, all of them; absent, "
            "the associations stay as they are; an empty list detaches them all."
        ),
    )
    seed_batch_peaks: bool | None = Field(
        None,
        description=_SEED_BATCH_PEAKS + " Absent, it stays as it is; null is refused.",
    )

    model_config = ConfigDict(from_attributes=True)

    @field_validator("seed_batch_peaks")
    @classmethod
    def validate_seed_batch_peaks(cls, value: bool | None) -> bool:
        """Refuse an explicit null: the column is NOT NULL, and a field left
        out never reaches a validator."""
        if value is None:
            raise ValueError("seed_batch_peaks cannot be null.")
        return value


class SampleBatchUpdateStatusBody(BaseModel):
    """
    Model for updating sample batch statuses in bulk.

    Allows updating multiple sample batches to the same status
    """

    sample_batch_ids: list[str] = Field(
        ..., description="List of sample batch IDs to update", min_length=1
    )
    status: str = Field(..., description="New status to set for all specified batches")

    @field_validator("status")
    @classmethod
    def validate_status(cls, status: str) -> str:
        """Validate that status is one of the allowed values."""
        if status not in sample_batch_config.SAMPLE_BATCH_STATUSES:
            raise ValueError(
                f"Invalid batch status '{status}'. Must be one of: {', '.join(sample_batch_config.SAMPLE_BATCH_STATUSES)}"
            )
        return status


# Columns `sort` accepts (see mascope_backend.api.lib.sorting).
SampleBatchSortColumn = Literal[
    "sample_batch_id",
    "dataset_id",
    "sample_batch_name",
    "sample_batch_description",
    "sample_batch_type",
    "status",
    "locked",
    "polarity",
    "sample_batch_utc_created",
    "sample_batch_utc_modified",
]


class GetSampleBatchesQueryParams(QueryParamsModel):
    dataset_id: str = Field(
        description="The dataset ID for which to fetch sample batches.",
    )
    sample_batch_name: str | None = Field(
        None, description="Filter by name of the sample batch"
    )
    sample_batch_type: list[str] | None = Field(
        default=None,
        description="Filter by sample batch types (ACQUISITION, ANALYSIS). Can specify multiple types.",
    )
    status: list[str] | None = Field(
        default=None,
        description="Filter by processing status (ready, processing, rematch). Can specify multiple statuses.",
    )
    polarity: list[str] | None = Field(
        default=None,
        description="Filter by polarities (+, -, +-). Can specify multiple polarities.",
    )
    sort: SampleBatchSortColumn | None = Field(
        "sample_batch_utc_created",
        description="Column name by which you want to sort the results. The column name should be one of the columns in the sample batch table.",
    )
    order: str | None = Field(
        "asc",
        description="Sorting order which can be asc for ascending or desc for descending.",
    )
    page: int | None = Field(None, description="Page number for pagination.")
    limit: int | None = Field(None, description="Number of results per page.")

    @field_validator("sample_batch_type")
    @classmethod
    def validate_sample_batch_type_list(
        cls, sample_batch_types: list[str] | None
    ) -> list[str] | None:
        """Validate sample batch types."""
        if sample_batch_types:
            for sample_batch_type in sample_batch_types:
                if sample_batch_type not in sample_batch_config.SAMPLE_BATCH_TYPES:
                    raise ValueError(
                        f"Invalid sample batch type '{sample_batch_type}'. Must be one of: {', '.join(sample_batch_config.SAMPLE_BATCH_TYPES)}"
                    )
        return sample_batch_types

    @field_validator("status")
    @classmethod
    def validate_status_list(cls, statuses: list[str] | None) -> list[str] | None:
        """Validate sample batch statuses."""
        if statuses:
            for status in statuses:
                if status not in sample_batch_config.SAMPLE_BATCH_STATUSES:
                    raise ValueError(
                        f"Invalid sample batch status '{status}'. Must be one of: {', '.join(sample_batch_config.SAMPLE_BATCH_STATUSES)}"
                    )
        return statuses

    @field_validator("polarity")
    @classmethod
    def validate_polarity_list(cls, polarities: list[str] | None) -> list[str] | None:
        """Validate polarities."""
        if polarities:
            for polarity in polarities:
                if polarity not in sample_batch_config.all_sample_batch_polarities:
                    raise ValueError(
                        f"Invalid polarity '{polarity}'. Must be one of: {', '.join(sample_batch_config.all_sample_batch_polarities)}"
                    )
        return polarities


class GetSampleBatchTargetsQueryParams(QueryParamsModel):
    deduplicate: bool | None = Field(
        False,
        description="Drop the potential duplicates (added to several target collections). Target collection info added if deduplicate is False.",
    )


class SampleBatchImportSamplesBody(BaseModel):
    sample_items: list[SampleItemCreate] = Field(
        ..., description="Sample items to be created and imported to the sample batch"
    )

    @model_validator(mode="after")
    def check_sample_items(self):
        batch_ids = {sample.sample_batch_id for sample in self.sample_items}
        if len(batch_ids) > 1:
            raise ValueError(
                "All samples should be imported to the same batch, please check if the sample batch ID is the same for all importing samples."
            )
        return self


class SampleBatchCopyBody(BaseModel):
    dataset_id: str = Field(
        ..., description="ID of the dataset where to copy the batch"
    )
    sample_batch_name: str = Field(..., description="Name of the new sample batch")
    sample_batch_description: str | None = Field(
        None, description="Description of the new sample batch"
    )


class SampleBatchMoveBody(BaseModel):
    """Request body for moving a sample batch into another dataset."""

    dataset_id: str = Field(..., description="ID of the dataset to move the batch into")
