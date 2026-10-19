"""
Integration tests for the repair of acquisition times guessed as UTC.

A file uploaded without a reported zone had its wall clock read in the
converter's own zone, UTC in production, so at a site in another zone it was
stored with a zero offset. The repair reads such an Orbitrap row's wall clock
in the zone the operator names and writes the UTC instant that gives, as the
converter would have had the upload reported the zone, and moves the file's
instrument function with it. These tests pin what it selects, what it
writes, what it leaves alone, what it counts, that a dry run writes nothing,
and that an apply leaves a ledger.

Each test's rows carry an instrument of their own and are deleted after it,
so the suite's shared database is left as it was found.
"""

import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, update

from mascope_backend.db import SampleFile
from mascope_backend.db.admin.fix_guessed_datetime_utc import (
    fix_guessed_datetime_utc,
    wall_clock_as_utc,
)
from mascope_backend.db.models import InstrumentFunction
from mascope_backend.file_converter.base_processor import resolve_wall_time_offset


#: Names an instrument of its own and resolves to a known instrument type.
INSTRUMENT = "repair-orbi-guess"
#: A second instrument, to pin that the instrument filter holds.
OTHER_INSTRUMENT = "repair-orbi-other"
INSTRUMENTS = [INSTRUMENT, OTHER_INSTRUMENT]

ZONE = "Europe/Vienna"
UTC = timezone.utc


@pytest_asyncio.fixture(autouse=True)
async def clean_rows(async_session_factory):
    """Remove the files and functions these tests insert, before and after each one."""

    async def _sweep():
        async with async_session_factory() as session:
            await session.execute(
                delete(SampleFile).where(SampleFile.instrument.in_(INSTRUMENTS))
            )
            await session.execute(
                delete(InstrumentFunction).where(
                    InstrumentFunction.instrument.in_(INSTRUMENTS)
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
    instrument_type: str = "orbi",
    created: datetime | None = None,
    function_utc: datetime | None = None,
) -> str:
    """One sample_file row as the converter would have written it.

    :param key: Distinguishes the row within the test; part of its id and name.
    :param local: The wall clock as the instrument recorded it, naive.
    :param stored_utc: What the converter stored as the UTC instant.
    :param source: The row's ``utc_offset_source``.
    :param zone: The row's ``acquisition_timezone``.
    :param instrument: The instrument the row is filed under.
    :param instrument_type: ``orbi`` or ``tof``.
    :param created: The registration time; the database's now() when None.
    :param function_utc: When given, an instrument function dated so, fitted
        from this file.
    :return: The row's id.
    """
    sample_file_id = f"repair{key:0>10}"[:16]
    async with async_session_factory() as session:
        function_id = None
        if function_utc is not None:
            function_id = f"repairfn{key:0>24}"[:32]
            session.add(
                InstrumentFunction(
                    instrument_function_id=function_id,
                    instrument=instrument,
                    method_file="repair.meth",
                    datetime_utc=function_utc,
                )
            )
            await session.flush()
        row = SampleFile(
            sample_file_id=sample_file_id,
            filename=f"{instrument}_{key}_NO3.raw",
            instrument=instrument,
            instrument_type=instrument_type,
            instrument_function_id=function_id,
            datetime=local,
            datetime_utc=stored_utc,
            length=60.0,
            range=[50.0, 500.0],
            polarity="-",
            utc_offset_source=source,
            acquisition_timezone=zone,
        )
        if created is not None:
            row.sample_file_utc_created = created
        session.add(row)
        await session.commit()
    return sample_file_id


async def _forget_created(async_session_factory, sample_file_id: str) -> None:
    """Take a row's registration time away, as rows registered before the
    column existed have none. The column has a server default, so an
    insert cannot leave it NULL."""
    async with async_session_factory() as session:
        await session.execute(
            update(SampleFile)
            .where(SampleFile.sample_file_id == sample_file_id)
            .values(sample_file_utc_created=None)
        )
        await session.commit()


async def _stored_utc(async_session_factory) -> dict[str, datetime]:
    """Every test row's ``datetime_utc``, by id."""
    async with async_session_factory() as session:
        rows = await session.execute(
            select(SampleFile.sample_file_id, SampleFile.datetime_utc).where(
                SampleFile.instrument.in_(INSTRUMENTS)
            )
        )
        return {sample_file_id: stored for sample_file_id, stored in rows}


async def _function_utc(async_session_factory) -> dict[str, datetime]:
    """Every test instrument function's ``datetime_utc``, by id."""
    async with async_session_factory() as session:
        rows = await session.execute(
            select(
                InstrumentFunction.instrument_function_id,
                InstrumentFunction.datetime_utc,
            ).where(InstrumentFunction.instrument.in_(INSTRUMENTS))
        )
        return {function_id: stored for function_id, stored in rows}


@pytest.mark.asyncio
async def test_a_wall_clock_stored_as_utc_is_read_in_the_zone(
    async_session_factory, tmp_path
):
    """Summer and winter rows each get the zone's offset at their own instant,
    the dry-run evidence reads as the docs say, and the apply leaves a ledger."""
    summer = datetime(2026, 7, 1, 14, 0)
    winter = datetime(2026, 1, 15, 9, 0)
    # Registered five minutes after the true end of the acquisition: as
    # stored (two hours late) the file ends after it was registered, which
    # no true acquisition time does; repaired, it ends before.
    summer_id = await _insert(
        async_session_factory,
        key="summer",
        local=summer,
        stored_utc=summer.replace(tzinfo=UTC),
        created=datetime(2026, 7, 1, 12, 6, tzinfo=UTC),
    )
    winter_id = await _insert(
        async_session_factory,
        key="winter",
        local=winter,
        stored_utc=winter.replace(tzinfo=UTC),
    )

    result = await fix_guessed_datetime_utc(
        zone=ZONE, instrument=INSTRUMENT, dry_run=False, ledger_dir=tmp_path
    )

    assert result["candidates"] == 2
    assert result["updated"] == 2
    assert result["unchanged"] == 0
    assert result["ambiguous"] == 0
    assert result["left_alone"] == 0
    assert result["with_created"] == 2
    assert result["impossible_before"] == 1
    assert result["impossible_after"] == 0
    stored = await _stored_utc(async_session_factory)
    assert stored[summer_id] == datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    assert stored[winter_id] == datetime(2026, 1, 15, 8, 0, tzinfo=UTC)

    ledger = [json.loads(line) for line in open(result["ledger"], encoding="utf-8")]
    assert {entry["sample_file_id"] for entry in ledger} == {summer_id, winter_id}
    by_id = {entry["sample_file_id"]: entry for entry in ledger}
    assert by_id[summer_id]["datetime_utc_before"] == "2026-07-01T14:00:00+00:00"
    assert by_id[summer_id]["datetime_utc_after"] == "2026-07-01T12:00:00+00:00"
    assert by_id[summer_id]["zone"] == ZONE
    assert by_id[summer_id]["instrument_function_id"] is None


@pytest.mark.asyncio
async def test_rows_that_were_not_guessed_as_utc_are_left_alone(
    async_session_factory, tmp_path
):
    """A row with a reported zone, a row guessed in a real zone, and a guessed
    TOF row stay as they are.

    The second is also counted, so that a run that fixed nothing in that row's
    scope is not read as a run that found nothing to fix. The TOF row is
    neither repaired nor counted: its ``datetime_utc`` is already right, since
    a TOF ``datetime`` is the acquisition's UTC instant plus the guessed
    offset, not a wall clock read off the instrument.
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
    tof_id = await _insert(
        async_session_factory,
        key="tof",
        local=local,
        stored_utc=local.replace(tzinfo=UTC),
        instrument_type="tof",
    )
    guessed_id = await _insert(
        async_session_factory,
        key="guessed",
        local=local,
        stored_utc=local.replace(tzinfo=UTC),
    )

    result = await fix_guessed_datetime_utc(
        zone=ZONE, instrument=INSTRUMENT, dry_run=False, ledger_dir=tmp_path
    )

    assert result["candidates"] == 1
    assert result["updated"] == 1
    assert result["left_alone"] == 1
    stored = await _stored_utc(async_session_factory)
    assert stored[reported_id] == datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    assert stored[elsewhere_id] == datetime(2026, 7, 1, 11, 0, tzinfo=UTC)
    assert stored[tof_id] == local.replace(tzinfo=UTC)
    assert stored[guessed_id] == datetime(2026, 7, 1, 12, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_a_dry_run_reports_and_writes_nothing(async_session_factory, tmp_path):
    local = datetime(2026, 7, 1, 14, 0)
    guessed_id = await _insert(
        async_session_factory,
        key="dry",
        local=local,
        stored_utc=local.replace(tzinfo=UTC),
    )

    result = await fix_guessed_datetime_utc(
        zone=ZONE, instrument=INSTRUMENT, ledger_dir=tmp_path
    )

    assert result["dry_run"] is True
    assert result["candidates"] == 1
    assert result["updated"] == 0
    assert result["ledger"] is None
    assert list(tmp_path.iterdir()) == []
    assert [row["sample_file_id"] for row in result["previewed"]] == [guessed_id]
    assert result["previewed"][0]["corrected_utc"] == datetime(
        2026, 7, 1, 12, 0, tzinfo=UTC
    )
    stored = await _stored_utc(async_session_factory)
    assert stored[guessed_id] == local.replace(tzinfo=UTC)


@pytest.mark.asyncio
async def test_the_instrument_filter_and_a_second_run_hold(
    async_session_factory, tmp_path
):
    """Only the named instrument is repaired; repairing again changes nothing,
    and reports the repaired row among those it leaves alone."""
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
        zone=ZONE, instrument=INSTRUMENT, dry_run=False, ledger_dir=tmp_path
    )
    again = await fix_guessed_datetime_utc(
        zone=ZONE, instrument=INSTRUMENT, dry_run=False, ledger_dir=tmp_path
    )

    assert first["updated"] == 1
    assert again["candidates"] == 0
    assert again["updated"] == 0
    # The repaired row now carries a non-zero offset under its unchanged
    # source columns, so a later run counts it with the rows it leaves alone.
    assert again["left_alone"] == 1
    assert again["ledger"] is None
    stored = await _stored_utc(async_session_factory)
    assert stored[mine_id] == datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    assert stored[other_id] == local.replace(tzinfo=UTC)


@pytest.mark.asyncio
async def test_the_files_instrument_function_moves_with_it(
    async_session_factory, tmp_path
):
    """The fit the converter made from the file carries the same guessed
    instant and is moved with the file; a function dated otherwise is not."""
    local = datetime(2026, 7, 1, 14, 0)
    moved_id = await _insert(
        async_session_factory,
        key="fnmoves",
        local=local,
        stored_utc=local.replace(tzinfo=UTC),
        function_utc=local.replace(tzinfo=UTC),
    )
    kept_id = await _insert(
        async_session_factory,
        key="fnkept",
        local=local,
        stored_utc=local.replace(tzinfo=UTC),
        # Fitted from another file, and so dated otherwise: not this file's.
        function_utc=datetime(2026, 6, 30, 9, 0, tzinfo=UTC),
    )

    result = await fix_guessed_datetime_utc(
        zone=ZONE, instrument=INSTRUMENT, dry_run=False, ledger_dir=tmp_path
    )

    assert result["updated"] == 2
    assert result["functions_updated"] == 1
    functions = await _function_utc(async_session_factory)
    assert functions[f"repairfn{'fnmoves':0>24}"[:32]] == datetime(
        2026, 7, 1, 12, 0, tzinfo=UTC
    )
    assert functions[f"repairfn{'fnkept':0>24}"[:32]] == datetime(
        2026, 6, 30, 9, 0, tzinfo=UTC
    )
    ledger = {
        entry["sample_file_id"]: entry
        for entry in map(json.loads, open(result["ledger"], encoding="utf-8"))
    }
    assert (
        ledger[moved_id]["instrument_function_id"] == f"repairfn{'fnmoves':0>24}"[:32]
    )
    assert ledger[kept_id]["instrument_function_id"] is None


@pytest.mark.asyncio
async def test_a_registration_window_counts_the_rows_it_cannot_hold(
    async_session_factory, tmp_path
):
    """A row registered before the column existed has no registration time,
    so no window holds it; it is reported rather than silently dropped."""
    local = datetime(2026, 7, 1, 14, 0)
    undated_id = await _insert(
        async_session_factory,
        key="undated",
        local=local,
        stored_utc=local.replace(tzinfo=UTC),
    )
    await _forget_created(async_session_factory, undated_id)
    dated_id = await _insert(
        async_session_factory,
        key="dated",
        local=local,
        stored_utc=local.replace(tzinfo=UTC),
        created=datetime(2026, 7, 1, 12, 6, tzinfo=UTC),
    )

    windowed = await fix_guessed_datetime_utc(
        zone=ZONE,
        instrument=INSTRUMENT,
        created_max=datetime(2026, 8, 1, tzinfo=UTC),
        dry_run=False,
        ledger_dir=tmp_path,
    )
    unwindowed = await fix_guessed_datetime_utc(
        zone=ZONE, instrument=INSTRUMENT, dry_run=False, ledger_dir=tmp_path
    )

    assert windowed["candidates"] == 1
    assert windowed["updated"] == 1
    assert windowed["outside_window"] == 1
    assert unwindowed["candidates"] == 1
    assert unwindowed["updated"] == 1
    assert unwindowed["outside_window"] == 0
    stored = await _stored_utc(async_session_factory)
    assert stored[dated_id] == datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    assert stored[undated_id] == datetime(2026, 7, 1, 12, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_a_row_already_right_in_the_zone_is_not_written(
    async_session_factory, tmp_path
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
        zone="Europe/London", instrument=INSTRUMENT, dry_run=False, ledger_dir=tmp_path
    )

    assert result["candidates"] == 1
    assert result["unchanged"] == 1
    assert result["updated"] == 0
    assert result["ledger"] is None
    stored = await _stored_utc(async_session_factory)
    assert stored[row_id] == local.replace(tzinfo=UTC)


@pytest.mark.asyncio
async def test_an_unknown_zone_is_refused_before_anything_is_read(
    async_session_factory, tmp_path
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
            zone="Mars/Olympus_Mons",
            instrument=INSTRUMENT,
            dry_run=False,
            ledger_dir=tmp_path,
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
