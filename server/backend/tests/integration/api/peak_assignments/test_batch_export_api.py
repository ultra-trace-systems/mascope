"""Integration tests for the ledger's flat rows: the paged members route and
the CSV export.

Two samples in a batch of their own, folded; one species seen in both and one
unassigned peak seen in one. See ``batch_export.py``.
"""

import os
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest
import pytest_asyncio
from sqlalchemy import event, select, update

from mascope_backend.api.new.peak_assignments import batch_peaks, batch_peaks_routes
from mascope_backend.api.new.peak_assignments.batch_export import (
    COLUMNS,
    write_batch_ledger_csv,
)
from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
    fold_sample_into_batch_peaks,
)
from mascope_backend.api.new.temp.storage import user_temp_path
from mascope_backend.db import (
    BatchPeak,
    PeakAssignment,
    PeakAssignmentRun,
    SampleBatch,
    SampleFile,
    SampleItem,
)
from mascope_backend.db.id import gen_id
from mascope_sdk import read_export_csv


pytestmark = pytest.mark.asyncio

# (peak id, mz, formula, ion formula, tier, fit, intensity)
_ROWS = {
    "S1": [
        ("p1", 181.0707, "C6H12O6", "C6H13O6+", "assigned", 0.95, 5000.0),
        ("p3", 200.1234, None, None, "unassigned", None, 300.0),
    ],
    "S2": [("p1", 181.0708, "C6H12O6", "C6H13O6+", "assigned", 0.90, 4000.0)],
    # The TOF sample of the mixed batch: the same species, its intensity an area.
    "T1": [("p1", 181.0709, "C6H12O6", "C6H13O6+", "assigned", 0.85, 90000.0)],
}


async def _seed_sample(session, batch_id, name, rows, now):
    sample_file_id, sample_item_id, run_id = gen_id(), gen_id(), gen_id()
    session.add(
        SampleFile(
            sample_file_id=sample_file_id,
            filename=f"orbi-ledger-export-{name}-{sample_file_id}.zarr",
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
            sample_item_name=f"Ledger export {name}",
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
    for pid, mz, formula, ion, tier, fit, intensity in rows:
        session.add(
            PeakAssignment(
                peak_assignment_id=gen_id(32),
                peak_assignment_run_id=run_id,
                sample_item_id=sample_item_id,
                sample_peak_id=pid,
                sample_peak_mz=mz,
                sample_peak_intensity=intensity,
                role="M0" if formula else "unassigned",
                assigned_formula=formula,
                ion_formula=ion,
                source=("database" if formula else None),
                fit_score=fit,
                mz_error_ppm=(1.0 if formula else None),
                tier=tier,
            )
        )
    return sample_item_id


@pytest_asyncio.fixture
async def ledger(async_session_factory, pa_test_data):
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
                sample_batch_name=f"Ledger export {batch_id}",
                sample_batch_utc_created=now,
            )
        )
        s1 = await _seed_sample(session, batch_id, "S1", _ROWS["S1"], now)
        s2 = await _seed_sample(session, batch_id, "S2", _ROWS["S2"], now)
        await session.commit()
    assert await fold_sample_into_batch_peaks(s1) == batch_id
    assert await fold_sample_into_batch_peaks(s2) == batch_id
    return {"batch_id": batch_id, "s1": s1, "s2": s2}


@pytest_asyncio.fixture
async def mixed_ledger(async_session_factory, pa_test_data, monkeypatch):
    """A batch of one Orbitrap and one TOF sample, the same species in both:
    two batch peaks at the same m/z, one per instrument type's partition."""
    # The fold reads the instrument type off the file name; T1 is the TOF file.
    monkeypatch.setattr(
        batch_peaks,
        "get_instrument_type",
        lambda filename: "tof" if "-T1-" in filename else "orbi",
    )
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
                sample_batch_name=f"Mixed ledger export {batch_id}",
                sample_batch_utc_created=now,
            )
        )
        s1 = await _seed_sample(session, batch_id, "S1", _ROWS["S1"], now)
        t1 = await _seed_sample(session, batch_id, "T1", _ROWS["T1"], now)
        await session.commit()
    assert await fold_sample_into_batch_peaks(s1) == batch_id
    assert await fold_sample_into_batch_peaks(t1) == batch_id
    return {"batch_id": batch_id, "s1": s1, "t1": t1}


def _partitions(rows):
    """(sample, instrument type, unit, formula, intensity) per row, sorted."""
    return sorted(
        (
            r["sample_item_id"],
            r["instrument_type"],
            r["intensity_variable"],
            r["consensus_formula"] or "",
            r["intensity"],
        )
        for r in rows
    )


async def test_the_members_route_names_each_rows_partition(guest_client, mixed_ledger):
    response = await guest_client.get(
        f"/api/batch-peaks/batch/{mixed_ledger['batch_id']}/members"
    )
    assert response.status_code == 200, response.text
    rows = response.json()["data"]
    s1, t1 = mixed_ledger["s1"], mixed_ledger["t1"]
    assert _partitions(rows) == sorted(
        [
            (s1, "orbi", "sum_peak_heights", "C6H12O6", 5000.0),
            (s1, "orbi", "sum_peak_heights", "", 300.0),
            (t1, "tof", "sum_peak_areas", "C6H12O6", 90000.0),
        ]
    )
    # The two glucose rows are two batch peaks, one per partition.
    glucose = [r for r in rows if r["consensus_formula"] == "C6H12O6"]
    assert len({r["batch_peak_id"] for r in glucose}) == 2


async def test_the_export_names_each_rows_partition(mixed_ledger, test_users):
    user_id = test_users["editor"].id
    name, rows = await write_batch_ledger_csv(mixed_ledger["batch_id"], user_id)
    assert rows == 3
    path = user_temp_path(user_id, name, create=False)
    try:
        frame = pd.read_csv(path, sep=";")
        assert list(frame.columns) == list(COLUMNS)
        frame["consensus_formula"] = frame["consensus_formula"].fillna("")
        s1, t1 = mixed_ledger["s1"], mixed_ledger["t1"]
        assert _partitions(frame.to_dict("records")) == sorted(
            [
                (s1, "orbi", "sum_peak_heights", "C6H12O6", 5000.0),
                (s1, "orbi", "sum_peak_heights", "", 300.0),
                (t1, "tof", "sum_peak_areas", "C6H12O6", 90000.0),
            ]
        )
    finally:
        os.remove(path)


async def test_the_members_route_pages_the_flat_ledger(guest_client, ledger):
    first = await guest_client.get(
        f"/api/batch-peaks/batch/{ledger['batch_id']}/members",
        params={"limit": 2, "offset": 0},
    )
    assert first.status_code == 200, first.text
    body = first.json()
    assert (body["total"], body["results"], body["limit"], body["offset"]) == (
        3,
        2,
        2,
        0,
    )
    assert list(body["data"][0]) == list(COLUMNS)
    second = await guest_client.get(
        f"/api/batch-peaks/batch/{ledger['batch_id']}/members",
        params={"limit": 2, "offset": 2},
    )
    rows = body["data"] + second.json()["data"]
    assert len(rows) == 3
    # Anchor m/z order, then sample: the glucose anchor's two members first.
    assert [r["consensus_formula"] for r in rows] == ["C6H12O6", "C6H12O6", None]
    glucose = [r for r in rows if r["consensus_formula"] == "C6H12O6"]
    assert {r["sample_item_id"] for r in glucose} == {ledger["s1"], ledger["s2"]}
    assert {r["sample_item_name"] for r in glucose} == {
        "Ledger export S1",
        "Ledger export S2",
    }
    assert all(
        r["n_present"] == 2 and r["assigned_formula"] == "C6H12O6" for r in glucose
    )
    unassigned = rows[-1]
    assert unassigned["tier"] == "unassigned" and unassigned["assigned_formula"] is None
    assert unassigned["sample_peak_id"] == "p3"


async def _give_glucose_a_verdict(session_factory, batch_id):
    async with session_factory() as session:
        await session.execute(
            update(BatchPeak)
            .where(
                BatchPeak.sample_batch_id == batch_id,
                BatchPeak.consensus_formula == "C6H12O6",
            )
            .values(engine_tier="candidate", engine_claim="the engine's words")
        )
        await session.commit()


async def test_the_members_route_carries_an_imports_verdict(
    guest_client, async_session_factory, ledger
):
    await _give_glucose_a_verdict(async_session_factory, ledger["batch_id"])
    response = await guest_client.get(
        f"/api/batch-peaks/batch/{ledger['batch_id']}/members"
    )
    assert response.status_code == 200, response.text
    rows = response.json()["data"]
    verdicts = [
        (
            r["consensus_formula"],
            r["consensus_tier"],
            r["engine_tier"],
            r["engine_claim"],
        )
        for r in rows
    ]
    assert verdicts == [
        ("C6H12O6", "assigned", "candidate", "the engine's words"),
        ("C6H12O6", "assigned", "candidate", "the engine's words"),
        (None, "unassigned", None, None),
    ]


async def test_the_members_route_narrows_to_a_sample(guest_client, ledger):
    response = await guest_client.get(
        f"/api/batch-peaks/batch/{ledger['batch_id']}/members",
        params={"sample_item_id": ledger["s2"]},
    )
    body = response.json()
    assert body["total"] == 1
    assert [r["sample_item_id"] for r in body["data"]] == [ledger["s2"]]


async def test_the_members_route_caps_the_page(guest_client, ledger):
    response = await guest_client.get(
        f"/api/batch-peaks/batch/{ledger['batch_id']}/members",
        params={"limit": 999999},
    )
    assert response.status_code == 422, response.text


async def test_the_export_writes_the_whole_ledger_as_csv(ledger, test_users):
    user_id = test_users["editor"].id
    name, rows = await write_batch_ledger_csv(ledger["batch_id"], user_id)
    assert rows == 3
    assert name.endswith(".csv") and "batch_ledger" in name
    path = user_temp_path(user_id, name, create=False)
    try:
        frame = pd.read_csv(path, sep=";")
        assert list(frame.columns) == list(COLUMNS)
        assert len(frame) == 3
        assert frame["consensus_formula"].fillna("").tolist() == [
            "C6H12O6",
            "C6H12O6",
            "",
        ]
        assert frame["n_present"].tolist() == [2, 2, 1]
        # No import judged this batch: the engine's columns are there, empty.
        assert frame["engine_tier"].isna().all()
        assert frame["engine_claim"].isna().all()
    finally:
        os.remove(path)


async def test_the_export_writes_an_imports_verdict(
    ledger, test_users, async_session_factory
):
    await _give_glucose_a_verdict(async_session_factory, ledger["batch_id"])
    user_id = test_users["editor"].id
    name, _ = await write_batch_ledger_csv(ledger["batch_id"], user_id)
    path = user_temp_path(user_id, name, create=False)
    try:
        frame = pd.read_csv(path, sep=";")
        assert frame["engine_tier"].fillna("").tolist() == [
            "candidate",
            "candidate",
            "",
        ]
        assert frame["engine_claim"].fillna("").tolist() == [
            "the engine's words",
            "the engine's words",
            "",
        ]
    finally:
        os.remove(path)


async def test_the_export_writes_a_formula_as_text(
    ledger, test_users, async_session_factory
):
    """A claim and a sample name a spreadsheet would run go out behind an
    apostrophe, and the SDK's reader gives them back as they were typed."""
    async with async_session_factory() as session:
        await session.execute(
            update(BatchPeak)
            .where(
                BatchPeak.sample_batch_id == ledger["batch_id"],
                BatchPeak.consensus_formula == "C6H12O6",
            )
            .values(engine_tier="candidate", engine_claim="=1+1")
        )
        await session.execute(
            update(SampleItem)
            .where(SampleItem.sample_item_id == ledger["s2"])
            .values(sample_item_name="@SUM(A1)")
        )
        await session.commit()
    user_id = test_users["editor"].id
    name, _ = await write_batch_ledger_csv(ledger["batch_id"], user_id)
    path = user_temp_path(user_id, name, create=False)
    try:
        raw = pd.read_csv(path, sep=";")
        assert raw["engine_claim"].fillna("").tolist() == ["'=1+1", "'=1+1", ""]
        assert "'@SUM(A1)" in raw["sample_item_name"].tolist()
        assert "Ledger export S1" in raw["sample_item_name"].tolist()
        assert pd.api.types.is_float_dtype(raw["batch_mz"])

        back = read_export_csv(path)
        assert back["engine_claim"].fillna("").tolist() == ["=1+1", "=1+1", ""]
        assert "@SUM(A1)" in back["sample_item_name"].tolist()
    finally:
        os.remove(path)


async def test_the_export_route_launches_for_a_guest(guest_client, ledger, monkeypatch):
    launched = []

    async def fake_task(**kwargs):
        launched.append(kwargs)

    monkeypatch.setattr(batch_peaks_routes, "export_batch_ledger", fake_task)
    response = await guest_client.post(
        f"/api/batch-peaks/batch/{ledger['batch_id']}/export"
    )
    assert response.status_code == 202, response.text
    assert response.headers.get("process-id")
    assert len(launched) == 1
    assert launched[0]["sample_batch_id"] == ledger["batch_id"]


async def _state_levels(session_factory, ledger, *, s2_formula="C6H12O6"):
    """Give each sample's glucose row the level its run stated: 4a on S1, 5a
    on S2, as a run since the identification level writes them; and S2's
    row optionally re-read as another formula, as a member a batch
    operation re-pointed is."""
    async with session_factory() as session:
        for sample, level in ((ledger["s1"], "4a"), (ledger["s2"], "5a")):
            await session.execute(
                update(PeakAssignment)
                .where(
                    PeakAssignment.sample_item_id == sample,
                    PeakAssignment.assigned_formula == "C6H12O6",
                )
                .values(
                    level=level,
                    assigned_formula=(
                        s2_formula if sample == ledger["s2"] else "C6H12O6"
                    ),
                    provenance={
                        "identification_level": {
                            "level": level,
                            "evidence": f"evidence of {level}",
                            "lift": f"lift of {level}",
                        }
                    },
                )
            )
        await session.commit()


async def test_the_members_route_carries_each_members_level(
    guest_client, async_session_factory, ledger
):
    await _state_levels(async_session_factory, ledger)

    response = await guest_client.get(
        f"/api/batch-peaks/batch/{ledger['batch_id']}/members"
    )

    assert response.status_code == 200, response.text
    by_sample = {
        (r["sample_item_id"], r["sample_peak_id"]): (
            r["level"],
            r["level_evidence"],
            r["level_lift"],
        )
        for r in response.json()["data"]
    }
    assert by_sample == {
        (ledger["s1"], "p1"): ("4a", "evidence of 4a", "lift of 4a"),
        (ledger["s2"], "p1"): ("5a", "evidence of 5a", "lift of 5a"),
        (ledger["s1"], "p3"): (None, None, None),
    }


async def test_a_members_level_about_another_formula_is_not_served(
    guest_client, async_session_factory, ledger
):
    # The run's row now commits another formula than the member reads: its
    # level is about that formula, so the member shows none.
    await _state_levels(async_session_factory, ledger, s2_formula="C6H12O5")

    rows = (
        await guest_client.get(f"/api/batch-peaks/batch/{ledger['batch_id']}/members")
    ).json()["data"]

    s2 = next(r for r in rows if r["sample_item_id"] == ledger["s2"])
    assert (s2["level"], s2["level_evidence"], s2["level_lift"]) == (None, None, None)


async def test_the_export_writes_each_members_level(
    async_session_factory, ledger, test_users
):
    await _state_levels(async_session_factory, ledger)
    user_id = test_users["editor"].id

    name, _ = await write_batch_ledger_csv(ledger["batch_id"], user_id)

    path = user_temp_path(user_id, name, create=False)
    try:
        frame = pd.read_csv(path, sep=";")
        levels = dict(
            zip(
                frame["sample_item_id"] + "/" + frame["sample_peak_id"],
                frame["level"].fillna(""),
            )
        )
        assert levels == {
            f"{ledger['s1']}/p1": "4a",
            f"{ledger['s2']}/p1": "5a",
            f"{ledger['s1']}/p3": "",
        }
        assert set(frame["level_lift"].dropna()) == {"lift of 4a", "lift of 5a"}
    finally:
        os.remove(path)


async def test_the_ledger_serves_the_levels_its_members_stated(
    guest_client, async_session_factory, ledger
):
    await _state_levels(async_session_factory, ledger)

    rows = (
        await guest_client.get(f"/api/batch-peaks/batch/{ledger['batch_id']}")
    ).json()["data"]

    [glucose] = [r for r in rows if r["consensus_formula"] == "C6H12O6"]
    assert glucose["member_levels"] == {"4a": 1, "5a": 1}
    # A tie reads as the weaker level: never more than the members stated.
    assert glucose["member_level"] == "5a"


async def test_the_ledger_counts_only_members_whose_run_commits_its_formula(
    guest_client, async_session_factory, ledger
):
    await _state_levels(async_session_factory, ledger, s2_formula="C6H12O5")

    rows = (
        await guest_client.get(f"/api/batch-peaks/batch/{ledger['batch_id']}")
    ).json()["data"]

    [glucose] = [r for r in rows if r["consensus_formula"] == "C6H12O6"]
    assert glucose["member_levels"] == {"4a": 1}
    assert glucose["member_level"] == "4a"


async def test_a_ledger_without_levels_serves_none(guest_client, ledger):
    rows = (
        await guest_client.get(f"/api/batch-peaks/batch/{ledger['batch_id']}")
    ).json()["data"]

    assert all(r["member_level"] is None and r["member_levels"] is None for r in rows)


def _member_level_reads(async_engine):
    """Collect the grouped member-level reads sent while the block runs, each
    as its parameters: the read is the one over the occurrence table that
    groups by level. A ``plan_cache_mode`` setting sent is collected too, as
    the statement itself, so its place around the read shows."""
    reads: list = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        if "plan_cache_mode" in statement:
            reads.append(statement)
        elif "batch_peak_occurrence" in statement and "GROUP BY" in statement:
            if "peak_assignment.level" in statement.split("GROUP BY")[1]:
                reads.append(tuple(parameters))

    return reads, capture


async def test_a_filtered_ledger_reads_only_its_rows_members(
    guest_client, async_session_factory, async_engine, ledger
):
    await _state_levels(async_session_factory, ledger)
    reads, capture = _member_level_reads(async_engine)

    event.listen(async_engine.sync_engine, "before_cursor_execute", capture)
    try:
        # The default occupancy floor of 2 keeps glucose and leaves out the
        # unassigned peak seen in one sample.
        response = await guest_client.get(
            f"/api/batch-peaks/batch/{ledger['batch_id']}"
        )
    finally:
        event.remove(async_engine.sync_engine, "before_cursor_execute", capture)

    assert response.status_code == 200, response.text
    [glucose] = response.json()["data"]
    assert glucose["member_levels"] == {"4a": 1, "5a": 1}
    async with async_session_factory() as session:
        whole = (
            (
                await session.execute(
                    select(BatchPeak.batch_peak_id).where(
                        BatchPeak.sample_batch_id == ledger["batch_id"]
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(whole) == 2
    # One read, over the served anchor's members alone, not the batch's, and
    # planned for those ids: a generic plan cannot see how many they are.
    [custom, read, back] = reads
    assert custom == "SET LOCAL plan_cache_mode = force_custom_plan"
    assert [p for p in read if isinstance(p, list)] == [[glucose["batch_peak_id"]]]
    assert back == "SET LOCAL plan_cache_mode = DEFAULT"


async def test_a_ledger_that_serves_no_row_reads_no_members(
    guest_client, async_session_factory, async_engine, ledger
):
    await _state_levels(async_session_factory, ledger)
    reads, capture = _member_level_reads(async_engine)

    event.listen(async_engine.sync_engine, "before_cursor_execute", capture)
    try:
        response = await guest_client.get(
            f"/api/batch-peaks/batch/{ledger['batch_id']}",
            # No anchor of this batch reads as a candidate.
            params={"tier": "candidate"},
        )
    finally:
        event.remove(async_engine.sync_engine, "before_cursor_execute", capture)

    assert response.status_code == 200, response.text
    assert response.json()["data"] == []
    assert reads == []


async def test_the_members_route_carries_an_imports_level(
    guest_client, async_session_factory, ledger
):
    async with async_session_factory() as session:
        await session.execute(
            update(BatchPeak)
            .where(
                BatchPeak.sample_batch_id == ledger["batch_id"],
                BatchPeak.consensus_formula == "C6H12O6",
            )
            .values(engine_tier="candidate", engine_level="4b", engine_evidence="why")
        )
        await session.commit()

    members = (
        await guest_client.get(f"/api/batch-peaks/batch/{ledger['batch_id']}/members")
    ).json()["data"]
    listed = (
        await guest_client.get(f"/api/batch-peaks/batch/{ledger['batch_id']}")
    ).json()["data"]

    assert [r["engine_level"] for r in members] == ["4b", "4b", None]
    assert [r["engine_evidence"] for r in members] == ["why", "why", None]
    assert {r["consensus_formula"]: r["engine_level"] for r in listed} == {
        "C6H12O6": "4b"
    }
