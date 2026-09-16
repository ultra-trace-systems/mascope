"""
Integration tests: reading ionization mechanisms the write validators refuse.

``GET /api/ionization_mechanisms`` reports stored rows as they are. A mechanism
written under older rules, or inserted directly, is listed like any other
rather than failing the whole response, which the frontend loads.
"""

import pytest
import pytest_asyncio
from sqlalchemy import delete

from mascope_backend.db import IonizationMechanism
from mascope_backend.db.id import gen_id


@pytest_asyncio.fixture
async def legacy_mechanism(async_session_factory):
    """A stored mechanism that ``IonizationMechanismCreate`` refuses."""
    mechanism_id = gen_id()
    # The mechanism column is unique, hence the id in the label.
    mechanism = f"+H+ (legacy {mechanism_id})"
    async with async_session_factory() as session:
        session.add(
            IonizationMechanism(
                ionization_mechanism_id=mechanism_id,
                ionization_mechanism_polarity="+",
                ionization_mechanism=mechanism,
            )
        )
        await session.commit()

    yield mechanism_id, mechanism

    async with async_session_factory() as session:
        await session.execute(
            delete(IonizationMechanism).where(
                IonizationMechanism.ionization_mechanism_id == mechanism_id
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_listing_reports_a_row_create_would_refuse(
    guest_client, legacy_mechanism
):
    mechanism_id, mechanism = legacy_mechanism
    response = await guest_client.get("/api/ionization_mechanisms")
    assert response.status_code == 200, response.text
    listed = {
        row["ionization_mechanism_id"]: row["ionization_mechanism"]
        for row in response.json()["data"]
    }
    assert listed[mechanism_id] == mechanism


@pytest.mark.asyncio
async def test_single_read_reports_a_row_create_would_refuse(
    guest_client, legacy_mechanism
):
    mechanism_id, mechanism = legacy_mechanism
    response = await guest_client.get(f"/api/ionization_mechanisms/{mechanism_id}")
    assert response.status_code == 200, response.text
    assert response.json()["data"]["ionization_mechanism"] == mechanism


@pytest.mark.asyncio
async def test_a_row_create_would_refuse_can_be_deleted(
    editor_client, async_session_factory, legacy_mechanism
):
    """Delete reads the mechanism first, so this route is what removes such a
    row - and it is the only one the API offers for it."""
    mechanism_id, _ = legacy_mechanism
    response = await editor_client.delete(f"/api/ionization_mechanisms/{mechanism_id}")
    assert response.status_code == 200, response.text

    async with async_session_factory() as session:
        assert await session.get(IonizationMechanism, mechanism_id) is None
