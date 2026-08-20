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


class EmptyAcquisitionError(Exception):
    """A raw file that carries too few scans to ingest.

    Raised by a processor when the reader reports an empty acquisition - a run
    that was aborted, or that wrote a file before recording a single scan -
    and also when a single scan was recorded, which yields no measurable time
    axis. Nothing downstream can be derived from such a file, so it still
    fails and lands in ``failed_files``; the distinct type marks it as a
    property of the data rather than a fault in Mascope, so
    ``BaseFileProcessor.run`` logs it at INFO without a traceback and error
    monitoring stays quiet.

    The message is written at the raise site because it names which of those
    cases applies, and it reaches the user's notification verbatim through
    ``describe_exception``.
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
