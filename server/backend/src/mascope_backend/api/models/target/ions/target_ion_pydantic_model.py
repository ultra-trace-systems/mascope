from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from mascope_backend.api.models.base_pydantic_model import QueryParamsModel
from mascope_match.params import BaseMatchParams


class TargetIonUpdate(BaseModel):
    match_params: dict[str, BaseMatchParams] | None = Field(
        None, description="Ion-specific match parameters"
    )
    delete_instrument_params: str | None = Field(
        None, description="Instrument name which match parameteres to delete"
    )

    model_config = ConfigDict(from_attributes=True)


# Columns `sort` accepts (see mascope_backend.api.lib.sorting).
TargetIonSortColumn = Literal[
    "target_ion_id",
    "target_compound_id",
    "ionization_mechanism_id",
    "target_ion_formula",
]


class GetTargetIonsQueryParams(QueryParamsModel):
    target_compound_id: str | None = Field(
        None, description="Filter by target compound ID."
    )
    ionization_mechanism_id: str | None = Field(
        None, description="Filter by ionization mechanism ID."
    )
    target_ion_formula: str | None = Field(
        None, description="Filter by target ion formula."
    )
    sample_batch_id: str | None = Field(
        None, description="The ID of the sample batch to filter ions by."
    )
    target_collection_id: str | None = Field(
        None, description="The ID of the target collection to filter ions by."
    )
    show_target_collection: bool = Field(
        False,
        description="Flag to include target collection ID, also duplicate compounds present in several collections will be shown.",
    )
    show_ionization_mechanism: bool = Field(
        False,
        description="Flag to to include ionization mechanism details.",
    )
    sort: TargetIonSortColumn | None = Field(None, description="Field to sort by.")
    order: str | None = Field(None, description="Order of sorting ('asc' or 'desc').")
    page: int | None = Field(None, description="Pagination page.")
    limit: int | None = Field(None, description="Number of items per page.")
