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

# The hermetic MS2 fixture lives with the backend tests, which every MS2 phase
# tests on; the reader's own tests read the same shapes and the same answers.
_MS2_FIXTURE = _TESTS_DIR.parents[2] / "server" / "backend" / "tests" / "ms2_fixture.py"


def ms2_fixture():
    """The hermetic MS2 fixture module (``server/backend/tests/ms2_fixture.py``).

    Imported by path, as the backend's test directory is not on this suite's
    path, and under the name the backend tests import it by, so a run
    spanning both suites holds one module.
    """
    module = sys.modules.get("ms2_fixture")
    if module is None:
        spec = importlib.util.spec_from_file_location("ms2_fixture", _MS2_FIXTURE)
        module = importlib.util.module_from_spec(spec)
        sys.modules["ms2_fixture"] = module
        spec.loader.exec_module(module)
    return module


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
