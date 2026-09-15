from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from mascope_backend.api.models.base_pydantic_model import QueryParamsModel
from mascope_backend.api.models.match.match_pydantic_model import (
    FilterSamplePayload,
)


class MatchCollectionBase(BaseModel):
    sample_item_id: str = Field(..., description="Foreign key to sample_item")
    target_collection_id: str = Field(
        ..., description="Foreign key to target_collection"
    )
    match_score: float = Field(..., description="Score of the match")
    match_category: int = Field(..., description="Category of the match")
    sample_peak_intensity_sum: float = Field(
        ..., description="Sum of the intensity of the sample peak"
    )


# Columns `sort` accepts (see mascope_backend.api.lib.sorting).
MATCH_COLLECTION_SORT_COLUMNS = (
    "match_collection_id",
    "sample_item_id",
    "target_collection_id",
    "match_score",
    "match_category",
    "sample_peak_intensity_sum",
    "match_collection_utc_created",
    "match_collection_utc_modified",
)


class GetMatchCollectionsQueryParams(QueryParamsModel):
    sample_item_id: Optional[str] = Field(
        None, description="Filter collections by sample item ID"
    )
    sample_batch_id: Optional[str] = Field(
        None, description="The ID of the sample batch to filter collections by."
    )
    target_collection_id: Optional[str] = Field(
        None, description="Filter collections by target collection ID"
    )
    match_category_min: Optional[int] = Field(
        None,
        description="Filter match collections by match_category to include match collections with specified match category and higher",
    )
    sort: Literal[MATCH_COLLECTION_SORT_COLUMNS] | None = Field(
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


class DeleteMatchCollectionsPayload(FilterSamplePayload):
    target_collections_ids: Optional[List[str]] = Field(
        None,
        description="Optional list of target collection IDs to limit the match collections being deleted.",
    )
