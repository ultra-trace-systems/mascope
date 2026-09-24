from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from mascope_backend.api.new.notifications.config import DEFAULT_LIMIT, MAX_LIMIT


class GetNotificationsQueryParams(BaseModel):
    include_read: bool = Field(
        False, description="List the notifications already read as well."
    )
    limit: int = Field(
        DEFAULT_LIMIT,
        ge=1,
        le=MAX_LIMIT,
        description="The most notifications to list, unread first.",
    )


class SeenNotification(BaseModel):
    notification_id: str
    updated_utc: datetime = Field(
        description=(
            "The notification's updated_utc as the reader saw it. A notification "
            "that took another file since is left unread."
        )
    )


class MarkNotificationsReadBody(BaseModel):
    notifications: list[SeenNotification] | None = Field(
        None, description="The notifications to mark read, each as it was seen."
    )
    all: bool = Field(
        False, description="Mark every unread notification read, as it is now."
    )

    @model_validator(mode="after")
    def one_selection(self):
        if self.all and self.notifications is not None:
            raise ValueError("Give notifications or all, not both")
        if not self.all and self.notifications is None:
            raise ValueError("Give the notifications to mark read, or all")
        return self
