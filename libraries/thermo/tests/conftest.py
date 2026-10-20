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

    The library writes trailer values as text in the current culture, which
    ``load_dotnet`` pins to the invariant one, so without this a test sees
    only invariant text. With it the library writes as it did on a server
    in that culture before the pin, and so the text such a server captured:
    a decimal comma under de-DE, and under sv-SE the minus sign U+2212 too.
    Yields a function taking a culture name (``""`` for the invariant
    culture), which sets it as the current culture and as the default for
    every other thread, as the pin does, and puts back the cultures the test
    found. Selects the Thermo backend, and skips without its DLLs.
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
    original = CultureInfo.CurrentCulture, CultureInfo.DefaultThreadCurrentCulture

    def use(name: str) -> None:
        culture = CultureInfo(name)
        CultureInfo.DefaultThreadCurrentCulture = culture
        CultureInfo.CurrentCulture = culture

    yield use
    CultureInfo.CurrentCulture, CultureInfo.DefaultThreadCurrentCulture = original
