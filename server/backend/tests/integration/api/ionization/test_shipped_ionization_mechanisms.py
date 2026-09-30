"""
Tests: the ionization mechanisms Mascope ships, over ``/api/ionization_mechanisms``.

A shipped mechanism is on every server from its first start, and the shipped
modes and the channels a run searches are built on it. Deleting one would
only have the next start create it again, under another id and with every
compound's ions rebuilt, so the route refuses it the way the mode route
refuses a shipped mode, and the listing says which rows those are so that the
app need not offer the delete. A row of a shipped notation under the wrong
polarity is not the shipped mechanism - nothing can use it - and is deleted
like any other.
"""

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from mascope_backend.db import IonizationMechanism
from mascope_backend.db.id import gen_id
from mascope_tools.composition.mechanism_notation import mechanism_spellings


async def _held(async_session_factory, notation):
    """The row holding a mechanism in either spelling, or None."""
    async with async_session_factory() as session:
        return (
            (
                await session.execute(
                    select(IonizationMechanism).where(
                        IonizationMechanism.ionization_mechanism.in_(
                            mechanism_spellings([notation])
                        )
                    )
                )
            )
            .scalars()
            .first()
        )


async def _insert(async_session_factory, notation, polarity):
    mechanism_id = gen_id(11)
    async with async_session_factory() as session:
        session.add(
            IonizationMechanism(
                ionization_mechanism_id=mechanism_id,
                ionization_mechanism_polarity=polarity,
                ionization_mechanism=notation,
            )
        )
        await session.commit()
    return mechanism_id


async def _remove(async_session_factory, mechanism_id):
    async with async_session_factory() as session:
        await session.execute(
            delete(IonizationMechanism).where(
                IonizationMechanism.ionization_mechanism_id == mechanism_id
            )
        )
        await session.commit()


@pytest_asyncio.fixture
async def shipped_row(async_session_factory):
    """The diiodide cluster, a mechanism Mascope ships.

    Startup seeds it, and these tests do not run one, so it is added here
    unless something before them left it in the shared database.
    """
    held = await _held(async_session_factory, "[M+I2]-")
    if held is not None:
        yield held.ionization_mechanism_id
        return
    mechanism_id = await _insert(async_session_factory, "[M+I2]-", "-")
    yield mechanism_id
    await _remove(async_session_factory, mechanism_id)


@pytest_asyncio.fixture
async def own_row(async_session_factory):
    """A mechanism the deployment made, which Mascope does not ship."""
    # The mechanism column is unique, hence the id in the label.
    mechanism_id = await _insert(
        async_session_factory, f"[M+Cl]- (own {gen_id()})", "-"
    )
    yield mechanism_id
    await _remove(async_session_factory, mechanism_id)


@pytest.mark.asyncio
async def test_a_shipped_mechanism_cannot_be_deleted(
    admin_client, async_session_factory, shipped_row
):
    response = await admin_client.delete(f"/api/ionization_mechanisms/{shipped_row}")

    assert response.status_code == 400, response.text
    assert "Mascope ships" in response.text
    async with async_session_factory() as session:
        assert await session.get(IonizationMechanism, shipped_row) is not None


@pytest.mark.asyncio
async def test_the_listing_says_which_mechanisms_ship(
    guest_client, shipped_row, own_row
):
    response = await guest_client.get("/api/ionization_mechanisms")

    assert response.status_code == 200, response.text
    shipped = {
        row["ionization_mechanism_id"]: row["shipped"]
        for row in response.json()["data"]
    }
    assert shipped[shipped_row] is True
    assert shipped[own_row] is False


@pytest.mark.asyncio
async def test_a_single_read_says_so_too(guest_client, shipped_row):
    response = await guest_client.get(f"/api/ionization_mechanisms/{shipped_row}")

    assert response.status_code == 200, response.text
    assert response.json()["data"]["shipped"] is True


@pytest.mark.asyncio
async def test_a_shipped_notation_under_the_wrong_polarity_can_be_deleted(
    admin_client, async_session_factory
):
    """Deleting it is how an operator lets the next start create it right."""
    notation = "[M+K]+"
    if await _held(async_session_factory, notation) is not None:
        pytest.skip(f"the shared database already holds {notation}")
    mechanism_id = await _insert(async_session_factory, notation, "-")
    try:
        response = await admin_client.delete(
            f"/api/ionization_mechanisms/{mechanism_id}"
        )

        assert response.status_code == 200, response.text
    finally:
        await _remove(async_session_factory, mechanism_id)
