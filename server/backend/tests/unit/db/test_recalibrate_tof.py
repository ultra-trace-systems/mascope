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
    )

    assert ledger.open_apply("done") is None
    assert ledger.open_apply("cut")["before"] == {"record": 1}
    assert ledger.open_apply("stopped")["seq"] == 5
    assert ledger.open_apply("never") is None


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
    )

    assert [e["sample_file_id"] for e in ledger.unmatched()] == ["c", "d"]
    assert ledger.touched_batches() == {"b1", "b2", "b3"}


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


def _node(fit, stored, outcome=None, issues=()):
    """``calibrate_with_retry`` as it runs: the fit offered to the hook, the
    record the database then holds, and the outcome."""
    seen = {}

    async def calibrate(sample, sample_file_id, process_id, before_apply):
        seen["sample"], seen["process_id"] = sample, process_id
        if fit is None:
            return outcome or CalibrationOutcome(verified=False, reason="no peaks")
        seen["applied"] = await before_apply(fit)
        if not seen["applied"]:
            return CalibrationOutcome(verified=False, withheld=True)
        return outcome or CalibrationOutcome(verified=True)

    return (
        patch.object(script, "calibrate_with_retry", calibrate),
        patch.object(
            script,
            "calibration_quality_issues",
            lambda quality, filename: [{"code": code} for code in issues],
        ),
        patch.object(
            script,
            "_file_state",
            AsyncMock(return_value=SimpleNamespace(mz_calibration=stored)),
        ),
        patch.object(script, "_other_batches", AsyncMock(return_value=["b9"])),
        patch.object(script, "_assignment_runs", AsyncMock(return_value=["run1"])),
        seen,
    )


async def _run_file(tmp_path, candidate, fit, stored, **node):
    ledger = script.Ledger(str(tmp_path / "ledger"), writable=True)
    *patches, seen = _node(fit, stored, **node)
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


@pytest.mark.asyncio
async def test_a_retried_apply_keeps_what_the_file_had_before_the_first(
    filestore, tmp_path
):
    write_file(filestore, APPLIED)
    ledger = _ledger(
        tmp_path / "ledger",
        {
            "event": "apply",
            "sample_file_id": "f1",
            "before": {"record": "first", "range": None, "props": {}},
            "restore": {"offset": None, "kept": ["sum_signal"], "snapshot": "x"},
        },
        {"event": "error", "sample_file_id": "f1", "apply_incomplete": True},
    )
    stored = {**FIT_OK, "status": "ok", "verified": True}
    *patches, _ = _node(FIT_OK, stored)
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        await script.run_file(
            _candidate({**APPLIED, "verified": True}),
            _target(),
            ledger,
            8,
            script.audit.DEFAULT_BAND_EDGES,
        )

    apply = ledger.events[2]
    assert apply["before"]["record"] == "first"
    assert apply["restore"]["snapshot"] == "x"


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
async def test_an_apply_that_did_not_land_is_an_open_error(filestore, tmp_path):
    write_file(filestore, APPLIED)
    # The record the apply failed to replace: other parameters than the fit's
    old = {"mode": 0, "par": [10849.0, -18000.0], "verified": True}

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
    assert ledger.open_apply("f1")["seq"] == 1


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
    tmp_path, candidates, config=None, ledger_events=(), stop_after=0, not_ready=None
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
    with (
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


@pytest.mark.asyncio
async def test_a_record_changed_since_the_apply_is_not_rolled_back(tmp_path):
    ledger = _ledger(
        tmp_path,
        {"event": "apply", "sample_file_id": "f1", "new": {"mode": 0, "par": [1, 2]}},
    )
    changed = SimpleNamespace(
        filename=FILENAME, mz_calibration={"mode": 0, "par": [3, 4]}
    )
    with patch.object(script, "fetch_sample_file", AsyncMock(return_value=changed)):
        outcome = await script.rollback_file(ledger.events[0], ledger)

    assert outcome.kind == "refused"
    assert ledger.events[-1]["event"] == "rollback_refused"


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
