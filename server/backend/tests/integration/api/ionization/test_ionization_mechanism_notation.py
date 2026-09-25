"""
Integration tests: a mechanism in either notation, stored in the standard one.

A mechanism is written ``[M-H]-`` - the standard adduct notation, the ion's
charge last - and a row written before that holds the legacy ``-H+`` until the
migration rewrites it. Both have to behave as one mechanism: read back in the
standard notation, found by either spelling, and refused a second time in the
other one, which the column's unique constraint alone would let through.
"""

import random

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, text

from mascope_backend.db import IonizationMechanism
from mascope_backend.db.id import gen_id


def _unique_moiety() -> str:
    """A moiety no other test stores, since the mechanism column is unique.

    Small, because creating a mechanism builds its ion for every compound in
    the library, and a heavy moiety makes every one of those envelopes large.
    """
    return f"C{random.randint(2, 9)}H{random.randint(20, 99)}"


@pytest_asyncio.fixture
async def legacy_row(async_session_factory):
    """A stored row still holding the legacy spelling of its mechanism."""
    mechanism_id = gen_id()
    moiety = _unique_moiety()
    legacy = f"-{moiety}+"
    async with async_session_factory() as session:
        session.add(
            IonizationMechanism(
                ionization_mechanism_id=mechanism_id,
                ionization_mechanism_polarity="-",
                ionization_mechanism=legacy,
            )
        )
        await session.commit()

    yield mechanism_id, legacy, f"[M-{moiety}]-"

    async with async_session_factory() as session:
        await session.execute(
            delete(IonizationMechanism).where(
                IonizationMechanism.ionization_mechanism_id == mechanism_id
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_a_legacy_row_is_read_in_the_standard_notation(
    async_session_factory, guest_client, legacy_row
):
    mechanism_id, legacy, standard = legacy_row

    async with async_session_factory() as session:
        stored = (
            await session.execute(
                text(
                    "SELECT ionization_mechanism FROM ionization_mechanism "
                    "WHERE ionization_mechanism_id = :id"
                ),
                {"id": mechanism_id},
            )
        ).scalar_one()
        read = (
            await session.execute(
                select(IonizationMechanism.ionization_mechanism).where(
                    IonizationMechanism.ionization_mechanism_id == mechanism_id
                )
            )
        ).scalar_one()
    assert stored == legacy
    assert read == standard

    response = await guest_client.get(f"/api/ionization_mechanisms/{mechanism_id}")
    assert response.status_code == 200, response.text
    assert response.json()["data"]["ionization_mechanism"] == standard


@pytest.mark.asyncio
@pytest.mark.parametrize("spelling", ["legacy", "standard"])
async def test_a_legacy_row_is_found_by_either_spelling(
    guest_client, legacy_row, spelling
):
    mechanism_id, legacy, standard = legacy_row
    wanted = legacy if spelling == "legacy" else standard

    response = await guest_client.get(
        "/api/ionization_mechanisms", params={"ionization_mechanism": [wanted]}
    )

    assert response.status_code == 200, response.text
    assert [row["ionization_mechanism_id"] for row in response.json()["data"]] == [
        mechanism_id
    ]


@pytest.mark.asyncio
async def test_a_legacy_row_is_not_created_again_in_the_standard_spelling(
    editor_client, legacy_row
):
    _, _, standard = legacy_row

    response = await editor_client.post(
        "/api/ionization_mechanisms", json={"ionization_mechanism": standard}
    )

    assert response.status_code == 409, response.text


@pytest.mark.asyncio
async def test_a_legacy_spelling_is_created_in_the_standard_one(
    editor_client, async_session_factory
):
    moiety = _unique_moiety()

    response = await editor_client.post(
        "/api/ionization_mechanisms", json={"ionization_mechanism": f"+{moiety}-"}
    )

    assert response.status_code == 201, response.text
    data = response.json()["data"]
    try:
        assert data["ionization_mechanism"] == f"[M+{moiety}]-"
        assert data["ionization_mechanism_polarity"] == "-"
        async with async_session_factory() as session:
            stored = (
                await session.execute(
                    text(
                        "SELECT ionization_mechanism FROM ionization_mechanism "
                        "WHERE ionization_mechanism_id = :id"
                    ),
                    {"id": data["ionization_mechanism_id"]},
                )
            ).scalar_one()
        assert stored == f"[M+{moiety}]-"
    finally:
        await editor_client.delete(
            f"/api/ionization_mechanisms/{data['ionization_mechanism_id']}"
        )
