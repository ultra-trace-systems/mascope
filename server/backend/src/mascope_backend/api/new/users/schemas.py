from datetime import datetime
from typing import Optional

from fastapi_users import schemas
from pydantic import (
    BaseModel,
    EmailStr,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

from mascope_backend.accounts import ACCOUNT_TYPE_MACHINE, ACCOUNT_TYPE_PERSON
from mascope_backend.api.models.base_pydantic_model import QueryParamsModel
from mascope_backend.api.new.auth.config import auth_settings
from mascope_backend.api.new.auth.mfa import policy
from mascope_backend.api.new.users.exceptions import InvalidFieldsException


class UserRead(schemas.BaseUser[int]):
    """Schema to read user data, typically used in responses to provide user details."""

    id: int = Field(..., description="Unique identifier of the user (Primary Key).")
    email: EmailStr = Field(
        ...,
        description=(
            "User's email address. This is used as the unique login credential in the authentication flow. "
            "Although the specs of OAuth2 login form refers to this as `username`, it actually expects the user's email "
            "address here for authentication."
        ),
    )
    is_active: bool = Field(
        ..., description="Indicates if the user account is active and can sign in."
    )
    is_superuser: bool = Field(
        ..., description="Designates whether the user has superuser privileges."
    )
    is_verified: bool = Field(
        ..., description="Indicates whether the user's email is verified."
    )
    username: str = Field(
        ...,
        description="Name of the user, used for display purposes (not for login).",
    )
    role_id: int = Field(
        ...,
        description="Role identifier of the user, linked to user role permissions.",
    )
    role_name: Optional[str] = Field(
        None, description="Name of the role assigned to the user."
    )
    registered_at: datetime = Field(
        ..., description="Timestamp indicating when the user registered."
    )
    must_change_password: bool = Field(
        ...,
        description=(
            "True while the account must set a new password before it can use the "
            "application. Read-only: set by an administrator action or by account "
            "creation, cleared only by the account holder's own password change."
        ),
    )
    password_change_reason: Optional[str] = Field(
        None,
        description=(
            "Why a password change is pending: 'policy', 'reset' or 'new_account'. "
            "Null when nothing is pending."
        ),
    )

    mfa_enabled: bool = Field(
        False,
        description=(
            "True when the account holds a confirmed second authentication "
            "factor. Read-only: armed and cleared through the two-factor routes "
            "and the administrative reset, never by a request body."
        ),
    )

    account_type: str = Field(
        ACCOUNT_TYPE_PERSON,
        description=(
            "Whether this is a person or a machine (instrument agent) account. "
            "Machine accounts never sign in interactively and are managed "
            "through Paired machines, not user management."
        ),
    )

    model_config = {
        "from_attributes": True  # Allows Pydantic to work with SQLAlchemy models
    }

    @computed_field
    @property
    def mfa_enrollment_required(self) -> bool:
        """
        Whether this account is held out of the application until it enrols.

        Derived rather than stored: it is a function of the deployment's policy
        and the account's role, both of which change without touching the row.
        Computed here so every response carrying a user gets it, instead of at
        each of the three places that build this schema - one of which would
        eventually be missed, and the miss would read as "no enrolment owed".

        Always false for a machine account: it never renders an enrolment
        screen and its credential does not depend on a second factor.
        """
        if self.account_type == ACCOUNT_TYPE_MACHINE:
            return False
        return policy.enrollment_required(self.role_id, self.mfa_enabled)

    @field_validator("role_name")
    @classmethod
    def validate_role_name(cls, role_name):
        """
        Validates that `role_name` exists in the configured roles.
        """
        role_access_levels = auth_settings.ROLE_ACCESS_LEVELS
        if role_name not in role_access_levels:
            raise ValueError(f"Invalid role name: '{role_name}'.")
        return role_name


class UserPublic(BaseModel):
    """Reduced user schema for public listing. Omits sensitive fields like email."""

    id: int = Field(..., description="Unique identifier of the user (Primary Key).")
    username: str = Field(
        ...,
        description="Name of the user, used for display purposes (not for login).",
    )

    model_config = {
        "from_attributes": True,
    }


class UserCreate(schemas.BaseUserCreate):
    """Schema for creating a new user. Provides fields required for user registration."""

    email: EmailStr = Field(
        ...,
        description="User's email address. This will be used as the login credential.",
    )
    password: Optional[str] = Field(
        None,
        description=(
            "Optional. Omit it and the server generates a temporary password and "
            "returns it once in the response, which is how administrators create "
            "accounts: the holder must replace it at first sign-in either way, so "
            "a password chosen here is only ever a hand-over secret. Required "
            "where the account holder is choosing their own - see FirstOwnerCreate."
        ),
    )
    is_active: Optional[bool] = Field(
        default=True,
        description="Sets whether the user account is active upon creation.",
    )
    is_superuser: Optional[bool] = Field(
        default=False,
        description="Specifies if the user will have superuser privileges upon creation.",
    )
    is_verified: Optional[bool] = Field(
        default=False, description="Specifies whether the user's email is verified."
    )
    username: str = Field(
        ...,
        description="Username for display purposes. Note: This is not used for login.",
    )
    role_id: int = Field(
        ...,
        description="Role ID assigned to the user (e.g., '100' for guest users).",
    )

    model_config = {"from_attributes": True}

    @field_validator("role_id")
    @classmethod
    def validate_role_id(cls, role_id):
        """
        Validates that the provided role_id exists in the configured ROLE_ACCESS_LEVELS.

        :param role_id: The role ID provided for creation.
        :raises ValueError: If the role ID is not defined in the configuration.
        :return: The role ID if it is valid.
        """
        role_access_levels = auth_settings.ROLE_ACCESS_LEVELS

        # Check if the role_id exists in the values of ROLE_ACCESS_LEVELS
        if role_id not in role_access_levels.values():
            raise ValueError(
                f"The requested role with access level '{role_id}' is not defined in the configuration."
            )

        return role_id

    @field_validator("username")
    @classmethod
    def validate_username(cls, username):
        """
        Validates that `username` is not an empty string.

        :param username: The username provided for creation.
        :raises ValueError: If the username is an empty string.
        :return: The username if it is valid.
        """
        if username.strip() == "":
            raise ValueError("The username cannot be an empty string.")
        return username

    @model_validator(mode="before")
    @classmethod
    def validate_allowed_fields(cls, values):
        """
        Validates that only allowed fields are included in the creation request.

        :param values: Dictionary of fields being updated.
        :type values: dict
        :raises ValueError: If any field outside the allowed fields is included.
        :return: Filtered dictionary containing only allowed fields.
        :rtype: dict
        """
        allowed_fields = {"email", "password", "username", "role_id"}
        invalid_fields = {key for key in values if key not in allowed_fields}

        if invalid_fields:
            raise InvalidFieldsException(
                f"You can not specify these fields for user registration: {', '.join(invalid_fields)}."
            )

        return values


class UserUpdate(schemas.BaseUserUpdate):
    """Schema for updating existing user information. Fields can be partially updated."""

    email: Optional[EmailStr] = Field(
        None,
        description="Updated email address. If not provided, the current email remains unchanged.",
    )
    password: Optional[str] = Field(
        None,
        description="New password for the user. If not provided, the password remains unchanged.",
    )
    is_active: Optional[bool] = Field(
        None,
        description="Set to True or False to activate or deactivate the user account.",
    )
    is_superuser: Optional[bool] = Field(
        None, description="Set to True to grant or revoke superuser privileges."
    )
    is_verified: Optional[bool] = Field(
        None, description="Set to True or False to mark the user as verified or not."
    )
    username: Optional[str] = Field(
        None,
        description="Updated username for display purposes. Note: This is not used for login.",
    )
    role_id: Optional[int] = Field(
        None, description="Updated role ID to assign a new role to the user."
    )

    model_config = {"from_attributes": True}

    @field_validator("role_id")
    @classmethod
    def validate_role_id(cls, role_id):
        """
        Validates that the provided role_id exists in the configured ROLE_ACCESS_LEVELS.

        :param role_id: The role ID provided for update.
        :raises ValueError: If the role ID is not defined in the configuration.
        :return: The role ID if it is valid.
        """
        role_access_levels = auth_settings.ROLE_ACCESS_LEVELS

        # Check if the role_id exists in the values of ROLE_ACCESS_LEVELS
        if role_id is not None and role_id not in role_access_levels.values():
            raise ValueError(
                f"The requested role with access level '{role_id}' is not defined in the configuration."
            )

        return role_id

    @field_validator("username")
    @classmethod
    def validate_username(cls, username):
        """
        Validates that `username` is not an empty string.

        :param username: The username provided for update.
        :raises ValueError: If the username is an empty string.
        :return: The username if it is valid.
        """
        if username is not None and username.strip() == "":
            raise ValueError("The username cannot be an empty string.")
        return username

    @model_validator(mode="before")
    @classmethod
    def validate_allowed_fields(cls, values):
        """
        This validator checks that only allowed fields are included in the update request.
        Any other fields provided in the payload will raise a validation error.

        :param values: Dictionary of fields being updated.
        :type values: dict
        :raises ValueError: If any field outside the allowed fields is included.
        :return: Filtered dictionary containing only allowed fields.
        :rtype: dict
        """
        # must_change_password is deliberately absent: it is written by
        # UserManager (armed by any password write, cleared only by the account
        # holder's own change), never by a request body.
        allowed_fields = {
            "email",
            "password",
            "is_active",
            "is_verified",
            "username",
            "role_id",
        }
        invalid_fields = {key for key in values if key not in allowed_fields}

        if invalid_fields:
            raise InvalidFieldsException(
                f"You can not update these fields for user: {', '.join(invalid_fields)}."
            )

        return values


class GetUsersQueryParams(QueryParamsModel):
    """
    Query parameter model for retrieving users with pagination, sorting, and filtering options.
    """

    role_name_min: Optional[str] = Field(
        None, description="Minimum role name to filter users (e.g., 'guest')."
    )
    role_name_max: Optional[str] = Field(
        None, description="Maximum role name to filter users (e.g., 'admin')."
    )
    sort: Optional[str] = Field(
        "registered_at",
        description=(
            "Column name by which you want to sort the results. The column name should match the columns in the user table."
        ),
    )
    order: Optional[str] = Field(
        "desc",
        description="Sorting order: 'asc' for ascending or 'desc' for descending.",
    )
    page: int | None = Field(None, description="Page number for pagination.")
    limit: int | None = Field(None, description="Number of results per page.")

    @model_validator(mode="after")
    def validate_roles(self):
        role_access_levels = auth_settings.ROLE_ACCESS_LEVELS

        # Validate role_name_min exists in configuration
        if self.role_name_min and self.role_name_min not in role_access_levels:
            raise ValueError(f"Invalid role_name_min: '{self.role_name_min}'.")

        # Validate role_name_max exists in configuration
        if self.role_name_max and self.role_name_max not in role_access_levels:
            raise ValueError(f"Invalid role_name_max: '{self.role_name_max}'.")

        # Validate role_name_min <= role_name_max based on access levels
        if self.role_name_min and self.role_name_max:
            min_level = role_access_levels[self.role_name_min]
            max_level = role_access_levels[self.role_name_max]
            if min_level > max_level:
                raise ValueError(
                    f"Invalid range: role_name_min '{self.role_name_min}' cannot have a higher access level than role_name_max '{self.role_name_max}'."
                )

        return self
