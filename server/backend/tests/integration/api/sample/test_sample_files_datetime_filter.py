"""
Integration tests: ``GET /api/sample/files`` filtered by acquisition time.

``datetime_min`` and ``datetime_max`` bound ``sample_file.datetime_utc``, a
``timestamptz``, inclusive at both ends. A bound sent without an offset once
reached the driver naive, and asyncpg reads a naive value in the backend
process's local time zone: the same request listed other files on a server
whose ``TZ`` is not UTC.

Run against the real route and a real database, because the reading is the
driver's.
"""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete

from mascope_backend.db import SampleFile
from mascope_backend.db.id import gen_id


_NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)

#: Each seeded file's acquisition time, by the name the tests know it under.
_ACQUIRED = {
    "early": _NOW - timedelta(days=2),
    "middle": _NOW,
    "late": _NOW + timedelta(days=2),
}


@pytest_asyncio.fixture
async def seeded(async_session_factory):
    """One file per acquisition time, from an instrument of their own."""
    instrument = f"datetime-filter-{gen_id(8)}"
    files = {name: gen_id(16) for name in _ACQUIRED}
    async with async_session_factory() as session:
        for name, acquired in _ACQUIRED.items():
            session.add(
                SampleFile(
                    sample_file_id=files[name],
                    filename=f"datetime-filter_{files[name]}",
                    instrument=instrument,
                    instrument_type="orbi",
                    datetime=acquired.replace(tzinfo=None),
                    datetime_utc=acquired,
                    length=60.0,
                    range=[100.0, 500.0],
                    polarity="-",
                )
            )
        await session.commit()
    yield {"instrument": instrument, "files": files}
    async with async_session_factory() as session:
        await session.execute(
            delete(SampleFile).where(SampleFile.sample_file_id.in_(files.values()))
        )
        await session.commit()


async def _listed(client, seeded: dict, **bounds) -> set[str]:
    """The names of the seeded files the route answers with."""
    response = await client.get(
        "/api/sample/files",
        params={"instrument": seeded["instrument"]}
        | {key: value.isoformat() for key, value in bounds.items()},
    )
    assert response.status_code == 200, response.text
    by_id = {file_id: name for name, file_id in seeded["files"].items()}
    return {by_id[row["sample_file_id"]] for row in response.json()["data"]}


@pytest.mark.asyncio
async def test_a_window_lists_the_files_inside_it(admin_client, seeded):
    listed = await _listed(
        admin_client,
        seeded,
        datetime_min=_NOW - timedelta(days=1),
        datetime_max=_NOW + timedelta(days=1),
    )

    assert listed == {"middle"}


@pytest.mark.asyncio
async def test_a_bound_in_another_offset_is_the_same_instant(admin_client, seeded):
    """A bound written in UTC+3 means the same moment as its UTC spelling."""
    eastern = timezone(timedelta(hours=3))
    listed = await _listed(admin_client, seeded, datetime_min=_NOW.astimezone(eastern))

    assert listed == {"middle", "late"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("bound", "zone", "expected"),
    [
        # Read in UTC-3, the lower bound would fall after the middle file.
        ("datetime_min", "Etc/GMT+3", {"middle", "late"}),
        # Read in UTC+3, the upper bound would fall before it.
        ("datetime_max", "Etc/GMT-3", {"early", "middle"}),
    ],
)
async def test_a_bound_with_no_offset_is_read_as_utc(
    admin_client, seeded, server_zone, bound, zone, expected
):
    """The column is UTC, so a bound written without an offset is too, not
    the server's local time."""
    server_zone(zone)
    listed = await _listed(admin_client, seeded, **{bound: _NOW.replace(tzinfo=None)})

    assert listed == expected
