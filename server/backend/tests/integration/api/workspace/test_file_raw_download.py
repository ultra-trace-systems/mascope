"""
Tests: the raw-file download task behind ``POST /api/file/download``.

What it leaves in the user's temp directory: one file is copied there, more
are written into one zip there, and nothing else is. Each sample file of this
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
async def test_one_file_is_copied_under_its_own_name(
    stored, async_session_factory, sample_file
):
    (name,) = await _store(stored, async_session_factory, sample_file)

    result = await download_files(sample_file_ids=[sample_file], user_id=1)

    download = result["_notification_data"]["download"]
    assert download == f"{name}.raw"
    # The copy's working name is gone: the download is all the temp dir holds.
    assert os.listdir(stored) == [download]
    assert (stored / download).read_bytes() == name.encode()


@pytest.mark.asyncio
async def test_the_copy_is_written_off_the_event_loop(
    stored, async_session_factory, sample_file, monkeypatch
):
    # A single raw file can be gigabytes; copied on the loop's own thread, it
    # would hold every other request until done.
    await _store(stored, async_session_factory, sample_file)
    copy_into_place = service._copy_into_place
    threads = []

    def recording(*args, **kwargs):
        threads.append(threading.get_ident())
        return copy_into_place(*args, **kwargs)

    monkeypatch.setattr(service, "_copy_into_place", recording)
    await download_files(sample_file_ids=[sample_file], user_id=1)

    assert len(threads) == 1
    assert threads[0] != threading.get_ident()


@pytest.mark.asyncio
async def test_a_second_download_replaces_the_copy_whole(
    stored, async_session_factory, sample_file, monkeypatch
):
    # The second download of one file writes beside the first's copy and
    # moves over it: the name never holds a partial copy, nor none at all.
    (name,) = await _store(stored, async_session_factory, sample_file)
    await download_files(sample_file_ids=[sample_file], user_id=1)
    download = stored / f"{name}.raw"
    first = download.stat().st_ino
    seen = []
    copyfile = service.shutil.copyfile

    def watching(src, dst):
        seen.append((dst, download.read_bytes()))
        return copyfile(src, dst)

    monkeypatch.setattr(service.shutil, "copyfile", watching)
    await download_files(sample_file_ids=[sample_file], user_id=1)

    ((written, during),) = seen
    assert os.path.dirname(written) == str(stored)
    assert os.path.basename(written) != download.name
    assert during == name.encode()
    assert download.stat().st_ino != first
    assert os.listdir(stored) == [download.name]
    assert download.read_bytes() == name.encode()


@pytest.mark.asyncio
async def test_a_failed_copy_leaves_the_earlier_one(
    stored, async_session_factory, sample_file, monkeypatch
):
    (name,) = await _store(stored, async_session_factory, sample_file)
    await download_files(sample_file_ids=[sample_file], user_id=1)

    def unwritable(_src, dst):
        with open(dst, "wb") as partial:
            partial.write(b"half")
        raise OSError("no space left on device")

    monkeypatch.setattr(service.shutil, "copyfile", unwritable)
    with pytest.raises(OSError):
        await download_files(sample_file_ids=[sample_file], user_id=1)

    assert os.listdir(stored) == [f"{name}.raw"]
    assert (stored / f"{name}.raw").read_bytes() == name.encode()
