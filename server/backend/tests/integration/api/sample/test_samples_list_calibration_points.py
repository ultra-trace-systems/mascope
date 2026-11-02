"""
Integration tests: a sample list leaves a TOF fit's calibration points out.

A TOF fit's record, ``sample_file.mz_calibration``, lists in
``quality.points`` every calibrant line the fit chose from. ``GET
/api/samples`` has no pagination, and put the whole record in every row, so
a batch's list carried the points of every TOF sample in it. The list keeps
what the badge reads (the counts, the errors, ``span``, ``segments``) and
leaves the points out; ``GET /api/calibration/mz_calibration`` still answers
the whole record, and the stored record is not changed.

Run against the real routes and a real database, because the record reaches
the list through ``sample_view``.
"""

import copy
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, text

from mascope_backend.db import (
    Dataset,
    IonizationMode,
    Sample,
    SampleBatch,
    SampleFile,
    SampleItem,
    Workspace,
    WorkspaceMember,
)
from mascope_backend.db.id import gen_id


_NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)

_POINTS = [
    {
        "ion": "C2H3O2-",
        "target_mz": 59.0139,
        "observed_mz": 59.0141,
        "residual_ppm": 0.4,
        "snr": 812.0,
        "retained": True,
    },
    {
        "ion": "NO3-",
        "target_mz": 61.9884,
        "observed_mz": 61.9890,
        "residual_ppm": 9.7,
        "snr": 40.5,
        "retained": False,
    },
]

#: A TOF fit's record, as the fit stores it since the points were added.
_TOF_RECORD = {
    "mode": 2,
    "par": [2800.0, -0.5, 0.5],
    "verified": True,
    "quality": {
        "status": "pass",
        "n_points": 1,
        "mean_abs_error_ppm": 0.4,
        "points": _POINTS,
        "span": [59.0139, 59.0139],
        "collection": {"id": "collection", "isotopes_sha256": "0" * 64},
        "model": {"mode": 2, "equation": "m/z = ((t - p2) / p1) ** (1 / p3)"},
        "node_version": 1,
        "segments": [{"key": "all", "label": "All", "quality": None}],
    },
}

#: An Orbitrap fit's record: no points to leave out.
_ORBI_RECORD = {
    "mode": "linear",
    "par": [1.0, 0.0],
    "quality": {"status": "pass", "n_points": 5, "mean_abs_error_ppm": 0.8},
}

#: Each seeded sample's record, by the name the tests know it under.
_RECORDS = {"tof": _TOF_RECORD, "orbi": _ORBI_RECORD, "none": None}


@pytest_asyncio.fixture(scope="module", autouse=True)
async def points_sample_view(async_engine):
    """``sample_view``, which ``Base.metadata.create_all`` does not create."""
    async with async_engine.begin() as conn:
        await conn.execute(text(Sample.drop_view()))
        await conn.execute(text(Sample.create_view()))


@pytest_asyncio.fixture
async def seeded(async_session_factory, test_users):
    """A batch the editor may read, one sample per calibration record."""
    ids = {
        name: gen_id(16)
        for name in ("workspace", "dataset", "batch", "mode", *_RECORDS)
    }
    files = {name: gen_id(16) for name in _RECORDS}
    async with async_session_factory() as session:
        session.add(
            Workspace(
                workspace_id=ids["workspace"],
                workspace_name=f"Calibration points {ids['workspace']}",
                workspace_status="active",
            )
        )
        await session.flush()
        session.add(
            WorkspaceMember(
                workspace_member_id=gen_id(16),
                workspace_id=ids["workspace"],
                user_id=test_users["editor"].id,
                workspace_role="editor",
            )
        )
        session.add(
            Dataset(
                dataset_id=ids["dataset"],
                workspace_id=ids["workspace"],
                dataset_name="Calibration points",
                dataset_type="ANALYSIS",
                dataset_utc_created=_NOW,
            )
        )
        session.add(
            SampleBatch(
                sample_batch_id=ids["batch"],
                dataset_id=ids["dataset"],
                sample_batch_name="Calibration points",
                sample_batch_utc_created=_NOW,
            )
        )
        session.add(
            IonizationMode(
                ionization_mode_id=ids["mode"],
                ionization_mode_name=f"Calibration points {ids['mode']}",
                ionization_mode_token=None,
                ionization_mode_polarity="-",
                ionization_mechanism_ids=[],
            )
        )
        for name, record in _RECORDS.items():
            session.add(
                SampleFile(
                    sample_file_id=files[name],
                    filename=f"calibration-points_{files[name]}",
                    instrument="calibration-points",
                    instrument_type="orbi" if name == "orbi" else "tof",
                    datetime=_NOW.replace(tzinfo=None),
                    datetime_utc=_NOW,
                    length=60.0,
                    range=[50.0, 500.0],
                    polarity="-",
                    mz_calibration=copy.deepcopy(record),
                )
            )
        await session.flush()
        for name in _RECORDS:
            session.add(
                SampleItem(
                    sample_item_id=ids[name],
                    sample_batch_id=ids["batch"],
                    sample_file_id=files[name],
                    sample_item_name=f"Calibration points {name}",
                    sample_item_type="SAMPLE",
                    sample_item_attributes={},
                    polarity="-",
                    ionization_mode_id=ids["mode"],
                    tic=1000.0,
                    t0=0.0,
                    t1=60.0,
                    sample_item_utc_created=_NOW,
                )
            )
        await session.commit()
    yield ids | {"files": files}
    async with async_session_factory() as session:
        # The workspace takes its dataset, batch and items with it.
        await session.execute(
            delete(Workspace).where(Workspace.workspace_id == ids["workspace"])
        )
        await session.execute(
            delete(SampleFile).where(SampleFile.sample_file_id.in_(files.values()))
        )
        await session.execute(
            delete(IonizationMode).where(
                IonizationMode.ionization_mode_id == ids["mode"]
            )
        )
        await session.commit()


async def _listed(client, ids: dict) -> dict[str, dict | None]:
    """Each of the batch's samples' ``mz_calibration``, as the list answers it."""
    response = await client.get(
        "/api/samples", params={"sample_batch_id": ids["batch"]}
    )
    assert response.status_code == 200, response.text
    by_id = {ids[name]: name for name in _RECORDS}
    return {
        by_id[row["sample_item_id"]]: row["mz_calibration"]
        for row in response.json()["data"]
    }


@pytest.mark.asyncio
async def test_the_list_leaves_out_a_tof_fits_points(editor_client, seeded):
    listed = await _listed(editor_client, seeded)

    assert "points" not in listed["tof"]["quality"]


@pytest.mark.asyncio
async def test_the_list_keeps_the_rest_of_the_record(editor_client, seeded):
    listed = await _listed(editor_client, seeded)

    expected = copy.deepcopy(_TOF_RECORD)
    del expected["quality"]["points"]
    assert listed["tof"] == expected


@pytest.mark.asyncio
async def test_a_record_without_points_is_listed_whole(editor_client, seeded):
    listed = await _listed(editor_client, seeded)

    assert listed["orbi"] == _ORBI_RECORD
    assert listed["none"] is None


@pytest.mark.asyncio
async def test_the_single_sample_route_still_answers_the_points(editor_client, seeded):
    await _listed(editor_client, seeded)
    response = await editor_client.get(
        "/api/calibration/mz_calibration",
        params={"sample_item_id": seeded["tof"]},
    )

    assert response.status_code == 200, response.text
    record = response.json()["data"]["mz_calibration"]
    assert record["quality"]["points"] == _POINTS


@pytest.mark.asyncio
async def test_listing_leaves_the_stored_record_as_it_was(
    editor_client, seeded, async_session_factory
):
    await _listed(editor_client, seeded)
    async with async_session_factory() as session:
        stored = await session.scalar(
            select(SampleFile.mz_calibration).where(
                SampleFile.sample_file_id == seeded["files"]["tof"]
            )
        )

    assert stored == _TOF_RECORD
