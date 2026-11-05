"""The matcher's peak read on a shifted axis, as a decoy run makes it.

The store is read where its peaks are stored, and the peaks are matched where
the block's axis puts them: a target qualifies a peak by its moved m/z, the
series is loaded by its stored one, and the match frame names the moved m/z.
The store reads are replaced, so the window, the load and the coordinate are
covered through ``compute_match_isotopes`` and not only the helper.
"""

import asyncio
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
import xarray as xr

import mascope_match.compute.isotopes as isotopes
from mascope_match import compute_match_isotopes, shifted_mz_axis


STORED = [100.0, 100.6, 200.0]


def _series(mzs) -> xr.Dataset:
    """The per-scan heights of the requested peaks, ten scans each."""
    mzs = np.asarray(mzs, dtype=float)
    heights = np.tile(np.linspace(900.0, 1100.0, 10), (len(mzs), 1))
    return xr.Dataset(
        {
            "peak_heights": (("mz", "time"), heights),
            "signal_to_noise": ("mz", np.full(len(mzs), 50.0)),
        },
        coords={
            "mz": mzs,
            "time": np.arange(10, dtype=float),
            "peak_id": ("mz", [f"p{round(mz * 10)}" for mz in mzs]),
            "tof": ("mz", np.zeros(len(mzs))),
        },
    )


@pytest.fixture()
def store(monkeypatch):
    """A file store of three peaks, recording which m/z each read asked for."""
    asked: dict = {}

    async def load_peak_timeseries(filename, mzs):
        asked["mzs"] = sorted(float(mz) for mz in mzs)
        return _series(sorted(mzs))

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
    return asked


def _targets(mz: float) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "target_ion_id": ["ion"],
            "target_isotope_id": ["iso"],
            "mz": [mz],
            "relative_abundance": [1.0],
        }
    )


def _match(mz: float) -> pd.Series:
    return asyncio.run(compute_match_isotopes("sample-file", _targets(mz))).iloc[0]


def test_outside_a_block_the_peaks_are_matched_where_they_are_stored(store):
    match = _match(200.0)

    assert match["sample_peak_mz"] == 200.0
    assert match["match_mz_error"] == pytest.approx(0.0)


def test_inside_a_ppm_block_every_line_is_off_its_target_by_the_shift(store):
    with shifted_mz_axis(ppm=9.0):
        match = _match(200.0)

    assert match["sample_peak_mz"] == pytest.approx(200.0018)
    assert match["match_mz_error"] == pytest.approx(9.0, abs=1e-6)
    assert match["sample_peak_id"] == "p2000"
    # The series and its noise estimate are the stored peak's own.
    assert match["signal_to_noise"] == 50.0


def test_a_peak_qualifies_by_where_it_reads_and_loads_by_where_it_is(store):
    # Moved 0.35 Da, the peak stored at 100.0 reads at 100.35 and the one at
    # 100.6 at 100.95: a target at 101.4 reaches the second alone, which on the
    # stored axis is 0.8 Da away and out of the window.
    with shifted_mz_axis(da=0.35):
        match = _match(101.4)

    assert store["mzs"] == [100.6]
    assert match["sample_peak_mz"] == pytest.approx(100.95)
    assert match["sample_peak_id"] == "p1006"
