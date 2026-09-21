"""
Sample file configuration: the vocabulary of a file's processing status.

Auto-processing records on each ``sample_file`` how far it got (see
``api/controllers/sample/files/process/status.py``, which writes it).
"""

from enum import StrEnum


class ProcessingStatus(StrEnum):
    """The stage a sample file's auto-processing reached."""

    #: The converter registered the file. Auto-processing starts next.
    CONVERTED = "converted"
    #: The file's samples exist, each under an ionization mode.
    BOUND = "bound"
    #: The m/z calibration was fitted and verified. Matching follows.
    CALIBRATED = "calibrated"
    #: No ionization mode could be bound to the file, so it has no samples.
    NEEDS_CHEMISTRY = "needs_chemistry"
    #: The m/z calibration failed or is not verified, so nothing was matched.
    CALIBRATION_FAILED = "calibration_failed"
    #: Every sample of the file was matched.
    DONE = "done"
    #: Processing stopped on an error.
    FAILED = "failed"


#: The statuses of a run still under way. A row that holds one when the server
#: starts belongs to a run the restart cut short.
IN_PROGRESS = frozenset(
    {ProcessingStatus.CONVERTED, ProcessingStatus.BOUND, ProcessingStatus.CALIBRATED}
)
