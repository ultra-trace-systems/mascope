"""
Tests: where the exports take a sample file's instrument model from.

The model is the one the file's ``Instrument`` row records, which ingest fills
from the reader, so a file whose raw file is gone still has one. A row with no
model yet - an instrument no file has registered since the table was made -
leaves an Orbitrap file to its raw file's header, as before the table; a TOF
file is never opened.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio

from mascope_backend.api.controllers.sample.batches.export import service
from mascope_backend.db import Instrument, SampleFile
from mascope_backend.db.id import gen_id


@pytest_asyncio.fixture
async def files(async_session_factory):
    """Four files: an Orbitrap on a row with a model, an Orbitrap on a row
    without one, an Orbitrap linked to no row, and a TOF on a row without
    one. By id: (filename, instrument class)."""
    ids = SimpleNamespace(
        modelled=gen_id(),
        bare=gen_id(),
        unlinked=gen_id(),
        tof=gen_id(),
        modelled_row=gen_id(16),
        bare_row=gen_id(16),
        tof_row=gen_id(16),
    )
    async with async_session_factory() as session:
        for row_id, instrument_type, model in (
            (ids.modelled_row, "orbi", "Q Exactive"),
            (ids.bare_row, "orbi", None),
            (ids.tof_row, "tof", None),
        ):
            session.add(
                Instrument(
                    instrument_id=row_id,
                    name=f"models-{row_id}",
                    instrument_type=instrument_type,
                    model=model,
                )
            )
        await session.flush()
        for file_id, row_id, instrument_type in (
            (ids.modelled, ids.modelled_row, "orbi"),
            (ids.bare, ids.bare_row, "orbi"),
            (ids.unlinked, None, "orbi"),
            (ids.tof, ids.tof_row, "tof"),
        ):
            session.add(
                SampleFile(
                    sample_file_id=file_id,
                    filename=f"models_{file_id}.raw",
                    instrument="instrument-x",
                    instrument_id=row_id,
                    instrument_type=instrument_type,
                    datetime=datetime(2026, 1, 1, 12, 0, 0),
                    datetime_utc=datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
                    length=60.0,
                    range=[50.0, 750.0],
                    polarity="+",
                )
            )
        await session.commit()
    ids.files = {
        file_id: (f"models_{file_id}.raw", instrument_type)
        for file_id, instrument_type in (
            (ids.modelled, "orbi"),
            (ids.bare, "orbi"),
            (ids.unlinked, "orbi"),
            (ids.tof, "tof"),
        )
    }
    return ids


@pytest.fixture
def reader(monkeypatch):
    """A raw file reader that names a model for every file it is asked about,
    and records which files those were."""
    opened = []

    def metadata_factory(filename):
        opened.append(filename)
        return SimpleNamespace(instrument_details={"Model": "Orbitrap Exploris 120"})

    monkeypatch.setattr(service, "get_sample_file_type", lambda filename: "orbi_raw")
    monkeypatch.setattr(service.m_metadata, "metadata_factory", metadata_factory)
    return opened


@pytest.mark.asyncio
async def test_the_rows_model_comes_first_and_the_header_fills_in(files, reader):
    models = await service.instrument_models_of(files.files)
    assert models == {
        files.modelled: "Q Exactive",
        files.bare: "Orbitrap Exploris 120",
        files.unlinked: "Orbitrap Exploris 120",
        files.tof: None,
    }
    assert sorted(reader) == sorted(
        [f"models_{files.bare}.raw", f"models_{files.unlinked}.raw"]
    )


@pytest.mark.asyncio
async def test_a_file_whose_raw_file_is_gone_keeps_its_rows_model(
    files, reader, monkeypatch
):
    monkeypatch.setattr(service, "get_sample_file_type", lambda filename: None)
    models = await service.instrument_models_of(files.files)
    assert models[files.modelled] == "Q Exactive"
    assert models[files.bare] is None
    assert reader == []


@pytest.mark.asyncio
async def test_no_files_names_no_model(reader):
    assert await service.instrument_models_of({}) == {}
    assert reader == []
