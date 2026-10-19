"""
Integration tests: the Raw files list is read in order off its indexes.

``get_sample_files`` writes the list's ORDER BY, and ``models.SampleFile``
declares the indexes that serve it (``ix_sample_file_datetime_utc``,
``ix_sample_file_instrument_datetime_utc``). Nothing else ties the two: an
index in an order the statement does not ask for is still an index, and the
planner bounds the scan with it and sorts all the same, so the list would
slow down with every test still green. Here the controller is called as the
routes call it, the statements it sends are captured, and each is EXPLAINed
with sorting and sequential scans priced out - a plan with no sort in it is
one an index delivers in order. The test schema is built from the models, so
a change to either side without the other fails here.
"""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete, event, text

from mascope_backend.api.controllers.sample.files.sample_files_controller import (
    get_sample_files,
)
from mascope_backend.db import SampleFile
from mascope_backend.db.id import gen_id


INSTRUMENT = "plan-orbi"
OTHER = "plan-other"
# Clear of every other test's files, so a look-back from here is these alone.
_FIRST = datetime(2031, 1, 1, tzinfo=timezone.utc)
_FILES = 200
_SINCE = _FIRST + timedelta(hours=_FILES // 2)


@pytest_asyncio.fixture(autouse=True)
async def files(async_session_factory):
    """One file an hour, one in ten from the instrument in focus.

    On an even split the planner reads one instrument's files off the time
    index with a filter, which is in order too; a rare instrument makes its
    own index the plainly cheaper read, so its plan shows that index.
    """
    async with async_session_factory() as session:
        for i in range(_FILES):
            acquired = _FIRST + timedelta(hours=i)
            instrument = INSTRUMENT if i % 10 == 0 else OTHER
            session.add(
                SampleFile(
                    sample_file_id=gen_id(),
                    filename=f"{instrument}_{i}.raw",
                    instrument=instrument,
                    instrument_type="orbi",
                    datetime=acquired.replace(tzinfo=None),
                    datetime_utc=acquired,
                    length=60.0,
                    range=[50.0, 500.0],
                    polarity="-",
                )
            )
        await session.commit()
        # Statistics are written in the transaction: committed, or lost.
        await session.execute(text("ANALYZE sample_file"))
        await session.commit()
    yield
    async with async_session_factory() as session:
        await session.execute(
            delete(SampleFile).where(SampleFile.instrument.in_([INSTRUMENT, OTHER]))
        )
        await session.commit()


async def _statements(async_engine, **query) -> dict[str, tuple[str, tuple]]:
    """The count and the page ``get_sample_files`` sends for ``query``.

    :return: ``{"count": (sql, parameters), "page": (sql, parameters)}``.
    """
    sent = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        if "FROM sample_file" in statement:
            sent.append((statement, parameters))

    event.listen(async_engine.sync_engine, "before_cursor_execute", capture)
    try:
        await get_sample_files(**query)
    finally:
        event.remove(async_engine.sync_engine, "before_cursor_execute", capture)
    assert len(sent) == 2, sent
    count, page = sorted(sent, key=lambda s: not s[0].startswith("SELECT count("))
    return {"count": count, "page": page}


async def _plan(async_engine, statement: str, parameters: tuple) -> str:
    """EXPLAIN ``statement`` with sorting and sequential scans priced out.

    SET LOCAL ends with the transaction, so the pooled connection goes back
    with the planner as it found it.
    """
    async with async_engine.begin() as conn:
        await conn.exec_driver_sql("SET LOCAL enable_sort = off")
        await conn.exec_driver_sql("SET LOCAL enable_seqscan = off")
        rows = (await conn.exec_driver_sql(f"EXPLAIN {statement}", parameters)).all()
    return "\n".join(row[0] for row in rows)


@pytest.mark.asyncio
@pytest.mark.parametrize("order", ["desc", "asc"])
@pytest.mark.parametrize(
    ("instrument", "index"),
    [
        (None, "ix_sample_file_datetime_utc"),
        (INSTRUMENT, "ix_sample_file_instrument_datetime_utc"),
    ],
)
async def test_a_page_of_the_list_is_read_in_order_off_its_index(
    async_engine, order, instrument, index
):
    """Newest first reads the index backwards, oldest first forwards."""
    sent = await _statements(
        async_engine,
        datetime_min=_SINCE,
        instrument=instrument,
        sort="datetime_utc",
        order=order,
        page=0,
        limit=10,
    )

    plan = await _plan(async_engine, *sent["page"])

    assert index in plan, plan
    assert "Sort" not in plan, plan


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("instrument", "index"),
    [
        (None, "ix_sample_file_datetime_utc"),
        (INSTRUMENT, "ix_sample_file_instrument_datetime_utc"),
    ],
)
async def test_the_count_is_unordered_and_read_off_the_index(
    async_engine, instrument, index
):
    """An ordered count would sort every row it keeps only to count them."""
    sent = await _statements(
        async_engine,
        datetime_min=_SINCE,
        instrument=instrument,
        sort="datetime_utc",
        order="asc",
        page=0,
        limit=10,
    )
    statement, parameters = sent["count"]

    plan = await _plan(async_engine, statement, parameters)

    assert "ORDER BY" not in statement, statement
    assert index in plan, plan
    assert "Sort" not in plan, plan
