"""The match frame carries each matched peak's scan counts.

A raw Orbitrap store keeps, per peak, how many of the scans averaged into it
hold it (``n_scans``) and how many those are (``n_scans_total``). The match
frame carries them for a matched line, as it carries the noise estimate, so
that the v2 fit score the assignment engine's first stage reads off it can
read a dim line at its mean per scan. An unmatched line has none, and
a store without them leaves the columns empty.
"""

import asyncio
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
import xarray as xr

import mascope_match.compute.isotopes as isotopes
from mascope_match import compute_match_isotopes


STORED = [100.0, 101.0]
N_SCANS = {100.0: 10.0, 101.0: 4.0}


def _series(mzs, counts: bool) -> xr.Dataset:
    """The per-scan heights of the requested peaks, ten scans each."""
    mzs = np.asarray(mzs, dtype=float)
    heights = np.tile(np.linspace(900.0, 1100.0, 10), (len(mzs), 1))
    data = {
        "peak_heights": (("mz", "time"), heights),
        "signal_to_noise": ("mz", np.full(len(mzs), 50.0)),
    }
    if counts:
        data["n_scans"] = ("mz", np.array([N_SCANS[mz] for mz in mzs]))
        data["n_scans_total"] = ("mz", np.full(len(mzs), 10, dtype=np.int64))
    return xr.Dataset(
        data,
        coords={
            "mz": mzs,
            "time": np.arange(10, dtype=float),
            "peak_id": ("mz", [f"p{round(mz * 10)}" for mz in mzs]),
            "tof": ("mz", np.zeros(len(mzs))),
        },
    )


@pytest.fixture()
def store(monkeypatch):
    """A file store of two peaks, with or without the scan counts."""

    def _store(counts: bool):
        async def load_peak_timeseries(filename, mzs):
            return _series(sorted(mzs), counts)

        monkeypatch.setattr(
            isotopes.m_io,
            "load_peak_data",
            lambda filename: SimpleNamespace(mz=pd.Series(STORED)),
        )
        monkeypatch.setattr(
            isotopes.m_compute, "load_peak_timeseries", load_peak_timeseries
        )
        monkeypatch.setattr(isotopes, "get_instrument_type", lambda _: "orbi")
        monkeypatch.setattr(isotopes, "get_sample_file_type", lambda _: "orbi_zarr")

    return _store


def _matches() -> pd.DataFrame:
    targets = pd.DataFrame(
        {
            "target_ion_id": ["ion"] * 3,
            "target_isotope_id": ["m0", "m1", "m2"],
            "mz": [100.0, 101.0, 102.0],
            "relative_abundance": [1.0, 0.11, 0.01],
        }
    )
    frame = asyncio.run(compute_match_isotopes("sample-file", targets))
    return frame.set_index("target_isotope_id")


def test_a_matched_line_carries_its_peaks_counts(store):
    store(counts=True)

    frame = _matches()

    assert frame.loc["m0", "n_scans"] == 10.0
    assert frame.loc["m1", "n_scans"] == 4.0
    assert (frame.loc[["m0", "m1"], "n_scans_total"] == 10).all()
    # Nothing at 102: no peak, no count
    assert np.isnan(frame.loc["m2", "n_scans"])
    assert np.isnan(frame.loc["m2", "n_scans_total"])


def test_a_store_without_counts_leaves_them_empty(store):
    store(counts=False)

    frame = _matches()

    assert frame["n_scans"].isna().all()
    assert frame["n_scans_total"].isna().all()
    assert frame.loc["m1", "signal_to_noise"] == 50.0
