from datetime import datetime as dt
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from mascope_backend.api.models.base_pydantic_model import QueryParamsModel


class IsotopeRating(BaseModel):
    isotope_rating: int = Field(
        ..., ge=1, le=5, description="Match isotope rating from 1 to 5"
    )
    target_isotope_id: str = Field(
        ..., description="ID of the associated target isotope"
    )


class MatchRatingChecklist(BaseModel):
    isotopes_rating: List[IsotopeRating] = Field(
        ..., description="List of match isotopes with data and rating"
    )
    timeseries_good_match: bool = Field(
        ..., description="Do the timeseries indicate a good match between the isotopes"
    )
    timeseries_expected_behavior: int = Field(
        ...,
        ge=0,
        le=2,
        description="Do the timeseries indicate expected behavior? Where 0 - 'No', 1 - 'Maybe', 2 - 'Yes'",
    )
    comment: Optional[str] = Field(None, description="Optional comment")


class Environment(BaseModel):
    mz_calibration: Dict = Field(..., description="m/z calibration data of the sample")


class MatchRatingBase(BaseModel):
    sample_item_id: str = Field(..., description="ID of the associated sample item")
    target_ion_id: str = Field(..., description="ID of the associated target ion")
    rating: int = Field(..., ge=0, le=2, description="Rating value between 0 and 2")
    checklist: Optional[MatchRatingChecklist] = Field(
        None, description="Checklist for the match rating"
    )
    environment: Environment = Field(..., description="Environment-related data")


class MatchRatingCreate(MatchRatingBase):
    pass


class MatchRatingUpdate(MatchRatingBase):
    pass


# Columns `sort` accepts (see mascope_backend.api.lib.sorting).
MatchRatingSortColumn = Literal[
    "match_rating_id",
    "sample_item_id",
    "target_ion_id",
    "match_rating_utc_created",
    "rating",
]


class GetMatchRatingsQueryParams(QueryParamsModel):
    sample_item_id: Optional[str] = Field(None, description="ID of the sample item")
    target_ion_id: Optional[str] = Field(None, description="ID of the target ion")
    rating: Optional[int] = Field(None, description="Rating value between 0 and 2")
    sort: MatchRatingSortColumn | None = Field(None, description="Field to sort by")
    order: Optional[str] = Field(None, description="Order of sorting ('asc' or 'desc')")
    page: int | None = Field(None, description="Page number for pagination")
    limit: int | None = Field(None, description="Number of items per page")
    datetime_min: Optional[dt] = Field(
        None, description="Minimum datetime of creation filter"
    )
    datetime_max: Optional[dt] = Field(
        None, description="Maximum datetime of creation filter"
    )
