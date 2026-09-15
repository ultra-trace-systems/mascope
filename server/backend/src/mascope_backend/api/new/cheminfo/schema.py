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
        description=(
            "Element count ranges permitted in candidate formulas, e.g. "
            "'C0-80 H0-160 O0-50 N0-20'. Defaults to the server's configured range."
        ),
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
