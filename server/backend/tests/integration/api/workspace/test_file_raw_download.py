"""
Tests: the raw-file download task behind ``POST /api/file/download``.

What it leaves in the user's temp directory: more than one file is written
into one zip there, and nothing else is; one file is not copied at all, but
named by its id for the web app to fetch from where it is stored
(``GET /api/sample/files/{id}/raw``, ``test_sample_file_raw_route.py``). Each sample file of this
module resolves to a raw file of its own under ``tmp_path``; the route's
access checks are ``test_file_download_access.py``'s.

Fixtures used:
- ``sample_file`` and ``beta_sample_file`` - two sample file records
"""

import datetime
import os
import threading
import zipfile
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from mascope_backend.api.lib.exceptions.api_exceptions import ApiException
from mascope_backend.api.new.file import service
from mascope_backend.db import SampleFile
from mascope_file import name as mascope_name


#: The task without its notification wrapper, which keeps a warning to the
#: notification it sends; the body raises it.
download_files = service.download_files.__wrapped__


@pytest.fixture
def stored(monkeypatch, tmp_path):
    """Resolve each sample file to a raw file of its own under ``tmp_path``,
    holding the file's name as its bytes, and the user's temp directory to
    ``tmp_path / "temp"``. Yields that temp directory."""
    store = tmp_path / "store"
    temp = tmp_path / "temp"
    temp.mkdir()
    monkeypatch.setattr(service, "get_instrument_type", lambda _name: "orbi")
    monkeypatch.setattr(
        service, "parse_path_from_item_filename", lambda name: str(store / name)
    )
    # The same for the single file, which the source data route's own lookup
    # finds.
    monkeypatch.setattr(mascope_name.runtime.meta, "filestore", str(store))
    monkeypatch.setattr(mascope_name, "get_sample_file_type", lambda _f: "orbi_raw")
    monkeypatch.setattr(
        mascope_name,
        "parse_path_from_item_filename",
        lambda name: os.path.join(str(store), name),
    )
    with patch(
        "mascope_backend.api.new.temp.storage.user_temp_dir",
        return_value=str(temp),
    ):
        yield temp


async def _filename(async_session_factory, sample_file_id) -> str:
    async with async_session_factory() as session:
        return (await session.get(SampleFile, sample_file_id)).filename


async def _store(stored, async_session_factory, *sample_file_ids) -> list[str]:
    """Write the raw file of each sample file; return their names."""
    names = []
    for sample_file_id in sample_file_ids:
        name = await _filename(async_session_factory, sample_file_id)
        folder = stored.parent / "store" / name
        folder.mkdir(parents=True)
        (folder / "data.raw").write_bytes(name.encode())
        names.append(name)
    return names


@pytest.mark.asyncio
async def test_two_files_leave_only_their_zip(
    stored, async_session_factory, sample_file, beta_sample_file
):
    names = await _store(stored, async_session_factory, sample_file, beta_sample_file)

    result = await download_files(
        sample_file_ids=[sample_file, beta_sample_file], user_id=1
    )

    download = result["_notification_data"]["download"]
    assert download.startswith("sample_files_") and download.endswith(".zip")
    # No folder of copies beside the zip: the zip is all the temp dir holds.
    assert os.listdir(stored) == [download]
    with zipfile.ZipFile(stored / download) as archive:
        assert sorted(archive.namelist()) == sorted(f"{n}.raw" for n in names)
        for name in names:
            assert archive.read(f"{name}.raw") == name.encode()


@pytest.mark.asyncio
async def test_the_zip_is_written_off_the_event_loop(
    stored, async_session_factory, sample_file, beta_sample_file, monkeypatch
):
    # A bundle of raw files is the slowest write the route makes; written on
    # the loop's own thread, it would hold every other request until done.
    await _store(stored, async_session_factory, sample_file, beta_sample_file)
    zip_files = service._zip_files
    threads = []

    def recording(*args, **kwargs):
        threads.append(threading.get_ident())
        return zip_files(*args, **kwargs)

    monkeypatch.setattr(service, "_zip_files", recording)
    await download_files(sample_file_ids=[sample_file, beta_sample_file], user_id=1)

    assert len(threads) == 1
    assert threads[0] != threading.get_ident()


@pytest.mark.asyncio
async def test_two_downloads_in_one_second_do_not_collide(
    stored, async_session_factory, sample_file, beta_sample_file, monkeypatch
):
    await _store(stored, async_session_factory, sample_file, beta_sample_file)

    class FrozenClock(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 4, 12, 0, 0)

    monkeypatch.setattr(service, "datetime", SimpleNamespace(datetime=FrozenClock))
    downloads = []
    for _ in range(2):
        result = await download_files(
            sample_file_ids=[sample_file, beta_sample_file], user_id=1
        )
        downloads.append(result["_notification_data"]["download"])
    first, second = downloads

    assert first != second
    assert first.startswith("sample_files_2026-11-04_120000_")
    assert sorted(os.listdir(stored)) == sorted([first, second])


@pytest.mark.asyncio
async def test_a_missing_file_is_left_out_of_the_zip(
    stored, async_session_factory, sample_file, beta_sample_file
):
    (name,) = await _store(stored, async_session_factory, sample_file)
    missing = await _filename(async_session_factory, beta_sample_file)

    with pytest.raises(ApiException) as warning:
        await download_files(sample_file_ids=[sample_file, beta_sample_file], user_id=1)

    data = warning.value.tech_message["data"]
    assert data["found"] == [name]
    assert data["not_found"] == [missing]
    assert os.listdir(stored) == [data["download"]]
    with zipfile.ZipFile(stored / data["download"]) as archive:
        assert archive.namelist() == [f"{name}.raw"]


@pytest.mark.asyncio
async def test_no_file_found_leaves_nothing_behind(
    stored, sample_file, beta_sample_file
):
    with pytest.raises(ApiException) as warning:
        await download_files(sample_file_ids=[sample_file, beta_sample_file], user_id=1)

    assert warning.value.tech_message["data"]["download"] is None
    assert os.listdir(stored) == []


@pytest.mark.asyncio
async def test_a_failed_zip_leaves_nothing_behind(
    stored, async_session_factory, sample_file, beta_sample_file, monkeypatch
):
    await _store(stored, async_session_factory, sample_file, beta_sample_file)

    def unwritable(*_args, **_kwargs):
        raise OSError("no space left on device")

    monkeypatch.setattr(service, "_zip_files", unwritable)
    with pytest.raises(OSError):
        await download_files(sample_file_ids=[sample_file, beta_sample_file], user_id=1)

    assert os.listdir(stored) == []


@pytest.mark.asyncio
async def test_one_file_is_named_by_its_id_and_not_copied(
    stored, async_session_factory, sample_file
):
    (name,) = await _store(stored, async_session_factory, sample_file)

    result = await download_files(sample_file_ids=[sample_file], user_id=1)

    notification = result["_notification_data"]
    assert notification == {"download": None, "source_download": sample_file}
    assert result["message"] == "File download: retrieved 1 file"
    assert [f["filename"] for f in result["data"]["found"]] == [name]
    assert result["data"]["not_found"] == []
    # Nothing is copied: the temp dir stays empty.
    assert os.listdir(stored) == []


@pytest.mark.asyncio
async def test_one_missing_file_is_a_warning_with_nothing_to_fetch(
    stored, async_session_factory, sample_file
):
    missing = await _filename(async_session_factory, sample_file)

    with pytest.raises(ApiException) as warning:
        await download_files(sample_file_ids=[sample_file], user_id=1)

    data = warning.value.tech_message["data"]
    assert data["download"] is None
    assert "source_download" not in data
    assert data["not_found"] == [missing]
    assert os.listdir(stored) == []


@pytest.mark.asyncio
async def test_one_file_whose_name_leaves_its_directory_is_not_found(
    stored, async_session_factory, sample_file
):
    # A name with a separator would lead out of the sample's own directory:
    # here to a raw file planted beside the store. It is not offered.
    (stored.parent / "store").mkdir()
    planted = stored.parent / "elsewhere"
    planted.mkdir()
    (planted / "data.raw").write_bytes(b"not this sample's")
    steered = "../elsewhere"
    assert os.path.isfile(os.path.join(stored.parent / "store", steered, "data.raw"))

    async with async_session_factory() as session:
        row = await session.get(SampleFile, sample_file)
        original = row.filename
        row.filename = steered
        await session.commit()
    try:
        with pytest.raises(ApiException) as warning:
            await download_files(sample_file_ids=[sample_file], user_id=1)
    finally:
        async with async_session_factory() as session:
            (await session.get(SampleFile, sample_file)).filename = original
            await session.commit()

    assert warning.value.tech_message["data"]["not_found"] == [steered]
    assert os.listdir(stored) == []


@pytest.mark.asyncio
async def test_one_file_whose_instrument_part_leaves_the_filestore_is_not_found(
    async_session_factory, sample_file, monkeypatch, tmp_path
):
    # Through the real layout, <filestore>/<instrument>/<date>/<name>: a ".."
    # instrument part leads beside the store, where a raw file is planted.
    store = tmp_path / "store"
    store.mkdir()
    temp = tmp_path / "temp"
    temp.mkdir()
    monkeypatch.setattr(mascope_name.runtime.meta, "filestore", str(store))
    monkeypatch.setattr(mascope_name, "get_sample_file_type", lambda _f: "orbi_raw")
    steered = ".._2024.01.01_12h00m00s"
    planted = tmp_path / "2024.01.01" / steered
    planted.mkdir(parents=True)
    (planted / "data.raw").write_bytes(b"not this sample's")
    assert os.path.isfile(mascope_name.filename_to_datafile_path(steered))

    async with async_session_factory() as session:
        row = await session.get(SampleFile, sample_file)
        original = row.filename
        row.filename = steered
        await session.commit()
    try:
        with (
            patch(
                "mascope_backend.api.new.temp.storage.user_temp_dir",
                return_value=str(temp),
            ),
            pytest.raises(ApiException) as warning,
        ):
            await download_files(sample_file_ids=[sample_file], user_id=1)
    finally:
        async with async_session_factory() as session:
            (await session.get(SampleFile, sample_file)).filename = original
            await session.commit()

    assert warning.value.tech_message["data"]["not_found"] == [steered]
    assert os.listdir(temp) == []


@pytest.mark.asyncio
async def test_one_file_whose_name_leads_into_another_samples_directory_is_not_found(
    async_session_factory, sample_file, monkeypatch, tmp_path
):
    # Through the real layout a leading "./" puts "instB" first, and the path
    # normalises to the directory of the sample named after the rest: inside
    # the filestore, so only the one-segment check refuses it.
    store = tmp_path / "store"
    temp = tmp_path / "temp"
    temp.mkdir()
    monkeypatch.setattr(mascope_name.runtime.meta, "filestore", str(store))
    monkeypatch.setattr(mascope_name, "get_sample_file_type", lambda _f: "orbi_raw")
    other = "instB_2024.01.01_13h00m00s"
    planted = store / "instB" / "2024.01.01" / other
    planted.mkdir(parents=True)
    (planted / "data.raw").write_bytes(b"another sample's")
    steered = f"./{other}"
    assert os.path.isfile(mascope_name.filename_to_datafile_path(steered))

    async with async_session_factory() as session:
        row = await session.get(SampleFile, sample_file)
        original = row.filename
        row.filename = steered
        await session.commit()
    try:
        with (
            patch(
                "mascope_backend.api.new.temp.storage.user_temp_dir",
                return_value=str(temp),
            ),
            pytest.raises(ApiException) as warning,
        ):
            await download_files(sample_file_ids=[sample_file], user_id=1)
    finally:
        async with async_session_factory() as session:
            (await session.get(SampleFile, sample_file)).filename = original
            await session.commit()

    assert warning.value.tech_message["data"]["not_found"] == [steered]
    assert os.listdir(temp) == []
