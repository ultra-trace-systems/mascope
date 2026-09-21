"""The vocabulary of kept notifications."""

from enum import StrEnum

from mascope_backend.api.models.sample.files.config import ProcessingStatus


class NotificationKind(StrEnum):
    """What a kept notification is about."""

    #: Processing stopped on an error for files of an instrument.
    PROCESSING_FAILED = "processing_failed"
    #: Files of an instrument could not be bound to an ionization mode.
    NEEDS_CHEMISTRY = "needs_chemistry"
    #: Files of an instrument were not matched, their calibration having failed.
    CALIBRATION_FAILED = "calibration_failed"


#: The processing outcomes that are kept for people to read, with the kind
#: and severity of the notification each becomes. Every other status is
#: either on its way somewhere or fine.
PROCESSING_NOTIFICATIONS: dict[ProcessingStatus, tuple[NotificationKind, str]] = {
    ProcessingStatus.FAILED: (NotificationKind.PROCESSING_FAILED, "error"),
    ProcessingStatus.NEEDS_CHEMISTRY: (NotificationKind.NEEDS_CHEMISTRY, "warning"),
    ProcessingStatus.CALIBRATION_FAILED: (
        NotificationKind.CALIBRATION_FAILED,
        "warning",
    ),
}

#: How many of a digest's files it names; the count goes on past it.
DIGEST_FILES = 20

#: The default and the largest page of notifications a person can ask for.
DEFAULT_LIMIT = 50
MAX_LIMIT = 200
