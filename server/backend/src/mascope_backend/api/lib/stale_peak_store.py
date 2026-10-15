"""Telling a stale peak store from any other failure, wherever one is met."""

from mascope_signal.compute import StalePeakStoreError


def is_stale_peak_store(error: BaseException) -> bool:
    """
    Whether something failed because a file's peak data predates how the file reads.

    The failure is raised deep in the signal library and reaches a caller
    already wrapped by the ``@api_controller`` around the step that met it,
    so the class is looked for along the chain rather than on the exception
    itself.

    :param error: The exception the work on one sample raised.
    :type error: BaseException
    :return: ``True`` when re-running peak detection is what repairs it.
    :rtype: bool
    """
    seen: set[int] = set()
    current: BaseException | None = error
    # Both links: `raise X from e` sets __cause__, a bare `raise X` inside an
    # except block sets only __context__. `seen` guards a cycle, which a
    # hand-built chain can have.
    while current is not None and id(current) not in seen:
        if isinstance(current, StalePeakStoreError):
            return True
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return False
