"""
The TOF re-calibration runner: its configuration, its ledger, what it decides
for a file, how it gives a file's axes back, its pacing, and the matching
that follows.

The fit and the apply are the node's own (``calibrate_with_retry``), tested
beside it; here they are answered, and the runner is tested around them. The
axes are real stores on disk: what a rollback has to give back bit for bit.
"""

import json
import os
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import numpy as np
import pytest
import xarray as xr

import mascope_file.io as m_io
import mascope_file.name as m_name
from mascope_backend.api.controllers.calibration.lib.calibration_mz_fit import (
    TOF_CALIBRATION_NODE_VERSION,
    TofCalibrationHandler,
)
from mascope_backend.api.controllers.sample.files.process.service import (
    CalibrationOutcome,
)
from mascope_backend.api.models.calibration.calibration_pydantic_model import (
    CalibrationFitParams,
)
from mascope_backend.db.scripts import recalibrate_tof as script
from mascope_tofwerk.calibration import tof_to_mass


FILENAME = "RecalTof_2026.10.20_12h00m00s_recal"
#: The converter's coefficients: they count samples from the start of the
#: raw file's axis, which the converter cut CUT samples from
CONVERTER = {"mode": 0, "par": [10850.0, -18500.0]}
CUT = 4721
#: A fit the node applied: the same axis read 3 ppm higher
APPLIED = {"mode": 0, "par": [10850.0 * (1 - 1.5e-6), -18500.0 - CUT]}
N_SAMPLES = 3000
TARGET = {
    "collection": {"id": "c1", "isotopes_sha256": "abc"},
    "node_version": TOF_CALIBRATION_NODE_VERSION,
}


@pytest.fixture
def filestore(tmp_path, monkeypatch):
    monkeypatch.setattr(m_name.runtime.meta, "filestore", str(tmp_path))
    monkeypatch.setattr(m_name, "_INSTRUMENT_TYPE", {})
    return tmp_path


@pytest.fixture
def no_env(monkeypatch):
    for name in script.os.environ.copy():
        if name.startswith("TOF_RECAL_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("TOF_RECAL_INSTRUMENT", "TOF H")
    return monkeypatch


def _path(root) -> Path:
    return Path(root) / "RecalTof" / "2026.10.20" / FILENAME


def write_file(
    root,
    record: dict,
    *,
    offset: float = 0.0,
    peaks_on_axis: bool = False,
    zarr_signal: bool = False,
    variants: int = 0,
) -> str:
    """A TOF file on disk whose axes ``record`` gives from ``offset``.

    :param peaks_on_axis: Place the peaks' m/z where they lie on the stored
        axis, as detection does on the converter's, rather than where the
        record puts their TOF index, as an apply does
    :param zarr_signal: A file with no source, whose ``signal`` store an
        apply writes the axis of too
    :param variants: More cached sum signals beside ``sum_signal``
    """
    path = _path(root)
    path.mkdir(parents=True)
    index = np.arange(N_SAMPLES)
    axis = np.asarray(
        tof_to_mass(index + offset, record["mode"], record["par"]), np.float64
    ).astype(np.float32)
    for name in ["sum_signal", *(f"sum_signal_{i:012x}" for i in range(variants))]:
        xr.Dataset({name: ("mz", np.ones(N_SAMPLES))}, coords={"mz": axis}).to_zarr(
            path / f"{name}.zarr", mode="w", consolidated=True
        )
    if zarr_signal:
        xr.Dataset(
            {"signal": (("mz", "time"), np.ones((N_SAMPLES, 2)))},
            coords={"mz": axis, "time": [0.0, 1.0]},
        ).to_zarr(path / "signal.zarr", mode="w", consolidated=True)
    else:
        (path / "data.h5").write_bytes(b"")
    tofs = np.array([500.25, 1200.5, 2400.75])
    if peaks_on_axis:
        mzs = np.interp(tofs, index, axis.astype(np.float64)) * (1 + 1e-12)
    else:
        mzs = np.asarray(
            tof_to_mass(tofs + offset, record["mode"], record["par"]), np.float64
        )
    xr.Dataset(
        {"sum_peak_areas": ("mz", np.ones(3))},
        coords={"mz": mzs, "tof": ("mz", tofs)},
    ).to_zarr(path / "peak_timeseries.zarr", mode="w", consolidated=True)
    with open(path / ".props", "w") as props:
        json.dump(
            {
                "instrument_type": "tof",
                "polarity": "-",
                "mz_calibration": record,
                "range": [float(axis[0]), float(axis[-1])],
            },
            props,
        )
    return FILENAME


def everything(root) -> dict:
    """Every axis and the .props of the file, to compare bit for bit."""
    path = _path(root)
    out = {".props": json.loads((path / ".props").read_text())}
    for store in sorted(path.glob("*.zarr")):
        data = xr.open_zarr(store)
        for name in data.coords:
            out[f"{store.name}:{name}"] = data[name].values
    return out


def assert_same(before: dict, after: dict):
    assert before.keys() == after.keys()
    for key in before:
        if key == ".props":
            assert before[key] == after[key]
        else:
            assert before[key].dtype == after[key].dtype, key
            assert np.array_equal(before[key], after[key]), key


# --- Configuration ---


def test_the_script_can_be_run_and_takes_a_backup():
    from mascope_cli.pg import script_writes_nothing

    assert callable(script.main)
    # A run and a rollback write, so the runner takes a dump before it
    assert not script_writes_nothing(Path(script.__file__).read_text("utf-8"))


def test_the_instrument_is_required(no_env):
    no_env.delenv("TOF_RECAL_INSTRUMENT")
    with pytest.raises(ValueError, match="TOF_RECAL_INSTRUMENT"):
        script.read_config()


def test_the_default_is_a_paced_dry_run_over_every_file(no_env, filestore):
    config = script.read_config()

    assert config.action == script.DRY_RUN
    assert (config.first_day, config.last_day, config.mode) == (None, None, None)
    assert (config.batch_size, config.gap_s, config.backlog) == (25, 60.0, 20)
    assert config.limit == 0 and not config.retry
    assert config.band_edges == script.audit.DEFAULT_BAND_EDGES
    assert config.ledger_dir == str(filestore / "tof_recalibration" / "TOF H")


def test_everything_can_be_set(no_env):
    for name, value in {
        "TOF_RECAL_ACTION": "rollback",
        "TOF_RECAL_ROLLBACK": "batch:3, file:abc",
        "TOF_RECAL_FROM": "2026-04-01",
        "TOF_RECAL_TO": "2026-10-31",
        "TOF_RECAL_MODE": "Nitrate",
        "TOF_RECAL_BATCH": "10",
        "TOF_RECAL_GAP": "5",
        "TOF_RECAL_BACKLOG": "3",
        "TOF_RECAL_LIMIT": "100",
        "TOF_RECAL_RETRY": "1",
        "TOF_RECAL_BANDS": "50,150",
        "TOF_RECAL_LEDGER": "/elsewhere",
        "TOF_RECAL_JSON": "1",
    }.items():
        no_env.setenv(name, value)

    config = script.read_config()

    assert config.action == script.ROLLBACK
    assert config.rollback == ("batch:3", "file:abc")
    assert (str(config.first_day), str(config.last_day)) == ("2026-04-01", "2026-10-31")
    assert config.mode == "Nitrate"
    assert (config.batch_size, config.gap_s, config.backlog, config.limit) == (
        10,
        5.0,
        3,
        100,
    )
    assert config.retry and config.as_json
    assert config.band_edges == (50.0, 150.0)
    assert config.ledger_dir == "/elsewhere"


@pytest.mark.parametrize(
    ("settings", "message"),
    [
        ({"TOF_RECAL_ACTION": "apply"}, "TOF_RECAL_ACTION"),
        ({"TOF_RECAL_ACTION": "rollback"}, "TOF_RECAL_ROLLBACK"),
        ({"TOF_RECAL_ROLLBACK": "batch:1"}, "rollback"),
        ({"TOF_RECAL_ACTION": "rollback", "TOF_RECAL_ROLLBACK": "all"}, "batch"),
        ({"TOF_RECAL_ACTION": "rollback", "TOF_RECAL_ROLLBACK": "batch:x"}, "number"),
        ({"TOF_RECAL_FROM": "2026-10-02", "TOF_RECAL_TO": "2026-10-01"}, "after"),
        ({"TOF_RECAL_BATCH": "0"}, "below"),
        ({"TOF_RECAL_BANDS": "100,50"}, "rise"),
    ],
)
def test_what_cannot_be_read_stops_the_run(no_env, settings, message):
    for name, value in settings.items():
        no_env.setenv(name, value)
    with pytest.raises(ValueError, match=message):
        script.read_config()


# --- The ledger ---


def test_the_ledger_appends_numbered_events_and_reads_them_back(tmp_path):
    ledger = script.Ledger(str(tmp_path), writable=True)
    ledger.write({"event": "apply", "sample_file_id": "f1", "batch": 1})
    ledger.write({"event": "applied", "sample_file_id": "f1", "apply_seq": 1})

    again = script.Ledger(str(tmp_path), writable=True)

    assert [(e["event"], e["seq"]) for e in again.events] == [
        ("apply", 1),
        ("applied", 2),
    ]
    assert again.next_batch() == 2
    assert (tmp_path / script.SNAPSHOTS).is_dir()


def test_a_line_cut_short_is_passed_over_and_the_next_starts_whole(tmp_path):
    ledger = script.Ledger(str(tmp_path), writable=True)
    ledger.write({"event": "apply", "sample_file_id": "f1"})
    with open(ledger.path, "a") as cut:
        cut.write('{"event": "app')

    again = script.Ledger(str(tmp_path), writable=True)
    again.write({"event": "skipped", "sample_file_id": "f2"})

    lines = Path(ledger.path).read_text().splitlines()
    assert [e["event"] for e in again.events] == ["apply", "skipped"]
    assert json.loads(lines[-1])["sample_file_id"] == "f2"


def test_a_dry_run_writes_no_ledger(tmp_path):
    ledger = script.Ledger(str(tmp_path / "ledger"), writable=False)
    with pytest.raises(RuntimeError):
        ledger.write({"event": "apply"})
    assert not (tmp_path / "ledger").exists()


def _ledger(tmp_path, *events) -> script.Ledger:
    ledger = script.Ledger(str(tmp_path), writable=True)
    for event in events:
        ledger.write(dict(event))
    return ledger


def test_an_outcome_under_the_same_target_settles_a_file(tmp_path):
    other = {**TARGET, "node_version": TOF_CALIBRATION_NODE_VERSION - 1}
    ledger = _ledger(
        tmp_path,
        {"event": "kept", "sample_file_id": "kept", "mode_id": "m", "target": TARGET},
        {"event": "failed", "sample_file_id": "old", "mode_id": "m", "target": other},
        {"event": "skipped", "sample_file_id": "s", "mode_id": "m", "target": TARGET},
        {"event": "error", "sample_file_id": "e", "mode_id": "m", "target": TARGET},
        {"event": "apply", "sample_file_id": "r", "mode_id": "m", "target": TARGET},
        {"event": "rollback", "sample_file_id": "r", "mode_id": "m"},
    )

    settled = ledger.settled({"m": TARGET}.get)

    # An error is tried again; a file failed under an earlier target is
    # offered to the new one; a rolled back file stays as the operator left it
    assert settled == {"kept", "s", "r"}


def test_an_apply_no_outcome_followed_is_open(tmp_path):
    ledger = _ledger(
        tmp_path,
        {"event": "apply", "sample_file_id": "done"},
        {"event": "applied", "sample_file_id": "done", "apply_seq": 1},
        {"event": "apply", "sample_file_id": "cut", "before": {"record": 1}},
        {"event": "error", "sample_file_id": "cut", "apply_incomplete": True},
        {"event": "apply", "sample_file_id": "stopped"},
        # A later run's fault, kept or failed fit says nothing of the apply
        {"event": "apply", "sample_file_id": "later"},
        {"event": "error", "sample_file_id": "later", "error": "database down"},
        {"event": "kept", "sample_file_id": "later"},
        {"event": "failed", "sample_file_id": "later"},
        {"event": "apply", "sample_file_id": "back"},
        {"event": "restored", "sample_file_id": "back", "apply_seq": 10},
        {"event": "apply", "sample_file_id": "over"},
        {"event": "superseded", "sample_file_id": "over", "apply_seq": 12},
        {"event": "apply", "sample_file_id": "undone"},
        {"event": "rollback", "sample_file_id": "undone", "apply_seq": 14},
    )

    assert ledger.open_apply("done") is None
    assert ledger.open_apply("cut")["before"] == {"record": 1}
    assert ledger.open_apply("stopped")["seq"] == 5
    assert ledger.open_apply("later")["seq"] == 6
    assert ledger.open_apply("never") is None
    for closed in ("back", "over", "undone"):
        assert ledger.open_apply(closed) is None
    assert {
        file_id: event["seq"] for file_id, event in ledger.open_applies().items()
    } == {"cut": 3, "stopped": 5, "later": 6}


def test_a_file_with_an_open_apply_is_never_settled(tmp_path):
    ledger = _ledger(
        tmp_path,
        {"event": "apply", "sample_file_id": "a", "mode_id": "m", "target": TARGET},
        {"event": "kept", "sample_file_id": "a", "mode_id": "m", "target": TARGET},
        {"event": "apply", "sample_file_id": "b", "mode_id": "m", "target": TARGET},
        {"event": "failed", "sample_file_id": "b", "mode_id": "m", "target": TARGET},
        {"event": "apply", "sample_file_id": "c", "mode_id": "m", "target": TARGET},
        {"event": "kept", "sample_file_id": "c", "mode_id": "m", "target": TARGET},
        {"event": "restored", "sample_file_id": "c", "apply_seq": 5},
    )

    # c's apply was put back, so its kept fit stands
    assert ledger.settled({"m": TARGET}.get) == {"c"}


def test_a_rollback_takes_each_file_last_completed_apply(tmp_path):
    ledger = _ledger(
        tmp_path,
        {"event": "apply", "sample_file_id": "a", "batch": 1},
        {"event": "applied", "sample_file_id": "a", "apply_seq": 1},
        {"event": "apply", "sample_file_id": "b", "batch": 1},
        {"event": "applied", "sample_file_id": "b", "apply_seq": 3},
        {"event": "apply", "sample_file_id": "a", "batch": 2},
        {"event": "applied", "sample_file_id": "a", "apply_seq": 5},
        {"event": "apply", "sample_file_id": "c", "batch": 2},
        {"event": "rollback", "sample_file_id": "b", "apply_seq": 3},
    )

    batch_1 = script.rollback_events(ledger, ("batch:1",))
    batch_2 = script.rollback_events(ledger, ("batch:2",))
    file_a = script.rollback_events(ledger, ("file:a",))

    # a's last apply was batch 2's; b was rolled back; c never completed
    assert batch_1 == []
    assert [(e["sample_file_id"], e["seq"]) for e in batch_2] == [("a", 5)]
    assert [e["seq"] for e in file_a] == [5]


def test_what_is_left_to_match_and_the_batches_touched(tmp_path):
    ledger = _ledger(
        tmp_path,
        {"event": "apply", "sample_file_id": "a", "batches": ["b1", "b2"]},
        {"event": "applied", "sample_file_id": "a"},
        {"event": "matched", "sample_file_id": "a"},
        {"event": "apply", "sample_file_id": "c", "batches": ["b2"]},
        {"event": "applied", "sample_file_id": "c"},
        {"event": "rollback", "sample_file_id": "d", "batches": ["b3"]},
        {"event": "restored", "sample_file_id": "e", "batches": ["b4"]},
    )

    # A put-back apply never landed, so its matches were not removed
    assert [e["sample_file_id"] for e in ledger.unmatched()] == ["c", "d"]
    assert ledger.touched_batches() == {"b1", "b2", "b3", "b4"}


def test_a_stop_file_asks_the_run_to_stop(tmp_path):
    ledger = script.Ledger(str(tmp_path), writable=True)
    assert not ledger.stop_requested()
    (tmp_path / script.STOP_FILE).touch()
    assert ledger.stop_requested()


# --- The target and the record ---


@pytest.mark.parametrize(
    ("record", "on"),
    [
        ({"status": "ok", "quality": TARGET}, True),
        ({"status": "poor", "quality": TARGET}, True),
        ({"status": "failed", "quality": TARGET}, False),
        ({"status": "ok", "quality": {**TARGET, "node_version": 1}}, False),
        (
            {
                "status": "ok",
                "quality": {
                    **TARGET,
                    "collection": {"id": "c1", "isotopes_sha256": "x"},
                },
            },
            False,
        ),
        ({"status": "ok", "quality": {"n_points": 4}}, False),
        ({"mode": 0, "par": [1, 2], "verified": True}, False),
        (None, False),
    ],
)
def test_a_record_is_on_target_only_fitted_by_this_node_from_the_collection_now(
    record, on
):
    assert script.on_target(record, TARGET) is on


@pytest.mark.parametrize(
    ("record", "status"),
    [
        ({"status": "ok"}, "ok"),
        ({"status": "unfitted", "mode": 0, "par": []}, "unfitted"),
        ({"mode": 0, "par": [], "verified": True}, "legacy"),
        ({"mode": 0, "par": []}, "unfitted"),
        (None, "none"),
    ],
)
def test_the_kinds_of_record(record, status):
    assert script.record_status(record) == status


# --- The axes before ---


def test_an_applied_fit_gives_its_axes_back_alone(filestore):
    write_file(filestore, APPLIED)

    plan, kept = script.restore_plan(FILENAME, APPLIED)

    assert plan == {"offset": 0.0, "kept": []}
    assert kept == {}


def test_the_converter_axes_are_its_record_from_where_the_cut_ended(filestore):
    write_file(filestore, CONVERTER, offset=CUT, peaks_on_axis=True)

    plan, kept = script.restore_plan(FILENAME, CONVERTER)

    # The axes from the record, but the peaks where detection found them
    assert plan == {"offset": float(CUT), "kept": ["peak_timeseries"]}
    assert set(kept) == {"peak_timeseries"}


def test_axes_no_record_gives_back_are_kept(filestore):
    write_file(filestore, APPLIED, zarr_signal=True)

    plan, kept = script.restore_plan(FILENAME, {"mode": 0, "par": [10000.0, -1.0]})

    assert plan["offset"] is None
    assert set(kept) == {"sum_signal", "signal", "peak_timeseries"}


def test_a_record_with_no_inverse_is_tried_at_the_start_only(filestore):
    write_file(filestore, APPLIED)

    plan, _ = script.restore_plan(FILENAME, {"mode": 5, "par": [1e-6, 0.01, 1.0]})

    assert plan["offset"] is None


@pytest.mark.parametrize(
    ("record", "offset", "options"),
    [
        (APPLIED, 0.0, {}),
        (CONVERTER, CUT, {"peaks_on_axis": True}),
        (APPLIED, 0.0, {"zarr_signal": True, "variants": 2}),
        (CONVERTER, CUT, {"zarr_signal": True, "peaks_on_axis": True}),
    ],
    ids=["applied fit", "converter", "signal and cached sums", "converter, signal"],
)
@pytest.mark.asyncio
async def test_a_rollback_gives_every_axis_and_the_props_back_bit_for_bit(
    filestore, tmp_path, record, offset, options
):
    write_file(filestore, record, offset=offset, **options)
    original = everything(filestore)
    props = script._props_before(FILENAME)
    plan, kept = script.restore_plan(FILENAME, props["mz_calibration"])
    ledger = script.Ledger(str(tmp_path / "ledger"), writable=True)
    if kept:
        plan["snapshot"] = script._snapshot_path(ledger, "f1")
        script.write_snapshot(ledger.directory, plan["snapshot"], kept)

    await TofCalibrationHandler(FILENAME).apply(
        {"mode": 0, "par": [10850.0 * (1 + 2e-5), -18500.0], "quality": {}}
    )
    assert not np.array_equal(
        everything(filestore)["sum_signal.zarr:mz"], original["sum_signal.zarr:mz"]
    )

    if plan.get("snapshot"):
        with np.load(os.path.join(ledger.directory, plan["snapshot"])) as arrays:
            kept = {name: arrays[name] for name in arrays.files}
    await script.RestoreHandler(FILENAME, {"props": props}, plan, kept).apply({})

    assert_same(original, everything(filestore))


@pytest.mark.asyncio
async def test_a_props_key_the_file_lacked_is_taken_out_again(filestore):
    write_file(filestore, APPLIED)
    path = _path(filestore) / ".props"
    props = json.loads(path.read_text())
    del props["range"]
    path.write_text(json.dumps(props))
    before = script._props_before(FILENAME)
    plan, kept = script.restore_plan(FILENAME, APPLIED)

    await TofCalibrationHandler(FILENAME).apply({"mode": 0, "par": [10851.0, -1.0]})
    assert "range" in m_io.read_props(FILENAME)
    await script.RestoreHandler(FILENAME, {"props": before}, plan, kept).apply({})

    assert m_io.read_props(FILENAME) == props


#: The record of a file whose axes it does not give from any offset: every
#: axis is kept beside the ledger
NO_OFFSET = {"mode": 0, "par": [10851.0, -18000.0]}


def _write_unfitting(root) -> None:
    write_file(root, APPLIED)
    path = _path(root) / ".props"
    props = json.loads(path.read_text())
    props["mz_calibration"] = NO_OFFSET
    path.write_text(json.dumps(props))


def _add_cache(root, name="sum_signal_00000000abcd", size=None) -> None:
    """A cached sum signal on the file's axis as it is now, as a time window
    of the file leaves one."""
    axis = xr.open_zarr(_path(root) / "sum_signal.zarr")["mz"].values
    if size is not None:
        axis = axis[:size]
    xr.Dataset({name: ("mz", np.ones(axis.size))}, coords={"mz": axis}).to_zarr(
        _path(root) / f"{name}.zarr", mode="w", consolidated=True
    )


async def _apply_then_plan(filestore, ledger_dir):
    """The file's plan and kept arrays, as the ledger keeps them, and an
    apply of another fit after."""
    props = script._props_before(FILENAME)
    plan, kept = script.restore_plan(FILENAME, props["mz_calibration"])
    await TofCalibrationHandler(FILENAME).apply(
        {"mode": 0, "par": [10850.0 * (1 + 2e-5), -18500.0], "quality": {}}
    )
    return {"props": props}, plan, kept


@pytest.mark.parametrize("offset", [True, False], ids=["offset", "no offset"])
@pytest.mark.asyncio
async def test_a_cache_made_after_the_apply_goes_back_to_the_files_old_axis(
    filestore, tmp_path, offset
):
    if offset:
        write_file(filestore, CONVERTER, offset=CUT, peaks_on_axis=True)
    else:
        _write_unfitting(filestore)
    original = everything(filestore)
    before, plan, kept = await _apply_then_plan(filestore, tmp_path)
    assert (plan["offset"] is not None) == offset
    _add_cache(filestore)

    await script.RestoreHandler(FILENAME, before, plan, kept).apply({})

    after = everything(filestore)
    cache = after.pop("sum_signal_00000000abcd.zarr:mz")
    assert_same(original, after)
    assert np.array_equal(cache, original["sum_signal.zarr:mz"])


@pytest.mark.parametrize(
    "size", [N_SAMPLES - 10, 3], ids=["part of the axis", "as many as the peaks"]
)
@pytest.mark.asyncio
async def test_a_store_no_axis_can_be_decided_for_leaves_the_file_as_it_is(
    filestore, tmp_path, size
):
    _write_unfitting(filestore)
    before, plan, kept = await _apply_then_plan(filestore, tmp_path)
    # A cache no kept axis is the size of: the peaks' m/z are no axis
    _add_cache(filestore, size=size)
    applied = everything(filestore)

    with pytest.raises(script.CannotRestore, match="sum_signal_00000000abcd"):
        await script.RestoreHandler(FILENAME, before, plan, kept).apply({})

    assert_same(applied, everything(filestore))


@pytest.mark.asyncio
async def test_a_new_store_is_not_given_one_of_two_kept_axes_that_disagree(
    filestore, tmp_path
):
    _write_unfitting(filestore)
    _add_cache(filestore, "sum_signal_000000000001")
    before, plan, kept = await _apply_then_plan(filestore, tmp_path)
    kept["sum_signal_000000000001"] = kept["sum_signal"] * np.float32(1.5)
    _add_cache(filestore)

    with pytest.raises(script.CannotRestore, match="sum_signal_00000000abcd"):
        script.restore_axes(FILENAME, before, plan, kept)


@pytest.mark.parametrize(
    ("one", "other", "same"),
    [
        ({"mode": 0, "par": [1.0]}, {"mode": 0, "par": [1.0], "status": "ok"}, True),
        ({"mode": 0, "par": [1.0]}, {"mode": 0, "par": [1.5]}, False),
        ({"mode": 0, "par": [1.0]}, {"mode": 2, "par": [1.0]}, False),
        # A failure marker the node made where there was no record
        (None, {"status": "failed", "verified": False}, True),
        (None, {"mode": 0, "par": [1.0]}, False),
        # A refit to the parameters the file had, stamped by the target
        (
            {"mode": 0, "par": [1.0], "quality": {**TARGET, "n_points": 5}},
            {"mode": 0, "par": [1.0], "quality": {**TARGET, "collection": "old"}},
            False,
        ),
        (
            {"mode": 0, "par": [1.0], "quality": TARGET},
            {"mode": 0, "par": [1.0], "quality": {**TARGET, "node_version": 1}},
            False,
        ),
        # The ledger's compact record of an applied one
        (
            {"mode": 0, "par": [1.0], "status": "ok", "quality": TARGET},
            script._compact({"mode": 0, "par": [1.0], "quality": TARGET}),
            True,
        ),
    ],
)
def test_two_records_are_the_same_fit_by_their_parameters_and_stamp(one, other, same):
    assert script._same_fit(one, other) is same
    assert script._same_fit(other, one) is same


def test_peaks_stored_after_the_apply_with_no_offset_cannot_be_restored(filestore):
    _write_unfitting(filestore)
    plan, kept = script.restore_plan(FILENAME, NO_OFFSET)
    del kept["peak_timeseries"]

    with pytest.raises(script.CannotRestore, match="peaks"):
        script.restore_axes(FILENAME, {"props": {}}, plan, kept)


# --- The axis change per band ---


def test_a_fit_that_reads_the_axis_higher_moves_every_band_by_as_much():
    old_fit = {"mode": 0, "par": [10850.0, -18500.0]}
    # sqrt(m) scaled by 1 - 1.5e-6 reads every m/z 3 ppm higher
    new_fit = {"mode": 0, "par": [10850.0 * (1 - 1.5e-6), -18500.0]}

    bands = script.axis_change(
        lambda index: script._formula(index, old_fit), new_fit, 400_000, (100.0, 300.0)
    )

    assert [band["band"] for band in bands] == ["<100", "100-300", "300+"]
    for band in bands:
        assert band["median"] == pytest.approx(3.0, abs=1e-3)
        assert band["min"] == pytest.approx(3.0, abs=1e-3)


def test_a_band_the_axis_does_not_reach_is_left_out():
    fit = {"mode": 0, "par": [10850.0, -18500.0]}
    bands = script.axis_change(
        lambda index: script._formula(index, fit), fit, 3000, (40.0, 100.0)
    )
    assert [band["band"] for band in bands] == ["<40"]
    assert script.axis_change(lambda index: index, fit, 0, (40.0,)) == []


# --- One file, run ---


def _candidate(record=None, **update) -> script.Candidate:
    values = {
        "sample_file_id": "f1",
        "filename": FILENAME,
        "acquired": datetime(2026, 10, 20, 12, tzinfo=UTC),
        "record": record,
        "range": [10.0, 300.0],
        "instrument_function_id": "if1",
        "samples": (
            {
                "sample_item_id": "s1",
                "sample_item_name": "s1",
                "mode_id": "m1",
                "mode_name": "Nitrate",
                "collection_id": "c1",
            },
        ),
    }
    return script.Candidate(**{**values, **update})


def _target() -> script.ModeTarget:
    return script.ModeTarget(
        mode=SimpleNamespace(ionization_mode_id="m1", ionization_mode_name="Nitrate"),
        params=None,
        isotopes=None,
        target=TARGET,
    )


#: A fit as the node hands it to ``before_apply``
FIT_OK = {"mode": 0, "par": list(APPLIED["par"]), "quality": {"n_points": 5}}


def _row(record, range_=(10.0, 300.0)):
    """A sample file's row as ``_file_state`` reads it."""
    return SimpleNamespace(mz_calibration=record, range=list(range_))


def _node(fit, stored, outcome=None, issues=(), turn=None, batches=None):
    """``calibrate_with_retry`` as it runs: the fit offered to the hook, the
    record the database then holds, and the outcome.

    :param fit: A fit, or a list of them: one per attempt, as when an apply
        fails with a status the node retries on
    :param stored: The record the database holds after the node
    :param issues: The quality issues of every fit, or a function of a fit's
        quality that answers them
    :param turn: The file's row when its turn comes
    :param batches: ``_other_batches`` as it answers
    """
    seen = {"offered": []}
    fits = fit if isinstance(fit, list) else [fit]

    async def calibrate(sample, sample_file_id, process_id, before_apply):
        seen["sample"], seen["process_id"] = sample, process_id
        if fits[0] is None:
            return outcome or CalibrationOutcome(verified=False, reason="no peaks")
        for attempt in fits:
            seen["applied"] = await before_apply(attempt)
            seen["offered"].append(seen["applied"])
            if not seen["applied"]:
                return CalibrationOutcome(
                    verified=False, reason="withheld", withheld=True
                )
        return outcome or CalibrationOutcome(verified=True)

    rows = [turn, _row(stored)]

    async def file_state(sample_file_id):
        return rows.pop(0) if len(rows) > 1 else rows[0]

    return (
        patch.object(script, "calibrate_with_retry", calibrate),
        patch.object(
            script,
            "calibration_quality_issues",
            lambda quality, filename: [
                {"code": code}
                for code in (issues(quality) if callable(issues) else issues)
            ],
        ),
        patch.object(script, "_file_state", file_state),
        patch.object(
            script,
            "_other_batches",
            batches or AsyncMock(return_value={"b9": "rematch"}),
        ),
        patch.object(script, "_assignment_runs", AsyncMock(return_value=["run1"])),
        seen,
    )


_TURN = object()


async def _run_file(tmp_path, candidate, fit, stored, turn=_TURN, ledger=None, **node):
    ledger = ledger or script.Ledger(str(tmp_path / "ledger"), writable=True)
    if turn is _TURN:
        turn = _row(candidate.record, candidate.range)
    *patches, seen = _node(fit, stored, turn=turn, **node)
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        outcome = await script.run_file(
            candidate, _target(), ledger, 7, script.audit.DEFAULT_BAND_EDGES
        )
    return outcome, ledger, seen


@pytest.mark.asyncio
async def test_a_poor_fit_never_replaces_an_ok_record(filestore, tmp_path):
    write_file(filestore, APPLIED)
    old = {**APPLIED, "status": "ok", "verified": True, "quality": {}}

    outcome, ledger, seen = await _run_file(
        tmp_path, _candidate(old), FIT_OK, old, issues=("residual",)
    )

    assert outcome.kind == "kept"
    assert seen["applied"] is False
    (event,) = ledger.events
    assert event["event"] == "kept"
    assert event["old"]["status"] == "ok"
    assert event["new"]["status"] == "poor"
    assert event["issues"] == ["residual"]
    assert event["target"] == TARGET


@pytest.mark.parametrize("old_status", ["poor", "legacy", "unfitted", "failed"])
@pytest.mark.asyncio
async def test_a_poor_fit_replaces_any_other_record(filestore, tmp_path, old_status):
    write_file(filestore, APPLIED)
    old = {
        "poor": {**APPLIED, "status": "poor", "verified": True},
        "legacy": {**APPLIED, "verified": True},
        "unfitted": {**CONVERTER, "status": "unfitted"},
        "failed": {**CONVERTER, "status": "failed", "verified": False},
    }[old_status]
    stored = {**FIT_OK, "status": "poor", "verified": True}

    outcome, ledger, seen = await _run_file(
        tmp_path, _candidate(old), FIT_OK, stored, issues=("residual",)
    )

    assert seen["applied"] is True
    assert outcome.kind == "poor"
    assert [e["event"] for e in ledger.events] == ["apply", "applied"]


@pytest.mark.asyncio
async def test_the_ledger_has_what_the_file_had_before_the_apply(filestore, tmp_path):
    write_file(filestore, APPLIED)
    old = {**APPLIED, "verified": True}
    stored = {**FIT_OK, "status": "ok", "verified": True}

    outcome, ledger, seen = await _run_file(tmp_path, _candidate(old), FIT_OK, stored)

    apply, applied = ledger.events
    assert outcome.kind == "ok"
    assert apply["event"] == "apply" and apply["batch"] == 7
    assert apply["before"] == {
        "record": old,
        "range": [10.0, 300.0],
        "props": script._props_before(FILENAME),
    }
    assert apply["restore"] == {"offset": 0.0, "kept": []}
    assert apply["old"]["status"] == "legacy"
    assert apply["new"]["par"] == FIT_OK["par"]
    assert apply["batches"] == ["b9"]
    assert apply["assignment_runs"] == ["run1"]
    assert [band["band"] for band in apply["bands"]]
    assert applied["apply_seq"] == apply["seq"]
    assert (applied["status"], applied["verified"]) == ("ok", True)
    # The node runs on the file's calibrating sample, in a process of its own
    assert seen["sample"]["sample_item_id"] == "s1"
    assert seen["process_id"]


@pytest.mark.asyncio
async def test_a_converter_file_keeps_its_peaks_beside_the_ledger(filestore, tmp_path):
    write_file(filestore, CONVERTER, offset=CUT, peaks_on_axis=True)
    stored = {**FIT_OK, "status": "ok", "verified": True}

    _, ledger, _ = await _run_file(
        tmp_path, _candidate({**CONVERTER, "status": "unfitted"}), FIT_OK, stored
    )

    plan = ledger.events[0]["restore"]
    assert plan["offset"] == CUT
    assert plan["snapshot"] == os.path.join(script.SNAPSHOTS, "f1.1.npz")
    with np.load(os.path.join(ledger.directory, plan["snapshot"])) as kept:
        assert kept.files == ["peak_timeseries"]


#: A second attempt's fit, when the node retried after an apply that failed
FIT_AGAIN = {"mode": 0, "par": [10850.0 * (1 - 1e-6), -18500.0 - CUT], "quality": {}}


@pytest.mark.asyncio
async def test_a_retried_apply_keeps_what_the_file_had_before_the_first(
    filestore, tmp_path
):
    write_file(filestore, CONVERTER, offset=CUT, peaks_on_axis=True)
    old = {**CONVERTER, "status": "unfitted"}
    stored = {**FIT_AGAIN, "status": "ok", "verified": True}
    batches = AsyncMock(
        side_effect=[{"b1": "recalibrate", "b2": "ready"}, {"b1": "processing"}]
    )

    outcome, ledger, seen = await _run_file(
        tmp_path, _candidate(old), [FIT_OK, FIT_AGAIN], stored, batches=batches
    )

    first, second, applied = ledger.events
    assert seen["offered"] == [True, True]
    assert outcome.kind == "ok"
    assert (first["event"], second["event"], applied["event"]) == (
        "apply",
        "apply",
        "applied",
    )
    # The first attempt's apply may have written the axes: the second keeps
    # what the file had before the first, and the batch it held still waits
    # on its recalibration
    assert second["before"] == first["before"]
    assert second["restore"] == first["restore"]
    assert second["recalibrate"] == ["b1"]
    assert applied["apply_seq"] == second["seq"]
    assert ledger.open_apply("f1") is None


def _second_attempt(quality):
    if quality.get("poor"):
        return ["residual"]
    if quality.get("unreadable"):
        raise OSError("disk full")
    return []


@pytest.mark.parametrize(
    ("second", "events", "error"),
    [
        ({"poor": True}, ["apply", "kept", "error"], "withheld"),
        ({"unreadable": True}, ["apply", "error"], "OSError: disk full"),
    ],
    ids=["kept", "the runner failing"],
)
@pytest.mark.asyncio
async def test_an_attempt_after_an_apply_that_did_not_land_puts_the_file_back(
    filestore, tmp_path, second, events, error
):
    write_file(filestore, APPLIED)
    old = {"mode": 0, "par": [10849.0, -18000.0], "status": "ok", "verified": True}
    close = AsyncMock(return_value="restored")

    with patch.object(script, "close_open_apply", close):
        outcome, ledger, seen = await _run_file(
            tmp_path,
            _candidate(old),
            [FIT_OK, {**FIT_AGAIN, "quality": second}],
            old,
            issues=_second_attempt,
            outcome=CalibrationOutcome(verified=False, reason="lock", fault=True),
        )

    assert seen["offered"] == [True, False]
    assert [e["event"] for e in ledger.events] == events
    assert ledger.events[-1]["apply_incomplete"] is True
    assert ledger.events[-1]["error"] == error
    assert outcome.kind == "error"
    close.assert_awaited_once()
    assert close.await_args.args[0]["seq"] == 1


@pytest.mark.asyncio
async def test_a_fit_that_fails_is_listed_and_the_record_left_to_the_node(
    filestore, tmp_path
):
    write_file(filestore, APPLIED)
    old = {**APPLIED, "status": "ok", "verified": True}

    outcome, ledger, _ = await _run_file(tmp_path, _candidate(old), None, old)

    assert outcome.kind == "failed"
    (event,) = ledger.events
    assert (event["event"], event["reason"], event["record"]) == (
        "failed",
        "no peaks",
        "ok",
    )


@pytest.mark.asyncio
async def test_a_fault_is_an_error_the_next_run_tries_again(filestore, tmp_path):
    write_file(filestore, APPLIED)
    fault = CalibrationOutcome(verified=False, reason="database down", fault=True)

    outcome, ledger, _ = await _run_file(
        tmp_path, _candidate(None), None, None, outcome=fault
    )

    assert outcome.kind == "error"
    assert ledger.events[0]["event"] == "error"
    assert ledger.settled({"m1": TARGET}.get) == set()


@pytest.mark.asyncio
async def test_an_apply_that_did_not_land_is_put_back_at_once(filestore, tmp_path):
    write_file(filestore, APPLIED)
    # The record the apply failed to replace: other parameters than the fit's
    old = {"mode": 0, "par": [10849.0, -18000.0], "verified": True}
    close = AsyncMock(return_value="restored")

    with patch.object(script, "close_open_apply", close):
        outcome, ledger, _ = await _run_file(
            tmp_path,
            _candidate(old),
            FIT_OK,
            old,
            outcome=CalibrationOutcome(verified=False, reason="lock", fault=True),
        )

    assert outcome.kind == "error"
    assert [e["event"] for e in ledger.events] == ["apply", "error"]
    assert ledger.events[1]["apply_incomplete"] is True
    assert ledger.events[1]["error"] == "lock"
    close.assert_awaited_once()
    assert close.await_args.args[0] is ledger.events[0]


@pytest.mark.asyncio
async def test_an_apply_that_did_not_land_is_told_by_its_stamp_too(filestore, tmp_path):
    """A refit from the same peaks and calibrants gives the parameters the
    file had: only the stamp of what it was fitted from is new."""
    write_file(filestore, APPLIED)
    old = {**APPLIED, "status": "ok", "verified": True}
    old["quality"] = {**TARGET, "collection": {"id": "c1", "isotopes_sha256": "old"}}
    refit = {**APPLIED, "quality": {**TARGET, "n_points": 5}}
    close = AsyncMock(return_value="restored")

    with patch.object(script, "close_open_apply", close):
        outcome, ledger, _ = await _run_file(
            tmp_path,
            _candidate(old),
            refit,
            old,
            outcome=CalibrationOutcome(verified=False, reason="lock", fault=True),
        )

    assert outcome.kind == "error"
    assert [e["event"] for e in ledger.events] == ["apply", "error"]
    close.assert_awaited_once()


@pytest.mark.asyncio
async def test_an_apply_that_cannot_be_put_back_is_left_to_the_next_run(
    filestore, tmp_path
):
    write_file(filestore, APPLIED)
    old = {"mode": 0, "par": [10849.0, -18000.0], "verified": True}
    close = AsyncMock(side_effect=OSError("disk full"))

    with patch.object(script, "close_open_apply", close):
        outcome, ledger, _ = await _run_file(
            tmp_path,
            _candidate(old),
            FIT_OK,
            old,
            outcome=CalibrationOutcome(verified=False, reason="lock", fault=True),
        )

    assert outcome.kind == "error"
    assert [e["event"] for e in ledger.events] == ["apply", "error", "error"]
    assert ledger.events[2]["open_apply"] == 1
    assert ledger.open_apply("f1")["seq"] == 1


@pytest.mark.parametrize("status", ["ok", "poor"])
@pytest.mark.asyncio
async def test_a_fault_after_the_record_landed_finishes_the_apply(
    filestore, tmp_path, status
):
    """The node's match removal or its batches' status failing after it wrote
    the record: what the apply left undone is done now, as for a run cut off
    there, and the file is applied."""
    write_file(filestore, APPLIED)
    old = {**APPLIED, "verified": True}
    stored = {**FIT_OK, "status": status, "verified": True}
    # The node held the batches at processing and never let them go
    stack, mocks = _controllers(
        _Row(sample_file_id="f1", filename=FILENAME, mz_calibration=stored, range=[]),
        [
            ("a1", "ACQUISITION", "ready"),
            ("b1", "ANALYSIS", "processing"),
            ("b2", "ANALYSIS", "processing"),
        ],
    )
    with stack:
        outcome, ledger, _ = await _run_file(
            tmp_path,
            _candidate(old),
            FIT_OK,
            stored,
            outcome=CalibrationOutcome(
                verified=False, reason="statement timeout", fault=True
            ),
            batches=AsyncMock(return_value={"b1": "ready", "b2": "recalibrate"}),
            issues=("residual",) if status == "poor" else (),
        )

    assert outcome.kind == status
    assert outcome.detail == {"error": "statement timeout"}
    apply, error, applied = ledger.events
    assert (error["event"], error["apply_incomplete"]) == ("error", True)
    assert (applied["event"], applied["apply_seq"]) == ("applied", apply["seq"])
    assert _removed(mocks) == [("s1", True), ("s2", True)]
    assert _statuses(mocks) == [
        ("recalibrate", ["b2"], False),
        ("rematch", ["b1", "b2"], True),
    ]
    assert mocks["update_sample_file"].await_count == 0
    # Matched by the run's settle, undone by a rollback, not offered again
    assert ledger.open_apply("f1") is None
    assert [e["sample_file_id"] for e in ledger.unmatched()] == ["f1"]
    assert script.rollback_events(ledger, ("file:f1",)) == [apply]
    assert ledger.settled({"m1": TARGET}.get) == {"f1"}


@pytest.mark.parametrize("fails", ["restore_plan", "write_snapshot", "_other_batches"])
@pytest.mark.asyncio
async def test_the_runner_failing_before_an_apply_is_its_own_error(
    filestore, tmp_path, fails
):
    """Not the file's calibration failing: the node never hears of it, so it
    logs no error and leaves no failure marker over the converter's record."""
    write_file(filestore, CONVERTER, offset=CUT, peaks_on_axis=True)
    old = {**CONVERTER, "status": "unfitted"}

    failing = AsyncMock(side_effect=OSError("disk full"))
    with patch.object(script, fails, side_effect=OSError("disk full")):
        outcome, ledger, seen = await _run_file(
            tmp_path,
            _candidate(old),
            FIT_OK,
            old,
            batches=failing if fails == "_other_batches" else None,
        )

    # The fit is withheld, so the node applies and records nothing
    assert seen["applied"] is False
    assert outcome.kind == "error"
    (event,) = ledger.events
    assert event["event"] == "error"
    assert event["error"] == "OSError: disk full"
    assert "apply_incomplete" not in event
    # Tried again by the next run
    assert ledger.settled({"m1": TARGET}.get) == set()
    assert ledger.open_apply("f1") is None


@pytest.mark.asyncio
async def test_the_file_is_read_again_at_its_turn(filestore, tmp_path):
    write_file(filestore, APPLIED)
    # Chosen on its converter record; calibrated by a person since
    chosen = _candidate({**CONVERTER, "status": "unfitted"}, range=[1.0, 2.0])
    now = {**APPLIED, "status": "ok", "verified": True, "quality": {}}

    outcome, ledger, _ = await _run_file(
        tmp_path,
        chosen,
        FIT_OK,
        now,
        turn=_row(now, (11.0, 299.0)),
        issues=("residual",),
    )

    # The ok rule judges what the file holds now
    assert outcome.kind == "kept"
    assert ledger.events[0]["old"]["status"] == "ok"

    stored = {**FIT_OK, "status": "ok", "verified": True}
    outcome, ledger, _ = await _run_file(
        tmp_path / "again",
        chosen,
        FIT_OK,
        stored,
        turn=_row(now, (11.0, 299.0)),
    )

    # And the ledger records it as what the file had before
    assert outcome.kind == "ok"
    assert ledger.events[0]["before"]["record"] == now
    assert ledger.events[0]["before"]["range"] == [11.0, 299.0]


@pytest.mark.asyncio
async def test_a_file_put_on_target_since_it_was_chosen_is_left(tmp_path):
    on_target = {"status": "ok", "verified": True, "quality": TARGET}

    outcome, ledger, seen = await _run_file(
        tmp_path, _candidate(None), FIT_OK, on_target, turn=_row(on_target)
    )

    assert outcome.kind == "on target already"
    assert "sample" not in seen
    assert ledger.events == []


@pytest.mark.asyncio
async def test_a_file_gone_since_it_was_chosen_is_left(tmp_path):
    outcome, ledger, seen = await _run_file(
        tmp_path, _candidate(None), FIT_OK, None, turn=None
    )

    assert outcome.kind == "gone"
    assert "sample" not in seen
    assert ledger.events == []


# --- Which files a run takes ---


def _run_config(tmp_path, **update) -> script.Config:
    values = {
        "action": script.RUN,
        "instrument": "TOF H",
        "first_day": None,
        "last_day": None,
        "mode": None,
        "batch_size": 2,
        "gap_s": 0.0,
        "backlog": 20,
        "limit": 0,
        "retry": False,
        "rollback": (),
        "band_edges": script.audit.DEFAULT_BAND_EDGES,
        "ledger_dir": str(tmp_path / "ledger"),
        "as_json": False,
    }
    return script.Config(**{**values, **update})


def _samples(*collections):
    return tuple(
        {
            "sample_item_id": f"s{i}",
            "sample_item_name": f"s{i}",
            "mode_id": "m1" if collection else "m0",
            "mode_name": "x",
            "collection_id": collection,
        }
        for i, collection in enumerate(collections)
    )


async def _runner(
    tmp_path,
    candidates,
    config=None,
    ledger_events=(),
    stop_after=0,
    not_ready=None,
    reconciled=None,
):
    config = config or _run_config(tmp_path)
    if ledger_events:
        _ledger(tmp_path / "ledger", *ledger_events)
    done = []

    async def run_one(candidate, target, ledger, batch, config):
        done.append((candidate.sample_file_id, batch))
        if stop_after and len(done) == stop_after:
            Path(ledger.directory, script.STOP_FILE).touch()
        return script.Outcome("ok")

    settle = AsyncMock(return_value={"files": 0, "left_at_rematch": []})
    reconcile = AsyncMock(return_value=reconciled or script.Counter())
    with (
        patch.object(script, "reconcile", reconcile),
        patch.object(script, "select_candidates", AsyncMock(return_value=candidates)),
        patch.object(script, "_targets", AsyncMock(return_value={"m1": _target()})),
        patch.object(script, "_run_one", run_one),
        patch.object(script, "settle", settle),
        patch.object(script, "fresh_in_progress", AsyncMock(return_value=0)),
        patch.object(
            script, "_refit_ready", lambda filename: (not_ready or {}).get(filename)
        ),
    ):
        report = await script.run_runner(config)
    settle.reconcile = reconcile
    return report, done, settle


@pytest.mark.asyncio
async def test_a_run_takes_the_files_ingest_calibrates_in_batches(tmp_path):
    candidates = [
        _candidate(sample_file_id="a"),
        _candidate(
            sample_file_id="ontarget", record={"status": "ok", "quality": TARGET}
        ),
        _candidate(sample_file_id="nocal", samples=_samples(None)),
        _candidate(sample_file_id="two", samples=_samples("c1", "c1")),
        _candidate(sample_file_id="blank", instrument_function_id=None),
        _candidate(sample_file_id="nosample", samples=()),
        _candidate(sample_file_id="b"),
        _candidate(sample_file_id="c"),
    ]

    report, done, settle = await _runner(tmp_path, candidates)

    assert done == [("a", 1), ("b", 1), ("c", 2)]
    assert report.counts["on target already"] == 1
    assert report.no_calibrants == 1
    assert report.counts["not fitted by ingest: more than one sample to calibrate"] == 1
    assert report.counts["not fitted by ingest: a blank: no instrument functions"] == 1
    assert report.counts["not fitted by ingest: no sample"] == 1
    # What a stopped run left first, then after each batch
    assert settle.await_count == 3


@pytest.mark.asyncio
async def test_a_restarted_run_takes_up_where_the_ledger_left_off(tmp_path):
    candidates = [_candidate(sample_file_id=name) for name in ("a", "b", "c")]
    events = [
        {"event": "kept", "sample_file_id": "a", "mode_id": "m1", "target": TARGET},
        {"event": "error", "sample_file_id": "b", "mode_id": "m1", "target": TARGET},
        {"event": "skipped", "sample_file_id": "c", "batch": 4, "mode_id": "m1"},
    ]

    report, done, _ = await _runner(tmp_path, candidates, ledger_events=events)

    # c's skip was under no target, so it is offered again; a's is settled
    assert [name for name, _ in done] == ["b", "c"]
    assert {batch for _, batch in done} == {5}
    assert report.counts["in the ledger already"] == 1


@pytest.mark.asyncio
async def test_a_run_first_closes_what_an_earlier_run_left_open(tmp_path):
    candidates = [_candidate(sample_file_id=name) for name in ("a", "b")]
    events = [{"event": "apply", "sample_file_id": "b", "mode_id": "m1"}]

    report, done, settle = await _runner(
        tmp_path,
        candidates,
        ledger_events=events,
        reconciled=script.Counter({"left open": 1}),
    )

    settle.reconcile.assert_awaited_once()
    assert report.reconciled == {"left open": 1}
    assert report.as_dict()["reconciled"] == {"left open": 1}
    # One it could not close is not run over
    assert done == [("a", 1)]
    assert report.counts["an apply left open"] == 1


@pytest.mark.asyncio
async def test_reconciling_comes_before_the_first_settle(tmp_path):
    calls = []
    reconcile = AsyncMock(side_effect=lambda ledger: calls.append("reconcile") or {})
    settle = AsyncMock(
        side_effect=lambda config, ledger: calls.append("settle") or {"files": 0}
    )
    with (
        patch.object(script, "reconcile", reconcile),
        patch.object(script, "settle", settle),
        patch.object(script, "select_candidates", AsyncMock(return_value=[])),
        patch.object(script, "_targets", AsyncMock(return_value={})),
    ):
        await script.run_runner(_run_config(tmp_path))

    assert calls == ["reconcile", "settle"]


@pytest.mark.asyncio
async def test_a_rollback_first_closes_what_a_run_left_open(tmp_path):
    _ledger(
        tmp_path / "ledger",
        {"event": "apply", "sample_file_id": "a", "batch": 1},
    )

    async def reconcile(ledger):
        # The apply had landed: closed as applied
        ledger.write({"event": "applied", "sample_file_id": "a", "apply_seq": 1})
        return script.Counter({"applied": 1})

    rollback = AsyncMock(return_value=script.Outcome("rolled_back"))
    with (
        patch.object(script, "reconcile", reconcile),
        patch.object(script, "rollback_file", rollback),
        patch.object(script, "settle", AsyncMock(return_value={})),
    ):
        report = await script.run_rollback(
            _run_config(tmp_path, action=script.ROLLBACK, rollback=("batch:1",))
        )

    assert [c.args[0]["seq"] for c in rollback.await_args_list] == [1]
    assert report.reconciled == {"applied": 1}
    assert report.counts == {"rolled_back": 1}


@pytest.mark.asyncio
async def test_retry_offers_again_what_the_ledger_settled(tmp_path):
    candidates = [_candidate(sample_file_id="a")]
    events = [
        {"event": "kept", "sample_file_id": "a", "mode_id": "m1", "target": TARGET}
    ]

    _, done, _ = await _runner(
        tmp_path,
        candidates,
        config=_run_config(tmp_path, retry=True),
        ledger_events=events,
    )

    assert done == [("a", 1)]


@pytest.mark.asyncio
async def test_a_stop_file_ends_the_run_at_the_next_file(tmp_path):
    candidates = [_candidate(sample_file_id=name) for name in "abcde"]

    report, done, settle = await _runner(tmp_path, candidates, stop_after=3)

    assert [name for name, _ in done] == ["a", "b", "c"]
    assert report.counts["stopped"] == 1
    # The batch it stopped in is matched before it ends
    assert settle.await_count == 3


@pytest.mark.asyncio
async def test_the_limit_leaves_the_rest_for_a_later_run(tmp_path):
    candidates = [_candidate(sample_file_id=name) for name in "abc"]

    report, done, _ = await _runner(
        tmp_path, candidates, config=_run_config(tmp_path, limit=2)
    )

    assert [name for name, _ in done] == ["a", "b"]
    assert report.counts["left for a later run"] == 1


@pytest.mark.asyncio
async def test_a_dry_run_reads_and_writes_nothing(tmp_path):
    candidates = [_candidate(sample_file_id="a")]
    read = AsyncMock(return_value=script.Outcome("would apply ok"))
    with (
        patch.object(script, "select_candidates", AsyncMock(return_value=candidates)),
        patch.object(script, "_targets", AsyncMock(return_value={"m1": _target()})),
        patch.object(script, "_dry_one", read),
        patch.object(script, "settle", AsyncMock()) as settle,
        patch.object(script, "fresh_in_progress", AsyncMock(return_value=0)),
        patch.object(script, "_refit_ready", lambda filename: None),
    ):
        report = await script.run_runner(_run_config(tmp_path, action=script.DRY_RUN))

    assert report.counts["would apply ok"] == 1
    settle.assert_not_awaited()
    assert not (tmp_path / "ledger").exists()


@pytest.mark.asyncio
async def test_a_file_without_stores_is_listed_and_takes_no_turn(tmp_path):
    candidates = [
        _candidate(sample_file_id="gone", filename="Gone_2026.10.20_12h00m00s_x"),
        _candidate(sample_file_id="ready"),
    ]

    report, done, _ = await _runner(
        tmp_path, candidates, not_ready={"Gone_2026.10.20_12h00m00s_x": "no directory"}
    )

    assert done == [("ready", 1)]
    assert report.counts["not refit-ready: no directory"] == 1
    events = script.Ledger(str(tmp_path / "ledger"), writable=False).events
    assert [(e["event"], e["sample_file_id"], e["target"]) for e in events] == [
        ("skipped", "gone", TARGET)
    ]


def test_what_a_refit_reads(filestore):
    assert script._refit_ready(FILENAME) == "no directory"
    write_file(filestore, APPLIED)
    assert script._refit_ready(FILENAME) is None
    (_path(filestore) / "peak_timeseries.zarr").rename(_path(filestore) / "gone")
    assert script._refit_ready(FILENAME) == "no peak store"
    (_path(filestore) / ".props").unlink()
    assert script._refit_ready(FILENAME) == "no .props"


# --- The dry run ---


def _dry_target():
    return script.ModeTarget(
        mode=SimpleNamespace(
            ionization_mode_id="m1",
            ionization_mode_name="Nitrate",
            ionization_mode_polarity="-",
        ),
        params=CalibrationFitParams(refine_window=20, mz_error_tolerance=15.0),
        isotopes="the collection's isotopes",
        target=TARGET,
    )


def _audit(answers: list[str]):
    """The audit's handler, answering each attempt in turn: ``ok``, ``warn``
    (a fit with a warning) or ``error`` (no fit)."""
    seen = []

    def handler(stored, params, isotopes, resolution, model):
        made = SimpleNamespace(
            fit_result=None,
            warning=None,
            error=None,
            stats={"n": 5},
            fitted_on={"n_points": 4},
        )

        async def fit():
            seen.append((params.mz_error_tolerance, params.refine_window, model))
            answer = answers[len(seen) - 1]
            if answer != "error":
                made.fit_result = {"mode": 0, "par": [1.0, float(len(seen))], "x": 0}
            if answer == "warn":
                made.warning = "few calibrants"
            if answer == "error":
                made.error = "no calibrants"

        made.fit = fit
        return made

    return handler, seen


def _dry_patches(handler):
    stack = ExitStack()
    stack.enter_context(
        patch.object(script.audit, "read_stored_peaks", lambda filename: "peaks")
    )
    stack.enter_context(
        patch.object(
            script,
            "read_instrument_functions",
            AsyncMock(return_value=(None, "resolution")),
        )
    )
    stack.enter_context(patch.object(script.audit, "AuditCalibrationHandler", handler))
    stack.enter_context(
        patch.object(
            script,
            "fit_quality",
            lambda stats, params: {"tolerance": params.mz_error_tolerance},
        )
    )
    stack.enter_context(
        patch.object(script, "calibration_quality_issues", lambda q, f: [])
    )
    return stack


@pytest.mark.asyncio
async def test_a_dry_fit_widens_as_ingest_does_until_a_fit_has_no_warning():
    handler, seen = _audit(["warn", "error", "ok"])
    target = _dry_target()
    with _dry_patches(handler):
        result = await script.dry_fit(_candidate(), target)

    # 15, 30, 60 ppm, the refine window kept wider; the node's own model
    assert seen == [(15.0, 20, None), (30.0, 31, None), (60.0, 61, None)]
    assert result.attempts == 3
    assert result.fit == {"mode": 0, "par": [1.0, 3.0]}
    assert result.quality == {"tolerance": 60.0, "n_points": 4}
    assert (result.issues, result.reason) == ([], None)
    # The mode's parameters are the next file's too: left as they were
    assert target.params.mz_error_tolerance == 15.0


@pytest.mark.asyncio
async def test_a_dry_fit_that_never_comes_clean_would_fail():
    handler, seen = _audit(["error"] * (script.CALIBRATION_ITERATIONS - 1) + ["warn"])
    with _dry_patches(handler):
        result = await script.dry_fit(_candidate(), _dry_target())

    assert len(seen) == script.CALIBRATION_ITERATIONS
    assert seen[-1][0] == 15.0 * 2 ** (script.CALIBRATION_ITERATIONS - 1)
    assert result.fit is None
    assert (result.reason, result.attempts) == (
        "few calibrants",
        script.CALIBRATION_ITERATIONS,
    )


def _dry(fit=True, issues=()):
    if not fit:
        return script.DryFit(None, None, [], "no calibrants", 7)
    return script.DryFit(
        fit={"mode": 0, "par": list(APPLIED["par"])},
        quality={},
        issues=[{"code": code} for code in issues],
        reason=None,
        attempts=2,
    )


OK_RECORD = {**APPLIED, "status": "ok", "verified": True}


@pytest.mark.parametrize(
    ("old", "dry", "outcome"),
    [
        (OK_RECORD, _dry(issues=("residual",)), "would keep its ok record"),
        (OK_RECORD, _dry(), "would apply ok"),
        ({**CONVERTER, "status": "unfitted"}, _dry(issues=("residual",)), None),
        (OK_RECORD, _dry(fit=False), "would fail"),
    ],
    ids=["keep", "apply ok", "apply poor", "fail"],
)
@pytest.mark.asyncio
async def test_what_a_dry_run_says_of_a_file(filestore, tmp_path, old, dry, outcome):
    outcome = outcome or "would apply poor"
    write_file(filestore, APPLIED)
    report = script.Report(action=script.DRY_RUN, instrument="TOF H")
    with (
        patch.object(script, "dry_fit", AsyncMock(return_value=dry)),
        patch.object(script, "_assignment_runs", AsyncMock(return_value=["r1", "r2"])),
    ):
        result = await script._dry_one(
            _candidate(old), _target(), _run_config(tmp_path), report
        )

    (entry,) = report.files
    assert result.kind == entry["outcome"] == outcome
    applies = outcome.startswith("would apply")
    # Only a fit it would apply moves the axis, and meets the assignment runs
    assert bool(entry.get("bands")) == applies
    assert bool(report.bands) == applies
    assert report.assignment_runs == (2 if applies else 0)
    if outcome == "would keep its ok record":
        assert entry["issues"] == ["residual"]
    if outcome == "would fail":
        assert entry["reason"] == "no calibrants"
    else:
        assert entry["new"] == dry.fit and entry["attempts"] == 2


@pytest.mark.asyncio
async def test_a_file_a_dry_run_cannot_read_is_an_error(tmp_path):
    report = script.Report(action=script.DRY_RUN, instrument="TOF H")
    with patch.object(script, "dry_fit", AsyncMock(side_effect=OSError("gone"))):
        result = await script._dry_one(
            _candidate(), _target(), _run_config(tmp_path), report
        )

    assert result.kind == "error"
    assert report.files[0]["error"] == "OSError"


# --- Pacing ---


@pytest.mark.asyncio
async def test_the_backlog_guard_holds_while_the_ingest_is_busy(tmp_path):
    ledger = script.Ledger(str(tmp_path), writable=True)
    sleep = AsyncMock()
    counts = AsyncMock(side_effect=[25, 21, 20])
    with patch.object(script, "fresh_in_progress", counts):
        held = await script.hold_for_backlog(_run_config(tmp_path), ledger, sleep=sleep)

    assert held is True
    assert sleep.await_count == 2


@pytest.mark.asyncio
async def test_a_stop_ends_a_hold(tmp_path):
    ledger = script.Ledger(str(tmp_path), writable=True)
    (tmp_path / script.STOP_FILE).touch()
    with patch.object(script, "fresh_in_progress", AsyncMock(return_value=99)):
        held = await script.hold_for_backlog(
            _run_config(tmp_path), ledger, sleep=AsyncMock()
        )
    assert held is False


# --- Matching what was applied ---


@pytest.mark.asyncio
async def test_settling_matches_the_files_and_rematches_the_batches(tmp_path):
    ledger = _ledger(
        tmp_path,
        {"event": "apply", "sample_file_id": "ok", "batches": ["b1", "b2"]},
        {"event": "applied", "sample_file_id": "ok"},
        {"event": "apply", "sample_file_id": "below", "batches": ["b2"]},
        {"event": "applied", "sample_file_id": "below"},
    )
    records = {
        "ok": {"status": "ok", "verified": True},
        "below": {"status": "poor", "verified": False},
    }
    at_rematch = [["b1"], []]
    match = AsyncMock()
    rematch = AsyncMock(return_value={"status": "success"})
    with (
        patch.object(
            script,
            "_file_state",
            AsyncMock(side_effect=lambda i: SimpleNamespace(mz_calibration=records[i])),
        ),
        patch.object(
            script,
            "_acquisition_samples",
            AsyncMock(side_effect=lambda i: [f"{i}-s1", f"{i}-s2"]),
        ),
        patch.object(
            script,
            "_batches_at_rematch",
            AsyncMock(side_effect=lambda ids: at_rematch.pop(0)),
        ),
        patch.object(script, "match_compute_sample", match),
        patch.object(script, "rematch_batch", rematch),
        patch.object(script, "fresh_in_progress", AsyncMock(return_value=0)),
    ):
        result = await script.settle(_run_config(tmp_path), ledger)

    # The unverified file's samples are left unmatched, as ingest leaves them
    assert [c.kwargs["sample_item_id"] for c in match.await_args_list] == [
        "ok-s1",
        "ok-s2",
    ]
    assert all(c.kwargs["process_id"] for c in match.await_args_list)
    rematch.assert_awaited_once()
    assert rematch.await_args.kwargs["sample_batch_id"] == "b1"
    assert rematch.await_args.kwargs["full_remove"] is False
    assert rematch.await_args.kwargs["process_id"]
    assert result["left_at_rematch"] == []
    assert result["files"] == 2 and result["samples"] == 2
    assert ledger.unmatched() == []
    matched = [e for e in ledger.events if e["event"] == "matched"]
    assert matched[1]["unverified"] is True


@pytest.mark.asyncio
async def test_a_batch_still_at_rematch_is_reported(tmp_path):
    ledger = _ledger(
        tmp_path, {"event": "rollback", "sample_file_id": "x", "batches": ["b1"]}
    )
    with (
        patch.object(script, "_file_state", AsyncMock(return_value=None)),
        patch.object(script, "_batches_at_rematch", AsyncMock(return_value=["b1"])),
        patch.object(
            script, "rematch_batch", AsyncMock(return_value={"status": "locked"})
        ),
        patch.object(script, "fresh_in_progress", AsyncMock(return_value=0)),
    ):
        result = await script.settle(_run_config(tmp_path), ledger)

    assert result["left_at_rematch"] == ["b1"]
    assert ledger.events[-1] == {**ledger.events[-1], "status": "locked"}


# --- Rollback ---


class _Row(SimpleNamespace):
    """A sample file's row, as the controllers fetch and save it."""

    def to_dict(self):
        return {
            "filename": self.filename,
            "instrument": "TOF H",
            "datetime": datetime(2026, 10, 20, 12),
            "datetime_utc": datetime(2026, 10, 20, 12, tzinfo=UTC),
            "length": 60.0,
            "range": self.range,
            "mz_calibration": self.mz_calibration,
        }


def _controllers(row, batches):
    """The controllers a rollback and a put-back call, answered: the file's
    row, its samples s1 and s2, and its batches as ``(id, type, status)``."""
    affected = SimpleNamespace(
        affected_samples=[SimpleNamespace(sample_item_id=i) for i in ("s1", "s2")],
        affected_sample_batches=[
            SimpleNamespace(sample_batch_id=i, sample_batch_type=kind, status=status)
            for i, kind, status in batches
        ],
    )
    mocks = {
        "fetch_sample_file": AsyncMock(return_value=row),
        "_file_state": AsyncMock(return_value=row),
        "fetch_affected_sample_data": AsyncMock(return_value=affected),
        "update_sample_batch_status": AsyncMock(),
        "update_sample_file": AsyncMock(),
        "match_remove_sample": AsyncMock(),
    }
    stack = ExitStack()
    for name, mock in mocks.items():
        stack.enter_context(patch.object(script, name, mock))
    return stack, mocks


def _statuses(mocks) -> list[tuple]:
    return [
        (
            call.kwargs["status"],
            call.kwargs["sample_batch_ids"],
            call.kwargs.get("never_lower", False),
        )
        for call in mocks["update_sample_batch_status"].await_args_list
    ]


def _saved(mocks):
    (call,) = mocks["update_sample_file"].await_args_list
    return call.args[1]


def _removed(mocks) -> list[tuple]:
    return [
        (call.kwargs["sample_item_id"], call.kwargs["full_remove"])
        for call in mocks["match_remove_sample"].await_args_list
    ]


#: The record the converter left, and the fit a run applied over it
OLD = {**CONVERTER, "status": "unfitted"}
NEW_FIT = {"mode": 0, "par": [10850.0 * (1 + 2e-5), -18500.0 - CUT]}


async def _apply_event(
    filestore, ledger, converter=True, record=None, **update
) -> tuple[dict, dict]:
    """The ledger's apply event for a file, as a run writes it, then the
    apply's writes to the file's axes and ``.props``.

    :param converter: A file on the converter's record, whose peaks the
        ledger keeps; else one on an applied fit, which keeps nothing
    :param record: The database record before, if not the file's own
    :return: The event, and every axis and the ``.props`` of the file before
    """
    if converter:
        write_file(filestore, CONVERTER, offset=CUT, peaks_on_axis=True)
    else:
        write_file(filestore, APPLIED)
    original = everything(filestore)
    props = script._props_before(FILENAME)
    plan, kept = script.restore_plan(FILENAME, props["mz_calibration"])
    if kept:
        plan["snapshot"] = script._snapshot_path(ledger, "f1")
        script.write_snapshot(ledger.directory, plan["snapshot"], kept)
    assert bool(kept) == converter
    if record is None:
        record = OLD if converter else {**APPLIED, "status": "ok", "verified": True}
    event = ledger.write(
        {
            "event": "apply",
            "sample_file_id": "f1",
            "batch": 3,
            "mode_id": "m1",
            "target": TARGET,
            "before": {"record": record, "range": [10.0, 300.0], "props": props},
            "restore": plan,
            "new": script._compact({**NEW_FIT, "status": "ok"}),
            "batches": ["b1", "b2"],
            "recalibrate": [],
            **update,
        }
    )
    await TofCalibrationHandler(FILENAME).apply({**NEW_FIT, "quality": {}})
    return event, original


@pytest.mark.parametrize("converter", [True, False], ids=["converter", "applied"])
@pytest.mark.asyncio
async def test_a_rollback_writes_back_the_record_and_rebuilds_the_matches(
    filestore, tmp_path, converter
):
    ledger = script.Ledger(str(tmp_path / "ledger"), writable=True)
    event, original = await _apply_event(filestore, ledger, converter=converter)
    row = _Row(
        sample_file_id="f1",
        filename=FILENAME,
        mz_calibration={**NEW_FIT, "status": "ok", "verified": True},
        range=[10.1, 300.2],
    )
    stack, mocks = _controllers(
        row,
        [
            ("a1", "ACQUISITION", "ready"),
            ("b1", "ANALYSIS", "ready"),
            ("b2", "ANALYSIS", "recalibrate"),
        ],
    )
    with stack:
        outcome = await script.rollback_file(event, ledger)

    assert outcome.kind == "rolled_back"
    assert_same(original, everything(filestore))
    saved = _saved(mocks)
    assert saved.mz_calibration == event["before"]["record"]
    assert saved.range == [10.0, 300.0]
    # Every sample loses the matches made on the axis taken back
    assert _removed(mocks) == [("s1", True), ("s2", True)]
    # Held while it writes; a pending recalibration kept; never lowered
    assert _statuses(mocks) == [
        ("processing", ["b1", "b2"], False),
        ("recalibrate", ["b2"], False),
        ("rematch", ["b1", "b2"], True),
    ]
    rollback = ledger.events[-1]
    assert rollback["event"] == "rollback"
    assert (rollback["apply_seq"], rollback["of_batch"]) == (event["seq"], 3)
    assert rollback["batches"] == ["b1", "b2"]
    assert [e["sample_file_id"] for e in ledger.unmatched()] == ["f1"]


@pytest.mark.asyncio
async def test_a_rollback_that_cannot_give_the_axes_back_touches_nothing(
    filestore, tmp_path
):
    ledger = script.Ledger(str(tmp_path / "ledger"), writable=True)
    event, _ = await _apply_event(filestore, ledger)
    row = _Row(
        sample_file_id="f1",
        filename=FILENAME,
        mz_calibration={**NEW_FIT, "status": "ok"},
        range=[10.1, 300.2],
    )
    stack, mocks = _controllers(row, [("b1", "ANALYSIS", "ready")])
    with (
        stack,
        patch.object(
            script, "restore_axes", side_effect=script.CannotRestore("no axis")
        ),
    ):
        outcome = await script.rollback_file(event, ledger)

    assert outcome.kind == "refused"
    assert ledger.events[-1]["event"] == "rollback_refused"
    assert ledger.events[-1]["reason"] == "no axis"
    assert mocks["update_sample_batch_status"].await_count == 0
    assert mocks["update_sample_file"].await_count == 0


@pytest.mark.asyncio
async def test_a_record_changed_since_the_apply_is_not_rolled_back(tmp_path):
    ledger = _ledger(
        tmp_path,
        {
            "event": "apply",
            "sample_file_id": "f1",
            "before": {"record": {"mode": 0, "par": [5, 6]}},
            "new": {"mode": 0, "par": [1, 2]},
        },
    )
    changed = SimpleNamespace(
        filename=FILENAME, mz_calibration={"mode": 0, "par": [3, 4]}
    )
    with patch.object(script, "fetch_sample_file", AsyncMock(return_value=changed)):
        outcome = await script.rollback_file(ledger.events[0], ledger)

    assert outcome.kind == "refused"
    assert ledger.events[-1]["event"] == "rollback_refused"


def _applied_file(ledger, event):
    """The file's row after the apply the event records, and the ledger's
    ``applied`` for it."""
    ledger.write(
        {
            "event": "applied",
            "sample_file_id": "f1",
            "apply_seq": event["seq"],
            "mode_id": "m1",
            "target": TARGET,
        }
    )
    return _Row(
        sample_file_id="f1",
        filename=FILENAME,
        mz_calibration={**NEW_FIT, "status": "ok", "verified": True},
        range=[10.1, 300.2],
    )


#: An analysis batch, and one that waits on a recalibration
_BATCHES = [("b1", "ANALYSIS", "ready"), ("b2", "ANALYSIS", "recalibrate")]
#: How a rollback leaves them: held, then the pending one kept
_HELD_AND_LET_GO = [
    ("processing", ["b1", "b2"], False),
    ("recalibrate", ["b2"], False),
    ("rematch", ["b1", "b2"], True),
]


@pytest.mark.parametrize("fails", ["axes", "record"])
@pytest.mark.asyncio
async def test_a_rollback_that_fails_writing_back_lets_the_batches_go(
    filestore, tmp_path, fails
):
    ledger = script.Ledger(str(tmp_path / "ledger"), writable=True)
    event, _ = await _apply_event(filestore, ledger)
    stack, mocks = _controllers(_applied_file(ledger, event), _BATCHES)
    with (
        stack,
        patch.object(
            script.RestoreHandler,
            "apply",
            AsyncMock(side_effect=OSError("disk full") if fails == "axes" else None),
        ),
    ):
        if fails == "record":
            mocks["update_sample_file"].side_effect = OSError("connection lost")
        with pytest.raises(OSError):
            await script.rollback_file(event, ledger)

    assert _statuses(mocks) == _HELD_AND_LET_GO
    assert _removed(mocks) == []
    assert ledger.events[-1]["event"] == "applied"


@pytest.mark.asyncio
async def test_a_rollback_cut_off_after_its_record_is_finished_by_the_next(
    filestore, tmp_path
):
    """The first rollback wrote the old record back, then failed removing the
    matches: the same rollback again finishes it."""
    ledger = script.Ledger(str(tmp_path / "ledger"), writable=True)
    event, original = await _apply_event(filestore, ledger)
    row = _applied_file(ledger, event)
    stack, mocks = _controllers(row, _BATCHES)
    with stack:
        mocks["match_remove_sample"].side_effect = [None, OSError("connection lost")]
        with pytest.raises(OSError):
            await script.rollback_file(event, ledger)
        # The database holds the old record; the ledger still says applied
        assert row.mz_calibration == event["before"]["record"]
        assert script.rollback_events(ledger, ("file:f1",)) == [event]

        mocks["match_remove_sample"].side_effect = None
        outcome = await script.rollback_file(event, ledger)

    assert outcome.kind == "rolled_back"
    assert_same(original, everything(filestore))
    assert row.mz_calibration == event["before"]["record"]
    assert _removed(mocks) == [("s1", True), ("s2", True)] * 2
    assert _statuses(mocks) == _HELD_AND_LET_GO * 2
    assert ledger.events[-1]["event"] == "rollback"
    assert [e["sample_file_id"] for e in ledger.unmatched()] == ["f1"]
    assert script.rollback_events(ledger, ("file:f1",)) == []


# --- An apply a run left open ---


@pytest.mark.asyncio
async def test_an_apply_that_landed_before_the_run_was_cut_off_is_finished(
    filestore, tmp_path
):
    ledger = script.Ledger(str(tmp_path / "ledger"), writable=True)
    event, _ = await _apply_event(filestore, ledger, recalibrate=["b2"])
    row = _Row(
        sample_file_id="f1",
        filename=FILENAME,
        mz_calibration={**NEW_FIT, "status": "ok", "verified": True},
        range=[10.1, 300.2],
    )
    # Cut off during the match removal: its batches still at processing
    stack, mocks = _controllers(
        row,
        [
            ("a1", "ACQUISITION", "ready"),
            ("b1", "ANALYSIS", "processing"),
            ("b2", "ANALYSIS", "processing"),
        ],
    )
    with stack:
        closed = await script.close_open_apply(event, ledger)

    assert closed == "applied"
    applied = ledger.events[-1]
    assert applied["event"] == "applied" and applied["reconciled"] is True
    assert (applied["apply_seq"], applied["status"]) == (event["seq"], "ok")
    # What the apply left undone after its record is done
    assert _removed(mocks) == [("s1", True), ("s2", True)]
    assert _statuses(mocks) == [
        ("recalibrate", ["b2"], False),
        ("rematch", ["b1", "b2"], True),
    ]
    assert mocks["update_sample_file"].await_count == 0
    # Matched by the next settle, undone by a rollback, settled for the run
    assert ledger.open_apply("f1") is None
    assert [e["sample_file_id"] for e in ledger.unmatched()] == ["f1"]
    assert script.rollback_events(ledger, ("file:f1",)) == [event]
    assert ledger.settled({"m1": TARGET}.get) == {"f1"}


@pytest.mark.asyncio
async def test_an_apply_that_did_not_land_is_put_back_and_offered_again(
    filestore, tmp_path
):
    ledger = script.Ledger(str(tmp_path / "ledger"), writable=True)
    event, original = await _apply_event(filestore, ledger, recalibrate=["b2"])
    # The node's failure marker over the converter's record, which keeps
    # its parameters
    marker = {**CONVERTER, "status": "failed", "verified": False, "error": "x"}
    row = _Row(
        sample_file_id="f1", filename=FILENAME, mz_calibration=marker, range=[1, 2]
    )
    # Both held at processing by the apply; b2 waited on a recalibration
    stack, mocks = _controllers(
        row, [("b1", "ANALYSIS", "processing"), ("b2", "ANALYSIS", "processing")]
    )
    with stack:
        closed = await script.close_open_apply(event, ledger)

    assert closed == "restored"
    assert_same(original, everything(filestore))
    saved = _saved(mocks)
    assert (saved.mz_calibration, saved.range) == (OLD, [10.0, 300.0])
    # The matches are on the axes put back: kept
    assert _removed(mocks) == []
    assert _statuses(mocks) == [
        ("processing", ["b1", "b2"], False),
        ("recalibrate", ["b2"], False),
        ("rematch", ["b1", "b2"], True),
    ]
    restored = ledger.events[-1]
    assert (restored["event"], restored["apply_seq"]) == ("restored", event["seq"])
    assert ledger.open_apply("f1") is None
    assert ledger.unmatched() == []
    assert ledger.settled({"m1": TARGET}.get) == set()
    assert "b1" in ledger.touched_batches()


@pytest.mark.asyncio
async def test_a_refit_to_the_old_parameters_that_did_not_land_is_put_back(
    filestore, tmp_path
):
    ledger = script.Ledger(str(tmp_path / "ledger"), writable=True)
    old = {**APPLIED, "status": "ok", "verified": True}
    old["quality"] = {**TARGET, "collection": {"id": "c1", "isotopes_sha256": "old"}}
    # The refit's parameters are the old ones; only its stamp is the target's
    refit = script._compact({**APPLIED, "status": "ok", "quality": TARGET})
    event, original = await _apply_event(
        filestore, ledger, converter=False, record=old, new=refit
    )
    row = _Row(sample_file_id="f1", filename=FILENAME, mz_calibration=old, range=[])
    stack, mocks = _controllers(row, [("b1", "ANALYSIS", "processing")])
    with stack:
        closed = await script.close_open_apply(event, ledger)

    assert closed == "restored"
    assert_same(original, everything(filestore))
    assert _saved(mocks).mz_calibration == old


@pytest.mark.parametrize(
    ("row", "reason"),
    [
        (
            _Row(
                sample_file_id="f1",
                filename=FILENAME,
                mz_calibration={"mode": 2, "par": [1, 2, 0.5], "status": "ok"},
                range=[1, 2],
            ),
            "another apply replaced the record since",
        ),
        (None, "the file is gone"),
    ],
    ids=["applied over", "gone"],
)
@pytest.mark.asyncio
async def test_an_apply_another_rewrote_over_is_superseded(
    filestore, tmp_path, row, reason
):
    ledger = script.Ledger(str(tmp_path / "ledger"), writable=True)
    event, _ = await _apply_event(filestore, ledger)
    stack, mocks = _controllers(row, [("b1", "ANALYSIS", "ready")])
    with stack:
        closed = await script.close_open_apply(event, ledger)

    assert closed == "superseded"
    assert ledger.events[-1]["reason"] == reason
    assert ledger.open_apply("f1") is None
    assert mocks["update_sample_batch_status"].await_count == 0
    assert mocks["update_sample_file"].await_count == 0


@pytest.mark.asyncio
async def test_an_apply_whose_axes_cannot_be_put_back_is_left_open(filestore, tmp_path):
    ledger = script.Ledger(str(tmp_path / "ledger"), writable=True)
    event, _ = await _apply_event(filestore, ledger)
    row = _Row(sample_file_id="f1", filename=FILENAME, mz_calibration=OLD, range=[])
    stack, mocks = _controllers(row, [("b1", "ANALYSIS", "processing")])
    with (
        stack,
        patch.object(
            script, "restore_axes", side_effect=script.CannotRestore("no axis")
        ),
    ):
        closed = await script.close_open_apply(event, ledger)

    assert closed == "left open"
    assert ledger.events[-1]["event"] == "restore_refused"
    assert ledger.open_apply("f1")["seq"] == event["seq"]
    assert mocks["update_sample_batch_status"].await_count == 0


@pytest.mark.asyncio
async def test_reconciling_closes_each_open_apply_and_goes_on_past_a_failure(
    tmp_path,
):
    ledger = _ledger(
        tmp_path,
        {"event": "apply", "sample_file_id": "a"},
        {"event": "apply", "sample_file_id": "b"},
        {"event": "applied", "sample_file_id": "b", "apply_seq": 2},
        {"event": "apply", "sample_file_id": "c"},
    )

    async def close(event, ledger):
        if event["sample_file_id"] == "a":
            raise OSError("database down")
        return "restored"

    with patch.object(script, "close_open_apply", side_effect=close) as closing:
        counts = await script.reconcile(ledger)

    assert [c.args[0]["sample_file_id"] for c in closing.await_args_list] == [
        "a",
        "c",
    ]
    assert counts == {"left open": 1, "restored": 1}
    assert ledger.events[-1]["open_apply"] == 1
    assert ledger.events[-1]["error"] == "OSError: database down"


@pytest.mark.parametrize("action", [script.DRY_RUN, script.ROLLBACK])
@pytest.mark.asyncio
async def test_the_whole_run_is_for_nobody(no_env, action):
    """Thousands of files calibrated for no user must not each file a
    monitoring event for a notification nobody could read."""
    from mascope_backend.socket.notifications import service as notifications

    no_env.setenv("TOF_RECAL_ACTION", action)
    if action == script.ROLLBACK:
        no_env.setenv("TOF_RECAL_ROLLBACK", "batch:1")
    seen = []

    async def runner(config):
        seen.append(notifications._without_audience.get())
        return script.Report(action=config.action, instrument=config.instrument)

    with (
        patch.object(script, "configure_database_engine", AsyncMock()),
        patch.object(script, "run_runner", runner),
        patch.object(script, "run_rollback", runner),
    ):
        await script.run()

    assert seen == [True]
    assert notifications._without_audience.get() is False
