from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from mascope_backend.api.models.base_pydantic_model import QueryParamsModel
from mascope_backend.api.models.match.match_pydantic_model import (
    FilterSamplePayload,
)


class MatchIonBase(BaseModel):
    sample_item_id: str = Field(..., description="Foreign key to sample_item")
    target_ion_id: str = Field(..., description="Foreign key to target_ion")
    match_score: float = Field(..., description="Score of the match ion")
    match_category: int = Field(..., description="Category of the match")
    sample_peak_intensity_sum: float = Field(
        ..., description="Intensity of the sample peak"
    )


# Columns `sort` accepts (see mascope_backend.api.lib.sorting).
MatchIonSortColumn = Literal[
    "match_ion_id",
    "sample_item_id",
    "target_ion_id",
    "match_score",
    "match_category",
    "sample_peak_intensity_sum",
    "match_ion_utc_created",
    "match_ion_utc_modified",
]


class GetMatchIonsQueryParams(QueryParamsModel):
    sample_item_id: Optional[str] = Field(
        None, description="Filter match ions by sample item ID"
    )
    sample_batch_id: Optional[str] = Field(
        None, description="The ID of the sample batch to filter match ions by."
    )
    target_ion_id: Optional[str] = Field(
        None, description="Filter match ions by target ion ID"
    )
    ionization_mechanism_id: Optional[str] = Field(
        None, description="Filter match ions by ionization mechanism ID"
    )
    match_category_min: Optional[int] = Field(
        None,
        description="Filter match ions by match_category to include match ions with specified match category and higher",
    )
    deduplicate: Optional[bool] = Field(
        False,
        description="Drop the potential duplicate ions (parent compounds added to several target collections) when show_target_collection. The target collection info is preserved based on collection type priority 'TARGETS' > 'DIAGNOSTICS' > 'CALIBRANTS'",
    )
    show_target_collection: bool = Field(
        False,
        description="Flag to include target collection ID, also duplicate compounds present in several collections will be shown.",
    )
    show_target_compound: bool = Field(
        False,
        description="Flag to include target compound details.",
    )
    show_target_ion: bool = Field(
        False,
        description="Flag to include target ion details.",
    )
    show_ionization_mechanism: bool = Field(
        False,
        description="Flag to to include ionization mechanism details.",
    )
    sort: MatchIonSortColumn | None = Field(
        None, description="The column name to sort the results by."
    )
    order: Optional[str] = Field(
        None,
        description="The sort order, either 'asc' for ascending or 'desc' for descending.",
    )
    page: int | None = Field(
        None,
        description="The page number for pagination, optional. None for no pagination.",
    )
    limit: int | None = Field(
        None,
        description="The number of results per page, optional. None for no pagination.",
    )


class DeleteMatchIonsPayload(FilterSamplePayload):
    target_ion_ids: Optional[List[str]] = Field(
        None,
        description="Optional list of target ion IDs to limit the match ions being deleted.",
    )
