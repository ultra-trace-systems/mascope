"""Request bodies of the environment registry (``/api/environments``)."""

from pydantic import BaseModel, ConfigDict, Field, field_validator

from mascope_backend.api.models.base_pydantic_model import QueryParamsModel
from mascope_backend.api.new.peak_assignments.config import named_chemistry_context
from mascope_reference.peaklist import REGIMES


#: A key is the value a sample is tagged with: lower-case words of letters and
#: digits joined by hyphens, as the chemistry contexts are named.
ENVIRONMENT_KEY_PATTERN = r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$"


def _clean_label(value: str | None) -> str:
    value = (value or "").strip()
    if not value:
        raise ValueError("An environment needs a label.")
    return value


def _clean_regimes(values: list[str]) -> list[str]:
    """Strip the regimes and refuse a repeated one or one the lists cannot declare.

    The regimes are checked against the vocabulary a list's header declares
    its own in (:data:`mascope_reference.peaklist.REGIMES`): a regime outside
    it is one no list can declare, so it would select nothing.
    """
    cleaned = [value.strip() for value in values]
    unknown = [value for value in cleaned if value not in REGIMES]
    if unknown:
        raise ValueError(
            f"Not a regime a reference list declares: {', '.join(unknown)}. "
            f"The regimes are: {', '.join(sorted(REGIMES))}."
        )
    if len(set(cleaned)) != len(cleaned):
        raise ValueError("Each regime may be listed only once.")
    return cleaned


def _clean_context(value: str | None) -> str | None:
    """The shipped chemistry context named, spelled as its own name; None for none."""
    return named_chemistry_context(value)


class EnvironmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(
        ...,
        min_length=1,
        max_length=64,
        pattern=ENVIRONMENT_KEY_PATTERN,
        description=(
            "The value a sample is tagged with under the environment field: "
            "lower-case words joined by hyphens. Never changes."
        ),
    )
    label: str = Field(..., max_length=128, description="The name a person reads.")
    chemistry_context: str | None = Field(
        None,
        description=(
            "The shipped chemistry context a sample of this environment is read "
            "under, such as 'ambient-air'; null, blank or 'auto' for none."
        ),
    )
    regimes: list[str] = Field(
        default_factory=list,
        description=(
            "The regimes whose reference lists a sample of this environment is "
            "read against, from the vocabulary the lists declare theirs in "
            "(GET /api/environments/regimes)."
        ),
    )

    @field_validator("label", mode="after")
    @classmethod
    def _label_given(cls, value):
        return _clean_label(value)

    @field_validator("chemistry_context", mode="after")
    @classmethod
    def _context(cls, value):
        return _clean_context(value)

    @field_validator("regimes", mode="after")
    @classmethod
    def _regimes(cls, value):
        return _clean_regimes(value)


class EnvironmentUpdate(BaseModel):
    """A change to an environment. Its key is not among them: a key never changes."""

    model_config = ConfigDict(extra="forbid")

    label: str | None = Field(None, max_length=128)
    chemistry_context: str | None = Field(
        None, description="A new chemistry context; null clears it."
    )
    regimes: list[str] | None = Field(None, description="The new regimes.")
    retired: bool | None = Field(
        None,
        description=(
            "True retires the environment: its key stays a value of the "
            "environment field, so what was tagged with it keeps its meaning, "
            "and it leaves the forms that offer environments. False restores it."
        ),
    )

    @field_validator("label", mode="after")
    @classmethod
    def _label_not_cleared(cls, value):
        # Only reached when a label was sent: blank is not "unchanged".
        return _clean_label(value)

    @field_validator("chemistry_context", mode="after")
    @classmethod
    def _context(cls, value):
        return _clean_context(value)

    @field_validator("regimes", mode="after")
    @classmethod
    def _regimes(cls, value):
        if value is None:
            raise ValueError("Send the regimes as a list; an empty one for none.")
        return _clean_regimes(value)


class GetEnvironmentsQueryParams(QueryParamsModel):
    include_retired: bool = Field(
        False, description="Include the environments that were retired."
    )
