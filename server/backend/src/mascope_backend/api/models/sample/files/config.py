"""
Sample file configuration: the vocabulary of a file's processing status.

Auto-processing records on each ``sample_file`` how far it got (see
``api/controllers/sample/files/process/status.py``, which writes it).
"""

from datetime import timedelta
from enum import StrEnum


class ProcessingStatus(StrEnum):
    """The stage a sample file's auto-processing reached."""

    #: The converter registered the file. Auto-processing starts next.
    CONVERTED = "converted"
    #: Processing was asked for again, and waits for its turn. What an earlier
    #: run left of the file is about to be replaced.
    QUEUED = "queued"
    #: The file's samples exist, each under an ionization mode.
    BOUND = "bound"
    #: The m/z calibration was fitted and verified. Matching follows.
    CALIBRATED = "calibrated"
    #: No ionization mode could be bound to the file, so it has no samples.
    NEEDS_CHEMISTRY = "needs_chemistry"
    #: An m/z calibration failed, fell below the quality bar, or could not be
    #: made, so some or all of the file's samples were not matched.
    CALIBRATION_FAILED = "calibration_failed"
    #: Every sample of the file was matched, or a blank had nothing to match.
    DONE = "done"
    #: Processing stopped on an error.
    FAILED = "failed"


#: The statuses of a run still under way. A row that holds one when the server
#: starts belongs to a run the restart cut short.
IN_PROGRESS = frozenset(
    {
        ProcessingStatus.CONVERTED,
        ProcessingStatus.QUEUED,
        ProcessingStatus.BOUND,
        ProcessingStatus.CALIBRATED,
    }
)

#: How long a run may go without recording a stage before its file counts as
#: stalled, and processing asked for again may take the file over. A run
#: records a stage within minutes of starting; the longest a file waits before
#: its run starts is its turn behind the ingest gate in a burst of uploads,
#: hours at most. A row in progress for longer has no run behind it - one
#: whose worker was restarted, say, which only a full restart marks failed.
STALLED_AFTER = timedelta(hours=24)
