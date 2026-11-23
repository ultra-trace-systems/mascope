"""``compute_match_isotopes`` matches off the event loop.

The match itself - parsing the peaks, pairing every target line, the
per-scan ratios and the statistics - takes about half a second on a demo
Orbitrap file, and the Stage A pass of a peak assignment run makes it once
per neighbour as well. Run on the event loop, it held every other request of
the server behind it (MAS-947). It runs in a worker thread, which carries the
caller's context along.
"""

import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from mascope_match.compute import isotopes
from mascope_match.compute.isotopes import compute_match_isotopes


def test_the_match_runs_in_a_worker_thread(monkeypatch):
    targets = pd.DataFrame({"mz": [100.0], "target_ion_id": ["ion-1"]})
    peaks = object()
    matched = pd.DataFrame({"target_ion_id": ["ion-1"]})
    ran_on: list[threading.Thread] = []

    def match(*args):
        ran_on.append(threading.current_thread())
        return matched

    monkeypatch.setattr(isotopes, "get_instrument_type", lambda _: "orbi")
    monkeypatch.setattr(isotopes, "load_peaks", AsyncMock(return_value=peaks))
    monkeypatch.setattr(isotopes, "_match_on_peaks", MagicMock(side_effect=match))

    result = asyncio.run(compute_match_isotopes("sample-file", targets))

    assert result is matched
    assert ran_on and ran_on[0] is not threading.main_thread()
    target_arg, peaks_arg, reference_arg, instrument_arg = (
        isotopes._match_on_peaks.call_args.args
    )
    assert target_arg is targets
    assert (peaks_arg, reference_arg, instrument_arg) == (peaks, None, "orbi")


def test_a_failed_match_still_reads_as_a_computing_failure(monkeypatch):
    targets = pd.DataFrame({"mz": [100.0]})

    def fail(*args):
        raise KeyError("sample_peak_mz")

    monkeypatch.setattr(isotopes, "get_instrument_type", lambda _: "orbi")
    monkeypatch.setattr(isotopes, "load_peaks", AsyncMock(return_value=object()))
    monkeypatch.setattr(isotopes, "_match_on_peaks", fail)

    with pytest.raises(ValueError, match="^Computing matches failed"):
        asyncio.run(compute_match_isotopes("sample-file", targets))
