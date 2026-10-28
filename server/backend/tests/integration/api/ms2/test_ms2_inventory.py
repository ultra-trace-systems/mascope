"""
Integration tests: the group inventory, from a file's registration to the
routes that read it.

A shape of the hermetic MS2 fixture (``tests/ms2_fixture.py``) is converted,
its MS2 store written as the converter writes it, and the file registered as
the converter asks the server to (``create_sample_file``), which lists its
groups in ``ms2_group``. A batch then holds samples of it that read different
parts of it:

- ``whole``: its acquisition window, from its first scan to its last;
- ``from the first fragment scan``: a window that starts on the first scan
  of a group, so that group is held whole by the reader's widening alone;
- ``cut``: a window that ends inside every group's span;
- ``late start``: a window that starts on the scan ``cut`` ends on, so it
  holds the groups whose first scan is that one or later whole and cuts
  the others;
- ``from the first fragment scan on``: the window of ``from the first
  fragment scan`` with no end, so it holds every group whole by the
  widening alone;
- ``open end``: a window with no end that starts on the last scan of the
  first group, which it holds by the widening alone and cuts;
- ``no polarity, no window``: a sample that records neither, which holds
  every group of the file;
- ``other polarity``: the window of ``whole``, in a polarity the file has
  no scan in, whose summary is refused as the store and the raw read refuse
  it;
- ``survey scan alone``: the first scan's time alone.

Over HTTP, as a client reads them: the summary of each sample lists the
groups the samples listing counts, the batch's groups are one query whatever
the batch's size, the sample record carries its groups, a TOF sample's
summary says it is not read, and an MS1 batch lists nothing.

The routes read the ``sample_view`` database VIEW, which
``Base.metadata.create_all`` does not create, so it is created here as the
other integration packages create it for themselves.
"""

import contextlib
import os
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from fastapi import BackgroundTasks
from ms2_fixture import SHAPES, SOURCE_FILE, answers, convert, converted, scan_time_s
from sqlalchemy import delete, event, select, text, update
from test_utils import captured_logs

from mascope_backend.api.controllers.sample.files import sample_files_controller
from mascope_backend.api.models.sample.files.sample_file_pydantic_model import (
    SampleFileCreate,
)
from mascope_backend.api.new.ms2 import inventory
from mascope_backend.api.new.ms2 import service as ms2_service
from mascope_backend.db import (
    Dataset,
    Ms2Group,
    Sample,
    SampleBatch,
    SampleFile,
    SampleItem,
    Workspace,
    WorkspaceMember,
)
from mascope_backend.db.id import gen_id
from mascope_file.name import parse_path_from_item_filename
from mascope_signal import ms2_store


ANSWERS = answers()
_NOW = datetime(2026, 1, 9, 16, 43, tzinfo=timezone.utc)

#: The fixture's fragment scans that every group's span reaches past: a
#: window that ends on it cuts every group of every shape.
_CUT_AFTER_SCAN = 6


@pytest_asyncio.fixture(scope="session", autouse=True)
async def sample_view(async_engine):
    """Create the sample_view VIEW in the integration test database."""
    async with async_engine.begin() as conn:
        await conn.execute(text(Sample.drop_view()))
        await conn.execute(text(Sample.create_view()))


@pytest_asyncio.fixture(scope="module")
async def dataset(async_session_factory, test_users):
    """A dataset in a workspace every test user is a member of."""
    workspace_id, dataset_id = gen_id(), gen_id()
    async with async_session_factory() as session:
        session.add(
            Workspace(
                workspace_id=workspace_id,
                workspace_name=f"MS2 inventory {workspace_id}",
                workspace_status="active",
                workspace_utc_created=_NOW,
            )
        )
        for role_name, user in test_users.items():
            session.add(
                WorkspaceMember(
                    workspace_member_id=gen_id(),
                    workspace_id=workspace_id,
                    user_id=user.id,
                    workspace_role=role_name,
                    granted_at=_NOW,
                    granted_by=user.id,
                )
            )
        session.add(
            Dataset(
                dataset_id=dataset_id,
                workspace_id=workspace_id,
                dataset_name="MS2 inventory",
                dataset_utc_created=_NOW,
            )
        )
        await session.commit()
    yield dataset_id
    async with async_session_factory() as session:
        await session.execute(
            delete(Workspace).where(Workspace.workspace_id == workspace_id)
        )
        await session.commit()


@pytest.fixture(params=SHAPES)
def stored(request, tmp_path):
    """A shape converted, its store written; still presented while entered.

    Not async: the converter runs an event loop of its own.
    """
    with converted(request.param, tmp_path) as done:
        yield SimpleNamespace(
            shape=request.param,
            answers=ANSWERS["shapes"][request.param]["answers"],
            props=done.props,
            filename=done.props.filename,
        )


async def _batch(async_session_factory, dataset_id: str, items: dict) -> dict:
    """A batch of samples, each ``name: (sample_file_id, polarity, t0, t1)``.

    :return: The batch's id, and each sample's id by its name.
    """
    batch_id = gen_id()
    ids = {name: gen_id() for name in items}
    async with async_session_factory() as session:
        session.add(
            SampleBatch(
                sample_batch_id=batch_id,
                dataset_id=dataset_id,
                sample_batch_name=f"MS2 inventory {batch_id}",
                sample_batch_utc_created=_NOW,
            )
        )
        await session.flush()
        for name, (sample_file_id, polarity, t0, t1) in items.items():
            session.add(
                SampleItem(
                    sample_item_id=ids[name],
                    sample_batch_id=batch_id,
                    sample_file_id=sample_file_id,
                    sample_item_name=name,
                    sample_item_type="sample",
                    polarity=polarity,
                    t0=t0,
                    t1=t1,
                    sample_item_utc_created=_NOW,
                )
            )
        await session.commit()
    return {"batch": batch_id, **ids}


async def _register(props, instrument_type: str = "orbi") -> str:
    """Register a converted file as the converter asks the server to."""
    with (
        patch.object(sample_files_controller, "emit_record_created", AsyncMock()),
        patch.object(
            sample_files_controller, "create_acquisition_datasets", AsyncMock()
        ),
    ):
        created = await sample_files_controller.create_sample_file(
            SampleFileCreate(
                filename=props.filename,
                instrument=props.filename.split("_")[0],
                datetime=datetime.fromisoformat(props.timestamp),
                datetime_utc=_NOW,
                length=props.length,
                range=props.range,
                polarity=props.polarity,
                instrument_type=instrument_type,
            ),
            BackgroundTasks(),
        )
    return created["data"]["sample_file_id"]


def _open_end(stored) -> int:
    """The scan the ``open end`` sample starts on: the first group's last."""
    return max(stored.answers["groups"][0]["scans"])


@pytest_asyncio.fixture
async def registered(stored, dataset, async_session_factory):
    """The shape registered, and a batch of samples reading parts of it."""
    sample_file_id = await _register(stored.props)
    groups = stored.answers["groups"]
    first, last = scan_time_s(1), scan_time_s(stored.answers["scans"])
    first_fragment = scan_time_s(min(min(g["scans"]) for g in groups))
    ids = await _batch(
        async_session_factory,
        dataset,
        {
            "whole": (sample_file_id, "+", first, last),
            "from the first fragment scan": (sample_file_id, "+", first_fragment, last),
            "cut": (sample_file_id, "+", first, scan_time_s(_CUT_AFTER_SCAN)),
            "late start": (sample_file_id, "+", scan_time_s(_CUT_AFTER_SCAN), last),
            "from the first fragment scan on": (
                sample_file_id,
                "+",
                first_fragment,
                None,
            ),
            "open end": (sample_file_id, "+", scan_time_s(_open_end(stored)), None),
            "no polarity, no window": (sample_file_id, None, None, None),
            "other polarity": (sample_file_id, "-", first, last),
            "survey scan alone": (sample_file_id, "+", first, first),
        },
    )
    yield SimpleNamespace(**vars(stored), sample_file_id=sample_file_id, ids=ids)
    async with async_session_factory() as session:
        await session.execute(
            delete(SampleBatch).where(SampleBatch.sample_batch_id == ids["batch"])
        )
        await session.execute(
            delete(SampleFile).where(SampleFile.sample_file_id == sample_file_id)
        )
        await session.commit()


async def _rows(async_session_factory, sample_file_id: str) -> list[Ms2Group]:
    async with async_session_factory() as session:
        return list(
            (
                await session.execute(
                    select(Ms2Group)
                    .where(Ms2Group.sample_file_id == sample_file_id)
                    .order_by(Ms2Group.parent_peak_mz, Ms2Group.t_min)
                )
            ).scalars()
        )


async def _summary(client, sample_item_id: str) -> dict:
    response = await client.get(f"/api/samples/{sample_item_id}/ms2/summary")
    assert response.status_code == 200, response.text
    return response.json()


async def _listing(client, sample_batch_id: str) -> dict[str, dict]:
    response = await client.get(f"/api/samples?sample_batch_id={sample_batch_id}")
    assert response.status_code == 200, response.text
    return {sample["sample_item_name"]: sample for sample in response.json()["data"]}


async def _batch_groups(client, sample_batch_id: str) -> dict:
    response = await client.get(f"/api/sample/batches/{sample_batch_id}/ms2/groups")
    assert response.status_code == 200, response.text
    return response.json()


@contextlib.contextmanager
def _neither_store_nor_raw_summary():
    """Fail a summary read from the store or from the raw file."""
    with (
        patch.object(
            ms2_store.Ms2Store,
            "summary",
            side_effect=AssertionError("the store's summary was read"),
        ),
        patch.object(
            ms2_service.m_compute,
            "get_ms2_summary",
            AsyncMock(side_effect=AssertionError("the raw file was read")),
        ),
    ):
        yield


# --- written when the store is ----------------------------------------------------


@pytest.mark.asyncio
async def test_registering_a_file_lists_its_stores_groups(
    registered, async_session_factory
):
    rows = await _rows(async_session_factory, registered.sample_file_id)
    store = ms2_store.Ms2Store.open(registered.filename)

    assert [(r.polarity, r.precursor_key) for r in rows] == [
        ("+", ms2_store.group_key(g["parent_peak_mz"], g["activation"]))
        for g in registered.answers["groups"]
    ]
    listed = {(g["polarity"], g["key"]): g for g in store.inventory()}
    for row in rows:
        group = listed[(row.polarity, row.precursor_key)]
        assert {
            "parent_peak_mz": row.parent_peak_mz,
            "activation": row.activation,
            "activation_kind": row.activation_kind,
            "nce": row.nce,
            "hcd_energy": row.hcd_energy,
            "isolation_widths": row.isolation_widths,
            "scan_count": row.scan_count,
            "t_min": row.t_min,
            "t_max": row.t_max,
            "low_mass": row.low_mass,
            "high_mass": row.high_mass,
        } == {
            name: group[name]
            for name in group
            if name not in ("polarity", "key", "isolation_width")
        }
        assert row.activation_kind == "hcd"
        assert row.nce == float(row.activation[3:])
        assert row.located_precursor_mz is None


@pytest.mark.asyncio
async def test_a_file_whose_groups_cannot_be_listed_is_registered(
    stored, async_session_factory
):
    """Its store is read by its routes, and the backfill lists them."""
    with (
        patch.object(
            sample_files_controller,
            "write_ms2_inventory",
            AsyncMock(side_effect=OSError("gone")),
        ),
        captured_logs() as records,
    ):
        sample_file_id = await _register(stored.props)
    try:
        assert await _rows(async_session_factory, sample_file_id) == []
        (warning,) = [r for r in records if r["level"].name == "WARNING"]
        assert "Could not list the MS2 groups" in warning["message"]
    finally:
        async with async_session_factory() as session:
            await session.execute(
                delete(SampleFile).where(SampleFile.sample_file_id == sample_file_id)
            )
            await session.commit()


@pytest.mark.asyncio
async def test_a_file_whose_store_is_gone_lists_none(registered, async_session_factory):
    """What re-processing a file whose census holds no fragmentation stream
    leaves it: no store, and no group."""
    ms2_store.remove_ms2_store(registered.filename)

    listed = await inventory.write_ms2_inventory(
        registered.sample_file_id, registered.filename
    )

    assert listed == 0
    assert await _rows(async_session_factory, registered.sample_file_id) == []


@pytest.mark.asyncio
async def test_deleting_the_file_deletes_its_groups(stored, async_session_factory):
    sample_file_id = await _register(stored.props)
    assert await _rows(async_session_factory, sample_file_id)

    async with async_session_factory() as session:
        await session.execute(
            delete(SampleFile).where(SampleFile.sample_file_id == sample_file_id)
        )
        await session.commit()

    assert await _rows(async_session_factory, sample_file_id) == []


# --- the summary ---------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sample",
    ["whole", "from the first fragment scan", "from the first fragment scan on"],
)
async def test_a_sample_holding_its_groups_whole_is_summarised_from_the_inventory(
    registered, guest_client, async_session_factory, sample
):
    """Bit for bit the store's summary, with neither it nor the raw file
    read for the groups."""
    sample_item_id = registered.ids[sample]
    with _neither_store_nor_raw_summary():
        from_inventory = await _summary(guest_client, sample_item_id)
    async with async_session_factory() as session:
        await session.execute(
            delete(Ms2Group).where(Ms2Group.sample_file_id == registered.sample_file_id)
        )
        await session.commit()
    from_store = await _summary(guest_client, sample_item_id)

    assert from_inventory["source"] == "inventory"
    assert from_store["source"] == "store"
    assert from_inventory["data"] == from_store["data"]
    assert from_inventory["parent_peak_tolerance"] == ms2_store.PARENT_PEAK_TOLERANCE
    assert from_inventory["data"]["reason"] is None
    assert (
        from_inventory["data"]["ms2_scan_count"]
        == (registered.answers["ms2_scan_count"])
    )


@pytest.mark.asyncio
async def test_its_groups_are_the_inventorys_rows(
    registered, guest_client, async_session_factory
):
    """What a row says is what the summary says of its group."""
    first = registered.answers["groups"][0]
    key = ms2_store.group_key(first["parent_peak_mz"], first["activation"])
    async with async_session_factory() as session:
        await session.execute(
            update(Ms2Group)
            .where(
                Ms2Group.sample_file_id == registered.sample_file_id,
                Ms2Group.precursor_key == key,
            )
            .values(hcd_energy=[99.0], scan_count=99)
        )
        await session.commit()

    summary = await _summary(guest_client, registered.ids["whole"])

    assert summary["source"] == "inventory"
    assert summary["data"]["groups"][0]["hcd_energy"] == [99.0]
    assert summary["data"]["groups"][0]["scan_count"] == 99
    assert summary["data"]["hcd_energy_map"][str(first["parent_peak_mz"])][0] == 99.0


@pytest.mark.asyncio
async def test_a_window_that_cuts_a_group_is_summarised_from_the_store(
    registered, guest_client
):
    """The inventory holds each group over the whole file."""
    summary = await _summary(guest_client, registered.ids["cut"])

    assert summary["source"] == "store"
    assert summary["data"]["groups"]
    assert all(
        group["t_max"] <= scan_time_s(_CUT_AFTER_SCAN)
        for group in summary["data"]["groups"]
    )


@pytest.mark.asyncio
async def test_a_window_with_no_end_holds_a_group_that_ends_on_its_start(
    registered, guest_client
):
    """As the reader takes the scan the window starts on: the listing, the
    batch and the record list the group the summary does."""
    sample_item_id = registered.ids["open end"]
    first = registered.answers["groups"][0]
    key = ms2_store.group_key(first["parent_peak_mz"], first["activation"])

    summary = await _summary(guest_client, sample_item_id)
    listing = await _listing(guest_client, registered.ids["batch"])
    body = await _batch_groups(guest_client, registered.ids["batch"])
    record = await guest_client.get(f"/api/samples/{sample_item_id}")

    assert summary["source"] == "store"
    keys = sorted(
        ms2_store.group_key(g["parent_peak_mz"], g["activation"])
        for g in summary["data"]["groups"]
    )
    assert key in keys
    assert listing["open end"]["ms2_group_count"] == len(keys)
    assert (
        sorted(
            g["precursor_key"]
            for g in body["data"]
            if g["sample_item_id"] == sample_item_id
        )
        == keys
    )
    assert record.status_code == 200, record.text
    assert (
        sorted(g["precursor_key"] for g in record.json()["data"]["ms2_groups"]) == keys
    )


@pytest.mark.asyncio
async def test_an_inventory_out_of_step_with_the_store_is_not_read(
    registered, guest_client, async_session_factory
):
    """A group it does not list, or one the store does not hold: the store
    answers."""
    first = registered.answers["groups"][0]
    async with async_session_factory() as session:
        await session.execute(
            delete(Ms2Group).where(
                Ms2Group.sample_file_id == registered.sample_file_id,
                Ms2Group.precursor_key
                == ms2_store.group_key(first["parent_peak_mz"], first["activation"]),
            )
        )
        await session.commit()
    missing = await _summary(guest_client, registered.ids["whole"])

    await inventory.write_ms2_inventory(registered.sample_file_id, registered.filename)
    (row, *_) = await _rows(async_session_factory, registered.sample_file_id)
    async with async_session_factory() as session:
        session.add(_copy(row, precursor_key="999.0000@hcd10.00"))
        await session.commit()
    extra = await _summary(guest_client, registered.ids["whole"])

    assert missing["source"] == "store"
    assert extra["source"] == "store"
    assert len(extra["data"]["groups"]) == len(registered.answers["groups"])


def _copy(row: Ms2Group, **changed) -> Ms2Group:
    return Ms2Group(
        **{
            column.name: changed.get(column.name, getattr(row, column.name))
            for column in Ms2Group.__table__.columns
        }
    )


@pytest.mark.asyncio
async def test_a_raw_orbitrap_sample_with_no_group_says_none(registered, guest_client):
    summary = await _summary(guest_client, registered.ids["survey scan alone"])

    assert summary["source"] == "store"
    assert summary["data"]["groups"] == []
    assert summary["data"]["reason"] == "none"


@pytest.fixture
def ms1_only(tmp_path):
    """The committed MS1 file as it is, converted: no fragmentation scan, so
    no store and no group."""
    with (
        patch.dict(os.environ, {"MASCOPE_THERMO_BACKEND": "opentfraw"}),
        convert(SOURCE_FILE, tmp_path) as done,
    ):
        yield done.props


@pytest.mark.asyncio
async def test_so_does_a_raw_orbitrap_file_with_no_ms2_scan(
    ms1_only, dataset, async_session_factory, guest_client
):
    sample_file_id = await _register(ms1_only)
    ids = await _batch(
        async_session_factory, dataset, {"MS1": (sample_file_id, "+", None, None)}
    )
    try:
        summary = await _summary(guest_client, ids["MS1"])
        listing = await _listing(guest_client, ids["batch"])
    finally:
        async with async_session_factory() as session:
            await session.execute(
                delete(SampleBatch).where(SampleBatch.sample_batch_id == ids["batch"])
            )
            await session.execute(
                delete(SampleFile).where(SampleFile.sample_file_id == sample_file_id)
            )
            await session.commit()

    assert summary["source"] == "raw"
    assert summary["data"]["groups"] == []
    assert summary["data"]["ms2_scan_count"] == 0
    assert summary["data"]["reason"] == "none"
    assert listing["MS1"]["ms2_group_count"] == 0


# --- the listing, the record and the batch ---------------------------------------------


@pytest.mark.asyncio
async def test_the_listing_counts_the_groups_each_summary_lists(
    registered, guest_client
):
    listing = await _listing(guest_client, registered.ids["batch"])

    counts = {}
    for name, sample in listing.items():
        counts[name] = sample["ms2_group_count"]
        response = await guest_client.get(
            f"/api/samples/{sample['sample_item_id']}/ms2/summary"
        )
        if name == "other polarity":
            # Refused: its window holds no scan at all
            assert response.status_code == 400
            continue
        listed = response.json()["data"]["groups"]
        assert sample["ms2_group_count"] == len(listed), name
    groups = len(registered.answers["groups"])
    open_end = _open_end(registered)
    assert counts == {
        "whole": groups,
        "from the first fragment scan": groups,
        "cut": groups,
        "late start": groups,
        "from the first fragment scan on": groups,
        "open end": sum(
            max(g["scans"]) >= open_end for g in registered.answers["groups"]
        ),
        "no polarity, no window": groups,
        "other polarity": 0,
        "survey scan alone": 0,
    }


@pytest.mark.asyncio
async def test_the_batch_lists_each_samples_groups(registered, guest_client):
    body = await _batch_groups(guest_client, registered.ids["batch"])
    store = ms2_store.Ms2Store.open(registered.filename)
    listed = {g["key"]: g for g in store.inventory()}
    held = {}
    for group in body["data"]:
        held.setdefault(group["sample_item_id"], []).append(group)

    groups = len(registered.answers["groups"])
    open_end = _open_end(registered)
    in_open_end = [
        g for g in registered.answers["groups"] if max(g["scans"]) >= open_end
    ]
    assert body["results"] == 6 * groups + len(in_open_end)
    by_name = {name: held.get(i, []) for name, i in registered.ids.items()}
    for whole in (
        "whole",
        "from the first fragment scan",
        "from the first fragment scan on",
        "no polarity, no window",
    ):
        assert [g["whole"] for g in by_name[whole]] == [True] * groups, whole
    assert [g["whole"] for g in by_name["cut"]] == [False] * groups
    assert [g["whole"] for g in by_name["late start"]] == [
        min(g["scans"]) >= _CUT_AFTER_SCAN for g in registered.answers["groups"]
    ]
    assert [g["whole"] for g in by_name["open end"]] == [
        min(g["scans"]) >= open_end for g in in_open_end
    ]
    assert by_name["other polarity"] == by_name["survey scan alone"] == []
    for group in by_name["whole"]:
        stored = listed[group["precursor_key"]]
        assert group == {
            "sample_item_id": registered.ids["whole"],
            "sample_file_id": registered.sample_file_id,
            "polarity": "+",
            "precursor_key": stored["key"],
            **{
                name: stored[name] for name in stored if name not in ("polarity", "key")
            },
            "located_precursor_mz": None,
            "whole": True,
        }
    # One sample's groups together, in the summary's order
    runs = [g["sample_item_id"] for g in body["data"]]
    assert [i for n, i in enumerate(runs) if n == 0 or runs[n - 1] != i] == list(
        dict.fromkeys(runs)
    )
    assert [g["precursor_key"] for g in by_name["whole"]] == [
        ms2_store.group_key(g["parent_peak_mz"], g["activation"])
        for g in registered.answers["groups"]
    ]


@pytest.mark.asyncio
async def test_the_sample_record_carries_its_groups(registered, guest_client):
    body = await _batch_groups(guest_client, registered.ids["batch"])

    for name in ("whole", "cut", "other polarity"):
        sample_item_id = registered.ids[name]
        response = await guest_client.get(f"/api/samples/{sample_item_id}")
        assert response.status_code == 200, response.text
        assert response.json()["data"]["ms2_groups"] == [
            {k: v for k, v in group.items() if k != "sample_item_id"}
            for group in body["data"]
            if group["sample_item_id"] == sample_item_id
        ]


@pytest.mark.asyncio
async def test_a_tof_sample_is_said_not_to_be_read(
    stored, dataset, async_session_factory, guest_client
):
    """Its summary answers, with the reason, and lists no group; it used
    to fail with a 500."""
    tof = stored.filename.replace("-17h43m57s_", "-17h45m57s_")
    sample_dir = parse_path_from_item_filename(tof)
    os.makedirs(sample_dir)
    open(os.path.join(sample_dir, "data.h5"), "wb").close()
    sample_file_id = gen_id()
    async with async_session_factory() as session:
        session.add(
            SampleFile(
                sample_file_id=sample_file_id,
                filename=tof,
                instrument=tof.split("_")[0],
                instrument_type="tof",
                datetime=datetime(2026, 1, 9, 17, 45),
                datetime_utc=_NOW,
                length=60.0,
                range=[40.0, 600.0],
                polarity="+",
            )
        )
        await session.commit()
    ids = await _batch(
        async_session_factory, dataset, {"tof": (sample_file_id, "+", 0.0, 60.0)}
    )
    try:
        summary = await _summary(guest_client, ids["tof"])
        listing = await _listing(guest_client, ids["batch"])
        body = await _batch_groups(guest_client, ids["batch"])
    finally:
        async with async_session_factory() as session:
            await session.execute(
                delete(SampleBatch).where(SampleBatch.sample_batch_id == ids["batch"])
            )
            await session.execute(
                delete(SampleFile).where(SampleFile.sample_file_id == sample_file_id)
            )
            await session.commit()

    assert summary["source"] == "inventory"
    assert summary["data"]["reason"] == "unsupported"
    assert summary["data"]["groups"] == []
    assert summary["data"]["ms1_scan_count"] is None
    assert summary["data"]["ms2_scan_count"] == 0
    assert listing["tof"]["ms2_group_count"] == 0
    assert body == {"message": body["message"], "results": 0, "data": []}


# --- one query for a batch -------------------------------------------------------------


def _group_row(sample_file_id: str, mz: float, t_min: float, t_max: float) -> dict:
    return {
        "sample_file_id": sample_file_id,
        "polarity": "+",
        "precursor_key": ms2_store.group_key(mz, "hcd20.00"),
        "parent_peak_mz": mz,
        "activation": "hcd20.00",
        "activation_kind": "hcd",
        "nce": 20.0,
        "hcd_energy": [7.8],
        "isolation_widths": [{"isolation_width": 1.0, "scan_count": 3}],
        "scan_count": 3,
        "t_min": t_min,
        "t_max": t_max,
        "low_mass": 40.0,
        "high_mass": 200.0,
    }


@pytest_asyncio.fixture
async def many_files(dataset, async_session_factory):
    """A batch of ``N`` samples of files listing groups, and of as many with
    none: rows written straight, as the inventory reads them."""
    n = 12
    files = [gen_id() for _ in range(2 * n)]
    async with async_session_factory() as session:
        for i, sample_file_id in enumerate(files):
            session.add(
                SampleFile(
                    sample_file_id=sample_file_id,
                    filename=f"ms2-inventory_{sample_file_id}.raw",
                    instrument="ms2-inventory",
                    instrument_type="orbi",
                    datetime=datetime(2026, 1, 9, 17, i),
                    datetime_utc=_NOW,
                    length=60.0,
                    range=[40.0, 600.0],
                    polarity="+",
                )
            )
        await session.flush()
        for sample_file_id in files[:n]:
            for mz in (137.096, 195.0877, 200.5):
                session.add(Ms2Group(**_group_row(sample_file_id, mz, 10.0, 50.0)))
        # One precursor of the first file stepped down in energy: acquired
        # in another order than its keys sort in
        for activation, t_min in (("hcd40.00", 11.0), ("hcd20.00", 12.0)):
            session.add(
                Ms2Group(
                    **{
                        **_group_row(files[0], 300.25, t_min, 50.0),
                        "precursor_key": ms2_store.group_key(300.25, activation),
                        "activation": activation,
                    }
                )
            )
        await session.commit()
    ids = await _batch(
        async_session_factory,
        dataset,
        {f"sample {i}": (f, "+", 0.0, 60.0) for i, f in enumerate(files)},
    )
    ms1 = await _batch(
        async_session_factory,
        dataset,
        {f"MS1 sample {i}": (f, "+", 0.0, 60.0) for i, f in enumerate(files[n:])},
    )
    yield SimpleNamespace(
        n=n, batch=ids["batch"], ms1_batch=ms1["batch"], first=ids["sample 0"]
    )
    async with async_session_factory() as session:
        await session.execute(
            delete(SampleBatch).where(
                SampleBatch.sample_batch_id.in_([ids["batch"], ms1["batch"]])
            )
        )
        await session.execute(
            delete(SampleFile).where(SampleFile.sample_file_id.in_(files))
        )
        await session.commit()


@contextlib.contextmanager
def _statements(async_engine):
    """The SQL statements run while entered."""
    statements: list[str] = []

    def before(_conn, _cursor, statement, *_args):
        statements.append(statement)

    event.listen(async_engine.sync_engine, "before_cursor_execute", before)
    try:
        yield statements
    finally:
        event.remove(async_engine.sync_engine, "before_cursor_execute", before)


@pytest.mark.asyncio
async def test_a_batch_answers_its_groups_in_one_query(many_files, async_engine):
    with _statements(async_engine) as statements:
        groups = await inventory.batch_ms2_groups(many_files.batch)

    assert len(groups) == 3 * many_files.n + 2
    assert len({g["sample_item_id"] for g in groups}) == many_files.n
    reads = [s for s in statements if "ms2_group" in s]
    assert len(reads) == 1
    assert len([s for s in statements if s.lstrip().upper().startswith("SELECT")]) == 1


@pytest.mark.asyncio
async def test_a_samples_groups_are_by_parent_peak_then_by_acquisition(
    many_files, guest_client
):
    body = await _batch_groups(guest_client, many_files.batch)

    assert [
        g["precursor_key"]
        for g in body["data"]
        if g["sample_item_id"] == many_files.first
    ] == [
        "137.0960@hcd20.00",
        "195.0877@hcd20.00",
        "200.5000@hcd20.00",
        "300.2500@hcd40.00",
        "300.2500@hcd20.00",
    ]


@pytest.mark.asyncio
async def test_an_ms1_batch_answers_an_empty_list(many_files, guest_client):
    body = await _batch_groups(guest_client, many_files.ms1_batch)
    listing = await _listing(guest_client, many_files.ms1_batch)

    assert body["results"] == 0
    assert body["data"] == []
    assert {s["ms2_group_count"] for s in listing.values()} == {0}
