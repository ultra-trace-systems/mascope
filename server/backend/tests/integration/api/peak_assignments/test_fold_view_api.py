"""Integration tests for the Sample view derived from the batch ledger.

A sample whose peaks are in the batch ledger but that has no assignment run -
its runs deleted or pruned after the fold, or, once ingest folds without
writing a run, never written - is served from its member rows and their
anchors, through the same routes and in the same shape as a run-backed sample.
Seeds two samples into the shared test batch, folds them, deletes their runs,
and reads them back over HTTP. See ``fold_view.py``.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
    fold_sample_into_batch_peaks,
)
from mascope_backend.api.new.peak_assignments.fold_view import (
    DERIVED_READ_ONLY_CODE,
    fold_assignment_id,
    fold_run_id,
)
from mascope_backend.db import (
    PeakAssignment,
    PeakAssignmentRun,
    SampleBatch,
    SampleFile,
    SampleItem,
)
from mascope_backend.db.id import gen_id


pytestmark = pytest.mark.asyncio

# (peak id, mz, formula, ion formula, role, tier, fit, intensity, owner key, p_correct)
_ROWS = {
    "S1": [
        (
            "p1",
            181.0707,
            "C6H12O6",
            "C6H13O6+",
            "M0",
            "assigned",
            0.95,
            5000.0,
            None,
            0.91,
        ),
        (
            "p2",
            182.0741,
            "C6H12O6",
            "C6H13O6+",
            "iso_child",
            "assigned",
            0.88,
            350.0,
            "p1",
            None,
        ),
        (
            "p3",
            250.1000,
            None,
            None,
            "unassigned",
            "unassigned",
            None,
            300.0,
            None,
            None,
        ),
    ],
    # The same anchor as S1's p1 under a different formula: the identity the
    # derived detail offers S1 as an alternative.
    "S2": [
        (
            "p1",
            181.0707,
            "C5H12N2O4",
            "C5H13N2O4+",
            "M0",
            "candidate",
            0.60,
            800.0,
            None,
            None,
        ),
    ],
}


async def _seed_sample(session, batch_id, name, rows, now):
    sample_file_id, sample_item_id, run_id = gen_id(), gen_id(), gen_id()
    session.add(
        SampleFile(
            sample_file_id=sample_file_id,
            filename=f"orbi-fold-view-{name}-{sample_file_id}.zarr",
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
            sample_batch_id=batch_id,
            sample_file_id=sample_file_id,
            sample_item_name=f"Fold view {name}",
            sample_item_type="sample",
            polarity="+",
            sample_item_utc_created=now,
        )
    )
    session.add(
        PeakAssignmentRun(
            peak_assignment_run_id=run_id,
            sample_item_id=sample_item_id,
            engine_version="0.1.0-test",
            status="completed",
            peak_assignment_run_utc_created=now - timedelta(hours=1),
            peak_assignment_run_utc_completed=now - timedelta(minutes=50),
        )
    )
    ids: dict[str, str] = {}
    for pid, mz, formula, ion, role, tier, fit, intensity, owner, p_correct in rows:
        ids[pid] = gen_id(32)
        session.add(
            PeakAssignment(
                peak_assignment_id=ids[pid],
                peak_assignment_run_id=run_id,
                sample_item_id=sample_item_id,
                sample_peak_id=pid,
                sample_peak_mz=mz,
                sample_peak_intensity=intensity,
                role=role,
                assigned_formula=formula,
                ion_formula=ion,
                source=("database" if formula else None),
                fit_score=fit,
                mz_error_ppm=(1.0 if formula else None),
                tier=tier,
                owner_peak_assignment_id=(ids[owner] if owner else None),
                provenance=(
                    {"p_correct": p_correct} if p_correct is not None else None
                ),
            )
        )
    return sample_item_id, run_id


@pytest_asyncio.fixture
async def folded(async_session_factory, pa_test_data):
    """Two fresh samples in a batch of their own, each with a completed run,
    both folded into the batch peaks. Their runs are still in place.

    A batch per test rather than the shared one: anchors are per batch, so
    seeding into the shared batch would pile every test's members onto the same
    anchors and the consensus would read the whole module's history."""
    now = datetime.now(timezone.utc)
    batch_id = gen_id()
    async with async_session_factory() as session:
        dataset_id = (
            await session.execute(
                select(SampleBatch.dataset_id).where(
                    SampleBatch.sample_batch_id == pa_test_data["sample_batch_id"]
                )
            )
        ).scalar_one()
        session.add(
            SampleBatch(
                sample_batch_id=batch_id,
                dataset_id=dataset_id,
                sample_batch_name=f"Fold view {batch_id}",
                sample_batch_utc_created=now,
            )
        )
        s1, run1 = await _seed_sample(session, batch_id, "S1", _ROWS["S1"], now)
        s2, run2 = await _seed_sample(session, batch_id, "S2", _ROWS["S2"], now)
        await session.commit()
    assert await fold_sample_into_batch_peaks(s1) == batch_id
    assert await fold_sample_into_batch_peaks(s2) == batch_id
    return {"batch_id": batch_id, "s1": s1, "s2": s2, "run1": run1, "run2": run2}


async def _delete_runs(session_factory, *sample_ids):
    """What a deleted import or a prune does: the runs go, their ledger rows
    cascade, the members keep standing with NULL links."""
    async with session_factory() as session:
        await session.execute(
            delete(PeakAssignmentRun).where(
                PeakAssignmentRun.sample_item_id.in_(list(sample_ids))
            )
        )
        await session.commit()


@pytest.fixture
def assignment_enabled(monkeypatch):
    """The write routes are behind the feature flag, which the test environment
    leaves off; the reads answer either way."""
    monkeypatch.setenv("MASCOPE_PEAK_ASSIGNMENT", "1")


async def _ledger(client, sample_item_id, **params):
    response = await client.get(
        f"/api/peak-assignments/sample/{sample_item_id}", params=params
    )
    assert response.status_code == 200, response.text
    return response.json()


# --- the runs listing --------------------------------------------------------


async def test_the_derived_run_is_listed_after_the_real_ones(guest_client, folded):
    response = await guest_client.get(
        f"/api/peak-assignments/sample/{folded['s1']}/runs"
    )
    assert response.status_code == 200
    runs = response.json()["data"]
    assert [run["peak_assignment_run_id"] for run in runs] == [
        folded["run1"],
        fold_run_id(folded["s1"]),
    ]
    derived = runs[-1]
    assert derived["engine"] == "batch"
    assert derived["status"] == "completed"
    assert derived["config"]["n_members"] == 3
    assert derived["config"]["sample_batch_id"] == folded["batch_id"]
    # No creation time on purpose: a client ordering by it sorts this run last.
    assert derived["peak_assignment_run_utc_created"] is None
    assert derived["peak_assignment_run_utc_completed"] is not None


async def test_a_sample_with_a_run_still_reads_its_run(guest_client, folded):
    body = await _ledger(guest_client, folded["s1"])
    assert body["total"] == 3
    assert {row["peak_assignment_run_id"] for row in body["data"]} == {folded["run1"]}
    assert all(row["batch_peak_id"] is None for row in body["data"])


# --- the derived ledger --------------------------------------------------------


async def test_a_sample_without_a_run_is_served_from_the_batch_ledger(
    guest_client, async_session_factory, folded
):
    await _delete_runs(async_session_factory, folded["s1"], folded["s2"])
    s1 = folded["s1"]

    runs = (await guest_client.get(f"/api/peak-assignments/sample/{s1}/runs")).json()
    assert [run["engine"] for run in runs["data"]] == ["batch"]

    body = await _ledger(guest_client, s1)
    assert (body["results"], body["total"]) == (3, 3)
    mzs = [row["sample_peak_mz"] for row in body["data"]]
    assert mzs == sorted(mzs)
    rows = {row["sample_peak_id"]: row for row in body["data"]}

    m0 = rows["p1"]
    assert m0["peak_assignment_run_id"] == fold_run_id(s1)
    assert m0["batch_peak_id"]
    assert m0["peak_assignment_id"] == fold_assignment_id(m0["batch_peak_id"])
    assert m0["assigned_formula"] == "C6H12O6"
    assert m0["ion_formula"] == "C6H13O6+"  # from the anchor's registry
    assert (m0["tier"], m0["role"]) == ("assigned", "M0")
    assert m0["fit_score"] == pytest.approx(0.95)
    assert m0["p_correct"] == pytest.approx(0.91)
    assert m0["sample_peak_intensity"] == pytest.approx(5000.0)
    # A run's numbers, which a member does not carry.
    assert m0["mz_error_ppm"] is None
    # The source is the identity's, recorded when its first member brought it.
    assert m0["source"] == "database"
    assert m0["evidence"] is None

    iso = rows["p2"]
    assert iso["role"] == "iso_child"
    assert iso["owner_peak_assignment_id"] == m0["peak_assignment_id"]

    bare = rows["p3"]
    assert (bare["tier"], bare["role"]) == ("unassigned", "unassigned")
    assert bare["assigned_formula"] is None
    assert bare["owner_peak_assignment_id"] is None


async def test_the_derived_ledger_pages_and_filters(
    guest_client, async_session_factory, folded
):
    await _delete_runs(async_session_factory, folded["s1"], folded["s2"])
    s1 = folded["s1"]

    page = await _ledger(guest_client, s1, limit=1, offset=1)
    assert (page["results"], page["total"]) == (1, 3)
    assert page["data"][0]["sample_peak_id"] == "p2"  # second by m/z

    by_tier = await _ledger(guest_client, s1, tier="unassigned")
    assert by_tier["total"] == 1
    assert by_tier["data"][0]["sample_peak_id"] == "p3"

    by_role = await _ledger(guest_client, s1, role="iso_child")
    assert by_role["total"] == 1
    assert by_role["data"][0]["sample_peak_id"] == "p2"

    # What a member does not carry matches nothing, rather than everything.
    by_source = await _ledger(guest_client, s1, source="database")
    assert (by_source["total"], by_source["data"]) == (0, [])

    explicit = await _ledger(guest_client, s1, peak_assignment_run_id=fold_run_id(s1))
    assert explicit["total"] == 3

    # The derived run's id names its sample; another sample's is not this one's.
    other = await guest_client.get(
        f"/api/peak-assignments/sample/{s1}",
        params={"peak_assignment_run_id": fold_run_id(folded["s2"])},
    )
    assert other.status_code == 404


async def test_the_derived_detail_carries_the_anchors_other_identities(
    guest_client, async_session_factory, folded
):
    await _delete_runs(async_session_factory, folded["s1"], folded["s2"])
    s1 = folded["s1"]
    rows = (await _ledger(guest_client, s1))["data"]
    m0 = next(row for row in rows if row["sample_peak_id"] == "p1")

    response = await guest_client.get(
        f"/api/peak-assignments/sample/{s1}/assignment/{m0['peak_assignment_id']}"
    )
    assert response.status_code == 200, response.text
    detail = response.json()["data"][0]
    assert detail["assigned_formula"] == "C6H12O6"
    assert detail["batch_peak_id"] == m0["batch_peak_id"]
    # What the other sample saw this m/z as, offered as the alternative.
    assert [alt["assigned_formula"] for alt in detail["alternatives"]] == ["C5H12N2O4"]
    assert detail["alternatives"][0]["ion_formula"] == "C5H13N2O4+"
    batch = detail["provenance"]["batch_peak"]
    assert batch["batch_peak_id"] == m0["batch_peak_id"]
    assert batch["n_present"] == 2
    assert batch["consensus_formula"] == "C6H12O6"
    assert detail["provenance"]["p_correct"] == pytest.approx(0.91)

    missing = await guest_client.get(
        f"/api/peak-assignments/sample/{s1}/assignment/"
        f"{fold_assignment_id('no-such-anchor')}"
    )
    assert missing.status_code == 404


async def test_a_derived_row_has_no_alternative_scores_to_measure(
    guest_client, async_session_factory, folded
):
    await _delete_runs(async_session_factory, folded["s1"], folded["s2"])
    s1 = folded["s1"]
    m0 = next(
        row
        for row in (await _ledger(guest_client, s1))["data"]
        if row["sample_peak_id"] == "p1"
    )
    response = await guest_client.get(
        f"/api/peak-assignments/sample/{s1}/assignment/"
        f"{m0['peak_assignment_id']}/alternative-scores"
    )
    assert response.status_code == 200, response.text
    assert (response.json()["results"], response.json()["data"]) == (0, [])


# --- writes ---------------------------------------------------------------------


async def test_a_derived_row_cannot_be_curated(
    editor_client, async_session_factory, folded, assignment_enabled
):
    await _delete_runs(async_session_factory, folded["s1"], folded["s2"])
    s1 = folded["s1"]
    m0 = next(
        row
        for row in (await _ledger(editor_client, s1))["data"]
        if row["sample_peak_id"] == "p1"
    )
    response = await editor_client.patch(
        f"/api/peak-assignments/sample/{s1}/assignment/{m0['peak_assignment_id']}",
        json={"action": "promote_alternative", "alternative_index": 0},
    )
    assert response.status_code == 409, response.text
    assert DERIVED_READ_ONLY_CODE in response.text
    assert "Assign the sample" in response.text


async def test_a_derived_row_can_carry_a_verdict(
    editor_client, async_session_factory, folded, assignment_enabled
):
    await _delete_runs(async_session_factory, folded["s1"], folded["s2"])
    s1 = folded["s1"]
    m0 = next(
        row
        for row in (await _ledger(editor_client, s1))["data"]
        if row["sample_peak_id"] == "p1"
    )
    response = await editor_client.post(
        f"/api/peak-assignments/sample/{s1}/verify",
        json={"peak_assignment_id": m0["peak_assignment_id"], "verdict": "rejected"},
    )
    assert response.status_code == 201, response.text
    record = response.json()["data"][0]
    # Keyed on the peak, snapshotting the member; no row to link to.
    assert record["sample_peak_id"] == "p1"
    assert record["assigned_formula"] == "C6H12O6"
    assert record["verdict"] == "rejected"
    assert record["fit_score"] == pytest.approx(0.95)
    assert record["p_correct"] == pytest.approx(0.91)
    assert record["peak_assignment_id"] is None
    assert record["peak_assignment_run_id"] is None

    listing = await editor_client.get(
        f"/api/peak-assignments/sample/{s1}/verifications"
    )
    assert listing.status_code == 200
    assert [v["sample_peak_id"] for v in listing.json()["data"]] == ["p1"]


# --- the run-less ingest path -----------------------------------------------------


def _memory_rows(sample_item_id, spec):
    """Stage A's output as the run-less ingest fold hands it over: ledger-shaped
    rows that were never written, stamped with the derived run's id."""
    ids = {pid: gen_id(32) for pid, *_ in spec}
    return [
        SimpleNamespace(
            peak_assignment_id=ids[pid],
            peak_assignment_run_id=fold_run_id(sample_item_id),
            sample_item_id=sample_item_id,
            sample_peak_id=pid,
            sample_peak_mz=mz,
            sample_peak_intensity=intensity,
            role=role,
            assigned_formula=formula,
            ion_formula=ion,
            ionization_mechanism_id=None,
            tier=tier,
            fit_score=fit,
            mz_error_ppm=(1.0 if formula else None),
            owner_peak_assignment_id=(ids[owner] if owner else None),
            provenance=({"p_correct": p_correct} if p_correct is not None else None),
        )
        for pid, mz, formula, ion, role, tier, fit, intensity, owner, p_correct in spec
    ]


async def test_a_sample_folded_without_any_run_is_served(
    guest_client, async_session_factory, pa_test_data
):
    """The ingest path that writes no run: the sample never had one, and its
    Sample view comes from the batch ledger from the first read."""
    now = datetime.now(timezone.utc)
    batch_id = gen_id()
    sample_file_id, sample_item_id = gen_id(), gen_id()
    async with async_session_factory() as session:
        dataset_id = (
            await session.execute(
                select(SampleBatch.dataset_id).where(
                    SampleBatch.sample_batch_id == pa_test_data["sample_batch_id"]
                )
            )
        ).scalar_one()
        session.add(
            SampleBatch(
                sample_batch_id=batch_id,
                dataset_id=dataset_id,
                sample_batch_name=f"Fold view runless {batch_id}",
                sample_batch_utc_created=now,
            )
        )
        session.add(
            SampleFile(
                sample_file_id=sample_file_id,
                filename=f"orbi-fold-view-runless-{sample_file_id}.zarr",
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
                sample_batch_id=batch_id,
                sample_file_id=sample_file_id,
                sample_item_name="Fold view runless",
                sample_item_type="sample",
                polarity="+",
                sample_item_utc_created=now,
            )
        )
        await session.commit()

    rows = _memory_rows(sample_item_id, _ROWS["S1"])
    assert (
        await fold_sample_into_batch_peaks(sample_item_id, rows=rows, persisted=False)
        == batch_id
    )

    runs = (
        await guest_client.get(f"/api/peak-assignments/sample/{sample_item_id}/runs")
    ).json()["data"]
    assert [run["engine"] for run in runs] == ["batch"]

    body = await _ledger(guest_client, sample_item_id)
    assert body["total"] == 3
    by_peak = {row["sample_peak_id"]: row for row in body["data"]}
    assert by_peak["p1"]["assigned_formula"] == "C6H12O6"
    assert by_peak["p1"]["ion_formula"] == "C6H13O6+"
    assert by_peak["p1"]["p_correct"] == pytest.approx(0.91)
    assert (
        by_peak["p2"]["owner_peak_assignment_id"] == by_peak["p1"]["peak_assignment_id"]
    )
    assert by_peak["p3"]["tier"] == "unassigned"
