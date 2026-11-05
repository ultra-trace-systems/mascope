"""What a run's envelope tolerance reads from the database (step 3.5b).

A thin Orbitrap file pools the lines its batch siblings' latest completed
in-app runs recorded under the same acquisition settings
(``envelope_fit.sibling_lines``); a formula measured outside any run reads the
tolerance the sample's latest completed in-app run recorded
(``envelope_fit.recorded_envelope_tolerance``); the settings are the sample's
stream's where it points at one, else its file's ``.props``
(``envelope_fit.acquisition_settings``).
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pytest
import pytest_asyncio

from mascope_backend.api.new.peak_assignments import envelope_fit
from mascope_backend.api.new.peak_assignments.config import IN_APP_ENGINE
from mascope_backend.db import (
    AcquisitionStream,
    Dataset,
    PeakAssignmentRun,
    SampleBatch,
    SampleFile,
    SampleItem,
    Workspace,
)
from mascope_backend.db.id import gen_id
from mascope_tools.composition.envelope_tolerance import (
    FORM_CENTRE_WIDTH,
    FORM_CURVE,
    EnvelopeLines,
    EnvelopeTolerance,
    fit_envelope_tolerance,
)


pytestmark = pytest.mark.asyncio

_NOW = datetime.now(timezone.utc)
TEN = {"polarity": "-", "microscans": 10.0, "resolution": 120000.0}
HUNDRED = {"polarity": "-", "microscans": 100.0, "resolution": 120000.0}


def _record(ratio: float, acquisition: dict, lines: int = 12) -> dict:
    """A curve run's record of ``lines`` 13C lines read at ``ratio``."""
    record = fit_envelope_tolerance(
        FORM_CURVE, EnvelopeLines(np.full(lines, 15.0), np.full(lines, ratio))
    ).record()
    record["acquisition"] = acquisition
    return record


@pytest_asyncio.fixture
async def batch(async_session_factory, pa_sample_view):
    """A batch of samples, each with one file, and a sample of another batch.

    :return: The ids, and a ``run`` writer.
    """
    workspace_id, dataset_id = gen_id(), gen_id()
    batch_id, other_batch_id = gen_id(), gen_id()
    samples: dict[str, str] = {}
    streams: dict[str, str] = {}
    async with async_session_factory() as session:
        session.add(
            Workspace(
                workspace_id=workspace_id,
                workspace_name=f"Envelope Fit Workspace {workspace_id}",
                workspace_status="active",
                workspace_utc_created=_NOW,
                workspace_utc_modified=_NOW,
            )
        )
        session.add(
            Dataset(
                dataset_id=dataset_id,
                workspace_id=workspace_id,
                dataset_name="Envelope Fit Dataset",
                dataset_utc_created=_NOW,
            )
        )
        for sample_batch_id in (batch_id, other_batch_id):
            session.add(
                SampleBatch(
                    sample_batch_id=sample_batch_id,
                    dataset_id=dataset_id,
                    sample_batch_name="Envelope Fit Batch",
                    sample_batch_utc_created=_NOW,
                )
            )
        await session.flush()
        for name in ("S", "A", "B", "C", "D", "E", "F"):
            file_id, item_id = gen_id(), gen_id()
            session.add(
                SampleFile(
                    sample_file_id=file_id,
                    filename=f"envelope-fit-{file_id}.zarr",
                    instrument="orbi-test",
                    datetime=datetime(2026, 7, 4, 12, 0, 0),
                    datetime_utc=_NOW,
                    length=60.0,
                    range=[50.0, 500.0],
                    polarity="-",
                )
            )
            await session.flush()
            stream_id = None
            if name == "S":
                stream_id = gen_id()
                session.add(
                    AcquisitionStream(
                        stream_id=stream_id,
                        sample_file_id=file_id,
                        stream_key="FTMS - p ESI Full ms [50.0000-500.0000]",
                        signature_key="FTMS - p ESI Full ms [50.0000-500.0000]",
                        signature={"polarity": "-", "ms_order": 1},
                        acquisition_params={
                            "constant": {
                                "Micro Scan Count:": 10,
                                "FT Resolution:": "120000",
                            },
                            "varying": [],
                        },
                        scan_count=2,
                        blocks=1,
                        t_first=0.0,
                        t_last=60.0,
                    )
                )
                await session.flush()
                streams[name] = stream_id
            session.add(
                SampleItem(
                    sample_item_id=item_id,
                    sample_batch_id=other_batch_id if name == "C" else batch_id,
                    sample_file_id=file_id,
                    sample_item_name=f"Envelope Fit {name}",
                    sample_item_type="sample",
                    polarity="-",
                    stream_id=stream_id,
                    sample_item_utc_created=_NOW,
                )
            )
            samples[name] = item_id
        await session.commit()

    async def run(
        name, record, *, minutes_ago, status="completed", engine=IN_APP_ENGINE
    ):
        async with async_session_factory() as session:
            session.add(
                PeakAssignmentRun(
                    peak_assignment_run_id=gen_id(),
                    sample_item_id=samples[name],
                    engine=engine,
                    engine_version="0.1.0-test",
                    status=status,
                    config={"run_untargeted": True, "envelope_tolerance": record},
                    peak_assignment_run_utc_created=_NOW
                    - timedelta(minutes=minutes_ago),
                )
            )
            await session.commit()

    return SimpleNamespace(id=batch_id, samples=samples, streams=streams, run=run)


def _sample(batch, name: str, filename: str = "orbi-sample.raw") -> SimpleNamespace:
    return SimpleNamespace(
        sample_item_id=batch.samples[name],
        sample_item_name=f"Envelope Fit {name}",
        sample_batch_id=batch.id,
        filename=filename,
        polarity="-",
    )


async def test_a_thin_file_pools_its_siblings_latest_lines_under_its_settings(batch):
    # A's latest completed in-app run, not its older one nor its newer failed
    # one; not B, acquired at other settings; not C, of another batch; not D,
    # whose only run is an import, which says nothing of how the in-app scorer
    # read a line; not E, whose latest fitted a TOF's form; not F, whose
    # record kept no lines; and not the sample's own.
    await batch.run("A", _record(0.5, TEN), minutes_ago=60)
    await batch.run("A", _record(0.7, TEN), minutes_ago=30)
    await batch.run("A", _record(0.1, TEN), minutes_ago=5, status="failed")
    await batch.run("B", _record(0.6, HUNDRED), minutes_ago=30)
    await batch.run("C", _record(0.6, TEN), minutes_ago=30)
    await batch.run("D", _record(0.6, TEN), minutes_ago=30, engine="ext")
    tof = fit_envelope_tolerance(
        FORM_CENTRE_WIDTH, EnvelopeLines(np.full(25, 10.0), np.full(25, 0.9))
    ).record()
    tof["acquisition"] = TEN
    tof["observations"] = _record(0.6, TEN)["observations"]
    await batch.run("E", tof, minutes_ago=30)
    await batch.run("F", dict(_record(0.6, TEN), observations=None), minutes_ago=30)
    await batch.run("S", _record(0.9, TEN), minutes_ago=30)

    pooled = await envelope_fit.sibling_lines(_sample(batch, "S"), TEN)

    assert len(pooled) == 1
    np.testing.assert_allclose(pooled[0].ratio, 0.7)


async def test_a_sample_in_no_batch_pools_nothing(batch):
    sample = _sample(batch, "S")
    sample.sample_batch_id = None

    assert await envelope_fit.sibling_lines(sample, TEN) == []


async def test_outside_a_run_a_line_is_read_at_the_latest_runs_tolerance(batch):
    fitted = _record(0.8, TEN, lines=25)
    await batch.run("S", _record(0.5, TEN, lines=25), minutes_ago=60)
    await batch.run("S", fitted, minutes_ago=30)
    await batch.run("S", _record(0.2, TEN, lines=25), minutes_ago=5, status="running")

    tolerance = await envelope_fit.recorded_envelope_tolerance(batch.samples["S"])

    assert tolerance == EnvelopeTolerance.from_record(fitted)
    assert tolerance.lower[1] == pytest.approx(0.8)


async def test_a_sample_no_run_fitted_is_read_at_today_s_numbers(batch):
    assert (
        await envelope_fit.recorded_envelope_tolerance(batch.samples["A"])
        == EnvelopeTolerance()
    )


async def test_the_settings_are_the_sample_s_stream_s(batch, monkeypatch):
    def unread(filename):
        raise AssertionError("a sample on a stream is not read off its .props")

    monkeypatch.setattr(envelope_fit.m_io, "read_props", unread)

    assert await envelope_fit.acquisition_settings(_sample(batch, "S")) == TEN


async def test_else_the_file_s_own(batch, monkeypatch):
    params = {"constant": {"Micro Scan Count:": "100", "FT Resolution:": 120000}}
    monkeypatch.setattr(
        envelope_fit.m_io,
        "read_props",
        lambda filename: {"acquisition_params": params},
    )

    assert await envelope_fit.acquisition_settings(_sample(batch, "A")) == HUNDRED


@pytest.mark.parametrize(
    "props",
    [
        OSError("no .props"),
        {"acquisition_params": None},
        {"acquisition_params": {"constant": {}, "varying": ["Micro Scan Count:"]}},
    ],
    ids=["unreadable", "none recorded", "varying"],
)
async def test_a_file_that_records_neither_count_has_no_settings(
    batch, monkeypatch, props
):
    def read(filename):
        if isinstance(props, Exception):
            raise props
        return props

    monkeypatch.setattr(envelope_fit.m_io, "read_props", read)

    assert await envelope_fit.acquisition_settings(_sample(batch, "A")) is None
