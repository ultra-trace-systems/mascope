"""
Integration tests for importing externally computed assignment runs.

Covers the endpoint contract end to end - the chunked create/append/finalize
assembly, its idempotency under the client retries the SDK performs on its own,
admission against durable run state, the abandon endpoint - and the payload
rules that need a database to reach.

The feature flag is forced through the ``MASCOPE_PEAK_ASSIGNMENT`` env override,
like the other write tests, so these do not depend on the test environment's
``[meta]`` config.
"""

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.exc import DBAPIError

from mascope_backend.api.controllers.samples.lib.samples_fetch import fetch_sample
from mascope_backend.api.new.peak_assignments import import_service
from mascope_backend.db import (
    AssignmentVerification,
    InstrumentFunction,
    IonizationMechanism,
    PeakAssignment,
    PeakAssignmentRun,
    SampleFile,
    SampleItem,
)
from mascope_backend.db.id import gen_id


#: Peak ids the fixture sample pretends to have. Small on purpose: the total
#: row cap is "at most one row per peak", so a short list makes it reachable.
SAMPLE_PEAK_IDS = [f"peak-{index}" for index in range(6)]

TIER_BANDS = {"assigned": 0.8, "candidate": 0.5}
CALIBRATION = {"method": "client-side offset fit", "server_verified": False}


@pytest.fixture
def feature_enabled(monkeypatch):
    """Force the peak assignment feature on for the test."""
    monkeypatch.setenv("MASCOPE_PEAK_ASSIGNMENT", "1")


@pytest.fixture
def feature_disabled(monkeypatch):
    """Force the peak assignment feature off for the test."""
    monkeypatch.setenv("MASCOPE_PEAK_ASSIGNMENT", "0")


@pytest.fixture(autouse=True)
def stub_peak_file(monkeypatch):
    """Serve the sample's peak id set without a peak file behind it.

    The fixture samples carry a zarr filename that points at no real file, so
    the id-only peak read is stubbed. Everything it feeds - peak existence and
    the total row cap - is exercised for real against this set.
    """

    async def _peak_ids(_sample):
        return set(SAMPLE_PEAK_IDS)

    monkeypatch.setattr(import_service, "_load_peak_ids", _peak_ids)


@pytest.fixture(autouse=True)
def stub_fold_in(monkeypatch):
    """Keep the batch fold-in out of these tests.

    Finalizing an import folds it into the batch peaks, which is best-effort and
    covered by its own suite; here it would only add a slow no-op over fixture
    data. Recorded rather than dropped so the tests can assert it was reached.
    """
    from mascope_backend.api.new.peak_assignments import batch_peaks_controller

    calls = []

    async def _fold(sample_item_id):
        calls.append(sample_item_id)
        return None

    monkeypatch.setattr(batch_peaks_controller, "fold_sample_into_batch_peaks", _fold)
    return calls


@pytest_asyncio.fixture
async def import_sample(async_session_factory, pa_test_data):
    """A measured sample of the test workspace with no runs of its own.

    Separate from ``pa_test_data``'s sample, which deliberately carries a run
    left in 'running' - that sample can never accept an import, which is what
    the admission test uses it for. Created per test and removed afterwards, so
    runs one test leaves behind cannot decide another's admission.
    """
    now = datetime.now(timezone.utc)
    instrument_function_id = gen_id(32)
    sample_file_id = gen_id()
    sample_item_id = gen_id()

    async with async_session_factory() as session:
        session.add(
            InstrumentFunction(
                instrument_function_id=instrument_function_id,
                instrument="orbi-test",
                method_file="import-test.meth",
                datetime_utc=now,
            )
        )
        session.add(
            SampleFile(
                # A sample is "measured" rather than blank precisely by having
                # an instrument function, which is the eligibility rule an
                # import is refused under.
                instrument_function_id=instrument_function_id,
                sample_file_id=sample_file_id,
                filename=f"import-test-{sample_file_id}.zarr",
                instrument="orbi-test",
                datetime=datetime(2026, 8, 19, 12, 0, 0),
                datetime_utc=now,
                length=60.0,
                range=[50.0, 500.0],
                polarity="+",
            )
        )
        session.add(
            SampleItem(
                sample_item_id=sample_item_id,
                sample_batch_id=pa_test_data["sample_batch_id"],
                sample_file_id=sample_file_id,
                sample_item_name="Import Test Sample",
                sample_item_type="sample",
                polarity="+",
                t0=0.0,
                t1=60.0,
                sample_item_utc_created=now,
            )
        )
        await session.commit()

    yield sample_item_id

    async with async_session_factory() as session:
        await session.execute(
            delete(SampleItem).where(SampleItem.sample_item_id == sample_item_id)
        )
        await session.execute(
            delete(SampleFile).where(SampleFile.sample_file_id == sample_file_id)
        )
        await session.execute(
            delete(InstrumentFunction).where(
                InstrumentFunction.instrument_function_id == instrument_function_id
            )
        )
        await session.commit()


def _row(peak_id: str, *, tier="assigned", fit_score=0.92, **overrides) -> dict:
    """One import row, coherent with TIER_BANDS unless a test says otherwise."""
    row = {
        "sample_peak_id": peak_id,
        "sample_peak_mz": 181.0707,
        "sample_peak_intensity": 5000.0,
        "role": "M0",
        "assigned_formula": "C6H12O6",
        "ion_formula": "C6H13O6+",
        "source": "untargeted",
        "fit_score": fit_score,
        "tier": tier,
    }
    row.update(overrides)
    return row


def _body(
    rows,
    *,
    engine="peaky",
    complete=True,
    run_id=None,
    index=0,
    import_id="import-default",
    **overrides,
):
    """An import request body with the required run fields filled in.

    ``import_id`` defaults to a fixed value rather than None: it is required,
    and the per-test ``import_sample`` fixture means one sample never sees two
    logical imports unless a test sets up that case itself.
    """
    body = {
        "engine": engine,
        "engine_version": "1.4.0",
        "tier_bands": TIER_BANDS,
        "calibration": CALIBRATION,
        "config": {"passes": 3},
        "rows": rows,
        "chunk": {
            "run_id": run_id,
            "index": index,
            "complete": complete,
            "import_id": import_id,
        },
    }
    body.update(overrides)
    return body


def _import_url(sample_item_id: str) -> str:
    return f"/api/peak-assignments/sample/{sample_item_id}/runs/import"


async def _post(client, sample_item_id, body):
    return await client.post(_import_url(sample_item_id), json=body)


async def _run_of(session, run_id) -> PeakAssignmentRun:
    return await session.get(PeakAssignmentRun, run_id)


async def _rows_of(session, run_id):
    return (
        (
            await session.execute(
                select(PeakAssignment).where(
                    PeakAssignment.peak_assignment_run_id == run_id
                )
            )
        )
        .scalars()
        .all()
    )


class TestGating:
    """An import is a write, so it is gated exactly like the other writes."""

    @pytest.mark.asyncio
    async def test_import_rejected_when_feature_disabled(
        self, editor_client, import_sample, feature_disabled
    ):
        """An opted-out deployment cannot accumulate imported ledgers either."""
        response = await _post(editor_client, import_sample, _body([_row("peak-0")]))

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_abandon_rejected_when_feature_disabled(
        self, editor_client, import_sample, feature_disabled
    ):
        response = await editor_client.delete(
            f"/api/peak-assignments/sample/{import_sample}/runs/some-run"
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_a_guest_cannot_import(
        self, guest_client, import_sample, feature_enabled
    ):
        """Editor role: an import must not reach a sample the caller cannot edit."""
        response = await _post(guest_client, import_sample, _body([_row("peak-0")]))

        assert response.status_code == 403


class TestSingleRequestImport:
    """The common case: a slim ledger arriving in one complete request."""

    @pytest.mark.asyncio
    async def test_a_complete_import_lands_and_reads_back(
        self, editor_client, import_sample, feature_enabled, async_session_factory
    ):
        response = await _post(
            editor_client,
            import_sample,
            _body([_row("peak-0"), _row("peak-1", tier="candidate", fit_score=0.6)]),
        )

        assert response.status_code == 200
        record = response.json()["data"][0]
        assert record["run_status"] == "completed"
        assert record["rows"] == 2

        async with async_session_factory() as session:
            run = await _run_of(session, record["peak_assignment_run_id"])
            assert run.status == "completed"
            assert run.engine == "peaky"
            assert run.engine_version == "1.4.0"
            # Stored verbatim: the server never writes into an engine's config.
            assert run.config == {"passes": 3}
            assert run.calibration == CALIBRATION
            assert run.tier_bands == TIER_BANDS
            assert run.peak_assignment_run_utc_completed is not None
            assert len(await _rows_of(session, run.peak_assignment_run_id)) == 2

    @pytest.mark.asyncio
    async def test_the_imported_run_serves_the_ledger_read(
        self, editor_client, import_sample, feature_enabled
    ):
        """An import is first-class: it becomes the sample's default ledger."""
        await _post(editor_client, import_sample, _body([_row("peak-0")]))

        response = await editor_client.get(
            f"/api/peak-assignments/sample/{import_sample}"
        )

        assert response.status_code == 200
        assert response.json()["total"] == 1
        assert response.json()["data"][0]["assigned_formula"] == "C6H12O6"

    @pytest.mark.asyncio
    async def test_the_fold_in_runs_on_completion(
        self, editor_client, import_sample, feature_enabled, stub_fold_in
    ):
        """Imported runs reach the batch overview the moment they land."""
        await _post(editor_client, import_sample, _body([_row("peak-0")]))

        assert stub_fold_in == [import_sample]

    @pytest.mark.asyncio
    async def test_a_fold_in_failure_does_not_un_complete_the_import(
        self, editor_client, import_sample, feature_enabled, monkeypatch
    ):
        """Failure isolation mirrors the in-app path: best-effort, never fatal."""
        from mascope_backend.api.new.peak_assignments import batch_peaks_controller

        async def _boom(_sample_item_id):
            raise RuntimeError("batch peaks unavailable")

        monkeypatch.setattr(
            batch_peaks_controller, "fold_sample_into_batch_peaks", _boom
        )

        response = await _post(editor_client, import_sample, _body([_row("peak-0")]))

        assert response.status_code == 200
        assert response.json()["data"][0]["run_status"] == "completed"


class TestChunkedAssembly:
    """Assembly across requests, and its behaviour under the SDK's retries."""

    @pytest.mark.asyncio
    async def test_create_append_finalize(
        self, editor_client, import_sample, feature_enabled, async_session_factory
    ):
        first = await _post(
            editor_client,
            import_sample,
            _body([_row("peak-0"), _row("peak-1")], complete=False),
        )
        assert first.status_code == 200
        run_id = first.json()["data"][0]["peak_assignment_run_id"]
        assert first.json()["data"][0]["run_status"] == "importing"
        assert first.json()["data"][0]["rows"] == 2

        second = await _post(
            editor_client,
            import_sample,
            _body([_row("peak-2")], run_id=run_id, index=2, complete=False),
        )
        assert second.json()["data"][0]["rows"] == 3

        final = await _post(
            editor_client,
            import_sample,
            _body([_row("peak-3")], run_id=run_id, index=3, complete=True),
        )
        assert final.json()["data"][0]["run_status"] == "completed"
        assert final.json()["data"][0]["rows"] == 4

        async with async_session_factory() as session:
            run = await _run_of(session, run_id)
            assert run.status == "completed"
            assert len(await _rows_of(session, run_id)) == 4

    @pytest.mark.asyncio
    async def test_a_run_mid_assembly_is_not_served_as_the_ledger(
        self, editor_client, import_sample, feature_enabled
    ):
        """The default read resolves to completed runs only."""
        await _post(
            editor_client, import_sample, _body([_row("peak-0")], complete=False)
        )

        response = await editor_client.get(
            f"/api/peak-assignments/sample/{import_sample}"
        )

        assert response.status_code == 200
        assert response.json()["total"] == 0

    @pytest.mark.asyncio
    async def test_a_replayed_chunk_is_a_no_op_not_a_duplicate(
        self, editor_client, import_sample, feature_enabled, async_session_factory
    ):
        """The SDK retries POSTs on timeouts and cannot be told not to.

        Without offset checking the retry lands the same rows twice, straight
        onto the unique constraint on (run, peak), failing an otherwise healthy
        import.
        """
        first = await _post(
            editor_client,
            import_sample,
            _body([_row("peak-0"), _row("peak-1")], complete=False),
        )
        run_id = first.json()["data"][0]["peak_assignment_run_id"]

        chunk = _body([_row("peak-2")], run_id=run_id, index=2, complete=False)
        applied = await _post(editor_client, import_sample, chunk)
        assert applied.json()["data"][0]["rows"] == 3

        replay = await _post(editor_client, import_sample, chunk)

        assert replay.status_code == 200
        assert replay.json()["data"][0]["rows"] == 3
        async with async_session_factory() as session:
            rows = await _rows_of(session, run_id)
            assert len(rows) == 3
            assert len({row.sample_peak_id for row in rows}) == 3

    @pytest.mark.asyncio
    async def test_a_replayed_finalize_returns_the_completed_run(
        self, editor_client, import_sample, feature_enabled
    ):
        """A lost response on the last chunk must not fail a finished import."""
        first = await _post(
            editor_client,
            import_sample,
            _body([_row("peak-0")], complete=False),
        )
        run_id = first.json()["data"][0]["peak_assignment_run_id"]

        final = _body([_row("peak-1")], run_id=run_id, index=1, complete=True)
        assert (await _post(editor_client, import_sample, final)).status_code == 200

        replay = await _post(editor_client, import_sample, final)

        assert replay.status_code == 200
        assert replay.json()["data"][0]["run_status"] == "completed"
        assert replay.json()["data"][0]["rows"] == 2

    @pytest.mark.asyncio
    async def test_a_replayed_empty_finalize_returns_the_completed_run(
        self, editor_client, import_sample, feature_enabled, stub_fold_in
    ):
        """Finalizing is the slowest request, so it is the likeliest retried.

        The shape here is the one a client that appended every row separately
        sends: a final request carrying no rows at all.
        """
        first = await _post(
            editor_client, import_sample, _body([_row("peak-0")], complete=False)
        )
        run_id = first.json()["data"][0]["peak_assignment_run_id"]

        final = _body([], run_id=run_id, index=1, complete=True)
        assert (await _post(editor_client, import_sample, final)).status_code == 200
        assert stub_fold_in == [import_sample]

        replay = await _post(editor_client, import_sample, final)

        assert replay.status_code == 200
        assert replay.json()["data"][0]["run_status"] == "completed"
        assert replay.json()["data"][0]["rows"] == 1
        # The fold-in must not run a second time.
        assert stub_fold_in == [import_sample]

    @pytest.mark.asyncio
    async def test_the_response_reports_the_per_request_row_cap(
        self, editor_client, import_sample, feature_enabled
    ):
        """So a chunker sizes itself from the server instead of guessing."""
        from mascope_backend.api.new.peak_assignments.config import (
            MAX_IMPORT_ROWS_PER_REQUEST,
        )

        response = await _post(
            editor_client, import_sample, _body([_row("peak-0")], complete=False)
        )

        assert (
            response.json()["data"][0]["max_rows_per_request"]
            == MAX_IMPORT_ROWS_PER_REQUEST
        )

    @pytest.mark.asyncio
    async def test_an_oversized_body_is_refused_with_413(
        self, editor_client, import_sample, feature_enabled
    ):
        """Rows bound the count, not the bytes: provenance is unbounded JSON.

        A deployed stack stops this at the proxy; this is the same limit stated
        where a client talking to the backend directly can act on it.
        """
        from mascope_backend.api.new.peak_assignments.config import (
            MAX_IMPORT_BODY_BYTES,
        )

        bloated = _row("peak-0", provenance={"junk": "x" * MAX_IMPORT_BODY_BYTES})

        response = await _post(editor_client, import_sample, _body([bloated]))

        assert response.status_code == 413

    @pytest.mark.asyncio
    async def test_a_gap_is_refused_and_reports_the_row_count(
        self, editor_client, import_sample, feature_enabled
    ):
        """The client resynchronises from the count rather than leaving a hole."""
        first = await _post(
            editor_client, import_sample, _body([_row("peak-0")], complete=False)
        )
        run_id = first.json()["data"][0]["peak_assignment_run_id"]

        response = await _post(
            editor_client,
            import_sample,
            _body([_row("peak-2")], run_id=run_id, index=4, complete=False),
        )

        assert response.status_code == 409

    @pytest.mark.asyncio
    async def test_a_completed_run_takes_no_more_rows(
        self, editor_client, import_sample, feature_enabled
    ):
        first = await _post(editor_client, import_sample, _body([_row("peak-0")]))
        run_id = first.json()["data"][0]["peak_assignment_run_id"]

        response = await _post(
            editor_client,
            import_sample,
            _body([_row("peak-1")], run_id=run_id, index=1, complete=False),
        )

        assert response.status_code == 409

    @pytest.mark.asyncio
    async def test_an_unknown_run_id_is_not_found(
        self, editor_client, import_sample, feature_enabled
    ):
        response = await _post(
            editor_client,
            import_sample,
            _body([_row("peak-0")], run_id="no-such-run", index=0),
        )

        assert response.status_code == 404


class TestCreateIdempotency:
    """The one request an offset cannot make idempotent.

    A create carries no run id, so a retry of it is indistinguishable from a
    second import: it would mint a second run, which durable admission then
    refuses - leaving the client wedged, since it never learned the first run's
    id. The client's own import id is what closes that.
    """

    @pytest.mark.asyncio
    async def test_a_retried_create_returns_the_same_run(
        self, editor_client, import_sample, feature_enabled, async_session_factory
    ):
        """Byte-identical retry, as the SDK's own retry loop would send it."""
        body = _body(
            [_row("peak-0"), _row("peak-1")],
            complete=False,
            import_id="import-abc",
        )

        first = await _post(editor_client, import_sample, body)
        replay = await _post(editor_client, import_sample, body)

        assert first.status_code == 200
        assert replay.status_code == 200
        run_id = first.json()["data"][0]["peak_assignment_run_id"]
        assert replay.json()["data"][0]["peak_assignment_run_id"] == run_id
        assert replay.json()["data"][0]["rows"] == 2

        async with async_session_factory() as session:
            runs = (
                (
                    await session.execute(
                        select(PeakAssignmentRun).where(
                            PeakAssignmentRun.sample_item_id == import_sample
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(runs) == 1
        assert runs[0].import_key == "import-abc"

    @pytest.mark.asyncio
    async def test_a_retried_single_request_import_does_not_import_twice(
        self,
        editor_client,
        import_sample,
        feature_enabled,
        stub_fold_in,
        async_session_factory,
    ):
        """The slim-ledger case: create, rows and finalize in one request.

        The one shape admission cannot backstop. Both requests finish as
        'completed', so `in_flight_run_id` has nothing to refuse the retry on -
        only the key stops a second run and a second fold-in, and neither would
        have raised anything for a client to notice.
        """
        body = _body([_row("peak-0")], import_id="import-xyz")

        first = await _post(editor_client, import_sample, body)
        replay = await _post(editor_client, import_sample, body)

        assert first.status_code == 200
        assert replay.status_code == 200
        assert (
            replay.json()["data"][0]["peak_assignment_run_id"]
            == first.json()["data"][0]["peak_assignment_run_id"]
        )
        assert replay.json()["data"][0]["run_status"] == "completed"
        assert replay.json()["data"][0]["rows"] == 1
        assert stub_fold_in == [import_sample]
        async with async_session_factory() as session:
            runs = (
                (
                    await session.execute(
                        select(PeakAssignmentRun).where(
                            PeakAssignmentRun.sample_item_id == import_sample
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(runs) == 1

    @pytest.mark.asyncio
    async def test_an_import_without_a_key_is_refused_by_the_schema(
        self, editor_client, import_sample, feature_enabled, async_session_factory
    ):
        """The key is required, because the damage without one can be silent.

        A chunked create that retried without one left a second *non-terminal*
        run, which admission refused - bad, but loud. A single-request create
        finishes as 'completed', which admission does not refuse, so the retry
        landed a duplicate ledger and a second fold-in with no error anywhere.
        Rather than fix the loud half and leave the quiet one, the schema
        requires the key.
        """
        body = _body([_row("peak-0")])
        body["chunk"].pop("import_id")

        response = await _post(editor_client, import_sample, body)

        assert response.status_code == 422
        async with async_session_factory() as session:
            runs = (
                (
                    await session.execute(
                        select(PeakAssignmentRun).where(
                            PeakAssignmentRun.sample_item_id == import_sample
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert runs == []

    @pytest.mark.asyncio
    async def test_a_follow_up_chunk_naming_another_import_is_refused(
        self, editor_client, import_sample, feature_enabled
    ):
        """The two ids a follow-up carries address one run; they must agree."""
        first = await _post(
            editor_client,
            import_sample,
            _body([_row("peak-0")], complete=False, import_id="import-1"),
        )
        run_id = first.json()["data"][0]["peak_assignment_run_id"]

        response = await _post(
            editor_client,
            import_sample,
            _body(
                [_row("peak-1")],
                run_id=run_id,
                index=1,
                complete=False,
                import_id="import-2",
            ),
        )

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_a_different_key_on_a_busy_sample_is_still_refused(
        self, editor_client, import_sample, feature_enabled
    ):
        """The key deduplicates a retry; it does not bypass admission."""
        await _post(
            editor_client,
            import_sample,
            _body([_row("peak-0")], complete=False, import_id="import-1"),
        )

        response = await _post(
            editor_client,
            import_sample,
            _body([_row("peak-1")], complete=False, import_id="import-2"),
        )

        assert response.status_code == 409


class TestOwnerResolution:
    """An iso_child references its owner by peak, and the server links it."""

    @pytest.mark.asyncio
    async def test_a_reference_resolves_to_the_minted_owner_id(
        self, editor_client, import_sample, feature_enabled, async_session_factory
    ):
        response = await _post(
            editor_client,
            import_sample,
            _body(
                [
                    _row("peak-0"),
                    _row(
                        "peak-1",
                        role="iso_child",
                        fit_score=0.85,
                        owner_sample_peak_id="peak-0",
                    ),
                ]
            ),
        )
        run_id = response.json()["data"][0]["peak_assignment_run_id"]

        async with async_session_factory() as session:
            rows = {row.sample_peak_id: row for row in await _rows_of(session, run_id)}

        owner = rows["peak-0"]
        child = rows["peak-1"]
        assert child.owner_peak_assignment_id == owner.peak_assignment_id
        assert owner.owner_peak_assignment_id is None

    @pytest.mark.asyncio
    async def test_a_reference_resolves_across_chunks(
        self, editor_client, import_sample, feature_enabled, async_session_factory
    ):
        """A child may arrive before the owner it names: resolution is at the end."""
        first = await _post(
            editor_client,
            import_sample,
            _body(
                [
                    _row(
                        "peak-1",
                        role="iso_child",
                        fit_score=0.85,
                        owner_sample_peak_id="peak-0",
                    )
                ],
                complete=False,
            ),
        )
        run_id = first.json()["data"][0]["peak_assignment_run_id"]

        await _post(
            editor_client,
            import_sample,
            _body([_row("peak-0")], run_id=run_id, index=1, complete=True),
        )

        async with async_session_factory() as session:
            rows = {row.sample_peak_id: row for row in await _rows_of(session, run_id)}

        assert (
            rows["peak-1"].owner_peak_assignment_id == rows["peak-0"].peak_assignment_id
        )

    @pytest.mark.asyncio
    async def test_an_unresolvable_reference_is_refused(
        self, editor_client, import_sample, feature_enabled, async_session_factory
    ):
        response = await _post(
            editor_client,
            import_sample,
            _body(
                [
                    _row(
                        "peak-1",
                        role="iso_child",
                        fit_score=0.85,
                        owner_sample_peak_id="peak-not-in-import",
                    )
                ]
            ),
        )

        assert response.status_code == 422


class TestValidation:
    """The payload rules that need the database or the peak set to reach."""

    @pytest.mark.asyncio
    async def test_the_in_app_engine_cannot_be_claimed(
        self, editor_client, import_sample, feature_enabled
    ):
        """Otherwise the provenance badge the trust model rests on is forgeable."""
        response = await _post(
            editor_client, import_sample, _body([_row("peak-0")], engine="mascope")
        )

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_a_peak_the_sample_does_not_have_is_refused(
        self, editor_client, import_sample, feature_enabled
    ):
        response = await _post(editor_client, import_sample, _body([_row("ghost")]))

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_a_duplicate_peak_within_a_chunk_is_refused(
        self, editor_client, import_sample, feature_enabled
    ):
        response = await _post(
            editor_client, import_sample, _body([_row("peak-0"), _row("peak-0")])
        )

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_a_duplicate_peak_across_chunks_is_refused(
        self, editor_client, import_sample, feature_enabled
    ):
        """Caught by the unique constraint, reported as the same refusal."""
        first = await _post(
            editor_client, import_sample, _body([_row("peak-0")], complete=False)
        )
        run_id = first.json()["data"][0]["peak_assignment_run_id"]

        response = await _post(
            editor_client,
            import_sample,
            _body([_row("peak-0")], run_id=run_id, index=1, complete=False),
        )

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_an_inflated_tier_is_refused(
        self, editor_client, import_sample, feature_enabled
    ):
        """0.62 is not 'assigned' under the bands this run declared."""
        response = await _post(
            editor_client,
            import_sample,
            _body([_row("peak-0", tier="assigned", fit_score=0.62)]),
        )

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_the_declared_bands_are_the_ones_applied(
        self, editor_client, import_sample, feature_enabled
    ):
        """The same row is coherent under bands that say so."""
        response = await _post(
            editor_client,
            import_sample,
            _body(
                [_row("peak-0", tier="assigned", fit_score=0.62)],
                tier_bands={"assigned": 0.6, "candidate": 0.3},
            ),
        )

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_tier_bands_are_required(
        self, editor_client, import_sample, feature_enabled
    ):
        response = await _post(
            editor_client, import_sample, _body([_row("peak-0")], tier_bands=None)
        )

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_calibration_is_required(
        self, editor_client, import_sample, feature_enabled
    ):
        """An import bypasses the m/z verification gate; disclosure replaces it."""
        response = await _post(
            editor_client, import_sample, _body([_row("peak-0")], calibration=None)
        )

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_an_empty_import_is_refused(
        self, editor_client, import_sample, feature_enabled
    ):
        """Completeness is not required, but emptiness is not a ledger."""
        response = await _post(editor_client, import_sample, _body([]))

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_an_empty_import_leaves_no_run_behind(
        self, editor_client, import_sample, feature_enabled, async_session_factory
    ):
        """A refused payload must not block the sample with a stuck run."""
        await _post(editor_client, import_sample, _body([]))

        async with async_session_factory() as session:
            runs = (
                (
                    await session.execute(
                        select(PeakAssignmentRun).where(
                            PeakAssignmentRun.sample_item_id == import_sample
                        )
                    )
                )
                .scalars()
                .all()
            )

        assert runs == []

    @pytest.mark.asyncio
    async def test_more_rows_than_the_sample_has_peaks_is_refused(
        self, editor_client, import_sample, feature_enabled
    ):
        """The total cap: a run holds at most one row per peak."""
        response = await _post(
            editor_client,
            import_sample,
            _body([_row(f"peak-{index}") for index in range(6)] + [_row("peak-0")]),
        )

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_an_unknown_ionization_mechanism_is_refused(
        self, editor_client, import_sample, feature_enabled
    ):
        """A 422 naming it, not a 500 out of the bulk insert after the upload."""
        response = await _post(
            editor_client,
            import_sample,
            _body([_row("peak-0", ionization_mechanism_id="no-such-mech")]),
        )

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_a_polarity_mismatched_mechanism_is_refused(
        self, editor_client, import_sample, feature_enabled, async_session_factory
    ):
        """The sample is positive; a negative-mode adduct cannot apply to it."""
        mechanism_id = gen_id()
        async with async_session_factory() as session:
            session.add(
                IonizationMechanism(
                    ionization_mechanism_id=mechanism_id,
                    ionization_mechanism_polarity="-",
                    ionization_mechanism=f"import-test-negative-{mechanism_id}",
                )
            )
            await session.commit()
        try:
            response = await _post(
                editor_client,
                import_sample,
                _body([_row("peak-0", ionization_mechanism_id=mechanism_id)]),
            )

            assert response.status_code == 422
        finally:
            async with async_session_factory() as session:
                await session.execute(
                    delete(IonizationMechanism).where(
                        IonizationMechanism.ionization_mechanism_id == mechanism_id
                    )
                )
                await session.commit()

    @pytest.mark.parametrize(
        "field", ["target_compound_id", "target_ion_id", "ionization_mechanism_id"]
    )
    @pytest.mark.asyncio
    async def test_an_unknown_reference_id_is_refused_by_name(
        self, editor_client, import_sample, feature_enabled, field
    ):
        """Every id a row carries into a foreign key, not just the mechanism.

        The insert catches all three as one indistinguishable IntegrityError,
        which used to be reported as a duplicate-peak error - sending a client
        hunting for a duplicate that was never there. Checked up front, each is
        a 422 naming the field and the value.
        """
        response = await _post(
            editor_client,
            import_sample,
            _body([_row("peak-0", **{field: "no-such-id"})]),
        )

        assert response.status_code == 422

        # The endpoint genericizes a 422 body to an error id, so the status
        # code alone cannot tell this apart from the bug it closes - which also
        # returned 422, just describing a duplicate peak that was never there.
        # The message is pinned against the validator directly instead.
        sample = await fetch_sample(import_sample)
        attributes = dict.fromkeys(
            ("ionization_mechanism_id", "target_compound_id", "target_ion_id")
        )
        attributes[field] = "no-such-id"
        with pytest.raises(import_service.UnprocessableImportException) as raised:
            await import_service._validate_references(
                [SimpleNamespace(**attributes)], sample
            )
        assert field in raised.value.detail
        assert "no-such-id" in raised.value.detail

    @pytest.mark.asyncio
    async def test_a_database_refusal_on_create_rolls_the_run_back(
        self,
        editor_client,
        import_sample,
        feature_enabled,
        async_session_factory,
        monkeypatch,
    ):
        """The run and the first chunk's rows share one transaction.

        The sibling test above provokes a refusal the payload rules catch
        before the run is built, so it never reaches that transaction. This
        one refuses at the insert - where a genuine race or a constraint no
        payload rule models yet would land - and pins what the shared
        transaction is for: the run must not survive the rows it was created
        with.
        """

        class _Refusal(Exception):
            sqlstate = "23505"

            def __str__(self):
                return "duplicate key value violates unique constraint"

        real_insert = import_service.insert

        def _refusing_insert(table):
            statement = real_insert(table)
            if table is PeakAssignment:
                raise DBAPIError("INSERT", {}, _Refusal())
            return statement

        monkeypatch.setattr(import_service, "insert", _refusing_insert)

        refused = await _post(editor_client, import_sample, _body([_row("peak-0")]))
        assert refused.status_code == 422

        async with async_session_factory() as session:
            runs = (
                (
                    await session.execute(
                        select(PeakAssignmentRun).where(
                            PeakAssignmentRun.sample_item_id == import_sample
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert runs == [], "the refused insert left its run behind"

        # The sample is free again: a fresh import is admitted rather than
        # refused by an 'importing' run nothing will ever finish. Restore only
        # the insert - undoing every patch would take the feature-flag override
        # with it and the next request would be refused by the gate instead.
        monkeypatch.setattr(import_service, "insert", real_insert)
        accepted = await _post(editor_client, import_sample, _body([_row("peak-0")]))
        assert accepted.status_code == 200

    @pytest.mark.asyncio
    async def test_a_refused_create_leaves_no_run_holding_the_sample(
        self, editor_client, import_sample, feature_enabled, async_session_factory
    ):
        """A refusal on the create request must not strand an 'importing' run.

        The run and the first chunk's rows share one transaction precisely so
        this holds for refusals the *database* raises too, not only the ones
        the payload rules catch before the run is built.
        """
        refused = await _post(
            editor_client,
            import_sample,
            _body([_row("peak-0", target_ion_id="no-such-ion")]),
        )
        assert refused.status_code == 422

        async with async_session_factory() as session:
            runs = (
                (
                    await session.execute(
                        select(PeakAssignmentRun).where(
                            PeakAssignmentRun.sample_item_id == import_sample
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert runs == []

        # And the sample is still free, which is what a stranded run would cost.
        accepted = await _post(
            editor_client,
            import_sample,
            _body([_row("peak-0")], import_id="import-after-refusal"),
        )
        assert accepted.status_code == 200

    @pytest.mark.parametrize(
        "field,width", [("assigned_formula", 256), ("isotope_label", 64)]
    )
    @pytest.mark.asyncio
    async def test_a_value_too_long_for_its_column_is_a_422_not_a_500(
        self, editor_client, import_sample, feature_enabled, field, width
    ):
        """Bounded in the schema, so it never reaches the column as a DataError.

        A DataError is not an IntegrityError, so nothing in the insert path
        caught it and the client saw a 500 where every other payload rule gives
        a 422.
        """
        response = await _post(
            editor_client,
            import_sample,
            _body([_row("peak-0", **{field: "C" * (width + 1)})]),
        )

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_a_non_finite_number_is_refused_before_it_poisons_the_read(
        self, editor_client, import_sample, feature_enabled
    ):
        """`1e999` is RFC-valid JSON and `json.loads` turns it into `inf`.

        A double precision column takes it, so the import used to succeed - and
        the damage landed on the ledger *read*, which renders with
        `allow_nan=False` and so failed for the whole run. That run is
        'completed', which puts it beyond the abandon endpoint, so the sample's
        ledger stayed broken until retention. Sent as raw content rather than
        through the json= encoder, since this is the shape that survives a
        strict client-side encoder.
        """
        body = _body([_row("peak-0")])
        raw = json.dumps(body).replace(
            str(body["rows"][0]["sample_peak_mz"]), "1e999", 1
        )
        assert "1e999" in raw

        response = await editor_client.post(
            _import_url(import_sample),
            content=raw,
            headers={"content-type": "application/json"},
        )

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_an_engine_name_too_long_for_its_column_is_refused(
        self, editor_client, import_sample, feature_enabled
    ):
        """`engine` is String(64) on the run, and was the one unbounded field."""
        response = await _post(
            editor_client, import_sample, _body([_row("peak-0")], engine="e" * 65)
        )

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_the_in_app_engines_own_tiering_is_accepted(
        self, editor_client, import_sample, feature_enabled
    ):
        """An engine reproducing Mascope's tiering must not be refused for it.

        `tier_for_evidence` maps a None to 'below_assignability', and the
        in-app ledger writes exactly that pair. Restating the thresholds here
        instead of delegating to it made that shape a 422.
        """
        response = await _post(
            editor_client,
            import_sample,
            _body(
                [
                    _row("peak-0", tier="below_assignability", fit_score=None),
                    _row(
                        "peak-1", tier="unassigned", fit_score=None, role="unassigned"
                    ),
                ]
            ),
        )

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_an_oversized_config_is_refused(
        self, editor_client, import_sample, feature_enabled
    ):
        """The run listing re-serves it in full on a hot path."""
        response = await _post(
            editor_client,
            import_sample,
            _body([_row("peak-0")], config={"junk": "x" * 200_000}),
        )

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_a_blank_sample_is_refused(
        self, editor_client, pa_test_data, feature_enabled, async_session_factory
    ):
        """A blank carries no measured peaks, so there is nothing to assign.

        Stated rather than inherited from peak validation, which would not
        refuse a payload that named no peaks at all.
        """
        now = datetime.now(timezone.utc)
        sample_file_id = gen_id()
        sample_item_id = gen_id()
        async with async_session_factory() as session:
            session.add(
                SampleFile(
                    sample_file_id=sample_file_id,
                    filename=f"blank-test-{sample_file_id}.zarr",
                    instrument="orbi-test",
                    datetime=datetime(2026, 8, 19, 12, 0, 0),
                    datetime_utc=now,
                    length=60.0,
                    range=[50.0, 500.0],
                    polarity="+",
                )
            )
            session.add(
                SampleItem(
                    sample_item_id=sample_item_id,
                    sample_batch_id=pa_test_data["sample_batch_id"],
                    sample_file_id=sample_file_id,
                    sample_item_name="Blank Import Test Sample",
                    sample_item_type="blank",
                    polarity="+",
                    sample_item_utc_created=now,
                )
            )
            await session.commit()
        try:
            response = await _post(
                editor_client, sample_item_id, _body([_row("peak-0")])
            )

            assert response.status_code == 422
        finally:
            async with async_session_factory() as session:
                await session.execute(
                    delete(SampleItem).where(
                        SampleItem.sample_item_id == sample_item_id
                    )
                )
                await session.execute(
                    delete(SampleFile).where(
                        SampleFile.sample_file_id == sample_file_id
                    )
                )
                await session.commit()


class TestAPreRenameLedgerStillImports:
    """The top tier used to be called 'identified', and payloads outlive a rename.

    An external engine publishes against the spec it was built for, and a ledger
    exported before the rename is re-imported exactly as it was written - so both
    places the vocabulary appears in a payload, a row's ``tier`` and the upper
    ``tier_bands`` key, still accept the old spelling. Only the current one is
    ever stored, so the ledger a reader gets back is in one vocabulary whichever
    one produced it.

    What the alias must not buy is a weaker tier: the coherence rule that keeps
    an engine's declared bands honest applies to a legacy payload exactly as it
    does to a current one.
    """

    #: A run's bands as an engine built against the older spec declares them.
    LEGACY_TIER_BANDS = {"identified": 0.8, "candidate": 0.5}

    @pytest.mark.asyncio
    async def test_a_legacy_row_tier_is_stored_under_the_current_name(
        self, editor_client, import_sample, feature_enabled, async_session_factory
    ):
        response = await _post(
            editor_client, import_sample, _body([_row("peak-0", tier="identified")])
        )

        assert response.status_code == 200
        async with async_session_factory() as session:
            rows = await _rows_of(
                session, response.json()["data"][0]["peak_assignment_run_id"]
            )
        assert [row.tier for row in rows] == ["assigned"]

    @pytest.mark.asyncio
    async def test_legacy_bands_are_stored_and_disclosed_under_the_current_key(
        self, editor_client, import_sample, feature_enabled, async_session_factory
    ):
        """The bands are the yardstick a reader judges an imported tier by.

        Disclosing them under the spelling the engine happened to use would make
        two runs that tiered identically look like they did not.
        """
        response = await _post(
            editor_client,
            import_sample,
            _body([_row("peak-0")], tier_bands=self.LEGACY_TIER_BANDS),
        )
        assert response.status_code == 200

        async with async_session_factory() as session:
            run = await _run_of(
                session, response.json()["data"][0]["peak_assignment_run_id"]
            )
        assert run.tier_bands == TIER_BANDS

        listing = await editor_client.get(
            f"/api/peak-assignments/sample/{import_sample}/runs"
        )
        assert listing.json()["data"][0]["tier_bands"] == TIER_BANDS

    @pytest.mark.asyncio
    async def test_a_whole_pre_rename_payload_reads_back_in_the_current_vocabulary(
        self, editor_client, import_sample, feature_enabled
    ):
        """Legacy tier and legacy bands together - the re-imported export."""
        response = await _post(
            editor_client,
            import_sample,
            _body(
                [
                    _row("peak-0", tier="identified"),
                    _row("peak-1", tier="candidate", fit_score=0.6),
                ],
                tier_bands=self.LEGACY_TIER_BANDS,
            ),
        )
        assert response.status_code == 200

        ledger = await editor_client.get(
            f"/api/peak-assignments/sample/{import_sample}"
        )

        assert ledger.status_code == 200
        assert {row["tier"] for row in ledger.json()["data"]} == {
            "assigned",
            "candidate",
        }

    @pytest.mark.asyncio
    async def test_the_alias_is_not_a_way_past_the_coherence_rule(
        self, editor_client, import_sample, feature_enabled
    ):
        """0.62 is no more 'identified' than it is 'assigned' under 0.8/0.5.

        Tier and bands are both the old spelling here, so the check only holds
        if the two are normalized together. Normalizing one of them alone would
        let this row through - and an alias that admits a claim the current
        vocabulary refuses is a hole, not a compatibility.
        """
        response = await _post(
            editor_client,
            import_sample,
            _body(
                [_row("peak-0", tier="identified", fit_score=0.62)],
                tier_bands=self.LEGACY_TIER_BANDS,
            ),
        )

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_an_import_opened_before_the_rename_can_still_finish(
        self, editor_client, import_sample, feature_enabled, async_session_factory
    ):
        """Every chunk after the first reads the bands back off the run row.

        An import assembling when the rename shipped has the old key stored on
        it, and a client paces its own upload - so its remaining chunks arrive
        against a build that no longer writes that key. Refusing them would
        strand the run in 'importing', where it blocks the sample's later
        imports and in-app assigns until someone abandons it.
        """
        run_id = gen_id()
        async with async_session_factory() as session:
            session.add(
                PeakAssignmentRun(
                    peak_assignment_run_id=run_id,
                    sample_item_id=import_sample,
                    engine="peaky",
                    engine_version="1.4.0",
                    status="importing",
                    tier_bands=self.LEGACY_TIER_BANDS,
                    calibration=CALIBRATION,
                    import_key="import-mid-rename",
                    peak_assignment_run_utc_created=datetime.now(timezone.utc),
                )
            )
            await session.commit()

        response = await _post(
            editor_client,
            import_sample,
            _body([_row("peak-0")], run_id=run_id, import_id="import-mid-rename"),
        )

        assert response.status_code == 200
        assert response.json()["data"][0]["run_status"] == "completed"


class TestServerOwnedFieldsStayEmpty:
    """An importer may not populate the confidence this server presents as its own."""

    @pytest.mark.asyncio
    async def test_supplied_confidence_is_not_stored(
        self, editor_client, import_sample, feature_enabled, async_session_factory
    ):
        response = await _post(
            editor_client,
            import_sample,
            _body(
                [
                    _row(
                        "peak-0",
                        p_correct=0.999,
                        provenance={
                            "p_correct": 0.999,
                            "calibration": {"provisional": False},
                            "corroboration": {"n_adducts": 12},
                            "notes": "the importer's own detail",
                        },
                    )
                ]
            ),
        )
        run_id = response.json()["data"][0]["peak_assignment_run_id"]

        async with async_session_factory() as session:
            row = (await _rows_of(session, run_id))[0]

        # The importer's own detail survives; the three keys this server presents
        # as its calibrated judgement do not. `evidence` is neither: the server
        # writes its OWN, derived from the row's fit and the plausibility of the
        # formula it commits (0.92 x 1.0 for the fixture's C6H12O6), because the
        # ledger shows that number beside the tier it validated.
        assert row.provenance == {
            "notes": "the importer's own detail",
            "evidence": 0.92,
            "plausibility": 1.0,
        }

    @pytest.mark.asyncio
    async def test_a_supplied_evidence_is_replaced_by_the_servers_own(
        self, editor_client, import_sample, feature_enabled, async_session_factory
    ):
        """Evidence is derived, never asserted.

        It is not merely stripped like the calibrated keys: the ledger renders it
        beside the tier, so blanking it would leave every imported row showing a
        band with no number to explain it. Recomputing is safe precisely because
        plausibility is a pure function of the formula - there is nothing an
        importer could tell us that we would rather believe than compute.
        """
        response = await _post(
            editor_client,
            import_sample,
            _body([_row("peak-0", provenance={"evidence": 0.999})]),
        )
        run_id = response.json()["data"][0]["peak_assignment_run_id"]

        async with async_session_factory() as session:
            row = (await _rows_of(session, run_id))[0]

        assert row.provenance == {"evidence": 0.92, "plausibility": 1.0}

    @pytest.mark.asyncio
    async def test_the_ledger_renders_no_confidence_for_an_imported_row(
        self, editor_client, import_sample, feature_enabled
    ):
        """The columns the UI presents as this server's judgement stay empty."""
        await _post(
            editor_client,
            import_sample,
            _body(
                [
                    _row(
                        "peak-0",
                        provenance={
                            "p_correct": 0.999,
                            "calibration": {"provisional": False},
                            "corroboration": {"n_adducts": 12},
                        },
                    )
                ]
            ),
        )

        response = await editor_client.get(
            f"/api/peak-assignments/sample/{import_sample}"
        )
        record = response.json()["data"][0]

        assert record["p_correct"] is None
        assert record["p_correct_provisional"] is None
        assert record["corroboration_adducts"] is None


class TestDurableAdmission:
    """Two ledgers for one sample is what admission exists to prevent.

    The advisory claim cannot answer this on its own: it lives and dies with one
    process, and an import spans several requests at a client's pace.
    """

    @pytest.mark.asyncio
    async def test_an_in_app_run_in_flight_refuses_a_new_import(
        self, editor_client, import_sample, feature_enabled, async_session_factory
    ):
        """A run the engine is still computing holds the sample against imports."""
        run_id = gen_id()
        async with async_session_factory() as session:
            session.add(
                PeakAssignmentRun(
                    peak_assignment_run_id=run_id,
                    sample_item_id=import_sample,
                    engine="mascope",
                    engine_version="test",
                    status="running",
                    peak_assignment_run_utc_created=datetime.now(timezone.utc),
                )
            )
            await session.commit()

        response = await _post(editor_client, import_sample, _body([_row("peak-0")]))

        assert response.status_code == 409

    @pytest.mark.asyncio
    async def test_an_assembling_import_refuses_a_second_one(
        self, editor_client, import_sample, feature_enabled
    ):
        await _post(
            editor_client,
            import_sample,
            _body([_row("peak-0")], complete=False, import_id="import-1"),
        )

        # A different key, so this is a second import rather than a retry of
        # the first - which is what admission has to refuse.
        response = await _post(
            editor_client,
            import_sample,
            _body([_row("peak-1")], complete=False, import_id="import-2"),
        )

        assert response.status_code == 409

    @pytest.mark.asyncio
    async def test_an_assembling_import_refuses_an_in_app_assign(
        self, editor_client, import_sample, feature_enabled, async_session_factory
    ):
        """The two paths refuse each other, which is the point of the shared check."""
        from mascope_backend.api.new.peak_assignments.service import assign_sample_peaks

        first = await _post(
            editor_client, import_sample, _body([_row("peak-0")], complete=False)
        )
        run_id = first.json()["data"][0]["peak_assignment_run_id"]

        result = await assign_sample_peaks(sample_item_id=import_sample)

        assert result["status"] == "skipped"
        assert result["data"]["peak_assignment_run_id"] == run_id
        async with async_session_factory() as session:
            runs = (
                (
                    await session.execute(
                        select(PeakAssignmentRun).where(
                            PeakAssignmentRun.sample_item_id == import_sample
                        )
                    )
                )
                .scalars()
                .all()
            )
        # The refusal happened before a second run was created.
        assert len(runs) == 1

    @pytest.mark.asyncio
    async def test_a_completed_import_does_not_block_the_next_one(
        self, editor_client, import_sample, feature_enabled
    ):
        """Only non-terminal runs hold the sample."""
        await _post(
            editor_client, import_sample, _body([_row("peak-0")], import_id="import-1")
        )

        # A distinct key, or this would resolve to the first run and be
        # answered as a replay rather than admitted as a new import.
        response = await _post(
            editor_client, import_sample, _body([_row("peak-1")], import_id="import-2")
        )

        assert response.status_code == 200
        assert response.json()["data"][0]["run_status"] == "completed"


class TestAbandon:
    """The way out of an assembly whose client will never come back."""

    @pytest.mark.asyncio
    async def test_an_importing_run_is_deleted_with_its_rows(
        self, editor_client, import_sample, feature_enabled, async_session_factory
    ):
        first = await _post(
            editor_client,
            import_sample,
            _body([_row("peak-0"), _row("peak-1")], complete=False),
        )
        run_id = first.json()["data"][0]["peak_assignment_run_id"]

        response = await editor_client.delete(
            f"/api/peak-assignments/sample/{import_sample}/runs/{run_id}"
        )

        assert response.status_code == 200
        assert response.json()["data"][0]["rows"] == 2
        async with async_session_factory() as session:
            assert await _run_of(session, run_id) is None
            assert await _rows_of(session, run_id) == []

    @pytest.mark.asyncio
    async def test_abandoning_releases_the_sample(
        self, editor_client, import_sample, feature_enabled
    ):
        """Which is the whole reason it exists: the run was blocking the sample."""
        first = await _post(
            editor_client,
            import_sample,
            _body([_row("peak-0")], complete=False, import_id="import-abandoned"),
        )
        run_id = first.json()["data"][0]["peak_assignment_run_id"]
        await editor_client.delete(
            f"/api/peak-assignments/sample/{import_sample}/runs/{run_id}"
        )

        response = await _post(
            editor_client,
            import_sample,
            _body([_row("peak-0")], import_id="import-retry"),
        )

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_a_completed_run_cannot_be_abandoned(
        self, editor_client, import_sample, feature_enabled, async_session_factory
    ):
        """A completed run is ledger data; removing it is retention's business."""
        first = await _post(editor_client, import_sample, _body([_row("peak-0")]))
        run_id = first.json()["data"][0]["peak_assignment_run_id"]

        response = await editor_client.delete(
            f"/api/peak-assignments/sample/{import_sample}/runs/{run_id}"
        )

        assert response.status_code == 409
        async with async_session_factory() as session:
            assert await _run_of(session, run_id) is not None

    @pytest.mark.asyncio
    async def test_an_in_app_run_cannot_be_abandoned(
        self, editor_client, pa_test_data, feature_enabled
    ):
        """Restricted to imports: this is not a delete-any-run endpoint."""
        response = await editor_client.delete(
            f"/api/peak-assignments/sample/{pa_test_data['sample_item_id']}"
            f"/runs/{pa_test_data['running_run_id']}"
        )

        assert response.status_code == 409

    @pytest.mark.asyncio
    async def test_a_guest_cannot_abandon(
        self, guest_client, import_sample, editor_client, feature_enabled
    ):
        first = await _post(
            editor_client, import_sample, _body([_row("peak-0")], complete=False)
        )
        run_id = first.json()["data"][0]["peak_assignment_run_id"]

        response = await guest_client.delete(
            f"/api/peak-assignments/sample/{import_sample}/runs/{run_id}"
        )

        assert response.status_code == 403


class TestRetentionIsPerEngine:
    """Publishing must not evict the runs this server computed.

    "An import only ever appends" is what a reader takes from the trust model,
    and retention is where that stops being true on its own: on a shared
    per-sample budget, republishing an import a few times would age out every
    in-app run for that sample, its ledger cascading with it. Proved here
    against real Postgres, where the grouping and ordering actually run.
    """

    @pytest_asyncio.fixture
    async def runs_of_both_engines(self, async_session_factory, import_sample):
        """Four completed runs for one sample: two per engine, newest first."""
        now = datetime.now(timezone.utc)
        ids = {}
        async with async_session_factory() as session:
            for engine in ("mascope", "peaky"):
                for age_hours, label in ((1, "new"), (5, "old")):
                    run_id = gen_id()
                    ids[f"{engine}-{label}"] = run_id
                    session.add(
                        PeakAssignmentRun(
                            peak_assignment_run_id=run_id,
                            sample_item_id=import_sample,
                            engine=engine,
                            engine_version="test",
                            status="completed",
                            peak_assignment_run_utc_created=now
                            - timedelta(hours=age_hours),
                            peak_assignment_run_utc_completed=now
                            - timedelta(hours=age_hours),
                        )
                    )
            await session.commit()
        return ids

    @pytest.mark.asyncio
    async def test_each_engine_keeps_its_own_newest_run(
        self, runs_of_both_engines, async_session_factory, import_sample
    ):
        from mascope_backend.db.admin.peak_assignments.prune_runs import (
            prune_peak_assignment_runs,
        )

        await prune_peak_assignment_runs(keep_per_sample=1)

        async with async_session_factory() as session:
            surviving = {
                run.peak_assignment_run_id
                for run in (
                    await session.execute(
                        select(PeakAssignmentRun).where(
                            PeakAssignmentRun.sample_item_id == import_sample
                        )
                    )
                )
                .scalars()
                .all()
            }

        assert runs_of_both_engines["mascope-new"] in surviving
        assert runs_of_both_engines["peaky-new"] in surviving
        assert runs_of_both_engines["mascope-old"] not in surviving
        assert runs_of_both_engines["peaky-old"] not in surviving

    @pytest.mark.asyncio
    async def test_imports_within_budget_evict_no_in_app_run(
        self, runs_of_both_engines, async_session_factory, import_sample
    ):
        """Two imports plus two in-app runs, budget two: nothing is prunable."""
        from mascope_backend.db.admin.peak_assignments.prune_runs import (
            prune_peak_assignment_runs,
        )

        await prune_peak_assignment_runs(keep_per_sample=2)

        async with async_session_factory() as session:
            surviving = {
                run.peak_assignment_run_id
                for run in (
                    await session.execute(
                        select(PeakAssignmentRun).where(
                            PeakAssignmentRun.sample_item_id == import_sample
                        )
                    )
                )
                .scalars()
                .all()
            }

        assert surviving == set(runs_of_both_engines.values())


class TestInAppRunsAreStampedWithTheEngine:
    """Attribution has to hold on both sides or the badge means nothing."""

    @pytest.mark.asyncio
    async def test_an_existing_run_reads_back_as_the_in_app_engine(
        self, async_session_factory, pa_test_data
    ):
        """Runs predating the column were backfilled to the in-app identity."""
        async with async_session_factory() as session:
            run = await session.get(PeakAssignmentRun, pa_test_data["completed_run_id"])

        assert run.engine == "mascope"


class TestRecalibrationExcludesImports:
    """An imported verdict must not reach the instrument-wide calibration curve.

    ``create_verification`` snapshots ``evidence`` from the judged row, and
    ``recalibrate_instrument`` fits the Platt curve every assignment's P(correct)
    reads from over those snapshots. That is why the route is superuser-only -
    so an editor-supplied number must not be one of its labels. Imported
    verifications are still stored, listed and shown; they simply do not vote.
    """

    @pytest_asyncio.fixture
    async def verified_runs(self, async_session_factory, import_sample):
        """One in-app and one imported run, each with a confirmed verdict.

        The two verdicts sit on different peaks. A verdict's identity spans
        runs by design - the peak, formula and adduct, with no run in it - so
        putting both on one peak would make them two *current* verdicts on a
        single finding, which the partial unique index now refuses. Nothing
        here is about the identity: what is under test is that the engine
        behind a verdict decides whether it votes.
        """
        now = datetime.now(timezone.utc)
        created = {}
        async with async_session_factory() as session:
            for engine, evidence, peak_id in (
                ("mascope", 0.9, "peak-0"),
                ("peaky", 0.1, "peak-1"),
            ):
                run_id = gen_id()
                assignment_id = gen_id(32)
                session.add(
                    PeakAssignmentRun(
                        peak_assignment_run_id=run_id,
                        sample_item_id=import_sample,
                        engine=engine,
                        engine_version="test",
                        status="completed",
                        peak_assignment_run_utc_created=now - timedelta(hours=2),
                        peak_assignment_run_utc_completed=now - timedelta(hours=1),
                    )
                )
                session.add(
                    PeakAssignment(
                        peak_assignment_id=assignment_id,
                        peak_assignment_run_id=run_id,
                        sample_item_id=import_sample,
                        sample_peak_id=peak_id,
                        sample_peak_mz=181.0707,
                        sample_peak_intensity=5000.0,
                        role="M0",
                        tier="assigned",
                    )
                )
                # The verification's foreign key points at the assignment, and
                # nothing declares a relationship between them for the unit of
                # work to order by, so the assignment is written out first.
                await session.flush()
                session.add(
                    AssignmentVerification(
                        assignment_verification_id=gen_id(32),
                        sample_item_id=import_sample,
                        peak_assignment_id=assignment_id,
                        peak_assignment_run_id=run_id,
                        sample_peak_id=peak_id,
                        assigned_formula="C6H12O6",
                        verdict="confirmed",
                        evidence_level="msms",
                        fit_score=0.9,
                        evidence=evidence,
                        verified_utc=now,
                    )
                )
                created[engine] = run_id
            await session.commit()

        yield created

        async with async_session_factory() as session:
            await session.execute(
                delete(AssignmentVerification).where(
                    AssignmentVerification.sample_item_id == import_sample
                )
            )
            for run_id in created.values():
                await session.execute(
                    delete(PeakAssignmentRun).where(
                        PeakAssignmentRun.peak_assignment_run_id == run_id
                    )
                )
            await session.commit()

    @pytest.mark.asyncio
    async def test_only_in_app_verdicts_reach_the_label_pool(
        self, verified_runs, monkeypatch
    ):
        """Two confirmed verdicts exist; only the in-app one becomes a label."""
        from mascope_backend.api.new.peak_assignments import service

        captured = {}

        def _recalibrate(scores, labels, levels, **kwargs):
            captured["scores"] = list(scores)
            raise service.InsufficientCalibrationData("stop here")

        # The instrument filter reads the sample's filename; pin it so this test
        # is about the engine filter and not about filename parsing.
        monkeypatch.setattr(service, "get_instrument_type", lambda _filename: "orbi")
        monkeypatch.setattr(service, "recalibrate", _recalibrate)

        await service.recalibrate_instrument(instrument="orbi")

        # 0.9 is the in-app run's evidence, 0.1 the imported run's.
        assert 0.9 in captured["scores"]
        assert 0.1 not in captured["scores"]

    @pytest.mark.asyncio
    async def test_imported_verifications_are_still_listed(
        self, editor_client, import_sample, verified_runs, feature_enabled
    ):
        """Excluded from calibration, not hidden from the user."""
        response = await editor_client.get(
            f"/api/peak-assignments/sample/{import_sample}/verifications"
        )

        assert response.status_code == 200
        assert response.json()["results"] == 2


class TestRunProvenanceIsServed:
    """Stored is not disclosed: a reader has to be able to see it.

    An import bypasses the server-side m/z verification gate because it
    calibrates client-side, and the `calibration` it declares is what replaces
    that gate - which only works if the run listing returns it. `tier_bands` is
    the same argument for tiers: 'assigned' means nothing comparable across
    engines until the thresholds behind it are visible.
    """

    @pytest.mark.asyncio
    async def test_an_imported_run_discloses_its_engine_bands_and_calibration(
        self, editor_client, import_sample, feature_enabled
    ):
        await _post(editor_client, import_sample, _body([_row("peak-0")]))

        response = await editor_client.get(
            f"/api/peak-assignments/sample/{import_sample}/runs"
        )

        assert response.status_code == 200
        record = response.json()["data"][0]
        assert record["engine"] == "peaky"
        assert record["tier_bands"] == TIER_BANDS
        assert record["calibration"] == CALIBRATION

    @pytest.mark.asyncio
    async def test_an_in_app_run_reads_back_as_the_in_app_engine(
        self, editor_client, import_sample, feature_enabled, async_session_factory
    ):
        """Never null, so a reader compares it without handling a sentinel."""
        async with async_session_factory() as session:
            session.add(
                PeakAssignmentRun(
                    peak_assignment_run_id=gen_id(),
                    sample_item_id=import_sample,
                    engine="mascope",
                    engine_version="test",
                    status="completed",
                    tier_bands={"assigned": 0.7, "candidate": 0.4},
                    peak_assignment_run_utc_created=datetime.now(timezone.utc),
                )
            )
            await session.commit()

        response = await editor_client.get(
            f"/api/peak-assignments/sample/{import_sample}/runs"
        )

        record = response.json()["data"][0]
        assert record["engine"] == "mascope"
        # An in-app run's calibration state is the sample's own, not a
        # disclosure, so the column stays null on this side.
        assert record["calibration"] is None


class TestOwnerLinkageIsOneLevelDeep:
    """Owner linkage models an isotopologue naming the M0 it belongs to.

    Only the direct self-reference used to be checked, which let a two-row cycle
    and arbitrary chains resolve into a ledger no in-app run can produce.
    """

    @pytest.mark.asyncio
    async def test_a_row_that_is_not_an_iso_child_may_not_name_an_owner(
        self, editor_client, import_sample, feature_enabled
    ):
        response = await _post(
            editor_client,
            import_sample,
            _body(
                [
                    _row("peak-0", role="M0"),
                    _row("peak-1", role="M0", owner_sample_peak_id="peak-0"),
                ]
            ),
        )

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_a_two_row_cycle_is_refused(
        self, editor_client, import_sample, feature_enabled, async_session_factory
    ):
        """A owns B, B owns A - both iso_child, so each owner is itself owned."""
        response = await _post(
            editor_client,
            import_sample,
            _body(
                [
                    _row("peak-0", role="iso_child", owner_sample_peak_id="peak-1"),
                    _row("peak-1", role="iso_child", owner_sample_peak_id="peak-0"),
                ]
            ),
        )

        assert response.status_code == 422
        async with async_session_factory() as session:
            runs = (
                (
                    await session.execute(
                        select(PeakAssignmentRun).where(
                            PeakAssignmentRun.sample_item_id == import_sample
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert [run.status for run in runs] == ["importing"]

    @pytest.mark.asyncio
    async def test_a_chain_is_refused(
        self, editor_client, import_sample, feature_enabled
    ):
        """peak-2 -> peak-1 -> peak-0: the middle row is an owned owner."""
        response = await _post(
            editor_client,
            import_sample,
            _body(
                [
                    _row("peak-0", role="M0"),
                    _row("peak-1", role="iso_child", owner_sample_peak_id="peak-0"),
                    _row("peak-2", role="iso_child", owner_sample_peak_id="peak-1"),
                ]
            ),
        )

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_the_ordinary_one_level_link_still_lands(
        self, editor_client, import_sample, feature_enabled, async_session_factory
    ):
        response = await _post(
            editor_client,
            import_sample,
            _body(
                [
                    _row("peak-0", role="M0"),
                    _row("peak-1", role="iso_child", owner_sample_peak_id="peak-0"),
                ]
            ),
        )

        assert response.status_code == 200
        run_id = response.json()["data"][0]["peak_assignment_run_id"]
        async with async_session_factory() as session:
            rows = {row.sample_peak_id: row for row in await _rows_of(session, run_id)}
        assert (
            rows["peak-1"].owner_peak_assignment_id == rows["peak-0"].peak_assignment_id
        )


class TestAbandonTakesTheSampleClaim:
    """The delete has to be exclusive with a chunk that is still in flight.

    Without the claim it can land between a chunk's run lookup and its insert,
    cascading the staged rows away underneath a request that is still running -
    and the client is told its chunk broke a database constraint rather than
    that its run is gone.
    """

    @pytest.mark.asyncio
    async def test_a_delete_is_refused_while_the_sample_is_claimed(
        self, editor_client, import_sample, feature_enabled
    ):
        from mascope_backend.api.new.peak_assignments.admission import assignment_claim

        first = await _post(
            editor_client,
            import_sample,
            _body([_row("peak-0")], complete=False, import_id="import-claimed"),
        )
        run_id = first.json()["data"][0]["peak_assignment_run_id"]

        async with assignment_claim("sample", import_sample) as acquired:
            assert acquired, "the fixture sample should be free to claim here"
            blocked = await editor_client.delete(
                f"/api/peak-assignments/sample/{import_sample}/runs/{run_id}"
            )

        assert blocked.status_code == 409

        # And it succeeds once the claim is released, so the refusal was the
        # claim and not the run's state.
        released = await editor_client.delete(
            f"/api/peak-assignments/sample/{import_sample}/runs/{run_id}"
        )
        assert released.status_code == 200


class TestAnAssemblingRunIsNotServedByExplicitId:
    """A partial ledger must not be served as a ledger.

    The default read resolves to the latest *completed* run, so it is safe on
    its own - but a read with an explicit run id serves whatever it names, the
    runs endpoint lists runs of every status, and the import's own first
    response hands the client the id. That is three ways to reach a half-built
    ledger whose rows are real while its set is not.
    """

    @pytest.mark.asyncio
    async def test_reading_an_importing_run_by_id_is_refused(
        self, editor_client, import_sample, feature_enabled
    ):
        first = await _post(
            editor_client,
            import_sample,
            _body([_row("peak-0")], complete=False, import_id="import-partial"),
        )
        run_id = first.json()["data"][0]["peak_assignment_run_id"]

        response = await editor_client.get(
            f"/api/peak-assignments/sample/{import_sample}",
            params={"peak_assignment_run_id": run_id},
        )

        assert response.status_code == 409

    @pytest.mark.asyncio
    async def test_the_same_run_reads_normally_once_it_completes(
        self, editor_client, import_sample, feature_enabled
    ):
        """The refusal is about the status, not the run."""
        first = await _post(
            editor_client,
            import_sample,
            _body([_row("peak-0")], complete=False, import_id="import-partial"),
        )
        run_id = first.json()["data"][0]["peak_assignment_run_id"]
        await _post(
            editor_client,
            import_sample,
            _body(
                [_row("peak-1")],
                run_id=run_id,
                index=1,
                complete=True,
                import_id="import-partial",
            ),
        )

        response = await editor_client.get(
            f"/api/peak-assignments/sample/{import_sample}",
            params={"peak_assignment_run_id": run_id},
        )

        assert response.status_code == 200
        assert response.json()["total"] == 2

    @pytest.mark.asyncio
    async def test_an_in_app_run_still_in_flight_is_not_refused(
        self, editor_client, import_sample, feature_enabled, async_session_factory
    ):
        """'running' is deliberately outside this guard.

        The in-app engine writes its whole ledger in one insert at the end, so a
        read mid-flight returns an empty result that is honest about itself -
        unlike an import, which accumulates. Widening the refusal to every
        non-terminal status would change behaviour this endpoint already
        promises for in-app runs.
        """
        run_id = gen_id()
        async with async_session_factory() as session:
            session.add(
                PeakAssignmentRun(
                    peak_assignment_run_id=run_id,
                    sample_item_id=import_sample,
                    engine="mascope",
                    engine_version="test",
                    status="running",
                    peak_assignment_run_utc_created=datetime.now(timezone.utc),
                )
            )
            await session.commit()

        response = await editor_client.get(
            f"/api/peak-assignments/sample/{import_sample}",
            params={"peak_assignment_run_id": run_id},
        )

        assert response.status_code == 200
        assert response.json()["total"] == 0


class TestTheEnginesOwnTierIsRecorded:
    """An imported run can say what ITS engine concluded, not only what this
    server's bands make of the evidence.

    `tier` is checked against the declared bands and is unchanged - that is what
    makes tiers from two engines comparable. But the check binds `tier` to a
    band function, so a verdict reached any other way had nowhere to go, and a
    DEMOTION was refused as firmly as an inflation. Demotions are exactly what a
    second engine contributes: peaky tiers on window uniqueness, corroboration
    and mass degeneracy, none of them a threshold on evidence.
    """

    @pytest.mark.asyncio
    async def test_an_engine_tier_the_bands_would_refuse_is_stored(
        self, editor_client, import_sample, feature_enabled, async_session_factory
    ):
        """The motivating case: a strong fit the engine nonetheless demoted.

        As `tier` this is a 422 (`test_a_demoted_tier_is_rejected_too` in the
        unit suite). As `engine_tier` it is the point of the field.
        """
        response = await _post(
            editor_client,
            import_sample,
            _body([_row("peak-0", fit_score=0.92, engine_tier="below_assignability")]),
        )

        assert response.status_code == 200
        run_id = response.json()["data"][0]["peak_assignment_run_id"]
        async with async_session_factory() as session:
            rows = await _rows_of(session, run_id)
        assert [(r.tier, r.engine_tier) for r in rows] == [
            ("assigned", "below_assignability")
        ]

    @pytest.mark.asyncio
    async def test_it_is_not_a_way_past_the_coherence_rule(
        self, editor_client, import_sample, feature_enabled
    ):
        """The exemption is one-directional: `tier` is still held to the bands
        however coherent the engine's own verdict is."""
        response = await _post(
            editor_client,
            import_sample,
            _body(
                [
                    _row(
                        "peak-0",
                        tier="assigned",
                        fit_score=0.62,
                        engine_tier="candidate",
                    )
                ]
            ),
        )

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_the_legacy_spelling_reaches_the_new_column(
        self, editor_client, import_sample, feature_enabled, async_session_factory
    ):
        """Otherwise a rename manufactures a disagreement out of nothing."""
        response = await _post(
            editor_client,
            import_sample,
            _body([_row("peak-0", engine_tier="identified")]),
        )

        assert response.status_code == 200
        run_id = response.json()["data"][0]["peak_assignment_run_id"]
        async with async_session_factory() as session:
            rows = await _rows_of(session, run_id)
        assert rows[0].engine_tier == "assigned"

    @pytest.mark.asyncio
    async def test_an_unknown_verdict_is_refused(
        self, editor_client, import_sample, feature_enabled
    ):
        response = await _post(
            editor_client,
            import_sample,
            _body([_row("peak-0", engine_tier="splendid")]),
        )

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_absence_is_the_default(
        self, editor_client, import_sample, feature_enabled, async_session_factory
    ):
        response = await _post(editor_client, import_sample, _body([_row("peak-0")]))

        assert response.status_code == 200
        run_id = response.json()["data"][0]["peak_assignment_run_id"]
        async with async_session_factory() as session:
            rows = await _rows_of(session, run_id)
        assert rows[0].engine_tier is None


class TestTheServerDerivesAnOmittedTier:
    """A row need not state a tier, and the documented advice is not to.

    Every input is already server-side, and the server computes the answer
    anyway to check a supplied one. Sending it means reproducing this
    deployment's chemical plausibility; the copies drift, and the drift refuses
    a whole import over a number the client had no reason to hold.
    """

    @pytest.mark.asyncio
    async def test_an_omitted_tier_is_derived_under_the_declared_bands(
        self, editor_client, import_sample, feature_enabled, async_session_factory
    ):
        rows = [
            _row("peak-0", fit_score=0.92, tier=None),
            _row("peak-1", fit_score=0.62, tier=None),
            _row("peak-2", fit_score=0.10, tier=None),
        ]
        response = await _post(editor_client, import_sample, _body(rows))

        assert response.status_code == 200
        run_id = response.json()["data"][0]["peak_assignment_run_id"]
        async with async_session_factory() as session:
            stored = await _rows_of(session, run_id)
        assert {r.sample_peak_id: r.tier for r in stored} == {
            "peak-0": "assigned",
            "peak-1": "candidate",
            "peak-2": "below_assignability",
        }

    @pytest.mark.asyncio
    async def test_an_unscored_row_splits_on_whether_a_formula_was_committed(
        self, editor_client, import_sample, feature_enabled, async_session_factory
    ):
        """The two tiers `coherent_tiers` admits, told apart the way the in-app
        ledger writes them."""
        rows = [
            _row("peak-0", fit_score=None, tier=None),
            _row("peak-1", fit_score=None, tier=None, assigned_formula=None),
        ]
        response = await _post(editor_client, import_sample, _body(rows))

        assert response.status_code == 200
        run_id = response.json()["data"][0]["peak_assignment_run_id"]
        async with async_session_factory() as session:
            stored = await _rows_of(session, run_id)
        assert {r.sample_peak_id: r.tier for r in stored} == {
            "peak-0": "below_assignability",
            "peak-1": "unassigned",
        }

    @pytest.mark.asyncio
    async def test_a_scored_row_with_no_formula_is_unassigned(
        self, editor_client, import_sample, feature_enabled, async_session_factory
    ):
        """A fit score is not on its own a claim about a peak.

        The regression this guards: banded on the bare fit, this row derived
        'assigned' and the ledger showed an assigned chip beside an empty
        formula cell - a row the in-app engine cannot produce.
        """
        response = await _post(
            editor_client,
            import_sample,
            _body([_row("peak-0", fit_score=0.92, tier=None, assigned_formula=None)]),
        )

        assert response.status_code == 200
        run_id = response.json()["data"][0]["peak_assignment_run_id"]
        async with async_session_factory() as session:
            stored = await _rows_of(session, run_id)
        assert stored[0].tier == "unassigned"
        # And no evidence beside it: the chip shows the number the tier was
        # read off, so "unassigned - 92%" would be the same mistake wearing
        # the right tier.
        assert "evidence" not in (stored[0].provenance or {})

    @pytest.mark.asyncio
    async def test_the_same_row_cannot_CLAIM_a_tier_either(
        self, editor_client, import_sample, feature_enabled
    ):
        """Derive and check are one rule, so the refusal moves with it."""
        response = await _post(
            editor_client,
            import_sample,
            _body(
                [
                    _row(
                        "peak-0",
                        fit_score=0.92,
                        tier="assigned",
                        assigned_formula=None,
                    )
                ]
            ),
        )

        # The endpoint genericizes a 422 body to an error id, so the wording is
        # pinned against the validator directly in the unit suite
        # (`test_a_fit_score_with_no_formula_is_not_evidence`); here the refusal
        # itself is what matters.
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_a_supplied_tier_is_still_honoured(
        self, editor_client, import_sample, feature_enabled, async_session_factory
    ):
        """Deriving is a default, not a rewrite - nothing that worked before
        stops working."""
        response = await _post(
            editor_client,
            import_sample,
            _body([_row("peak-0", tier="candidate", fit_score=0.62)]),
        )

        assert response.status_code == 200
        run_id = response.json()["data"][0]["peak_assignment_run_id"]
        async with async_session_factory() as session:
            stored = await _rows_of(session, run_id)
        assert stored[0].tier == "candidate"


class TestTheChemistryTheServerReadsIsWrittenBack:
    """An imported row carries this server's reading of its formula, not the
    importer's - and carries it at all.

    `plausibility` is the factor that turns a fit into the evidence a tier is
    read off. The peak inspector renders it as this server's judgement of the
    chemistry, so leaving it absent showed an imported row with a tier, an
    evidence, and a blank where the number connecting them belongs.
    """

    @pytest.mark.asyncio
    async def test_the_servers_plausibility_is_written(
        self, editor_client, import_sample, feature_enabled, async_session_factory
    ):
        response = await _post(editor_client, import_sample, _body([_row("peak-0")]))
        assert response.status_code == 200
        run_id = response.json()["data"][0]["peak_assignment_run_id"]

        async with async_session_factory() as session:
            row = (await _rows_of(session, run_id))[0]

        # C6H12O6 sits in the common element-ratio range, so it weighs nothing
        # and the evidence is the bare fit.
        assert row.provenance["plausibility"] == 1.0
        assert row.provenance["evidence"] == 0.92

    @pytest.mark.asyncio
    async def test_an_importers_own_plausibility_is_overwritten(
        self, editor_client, import_sample, feature_enabled, async_session_factory
    ):
        """Derived, never asserted - the same rule as `evidence`, and for the
        same reason: the inspector presents it as this server's reading."""
        response = await _post(
            editor_client,
            import_sample,
            _body([_row("peak-0", provenance={"plausibility": 0.01})]),
        )
        assert response.status_code == 200
        run_id = response.json()["data"][0]["peak_assignment_run_id"]

        async with async_session_factory() as session:
            row = (await _rows_of(session, run_id))[0]

        assert row.provenance["plausibility"] == 1.0

    @pytest.mark.asyncio
    async def test_a_row_with_no_formula_carries_no_plausibility(
        self, editor_client, import_sample, feature_enabled, async_session_factory
    ):
        """There is nothing to weigh, and a dash is the honest rendering."""
        response = await _post(
            editor_client,
            import_sample,
            _body(
                [
                    _row(
                        "peak-0",
                        assigned_formula=None,
                        ion_formula=None,
                        fit_score=None,
                        tier="unassigned",
                    )
                ]
            ),
        )
        assert response.status_code == 200
        run_id = response.json()["data"][0]["peak_assignment_run_id"]

        async with async_session_factory() as session:
            row = (await _rows_of(session, run_id))[0]

        assert row.provenance is None or "plausibility" not in row.provenance

    @pytest.mark.asyncio
    async def test_a_formula_this_server_cannot_read_carries_none_either(
        self, editor_client, import_sample, feature_enabled, async_session_factory
    ):
        """`formula_plausibility` fails open to 1.0 so an unreadable formula
        never demotes a row - right for weighing a fit, wrong for storing, where
        it would assert perfect chemistry for a string nothing could parse. The
        evidence beside it is still the bare fit, as everywhere else."""
        response = await _post(
            editor_client,
            import_sample,
            _body(
                [
                    _row(
                        "peak-0",
                        assigned_formula="not a formula",
                        ion_formula=None,
                        fit_score=0.92,
                        tier=None,
                    )
                ]
            ),
        )

        assert response.status_code == 200
        run_id = response.json()["data"][0]["peak_assignment_run_id"]

        async with async_session_factory() as session:
            row = (await _rows_of(session, run_id))[0]

        assert "plausibility" not in (row.provenance or {})
        assert row.provenance["evidence"] == 0.92


class TestTheLedgerFiltersOnTheEnginesOwnTier:
    """The comparison an imported run exists for, served rather than
    reconstructed.

    The subtle half is `tier_disagrees=false`: a row carrying NO engine tier is
    excluded from both answers, because silence is not agreement. Folding those
    into `false` would report every in-app row as an engine that concurred - and
    a later simplification to a plain `engine_tier != tier` would do exactly
    that while still passing a `true`-only test, since SQL `NULL != 'assigned'`
    is UNKNOWN rather than true.
    """

    @staticmethod
    async def _import_mixed(client, sample):
        """Three rows: one disagreeing, one agreeing, one with no verdict."""
        return await _post(
            client,
            sample,
            _body(
                [
                    _row("peak-0", engine_tier="candidate"),
                    _row("peak-1", engine_tier="assigned"),
                    _row("peak-2"),
                ]
            ),
        )

    @staticmethod
    def _peaks(response):
        return {row["sample_peak_id"] for row in response.json()["data"]}

    @pytest.mark.asyncio
    async def test_it_filters_on_the_engine_tier_directly(
        self, editor_client, import_sample, feature_enabled
    ):
        assert (await self._import_mixed(editor_client, import_sample)).status_code == (
            200
        )

        response = await editor_client.get(
            f"/api/peak-assignments/sample/{import_sample}",
            params={"engine_tier": "candidate"},
        )

        assert response.status_code == 200
        assert self._peaks(response) == {"peak-0"}

    @pytest.mark.asyncio
    async def test_disagreement_is_the_rows_where_the_two_differ(
        self, editor_client, import_sample, feature_enabled
    ):
        await self._import_mixed(editor_client, import_sample)

        response = await editor_client.get(
            f"/api/peak-assignments/sample/{import_sample}",
            params={"tier_disagrees": "true"},
        )

        assert response.status_code == 200
        assert self._peaks(response) == {"peak-0"}

    @pytest.mark.asyncio
    async def test_agreement_excludes_the_rows_that_said_nothing(
        self, editor_client, import_sample, feature_enabled
    ):
        """peak-2 stated no tier, so it is in neither answer."""
        await self._import_mixed(editor_client, import_sample)

        response = await editor_client.get(
            f"/api/peak-assignments/sample/{import_sample}",
            params={"tier_disagrees": "false"},
        )

        assert response.status_code == 200
        assert self._peaks(response) == {"peak-1"}

    @pytest.mark.asyncio
    async def test_an_unfiltered_read_still_carries_the_whole_run(
        self, editor_client, import_sample, feature_enabled
    ):
        """And serves the column, which nothing had to be taught to project."""
        await self._import_mixed(editor_client, import_sample)

        response = await editor_client.get(
            f"/api/peak-assignments/sample/{import_sample}"
        )

        assert response.status_code == 200
        by_peak = {row["sample_peak_id"]: row for row in response.json()["data"]}
        assert by_peak["peak-0"]["engine_tier"] == "candidate"
        assert by_peak["peak-2"]["engine_tier"] is None
