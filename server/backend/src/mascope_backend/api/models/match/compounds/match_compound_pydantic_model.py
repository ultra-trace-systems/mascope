from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from mascope_backend.api.models.base_pydantic_model import QueryParamsModel
from mascope_backend.api.models.match.match_pydantic_model import (
    FilterSamplePayload,
)


class MatchCompoundBase(BaseModel):
    sample_item_id: str = Field(..., description="Foreign key to sample_item")
    target_compound_id: str = Field(..., description="Foreign key to target_compound")
    match_score: float = Field(..., description="Score of the match")
    match_category: int = Field(..., description="Category of the match")
    sample_peak_intensity_sum: float = Field(
        ..., description="Sum of the intensity of the sample peak"
    )


# Columns `sort` accepts (see mascope_backend.api.lib.sorting).
MATCH_COMPOUND_SORT_COLUMNS = (
    "match_compound_id",
    "sample_item_id",
    "target_compound_id",
    "match_score",
    "match_category",
    "sample_peak_intensity_sum",
    "match_compound_utc_created",
    "match_compound_utc_modified",
)


class GetMatchCompoundsQueryParams(QueryParamsModel):
    sample_item_id: Optional[str] = Field(
        None, description="Filter compounds by sample item ID"
    )
    sample_batch_id: Optional[str] = Field(
        None, description="The ID of the sample batch to filter compounds by."
    )
    target_compound_id: Optional[str] = Field(
        None, description="Filter compounds by target compound ID"
    )
    match_category_min: Optional[int] = Field(
        None,
        description="Filter match compounds by match_category to include match compounds with specified match category and higher",
    )
    deduplicate: Optional[bool] = Field(
        False,
        description="Drop the potential duplicate compounds (added to several target collections) when show_target_collection. The target collection info is preserved based on collection type priority 'TARGETS' > 'DIAGNOSTICS' > 'CALIBRANTS'",
    )
    show_target_collection: bool = Field(
        False,
        description="Flag to include target collection ID, also duplicate compounds present in several collections will be shown.",
    )
    show_target_compound: bool = Field(
        False,
        description="Flag to include target compound name, used for BatchOverview.",
    )
    sort: Literal[MATCH_COMPOUND_SORT_COLUMNS] | None = Field(
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


class DeleteMatchCompoundsPayload(FilterSamplePayload):
    target_compound_ids: Optional[List[str]] = Field(
        None,
        description="Optional list of target compound IDs to limit the match compounds being deleted.",
    )
