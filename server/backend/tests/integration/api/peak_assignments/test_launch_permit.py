"""
Launched runs do not hold the worker's database permits.

A signed-in request's session holds one of the worker's ``db_semaphore``
permits. Before #3240 it kept that permit until the request's background tasks
had run, so each run launched through ``POST .../assign`` held one for the
whole run, and once runs held them all, every signed-in request on the worker
waited for a run to end.

These tests launch more runs than the worker has permits, through the real
route and its real sign-in, with a background task that waits on an event in
place of the engine. Every run has to start, and a signed-in read has to answer,
before that event is set.
"""

import asyncio
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from mascope_backend import db as db_module
from mascope_backend.api.new.peak_assignments import routes as routes_module
from mascope_backend.app.fast import fast
from mascope_backend.db import (
    AccessToken,
    Dataset,
    InstrumentFunction,
    PeakAssignmentRun,
    SampleBatch,
    SampleFile,
    SampleItem,
    hash_access_token,
)
from mascope_backend.db.id import gen_id


PERMITS = 2
RUNS = PERMITS + 1
# Generous for a loaded box, and far below a run's length: the runs here end
# only when the test lets them.
WAIT_S = 20


@pytest.fixture
def feature_enabled(monkeypatch):
    """Force the peak assignment feature on for the test."""
    monkeypatch.setenv("MASCOPE_PEAK_ASSIGNMENT", "1")


@pytest.fixture
def worker_permits(monkeypatch):
    """A worker with fewer permits than the runs the test launches.

    A fresh semaphore, so a permit left held by a failing test cannot reach
    the next one.
    """
    monkeypatch.setattr(db_module, "db_semaphore", asyncio.Semaphore(PERMITS))


@pytest_asyncio.fixture
async def measured_samples(async_session_factory, pa_test_data):
    """``RUNS`` measured samples in a batch of their own, removed afterwards."""
    now = datetime.now(timezone.utc)
    dataset_id = gen_id()
    sample_batch_id = gen_id()
    instrument_function_id = gen_id(32)
    file_ids = [gen_id() for _ in range(RUNS)]
    sample_ids = [gen_id() for _ in range(RUNS)]

    async with async_session_factory() as session:
        session.add(
            Dataset(
                dataset_id=dataset_id,
                workspace_id=pa_test_data["workspace_id"],
                dataset_name="Launch Permit Dataset",
                dataset_utc_created=now,
            )
        )
        session.add(
            SampleBatch(
                sample_batch_id=sample_batch_id,
                dataset_id=dataset_id,
                sample_batch_name="Launch Permit Batch",
                sample_batch_utc_created=now,
            )
        )
        session.add(
            InstrumentFunction(
                instrument_function_id=instrument_function_id,
                instrument="orbi-test",
                method_file="launch-permit.meth",
                datetime_utc=now,
            )
        )
        for index, (file_id, sample_id) in enumerate(zip(file_ids, sample_ids)):
            session.add(
                SampleFile(
                    sample_file_id=file_id,
                    instrument_function_id=instrument_function_id,
                    filename=f"launch-permit-{file_id}.zarr",
                    instrument="orbi-test",
                    datetime=datetime(2026, 8, 19, 12, index, 0),
                    datetime_utc=now,
                    length=60.0,
                    range=[50.0, 500.0],
                    polarity="+",
                )
            )
            session.add(
                SampleItem(
                    sample_item_id=sample_id,
                    sample_batch_id=sample_batch_id,
                    sample_file_id=file_id,
                    sample_item_name=f"Launch Permit Sample {index}",
                    sample_item_type="sample",
                    polarity="+",
                    t0=0.0,
                    t1=60.0,
                    sample_item_utc_created=now,
                )
            )
        await session.commit()

    yield sample_ids

    async with async_session_factory() as session:
        await session.execute(
            delete(PeakAssignmentRun).where(
                PeakAssignmentRun.sample_item_id.in_(sample_ids)
            )
        )
        await session.execute(
            delete(SampleItem).where(SampleItem.sample_item_id.in_(sample_ids))
        )
        await session.execute(
            delete(SampleFile).where(SampleFile.sample_file_id.in_(file_ids))
        )
        await session.execute(
            delete(InstrumentFunction).where(
                InstrumentFunction.instrument_function_id == instrument_function_id
            )
        )
        await session.execute(
            delete(SampleBatch).where(SampleBatch.sample_batch_id == sample_batch_id)
        )
        await session.execute(delete(Dataset).where(Dataset.dataset_id == dataset_id))
        await session.commit()


@pytest_asyncio.fixture
async def editor_sdk_client(async_session_factory, test_users):
    """A client sending a real API token of the editor, as the SDK does."""
    token = f"launch-{gen_id()}".ljust(43, "x")
    async with async_session_factory() as session:
        session.add(
            AccessToken(
                token_hash=hash_access_token(token),
                user_id=test_users["editor"].id,
                service_name="mascope_sdk",
            )
        )
        await session.commit()
    async with AsyncClient(
        transport=ASGITransport(app=fast),
        base_url="http://test",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Service-Name": "mascope_sdk",
        },
    ) as client:
        yield client
    async with async_session_factory() as session:
        await session.execute(
            delete(AccessToken).where(
                AccessToken.token_hash == hash_access_token(token)
            )
        )
        await session.commit()


@pytest.fixture
def held_runs(monkeypatch):
    """Runs that start, record their sample, and end only when released."""

    class _HeldRuns:
        def __init__(self):
            self.started: list[str] = []
            self.all_started = asyncio.Event()
            self.release = asyncio.Event()

        async def __call__(self, **kwargs):
            self.started.append(kwargs["sample_item_id"])
            if len(self.started) == RUNS:
                self.all_started.set()
            await self.release.wait()
            return {"status": "success", "message": "stubbed"}

    runs = _HeldRuns()
    monkeypatch.setattr(routes_module, "assign_sample_peaks", runs)
    return runs


def _assign_url(sample_item_id: str) -> str:
    return f"/api/peak-assignments/sample/{sample_item_id}/assign"


async def _launch_beyond_the_permits_then_read(client, sample_ids, held_runs):
    """Launch a run per sample, then read a signed-in route while all run."""
    # The transport returns a response only once its background tasks are
    # done, so each launch is a task of its own; the runs end on release.
    launches = [
        asyncio.create_task(client.post(_assign_url(sample_id)))
        for sample_id in sample_ids
    ]
    try:
        try:
            await asyncio.wait_for(held_runs.all_started.wait(), timeout=WAIT_S)
        except TimeoutError:
            pytest.fail(
                f"{len(held_runs.started)} of {RUNS} runs started on a worker "
                f"with {PERMITS} permits: requests or runs still hold them"
            )

        read = await asyncio.wait_for(client.get("/api/workspaces"), timeout=WAIT_S)

        assert read.status_code == 200, read.text
        assert not held_runs.release.is_set()
    finally:
        held_runs.release.set()
        # Bounded: a request that opened two sessions under these few permits
        # waits for a second one that the others hold, and never ends.
        _, stuck = await asyncio.wait(launches, timeout=WAIT_S)
        for launch in stuck:
            launch.cancel()
        await asyncio.gather(*launches, return_exceptions=True)

    assert not stuck, f"{len(stuck)} launches never answered"
    responses = [launch.result() for launch in launches]
    assert [response.status_code for response in responses] == [202] * RUNS
    assert sorted(held_runs.started) == sorted(sample_ids)


@pytest.mark.asyncio
async def test_signed_in_with_the_cookie(
    editor_client, measured_samples, feature_enabled, worker_permits, held_runs
):
    await _launch_beyond_the_permits_then_read(
        editor_client, measured_samples, held_runs
    )


@pytest.mark.asyncio
async def test_signed_in_with_an_sdk_token(
    editor_sdk_client, measured_samples, feature_enabled, worker_permits, held_runs
):
    await _launch_beyond_the_permits_then_read(
        editor_sdk_client, measured_samples, held_runs
    )
