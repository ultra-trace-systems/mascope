import re
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from mascope_backend.api.models.base_pydantic_model import QueryParamsModel
from mascope_tools.composition.utils import assert_valid_formula


# A bare numeric mass such as "136.1252", "60", "1e3". Matched explicitly rather
# than via float(), which also parses "NaN"/"inf"/"Infinity" and would
# misclassify the chemically valid formula "NaN" (sodium nitride) as a mass.
_NUMERIC_MASS = re.compile(r"[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?")

# Bracket isotope notation in either accepted spelling: '[15N]O3' and 'N[15]O3'.
# Caret isotopes ('^N') are deliberately NOT matched - see the docstring below.
_BRACKET_ISOTOPE = re.compile(r"\[\d+[A-Za-z]|[A-Za-z]\[\d+\]")


def validate_compound_formula(value: Optional[str]) -> Optional[str]:
    """Validate a target compound formula, rejecting masses and invalid formulas.

    Mass-based target compounds (a plain number such as ``"136.1252"`` instead of
    a chemical formula) are no longer supported: their ions/isotopes used to be
    generated from the mass alone, which relied on the retired molmass fork.
    Compounds must now be given by composition so an isotope pattern can be
    computed. An empty formula (``"()"``, adduct-only) is still allowed.

    Explicit bracket isotope notation (``"[13C]C5H12O6"``, ``"C[13]C5H12O6"``) is
    rejected too. Isotopes are always generated from the formula, so pinning one
    on the compound asks for a monoisotopic species where the pipeline will
    compute a full pattern anyway - the two cannot both be right.

    Caret isotopes (``"^N"`` = 15N) stay allowed: they name a *labelled reagent*
    - a genuinely different substance, such as the 15N nitrate behind the
    ``+^NO3-`` mechanism - rather than one isotopologue of an ordinary compound,
    and a natural-abundance pattern is still computed around them.

    Anything that is not a parseable chemical formula (unknown elements such as
    ``"Zz"``, unknown custom elements such as ``"^C"``, stray characters) is also
    rejected here: ion generation would silently skip such a compound, leaving a
    compound record that can never produce ions or matches.
    """
    if value is None:
        return value
    if _NUMERIC_MASS.fullmatch(value.strip()):
        raise ValueError(
            "Mass-based target compounds are no longer supported; provide a "
            "chemical formula (e.g. 'C6H12O6'), not a numeric mass."
        )
    if _BRACKET_ISOTOPE.search(value):
        raise ValueError(
            "Explicit isotope notation is not allowed for target compounds; "
            "isotopes are generated from the formula. Give the unlabelled "
            "composition (e.g. 'C6H12O6'), or use a caret isotope such as "
            "'^N' when the compound really is a labelled reagent."
        )
    assert_valid_formula(value)
    return value


class TargetCompoundBase(BaseModel):
    target_compound_id: Optional[str] = Field(
        None, description="ID of the target compound"
    )
    target_compound_name: Optional[str] = Field(
        "", description="Name of the target compound"
    )
    target_compound_formula: str = Field(
        ..., description="Formula of the target compound"
    )
    cas_number: Optional[str] = Field(
        None, description="CAS Number of the target compound"
    )

    _validate_formula = field_validator("target_compound_formula")(
        validate_compound_formula
    )


class TargetCompoundMatches(TargetCompoundBase):
    target_compound_name: Optional[str] = Field(
        "Unknown Compound", description="Name of the target compound"
    )


class TargetCompoundUpdate(BaseModel):
    target_compound_id: str = Field(..., description="ID of the target compound")
    target_collection_id: Optional[str] = Field(
        None, description="ID of the target collection"
    )
    target_compound_name: Optional[str] = Field(
        "", description="Name of the target compound"
    )
    target_compound_formula: Optional[str] = Field(
        None, description="Formula of the target compound"
    )
    cas_number: Optional[str] = Field(
        None, description="CAS Number of the target compound"
    )

    _validate_formula = field_validator("target_compound_formula")(
        validate_compound_formula
    )


class GetTargetCompoundsQueryParams(QueryParamsModel):
    target_compound_name: Optional[str] = Field(
        None, description="The name of the target compound to filter by."
    )
    target_compound_formula: Optional[str] = Field(
        None, description="The formula of the target compound to filter by."
    )
    sample_batch_id: Optional[str] = Field(
        None, description="The ID of the sample batch to filter compounds by."
    )
    target_collection_id: Optional[str] = Field(
        None, description="The ID of the target collection to filter compounds by."
    )
    show_target_collection: bool = Field(
        False,
        description="Flag to include target collection ID, also duplicate compounds present in several collections will be shown.",
    )
    sort: Optional[str] = Field(
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


class GetTargetCompoundInTargetCollectionQueryParams(QueryParamsModel):
    target_compound_id: Optional[str] = Field(
        None,
        description="The target compound ID filter for which you want to fetch the assosiated target collections ids.",
    )
    target_collection_id: Optional[str] = Field(
        None,
        description="The target collection ID filter for which you want to fetch the assosiated target compound ids.",
    )
    sort: Optional[str] = Field(
        None,
        description="The column name by which you want to sort the results. The column name should be either target_compound_id or target_collection_id.",
    )
    order: Optional[str] = Field(
        None,
        description="Can either be asc for ascending order or desc for descending order.",
    )
    page: int | None = Field(
        None,
        description="The page number for pagination, optional. None for no pagination.",
    )
    limit: int | None = Field(
        None,
        description="The number of results per page, optional. None for no pagination.",
    )
