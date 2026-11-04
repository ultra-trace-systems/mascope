"""
Tests: where the exports take a sample file's instrument model from.

Each file states its own: the model its props name, else its kept raw file's
header (Orbitrap only; a TOF file is never opened), and only then its
``Instrument`` row, which holds one model per name - the last registered
file's - so a file whose props and raw file are gone still has one.
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
def props(monkeypatch):
    """No file has props, until a test gives one some: by filename."""
    stored = {}

    def read_props(filename):
        if filename not in stored:
            raise FileNotFoundError(filename)
        return stored[filename]

    monkeypatch.setattr(service.m_io, "read_props", read_props)
    return stored


@pytest.fixture
def reader(monkeypatch, props):
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
async def test_the_raw_header_comes_before_the_rows_model(files, reader):
    models = await service.instrument_models_of(files.files)
    assert models == {
        files.modelled: "Orbitrap Exploris 120",
        files.bare: "Orbitrap Exploris 120",
        files.unlinked: "Orbitrap Exploris 120",
        files.tof: None,
    }
    assert sorted(reader) == sorted(
        [
            f"models_{files.modelled}.raw",
            f"models_{files.bare}.raw",
            f"models_{files.unlinked}.raw",
        ]
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


@pytest.mark.asyncio
async def test_two_files_of_one_instrument_keep_their_own_models(
    files, props, reader, async_session_factory
):
    """The owner's case: one instrument name, two files whose props name
    different models. Each keeps its own, not the row's."""
    older = gen_id()
    async with async_session_factory() as session:
        session.add(
            SampleFile(
                sample_file_id=older,
                filename=f"models_{older}.raw",
                instrument="instrument-x",
                instrument_id=files.modelled_row,
                instrument_type="orbi",
                datetime=datetime(2025, 1, 1, 12, 0, 0),
                datetime_utc=datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
                length=60.0,
                range=[50.0, 750.0],
                polarity="+",
            )
        )
        await session.commit()
    props[f"models_{files.modelled}.raw"] = {
        "instrument_model": "Orbitrap Exploris 240"
    }
    props[f"models_{older}.raw"] = {
        "axis_fingerprint": {"source": {"instrument_model": "Q Exactive Plus"}}
    }
    models = await service.instrument_models_of(
        {
            files.modelled: (f"models_{files.modelled}.raw", "orbi"),
            older: (f"models_{older}.raw", "orbi"),
        }
    )
    assert models == {
        files.modelled: "Orbitrap Exploris 240",
        older: "Q Exactive Plus",
    }
    assert reader == []


@pytest.mark.asyncio
async def test_a_tof_files_props_name_its_model(files, props, reader):
    props[f"models_{files.tof}.raw"] = {"instrument_model": "TOF model Y"}
    models = await service.instrument_models_of(files.files)
    assert models[files.tof] == "TOF model Y"
    assert f"models_{files.tof}.raw" not in reader


@pytest.mark.asyncio
async def test_a_file_whose_props_name_none_reads_its_header(files, props, reader):
    props[f"models_{files.modelled}.raw"] = {"instrument_model": None}
    models = await service.instrument_models_of(files.files)
    assert models[files.modelled] == "Orbitrap Exploris 120"
