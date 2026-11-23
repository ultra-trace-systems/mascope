"""The matcher refuses a peak store that keeps no peak ids as stale (MAS-795).

A matched peak is named by its id, which a store written before ids were kept
does not have. The store is refused before its timeseries are loaded, as the
stale peak store it is (``mascope_signal.compute.StalePeakStoreError``), so a
batch refresh counts the sample under the stale-store reason and queues the
file's peak detection, which gives its peaks ids.
"""

import asyncio

import numpy as np
import pandas as pd
import pytest
import xarray as xr

import mascope_match.compute.isotopes as isotopes
from mascope_match import compute_match_isotopes
from mascope_signal.compute import StalePeakStoreError


STORED = [100.0, 101.0]


def _stored(ids: bool) -> xr.Dataset:
    coords = {"mz": STORED}
    if ids:
        coords["peak_id"] = ("mz", ["p1000", "p1010"])
    return xr.Dataset({"sparsity": ("mz", np.zeros(len(STORED)))}, coords=coords)


@pytest.fixture()
def store(monkeypatch):
    """A file store of two peaks, with or without their ids; what loading
    their timeseries was asked for."""
    asked = []

    def _store(ids: bool):
        async def load_peak_timeseries(filename, mzs):
            asked.append(list(mzs))
            mzs = np.asarray(sorted(mzs), dtype=float)
            return xr.Dataset(
                {"peak_heights": (("mz", "time"), np.ones((len(mzs), 4)))},
                coords={
                    "mz": mzs,
                    "time": np.arange(4, dtype=float),
                    "peak_id": ("mz", [f"p{round(mz * 10)}" for mz in mzs]),
                    "tof": ("mz", np.zeros(len(mzs))),
                },
            )

        monkeypatch.setattr(
            isotopes.m_io, "load_peak_data", lambda filename: _stored(ids)
        )
        monkeypatch.setattr(
            isotopes.m_compute, "load_peak_timeseries", load_peak_timeseries
        )
        monkeypatch.setattr(isotopes, "get_instrument_type", lambda _: "orbi")
        monkeypatch.setattr(isotopes, "get_sample_file_type", lambda _: "orbi_zarr")
        return asked

    return _store


def _match() -> pd.DataFrame:
    targets = pd.DataFrame(
        {
            "target_ion_id": ["ion"] * 2,
            "target_isotope_id": ["m0", "m1"],
            "mz": [100.0, 101.0],
            "relative_abundance": [1.0, 0.11],
        }
    )
    return asyncio.run(compute_match_isotopes("sample-file", targets))


def test_a_store_with_its_ids_is_matched(store):
    store(ids=True)

    frame = _match().set_index("target_isotope_id")

    assert frame.loc["m0", "sample_peak_id"] == "p1000"


def test_a_store_without_them_is_refused_as_stale(store):
    asked = store(ids=False)

    with pytest.raises(ValueError) as raised:
        _match()

    # Raised as the class, and kept on the chain the matcher wraps it in
    assert isinstance(raised.value.__cause__, StalePeakStoreError)
    assert "written before peak ids were kept" in str(raised.value)
    # Refused before its timeseries were loaded or its peaks parsed
    assert asked == []
