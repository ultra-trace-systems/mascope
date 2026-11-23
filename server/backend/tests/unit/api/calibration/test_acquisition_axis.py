"""
The acquisition axis a TOF fit's record keeps (``acquisition_axis``, MAS-937).

Each line's raw-axis offset is the acquisition axis's m/z at the fit's TOF
index of the line's exact m/z, against that exact m/z: read from the axis a
record keeps, from the per-line offsets it keeps where the axis could not be
had, or, on a record from before either, from the line's observed m/z. What a
fit about to be applied keeps comes from the record it replaces, the
converter's coefficients, or the raw ``.h5`` read again (``resolve``).
"""

import os
from datetime import datetime, timezone

import h5py
import numpy as np
import pytest
import xarray as xr

import mascope_file.name as m_name
from mascope_backend.api.controllers.calibration.lib import acquisition_axis as axis
from mascope_tofwerk.calibration import tof_to_mass


#: The acquisition axis: mode 0, read 80 ppm high against :data:`FIT`.
ACQUISITION = {"mode": 0, "par": [10850.0 / np.sqrt(1 + 80e-6), -18500.0]}
#: A fit of the file: the true axis.
FIT = {"mode": 0, "par": [10850.0, -18500.0]}
LINES = [61.9884, 78.9189, 124.9840, 187.9797]


def _point(target, observed_ppm, retained=True):
    return {
        "ion": "X-",
        "target_mz": target,
        "observed_mz": target * (1 + observed_ppm * 1e-6),
        "residual_ppm": 0.1,
        "retained": retained,
    }


def _record(points, fit=FIT, **keys):
    return {
        **fit,
        "status": "ok",
        "verified": True,
        "quality": {"points": points},
        **keys,
    }


# --- The closed forms ---


@pytest.mark.parametrize(
    "mode, par",
    [
        (0, [2830.0, -4200.0]),
        (1, [-2830.0, 9000.0]),
        (2, [2830.0, -4200.0, 0.4999]),
        (5, [3.1e-8, 1.2e-4, 0.4]),
    ],
)
def test_the_m_z_of_an_index_is_tofwerks(mode, par):
    index = np.array([12000.0, 30000.0, 55000.0])
    expected = tof_to_mass(index, mode, par)

    assert [axis.mz_at(i, mode, par) for i in index] == pytest.approx(
        list(expected), rel=1e-12
    )


@pytest.mark.parametrize(
    "mode, par",
    [(0, [2830.0, -4200.0]), (1, [-2830.0, 9000.0]), (2, [2830.0, -4200.0, 0.4999])],
)
def test_the_index_of_an_m_z_inverts_it(mode, par):
    for mz in (40.0, 150.0, 600.0):
        assert axis.mz_at(axis.index_at(mz, mode, par), mode, par) == pytest.approx(
            mz, rel=1e-12
        )


@pytest.mark.parametrize(
    "index, mode, par",
    [
        (100.0, 3, [1.0, 2.0, 3.0, 4.0]),
        (100.0, 0, [0.0, 1.0]),
        (100.0, 0, [1.0]),
        (100.0, 0, [True, 1.0]),
        (100.0, 2, [1.0, 200.0, 0.5]),
        (100.0, 2, [1.0, 2.0, 0.0]),
        (100.0, 1, [1.0, 100.0]),
        (float("nan"), 0, [1.0, 2.0]),
        (100.0, True, [1.0, 2.0]),
    ],
)
def test_where_tofwerks_m_z_is_not_a_number_there_is_none(index, mode, par):
    assert axis.mz_at(index, mode, par) is None


def test_a_mode_written_as_a_float_is_read():
    assert axis.mz_at(100.0, 0.0, [1.0, 2.0]) == pytest.approx(98.0**2)
    assert axis.mz_at(100.0, 0.5, [1.0, 2.0]) is None


def test_an_index_of_a_mode_without_a_closed_inverse_is_none():
    assert axis.index_at(100.0, 5, [1.0, 2.0, 3.0]) is None
    assert axis.index_at(0.0, 0, [1.0, 2.0]) is None


# --- A line's offset on the acquisition axis ---


def test_an_axis_gives_every_line_its_raw_offset_whatever_it_was_observed_at():
    """A refit observed its lines on the axis the first fit set, near 0 ppm;
    the axis kept still reads them 80 ppm high."""
    record = _record(
        [_point(mz, 0.3) for mz in LINES],
        acquisition_axis={**ACQUISITION, "raw_index_offset": 0},
    )

    assert axis.line_offsets(record) == pytest.approx([80.0] * 4, abs=1e-6)
    assert axis.axis_offset_ppm(record) == pytest.approx(80.0, abs=1e-6)


def test_the_axis_is_read_at_the_raw_index():
    """The stored axis starts 1,000 samples into the raw one: the fit's
    index is moved by them before the converter's coefficients read it."""
    raw = ACQUISITION["par"]
    shifted = {"mode": 0, "par": [raw[0], raw[1] - 1000.0]}
    fit = {"mode": 0, "par": [FIT["par"][0], FIT["par"][1] - 1000.0]}
    record = _record(
        [_point(mz, 0.0) for mz in LINES],
        fit=fit,
        acquisition_axis={**ACQUISITION, "raw_index_offset": 1000},
    )
    unshifted = _record(
        [_point(mz, 0.0) for mz in LINES],
        fit=fit,
        acquisition_axis={**shifted, "raw_index_offset": 0},
    )

    assert axis.line_offsets(record) == pytest.approx([80.0] * 4, abs=1e-6)
    assert axis.line_offsets(record) == pytest.approx(axis.line_offsets(unshifted))


def test_a_mode_2_fit_is_read_at_its_own_index():
    p3 = 0.49997
    grid = np.linspace(50.0, 250.0, 200)
    design = np.vstack([grid**p3, np.ones_like(grid)]).T
    (p1, p2), *_ = np.linalg.lstsq(
        design, FIT["par"][0] * np.sqrt(grid) + FIT["par"][1], rcond=None
    )
    record = _record(
        [_point(mz, 0.0) for mz in LINES],
        fit={"mode": 2, "par": [p1, p2, p3]},
        acquisition_axis={**ACQUISITION, "raw_index_offset": 0},
    )

    for mz, offset in zip(LINES, axis.line_offsets(record)):
        raw = tof_to_mass(p1 * mz**p3 + p2, 0, ACQUISITION["par"])
        assert offset == pytest.approx(1e6 * (raw - mz) / mz, abs=1e-6)


def test_an_axis_without_its_index_offset_reads_nothing():
    record = _record([_point(mz, 80.0) for mz in LINES], acquisition_axis=ACQUISITION)

    assert axis.line_offsets(record) == [None] * 4
    assert axis.axis_offset_ppm(record) is None


def test_kept_offsets_are_read_by_the_lines_exact_m_z():
    """The first fit's offsets, kept: a line the record lists and the kept
    ones do not has none, and the observed m/z is not read."""
    kept = [
        {"ion": "X-", "target_mz": LINES[0] + 1e-7, "offset_ppm": 81.0},
        {"ion": "X-", "target_mz": LINES[1], "offset_ppm": 79.0},
        {"ion": "X-", "target_mz": LINES[1], "offset_ppm": 1.0},
        "not an entry",
        {"ion": "X-", "target_mz": "62", "offset_ppm": 5.0},
    ]
    record = _record(
        [_point(LINES[0], 0.2), _point(LINES[1], 0.2), _point(LINES[2], 0.2)],
        acquisition_offsets=kept,
    )

    assert axis.line_offsets(record) == [81.0, 79.0, None]
    assert axis.axis_offset_ppm(record) == pytest.approx(80.0)


def test_a_record_with_neither_reads_its_observed_offsets():
    """An old record: what it observed, which is the acquisition axis's
    reading on a first fit."""
    record = _record([_point(LINES[0], 80.0), _point(LINES[1], 60.0, retained=False)])

    assert axis.line_offsets(record) == pytest.approx([80.0, 60.0])
    assert axis.axis_offset_ppm(record) == pytest.approx(80.0)
    assert not axis.knows_raw_axis(record)


def test_a_record_without_points_has_no_offsets():
    record = {**FIT, "acquisition_axis": {**ACQUISITION, "raw_index_offset": 0}}

    assert axis.line_offsets(record) is None
    assert axis.axis_offset_ppm(record) is None
    assert axis.knows_raw_axis(record)


# --- When a record was fitted ---


def test_fitted_at_is_read_in_utc():
    assert axis.fitted_at({"fitted_at": "2026-11-23T12:00:00.5+02:00"}) == datetime(
        2026, 11, 23, 10, 0, 0, 500000, tzinfo=timezone.utc
    )


@pytest.mark.parametrize(
    "value", [None, 5, "yesterday", "2026-11-23T12:00:00", "2026-11-23 12:00:00+00:00"]
)
def test_a_fitted_at_not_as_written_is_none(value):
    assert axis.fitted_at({"fitted_at": value}) is None


def test_stamp_drops_what_the_request_brought_and_dates_the_record():
    fit = {
        **FIT,
        "fitted_at": "2001-01-01T00:00:00+00:00",
        "acquisition_axis": {"mode": 0, "par": [1, 2], "raw_index_offset": 0},
        "acquisition_offsets": [{"target_mz": 62.0, "offset_ppm": 9.0}],
    }
    kept = axis.Acquisition("record", {"acquisition_axis": {**ACQUISITION}})
    before = datetime.now(timezone.utc)

    axis.stamp(fit, kept)

    assert fit["acquisition_axis"] == ACQUISITION
    assert "acquisition_offsets" not in fit
    assert before <= axis.fitted_at(fit) <= datetime.now(timezone.utc)

    orbitrap = {"mode": "one-point", "acquisition_axis": {"mode": 0}}
    axis.stamp(orbitrap, None)
    assert set(orbitrap) == {"mode", "fitted_at"}


# --- Where a fit's record keeps it from ---


@pytest.fixture
def filestore(tmp_path, monkeypatch):
    """A filestore of its own, so no test reads or writes a real one."""
    monkeypatch.setattr(m_name.runtime.meta, "filestore", str(tmp_path))
    monkeypatch.setattr(m_name, "_INSTRUMENT_TYPE", {})
    return tmp_path


FILENAME = "AxisTof_2026.10.20_12h00m00s_axis"

#: Raw samples the converter cuts below 10 Th on :data:`ACQUISITION`.
CUT = int((tof_to_mass(np.arange(20000.0), 0, ACQUISITION["par"]) < 10.0).sum())


def write_tof(root, *, h5: bool = True, n_raw: int = 20000, stored=None) -> str:
    """A TOF file whose raw axis is :data:`ACQUISITION` over ``n_raw``
    samples, and whose stored axis is ``stored`` (the raw one cut below
    10 Th when None) as its cached sum signal."""
    path = os.path.join(
        root,
        m_name.get_instrument_name(FILENAME),
        m_name.timestamp_from_filename(FILENAME).strftime("%Y.%m.%d"),
        FILENAME,
    )
    os.makedirs(path, exist_ok=True)
    raw = np.asarray(tof_to_mass(np.arange(float(n_raw)), 0, ACQUISITION["par"]))
    if h5:
        with h5py.File(os.path.join(path, "data.h5"), "w") as h5_file:
            group = h5_file.create_group("FullSpectra")
            group.create_dataset("MassAxis", data=raw.astype(np.float32))
            group.attrs["MassCalibMode"] = np.array([ACQUISITION["mode"]])
            group.attrs["MassCalibration nbrParameters"] = np.array([2])
            for i, value in enumerate(ACQUISITION["par"]):
                group.attrs[f"MassCalibration p{i + 1}"] = np.array([value])
    kept = raw[raw >= 10.0] if stored is None else stored
    xr.Dataset({"sum_signal": ("mz", np.ones(kept.size))}, coords={"mz": kept}).to_zarr(
        os.path.join(path, "sum_signal.zarr"), mode="w", consolidated=True
    )
    with open(os.path.join(path, ".props"), "w") as props:
        props.write('{"instrument_type": "tof"}')
    return FILENAME


CONVERTER = {**ACQUISITION, "status": "unfitted"}
FIRST_FIT = _record([_point(mz, 80.0) for mz in LINES])


def test_a_record_that_has_the_axis_hands_it_on(filestore):
    previous = {**FIRST_FIT, "acquisition_axis": {**ACQUISITION, "raw_index_offset": 7}}

    kept = axis.resolve(FILENAME, previous, FIRST_FIT, on_acquisition_axis=False)

    assert kept.source == "record"
    assert kept.keys == {"acquisition_axis": {**ACQUISITION, "raw_index_offset": 7}}


def test_a_first_fit_keeps_the_converters_axis_and_where_it_starts(filestore):
    """No ``.h5`` is read: the stored axis's first m/z says where the cut
    ended."""
    filename = write_tof(filestore, h5=False)

    kept = axis.resolve(filename, CONVERTER, FIRST_FIT, on_acquisition_axis=True)

    assert CUT > 0
    assert kept.source == "converter"
    assert kept.keys == {"acquisition_axis": {**ACQUISITION, "raw_index_offset": CUT}}


def test_a_failed_calibrations_marker_gives_the_converters_axis(filestore):
    filename = write_tof(filestore, h5=False)
    marker = {**ACQUISITION, "status": "failed", "verified": False}

    kept = axis.resolve(filename, marker, FIRST_FIT, on_acquisition_axis=True)

    assert kept.keys["acquisition_axis"]["raw_index_offset"] == CUT


def test_a_stored_axis_the_converters_does_not_give_reads_the_raw_file(filestore):
    filename = write_tof(filestore, stored=np.linspace(10.0, 300.0, 50))
    wrong = {"mode": 0, "par": [2000.0, 3.0], "status": "unfitted"}

    kept = axis.resolve(filename, wrong, FIRST_FIT, on_acquisition_axis=True)

    assert kept.source == "raw file"
    assert kept.keys["acquisition_axis"]["mode"] == 0


def test_a_refit_of_a_record_without_the_axis_reads_the_raw_file(filestore):
    filename = write_tof(filestore)

    kept = axis.resolve(filename, FIRST_FIT, FIRST_FIT, on_acquisition_axis=False)

    assert kept.source == "raw file"
    assert kept.keys == {
        "acquisition_axis": {
            "mode": 0,
            "par": pytest.approx(ACQUISITION["par"]),
            "raw_index_offset": CUT,
        }
    }


def test_the_raw_files_axis_reads_the_lines_as_the_converters_does(filestore):
    filename = write_tof(filestore)
    read = axis.read_raw_axis(filename)
    # A fit counts samples from the stored axis's first, CUT after the raw's.
    on_stored = {"mode": 0, "par": [FIT["par"][0], FIT["par"][1] - CUT]}
    record = _record(
        [_point(mz, 0.0) for mz in LINES], fit=on_stored, acquisition_axis=read
    )

    assert axis.line_offsets(record) == pytest.approx([80.0] * 4, abs=1e-3)


def test_a_refit_without_the_raw_file_keeps_the_replaced_records_offsets(filestore):
    filename = write_tof(filestore, h5=False)

    kept = axis.resolve(filename, FIRST_FIT, {**FIT}, on_acquisition_axis=False)

    assert kept.source == "kept offsets"
    offsets = kept.keys["acquisition_offsets"]
    assert [entry["target_mz"] for entry in offsets] == LINES
    assert [entry["offset_ppm"] for entry in offsets] == pytest.approx([80.0] * 4)


def test_kept_offsets_are_handed_on_as_they_are(filestore):
    """The record being replaced is itself a refit: its own lines read near
    zero, and what it kept of the first fit is what goes on."""
    filename = write_tof(filestore, h5=False)
    kept_before = [{"ion": "X-", "target_mz": LINES[0], "offset_ppm": 80.0}]
    previous = _record([_point(LINES[0], 0.1)], acquisition_offsets=kept_before)

    kept = axis.resolve(filename, previous, FIRST_FIT, on_acquisition_axis=False)

    assert kept.keys == {"acquisition_offsets": kept_before}


def _refit_from_before_the_keys(**keys):
    """A record written before the keys by a refit: its lines read near zero
    on the axis the first fit set."""
    record = _record([_point(mz, 0.1) for mz in LINES], **keys)
    record["quality"]["pre_fit_mz_error_ppm"] = 0.1
    return record


@pytest.mark.parametrize(
    "keys",
    [
        {"acquisition_drift": True, "acquisition_drift_ppm": 120.0},
        {"drift_cleared_at": "2026-01-02T03:04:05+00:00", "drift_cleared_by": 7},
    ],
    ids=["carried marker", "cleared marker"],
)
def test_a_record_that_says_it_was_a_refit_hands_on_no_offsets(filestore, keys):
    """Its lines were read on a moved axis, not the acquisition's: they are
    not kept as acquisition offsets, and the fit keeps neither key."""
    filename = write_tof(filestore, h5=False)
    previous = _refit_from_before_the_keys(**keys)

    assert axis.says_it_was_a_refit(previous)
    kept = axis.resolve(filename, previous, FIRST_FIT, on_acquisition_axis=False)

    assert kept == axis.Acquisition("none", {})


@pytest.mark.parametrize(
    "marker, pre_fit, refit",
    [
        (120.0, 120.0, False),
        (-120.0, -120.0, False),
        (120.0, 0.1, True),
        (120.0, None, True),
        (None, 120.0, False),
    ],
    ids=[
        "raised by its own lines",
        "raised low",
        "carried",
        "carried, no pre-fit error",
        "before the magnitude",
    ],
)
def test_a_marker_says_a_refit_only_when_it_is_not_the_records_own(
    marker, pre_fit, refit
):
    record = _record([_point(LINES[0], 0.1)], acquisition_drift=True)
    if marker is not None:
        record["acquisition_drift_ppm"] = marker
    if pre_fit is not None:
        record["quality"]["pre_fit_mz_error_ppm"] = pre_fit

    assert axis.says_it_was_a_refit(record) is refit
    # Without the marker there is nothing to tell a refit by
    record.pop("acquisition_drift")
    assert axis.says_it_was_a_refit(record) is False


def test_a_first_fit_with_no_axis_to_read_keeps_its_own_lines(filestore):
    """No coefficients on the record and no ``.h5``: the fit's own lines
    were observed on the acquisition axis."""
    filename = write_tof(filestore, h5=False)

    kept = axis.resolve(filename, None, FIRST_FIT, on_acquisition_axis=True)

    assert kept.source == "kept offsets"
    assert [e["offset_ppm"] for e in kept.keys["acquisition_offsets"]] == (
        pytest.approx([80.0] * 4)
    )


def test_nothing_to_keep_keeps_nothing(filestore):
    filename = write_tof(filestore, h5=False)

    kept = axis.resolve(filename, {**FIT, "status": "ok"}, FIT, False)

    assert kept == axis.Acquisition("none", {})


def test_an_unreadable_file_does_not_fail_the_fit(filestore, monkeypatch):
    filename = write_tof(filestore)

    def broken(_filename):
        raise OSError("disk gone")

    monkeypatch.setattr(axis, "read_raw_axis", broken)
    kept = axis.resolve(filename, FIRST_FIT, FIRST_FIT, on_acquisition_axis=False)

    assert kept.source == "kept offsets"


def test_a_raw_file_in_a_mode_the_trend_cannot_read_gives_no_axis(filestore):
    filename = write_tof(filestore)
    path = m_name.filename_to_datafile_path(filename)
    with h5py.File(path, "a") as h5_file:
        h5_file["FullSpectra"].attrs["MassCalibMode"] = np.array([3])

    assert axis.read_raw_axis(filename) is None
