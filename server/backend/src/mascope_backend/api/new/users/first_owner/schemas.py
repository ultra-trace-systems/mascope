from pydantic import Field, field_validator, model_validator

from mascope_backend.api.new.auth.config import auth_settings
from mascope_backend.api.new.users.first_owner.exceptions import (
    InvalidServerOwnerSecretException,
)
from mascope_backend.api.new.users.first_owner.secrets import (
    server_owner_secret_key,
)
from mascope_backend.api.new.users.schemas import UserCreate


class FirstOwnerCreate(UserCreate):
    """Schema for creating the first owner user with server secret validation."""

    # Required again here, against the optional field on UserCreate: nobody is
    # handing this account over, so there is no temporary password to generate -
    # the first owner chooses their own and is never asked to replace it.
    password: str = Field(
        ..., description="Password the first owner chooses for themselves."
    )
    server_secret: str = Field(
        ..., description="Server-specific secret required for owner registration"
    )

    @model_validator(mode="before")
    @classmethod
    def validate_allowed_fields(cls, values):
        """
        First validation step: validates input fields and sets required role_id.
        Override parent's validator to include server_secret.
        """
        allowed_fields = {"email", "password", "username", "server_secret"}
        invalid_fields = {key for key in values if key not in allowed_fields}
        if invalid_fields:
            raise ValueError(
                f"Invalid fields for owner registration: {', '.join(invalid_fields)}"
            )

        # Set required role_id for owner
        values["role_id"] = auth_settings.ROLE_ACCESS_LEVELS["owner"]
        return values

    @field_validator("server_secret")
    @classmethod
    def validate_server_secret(cls, secret: str) -> str:
        """Validate the provided server secret."""
        if secret != server_owner_secret_key:
            raise InvalidServerOwnerSecretException()
        return secret

    @model_validator(mode="after")
    def transform_to_owner(self):
        """
        Transform validated data to include owner-specific fields and removes server secret.
        """
        # Set owner privileges
        self.is_superuser = True
        self.is_active = True

        # Remove server_secret field entirely
        if hasattr(self, "server_secret"):
            delattr(self, "server_secret")

        return self
