"""
Integration tests for ``peaks_current`` on the runs listing.

Peak detection mints a fresh id for every peak, so a run from before the
sample's peaks were found again names none of them. The listing says so for
each completed run, checked from one of its rows against the sample's peak ids,
so the run selector and the SDK can tell before the run is opened.

The fixture samples carry a zarr filename with no file behind it, so the peak
file is stood in for at ``extract_peaks`` - beneath the id-only read the listing
uses, which the unit suite pins against the real signature. Replacing the ids
it serves is what detecting the peaks again does to the file.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import text

from mascope_backend.api.new.peak_assignments import peak_ids as peak_ids_module
from mascope_backend.db import PeakAssignment, PeakAssignmentRun, SampleFile, SampleItem
from mascope_backend.db.id import gen_id


#: The peak ids the sample's file holds before its peaks are found again.
LIVE_PEAK_IDS = ["live-a", "live-b", "live-c"]


@pytest_asyncio.fixture
async def stale_sample(async_session_factory, pa_test_data):
    """A sample of its own with a run of each kind the listing tells apart.

    Its own, so the shared fixture sample's run count stays what the other read
    tests assert.

    :return: The sample id and the run ids by kind.
    """
    now = datetime.now(timezone.utc)
    sample_file_id = gen_id()
    sample_item_id = gen_id()
    runs = {
        "with_rows": gen_id(),
        "no_rows": gen_id(),
        "failed": gen_id(),
        "running": gen_id(),
    }
    async with async_session_factory() as session:
        session.add(
            SampleFile(
                sample_file_id=sample_file_id,
                filename=f"peaks-current-test-{sample_file_id}.zarr",
                instrument="orbi-test",
                datetime=datetime(2026, 7, 4, 12, 0, 0),
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
                sample_item_name="Peaks Current Test Sample",
                sample_item_type="sample",
                polarity="+",
                sample_item_utc_created=now,
            )
        )
        await session.flush()
        statuses = {
            "with_rows": "completed",
            "no_rows": "completed",
            "failed": "failed",
            "running": "running",
        }
        for age, (kind, run_id) in enumerate(runs.items()):
            session.add(
                PeakAssignmentRun(
                    peak_assignment_run_id=run_id,
                    sample_item_id=sample_item_id,
                    engine_version="0.1.0-test",
                    status=statuses[kind],
                    config={},
                    peak_assignment_run_utc_created=now - timedelta(minutes=age),
                )
            )
        await session.flush()
        # The failed and the running run carry rows too: what makes them null
        # is their status, not an empty ledger.
        for kind in ("with_rows", "failed", "running"):
            for peak_id in LIVE_PEAK_IDS:
                session.add(
                    PeakAssignment(
                        peak_assignment_id=gen_id(32),
                        peak_assignment_run_id=runs[kind],
                        sample_item_id=sample_item_id,
                        sample_peak_id=peak_id,
                        sample_peak_mz=100.0,
                        sample_peak_intensity=1.0,
                        role="unassigned",
                        tier="unassigned",
                    )
                )
        await session.commit()

    yield {"sample_item_id": sample_item_id, "runs": runs}

    async with async_session_factory() as session:
        await session.execute(
            text("DELETE FROM sample_item WHERE sample_item_id = :id"),
            {"id": sample_item_id},
        )
        await session.execute(
            text("DELETE FROM sample_file WHERE sample_file_id = :id"),
            {"id": sample_file_id},
        )
        await session.commit()


@pytest.fixture
def peak_file(monkeypatch):
    """The sample's peak file, as the id-only read sees it.

    ``ids`` is what the file holds; replace it to find the peaks again.
    ``reads`` counts the reads, ``error`` makes the next read fail.
    """
    state = SimpleNamespace(ids=list(LIVE_PEAK_IDS), reads=0, error=None)

    def _extract_peaks(*_args, **_kwargs):
        state.reads += 1
        if state.error is not None:
            raise state.error
        return SimpleNamespace(peak_ids=list(state.ids))

    monkeypatch.setattr(peak_ids_module, "extract_peaks", _extract_peaks)
    return state


async def _peaks_current(client, sample_item_id) -> dict:
    response = await client.get(f"/api/peak-assignments/sample/{sample_item_id}/runs")
    assert response.status_code == 200
    return {
        run["peak_assignment_run_id"]: run["peaks_current"]
        for run in response.json()["data"]
    }


@pytest.mark.asyncio
async def test_a_run_whose_rows_name_live_peaks_is_current(
    guest_client, stale_sample, peak_file
):
    current = await _peaks_current(guest_client, stale_sample["sample_item_id"])

    assert current[stale_sample["runs"]["with_rows"]] is True


@pytest.mark.asyncio
async def test_the_same_run_is_stale_once_the_peaks_are_found_again(
    guest_client, stale_sample, peak_file
):
    run_id = stale_sample["runs"]["with_rows"]
    assert (await _peaks_current(guest_client, stale_sample["sample_item_id"]))[
        run_id
    ] is True

    # Detection again: every peak gets a fresh id.
    peak_file.ids = [f"redetected-{n}" for n in range(len(LIVE_PEAK_IDS))]

    assert (await _peaks_current(guest_client, stale_sample["sample_item_id"]))[
        run_id
    ] is False


@pytest.mark.asyncio
async def test_a_run_with_no_rows_or_not_completed_is_not_flagged(
    guest_client, stale_sample, peak_file
):
    """Null either way: there is no row to check, or the run is not a result."""
    runs = stale_sample["runs"]
    # Stale, so a status the check forgot to skip would read False, not null.
    peak_file.ids = ["redetected"]

    current = await _peaks_current(guest_client, stale_sample["sample_item_id"])

    assert current[runs["no_rows"]] is None
    assert current[runs["failed"]] is None
    assert current[runs["running"]] is None
    assert current[runs["with_rows"]] is False


@pytest.mark.asyncio
async def test_the_peak_ids_are_read_once_per_listing(
    guest_client, stale_sample, peak_file
):
    await _peaks_current(guest_client, stale_sample["sample_item_id"])

    assert peak_file.reads == 1


@pytest.mark.asyncio
async def test_no_read_when_no_completed_run_has_rows(
    guest_client, stale_sample, peak_file, async_session_factory
):
    """A sample with nothing to check costs the listing no peak file read."""
    async with async_session_factory() as session:
        await session.execute(
            text("DELETE FROM peak_assignment WHERE peak_assignment_run_id = :id"),
            {"id": stale_sample["runs"]["with_rows"]},
        )
        await session.commit()

    current = await _peaks_current(guest_client, stale_sample["sample_item_id"])

    assert peak_file.reads == 0
    assert set(current.values()) == {None}


@pytest.mark.asyncio
async def test_an_unreadable_peak_file_leaves_the_listing_unflagged(
    guest_client, stale_sample, peak_file
):
    """The flag is a hint; a missing peak file must not cost the listing."""
    peak_file.error = FileNotFoundError("no peak store")

    current = await _peaks_current(guest_client, stale_sample["sample_item_id"])

    assert len(current) == 4
    assert set(current.values()) == {None}


@pytest.mark.asyncio
async def test_the_shared_fixture_sample_without_a_peak_file_still_lists(
    guest_client, pa_test_data
):
    """No stand-in at all: the real read fails on the missing file, and the
    listing answers with every run unflagged."""
    current = await _peaks_current(guest_client, pa_test_data["sample_item_id"])

    assert current == {
        pa_test_data["completed_run_id"]: None,
        pa_test_data["running_run_id"]: None,
    }
