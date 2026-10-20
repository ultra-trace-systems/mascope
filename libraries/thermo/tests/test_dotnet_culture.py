"""The culture the Thermo library writes its text in (``mascope_thermo.lib``).

The library formats trailer values in the current .NET culture, which starts
as the server's regional format. ``load_dotnet`` pins the invariant culture,
so a trailer reads alike whichever server read the file. Needs the Thermo
RawFileReader DLLs (``MASCOPE_THERMO_DLL_DIR``); the ``dotnet_culture``
fixture skips without them.
"""

import concurrent.futures
import sys

import pytest
from thermo_test_support import NEG_ORBI_FILE_PATH

import mascope_thermo.lib as m_lib
from mascope_thermo.backend import ThermoBackend
from mascope_thermo.calibration import CONVERSION_C
from mascope_thermo.thermo import _ensure_dotnet


# The negative-mode sample file's C, as the invariant culture writes it.
NEG_C = "-71050473.7320"


def _conversion_c() -> str:
    with ThermoBackend(NEG_ORBI_FILE_PATH) as reader:
        return reader.scan_trailer(reader.scan_filters()[0]["scan"])[CONVERSION_C]


def _conversion_c_on_another_thread() -> str:
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(_conversion_c).result()


@pytest.mark.parametrize("culture", ["fi-FI", "de-DE", "sv-SE"])
def test_the_library_writes_invariant_text_whatever_culture_it_starts_in(
    dotnet_culture, monkeypatch, culture
):
    """After ``load_dotnet``, a negative number has an ASCII minus and a
    decimal point, on the thread that loaded the library and on any other,
    such as the backend's worker threads.

    A server's regional format is the culture .NET starts in. The runtime
    starts once a process, so a stand-in for ``load`` starts it in the
    culture named, on this thread and as the default for every other,
    before ``load_dotnet`` goes on. fi-FI and de-DE write a decimal comma;
    sv-SE, where .NET takes its culture data from ICU, the minus sign U+2212
    too.
    """
    dotnet_culture(culture)
    unpinned = _conversion_c()
    # The stand-in writes as a server in that culture does.
    assert unpinned != NEG_C
    assert _conversion_c_on_another_thread() == unpinned

    monkeypatch.setattr(m_lib, "initialized", False)
    monkeypatch.setattr(m_lib, "load", lambda _runtime: dotnet_culture(culture))
    monkeypatch.setattr(sys, "path", list(sys.path))
    _ensure_dotnet()

    assert _conversion_c() == NEG_C
    assert _conversion_c_on_another_thread() == NEG_C
