"""Integration tests: deleting an instrument config removes that config only.

Every sample file gets an instrument config of its own at ingest, so many rows
share an (instrument, method_file) pair - all Orbitrap files converted while
the reader reported no method share ``(instrument, "")``, and every file of one
method shares that method's path. ``DELETE /api/instrument_configs/{id}`` must
remove the named row and unlink only the sample file that pointed at it
(``ondelete="SET NULL"``), not every config and file sharing the pair.
"""

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from mascope_backend.db import InstrumentFunction, SampleFile
from mascope_backend.db.id import gen_id


_ACQUIRED = datetime(2026, 7, 1, 12, 0, 0)
_NOW = datetime(2026, 7, 1, tzinfo=timezone.utc)
_METHOD = "C:\\Xcalibur\\methods\\ambient.meth"


@pytest_asyncio.fixture
async def shared_pair_configs(async_session_factory):
    """Four configs on one instrument, two per (instrument, method_file) pair,
    each with the one sample file it was fitted for.

    :return: ``{name: (instrument_function_id, sample_file_id)}`` with names
        ``empty_1``/``empty_2`` (method "") and ``named_1``/``named_2``.
    """
    instrument = f"CfgDel{gen_id(6)}"
    rows = {}
    async with async_session_factory() as session:
        for name, method in (
            ("empty_1", ""),
            ("empty_2", ""),
            ("named_1", _METHOD),
            ("named_2", _METHOD),
        ):
            instrument_function_id, sample_file_id = gen_id(32), gen_id()
            session.add(
                InstrumentFunction(
                    instrument_function_id=instrument_function_id,
                    instrument=instrument,
                    method_file=method,
                    datetime_utc=_NOW,
                    peakshape={},
                    resolution_function=[1.0],
                )
            )
            session.add(
                SampleFile(
                    sample_file_id=sample_file_id,
                    instrument_function_id=instrument_function_id,
                    filename=f"{instrument}_2026.07.01_12h00m00s_{name}",
                    instrument=instrument,
                    instrument_type="orbi",
                    datetime=_ACQUIRED,
                    datetime_utc=_NOW,
                    length=60.0,
                    range=[40.0, 500.0],
                    polarity="+",
                    method_file=method,
                )
            )
            rows[name] = (instrument_function_id, sample_file_id)
        await session.commit()

    yield rows

    async with async_session_factory() as session:
        await session.execute(
            delete(SampleFile).where(
                SampleFile.sample_file_id.in_([sf for _, sf in rows.values()])
            )
        )
        await session.execute(
            delete(InstrumentFunction).where(
                InstrumentFunction.instrument_function_id.in_(
                    [config for config, _ in rows.values()]
                )
            )
        )
        await session.commit()


async def _state(async_session_factory, rows):
    """Which configs still exist, and which config each sample file points at."""
    config_ids = [config for config, _ in rows.values()]
    sample_file_ids = [sf for _, sf in rows.values()]
    async with async_session_factory() as session:
        remaining = set(
            (
                await session.scalars(
                    select(InstrumentFunction.instrument_function_id).where(
                        InstrumentFunction.instrument_function_id.in_(config_ids)
                    )
                )
            ).all()
        )
        links = dict(
            (
                await session.execute(
                    select(
                        SampleFile.sample_file_id, SampleFile.instrument_function_id
                    ).where(SampleFile.sample_file_id.in_(sample_file_ids))
                )
            ).all()
        )
    return remaining, links


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["empty_1", "named_1"])
async def test_delete_removes_only_the_named_config(
    editor_client, async_session_factory, shared_pair_configs, target
):
    rows = shared_pair_configs
    config_id, sample_file_id = rows[target]

    response = await editor_client.delete(f"/api/instrument_configs/{config_id}")
    assert response.status_code == 200, response.text

    remaining, links = await _state(async_session_factory, rows)
    assert remaining == {config for name, (config, _) in rows.items() if name != target}
    # The deleted config's own file loses its link; every other file keeps its own.
    assert links[sample_file_id] is None
    for name, (config, sf) in rows.items():
        if name != target:
            assert links[sf] == config


@pytest.mark.asyncio
async def test_delete_of_an_unknown_config_is_not_found(
    editor_client, async_session_factory, shared_pair_configs
):
    rows = shared_pair_configs

    response = await editor_client.delete(f"/api/instrument_configs/{gen_id(32)}")
    assert response.status_code == 404, response.text

    remaining, _ = await _state(async_session_factory, rows)
    assert remaining == {config for config, _ in rows.values()}
