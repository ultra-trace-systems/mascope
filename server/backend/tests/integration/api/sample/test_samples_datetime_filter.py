"""
Integration tests: ``GET /api/samples`` filtered by acquisition time.

``datetime_min`` and ``datetime_max`` bound ``sample_view.datetime_utc``,
inclusive at both ends, and either works on its own. The filter once called
SQLite's ``julianday()``, which Postgres does not have: a window answered
500, and one bound alone was ignored.

Run against the real route and a real database, because the finding was the
SQL itself.
"""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete, text

from mascope_backend.db import (
    Dataset,
    IonizationMode,
    Sample,
    SampleBatch,
    SampleFile,
    SampleItem,
    Workspace,
    WorkspaceMember,
)
from mascope_backend.db.id import gen_id


_NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)

#: Each seeded sample's acquisition time, by the name the tests know it under.
_ACQUIRED = {
    "early": _NOW - timedelta(days=2),
    "middle": _NOW,
    "late": _NOW + timedelta(days=2),
}


@pytest_asyncio.fixture(scope="module", autouse=True)
async def datetime_sample_view(async_engine):
    """``sample_view``, which ``Base.metadata.create_all`` does not create."""
    async with async_engine.begin() as conn:
        await conn.execute(text(Sample.drop_view()))
        await conn.execute(text(Sample.create_view()))


@pytest_asyncio.fixture
async def seeded(async_session_factory, test_users):
    """A batch the editor may read, one sample per acquisition time."""
    ids = {
        name: gen_id(16)
        for name in ("workspace", "dataset", "batch", "mode", *_ACQUIRED)
    }
    files = {name: gen_id(16) for name in _ACQUIRED}
    async with async_session_factory() as session:
        session.add(
            Workspace(
                workspace_id=ids["workspace"],
                workspace_name=f"Datetime filter {ids['workspace']}",
                workspace_status="active",
            )
        )
        await session.flush()
        session.add(
            WorkspaceMember(
                workspace_member_id=gen_id(16),
                workspace_id=ids["workspace"],
                user_id=test_users["editor"].id,
                workspace_role="editor",
            )
        )
        session.add(
            Dataset(
                dataset_id=ids["dataset"],
                workspace_id=ids["workspace"],
                dataset_name="Datetime filter",
                dataset_type="ANALYSIS",
                dataset_utc_created=_NOW,
            )
        )
        session.add(
            SampleBatch(
                sample_batch_id=ids["batch"],
                dataset_id=ids["dataset"],
                sample_batch_name="Datetime filter",
                sample_batch_utc_created=_NOW,
            )
        )
        session.add(
            IonizationMode(
                ionization_mode_id=ids["mode"],
                ionization_mode_name=f"Datetime filter {ids['mode']}",
                ionization_mode_token=None,
                ionization_mode_polarity="-",
                ionization_mechanism_ids=[],
            )
        )
        for name, acquired in _ACQUIRED.items():
            session.add(
                SampleFile(
                    sample_file_id=files[name],
                    filename=f"datetime-filter_{files[name]}",
                    instrument="datetime-filter",
                    instrument_type="orbi",
                    datetime=acquired.replace(tzinfo=None),
                    datetime_utc=acquired,
                    length=60.0,
                    range=[100.0, 500.0],
                    polarity="-",
                )
            )
        await session.flush()
        for name in _ACQUIRED:
            session.add(
                SampleItem(
                    sample_item_id=ids[name],
                    sample_batch_id=ids["batch"],
                    sample_file_id=files[name],
                    sample_item_name=f"Datetime filter {name}",
                    sample_item_type="SAMPLE",
                    sample_item_attributes={},
                    polarity="-",
                    ionization_mode_id=ids["mode"],
                    tic=1000.0,
                    t0=0.0,
                    t1=60.0,
                    sample_item_utc_created=_NOW,
                )
            )
        await session.commit()
    yield ids
    async with async_session_factory() as session:
        # The workspace takes its dataset, batch and items with it.
        await session.execute(
            delete(Workspace).where(Workspace.workspace_id == ids["workspace"])
        )
        await session.execute(
            delete(SampleFile).where(SampleFile.sample_file_id.in_(files.values()))
        )
        await session.execute(
            delete(IonizationMode).where(
                IonizationMode.ionization_mode_id == ids["mode"]
            )
        )
        await session.commit()


async def _listed(client, ids: dict, **bounds) -> set[str]:
    """The names of the batch's samples the route answers with."""
    response = await client.get(
        "/api/samples",
        params={"sample_batch_id": ids["batch"]}
        | {key: value.isoformat() for key, value in bounds.items()},
    )
    assert response.status_code == 200, response.text
    by_id = {ids[name]: name for name in _ACQUIRED}
    return {by_id[row["sample_item_id"]] for row in response.json()["data"]}


@pytest.mark.asyncio
async def test_no_bound_lists_every_sample(editor_client, seeded):
    assert await _listed(editor_client, seeded) == {"early", "middle", "late"}


@pytest.mark.asyncio
async def test_a_window_lists_the_samples_inside_it(editor_client, seeded):
    listed = await _listed(
        editor_client,
        seeded,
        datetime_min=_NOW - timedelta(days=1),
        datetime_max=_NOW + timedelta(days=1),
    )

    assert listed == {"middle"}


@pytest.mark.asyncio
async def test_both_bounds_are_inclusive(editor_client, seeded):
    listed = await _listed(
        editor_client,
        seeded,
        datetime_min=_ACQUIRED["middle"],
        datetime_max=_ACQUIRED["middle"],
    )

    assert listed == {"middle"}


@pytest.mark.asyncio
async def test_a_lower_bound_alone_filters(editor_client, seeded):
    listed = await _listed(editor_client, seeded, datetime_min=_NOW)

    assert listed == {"middle", "late"}


@pytest.mark.asyncio
async def test_an_upper_bound_alone_filters(editor_client, seeded):
    listed = await _listed(editor_client, seeded, datetime_max=_NOW)

    assert listed == {"early", "middle"}


@pytest.mark.asyncio
async def test_a_bound_in_another_offset_is_the_same_instant(editor_client, seeded):
    """``datetime_utc`` is an instant: a bound written in UTC+3 means the
    same moment as its UTC spelling."""
    eastern = timezone(timedelta(hours=3))
    listed = await _listed(editor_client, seeded, datetime_min=_NOW.astimezone(eastern))

    assert listed == {"middle", "late"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("bound", "zone", "expected"),
    [
        # Read in UTC-3, the lower bound would fall after the middle sample.
        ("datetime_min", "Etc/GMT+3", {"middle", "late"}),
        # Read in UTC+3, the upper bound would fall before it.
        ("datetime_max", "Etc/GMT-3", {"early", "middle"}),
    ],
)
async def test_a_bound_with_no_offset_is_read_as_utc(
    editor_client, seeded, server_zone, bound, zone, expected
):
    """The column is UTC, so a bound written without an offset is too, not
    the server's local time."""
    server_zone(zone)
    listed = await _listed(editor_client, seeded, **{bound: _NOW.replace(tzinfo=None)})

    assert listed == expected
