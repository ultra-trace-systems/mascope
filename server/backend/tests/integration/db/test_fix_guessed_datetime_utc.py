"""
Integration tests for the repair of acquisition times guessed as UTC.

A file uploaded without a reported zone had its wall clock read in the
converter's own zone, UTC in production, so at a site in another zone it was
stored with a zero offset. The repair reads such a row's wall clock in the
zone the operator names and writes the UTC instant that gives, as the
converter would have had the upload reported the zone. These tests pin what
it selects, what it writes, what it leaves alone, and that a dry run writes
nothing.

Each test's rows carry an instrument of their own and are deleted after it,
so the suite's shared database is left as it was found.
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from mascope_backend.db import SampleFile
from mascope_backend.db.admin.fix_guessed_datetime_utc import (
    fix_guessed_datetime_utc,
    wall_clock_as_utc,
)
from mascope_backend.file_converter.base_processor import resolve_wall_time_offset


#: Names an instrument of its own and resolves to a known instrument type.
INSTRUMENT = "repair-orbi-guess"
#: A second instrument, to pin that the instrument filter holds.
OTHER_INSTRUMENT = "repair-orbi-other"

ZONE = "Europe/Vienna"
UTC = timezone.utc


@pytest_asyncio.fixture(autouse=True)
async def clean_rows(async_session_factory):
    """Remove the files these tests insert, before and after each one."""

    async def _sweep():
        async with async_session_factory() as session:
            await session.execute(
                delete(SampleFile).where(
                    SampleFile.instrument.in_([INSTRUMENT, OTHER_INSTRUMENT])
                )
            )
            await session.commit()

    await _sweep()
    yield
    await _sweep()


async def _insert(
    async_session_factory,
    *,
    key: str,
    local: datetime,
    stored_utc: datetime,
    source: str | None = "guess",
    zone: str | None = None,
    instrument: str = INSTRUMENT,
) -> str:
    """One sample_file row as the converter would have written it.

    :param key: Distinguishes the row within the test; part of its id and name.
    :param local: The wall clock as the instrument recorded it, naive.
    :param stored_utc: What the converter stored as the UTC instant.
    :param source: The row's ``utc_offset_source``.
    :param zone: The row's ``acquisition_timezone``.
    :param instrument: The instrument the row is filed under.
    :return: The row's id.
    """
    sample_file_id = f"repair{key:0>10}"[:16]
    async with async_session_factory() as session:
        session.add(
            SampleFile(
                sample_file_id=sample_file_id,
                filename=f"{instrument}_{key}_NO3.raw",
                instrument=instrument,
                instrument_type="orbi",
                datetime=local,
                datetime_utc=stored_utc,
                length=60.0,
                range=[50.0, 500.0],
                polarity="-",
                utc_offset_source=source,
                acquisition_timezone=zone,
            )
        )
        await session.commit()
    return sample_file_id


async def _stored_utc(async_session_factory) -> dict[str, datetime]:
    """Every test row's ``datetime_utc``, by id."""
    async with async_session_factory() as session:
        rows = await session.execute(
            select(SampleFile.sample_file_id, SampleFile.datetime_utc).where(
                SampleFile.instrument.in_([INSTRUMENT, OTHER_INSTRUMENT])
            )
        )
        return {sample_file_id: stored for sample_file_id, stored in rows}


@pytest.mark.asyncio
async def test_a_wall_clock_stored_as_utc_is_read_in_the_zone(async_session_factory):
    """Summer and winter rows each get the zone's offset at their own instant."""
    summer = datetime(2026, 7, 1, 14, 0)
    winter = datetime(2026, 1, 15, 9, 0)
    summer_id = await _insert(
        async_session_factory,
        key="summer",
        local=summer,
        stored_utc=summer.replace(tzinfo=UTC),
    )
    winter_id = await _insert(
        async_session_factory,
        key="winter",
        local=winter,
        stored_utc=winter.replace(tzinfo=UTC),
    )

    result = await fix_guessed_datetime_utc(
        zone=ZONE, instrument=INSTRUMENT, dry_run=False
    )

    assert result["candidates"] == 2
    assert result["updated"] == 2
    assert result["unchanged"] == 0
    assert result["ambiguous"] == 0
    assert result["left_alone"] == 0
    stored = await _stored_utc(async_session_factory)
    assert stored[summer_id] == datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    assert stored[winter_id] == datetime(2026, 1, 15, 8, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_rows_that_were_not_guessed_as_utc_are_left_alone(
    async_session_factory,
):
    """A row with a reported zone, and a row guessed in a real zone, stay as they are.

    The second is also counted, so that a run that fixed nothing in that row's
    scope is not read as a run that found nothing to fix.
    """
    local = datetime(2026, 7, 1, 14, 0)
    reported_id = await _insert(
        async_session_factory,
        key="reported",
        local=local,
        stored_utc=datetime(2026, 7, 1, 12, 0, tzinfo=UTC),
        source="agent",
        zone=ZONE,
    )
    # Guessed, but by a converter running in Helsinki: a 3 h offset is stored.
    elsewhere_id = await _insert(
        async_session_factory,
        key="elsewhere",
        local=local,
        stored_utc=datetime(2026, 7, 1, 11, 0, tzinfo=UTC),
    )
    guessed_id = await _insert(
        async_session_factory,
        key="guessed",
        local=local,
        stored_utc=local.replace(tzinfo=UTC),
    )

    result = await fix_guessed_datetime_utc(
        zone=ZONE, instrument=INSTRUMENT, dry_run=False
    )

    assert result["candidates"] == 1
    assert result["updated"] == 1
    assert result["left_alone"] == 1
    stored = await _stored_utc(async_session_factory)
    assert stored[reported_id] == datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    assert stored[elsewhere_id] == datetime(2026, 7, 1, 11, 0, tzinfo=UTC)
    assert stored[guessed_id] == datetime(2026, 7, 1, 12, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_a_dry_run_reports_and_writes_nothing(async_session_factory):
    local = datetime(2026, 7, 1, 14, 0)
    guessed_id = await _insert(
        async_session_factory,
        key="dry",
        local=local,
        stored_utc=local.replace(tzinfo=UTC),
    )

    result = await fix_guessed_datetime_utc(zone=ZONE, instrument=INSTRUMENT)

    assert result["dry_run"] is True
    assert result["candidates"] == 1
    assert result["updated"] == 0
    assert [row["sample_file_id"] for row in result["previewed"]] == [guessed_id]
    assert result["previewed"][0]["corrected_utc"] == datetime(
        2026, 7, 1, 12, 0, tzinfo=UTC
    )
    stored = await _stored_utc(async_session_factory)
    assert stored[guessed_id] == local.replace(tzinfo=UTC)


@pytest.mark.asyncio
async def test_the_instrument_filter_and_a_second_run_hold(async_session_factory):
    """Only the named instrument is repaired, and repairing again changes nothing."""
    local = datetime(2026, 7, 1, 14, 0)
    mine_id = await _insert(
        async_session_factory,
        key="mine",
        local=local,
        stored_utc=local.replace(tzinfo=UTC),
    )
    other_id = await _insert(
        async_session_factory,
        key="other",
        local=local,
        stored_utc=local.replace(tzinfo=UTC),
        instrument=OTHER_INSTRUMENT,
    )

    first = await fix_guessed_datetime_utc(
        zone=ZONE, instrument=INSTRUMENT, dry_run=False
    )
    again = await fix_guessed_datetime_utc(
        zone=ZONE, instrument=INSTRUMENT, dry_run=False
    )

    assert first["updated"] == 1
    assert again["candidates"] == 0
    assert again["updated"] == 0
    stored = await _stored_utc(async_session_factory)
    assert stored[mine_id] == datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    assert stored[other_id] == local.replace(tzinfo=UTC)


@pytest.mark.asyncio
async def test_a_row_already_right_in_the_zone_is_not_written(
    async_session_factory,
):
    """A zone at UTC in winter: the stored value is correct and is left as it is."""
    local = datetime(2026, 1, 15, 9, 0)
    row_id = await _insert(
        async_session_factory,
        key="london",
        local=local,
        stored_utc=local.replace(tzinfo=UTC),
    )

    result = await fix_guessed_datetime_utc(
        zone="Europe/London", instrument=INSTRUMENT, dry_run=False
    )

    assert result["candidates"] == 1
    assert result["unchanged"] == 1
    assert result["updated"] == 0
    stored = await _stored_utc(async_session_factory)
    assert stored[row_id] == local.replace(tzinfo=UTC)


@pytest.mark.asyncio
async def test_an_unknown_zone_is_refused_before_anything_is_read(
    async_session_factory,
):
    local = datetime(2026, 7, 1, 14, 0)
    row_id = await _insert(
        async_session_factory,
        key="unknown",
        local=local,
        stored_utc=local.replace(tzinfo=UTC),
    )

    with pytest.raises(ValueError, match="not a known IANA timezone"):
        await fix_guessed_datetime_utc(
            zone="Mars/Olympus_Mons", instrument=INSTRUMENT, dry_run=False
        )

    stored = await _stored_utc(async_session_factory)
    assert stored[row_id] == local.replace(tzinfo=UTC)


@pytest.mark.parametrize(
    "wall",
    [
        datetime(2026, 7, 1, 14, 0),  # an ordinary summer afternoon
        datetime(2026, 10, 25, 2, 30),  # the hour the clocks repeat
        datetime(2026, 3, 29, 2, 30),  # the hour the clocks skip
    ],
)
def test_the_repair_reads_the_wall_clock_as_the_converter_does(wall):
    """The repaired instant is the one the converter would have stored.

    Pinned against the converter's own resolver rather than by importing it,
    so the admin module stays free of the converter's dependencies while the
    two cannot drift apart unnoticed.
    """
    zone = ZoneInfo(ZONE)
    converter = resolve_wall_time_offset(wall, zone)

    repaired, uncertain = wall_clock_as_utc(wall, zone)

    assert repaired == (wall - timedelta(seconds=converter.seconds)).replace(tzinfo=UTC)
    assert uncertain == (converter.anomaly is not None)
