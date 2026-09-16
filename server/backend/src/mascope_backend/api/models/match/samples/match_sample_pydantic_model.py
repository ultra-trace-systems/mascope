from typing import Literal, Optional

from pydantic import BaseModel, Field

from mascope_backend.api.models.base_pydantic_model import QueryParamsModel


class MatchSampleBase(BaseModel):
    sample_item_id: str = Field(..., description="Foreign key to sample_item")
    match_score: float = Field(..., description="Score of the match")
    match_category: int = Field(..., description="Category of the match")
    sample_peak_intensity_sum: float = Field(
        ..., description="Sum of the intensity of the sample peak"
    )


# Columns `sort` accepts (see mascope_backend.api.lib.sorting).
MatchSampleSortColumn = Literal[
    "match_sample_id",
    "sample_item_id",
    "match_score",
    "match_category",
    "sample_peak_intensity_sum",
    "match_sample_utc_created",
    "match_sample_utc_modified",
]


class GetMatchSamplesQueryParams(QueryParamsModel):
    sample_item_id: Optional[str] = Field(
        None, description="Filter match samples by sample item ID"
    )
    sample_batch_id: Optional[str] = Field(
        None, description="The ID of the sample batch to filter match samples by."
    )
    match_category_min: Optional[int] = Field(
        None,
        description="Filter match samples by match_category to include match samples with specified match category and higher",
    )
    sort: MatchSampleSortColumn | None = Field(
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
