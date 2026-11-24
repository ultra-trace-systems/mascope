"""Request bodies of the metadata field registry (``/api/metadata/fields``)."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from mascope_backend.api.models.base_pydantic_model import QueryParamsModel


MetadataFieldType = Literal["text", "number", "datetime", "enum", "tags"]

#: The types whose values are drawn from ``allowed_values``.
CHOICE_TYPES = frozenset({"enum", "tags"})

#: A key is what a value is stored under, in JSON and in a filter: lower case,
#: a letter first, then letters, digits and underscores.
KEY_PATTERN = r"^[a-z][a-z0-9_]*$"


def _clean_text(value: str | None) -> str | None:
    """Strip a text value, reading a blank one as no value."""
    if value is None:
        return None
    value = value.strip()
    return value or None


def _clean_allowed_values(values: list[str] | None) -> list[str] | None:
    """Strip the allowed values and refuse a blank or repeated one."""
    if values is None:
        return None
    cleaned = [value.strip() for value in values]
    if any(not value for value in cleaned):
        raise ValueError("An allowed value may not be blank.")
    if any(len(value) > 128 for value in cleaned):
        raise ValueError("An allowed value is at most 128 characters.")
    if len(set(cleaned)) != len(cleaned):
        raise ValueError("Each allowed value may be listed only once.")
    return cleaned


class _FieldText(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("unit", "description", mode="after", check_fields=False)
    @classmethod
    def _strip(cls, value):
        return _clean_text(value)

    @field_validator("allowed_values", mode="after", check_fields=False)
    @classmethod
    def _values(cls, value):
        return _clean_allowed_values(value)


class MetadataFieldCreate(_FieldText):
    key: str = Field(
        ...,
        min_length=1,
        max_length=64,
        pattern=KEY_PATTERN,
        description=(
            "The stable name values are stored under: lower case, a letter "
            "first, then letters, digits and underscores. Never changes."
        ),
    )
    label: str = Field(..., max_length=128, description="The name a person reads.")
    type: MetadataFieldType = Field(..., description="The type of the field's values.")
    unit: str | None = Field(None, max_length=32, description="The unit of a value.")
    allowed_values: list[str] | None = Field(
        None,
        description=(
            "The values an enum field picks one of and a tags field draws on. "
            "Required, and not empty, for an enum field; refused for the other "
            "types but tags."
        ),
    )
    description: str | None = Field(None, description="What the field records.")

    @field_validator("label", mode="after")
    @classmethod
    def _label_given(cls, value):
        value = _clean_text(value)
        if not value:
            raise ValueError("A field needs a label.")
        return value


class MetadataFieldUpdate(_FieldText):
    """A change to a field. Its key is not among them: a key never changes."""

    label: str | None = Field(None, max_length=128)
    type: MetadataFieldType | None = Field(
        None, description="A new type; refused for a field in use."
    )
    unit: str | None = Field(None, max_length=32)
    allowed_values: list[str] | None = None
    description: str | None = None
    retired: bool | None = Field(
        None,
        description=(
            "True retires the field: it stays, with every value written under "
            "it, and leaves the forms that offer fields. False restores it."
        ),
    )

    @field_validator("label", mode="after")
    @classmethod
    def _label_not_cleared(cls, value):
        # Only reached when a label was sent: blank is not "unchanged".
        value = _clean_text(value)
        if value is None:
            raise ValueError("A field needs a label.")
        return value


class GetMetadataFieldsQueryParams(QueryParamsModel):
    include_retired: bool = Field(
        False, description="Include the fields that were retired."
    )
