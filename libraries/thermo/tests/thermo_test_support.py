"""
Sample raw files and a read helper shared by the thermo tests.

A plain module rather than conftest.py content, so the tests can import it:
`from conftest import ...` binds to whichever conftest pytest loaded last,
which in a run spanning several test directories is another directory's.
"""

import os
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
