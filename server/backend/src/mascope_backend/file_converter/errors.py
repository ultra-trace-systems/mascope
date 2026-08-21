"""Exception types and helpers for file-conversion failures."""


def describe_exception(e: BaseException) -> str:
    """
    Human-readable one-liner for an exception destined for a user notification.

    The bare message of common builtin exceptions is cryptic on its own - a
    ``KeyError('Configuration File')`` renders as just ``'Configuration File'``
    - so the exception class name is prefixed unless the message already reads
    as a sentence.

    :param e: The exception to describe.
    :return: A one-line description safe to show in a notification.
    :rtype: str
    """
    message = str(e).strip()
    if not message:
        return type(e).__name__
    if (
        isinstance(e, (KeyError, IndexError, TypeError, AttributeError))
        or " " not in message
    ):
        return f"{type(e).__name__}: {message}"
    return message


#: The file recorded nothing at all - the run was aborted before its first
#: scan, or the acquisition software wrote the file and never filled it.
EMPTY_ACQUISITION_MESSAGE = (
    "The file contains no scans; the acquisition is empty or was aborted."
)

#: Exactly one scan: there is a spectrum but no second one to measure the
#: spacing against, so neither an interval nor a length can be derived.
SINGLE_SCAN_MESSAGE = (
    "The file contains only one scan; the acquisition was aborted before a "
    "measurable time axis was recorded."
)

#: Scans were recorded but their timestamps are not a usable axis - a partly
#: written or corrupted timing block leaves entries that are not finite.
UNUSABLE_SCAN_TIMES_MESSAGE = (
    "The file's scan timestamps are incomplete, so the acquisition has no "
    "measurable time axis."
)


class EmptyAcquisitionError(Exception):
    """A raw file that carries too few scans to ingest.

    Raised by a processor when the reader reports an empty acquisition - a run
    that was aborted, or that wrote a file before recording a single scan -
    and also when what was recorded yields no measurable time axis, such as a
    single scan. Nothing downstream can be derived from such a file, so it
    still fails and lands in ``failed_files``; the distinct type marks it as a
    property of the data rather than a fault in Mascope, so
    ``BaseFileProcessor.run`` logs it at INFO without a traceback and error
    monitoring stays quiet.

    The raise site picks which of the messages above applies, because only it
    knows which case it found; the wording lives here because it reaches the
    user's notification verbatim through ``describe_exception`` and both
    reader paths must say the same thing about the same condition.
    """


def is_routine_file_failure(e: BaseException) -> bool:
    """
    Whether a processing failure is a property of the data, not a fault.

    These still fail the file and still notify the user; what they skip is the
    traceback and, with it, the error-monitoring event. A duplicate upload and
    an empty acquisition are both things the world does to us routinely -
    reporting them as faults buries the failures that are ours to fix.

    :param e: The exception that failed the file.
    :return: True when the run loop should log a bare INFO line instead of an
        exception with its traceback.
    :rtype: bool
    """
    return isinstance(e, (FileExistsError, EmptyAcquisitionError))
