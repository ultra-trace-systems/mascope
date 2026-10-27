"""The instrument-function repair copies a config with its instrument row.

``populate_none_instrument_function_ids`` gives a file with no instrument
function a copy of the newest matching one. The copy names the same
instrument, so it links the same ``instrument`` row as its source.
"""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mascope_backend.db.scripts import populate_none_instrument_function_ids as script


@pytest.mark.asyncio
async def test_the_copy_links_its_sources_instrument_row():
    sample_file = SimpleNamespace(
        instrument="ORBI-1",
        method_file="m.meth",
        filename="ORBI-1_f.raw",
        instrument_function_id=None,
    )
    source = SimpleNamespace(
        instrument_function_id="if-source",
        instrument="ORBI-1",
        instrument_id="instr-orbi-1",
        method_file="m.meth",
        datetime_utc=None,
        peakshape={},
        resolution_function=[1.0],
    )
    affected = MagicMock()
    affected.scalars.return_value.all.return_value = [sample_file]
    found = MagicMock()
    found.scalar_one_or_none.return_value = source
    session = MagicMock()
    session.execute = AsyncMock(side_effect=[affected, found])
    session.flush = AsyncMock()
    session.commit = AsyncMock()

    @asynccontextmanager
    async def async_session():
        yield session

    with patch.object(script, "async_session", async_session):
        counts = await script.populate_none_instrument_function_ids()

    assert counts["updated"] == 1
    (copy,), _ = session.add.call_args
    assert copy.instrument == "ORBI-1"
    assert copy.instrument_id == "instr-orbi-1"
    assert sample_file.instrument_function_id == copy.instrument_function_id
