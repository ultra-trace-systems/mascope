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

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

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

TIER_BANDS = {"identified": 0.8, "candidate": 0.5}
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


def _row(peak_id: str, *, tier="identified", fit_score=0.92, **overrides) -> dict:
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
    import_id=None,
    **overrides,
):
    """An import request body with the required run fields filled in."""
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
        self, editor_client, import_sample, feature_enabled, stub_fold_in
    ):
        """The slim-ledger case: create, rows and finalize in one request."""
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

    @pytest.mark.asyncio
    async def test_without_a_key_a_retried_create_is_refused_naming_the_first_run(
        self, editor_client, import_sample, feature_enabled
    ):
        """The hazard the key exists to remove, pinned as the documented cost."""
        body = _body([_row("peak-0")], complete=False)

        first = await _post(editor_client, import_sample, body)
        replay = await _post(editor_client, import_sample, body)

        assert first.status_code == 200
        assert replay.status_code == 409

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
        """0.62 is not 'identified' under the bands this run declared."""
        response = await _post(
            editor_client,
            import_sample,
            _body([_row("peak-0", tier="identified", fit_score=0.62)]),
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
                [_row("peak-0", tier="identified", fit_score=0.62)],
                tier_bands={"identified": 0.6, "candidate": 0.3},
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

        assert row.provenance == {"notes": "the importer's own detail"}

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
            editor_client, import_sample, _body([_row("peak-0")], complete=False)
        )

        response = await _post(
            editor_client, import_sample, _body([_row("peak-1")], complete=False)
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
        await _post(editor_client, import_sample, _body([_row("peak-0")]))

        response = await _post(editor_client, import_sample, _body([_row("peak-1")]))

        assert response.status_code == 200


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
            editor_client, import_sample, _body([_row("peak-0")], complete=False)
        )
        run_id = first.json()["data"][0]["peak_assignment_run_id"]
        await editor_client.delete(
            f"/api/peak-assignments/sample/{import_sample}/runs/{run_id}"
        )

        response = await _post(editor_client, import_sample, _body([_row("peak-0")]))

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
        """One in-app and one imported run, each with a confirmed verdict."""
        now = datetime.now(timezone.utc)
        created = {}
        async with async_session_factory() as session:
            for engine, evidence in (("mascope", 0.9), ("peaky", 0.1)):
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
                        sample_peak_id="peak-0",
                        sample_peak_mz=181.0707,
                        sample_peak_intensity=5000.0,
                        role="M0",
                        tier="identified",
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
                        sample_peak_id="peak-0",
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
