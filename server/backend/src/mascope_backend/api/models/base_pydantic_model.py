from pydantic import BaseModel, field_validator, model_validator


class QueryParamsModel(BaseModel):
    """
    Custom base model for query parameter models.
    Handles common behaviors like decoding '+' signs in query parameters.
    """

    @field_validator(
        "polarity",
        "ionization_mechanism_polarity",
        "ionization_mechanism",
        "filename",
        "method_file",
        check_fields=False,  # allows the validator to work across models without requiring these fields
        mode="before",
    )
    @classmethod
    def decode_plus_sign(cls, value):
        """
        Automatically decode '+' signs for specific string fields in the query parameters.
        Handles both single strings and lists of strings.

        NOTE: This approach only works for fields that contain ONLY symbols (like polarity: "+", "-", "+-")
        and do NOT contain legitimate spaces. For fields that can contain both spaces and plus signs
        (like sample_batch_name: "Test + sign"), this approach fails because it cannot distinguish
        between spaces that were originally plus signs vs. legitimate spaces.

        TODO: This validator is a temporary workaround for symbol-only fields.
        Fix client-side URL encoding instead of server-side workarounds.
        See GitHub issue: [#1076](https://github.com/ultra-trace-systems/mascope/issues/1076)
        """
        if isinstance(value, str) and value:
            return value.replace(" ", "+")
        elif isinstance(value, list):
            return [
                item.replace(" ", "+") if isinstance(item, str) and item else item
                for item in value
            ]
        return value


class RequestBodyModel(BaseModel):
    """
    Base model for request body models.
    Can be combined with CommonValidators when validation is needed.
    """

    pass


class CommonValidators:
    """
    Mixin class with common validators.
    """

    @field_validator("polarity", mode="after", check_fields=False)
    @classmethod
    def validate_polarity(cls, polarity: str | None) -> str | None:
        """
        Validates that polarity is either '+' or '-'.
        This should run after URL decoding for query parameters.
        check_fields=False tells Pydantic that it's OK if the model doesn't have a polarity field

        :param polarity: The polarity value to validate
        :raises ValueError: If polarity is not '+' or '-'
        :return: The validated polarity value
        """
        if polarity is not None and polarity not in ["+", "-"]:
            raise ValueError("Polarity must be '+' or '-'")
        return polarity

    @model_validator(mode="after")
    def validate_time_range(self):
        """
        Validates that t_max is greater than t_min when both are provided.
        Safely handles models that don't have these fields.

        :param values: The model instance with all field values
        :raises ValueError: If t_max is not greater than t_min
        :return: The validated model instance
        """
        # Safely get time values using getattr with None defaults
        t_min = getattr(self, "t_min", None)
        t_max = getattr(self, "t_max", None)

        # Only validate if both fields exist and both have values
        if t_min is not None and t_max is not None:
            if t_max <= t_min:
                raise ValueError(
                    "Maximum time limit must be greater than minimum time limit"
                )

        return self

    # Future common validators can be added here
