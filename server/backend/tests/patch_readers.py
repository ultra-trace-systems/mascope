"""Stand in for one name in each module of the assignment package that reads it.

A module reads a name it imports at its own top level, so a patch reaches the
code that calls the name only in the module that code is in. Where several
modules of a run read one name - the sample's peaks, its mechanisms, the
database session - a test that stands in for it stands in for it in each, with
one mock, so what the test sets on the mock and asserts of it holds wherever
the run read it.
"""

from unittest.mock import DEFAULT, patch


PACKAGE = "mascope_backend.api.new.peak_assignments"


class PatchReaders:
    """``unittest.mock.patch`` of ``name`` in each of ``modules``, one mock for all.

    Started and stopped as a patch is: ``start()`` returns the mock, and it is
    a context manager. ``patch.stopall()`` stops it too.

    :param name: The name to stand in for.
    :param modules: The package's modules that read it, the first patched with
        ``new`` and ``kwargs`` as ``patch`` would be, the others with the mock
        that made.
    """

    def __init__(self, name: str, modules: tuple[str, ...], new=DEFAULT, **kwargs):
        if not modules:
            raise ValueError(f"no module reads {name}")
        self.name = name
        self.modules = modules
        self.new = new
        self.kwargs = kwargs
        self._patches = []

    def start(self):
        first = patch(
            f"{PACKAGE}.{self.modules[0]}.{self.name}", self.new, **self.kwargs
        )
        mock = first.start()
        self._patches = [first]
        for module in self.modules[1:]:
            other = patch(f"{PACKAGE}.{module}.{self.name}", mock)
            other.start()
            self._patches.append(other)
        return mock

    def stop(self):
        for one in reversed(self._patches):
            one.stop()
        self._patches = []

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()
        return False
