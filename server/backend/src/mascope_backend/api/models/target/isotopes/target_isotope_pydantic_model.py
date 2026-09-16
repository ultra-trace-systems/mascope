"""
Target isotope pydantic models for API validation and serialization.

Defines data models for target isotope related requests and responses
with validation rules and business logic constraints.
"""

from typing import Literal

from pydantic import Field, field_validator

from mascope_backend.api.models.base_pydantic_model import QueryParamsModel
from mascope_backend.api.models.target.isotopes.config import target_isotope_config


# Columns `sort` accepts (see mascope_backend.api.lib.sorting).
TargetIsotopeSortColumn = Literal[
    "target_isotope_id",
    "target_ion_id",
    "target_isotope_formula",
    "mz",
    "relative_abundance",
    "resolution",
]


class GetTargetIsotopesQueryParams(QueryParamsModel):
    """Query parameters for retrieving target isotopes with filtering, sorting, and pagination."""

    target_ion_id: str | None = Field(None, description="Filter by target ion ID.")
    min_mz: float | None = Field(None, description="Minimum m/z value for filtering.")
    max_mz: float | None = Field(None, description="Maximum m/z value for filtering.")
    min_relative_abundance: float | None = Field(
        None, description="Minimum relative abundance for filtering."
    )
    max_relative_abundance: float | None = Field(
        None, description="Maximum relative abundance for filtering."
    )
    resolution: str | None = Field(
        None, description="Type of the target isotope resolution (HIGH or LOW)"
    )
    target_compound_ids: list[str] = Field(
        default=None, description="List of target compound IDs to filter isotopes."
    )
    ionization_mechanism_ids: list[str] = Field(
        default=None, description="List of ionization mechanism IDs to filter isotopes."
    )
    sample_batch_id: str | None = Field(
        None,
        description="ID of the sample batch for filtering the associated isotopes.",
    )
    target_collection_id: str | None = Field(
        None, description="The ID of the target collection to filter isotopes by."
    )
    show_target_collection: bool = Field(
        False,
        description="Flag to include target collection details. Compounds present in multiple collections will be shown separately.",
    )
    show_match_params: bool = Field(
        False,
        description="Flag to include match parameters of the isotope's parent target ion.",
    )
    show_ionization_mechanism: bool = Field(
        False,
        description="Flag to include ionization mechanism details including polarity.",
    )
    sort: TargetIsotopeSortColumn | None = Field(None, description="Field to sort by.")
    order: str | None = Field(None, description="Order of sorting ('asc' or 'desc').")
    page: int | None = Field(None, description="Pagination page number.")
    limit: int | None = Field(None, description="Number of items per page.")

    @field_validator("resolution")
    @classmethod
    def validate_resolution(cls, resolution):
        """Ensure isotope `resolution` is one of the allowed types."""
        if (
            resolution is not None
            and resolution not in target_isotope_config.ISOTOPE_RESOLUTION_TYPES
        ):
            allowed_types = ", ".join(target_isotope_config.ISOTOPE_RESOLUTION_TYPES)
            raise ValueError(
                f"Invalid isotope resolution type '{resolution}'. Allowed types are: {allowed_types}."
            )
        return resolution

    @field_validator("min_relative_abundance", "max_relative_abundance")
    @classmethod
    def validate_relative_abundance(cls, abundance: float | None) -> float | None:
        """Ensure relative abundance is within valid range (0.0 to 1.0)."""
        if abundance is not None:
            if not (
                target_isotope_config.MIN_RELATIVE_ABUNDANCE
                <= abundance
                <= target_isotope_config.MAX_RELATIVE_ABUNDANCE
            ):
                raise ValueError(
                    f"Relative abundance must be between {target_isotope_config.MIN_RELATIVE_ABUNDANCE} "
                    f"and {target_isotope_config.MAX_RELATIVE_ABUNDANCE}."
                )
        return abundance
