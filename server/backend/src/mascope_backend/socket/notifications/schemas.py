from pydantic import BaseModel, Field, field_validator

from mascope_backend.db.id import gen_id


class UserNotification(BaseModel):
    process_id: str = Field(
        default_factory=lambda: gen_id(8),
        description="Unique identifier for the notification process.",
    )
    parent_id: str | None = Field(
        None,
        description=(
            "Identifier of the parent process if this notification "
            "is part of a larger workflow."
        ),
    )
    type: str = Field(
        ...,
        description=(
            "Type of process, corresponds to the backend function "
            "name handling the operation."
        ),
    )
    message: str = Field(
        ..., description="User-friendly message describing the notification."
    )
    progress: float | None = Field(
        None, description="Current progress percentage of the process."
    )
    status: str = Field(
        ...,
        description=(
            "Current status of the process: "
            "'success', 'error', 'pending', 'warning', 'info'."
        ),
    )
    data: dict[str, object] | None = Field(
        None, description="Optional payload with additional information or results."
    )
    error: dict[str, object] | None = Field(
        None, description="Optional error details when status is 'error'."
    )
    silent: bool | None = Field(
        None,
        description=(
            "When true the frontend uses this packet only to end the "
            "process's progress bar: it is not logged, counted on the "
            "notification badge or displayed."
        ),
    )

    @field_validator("status")
    @classmethod
    def validate_status(cls, v):
        valid_statuses = ["success", "info", "error", "pending", "warning"]
        if v not in valid_statuses:
            raise ValueError(
                f"Invalid status '{v}'. Valid statuses are {valid_statuses}."
            )
        return v

    @field_validator("progress")
    @classmethod
    def validate_progress(cls, v):
        if v is not None and not (0 <= v <= 100):
            raise ValueError("Progress must be between 0 and 100.")
        return v
