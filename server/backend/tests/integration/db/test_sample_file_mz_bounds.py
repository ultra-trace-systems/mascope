"""
Tests: a sample file's m/z bounds are stored with its range wherever it is
written.

``sample_file.mz_min`` and ``mz_max`` are what the finder reads in SQL, so
they have to say what ``range`` says on the row itself: when the converter
registers the file, when its range is written again - the m/z calibration
writes the axis it moved through the file's update - and when
``fix_tofwerk_range`` cuts a stored axis down to its two ends, which is SQL
and passes by the model.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import BackgroundTasks
from sqlalchemy import delete, select, text

from mascope_backend.api.controllers.sample.files import sample_files_controller
from mascope_backend.api.models.sample.files.sample_file_pydantic_model import (
    SampleFileCreate,
    SampleFileUpdate,
)
from mascope_backend.db import SampleFile
from mascope_backend.db.id import gen_id
from mascope_backend.db.models import range_bounds
from mascope_backend.db.scripts import fix_tofwerk_range


_FIELDS = {
    "datetime": datetime(2026, 11, 4, 12, 0),
    "datetime_utc": datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc),
    "length": 60.0,
    "polarity": "-",
}


@pytest_asyncio.fixture
async def name(async_session_factory):
    """An instrument name of the test's own; its files removed afterwards."""
    instrument = f"bounds-{gen_id(8).lower()}"
    yield instrument
    async with async_session_factory() as session:
        await session.execute(
            delete(SampleFile).where(SampleFile.instrument == instrument)
        )
        await session.commit()


async def _bounds(async_session_factory, **where) -> tuple:
    async with async_session_factory() as session:
        return (
            await session.execute(
                select(SampleFile.range, SampleFile.mz_min, SampleFile.mz_max).where(
                    *(getattr(SampleFile, key) == value for key, value in where.items())
                )
            )
        ).one()


@pytest.mark.asyncio
async def test_a_registered_file_carries_the_ends_of_its_range(
    async_session_factory, name, monkeypatch
):
    monkeypatch.setattr(sample_files_controller, "emit_record_created", AsyncMock())
    monkeypatch.setattr(
        sample_files_controller, "create_acquisition_datasets", AsyncMock()
    )

    created = await sample_files_controller.create_sample_file(
        SampleFileCreate(
            filename=f"{name}_2026.11.04-12h00m00s_bounds",
            instrument=name,
            instrument_type="orbi",
            range=[40.25, 600.5],
            **_FIELDS,
        ),
        BackgroundTasks(),
    )

    assert await _bounds(
        async_session_factory, sample_file_id=created["data"]["sample_file_id"]
    ) == ([40.25, 600.5], 40.25, 600.5)
    # And what the registration answers, which the file's event carries too
    assert (created["data"]["mz_min"], created["data"]["mz_max"]) == (40.25, 600.5)


@pytest.mark.asyncio
async def test_a_range_written_again_moves_the_bounds(
    async_session_factory, name, monkeypatch
):
    """As the m/z calibration writes the axis it moved."""
    for unrelated in (
        "emit_record_updated",
        "create_acquisition_datasets",
        "delete_acquisition_datasets",
    ):
        monkeypatch.setattr(sample_files_controller, unrelated, AsyncMock())
    filename = f"{name}_2026.11.04-12h00m00s_moved"
    async with async_session_factory() as session:
        sample_file = SampleFile(
            sample_file_id=gen_id(),
            filename=filename,
            instrument=name,
            instrument_type="orbi",
            range=[40.0, 600.0],
            **_FIELDS,
        )
        session.add(sample_file)
        await session.commit()
        sample_file_id = sample_file.sample_file_id

    await sample_files_controller.update_sample_file(
        sample_file_id,
        SampleFileUpdate(
            filename=filename, instrument=name, range=[40.002, 599.998], **_FIELDS
        ),
    )

    assert await _bounds(async_session_factory, sample_file_id=sample_file_id) == (
        [40.002, 599.998],
        40.002,
        599.998,
    )


#: Stored axes, as the bug left them, and what the fix makes of each: its two
#: ends, and the bounds the model reads off them - both or neither, so an
#: axis with an end that is not a number gets no bounds at all.
_AXES = {
    "axis": ("[20.0, 20.5, 21.0, 1200.0]", ([20.0, 1200.0], 20.0, 1200.0)),
    "no last end": ("[50.1, 50.2, 50.3, null]", ([50.1, None], None, None)),
    "no first end": ("[null, 20.5, 21.0, 1200.0]", ([None, 1200.0], None, None)),
}


@pytest.mark.asyncio
async def test_fixing_a_stored_axis_writes_its_ends_beside_it(
    async_session_factory, name
):
    """Written in SQL, as the bug left it: the bounds no model ever set.
    Beside it a range that is no list at all, which the fix leaves alone
    rather than stopping on it."""
    axis_ids = {axis: gen_id() for axis in _AXES}
    odd_id = gen_id()
    async with async_session_factory() as session:
        session.add(
            SampleFile(
                sample_file_id=odd_id,
                filename=f"{name}_2026.11.04-12h00m00s_odd",
                instrument=name,
                instrument_type="orbi",
                range={"min": 0, "max": 500},
                **_FIELDS,
            )
        )
        for axis, (stored, _) in _AXES.items():
            await session.execute(
                text(
                    "INSERT INTO sample_file (sample_file_id, filename, instrument, "
                    '"datetime", datetime_utc, length, "range", polarity, '
                    "instrument_type) VALUES (:id, :filename, :instrument, :local, "
                    ":utc, 60.0, CAST(:stored AS json), '+', 'tof')"
                ),
                {
                    "id": axis_ids[axis],
                    "filename": f"{name}_2026.11.04-12h00m00s_{axis_ids[axis]}.h5",
                    "instrument": name,
                    "local": _FIELDS["datetime"],
                    "utc": _FIELDS["datetime_utc"],
                    "stored": stored,
                },
            )
        await session.commit()
    for sample_file_id in axis_ids.values():
        assert (await _bounds(async_session_factory, sample_file_id=sample_file_id))[
            1:
        ] == (None, None)

    await fix_tofwerk_range._fix_database()

    fixed = {
        axis: await _bounds(async_session_factory, sample_file_id=sample_file_id)
        for axis, sample_file_id in axis_ids.items()
    }
    assert fixed == {axis: expected for axis, (_, expected) in _AXES.items()}
    # The rule the model keeps wherever else the range is written
    assert all(
        range_bounds(mz_range) == (mz_min, mz_max)
        for mz_range, mz_min, mz_max in fixed.values()
    )
    assert await _bounds(async_session_factory, sample_file_id=odd_id) == (
        {"min": 0, "max": 500},
        None,
        None,
    )
