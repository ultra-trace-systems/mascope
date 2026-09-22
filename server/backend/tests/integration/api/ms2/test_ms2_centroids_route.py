"""
The MS2 centroids route's response shape, as a client receives it over HTTP.

``GET /api/samples/{id}/ms2/centroids`` is part of the released API, and the
published SDK's ``get_averaged_centroids()`` hands its response back unparsed,
so the keys and per-spectrum fields are what scripts index into. The unit tests
in ``tests/unit/api/ms2/`` call the service directly; these go through the
route, so the query string is parsed, the permission check runs and the body is
JSON-encoded the way a client sees it.

The Thermo read is stubbed: the demo data and the committed test files carry no
MS2 scans, and the shape does not depend on the acquisition.
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import numpy as np
import pytest
import pytest_asyncio

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

# Every spectrum carries these; parent_peak_mz and activation joined the
# original four without changing them.
SPECTRUM_FIELDS = {
    "parent_peak_mz",
    "activation",
    "mz",
    "intensity",
    "resolution",
    "signal_to_noise",
}


def _spectrum(*mz: float):
    masses = np.array(mz)
    return (
        masses,
        np.full(masses.shape, 1000.0),
        np.full(masses.shape, 60000.0),
        np.full(masses.shape, 50.0),
    )


async def _thermo_groups(*_args, by_activation: bool, **_kwargs) -> dict:
    """What the Thermo read returns for one stepped-energy precursor and one
    single-energy precursor, with and without the activation split."""
    if by_activation:
        return {
            (137.096, "hcd20.00"): _spectrum(59.05),
            (137.096, "hcd40.00"): _spectrum(41.02),
            (200.5, "cid35.00"): _spectrum(95.1),
        }
    return {(137.096, ""): _spectrum(41.02, 59.05), (200.5, ""): _spectrum(95.1)}


@pytest_asyncio.fixture(scope="module")
async def ms2_sample_id(async_session_factory, test_users) -> str:
    """A sample in a workspace every test user is a member of."""
    now = datetime.now(timezone.utc)
    workspace_id = gen_id()
    dataset_id = gen_id()
    sample_batch_id = gen_id()
    sample_file_id = gen_id()
    sample_item_id = gen_id()

    async with async_session_factory() as session:
        session.add(
            Workspace(
                workspace_id=workspace_id,
                workspace_name="MS2 Route Test Workspace",
                workspace_status="active",
                workspace_utc_created=now,
                workspace_utc_modified=now,
            )
        )
        for role_name, user in test_users.items():
            session.add(
                WorkspaceMember(
                    workspace_member_id=gen_id(),
                    workspace_id=workspace_id,
                    user_id=user.id,
                    workspace_role=role_name,
                    granted_at=now,
                    granted_by=user.id,
                )
            )
        session.add(
            Dataset(
                dataset_id=dataset_id,
                workspace_id=workspace_id,
                dataset_name="MS2 Route Test Dataset",
                dataset_utc_created=now,
            )
        )
        session.add(
            SampleBatch(
                sample_batch_id=sample_batch_id,
                dataset_id=dataset_id,
                sample_batch_name="MS2 Route Test Batch",
                sample_batch_utc_created=now,
            )
        )
        session.add(
            SampleFile(
                sample_file_id=sample_file_id,
                filename=f"ms2-route-test-{sample_file_id}.raw",
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
                sample_batch_id=sample_batch_id,
                sample_file_id=sample_file_id,
                sample_item_name="MS2 Route Test Sample",
                sample_item_type="sample",
                polarity="+",
                sample_item_utc_created=now,
            )
        )
        await session.commit()

    return sample_item_id


async def _get_centroids(client, sample_item_id: str, query: str = ""):
    """GET the route with the Thermo read stubbed; returns (response, stub)."""
    sample = SimpleNamespace(
        filename="ms2-route-test.raw",
        sample_item_name="MS2 Route Test Sample",
        t0=0.0,
        t1=60.0,
        polarity="+",
    )
    compute = AsyncMock(side_effect=_thermo_groups)
    with (
        patch(f"{_SERVICE}.fetch_sample", AsyncMock(return_value=sample)),
        patch(f"{_SERVICE}.m_compute.get_orbi_ms2_centroids_by_parent", compute),
    ):
        response = await client.get(
            f"/api/samples/{sample_item_id}/ms2/centroids{query}"
        )
    return response, compute


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["", "?by_activation=false"])
async def test_spectra_are_keyed_by_the_bare_parent_mz_unless_split(
    guest_client, ms2_sample_id, query
):
    response, compute = await _get_centroids(guest_client, ms2_sample_id, query)

    assert response.status_code == 200
    assert compute.await_args.kwargs["by_activation"] is False
    body = response.json()
    assert body["results"] == 2
    assert list(body["data"]) == ["137.096", "200.5"]
    for key, spectrum in body["data"].items():
        assert SPECTRUM_FIELDS <= set(spectrum)
        # A client that parses the key as the parent m/z gets the field back.
        assert float(key) == spectrum["parent_peak_mz"]
        assert spectrum["activation"] == ""
    assert body["data"]["137.096"]["mz"] == [41.02, 59.05]


@pytest.mark.asyncio
async def test_by_activation_query_keys_one_spectrum_per_step(
    guest_client, ms2_sample_id
):
    response, compute = await _get_centroids(
        guest_client, ms2_sample_id, "?by_activation=true"
    )

    assert response.status_code == 200
    assert compute.await_args.kwargs["by_activation"] is True
    body = response.json()
    assert body["results"] == 3
    assert list(body["data"]) == [
        "137.096@hcd20.00",
        "137.096@hcd40.00",
        "200.5@cid35.00",
    ]
    for key, spectrum in body["data"].items():
        assert SPECTRUM_FIELDS <= set(spectrum)
        assert key == f"{spectrum['parent_peak_mz']}@{spectrum['activation']}"
    assert body["data"]["137.096@hcd40.00"]["mz"] == [41.02]
