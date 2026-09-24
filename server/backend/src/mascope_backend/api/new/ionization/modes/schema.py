from pydantic import BaseModel, ConfigDict, Field, field_validator

from mascope_backend.api.models.base_pydantic_model import QueryParamsModel


class IonizationModeBaseValidator:
    @field_validator("ionization_mode_polarity")
    @classmethod
    def validate_polarity(cls, value: str):
        if value not in ["+", "-"]:
            raise ValueError('Polarity must be either "+" or "-"')
        return value


class IonizationModeTokenValidator:
    @field_validator("ionization_mode_token")
    @classmethod
    def validate_token(cls, value: str | None):
        """Strip trailing and leading whitespace and convert empty strings to None."""
        value = value.strip() if value else value
        if value == "":
            return None
        return value


class IonizationModeBase(
    IonizationModeBaseValidator, IonizationModeTokenValidator, BaseModel
):
    """Base model for ionization mode with common fields."""

    ionization_mode_name: str = Field(
        ..., max_length=256, description="Friendly, unique name of the ionization mode"
    )
    ionization_mode_token: str | None = Field(
        None,
        max_length=256,
        description="Unique filename token for the ionization mode",
    )
    ionization_mode_polarity: str = Field(
        ..., max_length=1, description="Polarity of the ionization mode (+ or -)"
    )
    ionization_mechanism_ids: list[str] = Field(
        ...,
        min_length=1,
        description="List of ionization mechanism IDs to apply for the scheme",
    )
    calibration_collection_id: str | None = Field(
        None,
        max_length=16,
        description="ID of the calibration collection to use for the scheme",
    )
    diagnostic_collection_id: str | None = Field(
        None,
        max_length=16,
        description="ID of the diagnostic collection to use for the scheme",
    )

    model_config = ConfigDict(from_attributes=True)


class IonizationModeCreate(IonizationModeBase):
    """Model for creating a new ionization mode."""

    pass


class IonizationModeUpdate(
    IonizationModeBaseValidator, IonizationModeTokenValidator, BaseModel
):
    """Model for updating an existing ionization mode."""

    ionization_mode_name: str = Field(
        ..., max_length=256, description="Friendly, unique name of the ionization mode"
    )
    ionization_mode_token: str | None = Field(
        None,
        max_length=256,
        description="Unique filename token for the ionization mode",
    )
    ionization_mode_polarity: str = Field(
        ..., max_length=1, description="Polarity of the ionization mode (+ or -)"
    )
    ionization_mechanism_ids: list[str] = Field(
        ...,
        min_length=1,
        description="List of ionization mechanism IDs to apply for the scheme",
    )
    calibration_collection_id: str | None = Field(
        None,
        max_length=16,
        description=(
            "ID of the calibration collection to use for the scheme. "
            "When updating, the collection may be changed to another one, but "
            "not cleared (un-set to null). Changing it flags affected batches "
            "for re-calibration."
        ),
    )
    diagnostic_collection_id: str | None = Field(
        None,
        max_length=16,
        description=(
            "ID of the diagnostic collection to use for the scheme. "
            "When updating, the collection may be changed to another one, but "
            "not cleared (un-set to null). Changing it flags affected batches "
            "for re-matching."
        ),
    )


class GetIonizationModesQueryParams(QueryParamsModel):
    """Query parameters for getting ionization modes."""

    include_system: bool = Field(
        False,
        description=(
            "Include the modes Mascope ships that this deployment has not "
            "adopted. They are left out by default: without target "
            "collections such a mode calibrates and matches nothing, so "
            "offering it would only invite a sample that cannot be processed. "
            "Ask for them to adopt one."
        ),
    )
