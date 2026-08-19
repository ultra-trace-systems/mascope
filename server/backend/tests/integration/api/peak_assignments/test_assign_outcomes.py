"""
Integration tests for the synchronous outcomes of the assign endpoints.

Both endpoints decide before they answer, so what a caller learns is a response
body rather than a socket notification a headless client cannot receive:

- the per-sample endpoint answers 202 with the run it created (and the engine
  adopts that run), 409 naming the run already in flight, or 422 naming why the
  sample cannot be assigned;
- the batch endpoint answers 202 with the eligibility partition it will execute -
  and no run ids, because it creates no runs - or 409 naming the samples held up.

These bodies have to survive the app's error sanitizer, which is why the refusal
tests assert the *content* of `detail`, not just the status code.

The feature flag is forced through the ``MASCOPE_PEAK_ASSIGNMENT`` env override,
like the other write tests, so these do not depend on the test environment's
``[meta]`` config.
"""

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, update

from mascope_backend.api.new.peak_assignments import batch as batch_module
from mascope_backend.api.new.peak_assignments import routes as routes_module
from mascope_backend.api.new.peak_assignments import service as service_module
from mascope_backend.api.new.peak_assignments.admission import in_flight_run_id
from mascope_backend.api.new.peak_assignments.batch import (
    assign_sample_batch_peaks,
    partition_batch_samples,
)
from mascope_backend.api.new.peak_assignments.service import (
    assign_sample_peaks,
    create_pending_run,
)
from mascope_backend.db import (
    Dataset,
    InstrumentFunction,
    PeakAssignmentRun,
    SampleBatch,
    SampleFile,
    SampleItem,
)
from mascope_backend.db.id import gen_id


BLANK_REASON = "blank sample (no peaks)"
UNCALIBRATED_REASON = "m/z calibration not verified"


@pytest.fixture
def feature_enabled(monkeypatch):
    """Force the peak assignment feature on for the test."""
    monkeypatch.setenv("MASCOPE_PEAK_ASSIGNMENT", "1")


def _sample_url(sample_item_id: str) -> str:
    return f"/api/peak-assignments/sample/{sample_item_id}/assign"


def _batch_url(sample_batch_id: str) -> str:
    return f"/api/peak-assignments/batch/{sample_batch_id}/assign"


async def _add_run(session_factory, sample_item_id: str, status: str) -> str:
    """Seed a run for a sample in the given state, and return its id."""
    run_id = gen_id()
    async with session_factory() as session:
        session.add(
            PeakAssignmentRun(
                peak_assignment_run_id=run_id,
                sample_item_id=sample_item_id,
                engine="mascope",
                engine_version="test",
                status=status,
                peak_assignment_run_utc_created=datetime.now(timezone.utc),
            )
        )
        await session.commit()
    return run_id


async def _runs_of(session_factory, sample_item_ids) -> list[PeakAssignmentRun]:
    async with session_factory() as session:
        return list(
            (
                await session.execute(
                    select(PeakAssignmentRun).where(
                        PeakAssignmentRun.sample_item_id.in_(tuple(sample_item_ids))
                    )
                )
            )
            .scalars()
            .all()
        )


@pytest_asyncio.fixture
async def assign_batch(async_session_factory, pa_test_data):
    """A batch of this workspace holding one measured sample and one blank.

    Its own dataset and batch, not ``pa_test_data``'s: the partition an endpoint
    reports covers every sample of the batch, so a shared one would make these
    assertions depend on what other suites seeded into it. Removed afterwards, so
    runs one test leaves behind cannot decide another's admission.
    """
    now = datetime.now(timezone.utc)
    dataset_id = gen_id()
    sample_batch_id = gen_id()
    instrument_function_id = gen_id(32)
    measured_file_id = gen_id()
    blank_file_id = gen_id()
    measured_id = gen_id()
    blank_id = gen_id()

    async with async_session_factory() as session:
        session.add(
            Dataset(
                dataset_id=dataset_id,
                workspace_id=pa_test_data["workspace_id"],
                dataset_name="Assign Outcomes Dataset",
                dataset_utc_created=now,
            )
        )
        session.add(
            SampleBatch(
                sample_batch_id=sample_batch_id,
                dataset_id=dataset_id,
                sample_batch_name="Assign Outcomes Batch",
                sample_batch_utc_created=now,
            )
        )
        session.add(
            InstrumentFunction(
                instrument_function_id=instrument_function_id,
                instrument="orbi-test",
                method_file="assign-outcomes.meth",
                datetime_utc=now,
            )
        )
        # A sample counts as measured precisely by having an instrument function;
        # without one it is a blank, which is the eligibility rule under test.
        session.add(
            SampleFile(
                sample_file_id=measured_file_id,
                instrument_function_id=instrument_function_id,
                filename=f"assign-measured-{measured_file_id}.zarr",
                instrument="orbi-test",
                datetime=datetime(2026, 8, 19, 12, 0, 0),
                datetime_utc=now,
                length=60.0,
                range=[50.0, 500.0],
                polarity="+",
            )
        )
        session.add(
            SampleFile(
                sample_file_id=blank_file_id,
                filename=f"assign-blank-{blank_file_id}.zarr",
                instrument="orbi-test",
                datetime=datetime(2026, 8, 19, 12, 30, 0),
                datetime_utc=now,
                length=60.0,
                range=[50.0, 500.0],
                polarity="+",
            )
        )
        # Names order the batch: "A ..." before "B ...", which is the order the
        # partition reports and the task visits.
        session.add(
            SampleItem(
                sample_item_id=measured_id,
                sample_batch_id=sample_batch_id,
                sample_file_id=measured_file_id,
                sample_item_name="A Measured Sample",
                sample_item_type="sample",
                polarity="+",
                t0=0.0,
                t1=60.0,
                sample_item_utc_created=now,
            )
        )
        session.add(
            SampleItem(
                sample_item_id=blank_id,
                sample_batch_id=sample_batch_id,
                sample_file_id=blank_file_id,
                sample_item_name="B Blank Sample",
                sample_item_type="blank",
                polarity="+",
                sample_item_utc_created=now,
            )
        )
        await session.commit()

    yield {
        "sample_batch_id": sample_batch_id,
        "measured": measured_id,
        "blank": blank_id,
        "measured_file_id": measured_file_id,
        "blank_file_id": blank_file_id,
        "instrument_function_id": instrument_function_id,
    }

    async with async_session_factory() as session:
        await session.execute(
            delete(SampleItem).where(
                SampleItem.sample_item_id.in_((measured_id, blank_id))
            )
        )
        await session.execute(
            delete(SampleFile).where(
                SampleFile.sample_file_id.in_((measured_file_id, blank_file_id))
            )
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


@pytest.fixture
def launched_samples(monkeypatch):
    """Record the per-sample background task instead of running the engine.

    The engine needs a real peak file; what these tests are about is the handover
    - which run id the route creates and passes on - so the task is recorded.
    """
    calls: list[dict] = []

    async def _assign(**kwargs):
        calls.append(kwargs)
        return {"status": "success", "message": "stubbed"}

    monkeypatch.setattr(routes_module, "assign_sample_peaks", _assign)
    return calls


@pytest.fixture
def launched_batches(monkeypatch):
    """Record the batch background task, capturing the partition handed to it."""
    calls: list[dict] = []

    async def _assign_batch(**kwargs):
        calls.append(kwargs)
        return {"status": "success", "message": "stubbed"}

    monkeypatch.setattr(routes_module, "assign_sample_batch_peaks", _assign_batch)
    return calls


class TestSampleAccepted:
    """202 names the run this request created, before the engine starts."""

    @pytest.mark.asyncio
    async def test_the_response_carries_the_new_run_id(
        self,
        editor_client,
        assign_batch,
        feature_enabled,
        launched_samples,
        async_session_factory,
    ):
        response = await editor_client.post(_sample_url(assign_batch["measured"]))

        assert response.status_code == 202
        record = response.json()["data"][0]
        assert record["sample_item_id"] == assign_batch["measured"]
        assert record["run_status"] == "pending"

        run = (await _runs_of(async_session_factory, [assign_batch["measured"]])).pop()
        assert run.peak_assignment_run_id == record["peak_assignment_run_id"]
        assert run.status == "pending"
        assert run.engine == "mascope"

    @pytest.mark.asyncio
    async def test_the_engine_adopts_that_run_rather_than_minting_its_own(
        self, editor_client, assign_batch, feature_enabled, launched_samples
    ):
        response = await editor_client.post(_sample_url(assign_batch["measured"]))

        run_id = response.json()["data"][0]["peak_assignment_run_id"]
        assert len(launched_samples) == 1
        assert launched_samples[0]["run_id"] == run_id
        assert launched_samples[0]["sample_item_id"] == assign_batch["measured"]

    @pytest.mark.asyncio
    async def test_the_created_run_holds_the_sample_against_a_second_request(
        self, editor_client, assign_batch, feature_enabled, launched_samples
    ):
        """The window the synchronous answer opens is closed by the run itself.

        The background task has not started, so nothing in-process holds the
        sample; the durable run created in the request is what refuses the next
        caller.
        """
        first = await editor_client.post(_sample_url(assign_batch["measured"]))
        second = await editor_client.post(_sample_url(assign_batch["measured"]))

        assert second.status_code == 409
        assert (
            second.json()["detail"]["peak_assignment_run_id"]
            == first.json()["data"][0]["peak_assignment_run_id"]
        )


class TestSampleRefused:
    """409 and 422 carry what a client needs, past the error sanitizer.

    The app genericizes error bodies to an opaque ``error_id``. A refusal whose
    run id or reason is sanitized away tells a headless client nothing it can
    act on, so these assert the payload, not only the status.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", ["pending", "running", "importing"])
    async def test_a_run_in_flight_is_refused_and_named(
        self,
        editor_client,
        assign_batch,
        feature_enabled,
        launched_samples,
        async_session_factory,
        status,
    ):
        run_id = await _add_run(async_session_factory, assign_batch["measured"], status)

        response = await editor_client.post(_sample_url(assign_batch["measured"]))

        assert response.status_code == 409
        assert response.json()["detail"]["peak_assignment_run_id"] == run_id
        assert response.json()["detail"]["sample_item_id"] == assign_batch["measured"]
        # Refused before a second run could be created.
        assert len(launched_samples) == 0

    @pytest.mark.asyncio
    async def test_a_refusal_creates_no_run(
        self,
        editor_client,
        assign_batch,
        feature_enabled,
        launched_samples,
        async_session_factory,
    ):
        await _add_run(async_session_factory, assign_batch["measured"], "running")

        await editor_client.post(_sample_url(assign_batch["measured"]))

        runs = await _runs_of(async_session_factory, [assign_batch["measured"]])
        assert len(runs) == 1

    @pytest.mark.asyncio
    async def test_a_blank_sample_is_refused_with_its_reason(
        self, editor_client, assign_batch, feature_enabled, launched_samples
    ):
        response = await editor_client.post(_sample_url(assign_batch["blank"]))

        assert response.status_code == 422
        detail = response.json()["detail"]
        assert detail["reason"] == BLANK_REASON
        assert detail["sample_item_id"] == assign_batch["blank"]
        # The prose says the same thing, for a user reading a toast.
        assert BLANK_REASON in response.json()["error"]
        assert len(launched_samples) == 0

    @pytest.mark.asyncio
    async def test_an_unverified_calibration_is_refused_with_its_reason(
        self,
        editor_client,
        assign_batch,
        feature_enabled,
        launched_samples,
        async_session_factory,
    ):
        async with async_session_factory() as session:
            await session.execute(
                update(SampleFile)
                .where(SampleFile.sample_file_id == assign_batch["blank_file_id"])
                .values(
                    instrument_function_id=assign_batch["instrument_function_id"],
                    mz_calibration={"verified": False},
                )
            )
            await session.commit()

        response = await editor_client.post(_sample_url(assign_batch["blank"]))

        assert response.status_code == 422
        assert response.json()["detail"]["reason"] == UNCALIBRATED_REASON

    @pytest.mark.asyncio
    async def test_an_ineligible_sample_creates_no_run(
        self,
        editor_client,
        assign_batch,
        feature_enabled,
        launched_samples,
        async_session_factory,
    ):
        await editor_client.post(_sample_url(assign_batch["blank"]))

        assert await _runs_of(async_session_factory, [assign_batch["blank"]]) == []


class TestAdoptedRunAdmission:
    """A run handed to the engine must not be refused by its own existence."""

    @pytest.mark.asyncio
    async def test_the_adopted_run_is_excluded_from_the_in_flight_query(
        self, assign_batch, async_session_factory
    ):
        run_id = await _add_run(
            async_session_factory, assign_batch["measured"], "pending"
        )

        assert await in_flight_run_id(assign_batch["measured"]) == run_id
        assert (
            await in_flight_run_id(assign_batch["measured"], exclude_run_id=run_id)
            is None
        )

    @pytest.mark.asyncio
    async def test_the_engine_runs_the_run_it_was_handed(
        self, assign_batch, monkeypatch
    ):
        run = await create_pending_run(assign_batch["measured"])
        reached: list[str | None] = []

        async def _engine(**kwargs):
            reached.append(kwargs.get("run_id"))
            return {"status": "success", "message": "stubbed"}

        monkeypatch.setattr(service_module, "_run_sample_assignment", _engine)

        result = await assign_sample_peaks(
            sample_item_id=assign_batch["measured"],
            run_id=run.peak_assignment_run_id,
        )

        assert result["status"] == "success"
        assert reached == [run.peak_assignment_run_id]

    @pytest.mark.asyncio
    async def test_another_run_still_refuses_the_adopted_one(
        self, assign_batch, async_session_factory, monkeypatch
    ):
        """The exclusion is scoped to the caller's own run, not to the rule."""
        run = await create_pending_run(assign_batch["measured"])
        other_run_id = await _add_run(
            async_session_factory, assign_batch["measured"], "running"
        )
        reached: list[str | None] = []

        async def _engine(**kwargs):
            reached.append(kwargs.get("run_id"))
            return {"status": "success", "message": "stubbed"}

        monkeypatch.setattr(service_module, "_run_sample_assignment", _engine)

        result = await assign_sample_peaks(
            sample_item_id=assign_batch["measured"],
            run_id=run.peak_assignment_run_id,
        )

        assert result["status"] == "skipped"
        assert result["data"]["peak_assignment_run_id"] == other_run_id
        assert reached == []


class TestBatchPartition:
    """The batch answers with what it will do, and creates no runs doing so."""

    @pytest.mark.asyncio
    async def test_the_response_carries_the_eligibility_partition(
        self, editor_client, assign_batch, feature_enabled, launched_batches
    ):
        response = await editor_client.post(_batch_url(assign_batch["sample_batch_id"]))

        assert response.status_code == 202
        record = response.json()["data"][0]
        assert record["sample_batch_id"] == assign_batch["sample_batch_id"]
        assert record["admitted"] == [assign_batch["measured"]]
        assert record["skipped"] == [
            {"sample_item_id": assign_batch["blank"], "reason": BLANK_REASON}
        ]

    @pytest.mark.asyncio
    async def test_no_runs_are_created_up_front(
        self,
        editor_client,
        assign_batch,
        feature_enabled,
        launched_batches,
        async_session_factory,
    ):
        """A pre-created run would block the very sample it was meant to assign.

        Durable admission refuses a sample that already has a non-terminal run,
        and a batch that stops early would leave one such row behind per sample
        it never reached. Runs are created as the batch reaches each sample.
        """
        await editor_client.post(_batch_url(assign_batch["sample_batch_id"]))

        runs = await _runs_of(
            async_session_factory, [assign_batch["measured"], assign_batch["blank"]]
        )
        assert runs == []

    @pytest.mark.asyncio
    async def test_the_task_receives_the_reported_partition(
        self, editor_client, assign_batch, feature_enabled, launched_batches
    ):
        response = await editor_client.post(_batch_url(assign_batch["sample_batch_id"]))

        record = response.json()["data"][0]
        partition = launched_batches[0]["partition"]
        assert list(partition.admitted) == record["admitted"]
        assert partition.skipped_payload() == record["skipped"]

    @pytest.mark.asyncio
    async def test_the_task_assigns_exactly_the_reported_partition(
        self,
        editor_client,
        assign_batch,
        feature_enabled,
        launched_batches,
        monkeypatch,
    ):
        """Sharing the value, not the rule, is what makes the two agree.

        A sample that becomes eligible after the response would join a
        recomputed partition - and be assigned by a run the caller was told
        would not happen. Executing the reported partition rules that out.
        """
        response = await editor_client.post(_batch_url(assign_batch["sample_batch_id"]))
        reported = response.json()["data"][0]

        # The skipped blank turns into a measured sample behind the response.
        async with batch_module.async_session() as session:
            await session.execute(
                update(SampleFile)
                .where(SampleFile.sample_file_id == assign_batch["blank_file_id"])
                .values(instrument_function_id=assign_batch["instrument_function_id"])
            )
            await session.commit()
        assert list(
            (await partition_batch_samples(assign_batch["sample_batch_id"])).admitted
        ) == [assign_batch["measured"], assign_batch["blank"]]

        visited: list[str] = []

        async def _assign(**kwargs):
            visited.append(kwargs["sample_item_id"])
            return {"status": "success", "message": "stubbed"}

        async def _progress(*args, **kwargs):
            return None

        monkeypatch.setattr(batch_module, "assign_sample_peaks", _assign)
        monkeypatch.setattr(batch_module, "send_progress_user_notification", _progress)

        await assign_sample_batch_peaks(**launched_batches[0])

        assert visited == reported["admitted"]

    @pytest.mark.asyncio
    async def test_a_batch_with_nothing_to_assign_still_reports_its_skips(
        self,
        editor_client,
        assign_batch,
        feature_enabled,
        launched_batches,
        async_session_factory,
    ):
        """Zero admitted is data, not a refusal - the caller has to tell them apart."""
        async with async_session_factory() as session:
            await session.execute(
                delete(SampleItem).where(
                    SampleItem.sample_item_id == assign_batch["measured"]
                )
            )
            await session.commit()

        response = await editor_client.post(_batch_url(assign_batch["sample_batch_id"]))

        assert response.status_code == 202
        record = response.json()["data"][0]
        assert record["admitted"] == []
        assert record["skipped"] == [
            {"sample_item_id": assign_batch["blank"], "reason": BLANK_REASON}
        ]


class TestBatchRefused:
    """409 names the samples that hold the batch up, not just the fact."""

    @pytest.mark.asyncio
    async def test_an_admitted_sample_in_flight_refuses_the_batch(
        self,
        editor_client,
        assign_batch,
        feature_enabled,
        launched_batches,
        async_session_factory,
    ):
        run_id = await _add_run(
            async_session_factory, assign_batch["measured"], "running"
        )

        response = await editor_client.post(_batch_url(assign_batch["sample_batch_id"]))

        assert response.status_code == 409
        detail = response.json()["detail"]
        assert detail["sample_batch_id"] == assign_batch["sample_batch_id"]
        assert detail["blocked"] == [
            {
                "sample_item_id": assign_batch["measured"],
                "peak_assignment_run_id": run_id,
            }
        ]
        assert len(launched_batches) == 0

    @pytest.mark.asyncio
    async def test_a_skipped_sample_in_flight_does_not_refuse_the_batch(
        self,
        editor_client,
        assign_batch,
        feature_enabled,
        launched_batches,
        async_session_factory,
    ):
        """Admission is asked about the samples that will actually run."""
        await _add_run(async_session_factory, assign_batch["blank"], "importing")

        response = await editor_client.post(_batch_url(assign_batch["sample_batch_id"]))

        assert response.status_code == 202
        assert response.json()["data"][0]["admitted"] == [assign_batch["measured"]]


class TestReclamation:
    """A run created in the request must always find its way to a terminal state.

    The startup reaper's coverage of 'pending' is pinned in the reaper's own unit
    tests; what needs a database here is the one path that can strand an adopted
    run without the process dying.
    """

    @pytest.mark.asyncio
    async def test_an_adopted_run_is_finalized_when_the_sample_turns_ineligible(
        self, assign_batch, async_session_factory
    ):
        """Otherwise the eligibility race would strand the run it created.

        The endpoint checks eligibility before creating the run, so this is only
        reachable when the sample changes in between - but a run left non-terminal
        would block every later assignment of that sample until the next restart.
        """
        run = await create_pending_run(assign_batch["measured"])
        async with async_session_factory() as session:
            await session.execute(
                update(SampleFile)
                .where(SampleFile.sample_file_id == assign_batch["measured_file_id"])
                .values(instrument_function_id=None)
            )
            await session.commit()

        result = await assign_sample_peaks(
            sample_item_id=assign_batch["measured"],
            run_id=run.peak_assignment_run_id,
        )

        assert result["status"] == "skipped"
        async with async_session_factory() as session:
            reloaded = await session.get(PeakAssignmentRun, run.peak_assignment_run_id)
        assert reloaded.status == "failed"
        assert await in_flight_run_id(assign_batch["measured"]) is None


class TestGating:
    """The synchronous outcomes do not open a way past the feature gate."""

    @pytest.mark.asyncio
    async def test_assign_is_still_refused_when_the_feature_is_disabled(
        self, editor_client, assign_batch, monkeypatch, async_session_factory
    ):
        monkeypatch.setenv("MASCOPE_PEAK_ASSIGNMENT", "0")

        response = await editor_client.post(_sample_url(assign_batch["measured"]))

        assert response.status_code == 403
        # The gate runs as a dependency, so no run is created on the way out.
        assert await _runs_of(async_session_factory, [assign_batch["measured"]]) == []

    @pytest.mark.asyncio
    async def test_a_guest_cannot_launch_a_run(
        self, guest_client, assign_batch, feature_enabled, async_session_factory
    ):
        response = await guest_client.post(_sample_url(assign_batch["measured"]))

        assert response.status_code == 403
        assert await _runs_of(async_session_factory, [assign_batch["measured"]]) == []
