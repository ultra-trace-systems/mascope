"""
Integration tests: calibration transfer to a TOF file whose mode has no
calibrants (MAS-187).

Against a real database and files on disk: which neighbours the query finds
(the instrument, the polarity, TOF, a fit that cleared the bar, the window),
and what a transfer writes - the record, and every m/z axis of the file,
recomputed from its TOF index on parameters moved between the files' cuts.
The arithmetic and the choice of the nearest two are the unit tests'
(``unit/api/calibration/test_tof_calibration_transfer.py``).
"""

import copy
import json
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import h5py
import numpy as np
import pytest
import pytest_asyncio
import xarray as xr
from sqlalchemy import delete

import mascope_file.io as m_io
import mascope_file.name as m_name
from mascope_backend.api.controllers.calibration import calibration_controller
from mascope_backend.api.controllers.calibration.lib import acquisition_axis
from mascope_backend.api.controllers.calibration.lib import (
    tof_calibration_transfer as transfer,
)
from mascope_backend.db import SampleFile
from mascope_backend.db.id import gen_id
from mascope_tofwerk.calibration import tof_to_mass


#: The ambient file's centre: its start plus half its two minutes.
_T0 = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
_LENGTH = 120.0

#: The raw axis's samples, and how the instrument's TOF index reads m/z on
#: the raw axis: ``i = p1 * sqrt(m) + p2``.
_N_RAW = 40000
_TRUE = [2830.0, -4200.0]

#: The acquisition records of the files: the ambient file's reads every m/z
#: 100 ppm high, each neighbour's a little otherwise, so each cut is its own.
_ACQUIRED = {
    "ambient": [_TRUE[0] / np.sqrt(1 + 100e-6), _TRUE[1]],
    "before": [_TRUE[0] / np.sqrt(1 + 130e-6), _TRUE[1] - 3.0],
    "after": [_TRUE[0] / np.sqrt(1 + 90e-6), _TRUE[1] + 2.0],
}

#: The raw-axis TOF index of three lines in the ambient spectrum.
_LINES = {"NO3-": 61.98837, "HSO4-": 96.96010, "NO3-.HNO3": 124.98404}


def _cut(acquired: list[float]) -> int:
    """How many raw samples the converter drops: those below 10 Th."""
    raw = np.asarray(tof_to_mass(np.arange(_N_RAW, dtype=float), 0, acquired))
    return int((raw < 10.0).sum())


def _fit(key: str, drift_ppm: float) -> dict:
    """The node's fit of a neighbour: the true axis, on its own cut index, read
    ``drift_ppm`` high as the instrument drifted (a smaller p1 reads every m/z
    high)."""
    return {
        "mode": 0,
        "par": [
            _TRUE[0] / np.sqrt(1 + drift_ppm * 1e-6),
            _TRUE[1] - _cut(_ACQUIRED[key]),
        ],
        "status": "ok",
        "verified": True,
        "quality": {"n_points": 5, "n_ions": 3},
    }


_FITS = {"before": _fit("before", 2.0), "after": _fit("after", -2.0)}


def _write_tof(filename: str, record: dict, acquired: list[float]) -> None:
    """A TOF file in the filestore: the raw axis in its ``data.h5``, the stored
    sum signal and peaks on the cut axis, as ``record`` places them."""
    path = m_name.parse_path_from_item_filename(filename)
    os.makedirs(path, exist_ok=True)
    raw_axis = np.asarray(tof_to_mass(np.arange(_N_RAW, dtype=float), 0, acquired))
    with h5py.File(os.path.join(path, "data.h5"), "w") as h5_file:
        h5_file.create_dataset("FullSpectra/MassAxis", data=raw_axis)
    cut = int((raw_axis < 10.0).sum())
    index = np.arange(_N_RAW - cut, dtype=float)
    converter = record.get("status") == "unfitted"
    axis = np.asarray(
        tof_to_mass(index + cut, 0, record["par"])
        if converter
        else tof_to_mass(index, record["mode"], record["par"])
    )
    xr.Dataset(
        {"sum_signal": ("mz", np.ones(index.size))}, coords={"mz": axis}
    ).to_zarr(os.path.join(path, "sum_signal.zarr"), mode="w", consolidated=True)
    tof = np.array([_TRUE[0] * np.sqrt(mz) + _TRUE[1] for mz in _LINES.values()]) - cut
    peak_mz = np.asarray(
        tof_to_mass(tof + cut, 0, record["par"])
        if converter
        else tof_to_mass(tof, record["mode"], record["par"])
    )
    xr.Dataset(
        {"sum_peak_areas": ("mz", np.ones(tof.size))},
        coords={"mz": peak_mz, "tof": ("mz", tof)},
    ).to_zarr(os.path.join(path, "peak_timeseries.zarr"), mode="w", consolidated=True)
    with open(os.path.join(path, ".props"), "w") as props:
        json.dump(
            {
                "instrument_type": "tof",
                "polarity": "-",
                "range": [float(axis[0]), float(axis[-1])],
                "mz_calibration": record,
            },
            props,
        )


@pytest.fixture
def filestore(tmp_path, monkeypatch):
    """A filestore of its own, so no test reads or writes a real one."""
    monkeypatch.setattr(m_name.runtime.meta, "filestore", str(tmp_path))
    monkeypatch.setattr(m_name, "_INSTRUMENT_TYPE", {})
    return tmp_path


@pytest.fixture(autouse=True)
def no_samples(monkeypatch):
    """The files have no sample items, so an apply has no matches to remove
    and no batches to flag; the lookup says so with empty lists, as it does
    for a file whose samples are all in acquisition batches."""

    async def affected(**_):
        return SimpleNamespace(
            affected_sample_item_ids=[],
            affected_sample_batch_ids=[],
            affected_samples=[],
            affected_sample_batches=[],
        )

    monkeypatch.setattr(calibration_controller, "fetch_affected_sample_data", affected)


@pytest_asyncio.fixture
async def instrument(async_session_factory, filestore):
    """A multi-scheme TOF: an ambient file on its converter record between two
    fitted neighbours, 6 minutes before and 4 after it, and beside them files
    no transfer reads - a positive one, an Orbitrap one, another instrument's,
    a fit below the bar, an unverified one, a transfer, all a minute away, an
    unverified one stored with a length no timedelta holds, and a fit outside
    the window on each side - and one fit inside the window but farther than
    the nearest."""
    name = f"Xfer{gen_id(6)}"
    other = f"Xother{gen_id(6)}"
    files: dict[str, dict] = {}
    async with async_session_factory() as session:

        def add(
            key,
            minutes,
            record,
            polarity="-",
            kind="tof",
            instrument_name=name,
            write=False,
            length=_LENGTH,
        ):
            sample_file_id = gen_id(16)
            start = _T0 + timedelta(minutes=minutes) - timedelta(seconds=_LENGTH / 2)
            filename = f"{instrument_name}_{start:%Y.%m.%d_%Hh%Mm%Ss}_{key}"
            files[key] = {"id": sample_file_id, "filename": filename}
            if write:
                _write_tof(filename, record, _ACQUIRED[key])
            session.add(
                SampleFile(
                    sample_file_id=sample_file_id,
                    filename=filename,
                    instrument=instrument_name,
                    instrument_type=kind,
                    datetime=start.replace(tzinfo=None),
                    datetime_utc=start,
                    length=length,
                    range=[10.0, 600.0],
                    polarity=polarity,
                    mz_calibration=copy.deepcopy(record),
                )
            )

        converter = {"mode": 0, "par": _ACQUIRED["ambient"], "status": "unfitted"}
        add("ambient", 0, converter, write=True)
        add("before", -6, _FITS["before"], write=True)
        add("after", 4, _FITS["after"], write=True)
        add("farther", -20, _FITS["before"])
        add("positive", -1, _FITS["before"], polarity="+")
        add("orbi", -1, _FITS["before"], kind="orbi")
        add("other", -1, _FITS["before"], instrument_name=other)
        add("poor", -1, {**_FITS["before"], "status": "poor"})
        add("unverified", -1, {**_FITS["before"], "verified": False})
        add("transferred", 1, {**_FITS["after"], "status": "transferred"})
        add("endless", -2, {**_FITS["before"], "verified": False}, length=1e15)
        add("too_early", -31, _FITS["before"])
        add("too_late", 31, _FITS["after"])
        await session.commit()
    yield {"name": name, "files": files}
    async with async_session_factory() as session:
        await session.execute(
            delete(SampleFile).where(SampleFile.instrument.in_([name, other]))
        )
        await session.commit()


async def _sample_file(async_session_factory, sample_file_id) -> SampleFile:
    async with async_session_factory() as session:
        return await session.get(SampleFile, sample_file_id)


@pytest.mark.asyncio
async def test_the_query_finds_the_fits_of_the_instrument_and_polarity_in_the_window(
    async_session_factory, instrument
):
    files = instrument["files"]
    ambient = await _sample_file(async_session_factory, files["ambient"]["id"])

    found = await transfer.candidate_sources(ambient, 1800.0)

    assert {source.sample_file_id for source in found} == {
        files[key]["id"] for key in ("before", "after", "farther")
    }
    before = next(s for s in found if s.sample_file_id == files["before"]["id"])
    assert before.centre == _T0 - timedelta(minutes=6)
    assert before.par == _FITS["before"]["par"]


@pytest.mark.asyncio
async def test_a_transfer_lands_the_ambient_lines_where_the_neighbours_read_them(
    async_session_factory, instrument
):
    """The ambient file's acquisition axis reads its lines 100 ppm high; the
    transfer puts them between the neighbours' readings, 6 minutes after the
    one that read 2 ppm high and 4 before the one that read 2 ppm low: 0.4 ppm
    low."""
    files = instrument["files"]
    ambient = files["ambient"]
    before = m_io.load_coord(ambient["filename"], "peak_timeseries", "mz")
    np.testing.assert_allclose(
        (before - np.array(list(_LINES.values()))) / before * 1e6, 100.0, atol=0.5
    )

    outcome = await calibration_controller.calibration_mz_transfer(ambient["id"])

    assert outcome.applied
    stored = (await _sample_file(async_session_factory, ambient["id"])).mz_calibration
    assert stored["status"] == "transferred"
    assert stored["verified"] is True
    assert stored == outcome.record
    sources = stored["transfer"]["sources"]
    assert [source["sample_file_id"] for source in sources] == [
        files["before"]["id"],
        files["after"]["id"],
    ]
    assert [source["gap_s"] for source in sources] == [-360.0, 240.0]
    assert stored["transfer"]["gap_s"] == 240.0
    assert stored["transfer"]["raw_index_offset"] == _cut(_ACQUIRED["ambient"])
    # The record keeps the converter's axis, and where the stored axis starts
    # on it, as a fit's does (MAS-937); and when it was written
    assert stored["acquisition_axis"] == {
        "mode": 0,
        "par": _ACQUIRED["ambient"],
        "raw_index_offset": _cut(_ACQUIRED["ambient"]),
    }
    assert acquisition_axis.fitted_at(stored) is not None

    peaks = m_io.load_coord(ambient["filename"], "peak_timeseries", "mz")
    error_ppm = (peaks - np.array(list(_LINES.values()))) / peaks * 1e6
    # The neighbours read +2 and -2 ppm; 0.6 of the way from the one: -0.4.
    np.testing.assert_allclose(error_ppm, -0.4, atol=0.05)
    axis = m_io.load_coord(ambient["filename"], "sum_signal", "mz")
    np.testing.assert_array_equal(
        axis, tof_to_mass(np.arange(axis.size), stored["mode"], stored["par"])
    )
    props = m_io.read_props(ambient["filename"])
    assert props["mz_calibration"]["status"] == "transferred"


@pytest.mark.asyncio
async def test_without_the_cut_the_lines_would_be_far_off(
    async_session_factory, instrument
):
    """The two cuts differ, so parameters copied as they are would put the
    lines tens of ppm off: what the move between the cuts is for."""
    files = instrument["files"]
    cuts = {key: _cut(_ACQUIRED[key]) for key in _ACQUIRED}
    assert len(set(cuts.values())) == 3
    ambient_tof = (
        np.array([_TRUE[0] * np.sqrt(mz) + _TRUE[1] for mz in _LINES.values()])
        - cuts["ambient"]
    )
    copied = tof_to_mass(ambient_tof, 0, _FITS["before"]["par"])
    error_ppm = (copied - np.array(list(_LINES.values()))) / copied * 1e6
    assert np.min(np.abs(error_ppm)) > 20.0
    assert files  # the fixture's files are in place


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("record", "reason"),
    [
        (_FITS["before"], "it holds a fit of its own"),
        (
            {"mode": 0, "par": [1.0, 2.0], "status": "failed", "verified": False},
            "its own m/z calibration failed",
        ),
    ],
    ids=["own fit", "failed fit"],
)
async def test_a_transfer_replaces_neither_a_fit_of_the_file_nor_a_failed_one(
    async_session_factory, instrument, record, reason
):
    files = instrument["files"]
    async with async_session_factory() as session:
        row = await session.get(SampleFile, files["ambient"]["id"])
        row.mz_calibration = copy.deepcopy(record)
        await session.commit()
    axis = m_io.load_coord(files["ambient"]["filename"], "sum_signal", "mz")

    outcome = await calibration_controller.calibration_mz_transfer(
        files["ambient"]["id"]
    )

    assert not outcome.applied
    assert outcome.reason.startswith(reason)
    stored = await _sample_file(async_session_factory, files["ambient"]["id"])
    assert stored.mz_calibration == record
    np.testing.assert_array_equal(
        m_io.load_coord(files["ambient"]["filename"], "sum_signal", "mz"), axis
    )


@pytest.mark.asyncio
async def test_a_transfer_is_made_again_over_an_earlier_one(
    async_session_factory, instrument
):
    files = instrument["files"]
    first = await calibration_controller.calibration_mz_transfer(files["ambient"]["id"])
    again = await calibration_controller.calibration_mz_transfer(files["ambient"]["id"])

    assert first.applied and again.applied
    # The same record, written anew; the acquisition axis it kept goes on
    first.record.pop("fitted_at")
    again.record.pop("fitted_at")
    assert again.record == first.record
    assert again.record["acquisition_axis"]["par"] == _ACQUIRED["ambient"]


@pytest.mark.asyncio
async def test_a_file_with_no_fit_in_the_window_stays_unfitted(
    async_session_factory, instrument
):
    files = instrument["files"]
    async with async_session_factory() as session:
        for key in ("before", "after", "farther"):
            row = await session.get(SampleFile, files[key]["id"])
            row.mz_calibration = {**_FITS[key if key != "farther" else "before"]}
            row.mz_calibration["status"] = "poor"
        await session.commit()

    outcome = await calibration_controller.calibration_mz_transfer(
        files["ambient"]["id"]
    )

    assert not outcome.applied
    assert outcome.reason.startswith("no fitted file of its instrument and polarity")
    stored = await _sample_file(async_session_factory, files["ambient"]["id"])
    assert stored.mz_calibration["status"] == "unfitted"


@pytest.mark.asyncio
async def test_a_fit_of_the_file_itself_replaces_a_transfer(
    async_session_factory, instrument
):
    """Once its mode has calibrants, the file's own fit is applied over the
    transfer, and the record keeps nothing of it."""
    files = instrument["files"]
    await calibration_controller.calibration_mz_transfer(files["ambient"]["id"])
    own = {
        "mode": 0,
        "par": [_TRUE[0], _TRUE[1] - _cut(_ACQUIRED["ambient"])],
        "quality": {"n_points": 5, "n_ions": 3},
    }

    await calibration_controller.calibration_mz_apply(
        fit=copy.deepcopy(own), filename=files["ambient"]["filename"]
    )

    stored = (
        await _sample_file(async_session_factory, files["ambient"]["id"])
    ).mz_calibration
    assert stored["status"] in ("ok", "poor")
    assert "transfer" not in stored
    peaks = m_io.load_coord(files["ambient"]["filename"], "peak_timeseries", "mz")
    np.testing.assert_allclose(peaks, list(_LINES.values()), rtol=1e-9)


@pytest.mark.asyncio
async def test_no_transfer_for_a_file_that_is_gone(instrument):
    outcome = await calibration_controller.calibration_mz_transfer(gen_id(16))

    assert not outcome.applied
    assert outcome.reason == "the file is gone"
