"""
Tests: the mzML export, ``POST /api/file/mzml`` and the task behind it.

The route is held to the access its two kinds of id need: a sample file the
access downloading it needs, a sample guest access to its workspace. The task
then writes real files: every sample file of this module resolves to the
committed KORBI raw file (two positive MS1 scans, at about 8 s and 16 s), so a
sample's window is seen to pick its scans by its own t0, t1 and polarity, and
the file is seen to name the instrument in the vocabulary's terms. What
a spectrum holds is ``mascope_thermo``'s to test (``tests/test_mzml.py``).

Fixtures used:
- ``sample_file`` - linked to ``alpha_item`` (ws_alpha, all users are members)
- ``beta_sample_file`` - linked to ``beta_item`` (ws_beta, only owner is member)
- ``orphan_sample_file`` - no sample item references
"""

import datetime
import os
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import delete

from mascope_backend.api.lib.exceptions.api_exceptions import (
    ApiException,
    NotFoundException,
)
from mascope_backend.api.new.file import service
from mascope_backend.db import SampleFile, SampleItem
from mascope_backend.db.id import gen_id


_REPO_ROOT = Path(__file__).resolve().parents[6]
KORBI_POS = (
    _REPO_ROOT / "libraries/thermo/tests/test_files/KORBI2_AMB_POS_20260109174345.raw"
)
NS = {"m": "http://psi.hupo.org/ms/mzml"}

#: The task without its notification wrapper, which keeps a warning to the
#: notification it sends; the body raises it.
export_mzml = service.export_mzml.__wrapped__


def _scans(path) -> list[int]:
    """The scan numbers of an mzML file's spectra, in order."""
    root = ET.parse(path).getroot()
    return [
        int(spectrum.get("id").rsplit("scan=", 1)[1])
        for spectrum in root.findall("m:run/m:spectrumList/m:spectrum", NS)
    ]


def _instrument_configurations(path) -> list[dict]:
    """Each instrument configuration of an mzML file: its model's accession
    and its components' (tag, accession)."""
    root = ET.parse(path).getroot()
    return [
        {
            "model": [p.get("accession") for p in ic.findall("m:cvParam", NS)],
            "components": [
                (c.tag.split("}")[1], c.find("m:cvParam", NS).get("accession"))
                for c in ic.findall("m:componentList/*", NS)
            ],
        }
        for ic in root.findall(
            "m:instrumentConfigurationList/m:instrumentConfiguration", NS
        )
    ]


#: The committed raw file's instrument, an Orbitrap Exploris 120, as the
#: vocabulary gives it (OpenTFRaw's own table once wrote MS:1003199 for it).
EXPLORIS_120 = {
    "model": ["MS:1003095"],
    "components": [
        ("source", "MS:1000071"),
        ("analyzer", "MS:1000484"),
        ("detector", "MS:1000624"),
    ],
}


# ============= The route: access =============


@pytest.mark.asyncio
async def test_a_member_exports_a_file_and_a_sample(
    guest_client, sample_file, alpha_item
):
    resp = await guest_client.post(
        "/api/file/mzml",
        json={"sample_file_ids": [sample_file], "sample_item_ids": [alpha_item]},
    )
    assert resp.status_code == 202


@pytest.mark.asyncio
async def test_a_file_of_another_workspace_is_refused(
    guest_client, beta_sample_file, beta_item
):
    resp = await guest_client.post(
        "/api/file/mzml", json={"sample_file_ids": [beta_sample_file]}
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_a_sample_of_another_workspace_is_refused(guest_client, beta_item):
    resp = await guest_client.post(
        "/api/file/mzml", json={"sample_item_ids": [beta_item]}
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_one_refused_sample_refuses_the_request(
    guest_client, alpha_item, beta_item
):
    resp = await guest_client.post(
        "/api/file/mzml", json={"sample_item_ids": [alpha_item, beta_item]}
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_an_orphan_file_is_refused(guest_client, orphan_sample_file):
    resp = await guest_client.post(
        "/api/file/mzml", json={"sample_file_ids": [orphan_sample_file]}
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_an_outsider_is_refused_a_sample(outsider_client, alpha_item):
    resp = await outsider_client.post(
        "/api/file/mzml", json={"sample_item_ids": [alpha_item]}
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_a_superuser_exports_any_sample(owner_client, beta_item):
    resp = await owner_client.post(
        "/api/file/mzml", json={"sample_item_ids": [beta_item]}
    )
    assert resp.status_code == 202


@pytest.mark.asyncio
async def test_a_request_naming_nothing_is_invalid(guest_client):
    resp = await guest_client.post("/api/file/mzml", json={})
    assert resp.status_code == 422


# ============= The task: what it writes =============


@pytest.fixture
def raw_file(monkeypatch, tmp_path):
    """Resolve every sample file to the committed raw file, and the user's
    temp directory to ``tmp_path``."""
    monkeypatch.delenv("MASCOPE_THERMO_BACKEND", raising=False)
    monkeypatch.setattr(service, "get_sample_file_type", lambda _name: "orbi_raw")
    monkeypatch.setattr(
        service, "filename_to_datafile_path", lambda _name: str(KORBI_POS)
    )
    with patch(
        "mascope_backend.api.new.temp.storage.user_temp_dir",
        return_value=str(tmp_path),
    ):
        yield tmp_path


@pytest_asyncio.fixture
async def make_sample(async_session_factory, alpha_batch, sample_file):
    """Make samples of ``sample_file`` with the window a test gives them."""
    made = []

    async def make(t0, t1, polarity="+"):
        item_id = gen_id()
        async with async_session_factory() as session:
            session.add(
                SampleItem(
                    sample_item_id=item_id,
                    sample_batch_id=alpha_batch,
                    sample_file_id=sample_file,
                    sample_item_name="Window",
                    sample_item_type="ANALYSIS",
                    sample_item_attributes={},
                    polarity=polarity,
                    t0=t0,
                    t1=t1,
                )
            )
            await session.commit()
        made.append(item_id)
        return item_id

    yield make
    async with async_session_factory() as session:
        await session.execute(
            delete(SampleItem).where(SampleItem.sample_item_id.in_(made))
        )
        await session.commit()


async def _filename(async_session_factory, sample_file_id) -> str:
    async with async_session_factory() as session:
        return (await session.get(SampleFile, sample_file_id)).filename


@pytest.mark.asyncio
async def test_a_file_is_written_whole(raw_file, async_session_factory, sample_file):
    result = await export_mzml(
        sample_file_ids=[sample_file], sample_item_ids=[], user_id=1
    )

    name = f"{await _filename(async_session_factory, sample_file)}.mzML"
    assert result["_notification_data"]["download"] == name
    assert result["data"] == {"found": [name], "not_found": []}
    assert _scans(raw_file / name) == [1, 2]
    configurations = _instrument_configurations(raw_file / name)
    assert configurations and all(c == EXPLORIS_120 for c in configurations)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("t0", "t1", "scans"),
    [(12.0, 60.0, [2]), (0.0, 12.0, [1]), (0.0, 60.0, [1, 2])],
)
async def test_a_sample_is_written_as_its_window(
    raw_file, async_session_factory, sample_file, make_sample, t0, t1, scans
):
    item_id = await make_sample(t0, t1)

    result = await export_mzml(sample_file_ids=[], sample_item_ids=[item_id], user_id=1)

    name = f"{await _filename(async_session_factory, sample_file)}.{item_id}.mzML"
    assert result["_notification_data"]["download"] == name
    assert _scans(raw_file / name) == scans
    configurations = _instrument_configurations(raw_file / name)
    assert configurations and all(c == EXPLORIS_120 for c in configurations)


@pytest.mark.asyncio
async def test_a_sample_of_the_other_polarity_is_skipped(
    raw_file, async_session_factory, sample_file, make_sample
):
    item_id = await make_sample(0.0, 60.0, polarity="-")

    with pytest.raises(ApiException) as warning:
        await export_mzml(sample_file_ids=[], sample_item_ids=[item_id], user_id=1)

    name = f"{await _filename(async_session_factory, sample_file)}.{item_id}.mzML"
    assert warning.value.tech_message["data"] == {
        "download": None,
        "found": [],
        "not_found": [name],
    }
    assert os.listdir(raw_file) == []


@pytest.mark.asyncio
async def test_more_than_one_is_zipped(
    raw_file, async_session_factory, sample_file, make_sample
):
    item_id = await make_sample(12.0, 60.0)

    result = await export_mzml(
        sample_file_ids=[sample_file], sample_item_ids=[item_id], user_id=1
    )

    filename = await _filename(async_session_factory, sample_file)
    download = result["_notification_data"]["download"]
    assert download.startswith("mzml_") and download.endswith(".zip")
    # The folder the files were written into is gone; only the zip remains.
    assert os.listdir(raw_file) == [download]
    with zipfile.ZipFile(raw_file / download) as archive:
        assert sorted(archive.namelist()) == sorted(
            [f"{filename}.mzML", f"{filename}.{item_id}.mzML"]
        )
        archive.extractall(raw_file / "out")
    assert _scans(raw_file / "out" / f"{filename}.{item_id}.mzML") == [2]


@pytest.mark.asyncio
async def test_two_zipped_exports_in_one_second_do_not_collide(
    raw_file, sample_file, make_sample, monkeypatch
):
    item_id = await make_sample(12.0, 60.0)

    class FrozenClock(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 23, 12, 0, 0)

    monkeypatch.setattr(service, "datetime", SimpleNamespace(datetime=FrozenClock))
    downloads = []
    for _ in range(2):
        result = await export_mzml(
            sample_file_ids=[sample_file], sample_item_ids=[item_id], user_id=1
        )
        downloads.append(result["_notification_data"]["download"])
    first, second = downloads

    assert first != second
    assert first.startswith("mzml_2026-10-23_120000_")
    assert sorted(os.listdir(raw_file)) == sorted([first, second])


@pytest.mark.asyncio
async def test_a_failed_write_leaves_no_folder_behind(
    raw_file, sample_file, make_sample, monkeypatch
):
    item_id = await make_sample(12.0, 60.0)

    def unreadable(*_args, **_kwargs):
        raise OSError("a scan could not be decoded")

    monkeypatch.setattr(service, "write_mzml", unreadable)
    with pytest.raises(OSError):
        await export_mzml(
            sample_file_ids=[sample_file], sample_item_ids=[item_id], user_id=1
        )

    assert os.listdir(raw_file) == []


@pytest.mark.asyncio
async def test_a_file_that_is_not_thermo_raw_is_skipped(
    raw_file, async_session_factory, sample_file, monkeypatch
):
    monkeypatch.setattr(service, "get_sample_file_type", lambda _name: "tof_h5")

    with pytest.raises(ApiException) as warning:
        await export_mzml(sample_file_ids=[sample_file], sample_item_ids=[], user_id=1)

    name = f"{await _filename(async_session_factory, sample_file)}.mzML"
    assert warning.value.tech_message["data"]["not_found"] == [name]
    assert os.listdir(raw_file) == []


@pytest.mark.asyncio
async def test_ids_that_find_nothing_are_not_found(raw_file):
    with pytest.raises(NotFoundException):
        await export_mzml(
            sample_file_ids=["missing"], sample_item_ids=["missing"], user_id=1
        )
