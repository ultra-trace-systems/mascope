from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from mascope_backend.api.models.base_pydantic_model import QueryParamsModel


class TemplateField(BaseModel):
    label: str = Field(..., description="Label of the template field")
    required: Optional[bool] = Field(
        default=False, description="Indicates if the field is required"
    )
    value: Optional[str] = Field(
        default="", description="Default value for the template field"
    )


class AttributeTemplateBase(BaseModel):
    name: str = Field(..., description="Name of the attribute template")
    type: str = Field(
        ..., description="Type of the attribute template, e.g., 'sample_item'"
    )
    template: List[TemplateField] = Field(
        ..., description="List of template fields for the attribute template"
    )

    @field_validator("type")
    @classmethod
    def validate_type(cls, item):
        allowed_types = ["sample_item"]
        if item not in allowed_types:
            raise ValueError(
                f"The '{item}' is not a valid type for template. Allowed type is '{', '.join(allowed_types)}'"
            )
        return item

    @field_validator("template")
    @classmethod
    def validate_unique_labels(cls, template_fields):
        if not template_fields:
            raise ValueError("Template is empty.")
        labels = [field.label for field in template_fields]
        if len(labels) != len(set(labels)):
            raise ValueError(
                "Duplicate labels found in template fields. Each label must be unique."
            )
        return template_fields


class AttributeTemplateCreateBody(AttributeTemplateBase):
    pass


class AttributeTemplateUpdateBody(AttributeTemplateBase):
    pass


# Columns `sort` accepts (see mascope_backend.api.lib.sorting).
AttributeTemplateSortColumn = Literal[
    "attribute_template_id",
    "name",
    "type",
]


class GetAttributeTemplatesQueryParams(QueryParamsModel):
    sort: AttributeTemplateSortColumn | None = Field(
        "name", description="The column name by which you want to sort the results."
    )
    order: Optional[str] = Field(
        "asc",
        description="Can either be 'asc' for ascending order or 'desc' for descending order.",
    )
    page: int | None = Field(
        None,
        description="The page number for pagination, optional. None for no pagination.",
    )
    limit: int | None = Field(
        None,
        description="The number of results per page, optional. None for no pagination.",
    )
