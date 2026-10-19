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


@pytest.fixture
def dotnet_culture(monkeypatch):
    """Format the Thermo library's text in a .NET culture the test names.

    The library writes trailer values as text in the current culture, so
    without this a test sees only the regional format of the machine it runs
    on: a decimal comma on one, a point on another. Yields a function taking
    a culture name (``""`` for the invariant culture), and puts back the
    culture the test found. Selects the Thermo backend, and skips without
    its DLLs.
    """
    if not thermo_available():
        pytest.skip(
            "Thermo backend unavailable; set MASCOPE_THERMO_DLL_DIR to the "
            "RawFileReader DLLs to run it."
        )
    from mascope_thermo.thermo import _ensure_dotnet

    _ensure_dotnet()
    from System.Globalization import CultureInfo

    monkeypatch.setenv("MASCOPE_THERMO_BACKEND", "thermo")
    original = CultureInfo.CurrentCulture

    def use(name: str) -> None:
        CultureInfo.CurrentCulture = CultureInfo(name)

    yield use
    CultureInfo.CurrentCulture = original
