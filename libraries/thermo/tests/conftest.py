"""Conftest for the thermo tests: the per-backend fixture.

The sample files and read helper the tests import live in
``thermo_test_support``.
"""

import pytest

from mascope_thermo.lib import thermo_available


@pytest.fixture(params=["thermo", "opentfraw"])
def backend(request, monkeypatch):
    """Run a contract test once per reader backend.

    Sets ``MASCOPE_THERMO_BACKEND`` for the test. The OpenTFRaw backend (the
    default, via the pinned ``opentfraw`` package) is always available and
    expected to pass. The Thermo backend needs the proprietary RawFileReader
    DLLs, which Mascope does not ship, so its runs are skipped unless
    ``MASCOPE_THERMO_DLL_DIR`` points at them.

    Tests/fixtures that build per-test state by *calling* a backend function must
    depend on this fixture (directly or via an autouse setup fixture) so the env
    var is set before the call -- a plain ``setup_method`` runs too early.
    """
    if request.param == "thermo" and not thermo_available():
        pytest.skip(
            "Thermo backend unavailable; set MASCOPE_THERMO_DLL_DIR to the "
            "RawFileReader DLLs to run it."
        )
    monkeypatch.setenv("MASCOPE_THERMO_BACKEND", request.param)
    return request.param
