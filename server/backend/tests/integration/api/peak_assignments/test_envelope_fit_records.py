"""What a run's envelope tolerance reads from the database (step 3.5b).

A thin Orbitrap file pools the lines of the files of its batch nearest it in
acquisition time that share its acquisition settings, each read fresh and never
from a run of theirs (``envelope_fit.read_pool``), so the curve is the same
whichever of them ran before it, or at once with it; a formula measured outside
any run reads the tolerance the sample's latest completed in-app run recorded,
with the rest of its model (``service.recorded_measurement``); the settings are
the sample's stream's where it points at one, else its file's ``.props``
(``envelope_fit.acquisition_settings``). A decoy run is the sample's for no
reader: it scored at its control's tolerance (step 5.2).
"""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
import pytest_asyncio

from mascope_backend.api.controllers.samples.lib.samples_fetch import fetch_sample
from mascope_backend.api.new.peak_assignments import envelope_fit, service
from mascope_backend.api.new.peak_assignments.config import IN_APP_ENGINE
from mascope_backend.api.new.peak_assignments.engine import SampleMassAccuracy
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
    FORM_CURVE,
    LEVEL_POOLED,
    EnvelopeLines,
    EnvelopeTolerance,
    fit_envelope_tolerance,
)
from mascope_tools.composition.measurement_model import MeasurementModel
from mascope_tools.composition.profiles import INSTRUMENT_FALLBACK_SIGMA_PPM


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
        name,
        record,
        *,
        minutes_ago,
        status="completed",
        engine=IN_APP_ENGINE,
        decoy=None,
    ):
        async with async_session_factory() as session:
            session.add(
                PeakAssignmentRun(
                    peak_assignment_run_id=gen_id(),
                    sample_item_id=samples[name],
                    engine=engine,
                    engine_version="0.1.0-test",
                    status=status,
                    decoy=decoy,
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


def _stream(file_id: str, microscans: int) -> AcquisitionStream:
    return AcquisitionStream(
        stream_id=gen_id(),
        sample_file_id=file_id,
        stream_key="FTMS - p ESI Full ms [50.0000-500.0000]",
        signature_key="FTMS - p ESI Full ms [50.0000-500.0000]",
        signature={"polarity": "-", "ms_order": 1},
        acquisition_params={
            "constant": {
                "Micro Scan Count:": microscans,
                "FT Resolution:": "120000",
            },
            "varying": [],
        },
        scan_count=2,
        blocks=1,
        t_first=0.0,
        t_last=60.0,
    )


#: When the sequence below started.
T0 = datetime(2026, 7, 4, 8, 0, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def sequence(async_session_factory, pa_sample_view, batch):
    """An Orbitrap sequence of thin files, one every ten minutes, in a batch of
    its own: ``X0`` to ``X8`` at 10 microscans, ``H`` at 100 between ``X3``
    and ``X4``, and ``Y`` at 10 in another batch, at ``X4``'s time.

    :return: The ids by name, the batch, and a ``run`` writer.
    """
    sequence_id = gen_id()
    other_id = gen_id()
    samples: dict[str, str] = {}
    async with async_session_factory() as session:
        dataset_id = (
            await session.get(SampleBatch, batch.id)
        ).dataset_id  # the batch fixture's dataset
        for sample_batch_id in (sequence_id, other_id):
            session.add(
                SampleBatch(
                    sample_batch_id=sample_batch_id,
                    dataset_id=dataset_id,
                    sample_batch_name="Envelope Pool Sequence",
                    sample_batch_utc_created=_NOW,
                )
            )
        await session.flush()
        files = [(f"X{index}", 10 * index, 10, sequence_id) for index in range(9)]
        files += [("H", 35, 100, sequence_id), ("Y", 40, 10, other_id)]
        for name, minutes, microscans, sample_batch_id in files:
            file_id, item_id = gen_id(), gen_id()
            session.add(
                SampleFile(
                    sample_file_id=file_id,
                    filename=f"orbi-envelope-pool-{file_id}.raw",
                    instrument="orbi-test",
                    datetime=datetime(2026, 7, 4, 8, 0, 0),
                    datetime_utc=T0 + timedelta(minutes=minutes),
                    length=60.0,
                    range=[50.0, 500.0],
                    polarity="-",
                )
            )
            await session.flush()
            stream = _stream(file_id, microscans)
            session.add(stream)
            await session.flush()
            session.add(
                SampleItem(
                    sample_item_id=item_id,
                    sample_batch_id=sample_batch_id,
                    sample_file_id=file_id,
                    sample_item_name=f"Envelope Pool {name}",
                    sample_item_type="sample",
                    polarity="-",
                    stream_id=stream.stream_id,
                    sample_item_utc_created=_NOW,
                )
            )
            samples[name] = item_id
        await session.commit()

    async def run(name, record):
        async with async_session_factory() as session:
            session.add(
                PeakAssignmentRun(
                    peak_assignment_run_id=gen_id(),
                    sample_item_id=samples[name],
                    engine=IN_APP_ENGINE,
                    engine_version="0.1.0-test",
                    status="completed",
                    config={"run_untargeted": True, "envelope_tolerance": record},
                    peak_assignment_run_utc_created=datetime.now(timezone.utc),
                )
            )
            await session.commit()

    return SimpleNamespace(id=sequence_id, samples=samples, run=run)


async def test_the_batch_is_read_nearest_in_acquisition_time_first(sequence):
    sample = await fetch_sample(sequence.samples["X3"])

    candidates = await envelope_fit._batch_candidates(sample)

    names = {item_id: name for name, item_id in sequence.samples.items()}
    order = [names[candidate.sample_item_id] for candidate in candidates]
    # Not the file itself, nor Y of another batch; ties in time by sample id.
    assert sorted(order) == sorted(
        ["X0", "X1", "X2", "X4", "X5", "X6", "X7", "X8", "H"]
    )
    distance = {
        name: abs(
            (
                next(
                    c for c in candidates if names[c.sample_item_id] == name
                ).datetime_utc
                - sample.datetime_utc
            ).total_seconds()
        )
        for name in order
    }
    assert order == sorted(
        order, key=lambda name: (distance[name], sequence.samples[name])
    )
    assert order[0] == "H"


def _reader(lines_of: dict[str, EnvelopeLines], read: list[str] | None = None):
    """A neighbour's first pass, stood in for: fixed lines per sample."""

    async def read_neighbour(neighbour):
        if read is not None:
            read.append(neighbour.sample_item_id)
        return lines_of[neighbour.sample_item_id]

    return read_neighbour


async def _fit(sample_item_id: str, read_neighbour) -> envelope_fit.RunEnvelopeFit:
    """A run's fit on a file whose own lists committed nothing: it is thin."""
    return await envelope_fit.fit_run_envelope_tolerance(
        await fetch_sample(sample_item_id),
        pd.DataFrame(),
        [],
        model=MeasurementModel.v1(
            SampleMassAccuracy(), INSTRUMENT_FALLBACK_SIGMA_PPM["orbi"]
        ),
        read_neighbour=read_neighbour,
    )


async def test_a_thin_file_pools_its_nearest_files_at_its_settings(sequence):
    # Five lines per file, each file's at a ratio of its own.
    lines_of = {
        item_id: EnvelopeLines(np.full(5, 15.0), np.full(5, 0.5 + index / 100))
        for index, item_id in enumerate(sequence.samples.values())
    }
    read: list[str] = []

    fit = await _fit(sequence.samples["X3"], _reader(lines_of, read))

    names = {item_id: name for name, item_id in sequence.samples.items()}
    pooled = [names[entry["sample_item_id"]] for entry in fit.record()["pool"]["files"]]
    # The six nearest at 10 microscans: H, at 100, and Y, of another batch,
    # take no place.
    assert sorted(pooled) == ["X0", "X1", "X2", "X4", "X5", "X6"]
    assert [names[item_id] for item_id in read] == pooled
    assert (fit.fit.level, fit.fit.pooled_files, fit.fit.pooled_lines) == (
        LEVEL_POOLED,
        7,
        30,
    )
    record = fit.record()
    assert {entry["lines"] for entry in record["pool"]["files"]} == {5}
    assert record["pool"]["span"] == {
        "from": T0.isoformat(),
        "to": (T0 + timedelta(minutes=60)).isoformat(),
        "hours": 1.0,
    }


async def test_a_thin_file_pools_alike_whatever_order_its_batch_ran_in(sequence):
    """The issue's batch: thin files of about five lines each. Run one after
    the other, again with every run in place, in reverse, and all at once,
    each file pools the same files and scores at the same curve."""
    names = [f"X{index}" for index in range(9)]
    lines_of = {
        item_id: EnvelopeLines(
            np.r_[np.full(4, 15.0), 6.0], np.r_[np.full(4, 0.6 + index / 50), np.nan]
        )
        for index, item_id in enumerate(sequence.samples.values())
    }
    read_neighbour = _reader(lines_of)

    first = {}
    for name in names:
        # Each run is recorded before the next starts, as a batch run one
        # file at a time records them.
        fit = await _fit(sequence.samples[name], read_neighbour)
        first[name] = fit.record()
        await sequence.run(name, first[name])
    again = {
        name: (await _fit(sequence.samples[name], read_neighbour)).record()
        for name in names
    }
    backwards = {
        name: (await _fit(sequence.samples[name], read_neighbour)).record()
        for name in reversed(names)
    }
    at_once = dict(
        zip(
            names,
            [
                fit.record()
                for fit in await asyncio.gather(
                    *(_fit(sequence.samples[name], read_neighbour) for name in names)
                )
            ],
        )
    )

    assert again == first
    assert backwards == first
    assert at_once == first
    # The first file of the batch to run pooled too: it did not wait for a
    # sibling's run.
    assert first["X0"]["level"] == LEVEL_POOLED
    assert len(first["X0"]["pool"]["files"]) == envelope_fit.POOL_NEIGHBOURS
    assert {
        EnvelopeTolerance.from_record(record) == EnvelopeTolerance()
        for record in first.values()
    } == {False}


async def _recorded_tolerance(sample_item_id: str) -> EnvelopeTolerance:
    """The tolerance a reader outside a run reads a sample's lines at: its
    model's, the reagent lines claimed as none."""
    sample = SimpleNamespace(
        sample_item_id=sample_item_id, filename="sample.raw", polarity="+"
    )
    model = await service.recorded_model(sample, reagent_offset=None)
    return model.envelope_tolerance


async def test_outside_a_run_a_line_is_read_at_the_latest_runs_tolerance(batch):
    fitted = _record(0.8, TEN, lines=25)
    await batch.run("S", _record(0.5, TEN, lines=25), minutes_ago=60)
    await batch.run("S", fitted, minutes_ago=30)
    await batch.run("S", _record(0.2, TEN, lines=25), minutes_ago=5, status="running")

    tolerance = await _recorded_tolerance(batch.samples["S"])

    assert tolerance == EnvelopeTolerance.from_record(fitted)
    assert tolerance.lower[1] == pytest.approx(0.8)


async def test_outside_a_run_a_decoy_run_s_tolerance_is_never_read(batch):
    control = _record(0.8, TEN, lines=25)
    await batch.run("S", control, minutes_ago=30)
    await batch.run(
        "S", _record(0.2, TEN, lines=25), minutes_ago=5, decoy="swap_adducts"
    )

    tolerance = await _recorded_tolerance(batch.samples["S"])

    assert tolerance == EnvelopeTolerance.from_record(control)


async def test_a_sample_no_run_fitted_is_read_at_today_s_numbers(batch):
    assert await _recorded_tolerance(batch.samples["A"]) == EnvelopeTolerance()


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
