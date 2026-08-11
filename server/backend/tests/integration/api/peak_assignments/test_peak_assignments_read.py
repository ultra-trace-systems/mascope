"""
Integration tests for the peak assignments read API.

Exercises the peaks-with-assignments and runs endpoints through the full
HTTP stack, including latest-completed-run resolution, filtering, 404
handling, and the editor-role requirement on the assign endpoint.
"""

import pytest


@pytest.mark.asyncio
async def test_get_runs_returns_all_runs_newest_first(guest_client, pa_test_data):
    response = await guest_client.get(
        f"/api/peak-assignments/sample/{pa_test_data['sample_item_id']}/runs"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["results"] == 2
    run_ids = [run["peak_assignment_run_id"] for run in body["data"]]
    assert run_ids == [
        pa_test_data["running_run_id"],
        pa_test_data["completed_run_id"],
    ]


@pytest.mark.asyncio
async def test_get_assignments_defaults_to_latest_completed_run(
    guest_client, pa_test_data
):
    response = await guest_client.get(
        f"/api/peak-assignments/sample/{pa_test_data['sample_item_id']}"
    )
    assert response.status_code == 200
    body = response.json()

    # The 'running' run is newer but not completed, so the completed run wins.
    # The response carries no run object; run identity is on each row instead.
    assert body["results"] == 3
    assert {row["peak_assignment_run_id"] for row in body["data"]} == {
        pa_test_data["completed_run_id"]
    }

    # One row per observed peak, ordered by m/z
    mzs = [row["sample_peak_mz"] for row in body["data"]]
    assert mzs == sorted(mzs)

    by_peak = {row["sample_peak_id"]: row for row in body["data"]}
    assert by_peak["peak-1"]["role"] == "M0"
    assert by_peak["peak-1"]["assigned_formula"] == "C6H12O6"
    assert (
        by_peak["peak-2"]["owner_peak_assignment_id"]
        == pa_test_data["m0_assignment_id"]
    )
    assert by_peak["peak-3"]["tier"] == "unassigned"
    assert by_peak["peak-3"]["assigned_formula"] is None


@pytest.mark.asyncio
async def test_get_assignments_supports_tier_and_role_filters(
    guest_client, pa_test_data
):
    sample_item_id = pa_test_data["sample_item_id"]

    response = await guest_client.get(
        f"/api/peak-assignments/sample/{sample_item_id}",
        params={"tier": "identified"},
    )
    assert response.status_code == 200
    assert response.json()["results"] == 2

    response = await guest_client.get(
        f"/api/peak-assignments/sample/{sample_item_id}",
        params={"role": "M0"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["results"] == 1
    assert body["data"][0]["sample_peak_id"] == "peak-1"

    response = await guest_client.get(
        f"/api/peak-assignments/sample/{sample_item_id}",
        params={"source": "database"},
    )
    assert response.status_code == 200
    assert response.json()["results"] == 2


@pytest.mark.asyncio
async def test_get_assignments_with_explicit_run_id(guest_client, pa_test_data):
    response = await guest_client.get(
        f"/api/peak-assignments/sample/{pa_test_data['sample_item_id']}",
        params={"peak_assignment_run_id": pa_test_data["running_run_id"]},
    )
    assert response.status_code == 200
    body = response.json()
    # An explicit run id is accepted; the 'running' run has no assignments yet.
    # The response echoes no run object (run metadata lives on the runs endpoint).
    assert body["results"] == 0
    assert body["data"] == []


@pytest.mark.asyncio
async def test_get_assignments_unknown_run_id_returns_404(guest_client, pa_test_data):
    response = await guest_client.get(
        f"/api/peak-assignments/sample/{pa_test_data['sample_item_id']}",
        params={"peak_assignment_run_id": "does-not-exist-42"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_assign_requires_editor_role(guest_client, pa_test_data):
    response = await guest_client.post(
        f"/api/peak-assignments/sample/{pa_test_data['sample_item_id']}/assign"
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_get_assignments_pages_without_losing_or_repeating_rows(
    guest_client, pa_test_data
):
    """The ledger is one row per detected peak, so it is read a page at a time.

    `total` is the count across all pages, which is what lets a client know it
    has the whole run; `results` is the size of the page in hand.
    """
    sample_item_id = pa_test_data["sample_item_id"]
    seen = []
    for offset in range(0, 3):
        response = await guest_client.get(
            f"/api/peak-assignments/sample/{sample_item_id}",
            params={"limit": 1, "offset": offset},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 3
        assert body["results"] == 1
        seen.extend(row["sample_peak_id"] for row in body["data"])

    # Every peak exactly once, in m/z order, across the pages.
    assert seen == ["peak-1", "peak-2", "peak-3"]

    # Reading past the end is empty rather than an error, and still reports total.
    response = await guest_client.get(
        f"/api/peak-assignments/sample/{sample_item_id}",
        params={"limit": 1, "offset": 99},
    )
    assert response.status_code == 200
    assert response.json()["data"] == []
    assert response.json()["total"] == 3


@pytest.mark.asyncio
async def test_get_assignments_rejects_an_unknown_tier(guest_client, pa_test_data):
    """A misspelled filter is a 422, not a 200 with an empty ledger.

    An empty 200 reads as "this sample has no such peaks", which is exactly the
    wrong answer to give someone who typed the value wrong.
    """
    response = await guest_client.get(
        f"/api/peak-assignments/sample/{pa_test_data['sample_item_id']}",
        params={"tier": "identifed"},
    )
    assert response.status_code == 422
