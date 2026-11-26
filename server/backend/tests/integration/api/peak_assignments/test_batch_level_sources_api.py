"""Integration tests for the read behind a batch ledger row's level: one batch
peak's members with the levels their runs stated (``batch_level_sources``).

Seven samples in a batch of their own, each with one glucose peak, folded into
one batch peak. Then each member's run row is put in one state: a level of
its own, another formula, no formula, no level, or no row at all.
"""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update

from mascope_backend.api.new.auth.config import auth_settings
from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
    fold_sample_into_batch_peaks,
)
from mascope_backend.app.fast import fast
from mascope_backend.db import (
    BatchPeak,
    BatchPeakOccurrence,
    PeakAssignment,
    PeakAssignmentRun,
    SampleBatch,
    SampleFile,
    SampleItem,
    User,
)
from mascope_backend.db.id import gen_id


pytestmark = pytest.mark.asyncio

GLUCOSE = "C6H12O6"

# Sample -> (intensity, what its run row says about the member).
_MEMBERS = {
    "S1": (3000.0, ("level", "4a")),
    "S2": (9000.0, ("level", "5a")),
    "S3": (7000.0, ("level", "5a")),
    "S4": (8000.0, ("formula", "C6H12O5")),
    "S5": (6000.0, ("formula", None)),
    "S6": (5000.0, ("level", None)),
    "S7": (4000.0, ("no_run", None)),
}


async def _seed_sample(session, batch_id, name, intensity, at):
    sample_file_id, sample_item_id, run_id = gen_id(), gen_id(), gen_id()
    session.add(
        SampleFile(
            sample_file_id=sample_file_id,
            filename=f"orbi-level-sources-{name}-{sample_file_id}.zarr",
            instrument="orbi-test",
            datetime=at.replace(tzinfo=None),
            datetime_utc=at,
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
            sample_item_name=f"Level sources {name}",
            sample_item_type="sample",
            polarity="+",
            sample_item_utc_created=at,
        )
    )
    session.add(
        PeakAssignmentRun(
            peak_assignment_run_id=run_id,
            sample_item_id=sample_item_id,
            engine_version="0.1.0-test",
            status="completed",
            peak_assignment_run_utc_created=at,
            peak_assignment_run_utc_completed=at + timedelta(minutes=5),
        )
    )
    session.add(
        PeakAssignment(
            peak_assignment_id=gen_id(32),
            peak_assignment_run_id=run_id,
            sample_item_id=sample_item_id,
            sample_peak_id="p1",
            sample_peak_mz=181.0707,
            sample_peak_intensity=intensity,
            role="M0",
            assigned_formula=GLUCOSE,
            ion_formula="C6H13O6+",
            source="database",
            fit_score=0.9,
            mz_error_ppm=1.0,
            tier="assigned",
        )
    )
    return sample_item_id


async def _put_in_state(session, sample_item_id, state):
    """Give a member's run row the state the table names."""
    kind, value = state
    rows = update(PeakAssignment).where(PeakAssignment.sample_item_id == sample_item_id)
    if kind == "level":
        block = (
            {
                "level": value,
                "evidence": f"evidence of {value}",
                "lift": f"lift of {value}",
            }
            if value
            else {}
        )
        await session.execute(
            rows.values(level=value, provenance={"identification_level": block})
        )
    elif kind == "formula":
        await session.execute(rows.values(assigned_formula=value, level="4a"))
    else:
        await session.execute(
            update(BatchPeakOccurrence)
            .where(BatchPeakOccurrence.sample_item_id == sample_item_id)
            .values(peak_assignment_id=None)
        )


@pytest_asyncio.fixture
async def sources(async_session_factory, pa_test_data):
    start = datetime(2026, 7, 4, 12, 0, 0, tzinfo=timezone.utc)
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
                sample_batch_name=f"Level sources {batch_id}",
                sample_batch_utc_created=start,
            )
        )
        samples = {}
        for index, (name, (intensity, _)) in enumerate(_MEMBERS.items()):
            samples[name] = await _seed_sample(
                session, batch_id, name, intensity, start + timedelta(hours=index)
            )
        await session.commit()
    for sample_item_id in samples.values():
        assert await fold_sample_into_batch_peaks(sample_item_id) == batch_id
    async with async_session_factory() as session:
        for name, (_, state) in _MEMBERS.items():
            await _put_in_state(session, samples[name], state)
        await session.commit()
        [batch_peak_id] = (
            (
                await session.execute(
                    select(BatchPeak.batch_peak_id).where(
                        BatchPeak.sample_batch_id == batch_id
                    )
                )
            )
            .scalars()
            .all()
        )
    return {"batch_id": batch_id, "batch_peak_id": batch_peak_id, "samples": samples}


def _url(sources, batch_peak_id=None):
    return (
        f"/api/batch-peaks/batch/{sources['batch_id']}/levels/"
        f"{batch_peak_id or sources['batch_peak_id']}"
    )


async def test_it_lists_the_members_stating_a_level_brightest_first(
    guest_client, sources
):
    response = await guest_client.get(_url(sources))

    assert response.status_code == 200, response.text
    body = response.json()
    names = {v: k for k, v in sources["samples"].items()}
    assert [
        (
            names[m["sample_item_id"]],
            m["level"],
            m["level_evidence"],
            m["level_lift"],
            m["intensity"],
        )
        for m in body["data"]
    ] == [
        ("S2", "5a", "evidence of 5a", "lift of 5a", 9000.0),
        ("S3", "5a", "evidence of 5a", "lift of 5a", 7000.0),
        ("S1", "4a", "evidence of 4a", "lift of 4a", 3000.0),
    ]
    s2 = body["data"][0]
    assert s2["sample_item_name"] == "Level sources S2"
    assert s2["sample_peak_id"] == "p1"
    assert s2["peak_assignment_id"]
    assert s2["datetime_utc"].startswith("2026-07-04T13:00:00")
    assert body["results"] == 3
    assert body["consensus_formula"] == GLUCOSE


async def test_it_counts_the_members_stating_none_by_why(guest_client, sources):
    body = (await guest_client.get(_url(sources))).json()

    assert body["n_members"] == 7
    assert body["unstated"] == {
        "no_run": 1,
        "no_formula": 1,
        "other_formula": 1,
        "no_level": 1,
    }


async def test_its_split_is_the_ledgers(guest_client, sources):
    body = (await guest_client.get(_url(sources))).json()
    [row] = (
        await guest_client.get(f"/api/batch-peaks/batch/{sources['batch_id']}")
    ).json()["data"]

    assert body["member_levels"] == {"4a": 1, "5a": 2} == row["member_levels"]
    assert body["member_level"] == "5a" == row["member_level"]


async def test_an_unknown_batch_peak_is_not_found(guest_client, sources):
    response = await guest_client.get(_url(sources, batch_peak_id=gen_id()))

    assert response.status_code == 404, response.text


async def test_a_batch_peak_asked_under_another_batch_is_not_found(
    guest_client, sources, pa_test_data
):
    # The reader may read the other batch; the batch peak is not in it.
    response = await guest_client.get(
        f"/api/batch-peaks/batch/{pa_test_data['sample_batch_id']}/levels/"
        f"{sources['batch_peak_id']}"
    )

    assert response.status_code == 404, response.text


@pytest_asyncio.fixture
async def outsider_client(async_session_factory, roles, create_jwt_auth_token):
    """A signed-in user who is a member of no workspace."""
    async with async_session_factory() as session:
        user = User(
            email=f"outsider-{gen_id()}@test.com",
            username=f"outsider_{gen_id()}",
            hashed_password="123456",
            is_active=True,
            is_verified=False,
            role_id=roles["editor"].role_id,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
    async with AsyncClient(
        transport=ASGITransport(app=fast),
        base_url="http://test",
        cookies={auth_settings.COOKIE_NAME: create_jwt_auth_token(user)},
    ) as client:
        yield client


async def test_a_reader_without_access_to_the_batch_is_refused(
    outsider_client, sources
):
    response = await outsider_client.get(_url(sources))

    assert response.status_code == 403, response.text
