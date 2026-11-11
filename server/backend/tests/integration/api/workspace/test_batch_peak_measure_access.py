"""
Tests: measuring a batch peak's identity on a sample keeps the workspace ACL.

``GET /api/batch-peaks/batch/{id}/measure-candidate`` reads one sample's peaks
and the batch's ledger, so it checks both: the batch and the sample must each
be one the caller may read. Either check alone refused here answers 403
before the handler looks for the batch peak (which would answer 404).

Fixtures used:
- ``alpha_batch`` and ``alpha_item`` in ``ws_alpha`` (every test user is a member)
- ``beta_batch`` and ``beta_item`` in ``ws_beta``, where only the owner is a member
"""

import pytest


pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def assignment_enabled(monkeypatch):
    monkeypatch.setenv("MASCOPE_PEAK_ASSIGNMENT", "1")


def _params(sample_item_id):
    return {
        "batch_peak_id": "no-such-peak",
        "candidate": 0,
        "sample_item_id": sample_item_id,
    }


async def test_an_outsider_is_refused(outsider_client, alpha_batch, alpha_item):
    response = await outsider_client.get(
        f"/api/batch-peaks/batch/{alpha_batch}/measure-candidate",
        params=_params(alpha_item),
    )
    assert response.status_code == 403, response.text


async def test_a_sample_of_another_workspace_is_refused(
    guest_client, alpha_batch, beta_item
):
    response = await guest_client.get(
        f"/api/batch-peaks/batch/{alpha_batch}/measure-candidate",
        params=_params(beta_item),
    )
    assert response.status_code == 403, response.text


async def test_a_batch_of_another_workspace_is_refused(
    guest_client, beta_batch, alpha_item
):
    response = await guest_client.get(
        f"/api/batch-peaks/batch/{beta_batch}/measure-candidate",
        params=_params(alpha_item),
    )
    assert response.status_code == 403, response.text


async def test_a_member_reaches_the_handler(guest_client, alpha_batch, alpha_item):
    response = await guest_client.get(
        f"/api/batch-peaks/batch/{alpha_batch}/measure-candidate",
        params=_params(alpha_item),
    )
    assert response.status_code == 404, response.text
