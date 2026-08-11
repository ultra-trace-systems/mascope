from pydantic import BaseModel, Field

from mascope_backend.api.new.cheminfo.config import cheminfo_config
from mascope_match.params import BaseMatchParams


class CheminfoQueryBody(BaseModel):
    mz: float = Field(..., description="The m/z value to search compositions for")
    mz_precision: float = Field(
        cheminfo_config.DEFAULT_MZ_PRECISION,
        description="The precision (tolerance in ppm) for m/z matching, i.e. the query returns matches between m/z +/- m/z precision",
    )
    formula_ranges: str = Field(
        cheminfo_config.DEFAULT_FORMULA_RANGE,
        description="The formula range to query, defaults to 'C0-100 H0-100 O0-100 N0-100'",
    )
    ionization_mechanism_ids: list[str] = Field(
        ..., description="The ionization mechanism IDs to query against"
    )
    known_only: bool = Field(
        False,
        description=(
            "When true, keep only compositions whose formula matches a known "
            "reference compound (the suspect-screening prior). Defaults to false."
        ),
    )


class CheminfoMatchedQueryBody(CheminfoQueryBody):
    match_params: BaseMatchParams | None = Field(
        None, description="Match parameters to use"
    )
