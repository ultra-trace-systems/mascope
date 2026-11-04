"""
The MS2 readings route as a client calls it over HTTP.

``GET /api/samples/{id}/ms2/readings`` takes the group and its window ions in
the query string. The unit tests in ``tests/unit/api/ms2/test_ms2_group_readings.py``
call the service directly; these go through the route, so the query string is
parsed, the permission check runs and the body is JSON-encoded the way a
client sees it. The file read is the hermetic MS2 fixture's stepped shape,
converted (``tests/ms2_fixture.py``).
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from ms2_fixture import answers, converted

from mascope_backend.db import (
    Dataset,
    SampleBatch,
    SampleFile,
    SampleItem,
    Workspace,
    WorkspaceMember,
)
from mascope_backend.db.id import gen_id


_SERVICE = "mascope_backend.api.new.ms2.service"
PRECURSOR = answers()["precursors"]["A"]
IONS = f"{PRECURSOR['formula']},{PRECURSOR['co_isolate']['formula']}"
GROUP = "195.0877@hcd20.00"


@pytest.fixture(scope="module")
def stepped(tmp_path_factory):
    """The stepped shape, converted into a filestore of its own."""
    with converted("stepped", tmp_path_factory.mktemp("filestore")) as done:
        yield done.props.filename


@pytest_asyncio.fixture(scope="module")
async def readings_sample_id(async_session_factory, test_users) -> str:
    """A sample in a workspace every test user is a member of."""
    now = datetime.now(timezone.utc)
    ids = {name: gen_id() for name in ("workspace", "dataset", "batch", "file")}
    sample_item_id = gen_id()
    async with async_session_factory() as session:
        session.add(
            Workspace(
                workspace_id=ids["workspace"],
                workspace_name="MS2 Readings Route Test Workspace",
                workspace_status="active",
                workspace_utc_created=now,
                workspace_utc_modified=now,
            )
        )
        for role_name, user in test_users.items():
            session.add(
                WorkspaceMember(
                    workspace_member_id=gen_id(),
                    workspace_id=ids["workspace"],
                    user_id=user.id,
                    workspace_role=role_name,
                    granted_at=now,
                    granted_by=user.id,
                )
            )
        session.add(
            Dataset(
                dataset_id=ids["dataset"],
                workspace_id=ids["workspace"],
                dataset_name="MS2 Readings Route Test Dataset",
                dataset_utc_created=now,
            )
        )
        session.add(
            SampleBatch(
                sample_batch_id=ids["batch"],
                dataset_id=ids["dataset"],
                sample_batch_name="MS2 Readings Route Test Batch",
                sample_batch_utc_created=now,
            )
        )
        session.add(
            SampleFile(
                sample_file_id=ids["file"],
                filename=f"ms2-readings-route-test-{ids['file']}.raw",
                instrument="orbi-test",
                datetime=datetime(2026, 1, 1, 12, 0, 0),
                datetime_utc=now,
                length=60.0,
                range=[50.0, 500.0],
                polarity="+",
            )
        )
        session.add(
            SampleItem(
                sample_item_id=sample_item_id,
                sample_batch_id=ids["batch"],
                sample_file_id=ids["file"],
                sample_item_name="MS2 Readings Route Test Sample",
                sample_item_type="sample",
                polarity="+",
                sample_item_utc_created=now,
            )
        )
        await session.commit()
    return sample_item_id


async def _get(client, sample_item_id: str, filename: str, **params):
    """GET the route on the converted fixture, the query encoded by httpx."""
    sample = SimpleNamespace(
        filename=filename,
        sample_item_name="MS2 Readings Route Test Sample",
        t0=None,
        t1=None,
        polarity="+",
    )
    with patch(f"{_SERVICE}.fetch_sample", AsyncMock(return_value=sample)):
        return await client.get(
            f"/api/samples/{sample_item_id}/ms2/readings", params=params
        )


@pytest.mark.asyncio
async def test_a_guest_reads_a_group_over_the_ions_named(
    guest_client, readings_sample_id, stepped
):
    response = await _get(
        guest_client, readings_sample_id, stepped, group=GROUP, ions=IONS
    )
    again = await _get(
        guest_client, readings_sample_id, stepped, group=GROUP, ions=IONS
    )

    assert response.status_code == again.status_code == 200
    body = response.json()
    assert {"source", "cached", "core_version", "reason", "data"} <= set(body)
    assert body["source"] == "store" and body["cached"] is False
    assert again.json()["cached"] is True
    assert again.json()["data"] == body["data"]
    data = body["data"]
    assert data["group"] == GROUP
    assert [s["group"] for s in data["spectra"]] == [
        f"195.0877@hcd{nce}.00" for nce in (10, 20, 40, 60)
    ]
    (row,) = [
        r
        for r in data["spectra"][0]["readings"]
        if r["formula"] == PRECURSOR["fragments"][0]["formula"]
    ]
    assert (
        row["role"] == "fragment" and row["loss"] == PRECURSOR["fragments"][0]["loss"]
    )


@pytest.mark.asyncio
async def test_ions_are_required(guest_client, readings_sample_id, stepped):
    response = await _get(guest_client, readings_sample_id, stepped, group=GROUP)

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_a_plus_sent_unencoded_is_refused_with_how_to_send_it(
    guest_client, readings_sample_id, stepped
):
    sample = SimpleNamespace(
        filename=stepped,
        sample_item_name="MS2 Readings Route Test Sample",
        t0=None,
        t1=None,
        polarity="+",
    )
    with patch(f"{_SERVICE}.fetch_sample", AsyncMock(return_value=sample)):
        response = await guest_client.get(
            f"/api/samples/{readings_sample_id}/ms2/readings"
            f"?group={GROUP}&ions={PRECURSOR['formula']}"
        )

    assert response.status_code == 422
    assert "send it as %2B" in response.text
