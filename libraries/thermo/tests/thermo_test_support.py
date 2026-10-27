"""
Sample raw files and a read helper shared by the thermo tests.

A plain module rather than conftest.py content, so the tests can import it:
`from conftest import ...` binds to whichever conftest pytest loaded last,
which in a run spanning several test directories is another directory's.
"""

import importlib.util
import os
import sys
from pathlib import Path

import pytest


_TESTS_DIR = Path(__file__).parent

# Directory of sample .raw files. Only the two small committed sample files
# ship; tests that need other acquisitions (e.g. an MS2 file) discover them at
# runtime and skip when absent, so the suite stays portable. Override with
# MASCOPE_THERMO_TEST_FILES_DIR to run the file-agnostic parity suite against a
# broader local corpus (e.g. a stratified sample of the filestore).
TEST_FILES_DIR = Path(
    os.environ.get("MASCOPE_THERMO_TEST_FILES_DIR", _TESTS_DIR / "test_files")
)

POS_ORBI_FILE_PATH = str(TEST_FILES_DIR / "KORBI2_AMB_POS_20260109174345.raw")
NEG_ORBI_FILE_PATH = str(TEST_FILES_DIR / "KORBI2_AMB_NEG_20260108144525.raw")

# The hermetic acquisitions live with the backend tests, which convert them;
# the reader's own tests read the same acquisitions.
_BACKEND_TESTS_DIR = _TESTS_DIR.parents[2] / "server" / "backend" / "tests"


def _backend_test_module(name: str):
    """A module of the backend's test directory, by its name.

    Imported by path, as the backend's test directory is not on this suite's
    path, and under the name the backend tests import it by, so a run
    spanning both suites holds one module.
    """
    module = sys.modules.get(name)
    if module is None:
        spec = importlib.util.spec_from_file_location(
            name, _BACKEND_TESTS_DIR / f"{name}.py"
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return module


def ms2_fixture():
    """The hermetic MS2 fixture module (``server/backend/tests/ms2_fixture.py``),
    which every MS2 phase tests on."""
    return _backend_test_module("ms2_fixture")


def centroid_acquisition():
    """The committed MS1 file read as centroid scans alone
    (``server/backend/tests/centroid_acquisition.py``)."""
    return _backend_test_module("centroid_acquisition")


def read_or_xfail(fn, *args, **kwargs):
    """Call a backend read inside *fixture setup*, turning a not-yet-implemented
    backend into an xfail.

    The ``backend`` fixture's ``xfail(raises=NotImplementedError)`` only covers a
    test's *call* phase; an exception during fixture setup is otherwise reported
    as an error. An imperative ``pytest.xfail()`` works in setup, and -- since it
    only fires when the call actually raises -- the test still runs (and XPASSes)
    once the backend is implemented.
    """
    try:
        return fn(*args, **kwargs)
    except NotImplementedError as exc:
        pytest.xfail(str(exc))


class ScriptedThermoFilter:
    """A Thermo ``IScanFilter`` scripted by its text alone.

    It answers ``ToString()`` with the text and the filter's scan ranges, which
    the Thermo backend writes itself (``backend._thermo_filter_text``), with the
    ranges the text states. Further attributes (``Polarity``, ``MSOrder``) are
    set on it by the test that needs them.
    """

    def __init__(self, text: str, **attributes):
        from types import SimpleNamespace

        from mascope_thermo.scan_filter import parse_scan_filter

        self._text = text
        self._ranges = [
            SimpleNamespace(Low=low, High=high)
            for low, high in parse_scan_filter(text).scan_ranges
        ]
        self.MassRangeCount = len(self._ranges)
        self.__dict__.update(attributes)

    def ToString(self):  # noqa: N802
        return self._text

    def GetMassRange(self, index):  # noqa: N802
        return self._ranges[index]
