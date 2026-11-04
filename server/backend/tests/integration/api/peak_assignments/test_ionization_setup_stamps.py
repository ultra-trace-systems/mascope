"""Integration tests: assignment runs and calibration curves record the setup.

A run records the version of its sample's ionization mode in its stored
config, read back over the runs route as it was stored. A confidence curve
records the versions its labels were scored under, and the lookup answers a
run under any other version with the curve all the same, but says so: in the
log once, and on the run's calibration record.
"""

from collections import defaultdict
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from test_utils import captured_logs

from mascope_backend.api.new.ionization.modes.versions import sample_setup
from mascope_backend.api.new.peak_assignments import calibration_store
from mascope_backend.api.new.peak_assignments import service as assignments
from mascope_backend.api.new.peak_assignments.calibration_store import (
    StoredCalibration,
    fit_under,
    load_calibration,
    save_calibration,
)
from mascope_backend.api.new.peak_assignments.engine import calibration_meta
from mascope_backend.db import (
    AssignmentCalibration,
    AssignmentVerification,
    Dataset,
    IonizationMode,
    PeakAssignmentRun,
    SampleBatch,
    SampleFile,
    SampleItem,
)
from mascope_backend.db.id import gen_id
from mascope_tools.composition.calibration import Calibration


_NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def stamped(async_session_factory, pa_test_data):
    """Two versions of a mode, and an orbi sample bound under each and none."""
    ids = {
        "dataset": gen_id(),
        "batch": gen_id(),
        "file": gen_id(),
        "first": gen_id(),
        "second": gen_id(),
        "on_first": gen_id(),
        "on_second": gen_id(),
        "unbound": gen_id(),
    }
    async with async_session_factory() as session:
        session.add(
            Dataset(
                dataset_id=ids["dataset"],
                workspace_id=pa_test_data["workspace_id"],
                dataset_name=f"Setup stamps {ids['dataset']}",
                dataset_utc_created=_NOW,
            )
        )
        session.add(
            SampleBatch(
                sample_batch_id=ids["batch"],
                dataset_id=ids["dataset"],
                sample_batch_name="Setup stamps",
                sample_batch_utc_created=_NOW,
            )
        )
        session.add(
            SampleFile(
                sample_file_id=ids["file"],
                filename=f"orbi-stamp_{ids['file']}.zarr",
                instrument="orbi-stamp",
                datetime=datetime(2026, 10, 1, 12, 0),
                datetime_utc=_NOW,
                length=60.0,
                range=[50.0, 500.0],
                polarity="+",
            )
        )
        session.add(
            IonizationMode(
                ionization_mode_id=ids["first"],
                ionization_mode_name=f"Stamped {ids['first']}",
                ionization_mode_polarity="+",
                ionization_mechanism_ids=[],
                is_active=False,
            )
        )
        await session.flush()
        session.add(
            IonizationMode(
                ionization_mode_id=ids["second"],
                ionization_mode_name=f"Stamped {ids['first']}",
                ionization_mode_polarity="+",
                ionization_mechanism_ids=[],
                version=2,
                supersedes_id=ids["first"],
            )
        )
        for key, mode in (
            ("on_first", ids["first"]),
            ("on_second", ids["second"]),
            ("unbound", None),
        ):
            session.add(
                SampleItem(
                    sample_item_id=ids[key],
                    sample_batch_id=ids["batch"],
                    sample_file_id=ids["file"],
                    sample_item_name=key,
                    sample_item_type="sample",
                    polarity="+",
                    ionization_mode_id=mode,
                    sample_item_utc_created=_NOW,
                )
            )
        await session.commit()
    yield ids
    async with async_session_factory() as session:
        await session.execute(
            delete(AssignmentVerification).where(
                AssignmentVerification.sample_item_id.in_(
                    [ids["on_first"], ids["on_second"]]
                )
            )
        )
        await session.execute(
            delete(SampleBatch).where(SampleBatch.sample_batch_id == ids["batch"])
        )
        await session.execute(
            delete(SampleFile).where(SampleFile.sample_file_id == ids["file"])
        )
        await session.execute(
            delete(IonizationMode).where(
                IonizationMode.ionization_mode_id.in_([ids["second"], ids["first"]])
            )
        )
        await session.execute(
            delete(Dataset).where(Dataset.dataset_id == ids["dataset"])
        )
        await session.commit()


# --- runs --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_run_records_the_version_its_sample_is_bound_under(
    async_session_factory, stamped, owner_client
):
    run = await assignments.create_pending_run(stamped["on_second"])

    stored = run.config["ionization_setup"]
    assert stored == {"ionization_mode_id": stamped["second"], "version": 2}
    # As stored, over the runs route.
    resp = await owner_client.get(
        f"/api/peak-assignments/sample/{stamped['on_second']}/runs"
    )
    assert resp.status_code == 200, resp.text
    (listed,) = [
        r
        for r in resp.json()["data"]
        if r["peak_assignment_run_id"] == run.peak_assignment_run_id
    ]
    assert listed["config"]["ionization_setup"] == stored


@pytest.mark.asyncio
async def test_a_run_of_a_sample_with_no_mode_says_so(stamped):
    run = await assignments.create_pending_run(stamped["unbound"])
    assert "ionization_setup" in run.config
    assert run.config["ionization_setup"] is None


@pytest.mark.asyncio
async def test_a_sample_bound_under_an_earlier_version_records_the_current_one(
    async_session_factory, stamped
):
    """The run moves the sample first, so the stamp is the version it searches."""
    run = await assignments.create_pending_run(stamped["on_first"])
    assert run.config["ionization_setup"] == {
        "ionization_mode_id": stamped["second"],
        "version": 2,
    }
    assert await sample_setup(stamped["on_first"]) == run.config["ionization_setup"]


# --- calibration curves ------------------------------------------------------


def _curve(instrument: str) -> Calibration:
    return Calibration(a=2.0, b=-1.0, instrument=instrument, provisional=False)


@pytest_asyncio.fixture
async def instrument_class(async_session_factory):
    """A made-up instrument class, so no other curve is the active one."""
    inst = f"cls{gen_id(6).lower()}"
    yield inst
    async with async_session_factory() as session:
        await session.execute(
            delete(AssignmentCalibration).where(
                AssignmentCalibration.instrument == inst
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_a_curve_keeps_the_setups_it_was_fit_under(instrument_class):
    fit = [{"ionization_mode_id": "m1", "version": 1}]
    await save_calibration(_curve(instrument_class), 1, fit)

    loaded = await load_calibration(instrument_class, 1)
    assert isinstance(loaded, StoredCalibration)
    assert list(loaded.ionization_setups) == fit
    assert fit_under(loaded, fit[0]) is True


@pytest.mark.asyncio
async def test_a_run_under_another_setup_is_warned_about_once(
    instrument_class, monkeypatch
):
    monkeypatch.setattr(calibration_store, "_warned", set())
    await save_calibration(
        _curve(instrument_class), 1, [{"ionization_mode_id": "m1", "version": 1}]
    )
    other = {"ionization_mode_id": "m1-v2", "version": 2}

    with captured_logs("WARNING") as records:
        first = await load_calibration(instrument_class, 1, ionization_setup=other)
        await load_calibration(instrument_class, 1, ionization_setup=other)
        await load_calibration(
            instrument_class,
            1,
            ionization_setup={"ionization_mode_id": "m1", "version": 1},
        )

    # Applied all the same - it is the instrument's curve - and said once.
    assert first.a == 2.0
    warned = [r for r in records if "was fit under" in r["message"]]
    assert len(warned) == 1
    assert "version 2 of mode m1-v2" in warned[0]["message"]


@pytest.mark.asyncio
async def test_a_curve_with_no_recorded_setups_warns_about_none(
    instrument_class, monkeypatch
):
    monkeypatch.setattr(calibration_store, "_warned", set())
    await save_calibration(_curve(instrument_class), 1)
    with captured_logs("WARNING") as records:
        loaded = await load_calibration(
            instrument_class,
            1,
            ionization_setup={"ionization_mode_id": "m1", "version": 1},
        )
    assert loaded.ionization_setups == ()
    assert not [r for r in records if "was fit under" in r["message"]]


def test_the_run_records_whether_its_curve_was_fit_under_its_setup():
    curve = StoredCalibration(
        a=1.0,
        b=0.0,
        instrument="orbi",
        ionization_setups=({"ionization_mode_id": "m1", "version": 1},),
    )
    under = {"ionization_mode_id": "m1", "version": 1}
    other = {"ionization_mode_id": "m2", "version": 1}

    assert calibration_meta(curve, under)["setup_mismatch"] is False
    assert calibration_meta(curve, other)["setup_mismatch"] is True
    assert calibration_meta(curve, other)["fit_setups"] == [under]
    assert "setup_mismatch" not in calibration_meta(curve, None)
    # The in-code curve records none, and its record is as it was.
    plain = calibration_meta(_curve("orbi"), other)
    assert set(plain) == {"instrument", "provisional", "source"}


@pytest.mark.asyncio
async def test_a_refit_records_the_setups_its_labels_were_scored_under(
    async_session_factory, stamped, monkeypatch
):
    """The run a label's evidence came from says which setup; a label whose
    run is gone is taken under its sample's version."""
    run_id = gen_id()
    async with async_session_factory() as session:
        # Scored under the first version, then moved on to the second.
        item = await session.get(SampleItem, stamped["on_first"])
        item.ionization_mode_id = stamped["second"]
        session.add(
            PeakAssignmentRun(
                peak_assignment_run_id=run_id,
                sample_item_id=stamped["on_first"],
                engine_version="test",
                status="completed",
                config={
                    "ionization_setup": {
                        "ionization_mode_id": stamped["first"],
                        "version": 1,
                    }
                },
                peak_assignment_run_utc_created=_NOW,
            )
        )
        for item_id, run, verdict in (
            (stamped["on_first"], run_id, "confirmed"),
            (stamped["on_second"], gen_id(), "rejected"),
        ):
            session.add(
                AssignmentVerification(
                    assignment_verification_id=gen_id(),
                    sample_item_id=item_id,
                    peak_assignment_run_id=run,
                    sample_peak_id=gen_id(8),
                    verdict=verdict,
                    evidence=0.5,
                    verified_utc=_NOW,
                )
            )
        await session.commit()

    saved = AsyncMock()
    monkeypatch.setattr(assignments, "save_calibration", saved)
    monkeypatch.setattr(assignments, "load_calibration", AsyncMock(return_value=None))
    monkeypatch.setattr(
        assignments,
        "recalibrate",
        # What the response reads of a fit, beyond the curve: counts.
        lambda *args, **kwargs: defaultdict(int, calibration=_curve("orbi")),
    )
    await assignments.recalibrate_instrument("orbi")

    setups = saved.await_args.args[2]
    assert {"ionization_mode_id": stamped["first"], "version": 1} in setups
    assert {"ionization_mode_id": stamped["second"], "version": 2} in setups
    async with async_session_factory() as session:
        await session.execute(
            delete(PeakAssignmentRun).where(
                PeakAssignmentRun.peak_assignment_run_id == run_id
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_the_store_writes_the_setups_on_the_row(
    instrument_class, async_session_factory
):
    fit = [{"ionization_mode_id": "m9", "version": 3}]
    await save_calibration(_curve(instrument_class), 1, fit)
    async with async_session_factory() as session:
        row = (
            await session.execute(
                select(AssignmentCalibration).where(
                    AssignmentCalibration.instrument == instrument_class,
                    AssignmentCalibration.is_active.is_(True),
                )
            )
        ).scalar_one()
    assert row.ionization_setups == fit
