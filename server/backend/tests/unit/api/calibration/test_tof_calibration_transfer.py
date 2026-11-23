"""
Calibration transfer for TOF files whose mode has no calibrants (MAS-187).

The pure parts: which records lend a calibration, which neighbours are taken,
how their parameters are moved between the files' cut axes and interpolated,
and the record. The crop offset is read from a file on disk.
"""

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import h5py
import numpy as np
import pytest
import xarray as xr

import mascope_file.name as m_name
from mascope_backend.api.controllers.calibration.lib import (
    tof_calibration_transfer as transfer,
)
from mascope_tofwerk.calibration import tof_to_mass


T0 = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)

#: A node fit's record, as a source carries it.
FIT = {
    "mode": 0,
    "par": [10850.0, -31300.0],
    "status": "ok",
    "verified": True,
    "quality": {"n_points": 5},
}


def source(sample_file_id, minutes, par=(10850.0, -31300.0), mode=0):
    return transfer.Source(
        sample_file_id=sample_file_id,
        filename=f"file-{sample_file_id}",
        centre=T0 + timedelta(minutes=minutes),
        mode=mode,
        par=list(par),
    )


# --- Which records lend a calibration ---


def test_a_node_fit_that_cleared_the_bar_is_a_source():
    assert transfer.is_transfer_source(FIT)
    assert transfer.is_transfer_source(
        {**FIT, "mode": 2, "par": [10850.0, -31300.0, 0.4999]}
    )


@pytest.mark.parametrize(
    "record",
    [
        None,
        {**FIT, "status": "poor"},
        {**FIT, "verified": False},
        {**FIT, "status": "transferred"},
        {**FIT, "status": "unfitted"},
        {key: value for key, value in FIT.items() if key != "quality"},
        {**FIT, "mode": 1},
        {**FIT, "mode": True},
        {**FIT, "par": [10850.0]},
        {**FIT, "mode": 2},
        {**FIT, "par": [10850.0, float("nan")]},
        {**FIT, "par": "10850"},
    ],
    ids=[
        "none",
        "poor",
        "unverified",
        "transferred",
        "converter",
        "no quality",
        "mode 1",
        "mode true",
        "too few",
        "mode 2, two parameters",
        "nan",
        "not a list",
    ],
)
def test_any_other_record_is_not_a_source(record):
    assert not transfer.is_transfer_source(record)


# --- Which neighbours ---


def test_the_nearest_on_each_side_within_the_window():
    candidates = [
        source("a", -40),  # outside
        source("b", -20),
        source("c", -5),
        source("d", 10),
        source("e", 25),
        source("f", 31),  # outside
    ]
    before, after = transfer.nearest_sources(T0, candidates, 1800.0)
    assert (before.sample_file_id, after.sample_file_id) == ("c", "d")


def test_only_one_side_within_the_window():
    before, after = transfer.nearest_sources(
        T0, [source("a", -31), source("b", 12)], 1800.0
    )
    assert before is None
    assert after.sample_file_id == "b"
    before, after = transfer.nearest_sources(T0, [source("a", -30)], 1800.0)
    assert before.sample_file_id == "a"
    assert after is None


def test_none_within_the_window():
    assert transfer.nearest_sources(
        T0, [source("a", -31), source("b", 31)], 1800.0
    ) == (None, None)


def test_a_source_at_the_files_own_centre_counts_as_before():
    before, after = transfer.nearest_sources(T0, [source("a", 0)], 1800.0)
    assert before.sample_file_id == "a"
    assert after is None


def test_equally_near_sources_are_told_apart_by_id():
    candidates = [source("z", -5), source("a", -5), source("y", 5), source("b", 5)]
    for order in (candidates, candidates[::-1]):
        before, after = transfer.nearest_sources(T0, order, 1800.0)
        assert (before.sample_file_id, after.sample_file_id) == ("a", "b")


def test_a_files_centre_is_half_its_length_after_its_start():
    assert transfer.centre_time(T0, 600.0) == T0 + timedelta(minutes=5)
    assert transfer.centre_time(T0, None) == T0
    assert transfer.centre_time(T0, float("nan")) == T0
    assert transfer.centre_time(T0, -5.0) == T0


def test_a_length_of_a_day_or_more_is_read_as_unknown():
    """Stored lengths far beyond any acquisition exist; one no timedelta
    holds must not raise."""
    assert transfer.centre_time(T0, 86399.0) == T0 + timedelta(seconds=86399.0 / 2)
    assert transfer.centre_time(T0, 86400.0) == T0
    assert transfer.centre_time(T0, 1e15) == T0


# --- The parameters ---


def test_one_source_moves_by_the_two_cuts():
    """Each file's index starts after its own cut, so the same raw sample has
    index i in the source and i + k_source - k_file in the file: on it, the
    transfer gives every sample the m/z the source gives it."""
    fit = source("a", -5, par=(10850.0, -31300.0))
    record = transfer.transferred_record(T0, fit, None, {"": 12790, "a": 12802}, 1800.0)
    assert record["mode"] == 0
    assert record["par"] == [10850.0, -31300.0 + 12802 - 12790]
    raw = np.arange(20000.0, 300000.0, 5000.0)
    np.testing.assert_allclose(
        tof_to_mass(raw - 12790, record["mode"], record["par"]),
        tof_to_mass(raw - 12802, 0, fit.par),
        rtol=1e-12,
    )


def test_two_sources_are_interpolated_in_time():
    before = source("a", -6, par=(10850.0, -31300.0))
    after = source("b", 4, par=(10851.0, -31310.0))
    offsets = {"": 12800, "a": 12802, "b": 12805}
    record = transfer.transferred_record(T0, before, after, offsets, 1800.0)
    # 6 minutes after the one, 4 before the other: 0.6 of the way, so the
    # nearer one weighs 0.6.
    p1 = 0.4 * 10850.0 + 0.6 * 10851.0
    p2 = 0.4 * (-31300.0 + 12802) + 0.6 * (-31310.0 + 12805) - 12800
    np.testing.assert_allclose(record["par"], [p1, p2], rtol=1e-12)
    assert record["transfer"]["sources"] == [
        {"sample_file_id": "a", "gap_s": -360.0, "weight": pytest.approx(0.4)},
        {"sample_file_id": "b", "gap_s": 240.0, "weight": pytest.approx(0.6)},
    ]
    assert record["transfer"]["gap_s"] == 240.0


def test_a_mode_0_and_a_mode_2_source_are_interpolated_in_mode_2():
    before = source("a", -5, par=(10850.0, -31300.0), mode=0)
    after = source("b", 5, par=(10860.0, -31290.0, 0.4998), mode=2)
    offsets = {"": 0, "a": 0, "b": 0}
    record = transfer.transferred_record(T0, before, after, offsets, 1800.0)
    assert record["mode"] == 2
    np.testing.assert_allclose(
        record["par"], [10855.0, -31295.0, (0.5 + 0.4998) / 2], rtol=1e-12
    )


def test_a_mode_0_source_read_in_mode_2_is_the_same_axis():
    """p3 = 0.5 makes mode 2 mode 0, which is what the promotion rests on."""
    index = np.arange(20000.0, 300000.0, 5000.0)
    np.testing.assert_allclose(
        tof_to_mass(index, 2, [10850.0, -31300.0, 0.5]),
        tof_to_mass(index, 0, [10850.0, -31300.0]),
        rtol=1e-12,
    )


def test_the_record_says_it_is_transferred_and_from_what():
    record = transfer.transferred_record(
        T0, source("a", -10), None, {"": 5, "a": 7}, 900.0
    )
    assert record["status"] == "transferred"
    assert record["verified"] is True
    assert record["transfer"] == {
        "sources": [{"sample_file_id": "a", "gap_s": -600.0, "weight": 1.0}],
        "gap_s": 600.0,
        "window_s": 900.0,
        "raw_index_offset": 5,
    }
    assert transfer.is_transferred_record(record)
    assert not transfer.is_transfer_source(record)


def test_a_transfer_needs_a_source():
    with pytest.raises(ValueError):
        transfer.transferred_record(T0, None, None, {"": 0}, 1800.0)


def test_the_note_says_where_the_calibration_came_from():
    two = transfer.transferred_record(
        T0, source("a", -6), source("b", 4), {"": 0, "a": 0, "b": 0}, 1800.0
    )
    one = transfer.transferred_record(
        T0, source("a", -12), None, {"": 0, "a": 0}, 1800.0
    )
    assert transfer.transfer_note(two) == (
        "m/z calibration transferred from two neighbouring fitted files, "
        "interpolated in time, the nearest 4 min away."
    )
    assert transfer.transfer_note(one) == (
        "m/z calibration transferred from a neighbouring fitted file 12 min away."
    )
    assert transfer.transfer_note(FIT) is None


# --- The cut, read from the file ---


@pytest.fixture
def filestore(tmp_path, monkeypatch):
    """A filestore of its own, so no test reads or writes a real one."""
    monkeypatch.setattr(m_name.runtime.meta, "filestore", str(tmp_path))
    monkeypatch.setattr(m_name, "_INSTRUMENT_TYPE", {})
    return tmp_path


FILENAME = "TransferTof_2026.10.20_12h00m00s_ambient"


def write_tof(root, raw_axis: np.ndarray, *, h5: bool = True) -> str:
    """A TOF file whose raw axis is ``raw_axis`` and whose stored sum signal
    is it from 10 Th up, as the converter cuts it."""
    path = os.path.join(
        root,
        m_name.get_instrument_name(FILENAME),
        m_name.timestamp_from_filename(FILENAME).strftime("%Y.%m.%d"),
        FILENAME,
    )
    os.makedirs(path, exist_ok=True)
    if h5:
        with h5py.File(os.path.join(path, "data.h5"), "w") as h5_file:
            h5_file.create_dataset("FullSpectra/MassAxis", data=raw_axis)
    kept = raw_axis[raw_axis >= 10.0]
    xr.Dataset({"sum_signal": ("mz", np.ones(kept.size))}, coords={"mz": kept}).to_zarr(
        os.path.join(path, "sum_signal.zarr"), mode="w", consolidated=True
    )
    with open(os.path.join(path, ".props"), "w") as props:
        props.write('{"instrument_type": "tof"}')
    return FILENAME


def test_the_cut_is_the_raw_samples_below_10_th(filestore):
    raw = np.asarray(tof_to_mass(np.arange(60000.0), 0, [2830.0, -4200.0]))
    filename = write_tof(filestore, raw)
    expected = int((raw < 10.0).sum())
    assert expected > 0
    assert transfer.raw_index_offset(filename) == expected


def test_no_cut_without_the_tofwerk_file(filestore):
    raw = np.asarray(tof_to_mass(np.arange(60000.0), 0, [2830.0, -4200.0]))
    filename = write_tof(filestore, raw, h5=False)
    assert transfer.raw_index_offset(filename) is None


def test_no_cut_where_the_stored_axis_is_the_longer(filestore):
    raw = np.asarray(tof_to_mass(np.arange(60000.0), 0, [2830.0, -4200.0]))
    filename = write_tof(filestore, raw)
    with patch.object(transfer.m_compute, "get_sum_signal") as stored:
        stored.return_value = np.ones(raw.size + 1)
        assert transfer.raw_index_offset(filename) is None


# --- The plan ---


class _File:
    sample_file_id = "sf-1"
    filename = "file-sf-1"
    datetime_utc = T0 - timedelta(minutes=2)
    length = 240.0
    instrument = "TransferTof"
    polarity = "-"


@pytest.mark.asyncio
async def test_the_plan_reads_each_files_cut():
    candidates = [source("a", -8), source("b", 6, par=(10852.0, -31302.0))]
    offsets = {"file-sf-1": 100, "file-a": 110, "file-b": 120}
    with (
        patch.object(
            transfer, "candidate_sources", AsyncMock(return_value=candidates)
        ) as found,
        patch.object(transfer, "raw_index_offset", side_effect=offsets.get),
    ):
        plan = await transfer.plan_transfer(_File())
    assert found.call_args.args[1] == 1800.0
    assert plan.reason is None
    expected = transfer.transferred_record(
        T0, candidates[0], candidates[1], {"": 100, "a": 110, "b": 120}, 1800.0
    )
    assert plan.record == expected


@pytest.mark.asyncio
async def test_the_plan_with_no_neighbour_says_so():
    with patch.object(transfer, "candidate_sources", AsyncMock(return_value=[])):
        plan = await transfer.plan_transfer(_File())
    assert plan.record is None
    assert plan.reason == (
        "no fitted file of its instrument and polarity is within 30 minutes "
        "of it to transfer a calibration from"
    )


@pytest.mark.asyncio
async def test_the_plan_refuses_a_file_of_both_polarities():
    class Both(_File):
        polarity = "+-"

    with patch.object(transfer, "candidate_sources", AsyncMock()) as found:
        plan = await transfer.plan_transfer(Both())
    found.assert_not_called()
    assert plan.record is None
    assert "both polarities" in plan.reason


@pytest.mark.asyncio
@pytest.mark.parametrize("unread", ["file-sf-1", "file-a"])
async def test_the_plan_refuses_where_a_cut_cannot_be_read(unread):
    offsets = {"file-sf-1": 100, "file-a": 110}
    with (
        patch.object(
            transfer, "candidate_sources", AsyncMock(return_value=[source("a", -8)])
        ),
        patch.object(
            transfer,
            "raw_index_offset",
            side_effect=lambda name: None if name == unread else offsets[name],
        ),
    ):
        plan = await transfer.plan_transfer(_File())
    assert plan.record is None
    assert "raw TOF axis" in plan.reason
    assert ("the file" in plan.reason) == (unread == "file-sf-1")


@pytest.mark.asyncio
@pytest.mark.parametrize("unread", ["file-sf-1", "file-a"])
@pytest.mark.parametrize(
    "error",
    [OSError("Unable to open file (truncated file)"), KeyError("FullSpectra")],
)
async def test_the_plan_refuses_where_reading_a_cut_raises(unread, error):
    """A truncated or incomplete .h5 of the file or of a neighbour is a
    reason, not an error the pipeline would fail the file on."""
    offsets = {"file-sf-1": 100, "file-a": 110}

    def read(name):
        if name == unread:
            raise error
        return offsets[name]

    with (
        patch.object(
            transfer, "candidate_sources", AsyncMock(return_value=[source("a", -8)])
        ),
        patch.object(transfer, "raw_index_offset", side_effect=read),
    ):
        plan = await transfer.plan_transfer(_File())
    assert plan.record is None
    assert "raw TOF axis" in plan.reason
    assert ("the file" in plan.reason) == (unread == "file-sf-1")
