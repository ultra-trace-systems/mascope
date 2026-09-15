"""
Dataset pydantic models for API validation and serialization.

Defines data models for dataset related requests and responses
with validation rules and business logic constraints.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mascope_backend.api.models.base_pydantic_model import QueryParamsModel
from mascope_backend.api.models.dataset.config import dataset_config


# The width of `Dataset.dataset_name` (String(256)). Bound here so a name too
# long for the column is refused as a 422 by request validation; without it the
# insert reaches Postgres and comes back as StringDataRightTruncation, which
# `process_exception` reports as a 500 - a server fault for what is a
# perfectly diagnosable client mistake. The migration that de-duplicates names
# trims its " (n)" suffixes to the same width.
#
# Measured against the name as submitted: pydantic applies a field constraint
# before an `after` validator, so `validate_dataset_name` has not stripped it
# yet. The bound is therefore very slightly stricter than the column - a name
# of 257 characters ending in a space would have fit once stripped - which is
# the harmless direction, and keeping it a plain `max_length` is what puts it
# in the OpenAPI schema for clients to see.
DATASET_NAME_MAX_LENGTH = 256


class DatasetIcon(BaseModel):
    """Icon configuration for dataset."""

    icon_id: str = Field(..., description="Icon identifier/class name")
    color: str = Field(..., description="Color in hex format (e.g., #3B82F6)")

    @field_validator("color")
    @classmethod
    def validate_color_format(cls, color: str) -> str:
        """Validate color is in hex format."""
        if not color.startswith("#") or len(color) != 7:
            raise ValueError("Color must be in hex format (e.g., #3B82F6)")
        return color


class DatasetBaseValidator:
    """Mixin class with common schemas fields validators."""

    @field_validator("dataset_name")
    @classmethod
    def validate_dataset_name(cls, dataset_name: str | None) -> str | None:
        """
        Validates that `dataset_name` is not an empty string or just whitespace.

        The name is stripped so stored names stay normalised (workspace names
        are handled the same way). Uniqueness is decided elsewhere, by the
        canonical key `lower(btrim(dataset_name))` that Postgres evaluates in
        `_assert_name_available` and in `uq_dataset_workspace_name_ci` alike -
        so a name that only differs by leading or trailing spaces from a
        stored one is refused, whether or not that stored row was ever seen by
        this validator.

        The two normalisations are not the same set, and the difference is
        worth being precise about. `str.strip()` here removes every character
        Python calls whitespace (tab, CR, LF, VT, FF, and the Unicode spaces
        U+00A0, U+1680, U+2007, U+202F, U+3000 ...); Postgres `btrim()` with
        its default trim set removes U+0020 only. So:

        - A name arriving through the API is stripped of all of them here, and
          therefore matches the key of any stored row it looks like.
        - A row written before this validator existed can still carry, say, a
          trailing tab. `btrim()` does not remove it, so that row keys
          differently and a stripped look-alike is accepted beside it. The
          index is not widened to fix this: changing the trim set would change
          the index expression - and the migration behind it - for an exotic
          case.
        - U+200B and U+FEFF are not whitespace to `str.strip()` either, so a
          name padded with one of those is stored as sent and keys as sent, in
          both directions.

        The gap only ever costs a look-alike pair, never a fault: this
        normalisation is strictly more aggressive than `btrim()`, so a name
        the pre-write check accepts is never one the index then rejects, and
        no input can turn into a 500 this way.

        :param dataset_name: The name provided for the dataset.
        :raises ValueError: If the dataset_name is an empty string or only whitespace.
        :return: The stripped dataset_name if it is valid.
        """
        if dataset_name is not None and dataset_name.strip() == "":
            raise ValueError(
                "The dataset name cannot be empty or contain only whitespace."
            )
        return dataset_name.strip() if dataset_name is not None else None


class DatasetValidator(DatasetBaseValidator):
    """Validators for all fields."""

    @field_validator("dataset_type")
    @classmethod
    def validate_dataset_type(cls, dataset_type: str | None) -> str | None:
        """Validate dataset type."""
        if dataset_type and dataset_type not in dataset_config.DATASET_TYPES:
            raise ValueError(
                f"Invalid dataset type. Must be one of: {dataset_config.DATASET_TYPES}"
            )
        return dataset_type

    @field_validator("instrument")
    @classmethod
    def validate_instrument(cls, instrument: str | None) -> str | None:
        """Validate instrument is not empty."""
        if instrument is not None and instrument.strip() == "":
            raise ValueError("Instrument cannot be empty or contain only whitespace")
        return instrument


class DatasetBase(DatasetValidator, BaseModel):
    """Base model with common fields for Dataset."""

    dataset_name: str = Field(
        ...,
        max_length=DATASET_NAME_MAX_LENGTH,
        description="Name of the dataset",
    )
    dataset_description: str | None = Field(
        None, description="Description of the dataset"
    )
    dataset_type: str = Field(
        default=dataset_config.DEFAULT_DATASET_TYPE,
        description="Type of dataset (ACQUISITION or ANALYSIS)",
    )
    instrument: str | None = Field(
        None, description="Instrument associated with the dataset"
    )
    icon: DatasetIcon | None = Field(
        None, description="Icon configuration with icon_id and color"
    )

    model_config = ConfigDict(from_attributes=True)


class DatasetCreate(DatasetBase):
    """Model used for dataset creation requests."""

    @model_validator(mode="after")
    def validate_acquisition_constraints(self):
        """Validate rules for ACQUISITION datasets."""
        if self.dataset_type == "ACQUISITION":
            # ACQUISITION datasets must have instrument
            if not self.instrument:
                raise ValueError("Acquisition datasets must specify an instrument")

        return self


class DatasetRead(DatasetBase):
    """Model used for reading datasets, includes database fields."""

    dataset_id: str = Field(..., description="Unique identifier for the dataset")
    workspace_id: str = Field(
        ..., description="ID of the workspace this dataset belongs to"
    )
    locked: int = Field(
        description="Lock status of the dataset (0=unlocked, 1=locked)",
    )
    dataset_utc_created: datetime = Field(
        ..., description="Timestamp when dataset was created"
    )
    dataset_utc_modified: datetime | None = Field(
        None, description="Timestamp when dataset was last modified"
    )


class DatasetUpdate(DatasetBaseValidator, BaseModel):
    """Model used for dataset update requests - only user-editable fields.
    All fields optional."""

    dataset_name: str | None = Field(
        None,
        max_length=DATASET_NAME_MAX_LENGTH,
        description="Name of the dataset",
    )
    dataset_description: str | None = Field(
        None, description="Description of the dataset"
    )
    icon: DatasetIcon | None = Field(
        None, description="Icon configuration with icon_id and color"
    )

    model_config = ConfigDict(from_attributes=True)


class GetDatasetsQueryParams(DatasetBaseValidator, QueryParamsModel):
    """
    Query parameters for filtering and paginating dataset listings.

    This model defines the parameters that can be passed to the get_datasets endpoint
    to control sorting, ordering, and pagination of dataset results.
    """

    dataset_name: str | None = Field(
        None,
        description="Filter by dataset name.",
    )
    dataset_type: list[str] | None = Field(
        default=None,
        description="Filter by dataset types (ACQUISITION, ANALYSIS). Can specify many",
    )
    instrument: list[str] | None = Field(
        None, description="Filter by associated instruments. Can specify many"
    )
    sort: str | None = Field(
        "dataset_utc_created",
        description=(
            "Column name by which you want to sort the results. "
            "The column name should be one of the columns in the dataset table."
        ),
    )
    order: str | None = Field(
        "asc",
        description="Sorting order, asc for ascending or desc for descending",
    )
    page: int | None = Field(None, description="Page number for pagination.")
    limit: int | None = Field(None, description="Number of results per page.")

    @field_validator("dataset_type")
    @classmethod
    def validate_dataset_type_list(
        cls, dataset_types: list[str] | None
    ) -> list[str] | None:
        """Validate dataset types in the list."""
        if dataset_types:
            for dataset_type in dataset_types:
                if dataset_type not in dataset_config.DATASET_TYPES:
                    raise ValueError(
                        f"Invalid dataset type '{dataset_type}'. "
                        f"Must be one of: {dataset_config.DATASET_TYPES}"
                    )
        return dataset_types


class DatasetMoveBody(BaseModel):
    """Request body for moving a dataset into another workspace."""

    target_workspace_id: str = Field(
        ..., description="ID of the workspace to move the dataset into"
    )
