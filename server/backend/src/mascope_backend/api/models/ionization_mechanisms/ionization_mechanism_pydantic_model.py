"""
Ionization mechanism pydantic models for API validation and serialization.

Defines data models for ionization mechanism related requests and responses
with validation rules and business logic constraints.
"""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mascope_backend.api.models.base_pydantic_model import QueryParamsModel
from mascope_backend.api.models.ionization_mechanisms.config import (
    ionization_mechanism_config,
)
from mascope_tools.composition.utils import assert_valid_formula


class IonizationMechanismBaseValidator:
    """Base validation logic for ionization mechanism shared fields."""

    @field_validator("ionization_mechanism")
    @classmethod
    def validate_ionization_mechanism(cls, value: str) -> str:
        """Validate ionization mechanism format and structure."""
        if not value.strip():
            raise ValueError("ionization_mechanism cannot be empty or just whitespace.")

        # Check if it starts with + or -
        if not re.match(r"^[\+\-]", value):
            raise ValueError(
                "The ionization mechanism must start with '+' (addition) or '-' (abstraction)."
            )

        # Check if it ends with + or -
        if not re.match(r".*[\+\-]$", value):
            raise ValueError(
                "The ionization mechanism must end with '+' or '-' to indicate the ion charge."
            )

        # Prevent invalid sequences like "+-" or "-+"
        if "+-" in value or "-+" in value:
            raise ValueError(
                "Invalid ionization mechanism: it cannot contain a combination of '+' and '-' in the middle."
            )

        # A multi-character mechanism must have a modification formula between
        # the operation and charge signs: "++" / "--" denote an empty
        # modification, which produces atomless ions downstream. Electron
        # transfer is written as plain "+" or "-".
        if len(value) > 1 and not value[1:-1]:
            raise ValueError(
                f"Invalid ionization mechanism '{value}': missing modification "
                "formula; use '+' or '-' alone for electron transfer."
            )

        # Validate the modification formula (the mechanism body, without the
        # leading operation and trailing charge sign). Raises on invalid
        # characters or unknown elements. Electron transfer ("+"/"-") has no body.
        formula_body = value[1:-1] if len(value) > 1 else ""
        try:
            assert_valid_formula(formula_body)
        except ValueError as e:
            raise ValueError(
                f"Invalid ionization mechanism formula '{value}': {str(e)}"
            ) from e

        return value

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
        """Validate ionization mechanism polarity matches ending charge."""
        polarity = self.ionization_mechanism_polarity
        ionization_mechanism = self.ionization_mechanism

        # Match the polarity with the final ion charge
        if len(ionization_mechanism) == 1:
            # Electron addition/abstraction case
            if ionization_mechanism != polarity:
                raise ValueError(
                    f"Ionization mechanism {ionization_mechanism}: polarity {polarity} is inconsistent with the mechanism."
                )
            return self

        if polarity == "+" and not (
            ionization_mechanism[0] == ionization_mechanism[-1]
        ):
            raise ValueError(
                f"Ionization mechanism {ionization_mechanism}: polarity {polarity} is inconsistent with the mechanism."
            )
        if polarity == "-" and not (
            ionization_mechanism[0] != ionization_mechanism[-1]
        ):
            raise ValueError(
                f"Ionization mechanism {ionization_mechanism}: polarity {polarity} is inconsistent with the mechanism."
            )

        return self


class IonizationMechanismFields(BaseModel):
    """The fields every IonizationMechanism schema carries, without validation."""

    ionization_mechanism_polarity: str = Field(
        ..., description="Polarity of the ionization mechanism ('+' or '-')"
    )
    ionization_mechanism: str = Field(
        ...,
        description="Chemical formula modification (addition/abstraction) representing the ionized form.",
    )

    model_config = ConfigDict(from_attributes=True)


class IonizationMechanismBase(
    IonizationMechanismBaseValidator, IonizationMechanismFields
):
    """Base model with common fields for IonizationMechanism schemas."""


class IonizationMechanismCreate(IonizationMechanismBase):
    """Model used for ionization mechanism creation requests."""

    @model_validator(mode="before")
    @classmethod
    def auto_derive_fields(cls, values):
        """Auto-derive polarity field."""
        mechanism = values.get("ionization_mechanism")
        polarity = values.get("ionization_mechanism_polarity")

        # Auto-derive polarity from the last character if not provided
        if polarity is None:
            if len(mechanism) > 1:
                if mechanism[0] == "+":
                    polarity = mechanism[-1]
                elif mechanism[0] == "-":
                    # Reverse polarity for abstraction
                    if mechanism[-1] == "+":
                        polarity = "-"
                    elif mechanism[-1] == "-":
                        polarity = "+"
                    else:
                        raise ValueError(
                            f"Invalid ionization mechanism {mechanism}: must end with '+' or '-'"
                        )
                else:
                    raise ValueError(
                        f"Invalid ionization mechanism {mechanism}: must start with '+' or '-'"
                    )
            else:
                # Electron addition/abstraction case
                polarity = mechanism[0]

            values["ionization_mechanism_polarity"] = polarity

        return values


class IonizationMechanismRead(IonizationMechanismFields):
    """
    Model used for reading ionization mechanisms, includes database fields.

    Not validated: a stored row is reported as it is. The create and update
    validators have tightened over time and nothing rewrites existing rows to
    match, so re-running them here would turn one row written under older
    rules into a 400 for the whole listing - including for the frontend,
    which loads it. What may be written is enforced where it is written.
    """

    ionization_mechanism_id: str = Field(
        ..., description="Unique identifier for the ionization mechanism"
    )


class IonizationMechanismUpdate(IonizationMechanismBaseValidator, BaseModel):
    """Model used for ionization mechanism update requests - only user-editable fields."""

    ionization_mechanism_polarity: str | None = Field(
        None, description="Polarity of the ionization mechanism ('+' or '-')"
    )
    ionization_mechanism: str | None = Field(
        None, description="Chemical formula modification representing the ionized form."
    )

    model_config = ConfigDict(from_attributes=True)


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
