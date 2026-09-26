"""
Ionization mechanism pydantic models for API validation and serialization.

Defines data models for ionization mechanism related requests and responses
with validation rules and business logic constraints.
"""

from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

from mascope_backend.api.models.base_pydantic_model import QueryParamsModel
from mascope_backend.api.models.ionization_mechanisms.config import (
    ionization_mechanism_config,
)
from mascope_backend.ionization_catalogue import is_shipped_mechanism
from mascope_tools.composition.mechanism_notation import (
    MechanismNotationError,
    parse_mechanism,
)
from mascope_tools.composition.utils import assert_valid_formula, parse_composition


class IonizationMechanismBaseValidator:
    """Base validation logic for ionization mechanism shared fields."""

    @field_validator("ionization_mechanism")
    @classmethod
    def validate_ionization_mechanism(cls, value: str) -> str:
        """Validate a mechanism and answer it in the standard adduct notation.

        Either notation is accepted - ``[M-H]-`` or its legacy spelling ``-H+``
        - and the standard one is what is stored (see
        :mod:`mascope_tools.composition.mechanism_notation`). Each term must be
        a formula of real elements: an unknown element or a stray character is
        refused rather than skipped.
        """
        if not value.strip():
            raise ValueError("ionization_mechanism cannot be empty or just whitespace.")
        try:
            parts = parse_mechanism(value)
        except MechanismNotationError as e:
            raise ValueError(str(e)) from e

        for term in parts.terms:
            try:
                assert_valid_formula(term)
            except ValueError as e:
                raise ValueError(
                    f"Invalid ionization mechanism formula '{value}': {str(e)}"
                ) from e
            if not parse_composition(term):
                # "()" passes as a formula, and a mechanism that adds nothing
                # makes an atomless ion.
                raise ValueError(
                    f"Invalid ionization mechanism '{value}': the term '{term}' "
                    "holds no atoms."
                )

        return parts.standard

    @field_validator("ionization_mechanism_polarity")
    @classmethod
    def validate_polarity(cls, value: str) -> str:
        """Validate polarity is '+' or '-'."""
        allowed_polarities = ionization_mechanism_config.IONIZATION_MECHANISM_POLARITY
        if value not in allowed_polarities:
            raise ValueError(
                f"Invalid polarity '{value}'. Must be one of {allowed_polarities}."
            )
        return value

    @model_validator(mode="after")
    def validate_ionization_mechanism_and_polarity(self):
        """Validate the polarity is the charge of the ion the mechanism makes."""
        polarity = self.ionization_mechanism_polarity
        ionization_mechanism = self.ionization_mechanism
        if parse_mechanism(ionization_mechanism).polarity != polarity:
            raise ValueError(
                f"Ionization mechanism {ionization_mechanism}: polarity {polarity} is inconsistent with the mechanism."
            )
        return self


class IonizationMechanismBase(BaseModel):
    """
    Base model with common fields for IonizationMechanism schemas.

    Fields only: the write models mix the validators in, and a response model
    built on this reports a stored row as it is (see IonizationMechanismRead).
    """

    ionization_mechanism_polarity: str = Field(
        ..., description="Polarity of the ionization mechanism ('+' or '-')"
    )
    ionization_mechanism: str = Field(
        ...,
        description=(
            "The ionization mechanism in the standard adduct notation: '[M+H]+', "
            "'[M-H]-', '[M+Br]-', '[M]+.' for electron transfer. The legacy "
            "spelling ('+H+', '-H+', '+Br-', '+') is accepted on input and "
            "stored in the standard one."
        ),
    )

    model_config = ConfigDict(from_attributes=True)


class IonizationMechanismCreate(
    IonizationMechanismBaseValidator, IonizationMechanismBase
):
    """Model used for ionization mechanism creation requests."""

    @model_validator(mode="before")
    @classmethod
    def auto_derive_fields(cls, values):
        """
        Auto-derive polarity field.

        Runs before pydantic has checked anything, so a body that is not an
        object, or whose mechanism is missing or not a string, is left to the
        field validation below rather than indexed into here - indexing it
        raises TypeError, which is a 500 rather than the 422 a malformed
        request deserves.
        """
        if not isinstance(values, dict):
            return values
        mechanism = values.get("ionization_mechanism")
        polarity = values.get("ionization_mechanism_polarity")
        if not isinstance(mechanism, str) or not mechanism.strip():
            return values

        # The polarity is the charge of the ion the mechanism makes: the
        # trailing sign of "[M-H]-", the reverse of the trailing one of "-H+".
        if polarity is None:
            try:
                values["ionization_mechanism_polarity"] = parse_mechanism(
                    mechanism
                ).polarity
            except MechanismNotationError as e:
                raise ValueError(str(e)) from e

        return values


class IonizationMechanismRead(IonizationMechanismBase):
    """
    Model used for reading ionization mechanisms, includes database fields.

    Not validated: a stored row is reported as it is, in the standard adduct
    notation where it reads as a mechanism in either (the column type reads it
    so). The create validators have tightened over time and nothing rewrites
    existing rows to match, so re-running them here would turn one row written
    under older rules into a 400 for the whole listing - including for the
    frontend, which loads it. What may be written is enforced where it is
    written.
    """

    ionization_mechanism_id: str = Field(
        ..., description="Unique identifier for the ionization mechanism"
    )

    @computed_field(
        description=(
            "Whether Mascope ships this mechanism. A shipped mechanism is on "
            "every server from its first start and cannot be deleted."
        )
    )
    @property
    def shipped(self) -> bool:
        """Whether this is a mechanism Mascope ships (``is_shipped_mechanism``)."""
        return is_shipped_mechanism(
            self.ionization_mechanism, self.ionization_mechanism_polarity
        )


# Columns `sort` accepts (see mascope_backend.api.lib.sorting).
IonizationMechanismSortColumn = Literal[
    "ionization_mechanism_id",
    "ionization_mechanism_polarity",
    "ionization_mechanism",
]


class GetIonizationMechanismsQueryParams(QueryParamsModel):
    """Query parameters for filtering and paginating ionization mechanism listings."""

    ionization_mechanism_polarity: str | None = Field(
        None,
        description="Filter by the polarity of the ionization mechanism ('+' or '-')",
    )
    ionization_mechanism: list[str] | None = Field(
        None,
        description="Filter by the chemical formula modification of the ionization mechanism. Can specify multiple values.",
    )

    sort: IonizationMechanismSortColumn | None = Field(
        "ionization_mechanism", description="Field to sort by"
    )
    order: str | None = Field(
        "asc",
        description="Order of sorting ('asc' for ascending, 'desc' for descending)",
    )
    page: int | None = Field(None, description="Pagination page number")
    limit: int | None = Field(None, description="Number of items per page")

    @field_validator("ionization_mechanism_polarity")
    @classmethod
    def validate_polarity_filter(cls, value: str | None) -> str | None:
        """Validate polarity filter values."""
        allowed_polarities = ionization_mechanism_config.IONIZATION_MECHANISM_POLARITY
        if value is not None and value not in allowed_polarities:
            raise ValueError(f"Polarity filter must be one of {allowed_polarities}")
        return value
