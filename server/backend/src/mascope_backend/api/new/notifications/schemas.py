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


class MarkNotificationsReadBody(BaseModel):
    notification_ids: list[str] | None = Field(
        None, description="The notifications to mark read."
    )
    all: bool = Field(False, description="Mark every unread notification read.")

    @model_validator(mode="after")
    def one_selection(self):
        if self.all == (self.notification_ids is not None):
            raise ValueError("Give either notification_ids or all, not both")
        return self
