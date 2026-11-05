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
    to_row,
)
from mascope_tools.composition.utils import (
    assert_valid_formula,
    mass_shift,
    parse_composition,
)


#: The mechanism field's description, on the model a request is read into and
#: on the one a stored row is reported through.
_MECHANISM_DESCRIPTION = (
    "The ionization mechanism in the standard adduct notation: '[M+H]+', "
    "'[M-H]-', '[M+Br]-', '[M]+.' for electron transfer, '[2M+H]+' for a "
    "dimer, '[M+2H]2+' for a doubly charged ion, '[M+H-H2O]+' for a neutral "
    "loss. The legacy spelling ('+H+', '-H+', '+Br-', '+') is accepted on "
    "input and stored in the standard one."
)

#: The longest name a mechanism may be given: the column's.
_NAME_MAX_LENGTH = 256


class IonizationMechanismBaseValidator:
    """Base validation logic for ionization mechanism shared fields."""

    @field_validator("ionization_mechanism")
    @classmethod
    def validate_ionization_mechanism(cls, value: str) -> str:
        """Validate a mechanism and answer it in the standard adduct notation.

        Either notation is accepted - ``[M-H]-`` or its legacy spelling ``-H+``
        - and the standard one is what is stored (see
        :mod:`mascope_tools.composition.mechanism_notation`). Each term, the
        neutral loss's included, must be a formula of real elements: an
        unknown element or a stray character is refused rather than skipped.

        The field's ``max_length`` refuses an over-long value before this runs,
        so the parse never reads one. That bounds the text as sent; the
        standard spelling has to fit the column too, and it can be the longer
        of the two (``+H+`` is stored as ``[M+H]+``).
        """
        if not value.strip():
            raise ValueError("ionization_mechanism cannot be empty or just whitespace.")
        try:
            parts = parse_mechanism(value)
        except MechanismNotationError as e:
            raise ValueError(str(e)) from e

        limit = ionization_mechanism_config.IONIZATION_MECHANISM_MAX_LENGTH
        if len(parts.standard) > limit:
            raise ValueError(
                f"Ionization mechanism '{value}' is {len(parts.standard)} "
                f"characters in the standard notation it is stored in; at most "
                f"{limit} fit."
            )

        for term in parts.formulas:
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
    ionization_mechanism: str = Field(..., description=_MECHANISM_DESCRIPTION)
    name: str | None = Field(
        None,
        description="What people call the mechanism, 'protonation'; not part of it.",
    )

    model_config = ConfigDict(from_attributes=True)


class IonizationMechanismCreate(
    IonizationMechanismBaseValidator, IonizationMechanismBase
):
    """Model used for ionization mechanism creation requests."""

    # Bounded here rather than on the base, which the read model shares: that
    # reports a stored row as it is, and a legacy row reads in the standard
    # spelling, which can be longer than the column.
    ionization_mechanism: str = Field(
        ...,
        max_length=ionization_mechanism_config.IONIZATION_MECHANISM_MAX_LENGTH,
        description=(
            f"{_MECHANISM_DESCRIPTION} At most "
            f"{ionization_mechanism_config.IONIZATION_MECHANISM_MAX_LENGTH} "
            "characters, both as sent and in the standard notation."
        ),
    )

    name: str | None = Field(
        None,
        max_length=_NAME_MAX_LENGTH,
        description=(
            "What people call the mechanism, 'protonation'; not part of it. "
            f"At most {_NAME_MAX_LENGTH} characters; blank is no name."
        ),
    )

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
        """A name as it is shown: stripped, and none where it is blank."""
        if value is None:
            return None
        return value.strip() or None

    @model_validator(mode="before")
    @classmethod
    def auto_derive_fields(cls, values):
        """
        Auto-derive polarity field.

        Runs before pydantic has checked anything, so a body that is not an
        object, or whose mechanism is missing or not a string, is left to the
        field validation below rather than indexed into here - indexing it
        raises TypeError, which is a 500 rather than the 422 a malformed
        request deserves. A mechanism longer than the field allows is left to
        it unparsed, for the field's bound to refuse: parsing takes time in
        proportion to the text, and the parser's cache keeps each value it
        accepts.
        """
        if not isinstance(values, dict):
            return values
        mechanism = values.get("ionization_mechanism")
        polarity = values.get("ionization_mechanism_polarity")
        limit = ionization_mechanism_config.IONIZATION_MECHANISM_MAX_LENGTH
        if (
            not isinstance(mechanism, str)
            or len(mechanism) > limit
            or not mechanism.strip()
        ):
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
    operation: str | None = Field(
        None,
        description=(
            "The mechanism as a row: 'add' or 'remove', what it does with its "
            "species. Written from the mechanism, as are the row's other "
            "fields; null on a stored row that reads as neither notation."
        ),
    )
    species: str | None = Field(
        None,
        description=(
            "The species added or removed, a formula: 'H', '(CH4N2O)H' for "
            "two terms, '(H)2' for the two protons of '[M+2H]2+'; empty for "
            "the electron of '[M]+.'."
        ),
    )
    species_charge: int | None = Field(
        None,
        description=(
            "The charge the species carries, all of it: 1 for a proton, -1 "
            "for bromide or the electron, 2 for '[M+2H]2+'."
        ),
    )
    multimer_n: int = Field(
        1, description="How many molecules the ion holds: 2 for '[2M+H]+'."
    )
    neutral_loss: str | None = Field(
        None,
        description="The neutral the ion loses, a formula: 'H2O' for '[M+H-H2O]+'.",
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

    @computed_field(
        description=(
            "The charge of the ion the mechanism makes, derived from it: 1 "
            "for '[M+H]+', -1 for '[M-H]-', 2 for '[M+2H]2+'. Null for a "
            "stored row that reads as neither notation."
        )
    )
    @property
    def ion_charge(self) -> int | None:
        """The ion's charge, read from the mechanism."""
        try:
            return parse_mechanism(self.ionization_mechanism).charge
        except MechanismNotationError:
            return None

    @computed_field(
        description=(
            "What the mechanism adds to the mass of its molecules, in Da, "
            "derived from it: the ion of n molecules M has the m/z "
            "(n * M + mass_shift) / |ion_charge|. Null for a stored row that "
            "reads as neither notation."
        )
    )
    @property
    def mass_shift(self) -> float | None:
        """The mass shift, read from the mechanism's row."""
        try:
            return mass_shift(to_row(self.ionization_mechanism))
        except Exception:  # noqa: BLE001 - a row older rules let through has no mass
            return None


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
