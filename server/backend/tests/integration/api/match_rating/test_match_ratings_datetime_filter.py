"""
Integration tests: ``GET /api/match_ratings`` filtered by creation time.

``datetime_min`` and ``datetime_max`` bound
``match_rating.match_rating_utc_created``, a ``timestamptz``, inclusive at
both ends. A bound sent without an offset once reached the driver naive, and
asyncpg reads a naive value in the backend process's local time zone: the
same request listed other ratings on a server whose ``TZ`` is not UTC.

Run against the real route and a real database, because the reading is the
driver's.
"""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete

from mascope_backend.db import (
    Dataset,
    IonizationMechanism,
    IonizationMode,
    MatchRating,
    SampleBatch,
    SampleFile,
    SampleItem,
    TargetCompound,
    TargetIon,
    Workspace,
)
from mascope_backend.db.id import gen_id


_NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)

#: Each seeded rating's creation time, by the name the tests know it under.
_CREATED = {
    "early": _NOW - timedelta(days=2),
    "middle": _NOW,
    "late": _NOW + timedelta(days=2),
}


@pytest_asyncio.fixture
async def seeded(async_session_factory):
    """One sample item and target ion, rated once at each creation time.

    Read as the owner, a superuser, so the route's workspace check passes
    without a membership."""
    workspace, dataset, batch, file, item = (gen_id() for _ in range(5))
    mechanism, mode, compound, ion = (gen_id() for _ in range(4))
    ratings = {name: gen_id(32) for name in _CREATED}
    async with async_session_factory() as session:
        session.add(
            Workspace(
                workspace_id=workspace,
                workspace_name=f"Rating WS {workspace}",
                workspace_description="Match rating filter test workspace",
                workspace_status="active",
                workspace_utc_created=_NOW,
                workspace_utc_modified=_NOW,
            )
        )
        session.add(
            Dataset(
                dataset_id=dataset,
                workspace_id=workspace,
                dataset_name=f"Rating DS {dataset}",
                dataset_description="Match rating filter test dataset",
                dataset_type="ANALYSIS",
                dataset_utc_created=_NOW,
            )
        )
        session.add(
            SampleBatch(
                sample_batch_id=batch,
                dataset_id=dataset,
                sample_batch_name="Rating Batch",
                sample_batch_utc_created=_NOW,
            )
        )
        session.add(
            SampleFile(
                sample_file_id=file,
                filename=f"rating-filter_{file}.raw",
                instrument="rating-filter",
                instrument_type="orbi",
                datetime=_NOW.replace(tzinfo=None),
                datetime_utc=_NOW,
                length=60.0,
                range={"min": 0, "max": 500},
                polarity="+",
            )
        )
        session.add(
            IonizationMechanism(
                ionization_mechanism_id=mechanism,
                ionization_mechanism_polarity="+",
                ionization_mechanism=f"[M+H]+ {mechanism}",
            )
        )
        session.add(
            IonizationMode(
                ionization_mode_id=mode,
                ionization_mode_name="Rating Mode",
                ionization_mode_polarity="+",
                ionization_mechanism_ids=[mechanism],
            )
        )
        session.add(
            SampleItem(
                sample_item_id=item,
                sample_batch_id=batch,
                sample_file_id=file,
                ionization_mode_id=mode,
                sample_item_name="Rating Item",
                sample_item_type="ANALYSIS",
                sample_item_attributes={},
                polarity="+",
                tic=1000.0,
                t0=0.0,
                t1=60.0,
                sample_item_utc_created=_NOW,
            )
        )
        session.add(
            TargetCompound(
                target_compound_id=compound,
                target_compound_name=f"Rating Compound {compound}",
                target_compound_formula="C6H12O6",
            )
        )
        session.add(
            TargetIon(
                target_ion_id=ion,
                target_compound_id=compound,
                ionization_mechanism_id=mechanism,
                target_ion_formula="C6H13O6+",
            )
        )
        await session.flush()
        for name, created in _CREATED.items():
            session.add(
                MatchRating(
                    match_rating_id=ratings[name],
                    sample_item_id=item,
                    target_ion_id=ion,
                    match_rating_utc_created=created,
                    rating=1,
                )
            )
        await session.commit()
    yield {"item": item, "ratings": ratings}
    async with async_session_factory() as session:
        # The workspace takes its dataset, batch, item and ratings with it,
        # and the compound its ion.
        await session.execute(
            delete(Workspace).where(Workspace.workspace_id == workspace)
        )
        await session.execute(
            delete(SampleFile).where(SampleFile.sample_file_id == file)
        )
        await session.execute(
            delete(TargetCompound).where(TargetCompound.target_compound_id == compound)
        )
        await session.execute(
            delete(IonizationMode).where(IonizationMode.ionization_mode_id == mode)
        )
        await session.execute(
            delete(IonizationMechanism).where(
                IonizationMechanism.ionization_mechanism_id == mechanism
            )
        )
        await session.commit()


async def _listed(client, seeded: dict, **bounds) -> set[str]:
    """The names of the seeded ratings the route answers with."""
    response = await client.get(
        "/api/match_ratings",
        params={"sample_item_id": seeded["item"]}
        | {key: value.isoformat() for key, value in bounds.items()},
    )
    assert response.status_code == 200, response.text
    by_id = {rating_id: name for name, rating_id in seeded["ratings"].items()}
    return {by_id[row["match_rating_id"]] for row in response.json()["data"]}


@pytest.mark.asyncio
async def test_a_window_lists_the_ratings_inside_it(owner_client, seeded):
    listed = await _listed(
        owner_client,
        seeded,
        datetime_min=_NOW - timedelta(days=1),
        datetime_max=_NOW + timedelta(days=1),
    )

    assert listed == {"middle"}


@pytest.mark.asyncio
async def test_a_bound_in_another_offset_is_the_same_instant(owner_client, seeded):
    """A bound written in UTC+3 means the same moment as its UTC spelling."""
    eastern = timezone(timedelta(hours=3))
    listed = await _listed(owner_client, seeded, datetime_min=_NOW.astimezone(eastern))

    assert listed == {"middle", "late"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("bound", "zone", "expected"),
    [
        # Read in UTC-3, the lower bound would fall after the middle rating.
        ("datetime_min", "Etc/GMT+3", {"middle", "late"}),
        # Read in UTC+3, the upper bound would fall before it.
        ("datetime_max", "Etc/GMT-3", {"early", "middle"}),
    ],
)
async def test_a_bound_with_no_offset_is_read_as_utc(
    owner_client, seeded, server_zone, bound, zone, expected
):
    """The column is UTC, so a bound written without an offset is too, not
    the server's local time."""
    server_zone(zone)
    listed = await _listed(owner_client, seeded, **{bound: _NOW.replace(tzinfo=None)})

    assert listed == expected
