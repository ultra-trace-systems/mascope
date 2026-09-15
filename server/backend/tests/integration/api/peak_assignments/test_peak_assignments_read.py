"""
Integration tests for the peak assignments read API.

Exercises the peaks-with-assignments and runs endpoints through the full
HTTP stack, including latest-completed-run resolution, filtering, 404
handling, and the editor-role requirement on the assign endpoint.
"""

import pytest
from sqlalchemy import text, update

from mascope_backend.db import PeakAssignment, PeakAssignmentRun
from mascope_backend.db.id import gen_id


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
        params={"tier": "assigned"},
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
async def test_the_tier_filter_still_accepts_the_pre_rename_spelling(
    guest_client, pa_test_data
):
    """A reader pinned to 'identified' keeps getting the rows it asked for.

    The filter is a closed vocabulary, so a spelling the server does not know
    is a 422 rather than an empty ledger - which would turn every SDK client
    written against the older documentation into an error on the release that
    renamed the tier. The rows it wants are the ones now stored as 'assigned',
    so the alias resolves to that and the answer is the same ledger.
    """
    sample_item_id = pa_test_data["sample_item_id"]

    legacy = await guest_client.get(
        f"/api/peak-assignments/sample/{sample_item_id}",
        params={"tier": "identified"},
    )
    current = await guest_client.get(
        f"/api/peak-assignments/sample/{sample_item_id}",
        params={"tier": "assigned"},
    )

    assert legacy.status_code == 200
    assert legacy.json()["results"] == 2
    assert [row["sample_peak_id"] for row in legacy.json()["data"]] == [
        row["sample_peak_id"] for row in current.json()["data"]
    ]
    # The rows come back under the current spelling either way: the alias is
    # applied to the query, never to what is served.
    assert {row["tier"] for row in legacy.json()["data"]} == {"assigned"}


@pytest.mark.asyncio
async def test_the_batch_ledger_filter_answers_the_pre_rename_spelling(
    guest_client, pa_test_data
):
    """The batch overview filters a tier the rename reaches too.

    Its filter is a second wire site, on `consensus_tier` rather than on the
    per-sample tier, and it has to keep the same promise: the old spelling is
    answered, and one that is neither spelling is refused rather than silently
    matching nothing.
    """
    sample_batch_id = pa_test_data["sample_batch_id"]

    legacy = await guest_client.get(
        f"/api/batch-peaks/batch/{sample_batch_id}",
        params={"tier": "identified", "min_n_present": 1},
    )
    misspelled = await guest_client.get(
        f"/api/batch-peaks/batch/{sample_batch_id}",
        params={"tier": "assinged", "min_n_present": 1},
    )

    assert legacy.status_code == 200
    assert misspelled.status_code == 422


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
async def test_list_rows_are_slim_with_flattened_confidence_scalars(
    guest_client, pa_test_data
):
    """The ledger list is a slim projection of the run.

    `alternatives`/`provenance` are ~74% of a full row's bytes and only the
    peak inspector reads them, so the list drops them and instead flattens the
    few provenance scalars the ledger columns render.
    """
    response = await guest_client.get(
        f"/api/peak-assignments/sample/{pa_test_data['sample_item_id']}"
    )
    assert response.status_code == 200
    body = response.json()

    for row in body["data"]:
        assert "alternatives" not in row
        assert "provenance" not in row

    by_peak = {row["sample_peak_id"]: row for row in body["data"]}
    assigned = by_peak["peak-1"]
    assert assigned["p_correct"] == pytest.approx(0.93)
    assert assigned["p_correct_provisional"] is True
    assert assigned["corroboration_adducts"] == 2

    # A row without provenance reports the scalars as null, not missing.
    unassigned = by_peak["peak-3"]
    assert unassigned["p_correct"] is None
    assert unassigned["p_correct_provisional"] is None
    assert unassigned["corroboration_adducts"] is None


@pytest.mark.asyncio
async def test_detail_returns_the_full_assignment(guest_client, pa_test_data):
    """The detail endpoint serves one assignment whole (a list-row superset)."""
    response = await guest_client.get(
        f"/api/peak-assignments/sample/{pa_test_data['sample_item_id']}"
        f"/assignment/{pa_test_data['m0_assignment_id']}"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["results"] == 1
    (record,) = body["data"]

    assert record["peak_assignment_id"] == pa_test_data["m0_assignment_id"]
    assert record["sample_peak_id"] == "peak-1"
    assert record["assigned_formula"] == "C6H12O6"

    # The inspector detail the list omits...
    assert record["alternatives"][0]["assigned_formula"] == "C7H16O5"
    assert record["provenance"]["plausibility"] == pytest.approx(0.9)
    assert record["provenance"]["corroboration"]["adducts"] == ["+H+", "+Na+"]
    # ...plus the same flattened scalars the list rows carry.
    assert record["p_correct"] == pytest.approx(0.93)
    assert record["p_correct_provisional"] is True
    assert record["corroboration_adducts"] == 2


@pytest.mark.asyncio
async def test_the_run_calibration_is_folded_back_into_the_row(
    guest_client, pa_test_data, async_session_factory
):
    """The engine records the curve once per run and only `p_correct` per row;
    both reads serve the row as if it still carried the pair - the detail with
    `calibrated` / `calibration`, the list with the provisional flag - and the
    runs endpoint shows the record itself."""
    curve = {"instrument": "orbi", "provisional": False, "source": "curated set"}
    run_id = pa_test_data["completed_run_id"]
    sample_id = pa_test_data["sample_item_id"]
    row_id = gen_id(32)
    async with async_session_factory() as session:
        await session.execute(
            update(PeakAssignmentRun)
            .where(PeakAssignmentRun.peak_assignment_run_id == run_id)
            .values(confidence_calibration=curve)
        )
        session.add(
            PeakAssignment(
                peak_assignment_id=row_id,
                peak_assignment_run_id=run_id,
                sample_item_id=sample_id,
                sample_peak_id="peak-cal",
                sample_peak_mz=333.3,
                sample_peak_intensity=10.0,
                role="M0",
                assigned_formula="C9H8O4",
                source="database",
                fit_score=0.8,
                tier="assigned",
                provenance={"evidence": 0.8, "p_correct": 0.77, "score_version": 2},
            )
        )
        await session.commit()
    try:
        detail = await guest_client.get(
            f"/api/peak-assignments/sample/{sample_id}/assignment/{row_id}"
        )
        assert detail.status_code == 200
        (record,) = detail.json()["data"]
        assert record["provenance"]["p_correct"] == pytest.approx(0.77)
        assert record["provenance"]["calibrated"] is True
        assert record["provenance"]["calibration"] == curve
        assert record["p_correct_provisional"] is False

        listing = await guest_client.get(f"/api/peak-assignments/sample/{sample_id}")
        by_peak = {row["sample_peak_id"]: row for row in listing.json()["data"]}
        assert by_peak["peak-cal"]["p_correct"] == pytest.approx(0.77)
        assert by_peak["peak-cal"]["p_correct_provisional"] is False
        # A row that still carries its own block (the fixture's) reads that block.
        assert by_peak["peak-1"]["p_correct_provisional"] is True

        runs = await guest_client.get(f"/api/peak-assignments/sample/{sample_id}/runs")
        by_run = {run["peak_assignment_run_id"]: run for run in runs.json()["data"]}
        assert by_run[run_id]["confidence_calibration"] == curve
    finally:
        async with async_session_factory() as session:
            row = await session.get(PeakAssignment, row_id)
            if row is not None:
                await session.delete(row)
            await session.execute(
                update(PeakAssignmentRun)
                .where(PeakAssignmentRun.peak_assignment_run_id == run_id)
                .values(confidence_calibration=None)
            )
            await session.commit()


@pytest.mark.asyncio
async def test_formula_only_alternatives_are_stored_packed_and_served_whole(
    guest_client, pa_test_data, async_session_factory
):
    """The finder's shortlist entries are two-element lists on disk and the
    dicts every reader expects on the way out, in the order they were written."""
    run_id = pa_test_data["completed_run_id"]
    sample_id = pa_test_data["sample_item_id"]
    row_id = gen_id(32)
    formula_only = {
        "assigned_formula": "C10H14O8",
        "plausibility": 1.0,
        "source": "untargeted",
    }
    scored = {
        "assigned_formula": "C8H12O4",
        "ion_formula": "C8H12BrO4-",
        "fit_score": 0.27,
        "plausibility": 1.0,
        "source": "database",
    }
    async with async_session_factory() as session:
        session.add(
            PeakAssignment(
                peak_assignment_id=row_id,
                peak_assignment_run_id=run_id,
                sample_item_id=sample_id,
                sample_peak_id="peak-packed",
                sample_peak_mz=444.4,
                sample_peak_intensity=10.0,
                role="M0",
                assigned_formula="C10H16O8",
                source="untargeted",
                fit_score=0.6,
                tier="candidate",
                alternatives=[formula_only, scored, formula_only],
                provenance={"evidence": 0.5},
            )
        )
        await session.commit()
    try:
        async with async_session_factory() as session:
            stored = (
                await session.execute(
                    text(
                        "SELECT alternatives::text FROM peak_assignment"
                        " WHERE peak_assignment_id = :id"
                    ),
                    {"id": row_id},
                )
            ).scalar_one()
        assert stored.startswith('[["C10H14O8", 1.0], {')

        detail = await guest_client.get(
            f"/api/peak-assignments/sample/{sample_id}/assignment/{row_id}"
        )
        assert detail.status_code == 200
        (record,) = detail.json()["data"]
        assert record["alternatives"] == [formula_only, scored, formula_only]
    finally:
        async with async_session_factory() as session:
            row = await session.get(PeakAssignment, row_id)
            if row is not None:
                await session.delete(row)
            await session.commit()


@pytest.mark.asyncio
async def test_detail_unknown_assignment_returns_404(guest_client, pa_test_data):
    response = await guest_client.get(
        f"/api/peak-assignments/sample/{pa_test_data['sample_item_id']}"
        "/assignment/does-not-exist-42"
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_assign_requires_editor_role(guest_client, pa_test_data, monkeypatch):
    """A guest is refused the launch by the role gate, not by the feature gate.

    Both answer 403 and the feature gate runs first, so the flag is pinned on
    here rather than left to its default: otherwise this passes for the wrong
    reason on any deployment where the feature is off, and stops testing the
    role it is named for.
    """
    monkeypatch.setenv("MASCOPE_PEAK_ASSIGNMENT", "1")
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


@pytest.mark.asyncio
async def test_batch_peak_counterpart_answers_a_miss_with_no_rows(
    guest_client, pa_test_data
):
    """A peak with no counterpart is a 200 with no rows, not a 404.

    The caller asks this on every sample switch and does nothing at all with a
    miss -- it leaves the selection empty, which is where the switch left it.
    An error status would be something every client has to catch and discard,
    and a toast the user never asked for.
    """
    sample_item_id = pa_test_data["sample_item_id"]

    response = await guest_client.get(
        "/api/batch-peaks/records/counterpart",
        params={
            "sample_item_id": sample_item_id,
            "sample_peak_id": "not-a-folded-peak",
            "target_sample_item_id": sample_item_id,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["results"] == 0
    assert body["data"] == []


@pytest.mark.asyncio
async def test_batch_peak_counterpart_refuses_a_sample_the_user_cannot_read(
    guest_client, pa_test_data
):
    """Both samples are checked, not just the one that resolves.

    The request names two of them, and an id the caller has no access to has to
    be refused even when the other one is theirs -- otherwise the endpoint
    answers questions about samples outside their workspaces.
    """
    sample_item_id = pa_test_data["sample_item_id"]

    as_source = await guest_client.get(
        "/api/batch-peaks/records/counterpart",
        params={
            "sample_item_id": "no-such-sample",
            "sample_peak_id": "A1",
            "target_sample_item_id": sample_item_id,
        },
    )
    as_target = await guest_client.get(
        "/api/batch-peaks/records/counterpart",
        params={
            "sample_item_id": sample_item_id,
            "sample_peak_id": "A1",
            "target_sample_item_id": "no-such-sample",
        },
    )

    assert as_source.status_code == 403
    assert as_target.status_code == 403
