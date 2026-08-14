from pydantic import BaseModel, Field, field_validator


class RequirePasswordChange(BaseModel):
    """Acknowledgement body for the deployment-wide password-change trigger."""

    confirm: bool = Field(
        ...,
        description=(
            "Must be true. Required so the endpoint cannot be fired by an empty "
            "or accidental request; the consequences are spelled out in the "
            "interface that offers it."
        ),
    )

    @field_validator("confirm")
    @classmethod
    def validate_confirm(cls, confirm: bool) -> bool:
        """Reject anything but an explicit acknowledgement."""
        if not confirm:
            raise ValueError(
                "Set 'confirm' to true to require a password change for all users."
            )
        return confirm
